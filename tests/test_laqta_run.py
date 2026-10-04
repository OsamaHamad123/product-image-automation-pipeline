"""Laqta Home (الرئيسية) and Run (التشغيل) pages, package p2-run.

- Structure: both pages extend layouts.laqta, keep their script in public/js and their style in public/css/pages,
  use only hooks, classes and tokens that exist, show no raw decision/failure codes and no user chip.
- One number, one source: the PHP helpers behind every count (QueueStats, RunController, OverviewController) run
  under the PHP CLI and are compared with the Python code they mirror (main.parse_row_filter, ops_health costs,
  local_cache_db.review_stats).
- The page scripts run under node: view functions and page controllers with recorded fetch / confirm / toast.
- The Laravel app runs through its HTTP kernel against the MariaDB test database with a stub cli_bridge: routes,
  the ?tab=review redirect, the sources behind each number, truthful unavailable states, and no stored secret in
  any page or API. Those are skipped when php, dashboard/vendor or MariaDB is missing.
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from blade_scripts import inline_scripts

ROOT = Path(__file__).resolve().parents[1]
DASH = ROOT / "dashboard"
VIEWS = DASH / "resources" / "views"
HOME = VIEWS / "dashboard" / "index.blade.php"
RUN = VIEWS / "dashboard" / "batch_automation.blade.php"
JS = DASH / "public" / "js"
COMMON_JS, RUN_JS, HOME_JS = JS / "run-common.js", JS / "run.js", JS / "home.js"
HOME_CSS, RUN_CSS = DASH / "public" / "css" / "pages" / "home.css", DASH / "public" / "css" / "pages" / "run.css"
LAQTA_CSS = DASH / "public" / "css" / "laqta.css"
CONTROLLERS = DASH / "app" / "Http" / "Controllers"
QUEUE_STATS = DASH / "app" / "Services" / "QueueStats.php"
ROUTES = DASH / "routes" / "web.php"
VENDOR_AUTOLOAD = DASH / "vendor" / "autoload.php"

PHP = shutil.which("php")
NODE = shutil.which("node")
NEEDS_PHP = pytest.mark.skipif(PHP is None, reason="php is not installed")
NEEDS_NODE = pytest.mark.skipif(NODE is None, reason="node is not installed")
NEEDS_LARAVEL = pytest.mark.skipif(PHP is None or not VENDOR_AUTOLOAD.exists(),
                                   reason="php or dashboard/vendor is not installed")

PAGE_FILES = [HOME, RUN, COMMON_JS, RUN_JS, HOME_JS, HOME_CSS, RUN_CSS]
RAW_CODES = ["REVIEW_PRESELECTED", "REVIEW_UNSELECTED", "NO_RESULTS", "PROVIDER_DOWN", "SEARCH_ERROR",
             "ALL_CONFLICTED", "SERPER_CREDIT", "GEMINI_DOWN", "VERIFIER_NOT_CONFIGURED", "vlm:", "T1"]


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Structure
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path, nav, title", [(HOME, "home", "الرئيسية"), (RUN, "run", "التشغيل")],
                         ids=["home", "run"])
def test_pages_extend_the_laqta_layout(path, nav, title):
    text = read(path)
    assert "@extends('layouts.laqta')" in text and "layouts.layout'" not in text
    assert f"@section('title', '{title}')" in text and f"@section('lq_nav', '{nav}')" in text
    assert "@push('styles')" in text and "@push('scripts')" in text
    # page script and style are local files; no inline executable script, nothing from another host
    assert not inline_scripts(text)
    assert not re.search(r"(?:href|src)=\"(?:https?:)?//", text)
    scripts = re.findall(r"asset\('js/([a-z-]+\.js)'\)", text)
    assert scripts == ["run-common.js", "home.js" if nav == "home" else "run.js"]
    # the initial snapshot is JSON data, escaped by @json
    assert f'<script type="application/json" id="lq-{nav}-initial">@json($live)</script>' in text


def test_no_user_chip_and_no_raw_codes_as_text():
    for path in PAGE_FILES:
        text = read(path)
        assert "أسامة" not in text and "مدير" not in text, path.name
        for code in RAW_CODES:
            assert code not in text, (path.name, code)
    emoji = re.compile("[\U0001F300-\U0001FAFF☀-➿️]")
    for path in PAGE_FILES + [CONTROLLERS / "RunController.php", CONTROLLERS / "OverviewController.php"]:
        assert not emoji.findall(read(path)), path.name


def _hooks(js: str, fn: str):
    return set(re.findall(fn + r"\('([a-z-]+)'\)", js))


def test_every_hook_the_scripts_use_exists_in_the_page():
    run, home = read(RUN), read(HOME)
    missing = _hooks(read(RUN_JS), r"\$") - set(re.findall(r'data-run="([a-z-]+)"', run))
    assert not missing, sorted(missing)
    missing = (_hooks(read(HOME_JS), r"\$") | _hooks(read(HOME_JS), r"\$\$")) - set(re.findall(r'data-home="([a-z-]+)"', home))
    assert not missing, sorted(missing)
    assert "data-run-page" in run and "data-home-page" in home
    assert "getElementById('lq-run-initial')" in read(RUN_JS) and 'id="lq-run-initial"' in run
    assert "getElementById('lq-home-initial')" in read(HOME_JS) and 'id="lq-home-initial"' in home


CLASS_RE = re.compile(r"(?<![\w-])lq-[a-z0-9]+(?:(?:__|--|-)[a-z0-9]+)*")


def _css_classes(*paths):
    classes = set()
    for p in paths:
        css = re.sub(r"/\*.*?\*/", "", read(p), flags=re.DOTALL)
        classes |= set(re.findall(r"\.(lq-[a-z0-9]+(?:(?:__|--|-)[a-z0-9]+)*)", css))
    return classes


def test_classes_and_tokens_exist():
    defined = _css_classes(LAQTA_CSS, HOME_CSS, RUN_CSS)
    for path in (HOME, RUN, COMMON_JS, RUN_JS, HOME_JS):
        text = read(path)
        # 'lq-page-home' / 'lq-page-run' are body classes; ids and hooks are not classes
        used = {c for c in CLASS_RE.findall(text)
                if not c.startswith(("lq-run-initial", "lq-home-initial", "lq-run-new-title", "lq-run-current-title",
                                     "lq-run-rows", "lq-home-funnel-title", "lq-home-lastrun-title",
                                     "lq-home-ready-title", "lq-home-services-title"))}
        used = {c.rstrip("-") for c in used}
        # classes built at runtime: 'lq-chip--' + status, 'lq-stat--' + tone, 'lq-tone--' + tone, 'lq-dot--' + tone
        used -= {"lq-chip", "lq-stat", "lq-tone", "lq-dot", "lq-kpi__note", "lq-legend__swatch lq-tone"}
        missing = sorted(c for c in used if c not in defined and c not in ("lq-page-home", "lq-page-run"))
        assert not missing, (path.name, missing)
    tokens = set(re.findall(r"(--lq-[a-z0-9-]+)\s*:", read(LAQTA_CSS)))
    for css in (HOME_CSS, RUN_CSS):
        text = read(css)
        used = set(re.findall(r"var\((--lq-[a-z0-9-]+)", text))
        assert not used - tokens, sorted(used - tokens)
        assert not re.search(r"--lq-[a-z0-9-]+\s*:", text), "page CSS must not define tokens"
        for physical in ("margin-left", "margin-right", "padding-left", "padding-right"):
            assert physical not in text, physical
        assert not re.search(r"(?<![-\w])(left|right)\s*:", text), "use inset-inline-start/end"
        assert "@media (max-width: 980px)" in text and "@media (max-width: 560px)" in text


@NEEDS_NODE
@pytest.mark.parametrize("path", [COMMON_JS, RUN_JS, HOME_JS], ids=lambda p: p.name)
def test_page_scripts_parse_and_stay_plain(path):
    result = subprocess.run([NODE, "--check", str(path)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    text = read(path)
    assert "innerHTML" not in text and "eval(" not in text and "new Function" not in text
    assert "require(" not in text and "import " not in text


def test_routes():
    routes = read(ROUTES)
    assert "Route::get('/', [OverviewController::class, 'index'])->name('dashboard.index');" in routes
    assert ("Route::get('/batch-automation', [RunController::class, 'page'])->name('dashboard.batch_automation');"
            in routes)
    for line in ("Route::get('/api/overview', [OverviewController::class, 'data']);",
                 "Route::get('/api/run/live', [RunController::class, 'live']);",
                 "Route::get('/api/run/plan', [RunController::class, 'plan']);"):
        assert line in routes, line
    # the Phase 1 run controls are untouched and still the only writers
    for line in ("Route::post('/api/run-all', [ApiController::class, 'runAll']);",
                 "Route::post('/api/stop-batch', [ApiController::class, 'stopBatch']);",
                 "Route::post('/api/batch/reset', [ApiController::class, 'resetBatch']);",
                 "Route::get('/api/batch-status', [ApiController::class, 'batchStatus']);"):
        assert line in routes, line
    for name in ("RunController.php", "OverviewController.php"):
        text = read(CONTROLLERS / name)
        assert "DB::table('automation_queue')->update" not in text and "->delete(" not in text, name
        assert not re.search(r"\b(INSERT|UPDATE|DELETE)\b", text), name


# ---------------------------------------------------------------------------
# PHP helpers under the PHP CLI
# ---------------------------------------------------------------------------

def _php(code: str):
    service = str(QUEUE_STATS).replace("\\", "/")
    run = str(CONTROLLERS / "RunController.php").replace("\\", "/")
    overview = str(CONTROLLERS / "OverviewController.php").replace("\\", "/")
    script = ("<?php\nnamespace App\\Http\\Controllers { abstract class Controller {} }\nnamespace {\n"
              f"require '{service}';\nrequire '{run}';\nrequire '{overview}';\n"
              "use App\\Services\\QueueStats;\nuse App\\Http\\Controllers\\RunController;\n"
              "use App\\Http\\Controllers\\OverviewController;\n$out = [];\n"
              + code + "\necho json_encode($out, JSON_UNESCAPED_UNICODE);\n}\n")
    with tempfile.NamedTemporaryFile("w", suffix=".php", delete=False, encoding="utf-8") as fh:
        fh.write(script)
        path = fh.name
    try:
        result = subprocess.run([PHP, path], capture_output=True, text=True, timeout=60, encoding="utf-8")
    finally:
        os.unlink(path)
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


def php_value(obj) -> str:
    """A PHP expression that evaluates to the JSON value (arrays stay associative)."""
    text = json.dumps(obj, ensure_ascii=False).replace("\\", "\\\\").replace("'", "\\'")
    return f"json_decode('{text}', true)"


@NEEDS_PHP
def test_result_kinds_are_plain_arabic():
    cases = [("completed", None, None), ("ready_for_review", None, "REVIEW_PRESELECTED"),
             ("ready_for_review", None, "AUTO_PUBLISH"), ("ready_for_review", "VERIFIER_DOWN", "REVIEW_UNSELECTED"),
             ("ready_for_review", None, None), ("failed", "NO_RESULTS", "NOT_FOUND"), ("failed", "ALL_CONFLICTED", None),
             ("failed", None, "NOT_FOUND"), ("failed", "SEARCH_ERROR", None), ("failed", "CANDIDATE_SAVE_FAILED", None),
             ("pending", "PROVIDER_DOWN", None), ("pending", None, None), ("processing", None, None),
             ("pending", "REJECTED", None)]
    out = _php("foreach (" + php_value(cases) + " as [$s, $c, $d]) { $out['kinds'][] = QueueStats::resultKind($s, $c, $d); }\n"
               "$out['labels'] = QueueStats::RESULT_KINDS; $out['failure'] = QueueStats::FAILURE_TEXT;")
    assert out["kinds"] == ["approved", "proposed", "proposed", "none", "none", "not_found", "not_found", "not_found",
                            "error", "error", "requeued", "waiting", "searching", "rejected"]
    labels = {k: v["label"] for k, v in out["labels"].items()}
    assert labels["proposed"] == "مقترحة" and labels["none"] == "بلا اقتراح" and labels["not_found"] == "ما انلقت"
    assert labels["error"] == "عطل مؤقت" and labels["requeued"] == "رجعت للطابور"
    assert labels["rejected"] == "رجعت للطابور"            # a reviewer's rejection reads as on the review page
    chips = {"proposed", "warning", "none", "not-found", "approved", "error"}          # x-lq.chip statuses
    assert {v["chip"] for v in out["labels"].values()} <= chips
    for text in list(labels.values()) + list(out["failure"].values()):
        assert re.search(r"[\u0600-\u06FF]", text) and not re.search(r"\b[A-Z]{3,}_[A-Z_]+\b", text), text


@NEEDS_PHP
def test_counting_words_and_row_labels():
    out = _php("foreach ([1, 2, 3, 10, 11, 40] as $n) { $out['count'][] = QueueStats::countText($n); }\n"
               "$out['rows'] = [QueueStats::rowsLabel([62, 63, 64, 101, 5, 5]), QueueStats::rowsLabel([9]),"
               " QueueStats::rowsLabel([]), QueueStats::rowsLabel([1, 3, 5, 7, 9])];")
    assert out["count"] == ["منتج واحد", "منتجين", "3 منتجات", "10 منتجات", "11 منتج", "40 منتج"]
    assert out["rows"] == ["5، 62–64، 101", "9", "", "1، 3، 5، …"]


ROW_FILTERS = ["", "5", "2-5, 9", "62-101", " 7 , 8 ", "5-3", "a", "5-", "-5", "1,,2", ",", "٦٢-١٠١", "5، 9",
               "+3", "3.5", "2-4-6", "10-10", "2 - 4", "62–101"]


@NEEDS_PHP
def test_row_filter_matches_main_parse_row_filter():
    """The plan counts the same rows the enqueue will: PHP parseRowFilter == main.parse_row_filter."""
    import main

    out = _php("foreach (" + php_value(ROW_FILTERS) + " as $t) { $out[] = QueueStats::parseRowFilter($t); }")
    for text, php in zip(ROW_FILTERS, out):
        try:
            expected = main.parse_row_filter(php["text"])
        except ValueError:
            expected = "error"
        if expected == "error":
            assert php["error"] and php["ranges"] is None, (text, php)
        elif expected is None:
            assert php["ranges"] is None and not php["error"], (text, php)
        else:
            got = set()
            for a, b in php["ranges"]:
                got.update(range(a, b + 1))
            assert got == expected and not php["error"], (text, php)
        if php["error"]:
            assert re.search(r"[\u0600-\u06FF]", php["error"])


def _outcome(serper=(), google=(), vlm=0, decision="REVIEW_PRESELECTED", at=None):
    health = [{"provider": "serper", "status": s} for s in serper] + [{"provider": "google", "status": s} for s in google]
    out = {"decision": decision, "failure_code": None, "provider_health": health, "vlm_calls": vlm}
    if at:
        out["searched_at"] = at
    return out


@NEEDS_PHP
def test_run_cost_counts_what_ops_health_bills():
    import ops_health

    def extra(outcome, health=(), usage=None):
        outcome["provider_health"] += [{"provider": p, "status": s} for p, s in health]
        if usage is not None:
            outcome["vlm_usage"] = usage
        return outcome

    read = lambda provider, model, usd, role="primary": {"role": role, "provider": provider, "model": model,
                                                          "input_tokens": 1200, "output_tokens": 80, "usd": usd}
    outcomes = [_outcome(("ok", "ok", "empty"), ("ok",), 2), _outcome(("quota", "error"), (), 0),
                _outcome(("ok",), ("error",), 3), _outcome((), (), 1, decision="NOT_FOUND"), None,
                # the expansion round: every Serper endpoint, SerpApi Lens (answered or not), and readers that recorded
                # their spend (billed by it, not by the flat price per call); a malformed entry is ignored
                extra(_outcome(("ok",), (), 3, decision="REVIEW_UNSELECTED"),
                      [("serper_web", "ok"), ("serper_shopping", "empty"), ("lens_serper", "quota"),
                       ("lens_serpapi", "ok"), ("lens_serpapi", "error"), ("page", "ok")],
                      [read("gemini", "gemini-3.1-flash-lite", 0.0004), read("claude", "claude-sonnet-5-5", 0.012, "strong"),
                       read("gemini", "gemini-3.1-flash-lite", -1), {"provider": "", "model": "x", "usd": 5}, "junk"]),
                extra(_outcome((), (), 2), [("lens_serpapi", "empty")], [])]
    rows = [{"status": "ready_for_review", "failure_code": None, "age_s": 30,
             "outcome_json": json.dumps(o) if o else None, "has_trace": True} for o in outcomes]
    entries = [e for e in (ops_health.entry_from_row(r) for r in rows) if e]
    expected = ops_health.window_stats(entries)["cost_usd"]["total"]
    prices = {"serper_per_query": ops_health.SERPER_COST_PER_QUERY, "gemini_per_call": ops_health.GEMINI_COST_PER_CALL,
              "lens_serpapi": ops_health.source_prices()["lens_serpapi"]}
    out = _php("$s = 0; $v = 0; $l = 0; $u = 0.0; foreach (" + php_value([o for o in outcomes if o]) + " as $o) {"
               " $use = QueueStats::outcomeUsage($o); $s += $use['serper_queries']; $v += $use['unpriced_vlm_calls'];"
               " $l += $use['serpapi_calls']; $u += $use['vlm_usd']; }\n"
               "$out['cost'] = QueueStats::usageCost($s, $v, " + php_value(prices) + ", $l, $u);\n"
               "$out['counts'] = [$s, $v, $l];\n"
               "$out['defaults'] = QueueStats::DEFAULT_PRICES; $out['answered'] = QueueStats::ANSWERED_STATUSES;"
               " $out['billed'] = QueueStats::SERPER_BILLED_PROVIDERS;")
    assert out["cost"] == pytest.approx(expected) and expected > 0.012
    # Serper 3 + 1 + 3 (web ok, shopping empty, not the refused lens); flat reads 2+0+3+1+2 (vlm_usage [] records
    # nothing); SerpApi 1 ok + 1 empty
    assert out["counts"] == [7, 8, 2]
    assert out["defaults"] == dict(prices, lens_serpapi=ops_health.SERPAPI_LENS_DEFAULT_COST)
    assert tuple(out["answered"]) == ops_health.ANSWERED_STATUSES
    assert tuple(out["billed"]) == ops_health.SERPER_BILLED_PROVIDERS


@NEEDS_PHP
def test_run_summary_counts_and_explains():
    t0 = datetime(2026, 9, 30, 9, 40, tzinfo=timezone.utc)
    iso = lambda s: (t0 + timedelta(seconds=s)).isoformat(timespec="seconds")
    rows = [
        {"row_number": 62, "status": "ready_for_review", "outcome": _outcome(("ok",), (), 1, at=iso(0))},
        {"row_number": 63, "status": "ready_for_review", "outcome": _outcome(("ok",), (), 1, "REVIEW_UNSELECTED", iso(20))},
        {"row_number": 64, "status": "failed", "failure_code": "NO_RESULTS", "outcome": _outcome(("ok",), (), 0, "NOT_FOUND", iso(40))},
        {"row_number": 65, "status": "failed", "failure_code": "SEARCH_ERROR", "outcome": None, "age_s": 0},
        {"row_number": 66, "status": "pending", "failure_code": "PROVIDER_DOWN", "outcome": _outcome(("error",), (), 0, "PROVIDER_DOWN", iso(80))},
        {"row_number": 67, "status": "pending", "outcome": None},
        {"row_number": 68, "status": "completed", "outcome": _outcome(("ok",), (), 2, "AUTO_PUBLISH", iso(60))},
    ]
    now = int(t0.timestamp()) + 100
    out = _php(f"$out['s'] = QueueStats::runSummary({php_value(rows)}, null, {now});\n"
               "$out['totals'] = QueueStats::runTotals('r', ['ready_for_review' => 2, 'failed' => 2, 'pending' => 2,"
               " 'completed' => 1]);\n"
               "$out['clean'] = QueueStats::explainFailures(['proposed' => 3]);\n"
               "$out['nothing'] = QueueStats::explainFailures([]);")
    s = out["s"]
    assert s["counts"] == {"proposed": 1, "none": 1, "not_found": 1, "error": 1, "requeued": 1, "rejected": 0,
                           "approved": 1, "searching": 0, "waiting": 1}
    assert sum(s["counts"].values()) == s["total"] == 7
    # the same "processed" as the sidebar and /api/batch-status (QueueStats::runTotals)
    assert s["processed"] == out["totals"]["processed"] == 5
    assert s["rows_label"] == "62–68"
    assert s["started_at"] == int(t0.timestamp()) and s["duration_s"] == 100      # the SEARCH_ERROR row at 'now'
    assert s["cost_usd"] == pytest.approx(0.004 + 0.004)                         # 4 answered queries, 4 Gemini calls
    explain = s["explain"]
    assert "منتج واحد (الصف 66) رجع للطابور" in explain and "بتنعاد لحالها بالتشغيل الجاي" in explain
    assert "الأعطال المؤقتة: منتج واحد (الصف 65)" in explain and "صار خطأ أثناء البحث" in explain
    assert "ما لقيناله صورة مناسبة" in explain and "لسا ما انبحث عنه" in explain
    assert not re.search(r"[A-Z]{3,}_", explain)
    assert out["clean"] == "كل المنتجات انبحث عنها بدون أعطال." and out["nothing"] == ""


@NEEDS_PHP
def test_stuck_reason_only_when_stuck():
    cases = {
        "error": ("error", "none", "error", 0, 5, 0),
        "orphan_processing": ("review", "none", "curation_pending", 2, 5, 0),
        "orphan_one": ("review", "none", "curation_pending", 1, 5, 0),
        "orphan_many": ("review", "none", "curation_pending", 5, 5, 0),
        "state_without_worker": ("idle", "none", "running", 0, 30, 0),
        "no_progress": ("running", "running", "running", 0, 1200, 0),
        "paused_long": ("paused", "running", "running", 0, 5000, 1),
        "slow_enqueue": ("starting", "starting", "starting", 0, 400, 0),
        "running_fine": ("running", "running", "running", 1, 20, 0),
        "idle": ("idle", "none", "idle", 0, 9999, 0),
        "review": ("review", "none", "curation_pending", 0, 9999, 0),
    }
    out = _php("foreach (" + php_value(cases) + " as $k => [$p, $w, $s, $n, $age, $pause]) {"
               " $out[$k] = QueueStats::stuckReason($p, $w, $s, $n, $age, $pause); }")
    for key in ("error", "orphan_processing", "state_without_worker", "no_progress", "slow_enqueue"):
        assert out[key] and re.search(r"[\u0600-\u06FF]", out[key]), key
    assert "20 دقيقة" in out["no_progress"]
    # the words agree with the number (review fix: «منتج واحد عالقة … يكمّلها»)
    assert out["orphan_one"] == "منتج واحد عالق على «عم ندوّر» وما في عامل شغّال يكمّله."
    assert out["orphan_processing"] == "منتجين عالقين على «عم ندوّر» وما في عامل شغّال يكمّلها."
    assert out["orphan_many"] == "5 منتجات عالقة على «عم ندوّر» وما في عامل شغّال يكمّلها."
    for key in ("paused_long", "running_fine", "idle", "review"):
        assert out[key] == "", key


SHEET = [
    {"row_number": 2, "product_name": "Almarai Milk 1L", "brand": "Almarai", "sku_key": "lqt-2",
     "existing_image_link": "https://res.cloudinary.com/demo/a.png"},
    {"row_number": 3, "product_name": "Almarai Laban", "brand": "Almarai", "sku_key": "lqt-3", "existing_image_link": ""},
    {"row_number": 4, "product_name": "Al Rawabi Milk", "brand": "Al Rawabi", "sku_key": "lqt-4", "existing_image_link": ""},
    {"row_number": 5, "product_name": "Healthy Eggs", "brand": "Healthy Farms", "sku_key": "lqt-5", "existing_image_link": ""},
    {"row_number": 6, "product_name": "تمر خلاص", "brand": "", "sku_key": "lqt-6", "existing_image_link": ""},
    {"row_number": 7, "product_name": "Tuna", "brand": "Rio Mare", "sku_key": "lqt-7", "existing_image_link": ""},
    {"row_number": 8, "product_name": "Kids Milk", "brand": "Almarai Kids", "sku_key": "lqt-8", "existing_image_link": ""},
    {"row_number": 9, "product_name": "Old Juice", "brand": "Old Brand", "sku_key": "lqt-9",
     "existing_image_link": "needs_review:https://x.ae/old.jpg", "needs_review": True},
    {"row_number": 10, "product_name": "Juice", "brand": "Masafi", "sku_key": "lqt-10", "existing_image_link": ""},
    {"row_number": 11, "product_name": "Water", "brand": "Masafi", "sku_key": "lqt-11", "existing_image_link": ""},
    {"row_number": 12, "product_name": "Chips", "brand": "Lays", "sku_key": "lqt-12", "existing_image_link": ""},
]

# row -> (status, failure_code, decision, lease_held); row 50 waits for review but is no longer in the sheet
QUEUE = {3: ("ready_for_review", None, "REVIEW_PRESELECTED", False), 4: ("ready_for_review", None, "REVIEW_UNSELECTED", False),
         5: ("failed", "NO_RESULTS", "NOT_FOUND", False), 6: ("failed", "SEARCH_ERROR", None, False),
         7: ("pending", "PROVIDER_DOWN", "PROVIDER_DOWN", False), 10: ("completed", None, "AUTO_PUBLISH", False),
         11: ("pending", None, None, False), 12: ("processing", None, None, True),
         50: ("ready_for_review", None, "REVIEW_PRESELECTED", False)}


def _queue_by_row():
    return {str(row): {"status": s, "failure_code": c, "decision": d, "sku_key": f"lqt-{row}", "lease_held": held}
            for row, (s, c, d, held) in QUEUE.items()}


FUNNEL = {"published": 2, "review": 2, "review_link": 1, "not_found": 1, "failed": 2, "not_searched": 3}


@NEEDS_PHP
def test_funnel_stages_sum_to_the_sheet_and_the_kpis_are_its_stages():
    out = _php(f"$f = OverviewController::funnel({php_value(SHEET)}, {php_value(_queue_by_row())});\n"
               "$out['f'] = $f;\n"
               "$out['k'] = OverviewController::kpis(['status' => 'ok', 'waiting' => 3, 'proposed' => 2, 'none' => 1],"
               " ['status' => 'ok'] + $f);\n"
               "$out['down'] = OverviewController::kpis(['status' => 'error', 'message' => 'x'],"
               " ['status' => 'error', 'message' => 'ما قدرنا نقرأ الشيت هلق']);")
    f = out["f"]
    assert f["counts"] == FUNNEL
    assert sum(f["counts"].values()) == f["total"] == len(SHEET)
    assert sum(s["value"] for s in f["stages"]) == f["total"]
    assert [s["key"] for s in f["stages"]] == ["published", "review", "review_link", "not_found", "failed", "not_searched"]
    assert f["orphans"] == 1
    assert f["notes"] == ["منتج واحد بانتظار المراجعة صفّه مش موجود بالشيت هلق، فمحسوب بعدّاد المراجعة وبرّا هالشريط."]
    k = out["k"]
    assert [k[key]["value"] for key in ("waiting", "published", "not_found", "failed")] == [3, 2, 1, 2]
    assert k["waiting"]["value"] == f["counts"]["review"] + f["orphans"]            # badge = funnel + orphans
    assert k["waiting"]["note"] == "2 منها مقترحة وجاهزة"
    assert "QueueStats::counters" in k["waiting"]["source"] and "/api/batch-status" in k["waiting"]["source"]
    for key in ("published", "not_found", "failed"):
        assert "OverviewController::funnel" in k[key]["source"]
    # a source that cannot be read gives no number (the page shows «—»), never zero
    assert all(v["value"] is None for v in out["down"].values())
    assert out["down"]["published"]["note"] == "ما قدرنا نقرأ الشيت هلق"


@NEEDS_PHP
def test_plan_mirrors_the_enqueue():
    calls = {"all": ("all", "", "", False), "rows": ("rows", "", "3-5", False), "brand": ("brand", "almarai", "", False),
             "force": ("all", "", "", True), "reversed": ("rows", "", "9-3", False), "empty_rows": ("rows", "", "", False),
             "no_brand": ("brand", "", "", False)}
    out = _php("foreach (" + php_value(calls) + " as $k => [$scope, $brand, $rows, $force]) {"
               f" $out[$k] = RunController::buildPlan({php_value(SHEET)}, {php_value(_queue_by_row())},"
               " $scope, $brand, $rows, $force); }\n"
               f"$out['brands'] = RunController::brandCounts({php_value(SHEET)});")
    pick = lambda p: {k: p[k] for k in ("in_scope", "to_search", "skipped_final", "skipped_review_link",
                                         "kept_waiting", "kept_approved", "leftovers", "total")}
    assert pick(out["all"]) == {"in_scope": 11, "to_search": 5, "skipped_final": 1, "skipped_review_link": 1,
                                "kept_waiting": 2, "kept_approved": 2, "leftovers": 0, "total": 5}
    # rows 3-5; the pending rows 7 and 11 are left in the queue and begin_run gives them to this run too
    assert pick(out["rows"]) == {"in_scope": 3, "to_search": 1, "skipped_final": 0, "skipped_review_link": 0,
                                 "kept_waiting": 2, "kept_approved": 0, "leftovers": 2, "total": 3}
    # the brand filter is main.py's substring match: "almarai" also takes "Almarai Kids"
    assert pick(out["brand"]) == {"in_scope": 3, "to_search": 1, "skipped_final": 1, "skipped_review_link": 0,
                                  "kept_waiting": 1, "kept_approved": 0, "leftovers": 2, "total": 3}
    assert pick(out["force"])["to_search"] == 11 and out["force"]["total"] == 11
    assert "مقلوب" in out["reversed"]["error"] and out["reversed"]["total"] == 0
    assert out["empty_rows"]["error"] and out["no_brand"]["error"] == "اختار الماركة اللي بدك تشغّلها."
    labels = [b["label"] for b in out["brands"]]
    # each count is what a run with that brand takes (review fix): «Almarai» also takes «Almarai Kids»
    assert labels[:3] == ["Almarai (3 منتجات)", "Masafi (منتجين)", "Al Rawabi (منتج واحد)"]
    assert "Almarai Kids (منتج واحد)" in labels and "Lays (منتج واحد)" in labels
    assert all(b["brand"] for b in out["brands"])                           # no empty brand option


@NEEDS_PHP
def test_estimates_and_auto_publish_text():
    week = {"windows": {"7d": {"searches": 10, "cost_usd": {"total": 0.05}}}}
    thin = {"windows": {"7d": {"searches": 2, "cost_usd": {"total": 0.01}}}}
    out = _php(f"$out['cost'] = [RunController::costPerProduct({php_value(week)}), RunController::costPerProduct({php_value(thin)}),"
               " RunController::costPerProduct(null)];\n"
               "$out['rate'] = [RunController::medianRate(['a' => [0, 20, 40, 60], 'b' => [0, 10, 20], 'c' => [5, 5, 5], 'd' => [1, 2]]),"
               " RunController::medianRate([])];\n"
               "$out['auto'] = [RunController::autoPublishText(false, []), RunController::autoPublishText(true, []),"
               " RunController::autoPublishText(true, ['*']), RunController::autoPublishText(true, ['Almarai', 'category:Dairy'])];\n"
               "$out['defaults'] = [RunController::DEFAULT_SECONDS_PER_PRODUCT, RunController::DEFAULT_COST_PER_PRODUCT];")
    assert out["cost"] == [0.005, None, None]
    assert out["rate"] == [15.0, None]                     # median of 20 and 10; flat and short runs ignored
    off, empty, star, some = out["auto"]
    assert off.startswith("النشر الآلي مطفأ") and "مراجعتك" in off
    assert "ما في ولا ماركة مسموحة" in empty and "لكل الماركات" in star
    assert "Almarai" in some and "فئة Dairy" in some
    assert out["defaults"] == [20.0, 0.003]


@NEEDS_PHP
def test_readiness_uses_review_stats():
    import local_cache_db

    rows = []
    for i in range(12):
        rows.append({"id": i + 1, "created_at": f"2026-09-01 10:{i:02d}:00", "action": "approved", "sku_key": f"alali-{i}",
                     "brand": "AL ALALI", "image_url": f"https://x.ae/{i}.jpg", "was_preselected": 1,
                     "engine_decision": "REVIEW_PRESELECTED"})
    for i in range(4):
        rows.append({"id": 100 + i, "created_at": f"2026-09-02 10:{i:02d}:00",
                     "action": "approved" if i else "rejected", "sku_key": f"virg-{i}", "brand": "VIRGINIA",
                     "image_url": f"https://x.ae/v{i}.jpg", "was_preselected": 1, "engine_decision": "REVIEW_PRESELECTED",
                     "reason_code": "WRONG_SIZE" if not i else None})
    stats = dict({"status": "success"}, **local_cache_db.review_stats(rows))
    out = _php(f"$out = OverviewController::readiness({php_value(stats)});")
    assert out["bar_pct"] == round(stats["thresholds"]["min_lower_bound"] * 100)
    assert out["perfect_record_reviews"] == stats["thresholds"]["perfect_record_reviews"]
    assert f"حوالي {out['perfect_record_reviews']} مراجعة صحيحة بدون ولا غلطة" in out["intro"]
    brands = {b["brand"]: b for b in out["brands"]}
    alali = next(b for b in stats["brands"] if b["brand"] == "AL ALALI")
    assert brands["AL ALALI"]["text"] == f"12 مراجعة صحيحة · مضمون {int(alali['lower_bound'] * 100)}%"
    assert brands["AL ALALI"]["pct"] == pytest.approx(round(12 / alali["reviews_needed"] * 100, 1))
    virginia = next(b for b in stats["brands"] if b["brand"] == "VIRGINIA")
    assert brands["VIRGINIA"]["tone"] == ("danger" if virginia["status"] == "low_precision" or virginia["reviews_needed"] is None
                                          else "teal")
    assert brands["VIRGINIA"]["text"].startswith("3 من 4 صحيحة")            # a rejected pick is never hidden
    for b in out["brands"]:
        assert not re.search(r"[a-z]+_[a-z]+", b["text"]), b["text"]         # no status codes in the text


@NEEDS_PHP
def test_services_greeting_date_and_alerts():
    diag = {"checked_at": "2026-09-30T09:40:00+00:00", "services": {
        "google_sheets": {"status": "online"}, "serper": {"status": "offline"}, "gemini": {"status": "disabled"},
        "photoroom": {"status": "online"}, "cloudinary": {"status": "online"}, "proxy": {"status": "offline"}}}
    alerts = [{"code": "SERPER_CREDIT", "message": "رصيد Serper انتهى أو المفتاح مرفوض", "detail": "quota / 401 / 403"},
              {"code": "GEMINI_DOWN", "message": "Gemini لا يستجيب"}, {"code": "X", "message": ""}]
    out = _php(f"$out['s'] = OverviewController::services({php_value(diag)});\n"
               "$out['none'] = OverviewController::services(null);\n"
               "$out['g'] = [OverviewController::greeting(9), OverviewController::greeting(15, 'سارة'),"
               " OverviewController::greeting(2)];\n"
               "$out['d'] = OverviewController::dateLine(3, 30, 9);\n"
               f"$out['a'] = OverviewController::alerts({php_value(alerts)});")
    items = {i["key"]: (i["name"], i["label"], i["tone"]) for i in out["s"]["items"]}
    assert items == {"google_sheets": ("Google Sheet", "متصل", "success"), "serper": ("Serper", "ما بيرد", "danger"),
                     "gemini": ("Gemini", "مش مفعّل", "muted"), "photoroom": ("PhotoRoom", "يعمل", "success"),
                     "cloudinary": ("Cloudinary", "يعمل", "success")}
    assert out["s"]["checked_at"] == int(datetime(2026, 9, 30, 9, 40, tzinfo=timezone.utc).timestamp())
    assert out["none"] == {"checked_at": None, "items": [], "text": "ما انعمل فحص بعد"}
    assert out["g"] == ["صباح الخير", "مسا الخير يا سارة", "مسا الخير"]
    assert out["d"] == "الأربعاء، 30 أيلول"
    assert [a["title"] for a in out["a"]] == ["رصيد Serper انتهى أو المفتاح مرفوض", "Gemini لا يستجيب"]
    assert all("401" not in a["text"] and "SERPER" not in a["text"] for a in out["a"])


# ---------------------------------------------------------------------------
# Page scripts under node
# ---------------------------------------------------------------------------

def _node(script: str):
    result = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=60, encoding="utf-8")
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


def _js(*files) -> str:
    return "globalThis.window = globalThis;\n" + "\n".join(read(f) for f in files) + "\n"


HARNESS = r"""
const fetchLog = [], toasts = [], confirms = [];
const views = { live: [], plan: [], start: [], overview: [], banner: [] };
let lives = [], liveIndex = 0, confirmAnswer = true, runAllAnswer = { ok: true, status: 200, data: { status: 'success' } };
let planAnswers = [];
function fakeFetch(url, opts) {
    fetchLog.push({ url, method: (opts && opts.method) || 'GET', body: opts && opts.body });
    if (url === '/api/run/live') return Promise.resolve({ ok: true, status: 200, data: lives[Math.min(liveIndex++, lives.length - 1)] });
    if (url.startsWith('/api/run/plan')) return planAnswers.length ? planAnswers.shift()
        : Promise.resolve({ ok: true, status: 200, data: { status: 'success', total: 3, skipped_final: 1, estimate: {} } });
    if (url === '/api/run-all') return Promise.resolve(runAllAnswer);
    return Promise.resolve({ ok: true, status: 200, data: { status: 'success', message: 'رسالة الخادم' } });
}
const deps = {
    fetchJson: fakeFetch,
    renderLive: v => views.live.push(v), renderPlan: v => views.plan.push(v), renderStart: v => views.start.push(v),
    renderOverview: v => views.overview.push(v), renderBanner: v => views.banner.push(v),
    confirm: t => { confirms.push(t); return confirmAnswer; }, toast: (t, v) => toasts.push([t, v]),
    now: () => 1790000000, schedule: () => 0
};
const tick = () => new Promise(r => setTimeout(r, 0));
const last = (list) => list[list.length - 1];
"""


def _batch(phase, **extra):
    base = {"phase": phase, "phase_text": f"text:{phase}", "alert": "", "status": "idle", "is_running": False,
            "stop_requested": 0, "pause_requested": 0, "current_product": "",
            "run": {"run_id": "r1", "total": 40, "processed": 24, "ready_for_review": 20, "completed": 0, "failed": 4,
                    "pending": 15, "processing": 1},
            "queue": {"ready_for_review": 33}, "ready_for_review": 33, "stuck": "", "worker": "none"}
    base.update(extra)
    return base


RUN_CURRENT = {"run_id": "r1", "total": 40, "processed": 24, "current": True, "rows_label": "62–101",
               "started_at": 1789999000, "ended_at": 1789999480, "duration_s": 480, "per_product_s": 20.0,
               "cost_usd": 0.11, "explain": "",
               "counts": {"proposed": 13, "none": 7, "not_found": 2, "error": 2, "requeued": 0, "approved": 0,
                          "searching": 1, "waiting": 15}}


@NEEDS_NODE
def test_common_helpers_match_the_php_words():
    out = _node(_js(COMMON_JS) + r"""
const C = LaqtaRunCommon;
const d = new Date(2026, 8, 30, 9, 40);
console.log(JSON.stringify({
    count: [1, 2, 3, 10, 11, 40].map(n => C.countText(n)),
    dur: [30, 60, 120, 360, 1260, 3600, 4200].map(C.durationText),
    usd: [0.19, 0, 0.004, null, 0.4099].map(C.usdText),
    rows: ['٦٢-١٠١', '5، 9', ' 2 - 4 ', '62–101'].map(C.normalizeRows),
    date: C.dateLine(d), greet: [C.greeting(9), C.greeting(18, 'سارة'), C.greeting(3, '  ')],
    alert: C.alertText('محركات البحث غير متاحة. | رصيد Serper انتهى أو المفتاح مرفوض'),
    ago: [C.agoText(1790000000 - 5, 1790000000), C.agoText(1790000000 - 20, 1790000000), C.agoText(1790000000 - 125, 1790000000)],
    finished: [C.runJustFinished('running', 'review'), C.runJustFinished('review', 'idle'), C.runJustFinished(null, 'error'),
               C.runJustFinished('stopping', 'error')],
    tiles: C.resultTiles({ proposed: 13, none: 7, not_found: 2, error: 2, requeued: 1, approved: 0 }).map(t => [t.label, t.value])
}));
""")
    assert out["count"] == ["منتج واحد", "منتجين", "3 منتجات", "10 منتجات", "11 منتج", "40 منتج"]
    assert out["dur"] == ["أقل من دقيقة", "دقيقة", "دقيقتين", "6 دقائق", "21 دقيقة", "ساعة", "ساعة و10 دقائق"]
    assert out["usd"] == ["$0.19", "$0.00", "أقل من $0.01", "—", "$0.41"]
    assert out["rows"] == ["62-101", "5, 9", "2 - 4", "62-101"]
    assert out["date"] == "الأربعاء، 30 أيلول"
    assert out["greet"] == ["صباح الخير", "مسا الخير يا سارة", "مسا الخير"]
    assert out["alert"] == "محركات البحث غير متاحة. رصيد Serper انتهى أو المفتاح مرفوض."
    assert out["ago"] == ["الآن", "20 ث", "2 د"]
    assert out["finished"] == [True, False, False, True]
    assert out["tiles"] == [["مقترحة", 13], ["بلا اقتراح", 7], ["ما انلقت", 2], ["أعطال مؤقتة", 3]]


@NEEDS_NODE
def test_run_view_for_every_phase():
    snaps = {
        "running": {"status": "success", "batch": _batch("running", is_running=True, current_product="Tuna"), "run": RUN_CURRENT, "recent": [
            {"row": 85, "name": "TASTY FOOD", "kind": "proposed", "label": "مقترحة", "chip": "proposed", "at": 1790000000 - 3, "href": "/catalog?row=85"},
            {"row": 86, "name": "ZWAN", "kind": "searching", "label": "عم ندوّر", "chip": "none", "at": None, "href": "/catalog?row=86"}]},
        "paused": {"status": "success", "batch": _batch("paused", is_running=True), "run": RUN_CURRENT, "recent": []},
        "stopping": {"status": "success", "batch": _batch("stopping", is_running=True, stop_requested=1), "run": RUN_CURRENT, "recent": []},
        "stuck": {"status": "success", "batch": _batch("review", stuck="منتجين عالقة على «عم ندوّر»"),
                  "run": dict(RUN_CURRENT, current=False), "recent": []},
        "idle": {"status": "success", "batch": _batch("idle", ready_for_review=0), "run": None, "recent": []},
        # a new run reads the sheet; the rows shown so far belong to the previous run
        "starting": {"status": "success", "batch": _batch("starting", is_running=True, run={"run_id": None, "total": 0}),
                     "run": dict(RUN_CURRENT, current=False),
                     "recent": [{"row": 85, "name": "OLD", "kind": "proposed", "label": "مقترحة", "chip": "proposed",
                                 "at": 1790000000 - 3, "href": "/catalog?row=85"}]},
        # a new run failed while reading the sheet (no run_id yet): the summary is of the run before it
        "start_failed": {"status": "success",
                         "batch": _batch("error", status="error", alert="ENQUEUE_FAILED: x", ready_for_review=4,
                                         run={"run_id": None, "total": 0, "processed": 0}),
                         "run": dict(RUN_CURRENT, current=False, is_state_run=False, processed=40), "recent": []},
        "down": {"status": "error", "message": "قاعدة البيانات مش متاحة هلق"},
        "garbage": None,
    }
    out = _node(_js(COMMON_JS, RUN_JS) + f"""
const snaps = {json.dumps(snaps, ensure_ascii=False)};
const out = {{}};
for (const [k, s] of Object.entries(snaps)) out[k] = LaqtaRunPage.describeLive(s, 1790000000, 12);
console.log(JSON.stringify(out));
""")
    run = out["running"]
    assert run["chip"]["label"] == "يعمل" and run["meta"].startswith("بدأ ") and run["meta"].endswith("الصفوف 62–101")
    p = run["progress"]
    assert (p["done"], p["total"], p["percent"], p["countsText"]) == (24, 40, 60, "24 من 40 في هذا التشغيل")
    assert p["remaining"] == "متبقي تقريباً 5 دقائق"                          # 16 left x 20 s (the run's own pace)
    assert [[t["label"], t["value"]] for t in p["legend"]] == [["مقترحة", 13], ["بلا اقتراح", 7], ["ما انلقت", 2],
                                                                ["أعطال مؤقتة", 2]]
    assert sum(s["pct"] for s in p["segments"]) == pytest.approx(60, abs=0.5)
    assert p["phaseText"] == "عم ندوّر هلق على: Tuna"
    assert p["pause"] == {"action": "pause", "label": "إيقاف مؤقت", "icon": "pause", "disabled": False}
    assert p["stop"] == {"disabled": False, "label": "إيقاف"} and run["startBlocked"] is True
    assert [r["ago"] for r in run["recent"]] == ["الآن", "هلق"] and run["recent"][0]["href"] == "/catalog?row=85"
    assert out["paused"]["chip"]["label"] == "متوقف مؤقتاً" and out["paused"]["progress"]["remaining"] == ""
    starting = out["starting"]
    assert starting["recent"] == [] and starting["progress"]["percentText"] == "—" and starting["meta"] == "عم نقرأ الشيت"
    assert out["paused"]["progress"]["pause"]["action"] == "resume" and out["paused"]["progress"]["pause"]["label"] == "استئناف"
    assert out["stopping"]["progress"]["stop"] == {"disabled": True, "label": "طلب الإيقاف مسجّل"}
    assert out["stopping"]["progress"]["pause"]["disabled"] is True
    stuck = out["stuck"]
    assert stuck["stuck"] == {"visible": True, "reason": "منتجين عالقة على «عم ندوّر»"} and stuck["progress"] is None
    assert stuck["state"] == "stopped" and stuck["chip"]["label"] == "وقف قبل ما يخلص"
    assert out["idle"]["state"] == "idle" and out["idle"]["stuck"]["visible"] is False and out["idle"]["finished"] is None
    failed = out["start_failed"]
    assert failed["state"] == "error" and failed["startFailed"] is True
    assert failed["finished"]["title"] == "التشغيل اللي قبله"          # never «آخر تشغيل وقف بعطل» for a good run
    assert "قبل ما يبحث عن ولا منتج" in failed["message"] and failed["finished"]["reviewVisible"] is True
    for key in ("down", "garbage"):
        view = out[key]
        assert view["state"] == "unavailable" and view["chip"]["label"] == "مش معروف"   # never idle or zero
        assert view["progress"] is None and view["startBlocked"] is False
    assert out["down"]["message"] == "قاعدة البيانات مش متاحة هلق"


@NEEDS_NODE
def test_plan_texts():
    answers = {
        "ready": {"ok": True, "data": {"status": "success", "total": 40, "skipped_final": 18, "kept_waiting": 2,
                                        "leftovers": 3, "estimate": {"cost_usd": 0.13, "seconds": 840,
                                                                     "time_basis": "history", "cost_basis": "history",
                                                                     "seconds_per_product": 21}}},
        "fresh_install": {"ok": True, "data": {"status": "success", "total": 5, "skipped_final": 1,
                                                "estimate": {"cost_usd": 0.015, "seconds": 100, "time_basis": "default",
                                                             "cost_basis": "history"}}},
        "nothing": {"ok": True, "data": {"status": "success", "total": 0, "skipped_final": 3, "estimate": {}}},
        "invalid": {"ok": True, "data": {"status": "success", "error": "النطاق 9-3 مقلوب"}},
        "sheet_down": {"ok": False, "status": 503, "data": {"status": "error", "message": "ما قدرنا نقرأ الشيت هلق"}},
        "network": {"ok": False, "status": 0, "data": None},
    }
    out = _node(_js(COMMON_JS, RUN_JS) + f"""
const answers = {json.dumps(answers, ensure_ascii=False)};
const out = {{}};
for (const [k, a] of Object.entries(answers)) out[k] = LaqtaRunPage.describePlan(a);
out.bodies = [
    LaqtaRunPage.runBody({{ scope: 'all', brand: 'X', rows: '1-2', force: false, skipCache: true }}),
    LaqtaRunPage.runBody({{ scope: 'brand', brand: ' Almarai ', rows: '1-2', force: true, skipCache: false }}),
    LaqtaRunPage.runBody({{ scope: 'rows', brand: 'X', rows: '٦٢-١٠١، 5', force: false, skipCache: false }})
];
out.query = [LaqtaRunPage.planQuery({{ scope: 'rows', rows: '٦٢-١٠١', force: true }}, true),
             LaqtaRunPage.planQuery({{ scope: 'brand', brand: 'Al Rawabi' }}, false)];
console.log(JSON.stringify(out));
""")
    ready = out["ready"]
    assert ready["headline"] == "رح نبحث عن 40 منتج"
    assert ready["detail"] == ("18 عندها صورة نهائية وبنتخطاها، منتجين بانتظار مراجعتك أصلاً وما منعيد البحث عنها، "
                               "ومعهم 3 منتجات باقية بالطابور من تشغيل سابق. التكلفة التقريبية $0.13، والوقت تقريباً 14 دقيقة.")
    assert "تقدير أولي" in out["fresh_install"]["detail"] and "منتج واحد عنده صورة نهائية وبنتخطاه" in out["fresh_install"]["detail"]
    assert out["nothing"]["headline"] == "ما في منتجات جديدة ندوّر عليها بهالنطاق" and "«إعادة البحث»" in out["nothing"]["detail"]
    assert out["invalid"]["state"] == "invalid" and out["invalid"]["invalid"] == "النطاق 9-3 مقلوب"
    assert out["sheet_down"] == {"state": "error", "headline": "ما في تقدير هلق", "detail": "ما قدرنا نقرأ الشيت هلق"}
    assert out["network"]["state"] == "error" and out["network"]["detail"]
    assert out["bodies"] == [
        {"row_filter": "", "brand_filter": "", "forceOverwrite": False, "skipCache": True},
        {"row_filter": "", "brand_filter": "Almarai", "forceOverwrite": True, "skipCache": False},
        {"row_filter": "62-101, 5", "brand_filter": "", "forceOverwrite": False, "skipCache": False},
    ]
    assert out["query"] == ["scope=rows&rows=62-101&force=1&refresh=1", "scope=brand&brand=Al%20Rawabi"]


@NEEDS_NODE
def test_run_controls_confirm_and_post_only_the_phase1_endpoints():
    out = _node(_js(COMMON_JS, RUN_JS) + HARNESS + r"""
lives = [{ status: 'success', batch: { phase: 'idle', run: {}, ready_for_review: 0 }, run: null, recent: [] }];
const ctl = LaqtaRunPage.createController(deps);
(async () => {
    const log = () => fetchLog.filter(f => f.method === 'POST').map(f => f.url);
    // rows scope without rows: nothing is posted
    ctl.state.form.scope = 'rows';
    const noRows = await ctl.start();
    const rowsError = last(views.start).error;
    // force: the confirmation says what it does and keeps; declined -> nothing posted
    Object.assign(ctl.state.form, { scope: 'all', force: true });
    confirmAnswer = false;
    const declined = await ctl.start();
    confirmAnswer = true;
    const started = await ctl.start();
    const startBody = fetchLog.filter(f => f.url === '/api/run-all').map(f => f.body)[0];
    // the server refuses (a run is already going): its Arabic reason is shown
    runAllAnswer = { ok: false, status: 400, data: { status: 'failed', error: 'عملية الأتمتة قيد التشغيل بالفعل حالياً.' } };
    ctl.state.form.force = false;
    const refused = await ctl.start();
    const refusedError = last(views.start).error;
    confirmAnswer = false;
    await ctl.stop(); await ctl.reset();
    const afterDeclined = log().length;
    confirmAnswer = true;
    await ctl.stop(); await ctl.reset(); await ctl.pause(); await ctl.resume();
    console.log(JSON.stringify({ noRows, rowsError, declined, started, refused, refusedError, startBody,
        posts: log(), afterDeclined, confirms, toasts }));
})();
""")
    assert out["noRows"] is False and out["rowsError"] == "اكتب الصفوف اللي بدك تشغّلها، مثل 62-101."
    assert out["declined"] is False and out["started"] is True and out["refused"] is False
    assert out["refusedError"] == "عملية الأتمتة قيد التشغيل بالفعل حالياً."
    assert out["startBody"] == {"row_filter": "", "brand_filter": "", "forceOverwrite": True, "skipCache": False}
    assert out["afterDeclined"] == 2                                   # the two starts; declined stop/reset post nothing
    assert out["posts"] == ["/api/run-all", "/api/run-all", "/api/stop-batch", "/api/batch/reset", "/api/batch/pause",
                            "/api/batch/resume"]
    force, _, stop, reset, stop2, reset2 = out["confirms"]
    assert "الصور المنشورة بالشيت بتضل مكانها" in force and "ما بينكتب أبداً فوق صورة اعتمدها مراجع" in force
    assert "لا يُحذف أي صف" in stop and stop == stop2
    assert "لا يُحذف أي منتج جاهز للمراجعة أو معتمد أو فاشل" in reset and reset == reset2
    assert any(t[0] == "رسالة الخادم" for t in out["toasts"])        # the server's own Arabic message is shown


@NEEDS_NODE
def test_run_plan_ignores_answers_to_an_older_form():
    out = _node(_js(COMMON_JS, RUN_JS) + HARNESS + r"""
let release;
planAnswers = [new Promise(r => { release = r; }),
               Promise.resolve({ ok: true, status: 200, data: { status: 'success', total: 7, skipped_final: 0, estimate: {} } })];
const ctl = LaqtaRunPage.createController(deps);
(async () => {
    const first = ctl.setForm({ scope: 'brand', brand: 'A' });
    await ctl.setForm({ scope: 'brand', brand: 'B' });
    release({ ok: true, status: 200, data: { status: 'success', total: 99, skipped_final: 0, estimate: {} } });
    await first; await tick();
    console.log(JSON.stringify({ shown: views.plan.filter(v => v.state === 'ready').map(v => v.total) }));
})();
""")
    assert out["shown"] == [7]


@NEEDS_NODE
def test_home_views_are_truthful():
    overview = {"status": "success",
                "kpis": {"waiting": {"value": 3, "note": "2 منها مقترحة وجاهزة", "tone": "success", "source": "QueueStats::counters"},
                         "published": {"value": 2, "note": "من أصل 11 منتج بالشيت"},
                         "not_found": {"value": None, "note": "ما قدرنا نقرأ الشيت هلق"},
                         "failed": {"value": 0, "note": "ما في أعطال"}},
                "sheet": {"status": "ok", "total": 11, "stages": [{"key": "published", "label": "منشورة", "value": 2, "tone": "teal"},
                                                                  {"key": "not_searched", "label": "لسا ما انبحث عنها", "value": 9, "tone": "empty"}],
                          "notes": ["منتج واحد بانتظار المراجعة صفوفه مش موجودة بالشيت"]},
                "readiness": {"status": "ok", "intro": "…98%…", "brands": []},
                "cost": {"status": "ok", "week_usd": 0.41},
                "services": {"checked_at": None, "items": [], "text": "ما انعمل فحص بعد"},
                "alerts": [{"title": "رصيد Serper انتهى أو المفتاح مرفوض", "text": "البحث واقف"},
                           {"title": "Gemini لا يستجيب", "text": "كلها لمراجعتك"}]}
    out = _node(_js(COMMON_JS, HOME_JS) + f"""
const H = LaqtaHomePage;
const ov = H.describeOverview({{ ok: true, status: 200, data: {json.dumps(overview, ensure_ascii=False)} }}, 1790000000);
const failed = H.describeOverview({{ ok: false, status: 500, data: null }}, 1790000000);
const down = H.describeHomeLive({{ status: 'error', message: 'قاعدة البيانات مش متاحة هلق' }}, 1790000000);
const summary = H.describeHomeLive({{ status: 'success', batch: {{ phase: 'review', ready_for_review: 33, run: {{}} }},
    run: {json.dumps(dict(RUN_CURRENT, current=False, processed=40, total=40), ensure_ascii=False)}, recent: [] }}, 1790000000);
const empty = H.describeHomeLive({{ status: 'success', batch: {{ phase: 'idle', ready_for_review: 0, run: {{}} }}, run: null }}, 1790000000);
const banners = [
    H.mergeBanner(null, []),
    H.mergeBanner({{ title: 'انتبه:', text: 'رصيد Serper انتهى أو المفتاح مرفوض.', variant: 'warning' }}, ov.alerts),
    H.mergeBanner(null, ov.alerts)
];
console.log(JSON.stringify({{ ov, failed, down, summary, empty, banners }}));
""")
    ov = out["ov"]
    assert [k["value"] for k in ov["kpis"]] == ["3", "2", "—", "0"]              # null is «—», never 0
    assert ov["funnel"]["meta"] == "11 منتج" and ov["funnel"]["notes"]
    assert [s["pct"] for s in ov["funnel"]["segments"]] == [18.2, 81.8]
    assert ov["cost"]["text"] == "$0.41" and ov["services"]["checkedText"] == "ما انعمل فحص بعد"
    assert ov["readiness"]["state"] == "empty" and "لسا ما في مراجعات كفاية" in ov["readiness"]["message"]
    failed = out["failed"]
    assert failed["state"] == "error" and all(k["value"] == "—" for k in failed["kpis"])
    assert failed["funnel"]["state"] == "error" and failed["cost"]["text"] == "—"
    assert out["down"]["waiting"] is None and out["down"]["lastRun"]["mode"] == "unavailable"
    s = out["summary"]
    assert s["waiting"] == 33 and s["lastRun"]["mode"] == "summary"
    assert s["lastRun"]["meta"].endswith("الصفوف 62–101 · 8 دقائق · $0.11")
    assert [t["value"] for t in s["lastRun"]["tiles"]] == [13, 7, 2, 2]
    assert out["empty"]["lastRun"]["mode"] == "empty" and out["empty"]["waiting"] == 0
    none, merged, ops = out["banners"]
    assert none["visible"] is False
    # the run already said the Serper problem: it is not repeated, the Gemini one is added
    assert merged["title"] == "انتبه:" and merged["text"].endswith("وكمان: Gemini لا يستجيب.") and merged["variant"] == "danger"
    assert ops["title"] == "رصيد Serper انتهى أو المفتاح مرفوض:" and ops["variant"] == "danger"


@NEEDS_NODE
def test_home_follows_the_badge_count_live():
    out = _node(_js(COMMON_JS, HOME_JS) + HARNESS + r"""
lives = [{ status: 'success', batch: { phase: 'review', ready_for_review: 5, run: {} }, run: null },
         { status: 'success', batch: { phase: 'review', ready_for_review: 4, run: {} }, run: null }];
const ctl = LaqtaHomePage.createController(deps);
(async () => {
    await ctl.poll();
    await ctl.loadOverview(false);
    await ctl.poll();
    console.log(JSON.stringify({ waiting: views.live.map(v => v.waiting),
                                 overview: fetchLog.filter(f => f.url.startsWith('/api/overview')).map(f => f.url) }));
})();
""")
    # the live review count is re-applied after the slower overview read, and follows every poll
    assert out["waiting"] == [5, 5, 4]
    assert out["overview"] == ["/api/overview"]


# ---------------------------------------------------------------------------
# The Laravel app through its HTTP kernel, on the MariaDB test database with a stub bridge
# ---------------------------------------------------------------------------

def _fake_secret(tag: str) -> str:
    """A recognisable stand-in for a stored key ('SECRET-GEMINI-<6 hex>'), built when the tests run so the source
    holds no key-shaped literal for secret scanners to report."""
    return f"SECRET-{tag}-{hashlib.sha256(tag.encode()).hexdigest()[:6]}"


SECRETS = {"gemini_api_key": _fake_secret("GEMINI"),
           "serper_api_key": _fake_secret("SERPER"),
           "photoroom_api_key": _fake_secret("PHOTO"),
           "cloudinary_api_secret": _fake_secret("CLOUD"),
           "proxy_url": "http://user:SECRET-PROXY-pass@proxy.local:8080"}
RUN_ID = "lqtestrun0001"
T0 = datetime(2026, 9, 30, 9, 40, tzinfo=timezone.utc)
SEARCHED = {3: 0, 4: 30, 5: 60, 6: 90, 50: 120}
OPS_HEALTH = {"status": "success", "windows": {"24h": {"searches": 4, "cost_usd": {"total": 0.02}},
                                              "7d": {"searches": 10, "cost_usd": {"total": 0.05}}},
              "alerts": [{"code": "SERPER_CREDIT", "message": "رصيد Serper انتهى أو المفتاح مرفوض", "searches": 3,
                          "detail": "quota / 401 / 403"}],
              "prices": {"serper_per_query": 0.001, "gemini_per_call": 0.001}}


def _sql(db, statement, params=()):
    conn = db.get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute(statement, params)
        rows = cur.fetchall()
        conn.commit()
        return rows
    finally:
        conn.close()


def _wipe(db):
    _sql(db, "DELETE FROM automation_queue")
    _sql(db, "UPDATE automation_state SET status = 'idle', stop_requested = 0, pause_requested = 0, run_id = NULL, "
             "notice = NULL, current_product_name = '', total_items = 0, processed_items = 0, success_count = 0, "
             "failed_count = 0 WHERE `key` = 'active_session'")
    for key in list(SECRETS) + ["auto_publish_enabled", "auto_publish_brands"]:
        _sql(db, "DELETE FROM system_settings WHERE `key` = %s", (key,))


@pytest.fixture
def app_env(mariadb_or_skip, tmp_path):
    if PHP is None or not VENDOR_AUTOLOAD.exists():
        pytest.skip("php or dashboard/vendor is not installed")
    if (ROOT / "temp" / "pipeline.lock").exists():
        pytest.skip("a pipeline lock exists in this checkout (a run is going); the snapshot would describe it")
    db = mariadb_or_skip
    _wipe(db)
    for row, (status, code, decision, held) in QUEUE.items():
        at = (T0 + timedelta(seconds=SEARCHED[row])).isoformat(timespec="seconds") if row in SEARCHED else None
        trace = json.dumps({"outcome": _outcome(("ok", "ok"), (), 1, decision, at)}) if decision or at else None
        name = next((p["product_name"] for p in SHEET if p["row_number"] == row), f"Orphan {row}")
        _sql(db, "INSERT INTO automation_queue (`row_number`, barcode, product_name, brand, search_query, status, "
                 "failure_code, trace_json, sku_key, run_id, lease_until) VALUES (%s, '', %s, '', 'q', %s, %s, %s, %s, "
                 "%s, " + ("NOW() + INTERVAL 10 MINUTE" if held else "NULL") + ")",
             (row, name, status, code, trace, f"lqt-{row}", RUN_ID if row not in (10,) else None))
    _sql(db, "UPDATE automation_state SET status = 'curation_pending', run_id = %s, notice = %s WHERE `key` = 'active_session'",
         (RUN_ID, "VERIFIER_NOT_CONFIGURED: no Gemini key, every result goes to human review"))
    for key, value in list(SECRETS.items()) + [("auto_publish_enabled", "true"), ("auto_publish_brands", "Almarai, Masafi")]:
        _sql(db, "INSERT INTO system_settings (`key`, `value`) VALUES (%s, %s) ON DUPLICATE KEY UPDATE `value` = VALUES(`value`)",
             (key, value))

    import local_cache_db
    stats = dict({"status": "success"}, **local_cache_db.review_stats([]))
    fixture = {"get_products": {"status": "success", "products": SHEET}, "review_stats": stats, "ops_health": OPS_HEALTH}
    calls = tmp_path / "calls.txt"
    stub = tmp_path / "stub_bridge.py"
    stub.write_text(
        "import json, os, sys\n"
        f"FIXTURE = json.loads({json.dumps(json.dumps(fixture, ensure_ascii=False))})\n"
        "action = sys.argv[1] if len(sys.argv) > 1 else ''\n"
        f"open({str(calls)!r}, 'a').write(action + '\\n')\n"
        "if os.environ.get('LQ_STUB_MODE') == 'down':\n"
        "    print(json.dumps({'status': 'failed', 'error': 'stub bridge is down'}))\n"
        "else:\n"
        "    print(json.dumps(FIXTURE.get(action, {'status': 'error', 'error': 'unexpected action'}), ensure_ascii=False))\n",
        encoding="utf-8")
    compiled = tmp_path / "views"
    compiled.mkdir()
    env = dict(os.environ)
    env.update({
        "APP_ENV": "testing", "APP_KEY": "base64:" + "A" * 43 + "=", "APP_DEBUG": "true",
        "SESSION_DRIVER": "array", "CACHE_STORE": "array", "LOG_CHANNEL": "stderr", "VIEW_COMPILED_PATH": str(compiled),
        "DB_CONNECTION": "mariadb", "DB_HOST": os.getenv("DB_HOST", "127.0.0.1"), "DB_PORT": os.getenv("DB_PORT", "3306"),
        "DB_DATABASE": os.environ["DB_DATABASE"], "DB_USERNAME": os.getenv("DB_USERNAME", "root"),
        "DB_PASSWORD": os.getenv("DB_PASSWORD", ""), "CLI_BRIDGE_PATH": str(stub), "PYTHON_PATH": sys.executable,
        "LAQTA_OWNER_NAME": "سارة <b>",
    })
    yield {"env": env, "calls": calls, "db": db}
    _wipe(db)


def _kernel(env, paths):
    dash = str(DASH).replace("\\", "/")
    script = f"""<?php
require '{dash}/vendor/autoload.php';
$app = require '{dash}/bootstrap/app.php';
$kernel = $app->make(Illuminate\\Contracts\\Http\\Kernel::class);
$out = [];
foreach (json_decode($argv[1], true) as $path) {{
    $response = $kernel->handle(Illuminate\\Http\\Request::create($path, 'GET', [], [], [], ['HTTP_ACCEPT' => 'text/html,application/json']));
    $out[$path] = ['status' => $response->getStatusCode(), 'location' => $response->headers->get('Location'),
                   'body' => $response->getContent()];
}}
echo json_encode($out, JSON_UNESCAPED_UNICODE);
"""
    with tempfile.NamedTemporaryFile("w", suffix=".php", delete=False, encoding="utf-8") as fh:
        fh.write(script)
        path = fh.name
    try:
        result = subprocess.run([PHP, path, json.dumps(paths)], cwd=DASH, env=env, capture_output=True, text=True,
                                timeout=240, encoding="utf-8")
    finally:
        os.unlink(path)
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-3000:]
    return json.loads(result.stdout)


PATHS = ["/batch-automation?tab=review", "/", "/batch-automation", "/api/batch-status", "/api/run/live", "/api/overview",
         "/api/run/plan?scope=all", "/api/run/plan?scope=rows&rows=3-5", "/api/run/plan?scope=brand&brand=almarai",
         "/api/run/plan?scope=rows&rows=9-3", "/api/run/plan?scope[]=rows&rows[]=1"]


@pytest.fixture
def served(app_env):
    return _kernel(app_env["env"], PATHS), app_env


def test_routes_and_the_old_review_tab_redirect(served):
    out, _ = served
    redirect = out["/batch-automation?tab=review"]
    assert redirect["status"] == 302 and redirect["location"].endswith("/catalog?mode=bulk")
    for path in ("/", "/batch-automation"):
        assert out[path]["status"] == 200, out[path]["body"][:2000]
    home, run = out["/"]["body"], out["/batch-automation"]["body"]
    assert "data-home-page" in home and 'class="lq-sidebar"' in home and "<title>الرئيسية · لقطة</title>" in home
    assert "data-run-page" in run and "<title>التشغيل · لقطة</title>" in run
    assert re.search(r'<a href="[^"]+/batch-automation"[^>]*aria-current="page"', run)
    for leftover in ("{{", "{!!", "@props", "<x-lq", "@json"):
        assert leftover not in home and leftover not in run, leftover
    # the owner's name is escaped; there is no user chip
    assert "مسا الخير يا سارة &lt;b&gt;" in home or "صباح الخير يا سارة &lt;b&gt;" in home
    assert 'data-owner="سارة &lt;b&gt;"' in home


def test_every_number_has_one_source(served):
    out, _ = served
    status = json.loads(out["/api/batch-status"]["body"])
    live = json.loads(out["/api/run/live"]["body"])
    overview = json.loads(out["/api/overview"]["body"])
    assert live["status"] == "success" and overview["status"] == "success"

    waiting = status["ready_for_review"]
    assert waiting == 3                                                      # rows 3, 4 and the orphan row 50
    assert live["batch"]["ready_for_review"] == waiting
    assert overview["kpis"]["waiting"]["value"] == waiting == overview["review"]["waiting"]
    assert overview["review"]["proposed"] + overview["review"]["none"] == waiting
    # the page and the sidebar badge are rendered with the same number
    home = out["/"]["body"]
    assert re.search(r'data-home="review-count">3<', home)
    assert re.search(r'data-lq-review-count\s*>3<', home)
    assert re.search(r'data-kpi="waiting"[^>]*>.*?data-kpi-value>3<', home, re.DOTALL)

    sheet = overview["sheet"]
    assert sheet["status"] == "ok" and sheet["counts"] == FUNNEL
    assert sum(s["value"] for s in sheet["stages"]) == sheet["total"] == len(SHEET)
    assert sheet["counts"]["review"] + sheet["orphans"] == waiting
    for key in ("published", "not_found", "failed"):
        assert overview["kpis"][key]["value"] == sheet["counts"][key]

    run = live["run"]
    assert run["run_id"] == RUN_ID == status["run"]["run_id"]
    assert run["total"] == status["run"]["total"] and run["processed"] == status["run"]["processed"]
    assert sum(run["counts"].values()) == run["total"]
    assert run["rows_label"] == "3–7، 11–12، 50"
    assert live["batch"]["phase"] == status["phase"] and live["batch"]["alert"] == status["alert"]
    # being searched first, then newest first (row 7 has no search time: its row update, just now, stands in)
    assert [r["row"] for r in live["recent"]] == [12, 7, 50, 6, 5, 4, 3]
    assert "11" not in [str(r["row"]) for r in live["recent"]]              # still waiting: not a result
    assert all(r["href"] == f"/catalog?row={r['row']}" for r in live["recent"])

    assert overview["cost"] == {"status": "ok", "week_usd": 0.05, "searches": 10}
    assert overview["alerts"][0]["title"] == "رصيد Serper انتهى أو المفتاح مرفوض"
    assert overview["readiness"]["status"] == "ok" and overview["readiness"]["brands"] == []


def test_plan_endpoint(served):
    out, env = served
    plan = json.loads(out["/api/run/plan?scope=all"]["body"])
    assert (plan["to_search"], plan["skipped_final"], plan["kept_waiting"], plan["total"]) == (5, 1, 2, 5)
    est = plan["estimate"]
    assert est["cost_basis"] == "history" and est["cost_per_product"] == 0.005 and est["cost_usd"] == 0.025
    assert est["time_basis"] == "history" and est["seconds_per_product"] == 30.0 and est["seconds"] == 150
    assert json.loads(out["/api/run/plan?scope=rows&rows=3-5"]["body"])["total"] == 3
    assert json.loads(out["/api/run/plan?scope=brand&brand=almarai"]["body"])["in_scope"] == 3
    assert "مقلوب" in json.loads(out["/api/run/plan?scope=rows&rows=9-3"]["body"])["error"]
    odd = out["/api/run/plan?scope[]=rows&rows[]=1"]                          # array parameters: the whole sheet
    assert odd["status"] == 200 and json.loads(odd["body"])["scope"] == "all"
    # read-only: only the three read actions of the bridge were called
    assert set(env["calls"].read_text().split()) <= {"get_products", "review_stats", "ops_health"}


def test_auto_publish_state_on_the_run_page(served):
    out, _ = served
    run = out["/batch-automation"]["body"]
    assert "النشر الآلي شغّال لـ Almarai، Masafi" in run
    assert 'href="http://localhost/settings?tab=auto-publish"' in run


def test_no_stored_secret_reaches_a_page_or_an_api(served):
    out, _ = served
    for path, response in out.items():
        for value in SECRETS.values():
            assert value not in response["body"], (path, value)
        assert "SECRET-" not in response["body"], path


def test_unavailable_sources_are_said_never_zero(app_env):
    env = dict(app_env["env"], LQ_STUB_MODE="down")
    out = _kernel(env, ["/api/overview", "/api/run/plan?scope=all"])
    overview = json.loads(out["/api/overview"]["body"])
    assert overview["sheet"]["status"] == "error" and "الشيت" in overview["sheet"]["message"]
    assert [overview["kpis"][k]["value"] for k in ("published", "not_found", "failed")] == [None, None, None]
    assert overview["kpis"]["waiting"]["value"] == 3                         # the database still answers
    assert overview["readiness"]["status"] == "error" and overview["cost"]["status"] == "error"
    plan = out["/api/run/plan?scope=all"]
    assert plan["status"] == 503 and json.loads(plan["body"])["error"] == "sheet_unavailable"

    db_down = dict(app_env["env"], DB_PORT="1")                              # nothing listens on port 1
    out = _kernel(db_down, ["/api/run/live", "/api/overview", "/batch-automation", "/"])
    live = out["/api/run/live"]
    assert live["status"] == 503 and json.loads(live["body"])["error"] == "database_unavailable"
    overview = json.loads(out["/api/overview"]["body"])
    assert overview["review"]["status"] == "error" and overview["kpis"]["waiting"]["value"] is None
    assert out["/batch-automation"]["status"] == 200 and out["/"]["status"] == 200
    island = re.search(r'id="lq-home-initial">(.*?)</script>', out["/"]["body"], re.DOTALL).group(1)
    assert json.loads(island)["error"] == "database_unavailable"            # the embedded snapshot says why
    assert "قاعدة البيانات مش متاحة" in json.loads(island)["message"]
    assert re.search(r'data-home="review-count">—<', out["/"]["body"])      # «—», not 0


# ---------------------------------------------------------------------------
# Review fixes (wp/p2-run-review): each test fails on the code before its fix
# ---------------------------------------------------------------------------

@NEEDS_NODE
def test_after_a_reset_the_summary_is_the_last_run_not_the_one_before():
    """«إصلاح تشغيل عالق» clears automation_state.run_id, so the last run is no longer the state's run. With no newer
    run the card must not say «التشغيل اللي قبله» (the run before what?) nor drop its time, rows and cost."""
    reset = {"status": "success", "batch": _batch("review", ready_for_review=4, run={"run_id": None, "total": 0, "processed": 0}),
             "run": dict(RUN_CURRENT, current=False, is_state_run=False, processed=40), "recent": []}
    stopped = dict(reset, run=dict(RUN_CURRENT, current=False, is_state_run=False))
    out = _node(_js(COMMON_JS, RUN_JS) + f"""
const out = [{json.dumps(reset, ensure_ascii=False)}, {json.dumps(stopped, ensure_ascii=False)}]
    .map(s => LaqtaRunPage.describeLive(s, 1790000000, 12));
console.log(JSON.stringify(out));
""")
    done, stop = out
    assert done["state"] == "finished" and done["finished"]["title"] == "خلص آخر تشغيل"
    assert done["startFailed"] is False and done["message"] == ""
    assert done["meta"].endswith("الصفوف 62–101 · 8 دقائق · $0.11")
    assert stop["state"] == "stopped" and stop["finished"]["title"] == "آخر تشغيل وقف قبل ما يخلص: 24 من 40"
    assert "التشغيل اللي قبله" not in json.dumps(out, ensure_ascii=False)


@NEEDS_NODE
def test_run_progress_bar_fills_only_the_processed_rows():
    """«N من M» counts processed rows (QueueStats::runTotals). A row back in the queue after an outage is pending again;
    filling it made the bar show 60% under «2 من 5» while the Home card and the sidebar showed 40%."""
    snap = {"status": "success",
            "batch": _batch("running", is_running=True,
                            run={"run_id": "r2", "total": 5, "processed": 2, "ready_for_review": 1, "completed": 0,
                                 "failed": 1, "pending": 2, "processing": 1}),
            "run": dict(RUN_CURRENT, total=5, processed=2, counts={"proposed": 1, "none": 0, "not_found": 0, "error": 1,
                                                                     "requeued": 1, "approved": 0, "searching": 1,
                                                                     "waiting": 1}),
            "recent": []}
    out = _node(_js(COMMON_JS, RUN_JS, HOME_JS) + f"""
const s = {json.dumps(snap, ensure_ascii=False)};
const run = LaqtaRunPage.describeLive(s, 1790000000, 12).progress;
const home = LaqtaHomePage.describeHomeLive(s, 1790000000).lastRun;
console.log(JSON.stringify({{ run, home }}));
""")
    run = out["run"]
    assert run["countsText"].startswith("2 من 5") and run["percent"] == 40
    assert sum(s["pct"] for s in run["segments"]) == pytest.approx(40)       # the fill is «2 من 5», not 60%
    assert sum(t["value"] for t in run["legend"]) == 2
    assert {t["label"]: t["value"] for t in run["legend"]}["أعطال مؤقتة"] == 1
    assert out["home"]["percent"] == run["percent"]                          # the same run on both pages


@NEEDS_NODE
def test_run_controls_toast_plain_arabic_only():
    """pauseBatch / resumeBatch answer "Automation paused." / "Automation resumed."; the page showed that English as
    the toast. Arabic server messages (run_control's stop / reset) are still shown as they are."""
    out = _node(_js(COMMON_JS, RUN_JS) + HARNESS + r"""
lives = [{ status: 'success', batch: { phase: 'running', run: { total: 5, processed: 1 }, ready_for_review: 0 }, run: null, recent: [] }];
const answers = {
    '/api/batch/pause': { ok: true, status: 200, data: { status: 'success', message: 'Automation paused.' } },
    '/api/batch/resume': { ok: true, status: 200, data: { status: 'success', message: 'Automation resumed.' } },
    '/api/stop-batch': { ok: true, status: 200, data: { status: 'success', message: 'تم إيقاف التشغيل. لم يُحذف أي صف.' } },
    '/api/batch/reset': { ok: false, status: 500, data: { error: 'SQLSTATE[HY000] Connection refused' } }
};
deps.fetchJson = (url, opts) => answers[url] ? (fetchLog.push({ url }), Promise.resolve(answers[url])) : fakeFetch(url, opts);
const ctl = LaqtaRunPage.createController(deps);
(async () => {
    await ctl.pause(); await ctl.resume(); await ctl.stop(); await ctl.reset();
    console.log(JSON.stringify({ toasts }));
})();
""")
    # review fix C5: Stop now waits up to 90 s for the worker to finish its products, so the page says so first
    assert out["toasts"] == [["انوقف التشغيل مؤقتاً.", "success"], ["رجع التشغيل يشتغل.", "success"],
                             ["عم نوقف التشغيل: العامل بيكمّل المنتجات الجارية (حتى دقيقة ونص).", "info"],
                             ["تم إيقاف التشغيل. لم يُحذف أي صف.", "success"], ["ما قدرنا نصلّح التشغيل.", "danger"]]


@NEEDS_NODE
def test_end_of_run_toast_says_stopped_or_finished_on_both_pages():
    finished = dict(RUN_CURRENT, current=False, processed=40)
    stopped = dict(RUN_CURRENT, current=False, processed=24)

    def lives(run):
        return [{"status": "success", "batch": _batch("running", is_running=True), "run": RUN_CURRENT, "recent": []},
                {"status": "success", "batch": _batch("review", ready_for_review=3), "run": run, "recent": []}]

    out = _node(_js(COMMON_JS, RUN_JS, HOME_JS) + HARNESS + f"""
const cases = {json.dumps({"finished": lives(finished), "stopped": lives(stopped)}, ensure_ascii=False)};
(async () => {{
    const out = {{}};
    for (const [name, snaps] of Object.entries(cases)) {{
        for (const page of ['run', 'home']) {{
            toasts.length = 0; lives = snaps; liveIndex = 0;
            const ctl = (page === 'run' ? LaqtaRunPage : LaqtaHomePage).createController(deps);
            await ctl.poll(); await ctl.poll(); await tick(); await tick();
            out[name + '_' + page] = toasts.slice();
        }}
    }}
    console.log(JSON.stringify(out));
}})();
""")
    assert out["finished_run"] == [["خلص التشغيل: 3 منتجات بانتظار مراجعتك.", "success"]]
    assert out["finished_home"] == [["خلص التشغيل.", "success"]]
    assert out["stopped_run"] == [["وقف التشغيل قبل ما يخلص: 3 منتجات بانتظار مراجعتك.", "warning"]]
    assert out["stopped_home"] == [["وقف التشغيل قبل ما يخلص.", "warning"]]


@NEEDS_NODE
def test_start_runs_the_scope_on_screen_not_the_one_typed_before():
    """The rows field reaches the form 400 ms after typing stops. «ابدأ التشغيل» pressed sooner started the rows typed
    before (e.g. 6 while the field shows 60). start(latest) reads the plan for what is on screen, then starts."""
    out = _node(_js(COMMON_JS, RUN_JS) + HARNESS + r"""
lives = [{ status: 'success', batch: { phase: 'idle', run: {}, ready_for_review: 0 }, run: null, recent: [] }];
deps.fetchJson = (url, opts) => {
    if (url.startsWith('/api/run/plan') && url.includes('rows=9-3')) {
        fetchLog.push({ url, method: 'GET' });
        return Promise.resolve({ ok: true, status: 200, data: { status: 'success', error: 'النطاق 9-3 مقلوب' } });
    }
    return fakeFetch(url, opts);
};
const ctl = LaqtaRunPage.createController(deps);
(async () => {
    await ctl.setForm({ scope: 'rows', rows: '6' });
    const started = await ctl.start({ rows: '60' });
    const order = fetchLog.map(f => f.method + ' ' + f.url);
    const body = fetchLog.filter(f => f.url === '/api/run-all').map(f => f.body);
    const invalid = await ctl.start({ rows: '9-3' });
    console.log(JSON.stringify({ started, order, body, invalid, posts: fetchLog.filter(f => f.url === '/api/run-all').length,
                                 error: last(views.start).error }));
})();
""")
    assert out["started"] is True and out["body"] == [{"row_filter": "60", "brand_filter": "", "forceOverwrite": False,
                                                       "skipCache": False}]
    plan60 = out["order"].index("GET /api/run/plan?scope=rows&rows=60")
    assert plan60 < out["order"].index("POST /api/run-all")                  # checked like any other scope first
    assert out["invalid"] is False and out["posts"] == 1 and out["error"] == "النطاق 9-3 مقلوب"


# A small DOM for public/js/run.js mount(): elements are found by their data-run hook and created on first use.
RUN_DOM = r"""
class El {
    constructor(tag) {
        this.tagName = String(tag).toUpperCase(); this.children = []; this.parentNode = null; this.attrs = {};
        this.listeners = {}; this.style = {}; this._text = ''; this._value = ''; this.disabled = false; this.checked = false;
        this.className = '';
        const set = new Set();
        this.classList = { add: c => set.add(c), remove: c => set.delete(c), contains: c => set.has(c),
                           toggle: (c, on) => { if (on === undefined ? !set.has(c) : on) set.add(c); else set.delete(c); } };
    }
    get firstChild() { return this.children[0] || null; }
    appendChild(c) { c.parentNode = this; this.children.push(c); return c; }
    removeChild(c) { this.children.splice(this.children.indexOf(c), 1); c.parentNode = null; return c; }
    get textContent() { return this._text + this.children.map(c => c.textContent).join(''); }
    set textContent(v) { this._text = String(v); this.children = []; }
    setAttribute(k, v) { this.attrs[k] = String(v); }
    getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
    removeAttribute(k) { delete this.attrs[k]; }
    get hidden() { return 'hidden' in this.attrs; }
    get options() { return this.children.filter(c => c.tagName === 'OPTION'); }
    get value() {
        if (this.tagName !== 'SELECT') return this._value;
        const hit = this.options.find(o => o._value === this._value);
        return hit ? hit._value : (this.options[0] ? this.options[0]._value : '');
    }
    set value(v) { this._value = String(v); }
    addEventListener(t, fn) { (this.listeners[t] = this.listeners[t] || []).push(fn); }
    fire(t, detail) { (this.listeners[t] || []).forEach(fn => fn({ detail, preventDefault() {}, stopPropagation() {} })); }
}
const hooks = {};
const page = new El('div');
page.querySelector = (sel) => {
    const m = sel.match(/^\[data-run="([a-z-]+)"\]$/);
    if (!m) return null;
    if (!hooks[m[1]]) hooks[m[1]] = new El(m[1] === 'brand' ? 'select' : (m[1] === 'rows' ? 'input' : 'div'));
    return hooks[m[1]];
};
const island = new El('script');
globalThis.document = {
    readyState: 'loading', hidden: false,
    querySelector: (sel) => sel === '[data-run-page]' ? page : null,
    getElementById: (id) => id === 'lq-run-initial' ? island : null,
    createElement: (t) => new El(t),
    createTextNode: (t) => { const n = new El('#text'); n._text = String(t); return n; },
    addEventListener() {}
};
const net = [];
let planReply = () => ({ status: 200, body: { status: 'success', total: 1, skipped_final: 0, estimate: {}, brands: [] } });
globalThis.fetch = (url, init) => {
    net.push({ url, method: init.method, body: init.body ? JSON.parse(init.body) : null });
    let reply = { status: 200, body: { status: 'success' } };
    if (url.startsWith('/api/run/plan')) reply = planReply(url);
    if (url === '/api/run/live') reply = { status: 200, body: { status: 'success', batch: { phase: 'idle', run: {}, ready_for_review: 0 }, run: null, recent: [] } };
    return Promise.resolve({ ok: reply.status < 400, status: reply.status, json: async () => reply.body });
};
const domToasts = [];
globalThis.Laqta = { toast: (m, o) => domToasts.push([m, o.variant]) };
globalThis.confirm = () => true;
const wait = (ms) => new Promise(r => setTimeout(r, ms));
"""


def _run_dom(script: str):
    return _node("globalThis.window = globalThis;\n" + RUN_DOM + read(COMMON_JS) + "\n" + read(RUN_JS) + "\n" + script)


@NEEDS_NODE
def test_run_page_start_button_uses_the_rows_field_as_shown():
    out = _run_dom(r"""
island.textContent = 'null';
LaqtaRunPage.mount(document);
(async () => {
    page.fire('lq:change', { value: 'rows' });
    hooks.rows.value = '6'; hooks.rows.fire('input');
    await wait(450);                                   // the plan for «6» arrives
    hooks.rows.value = '60'; hooks.rows.fire('input');
    hooks.start.fire('click');                         // pressed right after typing the 0
    await wait(600);
    console.log(JSON.stringify({ runs: net.filter(n => n.url === '/api/run-all').map(n => n.body.row_filter),
                                 plans: net.filter(n => n.url.startsWith('/api/run/plan')).map(n => n.url) }));
    process.exit(0);
})();
""")
    assert out["runs"] == ["60"]
    assert out["plans"].count("/api/run/plan?scope=rows&rows=60") == 1       # read once (no second debounce read)


@NEEDS_NODE
def test_brand_select_never_stays_on_loading():
    """When the sheet cannot be read the brand select said «لحظة، عم نقرأ الماركات…» forever, and a sheet without any
    brand left it the same way. It now says what happened, and fills once a plan brings the brands."""
    out = _run_dom(r"""
island.textContent = 'null';
const select = page.querySelector('[data-run="brand"]');
const loading = new El('option'); loading.value = ''; loading.textContent = 'لحظة، عم نقرأ الماركات…';
select.appendChild(loading);
const replies = [
    () => ({ status: 503, body: { status: 'error', error: 'sheet_unavailable', message: 'ما قدرنا نقرأ الشيت هلق' } }),
    () => ({ status: 200, body: { status: 'success', error: 'اختار الماركة اللي بدك تشغّلها.',
                                   brands: [{ brand: 'Almarai', label: 'Almarai (3 منتجات)' }] } }),
    () => ({ status: 200, body: { status: 'success', total: 0, estimate: {}, brands: [] } })
];
planReply = () => replies.shift()();
const texts = () => select.options.map(o => o.textContent);
LaqtaRunPage.mount(document);
(async () => {
    await wait(50);
    const down = texts();
    page.fire('lq:change', { value: 'brand' });
    await wait(50);
    const back = texts();
    page.fire('lq:change', { value: 'all' });
    await wait(50);
    console.log(JSON.stringify({ down, back, empty: texts() }));
    process.exit(0);
})();
""")
    assert out["down"] == ["ما قدرنا نقرأ الماركات من الشيت هلق"]
    assert out["back"] == ["اختار ماركة…", "Almarai (3 منتجات)"]             # even when the plan itself is invalid
    assert out["empty"] == ["ما في ماركات بالشيت"]


@NEEDS_PHP
def test_brand_select_counts_are_what_the_run_takes():
    """The brand select said «Almarai (منتجين)» while a run with Almarai takes 3 products (main.py's substring filter
    also takes «Almarai Kids») and «قبل ما تبدأ» said 3. Every count now equals the plan's in_scope for that brand."""
    out = _php(f"$sheet = {php_value(SHEET)}; $queue = {php_value(_queue_by_row())};\n"
               "foreach (RunController::brandCounts($sheet) as $b) {"
               " $out[] = [$b['brand'], $b['count'], RunController::buildPlan($sheet, $queue, 'brand', $b['brand'], '', false)['in_scope']]; }")
    assert out and all(count == in_scope for _, count, in_scope in out), out
    assert ["Almarai", 3, 3] in out and ["Almarai Kids", 1, 1] in out


def test_refresh_reads_past_every_server_cache(app_env):
    """The end-of-run refresh (?refresh=1 from both pages) must reach the server: the review stats and ops_health are
    read again although a minute-long cache holds them, and the sheet is read with sheetRows($refresh)."""
    paths = ["/api/overview", "/api/overview?again", "/api/overview?refresh=1"]
    _kernel(app_env["env"], paths)
    calls = app_env["calls"].read_text().split()
    assert calls.count("review_stats") == 2 and calls.count("ops_health") == 2     # cached once, then read again
    run, overview = read(CONTROLLERS / "RunController.php"), read(CONTROLLERS / "OverviewController.php")
    assert "ProductController::sheetRows($request->boolean('refresh'), $error)" in run
    assert "ProductController::sheetRows($refresh, $error)" in overview
    assert "self::reviewStats($refresh)" in overview and "self::opsHealth($refresh)" in overview


def test_last_run_is_the_one_that_searched_last_not_the_one_reviewed_last(app_env):
    """Without a run id in automation_state (after a reset, or while a start that failed before its first search is
    shown) the last run was the run of the most recently *updated* queue row. Approving or rejecting a row bumps its
    updated_at, so reviewing an old product turned an old run into «آخر تشغيل» on both pages."""
    db = app_env["db"]
    old = json.dumps({"outcome": _outcome(("ok",), (), 1, "REVIEW_PRESELECTED", (T0 - timedelta(days=3)).isoformat())})
    _sql(db, "INSERT INTO automation_queue (`row_number`, barcode, product_name, brand, search_query, status, trace_json, "
             "sku_key, run_id, updated_at) VALUES (70, '', 'Old Product', '', 'q', 'completed', %s, 'lqt-70', "
             "'lqoldrun0001', NOW() + INTERVAL 1 MINUTE)", (old,))       # approved by a reviewer just now
    _sql(db, "UPDATE automation_state SET run_id = NULL WHERE `key` = 'active_session'")
    out = _kernel(app_env["env"], ["/api/run/live"])
    live = json.loads(out["/api/run/live"]["body"])
    assert live["run"]["run_id"] == RUN_ID and live["run"]["is_state_run"] is False


@NEEDS_NODE
def test_a_searching_row_without_a_worker_is_shown_stuck():
    """With no live worker (an inactive phase) a row left in 'processing' was listed as «عم ندوّر · هلق» right under
    the «التشغيل عالق» box. It now reads «عالق», with the reason in its tooltip."""
    row = {"row": 9, "name": "Water", "kind": "searching", "label": "عم ندوّر", "chip": "none", "at": None,
           "href": "/catalog?row=9"}
    stuck = {"status": "success", "batch": _batch("review", stuck="منتج واحد عالق"), "run": dict(RUN_CURRENT, current=False),
             "recent": [row]}
    running = {"status": "success", "batch": _batch("running", is_running=True), "run": RUN_CURRENT, "recent": [row]}
    out = _node(_js(COMMON_JS, RUN_JS) + f"""
console.log(JSON.stringify([{json.dumps(stuck, ensure_ascii=False)}, {json.dumps(running, ensure_ascii=False)}]
    .map(s => LaqtaRunPage.describeLive(s, 1790000000, 12).recent[0])));
""")
    assert out[0]["label"] == "عالق" and out[0]["chip"] == "warning" and out[0]["ago"] == "" and out[0]["why"]
    assert out[1]["label"] == "عم ندوّر" and out[1]["ago"] == "هلق"


@NEEDS_PHP
def test_not_found_note_before_any_search():
    """«كل اللي انبحث عنه انلقى» under a 0 was shown before anything had been searched."""
    unsearched = [{"row_number": 2, "brand": "A", "existing_image_link": ""},
                  {"row_number": 3, "brand": "B", "existing_image_link": ""}]
    out = _php(f"$f = OverviewController::funnel({php_value(unsearched)}, []);\n"
               "$out = OverviewController::kpis(['status' => 'ok', 'waiting' => 0, 'proposed' => 0], ['status' => 'ok'] + $f);")
    assert out["not_found"]["value"] == 0 and out["not_found"]["note"] == "لسا ما انبحث عن شي"
