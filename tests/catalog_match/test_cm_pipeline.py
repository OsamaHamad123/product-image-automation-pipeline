"""catalog_match.pipeline.find_product_image, end to end with in-process stage doubles.

Providers, fetcher and verifier are stubs, but everything between them is the real
code: query plan, pooled retrieval, identity scoring and ranking, soft quality,
pHash exclusion and decision routing. The stub verifier only supplies what the model
would READ on each image; verify.make_verdict (production code) decides MATCH /
MISMATCH / UNSURE from those readings. Sockets are blocked for every test.
"""

import io
import socket

import pytest
from PIL import Image, ImageDraw

from catalog_match import pipeline, settings
from catalog_match.fetch import phash_distance, phash_hex
from catalog_match.identity import build_sku_spec
from catalog_match.models import Candidate, FetchedImage, ProviderResult, VerificationResult
from catalog_match.verify import make_verdict

MAPPINGS = {
    "almarai": {"brand": "Almarai", "synonyms": ["المراعي", "Al Marai"],
                "excluded_competitors": ["Al Ain", "Nada"], "official_domains": ["almarai.com"]},
}
ROW = {"name": "Almarai Full Fat Fresh Milk 1L", "brand": "Almarai", "category": "Dairy",
       "barcode": "6281007000000"}
SPEC = build_sku_spec(ROW, MAPPINGS)

# The SKU in words a correct image would show, and a sibling / non-packshot reading.
READ_MATCH = {"brand_text": "Almarai", "variant_text": "Full Fat Milk", "size_text": "1 L", "pack_count": 1,
              "view": "front_packshot", "brand_match": "yes", "variant_match": "yes", "size_match": "yes"}
READ_WRONG_VARIANT = dict(READ_MATCH, variant_text="Low Fat Milk", variant_match="no")
READ_UNSURE = dict(READ_MATCH, view="lifestyle")


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    """No network, no dashboard settings: auto-publish off unless a test turns it on."""
    def refuse(*args, **kwargs):
        raise AssertionError(f"network access attempted: {args!r}")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    monkeypatch.setattr(settings, "_config", None)
    monkeypatch.setenv("AUTO_PUBLISH_ENABLED", "false")
    monkeypatch.setenv("AUTO_PUBLISH_BRANDS", "")


# ---------------------------------------------------------------------------
# Images and stage doubles
# ---------------------------------------------------------------------------

def packshot_png(seed: int, size=(800, 800)) -> bytes:
    """A white-background product shot; the seed changes the drawing (and so the pHash)."""
    w, h = size
    img = Image.new("RGB", size, (255, 255, 255))
    d = ImageDraw.Draw(img)
    x0, y0 = int(w * 0.28), int(h * 0.12)
    x1, y1 = int(w * 0.72), int(h * 0.88)
    d.rectangle([x0, y0, x1, y1], fill=((seed * 67) % 200, (seed * 131) % 200, (seed * 29) % 200))
    # seed-dependent label blocks: big structural differences between seeds
    for i in range(6):
        if (seed >> i) & 1:
            yy = y0 + 30 + i * (y1 - y0 - 60) // 6
            d.rectangle([x0 + 20, yy, x1 - 20, yy + (y1 - y0) // 14], fill=(250, 250, 250))
    if seed % 3 == 0:
        d.ellipse([x0 - 60, y0 + 40, x0 + 60, y0 + 160], fill=(20, 20, 160))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def phash_of(data: bytes) -> str:
    return phash_hex(Image.open(io.BytesIO(data)))


def cand(n, title, page_url, provider="serper", gtin=None, domain=""):
    return Candidate(image_url=f"https://img.example-cdn.com/{n}.jpg", page_url=page_url, page_title=title,
                     title=title, domain=domain, provider=provider, rank=n, gtin_on_page=gtin)


class StubProvider:
    """models.Provider: returns fixed candidates for every text query and records the queries."""

    kind = "search"
    fallback = False

    def __init__(self, name, cands, sanctioned=True):
        self.name, self.cands, self.sanctioned = name, list(cands), sanctioned
        self.calls = []

    def search(self, query, hl, spec):
        self.calls.append((query, hl))
        return ProviderResult(provider=self.name, status="ok" if self.cands else "empty", http_status=200,
                              latency_ms=0, candidates=list(self.cands))


class StubLookup(StubProvider):
    """The Open Food Facts GTIN lookup: once per SKU, not a query."""

    kind = "lookup"

    def __init__(self, cands):
        super().__init__("off", cands)
        self.lookups = 0

    def lookup(self, spec):
        self.lookups += 1
        return ProviderResult(provider="off", status="ok" if self.cands else "empty", http_status=200,
                              candidates=list(self.cands))

    def search(self, query, hl, spec):  # pragma: no cover - a lookup provider is never searched
        raise AssertionError("the GTIN lookup must not be sent a text query")


class StubFetcher:
    """models.Fetcher: url -> bytes (ok) or an error code (failed download)."""

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
    """models.Verifier: supplies per-image readings; production make_verdict decides."""

    def __init__(self, readings, status="ok"):
        self.readings = dict(readings)
        self.status = status
        self.calls = []

    def verify(self, spec, images):
        self.calls.append([f.candidate.image_url for f in images])
        if self.status != "ok":
            return VerificationResult(status="unknown", calls=1, error="down", verdicts=[])
        verdicts = [make_verdict(spec, i, self.readings.get(f.candidate.image_url, {}))
                    for i, f in enumerate(images)]
        return VerificationResult(status="ok", verdicts=verdicts, calls=1)


def by_url(outcome):
    return {rc.candidate.image_url: rc for rc in outcome.ranked}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_no_first_hit():
    # The GTIN round (Open Food Facts lookup) brings the brand's official hero banner first:
    # tier 1 by GTIN, official domain, so it outranks everything before download. It answers 403.
    banner = cand(1, "Almarai Full Fat Milk 1L", "https://www.almarai.com/en/products/fresh-milk",
                  provider="off", gtin="6281007000000")
    packshot = cand(2, "Buy Almarai Full Fat Fresh Milk 1L Online - Carrefour UAE",
                    "https://www.carrefouruae.com/mafuae/en/almarai-full-fat-fresh-milk-1l/p/108596")
    sibling = cand(3, "Almarai Low Fat Milk 1L", "https://www.noon.com/uae-en/almarai-low-fat-milk-1l/p/")
    off = StubLookup([banner])
    serper = StubProvider("serper", [sibling, packshot])
    fetcher = StubFetcher({banner.image_url: "http_403", packshot.image_url: packshot_png(1),
                           sibling.image_url: packshot_png(2)})
    verifier = StubVerifier({packshot.image_url: READ_MATCH, sibling.image_url: READ_WRONG_VARIANT})

    outcome = pipeline.find_product_image(SPEC, providers=[serper, off], fetcher=fetcher, verifier=verifier)

    assert off.lookups == 1
    assert outcome.ranked[0].candidate.image_url == banner.image_url, "the banner is the pre-download leader"
    assert outcome.winner is not None and outcome.winner.candidate.image_url == packshot.image_url
    assert outcome.decision == "REVIEW_PRESELECTED"      # auto-publish is off
    rows = by_url(outcome)
    assert rows[banner.image_url].status == "rejected"
    assert "download:http_403" in rows[banner.image_url].reasons
    assert rows[sibling.image_url].status == "rejected"   # variant conflict in its title
    assert [rc.status for rc in outcome.ranked].count("preselected") == 1
    # the failed banner is never shown to the verifier
    assert all(banner.image_url not in batch for batch in verifier.calls)


def test_no_first_hit_auto_publish_when_enabled(monkeypatch):
    monkeypatch.setenv("AUTO_PUBLISH_ENABLED", "true")
    monkeypatch.setenv("AUTO_PUBLISH_BRANDS", "Almarai")
    packshot = cand(2, "Buy Almarai Full Fat Fresh Milk 1L Online - Carrefour UAE",
                    "https://www.carrefouruae.com/mafuae/en/almarai-full-fat-fresh-milk-1l/p/108596")
    outcome = pipeline.find_product_image(
        SPEC, providers=[StubProvider("serper", [packshot])],
        fetcher=StubFetcher({packshot.image_url: packshot_png(1)}),
        verifier=StubVerifier({packshot.image_url: READ_MATCH}))
    assert outcome.decision == "AUTO_PUBLISH"
    assert outcome.winner.candidate.image_url == packshot.image_url


def test_rejects_never_preselected():
    t1_failed = cand(1, "Almarai Full Fat Fresh Milk 1L | Carrefour UAE",
                     "https://www.carrefouruae.com/mafuae/en/almarai-full-fat-milk-1l/p/1")
    t1_ok = cand(2, "Almarai Full Fat Fresh Milk 1L - Lulu UAE",
                 "https://www.luluhypermarket.com/en-ae/almarai-full-fat-fresh-milk-1l/p/2")
    t2_ok = cand(3, "Almarai milk", "https://blog.example.org/almarai-milk")
    cands = [t1_failed, t1_ok, t2_ok]
    bodies = {t1_failed.image_url: "http_403", t1_ok.image_url: packshot_png(4), t2_ok.image_url: packshot_png(5)}

    # 1. The verifier reads a different product on every image: nothing is pre-checked.
    mismatch = {c.image_url: dict(READ_MATCH, brand_text="Al Ain", brand_match="no") for c in cands}
    outcome = pipeline.find_product_image(SPEC, providers=[StubProvider("serper", cands)],
                                          fetcher=StubFetcher(bodies), verifier=StubVerifier(mismatch))
    assert outcome.decision == "REVIEW_UNSELECTED"
    assert outcome.winner is None
    assert not any(rc.status == "preselected" for rc in outcome.ranked)
    rows = by_url(outcome)
    assert rows[t1_ok.image_url].verdict.decision == "MISMATCH"
    assert rows[t1_ok.image_url].status == "rejected"

    # 2. The verifier is down: the downloaded tier-1 image is pre-checked for review, the tier-1
    #    image whose download failed never is.
    outcome = pipeline.find_product_image(SPEC, providers=[StubProvider("serper", cands)],
                                          fetcher=StubFetcher(bodies), verifier=StubVerifier({}, status="unknown"))
    rows = by_url(outcome)
    assert rows[t1_failed.image_url].score.tier == 1
    assert rows[t1_failed.image_url].status == "rejected"
    assert outcome.failure_code == "VERIFIER_DOWN"
    assert outcome.decision == "REVIEW_PRESELECTED"
    assert outcome.winner is rows[t1_ok.image_url]

    # 3. Every download fails: no pick at all.
    failed = {c.image_url: "timeout" for c in cands}
    outcome = pipeline.find_product_image(SPEC, providers=[StubProvider("serper", cands)],
                                          fetcher=StubFetcher(failed), verifier=StubVerifier({}))
    assert outcome.decision == "REVIEW_UNSELECTED" and outcome.failure_code == "DOWNLOAD_FAILED"
    assert not any(rc.status == "preselected" for rc in outcome.ranked)


def test_custom_query_and_exclusions():
    keep = cand(1, "Almarai Full Fat Fresh Milk 1L - Carrefour UAE",
                "https://www.carrefouruae.com/mafuae/en/almarai-full-fat-fresh-milk-1l/p/1")
    rejected_url = cand(2, "Almarai Full Fat Fresh Milk 1L - noon",
                        "https://www.noon.com/uae-en/almarai-full-fat-fresh-milk-1l/p/")
    reupload = cand(3, "Almarai Full Fat Milk 1 L - Lulu UAE",
                    "https://www.luluhypermarket.com/en-ae/almarai-full-fat-milk-1l/p/3")
    keep_png, reupload_png = packshot_png(9), packshot_png(22)
    negative = int(phash_of(reupload_png), 16) ^ 0b10101          # 3 bits away from the re-upload
    negative_hex = f"{negative:016x}"
    assert phash_distance(phash_of(reupload_png), negative_hex) == 3
    assert phash_distance(phash_of(keep_png), negative_hex) > 6, "test images must differ in pHash"

    serper = StubProvider("serper", [keep, rejected_url, reupload])
    fetcher = StubFetcher({keep.image_url: keep_png, rejected_url.image_url: packshot_png(30),
                           reupload.image_url: reupload_png})
    custom = "almarai fresh milk red cap 1 litre"
    outcome = pipeline.find_product_image(
        SPEC, providers=[serper], fetcher=fetcher, verifier=StubVerifier({keep.image_url: READ_MATCH}),
        custom_query=custom, exclude_urls=[rejected_url.image_url + "?w=400"], exclude_phashes=[negative_hex])

    assert [q for q, _ in serper.calls] == [custom], "the custom query replaces the plan, nothing else is sent"
    assert outcome.queries == [custom]
    urls = [rc.candidate.image_url for rc in outcome.ranked]
    assert rejected_url.image_url not in urls, "a reviewer-rejected URL (any rendition) is excluded"
    assert rejected_url.image_url not in fetcher.fetched
    assert reupload.image_url not in urls, "an image within pHash distance 6 of a negative is excluded"
    assert keep.image_url in urls
    assert outcome.winner is not None and outcome.winner.candidate.image_url == keep.image_url
    assert outcome.reject_counts.get("reviewer_negative_phash") == 1


def test_budget():
    # A SKU whose plan wants Q1..Q4 plus R1/R2: Arabic name, valid GTIN, a variant and a size.
    spec = build_sku_spec({"name": "Almarai Full Fat Milk 1L", "name_ar": "حليب المراعي كامل الدسم 1 لتر",
                           "brand": "Almarai", "barcode": "6281007000000"}, MAPPINGS)
    # Only tier-2 evidence (brand, no size on the page): no early stop, relaxations wanted.
    t2 = [cand(n, f"Almarai milk photo {n}", f"https://blog{n}.example.org/almarai-milk") for n in range(1, 13)]
    serper = StubProvider("serper", t2)
    bing = StubProvider("bing_html", t2[:6], sanctioned=False)
    off = StubLookup([])
    fetcher = StubFetcher({c.image_url: packshot_png(40 + i) for i, c in enumerate(t2)})
    verifier = StubVerifier({c.image_url: READ_UNSURE for c in t2})   # never MATCH

    outcome = pipeline.find_product_image(spec, providers=[serper, bing, off], fetcher=fetcher, verifier=verifier)

    assert all(rc.score.tier == 2 for rc in outcome.ranked)
    assert 1 <= len(serper.calls) <= 4 and len(bing.calls) <= 4
    assert len(outcome.queries) <= 4
    assert off.lookups == 1                                            # the lookup is not a query
    assert len(verifier.calls) == 2, "one more call on the next 4 when nothing matched"
    assert outcome.vlm_calls == 2
    assert [len(batch) for batch in verifier.calls] == [4, 4]
    assert not set(verifier.calls[0]) & set(verifier.calls[1])
    assert len(fetcher.fetched) <= 8
    assert outcome.decision == "REVIEW_UNSELECTED"


def test_budget_stops_after_first_call_when_matched():
    t1 = [cand(n, f"Almarai Full Fat Fresh Milk 1L offer {n} - Carrefour UAE",
               f"https://www.carrefouruae.com/mafuae/en/almarai-full-fat-fresh-milk-1l/p/{n}") for n in range(1, 7)]
    verifier = StubVerifier({c.image_url: READ_MATCH for c in t1})
    outcome = pipeline.find_product_image(
        SPEC, providers=[StubProvider("serper", t1)],
        fetcher=StubFetcher({c.image_url: packshot_png(60 + i) for i, c in enumerate(t1)}), verifier=verifier)
    assert len(verifier.calls) == 1 and outcome.vlm_calls == 1
    assert outcome.decision == "REVIEW_PRESELECTED"


def test_empty_pool_is_not_found_and_outage_is_provider_down():
    outcome = pipeline.find_product_image(SPEC, providers=[StubProvider("serper", [])],
                                          fetcher=StubFetcher({}), verifier=StubVerifier({}))
    assert (outcome.decision, outcome.failure_code) == ("NOT_FOUND", "NO_RESULTS")

    class Down(StubProvider):
        def search(self, query, hl, spec):
            self.calls.append((query, hl))
            return ProviderResult(provider=self.name, status="blocked", http_status=200, error="captcha")

    down = Down("bing_html", [], sanctioned=False)
    outcome = pipeline.find_product_image(SPEC, providers=[down], fetcher=StubFetcher({}),
                                          verifier=StubVerifier({}))
    assert (outcome.decision, outcome.failure_code) == ("PROVIDER_DOWN", "PROVIDER_DOWN")
    assert len(down.calls) == 1, "a blocked provider is not asked again for this SKU"
