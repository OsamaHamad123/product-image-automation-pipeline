"""Site UX batch 4: the phone tab bar's «حسابي», polling while the tab is hidden, unsaved settings, the eval-export
error text and the shell script in its own cached file.

* «حسابي» on the tab bar (980px and below, where the sidebar's name and «خروج» are hidden) opens the name and a
  «خروج» that submits the sidebar's own logout form (POST + CSRF), for both roles; the owner's run tab carries a
  status dot that the layout's /api/batch-status poll keeps up to date, a reviewer gets none;
* public/js/layout.js (the layout's script, loaded with ?v=filemtime): no poll while the tab is hidden, one right
  away when it shows again, a slower one on Run and Home (they poll /api/run/live themselves), and one every 30 s
  while hidden only when «نبّهني لما يخلص» waits for a running run;
* run.js / home.js: their /api/run/live loop sends nothing while the tab is hidden, and asks at once when it shows;
* settings.js: a changed card says «ما انحفظ»; saving another card, a link out and leaving the page ask first; a
  save lands back on its card (SettingsController::anchorFor -> ?tab=…#id);
* health.js: an eval-export refusal in English reads as plain Arabic, the raw text under «التفاصيل التقنية».
Node runs the scripts unchanged against small fake DOM pieces; the role rendering boots Laravel through the PHP CLI
(no database: the user is set on the guard).
"""

import json
import os
import re
import subprocess
import tempfile

import pytest

from laqta_kernel import RUN_DOM
from test_laqta_run import COMMON_JS, HARNESS, HOME_JS, NODE, RUN_JS, _js
from test_laqta_ui import CSS, DASH, LAYOUT, LAYOUT_JS, NEEDS_LARAVEL, PHP, VIEWS, _laravel_env, read

NEEDS_NODE = pytest.mark.skipif(NODE is None, reason="node is not installed")
SETTINGS_JS = DASH / "public" / "js" / "settings.js"
HEALTH_JS = DASH / "public" / "js" / "health.js"
CONTROLLER = DASH / "app" / "Http" / "Controllers" / "SettingsController.php"


def _node(script: str):
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as fh:
        fh.write(script)
        path = fh.name
    try:
        result = subprocess.run([NODE, path], capture_output=True, text=True, timeout=60, encoding="utf-8")
    finally:
        os.unlink(path)
    assert result.returncode == 0, result.stderr[-3000:]
    return json.loads(result.stdout.strip().splitlines()[-1])


# ---------------------------------------------------------------------------
# «حسابي» and the run dot: the layout's markup and CSS
# ---------------------------------------------------------------------------

def test_the_tab_bar_has_an_account_item_that_submits_the_logout_form():
    layout = read(LAYOUT)
    nav = layout[layout.index('<nav class="lq-nav"'):layout.index("</nav>")]
    assert "data-lq-account-toggle" in nav and "حسابي" in nav and 'aria-controls="lq-account-sheet"' in nav
    form = re.search(r'<form class="lq-sidebar__user" id="lqLogoutForm" method="POST" action="\{\{ route\(\'logout\'\) \}\}">'
                     r'\s*@csrf', layout)
    assert form, "the sidebar's logout form keeps its POST, its CSRF token and gets an id"
    sheet = layout[layout.index('<div class="lq-account-sheet"'):]
    sheet = sheet[:sheet.index("</div>")]
    assert 'id="lq-account-sheet"' in sheet and "data-lq-account-sheet hidden" in sheet
    assert '<button class="lq-btn lq-btn--secondary lq-account-sheet__logout" type="submit" form="lqLogoutForm">خروج</button>' in sheet
    assert "auth()->user()->name" in sheet
    # the dot sits in the run item only (a reviewer has no run item) and is admin-only in any case
    dot = re.search(r"@if \(\$lqItem\['key'\] === 'run'\)\s*\{\{--.*?--\}\}\s*<span class=\"lq-nav__dot\" data-lq-run-dot data-lq-admin",
                    layout, re.S)
    assert dot


def test_the_account_item_and_the_dot_show_only_on_the_tab_bar():
    css = read(CSS)
    base, phone = css.split("@media (max-width: 980px)", 1)
    assert re.search(r"\.lq-body \.lq-nav__account,\s*\.lq-nav__dot,\s*\.lq-account-sheet \{\s*display: none;", base)
    assert re.search(r"\.lq-body \.lq-nav__account \{\s*display: flex;", phone)
    assert re.search(r"\.lq-account-sheet:not\(\[hidden\]\) \{\s*position: fixed;", phone)
    assert '.lq-nav__dot:not([data-state="loading"]):not([data-state="unknown"])' in phone
    # the sidebar's own row stays hidden on the phone: «حسابي» replaces it
    assert re.search(r"\.lq-sidebar__user,\s*\.lq-sidebar \.lq-runcard \{\s*display: none;", phone)


@pytest.fixture(scope="module")
def role_pages(tmp_path_factory):
    if PHP is None or not (DASH / "vendor" / "autoload.php").exists():
        pytest.skip("php or dashboard/vendor is not installed")
    compiled = tmp_path_factory.mktemp("compiled_roles")
    dash = str(DASH).replace("\\", "/")
    script = f"""<?php
require '{dash}/vendor/autoload.php';
$app = require '{dash}/bootstrap/app.php';
$app->make(Illuminate\\Contracts\\Http\\Kernel::class)->bootstrap();
$out = [];
foreach (['admin', 'reviewer'] as $role) {{
    $request = Illuminate\\Http\\Request::create('/', 'GET');
    $route = $app['router']->getRoutes()->match($request);
    $request->setRouteResolver(fn () => $route);
    $app->instance('request', $request);
    auth()->setUser(new App\\Models\\User(['name' => 'pytest-' . $role, 'email' => $role . '@x', 'role' => $role]));
    $out[$role] = Illuminate\\Support\\Facades\\Blade::render(
        "@extends('layouts.laqta')\\n@section('content')<p>محتوى</p>@endsection");
}}
echo json_encode($out, JSON_UNESCAPED_UNICODE);
"""
    with tempfile.NamedTemporaryFile("w", suffix=".php", delete=False, encoding="utf-8") as fh:
        fh.write(script)
        path = fh.name
    try:
        result = subprocess.run([PHP, path], cwd=DASH, env=_laravel_env(compiled), capture_output=True, text=True,
                                timeout=120, encoding="utf-8")
    finally:
        os.unlink(path)
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-3000:]
    return json.loads(result.stdout)


@NEEDS_LARAVEL
@pytest.mark.parametrize("role", ["admin", "reviewer"])
def test_both_roles_get_the_account_item_and_a_working_logout(role_pages, role):
    page = role_pages[role]
    assert f'data-lq-role="{role}"' in page
    form = re.search(r'<form class="lq-sidebar__user" id="lqLogoutForm" method="POST" action="([^"]+)">\s*'
                     r'<input type="hidden" name="_token" value="([^"]*)"', page)
    assert form and form.group(1).endswith("/logout")
    assert 'type="submit" form="lqLogoutForm">خروج</button>' in page
    assert "data-lq-account-toggle" in page and "حسابي" in page
    assert f'<span class="lq-account-sheet__name" dir="ltr">pytest-{role}</span>' in page
    assert ("data-lq-run-dot" in page) is (role == "admin")
    # the shell's script is the cached file, not an inline block
    assert re.search(r'<script src="[^"]*/js/layout\.js\?v=\d+"></script>', page)


# ---------------------------------------------------------------------------
# public/js/layout.js under node: the dot, «حسابي», and no poll while hidden
# ---------------------------------------------------------------------------

LAYOUT_DOM = r"""
class El {
    constructor(name, matches) {
        this.name = name; this.attrs = {}; this.listeners = {}; this.kids = {}; this.children = []; this.parent = null;
        this.matches = matches || []; this.className = ''; this.textContent = ''; this.style = {};
        const set = new Set();
        this.classList = { toggle: (c, on) => (on ? set.add(c) : set.delete(c)), contains: c => set.has(c) };
    }
    setAttribute(k, v) { this.attrs[k] = String(v); }
    getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
    hasAttribute(k) { return k in this.attrs; }
    removeAttribute(k) { delete this.attrs[k]; }
    addEventListener(t, fn) { (this.listeners[t] = this.listeners[t] || []).push(fn); }
    querySelector(sel) { return this.kids[sel] || null; }
    appendChild(c) { c.parent = this; this.children.push(c); return c; }
    removeChild(c) { this.children.splice(this.children.indexOf(c), 1); c.parent = null; return c; }
    get parentNode() { return this.parent; }
    focus() { document.activeElement = this; }
    closest(sel) { return this.matches.includes(sel) ? this : (this.parent ? this.parent.closest(sel) : null); }
}
const nodes = {};
const docListeners = {};
const body = new El('body');
body.setAttribute('data-lq-status-url', '/api/batch-status');
globalThis.document = {
    body, hidden: false, title: 'لقطة', activeElement: null,
    querySelector: sel => nodes[sel] || null,
    querySelectorAll: () => [],
    addEventListener: (t, fn) => { (docListeners[t] = docListeners[t] || []).push(fn); },
    dispatchEvent: () => true,
    createElement: t => new El(t),
    createElementNS: (ns, t) => new El(t),
    contains: () => true,
    getElementById: () => null
};
globalThis.CustomEvent = class { constructor(type, init) { this.type = type; this.detail = init && init.detail; } };
const timers = [];
globalThis.setTimeout = (fn, ms) => { timers.push({ fn, ms }); return timers.length; };
globalThis.clearTimeout = () => {};
const fetches = [];
let reply = { is_running: true, status: 'running', total: 10, current: 4 };
globalThis.fetch = url => { fetches.push(url); return Promise.resolve({ ok: true, status: 200, json: async () => reply }); };
const flush = async () => { for (let i = 0; i < 6; i++) await new Promise(r => setImmediate(r)); };
const runTimer = async () => { const t = timers.shift(); t.fn(); await flush(); return t.ms; };
function click(target) {
    const ev = { target, key: '', preventDefault() {}, stopPropagation() {} };
    (target.listeners.click || []).forEach(fn => fn(ev));
    (docListeners.click || []).forEach(fn => fn(ev));
}
function key(k) { (docListeners.keydown || []).forEach(fn => fn({ key: k, preventDefault() {}, stopPropagation() {} })); }
function visible(shown) { document.hidden = !shown; (docListeners.visibilitychange || []).forEach(fn => fn()); }

const runcard = new El('runcard');
nodes['[data-lq-runcard]'] = runcard;
const dot = new El('dot');
dot.setAttribute('data-state', 'loading');
nodes['[data-lq-run-dot]'] = dot;
const toggle = new El('toggle', ['[data-lq-account-toggle]']);
toggle.setAttribute('aria-expanded', 'false');
const sheet = new El('sheet', ['[data-lq-account-sheet]']);
sheet.setAttribute('hidden', '');
const logout = new El('logout');
logout.parent = sheet;
sheet.kids.button = logout;
nodes['[data-lq-account-toggle]'] = toggle;
nodes['[data-lq-account-sheet]'] = sheet;
const outside = new El('main');
"""


def _layout(setup: str, script: str):
    return _node("globalThis.window = globalThis;\n" + LAYOUT_DOM + setup + "\n" + read(LAYOUT_JS) + "\n" + script)


@NEEDS_NODE
def test_the_layout_poll_waits_while_hidden_and_feeds_the_dot():
    out = _layout("document.hidden = true;", r"""
(async () => {
    const out = {};
    await flush();
    out.hidden = [fetches.length, timers.length];          // hidden at load: nothing asked, nothing scheduled
    visible(true);
    out.shown = await runTimer();                            // back in view: at once
    out.afterShown = [fetches.slice(), dot.getAttribute('data-state'), dot.getAttribute('aria-label')];
    out.next = timers[0].ms;                                 // no page poll of its own here: every 5 s
    document.hidden = true;
    await runTimer();                                        // hidden again: the timer fires and asks nothing
    out.hiddenAgain = [fetches.length, timers.length];
    reply = { is_running: false, status: 'error', notice: 'SHEETS_UNAVAILABLE: x' };
    visible(true);
    await runTimer();
    out.error = [fetches.length, dot.getAttribute('data-state')];
    console.log(JSON.stringify(out));
})();
""")
    assert out["hidden"] == [0, 0]
    assert out["shown"] == 0
    assert out["afterShown"] == [["/api/batch-status"], "running", "حالة التشغيل: يعمل"]
    assert out["next"] == 5000
    assert out["hiddenAgain"] == [1, 0]
    assert out["error"] == [2, "error"]


@NEEDS_NODE
def test_the_layout_poll_backs_off_where_the_page_polls_the_run_itself():
    out = _layout("nodes['[data-run-page]'] = new El('run-page');", r"""
(async () => {
    await flush();
    console.log(JSON.stringify({ fetches: fetches.length, next: timers[0].ms }));
})();
""")
    assert out == {"fetches": 1, "next": 15000}


@NEEDS_NODE
def test_a_hidden_tab_still_hears_the_end_of_a_run_it_asked_to_be_told_about():
    """«نبّهني لما يخلص» notifies only while the tab is hidden: with the permission given and a run going, the
    layout keeps asking while hidden (every 30 s) and tells the end; without it, nothing is asked while hidden."""
    script = r"""
(async () => {
    await flush();                                          // a running run, seen in view
    document.hidden = true;
    reply = { is_running: false, status: 'idle', ready_for_review: 3 };
    await runTimer();
    const out = { fetches: fetches.length, next: timers.length ? timers[0].ms : null, notes };
    if (timers.length) await runTimer();                    // the run is over: hidden now asks nothing more
    out.after = [fetches.length, timers.length];
    console.log(JSON.stringify(out));
})();
"""
    granted = _layout("const notes = []; globalThis.Notification = function (title, o) { notes.push([title, o.body]); };"
                      "Notification.permission = 'granted';", script)
    assert granted["fetches"] == 2 and granted["notes"] == [["خلص التشغيل", "3 بانتظار مراجعتك."]]
    assert granted["next"] == 30000 and granted["after"] == [2, 0]
    silent = _layout("const notes = [];", script)
    assert silent == {"fetches": 1, "next": None, "notes": [], "after": [1, 0]}


@NEEDS_NODE
def test_the_account_sheet_opens_and_closes():
    out = _layout("", r"""
(async () => {
    const out = {};
    const state = () => [toggle.getAttribute('aria-expanded'), sheet.hasAttribute('hidden')];
    click(toggle);
    out.open = [...state(), document.activeElement === logout];
    click(logout);                                          // inside the sheet: stays open (the button submits the form)
    out.inside = state();
    click(outside);
    out.outside = state();
    click(toggle);
    key('Escape');
    out.escape = [...state(), document.activeElement === toggle];
    click(toggle); click(toggle);
    out.again = state();
    console.log(JSON.stringify(out));
})();
""")
    assert out["open"] == ["true", False, True]
    assert out["inside"] == ["true", False]
    assert out["outside"] == ["false", True]
    assert out["escape"] == ["false", True, True]
    assert out["again"] == ["false", True]


@NEEDS_NODE
def test_the_layout_script_keeps_what_the_pages_use():
    """The review page, the session banner and the approvals line use these: same names as the inline script had."""
    out = _layout("", r"""
console.log(JSON.stringify({ api: Object.keys(window.Laqta).sort(), events: docListeners.visibilitychange.length }));
""")
    assert out["api"] == sorted(["normalizeRunStatus", "describeRunStatus", "plainNotice", "refreshRunStatus",
                                 "sessionExpired", "sessionRestored", "lastRunStatus", "onRunStatus", "toast", "ask",
                                 "runNotice"])
    js = read(LAYOUT_JS)
    for hook in ("'lq:run-status'", "'lq:change'", "lqSessionExpired", "data-lq-session-pending", "data-lq-approvals",
                 "data-lq-confirm", "data-lq-review-url", "data-lq-status-url"):
        assert hook in js, hook


# ---------------------------------------------------------------------------
# run.js / home.js: no /api/run/live while the tab is hidden
# ---------------------------------------------------------------------------

LOOP = r"""
lives = [{ status: 'success', batch: { phase: 'running', ready_for_review: 2, run: {} }, run: null, recent: [] }];
let hidden = true;
const scheduled = [];
deps.hidden = () => hidden;
deps.schedule = (fn, ms) => { scheduled.push(ms); return scheduled.length; };
const ctl = PAGE.createController(deps);
(async () => {
    const live = () => fetchLog.filter(f => f.url === '/api/run/live').length;
    ctl.loop();
    await tick();
    const whileHidden = [live(), scheduled.slice()];
    hidden = false;
    ctl.loop();
    await tick(); await tick();
    console.log(JSON.stringify({ whileHidden, shown: live(), scheduled }));
})();
"""


@NEEDS_NODE
@pytest.mark.parametrize("page", ["run", "home"])
def test_the_live_loop_sends_nothing_while_the_tab_is_hidden(page):
    files = (COMMON_JS, RUN_JS) if page == "run" else (COMMON_JS, HOME_JS)
    name = "LaqtaRunPage" if page == "run" else "LaqtaHomePage"
    out = _node(_js(*files) + HARNESS + f"const PAGE = {name};\n" + LOOP)
    count, delays = out["whileHidden"]
    assert count == 0 and len(delays) == 1                  # the loop goes on, without a request
    assert out["shown"] == 1 and len(out["scheduled"]) == 2


@NEEDS_NODE
def test_the_run_page_asks_at_once_when_the_tab_shows_again():
    out = _node("globalThis.window = globalThis;\n" + RUN_DOM + read(COMMON_JS) + "\n" + read(RUN_JS) + r"""
const realTimeout = setTimeout;
const later = [];
globalThis.setTimeout = (fn, ms) => (ms >= 1000 ? later.push(ms) : realTimeout(fn, ms));
const docListeners = {};
document.addEventListener = (t, fn) => { (docListeners[t] = docListeners[t] || []).push(fn); };
island.textContent = 'null';
document.hidden = true;
LaqtaRunPage.mount(document);
(async () => {
    const live = () => net.filter(n => n.url === '/api/run/live').length;
    await wait(60);
    const hidden = live();
    document.hidden = false;
    docListeners.visibilitychange.forEach(fn => fn());
    await wait(60);
    console.log(JSON.stringify({ hidden, shown: live(), later: later.length > 0 }));
})();
""")
    assert out == {"hidden": 0, "shown": 1, "later": True}


def test_both_pages_tell_their_controller_when_the_tab_is_hidden():
    for path in (RUN_JS, HOME_JS):
        js = read(path)
        assert "hidden: function () { return !!doc.hidden; }" in js, path.name
        assert "doc.addEventListener('visibilitychange', function () {\n            if (!doc.hidden) controller.poll();" \
            in js.replace("\r\n", "\n"), path.name
        assert "if (deps.hidden && deps.hidden()) {" in js, path.name


# ---------------------------------------------------------------------------
# settings.js: unsaved changes
# ---------------------------------------------------------------------------

SETTINGS_DOM = r"""
class El {
    constructor(tag, matches) {
        this.tagName = tag; this.attrs = {}; this.listeners = {}; this.kids = {}; this.children = []; this.parent = null;
        this.matches = matches || []; this.className = ''; this._text = ''; this.elements = []; this.submitted = 0;
    }
    get textContent() { return this._text + this.children.map(c => c.textContent).join(''); }
    set textContent(v) { this._text = String(v); this.children = []; }
    setAttribute(k, v) { this.attrs[k] = String(v); }
    getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
    hasAttribute(k) { return k in this.attrs; }
    removeAttribute(k) { delete this.attrs[k]; }
    addEventListener(t, fn, capture) { (this.listeners[t] = this.listeners[t] || []).push({ fn, capture: !!capture }); }
    querySelector(sel) { return this.kids[sel] || null; }
    querySelectorAll(sel) { return this.kids[sel] ? [].concat(this.kids[sel]) : []; }
    appendChild(c) { c.parent = this; this.children.push(c); return c; }
    closest(sel) { return this.matches.includes(sel) ? this : (this.parent ? this.parent.closest(sel) : null); }
    requestSubmit() { this.submitted++; submit(this); }
}
const docListeners = {}, winListeners = {};
function card(title) {
    const c = new El('form', ['.lq-settings-card']);
    const h = new El('h2'); h.textContent = title;
    c.kids['.lq-section-title'] = h;
    c.title = h;
    return c;
}
const speed = card('سرعة التشغيل');
speed.elements = [{ name: '_token', type: 'hidden', value: 't' }, { name: 'section', type: 'hidden', value: 'speed' },
                  { name: 'worker_concurrency', type: 'number', value: '5' }, { name: '', type: 'submit', value: '' }];
const sources = card('مصادر البحث الإضافية');
sources.elements = [{ name: 'expansion_enabled', type: 'checkbox', value: 'true', checked: true }];
const page = new El('div');
page.kids.form = [speed, sources];
const tabLink = new El('a', ['a[href]']);
tabLink.setAttribute('href', '/settings?tab=keys');
tabLink.href = 'http://x/settings?tab=keys';
// a submit: capture listeners on the page, the form's own listeners, then the document (bubble)
function submit(form) {
    let prevented = false, stopped = false;
    const ev = { target: form, currentTarget: form, get defaultPrevented() { return prevented; },
                 preventDefault() { prevented = true; }, stopPropagation() { stopped = true; } };
    (page.listeners.submit || []).filter(l => l.capture).forEach(l => l.fn(ev));
    if (!stopped) (form.listeners.submit || []).forEach(l => l.fn(ev));
    if (!stopped) (docListeners.submit || []).forEach(fn => fn(ev));
    return prevented;
}
function change(form, i, value) {
    const el = form.elements[i];
    if (el.type === 'checkbox') el.checked = value; else el.value = value;
    (form.listeners.input || []).forEach(l => l.fn({ target: el }));
}
function clickLink(link) {
    let prevented = false;
    const ev = { target: link, button: 0, preventDefault() { prevented = true; }, get defaultPrevented() { return prevented; } };
    (docListeners.click || []).forEach(fn => fn(ev));
    return prevented;
}
function unload() {
    const ev = { returnValue: undefined, preventDefault() {} };
    (winListeners.beforeunload || []).forEach(fn => fn(ev));
    return ev.returnValue === undefined ? null : ev.returnValue;
}
globalThis.window = globalThis;
globalThis.document = {
    querySelector: sel => (sel === '[data-settings-page]' ? page : null),
    createElement: t => new El(t),
    addEventListener: (t, fn) => { (docListeners[t] = docListeners[t] || []).push(fn); },
    getElementById: () => null
};
globalThis.addEventListener = (t, fn) => { (winListeners[t] = winListeners[t] || []).push(fn); };
globalThis.location = { pathname: '/settings', search: '?tab=advanced', hash: '', href: 'http://x/settings?tab=advanced' };
const asked = [];
let answer = false;
globalThis.confirm = t => { asked.push(t); return answer; };
"""


@NEEDS_NODE
def test_settings_mark_changed_cards_and_ask_before_losing_them():
    out = _node(SETTINGS_DOM + read(SETTINGS_JS) + r"""
const out = {};
const badge = c => c.title.children.find(b => b.hasAttribute('data-settings-dirty'));
out.state = window.LaqtaSettings.formState(speed);
out.clean = [unload(), clickLink(tabLink), asked.length];
change(speed, 2, '7');
out.badge = [badge(speed).textContent, badge(speed).hasAttribute('hidden'), badge(sources) === undefined];
out.submitNo = [submit(sources), asked.pop()];
out.linkNo = [clickLink(tabLink), asked.pop(), location.href];
out.unload = unload();
out.ownSave = [submit(speed), asked.length];                 // saving the changed card itself: no question
change(speed, 2, '5');                                       // back as it was: nothing left unsaved
out.reverted = [badge(speed).hasAttribute('hidden'), clickLink(tabLink), asked.length];
change(speed, 2, '8');
answer = true;
out.submitYes = [submit(sources), asked.length];
out.leaving = unload();                                       // the save is leaving the page: no browser question
console.log(JSON.stringify(out));
""")
    assert out["state"] == "worker_concurrency=5"               # hidden inputs and buttons are not part of a card
    assert out["clean"] == [None, False, 0]
    assert out["badge"] == ["ما انحفظ", False, True]
    prevented, question = out["submitNo"]
    assert prevented is True and question.startswith('في تغييرات ما انحفظت بـ"سرعة التشغيل"')
    prevented, question, href = out["linkNo"]
    assert prevented is True and 'بـ"سرعة التشغيل"' in question and href.endswith("?tab=advanced")
    assert out["unload"] == 'في تغييرات ما انحفظت بـ"سرعة التشغيل"'
    assert out["ownSave"] == [False, 0]
    assert out["reverted"] == [True, False, 0]
    assert out["submitYes"] == [False, 1]
    assert out["leaving"] is None


@NEEDS_NODE
def test_settings_dirty_guard_waits_for_the_pages_question():
    """With the layout's dialog (Laqta.ask, a Promise) the submit waits, then goes again after «كمّل بلا حفظ»."""
    out = _node(SETTINGS_DOM + r"""
const asks = [];
let reply = false;
globalThis.Laqta = { ask: o => { asks.push(o); return Promise.resolve(reply); }, toast: () => {} };
""" + read(SETTINGS_JS) + r"""
(async () => {
    change(speed, 2, '7');
    const first = [submit(sources), sources.submitted];
    await new Promise(r => setImmediate(r));
    const afterNo = sources.submitted;
    reply = true;
    const second = submit(sources);
    await new Promise(r => setImmediate(r));
    console.log(JSON.stringify({ first, afterNo, second, afterYes: sources.submitted,
                                 ask: asks[0] }));
})();
""")
    assert out["first"] == [True, 0] and out["afterNo"] == 0
    assert out["second"] is True and out["afterYes"] == 1
    assert out["ask"]["title"] == 'في تغييرات ما انحفظت بـ"سرعة التشغيل"'
    assert out["ask"]["confirmText"] == "كمّل بلا حفظ" and out["ask"]["cancelText"] == "لا، رجوع"


def test_a_settings_save_lands_on_its_card():
    php = read(CONTROLLER)
    assert "$anchor = self::anchorFor(self::field(request(), 'section'));" in php
    assert "($anchor !== '' ? '#' . $anchor : '')" in php
    views = "".join(read(p) for p in (VIEWS / "settings").glob("*.blade.php"))
    for anchor in ("speed", "sources", "advanced", "models", "processing", "auto-publish", "strict-lane"):
        assert f'id="lq-settings-{anchor}"' in views, anchor
        assert f"'{anchor}'" in php[php.index("function anchorFor"):php.index("private static function back")], anchor
    assert "id=\"lq-key-{{ $row['id'] }}\"" in views and "'lq-key-' . $section" in php
    # the reloads after a JSON save keep the place; the flash is a toast when the page lands on a card
    js = read(SETTINGS_JS)
    assert "window.location.reload()" in js and js.count("setTimeout(reloadKeepingPlace, 900)") == 2
    assert "data-settings-flash" in read(VIEWS / "dashboard" / "settings.blade.php")


# ---------------------------------------------------------------------------
# health.js: the eval-export error in Arabic, the raw text under «التفاصيل التقنية»
# ---------------------------------------------------------------------------

@NEEDS_NODE
def test_the_eval_export_error_reads_in_arabic():
    out = _node("globalThis.window = globalThis;\n" + read(HEALTH_JS) + r"""
const H = window.LaqtaHealth;
console.log(JSON.stringify({
    english: H.evalExportView({ ok: false, status: 500, data: { status: 'failed', error: 'SQLSTATE[HY000] Connection refused' } }),
    arabic: H.evalExportView({ ok: false, status: 500, data: { status: 'failed', error: 'ما في مراجعات لسا' } }),
    none: H.evalExportView(null),
    expired: H.evalExportView({ ok: false, status: 419, data: { error: 'CSRF token mismatch.' } })
}));
""")
    assert out["english"]["text"] == "ما قدرنا نجهّز مجموعة الاختبار: صار خطأ بالخادم."
    assert out["english"]["detail"] == "SQLSTATE[HY000] Connection refused" and out["english"]["tone"] == "danger"
    assert out["arabic"]["text"] == "ما في مراجعات لسا" and out["arabic"]["detail"] == ""
    assert out["none"]["text"] == "ما قدرنا نجهّز مجموعة الاختبار: الخادم ما ردّ." and out["none"]["detail"] == ""
    assert "انتهت صلاحية الصفحة" in out["expired"]["text"] and out["expired"]["detail"] == "CSRF token mismatch."
    view = read(VIEWS / "dashboard" / "diagnostics.blade.php")
    assert re.search(r'<details[^>]*data-health="eval-export-details" hidden>\s*<summary>التفاصيل التقنية</summary>', view)
    js = read(HEALTH_JS)
    assert "evalDetailText.textContent = view.detail || '';" in js and "setHidden(evalDetails, !view.detail);" in js


def test_the_fonts_ask_only_for_the_weights_in_use():
    layout = read(LAYOUT)
    assert "Readex+Pro:wght@400;500;600;700&" in layout and "300" not in layout.split("fonts.googleapis.com/css2?")[1][:120]


@NEEDS_NODE
def test_a_toast_can_carry_an_action_button():
    """Laqta.toast(message, { variant, action: { label, onClick } }) (the review page's «تراجع»): a button with class
    lq-toast__action; a click closes the toast and runs the callback."""
    out = _layout("const region = new El('region'); nodes['[data-lq-toasts]'] = region;", r"""
let undone = 0;
window.Laqta.toast('انرفض', { variant: 'success', action: { label: 'تراجع', onClick: () => { undone++; } } });
const el = region.children[0];
const action = el.children.find(c => c.className === 'lq-toast__action');
const before = [region.children.length, action.textContent, action.type];
action.listeners.click.forEach(fn => fn({}));
window.Laqta.toast('بلا زر', { variant: 'info' });
console.log(JSON.stringify({ before, undone, after: region.children.length,
                             plain: region.children[0].children.some(c => c.className === 'lq-toast__action') }));
""")
    assert out["before"] == [1, "تراجع", "button"]
    assert out["undone"] == 1 and out["after"] == 1 and out["plain"] is False
