"""Published image quality: cutout quality gate, automatic fallback, clean white sources, verified bytes.

The scenarios are the audit probes turned into tests (P1 opaque no-op segmentation, P2 faint alpha haze,
P3 second object, P4 a Gemini box that cuts off the cap, P5 a transparent PNG with an opaque grey box).
All tests are offline: sockets are blocked, PhotoRoom / remove.bg are fakes behind requests.post, the
Gemini box is a fake, and the URL download goes through a scripted HTTP session.
"""

import hashlib
import io
import json
import os
import socket
import subprocess
import sys

import numpy as np
import pytest
from PIL import Image, ImageDraw

import config
import http_client
import image_processor
from edge_shadow_engine import EdgeShadowEngine, alpha_bbox, compose_on_white_canvas

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WHITE = (255, 255, 255)
GREY = (150, 160, 170)
RED = (200, 30, 30)
CAP = (20, 20, 160)


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
    monkeypatch.setattr(config, "PHOTOROOM_CROP", False)
    monkeypatch.setattr(config, "WHITE_SOURCE_MODE", "log", raising=False)
    monkeypatch.setattr(config, "ENABLE_STUDIO_SHADOWS", False)
    monkeypatch.setattr(config, "PROXY_URL", "")
    monkeypatch.setattr(config, "CANDIDATE_STORE_DIR", str(tmp_path / "candidates"), raising=False)
    if hasattr(config, "OUTPUT_CANVAS_SIZE"):
        monkeypatch.delattr(config, "OUTPUT_CANVAS_SIZE")
    monkeypatch.delenv("OUTPUT_CANVAS_SIZE", raising=False)

    # The automatic fallback never uses the local methods for anything that could auto-publish.
    monkeypatch.setattr(image_processor, "_isolate_grabcut", lambda img: pytest.fail("GrabCut must not be used"))
    monkeypatch.setattr(image_processor, "_isolate_rembg", lambda img: pytest.fail("rembg must not be used"))

    work = tmp_path / "tmp"
    work.mkdir()
    monkeypatch.setattr(image_processor.tempfile, "tempdir", str(work))
    yield work


class FakeResponse:
    def __init__(self, status_code=200, content=b""):
        self.status_code = status_code
        self.content = content
        self.text = content.decode("utf-8", "replace") if isinstance(content, bytes) else str(content)

    def json(self):
        return json.loads(self.content)


class FakeHttpResponse:
    """Minimal stand-in for a curl_cffi streaming response."""

    def __init__(self, status_code=200, body=b"", headers=None):
        self.status_code = status_code
        self._body = body
        self.headers = headers or {}

    @property
    def content(self):
        return self._body

    def iter_content(self, chunk_size=65536):
        for i in range(0, len(self._body), chunk_size):
            yield self._body[i:i + chunk_size]

    def close(self):
        pass


class ScriptedSession:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        return self.script.pop(0)


def png_bytes(img):
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class Providers:
    """Fake PhotoRoom / remove.bg behind requests.post. handler(image, form) -> FakeResponse."""

    URLS = {image_processor.PHOTOROOM_URL: "photoroom", image_processor.REMOVE_BG_URL: "remove_bg_api"}

    def __init__(self, monkeypatch, photoroom=None, remove_bg=None):
        self.handlers = {"photoroom": photoroom, "remove_bg_api": remove_bg}
        self.calls = []
        monkeypatch.setattr(image_processor.requests, "post", self.post)

    def post(self, url, headers=None, files=None, data=None, timeout=None, **_kw):
        name = self.URLS.get(url)
        assert name is not None, f"unexpected POST to {url}"
        _filename, blob, _mime = files["image_file"]
        sent = Image.open(io.BytesIO(blob))
        sent.load()
        form = dict(data or {})
        self.calls.append((name, sent.size, form))
        handler = self.handlers[name]
        assert handler is not None, f"{name} must not be called"
        return handler(sent, form)

    def sizes(self):
        return [(name, size) for name, size, _form in self.calls]


def keyer(bg, tolerance=30, edit=None):
    """A deterministic segmenter: pixels close to `bg` become transparent (RGB zeroed, as PhotoRoom does).
    It returns the full frame unless the request asks for crop=true. `edit(rgba_array)` damages the cutout."""

    def handler(img, form):
        arr = np.asarray(img.convert("RGB")).astype(int)
        alpha = np.where(np.abs(arr - np.array(bg)).sum(axis=2) <= tolerance, 0, 255).astype(np.uint8)
        rgba = np.dstack([arr.astype(np.uint8), alpha])
        rgba[alpha == 0, :3] = 0
        if edit is not None:
            rgba = edit(rgba.copy())
        out = Image.fromarray(rgba, "RGBA")
        if form.get("crop") == "true":
            out = out.crop(alpha_bbox(out))
        return FakeResponse(200, png_bytes(out))

    return handler


def returns(rgba_image):
    return lambda _img, _form: FakeResponse(200, png_bytes(rgba_image))


def bottle(bg=WHITE, size=(600, 900), body=(200, 150, 400, 750)):
    """Red bottle with a blue cap on top (so a clipped cap is visible). Extent x 200..400, y 90..750."""
    img = Image.new("RGB", size, bg)
    draw = ImageDraw.Draw(img)
    draw.rectangle(body, fill=RED)
    cx = (body[0] + body[2]) // 2
    draw.rectangle((cx - 40, body[1] - 60, cx + 40, body[1]), fill=CAP)
    return img


def save(img, tmp_path, name="src.png"):
    path = tmp_path / name
    img.save(path, format="PNG")
    return str(path)


def run(src, **kwargs):
    kwargs.setdefault("bg_method", "photoroom")
    return image_processor.process_product_image_result(src, "Milk 1L", "Almarai", 800, 800, **kwargs)


def canvas_of(result):
    assert result.path, f"no canvas produced: {result}"
    with Image.open(result.path) as out:
        out.load()
        return np.asarray(out.convert("RGB")).copy()


def non_white_bbox(arr, threshold=250):
    ys, xs = np.nonzero((arr < threshold).any(axis=2))
    assert ys.size, "the canvas is entirely white"
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def colour_count(arr, rgb, tol=40):
    return int((np.abs(arr.astype(int) - np.array(rgb)).sum(axis=2) <= tol).sum())


def ideal_cutout(img, bg=WHITE):
    arr = np.asarray(img.convert("RGB")).astype(int)
    alpha = np.where(np.abs(arr - np.array(bg)).sum(axis=2) <= 30, 0, 255).astype(np.uint8)
    out = img.convert("RGBA")
    out.putalpha(Image.fromarray(alpha))
    return out


# ---------------------------------------------------------------------------
# assess_cutout: one flag per audit finding
# ---------------------------------------------------------------------------

def test_gate_clean_cutout_has_no_flags():
    cut = ideal_cutout(bottle())
    assert image_processor.assess_cutout(cut, frame_size=cut.size) == []


def test_gate_opaque_fill_p1():
    # P1: the provider returned the grey photo fully opaque (nothing was removed).
    cut = bottle(bg=GREY).convert("RGBA")
    assert image_processor.FLAG_OPAQUE_FILL in image_processor.assess_cutout(cut, frame_size=cut.size)


def test_gate_edge_clipped_only_on_crop_lines():
    # P4: the crop's top edge runs through the cap.
    crop = bottle().crop((180, 120, 420, 770))
    cut = ideal_cutout(crop)
    flags = image_processor.assess_cutout(cut, frame_size=cut.size, crop_sides=(True, True, True, True))
    assert flags == [image_processor.FLAG_EDGE_CLIPPED]
    # The same contact with the source image's own edge is not a crop line: not "clipped by the box".
    assert image_processor.assess_cutout(cut, frame_size=cut.size, crop_sides=(False,) * 4) == []
    # Only the sides that are crop lines count.
    assert image_processor.assess_cutout(cut, frame_size=cut.size, crop_sides=(True, False, True, True)) == []


def test_gate_skips_frame_checks_when_the_provider_cropped():
    # PhotoRoom crop=true: the cutout is the product's own box, so it touches every edge and is mostly opaque.
    tight = ideal_cutout(bottle()).crop((200, 90, 401, 751))
    assert image_processor.assess_cutout(tight, frame_size=(600, 900), crop_sides=(True,) * 4) == []
    # Taken as the full frame that was sent, the same pixels are clipped by every crop line.
    flags = image_processor.assess_cutout(tight, frame_size=tight.size, crop_sides=(True,) * 4)
    assert flags == [image_processor.FLAG_EDGE_CLIPPED]


def test_gate_alpha_haze_attached_to_the_product():
    cut = ideal_cutout(bottle())
    alpha = np.array(cut.getchannel("A"))
    alpha[700:760, 100:200] = 12   # a faint shadow wisp attached to the base, widening the box by 100 px
    cut.putalpha(Image.fromarray(alpha))
    assert image_processor.assess_cutout(cut, frame_size=cut.size) == [image_processor.FLAG_ALPHA_HAZE]


def test_gate_second_object_p3():
    arr = np.array(ideal_cutout(bottle()))
    big = arr.copy()
    big[820:880, 20:120] = (0, 0, 0, 255)       # 6000 px: 4.8% of the bottle
    assert image_processor.FLAG_SECOND_OBJECT in image_processor.assess_cutout(Image.fromarray(big, "RGBA"))
    small = arr.copy()
    small[820:830, 20:60] = (0, 0, 0, 255)      # 400 px: 0.3%, a speck
    assert image_processor.FLAG_SECOND_OBJECT not in image_processor.assess_cutout(Image.fromarray(small, "RGBA"))


def test_gate_upscaled():
    small = ideal_cutout(bottle(size=(150, 220), body=(50, 60, 100, 200)))   # product 51x161 px
    assert image_processor.assess_cutout(small, frame_size=small.size) == [image_processor.FLAG_UPSCALED]
    assert image_processor.assess_cutout(small, frame_size=small.size, canvas_size=(300, 300)) == []


def test_gate_too_small_on_canvas_when_something_else_sets_the_size():
    arr = np.array(ideal_cutout(bottle(size=(900, 1300), body=(350, 150, 550, 750))))
    arr[1100:1200, 400:500] = (90, 90, 90, 60)   # a visible grey ghost below (not haze, not solid)
    flags = image_processor.assess_cutout(Image.fromarray(arr, "RGBA"))
    # it sets the size, and being shadow-grey beyond the solid product it is also a kept shadow
    assert sorted(flags) == [image_processor.FLAG_KEPT_SHADOW, image_processor.FLAG_TOO_SMALL]
    arr[1100:1200, 400:500] = (60, 140, 220, 60)  # a light blue ghost: something else sets the size, no shadow
    assert image_processor.assess_cutout(Image.fromarray(arr, "RGBA")) == [image_processor.FLAG_TOO_SMALL]


def test_gate_opaque_backdrop_p5():
    src = Image.new("RGBA", (600, 600), (0, 0, 0, 0))
    draw = ImageDraw.Draw(src)
    draw.rectangle((60, 60, 540, 540), fill=(180, 180, 180, 255))   # grey photo box
    draw.rectangle((220, 150, 380, 480), fill=RED + (255,))           # the product on it
    assert image_processor.assess_cutout(src, check_backdrop=True) == [image_processor.FLAG_OPAQUE_BACKDROP]
    assert image_processor.assess_cutout(src, check_backdrop=False) == []
    # A plain rectangular carton (one colour, nothing on it) is not a backdrop.
    carton = Image.new("RGBA", (600, 600), (0, 0, 0, 0))
    ImageDraw.Draw(carton).rectangle((150, 60, 450, 540), fill=RED + (255,))
    assert image_processor.assess_cutout(carton, check_backdrop=True) == []


# ---------------------------------------------------------------------------
# P2: the size box and the cleanup agree on what is product
# ---------------------------------------------------------------------------

def test_separate_haze_is_removed_so_the_size_box_is_the_product():
    cut = ideal_cutout(bottle())
    clean_box = alpha_bbox(cut)
    alpha = np.array(cut.getchannel("A"))
    alpha[:, 0:20] = 12          # faint haze strip at the far left, invisible on white
    cut.putalpha(Image.fromarray(alpha))
    assert alpha_bbox(cut)[0] == 0, "precondition: the haze widens the size box"

    cleaned = EdgeShadowEngine.process_mask(cut)

    assert alpha_bbox(cleaned) == clean_box
    assert (np.asarray(cleaned.getchannel("A"))[:, 0:20] == 0).all()
    # A soft edge attached to the product is product, and is kept.
    soft = np.array(ideal_cutout(bottle()).getchannel("A"))
    soft[150:750, 401:404] = 12
    kept = np.asarray(EdgeShadowEngine.process_mask(_with_alpha(soft)).getchannel("A"))
    assert (kept[150:750, 401:404] == 12).all()


def _with_alpha(alpha):
    rgba = np.zeros(alpha.shape + (4,), np.uint8)
    rgba[..., 0] = 200
    rgba[..., 3] = alpha
    return Image.fromarray(rgba, "RGBA")


def test_p2_haze_no_longer_shrinks_or_shifts_the_product(monkeypatch, tmp_path):
    src = save(bottle(), tmp_path)

    def haze(rgba):
        rgba[:, 0:20, 3] = 12
        return rgba

    Providers(monkeypatch, photoroom=keyer(WHITE))
    clean = canvas_of(run(src))
    Providers(monkeypatch, photoroom=keyer(WHITE, edit=haze))
    result = run(src)

    assert result.isolated is True and result.quality_flags == []
    hazy = canvas_of(result)
    assert non_white_bbox(hazy) == non_white_bbox(clean)
    x0, y0, x1, y1 = non_white_bbox(hazy)
    assert abs((x0 + x1) / 2 - 400) <= 1 and abs((y0 + y1) / 2 - 400) <= 1
    assert y1 - y0 >= 700   # the product fills the 88% box (704 px) instead of shrinking


# ---------------------------------------------------------------------------
# The automatic fallback and the review state
# ---------------------------------------------------------------------------

def test_p1_opaque_segmentation_is_never_published_as_isolated(monkeypatch, tmp_path):
    src = save(bottle(bg=GREY), tmp_path)
    providers = Providers(monkeypatch, photoroom=lambda img, form: FakeResponse(200, png_bytes(img.convert("RGBA"))))

    result = run(src)

    assert result.path and (result.width, result.height) == (800, 800)
    assert result.isolated is False
    assert result.error is None
    assert result.provider == "photoroom"
    assert result.quality_flags == [image_processor.FLAG_OPAQUE_FILL]
    # No Gemini box and no remove.bg key: nothing else to try (and never GrabCut / rembg).
    assert providers.sizes() == [("photoroom", (600, 900))]


def test_p1_falls_back_to_remove_bg_when_it_is_configured(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "REMOVE_BG_API_KEY", "test-removebg-key")
    src = save(bottle(bg=GREY), tmp_path)
    providers = Providers(monkeypatch, photoroom=lambda img, form: FakeResponse(200, png_bytes(img.convert("RGBA"))),
                          remove_bg=keyer(GREY))

    result = run(src)

    assert providers.sizes() == [("photoroom", (600, 900)), ("remove_bg_api", (600, 900))]
    assert (result.isolated, result.provider, result.quality_flags) == (True, "remove_bg_api", [])
    out = canvas_of(result)
    assert colour_count(out, CAP) > 500 and out[5, 5].tolist() == [255, 255, 255]


def test_p3_second_object_goes_to_review(monkeypatch, tmp_path):
    src = save(bottle(), tmp_path)

    def blob(rgba):
        rgba[820:880, 20:120] = (0, 0, 0, 255)
        return rgba

    Providers(monkeypatch, photoroom=keyer(WHITE, edit=blob))
    result = run(src)

    assert result.path and result.isolated is False
    assert image_processor.FLAG_SECOND_OBJECT in result.quality_flags


def test_p4_box_that_cuts_the_cap_is_retried_without_the_box(monkeypatch, tmp_path):
    src = save(bottle(), tmp_path)
    boxes = []
    monkeypatch.setattr(image_processor, "_locate_product_box",
                        lambda img, name, brand: boxes.append(img.size) or [250, 300, 840, 700])
    providers = Providers(monkeypatch, photoroom=keyer(WHITE))

    result = run(src)

    assert boxes == [(600, 900)], "the Gemini box is asked once"
    first, second = providers.sizes()
    assert first[1] != (600, 900) and second == ("photoroom", (600, 900))
    assert (result.isolated, result.provider, result.quality_flags) == (True, "photoroom", [])
    out = canvas_of(result)
    assert colour_count(out, CAP) > 3000, "the cap was cut off"
    x0, y0, x1, y1 = non_white_bbox(out)
    assert y1 - y0 >= 700 and abs((x0 + x1) / 2 - 400) <= 1


def test_fallback_order_box_then_full_frame_then_the_other_paid_provider(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "REMOVE_BG_API_KEY", "test-removebg-key")
    src = save(bottle(), tmp_path)
    monkeypatch.setattr(image_processor, "_locate_product_box", lambda *a: [50, 250, 900, 750])

    def blob(rgba):
        h, w = rgba.shape[:2]
        rgba[h - 40:h - 10, 10:110] = (0, 0, 0, 255)   # a price tag the PhotoRoom fake keeps
        return rgba

    providers = Providers(monkeypatch, photoroom=keyer(WHITE, edit=blob), remove_bg=keyer(WHITE))

    result = run(src)

    names = [name for name, _size in providers.sizes()]
    assert names == ["photoroom", "photoroom", "remove_bg_api"]
    crop_size = providers.sizes()[0][1]
    assert providers.sizes()[1][1] == (600, 900)
    # The box was not the problem (no edge_clipped), so remove.bg gets the cropped frame.
    assert providers.sizes()[2][1] == crop_size
    assert (result.isolated, result.provider, result.quality_flags) == (True, "remove_bg_api", [])


def test_still_flagged_after_every_fallback_returns_the_review_state(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "REMOVE_BG_API_KEY", "test-removebg-key")
    src = save(bottle(), tmp_path)
    monkeypatch.setattr(image_processor, "_locate_product_box", lambda *a: [50, 250, 900, 750])

    def blob(rgba):
        h, w = rgba.shape[:2]
        rgba[h - 40:h - 10, 10:110] = (0, 0, 0, 255)
        return rgba

    providers = Providers(monkeypatch, photoroom=keyer(WHITE, edit=blob), remove_bg=keyer(WHITE, edit=blob))

    result = run(src)

    assert len(providers.calls) == 3
    assert result.path and (result.width, result.height) == (800, 800)
    assert result.isolated is False and result.error is None
    assert result.quality_flags == [image_processor.FLAG_SECOND_OBJECT]


def test_upscaled_alone_is_not_retried_with_a_paid_provider(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "REMOVE_BG_API_KEY", "test-removebg-key")
    src = save(bottle(size=(150, 220), body=(50, 60, 100, 200)), tmp_path)
    providers = Providers(monkeypatch, photoroom=keyer(WHITE), remove_bg=keyer(WHITE))

    result = run(src)

    assert [name for name, _ in providers.sizes()] == ["photoroom"]
    assert (result.isolated, result.quality_flags) == (False, [image_processor.FLAG_UPSCALED])
    assert result.path


def test_a_provider_error_on_the_retry_keeps_the_flagged_canvas(monkeypatch, tmp_path):
    src = save(bottle(), tmp_path)
    monkeypatch.setattr(image_processor, "_locate_product_box", lambda *a: [250, 300, 840, 700])
    answers = [keyer(WHITE), lambda img, form: FakeResponse(402, b'{"detail": "no credits"}')]
    providers = Providers(monkeypatch, photoroom=lambda img, form: answers.pop(0)(img, form))

    result = run(src)

    assert len(providers.calls) == 2
    assert result.path and result.isolated is False
    assert result.quality_flags == [image_processor.FLAG_EDGE_CLIPPED]


def test_p5_transparent_png_with_a_grey_box_is_not_source_alpha(monkeypatch, tmp_path):
    src_img = Image.new("RGBA", (1200, 1200), (0, 0, 0, 0))
    draw = ImageDraw.Draw(src_img)
    draw.rectangle((120, 120, 1080, 1080), fill=(180, 180, 180, 255))
    draw.rectangle((440, 300, 760, 960), fill=RED + (255,))
    src = save(src_img, tmp_path, "boxed.png")
    assert image_processor.is_background_already_removed(src) is True

    # A provider that isolates the product: published as isolated, by the provider.
    def product_only(img, form):
        arr = np.asarray(img.convert("RGB")).astype(int)
        alpha = np.where(np.abs(arr - np.array(RED)).sum(axis=2) <= 40, 255, 0).astype(np.uint8)
        return FakeResponse(200, png_bytes(Image.fromarray(np.dstack([arr.astype(np.uint8), alpha]), "RGBA")))

    providers = Providers(monkeypatch, photoroom=product_only)
    result = run(src)
    assert providers.sizes() == [("photoroom", (1200, 1200))]
    sent = providers.calls[0]
    assert (result.isolated, result.provider, result.quality_flags) == (True, "photoroom", [])
    out = canvas_of(result)
    assert colour_count(out, (180, 180, 180), tol=20) == 0, "the grey box was published"

    assert sent[2]["crop"] == "false"

    # A provider that keeps the grey box while the Gemini box puts the product inside it: never published.
    monkeypatch.setattr(image_processor, "_locate_product_box", lambda *a: [250, 367, 800, 633])
    providers = Providers(monkeypatch, photoroom=keyer(WHITE))
    result = run(src)
    assert result.path and result.isolated is False
    assert image_processor.FLAG_OPAQUE_BACKDROP in result.quality_flags
    assert len(providers.calls) == 2   # the box crop (clipped, opaque) and the full frame; no remove.bg key

    # Without a Gemini box, a provider that returns exactly the source's rectangle confirms it is the product
    # (two independent opinions: a printed carton, not a photo card): the free source alpha is used.
    monkeypatch.setattr(image_processor, "_locate_product_box", lambda *a: None)
    providers = Providers(monkeypatch, photoroom=keyer(WHITE))
    result = run(src)
    assert (result.isolated, result.provider, result.quality_flags) == (True, "source_alpha", [])
    assert len(providers.calls) == 1


def test_rounded_corner_photo_is_not_taken_as_an_isolated_source(monkeypatch, tmp_path):
    photo = bottle(bg=GREY).convert("RGBA")
    mask = Image.new("L", photo.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, photo.width - 1, photo.height - 1), radius=90, fill=255)
    photo.putalpha(mask)
    src = save(photo, tmp_path, "rounded.png")
    assert image_processor.is_background_already_removed(src) is True   # >5% of the border is transparent

    def grey_and_white(img, form):   # the corners reach the provider as white (the photo seen on white)
        arr = np.asarray(img.convert("RGB")).astype(int)
        near = lambda c: np.abs(arr - np.array(c)).sum(axis=2) <= 30
        alpha = np.where(near(GREY) | near(WHITE), 0, 255).astype(np.uint8)
        return FakeResponse(200, png_bytes(Image.fromarray(np.dstack([arr.astype(np.uint8), alpha]), "RGBA")))

    providers = Providers(monkeypatch, photoroom=grey_and_white)

    result = run(src)

    assert len(providers.calls) == 1, "the opaque photo must be isolated by the provider"
    assert (result.isolated, result.provider, result.quality_flags) == (True, "photoroom", [])


# ---------------------------------------------------------------------------
# PhotoRoom crop off by default; a good cutout's canvas is exactly what it was
# ---------------------------------------------------------------------------

def test_photoroom_crop_is_off_by_default():
    env = {k: v for k, v in os.environ.items() if k != "PHOTOROOM_CROP"}
    env.update({"DB_HOST": "127.0.0.1", "DB_PORT": "1", "DB_DATABASE": "no_such_database_for_this_test"})
    code = "import config; print(config.PHOTOROOM_CROP, config.WHITE_SOURCE_MODE)"
    out = subprocess.run([sys.executable, "-c", code], cwd=REPO_ROOT, env=env, capture_output=True, text=True,
                         timeout=60)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip().splitlines()[-1] == "False log"


def _soft_bottle_cutout(size=(600, 900)):
    """A good cutout with anti-aliased (soft) edges: drawn at 4x and downsampled."""
    big = Image.new("RGBA", (size[0] * 4, size[1] * 4), (0, 0, 0, 0))
    draw = ImageDraw.Draw(big)
    draw.rounded_rectangle((800, 600, 1600, 3000), radius=160, fill=RED + (255,))
    draw.ellipse((1040, 360, 1360, 680), fill=CAP + (255,))
    return big.resize(size, Image.Resampling.LANCZOS)


@pytest.mark.parametrize("crop", [False, True])
def test_good_cutout_canvas_is_unchanged(monkeypatch, tmp_path, crop):
    cutout = _soft_bottle_cutout()
    alpha = np.asarray(cutout.getchannel("A"))
    assert 0 < int(((alpha > 0) & (alpha < 255)).sum()), "precondition: soft edges"
    assert np.array_equal(np.asarray(EdgeShadowEngine.process_mask(cutout)), np.asarray(cutout))
    expected = np.asarray(compose_on_white_canvas(cutout, (800, 800)))
    monkeypatch.setattr(config, "PHOTOROOM_CROP", crop)
    src = save(bottle(bg=GREY), tmp_path)
    reply = cutout.crop(alpha_bbox(cutout)) if crop else cutout
    providers = Providers(monkeypatch, photoroom=returns(reply))

    result = run(src)

    assert providers.calls[0][2]["crop"] == ("true" if crop else "false")
    assert result.isolated is True
    assert np.array_equal(canvas_of(result), expected), "a clean cutout must be trimmed and centred exactly as before"


# ---------------------------------------------------------------------------
# Clean white-background sources (WHITE_SOURCE_MODE)
# ---------------------------------------------------------------------------

def test_white_source_cutout_rules():
    cut, why = image_processor._white_source_cutout(bottle())
    assert why is None
    alpha = np.asarray(cut.getchannel("A"))
    assert alpha[0, 0] == 0 and alpha[400, 300] == 255 and alpha[100, 300] == 255   # body and cap
    assert alpha_bbox(cut) == (200, 90, 401, 751)

    assert image_processor._white_source_cutout(bottle(bg=(250, 250, 250)))[1] is None
    assert image_processor._white_source_cutout(bottle(bg=(238, 238, 238)))[1] == "background_not_white"
    straw = bottle()
    ImageDraw.Draw(straw).rectangle((299, 0, 301, 90), fill=(10, 10, 10))   # 3 px reach the top edge
    assert image_processor._white_source_cutout(straw)[1] == "product_touches_frame"
    two = bottle()
    ImageDraw.Draw(two).rectangle((20, 800, 160, 880), fill=(10, 10, 10))
    assert image_processor._white_source_cutout(two)[1] == "several_objects"

    # A white bottle with a thin grey outline keeps its white inside (only white connected to the frame goes).
    white_bottle = Image.new("RGB", (600, 900), WHITE)
    ImageDraw.Draw(white_bottle).rectangle((200, 150, 400, 750), fill=(252, 252, 252), outline=(200, 200, 200),
                                           width=2)
    cut, why = image_processor._white_source_cutout(white_bottle)
    assert why is None and np.asarray(cut.getchannel("A"))[450, 300] == 255


def _counting_box(monkeypatch):
    calls = []
    monkeypatch.setattr(image_processor, "_locate_product_box", lambda img, n, b: calls.append(img.size) or None)
    return calls


def test_white_source_log_mode_records_but_still_uses_the_paid_path(monkeypatch, tmp_path):
    src = save(bottle(), tmp_path)
    boxes = _counting_box(monkeypatch)
    providers = Providers(monkeypatch, photoroom=keyer(WHITE))

    result = run(src)

    assert len(providers.calls) == 1 and len(boxes) == 1
    assert (result.isolated, result.provider, result.white_source) == (True, "photoroom", "eligible")


def test_white_source_on_mode_skips_photoroom_and_the_gemini_box(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "WHITE_SOURCE_MODE", "on")
    src = save(bottle(), tmp_path)
    boxes = _counting_box(monkeypatch)
    providers = Providers(monkeypatch)   # any call fails the test

    result = run(src)

    assert providers.calls == [] and boxes == []
    assert (result.isolated, result.provider, result.white_source, result.quality_flags) == \
        (True, "white_source", "used", [])
    out = canvas_of(result)
    x0, y0, x1, y1 = non_white_bbox(out)
    assert y1 - y0 == 704 and abs((x0 + x1) / 2 - 400) <= 1 and abs((y0 + y1) / 2 - 400) <= 1
    assert colour_count(out, CAP) > 3000


def test_white_source_on_mode_uses_the_paid_path_when_the_source_is_not_clean(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "WHITE_SOURCE_MODE", "on")
    grey = save(bottle(bg=GREY), tmp_path, "grey.png")
    providers = Providers(monkeypatch, photoroom=keyer(GREY))
    result = run(grey)
    assert len(providers.calls) == 1
    assert (result.provider, result.white_source) == ("photoroom", "ineligible:background_not_white")

    # Eligible (one object holds 98.5%) but the gate sees a second object (1.5% of the main): paid path.
    speck = bottle()
    ImageDraw.Draw(speck).rectangle((20, 820, 69, 869), fill=(10, 10, 10))   # 2500 px vs ~142000 px
    src = save(speck, tmp_path, "speck.png")

    def clean(rgba):
        rgba[800:890, 0:100] = 0
        return rgba

    providers = Providers(monkeypatch, photoroom=keyer(WHITE, edit=clean))
    result = run(src)
    assert len(providers.calls) == 1
    assert (result.isolated, result.provider) == (True, "photoroom")
    assert result.white_source == "flagged:" + image_processor.FLAG_SECOND_OBJECT


def test_white_source_off_mode_does_not_look(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "WHITE_SOURCE_MODE", "off")
    monkeypatch.setattr(image_processor, "_white_source_cutout", lambda img: pytest.fail("must not run when off"))
    Providers(monkeypatch, photoroom=keyer(WHITE))
    result = run(save(bottle(), tmp_path))
    assert (result.isolated, result.white_source) == (True, None)


# ---------------------------------------------------------------------------
# Never publish different bytes than were verified
# ---------------------------------------------------------------------------

def test_redownloaded_bytes_must_match_the_verified_sha256(monkeypatch, tmp_path):
    (tmp_path / "candidates").mkdir()      # the store exists but the verified file is gone
    verified = png_bytes(bottle())
    sha = hashlib.sha256(verified).hexdigest()
    changed = png_bytes(bottle(body=(150, 150, 450, 750)))   # the URL now serves another picture

    providers = Providers(monkeypatch, photoroom=keyer(WHITE))
    session = ScriptedSession([FakeHttpResponse(200, changed, {"Content-Type": "image/png"})])
    monkeypatch.setattr(http_client, "_new_session", lambda: session)
    result = run("https://cdn.example.ae/p/1.png", candidate_sha256=sha.upper())
    assert (result.path, result.isolated, result.error) == (None, False, "source_changed")
    assert providers.calls == [], "nothing may be isolated or published from unverified bytes"

    session = ScriptedSession([FakeHttpResponse(200, verified, {"Content-Type": "image/png"})])
    monkeypatch.setattr(http_client, "_new_session", lambda: session)
    result = run("https://cdn.example.ae/p/1.png", candidate_sha256=sha)
    assert result.isolated is True and result.error is None

    # The same rule for a local file read again.
    local = tmp_path / "local.png"
    local.write_bytes(changed)
    assert run(str(local), candidate_sha256=sha).error == "source_changed"


# ---------------------------------------------------------------------------
# One processing profile: the canvas depends only on the explicit arguments
# ---------------------------------------------------------------------------

def test_same_profile_gives_identical_output_whatever_the_global_settings(monkeypatch, tmp_path):
    src = save(bottle(bg=GREY), tmp_path)
    Providers(monkeypatch, photoroom=keyer(GREY))

    def render(enhance):
        return canvas_of(image_processor.process_product_image_result(
            src, "Milk", "Almarai", 800, 800, bg_method="photoroom", enhance=enhance))

    plain = render(False)
    enhanced = render(True)
    assert not np.array_equal(plain, enhanced)

    monkeypatch.setattr(config, "OUTPUT_CANVAS_SIZE", 1200, raising=False)
    monkeypatch.setattr(config, "IMAGE_TARGET_SIZE", (1000, 1000))
    monkeypatch.setattr(config, "BG_REMOVAL_METHOD", "remove_bg_api")
    monkeypatch.setattr(config, "ENABLE_IMAGE_ENHANCEMENT", True)
    monkeypatch.setattr(config, "PHOTOROOM_CROP", True)
    for value in (False, "false", "0", "", "off", None):
        assert np.array_equal(render(value), plain), f"enhance={value!r} changed the canvas"
    for value in (True, "true", "1", "on"):
        assert np.array_equal(render(value), enhanced), f"enhance={value!r} changed the canvas"
