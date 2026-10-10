"""catalog_match.sitemaps and scripts/build_catalog_index.py: reading the stores' published sitemaps.

The HTTP client is a fake (url -> status, body); waits go to a list instead of sleeping. A store
that refuses (401 / 403 / 429, a bot-check page, a robots.txt 5xx) is skipped, never worked around.
"""

import gzip
import json
import os
import re
import socket
import sys
from dataclasses import replace

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
    def __init__(self, status, body=b"", url=None):
        self.status_code, self.body, self.closed, self.url = status, body, False, url

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
        status, body, *final = self.pages.get(url, (404, b""))     # (status, body[, the URL it redirected to])
        if isinstance(status, Exception):
            raise status
        return Resp(status, body, final[0] if final else url)


def robots_listed(stores):
    """The stores without their configured starting sitemaps: these tests serve stores whose robots.txt lists them
    (the shipped starting points are checked against the stores' real robots.txt in test_cm_store_sitemaps.py)."""
    return [replace(s, sitemaps=()) for s in stores]


@pytest.fixture
def robots_listed_stores(monkeypatch):
    """scripts/build_catalog_index.py reads robots_listed() stores (load_stores, wrapped)."""
    shipped = sitemaps.load_stores
    monkeypatch.setattr(sitemaps, "load_stores", lambda path=None: robots_listed(shipped(path)))


def lulu():
    return next(s for s in robots_listed(load_stores()) if s.key == "lulu")


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
    ("sharjahcoop", "https://www.sharjahcoop.ae/en/dahabi-slicesd-bread-brown-700g/p/9501100306746"),
]
NOT_PRODUCT_PAGES = [
    ("lulu", "https://gcc.luluhypermarket.com/en-ae/frozen-food/c/1001"),
    ("carrefour_uae", "https://www.carrefourksa.com/mafsau/en/french-fries/sunbulah-french-fries-7mm-1kg/p/26393"),
    ("carrefour_uae", "https://www.carrefouruae.com/mafuae/ar/beef-luncheon/zwan/p/10937"),
    ("talabat_mart_uae", "https://www.talabat.com/bahrain/talabat-mart/product/goody-tuna-185g/s/908043"),
    ("unioncoop", "https://www.unioncoop.ae/about-us.html"),
    ("sharjahcoop", "https://www.sharjahcoop.ae/ar/dahabi-slicesd-bread-brown-700g/p/9501100306746"),
    ("sharjahcoop", "https://www.sharjahcoop.ae/en/dairy/c/1001"),
]


@pytest.mark.parametrize("key,url", LIVE_PRODUCT_PAGES)
def test_the_shipped_patterns_match_the_product_pages_the_live_runs_found(key, url):
    store = {s.key: s for s in load_stores()}[key]
    assert store.is_product(url)


@pytest.mark.parametrize("key,url", NOT_PRODUCT_PAGES)
def test_categories_other_countries_and_other_languages_are_not_product_pages(key, url):
    store = {s.key: s for s in load_stores()}[key]
    assert not store.is_product(url)


def test_stores_that_cannot_be_used_are_off_unless_asked_for():
    # noon / Union Coop: slugs leave out the brand; talabat: no product sitemap; Carrefour: sitemaps not known yet
    stores = load_stores()
    assert [s.key for s in enabled_stores(stores)] == ["lulu", "spinneys", "sharjahcoop"]
    assert [s.key for s in enabled_stores(stores, ["noon_uae"])] == ["noon_uae"]
    assert [s.key for s in enabled_stores(stores, ["carrefour_uae", "talabat_mart_uae"])] == ["carrefour_uae",
                                                                                            "talabat_mart_uae"]
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


@pytest.mark.parametrize("body,html", [
    (b"<!-- cdn --> <!--x--><!DOCTYPE html><html>", True),
    (b'<?xml version="1.0"?>\n<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0//EN"><html>', True),
    (b"  <HTML lang=en>", True),
    (b'<?xml version="1.0"?><!-- generated --><urlset>', False),
    (b"<htmlish/>", False),
    (b"<!-- never closed <html>", False),
])
def test_an_html_page_is_recognised_after_a_prolog_and_comments(body, html):
    assert sitemaps.looks_like_html(body) is html


def test_html_detection_stays_linear_on_comment_runs():
    # CodeQL py/redos: '(?:<!--.*?-->\s*)*' backtracked exponentially on '<!--' + '--><!--' * n
    import time
    start = time.monotonic()
    assert sitemaps.looks_like_html(b"<!--" + b"--><!--" * 5000) is False
    assert time.monotonic() - start < 0.5


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
    # a guessed location that is not there is no failure: the harvest is complete (so --prune can run)
    assert rep.status == "ok" and rep.product_urls == 1
    rep = harvester({BASE + "/robots.txt": (404, b"")}).harvest(lulu())
    assert rep.status == "error" and rep.sitemaps_read == 0


def test_the_second_usual_location_is_asked_only_when_the_first_gave_nothing():
    # /sitemap_index.xml is the site's single-page app (HTML, status 200): never asked when /sitemap.xml works
    pages = {BASE + "/robots.txt": (404, b""), BASE + "/sitemap.xml": (200, index_of(BASE + "/s/p1.xml")),
             BASE + "/s/p1.xml": (200, urlset(PRODUCT_1)),
             BASE + "/sitemap_index.xml": (200, b"<!DOCTYPE html><html>app</html>")}
    h = harvester(pages)
    rep = h.harvest(lulu())
    assert rep.status == "ok" and rep.product_urls == 1 and BASE + "/sitemap_index.xml" not in h.http.calls


def test_an_html_page_after_the_first_sitemap_is_one_failed_sitemap_not_a_block():
    pages = full_store()
    pages[BASE + "/sitemaps/products-1.xml.gz"] = (200, b"<!DOCTYPE html><html>not found</html>")
    rep = harvester(pages).harvest(lulu())
    assert rep.status == "partial" and (BASE + "/sitemaps/products-1.xml.gz", "html") in rep.skipped
    assert BASE + "/sitemaps/categories.xml" in [n["url"] for n in rep.tree]   # the next sitemap was still read


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


def pasted(lines, store):
    """The store as it is after its 'paste these lines' block went into its entry of the stores file."""
    start = next(i for i, line in enumerate(lines) if "paste these lines" in line)
    values = json.loads("{" + "\n".join(lines[start + 1:start + 4]).rstrip(",") + "}")
    assert set(values) == {"sitemaps", "sitemap_include", "enabled"}
    return replace(store, sitemaps=tuple(values["sitemaps"]), enabled=values["enabled"],
                   include=sitemaps._pattern(values["sitemap_include"]))


def test_discover_prints_the_starting_sitemap_and_the_include_to_paste():
    rep = harvester(full_store()).harvest(lulu(), discover=True)
    found = sitemaps.suggest_config(rep)
    assert found == {"sitemaps": [BASE + "/sitemaps/index.xml"], "sitemap_include": r"/sitemaps/products-\d+\.xml\.gz$",
                     "listed": 2, "kept": 1, "also_empty": 0}          # categories.xml held no product page
    lines = sitemaps.format_report(rep, lulu(), discover=True).splitlines()
    assert '     "sitemap_include": "/sitemaps/products-\\\\d+\\\\.xml\\\\.gz$",' in lines     # JSON, as in the file
    store = pasted(lines, lulu())
    h = harvester(full_store())
    again = h.harvest(store)
    assert again.status == "ok" and again.product_urls == 2
    assert BASE + "/sitemaps/categories.xml" not in h.http.calls       # the include leaves it out now


def test_discover_follows_nested_indexes_and_keeps_only_the_paths_to_the_product_lists():
    root = BASE + "/sitemap.xml"
    en, ar = BASE + "/sitemaps/sitemap_index_en.xml", BASE + "/sitemaps/sitemap_index_ar.xml"
    lists = [BASE + f"/sitemaps/en/products_{i}.xml" for i in range(1, 6)]
    pages = {BASE + "/robots.txt": (200, f"User-agent: *\nSitemap: {root}\n".encode()),
             root: (200, index_of(ar, en, BASE + "/sitemaps/pages.xml")),
             en: (200, index_of(*lists, BASE + "/sitemaps/en/categories.xml")),
             ar: (200, index_of(BASE + "/sitemaps/ar/products_1.xml")),
             BASE + "/sitemaps/ar/products_1.xml": (200, urlset(BASE + "/ar-ae/x/p/1")),
             BASE + "/sitemaps/pages.xml": (200, urlset(BASE + "/en-ae/about")),
             BASE + "/sitemaps/en/categories.xml": (200, urlset(BASE + "/en-ae/frozen/c/1"))}
    for i, url in enumerate(lists, 1):
        pages[url] = (200, urlset(BASE + f"/en-ae/item-{i}/p/{i}"))
    rep = harvester(pages).harvest(lulu(), discover=True)
    found = sitemaps.suggest_config(rep)
    assert found["sitemaps"] == [root]
    include = re.compile(found["sitemap_include"])
    assert all(include.search(u) for u in [en] + lists)                 # the unread lists of the series too
    assert not any(include.search(u) for u in (ar, BASE + "/sitemaps/en/categories.xml", BASE + "/sitemaps/pages.xml"))
    full = harvester(pages).harvest(pasted(sitemaps.paste_lines(rep), lulu()))
    assert full.status == "ok" and full.product_urls == 5 and full.sitemaps_read == 7     # root, en and its 5 lists


def test_a_single_url_list_needs_no_include_and_nothing_found_says_why():
    pages = {BASE + "/robots.txt": (200, f"User-agent: *\nSitemap: {BASE}/en-ae/sitemap.xml\n".encode()),
             BASE + "/en-ae/sitemap.xml": (200, urlset(PRODUCT_1, BASE + "/en-ae/recipes/x"))}
    rep = harvester(pages).harvest(lulu(), discover=True)
    assert sitemaps.suggest_config(rep) == {"sitemaps": [BASE + "/en-ae/sitemap.xml"], "sitemap_include": "",
                                            "listed": 0, "kept": 0, "also_empty": 0}
    pages[BASE + "/en-ae/sitemap.xml"] = (200, urlset(BASE + "/en-ae/recipes/x"))
    rep = harvester(pages).harvest(lulu(), discover=True)
    assert sitemaps.suggest_config(rep) is None
    assert "nothing to paste (no page of the URL lists read matched product_path" in sitemaps.paste_lines(rep)[0]
    rep = harvester({BASE + "/robots.txt": (403, b"")}).harvest(lulu(), discover=True)
    assert sitemaps.paste_lines(rep) == ["   nothing to paste (the store did not give its sitemaps)"]


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
def fake_web(monkeypatch, robots_listed_stores):
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
    assert 'paste these lines into the store "lulu"' in text
    assert f'     "sitemaps": ["{BASE}/sitemaps/index.xml"],' in text and '     "enabled": true,' in text
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


# ---------------------------------------------------------------------------
# Review 3: robots.txt as RFC 9309 reads it, and a broken file never ends the run
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("rules,path,allowed", [
    ("Allow: /\nDisallow: /sitemaps/", "/sitemaps/a.xml", False),          # the longest match wins, not the first
    ("Disallow: /\nAllow: /sitemap", "/sitemap.xml", True),
    ("Disallow: /*?", "/sitemap.xml?page=2", False),                        # '*' wildcard
    ("Disallow: /*?", "/sitemap.xml", True),
    ("Disallow: /*.xml.gz$", "/s/products-1.xml.gz", False),                # '$' end anchor
    ("Disallow: /*.xml.gz$", "/s/products-1.xml.gz.txt", True),
    ("Allow: /s/\nDisallow: /s/", "/s/a.xml", True),                        # a tie: Allow wins
    ("Disallow:", "/anything.xml", True),                                    # an empty Disallow is no rule
])
def test_robots_rules_follow_rfc_9309(rules, path, allowed):
    robots = parse_robots(f"User-agent: *\n{rules}\n")
    assert robots.allowed(BASE + path) is allowed


def test_robots_groups_byte_order_mark_and_a_decimal_crawl_delay():
    text = ("\ufeffUser-agent: *\nDisallow: /sm/private\nCrawl-delay: 2.5\n\n"
            "User-agent: image\nDisallow: /\n")                            # another crawler's group
    robots = parse_robots(text)
    assert robots.crawl_delay == 2.5 and not robots.allowed(BASE + "/sm/private/a.xml")
    assert robots.allowed(BASE + "/sm/public.xml")                          # 'image' is not our agent
    ours = parse_robots(f"User-agent: *\nDisallow: /\n\nUser-agent: {sitemaps.ROBOTS_AGENT}\nDisallow: /tmp/\n")
    assert ours.allowed(BASE + "/sitemap.xml") and not ours.allowed(BASE + "/tmp/x.xml")   # our own group wins


def test_a_byte_order_mark_on_robots_never_drops_its_rules_in_a_harvest():
    pages = full_store()
    pages[BASE + "/robots.txt"] = (200, "\ufeff".encode() + ROBOTS)
    h = harvester(pages)
    rep = h.harvest(lulu())
    assert BASE + "/private/hidden.xml" not in h.http.calls and rep.crawl_delay == 2.0


def test_a_store_asking_for_a_longer_crawl_delay_than_we_wait_is_skipped():
    pages = {BASE + "/robots.txt": (200, b"User-agent: *\nCrawl-delay: 120\n")}
    h = harvester(pages)
    rep = h.harvest(lulu())
    assert rep.status == "blocked" and "Crawl-delay of 120s" in rep.error and h.http.calls == [BASE + "/robots.txt"]


CDN_INDEX = "https://sitemaps.example-cdn.net/gcc/sitemap_index.xml"


def test_a_robots_sitemap_on_another_host_is_reported_and_read_once_its_host_is_listed():
    pages = {BASE + "/robots.txt": (200, f"User-agent: *\nSitemap: {CDN_INDEX}\n".encode()),
             CDN_INDEX: (200, index_of("https://sitemaps.example-cdn.net/gcc/products.xml")),
             "https://sitemaps.example-cdn.net/gcc/products.xml": (200, urlset(PRODUCT_1))}
    rep = harvester(pages).harvest(lulu())
    assert any(url == CDN_INDEX and "sitemap_hosts" in why for url, why in rep.skipped)
    assert sitemaps.format_report(rep, lulu(), discover=True).count(CDN_INDEX) == 1
    from dataclasses import replace
    store = replace(lulu(), sitemap_hosts=("sitemaps.example-cdn.net",))
    rep = harvester(pages).harvest(store)
    assert rep.status == "ok" and rep.product_urls == 1
    assert not store.is_product("https://sitemaps.example-cdn.net/en-ae/x/p/1")   # product pages stay the store's


def test_a_redirect_off_the_store_or_to_its_home_page_is_a_failed_sitemap():
    pages = full_store()
    pages[BASE + "/sitemaps/products-1.xml.gz"] = (200, urlset(PRODUCT_1), "https://evil.example/x.xml")
    pages[BASE + "/sitemaps/categories.xml"] = (200, urlset(PRODUCT_2), BASE + "/")
    rep = harvester(pages).harvest(lulu())
    assert (BASE + "/sitemaps/products-1.xml.gz", "redirected outside the store") in rep.skipped
    assert (BASE + "/sitemaps/categories.xml", "redirected to the home page") in rep.skipped
    assert rep.status == "partial" and rep.product_urls == 0


@pytest.mark.parametrize("body,why", [
    (b"\x1f\x8b\x08\x00\x00\x00\x00\x00\x00\x03" + b"\xff" * 40, "bad gzip"),        # corrupt deflate data
    (b'<?xml version="1.0" encoding="x-unknown"?><urlset/>', "bad xml"),
    (b'<?xml version="1.0" encoding="gbk"?><urlset/>', "bad xml"),
    ('<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE x><urlset/>'.encode("utf-16"), "not utf-8"),
])
def test_a_broken_file_is_one_failed_sitemap(body, why):
    with pytest.raises(SitemapError, match=why):
        parse_sitemap(decompress(body))
    pages = full_store()
    pages[BASE + "/sitemaps/products-1.xml.gz"] = (200, body)
    rep = harvester(pages).harvest(lulu())
    assert rep.status == "partial" and any(u.endswith("products-1.xml.gz") for u, _ in rep.skipped)


def test_an_exact_max_urls_with_nothing_left_is_a_complete_harvest():
    one = {BASE + "/robots.txt": (404, b""), BASE + "/sitemap.xml": (200, urlset(PRODUCT_1, PRODUCT_2))}
    rep = harvester(one).harvest(lulu(), max_urls=2)
    assert rep.product_urls == 2 and not rep.truncated and rep.status == "ok"
    rep = harvester(full_store()).harvest(lulu(), max_urls=2)            # a sitemap left unread: partial
    assert rep.truncated and rep.status == "partial"


def test_discover_reads_every_nested_index_even_past_the_url_list_cap():
    pages = full_store()
    lists = [BASE + f"/sitemaps/products-{i}.xml" for i in range(4)]
    group, deep = BASE + "/sitemaps/group-index.xml", BASE + "/sitemaps/deep-index.xml"
    pages[BASE + "/sitemaps/index.xml"] = (200, index_of(*lists, group))
    pages[group] = (200, index_of(BASE + "/sitemaps/products-9.xml", deep))   # read first: its name says index
    for c in lists + [BASE + "/sitemaps/products-9.xml"]:
        pages[c] = (200, urlset(BASE + "/en-ae/item/p/1"))
    pages[deep] = (200, index_of(BASE + "/sitemaps/en-ae-products.xml"))
    rep = harvester(pages).harvest(lulu(), discover=True)
    indexes = [n["url"] for n in rep.tree if n["kind"] == "index"]
    assert group in indexes and deep in indexes                         # deep-index.xml came after the cap
    assert sum(1 for n in rep.tree if n["kind"] == "urlset") == sitemaps.DISCOVER_URLSETS


def test_a_bad_pattern_or_a_byte_order_mark_in_the_stores_file(tmp_path):
    good = tmp_path / "stores.json"
    good.write_text("\ufeff" + json.dumps({"stores": [{"key": "x", "base_url": "https://x.ae", "product_path": "/p/"}]}),
                    encoding="utf-8")
    assert [s.key for s in load_stores(good)] == ["x"]
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"stores": [{"key": "x", "base_url": "https://x.ae", "product_path": "/p/("}]}),
                   encoding="utf-8")
    with pytest.raises(ValueError, match="x: product_path"):
        load_stores(bad)
    assert build_catalog_index.main(["--discover", "--config", str(bad)]) == 2


def test_cli_one_store_failing_never_stops_the_others_and_the_json_is_written(monkeypatch, capsys, tmp_path,
                                                                             robots_listed_stores):
    pages = full_store()
    spinneys = next(s for s in load_stores() if s.key == "spinneys")
    pages[spinneys.base_url + "/robots.txt"] = (404, b"")
    pages[spinneys.base_url + "/sitemap.xml"] = (200, urlset(spinneys.base_url + "/en-ae/catalogue/x_1/"))
    http = FakeHttp(pages)

    class Harvester(SitemapHarvester):
        def __init__(self, *a, **kw):
            super().__init__(http=http, sleep=lambda s: None)

    monkeypatch.setattr(sitemaps, "SitemapHarvester", Harvester)
    store = MemoryCatalogStore()
    real_upsert = store.upsert

    def upsert(key, batch):
        if key == "lulu":
            raise RuntimeError("database went away")
        return real_upsert(key, batch)

    store.upsert = upsert
    monkeypatch.setattr(build_catalog_index, "_db_store", lambda: (store, ""))
    out = tmp_path / "run.json"
    code = build_catalog_index.main(["--stores", "lulu,spinneys", "--json", str(out)])
    reports = json.loads(out.read_text(encoding="utf-8"))
    assert code == 0 and [r["status"] for r in reports] == ["error", "ok"]
    assert "database went away" in reports[0]["error"]
    assert [h["store"] for h in store.harvests] == ["lulu", "spinneys"]   # both recorded


# ---------------------------------------------------------------------------
# Image sitemaps: <image:image><image:loc> is kept, so the index can offer the image without reading the page
# ---------------------------------------------------------------------------

IMAGE_NS = 'xmlns:image="http://www.google.com/schemas/sitemap-image/1.1"'
IMAGE_1 = "https://cdn.luluhypermarket.com/medias/2072326-01.jpg"


def image_urlset(*items):
    """items: (loc, image or None)."""
    body = "".join(f"<url><loc>{loc}</loc>"
                   + (f"<image:image><image:loc>{img}</image:loc><image:title>x</image:title></image:image>"
                      f"<image:image><image:loc>{img}-back.jpg</image:loc></image:image>" if img else "")
                   + "</url>" for loc, img in items)
    return f'<?xml version="1.0" encoding="UTF-8"?><urlset {NS} {IMAGE_NS}>{body}</urlset>'.encode()


def test_parse_sitemap_keeps_the_first_image_of_each_url_but_never_takes_it_for_the_page():
    images = {}
    kind, entries = parse_sitemap(image_urlset((PRODUCT_1, IMAGE_1), (PRODUCT_2, None)), images)
    assert kind == "urlset" and entries == [(PRODUCT_1, None), (PRODUCT_2, None)]   # the page's own <loc> only
    assert images == {PRODUCT_1: IMAGE_1}                                          # the first image, not the back
    assert parse_sitemap(image_urlset((PRODUCT_1, IMAGE_1)))[1] == [(PRODUCT_1, None)]   # without the dict: as before


def test_harvest_hands_over_the_sitemap_image_with_its_product_page_and_the_index_keeps_it():
    pages = full_store()
    pages[BASE + "/sitemaps/products-1.xml.gz"] = (200, gzip.compress(image_urlset((PRODUCT_1, IMAGE_1),
                                                                                   (PRODUCT_2, None))))
    got, store = [], MemoryCatalogStore()
    rep = harvester(pages).harvest(lulu(), on_urls=lambda batch: (got.extend(batch), store.upsert("lulu", batch))[1])
    assert rep.status == "ok" and rep.product_images == 1
    assert got == [(PRODUCT_1, None, IMAGE_1), (PRODUCT_2, None)]
    assert {r.url: r.image_url for r in store.rows.values()} == {PRODUCT_1: IMAGE_1, PRODUCT_2: ""}
    assert "(1 with their image)" in sitemaps.format_report(rep)
