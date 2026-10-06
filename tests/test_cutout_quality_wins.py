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
