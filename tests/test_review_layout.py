"""Review screen layout (phase 1 of the redesign): the phone order, the folding list, the bulk grid and toolbar.

* phone (≤ 980px): the queue head (title and count, mode toggle, search, chips) comes BEFORE the workspace, in the DOM
  too (app.js placeQueueHead), and the list after it folds behind «القائمة (N)» (aria-expanded, closed by default,
  remembered for the session, its own capped scroll); a wide screen keeps the head and the list in the side column;
* bulk on a phone: two cards a row with a shorter picture; on touch the first warning and «+N تفاصيل», the rest behind it;
* the bulk toolbar: «تصدير سجل الصور (CSV)» in a «⋯» menu beside the chips, no keyboard-hint line (the «?» button);
* «فحص القص» in single mode's «⋯» menu (admins), not in the queue head;
* review.css: the headings on the page-title token, no raw font sizes, no 40px controls.
"""

import json
import re
from pathlib import Path

import pytest

from laqta_review_harness import NODE, run

ROOT = Path(__file__).resolve().parents[1]
CSS = ROOT / "dashboard" / "public" / "css" / "pages" / "review.css"
NEEDS_NODE = pytest.mark.skipif(NODE is None, reason="node is not installed")

EV = {"brand": True, "size": "match", "gtin": "match", "source_class": "uae_retailer", "tier": 1}
VLM = {"decision": "MATCH", "brand_match": "yes", "size_match": "yes", "variant_match": "yes", "view": "front_packshot"}


def js(value):
    return json.dumps(value, ensure_ascii=False)


def product(row, name, warns=()):
    cands = [{"image_url": f"https://www.carrefouruae.com/p{row}.jpg", "status": "preselected", "is_selected": 1,
              "title": name, "reasons": [f"warn:{w}" for w in warns], "evidence": dict(EV), "vlm": VLM}]
    return {"row_number": row, "product_name": name, "brand": "Almarai", "barcode": "", "sku_key": f"key-{row}",
            "size": "1L", "product_name_ar": "", "brand_ar": "", "category": "Dairy", "existing_image_link": "",
            "needs_review": True, "preselected": True, "curation_candidates": cands}


def fixture(products):
    rows = [{"row_number": p["row_number"], "sku_key": p["sku_key"], "status": "ready_for_review", "failure_code": None,
             "product_name": p["product_name"], "brand": p["brand"], "updated_at": "2026-10-05 10:00:00"} for p in products]
    return {"products": products, "queue": {"status": "success", "ready_for_review": len(rows), "rows": rows}}


FOUR = [product(10, "Milk A"), product(11, "Milk B"), product(12, "Milk C"), product(13, "Milk D")]

PHONE = "globalThis.matchMedia = q => ({ matches: /max-width: (980|560)px/.test(q) });\n"
WIDE = "globalThis.matchMedia = q => ({ matches: false });\n"


def session(store=None):
    return ("const store = %s;\nglobalThis.sessionStorage = { getItem: k => (k in store ? store[k] : null), "
            "setItem: (k, v) => { store[k] = String(v); } };\n" % js(store or {}))


def page(scenario, tmp_path, fx, config=None, before=""):
    return run(before + f"await boot({js(config or {})});\n" + scenario, tmp_path, fx)


# ---------------------------------------------------------------------------
# CSS
# ---------------------------------------------------------------------------

def media_block(css, query):
    """The body of the first `@media <query> {…}` (brace matched)."""
    start = css.index("@media " + query + " {")
    i = css.index("{", start) + 1
    depth = 1
    j = i
    while depth:
        if css[j] == "{":
            depth += 1
        elif css[j] == "}":
            depth -= 1
        j += 1
    return css[i:j - 1]


def rule(block, selector):
    m = re.search(r"(?:^|[\s,}])" + re.escape(selector) + r"\s*(?:,[^{]*)?\{([^}]*)\}", block)
    assert m, selector
    return m.group(1)


def test_the_phone_layout_folds_the_list_and_caps_its_scroll():
    css = CSS.read_text(encoding="utf-8")
    phone = media_block(css, "(max-width: 980px)")
    assert ".rv-single__head" in phone
    assert "display: none" in rule(phone, ".rv-single.is-list-closed .rv-queue__listbox")
    lst = rule(phone, ".rv-list")
    assert "max-height: min(60vh, 520px)" in lst and "overflow-y: auto" in lst and "max-height: none" not in phone
    assert "display: flex" in rule(phone, ".rv-listtoggle")
    # wider than 980px: the toggle never shows and the head slot stays empty and hidden
    outside = css.replace(phone, "")
    assert "display: none" in rule(outside, ".rv-listtoggle")
    assert "display: none" in rule(outside, ".rv-single__head[hidden]")


def test_bulk_cards_are_two_a_row_on_a_phone_with_a_shorter_picture():
    css = CSS.read_text(encoding="utf-8")
    small = media_block(css, "(max-width: 560px)")
    cards = re.findall(r"\.rv-cards\s*\{([^}]*)\}", small)
    assert any("repeat(auto-fill, minmax(150px, 1fr))" in c for c in cards)
    assert not any("grid-template-columns: minmax(0, 1fr)" in c for c in cards)
    assert "height: 140px" in rule(small, ".rv-card__img")
    touch = media_block(css, "(hover: none), (max-width: 560px)")
    assert ".rv-card:not(.is-details) .rv-card__warn--extra" in touch and ".rv-card:not(.is-details) .rv-explain" in touch


def test_review_css_uses_the_type_scale_and_the_standard_heights():
    css = CSS.read_text(encoding="utf-8")
    raw = [m for m in re.findall(r"font-size:\s*([^;]+);", css) if not re.fullmatch(r"var\(--lq-fs-[a-z0-9]+\)", m.strip())]
    assert not raw, raw
    assert "font-size: var(--lq-fs-2xl)" in rule(css, ".rv-queue__h1")
    assert "font-size: var(--lq-fs-2xl)" in rule(css, ".rv-bulk__h1")
    assert "height: 40px" not in css
    assert ".rv-bulk__keys" not in css


# ---------------------------------------------------------------------------
# The phone order and the folding list (node harness)
# ---------------------------------------------------------------------------

ORDER = r"""
const single = document.querySelector('.rv-single');
out.order = single.children.map(c => c.className);
const slot = document.querySelector('.rv-single__head');
out.slot_hidden = slot.hidden;
out.head_in_slot = !!slot.querySelector('.rv-queue__head');
out.head_parent = document.querySelector('.rv-queue__head').parentNode.className;
const head = document.querySelector('.rv-queue__head');
out.head_has = ['.rv-queue__h1', '#rvWaiting', '.rv-modes', '#rvSearch', '.rv-filters'].map(s => !!head.querySelector(s));
const t = document.getElementById('rvListToggle');
out.toggle = [t.getAttribute('aria-expanded'), t.getAttribute('aria-controls'), t.textContent];
out.closed = single.classList.contains('is-list-closed');
out.list_in_box = document.getElementById('rvList').parentNode.id;
"""


@NEEDS_NODE
def test_on_a_phone_the_head_comes_before_the_workspace_and_the_list_after_it(tmp_path):
    out = page(ORDER, tmp_path, fixture(FOUR), before=PHONE + session())
    assert out["order"] == ["rv-single__head", "rv-ws", "rv-queue"]
    assert out["slot_hidden"] is False and out["head_in_slot"] is True and out["head_parent"] == "rv-single__head"
    assert all(out["head_has"])
    assert out["toggle"][:2] == ["false", "rvListBox"] and out["toggle"][2] == "القائمة (4)"
    assert out["closed"] is True and out["list_in_box"] == "rvListBox"


@NEEDS_NODE
def test_a_wide_screen_keeps_the_head_and_the_list_in_the_side_column(tmp_path):
    out = page(ORDER, tmp_path, fixture(FOUR), before=WIDE + session())
    assert out["slot_hidden"] is True and out["head_in_slot"] is False and out["head_parent"] == "rv-queue"
    queue = r"out.first = document.querySelector('.rv-queue').children[0].className;"
    first = page(queue, tmp_path, fixture(FOUR), before=WIDE + session())
    assert first["first"] == "rv-queue__head"


@NEEDS_NODE
def test_the_list_toggle_opens_and_closes_and_is_remembered_for_the_session(tmp_path):
    out = page(r"""
const t = document.getElementById('rvListToggle');
const single = document.querySelector('.rv-single');
t.focus();
t.click();
out.opened = [t.getAttribute('aria-expanded'), single.classList.contains('is-list-closed'), store['laqta.review.listOpen']];
// the focus inside the list when it folds goes back to the toggle, never to body
document.querySelector('.rv-item').focus();
t.click();
out.closed = [t.getAttribute('aria-expanded'), single.classList.contains('is-list-closed'), store['laqta.review.listOpen']];
out.focus = document.activeElement && document.activeElement.id;
""", tmp_path, fixture(FOUR), before=PHONE + session())
    assert out["opened"] == ["true", False, "1"]
    assert out["closed"] == ["false", True, "0"]

    again = page(r"""
out.state = [document.getElementById('rvListToggle').getAttribute('aria-expanded'),
             document.querySelector('.rv-single').classList.contains('is-list-closed')];
""", tmp_path, fixture(FOUR), before=PHONE + session({"laqta.review.listOpen": "1"}))
    assert again["state"] == ["true", False]


@NEEDS_NODE
def test_the_folded_list_focus_goes_back_to_its_toggle(tmp_path):
    out = page(r"""
const t = document.getElementById('rvListToggle');
document.querySelector('.rv-item').focus();
R.setListOpen(false);
out.focus = document.activeElement && document.activeElement.id;
""", tmp_path, fixture(FOUR), before=PHONE + session({"laqta.review.listOpen": "1"}))
    assert out["focus"] == "rvListToggle"


@NEEDS_NODE
def test_searching_on_a_phone_opens_the_folded_list(tmp_path):
    out = page(r"""
const s = document.getElementById('rvSearch');
s.value = 'Milk C';
dispatch(s, { type: 'input', bubbles: true });
await sleep(150);
await flush();
out.state = [document.getElementById('rvListToggle').getAttribute('aria-expanded'),
             document.getElementById('rvListToggle').textContent];
""", tmp_path, fixture(FOUR), before=PHONE + session())
    assert out["state"] == ["true", "القائمة (1)"]


# ---------------------------------------------------------------------------
# «فحص القص» in the «⋯» menu
# ---------------------------------------------------------------------------

@NEEDS_NODE
def test_the_cutout_check_is_a_mode_not_a_link_in_the_head_or_the_menu(tmp_path):
    # «فحص القص» is the third item of the mode toggle (tests/test_review_cutout_mode.py): no second way in
    scenario = r"""
out.in_head = document.querySelector('.rv-queue__head').querySelectorAll('a').length;
out.in_menu = document.getElementById('rvMoreMenu').querySelectorAll('a').length;
"""
    admin = page(scenario, tmp_path, fixture(FOUR), config={"urls": {"cutoutCheck": "/catalog?mode=cutout"}}, before=WIDE)
    assert admin == {"in_head": 0, "in_menu": 0}


# ---------------------------------------------------------------------------
# Bulk: the toolbar menu and the touch details
# ---------------------------------------------------------------------------

@NEEDS_NODE
def test_the_bulk_export_lives_in_a_menu_and_the_keys_line_is_gone(tmp_path):
    out = page(r"""
R.setMode('bulk');
await flush();
const menu = document.getElementById('rvBulkMenu');
const btn = document.getElementById('rvBulkMenuBtn');
const a = document.querySelector('.rv-bulk__export');
out.export_in_menu = menu.contains(a);
out.role = a.getAttribute('role');
out.tools_has_menu = document.querySelector('.rv-bulk__tools').contains(btn);
out.keys_line = !!document.querySelector('.rv-bulk__keys');
out.keys_btn = !!document.querySelector('.rv-bulk__head .rv-keys-btn');
out.closed = [menu.hidden, btn.getAttribute('aria-expanded'), btn.getAttribute('aria-haspopup'), btn.getAttribute('aria-controls')];
btn.click();
out.open = [menu.hidden, btn.getAttribute('aria-expanded'), document.activeElement === a];
press('Escape');
out.esc = [menu.hidden, btn.getAttribute('aria-expanded'), document.activeElement === btn];
btn.click();
document.body.click();
out.outside = menu.hidden;
""", tmp_path, fixture(FOUR))
    assert out["export_in_menu"] is True and out["role"] == "menuitem" and out["tools_has_menu"] is True
    assert out["keys_line"] is False and out["keys_btn"] is True
    assert out["closed"] == [True, "false", "true", "rvBulkMenu"]
    assert out["open"] == [False, "true", True]
    assert out["esc"] == [True, "false", True]
    assert out["outside"] is True


@NEEDS_NODE
def test_a_bulk_card_keeps_the_first_warning_and_the_rest_behind_details(tmp_path):
    prods = FOUR + [product(20, "Warned", warns=("foreign_store", "size_mismatch", "low_res"))]
    out = page(r"""
R.setMode('bulk');
await flush();
const card = document.querySelectorAll('.rv-card').find(c => c.getAttribute('data-key') === itemOf(20).key);
out.extra = card.querySelectorAll('.rv-card__warn').map(w => w.classList.contains('rv-card__warn--extra'));
const btn = card.querySelector('[data-details]');
out.before = [btn.textContent, btn.getAttribute('aria-expanded'), card.classList.contains('is-details')];
btn.focus();
btn.click();
out.after = [btn.textContent, btn.getAttribute('aria-expanded'), card.classList.contains('is-details'),
             document.activeElement === btn];
// still open after the grid is drawn again
R.bulk.render();
const again = document.querySelectorAll('.rv-card').find(c => c.getAttribute('data-key') === itemOf(20).key);
out.redrawn = again.classList.contains('is-details');
""", tmp_path, fixture(prods))
    assert out["extra"] == [False, True, True]
    assert out["before"] == ["+2 تفاصيل", "false", False]
    assert out["after"] == ["خبّي التفاصيل", "true", True, True]
    assert out["redrawn"] is True
