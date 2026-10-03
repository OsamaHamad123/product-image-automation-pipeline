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
