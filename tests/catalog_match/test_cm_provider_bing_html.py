"""catalog_match.providers.bing_html: the unsanctioned fallback scraper (D7, SRC-4).

Parses saved pages (fixtures/providers/bing_*.html); sockets are blocked. The
legacy bugs are pinned: 'desc' read as the title, the query used as a missing
title, the page URL dropped and 800x800 invented, a captcha page read as 'empty'.
"""

import socket
from pathlib import Path

import pytest

from catalog_match import ratelimit
from catalog_match.models import SkuSpec
from catalog_match.providers.bing_html import BING_IMAGES_URL, BingHtmlProvider, parse_html

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "providers"
QUERY = "Almarai Fresh Milk Full Fat 1L"
SPEC = SkuSpec(raw_name=QUERY, brand_raw="Almarai", brand_canonical="Almarai")


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def _blocked(*args, **kwargs):
        raise RuntimeError("network access is blocked in catalog_match unit tests")

    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)
    monkeypatch.setattr(socket, "getaddrinfo", _blocked)


class FakeResponse:
    def __init__(self, status_code, text, url="https://www.bing.com/images/search?q=x"):
        self.status_code = status_code
        self.text = text
        self.url = url


class FakeSession:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


def page(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


def search(response, hl="en"):
    session = FakeSession(response)
    p = BingHtmlProvider(session=session, bucket=ratelimit.UNLIMITED, proxy_url="")
    return p.search(QUERY, hl, SPEC), session


def test_parse_t_desc_purl():
    res, session = search(FakeResponse(200, page("bing_images_ok.html")))
    assert res.status == "ok"
    # Five a.iusc: one has no murl and one has broken JSON; three candidates remain.
    assert len(res.candidates) == 3
    first, no_text, desc_only = res.candidates

    assert first.title == "Almarai Fresh Milk Full Fat 1L | Carrefour UAE"      # 't', not 'desc'
    assert first.page_title == first.title
    assert first.snippet == "Buy Almarai Fresh Milk Full Fat 1L online at the best price"
    assert first.page_url == "https://www.carrefouruae.com/mafuae/en/fresh-milk/almarai-fresh-milk-full-fat-1l/p/12345"
    assert first.image_url == "https://cdn.mafrservices.com/sys-master-root/h9a/h1c/51234567890014/12345_main.jpg"
    assert first.domain == "carrefouruae.com"
    assert (first.width, first.height) == (1200, 1200)                          # real expw/exph from the link

    assert no_text.title == ""                  # neither t nor desc: never the query
    assert no_text.title != QUERY and no_text.page_title == ""
    assert no_text.page_url == "https://www.example-grocer.ae/product/98765"
    assert no_text.width is None and no_text.height is None                    # no expw/exph: unknown, not 800

    assert desc_only.title == "Almarai Full Fat Fresh Milk 1 Litre"
    assert desc_only.page_title == ""
    assert (desc_only.width, desc_only.height) == (800, 800)

    for c in res.candidates:
        assert c.sanctioned is False
        assert c.provider == "bing_html"
    assert [c.rank for c in res.candidates] == [1, 2, 3]

    url, kwargs = session.calls[0]
    assert url == BING_IMAGES_URL
    assert kwargs["params"]["q"] == QUERY
    assert kwargs["params"]["cc"] == "AE"


def test_captcha_is_blocked():
    res, _ = search(FakeResponse(200, page("bing_captcha.html")))
    assert res.status == "blocked"
    assert res.status != "empty"
    assert res.candidates == []


def test_redirect_to_a_challenge_url_is_blocked():
    res, _ = search(FakeResponse(200, "<html><body>Please wait</body></html>",
                                 url="https://www.bing.com/challenge/verify?x=1"))
    assert res.status == "blocked"


def test_results_page_mentioning_consent_is_not_blocked():
    # The OK fixture carries a cookie-consent banner; results present means 'ok'.
    html = page("bing_images_ok.html")
    assert "consent" in html.lower()
    assert len(parse_html(html)) == 3


def test_page_without_results_or_markers_is_empty():
    html = "<html><body><div id='b_content'><h2>There are no results for this search</h2></div></body></html>"
    res, _ = search(FakeResponse(200, html))
    assert res.status == "empty"


def test_http_errors_map_to_health():
    res, _ = search(FakeResponse(429, "Too Many Requests"))
    assert res.status == "quota"
    res, _ = search(FakeResponse(403, "Forbidden"))
    assert res.status == "error" and res.http_status == 403


def test_arabic_queries_ask_for_arabic():
    _, session = search(FakeResponse(200, page("bing_images_ok.html")), hl="ar")
    assert session.calls[0][1]["params"]["setlang"] == "ar"


def test_fallback_flag():
    assert BingHtmlProvider(fallback=True).fallback is True
    assert BingHtmlProvider().fallback is False
    assert BingHtmlProvider.sanctioned is False
