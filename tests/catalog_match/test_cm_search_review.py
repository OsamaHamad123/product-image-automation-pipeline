"""Regression tests for the review of the search package (live run 2026-10-03 and constructed cases).

Every case gave the wrong answer named in its comment before the fix. Sockets are blocked.
"""
import socket

import pytest

from catalog_match import brand_discovery, decide, settings
from catalog_match.identity import build_sku_spec
from catalog_match.models import (
    Candidate, FetchedImage, ProviderHealth, QualityReport, RankedCandidate, VerificationResult, VlmImageVerdict,
)
from catalog_match.score import rank_key, score_candidate
from catalog_match.variants import extract_variants
from catalog_match.verify import MISMATCH, classify, make_verdict

LULU = "https://gcc.luluhypermarket.com/en-ae/x/p/1"


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError(f"network access attempted: {args!r}")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    monkeypatch.setattr(settings, "_config", None)
    monkeypatch.setenv("AUTO_PUBLISH_ENABLED", "false")
    monkeypatch.setenv("AUTO_PUBLISH_BRANDS", "")
    brand_discovery.forget_all()
    yield
    brand_discovery.forget_all()


def spec_of(name, brand, mappings=None, **row):
    return build_sku_spec(dict(row, name=name, brand=brand), mappings or {})


def listing(title, page_url=LULU, image_url="https://img.example-cdn.com/1.jpg"):
    return Candidate(image_url=image_url, page_url=page_url, title=title, page_title=title, provider="serper",
                     query_id="Q1", rank=1, sanctioned=True)


def read_as_match(spec, cand, sha):
    return RankedCandidate(candidate=cand, score=score_candidate(spec, cand),
                           fetched=FetchedImage(candidate=cand, ok=True, width=800, height=800,
                                                content_sha256=sha * 64, path_or_bytes=b"x"),
                           quality=QualityReport(hard_ok=True), verdict=VlmImageVerdict(index=0, decision="MATCH"))


def route(spec, rcs):
    rcs = sorted(rcs, key=lambda rc: rank_key(rc.candidate, rc.score))
    return decide.route(spec, rcs, VerificationResult(status="ok", calls=1),
                        [ProviderHealth(provider="serper", status="ok", query_id="Q1")])


def label(spec, variant_text, brand="Shan", size="100g"):
    return classify(spec, make_verdict(spec, 0, {
        "brand_text": brand, "variant_text": variant_text, "size_text": size, "view": "front_packshot",
        "brand_match": "yes", "variant_match": "yes", "size_match": "yes"}))


# ---------------------------------------------------------------------------
# Protein: 'meat masala' is one value; 'luncheon meat' is never a protein; both sides read alike
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("title", [
    "Everest Meat Masala for Mutton & Chicken 100g",
    "Shan Meat Masala Mix for Beef, Mutton & Lamb 100g",
    "Everest Meat Masala 100g - Mutton & Lamb",
])
def test_a_meat_masala_listing_naming_the_meats_it_cooks_is_not_rejected(title):
    # was variant_conflict:protein: the listing's own 'meat masala' gave way to the meats it is for
    spec = spec_of("EVEREST MEAT MASALA 100G", "EVEREST")
    assert not score_candidate(spec, listing(title)).hard_reject


def test_a_meat_masala_for_red_meats_is_that_sku():
    spec = spec_of("SHAN MEAT MASALA 100G", "SHAN")
    assert score_candidate(spec, listing("Shan Meat Masala Mix for Beef, Mutton & Lamb 100g")).tier == 1
    # a red meat on its own is close, never rejected (was a hard reject for mutton + lamb)
    s = score_candidate(spec, listing("Shan Mutton & Lamb Masala 100g"))
    assert s.tier == 2 and any(c.startswith("soft_variant_conflict:protein") for c in s.conflicts)


@pytest.mark.parametrize("variant_text", ["Meat Masala - for mutton & lamb curry", "Meat Masala for beef & mutton",
                                          "Meat Masala"])
def test_the_label_of_a_meat_masala_naming_its_meats_is_not_a_mismatch(variant_text):
    assert label(spec_of("SHAN MEAT MASALA 100G", "SHAN"), variant_text) != MISMATCH       # was MISMATCH


def test_luncheon_meat_with_spices_has_no_protein():
    spec = spec_of("ZWAN LUNCHEON MEAT WITH SPICES 200G", "ZWAN")
    assert "protein" not in spec.variants                          # was 'meat': any spice word opened it
    s = score_candidate(spec, listing("Zwan Chicken Luncheon Meat with Spices 200g"))
    assert s.tier == 2 and not s.hard_reject                       # was a hard reject


@pytest.mark.parametrize("text, protein", [
    ("Meat Masala", "meat"), ("Meat Spice", "meat"), ("Seasoning Meat Spice", "meat"), ("بهارات لحم", "meat"),
    ("Meat & Chicken Masala", "chicken+meat"), ("Meat Burger", "meat"), ("برجر لحم", "meat"),
    ("Luncheon Meat with Spices", None), ("Beef Luncheon Meat with Spices", "beef"),
    ("Shan Meat & Vegetable Recipe & Masala Mix", None), ("Meat Stew Sauce", None),
])
def test_meat_is_a_protein_only_bound_to_its_masala_spice_or_burger(text, protein):
    assert extract_variants(text).get("protein") == protein


def test_the_sheet_side_gives_way_to_the_animal_a_listing_keeps_its_meat_masala():
    spec = spec_of("KABANI MUTTON MEAT MASALA 160GM", "KABANI")
    assert spec.variants == {"protein": "mutton"}
    assert score_candidate(spec, listing("Kabani Mutton Meat Masala 160g")).tier == 1
    assert "variant_conflict:protein" in score_candidate(spec, listing("Kabani Chicken Masala 160g")).hard_reject


def test_a_two_protein_masala_needs_both():
    spec = spec_of("EASTERN MEAT & CHICKEN MASALA 100G", "EASTERN")
    assert spec.variants == {"protein": "chicken+meat"}            # was 'chicken': meat gave way
    assert score_candidate(spec, listing("Eastern Chicken Masala 100g")).tier != 1
    assert score_candidate(spec, listing("Eastern Meat & Chicken Masala 100g")).tier == 1


def test_a_chicken_masala_sku_never_takes_a_meat_masala_as_tier1():
    spec = spec_of("EVEREST CHICKEN MASALA 100G", "EVEREST")
    assert score_candidate(spec, listing("Everest Meat Masala for Mutton & Chicken 100g")).tier != 1
    assert "variant_conflict:protein" in score_candidate(spec, listing("Everest Meat Masala 100g")).hard_reject


@pytest.mark.parametrize("name, brand, title, page", [
    ("AJMI MEAT MASALA 160GM", "AJMI", "Ajmi Chicken Masala 160 g Online at Best Price | Lulu UAE",
     "https://gcc.luluhypermarket.com/en-ae/ajmi-chicken-masala-160-g/p/2104380"),
    ("SARAS MEAT MASALA 160GM", "SARAS", "Saras Chicken Masala, 160 gm : Amazon.ae: Grocery",
     "https://www.amazon.ae/Saras-Chicken-Masala-160-gm/dp/B086N4VFZZ"),
    ("TASTY FOOD MEAT MASALA 160GM", "TASTY FOOD", "Tasty Food Fish Masala 160gm: Buy Online at Best Price in UAE - "
     "Amazon.ae", "https://www.amazon.ae/Tasty-Food-Fish-Masala-160gm/dp/B07P75SYZJ"),
    ("TASTY FOOD MEAT MASALA 160 GM", "TASTY FOOD", "Tasty Food Chicken Masala 160gm: Buy Online at Best Price in UAE "
     "- Amazon.ae", "https://www.amazon.ae/Tasty-Food-Chicken-Masala-160gm/dp/B07P8B2TNP"),
    ("NELLARA MEAT MASALA 165GM", "NELLARA", "Nellara Fish Masala, 165 gm : Amazon.ae: Grocery",
     "https://www.amazon.ae/Nellara-Fish-Masala-165-gm/dp/B086N559T9"),
    ("TARGET CHICKN LUNCHENMEAT RECTANGL 340GM", "TARGET", "Target Beef Luncheon Meat 340 Gm: Buy Online at Best "
     "Price in UAE -  Amazon.ae", "https://www.amazon.ae/Target-Beef-Luncheon-Meat-340/dp/B0941PFL9K"),
    ("ZWAN BEEF LUNCHEON MEAT 850GM", "ZWAN", "Zwan Chicken Luncheon Meat 850 g Online at Best Price | Lulu UAE",
     "https://gcc.luluhypermarket.com/en-ae/zwan-chicken-luncheon-meat-850-g/p/1651"),
])
def test_the_live_protein_rejects_stay_rejected(name, brand, title, page):
    assert "variant_conflict:protein" in score_candidate(spec_of(name, brand), listing(title, page)).hard_reject


def test_the_sheet_protein_counts_when_the_listing_opens_the_context():
    # was tier 2 (unstated_variant:protein) for chicken and only tier 2 for beef: the sheet was read without
    # the context the listing's 'with Seasoning' opens
    spec = spec_of("INDOMIE CHICKEN NOODLES 75G", "INDOMIE")
    right = score_candidate(spec, listing("Indomie Chicken Flavour Instant Noodles with Seasoning 75g"))
    wrong = score_candidate(spec, listing("Indomie Beef Flavour Instant Noodles with Seasoning 75g"))
    assert right.tier == 1 and wrong.tier is None and "variant_conflict:protein" in wrong.hard_reject
    v = make_verdict(spec, 0, {"brand_text": "Indomie", "variant_text": "Beef flavour with seasoning",
                               "size_text": "75g", "view": "front_packshot", "brand_match": "yes",
                               "variant_match": "yes", "size_match": "yes"})
    assert classify(spec, v) == MISMATCH


def test_a_meat_burger_is_not_a_chicken_burger():
    spec = spec_of("AMERICANA MEAT BURGER 1KG", "AMERICANA")
    assert "variant_conflict:protein" in score_candidate(spec, listing("Americana Chicken Burger 1kg")).hard_reject
    beef = score_candidate(spec, listing("Americana Beef Burger 1kg"))                  # close: held for review
    assert beef.tier == 2 and not beef.hard_reject
    arabic = spec_of("AMERICANA BURGER 1KG", "AMERICANA", name_ar="برجر لحم امريكانا 1 كجم")
    assert "variant_conflict:protein" in score_candidate(arabic, listing("امريكانا برجر دجاج 1 كجم")).hard_reject


def test_a_brand_name_never_states_a_protein():
    spec = spec_of("LAMB WESTON BURGER FRIES 1KG", "LAMB WESTON")
    assert "protein" not in spec.variants                         # was 'lamb'
    s = score_candidate(spec, listing("Lamb Weston Burger Fries 1kg"))
    assert s.tier == 1 and not any("protein" in c for c in s.conflicts)


# ---------------------------------------------------------------------------
# Medium: 'vegetable oil' covers soybean, sunflower, canola and corn oil (soft); 'SALTWATER' only on a tuna
# ---------------------------------------------------------------------------

def test_a_vegetable_oil_tuna_is_close_to_soybean_oil_never_rejected_for_it():
    spec = spec_of("GOLDEN PRIZE L/ MEAT TUNA VEGE OIL 185GM", "GOLDEN PRIZE")                     # row 35
    assert spec.variants.get("medium") == "vegetable_oil"
    soya = score_candidate(spec, listing("Golden Prize Canned Skipjack Light Meat Tuna - Chunk In Soya Oil 185g"))
    assert soya.tier == 2 and not soya.hard_reject                         # was variant_conflict:medium
    assert any(c.startswith("soft_variant_conflict:medium") for c in soya.conflicts)
    olive = score_candidate(spec, listing("Golden Prize Light Meat Tuna In Olive Oil 185g"))
    assert "variant_conflict:medium" in olive.hard_reject
    v = make_verdict(spec, 0, {"brand_text": "Golden Prize", "variant_text": "Light Meat Tuna in Soybean Oil",
                               "size_text": "185g", "view": "front_packshot", "brand_match": "yes",
                               "variant_match": "yes", "size_match": "yes"})
    assert classify(spec, v) == "UNSURE"                                  # for the reviewer, never a MATCH


def test_soybean_and_sunflower_oil_stay_apart():
    spec = spec_of("SUPER/T LIGHT MEAT TUNA SOBEANOIL 185GM", "SUPER/T")                          # row 52
    assert "variant_conflict:medium" in score_candidate(
        spec, listing("Super Tasty Light Meat Tuna In Sunflower Oil 185g")).hard_reject
    assert not score_candidate(spec, listing("Super Tasty Light Meat Tuna In Vegetable Oil 185g")).hard_reject


def test_saltwater_is_brine_only_on_a_tuna():
    from catalog_match import sheet_names

    assert sheet_names.readable("SALTWATER TAFFY 200G") == "SALTWATER TAFFY 200G"   # was 'SALT WATER TAFFY'
    assert "SALT WATER" in sheet_names.readable("SUP/T WT/MEAT SOLIDTUNA SALTWATER 185GM")      # row 49
    assert spec_of("SUP/T WT/MEAT SOLIDTUNA SALTWATER 185GM", "SUP/T").variants.get("medium") == "brine"
