"""Free cut-out quality wins: PhotoRoom's uncertainty score, remove.bg type=product, rembg decontamination, sRGB
colour management, the dark-rim check on white and no hole-fill for clear products.

All offline: the providers are fakes (test_publish_canvas / test_transparent_canvas helpers), no key, no network.
"""

import io

import numpy as np
import pytest
from PIL import Image

import config
import cutout_finish as cf
import image_processor
import main
import publish_check  # at import time: tests/test_export_run.py later puts scripts/ (scripts/publish_check.py) first
from test_publish_canvas import FakeResponse, bottle_source, install_photoroom, png_bytes
from test_transparent_canvas import clean_segmenter, offline  # noqa: F401 - autouse fixture


# ---------------------------------------------------------------------------
# a. PhotoRoom's x-uncertainty-score
# ---------------------------------------------------------------------------

def scored(score):
    handler = clean_segmenter()

    def respond(img):
        response = handler(img)
        response.headers = {"x-uncertainty-score": score} if score is not None else {}
        return response

    return respond


@pytest.mark.parametrize("headers, expected", [
    ({"x-uncertainty-score": "0.25"}, 0.25),
    ({"X-Uncertainty-Score": "1"}, 1.0),
    ({"x-uncertainty-score": "-1"}, None),           # PhotoRoom: no estimate
    ({"x-uncertainty-score": "n/a"}, None),
    ({"x-uncertainty-score": "3"}, None),
    ({}, None),
])
def test_the_header_is_read_as_a_score_between_0_and_1(headers, expected):
    response = FakeResponse(200, b"", headers)
    assert image_processor._uncertainty_score(response) == expected
    assert image_processor._uncertainty_score(object()) is None              # a fake without headers


def _run(monkeypatch, tmp_path, score):
    src = bottle_source(tmp_path / "src.png", 600, 1200)
    calls = install_photoroom(monkeypatch, scored(score))
    result = image_processor.process_product_image_result(src, "Almarai Milk 1L", "Almarai", bg_method="photoroom")
    image_processor.cleanup_processed_image(result.path)
    return result, calls


def test_an_unsure_cutout_goes_to_review_without_a_paid_retry(monkeypatch, tmp_path):
    result, calls = _run(monkeypatch, tmp_path, "0.8")
    assert len(calls) == 1                                                      # no second PhotoRoom call
    assert result.isolated is False and result.quality_flags == ["photoroom_unsure"]
    assert result.uncertainty_score == 0.8 and result.finish["uncertainty"] == 0.8


def test_a_confident_cutout_publishes_and_keeps_its_score(monkeypatch, tmp_path):
    result, calls = _run(monkeypatch, tmp_path, "0.2")
    assert len(calls) == 1 and result.isolated is True and result.quality_flags == []
    assert result.uncertainty_score == 0.2 and result.finish["uncertainty"] == 0.2


def test_no_score_changes_nothing(monkeypatch, tmp_path):
    result, _calls = _run(monkeypatch, tmp_path, None)
    assert result.isolated is True and result.uncertainty_score is None and "uncertainty" not in result.finish


def test_the_threshold_is_a_setting(monkeypatch, tmp_path):
    from catalog_match import settings

    monkeypatch.setattr(config, "PHOTOROOM_UNCERTAINTY_MAX", "0.9", raising=False)
    assert settings.photoroom_uncertainty_max() == 0.9
    result, _calls = _run(monkeypatch, tmp_path, "0.8")
    assert result.isolated is True and result.quality_flags == []
    monkeypatch.setattr(config, "PHOTOROOM_UNCERTAINTY_MAX", "junk", raising=False)
    assert settings.photoroom_uncertainty_max() == 0.5


def test_the_flag_is_a_presentation_flag_the_reviewer_can_publish_anyway():
    assert "photoroom_unsure" in main.PRESENTATION_FLAGS
    assert publish_check.QUALITY_FLAG_TEXT["photoroom_unsure"] == "PhotoRoom مش متأكد من حدود المنتج"


def test_the_worker_keeps_the_publish_details_for_the_export(monkeypatch):
    res = {"status": "needs_review", "error": "background_not_removed", "provider": "photoroom", "isolated": False,
           "quality_flags": ["photoroom_unsure"], "quality_notes": [], "width": 1228, "height": 1228,
           "finish": {"background": "transparent", "uncertainty": 0.8, "defringed": 12}}
    monkeypatch.setattr(main, "publish_image", lambda *a, **k: dict(res))
    monkeypatch.setattr(main, "page_barcode", lambda best, barcode: (None, None))
    report = {}
    task = {"id": 1, "row_number": 2, "product_name": "Milk", "brand": "Almarai", "payload_json": "{}"}
    assert main.auto_approve_product(task, {"url": "https://x/m.jpg"}, object(), 5, sku_key="k", report=report) \
        == "needs_review"
    assert report["uncertainty"] == 0.8 and report["quality_flags"] == ["photoroom_unsure"]
    assert report["canvas"] == [1228, 1228] and report["finish"] == {"background": "transparent"}


# ---------------------------------------------------------------------------
# b. remove.bg: type=product
# ---------------------------------------------------------------------------

def test_remove_bg_is_asked_for_a_product_cutout(monkeypatch):
    sent = []

    def post(url, headers=None, files=None, data=None, timeout=None, **_kw):
        sent.append((url, dict(data or {})))
        return FakeResponse(200, png_bytes(Image.new("RGBA", (40, 40), (200, 30, 30, 255))))

    monkeypatch.setattr(config, "REMOVE_BG_API_KEY", "-".join(("tst", "rbg", "7f3c9a1e")))
    monkeypatch.setattr(image_processor.requests, "post", post)
    cutout, error = image_processor._isolate_remove_bg(Image.new("RGB", (40, 40), "white"))
    assert cutout is not None and error is None
    assert sent == [(image_processor.REMOVE_BG_URL, {"size": "auto", "format": "png", "type": "product"})]


# ---------------------------------------------------------------------------
# c. local rembg: decontaminate=True only when the installed remove() names it; the model is always ours
# ---------------------------------------------------------------------------

def _fake_rembg(monkeypatch, remove):
    import sys
    import types

    module = types.ModuleType("rembg")
    module.remove = remove
    module.new_session = lambda model: ("session", model)
    monkeypatch.setitem(sys.modules, "rembg", module)
    image_processor.reset_rembg_sessions(everything=True)


def _rgba_png(img):
    out = Image.new("RGBA", img.size, (0, 0, 0, 0))
    out.paste((200, 30, 30, 255), (5, 5, 30, 30))
    return png_bytes(out)


def test_a_rembg_that_knows_decontaminate_gets_it(monkeypatch):
    seen = []

    def remove(data, alpha_matting=False, session=None, decontaminate=False, **kwargs):
        seen.append({"session": session, "decontaminate": decontaminate, "kwargs": kwargs})
        return _rgba_png(Image.open(io.BytesIO(data)))

    _fake_rembg(monkeypatch, remove)
    cutout, error = image_processor._isolate_rembg(Image.new("RGB", (40, 40), "white"), "birefnet-general")
    assert cutout is not None and error is None
    assert seen == [{"session": ("session", "birefnet-general"), "decontaminate": True, "kwargs": {}}]
    image_processor.reset_rembg_sessions(everything=True)


def test_an_older_rembg_is_called_as_before(monkeypatch):
    seen = []

    def remove(data, alpha_matting=False, session=None, *args, **kwargs):      # before 2.0.79: no decontaminate
        seen.append(dict(kwargs))
        return _rgba_png(Image.open(io.BytesIO(data)))

    _fake_rembg(monkeypatch, remove)
    cutout, _error = image_processor._isolate_rembg(Image.new("RGB", (40, 40), "white"), "birefnet-general")
    assert cutout is not None and seen == [{}]
    image_processor.reset_rembg_sessions(everything=True)


def test_rembg_never_runs_its_own_default_model(monkeypatch):
    """rembg's default bria-rmbg weights are CC BY-NC: the session is always one of REMBG_MODELS."""
    from catalog_match import settings

    models = []
    _fake_rembg(monkeypatch, lambda data, session=None, **k: models.append(session) or _rgba_png(
        Image.open(io.BytesIO(data))))
    monkeypatch.setattr(config, "REMBG_MODEL", "bria-rmbg", raising=False)
    assert settings.rembg_model() == "birefnet-general"
    image_processor._isolate(Image.new("RGB", (40, 40), "white"), "rembg")
    assert models == [("session", "birefnet-general")] and "bria-rmbg" not in settings.REMBG_MODELS
    image_processor.reset_rembg_sessions(everything=True)


def test_the_ubuntu_install_pins_a_rembg_with_decontaminate():
    from pathlib import Path

    text = (Path(__file__).resolve().parents[1] / "deploy" / "ubuntu" / "install.sh").read_text(encoding="utf-8")
    assert 'REMBG_VERSION="2.0.85"' in text and '"rembg[$extra]==$REMBG_VERSION"' in text


# ---------------------------------------------------------------------------
# d. colour management: an embedded profile (CMYK, Display P3, Adobe RGB) is converted to sRGB before isolation
# ---------------------------------------------------------------------------

def _s15(v):
    import struct
    return struct.pack(">i", int(round(v * 65536)))


def icc_rgb_profile(name, red, green, blue, gamma=2.2):
    """A minimal ICC v2 matrix/TRC display profile (primaries already adapted to D50), built in the test."""
    import struct

    def xyz(v):
        return b"XYZ " + b"\0" * 4 + b"".join(_s15(c) for c in v)

    text = name.encode("ascii") + b"\0"
    desc = (b"desc" + b"\0" * 4 + struct.pack(">I", len(text)) + text + struct.pack(">II", 0, 0)
            + struct.pack(">HB", 0, 0) + b"\0" * 67)
    curve = b"curv" + b"\0" * 4 + struct.pack(">IH", 1, int(round(gamma * 256))) + b"\0\0"
    tags = [(b"desc", desc), (b"wtpt", xyz((0.9642, 1.0, 0.8249))), (b"rXYZ", xyz(red)), (b"gXYZ", xyz(green)),
            (b"bXYZ", xyz(blue)), (b"rTRC", curve), (b"gTRC", curve), (b"bTRC", curve)]
    offset = 128 + 4 + 12 * len(tags)
    table, blob = b"", b""
    for sig, data in tags:
        data += b"\0" * (-len(data) % 4)
        table += sig + struct.pack(">II", offset + len(blob), len(data))
        blob += data
    size = offset + len(blob)
    header = (struct.pack(">I", size) + b"\0" * 4 + struct.pack(">I", 0x02100000) + b"mntrRGB XYZ " + b"\0" * 12
              + b"acsp" + b"\0" * 24 + struct.pack(">I", 0) + _s15(0.9642) + _s15(1.0) + _s15(0.8249))
    header += b"\0" * (128 - len(header))
    return header + struct.pack(">I", len(tags)) + table + blob


# Display P3 primaries, Bradford-adapted to D50 (as Apple's Display P3 profile)
P3 = icc_rgb_profile("Display P3 (test)", (0.5151, 0.2412, -0.0011), (0.2920, 0.6922, 0.0419),
                     (0.1571, 0.0666, 0.7841))


def test_a_display_p3_photo_is_converted_to_srgb():
    from PIL import ImageCms

    src = Image.new("RGB", (64, 64), (200, 120, 40))
    buf = io.BytesIO()
    src.save(buf, format="PNG", icc_profile=P3)
    expected = ImageCms.profileToProfile(src, ImageCms.ImageCmsProfile(io.BytesIO(P3)), ImageCms.createProfile("sRGB"),
                                         outputMode="RGB").getpixel((5, 5))
    img, error = image_processor._decode_image(buf.getvalue())
    assert error is None and img.mode == "RGB" and "icc_profile" not in img.info
    assert img.getpixel((5, 5)) == expected != (200, 120, 40)
    assert expected[0] > 200                       # a saturated P3 orange is redder than the same numbers in sRGB


def test_alpha_survives_the_conversion():
    src = Image.new("RGBA", (32, 32), (200, 120, 40, 0))
    src.paste((200, 120, 40, 255), (8, 8, 24, 24))
    buf = io.BytesIO()
    src.save(buf, format="PNG", icc_profile=P3)
    img, _error = image_processor._decode_image(buf.getvalue())
    assert img.mode == "RGBA" and img.getpixel((0, 0))[3] == 0 and img.getpixel((16, 16))[3] == 255
    assert img.getpixel((16, 16))[:3] != (200, 120, 40)


def test_an_srgb_or_unprofiled_photo_is_left_alone():
    from PIL import ImageCms

    srgb = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    for icc in (srgb, None):
        buf = io.BytesIO()
        Image.new("RGB", (16, 16), (200, 120, 40)).save(buf, format="PNG", **({"icc_profile": icc} if icc else {}))
        img, _error = image_processor._decode_image(buf.getvalue())
        assert img.getpixel((3, 3)) == (200, 120, 40) and "icc_profile" not in img.info


def test_a_cmyk_photo_is_converted_with_its_profile(monkeypatch):
    """A CMYK JPEG with an embedded CMYK profile goes through ImageCms to sRGB (LittleCMS does the colour maths)."""
    from PIL import ImageCms

    calls = []

    def spy(im, source, target, **kw):                     # the test has no CMYK profile: LittleCMS is not called
        calls.append((im.mode, source, kw.get("outputMode")))
        out = Image.new("RGB", im.size, (230, 25, 30))
        out.info["icc_profile"] = b"srgb-bytes"
        return out

    monkeypatch.setattr(ImageCms, "profileToProfile", spy)
    monkeypatch.setattr(ImageCms, "ImageCmsProfile", lambda f: "cmyk-profile")
    monkeypatch.setattr(ImageCms, "getProfileDescription", lambda p: "Coated FOGRA39 (test)")
    cmyk = Image.new("CMYK", (16, 16), (0, 255, 255, 0))                                   # pure red ink
    buf = io.BytesIO()
    cmyk.save(buf, format="JPEG", quality=95, icc_profile=b"not-a-real-profile")
    img, error = image_processor._decode_image(buf.getvalue())
    assert error is None and calls == [("CMYK", "cmyk-profile", "RGB")]
    assert img.mode == "RGB" and img.getpixel((3, 3)) == (230, 25, 30) and "icc_profile" not in img.info


def test_a_cmyk_photo_without_a_profile_converts_as_before():
    cmyk = Image.new("CMYK", (16, 16), (0, 255, 255, 0))
    buf = io.BytesIO()
    cmyk.save(buf, format="JPEG", quality=95)
    img, _error = image_processor._decode_image(buf.getvalue())
    r, g, b = img.getpixel((8, 8))
    assert img.mode == "RGB" and r > 200 and g < 60 and b < 60


def test_a_broken_profile_never_breaks_the_decode():
    buf = io.BytesIO()
    Image.new("RGB", (16, 16), (10, 20, 30)).save(buf, format="PNG", icc_profile=b"\0" * 200)
    img, error = image_processor._decode_image(buf.getvalue())
    assert error is None and img.getpixel((1, 1)) == (10, 20, 30)


def test_the_published_canvas_of_a_p3_photo_is_srgb(monkeypatch, tmp_path):
    src = Image.new("RGB", (600, 1200), (225, 225, 225))
    src.paste((40, 70, 200), (180, 60, 420, 1140))
    path = tmp_path / "p3.png"
    src.save(path, format="PNG", icc_profile=P3)
    sent = install_photoroom(monkeypatch, clean_segmenter())
    result = image_processor.process_product_image_result(str(path), "Milk", "Almarai", bg_method="photoroom")
    with Image.open(result.path) as out:
        out.load()
        assert "icc_profile" not in out.info                     # no profile: sRGB, as the app reads it
    assert "icc_profile" not in sent[0]["image"].info            # PhotoRoom got the sRGB pixels
    image_processor.cleanup_processed_image(result.path)


# ---------------------------------------------------------------------------
# e. the mirror of the halo check: dark rims on white (light mode), review flag only
# ---------------------------------------------------------------------------

from test_transparent_canvas import carton, clean_disc_rgba, disc  # noqa: E402


def dark_ringed():
    """A light product with a 2-px dark outline left from a dark backdrop."""
    _src, cut, r = disc(colour=(235, 235, 232), backdrop=30)
    arr = np.asarray(cut).copy()
    ring = (r >= 78.5) & (r < 80.5)
    arr[ring, :3] = 35
    arr[ring, 3] = 255
    return Image.fromarray(arr, "RGBA")


def test_the_dark_rim_score_sees_dark_rims_on_a_light_product_only():
    assert cf.dark_rim_score(cf.transparent_canvas(dark_ringed(), (400, 400))) > cf.DARK_RIM_MAX
    assert cf.dark_rim_score(cf.transparent_canvas(clean_disc_rgba(), (400, 400))) == 0.0     # dark all the way in
    light = np.asarray(dark_ringed()).copy()
    light[..., :3] = 235
    assert cf.dark_rim_score(cf.transparent_canvas(Image.fromarray(light, "RGBA"), (400, 400))) == 0.0
    assert cf.DARK_RIM_STEP > cf.HALO_STEP and cf.DARK_RIM_MAX > cf.HALO_MAX                  # the conservative side


def test_a_dark_rim_is_flagged_for_review_without_a_paid_call(monkeypatch):
    monkeypatch.setattr(image_processor, "_isolate", lambda *a, **k: pytest.fail("no paid re-isolation"))
    src = Image.new("RGB", (240, 240), (30, 30, 30))
    done = cf.finish(src, dark_ringed(), None, "photoroom", True, [], [], (400, 400))
    assert done.isolated is False and done.flags == [cf.FLAG_DARK_RIM] and done.info["dark_rim"] > cf.DARK_RIM_MAX


def test_dark_rim_is_a_presentation_flag_with_one_arabic_sentence():
    assert "dark_rim" in main.PRESENTATION_FLAGS
    assert publish_check.QUALITY_FLAG_TEXT["dark_rim"] == "حواف غامقة بتبين على الوضع الفاتح"


def test_the_clean_corpus_stays_far_from_the_dark_rim_threshold(tmp_path):
    import packshot_synth as ps

    scores = []
    for name, items in ps.good_corpus().items():
        for shot, _options in items:
            result, _services = ps.run_shot(image_processor, config, shot, tmp_path, remove_bg="truth",
                                            background="transparent")
            scores.append((result.finish.get("dark_rim", 0.0), name, shot.name, result.quality_flags))
            image_processor.cleanup_processed_image(result.path)
    assert all(flags == [] for _s, _n, _shot, flags in scores)
    assert max(scores)[0] <= cf.DARK_RIM_MAX / 2, max(scores)


# ---------------------------------------------------------------------------
# f. no hole-fill for glass and clear packaging
# ---------------------------------------------------------------------------

import categories  # noqa: E402


@pytest.mark.parametrize("texts, clear", [
    (("Beverages", "water"), True),
    (("Beverages > water > Glass",), True),
    (("Home & Living", "Food Storage", "Glass Jars"), True),
    (("Baby products", "Bottles"), True),
    (("مشروبات", "ماء"), True),
    (("أدوات المطبخ", "مرطبانات زجاجية"), True),
    (("Beverages", "Bottled Water"), True),
    (("Dairy", "Milk"), False),
    (("Beverages", "Coconut Water"), False),          # a carton: its eaten white panels still get filled
    (("Fashion", "Sunglasses"), False),
    (("", None), False),
])
def test_clear_packaging_comes_from_the_category(texts, clear):
    assert categories.is_clear_packaging(*texts) is clear


def test_a_clear_product_keeps_its_holes_open():
    src, cut = carton()
    filled_out, filled = cf.finish_cutout(src, cut, None, "rembg")
    clear_out, kept = cf.finish_cutout(src, cut, None, "rembg", fill_holes=False)
    assert filled["holes_filled"] == 1 and np.asarray(filled_out)[100, 100, 3] == 255
    assert (kept["holes_filled"], kept["holes_left"], kept["hole_fill"]) == (0, 1, "skipped_clear")
    assert np.asarray(clear_out)[100, 100, 3] == 0                            # no grey slab: still see-through
    done = cf.finish(src, cut, None, "rembg", True, [], [], (400, 400), clear=True)
    assert done.info["hole_fill"] == "skipped_clear" and done.info["holes_filled"] == 0


def test_publish_tells_the_processing_when_the_product_is_clear(monkeypatch, tmp_path):
    seen = []

    def process(*a, **k):
        seen.append(k.get("clear"))
        return image_processor.ProcessResult(None, False, "photoroom", "photoroom_402")

    monkeypatch.setattr(image_processor, "process_product_image_result", process)
    for hint, override in ((("Beverages", "water"), None), (("Dairy", "Milk"), None),
                           ((), {"category_l1_en": "Home & Living", "category_l3_en": "Glass Jars"})):
        main.publish_image("https://x/a.jpg", "P", "B", 2, None, 5, category_hint=hint, category_override=override)
    assert seen == [True, None, True]


# ---------------------------------------------------------------------------
# the worker keeps the auto-publish details in the queue row's trace (scripts/export_run.py reads 'publish')
# ---------------------------------------------------------------------------

from test_worker_wiring import _task, race  # noqa: E402,F401 - fixture


def test_the_auto_publish_details_reach_the_queue_trace(race, monkeypatch):
    import local_cache_db

    main_module, rec = race
    traces = []

    def auto(task, best, worksheet, link_column_index, sku_key=None, report=None):
        report.update({"status": "needs_review", "quality_flags": ["photoroom_unsure"], "uncertainty": 0.7})
        return "needs_review"

    monkeypatch.setattr(main_module, "auto_approve_product", auto)
    monkeypatch.setattr(local_cache_db, "update_task_status",
                        lambda task_id, status, *a, **k: traces.append((status, k.get("trace"))) or True)
    task = dict(_task(), worker_id="w1#claim")
    assert main_module.pre_cache_product_candidates(task, worksheet=object(), link_column_index=5,
                                                    sleep=lambda s: None) == "success"
    status, trace = traces[-1]
    assert status == "ready_for_review" and trace["publish"]["uncertainty"] == 0.7 and "outcome" in trace
