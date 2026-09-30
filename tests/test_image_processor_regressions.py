"""Regression tests for the audit findings on image_processor / http_client (IMG-3..10, IMG-13, IMG-17, IMG-19).

All tests are offline: sockets are blocked, HTTP sessions and requests.post are fakes.
"""

import base64
import hashlib
import importlib.util
import io
import json
import os
import socket
import sys
import threading

import numpy as np
import pytest
import requests
from PIL import Image, ImageDraw, ImageFilter

import config
import http_client
import image_processor

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BG = (225, 225, 225)
BLUE = (40, 70, 200)


# ---------------------------------------------------------------------------
# Offline harness
# ---------------------------------------------------------------------------

def _refuse(*_args, **_kwargs):
    raise RuntimeError("network access is blocked in tests")


class _NoNetworkSession:
    def get(self, *args, **kwargs):
        raise AssertionError("unexpected HTTP download in this test")


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path):
    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)
    monkeypatch.setattr(socket, "getaddrinfo", _refuse)
    monkeypatch.setattr(http_client, "_new_session", lambda: _NoNetworkSession())
    monkeypatch.setattr(http_client, "_sleep", lambda s: None)
    monkeypatch.setattr(image_processor.requests, "post", _refuse)

    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    monkeypatch.setattr(config, "PHOTOROOM_API_KEY", "test-photoroom-key")
    monkeypatch.setattr(config, "REMOVE_BG_API_KEY", "")
    monkeypatch.setattr(config, "BG_REMOVAL_METHOD", "photoroom")
    monkeypatch.setattr(config, "ENABLE_STUDIO_SHADOWS", False)
    monkeypatch.setattr(config, "PROXY_URL", "")
    monkeypatch.setattr(config, "CANDIDATE_STORE_DIR", str(tmp_path / "candidates"), raising=False)
    if hasattr(config, "OUTPUT_CANVAS_SIZE"):
        monkeypatch.delattr(config, "OUTPUT_CANVAS_SIZE")
    monkeypatch.delenv("OUTPUT_CANVAS_SIZE", raising=False)

    work = tmp_path / "tmp"
    work.mkdir()
    monkeypatch.setattr(image_processor.tempfile, "tempdir", str(work))
    yield work


class FakeResponse:
    def __init__(self, status_code=200, content=b"", headers=None):
        self.status_code = status_code
        self.content = content
        self.headers = headers or {}
        self.text = content.decode("utf-8", "replace") if isinstance(content, bytes) else str(content)

    def json(self):
        return json.loads(self.content)


class FakeHttpResponse:
    """Minimal stand-in for a curl_cffi streaming response."""

    def __init__(self, status_code=200, body=b"", headers=None):
        self.status_code = status_code
        self._body = body
        self.headers = headers or {}
        self.closed = False

    @property
    def content(self):
        return self._body

    def iter_content(self, chunk_size=65536):
        for i in range(0, len(self._body), chunk_size):
            yield self._body[i:i + chunk_size]

    def close(self):
        self.closed = True


class ScriptedSession:
    """Returns (or raises) the scripted items in order and records every call."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


def png_bytes(img):
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def jpeg_bytes(img):
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=92)
    return buf.getvalue()


def install_photoroom(monkeypatch, handler):
    calls = []

    def fake_post(url, headers=None, files=None, data=None, timeout=None, **_kw):
        assert url == image_processor.PHOTOROOM_URL
        _name, blob, _mime = files["image_file"]
        sent = Image.open(io.BytesIO(blob))
        sent.load()
        calls.append(sent)
        return handler(sent)

    monkeypatch.setattr(image_processor.requests, "post", fake_post)
    return calls


def segmenter(bg=BG, tolerance=30):
    def handler(img):
        arr = np.asarray(img.convert("RGB")).astype(int)
        alpha = np.where(np.abs(arr - np.array(bg)).sum(axis=2) <= tolerance, 0, 255).astype(np.uint8)
        rgba = np.dstack([arr.astype(np.uint8), alpha])
        rgba[alpha == 0, :3] = 0
        return FakeResponse(200, png_bytes(Image.fromarray(rgba, "RGBA")))

    return handler


def simple_product(path, size=(500, 700)):
    img = Image.new("RGB", size, BG)
    ImageDraw.Draw(img).rectangle([size[0] // 4, size[1] // 8, 3 * size[0] // 4, 7 * size[1] // 8], fill=BLUE)
    img.save(path, format="PNG")
    return str(path)


def files_under(root):
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        found.extend(os.path.join(dirpath, d) for d in dirnames)
        found.extend(os.path.join(dirpath, f) for f in filenames)
    return found


# ---------------------------------------------------------------------------
# IMG-5: fail closed when background removal fails
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("status", [402, 429, 500])
def test_photoroom_failure_fail_closed(monkeypatch, tmp_path, offline, status):
    src = simple_product(tmp_path / "src.png")
    calls = install_photoroom(monkeypatch, lambda _img: FakeResponse(status, b'{"detail": "no credits"}'))

    result = image_processor.process_product_image_result(src, "Laban 1L", "Al Rawabi", 800, 800, bg_method="photoroom")

    assert len(calls) == 1
    assert result.isolated is False
    assert result.error == f"photoroom_{status}"
    assert result.path is None
    assert (result.width, result.height) == (0, 0)
    assert files_under(offline) == [], "a publishable file was written although isolation failed"
    # The legacy wrapper fails closed too: no path means callers cannot upload the raw photo.
    assert image_processor.process_product_image(src, "Laban 1L", "Al Rawabi", bg_removal_method="photoroom") is None
    out = tmp_path / "nobg.png"
    assert image_processor.remove_background(src, str(out), bg_method="photoroom") is False
    assert not out.exists()


def test_photoroom_timeout_and_missing_key(monkeypatch, tmp_path):
    src = simple_product(tmp_path / "src.png")

    def timeout_post(*_a, **_kw):
        raise requests.exceptions.Timeout("read timed out")

    monkeypatch.setattr(image_processor.requests, "post", timeout_post)
    result = image_processor.process_product_image_result(src, "Milk", "Almarai", bg_method="photoroom")
    assert (result.isolated, result.error, result.path) == (False, "photoroom_timeout", None)

    monkeypatch.setattr(config, "PHOTOROOM_API_KEY", "")
    result = image_processor.process_product_image_result(src, "Milk", "Almarai", bg_method="photoroom")
    assert (result.isolated, result.error, result.path) == (False, "photoroom_no_key", None)


def test_rembg_import_error_is_not_success(monkeypatch, tmp_path, offline):
    src = simple_product(tmp_path / "src.png")
    monkeypatch.setitem(sys.modules, "rembg", None)  # makes `from rembg import ...` raise ImportError

    result = image_processor.process_product_image_result(src, "Milk", "Almarai", bg_method="rembg")

    assert result.isolated is False
    assert result.error == "rembg_not_installed"
    assert result.path is None
    assert files_under(offline) == []


@pytest.mark.parametrize("method, error", [
    ("bria", "bria_rmbg_unsupported"),
    ("magic_eraser", "unknown_bg_method"),
    ("remove_bg_api", "removebg_no_key"),
])
def test_unsupported_methods_fail_closed(tmp_path, method, error):
    src = simple_product(tmp_path / "src.png")
    result = image_processor.process_product_image_result(src, "Milk", "Almarai", bg_method=method)
    assert (result.isolated, result.error, result.path) == (False, error, None)


def test_grabcut_exception_fails_closed(monkeypatch, tmp_path):
    import cv2

    src = simple_product(tmp_path / "src.png")

    def broken(*_a, **_kw):
        raise cv2.error("simulated failure")

    monkeypatch.setattr(cv2, "grabCut", broken)
    result = image_processor.process_product_image_result(src, "Milk", "Almarai", bg_method="grabcut")
    assert (result.isolated, result.error, result.path) == (False, "grabcut_failed", None)


def test_already_transparent_needs_more_than_one_pixel(monkeypatch, tmp_path):
    # One transparent pixel (e.g. an anti-aliased corner) does NOT make an image "already isolated".
    one_pixel = Image.new("RGBA", (400, 400), BG + (255,))
    one_pixel.putpixel((0, 0), (0, 0, 0, 0))
    one_pixel_path = tmp_path / "one_pixel.png"
    one_pixel.save(one_pixel_path)
    assert image_processor.is_background_already_removed(str(one_pixel_path)) is False

    # A real transparent-background packshot is, and PhotoRoom is not called for it.
    cutout = Image.new("RGBA", (400, 400), (0, 0, 0, 0))
    ImageDraw.Draw(cutout).rectangle([100, 50, 300, 350], fill=BLUE + (255,))
    cutout_path = tmp_path / "cutout.png"
    cutout.save(cutout_path)
    assert image_processor.is_background_already_removed(str(cutout_path)) is True

    calls = install_photoroom(monkeypatch, lambda _img: pytest.fail("PhotoRoom must not be called"))
    result = image_processor.process_product_image_result(str(cutout_path), "Milk", "Almarai", bg_method="photoroom")
    assert calls == []
    assert (result.isolated, result.provider, result.error) == (True, "source_alpha", None)
    # An explicit 'none' never claims isolation, even for an already transparent source.
    result = image_processor.process_product_image_result(str(cutout_path), "Milk", "Almarai", bg_method="none")
    assert (result.isolated, result.provider, result.error) == (False, "none", None)
    with Image.open(result.path) as out:
        assert out.mode == "RGB" and out.getpixel((0, 0)) == (255, 255, 255)

    # The one-pixel image goes through real isolation instead.
    calls = install_photoroom(monkeypatch, segmenter())
    result = image_processor.process_product_image_result(str(one_pixel_path), "Milk", "Almarai", bg_method="photoroom")
    assert len(calls) == 1


# ---------------------------------------------------------------------------
# IMG-6: enclosed handle holes stay see-through (white on the canvas), never filled
# ---------------------------------------------------------------------------

def test_handle_hole_not_filled(monkeypatch, tmp_path):
    w, h = 400, 500
    hole = (250, 150, 330, 300)  # x0, y0, x1, y1 inside the jug body
    jug = np.zeros((h, w, 4), np.uint8)
    jug[:, :, :3] = BLUE
    jug[:, :, 3] = 255
    jug[hole[1]:hole[3], hole[0]:hole[2], :] = 0  # transparent with RGB 0 underneath (PhotoRoom style)
    cutout = Image.fromarray(jug, "RGBA")

    src = simple_product(tmp_path / "src.png")
    install_photoroom(monkeypatch, lambda _img: FakeResponse(200, png_bytes(cutout)))

    result = image_processor.process_product_image_result(src, "Oil 1.8L", "Noor", 800, 800, bg_method="photoroom")
    assert result.isolated is True

    with Image.open(result.path) as out:
        arr = np.asarray(out.convert("RGB")).astype(int)
    body = (np.abs(arr - np.array(BLUE)).sum(axis=2) <= 40)
    ys, xs = np.nonzero(body)
    bx0, by0, bx1, by1 = xs.min(), ys.min(), xs.max() + 1, ys.max() + 1
    sx, sy = (bx1 - bx0) / w, (by1 - by0) / h
    # map the hole into canvas coordinates and look at its interior (away from resampled edges)
    hx0, hx1 = int(bx0 + hole[0] * sx) + 4, int(bx0 + hole[2] * sx) - 4
    hy0, hy1 = int(by0 + hole[1] * sy) + 4, int(by0 + hole[3] * sy) - 4
    hole_pixels = arr[hy0:hy1, hx0:hx1]
    assert hole_pixels.size > 0
    assert hole_pixels.min() >= 250, "the handle hole was filled (black or backdrop visible)"
    # sanity: the jug body right next to the hole is still blue
    assert body[(hy0 + hy1) // 2, int(bx0 + (hole[0] - 20) * sx)]


def test_process_mask_keeps_provider_rgba_and_holes():
    from edge_shadow_engine import EdgeShadowEngine

    rgba = np.zeros((200, 200, 4), np.uint8)
    rgba[20:180, 20:180] = (10, 200, 30, 255)
    rgba[80:120, 80:120] = (0, 0, 0, 0)        # enclosed hole
    rgba[2:4, 195:197] = (255, 0, 0, 255)      # 4-pixel speck far from the product
    out = np.asarray(EdgeShadowEngine.process_mask(Image.fromarray(rgba, "RGBA")))

    assert (out[80:120, 80:120, 3] == 0).all(), "hole must stay transparent"
    assert (out[20:80, 20:180] == (10, 200, 30, 255)).all(), "provider RGB/alpha must be kept as-is"
    assert (out[2:4, 195:197, 3] == 0).all(), "isolated speck should be removed"


# ---------------------------------------------------------------------------
# IMG-7 / IMG-9: unicode and Windows-illegal names never reach file paths
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("product_name, brand", [
    ("حليب المراعي كامل الدسم", "المراعي"),
    ('6*330ml "Salted"', "Lay's <UAE>|?"),
])
def test_unicode_and_illegal_names(monkeypatch, tmp_path, offline, product_name, brand):
    src = simple_product(tmp_path / "src.png")
    install_photoroom(monkeypatch, segmenter())
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)

    created = []
    real_mkdtemp = image_processor.tempfile.mkdtemp
    real_mkstemp = image_processor.tempfile.mkstemp

    def recording_mkdtemp(*args, **kwargs):
        path = real_mkdtemp(*args, **kwargs)
        created.append(path)
        return path

    def recording_mkstemp(*args, **kwargs):
        fd, path = real_mkstemp(*args, **kwargs)
        created.append(path)
        return fd, path

    monkeypatch.setattr(image_processor.tempfile, "mkdtemp", recording_mkdtemp)
    monkeypatch.setattr(image_processor.tempfile, "mkstemp", recording_mkstemp)

    result = image_processor.process_product_image_result(src, product_name, brand, 800, 800, bg_method="photoroom")

    assert result.isolated is True and result.error is None
    assert os.path.isfile(result.path)
    assert created, "the output must live in a tempfile.mkdtemp() directory"
    everything = created + files_under(offline) + [result.path]
    for path in everything:
        assert path.isascii(), f"non-ASCII temp path: {path!r}"
        for bad in ('*', '"', "<", ">", "|", "?"):
            assert bad not in os.path.basename(path)
    assert os.listdir(cwd) == [], "nothing may be written to a name-based ./temp directory"
    with Image.open(result.path) as out:
        assert out.size == (800, 800)


# ---------------------------------------------------------------------------
# IMG-3 / IMG-M5 / IMG-13: no blur gate; undecodable downloads get a precise error
# ---------------------------------------------------------------------------

def _minimalist_packshot(size=2000):
    """Large, clean, text-free white-background packshot: very low whole-frame Laplacian variance."""
    img = Image.new("RGB", (size, size), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([int(size * 0.35), int(size * 0.1), int(size * 0.65), int(size * 0.9)],
                           radius=int(size * 0.05), fill=(228, 218, 196))
    return img.filter(ImageFilter.GaussianBlur(radius=3))


def test_no_blurry_gate_and_not_image(monkeypatch, tmp_path):
    import cv2

    packshot = _minimalist_packshot()
    gray = cv2.cvtColor(np.asarray(packshot), cv2.COLOR_RGB2GRAY)
    assert cv2.Laplacian(gray, cv2.CV_64F).var() < 40.0  # the removed gate would have rejected it
    path = tmp_path / "packshot.jpg"
    packshot.save(path, format="JPEG", quality=95)
    install_photoroom(monkeypatch, segmenter(bg=(255, 255, 255), tolerance=24))

    result = image_processor.process_product_image_result(str(path), "Water 500ml", "Masafi", 800, 800,
                                                          bg_method="photoroom")
    assert result != "blurry"
    assert result.isolated is True and result.error is None
    with Image.open(result.path) as out:
        assert out.size == (800, 800)

    # An HTML soft-404 served with status 200 is a download error, not a "blurry" image.
    html = b"<!doctype html><html><body>Image not found</body></html>"
    for content_type in ("text/html; charset=utf-8", "image/jpeg"):  # the second one lies
        session = ScriptedSession([FakeHttpResponse(200, html, {"Content-Type": content_type})])
        monkeypatch.setattr(http_client, "_new_session", lambda s=session: s)
        result = image_processor.process_product_image_result(
            "https://cdn.example.ae/p/123.jpg", "Milk", "Almarai", 800, 800, bg_method="photoroom")
        assert (result.isolated, result.error, result.path) == (False, "download_not_image", None)
        assert len(session.calls) == 1

    svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"></svg>'
    session = ScriptedSession([FakeHttpResponse(200, svg, {"Content-Type": "image/svg+xml"})])
    monkeypatch.setattr(http_client, "_new_session", lambda: session)
    result = image_processor.process_product_image_result("https://x.example/a.svg", "Milk", "Almarai")
    assert result.error == "download_not_image"

    result = image_processor.process_product_image_result("ftp://x.example/a.jpg", "Milk", "Almarai")
    assert result.error == "download_bad_scheme"


def test_download_path_end_to_end(monkeypatch, tmp_path):
    img = Image.new("RGB", (600, 900), BG)
    ImageDraw.Draw(img).rectangle([150, 100, 450, 800], fill=BLUE)
    session = ScriptedSession([FakeHttpResponse(200, jpeg_bytes(img), {"Content-Type": "image/jpeg"})])
    monkeypatch.setattr(http_client, "_new_session", lambda: session)
    install_photoroom(monkeypatch, segmenter(tolerance=40))

    result = image_processor.process_product_image_result("https://cdn.example.ae/p/1.jpg", "Milk", "Almarai")

    assert result.isolated is True and (result.width, result.height) == (800, 800)
    (_url, kwargs), = session.calls
    accept = kwargs["headers"]["Accept"]
    assert accept.startswith("image/") and "text/html" not in accept
    assert kwargs.get("verify", True) is not False


def test_candidate_store_bytes_are_used(monkeypatch, tmp_path):
    store = tmp_path / "candidates"
    store.mkdir()
    img = Image.new("RGB", (500, 700), BG)
    ImageDraw.Draw(img).rectangle([100, 100, 400, 600], fill=BLUE)
    data = png_bytes(img)
    sha = hashlib.sha256(data).hexdigest()
    (store / f"{sha}.png").write_bytes(data)
    install_photoroom(monkeypatch, segmenter())

    # The URL is never downloaded (the session would raise): the verified bytes are processed.
    result = image_processor.process_product_image_result(
        "https://expired.example/signed.jpg?token=1", "Milk", "Almarai", candidate_sha256=sha)
    assert result.isolated is True

    # Bytes whose hash does not match are ignored, and the URL is downloaded instead.
    (store / f"{'0' * 64}.png").write_bytes(data)
    session = ScriptedSession([FakeHttpResponse(404, b"", {"Content-Type": "text/html"})])
    monkeypatch.setattr(http_client, "_new_session", lambda: session)
    result = image_processor.process_product_image_result(
        "https://expired.example/signed.jpg", "Milk", "Almarai", candidate_sha256="0" * 64)
    assert (result.isolated, result.error) == (False, "download_http_404")
    assert len(session.calls) == 1


# ---------------------------------------------------------------------------
# http_client retry policy and TLS
# ---------------------------------------------------------------------------

def _client_with(monkeypatch, script):
    session = ScriptedSession(script)
    sleeps = []
    monkeypatch.setattr(http_client, "_new_session", lambda: session)
    monkeypatch.setattr(http_client, "_sleep", sleeps.append)
    return http_client.ImpersonateClient(), session, sleeps


PNG_OK = png_bytes(Image.new("RGB", (20, 20), BLUE))


def test_http_retries_only_transient_errors(monkeypatch):
    ok = FakeHttpResponse(200, PNG_OK, {"Content-Type": "image/png"})

    client, session, sleeps = _client_with(monkeypatch, [FakeHttpResponse(503), FakeHttpResponse(429), ok])
    assert client.fetch_image("https://a.example/x.png").content == PNG_OK
    assert len(session.calls) == 3 and len(sleeps) == 2

    client, session, sleeps = _client_with(monkeypatch, [FakeHttpResponse(503)] * 3)
    res = client.fetch_image("https://a.example/x.png")
    assert (res.content, res.error, res.attempts) == (None, "http_503", 3)
    assert len(session.calls) == 3 and len(sleeps) == 2, "no sleep after the last attempt"

    client, session, sleeps = _client_with(monkeypatch, [FakeHttpResponse(404)])
    assert client.fetch_image("https://a.example/x.png").error == "http_404"
    assert len(session.calls) == 1 and sleeps == []

    timeout = http_client._http.exceptions.Timeout("Operation timed out")
    client, session, sleeps = _client_with(monkeypatch, [timeout, timeout, timeout])
    assert client.fetch_image("https://a.example/x.png").error == "timeout"
    assert len(session.calls) == 3 and len(sleeps) == 2

    client, session, sleeps = _client_with(monkeypatch, [OSError("Could not resolve host")])
    assert client.fetch_image("https://a.example/x.png").error == "connection_error"
    assert len(session.calls) == 1 and sleeps == []


def test_http_size_cap_and_tls(monkeypatch):
    big = FakeHttpResponse(200, b"", {"Content-Type": "image/jpeg", "Content-Length": str(16 * 1024 * 1024)})
    client, _session, _ = _client_with(monkeypatch, [big])
    assert client.fetch_image("https://a.example/x.jpg").error == "too_large"

    streamed = FakeHttpResponse(200, b"\xff\xd8\xff" + b"0" * 2048, {"Content-Type": "image/jpeg"})
    client, _session, _ = _client_with(monkeypatch, [streamed])
    assert client.fetch_image("https://a.example/x.jpg", max_bytes=1024).error == "too_large"

    # The client itself rejects non-image bodies (before any decoder sees them).
    for body, ctype in ((b"<html>Access denied</html>", "text/html"),
                        (b"<svg xmlns='http://www.w3.org/2000/svg'/>", "image/svg+xml")):
        client, _session, _ = _client_with(monkeypatch, [FakeHttpResponse(200, body, {"Content-Type": ctype})])
        res = client.fetch_image("https://a.example/x")
        assert (res.content, res.error) == (None, "not_image")
    # ...but trusts magic bytes when a CDN sends a generic content type.
    client, _session, _ = _client_with(
        monkeypatch, [FakeHttpResponse(200, PNG_OK, {"Content-Type": "application/octet-stream"})])
    assert client.fetch_image("https://a.example/x").content == PNG_OK

    client, session, _ = _client_with(monkeypatch, [FakeHttpResponse(200, PNG_OK, {"Content-Type": "image/png"})])
    client.fetch_image("https://a.example/x.png", referer="https://shop.example/p/1")
    (_url, kwargs), = session.calls
    assert kwargs["headers"]["Referer"] == "https://shop.example/p/1"
    assert "verify" not in kwargs  # TLS verification stays at the library default (on)

    for module in ("image_processor.py", "http_client.py"):
        with open(os.path.join(REPO_ROOT, module), encoding="utf-8") as fh:
            source = fh.read()
        assert "verify=False" not in source
        assert "disable_warnings" not in source


# ---------------------------------------------------------------------------
# IMG-9 / IMG-17: config is never mutated; taxonomy_classifier is gone
# ---------------------------------------------------------------------------

def test_config_not_mutated(monkeypatch, tmp_path):
    import cv2

    rng = np.random.default_rng(0)
    arr = np.clip(rng.normal(235, 6, (300, 300, 3)), 0, 255).astype(np.uint8)
    arr[60:240, 90:210] = np.clip(rng.normal(60, 10, (180, 120, 3)), 0, 255).astype(np.uint8)
    src = tmp_path / "noisy.png"
    Image.fromarray(arr, "RGB").save(src)

    seen = []
    real_grabcut = cv2.grabCut

    def spying_grabcut(*args, **kwargs):
        seen.append(config.BG_REMOVAL_METHOD)
        return real_grabcut(*args, **kwargs)

    monkeypatch.setattr(cv2, "grabCut", spying_grabcut)
    before = config.BG_REMOVAL_METHOD

    result = image_processor.process_product_image_result(str(src), "Milk", "Almarai", bg_method="grabcut")

    assert result.provider == "grabcut"
    assert result.isolated is True
    assert seen == [before] == ["photoroom"], "the method must be a parameter, not a config mutation"
    assert config.BG_REMOVAL_METHOD == before

    # Concurrent calls with different methods do not see each other's method.
    install_photoroom(monkeypatch, segmenter())
    results = {}

    def run(method):
        results[method] = image_processor.process_product_image_result(str(src), "Milk", "Almarai", bg_method=method)

    threads = [threading.Thread(target=run, args=(m,)) for m in ("grabcut", "photoroom", "none")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert {m: r.provider for m, r in results.items()} == {"grabcut": "grabcut", "photoroom": "photoroom",
                                                           "none": "none"}
    assert config.BG_REMOVAL_METHOD == before


def test_import_without_taxonomy_classifier(monkeypatch):
    assert not os.path.exists(os.path.join(REPO_ROOT, "taxonomy_classifier.py"))
    monkeypatch.setitem(sys.modules, "taxonomy_classifier", None)
    spec = importlib.util.spec_from_file_location("image_processor_fresh", os.path.join(REPO_ROOT, "image_processor.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert callable(module.process_product_image_result)
    assert callable(module.process_product_image)


# ---------------------------------------------------------------------------
# Legacy entry points used by main.py / cli_bridge.py / fastapi_server.py
# ---------------------------------------------------------------------------

def test_legacy_entry_points(monkeypatch, tmp_path):
    src = simple_product(tmp_path / "src.png")
    install_photoroom(monkeypatch, segmenter())

    # process_product_image keeps its old signature and returns the canvas path on success.
    path = image_processor.process_product_image(
        src, "Milk", "Almarai", bg_removal_method="photoroom", enhance=False,
        target_width=None, target_height=None, padding_ratio=0.85, bypass_heuristics=True)
    with Image.open(path) as out:
        assert out.size == (800, 800) and out.mode == "RGB"

    # Callers still read LAST_PROCESSING_STATUS; it is now an empty read-only view, not shared state.
    status = image_processor.LAST_PROCESSING_STATUS
    assert status.get(("Milk", "Almarai"), "missing") == "missing"
    with pytest.raises(TypeError):
        status[("Milk", "Almarai")] = "success"

    # remove_background writes a real RGBA cutout and reports True only on real isolation.
    nobg = tmp_path / "nobg.png"
    assert image_processor.remove_background(src, str(nobg), bg_method="photoroom") is True
    with Image.open(nobg) as cut:
        assert cut.mode == "RGBA" and cut.getchannel("A").getextrema() == (0, 255)
    assert image_processor.remove_background(src, str(tmp_path / "none.png"), bg_method="none") is False

    # crop_image_by_box ignores implausible boxes and crops plausible ones.
    assert image_processor.crop_image_by_box(src, [0, 0, 50, 50], str(tmp_path / "c.png")) is False
    assert image_processor.crop_image_by_box(src, [100, 200, 900, 800], str(tmp_path / "c.png")) is True
    with Image.open(tmp_path / "c.png") as cropped:
        assert cropped.size[0] < 500 and cropped.size[1] < 700


def test_enhance_keeps_the_product_edges(tmp_path):
    # IMG-8: the old loop made a 5% strip on every side transparent. A tight cutout touches all edges.
    cut = Image.new("RGBA", (300, 600), BLUE + (255,))
    path = tmp_path / "tight.png"
    cut.save(path)
    out_path = tmp_path / "enhanced.png"

    assert image_processor.enhance_image_quality(str(path), str(out_path)) is True

    with Image.open(out_path) as out:
        alpha = np.asarray(out.convert("RGBA").getchannel("A"))
    assert alpha.min() == 255, "enhancement must not erase any part of the product"


# ---------------------------------------------------------------------------
# IMG-10: honest metadata prompt, 1024 px input
# ---------------------------------------------------------------------------

def test_metadata_prompt_honest(monkeypatch, tmp_path):
    prompt = image_processor.build_metadata_prompt("Full Fat Milk 1L", "Almarai")
    assert "null" in prompt
    assert "Calories 150" not in prompt
    assert "contains gluten" not in prompt

    monkeypatch.setattr(config, "GEMINI_API_KEY", "test-gemini-key")
    src = tmp_path / "front.png"
    Image.new("RGB", (2000, 1600), (200, 30, 30)).save(src)
    sent = {}
    model_reply = {
        "nutrition": None, "ingredients": "null", "description_en": "Fresh milk.", "description_ar": "حليب طازج",
        "category_l1_en": "Dairy & Eggs", "category_l2_en": "", "category_l3_en": "",
        "category_l1_ar": "", "category_l2_ar": "", "category_l3_ar": "", "tags_en": None, "tags_ar": None,
    }

    def fake_post(url, headers=None, json=None, timeout=None, **_kw):  # noqa: A002 - mirrors requests.post
        sent["url"], sent["headers"], sent["payload"] = url, headers, json
        body = {"candidates": [{"content": {"parts": [{"text": _json_dumps(model_reply)}]}}]}
        return FakeResponse(200, _json_dumps(body).encode("utf-8"))

    monkeypatch.setattr(image_processor.requests, "post", fake_post)
    result = image_processor.extract_metadata_from_image(str(src), "Full Fat Milk 1L", "Almarai")

    parts = sent["payload"]["contents"][0]["parts"]
    assert parts[0]["text"] == prompt
    with Image.open(io.BytesIO(base64.b64decode(parts[1]["inlineData"]["data"]))) as img:
        assert max(img.size) == 1024
    assert "test-gemini-key" not in sent["url"]
    assert sent["headers"]["x-goog-api-key"] == "test-gemini-key"
    assert result["nutrition"] is None
    assert result["ingredients"] is None
    assert result["description_en"] == "Fresh milk."
    assert isinstance(result["category_l1_en"], str)


def _json_dumps(value):
    return json.dumps(value, ensure_ascii=False)
