"""Live run of 2026-10-04 19:33 (laqta_run_2026-10-04_1933.json, run on 62ff4b6): four of the nine rows left without
a pick had the right picture among their candidates, and the cases that must stay as they are.

Every row test is built from the real row: the sheet cells, the listing titles, page and image URLs and the label
reader's readings are copied from the run export, in its rank order. Listings are scored again with
score.score_candidate, readings are classified with verify.make_verdict.

    rows 3, 15  Sharjah Co-op (sharjahcoop.ae) is a UAE retailer: with the run's tiers its page alone corroborates
                the tier-2 label (tier2_corroborated); scored again its listing makes tier 1 (tier1_unsure)
    row 9       '840ge' on an 850G SKU read size 'no' everywhere: a size within the tolerance is UNSURE with the
                review warning 'size_close', never MATCH; an exact or a far size stays MISMATCH
    row 5       a 'no' the reader's own verbatim text cannot support (no size printed, every word of the
                description read) is UNSURE ('vlm:flag_overruled:<flag>'); another brand's picture and a 'no' the
                text supports ('FLAKES' for 'SHREDDED') stay MISMATCH
None of these paths ever auto-publishes: each one is an UNSURE reading, and AUTO_PUBLISH needs a MATCH.
"""

import dataclasses
import socket

import pytest

from catalog_match import decide, expand, settings
from catalog_match.identity import build_sku_spec, description_words
from catalog_match.models import (
    Candidate, FetchedImage, ProviderHealth, QualityReport, RankedCandidate, VerificationResult,
)
from catalog_match.query_plan import build_queries
from catalog_match.score import score_candidate, source_trust, strip_site_suffix, trusted_domains
from catalog_match.sizes import parse_sizes
from catalog_match.verifiers.cascade import needs_second_look
from catalog_match.verify import (
    MATCH, MISMATCH, UNSURE, make_verdict, multipack_unit_image, overruled_flags, size_close,
)

OK = VerificationResult(status="ok", calls=1)
HEALTHY = [ProviderHealth("serper", "ok", 200, query_id="Q1")]


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError(f"network access attempted: {args!r}")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    monkeypatch.setattr(settings, "_config", None)
    monkeypatch.setenv("AUTO_PUBLISH_ENABLED", "false")
    monkeypatch.setenv("AUTO_PUBLISH_BRANDS", "")
    monkeypatch.setenv("GTIN_POLICY", "evidence")


def spec_of(name, brand, name_ar="", brand_ar="", mappings=None):
    return build_sku_spec({"name": name, "brand": brand, "name_ar": name_ar, "brand_ar": brand_ar}, mappings or {})


def read(brand, variant, size="", view="front_packshot", brand_match="yes", variant_match="yes",
         size_match="unsure", pack=1):
    """One label reading as the export records it (vlm texts + label_reader flags)."""
    return {"brand_text": brand, "variant_text": variant, "size_text": size, "pack_count": pack, "view": view,
            "brand_match": brand_match, "variant_match": variant_match, "size_match": size_match}


def listing(spec, n, title, page_url, image_url, reading=None, size=(1000, 1000), tier=None):
    """A candidate scored again from its recorded title and URLs, fetched, read by the label reader. tier, when
    given, is the tier the run recorded (scripts/reroute_export.py routes with it)."""
    cand = Candidate(image_url=image_url, page_url=page_url, title=title, provider="serper", query_id="Q1", rank=n)
    fetched = FetchedImage(candidate=cand, ok=True, width=size[0], height=size[1])
    score = score_candidate(spec, cand)
    if tier is not None:
        score = dataclasses.replace(score, tier=tier)
    verdict = make_verdict(spec, n - 1, reading) if reading is not None else None
    return RankedCandidate(candidate=cand, score=score, fetched=fetched,
                           quality=QualityReport(hard_ok=True, quality_score=0.8), verdict=verdict)


def route(spec, rcs):
    """decide.route on the candidates in the run's rank order."""
    return decide.route(spec, list(rcs), OK, HEALTHY, set())


def warns(rc):
    return decide.warning_codes(rc.reasons)


def decisions(rcs):
    return [rc.verdict.decision if rc.verdict is not None else None for rc in rcs]


# ---------------------------------------------------------------------------
# Fix 1: Sharjah Co-op is a UAE retailer (rows 3 and 15)
# ---------------------------------------------------------------------------

def test_sharjah_coop_is_a_listed_uae_retailer_and_the_other_coops_are_not():
    assert "sharjahcoop.ae" in trusted_domains()["uae_retailers"]
    spec = spec_of("SHAMA ARABIC CHICKEN MASALA 225G", "SHAMA")
    page = Candidate(image_url="https://www.sharjahcoop.ae/medias/x.jpg",
                     page_url="https://www.sharjahcoop.ae/en/shama-arabic-chicken-masala-225g/p/6291100790760")
    assert source_trust(spec, page) == (3, "uae_retailer")
    # an earlier run showed wrong pictures from these: they stay generic
    for host in ("www.emcoop.ae", "emiratescoop.suppy.app"):
        other = Candidate(image_url=f"https://{host}/x.jpg", page_url=f"https://{host}/shama-arabic-chicken-masala")
        assert source_trust(spec, other) == (0, "generic"), host


@pytest.mark.parametrize("title, kept", [
    ("Virginia Tuna In Sf Oil W/Chilli 2X185g | Sharjah Co-operative Society", "Virginia Tuna In Sf Oil W/Chilli 2X185g"),
    ("SUPER TASTY Light Meat Solid Tuna In Sunflower Oil 185g | Sharjah  Co-operative Society",
     "SUPER TASTY Light Meat Solid Tuna In Sunflower Oil 185g"),
    ("Shama Arabic Chicken Masala 225g - Sharjah Coop", "Shama Arabic Chicken Masala 225g"),
    ("Shama Arabic Chicken Masala 225g | Sharjah Co-op UAE", "Shama Arabic Chicken Masala 225g"),
    ("Shama Arabic Chicken Masala 225g | Sharjah Cooperative Society", "Shama Arabic Chicken Masala 225g"),
    ("Buy Shama Arabic Chicken Masala 225g online at Sharjahcoop", "Buy Shama Arabic Chicken Masala 225g"),
])
def test_the_store_name_segment_is_dropped_before_brand_matching(title, kept):
    assert strip_site_suffix(title) == kept


def test_the_store_name_is_never_the_brand_of_a_listing():
    # a sheet brand that is the store's own name never matches another brand's listing through its title segment
    spec = spec_of("SHARJAH COOP TUNA IN SUNFLOWER OIL 185G", "SHARJAH COOP")
    cand = Candidate(image_url="https://www.sharjahcoop.ae/medias/6297000958187-1200Wx1200H-001.jpg",
                     page_url="https://www.sharjahcoop.ae/en/virginia-tuna-in-sunflower-oil-185g/p/6297000958187",
                     title="Virginia Tuna In Sunflower Oil 185g | Sharjah Coop")
    score = score_candidate(spec, cand)
    assert not score.matched["brand"] and score.tier == 3


def test_the_site_groups_change_only_in_the_second_web_group():
    spec = spec_of("SHAMA ARABIC CHICKEN MASALA 225G", "SHAMA")
    first, second = expand.site_groups(spec)
    # X1 (always sent in the expansion round) is unchanged; X5 (sent only when calls remain and nothing turned
    # up) gains one site: operator, no new query
    assert first == ["luluhypermarket.com", "carrefouruae.com", "amazon.ae", "noon.com", "talabat.com"]
    assert second == ["kibsons.com", "spinneys.com", "unioncoop.ae", "choithrams.com", "sharjahcoop.ae"]
    assert len(expand.site_groups(spec)) == 2
    # the planned Q3 has its own fixed retailer list
    assert all("sharjahcoop" not in q.text for q in build_queries(spec))


ROW3 = spec_of("VIRGINIA TUNA IN SUNFLOWER OIL WITH CHILLI 2X185G", "VIRGINIA", "فرجينيا تونة بزيت حار 185×2",
               "فرجينيا")
NOON_VIRGINIA = "https://www.noon.com/uae-ar/grocery-store/canned-dry-and-packaged-foods/tuna/virginia/"
NOON_VIRGINIA_TITLE = "التونة من فيرجينيا في الإمارات | خصم 30-75% | دبي وأبوظبي | نون"
SHARJAH_VIRGINIA_IMAGE = (
    "https://www.sharjahcoop.ae/medias/6297000958187-1200Wx1200H-001.jpg?context=bWFzdGVyfHNjc3wxMTg4MzJ8aW1hZ2UvanBl"
    "Z3xhR1ZtTDJobE5pODVNVEUzTmpjd056TTFPVEF5THpZeU9UY3dNREE1TlRneE9EZGZNVEl3TUZkNE1USXdNRWhmTURBeExtcHdad3w1MGIxNzg3"
    "MWFjZGUwYWVkYTMzY2ZkMTk5YTM3OWJhMThmYzQwZDE3OWU0NWIwYzJhNjNhMjcyM2MxMWVjZjA1")


def row3(recorded_tiers=False):
    s = ROW3

    def t(tier):
        return tier if recorded_tiers else None

    return [
        listing(s, 1, "Virginia Tuna In Sf Oil W/Chilli 2X185g | Sharjah Co-operative Society",
                "https://www.sharjahcoop.ae/en/virginia-tuna-in-sf-oil-w-chilli-2x185gm/p/6297000958187"
                "?srsltid=AU7gw4Xp61HteOjtuu0JvY1J8Ra1mbA8iGcHW2tIlvgzhiAM68aRLE3_",
                SHARJAH_VIRGINIA_IMAGE,
                read("Virginia", "TUNA With Chilli", "185g", size_match="no"), (1200, 1200), t(2)),
        listing(s, 2, "تسوق VIRGINIA وساندويتش تونة فرجينيا في زيت عباد الشمس أونلاين في الإمارات",
                "https://www.noon.com/uae-ar/virginia-tuna-sandwich-in-sunflower-oil/ZA7837403574A22EE6657Z/p/",
                "https://f.nooncdn.com/p/pzsku/ZA7837403574A22EE6657Z/45/1751270692/8315e27c-b650-46f7-9557-"
                "47f87cc66325.jpg",
                read("Virginia", "WHITE MEAT TUNA IN SUNFLOWER", variant_match="no", size_match="no", pack=None),
                (660, 900), t(2)),
        listing(s, 3, "VIRGINIA Virginia Skip Jack Tuna in Sunflower Oil | Best Price UAE | Dubai,  Abu Dhabi",
                "https://www.noon.com/uae-en/virginia-skip-jack-tuna-in-sunflower-oil/ZA951F93C7B85608CB13AZ/p/",
                "https://f.nooncdn.com/p/pzsku/ZA951F93C7B85608CB13AZ/45/1751270669/a7d51f40-bc0e-490c-96e3-"
                "ae08a2cf6f56.jpg",
                read("Virginia", "SKIPJACK IN SUNFLOWER", variant_match="no", size_match="no", pack=None),
                (660, 900), t(2)),
        listing(s, 4, "Virginia Tuna Light Meat In Sunflower Oil",
                "https://www.grandiose.ae/virginia-tuna-light-meat-in-sunflower-oil8286"
                "?srsltid=AU7gw4Vw_wruv5dbZ19JH7zu36RT-1WnqGfKBGHMBXA2aMSlFTZdUxGp",
                "https://www.grandiose.ae/media/catalog/product/cache/6517c62f5899ad6aa0ba23ceb3eeff97/6/2/"
                "6297000958286_1.jpg",
                read("Virginia", "Tuna in Sunflower Oil with Chilli", size_match="no", pack=3), (265, 265), t(2)),
        listing(s, 5, NOON_VIRGINIA_TITLE, NOON_VIRGINIA,
                "https://f.nooncdn.com/p/pzsku/Z9BA9D0813B2CCDB41C8DZ/45/_/1777962793/8e902356-04bd-4db7-b4c7-"
                "f4213f0f53a9.jpg",
                read("فرجينيا", "سكيب جاك بزيت دوار الشمس و الفلفل الحار", variant_match="no", size_match="no",
                     pack=None), (660, 900), t(2)),
        listing(s, 6, NOON_VIRGINIA_TITLE, NOON_VIRGINIA,
                "https://f.nooncdn.com/p/94c7ee8147735dd094b6d0435646e825%7Cpzsku/Z9BA9D0813B2CCDB41C8DZ/45/"
                "1770123825/7006d50b-a0a7-401f-b58f-fec067e13490.jpg",
                read("Virginia", "TUNA FOR SANDWICH (FLAKES) IN SUNFLOWER OIL", "170 GM", variant_match="no",
                     size_match="no", pack=None), (660, 900), t(2)),
        listing(s, 7, NOON_VIRGINIA_TITLE, NOON_VIRGINIA,
                "https://f.nooncdn.com/p/94c7ee8147735dd094b6d0435646e825%7Cpzsku/Z9BA9D0813B2CCDB41C8DZ/45/"
                "1770123793/6e8b1e1c-3b1f-498d-a676-d0d22a6a1532.jpg",
                read("", "", view="other_side", brand_match="unsure", variant_match="unsure", size_match="no",
                     pack=None), (660, 900), t(2)),
        listing(s, 8, NOON_VIRGINIA_TITLE, NOON_VIRGINIA,
                "https://f.nooncdn.com/p/pzsku/Z412120E10B1C67B9094FZ/45/_/1779700509/d49d3b03-7ef4-4710-adca-"
                "a1a23dc5ac57.jpg",
                read("Virginia", "Tuna IN SUNFLOWER OIL", "3x170g", variant_match="no", size_match="no", pack=3),
                (1000, 1000), t(2)),
    ]


def test_row3_the_sharjah_coop_unit_picture_is_preselected_with_the_runs_tiers():
    rcs = row3(recorded_tiers=True)
    assert decisions(rcs) == [UNSURE, MISMATCH, MISMATCH, MISMATCH, MISMATCH, MISMATCH, UNSURE, MISMATCH]
    assert multipack_unit_image(ROW3, rcs[0].verdict)
    assert rcs[0].score.matched["source_class"] == "uae_retailer"
    out = route(ROW3, rcs)
    assert out.decision == "REVIEW_PRESELECTED" and out.winner is rcs[0]
    # one domain, but a UAE retailer's page: the trusted page corroborates on its own
    assert "preselected:tier2_corroborated" in out.winner.reasons
    assert warns(out.winner) == ["vlm_unsure", "multipack_unit_image"]
    assert "auto_blocked:not_vlm_match" in out.winner.reasons


def test_row3_scored_again_the_sharjah_coop_listing_is_tier_1():
    rcs = row3()
    assert rcs[0].score.tier == 1 and rcs[0].score.matched["source_class"] == "uae_retailer"
    out = route(ROW3, rcs)
    assert out.decision == "REVIEW_PRESELECTED" and out.winner is rcs[0]
    assert "preselected:tier1_unsure" in out.winner.reasons
    assert {"vlm_unsure", "multipack_unit_image"} <= set(warns(out.winner))


ROW15 = spec_of("SHAMA ARABIC CHICKEN MASALA 225G", "SHAMA", "شما بهارات الدجاج العربية 225 غرام", "شما")
SHARJAH_SHAMA = "https://www.sharjahcoop.ae/en/shama-arabic-chicken-masala-225g/p/6291100790760"
SHARJAH_SHAMA_TITLE = "Shama Arabic Chicken Masala 225g | Sharjah Co-operative Society"


def row15(recorded_tiers=False, host="www.sharjahcoop.ae"):
    s = ROW15

    def t(tier):
        return tier if recorded_tiers else None

    page = SHARJAH_SHAMA.replace("www.sharjahcoop.ae", host)
    return [
        listing(s, 1, SHARJAH_SHAMA_TITLE,
                page + "?srsltid=AU7gw4U_O-r9BkQI9fIFQwxgs7YCRHps3cSBa_AHQ5zL8-gT8OwHWnHB",
                "https://www.sharjahcoop.ae/medias/6291100790760-1200Wx1200H-002.jpg?context=bWFzdGVyfHNjc3wzMDk5NDd8"
                "aW1hZ2UvanBlZ3xjMk56TDJnMk55OW9PRGt2T0RnMk1qYzJPVFkwTXpVMU1DNXFjR2N8NTQxYTlkN2E2YzUyM2EzNjc1YzhmNzNi"
                "NGZmYzU0MTQ3NDA0NmNhYWU2M2ZjOTUwMmFmODdjZTYxM2NlMTY5Ng",
                read("Shama", "ARABIC CHICKEN Spice Mix"), (1200, 1200), t(2)),
        listing(s, 2, SHARJAH_SHAMA_TITLE,
                page + "?srsltid=AU7gw4WhiuWxTnB1fK92uLf_HUyMo5aq6cNblBCBmTlLGATH5Y-ySfpa",
                "https://www.sharjahcoop.ae/medias/1200Wx1200H-00000-6291100790760-001.jpg?context=bWFzdGVyfGltYWdlc3"
                "wxNTAyMTN8aW1hZ2UvanBlZ3xhRFUyTDJneVl5OHhNREExTURjNE1UUTBOakUzTkM4eE1qQXdWM2d4TWpBd1NGOHdNREF3TUY4Mk1q"
                "a3hNVEF3Tnprd056WXdYekF3TVM1cWNHY3w2ZDJiNTE0NzNjNTc5ZDdkMjhiZGY4MzBmMTdkYTRiMjVlZWNkMTI3OGRlMGU5NDk0"
                "NmEwNzFmMDRkOWI0MzVj",
                read("Shama", "بهارات الدجاج العربية"), (1200, 1200), t(2)),
        listing(s, 3, "Shama Arabic Biryani Masala 225g | Sharjah Co-operative Society",
                page.replace("chicken-masala-225g/p/6291100790760", "biryani-masala-225g/p/6291100790807")
                + "?srsltid=AU7gw4URnG2-IyKk-bcvFKzbViJL_4lTPm1SD0ODjGbDbSB9bEkRCSuv",
                "https://www.sharjahcoop.ae/medias/1200Wx1200H-00000-6291100790807-001.jpg",
                read("Shama", "Biryani Spice Mix", variant_match="no"), (1200, 1200), t(2)),
        listing(s, 4, "اشتر المهباج بهارات دجاج 225 جم | نينجا المملكة العربية السعودية",
                "https://ananinja.com/sa/ar/product/almehbaj-chiken-spices-225-gm-19292630"
                "?srsltid=AU7gw4VV5pBFwPVtL9AnbaOYXdk55qEQSVU3IADiF3axygqh0nxSZHhT",
                "https://img.ananinja.com/media/ninja-catalog-42/Banmaly/6285131012694_2.png",
                read("mehbaj", "Chicken Spices", brand_match="no", variant_match="no"), (1024, 1024), t(3)),
        listing(s, 7, "Arabic Masala 225g price in UAE | Noon UAE | kanbkam",
                "https://www.kanbkam.com/ae/en/arabic-masala-225-g-N12278264A",
                "https://z.nooncdn.com/products/tr:n-t_400/v1532408096/N12278264A_1.jpg", None, (400, 545), t(3)),
    ]


def test_row15_the_sharjah_coop_label_is_preselected_with_the_runs_tiers():
    rcs = row15(recorded_tiers=True)
    assert decisions(rcs) == [UNSURE, UNSURE, MISMATCH, MISMATCH, None]
    out = route(ROW15, rcs)
    assert out.decision == "REVIEW_PRESELECTED" and out.winner is rcs[0]
    assert "preselected:tier2_corroborated" in out.winner.reasons and warns(out.winner) == ["vlm_unsure"]


def test_row15_scored_again_the_sharjah_coop_listing_is_tier_1():
    rcs = row15()
    assert [rc.score.tier for rc in rcs[:2]] == [1, 1]
    out = route(ROW15, rcs)
    assert out.decision == "REVIEW_PRESELECTED" and out.winner is rcs[0]
    assert "preselected:tier1_unsure" in out.winner.reasons


def test_row15_the_same_listing_on_a_generic_coop_site_still_needs_corroboration():
    # emcoop.ae is not promoted: one generic domain alone is no corroboration (a size not legible on the front)
    rcs = row15(recorded_tiers=True, host="www.emcoop.ae")
    assert rcs[0].score.matched["source_class"] == "generic"
    out = route(ROW15, rcs)
    assert out.decision == "REVIEW_UNSELECTED" and out.winner is None


# ---------------------------------------------------------------------------
# Fix 2: a size within the tolerance read as 'no' (row 9)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", ["840ge", "840 g e", "840g℮", "840 g ℮", "840gm℮", "840 GRAMS", "Net Wt 840ge"])
def test_the_printed_size_with_the_estimated_sign_parses(text):
    assert [(s.dimension, s.base_value, s.pack_count) for s in parse_sizes(text, "vlm")] == [("mass", 840.0, None)]


ROW9 = spec_of("AL TAGHZIAH CHICKEN LUNCHEON MEAT 850G", "AL TAGHZIAH")
LULU_TAGHZIAH = "https://gcc.luluhypermarket.com/en-ae/al-taghziah-chicken-luncheon-meat-840-g/p/386766"
LULU_TAGHZIAH_TITLE = "Al Taghziah Chicken Luncheon Meat 840 g Online at Best Price | Lulu UAE"
SHARJAH_TAGHZIAH = "https://www.sharjahcoop.ae/en/al-taghziah-chicken-luncheon-meat-850g/p/5283002830089"
SHARJAH_TAGHZIAH_TITLE = "Al Taghziah Chicken luncheon meat 850g | Sharjah Co-operative Society"
TAGHZIAH_840 = read("Al Taghziah", "Chicken Luncheon Meat", "840ge", size_match="no")


def row9():
    s = ROW9
    return [
        listing(s, 1, LULU_TAGHZIAH_TITLE, LULU_TAGHZIAH,
                "https://bf1af2.akinoncloudcdn.com/products/2024/09/20/123254/57e3ef83-4238-41ba-b65d-18506328541f.jpg",
                TAGHZIAH_840, (1500, 1493)),
        listing(s, 2, LULU_TAGHZIAH_TITLE, LULU_TAGHZIAH,
                "https://bf1af2.akinoncloudcdn.com/products/2024/09/20/123254/485175db-cd06-4d58-a5ac-9eeb03793089"
                "_size1920x1920_cropCenter.jpg",
                read("Al Taghziah", "Chicken Luncheon Meat", view="other_side", size_match="no"), (1920, 1920)),
        listing(s, 3, "Al Taghziah Luncheon Chicken - 840 g: Buy Online at Best Price in UAE -  Amazon.ae",
                "https://www.amazon.ae/Al-Taghziah-Luncheon-Chicken-840/dp/B07PCLFY5T",
                "https://m.media-amazon.com/images/I/71I8b7eAD9L.jpg", TAGHZIAH_840, (2400, 2400)),
        listing(s, 4, "Buy Al Taghziah Chicken Luncheon Meat 840g Online | Carrefour Lebanon",
                "https://www.carrefourlebanon.com/maflbn/en/poultry-luncheon/al-taghziah-chicken-luncheon-840g/p/84262"
                "?srsltid=AU7gw4V46TNJ3KQltLwHkWfbOgPKqGK3jDlMYrBMZJAWq2EoJGnfCAEw",
                "https://cdn.mafrservices.com/sys-master-root/hc1/h3d/45783658725406/84262_main.jpg",
                read("Al Taghziah", "LUNCHEON MEAT CHICKEN", "840 GRAMS", size_match="no"), (1700, 1700)),
        listing(s, 5, SHARJAH_TAGHZIAH_TITLE,
                SHARJAH_TAGHZIAH + "?srsltid=AU7gw4UxmSZgf7_isSRan0-V85Gw-NpgsrpYBCiGdRVu4XLaHueYSX9G",
                "https://www.sharjahcoop.ae/medias/5283002830089-1200Wx1200H-001.jpg", TAGHZIAH_840, (1200, 1200)),
        listing(s, 6, SHARJAH_TAGHZIAH_TITLE,
                SHARJAH_TAGHZIAH + "?srsltid=AU7gw4X-y99X5ERxtxrgsXToP9-3q1oT4m6T2ynHaGMGplO8oZ2iB9Hq",
                "https://www.sharjahcoop.ae/medias/1200Wx1200H-00000-5283002830089-001.jpg", TAGHZIAH_840,
                (1200, 1200)),
        listing(s, 7, SHARJAH_TAGHZIAH_TITLE,
                SHARJAH_TAGHZIAH + "?srsltid=AU7gw4V1jPhetFvdiKQG6JTHq4D862UKvMCn2HKD6k1HkGgMAsbkTx1T",
                "https://www.sharjahcoop.ae/medias/300Wx300H-00000-5283002830089-002.jpg", TAGHZIAH_840, (300, 300)),
        listing(s, 8, "Magic Trading | Al taghziah turkey luncheon meat 850g",
                "https://magic-sl.com/al-taghziah-turkey-luncheon-meat-850g-",
                "https://magic-sl.com/cache/original/product/3394/gV40u2nv6ws7JgqimFUOZpABw4whK2qW55ga9Gmj.jpg",
                read("Al Taghziah", "Turkey Luncheon Meat", "340ge", variant_match="no", size_match="no"),
                (591, 633)),
    ]


def test_row9_the_840g_label_is_preselected_with_a_size_close_warning():
    rcs = row9()
    assert [rc.score.tier for rc in rcs[:4]] == [1, 1, 1, 2]
    assert decisions(rcs) == [UNSURE] * 7 + [MISMATCH]
    printed = size_close(ROW9, rcs[0].verdict)
    assert printed is not None and printed.base_value == 840.0
    out = route(ROW9, rcs)
    assert out.decision == "REVIEW_PRESELECTED" and out.winner is rcs[0]
    assert "preselected:tier1_unsure" in out.winner.reasons and "auto_blocked:not_vlm_match" in out.winner.reasons
    assert warns(out.winner) == ["vlm_unsure", "size_close:840g/850g"]
    # every alternative that shows the 840 g label tells the reviewer too
    assert "size_close:840g/850g" in decide.candidate_warnings(ROW9, rcs[2])
    assert "size_close:840g/850g" in decide.candidate_warnings(ROW9, rcs[3])          # '840 GRAMS'
    # the back of the can (nothing printed read) is a size 'no' the reading cannot support: UNSURE, no size_close
    assert overruled_flags(ROW9, rcs[1].verdict) == ("size",) and size_close(ROW9, rcs[1].verdict) is None
    assert rcs[7].status == "rejected"                                              # turkey, 340 g


def test_row9_a_reading_the_reader_left_unsure_by_itself_still_comes_first():
    # the tier-1 fallback keeps the pick of a row that had one: a reading whose 'no' was set aside comes after it
    rcs = row9()
    plain = listing(ROW9, 9, LULU_TAGHZIAH_TITLE, LULU_TAGHZIAH, "https://bf1af2.akinoncloudcdn.com/plain.jpg",
                    read("Al Taghziah", "Chicken Luncheon Meat"), (1500, 1500))
    out = route(ROW9, rcs + [plain])
    assert out.winner is plain and "size_close:840g/850g" not in warns(out.winner)


@pytest.mark.parametrize("size_text", ["800g", "800ge", "1kg", "340ge"])
def test_a_size_outside_the_tolerance_stays_mismatch(size_text):
    v = make_verdict(ROW9, 0, read("Al Taghziah", "Chicken Luncheon Meat", size_text, size_match="no"))
    assert v.decision == MISMATCH and size_close(ROW9, v) is None


@pytest.mark.parametrize("size_text", ["850g", "850ge", "0.85 kg"])
def test_an_exact_size_read_as_no_stays_mismatch(size_text):
    v = make_verdict(ROW9, 0, read("Al Taghziah", "Chicken Luncheon Meat", size_text, size_match="no"))
    assert v.decision == MISMATCH and size_close(ROW9, v) is None


def test_another_pack_of_the_exact_unit_size_stays_mismatch():
    # a single-unit SKU against a printed '6 x 185g': the reader's size 'no' is the pack
    deep = spec_of("DEEP BLUE SHREDDED TUNA IN SUNFLOWER OIL 185G", "DEEP BLUE")
    for size_text, pack in (("6 x 185g", 6), ("6 x 182g", 6), ("182g", 6)):
        v = make_verdict(deep, 0, read("DEEP blue", "Tuna SHREDDED SUNFLOWER OIL", size_text, size_match="no",
                                       pack=pack))
        assert v.decision == MISMATCH, size_text


def test_a_close_size_with_another_no_stays_mismatch():
    brand_no = make_verdict(ROW9, 0, read("Zwan", "Chicken Luncheon Meat", "840ge", brand_match="no",
                                          size_match="no"))
    variant_no = make_verdict(ROW9, 0, read("Al Taghziah", "Turkey Luncheon Meat", "840ge", variant_match="no",
                                            size_match="no"))
    assert brand_no.decision == MISMATCH and variant_no.decision == MISMATCH


def test_a_close_size_is_unsure_never_match():
    for flags in ({"size_match": "no"}, {"size_match": "no", "variant_match": "unsure"}):
        v = make_verdict(ROW9, 0, read("Al Taghziah", "Chicken Luncheon Meat", "840 g", **flags))
        assert v.decision == UNSURE


# ---------------------------------------------------------------------------
# Fix 3: a 'no' the reader's own verbatim reading contradicts (row 5)
# ---------------------------------------------------------------------------

ROW5 = spec_of("DEEP BLUE SHREDDED TUNA IN SUNFLOWER OIL 185G", "DEEP BLUE")
AMAZON_DEEP = "https://www.amazon.ae/Deep-Blue-Shredded-Tuna-Sunflower/dp/B0CTNL38Z8"
AMAZON_DEEP_TITLE = "Deep Blue Shredded Tuna in Sunflower Oil 185 g: Buy Online at Best Price in  UAE - Amazon.ae"
SIBLOU = read("Siblou", "Light Meat Tuna", view="multi_product", brand_match="no", variant_match="no",
              size_match="no", pack=3)
SHREDDED = read("DEEP blue", "Tuna SHREDDED SUNFLOWER OIL", variant_match="no", size_match="no")
FLAKES = read("DEEP blue", "Tuna FLAKES SUNFLOWER OIL", "185g", variant_match="no", size_match="yes")


def row5():
    s = ROW5
    return [
        listing(s, 1, AMAZON_DEEP_TITLE, AMAZON_DEEP, "https://images-eu.ssl-images-amazon.com/images/I/81oYC1sqVhL.jpg",
                SIBLOU, (1536, 2560)),
        listing(s, 2, AMAZON_DEEP_TITLE, AMAZON_DEEP, "https://m.media-amazon.com/images/I/61xMhNRR2DL.jpg",
                SHREDDED, (1266, 745)),
        listing(s, 3, AMAZON_DEEP_TITLE, AMAZON_DEEP, "https://images-eu.ssl-images-amazon.com/images/I/81ElJwUjF3L.jpg",
                FLAKES, (2560, 2193)),
        listing(s, 4, "Deep Blue Tuna Shredded In Sunflower Oil - NowNow - noon",
                "https://nownow.noon.com/uae-en/store/VVSPRMDOHA/product/P2944478830241768A/",
                "https://f.nooncdn.com/s/app/mp-nownow-web/images/NOWNOW_OG.jpg",
                read("", "", view="not_product", brand_match="no", variant_match="no", size_match="no", pack=None),
                (1024, 500)),
        listing(s, 5, "DEEP blue Canned Tuna UAE | 30-75% OFF | Dubai, Abu Dhabi",
                "https://www.noon.com/uae-en/grocery-store/canned-dry-and-packaged-foods/tuna/deep_blue/",
                "https://f.nooncdn.com/p/pzsku/Z910FE8E56234B85B3BDCZ/45/1754508629/1c9ec8d4-28fb-4fe9-b2ff-"
                "4fa0b58a62c1.jpg",
                read("", "Tuna FLAKES SUNFLOWER OIL", brand_match="unsure", variant_match="no", size_match="no"),
                (660, 900)),
        listing(s, 7, "Tuna Shredded In Sunflower Oil, Thailand",
                "https://www.noon.com/uae-en/tuna-shredded-in-sunflower-oil-thailand/ZC8060A3CBDB11F0E20B3Z/p/",
                "https://f.nooncdn.com/p/pzsku/ZC8060A3CBDB11F0E20B3Z/45/1746539982/18162d85-a028-4fae-8dc4-"
                "67b0fa25ccfd.jpg", None, (660, 900)),
    ]


def test_the_description_words_of_row5():
    assert description_words(ROW5) == ("shredded", "tuna", "sunflower", "oil")


def test_row5_the_shredded_label_is_preselected_and_the_overruled_flags_are_recorded():
    rcs = row5()
    assert [rc.score.tier for rc in rcs[:3]] == [1, 1, 1]
    assert decisions(rcs) == [MISMATCH, UNSURE, MISMATCH, MISMATCH, MISMATCH, None]
    assert overruled_flags(ROW5, rcs[1].verdict) == ("size", "variant")
    assert decide.brand_refuted(rcs), "the Siblou picture on the Amazon page refutes the listing text"
    out = route(ROW5, rcs)
    assert out.decision == "REVIEW_PRESELECTED" and out.winner is rcs[1]
    # tier 1 UNSURE whose own label reads DEEP blue: the refuted-brand exception applies
    assert not decide.refuted_for(ROW5, rcs, rcs[1])
    assert "preselected:tier1_unsure" in out.winner.reasons and "auto_blocked:not_vlm_match" in out.winner.reasons
    assert {"vlm:flag_overruled:size", "vlm:flag_overruled:variant"} <= set(out.winner.reasons)
    assert warns(out.winner) == ["vlm_unsure"]
    # (a) another product of the page, (c) FLAKES for SHREDDED: may be another product
    assert rcs[0].status == "rejected" and rcs[2].status == "rejected"
    assert not any(r.startswith("vlm:flag_overruled") for rc in (rcs[0], rcs[2]) for r in rc.reasons)
    # route() is idempotent: the reasons are recomputed, never doubled
    again = route(ROW5, rcs)
    assert again.winner is rcs[1] and out.winner.reasons.count("vlm:flag_overruled:size") == 1


def test_row5_the_flakes_label_is_never_the_pick():
    rcs = row5()
    del rcs[1]
    out = route(ROW5, rcs)
    assert out.decision == "REVIEW_UNSELECTED" and out.winner is None


@pytest.mark.parametrize("fields", [
    # a word of the description is missing from the printed variant
    dict(variant_text="Tuna FLAKES SUNFLOWER OIL"),
    dict(variant_text="Tuna SHREDDED OIL"),
    dict(variant_text="SHREDDED SUNFLOWER OIL"),
    # every word and another one: a marked variant the SKU does not state, one that conflicts with it, or any
    # other word (it may be what the 'no' is about)
    dict(variant_text="Tuna SHREDDED SUNFLOWER OIL WITH CHILLI"),
    dict(variant_text="Tuna SHREDDED SUNFLOWER OIL in brine"),
    dict(variant_text="Tuna SHREDDED SUNFLOWER OIL Solid"),
    dict(variant_text="Tuna SHREDDED SUNFLOWER OIL Premium"),
    # a size 'no' with a size printed, or with another pack counted
    dict(size_text="185g"),
    dict(size_text="160g"),
    dict(pack_count=3),
    # a brand 'no' is never overruled
    dict(brand_text="Siblou", brand_match="no"),
    dict(brand_text="DEEP blue", brand_match="no"),
])
def test_a_no_the_reading_supports_stays_mismatch(fields):
    reading = dict(SHREDDED)
    reading.update(fields)
    v = make_verdict(ROW5, 0, reading)
    assert v.decision == MISMATCH and overruled_flags(ROW5, v) == ()


@pytest.mark.parametrize("fields, flags", [
    (dict(variant_match="yes"), ("size",)),
    (dict(size_match="unsure"), ("variant",)),
    (dict(variant_text="Shredded Tuna in Sunflower Oils", size_match="unsure"), ("variant",)),   # a plain plural
    (dict(brand_match="unsure", brand_text=""), ("size", "variant")),
])
def test_an_overruled_reading_is_unsure_never_match(fields, flags):
    reading = dict(SHREDDED)
    reading.update(fields)
    v = make_verdict(ROW5, 0, reading)
    assert v.decision == UNSURE and overruled_flags(ROW5, v) == flags


@pytest.mark.parametrize("name, brand, reading", [
    # real readings of the 2026-10-04 runs whose label prints every sheet word and more: the 'no' stands
    ("ROBERT CHICKEN LUNCHEON MEAT 340G", "ROBERT",                                      # 19:33, row 11
     read("Robert", "CHICKEN LUNCHEON MEAT HOT SPICED", "340g", variant_match="no", size_match="yes")),
    ("SADIA CHICKEN NUGGETS 400G", "SADIA",                                             # 19:33, row 21
     read("Sadia", "MINI CHEF CHICKEN NUGGETS TRIANGLES", "400g", variant_match="no", size_match="yes")),
    ("ZWAN TURKEY LUNCHEON MEAT 200GM", "ZWAN",                                         # 16:19, row 75
     read("ZWAN", "TURKEY LUNCHEON MEAT WITH HERBS", variant_match="no")),
    ("ZWAN CHICKEN LUNCHEON MEAT H/S 200GM", "ZWAN",                                    # 16:19, row 66
     read("ZWAN", "CHICKEN LUNCHEON MEAT TANDOORI", variant_match="no")),
    ("MARA MIXED VEGETABLES 400GM", "MARA",                                             # 16:19, row 83
     read("MARA", "Mixed vegetables with tomato sauce", variant_match="no")),
])
def test_a_label_that_prints_more_than_the_sheet_keeps_its_variant_no(name, brand, reading):
    spec = spec_of(name, brand)
    v = make_verdict(spec, 0, reading)
    assert v.decision == MISMATCH and overruled_flags(spec, v) == ()


def test_an_overruled_variant_without_stated_variants_is_still_never_match():
    # the SKU states no variant: a variant 'no' overruled must not let the reading through as MATCH
    spec = spec_of("AL TAGHZIAH LUNCHEON MEAT 850G", "AL TAGHZIAH")
    v = make_verdict(spec, 0, read("Al Taghziah", "Luncheon Meat", "850g", variant_match="no", size_match="yes"))
    assert overruled_flags(spec, v) == ("variant",) and v.decision == UNSURE


# ---------------------------------------------------------------------------
# Never an auto-publish, never a paid look or seed
# ---------------------------------------------------------------------------

def _mapped(name, brand):
    key = brand.lower()
    return spec_of(name, brand, mappings={key: {"brand": brand.title(), "synonyms": [brand]}})


@pytest.mark.parametrize("row", ["row3", "row5", "row9", "row15"])
def test_none_of_the_new_paths_auto_publishes(row, monkeypatch):
    monkeypatch.setenv("AUTO_PUBLISH_ENABLED", "true")
    monkeypatch.setenv("AUTO_PUBLISH_BRANDS", "*")
    name, brand, build = {
        "row3": (ROW3.raw_name, "VIRGINIA", row3), "row5": (ROW5.raw_name, "DEEP BLUE", row5),
        "row9": (ROW9.raw_name, "AL TAGHZIAH", row9), "row15": (ROW15.raw_name, "SHAMA", row15)}[row]
    spec = _mapped(name, brand)
    assert spec.brand_conf == "mapped"
    rcs = [listing(spec, rc.candidate.rank, rc.candidate.title, rc.candidate.page_url, rc.candidate.image_url,
                   {"brand_text": rc.verdict.brand_text, "variant_text": rc.verdict.variant_text,
                    "size_text": rc.verdict.size_text, "pack_count": rc.verdict.pack_count, "view": rc.verdict.view,
                    "brand_match": rc.verdict.brand_match, "variant_match": rc.verdict.variant_match,
                    "size_match": rc.verdict.size_match} if rc.verdict is not None else None,
                   (rc.fetched.width, rc.fetched.height))
           for rc in build()]
    out = route(spec, rcs)
    assert out.decision == "REVIEW_PRESELECTED"
    assert out.winner.verdict.decision == UNSURE and "auto_blocked:not_vlm_match" in out.winner.reasons
    assert not any(rc.verdict is not None and rc.verdict.decision == MATCH for rc in rcs)


def test_a_set_aside_reading_gets_no_second_look_and_seeds_no_visual_search():
    lulu, back = row9()[:2]
    shredded = row5()[1]
    for spec, rc in ((ROW9, lulu), (ROW9, back), (ROW5, shredded)):
        assert rc.verdict.decision == UNSURE
        assert not needs_second_look(spec, rc.verdict)     # a second look must never turn it into a MATCH
        assert decide.no_set_aside(spec, rc)
    assert expand.near_matches(ROW9, [lulu, back]) == []
    assert expand.near_matches(ROW5, [shredded]) == []
    # a reading the reader left UNSURE by itself still gets both
    plain = row15()[0]
    assert needs_second_look(ROW15, plain.verdict) and not decide.no_set_aside(ROW15, plain)
    assert expand.near_matches(ROW15, [plain]) == [plain]
