"""Dashboard side of work package R (running the automation never destroys work and says what is happening).

- ApiController: stop / reset never delete queue rows or review work; they go through cli_bridge run_control.
- QueueStats: the run phase, its Arabic texts and the red banner (pure functions, run under the PHP CLI).
- The views' scripts run under node: the Run page (public/js/run.js) and the Home page (public/js/home.js)
  through their pure view functions and page controllers (end-of-run refresh, per-run progress, the red banner),
  the old sidebar against a small stub DOM. Bulk approve locking and the review shortcuts moved with the review
  grid to the review page (/catalog?mode=bulk) and are checked there.
PHP or node checks are skipped when the binary is not installed.
"""

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DASH = ROOT / "dashboard"
VIEWS = DASH / "resources" / "views"
CONTROLLER = DASH / "app" / "Http" / "Controllers" / "ApiController.php"
QUEUE_STATS = DASH / "app" / "Services" / "QueueStats.php"
BATCH = VIEWS / "dashboard" / "batch_automation.blade.php"
INDEX = VIEWS / "dashboard" / "index.blade.php"
LAYOUT = VIEWS / "layouts" / "laqta.blade.php"
JS = DASH / "public" / "js"
RUN_JS = [JS / "run-common.js", JS / "run.js"]         # the Run page (batch_automation.blade.php)
HOME_JS = [JS / "run-common.js", JS / "home.js"]       # the Home page (index.blade.php)
PHP = shutil.which("php")
NODE = shutil.which("node")


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def method(text: str, name: str) -> str:
    start = text.index(f"public function {name}(")
    nxt = text.find("\n    public function ", start + 1)
    return text[start:nxt if nxt > 0 else len(text)]


# ---------------------------------------------------------------------------
# ApiController
# ---------------------------------------------------------------------------

def test_stop_and_reset_delete_nothing_and_use_the_bridge():
    text = read(CONTROLLER)
    stop, reset = method(text, "stopBatch"), method(text, "resetBatch")
    for body in (stop, reset):
        assert "DELETE" not in body.upper().replace("DELETED", "")
        assert "automation_queue" not in body and "curation_candidates" not in body
        assert "DB::" not in body, "queue state changes go through local_cache_db (run_control)"
    assert "$this->runPython('run_control', ['op' => 'stop', 'worker' => $worker])" in stop
    assert "$this->runPython('run_control', ['op' => 'reset', 'worker' => $worker])" in reset
    # the process is still killed in PHP (it needs the OS), for both buttons
    assert "$this->terminateWorker($process)" in stop and "$this->terminateWorker($process)" in reset
    helper = text[text.index("private function terminateWorker"):text.index("private function removeRunFiles")]
    assert "taskkill /F /PID" in helper and "kill -9" in helper
    # stop keeps the lock while the enqueue reads the sheet (the worker honours the request when it starts)
    assert "if (in_array($worker, ['killed', 'none'], true))" in stop


def test_run_all_prepares_the_run_before_starting_it():
    body = method(read(CONTROLLER), "runAll")
    prepare = body.index("$this->runPython('run_control', ['op' => 'start'])")
    lock = body.index("file_put_contents($lockFile, 'STARTING')")
    # Review fix: the run is prepared BEFORE the STARTING lock is written. The stop button appears with the lock;
    # with the lock first, a stop pressed during the 1-3 s preparation was recorded and then cleared by
    # prepare_run, while the dashboard said «سُجل طلب الإيقاف» and the run went on.
    assert prepare < lock < body.index("shell_exec($linuxCmd)")
    failure = body[prepare:lock]
    assert "return response()->json(['status' => 'failed'" in failure     # a failed preparation writes no lock
    # a run started elsewhere (nightly, another tab) during the preparation is refused before the lock is taken
    assert "$this->pipelineProcess()['state'] !== 'none'" in failure


def test_batch_status_reports_the_run_and_the_phase():
    body = method(read(CONTROLLER), "batchStatus")
    assert "QueueStats::run($state->run_id ?? null)" in body
    assert "QueueStats::runPhase($process['state'], $status, $pauseRequested, $stopRequested, $readyForReview)" in body
    for key in ("'phase' =>", "'phase_text' =>", "'alert' =>", "'run' => $run", "'stop_requested' =>",
                "'queue' => $counters['by_status']"):
        assert key in body, key
    assert "'total' => $run['total']" in body and "'current' => $run['processed']" in body


@pytest.mark.skipif(PHP is None or not os.path.isdir("/proc") or not hasattr(os, "fork"),
                    reason="needs the PHP CLI and a Linux /proc")
def test_a_killed_worker_not_yet_reaped_counts_as_stopped():
    """Review fix: after kill -9 the worker can stay a zombie until its parent (or init) reaps it; posix_kill(pid, 0)
    still succeeds on a zombie. terminateWorker then reported 'running' (could not kill), so stop only recorded a
    request, kept the lock, and left the killed worker's rows in 'processing' until their 15-minute lease ran out."""
    import signal
    import time

    zombie = os.fork()
    if zombie == 0:                       # the child exits at once and is not reaped: a zombie
        os._exit(0)
    alive = subprocess.Popen(["sleep", "30"])
    try:
        for _ in range(100):
            with open(f"/proc/{zombie}/stat") as fh:
                if fh.read().rsplit(")", 1)[1].split()[0] == "Z":
                    break
            time.sleep(0.01)
        controller = str(CONTROLLER).replace("\\", "/")
        out = _run_php(f"""<?php
namespace App\\Http\\Controllers {{ class Controller {{}} }}
namespace {{
require '{controller}';
$c = new App\\Http\\Controllers\\ApiController();
$alive = new ReflectionMethod($c, 'processAlive');
$alive->setAccessible(true);
$terminate = new ReflectionMethod($c, 'terminateWorker');
$terminate->setAccessible(true);
echo json_encode([
    'zombie' => $alive->invoke($c, '{zombie}'),
    'live' => $alive->invoke($c, '{alive.pid}'),
    'kill' => $terminate->invoke($c, ['state' => 'running', 'pid' => '{alive.pid}']),
]);
}}
""")
    finally:
        alive.kill()
        alive.wait()
        os.waitpid(zombie, 0)
    assert out == {"zombie": False, "live": True, "kill": "killed"}

def _run_php(script: str):
    with tempfile.NamedTemporaryFile("w", suffix=".php", delete=False, encoding="utf-8") as fh:
        fh.write(script)
        path = fh.name
    try:
        result = subprocess.run([PHP, path], capture_output=True, text=True, timeout=60, encoding="utf-8")
    finally:
        os.unlink(path)
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


def _queue_stats(calls: str):
    service = str(QUEUE_STATS).replace("\\", "/")
    return _run_php(f"<?php\nrequire '{service}';\nuse App\\Services\\QueueStats;\n$out = [];\n{calls}\n"
                    "echo json_encode($out, JSON_UNESCAPED_UNICODE);\n")


@pytest.mark.skipif(PHP is None, reason="php is not installed")
def test_run_totals_and_phases():
    out = _queue_stats("""
$out['run'] = QueueStats::runTotals('r1', ['pending' => 4, 'processing' => 1, 'ready_for_review' => 2,
                                           'completed' => 3, 'failed' => 1]);
foreach ([
    ['starting', 'idle', 0, 0, 5], ['starting', 'starting', 0, 1, 0],
    ['running', 'pre_caching', 0, 0, 0], ['running', 'pre_caching', 1, 0, 0], ['running', 'pre_caching', 1, 1, 0],
    ['running', 'starting', 0, 0, 0],
    ['none', 'error', 0, 0, 3], ['none', 'provider_down', 0, 0, 0],
    ['none', 'curation_pending', 0, 0, 2], ['none', 'curation_pending', 0, 0, 0], ['none', 'idle', 0, 0, 0],
] as [$p, $s, $pause, $stop, $ready]) {
    $out['phases'][] = QueueStats::runPhase($p, $s, $pause, $stop, $ready);
}
$out['text'] = [
    'starting' => QueueStats::phaseText('starting', 0, 0),
    'starting_stop' => QueueStats::phaseText('starting', 1, 0),
    'review' => QueueStats::phaseText('review', 0, 12),
    'idle' => QueueStats::phaseText('idle', 0, 0),
];
""")
    assert out["run"] == {"run_id": "r1", "total": 11, "processed": 6, "ready_for_review": 2, "completed": 3,
                          "failed": 1, "pending": 4, "processing": 1}
    assert out["phases"] == ["starting", "starting", "running", "paused", "stopping", "starting", "error", "error",
                             "review", "idle", "idle"]
    assert out["text"]["starting"] == "جاري قراءة الشيت وتجهيز الطابور…"
    assert "سيتوقف العامل فور بدئه قبل معالجة أي منتج" in out["text"]["starting_stop"]
    assert out["text"]["review"] == "انتهى التحضير: 12 منتج بانتظار المراجعة."
    assert "لا توجد منتجات بانتظار المراجعة" in out["text"]["idle"]


@pytest.mark.skipif(PHP is None, reason="php is not installed")
def test_alert_texts_are_arabic():
    cases = {
        "enqueue": ("error", "ENQUEUE_FAILED: فلتر الصفوف غير صالح: «5-x».", False),
        "sheets": ("error", "SHEETS_UNAVAILABLE: Google Sheets connection failed", False),
        "sheet_config": ("error", "SHEET_CONFIG: sheet not found: Products", False),
        "provider": ("provider_down", "PROVIDER_DOWN: search providers unavailable; remaining rows stay pending | "
                                      "SERPER_CREDIT: رصيد Serper انتهى أو المفتاح مرفوض", False),
        "verifier": ("pre_caching", "VERIFIER_NOT_CONFIGURED: no Gemini key, every result goes to human review", True),
        "gemini": ("curation_pending", "GEMINI_DOWN: Gemini لا يستجيب", False),
        "unknown": ("idle", "SOMETHING_NEW: raw text", False),
        "budget": ("idle", "BUDGET_REACHED: daily search budget 5.00 USD reached (spent 5.01); remaining rows stay "
                           "pending", False),
        "db": ("error", "DB_UNAVAILABLE: database unreachable", False),
        "error_without_notice": ("error", "", False),
        "idle": ("idle", "", False),
    }
    lit = lambda v: json.dumps(v, ensure_ascii=False)           # a PHP double-quoted literal (no $ in the texts)
    calls = "\n".join(f"$out[{lit(k)}] = QueueStats::alertText({lit(s)}, {lit(n)}, "
                      f"{'true' if r else 'false'});" for k, (s, n, r) in cases.items())
    out = _queue_stats(calls)
    assert out["enqueue"] == "فشل تجهيز التشغيل ولم يبدأ العامل: فلتر الصفوف غير صالح: «5-x»."
    assert out["sheets"].startswith("تعذر الاتصال بـ Google Sheets")
    assert out["sheet_config"] == "إعداد الشيت غير صالح فتوقف التشغيل قبل معالجة أي منتج: sheet not found: Products"
    assert out["provider"].startswith("محركات البحث غير متاحة") and out["provider"].endswith("رصيد Serper انتهى أو المفتاح مرفوض")
    assert out["verifier"].startswith("لا يوجد مفتاح Gemini")
    assert out["gemini"] == "Gemini لا يستجيب"
    assert out["unknown"] == "SOMETHING_NEW: raw text"
    assert out["budget"].startswith("بلغ صرف اليوم الميزانية اليومية") and "spent" not in out["budget"]
    assert out["db"].startswith("تعذر الوصول إلى قاعدة البيانات")
    assert out["error_without_notice"].startswith("توقف التشغيل بسبب خطأ غير معروف")
    assert out["idle"] == ""


# ---------------------------------------------------------------------------
# Views under node with a stub DOM
# ---------------------------------------------------------------------------

def _node(script: str):
    result = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=60, encoding="utf-8")
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


def _status(phase, **extra):
    base = {"phase": phase, "phase_text": f"text:{phase}", "alert": "", "status": "idle", "is_running": False,
            "stop_requested": 0, "pause_requested": 0, "run": {"run_id": "r1", "total": 20, "processed": 5,
                                                               "ready_for_review": 3, "completed": 1, "failed": 1,
                                                               "pending": 14, "processing": 1},
            "queue": {"failed": 7, "pending": 300}, "ready_for_review": 3, "approved": 1, "failed_by_code": {},
            "notice": "", "current_product": "Milk"}
    base.update(extra)
    return base


def _page_js(files) -> str:
    """The page's scripts without a DOM: they only define window.LaqtaRunPage / LaqtaHomePage (no auto-mount)."""
    return "globalThis.window = globalThis;\n" + "\n".join(read(f) for f in files) + "\n"


# Fake deps for the page controllers: every fetch, rendered view, confirmation and toast is recorded.
CONTROLLER_HARNESS = r"""
const fetchLog = [];
const toasts = [];
const confirms = [];
const views = { live: [], plan: [], start: [], overview: [], banner: [] };
let lives = [];
let liveIndex = 0;
let confirmAnswer = true;
function fakeFetch(url, opts) {
    fetchLog.push({ url, method: (opts && opts.method) || 'GET', body: opts && opts.body });
    if (url === '/api/run/live') return Promise.resolve({ ok: true, status: 200, data: lives[Math.min(liveIndex++, lives.length - 1)] });
    if (url.startsWith('/api/run/plan')) return Promise.resolve({ ok: true, status: 200, data: { status: 'success', total: 3, skipped_final: 1, estimate: {} } });
    if (url.startsWith('/api/overview')) return Promise.resolve({ ok: true, status: 200, data: { status: 'success', kpis: {}, sheet: { status: 'ok', total: 0, stages: [] }, readiness: { status: 'ok', brands: [] }, cost: { status: 'ok', week_usd: 0 }, services: {}, alerts: [] } });
    return Promise.resolve({ ok: true, status: 200, data: { status: 'success', message: 'ok' } });
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
const freshPlans = () => fetchLog.filter(f => f.url.startsWith('/api/run/plan') && f.url.includes('refresh=1')).length;
"""


def _snap(batch, run=None):
    """A GET /api/run/live snapshot: the /api/batch-status payload plus the run summary (RunController::snapshot)."""
    return {"status": "success", "batch": batch, "run": run, "recent": []}


def _run_summary(total=20, processed=20, **counts):
    base = {"proposed": 0, "none": 0, "not_found": 0, "error": 0, "requeued": 0, "approved": 0, "searching": 0,
            "waiting": 0}
    base.update(counts)
    return {"run_id": "r1", "total": total, "processed": processed, "counts": base, "rows_label": "2–21",
            "started_at": 1789999000, "ended_at": 1789999400, "duration_s": 400, "per_product_s": 20,
            "cost_usd": 0.06, "explain": "", "current": False}


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_run_page_refreshes_past_the_caches_and_offers_the_review_when_a_run_finishes():
    """Ported from the batch page's end-of-run test: the review grid moved to /catalog?mode=bulk, so at the end of a
    run the Run page shows the run's summary with a review button and reads the sheet again past every cache."""
    lives = [_snap(_status("running", is_running=True)), _snap(_status("running", is_running=True)),
             _snap(_status("review"), _run_summary(proposed=3, none=0, error=1, approved=1, waiting=15, processed=5))]
    script = _page_js(RUN_JS) + CONTROLLER_HARNESS + f"""
lives = {json.dumps(lives, ensure_ascii=False)};
const ctl = LaqtaRunPage.createController(deps);
(async () => {{
    await ctl.poll(); await tick();
    const v = last(views.live);
    const running = {{ percent: v.progress.percentText, counts: v.progress.countsText, fresh: freshPlans(),
                       finished: v.finished, state: v.state }};
    await ctl.poll(); await tick();
    await ctl.poll(); await tick(); await tick();
    const end = last(views.live);
    console.log(JSON.stringify({{ running, fresh: freshPlans(), state: end.state, finished: end.finished,
                                  progress: end.progress, toasts }}));
}})();
"""
    out = _node(script)
    assert out["running"]["percent"] == "25%"                                  # per-run: 5 of 20
    assert out["running"]["counts"].startswith("5 من 20 في هذا التشغيل")
    assert "300" not in out["running"]["counts"] and "7" not in out["running"]["counts"]   # never the queue's numbers
    assert out["running"]["fresh"] == 0 and out["running"]["finished"] is None
    assert out["fresh"] == 1                                                   # never the server's caches
    assert out["progress"] is None and out["finished"]["reviewVisible"] is True
    assert out["finished"]["reviewCount"] == 3 and out["finished"]["reviewLabel"] == "راجع النتائج (3)"
    assert out["state"] == "stopped"                     # 5 of 20 searched: it did not finish, and says so
    assert "وقف قبل ما يخلص" in out["finished"]["title"]
    # the end-of-run toast agrees with the card: a run that stopped at 5 of 20 did not finish
    assert out["toasts"][-1][0] == "وقف التشغيل قبل ما يخلص: 3 منتجات بانتظار مراجعتك."


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_run_ending_on_a_provider_outage_reloads_past_the_caches_and_shows_the_banner():
    """Review fix, ported: a run that stops on a provider outage ends in 'provider_down' (phase error). The page
    must still read the sheet again past the caches and offer the rows that reached review during the run."""
    lives = [_snap(_status("running", is_running=True, status="pre_caching")),
             _snap(_status("error", status="provider_down", ready_for_review=4,
                           alert="محركات البحث غير متاحة، فتوقف العامل وبقيت الصفوف المتبقية في الانتظار."),
                   _run_summary(total=10, processed=6, proposed=4, requeued=4))]
    script = _page_js(RUN_JS) + CONTROLLER_HARNESS + f"""
lives = {json.dumps(lives, ensure_ascii=False)};
const ctl = LaqtaRunPage.createController(deps);
(async () => {{
    await ctl.poll(); await tick();
    await ctl.poll(); await tick(); await tick();
    const v = last(views.live);
    console.log(JSON.stringify({{ fresh: freshPlans(), banner: v.banner, finished: v.finished, state: v.state }}));
}})();
"""
    out = _node(script)
    assert out["fresh"] == 1
    assert out["banner"]["visible"] is True and out["banner"]["variant"] == "danger"
    assert out["banner"]["text"].startswith("محركات البحث غير متاحة")
    assert out["finished"]["reviewVisible"] is True and out["finished"]["reviewCount"] == 4
    assert out["finished"]["title"] == "آخر تشغيل وقف بعطل"


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_run_page_shows_the_enqueue_phase_and_the_red_banner():
    lives = [_snap(_status("starting", is_running=True, run={"run_id": None, "total": 0, "processed": 0})),
             _snap(_status("error", status="error", ready_for_review=0,
                           alert="فشل تجهيز التشغيل ولم يبدأ العامل: لم يُعثر على الشيت"))]
    script = _page_js(RUN_JS) + CONTROLLER_HARNESS + f"""
lives = {json.dumps(lives, ensure_ascii=False)};
const ctl = LaqtaRunPage.createController(deps);
(async () => {{
    await ctl.poll();
    const s = last(views.live);
    const starting = {{ text: s.progress.phaseText, percent: s.progress.percentText, counts: s.progress.countsText,
                        pause: s.progress.pause.disabled, banner: s.banner.visible, big: s.progress.done }};
    await ctl.poll(); await tick();
    const e = last(views.live);
    console.log(JSON.stringify({{ starting, banner: e.banner, chip: e.chip, finished: e.finished, state: e.state,
                                  startBlocked: [s.startBlocked, e.startBlocked] }}));
}})();
"""
    out = _node(script)
    assert out["starting"] == {"text": "text:starting", "percent": "—", "counts": "", "pause": True,
                               "banner": False, "big": None}
    assert out["banner"]["visible"] is True and "لم يُعثر على الشيت" in out["banner"]["text"]
    assert "عطل" in out["chip"]["label"] and "خامل" not in out["chip"]["label"]
    assert out["state"] == "error" and out["finished"] is None   # an error that left nothing to review offers none
    assert out["startBlocked"] == [True, False]


def test_old_review_tab_link_opens_the_bulk_review():
    """The home page used to link to /batch-automation?tab=review; the review grid now lives at /catalog?mode=bulk,
    and the old link redirects there. The home page links to the review list directly."""
    run = read(DASH / "app" / "Http" / "Controllers" / "RunController.php")
    page = method(run, "page")
    assert "$request->query('tab') === 'review'" in page and "redirect('/catalog?mode=bulk')" in page
    assert page.index("redirect('/catalog?mode=bulk')") < page.index("self::snapshot()")
    assert "?tab=review" not in read(INDEX)


def test_sidebar_run_card_speaks_the_run_states():
    """The Laqta sidebar card (layouts/laqta) reads the same phase, alert and stuck fields as the Run page.
    Its behaviour is run under node in tests/test_laqta_ui.py::test_run_card_follows_the_run_pages_phase_alert_and_stuck."""
    layout = read(LAYOUT)
    for field in ("'phase'", "'stuck'", "'alert'"):
        assert field in layout, field
    assert "جاهز وخامل" not in layout and "خامل" not in layout


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_home_page_banner_progress_and_review_link():
    lives = [_snap(_status("starting", is_running=True)), _snap(_status("running", is_running=True)),
             _snap(_status("review", alert="Gemini لا يستجيب"))]
    script = _page_js(HOME_JS) + CONTROLLER_HARNESS + f"""
lives = {json.dumps(lives, ensure_ascii=False)};
const ctl = LaqtaHomePage.createController(deps);
(async () => {{
    await ctl.poll();
    const s = last(views.live).lastRun;
    const starting = {{ percent: s.percentText, counts: s.countsText, text: s.phaseText }};
    await ctl.poll();
    const r = last(views.live).lastRun;
    const running = {{ percent: r.percentText, counts: r.countsText }};
    await ctl.poll(); await tick(); await tick();
    console.log(JSON.stringify({{ starting, running, banner: last(views.banner), waiting: last(views.live).waiting,
        overviews: fetchLog.filter(f => f.url.startsWith('/api/overview')).map(f => f.url) }}));
}})();
"""
    out = _node(script)
    assert out["starting"] == {"percent": "—", "counts": "", "text": "text:starting"}
    assert out["running"]["percent"] == "25%" and out["running"]["counts"].startswith("5 من 20 في هذا التشغيل")
    assert out["banner"]["visible"] is True and "Gemini لا يستجيب" in out["banner"]["text"]
    assert out["waiting"] == 3                                  # the sidebar badge's ready_for_review
    assert out["overviews"] == ["/api/overview?refresh=1"]      # the run ended: read again past the caches
    index = read(INDEX)
    link = index[index.index('data-home="review-link"') - 200:index.index('data-home="review-link"')]
    assert "route('dashboard.catalog')" in link                # the review list, not the run page
    assert "?tab=review" not in index


def test_confirm_texts_say_exactly_what_happens():
    run_js, batch, index = read(JS / "run.js"), read(BATCH), read(INDEX)
    stop = run_js[run_js.index("var STOP_CONFIRM_TEXT"):]
    stop = stop[:stop.index(";\n")]
    assert "تعود الصفوف التي كانت قيد المعالجة إلى الانتظار" in stop
    assert "لا يُحذف أي صف" in stop
    for text in (run_js, batch, index, read(JS / "home.js")):
        assert "إنهاء قسري" not in text
    reset = run_js[run_js.index("var RESET_CONFIRM_TEXT"):]
    reset = reset[:reset.index(";\n")]
    assert "لا يُحذف أي منتج جاهز للمراجعة أو معتمد أو فاشل، ولا أي مرشح أو قرار مراجعة" in reset
    assert "إصلاح تشغيل عالق" in batch and "تصفير وإعادة تعيين الحالة" not in batch + run_js
    # the page says the same next to the button, and the stop button exists only on the Run page
    assert "الإيقاف ما بيحذف شي" in batch
    assert "/api/stop-batch" not in index + read(JS / "home.js")
