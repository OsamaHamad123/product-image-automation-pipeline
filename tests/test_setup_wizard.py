"""«جهّز لقطة»: the first-run setup wizard (SetupController, dashboard/setup.blade.php, public/js/setup.js, the bridge
action setup_brands) and its Home card.

- Structure: the page extends layouts.laqta, keeps its script and style in public/, every hook the script uses exists,
  no key value and no connection-check URL in the script, and the routes sit before the UI kit line.
- The bridge's brands check reads the Brands Mapping tab without ever creating it.
- The step views are pure PHP functions of the facts (Laravel booted, no database): each step's status, its rows and
  fixes, skipping, resuming at the first open step, the first-run plan and the Home card that never shows on a
  configured install.
- The Laravel app runs through its HTTP kernel against the MariaDB test database with a stub cli_bridge: live checks,
  progress kept in system_settings.setup_wizard, no stored key on any page or answer, and a truthful database-down state.
- public/js/setup.js runs under node: view functions and the controller with recorded requests.
"""

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
SETUP_VIEW = VIEWS / "dashboard" / "setup.blade.php"
HOME_CARD = VIEWS / "setup" / "home_card.blade.php"
HOME_VIEW = VIEWS / "dashboard" / "index.blade.php"
SETTINGS_VIEW = VIEWS / "dashboard" / "settings.blade.php"
SETUP_JS = DASH / "public" / "js" / "setup.js"
SETTINGS_JS = DASH / "public" / "js" / "settings.js"
SETUP_CSS = DASH / "public" / "css" / "pages" / "setup.css"
HOME_CSS = DASH / "public" / "css" / "pages" / "home.css"
LAQTA_CSS = DASH / "public" / "css" / "laqta.css"
CONTROLLER = DASH / "app" / "Http" / "Controllers" / "SetupController.php"
ROUTES = DASH / "routes" / "web.php"
VENDOR_AUTOLOAD = DASH / "vendor" / "autoload.php"

PHP = shutil.which("php")
NODE = shutil.which("node")
NEEDS_LARAVEL = pytest.mark.skipif(PHP is None or not VENDOR_AUTOLOAD.exists(), reason="php or dashboard/vendor is missing")
NEEDS_NODE = pytest.mark.skipif(NODE is None, reason="node is not installed")
STEPS = ["sheet", "keys", "brands", "publish", "run"]


def read(path):
    return path.read_text(encoding="utf-8")


def _fake_secret(tag):
    """A key-shaped stand-in built at run time (no credential-looking literal in the source)."""
    return "-".join(("tst", tag.lower(), "7f3c9a1e"))


# ---------------------------------------------------------------------------
# Structure
# ---------------------------------------------------------------------------

def test_routes_sit_before_the_ui_kit_line():
    routes = read(ROUTES)
    c = "\\App\\Http\\Controllers\\SetupController"
    for line in (f"Route::get('/setup', [{c}::class, 'page'])->name('dashboard.setup');",
                 f"Route::get('/api/setup/state', [{c}::class, 'state']);",
                 f"Route::post('/api/setup/check', [{c}::class, 'check']);",
                 f"Route::post('/api/setup/progress', [{c}::class, 'progress']);"):
        assert line in routes, line
    assert routes.index("'/api/setup/progress'") < routes.index("Route::view('/ui-kit'")


def test_the_page_extends_the_layout_and_loads_its_own_files():
    view = read(SETUP_VIEW)
    assert "@extends('layouts.laqta')" in view and "@section('title', 'جهّز لقطة')" in view
    assert "@section('lq_nav', 'settings')" in view
    # settings.js first (its sheet wording, window.LaqtaSettings), then the wizard
    assert re.findall(r"asset\('js/([a-z-]+\.js)'\)", view) == ["settings.js", "setup.js"]
    assert re.findall(r"asset\('css/pages/([a-z-]+\.css)'\)", view) == ["setup.css"]
    assert not inline_scripts(view) and not re.search(r"(?:href|src)=\"(?:https?:)?//", view)
    assert '<script type="application/json" id="lq-setup-initial">@json($setup)</script>' in view
    # one body per step, and the Settings link back to the wizard
    for key in STEPS:
        assert f"@case('{key}')" in view, key
    assert "route('dashboard.setup')" in read(SETTINGS_VIEW) and "جهّز لقطة خطوة بخطوة" in read(SETTINGS_VIEW)
    assert "@include('setup.home_card')" in read(HOME_VIEW) and "'setupCard' => SetupController::homeCard()," in read(
        DASH / "app" / "Http" / "Controllers" / "OverviewController.php")


def test_every_hook_the_script_uses_exists_and_the_script_stays_plain():
    view, js = read(SETUP_VIEW), read(SETUP_JS)
    used = set(re.findall(r"\$\('([a-z-]+)'\)", js))
    missing = used - set(re.findall(r'data-setup="([a-z-]+)"', view))
    assert used and not missing, sorted(missing)
    for part in set(re.findall(r"part\(section, '([a-z]+)'\)", js)):
        assert f'data-setup-part="{part}"' in view, part
    for hook in ("data-setup-page", "data-setup-step", "data-setup-tab", "data-setup-tab-state", "data-setup-go",
                 "data-setup-skip", 'id="lq-setup-initial"'):
        assert hook in view, hook
    assert "innerHTML" not in js and "insertAdjacentHTML" not in js and "eval(" not in js and "new Function" not in js
    # the connection check URL stays in health.js only (test_diagnostics_on_demand): the wizard asks its own endpoint
    assert "/api/system/run-diagnostics" not in js
    for url in ("'/api/setup/state'", "'/api/setup/check'", "'/api/setup/progress'", "'/api/sheet/save'",
                "'/api/system/publish-check'", "'/api/run-all'", "'/api/run/live'"):
        assert url in js, url


@NEEDS_NODE
@pytest.mark.parametrize("path", [SETUP_JS], ids=lambda p: p.name)
def test_the_script_parses(path):
    result = subprocess.run([NODE, "--check", str(path)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr


CLASS_RE = re.compile(r"(?<![\w-])lq-[a-z0-9]+(?:(?:__|--|-)[a-z0-9]+)*")


def _css_classes(*paths):
    classes = set()
    for p in paths:
        css = re.sub(r"/\*.*?\*/", "", read(p), flags=re.DOTALL)
        classes |= set(re.findall(r"\.(lq-[a-z0-9]+(?:(?:__|--|-)[a-z0-9]+)*)", css))
    return classes


def test_classes_exist_and_the_css_follows_the_rules():
    defined = _css_classes(LAQTA_CSS, SETUP_CSS)
    ids = {"lq-setup-initial", "lq-page-setup", "lq-home-setup-title"}
    for path, extra in ((SETUP_VIEW, set()), (SETUP_JS, set()), (HOME_CARD, _css_classes(HOME_CSS))):
        used = {c.rstrip("-") for c in CLASS_RE.findall(read(path))} - ids - {"lq-dot"}
        missing = sorted(c for c in used if c not in defined | extra)
        assert not missing, (path.name, missing)
    tokens = set(re.findall(r"(--lq-[a-z0-9-]+)\s*:", read(LAQTA_CSS)))
    for css in (SETUP_CSS, HOME_CSS):
        text = read(css)
        assert not set(re.findall(r"var\((--lq-[a-z0-9-]+)", text)) - tokens
        assert not re.search(r"--lq-[a-z0-9-]+\s*:", text)
        for physical in ("margin-left", "margin-right", "padding-left", "padding-right", "border-left", "border-right"):
            assert physical not in text, physical
        assert not re.search(r"(?<![-\w])(left|right)\s*:", text)
        assert "@media (max-width: 980px)" in text and "@media (max-width: 560px)" in text


def test_no_emoji_no_raw_codes_and_the_required_columns_match_the_settings_script():
    emoji = re.compile("[\U0001F300-\U0001FAFF☀-➿️]")
    for path in (SETUP_VIEW, HOME_CARD, SETUP_JS, SETUP_CSS, CONTROLLER):
        assert not emoji.findall(read(path)), path.name
    required = re.findall(r"\{ key: '([a-z_]+)', label: '[^']+', required: true \}", read(SETTINGS_JS))
    php = re.search(r"SHEET_REQUIRED = \[(.*?)\];", read(CONTROLLER)).group(1)
    assert required == re.findall(r"'([a-z_]+)' =>", php) == ["name", "brand"]


# ---------------------------------------------------------------------------
# The bridge: setup_brands reads the Brands Mapping tab, never creates it
# ---------------------------------------------------------------------------

class _Worksheet:
    def __init__(self, title, rows):
        self.title, self.rows = title, rows

    def get_all_values(self):
        return self.rows


class _Spreadsheet:
    def __init__(self, sheets):
        self.sheets = sheets
        self.added = []

    def worksheets(self):
        return list(self.sheets)

    def worksheet(self, title):
        return next(ws for ws in self.sheets if ws.title == title)

    def add_worksheet(self, **kwargs):           # pragma: no cover - must never be called
        self.added.append(kwargs)


def _bridge_with(monkeypatch, sheet):
    import cli_bridge
    import google_sheets
    monkeypatch.setattr(google_sheets, "get_sheets_client", lambda: object())
    monkeypatch.setattr(google_sheets, "_open_spreadsheet", lambda client, name: sheet)
    return cli_bridge


def test_setup_brands_counts_the_brands_of_the_tab(monkeypatch):
    rows = [["Brand", "Synonyms"], ["Almarai", "المراعي"], ["Masafi", ""], ["", "orphan synonyms"], ["Al Ain", "العين"]]
    sheet = _Spreadsheet([_Worksheet("Products", [["Product Name"]]), _Worksheet("Brands Mapping", rows)])
    bridge = _bridge_with(monkeypatch, sheet)
    out = bridge.ACTIONS["setup_brands"]({})
    assert out == {"status": "success", "found": True, "title": "Brands Mapping", "brands": 3}


def test_setup_brands_says_a_missing_tab_and_never_creates_it(monkeypatch):
    sheet = _Spreadsheet([_Worksheet("Products", [["Product Name"]])])
    bridge = _bridge_with(monkeypatch, sheet)
    assert bridge.action_setup_brands({}) == {"status": "success", "found": False, "title": "Brands Mapping", "brands": 0}
    assert sheet.added == []


def test_setup_brands_failure_is_a_fixed_message(monkeypatch):
    import cli_bridge
    import google_sheets

    def boom(client, name):
        raise RuntimeError("sheet https://docs.google.com/x?key=" + _fake_secret("leak"))

    monkeypatch.setattr(google_sheets, "get_sheets_client", lambda: object())
    monkeypatch.setattr(google_sheets, "_open_spreadsheet", boom)
    out = cli_bridge.action_setup_brands({})
    assert out["status"] == "failed" and "leak" not in json.dumps(out) and "Brands Mapping" in out["error"]
    monkeypatch.setattr(google_sheets, "get_sheets_client", lambda: None)
    assert cli_bridge.action_setup_brands({})["status"] == "failed"


def test_setup_brands_is_registered_before_the_last_two_actions():
    import cli_bridge
    assert cli_bridge.ACTIONS["setup_brands"] is cli_bridge.action_setup_brands
    assert list(cli_bridge.ACTIONS)[-2:] == ["ops_health", "run_control"]


# ---------------------------------------------------------------------------
# The step views under PHP (Laravel booted, no database)
# ---------------------------------------------------------------------------

def _php(code, env=None):
    dash = str(DASH).replace("\\", "/")
    script = (f"<?php\nrequire '{dash}/vendor/autoload.php';\n$app = require '{dash}/bootstrap/app.php';\n"
              "$app->make(Illuminate\\Contracts\\Console\\Kernel::class)->bootstrap();\n"
              "use App\\Http\\Controllers\\SetupController as S;\n$out = [];\n" + code
              + "\necho json_encode($out, JSON_UNESCAPED_UNICODE);\n")
    with tempfile.NamedTemporaryFile("w", suffix=".php", delete=False, encoding="utf-8") as fh:
        fh.write(script)
        path = fh.name
    compiled = tempfile.mkdtemp()
    run_env = dict(os.environ, APP_ENV="testing", APP_KEY="base64:" + "A" * 43 + "=", CACHE_STORE="array",
                   LOG_CHANNEL="stderr", VIEW_COMPILED_PATH=compiled, DB_CONNECTION="mariadb", DB_HOST="127.0.0.1",
                   DB_PORT="1", DB_DATABASE="automation_test_offline", **(env or {}))
    try:
        result = subprocess.run([PHP, path], cwd=DASH, env=run_env, capture_output=True, text=True, timeout=120,
                                encoding="utf-8")
    finally:
        os.unlink(path)
        shutil.rmtree(compiled, ignore_errors=True)
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-3000:]
    return json.loads(result.stdout)


def _php_value(obj):
    """A Python value as a PHP literal (json_decode of its JSON)."""
    return "json_decode(" + json.dumps(json.dumps(obj, ensure_ascii=False), ensure_ascii=False).replace("$", "\\$") + ", true)"


KEYS_ALL_OK = [
    {"id": "serper", "saved": True, "state": "محفوظ · يعمل", "tone": "success"},
    {"id": "gemini", "saved": True, "state": "محفوظ · يعمل", "tone": "success"},
    {"id": "photoroom", "saved": True, "state": "محفوظ · يعمل", "tone": "success"},
    {"id": "cloudinary", "saved": True, "state": "محفوظ · يعمل", "tone": "success"},
    {"id": "anthropic", "saved": False, "state": "مش مضبوط · اختياري", "tone": "muted"},
]
SHEET = {"url": "https://docs.google.com/spreadsheets/d/" + "a" * 30 + "/edit", "url_is_default": False, "tab": "Products",
         "credentials": {"exists": True, "email": "laqta@demo-project.iam.gserviceaccount.com"}}


def _facts(**over):
    base = {"db": True, "state": {"checks": {}, "skipped": {}, "done_at": None}, "sheet": SHEET, "keys": KEYS_ALL_OK,
            "bg_method": "photoroom", "removebg_key": False, "publish": None, "last_run": None}
    base.update(over)
    return base


def _views(cases):
    """{name: SetupController::view(facts)} for {name: facts}."""
    code = "".join(f"$out[{json.dumps(name)}] = S::view({_php_value(facts)});\n" for name, facts in cases.items())
    return _php(code)


def _step(view, key):
    return next(s for s in view["steps"] if s["key"] == key)


def _fp(sheet):
    import hashlib
    return hashlib.sha1((sheet["url"].strip() + "\n" + sheet["tab"].strip()).encode()).hexdigest()[:16]


SHEET_OK = {"status": "ok", "at": 1000, "for": _fp(SHEET), "error": "", "headers": 12, "missing": []}
BRANDS_OK = {"status": "ok", "at": 1000, "for": _fp(SHEET), "found": True, "brands": 42, "error": ""}
PUBLISH_OK = {"ok": True, "overall": "ok", "finished_at": "2026-10-06T08:00:00+00:00", "summary_ar": "النشر شغّال.",
              "sample": {"kind": "bundled"}, "notes": [],
              "steps": [{"key": k, "status": "ok", "ms": 900, "title_ar": "", "detail_ar": "", "action_ar": "", "code": ""}
                        for k in ("download", "process", "upload", "sheet")]}
RUN_DONE = {"run_trigger": "dashboard", "started_at": "2026-10-06T09:00:00+00:00", "outcome": "done", "attempts": 1,
            "ready_for_review": 4, "not_found": 1, "report_json": {"reason_text": ""}}


@NEEDS_LARAVEL
def test_a_fresh_install_starts_at_the_sheet_with_a_clear_fix():
    fresh = _facts(sheet={"url": "automation sheet", "url_is_default": True, "tab": "",
                          "credentials": {"exists": False, "email": None}},
                   keys=[dict(k, saved=False, state="غير محفوظ", tone="warning") for k in KEYS_ALL_OK])
    view = _views({"fresh": fresh})["fresh"]
    assert [s["key"] for s in view["steps"]] == STEPS and [s["n"] for s in view["steps"]] == [1, 2, 3, 4, 5]
    assert [s["status"] for s in view["steps"]] == ["fail", "todo", "todo", "todo", "todo"]
    assert view["current"] == "sheet" and view["ready"] is False and view["progress"] == {"ok": 0, "total": 5}
    sheet = _step(view, "sheet")
    rows = {r["key"]: r for r in sheet["rows"]}
    assert rows["credentials"]["state"] == "مش موجود" and "credentials.json" in rows["credentials"]["fix"]
    assert rows["link"]["state"] == "ما في رابط محفوظ" and "احفظ وافحص" in rows["link"]["fix"]
    assert (sheet["label"], sheet["tone"]) == ("بدها تصليح", "danger")
    keys = _step(view, "keys")
    assert [r["key"] for r in keys["rows"]] == ["serper", "gemini", "bg", "cloudinary"]
    assert all(r["state"] == "غير محفوظ" and r["href"] == "/settings?tab=keys" for r in keys["rows"])


@NEEDS_LARAVEL
def test_each_step_reports_its_live_check():
    stale = dict(SHEET_OK, **{"for": "0" * 16})
    out = _views({
        "ok": _facts(state={"checks": {"sheet": SHEET_OK, "brands": BRANDS_OK}, "skipped": {}, "done_at": None},
                     publish=PUBLISH_OK, last_run=RUN_DONE),
        "stale": _facts(state={"checks": {"sheet": stale}, "skipped": {}, "done_at": None}),
        "open": _facts(state={"checks": {"sheet": dict(SHEET_OK, status="fail", error="open")}, "skipped": {}, "done_at": None}),
        "columns": _facts(state={"checks": {"sheet": dict(SHEET_OK, status="fail", error="columns", missing=["name"])},
                                 "skipped": {}, "done_at": None}),
    })
    ok = out["ok"]
    assert [s["status"] for s in ok["steps"]] == ["ok"] * 5 and ok["ready"] is True and ok["current"] is None
    assert _step(ok, "sheet")["rows"][-1]["state"] == "اسم المنتج والماركة موجودين"
    assert _step(ok, "brands")["rows"][-1]["state"] == "42 ماركة" and _step(ok, "brands")["href"] == "/batch-automation#run-brands"
    run = _step(ok, "run")
    assert run["rows"][0]["href"] == "/catalog?mode=bulk" and "بانتظار المراجعة 4" in run["rows"][0]["fix"]
    # a check of another link does not describe the saved one
    read_row = {r["key"]: r for r in _step(out["stale"], "sheet")["rows"]}["read"]
    assert _step(out["stale"], "sheet")["status"] == "todo" and read_row["state"] == "ما انفحص بعد آخر تغيير"
    opened = {r["key"]: r for r in _step(out["open"], "sheet")["rows"]}["read"]
    assert _step(out["open"], "sheet")["status"] == "fail" and SHEET["credentials"]["email"] in opened["fix"]
    columns = {r["key"]: r for r in _step(out["columns"], "sheet")["rows"]}["columns"]
    assert columns["state"] == "ناقص: اسم المنتج" and "Product Name" in columns["fix"]


@NEEDS_LARAVEL
def test_keys_step_by_saved_state_last_check_and_background_method():
    failing = [dict(k, state="محفوظ · ما بيرد", tone="danger") if k["id"] == "serper" else k for k in KEYS_ALL_OK]
    untested = [dict(k, state="محفوظ · ما انفحص", tone="muted") if k["id"] == "gemini" else k for k in KEYS_ALL_OK]
    out = _views({
        "ok": _facts(),
        "failing": _facts(keys=failing),
        "untested": _facts(keys=untested),
        "removebg_missing": _facts(bg_method="remove_bg_api", removebg_key=False),
        "removebg": _facts(bg_method="remove_bg_api", removebg_key=True,
                           keys=[k for k in KEYS_ALL_OK if k["id"] != "photoroom"]),
        "local": _facts(bg_method="rembg", keys=[k for k in KEYS_ALL_OK if k["id"] != "photoroom"]),
    })
    assert _step(out["ok"], "keys")["status"] == "ok"
    failing_step = _step(out["failing"], "keys")
    serper = failing_step["rows"][0]
    assert failing_step["status"] == "fail" and serper["tone"] == "danger" and "رصيد" in serper["fix"]
    assert _step(out["untested"], "keys")["status"] == "todo" and "افحص المفاتيح" in _step(out["untested"], "keys")["rows"][1]["fix"]
    bg = {r["key"]: r for r in _step(out["removebg_missing"], "keys")["rows"]}["bg"]
    assert bg["label"].startswith("remove.bg") and "REMOVE_BG_API_KEY" in bg["fix"] and bg["href"] == "/settings?tab=processing"
    assert _step(out["removebg_missing"], "keys")["status"] == "todo"
    assert _step(out["removebg"], "keys")["status"] == "ok" and _step(out["local"], "keys")["status"] == "ok"
    assert {r["key"]: r for r in _step(out["local"], "keys")["rows"]}["bg"]["state"] == "محلي على هالجهاز: ما بدو مفتاح"
    # never a key value: the rows carry words only
    assert "tst-" not in json.dumps(out)


@NEEDS_LARAVEL
def test_publish_run_skip_and_resume():
    failed = dict(PUBLISH_OK, overall="fail", summary_ar="الرفع ما زبط.",
                  steps=[dict(s, status="fail", action_ar="تأكد من مفاتيح Cloudinary.") if s["key"] == "upload" else s
                         for s in PUBLISH_OK["steps"]])
    warned = dict(PUBLISH_OK, overall="warn")
    started = {"status": "started", "at": 1_900_000_000, "rows": "2-6"}
    out = _views({
        "failed": _facts(publish=failed),
        "warned": _facts(publish=warned),
        "running": _facts(state={"checks": {"run": started}, "skipped": {}, "done_at": None}, last_run=RUN_DONE),
        "run_failed": _facts(last_run=dict(RUN_DONE, outcome="outage")),
        "skipped": _facts(state={"checks": {"sheet": SHEET_OK}, "skipped": {"brands": 5, "sheet": 5}, "done_at": None}),
    })
    publish = _step(out["failed"], "publish")
    assert publish["status"] == "fail" and publish["summary"] == "الرفع ما زبط."
    assert {r["key"]: r for r in publish["rows"]}["upload"]["fix"] == "تأكد من مفاتيح Cloudinary."
    assert _step(out["warned"], "publish")["status"] == "ok"
    run = _step(out["running"], "run")
    # a report older than the run started from the wizard does not describe it
    assert run["status"] == "running" and run["started_rows"] == "2-6" and run["label"] == "شغّال"
    assert _step(out["run_failed"], "run")["status"] == "fail"
    skipped = out["skipped"]
    # skipping never hides a step that is done; the next open step is where the wizard resumes
    assert _step(skipped, "sheet")["status"] == "ok" and _step(skipped, "brands")["status"] == "skipped"
    assert _step(skipped, "brands")["skipped"] is True and skipped["current"] == "publish"


@NEEDS_LARAVEL
def test_live_check_results_as_they_are_saved():
    out = _php(f"""
$sheet = {_php_value(SHEET)};
$out['no_credentials'] = S::sheetCheck(array_merge($sheet, ['credentials' => ['exists' => false]]), null, 5);
$out['open'] = S::sheetCheck($sheet, ['status' => 'failed', 'error' => 'x'], 5);
$out['empty'] = S::sheetCheck($sheet, ['status' => 'success', 'headers' => [], 'columns' => []], 5);
$out['columns'] = S::sheetCheck($sheet, ['status' => 'success', 'headers' => ['Brand'], 'columns' => ['name' => -1, 'brand' => 0]], 5);
$out['ok'] = S::sheetCheck($sheet, ['status' => 'success', 'headers' => ['Product Name', 'Brand'], 'columns' => ['name' => 0, 'brand' => 1]], 5);
$out['brands_down'] = S::brandsCheck(['status' => 'failed'], $sheet, 5);
$out['brands_none'] = S::brandsCheck(['status' => 'success', 'found' => false, 'brands' => 0], $sheet, 5);
$out['brands_empty'] = S::brandsCheck(['status' => 'success', 'found' => true, 'brands' => 0], $sheet, 5);
$out['brands_ok'] = S::brandsCheck(['status' => 'success', 'found' => true, 'brands' => 7], $sheet, 5);
$out['fp'] = S::sheetFingerprint($sheet);
""")
    assert out["fp"] == _fp(SHEET)
    assert (out["no_credentials"]["status"], out["no_credentials"]["error"]) == ("fail", "credentials")
    assert out["open"]["error"] == "open" and out["empty"]["error"] == "empty"
    assert out["columns"]["missing"] == ["name"] and out["columns"]["status"] == "fail"
    assert out["ok"] == {"status": "ok", "at": 5, "for": _fp(SHEET), "error": "", "headers": 2, "missing": []}
    assert out["brands_down"]["status"] == "fail" and out["brands_none"]["status"] == "todo"
    assert out["brands_empty"]["status"] == "todo" and out["brands_ok"]["status"] == "ok" and out["brands_ok"]["brands"] == 7


@NEEDS_LARAVEL
def test_the_first_run_takes_the_first_five_rows_that_need_an_image():
    sheet = [{"row_number": n, "product_name": f"P{n}", "brand": "B", "existing_image_link": ""} for n in range(2, 14)]
    sheet[1]["existing_image_link"] = "https://res.cloudinary.com/demo/image/upload/p3.png"     # row 3: has an image
    sheet[3]["needs_review"] = True                                                            # row 5: old review link
    sheet[6]["product_name"] = ""                                                              # row 8: empty
    queue = {6: {"status": "ready_for_review", "sku_key": None, "lease_held": False},
             40: {"status": "pending", "sku_key": None, "lease_held": False}}                 # a leftover row
    out = _php(f"""
$sheet = {_php_value(sheet)};
$queue = {_php_value({str(k): v for k, v in queue.items()})};
$queue = array_combine(array_map('intval', array_keys($queue)), array_values($queue));
$out['rows'] = S::firstRows($sheet, $queue);
$out['plan'] = S::planFor($sheet, $queue);
$out['none'] = S::planFor([['row_number' => 2, 'product_name' => 'A', 'existing_image_link' => 'x']], []);
$out['texts'] = [S::rowFilterText([2, 3, 4, 9]), S::rowFilterText([7]), S::rowFilterText([2, 4, 5, 6, 10, 11])];
$out['products'] = [S::products(1), S::products(2), S::products(5), S::products(12)];
""")
    assert out["rows"] == [2, 4, 7, 9, 10]
    plan = out["plan"]
    assert plan["status"] == "success" and plan["rows"] == "2, 4, 7, 9-10" and plan["count"] == 5 and plan["leftovers"] == 1
    assert plan["message"] == ("رح ندوّر على صور 5 منتجات من الشيت (الصفوف 2, 4, 7, 9-10). "
                               "وبيمشي معهم كمان اللي باقي بالطابور من تشغيل قبل (منتج واحد).")
    assert out["none"]["status"] == "empty" and out["none"]["rows"] == "" and "فيك تخلّص التجهيز" in out["none"]["message"]
    assert out["texts"] == ["2-4, 9", "7", "2, 4-6, 10-11"]
    assert out["products"] == ["منتج واحد", "منتجين", "5 منتجات", "12 منتج"]


@NEEDS_LARAVEL
def test_the_home_card_shows_only_while_setup_is_incomplete():
    done = {"checks": {}, "skipped": {}, "done_at": 1_800_000_000}
    out = _php(f"""
$facts = {_php_value(_facts())};
$out['card'] = S::homeCard($facts);
$out['db_down'] = S::homeCard(array_merge($facts, ['db' => false]));
$out['done'] = S::homeCard(array_merge($facts, ['state' => {_php_value(done)}]));
$out['configured'] = S::homeCard(array_merge($facts, ['last_run' => {_php_value(RUN_DONE)}]));
$out['is_configured'] = [S::configured(array_merge($facts, ['last_run' => {_php_value(RUN_DONE)}])),
    S::configured($facts),
    S::configured(array_merge($facts, ['last_run' => {_php_value(RUN_DONE)}, 'keys' => []]))];
""")
    card = out["card"]
    assert card["ok"] == 1 and card["total"] == 5 and card["next"] == "ربط الشيت" and card["next_n"] == 1
    assert card["started"] is True and [s["status"] for s in card["steps"]] == ["todo", "ok", "todo", "todo", "todo"]
    # unknown (no database), finished, or a configured install that already ran: no card, nothing waits on it
    assert out["db_down"] is None and out["done"] is None and out["configured"] is None
    assert out["is_configured"] == [True, False, False]


# ---------------------------------------------------------------------------
# The Laravel app through its HTTP kernel (MariaDB test database, stub bridge)
# ---------------------------------------------------------------------------

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


TOUCHED = ["setup_wizard", "serper_api_key", "gemini_api_key", "photoroom_api_key", "cloudinary_api_key",
           "cloudinary_api_secret", "cloudinary_cloud_name", "bg_removal_method"]
SHEET_URL = "https://docs.google.com/spreadsheets/d/" + "b" * 32 + "/edit"
PRODUCTS = [{"row_number": n, "product_name": f"ALMARAI MILK {n}", "brand": "Almarai", "barcode": "", "sku_key": f"k{n}",
             "existing_image_link": "https://res.cloudinary.com/demo/p.png" if n == 3 else ""} for n in range(2, 12)]
FIXTURE = {
    "sheet-preview": {"status": "success", "headers": ["Product Name", "Brand", "Barcode"], "rows": [["A", "B", "1"]],
                      "columns": {"name": 0, "brand": 1, "barcode": 2, "link": -1}},
    "setup_brands": {"status": "success", "found": True, "title": "Brands Mapping", "brands": 12},
    "get_products": {"status": "success", "products": PRODUCTS},
}
DIAGNOSTICS = {"status": "success", "all_ok": True, "checked_at": "2099-01-01T00:00:00+00:00",
               "services": {k: {"status": "online", "is_critical": True, "details": ""}
                            for k in ("google_sheets", "serper", "gemini", "photoroom", "cloudinary")}}


RESULT_FILES = [ROOT / "temp" / "nightly" / "last_report.json", ROOT / "temp" / "publish_check_last.json",
                ROOT / "temp" / "diagnostics_last.json"]


@pytest.fixture
def aside_results():
    """The saved results the steps read (last run, last rehearsal, last connection check) are set aside for the test
    and put back after it, so a checkout that holds real ones gives the same answers."""
    kept = {p: p.read_bytes() for p in RESULT_FILES if p.exists()}
    for p in kept:
        p.unlink()
    yield
    for p in RESULT_FILES:                      # what the test wrote goes, what was there comes back
        if p.exists():
            p.unlink()
    for p, data in kept.items():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)


@pytest.fixture
def setup_app(mariadb_or_skip, tmp_path, aside_results):
    if PHP is None or not VENDOR_AUTOLOAD.exists():
        pytest.skip("php or dashboard/vendor is not installed")
    db = mariadb_or_skip
    saved = {r["key"]: r["value"] for r in _sql(db, "SELECT `key`, `value` FROM system_settings")
             if r["key"] in TOUCHED}
    _sql(db, "DELETE FROM system_settings WHERE `key` IN (" + ", ".join(["%s"] * len(TOUCHED)) + ")", tuple(TOUCHED))
    secrets = {"serper_api_key": _fake_secret("SERPER"), "gemini_api_key": _fake_secret("GEMINI"),
               "photoroom_api_key": _fake_secret("PHOTO"), "cloudinary_api_key": _fake_secret("CLKEY"),
               "cloudinary_api_secret": _fake_secret("CLSEC")}
    for key, value in dict(secrets, cloudinary_cloud_name="laqta-test", bg_removal_method="photoroom").items():
        _sql(db, "INSERT INTO system_settings (`key`, `value`, updated_at) VALUES (%s, %s, NOW() - INTERVAL 1 DAY)",
             (key, value))
    _sql(db, "DELETE FROM automation_queue WHERE `row_number` BETWEEN 2 AND 11")
    calls = tmp_path / "calls.txt"
    stub = tmp_path / "stub_bridge.py"
    stub.write_text(
        "import base64, json, os, sys\n"
        f"FIXTURE = json.loads({json.dumps(json.dumps(FIXTURE, ensure_ascii=False))})\n"
        "action = sys.argv[1] if len(sys.argv) > 1 else ''\n"
        "params = json.loads(base64.b64decode(sys.argv[2]).decode('utf-8')) if len(sys.argv) > 2 else {}\n"
        f"open({str(calls)!r}, 'a').write(json.dumps([action, params]) + '\\n')\n"
        "if os.environ.get('LQ_STUB_MODE') == 'down':\n"
        "    print(json.dumps({'status': 'failed', 'error': 'stub down'}))\n"
        "else:\n"
        "    print(json.dumps(FIXTURE.get(action, {'status': 'error', 'error': 'unexpected action'}), ensure_ascii=False))\n",
        encoding="utf-8")
    credentials = tmp_path / "credentials.json"
    credentials.write_text(json.dumps({"type": "service_account", "client_email": "laqta@demo-project.iam.gserviceaccount.com"}),
                           encoding="utf-8")
    compiled = tmp_path / "views"
    compiled.mkdir()
    env = dict(os.environ)
    env.update({
        "APP_ENV": "testing", "APP_KEY": "base64:" + "A" * 43 + "=", "APP_DEBUG": "true", "SESSION_DRIVER": "array",
        "CACHE_STORE": "array", "LOG_CHANNEL": "stderr", "VIEW_COMPILED_PATH": str(compiled),
        "DB_CONNECTION": "mariadb", "DB_HOST": os.getenv("DB_HOST", "127.0.0.1"), "DB_PORT": os.getenv("DB_PORT", "3306"),
        "DB_DATABASE": os.environ["DB_DATABASE"], "DB_USERNAME": os.getenv("DB_USERNAME", "root"),
        "DB_PASSWORD": os.getenv("DB_PASSWORD", ""), "CLI_BRIDGE_PATH": str(stub), "PYTHON_PATH": sys.executable,
        "CREDENTIALS_FILE": str(credentials), "SPREADSHEET_NAME_OR_URL": SHEET_URL, "SPREADSHEET_TAB_NAME": "Products",
    })
    for name in ("SERPER_API_KEY", "GEMINI_API_KEY", "PHOTOROOM_API_KEY", "CLOUDINARY_API_KEY", "CLOUDINARY_API_SECRET",
                 "CLOUDINARY_CLOUD_NAME", "BG_REMOVAL_METHOD", "REMOVE_BG_API_KEY"):
        env.pop(name, None)
    yield {"env": env, "calls": calls, "db": db, "secrets": secrets, "tmp": tmp_path}
    _sql(db, "DELETE FROM system_settings WHERE `key` IN (" + ", ".join(["%s"] * len(TOUCHED)) + ")", tuple(TOUCHED))
    for key, value in saved.items():
        _sql(db, "INSERT INTO system_settings (`key`, `value`) VALUES (%s, %s)", (key, value))


def _kernel(env, requests):
    dash = str(DASH).replace("\\", "/")
    script = f"""<?php
require '{dash}/vendor/autoload.php';
$app = require '{dash}/bootstrap/app.php';
$kernel = $app->make(Illuminate\\Contracts\\Http\\Kernel::class);
$out = [];
foreach (json_decode($argv[1], true) as [$method, $path, $params]) {{
    $request = Illuminate\\Http\\Request::create($path, $method, $params, [], [], ['HTTP_ACCEPT' => 'text/html,application/json']);
    $response = $kernel->handle($request);
    $out[] = ['status' => $response->getStatusCode(), 'body' => $response->getContent()];
    $kernel->terminate($request, $response);
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


def _calls(app):
    return [json.loads(line) for line in app["calls"].read_text(encoding="utf-8").splitlines()] if app["calls"].exists() else []


def _state(db):
    rows = _sql(db, "SELECT `value` FROM system_settings WHERE `key` = 'setup_wizard'")
    return json.loads(rows[0]["value"]) if rows else None


def test_the_page_opens_at_the_first_open_step_and_checks_nothing(setup_app):
    page, keys, state, home, settings = _kernel(setup_app["env"], [
        ["GET", "/setup", {}], ["GET", "/setup?step=keys", {}], ["GET", "/api/setup/state", {}], ["GET", "/", {}],
        ["GET", "/settings", {}]])
    assert page["status"] == 200 and "<title>جهّز لقطة · لقطة</title>" in page["body"]
    assert 'data-active="sheet"' in page["body"]
    assert re.search(r'data-setup-step="sheet" data-status="todo"[^>]*>', page["body"]).group(0).count("hidden") == 0
    assert re.search(r'data-setup-step="keys"[^>]*hidden', page["body"])
    assert 'data-active="keys"' in keys["body"] and not re.search(r'data-setup-step="keys"[^>]*hidden', keys["body"])
    # the service account to share the sheet with, never a key
    assert "laqta@demo-project.iam.gserviceaccount.com" in page["body"]
    body = json.loads(state["body"])
    assert body["status"] == "success" and body["current"] == "sheet" and [s["key"] for s in body["steps"]] == STEPS
    for value in setup_app["secrets"].values():
        for out in (page, keys, state, home, settings):
            assert value not in out["body"]
    # the Home card while setup is incomplete, and the Settings link back to the wizard
    assert "data-home-setup" in home["body"] and "ابدأ التجهيز" in home["body"]
    assert re.search(r'<a [^>]*href="[^"]*/setup"', settings["body"])
    assert _calls(setup_app) == [] and _state(setup_app["db"]) is None


def test_the_sheet_check_reads_the_saved_link_and_is_kept(setup_app):
    (checked,) = _kernel(setup_app["env"], [["POST", "/api/setup/check", {"step": "sheet"}]])
    body = json.loads(checked["body"])
    assert checked["status"] == 200 and _step(body, "sheet")["status"] == "ok" and body["current"] == "keys"
    assert _calls(setup_app) == [["sheet-preview", {"spreadsheet_url": SHEET_URL, "tab_name": "Products"}]]
    kept = _state(setup_app["db"])["checks"]["sheet"]
    assert kept["status"] == "ok" and kept["headers"] == 3 and kept["for"] == _fp({"url": SHEET_URL, "tab": "Products"})
    # resumable: opening the wizard again starts at the next step
    (page,) = _kernel(setup_app["env"], [["GET", "/setup", {}]])
    assert 'data-active="keys"' in page["body"]
    # another tab name: the old check no longer counts
    (other,) = _kernel(dict(setup_app["env"], SPREADSHEET_TAB_NAME="Other"), [["GET", "/api/setup/state", {}]])
    assert _step(json.loads(other["body"]), "sheet")["status"] == "todo"


def test_the_sheet_check_without_credentials_never_calls_the_bridge(setup_app):
    env = dict(setup_app["env"], CREDENTIALS_FILE=str(setup_app["tmp"] / "missing.json"))
    (checked,) = _kernel(env, [["POST", "/api/setup/check", {"step": "sheet"}]])
    sheet = _step(json.loads(checked["body"]), "sheet")
    assert sheet["status"] == "fail" and sheet["rows"][0]["state"] == "مش موجود"
    assert _calls(setup_app) == []


def test_the_brands_check_and_a_bridge_that_does_not_answer(setup_app):
    (ok,) = _kernel(setup_app["env"], [["POST", "/api/setup/check", {"step": "brands"}]])
    brands = _step(json.loads(ok["body"]), "brands")
    assert brands["status"] == "ok" and brands["rows"][-1]["state"] == "12 ماركة"
    (down,) = _kernel(dict(setup_app["env"], LQ_STUB_MODE="down"), [["POST", "/api/setup/check", {"step": "brands"}]])
    brands = _step(json.loads(down["body"]), "brands")
    assert brands["status"] == "fail" and brands["rows"][0]["href"] == "/setup?step=sheet"
    assert [c[0] for c in _calls(setup_app)] == ["setup_brands", "setup_brands"]


def test_the_keys_test_is_the_connection_check_and_shows_no_key(setup_app):
    stub = setup_app["tmp"] / "python_stub.sh"
    stub.write_text("#!/bin/sh\ncat <<'EOF'\n" + json.dumps(DIAGNOSTICS) + "\nEOF\n", encoding="utf-8")
    stub.chmod(0o755)
    (before,) = _kernel(setup_app["env"], [["POST", "/api/setup/check", {"step": "keys"}]])
    assert _step(json.loads(before["body"]), "keys")["status"] == "todo"
    (tested,) = _kernel(dict(setup_app["env"], PYTHON_PATH=str(stub)), [["POST", "/api/setup/check", {"step": "keys", "test": "1"}]])
    keys = _step(json.loads(tested["body"]), "keys")
    assert tested["status"] == 200 and keys["status"] == "ok"
    assert [r["state"] for r in keys["rows"]] == ["محفوظ · يعمل"] * 4
    for value in setup_app["secrets"].values():
        assert value not in before["body"] and value not in tested["body"]
    broken = setup_app["tmp"] / "python_broken.sh"
    broken.write_text("#!/bin/sh\necho not json\n", encoding="utf-8")
    broken.chmod(0o755)
    (failed,) = _kernel(dict(setup_app["env"], PYTHON_PATH=str(broken)), [["POST", "/api/setup/check", {"step": "keys", "test": "1"}]])
    assert failed["status"] == 500 and re.search(r"[؀-ۿ]", json.loads(failed["body"])["error"])


def test_the_first_run_plan_progress_and_finishing(setup_app):
    plan, started, skip, unskip, bad, bogus, done = _kernel(setup_app["env"], [
        ["POST", "/api/setup/check", {"step": "run"}],
        ["POST", "/api/setup/progress", {"action": "run_started", "rows": "2, 4-7"}],
        ["POST", "/api/setup/progress", {"action": "skip", "step": "brands"}],
        ["POST", "/api/setup/progress", {"action": "unskip", "step": "brands"}],
        ["POST", "/api/setup/progress", {"action": "run_started", "rows": "x"}],
        ["POST", "/api/setup/check", {"step": "bogus"}],
        ["POST", "/api/setup/progress", {"action": "done"}]])
    body = json.loads(plan["body"])
    assert body["plan"]["status"] == "success" and body["plan"]["rows"] == "2, 4-7" and body["plan"]["count"] == 5
    run = _step(json.loads(started["body"]), "run")
    assert run["status"] == "running" and run["started_rows"] == "2, 4-7"
    assert _step(json.loads(skip["body"]), "brands")["status"] == "skipped"
    assert _step(json.loads(unskip["body"]), "brands")["status"] == "todo"
    assert bad["status"] == 422 and bogus["status"] == 422
    assert json.loads(done["body"])["done"] is True
    state = _state(setup_app["db"])
    assert state["done_at"] and state["checks"]["run"]["rows"] == "2, 4-7" and state["skipped"] == {}
    # finished: the Home page no longer shows the card; the wizard stays reachable and says it is done
    home, page, reopened = _kernel(setup_app["env"], [["GET", "/", {}], ["GET", "/setup", {}],
                                                      ["POST", "/api/setup/progress", {"action": "reopen"}]])
    assert home["status"] == 200 and "data-home-setup" not in home["body"]
    assert re.search(r'data-setup="done-note"\s*>', page["body"])
    assert json.loads(reopened["body"])["done"] is False


def test_without_the_database_the_page_says_so_and_nothing_is_saved(setup_app):
    env = dict(setup_app["env"], DB_PORT="1")
    page, check, progress, home = _kernel(env, [["GET", "/setup", {}], ["POST", "/api/setup/check", {"step": "brands"}],
                                                ["POST", "/api/setup/progress", {"action": "done"}], ["GET", "/", {}]])
    assert page["status"] == 200 and "قاعدة البيانات مش متاحة" in page["body"]
    assert check["status"] == 503 and progress["status"] == 503
    assert home["status"] == 200 and "data-home-setup" not in home["body"]
    assert _calls(setup_app) == []


# ---------------------------------------------------------------------------
# public/js/setup.js under node
# ---------------------------------------------------------------------------

def _node(script):
    result = subprocess.run([NODE, "-"], input="globalThis.window = globalThis;\n" + read(SETUP_JS) + "\n" + script,
                            capture_output=True, text=True, timeout=60, encoding="utf-8")
    assert result.returncode == 0, result.stderr[-3000:]
    return json.loads(result.stdout.strip().splitlines()[-1])


@NEEDS_NODE
def test_view_functions():
    out = _node("""
const S = window.LaqtaSetup;
console.log(JSON.stringify({
  save: S.sheetAction({url: 'https://x', tab: 'A'}, 'https://y', 'A'),
  check: S.sheetAction({url: 'https://x', tab: 'A'}, ' https://x ', 'A'),
  emptyField: S.sheetAction({url: '', tab: ''}, '', ''),
  tab: S.sheetAction({url: 'https://x', tab: 'A'}, 'https://x', 'B'),
  next: [S.neighbour('sheet', 1), S.neighbour('run', 1), S.neighbour('ready', -1), S.neighbour('sheet', -1)],
  live: [S.liveView({status: 'success', batch: {phase: 'running'}, run: {processed: 2, total: 5}}),
         S.liveView({status: 'success', batch: {phase: 'starting'}}),
         S.liveView({status: 'success', batch: {phase: 'idle'}, run: {counts: {proposed: 3, none: 1, not_found: 1, error: 0}}}),
         S.liveView(null)],
  plan: [S.planView({status: 'success', rows: '2-6', message: 'رح ندوّر'}), S.planView({status: 'empty', rows: '', message: 'كل الصفوف'}), S.planView(null)],
  err: [S.requestError({status: 419}, 'x'), S.requestError({status: 500, data: {error: 'English'}}, 'بديل'),
        S.requestError({status: 503, data: {error: 'قاعدة البيانات'}}, 'بديل')],
  meta: S.statusMeta('fail'),
  state: [S.stateOf({steps: []}), !!S.stateOf({steps: S.STEP_KEYS.map(k => ({key: k}))})]
}));
""")
    assert out["save"] == {"kind": "save", "label": "احفظ وافحص"} and out["check"]["kind"] == "check"
    assert out["emptyField"] == {"kind": "check", "label": "افحص الشيت"} and out["tab"]["kind"] == "save"
    assert out["next"] == ["keys", "ready", "run", "sheet"]
    running, starting, finished, unknown = out["live"]
    assert running == {"known": True, "active": True, "text": "عم ندوّر: 2 من 5 خلصوا."}
    assert starting["active"] and starting["text"].startswith("عم نقرأ الشيت")
    assert finished == {"known": True, "active": False, "text": "خلص التشغيل: 3 مقترحة · 1 بلا اقتراح · 1 ما انلقت."}
    assert unknown["known"] is False
    assert out["plan"][0] == {"text": "رح ندوّر", "canStart": True, "rows": "2-6"}
    assert out["plan"][1]["canStart"] is False and out["plan"][2]["canStart"] is False
    assert out["err"] == ["انتهت صلاحية الصفحة. حدّثها وجرّب مرة تانية.", "بديل", "قاعدة البيانات"]
    assert out["meta"] == {"label": "بدها تصليح", "tone": "danger"} and out["state"] == [None, True]


CONTROLLER_HARNESS = r"""
const S = window.LaqtaSetup;
const steps = S.STEP_KEYS.map(k => ({key: k, status: 'todo', summary: k + ' summary', rows: [], skipped: false,
  url: 'https://docs.google.com/spreadsheets/d/old', url_is_default: false, tab: 'A'}));
const state = (over) => ({status: 'success', steps: steps.map(s => Object.assign({}, s, (over || {})[s.key] || {})),
  progress: {ok: 0, total: 5}, current: 'sheet'});
const sent = [], toasts = [], renders = [], plans = [], lives = [], statuses = [], navigated = [];
let answers = {};
let liveAnswers = [];
const deps = {
  initial: state(),
  fetchJson: (url, opts) => {
    sent.push([opts.method, url, opts.body || null]);
    if (url === '/api/run/live') return Promise.resolve(liveAnswers.shift() || {ok: true, status: 200, data: {status: 'success', batch: {phase: 'idle'}}});
    const a = answers[url];
    return Promise.resolve(typeof a === 'function' ? a(opts.body) : (a || {ok: true, status: 200, data: state()}));
  },
  render: (d) => renders.push(d.steps.map(s => s.status).join(',')),
  renderStep: (k, s) => renders.push(k + ':' + s.status),
  renderPlan: (p) => plans.push(p), renderLive: (v) => lives.push(v.text),
  setBusy: () => {}, sheetStatus: (t, tone) => statuses.push([t, tone]),
  sheetError: (res, saving) => ({text: saving ? 'ما انحفظ الربط.' : 'ما انفتح.'}),
  saveConfirmText: (u, t) => 'رح نربط ' + u,
  confirm: (t) => { sent.push(['ASK', t]); return Promise.resolve(globalThis.CONFIRM !== false); },
  toast: (t, v) => toasts.push([t, v]),
  schedule: (fn, ms) => { setTimeout(fn, 0); return 1; },
  navigate: (u) => navigated.push(u)
};
const c = S.createSetup(deps);
const done = () => new Promise(r => setTimeout(r, 30));
"""


@NEEDS_NODE
def test_the_sheet_button_saves_through_the_settings_endpoint_then_checks():
    out = _node(CONTROLLER_HARNESS + """
(async () => {
  answers['/api/setup/check'] = (body) => ({ok: true, status: 200, data: state({sheet: {status: 'ok', summary: 'الشيت مربوط وبينقرا.'}})});
  answers['/api/sheet/save'] = {ok: true, status: 200, data: {status: 'success'}};
  await c.sheet({spreadsheet_url: 'https://docs.google.com/spreadsheets/d/new', tab_name: 'A'});
  const saved = sent.slice();
  sent.length = 0;
  await c.sheet({spreadsheet_url: '', tab_name: ''});               // the saved link: check only
  const checkOnly = sent.slice();
  sent.length = 0;
  answers['/api/sheet/save'] = {ok: false, status: 500, data: {status: 'failed', error: 'x'}};
  await c.sheet({spreadsheet_url: 'https://docs.google.com/spreadsheets/d/other', tab_name: ''});
  const refused = sent.slice();
  sent.length = 0;
  globalThis.CONFIRM = false;
  await c.sheet({spreadsheet_url: 'https://docs.google.com/spreadsheets/d/third', tab_name: ''});
  console.log(JSON.stringify({saved, checkOnly, refused, declined: sent, statuses}));
})();
""")
    assert out["saved"] == [["ASK", "رح نربط https://docs.google.com/spreadsheets/d/new"],
                            ["POST", "/api/sheet/save", {"spreadsheet_url": "https://docs.google.com/spreadsheets/d/new", "tab_name": "A"}],
                            ["POST", "/api/setup/check", {"step": "sheet"}]]
    assert out["checkOnly"] == [["POST", "/api/setup/check", {"step": "sheet"}]]
    # a refused save is said and nothing is checked; a declined question sends nothing
    assert [s[1] for s in out["refused"]] == [out["refused"][0][1], "/api/sheet/save"]
    assert out["declined"] == [["ASK", "رح نربط https://docs.google.com/spreadsheets/d/third"]]
    assert ["الشيت مربوط وبينقرا.", "success"] in out["statuses"] and ["ما انحفظ الربط.", "danger"] in out["statuses"]


@NEEDS_NODE
def test_keys_brands_and_publish_buttons():
    out = _node(CONTROLLER_HARNESS + """
(async () => {
  answers['/api/setup/check'] = (body) => ({ok: true, status: 200, data: state({[body.step]: {status: 'ok', summary: 'تمام'}})});
  answers['/api/system/publish-check'] = {ok: true, status: 200, data: {status: 'success', result: {steps: []}}};
  await c.testKeys();
  await c.checkBrands();
  await c.publish();
  answers['/api/setup/check'] = {ok: false, status: 503, data: {status: 'failed', error: 'قاعدة البيانات مش متاحة هلق'}};
  await c.checkBrands();
  console.log(JSON.stringify({sent, toasts, renders}));
})();
""")
    assert out["sent"] == [["POST", "/api/setup/check", {"step": "keys", "test": True}],
                           ["POST", "/api/setup/check", {"step": "brands"}],
                           ["POST", "/api/system/publish-check", {}],
                           ["POST", "/api/setup/check", {"step": "publish"}],
                           ["POST", "/api/setup/check", {"step": "brands"}]]
    assert ["كل المفاتيح اشتغلت.", "success"] in out["toasts"]
    assert ["قاعدة البيانات مش متاحة هلق", "danger"] in out["toasts"]
    assert "publish:running" in out["renders"]


@NEEDS_NODE
def test_the_first_run_starts_with_the_plans_rows_and_follows_the_run():
    out = _node(CONTROLLER_HARNESS + """
(async () => {
  answers['/api/setup/check'] = {ok: true, status: 200, data: Object.assign(state(), {plan: {status: 'success', rows: '2-6', message: 'رح ندوّر على صور 5 منتجات'}})};
  answers['/api/run-all'] = {ok: true, status: 200, data: {status: 'success'}};
  answers['/api/setup/progress'] = {ok: true, status: 200, data: state({run: {status: 'running'}})};
  liveAnswers = [{ok: true, status: 200, data: {status: 'success', batch: {phase: 'running'}, run: {processed: 1, total: 5}}},
                 {ok: true, status: 200, data: {status: 'success', batch: {phase: 'idle'}, run: {counts: {proposed: 4, not_found: 1}}}}];
  answers['/api/setup/state'] = {ok: true, status: 200, data: state({run: {status: 'ok'}})};
  await c.plan();
  await c.startRun();
  await done();
  console.log(JSON.stringify({sent, plans, lives, renders}));
})();
""")
    assert out["sent"][:4] == [["POST", "/api/setup/check", {"step": "run"}],
                               ["POST", "/api/run-all", {"row_filter": "2-6", "brand_filter": "", "forceOverwrite": False, "skipCache": False}],
                               ["POST", "/api/setup/progress", {"action": "run_started", "rows": "2-6"}],
                               ["GET", "/api/run/live", None]]
    assert out["sent"][4:] == [["GET", "/api/run/live", None], ["GET", "/api/setup/state", None]]
    assert out["plans"][0] == {"text": "رح ندوّر على صور 5 منتجات", "canStart": True, "rows": "2-6"} and out["plans"][1] is None
    assert out["lives"] == ["عم ندوّر: 1 من 5 خلصوا.", "خلص التشغيل: 4 مقترحة · 1 ما انلقت."]
    assert out["renders"][-1].endswith(",ok")


@NEEDS_NODE
def test_a_refused_run_start_says_why_and_saves_nothing():
    out = _node(CONTROLLER_HARNESS + """
(async () => {
  answers['/api/setup/check'] = {ok: true, status: 200, data: Object.assign(state(), {plan: {status: 'success', rows: '2-6', message: 'm'}})};
  answers['/api/run-all'] = {ok: false, status: 400, data: {status: 'failed', error: 'عملية الأتمتة قيد التشغيل بالفعل حالياً.'}};
  await c.plan();
  const started = await c.startRun();
  console.log(JSON.stringify({started, sent: sent.map(s => s[1]), toasts}));
})();
""")
    assert out["started"] is False and out["sent"] == ["/api/setup/check", "/api/run-all"]
    assert ["عملية الأتمتة قيد التشغيل بالفعل حالياً.", "danger"] in out["toasts"]


@NEEDS_NODE
def test_skip_finish_and_resuming_a_running_first_run():
    out = _node(CONTROLLER_HARNESS + """
(async () => {
  answers['/api/setup/progress'] = (body) => ({ok: true, status: 200, data: state({brands: {skipped: body.action === 'skip'}})});
  await c.skip('brands');
  await c.skip('brands');
  await c.finish();
  const first = sent.slice();
  sent.length = 0;
  const resumed = S.createSetup(Object.assign({}, deps, {initial: state({run: {status: 'running'}})}));
  resumed.start();
  await done();
  console.log(JSON.stringify({first, resumed: sent.map(s => s[1]), navigated}));
})();
""")
    assert out["first"] == [["POST", "/api/setup/progress", {"action": "skip", "step": "brands"}],
                            ["POST", "/api/setup/progress", {"action": "unskip", "step": "brands"}],
                            ["POST", "/api/setup/progress", {"action": "done"}]]
    assert out["navigated"] == ["/"]
    # opening the wizard while its first run goes: it follows the run at once
    assert out["resumed"][0] == "/api/run/live"
