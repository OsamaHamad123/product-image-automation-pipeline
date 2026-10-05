"""X0 page recovery offers a page's own gallery when its main image is no good for the SKU.

Real misses (owner's runs): a trusted page whose title is the product exactly, but its main image is a twin pack
(Golden Prize 185g on carrefouruae, read multi_product, pack 2) or the store's placeholder logo (Emirates Coop,
read not_product); the page's other gallery images were never tried. Now X0 offers up to
pages.MAX_GALLERY_IMAGES more images of the page's own product gallery (JSON-LD image list or the embedded product
JSON; never og:image duplicates, never recommendation carousels), provider 'page', marked page_gallery, through the
normal stages, within X0's one verifier call; a gallery image is pre-checked only on a MATCH. The first pass keeps
pages.MAX_IMAGES_PER_PAGE = 1. Under a replay cassette, a gallery image it holds no download of is left out.

Stage doubles and the offline fixture come from test_cm_expand.py; everything between them is production code.
"""

from pathlib import Path

import pytest

from catalog_match import cassette, decide, expand, facade, pages, pipeline
from catalog_match.identity import build_sku_spec
from catalog_match.models import Candidate
from test_cm_expand import (  # noqa: F401  (the offline fixture and the doubles)
    StubFetcher, StubPages, StubProvider, StubVerifier, _offline, cand, packshot_png,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "pages"
GP_MAPPINGS = {"golden prize": {"brand": "Golden Prize", "synonyms": ["GOLDEN PRIZE"]}}
GP = build_sku_spec({"name": "GOLDEN PRIZE LIGHT MEAT TUNA 185G", "brand": "GOLDEN PRIZE"}, GP_MAPPINGS)

CARREFOUR = "https://www.carrefouruae.com/mafuae/en/canned-tuna/golden-prize-light-meat-tuna-185g/p/543210"
COOP = "https://www.emiratescoop.ae/en/product/golden-prize-light-meat-tuna-185g"
TWIN = "https://cdn.mafrservices.com/pim-content/AE/media/product/543210/twin-pack.jpg"
SINGLE = "https://cdn.mafrservices.com/pim-content/AE/media/product/543210/single-can.jpg"
BACK = "https://cdn.mafrservices.com/pim-content/AE/media/product/543210/back-label.jpg"
LOGO = "https://www.emiratescoop.ae/media/catalog/product/default/coop-store.jpg"
COOP_SINGLE = "https://www.emiratescoop.ae/media/catalog/product/g/p/golden-prize-185.jpg"
TITLE = "Golden Prize Light Meat Tuna 185g"

READ_SINGLE = {"brand_text": "Golden Prize", "variant_text": "Light Meat Tuna", "size_text": "185 g", "pack_count": 1,
               "view": "front_packshot", "brand_match": "yes", "variant_match": "yes", "size_match": "yes"}
READ_TWIN = dict(READ_SINGLE, size_text="2 x 185 g", pack_count=2, view="multi_product")
READ_BACK = dict(READ_SINGLE, view="other_side", variant_match="unsure", size_match="unsure")
READ_LOGO = {"brand_text": "", "variant_text": "", "size_text": "", "pack_count": None, "view": "not_product",
             "brand_match": "unsure", "variant_match": "unsure", "size_match": "unsure"}


def gallery_page(name, images, og=None, brand="Golden Prize", gtin=None):
    """A store page whose JSON-LD Product lists its own gallery; og:image is the first one unless given."""
    og = images[0] if og is None else og
    listed = ", ".join(f'"{u}"' for u in images)
    gtin_json = f', "gtin13": "{gtin}"' if gtin else ""
    return (f'<html><head><title>{name} | Store</title><meta property="og:image" content="{og}">'
            f'<script type="application/ld+json">{{"@type": "Product", "name": "{name}", '
            f'"brand": {{"@type": "Brand", "name": "{brand}"}}, "image": [{listed}]{gtin_json}}}</script>'
            f'</head><body></body></html>')


def run_gp(listings, docs, bodies, readings, web=(), max_calls=4):
    web_p, shop_p = StubProvider("serper_web", list(web)), StubProvider("serper_shopping", [])
    pages_s = StubPages(docs)
    exp = expand.Expansion(web=web_p, shopping=shop_p, visual=None, pages=pages_s, max_calls=max_calls)
    verifier = StubVerifier(readings)
    outcome = pipeline.find_product_image(GP, providers=[StubProvider("serper", listings)],
                                          fetcher=StubFetcher(bodies), verifier=verifier, expansion=exp)
    return outcome, {"web": web_p, "shop": shop_p, "pages": pages_s, "verifier": verifier}


def ranked_of(outcome, url):
    return next((rc for rc in outcome.ranked if rc.candidate.image_url == url), None)


# ---------------------------------------------------------------------------
# pages.py: the page's own gallery, never og:image duplicates or recommendation carousels
# ---------------------------------------------------------------------------

def _read(name, url):
    return pages.extract((FIXTURES / name).read_text(encoding="utf-8"), url)


def test_the_first_pass_still_offers_one_image_per_page():
    assert pages.MAX_IMAGES_PER_PAGE == 1
    info = pages.extract(gallery_page(TITLE, [TWIN, SINGLE, BACK]), CARREFOUR)
    assert [c.image_url for c in pages.page_candidates(info, title=TITLE)] == [TWIN]


def test_gallery_images_are_the_products_own_list_after_its_main_image():
    info = pages.extract(gallery_page(TITLE, [TWIN, SINGLE, BACK, "https://cdn.x.ae/fourth.jpg"]), CARREFOUR)
    assert [i.url for i in pages.gallery_images(info)] == [SINGLE, BACK]          # at most two
    gallery = pages.gallery_candidates(info, title=TITLE, query_id="X0", rank=3, max_images=3)
    main = pages.page_candidates(info, title=TITLE, query_id="X0", rank=3)[0]
    assert [c.image_url for c in gallery] == [SINGLE, BACK, "https://cdn.x.ae/fourth.jpg"]
    assert all(c.page_gallery and c.provider == "page" and not c.sanctioned for c in gallery)
    assert all((c.page_url, c.page_title, c.domain, c.query_id) == (main.page_url, main.page_title, main.domain, "X0")
               for c in gallery)
    assert [c.rank for c in gallery] == [main.rank + 1, main.rank + 2, main.rank + 3] and not main.page_gallery


def test_the_carrefour_fixture_offers_its_back_shot_and_noon_its_second_image_key():
    carrefour = _read("carrefour_graph.html", "https://www.carrefouruae.com/mafuae/en/p/631098")
    assert [i.url for i in pages.gallery_images(carrefour)] == [
        "https://cdn.mafrservices.com/sys-master-root/h3a/h91/51234/631098_back.jpg"]   # the og:image rendition is the main
    noon = _read("noon_next_data.html", "https://www.noon.com/uae-en/al-alali-fancy-tuna/N40123456A/p/")
    assert [i.url for i in pages.gallery_images(noon)] == ["https://f.nooncdn.com/p/v1690000000/N40123456A_2.jpg"]


def test_never_an_og_image_duplicate_a_recommendation_or_another_products_image():
    # talabat: the page's Product has one image; og:image is another rendition name; the other Products and the
    # ItemList (a carousel) are other products
    assert pages.gallery_images(_read("related_products.html", "https://www.talabat.com/uae/mart/p/1")) == []
    # a gallery entry that is also the og:image is not offered again
    info = pages.extract(gallery_page(TITLE, [TWIN, SINGLE, BACK], og=SINGLE), CARREFOUR)
    assert [i.url for i in pages.gallery_images(info)] == [BACK]
    assert pages.gallery_images(_read("og_only.html", "https://www.example.ae/p/1")) == []
    assert pages.gallery_images(pages.PageInfo(url=CARREFOUR, ok=False)) == []


# ---------------------------------------------------------------------------
# X0 through find_product_image
# ---------------------------------------------------------------------------

def test_golden_prize_twin_pack_page_the_single_can_from_its_gallery_is_picked():
    listing = cand(TWIN, f"{TITLE} | Carrefour UAE", CARREFOUR)           # Google filed the page under its main image
    outcome, d = run_gp([listing], docs={CARREFOUR: gallery_page(TITLE, [TWIN, SINGLE, BACK], gtin="6281007035309")},
                        bodies={TWIN: packshot_png(3), SINGLE: packshot_png(5), BACK: packshot_png(7)},
                        readings={TWIN: READ_TWIN, SINGLE: READ_SINGLE, BACK: READ_BACK},
                        web=[cand("https://x.ae/web.jpg", TITLE, "https://x.ae/p")])
    assert d["pages"].fetched == [CARREFOUR]
    assert outcome.decision == "REVIEW_PRESELECTED"
    win = outcome.winner.candidate
    assert (win.image_url, win.provider, win.query_id, win.page_gallery, win.sanctioned) == (SINGLE, "page", "X0",
                                                                                         True, False)
    assert len(d["verifier"].calls) == 2 and set(d["verifier"].calls[1]) == {SINGLE, BACK}   # X0's one call
    assert d["web"].calls == [] and d["shop"].calls == []                  # no paid call
    back = ranked_of(outcome, BACK)
    assert back.status != "preselected" and back.candidate.page_gallery
    shown = facade.serialise_candidate(outcome.winner, GP)
    assert shown["evidence"]["page_gallery"] is True and shown["evidence"]["page_gtin"] == "6281007035309"
    assert facade.serialise_candidate(ranked_of(outcome, TWIN), GP)["evidence"]["page_gallery"] is False


def test_the_same_twin_pack_under_another_address_is_marked_and_the_gallery_read_in_the_second_pass():
    own_twin = "https://cdn.mafrservices.com/pim-content/AE/media/product/543210/main.jpg"
    listing = cand(TWIN, f"{TITLE} | Carrefour UAE", CARREFOUR)
    body = packshot_png(3)
    outcome, d = run_gp([listing], docs={CARREFOUR: gallery_page(TITLE, [own_twin, SINGLE])},
                        bodies={TWIN: body, own_twin: body, SINGLE: packshot_png(5)},
                        readings={TWIN: READ_TWIN, SINGLE: READ_SINGLE})
    assert expand.STORE_IMAGE_WRONG in ranked_of(outcome, own_twin).reasons
    assert outcome.winner is not None and outcome.winner.candidate.image_url == SINGLE
    assert len(d["verifier"].calls) == 2 and d["verifier"].calls[1] == [SINGLE]       # still X0's one call
    assert d["web"].calls == [] and d["shop"].calls == []


def test_emirates_coop_placeholder_logo_page_its_product_image_is_picked():
    listing = cand(LOGO, f"{TITLE} | Emirates Coop", COOP)
    outcome, d = run_gp([listing], docs={COOP: gallery_page(TITLE, [LOGO, COOP_SINGLE])},
                        bodies={LOGO: packshot_png(9), COOP_SINGLE: packshot_png(6)},
                        readings={LOGO: READ_LOGO, COOP_SINGLE: READ_SINGLE})
    assert decide.MISMATCH == ranked_of(outcome, LOGO).verdict.decision
    assert outcome.decision == "REVIEW_PRESELECTED" and outcome.winner.candidate.image_url == COOP_SINGLE
    assert outcome.winner.candidate.page_gallery


def test_a_stores_own_image_from_the_index_gets_its_page_read_for_the_gallery():
    # the local index already holds the page's main image (the logo): X0 reads the page for its gallery only
    indexed = Candidate(image_url=LOGO, page_url=COOP, page_title=TITLE, title=TITLE, provider="local_index",
                        query_id="IDX", rank=1, sanctioned=False)
    outcome, d = run_gp([indexed], docs={COOP: gallery_page(TITLE, [LOGO, COOP_SINGLE])},
                        bodies={LOGO: packshot_png(9), COOP_SINGLE: packshot_png(6)},
                        readings={LOGO: READ_LOGO, COOP_SINGLE: READ_SINGLE})
    assert d["pages"].fetched == [COOP]
    assert outcome.winner is not None and outcome.winner.candidate.image_url == COOP_SINGLE


def test_a_good_main_image_brings_no_gallery():
    listing = cand("https://x.ae/broken.jpg", f"{TITLE} | Carrefour UAE", CARREFOUR)
    outcome, d = run_gp([listing], docs={CARREFOUR: gallery_page(TITLE, [SINGLE, BACK])},
                        bodies={"https://x.ae/broken.jpg": "http_404", SINGLE: packshot_png(5), BACK: packshot_png(7)},
                        readings={SINGLE: READ_SINGLE, BACK: READ_BACK})
    assert outcome.winner.candidate.image_url == SINGLE and not outcome.winner.candidate.page_gallery
    assert ranked_of(outcome, BACK) is None                               # never offered


def test_a_gallery_image_read_unsure_is_never_pre_checked():
    # the gallery's only other image is the back of the can, read UNSURE (tier 1 by its page's name): a tier-1
    # UNSURE listing image would be the fallback pick, a gallery image never is
    listing = cand(TWIN, f"{TITLE} | Carrefour UAE", CARREFOUR)
    outcome, d = run_gp([listing], docs={CARREFOUR: gallery_page(TITLE, [TWIN, BACK])},
                        bodies={TWIN: packshot_png(3), BACK: packshot_png(7)},
                        readings={TWIN: READ_TWIN, BACK: READ_BACK})
    back = ranked_of(outcome, BACK)
    assert back.score.tier == 1 and back.verdict.decision == decide.UNSURE and back.candidate.page_gallery
    assert outcome.winner is None and outcome.decision == "REVIEW_UNSELECTED"


def test_when_x0s_call_is_spent_the_gallery_is_offered_unread():
    # the main image is new to the round: X0's one call reads it (the twin pack); its gallery comes after, with no
    # call left: downloaded and offered for review, never pre-checked unread
    listing = cand("https://x.ae/broken.jpg", f"{TITLE} | Carrefour UAE", CARREFOUR)
    outcome, d = run_gp([listing], docs={CARREFOUR: gallery_page(TITLE, [TWIN, SINGLE])},
                        bodies={"https://x.ae/broken.jpg": "http_404", TWIN: packshot_png(3), SINGLE: packshot_png(5)},
                        readings={TWIN: READ_TWIN, SINGLE: READ_SINGLE})
    single = ranked_of(outcome, SINGLE)
    assert single is not None and single.candidate.page_gallery and single.fetched.ok and single.verdict is None
    assert [c for c in d["verifier"].calls if SINGLE in c] == []
    assert outcome.winner is None or outcome.winner.candidate.image_url != SINGLE


def test_under_a_replay_cassette_an_unrecorded_gallery_image_is_left_out(monkeypatch):
    class Replay:
        mode = "replay"

        def __init__(self, held):
            self.held = set(held)

        def holds_download(self, url):
            return url in self.held

    listing = cand(TWIN, f"{TITLE} | Carrefour UAE", CARREFOUR)
    args = dict(docs={CARREFOUR: gallery_page(TITLE, [TWIN, SINGLE, BACK])},
                bodies={TWIN: packshot_png(3), SINGLE: packshot_png(5), BACK: packshot_png(7)},
                readings={TWIN: READ_TWIN, SINGLE: READ_SINGLE, BACK: READ_BACK})
    monkeypatch.setattr(cassette, "_STATE", Replay([]))
    outcome, d = run_gp([listing], **args)                                # no error: simply no gallery
    assert ranked_of(outcome, SINGLE) is None and ranked_of(outcome, BACK) is None
    assert outcome.winner is None
    monkeypatch.setattr(cassette, "_STATE", Replay([SINGLE]))
    outcome, d = run_gp([listing], **args)
    assert outcome.winner.candidate.image_url == SINGLE and ranked_of(outcome, BACK) is None


def test_a_real_replay_cassette_answers_can_download_without_a_miss(tmp_path, monkeypatch):
    from test_cm_cassette import URLS, Session, _download_world, _fetch_all

    from catalog_match.fetch import HttpFetcher

    assert cassette.can_download("https://cdn.example.ae/anything.jpg")      # no cassette
    monkeypatch.setattr("catalog_match.settings.proxy_url", lambda: "")
    folder = str(tmp_path / "cas")
    cassette.install("record", folder)
    try:
        with cassette.row(3):
            _fetch_all(HttpFetcher(store_dir=str(tmp_path / "a"), session=Session(_download_world())), [URLS["ok"]])
    finally:
        cassette.uninstall()
    cas = cassette.install("replay", folder)
    try:
        with cassette.row(4):
            assert cassette.can_download(URLS["ok"]) is True
            assert cassette.can_download("https://cdn.example.ae/never.jpg") is False
        assert cas.row_report(4)["misses"] == []
    finally:
        cassette.uninstall()


def test_the_review_screen_marks_a_gallery_image():
    js = Path(__file__).resolve().parents[2] / "dashboard" / "public" / "js" / "review"
    core = (js / "core.js").read_text(encoding="utf-8")
    single = (js / "single.js").read_text(encoding="utf-8")
    assert "const GALLERY_NOTE = 'صورة ثانية من معرض صفحة المتجر';" in core and "ev.page_gallery === true" in core
    assert single.count("R.galleryNote(") == 4                    # the selected card and every other image
