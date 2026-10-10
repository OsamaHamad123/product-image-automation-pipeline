"""The shell, Health, Run and Home for a keyboard and a screen reader, with no extra step.

* the phone's «حسابي» sheet is aria-modal: Tab goes round its two buttons («روح لـ…» and «خروج»), Esc closes it and
  the focus goes back to «حسابي»; «روح لـ…» opened from it gets its focus back on «حسابي», never on a hidden button;
* Health's log tabs follow the ARIA tabs pattern (Home / End, the arrows by what is on screen in RTL), and every
  scroll to a card is smooth only when the system does not ask for less motion;
* Run's stop / fix-stuck / search-again questions are a short title and a sentence or two that still say what
  happens to the rows and that nothing published is touched;
* Home's funnel picture is named with its numbers, and the next-step status is drawn (and so read out) only when it
  changes, not on every poll;
* «روح لـ…» (jump.js): a listbox of options in named groups, «ما في شي» and the count outside it, the focus back where
  it was, and no key taken while another open aria-modal dialog is up.
"""

import re

import pytest

from test_laqta_run import COMMON_JS, HARNESS, HOME_JS, NODE, RUN_JS, _js
from test_laqta_site_ux import _layout, _node
from test_laqta_ui import DASH, LAYOUT, read

NEEDS_NODE = pytest.mark.skipif(NODE is None, reason="node is not installed")
JS = DASH / "public" / "js"
HEALTH_JS, JUMP_JS = JS / "health.js", JS / "jump.js"


# ---------------------------------------------------------------------------
# «حسابي»
# ---------------------------------------------------------------------------

def test_the_account_sheet_is_a_modal_dialog():
    layout = read(LAYOUT)
    sheet = layout[layout.index('<div class="lq-account-sheet"'):]
    sheet = sheet[:sheet.index(">")]
    assert 'role="dialog"' in sheet and 'aria-modal="true"' in sheet and 'aria-label="حسابي"' in sheet


ACCOUNT_SETUP = r"""
const goto = new El('goto');
goto.parent = sheet;
sheet.kids.button = goto;
sheet.querySelectorAll = sel => sel === 'button' ? [goto, logout] : [];
function press(k, shift) {
    const e = { key: k, shiftKey: !!shift, prevented: false, preventDefault() { this.prevented = true; }, stopPropagation() {} };
    (docListeners.keydown || []).forEach(fn => fn(e));
    return e.prevented;
}
"""


@NEEDS_NODE
def test_tab_stays_in_the_account_sheet_and_esc_gives_the_focus_back():
    out = _layout(ACCOUNT_SETUP, r"""
(async () => {
    const out = {};
    const at = () => document.activeElement === goto ? 'goto' : document.activeElement === logout ? 'logout'
        : document.activeElement === toggle ? 'toggle' : 'other';
    click(toggle);
    out.open = at();                                       // the first button
    out.tabMiddle = [press('Tab'), at()];                  // goto -> logout: the browser's own move
    document.activeElement = logout;
    out.tabLast = [press('Tab'), at()];                    // the last one goes round to the first
    out.shiftFirst = [press('Tab', true), at()];           // and back from the first to the last
    document.activeElement = outside;
    out.fromOutside = [press('Tab'), at()];                // the focus outside the sheet is brought in
    out.escape = [press('Escape'), at(), toggle.getAttribute('aria-expanded'), sheet.hasAttribute('hidden')];
    out.closedTab = press('Tab');                          // closed: Tab is the page's again
    click(toggle);
    document.activeElement = goto;
    window.Laqta.closeAccount();                           // «روح لـ…» from the sheet: the focus goes to «حسابي»
    out.closeAccount = [at(), sheet.hasAttribute('hidden')];
    console.log(JSON.stringify(out));
})();
""")
    assert out["open"] == "goto"
    assert out["tabMiddle"] == [False, "goto"]
    assert out["tabLast"] == [True, "goto"]
    assert out["shiftFirst"] == [True, "logout"]
    assert out["fromOutside"] == [True, "goto"]
    assert out["escape"][1:] == ["toggle", "false", True]
    assert out["closedTab"] is False
    assert out["closeAccount"] == ["toggle", True]


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@NEEDS_NODE
def test_the_log_tabs_follow_the_tabs_pattern_in_rtl():
    out = _node(_js(HEALTH_JS) + r"""
const s = window.LaqtaHealth.tabStep;
console.log(JSON.stringify({
    rtlLeft: s('ArrowLeft', 0, 3, true), rtlRight: s('ArrowRight', 0, 3, true), rtlLeftEnd: s('ArrowLeft', 2, 3, true),
    ltrRight: s('ArrowRight', 0, 3, false), ltrLeft: s('ArrowLeft', 0, 3, false),
    home: s('Home', 2, 3, true), end: s('End', 0, 3, true), other: s('Enter', 1, 3, true), none: s('Home', 0, 0, true),
}));
""")
    assert out == {"rtlLeft": 1, "rtlRight": 2, "rtlLeftEnd": 0, "ltrRight": 1, "ltrLeft": 2,
                   "home": 0, "end": 2, "other": -1, "none": -1}
    js = read(HEALTH_JS)
    keys = js[js.index("tabs[t].addEventListener('keydown'"):]
    keys = keys[:keys.index("});")]
    assert "tabStep(e.key" in keys and "getAttribute('dir')" in keys


def test_every_scroll_to_a_card_respects_reduced_motion():
    js = read(HEALTH_JS)
    assert js.count("prefers-reduced-motion: reduce") == 1
    assert "behavior: 'smooth'" not in js
    helper = js[js.index("function scrollToCard"):]
    helper = helper[:helper.index("\n    }\n")]
    assert "behavior: still ? 'auto' : 'smooth'" in helper
    calls = [m.start() for m in re.finditer(r"\.scrollIntoView\(", js)]
    assert len(calls) == 1 and js.index("function scrollToCard") < calls[0]       # one place scrolls
    go = js[js.index("function goTo(where)"):]
    go = go[:go.index("\n    }\n")]
    assert "scrollToCard(card)" in go


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

@NEEDS_NODE
def test_the_run_questions_are_short_and_still_say_what_is_kept():
    out = _node(_js(COMMON_JS, RUN_JS) + r"""
const P = LaqtaRunPage;
console.log(JSON.stringify({ stop: P.STOP_CONFIRM_TEXT, reset: P.RESET_CONFIRM_TEXT, force: P.FORCE_CONFIRM_TEXT }));
""")
    for name, q in out.items():
        assert q["title"].endswith("؟"), name
        assert len(q["text"]) <= 200 and q["text"].count(".") <= 2 and "•" not in q["text"] and "\n" not in q["text"], name
        assert q["confirmText"] and q["confirmText"] != "أكيد", name
    assert out["stop"]["danger"] is True and out["reset"]["danger"] is True and not out["force"].get("danger")
    assert "بترجع تستنى" in out["stop"]["text"] and "ما في ولا صف بينمسح" in out["stop"]["text"]
    assert "المنشور ما بينلمس" in out["stop"]["text"]
    assert "بترجع تستنى" in out["reset"]["text"] and "قرار مراجعة" in out["reset"]["text"]
    assert "الصور المنشورة بتضل مكانها" in out["force"]["text"] and "اعتمدها مراجع" in out["force"]["text"]


@NEEDS_NODE
def test_without_the_layout_the_run_question_is_plain_text_for_window_confirm():
    js = read(RUN_JS)
    wrapper = js[js.index("confirm: function (q)"):]
    wrapper = wrapper[:wrapper.index("},")]
    assert "q.title + '\\n' + q.text" in wrapper and "root.Laqta.ask" in wrapper


# ---------------------------------------------------------------------------
# Home
# ---------------------------------------------------------------------------

@NEEDS_NODE
def test_the_funnel_picture_is_named_with_its_numbers():
    out = _node(_js(COMMON_JS, HOME_JS) + r"""
const ov = LaqtaHomePage.describeOverview({ ok: true, status: 200, data: { status: 'success', kpis: {},
    sheet: { status: 'ok', total: 170, stages: [{ label: 'منشورة', value: 120, tone: 'teal' },
        { label: 'بانتظار المراجعة', value: 38, tone: 'amber' }, { label: 'ما انلقت', value: 12, tone: 'muted' }] } } }, 1790000000);
console.log(JSON.stringify(ov.funnel));
""")
    assert out["label"] == "وين وصلت منتجات الشيت (170 منتج): منشورة 120، بانتظار المراجعة 38، ما انلقت 12"
    js = read(HOME_JS)
    assert "bar.setAttribute('aria-label', f.label)" in js


@NEEDS_NODE
def test_the_next_step_status_is_drawn_only_when_it_changes():
    out = _node(_js(COMMON_JS, HOME_JS) + HARNESS + r"""
const nexts = [];
deps.renderNext = v => nexts.push(v && v.title);
lives = [{ status: 'success', batch: { phase: 'review', ready_for_review: 5, run: {} }, run: null },
         { status: 'success', batch: { phase: 'review', ready_for_review: 5, run: {} }, run: null },
         { status: 'success', batch: { phase: 'review', ready_for_review: 5, run: {} }, run: null },
         { status: 'success', batch: { phase: 'review', ready_for_review: 4, run: {} }, run: null }];
const ctl = LaqtaHomePage.createController(deps);
(async () => {
    await ctl.poll();
    await ctl.poll();
    await ctl.poll();
    const same = nexts.length;
    await ctl.poll();
    console.log(JSON.stringify({ same, nexts }));
})();
""")
    assert out["same"] == 1                                 # three polls, one step: drawn (and read out) once
    assert out["nexts"] == ["5 صور جاهزة للمراجعة.", "4 صور جاهزة للمراجعة."]


# ---------------------------------------------------------------------------
# «روح لـ…»
# ---------------------------------------------------------------------------

JUMP_DOM = r"""
const listeners = {};
class N {
    constructor(tag) { this.tagName = String(tag).toUpperCase(); this.children = []; this.attrs = {}; this.listeners = {};
        this.style = {}; this.parentNode = null; this.text = ''; this.hidden = false; this.className = ''; this.id = '';
        this.value = ''; }
    get firstChild() { return this.children[0] || null; }
    set textContent(t) { this.text = String(t); this.children = []; }
    get textContent() { return this.text + this.children.map(c => c.textContent).join(''); }
    setAttribute(k, v) { this.attrs[k] = String(v); }
    getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
    hasAttribute(k) { return k in this.attrs; }
    removeAttribute(k) { delete this.attrs[k]; }
    appendChild(c) { c.parentNode = this; this.children.push(c); return c; }
    removeChild(c) { this.children = this.children.filter(x => x !== c); c.parentNode = null; return c; }
    addEventListener(t, fn) { (this.listeners[t] = this.listeners[t] || []).push(fn); }
    focus() { document.activeElement = this; }
    all() { return this.children.flatMap(c => [c, ...(c.all ? c.all() : [])]); }
    is(sel) {
        if (sel === '[hidden]') return this.hidden || this.hasAttribute('hidden');
        if (sel === '[aria-modal="true"]') return this.attrs['aria-modal'] === 'true';
        if (sel.startsWith('.')) return this.className.split(' ').includes(sel.slice(1));
        return false;
    }
    querySelectorAll(sel) { return this.all().filter(n => n.is && n.is(sel)); }
    closest(sel) { for (let n = this; n && n.is; n = n.parentNode) if (n.is(sel)) return n; return null; }
}
const body = new N('body');
let index = [{ title: 'الرئيسية', group: 'صفحات', href: '/', words: '' },
             { title: 'التشغيل', group: 'صفحات', href: '/batch-automation', words: '' },
             { title: 'مفتاح Serper', group: 'الإعدادات', href: '/settings?tab=keys#lq-key-serper', words: '' }];
globalThis.document = {
    body, activeElement: null,
    createElement: t => new N(t), createTextNode: t => ({ textContent: t }),
    getElementById: id => id === 'lqGotoIndex' ? { textContent: JSON.stringify(index) } : null,
    addEventListener(t, fn) { (listeners[t] = listeners[t] || []).push(fn); },
    removeEventListener(t, fn) { listeners[t] = (listeners[t] || []).filter(f => f !== fn); },
    querySelector: () => null, querySelectorAll: sel => body.querySelectorAll(sel),
    contains: n => { for (; n; n = n.parentNode) if (n === body) return true; return false; },
};
globalThis.window = globalThis;
""" + "%JUMP%" + r"""
function key(props) {
    const e = Object.assign({ key: '', code: '', ctrlKey: false, metaKey: false, altKey: false, shiftKey: false, repeat: false,
        defaultPrevented: false, target: body, preventDefault() { this.defaultPrevented = true; }, stopPropagation() {} }, props);
    (listeners.keydown || []).slice().forEach(fn => fn(e));
    return e;
}
const G = window.LaqtaGoto;
const box = () => body.children.find(c => c.className === 'lq-goto-backdrop') || null;
const find = cls => box() ? box().querySelectorAll('.' + cls)[0] || null : null;
const said = () => box().querySelectorAll('.lq-sr-only').find(n => n.getAttribute('role') === 'status');
const opener = new N('button');
body.appendChild(opener);
"""


def _jump(script: str):
    return _node(JUMP_DOM.replace("%JUMP%", read(JUMP_JS)) + script)


@NEEDS_NODE
def test_goto_is_a_listbox_of_options_in_named_groups():
    out = _jump(r"""
opener.focus();
G.open();
const list = find('lq-goto__list'), input = find('lq-goto__input');
const kids = list.children.map(c => [c.tagName, c.getAttribute('role'), c.getAttribute('aria-label')]);
const groups = list.children.map(g => {
    const head = g.children[0], opts = g.children[1];
    return { head: [head.getAttribute('aria-hidden'), head.textContent], wrap: opts.getAttribute('role'),
             options: opts.children.map(o => o.getAttribute('role')) };
});
const options = list.querySelectorAll('.lq-goto__item');
const first = { active: input.getAttribute('aria-activedescendant'), id: options[0].id,
                selected: options.map(o => o.getAttribute('aria-selected')) };
key({ key: 'ArrowDown' });
const moved = { active: input.getAttribute('aria-activedescendant'), id: options[1].id };
console.log(JSON.stringify({ kids, groups, first, moved, expanded: input.getAttribute('aria-expanded'),
    role: input.getAttribute('role'), controls: input.getAttribute('aria-controls'), listId: list.id,
    status: said().getAttribute('role') + ':' + said().textContent,
    emptyHidden: find('lq-goto__empty').hidden, modal: box().children[0].getAttribute('aria-modal'),
    presentation: list.all().filter(n => n.getAttribute && n.getAttribute('role') === 'presentation').length }));
""")
    assert out["kids"] == [["LI", "group", "صفحات"], ["LI", "group", "الإعدادات"]]
    assert out["groups"][0] == {"head": ["true", "صفحات"], "wrap": "none", "options": ["option", "option"]}
    assert out["groups"][1]["options"] == ["option"]
    assert out["presentation"] == 0                          # no header item inside the listbox
    assert out["first"]["active"] == out["first"]["id"] and out["first"]["selected"] == ["true", "false", "false"]
    assert out["moved"]["active"] == out["moved"]["id"]
    assert out["role"] == "combobox" and out["expanded"] == "true" and out["controls"] == out["listId"]
    assert out["status"] == "status:3 نتائج" and out["emptyHidden"] is True and out["modal"] == "true"


@NEEDS_NODE
def test_goto_says_nothing_found_outside_the_listbox():
    out = _jump(r"""
index = [];
G.open();
const list = find('lq-goto__list'), input = find('lq-goto__input');
console.log(JSON.stringify({ kids: list.children.length, listHidden: list.hidden, emptyHidden: find('lq-goto__empty').hidden,
    expanded: input.getAttribute('aria-expanded'), active: input.getAttribute('aria-activedescendant'),
    status: said().textContent }));
""")
    assert out == {"kids": 0, "listHidden": True, "emptyHidden": False, "expanded": "false", "active": None,
                   "status": "ما في شي بهالاسم."}


@NEEDS_NODE
def test_goto_gives_the_focus_back_but_never_to_a_hidden_button():
    out = _jump(r"""
opener.focus();
G.open();
const inside = document.activeElement === find('lq-goto__input');
key({ key: 'Escape' });
const back = document.activeElement === opener;
const sheet = new N('div');
sheet.hidden = true;
const hiddenBtn = new N('button');
sheet.appendChild(hiddenBtn);
body.appendChild(sheet);
hiddenBtn.focus();
G.open();
key({ key: 'Escape' });
console.log(JSON.stringify({ inside, back, closed: box() === null, notHidden: document.activeElement !== hiddenBtn }));
""")
    assert out == {"inside": True, "back": True, "closed": True, "notHidden": True}


@NEEDS_NODE
def test_goto_never_takes_keys_from_an_open_modal_dialog():
    out = _jump(r"""
const out = {};
const sheet = new N('div');                     // the «حسابي» sheet: aria-modal, closed (hidden)
sheet.setAttribute('aria-modal', 'true');
sheet.hidden = true;
body.appendChild(sheet);
out.hiddenModal = key({ key: 'k', code: 'KeyK', ctrlKey: true }).defaultPrevented && box() !== null;
G.close(false);
const ask = new N('div');                       // a question (Laqta.ask) on the page
ask.setAttribute('aria-modal', 'true');
body.appendChild(ask);
out.ctrlK = [key({ key: 'k', code: 'KeyK', ctrlKey: true }).defaultPrevented, box() !== null];
out.slash = [key({ key: '/', code: 'Slash' }).defaultPrevented, box() !== null];
body.removeChild(ask);
G.open();                                       // the box is open, then a question comes over it
body.appendChild(ask);
out.overBox = [key({ key: 'Enter' }).defaultPrevented, key({ key: 'Escape' }).defaultPrevented, box() !== null];
body.removeChild(ask);
out.after = [key({ key: 'Escape' }).defaultPrevented, box() === null];
console.log(JSON.stringify(out));
""")
    assert out["hiddenModal"] is True                       # a closed modal does not block Ctrl+K
    assert out["ctrlK"] == [False, False] and out["slash"] == [False, False]
    assert out["overBox"] == [False, False, True]           # the question's Enter / Esc stay the question's
    assert out["after"] == [True, True]
