"""catalog_match.local_index: the local catalog index of UAE store product pages (free retrieval).

The index here is MemoryCatalogStore (DbCatalogStore runs the same interface against MariaDB in
tests/test_catalog_index_db.py); product pages are read by a fake PageFetcher. Score, ranking,
retrieval and routing are the real code. Sockets are blocked.
"""

import io
import socket
import threading

import pytest
from PIL import Image, ImageDraw

from catalog_match import decide, local_index, pipeline, settings
from catalog_match.fetch import phash_hex
from catalog_match.identity import build_sku_spec
from catalog_match.local_index import (CatalogRow, LocalIndexProvider, MemoryCatalogStore, PageRecord, clean_url,
                                       index_keys, rank_rows, search_keys, url_hash)
from catalog_match.models import Candidate, FetchedImage, ProviderHealth, ProviderResult, VerificationResult
from catalog_match.pages import PageImage, PageInfo
from catalog_match.providers import default_providers
from catalog_match.retrieve import retrieve, t1_early_stop
from catalog_match.verify import make_verdict


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    def refuse(*args, **kwargs):
        raise RuntimeError("network access is blocked in catalog_match unit tests")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    monkeypatch.setattr(settings, "_config", None)
    for name in ("LOCAL_INDEX_ENABLED", "LOCAL_INDEX_MAX_PAGES", "LOCAL_INDEX_PAGE_TTL_DAYS", "SERPER_API_KEY",
                 "AUTO_PUBLISH_ENABLED", "AUTO_PUBLISH_BRANDS"):
        monkeypatch.delenv(name, raising=False)
    local_index.reset_blocked_hosts()
    yield
    local_index.reset_blocked_hosts()


LULU = "https://gcc.luluhypermarket.com/en-ae/{}/p/{}"
CARREFOUR = "https://www.carrefouruae.com/mafuae/en/frozen-bread/{}/p/{}"
PARATHA = {"name": "ASHOKA PLAIN PARATHA 5S 400GM", "brand": "ASHOKA"}


def spec_for(row=None, mappings=None):
    return build_sku_spec(dict(row or PARATHA), mappings or {})


def index(*entries):
    """A MemoryCatalogStore holding (store, url) entries, in order."""
    store = MemoryCatalogStore()
    for key, url in entries:
        store.upsert(key, [(url, None)])
    return store


class FakeFetcher:
    """pages.PageFetcher: url -> PageInfo built from `pages` ({url: dict(name=..., image=..., gtins=...)})."""

    def __init__(self, pages=None, errors=None, redirects=None):
        self.pages = dict(pages or {})
        self.errors = dict(errors or {})
        self.redirects = dict(redirects or {})
        self.calls = []
        self.lock = threading.Lock()

    def fetch_page(self, url, referer=""):
        with self.lock:
            self.calls.append(url)
        if url in self.errors:
            return PageInfo(url=url, ok=False, error=self.errors[url])
        if url in self.redirects:
            return PageInfo(url=self.redirects[url], ok=True, name="Frozen Bread", images=[
                PageImage(url="https://cdn.example.com/category-banner.jpg", width=1200, height=400, source="og")],
                sources=["og"], redirected_from=url)
        page = self.pages.get(url)
        if page is None:
            return PageInfo(url=url, ok=False, error="http_404")
        image = page.get("image", url.rstrip("/").replace("/p/", "/img/") + ".jpg")
        return PageInfo(url=url, ok=True, name=page.get("name", ""), brand=page.get("brand", ""),
                        images=[PageImage(url=image, width=1000, height=1000, source="jsonld")] if image else [],
                        gtins=list(page.get("gtins", [])), sources=["jsonld"])


def provider(store, fetcher, **kw):
    kw.setdefault("max_pages", 3)
    kw.setdefault("page_ttl_days", 30)
    return LocalIndexProvider(store=store, fetcher=fetcher, **kw)


# ---------------------------------------------------------------------------
# Words, URLs
# ---------------------------------------------------------------------------

def test_index_keys_fold_plurals_and_drop_sizes():
    assert index_keys("Kitchen Treasures Meat Masala 160 g") == ["kitchen", "treasure", "meat", "masala"]
    assert index_keys("Swiss Miss 5x40g") == ["swiss", "miss"]           # 'ss' is not a plural
    assert index_keys("Lay's Chips") == ["lay", "chip"]                  # possessive folded, then the plural 's'
    assert index_keys("المراعي حليب") == ["مراعي", "حليب"]                  # Arabic: the article stripped
    assert index_keys("7up 330ml") == ["up", "ml"]


def test_urls_are_one_product_whatever_the_query_or_slash():
    a = "https://gcc.luluhypermarket.com/en-ae/aida-french-fries-1-kg/p/2072326"
    assert url_hash(a) == url_hash("https://GCC.luluhypermarket.com/en-ae/aida-french-fries-1-kg/p/2072326/")
    assert url_hash(a) == url_hash(clean_url(a + "?srsltid=AU7gw4X0#reviews"))
    assert clean_url(a + "?srsltid=AU7gw4X0#reviews") == a
    assert url_hash("https://www.spinneys.com/en-ae/catalogue/x_1/") == url_hash("https://spinneys.com/en-ae/catalogue/x_1")


def test_search_keys_need_the_brand_words_and_rank_by_product_words():
    groups, extra = search_keys(spec_for())
    assert groups == [["ashoka"]]
    assert extra[:2] == ["plain", "paratha"] and "gm" not in extra     # unit words are size evidence, not words
    mappings = {"al alali": {"brand": "Al Alali", "synonyms": ["Alali"]}}
    groups, _ = search_keys(spec_for({"name": "AL ALALI TUNA WHITE MEAT IN WATER 170G", "brand": "AL ALALI"},
                                     mappings))
    assert ["alali"] in groups                                           # 'al' alone would match half the index


def test_memory_store_finds_rows_with_every_brand_word_most_words_first():
    store = index(("lulu", LULU.format("ashoka-garlic-naan-400-g", 1)),
                  ("lulu", LULU.format("ashoka-plain-paratha-400-g", 2)),
                  ("lulu", LULU.format("kawan-plain-paratha-400-g", 3)))
    rows = store.find(["ashoka"], ["plain", "paratha"])
    assert [r.id for r in rows] == [2, 1] and [r.hits for r in rows] == [3, 1]
    assert store.find(["nonexistent"], ["paratha"]) == []
    assert store.upsert("lulu", [(LULU.format("ashoka-plain-paratha-400-g", 2) + "?utm=x", None)]) == 0


# ---------------------------------------------------------------------------
# Choosing the pages worth reading
# ---------------------------------------------------------------------------

def test_rows_of_another_product_size_or_brand_are_never_read():
    spec = spec_for()
    rows = [CatalogRow(1, "lulu", LULU.format("ashoka-plain-paratha-800-g", 1), hits=3),     # another size
            CatalogRow(2, "lulu", LULU.format("ashoka-garlic-naan-400-g", 2), hits=1),        # another product
            CatalogRow(3, "lulu", LULU.format("kawan-plain-paratha-400-g", 3), hits=2),       # another brand
            CatalogRow(4, "lulu", LULU.format("ashoka-plain-paratha-400-g", 4), hits=3)]
    assert [r.id for r, _ in rank_rows(spec, rows, 3)] == [4]


def test_one_page_per_store_before_a_second_page_of_any_store():
    spec = spec_for()
    rows = [CatalogRow(1, "lulu", LULU.format("ashoka-plain-paratha-400-g", 1), hits=3),
            CatalogRow(2, "lulu", LULU.format("ashoka-plain-paratha-400g-5-pcs", 2), hits=3),
            CatalogRow(3, "carrefour_uae", CARREFOUR.format("ashoka-plain-paratha-400g", 3), hits=3)]
    picked = [r.store for r, _ in rank_rows(spec, rows, 3)]
    assert picked[:2] in (["lulu", "carrefour_uae"], ["carrefour_uae", "lulu"]) and picked[2] == "lulu"
    assert len(rank_rows(spec, rows, 1)) == 1


# ---------------------------------------------------------------------------
# The provider
# ---------------------------------------------------------------------------

def test_lookup_reads_the_best_pages_and_returns_unsanctioned_index_candidates():
    paratha = LULU.format("ashoka-plain-paratha-400-g", 2)
    store = index(("lulu", paratha), ("lulu", LULU.format("ashoka-plain-paratha-800-g", 3)))
    fetcher = FakeFetcher({paratha: {"name": "Ashoka Plain Paratha 400 g", "gtins": ["08906008560022"]}})
    res = provider(store, fetcher).lookup(spec_for())
    assert res.status == "ok" and fetcher.calls == [paratha]           # the 800 g page is never read
    (c,) = res.candidates
    assert (c.provider, c.query_id, c.sanctioned) == ("local_index", "IDX", False)
    assert LocalIndexProvider.sanctioned is False      # the retriever also clears sanctioned for an unsanctioned provider
    assert c.page_url == paratha and c.page_title == "Ashoka Plain Paratha 400 g"
    assert c.gtin_on_page == "08906008560022" and (c.width, c.height) == (1000, 1000)
    row = store.rows[1]
    assert (row.page_status, row.image_url, row.gtin) == ("ok", c.image_url, "08906008560022")


def test_a_page_read_once_is_reused_until_its_ttl_then_read_again():
    paratha = LULU.format("ashoka-plain-paratha-400-g", 2)
    store = index(("lulu", paratha))
    now = [1_000_000.0]
    store.clock = lambda: now[0]
    fetcher = FakeFetcher({paratha: {"name": "Ashoka Plain Paratha 400 g"}})
    p = provider(store, fetcher, page_ttl_days=30)
    first = p.lookup(spec_for())
    now[0] += 29 * 86400
    second = p.lookup(spec_for())
    assert len(fetcher.calls) == 1 and second.candidates == first.candidates
    now[0] += 2 * 86400
    p.lookup(spec_for())
    assert len(fetcher.calls) == 2


def test_redirected_and_failed_pages_give_nothing_and_are_remembered():
    gone = LULU.format("ashoka-plain-paratha-400-g", 2)
    flaky = CARREFOUR.format("ashoka-plain-paratha-400g", 3)
    store = index(("lulu", gone), ("carrefour_uae", flaky))
    now = [1_000_000.0]
    store.clock = lambda: now[0]
    fetcher = FakeFetcher(redirects={gone: "https://gcc.luluhypermarket.com/en-ae/frozen/c/1"},
                          errors={flaky: "timeout"})
    p = provider(store, fetcher)
    res = p.lookup(spec_for())
    assert res.status == "empty" and res.candidates == []              # never the category page's banner
    assert sorted(fetcher.calls) == sorted([gone, flaky])
    assert {r.store: r.page_status for r in store.rows.values()} == {"lulu": "redirected", "carrefour_uae": "timeout"}
    now[0] += 2 * 3600
    p.lookup(spec_for())
    assert len(fetcher.calls) == 2                                       # both remembered
    now[0] += local_index.FAILED_PAGE_TTL_H * 3600
    p.lookup(spec_for())
    assert fetcher.calls[2:] == [flaky]                                  # a timeout is retried the next day


def test_a_store_that_keeps_refusing_is_left_alone_for_the_run():
    urls = [CARREFOUR.format(f"ashoka-plain-paratha-400g-{i}", i) for i in range(1, 6)]
    fetcher = FakeFetcher(errors={u: "http_403" for u in urls})
    for u in urls:
        provider(index(("carrefour_uae", u)), fetcher, max_pages=1).lookup(spec_for())
    assert len(fetcher.calls) == local_index.BLOCKED_HOST_LIMIT


def test_a_row_whose_page_stated_the_gtin_is_found_by_the_gtin():
    url = LULU.format("plain-paratha-frozen-400-g", 9)                  # the slug leaves out the brand
    store = index(("lulu", url))
    store.save_page(1, PageRecord(status="ok", page_title="Ashoka Plain Paratha 400g", image_url="https://x/i.jpg",
                                  width=900, height=900, gtin="08906008560022"))
    spec = spec_for(dict(PARATHA, barcode="8906008560022"))
    res = provider(store, FakeFetcher()).lookup(spec)
    assert [c.page_url for c in res.candidates] == [url]


def test_nothing_in_the_index_is_an_empty_answer_without_any_read():
    fetcher = FakeFetcher()
    res = provider(index(("lulu", LULU.format("kawan-plain-paratha-400-g", 1))), fetcher).lookup(spec_for())
    assert res.status == "empty" and fetcher.calls == []


def test_create_needs_the_setting_pages_and_rows(monkeypatch):
    rows = index(("lulu", LULU.format("ashoka-plain-paratha-400-g", 1)))
    assert LocalIndexProvider.create(store=MemoryCatalogStore()) is None          # empty index
    assert isinstance(LocalIndexProvider.create(store=rows), LocalIndexProvider)
    monkeypatch.setenv("LOCAL_INDEX_MAX_PAGES", "0")
    assert LocalIndexProvider.create(store=rows) is None
    monkeypatch.setenv("LOCAL_INDEX_MAX_PAGES", "3")
    monkeypatch.setenv("LOCAL_INDEX_ENABLED", "false")
    assert LocalIndexProvider.create(store=rows) is None


def test_default_providers_put_the_index_first_only_when_it_has_rows(monkeypatch):
    monkeypatch.setattr(local_index.DbCatalogStore, "count", lambda self, fresh=False: 0)
    assert "local_index" not in [p.name for p in default_providers()]
    monkeypatch.setattr(local_index.DbCatalogStore, "count", lambda self, fresh=False: 12)
    assert [p.name for p in default_providers()][:2] == ["local_index", "off"]


def test_the_database_store_reports_an_unreachable_index_as_empty():
    def broken():
        raise OSError("connection refused")

    assert local_index.DbCatalogStore(connect=broken).count(fresh=True) == 0


# ---------------------------------------------------------------------------
# Retrieval and routing
# ---------------------------------------------------------------------------

class SearchStub:
    kind = "search"
    fallback = False
    sanctioned = True

    def __init__(self, name, cands=()):
        self.name, self.cands, self.calls = name, list(cands), []

    def search(self, query, hl, spec):
        self.calls.append(query)
        return ProviderResult(provider=self.name, status="ok" if self.cands else "empty", candidates=list(self.cands))


class OffStub(SearchStub):
    kind = "lookup"

    def lookup(self, spec):
        self.calls.append("lookup")
        return ProviderResult(provider=self.name, status="empty")


def test_the_index_runs_without_a_gtin_and_never_stops_the_web_search():
    paratha = LULU.format("ashoka-plain-paratha-400-g", 2)
    fetcher = FakeFetcher({paratha: {"name": "Ashoka Plain Paratha 5 pcs 400 g"}})
    idx = provider(index(("lulu", paratha)), fetcher)
    off = OffStub("off")
    serper = SearchStub("serper")
    spec = spec_for()
    res = retrieve(spec, [idx, off, serper], max_queries=3, early_stop=t1_early_stop(spec))
    assert off.calls == []                                              # the GTIN lookup still needs a GTIN
    assert [c.provider for c in res.pool] == ["local_index"]
    assert [(h.provider, h.query_id) for h in res.health if h.provider == "local_index"] == [("local_index", "IDX")]
    assert len(serper.calls) >= 2, "a tier-1 index page must not stop the web search"


def test_an_index_answer_never_hides_a_search_outage():
    health = [ProviderHealth("local_index", "ok", query_id="IDX"), ProviderHealth("serper", "quota", query_id="Q1")]
    assert decide.providers_down(health) is True
    assert decide.main_query_down(health) is True


def _packshot(seed=3):
    img = Image.new("RGB", (800, 800), (255, 255, 255))
    ImageDraw.Draw(img).rectangle([200, 100, 600, 700], fill=(40 + seed * 20, 90, 160))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


class ImageFetcher:
    def __init__(self, bodies):
        self.bodies = bodies

    def fetch(self, cands, spec):
        out = []
        for c in cands:
            body = self.bodies.get(c.image_url)
            if body is None:
                out.append(FetchedImage(candidate=c, ok=False, error="http_404"))
                continue
            with Image.open(io.BytesIO(body)) as im:
                out.append(FetchedImage(candidate=c, ok=True, content_sha256="0" * 64, width=im.width,
                                        height=im.height, path_or_bytes=body, phash=phash_hex(im)))
        return out


class ReadsMatch:
    def verify(self, spec, images):
        reading = {"brand_text": "Ashoka", "variant_text": "Plain Paratha", "size_text": "400 g",
                   "view": "front_packshot", "brand_match": "yes", "variant_match": "yes", "size_match": "yes"}
        return VerificationResult(status="ok", calls=1,
                                  verdicts=[make_verdict(spec, i, reading) for i, _ in enumerate(images)])


def test_an_index_page_can_be_preselected_for_review_but_never_auto_published(monkeypatch):
    monkeypatch.setenv("AUTO_PUBLISH_ENABLED", "true")
    monkeypatch.setenv("AUTO_PUBLISH_BRANDS", "*")
    paratha = LULU.format("ashoka-plain-paratha-400-g", 2)
    image = "https://gcc.luluhypermarket.com/medias/ashoka-paratha.jpg"
    fetcher = FakeFetcher({paratha: {"name": "Ashoka Plain Paratha 5 pcs 400 g", "image": image}})
    outcome = pipeline.find_product_image(
        spec_for(), providers=[provider(index(("lulu", paratha)), fetcher), SearchStub("serper")],
        fetcher=ImageFetcher({image: _packshot()}), verifier=ReadsMatch(), expansion=False)
    assert outcome.decision == "REVIEW_PRESELECTED"
    assert outcome.winner.candidate.image_url == image
    assert outcome.winner.candidate.provider == "local_index" and outcome.winner.candidate.sanctioned is False


# ---------------------------------------------------------------------------
# Review 3: what the index may never change about the web search
# ---------------------------------------------------------------------------

def test_a_web_hit_of_the_same_image_never_sanctions_what_the_index_page_said(monkeypatch):
    # Serper alone: 'Ashoka Frozen Paratha' is tier 2 (review). The index page of the same image states the
    # pieces and the size; that scraped text may pre-check the image, never make it auto-publishable, and the
    # entry never stops the web search early.
    monkeypatch.setenv("AUTO_PUBLISH_ENABLED", "true")
    monkeypatch.setenv("AUTO_PUBLISH_BRANDS", "*")
    paratha = LULU.format("ashoka-plain-paratha-400-g", 2)
    image = "https://gcc.luluhypermarket.com/medias/ashoka-paratha.jpg"
    fetcher = FakeFetcher({paratha: {"name": "Ashoka Plain Paratha 5 pcs 400 g", "image": image}})
    web = Candidate(image_url=image, page_url=paratha, title="Ashoka Frozen Paratha", page_title="Ashoka Frozen Paratha",
                    provider="serper", rank=1)
    serper = SearchStub("serper", [web])
    outcome = pipeline.find_product_image(
        spec_for(), providers=[provider(index(("lulu", paratha)), fetcher), serper],
        fetcher=ImageFetcher({image: _packshot()}), verifier=ReadsMatch(), expansion=False)
    assert outcome.decision == "REVIEW_PRESELECTED"
    assert outcome.winner.candidate.sanctioned is False and outcome.winner.candidate.provider == "local_index"
    assert len(serper.calls) >= 2                                       # no early stop on scraped evidence


class DownStub(SearchStub):
    def search(self, query, hl, spec):
        self.calls.append(query)
        return ProviderResult(provider=self.name, status="quota", error="quota")


def test_a_search_outage_is_never_hidden_by_an_index_hit():
    paratha = LULU.format("ashoka-plain-paratha-400-g", 2)
    image = "https://gcc.luluhypermarket.com/medias/ashoka-paratha.jpg"
    fetcher = FakeFetcher({paratha: {"name": "Ashoka Plain Paratha 5 pcs 400 g", "image": image}})
    for images in ({image: _packshot()}, {}):                           # read fine, or its download failed
        outcome = pipeline.find_product_image(
            spec_for(), providers=[provider(index(("lulu", paratha)), fetcher), DownStub("serper")],
            fetcher=ImageFetcher(images), verifier=ReadsMatch(), expansion=False)
        assert (outcome.decision, outcome.failure_code) == ("PROVIDER_DOWN", "PROVIDER_DOWN")
    # no web search provider at all: nothing searched the web
    assert decide.providers_down([ProviderHealth("local_index", "empty", query_id="IDX")]) is True


def test_an_index_hit_never_cancels_the_relaxed_queries():
    paratha = LULU.format("ashoka-plain-paratha-400-g", 2)
    fetcher = FakeFetcher({paratha: {"name": "Ashoka Plain Paratha 5 pcs 400 g"}})
    serper = SearchStub("serper")                                        # the web finds nothing
    pipeline.find_product_image(spec_for(), providers=[provider(index(("lulu", paratha)), fetcher), serper],
                                fetcher=ImageFetcher({}), verifier=ReadsMatch(), expansion=False)
    alone = SearchStub("serper")
    pipeline.find_product_image(spec_for(), providers=[alone], fetcher=ImageFetcher({}), verifier=ReadsMatch(),
                                expansion=False)
    assert len(serper.calls) == len(alone.calls) >= 3                    # the relaxations ran as without the index


TALABAT = "https://www.talabat.com/uae/grocery/600123/frozen/{}"
SPINNEYS = "https://www.spinneys.com/en-ae/catalogue/{}/"


def test_pages_known_to_be_dead_never_take_the_read_slots():
    lulu, carrefour = LULU.format("ashoka-plain-paratha-400-g", 1), CARREFOUR.format("ashoka-plain-paratha-400g", 2)
    talabat, spinneys = TALABAT.format("ashoka-plain-paratha-400g"), SPINNEYS.format("ashoka-plain-paratha-400g_3")
    store = index(("lulu", lulu), ("carrefour_uae", carrefour), ("talabat", talabat), ("spinneys", spinneys))
    now = [1_000_000.0]
    store.clock = lambda: now[0]
    fetcher = FakeFetcher({spinneys: {"name": "Ashoka Plain Paratha 400 g"}},
                          errors={carrefour: "http_404", talabat: "http_410"},
                          redirects={lulu: "https://gcc.luluhypermarket.com/en-ae/frozen/c/1"})
    p = provider(store, fetcher, max_pages=3)
    first = p.lookup(spec_for())
    read_first = list(fetcher.calls)
    assert len(read_first) == 3
    now[0] += 86400                                                     # the next day: the dead rows are remembered
    later = p.lookup(spec_for())
    found = first.candidates or later.candidates
    assert [c.page_url for c in found] == [spinneys]
    assert spinneys in fetcher.calls and len(set(fetcher.calls)) == 4 and len(fetcher.calls) == 4


class SlowFetcher(FakeFetcher):
    def __init__(self, slow, delay, **kw):
        super().__init__(**kw)
        self.slow, self.delay, self.done = slow, delay, threading.Event()

    def fetch_page(self, url, referer=""):
        if url == self.slow:
            import time
            time.sleep(self.delay)
            info = super().fetch_page(url, referer)
            self.done.set()
            return info
        return super().fetch_page(url, referer)


def test_the_page_reads_never_hold_the_search_past_the_deadline(monkeypatch):
    import time
    monkeypatch.setattr(local_index, "READ_DEADLINE_S", 0.2)
    fast, slow = LULU.format("ashoka-plain-paratha-400-g", 1), CARREFOUR.format("ashoka-plain-paratha-400g", 2)
    store = index(("lulu", fast), ("carrefour_uae", slow))
    fetcher = SlowFetcher(slow, 1.0, pages={fast: {"name": "Ashoka Plain Paratha 400 g"},
                                            slow: {"name": "Ashoka Plain Paratha 400 g"}})
    started = time.monotonic()
    res = provider(store, fetcher).lookup(spec_for())
    assert time.monotonic() - started < 0.9
    assert [c.page_url for c in res.candidates] == [fast]
    assert fetcher.done.wait(3)                                         # the slow read still finishes...
    for _ in range(50):
        if store.rows[2].page_status:
            break
        time.sleep(0.02)
    assert store.rows[2].page_status == "ok"                            # ...and is remembered for the next run


def test_a_store_that_keeps_timing_out_is_left_alone_for_the_run():
    urls = [CARREFOUR.format(f"ashoka-plain-paratha-400g-{i}", i) for i in range(1, 6)]
    fetcher = FakeFetcher(errors={u: "timeout" for u in urls})
    for u in urls:
        provider(index(("carrefour_uae", u)), fetcher, max_pages=1).lookup(spec_for())
    assert len(fetcher.calls) == local_index.BLOCKED_HOST_LIMIT


def test_a_transient_failure_keeps_what_an_earlier_read_found():
    url = LULU.format("ashoka-plain-paratha-400-g", 1)
    store = index(("lulu", url))
    store.save_page(1, PageRecord(status="ok", page_title="Ashoka Plain Paratha 400g", image_url="https://x/i.jpg",
                                  width=900, height=900, gtin="08906008560022"))
    store.save_page(1, PageRecord(status="connection_error"))
    row = store.rows[1]
    assert (row.page_status, row.image_url, row.image_width, row.gtin) == ("connection_error", "https://x/i.jpg", 900,
                                                                           "08906008560022")
    assert [r.id for r in store.by_gtin("08906008560022")] == [1]
