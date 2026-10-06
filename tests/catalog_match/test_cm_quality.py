"""catalog_match.quality: only broken images are hard-rejected (decision D5)."""

import io
import os
import sys

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
    # a solid-colour graphic with one line of text: its plain backdrop is nearly all of it (a blank frame)
    full_bleed = Image.new("RGB", (1600, 600), (230, 30, 40))
    ImageDraw.Draw(full_bleed).text((50, 50), "SALE ALMARAI", fill=(255, 255, 255))
    assert assess(full_bleed).hard_reasons == ["foreground<3%"]


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


# ---------------------------------------------------------------------------
# No white background: the border band decides (run exports 2026-10-04/05: 'foreground>98%' hard-rejected 89 real
# packshots in 49 rows, Lulu's 1920 px 'Shan Meat Masala 100 g' among them)
# ---------------------------------------------------------------------------

def _jpeg(img, quality=85):
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=quality)
    buf.seek(0)
    return Image.open(buf).convert("RGB")


def _pack_on(bg, size=(1200, 1200), box=(0.3, 0.12, 0.7, 0.88), body=(30, 120, 200), vignette=0.0, grain=0.0,
             gradient=None, seed=0):
    """A labelled pack on a plain studio backdrop of any colour (optional vignette, sensor grain, vertical sweep)."""
    rng = np.random.default_rng(seed)
    w, h = size
    arr = np.zeros((h, w, 3), np.float32) + np.array(bg, np.float32)
    if gradient is not None:
        t = np.linspace(0.0, 1.0, h, dtype=np.float32)[:, None, None]
        arr = arr * (1 - t) + np.array(gradient, np.float32)[None, None, :] * t
    if vignette:
        yy, xx = np.mgrid[0:h, 0:w]
        r = np.sqrt(((xx - w / 2) / (w / 2)) ** 2 + ((yy - h / 2) / (h / 2)) ** 2) / np.sqrt(2)
        arr -= (r * vignette)[..., None]
    if grain:
        arr += rng.normal(0, grain, arr.shape)
    img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
    d = ImageDraw.Draw(img)
    x0, y0, x1, y1 = int(box[0] * w), int(box[1] * h), int(box[2] * w), int(box[3] * h)
    d.rounded_rectangle([x0, y0, x1, y1], radius=20, fill=body)
    d.rectangle([x0 + (x1 - x0) // 8, y0 + (y1 - y0) // 3, x1 - (x1 - x0) // 8, y0 + (y1 - y0) // 2],
                fill=(250, 250, 240))
    for i in range(6):
        d.text((x0 + (x1 - x0) // 6, y0 + (y1 - y0) // 3 + 8 + i * 14), "SHAN MEAT MASALA 100G", fill=(200, 20, 20))
    return _jpeg(img.filter(ImageFilter.GaussianBlur(0.8)))


def _front_cropped_to_frame(size=(1000, 1000)):
    """A pack front cropped to the frame (its own print touches every edge)."""
    w, h = size
    img = Image.new("RGB", size, (190, 25, 30))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, w, int(h * 0.05)], fill=(240, 200, 20))
    d.ellipse([w * 0.2, h * 0.2, w * 0.8, h * 0.6], fill=(250, 240, 220))
    for i in range(10):
        d.text((w * 0.25, h * 0.3 + i * 18), "SHAN MEAT MASALA", fill=(20, 20, 20))
    d.rectangle([w * 0.1, h * 0.7, w * 0.9, h * 0.85], fill=(30, 110, 40))
    return _jpeg(img.filter(ImageFilter.GaussianBlur(0.8)))


def _busy_scene(size=(1200, 900), seed=1):
    """Something different all along the border (a kitchen or street photo)."""
    rng = np.random.default_rng(seed)
    w, h = size
    img = Image.fromarray(np.clip(rng.normal(128, 30, (h, w, 3)), 0, 255).astype(np.uint8)).filter(
        ImageFilter.GaussianBlur(3))
    d = ImageDraw.Draw(img)
    for _ in range(60):
        x, y = rng.integers(0, w), rng.integers(0, h)
        r = rng.integers(20, 160)
        d.ellipse([x - r, y - r, x + r, y + r], fill=tuple(int(v) for v in rng.integers(0, 256, 3)))
    return _jpeg(img.filter(ImageFilter.GaussianBlur(1.5)))


def _shelf(size=(1200, 900), seed=2):
    """A supermarket shelf: packs edge to edge on every row."""
    rng = np.random.default_rng(seed)
    w, h = size
    img = Image.new("RGB", size, (200, 200, 205))
    d = ImageDraw.Draw(img)
    for row in range(4):
        y0 = int(row * h / 4)
        d.rectangle([0, y0 + h // 4 - 12, w, y0 + h // 4], fill=(60, 60, 70))
        x = -int(rng.integers(0, 60))
        while x < w:
            pw = int(rng.integers(50, 120))
            d.rectangle([x, y0 + 10, x + pw, y0 + h // 4 - 14], fill=tuple(int(v) for v in rng.integers(0, 256, 3)))
            d.text((x + 5, y0 + 40), "BRAND", fill=(255, 255, 255))
            x += pw + 4
    return _jpeg(img.filter(ImageFilter.GaussianBlur(0.8)))


def _hand_held(size=(1000, 1000)):
    """A pack held in a hand in front of a blurred kitchen."""
    img = _busy_scene(size, 3).filter(ImageFilter.GaussianBlur(8))
    d = ImageDraw.Draw(img)
    w, h = size
    d.ellipse([w * 0.25, h * 0.6, w * 0.75, h * 1.1], fill=(225, 180, 150))
    d.rounded_rectangle([w * 0.33, h * 0.15, w * 0.67, h * 0.8], radius=12, fill=(30, 120, 200))
    return _jpeg(img)


def _imagegen():
    eval_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "eval")
    if eval_dir not in sys.path:
        sys.path.insert(0, eval_dir)
    import imagegen
    return imagegen


PLAIN_BACKDROPS = {
    "light_grey": dict(bg=(242, 242, 242)),
    "grey_with_grain": dict(bg=(230, 230, 230), grain=3),
    "vignette": dict(bg=(246, 246, 246), vignette=14),
    "studio_sweep": dict(bg=(238, 238, 238), gradient=(205, 205, 210)),
    "yellow": dict(bg=(250, 215, 0)),
    "red": dict(bg=(200, 30, 30), body=(240, 240, 240)),
    "black": dict(bg=(15, 15, 15), body=(240, 200, 20)),
    "touching_top_and_bottom": dict(bg=(240, 240, 240), box=(0.3, 0.0, 0.7, 1.0)),
    "touching_left_and_right": dict(bg=(238, 238, 238), box=(0.0, 0.2, 1.0, 0.8)),
}


@pytest.mark.parametrize("name", sorted(PLAIN_BACKDROPS))
def test_packshot_on_a_plain_backdrop_of_any_colour_passes_with_a_soft_penalty(name):
    img = _pack_on(**PLAIN_BACKDROPS[name])
    report = assess(img)
    assert report.soft["foreground_ratio"] > 0.98          # no white background at all: rejected before
    assert report.hard_ok is True, report.hard_reasons
    assert report.soft["full_frame"] == 1.0
    assert report.soft["white_border_ratio"] < 0.05
    # the soft penalty: the same pack on a white backdrop ranks above it
    white = assess(_pack_on((255, 255, 255), box=PLAIN_BACKDROPS[name].get("box", (0.3, 0.12, 0.7, 0.88))))
    assert white.hard_ok and white.soft["full_frame"] == 0.0
    assert 0.0 < report.quality_score < white.quality_score


def test_pack_front_cropped_to_the_frame_passes():
    report = assess(_front_cropped_to_frame())
    assert report.hard_ok is True, report.hard_reasons
    assert report.soft["full_frame"] == 1.0 and report.quality_score > 0.0


@pytest.mark.parametrize("make", [_busy_scene, _shelf, _hand_held], ids=["scene", "shelf", "hand_held"])
def test_busy_frames_with_no_white_background_are_still_rejected(make):
    report = assess(make())
    assert report.soft["foreground_ratio"] > 0.98
    assert report.hard_ok is False
    assert report.hard_reasons == ["busy_background"]
    assert report.quality_score == 0.0


@pytest.mark.parametrize("seed", [100, 101, 102, 103])
def test_eval_banners_with_no_white_are_busy(seed):
    """The offline eval's banners (a gradient backdrop, splashes, a row of packs): when nothing in them is white
    they are 'busy_background'; the two plain ends of a gradient are two colours, not one plain backdrop."""
    report = assess(_imagegen().render({"kind": "banner", "label_text": "AL AIN|WATER|1L"}, seed))
    assert report.soft["foreground_ratio"] > 0.98
    assert report.hard_reasons == ["busy_background"]


def test_noise_frame_is_busy_and_noise():
    rng = np.random.default_rng(3)
    report = assess(Image.fromarray(rng.integers(0, 256, (1000, 1000, 3), dtype=np.uint8)))
    assert report.hard_reasons == ["busy_background", "noise"]


def test_clean_packshot_on_grey_outranks_a_busy_photo_that_passes_the_gates():
    """A lifestyle photo with some white in it passes the gates (as before) but scores below a clean grey packshot."""
    imagegen = _imagegen()
    grey = assess(_pack_on((242, 242, 242)))
    for seed in (3, 7, 100):
        busy = assess(imagegen.render({"kind": "lifestyle_clutter"}, seed))
        assert busy.hard_ok is True and busy.soft["full_frame"] == 0.0
        assert grey.quality_score > busy.quality_score


def test_white_backdrop_path_is_unchanged():
    """A frame with a white background never reaches the backdrop judgement: no 'border_uniformity', no penalty."""
    report = assess(_packshot(1200))
    assert report.soft["full_frame"] == 0.0
    assert "border_uniformity" not in report.soft


def test_other_gates_still_apply_to_a_plain_coloured_frame():
    small = Image.new("RGB", (200, 200), (242, 242, 242))
    ImageDraw.Draw(small).rectangle([60, 30, 140, 170], fill=(30, 120, 200))
    assert assess(small).hard_reasons == ["short_side<250"]
    strip = _pack_on((242, 242, 242), size=(300, 1800), box=(0.2, 0.1, 0.8, 0.9))
    assert assess(strip).hard_reasons == ["aspect_ratio"]
    blank = Image.new("RGB", (900, 900), (238, 238, 238))
    assert assess(blank).hard_reasons == ["foreground<3%"]
