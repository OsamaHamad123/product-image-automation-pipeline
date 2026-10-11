"""Bulk review (?mode=bulk) under the node harness: fewer steps, same safety.

* a bulk reject (R / X + reason digit, or the «رفض المحدد…» dialog) is held approveUndoMs like an approval: «تراجع»
  in the same undo box takes it back before anything is sent (the card and its tick come back), and once the hold is
  over it goes out as before (no new search); with undo off it is sent at once;
* Shift+A asks no question when every ticked card is pre-selected without a warning and undo is on; it still asks
  when a ticked card has a warning or undo is off;
* roving tabindex: the grid is one tab stop (the focused card, or the first), arrows move it, focus entering a card
  makes it the focused one;
* Enter approves the focused card like A; after A / Enter / a quick reject advances, a fast second key is ignored;
  Shift+Space ticks a range like Shift+click, and a stale anchor ticks the one card.
"""

import json

import pytest

from laqta_review_harness import NODE, run

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")

EV = {"brand": True, "size": "match", "gtin": "match", "source_class": "uae_retailer", "tier": 1}
VLM = {"decision": "MATCH", "brand_match": "yes", "size_match": "yes", "variant_match": "yes", "view": "front_packshot"}


def js(value):
    return json.dumps(value, ensure_ascii=False)


def product(row, name, warn=None):
    reasons = [f"warn:{warn}"] if warn else []
    cands = [{"image_url": f"https://www.carrefouruae.com/p{row}.jpg", "status": "preselected", "is_selected": 1,
              "title": name, "reasons": reasons, "evidence": dict(EV), "vlm": VLM},
             {"image_url": f"https://www.carrefouruae.com/a{row}.jpg", "status": "eligible", "is_selected": 0,
              "title": f"{name} alt", "reasons": [], "evidence": dict(EV)}]
    return {"row_number": row, "product_name": name, "brand": "Almarai", "barcode": "", "sku_key": f"key-{row}",
            "size": "1L", "product_name_ar": "", "brand_ar": "", "category": "Dairy", "existing_image_link": "",
            "needs_review": True, "preselected": True, "curation_candidates": cands}


def fixture(products):
    rows = [{"row_number": p["row_number"], "sku_key": p["sku_key"], "status": "ready_for_review", "failure_code": None,
             "product_name": p["product_name"], "brand": p["brand"], "updated_at": "2026-10-05 10:00:00"} for p in products]
    return {"products": products, "queue": {"status": "success", "ready_for_review": len(rows), "rows": rows}}


SIX = [product(40 + i, f"Card {i}") for i in range(6)]

HELP = r"""
const cards = () => document.querySelectorAll('.rv-card').filter(c => c.getAttribute('data-key'));
const rowOf = node => S().byKey.get(node.getAttribute('data-key')).product.row_number;
const focusedRow = () => { const k = S().bulk.focus; return k ? S().byKey.get(k).product.row_number : null; };
const tabStops = () => cards().filter(c => c.getAttribute('tabindex') === '0').map(rowOf);
const overlays = () => document.querySelectorAll('.rv-card__overlay').map(o => o.textContent);
const undoBox = () => document.getElementById('rvUndo');
const tickedRows = () => R.bulk.tickedKeys().map(k => S().byKey.get(k).product.row_number).sort();
"""


def page(scenario, tmp_path, products=SIX, config=None):
    cfg = dict({"mode": "bulk"}, **(config or {}))
    return run(HELP + f"await boot({js(cfg)});\nawait flush();\n" + scenario, tmp_path, fixture(products))


# ---------------------------------------------------------------------------
# Held rejects and «تراجع»
# ---------------------------------------------------------------------------

def test_a_quick_reject_is_held_and_undo_takes_it_back_before_anything_is_sent(tmp_path):
    out = page(r"""
press('ArrowLeft');
press('ArrowLeft');                                  // the second card
press('r');
press('3');                                          // «نوع أو نكهة مختلفة»
await flush();
out.sent = requests('/api/reject_image').length;
out.undo = undoBox().textContent;
out.undo_hidden = undoBox().hidden;
out.undo_is_reject = !!document.querySelector('#rvUndo .rv-undo__row.is-reject');
out.overlays = overlays();
out.focus_after = focusedRow();
out.progress = R.bulk.progressView() ? R.bulk.progressView().text : null;
const leave = { preventDefault() { this.prevented = true; } };
windowListeners.beforeunload.forEach(fn => fn(leave));
out.leave_prompt = !!leave.prevented;
document.querySelector('#rvUndo [data-undo]').click();
await flush();
out.after = [requests('/api/reject_image').length, overlays().length, undoBox().hidden, focusedRow()];
out.ticked_back = tickedRows().includes(41);
out.progress_after = R.bulk.progressView();
out.toast = toasts.map(t => t.text).filter(t => t.startsWith('تراجعت'));
""", tmp_path, config={"approveUndoMs": 5000})
    assert out["sent"] == 0 and out["undo_hidden"] is False and out["undo_is_reject"] is True
    assert out["undo"].startswith("رفضت Card 1") and "«" in out["undo"] and "اعتمدت" not in out["undo"]
    assert out["overlays"] == ["رح تنرفض…"]
    assert out["focus_after"] == 42                       # moved on to the next card, like A
    assert out["progress"] == "1 من 6"
    assert out["leave_prompt"] is True                    # a held reject is not lost silently
    assert out["after"] == [0, 0, True, 41]               # nothing sent, the card is back and focused again
    assert out["ticked_back"] is True and out["progress_after"] is None
    assert out["toast"] == ["تراجعت: ما انرفضت."]


def test_the_bulk_reject_dialog_holds_them_together_then_sends_them_without_a_new_search(tmp_path):
    out = page(r"""
const box = row => document.querySelectorAll('.rv-card input[type="checkbox"]').find(b => b.getAttribute('data-select') === itemOf(row).key);
document.getElementById('rvBulkEligible').click();   // untick all
await flush();
box(42).click();
box(43).click();
await flush();
document.getElementById('rvBulkReject').click();
press('2');
press('Enter');
await flush();
out.sent_now = requests('/api/reject_image').length;
out.undo = undoBox().textContent;
out.overlays = overlays();
await sleep(120);                                    // the hold is over
await flush();
const sent = requests('/api/reject_image');
out.sent = sent.map(c => [c.body.row_number, c.body.research]).sort();
out.searches = requests('/api/search').length;
sent.forEach(c => answer(c, { status: 'success', rejection: { requeued: true } }));
await flush();
out.cards_after = document.querySelectorAll('.rv-card__overlay').map(o => o.textContent);
""", tmp_path, config={"approveUndoMs": 60})
    assert out["sent_now"] == 0
    assert out["undo"].startswith("رفضت صورتين") and "اعتمدت" not in out["undo"]
    assert out["overlays"] == ["رح تنرفض…", "رح تنرفض…"]
    assert out["sent"] == [["42", False], ["43", False]]
    assert out["searches"] == 0
    assert "رح تنرفض…" not in out["cards_after"]


def test_with_undo_off_a_bulk_reject_is_sent_at_once(tmp_path):
    out = page(r"""
press('ArrowLeft');
press('x');
press('1');
await flush();
out.sent = requests('/api/reject_image').map(c => c.body.row_number);
out.undo_hidden = undoBox().hidden;
""", tmp_path, config={"approveUndoMs": 0})
    assert out["sent"] == ["40"] and out["undo_hidden"] is True


# ---------------------------------------------------------------------------
# Shift+A: a question only when it is worth one
# ---------------------------------------------------------------------------

def test_shift_a_asks_nothing_when_every_ticked_card_is_clean_and_undo_is_on(tmp_path):
    out = page(r"""
press('a', { shiftKey: true });
await flush();
out.confirms = confirms.length;
out.undo = undoBox().textContent;
out.sent = requests('/api/select_image').length;
""", tmp_path, config={"approveUndoMs": 5000})
    assert out["confirms"] == 0
    assert out["undo"].startswith("اعتمدت 6 صور") and out["sent"] == 0


def test_shift_a_still_asks_with_undo_off_or_a_ticked_card_with_a_warning(tmp_path):
    off = page(r"""
press('a', { shiftKey: true });
await flush();
out.confirms = confirms.slice();
""", tmp_path, config={"approveUndoMs": 0})
    assert len(off["confirms"]) == 1 and off["confirms"][0].startswith("اعتماد 6 صور")

    warned = SIX[:3] + [product(50, "Warned", warn="foreign_store")]
    out = page(r"""
const box = document.querySelectorAll('.rv-card input[type="checkbox"]').find(b => b.getAttribute('data-select') === itemOf(50).key);
box.click();                                         // tick the card with a warning too
await flush();
press('a', { shiftKey: true });
await flush();
out.confirms = confirms.slice();
out.sent_rows = S().jobs.state().held.flatMap(g => g.jobs.map(j => j.row)).sort();
""", tmp_path, products=warned, config={"approveUndoMs": 5000})
    assert len(out["confirms"]) == 1 and out["confirms"][0].startswith("اعتماد 3 صور")
    assert out["sent_rows"] == ["40", "41", "42"]          # the warned card is never in the bulk approval


# ---------------------------------------------------------------------------
# Roving tabindex
# ---------------------------------------------------------------------------

def test_the_grid_is_one_tab_stop_that_follows_the_focused_card(tmp_path):
    out = page(r"""
out.start = tabStops();
press('ArrowLeft');
press('ArrowLeft');
out.after_arrows = [tabStops(), focusedRow(), rowOf(document.activeElement)];
// Tab (or a click) lands inside the fourth card: it becomes the focused card and the tab stop
const fourth = cards()[3];
const btn = fourth.querySelector('[data-open]');
btn.focus();
dispatch(btn, { type: 'focusin', bubbles: true });
out.after_focusin = [tabStops(), focusedRow()];
R.bulk.render();
out.after_render = tabStops();
""", tmp_path)
    assert out["start"] == [40]
    assert out["after_arrows"] == [[41], 41, 41]         # the first arrow lands on the first card
    assert out["after_focusin"] == [[43], 43]
    assert out["after_render"] == [43]


# ---------------------------------------------------------------------------
# Enter, the settle guard, Shift+Space
# ---------------------------------------------------------------------------

def test_enter_approves_like_a_and_a_fast_second_key_waits_for_the_reviewer(tmp_path):
    out = page(r"""
press('ArrowLeft');                                  // the first card
press('Enter');
await flush();
out.first = [requests('/api/select_image').map(c => c.body.row_number), focusedRow()];
press('Enter');                                      // at once: the next card is not approved before it is seen
press('a');
press('r');
await flush();
out.second = [requests('/api/select_image').length, document.getElementById('rvDialog').hidden];
press('Enter', { repeat: true });
await flush();
out.repeat = requests('/api/select_image').length;
""", tmp_path, config={"approveSettleMs": 60000})
    assert out["first"] == [["40"], 41]
    assert out["second"] == [1, True]
    assert out["repeat"] == 1


def test_a_quick_reject_also_waits_before_the_next_key_acts(tmp_path):
    out = page(r"""
press('ArrowLeft');
press('r');
press('1');
await flush();
press('a');
await flush();
out.approved = requests('/api/select_image').length;
out.focus = focusedRow();
""", tmp_path, config={"approveSettleMs": 60000})
    assert out["approved"] == 0 and out["focus"] == 41


def test_shift_space_ticks_a_range_and_a_stale_anchor_ticks_the_one_card(tmp_path):
    out = page(r"""
document.getElementById('rvBulkEligible').click();   // untick all
await flush();
press('ArrowLeft');
press('ArrowLeft');                                  // 41
press(' ');
press('ArrowLeft');
press('ArrowLeft');
press('ArrowLeft');                                  // 44
press(' ', { shiftKey: true });
out.range = tickedRows();
document.getElementById('rvBulkEligible').click();   // tick all
document.getElementById('rvBulkEligible').click();   // untick all
await flush();
S().bulk.anchor = 'gone';                            // the anchor card left the grid
document.getElementById('rvBulkEligible').focus();   // the click left the focus on the checkbox
press('ArrowRight');
press('ArrowLeft');                                  // back on 44 (the grid was drawn again)
press(' ', { shiftKey: true });
out.stale = tickedRows();
""", tmp_path)
    assert out["range"] == [41, 42, 43, 44]
    assert out["stale"] == [44]
