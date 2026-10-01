"""Diagnostics page and connection check: nothing paid runs on page load, Serper is critical, the check is bounded.

Before this fix the diagnostics page ran verify_cloud_services on every load (a real Serper query and a PhotoRoom
call per visit) through a blocking shell_exec of up to 180 s, and a failing Serper showed as a grey "optional"
card without details.

Python checks run with every provider check replaced (offline). The Laqta health page script (public/js/health.js)
runs under node with recorded fetches and renders.
"""

import io
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

import verify_cloud_services as vcs

ROOT = Path(__file__).resolve().parents[1]
DASH = ROOT / "dashboard"
VIEWS = DASH / "resources" / "views"
DIAG_VIEW = VIEWS / "dashboard" / "diagnostics.blade.php"
PRODUCT = DASH / "app" / "Http" / "Controllers" / "ProductController.php"
HEALTH = DASH / "app" / "Http" / "Controllers" / "HealthController.php"
HEALTH_JS = DASH / "public" / "js" / "health.js"
NODE = shutil.which("node")

CHECKS = [fn for _key, _name, fn, _critical in vcs.SERVICES]
REAL_CHECKS = {fn: getattr(vcs, fn) for fn in CHECKS}


def read(path):
    return path.read_text(encoding="utf-8")


@pytest.fixture
def checks(monkeypatch):
    """Every provider check replaced: all online unless a test overrides one."""
    def ok():
        print("✅ يعمل")
        return True

    for fn in CHECKS:
        monkeypatch.setattr(vcs, fn, ok)
    return monkeypatch


# ---------------------------------------------------------------------------
# verify_cloud_services: criticality, per-service details, deadline, saved result
# ---------------------------------------------------------------------------

def test_serper_is_critical_and_only_legacy_or_optional_services_are_not():
    critical = {key: is_critical for key, _name, _fn, is_critical in vcs.SERVICES}
    assert critical == {"google_sheets": True, "cloudinary": True, "gemini": True, "photoroom": True,
                        "serper": True, "google_search": False, "proxy": False}


def test_failing_serper_is_a_critical_failure_with_its_own_details(checks, monkeypatch):
    monkeypatch.setattr(vcs.config, "SERPER_API_KEY", "serper-test-key", raising=False)

    class Resp:
        status_code = 429
        text = ""

        def json(self):
            return {}

    monkeypatch.setattr(vcs.requests, "post", lambda *a, **k: Resp())
    monkeypatch.setattr(vcs, "verify_serper", REAL_CHECKS["verify_serper"])
    result = vcs.run_checks(deadline_s=10)

    serper = result["services"]["serper"]
    assert serper["status"] == "offline" and serper["is_critical"] is True
    assert "429" in serper["details"] and "اشحن الرصيد" in serper["details"]
    assert "serper-test-key" not in json.dumps(result, ensure_ascii=False)
    # details belong to their own service only
    assert "429" not in result["services"]["gemini"]["details"]
    assert result["all_ok"] is False


def test_all_ok_needs_every_critical_service_but_not_the_optional_ones(checks):
    checks.setattr(vcs, "verify_google_search", lambda: None)   # not configured
    checks.setattr(vcs, "verify_proxy", lambda: False)          # optional and down
    result = vcs.run_checks(deadline_s=10)
    assert result["all_ok"] is True
    assert result["services"]["google_search"]["status"] == "disabled"
    assert result["services"]["proxy"] == {"name": "Proxy Server", "status": "offline", "is_critical": False,
                                           "details": ""}

    checks.setattr(vcs, "verify_serper", lambda: False)
    assert vcs.run_checks(deadline_s=10)["all_ok"] is False


def test_a_hung_check_is_reported_as_failed_when_the_deadline_passes(checks):
    checks.setattr(vcs, "verify_photoroom", lambda: time.sleep(20))
    started = time.monotonic()
    result = vcs.run_checks(deadline_s=0.5)
    assert time.monotonic() - started < 5
    photoroom = result["services"]["photoroom"]
    assert photoroom["status"] == "offline" and photoroom["is_critical"] is True
    assert "لم يكتمل فحص PhotoRoom API خلال 0.5 ثانية" in photoroom["details"]
    assert result["services"]["gemini"]["status"] == "online"
    assert result["all_ok"] is False


def test_a_crashing_check_fails_only_its_own_service(checks):
    def boom():
        raise RuntimeError("unexpected")

    checks.setattr(vcs, "verify_cloudinary", boom)
    result = vcs.run_checks(deadline_s=10)
    assert result["services"]["cloudinary"]["status"] == "offline"
    assert "RuntimeError" in result["services"]["cloudinary"]["details"]
    assert result["services"]["google_sheets"]["status"] == "online"


def test_library_errors_land_in_the_details_of_the_check_that_logged_them(checks):
    import logging

    def sheets():
        logging.getLogger("google_sheets").error("فشل الاتصال بـ Google Sheets API: %s", "invalid_grant")
        return False

    checks.setattr(vcs, "verify_google_sheets", sheets)
    result = vcs.run_checks(deadline_s=10)
    assert "invalid_grant" in result["services"]["google_sheets"]["details"]
    assert "invalid_grant" not in result["services"]["cloudinary"]["details"]


def test_json_mode_prints_one_document_and_saves_it_with_its_time(checks, tmp_path):
    out = io.StringIO()
    path = tmp_path / "temp" / "diagnostics_last.json"
    code = vcs.main_json(["verify_cloud_services.py", "--json", "--deadline", "5"], out, result_path=str(path))
    assert code == 0
    lines = out.getvalue().strip().splitlines()
    assert len(lines) == 1
    printed = json.loads(lines[0])
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved == printed
    assert re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+00:00$", saved["checked_at"])
    assert set(saved["services"]) == {key for key, *_ in vcs.SERVICES}
    assert not list(path.parent.glob("*.tmp"))


def test_a_hung_check_does_not_keep_the_process_alive(tmp_path):
    driver = tmp_path / "driver.py"
    driver.write_text(
        "import sys, time\n"
        f"sys.path.insert(0, {str(ROOT)!r})\n"
        "import verify_cloud_services as vcs\n"
        f"for fn in {CHECKS!r}:\n"
        "    setattr(vcs, fn, lambda: True)\n"
        "vcs.verify_serper = lambda: time.sleep(60)\n"
        f"code = vcs.main_json(['x', '--deadline', '1'], sys.stdout, result_path={str(tmp_path / 'last.json')!r})\n"
        "sys.exit(code)\n",
        encoding="utf-8",
    )
    started = time.monotonic()
    result = subprocess.run([sys.executable, str(driver)], capture_output=True, text=True, timeout=30)
    assert time.monotonic() - started < 20
    assert result.returncode == 1
    doc = json.loads(result.stdout.strip().splitlines()[-1])
    assert doc["services"]["serper"]["status"] == "offline"


# ---------------------------------------------------------------------------
# PHP: bounded check, last result shown on the page
# ---------------------------------------------------------------------------

def _method(text, name):
    start = text.index(f"function {name}(")
    nxt = re.search(r"\n    (?:public|private|protected)(?: static)? function ", text[start + 1:])
    return text[start:start + 1 + nxt.start()] if nxt else text[start:]


def test_controller_bounds_the_check_and_passes_the_last_result_to_the_page():
    text = read(PRODUCT)
    run = _method(text, "runDiagnosticsJson")
    assert "shell_exec" not in run and "set_time_limit(180)" not in run
    assert "proc_open(" in run and "proc_terminate(" in run
    assert "'--deadline'" in run
    deadline = int(re.search(r"DIAGNOSTICS_DEADLINE_SECONDS = (\d+);", text).group(1))
    kill = int(re.search(r"DIAGNOSTICS_KILL_SECONDS = (\d+);", text).group(1))
    assert deadline < kill <= 60
    # the Laqta health page (HealthController::page) gets the saved result, never a fresh check
    assert "return app(HealthController::class)->page();" in _method(text, "systemDiagnostics")
    page = _method(read(HEALTH), "page")
    assert "$last = ProductController::lastDiagnostics();" in page
    assert "'lastDiagnostics' => self::publicResult($last)" in page
    assert "verify_cloud_services" not in read(HEALTH) and "run-diagnostics" not in read(HEALTH)
    assert "temp/diagnostics_last.json" in text
    assert vcs.LAST_RESULT_PATH.replace("\\", "/").endswith("temp/diagnostics_last.json")
    # the note on the page states the same upper bound
    assert f"وما بياخد أكتر من {kill} ثانية" in read(DIAG_VIEW)


def test_every_check_failure_the_page_alerts_is_in_arabic():
    # the page shows data.error in a toast when the check fails (timeout, no result, crash)
    run = _method(read(PRODUCT), "runDiagnosticsJson")
    assert "Invalid output from python" not in run
    literals = re.findall(r"'error' => '([^']*)'", run)
    assert len(literals) == 2, literals
    for text in literals:
        assert re.search(r"[؀-ۿ]", text), text


# ---------------------------------------------------------------------------
# Pages: no automatic check, explicit button with an honest note
# ---------------------------------------------------------------------------

def test_no_page_runs_the_connection_check_on_its_own():
    for path in list(VIEWS.rglob("*.blade.php")):
        text = read(path)
        calls = [m.start() for m in re.finditer(r"runDiagnostics\(\)", text)]
        for pos in calls:
            line = text[text.rfind("\n", 0, pos) + 1:text.find("\n", pos)]
            assert 'onclick="runDiagnostics()"' in line or "async function runDiagnostics()" in line, \
                f"{path.name}: {line.strip()}"
    # the Laqta health page script is the only one that knows the endpoint, and runs it from the button only
    for path in (DASH / "public" / "js").rglob("*.js"):
        if "/api/system/run-diagnostics" in read(path):
            assert path.name == "health.js", path.name
    js = read(HEALTH_JS)
    assert js.count("RUN_URL") == 2                                   # the constant and the one POST in runCheck
    assert "deps.fetchJson(RUN_URL, { method: 'POST', body: {} })" in _js_function(js, "runCheck")
    assert "runButton.addEventListener('click', function () { controller.runCheck(); });" in js
    assert "runCheck" not in _js_function(js, "start")


def _js_function(js, name):
    """Body of one function of the controller (indented 8 spaces in createController)."""
    start = js.index(f"        function {name}(")
    return js[start:js.index("\n        }\n", start)]


def test_button_and_note_say_what_a_check_costs():
    view = read(DIAG_VIEW)
    assert "فحص الاتصالات الآن" in view
    note = re.search(r'id="diagCheckNote"[^>]*>(.*?)</span>', view, re.DOTALL).group(1)
    assert "Serper" in note and "PhotoRoom" in note and "واحد" in note
    assert "الفحص ما بيشتغل لحاله لما تفتح الصفحة" in view
    # Serper has its own card among the main services, never on the optional line
    health = read(HEALTH)
    services = health[health.index("public const SERVICES"):health.index("public const OPTIONAL_SERVICES")]
    optional = health[health.index("public const OPTIONAL_SERVICES"):health.index("public function page")]
    assert "'serper' =>" in services and "serper" not in optional


def test_recheck_resets_every_service_card():
    view = read(DIAG_VIEW)
    health = read(HEALTH)
    services = health[health.index("public const SERVICES"):health.index("public const OPTIONAL_SERVICES")]
    cards = re.findall(r"'([a-z_]+)' => \['name'", services)
    listed = json.loads(re.search(r"var SERVICE_KEYS = (\[.*?\]);", read(HEALTH_JS)).group(1).replace("'", '"'))
    assert "serper" in cards
    assert sorted(listed) == sorted(cards)
    assert "@foreach ($services as $service)" in view and "id=\"card-{{ $service['key'] }}\"" in view


def _run_page(stored, after=""):
    """The health page controller under node with recorded fetches and renders (health.js, no DOM)."""
    js = "globalThis.window = globalThis;\n" + read(HEALTH_JS) + f"""
const fetched = [];
const views = {{ services: [], checked: [] }};
const c = window.LaqtaHealth.createController({{
    initial: {json.dumps(stored, ensure_ascii=False)},
    fetchJson: (url, opts) => {{ fetched.push(url); return new Promise(() => {{}}); }},
    renderServices: v => views.services.push(v), renderChecked: v => views.checked.push(v),
    renderOptional: () => {{}}, setChecking: () => {{}}, renderOps: () => {{}}, renderLog: () => {{}},
    toast: () => {{}}, now: () => Date.parse('2026-09-30T12:00:00+00:00'), schedule: () => 0, isHidden: () => false
}});
c.start();
{after}
console.log(JSON.stringify({{ fetched, cards: views.services[views.services.length - 1],
                              info: views.checked[views.checked.length - 1] }}));
"""
    result = subprocess.run([NODE, "-e", js], capture_output=True, text=True, timeout=60, encoding="utf-8")
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


def _stored_result():
    def svc(name, status, critical, details=""):
        return {"name": name, "status": status, "is_critical": critical, "details": details}

    return {
        "status": "success", "all_ok": False, "checked_at": "2026-09-30T08:15:00+00:00", "duration_s": 4.2,
        "services": {
            "google_sheets": svc("Google Sheets API", "online", True),
            "cloudinary": svc("Cloudinary CDN", "online", True),
            "gemini": svc("Google Gemini API", "online", True),
            "photoroom": svc("PhotoRoom API", "online", True),
            "serper": svc("Serper (Google Images)", "offline", True, "❌ انتهى رصيد Serper (429)"),
            "google_search": svc("Google Custom Search", "disabled", False),
            "proxy": svc("Proxy Server", "disabled", False),
        },
        "raw_logs": "",
    }


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_opening_the_page_shows_the_saved_result_without_running_a_check():
    out = _run_page(_stored_result())
    assert "/api/system/run-diagnostics" not in out["fetched"]
    assert "/api/system/ops-health" in out["fetched"]                  # the read-only panels load
    assert out["cards"]["serper"] == {"state": "ما بيرد", "tone": "danger", "details": "❌ انتهى رصيد Serper (429)"}
    assert out["cards"]["gemini"]["tone"] == "success" and out["cards"]["google_sheets"]["state"] == "متصل"
    assert out["info"]["text"].startswith("آخر فحص للاتصالات:") and out["info"]["warn"] is True


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_page_without_a_saved_result_asks_for_a_check():
    out = _run_page(None)
    assert "/api/system/run-diagnostics" not in out["fetched"]
    assert out["info"] == {"text": "لسا ما انعمل فحص للاتصالات.", "warn": False}
    assert all(card["state"] == "لسا ما انفحص" and card["tone"] == "muted" for card in out["cards"].values())


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_button_rechecks_every_card_including_serper():
    out = _run_page(_stored_result(), after="c.runCheck();")
    assert out["fetched"].count("/api/system/run-diagnostics") == 1
    assert sorted(out["cards"]) == ["cloudinary", "gemini", "google_sheets", "photoroom", "serper"]
    for key, card in out["cards"].items():
        assert card["state"] == "عم نفحص…", key
        assert card["details"] == "", key
