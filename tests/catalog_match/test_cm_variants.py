"""catalog_match.variants: exclusive variant groups in English and Arabic (RANK-5, MISS-3)."""

import socket

import pytest

from catalog_match.variants import (
    conflicts,
    extract_variants,
    matched_axes,
    merge,
    unstated_marked,
)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def _blocked(*args, **kwargs):
        raise RuntimeError("network access is blocked in catalog_match unit tests")

    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)
    monkeypatch.setattr(socket, "getaddrinfo", _blocked)


def test_cm_variants():
    full_cream = extract_variants("Almarai Full Cream Milk 1L")
    low_fat = extract_variants("Almarai Low Fat Milk 1L")
    assert full_cream == {"fat": "full"}
    assert low_fat == {"fat": "low"}
    assert conflicts(full_cream, low_fat) == ["fat"]

    assert conflicts(extract_variants("حليب كامل الدسم"), extract_variants("Milk Full Fat")) == []
    assert extract_variants("حليب كامل الدسم") == {"fat": "full"}

    assert conflicts(extract_variants("Laban strawberry"), extract_variants("Laban mango")) == ["flavour"]

    fresh = extract_variants("Almarai Fresh Milk")
    assert "fat" not in fresh
    assert conflicts(fresh, extract_variants("Almarai Fresh Milk Full Fat 1L")) == []
    assert conflicts(fresh, extract_variants("Almarai Low Fat Fresh Milk 1L")) == []


def test_longest_phrase_wins():
    # 'semi skimmed' is low fat, not skimmed; 'fat free' is skimmed, not a 'fat' mention
    assert extract_variants("Semi-Skimmed Milk") == {"fat": "low"}
    assert extract_variants("Fat Free Yoghurt") == {"fat": "skimmed"}
    assert extract_variants("Coca Cola Zero Sugar") == {"sugar": "zero"}
    assert extract_variants("Yoghurt Zero Fat") == {"fat": "skimmed"}


def test_arabic_variants_with_clitics():
    assert extract_variants("لبن بالفراولة") == {"flavour": "strawberry"}
    assert extract_variants("عصير المانجو") == {"flavour": "mango"}
    assert extract_variants("حليب قليل الدسم") == {"fat": "low"}
    assert extract_variants("قهوة منزوعة الكافيين") == {"caffeine": "decaf"}   # feminine agreement
    assert extract_variants("قهوة منزوع الكافيين") == {"caffeine": "decaf"}
    assert extract_variants("حليب خالي الدسم") == {"fat": "skimmed"}
    assert conflicts(extract_variants("لبن بنكهة الفراولة"), extract_variants("Strawberry Laban")) == []
    assert conflicts(extract_variants("لبن بنكهة الفراولة"), extract_variants("Mango Laban")) == ["flavour"]


def test_plain_is_never_inferred():
    plain = extract_variants("Almarai Laban Plain 180ml")
    strawberry = extract_variants("Almarai Laban Strawberry 180ml")
    unstated = extract_variants("Almarai Laban 180ml")
    assert unstated == {}
    # target says plain -> a flavour conflicts; target silent -> no conflict
    assert conflicts(plain, strawberry) == ["flavour"]
    assert conflicts(unstated, strawberry) == []
    # dossier s4: target strawberry vs the sharper 'Plain' sibling conflicts
    assert conflicts(strawberry, plain) == ["flavour"]


def test_multi_value_listing_is_not_a_conflict_nor_a_match():
    listing = extract_variants("Milk - Full Fat / Low Fat / Skimmed")
    assert listing == {"fat": "full+low+skimmed"}
    assert conflicts({"fat": "full"}, listing) == []
    assert matched_axes({"fat": "full"}, listing) == []
    assert matched_axes({"fat": "full"}, extract_variants("Full Fat Milk")) == ["fat"]
    combo = extract_variants("Strawberry & Banana Smoothie")
    assert combo == {"flavour": "banana+strawberry"}
    assert matched_axes(combo, extract_variants("Banana Strawberry smoothie")) == ["flavour"]


def test_other_axes():
    assert conflicts(extract_variants("Pepsi Diet"), extract_variants("Pepsi Regular")) == ["sugar"]
    assert extract_variants("Nescafe Gold Decaf") == {"caffeine": "decaf"}
    assert conflicts(extract_variants("Nido Milk Powder"), extract_variants("Nido Long Life Milk UHT")) == ["form"]
    assert "fat" not in extract_variants("Whole Wheat Bread")        # 'whole' alone is not full fat
    assert extract_variants("Whole Milk") == {"fat": "full"}
    assert extract_variants("") == {}
    assert merge({"fat": "full"}, {"flavour": "mango"}, {"fat": "full"}) == {"fat": "full", "flavour": "mango"}


def test_unstated_marked():
    assert unstated_marked({}, extract_variants("Almarai Laban Strawberry")) == ["flavour"]
    assert unstated_marked({}, extract_variants("Pepsi Diet 330ml")) == ["sugar"]
    assert unstated_marked({}, extract_variants("Almarai Low Fat Milk")) == ["fat"]
    # defaults are not suspicious
    assert unstated_marked({}, extract_variants("Almarai Full Fat Milk")) == []
    assert unstated_marked({}, extract_variants("Almarai Laban Plain")) == []
    assert unstated_marked({"fat": "full"}, extract_variants("Almarai Low Fat Milk")) == []


def test_packing_medium_is_an_exclusive_axis():
    """Tuna in sunflower oil vs olive oil vs water (and sunflower vs corn oil) are different SKUs.

    Before the 'medium' axis a Lulu 'Tuna Chunks in Olive Oil 170g' listing whose photo the model
    misread as sunflower oil scored tier 1 for 'Al Alali Tuna Chunks in Sunflower Oil 170g' and was
    auto-published when the sunflower-oil listings were missing.
    """
    sunflower = extract_variants("Al Alali Tuna Chunks in Sunflower Oil 170g")
    assert sunflower == {"medium": "sunflower_oil", "tuna_cut": "chunks"}
    assert extract_variants("تونة العلالي قطع في زيت دوار الشمس 170 جم") == sunflower
    assert conflicts(sunflower, extract_variants("Al Alali Tuna Chunks in Olive Oil 170g")) == ["medium"]
    assert conflicts(sunflower, extract_variants("Tuna Chunks in Water")) == ["medium"]
    assert conflicts(extract_variants("Carrefour Sunflower Oil 1.5L"), extract_variants("Carrefour Corn Oil 1.5L")) \
        == ["medium"]
    assert unstated_marked({}, extract_variants("Tuna in Brine 185g")) == ["medium"]
    # plain water products state no packing medium
    assert extract_variants("Al Ain Water 500ml") == {} and extract_variants("Masafi Drinking Water 1.5L") == {}


def test_olive_oil_tin_is_rejected_for_a_sunflower_oil_sku():
    from catalog_match.identity import build_sku_spec
    from catalog_match.models import Candidate
    from catalog_match.score import score_candidate

    spec = build_sku_spec({"name": "Al Alali Tuna Chunks in Sunflower Oil 170g", "brand": "Al Alali"}, {})
    olive = Candidate(image_url="https://www.luluhypermarket.com/medias/8280556-01.jpg",
                      page_url="https://www.luluhypermarket.com/en-ae/al-alali-tuna-chunks-olive-oil-170g/p/8280556",
                      page_title="Al Alali Tuna Chunks in Olive Oil 170g Online at Best Price | Lulu UAE",
                      title="Al Alali Tuna Chunks in Olive Oil 170g Online at Best Price | Lulu UAE",
                      domain="luluhypermarket.com", provider="serper", sanctioned=True)
    score = score_candidate(spec, olive)
    assert score.tier is None and "variant_conflict:medium" in score.hard_reject


# -- live run 2026-09-30: Light vs White tuna (sheet rows 57-60) ----------------------------------

def test_tuna_meat_grade_and_sheet_abbreviations():
    assert extract_variants("VIRGINIA L/MEAT TUNA WATER 170GM") == {"tuna_meat": "light", "medium": "water"}
    assert extract_variants("VIRGINIA WHITE TUNA S/F OIL 170GM") == {"tuna_meat": "white", "medium": "sunflower_oil"}
    assert extract_variants("SUP/T WT/MEAT SOLID TUNA 185GM") == {"tuna_meat": "white", "tuna_cut": "solid"}
    assert extract_variants("SUPER T SOLID TUNA SALT WATER 3X185GM") == {"medium": "brine", "tuna_cut": "solid"}
    assert extract_variants("GOLDEN PRIZE TUNA VEG OIL 185GM") == {"medium": "vegetable_oil"}
    assert extract_variants("RIO MARIE TUNA SUN OIL 3X70GM") == {"medium": "sunflower_oil"}
    assert extract_variants("SUPER T SOLID TUNA SUNFL OIL 3X185GM")["medium"] == "sunflower_oil"
    assert extract_variants("تونة لحم أبيض بالماء") == {"tuna_meat": "white", "medium": "water"}
    # On a tuna can 'light' is the meat grade, not low fat.
    assert extract_variants("American Light Meat Tuna 185g") == {"tuna_meat": "light"}
    assert extract_variants("Virginia Light Tuna in Water") == {"tuna_meat": "light", "medium": "water"}
    white, light = extract_variants("Virginia White Meat Tuna In Water 170g"), extract_variants("VIRGINIA L/MEAT TUNA")
    assert conflicts(light, white) == ["tuna_meat"]


def test_context_bound_phrases_need_the_product_type():
    # Without tuna, 'white', 'light' and 'water' keep their everyday meaning.
    assert extract_variants("Kiri White Cheese 200g") == {"flavour": "cheese"}
    assert extract_variants("Almarai Light Milk 1L") == {"fat": "low"}
    assert extract_variants("Masafi Water 1.5L") == {} and extract_variants("Arwa Spring Water 500ml") == {}
    # A label reading seldom repeats the product type: the SKU supplies it.
    assert extract_variants("WHITE MEAT") == {}
    assert extract_variants("WHITE MEAT", context="virginia tuna") == {"tuna_meat": "white"}


def test_fancy_and_light_are_close_lines_white_is_not():
    from catalog_match.variants import soft_conflicts

    fancy = extract_variants("ALALALI FANCY TUNA S/F OIL 85GM")
    light = extract_variants("Al Alali Light Meat Tuna in Sunflower Oil 85g")
    white = extract_variants("Al Alali White Meat Tuna in Sunflower Oil 85g")
    assert conflicts(fancy, light) == [] and soft_conflicts(fancy, light) == ["tuna_meat"]
    assert conflicts(fancy, white) == ["tuna_meat"]


@pytest.mark.parametrize("sheet_name,listing", [
    ("VIRGINIA L/MEAT TUNA WATER 170GM", "Virginia White Meat Tuna In Water 170g : Amazon.ae: Grocery"),
    ("VIRGINIA WHITE TUNA S/F OIL 170GM", "VIRGINIA Tuna L/meat solid in S/F Oil 170GM : Amazon.ae"),
])
def test_rows_58_and_60_listings_are_hard_rejected(sheet_name, listing):
    from catalog_match.identity import build_sku_spec
    from catalog_match.models import Candidate
    from catalog_match.score import score_candidate

    spec = build_sku_spec({"name": sheet_name, "brand": "VIRGINIA"}, {})
    cand = Candidate(image_url="https://m.media-amazon.com/images/I/81ULgp9PnIL.jpg",
                     page_url="https://www.amazon.ae/dp/B0TEST", page_title=listing, title=listing,
                     domain="amazon.ae", provider="serper", sanctioned=True)
    score = score_candidate(spec, cand)
    assert score.tier is None and "variant_conflict:tuna_meat" in score.hard_reject


def test_frozen_is_the_normal_state_of_fries_paratha_and_nuggets():
    from catalog_match.variants import unmarked_values

    fries = "AIDA FRENCH FRIES 1KG"
    assert unstated_marked({}, extract_variants("Aida Frozen French Fries 1kg"), fries) == []
    assert unstated_marked({}, extract_variants("Ashoka Frozen Plain Paratha"), "ASHOKA PLAIN PARATHA 400GM") == []
    # other forms of fries, and frozen milk, are still marked
    assert unstated_marked({}, extract_variants("Aida Fresh French Fries 1kg"), fries) == ["form"]
    assert unstated_marked({}, extract_variants("Almarai Frozen Milk"), "ALMARAI MILK 1L") == ["form"]
    assert unstated_marked({}, extract_variants("Aida Frozen French Fries 1kg")) == ["form"]   # no SKU context
    assert "frozen" in unmarked_values("form", fries) and "frozen" not in unmarked_values("form", "milk")
