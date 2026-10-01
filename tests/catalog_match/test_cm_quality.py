"""catalog_match.quality: only broken images are hard-rejected (decision D5)."""

import io

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFilter

from catalog_match.quality import assess


def _packshot(size, fill=0.5, seed=0, jpeg_q=90):
    """A sharp labelled bottle on pure #FFFFFF, JPEG round-tripped like a web image."""
    rng = np.random.default_rng(seed)
    w = h = size
    img = Image.new("RGB", (w, h), (255, 255, 255))
    side = np.sqrt(fill)
    pw, ph = int(w * side * 0.7), int(h * side)
    x0, y0 = (w - pw) // 2, (h - ph) // 2
    xs = np.linspace(-1, 1, pw)
    shade = 0.55 + 0.45 * np.sqrt(np.clip(1 - xs ** 2, 0, 1))
    body = np.array([30, 120, 200], np.float32) * shade[None, :, None] * np.ones((ph, 1, 1), np.float32)
    body += rng.normal(0, 5, body.shape)
    prod = Image.fromarray(np.clip(body, 0, 255).astype(np.uint8))
    d = ImageDraw.Draw(prod)
    d.rectangle([int(pw * .1), int(ph * .3), int(pw * .9), int(ph * .6)], fill=(250, 250, 240))
    for i in range(6):
        d.text((int(pw * .15), int(ph * .32) + i * int(ph * .045)), "ALMARAI FULL CREAM MILK 1L",
               fill=(200, 20, 20))
    img.paste(prod, (x0, y0))
    shadow = Image.new("L", (w, h), 0)
    ImageDraw.Draw(shadow).ellipse([x0, y0 + ph - 10, x0 + pw, y0 + ph + 20], fill=90)
    shadow = np.asarray(shadow.filter(ImageFilter.GaussianBlur(12)), np.float32)[..., None] / 255.0
    arr = np.asarray(img, np.float32)
    mask = np.zeros((h, w, 1), np.float32)
    mask[y0:y0 + ph, x0:x0 + pw] = 1
    arr = arr * (1 - shadow * (1 - mask) * 0.5)
    buf = io.BytesIO()
    Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8)).save(buf, "JPEG", quality=jpeg_q)
    buf.seek(0)
    return Image.open(buf).convert("RGB")


def _white_carton_on_white(size=800):
    """White carton on a white background (skeptic white_carton.py): only the print differs."""
    img = Image.new("RGB", (size, size), (255, 255, 255))
    d = ImageDraw.Draw(img)
    pw, ph = int(size * 0.45), int(size * 0.85)
    x0, y0 = (size - pw) // 2, (size - ph) // 2
    d.rounded_rectangle([x0, y0, x0 + pw, y0 + ph], radius=12, fill=(255, 255, 255),
                        outline=(200, 200, 200), width=2)
    d.rectangle([x0 + 5, y0 + int(ph * .55), x0 + pw - 5, y0 + int(ph * .72)], fill=(0, 120, 60))
    d.ellipse([x0 + pw * .25, y0 + ph * .15, x0 + pw * .75, y0 + ph * .40], outline=(0, 90, 160), width=5)
    for k in range(6):
        d.text((x0 + pw * .18, y0 + ph * .44 + k * 11), "FULL FAT MILK 1L", fill=(30, 30, 30))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    buf.seek(0)
    return Image.open(buf).convert("RGB")


def _pixel_share_at_least(img, level=254):
    g = np.asarray(img.convert("L"))
    return float((g >= level).mean())


@pytest.mark.parametrize("size", [800, 1500, 2000])
def test_white_packshots_pass(size):
    img = _packshot(size)
    # The fixture really is the case v1 rejected as 'overexposed' (>20 % clamped white).
    assert _pixel_share_at_least(img) > 0.40
    report = assess(img)
    assert report.hard_ok is True, report.hard_reasons
    assert report.hard_reasons == []
    assert report.soft["white_border_ratio"] > 0.95
    assert 0.0 < report.quality_score <= 1.0


def test_white_carton_on_white_passes():
    img = _white_carton_on_white()
    assert _pixel_share_at_least(img) > 0.75
    report = assess(img)
    assert report.hard_ok is True, report.hard_reasons


def test_junk_fails():
    rng = np.random.default_rng(3)
    noise = Image.fromarray(rng.integers(0, 256, (1000, 1000, 3), dtype=np.uint8))
    report = assess(noise)
    assert report.hard_ok is False
    assert "noise" in report.hard_reasons

    tiny = _packshot(120)
    report = assess(tiny)
    assert report.hard_ok is False
    assert report.hard_reasons == ["short_side<250"]

    strip = Image.new("RGB", (300, 1800), (255, 255, 255))
    ImageDraw.Draw(strip).rectangle([100, 200, 200, 1600], fill=(20, 90, 160))
    report = assess(strip)
    assert report.hard_ok is False
    assert report.hard_reasons == ["aspect_ratio"]


def test_noise_rule_fires_on_its_own():
    """A noise patch on a white frame is caught by the noise rule, not by the foreground rule."""
    rng = np.random.default_rng(5)
    frame = np.full((1000, 1000, 3), 255, np.uint8)
    frame[100:900, 100:900] = rng.integers(0, 256, (800, 800, 3), dtype=np.uint8)
    report = assess(Image.fromarray(frame))
    assert report.hard_reasons == ["noise"]


def test_foreground_bounds():
    blank = Image.new("RGB", (800, 800), (255, 255, 255))
    assert assess(blank).hard_reasons == ["foreground<3%"]
    full_bleed = Image.new("RGB", (1600, 600), (230, 30, 40))
    ImageDraw.Draw(full_bleed).text((50, 50), "SALE ALMARAI", fill=(255, 255, 255))
    assert assess(full_bleed).hard_reasons == ["foreground>98%"]


def test_no_exposure_or_blur_gate():
    """A soft, low-contrast (blurred) packshot is kept: blur and exposure are not hard gates."""
    img = _packshot(1500).filter(ImageFilter.GaussianBlur(6))
    report = assess(img)
    assert report.hard_ok is True
    sharp = assess(_packshot(1500))
    assert report.soft["fg_sharpness"] < sharp.soft["fg_sharpness"]


def test_rgba_composited_on_white():
    img = Image.new("RGBA", (800, 800), (0, 0, 0, 0))   # fully transparent black
    d = ImageDraw.Draw(img)
    d.rectangle([250, 100, 550, 700], fill=(20, 120, 200, 255))
    report = assess(img)
    # Composited on black the border would be black (ratio 0).
    assert report.soft["white_border_ratio"] > 0.9
    assert report.hard_ok is True


def test_white_cutout_on_transparent_is_foreground():
    img = Image.new("RGBA", (800, 800), (255, 255, 255, 0))
    ImageDraw.Draw(img).rectangle([250, 100, 550, 700], fill=(255, 255, 255, 255))
    report = assess(img)
    assert report.hard_ok is True, report.hard_reasons
    assert 0.2 < report.soft["foreground_ratio"] < 0.4


def test_unreadable_image_is_hard_fail_not_exception():
    class Broken:
        size = (800, 800)
        mode = "RGB"
        info = {}

        def convert(self, *_a, **_k):
            raise OSError("truncated")

    report = assess(Broken())
    assert report.hard_ok is False
    assert report.hard_reasons == ["decode_error"]
