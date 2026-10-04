"""catalog_match.learning: what the reviewers confirm becomes search knowledge, never an auto-publish.

Pure rules here (no database): reading the spelling from a warning, merging learned spellings and
sources into the Brands Mapping dict, what a learned brand and a learned source change in identity,
scoring, queries and routing. The database and the approval path are in tests/test_learning_db.py.
"""

import io
import socket

import pytest
from PIL import Image, ImageDraw

from catalog_match import decide, learning, pipeline, settings
from catalog_match.fetch import phash_hex
from catalog_match.identity import build_sku_spec
from catalog_match.models import Candidate, FetchedImage, ProviderResult, VerificationResult
from catalog_match.query_plan import build_queries
from catalog_match.score import score_candidate, source_trust
from catalog_match.verify import make_verdict


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    def refuse(*args, **kwargs):
        raise RuntimeError("network access is blocked in catalog_match unit tests")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    monkeypatch.setattr(settings, "_config", None)
    monkeypatch.setenv("AUTO_PUBLISH_ENABLED", "true")
    monkeypatch.setenv("AUTO_PUBLISH_BRANDS", "*")


SHEET = {"almarai": {"brand": "Almarai", "synonyms": ["Almarai", "Al Marai"],
                     "excluded_competitors": ["Al Rawabi"], "official_domains": ["almarai.com"]},
         "rio mare": {"brand": "Rio Mare", "synonyms": ["Rio Mare"]}}
ALIASES = [("SUP/T", "Super Tasty"), ("SUPER T/", "Super Tasty"), ("RIO MARIE", "Rio Mare"), ("ALMARAI", "Almaraii")]
# (sheet brand, site, approved products, identity rejections), as local_cache_db.get_brand_source_counts gives them
SOURCES = [("SUP/T", "www.tradeling.com", 2, 0), ("SUP/T", "www.instagram.com", 5, 0), ("SUP/T", "shutterstock.com", 3, 0),
           ("ALMARAI", "www.example-grocer.com", 2, 0), ("ALMARAI", "almarai.com", 4, 0),
           ("KABANI", "ajmanmarkets.ae", 2, 0), ("KABANI", "tradeling.com", 1, 0)]


def learned():
    return learning.apply(SHEET, ALIASES, SOURCES)


# ---------------------------------------------------------------------------
# Reading the lesson from a review
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("warnings,spelling", [
    (["tier T1", "warn:brand_spelling:Rio Mare", "warn:vlm_unsure"], "Rio Mare"),     # stored reasons
    (["brand_spelling:Super Tasty", "low_resolution"], "Super Tasty"),                 # the search response
    ("vlm_unsure|brand_spelling:Ina Paarmans", "Ina Paarmans"),                        # the review screen's param
    (["warn:brand_spelling"], None), ([], None), (None, None), ("", None),
])
def test_the_spelling_comes_from_the_brand_spelling_warning(warnings, spelling):
    assert learning.spelling_from(warnings) == spelling


# ---------------------------------------------------------------------------
# Merging into the Brands Mapping dict
# ---------------------------------------------------------------------------

def test_a_learned_spelling_resolves_the_sheet_brand_as_learned():
    spec = build_sku_spec({"name": "SUP/T WT/MEAT SOLIDTUNA SALTWATER 185GM", "brand": "SUP/T"}, learned())
    assert (spec.brand_conf, spec.brand_canonical) == ("learned", "Super Tasty")
    assert {"super tasty", "sup t"} <= set(spec.match_brands)
    assert build_queries(spec)[0].text.startswith("Super Tasty ")       # from the first query on
    assert spec.learned_domains == ("tradeling.com",)                   # social and stock sites never count


def test_the_sheet_always_wins():
    m = learned()
    assert m["almarai"]["brand"] == "Almarai" and "learned" not in m["almarai"]
    almarai = build_sku_spec({"name": "ALMARAI MILK 1L", "brand": "ALMARAI"}, m)
    assert almarai.brand_conf == "mapped"                               # 'Almaraii' is never learned over it
    assert almarai.learned_domains == ("example-grocer.com",)           # its official site is not repeated
    # the sheet maps 'Rio Mare' without 'RIO MARIE': the sheet's owner decides, nothing is learned
    rio = build_sku_spec({"name": "RIO MARIE TUNA 70G", "brand": "RIO MARIE"}, m)
    assert rio.brand_conf == "sheet_raw"


def test_sites_alone_give_an_unmapped_brand_no_identity_and_nothing_learned_changes_the_sku_key():
    kabani = build_sku_spec({"name": "KABANI MEAT MASALA 160 GM", "brand": "KABANI"}, learned())
    # the brand stays the sheet's (brand discovery still runs for it); only the sites are learned
    assert (kabani.brand_conf, kabani.learned_domains) == ("sheet_raw", ("ajmanmarkets.ae",))
    plain = build_sku_spec({"name": "KABANI MEAT MASALA 160 GM", "brand": "KABANI"}, SHEET)
    assert plain.brand_conf == "sheet_raw" and plain.sku_key == kabani.sku_key
    assert plain.competitors == kabani.competitors
    rio = build_sku_spec({"name": "RIO MARIE TUNA 70G", "brand": "RIO MARIE"},
                         learning.apply(SHEET, [], [("RIO MARIE", "ajmanmarkets.ae", 3, 0)]))
    assert rio.brand_conf == "sheet_raw" and rio.learned_domains == ("ajmanmarkets.ae",)


def test_sites_learned_under_a_sheet_brand_never_override_the_sheets_name_rule():
    # brand cell 'NESTLE', product 'NIDO ...': the sheet maps Nido, so the product is Nido (tier 1 possible)
    sheet = {"nido": {"brand": "Nido", "synonyms": ["Nido"], "official_domains": ["nido.com"]}}
    merged = learning.apply(sheet, [], [("NESTLE", "ajmanmarkets.ae", 4, 0)])
    row = {"name": "NIDO FORTIFIED MILK POWDER 900G", "brand": "NESTLE"}
    spec = build_sku_spec(row, merged)
    assert (spec.brand_conf, spec.brand_canonical) == ("mapped", "Nido")
    assert spec.competitors == build_sku_spec(row, sheet).competitors
    nido = Candidate(image_url="https://img.example-cdn.com/nido.jpg",
                     page_url="https://www.carrefouruae.com/mafuae/en/milk/nido-fortified-milk-powder-900g/p/1",
                     title="Nido Fortified Milk Powder 900g", page_title="Nido Fortified Milk Powder 900g")
    assert score_candidate(spec, nido).tier == 1


def test_a_learned_spelling_never_makes_another_product_reject_its_own_brand():
    # Live rows 49-52: 'SUP/T' and 'SUPER T/' are two sheet abbreviations of Super Tasty. Once 'SUP/T' is
    # taught, a 'SUPER T/' product must still find (and accept) the Super Tasty listing.
    merged = learning.apply(SHEET, [("SUP/T", "Super Tasty")], [])
    spec = build_sku_spec({"name": "SUPER T/WHITE MEAT SOLID TUNA IN WATER 185GM", "brand": "SUPER T/"}, merged)
    assert spec.brand_conf == "sheet_raw" and "super tasty" not in spec.competitors
    listing = Candidate(image_url="https://cdn.mafrservices.com/st-white.jpg",
                        page_url="https://www.carrefouruae.com/mafuae/en/tuna/super-tasty-white-meat-solid-tuna/p/7",
                        title="Super Tasty White Meat Solid Tuna In Water 185g",
                        page_title="Super Tasty White Meat Solid Tuna In Water 185g", provider="serper", rank=1)
    scored = score_candidate(spec, listing)
    assert not scored.hard_reject and scored.tier in (2, 3)
    from catalog_match.brand_discovery import discover
    found = discover(spec, [listing])
    assert found is not None and found.display == "Super Tasty"
    # nor does a lesson apply to a name that merely starts with the spelling
    other = build_sku_spec({"name": "SUPER TASTY TUNA CHUNKS 170G", "brand": "AL ALALI"}, merged)
    assert (other.brand_conf, other.brand_canonical) == ("sheet_raw", "AL ALALI")


def test_nothing_learned_means_the_mappings_unchanged():
    assert learning.apply(SHEET, [], []) == SHEET
    assert learning.apply(None, [("A", "A")], [("X", "instagram.com", 9, 0)]) == {}


@pytest.mark.parametrize("site", [
    "www.carrefouruae.com", "noon.com", "luluhypermarket.com",       # listed UAE retailers: trust already set
    "www.sharjahcoop.ae",                                            # (Sharjah Co-op, listed since 2026-10-04)
    "openfoodfacts.org",                                             # structured source
    "carrefourksa.com", "amazon.com", "www.tesco.com",               # foreign stores (other_retail)
    "danube.sa", "shop.example.co.uk", "angola.desertcart.com",      # foreign country domain / country store
    "www.instagram.com", "shutterstock.com", "almarai.com",          # social, stock, the brand's own site
])
def test_a_site_whose_trust_is_already_set_or_never_given_is_never_learned(site):
    merged = learning.apply(SHEET, [], [("ALMARAI", site, 9, 0)])
    assert "learned_domains" not in merged["almarai"] or not merged["almarai"]["learned_domains"]


def test_a_uae_store_the_search_does_not_list_is_learned():
    merged = learning.apply(SHEET, [], [("ALMARAI", "nesto.ae", 2, 0), ("ALMARAI", "uae.desertcart.com", 3, 0),
                                        ("ALMARAI", "instashop.com", 2, 0)])
    assert merged["almarai"]["learned_domains"] == ["uae.desertcart.com", "instashop.com", "nesto.ae"]


def test_sources_are_counted_per_brand_as_the_search_resolves_it():
    # 'ALMARAI' and 'Al Marai' are two sheet spellings of one mapped brand
    two_spellings = learning.apply(SHEET, [], [("ALMARAI", "example-grocer.com", 1, 0),
                                               ("Al Marai", "example-grocer.com", 1, 0)])
    assert two_spellings["almarai"]["learned_domains"] == ["example-grocer.com"]
    rejected = learning.apply(SHEET, [], [("ALMARAI", "example-grocer.com", 2, 0),
                                          ("Al Marai", "example-grocer.com", 0, 1)])
    assert not rejected["almarai"].get("learned_domains")
    once = learning.apply(SHEET, [], [("ALMARAI", "example-grocer.com", 1, 0)])
    assert not once["almarai"].get("learned_domains")


def test_a_listed_retailer_keeps_its_own_trust_even_if_a_brand_lists_it():
    m = {"almarai": dict(SHEET["almarai"], learned_domains=["carrefouruae.com"])}
    spec = build_sku_spec({"name": "ALMARAI FULL FAT MILK 1L", "brand": "ALMARAI"}, m)
    page = Candidate(image_url="https://cdn.mafrservices.com/a.jpg",
                     page_url="https://www.carrefouruae.com/mafuae/en/milk/almarai-full-fat-milk-1l/p/1")
    assert source_trust(spec, page) == (3, "uae_retailer")


# ---------------------------------------------------------------------------
# What a learned source changes: trust and the site: query, never an auto-publish
# ---------------------------------------------------------------------------

def test_a_learned_source_is_trusted_like_a_uae_retailer_for_its_brand_only():
    m = learned()
    almarai = build_sku_spec({"name": "ALMARAI FULL FAT MILK 1L", "brand": "ALMARAI"}, m)
    other = build_sku_spec({"name": "AL RAWABI FULL FAT MILK 1L", "brand": "AL RAWABI"}, m)
    page = Candidate(image_url="https://img.example-cdn.com/a.jpg",
                     page_url="https://www.example-grocer.com/almarai-full-fat-milk-1l")
    assert source_trust(almarai, page) == (3, "reviewed_source")
    assert source_trust(other, page)[0] == 0
    foreign = Candidate(image_url="https://img.example-cdn.com/a.jpg",
                        page_url="https://www.example-grocer.com/saudi-en/almarai-full-fat-milk-1l")
    assert source_trust(almarai, foreign) != (3, "reviewed_source")
    q3 = next(q for q in build_queries(almarai) if q.query_id == "Q3")
    assert "site:almarai.com OR site:example-grocer.com OR site:carrefouruae.com" in q3.text


def _png():
    img = Image.new("RGB", (800, 800), (255, 255, 255))
    ImageDraw.Draw(img).rectangle([220, 120, 580, 680], fill=(40, 90, 160))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


class Search:
    kind, fallback, sanctioned = "search", False, True

    def __init__(self, cands):
        self.name, self.cands, self.queries = "serper", list(cands), []

    def search(self, query, hl, spec_):
        self.queries.append(query)
        return ProviderResult(provider="serper", status="ok", candidates=list(self.cands))


class Images:
    def __init__(self, body):
        self.body = body

    def fetch(self, cands, spec_):
        out = []
        for c in cands:
            with Image.open(io.BytesIO(self.body)) as im:
                out.append(FetchedImage(candidate=c, ok=True, content_sha256="cd" * 32, width=im.width,
                                        height=im.height, path_or_bytes=self.body, phash=phash_hex(im)))
        return out


class Reads:
    def __init__(self, reading):
        self.reading = reading

    def verify(self, spec_, images):
        return VerificationResult(status="ok", calls=1,
                                  verdicts=[make_verdict(spec_, i, self.reading) for i, _ in enumerate(images)])


def test_a_pick_trusted_only_through_a_learned_source_is_never_auto_published():
    spec = build_sku_spec({"name": "ALMARAI FULL FAT FRESH MILK 1L", "brand": "ALMARAI"}, learned())
    hit = Candidate(image_url="https://img.example-cdn.com/almarai-ff-1l.jpg",
                    page_url="https://www.example-grocer.com/almarai-full-fat-fresh-milk-1l",
                    title="Almarai Full Fat Fresh Milk 1L", page_title="Almarai Full Fat Fresh Milk 1L",
                    provider="serper", rank=1)
    assert score_candidate(spec, hit).tier == 1
    reading = {"brand_text": "Almarai", "variant_text": "Full Fat Fresh Milk", "size_text": "1 L", "pack_count": 1,
               "view": "front_packshot", "brand_match": "yes", "variant_match": "yes", "size_match": "yes"}
    outcome = pipeline.find_product_image(spec, providers=[Search([hit])], fetcher=Images(_png()),
                                          verifier=Reads(reading), expansion=False)
    assert outcome.decision == "REVIEW_PRESELECTED"
    assert "auto_blocked:reviewed_source" in outcome.winner.reasons


def test_a_learned_brand_is_never_auto_published_and_needs_no_discovery():
    spec = build_sku_spec({"name": "SUPER/T LIGHT MEAT TUNA SOBEANOIL 185GM", "brand": "SUPER/T"}, learned())
    hit = Candidate(image_url="https://img.example-cdn.com/st.jpg",
                    page_url="https://www.carrefouruae.com/mafuae/en/tuna/super-tasty-light-meat-tuna-185g/p/9",
                    title="Super Tasty Light Meat Tuna In Soyabean Oil 185g",
                    page_title="Super Tasty Light Meat Tuna In Soyabean Oil 185g", provider="serper", rank=1)
    reading = {"brand_text": "Super Tasty", "variant_text": "Light Meat Tuna in Soyabean Oil", "size_text": "185 g",
               "pack_count": 1, "view": "front_packshot", "brand_match": "yes", "variant_match": "yes",
               "size_match": "yes"}
    search = Search([hit])
    outcome = pipeline.find_product_image(spec, providers=[search], fetcher=Images(_png()), verifier=Reads(reading),
                                          expansion=False)
    assert outcome.decision == "REVIEW_PRESELECTED"
    assert "auto_blocked:brand_conf_learned" in outcome.winner.reasons
    assert outcome.discovered_brands == []                               # already known: nothing to discover
    assert search.queries[0].startswith("Super Tasty ")
    # the warning stays: approving keeps counting for the spelling, a WRONG_BRAND rejection against it
    assert "warn:brand_spelling:Super Tasty" in outcome.winner.reasons
    assert learning.spelling_from(outcome.winner.reasons) == "Super Tasty"


def test_a_spelling_the_sheet_lists_under_another_entry_is_never_learned_as_a_new_brand():
    # Without this rule 'RIO MARIE' would become a learned brand 'Riomare' whose competitors include the sheet's
    # own 'Rio Mare': every real Rio Mare listing would then be hard-rejected as another brand.
    sheet = {"rio mare": {"brand": "Rio Mare", "synonyms": ["Rio Mare", "Riomare"]}}
    merged = learning.apply(sheet, [("RIO MARIE", "Riomare")], {})
    assert merged == sheet
    rio = build_sku_spec({"name": "RIO MARIE LIGHT MEAT TUNA 70G", "brand": "RIO MARIE"}, merged)
    assert rio.brand_conf == "sheet_raw"
