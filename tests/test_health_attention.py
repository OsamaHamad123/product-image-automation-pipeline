"""The simpler Health page: «كلشي تمام» or a short «شو بدو منك» list on top (HealthAttentionController), every detailed
card under «تفاصيل متقدمة», collapsed by default, and nothing lost.

- Structure: the top comes first, the advanced section holds every card and action that was on the page, each
  item's in-page button lands on a card that exists, and opening the page still runs no Python.
- The items are a pure function of the existing sources (ops_health alerts, the last connection check, DEAD sheet
  writes, disk, the nightly age, the last run and the daily budget, the last publish rehearsal, background removal
  switched off, the local index), each with its fix; under PHP with Laravel booted and no database.
- The Laravel app through its HTTP kernel (MariaDB test database, stub bridge): a DEAD write and a provider outage
  read through ops-health reach the top; the page answers without the database too.
- health.js under node: the top's view function and its fixes.
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

ROOT = Path(__file__).resolve().parents[1]
DASH = ROOT / "dashboard"
VIEW = DASH / "resources" / "views" / "dashboard" / "diagnostics.blade.php"
HEALTH_JS = DASH / "public" / "js" / "health.js"
HEALTH_CSS = DASH / "public" / "css" / "pages" / "health.css"
CONTROLLERS = DASH / "app" / "Http" / "Controllers"
ATTENTION_PHP = CONTROLLERS / "HealthAttentionController.php"
HEALTH_PHP = CONTROLLERS / "HealthController.php"
ROUTES = DASH / "routes" / "web.php"
VENDOR_AUTOLOAD = DASH / "vendor" / "autoload.php"
PHP = shutil.which("php")
NODE = shutil.which("node")
NEEDS_LARAVEL = pytest.mark.skipif(PHP is None or not VENDOR_AUTOLOAD.exists(), reason="php or dashboard/vendor is missing")
NEEDS_NODE = pytest.mark.skipif(NODE is None, reason="node is not installed")


def read(path):
    return path.read_text(encoding="utf-8")


def _advanced(view):
    start = view.index('<details class="lq-health-advanced" data-health="advanced">')
    return view[start:view.rindex("</details>")]        # the service cards hold <details> of their own


# ---------------------------------------------------------------------------
# Structure
# ---------------------------------------------------------------------------

def test_the_route_the_page_data_and_no_python_on_open():
    c = "\\App\\Http\\Controllers\\HealthAttentionController"
    assert f"Route::get('/api/system/attention', [{c}::class, 'show']);" in read(ROUTES)
    php = read(HEALTH_PHP)
    page = php[php.index("public function page()"):php.index("public static function bgProblem(")]
    assert "'attention' => HealthAttentionController::current()," in page and "PythonBridge" not in page
    # the top reads ops-health from the cache only; the page script asks again once ops-health answered
    attention = read(ATTENTION_PHP)
    assert "PythonBridge" not in attention and "Cache::get(HealthController::CACHE_KEY)" in attention


def test_the_top_comes_first_and_every_card_stays_reachable_under_the_advanced_section():
    view = read(VIEW)
    top = view.index('data-health="now"')
    advanced = _advanced(view)
    assert top < view.index('<details class="lq-health-advanced"')
    # collapsed by default: no open attribute
    assert re.search(r'<details class="lq-health-advanced" data-health="advanced">', view)
    assert "تفاصيل متقدمة" in advanced
    # every card and action of the page before: the connection check and the service cards, «فحص النشر» and its
    # background-removal box, the last run, the lanes, the local index, «عمليات البحث», the log, the test set
    for hook in ('data-health="run-check"', "@foreach ($services as $service)", 'id="card-{{ $service[\'key\'] }}"',
                 'data-health="optional"', 'id="publish-check"', 'data-health="publish-run"', 'data-health="bg-skip"',
                 'data-health="bg-restore"', "data-health-last-run", 'data-health="lanes"', 'data-health="index-card"',
                 'data-health="index-refresh"', 'data-health="ops"', 'data-health="window"', 'data-health="ops-retry"',
                 'data-log-tab="pipeline"', 'data-log-tab="laravel"', 'data-log-tab="nightly"', 'data-health="eval-export"',
                 'data-health="eval-export-link"', 'id="lq-health-initial"', 'id="publish-check-initial"'):
        assert hook in advanced, hook
    # nothing detailed is left above the section, and one more card drops in at its end
    head = view[:view.index('<details class="lq-health-advanced"')]
    for hook in ('data-health="run-check"', 'id="publish-check"', 'data-health="ops"', 'data-health="log"'):
        assert hook not in head, hook
    assert advanced.rstrip().endswith("</div>") and "بطاقات متقدمة إضافية بتنحط هون" in advanced


def test_every_in_page_fix_lands_on_a_card_and_a_button_that_exist():
    view, js = read(VIEW), read(HEALTH_JS)
    goto = re.search(r"var GOTO = \{(.*?)\};", js, re.DOTALL).group(1)
    pairs = re.findall(r"'?([a-z-]+)'?: \['([^']+)', '([^']+)'\]", goto)
    assert {p[0] for p in pairs} == {"services", "publish-check", "bg-restore", "index-card", "log-nightly"}
    advanced = _advanced(view)
    for _, card, button in pairs:
        for selector in (card, button):
            if selector.startswith("#"):
                assert f'id="{selector[1:]}"' in advanced, selector
            else:
                assert selector[1:-1].replace('"', '"') in advanced, selector
    php = read(ATTENTION_PHP)
    used = set(re.findall(r"'goto' => '([a-z-]+)'", php))
    assert used - {"reload"} <= {p[0] for p in pairs}, used


def test_the_top_has_its_hooks_classes_and_no_raw_codes():
    view, css = read(VIEW), read(HEALTH_CSS)
    for hook in ("now", "now-title", "now-text", "now-items", "now-note"):
        assert f'data-health="{hook}"' in view, hook
    assert '<script type="application/json" id="lq-health-attention-initial">@json($attention)</script>' in view
    for cls in ("lq-health-now", "lq-health-now__icon", "lq-health-todo", "lq-health-todo__fix", "lq-health-advanced__label",
                "lq-health-advanced__hint", "lq-health__checkbar", "lq-health-eval"):
        assert re.search(r"\." + re.escape(cls) + r"[\s,\[{:]", css), cls
    top = view[view.index('data-health="now"'):view.index('<details class="lq-health-advanced"')]
    for code in ("SERPER_CREDIT", "GEMINI_DOWN", "VERIFIER_BUDGET", "VERIFIER_KEY", "DEAD", "budget_reached"):
        assert code not in top, code


# ---------------------------------------------------------------------------
# The items under PHP (Laravel booted, no database)
# ---------------------------------------------------------------------------

def _php(code):
    dash = str(DASH).replace("\\", "/")
    script = (f"<?php\nrequire '{dash}/vendor/autoload.php';\n$app = require '{dash}/bootstrap/app.php';\n"
              "$app->make(Illuminate\\Contracts\\Console\\Kernel::class)->bootstrap();\n"
              "use App\\Http\\Controllers\\HealthAttentionController as A;\n$out = [];\n" + code
              + "\necho json_encode($out, JSON_UNESCAPED_UNICODE);\n")
    with tempfile.NamedTemporaryFile("w", suffix=".php", delete=False, encoding="utf-8") as fh:
        fh.write(script)
        path = fh.name
    compiled = tempfile.mkdtemp()
    env = dict(os.environ, APP_ENV="testing", APP_KEY="base64:" + "A" * 43 + "=", CACHE_STORE="array", LOG_CHANNEL="stderr",
               VIEW_COMPILED_PATH=compiled, DB_CONNECTION="mariadb", DB_HOST="127.0.0.1", DB_PORT="1",
               DB_DATABASE="automation_test_offline")
    try:
        result = subprocess.run([PHP, path], cwd=DASH, env=env, capture_output=True, text=True, timeout=120, encoding="utf-8")
    finally:
        os.unlink(path)
        shutil.rmtree(compiled, ignore_errors=True)
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-3000:]
    return json.loads(result.stdout)


def _v(obj):
    return "json_decode(" + json.dumps(json.dumps(obj, ensure_ascii=False), ensure_ascii=False).replace("$", "\\$") + ", true)"


HEALTHY = {"db": True, "nightly_known": True, "nightly_age_s": 3 * 3600, "dead_total": 0, "dead_recent": 0, "free_gb": 40.0}
OPS = {"status": "success", "scanned": 10, "alerts": []}
ONLINE = {"checked_at": "2026-10-06T06:00:00+00:00",
          "services": {k: {"status": "online"} for k in ("google_sheets", "serper", "gemini", "photoroom", "cloudinary")}}


def _facts(**over):
    facts = {"healthz": HEALTHY, "ops": OPS, "diagnostics": ONLINE, "publish": None,
             "bg": {"method": "photoroom", "previous": "photoroom"}, "last_run": None,
             "index": {"db": True, "rows": [{"key": "lulu", "state": "ok"}], "refresh": {"running": False}},
             "index_rows": {"lulu": {"ok_age_s": 2 * 86400}}}
    facts.update(over)
    return facts


def _views(cases, after=False):
    code = "".join(f"$out[{json.dumps(k)}] = A::view({_v(v)}, 1800000000, {'true' if after else 'false'});\n"
                   for k, v in cases.items())
    return _php(code)


def _keys(view):
    return [i["key"] for i in view["items"]]


@NEEDS_LARAVEL
def test_all_fine_is_one_plain_status():
    out = _views({"fine": _facts(), "no_ops": _facts(ops=None)})
    fine = out["fine"]
    assert (fine["state"], fine["title"], fine["items"], fine["pending"]) == ("ok", "كلشي تمام", [], False)
    assert fine["text"] == "ما في شي بدو منك هلق. التفاصيل تحت إذا حابب تشوف."
    # ops-health not read yet: «عم نتأكد…» until the page asks again; asked and still missing: said plainly
    assert out["no_ops"]["state"] == "checking" and out["no_ops"]["pending"] is True
    after = _views({"no_ops": _facts(ops=None)}, after=True)["no_ops"]
    assert after["state"] == "ok" and "ما قدرنا نقرأ سجل البحث" in after["note"] and after["pending"] is False


@NEEDS_LARAVEL
def test_each_source_gives_an_item_with_its_fix():
    down = dict(ONLINE, services=dict(ONLINE["services"], gemini={"status": "offline"}, cloudinary={"status": "offline"},
                                      google_search={"status": "offline"}))
    alerts = {"status": "success", "alerts": [{"code": "SERPER_CREDIT", "searches": 3}, {"code": "VERIFIER_BUDGET"},
                                              {"code": "SOMETHING_NEW"}]}
    out = _views({
        "db": _facts(healthz={"db": False}, ops=None),
        "ops": _facts(ops=alerts),
        "services": _facts(diagnostics=down),
        "dead": _facts(healthz=dict(HEALTHY, dead_total=9, dead_recent=3)),
        "dead_old": _facts(healthz=dict(HEALTHY, dead_total=9, dead_recent=0)),
        "conflict": _facts(healthz=dict(HEALTHY, conflict_recent=23)),
        "conflict_none": _facts(healthz=dict(HEALTHY, conflict_recent=0)),
        "disk": _facts(healthz=dict(HEALTHY, free_gb=1.4)),
        "disk_unknown": _facts(healthz=dict(HEALTHY, free_gb=None)),
        "run_failed": _facts(last_run={"run_trigger": "nightly", "outcome": "outage", "started_at": "2026-10-06T00:30:00+00:00",
                                       "report_json": {"reason_text": "Serper ما ردّ"}}),
        "budget": _facts(last_run={"outcome": "stopped", "stop_reason": "budget_reached", "report_json": {}}),
        "nightly": _facts(healthz=dict(HEALTHY, nightly_age_s=30 * 3600)),
        "never": _facts(healthz=dict(HEALTHY, nightly_age_s=None)),
        "publish": _facts(publish={"overall": "fail", "summary_ar": "الرفع على Cloudinary ما زبط.", "steps": []}),
        "bg_off": _facts(bg={"method": "none", "previous": "photoroom"}),
        "index": _facts(index_rows={"lulu": {"ok_age_s": 20 * 86400}}),
        "index_running": _facts(index={"db": True, "rows": [{"key": "lulu", "state": "ok"}], "refresh": {"running": True}},
                                index_rows={"lulu": {"ok_age_s": 20 * 86400}}),
    })
    db = out["db"]["items"][0]
    assert db["key"] == "db" and db["tone"] == "danger" and db["action"] == {"label": "حدّث الصفحة", "href": "", "goto": "reload"}
    assert _keys(out["ops"]) == ["ops_serper_credit", "ops_verifier_budget"]
    assert out["ops"]["items"][0]["action"]["href"] == "/settings?tab=keys#lq-key-serper"
    assert out["ops"]["items"][1]["action"]["href"] == "/settings?tab=models"
    services = out["services"]["items"][0]
    assert services["title"] == "Gemini وCloudinary ما ردّوا بآخر فحص للاتصالات"
    assert services["action"]["goto"] == "services" and "آخر فحص:" in services["text"]
    dead = out["dead"]["items"][0]
    assert dead["title"] == "3 روابط ما وصلوا للشيت" and dead["action"]["href"] == "/batch-automation#run-new"
    assert out["dead_old"]["items"] == [] and out["disk_unknown"]["items"] == [] and out["never"]["items"] == []
    # writes the sheet refused because a row or a column changed: a warning with what happens next, never silent
    conflict = out["conflict"]["items"][0]
    assert (conflict["key"], conflict["tone"]) == ("outbox-conflict", "warning")
    assert conflict["title"] == "23 كتابات بالشيت ما انكتبت لأنه الشيت تغيّر" and "التشغيل الجاي" in conflict["text"]
    assert out["conflict_none"]["items"] == []
    disk = out["disk"]["items"][0]
    assert disk["title"] == "المساحة الفاضية عالجهاز قليلة (1.4 GB)" and "2 GB" in disk["text"]
    run = out["run_failed"]["items"][0]
    assert run["key"] == "last_run" and run["text"].startswith("التشغيل الليلي: انقطاع (Serper ما ردّ)")
    assert _keys(out["budget"]) == ["spend"] and out["budget"]["items"][0]["tone"] == "warning"
    assert out["nightly"]["items"][0]["title"] == "التشغيل الليلي ما اشتغل من 30 ساعة"
    assert out["nightly"]["items"][0]["action"]["goto"] == "log-nightly"
    publish = out["publish"]["items"][0]
    assert publish["text"] == "الرفع على Cloudinary ما زبط." and publish["action"]["goto"] == "publish-check"
    assert out["bg_off"]["items"][0]["action"] == {"label": "رجّع عزل الخلفية", "href": "", "goto": "bg-restore"}
    assert out["index"]["items"][0]["text"].startswith("آخر جمع ناجح من 20 يوم")
    assert out["index_running"]["items"] == []
    for name, view in out.items():
        if view["items"]:
            assert view["state"] == "attention" and view["title"] == "شو بدو منك", name
    assert out["ops"]["text"] == "في شغلتين لازم تنتبهلها. كل وحدة إلها زر لتصليحها."
    assert out["dead"]["text"].startswith("في شغلة وحدة")


@NEEDS_LARAVEL
def test_small_words():
    out = _php("""
$out['names'] = [A::names(['Gemini']), A::names(['Serper', 'Gemini', 'Cloudinary'])];
$out['gb'] = [A::gb(1.4), A::gb(2.0), A::gb(0.05)];
$out['hours'] = [A::hours(1), A::hours(2), A::hours(5), A::hours(30)];
$out['down'] = A::servicesDown(['services' => ['serper' => ['status' => 'offline'], 'gemini' => ['status' => 'disabled'],
    'proxy' => ['status' => 'offline']]]);
$out['none'] = A::servicesDown(null);
""")
    assert out["names"] == ["Gemini", "Serper وGemini وCloudinary"]
    assert out["gb"] == ["1.4 GB", "2 GB", "0.1 GB"]
    assert out["hours"] == ["ساعة", "ساعتين", "5 ساعات", "30 ساعة"]
    assert out["down"] == ["Serper"] and out["none"] == []


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


MARK = "laqta-attention-test-dead"


@pytest.fixture
def health_app(mariadb_or_skip, tmp_path):
    if PHP is None or not VENDOR_AUTOLOAD.exists():
        pytest.skip("php or dashboard/vendor is not installed")
    import google_sheets
    google_sheets.SQLiteTransactionQueue()                       # the outbox table
    db = mariadb_or_skip
    fixture = {"ops_health": {"status": "success", "scanned": 12, "windows": {}, "prices": {},
                              "alerts": [{"code": "SERPER_CREDIT", "message": "x", "searches": 3, "detail": ""}]}}
    calls = tmp_path / "calls.txt"
    stub = tmp_path / "stub_bridge.py"
    stub.write_text(
        "import json, sys\n"
        f"FIXTURE = json.loads({json.dumps(json.dumps(fixture))})\n"
        "action = sys.argv[1] if len(sys.argv) > 1 else ''\n"
        f"open({str(calls)!r}, 'a').write(action + '\\n')\n"
        "print(json.dumps(FIXTURE.get(action, {'status': 'error', 'error': 'unexpected action'})))\n", encoding="utf-8")
    compiled = tmp_path / "views"
    compiled.mkdir()
    env = dict(os.environ)
    env.update({
        "APP_ENV": "testing", "APP_KEY": "base64:" + "A" * 43 + "=", "APP_DEBUG": "true", "SESSION_DRIVER": "array",
        "CACHE_STORE": "array", "LOG_CHANNEL": "stderr", "VIEW_COMPILED_PATH": str(compiled), "DB_CONNECTION": "mariadb",
        "DB_HOST": os.getenv("DB_HOST", "127.0.0.1"), "DB_PORT": os.getenv("DB_PORT", "3306"),
        "DB_DATABASE": os.environ["DB_DATABASE"], "DB_USERNAME": os.getenv("DB_USERNAME", "root"),
        "DB_PASSWORD": os.getenv("DB_PASSWORD", ""), "CLI_BRIDGE_PATH": str(stub), "PYTHON_PATH": sys.executable,
    })
    yield {"env": env, "calls": calls, "db": db}
    _sql(db, "DELETE FROM sheet_updates WHERE `value` = %s", (MARK,))


def _kernel(env, paths):
    dash = str(DASH).replace("\\", "/")
    script = f"""<?php
require '{dash}/vendor/autoload.php';
$app = require '{dash}/bootstrap/app.php';
$kernel = $app->make(Illuminate\\Contracts\\Http\\Kernel::class);
$out = [];
foreach (json_decode($argv[1], true) as $path) {{
    $request = Illuminate\\Http\\Request::create($path, 'GET', [], [], [], ['HTTP_ACCEPT' => 'text/html,application/json']);
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
        result = subprocess.run([PHP, path, json.dumps(paths)], cwd=DASH, env=env, capture_output=True, text=True,
                                timeout=240, encoding="utf-8")
    finally:
        os.unlink(path)
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-3000:]
    return json.loads(result.stdout)


def test_the_page_opens_on_the_top_and_runs_no_python(health_app):
    (page,) = _kernel(health_app["env"], ["/system-diagnostics"])
    assert page["status"] == 200
    body = page["body"]
    assert body.index('data-health="now"') < body.index('data-health="advanced"')
    assert re.search(r'<details class="lq-health-advanced" data-health="advanced">\s*<summary', body)
    assert "تفاصيل متقدمة" in body and "فحص الاتصالات الآن" in body and "افحص النشر" in body
    initial = json.loads(re.search(r'id="lq-health-attention-initial">(.*?)</script>', body, re.DOTALL).group(1))
    assert initial["state"] in ("checking", "attention") and initial["pending"] is True
    assert not health_app["calls"].exists()


def test_a_dead_write_and_a_provider_outage_reach_the_top(health_app):
    _sql(health_app["db"], "INSERT INTO sheet_updates (`row_number`, `col_index`, `value`, sync_status) VALUES (5, 3, %s, 'DEAD')",
         (MARK,))
    before, ops, after = _kernel(health_app["env"], ["/api/system/attention", "/api/system/ops-health",
                                                     "/api/system/attention?after=1"])
    first, last = json.loads(before["body"]), json.loads(after["body"])
    assert first["status"] == "success" and "outbox" in _keys(first) and first["pending"] is True
    assert "ops_serper_credit" not in _keys(first)
    # once the page read ops-health (cached), the top says the outage too, with its fix
    assert json.loads(ops["body"])["status"] == "success"
    assert {"outbox", "ops_serper_credit"} <= set(_keys(last)) and last["pending"] is False
    serper = next(i for i in last["items"] if i["key"] == "ops_serper_credit")
    assert serper["title"] == "رصيد Serper خلص أو المفتاح مرفوض" and serper["action"]["href"] == "/settings?tab=keys#lq-key-serper"
    assert health_app["calls"].read_text(encoding="utf-8").split() == ["ops_health"]


def test_without_the_database_the_top_says_so(health_app):
    page, attention = _kernel(dict(health_app["env"], DB_PORT="1"), ["/system-diagnostics", "/api/system/attention"])
    assert page["status"] == 200 and "قاعدة البيانات ما بتردّ" in page["body"]
    body = json.loads(attention["body"])
    assert body["state"] == "attention" and _keys(body)[0] == "db"


# ---------------------------------------------------------------------------
# health.js under node
# ---------------------------------------------------------------------------

@NEEDS_NODE
def test_the_scripts_view_of_the_top():
    script = "globalThis.window = globalThis;\n" + read(HEALTH_JS) + """
const H = window.LaqtaHealth;
console.log(JSON.stringify({
  ok: H.attentionView({state: 'ok', title: 'كلشي تمام', text: 't', items: [], note: '', pending: false}),
  items: H.attentionView({state: 'attention', title: 'شو بدو منك', items: [{key: 'outbox', tone: 'danger', title: 'x',
          text: 'y', action: {label: 'صفحة التشغيل', href: '/batch-automation'}}, 'junk']}),
  bad: [H.attentionView(null), H.attentionView({state: 'weird'})],
  url: H.ATTENTION_URL
}));
"""
    result = subprocess.run([NODE, "-"], input=script, capture_output=True, text=True, timeout=60, encoding="utf-8")
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout.strip().splitlines()[-1])
    assert out["ok"] == {"state": "ok", "title": "كلشي تمام", "text": "t", "items": [], "note": "", "pending": False}
    assert out["items"]["items"] == [{"key": "outbox", "tone": "danger", "title": "x", "text": "y",
                                      "action": {"label": "صفحة التشغيل", "href": "/batch-automation", "goto": ""}}]
    assert out["bad"] == [None, None] and out["url"] == "/api/system/attention"
