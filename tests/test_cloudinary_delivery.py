"""Cloudinary upload/delivery contract (IMG-1, IMG-15, IMG-M4, IMG-M6).

cloudinary.uploader.upload is replaced by a fake; sockets are blocked, so nothing leaves the machine.
"""

import io
import socket

import cloudinary
import cloudinary.exceptions
import cloudinary.uploader
import numpy as np
import pytest
from PIL import Image, ImageDraw

import cloudinary_storage
import config

FORBIDDEN_DELIVERY_PARTS = ("c_pad", "e_trim", "e_sharpen", "c_fit", "b_rgb", "c_scale")


def _refuse(*_args, **_kwargs):
    raise RuntimeError("network access is blocked in tests")


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)
    monkeypatch.setattr(socket, "getaddrinfo", _refuse)
    saved = dict(cloudinary.config().__dict__)
    cloudinary.config(cloud_name="demo", api_key="key", api_secret="secret", secure=True)
    if hasattr(config, "OUTPUT_CANVAS_SIZE"):
        monkeypatch.delattr(config, "OUTPUT_CANVAS_SIZE")
    monkeypatch.delenv("OUTPUT_CANVAS_SIZE", raising=False)
    yield
    cloudinary.config().__dict__.clear()
    cloudinary.config().__dict__.update(saved)


class FakeUploader:
    """Records every upload call; `failures` are raised first, in order."""

    def __init__(self, failures=()):
        self.failures = list(failures)
        self.calls = []

    def __call__(self, file, **options):
        payload = file.read() if hasattr(file, "read") else open(file, "rb").read()
        self.calls.append({"file": file, "payload": payload, "options": options})
        if self.failures:
            raise self.failures.pop(0)
        return {"public_id": f"{options['folder']}/{options['public_id']}", "version": 1700000000}


@pytest.fixture
def uploader(monkeypatch):
    def install(failures=()):
        fake = FakeUploader(failures)
        sleeps = []
        monkeypatch.setattr(cloudinary.uploader, "upload", fake)
        monkeypatch.setattr(cloudinary_storage, "_sleep", sleeps.append)
        return fake, sleeps

    return install


@pytest.fixture
def canvas_png(tmp_path):
    img = Image.new("RGB", (800, 800), (255, 255, 255))
    ImageDraw.Draw(img).rectangle([300, 48, 500, 752], fill=(40, 70, 200))
    path = tmp_path / "canvas.png"
    img.save(path, format="PNG")
    return str(path)


def test_upload_uses_timeout_slug_folder_and_plain_delivery(uploader, canvas_png):
    fake, sleeps = uploader()

    url = cloudinary_storage.upload_product_image_to_cloudinary(
        canvas_png, "Al Rawabi Orange Juice", "Al Rawabi",
        folder="products/beverages/100%_juice", tags=["Juice", " "],
        target_width=326, target_height=1282, padding_ratio=0.85, bg_color="ffffff")

    assert len(fake.calls) == 1 and sleeps == []
    options = fake.calls[0]["options"]
    assert options["timeout"] == 60
    assert options["folder"] == "products/beverages/100_juice"
    assert "%" not in options["folder"]
    assert "eager" not in options
    assert "background_removal" not in options
    assert options["tags"] == ["Juice"]

    assert url.startswith("https://res.cloudinary.com/demo/image/upload/")
    assert "q_auto" in url and "f_auto" in url
    for part in FORBIDDEN_DELIVERY_PARTS:
        assert part not in url, f"{part} must not be in the delivery URL: {url}"
    # the caller's (legacy) file-derived target size no longer shapes the output
    assert "326" not in url and "1282" not in url
    assert url.endswith("products/beverages/100_juice/" + options["public_id"])


@pytest.mark.parametrize("raw, expected", [
    ("100% Juice", "100_juice"),
    ("Men's Care", "mens_care"),
    ("Dairy & Eggs", "dairy_and_eggs"),
    ("Children’s Food", "childrens_food"),
    ("  Snacks / Chips?", "snacks_chips"),
])
def test_slugify_segment(raw, expected):
    assert cloudinary_storage.slugify_segment(raw) == expected


def test_product_folder_from_categories():
    folder = cloudinary_storage.product_folder("Beverages", "100% Juice")
    assert folder == "products/beverages/100_juice"
    assert "%" not in folder
    assert cloudinary_storage.product_folder("", "") == "products"
    assert cloudinary_storage.slugify_folder("products/حليب/Men's") == "products/mens"


def test_transient_failure_then_success_is_two_calls(uploader, canvas_png):
    fake, sleeps = uploader(failures=[cloudinary.exceptions.Error("Socket error: timed out")])

    url = cloudinary_storage.upload_product_image_to_cloudinary(canvas_png, "Milk", "Almarai")

    assert url and "q_auto" in url
    assert len(fake.calls) == 2
    assert len(sleeps) == 1
    assert all(c["options"]["timeout"] == 60 for c in fake.calls)


def test_5xx_is_retried_twice_then_gives_up(uploader, canvas_png):
    fake, sleeps = uploader(failures=[cloudinary.exceptions.GeneralError("502 Bad Gateway")] * 3)

    assert cloudinary_storage.upload_product_image_to_cloudinary(canvas_png, "Milk", "Almarai") is None
    assert len(fake.calls) == 3
    assert len(sleeps) == 2, "no sleep after the last attempt"


def test_client_errors_are_not_retried(uploader, canvas_png):
    fake, sleeps = uploader(failures=[cloudinary.exceptions.AuthorizationRequired("Invalid api_key")])

    assert cloudinary_storage.upload_product_image_to_cloudinary(canvas_png, "Milk", "Almarai") is None
    assert len(fake.calls) == 1 and sleeps == []


def test_final_canvas_bytes_are_uploaded_unchanged(uploader, canvas_png):
    fake, _ = uploader()
    cloudinary_storage.upload_product_image_to_cloudinary(canvas_png, "Milk", "Almarai")
    with open(canvas_png, "rb") as fh:
        assert fake.calls[0]["payload"] == fh.read()


def test_transparent_upload_is_flattened_to_white_canvas(uploader, tmp_path):
    # Legacy manual-upload path hands over a tight transparent cutout (product touches the edges,
    # enclosed hole with black RGB underneath). The delivered asset must still be an opaque white canvas.
    cut = np.zeros((700, 330, 4), np.uint8)
    cut[:, :] = (40, 70, 200, 255)
    cut[300:400, 120:210] = (0, 0, 0, 0)
    path = tmp_path / "cutout.png"
    Image.fromarray(cut, "RGBA").save(path)
    fake, _ = uploader()

    url = cloudinary_storage.upload_product_image_to_cloudinary(str(path), "Milk", "Almarai")

    assert url
    with Image.open(io.BytesIO(fake.calls[0]["payload"])) as uploaded:
        uploaded.load()
        assert uploaded.mode == "RGB"
        assert uploaded.size == (800, 800)
        assert uploaded.getpixel((0, 0)) == (255, 255, 255)
        arr = np.asarray(uploaded)
    assert arr[400, 400].min() >= 250  # the hole is white, not black


def test_missing_or_non_image_file_is_not_uploaded(uploader, tmp_path):
    fake, _ = uploader()
    assert cloudinary_storage.upload_product_image_to_cloudinary(str(tmp_path / "nope.png"), "M", "A") is None
    html = tmp_path / "page.png"
    html.write_bytes(b"<html>not an image</html>")
    assert cloudinary_storage.upload_product_image_to_cloudinary(str(html), "M", "A") is None
    assert fake.calls == []
