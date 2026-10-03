"""catalog_match.sitemaps and scripts/build_catalog_index.py: reading the stores' published sitemaps.

The HTTP client is a fake (url -> status, body); waits go to a list instead of sleeping. A store
that refuses (401 / 403 / 429, a bot-check page, a robots.txt 5xx) is skipped, never worked around.
"""

import gzip
import json
import os
import socket
import sys

import pytest

from catalog_match import sitemaps
from catalog_match.local_index import MemoryCatalogStore
from catalog_match.sitemaps import (SitemapError, SitemapHarvester, decompress, enabled_stores, load_stores,
                                    parse_robots, parse_sitemap)

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import build_catalog_index  # noqa: E402


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def _blocked(*args, **kwargs):
        raise RuntimeError("network access is blocked in catalog_match unit tests")

    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)
    monkeypatch.setattr(socket, "getaddrinfo", _blocked)


BASE = "https://gcc.luluhypermarket.com"
NS = 'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"'


def urlset(*locs):
    body = "".join(f"<url><loc>{loc}</loc><lastmod>2026-09-0{i % 9 + 1}</lastmod></url>" for i, loc in enumerate(locs))
    return f'<?xml version="1.0" encoding="UTF-8"?><urlset {NS}>{body}</urlset>'.encode()


def index_of(*locs):
    body = "".join(f"<sitemap><loc>{loc}</loc></sitemap>" for loc in locs)
    return f'<?xml version="1.0" encoding="UTF-8"?><sitemapindex {NS}>{body}</sitemapindex>'.encode()


class Resp:
    def __init__(self, status, body=b""):
        self.status_code, self.body, self.closed = status, body, False

    def iter_content(self, chunk_size):
        for i in range(0, len(self.body), chunk_size):
            yield self.body[i:i + chunk_size]

    def close(self):
        self.closed = True


class FakeHttp:
    def __init__(self, pages):
        self.pages = dict(pages)
        self.calls = []
        self.headers = []

    def get(self, url, headers=None, **kwargs):
        self.calls.append(url)
        self.headers.append(dict(headers or {}))
        assert "proxies" not in kwargs, "a sitemap is never fetched through a proxy"
        status, body = self.pages.get(url, (404, b""))
        if isinstance(status, Exception):
            raise status
        return Resp(status, body)


def lulu():
    return next(s for s in load_stores() if s.key == "lulu")


def harvester(pages, waits=None):
    waits = waits if waits is not None else []
    return SitemapHarvester(http=FakeHttp(pages), sleep=waits.append, clock=lambda: 0.0)


PRODUCT_1 = BASE + "/en-ae/ashoka-plain-paratha-400-g/p/2072326"
PRODUCT_2 = BASE + "/en-ae/al-alali-fancy-meat-tuna-in-water-170-g/p/14751"
ROBOTS = (b"User-agent: *\nDisallow: /private/\nCrawl-delay: 2\n"
          b"Sitemap: https://gcc.luluhypermarket.com/sitemaps/index.xml\n")


def full_store():
    return {
        BASE + "/robots.txt": (200, ROBOTS),
        BASE + "/sitemaps/index.xml": (200, index_of(BASE + "/sitemaps/products-1.xml.gz",
                                                     BASE + "/sitemaps/categories.xml",
                                                     BASE + "/private/hidden.xml",
                                                     "https://cdn.other.example/elsewhere.xml")),
        BASE + "/sitemaps/products-1.xml.gz": (200, gzip.compress(urlset(PRODUCT_1, PRODUCT_2,
                                                                         BASE + "/ar-ae/x/p/3"))),
        BASE + "/sitemaps/categories.xml": (200, urlset(BASE + "/en-ae/frozen/c/9")),
    }


# ---------------------------------------------------------------------------
# Stores file
# ---------------------------------------------------------------------------

LIVE_PRODUCT_PAGES = [   # product pages the live runs of 2026-10-03 found
    ("lulu", "https://gcc.luluhypermarket.com/en-ae/aida-french-fries-1-kg/p/2072326"),
    ("carrefour_uae", "https://www.carrefouruae.com/mafuae/en/beef-luncheon/zwan-beef-luncheon-meat-850gr/p/10937"),
    ("spinneys", "https://www.spinneys.com/en-ae/catalogue/mccain-golden-long-french-fries-750g_22359/"),
    ("talabat_mart_uae", "https://www.talabat.com/uae/talabat-mart/product/ashoka-plain-paratha-400-g/s/905099"),
    ("noon_uae", "https://www.noon.com/uae-en/fancy-meat-tuna-in-water-85grams/N12277957A/p/"),
    ("noon_uae", "https://supermall.noon.com/uae-en/fancy-meat-tuna-in-water-170grams/N12277975A/p/"),
    ("unioncoop", "https://www.unioncoop.ae/skipjack-tuna-chunks-in-brine-13600527.html"),
]
NOT_PRODUCT_PAGES = [
    ("lulu", "https://gcc.luluhypermarket.com/en-ae/frozen-food/c/1001"),
    ("carrefour_uae", "https://www.carrefourksa.com/mafsau/en/french-fries/sunbulah-french-fries-7mm-1kg/p/26393"),
    ("carrefour_uae", "https://www.carrefouruae.com/mafuae/ar/beef-luncheon/zwan/p/10937"),
    ("talabat_mart_uae", "https://www.talabat.com/bahrain/talabat-mart/product/goody-tuna-185g/s/908043"),
    ("unioncoop", "https://www.unioncoop.ae/about-us.html"),
]


@pytest.mark.parametrize("key,url", LIVE_PRODUCT_PAGES)
def test_the_shipped_patterns_match_the_product_pages_the_live_runs_found(key, url):
    store = {s.key: s for s in load_stores()}[key]
    assert store.is_product(url)


@pytest.mark.parametrize("key,url", NOT_PRODUCT_PAGES)
def test_categories_other_countries_and_other_languages_are_not_product_pages(key, url):
    store = {s.key: s for s in load_stores()}[key]
    assert not store.is_product(url)


def test_stores_whose_slugs_leave_out_the_brand_are_off_unless_asked_for():
    stores = load_stores()
    assert [s.key for s in enabled_stores(stores)] == ["lulu", "carrefour_uae", "spinneys", "talabat_mart_uae"]
    assert [s.key for s in enabled_stores(stores, ["noon_uae"])] == ["noon_uae"]
    with pytest.raises(KeyError):
        enabled_stores(stores, ["lulu", "nope"])


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def test_parse_sitemap_reads_locations_and_lastmod_of_both_kinds():
    kind, entries = parse_sitemap(urlset(PRODUCT_1, PRODUCT_2))
    assert kind == "urlset" and entries == [(PRODUCT_1, "2026-09-01"), (PRODUCT_2, "2026-09-02")]
    kind, entries = parse_sitemap(index_of(BASE + "/a.xml"))
    assert kind == "index" and entries == [(BASE + "/a.xml", None)]


@pytest.mark.parametrize("body,why", [
    (b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><urlset><url><loc>&a;</loc></url></urlset>', "doctype"),
    (b"<!DOCTYPE html><html><body>Checking your browser</body></html>", "html"),
    (b"<html><head><title>Access denied</title></head></html>", "html"),
    (b"<rss><channel/></rss>", "not a sitemap"),
    (b"<urlset><url><loc>x</loc>", "bad xml"),
])
def test_documents_that_are_not_plain_sitemaps_are_refused(body, why):
    with pytest.raises(SitemapError, match=why):
        parse_sitemap(body)


def test_gzip_is_decompressed_within_a_limit():
    data = urlset(PRODUCT_1)
    assert decompress(gzip.compress(data)) == data
    assert decompress(data) == data
    with pytest.raises(SitemapError, match="too_large"):
        decompress(gzip.compress(b"<urlset>" + b" " * 5000 + b"</urlset>"), limit=1000)
    with pytest.raises(SitemapError, match="bad gzip"):
        decompress(b"\x1f\x8b" + b"broken")


def test_robots_rules_sitemaps_and_crawl_delay():
    robots = parse_robots(ROBOTS.decode())
    assert robots.sitemaps == [BASE + "/sitemaps/index.xml"]
    assert robots.crawl_delay == 2.0
    assert robots.allowed(BASE + "/sitemaps/index.xml") and not robots.allowed(BASE + "/private/x.xml")


# ---------------------------------------------------------------------------
# Harvest
# ---------------------------------------------------------------------------

def test_harvest_follows_robots_and_hands_over_only_product_pages():
    waits, got = [], []
    h = harvester(full_store(), waits)
    rep = h.harvest(lulu(), on_urls=lambda batch: (got.extend(batch), len(batch))[1])
    assert rep.status == "ok" and rep.robots == "ok (http 200)"
    assert got == [(PRODUCT_1, "2026-09-01"), (PRODUCT_2, "2026-09-02")]
    assert (rep.sitemaps_read, rep.urls_seen, rep.product_urls, rep.new_urls) == (3, 4, 2, 2)
    assert dict((why, url) for url, why in rep.skipped) == {
        "disallowed by robots.txt": BASE + "/private/hidden.xml", "outside the store": "https://cdn.other.example/elsewhere.xml"}
    assert BASE + "/private/hidden.xml" not in h.http.calls
    assert waits == [2.0, 2.0, 2.0]                                      # the store's Crawl-delay between requests
    assert all(hd["User-Agent"] == sitemaps.USER_AGENT for hd in h.http.headers)   # says who it is


@pytest.mark.parametrize("status", [401, 403, 429])
def test_a_store_that_refuses_robots_is_skipped_at_once(status):
    h = harvester({BASE + "/robots.txt": (status, b"")})
    rep = h.harvest(lulu())
    assert rep.status == "blocked" and h.http.calls == [BASE + "/robots.txt"]


def test_a_refusal_on_a_sitemap_stops_the_store():
    pages = full_store()
    pages[BASE + "/sitemaps/products-1.xml.gz"] = (403, b"")
    got = []
    rep = harvester(pages).harvest(lulu(), on_urls=got.extend)
    assert rep.status == "blocked" and "http_403" in rep.error and got == []


def test_a_bot_check_page_instead_of_a_sitemap_stops_the_store():
    pages = {BASE + "/robots.txt": (404, b""), BASE + "/sitemap.xml": (200, b"<!DOCTYPE html><html>captcha</html>")}
    h = harvester(pages)
    rep = h.harvest(lulu())
    assert rep.status == "blocked" and "bot check" in rep.error
    assert h.http.calls == [BASE + "/robots.txt", BASE + "/sitemap.xml"]


def test_robots_server_error_means_disallowed_and_no_answer_is_an_error():
    rep = harvester({BASE + "/robots.txt": (503, b"")}).harvest(lulu())
    assert rep.status == "error"
    rep = harvester({BASE + "/robots.txt": (OSError("refused"), b"")}).harvest(lulu())
    assert rep.status == "error"


def test_without_robots_the_usual_sitemap_locations_are_tried():
    pages = {BASE + "/robots.txt": (404, b""), BASE + "/sitemap_index.xml": (200, urlset(PRODUCT_1))}
    rep = harvester(pages).harvest(lulu())
    assert rep.started_from == [BASE + "/sitemap.xml", BASE + "/sitemap_index.xml"]
    assert rep.status == "partial" and rep.product_urls == 1        # /sitemap.xml answered 404
    rep = harvester({BASE + "/robots.txt": (404, b"")}).harvest(lulu())
    assert rep.status == "error" and rep.sitemaps_read == 0


def test_limits_stop_early_and_mark_the_harvest_partial():
    got = []
    rep = harvester(full_store()).harvest(lulu(), on_urls=got.extend, max_urls=1)
    assert rep.truncated and rep.status == "partial" and got == [(PRODUCT_1, "2026-09-01")]
    rep = harvester(full_store()).harvest(lulu(), max_sitemaps=1)
    assert rep.truncated and rep.sitemaps_read == 1


def test_discover_reads_the_indexes_but_only_a_few_url_lists():
    pages = full_store()
    children = [BASE + f"/sitemaps/list-{i}.xml" for i in range(6)]
    pages[BASE + "/sitemaps/index.xml"] = (200, index_of(*children))
    for i, c in enumerate(children):
        pages[c] = (200, urlset(BASE + f"/en-ae/item-{i}/p/{i}", BASE + f"/en-ae/cat-{i}/c/{i}"))
    rep = harvester(pages).harvest(lulu(), discover=True)
    assert rep.sitemaps_read == 1 + sitemaps.DISCOVER_URLSETS
    assert sum(1 for _, why in rep.skipped if why == "not read (discover)") == 6 - sitemaps.DISCOVER_URLSETS
    text = sitemaps.format_report(rep, lulu(), discover=True)
    assert "product page samples" in text and "/en-ae/cat-0/c/0" in text


def test_nested_indexes_stop_at_max_depth(monkeypatch):
    monkeypatch.setattr(sitemaps, "MAX_DEPTH", 1)
    pages = {BASE + "/robots.txt": (404, b""),
             BASE + "/sitemap.xml": (200, index_of(BASE + "/a.xml")),
             BASE + "/a.xml": (200, index_of(BASE + "/b.xml")),
             BASE + "/b.xml": (200, urlset(PRODUCT_1))}
    rep = harvester(pages).harvest(lulu())
    assert (BASE + "/b.xml", "too deep") in rep.skipped and rep.product_urls == 0


# ---------------------------------------------------------------------------
# scripts/build_catalog_index.py
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_web(monkeypatch):
    http = FakeHttp(full_store())

    class Harvester(SitemapHarvester):
        def __init__(self, *a, **kw):
            super().__init__(http=http, sleep=lambda s: None)

    monkeypatch.setattr(sitemaps, "SitemapHarvester", Harvester)
    return http


def test_cli_discover_writes_nothing_and_needs_no_database(fake_web, monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(build_catalog_index, "_db_store", lambda: pytest.fail("discover must not open the database"))
    out = tmp_path / "discover.json"
    code = build_catalog_index.main(["--discover", "--stores", "lulu", "--json", str(out)])
    text = capsys.readouterr().out
    assert code == 0 and "nothing was written" in text and "product page samples" in text
    assert json.loads(out.read_text(encoding="utf-8"))[0]["product_urls"] == 2


def test_cli_build_upserts_and_prunes_only_after_a_complete_harvest(fake_web, monkeypatch, capsys):
    store = MemoryCatalogStore()
    store.upsert("lulu", [(BASE + "/en-ae/delisted-product/p/1", None)])
    monkeypatch.setattr(build_catalog_index, "_db_store", lambda: (store, ""))
    assert build_catalog_index.main(["--stores", "lulu", "--max-urls", "1", "--prune"]) == 0
    assert store.count() == 2                                            # partial: nothing pruned
    assert build_catalog_index.main(["--stores", "lulu", "--prune"]) == 0
    assert sorted(r.url for r in store.rows.values()) == sorted([PRODUCT_1, PRODUCT_2])
    assert [h["status"] for h in store.harvests] == ["partial", "ok"]
    assert "new product pages indexed" in capsys.readouterr().out


def test_cli_reports_a_blocked_store_and_fails_when_every_store_was_refused(monkeypatch, capsys):
    http = FakeHttp({BASE + "/robots.txt": (403, b"")})

    class Harvester(SitemapHarvester):
        def __init__(self, *a, **kw):
            super().__init__(http=http, sleep=lambda s: None)

    monkeypatch.setattr(sitemaps, "SitemapHarvester", Harvester)
    assert build_catalog_index.main(["--dry-run", "--stores", "lulu"]) == 1
    assert "never worked around): lulu" in capsys.readouterr().out
