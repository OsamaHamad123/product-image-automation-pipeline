"""«التنبيهات»: the notice center in the shell (public/js/notices.js, loaded by layouts/laqta.blade.php after layout.js).

* the bell sits next to «روح لـ…» in the sidebar and in «حسابي» on the phone, with an unseen-count badge, and opens a
  panel (a modal dialog, not a page);
* events come from the layout's one /api/batch-status poll (run started / finished / stopped / failed, new products
  for review, server approvals that failed) and from /api/system/attention (asked at most every 5 minutes, never while
  the tab is hidden; an event only when an item's key appears);
* newest first, at most 30, kept in localStorage per browser (blocked or broken storage: this page only);
* a reviewer never gets a link to the owner's places; Tab stays in the panel, Esc gives the focus back.
Node runs layout.js and notices.js unchanged against the fake DOM of test_laqta_site_ux.
"""

import json
import re

import pytest

from test_laqta_run import NODE
from test_laqta_site_ux import LAYOUT_DOM, _node
from test_laqta_ui import DASH, LAYOUT, LAYOUT_JS, read

NEEDS_NODE = pytest.mark.skipif(NODE is None, reason="node is not installed")
NOTICES_JS = DASH / "public" / "js" / "notices.js"
CSS = DASH / "public" / "css" / "laqta.css"

NOTICES_DOM = r"""
Object.defineProperty(El.prototype, 'firstChild', { get() { return this.children[0] || null; } });
document.dispatchEvent = ev => { (docListeners[ev.type] || []).forEach(fn => fn(ev)); return true; };
body.setAttribute('data-lq-review-url', '/catalog');
body.setAttribute('data-lq-home-url', '/');
body.setAttribute('data-lq-attention-url', '/api/system/attention');
let clock = 1700000000000;
Date.now = () => clock;
const store = {};
let storageBroken = false;
globalThis.localStorage = {
    getItem: k => { if (storageBroken) throw new Error('blocked'); return k in store ? store[k] : null; },
    setItem: (k, v) => { if (storageBroken) throw new Error('blocked'); store[k] = String(v); }
};
let attReply = { status: 'success', state: 'ok', items: [] };
globalThis.fetch = url => {
    fetches.push(url);
    const data = url.indexOf('attention') >= 0 ? attReply : reply;
    return Promise.resolve({ ok: true, status: 200, json: async () => data });
};
const runNamed = async name => {
    const i = timers.findIndex(t => t.fn.name === name);
    if (i < 0) return null;
    const t = timers.splice(i, 1)[0];
    t.fn(); await flush(); return t.ms;
};
const poll = async () => { await runNamed('poll'); };
const attFetches = () => fetches.filter(u => u.indexOf('attention') >= 0).length;

function child(name, sel, parent) {
    const el = new El(name, [sel]);
    el.parent = parent;
    parent.kids[sel] = el;
    return el;
}
const panel = new El('panel', ['[data-lq-notices]']);
panel.setAttribute('hidden', '');
nodes['[data-lq-notices]'] = panel;
const list = child('list', '[data-lq-notices-list]', panel);
const empty = child('empty', '[data-lq-notices-empty]', panel);
const readBtn = child('read', '[data-lq-notices-read]', panel);
const clearBtn = child('clear', '[data-lq-notices-clear]', panel);
const closeBtn = child('close', '[data-lq-notices-close]', panel);
readBtn.setAttribute('hidden', ''); clearBtn.setAttribute('hidden', '');
panel.querySelectorAll = () => [closeBtn, readBtn, clearBtn].filter(b => !b.hasAttribute('hidden'));
const bell = new El('bell', ['[data-lq-notices-open]']);
const sheetBell = new El('sheet-bell', ['[data-lq-notices-open]']);
sheetBell.parent = sheet;
const badge = new El('badge');
badge.setAttribute('hidden', '');
document.querySelectorAll = sel => sel === '[data-lq-notices-count]' ? [badge]
    : sel === '[data-lq-notices-open]' ? [bell, sheetBell] : [];
const items = () => list.children.map(li => {
    const text = li.children[1].children[0];
    return { cls: li.className, tag: text.name, text: text.textContent, href: text.getAttribute('href'),
             when: li.children[1].children[1].textContent };
});
const saved = () => JSON.parse(store['laqta.notices'] || 'null');
function press(k, shift) {
    const e = { key: k, shiftKey: !!shift, prevented: false, preventDefault() { this.prevented = true; }, stopPropagation() {} };
    (docListeners.keydown || []).forEach(fn => fn(e));
    return e.prevented;
}
"""


def _notices(setup: str, script: str):
    return _node("globalThis.window = globalThis;\n" + LAYOUT_DOM + NOTICES_DOM + setup + "\n" + read(LAYOUT_JS) + "\n"
                 + read(NOTICES_JS) + "\n" + script)


# ---------------------------------------------------------------------------
# markup
# ---------------------------------------------------------------------------

def test_the_bell_sits_next_to_goto_and_in_the_account_sheet():
    layout = read(LAYOUT)
    goto = layout.index('class="lq-goto-open"')
    bell = layout.index('class="lq-notices-open"')
    assert goto < bell < layout.index('<nav class="lq-nav"')
    sheet = layout[layout.index('<div class="lq-account-sheet"'):]
    sheet = sheet[:sheet.index("</div>")]
    assert "data-lq-notices-open" in sheet and "data-lq-notices-count" in sheet
    panel = layout[layout.index('<div class="lq-notices"'):]
    head = panel[:panel.index(">")]
    assert 'role="dialog"' in head and 'aria-modal="true"' in head and "hidden" in head
    for hook, text in (("data-lq-notices-read", "علّم الكل مقروء"), ("data-lq-notices-clear", "امسح")):
        assert re.search(hook + r"[^>]*>" + text + "<", panel), hook
    assert layout.count('aria-controls="lq-notices"') == 2
    assert "data-lq-attention-url" in layout and "data-lq-home-url" in layout
    # one commented block, tokens only (test_design_tokens / test_dark_mode_shell check the colours)
    css = read(CSS)
    assert css.count("/* «التنبيهات»") == 1 and ".lq-notices:not([hidden])" in css


# ---------------------------------------------------------------------------
# events from the batch-status poll
# ---------------------------------------------------------------------------

@NEEDS_NODE
def test_poll_transitions_become_events_newest_first():
    out = _notices("", r"""
(async () => {
    const out = {};
    await flush();                                                  // a running run, first seen: no event
    out.first = window.Laqta.notices.list().length;
    reply = { status: 'idle', is_running: false, total: 10, current: 10, ready_for_review: 3, approvals: { failed_open: 0 } };
    await poll();
    reply = { status: 'running', is_running: true, total: 5, current: 0, ready_for_review: 3, approvals: { failed_open: 0 } };
    await poll();
    reply = Object.assign({}, reply, { ready_for_review: 5 });
    await poll();
    reply = Object.assign({}, reply, { ready_for_review: 6 });      // adds up into the same unseen line
    await poll();
    reply = Object.assign({}, reply, { ready_for_review: 4 });      // reviewed: no event
    await poll();
    reply = Object.assign({}, reply, { approvals: { failed_open: 1 } });
    await poll();
    reply = { status: 'error', is_running: false, notice: 'SHEETS_UNAVAILABLE: x', ready_for_review: 4, approvals: { failed_open: 1 } };
    await poll();
    reply = { status: 'running', is_running: true, total: 5, current: 1, ready_for_review: 4, approvals: { failed_open: 1 } };
    await poll();
    reply = Object.assign({}, reply, { phase: 'stopping', current: 2 });
    await poll();
    reply = { status: 'idle', is_running: false, total: 5, current: 2, ready_for_review: 4, approvals: { failed_open: 1 } };
    await poll();
    out.events = window.Laqta.notices.list().map(e => [e.kind, e.tone, e.text, e.href]);
    out.badge = [badge.textContent, badge.hasAttribute('hidden'), bell.getAttribute('aria-label')];
    console.log(JSON.stringify(out));
})();
""")
    assert out["first"] == 0
    assert out["events"] == [
        ["run-stop", "warning", "وقف التشغيل قبل ما يخلص (2 من 5)", "/#run-current"],
        ["run-start", "info", "بلّش تشغيل جديد (5 منتجات)", "/#run-current"],
        ["run-error", "danger", "وقف التشغيل بعطل: تعذّر الاتصال بـ Google Sheet.", "/#run-current"],
        ["approval", "danger", "اعتماد ما زبط عالخادم: شوفه وأعده", "/catalog"],
        ["review", "info", "3 منتجات جديدة جاهزة للمراجعة", "/catalog"],
        ["run-start", "info", "بلّش تشغيل جديد (5 منتجات)", "/#run-current"],
        ["run-done", "success", "خلص التشغيل: 3 بانتظار مراجعتك", "/catalog"],
    ]
    assert out["badge"] == ["7", False, "التنبيهات: 7 جديد"]


@NEEDS_NODE
def test_the_list_keeps_30_and_survives_a_reload_and_blocked_storage():
    seed = json.dumps({"events": [{"id": str(i), "kind": "review", "tone": "info", "text": "قديم " + str(i), "href": "/catalog",
                                   "admin": False, "at": 1700000000000 - (i + 1) * 60000, "n": 1} for i in range(30)],
                       "seen": 1700000000000 - 10 * 60000 - 1, "read": 0, "base": {"run": "running"}, "keys": [],
                       "attAt": 1700000000000, "n": 30}, ensure_ascii=False)
    out = _notices("store['laqta.notices'] = " + json.dumps(seed) + ";", r"""
(async () => {
    const out = {};
    await flush();
    out.reload = [window.Laqta.notices.list().length, badge.textContent, attFetches()];   // 10 unseen; asked 0 s ago
    reply = { status: 'idle', is_running: false, total: 10, current: 10, ready_for_review: 0 };
    await poll();
    const s = saved();
    out.cap = [s.events.length, s.events[0].text, s.events[29].text];
    storageBroken = true;                                        // private window: this page only, no error
    reply = { status: 'running', is_running: true, total: 1, current: 0, ready_for_review: 0 };
    await poll();
    out.blocked = [window.Laqta.notices.list()[0].text, window.Laqta.notices.list().length, saved().events[0].text];
    console.log(JSON.stringify(out));
})();
""")
    assert out["reload"] == [30, "10", 0]
    assert out["cap"] == [30, "خلص التشغيل. ما في شي جديد للمراجعة", "قديم 28"]
    assert out["blocked"] == ["بلّش تشغيل جديد (منتج واحد)", 30, "خلص التشغيل. ما في شي جديد للمراجعة"]


@NEEDS_NODE
def test_broken_or_blocked_storage_from_the_start_still_works():
    for setup in ("store['laqta.notices'] = '{not json';", "storageBroken = true;"):
        out = _notices(setup, r"""
(async () => {
    await flush();
    reply = { status: 'idle', is_running: false, total: 10, current: 10, ready_for_review: 2 };
    await poll();
    console.log(JSON.stringify([window.Laqta.notices.list().map(e => e.text), badge.textContent]));
})();
""")
        assert out == [["خلص التشغيل: 2 بانتظار مراجعتك"], "1"], setup


# ---------------------------------------------------------------------------
# «شو بدو منك»: /api/system/attention
# ---------------------------------------------------------------------------

@NEEDS_NODE
def test_attention_is_asked_every_5_minutes_at_most_and_never_while_hidden():
    out = _notices(r"""
attReply = { status: 'success', state: 'attention', items: [
    { key: 'outbox', tone: 'danger', title: 'رابطين ما وصلوا للشيت', action: { label: 'تشغيل جديد', href: '/#run-new', admin: true } },
    { key: 'services', tone: 'danger', title: 'Gemini ما ردّ بآخر فحص للاتصالات', action: { href: '/system?tab=health&goto=services#x', admin: false } }] };
""", r"""
(async () => {
    const out = {};
    await flush();
    out.load = [attFetches(), timers.find(t => t.fn.name === 'checkAttention').ms];
    out.first = window.Laqta.notices.list().map(e => [e.text, e.tone, e.href]);
    clock += 60000;
    visible(true); await flush();                                      // back in view a minute later: not yet
    out.minute = attFetches();
    attReply = { status: 'success', state: 'attention', items: [
        { key: 'services', tone: 'danger', title: 'Gemini ما ردّ بآخر فحص للاتصالات', action: {} },
        { key: 'ops_gemini_down', tone: 'warning', title: 'Gemini ما عم يردّ', action: { href: '/system?tab=keys#lq-key-gemini', admin: true } }] };
    document.hidden = true;
    clock += 10 * 60000;
    while (await runNamed('checkAttention') !== null) {}               // hidden: the timers ask nothing
    out.hidden = attFetches();
    visible(true); await flush();
    out.shown = attFetches();
    out.added = window.Laqta.notices.list().map(e => e.text);
    attReply = { status: 'success', state: 'checking', pending: true, items: [] };   // search side not read yet
    clock += 5 * 60000;
    await runNamed('checkAttention');
    out.pending = [attFetches(), saved().keys];
    console.log(JSON.stringify(out));
})();
""")
    assert out["load"] == [1, 300000]
    assert out["first"] == [["رابطين ما وصلوا للشيت", "danger", "/#run-new"],
                            ["Gemini ما ردّ بآخر فحص للاتصالات", "danger", "/system?tab=health&goto=services#x"]]
    assert out["minute"] == 1
    assert out["hidden"] == 1
    assert out["shown"] == 2
    assert out["added"] == ["Gemini ما عم يردّ", "رابطين ما وصلوا للشيت", "Gemini ما ردّ بآخر فحص للاتصالات"]
    assert out["pending"] == [3, ["services", "ops_gemini_down"]]


# ---------------------------------------------------------------------------
# the panel: links by role, seen / read / clear, focus
# ---------------------------------------------------------------------------

PANEL_SEED = r"""
attReply = { status: 'success', state: 'attention', items: [
    { key: 'ops_serper_credit', tone: 'danger', title: 'رصيد Serper خلص', action: { href: '/system?tab=keys#lq-key-serper', admin: true } },
    { key: 'services', tone: 'danger', title: 'Serper ما ردّ', action: { href: '/system?tab=health&goto=services', admin: false } }] };
"""


@pytest.mark.parametrize("role", ["admin", "reviewer"])
@NEEDS_NODE
def test_a_reviewer_never_gets_a_link_to_the_owners_places(role):
    out = _notices(PANEL_SEED + "body.setAttribute('data-lq-role', '" + role + "');", r"""
(async () => {
    await flush();
    reply = { status: 'idle', is_running: false, total: 3, current: 3, ready_for_review: 3 };
    await poll();
    reply = { status: 'running', is_running: true, total: 3, current: 0, ready_for_review: 3 };
    await poll();
    clock += 5 * 60000;
    click(bell);
    console.log(JSON.stringify(items().map(i => [i.text, i.tag, i.href])));
})();
""")
    owner = role == "admin"
    assert out == [
        ["بلّش تشغيل جديد (3 منتجات)", "a" if owner else "span", "/#run-current" if owner else None],
        ["خلص التشغيل: 3 بانتظار مراجعتك", "a", "/catalog"],
        ["رصيد Serper خلص", "a" if owner else "span", "/system?tab=keys#lq-key-serper" if owner else None],
        ["Serper ما ردّ", "a", "/system?tab=health&goto=services"],
    ]


@NEEDS_NODE
def test_opening_marks_seen_and_the_dialog_keeps_the_focus():
    out = _notices(PANEL_SEED, r"""
(async () => {
    const out = {};
    await flush();
    clock += 5 * 60000;
    out.before = [badge.textContent, panel.hasAttribute('hidden')];
    click(bell);
    out.open = [panel.hasAttribute('hidden'), bell.getAttribute('aria-expanded'), document.activeElement === closeBtn,
                badge.hasAttribute('hidden'), window.Laqta.notices.unseen(), items().map(i => [i.cls, i.when])];
    out.tabLast = [(document.activeElement = clearBtn, press('Tab')), document.activeElement === closeBtn];
    out.shiftFirst = [press('Tab', true), document.activeElement === clearBtn];
    document.activeElement = outside;
    out.fromOutside = [press('Tab'), document.activeElement === closeBtn];
    click(readBtn);
    out.read = items().map(i => i.cls);
    out.escape = [press('Escape'), panel.hasAttribute('hidden'), document.activeElement === bell, bell.getAttribute('aria-expanded')];
    out.closedKeys = press('Tab');
    click(toggle);                                                  // the phone: «حسابي» → «التنبيهات»
    document.activeElement = sheetBell;
    click(sheetBell);
    out.fromSheet = [sheet.hasAttribute('hidden'), panel.hasAttribute('hidden')];
    click(clearBtn);
    out.cleared = [list.children.length, empty.hasAttribute('hidden'), readBtn.hasAttribute('hidden'), saved().events.length];
    press('Escape');
    out.backToAccount = document.activeElement === toggle;
    click(bell);
    click(outside);                                                 // a click outside closes it
    out.outside = panel.hasAttribute('hidden');
    console.log(JSON.stringify(out));
})();
""")
    assert out["before"] == ["2", True]
    assert out["open"] == [False, "true", True, True, 0,
                           [["lq-notices__item lq-notices__item--danger is-new", "قبل 5 دقايق"]] * 2]
    assert out["tabLast"] == [True, True]
    assert out["shiftFirst"] == [True, True]
    assert out["fromOutside"] == [True, True]
    assert out["read"] == ["lq-notices__item lq-notices__item--danger"] * 2
    assert out["escape"] == [True, True, True, "false"]
    assert out["closedKeys"] is False
    assert out["fromSheet"] == [True, False]
    assert out["cleared"] == [0, False, True, 0]
    assert out["backToAccount"] is True
    assert out["outside"] is True
