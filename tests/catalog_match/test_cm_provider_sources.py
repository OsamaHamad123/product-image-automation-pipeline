"""Expansion-round providers (sources package): Serper web search, Serper Shopping, visual search.

The HTTP layer is a recording fake that returns saved response bodies written in the
shapes the services document (fixtures/providers/serper_search_ok.json,
serper_shopping_ok.json, serper_lens_ok.json, serpapi_lens_ok.json); sockets are blocked.
"""

import hashlib
import json
import logging
import re
import socket
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from catalog_match import ratelimit, settings
from catalog_match.models import PlannedQuery, ProviderResult, SkuSpec
from catalog_match.providers import lens as lens_mod
from catalog_match.providers.lens import (
    SERPAPI_URL, SERPER_LENS_URL, SerpApiLensProvider, SerperLensProvider, VisualSearch, build_visual_search,
)
from catalog_match.providers.serper import SerperImagesProvider
from catalog_match.providers.serper_shopping import (
    SERPER_SHOPPING_URL, SerperShoppingProvider, store_domain, unwrap_link,
)
from catalog_match.providers.serper_web import SERPER_SEARCH_URL, SerperWebProvider, site_query
from catalog_match.retrieve import Retriever

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "providers"
SPEC = SkuSpec(raw_name="BARTS TRADITON FRIES 1KG", brand_raw="BARTS", brand_canonical="Barts")
def _fake_key(tag: str) -> str:
    """A recognisable stand-in key built when the tests run, so the source holds no key-shaped literal for
    secret scanners to report."""
    return f"{tag}-{hashlib.sha256(tag.encode()).hexdigest()[:6]}"


KEY = _fake_key("test-serper")
SERPAPI_STANDIN = _fake_key("test-serpapi")


def _on_host(url: str, domain: str) -> bool:
    """The URL's host is the domain or one of its subdomains (compared by host, not by substring)."""
    host = (urlsplit(url).hostname or "").lower()
    return host == domain or host.endswith("." + domain)


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError(f"network access attempted: {args!r}")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    monkeypatch.setattr(settings, "_config", None)
    for name in ("SERPER_API_KEY", "SERPAPI_API_KEY", "VISUAL_SEARCH"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(SerperImagesProvider, "operators_blocked", False)
    lens_mod.reset_run_state()
    yield
    lens_mod.reset_run_state()


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FakeResponse:
    def __init__(self, status_code=200, body=None, text=None):
        self.status_code = status_code
        self._body = body
        self.text = text if text is not None else (json.dumps(body) if body is not None else "")

    def json(self):
        if self._body is None:
            return json.loads(self.text)
        return self._body


class FakeSession:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def _next(self, method, url, kwargs):
        self.calls.append((method, url, kwargs))
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    def post(self, url, **kwargs):
        return self._next("POST", url, kwargs)

    def get(self, url, **kwargs):
        return self._next("GET", url, kwargs)


def web(session):
    return SerperWebProvider(api_key=KEY, session=session, bucket=ratelimit.UNLIMITED)


def shopping(session):
    return SerperShoppingProvider(api_key=KEY, session=session, bucket=ratelimit.UNLIMITED)


def serper_lens(session):
    return SerperLensProvider(api_key=KEY, session=session, bucket=ratelimit.UNLIMITED)


def serpapi(session, key=SERPAPI_STANDIN):
    return SerpApiLensProvider(api_key=key, session=session, bucket=ratelimit.UNLIMITED)


# ---------------------------------------------------------------------------
# Serper web search (product pages)
# ---------------------------------------------------------------------------

def test_site_query():
    assert site_query("BARTS FRIES 1kg", ["barts.com", "luluhypermarket.com", "barts.com"]) == \
        "BARTS FRIES 1kg (site:barts.com OR site:luluhypermarket.com)"
    assert site_query("  BARTS   FRIES ", []) == "BARTS FRIES"


def test_web_request_and_page_links():
    session = FakeSession(FakeResponse(200, load("serper_search_ok.json")))
    query = site_query("BARTS TRADITON FRIES 1kg", ["luluhypermarket.com", "carrefouruae.com"])
    res = web(session).search(query, "en", SPEC)

    method, url, kwargs = session.calls[0]
    assert (method, url) == ("POST", SERPER_SEARCH_URL)
    assert kwargs["headers"]["X-API-KEY"] == KEY
    assert kwargs["json"] == {"q": query, "gl": "ae", "hl": "en", "num": 10}
    assert res.status == "ok" and res.provider == "serper_web"
    # two real links; the result without a link and the javascript: link are dropped
    assert [c.page_url for c in res.candidates] == [
        "https://www.luluhypermarket.com/en-ae/barts-traditional-fries-1kg/p/88231",
        "https://www.carrefouruae.com/mafuae/en/frozen-potato/barts-traditional-fries-2-5kg/p/77812"]
    first = res.candidates[0]
    assert first.image_url == ""                     # a result thumbnail is never an image candidate
    assert first.title == "Barts Traditional Fries 1kg Online at Best Price | Lulu UAE"
    assert first.snippet.startswith("Buy Barts Traditional Fries 1kg")
    assert first.domain == "luluhypermarket.com" and first.rank == 1 and first.sanctioned


def test_web_site_refused_is_retried_plain_and_learned(monkeypatch):
    refused = FakeResponse(400, text='{"message":"Query pattern not allowed for free accounts","statusCode":400}')
    session = FakeSession(refused, FakeResponse(200, load("serper_search_ok.json")),
                          FakeResponse(200, load("serper_search_ok.json")))
    p = web(session)
    query = site_query("BARTS FRIES 1kg", ["luluhypermarket.com"])
    assert p.search(query, "en", SPEC).status == "ok"
    assert session.calls[1][2]["json"]["q"] == "BARTS FRIES 1kg UAE"
    # learned for the account: the images provider and the next web query send the plain form directly
    assert SerperImagesProvider.operators_blocked is True
    p.search(query, "en", SPEC)
    assert session.calls[2][2]["json"]["q"] == "BARTS FRIES 1kg UAE" and len(session.calls) == 3


@pytest.mark.parametrize("status,text,expected", [
    (400, '{"message":"Not enough credits"}', "quota"),
    (429, "Too many requests", "quota"),
    (403, '{"message":"Unauthorized."}', "error"),
])
def test_web_http_errors(status, text, expected):
    res = web(FakeSession(FakeResponse(status, text=text))).search("BARTS FRIES", "en", SPEC)
    assert res.status == expected and res.http_status == status and res.candidates == []


def test_web_without_key_makes_no_call():
    session = FakeSession()
    res = SerperWebProvider(api_key="", session=session, bucket=ratelimit.UNLIMITED).search("x", "en", SPEC)
    assert res.status == "error" and "SERPER_API_KEY" in res.error and session.calls == []


def test_expansion_providers_are_never_sent_planned_queries():
    class Primary:
        name, sanctioned, kind = "serper", True, "search"

        def search(self, query, hl, spec):
            return ProviderResult(provider="serper", status="empty")

    session = FakeSession()
    r = Retriever(SPEC, [Primary(), web(session), shopping(session)])
    eligible = r._eligible(PlannedQuery(query_id="Q1", text="BARTS FRIES 1kg"))
    assert [p.name for p in eligible] == ["serper"]


# ---------------------------------------------------------------------------
# Serper Shopping
# ---------------------------------------------------------------------------

def test_shopping_request_and_listings():
    session = FakeSession(FakeResponse(200, load("serper_shopping_ok.json")))
    res = shopping(session).search("MEHRAN PARATHA 400g", "en", SPEC)
    method, url, kwargs = session.calls[0]
    assert (method, url) == ("POST", SERPER_SHOPPING_URL)
    assert kwargs["json"]["gl"] == "ae" and kwargs["json"]["q"] == "MEHRAN PARATHA 400g"
    assert res.status == "ok" and res.provider == "serper_shopping"
    by_rank = {c.rank: c for c in res.candidates}
    assert sorted(by_rank) == [1, 2, 3]              # no title / nothing to follow are dropped
    carrefour = by_rank[1]
    assert carrefour.page_url == "https://www.carrefouruae.com/mafuae/en/frozen-bread/mehran-plain-paratha-400g/p/631098"
    assert carrefour.domain == "carrefouruae.com" and carrefour.snippet == "Carrefour UAE"
    assert _on_host(carrefour.image_url, "gstatic.com")   # a thumbnail: the expansion round follows the page
    amazon = by_rank[2]
    # the full-size Amazon image, its overlay/resize modifiers removed (providers.base.canonical_image_url)
    assert amazon.image_url == "https://m.media-amazon.com/images/I/71abcDEF12L.jpg"
    assert (amazon.width, amazon.height) == (1500, 1500) and amazon.sanctioned
    google_page = by_rank[3]
    assert google_page.page_url == "" and google_page.domain == ""   # a google.com page is not a store page


def test_shopping_domain_is_the_page_host_never_the_store_label():
    # Google labels the store 'Carrefour', but the page is Carrefour Saudi Arabia: the review page shows
    # this domain, so it must be the page's own host (the store-name map only fills a listing with no page)
    body = {"shopping": [
        {"title": "Mehran Plain Paratha 400g", "source": "Carrefour", "position": 1,
         "link": "https://www.carrefourksa.com/mafsau/en/frozen-bread/mehran-paratha-400g/p/1",
         "imageUrl": "https://encrypted-tbn2.gstatic.com/shopping?q=tbn:a"},
        {"title": "Mehran Plain Paratha 400g", "source": "Carrefour UAE", "position": 2,
         "link": "https://www.google.com/shopping/product/1", "imageUrl": "https://cdn.example.ae/p/m.jpg",
         "imageWidth": 1000, "imageHeight": 1000}]}
    res = shopping(FakeSession(FakeResponse(200, body))).search("MEHRAN PARATHA 400g", "en", SPEC)
    ksa, no_page = sorted(res.candidates, key=lambda c: c.rank)
    assert ksa.domain == "carrefourksa.com" and ksa.snippet == "Carrefour"
    assert no_page.page_url == "" and no_page.domain == "carrefouruae.com"


@pytest.mark.parametrize("source,domain", [
    ("Carrefour UAE", "carrefouruae.com"), ("Amazon.ae - Al Noor Trading", "amazon.ae"),
    ("Lulu Hypermarket", "luluhypermarket.com"), ("noon", "noon.com"), ("Some Store", ""), ("", ""),
    ("Amazon.com", ""),
])
def test_store_domain(source, domain):
    assert store_domain(source) == domain


def test_unwrap_link():
    assert unwrap_link("https://www.google.com/url?url=https://www.noon.com/p/1&rct=j") == "https://www.noon.com/p/1"
    assert unwrap_link("https://www.googleadservices.com/pagead/aclk?adurl=https://www.lulu.ae/x") == \
        "https://www.lulu.ae/x"
    assert unwrap_link("https://www.google.ae/shopping/product/123") == ""
    assert unwrap_link("https://www.talabat.com/uae/x") == "https://www.talabat.com/uae/x"
    assert unwrap_link("javascript:void(0)") == ""
    # a host that only ends like Google's ad redirect is a store link, not a redirect to unwrap
    assert unwrap_link("https://evilgoogleadservices.com/aclk?adurl=https://www.lulu.ae/x") == \
        "https://evilgoogleadservices.com/aclk?adurl=https://www.lulu.ae/x"
    assert unwrap_link("https://googleadservices.com/aclk?adurl=https://www.lulu.ae/x") == "https://www.lulu.ae/x"


# ---------------------------------------------------------------------------
# Visual search: Serper Lens and SerpApi Google Lens
# ---------------------------------------------------------------------------

SEED = "https://img.example-cdn.com/barts-small.jpg"


def test_serper_lens_request_and_matches():
    session = FakeSession(FakeResponse(200, load("serper_lens_ok.json")))
    res = serper_lens(session).search(SEED, "en", SPEC)
    method, url, kwargs = session.calls[0]
    assert (method, url) == ("POST", SERPER_LENS_URL)
    assert kwargs["json"] == {"url": SEED, "gl": "ae", "hl": "en"}
    assert kwargs["headers"]["X-API-KEY"] == KEY
    assert res.status == "ok" and res.provider == "lens_serper"
    assert [c.rank for c in res.candidates] == [1, 2, 5]     # untitled and malformed matches dropped
    lulu, fb, talabat = res.candidates
    assert lulu.image_url.endswith("barts-traditional-fries-1kg-1500x1500.jpg")
    assert lulu.page_url.startswith("https://www.luluhypermarket.com/") and lulu.domain == "luluhypermarket.com"
    assert lulu.snippet == "Lulu Hypermarket" and lulu.sanctioned
    assert _on_host(fb.image_url, "gstatic.com") and _on_host(talabat.image_url, "gstatic.com")   # thumbnails only


@pytest.mark.parametrize("status,text,unsupported,expected", [
    (404, "Not Found", True, "error"),
    (403, '{"message":"Lens is not available on your plan. Please upgrade."}', True, "error"),
    (400, '{"message":"Not enough credits"}', False, "quota"),
    (403, '{"message":"Unauthorized."}', False, "error"),
])
def test_serper_lens_unsupported_plan_is_learned(status, text, unsupported, expected):
    res = serper_lens(FakeSession(FakeResponse(status, text=text))).search(SEED, "en", SPEC)
    assert res.status == expected
    assert SerperLensProvider.unsupported is unsupported


def test_serpapi_request_parse_and_key_stays_out_of_results():
    session = FakeSession(FakeResponse(200, load("serpapi_lens_ok.json")))
    res = serpapi(session).search(SEED, "en", SPEC)
    method, url, kwargs = session.calls[0]
    assert (method, url) == ("GET", SERPAPI_URL)
    assert kwargs["params"] == {"engine": "google_lens", "url": SEED, "hl": "en", "country": "ae",
                                "api_key": SERPAPI_STANDIN}
    assert res.status == "ok" and res.provider == "lens_serpapi"
    car, noon = res.candidates
    # the full image, without the Carrefour resize policy
    assert car.image_url == "https://cdn.mafrservices.com/sys-master-root/h1/h2/77811_main.jpg"
    assert (car.width, car.height) == (1000, 1000) and car.domain == "carrefouruae.com"
    assert noon.image_url.startswith("https://encrypted-tbn1.gstatic.com/") and noon.snippet == "noon"
    assert (noon.width, noon.height) == (200, 200)


@pytest.mark.parametrize("body,expected", [
    ({"error": "Google Lens hasn't returned any results for this query."}, "empty"),
    ({"error": "Your account has run out of searches."}, "quota"),
    ({"error": "Something else went wrong."}, "error"),
])
def test_serpapi_error_bodies(body, expected):
    res = serpapi(FakeSession(FakeResponse(200, body))).search(SEED, "en", SPEC)
    assert res.status == expected


def test_serpapi_never_leaks_the_key(caplog):
    caplog.set_level(logging.DEBUG)
    leaky = ConnectionError(f"HTTPSConnectionPool(host='serpapi.com'): Max retries exceeded with url: "
                            f"/search.json?engine=google_lens&api_key={SERPAPI_STANDIN}&url=x")
    timeout = type("ReadTimeout", (Exception,), {})(f"read timed out (api_key={SERPAPI_STANDIN})")
    bad_key = FakeResponse(401, text=f'{{"error": "Invalid API key {SERPAPI_STANDIN}"}}')
    results = [serpapi(FakeSession(r)).search(SEED, "en", SPEC) for r in (leaky, timeout, bad_key)]
    assert [r.status for r in results] == ["error", "error", "error"]
    assert results[1].error == "timeout"
    blob = json.dumps([r.__dict__ for r in results], default=str) + caplog.text
    assert SERPAPI_STANDIN not in blob
    assert "[REDACTED]" in results[0].error


def test_build_visual_search_modes(monkeypatch):
    assert build_visual_search("off", KEY, SERPAPI_STANDIN) is None
    assert build_visual_search("auto", "", "") is None
    assert build_visual_search("serpapi", KEY, "") is None
    assert build_visual_search("auto", KEY, "").names == ["lens_serper"]
    assert build_visual_search("auto", KEY, SERPAPI_STANDIN).names == ["lens_serper", "lens_serpapi"]
    assert build_visual_search("serpapi", KEY, SERPAPI_STANDIN).names == ["lens_serpapi"]
    # from settings: an unknown mode is 'off', never a surprise paid call
    monkeypatch.setenv("SERPER_API_KEY", KEY)
    monkeypatch.setenv("VISUAL_SEARCH", "bogus")
    assert build_visual_search() is None
    monkeypatch.setenv("VISUAL_SEARCH", "auto")
    assert build_visual_search().names == ["lens_serper"]


def test_visual_search_falls_back_to_serpapi_and_remembers_the_plan():
    s1 = FakeSession(FakeResponse(404, text="Not Found"))
    s2 = FakeSession(FakeResponse(200, load("serpapi_lens_ok.json")), FakeResponse(200, load("serpapi_lens_ok.json")))
    vs = VisualSearch([serper_lens(s1), serpapi(s2)])
    first = vs.search(SEED, SPEC, query_id="X3")
    assert [(r.provider, r.status, r.query_id) for r in first] == [("lens_serper", "error", "X3"),
                                                                    ("lens_serpapi", "ok", "X3")]
    assert vs.next_backend() == "lens_serpapi"
    second = vs.search(SEED, SPEC, query_id="X4")
    assert [r.provider for r in second] == ["lens_serpapi"] and len(s1.calls) == 1


def test_visual_search_stops_at_the_first_answer():
    s1 = FakeSession(FakeResponse(200, {"organic": []}))
    s2 = FakeSession()
    vs = VisualSearch([serper_lens(s1), serpapi(s2)])
    assert [(r.provider, r.status) for r in vs.search(SEED, SPEC)] == [("lens_serper", "empty")]
    assert s2.calls == []


def test_http_client_debug_logs_never_show_the_serpapi_key(caplog):
    caplog.set_level(logging.DEBUG)
    serpapi(FakeSession())                      # building the provider guards the HTTP client loggers
    logging.getLogger("urllib3.connectionpool").debug(
        '%s://%s:%s "%s %s %s" %s %s', "https", "serpapi.com", 443, "GET",
        f"/search.json?engine=google_lens&api_key={SERPAPI_STANDIN}&url=x", "HTTP/1.1", 200, 512)
    assert re.search(r"\bserpapi\.com\b", caplog.text) and SERPAPI_STANDIN not in caplog.text
