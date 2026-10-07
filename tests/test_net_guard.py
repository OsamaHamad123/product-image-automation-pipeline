"""net_guard: the SSRF guard, and every download path that goes through it.

A URL from a search result, a product page, a sitemap or a reviewer's pick may name this server, the local network
or the cloud metadata service, directly or through a public name or a redirect. Nothing may fetch it. DNS is the
fake of tests/net_fakes.py (installed by conftest.py): no test looks a name up for real or opens a connection.
"""

import io
import socket
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
# tests/catalog_match/test_cm_sitemaps.py (and others) put scripts/ first on sys.path, and scripts/publish_check.py is
# the terminal script of the same name: the repository's own publish_check comes first (as tests/test_bg_skip_dashboard.py)
if sys.path[0] != str(ROOT):
    sys.path.insert(0, str(ROOT))

import net_guard  # noqa: E402
import publish_check  # noqa: E402
from net_fakes import PUBLIC_TEST_ADDRESS, fake_getaddrinfo  # noqa: E402

PRIVATE = {"intranet.example": "10.0.0.5", "metadata.example": "169.254.169.254", "cgnat.example": "100.64.1.2",
           "ula.example": "fd12::1", "mixed.example": ["93.184.216.34", "192.168.1.10"],
           "mapped.example": "::ffff:127.0.0.1", "gone.example": socket.gaierror(-2, "Name or service not known")}


@pytest.fixture
def dns(monkeypatch):
    """Resolve the names of PRIVATE (and anything else to the public test address); count the lookups."""
    looked = []
    inner = fake_getaddrinfo(PRIVATE)

    def getaddrinfo(host):
        looked.append(host)
        return inner(host)

    monkeypatch.setattr(net_guard, "getaddrinfo", getaddrinfo)
    net_guard.reset_cache()
    return looked


@pytest.fixture(autouse=True)
def _no_connections(monkeypatch):
    def refuse(*_a, **_k):
        raise AssertionError("a connection was attempted in an offline test")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


def _jpeg(width=400, height=300):
    arr = np.random.default_rng(1).integers(0, 256, (height, width, 3), dtype=np.uint8)
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, "JPEG", quality=90)
    return buf.getvalue()


class Resp:
    def __init__(self, status=200, body=b"", ctype="image/jpeg", location=None, url=""):
        self.status_code = status
        self.headers = {"Content-Type": ctype, "Content-Length": str(len(body))}
        if location:
            self.headers["Location"] = location
        self._body = body
        self.content = body
        self.url = url
        self.closed = False

    def iter_content(self, chunk_size=65536):
        for i in range(0, len(self._body), chunk_size):
            yield self._body[i:i + chunk_size]

    def close(self):
        self.closed = True


class Web:
    """A fake client: routes[url] -> Resp; records every URL requested and its keyword arguments."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        resp = self.routes.get(url) or Resp(404, b"", "text/html")
        resp.url = url
        return resp

    @property
    def urls(self):
        return [u for u, _k in self.calls]


# ---------------------------------------------------------------------------
# The guard itself
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("url", [
    "file:///etc/hosts", "ftp://example.com/a.jpg", "gopher://example.com/", "data:image/png;base64,AAAA",
    "javascript:alert(1)", "//example.com/a.jpg", "", "https://", "http://[::1", "C:\\temp\\a.jpg",
])
def test_only_http_and_https_urls_with_a_host_pass(dns, url):
    with pytest.raises(net_guard.BlockedURL):
        net_guard.assert_public_url(url)


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/a.jpg", "http://127.1/a.jpg", "http://2130706433/a.jpg", "http://0x7f.1/a.jpg",
    "http://localhost:8000/api", "http://LOCALHOST./x", "http://0.0.0.0/", "http://[::1]/x", "http://[::]/x",
    "http://10.1.2.3/x", "http://172.16.0.9/x", "http://192.168.0.1/x", "http://169.254.169.254/latest/meta-data/",
    "http://100.64.0.1/x", "http://224.0.0.1/x", "http://[fd00::1]/x", "http://[fe80::1]/x",
    "http://[::ffff:127.0.0.1]/x", "http://[::ffff:a9fe:a9fe]/x", "http://[2002:7f00:1::]/x",
    "http://[64:ff9b::a00:1]/x",
])
def test_internal_addresses_are_refused_in_every_spelling(dns, url):
    with pytest.raises(net_guard.BlockedURL):
        net_guard.assert_public_url(url)


@pytest.mark.parametrize("host", ["intranet.example", "metadata.example", "cgnat.example", "ula.example",
                                  "mixed.example", "mapped.example"])
def test_a_public_name_that_resolves_inside_is_refused(dns, host):
    with pytest.raises(net_guard.BlockedURL) as err:
        net_guard.assert_public_url(f"https://{host}/p.jpg")
    assert err.value.code == "blocked_url" and host in str(err.value)


@pytest.mark.parametrize("url", ["http://user@example.com/a.jpg", "http://example.com\\@10.0.0.5/a.jpg",
                                 "http://exa mple.com/a.jpg", "http://example.com\t/a.jpg",
                                 "https://cdn.example.ae/a.jpg\r\nHost: 10.0.0.5"])
def test_an_authority_two_parsers_could_read_differently_is_refused(dns, url):
    with pytest.raises(net_guard.BlockedURL):
        net_guard.assert_public_url(url)


def test_a_public_url_passes_with_its_checked_addresses(dns):
    target = net_guard.assert_public_url("https://cdn.example.ae/img/p.jpg?w=800")
    assert (target.host, target.port, target.addresses) == ("cdn.example.ae", 443, (PUBLIC_TEST_ADDRESS,))
    assert net_guard.assert_public_url("http://cdn.example.ae:8080/x").port == 8080
    assert net_guard.is_public_url("https://8.8.8.8/x") and not net_guard.is_public_url("http://10.0.0.1/")
    assert net_guard.is_public_ip("2606:4700::1111") and net_guard.is_public_ip("::ffff:8.8.8.8")


def test_a_name_that_does_not_resolve_is_a_connection_error_and_is_asked_again(dns):
    for _ in range(2):
        with pytest.raises(net_guard.HostLookupFailed) as err:
            net_guard.assert_public_url("https://gone.example/a.jpg")
        assert isinstance(err.value, ConnectionError) and not isinstance(err.value, net_guard.BlockedURL)
    assert dns.count("gone.example") == 2                    # a failure is not cached
    assert not net_guard.is_public_url("https://gone.example/a.jpg")


def test_dns_answers_are_cached_per_host_for_a_short_time(dns, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(net_guard, "_clock", lambda: now[0])
    for path in ("a.jpg", "b.jpg", "c.jpg"):
        net_guard.assert_public_url(f"https://cdn.example.ae/{path}")
    net_guard.assert_public_url("https://CDN.example.ae/d.jpg")
    assert dns == ["cdn.example.ae"]
    now[0] += net_guard.DNS_TTL_S + 1
    net_guard.assert_public_url("https://cdn.example.ae/e.jpg")
    assert dns == ["cdn.example.ae", "cdn.example.ae"]


def test_a_cached_name_that_turns_private_is_refused_once_its_answer_expires(dns, monkeypatch):
    now = [0.0]
    monkeypatch.setattr(net_guard, "_clock", lambda: now[0])
    net_guard.assert_public_url("https://rebind.example/a.jpg")
    PRIVATE_NOW = fake_getaddrinfo({"rebind.example": "127.0.0.1"})
    monkeypatch.setattr(net_guard, "getaddrinfo", PRIVATE_NOW)
    now[0] += net_guard.DNS_TTL_S + 1
    with pytest.raises(net_guard.BlockedURL):
        net_guard.assert_public_url("https://rebind.example/a.jpg")


def test_every_redirect_hop_is_checked_before_it_is_requested(dns):
    web = Web({"https://shop.example/a.jpg": Resp(302, location="/b.jpg"),
               "https://shop.example/b.jpg": Resp(301, location="http://metadata.example/latest/meta-data/"),
               "http://metadata.example/latest/meta-data/": Resp(200, b"secret", "text/plain")})
    with pytest.raises(net_guard.BlockedURL):
        net_guard.follow("https://shop.example/a.jpg", lambda url, _t: web.get(url))
    assert web.urls == ["https://shop.example/a.jpg", "https://shop.example/b.jpg"]   # the metadata hop never sent
    assert all(web.routes[u].closed for u in web.urls)


def test_redirects_are_followed_to_the_end_and_limited(dns):
    web = Web({"https://a.example/1": Resp(307, location="https://b.example/2"),
               "https://b.example/2": Resp(200, b"ok", "text/plain")})
    resp = net_guard.follow("https://a.example/1", lambda url, _t: web.get(url))
    assert (resp.status_code, resp.url) == (200, "https://b.example/2")
    loop = Web({"https://a.example/loop": Resp(302, location="https://a.example/loop")})
    with pytest.raises(net_guard.TooManyRedirects):
        net_guard.follow("https://a.example/loop", lambda url, _t: loop.get(url), max_redirects=3)
    assert len(loop.urls) == 4
    assert net_guard.follow("https://a.example/x", lambda url, _t: Resp(302)).status_code == 302   # no Location


def test_curl_is_pinned_to_the_checked_addresses(dns):
    from curl_cffi import CurlOpt

    pinned = net_guard.curl_resolve(net_guard.assert_public_url("https://cdn.example.ae/a.jpg"))
    assert pinned == {CurlOpt.RESOLVE: [f"cdn.example.ae:443:{PUBLIC_TEST_ADDRESS}"]}
    v6 = net_guard.Target("https://v6.example/", "v6.example", 80, ("2606:4700::1111",))
    assert net_guard.curl_resolve(v6) == {CurlOpt.RESOLVE: ["v6.example:80:[2606:4700::1111]"]}
    assert net_guard.curl_resolve(net_guard.assert_public_url("https://8.8.8.8/a.jpg")) == {}


# ---------------------------------------------------------------------------
# catalog_match.fetch and pages
# ---------------------------------------------------------------------------

def _cand(url, page=None):
    from catalog_match.models import Candidate

    return Candidate(image_url=url, page_url=page or "", provider="serper_images")


def test_a_candidate_on_an_internal_address_is_never_downloaded_or_retried(dns, monkeypatch, tmp_path):
    from catalog_match import fetch as fetch_mod

    web = Web({})
    monkeypatch.setattr(fetch_mod, "_curl_requests", web)
    monkeypatch.setattr(fetch_mod.settings, "proxy_url", lambda: "http://proxy.example:3128")
    breaker = fetch_mod.HostBreaker()
    urls = ["http://169.254.169.254/latest/meta-data/iam", "https://intranet.example/p.jpg", "http://[::1]/p.jpg"]
    results = fetch_mod.HttpFetcher(store_dir=str(tmp_path), breaker=breaker).fetch([_cand(u) for u in urls])
    assert [(r.ok, r.error) for r in results] == [(False, "blocked_url")] * 3
    assert web.calls == []                              # not directly, not through the proxy
    assert breaker._failures == {} and breaker._until == {}


def test_a_candidate_that_redirects_inside_is_refused_and_a_public_redirect_is_followed(dns, monkeypatch, tmp_path):
    from catalog_match import fetch as fetch_mod

    body = _jpeg()
    web = Web({"https://cdn.example.ae/evil.jpg": Resp(302, location="http://10.0.0.7/admin"),
               "https://cdn.example.ae/old.jpg": Resp(301, location="https://img.example.ae/new.jpg"),
               "https://img.example.ae/new.jpg": Resp(200, body)})
    monkeypatch.setattr(fetch_mod, "_curl_requests", web)
    monkeypatch.setattr(fetch_mod.settings, "proxy_url", lambda: "")
    evil, good = fetch_mod.HttpFetcher(store_dir=str(tmp_path)).fetch(
        [_cand("https://cdn.example.ae/evil.jpg"), _cand("https://cdn.example.ae/old.jpg", "https://shop.ae/p/1")])
    assert (evil.ok, evil.error) == (False, "blocked_url")
    assert good.ok and good.width == 400
    assert "http://10.0.0.7/admin" not in web.urls
    hop = dict(web.calls)["https://img.example.ae/new.jpg"]
    assert hop["allow_redirects"] is False and hop["headers"]["Referer"] == "https://shop.ae/p/1"
    from curl_cffi import CurlOpt

    assert hop["curl_options"] == {CurlOpt.RESOLVE: [f"img.example.ae:443:{PUBLIC_TEST_ADDRESS}"]}


def test_a_download_through_the_proxy_is_checked_but_not_pinned(dns, monkeypatch, tmp_path):
    from catalog_match import fetch as fetch_mod

    body = _jpeg()
    answers = {"n": 0}

    class Flaky(Web):
        def get(self, url, **kwargs):
            answers["n"] += 1
            if answers["n"] == 1:
                self.calls.append((url, kwargs))
                return Resp(403, b"", "text/html")
            return super().get(url, **kwargs)

    web = Flaky({"https://cdn.example.ae/p.jpg": Resp(200, body)})
    monkeypatch.setattr(fetch_mod, "_curl_requests", web)
    monkeypatch.setattr(fetch_mod.settings, "proxy_url", lambda: "http://proxy.example:3128")
    [res] = fetch_mod.HttpFetcher(store_dir=str(tmp_path)).fetch([_cand("https://cdn.example.ae/p.jpg")])
    assert res.ok
    direct, proxied = web.calls
    assert "curl_options" in direct[1] and "curl_options" not in proxied[1] and proxied[1]["proxies"]


def test_a_product_page_that_redirects_to_an_internal_address_is_not_read(dns, monkeypatch):
    from catalog_match import fetch as fetch_mod
    from catalog_match import pages

    pages.clear_cache()
    web = Web({"https://shop.example.ae/p/1": Resp(302, location="http://192.168.1.1/router")})
    monkeypatch.setattr(fetch_mod, "_curl_requests", web)
    monkeypatch.setattr(fetch_mod.settings, "proxy_url", lambda: "")
    info = pages.PageFetcher(bucket_factory=lambda host: type("B", (), {"acquire": lambda self: None})()).fetch_page(
        "https://shop.example.ae/p/1")
    assert (info.ok, info.error) == (False, "blocked_url")
    assert web.urls == ["https://shop.example.ae/p/1"]
    pages.clear_cache()


# ---------------------------------------------------------------------------
# Sitemaps and brand sites
# ---------------------------------------------------------------------------

def test_a_sitemap_request_and_its_redirects_stay_on_public_hosts(dns):
    from catalog_match.sitemaps import SitemapHarvester

    web = Web({"https://store.example.ae/sitemap.xml": Resp(302, location="http://127.0.0.1:8000/sitemap.xml"),
               "https://store.example.ae/robots.txt": Resp(200, b"User-agent: *\nAllow: /\n", "text/plain")})
    harvester = SitemapHarvester(http=web, sleep=lambda s: None, clock=lambda: 0.0)
    assert harvester.get("https://store.example.ae/sitemap.xml") == (None, None, "blocked_url")
    assert harvester.get("https://intranet.example/sitemap.xml") == (None, None, "blocked_url")
    body, status, error = harvester.get("https://store.example.ae/robots.txt")
    assert (status, error) == (200, "") and body.startswith(b"User-agent")
    assert web.urls == ["https://store.example.ae/sitemap.xml", "https://store.example.ae/robots.txt"]
    assert all(kw["allow_redirects"] is False for _u, kw in web.calls)


def test_a_brand_site_whose_name_resolves_inside_is_refused_and_leaves_the_queue(dns):
    from catalog_match import brand_assistant as ba

    class Harvester:
        def __init__(self):
            self.stores = []

        def harvest(self, store, **_kwargs):
            self.stores.append(store.name)
            from catalog_match.sitemaps import HarvestReport

            return HarvestReport(store=store.key, status="error")

    class Index:
        def __init__(self):
            self.finished = []

        def begin_harvest(self, store):
            return 1

        def upsert(self, store, batch):
            pass

        def finish_harvest(self, store, started, report):
            self.finished.append((store, report["status"]))

    harvester, index, removed = Harvester(), Index(), []
    out = ba.harvest_pending(harvester=harvester, db=index, pending=lambda: ["intranet.example", "gone.example"],
                             finish=removed.extend)
    assert [(r["domain"], r["status"]) for r in out] == [("intranet.example", "blocked"), ("gone.example", "error")]
    assert harvester.stores == ["gone.example"]          # the internal site is never asked for its robots.txt
    assert removed == ["intranet.example"] and ("b:intranet.example", "blocked") in index.finished


# ---------------------------------------------------------------------------
# The publish-time download (http_client, image_processor._read_source)
# ---------------------------------------------------------------------------

class Session(Web):
    pass


def test_the_publish_download_refuses_an_internal_url_and_checks_each_redirect(dns, monkeypatch):
    import http_client

    body = _jpeg()
    session = Session({"https://cdn.example.ae/a.jpg": Resp(302, location="https://img.example.ae/a.jpg"),
                       "https://img.example.ae/a.jpg": Resp(200, body),
                       "https://cdn.example.ae/evil.jpg": Resp(302, location="http://[fd00::5]/x")})
    monkeypatch.setattr(http_client, "_new_session", lambda: session)
    client = http_client.ImpersonateClient()
    got = client.fetch_image("https://cdn.example.ae/a.jpg")
    assert got.content == body and got.error is None
    assert all(kw["allow_redirects"] is False for _u, kw in session.calls)
    for url in ("http://169.254.169.254/latest/meta-data/", "https://cdn.example.ae/evil.jpg"):
        refused = client.fetch_image(url)
        assert (refused.content, refused.error, refused.attempts) == (None, "blocked_url", 1)
    assert "http://[fd00::5]/x" not in session.urls and "http://169.254.169.254/latest/meta-data/" not in session.urls


def test_an_approved_url_on_an_internal_address_is_never_downloaded_even_with_a_proxy(dns, monkeypatch):
    import http_client
    import image_processor

    session = Session({})
    monkeypatch.setattr(http_client, "_new_session", lambda: session)
    monkeypatch.setattr(image_processor.settings, "proxy_url", lambda: "http://proxy.example:3128")
    data, error, origin = image_processor._read_source("http://metadata.example/latest/meta-data/")
    assert (data, error, origin) == (None, "download_blocked_url", "download")
    assert session.calls == []


def test_a_refused_url_is_told_to_the_owner_in_plain_arabic_on_both_screens():
    import pathlib

    text = publish_check.download_error_text("download_blocked_url")
    assert text and "آمن" in text and "blocked" not in text
    assert publish_check.fetch_error_word("blocked_url") == "رابط مش آمن"
    core = (pathlib.Path(__file__).resolve().parent.parent / "dashboard" / "public" / "js" / "review" / "core.js")
    source = core.read_text(encoding="utf-8")
    assert f"[/^download_blocked_url$/i, '{text}']" in source            # the same sentence as publish_check
    assert "/^download:blocked_url$/" in source


def test_an_akamai_challenge_page_goes_through_the_proxy_until_an_address_gets_the_image(dns, monkeypatch, tmp_path):
    # Carrefour's CDN answers a non-browser with a 200 empty HTML page and blocks some proxy exit addresses (403)
    from catalog_match import fetch as fetch_mod

    body = _jpeg()
    script = [Resp(200, b"<!DOCTYPE html><html><body><p></p></body></html>", "text/html"),   # direct: challenge
              Resp(403, b"Access Denied", "text/html"),                                       # proxy address 1
              Resp(200, body)]                                                                # proxy address 2

    class Akamai(Web):
        def get(self, url, **kwargs):
            self.calls.append((url, kwargs))
            return script.pop(0)

    web = Akamai({})
    monkeypatch.setattr(fetch_mod, "_curl_requests", web)
    monkeypatch.setattr(fetch_mod.settings, "proxy_url", lambda: "http://proxy.example:3128")
    [res] = fetch_mod.HttpFetcher(store_dir=str(tmp_path)).fetch([_cand("https://cdn.mafr.example/p.jpg")])
    assert res.ok and res.width == 400
    assert [bool(kw.get("proxies")) for _, kw in web.calls] == [False, True, True]


def test_a_challenge_page_without_a_proxy_is_not_an_image(dns, monkeypatch, tmp_path):
    from catalog_match import fetch as fetch_mod

    web = Web({"https://cdn.mafr.example/p.jpg": Resp(200, b"<!DOCTYPE html><html></html>", "text/html")})
    monkeypatch.setattr(fetch_mod, "_curl_requests", web)
    monkeypatch.setattr(fetch_mod.settings, "proxy_url", lambda: "")
    [res] = fetch_mod.HttpFetcher(store_dir=str(tmp_path)).fetch([_cand("https://cdn.mafr.example/p.jpg")])
    assert (res.ok, res.error) == (False, "not_image") and len(web.calls) == 1


def test_the_proxy_gets_at_most_three_tries_for_a_refusal(dns, monkeypatch, tmp_path):
    from catalog_match import fetch as fetch_mod

    class Refuses(Web):
        def get(self, url, **kwargs):
            self.calls.append((url, kwargs))
            return Resp(403, b"Access Denied", "text/html")

    web = Refuses({})
    monkeypatch.setattr(fetch_mod, "_curl_requests", web)
    monkeypatch.setattr(fetch_mod.settings, "proxy_url", lambda: "http://proxy.example:3128")
    [res] = fetch_mod.HttpFetcher(store_dir=str(tmp_path)).fetch([_cand("https://cdn.mafr.example/p.jpg")])
    assert (res.ok, res.error) == (False, "http_403")
    assert len(web.calls) == 1 + fetch_mod.PROXY_ATTEMPTS
