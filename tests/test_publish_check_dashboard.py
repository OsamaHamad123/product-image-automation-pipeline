"""«فحص النشر» on the dashboard: the Health page card, its endpoint, and the link from the review screen.

- Static: the routes, the controller running the bridge action with a kill timeout (like the connection check), the
  card with its button, explanation and cost note, the review screen's failed-approvals panel linking to the check.
- PHP helpers under the PHP CLI and the page script under node: the card says the same on the server and in the
  browser; the result the page gets keeps only the known fields and hides stored secrets; only the button runs the
  check (it may cost a background-removal call), and a failed request keeps the last result with an Arabic reason.
- The Laravel app through its HTTP kernel with a stub bridge (MariaDB and dashboard/vendor; skipped without them).
"""

import json
import os
import re
import sys
from pathlib import Path

import pytest

import publish_check as pc
from laqta_review_harness import NODE
from test_laqta_health import (DASH, PHP, SECRETS, TOUCHED, VENDOR_AUTOLOAD, _js, _kernel, _node, _php, _put,
                               _settings, _sql, php_value)
from test_laqta_review_guards import fixture, page, picked

ROOT = Path(__file__).resolve().parents[1]
CONTROLLERS = DASH / "app" / "Http" / "Controllers"
HEALTH_PHP = CONTROLLERS / "HealthController.php"
REVIEW_PHP = CONTROLLERS / "ReviewController.php"
BRIDGE_PHP = DASH / "app" / "Services" / "PythonBridge.php"
VIEW = DASH / "resources" / "views" / "dashboard" / "diagnostics.blade.php"
HEALTH_JS = DASH / "public" / "js" / "health.js"
HEALTH_CSS = DASH / "public" / "css" / "pages" / "health.css"
REVIEW_APP = DASH / "public" / "js" / "review" / "app.js"
REVIEW_CSS = DASH / "public" / "css" / "pages" / "review.css"
ROUTES = DASH / "routes" / "web.php"

NEEDS_PHP = pytest.mark.skipif(PHP is None, reason="php is not installed")
NEEDS_NODE = pytest.mark.skipif(NODE is None, reason="node is not installed")

ARABIC = re.compile(r"[؀-ۿ]")


def read(path):
    return path.read_text(encoding="utf-8")


def _method(text, name):
    start = text.index(f"function {name}(")
    nxt = re.search(r"\n    (?:public|private|protected)(?: static)? function ", text[start + 1:])
    return text[start:start + 1 + nxt.start()] if nxt else text[start:]


def _js_function(js, name, indent=8):
    pad = " " * indent
    start = js.index(f"{pad}function {name}(")
    return js[start:js.index(f"\n{pad}}}\n", start)]


def _result(overall="fail", **extra):
    """A publish_check result as the bridge returns it."""
    steps = [
        {"key": "download", "status": "ok", "ms": 1240, "title_ar": "تنزيل الصورة",
         "detail_ar": "نزلت مباشرة من موقع المتجر (بدون بروكسي).", "action_ar": "", "code": ""},
        {"key": "process", "status": "ok" if overall == "ok" else "warn", "ms": 3051, "title_ar": "عزل الخلفية والمعالجة",
         "detail_ar": "انعزلت الخلفية بـ PhotoRoom.", "action_ar": "جرّب مزوّد عزل تاني." if overall != "ok" else "",
         "code": "" if overall == "ok" else "quality_flags"},
        {"key": "upload", "status": "fail" if overall == "fail" else "ok", "ms": 849, "title_ar": "الرفع على Cloudinary",
         "detail_ar": "ما انرفعت الصورة التجريبية: Cloudinary رفض مفتاح الحساب.",
         "action_ar": "مفتاح Cloudinary مرفوض: حدّثه بالإعدادات.", "code": "upload_auth" if overall == "fail" else ""},
        {"key": "sheet", "status": "ok", "ms": 2203, "title_ar": "الكتابة بالشيت",
         "detail_ar": "النشر رح يكتب بتبويب «Products».", "action_ar": "", "code": ""},
    ]
    doc = {"ok": overall == "ok", "overall": overall, "started_at": "2026-10-04T09:10:00+00:00",
           "finished_at": "2026-10-04T09:10:08+00:00", "duration_ms": 8100, "steps": steps,
           "summary_ar": {"ok": "النشر شغّال.", "warn": "النشر لازم يمشي، بس في ملاحظة.",
                          "fail": "النشر واقف عند «الرفع على Cloudinary»: مفتاح Cloudinary مرفوض: حدّثه بالإعدادات."}[overall],
           "failed_step": "upload" if overall == "fail" else None,
           "sample": {"kind": "review", "product_name": "Almarai Milk 1L", "row_number": 12, "source": "lulu.ae"},
           "sheet_tab": "Products", "run_active": False, "notes": [], "cost_note": pc.COST_NOTE}
    doc.update(extra)
    return doc


# ---------------------------------------------------------------------------
# Static
# ---------------------------------------------------------------------------

def test_routes():
    routes = read(ROUTES)
    health = "\\App\\Http\\Controllers\\HealthController"
    assert f"Route::post('/api/system/publish-check', [{health}::class, 'runPublishCheck']);" in routes
    assert f"Route::get('/api/system/publish-check', [{health}::class, 'lastPublishCheckJson']);" in routes


def test_the_controller_runs_the_bridge_action_with_a_kill_timeout():
    text = read(HEALTH_PHP)
    run = _method(text, "runPublishCheck")
    assert "shell_exec" not in run and "PythonBridge::run(" not in run
    assert "proc_open(" in run and "proc_terminate($process, 9)" in run
    assert "@set_time_limit(self::PUBLISH_CHECK_KILL_SECONDS + 30)" in run
    assert "'publish_check'" in run and "PythonBridge::bridgePath()" in run and "PythonBridge::pythonPath()" in run
    assert "tempnam(" in run and "@unlink($outFile)" in run          # output to a file, removed afterwards
    kill = int(re.search(r"PUBLISH_CHECK_KILL_SECONDS = (\d+);", text).group(1))
    limit = int(re.search(r"TIME_LIMIT = (\d+);", read(BRIDGE_PHP)).group(1))
    assert sum(pc.STEP_TIMEOUTS.values()) < kill <= limit
    # the card's note states the same upper bound
    assert kill % 60 == 0 and f"وما بيطول أكتر من {kill // 60} دقايق" in read(VIEW)
    # every error the page may show is Arabic, and the raw output never reaches the page
    for message in re.findall(r"publishCheckError\('([^']*)'", run):
        assert ARABIC.search(message), message
    assert "raw_output" not in run and "getMessage()" not in run.split("Log::error")[0]
    # the page and the GET read the saved result; neither runs the check
    assert "PUBLISH_CHECK_FILE = '../temp/publish_check_last.json'" in text
    assert pc.LAST_RESULT_PATH.replace("\\", "/").endswith("temp/publish_check_last.json")
    for name in ("page", "lastPublishCheckJson"):
        body = _method(text, name)
        assert "runPublishCheck" not in body and "proc_open" not in body and "PythonBridge::run" not in body


def test_the_card_explains_the_check_and_what_it_costs():
    view = read(VIEW)
    card = view[view.index('id="publish-check"'):view.index('id="publish-check-initial"')]
    for text in ("فحص النشر",
                 "بيجرّب النشر كامل على صورة تجريبية بدون ما يلمس منتجاتك: التنزيل، عزل الخلفية، الرفع، والكتابة بالشيت.",
                 "افحص النشر", "ممكن يكلّف طلب عزل خلفية واحد", "بتنرفع على Cloudinary وبتنمسح",
                 "هالخطوة ممكن تكلّف طلب عزل خلفية واحد.",
                 "منكتب عنوان عمود الرابط نفسه فوق حاله"):
        assert text in card, text
    for hook in ("publish", "publish-run", "publish-run-label", "publish-summary", "publish-dot", "publish-meta",
                 "publish-steps", "publish-notes"):
        assert f'data-health="{hook}"' in card, hook
    for attr in ("data-step-icon", "data-step-title", "data-step-state", "data-step-time", "data-step-detail",
                 "data-step-action"):
        assert attr in card, attr
    assert '<script type="application/json" id="publish-check-initial">@json($lastPublishCheck)</script>' in view
    assert "@foreach ($publish['steps'] as $step)" in card
    assert "title=\"{{ $step['code'] }}\"" in card                  # codes only in the tooltip
    assert 'role="status" aria-live="polite"' in card


def test_every_arabic_string_of_the_check_is_on_the_page_and_in_the_script():
    php, js = read(HEALTH_PHP), read(HEALTH_JS)
    for title in pc.STEP_TITLES.values():
        assert title in php and title in js, title
    for word in ("نجحت", "فيها ملاحظة", "ما زبطت", "ما انفحصت", "لسا ما انفحصت"):
        assert f"'{word}'" in php and f"'{word}'" in js, word
    for text in ("لسا ما انعمل فحص للنشر.", "الصورة: الصورة التجريبية", "آخر فحص للنشر: "):
        assert text in js and (text in php or text in read(VIEW)), text
    for text in ("جاري الفحص…", "خلص فحص النشر: كل الخطوات نجحت.", "خلص فحص النشر: النشر لازم يمشي، بس في ملاحظات.",
                 "ما خلص فحص النشر: ", "ما قدرنا نوصل للخادم لنفحص النشر.", "افحص النشر"):
        assert text in js, text
    emoji = re.compile("[\U0001F300-\U0001FAFF☀-➿️⏭]")
    for path in (VIEW, HEALTH_JS, HEALTH_CSS, HEALTH_PHP):
        assert not emoji.findall(read(path)), path.name


def test_only_the_button_runs_the_check():
    js = read(HEALTH_JS)
    assert js.count("PUBLISH_CHECK_URL") == 2                          # the constant and the one POST in run()
    run = _js_function(js, "run")
    assert "deps.fetchJson(PUBLISH_CHECK_URL, { method: 'POST', body: {} })" in run
    assert "publishButton.addEventListener('click', function () { publish.run(); });" in js
    assert js.count("publish.run()") == 1
    assert "fetchJson" not in _js_function(js, "show")
    # arriving from the review screen focuses the button; it never presses it
    hash_line = next(line for line in js.splitlines() if "#publish-check" in line and "hash" in line)
    assert "focus()" in hash_line and "run(" not in hash_line


def test_the_review_screen_links_failed_approvals_to_the_check():
    app = read(REVIEW_APP)
    panel = app[app.index("function renderJobs("):app.index("function setupJobs(")]
    failed = panel[panel.index("} else {"):panel.index("const failed = st.jobs.filter")]
    assert "j.state === 'failed' && j.type !== 'reject'" in failed       # an approval or upload, not a reject
    assert "href: S.urls.publishCheck" in failed and "text: 'افحص النشر'" in failed
    assert "publishCheck: '/system-diagnostics#publish-check'" in app
    assert "'publishCheck' => route('dashboard.diagnostics') . '#publish-check'," in read(REVIEW_PHP)
    assert ".rv-jobs__check {" in read(REVIEW_CSS)


@NEEDS_NODE
def test_a_failed_approval_shows_the_link_to_the_check(tmp_path):
    out = page(r"""
openRow(30);
press('Enter');
await flush();
answer(requests('/api/select_image')[0], { status: 'failed', error: 'download_timeout' }, 500);
await flush();
out.links = document.querySelectorAll('.rv-jobs__check').map(a => [a.getAttribute('href'), a.textContent, a.tagName]);
out.text = jobsText();
""", tmp_path, fixture([picked(30, "Almarai Milk 1L"), picked(31, "Almarai Laban 1L")]), config={"row": 30})
    assert out["links"] == [["/system-diagnostics#publish-check", "افحص النشر", "A"]]
    # «وحدة فشلت: أعد المحاولة» (was «خلصت: 0 مشيت، ووحدة ما مشيت»)
    assert "وحدة فشلت: أعد المحاولة" in out["text"] and "ما قدرنا ننزّل الصورة" in out["text"]


# ---------------------------------------------------------------------------
# PHP helpers and the page script
# ---------------------------------------------------------------------------

RESULTS = [None, _result("ok"), _result("fail"), _result("warn", sample={"kind": "bundled", "product_name": "Laqta Test"},
                                                          notes=[pc.RUN_ACTIVE_NOTE])]


@NEEDS_PHP
@NEEDS_NODE
def test_the_card_says_the_same_on_the_server_and_in_the_script():
    php = _php("foreach (" + php_value(RESULTS) + " as $r) {"
               " $p = HealthController::publicPublishCheck($r);"
               " $out['public'][] = $p; $out['views'][] = HealthController::publishCheckView($p); }")
    js = _node(_js(HEALTH_JS) + "const H = window.LaqtaHealth; const now = Date.parse('2026-10-04T12:00:00+00:00');"
               "console.log(JSON.stringify(" + json.dumps(php["public"], ensure_ascii=False)
               + ".map(r => H.publishView(r, now))));")
    for server, script in zip(php["views"], js):
        assert {k: v for k, v in server.items() if k != "when"} == {k: v for k, v in script.items() if k != "when"}
        assert bool(server["when"]) == bool(script["when"])
    never, ok, fail, warn = php["views"]
    assert never["state"] == "never" and [s["label"] for s in never["steps"]] == ["لسا ما انفحصت"] * 4
    assert [s["title"] for s in never["steps"]] == list(pc.STEP_TITLES.values())
    assert ok["tone"] == "success" and [s["icon"] for s in ok["steps"]] == ["check"] * 4
    assert [s["time"] for s in ok["steps"]] == ["1.2 ث", "3.1 ث", "0.8 ث", "2.2 ث"]
    assert ok["sample"] == "الصورة: Almarai Milk 1L (صف 12)" and re.match(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}$", ok["when"])
    assert fail["tone"] == "danger" and fail["steps"][2]["action"] == "مفتاح Cloudinary مرفوض: حدّثه بالإعدادات."
    assert fail["steps"][2]["code"] == "upload_auth" and fail["steps"][0]["action"] == ""
    assert warn["tone"] == "warning" and warn["sample"] == "الصورة: الصورة التجريبية"
    assert warn["notes"] == [pc.RUN_ACTIVE_NOTE]


@NEEDS_PHP
def test_the_page_gets_only_known_fields_with_secrets_hidden():
    secret = "SECRET-PHOTO-a1b2c3"
    doc = _result("fail", raw_logs="RAW " + secret, cost_note="x")
    doc["steps"][1]["detail_ar"] = f"PhotoRoom رفض المفتاح {secret}"
    doc["steps"][1]["code"] = f"photoroom_{secret}"
    doc["steps"].append({"key": "../../etc", "status": "ok", "detail_ar": "x"})
    doc["steps"][3]["status"] = "weird"
    doc["sample"]["image_url"] = f"https://x.ae/a.jpg?key={secret}"
    out = _php(f"$out = HealthController::publicPublishCheck({php_value(doc)}, ['{secret}']);")
    dumped = json.dumps(out, ensure_ascii=False)
    assert secret not in dumped and "[محجوب]" in dumped
    assert set(out) == {"ok", "overall", "started_at", "finished_at", "duration_ms", "summary_ar", "failed_step",
                        "sheet_tab", "sample", "run_active", "notes", "steps"}
    assert set(out["sample"]) == {"kind", "product_name", "row_number"}
    assert [s["key"] for s in out["steps"]] == ["download", "process", "upload", "sheet"]
    assert out["steps"][3]["status"] == "fail"                         # an unknown status is never shown as passed
    assert _php("$out = [HealthController::publicPublishCheck(null), HealthController::publicPublishCheck(['x' => 1]),"
                " HealthController::lastPublishCheck('/nonexistent/x.json')];") == [None, None, None]


@NEEDS_NODE
def test_the_button_shows_the_steps_running_then_the_result():
    stored = _result("ok")
    fresh = _result("fail")
    out = _node(_js(HEALTH_JS) + "const H = window.LaqtaHealth; const views = [], toasts = [], busy = [], fetched = [];\n"
                "let answer;\n"
                "const c = H.createPublishCheck({ initial: " + json.dumps(stored, ensure_ascii=False) + ",\n"
                "  fetchJson: (url, opts) => { fetched.push([url, opts.method]); return new Promise(r => { answer = r; }); },\n"
                "  renderPublish: v => views.push(v), setPublishBusy: b => busy.push(b),\n"
                "  toast: (t, v) => toasts.push([t, v]), now: () => Date.parse('2026-10-04T12:00:00+00:00') });\n"
                "c.start();\n"
                "const p = c.run(); const again = c.run();\n"
                "const during = views[views.length - 1];\n"
                "answer({ ok: true, status: 200, data: { status: 'success', result: " + json.dumps(fresh, ensure_ascii=False) + " } });\n"
                "Promise.all([p, again]).then(([done, second]) => console.log(JSON.stringify({ done, second, fetched,"
                " busy, toasts, first: views[0].state, during, last: views[views.length - 1] })));")
    assert out["first"] == "ok" and out["fetched"] == [["/api/system/publish-check", "POST"]]     # one request
    assert out["done"] is True and out["second"] is False and out["busy"] == [True, False]
    assert out["during"]["state"] == "running"
    assert {s["label"] for s in out["during"]["steps"]} == {"جاري الفحص…"} and out["during"]["steps"][0]["icon"] == "spinner"
    assert out["last"]["state"] == "fail" and out["last"]["steps"][2]["status"] == "fail"
    assert out["toasts"] == [["خلص فحص النشر: في خطوة ما زبطت، شوف شو لازم تعمل.", "danger"]]


@NEEDS_NODE
def test_a_failed_request_keeps_the_last_result_and_says_why_in_arabic():
    stored = _result("ok")
    out = _node(_js(HEALTH_JS) + "const H = window.LaqtaHealth; const views = [], toasts = [];\n"
                "const make = res => H.createPublishCheck({ initial: " + json.dumps(stored, ensure_ascii=False) + ",\n"
                "  fetchJson: () => res, renderPublish: v => views.push(v), setPublishBusy: () => {},\n"
                "  toast: (t, v) => toasts.push([t, v]), now: () => 0 });\n"
                "make(Promise.resolve({ ok: false, status: 504, data: { status: 'failed', error: 'فحص النشر أخد أكتر من 7 دقايق فوقفناه.' } })).run()\n"
                " .then(() => make(Promise.resolve({ ok: false, status: 500, data: { status: 'failed', error: 'Traceback (most recent call last)' } })).run())\n"
                " .then(() => make(Promise.reject(new Error('offline'))).run())\n"
                " .then(() => console.log(JSON.stringify({ toasts, last: views[views.length - 1].state })));")
    assert out["toasts"] == [["ما خلص فحص النشر: فحص النشر أخد أكتر من 7 دقايق فوقفناه.", "danger"],
                             ["ما خلص فحص النشر: الخادم ما رجّع نتيجة.", "danger"],
                             ["ما قدرنا نوصل للخادم لنفحص النشر.", "danger"]]
    assert out["last"] == "ok"                                           # the saved result again


@NEEDS_NODE
def test_opening_the_page_never_runs_the_check():
    out = _node(_js(HEALTH_JS) + "const H = window.LaqtaHealth; const fetched = [], views = [];\n"
                "const c = H.createPublishCheck({ initial: null, fetchJson: url => { fetched.push(url); return new Promise(() => {}); },\n"
                "  renderPublish: v => views.push(v), setPublishBusy: () => {}, toast: () => {}, now: () => 0 });\n"
                "c.start(); console.log(JSON.stringify({ fetched, view: views[0] }));")
    assert out["fetched"] == [] and out["view"]["state"] == "never"
    assert out["view"]["summary"] == "لسا ما انعمل فحص للنشر." and len(out["view"]["steps"]) == 4


# ---------------------------------------------------------------------------
# The Laravel app with a stub bridge
# ---------------------------------------------------------------------------

@pytest.fixture
def pc_app(mariadb_or_skip, tmp_path):
    if PHP is None or not VENDOR_AUTOLOAD.exists():
        pytest.skip("php or dashboard/vendor is not installed")
    db = mariadb_or_skip
    saved = {k: v for k, v in _settings(db).items() if k in TOUCHED}
    _put(db, dict(SECRETS))
    doc = _result("fail")
    doc["steps"][2]["detail_ar"] = "Cloudinary رفض المفتاح " + SECRETS["cloudinary_api_secret"]
    doc["raw_logs"] = SECRETS["photoroom_api_key"]
    calls = tmp_path / "calls.txt"
    stub = tmp_path / "stub_bridge.py"
    stub.write_text(
        "import json, os, sys\n"
        f"DOC = json.loads({json.dumps(json.dumps(dict(doc, status='success'), ensure_ascii=False))})\n"
        f"open({str(calls)!r}, 'a').write((sys.argv[1] if len(sys.argv) > 1 else '') + '\\n')\n"
        "mode = os.environ.get('LQ_STUB_MODE')\n"
        "print('a library printed this line')\n"
        "if mode == 'down':\n"
        "    print(json.dumps({'status': 'failed', 'error': 'ما قدرنا نشغّل فحص النشر (التفاصيل في temp/search.log).'}))\n"
        "elif mode == 'garbage':\n"
        "    print('Traceback (most recent call last): boom')\n"
        "elif sys.argv[1] == 'publish_check':\n"
        "    print(json.dumps(DOC))\n"
        "else:\n"
        "    print(json.dumps({'status': 'error', 'error': 'unexpected action'}))\n",
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
    yield {"env": env, "calls": calls}
    _sql(db, "DELETE FROM system_settings WHERE `key` IN (" + ", ".join(["%s"] * len(TOUCHED)) + ")", tuple(TOUCHED))
    _put(db, saved)


def test_the_endpoint_runs_the_check_and_returns_it_without_secrets(pc_app):
    page_, run, last = _kernel(pc_app["env"], [["GET", "/system-diagnostics", {}],
                                               ["POST", "/api/system/publish-check", {}],
                                               ["GET", "/api/system/publish-check", {}]])
    assert page_["status"] == 200 and 'id="publish-check"' in page_["body"] and "افحص النشر" in page_["body"]
    assert "فحص النشر" in page_["body"] and 'id="publish-check-initial"' in page_["body"]
    assert run["status"] == 200, run["body"][:2000]
    body = json.loads(run["body"])
    assert body["status"] == "success" and body["result"]["overall"] == "fail"
    assert [s["status"] for s in body["result"]["steps"]] == ["ok", "warn", "fail", "ok"]
    assert body["result"]["steps"][2]["code"] == "upload_auth" and "[محجوب]" in body["result"]["steps"][2]["detail_ar"]
    assert "raw_logs" not in body["result"] and "cost_note" not in body["result"]
    assert last["status"] == 200 and json.loads(last["body"])["status"] == "success"
    for response in (page_, run, last):
        for value in SECRETS.values():
            assert value not in response["body"], value
    # the page and the GET call nothing; the POST runs the bridge action once
    assert pc_app["calls"].read_text().split() == ["publish_check"]


@pytest.mark.parametrize("mode, text", [("down", "ما قدرنا نشغّل فحص النشر"), ("garbage", "فحص النشر ما رجّع نتيجة")])
def test_a_failed_bridge_answers_in_arabic(pc_app, mode, text):
    (run,) = _kernel(dict(pc_app["env"], LQ_STUB_MODE=mode), [["POST", "/api/system/publish-check", {}]])
    body = json.loads(run["body"])
    assert run["status"] == 500 and body["status"] == "failed" and body["error"].startswith(text)
    assert "Traceback" not in run["body"]
