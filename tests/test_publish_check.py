"""«فحص النشر» (publish_check.py): the publish chain rehearsed step by step on a test image, offline.

The owner bulk-approved 11 products and every one failed with no visible reason; the connection check only asks
each service whether it answers. publish_check runs the real publish functions instead: the review candidate's
image from the candidate store or the real download (direct, then the proxy), background removal with the current
processing profile, an upload to Cloudinary at a fixed test location that is deleted right after, and a
value-preserving write of the link column's header cell in the products tab.

Every service is replaced here (sockets are blocked): the candidate query, the download client, the processing,
the Cloudinary uploader, the Google Sheets client and the outbox. The checks: each step's ok / warn / fail / skipped
mapping and its Arabic action, the step timeout, no secret anywhere in the result, the sheet step writing exactly
the value it read to the link column's header cell only, the Cloudinary step deleting after every upload that
reached Cloudinary, the bridge action and the terminal script.
"""

import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

import cloudinary
import cloudinary.exceptions
import cloudinary.uploader
import pytest
from PIL import Image

import cli_bridge
import cloudinary_storage
import config
import google_sheets
import image_processor
import local_cache_db
import processing_profile
import publish_check as pc
from http_client import FetchResult

ROOT = Path(__file__).resolve().parents[1]
CORE_JS = ROOT / "dashboard" / "public" / "js" / "review" / "core.js"
NODE = shutil.which("node")
PNG = (ROOT / "assets" / "selftest" / "publish_check_sample.png").read_bytes()
SHA = hashlib.sha256(PNG).hexdigest()
EMAIL = "laqta-bot@laqta-test.iam.gserviceaccount.com"


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeClient:
    """http_client.ImpersonateClient stand-in: one result per route (direct / proxy)."""

    calls = []
    results = {}

    def __init__(self, use_proxy=False, proxy_url=None, **_kwargs):
        self.route = "proxy" if use_proxy and proxy_url else "direct"

    def fetch_image(self, url, timeout=15, max_bytes=0, referer=None, headers=None):
        FakeClient.calls.append(self.route)
        return FakeClient.results[self.route]


SAME, EMPTY = object(), object()


class FakeSpreadsheet:
    def __init__(self, sheet):
        self.sheet = sheet

    def values_get(self, rng, params=None):
        self.sheet.calls.append(("values_get", rng, dict(params or {})))
        if self.sheet.read_error:
            raise self.sheet.read_error
        raw = self.sheet.raw_header
        if raw is SAME:                     # the link header as row_values showed it
            raw = next(h for h in self.sheet.headers if google_sheets.resolve_columns([h])["link"] == 0)
        return {"range": rng} if raw is EMPTY else {"range": rng, "values": [[raw]]}

    def values_batch_update(self, body):
        self.sheet.calls.append(("values_batch_update", json.loads(json.dumps(body))))
        if self.sheet.write_error:
            raise self.sheet.write_error
        return {"totalUpdatedCells": 1}


class FakeWorksheet:
    """A gspread worksheet with only the reads publishing uses; every write except the batch update fails."""

    def __init__(self, title="Products", headers=("Barcode", "Product Name", "Brand", "Drive Image Link")):
        self.title = title
        self.id = 7
        self.headers = list(headers)
        self.raw_header = SAME            # FORMULA render of the link header (EMPTY: the cell has no value)
        self.read_error = None
        self.write_error = None
        self.calls = []
        self.spreadsheet = FakeSpreadsheet(self)

    def row_values(self, row):
        self.calls.append(("row_values", row))
        return list(self.headers)

    def update_cell(self, *args, **kwargs):
        raise AssertionError(f"publish_check must not write cells: update_cell{args}")

    def update(self, *args, **kwargs):
        raise AssertionError("publish_check must not write ranges")

    def batch_update(self, *args, **kwargs):
        raise AssertionError("publish_check must not batch_update the worksheet")


class FakeApiError(Exception):
    def __init__(self, code, message):
        super().__init__({"code": code, "message": message})
        self.code = code


def _canvas(tmp_path):
    folder = tmp_path / "imgproc_test"
    folder.mkdir(exist_ok=True)
    path = folder / "canvas.png"
    Image.new("RGB", (800, 800), (255, 255, 255)).save(path)
    return str(path)


@pytest.fixture
def world(monkeypatch, tmp_path, offline):
    """Every service of the publish chain replaced; each test changes what it needs."""
    w = type("World", (), {})()
    w.candidate = {"row_number": 12, "sku_key": "uae-12", "product_name": "Almarai Milk 1L", "brand": "Almarai",
                   "image_url": "https://www.luluhypermarket.com/p/milk.jpg", "content_sha256": SHA,
                   "page_url": "https://www.luluhypermarket.com/p/milk"}
    w.store = None
    w.proxy = "http://proxy.example:8080"
    FakeClient.calls = []
    FakeClient.results = {"direct": FetchResult(content=PNG, status=200, content_type="image/png")}
    w.client = FakeClient
    w.profile = processing_profile.ProcessingProfile(canvas=800, enhance=False, bg_method="photoroom")
    w.processed = []
    w.process_result = lambda: image_processor.ProcessResult(_canvas(tmp_path), True, "photoroom", None, 800, 800)
    w.upload = cloudinary_storage.UploadResult("https://res.cloudinary.com/laqta/image/upload/v1/laqta_selftest/publish_check",
                                               public_id="laqta_selftest/publish_check", content_md5="0" * 32)
    w.uploaded = []
    w.destroy = None
    w.destroyed = []
    w.sheet = FakeWorksheet()
    w.open_error = None
    w.outbox = {"pending": 0, "conflict": 0, "dead": 0, "written": 5}
    w.outbox_recent = {"pending": 0, "conflict": 0, "dead": 0, "written": 2}

    creds = tmp_path / "credentials.json"
    creds.write_text(json.dumps({"client_email": EMAIL, "private_key": "PRIVATE"}), encoding="utf-8")
    monkeypatch.setattr(config, "CREDENTIALS_FILE", str(creds))
    monkeypatch.setattr(config, "SPREADSHEET_TAB_NAME", "Products", raising=False)
    monkeypatch.setattr(config, "CLOUDINARY_CLOUD_NAME", "laqta-test")
    monkeypatch.setattr(config, "CLOUDINARY_API_KEY", _fake("CLOUDKEY"))
    monkeypatch.setattr(config, "CLOUDINARY_API_SECRET", _fake("CLOUDSECRET"))

    def candidate():
        if isinstance(w.candidate, Exception):
            raise w.candidate
        return w.candidate

    def process(path, name, brand, **kwargs):
        with open(path, "rb") as fh:
            w.processed.append({"bytes": fh.read(), "name": name, "brand": brand, **kwargs})
        return w.process_result()

    def upload(path):
        w.uploaded.append(path)
        assert os.path.isfile(path)
        return w.upload

    def destroy(public_id):
        w.destroyed.append(public_id)
        return w.destroy

    def open_worksheet(client, name, worksheet_index=0):
        assert name == config.SPREADSHEET_NAME_OR_URL and worksheet_index == 0
        if w.open_error:
            raise w.open_error
        return w.sheet

    def outbox_summary(since_ts=None):
        return dict(w.outbox if since_ts is None else w.outbox_recent)

    import http_client
    monkeypatch.setattr(pc, "_run_active", lambda: False)
    monkeypatch.setattr(local_cache_db, "first_review_candidate", candidate)
    monkeypatch.setattr(image_processor, "_load_from_candidate_store", lambda sha: w.store if sha == SHA else None)
    monkeypatch.setattr(http_client, "ImpersonateClient", FakeClient)
    monkeypatch.setattr(image_processor.settings, "proxy_url", lambda: w.proxy)
    monkeypatch.setattr(processing_profile, "current", lambda: w.profile)
    monkeypatch.setattr(image_processor, "process_product_image_result", process)
    monkeypatch.setattr(cloudinary_storage, "upload_selftest_image", upload)
    monkeypatch.setattr(cloudinary_storage, "destroy_selftest_image", destroy)
    monkeypatch.setattr(google_sheets, "get_sheets_client", lambda: object())
    monkeypatch.setattr(google_sheets, "open_worksheet", open_worksheet)
    monkeypatch.setattr(google_sheets, "outbox_summary", outbox_summary)
    # publishing writes and queues: none of them may run
    for name in ("update_image_link", "update_product_metadata", "init_async_queue", "flush_outbox"):
        monkeypatch.setattr(google_sheets, name, lambda *a, **k: pytest.fail("the publish check wrote or queued"))
    monkeypatch.setattr(local_cache_db, "save_product_resolution", lambda *a, **k: pytest.fail("a product was saved"))
    google_sheets._header_cache.clear()
    w.result_path = tmp_path / "temp" / "publish_check_last.json"
    w.run = lambda **kw: pc.run_publish_check(result_path=str(w.result_path), **kw)
    return w



def _fake(tag):
    """A fake secret built at run time (as tests/test_export_run.py does): nothing secret-shaped in the source."""
    return f"W2E-FAKE-{tag}-7f3c9a1e"

def steps(result):
    return {s["key"]: s for s in result["steps"]}


# ---------------------------------------------------------------------------
# image_processor._download_bytes tells which route worked (its return value is unchanged)
# ---------------------------------------------------------------------------

def test_download_bytes_reports_the_route_without_changing_its_answer(world):
    info = {}
    assert image_processor._download_bytes("https://cdn.example/a.png", info=info) == (PNG, None)
    assert info == {"route": "direct", "direct_error": None, "proxy_tried": False, "proxy_error": None}

    FakeClient.results = {"direct": FetchResult(error="timeout"), "proxy": FetchResult(content=PNG, status=200)}
    info = {}
    assert image_processor._download_bytes("https://cdn.example/a.png", info=info) == (PNG, None)
    assert info == {"route": "proxy", "direct_error": "timeout", "proxy_tried": True, "proxy_error": None}

    FakeClient.results = {"direct": FetchResult(error="http_403"), "proxy": FetchResult(error="connection_error")}
    info = {}
    assert image_processor._download_bytes("https://cdn.example/a.png", info=info) == (None, "download_connection_error")
    assert info == {"route": None, "direct_error": "http_403", "proxy_tried": True, "proxy_error": "connection_error"}
    # the old two-argument call still works
    assert image_processor._download_bytes("https://cdn.example/a.png") == (None, "download_connection_error")


# ---------------------------------------------------------------------------
# The whole rehearsal
# ---------------------------------------------------------------------------

def test_every_step_passes_and_the_result_is_saved(world):
    result = world.run()
    by = steps(result)
    assert [s["key"] for s in result["steps"]] == ["download", "process", "upload", "sheet"]
    assert {k: s["status"] for k, s in by.items()} == dict.fromkeys(by, "ok")
    assert result["ok"] is True and result["overall"] == "ok" and result["failed_step"] is None
    assert result["summary_ar"].startswith("النشر شغّال")
    for step in result["steps"]:
        assert set(step) == {"key", "status", "ms", "title_ar", "detail_ar", "action_ar", "code"}
        assert isinstance(step["ms"], int) and step["ms"] >= 0 and step["title_ar"] == pc.STEP_TITLES[step["key"]]
    assert "نزلت مباشرة" in by["download"]["detail_ar"] and "Almarai Milk 1L (صف 12)" in by["download"]["detail_ar"]
    assert "مش موجودة بالمخزن" in by["download"]["detail_ar"]
    assert "PhotoRoom" in by["process"]["detail_ar"] and "800×800" in by["process"]["detail_ar"]
    assert "وانمسحت" in by["upload"]["detail_ar"]
    assert "«Products»" in by["sheet"]["detail_ar"] and result["sheet_tab"] == "Products"
    assert result["sample"] == {"kind": "review", "product_name": "Almarai Milk 1L", "row_number": 12,
                                "source": "luluhypermarket.com"}
    assert re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+00:00$", result["finished_at"])
    # the processing ran on the downloaded bytes with the current processing profile
    (call,) = world.processed
    assert call["bytes"] == PNG and call["name"] == "Almarai Milk 1L" and call["brand"] == "Almarai"
    assert (call["target_width"], call["target_height"], call["bg_method"], call["enhance"]) == (800, 800, "photoroom", False)
    # the canvas is gone after the upload; the saved result is the returned one (no image bytes, no paths)
    assert world.uploaded and not os.path.exists(world.uploaded[0])
    saved = json.loads(world.result_path.read_text(encoding="utf-8"))
    assert saved == json.loads(json.dumps(result)) and pc.read_last_result(str(world.result_path)) == saved
    assert "canvas" not in json.dumps(saved) and "bytes" not in saved


def test_a_store_hit_is_reported(world):
    world.store = PNG
    by = steps(world.run())
    assert by["download"]["status"] == "ok" and "الصورة موجودة بالمخزن" in by["download"]["detail_ar"]


def test_proxy_fallback_is_reported(world):
    FakeClient.results = {"direct": FetchResult(error="timeout"), "proxy": FetchResult(content=PNG, status=200)}
    by = steps(world.run())
    assert by["download"]["status"] == "ok"
    assert "التنزيل المباشر ما زبط (انتهت المهلة)، ونزلت عن طريق البروكسي" in by["download"]["detail_ar"]
    assert FakeClient.calls == ["direct", "proxy"]


def test_both_routes_failing_names_the_proxy_and_skips_what_needs_the_image(world):
    FakeClient.results = {"direct": FetchResult(error="timeout"), "proxy": FetchResult(error="connection_error")}
    result = world.run()
    by = steps(result)
    assert by["download"]["status"] == "fail" and by["download"]["code"] == "download_connection_error"
    assert by["download"]["action_ar"] == pc.PROXY_ACTION == "البروكسي ما بيرد: افحصه بصفحة الإعدادات أو شيله."
    for key in ("process", "upload"):
        assert by[key]["status"] == "skipped" and by[key]["ms"] == 0
    assert "«تنزيل الصورة»" in by["process"]["detail_ar"] and "«عزل الخلفية والمعالجة»" in by["upload"]["detail_ar"]
    assert by["sheet"]["status"] == "ok"                       # the sheet does not need the image
    assert result["overall"] == "fail" and result["failed_step"] == "download"
    assert result["summary_ar"] == "النشر واقف عند «تنزيل الصورة»: " + pc.PROXY_ACTION
    assert not world.processed and not world.uploaded and not world.destroyed


def test_a_refused_direct_download_without_a_proxy_says_the_shops_answer(world):
    world.proxy = ""
    FakeClient.results = {"direct": FetchResult(error="http_404")}
    by = steps(world.run())
    assert by["download"]["status"] == "fail" and by["download"]["code"] == "download_http_404"
    assert by["download"]["action_ar"].startswith("الصورة انشالت من موقع المتجر")
    assert "ما في بروكسي مضبوط" in by["download"]["detail_ar"]


def test_a_failed_download_with_the_image_in_the_store_is_a_warning(world):
    world.store = PNG
    FakeClient.results = {"direct": FetchResult(error="http_403"), "proxy": FetchResult(error="http_403")}
    by = steps(world.run())
    assert by["download"]["status"] == "warn" and "من المخزن" in by["download"]["detail_ar"]
    assert by["process"]["status"] == "ok" and world.processed[0]["bytes"] == PNG


def test_bytes_that_changed_since_the_check_fail_like_the_approval(world):
    FakeClient.results = {"direct": FetchResult(content=PNG + b"x", status=200)}
    by = steps(world.run())
    assert by["download"]["status"] == "fail" and by["download"]["code"] == "source_changed"
    assert by["download"]["action_ar"].startswith("الصورة على موقع المتجر تغيّرت")


@pytest.mark.parametrize("candidate, said", [(None, "ما في منتج بانتظار المراجعة"),
                                             (RuntimeError("db down"), "ما قدرنا نقرأ قاعدة البيانات")])
def test_without_a_review_candidate_the_bundled_sample_is_used(world, candidate, said):
    world.candidate = candidate
    result = world.run()
    by = steps(result)
    assert by["download"]["status"] == "warn" and by["download"]["code"] == "no_review_sample"
    assert said in by["download"]["detail_ar"] and "ما انفحص هالمرة" in by["download"]["detail_ar"]
    assert FakeClient.calls == []                              # nothing to download
    assert world.processed[0]["bytes"] == PNG and world.processed[0]["name"] == pc.SAMPLE_PRODUCT[0]
    assert [by[k]["status"] for k in ("process", "upload", "sheet")] == ["ok", "ok", "ok"]
    assert result["overall"] == "warn" and result["ok"] is False and result["summary_ar"].startswith("النشر لازم يمشي")
    assert result["sample"]["kind"] == "bundled"


def test_the_bundled_sample_is_a_small_opaque_product_picture():
    with Image.open(pc.SAMPLE_IMAGE) as img:
        assert img.format == "PNG" and img.mode == "RGB" and min(img.size) >= 400
    assert os.path.getsize(pc.SAMPLE_IMAGE) < 100_000


@pytest.mark.parametrize("code, action", [
    ("photoroom_402", "اشحن رصيد PhotoRoom"),
    ("photoroom_401", "مفتاح PhotoRoom مرفوض: حدّثه بالإعدادات."),
    ("photoroom_no_key", "ضيف مفتاح PhotoRoom بالإعدادات"),
    ("photoroom_timeout", "PhotoRoom ما بيرد"),
    ("removebg_403", "مفتاح remove.bg مرفوض"),
    ("rembg_not_installed", "اختار PhotoRoom أو remove.bg"),
    ("processing_failed", "التفاصيل بسجل الأتمتة"),
])
def test_a_processing_failure_says_what_to_do_and_skips_the_upload(world, code, action):
    world.process_result = lambda: image_processor.ProcessResult(None, False, "photoroom", code)
    result = world.run()
    by = steps(result)
    assert by["process"]["status"] == "fail" and by["process"]["code"] == code
    assert action in by["process"]["action_ar"]
    assert code not in by["process"]["detail_ar"] + by["process"]["action_ar"]      # the code is for the tooltip
    assert by["upload"]["status"] == "skipped" and not world.uploaded
    assert result["failed_step"] == "process" and result["summary_ar"].startswith("النشر واقف عند «عزل الخلفية والمعالجة»")


def test_quality_flags_are_a_warning_and_the_upload_still_runs(world, tmp_path):
    world.process_result = lambda: image_processor.ProcessResult(_canvas(tmp_path), False, "photoroom", None, 800, 800,
                                                                 quality_flags=["alpha_haze"])
    by = steps(world.run())
    assert by["process"]["status"] == "warn" and by["process"]["code"] == "quality_flags"
    assert "هالة أو ضباب حول حواف المنتج" in by["process"]["detail_ar"] and "alpha_haze" not in by["process"]["detail_ar"]
    assert by["upload"]["status"] == "ok" and world.uploaded


def test_no_background_removal_fails_because_approvals_refuse_it(world, tmp_path):
    world.profile = processing_profile.ProcessingProfile(canvas=800, enhance=False, bg_method="none")
    world.process_result = lambda: image_processor.ProcessResult(_canvas(tmp_path), False, "none", None, 800, 800)
    by = steps(world.run())
    assert by["process"]["status"] == "fail" and by["process"]["code"] == "background_not_removed"
    assert "بدون عزل" in by["process"]["detail_ar"] and "معالجة الصور" in by["process"]["action_ar"]


# ---------------------------------------------------------------------------
# Cloudinary: a fixed test location, deleted after every upload that reached Cloudinary
# ---------------------------------------------------------------------------

def test_the_upload_is_always_deleted_after_it_succeeds(world):
    by = steps(world.run())
    assert by["upload"]["status"] == "ok"
    assert world.destroyed == ["laqta_selftest/publish_check"]


@pytest.mark.parametrize("cause, code, action", [
    ("AuthorizationRequired", "upload_auth", "مفتاح Cloudinary مرفوض: حدّثه بالإعدادات."),
    ("NotAllowed", "upload_not_allowed", "صلاحية رفع"),
    (None, "upload_failed", "Cloudinary ما بيرد"),
])
def test_a_failed_upload_says_why(world, cause, code, action):
    world.upload = cloudinary_storage.UploadResult(None, content_md5="0" * 32, error="upload_failed", cause=cause)
    result = world.run()
    by = steps(result)
    assert by["upload"]["status"] == "fail" and by["upload"]["code"] == code and action in by["upload"]["action_ar"]
    assert world.destroyed == []                                # nothing reached Cloudinary
    assert result["summary_ar"].startswith("النشر واقف عند «الرفع على Cloudinary»")


def test_an_upload_that_was_stored_differently_is_still_deleted(world):
    world.upload = cloudinary_storage.UploadResult(None, public_id="laqta_selftest/publish_check",
                                                   error="upload_etag_mismatch")
    by = steps(world.run())
    assert by["upload"]["status"] == "fail" and by["upload"]["code"] == "upload_etag_mismatch"
    assert world.destroyed == ["laqta_selftest/publish_check"] and "وانمسحت" in by["upload"]["detail_ar"]


def test_a_failed_delete_is_a_warning(world):
    world.destroy = "destroy_NotAllowed"
    result = world.run()
    by = steps(result)
    assert by["upload"]["status"] == "warn" and by["upload"]["code"] == "destroy_NotAllowed"
    assert "laqta_selftest/publish_check" in by["upload"]["action_ar"] and "النشر نفسه شغّال" in by["upload"]["action_ar"]
    assert world.destroyed == ["laqta_selftest/publish_check"] and result["overall"] == "warn"


def test_missing_cloudinary_settings_fail_before_any_upload(world, monkeypatch):
    monkeypatch.setattr(config, "CLOUDINARY_API_SECRET", "")
    by = steps(world.run())
    assert by["upload"]["status"] == "fail" and by["upload"]["code"] == "cloudinary_not_configured"
    assert not world.uploaded


class _Uploader:
    def __init__(self, failure=None):
        self.failure = failure
        self.calls = []

    def __call__(self, file, **options):
        payload = file.read() if hasattr(file, "read") else open(file, "rb").read()
        self.calls.append(options)
        if self.failure:
            raise self.failure
        with Image.open(io.BytesIO(payload)) as img:
            width, height = img.size
        return {"public_id": f"{options['folder']}/{options['public_id']}", "version": 1, "bytes": len(payload),
                "width": width, "height": height, "etag": hashlib.md5(payload).hexdigest()}


@pytest.fixture
def cloudinary_offline(monkeypatch, offline):
    saved = dict(cloudinary.config().__dict__)
    cloudinary.config(cloud_name="demo", api_key="key", api_secret="secret", secure=True)
    monkeypatch.setattr(cloudinary_storage, "_sleep", lambda s: None)
    yield
    cloudinary.config().__dict__.clear()
    cloudinary.config().__dict__.update(saved)


def test_the_selftest_upload_uses_the_publish_uploader_at_a_fixed_replaced_location(monkeypatch, tmp_path,
                                                                                     cloudinary_offline):
    fake = _Uploader()
    monkeypatch.setattr(cloudinary.uploader, "upload", fake)
    result = cloudinary_storage.upload_selftest_image(_canvas(tmp_path))
    assert result.url and result.public_id == "laqta_selftest/publish_check" and result.error is None
    (options,) = fake.calls
    assert (options["public_id"], options["folder"], options["overwrite"], options["timeout"]) == \
        ("publish_check", "laqta_selftest", True, cloudinary_storage.SELFTEST_TIMEOUT_SECONDS)
    # publishing keeps its own name (md5 of the bytes), no overwrite and its timeout
    cloudinary_storage.upload_product_image(_canvas(tmp_path), "Milk", "Almarai", folder="products")
    assert fake.calls[1]["public_id"] != "publish_check" and fake.calls[1]["overwrite"] is False
    assert fake.calls[1]["timeout"] == cloudinary_storage.UPLOAD_TIMEOUT_SECONDS


def test_a_refused_upload_reports_its_cause(monkeypatch, tmp_path, cloudinary_offline):
    monkeypatch.setattr(cloudinary.uploader, "upload",
                        _Uploader(cloudinary.exceptions.AuthorizationRequired("Invalid Signature")))
    result = cloudinary_storage.upload_selftest_image(_canvas(tmp_path))
    assert (result.url, result.error, result.cause) == (None, "upload_failed", "AuthorizationRequired")


def test_the_delete_never_touches_anything_but_the_test_image(monkeypatch, cloudinary_offline):
    calls = []
    answers = iter([{"result": "ok"}, {"result": "ok"}, {"result": "not found"}])

    def destroy(public_id, **options):
        calls.append((public_id, options))
        return next(answers)

    monkeypatch.setattr(cloudinary.uploader, "destroy", destroy)
    product = "products/dairy/" + "a" * 32
    assert cloudinary_storage.destroy_selftest_image(product) == "destroy_refused"
    assert cloudinary_storage.destroy_selftest_image("laqta_selftest/../products/x") == "destroy_refused"
    assert calls == []
    assert cloudinary_storage.destroy_selftest_image("laqta_selftest/publish_check") is None
    assert cloudinary_storage.destroy_selftest_image("publish_check") is None          # dynamic folders
    assert cloudinary_storage.destroy_selftest_image("laqta_selftest/publish_check") == "destroy_not_found"
    assert [c[0] for c in calls] == ["laqta_selftest/publish_check", "publish_check", "laqta_selftest/publish_check"]
    assert all(c[1]["invalidate"] is True and c[1]["resource_type"] == "image" for c in calls)

    def boom(public_id, **options):
        raise cloudinary.exceptions.NotAllowed("no destroy permission")

    monkeypatch.setattr(cloudinary.uploader, "destroy", boom)
    assert cloudinary_storage.destroy_selftest_image("laqta_selftest/publish_check") == "destroy_NotAllowed"


# ---------------------------------------------------------------------------
# The sheet: the products tab as publishing opens it, a value-preserving write of the link header only
# ---------------------------------------------------------------------------

def test_the_sheet_step_writes_exactly_the_header_it_read_to_that_cell_only(world):
    by = steps(world.run())
    assert by["sheet"]["status"] == "ok"
    reads = [c for c in world.sheet.calls if c[0] == "values_get"]
    writes = [c for c in world.sheet.calls if c[0] == "values_batch_update"]
    assert reads == [("values_get", "'Products'!D1", {"valueRenderOption": "FORMULA"})]
    assert writes == [("values_batch_update", {"valueInputOption": "RAW",
                                               "data": [{"range": "'Products'!D1", "values": [["Drive Image Link"]]}]})]
    assert "حساب الخدمة بيقدر يكتب" in by["sheet"]["detail_ar"]
    assert "طابور الكتابة بالشيت: 0 كتابة بتستنى، و0 كتابة فشلت نهائياً" in by["sheet"]["detail_ar"]


def test_the_header_is_written_back_as_stored_in_a_tab_with_a_quote(world):
    world.sheet = FakeWorksheet(title="Lulu's products", headers=("اسم المنتج", "رابط الصورة", "Brand"))
    by = steps(world.run())
    assert by["sheet"]["status"] == "ok"
    (write,) = [c for c in world.sheet.calls if c[0] == "values_batch_update"]
    assert write[1]["data"] == [{"range": "'Lulu''s products'!B1", "values": [["رابط الصورة"]]}]


def test_a_refused_write_asks_to_share_the_sheet_as_editor(world):
    world.sheet.write_error = FakeApiError(403, "The caller does not have permission")
    result = world.run()
    by = steps(result)
    assert by["sheet"]["status"] == "fail" and by["sheet"]["code"] == "sheet_no_edit"
    assert by["sheet"]["action_ar"] == f"حساب الخدمة ({EMAIL}) ما عنده صلاحية تعديل على الشيت: شارك الشيت معه كمحرر."
    assert result["summary_ar"].startswith("النشر واقف عند «الكتابة بالشيت»: حساب الخدمة")


def test_a_protected_header_row_is_a_warning_not_a_failure(world):
    world.sheet.write_error = FakeApiError(400, "You are trying to edit a protected cell or object.")
    by = steps(world.run())
    assert by["sheet"]["status"] == "warn" and by["sheet"]["code"] == "sheet_header_protected"


@pytest.mark.parametrize("raw", ["=IMAGE(A1)", 42, EMPTY, "Something else", "   ", "drive image link"])
def test_an_unreadable_header_is_never_written(world, raw):
    world.sheet.raw_header = raw
    by = steps(world.run())
    assert by["sheet"]["status"] == "warn" and by["sheet"]["code"] == "sheet_header_unreadable"
    assert not [c for c in world.sheet.calls if c[0] == "values_batch_update"]


def test_the_raw_header_is_read_back_exactly(world):
    world.sheet.raw_header = "Drive Image Link"
    assert steps(world.run())["sheet"]["status"] == "ok"


def test_without_a_link_column_nothing_is_created_or_written(world):
    world.sheet = FakeWorksheet(headers=("Barcode", "Product Name", "Brand"))
    by = steps(world.run())
    assert by["sheet"]["status"] == "warn" and by["sheet"]["code"] == "sheet_no_link_column"
    assert [c[0] for c in world.sheet.calls] == ["row_values"]   # update_cell would have raised


def test_a_tab_without_product_names_is_not_the_products_tab(world):
    world.sheet = FakeWorksheet(title="Sheet2", headers=("Date", "Drive Image Link"))
    by = steps(world.run())
    assert by["sheet"]["status"] == "fail" and by["sheet"]["code"] == "sheet_no_name_column"
    assert "«Sheet2»" in by["sheet"]["detail_ar"]


def test_a_backup_looking_tab_is_called_out(world):
    world.sheet = FakeWorksheet(title="Products backup")
    by = steps(world.run())
    assert by["sheet"]["status"] == "warn" and by["sheet"]["code"] == "sheet_tab_backup"
    assert "نسخة احتياطية" in by["sheet"]["action_ar"]


def test_dead_writes_in_the_outbox_are_a_warning(world):
    world.outbox = {"pending": 3, "conflict": 0, "dead": 9, "written": 5}
    world.outbox_recent = {"pending": 1, "conflict": 0, "dead": 2, "written": 2}
    by = steps(world.run())
    assert by["sheet"]["status"] == "warn" and by["sheet"]["code"] == "sheet_outbox_dead"
    assert "3 كتابة بتستنى، و2 كتابة فشلت نهائياً بآخر 7 أيام" in by["sheet"]["detail_ar"]


def test_an_unreadable_outbox_fails_the_sheet_step_because_approvals_write_through_it(world, monkeypatch):
    def down(since_ts=None):
        raise ConnectionRefusedError("MariaDB is down")

    monkeypatch.setattr(google_sheets, "outbox_summary", down)
    result = world.run()
    by = steps(result)
    assert by["sheet"]["status"] == "fail" and by["sheet"]["code"] == "outbox_unavailable"
    assert by["sheet"]["action_ar"] == pc.DB_ACTION
    assert "حساب الخدمة بيقدر يكتب" in by["sheet"]["detail_ar"]               # the write itself was still tried
    assert "ما قدرنا نقرأ طابور الكتابة بالشيت من قاعدة البيانات" in by["sheet"]["detail_ar"]
    assert result["failed_step"] == "sheet"


# setup -> the step's code (a table, not string pairs: a secret scanner reads a pair as a user and a password)
SHEET_OPEN_FAILURES = {
    "not_shared": "sheet_not_shared",
    "tab_missing": "sheet_tab_missing",
    "transient": "sheet_unavailable",
    "sheets_api_none": "sheet_credentials_rejected",
    "key_file_absent": "credentials_missing",
}


@pytest.mark.parametrize("setup", list(SHEET_OPEN_FAILURES))
def test_sheet_failures_before_the_write(world, monkeypatch, setup):
    code = SHEET_OPEN_FAILURES[setup]
    if setup == "not_shared":
        world.sheet = None
    elif setup == "tab_missing":
        world.open_error = google_sheets.SheetConfigError("التبويب 'Products' غير موجود")
    elif setup == "transient":
        world.open_error = google_sheets.SheetTransientError("Google Sheets غير متاح مؤقتاً")
    elif setup == "sheets_api_none":
        monkeypatch.setattr(google_sheets, "get_sheets_client", lambda: None)
    else:
        monkeypatch.setattr(config, "CREDENTIALS_FILE", "/nonexistent/credentials.json")
    by = steps(world.run())
    assert by["sheet"]["status"] == "fail" and by["sheet"]["code"] == code
    assert by["sheet"]["action_ar"]


def test_the_tab_title_says_when_no_tab_is_set(world, monkeypatch):
    monkeypatch.setattr(config, "SPREADSHEET_TAB_NAME", "")
    by = steps(world.run())
    assert "أول تبويب، لأنو ما في تبويب محدد بالإعدادات" in by["sheet"]["detail_ar"]


# ---------------------------------------------------------------------------
# Timeouts, crashes, a running worker, secrets
# ---------------------------------------------------------------------------

def test_a_hung_step_times_out_and_what_needs_it_is_skipped(world, monkeypatch):
    release = threading.Event()

    def hang(url, page_url=None, info=None):
        release.wait(30)
        return PNG, None

    monkeypatch.setattr(image_processor, "_download_bytes", hang)
    started = time.monotonic()
    try:
        result = world.run(timeouts={"download": 0.3})
    finally:
        release.set()
    assert time.monotonic() - started < 10
    by = steps(result)
    assert by["download"]["status"] == "fail" and by["download"]["code"] == "step_timeout"
    assert by["download"]["action_ar"] == pc.TIMEOUT_ACTIONS["download"] and by["download"]["ms"] >= 300
    assert by["process"]["status"] == "skipped" and by["upload"]["status"] == "skipped"
    assert by["sheet"]["status"] == "ok"


def test_a_crashing_step_fails_alone(world, monkeypatch):
    def boom():
        raise ValueError("unexpected")

    monkeypatch.setattr(processing_profile, "current", boom)
    by = steps(world.run())
    assert by["process"]["status"] == "fail" and by["process"]["code"] == "step_crashed:ValueError"
    assert "unexpected" not in json.dumps(by, ensure_ascii=False)
    assert by["sheet"]["status"] == "ok"


def test_a_running_worker_is_noted_and_nothing_else_changes(world, monkeypatch):
    monkeypatch.setattr(pc, "_run_active", lambda: True)
    result = world.run()
    assert result["run_active"] is True and result["notes"] == [pc.RUN_ACTIVE_NOTE]
    assert result["overall"] == "ok"


def test_run_active_reads_the_worker_lock(tmp_path, monkeypatch):
    import main
    lock = tmp_path / "pipeline.lock"
    monkeypatch.setattr(main, "LOCK_FILE", str(lock))
    assert pc._run_active() is False
    lock.write_text("STARTING", encoding="utf-8")
    assert pc._run_active() is True
    lock.write_text("not a lock", encoding="utf-8")
    old = time.time() - 3600
    os.utime(lock, (old, old))
    assert pc._run_active() is False                   # a stale lock is no run


def test_no_secret_reaches_the_result_or_the_saved_file(world, monkeypatch, tmp_path):
    secrets = {"PHOTOROOM_API_KEY": _fake("PHOTOROOM"), "CLOUDINARY_API_SECRET": _fake("CLOUDSECRET"),
               "CLOUDINARY_API_KEY": _fake("CLOUDKEY"), "GEMINI_API_KEY": _fake("GEMINI"),
               "SERPER_API_KEY": _fake("SERPER")}
    for name, value in secrets.items():
        monkeypatch.setattr(config, name, value, raising=False)
    proxy_user, proxy_pass = "shopuser", _fake("PROXYPASS")
    monkeypatch.setattr(config, "PROXY_URL", f"http://{proxy_user}:{proxy_pass}@proxy.example:8080")
    # every service echoes a secret where its text reaches the result
    world.candidate = dict(world.candidate, product_name="Milk " + secrets["GEMINI_API_KEY"],
                           image_url="https://cdn.example/a.jpg?key=" + secrets["SERPER_API_KEY"])
    world.process_result = lambda: image_processor.ProcessResult(None, False, "photoroom",
                                                                 "photoroom_" + secrets["PHOTOROOM_API_KEY"])
    world.sheet = FakeWorksheet(title="Products " + secrets["CLOUDINARY_API_SECRET"])
    result = world.run()
    dumped = json.dumps(result, ensure_ascii=False) + world.result_path.read_text(encoding="utf-8")
    for value in list(secrets.values()) + [proxy_pass, proxy_user]:
        assert value not in dumped, value
    assert "[REDACTED]" in dumped
    assert steps(result)["process"]["code"] == "photoroom_[REDACTED]"


# ---------------------------------------------------------------------------
# Texts shared with the review screen
# ---------------------------------------------------------------------------

def _core_js_value(name):
    text = CORE_JS.read_text(encoding="utf-8")
    start = text.index(f"const {name} = ")
    return text[start:text.index("};", start) + 2]


def test_quality_flag_words_are_the_review_screens():
    block = _core_js_value("QUALITY_FLAG_LABELS")
    labels = dict(re.findall(r"(\w+): '([^']+)'", block))
    assert labels == pc.QUALITY_FLAG_TEXT


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_download_sentences_are_the_review_screens(tmp_path):
    from laqta_review_harness import run as harness_run
    codes = ["source_changed", "download_timeout", "download_connection_error", "download_http_503",
             "download_failed", "download_http_403", "download_http_429", "download_http_404", "download_http_410",
             "download_not_image", "download_too_large", "download_bad_scheme", "not_image", "image_too_large"]
    texts = harness_run(f"out.texts = {json.dumps(codes)}.map(c => R.plainError(c, ''));",
                        tmp_path, {"products": [], "queue": {"status": "success", "ready_for_review": 0, "rows": []}})
    assert dict(zip(codes, texts["texts"])) == {code: pc.download_error_text(code) for code in codes}


def test_visible_texts_carry_no_codes(world):
    """English codes stay in `code` (the tooltip); detail and action are plain Arabic."""
    FakeClient.results = {"direct": FetchResult(error="http_403"), "proxy": FetchResult(error="timeout")}
    world.sheet.write_error = FakeApiError(403, "The caller does not have permission")
    result = world.run()
    for step in result["steps"]:
        text = step["detail_ar"] + " " + step["action_ar"]
        assert not re.search(r"\b[a-z]+_[a-z0-9_]+\b", text), text
        assert not re.search(r"\b(timeout|connection_error|http_\d+)\b", text), text
    assert re.search(r"[؀-ۿ]", result["summary_ar"])


# ---------------------------------------------------------------------------
# The bridge action and the terminal script
# ---------------------------------------------------------------------------

def test_the_bridge_action_returns_the_rehearsal(monkeypatch):
    assert cli_bridge.ACTIONS["publish_check"] is cli_bridge.action_publish_check
    assert list(cli_bridge.ACTIONS)[-2:] == ["ops_health", "run_control"]
    doc = {"ok": True, "overall": "ok", "steps": [], "summary_ar": "النشر شغّال"}
    monkeypatch.setattr(pc, "run_publish_check", lambda: dict(doc))
    assert cli_bridge.action_publish_check({}) == dict(doc, status="success")

    def boom():
        raise RuntimeError("secret detail")

    monkeypatch.setattr(pc, "run_publish_check", boom)
    out = cli_bridge.action_publish_check({})
    assert out["status"] == "failed" and re.search(r"[؀-ۿ]", out["error"]) and "secret detail" not in out["error"]


def test_the_bridge_prints_one_json_document(monkeypatch, tmp_path):
    doc = {"ok": False, "overall": "fail", "steps": [{"key": "download", "status": "fail"}], "summary_ar": "واقف"}
    monkeypatch.setattr(pc, "run_publish_check", lambda: dict(doc))
    out = io.StringIO()
    monkeypatch.setattr(cli_bridge, "_JSON_STDOUT", out)
    monkeypatch.setattr(cli_bridge, "LOG_PATH", str(tmp_path / "search.log"))
    assert cli_bridge.main(["cli_bridge.py", "publish_check", "e30="]) == 0
    (line,) = out.getvalue().strip().splitlines()
    assert json.loads(line) == dict(doc, status="success")


def test_the_terminal_script(monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location("publish_check_script", ROOT / "scripts" / "publish_check.py")
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    doc = {"ok": False, "overall": "fail", "summary_ar": "النشر واقف عند «الرفع على Cloudinary»: x",
           "notes": [pc.RUN_ACTIVE_NOTE], "steps": [
               {"key": "download", "status": "ok", "ms": 1234, "title_ar": "تنزيل الصورة", "detail_ar": "نزلت",
                "action_ar": "", "code": ""},
               {"key": "upload", "status": "fail", "ms": 50, "title_ar": "الرفع على Cloudinary", "detail_ar": "ما انرفعت",
                "action_ar": "مفتاح Cloudinary مرفوض: حدّثه بالإعدادات.", "code": "upload_auth"},
               {"key": "sheet", "status": "skipped", "ms": 0, "title_ar": "الكتابة بالشيت", "detail_ar": "ما انفحصت",
                "action_ar": "", "code": ""}]}
    saves = []

    def fake_run(save=True):
        saves.append(save)
        return json.loads(json.dumps(doc))

    monkeypatch.setattr(pc, "run_publish_check", fake_run)
    cwd = os.getcwd()
    try:
        out = io.StringIO()
        assert script.main(["--json"], out) == 1
        (line,) = out.getvalue().strip().splitlines()
        assert json.loads(line) == doc
        out = io.StringIO()
        assert script.main([], out) == 1
    finally:
        os.chdir(cwd)
    text = out.getvalue()
    assert "✅ تنزيل الصورة (1.2 ث)" in text and "❌ الرفع على Cloudinary" in text and "⏭ الكتابة بالشيت (ما انفحصت)" in text
    assert "← مفتاح Cloudinary مرفوض" in text and pc.RUN_ACTIVE_NOTE in text
    doc["ok"] = True
    out = io.StringIO()
    try:
        assert script.main(["--json", "--no-save"], out) == 0
    finally:
        os.chdir(cwd)
    assert saves == [True, True, False]


def test_the_terminal_script_keeps_stdout_for_the_json(tmp_path):
    """Run as a process with every service unreachable: one JSON document, whatever the libraries print."""
    env = dict(os.environ, DB_PORT="1", PYTHONIOENCODING="cp1256", BG_REMOVAL_METHOD="none",
               CREDENTIALS_FILE=str(tmp_path / "none.json"), CLOUDINARY_CLOUD_NAME="", GEMINI_API_KEY="",
               PROXY_URL="", PHOTOROOM_API_KEY="")
    env.pop("PYTHONUTF8", None)
    saved = Path(pc.LAST_RESULT_PATH)
    before = saved.read_bytes() if saved.exists() else None
    proc = subprocess.run([os.sys.executable, str(ROOT / "scripts" / "publish_check.py"), "--json", "--no-save"],
                          cwd=str(tmp_path), env=env, capture_output=True, timeout=240)
    assert (saved.read_bytes() if saved.exists() else None) == before       # the Health page's last result stays
    lines = [l for l in proc.stdout.decode("utf-8").splitlines() if l.strip()]
    assert len(lines) == 1, proc.stdout
    doc = json.loads(lines[0])
    assert proc.returncode == 1 and doc["overall"] == "fail"
    assert [s["key"] for s in doc["steps"]] == list(pc.STEP_ORDER)


# ---------------------------------------------------------------------------
# The review candidate the rehearsal uses (MariaDB)
# ---------------------------------------------------------------------------

def test_first_review_candidate_is_a_preselected_image_of_a_product_waiting_for_review(mariadb_or_skip):
    db = mariadb_or_skip

    def sql(statement, params=()):
        conn = db.get_db_connection()
        try:
            cur = conn.cursor()
            cur.execute(statement, params)
            conn.commit()
        finally:
            conn.close()

    def clean():
        sql("DELETE FROM automation_queue")
        sql("DELETE FROM curation_candidates")

    clean()
    try:
        assert db.first_review_candidate() is None
        sql("INSERT INTO automation_queue (`row_number`, product_name, brand, status, sku_key) VALUES "
            "(5, 'Done', 'X', 'completed', 'k5'), (6, 'Wait', 'Y', 'ready_for_review', 'k6')")
        sql("INSERT INTO curation_candidates (`row_number`, product_name, brand, image_url, is_selected, status, "
            "sku_key, content_sha256, page_url) VALUES "
            "(5, 'Done', 'X', 'https://a/5.jpg', 1, 'preselected', 'k5', NULL, NULL), "
            "(6, 'Wait', 'Y', 'https://a/6-other.jpg', 0, 'eligible', 'k6', NULL, NULL), "
            "(6, 'Wait', 'Y', 'https://a/6.jpg', 1, 'preselected', 'k6', %s, 'https://a/p6')", (SHA,))
        assert db.first_review_candidate() == {"row_number": 6, "sku_key": "k6", "product_name": "Wait", "brand": "Y",
                                               "image_url": "https://a/6.jpg", "content_sha256": SHA,
                                               "page_url": "https://a/p6"}
        # a candidate kept under another product's key at the same row is not this product's
        sql("UPDATE curation_candidates SET sku_key = 'other' WHERE image_url = 'https://a/6.jpg'")
        assert db.first_review_candidate() is None
    finally:
        clean()
