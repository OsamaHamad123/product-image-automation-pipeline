"""catalog_match.url_gtin: the barcodes UAE stores write in their own URLs (free evidence, data/url_gtins.json).

Live runs 2026-10-04/05: 23 of the winning image URLs carried a valid GTIN (Sharjah Co-op '/p/<GTIN>' and
'medias/<GTIN>-1200Wx1200H', Spinneys, Grand Hyper, an amazon.ae slug) while only the JSON-LD of a page was read.
A URL barcode is evidence only: never gtin_on_page, never a tier, never an auto-publish. Its uses: the review
evidence, the barcode an approval keeps for a row without one, and a local-index lookup of the same barcode -
each only when the listing's own title agrees with the row. Sockets are blocked.
"""

import pytest

from catalog_match import facade, local_index, pipeline, url_gtin
from catalog_match.gtin import normalize_gtin
from catalog_match.identity import build_sku_spec
from catalog_match.local_index import LocalIndexProvider, MemoryCatalogStore
from catalog_match.models import Candidate, RankedCandidate
from catalog_match.score import score_candidate

from test_cm_local_index import (  # noqa: F401  (the autouse fixture keeps every test offline)
    FakeFetcher, ImageFetcher, ReadsMatch, SearchStub, _offline, _packshot,
)

TUNA = {"name": "AL TAGHZIAH CHICKEN LUNCHEON MEAT 850G", "brand": "AL TAGHZIAH"}
G14 = normalize_gtin("5283002830089")[0]
SHARJAH_PAGE = "https://www.sharjahcoop.ae/en/al-taghziah-chicken-luncheon-meat-850g/p/5283002830089?srsltid=AU7"
SHARJAH_IMAGE = "https://www.sharjahcoop.ae/medias/5283002830089-1200Wx1200H-001.jpg?context=bWFzdGVy"


def spec_for(row=None):
    return build_sku_spec(dict(row or TUNA), {})


@pytest.mark.parametrize("url, kind, expected", [
    (SHARJAH_PAGE, "page", "5283002830089"),
    (SHARJAH_IMAGE, "image", "5283002830089"),
    ("https://www.sharjahcoop.ae/medias/1200Wx1200H-00000-6084000090241-001.jpg?context=x", "image", "6084000090241"),
    ("https://www.sharjahcoop.ae/medias/0032894020840-1200Wx1200H-002.jpg", "image", "032894020840"),
    ("https://www.unioncoop.ae/media/catalog/product/6/2/6294010703134_1.jpg", "image", "6294010703134"),
    ("https://www.unioncoop.ae/5033712451018-6394287.html", "page", "5033712451018"),
    ("https://prod-spinneys-cdn-new.azureedge.net/media/images/products/2023/12/6291003607486.jpg", "image",
     "6291003607486"),
    ("https://talabat.dhmedia.io/image/talabat-nv/Regional_Images/Al_Rawabi_Tmart/6291030109564_1.jpg", "image",
     "6291030109564"),
    ("https://uae-image-bucket.s3.ap-south-1.amazonaws.com/products/8714555000034_2.webp", "image", "8714555000034"),
    ("https://www.thefreshmarketdubai.com/products/zwan-luncheon-meat-beef-hot-spicy-340g-8714555001239", "page",
     "8714555001239"),
    ("https://www.amazon.ae/Kimball-9556191030742_6-KIMBALL-Spaghetti-400/dp/B07PN2TM9C", "page", "9556191030742"),
    ("https://www.carrefouruae.com/mafuae/en/utensils-gadgets/delcasa-ice-cream-scoop-dc2426/p/6294016314273", "page",
     "6294016314273"),
])
def test_known_stores_write_the_barcode_in_their_urls(url, kind, expected):
    found = url_gtin.from_url(url, kind)
    assert found == normalize_gtin(expected)[0]


@pytest.mark.parametrize("url, kind", [
    # store-internal numbers: Carrefour's media ids pass a check digit by chance, Lulu's and Carrefour's short ids
    ("https://cdn.mafrservices.com/sys-master-root/hd8/h94/50736324968478/126579_main.jpg", "image"),
    ("https://www.luluhypermarket.com/en-ae/al-rawabi-laban-1l/p/4504613", "page"),
    ("https://www.carrefouruae.com/mafuae/en/fresh-food/al-rawabi-laban-180ml-x-6/p/975386", "page"),
    # an in-store (restricted) code, a coupon range, a case code (indicator 1), a wrong check digit
    ("https://prod-spinneys-cdn-new.azureedge.net/media/images/products/2024/05/22240910.jpg", "image"),
    ("https://www.sharjahcoop.ae/en/x/p/0512345000107", "page"),
    ("https://www.sharjahcoop.ae/en/x/p/16297000958184", "page"),
    ("https://www.sharjahcoop.ae/en/x/p/6297000958188", "page"),
    # a valid barcode on a store that is not listed (a Qatar store): its pack may carry another one
    ("https://www.ansargallery.com/en/yumway-straight-cut-french-fries-9mm-2-5kg-qatar-626861497454", "page"),
    # a page pattern never reads an image URL and the other way round
    (SHARJAH_IMAGE, "page"),
    ("", "page"),
])
def test_internal_codes_and_unknown_stores_give_no_barcode(url, kind):
    assert url_gtin.from_url(url, kind) is None


def test_plausible_barcodes():
    assert url_gtin.plausible(G14)
    assert url_gtin.plausible(normalize_gtin("96385074")[0])                      # a GTIN-8
    assert not url_gtin.plausible(normalize_gtin("9912345678904")[0])             # a coupon range
    assert not url_gtin.plausible(normalize_gtin("2012345678909")[0])             # in-store
    assert not url_gtin.plausible(None) and not url_gtin.plausible("123")


def test_a_candidate_with_two_different_barcodes_has_none():
    c = Candidate(image_url="https://www.sharjahcoop.ae/medias/6084000090241-1200Wx1200H-001.jpg",
                  page_url=SHARJAH_PAGE)
    assert url_gtin.url_gtin(c) is None
    assert url_gtin.url_gtin(Candidate(image_url=SHARJAH_IMAGE, page_url=SHARJAH_PAGE)) == G14
    assert url_gtin.url_gtin(Candidate(image_url=SHARJAH_IMAGE, page_url="https://x.example.com/p")) == G14


# ---------------------------------------------------------------------------
# Identity agreement and the review evidence
# ---------------------------------------------------------------------------

def cand(title, page=SHARJAH_PAGE, image=SHARJAH_IMAGE):
    return Candidate(image_url=image, page_url=page, title=title, page_title=title, provider="serper", query_id="Q1")


@pytest.mark.parametrize("title, agrees", [
    ("Al Taghziah Chicken Luncheon Meat 850g | Sharjah Co-operative Society", True),
    ("Chicken Luncheon Meat 850g", False),                       # the brand only in the URL slug
    ("Al Taghziah Chicken Luncheon Meat 340g", False),           # another size
    ("Al Taghziah Beef Luncheon Meat 850g", False),              # another variant
])
def test_a_url_barcode_counts_only_where_the_listing_title_agrees(title, agrees):
    spec = spec_for()
    c = cand(title)
    score = score_candidate(spec, c)
    assert url_gtin.identity_agrees(score) is agrees
    ev = facade.evidence(RankedCandidate(candidate=c, score=score), spec)
    assert ev["url_gtin"] == "5283002830089"                     # the evidence is always shown
    assert (ev["page_gtin"], ev["page_gtin_source"]) == (("5283002830089", "url") if agrees else (None, None))


def test_the_page_barcode_wins_over_the_url_and_a_url_barcode_never_scores():
    spec = spec_for(dict(TUNA, barcode="5283002830089"))
    c = cand("Al Taghziah Chicken Luncheon Meat 850g")
    stated = Candidate(**{**c.__dict__, "gtin_on_page": "6084000090241"})
    ev = facade.evidence(RankedCandidate(candidate=stated, score=score_candidate(spec, stated)), spec)
    assert (ev["page_gtin"], ev["page_gtin_source"], ev["url_gtin"]) == ("6084000090241", "page", "5283002830089")
    # the URL holds the sheet's own barcode: the score is the one of the same listing without it
    plain = Candidate(**{**c.__dict__, "page_url": "https://www.sharjahcoop.ae/en/x/p/0000000000017",
                         "image_url": "https://www.sharjahcoop.ae/medias/x.jpg"})
    with_url, without = score_candidate(spec, c), score_candidate(spec, plain)
    assert with_url.tier == without.tier and with_url.matched["gtin"] is None


# ---------------------------------------------------------------------------
# The local index
# ---------------------------------------------------------------------------

def test_the_index_reads_the_barcode_from_the_url_at_harvest_time():
    store = MemoryCatalogStore()
    store.upsert("sharjahcoop", [(SHARJAH_PAGE, None), ("https://www.sharjahcoop.ae/en/x-y/p/D000000000002", None)])
    assert [r.url_gtin for r in store.rows.values()] == [G14, None]
    assert [r.id for r in store.by_gtin(G14)] == [1]


def test_a_sheet_barcode_finds_its_store_page_even_when_the_slug_leaves_the_brand_out():
    page = "https://www.sharjahcoop.ae/en/chicken-luncheon-meat-850g/p/5283002830089"
    store = MemoryCatalogStore()
    store.upsert("sharjahcoop", [(page, None)])
    fetcher = FakeFetcher({page: {"name": "Al Taghziah Chicken Luncheon Meat 850g", "image": "https://x.ae/i.jpg"}})
    spec = spec_for(dict(TUNA, barcode="5283002830089"))
    res = LocalIndexProvider(store=store, fetcher=fetcher, max_pages=3, page_ttl_days=30).lookup(spec)
    assert [c.page_url for c in res.candidates] == [page]
    # without the barcode, the brand-less slug of a page never read is not worth a read
    fresh = MemoryCatalogStore()
    fresh.upsert("sharjahcoop", [(page, None)])
    res = LocalIndexProvider(store=fresh, fetcher=FakeFetcher(), max_pages=3, page_ttl_days=30).lookup(spec_for())
    assert res.status == "empty"


def _run(serper_cands, store, fetcher, bodies):
    idx = LocalIndexProvider(store=store, fetcher=fetcher, max_pages=3, page_ttl_days=30)
    return pipeline.find_product_image(spec_for(), providers=[idx, SearchStub("serper", serper_cands)],
                                       fetcher=ImageFetcher(bodies), verifier=ReadsMatch(), expansion=False)


def test_a_row_without_barcode_finds_the_index_page_of_another_stores_url_barcode():
    """Spinneys names the picture after the barcode and its title agrees (tier 2: it states no size, so the search
    goes on): the index's Sharjah Co-op page of the same barcode (its slug alone would never be read: no brand word in
    it) joins the pool, free, under IDXG."""
    spinneys = Candidate(image_url="https://prod-spinneys-cdn-new.azureedge.net/media/images/products/2023/12/"
                                   "5283002830089.jpg",
                         page_url="https://www.spinneys.com/en-ae/catalogue/al-taghziah-luncheon_1/",
                         title="Al Taghziah Chicken Luncheon Meat", provider="serper", rank=1)
    page = "https://www.sharjahcoop.ae/en/chicken-luncheon-meat-850g/p/5283002830089"
    image = "https://www.sharjahcoop.ae/medias/5283002830089-1200Wx1200H-001.jpg"
    store = MemoryCatalogStore()
    store.upsert("sharjahcoop", [(page, None)])
    fetcher = FakeFetcher({page: {"name": "Al Taghziah Chicken Luncheon Meat 850g", "image": image}})
    outcome = _run([spinneys], store, fetcher, {image: _packshot(4), spinneys.image_url: _packshot(2)})
    assert ("local_index", "IDXG") in [(h.provider, h.query_id) for h in outcome.provider_health]
    found = [rc for rc in outcome.ranked if rc.candidate.page_url == page]
    assert found and found[0].candidate.provider == "local_index" and found[0].candidate.sanctioned is False
    assert fetcher.calls == [page]


def test_no_index_lookup_when_the_listing_title_disagrees():
    other = Candidate(image_url="https://prod-spinneys-cdn-new.azureedge.net/media/images/products/2023/12/"
                                "5283002830089.jpg",
                      page_url="https://www.spinneys.com/en-ae/catalogue/x_1/", title="Chicken Luncheon Meat 340g",
                      provider="serper", rank=1)
    page = "https://www.sharjahcoop.ae/en/chicken-luncheon-meat-850g/p/5283002830089"
    store = MemoryCatalogStore()
    store.upsert("sharjahcoop", [(page, None)])
    fetcher = FakeFetcher({page: {"name": "Al Taghziah Chicken Luncheon Meat 850g"}})
    outcome = _run([other], store, fetcher, {})
    assert "IDXG" not in [h.query_id for h in outcome.provider_health] and fetcher.calls == []


def test_a_row_with_a_valid_barcode_uses_it_not_the_urls():
    assert local_index.GTIN_QUERY_ID == "IDXG"
    spinneys = Candidate(image_url="https://prod-spinneys-cdn-new.azureedge.net/media/images/products/2023/12/"
                                   "6084000090241.jpg", page_url="https://www.spinneys.com/en-ae/catalogue/x_1/",
                         title="Al Taghziah Chicken Luncheon Meat 850g", provider="serper", rank=1)
    idx = LocalIndexProvider(store=MemoryCatalogStore(), fetcher=FakeFetcher(), max_pages=3, page_ttl_days=30)
    idx.store.upsert("sharjahcoop", [("https://www.sharjahcoop.ae/en/a-b/p/6084000090241", None)])
    outcome = pipeline.find_product_image(spec_for(dict(TUNA, barcode="5283002830089")),
                                          providers=[idx, SearchStub("serper", [spinneys])],
                                          fetcher=ImageFetcher({}), verifier=ReadsMatch(), expansion=False)
    assert "IDXG" not in [h.query_id for h in outcome.provider_health]
