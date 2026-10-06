"""«صور قديمة بخلفية بيضا»: the Health card (advanced section), RecutController's reprocess endpoints and the bridge
actions reprocess_plan / reprocess_start.

- Static: the routes, the card's hooks inside the advanced section, the page still loads one script.
- The page script under node: «احسب» posts once and says how many and what it costs, «ابدأ» asks first and starts a
  capped batch, the status line follows the batch.
- The Laravel app through its HTTP kernel with a stub bridge: opening the page runs nothing, the dry run and the start
  go through the bridge, a bad cap is refused before it, the batch state is read from temp/ without the bridge.
- The bridge actions and the detached start with fakes (no sheet, no process started).
"""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from laqta_review_harness import NODE
from test_laqta_health import DASH, PHP, ROOT, _js, _kernel, _node, app_env  # noqa: F401

CONTROLLER = DASH / "app" / "Http" / "Controllers" / "RecutController.php"
ROUTES = DASH / "routes" / "web.php"
HEALTH_VIEW = DASH / "resources" / "views" / "dashboard" / "diagnostics.blade.php"
HEALTH_JS = DASH / "public" / "js" / "health.js"
STATE = ROOT / "temp" / "reprocess_state.json"

NEEDS_PHP = pytest.mark.skipif(PHP is None, reason="php is not installed")
NEEDS_NODE = pytest.mark.skipif(NODE is None, reason="node is not installed")


def read(path):
    return path.read_text(encoding="utf-8")


def script_module():
    spec = importlib.util.spec_from_file_location("reprocess_transparent", ROOT / "scripts" / "reprocess_transparent.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# Static
# ---------------------------------------------------------------------------

def test_routes_and_the_card_inside_the_advanced_section():
    routes = read(ROUTES)
    c = "\\App\\Http\\Controllers\\RecutController"
    for line in (f"Route::post('/api/system/reprocess/plan', [{c}::class, 'plan']);",
                 f"Route::post('/api/system/reprocess/start', [{c}::class, 'start']);",
                 f"Route::get('/api/system/reprocess', [{c}::class, 'batchStatus']);"):
        assert line in routes, line
    view = read(HEALTH_VIEW)
    advanced = view[view.index('data-health="advanced"'):view.rindex("</details>")]
    for hook in ("reprocess", "reprocess-status", "reprocess-plan", "reprocess-plan-label", "reprocess-form",
                 "reprocess-max", "reprocess-usd", "reprocess-start"):
        assert f'data-health="{hook}"' in advanced, hook
    assert "صور قديمة بخلفية بيضا" in advanced and "بدون ما يغيّر شي" in advanced
    assert advanced.count("<section") == 1                     # one card, in the advanced section
    js = read(HEALTH_JS)
    assert "function createReprocess(" in js and "'/api/system/reprocess/plan'" in js
    assert len([line for line in view.splitlines() if "asset('js/" in line and ".js" in line]) == 1
    import cli_bridge
    assert cli_bridge.ACTIONS["reprocess_plan"] is cli_bridge.action_reprocess_plan
    assert cli_bridge.ACTIONS["reprocess_start"] is cli_bridge.action_reprocess_start


@NEEDS_PHP
def test_the_controller_parses():
    out = subprocess.run([PHP, "-l", str(CONTROLLER)], capture_output=True, text=True)
    assert out.returncode == 0, out.stdout + out.stderr


# ---------------------------------------------------------------------------
# The page script under node
# ---------------------------------------------------------------------------

@NEEDS_NODE
def test_the_plan_and_batch_lines_in_plain_arabic():
    out = _node(_js(HEALTH_JS) + r"""
const H = window.LaqtaHealth;
const plan = (p, extra) => H.reprocessPlanView({ ok: true, data: Object.assign({ status: 'success', plan: p, batch_max: 200 }, extra || {}) });
const out = {};
out.two = plan({ todo: 2, transparent: 40, not_in_sheet: 3, skipped_before: 1, probe_failed: 0, calls: 2, price: 0.02, usd: 0.04, worst_usd: 0.12 });
out.many = plan({ todo: 75, transparent: 0, calls: 75, price: 0.02, usd: 1.5, worst_usd: 4.5 });
out.none = plan({ todo: 0, transparent: 12 });
out.local = plan({ todo: 3, price: 0, calls: 0, usd: 0 });
out.off = plan({ todo: 9 }, { bg_off: true });
out.expired = H.reprocessPlanView({ ok: false, status: 419, data: null });
out.running = H.reprocessBatchText({ state: 'running', running: true, done: 3, planned: 50, max: 20, spent_usd: 0.06, current: 'Almarai Laban' });
out.done = H.reprocessBatchText({ state: 'max', running: false, done: 20, spent_usd: 0.4, needs_look: 2 });
out.stopped = H.reprocessBatchText({ state: 'stopped', running: false, done: 1, spent_usd: 0.02, last_error: 'photoroom_402', last_item: 'Milk' });
out.none_batch = H.reprocessBatchText({ state: 'none', running: false });
console.log(JSON.stringify(out));
""")
    assert out["two"]["text"].startswith("في صورتين بخلفية بيضا لازم تنعاد.")
    assert "التكلفة التقريبية \u2066$0.04\u2069 (2 طلب عزل × \u2066$0.020\u2069)" in out["two"]["text"]
    assert "40 صورة شفافة أصلاً." in out["two"]["text"] and "3 صور ما عادت بالشيت أو غيّرتها بإيدك" in out["two"]["text"]
    assert out["two"]["form"] is True and out["two"]["max"] == 2
    assert out["many"]["max"] == 20 and "75 صورة" in out["many"]["text"]
    assert out["none"]["form"] is False and "كل الصور اللي بالشيت شفافة" in out["none"]["text"]
    assert "ما في تكلفة" in out["local"]["text"]
    assert out["off"]["form"] is False and "عزل الخلفية متوقف" in out["off"]["text"]
    assert "انتهت صلاحية الصفحة" in out["expired"]["text"]
    assert out["running"] == "عم نعيد القص: خلص 3 من 20، والتكلفة لهلق \u2066$0.06\u2069. هلق: «Almarai Laban»."
    assert out["done"].startswith("آخر دفعة وقفت عند عدد الصور اللي حددته: انعادت 20 صورة شفافة")
    assert "صورتين طلع قصها بملاحظات" in out["done"]
    assert "رصيد PhotoRoom خلص" in out["stopped"] and "(عند «Milk»)" in out["stopped"] and "لتكمّل من وين وقفت" in out["stopped"]
    assert out["none_batch"] == ""


@NEEDS_NODE
def test_plan_posts_once_and_start_asks_then_follows_the_batch():
    out = _node(_js(HEALTH_JS) + r"""
const H = window.LaqtaHealth;
const log = { renders: [], toasts: [], fetches: [], asked: [], timers: [] };
function make(responses, answer) {
  return H.createReprocess({
    fetchJson: (url, opts) => { log.fetches.push([opts.method, url, opts.body || null]); const r = responses.shift();
      return r instanceof Error ? Promise.reject(r) : Promise.resolve(r); },
    render: v => log.renders.push(v),
    toast: (t, v) => log.toasts.push([v, t]),
    confirm: t => { log.asked.push(t); return Promise.resolve(answer); },
    schedule: (fn, ms) => { log.timers.push(ms); return 1; }
  });
}
(async () => {
  const out = {};
  const planned = { ok: true, data: { status: 'success', plan: { todo: 5, calls: 5, price: 0.02, usd: 0.1, worst_usd: 0.3 }, batch_max: 200,
                                      batch: { state: 'none', running: false } } };
  const c = make([planned], false);
  const pending = c.plan();
  out.again = await c.plan();
  const view = await pending;
  out.plan = [view.form, view.max, log.fetches.slice(), log.renders[0].busy];
  out.declined = [await c.start(5, 1), log.asked.length, log.fetches.length];
  out.bad = await c.start(0, 1);
  const started = { ok: true, data: { status: 'success', started: true, message: 'بلّشت الدفعة بالخلفية.',
                                      batch: { state: 'starting', running: true } } };
  const s = make([started], true);
  out.started = [await s.start('5', '0.5'), log.fetches[log.fetches.length - 1], log.timers.slice(), s.state.running,
                 log.asked[log.asked.length - 1]];
  const refused = { ok: true, data: { status: 'success', started: false, reason: 'run_active', message: 'في تشغيل للأتمتة هلق.' } };
  const r = make([refused], true);
  out.refused = [await r.start(5, 1), log.toasts[log.toasts.length - 1]];
  console.log(JSON.stringify(out));
})();
""")
    assert out["again"] is None
    form, max_, fetches, busy = out["plan"]
    assert (form, max_, busy) == (True, 5, True) and fetches == [["POST", "/api/system/reprocess/plan", {}]]
    assert out["declined"] == [False, 1, 1]                      # asked, said no: nothing sent
    assert out["bad"] is False
    ok, sent, timers, running, asked = out["started"]
    assert ok is True and sent == ["POST", "/api/system/reprocess/start", {"max": 5, "max_usd": 0.5}]
    assert timers == [5000] and running is True
    assert "رح نعيد قص لحد 5 صور شفافة، وما منتعدّى \u2066$0.50\u2069" in asked
    assert out["refused"] == [False, ["warning", "في تشغيل للأتمتة هلق."]]


# ---------------------------------------------------------------------------
# The Laravel app through its HTTP kernel (a stub bridge)
# ---------------------------------------------------------------------------

@pytest.fixture
def reprocess_app(app_env, tmp_path):
    calls = tmp_path / "rp_calls.txt"
    stub = tmp_path / "rp_bridge.py"
    stub.write_text(
        "import base64, json, os, sys\n"
        "action = sys.argv[1] if len(sys.argv) > 1 else ''\n"
        "params = json.loads(base64.b64decode(sys.argv[2]).decode('utf-8')) if len(sys.argv) > 2 else {}\n"
        f"open({str(calls)!r}, 'a').write(json.dumps([action, params]) + '\\n')\n"
        "if os.environ.get('LQ_STUB_MODE') == 'down':\n"
        "    print(json.dumps({'status': 'failed', 'error': 'stub bridge is down'}))\n"
        "elif action == 'reprocess_plan':\n"
        "    print(json.dumps({'status': 'success', 'bg_off': False, 'batch_max': 200, 'running': False,\n"
        "                      'plan': {'todo': 7, 'transparent': 30, 'not_in_sheet': 2, 'skipped_before': 0,\n"
        "                               'probe_failed': 1, 'pictures': 40, 'method': 'photoroom',\n"
        "                               'estimate': {'calls': 7, 'price': 0.02, 'usd': 0.14, 'worst_usd': 0.42}}}))\n"
        "elif action == 'reprocess_start':\n"
        "    print(json.dumps({'status': 'success', 'started': True, 'reason': 'started'}))\n"
        "else:\n"
        "    print(json.dumps({'status': 'success', 'scanned': 0, 'windows': {}, 'alerts': []}))\n", encoding="utf-8")
    saved = STATE.read_bytes() if STATE.exists() else None
    yield {"env": dict(app_env["env"], CLI_BRIDGE_PATH=str(stub)), "calls": calls}
    if saved is None:
        STATE.unlink(missing_ok=True)
    else:
        STATE.write_bytes(saved)


def calls_of(app):
    return [json.loads(line) for line in app["calls"].read_text(encoding="utf-8").splitlines()] \
        if app["calls"].exists() else []


def test_opening_the_page_runs_nothing(reprocess_app):
    (page,) = _kernel(reprocess_app["env"], [["GET", "/system-diagnostics", {}]])
    assert page["status"] == 200 and 'data-health="reprocess"' in page["body"]
    assert not [c for c in calls_of(reprocess_app) if c[0].startswith("reprocess")]


def test_the_dry_run_goes_through_the_bridge(reprocess_app):
    (done,) = _kernel(reprocess_app["env"], [["POST", "/api/system/reprocess/plan", {}]])
    body = json.loads(done["body"])
    assert done["status"] == 200 and body["status"] == "success"
    assert body["plan"] == {"todo": 7, "transparent": 30, "not_in_sheet": 2, "skipped_before": 0, "probe_failed": 1,
                            "pictures": 40, "method": "photoroom", "calls": 7, "price": 0.02, "usd": 0.14,
                            "worst_usd": 0.42}
    assert [c[0] for c in calls_of(reprocess_app)] == ["reprocess_plan"]
    (down,) = _kernel(dict(reprocess_app["env"], LQ_STUB_MODE="down"), [["POST", "/api/system/reprocess/plan", {}]])
    assert down["status"] == 500 and "ما قدرنا نعدّ الصور" in json.loads(down["body"])["error"]
    assert "stub" not in down["body"]


def test_the_start_checks_its_caps_before_the_bridge(reprocess_app):
    env = reprocess_app["env"]
    bad, too_much, ok = _kernel(env, [["POST", "/api/system/reprocess/start", {"max": "0", "max_usd": "1"}],
                                      ["POST", "/api/system/reprocess/start", {"max": "10", "max_usd": "500"}],
                                      ["POST", "/api/system/reprocess/start", {"max": "10", "max_usd": "0.5"}]])
    assert bad["status"] == 422 and too_much["status"] == 422
    assert "اكتب عدد صور" in json.loads(bad["body"])["error"]
    body = json.loads(ok["body"])
    assert ok["status"] == 200 and body["started"] is True and body["message"].startswith("بلّشت الدفعة بالخلفية")
    assert calls_of(reprocess_app) == [["reprocess_start", {"max": 10, "max_usd": 0.5}]]


def test_the_batch_state_is_read_without_the_bridge(reprocess_app):
    import time
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps({"state": "running", "done": 4, "planned": 12, "max": 10, "spent_usd": 0.08,
                                 "current": "Almarai Laban", "updated_at": time.time()}), encoding="utf-8")
    (live,) = _kernel(reprocess_app["env"], [["GET", "/api/system/reprocess", {}]])
    batch = json.loads(live["body"])["batch"]
    assert batch["running"] is True and (batch["done"], batch["planned"], batch["current"]) == (4, 12, "Almarai Laban")
    STATE.write_text(json.dumps({"state": "running", "done": 4, "updated_at": time.time() - 3600}), encoding="utf-8")
    (dead,) = _kernel(reprocess_app["env"], [["GET", "/api/system/reprocess", {}]])
    assert json.loads(dead["body"])["batch"]["state"] == "stale"
    STATE.unlink()
    (none,) = _kernel(reprocess_app["env"], [["GET", "/api/system/reprocess", {}]])
    assert json.loads(none["body"])["batch"] == {"state": "none", "running": False}
    assert calls_of(reprocess_app) == []


def test_the_start_endpoint_needs_the_csrf_token(reprocess_app):
    env = dict(reprocess_app["env"], APP_ENV="local")
    (response,) = _kernel(env, [["POST", "/api/system/reprocess/start", {"max": "5", "max_usd": "1"}]])
    assert response["status"] == 419 and calls_of(reprocess_app) == []


# ---------------------------------------------------------------------------
# The bridge actions and the detached start
# ---------------------------------------------------------------------------

def test_the_plan_action_reads_the_sheet_and_reports_the_estimate(monkeypatch):
    import cli_bridge
    import recut

    rpt = script_module()
    group = recut.Group(url="https://res.cloudinary.com/a/image/upload/q_auto,f_auto/v1/products/x/aa",
                        rows=[{"product_name": "Milk", "brand": "Almarai"}], sheet_rows=[{"row_number": 4}],
                        background="white", state="todo")
    from collections import Counter
    found = {"link_col": 5, "groups": [group], "todo": [group], "method": "photoroom",
             "counts": Counter({"todo": 1, "transparent": 3, "not_in_sheet": 0, "skipped_before": 0, "probe_failed": 0}),
             "estimate": recut.estimate(1, "photoroom")}
    monkeypatch.setattr(rpt, "build_plan", lambda ws, retry_skipped=False: found)
    monkeypatch.setattr(rpt, "read_state", lambda path=None: {})
    monkeypatch.setattr(cli_bridge, "_reprocess_script", lambda: rpt)
    monkeypatch.setattr(cli_bridge, "_open_sheet", lambda: object())
    out = cli_bridge.action_reprocess_plan({})
    assert out["status"] == "success" and out["running"] is False
    assert (out["plan"]["todo"], out["plan"]["transparent"], out["plan"]["pictures"]) == (1, 3, 4)
    assert out["plan"]["estimate"] == {"calls": 1, "price": 0.02, "usd": 0.02, "worst_usd": 0.06}
    assert out["plan"]["samples"] == [{"product_name": "Milk", "brand": "Almarai", "url": group.url, "rows": [4]}]

    monkeypatch.setattr(cli_bridge, "_open_sheet", lambda: (_ for _ in ()).throw(RuntimeError("sheet down")))
    failed = cli_bridge.action_reprocess_plan({})
    assert failed["status"] == "failed" and "sheet down" not in json.dumps(failed)


def test_the_start_action_validates_the_caps(monkeypatch):
    import cli_bridge

    rpt = script_module()
    started = []
    monkeypatch.setattr(rpt, "start_detached", lambda n, usd: started.append((n, usd)) or {"started": True,
                                                                                         "reason": "started"})
    monkeypatch.setattr(cli_bridge, "_reprocess_script", lambda: rpt)
    assert cli_bridge.action_reprocess_start({"max": 0, "max_usd": 1})["status"] == "invalid"
    assert cli_bridge.action_reprocess_start({"max": 10, "max_usd": 0})["status"] == "invalid"
    assert cli_bridge.action_reprocess_start({"max": "x"})["status"] == "invalid"
    assert cli_bridge.action_reprocess_start({"max": 100000, "max_usd": 1})["status"] == "invalid"
    assert cli_bridge.action_reprocess_start({"max": 10, "max_usd": 0.5}) == {"status": "success", "started": True,
                                                                           "reason": "started"}
    assert started == [(10, 0.5)]


def test_start_detached_launches_one_capped_apply_and_refuses_while_one_runs(monkeypatch, tmp_path):
    import config

    rpt = script_module()
    monkeypatch.setattr(config, "BG_REMOVAL_METHOD", "photoroom", raising=False)
    monkeypatch.setattr(rpt, "run_active", lambda: False)
    launched = []
    state, log = tmp_path / "state.json", tmp_path / "rp.log"
    out = rpt.start_detached(15, 0.75, popen=lambda cmd, **kw: launched.append((cmd, kw)), python="/py",
                             state_path=str(state), log_path=str(log))
    assert out == {"started": True, "reason": "started"}
    cmd, kw = launched[0]
    assert cmd[0] == "/py" and cmd[1].endswith("reprocess_transparent.py")
    assert cmd[2:] == ["--apply", "--max", "15", "--max-usd", "0.7500", "--trigger", "dashboard"]
    assert kw["cwd"] == str(ROOT) and json.loads(state.read_text())["state"] == "starting"
    again = rpt.start_detached(15, 0.75, popen=lambda *a, **k: pytest.fail("a second batch"), state_path=str(state),
                               log_path=str(log))
    assert again == {"started": False, "reason": "running"}
    state.unlink()
    monkeypatch.setattr(rpt, "run_active", lambda: True)
    assert rpt.start_detached(5, 1, popen=lambda *a, **k: pytest.fail("during a run"), state_path=str(state),
                              log_path=str(log))["reason"] == "run_active"
    monkeypatch.setattr(config, "BG_REMOVAL_METHOD", "none")
    assert rpt.start_detached(5, 1, popen=lambda *a, **k: pytest.fail("bg off"), state_path=str(state),
                              log_path=str(log))["reason"] == "bg_off"
