"""catalog_match.providers.serper: Serper.dev Google Images (D7).

The HTTP layer is replaced by a recording fake that returns saved response bodies
(fixtures/providers/serper_images_*.json); sockets are blocked.
"""

import json
import logging
import socket
from pathlib import Path

import pytest
import requests

from catalog_match import ratelimit
from catalog_match.models import SkuSpec
from catalog_match.providers import serper as serper_mod
from catalog_match.providers.serper import SERPER_IMAGES_URL, SerperImagesProvider

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "providers"
SPEC = SkuSpec(raw_name="Almarai Fresh Milk Full Fat 1L", brand_raw="Almarai", brand_canonical="Almarai")


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def _blocked(*args, **kwargs):
        raise RuntimeError("network access is blocked in catalog_match unit tests")

    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)
    monkeypatch.setattr(socket, "getaddrinfo", _blocked)


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
    def __init__(self, response=None, exc=None):
        self.response = response
        self.exc = exc
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.exc is not None:
            raise self.exc
        return self.response


def provider(session, key="test-serper-key"):
    return SerperImagesProvider(api_key=key, session=session, bucket=ratelimit.UNLIMITED)


def test_parse_ok():
    session = FakeSession(FakeResponse(200, load("serper_images_ok.json")))
    res = provider(session).search("Almarai Fresh Milk Full Fat 1L", "en", SPEC)

    assert res.status == "ok"
    assert res.http_status == 200
    assert res.provider == "serper"
    # The item without imageUrl is dropped; the other four keep their page evidence.
    assert len(res.candidates) == 4
    first = res.candidates[0]
    assert first.image_url == "https://cdn.mafrservices.com/sys-master-root/h9a/h1c/51234567890014/12345_main.jpg"
    assert first.page_url == "https://www.carrefouruae.com/mafuae/en/fresh-milk/almarai-fresh-milk-full-fat-1l/p/12345"
    assert first.page_title == "Almarai Fresh Milk Full Fat 1L | Carrefour UAE"
    assert first.title == first.page_title
    assert first.domain == "carrefouruae.com"
    assert (first.width, first.height) == (1200, 1200)
    assert first.rank == 1
    assert first.sanctioned is True
    assert first.provider == "serper"
    for c in res.candidates:
        assert c.page_url and c.page_title and c.domain

    lulu = res.candidates[2]
    assert lulu.domain == "luluhypermarket.com"          # no 'domain' in the item: taken from the link host
    blog = res.candidates[3]
    assert blog.width is None and blog.height is None    # not reported: never invented
    assert [c.rank for c in res.candidates] == [1, 2, 3, 4]

    # The request: POST to the images endpoint, API key in the header, gl=ae, hl passed through.
    assert len(session.calls) == 1
    url, kwargs = session.calls[0]
    assert url == SERPER_IMAGES_URL
    assert kwargs["headers"]["X-API-KEY"] == "test-serper-key"
    assert kwargs["json"] == {"q": "Almarai Fresh Milk Full Fat 1L", "gl": "ae", "hl": "en", "num": 20}
    assert kwargs["timeout"] == 15

    session_ar = FakeSession(FakeResponse(200, load("serper_images_ok.json")))
    provider(session_ar).search("المراعي حليب طازج 1 لتر", "ar", SPEC)
    assert session_ar.calls[0][1]["json"]["hl"] == "ar"
    assert session_ar.calls[0][1]["json"]["gl"] == "ae"


def test_default_http_path_uses_requests(monkeypatch):
    """Without an injected session the provider posts through requests (mocked here)."""
    calls = []

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return FakeResponse(200, load("serper_images_ok.json"))

    monkeypatch.setattr(serper_mod.requests, "post", fake_post)
    res = SerperImagesProvider(api_key="k", bucket=ratelimit.UNLIMITED).search("q", "en", SPEC)
    assert res.status == "ok" and len(res.candidates) == 4
    assert calls[0][1]["headers"]["X-API-KEY"] == "k"


def test_site_operator_passes_through():
    session = FakeSession(FakeResponse(200, load("serper_images_ok.json")))
    q = "Almarai Full Cream Milk 1L site:carrefouruae.com"
    provider(session).search(q, "en", SPEC)
    assert session.calls[0][1]["json"]["q"] == q


@pytest.mark.parametrize(
    "response, exc, status, http_status, error",
    [
        (FakeResponse(429, text='{"message":"Too many requests"}'), None, "quota", 429, "http_429"),
        (FakeResponse(400, text='{"message":"Not enough credits","statusCode":400}'), None, "quota", 400, "http_400"),
        (FakeResponse(403, text='{"message":"Unauthorized."}'), None, "error", 403, "http_403"),
        (FakeResponse(401, text='{"message":"Unauthorized."}'), None, "error", 401, "http_401"),
        (FakeResponse(500, text="Internal Server Error"), None, "error", 500, "http_500"),
        (None, requests.exceptions.ReadTimeout("read timed out"), "error", None, "timeout"),
        (None, requests.exceptions.ConnectionError("connection refused"), "error", None, None),
        (FakeResponse(200, text="<html>not json</html>"), None, "error", None, None),
    ],
)
def test_status_mapping(response, exc, status, http_status, error):
    session = FakeSession(response, exc)
    res = provider(session).search("Almarai Fresh Milk 1L", "en", SPEC)  # must not raise
    assert res.status == status
    assert res.http_status == http_status
    assert res.candidates == []
    if error is not None:
        assert res.error == error
    else:
        assert res.error  # an explanation is always recorded


def test_empty_images_is_empty_not_ok():
    session = FakeSession(FakeResponse(200, load("serper_images_empty.json")))
    res = provider(session).search("Almarai Camel Milk Mango 7L", "en", SPEC)
    assert res.status == "empty"
    assert res.candidates == []
    assert res.error is None


def test_missing_key_is_error_without_http_call():
    session = FakeSession(FakeResponse(200, load("serper_images_ok.json")))
    res = provider(session, key="").search("q", "en", SPEC)
    assert res.status == "error"
    assert session.calls == []


def test_one_structured_log_line_and_no_print(caplog, capsys):
    session = FakeSession(FakeResponse(200, load("serper_images_ok.json")))
    with caplog.at_level(logging.INFO, logger="catalog_match.providers"):
        provider(session).search("Almarai Fresh Milk 1L", "en", SPEC)
    lines = [r.getMessage() for r in caplog.records if r.name == "catalog_match.providers"]
    assert len(lines) == 1
    assert "provider=serper" in lines[0] and "status=ok" in lines[0]
    assert "count=4" in lines[0] and "latency_ms=" in lines[0]
    assert capsys.readouterr().out == ""
