"""catalog_match.expand: the expansion round, end to end through pipeline.find_product_image.

Providers, page fetcher, image fetcher and verifier are stage doubles; everything between
them is production code (query plan, pool, identity scoring, page extraction, pHash and
quality checks, decision routing). The verifier double only supplies what a model would
READ on each image; verify.make_verdict decides. Sockets are blocked for every test.
"""

import io
import socket
import sys
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from catalog_match import expand, pages, pipeline, settings
from catalog_match.fetch import phash_hex
from catalog_match.identity import build_sku_spec
from catalog_match.models import (
    Candidate, CandidateScore, FetchedImage, ProviderResult, RankedCandidate, SearchOutcome, VerificationResult,
)
from catalog_match.providers.lens import VisualSearch
from catalog_match.verify import make_verdict

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "pages"
MAPPINGS = {"barts": {"brand": "Barts", "synonyms": ["Barts", "BARTS TRADITON"],
                      "excluded_competitors": ["Emborg", "McCain"], "official_domains": ["barts.com"]}}
SPEC = build_sku_spec({"name": "BARTS TRADITIONAL FRIES 1KG", "brand": "BARTS"}, MAPPINGS)

LULU_PAGE = "https://www.luluhypermarket.com/en-ae/barts-traditional-fries-1kg/p/88231"
LULU_IMAGE = "https://lulu.akinoncloudcdn.com/products/2025/03/11/88231/barts-traditional-fries-1kg-1500x1500.jpg"
EMBORG = "https://img.example-cdn.com/emborg-fries.jpg"
INSTA = "https://img.example-cdn.com/barts-insta.jpg"

READ_MATCH = {"brand_text": "Barts", "variant_text": "Traditional Fries", "size_text": "1 kg", "pack_count": 1,
              "view": "front_packshot", "brand_match": "yes", "variant_match": "yes", "size_match": "yes"}
READ_SIZE_UNREADABLE = dict(READ_MATCH, size_text="", size_match="unsure")


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError(f"network access attempted: {args!r}")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    monkeypatch.setattr(settings, "_config", None)
    monkeypatch.setenv("AUTO_PUBLISH_ENABLED", "false")
    monkeypatch.setenv("AUTO_PUBLISH_BRANDS", "")
    for name in ("SERPER_API_KEY", "SERPAPI_API_KEY", "VISUAL_SEARCH", "EXPANSION_ENABLED", "EXPANSION_MAX_CALLS"):
        monkeypatch.delenv(name, raising=False)
    pages.clear_cache()


# ---------------------------------------------------------------------------
# Images and stage doubles
# ---------------------------------------------------------------------------

def packshot_png(seed: int, size=(800, 800)) -> bytes:
    """A white-background product shot; the seed changes the drawing (and so the pHash)."""
    w, h = size
    img = Image.new("RGB", size, (255, 255, 255))
    d = ImageDraw.Draw(img)
    x0, y0, x1, y1 = int(w * 0.28), int(h * 0.12), int(w * 0.72), int(h * 0.88)
    d.rectangle([x0, y0, x1, y1], fill=((seed * 67) % 200, (seed * 131) % 200, (seed * 29) % 200))
    for i in range(6):
        if (seed >> i) & 1:
            yy = y0 + int((30 + i * ((y1 - y0) - 60) / 6) * 1)
            d.rectangle([x0 + int(w * 0.025), yy, x1 - int(w * 0.025), yy + (y1 - y0) // 14], fill=(250, 250, 250))
    if seed % 3 == 0:
        d.ellipse([x0 - int(w * 0.075), y0 + int(h * 0.05), x0 + int(w * 0.075), y0 + int(h * 0.2)], fill=(20, 20, 160))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def cand(url, title, page_url, provider="serper", n=1):
    return Candidate(image_url=url, page_url=page_url, page_title=title, title=title, provider=provider, rank=n)


def hit(page_url, title, provider="serper_web", n=1, image_url="", snippet=""):
    return Candidate(image_url=image_url, page_url=page_url, title=title, snippet=snippet, provider=provider, rank=n)


def product_page(name, image, brand=None, gtin=None):
    brand_json = f', "brand": {{"@type": "Brand", "name": "{brand}"}}' if brand else ""
    gtin_json = f', "gtin13": "{gtin}"' if gtin else ""
    return (f'<html><head><title>{name}</title><script type="application/ld+json">'
            f'{{"@type": "Product", "name": "{name}", "image": "{image}"{brand_json}{gtin_json}}}'
            f'</script></head><body></body></html>')


class StubProvider:
    kind = "search"

    def __init__(self, name, cands, sanctioned=True, status=None, fallback=False, http_status=200):
        self.name, self.cands, self.sanctioned, self.fallback = name, list(cands), sanctioned, fallback
        self.status, self.http_status = status, http_status
        self.calls = []

    def search(self, query, hl, spec):
        self.calls.append(query)
        status = self.status or ("ok" if self.cands else "empty")
        return ProviderResult(provider=self.name, status=status, http_status=self.http_status, latency_ms=0,
                              candidates=list(self.cands) if status == "ok" else [])


class StubFetcher:
    def __init__(self, bodies):
        self.bodies = dict(bodies)
        self.fetched = []

    def fetch(self, cands, spec):
        out = []
        for c in cands:
            self.fetched.append(c.image_url)
            body = self.bodies.get(c.image_url, "http_404")
            if isinstance(body, str):
                out.append(FetchedImage(candidate=c, ok=False, error=body))
                continue
            with Image.open(io.BytesIO(body)) as im:
                w, h = im.size
                ph = phash_hex(im)
            out.append(FetchedImage(candidate=c, ok=True, content_sha256=f"{abs(hash(body)):064x}"[:64],
                                    width=w, height=h, path_or_bytes=body, phash=ph))
        return out


class StubVerifier:
    def __init__(self, readings):
        self.readings = dict(readings)
        self.calls = []

    def verify(self, spec, images):
        self.calls.append([f.candidate.image_url for f in images])
        verdicts = [make_verdict(spec, i, self.readings.get(f.candidate.image_url, {})) for i, f in enumerate(images)]
        return VerificationResult(status="ok", verdicts=verdicts, calls=1)


class StubPages:
    def __init__(self, docs):
        self.docs = dict(docs)
        self.fetched = []

    def fetch_page(self, url, referer=""):
        self.fetched.append(url)
        doc = self.docs.get(url)
        if doc is None:
            return pages.PageInfo(url=url, ok=False, error="http_404")
        return pages.extract(doc, url)


def lulu_html():
    return (FIXTURES / "lulu_og_jsonld.html").read_text(encoding="utf-8")


def run(main_cands, *, web=(), shop=(), lens=None, docs=None, bodies=None, readings=None, max_calls=4,
        main=None, **kwargs):
    """find_product_image with stage doubles and an explicit Expansion; returns (outcome, doubles)."""
    web_p = StubProvider("serper_web", list(web))
    shop_p = StubProvider("serper_shopping", list(shop))
    pages_s = StubPages(docs or {})
    exp = expand.Expansion(web=web_p, shopping=shop_p, visual=lens, pages=pages_s, max_calls=max_calls)
    fetcher = StubFetcher(bodies or {})
    verifier = StubVerifier(readings or {})
    providers = main if main is not None else [StubProvider("serper", main_cands)]
    outcome = pipeline.find_product_image(SPEC, providers=providers, fetcher=fetcher, verifier=verifier,
                                          expansion=exp, **kwargs)
    return outcome, {"web": web_p, "shop": shop_p, "pages": pages_s, "fetcher": fetcher, "verifier": verifier,
                     "exp": exp}


def health_of(outcome):
    return [(h.provider, h.query_id, h.status) for h in outcome.provider_health]


# ---------------------------------------------------------------------------
# When the round runs
# ---------------------------------------------------------------------------

def _rc(reasons=(), tier=1, w=400, phash="0f0f0f0f0f0f0f0f"):
    rc = RankedCandidate(candidate=Candidate(image_url="https://x.ae/a.jpg"), score=CandidateScore(tier=tier),
                         fetched=FetchedImage(candidate=Candidate(image_url="https://x.ae/a.jpg"), ok=True, width=w,
                                              height=w, phash=phash))
    rc.reasons = list(reasons)
    return rc


@pytest.mark.parametrize("decision,failure,reasons,visual,expected", [
    ("NOT_FOUND", "ALL_CONFLICTED", (), False, "expand"),
    ("NOT_FOUND", "NO_RESULTS", (), False, "expand"),
    ("REVIEW_UNSELECTED", None, (), False, "expand"),
    ("REVIEW_UNSELECTED", "DOWNLOAD_FAILED", (), False, "expand"),
    ("REVIEW_UNSELECTED", "VERIFIER_DOWN", (), True, ""),       # nobody could read what it would find
    ("PROVIDER_DOWN", "PROVIDER_DOWN", (), True, ""),
    ("REVIEW_PRESELECTED", None, (), True, ""),
    ("REVIEW_PRESELECTED", None, ("warn:low_resolution",), True, "upgrade"),
    ("AUTO_PUBLISH", None, ("warn:low_resolution",), True, "upgrade"),
    ("REVIEW_PRESELECTED", None, ("warn:low_resolution",), False, ""),   # no visual search configured
])
def test_trigger(decision, failure, reasons, visual, expected):
    winner = _rc(reasons) if decision in ("REVIEW_PRESELECTED", "AUTO_PUBLISH") else None
    outcome = SearchOutcome(decision=decision, failure_code=failure, winner=winner)
    exp = expand.Expansion(web=StubProvider("serper_web", []),
                           visual=VisualSearch([StubProvider("lens_serper", [])]) if visual else None)
    assert expand.trigger(outcome, exp) == expected
    assert expand.trigger(outcome, None) == ""
    assert expand.trigger(outcome, expand.Expansion(web=StubProvider("serper_web", []), max_calls=0)) == ""


def test_resolve_never_builds_a_round_for_injected_stages(monkeypatch):
    built = []
    monkeypatch.setattr(expand, "default_expansion", lambda: built.append(1) or "configured")
    assert expand.resolve(None, injected=True) is None and built == []
    assert expand.resolve(False, injected=False) is None and built == []
    assert expand.resolve(None, injected=False) == "configured"
    assert expand.resolve(True, injected=True) == "configured"        # explicit opt-in (live dry run)
    own = expand.Expansion(web=StubProvider("serper_web", []))
    assert expand.resolve(own, injected=True) is own


def test_default_expansion_is_inert_without_keys(monkeypatch):
    assert expand.default_expansion() is None                          # no key at all: nothing to call
    monkeypatch.setenv("SERPER_API_KEY", "k-123456")
    exp = expand.default_expansion()
    assert exp.web.name == "serper_web" and exp.shopping.name == "serper_shopping"
    assert exp.visual.names == ["lens_serper"] and exp.max_calls == 4
    monkeypatch.setenv("SERPAPI_API_KEY", "s-123456")
    assert expand.default_expansion().visual.names == ["lens_serper", "lens_serpapi"]
    monkeypatch.setenv("VISUAL_SEARCH", "off")
    assert expand.default_expansion().visual is None
    monkeypatch.setenv("EXPANSION_MAX_CALLS", "0")
    assert expand.default_expansion() is None
    monkeypatch.setenv("EXPANSION_MAX_CALLS", "2")
    monkeypatch.setenv("EXPANSION_ENABLED", "false")
    assert expand.default_expansion() is None


def test_no_round_when_providers_or_verifier_are_injected(monkeypatch):
    monkeypatch.setenv("SERPER_API_KEY", "k-123456")                    # a round WOULD be configured
    monkeypatch.setattr(expand, "default_expansion",
                        lambda: (_ for _ in ()).throw(AssertionError("expansion built for injected stages")))
    emborg = cand(EMBORG, "Emborg French Fries 1kg", "https://www.noon.com/uae-en/emborg-fries/p/")
    outcome = pipeline.find_product_image(SPEC, providers=[StubProvider("serper", [emborg])],
                                          fetcher=StubFetcher({}), verifier=StubVerifier({}))
    assert outcome.decision == "NOT_FOUND"
    assert {h.provider for h in outcome.provider_health} == {"serper"}


# ---------------------------------------------------------------------------
# The round
# ---------------------------------------------------------------------------

def test_retailer_page_found_when_google_images_had_only_other_brands(monkeypatch):
    monkeypatch.setenv("AUTO_PUBLISH_ENABLED", "true")
    monkeypatch.setenv("AUTO_PUBLISH_BRANDS", "*")
    emborg = cand(EMBORG, "Emborg French Fries 1kg", "https://www.noon.com/uae-en/emborg-fries/p/")
    outcome, d = run([emborg], web=[hit(LULU_PAGE, "Barts Traditional Fries 1kg | Lulu UAE")],
                     docs={LULU_PAGE: lulu_html()}, bodies={LULU_IMAGE: packshot_png(5)},
                     readings={LULU_IMAGE: READ_MATCH})

    assert outcome.decision == "REVIEW_PRESELECTED"
    w = outcome.winner
    assert w.candidate.image_url == LULU_IMAGE and w.candidate.provider == "page"
    assert w.candidate.page_url == LULU_PAGE and w.score.tier == 1
    # an image read from a fetched page is never auto-published, even with auto-publish on for every brand
    assert w.candidate.sanctioned is False and "auto_blocked:unsanctioned_source" in w.reasons
    # the web query is the SKU's own text on the brand's site and the main UAE retailers
    assert d["web"].calls == ["Barts TRADITIONAL FRIES 1kg (site:barts.com OR site:luluhypermarket.com OR "
                              "site:carrefouruae.com OR site:amazon.ae OR site:noon.com OR site:talabat.com)"]
    assert d["shop"].calls == ["Barts TRADITIONAL FRIES 1kg"]
    # every paid call is in the trace, after the normal flow's
    health = health_of(outcome)
    assert health[-2:] == [("serper_web", "X1", "ok"), ("serper_shopping", "X2", "empty")]
    assert {p for p, _, _ in health[:-2]} == {"serper"}
    assert any(q.startswith("serper_web[X1]: ") for q in outcome.queries)
    assert outcome.vlm_calls == 1 and d["verifier"].calls == [[LULU_IMAGE]]
    # the competitor listing of the normal flow is still there, still rejected
    emborg_rc = next(rc for rc in outcome.ranked if rc.candidate.image_url == EMBORG)
    assert emborg_rc.status == "rejected" and "hard:competitor_brand" in emborg_rc.reasons


def test_page_with_another_size_or_brand_is_hard_rejected():
    two_kg = "https://www.carrefouruae.com/mafuae/en/barts-traditional-fries/p/77812"
    other = "https://www.talabat.com/uae/grocery/traditional-fries"
    img_2kg = "https://cdn.mafrservices.com/sys-master-root/h1/77812_main.jpg"
    img_other = "https://images.talabat.com/mart/traditional-fries.jpg"
    # the listing titles are clean, so the pages are fetched; the pages themselves state the conflict
    outcome, d = run([], web=[hit(two_kg, "Barts Traditional Fries | Carrefour UAE", n=1),
                              hit(other, "Traditional Fries 1kg | talabat", n=2)],
                     docs={two_kg: product_page("Barts Traditional Fries 2.5kg", img_2kg),
                           other: product_page("Traditional Fries 1kg", img_other, brand="Emborg")},
                     bodies={img_2kg: packshot_png(6), img_other: packshot_png(7)},
                     readings={img_2kg: READ_MATCH, img_other: READ_MATCH})
    assert sorted(d["pages"].fetched) == sorted([two_kg, other])
    by_url = {rc.candidate.image_url: rc for rc in outcome.ranked}
    assert by_url[img_2kg].score.hard_reject == ("size_conflict",)
    assert by_url[img_other].score.hard_reject == ("competitor_brand",)
    assert by_url[img_other].candidate.page_title == "Emborg Traditional Fries 1kg"
    # hard-rejected candidates are never downloaded nor shown to the verifier, and nothing is picked
    assert d["fetcher"].fetched == [] and d["verifier"].calls == []
    assert outcome.decision == "NOT_FOUND" and outcome.failure_code == "ALL_CONFLICTED"
    assert outcome.reject_counts == {"size_conflict": 1, "competitor_brand": 1}


def test_hits_that_already_conflict_are_not_fetched():
    outcome, d = run([], web=[hit("https://www.noon.com/uae-en/barts-fries-2-5kg/p/", "Barts Fries 2.5kg | noon"),
                              hit("https://www.instagram.com/p/barts", "Barts Traditional Fries 1kg"),
                              hit("https://randomblog.example.com/fries", "Barts Traditional Fries 1kg")])
    assert d["pages"].fetched == []          # a size conflict in the listing, a social and an unknown host
    assert outcome.decision == "NOT_FOUND"


def test_call_cap_and_order(monkeypatch):
    insta = cand(INSTA, "Barts fries", "https://www.instagram.com/p/barts")
    lens_backend = StubProvider("lens_serper", [])
    common = dict(bodies={INSTA: packshot_png(9)}, readings={INSTA: READ_SIZE_UNREADABLE})

    # one call: the web search only
    outcome, d = run([insta], lens=VisualSearch([lens_backend]), max_calls=1, **common)
    assert len(d["web"].calls) == 1 and d["shop"].calls == [] and lens_backend.calls == []
    assert [h for h in health_of(outcome) if h[1].startswith("X")] == [("serper_web", "X1", "empty")]

    # two calls: web + shopping, no visual search even with a near-match
    outcome, d = run([insta], lens=VisualSearch([lens_backend]), max_calls=2, **common)
    assert len(d["web"].calls) == 1 and len(d["shop"].calls) == 1 and lens_backend.calls == []

    # four calls: web + shopping + visual search seeded by the near-match (the size was unreadable)
    outcome, d = run([insta], lens=VisualSearch([lens_backend]), max_calls=4, **common)
    assert lens_backend.calls == [INSTA]
    assert [h[:2] for h in health_of(outcome) if h[1].startswith("X")] == [
        ("serper_web", "X1"), ("serper_shopping", "X2"), ("lens_serper", "X3")]
    assert len(d["web"].calls) == 1          # a seed existed: no second web group


def test_second_web_group_only_when_nothing_turned_up():
    outcome, d = run([], max_calls=4)
    assert len(d["web"].calls) == 2
    second = d["web"].calls[1]
    assert "site:kibsons.com" in second and "site:luluhypermarket.com" not in second
    assert [h[:2] for h in health_of(outcome) if h[1].startswith("X")] == [
        ("serper_web", "X1"), ("serper_shopping", "X2"), ("serper_web", "X5")]


def test_visual_search_seeded_by_a_near_match_finds_the_titled_listing():
    insta = cand(INSTA, "Barts fries", "https://www.instagram.com/p/barts")
    car_img = "https://cdn.mafrservices.com/sys-master-root/h1/77811_main.jpg"
    car_page = "https://www.carrefouruae.com/mafuae/en/frozen-potato/barts-traditional-fries-1kg/p/77811"
    match = Candidate(image_url=car_img, page_url=car_page, title="Barts Traditional Fries 1kg - Carrefour UAE",
                      snippet="Carrefour UAE", domain="carrefouruae.com", provider="lens_serper", rank=1,
                      width=1000, height=1000)
    backend = StubProvider("lens_serper", [match])
    outcome, d = run([insta], lens=VisualSearch([backend]),
                     bodies={INSTA: packshot_png(9), car_img: packshot_png(9, (1000, 1000))},
                     readings={INSTA: READ_SIZE_UNREADABLE, car_img: READ_MATCH})
    assert backend.calls == [INSTA]                                   # the seed is the near-match's image
    assert outcome.decision == "REVIEW_PRESELECTED"
    w = outcome.winner
    assert w.candidate.image_url == car_img and w.candidate.provider == "lens_serper"
    assert w.candidate.sanctioned is True and w.candidate.query_id == "X3"
    assert outcome.vlm_calls == 2


def test_a_mismatch_is_never_a_seed():
    insta = cand(INSTA, "Barts fries", "https://www.instagram.com/p/barts")
    backend = StubProvider("lens_serper", [])
    run([insta], lens=VisualSearch([backend]), bodies={INSTA: packshot_png(9)},
        readings={INSTA: dict(READ_MATCH, brand_text="Emborg", brand_match="no")})
    assert backend.calls == []


def test_serpapi_is_asked_for_one_seed_at_most():
    a, b = INSTA, "https://img.example-cdn.com/barts-other.jpg"
    near = [cand(a, "Barts fries", "https://www.instagram.com/p/a", n=1),
            cand(b, "Barts fries pack", "https://www.facebook.com/p/b", n=2)]
    serpapi = StubProvider("lens_serpapi", [])
    run(near, lens=VisualSearch([serpapi]), bodies={a: packshot_png(9), b: packshot_png(22)},
        readings={a: READ_SIZE_UNREADABLE, b: READ_SIZE_UNREADABLE})
    assert len(serpapi.calls) == 1


def test_serpapi_stays_at_one_call_when_serper_lens_keeps_failing():
    # Serper Lens fails for every seed (a transient 5xx, not a plan refusal): SerpApi, ~15x the price,
    # is still asked for one seed only, even with a generous call budget
    a, b = INSTA, "https://img.example-cdn.com/barts-other.jpg"
    near = [cand(a, "Barts fries", "https://www.instagram.com/p/a", n=1),
            cand(b, "Barts fries pack", "https://www.facebook.com/p/b", n=2)]
    serper_lens = StubProvider("lens_serper", [], status="error", http_status=503)
    serpapi = StubProvider("lens_serpapi", [], status="error", http_status=503)
    outcome, _ = run(near, lens=VisualSearch([serper_lens, serpapi]), max_calls=10,
                     bodies={a: packshot_png(9), b: packshot_png(22)},
                     readings={a: READ_SIZE_UNREADABLE, b: READ_SIZE_UNREADABLE})
    assert sorted(serper_lens.calls) == sorted([a, b])
    assert serpapi.calls == serper_lens.calls[:1]             # the first seed only
    assert [h[:2] for h in health_of(outcome) if h[0].startswith("lens")] == [
        ("lens_serper", "X3"), ("lens_serpapi", "X3"), ("lens_serper", "X4")]


def test_serper_refused_keeps_serper_sources_out():
    serper = StubProvider("serper", [], status="quota", http_status=429)
    bing = StubProvider("bing_html", [cand(INSTA, "Barts fries", "https://www.instagram.com/p/barts",
                                           provider="bing_html")], sanctioned=False, fallback=True)
    serper_lens = StubProvider("lens_serper", [])
    serpapi = StubProvider("lens_serpapi", [])
    outcome, d = run([], main=[serper, bing], lens=VisualSearch([serper_lens, serpapi]),
                     bodies={INSTA: packshot_png(9)}, readings={INSTA: READ_SIZE_UNREADABLE})
    assert outcome.decision == "REVIEW_UNSELECTED"
    assert d["web"].calls == [] and d["shop"].calls == [] and serper_lens.calls == []
    assert serpapi.calls == [INSTA]


def test_reviewer_negatives_stay_out():
    outcome, d = run([], web=[hit(LULU_PAGE, "Barts Traditional Fries 1kg | Lulu UAE")],
                     docs={LULU_PAGE: lulu_html()}, bodies={LULU_IMAGE: packshot_png(5)},
                     readings={LULU_IMAGE: READ_MATCH}, exclude_urls=[LULU_IMAGE + "?w=600"])
    assert all(rc.candidate.image_url != LULU_IMAGE for rc in outcome.ranked)
    assert outcome.reject_counts.get("reviewer_negative_url") == 1
    assert outcome.decision != "REVIEW_PRESELECTED" and d["verifier"].calls == []


def test_reviewer_negative_phash_drops_a_new_image():
    neg = phash_hex(Image.open(io.BytesIO(packshot_png(5))))
    outcome, d = run([], web=[hit(LULU_PAGE, "Barts Traditional Fries 1kg | Lulu UAE")],
                     docs={LULU_PAGE: lulu_html()}, bodies={LULU_IMAGE: packshot_png(5)},
                     readings={LULU_IMAGE: READ_MATCH}, exclude_phashes=[neg])
    assert outcome.reject_counts.get("reviewer_negative_phash") == 1
    assert outcome.winner is None and d["verifier"].calls == []


def test_same_bytes_inherit_the_reading_and_tier_one_evidence_preselects():
    insta = cand(INSTA, "Barts fries", "https://www.instagram.com/p/barts")
    same = packshot_png(5)
    outcome, d = run([insta], web=[hit(LULU_PAGE, "Barts Traditional Fries 1kg | Lulu UAE")],
                     docs={LULU_PAGE: lulu_html()}, bodies={INSTA: same, LULU_IMAGE: same},
                     readings={INSTA: READ_SIZE_UNREADABLE, LULU_IMAGE: READ_MATCH})
    # the Lulu copy is the same picture the verifier already read (size unreadable): no second call,
    # and its page evidence (brand + 1kg on a UAE retailer) makes it the tier-1 pick, flagged for review
    assert d["verifier"].calls == [[INSTA]] and outcome.vlm_calls == 1
    assert outcome.decision == "REVIEW_PRESELECTED"
    assert outcome.winner.candidate.image_url == LULU_IMAGE
    assert "preselected:tier1_unsure" in outcome.winner.reasons and "warn:vlm_unsure" in outcome.winner.reasons


def test_a_failing_round_keeps_the_normal_decision(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("bug in the round")

    monkeypatch.setattr(expand, "_merge_into_pool", boom)
    emborg = cand(EMBORG, "Emborg French Fries 1kg", "https://www.noon.com/uae-en/emborg-fries/p/")
    outcome, d = run([emborg], web=[hit(LULU_PAGE, "Barts Traditional Fries 1kg | Lulu UAE")],
                     docs={LULU_PAGE: lulu_html()}, bodies={LULU_IMAGE: packshot_png(5)},
                     readings={LULU_IMAGE: READ_MATCH})
    assert outcome.decision == "NOT_FOUND" and outcome.failure_code == "ALL_CONFLICTED"
    assert ("serper_web", "X1", "ok") in health_of(outcome)        # the paid call stays recorded


def test_broken_sources_are_isolated():
    class Raises:
        name = "serper_web"

        def search(self, *a):
            raise ValueError("boom")

    class BadPages:
        def fetch_page(self, url, referer=""):
            raise OSError("disk")

    exp = expand.Expansion(web=Raises(), shopping=StubProvider("serper_shopping", [
        hit(LULU_PAGE, "Barts Traditional Fries 1kg", provider="serper_shopping",
            image_url="https://encrypted-tbn0.gstatic.com/shopping?q=tbn:x")]), pages=BadPages(), max_calls=2)
    outcome = pipeline.find_product_image(SPEC, providers=[StubProvider("serper", [])], fetcher=StubFetcher({}),
                                          verifier=StubVerifier({}), expansion=exp)
    assert ("serper_web", "X1", "error") in health_of(outcome)
    assert outcome.decision == "NOT_FOUND"


# ---------------------------------------------------------------------------
# Low-resolution pick: same packshot at a higher resolution
# ---------------------------------------------------------------------------

SMALL = "https://img.example-cdn.com/barts-400.jpg"
SMALL_PAGE = "https://www.luluhypermarket.com/en-ae/barts-traditional-fries-1kg/p/1"
BIG = "https://cdn.mafrservices.com/sys-master-root/h1/barts-1200.jpg"
BIG_PAGE = "https://www.carrefouruae.com/mafuae/en/barts-traditional-fries-1kg/p/2"


def _low_res_case(big_reading, big_seed=5, via_page=False):
    small = cand(SMALL, "Barts Traditional Fries 1kg | Lulu UAE", SMALL_PAGE)
    title = "Barts Traditional Fries 1kg - Carrefour UAE"
    if via_page:   # a thumbnail-only match: the full image is read from the store page (provider 'page')
        big = Candidate(image_url="https://encrypted-tbn0.gstatic.com/images?q=tbn:big", page_url=BIG_PAGE,
                        title=title, provider="lens_serper", rank=1)
    else:
        big = Candidate(image_url=BIG, page_url=BIG_PAGE, title=title, domain="carrefouruae.com",
                        provider="lens_serper", rank=1, width=1200, height=1200)
    backend = StubProvider("lens_serper", [big])
    outcome, d = run([small], lens=VisualSearch([backend]),
                     docs={BIG_PAGE: product_page("Barts Traditional Fries 1kg", BIG)},
                     bodies={SMALL: packshot_png(5, (400, 400)), BIG: packshot_png(big_seed, (1200, 1200))},
                     readings={SMALL: READ_MATCH, BIG: big_reading})
    return outcome, d, backend


def test_low_resolution_pick_is_upgraded_to_the_same_packshot():
    outcome, d, backend = _low_res_case(READ_MATCH)
    assert backend.calls == [SMALL]
    assert outcome.decision == "REVIEW_PRESELECTED"
    assert outcome.winner.candidate.image_url == BIG
    assert "warn:low_resolution" not in outcome.winner.reasons
    small_rc = next(rc for rc in outcome.ranked if rc.candidate.image_url == SMALL)
    assert small_rc.status == "eligible"                       # still there for the reviewer
    assert outcome.vlm_calls == 2
    assert d["web"].calls == [] and d["shop"].calls == []      # the upgrade is one visual search only
    assert [h[:2] for h in health_of(outcome) if h[1].startswith("X")] == [("lens_serper", "XU")]


def test_upgrade_needs_a_match_and_the_same_packshot():
    outcome, _, _ = _low_res_case(READ_SIZE_UNREADABLE)
    assert outcome.winner.candidate.image_url == SMALL and "warn:low_resolution" in outcome.winner.reasons
    assert all(rc.candidate.image_url != BIG for rc in outcome.ranked)

    outcome, d, _ = _low_res_case(READ_MATCH, big_seed=6)              # another picture: not its pHash family
    assert outcome.winner.candidate.image_url == SMALL and d["verifier"].calls == [[SMALL]]


def test_auto_publish_is_never_downgraded_by_an_upgrade(monkeypatch):
    monkeypatch.setenv("AUTO_PUBLISH_ENABLED", "true")
    monkeypatch.setenv("AUTO_PUBLISH_BRANDS", "*")
    # the copy comes from a fetched page (never auto-published): keep the AUTO_PUBLISH pick as it was
    outcome, d, _ = _low_res_case(READ_MATCH, via_page=True)
    assert d["pages"].fetched == [BIG_PAGE] and d["verifier"].calls[-1] == [BIG]   # found and read ...
    assert outcome.decision == "AUTO_PUBLISH" and outcome.winner.candidate.image_url == SMALL  # ... not taken
    assert all(rc.candidate.image_url != BIG for rc in outcome.ranked)
    # an API copy with the same evidence keeps AUTO_PUBLISH and is taken
    outcome, _, _ = _low_res_case(READ_MATCH)
    assert outcome.decision == "AUTO_PUBLISH" and outcome.winner.candidate.image_url == BIG


def _upgrade_with(copy_url, copy_page, copy_title, small_title="Barts Traditional Fries | Lulu UAE"):
    small_page = "https://www.luluhypermarket.com/en-ae/barts-traditional-fries/p/1"
    small = cand(SMALL, small_title, small_page)
    copy = Candidate(image_url=copy_url, page_url=copy_page, title=copy_title, provider="lens_serper", rank=1,
                     width=1200, height=1200)
    backend = StubProvider("lens_serper", [copy])
    return run([small], lens=VisualSearch([backend]),
               bodies={SMALL: packshot_png(5, (400, 400)), copy_url: packshot_png(5, (1200, 1200))},
               readings={SMALL: READ_MATCH, copy_url: READ_MATCH})


def test_upgrade_never_trades_identity_evidence_for_resolution():
    # the pick is on Lulu; the sharper copy of the same packshot is on amazon.in (a foreign store, the pack
    # may differ): identity before image quality, so the Lulu pick stays and the copy is not even read
    amazon_in = "https://m.media-amazon.com/images/I/71barts.jpg"
    outcome, d = _upgrade_with(amazon_in, "https://www.amazon.in/Barts-Traditional-Fries/dp/B0X",
                               "Barts Traditional Fries : Amazon.in")
    assert outcome.decision == "REVIEW_PRESELECTED"
    assert outcome.winner.candidate.image_url == SMALL and "warn:low_resolution" in outcome.winner.reasons
    assert "warn:foreign_store" not in outcome.winner.reasons
    assert d["verifier"].calls == [[SMALL]]


def test_upgrade_never_brings_a_new_review_warning():
    # same store trust, same identity evidence, but the copy is a chat export: not an upgrade
    chat = "https://cdn.mafrservices.com/sys-master-root/h1/WhatsApp%20Image%202025-10-14%20at%2010.07.41.jpeg"
    outcome, d = _upgrade_with(chat, "https://www.carrefouruae.com/mafuae/en/barts-traditional-fries/p/2",
                               "Barts Traditional Fries - Carrefour UAE")
    assert outcome.winner.candidate.image_url == SMALL
    assert "warn:chat_or_screenshot" not in outcome.winner.reasons
    assert all(rc.candidate.image_url != chat for rc in outcome.ranked)
    # the same copy under a plain file name is taken
    plain = "https://cdn.mafrservices.com/sys-master-root/h1/barts-traditional-fries.jpg"
    outcome, _ = _upgrade_with(plain, "https://www.carrefouruae.com/mafuae/en/barts-traditional-fries/p/2",
                               "Barts Traditional Fries - Carrefour UAE")
    assert outcome.winner.candidate.image_url == plain and "warn:low_resolution" not in outcome.winner.reasons


def test_page_evidence_never_makes_an_api_image_auto_publishable(monkeypatch):
    monkeypatch.setenv("AUTO_PUBLISH_ENABLED", "true")
    monkeypatch.setenv("AUTO_PUBLISH_BRANDS", "*")
    image = "https://lulu.akinoncloudcdn.com/products/2025/03/11/88231/img_88231_1.jpg"
    # Google Images found the image, but its listing names no brand: tier 3, nothing picked
    weak = cand(image, "Traditional fries 1kg pack", "https://www.someblog.example.com/fries")
    outcome, d = run([weak], web=[hit(LULU_PAGE, "Barts Traditional Fries 1kg | Lulu UAE")],
                     docs={LULU_PAGE: product_page("Barts Traditional Fries 1kg", image, brand="Barts")},
                     bodies={image: packshot_png(5)}, readings={image: READ_MATCH})
    # the Lulu page that shows the same image is strong evidence for review, but it is scraped text:
    # it can pre-check the image, never be what makes it auto-publishable
    assert outcome.decision == "REVIEW_PRESELECTED"
    w = outcome.winner
    assert w.candidate.image_url == image and w.score.tier == 1 and w.candidate.page_url == LULU_PAGE
    assert w.candidate.sanctioned is False and "auto_blocked:unsanctioned_source" in w.reasons


def test_an_unread_pool_image_a_page_vouches_for_is_downloaded_and_read():
    # the normal flow had ten brandless listings; the last one was ranked below the 8 download slots
    others = [cand(f"https://img.example-cdn.com/fries-{i}.jpg", "Traditional fries 1kg",
                   f"https://www.someblog.example.com/fries-{i}", n=i) for i in range(1, 10)]
    image = "https://lulu.akinoncloudcdn.com/products/2025/03/11/88231/img_88231_1.jpg"
    last = cand(image, "Frozen food", "https://www.someblog.example.com/frozen", n=10)
    outcome, d = run(others + [last], web=[hit(LULU_PAGE, "Barts Traditional Fries 1kg | Lulu UAE")],
                     docs={LULU_PAGE: product_page("Barts Traditional Fries 1kg", image, brand="Barts")},
                     bodies={image: packshot_png(5)}, readings={image: READ_MATCH})
    assert d["fetcher"].fetched.count(image) == 1            # not downloaded by the normal flow, then by the round
    assert [image] in d["verifier"].calls
    assert outcome.decision == "REVIEW_PRESELECTED" and outcome.winner.candidate.image_url == image


# ---------------------------------------------------------------------------
# The offline eval never runs a round
# ---------------------------------------------------------------------------

@pytest.mark.eval
def test_eval_harness_is_untouched_by_the_round(monkeypatch):
    eval_dir = Path(__file__).resolve().parents[1] / "eval"
    if str(eval_dir) not in sys.path:
        sys.path.insert(0, str(eval_dir))
    import harness

    # everything a configured round needs is set: only the injection rule keeps it off
    monkeypatch.setenv("SERPER_API_KEY", "k-123456")
    monkeypatch.setenv("SERPAPI_API_KEY", "s-123456")
    resolved = []
    real_resolve = expand.resolve
    monkeypatch.setattr(expand, "resolve", lambda e, injected: resolved.append(real_resolve(e, injected))
                        or resolved[-1])
    monkeypatch.setattr(expand, "default_expansion",
                        lambda: (_ for _ in ()).throw(AssertionError("expansion built inside the eval")))
    golden = harness.load_golden()
    subset = dict(golden, skus=golden["skus"][:12])
    report = harness.run_all("v2", "normal", golden=subset)
    assert resolved and all(r is None for r in resolved)
    assert report["network_attempts"] == []
    assert all(o["decision"] != "ERROR" for o in report["outcomes"])


# ---------------------------------------------------------------------------
# X0 page recovery (free): the page names the product, the picture Google filed under it does not
# (live run 2026-10-03: Ansar Gallery's Yumway page under a Mondelle pack, row 13; Tradeling's Green Farm
# Meat Masala under Shan and Double Horse, row 36)
# ---------------------------------------------------------------------------

SHOP_PAGE = "https://www.example-mart.com/en/barts-traditional-fries-1kg"
SHOP_LISTING_IMG = "https://img.example-cdn.com/related-emborg.jpg"     # a "related products" picture
SHOP_OWN_IMG = "https://img.example-cdn.com/barts-traditional-fries-1kg-main.jpg"
READ_EMBORG = dict(READ_MATCH, brand_text="Emborg", brand_match="no")


def shop_listing(image=SHOP_LISTING_IMG, page=SHOP_PAGE, title="Barts Traditional Fries 1kg | Example Mart", n=1):
    return cand(image, title, page, n=n)


def test_x0_reads_the_page_of_a_listing_whose_picture_is_another_brand_and_needs_no_paid_call():
    outcome, d = run([shop_listing()], web=[hit(LULU_PAGE, "Barts Traditional Fries 1kg")],
                     docs={SHOP_PAGE: product_page("Barts Traditional Fries 1kg", SHOP_OWN_IMG, brand="Barts")},
                     bodies={SHOP_LISTING_IMG: packshot_png(3), SHOP_OWN_IMG: packshot_png(5)},
                     readings={SHOP_LISTING_IMG: READ_EMBORG, SHOP_OWN_IMG: READ_MATCH})
    assert d["pages"].fetched == [SHOP_PAGE]
    assert outcome.decision == "REVIEW_PRESELECTED"
    win = outcome.winner.candidate
    assert (win.image_url, win.provider, win.query_id, win.sanctioned) == (SHOP_OWN_IMG, "page", "X0", False)
    assert d["web"].calls == [] and d["shop"].calls == []               # X0 was enough: no paid call
    assert not any(h.provider in ("serper_web", "serper_shopping") for h in outcome.provider_health)


@pytest.mark.parametrize("body", ["http_404", "not_image"])
def test_x0_reads_the_page_when_the_listing_picture_cannot_be_downloaded(body):
    outcome, d = run([shop_listing()],
                     docs={SHOP_PAGE: product_page("Barts Traditional Fries 1kg", SHOP_OWN_IMG, brand="Barts")},
                     bodies={SHOP_LISTING_IMG: body, SHOP_OWN_IMG: packshot_png(5)},
                     readings={SHOP_OWN_IMG: READ_MATCH})
    assert d["pages"].fetched == [SHOP_PAGE] and outcome.winner.candidate.image_url == SHOP_OWN_IMG


def test_x0_leaves_a_page_whose_picture_is_the_right_brand_in_another_size():
    # the label says Barts, front packshot, but 2.5 kg: the page itself sells the other size
    read_other_size = dict(READ_MATCH, size_text="2.5 kg", size_match="no")
    outcome, d = run([shop_listing()], bodies={SHOP_LISTING_IMG: packshot_png(3)},
                     readings={SHOP_LISTING_IMG: read_other_size})
    assert d["pages"].fetched == [] and d["web"].calls                 # the round ran; X0 left the page


def test_x0_never_reads_social_networks_or_listings_of_another_size_or_brand():
    insta = shop_listing(image="https://img.example-cdn.com/insta.jpg",
                         page="https://www.instagram.com/p/barts-fries/", title="Barts Traditional Fries 1kg", n=1)
    # the size is only in the link (a title stating it is already a hard reject): tier 2 with a URL size conflict
    other_size = shop_listing(image="https://img.example-cdn.com/2kg.jpg",
                              page="https://www.example-mart.com/barts-traditional-fries-2kg",
                              title="Barts Traditional Fries | Example Mart", n=2)
    other_brand = shop_listing(image="https://img.example-cdn.com/emb.jpg", page="https://www.example-mart.com/emborg",
                               title="Emborg Fries 1kg | Example Mart", n=3)
    outcome, d = run([insta, other_size, other_brand],
                     bodies={c.image_url: "http_404" for c in (insta, other_size, other_brand)})
    assert d["pages"].fetched == [] and d["web"].calls                 # the round ran; X0 read nothing


def test_x0_reads_at_most_three_pages_best_first():
    listings = [shop_listing(image=f"https://img.example-cdn.com/l{i}.jpg",
                             page=f"https://www.example-mart.com/barts-traditional-fries-1kg-{i}", n=i + 1)
                for i in range(5)]
    outcome, d = run(listings, bodies={c.image_url: "http_404" for c in listings})
    assert len(d["pages"].fetched) == expand.MAX_RECOVER_PAGES
    assert d["pages"].fetched == [c.page_url for c in listings[:expand.MAX_RECOVER_PAGES]]


def test_when_x0_finds_no_pick_the_paid_steps_still_run():
    outcome, d = run([shop_listing()], web=[hit(LULU_PAGE, "Barts Traditional Fries 1kg")],
                     docs={SHOP_PAGE: product_page("Barts Traditional Fries 1kg", SHOP_OWN_IMG, brand="Barts"),
                           LULU_PAGE: lulu_html()},
                     bodies={SHOP_LISTING_IMG: packshot_png(3), SHOP_OWN_IMG: packshot_png(5),
                             LULU_IMAGE: packshot_png(7)},
                     readings={SHOP_LISTING_IMG: READ_EMBORG, SHOP_OWN_IMG: READ_EMBORG, LULU_IMAGE: READ_MATCH})
    assert d["pages"].fetched[0] == SHOP_PAGE and LULU_PAGE in d["pages"].fetched
    assert len(d["web"].calls) == 1
    assert outcome.decision == "REVIEW_PRESELECTED" and outcome.winner.candidate.image_url == LULU_IMAGE


# ---------------------------------------------------------------------------
# X0, review 3: one page is one page, the listing must name the brand, the one call is for the pages' images
# ---------------------------------------------------------------------------

def test_x0_reads_one_page_once_whatever_its_tracking_parameters():
    # row 36: five listings of one Tradeling page under different '?srsltid=' values took every X0 slot
    same = [shop_listing(image=f"https://img.example-cdn.com/s{i}.jpg", page=f"{SHOP_PAGE}?srsltid=AfmBOo{i}", n=i + 1)
            for i in range(4)]
    other_page = "https://www.example-grocer.ae/barts-traditional-fries-1kg"
    other = shop_listing(image="https://img.example-cdn.com/o.jpg", page=other_page, n=5)
    outcome, d = run(same + [other], bodies={c.image_url: "http_404" for c in same + [other]})
    assert len(d["pages"].fetched) == 2
    assert d["pages"].fetched[0].startswith(SHOP_PAGE) and d["pages"].fetched[1] == other_page
    assert expand.page_key(f"{SHOP_PAGE}?srsltid=x&utm_source=g") == expand.page_key(SHOP_PAGE + "/")
    assert expand.page_key("https://shop.example.com/p?id=1") != expand.page_key("https://shop.example.com/p?id=2")


def test_x0_never_reads_a_page_whose_listing_names_the_brand_only_in_the_image_file():
    # row 13: a blog page ('Blogs -') whose only brand evidence was the failed image's own file name
    blog = cand("https://img.example-cdn.com/barts-traditional-fries-1kg.jpg", "Blogs - Example Mart",
                "https://www.example-mart.com/blogs/news")
    outcome, d = run([blog], bodies={blog.image_url: "http_404"})
    assert d["pages"].fetched == []


def test_x0_never_reads_a_page_where_a_common_word_brand_is_not_in_a_brand_position():
    # row 32 (FAMILY): 'Western Family' pages of Canadian stores took two X0 slots
    spec = build_sku_spec({"name": "FAMILY TOMATO PASTE 400G", "brand": "FAMILY"}, {})
    western = cand("https://img.example-cdn.com/wf.jpg", "Western Family Tomato Paste 400g",
                   "https://www.urbanfare.com/western-family-tomato-paste-400g")
    from catalog_match.score import score_candidate
    rc = RankedCandidate(candidate=western, score=score_candidate(spec, western),
                         fetched=FetchedImage(candidate=western, ok=False, error="http_404"))
    assert rc.score.tier in (1, 2, 3) and any(str(c).startswith("generic_brand_position") for c in rc.score.conflicts)
    assert expand.recovery_pages(spec, [rc]) == []


def test_x0s_one_verifier_call_reads_the_recovered_images_first():
    # The normal flow downloads and reads 8 retailer listings (tier 2: no size in their titles); 4 more are
    # never downloaded, and they rank above the recovered page's own image (a lower soft-quality score: the
    # last ranking key). X0's single verifier call must read that image, not those four.
    read_no = dict(READ_MATCH, size_text="2.5 kg", size_match="no")
    page = "https://www.spinneys.com/en-ae/catalogue/barts-traditional-fries/"
    own = "https://img.example-cdn.com/barts-traditional-fries-main.jpg"
    trigger = cand(SHOP_LISTING_IMG, "Barts Traditional Fries | Spinneys", page, n=1)
    retail = [cand(f"https://img.example-cdn.com/o{i}.jpg", "Barts Traditional Fries | Carrefour UAE",
                   f"https://www.carrefouruae.com/mafuae/en/fries/barts-traditional-fries-{i}/p/{i}", n=i + 1)
              for i in range(1, 12)]
    sharp = (45, 51, 54, 57, 60, 30, 75, 78, 69, 63, 39)                       # quality.assess >= 0.82
    bodies = {c.image_url: packshot_png(seed) for c, seed in zip(retail, sharp)}
    bodies[SHOP_LISTING_IMG] = packshot_png(3)
    bodies[own] = packshot_png(32)                                              # quality.assess 0.64
    readings = {c.image_url: read_no for c in retail}
    readings.update({SHOP_LISTING_IMG: READ_EMBORG, own: READ_MATCH})
    outcome, d = run([trigger] + retail, web=[hit(LULU_PAGE, "Barts Traditional Fries 1kg")],
                     docs={page: product_page("Barts Traditional Fries", own, brand="Barts")},
                     bodies=bodies, readings=readings)
    assert len(d["verifier"].calls) == 3 and own in d["verifier"].calls[2]   # the X0 call
    assert outcome.winner is not None and outcome.winner.candidate.image_url == own
    assert d["web"].calls == [] and d["shop"].calls == []               # X0 was enough: no paid call


class DownOnCall(StubVerifier):
    def __init__(self, readings, down_on):
        super().__init__(readings)
        self.down_on = down_on

    def verify(self, spec, images):
        if len(self.calls) + 1 == self.down_on:
            self.calls.append([f.candidate.image_url for f in images])
            return VerificationResult(status="unknown", calls=1, error="http_503")
        return super().verify(spec, images)


def test_when_x0s_verifier_call_gets_no_answer_no_paid_call_is_made():
    web_p, shop_p = StubProvider("serper_web", [hit(LULU_PAGE, "Barts Traditional Fries 1kg")]), \
        StubProvider("serper_shopping", [])
    exp = expand.Expansion(web=web_p, shopping=shop_p, visual=None,
                           pages=StubPages({SHOP_PAGE: product_page("Barts Traditional Fries 1kg", SHOP_OWN_IMG,
                                                                    brand="Barts")}), max_calls=4)
    verifier = DownOnCall({SHOP_LISTING_IMG: READ_EMBORG, SHOP_OWN_IMG: READ_MATCH}, down_on=2)
    outcome = pipeline.find_product_image(
        SPEC, providers=[StubProvider("serper", [shop_listing()])],
        fetcher=StubFetcher({SHOP_LISTING_IMG: packshot_png(3), SHOP_OWN_IMG: packshot_png(5)}),
        verifier=verifier, expansion=exp)
    assert len(verifier.calls) == 2 and SHOP_OWN_IMG in verifier.calls[1]
    assert web_p.calls == [] and shop_p.calls == []
    assert outcome.failure_code == "VERIFIER_DOWN"


# ---------------------------------------------------------------------------
# X0: the store's own page shows the same picture that failed (same bytes): never read again, never picked
# (live run 2026-10-03: Google filed Ansar Gallery's Yumway page and Tradeling's Green Farm page under
# another product's pack, rows 13 and 36; a store whose own page shows that pack has the wrong image)
# ---------------------------------------------------------------------------

STORE_WRONG = "x0:store_image_wrong"


def test_x0_records_a_store_page_that_shows_the_failed_picture_itself():
    outcome, d = run([shop_listing()], web=[hit(LULU_PAGE, "Barts Traditional Fries 1kg")],
                     docs={SHOP_PAGE: product_page("Barts Traditional Fries 1kg", SHOP_OWN_IMG, brand="Barts"),
                           LULU_PAGE: lulu_html()},
                     bodies={SHOP_LISTING_IMG: packshot_png(3), SHOP_OWN_IMG: packshot_png(3),
                             LULU_IMAGE: packshot_png(7)},
                     readings={SHOP_LISTING_IMG: READ_EMBORG, LULU_IMAGE: READ_MATCH})
    own = next(rc for rc in outcome.ranked if rc.candidate.image_url == SHOP_OWN_IMG)
    assert STORE_WRONG in own.reasons and own.status == "rejected"
    assert outcome.reject_counts.get(STORE_WRONG) == 1
    assert not any(SHOP_OWN_IMG in call for call in d["verifier"].calls)       # never read again
    # the paid steps still look further: the Lulu page is the pick
    assert outcome.decision == "REVIEW_PRESELECTED" and outcome.winner.candidate.image_url == LULU_IMAGE


def test_x0_never_preselects_the_back_of_the_pack_its_page_shows_again():
    # The Spinneys listing has no size in its title (tier 2) and its picture is the back of the pack (UNSURE,
    # other_side). The page's own picture is the same image file under another address, and the page's
    # name states the size (tier 1): the inherited UNSURE must not pre-check the back of the pack.
    page = "https://www.spinneys.com/en-ae/catalogue/barts-traditional-fries_88/"
    listing_img = "https://img.example-cdn.com/barts-back.jpg"
    own_img = "https://img.example-cdn.com/barts-traditional-fries-1kg-back.jpg"
    back = cand(listing_img, "Barts Traditional Fries | Spinneys", page)
    read_back = dict(READ_MATCH, view="other_side")
    outcome, d = run([back], docs={page: product_page("Barts Traditional Fries 1kg", own_img, brand="Barts")},
                     bodies={listing_img: packshot_png(3), own_img: packshot_png(3)},
                     readings={listing_img: read_back, own_img: read_back}, max_calls=1)
    own = next(rc for rc in outcome.ranked if rc.candidate.image_url == own_img)
    assert own.score.tier == 1 and STORE_WRONG in own.reasons
    assert outcome.winner is None or outcome.winner.candidate.image_url != own_img
    assert outcome.decision != "REVIEW_PRESELECTED"


def test_x0_spends_no_verifier_call_on_the_failed_picture_under_another_address():
    # The page's own picture was already in the pool under its address, below the 8 download slots of the
    # normal flow; it is the same file as the failed listing picture. X0 downloads it and must not read it.
    retail = [cand(f"https://img.example-cdn.com/o{i}.jpg", "Barts Traditional Fries | Carrefour UAE",
                   f"https://www.carrefouruae.com/mafuae/en/fries/barts-traditional-fries-{i}/p/{i}", n=i + 1)
              for i in range(1, 9)]
    own_img = "https://img.example-cdn.com/p-77.jpg"                     # no product words in its address
    same = cand(own_img, "Barts fries", "https://www.example-blog.com/barts", n=20)
    read_no = dict(READ_MATCH, size_text="2.5 kg", size_match="no")
    bodies = {c.image_url: packshot_png(40 + i) for i, c in enumerate(retail)}
    bodies.update({SHOP_LISTING_IMG: packshot_png(3), own_img: packshot_png(3)})
    readings = {c.image_url: read_no for c in retail}
    readings.update({SHOP_LISTING_IMG: READ_EMBORG})
    outcome, d = run([shop_listing()] + retail + [same],
                     docs={SHOP_PAGE: product_page("Barts Traditional Fries 1kg", own_img, brand="Barts")},
                     bodies=bodies, readings=readings, max_calls=1)
    assert d["fetcher"].fetched.index(own_img) >= 8                 # downloaded by X0, not the normal flow
    assert not any(own_img in call for call in d["verifier"].calls)  # but never read
    own = next(rc for rc in outcome.ranked if rc.candidate.image_url == own_img)
    assert STORE_WRONG in own.reasons
