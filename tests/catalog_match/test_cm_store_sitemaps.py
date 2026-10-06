"""The shipped catalog_stores.json against what each UAE store really publishes (checked 2026-10-06).

fixtures/stores holds each store's robots.txt as it was served (Carrefour UAE's is the empty HTML page its CDN
answered), trimmed copies of the sitemaps that could be read (Spinneys' en-ae urlset; talabat's index and two of its
children) and real URL samples, URLs only and no page bodies, from the stores' sitemaps and the live runs' exports.
The HTTP client is test_cm_sitemaps' fake: nothing here reaches the network.
"""

import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from catalog_match.sitemaps import SitemapHarvester, load_stores, parse_robots, parse_sitemap, suggest_config
from test_cm_sitemaps import FakeHttp, harvester, index_of, urlset

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "stores"
SAMPLES = json.loads((FIXTURES / "url_samples.json").read_text(encoding="utf-8"))["stores"]
ROBOTS = {"lulu": "robots_lulu.txt", "spinneys": "robots_spinneys.txt", "talabat_mart_uae": "robots_talabat.txt",
          "sharjahcoop": "robots_sharjahcoop.txt", "carrefour_uae": "robots_carrefour_uae.html"}

pytestmark = pytest.mark.usefixtures("offline")


def fixture(name):
    return (FIXTURES / name).read_bytes()


def store(key):
    return {s.key: s for s in load_stores()}[key]


def robots(key):
    return parse_robots(fixture(ROBOTS[key]).decode("utf-8"))


# ---------------------------------------------------------------------------
# product_path and the starting sitemaps
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("key", sorted(SAMPLES))
def test_product_path_matches_the_real_product_pages_and_nothing_else(key):
    s = store(key)
    for url in SAMPLES[key]["product"]:                 # sitemap entries and the live runs' product pages
        assert s.is_product(url), url
    for url in SAMPLES[key]["not_product"]:             # other countries, Arabic pages, recipes, a page without slug
        assert not s.is_product(url), url


def test_a_sharjah_coop_vendor_item_id_with_an_underscore_is_a_product_page():
    assert store("sharjahcoop").is_product(
        "https://www.sharjahcoop.ae/en/nellara-chicken-fry-masala-100-g-duplex/p/V00236_6291104180277NLR")


@pytest.mark.parametrize("key", ["lulu", "spinneys", "sharjahcoop"])
def test_each_enabled_store_starts_from_the_sitemap_its_robots_txt_lists_and_allows(key):
    s, rules = store(key), robots(key)
    assert s.enabled and s.sitemaps
    for url in s.sitemaps:
        assert url in rules.sitemaps and rules.allowed(url) and s.on_store(url)
    for url in SAMPLES[key]["product"]:                 # the pages the index reads later are allowed as well
        assert rules.allowed(url), url


@pytest.mark.parametrize("key,delay,visit", [
    ("lulu", 1.0, []), ("spinneys", None, []), ("talabat_mart_uae", None, []), ("sharjahcoop", 10.0, [(240, 525)]),
])
def test_the_stores_robots_txt_as_served(key, delay, visit):
    rules = robots(key)
    assert rules.crawl_delay == delay and rules.visit_times == visit
    assert rules.sitemaps                               # each one names its sitemaps


# ---------------------------------------------------------------------------
# Each store's own robots.txt is kept by the harvester
# ---------------------------------------------------------------------------

INSIDE = datetime(2026, 10, 6, 5, 0, tzinfo=timezone.utc)           # inside Sharjah Co-op's 04:00-08:45 UTC
OUTSIDE = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("key,child,disallowed,delay", [
    ("lulu", "/en-ae/sitemap-products-1.xml", "/en-ae/sitemap.xml?page=2", 1.0),                  # /*?page=
    ("spinneys", "/en-ae/sitemap-products.xml", "/en-ae/search/sitemap.xml", 1.0),                # /*search/*
    ("sharjahcoop", "/sitemaps/Product-en-AED-1.xml", "/en/checkout/sitemaps/Product-en-AED-2.xml", 10.0),
])
def test_each_stores_disallow_rules_and_crawl_delay_are_kept(key, child, disallowed, delay):
    s = store(key)
    product = SAMPLES[key]["product"][0]
    pages = {s.base_url + "/robots.txt": (200, fixture(ROBOTS[key])),
             s.sitemaps[0]: (200, index_of(s.base_url + disallowed, s.base_url + child)),
             s.base_url + child: (200, urlset(product))}
    waits, got = [], []
    h = SitemapHarvester(http=FakeHttp(pages), sleep=waits.append, clock=lambda: 0.0, utc_now=lambda: INSIDE)
    rep = h.harvest(s, on_urls=lambda batch: (got.extend(batch), len(batch))[1])
    assert rep.status == "ok" and [u for u, _ in got] == [product]
    assert (s.base_url + disallowed, "disallowed by robots.txt") in rep.skipped
    assert s.base_url + disallowed not in h.http.calls
    assert waits == [delay, delay]                      # the store's Crawl-delay (at least 1 s) between requests


def test_discover_finds_the_include_sharjah_coop_ships_with():
    # its index lists the English and Arabic product lists and others; discover reads three lists and prints the rest
    s = store("sharjahcoop")
    base = s.base_url
    children = ([base + f"/sitemaps/Product-en-AED-{i}.xml" for i in range(1, 6)]
                + [base + f"/sitemaps/Product-ar-AED-{i}.xml" for i in (1, 2)]
                + [base + "/sitemaps/Category-en-AED-1.xml", base + "/sitemaps/Content-en-AED-1.xml"])
    pages = {base + "/robots.txt": (200, fixture(ROBOTS["sharjahcoop"])), s.sitemaps[0]: (200, index_of(*children))}
    for i, url in enumerate(children[:5], 1):
        pages[url] = (200, urlset(SAMPLES["sharjahcoop"]["product"][i - 1]))
    h = SitemapHarvester(http=FakeHttp(pages), sleep=lambda _: None, clock=lambda: 0.0, utc_now=lambda: INSIDE)
    rep = h.harvest(replace(s, include=None), discover=True)
    found = suggest_config(rep)
    assert found["sitemaps"] == list(s.sitemaps) and found["sitemap_include"] == s.include.pattern
    assert (found["listed"], found["kept"]) == (9, 5)


def test_sharjah_coop_is_not_read_outside_its_visit_time():
    s = store("sharjahcoop")
    h = SitemapHarvester(http=FakeHttp({s.base_url + "/robots.txt": (200, fixture(ROBOTS["sharjahcoop"]))}),
                         sleep=lambda _: None, clock=lambda: 0.0, utc_now=lambda: OUTSIDE)
    rep = h.harvest(s)
    assert rep.status == "outside_visit_time" and h.http.calls == [s.base_url + "/robots.txt"]


# ---------------------------------------------------------------------------
# What each store gives
# ---------------------------------------------------------------------------

def test_spinneys_is_read_from_its_one_en_ae_sitemap_and_gives_only_its_product_pages():
    s = store("spinneys")
    pages = {s.base_url + "/robots.txt": (200, fixture(ROBOTS["spinneys"])),
             s.sitemaps[0]: (200, fixture("spinneys_en-ae_sitemap.xml"))}
    waits, got = [], []
    h = harvester(pages, waits)
    rep = h.harvest(s, on_urls=lambda batch: (got.extend(batch), len(batch))[1])
    assert rep.status == "ok" and rep.started_from == ["https://www.spinneys.com/en-ae/sitemap.xml"]
    assert h.http.calls == [s.base_url + "/robots.txt", s.sitemaps[0]]        # never the Arabic sitemap
    assert waits == [1.0]                               # no Crawl-delay: one second between requests
    assert (rep.urls_seen, rep.product_urls) == (14, 8)  # home, recipes, lifestyle and podcasts are not products
    assert all("/en-ae/catalogue/" in url for url, _ in got) and len(got) == 8


def test_lulu_reads_only_its_en_ae_sitemap_and_a_refusal_is_recorded_never_worked_around():
    s = store("lulu")
    rules = robots("lulu")
    assert len(rules.sitemaps) == 12 and s.sitemaps == ("https://gcc.luluhypermarket.com/en-ae/sitemap.xml",)
    pages = {s.base_url + "/robots.txt": (200, fixture(ROBOTS["lulu"])), s.sitemaps[0]: (403, b"")}
    h = harvester(pages)
    rep = h.harvest(s)
    assert rep.status == "blocked" and "http_403" in rep.error and rep.crawl_delay == 1.0
    assert h.http.calls == [s.base_url + "/robots.txt", s.sitemaps[0]]        # no other country, no retry


def test_talabat_publishes_no_sitemap_of_its_mart_product_pages_so_it_stays_off():
    s = store("talabat_mart_uae")
    assert not s.enabled and "no sitemap" in s.note
    base, index = s.base_url, fixture("talabat_sitemap_index.xml")
    kind, children = parse_sitemap(index)
    assert kind == "index" and not any("mart" in loc or "product" in loc for loc, _ in children)
    pages = {base + "/robots.txt": (200, fixture(ROBOTS["talabat_mart_uae"])),
             base + "/_sitemap/sitemap.xml.gz": (200, index, base + "/sitemap/sitemap.xml.gz"),   # redirects there
             base + "/sitemap/sitemap.xml.gz": (200, index),
             base + "/sitemap/uae/en/groceries_areas.xml.gz": (200, fixture("talabat_uae_en_groceries_areas.xml")),
             base + "/sitemap/sitemap-static.xml.gz": (200, fixture("talabat_sitemap_static.xml"))}
    rep = harvester(pages).harvest(s)                    # asked for by key, as --stores does
    assert rep.sitemaps_read >= 3 and rep.urls_seen == 8 and rep.product_urls == 0


def test_carrefour_answers_an_empty_html_page_so_it_stays_off_until_discover_runs_on_the_server():
    s = store("carrefour_uae")
    assert not s.enabled and not s.sitemaps and "--discover --stores carrefour_uae" in s.note
    page = fixture(ROBOTS["carrefour_uae"])
    rep = harvester({s.base_url + "/robots.txt": (200, page), s.base_url + "/sitemap.xml": (200, page)}).harvest(s)
    assert rep.robots == "missing (http 200)" and rep.status == "blocked" and "bot check" in rep.error


# ---------------------------------------------------------------------------
# Trusted hosts: an index hit ranks as a UAE retailer's, and the stores' image CDNs are never skipped as slow
# ---------------------------------------------------------------------------

INDEX_HITS = [   # (store, a real product page, the sheet row it is the product of)
    ("lulu", "https://gcc.luluhypermarket.com/en-ae/shan-meat-masala-100-g/p/22596", "SHAN MEAT MASALA 100GM", "SHAN"),
    ("spinneys", "https://www.spinneys.com/en-ae/catalogue/zwan-chicken-luncheon-200g_5651/",
     "ZWAN CHICKEN LUNCHEON MEAT 200GM", "ZWAN"),
    ("sharjahcoop", "https://www.sharjahcoop.ae/en/eastern-kabsa-masala-200g/p/8901440206897",
     "EASTERN KABSA MASALA 200G", "EASTERN"),
    ("carrefour_uae", "https://www.carrefouruae.com/mafuae/en/masala-and-mix/mehran-meat-masala-100g/p/1533388",
     "MEHRAN MEAT MASALA 100G", "MEHRAN"),
    ("talabat_mart_uae", "https://www.talabat.com/uae/talabat-mart/product/al-kabeer-frozen-punjabi-samosa-900g/s/500273",
     "AL KABEER PUNJABI SAMOSA 900G", "AL KABEER"),
]


@pytest.mark.parametrize("key,url,name,brand", INDEX_HITS)
def test_an_index_hit_from_each_store_is_a_tier_1_uae_retailer_candidate(key, url, name, brand):
    from catalog_match.identity import build_sku_spec
    from catalog_match.local_index import MemoryCatalogStore, rank_rows, search_keys
    spec = build_sku_spec({"name": name, "brand": brand})
    index = MemoryCatalogStore()
    index.upsert(key, [(url, None)])
    groups, extra = search_keys(spec)
    [(row, score)] = rank_rows(spec, [r for required in groups for r in index.find(required, extra)])
    assert row.url == url and score.tier == 1 and score.matched["source_class"] == "uae_retailer"


@pytest.mark.parametrize("key", sorted(SAMPLES))
def test_every_store_host_is_a_uae_retailer_in_source_trust_and_its_other_countries_are_not(key):
    from catalog_match.identity import build_sku_spec
    from catalog_match.models import Candidate
    from catalog_match.score import TRUST_OTHER_RETAIL, TRUST_UAE_RETAILER, source_trust
    spec = build_sku_spec({"name": "ZWAN LUNCHEON MEAT 200GM", "brand": "ZWAN"})
    s = store(key)
    for host in s.hosts:
        assert source_trust(spec, Candidate(image_url="https://img.example/a.jpg",
                                            page_url=f"https://{host}/x"))[0] == TRUST_UAE_RETAILER, host
    for url in SAMPLES[key]["product"]:
        assert source_trust(spec, Candidate(image_url="https://img.example/a.jpg", page_url=url))[0] \
            == TRUST_UAE_RETAILER, url
    for url in SAMPLES[key]["not_product"]:          # another country's section sells the foreign pack
        if any(part in url for part in ("/en-kw/", "/en-om/", "/en-sa/", "/en-bh/", "/ar-bh/", "/kuwait/",
                                        "/bahrain/", "/oman/")):
            assert source_trust(spec, Candidate(image_url="https://img.example/a.jpg", page_url=url))[0] \
                == TRUST_OTHER_RETAIL, url


@pytest.mark.parametrize("key", sorted(SAMPLES))
def test_the_image_hosts_of_each_stores_own_pages_are_never_skipped_as_slow(key):
    from catalog_match.fetch import HOST_FAIL_LIMIT_TRUSTED, HostBreaker
    from catalog_match.text_norm import url_host
    breaker = HostBreaker()
    for page, image in SAMPLES[key]["image_pairs"]:
        host = url_host(image)
        assert breaker.exempt(host, url_host(page)) and breaker.exempt(host, ""), image   # with or without its page
        assert breaker.limit(host) == HOST_FAIL_LIMIT_TRUSTED, host


def test_a_retailer_image_host_gives_no_source_trust_and_other_tenants_of_a_shared_cdn_are_not_exempt():
    from catalog_match.fetch import HostBreaker
    from catalog_match.identity import build_sku_spec
    from catalog_match.models import Candidate
    from catalog_match.score import TRUST_GENERIC, source_trust, trusted_domains
    spec = build_sku_spec({"name": "ZWAN LUNCHEON MEAT 200GM", "brand": "ZWAN"})
    for host in trusted_domains()["uae_retailer_image_hosts"]:
        cand = Candidate(image_url=f"https://{host}/products/zwan.jpg", page_url="", domain=host)
        assert source_trust(spec, cand)[0] == TRUST_GENERIC, host     # trust stays keyed on the page domain
    breaker = HostBreaker()
    for host in ("other.akinoncloudcdn.com", "akinoncloudcdn.com", "someone.azureedge.net", "hungerstation.dhmedia.io",
                 "m.media-amazon.com"):
        assert not breaker.exempt(host, "some-blog.example"), host


def test_a_store_configured_from_robots_reads_what_robots_lists():
    # the same Carrefour entry once its robots.txt answers on the server: the Sitemap: lines are the starting points
    s = store("carrefour_uae")
    listed = s.base_url + "/sitemap.xml"
    product = SAMPLES["carrefour_uae"]["product"][0]
    pages = {s.base_url + "/robots.txt": (200, f"User-agent: *\nSitemap: {listed}\n".encode()),
             listed: (200, urlset(product, SAMPLES["carrefour_uae"]["not_product"][0]))}
    rep = harvester(pages).harvest(replace(s, enabled=True))
    assert rep.status == "ok" and rep.started_from == [listed] and rep.product_urls == 1
