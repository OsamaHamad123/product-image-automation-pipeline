"""The «فهرس المتاجر المحلي» card on the Health page (LocalIndexController, health.js createLocalIndex).

- Static: the routes, the card's own hooks, one script and one stylesheet for the page, no inline script.
- PHP helpers under the PHP CLI (no Laravel): the per-store state words, «من 3 أيام», the progress line, the last run's
  number of products the index answered.
- The page script under node: the button POSTs once, follows the progress, reloads when the refresh is over.
- The Laravel app through its HTTP kernel with a stub bridge and the MariaDB test database: the rows and states, the
  progress on reload, the POST through the bridge with CSRF, the unavailable-database state. Skipped without php,
  dashboard/vendor or MariaDB.
"""

import json
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

import pytest
from laqta_review_harness import NODE
from test_laqta_health import DASH, PHP, ROOT, _js, _kernel, _node, _put, _sql, app_env  # noqa: F401

CONTROLLER = DASH / "app" / "Http" / "Controllers" / "LocalIndexController.php"
ROUTES = DASH / "routes" / "web.php"
HEALTH_VIEW = DASH / "resources" / "views" / "dashboard" / "diagnostics.blade.php"
HEALTH_JS = DASH / "public" / "js" / "health.js"
STATE = ROOT / "temp" / "local_index_refresh.json"
LOCK = ROOT / "temp" / "local_index_refresh.lock"
ROBOTS = ROOT / "temp" / "local_index_robots.json"

NEEDS_PHP = pytest.mark.skipif(PHP is None, reason="php is not installed")
NEEDS_NODE = pytest.mark.skipif(NODE is None, reason="node is not installed")
DAY, HOUR = 86400, 3600


def read(path):
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Static
# ---------------------------------------------------------------------------

def test_routes_and_hooks():
    routes = read(ROUTES)
    c = "\\App\\Http\\Controllers\\LocalIndexController"
    assert f"Route::get('/api/system/local-index', [{c}::class, 'status']);" in routes
    assert f"Route::post('/api/system/local-index/refresh', [{c}::class, 'refresh']);" in routes
    assert "withoutMiddleware" not in routes and "validateCsrfTokens" not in read(DASH / "bootstrap" / "app.php")
    view, js = read(HEALTH_VIEW), read(HEALTH_JS)
    for hook in ("index-card", "index-rows", "index-last-run", "index-status", "index-refresh", "index-refresh-label"):
        assert f'data-health="{hook}"' in view, hook
    assert "'localIndex' => LocalIndexController::card()" in read(DASH / "app" / "Http" / "Controllers" / "HealthController.php")
    assert "فهرس المتاجر المحلي" in view and "حدّث الفهرس هلق" in view
    # the card has its own JS functions and its own URLs; the page still loads exactly one script
    assert "function localIndexView(" in js and "function createLocalIndex(" in js
    assert "'/api/system/local-index/refresh'" in js and "'/api/system/local-index'" in js
    assert re.findall(r"asset\('js/([a-z-]+\.js)'\)", view) == ["health.js"]


@NEEDS_PHP
def test_the_controller_parses():
    out = subprocess.run([PHP, "-l", str(CONTROLLER)], capture_output=True, text=True)
    assert out.returncode == 0, out.stdout + out.stderr


# ---------------------------------------------------------------------------
# PHP helpers (no Laravel)
# ---------------------------------------------------------------------------

def _php(code):
    controller = str(CONTROLLER).replace("\\", "/")
    script = ("<?php\nnamespace App\\Http\\Controllers { abstract class Controller {} }\nnamespace {\n"
              f"require '{controller}';\nuse App\\Http\\Controllers\\LocalIndexController as L;\n"
              "$out = [];\n" + code + "\necho json_encode($out, JSON_UNESCAPED_UNICODE);\n}\n")
    with tempfile.NamedTemporaryFile("w", suffix=".php", delete=False, encoding="utf-8") as fh:
        fh.write(script)
        path = fh.name
    try:
        result = subprocess.run([PHP, path], capture_output=True, text=True, encoding="utf-8", timeout=60)
    finally:
        os.unlink(path)
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


STORES = [{"key": "lulu", "name": "Lulu Hypermarket UAE", "enabled": True},
          {"key": "spinneys", "name": "Spinneys UAE", "enabled": True},
          {"key": "carrefour_uae", "name": "Carrefour UAE", "enabled": True},
          {"key": "sharjahcoop", "name": "Sharjah Co-op", "enabled": True},
          {"key": "noon_uae", "name": "noon UAE", "enabled": False}]


@NEEDS_PHP
def test_the_age_words_are_levantine_and_never_say_zero():
    out = _php("foreach ([null, 5, 59, 60, 120, 200, 3600, 7200, 3 * 3600, 11 * 3600, 86400, 2 * 86400, 3 * 86400, "
               "12 * 86400, 40 * 86400] as $s) { $out[] = L::ageText($s); }")
    assert out == ["", "هلق", "هلق", "من دقيقة", "من دقيقتين", "من 3 دقايق", "من ساعة", "من ساعتين", "من 3 ساعات",
                   "من 11 ساعة", "من يوم", "من يومين", "من 3 أيام", "من 12 يوم", "من 40 يوم"]
    out = _php("foreach ([0, 1, 2, 3, 10, 11, 4930] as $n) { $out[] = L::pagesText($n); }")
    assert out == ["ما في صفحات", "صفحة منتج وحدة", "صفحتين", "3 صفحات", "10 صفحات", "11 صفحة", "4930 صفحة"]


@NEEDS_PHP
def test_each_store_is_ok_blocked_or_never_with_its_pages_and_last_harvest():
    out = _php("""
$stores = json_decode('%s', true);
$db = [
  'lulu' => ['pages' => 1200, 'last_status' => 'ok', 'last_age_s' => 3 * 86400, 'ok_age_s' => 3 * 86400],
  'spinneys' => ['pages' => 0, 'last_status' => 'blocked', 'last_age_s' => 86400, 'ok_age_s' => null],
  'carrefour_uae' => ['pages' => 40, 'last_status' => 'partial', 'last_age_s' => 3600, 'ok_age_s' => 3600],
];
$view = L::view($stores, $db, ['running' => false, 'state' => []], null, 1000);
$out['rows'] = $view['rows']; $out['total'] = $view['total_text']; $out['db'] = $view['db'];
$none = L::view($stores, null, ['running' => false, 'state' => []], null, 1000);
$out['no_db'] = [$none['db'], $none['total_text'], $none['rows'][0]['pages_text']];
""" % json.dumps(STORES, ensure_ascii=False))
    rows = {r["key"]: r for r in out["rows"]}
    assert (rows["lulu"]["state"], rows["lulu"]["tone"], rows["lulu"]["pages_text"], rows["lulu"]["when"]) == (
        "ok", "success", "1200 صفحة", "من 3 أيام")
    assert rows["spinneys"]["state"] == "blocked" and rows["spinneys"]["state_text"] == "ممنوع"
    assert "ما بنسأله قبل 6 أيام" in rows["spinneys"]["note"] and rows["spinneys"]["when"] == ""
    assert rows["carrefour_uae"]["state"] == "partial" and rows["carrefour_uae"]["when"] == "من ساعة"
    assert (rows["sharjahcoop"]["state"], rows["sharjahcoop"]["state_text"], rows["sharjahcoop"]["pages_text"]) == (
        "never", "ما انجمع أبداً", "ما في صفحات")
    assert rows["noon_uae"]["state"] == "off" and rows["noon_uae"]["tone"] == "muted"
    assert out["total"] == "بالفهرس 1240 صفحة" and out["db"] is True
    assert out["no_db"] == [False, "", "—"]


@NEEDS_PHP
def test_the_progress_line_follows_the_refresh_and_the_last_runs_index_answers_are_said():
    out = _php("""
$stores = json_decode('%s', true);
$state = ['state' => 'running', 'current' => 'spinneys', 'plan' => [['store' => 'lulu'], ['store' => 'spinneys'], ['store' => 'sharjahcoop']],
          'results' => [['store' => 'lulu']]];
$out['running'] = L::progressView(['running' => true, 'state' => $state], $stores);
$out['starting'] = L::progressView(['running' => true, 'state' => ['state' => 'starting']], $stores);
$out['done'] = L::progressView(['running' => false, 'state' => ['state' => 'idle', 'ended' => 'done',
    'finished_at' => time() - 2 * 3600, 'results' => [['store' => 'lulu'], ['store' => 'spinneys']]]], $stores);
$out['budget'] = L::progressView(['running' => false, 'state' => ['ended' => 'budget', 'finished_at' => time() - 60,
    'results' => [['store' => 'lulu']]]], $stores);
$out['never'] = L::progressView(['running' => false, 'state' => []], $stores);
$out['answered'] = L::lastRunText(['local_index' => ['answered' => 14, 'asked' => 40], 'counts' => ['searched' => 58]]);
$out['zero'] = L::lastRunText(['local_index' => ['answered' => 0, 'asked' => 3], 'counts' => ['searched' => 58]]);
$out['old_report'] = L::lastRunText(['counts' => ['searched' => 58]]);
$out['no_report'] = L::lastRunText(null);
""" % json.dumps(STORES, ensure_ascii=False))
    assert out["running"] == {"running": True, "text": "عم نحدّث الفهرس بالخلفية: Spinneys UAE (2 من 3)"}
    assert out["starting"] == {"running": True, "text": "عم يبلّش تحديث الفهرس…"}
    assert out["done"] == {"running": False, "text": "آخر تحديث من ساعتين: خلص (قرينا متجرين)"}
    assert out["budget"]["text"] == "آخر تحديث من دقيقة: وقف عند حد الوقت والباقي بالمرة الجاية (قرينا متجر واحد)"
    assert out["never"] == {"running": False, "text": ""}
    assert out["answered"] == "الفهرس جاوب على 14 من 58 منتج بآخر تشغيل (مجاناً، قبل أي بحث مدفوع)."
    assert out["zero"] == "الفهرس ما جاوب على أي منتج بآخر تشغيل."
    assert out["old_report"] is None and out["no_report"] is None


@NEEDS_PHP
def test_a_live_lock_or_a_fresh_button_job_counts_as_running(tmp_path):
    state, lock = tmp_path / "state.json", tmp_path / "state.lock"
    out = _php(f"""
$s = '{state.as_posix()}'; $l = '{lock.as_posix()}';
$out['none'] = L::refreshState($s, $l, 5000)['running'];
file_put_contents($s, json_encode(['state' => 'starting', 'updated_at' => 4950]));
$out['starting'] = L::refreshState($s, $l, 5000)['running'];
$out['starting_old'] = L::refreshState($s, $l, 5100)['running'];
file_put_contents($l, json_encode(['budget_s' => 300])); touch($l, 4900);
$out['lock'] = L::refreshState($s, $l, 5000)['running'];
$out['lock_dead'] = L::refreshState($s, $l, 4900 + 300 + 181)['running'];
""")
    assert out == {"none": False, "starting": True, "starting_old": False, "lock": True, "lock_dead": False}


@NEEDS_PHP
def test_the_stores_come_from_the_shipped_stores_file():
    stores = _php("$out = L::stores('%s');" % str(ROOT / "catalog_match" / "data" / "catalog_stores.json").replace("\\", "/"))
    by_key = {s["key"]: s for s in stores}
    assert by_key["sharjahcoop"] == {"key": "sharjahcoop", "name": "Sharjah Co-op", "enabled": True}
    assert by_key["noon_uae"]["enabled"] is False and by_key["lulu"]["enabled"] is True
    assert _php("$out = L::stores('/nonexistent/stores.json');") == []


@NEEDS_PHP
def test_the_visit_window_is_said_in_dubai_time(tmp_path):
    out = _php("""
foreach ([[[240, 525]], [[1320, 120]], [[0, 1440]], [[240, 525], [720, 780]], []] as $w) { $out[] = L::visitText($w); }
""")
    assert out == ["بيسمح بالقراءة بين 08:00 و12:45 بتوقيت الإمارات",            # 04:00-08:45 UTC
                   "بيسمح بالقراءة بين 02:00 و06:00 بتوقيت الإمارات",            # 22:00-02:00 UTC crosses midnight
                   "",                                                                   # a full-day window is no restriction
                   "بيسمح بالقراءة بين 08:00 و12:45 وبين 16:00 و17:00 بتوقيت الإمارات",
                   ""]
    saved = tmp_path / "robots.json"
    saved.write_text(json.dumps({"sharjahcoop": {"visit_time": [[240, 525]], "checked_at": 1},
                                 "lulu": {"visit_time": []}, "bad": {"visit_time": [[5000, 1], ["x", 2], [7, 7]]},
                                 "weird": "text"}), encoding="utf-8")
    assert _php("$out = L::visitWindows('%s');" % saved.as_posix()) == {"sharjahcoop": [[240, 525]]}
    assert _php("$out = L::visitWindows('/nonexistent/robots.json');") == []


@NEEDS_PHP
def test_a_store_with_a_visit_window_says_when_it_may_be_read_and_when_the_last_refresh_skipped_it():
    out = _php("""
$stores = json_decode('%s', true);
$state = ['state' => 'idle', 'ended' => 'done', 'finished_at' => time() - 3600,
          'results' => [['store' => 'lulu', 'status' => 'ok'], ['store' => 'sharjahcoop', 'status' => 'outside_visit_time']]];
$view = L::view($stores, [], ['running' => false, 'state' => $state], null, null, ['sharjahcoop' => [[240, 525]]]);
$out['rows'] = array_column($view['rows'], null, 'key');
$out['progress'] = $view['refresh']['text'];
$running = L::view($stores, [], ['running' => true, 'state' => $state], null, null, []);
$out['running_note'] = array_column($running['rows'], 'note', 'key')['sharjahcoop'];
""" % json.dumps(STORES, ensure_ascii=False))
    sharjah = out["rows"]["sharjahcoop"]
    assert sharjah["visit"] == "بيسمح بالقراءة بين 08:00 و12:45 بتوقيت الإمارات"
    assert "ما انقرا بآخر تحديث لأنو الوقت برا المسموح" in sharjah["note"]
    assert out["rows"]["lulu"]["visit"] == "" and "برا المسموح" not in out["rows"]["lulu"]["note"]
    assert out["progress"] == ("آخر تحديث من ساعة: خلص (قرينا متجر واحد، متجر واحد برا وقت الزيارة المسموح "
                               "(بنعيد المحاولة بالتحديث الجاي))")
    assert "برا المسموح" not in out["running_note"]


# ---------------------------------------------------------------------------
# The page script under node
# ---------------------------------------------------------------------------

@NEEDS_NODE
def test_the_button_posts_once_follows_the_progress_and_reloads_when_it_is_over():
    out = _node(_js(HEALTH_JS) + r"""
const H = window.LaqtaHealth;
const log = { renders: [], toasts: [], fetches: [], reloads: 0, scheduled: [] };
function make(responses, running) {
  return H.createLocalIndex({
    running: !!running,
    fetchJson: (url, opts) => { log.fetches.push([opts.method, url]); const r = responses.shift();
      return r instanceof Error ? Promise.reject(r) : Promise.resolve(r); },
    render: v => log.renders.push(v),
    toast: (t, v) => log.toasts.push([v, t]),
    schedule: (fn, ms) => { log.scheduled.push([fn, ms]); return log.scheduled.length; },
    reload: () => { log.reloads += 1; }
  });
}
const card = (running, text) => ({ ok: true, data: { status: 'success', card: { refresh: { running, text } } } });
(async () => {
  const out = { view: [H.localIndexView(null), H.localIndexView(card(true, 'عم نحدّث').data.card)] };
  const c = make([{ ok: true, data: { status: 'success', started: true, running: true, message: 'بلّش' } },
                  card(true, 'عم نحدّث الفهرس بالخلفية: Lulu (1 من 3)'), card(false, 'آخر تحديث هلق: خلص')]);
  const first = await c.run();
  const again = await c.run();                 // pressed again while it runs: nothing is sent
  out.first = [first, again, log.fetches.slice(), log.scheduled.length, log.scheduled[0][1]];
  await c.poll();                               // still running: the status line, and the next poll is scheduled
  out.mid = [log.renders[log.renders.length - 1].text, log.scheduled.length, log.reloads];
  await c.poll();                               // over: the page reloads to show the new numbers
  out.end = [log.reloads, log.renders[log.renders.length - 1].disabled, log.scheduled.length];
  // a refused start gives the button back and says why
  const log2 = log.toasts.length;
  const bad = make([{ ok: false, status: 500, data: { status: 'failed', error: 'ما قدرنا نبدأ' } }]);
  out.bad = [await bad.run(), log.renders[log.renders.length - 1].disabled, log.toasts[log2]];
  const off = make([{ ok: true, data: { status: 'success', started: false, running: false, message: 'مطفي' } }]);
  out.off = [await off.run(), log.toasts[log.toasts.length - 1]];
  const down = make([new Error('network')]);
  out.down = [await down.run(), log.toasts[log.toasts.length - 1][0]];
  // opened while a refresh was running: it watches at once
  const before = log.scheduled.length;
  make([], true).start();
  out.watching = log.scheduled.length - before;
  console.log(JSON.stringify(out));
})();
""")
    assert out["view"][0] == {"running": False, "text": "", "disabled": False, "label": "حدّث الفهرس هلق"}
    assert out["view"][1]["disabled"] is True and out["view"][1]["label"] == "عم يحدّث…"
    assert out["first"] == [True, False, [["POST", "/api/system/local-index/refresh"]], 1, 4000]
    assert out["mid"] == ["عم نحدّث الفهرس بالخلفية: Lulu (1 من 3)", 2, 0]
    assert out["end"] == [1, False, 2]
    assert out["bad"][0] is False and out["bad"][1] is False and out["bad"][2][0] == "danger"
    assert "ما قدرنا نبدأ" in out["bad"][2][1]
    assert out["off"] == [False, ["warning", "مطفي"]] and out["down"] == [False, "danger"]
    assert out["watching"] == 1


# ---------------------------------------------------------------------------
# The Laravel app through its HTTP kernel (MariaDB test database and a stub bridge)
# ---------------------------------------------------------------------------

@pytest.fixture
def index_app(app_env, tmp_path):
    """app_env with a stub bridge that also answers local_index_refresh (each call recorded)."""
    db = app_env["db"]
    calls = tmp_path / "li_calls.txt"
    stub = tmp_path / "li_bridge.py"
    stub.write_text(
        "import json, os, sys\n"
        f"open({str(calls)!r}, 'a').write((sys.argv[1] if len(sys.argv) > 1 else '') + '\\n')\n"
        "if os.environ.get('LQ_STUB_MODE') == 'down':\n"
        "    print(json.dumps({'status': 'failed', 'error': 'stub bridge is down'}))\n"
        "elif sys.argv[1] == 'local_index_refresh':\n"
        "    print(json.dumps({'status': 'success', 'started': True, 'running': True, 'reason': 'started',\n"
        "                      'message_ar': 'بلّش تحديث الفهرس بالخلفية. افتح الصفحة بعد شوي لتشوف التقدم.'}))\n"
        "else:\n"
        "    print(json.dumps({'status': 'success', 'scanned': 0, 'windows': {}, 'alerts': []}))\n", encoding="utf-8")
    env = dict(app_env["env"], CLI_BRIDGE_PATH=str(stub))
    for table in ("catalog_products", "catalog_harvests"):
        _sql(db, f"DELETE FROM {table}")
    for path in (STATE, LOCK, ROBOTS):
        path.unlink(missing_ok=True)
    yield {"env": env, "db": db, "calls": calls}
    for table in ("catalog_products", "catalog_harvests"):
        _sql(db, f"DELETE FROM {table}")
    for path in (STATE, LOCK, ROBOTS):
        path.unlink(missing_ok=True)


def _harvest(db, store, ago, status):
    _sql(db, "INSERT INTO catalog_harvests (store, started_at, finished_at, status) "
             f"VALUES (%s, NOW() - INTERVAL {ago}, NOW() - INTERVAL {ago}, %s)", (store, status))


def _pages(db, store, n):
    for i in range(n):
        _sql(db, "INSERT INTO catalog_products (store, url, url_hash, slug_text) VALUES (%s, %s, %s, 'x')",
             (store, f"https://example.ae/{store}/p/{i}", f"{store[:6]}{i:034d}"[:40]))


def test_the_health_page_shows_each_store_with_its_pages_last_harvest_and_status(index_app):
    db, env = index_app["db"], index_app["env"]
    (empty,) = _kernel(env, [["GET", "/system-diagnostics", {}]])
    assert empty["status"] == 200 and 'data-health="index-card"' in empty["body"]
    for name in ("Lulu Hypermarket UAE", "Carrefour UAE", "Spinneys UAE", "talabat mart UAE", "Sharjah Co-op", "noon UAE",
                 "Union Coop"):
        assert name in empty["body"], name
    assert "ما انجمع أبداً" in empty["body"] and "موقوف بالإعدادات" in empty["body"]
    assert "حدّث الفهرس هلق" in empty["body"] and not re.search(r'data-health="index-refresh"[^>]*disabled', empty["body"])

    _pages(db, "lulu", 3)
    _pages(db, "sharjahcoop", 2)
    _harvest(db, "lulu", "3 DAY", "ok")
    _harvest(db, "spinneys", "1 DAY", "blocked")
    _harvest(db, "sharjahcoop", "5 HOUR", "partial")
    (page,) = _kernel(env, [["GET", "/system-diagnostics", {}]])
    body = page["body"]
    lulu = re.search(r'data-store="lulu" data-state="ok".*?</div>', body, re.S).group(0)
    assert "3 صفحات" in lulu and "تمام" in lulu and "آخر جمع ناجح من 3 أيام" in lulu
    spinneys = re.search(r'data-store="spinneys" data-state="blocked".*?</div>', body, re.S).group(0)
    assert "ممنوع" in spinneys and "ما بنسأله قبل 6 أيام" in spinneys
    # the shipped stores file has Carrefour UAE and talabat mart off (their sitemaps are not usable yet)
    assert 'data-store="sharjahcoop" data-state="partial"' in body and "من 5 ساعات" in body
    assert 'data-store="carrefour_uae" data-state="off"' in body and 'data-store="noon_uae" data-state="off"' in body
    assert "بالفهرس 5 صفحات" in body
    # opening the page never starts a refresh and never calls the bridge for it
    assert not index_app["calls"].exists()


def test_the_page_says_when_a_store_with_a_visit_time_may_be_read(index_app):
    env = index_app["env"]
    ROBOTS.parent.mkdir(exist_ok=True)
    ROBOTS.write_text(json.dumps({"sharjahcoop": {"visit_time": [[240, 525]], "checked_at": int(time.time())}}),
                      encoding="utf-8")
    (page,) = _kernel(env, [["GET", "/system-diagnostics", {}]])
    row = re.search(r'data-store="sharjahcoop".*?</div>', page["body"], re.S).group(0)
    assert "بيسمح بالقراءة بين 08:00 و12:45 بتوقيت الإمارات" in row
    assert "بيسمح بالقراءة" not in re.search(r'data-store="lulu".*?</div>', page["body"], re.S).group(0)


def test_the_page_shows_the_refresh_progress_on_reload(index_app):
    env = index_app["env"]
    now = int(time.time())
    STATE.parent.mkdir(exist_ok=True)
    STATE.write_text(json.dumps({"state": "running", "current": "spinneys", "updated_at": now,
                                 "plan": [{"store": "lulu"}, {"store": "spinneys"}, {"store": "sharjahcoop"}],
                                 "results": [{"store": "lulu"}]}), encoding="utf-8")
    LOCK.write_text(json.dumps({"pid": 1, "budget_s": 300}), encoding="utf-8")
    running, api = _kernel(env, [["GET", "/system-diagnostics", {}], ["GET", "/api/system/local-index", {}]])
    assert "عم نحدّث الفهرس بالخلفية: Spinneys UAE (2 من 3)" in running["body"]
    assert re.search(r'data-health="index-refresh"[^>]*disabled', running["body"]) and "عم يحدّث…" in running["body"]
    card = json.loads(api["body"])
    assert api["status"] == 200 and card["status"] == "success" and card["card"]["refresh"]["running"] is True
    LOCK.unlink()
    STATE.write_text(json.dumps({"state": "idle", "ended": "done", "finished_at": now - 2 * HOUR,
                                 "results": [{"store": "lulu"}]}), encoding="utf-8")
    (done,) = _kernel(env, [["GET", "/system-diagnostics", {}]])
    assert "آخر تحديث من ساعتين: خلص (قرينا متجر واحد)" in done["body"]
    assert not re.search(r'data-health="index-refresh"[^>]*disabled', done["body"])


def test_the_button_endpoint_starts_the_job_through_the_bridge_and_answers_at_once(index_app):
    env = index_app["env"]
    (started,) = _kernel(env, [["POST", "/api/system/local-index/refresh", {}]])
    body = json.loads(started["body"])
    assert started["status"] == 200 and body["status"] == "success" and body["started"] is True and body["running"] is True
    assert "بلّش تحديث الفهرس" in body["message"]
    assert index_app["calls"].read_text(encoding="utf-8").split() == ["local_index_refresh"]
    # a bridge that is down is said plainly, with the Arabic words and no raw error
    (down,) = _kernel(dict(env, LQ_STUB_MODE="down"), [["POST", "/api/system/local-index/refresh", {}]])
    assert down["status"] == 500 and json.loads(down["body"])["status"] == "failed"
    assert "ما قدرنا نبدأ تحديث الفهرس" in json.loads(down["body"])["error"] and "stub" not in down["body"]


def test_the_button_endpoint_needs_the_csrf_token_like_every_post(index_app):
    env = dict(index_app["env"], APP_ENV="local")
    (response,) = _kernel(env, [["POST", "/api/system/local-index/refresh", {}]])
    assert response["status"] == 419
    assert not index_app["calls"].exists()                          # refused before it reached the bridge


def test_without_the_database_the_card_says_so_and_the_page_still_opens(index_app):
    env = dict(index_app["env"], DB_PORT="1")
    (page,) = _kernel(env, [["GET", "/system-diagnostics", {}]])
    assert page["status"] == 200 and "ما قدرنا نقرأ الفهرس: قاعدة البيانات مش متاحة" in page["body"]
    assert "Sharjah Co-op" in page["body"] and "بالفهرس" not in page["body"]
