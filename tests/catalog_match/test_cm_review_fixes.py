"""Regression tests for the reviewer findings on identity, scoring, verification and routing.

Every case below was reproduced against the previous code (it gave the wrong answer
named in the comment). Sockets are blocked; nothing reaches the network.
"""

import json
import socket

import pytest

from catalog_match import decide, pipeline, settings
from catalog_match import verify as verify_mod
from catalog_match.gtin import is_global_gtin, is_restricted, normalize_gtin
from catalog_match.identity import build_sku_spec
from catalog_match.models import (
    Candidate, FetchedImage, ProviderHealth, QualityReport, RankedCandidate, VerificationResult, VlmImageVerdict,
)
from catalog_match.query_plan import build_queries
from catalog_match.score import score_candidate, strip_site_suffix
from catalog_match.sizes import compare_pack, parse_sizes, product_size
from catalog_match.text_norm import phrase_in
from catalog_match.verify import build_prompt, make_verdict

from test_cm_pipeline import StubFetcher, StubLookup, StubProvider, StubVerifier, packshot_png


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError(f"network access attempted: {args!r}")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    monkeypatch.setattr(settings, "_config", None)
    monkeypatch.setenv("AUTO_PUBLISH_ENABLED", "false")
    monkeypatch.setenv("AUTO_PUBLISH_BRANDS", "")


MAPPINGS = {
    "nestle": {"brand": "Nestle", "synonyms": ["Nestlé", "نستله"], "sub_brands": ["Nido", "Everyday", "Nesquik"]},
    "almarai": {"brand": "Almarai", "synonyms": ["المراعي"], "excluded_competitors": ["Al Rawabi"]},
    "al rawabi": {"brand": "Al Rawabi"},
    "pepsi": {"brand": "Pepsi"},
    "coca cola": {"brand": "Coca-Cola", "synonyms": ["Coca Cola", "Coke"]},
    "pepsico": {"brand": "PepsiCo", "sub_brands": ["Lay's"]},
    "carrefour": {"brand": "Carrefour"},
    "sadia": {"brand": "Sadia"},
    "ferrero": {"brand": "Ferrero", "sub_brands": ["Ferrero Rocher"]},
    "kinder": {"brand": "Kinder"},
}
CARREFOUR_PAGE = "https://www.carrefouruae.com/mafuae/en/p/1"


def cand(title, page_url=CARREFOUR_PAGE, image_url="https://cdn.mafrservices.com/a.jpg", provider="serper", **kw):
    return Candidate(image_url=image_url, page_url=page_url, title=title, page_title=title, provider=provider,
                     sanctioned=provider != "bing_html", **kw)


def spec_of(name, brand, **row):
    return build_sku_spec(dict(row, name=name, brand=brand), MAPPINGS)


def tier(spec, title, **kw):
    return score_candidate(spec, cand(title, **kw))


READ = {"brand_text": "", "variant_text": "", "size_text": "", "pack_count": None, "view": "front_packshot",
        "brand_match": "yes", "variant_match": "yes", "size_match": "yes"}


# ---------------------------------------------------------------------------
# Sub-brands (a sibling sub-brand was tier 1 and auto-published)
# ---------------------------------------------------------------------------

def test_sub_brand_sku_rejects_sibling_and_caps_parent_only():
    nido = spec_of("Nido Milk Powder 900g", "Nestle")
    assert nido.required_brands == ("nido",) and set(nido.sibling_brands) == {"everyday", "nesquik"}
    assert tier(nido, "Nestle Nido Milk Powder 900g").tier == 1
    sib = tier(nido, "Nestle Everyday Milk Powder 900g")
    assert sib.tier is None and "competitor_brand" in sib.hard_reject
    assert tier(nido, "Nestle Nesquik Milk Powder 900g").tier is None
    parent_only = tier(nido, "Nestle Milk Powder 900g")
    assert parent_only.tier == 2 and "sub_brand_missing" in parent_only.conflicts


def test_sub_brand_verdicts_and_prompt():
    nido = spec_of("Nido Milk Powder 900g", "Nestle")
    base = dict(READ, size_text="900 g")
    assert make_verdict(nido, 0, dict(base, brand_text="Nestle Nido")).decision == "MATCH"
    assert make_verdict(nido, 0, dict(base, brand_text="Nestle Everyday")).decision == "MISMATCH"
    assert make_verdict(nido, 0, dict(base, brand_text="Nestle")).decision == "UNSURE"
    prompt = build_prompt(nido, 2)
    assert "Sub-brand that MUST be printed: nido" in prompt and "everyday" in prompt


def test_two_match_sub_brands_block_auto_publish(monkeypatch):
    monkeypatch.setenv("AUTO_PUBLISH_ENABLED", "true")
    monkeypatch.setenv("AUTO_PUBLISH_BRANDS", "*")
    nido = spec_of("Nido Milk Powder 900g", "Nestle")
    a = RankedCandidate(candidate=cand("Nestle Nido Milk Powder 900g"),
                        score=tier(nido, "Nestle Nido Milk Powder 900g"),
                        verdict=VlmImageVerdict(index=0, decision="MATCH", brand_text="Nido", size_text="900 g"))
    b = RankedCandidate(candidate=cand("Nestle Nido 900g", image_url="https://cdn.mafrservices.com/b.jpg"),
                        score=tier(nido, "Nestle Nido Milk Powder 900g"),
                        verdict=VlmImageVerdict(index=1, decision="MATCH", brand_text="Everyday", size_text="900 g"))
    assert decide.identity_conflict(a, b, nido) == "brand"


# ---------------------------------------------------------------------------
# Size column vs name (the multipack was hard-rejected, the single can tier 1)
# ---------------------------------------------------------------------------

def test_size_column_keeps_the_pack_from_the_name():
    spec = spec_of("Pepsi Cola Can 330ml x 6", "Pepsi", size="330ml")
    assert spec.size.base_value == 330 and spec.pack_count == 6
    assert tier(spec, "Pepsi Cola Can 6 x 330ml").tier == 1
    assert tier(spec, "Pepsi Cola Can 330ml").tier != 1


def test_count_only_size_column_takes_the_measure_from_the_name():
    spec = spec_of("Almarai Full Fat Fresh Milk 1L x 6", "Almarai", size="6 pcs")
    assert spec.size.dimension == "volume" and spec.size.base_value == 1000 and spec.pack_count == 6
    assert tier(spec, "Almarai Full Fat Fresh Milk 2L").tier is None          # size conflict now fires


# ---------------------------------------------------------------------------
# Size grammar
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("Tomato 1/2 kg", ("mass", 500.0, None)),
    ("Milk ½ L", ("volume", 500.0, None)),
    ("Rice 1 1/2 kg", ("mass", 1500.0, None)),
    ("Almarai Milk 1L (4+1 Free)", ("volume", 1000.0, 5)),
    ("Al Rawabi Fresh Milk Full Fat 2L Twin Pack", ("volume", 2000.0, 2)),
])
def test_fractions_and_worded_packs(text, expected):
    s = product_size(parse_sizes(text, "title"))
    assert (s.dimension, s.base_value, s.pack_count) == expected


@pytest.mark.parametrize("text", ["Almarai Laban 1-5l", "Tide 2.5-3kg"])
def test_ranges_are_ambiguous(text):
    assert product_size(parse_sizes(text, "title")) == "ambiguous"
    assert build_sku_spec({"name": text, "brand": "x"}, {}).size is None


def test_pieces_in_one_box_are_not_a_pack_conflict():
    rocher = spec_of("Ferrero Rocher Chocolate T16 200g", "Ferrero", size="200g")
    s = tier(rocher, "Ferrero Rocher Premium Chocolates 16 Pieces 200g")
    assert s.tier == 2 and not s.hard_reject                    # kept for review, not proven for auto
    assert compare_pack(None, parse_sizes("Ferrero Rocher 16 pcs 200g", "title")) == "ambiguous"
    # with a volume, 'N pcs' is still a pack
    assert compare_pack(None, parse_sizes("Laban 180ml 6 pcs", "title")) == "conflict"
    twin = spec_of("Al Rawabi Fresh Milk Full Fat 2L", "Al Rawabi")
    assert "pack_conflict" in tier(twin, "Al Rawabi Fresh Milk Full Fat 2L Twin Pack").hard_reject


def test_sku_stating_pieces_next_to_a_mass_keeps_its_pack_listings():
    """'Kinder Bueno 43g 6 pcs' is one box of 6 or a 6-pack: the 6 x 43g listing is never a hard reject."""
    bueno = spec_of("Kinder Bueno 43g 6 pcs", "Kinder")
    assert tier(bueno, "Kinder Bueno 43g 6 pcs").tier == 1                   # the same wording
    six = tier(bueno, "Kinder Bueno 6 x 43g")
    assert six.tier == 2 and not six.hard_reject
    assert tier(bueno, "Kinder Bueno 43g").tier == 2                         # a single bar: not proven
    assert "pack_conflict" in tier(bueno, "Kinder Bueno 3 x 43g").hard_reject
    assert make_verdict(bueno, 0, dict(READ, brand_text="Kinder", size_text="6 x 43 g")).decision != "MISMATCH"


# ---------------------------------------------------------------------------
# Variant lexicon
# ---------------------------------------------------------------------------

def test_diet_and_zero_sugar_are_different_lines():
    diet = spec_of("Pepsi Diet Can 330ml", "Pepsi")
    s = tier(diet, "Pepsi Zero Sugar Cola Can 330ml")
    assert s.tier == 2 and any(c.startswith("soft_variant_conflict:sugar") for c in s.conflicts)
    coke = spec_of("Coca-Cola Zero Sugar Can 330ml", "Coca-Cola")
    assert tier(coke, "Diet Coke Can 330ml").tier == 2
    assert tier(diet, "Pepsi Diet Can 330ml").tier == 1
    assert "variant_conflict:sugar" in tier(diet, "Pepsi Regular Can 330ml").hard_reject
    # the verifier reading 'Zero Sugar' for a Diet SKU is not a MATCH
    v = make_verdict(diet, 0, dict(READ, brand_text="Pepsi", variant_text="Zero Sugar", size_text="330 ml",
                                   variant_match="unsure"))
    assert v.decision == "UNSURE"


def test_unstated_form_is_never_tier1():
    milk = spec_of("Almarai Full Fat Milk 1L", "Almarai")
    assert tier(milk, "Almarai Long Life Full Fat Milk 1L").tier == 2
    assert tier(milk, "Almarai Fresh Full Fat Milk 1L").tier == 2
    chicken = spec_of("Sadia Chicken Breast 1kg", "Sadia")
    assert tier(chicken, "Sadia Frozen Chicken Breast 1kg").tier == 2
    fresh = spec_of("Almarai Fresh Full Fat Milk 1L", "Almarai")
    assert tier(fresh, "Almarai Fresh Full Fat Milk 1L").tier == 1
    assert "variant_conflict:form" in tier(fresh, "Almarai Long Life Full Fat Milk 1L").hard_reject


# ---------------------------------------------------------------------------
# classify decides from verbatim readings (D6)
# ---------------------------------------------------------------------------

LOW = spec_of("Almarai Low Fat Fresh Milk 1L", "Almarai")
LOW_READ = dict(READ, brand_text="Almarai", variant_text="Low Fat Fresh Milk", size_text="1 L")


@pytest.mark.parametrize("over,expected", [
    ({}, "MATCH"),
    ({"variant_text": "Full Fat"}, "MISMATCH"),                      # printed variant contradicts the SKU
    ({"brand_text": "Al Rawabi"}, "MISMATCH"),                       # printed brand is a competitor
    ({"brand_text": ""}, "UNSURE"),                                  # brand not readable
    ({"size_text": "", "size_match": "unsure"}, "UNSURE"),           # size not readable
    ({"variant_match": "unsure"}, "UNSURE"),                         # SKU states variants
    ({"view": "banner"}, "MISMATCH"),
    ({"view": "not_product"}, "MISMATCH"),
    ({"view": "multi_product"}, "MISMATCH"),
    ({"view": "lifestyle"}, "UNSURE"),
    ({"view": "other_side"}, "UNSURE"),
])
def test_classify_uses_verbatim_readings(over, expected):
    assert make_verdict(LOW, 0, dict(LOW_READ, **over)).decision == expected


def test_multipack_needs_pack_evidence():
    spec = spec_of("Pepsi Cola Can 24 x 330ml", "Pepsi")
    base = dict(READ, brand_text="Pepsi", variant_text="", size_text="330 ml")
    assert make_verdict(spec, 0, base).decision == "UNSURE"                       # a single can
    assert make_verdict(spec, 0, dict(base, pack_count=24)).decision == "MATCH"
    assert make_verdict(spec, 0, dict(base, size_text="24 x 330 ml")).decision == "MATCH"
    assert make_verdict(spec, 0, dict(base, view="multi_product", pack_count=24)).decision == "UNSURE"


def test_banner_is_never_preselected():
    spec = spec_of("Almarai Fresh Full Fat Milk 1L", "Almarai")
    c = cand("Almarai Fresh Full Fat Milk 1L", image_url="https://cdn.mafrservices.com/hero-banner.jpg")
    banner = make_verdict(spec, 0, dict(READ, brand_text="Almarai", variant_text="Fresh Full Fat",
                                        size_text="1 L", view="banner"))
    fi = FetchedImage(candidate=c, ok=True, content_sha256="a" * 64, width=1600, height=600, path_or_bytes=b"x")
    rc = RankedCandidate(candidate=c, score=score_candidate(spec, c), fetched=fi,
                         quality=QualityReport(hard_ok=True, quality_score=0.5), verdict=banner)
    assert rc.score.tier == 1
    out = decide.route(spec, [rc], VerificationResult(status="ok", calls=1),
                       [ProviderHealth("serper", "ok", 200, query_id="Q1")], set())
    assert out.decision == "REVIEW_UNSELECTED" and out.winner is None and rc.status == "rejected"


# ---------------------------------------------------------------------------
# GTIN evidence (an unrelated Open Food Facts record was tier 1 and stopped retrieval)
# ---------------------------------------------------------------------------

def test_gtin_match_alone_is_not_tier1():
    spec = spec_of("Almarai Fresh Full Fat Milk 1L", "Almarai", barcode="6281007000024")
    off = Candidate(image_url="https://images.openfoodfacts.org/x.jpg", page_url="https://world.openfoodfacts.org/p",
                    title="Emmental rape", page_title="Emmental rape", provider="off", gtin_on_page=spec.gtin,
                    sanctioned=True)
    assert score_candidate(spec, off).tier == 2
    ok = Candidate(image_url="https://images.openfoodfacts.org/y.jpg", page_url="https://world.openfoodfacts.org/p",
                   title="Almarai Milk", page_title="Almarai Milk", provider="off", gtin_on_page=spec.gtin,
                   sanctioned=True)
    assert score_candidate(spec, ok).tier == 1


def test_restricted_circulation_codes_are_not_global():
    gtin14, status = normalize_gtin("2012345000001")
    assert status == "ok" and is_restricted(gtin14) and not is_global_gtin("2012345000001")
    assert not is_restricted(normalize_gtin("6281007000024")[0])
    spec = spec_of("Tomato Local 1kg", "Almarai", barcode="2012345000001")
    assert not any(q.query_id == "Q4" for q in build_queries(spec))


def test_off_record_alone_never_stops_retrieval():
    spec = spec_of("Almarai Fresh Full Fat Milk 1L", "Almarai", barcode="6281007000024")
    off = Candidate(image_url="https://images.openfoodfacts.org/x.jpg", page_url="https://world.openfoodfacts.org/p",
                    title="Almarai Fresh Full Fat Milk 1L", page_title="Almarai Fresh Full Fat Milk 1L",
                    provider="off", gtin_on_page=spec.gtin, sanctioned=True)
    assert score_candidate(spec, off).tier == 1
    serper = StubProvider("serper", [])
    pipeline.find_product_image(spec, providers=[serper, StubLookup([off])],
                                fetcher=StubFetcher({off.image_url: packshot_png(3)}), verifier=StubVerifier({}))
    assert len(serper.calls) > 1, "the web queries after Q1 must still run"


# ---------------------------------------------------------------------------
# Reviewer negatives use the pool's URL key
# ---------------------------------------------------------------------------

def test_reviewer_negative_keeps_identifying_query():
    spec = spec_of("Almarai Fresh Full Fat Milk 1L", "Almarai")
    neg = {"urls": ["https://shop.example.ae/_next/image?url=%2Fp%2Fsibling.png&w=640"]}
    other = cand("Almarai Milk", image_url="https://shop.example.ae/_next/image?url=%2Fp%2Fcorrect.png&w=640")
    same = cand("Almarai Milk", image_url="https://shop.example.ae/_next/image?url=%2Fp%2Fsibling.png&w=1080")
    assert "reviewer_negative" not in score_candidate(spec, other, neg).hard_reject
    assert "reviewer_negative" in score_candidate(spec, same, neg).hard_reject


# ---------------------------------------------------------------------------
# Brand text matching
# ---------------------------------------------------------------------------

def test_possessive_brand_matches_unpunctuated_spelling():
    assert phrase_in("Lay's", "Lays Classic Salted Potato Chips 170g")
    assert phrase_in("Lays", "Lay's Classic")
    lays = spec_of("Lay's Classic Salted Chips 170g", "PepsiCo")
    assert score_candidate(lays, cand("Lays Classic Salted Potato Chips 170g")).matched["brand"] is True


def test_private_label_does_not_match_the_store_name():
    own = spec_of("Carrefour Full Cream Milk 1L", "Carrefour", size="1L")
    other = tier(own, "Buy Almarai Full Fat Fresh Milk 1L Online - Shop on Carrefour UAE")
    assert other.tier is None and "competitor_brand" in other.hard_reject
    assert tier(own, "Carrefour Full Cream Milk 1L | Carrefour UAE").tier == 1
    assert strip_site_suffix("India Gate Basmati Rice Classic 5kg | Lulu UAE") == "India Gate Basmati Rice Classic 5kg"
    assert strip_site_suffix("Nido Milk Powder 900g at Amazon.ae") == "Nido Milk Powder 900g"


# ---------------------------------------------------------------------------
# Verifier numbering
# ---------------------------------------------------------------------------

class _Resp:
    status_code = 200
    headers = {}

    def __init__(self, entries):
        self._payload = {"candidates": [{"content": {"parts": [{"text": json.dumps({"images": entries})}]},
                                         "finishReason": "STOP"}]}

    def json(self):
        return self._payload


def test_zero_based_image_index_fails_closed(monkeypatch):
    import io
    from PIL import Image
    spec = spec_of("Almarai Fresh Full Fat Milk 1L", "Almarai")

    def fetched(i):
        buf = io.BytesIO()
        Image.new("RGB", (400, 400), (i * 40, 80, 120)).save(buf, "PNG")
        return FetchedImage(candidate=Candidate(image_url=f"https://a.ae/{i}.png"), ok=True, path_or_bytes=buf.getvalue())

    entries = [dict(READ, image_index=0, brand_text="Almarai"), dict(READ, image_index=1, brand_text="X")]
    monkeypatch.setattr(verify_mod.requests, "post", lambda *a, **k: _Resp(entries))
    v = verify_mod.GeminiVerifier(api_key="k", model="m", breaker=verify_mod.CircuitBreaker(), sleep=lambda s: None)
    result = v.verify(spec, [fetched(1), fetched(2)])
    assert result.status == "unknown" and result.error.startswith("schema_error")
    assert [x.decision for x in result.verdicts] == ["UNKNOWN", "UNKNOWN"]
