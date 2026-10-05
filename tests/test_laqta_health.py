"""Laqta Health (الصحة والتكلفة) and Settings (الإعدادات) pages, package p2-health.

- Structure: both pages extend layouts.laqta, keep their script in public/js and their style in public/css/pages,
  use only hooks, classes and tokens that exist, and show no raw decision/failure codes and no user chip.
- One vocabulary, one source: the PHP helpers behind the service cards, the key states and the auto-publish table run
  under the PHP CLI and are compared with the page script and with the Python code they mirror
  (local_cache_db.review_stats, config's AUTO_PUBLISH_BRANDS parsing, main.SUPPORTED_BG_METHODS).
- The page scripts run under node: view functions and the health controller with recorded fetches.
- The Laravel app runs through its HTTP kernel against the MariaDB test database with a stub cli_bridge: routes and
  redirects, every tab, section saves that touch only their own keys, the auto-publish rules, no stored secret in any
  page or API, and truthful unavailable states. Those are skipped when php, dashboard/vendor or MariaDB is missing.
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
from blade_scripts import inline_scripts

ROOT = Path(__file__).resolve().parents[1]
DASH = ROOT / "dashboard"
VIEWS = DASH / "resources" / "views"
HEALTH = VIEWS / "dashboard" / "diagnostics.blade.php"
SETTINGS = VIEWS / "dashboard" / "settings.blade.php"
PARTIALS = sorted((VIEWS / "settings").glob("*.blade.php"))
JS = DASH / "public" / "js"
HEALTH_JS, SETTINGS_JS = JS / "health.js", JS / "settings.js"
HEALTH_CSS = DASH / "public" / "css" / "pages" / "health.css"
SETTINGS_CSS = DASH / "public" / "css" / "pages" / "settings.css"
LAQTA_CSS = DASH / "public" / "css" / "laqta.css"
CONTROLLERS = DASH / "app" / "Http" / "Controllers"
HEALTH_PHP, SETTINGS_PHP = CONTROLLERS / "HealthController.php", CONTROLLERS / "SettingsController.php"
ROUTES = DASH / "routes" / "web.php"
VENDOR_AUTOLOAD = DASH / "vendor" / "autoload.php"

PHP = shutil.which("php")
NODE = shutil.which("node")
NEEDS_PHP = pytest.mark.skipif(PHP is None, reason="php is not installed")
NEEDS_NODE = pytest.mark.skipif(NODE is None, reason="node is not installed")

VIEW_FILES = [HEALTH, SETTINGS] + PARTIALS
PAGE_FILES = VIEW_FILES + [HEALTH_JS, SETTINGS_JS, HEALTH_CSS, SETTINGS_CSS]
RAW_CODES = ["REVIEW_PRESELECTED", "REVIEW_UNSELECTED", "NO_RESULTS", "PROVIDER_DOWN", "ALL_CONFLICTED",
             "SERPER_CREDIT", "GEMINI_DOWN", "VERIFIER_DOWN", "AUTO_PUBLISH_ENABLED", "needs_reviews", "low_precision"]
LTR, PDI = "\u2066", "\u2069"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Structure
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path, nav, title, script", [(HEALTH, "health", "الصحة والتكلفة", "health.js"),
                                                       (SETTINGS, "settings", "الإعدادات", "settings.js")],
                         ids=["health", "settings"])
def test_pages_extend_the_laqta_layout(path, nav, title, script):
    text = read(path)
    assert "@extends('layouts.laqta')" in text and "layouts.layout'" not in text
    assert f"@section('title', '{title}')" in text and f"@section('lq_nav', '{nav}')" in text
    assert "@push('styles')" in text and "@push('scripts')" in text
    assert re.findall(r"asset\('js/([a-z-]+\.js)'\)", text) == [script]
    assert re.findall(r"asset\('css/pages/([a-z-]+\.css)'\)", text) == [script.replace(".js", ".css")]
    # no inline executable script and nothing from another host
    assert not inline_scripts(text)
    assert not re.search(r"(?:href|src)=\"(?:https?:)?//", text)


def test_settings_partials_and_the_old_page():
    assert {p.name for p in PARTIALS} == {"sheet.blade.php", "keys.blade.php", "auto_publish.blade.php",
                                          "processing.blade.php", "advanced.blade.php", "models.blade.php"}
    for path in PARTIALS:
        assert "<script" not in read(path), path.name
    assert not (VIEWS / "dashboard" / "active_learning.blade.php").exists()
    # the health page embeds only the saved check result, as escaped JSON data
    assert '<script type="application/json" id="lq-health-initial">@json($lastDiagnostics)</script>' in read(HEALTH)


def test_no_user_chip_and_no_raw_codes_as_text():
    for path in PAGE_FILES + [HEALTH_PHP, SETTINGS_PHP]:
        text = read(path)
        assert "أسامة" not in text and "مدير" not in text, path.name
    for path in VIEW_FILES:
        text = re.sub(r"\{\{--.*?--\}\}", "", read(path), flags=re.DOTALL)    # Blade comments never render
        for code in RAW_CODES:
            assert code not in text, (path.name, code)
    emoji = re.compile("[\U0001F300-\U0001FAFF☀-➿️]")
    for path in PAGE_FILES + [HEALTH_PHP, SETTINGS_PHP]:
        assert not emoji.findall(read(path)), path.name


def test_every_hook_the_scripts_use_exists_in_the_page():
    health_view, health_js = read(HEALTH), read(HEALTH_JS)
    used = set(re.findall(r"\$\('([a-z-]+)'\)", health_js))
    assert used and not used - set(re.findall(r'data-health="([a-z-]+)"', health_view)), sorted(used)
    for hook in ("data-service-state", "data-service-dot", "data-service-details", "data-service-details-text",
                 "data-log-tab", "data-health-page", 'id="lq-health-initial"'):
        assert hook in health_view, hook
    settings = "\n".join(read(p) for p in [SETTINGS] + PARTIALS)
    for attr in set(re.findall(r"\[(data-[a-z-]+)", read(SETTINGS_JS))):
        assert attr in settings, attr


CLASS_RE = re.compile(r"(?<![\w-])lq-[a-z0-9]+(?:(?:__|--|-)[a-z0-9]+)*")


def _css_classes(*paths):
    classes = set()
    for p in paths:
        css = re.sub(r"/\*.*?\*/", "", read(p), flags=re.DOTALL)
        classes |= set(re.findall(r"\.(lq-[a-z0-9]+(?:(?:__|--|-)[a-z0-9]+)*)", css))
    return classes


def test_classes_and_tokens_exist():
    defined = _css_classes(LAQTA_CSS, HEALTH_CSS, SETTINGS_CSS)
    ids = {"lq-health-initial", "lq-health-search-title", "lq-health-results-title", "lq-health-cost-title",
           "lq-health-reasons-title", "lq-health-log-title", "lq-health-log-body", "lq-settings-sheet-title",
           "lq-settings-keys-title", "lq-settings-ap-title", "lq-settings-processing-title",
           "lq-settings-advanced-title", "lq-settings-models-title", "lq-settings-sources-title", "lq-settings-speed-title", "lq-key-form", "lq-page-health", "lq-page-settings", "lq-health-spin"}
    # classes built at runtime: 'lq-tone--' + tone, 'lq-dot--' + tone, '...--' + tone
    runtime = {"lq-tone", "lq-dot", "lq-keys__state", "lq-settings-columns__item", "lq-settings-form__status",
               "lq-health-provider__problems", "lq-alert", "lq-toast", "lq-dot lq-dot", "lq-skeleton"}
    for path in VIEW_FILES + [HEALTH_JS, SETTINGS_JS]:
        used = {c.rstrip("-") for c in CLASS_RE.findall(read(path))}
        missing = sorted(c for c in used - ids - runtime if c not in defined)
        assert not missing, (path.name, missing)
    tokens = set(re.findall(r"(--lq-[a-z0-9-]+)\s*:", read(LAQTA_CSS)))
    for css in (HEALTH_CSS, SETTINGS_CSS):
        text = read(css)
        used = set(re.findall(r"var\((--lq-[a-z0-9-]+)", text))
        assert not used - tokens, sorted(used - tokens)
        assert not re.search(r"--lq-[a-z0-9-]+\s*:", text), "page CSS must not define tokens"
        for physical in ("margin-left", "margin-right", "padding-left", "padding-right", "border-left", "border-right"):
            assert physical not in text, physical
        assert not re.search(r"(?<![-\w])(left|right)\s*:", text), "use inset-inline-start/end"
        assert "@media (max-width: 980px)" in text and "@media (max-width: 560px)" in text


@NEEDS_NODE
@pytest.mark.parametrize("path", [HEALTH_JS, SETTINGS_JS], ids=lambda p: p.name)
def test_page_scripts_parse_and_stay_plain(path):
    result = subprocess.run([NODE, "--check", str(path)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    text = read(path)
    assert "innerHTML" not in text and "insertAdjacentHTML" not in text and "eval(" not in text
    assert "new Function" not in text and "require(" not in text and "import " not in text


def test_routes():
    routes = read(ROUTES)
    health, settings = "\\App\\Http\\Controllers\\HealthController", "\\App\\Http\\Controllers\\SettingsController"
    for line in (
        f"Route::get('/system-diagnostics', [{health}::class, 'page'])->name('dashboard.diagnostics');",
        f"Route::get('/settings', [{settings}::class, 'show'])->name('dashboard.settings');",
        f"Route::post('/settings', [{settings}::class, 'save'])->name('dashboard.save_settings');",
        "Route::get('/active-learning', [ProductController::class, 'activeLearning'])->name('dashboard.active_learning');",
        f"Route::get('/api/view-pipeline-log', [{health}::class, 'pipelineLog']);",
        f"Route::get('/api/view-laravel-log', [{health}::class, 'laravelLog']);",
        "Route::post('/api/system/run-diagnostics', [ProductController::class, 'runDiagnosticsJson']);",
        "Route::post('/api/sheet/preview', [ApiController::class, 'previewSheet']);",
        "Route::post('/api/sheet/save', [ApiController::class, 'saveSheetConfig']);",
    ):
        assert line in routes, line
    # the log closures (and their 404 for a missing file) are gone
    assert "Log file not found" not in routes and "function()" not in routes
    product = read(CONTROLLERS / "ProductController.php")
    assert "return redirect()->to(route('dashboard.settings') . '?tab=auto-publish');" in product
    # the old settings and diagnostics methods are gone; the routes point at SettingsController / HealthController
    for gone in ("function settings(", "function saveSettings(", "function systemDiagnostics(", "maskSecret"):
        assert gone not in product, gone


# ---------------------------------------------------------------------------
# PHP helpers under the PHP CLI (no Laravel)
# ---------------------------------------------------------------------------

def _php(code: str, root: Path = None):
    settings = str(SETTINGS_PHP).replace("\\", "/")
    health = str(HEALTH_PHP).replace("\\", "/")
    script = ("<?php\nnamespace App\\Http\\Controllers { abstract class Controller {} }\nnamespace {\n"
              "function base_path($p = '') { return rtrim((string) getenv('LQ_TEST_DASH'), '/') . '/' . ltrim($p, '/'); }\n"
              f"require '{settings}';\nrequire '{health}';\n"
              "use App\\Http\\Controllers\\SettingsController;\nuse App\\Http\\Controllers\\HealthController;\n"
              "$out = [];\n" + code + "\necho json_encode($out, JSON_UNESCAPED_UNICODE);\n}\n")
    with tempfile.NamedTemporaryFile("w", suffix=".php", delete=False, encoding="utf-8") as fh:
        fh.write(script)
        path = fh.name
    env = dict(os.environ, LQ_TEST_DASH=str(root or (Path(tempfile.gettempdir()) / "lq-none" / "dashboard")))
    for name in ("SERPER_API_KEY", "GEMINI_API_KEY", "PHOTOROOM_API_KEY", "CLOUDINARY_API_KEY",
                 "CLOUDINARY_API_SECRET", "CLOUDINARY_CLOUD_NAME", "SPREADSHEET_NAME_OR_URL", "SPREADSHEET_TAB_NAME",
                 "CREDENTIALS_FILE", "BG_REMOVAL_METHOD", "OUTPUT_CANVAS_SIZE"):
        env.pop(name, None)
    try:
        result = subprocess.run([PHP, path], capture_output=True, text=True, timeout=60, encoding="utf-8", env=env)
    finally:
        os.unlink(path)
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


def php_value(obj) -> str:
    text = json.dumps(obj, ensure_ascii=False).replace("\\", "\\\\").replace("'", "\\'")
    return f"json_decode('{text}', true)"


def _node(script: str):
    result = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=60, encoding="utf-8")
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


def _js(path: Path) -> str:
    return "globalThis.window = globalThis;\n" + read(path) + "\n"


def _diag(**statuses):
    services = {key: {"name": key, "status": status, "is_critical": key not in ("proxy", "google_search"),
                      "details": f"details of {key}" if status == "offline" else ""}
                for key, status in statuses.items()}
    return {"status": "success", "all_ok": "offline" not in statuses.values(),
            "checked_at": "2026-09-30T09:12:00+00:00", "services": services, "raw_logs": "RAW"}


@NEEDS_PHP
@NEEDS_NODE
def test_service_cards_say_the_same_on_the_server_and_in_the_script():
    results = [None, _diag(google_sheets="online", serper="offline", gemini="disabled", photoroom="online"),
               _diag(google_sheets="offline", serper="online", gemini="online", photoroom="online", cloudinary="online",
                     proxy="offline", google_search="disabled")]
    php = _php("foreach (" + php_value(results) + " as $r) {"
               " $out['cards'][] = HealthController::serviceCards($r, ['sheet_tab' => 'Products', 'sheet_rows' => 100,"
               " 'gemini_model' => 'gemini-3.1-flash-lite']);"
               " $out['optional'][] = HealthController::optionalServices($r);"
               " $out['public'][] = HealthController::publicResult($r); }")
    js = _node(_js(HEALTH_JS) + "const H = window.LaqtaHealth; console.log(JSON.stringify("
               + json.dumps(results) + ".map(r => ({ cards: H.servicesView(r), optional: H.optionalView(r) }))));")
    for cards, optional, script in zip(php["cards"], php["optional"], js):
        assert [c["key"] for c in cards] == ["google_sheets", "serper", "gemini", "photoroom", "cloudinary"]
        for card in cards:
            view = script["cards"][card["key"]]
            assert (card["state"], card["tone"], card["details"]) == (view["state"], view["tone"], view["details"])
        assert optional == script["optional"]
    never, partial, full = php["cards"]
    assert {c["state"] for c in never} == {"لسا ما انفحص"}                     # never «يعمل» without a check
    assert [c["state"] for c in partial] == ["متصل", "ما بيرد", "مش مفعّل", "يعمل", "ما انفحص"]
    assert partial[1]["details"] == "details of serper"
    assert never[0]["note"] == "تبويب Products · 100 صف بآخر قراءة" and never[2]["note"] == "gemini-3.1-flash-lite"
    assert php["optional"][2] == [{"name": "البروكسي", "state": "ما بيرد", "tone": "warning"}]
    # the page gets the statuses and details, never the raw check output
    assert php["public"][0] is None and "raw_logs" not in php["public"][1]
    assert php["public"][1]["services"]["serper"] == {"status": "offline", "is_critical": True,
                                                       "details": "details of serper"}


@NEEDS_PHP
@NEEDS_NODE
def test_row_counts_use_the_arabic_plural():
    """«5 صفوف», «صفين», «صف واحد», «100 صف»: never «5 صف» (the sheet card and the search footnote)."""
    php = _php("foreach ([1, 2, 5, 10, 11, 100] as $n) {"
               " $out[] = HealthController::serviceNote('google_sheets', ['sheet_rows' => $n]); }")
    assert php == ["صف واحد بآخر قراءة", "صفين بآخر قراءة", "5 صفوف بآخر قراءة", "10 صفوف بآخر قراءة",
                   "11 صف بآخر قراءة", "100 صف بآخر قراءة"]
    reports = [{"status": "success", "scanned": n, "limit": 2000, "truncated": n >= 2000, "latest_age_s": 60,
                "timed_by_row_update": 0, "alerts": [],
                "windows": {"24h": {"searches": 1, "unreadable": u, "decisions": {"NOT_FOUND": 1}, "providers": {},
                                    "verifier": {}, "cost_usd": {}, "failure_codes": []}}}
               for n, u in ((1, 0), (2, 2), (5, 3), (2000, 0))]
    js = _node(_js(HEALTH_JS) + "console.log(JSON.stringify(" + json.dumps(reports)
               + ".map(r => window.LaqtaHealth.opsView(r, '24h').note)));")
    assert "(صف واحد)" in js[0]
    assert "(صفين)" in js[1] and "في صفين ما قدرنا نقرأ" in js[1]
    assert "(5 صفوف)" in js[2] and "في 3 صفوف ما قدرنا نقرأ" in js[2]
    assert "(2000 صف)" in js[3] and "وصلنا للحد (2000 صف)" in js[3]
    for note in js:
        assert not re.search(r"\b(?:[3-9]|10) صف\b", note), note


@NEEDS_PHP
def test_key_states():
    cases = [(False, None, None, None), (True, None, None, None), (True, "online", 100, 50), (True, "online", 100, 200),
             (True, "offline", 100, None), (True, "disabled", 100, 50)]
    out = _php("foreach (" + php_value(cases) + " as [$s, $st, $c, $ch]) {"
               " $out[] = SettingsController::keyState($s, $st, $c, $ch); }")
    assert out == [{"state": "غير محفوظ", "tone": "warning"}, {"state": "محفوظ · ما انفحص", "tone": "muted"},
                   {"state": "محفوظ · يعمل", "tone": "success"},
                   {"state": "محفوظ · ما انفحص بعد التغيير", "tone": "muted"},
                   {"state": "محفوظ · ما بيرد", "tone": "danger"}, {"state": "محفوظ · مش مفعّل", "tone": "muted"}]


@NEEDS_PHP
def test_key_rows_come_from_saved_settings_and_the_last_check_never_from_the_key(tmp_path):
    dash = tmp_path / "dashboard"
    dash.mkdir()
    (tmp_path / ".env").write_text('GEMINI_API_KEY="from-env-SECRET-77"\n', encoding="utf-8")
    (tmp_path / "credentials.json").write_text(json.dumps({"client_email": "bot@proj.iam.gserviceaccount.com",
                                                            "private_key": "PRIVATE-SECRET-KEY"}), encoding="utf-8")
    stored = {"serper_api_key": {"value": "db-SECRET-SERPER-1", "updated_at": 50},
              "cloudinary_cloud_name": {"value": "laqta", "updated_at": 50},
              "cloudinary_api_key": {"value": "db-SECRET-CLOUD-2", "updated_at": 50}}
    diag = _diag(google_sheets="online", serper="online", gemini="offline", photoroom="online", cloudinary="online")
    out = _php(f"$out['rows'] = SettingsController::keyRows({php_value(stored)}, {php_value(diag)});"
               "$out['sheet'] = SettingsController::sheetData();", root=dash)
    rows = {r["id"]: r for r in out["rows"]}
    assert rows["serper"]["state"] == "محفوظ · يعمل" and rows["serper"]["from_env"] is False
    assert rows["gemini"]["state"] == "محفوظ · ما بيرد" and rows["gemini"]["from_env"] is True
    assert rows["photoroom"]["state"] == "غير محفوظ"
    assert rows["cloudinary"]["state"] == "غير محفوظ" and rows["cloudinary"]["cloud_name"] == "laqta"  # no API secret
    assert rows["google_sheet"]["state"] == "متصل"
    assert out["sheet"]["credentials"] == {"exists": True, "email": "bot@proj.iam.gserviceaccount.com"}
    assert out["sheet"]["url"] == "automation sheet" and out["sheet"]["url_is_default"] is True
    dumped = json.dumps(out, ensure_ascii=False)
    assert "SECRET" not in dumped                                    # no key, not even in part


@NEEDS_PHP
def test_a_key_cleared_after_the_check_is_not_reported_working(tmp_path):
    """Gemini was checked (online) with the key saved in the database; then that key was cleared and the .env key
    took over. The last check describes the cleared key, so the row must say «ما انفحص بعد التغيير»."""
    dash = tmp_path / "dashboard"
    dash.mkdir()
    (tmp_path / ".env").write_text('GEMINI_API_KEY="env-key-never-checked-1"\n', encoding="utf-8")
    checked = 1_790_000_000
    diag = _diag(google_sheets="online", serper="online", gemini="online", photoroom="online", cloudinary="online")
    diag["checked_at"] = "2026-09-21T14:13:20+00:00"                       # == checked
    stored = {"gemini_api_key": {"value": "", "updated_at": checked + 600},       # cleared 10 minutes later
              "serper_api_key": {"value": "db-serper-key-1", "updated_at": checked - 600}}
    out = _php(f"$out = SettingsController::keyRows({php_value(stored)}, {php_value(diag)});", root=dash)
    rows = {r["id"]: r for r in out}
    assert rows["gemini"]["from_env"] is True
    assert rows["gemini"]["state"] == "محفوظ · ما انفحص بعد التغيير"
    assert rows["serper"]["state"] == "محفوظ · يعمل"                         # changed before the check


def _review_rows(plan):
    rows, n = [], 0
    for brand, good, bad in plan:
        for i in range(good + bad):
            n += 1
            rows.append({"id": n, "created_at": f"2026-09-30 10:{n // 60 % 60:02d}:{n % 60:02d}",
                         "action": "approved" if i < good else "rejected", "sku_key": f"{brand}-{i}", "brand": brand,
                         "image_url": f"https://img.ae/{brand}-{i}.jpg", "was_preselected": 1,
                         "engine_decision": "REVIEW_PRESELECTED", "reason_code": None if i < good else "WRONG_SIZE",
                         "page_domain": "lulu.ae"})
    return rows


@NEEDS_PHP
def test_auto_publish_table_is_review_stats(offline):
    import local_cache_db

    stats = dict({"status": "success"}, **local_cache_db.review_stats(
        _review_rows([("AL ALALI", 12, 0), ("VIRGINIA", 3, 1), ("ALMARAI", 189, 0), ("MASAFI", 189, 0), ("BAD", 30, 3)])))
    stored = {"auto_publish_enabled": {"value": "false"}, "auto_publish_brands": {"value": "Masafi, *, category:Dairy"}}
    out = _php(f"$out = SettingsController::autoPublishData({php_value(stored)}, {php_value(stats)});")
    rows = {r["brand"]: r for r in out["rows"]}
    for b in stats["brands"]:
        row = rows[b["brand"]]
        assert row["reviews"] == b["prechecked"]
        assert row["precision"] == f"{int(b['precision'] * 100 + 1e-9)}%"
        assert row["lower_bound"] == f"{int(b['lower_bound'] * 100 + 1e-9)}%"
        if b["status"] == "needs_reviews":
            assert row["chip"] == f"تحتاج {b['reviews_needed'] - b['prechecked']} مراجعة"
    assert rows["AL ALALI"]["chip"] == "تحتاج 177 مراجعة" and rows["AL ALALI"]["action"] is None
    assert rows["BAD"]["chip"] == "دقة أقل من المطلوب" and rows["BAD"]["tone"] == "error"
    assert (rows["ALMARAI"]["chip"], rows["ALMARAI"]["action"]) == ("جاهزة", "enable")
    assert (rows["MASAFI"]["chip"], rows["MASAFI"]["action"]) == ("مفعّلة", "disable")     # listed as 'Masafi'
    # entries without evidence stay visible and can be switched off
    assert (rows["*"]["label"], rows["*"]["action"], rows["*"]["unproven"]) == ("كل الماركات (*)", "disable", True)
    assert rows["category:Dairy"]["label"] == "فئة Dairy"
    assert out["ready_listed"] == ["MASAFI"] and out["can_enable"] is True and out["enabled"] is False
    assert "98%" in out["criterion"] and "189 اقتراح" in out["criterion"]
    for r in out["rows"]:
        assert not re.search(r"[a-z]+_[a-z]+", r["chip"]), r["chip"]

    # nothing ready in the list: the switch cannot be turned on
    stored["auto_publish_brands"] = {"value": "AL ALALI, *"}
    out = _php(f"$out = SettingsController::autoPublishData({php_value(stored)}, {php_value(stats)});")
    assert out["can_enable"] is False and out["ready_listed"] == []
    # the bridge is down: an error state, never an empty table
    out = _php(f"$out = SettingsController::autoPublishData({php_value(stored)}, ['status' => 'error']);")
    assert out["status"] == "error" and out["can_enable"] is False and out["reviews"] is None


@NEEDS_PHP
def test_enabled_brands_are_not_called_unproven_when_the_stats_are_unreadable():
    """review_stats down: the enabled entries stay listed (and can be switched off), but the page cannot know their
    evidence, so it must not say «بدون مراجعات» or warn that they are unproven."""
    stored = {"auto_publish_enabled": {"value": "true"}, "auto_publish_brands": {"value": "ALMARAI, *"}}
    out = _php(f"$out = SettingsController::autoPublishData({php_value(stored)}, ['status' => 'failed']);")
    rows = {r["brand"]: r for r in out["rows"]}
    assert set(rows) == {"ALMARAI", "*"}
    for row in rows.values():
        assert row["action"] == "disable" and row["listed"] is True
        assert row["unproven"] is False and row["reviews"] is None
        assert "بدون مراجعات" not in row["chip"] and row["tone"] != "warning"
    # with readable stats an entry without any review is still called out
    out = _php(f"$out = SettingsController::autoPublishData({php_value(stored)}, "
               "['status' => 'success', 'brands' => [], 'thresholds' => []]);")
    for row in out["rows"]:
        assert row["unproven"] is True and row["chip"] == "مفعّلة بدون مراجعات" and row["reviews"] == 0


@NEEDS_PHP
def test_auto_publish_blocker_and_brand_list(offline):
    import local_cache_db

    stats = dict({"status": "success"}, **local_cache_db.review_stats(_review_rows([("ALMARAI", 189, 0), ("X", 5, 0)])))
    out = _php("foreach ([[['almarai ']], [['X', '*']], [[]]] as [$b]) {"
               f" $out['why'][] = SettingsController::autoPublishBlocker($b, {php_value(stats)}); }}"
               "$out['down'] = SettingsController::autoPublishBlocker(['ALMARAI'], ['status' => 'failed']);"
               "$out['list'] = SettingsController::brandList(' Almarai, ,Masafi ,Almarai,category:Dairy ');")
    assert out["why"][0] is None
    assert "ماركة جاهزة" in out["why"][1] and "ماركة جاهزة" in out["why"][2]
    assert "ما قدرنا نتأكد" in out["down"]
    # read back the way config.py parses AUTO_PUBLISH_BRANDS
    value = ", ".join(out["list"])
    assert [b.strip() for b in value.split(",") if b.strip()] == out["list"] == ["Almarai", "Masafi", "category:Dairy"]


def _lane_rows(plan):
    """_review_rows with the lane of each pick: [(brand, good, bad, lane)]."""
    rows = []
    for brand, good, bad, lane in plan:
        rows += [dict(r, lane=lane) for r in _review_rows([(brand, good, bad)])]
    for n, r in enumerate(rows, 1):
        r["id"] = n
    return rows


@NEEDS_PHP
def test_strict_lane_section_reads_the_lane_stats(offline):
    """«النشر الآلي لكل الماركات المؤكدة»: lane strict in plain words, its switch only when ready, unsure as info."""
    import local_cache_db

    stats = dict({"status": "success"}, **local_cache_db.review_stats(
        _lane_rows([("ALMARAI", 12, 0, "strict"), ("ALMARAI-U", 4, 1, "unsure")])))
    stored = {"auto_publish_enabled": {"value": "true"}, "auto_publish_brands": {"value": ""},
              "auto_publish_strict_lane": {"value": "false"}}
    out = _php(f"$out = SettingsController::autoPublishData({php_value(stored)}, {php_value(stats)});"
               f"$out['why'] = SettingsController::strictLaneBlocker({php_value(stats)});"
               "$out['down'] = SettingsController::strictLaneBlocker(['status' => 'failed']);")
    lane = out["lane"]
    assert lane["text"] == "من 12 اقتراح بهالفئة، اعتمدت 12."
    assert lane["unsure_text"].startswith("القارئ مش متأكد بس العنوان بيأكد: اعتمدت 4 من 5.")
    assert (lane["ready"], lane["can_enable"], lane["enabled"]) == (False, False, False)
    assert lane["chip"] == "تحتاج 177 مراجعة" and "الحد المضمون" in lane["detail"]
    assert "لسا مش جاهزة" in out["why"] and "ما قدرنا نتأكد" in out["down"]
    assert out["can_enable"] is False                       # no ready brand, the lane is not ready either

    ready = dict({"status": "success"}, **local_cache_db.review_stats(
        _lane_rows([("ALMARAI", 189, 0, "strict")])))
    stored["auto_publish_strict_lane"] = {"value": "true"}
    out = _php(f"$out = SettingsController::autoPublishData({php_value(stored)}, {php_value(ready)});"
               f"$out['why'] = SettingsController::strictLaneBlocker({php_value(ready)});"
               f"$out['main'] = SettingsController::autoPublishBlocker([], {php_value(ready)}, true);"
               f"$out['main_off'] = SettingsController::autoPublishBlocker([], {php_value(ready)}, false);")
    assert out["why"] is None and out["main"] is None and "ماركة جاهزة" in out["main_off"]
    assert (out["lane"]["ready"], out["lane"]["chip"], out["can_enable"]) == (True, "شغّال", True)
    assert out["lane"]["text"] == "من 189 اقتراح بهالفئة، اعتمدت 189."
    out = _php(f"$out = SettingsController::autoPublishData({php_value(stored)}, ['status' => 'error']);")
    assert out["lane"]["status"] == "error" and out["lane"]["can_enable"] is False


@NEEDS_PHP
def test_health_lanes_payload(offline):
    import local_cache_db

    stats = dict({"status": "success"}, **local_cache_db.review_stats(
        _lane_rows([("A", 11, 0, "strict"), ("B", 3, 1, "unsure"), ("C", 0, 0, "other"), ("D", 2, 0, None)])))
    out = _php(f"$out = HealthController::lanesPayload({php_value(stats)});")
    assert out["status"] == "success" and out["unlaned"] == 2
    assert {k: (v["accepted"], v["prechecked"]) for k, v in out["lanes"].items()} == {
        "strict": (11, 11), "unsure": (3, 4), "other": (0, 0)}
    assert out["lanes"]["other"]["lower_bound"] is None and out["lanes"]["strict"]["ready"] is False
    assert "brands" not in out and "domains" not in out                  # only the card's numbers leave the server


@NEEDS_NODE
def test_health_lanes_view():
    out = _node(_js(HEALTH_JS) + """
const H = window.LaqtaHealth;
const ok = H.lanesView({status: 'success', lanes: {strict: {prechecked: 12, accepted: 12, lower_bound: 0.7575},
    unsure: {prechecked: 0, accepted: 0, lower_bound: null}}});
console.log(JSON.stringify({ok: ok, down: H.lanesView(null)}));
""")
    rows = {r["key"]: r for r in out["ok"]["rows"]}
    assert [r["key"] for r in out["ok"]["rows"]] == ["strict", "unsure", "other"]
    assert (rows["strict"]["text"], rows["strict"]["bound"]) == ("اعتمدت 12 من 12", "الحد المضمون 75.8%")
    assert (rows["unsure"]["text"], rows["unsure"]["bound"]) == ("لسا ما في مراجعات", "")
    assert rows["unsure"]["label"] == "القارئ مش متأكد بس العنوان بيأكد"
    assert out["down"]["kind"] == "error" and out["down"]["rows"] == []


def test_background_methods_match_python():
    import config
    import main

    php = re.search(r"BG_METHODS = \[(.*?)\];", read(SETTINGS_PHP)).group(1)
    assert tuple(re.findall(r"'([a-z_]+)'", php)) == config.BG_REMOVAL_METHODS == main.SUPPORTED_BG_METHODS


def test_config_reads_processing_settings(monkeypatch, fake_connection):
    import config
    import pymysql

    for name in ("BG_REMOVAL_METHOD", "ENABLE_IMAGE_ENHANCEMENT", "OUTPUT_CANVAS_SIZE", "AUTO_PUBLISH_ENABLED",
                 "AUTO_PUBLISH_BRANDS", "STRICT_BRAND_MATCH", "SEARCH_ENGINE"):
        monkeypatch.setattr(config, name, getattr(config, name))
    rows = [{"key": "bg_removal_method", "value": "NONE"}, {"key": "enable_image_enhancement", "value": "true"},
            {"key": "output_canvas_size", "value": "1200"}]

    def responder(sql, params):
        return [{"t": "system_settings"}] if sql.upper().startswith("SHOW") else rows

    monkeypatch.setattr(pymysql, "connect", lambda **k: fake_connection(responder))
    config.load_db_config()
    assert (config.BG_REMOVAL_METHOD, config.ENABLE_IMAGE_ENHANCEMENT, config.OUTPUT_CANVAS_SIZE) == ("none", True, 1200)

    rows[:] = [{"key": "bg_removal_method", "value": "magic"}, {"key": "enable_image_enhancement", "value": "false"}]
    config.load_db_config()
    assert config.BG_REMOVAL_METHOD == "none" and config.ENABLE_IMAGE_ENHANCEMENT is False   # unknown method ignored


@NEEDS_PHP
def test_log_tail_is_a_normal_state_and_masks_secrets(tmp_path):
    log = tmp_path / "pipeline.log"
    lines = [f"line {i}" for i in range(500)] + ["key=db-SECRET-SERPER-1 used", "proxy http://u:pw@proxy.local:8080 ok"]
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")
    out = _php(f"$out['missing'] = HealthController::logPayload('pipeline', {php_value(str(tmp_path / 'none.log'))});"
               f"$out['tail'] = HealthController::logPayload('pipeline', {php_value(str(log))}, ['db-SECRET-SERPER-1', 'abc']);"
               f"$out['small'] = HealthController::tailLines({php_value(str(log))}, 5, 40);")
    assert out["missing"] == [200, {"status": "success", "kind": "pipeline", "exists": False, "lines": [],
                                    "updated_at": None}]
    code, body = out["tail"]
    assert code == 200 and body["exists"] is True and len(body["lines"]) == 200
    assert body["lines"][-2] == "key=[محجوب] used" and body["lines"][-1] == "proxy http://[محجوب]@proxy.local:8080 ok"
    assert "SECRET" not in json.dumps(body) and "pw@" not in json.dumps(body)
    assert out["small"] == ["proxy http://u:pw@proxy.local:8080 ok"]          # only whole lines from the tail


@NEEDS_PHP
def test_log_tail_masks_env_keys_key_lists_and_url_keys_and_keeps_bad_lines(tmp_path):
    """The log tail hides what verify_cloud_services._redact hides: a key set only in .env, each key of a
    comma-separated Custom Search list, the proxy's user and password, and any ?key= in a URL. A line with a
    byte that is not UTF-8 is shown (repaired), not blanked."""
    dash = tmp_path / "dashboard"
    dash.mkdir()
    (tmp_path / ".env").write_text('GEMINI_API_KEY="ENVONLY-GEMINI-777"\nSPREADSHEET_TAB_NAME=Products\n',
                                   encoding="utf-8")
    log = tmp_path / "pipeline.log"
    lines = [
        "ERROR 403 Forbidden for url: https://www.googleapis.com/customsearch/v1?key=AIzaCSE-SECOND-222&cx=abc",
        "WARNING generateContent failed: https://generativelanguage.googleapis.com/v1beta/m:gen?key=UNSTORED-KEY-99",
        "INFO gemini key ENVONLY-GEMINI-777 loaded from .env",
        "INFO proxy login proxyuser / ProxyPassw0rd refused",
        "INFO row 5 sku_key=uae-001-almarai kept for review",
        "INFO Custom Search switched to AIzaCSE-FIRST-111 (key 1 of 2)",
    ]
    log.write_bytes(("\n".join(lines) + "\n").encode("utf-8") + b"WARNING bad \xff byte api_key=RAWSECRET-123 here\n")
    stored = ["AIzaCSE-FIRST-111,AIzaCSE-SECOND-222", "http://proxyuser:ProxyPassw0rd@proxy.local:8080", "", "short"]
    out = _php(f"$secrets = SettingsController::secretList({php_value(stored)});"
               f"$out = HealthController::logPayload('pipeline', {php_value(str(log))}, $secrets);", root=dash)
    code, body = out
    assert code == 200 and len(body["lines"]) == 7
    dumped = json.dumps(body, ensure_ascii=False)
    for secret in ("AIzaCSE-FIRST-111", "AIzaCSE-SECOND-222", "UNSTORED-KEY-99", "ENVONLY-GEMINI-777", "proxyuser",
                   "ProxyPassw0rd", "RAWSECRET-123"):
        assert secret not in dumped, secret
    got = body["lines"]
    assert got[0].endswith("customsearch/v1?key=[محجوب]&cx=abc")
    assert got[1].endswith("?key=[محجوب]") and got[2] == "INFO gemini key [محجوب] loaded from .env"
    assert got[3] == "INFO proxy login [محجوب] / [محجوب] refused"
    assert got[4] == "INFO row 5 sku_key=uae-001-almarai kept for review"      # not a key: kept for debugging
    assert got[5] == "INFO Custom Search switched to [محجوب] (key 1 of 2)"
    assert got[6].startswith("WARNING bad ") and got[6].endswith(" byte api_key=[محجوب] here")


# ---------------------------------------------------------------------------
# Page scripts under node
# ---------------------------------------------------------------------------

@NEEDS_NODE
def test_health_check_failure_says_why_and_keeps_the_last_result():
    stored = _diag(google_sheets="online", serper="online", gemini="online", photoroom="online", cloudinary="online")
    out = _node(_js(HEALTH_JS) + "const toasts = [], views = [], busy = [];\n"
                "const c = window.LaqtaHealth.createController({ initial: " + json.dumps(stored) + ",\n"
                "  fetchJson: (url) => url.indexOf('run-diagnostics') >= 0\n"
                "    ? Promise.resolve({ ok: false, status: 504, data: { status: 'failed', error: 'انتهت مهلة فحص الاتصالات' } })\n"
                "    : new Promise(() => {}),\n"
                "  renderServices: v => views.push(v), renderChecked: () => {}, renderOptional: () => {},\n"
                "  setChecking: b => busy.push(b), renderOps: () => {}, renderLog: () => {},\n"
                "  toast: (t, v) => toasts.push([t, v]), now: () => 0, schedule: () => 0 });\n"
                "c.runCheck().then(done => console.log(JSON.stringify({ done, toasts, busy, last: views[views.length - 1],"
                " during: views[0] })));")
    assert out["done"] is False and out["busy"] == [True, False]
    assert out["toasts"] == [["ما خلص الفحص: انتهت مهلة فحص الاتصالات", "danger"]]
    assert out["during"]["serper"]["state"] == "عم نفحص…"
    assert out["last"]["serper"] == {"state": "يعمل", "tone": "success", "details": ""}     # the saved result again


@NEEDS_NODE
def test_health_views_for_logs_money_and_time():
    out = _node(_js(HEALTH_JS) + "const H = window.LaqtaHealth; const now = new Date(2026, 8, 30, 12, 0).getTime();\n"
                "console.log(JSON.stringify({\n"
                " missing: H.logView({ status: 'success', exists: false, lines: [] }, 'pipeline', now),\n"
                " laravel: H.logView({ status: 'success', exists: false, lines: [] }, 'laravel', now),\n"
                " empty: H.logView({ status: 'success', exists: true, lines: [] }, 'pipeline', now),\n"
                " ok: H.logView({ status: 'success', exists: true, lines: ['a ok', 'photoroom timeout after 30s'],"
                " updated_at: now / 1000 }, 'pipeline', now),\n"
                " error: H.logView(null, 'pipeline', now),\n"
                " usd: [H.usd(0.19), H.usd(0.004), H.usd(0), H.usd(null), H.usdPrecise(0.0032)],\n"
                " when: [H.whenText(new Date(2026, 8, 30, 9, 12).getTime(), now), H.whenText(new Date(2026, 8, 29, 18, 40).getTime(), now),"
                " H.whenText(new Date(2026, 8, 3, 7, 5).getTime(), now)],\n"
                " age: [H.ageText(30), H.ageText(60), H.ageText(660), H.ageText(7200), H.ageText(3 * 86400)],\n"
                " err: [H.requestError({ status: 419 }, 'x'), H.requestError({ status: 500, data: { error: 'Invalid JSON' } }, 'بديل')]\n"
                "}));")
    assert out["missing"]["kind"] == "missing" and out["missing"]["text"].startswith("لسا ما في سجل.")
    assert out["laravel"]["text"].startswith("لسا ما في سجل.") and out["empty"]["kind"] == "empty"
    assert [line["error"] for line in out["ok"]["lines"]] == [False, True]
    assert out["ok"]["meta"] == "آخر سطرين · آخر تعديل اليوم 12:00"
    assert out["error"]["kind"] == "error" and re.search(r"[\u0600-\u06FF]", out["error"]["text"])
    assert out["usd"] == [f"{LTR}$0.19{PDI}", f"أقل من {LTR}$0.01{PDI}", f"{LTR}$0.00{PDI}", "—", f"{LTR}$0.003{PDI}"]
    assert out["when"] == ["اليوم 09:12", "مبارح 18:40", "3 أيلول 07:05"]
    assert out["age"] == ["أقل من دقيقة", "دقيقة", "11 دقيقة", "ساعتين", "3 أيام"]
    assert out["err"] == ["انتهت صلاحية الصفحة. حدّثها وجرّب مرة تانية.", "بديل"]       # English never shown


@NEEDS_NODE
def test_settings_script_helpers():
    out = _node(_js(SETTINGS_JS) + "const S = window.LaqtaSettings;\n"
                "console.log(JSON.stringify({\n"
                " full: S.previewView({ status: 'success', headers: ['Barcode', 'Product Name', 'Brand'],"
                " rows: [['1', 'Milk', 'Almarai'], [null, 'x']], columns: { barcode: 0, name: 1, brand: 2, link: -1 } }),\n"
                " missing: S.previewView({ status: 'success', headers: ['Item'], rows: [], columns: { name: -1, brand: -1 } }),\n"
                " empty: S.previewView({ status: 'success', headers: [], rows: [], columns: {} }),\n"
                " failed: S.previewView({ status: 'failed', error: 'x' }),\n"
                " err: [S.sheetError({ status: 500, data: { status: 'failed', error: 'Could not open the spreadsheet.' } }, false),"
                " S.sheetError({ status: 500, data: { error: 'Spreadsheet URL or name is required' } }, true),"
                " S.sheetError({ status: 419 }, true)],\n"
                " save: S.saveConfirmText('https://docs.google.com/x', ''), clear: S.clearConfirmText('Serper'),\n"
                " rollback: S.ROLLBACK_TEXT }));")
    cols = {c["key"]: c for c in out["full"]["columns"]}
    assert cols["barcode"]["text"] == "Barcode" and cols["barcode"]["tone"] == "success"
    assert cols["link"] == {"key": "link", "label": "رابط الصورة", "found": False, "tone": "info",
                            "text": "رح ينضاف عمود Drive Image Link"}
    assert out["full"]["rows"][1] == ["", "x"] and out["full"]["tone"] == "success"
    missing = {c["key"]: c for c in out["missing"]["columns"]}
    assert missing["name"]["tone"] == "danger" and missing["brand"]["tone"] == "danger"
    assert out["missing"]["tone"] == "warning" and "ناقصة" in out["missing"]["status"]
    assert out["empty"]["empty"] is True and out["failed"] is None
    assert out["err"][0] == {"text": "ما قدرنا نفتح الشيت. تأكد من الرابط أو الاسم، ومن إنه مشارك مع حساب الخدمة.",
                             "detail": "Could not open the spreadsheet."}
    assert out["err"][1]["text"] == "اكتب رابط الشيت أو اسمه أول." and "انتهت صلاحية" in out["err"][2]["text"]
    # no silent destructive action: each confirmation says what changes and what is kept
    assert "المراجعات والصور المعتمدة" in out["save"] and "ما بتتأثر" in out["save"] and "أول تبويب" in out["save"]
    assert "Serper" in out["clear"] and ".env" in out["clear"]
    assert "للنظام القديم" in out["rollback"] and "والمراجعات ما بتتغير" in out["rollback"]


# A few elements with attributes and listeners: enough for the confirmations settings.js wires to the page.
FAKE_SETTINGS_DOM = r"""
function makeEl(attrs, children) {
    const el = { attrs: Object.assign({}, attrs || {}), listeners: {}, checked: false, disabled: false,
                 children: children || {}, submitted: 0 };
    el.getAttribute = n => (n in el.attrs ? el.attrs[n] : null);
    el.hasAttribute = n => n in el.attrs;
    el.setAttribute = (n, v) => { el.attrs[n] = String(v); };
    el.removeAttribute = n => { delete el.attrs[n]; };
    el.addEventListener = (type, fn) => { (el.listeners[type] = el.listeners[type] || []).push(fn); };
    el.querySelector = sel => {
        if (sel === '[data-key-clear]:checked') { const c = el.children['[data-key-clear]']; return c && c.checked ? c : null; }
        return el.children[sel] || null;
    };
    el.querySelectorAll = sel => (el.children[sel] ? [].concat(el.children[sel]) : []);
    el.requestSubmit = () => { el.submitted++; };
    el.fire = (type) => { let prevented = false; const ev = { currentTarget: el, target: el,
        preventDefault: () => { prevented = true; } }; (el.listeners[type] || []).forEach(fn => fn(ev)); return prevented; };
    return el;
}
const clearBox = makeEl({ 'data-key-clear': '' });
const keyForm = makeEl({ 'data-key-form': 'photoroom', 'data-key-name': 'PhotoRoom' }, { '[data-key-clear]': clearBox });
const v1Radio = makeEl({ 'data-engine-v1': '' });
const advForm = makeEl({ 'data-advanced-form': '', 'data-engine': 'v2' }, { '[data-engine-v1]': v1Radio });
const apSwitch = makeEl({ 'data-autopub-switch': '', 'data-confirm-on': 'ON-TEXT', 'data-confirm-off': 'OFF-TEXT' });
const apSave = makeEl({ 'data-autopub-save': '' });
const apForm = makeEl({ 'data-autopub-form': '' }, { '[data-autopub-switch]': apSwitch, '[data-autopub-save]': apSave });
const page = makeEl({ 'data-settings-page': '' }, { form: [keyForm, advForm, apForm], '[data-autopub-form]': apForm });
globalThis.window = globalThis;
globalThis.document = { querySelector: sel => (sel === '[data-settings-page]' ? page : null) };
const asked = [];
let answer = false;
globalThis.confirm = text => { asked.push(text); return answer; };
"""


@NEEDS_NODE
def test_settings_page_asks_before_clearing_a_key_rolling_back_or_switching_auto_publish():
    """No silent destructive action: the wiring in settings.js, not only the texts, under node with a few fake elements."""
    script = FAKE_SETTINGS_DOM + read(SETTINGS_JS) + r"""
const out = {};
// a key form without «امسح المحفوظ» submits without a question
out.plain = [keyForm.fire('submit'), asked.length];
clearBox.checked = true;
out.clearNo = [keyForm.fire('submit'), asked.pop()];
answer = true;
out.clearYes = [keyForm.fire('submit'), asked.length];
answer = false;
v1Radio.checked = true;
out.rollbackNo = [advForm.fire('submit'), asked.pop() === window.LaqtaSettings.ROLLBACK_TEXT];
advForm.attrs['data-engine'] = 'v1';                       // already on v1: saving again does not ask
out.v1Again = [advForm.fire('submit'), asked.length];
out.saveHidden = apSave.hasAttribute('hidden');
apSwitch.checked = true;                                    // the click turned it on
apSwitch.fire('change');
out.onNo = [asked.pop(), apSwitch.checked, apForm.submitted];
answer = true;
apSwitch.checked = true;
apSwitch.fire('change');
out.onYes = [asked.pop(), apSwitch.checked, apForm.submitted];
apSwitch.checked = false;
apSwitch.fire('change');
out.offYes = [asked.pop(), apForm.submitted];
console.log(JSON.stringify(out));
"""
    out = _node(script)
    assert out["plain"] == [False, 0]
    assert out["clearNo"][0] is True and "PhotoRoom" in out["clearNo"][1] and ".env" in out["clearNo"][1]
    assert out["clearYes"] == [False, 1]
    assert out["rollbackNo"] == [True, True]
    assert out["v1Again"] == [False, 1]
    assert out["saveHidden"] is True                            # with the script, the switch itself saves
    assert out["onNo"] == ["ON-TEXT", False, 0]                 # declined: switched back, nothing sent
    assert out["onYes"] == ["ON-TEXT", True, 1]
    assert out["offYes"] == ["OFF-TEXT", 2]


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
           "cloudinary_api_key": _fake_secret("CLKEY"),
           "google_search_api_key": _fake_secret("CSE"),
           "anthropic_api_key": _fake_secret("ANTHROPIC"),
           "serpapi_api_key": _fake_secret("SERPAPI"),
           "proxy_url": "http://user:SECRET-PROXY-pass@proxy.local:8080"}
TOUCHED = list(SECRETS) + ["auto_publish_enabled", "auto_publish_brands", "search_engine", "gemini_model",
                           "google_search_cx", "cloudinary_cloud_name", "strict_brand_match", "output_canvas_size",
                           "bg_removal_method", "enable_image_enhancement", "enable_gemini_pre_validation",
                           "filter_competitors", "bypass_white_background_check", "verifier_primary",
                           "verifier_strong", "verifier_monthly_budget_usd", "model_prices", "expansion_enabled",
                           "expansion_max_calls", "visual_search", "serpapi_lens_price_usd", "gtin_policy",
                           "local_index_enabled", "local_index_max_pages", "bg_removal_method_previous",
                           "worker_concurrency", "auto_publish_strict_lane"]


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


def _settings(db):
    return {r["key"]: r["value"] for r in _sql(db, "SELECT `key`, `value` FROM system_settings")}


def _put(db, values):
    for key, value in values.items():
        _sql(db, "INSERT INTO system_settings (`key`, `value`, updated_at) VALUES (%s, %s, NOW() - INTERVAL 1 DAY) "
                 "ON DUPLICATE KEY UPDATE `value` = VALUES(`value`), updated_at = VALUES(updated_at)", (key, value))


@pytest.fixture
def app_env(mariadb_or_skip, tmp_path):
    if PHP is None or not VENDOR_AUTOLOAD.exists():
        pytest.skip("php or dashboard/vendor is not installed")
    db = mariadb_or_skip
    saved = {k: v for k, v in _settings(db).items() if k in TOUCHED}
    _put(db, dict(SECRETS, auto_publish_enabled="false", auto_publish_brands="", auto_publish_strict_lane="false",
                  search_engine="v2",
                  gemini_model="gemini-3.1-flash-lite", strict_brand_match="true", output_canvas_size="800",
                  cloudinary_cloud_name="laqta-test"))

    import local_cache_db
    stats = dict({"status": "success"}, **local_cache_db.review_stats(
        _review_rows([("ALMARAI", 189, 0), ("AL ALALI", 12, 0)])))
    lane_ready = dict({"status": "success"}, **local_cache_db.review_stats(
        [dict(r, lane="strict") for r in _review_rows([("ALMARAI", 189, 0)])]))
    fixture = {"review_stats": stats, "lane_ready": lane_ready,
               "ops_health": {"status": "success", "scanned": 0, "windows": {}, "alerts": []},
               "bg_methods": {"status": "success", "local": {"grabcut": True, "rembg": False}}}
    calls = tmp_path / "calls.txt"
    stub = tmp_path / "stub_bridge.py"
    stub.write_text(
        "import json, os, sys\n"
        f"FIXTURE = json.loads({json.dumps(json.dumps(fixture, ensure_ascii=False))})\n"
        "action = sys.argv[1] if len(sys.argv) > 1 else ''\n"
        f"open({str(calls)!r}, 'a').write(action + '\\n')\n"
        "if os.environ.get('LQ_STUB_MODE') == 'down':\n"
        "    print(json.dumps({'status': 'failed', 'error': 'stub bridge is down'}))\n"
        "elif os.environ.get('LQ_STUB_MODE') == 'lane_ready' and action == 'review_stats':\n"
        "    print(json.dumps(FIXTURE['lane_ready'], ensure_ascii=False))\n"
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
    })
    yield {"env": env, "calls": calls, "db": db}
    _sql(db, "DELETE FROM system_settings WHERE `key` IN (" + ", ".join(["%s"] * len(TOUCHED)) + ")", tuple(TOUCHED))
    _put(db, saved)


def _kernel(env, requests):
    """Each request is (method, path, params); returns status, location, body and the session flash of each."""
    dash = str(DASH).replace("\\", "/")
    script = f"""<?php
require '{dash}/vendor/autoload.php';
$app = require '{dash}/bootstrap/app.php';
$kernel = $app->make(Illuminate\\Contracts\\Http\\Kernel::class);
$out = [];
foreach (json_decode($argv[1], true) as [$method, $path, $params]) {{
    $request = Illuminate\\Http\\Request::create($path, $method, $params, [], [], ['HTTP_ACCEPT' => 'text/html,application/json']);
    $response = $kernel->handle($request);
    $session = $app['session']->driver();
    $out[] = ['status' => $response->getStatusCode(), 'location' => $response->headers->get('Location'),
              'body' => $response->getContent(),
              'flash' => ['success' => $session->get('success'), 'error' => $session->get('error'),
                          'warnings' => $session->get('warnings')]];
}}
echo json_encode($out, JSON_UNESCAPED_UNICODE);
"""
    with tempfile.NamedTemporaryFile("w", suffix=".php", delete=False, encoding="utf-8") as fh:
        fh.write(script)
        path = fh.name
    try:
        result = subprocess.run([PHP, path, json.dumps(requests)], cwd=DASH, env=env, capture_output=True, text=True,
                                timeout=240, encoding="utf-8")
    finally:
        os.unlink(path)
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-3000:]
    return json.loads(result.stdout)


TABS = ["sheet", "keys", "models", "auto-publish", "processing", "advanced"]
N_PAGES = 3 + len(TABS)
PAGES = [["GET", "/system-diagnostics", {}], ["GET", "/active-learning", {}], ["GET", "/settings?tab=bogus", {}]] \
    + [["GET", f"/settings?tab={tab}", {}] for tab in TABS] \
    + [["GET", "/api/view-pipeline-log", {}], ["GET", "/api/view-laravel-log", {}]]


def test_routes_render_redirect_and_never_show_a_stored_key(app_env):
    out = _kernel(app_env["env"], PAGES)
    health, active, bogus = out[0], out[1], out[2]
    assert health["status"] == 200, health["body"][:2000]
    assert "<title>الصحة والتكلفة · لقطة</title>" in health["body"] and 'class="lq-sidebar"' in health["body"]
    assert re.search(r'<a href="[^"]+/system-diagnostics"[^>]*aria-current="page"', health["body"])
    assert "data-health-page" in health["body"] and "فحص الاتصالات الآن" in health["body"]
    assert active["status"] == 302 and active["location"].endswith("/settings?tab=auto-publish")
    assert bogus["status"] == 200 and re.search(r'href="[^"]+\?tab=sheet"[^>]*aria-current="page"', bogus["body"])
    for tab, page in zip(TABS, out[3:N_PAGES]):
        assert page["status"] == 200, (tab, page["body"][:2000])
        assert "<title>الإعدادات · لقطة</title>" in page["body"]
        assert re.search(rf'href="[^"]+\?tab={tab}"[^>]*aria-current="page"', page["body"]), tab
        for leftover in ("{{", "{!!", "@props", "<x-lq", "@json", "@include"):
            assert leftover not in page["body"], (tab, leftover)
    for log in out[N_PAGES:N_PAGES + 2]:
        assert log["status"] == 200 and json.loads(log["body"])["status"] == "success"
    # no stored key reaches any page or API, not even its last four characters on a page. The log tails read the
    # real temp/pipeline.log and laravel.log, whose lines may hold any text (a port like 8080): there a stored value
    # must be masked, which is what the check of the whole value covers.
    for i, response in enumerate(out):
        for value in SECRETS.values():
            assert value not in response["body"], value
            if i < N_PAGES:
                assert value[-4:] not in response["body"], value
        if i < N_PAGES:
            assert "SECRET-" not in response["body"]
    keys = out[4]["body"]
    assert keys.count('type="password"') == 7                      # one empty, write-only field per stored key
    assert len(re.findall(r'type="password"[^>]*value=""', keys)) == 7
    assert "محفوظ" in keys and "غير محفوظ" not in keys
    # the auto-publish tab reads the bridge, and the processing tab asks once which local background-removal methods
    # are installed (bg_methods, cached); nothing else on these pages does
    assert set(app_env["calls"].read_text().split()) == {"review_stats", "bg_methods"}


def test_each_form_saves_only_its_own_section(app_env):
    db = app_env["db"]
    _put(db, {"auto_publish_brands": "ALMARAI", "filter_competitors": "true"})
    before = _settings(db)
    out = _kernel(app_env["env"], [
        ["POST", "/settings", {"section": "serper", "serper_api_key": "  NEW-SERPER-KEY-1  "}],
        ["POST", "/settings", {"section": "gemini", "gemini_api_key": ""}],                 # empty: keep
        ["POST", "/settings", {"section": "photoroom", "photoroom_api_key": "••••b44"}],      # a mask: keep
        ["POST", "/settings", {"section": "cloudinary", "cloudinary_cloud_name": " laqta ", "clear_cloudinary_api_secret": "1"}],
        ["POST", "/settings", {"section": "processing", "output_canvas_size": "1200", "bg_removal_method": "none"}],
        ["POST", "/settings", {"section": "processing", "output_canvas_size": "99999", "bg_removal_method": "magic",
                               "enable_image_enhancement": "true"}],
        ["POST", "/settings", {"section": "nope"}],
        ["POST", "/settings", {"section": "serper", "serper_api_key": ["ARRAY-VALUE"]}],      # crafted: ignored
        # four digits but outside 300-4000: refused, the rest of the form still saves
        ["POST", "/settings", {"section": "processing", "output_canvas_size": "5000", "bg_removal_method": "none",
                               "enable_image_enhancement": "true"}],
        ["POST", "/settings", {"section": "processing", "output_canvas_size": "250", "bg_removal_method": "none",
                               "enable_image_enhancement": "true"}],
    ])
    after = _settings(db)
    assert "ما تغيّر شي" in out[7]["flash"]["success"]
    for refused in out[8:10]:
        assert len(refused["flash"]["warnings"]) == 1 and "بين 300 و4000" in refused["flash"]["warnings"][0]
    out = out[:7]
    assert after["serper_api_key"] == "NEW-SERPER-KEY-1"
    assert after["gemini_api_key"] == before["gemini_api_key"] and after["photoroom_api_key"] == before["photoroom_api_key"]
    assert after["cloudinary_api_secret"] == "" and after["cloudinary_api_key"] == before["cloudinary_api_key"]
    assert after["cloudinary_cloud_name"] == "laqta"
    assert (after["output_canvas_size"], after["bg_removal_method"], after["enable_image_enhancement"]) == ("1200", "none", "true")
    # keys of other tabs are untouched by these forms (the old single form reset every checkbox it did not send)
    for key in ("auto_publish_brands", "auto_publish_enabled", "filter_competitors", "strict_brand_match",
                "search_engine", "gemini_model"):
        assert after[key] == before[key], key
    assert [r["location"].split("?tab=")[-1] for r in out] == ["keys"] * 4 + ["processing"] * 2 + ["keys"]
    assert len(out[5]["flash"]["warnings"]) == 2 and out[6]["flash"]["error"]
    for response in out:
        assert "NEW-SERPER-KEY-1" not in json.dumps(response, ensure_ascii=False)


def test_the_phase1_full_form_keeps_its_rules(app_env):
    db = app_env["db"]
    out = _kernel(app_env["env"], [["POST", "/settings", {
        "search_engine": "v9", "gemini_model": "gemini-2.0-flash-lite", "auto_publish_brands": " Almarai, ,Masafi,Almarai ",
        "google_search_cx": "cx-1", "cloudinary_cloud_name": "c", "strict_brand_match": "true", "serper_api_key": "",
        "auto_publish_enabled": "true"}]])
    after = _settings(db)
    assert after["search_engine"] == "v2" and after["gemini_model"] == "gemini-3.1-flash-lite"
    assert after["auto_publish_brands"] == "Almarai, Masafi" and after["google_search_cx"] == "cx-1"
    assert after["serper_api_key"] == SECRETS["serper_api_key"]
    assert after["filter_competitors"] == "false" and after["strict_brand_match"] == "true"
    # turning auto-publish on still needs a ready brand: ALMARAI is ready in review_stats
    assert after["auto_publish_enabled"] == "true"
    assert any("غير مدعوم" in w for w in out[0]["flash"]["warnings"])


def test_auto_publish_rules_on_the_server(app_env):
    db = app_env["db"]
    env = app_env["env"]
    out = _kernel(env, [
        ["POST", "/settings", {"section": "auto-publish", "auto_publish_enabled": "true"}],     # no brand yet
        ["POST", "/settings", {"section": "brand", "op": "enable", "brand": "AL ALALI"}],      # not ready
        ["POST", "/settings", {"section": "brand", "op": "enable", "brand": "almarai"}],       # ready
        ["POST", "/settings", {"section": "brand", "op": "enable", "brand": "ALMARAI"}],       # already on
    ])
    assert out[0]["flash"]["warnings"] and "ماركة جاهزة" in out[0]["flash"]["warnings"][0]
    assert "مش جاهزة" in out[1]["flash"]["error"] and "من قبل" in out[3]["flash"]["success"]
    after = _settings(db)
    assert after["auto_publish_enabled"] == "false" and after["auto_publish_brands"] == "ALMARAI"

    out = _kernel(env, [
        ["POST", "/settings", {"section": "auto-publish", "auto_publish_enabled": "true"}],
        ["GET", "/settings?tab=auto-publish", {}],
        ["POST", "/settings", {"section": "brand", "op": "disable", "brand": "Almarai"}],
    ])
    assert out[0]["flash"]["warnings"] in (None, [])
    page = out[1]["body"]
    assert re.search(r'name="auto_publish_enabled"[^>]*checked', page)
    assert "data-lq-confirm=" in page and "إيقاف" in page
    # the last brand switched off also switches auto-publish off, and says so
    assert "طفينا النشر الآلي كله" in out[2]["flash"]["success"]
    after = _settings(db)
    assert after["auto_publish_enabled"] == "false" and after["auto_publish_brands"] == ""

    # the bridge is down: nothing can be turned on, the page says why (and does not call an enabled brand unproven)
    _put(db, {"auto_publish_brands": "ALMARAI"})
    down = _kernel(dict(env, LQ_STUB_MODE="down"), [
        ["POST", "/settings", {"section": "brand", "op": "enable", "brand": "ALMARAI"}],
        ["POST", "/settings", {"section": "auto-publish", "auto_publish_enabled": "true"}],
        ["GET", "/settings?tab=auto-publish", {}],
    ])
    assert "ما قدرنا نتأكد" in down[0]["flash"]["error"] and "ما قدرنا نتأكد" in down[1]["flash"]["warnings"][0]
    assert "ما قدرنا نحسب دقة الماركات" in down[2]["body"]
    assert "بدون دليل كافٍ" not in down[2]["body"] and "بدون مراجعات" not in down[2]["body"]
    assert "ALMARAI" in down[2]["body"] and 'name="op" value="disable"' in down[2]["body"]
    assert re.search(r'name="auto_publish_enabled"[^>]*disabled', down[2]["body"])
    assert _settings(db)["auto_publish_enabled"] == "false"


def test_strict_lane_switch_is_refused_until_the_lane_is_ready(app_env):
    """«النشر الآلي لكل الماركات المؤكدة»: the server refuses the switch while lane strict is not ready (like an
    unready brand); once ready it turns on, and the main switch may then open without a listed brand."""
    db, env = app_env["db"], app_env["env"]
    out = _kernel(env, [
        ["POST", "/settings", {"section": "strict-lane", "auto_publish_strict_lane": "true"}],
        ["GET", "/settings?tab=auto-publish", {}],
    ])
    assert "لسا مش جاهزة" in out[0]["flash"]["error"]
    assert _settings(db)["auto_publish_strict_lane"] == "false"
    page = out[1]["body"]
    assert "النشر الآلي لكل الماركات المؤكدة" in page and "لسا ما راجعت ولا اقتراح بهالفئة." in page
    assert "القارئ مش متأكد بس العنوان بيأكد" in page
    assert re.search(r'name="auto_publish_strict_lane"[^>]*disabled', page)

    down = _kernel(dict(env, LQ_STUB_MODE="down"), [
        ["POST", "/settings", {"section": "strict-lane", "auto_publish_strict_lane": "true"}]])
    assert "ما قدرنا نتأكد" in down[0]["flash"]["error"] and _settings(db)["auto_publish_strict_lane"] == "false"

    ready = _kernel(dict(env, LQ_STUB_MODE="lane_ready"), [
        ["POST", "/settings", {"section": "strict-lane", "auto_publish_strict_lane": "true"}],
        ["POST", "/settings", {"section": "auto-publish", "auto_publish_enabled": "true"}],     # no brand listed
        ["GET", "/settings?tab=auto-publish", {}],
    ])
    assert "شغّلنا النشر الآلي لكل الماركات المؤكدة" in ready[0]["flash"]["success"]
    assert ready[1]["flash"]["warnings"] in (None, [])
    after = _settings(db)
    assert (after["auto_publish_strict_lane"], after["auto_publish_enabled"], after["auto_publish_brands"]) == (
        "true", "true", "")
    page = ready[2]["body"]
    assert "من 189 اقتراح بهالفئة، اعتمدت 189." in page
    assert re.search(r'name="auto_publish_strict_lane"[^>]*checked', page)

    off = _kernel(env, [["POST", "/settings", {"section": "strict-lane"}]])
    assert "وقّفنا" in off[0]["flash"]["success"] and _settings(db)["auto_publish_strict_lane"] == "false"


def test_health_lanes_endpoint_reads_the_bridge_once_and_caches(app_env):
    out = _kernel(app_env["env"], [["GET", "/api/system/review-lanes", {}], ["GET", "/api/system/review-lanes", {}]])
    first, second = (json.loads(o["body"]) for o in out)
    assert out[0]["status"] == 200 and first == second and set(first["lanes"]) == {"strict", "unsure", "other"}
    assert app_env["calls"].read_text().split().count("review_stats") == 1
    down = _kernel(dict(app_env["env"], LQ_STUB_MODE="down"), [["GET", "/api/system/review-lanes?refresh=1", {}]])
    assert down[0]["status"] == 500 and json.loads(down[0]["body"])["status"] == "error"


def test_extra_sources_form_saves_checks_and_shows_its_section(app_env):
    """«مصادر البحث الإضافية» on the «متقدم» tab: its own section, refused values keep the stored ones, no key shown."""
    db = app_env["db"]
    env = app_env["env"]
    _put(db, {"filter_competitors": "true", "expansion_enabled": "true", "expansion_max_calls": "4",
              "visual_search": "auto", "serpapi_lens_price_usd": "0.015", "gtin_policy": "evidence"})
    before = _settings(db)
    out = _kernel(env, [
        ["POST", "/settings", {"section": "sources", "expansion_enabled": "true", "expansion_max_calls": "6",
                               "visual_search": "serpapi", "serpapi_lens_price_usd": "0.02", "gtin_policy": "strict"}],
        ["GET", "/settings?tab=advanced", {}],
        # every value refused: the stored ones stay, one warning each; the switch left off turns the round off
        ["POST", "/settings", {"section": "sources", "expansion_max_calls": "12", "visual_search": "lens",
                               "serpapi_lens_price_usd": "abc", "gtin_policy": "hard"}],
    ])
    saved, page, refused = out
    assert saved["location"].endswith("?tab=advanced") and "الجولة الإضافية بتشتغل" in saved["flash"]["success"]
    assert page["status"] == 200, page["body"][:2000]
    body = page["body"]
    assert 'name="section" value="sources"' in body and "مصادر البحث الإضافية" in body
    assert re.search(r'name="expansion_enabled"[^>]*checked', body)
    assert re.search(r'name="expansion_max_calls"[^>]*value="6"', body)
    assert re.search(r'name="visual_search" value="serpapi" checked', body)
    assert re.search(r'name="gtin_policy" value="strict" checked', body)
    assert "مفتاح SerpApi: محفوظ" in body and "مفتاح SerpApi مش محفوظ" not in body
    assert SECRETS["serpapi_api_key"] not in body and SECRETS["serpapi_api_key"][-4:] not in body
    assert len(refused["flash"]["warnings"]) == 4, refused["flash"]
    assert "الجولة الإضافية مطفأة" in refused["flash"]["success"]
    after = _settings(db)
    assert (after["expansion_enabled"], after["expansion_max_calls"], after["visual_search"],
            after["serpapi_lens_price_usd"], after["gtin_policy"]) == ("false", "6", "serpapi", "0.02", "strict")
    for key in ("search_engine", "strict_brand_match", "filter_competitors", "serpapi_api_key", "auto_publish_enabled"):
        assert after[key] == before[key], key

    # SerpApi chosen without a saved key: the page says visual search is off until the key is added
    _put(db, {"serpapi_api_key": ""})
    page = _kernel(env, [["GET", "/settings?tab=advanced", {}]])[0]["body"]
    assert "مفتاح SerpApi مش محفوظ" in page and "مفتاح SerpApi: غير محفوظ" in page


def test_local_index_switch_and_pages_save_and_the_page_says_what_the_index_holds(app_env):
    """The local catalog index in the «مصادر البحث الإضافية» form: its switch, pages per product, and its status."""
    db = app_env["db"]
    env = app_env["env"]
    _sql(db, "DELETE FROM catalog_products")
    _sql(db, "DELETE FROM catalog_harvests")
    _put(db, {"expansion_enabled": "true", "local_index_enabled": "true", "local_index_max_pages": "3"})
    try:
        saved, empty_page, refused = _kernel(env, [
            ["POST", "/settings", {"section": "sources", "expansion_enabled": "true", "local_index_max_pages": "5"}],
            ["GET", "/settings?tab=advanced", {}],
            ["POST", "/settings", {"section": "sources", "expansion_enabled": "true", "local_index_enabled": "true",
                                   "local_index_max_pages": "12"}],
        ])
        after = _settings(db)
        # the switch left off turns the index off; 12 pages is refused and 5 stays
        assert (after["local_index_enabled"], after["local_index_max_pages"]) == ("true", "5")
        assert any("الفهرس المحلي" in w for w in refused["flash"]["warnings"]), refused["flash"]
        body = empty_page["body"]
        assert 'data-local-index' in body and re.search(r'name="local_index_max_pages"[^>]*value="5"', body)
        assert not re.search(r'name="local_index_enabled"[^>]*checked', body)
        assert "الفهرس لسا ما انبنى" in body and "build_catalog_index.py --discover" in body

        for i, store in enumerate(("lulu", "lulu", "spinneys")):
            _sql(db, "INSERT INTO catalog_products (store, url, url_hash, slug_text) VALUES (%s, %s, %s, %s)",
                 (store, f"https://example.ae/p/{i}", f"{i:040d}", "x"))
        _sql(db, "INSERT INTO catalog_harvests (store, started_at, finished_at, status) "
                 "VALUES ('lulu', NOW(), '2026-10-03 09:30:00', 'ok')")
        page = _kernel(env, [["GET", "/settings?tab=advanced", {}]])[0]["body"]
        assert re.search(r'name="local_index_enabled"[^>]*checked', page)
        assert "بالفهرس 3 صفحة منتج من 2 متجر" in page and "2026-10-03 09:30:00" in page
        assert "الفهرس لسا ما انبنى" not in page
    finally:
        _sql(db, "DELETE FROM catalog_products")
        _sql(db, "DELETE FROM catalog_harvests")


def test_unavailable_database_is_said(app_env):
    env = dict(app_env["env"], DB_PORT="1")                                  # nothing listens on port 1
    out = _kernel(env, [["GET", "/settings?tab=keys", {}], ["GET", "/settings?tab=sheet", {}],
                        ["GET", "/system-diagnostics", {}],
                        ["POST", "/settings", {"section": "serper", "serper_api_key": "X-KEY-1234567"}]])
    assert out[0]["status"] == 200 and "ما قدرنا نقرأ الإعدادات" in out[0]["body"] and "غير محفوظ" not in out[0]["body"]
    assert out[1]["status"] == 200 and "data-sheet-form" in out[1]["body"]
    assert out[2]["status"] == 200 and "data-health-page" in out[2]["body"]
    assert out[3]["status"] == 302 and "قاعدة البيانات" in out[3]["flash"]["error"]
