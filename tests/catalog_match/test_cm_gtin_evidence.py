"""GTIN_POLICY 'evidence' (phase 3): brand + product name is the identity, the barcode only supports it.

The owner's sheet has no barcode column today; barcodes may be added later and may be wrong. So:
  * a page GTIN that MATCHES the sheet's lifts a candidate only where the brand is fully evidenced;
  * a page GTIN that DIFFERS caps the candidate at tier 2 (never tier 1, never auto-published),
    warns the reviewer ('barcode_conflict') and is pre-checked only on a MATCH that read brand,
    size and variant as 'yes'; it stays a hard reject when the brand is missing / another brand
    or a size, pack or variant conflict is present too;
  * an invalid sheet barcode (bad check digit) is no evidence at all;
  * a GTIN query that surfaces other brands never pushes them up.
'strict' keeps the earlier rule (any differing GTIN is a hard reject); 'off' ignores barcodes.
"""

import socket

import pytest

from catalog_match import decide, facade, pipeline, settings
from catalog_match.identity import build_sku_spec
from catalog_match.models import (
    Candidate, CandidateScore, FetchedImage, ProviderHealth, ProviderResult, QualityReport, RankedCandidate,
    SkuSpec, VerificationResult, VlmImageVerdict,
)
from catalog_match.query_plan import build_queries
from catalog_match.score import rank, score_candidate

from test_cm_pipeline import StubFetcher, StubVerifier, packshot_png

MAPPINGS = {
    "almarai": {"brand": "Almarai", "synonyms": ["المراعي", "Al Marai"],
                "excluded_competitors": ["Al Ain", "Nada", "Lacnor"], "official_domains": ["almarai.com"]},
    "nestle": {"brand": "Nestle", "synonyms": ["نستله"], "sub_brands": ["Nido"]},
    "freshly": {"brand": "Freshly", "synonyms": []},
}
SHEET_GTIN = "6281007000024"            # valid check digit
OTHER_GTIN = "4006381333931"            # valid, another product
BAD_GTIN = "6281007000025"              # wrong check digit
SPEC = build_sku_spec({"name": "Almarai Full Fat Milk 1L", "brand": "Almarai", "barcode": SHEET_GTIN}, MAPPINGS)
CARREFOUR = "https://www.carrefouruae.com/mafuae/en/fresh-food/almarai-full-fat-milk-1l/p/108596"
HEALTHY = [ProviderHealth("serper", "ok", 200, query_id="Q1")]
OK = VerificationResult(status="ok", calls=1)


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError(f"network access attempted: {args!r}")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    monkeypatch.setattr(settings, "_config", None)
    monkeypatch.delenv("GTIN_POLICY", raising=False)
    monkeypatch.setenv("AUTO_PUBLISH_ENABLED", "false")
    monkeypatch.setenv("AUTO_PUBLISH_BRANDS", "")


def listing(title, gtin=None, page_url=CARREFOUR, image_url="https://cdn.mafrservices.com/p/1.jpg", **kw):
    return Candidate(image_url=image_url, page_url=page_url, title=title, page_title=title, provider="serper",
                     query_id="Q1", rank=1, gtin_on_page=gtin, **kw)


def reading(**over):
    base = dict(brand_text="Almarai", variant_text="Full Fat Milk", size_text="1 L", view="front_packshot",
                brand_match="yes", variant_match="yes", size_match="yes", decision="MATCH")
    base.update(over)
    return VlmImageVerdict(index=0, **base)


def ranked(spec, cand, verdict=None, size=(1000, 1000), phash=None):
    fi = FetchedImage(candidate=cand, ok=True, content_sha256="ab" * 32, width=size[0], height=size[1],
                      path_or_bytes=b"x", phash=phash)
    return RankedCandidate(candidate=cand, score=score_candidate(spec, cand), fetched=fi,
                           quality=QualityReport(hard_ok=True, quality_score=0.8), verdict=verdict)


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw, policy", [(None, "evidence"), ("STRICT", "strict"), (" off ", "off"),
                                          ("nonsense", "evidence"), ("", "evidence")])
def test_gtin_policy_setting(monkeypatch, raw, policy):
    if raw is not None:
        monkeypatch.setenv("GTIN_POLICY", raw)
    assert settings.gtin_policy() == policy


def test_gtin_policy_default_is_evidence():
    assert settings.DEFAULTS["GTIN_POLICY"] == "evidence"
    import config
    assert getattr(config, "GTIN_POLICY", "evidence") in settings.GTIN_POLICIES


# ---------------------------------------------------------------------------
# Scoring: a differing page GTIN
# ---------------------------------------------------------------------------

def test_differing_gtin_with_full_brand_evidence_is_tier2_not_rejected():
    sc = score_candidate(SPEC, listing("Almarai Full Fat Milk 1L - Carrefour UAE", gtin=OTHER_GTIN))
    assert sc.hard_reject == ()
    assert sc.tier == 2                       # brand + size + variant on a UAE retailer would be tier 1
    assert sc.matched["gtin"] == "mismatch"
    assert f"gtin_mismatch:{'0' + OTHER_GTIN}" in sc.conflicts
    # control: the same listing with the sheet's code (or none) is tier 1
    assert score_candidate(SPEC, listing("Almarai Full Fat Milk 1L - Carrefour UAE", gtin=SHEET_GTIN)).tier == 1
    assert score_candidate(SPEC, listing("Almarai Full Fat Milk 1L - Carrefour UAE")).tier == 1


@pytest.mark.parametrize("title, page_url, also", [
    ("Full Fat Milk 1L", CARREFOUR.replace("almarai-", ""), None),                           # no brand
    ("Al Ain Full Fat Milk 1L", CARREFOUR.replace("almarai", "al-ain"), "competitor_brand"),  # another brand
    ("Almarai Full Fat Milk 2L", CARREFOUR.replace("1l", "2l"), "size_conflict"),             # another size
    ("Almarai Low Fat Milk 1L", CARREFOUR.replace("full-fat", "low-fat"), "variant_conflict:fat"),
    ("Almarai Full Fat Milk 6 x 1L", CARREFOUR, "pack_conflict"),                              # another pack
])
def test_differing_gtin_with_another_conflict_stays_hard_rejected(title, page_url, also):
    sc = score_candidate(SPEC, listing(title, gtin=OTHER_GTIN, page_url=page_url))
    assert sc.tier is None
    assert "gtin_mismatch" in sc.hard_reject
    if also:
        assert also in sc.hard_reject


def test_differing_gtin_with_a_soft_size_conflict_is_rejected():
    # the size differs only in the URL: alone a tier-2 cap, with a differing barcode a reject
    url = "https://www.carrefouruae.com/mafuae/en/fresh-food/almarai-full-fat-milk-2l/p/1"
    assert score_candidate(SPEC, listing("Almarai Full Fat Milk", page_url=url)).hard_reject == ()
    sc = score_candidate(SPEC, listing("Almarai Full Fat Milk", gtin=OTHER_GTIN, page_url=url))
    assert "gtin_mismatch" in sc.hard_reject and sc.tier is None


@pytest.mark.parametrize("name, title, conflict", [
    # the SKU states no fat level, the page a marked one (low fat): another product of the brand
    ("Almarai Milk 1L", "Almarai Low Fat Milk 1L - Carrefour UAE", "unstated_variant:fat"),
    # a single-unit SKU against a listing that also offers the multipack
    ("Almarai Laban Can 330ml", "Almarai Laban Can 330ml / 6 x 330ml - Carrefour UAE", "pack_ambiguous"),
])
def test_differing_gtin_with_an_unstated_variant_or_an_ambiguous_pack_is_rejected(name, title, conflict):
    # Review fix: these soft variant / pack doubts cap at tier 2 on their own; together with a
    # differing barcode they point at another product of the brand and are a hard reject, like
    # the URL-only size and image-file variant conflicts.
    spec = build_sku_spec({"name": name, "brand": "Almarai", "barcode": SHEET_GTIN}, MAPPINGS)
    url = "https://www.carrefouruae.com/mafuae/en/p/108597"          # a slug that states nothing
    alone = score_candidate(spec, listing(title, page_url=url))
    assert alone.hard_reject == () and alone.tier == 2 and alone.conflicts == (conflict,)
    sc = score_candidate(spec, listing(title, gtin=OTHER_GTIN, page_url=url))
    assert sc.tier is None and sc.hard_reject == ("gtin_mismatch",) and conflict in sc.conflicts


def test_low_fat_page_with_another_barcode_is_never_preselected():
    # The verifier may read brand, size and even the variant ('yes': milk is milk) as a match for a
    # SKU that states no fat level; the differing barcode plus the marked variant keeps it out.
    spec = build_sku_spec({"name": "Almarai Milk 1L", "brand": "Almarai", "barcode": SHEET_GTIN}, MAPPINGS)
    c = listing("Almarai Low Fat Milk 1L - Carrefour UAE", gtin=OTHER_GTIN)
    rc = ranked(spec, c, reading(variant_text="Low Fat Milk"))
    out = _route(spec, [rc])
    assert out.winner is None and rc.status == "rejected" and "hard:gtin_mismatch" in rc.reasons


IN_STORE_GTIN = "2000000000015"          # valid, but a restricted-circulation (in-store) number


def test_matching_in_store_code_lifts_nothing():
    # Review fix: in-store codes (GS1 prefixes 02x / 04x / 2xx) are only unique inside one company;
    # another store's page that carries the same number says nothing about the product. Under
    # 'evidence' the match neither makes tier 1 nor counts in the display score.
    spec = build_sku_spec({"name": "Almarai Full Fat Milk 1L", "brand": "Almarai", "barcode": IN_STORE_GTIN},
                          MAPPINGS)
    page = listing("Almarai Milk - Lulu UAE", gtin=IN_STORE_GTIN,
                   page_url="https://www.luluhypermarket.com/en-ae/almarai-milk/p/2")
    sc = score_candidate(spec, page)
    assert sc.tier == 2 and sc.matched["gtin"] is None and sc.identity_score < 1.0
    # control: the same listing with a global code that matches is tier 1 (brand agrees)
    spec_global = build_sku_spec({"name": "Almarai Full Fat Milk 1L", "brand": "Almarai", "barcode": SHEET_GTIN},
                                 MAPPINGS)
    assert score_candidate(spec_global, listing("Almarai Milk - Lulu UAE", gtin=SHEET_GTIN,
                                                page_url="https://www.luluhypermarket.com/en-ae/almarai-milk/p/2")
                           ).tier == 1
    # a DIFFERING in-store code still caps and warns (it can only lower confidence)
    other = score_candidate(spec, listing("Almarai Full Fat Milk 1L - Carrefour UAE", gtin="2912345000011"))
    assert other.tier == 2 and other.matched["gtin"] == "mismatch"


def test_matching_in_store_code_under_strict_keeps_the_earlier_rule(monkeypatch):
    monkeypatch.setenv("GTIN_POLICY", "strict")
    spec = build_sku_spec({"name": "Almarai Full Fat Milk 1L", "brand": "Almarai", "barcode": IN_STORE_GTIN},
                          MAPPINGS)
    page = listing("Almarai Milk - Lulu UAE", gtin=IN_STORE_GTIN,
                   page_url="https://www.luluhypermarket.com/en-ae/almarai-milk/p/2")
    assert score_candidate(spec, page).tier == 1


def test_differing_gtin_with_a_common_word_brand_out_of_place_is_rejected():
    spec = build_sku_spec({"name": "FRESHLY CHICKEN SHAWARMA 350GM", "brand": "FRESHLY", "barcode": SHEET_GTIN},
                          {})
    weak = listing("Seara Chicken Shawarma 350g, freshly prepared", gtin=OTHER_GTIN)
    assert "gtin_mismatch" in score_candidate(spec, weak).hard_reject
    strong = listing("Freshly Chicken Shawarma 350g - Carrefour UAE", gtin=OTHER_GTIN)
    sc = score_candidate(spec, strong)
    assert sc.hard_reject == () and sc.tier == 2


def test_differing_gtin_with_parent_brand_only_is_rejected():
    spec = build_sku_spec({"name": "Nido Fortified Milk Powder 2.25kg", "brand": "Nestle", "barcode": SHEET_GTIN},
                          MAPPINGS)
    parent_only = listing("Nestle Fortified Milk Powder 2.25kg", gtin=OTHER_GTIN,
                          page_url="https://www.carrefouruae.com/mafuae/en/nestle-fortified-milk-powder-2-25kg/p/1")
    assert "gtin_mismatch" in score_candidate(spec, parent_only).hard_reject


def test_invalid_sheet_gtin_is_no_evidence():
    spec = build_sku_spec({"name": "Almarai Full Fat Milk 1L", "brand": "Almarai", "barcode": BAD_GTIN}, MAPPINGS)
    assert spec.gtin is None and spec.gtin_status == "bad_check_digit"
    for gtin in (OTHER_GTIN, BAD_GTIN):
        sc = score_candidate(spec, listing("Almarai Full Fat Milk 1L - Carrefour UAE", gtin=gtin))
        assert sc.tier == 1 and sc.matched["gtin"] is None and not any("gtin" in c for c in sc.conflicts)
    # a hand-built spec that carries an invalid code is treated the same way
    raw = SkuSpec(raw_name=SPEC.raw_name, brand_raw="Almarai", brand_canonical="Almarai",
                  match_brands=SPEC.match_brands, brand_conf="mapped", gtin=BAD_GTIN, size=SPEC.size,
                  variants=SPEC.variants, class_tokens=SPEC.class_tokens)
    sc = score_candidate(raw, listing("Almarai Full Fat Milk 1L - Carrefour UAE", gtin=OTHER_GTIN))
    assert sc.matched["gtin"] is None and sc.tier == 1
    assert build_queries(spec) and not any(q.query_id == "Q4" for q in build_queries(spec))


def test_invalid_page_gtin_is_no_evidence():
    sc = score_candidate(SPEC, listing("Almarai Full Fat Milk 1L - Carrefour UAE", gtin="6.29E+12"))
    assert sc.matched["gtin"] is None and sc.tier == 1


def test_matching_gtin_lifts_only_with_the_brand():
    off = dict(provider="off", page_url="https://world.openfoodfacts.org/product/6281007000024")
    with_brand = Candidate(image_url="https://images.openfoodfacts.org/a.jpg", title="Almarai Milk",
                           page_title="Almarai Milk", gtin_on_page=SHEET_GTIN, **off)
    sc = score_candidate(SPEC, with_brand)
    assert sc.tier == 1 and sc.matched["gtin"] == "match" and sc.identity_score == 1.0
    no_brand = Candidate(image_url="https://images.openfoodfacts.org/b.jpg", title="Full fat milk 1 L",
                         page_title="Full fat milk 1 L", gtin_on_page=SHEET_GTIN, **off)
    sc = score_candidate(SPEC, no_brand)
    assert sc.tier == 3 and sc.matched["gtin"] == "match" and sc.identity_score < 1.0


def test_strict_policy_keeps_the_earlier_hard_reject(monkeypatch):
    monkeypatch.setenv("GTIN_POLICY", "strict")
    sc = score_candidate(SPEC, listing("Almarai Full Fat Milk 1L - Carrefour UAE", gtin=OTHER_GTIN))
    assert sc.tier is None and sc.hard_reject == ("gtin_mismatch",)


def test_off_policy_ignores_barcodes(monkeypatch):
    monkeypatch.setenv("GTIN_POLICY", "off")
    sc = score_candidate(SPEC, listing("Almarai Full Fat Milk 1L - Carrefour UAE", gtin=OTHER_GTIN))
    assert sc.tier == 1 and sc.matched["gtin"] is None and sc.conflicts == ()
    off = Candidate(image_url="https://images.openfoodfacts.org/b.jpg", title="Full fat milk 1 L",
                    page_title="Full fat milk 1 L", gtin_on_page=SHEET_GTIN, provider="off")
    assert score_candidate(SPEC, off).matched["gtin"] is None
    assert not any(q.query_id == "Q4" for q in build_queries(SPEC))
    monkeypatch.setenv("GTIN_POLICY", "evidence")
    assert any(q.query_id == "Q4" for q in build_queries(SPEC))


def test_gtin_query_surfacing_other_brands_never_pushes_them_up():
    # Q4 ('"Almarai" 6281007000024') answers with other brands' listings that print the code;
    # some even carry a page GTIN equal to the sheet's (a wrong or reused barcode).
    q4 = [
        listing("Lacnor Full Cream Milk 1L | EAN 6281007000024", image_url="https://x.test/lacnor.jpg",
                page_url="https://www.noon.com/uae-en/lacnor-full-cream-milk-1l/N1/p/", gtin=SHEET_GTIN),
        listing("Nada Fresh Milk 1L - barcode 6281007000024", image_url="https://x.test/nada.jpg",
                page_url="https://www.barcodelookup.com/6281007000024"),
        listing("Fresh Milk Full Fat 1L EAN 6281007000024", image_url="https://x.test/generic.jpg",
                page_url="https://go-upc.com/barcode/6281007000024", gtin=SHEET_GTIN),
    ]
    right = listing("Almarai Full Fat Milk 1L - Carrefour UAE", image_url="https://cdn.mafrservices.com/ok.jpg")
    scored = [(c, score_candidate(SPEC, c)) for c in q4 + [right]]
    by_url = {c.image_url: s for c, s in scored}
    assert by_url["https://x.test/lacnor.jpg"].tier is None          # a competitor, whatever its barcode
    assert by_url["https://x.test/nada.jpg"].tier is None
    assert by_url["https://x.test/generic.jpg"].tier == 3            # the code alone lifts nothing
    assert rank(scored)[0][0] is right


def test_pipeline_gtin_query_with_other_brands_preselects_the_brand_listing():
    class QueryProvider:
        name, sanctioned, kind, fallback = "serper", True, "search", False

        def __init__(self, by_kind):
            self.by_kind, self.calls = by_kind, []

        def search(self, query, hl, spec):
            self.calls.append(query)
            kind = "gtin" if "6281007000024" in query else "text"
            cands = self.by_kind.get(kind, [])
            return ProviderResult(provider=self.name, status="ok" if cands else "empty", http_status=200,
                                  candidates=list(cands))

    other = Candidate(image_url="https://x.test/lacnor.jpg", page_url="https://www.noon.com/uae-en/lacnor-milk/N1/p/",
                      title="Lacnor Full Cream Milk 1L EAN 6281007000024", rank=1)
    generic = Candidate(image_url="https://x.test/generic.jpg", page_url="https://go-upc.com/barcode/6281007000024",
                        title="Fresh Milk Full Fat 1L EAN 6281007000024", rank=2)
    right = Candidate(image_url="https://cdn.mafrservices.com/ok.jpg", page_url=CARREFOUR,
                      title="Almarai Full Fat Milk 1L - Carrefour UAE", rank=3)
    provider = QueryProvider({"gtin": [other, generic], "text": [right]})
    fetcher = StubFetcher({other.image_url: packshot_png(1), generic.image_url: packshot_png(2),
                           right.image_url: packshot_png(3)})
    # the model reads every packshot as a match: only the identity rules keep the others out
    read = {"brand_text": "Almarai", "variant_text": "Full Fat Milk", "size_text": "1 L", "view": "front_packshot",
            "brand_match": "yes", "variant_match": "yes", "size_match": "yes"}
    verifier = StubVerifier({other.image_url: read, generic.image_url: read, right.image_url: read})
    outcome = pipeline.find_product_image(SPEC, providers=[provider], fetcher=fetcher, verifier=verifier)
    assert outcome.winner is not None and outcome.winner.candidate.image_url == right.image_url
    rows = {rc.candidate.image_url: rc for rc in outcome.ranked}
    assert rows.get(other.image_url) is None or rows[other.image_url].status == "rejected"
    assert rows.get(generic.image_url) is None or rows[generic.image_url].status != "preselected"


# ---------------------------------------------------------------------------
# Decision: never tier 1, never auto-published, pre-checked only on a full MATCH
# ---------------------------------------------------------------------------

def _route(spec, rcs, verification=OK):
    return decide.route(spec, rcs, verification, HEALTHY, set())


def test_barcode_conflict_preselected_only_on_full_match_and_warned(monkeypatch):
    monkeypatch.setenv("AUTO_PUBLISH_ENABLED", "true")
    monkeypatch.setenv("AUTO_PUBLISH_BRANDS", "*")
    c = listing("Almarai Full Fat Milk 1L - Carrefour UAE", gtin=OTHER_GTIN)
    rc = ranked(SPEC, c, reading())
    out = _route(SPEC, [rc])
    assert out.decision == "REVIEW_PRESELECTED" and out.winner is rc
    assert "warn:barcode_conflict" in rc.reasons
    assert "auto_blocked:barcode_conflict" in rc.reasons and "auto_blocked:not_tier1" in rc.reasons
    assert decide.warning_codes(rc.reasons) == ["barcode_conflict"]
    legacy = facade.outcome_to_legacy(out)
    assert legacy["needs_review"] is True and legacy["candidates"][0]["warnings"] == ["barcode_conflict"]


def test_a_clean_match_is_preferred_to_a_barcode_conflict():
    # Review fix: the conflicting page ranks first (its title states the size, the clean one does
    # not), both are tier 2 and both read as a full MATCH. The pick without the doubt is pre-checked.
    conflict = listing("Almarai Full Fat Milk 1L - Carrefour UAE", gtin=OTHER_GTIN)
    clean = listing("Almarai Full Fat Milk - Lulu UAE", image_url="https://www.luluhypermarket.com/medias/2.jpg",
                    page_url="https://www.luluhypermarket.com/en-ae/almarai-full-fat-milk/p/2")
    rc_conflict, rc_clean = ranked(SPEC, conflict, reading()), ranked(SPEC, clean, reading())
    assert rc_conflict.score.tier == rc_clean.score.tier == 2
    assert rank([(conflict, rc_conflict.score), (clean, rc_clean.score)])[0][0] is conflict
    out = _route(SPEC, [rc_conflict, rc_clean])
    assert out.winner is rc_clean and out.decision == "REVIEW_PRESELECTED"
    assert "warn:barcode_conflict" not in rc_clean.reasons and rc_conflict.status == "eligible"
    # alone, the conflicting page is still pre-checked on its full MATCH (with the warning)
    rc_conflict = ranked(SPEC, conflict, reading())
    assert _route(SPEC, [rc_conflict]).winner is rc_conflict and "warn:barcode_conflict" in rc_conflict.reasons


def test_barcode_conflict_without_a_size_reading_is_not_preselected():
    # A SKU without a size can be a MATCH with no size reading; with a differing barcode
    # (the brand's other sizes carry other codes) that is not enough to pre-check it.
    spec = build_sku_spec({"name": "Almarai Full Fat Milk", "brand": "Almarai", "barcode": SHEET_GTIN}, MAPPINGS)
    c = listing("Almarai Full Fat Milk - Carrefour UAE", gtin=OTHER_GTIN)
    rc = ranked(spec, c, reading(size_match="unsure", size_text=""))
    assert rc.score.tier == 2 and rc.verdict.decision == "MATCH"
    out = _route(spec, [rc])
    assert out.winner is None and out.decision == "REVIEW_UNSELECTED"
    assert "gtin:conflict_needs_full_match" in rc.reasons and rc.status == "eligible"
    # the same reading without a barcode conflict is pre-checked
    clean = ranked(spec, listing("Almarai Full Fat Milk - Carrefour UAE"), reading(size_match="unsure", size_text=""))
    assert _route(spec, [clean]).winner is clean


def test_barcode_conflict_on_a_sku_without_variants_needs_no_variant_yes():
    # no variant stated on the SKU: 'unsure' on an absent axis is not a conflict; brand and size are read
    spec = build_sku_spec({"name": "Almarai Milk 1L", "brand": "Almarai", "barcode": SHEET_GTIN}, MAPPINGS)
    c = listing("Almarai Milk 1L - Carrefour UAE", gtin=OTHER_GTIN)
    rc = ranked(spec, c, reading(variant_match="unsure", variant_text=""))
    out = _route(spec, [rc])
    assert out.winner is rc and out.decision == "REVIEW_PRESELECTED"
    assert "warn:barcode_conflict" in rc.reasons


def test_barcode_conflict_with_variant_unsure_on_a_stated_variant_is_not_preselected():
    c = listing("Almarai Full Fat Milk 1L - Carrefour UAE", gtin=OTHER_GTIN)
    rc = ranked(SPEC, c, reading(variant_match="unsure", decision="MATCH"))
    assert not decide.full_match(SPEC, rc)
    out = _route(SPEC, [rc])
    assert out.winner is None and "gtin:conflict_needs_full_match" in rc.reasons


def test_tier1_fallback_never_takes_a_barcode_conflict():
    # defence in depth: even a tier-1 score that carries a differing GTIN (a hand-built score)
    # is not pre-checked on UNSURE / UNKNOWN
    c = listing("Almarai Full Fat Milk 1L - Carrefour UAE", gtin=OTHER_GTIN)
    fake = CandidateScore(tier=1, matched={"gtin": "mismatch", "brand": True, "source_trust": 3})
    fi = FetchedImage(candidate=c, ok=True, width=1000, height=1000, path_or_bytes=b"x")
    for verdict, verification in ((reading(decision="UNSURE", view="other_side"), OK),
                                  (None, VerificationResult(status="unknown", calls=1, error="down"))):
        rc = RankedCandidate(candidate=c, score=fake, fetched=fi, quality=QualityReport(hard_ok=True),
                             verdict=verdict)
        out = _route(SPEC, [rc], verification)
        assert out.winner is None and out.decision == "REVIEW_UNSELECTED"


def test_barcode_conflict_never_auto_publishes_even_with_a_tier1_score(monkeypatch):
    monkeypatch.setenv("AUTO_PUBLISH_ENABLED", "true")
    monkeypatch.setenv("AUTO_PUBLISH_BRANDS", "*")
    c = listing("Almarai Full Fat Milk 1L - Carrefour UAE", gtin=OTHER_GTIN)
    real = score_candidate(SPEC, c)
    fake = CandidateScore(tier=1, matched=dict(real.matched), conflicts=real.conflicts, size_status="match")
    fi = FetchedImage(candidate=c, ok=True, width=1000, height=1000, path_or_bytes=b"x")
    rc = RankedCandidate(candidate=c, score=fake, fetched=fi, quality=QualityReport(hard_ok=True), verdict=reading())
    out = _route(SPEC, [rc])
    assert out.decision == "REVIEW_PRESELECTED" and "auto_blocked:barcode_conflict" in rc.reasons


def test_a_barcode_conflict_match_that_cannot_be_picked_still_lets_the_next_candidates_be_read():
    # Integration review: the second verifier call is skipped once the first batch holds a MATCH. A MATCH on a page
    # whose barcode differs from the sheet's, without a full reading, is never picked by decide.route; it must not
    # stop the pipeline from reading the next tier-1/2 candidates either.
    from test_cm_pipeline import StubProvider, cand

    spec = build_sku_spec({"name": "Almarai Full Fat Milk", "brand": "Almarai", "barcode": SHEET_GTIN}, MAPPINGS)
    pages = [cand(n, f"Almarai Full Fat Milk - Carrefour UAE {n}",
                  f"https://www.carrefouruae.com/mafuae/en/almarai-full-fat-milk/p/{n}", gtin=OTHER_GTIN)
             for n in range(1, 7)]
    no_size = {"brand_text": "Almarai", "variant_text": "Full Fat Milk", "size_text": "", "view": "front_packshot",
               "brand_match": "yes", "variant_match": "yes", "size_match": "unsure"}
    full = dict(no_size, size_text="1 L", size_match="yes")

    class FirstBatchWithoutSize(StubVerifier):
        """The first call reads every image with no size; a later call reads them in full."""

        def verify(self, spec, images):
            self.readings = {f.candidate.image_url: (full if self.calls else no_size) for f in images}
            return super().verify(spec, images)

    verifier = FirstBatchWithoutSize({})
    outcome = pipeline.find_product_image(
        spec, providers=[StubProvider("serper", pages)],
        fetcher=StubFetcher({c.image_url: packshot_png(90 + c.rank) for c in pages}), verifier=verifier)

    by_url = {rc.candidate.image_url: rc for rc in outcome.ranked}
    assert all(rc.score.tier == 2 for rc in outcome.ranked)
    assert len(verifier.calls[0]) == 4
    assert all(by_url[u].verdict.decision == "MATCH" and not decide.full_match(spec, by_url[u])
               for u in verifier.calls[0]), "the first batch: MATCH readings that cannot be picked"
    assert len(verifier.calls) == 2 and outcome.vlm_calls == 2
    assert outcome.decision == "REVIEW_PRESELECTED"
    assert outcome.winner.candidate.image_url in verifier.calls[1]
    assert "warn:barcode_conflict" in outcome.winner.reasons
