"""Accessibility pass (screen readers, keyboard, ARIA) over the shell and the review screen.

* behind an open aria-modal dialog the page is inert (layout.js syncModals): the siblings of the dialog and of its
  parents up to <body>, but not the scripts or the toast region; only what it set is cleared, and the focus can go back
  as soon as the dialog closes (Laqta.ask, «التنبيهات», «روح لـ…» and the review dialogs call it before refocusing);
* the review keys (S, X, Enter, 1–9…) do nothing while a dialog from outside the review screen is open («التنبيهات»,
  «حسابي», «روح لـ…»); a closed (hidden) one does not count;
* names: the picked image is named after the product (not a file name), each bulk card says the product, its state and
  «محددة», and its «اعتماد» / «افتح» name the product; the lightbox is labelled by its visible title;
* a new notice is said once in a polite status (not at load, not from another tab), and «جديد» is a word, not a colour;
* the run panel says its progress once per quarter; the sidebar run card's landmark name is not the run panel's;
* «روح لـ…»'s groups are divs (role="group" is not allowed on <li>); the setup steps keep a name on the phone, where
  only their number is shown; the cutout image rows that scroll sideways take the focus.
"""

import json

import pytest

from laqta_review_harness import NODE, run
from test_laqta_site_ux import _layout
from test_laqta_ui import DASH, LAYOUT_JS, read
from test_notice_center import _notices

NEEDS_NODE = pytest.mark.skipif(NODE is None, reason="node is not installed")
JS = DASH / "public" / "js"
VIEWS = DASH / "resources" / "views"


# ---------------------------------------------------------------------------
# The background of an open dialog is inert
# ---------------------------------------------------------------------------

TREE = r"""
const all = () => { const out = []; const walk = n => n.children.forEach(c => { out.push(c); walk(c); }); walk(body); return out; };
const bySel = sel => { const m = /^\[([\w-]+)(?:="([^"]*)")?\]$/.exec(sel);
                       return m ? all().filter(n => m[2] === undefined ? n.hasAttribute(m[1]) : n.getAttribute(m[1]) === m[2]) : []; };
document.querySelectorAll = bySel;
document.removeEventListener = (t, fn) => { docListeners[t] = (docListeners[t] || []).filter(f => f !== fn); };
document.contains = n => { for (; n; n = n.parent) if (n === body) return true; return false; };
El.prototype.closest = function (sel) {
    if (sel === '[hidden]') { for (let n = this; n; n = n.parent) if (n.hasAttribute('hidden')) return n; return null; }
    return this.matches.includes(sel) ? this : (this.parent ? this.parent.closest(sel) : null);
};
function mk(name, parent, tag) { const n = new El(name); n.tagName = tag || 'DIV'; parent.appendChild(n); return n; }
const skip = mk('skip', body, 'A');
const shell = mk('shell', body);
const sidebar = mk('sidebar', shell, 'ASIDE');
const panel = mk('notices', shell);
panel.setAttribute('role', 'dialog'); panel.setAttribute('aria-modal', 'true'); panel.setAttribute('hidden', '');
const main = mk('main', shell, 'MAIN');
const toasts = mk('toasts', body); toasts.setAttribute('data-lq-toasts', '');
const script = mk('script', body, 'SCRIPT');
const theirs = mk('theirs', body); theirs.setAttribute('inert', '');            // inert set by someone else
const inert = () => [skip, shell, sidebar, panel, main, toasts, script, theirs].filter(n => n.hasAttribute('inert')).map(n => n.name);
"""


@NEEDS_NODE
def test_an_open_modal_makes_the_rest_of_the_page_inert_and_closing_gives_it_back():
    out = _layout(TREE, r"""
const out = {};
out.closed = inert();
panel.removeAttribute('hidden');
window.Laqta.syncModals();
out.open = inert();
out.marked = all().filter(n => n.hasAttribute('data-lq-inert')).map(n => n.name);
panel.setAttribute('hidden', '');
window.Laqta.syncModals();
out.after = inert();
console.log(JSON.stringify(out));
""")
    assert out["closed"] == ["theirs"]
    assert out["open"] == ["skip", "sidebar", "main", "theirs"]        # not the toasts, not the script, not the shell
    assert out["marked"] == ["skip", "sidebar", "main"]
    assert out["after"] == ["theirs"]                                  # what someone else set stays


@NEEDS_NODE
def test_a_question_makes_the_page_inert_and_esc_gives_the_focus_back_to_a_live_page():
    out = _layout(TREE, r"""
(async () => {
    const out = {};
    const opener = mk('opener', main, 'BUTTON');
    opener.focus();
    const answer = window.Laqta.ask('نكمّل؟');
    out.open = inert();
    key('Escape');
    out.answer = await answer;
    out.after = inert();
    out.focusBack = document.activeElement === opener;
    console.log(JSON.stringify(out));
})();
""")
    assert "shell" in out["open"] and "skip" in out["open"] and "toasts" not in out["open"]
    assert out["answer"] is False and out["after"] == ["theirs"] and out["focusBack"] is True


def test_every_dialog_frees_the_page_before_it_gives_the_focus_back():
    notices, jump = read(JS / "notices.js"), read(JS / "jump.js")
    app, bulk = read(JS / "review" / "app.js"), read(JS / "review" / "bulk.js")
    close = notices[notices.index("function close(refocus)"):]
    assert close.index("syncModals") < close.index("back.focus()")
    jclose = jump[jump.index("function close(refocus)"):]
    assert jclose.index("syncModals") < jclose.index("opener.focus()")
    finish = app[app.index("dlg.finish = ok =>"):]
    assert finish.index("R.syncModals()") < finish.index("restoreFocus(opener)")
    lb = app[app.index("function closeLightbox(silent)"):]
    assert lb.index("R.syncModals()") < lb.index("restoreFocus(lb.opener)")
    bclose = bulk[bulk.index("function closeDialog()"):]
    assert bclose.index("R.syncModals()") < bclose.index("opener.focus()")
    layout = read(LAYOUT_JS)
    assert "MutationObserver" in layout and "attributeFilter: ['hidden', 'aria-modal']" in layout


# ---------------------------------------------------------------------------
# The review keys behind another dialog
# ---------------------------------------------------------------------------

EV = {"brand": True, "size": "match", "gtin": "match", "source_class": "uae_retailer", "tier": 1}
VLM = {"decision": "MATCH", "brand_match": "yes", "size_match": "yes", "variant_match": "yes", "view": "front_packshot"}


def product(row, name):
    cands = [{"image_url": f"https://www.carrefouruae.com/p{row}.jpg", "status": "preselected", "is_selected": 1,
              "title": f"p{row}.jpg", "reasons": [], "evidence": dict(EV), "vlm": VLM},
             {"image_url": f"https://www.carrefouruae.com/a{row}.jpg", "status": "eligible", "is_selected": 0,
              "title": f"{name} alt", "reasons": [], "evidence": dict(EV)}]
    return {"row_number": row, "product_name": name, "brand": "Almarai", "barcode": "", "sku_key": f"key-{row}",
            "size": "1L", "product_name_ar": "", "brand_ar": "", "category": "Dairy", "existing_image_link": "",
            "needs_review": True, "preselected": True, "curation_candidates": cands}


PRODUCTS = [product(40 + i, f"Card {i}") for i in range(4)]


def fixture():
    rows = [{"row_number": p["row_number"], "sku_key": p["sku_key"], "status": "ready_for_review", "failure_code": None,
             "product_name": p["product_name"], "brand": p["brand"], "updated_at": "2026-10-05 10:00:00"} for p in PRODUCTS]
    return {"products": PRODUCTS, "queue": {"status": "success", "ready_for_review": len(rows), "rows": rows}}


def page(script, tmp_path, config):
    return run(f"await boot({json.dumps(config)});\nawait flush();\n" + script, tmp_path, fixture())


@NEEDS_NODE
def test_review_keys_wait_while_an_outside_dialog_is_open(tmp_path):
    out = page(r"""
const row = () => S().byKey.get(S().openKey).product.row_number;
out.start = row();
const sheet = document.createElement('div');            // «التنبيهات» on the shell, outside #rvApp
sheet.setAttribute('aria-modal', 'true');
document.body.appendChild(sheet);
press('s');
press('x');
await flush();
out.behind = [row(), S().reasonsOpen === true];
sheet.setAttribute('hidden', '');                       // closed: the keys are the page's again
press('s');
await flush();
out.closed = row();
""", tmp_path, {"row": 40})
    assert out["start"] == 40
    assert out["behind"] == [40, False]
    assert out["closed"] == 41


@NEEDS_NODE
def test_the_picked_image_is_named_after_the_product_and_the_lightbox_by_its_title(tmp_path):
    out = page(r"""
out.alt = ws().querySelector('.rv-pick__img').getAttribute('alt');
press('z');
await flush();
const lb = document.getElementById('rvLightbox');
out.lb = [lb.getAttribute('aria-labelledby'), lb.getAttribute('aria-label'),
          document.getElementById('rvLbTitle').textContent];
""", tmp_path, {"row": 40})
    assert out["alt"] == "Card 0: الصورة المختارة"                     # not the source page title «p40.jpg»
    assert out["lb"] == ["rvLbTitle", None, "Card 0"]


@NEEDS_NODE
def test_bulk_cards_name_the_product_its_state_and_the_tick(tmp_path):
    out = page(r"""
const card = () => document.querySelectorAll('.rv-card').filter(c => c.getAttribute('data-key'))[0];
out.before = card().getAttribute('aria-label');
out.buttons = [card().querySelector('.rv-card__approve').getAttribute('aria-label'),
               card().querySelector('.rv-card__open').getAttribute('aria-label'),
               card().querySelector('input[type="checkbox"]').getAttribute('aria-label')];
card().querySelector('input[type="checkbox"]').click();
await flush();
out.after = card().getAttribute('aria-label');
""", tmp_path, {"mode": "bulk"})
    assert out["buttons"] == ["اعتماد «Card 0»", "افتح «Card 0»", "تحديد Card 0"]
    states = {out["before"], out["after"]}
    assert states == {"Card 0، مقترحة، محددة", "Card 0، مقترحة"}       # the tick flips, and the name follows


# ---------------------------------------------------------------------------
# Live regions
# ---------------------------------------------------------------------------

@NEEDS_NODE
def test_a_new_notice_is_said_once_and_new_is_a_word():
    out = _notices("", r"""
(async () => {
    const out = {};
    const say = () => { const n = body.children.find(c => c.hasAttribute('data-lq-notices-say')); return n ? n.textContent : null; };
    await flush();
    out.load = say();
    reply = { status: 'idle', is_running: false, total: 10, current: 10, ready_for_review: 3, approvals: { failed_open: 0 } };
    await poll();
    out.first = say();
    reply = Object.assign({}, reply, { ready_for_review: 5 });
    await poll();
    out.second = say();
    reply = Object.assign({}, reply, { ready_for_review: 6 });      // the same line grows: nothing new to say
    await poll();
    out.grown = say();
    click(bell);
    const li = list.children[0];
    out.word = li.children[1].children.map(c => c.className).includes('lq-sr-only')
        && li.children[1].children[li.children[1].children.length - 1].textContent;
    console.log(JSON.stringify(out));
})();
""")
    assert out["load"] is None
    assert out["first"] == "تنبيه جديد: خلص التشغيل: 3 بانتظار مراجعتك"
    assert out["second"].startswith("2 تنبيهات جديدة، آخرها: ")
    assert out["grown"] == out["second"]
    assert out["word"] == "، جديد"


def test_run_progress_is_said_per_quarter_and_the_run_card_has_its_own_name():
    index = read(VIEWS / "dashboard" / "index.blade.php")
    assert 'class="lq-sr-only" role="status" data-run="progress-say"' in index
    run_js = read(JS / "run.js")
    say = run_js[run_js.index("function sayProgress(p)"):]
    say = say[:say.index("function renderLive")]
    assert "/ 25" in say and "quarter <= before" in say and "before === null" in say
    card = read(VIEWS / "components" / "lq" / "run-card.blade.php")
    assert 'aria-label="حالة التشغيل"' in card and 'aria-label="التشغيل الحالي"' not in card


# ---------------------------------------------------------------------------
# Markup
# ---------------------------------------------------------------------------

def test_goto_groups_are_divs_and_setup_steps_keep_a_name():
    jump = read(JS / "jump.js")
    assert "el('div', 'lq-goto__section')" in jump and "el('li', 'lq-goto__section')" not in jump
    assert "list = el('div', 'lq-goto__list')" in jump
    setup = read(VIEWS / "dashboard" / "setup.blade.php")
    assert "aria-label=\"الخطوة {{ $step['n'] }}: {{ $step['title'] }}، {{ $step['label'] }}\"" in setup
    assert "tab.setAttribute('aria-label', 'الخطوة '" in read(JS / "setup.js")


def test_close_buttons_say_sakker_and_cutout_rows_that_scroll_take_the_focus():
    for path in (JS / "layout.js", JS / "review" / "app.js"):
        assert "'إغلاق'" not in read(path), path
    assert "close.setAttribute('aria-label', 'سكّر')" in read(JS / "layout.js")
    cutout = read(JS / "review" / "cutout.js")
    assert "className: 'cq-stages', role: 'group', 'aria-label': alt" in cutout
    marks = cutout[cutout.index("function markScrollers()"):]
    assert "row.scrollWidth > row.clientWidth" in marks and "setAttribute('tabindex', '0')" in marks
