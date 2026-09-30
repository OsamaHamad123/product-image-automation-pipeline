"""catalog_match.providers.cse_legacy: the legacy Google CSE adapter (D7, SRC-11).

Saved body in fixtures/providers/cse_ok.json; dates are injected; sockets are blocked.
"""

import datetime as dt
import json
import logging
import socket
from pathlib import Path

import pytest

from catalog_match import ratelimit
from catalog_match.models import SkuSpec
from catalog_match.providers.cse_legacy import CSE_URL, CseLegacyProvider

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "providers"
SPEC = SkuSpec(raw_name="Almarai Fresh Milk Full Fat 1L", brand_raw="Almarai", brand_canonical="Almarai")
BEFORE = dt.date(2026, 9, 30)
SUNSET = dt.date(2026, 12, 31)
AFTER = dt.date(2027, 1, 1)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def _blocked(*args, **kwargs):
        raise RuntimeError("network access is blocked in catalog_match unit tests")

    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)
    monkeypatch.setattr(socket, "getaddrinfo", _blocked)


class FakeResponse:
    def __init__(self, status_code, body=None, text=None):
        self.status_code = status_code
        self._body = body
        self.text = text if text is not None else json.dumps(body)

    def json(self):
        return self._body


class FakeSession:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


def ok_body():
    return json.loads((FIXTURES / "cse_ok.json").read_text(encoding="utf-8"))


def make(session, keys=("key-1",), cxs=("cx-1",)):
    return CseLegacyProvider.create(list(keys), list(cxs), today=BEFORE, sunset=SUNSET,
                                    session=session, bucket=ratelimit.UNLIMITED)


def test_not_constructed_without_key_or_after_sunset():
    assert CseLegacyProvider.create([], ["cx-1"], today=BEFORE, sunset=SUNSET) is None
    assert CseLegacyProvider.create(["key-1"], [], today=BEFORE, sunset=SUNSET) is None
    assert CseLegacyProvider.create(["  "], ["cx-1"], today=BEFORE, sunset=SUNSET) is None
    assert CseLegacyProvider.create(["key-1"], ["cx-1"], today=AFTER, sunset=SUNSET) is None
    with pytest.raises(ValueError):
        CseLegacyProvider(["key-1"], ["cx-1"], today=AFTER, sunset=SUNSET)
    with pytest.raises(ValueError):
        CseLegacyProvider([], ["cx-1"], today=BEFORE, sunset=SUNSET)
    # The sunset day itself is still allowed.
    assert CseLegacyProvider.create(["key-1"], ["cx-1"], today=SUNSET, sunset=SUNSET) is not None


def test_sunset_comes_from_settings_by_default(monkeypatch):
    from catalog_match import settings
    monkeypatch.setattr(settings, "_config", None)
    monkeypatch.setenv("CSE_SUNSET_DATE", "2026-10-15")
    assert settings.cse_sunset_date() == dt.date(2026, 10, 15)
    assert CseLegacyProvider.create(["k"], ["cx"], today=dt.date(2026, 10, 15)) is not None
    assert CseLegacyProvider.create(["k"], ["cx"], today=dt.date(2026, 10, 16)) is None


def test_request_params_and_mapping():
    session = FakeSession(FakeResponse(200, ok_body()))
    res = make(session).search("Almarai Fresh Milk Full Fat 1L", "en", SPEC)

    url, kwargs = session.calls[0]
    assert url == CSE_URL
    params = kwargs["params"]
    assert "imgSize" not in params
    assert "fileType" not in params
    assert params["q"] == "Almarai Fresh Milk Full Fat 1L"
    assert params["cx"] == "cx-1" and params["key"] == "key-1"
    assert params["searchType"] == "image"
    assert params["num"] == 10
    assert params["gl"] == "ae"
    assert params["cr"] == "countryAE"

    assert res.status == "ok"
    assert len(res.candidates) == 2
    c = res.candidates[0]
    assert c.page_url == "https://www.carrefouruae.com/mafuae/en/fresh-milk/almarai-fresh-milk-full-fat-1l/p/12345"
    assert c.image_url == "https://cdn.mafrservices.com/sys-master-root/h9a/h1c/51234567890014/12345_main.jpg"
    assert c.domain == "carrefouruae.com"
    assert c.page_title == "Almarai Fresh Milk Full Fat 1L | Carrefour UAE"
    assert (c.width, c.height) == (1200, 1200)
    assert c.provider == "cse_legacy" and c.sanctioned is True
    assert res.candidates[1].domain == "talabat.com"


def test_non_200_is_logged_with_body_and_keys_rotate(caplog):
    quota_body = '{"error": {"code": 429, "message": "Quota exceeded for quota metric \'Queries\'"}}'
    session = FakeSession(FakeResponse(429, text=quota_body), FakeResponse(200, ok_body()))
    p = make(session, keys=("key-1", "key-2"), cxs=("cx-1",))
    with caplog.at_level(logging.WARNING):
        res = p.search("q", "en", SPEC)
    assert res.status == "ok"
    assert [call[1]["params"]["key"] for call in session.calls] == ["key-1", "key-2"]
    assert any("Quota exceeded" in r.getMessage() for r in caplog.records)


def test_all_keys_exhausted_is_quota_and_400_is_error(caplog):
    session = FakeSession(FakeResponse(429, text="quota"), FakeResponse(429, text="quota"))
    res = make(session, keys=("a", "b")).search("q", "en", SPEC)
    assert res.status == "quota" and res.http_status == 429

    invalid = '{"error": {"code": 400, "message": "API key not valid. Please pass a valid API key."}}'
    session = FakeSession(FakeResponse(400, text=invalid))
    with caplog.at_level(logging.WARNING):
        res = make(session).search("q", "en", SPEC)
    assert res.status == "error" and res.http_status == 400
    assert any("API key not valid" in r.getMessage() for r in caplog.records)


def test_403_on_every_key_disables_cse_for_the_rest_of_the_run(caplog):
    denied = '{"error": {"code": 403, "message": "This project does not have the access to Custom Search JSON API."}}'
    session = FakeSession(FakeResponse(403, text=denied), FakeResponse(403, text=denied))
    with caplog.at_level(logging.WARNING):
        res = make(session, keys=("a", "b")).search("q", "en", SPEC)
    assert res.status == "error" and res.http_status == 403
    assert make(FakeSession()) is None                      # later SKUs skip CSE entirely
    assert any("skipped for the rest of this run" in r.getMessage() for r in caplog.records)


def test_one_key_403_while_another_works_keeps_cse():
    session = FakeSession(FakeResponse(403, text="denied"), FakeResponse(200, ok_body()))
    res = make(session, keys=("a", "b")).search("q", "en", SPEC)
    assert res.status == "ok"
    assert make(FakeSession()) is not None
