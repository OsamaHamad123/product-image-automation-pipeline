"""catalog_match.providers.open_food_facts: the GTIN lookup (D3, D7).

Saved bodies in fixtures/providers/off_product_*.json; sockets are blocked.
"""

import json
import socket
from pathlib import Path

import pytest

from catalog_match import ratelimit
from catalog_match.identity import build_sku_spec
from catalog_match.models import SkuSpec
from catalog_match.providers.open_food_facts import OFF_FIELDS, OffProvider
from catalog_match.ratelimit import TokenBucket

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "providers"
VALID_EAN = "6281007035224"          # GS1 check digit 4 is correct


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
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body
        self.text = json.dumps(body)

    def json(self):
        return self._body


class FakeSession:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


def spec_with(barcode):
    return build_sku_spec({"name": "Almarai Fresh Milk Full Fat 1L", "brand": "Almarai", "barcode": barcode}, {})


def test_valid_gtin_gives_one_structured_candidate():
    spec = spec_with(VALID_EAN)
    assert spec.gtin == "0" + VALID_EAN
    session = FakeSession(FakeResponse(200, load("off_product_ok.json")))
    res = OffProvider(session=session, bucket=ratelimit.UNLIMITED).lookup(spec)

    assert res.status == "ok"
    assert len(res.candidates) == 1
    c = res.candidates[0]
    assert c.gtin_on_page == spec.gtin
    assert c.sanctioned is True
    assert c.provider == "off"
    assert c.image_url == "https://images.openfoodfacts.org/images/products/628/100/703/5224/front_en.12.400.jpg"
    assert c.page_title == "Almarai Fresh Milk Full Fat 1 L"
    assert c.page_url == f"https://world.openfoodfacts.org/product/{VALID_EAN}"
    assert c.domain == "openfoodfacts.org"
    assert c.width is None and c.height is None

    url, kwargs = session.calls[0]
    assert url == f"https://world.openfoodfacts.org/api/v2/product/{VALID_EAN}"
    assert kwargs["params"] == {"fields": OFF_FIELDS}
    assert set(OFF_FIELDS.split(",")) == {"code", "product_name", "brands", "quantity", "image_front_url"}
    assert "User-Agent" in kwargs["headers"] and kwargs["headers"]["User-Agent"]


def test_search_interface_delegates_to_the_lookup():
    session = FakeSession(FakeResponse(200, load("off_product_ok.json")))
    res = OffProvider(session=session, bucket=ratelimit.UNLIMITED).search("ignored text", "en", spec_with(VALID_EAN))
    assert res.status == "ok" and len(res.candidates) == 1
    assert session.calls[0][0].endswith(VALID_EAN)


@pytest.mark.parametrize("http_status", [200, 404])
def test_missing_product_is_empty(http_status):
    session = FakeSession(FakeResponse(http_status, load("off_product_missing.json")))
    res = OffProvider(session=session, bucket=ratelimit.UNLIMITED).lookup(spec_with(VALID_EAN))
    assert res.status == "empty"
    assert res.candidates == []


def test_product_without_front_image_is_empty():
    body = load("off_product_ok.json")
    body["product"].pop("image_front_url")
    session = FakeSession(FakeResponse(200, body))
    res = OffProvider(session=session, bucket=ratelimit.UNLIMITED).lookup(spec_with(VALID_EAN))
    assert res.status == "empty" and res.candidates == []


@pytest.mark.parametrize("spec", [
    spec_with("6281007035225"),                              # bad check digit -> spec.gtin is None
    spec_with("6.28101E+12"),                                # spreadsheet scientific notation
    spec_with(None),                                         # no barcode
    SkuSpec(raw_name="x", gtin="06281007035225"),            # hand-built spec with an invalid GTIN
])
def test_invalid_gtin_makes_no_http_call(spec):
    session = FakeSession(FakeResponse(200, load("off_product_ok.json")))
    res = OffProvider(session=session, bucket=ratelimit.UNLIMITED).lookup(spec)
    assert session.calls == []
    assert res.status == "empty"
    assert res.candidates == []


def test_rate_limit_is_15_per_minute_through_the_bucket():
    assert OffProvider.rate_per_min == 15
    sleeps = []
    bucket = TokenBucket(rate_per_min=OffProvider.rate_per_min, burst=OffProvider.burst,
                         clock=lambda: 50.0, sleeper=sleeps.append)
    session = FakeSession(FakeResponse(200, load("off_product_ok.json")))
    p = OffProvider(session=session, bucket=bucket)
    p.lookup(spec_with(VALID_EAN))
    p.lookup(spec_with(VALID_EAN))
    assert len(session.calls) == 2
    assert sleeps == [pytest.approx(4.0)]   # the second lookup waited one 15/min slot


def test_http_errors_never_raise():
    for status, expected in ((429, "quota"), (503, "error"), (403, "error")):
        session = FakeSession(FakeResponse(status, {"status": "failure"}))
        res = OffProvider(session=session, bucket=ratelimit.UNLIMITED).lookup(spec_with(VALID_EAN))
        assert res.status == expected
        assert res.http_status == status
