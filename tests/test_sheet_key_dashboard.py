"""The dashboard after the owner switches the Google Sheet or its tab (sheet_key, Laravel side).

Before: the sheet-rows cache was keyed on products_cache.json alone (which does not say which sheet it came from), and
curation_candidates / automation_queue / product_failures / sheet_updates were matched to the sheet by row number or
barcode only, so row 5 of the new sheet got the old sheet's candidates, queue state and failure.

* the sheet-rows cache misses when SPREADSHEET_NAME_OR_URL or SPREADSHEET_TAB_NAME changes (an .env edited by hand);
  ProductController::currentSheetKey is the sheet_key of the last get_products, never a bridge call;
* the review merge (/api/products-json), /api/review/queue-state and the badge count read the current sheet's rows only;
* no filter when the sheet_key column is missing (before the Python migration) or the current key is unknown;
* save-candidates stamps sheet_key and leaves the other sheet's candidates alone;
* POST /api/sheet/save is refused (409, Arabic) while a run is active, and clears the caches after a save;
* «الشيت: X · الورقة: Y» on «اليوم» and in the review headers: a link to «ربط الشيت» for the owner, plain text for a
  reviewer.

The Laravel app runs through the PHP CLI (kernel requests and direct calls in one process: the array cache lives as
long as the process) against two private copies of the test database: one with sheet_key, one without.
"""

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from laqta_kernel import DASH, NEEDS_LARAVEL, NEEDS_NODE, PHP, ROOT
from laqta_review_harness import run as review_run

KEY_A = "1OldSheetIdAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA:0"
KEY_B = "1NewSheetIdBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB:812"
SHEET_URL = "https://docs.google.com/spreadsheets/d/1NewSheetIdBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB/edit"
TABLES = ("curation_candidates", "automation_queue", "product_failures", "sheet_updates")
RUN_ACTIVE = "في تشغيل شغّال هلق. وقّفه أولاً وبعدين غيّر الشيت."

# the new sheet (B): row 5 has the barcode the old sheet's row 5 failed with; row 6 failed in this sheet
PRODUCTS = [
    {"row_number": 5, "product_name": "NIDO MILK POWDER 900G", "brand": "NIDO", "barcode": "6281000000005",
     "sku_key": "sku-b5", "size": "900G"},
    {"row_number": 6, "product_name": "NIDO MILK POWDER 400G", "brand": "NIDO", "barcode": "6281000000006",
     "sku_key": "sku-b6", "size": "400G"},
    {"row_number": 7, "product_name": "NIDO MILK POWDER 2.5KG", "brand": "NIDO", "barcode": "",
     "sku_key": "sku-b7", "size": "2.5KG"},
]


# ---------------------------------------------------------------------------
# Two private databases (with and without sheet_key), cloned from the migrated test database
# ---------------------------------------------------------------------------

def _connect(database=None):
    import pymysql
    return pymysql.connect(host=os.getenv("DB_HOST", "127.0.0.1"), port=int(os.getenv("DB_PORT", "3306")),
                           user=os.getenv("DB_USERNAME", "root"), password=os.getenv("DB_PASSWORD", ""),
                           database=database, connect_timeout=3, autocommit=True, charset="utf8mb4")


def sql(database, statement, params=()):
    conn = _connect(database)
    try:
        cur = conn.cursor()
        cur.execute(statement, params)
        return cur.fetchall()
    finally:
        conn.close()


@pytest.fixture(scope="module")
def dbs():
    try:
        _connect().close()
    except Exception as exc:  # pragma: no cover - depends on the environment
        pytest.skip(f"MariaDB is not reachable: {exc}")
    src = os.environ["DB_DATABASE"]
    assert src != "automation_db", "tests must never touch the production database"
    import local_cache_db
    assert local_cache_db.init_db(), "init_db failed although MariaDB is reachable"
    names = {"col": f"{src}_skc_{os.getpid()}", "nocol": f"{src}_skn_{os.getpid()}"}
    tables = [r[0] for r in sql(src, "SELECT table_name FROM information_schema.tables "
                                     "WHERE table_schema = %s AND table_type = 'BASE TABLE'", (src,))]
    for kind, name in names.items():
        sql(None, f"DROP DATABASE IF EXISTS `{name}`")
        sql(None, f"CREATE DATABASE `{name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
        for table in tables:
            sql(None, f"CREATE TABLE `{name}`.`{table}` LIKE `{src}`.`{table}`")
        for table in TABLES:
            if kind == "col":
                sql(name, f"ALTER TABLE `{table}` ADD COLUMN IF NOT EXISTS sheet_key VARCHAR(128) NULL")
            else:
                sql(name, f"ALTER TABLE `{table}` DROP COLUMN IF EXISTS sheet_key")
    yield names
    for name in names.values():
        sql(None, f"DROP DATABASE IF EXISTS `{name}`")


def seed(database, with_column=True):
    """The old sheet's rows (A) and the new sheet's (B), on the same row numbers."""
    for table in TABLES:
        sql(database, f"DELETE FROM `{table}`")
    sql(database, "DELETE FROM resolved_products")
    k = (lambda key: [key]) if with_column else (lambda key: [])
    col = ", sheet_key" if with_column else ""
    mark = ", %s" if with_column else ""
    # row 5: the old sheet's legacy candidate (no sku_key: matched by row number) and the new sheet's own
    sql(database, f"INSERT INTO curation_candidates (row_number, product_name, brand, image_url, is_selected, status{col}) "
                  f"VALUES (5, 'OLD SHEET PRODUCT', 'OLD', 'https://www.lulu.com/old5.jpg', 1, 'preselected'{mark})",
        k(KEY_A))
    sql(database, f"INSERT INTO curation_candidates (row_number, product_name, brand, image_url, is_selected, status, "
                  f"sku_key{col}) VALUES (6, 'NIDO MILK POWDER 400G', 'NIDO', 'https://www.lulu.com/new6.jpg', 1, "
                  f"'preselected', 'sku-b6'{mark})", k(KEY_B))
    # row 7: an old-sheet candidate that save-candidates for the new row 7 must not delete
    sql(database, f"INSERT INTO curation_candidates (row_number, product_name, brand, image_url, status{col}) "
                  f"VALUES (7, 'OLD SHEET ROW 7', 'OLD', 'https://www.lulu.com/old7.jpg', 'eligible'{mark})", k(KEY_A))
    # the queue: the old sheet's row 5 waits for review; the new sheet's row 6 too
    sql(database, f"INSERT INTO automation_queue (row_number, product_name, brand, status{col}) "
                  f"VALUES (5, 'OLD SHEET PRODUCT', 'OLD', 'ready_for_review'{mark})", k(KEY_A))
    sql(database, f"INSERT INTO automation_queue (row_number, product_name, brand, status, sku_key{col}) "
                  f"VALUES (6, 'NIDO MILK POWDER 400G', 'NIDO', 'ready_for_review', 'sku-b6'{mark})", k(KEY_B))
    # failures: the old sheet's under row 5's barcode, the new sheet's under row 6's
    sql(database, f"INSERT INTO product_failures (barcode, product_name, error_message{col}) "
                  f"VALUES ('6281000000005', 'OLD SHEET PRODUCT', 'old sheet error'{mark})", k(KEY_A))
    sql(database, f"INSERT INTO product_failures (barcode, product_name, error_message{col}) "
                  f"VALUES ('6281000000006', 'NIDO MILK POWDER 400G', 'new sheet error'{mark})", k(KEY_B))


# ---------------------------------------------------------------------------
# The app through the PHP CLI
# ---------------------------------------------------------------------------

STUB = r"""
import base64, json, os, sys
action = sys.argv[1] if len(sys.argv) > 1 else ''
params = json.loads(base64.b64decode(sys.argv[2]).decode('utf-8')) if len(sys.argv) > 2 else {}
open(os.environ['LQ_STUB_CALLS'], 'a', encoding='utf-8').write(json.dumps([action, params]) + '\n')
fixture = json.load(open(os.environ['LQ_STUB_FIXTURE'], encoding='utf-8'))
print(json.dumps(fixture.get(action, {'status': 'error', 'error': 'unexpected action'}), ensure_ascii=False))
"""

RUNNER = r"""<?php
require '__DASH__/vendor/autoload.php';
$app = require '__DASH__/bootstrap/app.php';
$kernel = $app->make(Illuminate\Contracts\Http\Kernel::class);
$kernel->bootstrap();
use App\Http\Controllers\ProductController;
$calls = function () {
    $path = getenv('LQ_STUB_CALLS');
    return is_file($path) ? count(array_filter(file($path), fn ($l) => str_starts_with($l, '["get_products"'))) : 0;
};
$out = [];
foreach (json_decode(file_get_contents($argv[1]), true) as $step) {
    switch ($step[0]) {
        case 'env':
            putenv($step[1] . '=' . $step[2]);
            $out[] = null;
            break;
        case 'write':
            file_put_contents($step[1], $step[2]);
            clearstatcache();
            $out[] = null;
            break;
        case 'rows':
            $error = null;
            $rows = ProductController::sheetRows(false, $error);
            $out[] = ['rows' => $rows === null ? null : count($rows), 'bridge' => $calls(), 'error' => $error];
            break;
        case 'key':
            $out[] = ProductController::currentSheetKey();
            break;
        case 'forget':
            ProductController::forgetProductCaches();
            $out[] = null;
            break;
        case 'phase':
            // «اليوم»'s run state, as batchStatus would report it
            App\Http\Controllers\FakePhase::$phase = $step[1];
            $app->bind(App\Http\Controllers\ApiController::class, App\Http\Controllers\FakePhase::class);
            $out[] = null;
            break;
        case 'cache':
            $out[] = Cache::has($step[1]);
            break;
        case 'put':
            Cache::put($step[1], $step[2], 600);
            $out[] = null;
            break;
        case 'http':
            $request = Illuminate\Http\Request::create($step[2], $step[1], [], [], [],
                ['HTTP_ACCEPT' => 'application/json', 'CONTENT_TYPE' => 'application/json'],
                isset($step[3]) ? json_encode($step[3]) : null);
            $response = $kernel->handle($request);
            $out[] = ['status' => $response->getStatusCode(), 'body' => json_decode($response->getContent(), true)];
            $kernel->terminate($request, $response);
            break;
    }
}
echo json_encode($out, JSON_UNESCAPED_UNICODE);
"""

FAKE_PHASE = r"""
namespace App\Http\Controllers;
class FakePhase extends ApiController {
    public static $phase = 'idle';
    public function batchStatus() { return response()->json(['phase' => self::$phase]); }
}
"""


def laravel_env(tmp_path, database, products_answer, extra=None, sheet_env=None):
    fixture = tmp_path / "fixture.json"
    fixture.write_text(json.dumps(dict({"get_products": products_answer}, **(extra or {})), ensure_ascii=False),
                       encoding="utf-8")
    stub = tmp_path / "stub_bridge.py"
    stub.write_text(STUB, encoding="utf-8")
    compiled = tmp_path / "views"
    compiled.mkdir(exist_ok=True)
    env = dict(os.environ)
    for name in ("SPREADSHEET_NAME_OR_URL", "SPREADSHEET_TAB_NAME"):
        env.pop(name, None)
    env.update({
        "APP_ENV": "testing", "APP_KEY": "base64:" + "A" * 43 + "=", "APP_DEBUG": "true", "SESSION_DRIVER": "array",
        "CACHE_STORE": "array", "LOG_CHANNEL": "stderr", "VIEW_COMPILED_PATH": str(compiled),
        "DB_CONNECTION": "mariadb", "DB_HOST": os.getenv("DB_HOST", "127.0.0.1"), "DB_PORT": os.getenv("DB_PORT", "3306"),
        "DB_DATABASE": database, "DB_USERNAME": os.getenv("DB_USERNAME", "root"),
        "DB_PASSWORD": os.getenv("DB_PASSWORD", ""), "CLI_BRIDGE_PATH": str(stub), "PYTHON_PATH": sys.executable,
        "LQ_STUB_CALLS": str(tmp_path / "calls.txt"), "LQ_STUB_FIXTURE": str(fixture),
        "SPREADSHEET_NAME_OR_URL": SHEET_URL, "SPREADSHEET_TAB_NAME": "Products",
    })
    env.update(sheet_env or {})
    return env


def php(env, steps, tmp_path):
    dash = str(DASH).replace("\\", "/")
    script = tmp_path / "runner.php"
    fake = tmp_path / "fake_phase.php"
    fake.write_text("<?php\n" + FAKE_PHASE, encoding="utf-8")
    script.write_text(RUNNER.replace("__DASH__", dash).replace(
        "use App\\Http\\Controllers\\ProductController;",
        "require '" + str(fake).replace("\\", "/") + "';\nuse App\\Http\\Controllers\\ProductController;"),
        encoding="utf-8")
    steps_file = tmp_path / "steps.json"
    steps_file.write_text(json.dumps(steps, ensure_ascii=False), encoding="utf-8")
    result = subprocess.run([PHP, str(script), str(steps_file)], cwd=DASH, env=env, capture_output=True, text=True,
                            timeout=240, encoding="utf-8")
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-3000:]
    return json.loads(result.stdout[result.stdout.index("["):])


def bridge_actions(tmp_path):
    path = tmp_path / "calls.txt"
    return [json.loads(line)[0] for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


@pytest.fixture
def sheet_cache_file():
    """products_cache.json at the checkout root (python's sheet cache): the dashboard serves its rows cache only while
    it exists. Put back as it was afterwards."""
    path = ROOT / "products_cache.json"
    before = path.read_bytes() if path.exists() else None
    yield str(path).replace("\\", "/")
    if before is None:
        path.unlink(missing_ok=True)
    else:
        path.write_bytes(before)


ANSWER_B = {"status": "success", "products": PRODUCTS, "sheet_tab": "Products", "sheet_key": KEY_B}


# ---------------------------------------------------------------------------
# The sheet-rows cache and currentSheetKey
# ---------------------------------------------------------------------------

@NEEDS_LARAVEL
def test_the_rows_cache_misses_when_the_configured_sheet_or_tab_changes(dbs, tmp_path, sheet_cache_file):
    env = laravel_env(tmp_path, dbs["col"], ANSWER_B)
    out = php(env, [
        ["key"],                                                       # 0 nothing read yet: unknown, no bridge call
        ["write", sheet_cache_file, '{"products": []}'],
        ["rows"],                                                      # 2 read
        ["rows"],                                                      # 3 served from the cache
        ["key"],                                                       # 4
        ["env", "SPREADSHEET_TAB_NAME", "Other tab"],
        ["key"],                                                       # 6 the key was another tab's: unknown
        ["rows"],                                                      # 7 read again
        ["env", "SPREADSHEET_NAME_OR_URL", "another sheet"],
        ["rows"],                                                      # 9 read again
        ["rows"],                                                      # 10 cached again
        ["forget"],
        ["key"],                                                       # 12 an approval empties the rows: key stays
    ], tmp_path)
    assert out[0] is None
    assert out[2] == {"rows": 3, "bridge": 1, "error": None}
    assert out[3]["bridge"] == 1
    assert out[4] == KEY_B
    assert out[6] is None
    assert out[7]["bridge"] == 2
    assert out[9]["bridge"] == 3
    assert out[10]["bridge"] == 3
    assert out[12] == KEY_B
    assert bridge_actions(tmp_path) == ["get_products"] * 3          # currentSheetKey never called the bridge


@NEEDS_LARAVEL
def test_an_old_bridge_without_sheet_key_leaves_the_key_unknown(dbs, tmp_path, sheet_cache_file):
    env = laravel_env(tmp_path, dbs["col"], {"status": "success", "products": PRODUCTS, "sheet_tab": "Products"})
    out = php(env, [["write", sheet_cache_file, "{}"], ["rows"], ["key"]], tmp_path)
    assert out[1]["rows"] == 3 and out[2] is None


# ---------------------------------------------------------------------------
# The review merge, the queue state and the badge
# ---------------------------------------------------------------------------

def _review(out):
    products, queue = out[-2]["body"], out[-1]["body"]
    by_row = {p["row_number"]: p for p in products["products"]}
    return {
        "candidates": {r: [c["image_url"] for c in by_row[r].get("curation_candidates") or []] for r in (5, 6)},
        "errors": {r: (by_row[r]["has_error"], by_row[r]["error_message"]) for r in (5, 6)},
        "queue_rows": sorted(r["row_number"] for r in queue["rows"]),
        "badge": queue["ready_for_review"],
    }


@NEEDS_LARAVEL
def test_the_review_ignores_the_other_sheets_candidates_queue_rows_and_failures(dbs, tmp_path):
    seed(dbs["col"])
    env = laravel_env(tmp_path, dbs["col"], ANSWER_B)
    review = _review(php(env, [["rows"], ["http", "GET", "/api/products-json"],
                               ["http", "GET", "/api/review/queue-state"]], tmp_path))
    assert review["candidates"] == {5: [], 6: ["https://www.lulu.com/new6.jpg"]}
    assert review["errors"] == {5: (False, ""), 6: (True, "new sheet error")}
    assert review["queue_rows"] == [6]
    assert review["badge"] == 1


@NEEDS_LARAVEL
def test_the_review_merge_reads_the_key_of_its_own_sheet_read(dbs, tmp_path):
    """/api/products-json reads the sheet first (no rows cached yet), then filters with the key that read returned."""
    seed(dbs["col"])
    env = laravel_env(tmp_path, dbs["col"], ANSWER_B)
    out = php(env, [["http", "GET", "/api/products-json"], ["http", "GET", "/api/review/queue-state"]], tmp_path)
    review = _review(out)
    assert review["candidates"][5] == [] and review["errors"][5] == (False, "")
    assert review["queue_rows"] == [6]


@NEEDS_LARAVEL
def test_no_filter_while_the_current_sheet_is_unknown(dbs, tmp_path):
    seed(dbs["col"])
    env = laravel_env(tmp_path, dbs["col"], {"status": "success", "products": PRODUCTS})   # no sheet_key
    review = _review(php(env, [["http", "GET", "/api/products-json"],
                               ["http", "GET", "/api/review/queue-state"]], tmp_path))
    # today's behaviour: every row, whatever its sheet
    assert review["candidates"][5] == ["https://www.lulu.com/old5.jpg"]
    assert review["errors"][5] == (True, "old sheet error")
    assert review["queue_rows"] == [5, 6] and review["badge"] == 2


@NEEDS_LARAVEL
def test_no_filter_and_no_stamp_before_the_column_exists(dbs, tmp_path):
    seed(dbs["nocol"], with_column=False)
    env = laravel_env(tmp_path, dbs["nocol"], ANSWER_B)
    out = php(env, [
        ["rows"],
        ["http", "POST", "/api/v1/curation/save-candidates",
         {"row_number": 7, "product_name": "NIDO MILK POWDER 2.5KG", "brand": "NIDO", "sku_key": "sku-b7",
          "candidates": [{"image_url": "https://www.lulu.com/new7.jpg", "status": "eligible"}]}],
        ["http", "GET", "/api/products-json"],
        ["http", "GET", "/api/review/queue-state"],
    ], tmp_path)
    assert out[1]["status"] == 200, out[1]
    review = _review(out)
    assert review["candidates"][5] == ["https://www.lulu.com/old5.jpg"]
    assert review["errors"][5] == (True, "old sheet error")
    assert review["queue_rows"] == [5, 6]
    assert sql(dbs["nocol"], "SELECT image_url FROM curation_candidates WHERE sku_key = 'sku-b7'") == \
        (("https://www.lulu.com/new7.jpg",),)


# ---------------------------------------------------------------------------
# Writers
# ---------------------------------------------------------------------------

@NEEDS_LARAVEL
def test_save_candidates_stamps_the_key_and_keeps_the_other_sheets_candidates(dbs, tmp_path):
    seed(dbs["col"])
    env = laravel_env(tmp_path, dbs["col"], ANSWER_B)
    out = php(env, [
        ["rows"],
        ["http", "POST", "/api/v1/curation/save-candidates",
         {"row_number": 7, "product_name": "NIDO MILK POWDER 2.5KG", "brand": "NIDO", "sku_key": "sku-b7",
          "candidates": [{"image_url": "https://www.lulu.com/new7.jpg", "status": "eligible"}]}],
        ["http", "POST", "/api/v1/curation/save-candidates",                       # saved again: replaces its own
         {"row_number": 7, "product_name": "NIDO MILK POWDER 2.5KG", "brand": "NIDO", "sku_key": "sku-b7",
          "candidates": [{"image_url": "https://www.lulu.com/new7b.jpg", "status": "eligible"}]}],
    ], tmp_path)
    assert out[1]["status"] == 200 and out[2]["status"] == 200
    rows = sql(dbs["col"], "SELECT image_url, sheet_key FROM curation_candidates WHERE row_number = 7 ORDER BY id")
    assert rows == (("https://www.lulu.com/old7.jpg", KEY_A), ("https://www.lulu.com/new7b.jpg", KEY_B))


@NEEDS_LARAVEL
def test_retry_requeues_into_the_current_sheet(dbs, tmp_path):
    seed(dbs["col"])
    env = laravel_env(tmp_path, dbs["col"], ANSWER_B)
    out = php(env, [["rows"], ["http", "POST", "/api/failures/retry", {"all": True}]], tmp_path)
    assert out[1]["status"] == 200 and out[1]["body"]["requeued"] == 1, out[1]
    # only this sheet's failure was retried; the old sheet's stays recorded
    assert sql(dbs["col"], "SELECT barcode FROM product_failures") == (("6281000000005",),)
    assert sql(dbs["col"], "SELECT status, sheet_key FROM automation_queue WHERE row_number = 6") == \
        (("pending", KEY_B),)


@NEEDS_LARAVEL
def test_save_candidates_leaves_the_key_null_while_it_is_unknown(dbs, tmp_path):
    seed(dbs["col"])
    env = laravel_env(tmp_path, dbs["col"], {"status": "success", "products": PRODUCTS})
    out = php(env, [["http", "POST", "/api/v1/curation/save-candidates",
                     {"row_number": 7, "sku_key": "sku-b7",
                      "candidates": [{"image_url": "https://www.lulu.com/new7.jpg", "status": "eligible"}]}]],
              tmp_path)
    assert out[0]["status"] == 200
    assert sql(dbs["col"], "SELECT sheet_key FROM curation_candidates WHERE sku_key = 'sku-b7'") == ((None,),)


def test_candidate_row_carries_the_sheet_key():
    text = (DASH / "app" / "Services" / "CandidateRow.php").read_text(encoding="utf-8")
    assert "?string $sheetKey = null" in text and "'sheet_key' =>" in text
    curation = (DASH / "app" / "Http" / "Controllers" / "CurationController.php").read_text(encoding="utf-8")
    assert "CandidateRow::optionalColumns($c, $skuKey, $runId, $sheetKey)" in curation


# ---------------------------------------------------------------------------
# No sheet change while a run is active
# ---------------------------------------------------------------------------

SAVE = ["http", "POST", "/api/sheet/save", {"spreadsheet_url": "another sheet", "tab_name": "Tab 2"}]


@NEEDS_LARAVEL
@pytest.mark.parametrize("phase", ["starting", "running", "paused", "stopping"])
def test_the_sheet_save_is_refused_while_a_run_is_active(dbs, tmp_path, phase):
    env = laravel_env(tmp_path, dbs["col"], ANSWER_B, extra={"sheet-save": {"status": "success"}})
    out = php(env, [["phase", phase], SAVE], tmp_path)
    assert out[1]["status"] == 409
    assert out[1]["body"] == {"status": "failed", "error_code": "run_active", "error": RUN_ACTIVE}
    assert "sheet-save" not in bridge_actions(tmp_path)


@NEEDS_LARAVEL
def test_a_sheet_save_while_idle_clears_the_sheet_and_health_caches(dbs, tmp_path, sheet_cache_file):
    env = laravel_env(tmp_path, dbs["col"], ANSWER_B, extra={"sheet-save": {"status": "success"}})
    out = php(env, [
        ["write", sheet_cache_file, "{}"], ["rows"], ["put", "ops_health_v1", {"x": 1}],
        ["put", "review_lanes_v3", {"x": 1}],
        ["phase", "idle"], SAVE,
        ["cache", "sheet_rows_v2"], ["cache", "sheet_key_v1"], ["cache", "ops_health_v1"], ["cache", "review_lanes_v3"],
        ["key"],
    ], tmp_path)
    assert out[5]["status"] == 200, out[5]
    assert out[6:10] == [False, False, False, False]
    assert out[10] is None
    assert bridge_actions(tmp_path) == ["get_products", "sheet-save"]


def test_the_settings_and_setup_pages_show_the_409_text():
    settings = (DASH / "public" / "js" / "settings.js").read_text(encoding="utf-8")
    body = settings[settings.index("function sheetError"):settings.index("function saveConfirmText")]
    assert "data.error_code === 'run_active'" in body and RUN_ACTIVE in body
    # «جهّز لقطة» uses the same sheetError for its save (the existing danger status line)
    assert "sheetError: settings.sheetError" in (DASH / "public" / "js" / "setup.js").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# «الشيت: X · الورقة: Y»
# ---------------------------------------------------------------------------

def _render(tmp_path, role, page):
    """«اليوم» (OverviewController::index) or the review page (ReviewController::page) for a user of the role, with
    the database down (the line must not need it)."""
    dash = str(DASH).replace("\\", "/")
    call = ("app(App\\Http\\Controllers\\OverviewController::class)->index()" if page == "today"
            else "app(App\\Http\\Controllers\\ReviewController::class)->page($request)")
    uri = "/" if page == "today" else "/catalog"
    script = f"""<?php
require '{dash}/vendor/autoload.php';
$app = require '{dash}/bootstrap/app.php';
$app->make(Illuminate\\Contracts\\Http\\Kernel::class)->bootstrap();
$request = Illuminate\\Http\\Request::create('{uri}', 'GET');
$route = $app['router']->getRoutes()->match($request);
$request->setRouteResolver(fn () => $route);
$app->instance('request', $request);
$user = new App\\Models\\User(['name' => 'pytest-{role}', 'email' => '{role}@x', 'role' => '{role}']);
auth()->setUser($user);
$request->setUserResolver(fn () => $user);
echo {call}->render();
"""
    with tempfile.NamedTemporaryFile("w", suffix=".php", delete=False, encoding="utf-8") as fh:
        fh.write(script)
        path = fh.name
    compiled = tmp_path / "views"
    compiled.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, APP_ENV="testing", APP_KEY="base64:" + "A" * 43 + "=", APP_DEBUG="true",
               SESSION_DRIVER="array", CACHE_STORE="array", LOG_CHANNEL="stderr", VIEW_COMPILED_PATH=str(compiled),
               DB_CONNECTION="mariadb", DB_HOST="127.0.0.1", DB_PORT="1", DB_DATABASE="laqta_nowhere",
               CLI_BRIDGE_PATH=str(tmp_path / "no_bridge.py"), SPREADSHEET_NAME_OR_URL=SHEET_URL,
               SPREADSHEET_TAB_NAME="Products")
    try:
        result = subprocess.run([PHP, path], cwd=DASH, env=env, capture_output=True, text=True, timeout=180,
                                encoding="utf-8")
    finally:
        os.unlink(path)
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-3000:]
    return result.stdout


def _lines(page):
    return [re.sub(r"\s+", " ", m) for m in re.findall(r'<p class="[^"]*lq-sheet-line[^"]*" data-sheet-line>(.*?)</p>',
                                                       page, re.S)]


@NEEDS_LARAVEL
@pytest.mark.parametrize("page", ["today", "review"])
def test_the_sheet_line_links_for_the_owner_only(tmp_path, page):
    owner = _lines(_render(tmp_path / "admin", "admin", page))
    reviewer = _lines(_render(tmp_path / "reviewer", "reviewer", page))
    assert len(owner) == 1 and len(reviewer) == 1
    text = "الشيت: <bdi>1NewShee…</bdi> · الورقة: <bdi>Products</bdi>"
    assert text in owner[0] and text in reviewer[0]
    assert 'href="/system?tab=sheet"' in owner[0]
    assert "<a" not in reviewer[0] and "href" not in reviewer[0]


@NEEDS_LARAVEL
def test_the_review_config_carries_the_line(tmp_path):
    page = _render(tmp_path / "admin", "admin", "review")
    config = json.loads(re.search(r'data-config="([^"]*)"', page).group(1).replace("&quot;", '"').replace("&amp;", "&"))
    assert config["sheet"] == {"sheet": "1NewShee…", "tab": "Products",
                               "text": "الشيت: 1NewShee… · الورقة: Products", "href": "/system?tab=sheet"}
    reviewer = _render(tmp_path / "reviewer", "reviewer", "review")
    config = json.loads(re.search(r'data-config="([^"]*)"', reviewer).group(1).replace("&quot;", '"').replace("&amp;", "&"))
    assert config["sheet"]["href"] == ""


LINE = {"sheet": "automation sheet", "tab": "Products", "text": "الشيت: automation sheet · الورقة: Products"}


@NEEDS_NODE
@pytest.mark.parametrize("mode", ["single", "bulk"])
@pytest.mark.parametrize("href", ["/system?tab=sheet", ""])
def test_the_review_headers_draw_the_line(tmp_path, mode, href):
    out = review_run(f"""
await boot({{ mode: {json.dumps(mode)}, sheet: {json.dumps(dict(LINE, href=href))} }});
const head = document.querySelector({json.dumps('.rv-queue__head' if mode == 'single' else '.rv-bulk__head')});
const line = head.querySelector('.rv-sheetline');
out.text = line ? line.textContent : null;
const a = line ? line.querySelector('a') : null;
out.href = a ? a.getAttribute('href') : null;
out.muted = line ? line.classList.contains('lq-text--muted') : null;
""", tmp_path, {"products": PRODUCTS, "queue": {"status": "success", "ready_for_review": 0, "rows": []}})
    assert out["text"] == "الشيت: automation sheet · الورقة: Products"
    assert out["href"] == (href or None)
    assert out["muted"] is True
