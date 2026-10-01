"""catalog_match.fetch: download, reject non-images, EXIF-transpose, store by sha256."""

import hashlib
import io
import socket
import threading

import numpy as np
import pytest
import requests
from PIL import Image

from catalog_match import fetch as fetch_mod
from catalog_match.fetch import HttpFetcher, load_image, phash_distance
from catalog_match.models import Candidate


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def _refuse(*_a, **_k):
        raise AssertionError("network access attempted in an offline test")
    monkeypatch.setattr(socket.socket, "connect", _refuse)


def _jpeg(width=400, height=300, seed=0, exif_orientation=None, quality=90):
    rng = np.random.default_rng(seed)
    arr = rng.integers(0, 256, (height, width, 3), dtype=np.uint8)
    img = Image.fromarray(arr)
    buf = io.BytesIO()
    if exif_orientation is not None:
        exif = Image.Exif()
        exif[0x0112] = exif_orientation
        img.save(buf, "JPEG", quality=quality, exif=exif.tobytes())
    else:
        img.save(buf, "JPEG", quality=quality)
    return buf.getvalue()


class FakeResponse:
    def __init__(self, status=200, body=b"", content_type="image/jpeg", length_header=True):
        self.status_code = status
        self._body = body
        self.headers = {"Content-Type": content_type}
        if length_header:
            self.headers["Content-Length"] = str(len(body))
        self.closed = False

    def iter_content(self, chunk_size=65536):
        for i in range(0, len(self._body), chunk_size):
            yield self._body[i:i + chunk_size]

    def close(self):
        self.closed = True


class FakeRequests:
    """Routes requests.get by URL to a list of responses (or exceptions), recording calls."""

    def __init__(self, routes):
        self.routes = {url: list(items) for url, items in routes.items()}
        self.calls = []
        self._lock = threading.Lock()

    def get(self, url, **kwargs):
        with self._lock:
            self.calls.append((url, kwargs))
            item = self.routes[url].pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def calls_for(self, url):
        return [kw for u, kw in self.calls if u == url]


@pytest.fixture
def fake(monkeypatch):
    def install(routes):
        f = FakeRequests(routes)
        monkeypatch.setattr(fetch_mod, "_curl_requests", None)      # plain requests path
        monkeypatch.setattr(fetch_mod.requests, "get", f.get)
        monkeypatch.setattr(fetch_mod.settings, "proxy_url", lambda: "")
        return f
    return install


def _cand(url, page_url=""):
    return Candidate(image_url=url, page_url=page_url, title="Almarai Full Fat Milk 1L")


def test_http_403_is_reported(fake, tmp_path):
    f = fake({"https://cdn.a.ae/banner.jpg": [FakeResponse(403, b"forbidden", "text/html")]})
    [res] = HttpFetcher(store_dir=str(tmp_path)).fetch([_cand("https://cdn.a.ae/banner.jpg")], None)
    assert res.ok is False
    assert res.error == "http_403"
    assert len(f.calls) == 1            # 4xx is never retried
    assert list(tmp_path.iterdir()) == []


def test_html_body_served_as_jpeg_is_not_image(fake, tmp_path):
    html = b"<!DOCTYPE html><html><head><title>Access denied</title></head><body>" + b"x" * 5000 + b"</body></html>"
    fake({"https://a.ae/p.jpg": [FakeResponse(200, html, "image/jpeg")]})
    [res] = HttpFetcher(store_dir=str(tmp_path)).fetch([_cand("https://a.ae/p.jpg")], None)
    assert (res.ok, res.error) == (False, "not_image")


def test_svg_is_not_image(fake, tmp_path):
    svg = (b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg" width="800" height="800">'
           + b'<rect width="800" height="800" fill="#fff"/>' * 120 + b"</svg>")
    fake({"https://a.ae/logo.svg": [FakeResponse(200, svg, "image/svg+xml")]})
    [res] = HttpFetcher(store_dir=str(tmp_path)).fetch([_cand("https://a.ae/logo.svg")], None)
    assert (res.ok, res.error) == (False, "not_image")


def test_garbage_bytes_are_not_image(fake, tmp_path):
    fake({"https://a.ae/x.jpg": [FakeResponse(200, bytes(range(256)) * 40, "image/jpeg")]})
    [res] = HttpFetcher(store_dir=str(tmp_path)).fetch([_cand("https://a.ae/x.jpg")], None)
    assert (res.ok, res.error) == (False, "not_image")


def test_valid_jpeg_is_stored_by_sha256_with_referer_and_phash(fake, tmp_path):
    body = _jpeg()
    url, page = "https://cdn.carrefouruae.com/img/123.jpg", "https://www.carrefouruae.com/almarai-milk-1l/p/123"
    f = fake({url: [FakeResponse(200, body)]})
    [res] = HttpFetcher(store_dir=str(tmp_path)).fetch([_cand(url, page)], None)
    assert res.ok is True, res.error
    sha = hashlib.sha256(body).hexdigest()
    assert res.content_sha256 == sha
    stored = tmp_path / f"{sha}.jpg"
    assert stored.is_file() and stored.read_bytes() == body
    assert res.path_or_bytes == str(stored)
    assert (res.width, res.height) == (400, 300)
    assert isinstance(res.phash, str) and len(res.phash) == 16 and int(res.phash, 16) != 0
    [kw] = f.calls_for(url)
    assert kw["headers"]["Referer"] == page
    assert kw["headers"]["Accept"] == "image/avif,image/webp,image/png,image/jpeg;q=0.9,*/*;q=0.5"
    assert kw["timeout"] == 10
    # load_image re-opens the stored file for later stages.
    assert load_image(res).size == (400, 300)


def test_store_dir_comes_from_settings(fake, tmp_path, monkeypatch):
    monkeypatch.setenv("CANDIDATE_STORE_DIR", str(tmp_path / "store"))
    body = _jpeg(seed=9)
    fake({"https://a.ae/s.jpg": [FakeResponse(200, body)]})
    [res] = HttpFetcher().fetch([_cand("https://a.ae/s.jpg")], None)
    assert res.ok
    assert (tmp_path / "store" / f"{hashlib.sha256(body).hexdigest()}.jpg").is_file()


def test_no_referer_without_page_url(fake, tmp_path):
    url = "https://cdn.a.ae/x.jpg"
    f = fake({url: [FakeResponse(200, _jpeg())]})
    HttpFetcher(store_dir=str(tmp_path)).fetch([_cand(url)], None)
    assert "Referer" not in f.calls_for(url)[0]["headers"]


def test_exif_orientation_6_is_transposed(fake, tmp_path):
    body = _jpeg(400, 300, exif_orientation=6)
    fake({"https://a.ae/rot.jpg": [FakeResponse(200, body)]})
    [res] = HttpFetcher(store_dir=str(tmp_path)).fetch([_cand("https://a.ae/rot.jpg")], None)
    assert res.ok is True
    assert (res.width, res.height) == (300, 400)
    assert load_image(res).size == (300, 400)


def test_one_retry_on_5xx_and_timeout_only(fake, tmp_path):
    body = _jpeg()
    f = fake({
        "https://a.ae/5xx.jpg": [FakeResponse(503, b"busy", "text/plain"), FakeResponse(200, body)],
        "https://a.ae/slow.jpg": [requests.Timeout("read timed out"), requests.Timeout("read timed out")],
        "https://a.ae/404.jpg": [FakeResponse(404, b"nope", "text/html")],
    })
    results = HttpFetcher(store_dir=str(tmp_path)).fetch(
        [_cand("https://a.ae/5xx.jpg"), _cand("https://a.ae/slow.jpg"), _cand("https://a.ae/404.jpg")], None)
    assert [r.ok for r in results] == [True, False, False]
    assert [r.error for r in results] == [None, "timeout", "http_404"]
    assert len(f.calls_for("https://a.ae/5xx.jpg")) == 2
    assert len(f.calls_for("https://a.ae/slow.jpg")) == 2    # one retry, then give up
    assert len(f.calls_for("https://a.ae/404.jpg")) == 1


def test_size_window(fake, tmp_path):
    tiny = Image.new("RGB", (8, 8), "white")
    buf = io.BytesIO()
    tiny.save(buf, "PNG")
    body = _jpeg()
    fake({
        "https://a.ae/tiny.png": [FakeResponse(200, buf.getvalue(), "image/png")],
        "https://a.ae/declared.jpg": [FakeResponse(200, body)],
        "https://a.ae/streamed.jpg": [FakeResponse(200, body, length_header=False)],
    })
    assert len(buf.getvalue()) < 3 * 1024 < len(body)
    small = HttpFetcher(store_dir=str(tmp_path)).fetch([_cand("https://a.ae/tiny.png")], None)[0]
    assert (small.ok, small.error) == (False, "too_small")
    # Larger than the cap: refused from Content-Length, or while streaming when it is absent.
    capped = HttpFetcher(store_dir=str(tmp_path), max_bytes=len(body) - 1)
    declared, streamed = capped.fetch([_cand("https://a.ae/declared.jpg"), _cand("https://a.ae/streamed.jpg")], None)
    assert (declared.ok, declared.error) == (False, "too_large")
    assert (streamed.ok, streamed.error) == (False, "too_large")
    assert list(tmp_path.iterdir()) == []


def test_only_top_8_in_order(fake, tmp_path):
    urls = [f"https://a.ae/{i}.jpg" for i in range(11)]
    f = fake({u: [FakeResponse(200, _jpeg(seed=i))] for i, u in enumerate(urls)})
    results = HttpFetcher(store_dir=str(tmp_path)).fetch([_cand(u) for u in urls], None)
    assert [r.candidate.image_url for r in results] == urls[:8]
    assert sorted({u for u, _ in f.calls}) == sorted(urls[:8])


def test_bad_url_is_not_requested(fake, tmp_path):
    f = fake({})
    [res] = HttpFetcher(store_dir=str(tmp_path)).fetch([_cand("data:image/png;base64,AAAA")], None)
    assert (res.ok, res.error) == (False, "bad_url")
    assert f.calls == []


def test_phash_distance():
    assert phash_distance("ffff000000000000", "ffff000000000001") == 1
    assert phash_distance(None, "00") is None
    assert phash_distance("zz", "00") is None


# ---------------------------------------------------------------------------
# Routes: direct first, the proxy only as the second attempt; browser fingerprint
# ---------------------------------------------------------------------------

PROXY = "http://user:pw@proxy.example:8080"


def test_proxy_is_a_fallback_not_the_only_route(fake, tmp_path, monkeypatch):
    body = _jpeg()
    f = fake({
        "https://a.ae/ok.jpg": [FakeResponse(200, body)],
        "https://a.ae/slow.jpg": [requests.Timeout("read timed out"), FakeResponse(200, _jpeg(seed=1))],
    })
    monkeypatch.setattr(fetch_mod.settings, "proxy_url", lambda: PROXY)
    ok, slow = HttpFetcher(store_dir=str(tmp_path)).fetch([_cand("https://a.ae/ok.jpg"), _cand("https://a.ae/slow.jpg")], None)
    assert ok.ok and slow.ok
    assert "proxies" not in f.calls_for("https://a.ae/ok.jpg")[0]          # a working direct download never uses the proxy
    first, second = f.calls_for("https://a.ae/slow.jpg")
    assert "proxies" not in first and second["proxies"] == {"http": PROXY, "https": PROXY}


def test_403_is_retried_through_the_proxy_only_when_one_is_configured(fake, tmp_path, monkeypatch):
    f = fake({"https://a.ae/geo.jpg": [FakeResponse(403, b"forbidden", "text/html"), FakeResponse(200, _jpeg())]})
    monkeypatch.setattr(fetch_mod.settings, "proxy_url", lambda: PROXY)
    [res] = HttpFetcher(store_dir=str(tmp_path)).fetch([_cand("https://a.ae/geo.jpg")], None)
    assert res.ok
    assert [("proxies" in kw) for kw in f.calls_for("https://a.ae/geo.jpg")] == [False, True]


def test_connection_error_is_retried_once(fake, tmp_path):
    f = fake({"https://a.ae/reset.jpg": [requests.ConnectionError("reset"), FakeResponse(200, _jpeg())]})
    [res] = HttpFetcher(store_dir=str(tmp_path)).fetch([_cand("https://a.ae/reset.jpg")], None)
    assert res.ok and len(f.calls_for("https://a.ae/reset.jpg")) == 2


def test_curl_cffi_impersonates_a_browser(monkeypatch, tmp_path):
    body = _jpeg()
    seen = []

    class FakeCurl:
        @staticmethod
        def get(url, **kwargs):
            seen.append(kwargs)
            return FakeResponse(200, body)

    monkeypatch.setattr(fetch_mod, "_curl_requests", FakeCurl)
    monkeypatch.setattr(fetch_mod.requests, "get", lambda *a, **k: pytest.fail("requests used although curl_cffi is available"))
    monkeypatch.setattr(fetch_mod.settings, "proxy_url", lambda: "")
    page = "https://www.luluhypermarket.com/en-ae/p/123"
    [res] = HttpFetcher(store_dir=str(tmp_path)).fetch([_cand("https://cdn.lulu.ae/p.jpg", page)], None)
    assert res.ok
    assert seen[0]["impersonate"] == "chrome"
    assert "User-Agent" not in seen[0]["headers"]          # the browser profile sends its own, matching its TLS fingerprint
    assert seen[0]["headers"]["Referer"] == page


def test_curl_cffi_timeout_is_classified(monkeypatch, tmp_path):
    class CurlTimeout(Exception):
        pass

    class FakeCurl:
        @staticmethod
        def get(url, **kwargs):
            raise CurlTimeout("Failed to perform, curl: (28) Operation timed out after 10001 milliseconds")

    monkeypatch.setattr(fetch_mod, "_curl_requests", FakeCurl)
    monkeypatch.setattr(fetch_mod.settings, "proxy_url", lambda: "")
    [res] = HttpFetcher(store_dir=str(tmp_path)).fetch([_cand("https://a.ae/t.jpg")], None)
    assert res.ok is False and res.error == "timeout"
