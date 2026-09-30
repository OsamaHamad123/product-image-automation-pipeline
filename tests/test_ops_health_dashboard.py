"""Diagnostics page: the search health and cost panel (HealthController + diagnostics.blade.php).

Static checks, `php -l`, and the panel's rendering code run under node against a stub DOM with a report
shaped exactly like ops_health.summarize's output.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

import ops_health

ROOT = Path(__file__).resolve().parents[1]
DASH = ROOT / "dashboard"
CONTROLLER = DASH / "app" / "Http" / "Controllers" / "HealthController.php"
VIEW = DASH / "resources" / "views" / "dashboard" / "diagnostics.blade.php"
ROUTES = DASH / "routes" / "web.php"
PHP = shutil.which("php")
NODE = shutil.which("node")


def read(path):
    return path.read_text(encoding="utf-8")


@pytest.mark.skipif(PHP is None, reason="php is not installed")
@pytest.mark.parametrize("path", [CONTROLLER, ROUTES, VIEW], ids=lambda p: p.name)
def test_php_lint(path):
    result = subprocess.run([PHP, "-l", str(path)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr


def test_route_sits_with_the_diagnostics_routes():
    routes = read(ROUTES)
    lines = routes.splitlines()
    i = next(n for n, line in enumerate(lines) if "/api/system/run-diagnostics" in line)
    near = "\n".join(lines[i:i + 3])
    assert "Route::get('/api/system/ops-health', [\\App\\Http\\Controllers\\HealthController::class, 'summary']);" \
        in near


def test_controller_is_read_only_through_the_bridge():
    text = read(CONTROLLER)
    assert "PythonBridge::run('ops_health')" in text
    assert "Cache::put(self::CACHE_KEY, $result, self::CACHE_SECONDS)" in text
    assert "$request->boolean('refresh')" in text
    for forbidden in ("DB::", "shell_exec", "->update(", "->insert(", "->delete(", "'raw'"):
        assert forbidden not in text, forbidden


def test_view_loads_the_panel():
    view = read(VIEW)
    assert "fetch('/api/system/ops-health'" in view
    assert "loadOpsHealth(false);" in view
    assert 'id="opsHealthAlerts"' in view and 'id="opsHealthBody"' in view
    assert "لا توجد عمليات بحث مسجلة في الطابور خلال آخر 7 أيام" in view


def _render(report, window="24h"):
    """Run the diagnostics page script under node with a stub DOM; return the panel's HTML and note text."""
    blocks = re.findall(r"<script>(.*?)</script>", read(VIEW), re.DOTALL)
    script = re.sub(r"\{\{.*?\}\}", "''", blocks[-1])
    harness = """
const elements = {};
function el(id) {
    if (!elements[id]) elements[id] = { id, innerHTML: '', textContent: '', className: '', disabled: false,
                                        checked: false, style: {}, value: '' };
    return elements[id];
}
globalThis.document = { getElementById: el, querySelector: () => ({ content: '' }) };
globalThis.window = { addEventListener: () => {} };
""" + script + f"""
opsHealthData = {json.dumps(report, ensure_ascii=False)};
switchHealthWindow({json.dumps(window)});
console.log(JSON.stringify({{
    alerts: el('opsHealthAlerts').innerHTML,
    body: el('opsHealthBody').innerHTML,
    note: el('opsHealthNote').textContent,
    tab7d: el('health-tab-7d').className
}}));
"""
    result = subprocess.run([NODE, "-e", harness], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


def _outcome(decision, failure_code=None, providers=(), vlm_calls=0):
    return {"decision": decision, "failure_code": failure_code, "provider_health": list(providers),
            "vlm_calls": vlm_calls, "queries": ["q"], "sku_key": "k"}


def _row(age_s, out, failure_code=None):
    return {"status": "ready_for_review", "failure_code": failure_code, "has_trace": 1, "age_s": age_s,
            "outcome_json": json.dumps(out)}


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_panel_renders_alerts_in_red_and_escapes_provider_names():
    quota = [{"provider": "serper", "status": "quota", "http_status": 400},
             {"provider": "<img src=x onerror=alert(1)>", "status": "ok", "http_status": 200}]
    rows = [_row(60, _outcome("REVIEW_UNSELECTED", "VERIFIER_DOWN", quota, 1), "VERIFIER_DOWN"),
            _row(120, _outcome("PROVIDER_DOWN", "PROVIDER_DOWN", quota), "PROVIDER_DOWN"),
            _row(130, _outcome("<script>x()</script>", None, quota), "<b>CODE</b>"),
            _row(3 * 86400, _outcome("REVIEW_PRESELECTED", None, [{"provider": "serper", "status": "ok"}], 1))]
    report = dict(ops_health.summarize(rows), status="success")
    out = _render(report)

    assert out["alerts"].count('class="health-alert"') == 1
    assert "رصيد Serper انتهى أو المفتاح مرفوض" in out["alerts"]
    # values from the table never reach the page as markup
    assert "<img" not in out["body"] and "&lt;img src=x onerror=alert(1)&gt;" in out["body"]
    assert "<script>" not in out["body"] and "&lt;script&gt;x()&lt;/script&gt;" in out["body"]
    assert "<b>" not in out["body"] and "&lt;b&gt;CODE&lt;/b&gt;" in out["body"]
    assert "مراجعة بدون اختيار (REVIEW_UNSELECTED)" in out["body"]
    assert "$0.001" in out["body"]                         # Serper answered nothing; one Gemini call
    assert "VERIFIER_DOWN" in out["body"] and "PROVIDER_DOWN" in out["body"]
    assert "تم فحص 4 صف" in out["note"] and "Serper 0.001$" in out["note"]

    week = _render(report, "7d")
    assert week["tab7d"] == "console-tab active"
    assert "REVIEW_PRESELECTED" in week["body"]


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_panel_empty_states():
    empty = dict(ops_health.summarize([]), status="success")
    out = _render(empty)
    assert out["alerts"] == ""
    assert "لا توجد عمليات بحث مسجلة في الطابور خلال آخر 7 أيام" in out["body"]

    only_old = dict(ops_health.summarize([_row(3 * 86400, _outcome("NOT_FOUND", "NO_RESULTS"), "NO_RESULTS")]),
                    status="success")
    out = _render(only_old)
    assert "لا توجد عمليات بحث مسجلة في آخر 24 ساعة" in out["body"]
    assert "health-tile" not in out["body"]
