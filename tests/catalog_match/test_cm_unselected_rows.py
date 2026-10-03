"""Live run of 2026-10-03 (runs/2026-10-03/smoke_6.json): the 27 rows left without a pick, defect by defect.

Every test is built from the real sheet row (and, where a listing matters, the real listing title and
page) it cites. The sheet's sku_key of every one of the 60 rows must stay exactly what it was: the
names are read the way the stores write them for parsing and queries only.
"""
import csv
import json
import socket
from pathlib import Path

import pytest

from catalog_match import settings, sheet_names
from catalog_match.identity import build_sku_spec
from catalog_match.models import Candidate, VlmImageVerdict
from catalog_match.query_plan import build_queries
from catalog_match.score import score_candidate
from catalog_match.variants import extract_variants
from catalog_match.verify import MATCH, MISMATCH, classify

REPO = Path(__file__).resolve().parents[2]
RUN = REPO / "runs" / "2026-10-03"


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError(f"network access attempted: {args!r}")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    monkeypatch.setattr(settings, "_config", None)
    monkeypatch.setenv("AUTO_PUBLISH_ENABLED", "false")
    monkeypatch.setenv("AUTO_PUBLISH_BRANDS", "")
    from catalog_match import brand_discovery

    brand_discovery.forget_all()          # the spellings sibling rows proved live per process: none leaks in or out
    yield
    brand_discovery.forget_all()


def spec_of(name, brand, mappings=None):
    return build_sku_spec({"name": name, "brand": brand}, mappings or {})


def listing(title, page_url, image_url="https://img.example-cdn.com/1.jpg", provider="serper"):
    return Candidate(image_url=image_url, page_url=page_url, title=title, page_title=title, provider=provider,
                     query_id="Q1", rank=1)


def suggested_mappings():
    import google_sheets

    with open(RUN / "brands_mapping_suggested.csv", encoding="utf-8-sig", newline="") as fh:
        return google_sheets.parse_brand_mapping_rows([r for r in csv.reader(fh)])


def live_rows():
    with open(RUN / "rows_2_61.csv", encoding="utf-8-sig", newline="") as fh:
        return {int(r["row"]): r for r in csv.DictReader(fh)}


# ---------------------------------------------------------------------------
# The sku_key of every live row never moves (it keys approvals, rejections and the queue)
# ---------------------------------------------------------------------------

# Computed on main at 51d6c59, before any change of this package.
LIVE_KEYS = {
    2: "7bc4dae17ed58c82", 3: "2ebb6a03780769b3", 4: "834b2a9ce73f921f", 5: "12a83cc20b9682bb",
    6: "00206ddb0e0ad08f", 7: "146f93363729ab1a", 8: "b29fcc37c34a3bc2", 9: "c18f7dcea1ad53b8",
    10: "83a280d3fa3df468", 11: "65f3b486429f0423", 12: "8a15e3997d23c3fe", 13: "188e6c84ed040201",
    14: "3667945bf0deb403", 15: "a66c3b5167bb6e5e", 16: "90437b20fba1cebd", 17: "af3f4c5604abafee",
    18: "959d361cd096423f", 19: "c23ef872761f5765", 20: "8827015bf07ff456", 21: "06206a3fa7b86a81",
    22: "c9a1307106c02ed2", 23: "b853b9354ca6c16e", 24: "ca43a81c9f9b6258", 25: "b644c66bc82adf73",
    26: "df00af707392acc5", 27: "fe0fbbf624dd8d1a", 28: "5eb2bb69d6b8563f", 29: "82cb805b0ecc9e5a",
    30: "992015184827178a", 31: "93848e754f2d4966", 32: "6e8482e5baa40f6c", 33: "194b0629c9ece644",
    34: "983109fc60b385bb", 35: "1aa9aa8fb6c5de4a", 36: "e93d8a6a2a3db9ac", 37: "1c74e287e58390b3",
    38: "e037c77395bb0062", 39: "35ef721ebe57724f", 40: "ee8fa1cee3168f68", 41: "311a3752a07c62bd",
    42: "a0aff764334c952c", 43: "669f98c343570fda", 44: "a158c6421caca234", 45: "39c78ae2298d9c22",
    46: "957612d33ceebba7", 47: "7baa3324431907c4", 48: "ea5465fa27c51b2c", 49: "2f05184b63702d59",
    50: "f0a1bbae3f58383a", 51: "cc7e4979b610ae65", 52: "c8da4a7a5be7788f", 53: "0c644d5dc536f19b",
    54: "07a22c2a8e99b95c", 55: "bbd106753ba02a47", 56: "b60aa74cde2c2d2f", 57: "c722db0d31ad7a15",
    58: "5285421cadb35ed5", 59: "506854206a385469", 60: "60355af9a37b9268", 61: "04b2fe81ce30e1cc",
}


def test_the_sku_key_of_every_live_row_is_unchanged():
    rows, mappings = live_rows(), suggested_mappings()
    assert sorted(rows) == sorted(LIVE_KEYS)
    for number, row in rows.items():
        for maps in ({}, mappings):
            assert spec_of(row["name"], row["brand"], maps).sku_key == LIVE_KEYS[number], (number, row["name"])


# ---------------------------------------------------------------------------
# 1. A size glued to the word before it ('MASALA160 GM', 'WATE3X185GM')
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("row, name, brand, size, pack", [
    (38, "KABANI MEAT MASALA160 GM", "KABANI", "160g", None),
    (19, "AL ALALI FANCY MEAT TUNA IN WATER170GM", "AL ALALI", "170g", None),
    (50, "SUPER T/MEAT SOLID TUNA SALT WATE3X185GM", "SUPER T/", "3x185g", 3),
    (51, "SUPER T/MEAT SOLID TUNA SUNFL OIL3X185GM", "SUPER T/", "3x185g", 3),
])
def test_a_size_glued_to_a_word_is_read(row, name, brand, size, pack):
    spec = spec_of(name, brand)
    assert spec.size is not None and spec.size.canonical() == size, row
    assert spec.pack_count == pack, row


def test_only_a_size_is_split_off():
    for text in ("FERRERO ROCHER T3 37.5G", "OMEGA3 500G", "7UP 330ML", "VITAMIN B12 100ML", "PEPSI 6X330ML",
                 "PEPSI 6x330ml", "RIO MARIE LIGHT MEAT TUNA IN SUN OIL 3X70GM"):
        assert sheet_names.split_glued_sizes(text) == text
    assert sheet_names.split_glued_sizes("SALT WATE3X185GM") == "SALT WATE 3X185GM"     # the 'X' stays the pack's
    assert sheet_names.split_glued_sizes("COKE ZERO1.5L") == "COKE ZERO 1.5L"


def test_rows_50_and_51_keep_their_pack_of_3():
    spec = spec_of("SUPER T/MEAT SOLID TUNA SUNFL OIL3X185GM", "SUPER T/")
    q1 = build_queries(spec)[0].text
    assert q1 == "SUPER T SOLID TUNA SUNFLOWER OIL 3x185g"            # was 'SUPER T SOLID TUNA SUNFLOWER OIL3X185GM'
    page = "https://www.tradeling.com/ae-en/product/super-tasty-tuna"
    three = score_candidate(spec, listing("Buy Super Tasty White Meat Solid Premium Tuna In Sunflower Oil 185g x 3 "
                                          "Pieces Online in UAE | Tradeling", page))
    assert three.size_status == "match" and three.matched["pack"] == "match"     # was size 'unknown'
    four = score_candidate(spec, listing("Super Tasty Solid Tuna In Sunflower Oil 4 x 185g | Tradeling", page + "-1"))
    assert "pack_conflict" in four.hard_reject               # another pack is now told apart


# ---------------------------------------------------------------------------
# 2. Sheet compounds and typos (data/sheet_spellings.json), never the key
# ---------------------------------------------------------------------------

def test_the_spellings_file_is_documented_row_by_row():
    raw = json.loads(sheet_names.SPELLINGS_PATH.read_text(encoding="utf-8"))
    assert raw["_doc"]
    for key, entry in raw["entries"].items():
        assert entry["reads"].strip() and "row" in entry["why"], key
    for rule in sheet_names.rules():
        # a fixed spelling is never a key itself (reading twice changes nothing)
        assert sheet_names.readable(rule.words, "tuna") == rule.words, rule


@pytest.mark.parametrize("row, name, brand, variants, class_tokens", [
    (32, "FAMILY LIGHTMEAT SKIPJACK CHUNKS TUNA 185GM", "FAMILY",
     {"tuna_meat": "light", "tuna_cut": "chunks"}, ("skipjack", "tuna")),
    (33, "FAMILY LIGHTMEAT TUNA SPRING WATER 185GM", "FAMILY", {"tuna_meat": "light", "medium": "water"}, ("tuna",)),
    # SOLIDTUNA hid the 'tuna' that makes WT/MEAT the white-meat grade and SALTWATER the brine
    (49, "SUP/T WT/MEAT SOLIDTUNA SALTWATER 185GM", "SUP/T",
     {"tuna_meat": "white", "tuna_cut": "solid", "medium": "brine"}, ("tuna",)),
    (50, "SUPER T/MEAT SOLID TUNA SALT WATE3X185GM", "SUPER T/", {"tuna_cut": "solid", "medium": "brine"},
     ("meat", "tuna")),
    (52, "SUPER/T LIGHT MEAT TUNA SOBEANOIL 185GM", "SUPER/T", {"tuna_meat": "light", "medium": "soybean_oil"},
     ("tuna",)),
    (35, "GOLDEN PRIZE L/ MEAT TUNA VEGE OIL 185GM", "GOLDEN PRIZE", {"tuna_meat": "light", "medium": "vegetable_oil"},
     ("tuna",)),
])
def test_sheet_compounds_and_typos_are_read_as_the_stores_write_them(row, name, brand, variants, class_tokens):
    spec = spec_of(name, brand)
    assert spec.variants == variants, row
    assert spec.class_tokens == class_tokens, row


def test_row53_luncheon_meat_is_found_by_its_words():
    spec = spec_of("TARGET CHICKN LUNCHENMEAT RECTANGL 340GM", "TARGET")
    assert {"luncheon", "meat"} <= set(spec.class_tokens) and "lunchenmeat" not in spec.class_tokens
    assert build_queries(spec)[0].text.startswith("TARGET CHICKEN LUNCHEON MEAT")
    s = score_candidate(spec, listing("Buy Target Chicken Luncheon Meat 340g Online | Carrefour UAE",
                                      "https://www.carrefouruae.com/mafuae/en/target-chicken-luncheon-meat/p/1"))
    assert s.matched["coverage"] >= 0.5                       # was 0.0: no listing writes 'chickn lunchenmeat'
    # 'CHICKN' read as chicken is the protein: the beef luncheon meat (read BEEF by the label reader) is rejected
    assert spec.variants == {"protein": "chicken"}
    beef = score_candidate(spec, listing("Target Beef Luncheon Meat 340 Gm: Buy Online at Best Price in UAE - Amazon.ae",
                                         "https://www.amazon.ae/Target-Beef-Luncheon-Meat-340/dp/B0"))
    assert "variant_conflict:protein" in beef.hard_reject


def test_row37_seasoning_typo():
    spec = spec_of("INA PARAMANS SEASOING MEAT SPICE 200ML", "INA PARAMANS")
    assert "seasoning" in spec.class_tokens and "seasoing" not in spec.class_tokens
    assert "SEASONING" in build_queries(spec)[0].text


def test_a_context_bound_spelling_needs_its_context():
    assert sheet_names.readable("XYZ SEA SALT WATE 500ML") == "XYZ SEA SALT WATE 500ML"
    assert sheet_names.readable("XYZ TUNA SALT WATE 185G") == "XYZ TUNA SALT WATER 185G"
    # an Arabic name is never rewritten
    assert sheet_names.readable("تونة بالماء والملح 185 جم") == "تونة بالماء والملح 185 جم"


# ---------------------------------------------------------------------------
# 5. What a luncheon meat or a masala is made of (the protein axis)
# ---------------------------------------------------------------------------

ZWAN_BEEF = ("ZWAN BEEF LUNCHEON MEAT 850GM", "ZWAN")                      # row 61
ALLDE_MEAT = ("ALLDE MEAT MASALA 160GM", "ALLDE")                           # row 25


def test_row61_the_chicken_luncheon_meat_is_another_product():
    spec = spec_of(*ZWAN_BEEF)
    assert spec.variants == {"protein": "beef"}
    chicken = score_candidate(spec, listing("Zwan Chicken Luncheon Meat 850 g Online at Best Price | Lulu UAE",
                                            "https://gcc.luluhypermarket.com/en-ae/zwan-chicken-luncheon-meat-850-g/p/1"))
    assert chicken.tier != 1 and "variant_conflict:protein" in chicken.hard_reject      # was tier 1
    beef = score_candidate(spec, listing("Zwan Zwan Beef Luncheon Meat Can - 850gms | Best Price UAE",
                                         "https://www.noon.com/uae-en/zwan-beef-luncheon-meat-can-850gms/p/2"))
    assert beef.tier == 1
    unsaid = score_candidate(spec, listing("Zwan Luncheon Meat, 850g",
                                           "https://www.carrefouruae.com/mafuae/en/zwan-luncheon-meat-850g/p/3"))
    assert unsaid.tier == 2 and not unsaid.hard_reject                  # the listing does not say beef


def test_row25_chicken_masala_is_not_meat_masala():
    spec = spec_of(*ALLDE_MEAT)
    assert spec.variants == {"protein": "meat"} and spec.class_tokens == ("masala",)
    page = "https://www.carrefouruae.com/mafuae/en/allde-{}-masala-160g/p/1"
    chicken = score_candidate(spec, listing("Allde Chicken Masala 160g | Carrefour UAE", page.format("chicken")))
    assert chicken.tier != 1 and "variant_conflict:protein" in chicken.hard_reject      # was tier 1
    assert score_candidate(spec, listing("Allde Meat Masala 160g | Carrefour UAE", page.format("meat"))).tier == 1
    # mutton masala is a close line (soft): never tier 1, never a hard reject
    mutton = score_candidate(spec, listing("Allde Mutton Masala 160g | Carrefour UAE", page.format("mutton")))
    assert mutton.tier == 2 and any(c.startswith("soft_variant_conflict:protein") for c in mutton.conflicts)


def test_the_label_reader_is_held_to_the_protein_too():
    spec = spec_of(*ZWAN_BEEF)
    read = VlmImageVerdict(index=0, brand_text="ZWAN", variant_text="CHICKEN Luncheon Meat", size_text="850 g",
                           view="front_packshot", brand_match="yes", variant_match="yes", size_match="yes")
    assert classify(spec, read) == MISMATCH
    read.variant_text = "BEEF Luncheon Meat"
    assert classify(spec, read) == MATCH


def test_meat_in_luncheon_meat_is_the_product_not_the_protein():
    spec = spec_of("TARGET LUNCHEON MEAT 340GM", "TARGET")
    assert "protein" not in spec.variants and "meat" in spec.class_tokens
    # a listing that names the animal the sheet leaves out is held for review, never rejected
    s = score_candidate(spec, listing("Target Beef Luncheon Meat 340 Gm: Buy Online at Best Price in UAE - Amazon.ae",
                                      "https://www.amazon.ae/Target-Beef-Luncheon-Meat-340/dp/B0"))
    assert s.tier == 2 and "unstated_variant:protein" in s.conflicts


@pytest.mark.parametrize("text, context, protein", [
    ("Mutton Meat Masala", None, "mutton"),             # 'meat' is generic: the animal wins
    ("Beef Luncheon Meat", None, "beef"),
    ("مرتديلا لحم بقري 340 جم", None, "beef"),
    ("ماسالا الدجاج", None, "chicken"),
    ("Meat Spice 200ml", None, "meat"),
    ("Chicken Flavour Instant Noodles 75g", None, None),  # not a meat product: no protein axis
    ("Light Meat Tuna In Water 170g", "tuna", None),
    ("Breaded Chicken Nuggets 400g", None, None),
])
def test_protein_is_read_only_where_it_names_the_product(text, context, protein):
    assert extract_variants(text, context).get("protein") == protein


def test_the_suggested_mapping_keeps_zwan_beef_and_chicken_apart():
    spec = spec_of(*ZWAN_BEEF, mappings=suggested_mappings())
    assert spec.brand_conf == "mapped" and spec.variants == {"protein": "beef"}


# ---------------------------------------------------------------------------
# 12. 'mm' is a unit, never a product word ('9MM' fries are a cut)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("row, name, brand", [
    (4, "BATO FRENCH FRIES 900 MM", "BATO"),
    (5, "FARMILA FRENCH FRIES 9MM 1KG", "FARMILA"),
    (12, "TOMEX FRENCH FRIES 9MM 1 KG", "TOMEX"),
])
def test_mm_is_not_a_product_word(row, name, brand):
    spec = spec_of(name, brand)
    assert spec.class_tokens == ("french", "fries"), row
    assert "9MM" in build_queries(spec)[0].text or row == 4        # the cut stays in the query


def test_row12_a_listing_without_mm_covers_every_product_word():
    spec = spec_of("TOMEX FRENCH FRIES 9MM 1 KG", "TOMEX")
    s = score_candidate(spec, listing("Tomex French Fries 1kg | Example Mart", "https://www.example-mart.ae/tomex-fries"))
    assert s.matched["coverage"] == 1.0                                # was 0.67: 'mm' counted as a product word


# ---------------------------------------------------------------------------
# 4. A listing with a soft doubt ranks below the plain listing it ties with
# ---------------------------------------------------------------------------

SUNBULAH = ("SUNBULAH FRENCH FRIES 1KG", "SUNBULAH")                         # row 11
LULU_KSA_THIN = listing("Sunbulah Thin French Fries 1 kg Online at Best Price | Lulu KSA",
                        "https://gcc.luluhypermarket.com/en-sa/sunbulah-thin-french-fries-1-kg/p/136497",
                        "https://bf1af2.akinoncloudcdn.com/products/2024/09/20/121771/491e497e.jpg")
SPINNEYS = listing("Sunbulah Frozen French Fries 1kg",
                   "https://www.spinneys.com/en-sa/catalogue/sunbulah-frozen-french-fries-1kg_89748/",
                   "https://prod-spinneys-cdn-new.azureedge.net/media/images/products/2026/07/6281073151026.jpg")
SHARJAH = listing("Sunbulah Sunbullah French Fries Potato 1Kg | Sharjah Co-operative Society",
                  "https://www.sharjahcoop.ae/en/sunbulah-sunbullah-french-fries-potato-1kg/p/6281073151026",
                  "https://www.sharjahcoop.ae/medias/6281073151026-1200Wx1200H-001.jpg")


def _ranked_row11():
    from catalog_match.score import rank

    spec = spec_of(*SUNBULAH)
    # the provider order of the live run: the thin listing came first
    cands = [LULU_KSA_THIN, SPINNEYS, SHARJAH]
    return spec, rank([(c, score_candidate(spec, c)) for c in cands])


def test_row11_the_thin_fries_listing_ranks_below_the_plain_ones():
    spec, ranked = _ranked_row11()
    scores = {c.image_url: s for c, s in ranked}
    assert all(s.tier == 2 for s in scores.values())
    assert "unstated_variant:fries_cut" in scores[LULU_KSA_THIN.image_url].conflicts
    assert [c.image_url for c, _ in ranked][-1] == LULU_KSA_THIN.image_url     # was first, on provider order


def test_row11_the_plain_listing_is_preselected_when_every_one_reads_as_a_match():
    from catalog_match import decide
    from catalog_match.models import FetchedImage, ProviderHealth, QualityReport, RankedCandidate, VerificationResult

    spec, ranked = _ranked_row11()
    rcs = []
    for c, s in ranked:
        read = VlmImageVerdict(index=0, brand_text="Sunbulah", variant_text="French Fries", size_text="1 kg",
                               view="front_packshot", brand_match="yes", variant_match="yes", size_match="yes",
                               decision="MATCH")
        rcs.append(RankedCandidate(candidate=c, score=s, verdict=read,
                                   fetched=FetchedImage(candidate=c, ok=True, content_sha256=c.image_url[-8:] * 8,
                                                        width=900, height=900, path_or_bytes=b"x"),
                                   quality=QualityReport(hard_ok=True, quality_score=0.8)))
    out = decide.route(spec, rcs, VerificationResult(status="ok", calls=1),
                       [ProviderHealth(provider="serper", status="ok", query_id="Q1")])
    assert out.decision == "REVIEW_PRESELECTED" and out.failure_code is None
    assert out.winner is not None and out.winner.candidate is not LULU_KSA_THIN
    assert not any(w.startswith("warn:sheet_silent") for w in out.winner.reasons)


# ---------------------------------------------------------------------------
# 3. An official site's own name is not brand evidence
# ---------------------------------------------------------------------------

MEHRAN_OFFICIAL = {"mehran": {"brand": "Mehran", "synonyms": ["MEHRAN"], "official_domains": ["mehranfoods.com"]}}
MEHRAN_PAGE = ("https://mehranfoods.com/ar/products/dawn-bread-plain-frozen-paratha-bread-5pcs-400g-"
               "%ED%94%8C%EB%9E%98%EC%9D%B8-%EB%83%89%EB%8F%99-%ED%8C%8C%EB%9D%BC%ED%83%80")


def test_row16_the_site_name_of_an_official_domain_is_not_the_brand():
    spec = spec_of("MEHRAN PLAIN PARATHA 400GM 5S", "MEHRAN", MEHRAN_OFFICIAL)
    dawn = score_candidate(spec, listing("Buy DAWN BREAD Plain Frozen Paratha Online | Mehran Foods Korea", MEHRAN_PAGE,
                                         "https://cdn.shopify.com/s/files/1/0848/9110/7613/files/front-163.jpg"))
    assert dawn.matched["source_class"] == "official"
    assert dawn.tier == 3 and not dawn.matched["brand"]                 # was tier 1 on the site's name alone
    # a page of that site that names the brand in the product part keeps it
    own = score_candidate(spec, listing("Mehran Plain Paratha (5 pieces) 400 g | Mehran Foods",
                                        "https://mehranfoods.com/products/mehran-plain-paratha-400g"))
    assert own.tier == 1 and own.matched["brand_fields"]["title"] == "mehran"
    # the same title on a site that is not the brand's is unaffected (store-name rules only there)
    other = score_candidate(spec, listing("Buy DAWN BREAD Plain Frozen Paratha Online | Mehran Foods Korea",
                                          "https://www.example-shop.com/dawn-bread-paratha"))
    assert other.matched["brand"]


def _paratha_only(text):
    return "paratha" in text.lower()


def test_a_site_name_segment_that_names_the_product_is_kept():
    from catalog_match.score import strip_site_suffix

    assert strip_site_suffix("Buy DAWN BREAD Plain Paratha | Mehran Foods Korea", ("mehranfoods",),
                             _paratha_only) == "Buy DAWN BREAD Plain Paratha"
    assert strip_site_suffix("Plain Paratha | Mehran Plain Paratha", ("mehranfoods",),
                             _paratha_only) == "Plain Paratha - Mehran Plain Paratha"
    assert strip_site_suffix("YUMWAY | Premium Frozen French Fries", ("yumwayfood",)) == "Premium Frozen French Fries"
    assert strip_site_suffix("Fries 1kg - Shop on Carrefour UAE") == "Fries 1kg"


def test_the_suggested_mapping_no_longer_lists_mehranfoods_as_official():
    mehran = [v for v in suggested_mappings().values() if v.get("brand") == "Mehran"]
    assert len(mehran) == 1 and "mehranfoods.com" not in (mehran[0].get("official_domains") or [])


# ---------------------------------------------------------------------------
# 7. A mapped misspelling is never sent again next to the right name
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("row, name, brand, q1", [
    (45, "RIO MARIE LIGHT MEAT TUNA IN SUN OIL 3X70GM", "RIO MARIE",
     "Rio Mare LIGHT MEAT TUNA IN SUNFLOWER OIL 3x70g"),                 # was 'Rio Mare RIO MARIE LIGHT ...'
    (49, "SUP/T WT/MEAT SOLIDTUNA SALTWATER 185GM", "SUP/T",
     "Super Tasty WHITE MEAT SOLID TUNA SALT WATER 185g"),                # was 'Super Tasty SUP/T WT/MEAT ...'
    (52, "SUPER/T LIGHT MEAT TUNA SOBEANOIL 185GM", "SUPER/T", "Super Tasty LIGHT MEAT TUNA SOYBEAN OIL 185g"),
    (37, "INA PARAMANS SEASOING MEAT SPICE 200ML", "INA PARAMANS", "Ina Paarman's SEASONING MEAT SPICE 200ml"),
    (39, "KITCHEN TREASURE MEAT MASALA 160GM", "KITCHEN TREASURE", "Kitchen Treasures MEAT MASALA 160g"),
    # a synonym that only adds the product's words to the brand keeps them: 'LIGHT' is the meat grade
    (28, "AMERICAN LIGHT MEAT TUNA SOLID 185GM", "AMERICAN LIGHT", "American LIGHT MEAT TUNA SOLID 185g"),
    (3, "BARTS TRADITON FRENCH FRIES STRAIGHT CUT 1KG", "BARTS TRADITON",
     "Bart's TRADITON FRENCH FRIES STRAIGHT CUT 1kg"),
])
def test_q1_writes_the_mapped_brand_once(row, name, brand, q1):
    spec = spec_of(name, brand, suggested_mappings())
    assert spec.brand_conf == "mapped"
    assert build_queries(spec)[0].text == q1, row


def test_a_sub_brand_stays_in_the_query():
    maps = {"nestle": {"brand": "Nestle", "synonyms": ["NESTLE"], "sub_brands": ["Nido", "Nesquik"]}}
    spec = spec_of("NIDO FORTIFIED MILK POWDER 2.25KG", "NESTLE", maps)
    assert build_queries(spec)[0].text == "Nestle NIDO FORTIFIED MILK POWDER 2.25kg"
    spec = spec_of("NIDO FORTIFIED MILK POWDER 2.25KG", "NIDO", maps)          # the brand cell is the sub-brand
    assert "NIDO" in build_queries(spec)[0].text


# ---------------------------------------------------------------------------
# Pipeline stage doubles (sockets stay blocked: every stage is a double)
# ---------------------------------------------------------------------------

def _png(seed=2):
    import io

    from PIL import Image, ImageDraw

    img = Image.new("RGB", (800, 800), (255, 255, 255))
    ImageDraw.Draw(img).rectangle([220, 120, 580, 680], fill=(30 + seed * 30, 80, 150))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


class ByQuery:
    """A web search double: the candidates of the first rule whose words the query holds."""
    kind, fallback, sanctioned, name = "search", False, True, "serper"

    def __init__(self, rules):
        self.rules, self.queries = list(rules), []

    def search(self, query, hl, spec_):
        from catalog_match.models import ProviderResult

        self.queries.append(query)
        cands = next((c for words, c in self.rules if words.lower() in query.lower()), [])
        return ProviderResult(provider="serper", status="ok" if cands else "empty", candidates=list(cands))


class IndexLookup:
    """A local catalog index double: rows only for a brand phrase the spec accepts."""
    kind, needs_gtin, sanctioned, name, lookup_query_id = "lookup", False, False, "local_index", "IDX"

    def __init__(self, phrase, cands):
        self.phrase, self.cands, self.asked = phrase, list(cands), []

    def lookup(self, spec_):
        from catalog_match.models import ProviderResult

        self.asked.append(tuple(spec_.match_brands))
        found = self.phrase in spec_.match_brands
        return ProviderResult(provider="local_index", status="ok" if found else "empty",
                              candidates=list(self.cands) if found else [])


class Images:
    def __init__(self, bodies):
        self.bodies = bodies

    def fetch(self, cands, spec_):
        import io

        from PIL import Image

        from catalog_match.fetch import phash_hex
        from catalog_match.models import FetchedImage

        out = []
        for c in cands:
            body = self.bodies.get(c.image_url)
            if body is None:
                out.append(FetchedImage(candidate=c, ok=False, error="http_404"))
                continue
            with Image.open(io.BytesIO(body)) as im:
                out.append(FetchedImage(candidate=c, ok=True, content_sha256=f"{abs(hash(body)):064x}"[:64],
                                        width=im.width, height=im.height, path_or_bytes=body, phash=phash_hex(im)))
        return out


class Reads:
    """A label reader double: what a model would read on each image (by URL); verify.make_verdict decides."""

    def __init__(self, readings):
        self.readings, self.calls = dict(readings), []

    def verify(self, spec_, images):
        from catalog_match.models import VerificationResult
        from catalog_match.verify import make_verdict

        self.calls.append([f.candidate.image_url for f in images])
        return VerificationResult(status="ok", calls=1, verdicts=[
            make_verdict(spec_, i, self.readings.get(f.candidate.image_url, {})) for i, f in enumerate(images)])


def _reading(brand, variant, size, pack=1):
    return {"brand_text": brand, "variant_text": variant, "size_text": size, "pack_count": pack,
            "view": "front_packshot", "brand_match": "yes", "variant_match": "yes", "size_match": "yes"}


# ---------------------------------------------------------------------------
# 6. The local index is asked again with a discovered spelling
# ---------------------------------------------------------------------------

RIO_ROW = ("RIO MARIE LIGHT MEAT TUNA IN SUN OIL 3X70GM", "RIO MARIE")                  # row 45


def test_row45_the_local_index_is_asked_again_in_the_store_spelling(monkeypatch):
    from catalog_match import pipeline

    monkeypatch.setenv("AUTO_PUBLISH_ENABLED", "true")
    monkeypatch.setenv("AUTO_PUBLISH_BRANDS", "*")
    # the web search finds the brand only in a listing without the size (tier 2), written the stores' way
    weak = listing("Rio Mare Light Meat Tuna in Sunflower Oil | Carrefour UAE",
                   "https://www.carrefouruae.com/mafuae/en/tuna/rio-mare-light-meat-tuna/p/1",
                   "https://img.example-cdn.com/rio-mare-tuna.jpg")
    indexed = Candidate(image_url="https://img.example-cdn.com/rio-mare-3x70-lulu.jpg",
                        page_url="https://gcc.luluhypermarket.com/en-ae/rio-mare-light-meat-tuna-sunflower-oil-3x70g/p/7",
                        title="Rio Mare Light Meat Tuna In Sunflower Oil 3 x 70 g",
                        page_title="Rio Mare Light Meat Tuna In Sunflower Oil 3 x 70 g", provider="local_index",
                        query_id="IDX", sanctioned=False)
    index = IndexLookup("rio mare", [indexed])
    search = ByQuery([("", [weak])])
    reader = Reads({indexed.image_url: _reading("Rio mare", "Light Meat Tuna in Sunflower Oil", "3 x 70 g", 3)})
    outcome = pipeline.find_product_image(spec_of(*RIO_ROW), providers=[search, index],
                                          fetcher=Images({weak.image_url: _png(1), indexed.image_url: _png(2)}),
                                          verifier=reader, expansion=False)
    assert outcome.discovered_brands == ["Rio Mare"]
    assert len(index.asked) == 2 and "rio mare" in index.asked[1]          # was asked once, in the sheet's spelling
    assert outcome.decision == "REVIEW_PRESELECTED"                       # an index page never auto-publishes
    assert outcome.winner.candidate.image_url == indexed.image_url
    assert "warn:brand_spelling:Rio Mare" in outcome.winner.reasons
    # the corrected query went out alongside the second lookup
    assert any(q.startswith("Rio Mare ") for q in search.queries)


# ---------------------------------------------------------------------------
# 9. One proven spelling per process, shared by the sheet's sibling spellings
# ---------------------------------------------------------------------------

ROW49 = ("SUP/T WT/MEAT SOLIDTUNA SALTWATER 185GM", "SUP/T")
ROW52 = ("SUPER/T LIGHT MEAT TUNA SOBEANOIL 185GM", "SUPER/T")
SHARJAH_ST = Candidate(image_url="https://www.sharjahcoop.ae/medias/1200Wx1200H-00000-6290360090887-001.jpg",
                       page_url="https://www.sharjahcoop.ae/en/super-tasty-l-meat-tuna-in-soya-oil-185g/p/6290360090887",
                       title="Super Tasty L.Meat Tuna In Soya Oil 185g | Sharjah Co-operative Society",
                       page_title="Super Tasty L.Meat Tuna In Soya Oil 185g | Sharjah Co-operative Society",
                       provider="serper", rank=5)
# row 52's own results (live run): other brands' light meat tuna, no Super Tasty listing
ROW52_OWN = [listing("Aloha Light Meat Tuna in Vegetable Oil 185 g Online at Best Price | Lulu UAE",
                     "https://gcc.luluhypermarket.com/en-ae/aloha-light-meat-tuna/p/1", "https://img.example-cdn.com/a.jpg"),
             listing("Le Supreme Light Meat Tuna in Sunflower Oil 185 g Can, Premium Quality: Buy Online",
                     "https://www.amazon.ae/Le-Supreme-Light-Meat-Tuna/dp/B0", "https://img.example-cdn.com/l.jpg")]


def _row(name_brand, search, reader, bodies):
    from catalog_match import pipeline

    return pipeline.find_product_image(spec_of(*name_brand), providers=[search], fetcher=Images(bodies),
                                       verifier=reader, expansion=False)


def test_rows_49_and_52_one_spelling_proved_by_a_sibling_row(monkeypatch):
    monkeypatch.setenv("AUTO_PUBLISH_ENABLED", "true")
    monkeypatch.setenv("AUTO_PUBLISH_BRANDS", "*")
    st_reading = _reading("SUPER TASTY", "Light Meat Tuna in Soya Oil", "185 g")
    bodies = {SHARJAH_ST.image_url: _png(3), ROW52_OWN[0].image_url: _png(4), ROW52_OWN[1].image_url: _png(5)}
    # row 49: its own results hold the UAE store's Super Tasty listing: discovered there
    first = _row(ROW49, ByQuery([("", [SHARJAH_ST])]), Reads({}), bodies)
    assert first.discovered_brands == ["Super Tasty"]
    # row 52: no Super Tasty listing of its own; the spelling row 49 proved sends B1 written with it
    search = ByQuery([("super tasty", [SHARJAH_ST]), ("", ROW52_OWN)])
    out = _row(ROW52, search, Reads({SHARJAH_ST.image_url: st_reading}), bodies)
    assert out.discovered_brands == ["Super Tasty"]                       # was [] (no pick, row left unselected)
    assert search.queries[-1] == "Super Tasty LIGHT MEAT TUNA SOYBEAN OIL 185g"
    assert out.decision == "REVIEW_PRESELECTED" and out.winner.candidate.image_url == SHARJAH_ST.image_url
    assert "warn:brand_spelling:Super Tasty" in out.winner.reasons
    assert "auto_blocked:brand_conf_sheet_raw" in out.winner.reasons      # never an auto-publish


def test_the_memory_is_keyed_by_the_sheet_brand_letters_and_never_guesses():
    from catalog_match import brand_discovery as bd

    assert bd.memory_key("SUPER T/") == bd.memory_key("SUPER/T") == "supert" and bd.memory_key("SUP/T") == "supt"
    proved = bd.discover(spec_of(*ROW49), [SHARJAH_ST])
    assert proved is not None and proved.display == "Super Tasty"
    bd.remember(spec_of(*ROW49), proved)
    # a sibling spelling recalls it; an unrelated or a mapped brand never does
    assert bd.find(spec_of(*ROW52), []) == proved
    assert bd.find(spec_of("SUPER T/MEAT SOLID TUNA SALT WATE3X185GM", "SUPER T/"), ROW52_OWN) == proved
    assert bd.find(spec_of("AMERICAN G/ LIGHT MEAT TUNA 185GM", "AMERICAN G/"), ROW52_OWN) is None
    mapped = spec_of(*ROW52, mappings={"super t": {"brand": "Super T", "synonyms": ["SUPER/T"]}})
    assert bd.find(mapped, []) is None
    # a row whose own listings prove another spelling gets neither: the owner's call
    other = listing("Super Taste Light Meat Tuna 185g | Union Coop", "https://www.unioncoop.ae/super-taste-tuna/p/9")
    assert bd.discover(spec_of(*ROW52), [other]) is not None
    assert bd.find(spec_of(*ROW52), [other]) is None
    # a listing that writes the sheet's own spelling needs no store spelling at all
    sheet_way = listing("Super T Light Meat Tuna 185g | Union Coop", "https://www.unioncoop.ae/super-t-tuna/p/8")
    assert bd.find(spec_of(*ROW52), [sheet_way]) is None


# ---------------------------------------------------------------------------
# 11. Only social-network posts show the product: a failure code of its own, with the links
# ---------------------------------------------------------------------------

def smoke6_row(number):
    with open(RUN / "smoke_6.json", encoding="utf-8") as fh:
        return next(r for r in json.load(fh) if r["row"] == number)


def replay_route(number):
    """decide.route over the live run's own top listings of one row, as they were downloaded and read."""
    from catalog_match import decide
    from catalog_match.models import (
        FetchedImage, ProviderHealth, QualityReport, RankedCandidate, VerificationResult,
    )

    row = smoke6_row(number)
    spec = spec_of(row["name"], row["brand"])
    ranked = []
    for t in row["top"]:
        c = Candidate(image_url=t["image_url"], page_url=t["page_url"] or "", title=t["title"] or "",
                      page_title=t["title"] or "", domain=t["domain"] or "", provider=t["provider"], rank=t["rank"])
        failed = next((r.split(":", 1)[1] for r in t["reasons"] if r.startswith("download:")), None)
        fetched = FetchedImage(candidate=c, ok=failed is None, error=failed, width=800, height=800,
                               content_sha256=None if failed else f"{t['rank']:064d}", path_or_bytes=b"x")
        bad_quality = [r.split(":", 1)[1] for r in t["reasons"] if r.startswith("quality:")]
        vlm = (t.get("vlm") or {}).get("decision")
        ranked.append(RankedCandidate(
            candidate=c, score=score_candidate(spec, c), fetched=fetched,
            quality=None if failed else QualityReport(hard_ok=not bad_quality, hard_reasons=bad_quality),
            verdict=VlmImageVerdict(index=0, decision=vlm) if vlm else None))
    return decide.route(spec, ranked, VerificationResult(status="ok", calls=1),
                        [ProviderHealth(provider="serper", status="ok", query_id="Q1")])


@pytest.mark.parametrize("number, before", [(29, "DOWNLOAD_FAILED"), (38, None), (41, None)])
def test_rows_29_38_41_only_social_posts_show_the_product(number, before):
    out = replay_route(number)
    assert out.decision == "REVIEW_UNSELECTED"
    assert out.failure_code == "SOCIAL_ONLY", (number, before)            # was `before`
    assert out.social_links and all(("instagram.com" in u or "facebook.com" in u) for u in out.social_links)


def test_a_downloadable_brand_listing_is_not_social_only():
    out = replay_route(6)            # HUP HUP: farzana.ae's listing downloaded (read as not the product)
    assert out.failure_code != "SOCIAL_ONLY" and out.social_links == []
