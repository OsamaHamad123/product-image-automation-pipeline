"""The dashboard forwards the stale-approval guard to the bridge (contract C1).

select_image and upload_manual_image take expected_state (what the review page showed: the queue row's status, update
time and number, and the approved image) and replace (the reviewer's explicit confirmation). Both forward an explicit
allow-list of the fields the review page sends: phash, content_sha256, category_l*_en or any other field never reaches
the bridge (they skipped the pHash half of the rejected-image check, chose the published bytes from the candidate
store, and set the Cloudinary folder and sheet metadata). The upload's form fields are text, so the page sends
expected_state as JSON. replace is always a boolean for the bridge ("0" would be truthy in Python). Run through the PHP
CLI with small stand-ins for the framework (skipped without php).

reject_image is allow-listed the same way (ApiController::REJECT_FIELDS, the fields review/core.js rejectBody sends),
and the bridge drops any other key itself (cli_bridge.REJECT_FIELDS): a phash sent with a rejection never becomes the
rejected image's fingerprint, which comes from the stored bytes only.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CONTROLLERS = ROOT / "dashboard" / "app" / "Http" / "Controllers"
PHP = shutil.which("php")

NEEDS_PHP = pytest.mark.skipif(PHP is None, reason="php is not installed")

API_HARNESS = r"""<?php
namespace Illuminate\Http {
    class Request {
        public $data;
        public function __construct(array $data) { $this->data = $data; }
        public function hasFile($k) { return $k === 'file'; }
        public function file($k) { return new \FakeFile(); }
        public function only(array $keys) { return array_intersect_key($this->data, array_flip($keys)); }
        public function input($k, $d = null) { return $this->data[$k] ?? $d; }
        public function has($k) { return array_key_exists($k, $this->data); }
        public function boolean($k) { return filter_var($this->data[$k] ?? false, FILTER_VALIDATE_BOOLEAN); }
        public function all() { return $this->data; }
    }
}
namespace Illuminate\Support\Facades {
    class Validator {
        public static function make($data, $rules) { return new class { public function fails() { return false; } }; }
    }
}
namespace App\Services {
    class PythonBridge {
        public static $calls = [];
        public static function run($action, $params = []) { self::$calls[] = [$action, $params]; return ['status' => 'success']; }
        public static function pythonPath() { return 'python'; }
        public static function isError($r) { return false; }
        public static function httpStatus($r) { return 200; }
    }
    class QueueStats {}
}
namespace App\Http\Controllers {
    class ProductController { public static function forgetProductCaches() {} }
}
namespace {
    class FakeFile {
        public function getClientOriginalExtension() { return 'png'; }
        public function isValid() { return true; }
        public function getMimeType() { return 'image/png'; }
        public function move($dir, $name) { file_put_contents($dir . '/' . $name, 'x'); }
    }
    class FakeResponse { public $data; public $status; public function __construct($d, $s) { $this->data = $d; $this->status = $s; } }
    class FakeFactory { public function json($d, $s = 200) { return new FakeResponse($d, $s); } }
    function response() { return new FakeFactory(); }
    function base_path($p = '') { return getenv('HARNESS_ROOT') . '/dashboard' . ($p !== '' ? '/' . $p : ''); }
    require getenv('CONTROLLER_BASE');
    require getenv('API_CONTROLLER');
    $api = new App\Http\Controllers\ApiController();
    $api->uploadManualImage(new Illuminate\Http\Request(['row_number' => '9', 'product_name' => 'Milk', 'file' => 'x',
        'expected_state' => '{"queue_status":"ready_for_review","queue_updated_at":"2026-10-03 10:00:00","approved_url":null,"extra":1}',
        'replace' => '1']));
    $api->uploadManualImage(new Illuminate\Http\Request(['row_number' => '9', 'product_name' => 'Milk', 'file' => 'x',
        'expected_state' => 'null']));
    $api->selectImage(new Illuminate\Http\Request(['image_url' => 'u', 'replace' => true, 'row_number' => '9',
        'product_name' => 'Milk', 'sku_key' => 'k', 'candidate_sha256' => 'ab', 'candidate_warnings' => 'w',
        'search_decision' => 'REVIEW_PRESELECTED', 'phash' => '0f0f0f0f0f0f0f0f', 'content_sha256' => 'cd',
        'category_l1_en' => 'Evil', 'category_l2_en' => 'X', 'upscale' => true, 'enhance' => true, 'anything' => 1,
        'expected_state' => ['queue_status' => null, 'queue_updated_at' => null, 'approved_url' => 'a', 'queue_row' => 9,
                             'injected' => 1]]));
    $api->rejectImage(new Illuminate\Http\Request(['row_number' => '9', 'image_url' => 'u', 'page_url' => 'p',
        'candidate_sha256' => 'ab', 'product_name' => 'Milk', 'brand' => 'Almarai', 'barcode' => '', 'sku_key' => 'k',
        'reason_code' => 'WRONG_SIZE', 'rejection_reasons' => ['WRONG_SIZE'], 'search_decision' => 'REVIEW_PRESELECTED',
        'search_lane' => 'strict', 'candidate_status' => 'preselected', 'candidate_cache_hit' => false,
        'identity_tier' => '1', 'vlm_decision' => 'MATCH', 'candidate_warnings' => '', 'research' => '0',
        'product_name_ar' => '', 'brand_ar' => '', 'category' => '', 'size' => '1L', 'sub_category' => '', 'origin' => '',
        'custom_query' => '', 'phash' => '0f0f0f0f0f0f0f0f', 'content_sha256' => 'cd', 'exclude_urls' => ['x'],
        'skip_cache' => true, 'expected_state' => ['queue_row' => 3], 'anything' => 1]));
    echo json_encode(App\Services\PythonBridge::$calls);
}
"""


@NEEDS_PHP
def test_the_dashboard_forwards_expected_state_and_replace_to_the_bridge(tmp_path):
    (tmp_path / "dashboard").mkdir()
    script = tmp_path / "harness.php"
    script.write_text(API_HARNESS, encoding="utf-8")
    env = dict(os.environ, HARNESS_ROOT=str(tmp_path), CONTROLLER_BASE=str(CONTROLLERS / "Controller.php"),
               API_CONTROLLER=str(CONTROLLERS / "ApiController.php"))
    result = subprocess.run([PHP, str(script)], capture_output=True, text=True, timeout=60, env=env)
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]
    calls = json.loads(result.stdout)
    upload, plain, select, reject = calls
    assert upload[0] == "upload_manual_image"
    assert upload[1]["expected_state"] == {"queue_status": "ready_for_review", "queue_updated_at": "2026-10-03 10:00:00",
                                           "approved_url": None}
    assert upload[1]["replace"] is True
    assert "expected_state" not in plain[1] and "replace" not in plain[1]
    assert select[0] == "select_image" and select[1]["replace"] is True
    assert select[1]["expected_state"] == {"queue_status": None, "queue_updated_at": None, "approved_url": "a",
                                           "queue_row": 9}
    # the allow-list: what the review page sends, nothing else
    assert set(select[1]) == {"image_url", "row_number", "product_name", "sku_key", "candidate_sha256",
                              "candidate_warnings", "search_decision", "replace", "expected_state"}
    # reject: the fields rejectBody sends, nothing else; research is a boolean for the bridge
    assert reject[0] == "reject_image"
    assert set(reject[1]) == {"row_number", "image_url", "page_url", "candidate_sha256", "product_name", "brand",
                              "barcode", "sku_key", "reason_code", "rejection_reasons", "search_decision",
                              "search_lane", "candidate_status", "candidate_cache_hit", "identity_tier",
                              "vlm_decision", "candidate_warnings", "research", "product_name_ar", "brand_ar",
                              "category", "size", "sub_category", "origin", "custom_query"}
    assert reject[1]["research"] is False and reject[1]["rejection_reasons"] == ["WRONG_SIZE"]


def _php_list(name):
    import re
    source = (CONTROLLERS / "ApiController.php").read_text(encoding="utf-8")
    body = re.search(r"const %s = \[(.*?)\];" % name, source, re.S).group(1)
    return set(re.findall(r"'([a-z_0-9]+)'", body))


def test_the_dashboard_and_the_bridge_allow_the_same_reject_fields():
    import cli_bridge
    # skip_cache: CurationController::rejectAndReSearch builds its own body (research on, never the page's fields)
    assert _php_list("REJECT_FIELDS") == set(cli_bridge.REJECT_FIELDS) - {"skip_cache"}
    assert not {"phash", "content_sha256", "exclude_urls", "expected_state"} & set(cli_bridge.REJECT_FIELDS)


def test_the_bridge_drops_reject_fields_the_page_never_sends(monkeypatch):
    import cli_bridge
    seen = {}

    def changed(params, task):
        seen.update(params)
        return True                                     # stop right after the identity check: nothing is written

    monkeypatch.setattr(cli_bridge.local_cache_db, "get_task_by_row", lambda row: None)
    monkeypatch.setattr(cli_bridge, "_product_changed", changed)
    result = cli_bridge.action_reject_image({
        "row_number": 9, "image_url": "https://shop/x.jpg", "product_name": "Milk", "sku_key": "k",
        "reason_code": "WRONG_SIZE", "phash": "0f0f0f0f0f0f0f0f", "content_sha256": "cd", "exclude_urls": ["y"],
        "expected_state": {"queue_row": 3}, "scope": "all", "anything": 1})
    assert result["status"] == "error"
    assert set(seen) == {"row_number", "image_url", "product_name", "sku_key", "reason_code"}


def test_a_rejection_fingerprint_never_comes_from_the_request(monkeypatch):
    import cli_bridge
    monkeypatch.setattr(cli_bridge.local_cache_db, "get_curation_candidates", lambda *a, **k: [])
    monkeypatch.setattr(cli_bridge, "_phash_of_stored", lambda sha: {"ab": "1111111111111111"}.get(sha))
    url = "https://shop/x.jpg"
    assert cli_bridge._candidate_phash(9, url, {"phash": "0f0f0f0f0f0f0f0f"}) == (None, None)
    assert cli_bridge._candidate_phash(9, url, {"phash": "0f0f0f0f0f0f0f0f", "candidate_sha256": "ab"}) ==         ("1111111111111111", None)                     # the stored bytes' fingerprint, not the sent one
