"""Dashboard side of work package R (running the automation never destroys work and says what is happening).

- ApiController: stop / reset never delete queue rows or review work; they go through cli_bridge run_control.
- QueueStats: the run phase, its Arabic texts and the red banner (pure functions, run under the PHP CLI).
- The views' scripts run under node against a small stub DOM: end-of-run refresh and tab switch, the red
  banner and sidebar state, bulk approve locking, and review shortcuts only on the review tab.
PHP or node checks are skipped when the binary is not installed.
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
from blade_scripts import inline_scripts

ROOT = Path(__file__).resolve().parents[1]
DASH = ROOT / "dashboard"
VIEWS = DASH / "resources" / "views"
CONTROLLER = DASH / "app" / "Http" / "Controllers" / "ApiController.php"
QUEUE_STATS = DASH / "app" / "Services" / "QueueStats.php"
BATCH = VIEWS / "dashboard" / "batch_automation.blade.php"
INDEX = VIEWS / "dashboard" / "index.blade.php"
LAYOUT = VIEWS / "layouts" / "layout.blade.php"
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
    assert out["error_without_notice"].startswith("توقف التشغيل بسبب خطأ غير معروف")
    assert out["idle"] == ""


# ---------------------------------------------------------------------------
# Views under node with a stub DOM
# ---------------------------------------------------------------------------

STUB_DOM = r"""
const byId = {};
const docListeners = {};
const fetchLog = [];
const alerts = [];
let fetchHandler = () => ({});
function matches(node, sel) {
    let s = sel;
    let needChecked = false;
    if (s.endsWith(':checked')) { needChecked = true; s = s.slice(0, -8); }
    if (needChecked && !node.checked) return false;
    let m = s.match(/^([a-z]*)\[name="([^"]+)"\]$/);
    if (m) return (!m[1] || node.tagName === m[1].toUpperCase()) && node.attributes.name === m[2];
    if (s.startsWith('.')) {
        const cls = s.slice(1);
        return String(node.className || '').split(/\s+/).includes(cls) || node.classList._s.has(cls);
    }
    return node.tagName === s.toUpperCase();
}
function walk(node, out) { node.children.forEach(c => { out.push(c); walk(c, out); }); return out; }
function allNodes() { const out = []; Object.values(byId).forEach(r => { out.push(r); walk(r, out); }); return out; }
function makeEl(tag, id) {
    const node = {
        tagName: String(tag || 'div').toUpperCase(), id: id || '', children: [], parentNode: null,
        style: {}, dataset: {}, attributes: {}, listeners: {}, disabled: false, checked: false, value: '',
        className: '', innerHTML: '', title: '', _text: '', scrollTop: 0, scrollHeight: 0,
        classList: { _s: new Set(), add(c) { this._s.add(c); }, remove(c) { this._s.delete(c); },
                     contains(c) { return this._s.has(c); } },
        get textContent() { return this._text + this.children.map(c => c.textContent).join(''); },
        set textContent(v) { this._text = String(v); this.children = []; },
        get innerText() { return this.textContent; },
        set innerText(v) { this.textContent = v; },
        appendChild(c) { c.parentNode = this; this.children.push(c); return c; },
        after(c) { if (this.parentNode) { const k = this.parentNode.children; k.splice(k.indexOf(this) + 1, 0, c); c.parentNode = this.parentNode; } },
        remove() { if (this.parentNode) { const k = this.parentNode.children; k.splice(k.indexOf(this), 1); this.parentNode = null; } },
        setAttribute(k, v) { this.attributes[k] = String(v); if (k === 'id') this.id = String(v); },
        getAttribute(k) { return this.attributes[k]; },
        addEventListener(t, fn) { (this.listeners[t] = this.listeners[t] || []).push(fn); },
        click() { (this.listeners.click || []).forEach(fn => fn({ stopPropagation() {}, preventDefault() {} })); },
        querySelectorAll(sel) { return walk(this, []).filter(n => matches(n, sel)); },
        querySelector(sel) { return this.querySelectorAll(sel)[0] || null; },
        closest(sel) { let n = this; while (n) { if (matches(n, sel)) return n; n = n.parentNode; } return null; },
        scrollIntoView() {}, focus() {}
    };
    return node;
}
globalThis.document = {
    getElementById(id) {
        const found = allNodes().find(n => n.id === id);
        if (found) return found;
        byId[id] = makeEl('div', id);
        return byId[id];
    },
    createElement: (t) => makeEl(t),
    createTextNode: (t) => { const n = makeEl('#text'); n._text = String(t); return n; },
    querySelector(sel) {
        if (sel.startsWith('meta')) return { content: 'token', getAttribute: () => 'token' };
        return this.querySelectorAll(sel)[0] || null;
    },
    querySelectorAll(sel) { return allNodes().filter(n => matches(n, sel)); },
    addEventListener(t, fn) { (docListeners[t] = docListeners[t] || []).push(fn); },
    activeElement: { tagName: 'BODY' },
    body: makeEl('body')
};
globalThis.window = { location: { search: '', href: '', reload() { fetchLog.push('RELOAD'); } },
                      addEventListener() {} };
globalThis.location = window.location;
globalThis.confirm = () => true;
globalThis.alert = (m) => alerts.push(String(m));
globalThis.setInterval = () => 0;
const store = {};
globalThis.localStorage = { getItem: k => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); },
                            removeItem: k => { delete store[k]; } };
globalThis.fetch = (url, opts) => {
    fetchLog.push(url);
    return Promise.resolve(fetchHandler(url, opts)).then(body => ({ status: 200, json: async () => body }));
};
const tick = () => new Promise(r => setTimeout(r, 0));
function setDisplay(ids) { Object.entries(ids).forEach(([id, d]) => { document.getElementById(id).style.display = d; }); }
"""


def _page_script(path: Path, index: int = -1) -> str:
    blocks = inline_scripts(read(path))
    return re.sub(r"\{\{.*?\}\}", "''", blocks[index])


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


BATCH_SETUP = """
setDisplay({ tabContentAutomation: 'block', tabContentCuration: 'none', batchCurationWorkspace: 'none',
             batchRejectModal: 'none' });
"""


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_batch_page_refreshes_the_grid_and_opens_the_review_tab_when_a_run_finishes():
    statuses = [_status("running", is_running=True), _status("running", is_running=True), _status("review")]
    script = STUB_DOM + _page_script(BATCH) + BATCH_SETUP + f"""
const statuses = {json.dumps(statuses, ensure_ascii=False)};
let i = 0;
fetchHandler = (url) => url === '/api/batch-status' ? statuses[Math.min(i++, statuses.length - 1)] : {{ products: [] }};
(async () => {{
    // the workspace is already visible (products loaded): the old code never switched in that case
    document.getElementById('batchCurationWorkspace').style.display = 'block';
    await pollBatchStatus(); await tick();
    const running = {{
        curation: document.getElementById('tabContentCuration').style.display,
        percent: document.getElementById('batchProgressPercent').innerText,
        counts: document.getElementById('batchProgressCounts').innerText,
        failedCard: document.getElementById('statFailedCount').innerText,
        productsFetched: fetchLog.filter(u => u.startsWith('/api/products-json')).length
    }};
    await pollBatchStatus(); await tick();
    await pollBatchStatus(); await tick();
    console.log(JSON.stringify({{ running,
        curation: document.getElementById('tabContentCuration').style.display,
        automation: document.getElementById('tabContentAutomation').style.display,
        productsFetched: fetchLog.filter(u => u.startsWith('/api/products-json')).length,
        freshFetched: fetchLog.filter(u => u === '/api/products-json?refresh=true').length,
        idleText: document.getElementById('batchIdleState').textContent }}));
}})();
"""
    out = _node(script)
    assert out["running"]["curation"] == "none" and out["running"]["productsFetched"] == 0
    assert out["running"]["percent"] == "25%"                                 # per-run: 5 of 20
    assert out["running"]["counts"].startswith("5 من 20 في هذا التشغيل")
    assert out["running"]["failedCard"] == "7"                               # the global card keeps queue numbers
    assert out["curation"] == "block" and out["automation"] == "none"
    assert out["productsFetched"] == 1 and out["freshFetched"] == 1       # never the server's products cache
    assert "text:review" in out["idleText"]


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_run_ending_on_a_provider_outage_reloads_the_grid_past_the_products_cache():
    """Review fix. ProductController::getProductsJson skips its one-hour cache only while the state is pre_caching /
    running / curation_pending. A run that stops on a provider outage ends in 'provider_down' (phase error), so a
    plain /api/products-json returned the list cached when the page was opened, without the rows that reached
    review during the run, while the page switched to the review tab."""
    statuses = [_status("running", is_running=True, status="pre_caching"),
                _status("error", status="provider_down", ready_for_review=4,
                        alert="محركات البحث غير متاحة، فتوقف العامل وبقيت الصفوف المتبقية في الانتظار.")]
    script = STUB_DOM + _page_script(BATCH) + BATCH_SETUP + f"""
const statuses = {json.dumps(statuses, ensure_ascii=False)};
let i = 0;
fetchHandler = (url) => url === '/api/batch-status' ? statuses[Math.min(i++, statuses.length - 1)] : {{ products: [] }};
(async () => {{
    await pollBatchStatus(); await tick();
    await pollBatchStatus(); await tick();
    console.log(JSON.stringify({{
        products: fetchLog.filter(u => u.startsWith('/api/products-json')),
        curation: document.getElementById('tabContentCuration').style.display,
        banner: document.getElementById('batchStateAlert').style.display }}));
}})();
"""
    out = _node(script)
    assert out["products"] == ["/api/products-json?refresh=true"]
    assert out["curation"] == "block" and out["banner"] == "block"


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_batch_page_shows_the_enqueue_phase_and_the_red_banner():
    statuses = [_status("starting", is_running=True, run={"run_id": None, "total": 0, "processed": 0}),
                _status("error", status="error", ready_for_review=0,
                        alert="فشل تجهيز التشغيل ولم يبدأ العامل: لم يُعثر على الشيت")]
    script = STUB_DOM + _page_script(BATCH) + BATCH_SETUP + f"""
const statuses = {json.dumps(statuses, ensure_ascii=False)};
let i = 0;
fetchHandler = (url) => url === '/api/batch-status' ? statuses[Math.min(i++, statuses.length - 1)] : {{ products: [] }};
(async () => {{
    await pollBatchStatus();
    const starting = {{
        text: document.getElementById('batchProgressText').textContent,
        percent: document.getElementById('batchProgressPercent').innerText,
        counts: document.getElementById('batchProgressCounts').innerText,
        pause: document.getElementById('pauseResumeBatchBtn').disabled,
        banner: document.getElementById('batchStateAlert').style.display
    }};
    await pollBatchStatus(); await tick();
    console.log(JSON.stringify({{ starting,
        banner: document.getElementById('batchStateAlert').style.display,
        bannerText: document.getElementById('batchStateAlert').textContent,
        label: document.getElementById('statProgressLabel').innerHTML,
        curation: document.getElementById('tabContentCuration').style.display }}));
}})();
"""
    out = _node(script)
    assert out["starting"]["text"] == "text:starting" and out["starting"]["percent"] == "—"
    assert out["starting"]["counts"] == "" and out["starting"]["pause"] is True
    assert out["starting"]["banner"] == "none"
    assert out["banner"] == "block" and "لم يُعثر على الشيت" in out["bannerText"]
    assert "خطأ" in out["label"] and "خامل" not in out["label"]
    assert out["curation"] == "none"         # an error that left nothing to review keeps the run tab and its log


def _products():
    def cand(url, status):
        return {"image_url": url, "status": status, "is_selected": 1 if status == "preselected" else 0}
    return [{"row_number": r, "product_name": f"P{r}", "brand": "B", "sku_key": f"k{r}", "needs_review": True,
             "curation_candidates": [cand(f"https://x.ae/{r}a.jpg", "preselected"), cand(f"https://x.ae/{r}b.jpg", "eligible")]}
            for r in (11, 12)]


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_bulk_approve_locks_the_review_controls_and_never_publishes_twice():
    script = STUB_DOM + _page_script(BATCH) + BATCH_SETUP + f"""
setDisplay({{ tabContentAutomation: 'none', tabContentCuration: 'block', batchCurationWorkspace: 'block' }});
document.getElementById('curationPageSize').value = '25';
const products = {json.dumps(_products())};
const pending = [];
fetchHandler = (url, opts) => {{
    if (url === '/api/select_image') return new Promise(r => pending.push({{ body: JSON.parse(opts.body), r }}));
    if (url === '/api/products-json') return {{ products: JSON.parse(JSON.stringify(products)) }};
    return {{}};
}};
(async () => {{
    await fetchCurationProducts(); await tick();
    const boxes = () => document.querySelectorAll('.batch-select-checkbox');
    const checkedBefore = boxes().filter(b => b.checked).length;
    const run = submitBatchApproval();
    await tick();
    const during = {{
        busy: reviewBusy, requests: pending.length,
        approveDisabled: document.getElementById('batchApproveBtn').disabled,
        rejectDisabled: document.getElementById('batchRejectBtn').disabled,
        boxesDisabled: boxes().every(b => b.disabled),
        percent: document.getElementById('batchCurationProgressPercent').innerText
    }};
    // everything that could approve, reject or change the pick is ignored while the bulk run works
    submitBatchApproval();
    const thumb = document.querySelector('.curation-thumb-card');
    selectCurationThumb(thumb, 11, 'https://x.ae/11b.jpg');
    docListeners.keydown.forEach(fn => fn({{ key: 'Enter', preventDefault() {{}} }}));
    openBatchRejectModal({{ mode: 'bulk', count: 2 }});
    await tick();
    const ignored = {{ requests: pending.length, modal: document.getElementById('batchRejectModal').style.display,
                       pick: products[0].curation_candidates[0].image_url }};
    pending[0].r({{ status: 'success' }}); await tick(); await tick();
    const afterFirst = {{ percent: document.getElementById('batchCurationProgressPercent').innerText,
                          requests: pending.length }};
    pending[1].r({{ status: 'success' }});
    await run; await tick(); await tick();
    const after = {{ busy: reviewBusy, approved: Array.from(approvedRowKeys),
                     boxes: boxes().map(b => ({{ checked: b.checked, disabled: b.disabled }})),
                     approveDisabled: document.getElementById('batchApproveBtn').disabled }};
    // the sheet still says needs_review (background not removed): the rows come back but cannot be approved again
    selectAllBatch(true);
    const before2 = pending.length;
    await submitBatchApproval();
    console.log(JSON.stringify({{ checkedBefore, during, ignored, afterFirst, after,
                                  secondRequests: pending.length - before2,
                                  bodies: pending.map(p => p.body.row_number), lastAlert: alerts[alerts.length - 1] }}));
}})();
"""
    out = _node(script)
    assert out["checkedBefore"] == 2
    assert out["during"] == {"busy": True, "requests": 1, "approveDisabled": True, "rejectDisabled": True,
                             "boxesDisabled": True, "percent": "0%"}          # no progress before a request ends
    assert out["ignored"]["requests"] == 1 and out["ignored"]["modal"] == "none"
    assert out["afterFirst"] == {"percent": "50%", "requests": 2}
    assert out["after"]["busy"] is False and out["after"]["approveDisabled"] is False
    assert sorted(out["after"]["approved"]) == ["11|k11", "12|k12"]
    assert all(b == {"checked": False, "disabled": True} for b in out["after"]["boxes"])
    assert out["secondRequests"] == 0 and out["bodies"] == [11, 12]
    assert out["lastAlert"].startswith("❌ يرجى تحديد منتج واحد على الأقل")


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_review_shortcuts_only_work_on_the_review_tab():
    script = STUB_DOM + _page_script(BATCH) + BATCH_SETUP + f"""
setDisplay({{ batchCurationWorkspace: 'block' }});
document.getElementById('curationPageSize').value = '25';
const products = {json.dumps(_products())};
let approvals = 0;
fetchHandler = (url) => {{
    if (url === '/api/select_image') {{ approvals++; return {{ status: 'success' }}; }}
    if (url === '/api/products-json') return {{ products: JSON.parse(JSON.stringify(products)) }};
    return {{}};
}};
const press = (key) => docListeners.keydown.forEach(fn => fn({{ key, preventDefault() {{}} }}));
(async () => {{
    await fetchCurationProducts(); await tick();
    switchTab('automation');
    press('ArrowDown'); press('2'); press('Enter'); await tick(); await tick();
    const onRunTab = {{ focused: focusedCardIndex, approvals,
                        pick: currentProducts[0].curation_candidates.find(c => c.is_selected === 1).image_url }};
    switchTab('curation');
    press('ArrowDown'); press('2'); await tick();
    const onReviewTab = {{ focused: focusedCardIndex,
                           pick: currentProducts[0].curation_candidates.find(c => c.is_selected === 1).image_url }};
    console.log(JSON.stringify({{ onRunTab, onReviewTab }}));
}})();
"""
    out = _node(script)
    assert out["onRunTab"] == {"focused": -1, "approvals": 0, "pick": "https://x.ae/11a.jpg"}
    assert out["onReviewTab"] == {"focused": 0, "pick": "https://x.ae/11b.jpg"}


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_batch_page_opens_the_review_tab_from_the_home_link():
    text = read(BATCH)
    loaded = text[text.index('document.addEventListener("DOMContentLoaded"'):]
    assert "new URLSearchParams(window.location.search).get('tab') === 'review'" in loaded
    assert loaded.index("switchTab('curation')") < loaded.index("pollBatchStatus();")


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_sidebar_widget_states():
    script = STUB_DOM + _page_script(LAYOUT, 0) + f"""
const cases = {json.dumps([_status("error", alert="x"), _status("review", ready_for_review=4), _status("idle"),
                           _status("starting", is_running=True), _status("running", is_running=True)],
                          ensure_ascii=False)};
(async () => {{
    const out = [];
    for (const c of cases) {{
        fetchHandler = () => c;
        await updateSidebarStatus();
        out.push({{ label: document.getElementById('stateLabel').innerText,
                    color: document.getElementById('stateDot').style.color,
                    progress: document.getElementById('stateProgressContainer').style.display,
                    text: document.getElementById('stateProgressText').innerText }});
    }}
    console.log(JSON.stringify(out));
}})();
"""
    error, review, idle, starting, running = _node(script)
    assert error["color"] == "var(--danger)" and "خطأ" in error["label"]
    assert review["label"] == "بانتظار الفرز والاعتماد البشري (4)"
    assert idle["label"] == "جاهز: لا توجد منتجات بانتظار المراجعة" and "خامل" not in idle["label"]
    assert starting["label"] == "جاري قراءة الشيت وتجهيز الطابور…" and starting["progress"] == "none"
    assert running["progress"] == "block" and running["text"] == "5/20 منتج"        # per-run numbers
    assert "جاهز وخامل" not in read(LAYOUT)


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_home_page_banner_progress_and_review_link():
    statuses = [_status("starting", is_running=True), _status("running", is_running=True),
                _status("review", alert="Gemini لا يستجيب")]
    script = STUB_DOM + _page_script(INDEX) + f"""
const statuses = {json.dumps(statuses, ensure_ascii=False)};
let i = 0;
fetchHandler = () => statuses[Math.min(i++, statuses.length - 1)];
(async () => {{
    await pollBatchStatus();
    const starting = {{ percent: document.getElementById('batchProgressPercent').innerText,
                        counts: document.getElementById('batchProgressCounts').innerText,
                        text: document.getElementById('batchProgressText').textContent }};
    await pollBatchStatus();
    const running = {{ percent: document.getElementById('batchProgressPercent').innerText,
                       counts: document.getElementById('batchProgressCounts').innerText }};
    await pollBatchStatus();
    document.getElementById('runAllBtn').onclick();
    console.log(JSON.stringify({{ starting, running, href: window.location.href,
        banner: document.getElementById('batchStateAlert').style.display,
        bannerText: document.getElementById('batchStateAlert').textContent }}));
}})();
"""
    out = _node(script)
    assert out["starting"] == {"percent": "—", "counts": "", "text": "text:starting"}
    assert out["running"]["percent"] == "25%" and out["running"]["counts"].startswith("5 من 20 في هذا التشغيل")
    assert out["href"] == "/batch-automation?tab=review"
    assert out["banner"] == "block" and "Gemini لا يستجيب" in out["bannerText"]
    assert '"/catalog"' not in read(INDEX)


def test_confirm_texts_say_exactly_what_happens():
    batch, index = read(BATCH), read(INDEX)
    for text in (batch, index):
        stop = text[text.index("const STOP_CONFIRM_TEXT"):]
        stop = stop[:stop.index(";\n")]
        assert "تعود الصفوف التي كانت قيد المعالجة إلى الانتظار" in stop
        assert "لا يُحذف أي صف" in stop
        assert "إنهاء قسري" not in text
    reset = batch[batch.index("const RESET_CONFIRM_TEXT"):]
    reset = reset[:reset.index(";\n")]
    assert "لا يُحذف أي منتج جاهز للمراجعة أو معتمد أو فاشل، ولا أي مرشح أو قرار مراجعة" in reset
    assert "إصلاح تشغيل عالق" in batch and "تصفير وإعادة تعيين الحالة" not in batch
