"""Dashboard product caches: raw sheet rows and enriched review products never share a key.

Before this fix the home page, the brand estimate and the failure retry stored raw sheet rows under
'products_json_v1', and the catalog / batch review stored enriched products (stored candidates, preselection,
cached image) under the same key and served whatever was there. After a home visit, pre-checked products were
missing from review for up to an hour. While a run was active the catalog bypassed the cache and re-read the
whole sheet through Python on every load.

The controller is exercised through the PHP CLI with small stand-ins for the Laravel facades (repo style: no
framework boot). The database fingerprint the controller relies on is checked against MariaDB with the real
writers the worker uses.
"""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DASH = ROOT / "dashboard"
CONTROLLERS = DASH / "app" / "Http" / "Controllers"
PRODUCT = CONTROLLERS / "ProductController.php"
API = CONTROLLERS / "ApiController.php"
CURATION = CONTROLLERS / "CurationController.php"
MATCHER = DASH / "app" / "Services" / "CandidateMatcher.php"
PHP = shutil.which("php")


def read(path):
    return path.read_text(encoding="utf-8")


def method_body(text, name):
    """Source of one PHP method: from its signature to the next method signature (or the class end)."""
    start = text.index(f"function {name}(")
    nxt = re.search(r"\n    (?:public|private|protected)(?: static)? function ", text[start + 1:])
    return text[start:start + 1 + nxt.start()] if nxt else text[start:]


# ---------------------------------------------------------------------------
# Static checks
# ---------------------------------------------------------------------------

def test_no_controller_uses_the_shared_legacy_key():
    for path in CONTROLLERS.glob("*.php"):
        assert "products_json_v1" not in read(path), path.name


def test_raw_rows_and_review_products_have_separate_versioned_keys():
    text = read(PRODUCT)
    raw = re.search(r"SHEET_ROWS_CACHE_KEY = '([^']+)'", text).group(1)
    review = re.search(r"REVIEW_PRODUCTS_CACHE_KEY = '([^']+)'", text).group(1)
    assert raw != review
    assert re.search(r"_v\d+$", raw) and re.search(r"_v\d+$", review)

    # the only cache writes: raw rows in sheetRows(), enriched products in reviewProducts()
    puts = re.findall(r"Cache::put\(\s*([^,]+),", text)
    assert sorted(p.strip() for p in puts) == ["self::REVIEW_PRODUCTS_CACHE_KEY", "self::SHEET_ROWS_CACHE_KEY"]
    assert "SHEET_ROWS_CACHE_KEY" in method_body(text, "sheetRows")
    assert "REVIEW_PRODUCTS_CACHE_KEY" not in method_body(text, "sheetRows")
    assert "REVIEW_PRODUCTS_CACHE_KEY" in method_body(text, "reviewProducts")
    assert "Cache::put(self::SHEET_ROWS_CACHE_KEY" not in method_body(text, "reviewProducts")
    # the enrichment happens only on the review path
    assert "CandidateMatcher::attach" in method_body(text, "reviewProducts")
    assert "CandidateMatcher::attach" not in method_body(text, "sheetRows")

    for path in (API, CURATION):
        assert "Cache::put" not in read(path), path.name


def test_readers_use_the_right_cache():
    product = read(PRODUCT)
    api = read(API)
    assert "self::reviewProducts(" in method_body(product, "getProductsJson")
    assert "self::reviewProducts(" in method_body(product, "index")
    assert "self::sheetRows(" in method_body(product, "getBrandEstimateCount")
    assert "ProductController::sheetRows(" in method_body(api, "retryFailures")
    for name in ("index", "getProductsJson", "getBrandEstimateCount"):
        assert "runPython('get_products')" not in method_body(product, name), name
    assert "runPython('get_products')" not in method_body(api, "retryFailures")


def test_catalog_no_longer_bypasses_the_cache_while_a_run_is_active():
    body = method_body(read(PRODUCT), "getProductsJson")
    assert "curation_pending" not in body and "pre_caching" not in body and "automation_state" not in body


def test_helper_forgets_both_keys():
    body = method_body(read(PRODUCT), "forgetProductCaches")
    assert "Cache::forget(self::SHEET_ROWS_CACHE_KEY)" in body
    assert "Cache::forget(self::REVIEW_PRODUCTS_CACHE_KEY)" in body


@pytest.mark.parametrize("path,name", [
    (API, "selectImage"),               # approve
    (API, "rejectImage"),               # reject
    (API, "uploadManualImage"),         # manual upload
    (API, "retryFailures"),             # retry from the errors page
    (API, "saveSheetConfig"),           # sheet config save
    (API, "clearProductsCache"),        # clear-cache / refresh button
    (API, "resetBatch"),
    (CURATION, "rejectAndReSearch"),
    (CURATION, "selectCandidate"),
    (CURATION, "saveCandidates"),
    (PRODUCT, "updateRichProduct"),
])
def test_every_write_path_calls_the_invalidation_helper(path, name):
    body = method_body(read(path), name)
    assert "forgetProductCaches()" in body, f"{path.name}::{name}"
    assert "Cache::forget(" not in body, f"{path.name}::{name} forgets a key by hand"


# ---------------------------------------------------------------------------
# Behaviour through the PHP CLI (facades replaced by small stand-ins)
# ---------------------------------------------------------------------------

HARNESS = r"""<?php
namespace Illuminate\Http {
    class Request {
        private $q;
        public function __construct(array $q = []) { $this->q = $q; }
        public function query($k, $d = null) { return $this->q[$k] ?? $d; }
    }
}

namespace Illuminate\Support\Facades {
    class DB {
        public static $version = ['candidates' => '0:0:0', 'resolved' => '0:0:0:0', 'failures' => '0:0:0'];
        public static $candidates = [];
        public static $failures = [];   // product_failures: barcode (or ERR_<name>_<brand>) => error_message
        public static $status = 'idle';
        public static function selectOne($sql) { return (object) self::$version; }
        public static function select($sql) { return [(object) ['status' => self::$status]]; }
        public static function table($t) {
            if ($t === 'product_failures') {
                return new \FakeQuery(array_map(fn ($k, $v) => ['barcode' => $k, 'error_message' => $v],
                    array_keys(self::$failures), self::$failures));
            }
            return new \FakeQuery($t === 'curation_candidates' ? self::$candidates : []);
        }
        public static function raw($x) { return $x; }
    }
    class Schema {
        public static function hasColumn($t, $c) { return false; }
    }
}

namespace App\Models {
    class ResolvedProduct {
        public static function query() { return new \FakeQuery([]); }
        public static function count() { return 0; }
    }
    class ProductFailure {
        public static function count() { return 0; }
    }
}

namespace App\Services {
    class PythonBridge {
        public static $calls = 0;
        public static $rows = [];
        // like cli_bridge get_products: google_sheets writes its disk cache (products_cache.json)
        public static function run($action, $params = []) {
            self::$calls++;
            file_put_contents($GLOBALS['ROOT_DIR'] . '/products_cache.json', json_encode(['products' => self::$rows]));
            return ['status' => 'success', 'products' => self::$rows];
        }
        public static function pythonPath() { return 'python'; }
    }
    class QueueStats {
        public static function counters() { return ['by_status' => [], 'by_failure_code' => []]; }
    }
}

namespace {
    class FakeQuery {
        private $rows;
        public function __construct($rows) { $this->rows = $rows; }
        public function __call($name, $args) { return $this; }
        public function get() { return new FakeCollection($this->rows); }
        public function pluck($value, $key) { return new FakeCollection(array_column($this->rows, $value, $key)); }
    }
    class FakeCollection implements IteratorAggregate {
        private $items;
        public function __construct($items) { $this->items = $items; }
        public function map($fn) { return new FakeCollection(array_map($fn, $this->items)); }
        public function all() { return $this->items; }
        public function keyBy($k) { return []; }
        public function getIterator(): Iterator { return new ArrayIterator($this->items); }
    }
    class Cache {
        public static $store = [];
        public static function get($k, $d = null) { return self::$store[$k] ?? $d; }
        public static function put($k, $v, $ttl = null) { self::$store[$k] = $v; return true; }
        public static function forget($k) { unset(self::$store[$k]); return true; }
    }
    class FakeResponse {
        public $data; public $status; public $headers = [];
        public function __construct($data, $status) { $this->data = $data; $this->status = $status; }
        public function header($k, $v) { $this->headers[$k] = $v; return $this; }
    }
    class FakeResponseFactory {
        public function json($data, $status = 200) { return new FakeResponse($data, $status); }
    }
    class_alias('Illuminate\\Support\\Facades\\DB', 'DB');   // Laravel's global facade alias
    function response() { return new FakeResponseFactory(); }
    function view($name, $data = []) { return ['view' => $name, 'data' => $data]; }
    function base_path($p = '') { return $GLOBALS['ROOT_DIR'] . '/dashboard' . ($p !== '' ? '/' . $p : ''); }

    $GLOBALS['ROOT_DIR'] = getenv('HARNESS_ROOT');
    require getenv('CONTROLLER_BASE');
    require getenv('MATCHER_FILE');
    require getenv('PRODUCT_CONTROLLER');

    use Illuminate\Support\Facades\DB;
    use App\Services\PythonBridge;
    use App\Http\Controllers\ProductController;

    $out = [];
    $controller = new ProductController();
    $catalog = function () use ($controller) {
        $r = $controller->getProductsJson(new Illuminate\Http\Request([]));
        $byRow = [];
        foreach ($r->data['products'] ?? [] as $p) {
            $byRow[$p['row_number']] = [
                'needs_review' => !empty($p['needs_review']),
                'candidates' => count($p['curation_candidates'] ?? []),
                'enriched' => array_key_exists('curation_candidates', $p),
                'link' => $p['existing_image_link'] ?? '',
            ];
        }
        $errors = [];
        foreach ($r->data['products'] ?? [] as $p) {
            $errors[$p['row_number']] = !empty($p['has_error']);
        }
        return ['status' => $r->status, 'cache' => $r->headers['X-Cache'] ?? null, 'rows' => $byRow,
                'errors' => $errors, 'python_calls' => PythonBridge::$calls];
    };
    $candidate = function ($id, $row, $sku) {
        return ['id' => $id, 'row_number' => $row, 'product_name' => 'Milk', 'brand' => 'Almarai',
                'image_url' => "https://img.example/{$id}.jpg", 'is_selected' => 1, 'status' => 'preselected',
                'sku_key' => $sku, 'run_id' => "run{$id}"];
    };

    // get_products sets has_error from product_failures when it reads the sheet (cli_bridge.py)
    PythonBridge::$rows = [
        ['row_number' => 2, 'product_name' => 'Milk', 'brand' => 'Almarai', 'barcode' => '', 'sku_key' => 'sku-2',
         'existing_image_link' => '', 'needs_review' => false, 'has_error' => false, 'error_message' => ''],
        ['row_number' => 3, 'product_name' => 'Juice', 'brand' => 'Almarai', 'barcode' => '', 'sku_key' => 'sku-3',
         'existing_image_link' => '', 'needs_review' => false, 'has_error' => false, 'error_message' => ''],
        ['row_number' => 4, 'product_name' => 'Still Water', 'brand' => 'Masafi', 'barcode' => '', 'sku_key' => 'sku-4',
         'existing_image_link' => '', 'needs_review' => false, 'has_error' => false, 'error_message' => ''],
    ];
    DB::$candidates = [$candidate(10, 2, 'sku-2')];
    DB::$version = ['candidates' => '1:10:10', 'resolved' => '0:0:0:0', 'failures' => '0:0:0'];
    // a stopped run left pre-checked candidates behind (automation_state is back to idle)
    DB::$status = 'idle';

    // 1. home first (it used to store raw rows under the shared key), then the catalog
    $home = $controller->index();
    $out['home_review'] = $home['data']['review'] ?? null;
    $out['after_home'] = $catalog();
    $out['estimate'] = $controller->getBrandEstimateCount(new Illuminate\Http\Request(['brand' => 'almarai']))->data;

    // 2. a new run starts (before the fix the catalog skipped the cache in this state); nothing changed yet
    DB::$status = 'pre_caching';
    $out['second'] = $catalog();

    // 3. the worker stores candidates for row 3 (straight into MariaDB, not through Laravel)
    DB::$candidates[] = $candidate(11, 3, 'sku-3');
    DB::$version = ['candidates' => '2:11:21', 'resolved' => '0:0:0:0', 'failures' => '0:0:0'];
    $out['after_worker_candidates'] = $catalog();

    // 3b. the worker records a failure for row 4 (save_product_failure only: no sheet write, no candidates)
    DB::$failures['ERR_Still_Water_Masafi'] = 'NO_RESULTS: No acceptable image found (NO_RESULTS)';
    DB::$version['failures'] = '1:123:456';
    $out['after_worker_failure'] = $catalog();

    // 4. the worker publishes row 2 to the sheet: google_sheets.clear_cache() deletes products_cache.json
    PythonBridge::$rows[0]['existing_image_link'] = 'https://res.cloudinary.com/x/milk.png';
    @unlink($GLOBALS['ROOT_DIR'] . '/products_cache.json');
    $out['after_sheet_write'] = $catalog();

    // 5. a write from the dashboard empties both caches
    $out['keys_before_forget'] = array_keys(Cache::$store);
    $rawKey = defined(ProductController::class . '::SHEET_ROWS_CACHE_KEY') ? ProductController::SHEET_ROWS_CACHE_KEY : null;
    $out['raw_rows_enriched'] = $rawKey === null ? null
        : array_key_exists('curation_candidates', (Cache::$store[$rawKey]['rows'] ?? [[]])[0]);
    if (method_exists(ProductController::class, 'forgetProductCaches')) {
        ProductController::forgetProductCaches();
    }
    $out['keys_after_forget'] = array_keys(Cache::$store);
    $out['home_after_all'] = $controller->index()['data']['review'] ?? null;

    echo json_encode($out);
}
"""


def run_harness(tmp_path, product_controller=PRODUCT):
    root = tmp_path / "root"
    (root / "dashboard").mkdir(parents=True)
    script = tmp_path / "harness.php"
    script.write_text(HARNESS, encoding="utf-8")
    env = dict(os.environ, HARNESS_ROOT=str(root), CONTROLLER_BASE=str(CONTROLLERS / "Controller.php"),
               MATCHER_FILE=str(MATCHER), PRODUCT_CONTROLLER=str(product_controller))
    result = subprocess.run([PHP, str(script)], capture_output=True, text=True, timeout=60, env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


@pytest.mark.skipif(PHP is None, reason="php is not installed")
def test_home_visit_does_not_hide_prechecked_products_from_review(tmp_path):
    out = run_harness(tmp_path)
    first = out["after_home"]
    assert first["status"] == 200
    # row 2 has a stored, preselected candidate: it is in review with its candidate, even right after a home visit
    assert first["rows"]["2"] == {"needs_review": True, "candidates": 1, "enriched": True, "link": ""}
    assert first["rows"]["3"]["enriched"] is True and first["rows"]["3"]["needs_review"] is False
    # home counts the same review products as the catalog
    assert out["home_review"] == 1
    assert out["estimate"] == {"count": 2}


@pytest.mark.skipif(PHP is None, reason="php is not installed")
def test_catalog_reads_the_sheet_once_and_still_sees_worker_results(tmp_path):
    out = run_harness(tmp_path)
    assert out["after_home"]["python_calls"] == 1          # home read the sheet; the catalog reused it
    assert out["second"]["cache"] == "HIT"
    assert out["second"]["python_calls"] == 1              # no sheet read per catalog load during a run

    fresh = out["after_worker_candidates"]
    assert fresh["cache"] == "MISS"
    assert fresh["rows"]["3"] == {"needs_review": True, "candidates": 1, "enriched": True, "link": ""}
    assert fresh["python_calls"] == 1                      # rebuilt from the database only

    written = out["after_sheet_write"]
    assert written["python_calls"] == 2                    # the worker's sheet write is noticed
    assert written["rows"]["2"]["link"] == "https://res.cloudinary.com/x/milk.png"


@pytest.mark.skipif(PHP is None, reason="php is not installed")
def test_worker_failures_reach_the_errors_tab_without_a_sheet_write(tmp_path):
    # the worker records a failure with save_product_failure only; before the fix the review cache kept the
    # has_error value get_products computed at the last sheet read, so the catalog's errors tab stayed stale
    # during a run (the old bypass re-read everything on each load)
    out = run_harness(tmp_path)
    assert out["after_worker_candidates"]["errors"] == {"2": False, "3": False, "4": False}
    failed = out["after_worker_failure"]
    assert failed["cache"] == "MISS"
    assert failed["errors"] == {"2": False, "3": False, "4": True}
    assert failed["python_calls"] == 1                     # rebuilt from the database only
    assert out["after_sheet_write"]["errors"]["4"] is True


@pytest.mark.skipif(PHP is None, reason="php is not installed")
def test_raw_rows_and_review_products_live_under_different_keys(tmp_path):
    out = run_harness(tmp_path)
    assert sorted(out["keys_before_forget"]) == ["review_products_v2", "sheet_rows_v2"]
    assert out["raw_rows_enriched"] is False               # the raw-rows cache never holds enriched products
    assert out["keys_after_forget"] == []
    assert out["home_after_all"] == 2                      # rows 2 and 3 both have stored candidates


# ---------------------------------------------------------------------------
# The database fingerprint changes on every worker write (real MariaDB)
# ---------------------------------------------------------------------------

def review_version_sql():
    text = read(PRODUCT)
    decl = re.search(r"REVIEW_VERSION_SQL = (.*?);\n", text, re.DOTALL).group(1)
    return "".join(re.findall(r'"((?:[^"\\]|\\.)*)"', decl))


def test_fingerprint_sql_is_one_read_only_select():
    sql = review_version_sql()
    assert sql.startswith("SELECT ") and ";" not in sql
    assert "curation_candidates" in sql and "resolved_products" in sql
    for verb in ("INSERT", "UPDATE", "DELETE", "ALTER", "DROP"):
        assert verb not in sql.upper()


def test_worker_writes_change_the_review_fingerprint(mariadb_or_skip):
    db = mariadb_or_skip
    conn = db.get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM curation_candidates")
        cursor.execute("DELETE FROM resolved_products")
        cursor.execute("DELETE FROM product_failures")
        conn.commit()
    finally:
        conn.close()

    def version():
        conn = db.get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(review_version_sql())
            row = cursor.fetchone()
            return (row["candidates"], row["resolved"], row["failures"]) if isinstance(row, dict) else tuple(row)
        finally:
            conn.close()

    def candidates(url):
        return [{"url": url, "title": "Almarai Milk 1L", "status": "preselected", "domain": "almarai.com"},
                {"url": url + "?b", "title": "Almarai Milk", "status": "candidate", "domain": "noon.com"}]

    # a review cache built just before a write must not match the state right after it
    seen = [version()]

    def changed():
        now = version()
        assert now != seen[-1], now
        seen.append(now)

    # the worker stores candidates for a row, then a newer run replaces them (delete + insert)
    assert db.save_curation_candidates(7, "Milk", "Almarai", candidates("https://a.example/1.jpg"),
                                       "https://a.example/1.jpg", sku_key="sku-7", run_id="r1")
    changed()
    assert db.save_curation_candidates(7, "Milk", "Almarai", candidates("https://a.example/2.jpg"),
                                       "https://a.example/2.jpg", sku_key="sku-7", run_id="r2")
    changed()
    # an approval / rejection from the bridge removes them
    db.delete_curation_candidates(7, sku_key="sku-7")
    changed()

    # auto-publish inserts a resolution, a later publish of the same SKU updates it, a reject supersedes it
    assert db.save_product_resolution("6281007000014", "Milk", "Almarai", "https://a.example/1.jpg",
                                      "https://res.cloudinary.com/x/1.png", None, None,
                                      verification_status="auto_verified", approved_by="auto", sku_key="sku-7")
    changed()
    conn = db.get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("UPDATE resolved_products SET resolved_at = NOW() - INTERVAL 1 HOUR")
        conn.commit()
    finally:
        conn.close()
    seen.append(version())
    assert db.save_product_resolution("6281007000014", "Milk", "Almarai", "https://a.example/2.jpg",
                                      "https://res.cloudinary.com/x/2.png", None, None,
                                      verification_status="human_approved", approved_by="reviewer",
                                      sku_key="sku-7")
    changed()
    assert db.supersede_resolution("sku-7") == 1
    changed()

    # the worker records a failure (no sheet write), the same product fails again later, a retry clears it
    db.save_product_failure("", "Still Water", "Masafi", "NO_RESULTS: No acceptable image found")
    changed()
    conn = db.get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("UPDATE product_failures SET failed_at = NOW() - INTERVAL 1 HOUR")
        conn.commit()
    finally:
        conn.close()
    seen.append(version())
    db.save_product_failure("", "Still Water", "Masafi", "SEARCH_ERROR: search raised an exception")
    changed()
    db.delete_product_failure("ERR_Still_Water_Masafi")
    changed()
