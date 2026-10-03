"""Live run of 2026-10-03 (smoke_6.json, sheet rows 2-61): two fixes.

* Brand names differ by a plural 's' between the sheet and the stores: 'KITCHEN TREASURE MEAT MASALA 160GM'
  (row 39) had the right Lulu listing 'Kitchen Treasures Meat Masala 160 g' in its results, scored as
  no-brand tier 3. The last Latin word of a brand phrase now matches with or without a final 's'
  (4+ letters, never 'ss'), in scoring and in the label reading alike. Real typos ('INA PARAMANS' for
  Ina Paarman's, 'RIO MARIE' for Rio Mare) still need a Brands Mapping synonym.
* angola.desertcart.com (row 47) was pre-checked without the foreign_store warning: on desertcart the
  first host label names the country store.
"""
import pytest

from catalog_match import decide, settings
from catalog_match.identity import build_sku_spec
from catalog_match.models import Candidate, FetchedImage, QualityReport, RankedCandidate, VlmImageVerdict
from catalog_match.score import score_candidate
from catalog_match.text_norm import any_brand_in, brand_in, phrase_in
from catalog_match.verify import MATCH, MISMATCH, classify

TREASURE = build_sku_spec({"name": "KITCHEN TREASURE MEAT MASALA 160GM", "brand": "KITCHEN TREASURE"}, {})
SARAS = build_sku_spec({"name": "SARAS MEAT MASALA 160GM", "brand": "SARAS"}, {})


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    monkeypatch.setattr(settings, "_config", None)
    monkeypatch.setenv("AUTO_PUBLISH_ENABLED", "false")
    monkeypatch.setenv("AUTO_PUBLISH_BRANDS", "")


def listing(title, page_url, image_url="https://img.example-cdn.com/1.jpg"):
    return Candidate(image_url=image_url, page_url=page_url, title=title, page_title=title, provider="serper",
                     query_id="Q1", rank=1)


@pytest.mark.parametrize("phrase, text, found", [
    ("KITCHEN TREASURE", "Kitchen Treasures Meat Masala 160 g", True),
    ("Kitchen Treasures", "Kitchen Treasure Meat Masala", True),
    ("Kelloggs", "Kellogg Corn Flakes 500g", True),
    ("Ina Paarman", "Ina Paarman's Seasoning Meat Spice", True),          # the possessive folds to 'paarmans'
    ("Lays", "Lay chips", False),                                         # 4 letters ending in 's': exact only
    ("Swiss", "Swis Miss cocoa", False),                                  # '-ss' is not a plural
    ("Treasure", "Treasurer box", False),                                 # still a whole word
    ("Almarai", "Almaraiz", False),
    ("Al Ain", "Al Ains", False),                                         # a 3-letter last word stays exact
    ("Family", "Families pack", False),
    ("INA PARAMANS", "Ina Paarman's Seasoning", False),                  # a typo, not a plural: needs a synonym
    ("RIO MARIE", "Rio Mare Light Meat Tuna", False),
])
def test_brand_phrases_match_singular_and_plural_only(phrase, text, found):
    assert brand_in(phrase, text) is found, (phrase, text)
    assert (any_brand_in([phrase], text) == phrase) is found


def test_plain_phrase_matching_is_unchanged():
    # product words keep the exact rule: 'chunk' is not 'chunks' for variants and keywords
    assert not phrase_in("Kitchen Treasure", "Kitchen Treasures Meat Masala")
    assert not phrase_in("chunk", "Light Meat Tuna Chunks")


def test_row39_the_plural_brand_listing_is_brand_evidence():
    cand = listing("Kitchen Treasures Meat Masala 160 g Online at Best Price | Lulu UAE",
                   "https://gcc.luluhypermarket.com/en-ae/kitchen-treasures-meat-masala-160-g/p/1")
    score = score_candidate(TREASURE, cand)
    assert score.tier in (1, 2) and not score.hard_reject, score
    # another brand's listing of the same product stays outside
    other = score_candidate(TREASURE, listing("Eastern Meat Masala 160g | Carrefour UAE",
                                              "https://www.carrefouruae.com/mafuae/en/eastern-meat-masala-160g/p/2"))
    assert other.tier not in (1, 2)


def test_row39_the_label_reading_counts_the_plural_brand():
    read = VlmImageVerdict(index=0, brand_text="KITCHEN TREASURES", variant_text="Meat Masala", size_text="160g",
                           view="front_packshot", brand_match="yes", variant_match="yes", size_match="yes")
    assert classify(TREASURE, read) == MATCH
    other = VlmImageVerdict(index=0, brand_text="EASTERN", variant_text="Meat Masala", size_text="160g",
                            view="front_packshot", brand_match="no", variant_match="yes", size_match="yes")
    assert classify(TREASURE, other) == MISMATCH


def rc(spec, cand):
    read = VlmImageVerdict(index=0, brand_text="Saras", variant_text="Meat Masala", size_text="160 g",
                           view="front_packshot", brand_match="yes", variant_match="yes", size_match="yes",
                           decision="MATCH")
    fi = FetchedImage(candidate=cand, ok=True, content_sha256="ab" * 32, width=800, height=800, path_or_bytes=b"x")
    return RankedCandidate(candidate=cand, score=score_candidate(spec, cand), fetched=fi,
                           quality=QualityReport(hard_ok=True, quality_score=0.8), verdict=read)


@pytest.mark.parametrize("page_url, foreign", [
    ("https://angola.desertcart.com/products/123-saras-meat-masala-160-g", True),     # live row 47
    ("https://kenya.desertcart.com/products/123-saras-meat-masala-160-g", True),
    ("https://uae.desertcart.com/products/123-saras-meat-masala-160-g", False),
    ("https://www.desertcart.ae/products/123-saras-meat-masala-160-g", False),
    ("https://www.desertcart.com/products/123-saras-meat-masala-160-g", False),     # no country named
])
def test_desertcart_country_stores(page_url, foreign):
    cand = listing("Saras Meat Masala 160 G | Desertcart", page_url)
    warned = "foreign_store" in decide.review_warnings(SARAS, rc(SARAS, cand))
    assert warned is foreign, page_url
