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
import sys
import tempfile
import time
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
LAYOUT_JS = DASH / "public" / "js" / "layout.js"       # the layout's script (sidebar card, review badge)
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
    # the process is still stopped in PHP (it needs the OS), for both buttons: a stop request first, a kill only
    # after the wait (review fix C5)
    assert "$this->stopWorker($process)" in stop and "$this->stopWorker($process)" in reset
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



BATCH_STATUS_HARNESS = r"""<?php
namespace App\Http\Controllers { class Controller {} }
namespace App\Services {
    class PythonBridge {
        public static function run($action, $params = []) {
            return ['status' => 'success', 'state' => getenv('WORKER_STATE'), 'pid' => 4242, 'verified' => true];
        }
        public static function pythonPath() { return 'python'; }
    }
    class QueueStats {
        public static function counters() {
            return ['by_status' => ['ready_for_review' => 0, 'processing' => 0, 'pending' => 3], 'by_failure_code' => []];
        }
        public static function run($runId) { return null; }
        public static function runTotals($a, $b) {
            return ['total' => 0, 'processed' => 0, 'failed' => 0, 'ready_for_review' => 0, 'completed' => 0];
        }
        public static function runPhase(...$a) { return 'idle'; }
        public static function phaseText(...$a) { return ''; }
        public static function alertText(...$a) { return ''; }
        public static function stuckReason(...$a) { return ''; }
        public static function retryWaitS() { return null; }
    }
}
namespace {
    class DB {
        public static $updates = [];
        public static function select($sql, $bindings = []) {
            return [(object) ['status' => 'pre_caching', 'pause_requested' => 1, 'stop_requested' => 0, 'notice' => '',
                'current_product_name' => 'P1', 'updated_at' => gmdate('Y-m-d H:i:s', time() - 600), 'lq_age_s' => 600,
                'run_id' => null, 'total_items' => 0, 'processed_items' => 0, 'failed_count' => 0, 'success_count' => 0]];
        }
        public static function update($sql, $bindings = []) { self::$updates[] = $sql; return 1; }
    }
    class FakeResponse {
        public $data;
        public function __construct($d) { $this->data = $d; }
        public function header($k, $v) { return $this; }
    }
    class FakeFactory { public function json($d, $s = 200) { return new FakeResponse($d); } }
    function response() { return new FakeFactory(); }
    function base_path($p = '') { return getenv('HARNESS_ROOT') . '/dashboard' . ($p !== '' ? '/' . $p : ''); }
    require getenv('API_CONTROLLER');
    $r = (new App\Http\Controllers\ApiController())->batchStatus();
    echo json_encode(['pause_requested' => $r->data['pause_requested'], 'status' => $r->data['status'],
                      'updates' => DB::$updates]);
}
"""


@pytest.mark.skipif(PHP is None, reason="php is not installed")
@pytest.mark.parametrize("lock, worker_state", [(None, "none"), ('{"pid": 4242}', "none"), ('{"pid": 4242}', "running")])
def test_batch_status_never_clears_a_pause_request(tmp_path, lock, worker_state):
    """Review fix C1: when the dashboard judged a live paused worker stopped (its 24-hour rule), the status poll's
    self-healing UPDATE also set pause_requested = 0, and the paused worker silently resumed. The status poll may still
    settle a stale 'pre_caching' status, but the pause request stays until the next run clears it."""
    root = tmp_path / "root"
    (root / "dashboard").mkdir(parents=True)
    (root / "temp").mkdir()
    if lock is not None:
        (root / "temp" / "pipeline.lock").write_text(lock, encoding="utf-8")
    script = tmp_path / "status.php"
    script.write_text(BATCH_STATUS_HARNESS, encoding="utf-8")
    env = dict(os.environ, HARNESS_ROOT=str(root), API_CONTROLLER=str(CONTROLLER), WORKER_STATE=worker_state)
    result = subprocess.run([PHP, str(script)], capture_output=True, text=True, timeout=60, encoding="utf-8", env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    out = json.loads(result.stdout)
    assert out["pause_requested"] == 1
    assert all("pause_requested" not in sql for sql in out["updates"]), out["updates"]
    if worker_state == "none":
        assert out["status"] == "idle" and len(out["updates"]) == 1        # the stale status itself is still settled
    else:
        assert out["updates"] == [] and out["status"] == "pre_caching"


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
    unconfirmed = subprocess.Popen(["sleep", "30"])
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
    'kill' => $terminate->invoke($c, ['state' => 'running', 'pid' => '{alive.pid}', 'verified' => true]),
    'unconfirmed' => $terminate->invoke($c, ['state' => 'running', 'pid' => '{unconfirmed.pid}', 'verified' => false]),
]);
}}
""")
        # review fix C9: a PID whose identity Python did not confirm is never killed (it may be any program now)
        assert unconfirmed.poll() is None
    finally:
        for proc in (alive, unconfirmed):
            proc.kill()
            proc.wait()
        os.waitpid(zombie, 0)
    assert out == {"zombie": False, "live": True, "kill": "killed", "unconfirmed": "running"}

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
    result = subprocess.run([NODE, "-"], input=script, capture_output=True, text=True, timeout=60, encoding="utf-8")
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
    layout = read(LAYOUT) + read(LAYOUT_JS)
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
    # plain Levantine: what keeps going, what comes back, and that nothing is deleted
    assert "الصفوف اللي كانت عم تتعالج بترجع تستنى" in stop
    assert "ما في ولا صف بينمسح" in stop
    for text in (run_js, batch, index, read(JS / "home.js")):
        assert "إنهاء قسري" not in text
    reset = run_js[run_js.index("var RESET_CONFIRM_TEXT"):]
    reset = reset[:reset.index(";\n")]
    assert "ما في ولا منتج جاهز للمراجعة أو معتمد أو فاشل بينمسح، ولا أي اقتراح أو قرار مراجعة" in reset
    assert "إصلاح تشغيل عالق" in batch and "تصفير وإعادة تعيين الحالة" not in batch + run_js
    # the page says the same next to the button, and the stop button exists only on the Run page
    assert "الإيقاف ما بيحذف شي" in batch
    assert "/api/stop-batch" not in index + read(JS / "home.js")


# ---------------------------------------------------------------------------
# Stop / «fix stuck run» stop a live worker gracefully (review fix C5)
# ---------------------------------------------------------------------------

# A stand-in worker: holds temp/pipeline.lock with its own PID and, like main.run_worker_mode, leaves (removing its
# lock) once the stop request reaches it, unless IGNORE_STOP is set.
FAKE_WORKER = r"""
import json, os, sys, time
root, ignore = sys.argv[1], sys.argv[2] == "1"
lock = os.path.join(root, "temp", "pipeline.lock")
with open(lock, "w") as fh:
    json.dump({"pid": os.getpid(), "role": "worker"}, fh)
while True:
    if not ignore and os.path.exists(os.path.join(root, "temp", "stop_requested")):
        os.remove(lock)
        sys.exit(0)
    time.sleep(0.05)
"""

STOP_HARNESS = r"""<?php
namespace App\Http\Controllers {
    class Controller {}
    class ProductController { public static function forgetProductCaches() {} }
}
namespace App\Services {
    class PythonBridge {
        public static $calls = [];
        public static function run($action, $params = []) {
            $root = getenv('HARNESS_ROOT');
            if ($action === 'lock_state') {
                return ['status' => 'success', 'state' => 'running', 'pid' => (int) getenv('WORKER_PID'),
                        'verified' => getenv('VERIFIED') === '1'];
            }
            // what run_control saw: was the lock still there (the killed run's report is built from it)?
            self::$calls[] = [$params['op'], $params['worker'], file_exists($root . '/temp/pipeline.lock')];
            if ($params['op'] === 'stop' && $params['worker'] === 'running') {
                touch($root . '/temp/stop_requested');
            }
            return ['status' => 'success', 'message' => 'ok'];
        }
        public static function pythonPath() { return 'python'; }
    }
    class QueueStats {}
}
namespace {
    class FakeResponse { public $data; public function __construct($d) { $this->data = $d; } }
    class FakeFactory { public function json($d, $s = 200) { return new FakeResponse($d); } }
    function response() { return new FakeFactory(); }
    function base_path($p = '') { return getenv('HARNESS_ROOT') . '/dashboard' . ($p !== '' ? '/' . $p : ''); }
    require getenv('API_CONTROLLER');
    App\Http\Controllers\ApiController::$stopWaitSeconds = (int) getenv('STOP_WAIT');
    $api = new App\Http\Controllers\ApiController();
    $started = microtime(true);
    $r = getenv('BUTTON') === 'reset' ? $api->resetBatch() : $api->stopBatch();
    echo json_encode(['worker' => $r->data['worker'] ?? null, 'calls' => App\Services\PythonBridge::$calls,
                      'lock_left' => file_exists(getenv('HARNESS_ROOT') . '/temp/pipeline.lock'),
                      'seconds' => microtime(true) - $started]);
}
"""


def _press(tmp_path, button="stop", ignore_stop=False, verified=True, wait=5):
    root = tmp_path / "root"
    (root / "dashboard").mkdir(parents=True)
    (root / "temp").mkdir()
    worker_py = tmp_path / "fake_worker.py"
    worker_py.write_text(FAKE_WORKER, encoding="utf-8")
    # On Windows a venv's python.exe is a launcher that runs the real interpreter as a child process, so the Popen PID
    # would not be the PID the worker writes to its lock (the one production reads and stops): start the stand-in
    # worker (stdlib only) with the base interpreter there, so both are the same process, as on Linux
    python = sys.executable
    if sys.platform == "win32":
        python = getattr(sys, "_base_executable", None) or sys.executable
    worker = subprocess.Popen([python, str(worker_py), str(root), "1" if ignore_stop else "0"])
    try:
        lock = root / "temp" / "pipeline.lock"
        for _ in range(200):
            if lock.exists() and lock.read_text():
                break
            time.sleep(0.02)
        script = tmp_path / "stop.php"
        script.write_text(STOP_HARNESS, encoding="utf-8")
        env = dict(os.environ, HARNESS_ROOT=str(root), API_CONTROLLER=str(CONTROLLER), WORKER_PID=str(worker.pid),
                   VERIFIED="1" if verified else "0", STOP_WAIT=str(wait), BUTTON=button)
        result = subprocess.run([PHP, str(script)], capture_output=True, text=True, timeout=120, encoding="utf-8",
                                env=env)
        assert result.returncode == 0, result.stdout + result.stderr
        out = json.loads(result.stdout)
        time.sleep(0.1)
        out["exit"] = worker.poll()
        return out
    finally:
        if worker.poll() is None:
            worker.kill()
        worker.wait()


@pytest.mark.skipif(PHP is None or not hasattr(os, "kill"), reason="needs the PHP CLI")
@pytest.mark.parametrize("button, final_op", [("stop", "stop"), ("reset", "reset")])
def test_stop_lets_a_live_worker_finish_and_write_its_own_report(tmp_path, button, final_op):
    """Review fix C5: Stop and «fix stuck run» sent taskkill /F / kill -9 at once, to the nightly runner too: the
    worker's finally and run_nightly never ran (no report, no run_history row, cost lost), and Task Scheduler recorded
    1. A live worker now gets the stop request and time to finish its products; it leaves by itself (exit 0)."""
    out = _press(tmp_path, button=button)
    assert out["worker"] == "exited" and out["exit"] == 0, out           # not killed
    assert out["calls"] == [["stop", "running", True], [final_op, "exited", False]]
    assert out["seconds"] < 5


@pytest.mark.skipif(PHP is None or not hasattr(os, "kill"), reason="needs the PHP CLI")
def test_a_worker_that_ignores_the_stop_is_killed_after_the_wait_and_reported(tmp_path):
    out = _press(tmp_path, ignore_stop=True, wait=1)
    assert out["worker"] == "killed" and out["exit"] is not None and out["exit"] != 0
    # run_control (which writes the 'stopped' report from the lock) runs before the lock is removed
    assert out["calls"] == [["stop", "running", True], ["stop", "killed", True]]
    assert out["lock_left"] is False and out["seconds"] >= 1


@pytest.mark.skipif(PHP is None or not hasattr(os, "kill"), reason="needs the PHP CLI")
@pytest.mark.parametrize("button, final_op", [("stop", "stop"), ("reset", "reset")])
def test_an_unconfirmed_worker_is_never_killed_and_keeps_its_lock(tmp_path, button, final_op):
    out = _press(tmp_path, button=button, ignore_stop=True, verified=False, wait=1)
    assert out["worker"] == "running" and out["exit"] is None
    assert out["calls"] == [["stop", "running", True], [final_op, "running", True]]
    assert out["lock_left"] is True
