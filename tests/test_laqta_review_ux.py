"""Review screen UX (wp/ux) under the node harness: the page scripts unchanged, a small DOM, a scripted fetch.

* approvals are held approveUndoMs with «تراجع», which takes them back before anything is sent; a held approval is
  never lost: when the page is hidden every held or waiting job goes out at once with fetch keepalive;
* the queue sends two requests at a time, never two for the same product (sku_key);
* R.ask (every former window.confirm): Enter confirms (not in the first moment, not a held key), Esc cancels, Tab
  stays inside, the focus goes back to the opener;
* one filter set for both modes, kept across a switch; the mode is remembered (localStorage) and bulk opens by
  default with 10+ pictures proposed without a warning;
* the lightbox: Z opens it, ← → move across the candidates (RTL), Esc closes it and gives the focus back, Enter
  inside never approves;
* bulk: R rejects the focused card with a number key, Shift+click ticks a range, the header says «N من M»;
* phone: the stacked list never scrolls the page; «all done» says the day's numbers and the next steps.
"""

import json
from pathlib import Path

import pytest

from laqta_review_harness import NODE, run

ROOT = Path(__file__).resolve().parents[1]
REVIEW_JS = ROOT / "dashboard" / "public" / "js" / "review"
NEEDS_NODE = pytest.mark.skipif(NODE is None, reason="node is not installed")
pytestmark = NEEDS_NODE

EV = {"brand": True, "size": "match", "gtin": "match", "source_class": "uae_retailer", "tier": 1}
VLM = {"decision": "MATCH", "brand_match": "yes", "size_match": "yes", "variant_match": "yes", "view": "front_packshot"}
OK = {"status": "success", "image_link": "https://res.cloudinary.com/demo/ok.png", "isolated": True, "sheet": "written"}


def js(value):
    return json.dumps(value, ensure_ascii=False)


def product(row, name, sku=None, alts=0, warn=None, brand="Almarai"):
    url = f"https://www.carrefouruae.com/p{row}.jpg"
    reasons = [f"warn:{warn}"] if warn else []
    cands = [{"image_url": url, "status": "preselected", "is_selected": 1, "title": name, "reasons": reasons,
              "evidence": dict(EV), "vlm": VLM}]
    cands += [{"image_url": f"https://www.carrefouruae.com/a{row}-{i}.jpg", "status": "eligible", "is_selected": 0,
               "title": f"{name} alt {i}", "reasons": [], "evidence": dict(EV)} for i in range(alts)]
    return {"row_number": row, "product_name": name, "brand": brand, "barcode": "", "sku_key": sku or f"key-{row}",
            "size": "1L", "product_name_ar": "", "brand_ar": "", "category": "Dairy", "existing_image_link": "",
            "needs_review": True, "preselected": True, "curation_candidates": cands}


def fixture(products, today=None):
    rows = [{"row_number": p["row_number"], "sku_key": p["sku_key"], "status": "ready_for_review", "failure_code": None,
             "product_name": p["product_name"], "brand": p["brand"], "updated_at": "2026-10-05 10:00:00"} for p in products]
    queue = {"status": "success", "ready_for_review": len(rows), "rows": rows}
    if today is not None:
        queue["today"] = today
    return {"products": products, "queue": queue}


def page(scenario, tmp_path, fx, config=None, before=""):
    return run(before + f"await boot({js(config or {})});\n" + scenario, tmp_path, fx)


FOUR = [product(10 + i, f"Prod {i}", alts=5) for i in range(4)]


# ---------------------------------------------------------------------------
# Undo: held, taken back, never lost
# ---------------------------------------------------------------------------

def test_an_approval_is_held_and_undo_takes_it_back_before_anything_is_sent(tmp_path):
    out = page(r"""
press('Enter');
await flush();
out.sent_at_once = requests('/api/select_image').length;
out.undo = document.getElementById('rvUndo').textContent;
out.undo_hidden = document.getElementById('rvUndo').hidden;
out.moved_on = productName();
const leave = { preventDefault() { this.prevented = true; } };
windowListeners.beforeunload.forEach(fn => fn(leave));
out.leave_prompt = !!leave.prevented;
document.querySelector('#rvUndo [data-undo]').click();
await flush();
out.after_undo = [requests('/api/select_image').length, productName(), S().local.get(itemOf(10).key) || null,
                  document.getElementById('rvUndo').hidden, toasts.slice(-1)[0].text];
await sleep(450);
out.never_sent = requests('/api/select_image').length;
// approved again and left alone: it goes out when the hold ends
press('Enter');
await sleep(450);
await flush();
out.sent_later = requests('/api/select_image').map(c => c.body.row_number);
""", tmp_path, fixture(FOUR), config={"row": 10, "approveUndoMs": 300})
    assert out["sent_at_once"] == 0 and out["undo_hidden"] is False
    assert out["undo"].startswith("اعتمدت Prod 0") and "تراجع" in out["undo"]
    assert out["moved_on"] == "Prod 1" and out["leave_prompt"] is True       # a held approval keeps the leave prompt
    assert out["after_undo"] == [0, "Prod 0", None, True, "تراجعت: ما انعتمدت."]
    assert out["never_sent"] == 0
    assert out["sent_later"] == ["10"]


def test_a_held_approval_goes_out_with_keepalive_when_the_page_is_hidden(tmp_path):
    out = page(r"""
press('Enter');
await flush();
out.held = requests('/api/select_image').length;
document.visibilityState = 'hidden';
dispatch(document, { type: 'visibilitychange' });
await flush();
const sent = requests('/api/select_image');
out.flushed = sent.map(c => [c.body.row_number, !!c.init.keepalive]);
out.undo_hidden = document.getElementById('rvUndo').hidden;
// pagehide (the tab closes) with nothing held left: nothing more is sent
(windowListeners.pagehide || []).forEach(fn => fn({ type: 'pagehide' }));
out.after_pagehide = requests('/api/select_image').length;
""", tmp_path, fixture(FOUR), config={"row": 10, "approveUndoMs": 8000})
    assert out["held"] == 0
    assert out["flushed"] == [["10", True]] and out["undo_hidden"] is True
    assert out["after_pagehide"] == 1


def test_a_keepalive_approval_the_server_queued_is_followed_not_shown_as_failed(tmp_path):
    # the page hides during the undo hold: the approval goes out with keepalive and the server answers 202 (queued).
    # It is the server's now: never «ما انعتمدت» with a retry button, and it settles when the job finishes
    out = page(r"""
press('Enter');
await flush();
document.visibilityState = 'hidden';
dispatch(document, { type: 'visibilitychange' });
await flush();
const sel = requests('/api/select_image')[0];
out.keepalive = !!sel.init.keepalive;
answer(sel, { status: 'queued', job_id: 21, existing: false }, 202);
await flush();
out.busy = S().jobs.busy();
out.failedNow = S().jobs.state().failedAll;
document.visibilityState = 'visible';
dispatch(document, { type: 'visibilitychange' });
approvalJobs = url => ({ status: 'success', jobs: url.includes('ids=21') ? [{ id: 21, status: 'done', http_status: 200,
    result: { status: 'success', image_link: 'https://res.cloudinary.com/demo/10.png', isolated: true } }] : [] });
await sleep(60);
await flush();
out.failedAfter = S().jobs.state().failedAll;
out.done = S().jobs.state().done;
""", tmp_path, fixture(FOUR), config={"row": 10, "approveUndoMs": 8000, "approvalPollMs": 10})
    assert out["keepalive"] is True
    assert out["busy"] is False and out["failedNow"] == 0
    assert out["failedAfter"] == 0 and out["done"] == 1


def test_bulk_approvals_are_held_together_and_one_undo_takes_them_all_back(tmp_path):
    out = page(r"""
R.setMode('bulk');
await flush();
document.getElementById('rvBulkApprove').click();
await flush();
out.undo = document.getElementById('rvUndo').textContent;
out.overlays = document.querySelectorAll('.rv-card__overlay').map(o => o.textContent);
out.sent = requests('/api/select_image').length;
document.querySelector('#rvUndo [data-undo]').click();
await flush();
out.after = [requests('/api/select_image').length, R.bulk.tickedKeys().length,
             document.querySelectorAll('.rv-card__overlay').length];
""", tmp_path, fixture(FOUR), config={"mode": "bulk", "approveUndoMs": 5000})
    assert out["undo"].startswith("اعتمدت 4 صور") and out["sent"] == 0
    assert out["overlays"] == ["رح تنعتمد…"] * 4
    assert out["after"] == [0, 4, 0]                          # nothing sent, the ticks are back, no overlay


# ---------------------------------------------------------------------------
# Two at a time, never two for the same product
# ---------------------------------------------------------------------------

def test_two_requests_at_a_time_and_never_two_for_the_same_product(tmp_path):
    same = [product(10, "Twin A", sku="key-twin"), product(11, "Twin B", sku="key-twin"), product(12, "Other"),
            product(13, "Third")]
    out = page(r"""
R.setMode('bulk');
await flush();
document.getElementById('rvBulkApprove').click();
await flush();
out.first = requests('/api/select_image').map(c => c.body.row_number);
answer(requests('/api/select_image')[0], OK_ANSWER);
await flush();
out.second = requests('/api/select_image').map(c => c.body.row_number);
out.max = maxInFlight.select;
""".replace("OK_ANSWER", js(OK)), tmp_path, fixture(same), config={"mode": "bulk"})
    # the twin rows share a product: B waits for A even though a slot is free; the third product takes the slot
    assert out["first"] == ["10", "12"]
    assert out["second"] == ["10", "12", "11"]
    assert out["max"] == 2


# ---------------------------------------------------------------------------
# R.ask: the page's own question
# ---------------------------------------------------------------------------

def test_the_question_takes_enter_esc_and_tab_and_gives_the_focus_back(tmp_path):
    out = page(r"""
askAuto = false;
const opener = document.getElementById('rvSkip');
opener.focus();
let answers = [];
R.ask({ title: 'اعتماد صورتين؟', text: 'بتنحط بالشيت.' }).then(v => answers.push(v));
out.focus_on_open = document.activeElement.id;
press('Enter');                                    // at once: the Enter that opened it, never a «yes»
await flush();
out.too_fast = [answers.slice(), !!document.getElementById('rvAskConfirm')];
press('Tab');
out.tab = document.activeElement.id;
press('Tab');
out.tab_wraps = document.activeElement.id;
press('Escape');
await flush();
out.esc = [answers.slice(), document.getElementById('rvDialog').hidden, document.activeElement === opener];
R.ask({ title: 'اعتماد صورة؟' }).then(v => answers.push(v));
await sleep(450);
dispatch(document.activeElement, { type: 'keydown', key: 'Enter', code: 'Enter', repeat: true, bubbles: true });
await flush();
out.repeat_ignored = answers.length;
press('Enter');
await flush();
out.enter = answers.slice();
// Enter on a focused «إلغاء» cancels
R.ask({ title: 'متأكد؟' }).then(v => answers.push(v));
await sleep(450);
document.getElementById('rvAskCancel').focus();
press('Enter');
await flush();
out.enter_on_cancel = answers.slice(-1)[0];
""", tmp_path, fixture(FOUR), config={"row": 10})
    assert out["focus_on_open"] == "rvAskConfirm"
    assert out["too_fast"] == [[], True]
    assert out["tab"] == "rvAskCancel" and out["tab_wraps"] == "rvAskConfirm"   # Tab stays inside
    assert out["esc"] == [[False], True, True]                                   # cancelled, closed, focus back
    assert out["repeat_ignored"] == 1
    assert out["enter"] == [False, True]
    assert out["enter_on_cancel"] is False


def test_no_window_confirm_is_left_in_the_review_scripts():
    for name in ("core", "ui", "jobs", "single", "bulk", "app"):
        text = (REVIEW_JS / f"{name}.js").read_text(encoding="utf-8")
        assert "root.confirm(" not in text and "window.confirm(" not in text, name


# ---------------------------------------------------------------------------
# Modes and the one filter set
# ---------------------------------------------------------------------------

STORE = r"""
const stored = {};
globalThis.localStorage = { getItem: k => (k in stored ? stored[k] : null), setItem: (k, v) => { stored[k] = String(v); } };
"""

ELEVEN = [product(20 + i, f"Bulk {i}") for i in range(11)]


def test_bulk_opens_by_default_with_ten_eligible_pictures_and_a_chosen_mode_is_remembered(tmp_path):
    out = page(r"""
out.mode = S().mode;
out.filter = S().filter;
out.url = historyLog.slice(-1)[0];
document.querySelectorAll('.rv-modes__item').find(b => b.getAttribute('data-mode') === 'single' && !b.closest('[hidden]')).click();
await flush();
out.chosen = [S().mode, stored['laqta.review.mode']];
""", tmp_path, fixture(ELEVEN), config={"modeExplicit": False, "filter": None}, before=STORE)
    assert out["mode"] == "bulk" and out["filter"] == "eligible" and out["url"][1] == "/catalog?mode=bulk&filter=eligible"
    assert out["chosen"] == ["single", "single"]

    again = page(r"""
out.mode = S().mode;
""", tmp_path, fixture(ELEVEN), config={"modeExplicit": False}, before=STORE + "stored['laqta.review.mode'] = 'single';\n")
    assert again["mode"] == "single"                           # the reviewer's own choice wins over the default

    few = page(r"""
out.mode = S().mode;
""", tmp_path, fixture(ELEVEN[:9]), config={"modeExplicit": False}, before=STORE)
    assert few["mode"] == "single"                             # nine: single mode


def test_one_filter_set_kept_across_a_mode_switch(tmp_path):
    prods = [product(30, "Clean A"), product(31, "Clean B"), product(32, "Warned", warn="foreign_store")]
    out = page(r"""
out.single_chips = document.querySelectorAll('.rv-filters [data-filter]').map(b => b.getAttribute('data-filter'));
R.setFilter('warning');
await flush();
R.chooseMode('bulk');
await flush();
out.bulk = [S().filter, document.querySelectorAll('.rv-card').map(c => c.querySelector('.rv-card__name').textContent),
            document.querySelector('[data-bulk-filter="warning"]').getAttribute('aria-pressed')];
document.querySelector('[data-bulk-filter="eligible"]').click();
await flush();
R.chooseMode('single');
await flush();
out.single = [S().filter, R.visibleItems().map(it => it.product.product_name),
              document.querySelector('.rv-filters [data-filter="eligible"]').getAttribute('aria-pressed')];
""", tmp_path, fixture(prods), config={"row": 30}, before=STORE)
    assert out["single_chips"] == ["all", "proposed", "eligible", "strict", "warning", "none", "not_found", "failed", "bg_failed"]
    assert out["bulk"] == ["warning", ["Warned"], "true"]
    assert out["single"] == ["eligible", ["Clean A", "Clean B"], "true"]


# ---------------------------------------------------------------------------
# Lightbox
# ---------------------------------------------------------------------------

def test_the_lightbox_opens_with_z_moves_across_candidates_and_closes_with_esc(tmp_path):
    out = page(r"""
const opener = approveBtn();
opener.focus();
press('z');
await flush();
const lb = () => document.getElementById('rvLightbox');
out.open = [!!lb(), R.lightboxOpen().index, R.lightboxOpen().count, lb().querySelector('.rv-lb__count').textContent];
out.compare = lb().querySelector('.rv-lb__side').textContent;
press('ArrowLeft');                                  // RTL: the next one
out.left = R.lightboxOpen().index;
press('ArrowLeft');
press('ArrowRight');                                 // and back
out.right = R.lightboxOpen().index;
press('Enter');                                      // never an approval from behind the lightbox
await flush();
out.no_approval = requests('/api/select_image').length;
document.getElementById('rvLbChoose').click();       // «اختارها»: selects only
await flush();
out.chosen = [pickUrl(), requests('/api/select_image').length, !!lb()];
press('z');
press('Escape');
out.closed = [!!lb(), document.activeElement === opener];
// a click on the picture opens it too
ws().querySelector('.rv-pick__stage img').click();
out.click_open = !!lb();
""", tmp_path, fixture(FOUR), config={"row": 10})
    assert out["open"] == [True, 0, 6, "1 من 6"]
    assert "الصورة الحالية بالشيت" in out["compare"]
    assert out["left"] == 1 and out["right"] == 1
    assert out["no_approval"] == 0
    assert out["chosen"] == ["https://www.carrefouruae.com/a10-0.jpg", 0, False]
    assert out["closed"] == [False, True]
    assert out["click_open"] is True


def test_the_lightbox_works_on_bulk_cards(tmp_path):
    out = page(r"""
R.setMode('bulk');
await flush();
press('ArrowLeft');
press('ArrowLeft');
press('z');
out.open = [R.lightboxOpen().index, R.lightboxOpen().count];
press('ArrowLeft');
out.next = R.lightboxOpen().url;
press('Escape');
document.querySelectorAll('.rv-card__img img')[3].click();
out.clicked = R.lightboxOpen().index;
""", tmp_path, fixture(FOUR), config={"mode": "bulk"})
    assert out["open"] == [1, 4] and out["next"] == "https://www.carrefouruae.com/p12.jpg"
    assert out["clicked"] == 3


# ---------------------------------------------------------------------------
# Bulk keyboard, range, progress
# ---------------------------------------------------------------------------

def test_r_rejects_the_focused_card_with_a_number_and_shift_click_ticks_a_range(tmp_path):
    prods = [product(40 + i, f"Card {i}") for i in range(6)]
    out = page(r"""
R.setMode('bulk');
await flush();
press('ArrowLeft');
press('ArrowLeft');                                  // the second card
press('r');
out.dialog = document.getElementById('rvDialog').textContent;
press('3');                                          // «نوع أو نكهة مختلفة»
await flush();
out.rejected = requests('/api/reject_image').map(c => [c.body.row_number, c.body.reason_code]);
out.dialog_closed = document.getElementById('rvDialog').hidden;
// untick all, then a click and a Shift+click tick everything between
document.getElementById('rvBulkEligible').click();
await flush();
const box = row => document.querySelectorAll('.rv-card input[type="checkbox"]').find(b => b.getAttribute('data-select') === itemOf(row).key);
box(42).click();
await flush();
const target = box(45);
dispatch(target, { type: 'click', bubbles: true, shiftKey: true });
target.checked = true;
dispatch(target, { type: 'change', bubbles: true });
await flush();
out.range = R.bulk.tickedKeys().map(k => S().byKey.get(k).product.row_number).sort();
out.progress = document.getElementById('rvBulkProgress').textContent;
""", tmp_path, fixture(prods), config={"mode": "bulk"})
    assert "ليش ترفضها؟" in out["dialog"] and "اضغط رقم السبب 1–7" in out["dialog"] and "«Card 1»" in out["dialog"]
    assert out["rejected"] == [["41", "WRONG_VARIANT"]] and out["dialog_closed"] is True
    assert out["range"] == [42, 43, 44, 45]
    assert out["progress"] == "1 من 6"


def test_the_bulk_progress_estimates_the_time_left_from_the_pace(tmp_path):
    prods = [product(50 + i, f"Card {i}") for i in range(10)]
    out = run(r"""
let fakeNow = 1000000;
Date.now = () => fakeNow;
await boot({ mode: 'bulk' });
await flush();
S().bulk.startedAt = fakeNow;
fakeNow += 3 * 60000;                                // three minutes for three cards: a minute each
for (const row of [50, 51, 52]) R.bulk.approveOne(itemOf(row).key);
await flush();
out.text = R.bulk.progressView().text;
""", tmp_path, fixture(prods))
    assert out["text"] == "3 من 10 · باقي ~7 دقايق"


# ---------------------------------------------------------------------------
# Phone and «all done»
# ---------------------------------------------------------------------------

def test_the_stacked_list_never_scrolls_the_page(tmp_path):
    out = page(r"""
out.calls = scrolls.length;
R.single.move(1);
out.after_move = scrolls.length;
""", tmp_path, fixture(FOUR), config={"row": 11},
        before="globalThis.matchMedia = q => ({ matches: /max-width: 980px/.test(q) });\n"
               "const scrolls = []; ShimElement.prototype.scrollIntoView = function () { scrolls.push(this.className); };\n")
    assert out["calls"] == 0 and out["after_move"] == 0

    wide = page(r"""
out.calls = scrolls.filter(c => /rv-item/.test(c)).length;
""", tmp_path, fixture(FOUR), config={"row": 11},
        before="globalThis.matchMedia = q => ({ matches: false });\n"
               "const scrolls = []; ShimElement.prototype.scrollIntoView = function () { scrolls.push(this.className); };\n")
    assert wide["calls"] >= 1                                  # the side list still follows the open product


def test_all_done_says_the_days_numbers_and_the_next_steps(tmp_path):
    done = dict(product(60, "Done"), needs_review=False, curation_candidates=[],
                existing_image_link="https://res.cloudinary.com/demo/d.png")
    missing = dict(product(61, "Missing"), needs_review=False, curation_candidates=[], has_error=True,
                   error_message="NO_RESULTS: No acceptable image found (NO_RESULTS)")
    fx = {"products": [done, missing],
          "queue": {"status": "success", "ready_for_review": 0, "today": {"approved": 87, "rejected": 6},
                    "rows": [{"row_number": 61, "sku_key": "key-61", "status": "failed", "failure_code": "NO_RESULTS",
                              "product_name": "Missing", "brand": "Almarai", "updated_at": "2026-10-05 10:00:00"}]}}
    out = page(r"""
R.setMode('bulk');
await flush();
out.text = document.getElementById('rvDone').textContent;
out.links = document.querySelectorAll('#rvDone a').map(a => a.getAttribute('href'));
document.getElementById('rvRetryNotFound').click();
await flush();
out.retry = requests('/api/failures/retry').map(c => c.body.barcodes);
""", tmp_path, fx, config={"mode": "bulk"})
    assert "اليوم: اعتمدت 87، رفضت 6، 1 ما انلقت" in out["text"]
    assert out["links"] == ["/batch-automation#run-brands", "/batch-automation"]
    assert out["retry"] == [["ERR_Missing_Almarai"]]


def test_the_tab_title_says_how_many_wait(tmp_path):
    out = page(r"""
out.title = document.title;
press('Enter');
await flush();
out.after = document.title;
""", tmp_path, fixture(FOUR), config={"row": 10}, before="document.title = 'المراجعة · لقطة';\n")
    assert out["title"] == "(4) المراجعة · لقطة" and out["after"] == "(3) المراجعة · لقطة"
