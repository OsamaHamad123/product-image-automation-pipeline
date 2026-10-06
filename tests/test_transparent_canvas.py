"""Transparent publish canvas (OUTPUT_BACKGROUND = transparent) and the finishing every cutout gets (cutout_finish).

The app shows the published image on dark and light themes, so the master is an RGBA PNG with no shadow, uploaded to
Cloudinary as is (f_auto keeps the alpha), with a white JPEG on demand from the same asset (b_white,f_jpg). Before the
canvas: enclosed holes that are product are filled from the source, background spill is removed from soft edges, and a
light rim that would show on dark mode either triggers one PhotoRoom re-isolation or sends the image to review.

Offline: sockets are blocked and PhotoRoom / Cloudinary are fakes. The white canvas keeps its own tests
(test_publish_canvas.py, test_cloudinary_delivery.py ... pinned to OUTPUT_BACKGROUND=white).
"""

import io
import re
import socket
from pathlib import Path

import cloudinary
import cloudinary.uploader
import numpy as np
import pytest
from PIL import Image

import cloudinary_storage
import config
import cutout_finish as cf
import http_client
import image_processor
from test_cloudinary_delivery import AccountUploader, FakeUploader
from test_laqta_health import PHP, SETTINGS_PHP, _kernel, _php, _settings, _sql, _put, app_env  # noqa: F401
import packshot_synth as ps
from test_publish_canvas import BG, BLUE, GREEN, RED, FakeResponse, bottle_source, install_photoroom, png_bytes, segmenter

ROOT = Path(__file__).resolve().parents[1]
CORE_JS = ROOT / "dashboard" / "public" / "js" / "review" / "core.js"
DARK = 18


def _refuse(*_args, **_kwargs):
    raise RuntimeError("network access is blocked in tests")


@pytest.fixture(autouse=True)
def offline(request, monkeypatch, tmp_path):
    if "app_env" not in request.fixturenames:          # the Settings page test talks to the test MariaDB
        monkeypatch.setattr(socket.socket, "connect", _refuse)
        monkeypatch.setattr(socket, "create_connection", _refuse)
        monkeypatch.setattr(socket, "getaddrinfo", _refuse)
    monkeypatch.setattr(http_client, "_new_session", lambda: None)
    monkeypatch.setattr(image_processor.requests, "post", _refuse)
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    monkeypatch.setattr(config, "PHOTOROOM_API_KEY", "test-photoroom-key")
    monkeypatch.setattr(config, "REMOVE_BG_API_KEY", "")
    monkeypatch.setattr(config, "BG_REMOVAL_METHOD", "photoroom")
    monkeypatch.setattr(config, "ENABLE_STUDIO_SHADOWS", False)
    monkeypatch.setattr(config, "PROXY_URL", "")
    monkeypatch.setattr(config, "CANDIDATE_STORE_DIR", str(tmp_path / "candidates"), raising=False)
    monkeypatch.setattr(config, "OUTPUT_CANVAS_SIZE", 800, raising=False)
    monkeypatch.setattr(config, "OUTPUT_BACKGROUND", "transparent", raising=False)
    monkeypatch.setattr(config, "OUTPUT_PRODUCT_FILL", "0.88", raising=False)
    work = tmp_path / "tmp"
    work.mkdir()
    monkeypatch.setattr(image_processor.tempfile, "tempdir", str(work))
    saved = dict(cloudinary.config().__dict__)
    cloudinary.config(cloud_name="demo", api_key="key", api_secret="secret", secure=True)
    yield
    cloudinary.config().__dict__.clear()
    cloudinary.config().__dict__.update(saved)


# ---------------------------------------------------------------------------
# Synthetic products
# ---------------------------------------------------------------------------

def disc(colour=(60, 30, 30), size=240, radius=80, ramp=3.0, backdrop=255):
    """A round product on a flat backdrop with a soft edge: (source RGB, cutout RGBA whose edge carries the backdrop)."""
    yy, xx = np.mgrid[:size, :size]
    r = np.sqrt((yy - size / 2) ** 2 + (xx - size / 2) ** 2)
    a = np.clip((radius - r) / ramp + 0.5, 0, 1)[..., None]
    src = np.rint(a * np.asarray(colour, np.float32) + (1 - a) * backdrop).astype(np.uint8)
    cut = np.dstack([src, np.rint(a[..., 0] * 255).astype(np.uint8)])
    return Image.fromarray(src), Image.fromarray(cut, "RGBA"), r


def carton(panel_level=248, panel_noise=1.5, hole_on_border=False):
    """A blue carton on white whose white printed panel the cutout model ate (alpha 0 over the panel)."""
    src = np.full((200, 200, 3), 255, np.uint8)
    src[40:160, 60:140] = (40, 80, 160)
    rng = np.random.default_rng(7)
    src[80:120, 80:120] = np.clip(panel_level + rng.normal(0, panel_noise, (40, 40, 3)), 0, 255).astype(np.uint8)
    cut = np.zeros((200, 200, 4), np.uint8)
    cut[..., :3] = src
    cut[40:160, 60:140, 3] = 255
    cut[80:120, 80:120, 3] = 0
    if hole_on_border:
        cut[80:120, 60:80, 3] = 0          # the panel opens to the outside: not an enclosed hole
    return Image.fromarray(src), Image.fromarray(cut, "RGBA")


def jug():
    """A milk jug whose handle opening shows the white backdrop itself: a real see-through hole."""
    src = np.full((200, 200, 3), 255, np.uint8)
    src[40:160, 60:140] = (232, 232, 236)
    src[70:110, 100:125] = 255
    cut = np.zeros((200, 200, 4), np.uint8)
    cut[..., :3] = src
    cut[40:160, 60:140, 3] = 255
    cut[70:110, 100:125, 3] = 0
    return Image.fromarray(src), Image.fromarray(cut, "RGBA")


def clean_segmenter():
    """A provider with clean edges for bottle_source: background out, every kept pixel its own pure colour."""
    palette = np.array([BLUE, RED, GREEN], np.int32)

    def handler(img):
        arr = np.asarray(img.convert("RGB")).astype(np.int32)
        keep = np.abs(arr - np.array(BG)).sum(axis=2) > 90
        nearest = palette[np.abs(arr[..., None, :] - palette).sum(axis=-1).argmin(axis=-1)]
        rgba = np.dstack([np.where(keep[..., None], nearest, 0), np.where(keep, 255, 0)]).astype(np.uint8)
        return FakeResponse(200, png_bytes(Image.fromarray(rgba, "RGBA")))

    return handler


def bands_on_dark(canvas, width=2):
    """Mean luminance on #121212 of the outer `width`-px band of the visible product and of the band just inside it."""
    import cv2

    arr = np.asarray(canvas.convert("RGBA"), np.float32)
    a = arr[..., 3:] / 255.0
    lum = ((a * arr[..., :3] + (1 - a) * DARK) @ np.array([0.299, 0.587, 0.114]))
    visible = (arr[..., 3] >= 8).astype(np.uint8)
    dist = cv2.distanceTransform(np.pad(visible, 1), cv2.DIST_L2, 3)[1:-1, 1:-1]
    outer = (visible > 0) & (dist <= width)
    inner = (visible > 0) & (dist > width) & (dist <= width + 6) & (arr[..., 3] >= 250)
    return float(lum[outer].mean()), float(lum[inner].mean())


# ---------------------------------------------------------------------------
# The canvas
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("size, fill, side", [(800, None, 704), (1200, None, 1056), (800, 0.5, 400)])
def test_the_canvas_is_a_transparent_square_with_the_product_filling_and_centred(monkeypatch, size, fill, side):
    if fill is not None:
        monkeypatch.setattr(config, "OUTPUT_PRODUCT_FILL", str(fill), raising=False)
    cut = Image.new("RGBA", (500, 500), (0, 0, 0, 0))
    cut.paste(Image.new("RGBA", (300, 150), (40, 70, 200, 255)), (100, 200))     # wide product, loose margins
    canvas = cf.transparent_canvas(cut, (size, size))
    assert canvas.mode == "RGBA" and canvas.size == (size, size)
    left, top, right, bottom = canvas.getchannel("A").getbbox()
    assert right - left == side and abs((bottom - top) - side / 2) <= 1          # longer side fills, ratio kept
    assert abs((left + right) - size) <= 1 and abs((top + bottom) - size) <= 1  # centred
    alpha = np.asarray(canvas.getchannel("A"))
    assert alpha[0, 0] == alpha[-1, -1] == 0 and set(np.unique(alpha)) <= {0, 255}   # no shadow, nothing baked


def test_the_product_fill_setting_is_clamped(monkeypatch):
    from catalog_match import settings
    for raw, want in (("0.88", 0.88), ("2", 1.0), ("0.1", 0.5), ("bogus", 0.88), ("nan", 0.88)):
        monkeypatch.setattr(config, "OUTPUT_PRODUCT_FILL", raw, raising=False)
        assert settings.output_product_fill() == pytest.approx(want)
    for raw, want in (("white", "white"), ("TRANSPARENT", "transparent"), ("grey", "transparent"), ("", "transparent")):
        monkeypatch.setattr(config, "OUTPUT_BACKGROUND", raw, raising=False)
        assert settings.output_background() == want


def test_a_published_cutout_is_an_rgba_png_with_no_shadow(monkeypatch, tmp_path):
    src = bottle_source(tmp_path / "src.png", 600, 1200)
    calls = install_photoroom(monkeypatch, clean_segmenter())
    result = image_processor.process_product_image_result(src, "Almarai Milk 1L", "Almarai", bg_method="photoroom")
    assert len(calls) == 1 and result.isolated is True and result.provider == "photoroom"
    with Image.open(result.path) as out:
        out.load()
        assert out.format == "PNG" and out.mode == "RGBA" and out.size == (800, 800)
        alpha = np.asarray(out.getchannel("A"))
    left, top, right, bottom = Image.fromarray(alpha).getbbox()
    assert bottom - top == 704 and abs((top + bottom) - 800) <= 1
    assert alpha[: top].max() == 0 and alpha[bottom:].max() == 0                 # nothing below the product: no shadow
    assert result.finish["background"] == "transparent" and result.finish["halo"] <= cf.HALO_MAX
    assert result.quality_flags == []
    image_processor.cleanup_processed_image(result.path)


def test_white_mode_keeps_the_opaque_white_canvas(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "OUTPUT_BACKGROUND", "white", raising=False)
    src = bottle_source(tmp_path / "src.png", 600, 1200)
    install_photoroom(monkeypatch, segmenter())
    result = image_processor.process_product_image_result(src, "Almarai Milk 1L", "Almarai", bg_method="photoroom")
    with Image.open(result.path) as out:
        assert out.mode == "RGB" and out.getpixel((0, 0)) == (255, 255, 255)
    assert result.finish == {} and result.isolated is True
    image_processor.cleanup_processed_image(result.path)


def test_an_image_published_without_isolation_stays_opaque_with_a_note(tmp_path):
    src = bottle_source(tmp_path / "src.png", 600, 1200)
    result = image_processor.process_product_image_result(src, "Almarai Milk 1L", "Almarai", bg_method="none")
    assert result.provider == "none" and result.isolated is False
    assert result.quality_notes == [cf.NOTE_NOT_CUT_OUT] and result.quality_flags == []
    with Image.open(result.path) as out:
        assert out.mode == "RGB" and out.getpixel((0, 0)) == (255, 255, 255)   # the photo on white, never see-through
    image_processor.cleanup_processed_image(result.path)
    labels = dict(re.findall(r"(\w+): '([^']+)'", CORE_JS.read_text(encoding="utf-8").split("const QUALITY_NOTE_LABELS")[1]
                             .split("};")[0]))
    assert labels["not_cut_out"] == "الصورة مش مقصوصة: رح تبين بخلفيتها بالتطبيق"


# ---------------------------------------------------------------------------
# Finishing: enclosed holes, defringe
# ---------------------------------------------------------------------------

def test_an_eaten_white_panel_on_a_carton_is_filled_from_the_source():
    src, cut = carton()
    out, info = cf.finish_cutout(src, cut, None, "rembg")
    arr = np.asarray(out)
    assert info["holes_filled"] == 1 and info["holes_left"] == 0
    assert arr[80:120, 80:120, 3].min() == 255                                   # the panel is product again
    assert np.abs(arr[100, 100, :3].astype(int) - np.asarray(src)[100, 100]).max() == 0


@pytest.mark.parametrize("level, noise", [(251, 0.5), (254, 8.0)])
def test_a_panel_a_few_levels_off_the_backdrop_or_textured_is_still_filled(level, noise):
    src, cut = carton(panel_level=level, panel_noise=noise)       # flat but 4 levels off; or at the level but shaded
    assert cf.finish_cutout(src, cut, None, "remove_bg_api")[1]["holes_filled"] == 1


def test_a_hole_within_two_levels_of_the_backdrop_stays_open():
    src, cut = carton(panel_level=254, panel_noise=0.4)
    assert cf.finish_cutout(src, cut, None, "remove_bg_api")[1]["holes_left"] == 1


def test_a_jug_handle_that_shows_the_backdrop_stays_transparent():
    src, cut = jug()
    out, info = cf.finish_cutout(src, cut, None, "rembg")
    assert info["holes_filled"] == 0 and info["holes_left"] == 1
    assert np.asarray(out)[90, 110, 3] == 0


def test_a_hole_open_to_the_outside_or_without_a_known_backdrop_is_left_alone():
    src, cut = carton(hole_on_border=True)
    assert cf.finish_cutout(src, cut, None, "rembg")[1]["holes_filled"] == 0
    busy = np.asarray(src).copy()
    busy[:12] = np.random.default_rng(1).integers(0, 255, busy[:12].shape)      # a lifestyle frame: no backdrop
    busy[-12:] = np.random.default_rng(2).integers(0, 255, busy[-12:].shape)
    busy[:, :12] = np.random.default_rng(3).integers(0, 255, busy[:, :12].shape)
    busy[:, -12:] = np.random.default_rng(4).integers(0, 255, busy[:, -12:].shape)
    src2, cut2 = carton()
    out, info = cf.finish_cutout(Image.fromarray(busy), cut2, None, "rembg")
    assert info["backdrop"] is None and info["holes_filled"] == 0 and info["holes_left"] == 1


def test_source_alpha_cutouts_are_never_hole_filled():
    src, cut = carton()
    assert cf.finish_cutout(src, cut, None, "source_alpha")[1]["holes_filled"] == 0


@pytest.mark.parametrize("ramp", [2.0, 3.0])
def test_a_white_backdrop_cutout_shows_no_bright_ring_on_dark(ramp):
    src, cut, _ = disc(ramp=ramp)
    raw_outer, raw_inner = bands_on_dark(cut)
    assert raw_outer > raw_inner + 15                       # the spill shows as a light ring before finishing
    out, info = cf.finish_cutout(src, cut, None, "rembg")
    assert info["defringed"] > 0
    for image in (out, cf.transparent_canvas(out, (400, 400))):
        outer, inner = bands_on_dark(image)                 # the 2-px outer band is no lighter than the product
        assert outer <= inner + 3


def test_defringe_leaves_already_clean_edges_alone():
    src, cut, _ = disc()
    arr = np.asarray(cut).copy()
    arr[..., :3] = (60, 30, 30)                             # a provider that already removed the spill
    out, info = cf.finish_cutout(src, Image.fromarray(arr, "RGBA"), None, "photoroom")
    assert info["defringed"] == 0 and np.array_equal(np.asarray(out), arr)


# ---------------------------------------------------------------------------
# Halo QA on dark and the one PhotoRoom retry
# ---------------------------------------------------------------------------

def ringed():
    """A dark product with a 2-px opaque white outline left from the backdrop (what u2net-style models leave)."""
    src, cut, r = disc()
    arr = np.asarray(cut).copy()
    ring = (r >= 78.5) & (r < 80.5)
    arr[ring, :3] = 250
    arr[ring, 3] = 255
    return src, Image.fromarray(arr, "RGBA")


def clean_disc_rgba():
    _, cut, _ = disc()
    arr = np.asarray(cut).copy()
    arr[..., :3] = (60, 30, 30)
    return Image.fromarray(arr, "RGBA")


def test_the_halo_score_sees_light_rims_only():
    src, cut = ringed()
    assert cf.halo_score(cf.transparent_canvas(cut, (400, 400))) > cf.HALO_MAX
    assert cf.halo_score(cf.transparent_canvas(clean_disc_rgba(), (400, 400))) == 0.0
    white = np.asarray(cut).copy()
    white[..., :3] = 250                                     # a white product is light all the way in: no halo
    assert cf.halo_score(cf.transparent_canvas(Image.fromarray(white, "RGBA"), (400, 400))) == 0.0


@pytest.fixture
def retry(monkeypatch):
    calls = []

    def install(cutout=None, error=None):
        def fake(img, method):
            calls.append((img.size, method))
            return (cutout, None) if cutout is not None else (None, error or "photoroom_402")
        monkeypatch.setattr(image_processor, "_isolate", fake)
        return calls

    return install


def _finish(src, cut, provider="rembg", isolated=True, flags=()):
    return cf.finish(src, cut, None, provider, isolated, list(flags), [], (400, 400))


def test_a_halo_from_another_provider_is_re_isolated_once_with_photoroom(retry):
    calls = retry(clean_disc_rgba())
    src, cut = ringed()
    done = _finish(src, cut)
    assert calls == [((240, 240), "photoroom")]
    assert done.provider == "photoroom" and done.isolated is True and cf.FLAG_DARK_HALO not in done.flags
    assert done.info["halo_retry"] == "photoroom" and done.info["provider_before"] == "rembg"


@pytest.mark.parametrize("case", ["photoroom_itself", "no_key", "paused", "not_isolated", "retry_fails",
                                  "retry_has_halo"])
def test_otherwise_the_halo_goes_to_review(monkeypatch, retry, case):
    src, cut = ringed()
    calls = retry(None if case == "retry_fails" else (cut if case == "retry_has_halo" else clean_disc_rgba()))
    provider, isolated, flags = "rembg", True, []
    if case == "photoroom_itself":
        provider = "photoroom"
    elif case == "no_key":
        monkeypatch.setattr(config, "PHOTOROOM_API_KEY", "")
    elif case == "paused":
        class Breaker:
            def paused_code(self, method):
                return "photoroom_402" if method == "photoroom" else None
        monkeypatch.setattr(image_processor, "cloud_breaker", lambda: Breaker(), raising=False)
    elif case == "not_isolated":
        isolated, flags = False, ["second_object"]
    done = _finish(src, cut, provider, isolated, flags)
    assert cf.FLAG_DARK_HALO in done.flags and done.isolated is False and done.provider == provider
    paid = case in ("retry_fails", "retry_has_halo")
    assert len(calls) == (1 if paid else 0)                  # one paid retry at most, never for a lost cause
    if paid:
        assert done.info["halo_retry"].startswith("photoroom_failed:")


def test_dark_halo_is_a_presentation_flag_with_one_arabic_sentence():
    import main
    import publish_check

    assert "dark_halo" in main.PRESENTATION_FLAGS
    assert publish_check.QUALITY_FLAG_TEXT["dark_halo"] == "حواف فاتحة بتبين على الوضع الغامق"
    core = CORE_JS.read_text(encoding="utf-8")
    assert "dark_halo: 'حواف فاتحة بتبين على الوضع الغامق'" in core
    assert "dark_halo:" in core.split("const PRESENTATION_FLAG_TEXT")[1].split("};")[0]


def test_clean_packshots_come_out_clean_on_the_transparent_canvas(tmp_path):
    """The false-positive corpus (bottles, cans, cartons, a jerrycan handle, clear bottles, packs, source PNGs):
    finishing must not flag any of them, and the light-rim score stays far under the threshold."""
    problems = []
    for name, items in ps.good_corpus().items():
        for shot, _options in items:
            result, _services = ps.run_shot(image_processor, config, shot, tmp_path, remove_bg="truth",
                                            background="transparent")
            with Image.open(result.path) as out:
                mode = out.mode
            if not result.isolated or result.quality_flags or mode != "RGBA" or result.finish["halo"] > cf.HALO_MAX / 2:
                problems.append((name, shot.name, result.quality_flags, result.finish))
            image_processor.cleanup_processed_image(result.path)
    assert problems == []


def test_a_crude_cutout_with_light_jpeg_edges_is_retried_with_photoroom_not_published(monkeypatch, tmp_path):
    """remove.bg answered with hard edges that keep the light JPEG blend: halo, so one PhotoRoom call fixes it."""
    src = bottle_source(tmp_path / "src.png", 600, 1200)
    monkeypatch.setattr(config, "REMOVE_BG_API_KEY", "test-removebg-key")
    sent = []

    def post(url, headers=None, files=None, data=None, timeout=None, **_kw):
        blob = files["image_file"][1]
        img = Image.open(io.BytesIO(blob))
        img.load()
        provider = "photoroom" if url == image_processor.PHOTOROOM_URL else "remove_bg_api"
        sent.append(provider)
        return (clean_segmenter() if provider == "photoroom" else segmenter())(img)

    monkeypatch.setattr(image_processor.requests, "post", post)
    result = image_processor.process_product_image_result(src, "Milk", "Almarai", bg_method="remove_bg_api")
    assert sent == ["remove_bg_api", "photoroom"]
    assert result.provider == "photoroom" and result.isolated is True and result.quality_flags == []
    assert result.finish["halo_retry"] == "photoroom" and result.finish["halo_before"] > cf.HALO_MAX
    image_processor.cleanup_processed_image(result.path)


# ---------------------------------------------------------------------------
# Cloudinary: alpha kept, plain f_auto delivery, white version on demand
# ---------------------------------------------------------------------------

@pytest.fixture
def rgba_png(tmp_path):
    canvas = cf.transparent_canvas(clean_disc_rgba(), (800, 800))
    path = tmp_path / "canvas.png"
    canvas.save(path, format="PNG")
    return str(path)


def test_the_transparent_master_is_uploaded_unchanged(monkeypatch, rgba_png):
    fake = FakeUploader()
    monkeypatch.setattr(cloudinary.uploader, "upload", fake)
    result = cloudinary_storage.upload_product_image(rgba_png, "Product", "Brand", folder="products/dairy")
    sent = fake.calls[0]["payload"]
    assert sent == open(rgba_png, "rb").read()                                # no flattening
    with Image.open(io.BytesIO(sent)) as img:
        assert img.mode == "RGBA" and img.getchannel("A").getextrema() == (0, 255)
    url = result.url
    # WebP keeps the alpha for every native client (f_auto answered okhttp / CFNetwork / Dart with a JPEG), width capped
    assert re.search(r"/image/upload/c_limit,w_1200,f_webp,q_auto/v\d+/products/dairy/[0-9a-f]{32}$", url), url
    for part in ("b_", "f_jpg", "f_png", "f_auto", "fl_", "c_pad", "e_"):
        assert part not in url.split("/image/upload/")[1].split("/")[0]


def test_the_upload_is_verified_against_the_transparent_bytes(monkeypatch, rgba_png):
    monkeypatch.setattr(cloudinary.uploader, "upload", AccountUploader())
    result = cloudinary_storage.upload_product_image(rgba_png, "Product", "Brand")
    assert result.url and result.error is None


def test_a_fully_transparent_file_is_not_uploaded(monkeypatch, tmp_path):
    fake = FakeUploader()
    monkeypatch.setattr(cloudinary.uploader, "upload", fake)
    path = tmp_path / "empty.png"
    Image.new("RGBA", (800, 800), (0, 0, 0, 0)).save(path)
    assert cloudinary_storage.upload_product_image(str(path), "P", "B").error == "upload_not_image"
    assert fake.calls == []


def test_the_white_version_is_the_same_asset_on_white_as_jpeg():
    url = cloudinary_storage.delivery_url("products/dairy/abc", 1700000000)
    white = cloudinary_storage.white_version_url(url)
    assert white == url.replace("/image/upload/c_limit,w_1200,f_webp,q_auto/",
                                "/image/upload/b_white,c_limit,w_1200,f_jpg,q_auto/")
    assert cloudinary_storage.white_version_url("needs_review:" + url) == white
    assert cloudinary_storage.white_version_url("https://example.com/x.png") is None
    assert cloudinary_storage.white_version_url(None) is None


def test_the_white_version_of_an_old_link_is_capped_too():
    old = "https://res.cloudinary.com/demo/image/upload/q_auto,f_auto/v1700000000/products/dairy/abc"
    white = "https://res.cloudinary.com/demo/image/upload/b_white,c_limit,w_1200,f_jpg,q_auto/v1700000000/products/dairy/abc"
    assert cloudinary_storage.white_version_url(old) == white
    assert cloudinary_storage.white_version_url("needs_review:" + old) == white
    # a white link, a foreign transformation or a bare upload path is not a delivery link we know
    assert cloudinary_storage.white_version_url(white) is None
    assert cloudinary_storage.white_version_url(old.replace("q_auto,f_auto", "w_300")) is None
    assert cloudinary_storage.white_version_url(old.replace("q_auto,f_auto/", "")) is None


def test_the_approval_result_carries_the_white_url():
    import cli_bridge

    res = {"link": "https://res.cloudinary.com/demo/image/upload/q_auto,f_auto/v1/products/x",
           "sheet_value": "https://res.cloudinary.com/demo/image/upload/q_auto,f_auto/v1/products/x", "isolated": True,
           "white_url": "https://res.cloudinary.com/demo/image/upload/b_white,q_auto,f_jpg/v1/products/x"}
    response = cli_bridge._published_response(res, "key-1", 2)
    assert response["white_url"].endswith("/b_white,q_auto,f_jpg/v1/products/x")


def test_publish_fingerprints_see_the_canvas_on_white(rgba_png, tmp_path):
    import main
    from catalog_match.fetch import phash_hex
    import image_dedup_bktree

    flat = cf.flatten_on_white(Image.open(rgba_png))
    flat_path = tmp_path / "flat.png"
    flat.save(flat_path)
    assert main._canvas_phash(rgba_png) == phash_hex(flat) == main._canvas_phash(str(flat_path))
    assert main._canvas_color_signature(rgba_png) == image_dedup_bktree.color_signature(str(flat_path))


def test_publish_image_returns_the_white_url(monkeypatch, tmp_path):
    import main

    src = bottle_source(tmp_path / "src.png", 600, 1200)
    install_photoroom(monkeypatch, clean_segmenter())
    uploaded = []

    def fake_upload(path, *a, **k):
        with Image.open(path) as img:
            uploaded.append(img.mode)
        return "https://res.cloudinary.com/demo/image/upload/q_auto,f_auto/v1/products/abc"

    monkeypatch.setattr(main.cloudinary_storage, "upload_product_image_to_cloudinary", fake_upload)
    monkeypatch.setattr(main.image_processor, "extract_metadata_from_image", lambda *a, **k: {})
    monkeypatch.setattr(main.local_cache_db, "find_image_owners", lambda *a, **k: [])
    monkeypatch.setattr(main.google_sheets, "update_image_link", lambda *a, **k: True)

    class Lock:
        def __enter__(self):
            return "ok"

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(main.local_cache_db, "sku_publish_lock", lambda key: Lock())
    out = main.publish_image(src, "Almarai Milk 1L", "Almarai", 2, None, 5, sku_key="k")
    assert out["status"] == "published" and uploaded == ["RGBA"]
    # an old-form link (q_auto,f_auto) still gets its white version, capped like the new one
    assert out["white_url"] == "https://res.cloudinary.com/demo/image/upload/b_white,c_limit,w_1200,f_jpg,q_auto/v1/products/abc"
    assert out["profile"]["background"] == "transparent" and out["finish"]["background"] == "transparent"


# ---------------------------------------------------------------------------
# The setting: config, profile, Settings page
# ---------------------------------------------------------------------------

def test_config_and_the_profile_carry_the_background(monkeypatch, fake_connection):
    import pymysql
    import processing_profile

    monkeypatch.setattr(config, "OUTPUT_BACKGROUND", config.OUTPUT_BACKGROUND)
    rows = [{"key": "output_background", "value": "WHITE"}]

    def responder(sql, params):
        return [{"t": "system_settings"}] if sql.upper().startswith("SHOW") else rows

    monkeypatch.setattr(pymysql, "connect", lambda **k: fake_connection(responder))
    config.load_db_config()
    assert config.OUTPUT_BACKGROUND == "white"
    assert processing_profile.current().background == "white"
    rows[:] = [{"key": "output_background", "value": "purple"}]
    config.load_db_config()
    assert config.OUTPUT_BACKGROUND == "white"                                  # unknown value ignored
    assert processing_profile.ProcessingProfile(800, False, "photoroom").as_dict()["background"] == "white"


@pytest.mark.skipif(PHP is None, reason="php is not installed")
def test_the_processing_tab_offers_both_backgrounds(monkeypatch):
    monkeypatch.delenv("OUTPUT_BACKGROUND", raising=False)
    out = _php("$out[] = SettingsController::processingData([]);"
               "$out[] = SettingsController::processingData(['output_background' => ['value' => 'white']]);"
               "$out[] = SettingsController::processingData(['output_background' => ['value' => 'grey']]);")
    assert [o["background"] for o in out] == ["transparent", "white", "transparent"]
    assert out[0]["backgrounds"] == {"transparent": "خلفية شفافة (للتطبيق: وضع غامق وفاتح)", "white": "خلفية بيضا"}
    import catalog_match.settings as settings
    php = re.search(r"OUTPUT_BACKGROUNDS = \[(.*?)\];", SETTINGS_PHP.read_text(encoding="utf-8")).group(1)
    assert tuple(re.findall(r"'([a-z]+)' =>", php)) == settings.OUTPUT_BACKGROUNDS == config.OUTPUT_BACKGROUNDS


def test_the_settings_form_validates_the_background_on_the_server(app_env):
    db, env = app_env["db"], app_env["env"]
    saved = _settings(db).get("output_background")
    try:
        _put(db, {"output_background": "transparent"})
        out = _kernel(env, [
            ["POST", "/settings", {"section": "processing", "output_canvas_size": "800", "bg_removal_method": "photoroom",
                                   "output_background": "white"}],
            ["POST", "/settings", {"section": "processing", "output_canvas_size": "800", "bg_removal_method": "photoroom",
                                   "output_background": "<script>"}],
            ["POST", "/settings", {"section": "processing", "output_canvas_size": "800",
                                   "bg_removal_method": "photoroom"}],                       # an older form: kept
            ["GET", "/settings?tab=processing", {}],
        ])
        assert not out[0]["flash"]["warnings"] and _settings(db)["output_background"] == "white"
        assert out[1]["flash"]["warnings"] == ["خلفية الصورة هاي مش مدعومة؛ ما تغيّرت المحفوظة."]
        assert not out[2]["flash"]["warnings"]
        assert _settings(db)["output_background"] == "white"
        page = out[3]["body"]
        assert 'name="output_background"' in page and "خلفية شفافة (للتطبيق: وضع غامق وفاتح)" in page
        assert re.search(r'<option value="white"\s+selected', page)
    finally:
        if saved is None:
            _sql(db, "DELETE FROM system_settings WHERE `key` = %s", ("output_background",))
        else:
            _put(db, {"output_background": saved})


# ---------------------------------------------------------------------------
# Review: «غامق / فاتح / مربعات» behind the published image
# ---------------------------------------------------------------------------

def test_the_review_page_loads_the_toggle_script():
    page = (ROOT / "dashboard" / "resources" / "views" / "dashboard" / "catalog.blade.php").read_text(encoding="utf-8")
    assert "$rvVersion('js/review/theme_preview.js')" in page
    assert page.index("theme_preview.js") < page.index("['core', 'ui', 'jobs', 'single', 'bulk', 'app']")


def test_the_approved_view_has_the_theme_toggle(tmp_path):
    from laqta_review_harness import NODE
    if NODE is None:
        pytest.skip("node is not installed")
    from test_laqta_review_guards import fixture, page, picked

    out = page(r"""
const store = {};
let blocked = false;
globalThis.localStorage = {
    getItem(k) { if (blocked) throw new Error('blocked'); return Object.prototype.hasOwnProperty.call(store, k) ? store[k] : null; },
    setItem(k, v) { if (blocked) throw new Error('blocked'); store[k] = String(v); }
};
openRow(30);
press('Enter');
await flush();
answer(requests('/api/select_image')[0], { status: 'success', image_link: 'https://res.cloudinary.com/demo/image/upload/q_auto,f_auto/v1/p/b',
                                           sheet_value: 'https://res.cloudinary.com/demo/image/upload/q_auto,f_auto/v1/p/b',
                                           isolated: true, sheet: 'written',
                                           white_url: 'https://res.cloudinary.com/demo/image/upload/b_white,q_auto,f_jpg/v1/p/b' });
await flush();
const stage = () => document.querySelector('.rv-final__stage');
const buttons = () => document.querySelectorAll('.rv-theme__btn');
out.labels = buttons().map(b => b.textContent);
out.first = [stage().getAttribute('class'), buttons().map(b => b.getAttribute('aria-pressed'))];
const sent = calls.length;
buttons()[2].click();
out.checker = [stage().getAttribute('class'), store[R.themePreview.KEY]];
buttons()[1].click();
out.light = [stage().getAttribute('class'), store[R.themePreview.KEY]];
out.noRequests = calls.length === sent;
openRow(30);
out.reopened = stage().getAttribute('class');
blocked = true;
openRow(30);
out.blocked = stage().getAttribute('class');
buttons()[2].click();
out.blockedClick = stage().getAttribute('class');
""", tmp_path, fixture([picked(30, "Almarai Milk 1L")]), config={"row": 30})
    assert out["labels"] == ["غامق", "فاتح", "مربعات"]
    assert "rv-theme--dark" in out["first"][0] and out["first"][1] == ["true", "false", "false"]
    assert "rv-theme--checker" in out["checker"][0] and out["checker"][1] == "checker"
    assert "rv-theme--light" in out["light"][0] and "rv-theme--checker" not in out["light"][0]
    assert out["light"][1] == "light" and out["noRequests"] is True
    assert "rv-theme--light" in out["reopened"]                                 # remembered
    assert "rv-theme--dark" in out["blocked"] and "rv-theme--checker" in out["blockedClick"]   # storage blocked: still works
