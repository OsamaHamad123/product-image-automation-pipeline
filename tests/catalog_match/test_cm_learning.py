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
SOURCES = {"SUP/T": ["www.tradeling.com", "www.instagram.com", "shutterstock.com"],
           "ALMARAI": ["www.example-grocer.com", "almarai.com"], "KABANI": ["sharjahcoop.ae"]}


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


def test_a_source_alone_makes_a_learned_entry_and_nothing_learned_changes_the_sku_key():
    kabani = build_sku_spec({"name": "KABANI MEAT MASALA 160 GM", "brand": "KABANI"}, learned())
    assert (kabani.brand_conf, kabani.learned_domains) == ("learned", ("sharjahcoop.ae",))
    plain = build_sku_spec({"name": "KABANI MEAT MASALA 160 GM", "brand": "KABANI"}, SHEET)
    assert plain.brand_conf == "sheet_raw" and plain.sku_key == kabani.sku_key


def test_nothing_learned_means_the_mappings_unchanged():
    assert learning.apply(SHEET, [], {}) == SHEET
    assert learning.apply(None, [("A", "A")], {"X": ["instagram.com"]}) == {}


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
    assert not [r for r in outcome.winner.reasons if r.startswith(decide.WARN_PREFIX + "brand_spelling")]


def test_a_spelling_the_sheet_lists_under_another_entry_is_never_learned_as_a_new_brand():
    # Without this rule 'RIO MARIE' would become a learned brand 'Riomare' whose competitors include the sheet's
    # own 'Rio Mare': every real Rio Mare listing would then be hard-rejected as another brand.
    sheet = {"rio mare": {"brand": "Rio Mare", "synonyms": ["Rio Mare", "Riomare"]}}
    merged = learning.apply(sheet, [("RIO MARIE", "Riomare")], {})
    assert merged == sheet
    rio = build_sku_spec({"name": "RIO MARIE LIGHT MEAT TUNA 70G", "brand": "RIO MARIE"}, merged)
    assert rio.brand_conf == "sheet_raw"
