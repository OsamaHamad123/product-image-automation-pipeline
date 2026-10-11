"""«اليوم» (/): the Home page and the Run page merged into one destination (UI redesign phase 2).

* /batch-automation is a 302 to / that keeps its query (?scope=rows&rows=…), so run.js linkedForm still opens the form
  on those rows; ?tab=review keeps its own redirect to the bulk review; the browser keeps a #fragment across it.
* The admin's page, top to bottom: header, «جهّز لقطة» (setup unfinished), the next step and ONE alert banner, the KPI
  tiles, «التشغيل» (new run + current run), the sheet funnel, «أدوات الشيت». A reviewer gets the header, the next step,
  the KPIs and the funnel: no run panel and no sheet tools are rendered for them.
* «أدوات الشيت» is one card of ARIA tabs (roving tabindex, Home / End, the arrows by reading direction); a #run-… hash
  selects its tab; each tool reads its data the first time its tab shows.
* One /api/run/live poller per page: run.js for the admin (home.js follows its `lq:run-live` snapshots), home.js for a
  reviewer (no run.js on that page).
* Gone from the page: «آخر تشغيل» (the current-run card shows the last run), «جاهزية النشر الآلي» (Settings), the
  services and week's cost strip (Health), the Run page's own banner and its «روح على» row.
"""

import json
import os
import re
import subprocess
import tempfile

import pytest

from laqta_kernel import NEEDS_LARAVEL, kernel
from test_laqta_run import COMMON_JS, HOME_JS, NODE, RUN_JS, read
from test_laqta_ui import DASH, LAYOUT, PHP, VIEWS, _laravel_env

NEEDS_NODE = pytest.mark.skipif(NODE is None, reason="node is not installed")
TODAY = VIEWS / "dashboard" / "index.blade.php"
CONTROLLERS = DASH / "app" / "Http" / "Controllers"
TOOLS = ["run-brands", "run-barcodes", "run-quality", "run-export"]


def _down_env(tmp_path):
    """The app with nothing listening on its database port: the page and the redirect must not need it."""
    compiled = tmp_path / "views"
    compiled.mkdir(exist_ok=True)
    return dict(_laravel_env(compiled), DB_CONNECTION="mariadb", DB_HOST="127.0.0.1", DB_PORT="1",
                DB_DATABASE="laqta_nowhere", CLI_BRIDGE_PATH=str(tmp_path / "no_bridge.py"))


# ---------------------------------------------------------------------------
# /batch-automation -> /
# ---------------------------------------------------------------------------

@NEEDS_LARAVEL
def test_the_old_run_address_redirects_to_today_with_its_query(tmp_path):
    rows, brand, plain, review = kernel(_down_env(tmp_path), [
        ["GET", "/batch-automation?scope=rows&rows=5-7"], ["GET", "/batch-automation?scope=brand&brand=Al%20Rawabi"],
        ["GET", "/batch-automation"], ["GET", "/batch-automation?tab=review"]])
    assert rows["status"] == 302 and rows["location"] == "http://localhost/?scope=rows&rows=5-7"
    assert brand["status"] == 302 and brand["location"] == "http://localhost/?scope=brand&brand=Al%20Rawabi"
    assert plain["status"] == 302 and plain["location"] == "http://localhost/"
    assert review["status"] == 302 and review["location"].endswith("/catalog?mode=bulk")
    routes = read(DASH / "routes" / "web.php")
    # the route name stays, so every route('dashboard.batch_automation') still resolves (to the redirect)
    assert "Route::get('/batch-automation', [RunController::class, 'page'])->name('dashboard.batch_automation');" in routes


@NEEDS_NODE
def test_linked_form_still_reads_the_scope_on_today():
    script = "globalThis.window = globalThis;\n" + read(COMMON_JS) + "\n" + read(RUN_JS) + r"""
const P = window.LaqtaRunPage;
console.log(JSON.stringify([P.linkedForm('?scope=rows&rows=5-7'), P.linkedForm('?brand=Al%20Rawabi&scope=brand'), P.linkedForm('')]));
"""
    out = _node(script)
    assert out == [{"scope": "rows", "rows": "5-7", "brand": ""}, {"scope": "brand", "rows": "", "brand": "Al Rawabi"}, None]


def test_in_app_links_point_at_today():
    for path in (CONTROLLERS / "HealthAttentionController.php", CONTROLLERS / "SetupController.php",
                 DASH / "app" / "Services" / "GotoIndex.php", DASH / "public" / "js" / "home.js",
                 VIEWS / "components" / "lq" / "run-card.blade.php", LAYOUT):
        text = read(path)
        assert "/batch-automation" not in text and "dashboard.batch_automation" not in text, path.name
    assert "route('dashboard.batch_automation')" not in read(TODAY)
    review = read(CONTROLLERS / "ReviewController.php")
    assert "'run' => rtrim(route('dashboard.index'), '/') . '/'," in review    # review/*.js adds ?scope=… or #run-brands
    assert "SetupController::BRANDS_HREF" not in review
    assert "public const BRANDS_HREF = '/#run-brands';" in read(CONTROLLERS / "SetupController.php")
    assert not (VIEWS / "dashboard" / "batch_automation.blade.php").exists()


def test_the_nav_has_today_and_no_run_item():
    layout = read(LAYOUT)
    items = layout[layout.index("$lqNavItems = ["):layout.index("];", layout.index("$lqNavItems = ["))]
    assert "['key' => 'home', 'route' => 'dashboard.index', 'label' => 'اليوم', 'icon' => 'home']," in items
    assert "'key' => 'run'" not in items and "الرئيسية" not in items
    # the run status dot moved onto «اليوم», for the owner only
    assert re.search(r"@if \(\$lqItem\['key'\] === 'home' && ! \$lqReviewer\)\s*\{\{--.*?--\}\}\s*"
                     r"<span class=\"lq-nav__dot\" data-lq-run-dot data-lq-admin", layout, re.S)


# ---------------------------------------------------------------------------
# The page, for each role
# ---------------------------------------------------------------------------

def _render(tmp_path, role):
    """GET / rendered by OverviewController::index for a signed-in user of the role (the database is down)."""
    dash = str(DASH).replace("\\", "/")
    script = f"""<?php
require '{dash}/vendor/autoload.php';
$app = require '{dash}/bootstrap/app.php';
$app->make(Illuminate\\Contracts\\Http\\Kernel::class)->bootstrap();
$request = Illuminate\\Http\\Request::create('/', 'GET');
$route = $app['router']->getRoutes()->match($request);
$request->setRouteResolver(fn () => $route);
$app->instance('request', $request);
auth()->setUser(new App\\Models\\User(['name' => 'pytest-{role}', 'email' => '{role}@x', 'role' => '{role}']));
echo app(App\\Http\\Controllers\\OverviewController::class)->index()->render();
"""
    with tempfile.NamedTemporaryFile("w", suffix=".php", delete=False, encoding="utf-8") as fh:
        fh.write(script)
        path = fh.name
    try:
        result = subprocess.run([PHP, path], cwd=DASH, env=_down_env(tmp_path), capture_output=True, text=True,
                                timeout=180, encoding="utf-8")
    finally:
        os.unlink(path)
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-3000:]
    return result.stdout


@pytest.fixture(scope="module")
def pages(tmp_path_factory):
    if PHP is None or not (DASH / "vendor" / "autoload.php").exists():
        pytest.skip("php or dashboard/vendor is not installed")
    return {role: _render(tmp_path_factory.mktemp(role), role) for role in ("admin", "reviewer")}


def _main(page):
    return page[page.index('<div class="lq-home"'):page.index('id="lq-home-initial"')]


@NEEDS_LARAVEL
def test_the_admin_page_order(pages):
    main = _main(pages["admin"])
    assert "<title>اليوم · لقطة</title>" in pages["admin"]
    marks = ['data-home="greeting"', 'data-home="next"', 'data-home="alert"', 'data-kpi="waiting"',
             'id="lq-today-run-title"', 'id="run-new"', 'id="run-current"', 'data-home="funnel"',
             'id="lq-today-tools-title"', 'id="run-brands"', 'id="run-barcodes"', 'id="run-quality"', 'id="run-export"']
    at = [main.index(m) for m in marks]
    assert at == sorted(at), marks
    assert "data-run-page" in main and "data-home-page" in main
    # the run panel and the tools are action blocks (raised), the funnel a reference block (flat)
    assert re.search(r'<section class="lq-card lq-run-new" id="run-new"', main)
    assert re.search(r'<section class="lq-card lq-run-current" id="run-current"', main)
    assert re.search(r'<section class="lq-card lq-today-tools" id="run-tools"', main)
    assert re.search(r'<section class="lq-card lq-card--flat"[^>]*data-home="funnel"', main)
    # run.js before home.js, run.css with home.css; one initial snapshot
    page = pages["admin"]
    scripts = re.findall(r'<script src="[^"]*/js/([a-z-]+)\.js\?v=', page)
    assert [s for s in scripts if s in ("run-common", "run", "home")] == ["run-common", "run", "home"]
    assert "/css/pages/run.css" in page and "/css/pages/home.css" in page
    assert page.count('id="lq-home-initial"') == 1 and 'id="lq-run-initial"' not in page
    # the nav: «اليوم» is current and carries the owner's run dot; no «التشغيل» destination
    nav = page[page.index('<nav class="lq-nav"'):page.index("</nav>")]
    today = re.search(r'<a href="http://localhost"[^>]*aria-current="page"\s*>(.*?)</a>', nav, re.S)
    assert today and "اليوم" in today.group(1) and "data-lq-run-dot" in today.group(1)
    assert "/batch-automation" not in nav and ">التشغيل<" not in nav


@NEEDS_LARAVEL
def test_the_reviewer_gets_no_run_panel_and_no_tools(pages):
    page = pages["reviewer"]
    main = _main(page)
    for mark in ('data-home="greeting"', 'data-home="next"', 'data-kpi="waiting"', 'data-home="funnel"'):
        assert mark in main, mark
    for gone in ("data-run-page", 'id="run-new"', 'id="run-current"', 'id="run-brands"', 'id="run-export"',
                 "data-run-tool-tab", 'data-run="', "أدوات الشيت", "ابدأ التشغيل"):
        assert gone not in main, gone
    assert "/js/run.js" not in page and "/css/pages/run.css" not in page and "/js/home.js" in page
    assert "data-lq-run-dot" not in page and "#run-current" not in page          # no dot, no run-card link
    assert page.count('id="lq-home-initial"') == 1                             # home.js polls from its snapshot


@NEEDS_LARAVEL
@pytest.mark.parametrize("role", ["admin", "reviewer"])
def test_the_duplicate_cards_are_gone(pages, role):
    main = _main(pages[role])
    for gone in ("آخر تشغيل</h2>", 'data-home="lastrun', "جاهزية النشر الآلي", 'data-home="ready', "lq-home-services",
                 'data-home="services', 'data-home="cost"', "تكلفة هالأسبوع", 'data-run="alert"', "lq-jump",
                 "روح على"):
        assert gone not in main, (role, gone)
    # ONE alert banner on the page: the next step's box is a status, the banner the only role="alert" banner
    assert main.count("lq-alert--banner") == 1 and 'data-home="alert"' in main


def test_the_controller_left_what_other_pages_own():
    overview = read(CONTROLLERS / "OverviewController.php")
    for gone in ("function readiness(", "function reviewStats(", "function services(", "review_stats", "'cost'"):
        assert gone not in overview, gone
    assert "self::alerts(" in overview                          # the provider alerts still reach the one banner
    home = read(DASH / "public" / "js" / "home.js")
    for gone in ("readiness", "services", "lastrun", "lastRun", "cost"):
        assert gone not in home, gone


# ---------------------------------------------------------------------------
# «أدوات الشيت»: tabs
# ---------------------------------------------------------------------------

def test_the_tools_card_is_an_aria_tablist_with_the_old_ids():
    view = read(TODAY)
    tools = view[view.index('id="run-tools"'):view.index("</section>", view.index('id="run-tools"'))]
    assert 'role="tablist" aria-label="أدوات الشيت"' in tools
    tab = re.search(r'<button type="button" role="tab" class="lq-today-tools__tab" id="\{\{ \$toolId \}\}-tab" '
                    r'aria-controls="\{\{ \$toolId \}\}" aria-selected="\{\{ \$loop->first \? \'true\' : \'false\' \}\}" '
                    r'@unless \(\$loop->first\) tabindex="-1" @endunless data-run-tool-tab>', tools)
    assert tab, "one tab per tool, the first selected and the only one in the tab order"
    labels = re.search(r"\$todayTools = \[(.*?)\];", view, re.S).group(1)
    assert re.findall(r"'(run-[a-z]+)' =>", labels) == TOOLS
    for label in ("ماركات ناقصة", "باركودات", "جودة بيانات الشيت", "تقرير للتحليل"):
        assert f"=> '{label}'" in labels, label
    for i, tool in enumerate(TOOLS):
        panel = re.search(r'<div class="lq-today-tools__panel" id="' + tool + r'" role="tabpanel" aria-labelledby="'
                          + tool + r'-tab" tabindex="0"[^>]*>', tools)
        assert panel, tool
        assert (" hidden>" in panel.group(0)) is (i > 0), tool              # one tool visible at a time
    assert "<x-lq.jump-links" not in view


@NEEDS_NODE
def test_tab_keys_follow_the_reading_direction_and_hashes_name_tools():
    out = _node("globalThis.window = globalThis;\n" + read(COMMON_JS) + "\n" + read(RUN_JS) + r"""
const P = window.LaqtaRunPage;
console.log(JSON.stringify({
    rtl: ['ArrowLeft', 'ArrowRight', 'Home', 'End', 'Tab'].map(k => P.tabStep(k, 0, 4, true)),
    rtlWrap: [P.tabStep('ArrowLeft', 3, 4, true), P.tabStep('ArrowRight', 0, 4, true)],
    ltr: [P.tabStep('ArrowRight', 0, 4, false), P.tabStep('ArrowLeft', 0, 4, false)],
    hashes: ['#run-brands', 'run-export', '#run-new', '#run-tools', '', null].map(P.toolFromHash),
    ids: P.TOOL_IDS
}));
""")
    assert out["rtl"] == [1, 3, 0, 3, -1]                     # RTL: ArrowLeft is the next tab
    assert out["rtlWrap"] == [0, 3]
    assert out["ltr"] == [1, 3]
    assert out["hashes"] == ["run-brands", "run-export", None, None, None, None]
    assert out["ids"] == TOOLS


# A small DOM for run.js + home.js mounted together: hooks are found by their selector and created on first use.
TODAY_DOM = r"""
const docListeners = {}, winListeners = {};
class El {
    constructor(tag, attrs) {
        this.tagName = String(tag || 'div').toUpperCase(); this.children = []; this.parentNode = null;
        this.attrs = Object.assign({}, attrs || {}); this.listeners = {}; this.style = {}; this._text = ''; this._value = '';
        this.disabled = false; this.checked = false; this.className = ''; this.tabIndex = 0; this.q = {}; this.scrolled = 0;
        const set = new Set();
        this.classList = { add: c => set.add(c), remove: c => set.delete(c), contains: c => set.has(c),
                           toggle: (c, on) => { if (on === undefined ? !set.has(c) : on) set.add(c); else set.delete(c); } };
    }
    get firstChild() { return this.children[0] || null; }
    appendChild(c) { c.parentNode = this; this.children.push(c); return c; }
    removeChild(c) { this.children.splice(this.children.indexOf(c), 1); c.parentNode = null; return c; }
    get textContent() { return this._text + this.children.map(c => c.textContent).join(''); }
    set textContent(v) { this._text = String(v); this.children = []; }
    setAttribute(k, v) { this.attrs[k] = String(v); }
    getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
    hasAttribute(k) { return k in this.attrs; }
    removeAttribute(k) { delete this.attrs[k]; }
    get hidden() { return 'hidden' in this.attrs; }
    get options() { return this.children.filter(c => c.tagName === 'OPTION'); }
    get value() { return this._value; }
    set value(v) { this._value = String(v); }
    focus() { document.activeElement = this; }
    scrollIntoView() { this.scrolled++; }
    addEventListener(t, fn) { (this.listeners[t] = this.listeners[t] || []).push(fn); }
    fire(t, extra) {
        const e = Object.assign({ defaultPrevented: false, preventDefault() { this.defaultPrevented = true; }, stopPropagation() {} }, extra || {});
        (this.listeners[t] || []).forEach(fn => fn(Object.assign(e, { currentTarget: this, target: this })));
        return e;
    }
    querySelector(sel) {
        if (!this.q[sel]) {
            const m = sel.match(/^\[data-run="([a-z-]+)"\]$/);
            const tag = m && ['brand', 'export-scope'].includes(m[1]) ? 'select' : (m && m[1] === 'rows' ? 'input' : 'div');
            this.q[sel] = new El(tag);
        }
        return this.q[sel];
    }
    querySelectorAll(sel) {
        if (sel === '[data-run-tool-tab]') return this.tabs || [];
        if (sel === '[data-home="review-count"]') return [this.querySelector(sel)];
        return [];
    }
}
const ADMIN = typeof ROLE === 'undefined' || ROLE === 'admin';
const page = new El('div', ADMIN ? { 'data-home-page': '', 'data-run-page': '' } : { 'data-home-page': '' });
const PANEL_HOOK = { 'run-brands': 'brands', 'run-barcodes': 'barcodes', 'run-quality': 'quality' };
const byId = {};
if (ADMIN) {
    page.tabs = ['run-brands', 'run-barcodes', 'run-quality', 'run-export'].map((id, i) => {
        const tab = new El('button', { role: 'tab', id: id + '-tab', 'aria-controls': id, 'aria-selected': i ? 'false' : 'true' });
        tab.tabIndex = i ? -1 : 0;
        byId[id + '-tab'] = tab;
        const panel = PANEL_HOOK[id] ? page.querySelector('[data-run="' + PANEL_HOOK[id] + '"]') : new El('div');
        if (i) panel.setAttribute('hidden', '');
        byId[id] = panel;
        return tab;
    });
}
const island = new El('script');
island.textContent = 'null';
globalThis.document = {
    readyState: 'loading', hidden: false, activeElement: null,
    body: new El('body', { 'data-lq-role': ADMIN ? 'admin' : 'reviewer', 'data-lq-tz': 'Asia/Dubai' }),
    documentElement: new El('html', { dir: 'rtl' }),
    querySelector: (sel) => sel === '[data-home-page]' || (sel === '[data-run-page]' && ADMIN) ? page : null,
    getElementById: (id) => id === 'lq-home-initial' ? island : (byId[id] || null),
    createElement: (t) => new El(t),
    createTextNode: (t) => { const n = new El('#text'); n._text = String(t); return n; },
    addEventListener: (t, fn) => { (docListeners[t] = docListeners[t] || []).push(fn); },
    dispatchEvent: (e) => { (docListeners[e.type] || []).forEach(fn => fn(e)); return true; }
};
globalThis.location = { hash: typeof HASH === 'undefined' ? '' : HASH, pathname: '/', search: '' };
globalThis.history = { pushed: [], pushState(s, t, url) { this.pushed.push(url); location.hash = url; } };
globalThis.addEventListener = (t, fn) => { (winListeners[t] = winListeners[t] || []).push(fn); };
const net = [];
let liveWaiting = 7;
globalThis.fetch = (url, init) => {
    net.push(url);
    let body = { status: 'success' };
    if (url === '/api/run/live') body = { status: 'success', batch: { phase: 'idle', run: {}, ready_for_review: liveWaiting, alert: '' }, run: null, recent: [] };
    else if (url.startsWith('/api/overview')) body = { status: 'success', kpis: {}, sheet: { status: 'ok', total: 0, stages: [] }, alerts: [] };
    else if (url.startsWith('/api/run/plan')) body = { status: 'success', total: 1, skipped_final: 0, estimate: {}, brands: [] };
    else if (url.startsWith('/api/run/brand-suggestions')) body = { status: 'success', count: 0, rows: 0, brands: [] };
    else if (url.startsWith('/api/run/barcode-suggestions')) body = { status: 'success', rows: [], duplicates: [], filled: [] };
    else if (url.startsWith('/api/run/sheet-quality')) body = { status: 'success', total: 3, groups: [] };
    return Promise.resolve({ ok: true, status: 200, json: async () => body });
};
globalThis.Laqta = { toast: () => {} };
const realTimeout = setTimeout;
const later = [];
globalThis.setTimeout = (fn, ms) => (ms >= 1000 ? later.push(ms) : realTimeout(fn, ms));
const wait = (ms) => new Promise(r => realTimeout(r, ms));
const tools = () => net.filter(u => /^\/api\/run\/(brand-suggestions|barcode-suggestions|sheet-quality)/.test(u)).map(u => u.split('?')[0]);
const selected = () => page.tabs.filter(t => t.getAttribute('aria-selected') === 'true').map(t => t.getAttribute('aria-controls'));
const shown = () => ['run-brands', 'run-barcodes', 'run-quality', 'run-export'].filter(id => !byId[id].hidden);
"""


def _node(script):
    result = subprocess.run([NODE, "-"], input=script, capture_output=True, text=True, timeout=60, encoding="utf-8")
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


def _today(prelude, script, files=(COMMON_JS, RUN_JS, HOME_JS)):
    boot = "(docListeners.DOMContentLoaded || []).forEach(fn => fn());\n"     # the scripts mount in their order
    return _node("globalThis.window = globalThis;\n" + prelude + TODAY_DOM + "\n".join(read(f) for f in files) + "\n"
                 + boot + script)


@NEEDS_NODE
def test_a_hash_selects_its_tool_and_each_tool_loads_on_its_first_show():
    out = _today("const HASH = '#run-barcodes';\n", r"""
(async () => {
    await wait(60);
    const first = { selected: selected(), shown: shown(), tools: tools(), scrolled: page.querySelector('[data-run="tools"]').scrolled,
                    tabIndex: page.tabs.map(t => t.tabIndex) };
    page.tabs[0].fire('click');                                  // ماركات ناقصة
    await wait(30);
    page.tabs[1].fire('click'); page.tabs[0].fire('click');      // back and forth: nothing is read twice
    await wait(30);
    const clicked = { selected: selected(), shown: shown(), tools: tools() };
    // the keys: in RTL ArrowLeft goes to the next tab, End to the last, Home to the first
    const k1 = page.tabs[0].fire('keydown', { key: 'ArrowLeft' });
    const afterLeft = [selected()[0], document.activeElement === page.tabs[1], k1.defaultPrevented];
    page.tabs[1].fire('keydown', { key: 'End' });
    const afterEnd = selected()[0];
    page.tabs[3].fire('keydown', { key: 'Home' });
    const afterHome = selected()[0];
    page.tabs[0].fire('keydown', { key: 'ArrowRight' });
    const afterRight = selected()[0];
    await wait(30);
    // a link on the page to a tool («ضيف الماركات»): no reload, its tab, the card in view, focus on the tab
    const link = { hash: '#run-quality', pathname: '/', closest() { return this; } };
    const e = { target: link, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; } };
    (docListeners.click || []).forEach(fn => fn(e));
    await wait(30);
    const linked = { prevented: e.defaultPrevented, selected: selected(), pushed: history.pushed,
                     focus: document.activeElement === page.tabs[2], scrolled: page.querySelector('[data-run="tools"]').scrolled };
    // a hash change from elsewhere («روح لـ…»)
    location.hash = '#run-export';
    (winListeners.hashchange || []).forEach(fn => fn());
    console.log(JSON.stringify({ first, clicked, afterLeft, afterEnd, afterHome, afterRight, linked,
                                 hashed: selected(), tools: tools() }));
    process.exit(0);
})();
""")
    first = out["first"]
    assert first["selected"] == ["run-barcodes"] and first["shown"] == ["run-barcodes"] and first["scrolled"] == 1
    assert first["tabIndex"] == [-1, 0, -1, -1]                                 # roving tabindex
    assert first["tools"] == ["/api/run/barcode-suggestions"]                   # only the tool on screen is read
    clicked = out["clicked"]
    assert clicked["selected"] == ["run-brands"] and clicked["shown"] == ["run-brands"]
    assert clicked["tools"].count("/api/run/brand-suggestions") >= 1 and clicked["tools"].count("/api/run/barcode-suggestions") == 1
    assert out["afterLeft"] == ["run-barcodes", True, True]
    assert (out["afterEnd"], out["afterHome"], out["afterRight"]) == ("run-export", "run-brands", "run-export")
    linked = out["linked"]
    assert linked["prevented"] and linked["selected"] == ["run-quality"] and linked["pushed"] == ["#run-quality"]
    assert linked["focus"] and linked["scrolled"] == 2
    assert out["hashed"] == ["run-export"]
    assert out["tools"].count("/api/run/sheet-quality") == 1                     # read once, on its first show
    assert "/api/run/sheet-quality" not in first["tools"]


@NEEDS_NODE
def test_the_tools_wait_for_the_plan_and_the_first_tab_loads_without_a_hash():
    out = _today("", r"""
(async () => {
    await wait(60);
    const order = net.filter(u => u.startsWith('/api/run/')).map(u => u.split('?')[0]);
    console.log(JSON.stringify({ order, selected: selected(), shown: shown(), scrolled: page.querySelector('[data-run="tools"]').scrolled }));
    process.exit(0);
})();
""", files=(COMMON_JS, RUN_JS))
    assert out["selected"] == ["run-brands"] and out["shown"] == ["run-brands"] and out["scrolled"] == 0
    assert out["order"].index("/api/run/plan") < out["order"].index("/api/run/brand-suggestions")
    assert "/api/run/barcode-suggestions" not in out["order"] and "/api/run/sheet-quality" not in out["order"]


# ---------------------------------------------------------------------------
# One /api/run/live poller
# ---------------------------------------------------------------------------

@NEEDS_NODE
def test_the_admin_page_polls_the_run_once_and_home_follows_it():
    out = _today("", r"""
(async () => {
    await wait(60);
    const live = () => net.filter(u => u === '/api/run/live').length;
    const first = { live: live(), count: page.querySelector('[data-home="review-count"]').textContent,
                    kpi: page.querySelector('[data-kpi="waiting"] [data-kpi-value]').textContent, later: later.slice(),
                    owner: !!LaqtaRunPage.controller };
    liveWaiting = 4;
    await LaqtaRunPage.controller.poll();                    // run.js's next poll
    await wait(20);
    console.log(JSON.stringify({ first, live: live(), count: page.querySelector('[data-home="review-count"]').textContent,
                                 visibility: (docListeners.visibilitychange || []).length }));
    process.exit(0);
})();
""")
    first = out["first"]
    assert first["owner"] and first["live"] == 1                       # one request, from run.js
    assert first["count"] == "7" and first["kpi"] == "7"               # home.js drew run.js's snapshot
    assert first["later"] == [8000]                                    # run.js's next poll only: home.js runs no loop
    assert out["live"] == 2 and out["count"] == "4"                    # home.js follows every snapshot
    assert out["visibility"] == 1                                      # run.js alone asks again when the tab shows


@NEEDS_NODE
def test_the_reviewer_page_polls_from_home_js():
    out = _today("const ROLE = 'reviewer';\n", r"""
(async () => {
    await wait(60);
    console.log(JSON.stringify({ live: net.filter(u => u === '/api/run/live').length, run: typeof LaqtaRunPage,
                                 count: page.querySelector('[data-home="review-count"]').textContent, later: later.slice(),
                                 tools: tools() }));
    process.exit(0);
})();
""", files=(COMMON_JS, HOME_JS))
    assert out["run"] == "undefined" and out["live"] == 1 and out["count"] == "7"
    assert out["later"] == [10000]                                     # home.js's own idle loop
    assert out["tools"] == []                                          # a reviewer's next step reads no brands


def test_each_script_names_the_poll_owner():
    run_js, home_js = read(RUN_JS), read(HOME_JS)
    assert "THE /api/run/live POLLER of the page" in run_js and "var LIVE_EVENT = 'lq:run-live';" in run_js
    assert "ONE poller per page" in home_js and "owner.LIVE_EVENT" in home_js
    layout_js = read(DASH / "public" / "js" / "layout.js")
    assert "return $('[data-home-page]') ? PAGE_POLL_MS : POLL_MS;" in layout_js   # the sidebar slows down on «اليوم»
