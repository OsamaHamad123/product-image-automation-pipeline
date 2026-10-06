"""Health page: the search health and cost panel (HealthController + diagnostics.blade.php + public/js/health.js).

Static checks, `php -l`, and the panel's view code (LaqtaHealth.opsView) run under node with a report shaped
exactly like ops_health.summarize's output.
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
JS = DASH / "public" / "js" / "health.js"
ROUTES = DASH / "routes" / "web.php"
PHP = shutil.which("php")
NODE = shutil.which("node")
LTR = "⁦"
PDI = "⁩"


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
    js = read(JS)
    assert "var OPS_URL = '/api/system/ops-health';" in js
    assert "deps.fetchJson(OPS_URL + (refresh ? '?refresh=1' : '')" in js
    assert "loadOps(false);" in js
    assert 'data-health="alerts"' in view and 'data-health="ops"' in view
    assert "ما في عمليات بحث بآخر 7 أيام" in view and "ما في عمليات بحث بآخر 7 أيام" in js
    # values from the table only ever reach the page as text
    assert "innerHTML" not in js and "insertAdjacentHTML" not in js and ".textContent" in js


def _render(report, window="24h"):
    """LaqtaHealth.opsView(report, window) under node: the panel exactly as the page renders it."""
    harness = "globalThis.window = globalThis;\n" + read(JS) + f"""
console.log(JSON.stringify(window.LaqtaHealth.opsView({json.dumps(report, ensure_ascii=False)}, {json.dumps(window)})));
"""
    result = subprocess.run([NODE, "-"], input=harness, capture_output=True, text=True, timeout=60, encoding="utf-8")
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

    assert out["kind"] == "ok"
    assert [a["title"] for a in out["alerts"]] == ["رصيد Serper انتهى أو المفتاح مرفوض"]
    assert "401" not in out["alerts"][0]["text"] and "Serper" in out["alerts"][0]["text"]
    # hostile values stay plain strings (the DOM code writes them with textContent, never as markup)
    providers = {p["name"]: p for p in out["providers"]}
    assert "<img src=x onerror=alert(1)>" in providers
    assert providers["Serper (Google)"]["problems"] == "رفض الرصيد 3" and providers["Serper (Google)"]["tone"] == "danger"
    decisions = {d["key"]: d for d in out["decisions"]}
    assert decisions["other"]["title"] == "<script>x()</script> 1" and decisions["other"]["label"] == "غير ذلك"
    # plain Arabic as the main text, the code only in the tooltip
    assert decisions["none"]["label"] == "بلا اقتراح" and decisions["none"]["value"] == 1
    assert decisions["none"]["title"] == "REVIEW_UNSELECTED 1"
    assert decisions["error"]["label"] == "أعطال مؤقتة" and decisions["error"]["title"] == "PROVIDER_DOWN 1"
    for d in out["decisions"]:
        assert not re.search(r"[A-Z]{3,}_[A-Z]", d["label"]), d
    reasons = {r["title"]: r for r in out["reasons"]}
    assert reasons["VERIFIER_DOWN"]["label"] == "نموذج قراءة الملصق ما ردّ"
    assert reasons["PROVIDER_DOWN"]["label"] == "مصادر البحث ما ردّت"
    assert reasons["<b>CODE</b>"]["label"] == "سبب تاني"
    # Serper answered nothing; one Gemini call ($0.001)
    assert out["cost"]["lines"][0] == {"label": "Serper · 0 استعلام", "value": f"{LTR}$0.00{PDI}"}
    assert out["cost"]["lines"][1] == {"label": "Gemini · فحص واحد", "value": f"أقل من {LTR}$0.01{PDI}"}
    assert out["total"] == "3 منتجات"
    assert "(4 صفوف)" in out["note"] and f"Serper {LTR}$0.001{PDI}" in out["cost"]["note"]

    week = _render(report, "7d")
    assert {d["key"]: d for d in week["decisions"]}["proposed"]["title"] == "REVIEW_PRESELECTED 1"
    assert week["total"] == "4 منتجات"


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_panel_empty_states():
    empty = dict(ops_health.summarize([]), status="success")
    out = _render(empty)
    assert out["alerts"] == []
    assert out["kind"] == "empty" and out["title"] == "ما في عمليات بحث بآخر 7 أيام" and out["action"] is True

    only_old = dict(ops_health.summarize([_row(3 * 86400, _outcome("NOT_FOUND", "NO_RESULTS"), "NO_RESULTS")]),
                    status="success")
    out = _render(only_old)
    assert out["kind"] == "window-empty" and out["title"] == "ما في عمليات بحث بآخر 24 ساعة"
    assert "decisions" not in out
    week = _render(only_old, "7d")
    assert week["kind"] == "ok" and week["reasons"][0]["label"] == "ما في نتائج أبداً"
