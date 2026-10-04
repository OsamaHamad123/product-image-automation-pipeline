"""Regression tests for the review of the search package (live run 2026-10-03 and constructed cases).

Every case gave the wrong answer named in its comment before the fix. Sockets are blocked.
"""
import csv
import json
import socket
from pathlib import Path

import pytest

from catalog_match import brand_discovery, decide, settings, sheet_names
from catalog_match.identity import build_sku_spec
from catalog_match.models import (
    Candidate, FetchedImage, ProviderHealth, QualityReport, RankedCandidate, VerificationResult, VlmImageVerdict,
)
from catalog_match.query_plan import build_queries
from catalog_match.score import rank_key, score_candidate
from catalog_match.text_norm import tokens
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
    assert sheet_names.readable("SALTWATER TAFFY 200G") == "SALTWATER TAFFY 200G"   # was 'SALT WATER TAFFY'
    assert "SALT WATER" in sheet_names.readable("SUP/T WT/MEAT SOLIDTUNA SALTWATER 185GM")      # row 49
    assert spec_of("SUP/T WT/MEAT SOLIDTUNA SALTWATER 185GM", "SUP/T").variants.get("medium") == "brine"


# ---------------------------------------------------------------------------
# site_name_brand: a brand's own page naming its product keeps tier 1
# ---------------------------------------------------------------------------

MARMUM = {"marmum": {"brand": "Marmum", "synonyms": ["Marmum"], "official_domains": ["marmum.ae"]}}
MARMUM_PAGE = "https://www.marmum.ae/products/fresh-milk-full-cream"
MARMUM_IMG = "https://www.marmum.ae/wp-content/uploads/2020/08/fresh-milk-full-cream-1l.png"


@pytest.mark.parametrize("title", [
    "Fresh Milk Full Cream 1L - Marmum",                 # golden uae-009: was tier 2 (site_name_brand)
    "Marmum | Fresh Milk Full Cream 1L",
    "Fresh Milk Full Cream 1L | Marmum Dairy Farm LLC",
])
def test_the_brands_own_page_naming_its_product_keeps_tier1(title):
    spec = spec_of("MARMUM FRESH MILK FULL CREAM 1L", "MARMUM", MARMUM)
    s = score_candidate(spec, listing(title, MARMUM_PAGE, MARMUM_IMG))
    assert s.tier == 1 and "site_name_brand" not in s.conflicts


@pytest.mark.parametrize("title", [
    "Fresh Milk Full Cream 1L | Marmum Korea",              # a store in another country under the brand's name
    "Buy Lacnor Fresh Milk Full Cream 1L Online | Marmum",  # another brand where the brand stands
])
def test_a_site_name_that_is_no_evidence_of_the_brand_still_caps(title):
    spec = spec_of("MARMUM FRESH MILK FULL CREAM 1L", "MARMUM", MARMUM)
    s = score_candidate(spec, listing(title, MARMUM_PAGE, MARMUM_IMG))
    assert s.tier == 2 and "site_name_brand" in s.conflicts


# ---------------------------------------------------------------------------
# rank_key: product-type coverage before 'no soft conflict'
# ---------------------------------------------------------------------------

def test_a_listing_of_another_product_type_never_ranks_above_the_right_type():
    # 'Aida Mixed Vegetables' (no product word) ranked above the crinkle-cut fries for the fries' soft doubt,
    # and route() pre-selected the vegetables when both read MATCH
    spec = spec_of("AIDA FRENCH FRIES 1KG", "AIDA")
    fries = listing("Aida Crinkle Cut French Fries 1kg | Lulu UAE", image_url="https://i.example.com/fries.jpg")
    veg = listing("Aida Mixed Vegetables 1kg | Carrefour UAE", "https://www.carrefouruae.com/mafuae/en/x/aida-veg/p/2",
                  "https://i.example.com/veg.jpg")
    assert rank_key(fries, score_candidate(spec, fries)) < rank_key(veg, score_candidate(spec, veg))
    out = route(spec, [read_as_match(spec, veg, "a"), read_as_match(spec, fries, "b")])
    assert out.winner is not None and out.winner.candidate.image_url == fries.image_url


def test_the_official_milk_page_ranks_above_a_laban_listing_of_the_brand():
    spec = spec_of("MARMUM FRESH MILK FULL CREAM 1L", "MARMUM", MARMUM)
    page = listing("Milk Full Cream 1L | Marmum Korea", "https://www.marmum.ae/products/milk-full-cream",
                   "https://www.marmum.ae/wp-content/uploads/milk-1l.png")                # capped: a soft doubt
    laban = listing("Shop Marmum Laban Full Cream 1L online in Dubai, Abu Dhabi and all UAE",
                    "https://www.noon.com/uae-en/laban-full-cream-1l/N20346969A/p/", "https://i.example.com/laban.jpg")
    ps, ls = score_candidate(spec, page), score_candidate(spec, laban)
    assert ps.tier == ls.tier == 2 and ps.matched["variants"] == ls.matched["variants"] == ["fat"]
    assert "site_name_brand" in ps.conflicts and not ls.conflicts
    assert ls.matched["coverage"] == 0.0 < ps.matched["coverage"]
    assert rank_key(page, ps) < rank_key(laban, ls)                 # the laban (no 'milk') ranked first


# ---------------------------------------------------------------------------
# Q1: every brand spelling stripped once, nothing else
# ---------------------------------------------------------------------------

REPO = Path(__file__).resolve().parents[2]


def suggested_mappings():
    import google_sheets

    with open(REPO / "runs" / "2026-10-03" / "brands_mapping_suggested.csv", encoding="utf-8-sig", newline="") as fh:
        return google_sheets.parse_brand_mapping_rows([r for r in csv.reader(fh)])


@pytest.mark.parametrize("name, brand, mappings, q1", [
    ("FERRERO ROCHER T16 200G", "FERRERO ROCHER",
     {"ferrero rocher": {"brand": "Ferrero Rocher", "synonyms": ["Ferrero Rocher", "Ferrero"]}},
     "Ferrero Rocher T16 200g"),                                   # was 'Ferrero Rocher ROCHER T16 200g'
    ("AL AIN FARMS FRESH MILK 1L", "AL AIN FARMS",
     {"al ain farms": {"brand": "Al Ain Farms", "synonyms": ["Al Ain"]}},
     "Al Ain Farms FRESH MILK 1L"),                                # was 'Al Ain Farms FARMS FRESH MILK 1L'
])
def test_q1_writes_a_brand_with_a_prefix_synonym_once(name, brand, mappings, q1):
    assert build_queries(spec_of(name, brand, mappings))[0].text == q1


@pytest.mark.parametrize("name, q1", [
    ("SUPER T/LIGHT MEAT TUNA IN SUNFLOWER OIL 185GM", "Super Tasty LIGHT MEAT TUNA IN SUNFLOWER OIL 185g"),
    ("SUPER T/WHITE MEAT TUNA IN SUNFLOWER OIL 185GM", "Super Tasty WHITE MEAT TUNA IN SUNFLOWER OIL 185g"),
    ("SUPER T/MEAT SOLID TUNA SALT WATE3X185GM", "Super Tasty MEAT SOLID TUNA SALT WATER 3x185g"),   # row 50
])
def test_q1_strips_only_the_brand_glued_to_a_word(name, q1):
    # 'SUPER T/' took the whole word 'T/LIGHT' with it: the meat grade was lost ('Super Tasty MEAT TUNA ...')
    assert build_queries(spec_of(name, "SUPER T/", suggested_mappings()))[0].text == q1


def _stem(tok):
    return tok[:-1] if len(tok) > 3 and tok.endswith("s") and not tok.endswith("ss") else tok


def _sheet_words(spec):
    """The readable sheet name's own words (shorthand written out) without brand spellings and size words."""
    from catalog_match import abbreviations, identity, query_plan
    from catalog_match.variants import spec_context

    text = sheet_names.spec_name(spec)
    text = abbreviations.expand(text, spec_context(spec)) if query_plan._lang_of(spec.raw_name) == "en" else text
    brand = set()
    for p in (spec.brand_raw, spec.brand_canonical, spec.brand_ar) + tuple(spec.match_brands):
        toks = tokens(p, strip_clitics=True)
        brand.update(toks)
        brand.add("".join(toks))
    skip = brand | identity._UNIT_WORDS | query_plan._PACK_UNITS
    return [t for t in tokens(text, strip_clitics=True) if len(t) > 1 and not t[0].isdigit() and t not in skip]


def _golden_and_live_specs():
    golden = json.loads((REPO / "tests" / "eval" / "fixtures" / "golden_skus.json").read_text(encoding="utf-8"))
    maps = json.loads((REPO / "tests" / "eval" / "fixtures" / "brand_mappings.json").read_text(encoding="utf-8"))
    for sku in golden["skus"]:
        row = {"name": sku.get("name_en") or sku.get("name_ar") or "", "name_ar": sku.get("name_ar", ""),
               "brand": sku.get("brand", ""), "brand_ar": sku.get("brand_ar", ""), "barcode": sku.get("barcode", ""),
               "category": sku.get("category", ""), "size": sku.get("size", "")}
        yield sku["id"], build_sku_spec(row, maps["mappings"])
    suggested = suggested_mappings()
    with open(REPO / "runs" / "2026-10-03" / "rows_2_61.csv", encoding="utf-8-sig", newline="") as fh:
        for r in csv.DictReader(fh):
            for mappings in ({}, suggested):
                yield f"row {r['row']}", spec_of(r["name"], r["brand"], mappings)


def test_no_q1_repeats_a_word_pair_or_loses_a_sheet_word():
    # golden uae-066 'Ferrero Rocher Rocher Chocolate', uae-051 'Al Jadeed Bakery Bakery Arabic Bread', live rows
    # 50 and 51 lost MEAT
    bad = []
    for sku_id, spec in _golden_and_live_specs():
        q1 = build_queries(spec)[0].text
        toks = tokens(q1, strip_clitics=True)
        pairs = list(zip(toks, toks[1:]))
        own = tokens(sheet_names.spec_name(spec), strip_clitics=True)
        repeated = [p for p in pairs if (p[0] == p[1] or pairs.count(p) > 1) and p not in set(zip(own, own[1:]))]
        bag = {_stem(t) for t in toks}
        lost = [w for w in _sheet_words(spec) if _stem(w) not in bag]
        if repeated or lost:
            bad.append((sku_id, q1, repeated, lost))
    assert not bad


# ---------------------------------------------------------------------------
# Brand-spelling memory: a hint, never this row's evidence; digits count; every run starts empty
# ---------------------------------------------------------------------------

MAYO = ("AMERICAN G/ MAYONNAISE 473ML", "AMERICAN G/")
AG_MAYO = listing("American Garden Mayonnaise 473ml | Carrefour UAE",
                  "https://www.carrefouruae.com/mafuae/en/x/american-garden-mayonnaise-473ml/p/1",
                  "https://img.example-cdn.com/ag-mayo.jpg")
TUNA = ("AMERICAN G/ LIGHT MEAT TUNA 185GM", "AMERICAN G/")
AMERICANA_TUNA = listing("Americana Light Meat Tuna 185g | Lulu UAE", image_url="https://img.example-cdn.com/am.jpg")


def test_a_spelling_proved_for_mayonnaise_is_only_a_hint_for_a_tuna_row():
    assert brand_discovery.find(spec_of(*MAYO), [AG_MAYO]).display == "American Garden"
    tuna = spec_of(*TUNA)
    # was American Garden, added to the tuna row's brand phrases: a label reading 'American Garden' then
    # confirmed a brand this row's own listings never showed
    assert brand_discovery.find(tuna, [AMERICANA_TUNA]) is None
    assert brand_discovery.hint(tuna, [AMERICANA_TUNA]).display == "American Garden"
    hinted = brand_discovery.as_hint(tuna, brand_discovery.hint(tuna, [AMERICANA_TUNA]))
    assert hinted.match_brands == tuna.match_brands and build_queries(hinted)[0].text.startswith("American Garden")
    # a listing of this row naming the product under the spelling makes it this row's evidence
    ag_tuna = listing("American Garden Light Meat Tuna 185g | Carrefour UAE",
                      "https://www.carrefouruae.com/mafuae/en/x/american-garden-tuna/p/3")
    assert brand_discovery.find(tuna, [AMERICANA_TUNA, ag_tuna]).display == "American Garden"


def test_a_tuna_row_sends_the_hint_query_and_keeps_its_own_brand_evidence():
    from catalog_match import pipeline

    class Search:
        kind, fallback, sanctioned, name = "search", False, True, "serper"

        def __init__(self):
            self.queries = []

        def search(self, query, hl, spec_):
            from catalog_match.models import ProviderResult

            self.queries.append(query)
            hits = [AG_MAYO] if "mayonnaise" in query.lower() else [AMERICANA_TUNA]
            return ProviderResult(provider="serper", status="ok", candidates=hits)

    class Reads:
        def verify(self, spec_, images):
            # the Americana can misread as American Garden
            return VerificationResult(status="ok", calls=1, verdicts=[make_verdict(spec_, i, {
                "brand_text": "American Garden", "variant_text": "Light Meat Tuna", "size_text": "185g",
                "view": "front_packshot", "brand_match": "yes", "variant_match": "yes", "size_match": "yes"})
                for i, _ in enumerate(images)])

    class Images:
        def fetch(self, cands, spec_):
            return [FetchedImage(candidate=c, ok=True, width=800, height=800, content_sha256=c.image_url[-12:] * 6,
                                 path_or_bytes=_packshot()) for c in cands]

    search = Search()
    mayo = pipeline.find_product_image(spec_of(*MAYO), providers=[search], fetcher=Images(), verifier=Reads(),
                                       expansion=False)
    assert mayo.discovered_brands == ["American Garden"]
    out = pipeline.find_product_image(spec_of(*TUNA), providers=[search], fetcher=Images(), verifier=Reads(),
                                      expansion=False)
    assert "American Garden LIGHT MEAT TUNA 185g" in search.queries               # the hint is still searched
    assert out.discovered_brands == []                                            # was ['American Garden']
    assert out.winner is None


def _packshot():
    import io

    from PIL import Image, ImageDraw

    img = Image.new("RGB", (800, 800), (255, 255, 255))
    ImageDraw.Draw(img).rectangle([220, 120, 580, 680], fill=(60, 80, 150))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def test_a_sheet_brand_with_digits_is_not_the_one_without():
    assert brand_discovery.memory_key("7UP") != brand_discovery.memory_key("UP")              # both were 'up'
    assert brand_discovery.memory_key("3 ROSES") != brand_discovery.memory_key("ROSES")


def _remember_one():
    spec = spec_of(*MAYO)
    brand_discovery.remember(spec, brand_discovery.discover(spec, [AG_MAYO]))
    assert brand_discovery.recall(spec) is not None
    return spec


def test_a_worker_run_starts_with_no_remembered_spelling():
    import main

    spec = _remember_one()
    main._forget_brand_spellings()
    assert brand_discovery.recall(spec) is None


def test_an_evaluation_run_starts_with_no_remembered_spelling():
    import sys

    sys.path.insert(0, str(REPO / "tests" / "eval"))
    import harness

    spec = _remember_one()
    harness.run_all("v2", sku_ids=["no-such-sku"])
    assert brand_discovery.recall(spec) is None
