"""image_quality_gatekeeper after WP-4: only dimensions and aspect ratio are hard gates (D5)."""

import importlib
import io
import sys

import cv2
import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFilter


def _packshot(size=1500, bg=(255, 255, 255), fill=0.5, seed=0):
    """A sharp labelled bottle on pure white, JPEG round-tripped (>40 % of pixels are >= 254)."""
    rng = np.random.default_rng(seed)
    img = Image.new("RGB", (size, size), bg)
    side = np.sqrt(fill)
    pw, ph = int(size * side * 0.7), int(size * side)
    x0, y0 = (size - pw) // 2, (size - ph) // 2
    xs = np.linspace(-1, 1, pw)
    shade = 0.55 + 0.45 * np.sqrt(np.clip(1 - xs ** 2, 0, 1))
    body = np.array([30, 120, 200], np.float32) * shade[None, :, None] * np.ones((ph, 1, 1), np.float32)
    body += rng.normal(0, 6, body.shape)
    prod = Image.fromarray(np.clip(body, 0, 255).astype(np.uint8))
    d = ImageDraw.Draw(prod)
    d.rectangle([int(pw * .1), int(ph * .3), int(pw * .9), int(ph * .6)], fill=(250, 250, 240))
    for i in range(12):
        d.text((int(pw * .15), int(ph * .32) + i * int(ph * .022)), "ALMARAI FULL CREAM MILK 1L", fill=(200, 20, 20))
    img.paste(prod, (x0, y0))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    buf.seek(0)
    return Image.open(buf).convert("RGB")


@pytest.fixture
def gatekeeper_module(monkeypatch):
    """A fresh import of the module while verification_layer cannot be imported at all."""
    for name in list(sys.modules):
        if name == "verification_layer" or name.startswith("verification_layer."):
            monkeypatch.delitem(sys.modules, name)
    monkeypatch.setitem(sys.modules, "verification_layer", None)
    monkeypatch.delitem(sys.modules, "image_quality_gatekeeper", raising=False)
    module = importlib.import_module("image_quality_gatekeeper")
    yield module
    sys.modules.pop("image_quality_gatekeeper", None)


def test_import_and_evaluate_without_verification_layer(gatekeeper_module):
    gk = gatekeeper_module.ImageQualityGatekeeper()
    report = gk.evaluate_image(_packshot(800), product_name="Full Cream Milk 1L", brand="Almarai")
    assert report["passes_gates"] is True


def test_white_background_packshot_passes(gatekeeper_module):
    img = _packshot(1500)
    white = float((np.asarray(img.convert("L")) >= 254).mean())
    assert white > 0.40                       # the case v1 called 'overexposed'
    gk = gatekeeper_module.ImageQualityGatekeeper(min_width=500, min_height=500)
    report = gk.evaluate_image(img)
    assert report["passes_gates"] is True, report["gate_reasons"]
    assert report["gate_reasons"] == []
    assert not any("overexposed" in r.lower() for r in report["gate_reasons"])
    # The measurement is still reported, only softly.
    assert report["is_overexposed"] is True and "overexposed" in report["soft_flags"]
    assert report["unified_score"] > 0.0


def test_evaluate_never_calls_grabcut(gatekeeper_module, monkeypatch):
    calls = []

    def forbidden(*args, **kwargs):
        calls.append(args)
        raise AssertionError("GrabCut must not run in evaluate_image")

    monkeypatch.setattr(cv2, "grabCut", forbidden)
    gk = gatekeeper_module.ImageQualityGatekeeper()
    report = gk.evaluate_image(_packshot(1000))
    assert calls == []
    assert report["passes_gates"] is True
    assert 0.0 < report["fill_ratio"] < 1.0          # perimeter / threshold fallback still measures fill
    assert report["bg_score"] > 0.99


def test_blur_and_contrast_are_soft(gatekeeper_module):
    gk = gatekeeper_module.ImageQualityGatekeeper()
    blurred = _packshot(1500).filter(ImageFilter.GaussianBlur(8))
    report = gk.evaluate_image(blurred)
    assert report["passes_gates"] is True, report["gate_reasons"]
    assert report["is_blurry"] is True
    assert any(f.startswith("blurry") for f in report["soft_flags"])

    flat = Image.new("RGB", (800, 800), (128, 128, 128))
    ImageDraw.Draw(flat).rectangle([300, 200, 500, 600], fill=(135, 135, 135))
    report = gk.evaluate_image(flat)
    assert report["passes_gates"] is True
    assert report["is_low_contrast"] is True and "low_contrast" in report["soft_flags"]


def test_dimension_and_aspect_gates_remain(gatekeeper_module):
    gk = gatekeeper_module.ImageQualityGatekeeper(min_width=500, min_height=500)
    small = gk.evaluate_image(_packshot(300))
    assert small["passes_gates"] is False
    assert any(r.startswith("Dimensions below minimum") for r in small["gate_reasons"])
    assert small["unified_score"] == 0.0

    strip = Image.new("RGB", (600, 2000), (255, 255, 255))
    ImageDraw.Draw(strip).rectangle([200, 300, 400, 1700], fill=(20, 90, 160))
    report = gk.evaluate_image(strip)
    assert report["passes_gates"] is False
    assert report["gate_reasons"] == ["Aspect ratio drift: 0.30 (Allowed: 0.4 - 2.5)"]


def test_rgba_input_is_composited_on_white(gatekeeper_module):
    img = Image.new("RGBA", (800, 800), (0, 0, 0, 0))
    ImageDraw.Draw(img).rectangle([250, 100, 550, 700], fill=(20, 120, 200, 255))
    report = gatekeeper_module.ImageQualityGatekeeper().evaluate_image(img)
    assert report["passes_gates"] is True
    assert report["bg_score"] > 0.99          # on black the perimeter would score 0


def test_boundary_segmenter_class_still_available_for_cli_bridge(gatekeeper_module):
    assert hasattr(gatekeeper_module, "BoundaryComplianceSegmenter")
