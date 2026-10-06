"""Publishing canvas contract: every published image is an opaque, fixed-size white canvas.

All tests are offline: sockets are blocked, the HTTP session factory and requests.post are
replaced by fakes, and no API key or database is needed.
"""

import io
import json
import socket

import numpy as np
import pytest
from PIL import Image, ImageDraw

import config
import http_client
import image_processor

BG = (225, 225, 225)
BLUE = (40, 70, 200)
RED = (220, 20, 20)
GREEN = (20, 170, 60)


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
    # these tests pin the white canvas (OUTPUT_BACKGROUND=white); the transparent one: test_transparent_canvas.py
    monkeypatch.setattr(config, "OUTPUT_BACKGROUND", "white", raising=False)

    work = tmp_path / "tmp"
    work.mkdir()
    monkeypatch.setattr(image_processor.tempfile, "tempdir", str(work))
    yield


class FakeResponse:
    def __init__(self, status_code=200, content=b"", headers=None):
        self.status_code = status_code
        self.content = content
        self.headers = headers or {}
        self.text = content.decode("utf-8", "replace") if isinstance(content, bytes) else str(content)

    def json(self):
        return json.loads(self.content)


def png_bytes(img):
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def install_photoroom(monkeypatch, handler):
    """Replace the PhotoRoom HTTP call. `handler(input_image) -> FakeResponse`."""
    calls = []

    def fake_post(url, headers=None, files=None, data=None, timeout=None, **_kw):
        assert url == image_processor.PHOTOROOM_URL
        assert headers == {"x-api-key": "test-photoroom-key"}
        _name, blob, _mime = files["image_file"]
        sent = Image.open(io.BytesIO(blob))
        sent.load()
        calls.append({"image": sent, "form": dict(data), "timeout": timeout})
        return handler(sent)

    monkeypatch.setattr(image_processor.requests, "post", fake_post)
    return calls


def segmenter(bg=BG, tolerance=30):
    """A deterministic stand-in for a real segmenter: pixels close to `bg` become transparent
    (RGB zeroed, as PhotoRoom does), everything else is kept. Returns the full frame."""

    def handler(img):
        arr = np.asarray(img.convert("RGB")).astype(int)
        dist = np.abs(arr - np.array(bg)).sum(axis=2)
        alpha = np.where(dist <= tolerance, 0, 255).astype(np.uint8)
        rgba = np.dstack([arr.astype(np.uint8), alpha])
        rgba[alpha == 0, :3] = 0
        return FakeResponse(200, png_bytes(Image.fromarray(rgba, "RGBA")))

    return handler


def tight_crop_330x700():
    """A PhotoRoom crop=true style cutout: the product touches all four edges."""
    cut = Image.new("RGBA", (330, 700), BLUE + (255,))
    draw = ImageDraw.Draw(cut)
    draw.rectangle([0, 0, 329, 90], fill=RED + (255,))
    draw.rectangle([0, 610, 329, 699], fill=GREEN + (255,))
    # rounded shoulders: transparent corners with black RGB underneath
    for (x0, y0) in [(0, 0), (310, 0)]:
        draw.rectangle([x0, y0, x0 + 19, y0 + 19], fill=(0, 0, 0, 0))
    return cut


def bottle_source(path, w=600, h=1200):
    """Tall bottle on a grey studio background: red cap at the top, green base at the bottom."""
    img = Image.new("RGB", (w, h), BG)
    draw = ImageDraw.Draw(img)
    cx = w // 2
    draw.rectangle([cx - 70, int(h * 0.05), cx + 70, int(h * 0.17)], fill=RED)       # cap
    draw.rectangle([cx - 120, int(h * 0.17), cx + 120, int(h * 0.83)], fill=BLUE)    # body
    draw.rectangle([cx - 120, int(h * 0.83), cx + 120, int(h * 0.95)], fill=GREEN)   # base
    img.save(path, format="PNG")
    return str(path)


def non_white_bbox(img, threshold=250):
    arr = np.asarray(img.convert("RGB"))
    mask = (arr < threshold).any(axis=2)
    ys, xs = np.nonzero(mask)
    assert ys.size, "canvas is entirely white: the product is missing"
    return xs.min(), ys.min(), xs.max() + 1, ys.max() + 1


def colour_count(img, rgb, tol=60):
    arr = np.asarray(img.convert("RGB")).astype(int)
    return int((np.abs(arr - np.array(rgb)).sum(axis=2) <= tol).sum())


def open_output(result):
    assert result.path, f"no output produced: {result}"
    with Image.open(result.path) as out:
        out.load()
        return out.copy(), out.format


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("target, expected", [
    ((800, 800), (800, 800)),
    ((1000, 1000), (1000, 1000)),
    ((0, 0), (800, 800)),
    (("dynamic", "dynamic"), (800, 800)),
    ((None, None), (800, 800)),
])
def test_fixed_white_canvas(monkeypatch, tmp_path, target, expected):
    src = bottle_source(tmp_path / "src.png", 1000, 1000)
    calls = install_photoroom(monkeypatch, lambda _img: FakeResponse(200, png_bytes(tight_crop_330x700())))

    result = image_processor.process_product_image_result(
        src, "Almarai Full Fat Milk 1L", "Almarai",
        target_width=target[0], target_height=target[1], bg_method="photoroom")

    assert len(calls) == 1
    assert result.isolated is True
    assert result.provider == "photoroom"
    assert result.error is None
    assert (result.width, result.height) == expected

    out, fmt = open_output(result)
    assert fmt == "PNG"
    assert out.mode == "RGB"
    assert out.size == expected
    w, h = expected
    for corner in [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)]:
        assert out.getpixel(corner) == (255, 255, 255)

    x0, y0, x1, y1 = non_white_bbox(out)
    assert x1 - x0 <= int(0.88 * w)
    assert y1 - y0 <= int(0.88 * h)
    # The tall cutout is fitted by its height (not left at its native 700 px) and centred.
    assert y1 - y0 >= int(0.86 * h)
    assert abs((x0 + x1) / 2 - w / 2) <= 2
    assert abs((y0 + y1) / 2 - h / 2) <= 2
    # The rounded, transparent shoulders (black RGB underneath) must come out white, not black.
    assert out.getpixel((x0 + 2, y0 + 2)) == (255, 255, 255)


def test_output_canvas_size_setting(monkeypatch, tmp_path):
    src = bottle_source(tmp_path / "src.png")
    install_photoroom(monkeypatch, lambda _img: FakeResponse(200, png_bytes(tight_crop_330x700())))
    monkeypatch.setattr(config, "OUTPUT_CANVAS_SIZE", 1200, raising=False)

    result = image_processor.process_product_image_result(src, "Milk", "Almarai", 0, 0, bg_method="photoroom")

    out, _ = open_output(result)
    assert out.size == (1200, 1200)


def test_studio_shadows_keep_the_same_canvas(monkeypatch, tmp_path):
    src = bottle_source(tmp_path / "src.png")
    install_photoroom(monkeypatch, lambda _img: FakeResponse(200, png_bytes(tight_crop_330x700())))
    monkeypatch.setattr(config, "ENABLE_STUDIO_SHADOWS", True)

    result = image_processor.process_product_image_result(src, "Milk", "Almarai", 800, 800, bg_method="photoroom")

    out, _ = open_output(result)
    assert out.mode == "RGB" and out.size == (800, 800)
    for corner in [(0, 0), (799, 0), (0, 799), (799, 799)]:
        assert out.getpixel(corner) == (255, 255, 255)
    # The product itself (saturated blue) stays inside the 88% box and is centred.
    arr = np.asarray(out).astype(int)
    blue = (np.abs(arr - np.array(BLUE)).sum(axis=2) <= 40)
    ys, xs = np.nonzero(blue)
    assert ys.max() - ys.min() + 1 <= 704
    assert abs((xs.min() + xs.max() + 1) / 2 - 400) <= 3


def test_no_square_crop(monkeypatch, tmp_path):
    src = bottle_source(tmp_path / "tall.png", 600, 1200)
    box_calls = []

    def no_box(img, name, brand):
        box_calls.append(img.size)
        return None

    monkeypatch.setattr(image_processor, "_locate_product_box", no_box)
    calls = install_photoroom(monkeypatch, segmenter())

    result = image_processor.process_product_image_result(src, "Water 1.5L", "Masafi", 800, 800, bg_method="photoroom")

    assert box_calls == [(600, 1200)]
    # The whole tall frame reaches background removal: no square window, no resize to 800x800.
    assert calls[0]["image"].size == (600, 1200)
    assert result.isolated is True
    out, _ = open_output(result)
    assert colour_count(out, RED) > 500, "cap was cut off"
    assert colour_count(out, GREEN) > 500, "base was cut off"
    x0, y0, x1, y1 = non_white_bbox(out)
    assert y1 - y0 >= 690  # the full bottle height is fitted to ~88% of the canvas


def test_no_square_crop_with_method_none(monkeypatch, tmp_path):
    src = bottle_source(tmp_path / "tall.png", 600, 1200)
    result = image_processor.process_product_image_result(src, "Water 1.5L", "Masafi", 800, 800, bg_method="none")

    assert result.isolated is False
    assert result.provider == "none"
    assert result.error is None
    out, _ = open_output(result)
    assert out.size == (1364, 1364)          # adaptive: the 1200 px tall photo / 0.88, never cropped square
    assert colour_count(out, RED) > 500
    assert colour_count(out, GREEN) > 500


def test_gemini_box_sanity_check(monkeypatch, tmp_path):
    src = bottle_source(tmp_path / "tall.png", 600, 1200)
    calls = install_photoroom(monkeypatch, segmenter())

    # A plausible box (40% of the frame) is applied, widened by the 6% safety margin.
    monkeypatch.setattr(image_processor, "_locate_product_box", lambda *a: [100, 250, 900, 750])
    result = image_processor.process_product_image_result(src, "Water", "Masafi", 800, 800, bg_method="photoroom")
    cropped = calls[0]["image"].size
    # x: 220..780 of 1000 -> 132..468 px = 336 ; y: 52..948 -> floor(62.4)=62 .. round(1137.6)=1138 = 1076
    assert cropped == (336, 1076)
    # That crop still cuts 2 px off the cap (y 60..61) and the base (y 1138..1140): the quality gate sees
    # the product touching the crop lines and the cutout is redone once on the full frame.
    assert [c["image"].size for c in calls] == [(336, 1076), (600, 1200)]
    assert result.isolated is True and result.quality_flags == []

    # A tiny box (1% of the frame, e.g. a sticker) is ignored: the full frame is used.
    monkeypatch.setattr(image_processor, "_locate_product_box", lambda *a: [0, 0, 100, 100])
    result = image_processor.process_product_image_result(src, "Water", "Masafi", 800, 800, bg_method="photoroom")
    assert calls[-1]["image"].size == (600, 1200)
    out, _ = open_output(result)
    assert colour_count(out, RED) > 500 and colour_count(out, GREEN) > 500


def _exif_rotated_jpeg(path):
    """Logical upright 900x1200 bottle (red cap on top), stored sideways as 1200x900 + Orientation=6."""
    logical = Image.new("RGB", (900, 1200), BG)
    draw = ImageDraw.Draw(logical)
    draw.rectangle([330, 60, 570, 220], fill=RED)
    draw.rectangle([270, 220, 630, 1140], fill=BLUE)
    stored = logical.rotate(90, expand=True)  # counter-clockwise; Orientation=6 rotates it back
    exif = Image.Exif()
    exif[0x0112] = 6
    stored.save(path, format="JPEG", quality=95, exif=exif.tobytes())
    return str(path)


@pytest.mark.parametrize("method", ["none", "photoroom"])
def test_exif_upright(monkeypatch, tmp_path, method):
    src = _exif_rotated_jpeg(tmp_path / "phone.jpg")
    with Image.open(src) as raw:
        assert raw.size == (1200, 900)  # stored sideways
    calls = install_photoroom(monkeypatch, segmenter(tolerance=40))

    result = image_processor.process_product_image_result(src, "Milk", "Almarai", 800, 800, bg_method=method)

    if method == "photoroom":
        assert calls[0]["image"].size == (900, 1200)  # the provider receives the upright image
    out, _ = open_output(result)
    arr = np.asarray(out).astype(int)
    red = (arr[:, :, 0] > 170) & (arr[:, :, 1] < 90) & (arr[:, :, 2] < 90)
    ys, _xs = np.nonzero(red)
    assert ys.size > 500
    assert ys.mean() < out.height / 3, "red cap is not at the top: the image was published sideways"
