"""verify_cloud_services: the key checker the launcher and the diagnostics page run (offline, requests mocked)."""

import re
from pathlib import Path

import pytest
import requests

import config
import verify_cloud_services as vcs
from catalog_match import verify as cm_verify

ROOT = Path(__file__).resolve().parent.parent


class _Resp:
    def __init__(self, status, payload=None):
        self.status_code = status
        self._payload = payload if payload is not None else {}
        self.text = ""

    def json(self):
        return self._payload


@pytest.fixture
def serper_key(monkeypatch):
    monkeypatch.setattr(config, "SERPER_API_KEY", "serper-test-key", raising=False)


def test_serper_without_key_makes_no_request(monkeypatch):
    monkeypatch.setattr(config, "SERPER_API_KEY", "", raising=False)
    monkeypatch.setattr(requests, "post", lambda *a, **k: pytest.fail("no request without a key"))
    assert vcs.verify_serper() is False


def test_serper_ok_sends_key_in_header_and_uae_locale(monkeypatch, serper_key):
    seen = {}

    def post(url, headers=None, json=None, timeout=None):
        seen.update(url=url, headers=headers, body=json)
        return _Resp(200, {"images": [{"imageUrl": "https://x.ae/a.jpg"}]})

    monkeypatch.setattr(requests, "post", post)
    assert vcs.verify_serper() is True
    assert seen["url"] == "https://google.serper.dev/images"
    assert seen["headers"]["X-API-KEY"] == "serper-test-key"
    assert seen["body"]["gl"] == "ae"


@pytest.mark.parametrize("status,payload", [(401, {}), (403, {}), (429, {}), (500, {}), (200, {"images": []})])
def test_serper_failures(monkeypatch, serper_key, status, payload):
    monkeypatch.setattr(requests, "post", lambda *a, **k: _Resp(status, payload))
    assert vcs.verify_serper() is False


def test_serper_connection_error(monkeypatch, serper_key):
    def boom(*a, **k):
        raise requests.ConnectionError("down")

    monkeypatch.setattr(requests, "post", boom)
    assert vcs.verify_serper() is False


def test_gemini_key_goes_in_the_header_not_the_url(monkeypatch, capsys):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "gemini-secret-key", raising=False)
    monkeypatch.setattr(config, "GEMINI_MODEL", "gemini-3.1-flash-lite", raising=False)
    seen = {}

    def get(url, headers=None, timeout=None):
        seen.update(url=url, headers=headers)
        return _Resp(200)

    monkeypatch.setattr(cm_verify.requests, "get", get)
    assert vcs.verify_gemini() is True
    assert "gemini-secret-key" not in seen["url"]
    assert seen["headers"]["x-goog-api-key"] == "gemini-secret-key"
    assert "gemini-secret-key" not in capsys.readouterr().out


@pytest.mark.parametrize("status", [404, 403, 429])
def test_gemini_failures(monkeypatch, status):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "k", raising=False)
    monkeypatch.setattr(cm_verify.requests, "get", lambda *a, **k: _Resp(status))
    assert vcs.verify_gemini() is False


def test_google_cse_is_optional(monkeypatch):
    monkeypatch.setattr(config, "GOOGLE_SEARCH_API_KEYS", [], raising=False)
    monkeypatch.setattr(config, "GOOGLE_SEARCH_CX_LIST", [], raising=False)
    assert vcs.verify_google_search() is None


def test_diagnostics_page_shows_serper_and_no_fabricated_panel():
    page = (ROOT / "dashboard/resources/views/dashboard/diagnostics.blade.php").read_text(encoding="utf-8")
    assert 'id="card-serper"' in page and 'id="ind-serper"' in page and 'id="text-serper"' in page
    for fabricated in ("98.4%", "Next-Gen Frontiers", "CIEDE2000", "Speculative Search"):
        assert fabricated not in page


def test_printed_errors_never_contain_keys(monkeypatch, capsys):
    monkeypatch.setattr(config, "GOOGLE_SEARCH_API_KEYS", ["AIzaLEAKCHECK0123456789"], raising=False)
    monkeypatch.setattr(config, "GOOGLE_SEARCH_CX_LIST", ["cx-123456"], raising=False)

    def boom(url, params=None, timeout=None, **k):
        raise requests.ConnectionError(f"Max retries exceeded with url: {url}?key={params['key']}&cx={params['cx']}")

    monkeypatch.setattr(requests, "get", boom)
    assert vcs.verify_google_search() is False
    out = capsys.readouterr().out
    assert "AIzaLEAKCHECK0123456789" not in out and "AIzaLEAK" not in out
    assert "[REDACTED]" in out


def test_proxy_credentials_are_never_printed(monkeypatch, capsys):
    monkeypatch.setattr(config, "PROXY_URL", "http://staffuser:s3cretpass@proxy.example.com:8080", raising=False)

    def boom(*a, **k):
        raise requests.ConnectionError("ProxyError('Cannot connect to proxy http://staffuser:s3cretpass@proxy.example.com:8080')")

    monkeypatch.setattr(requests, "get", boom)
    assert vcs.verify_proxy() is False
    out = capsys.readouterr().out
    assert "s3cretpass" not in out and "staffuser" not in out
    # the host is still shown, so the owner knows which proxy failed
    assert re.search(r"\bproxy\.example\.com:8080\b", out)
