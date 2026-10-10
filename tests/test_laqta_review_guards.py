"""Review screen guards (wp/p4-review-fix): the reviewer never approves a picture they did not see with its warnings,
the approval guard carries what the reviewer saw, and the screen never claims what did not happen.

Each test failed before its change (the finding's number in the reviewer's list is in the test name's comment):

* #1  a quiet reload moved the approval guard (expected_state) to the state the reload showed, so an approval sent
      after another reviewer approved overwrote theirs; now the guard is the snapshot taken when the product was
      opened, a reload that changes the open product shows a banner and blocks approval until it is shown again;
* #3  the page sends the queue row it matched (a sheet row moved after the queue was built);
* #4  a picture that never rendered was approvable (single and bulk), a fast second Enter / A approved the next
      product unseen, and single mode did not confirm a pick with warnings;
* #5  «publish anyway» said the background was isolated for every flag (opaque_fill means nothing was removed);
* #7  a bulk tick survived a reload that changed the pick; #8 A approved the focus-ring card, not the card clicked;
* #9  Shift+A approved cards never scrolled into view; #10 the derived «size / variant unverified» missed Arabic
      digits, a size in the name, an unconfirmed pack, the variant, and rows without evidence;
* #11 a reject message contradicted the server (approval kept); #13 an unknown warning code was shown raw;
* #14 «why this image» chips on a rejected pick; #15 a Lulu Kuwait page was labelled UAE; #17 the chip count of a
      product kept in its chip.
"""

import json
import re
import subprocess
from pathlib import Path

import pytest

from laqta_review_harness import NODE, run

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "dashboard" / "public" / "js" / "review" / "core.js"

NEEDS_NODE = pytest.mark.skipif(NODE is None, reason="node is not installed")

EV = {"brand": True, "size": "match", "gtin": "match", "source_class": "uae_retailer", "tier": 1}
VLM = {"decision": "MATCH", "brand_match": "yes", "size_match": "yes", "variant_match": "yes", "view": "front_packshot"}
OTHER_APPROVAL = "https://res.cloudinary.com/demo/image/upload/other_reviewer.png"


def js(value):
    return json.dumps(value, ensure_ascii=False)


def cand(url, status="eligible", selected=0, **extra):
    row = {"image_url": url, "status": status, "is_selected": selected, "title": url.rsplit("/", 1)[-1], "reasons": [],
           "evidence": dict(EV)}
    row.update(extra)
    return row


def product(row, name, brand="Almarai", **extra):
    p = {"row_number": row, "product_name": name, "brand": brand, "barcode": "", "sku_key": f"key-{row}", "size": "1L",
         "product_name_ar": "", "brand_ar": "", "category": "Dairy", "sub_category": "", "origin": "",
         "existing_image_link": "", "needs_review": True, "preselected": True, "search_query": f"{name} {brand}"}
    p.update(extra)
    return p


def picked(row, name, url=None, **extra):
    url = url or f"https://www.carrefouruae.com/p{row}.jpg"
    return product(row, name, curation_candidates=[cand(url, "preselected", 1, vlm=VLM, **extra)])


def ready_rows(products, updated="2026-10-03 10:00:00"):
    return [{"row_number": p["row_number"], "sku_key": p["sku_key"], "status": "ready_for_review", "failure_code": None,
             "product_name": p["product_name"], "brand": p["brand"], "updated_at": updated} for p in products]


def fixture(products, rows=None):
    rows = ready_rows(products) if rows is None else rows
    return {"products": products,
            "queue": {"status": "success", "ready_for_review": sum(r["status"] == "ready_for_review" for r in rows),
                      "rows": rows}}


def page(scenario, tmp_path, fx, config=None):
    return run(f"await boot({js(config or {})});\n" + scenario, tmp_path, fx)


def scand(url, status="eligible", warnings=None):
    return {"url": url, "title": url, "page_url": url, "domain": "noon.com", "status": status, "reasons": [],
            "warnings": warnings or [], "evidence": dict(EV), "vlm": None, "width": 800, "height": 800}


# ---------------------------------------------------------------------------
# #1 the approval guard is what the reviewer saw, not what a quiet reload shows
# ---------------------------------------------------------------------------

STALE_SEARCH = {"status": "review", "decision": "REVIEW_UNSELECTED", "selected_image": None, "sku_key": "key-40",
                "candidates": [scand("https://www.noon.com/s1.jpg"), scand("https://www.noon.com/s2.jpg")]}


@NEEDS_NODE
def test_a_reload_that_changes_the_open_product_blocks_approval_until_it_is_shown_again(tmp_path):
    out = page(r"""
openRow(40);
openNotFound();
submitForm(nfForms()[0]);                                   // a custom search: the workspace shows its results
await flush();
answer(requests('/api/search')[0], __SEARCH__);
await flush();
press('2');
// another reviewer approves row 40 (link in the sheet, candidates deleted, queue row completed); a quiet reload runs
FIXTURE.products[0].existing_image_link = '__OTHER__';
FIXTURE.products[0].needs_review = false;
FIXTURE.products[0].curation_candidates = [];
FIXTURE.queue.rows = FIXTURE.queue.rows.filter(r => r.row_number !== 40);
await R.loadData({ quiet: true });
await flush();
out.banner = ws().querySelectorAll('.rv-alert').map(a => a.textContent)[0];
out.blocked = [R.single.canApprove(), approveBtn().disabled, approveBtn().title];
press('Enter');
await flush();
out.sent = requests('/api/select_image').length;
out.guard = R.seenExpected(itemOf(40));                      // still what the reviewer was shown
document.getElementById('rvReopen').click();               // one click: the product as it is now
await flush();
out.after = [S().ws.state, S().moved.has(itemOf(40).key), !!document.getElementById('rvReopen')];
out.guard_after = R.seenExpected(itemOf(40));
""".replace("__SEARCH__", js(STALE_SEARCH)).replace("__OTHER__", OTHER_APPROVAL), tmp_path,
               fixture([picked(40, "Almarai Milk 1L"), picked(41, "Almarai Laban 1L")]), config={"row": 40})
    assert out["banner"].startswith("تغيّر هالمنتج بعد ما فتحته: صار له صورة معتمدة")
    assert "اعرضه من جديد" in out["banner"]
    assert out["blocked"][0] is False and out["blocked"][1] is True and "اعرضه من جديد" in out["blocked"][2]
    assert out["sent"] == 0                                    # Enter never sent the other reviewer's link as «seen»
    assert out["guard"] == {"queue_status": "ready_for_review", "queue_updated_at": "2026-10-03 10:00:00",
                            "approved_url": None, "queue_row": 40}
    # shown again: the approved image (the old search results are not offered over another reviewer's approval)
    assert out["after"] == ["final", False, False]
    assert out["guard_after"]["approved_url"] == OTHER_APPROVAL


@NEEDS_NODE
def test_the_reviewers_own_actions_and_unchanged_reloads_never_raise_the_banner(tmp_path):
    out = page(r"""
openRow(40);
await R.loadData({ quiet: true });                          // nothing changed
await flush();
out.quiet = [S().moved.size, R.single.canApprove()];
FIXTURE.status = { '/api/review/queue-state': 503 };         // the queue could not be read this time
await R.loadData({ quiet: true });
await flush();
out.during = [S().moved.size, !!document.getElementById('rvReopen')];
FIXTURE.status = {};
await R.loadData({ quiet: true });
await flush();
out.unreadable = [S().moved.size, R.single.canApprove()];
press('Enter');                                             // the reviewer's own approval
await flush();
FIXTURE.products[0].existing_image_link = 'https://res.cloudinary.com/demo/own.png';
FIXTURE.products[0].curation_candidates = [];
FIXTURE.queue.rows = FIXTURE.queue.rows.filter(r => r.row_number !== 40);
answer(requests('/api/select_image')[0], { status: 'success', image_link: 'https://res.cloudinary.com/demo/own.png',
    isolated: true, sheet: 'written', current: { queue_status: 'completed', queue_updated_at: '2026-10-03 10:05:00',
    approved_url: 'https://res.cloudinary.com/demo/own.png', queue_row: 40 } });
await flush();
out.own = S().moved.size;
out.next = R.seenExpected(itemOf(40));
""", tmp_path, fixture([picked(40, "Almarai Milk 1L"), picked(41, "Almarai Laban 1L")]), config={"row": 40})
    assert out["quiet"] == [0, True]
    assert out["during"] == [0, False] and out["unreadable"] == [0, True]   # a failed queue read is not «changed»
    assert out["own"] == 0                                     # the reload after its own approval is not «changed»
    assert out["next"] == {"queue_status": "completed", "queue_updated_at": "2026-10-03 10:05:00",
                           "approved_url": "https://res.cloudinary.com/demo/own.png", "queue_row": 40}


@NEEDS_NODE
def test_a_bulk_card_changed_by_a_reload_is_not_approvable_until_shown_again(tmp_path):
    out = page(r"""
R.setMode('bulk');
await flush();
FIXTURE.queue.rows[0].updated_at = '2026-10-03 10:09:00';   // the worker touched row 10 meanwhile
await R.loadData({ quiet: true });
await flush();
const card = () => document.querySelectorAll('.rv-card').find(c => c.getAttribute('data-key') === itemOf(10).key);
out.note = card().querySelector('.rv-card__moved').textContent;
out.button = card().querySelector('[data-approve]').disabled;
out.ticked = R.bulk.tickedKeys().map(k => S().byKey.get(k).product.row_number);
R.bulk.approveOne(itemOf(10).key);
await flush();
out.sent = requests('/api/select_image').length;
card().querySelector('[data-reopen]').click();
await flush();
out.after = [!!card().querySelector('.rv-card__moved'), card().querySelector('[data-approve]').disabled];
R.bulk.approveOne(itemOf(10).key);
await flush();
out.sent_after = requests('/api/select_image').map(c => c.body.expected_state.queue_updated_at);
""", tmp_path, fixture([picked(10, "Almarai Milk 1L"), picked(11, "Almarai Laban 1L")]), config={"mode": "bulk"})
    assert "تغيّر هالمنتج بعد ما ظهر لك" in out["note"] and out["button"] is True
    assert out["ticked"] == [11] and out["sent"] == 0
    assert out["after"] == [False, False]
    assert out["sent_after"] == ["2026-10-03 10:09:00"]


# ---------------------------------------------------------------------------
# #3 the queue row the page matched travels with the approval
# ---------------------------------------------------------------------------

@NEEDS_NODE
def test_the_approval_names_the_queue_row_the_page_matched_for_a_shifted_sheet_row(tmp_path):
    moved = picked(61, "Almarai Milk 1L", sku_key="key-60")
    moved["sku_key"] = "key-60"
    rows = [{"row_number": 60, "sku_key": "key-60", "status": "ready_for_review", "failure_code": None,
             "product_name": "Almarai Milk 1L", "brand": "Almarai", "updated_at": "2026-10-03 10:00:00"}]
    out = page(r"""
openRow(61);
press('Enter');
await flush();
const c = requests('/api/select_image')[0].body;
out.sent = [c.row_number, c.expected_state];
""", tmp_path, fixture([moved], rows=rows), config={"row": 61})
    assert out["sent"] == ["61", {"queue_status": "ready_for_review", "queue_updated_at": "2026-10-03 10:00:00",
                                  "approved_url": None, "queue_row": 60}]


# ---------------------------------------------------------------------------
# #4 approval only of a picture the reviewer saw
# ---------------------------------------------------------------------------

@NEEDS_NODE
def test_single_mode_never_approves_a_pick_whose_picture_did_not_render(tmp_path):
    out = page(r"""
imageFails.add('https://www.carrefouruae.com/p70.jpg');
openRow(70);
await flush();
out.note = ws().querySelector('.rv-pick .rv-img-missing').textContent;
out.state = [R.single.canApprove(), approveBtn().title];
press('Enter');
await flush();
out.sent = requests('/api/select_image').length;
imageMode = 'hold';                                         // the next picture is still loading
openRow(71);
out.loading = [R.single.canApprove(), approveBtn().title];
press('Enter');
releaseImages();
out.loaded = R.single.canApprove();
press('Enter');
await flush();
out.sent_after = requests('/api/select_image').map(c => c.body.row_number);
""", tmp_path, fixture([picked(70, "Almarai Milk 1L"), picked(71, "Almarai Laban 1L")]), config={"row": 70})
    assert out["note"] == "ما قدرنا نعرض الصورة"
    assert out["state"][0] is False and "ما قدرنا نعرض هالصورة" in out["state"][1]
    assert out["sent"] == 0
    assert out["loading"] == [False, "الصورة لسا عم تتحمّل"]
    assert out["loaded"] is True and out["sent_after"] == ["71"]


@NEEDS_NODE
def test_a_fast_second_enter_after_approving_does_not_approve_the_next_product_unseen(tmp_path):
    prods = [picked(80, "Prod 0"), picked(81, "Prod 1", reasons=["warn:barcode_conflict", "warn:size_unverified"])]
    out = page(r"""
openRow(80);
await sleep(450);
press('Enter'); press('Enter');                             // two taps, not a held key
await flush();
out.first = [requests('/api/select_image').map(c => c.body.row_number), confirms.length, productName()];
await sleep(450);                                           // the next product has been on screen long enough
confirmAnswer = false;
press('Enter');
await flush();
out.refused = [requests('/api/select_image').length, confirms.slice(-1)[0]];
confirmAnswer = true;
press('Enter');
await flush();
out.second = S().jobs.state().jobs.length;                  // one request at a time: the second job waits
""", tmp_path, fixture(prods), config={"row": 80, "approveSettleMs": 400})
    assert out["first"] == [["80"], 0, "Prod 1"]                 # row 81 (with warnings) was never approved unseen
    # single mode confirms a pick with warnings, naming the product
    assert out["refused"][0] == 1
    assert out["refused"][1].startswith("«Prod 1»: تأكد قبل الاعتماد: الباركود بالشيت مختلف")
    assert "الحجم غير مؤكد" in out["refused"][1]
    assert out["second"] == 2


@NEEDS_NODE
def test_bulk_never_approves_a_card_whose_picture_did_not_render(tmp_path):
    prods = [picked(10 + i, f"Prod {i}") for i in range(4)]
    out = page(r"""
imageFails.add('https://www.carrefouruae.com/p11.jpg');
imageFails.add('https://www.carrefouruae.com/p12.jpg');
R.setMode('bulk');
await flush();
const card = row => document.querySelectorAll('.rv-card').find(c => c.getAttribute('data-key') === itemOf(row).key);
out.missing = document.querySelectorAll('.rv-card .rv-img-missing').length;
out.ticked = R.bulk.tickedKeys().map(k => S().byKey.get(k).product.row_number);
out.disabled = [card(11).querySelector('input').disabled, card(11).querySelector('[data-approve]').disabled];
out.label = document.getElementById('rvBulkApprove').textContent;
R.bulk.approveOne(itemOf(12).key);
press('a', { shiftKey: true });
await flush();
out.confirm = confirms.slice(-1)[0];
out.jobs = S().jobs.state().jobs.map(j => j.ctx.row_number);
""", tmp_path, fixture(prods), config={"mode": "bulk"})
    assert out["missing"] == 2
    assert out["ticked"] == [10, 13] and out["disabled"] == [True, True]
    assert out["label"] == "اعتماد صورتين بلا تحذير"
    assert out["jobs"] == ["10", "13"] and out["confirm"].startswith("اعتماد صورتين؟")


@NEEDS_NODE
def test_a_fast_second_a_in_bulk_mode_is_ignored(tmp_path):
    prods = [picked(10 + i, f"Prod {i}") for i in range(4)]
    out = page(r"""
R.setMode('bulk');
await flush();
press('ArrowLeft');
press('a'); press('a');                                     // 39 ms apart in the browser: one approval
await flush();
out.fast = requests('/api/select_image').map(c => c.body.row_number);
await sleep(450);
press('a');
await flush();
out.later = requests('/api/select_image').length + S().jobs.state().jobs.length;
""", tmp_path, fixture(prods), config={"mode": "bulk", "approveSettleMs": 400})
    assert out["fast"] == ["10"]
    # the third press (after the delay) approved card 11: two requests (two products may go out together), two jobs
    assert out["later"] == 2 + 2


# ---------------------------------------------------------------------------
# #5 publish anyway: per flag, never «the background is isolated», and the note stays on the approved view
# ---------------------------------------------------------------------------

@NEEDS_NODE
def test_publish_anyway_is_never_offered_for_opaque_fill_and_says_what_each_flag_means(tmp_path):
    refused = {"status": "failed", "error_code": "quality_flags", "error": "q", "publish_anyway_allowed": True,
               "quality_flags": ["opaque_fill"], "current": {"queue_status": "ready_for_review"}}
    out = page(r"""
openRow(30);
press('Enter');
await flush();
answer(requests('/api/select_image')[0], __OPAQUE__, 500);   // an older server that still allowed it
await flush();
out.opaque = [document.querySelectorAll('.rv-jobs__anyway').length, jobsText()];
openRow(31);
press('Enter');
await flush();
answer(requests('/api/select_image')[1], { status: 'failed', error_code: 'quality_flags', error: 'q', publish_anyway_allowed: true,
    quality_flags: ['alpha_haze', 'kept_shadow'], current: { queue_status: 'ready_for_review' } }, 500);
await flush();
document.querySelector('.rv-jobs__anyway').click();
await flush();
out.confirm = confirms.slice(-1)[0];
answer(requests('/api/select_image')[2], { status: 'success', image_link: 'https://res.cloudinary.com/demo/b.png',
    isolated: false, sheet: 'written', published_anyway: true, warning: 'quality_flags', warnings: ['quality_flags'],
    quality_flags: ['alpha_haze', 'kept_shadow'] });
await flush();
openRow(31);
out.approved_view = wsText();
""".replace("__OPAQUE__", js(refused)), tmp_path,
               fixture([picked(30, "Almarai Milk 1L"), picked(31, "Almarai Laban 1L")]), config={"row": 30})
    assert out["opaque"][0] == 0 and "ما انعتمدت" in out["opaque"][1]
    assert "الخلفية معزولة" not in out["confirm"]
    assert "هالة أو ضباب خفيف حول حواف المنتج" in out["confirm"] and "ظل المنتج سيبقى ظاهراً" in out["confirm"]
    assert "انعتمدت رغم ملاحظات فحص القص:" in out["approved_view"]
    assert "هالة أو ضباب حول حواف المنتج" in out["approved_view"]


# ---------------------------------------------------------------------------
# #7 #8 #9 bulk ticks, focus and the viewport
# ---------------------------------------------------------------------------

@NEEDS_NODE
def test_a_bulk_tick_is_dropped_when_a_reload_changes_the_pick(tmp_path):
    prods = [picked(10, "Almarai Milk 1L", url="https://www.carrefouruae.com/a1.jpg"), picked(11, "Almarai Laban 1L")]
    out = page(r"""
await flush();
out.before = R.bulk.tickedKeys().map(k => S().byKey.get(k).product.row_number);
FIXTURE.products[0].curation_candidates = [{ image_url: 'https://www.carrefouruae.com/a2.jpg', status: 'preselected',
    is_selected: 1, title: 'a2', reasons: [], evidence: { brand: true, size: 'match', gtin: 'match' } }];
await R.loadData({ quiet: true });
await flush();
out.after = R.bulk.tickedKeys().map(k => S().byKey.get(k).product.row_number);
press('a', { shiftKey: true });
await flush();
out.sent = requests('/api/select_image').map(c => c.body.image_url);
""", tmp_path, fixture(prods), config={"mode": "bulk"})
    assert out["before"] == [10, 11]
    assert out["after"] == [11]                                  # a2 was never ticked by the reviewer
    assert out["sent"] == ["https://www.carrefouruae.com/p11.jpg"]


@NEEDS_NODE
def test_a_click_inside_a_card_moves_the_keyboard_focus_to_it(tmp_path):
    prods = [picked(10 + i, f"Prod {i}", **({"reasons": ["warn:foreign_store"]} if i == 2 else {})) for i in range(4)]
    out = page(r"""
await flush();
press('ArrowLeft');                                         // the focus ring on the first card
const box = document.querySelectorAll('.rv-card').find(c => c.getAttribute('data-key') === itemOf(12).key)
    .querySelector('input[type="checkbox"]');
out.ring = S().bulk.focus === itemOf(12).key;
box.click();                                                // the reviewer clicks another card's checkbox
await flush();
out.focus = S().bulk.focus === itemOf(12).key;
document.activeElement = document.body;
press('a');
await flush();
out.confirm = confirms.slice(-1)[0];
out.sent = requests('/api/select_image').map(c => c.body.row_number);
""", tmp_path, fixture(prods), config={"mode": "bulk"})
    assert out["ring"] is False and out["focus"] is True
    assert out["confirm"].startswith("«Prod 2»: تأكد قبل الاعتماد: الصورة من متجر خارج الإمارات")
    assert out["sent"] == ["12"]


@NEEDS_NODE
def test_shift_a_takes_only_cards_whose_picture_the_reviewer_has_seen(tmp_path):
    prods = [picked(100 + i, f"Prod {i}") for i in range(20)]
    out = page(r"""
const rowOf = node => S().byKey.get(node.closest('.rv-card').getAttribute('data-key')).product.row_number;
ioVisible = node => rowOf(node) < 108;                      // eight cards on screen
R.setMode('bulk');
await flush();
out.ticked = R.bulk.tickedKeys().length;
out.label = document.getElementById('rvBulkApprove').textContent;
document.getElementById('rvBulkEligible').click();          // «select the pre-selected ones»: still only the seen
await flush();
document.getElementById('rvBulkEligible').click();
await flush();
for (const it of S().items) {                               // the reviewer ticks every card (each click redraws)
    const b = document.querySelectorAll('.rv-card input[type="checkbox"]').find(x => x.getAttribute('data-select') === it.key);
    if (b && !b.checked && !b.disabled) b.click();
}
await flush();
out.note = document.querySelector('.rv-bulkbar__note').textContent;
press('a', { shiftKey: true });
await flush();
out.confirm = confirms.slice(-1)[0];
out.jobs = S().jobs.state().jobs.map(j => parseInt(j.ctx.row_number, 10));
ioVisible = () => true;                                     // the reviewer scrolls down
ioRefresh();
await flush();
out.later = R.bulk.tickedKeys().length;
""", tmp_path, fixture(prods))
    assert out["ticked"] == 8 and out["label"] == "اعتماد 8 صور بلا تحذير"
    assert "12 مقترحة بلا تحذير ما ظهرت صورتها لك بعد" in out["note"]
    assert "12 مقترحة ما ظهرت صورتها لك بعد، فما رح تنعتمد هلق" in out["confirm"]
    assert out["jobs"] == list(range(100, 108))
    assert out["later"] == 12                                   # ticked by the reviewer, approvable once seen


# ---------------------------------------------------------------------------
# #10 «size / variant unverified» for rows saved before the server computed them
# ---------------------------------------------------------------------------

@NEEDS_NODE
def test_derived_unverified_warnings_follow_the_servers_rule(tmp_path):
    out = run(r"""
const mk = (size, name, ev, extra, prod) => Object.assign({ row_number: 5, product_name: name || 'Almarai Fresh Milk',
    brand: 'Almarai', size: size, curation_candidates: [Object.assign({ image_url: 'https://x/a.jpg', status: 'preselected',
    is_selected: 1, reasons: ['vlm:MATCH'], evidence: ev }, extra || {})] }, prod || {});
const cases = {
    ascii_size_unknown: mk('1L', null, { size: 'unknown' }),
    arabic_digits: mk('١ لتر', null, { size: 'unknown' }),
    size_only_in_name: mk('', 'Almarai Fresh Milk 1L', { size: 'unknown' }),
    pack_unconfirmed: mk('6x330ml', 'Pepsi 6x330ml', { size: 'match', pack: 'unknown' }),
    pack_read_on_label: mk('6x330ml', 'Pepsi 6x330ml', { size: 'match' }, { vlm: { pack_count: 6 } }),
    no_evidence: mk('1L', null, {}),
    no_size_anywhere: mk('', 'Almarai Fresh Milk', {}),
    gtin_on_page: mk('1L', null, { gtin: 'match' }),
    variant_unconfirmed: mk('1L', 'Almarai Low Fat Milk 1L', { size: 'match', variants: [] }, null,
                            { sheet_states: { size: true, pack: 1, variants: ['fat'] } }),
    variant_partial: mk('1L', 'Almarai Low Fat Strawberry Milk 1L', { size: 'match', variants: ['fat'], variant_status: 'match' },
                        null, { sheet_states: { size: true, pack: 1, variants: ['fat', 'flavour'] } }),
    variant_on_label: mk('1L', 'Almarai Low Fat Milk 1L', { size: 'match' }, { vlm: { variant_match: 'yes' } },
                         { sheet_states: { size: true, pack: 1, variants: ['fat'] } }),
    server_says_no_size: mk('', 'Mystery 1L', {}, null, { sheet_states: { size: false, pack: 1, variants: [] } }),
};
for (const [k, p] of Object.entries(cases)) {
    const sel = R.storedSelected(p);
    out[k] = [sel.warnings, R.bulkEligible(sel)];
}
""", tmp_path, {"products": [], "queue": {"status": "success", "rows": []}})
    assert out["ascii_size_unknown"] == [["size_unverified"], False]
    assert out["arabic_digits"] == [["size_unverified"], False]
    assert out["size_only_in_name"] == [["size_unverified"], False]
    assert out["pack_unconfirmed"] == [["size_unverified"], False]
    assert out["pack_read_on_label"] == [[], True]
    assert out["no_evidence"] == [["size_unverified"], False]
    assert out["no_size_anywhere"] == [[], True]
    assert out["gtin_on_page"] == [[], True]
    assert out["variant_unconfirmed"] == [["variant_unverified"], False]
    assert out["variant_partial"] == [["variant_unverified"], False]
    assert out["variant_on_label"] == [[], True]
    assert out["server_says_no_size"] == [[], True]


def test_the_products_carry_what_the_sheet_states_as_the_search_reads_it():
    import cli_bridge
    from catalog_match.identity import build_sku_spec

    spec = build_sku_spec({"name": "Almarai Low Fat Milk 6x200ml", "brand": "Almarai", "size": ""})
    states = cli_bridge._sheet_states(spec)
    assert states["size"] is True and states["pack"] == 6 and "fat" in states["variants"]
    plain = cli_bridge._sheet_states(build_sku_spec({"name": "Almarai Milk", "brand": "Almarai"}))
    assert plain == {"size": False, "pack": 1, "variants": []}


# ---------------------------------------------------------------------------
# #11 #13 #14 #15 #17 honest texts
# ---------------------------------------------------------------------------

@NEEDS_NODE
def test_a_reject_with_research_that_kept_the_approval_says_so_and_queues_nothing(tmp_path):
    approved = product(20, "Almarai Milk 1L", existing_image_link="https://res.cloudinary.com/demo/milk.png",
                       needs_review=False, preselected=False)
    search = {"status": "review", "decision": "REVIEW_PRESELECTED", "sku_key": "key-20",
              "selected_image": {"url": "https://www.carrefouruae.com/n1.jpg", "status": "preselected"},
              "candidates": [scand("https://www.carrefouruae.com/n1.jpg", "preselected"),
                             scand("https://www.carrefouruae.com/n2.jpg")]}
    research = dict(search, candidates=[scand("https://www.carrefouruae.com/n3.jpg", "preselected")],
                    selected_image={"url": "https://www.carrefouruae.com/n3.jpg", "status": "preselected"},
                    rejection={"approval_kept": True, "candidates_left": 0, "queue_status": None},
                    candidates_saved=1, current={"queue_status": "completed", "approved_url": "https://res.cloudinary.com/demo/milk.png"})
    out = page(r"""
openRow(20);
ws().querySelector('.rv-research').click();
await flush();
answer(requests('/api/search')[0], __SEARCH__);
await flush();
press('x');
press('1');
await flush();
answer(requests('/api/reject_image')[0], __RESEARCH__);
await flush();
out.toast = toasts.slice(-1)[0].text;
out.bucket = itemOf(20).bucket;
out.rows = S().queue.rows.map(r => r.row_number);
""".replace("__SEARCH__", js(search)).replace("__RESEARCH__", js(research)), tmp_path,
               fixture([approved, picked(21, "Almarai Laban 1L")], rows=ready_rows([picked(21, "x")])),
               config={"row": 20, "filter": "all"})
    assert out["toast"] == "انرفضت الصورة وسجّلنا السبب. الصورة المعتمدة قبل بتضل زي ما هي."
    assert "بانتظار مراجعتك" not in out["toast"]
    assert out["bucket"] == "approved" and out["rows"] == [21]   # no fake «ready for review» row


@NEEDS_NODE
def test_no_explain_chips_on_a_rejected_pick_and_unknown_codes_are_plain_arabic(tmp_path):
    out = page(r"""
openRow(10);
press('2');                                                 // the image the system set aside
out.pick_chips = ws().querySelectorAll('.rv-pick .rv-explain__chip').length;
R.setMode('bulk');
await flush();
const warns = document.querySelectorAll('.rv-card__warn');
out.card = warns.map(w => [w.textContent, w.getAttribute('title')]);
""", tmp_path, fixture([product(10, "Almarai Milk 1L", curation_candidates=[
        cand("https://www.carrefouruae.com/a.jpg", "preselected", 1, vlm=VLM,
             reasons=["warn:duplicate_image", "warn:brand_new_code"]),
        cand("https://www.carrefouruae.com/b.jpg", "rejected", 0, reasons=["vlm:MISMATCH"], consensus_count=3)])]),
        config={"row": 10})
    assert out["pick_chips"] == 0
    assert out["card"][0][0].startswith("الصورة نفسها منشورة لمنتج آخر") and out["card"][0][1] == "duplicate_image"
    assert out["card"][1] == ["تحذير آخر على هالصورة: راجعها بعناية قبل الاعتماد", "brand_new_code"]


@NEEDS_NODE
def test_the_store_market_follows_the_servers_country_sections(tmp_path):
    from catalog_match.text_norm import store_market

    urls = ["https://www.luluhypermarket.com/en-kw/almarai-fresh-milk-1l/p/123",
            "https://www.luluhypermarket.com/en-qa/x/p/1", "https://www.luluhypermarket.com/en-ae/x/p/1",
            "https://www.noon.com/saudi-en/x/p/1", "https://www.noon.com/uae-en/x/p/1",
            "https://www.talabat.com/ar/kuwait/x", "https://www.carrefouruae.com/mafuae/en/saudi-coffee-200g/p/1",
            "https://www.luluhypermarket.com/ar-bh/x"]
    out = run(r"""
out.markets = __URLS__.map(u => [R.storeMarket(u).market, R.marketOf(u)]);
""".replace("__URLS__", js(urls)), tmp_path, {"products": [], "queue": {"status": "success", "rows": []}})
    js_markets = [m for m, _ in out["markets"]]
    assert js_markets == [store_market(u) for u in urls]       # the same rule as the server's foreign_store
    labels = [label for _, label in out["markets"]]
    assert labels[:2] == ["الكويت", "قطر"] and labels[2] == "الإمارات"
    assert labels[3] == "السعودية" and labels[4] == "الإمارات" and labels[5] == "الكويت"
    assert labels[6] == "الإمارات"                              # a product slug, not the Saudi store
    assert labels[7] == "البحرين"


def test_the_market_words_are_the_servers():
    from catalog_match import text_norm

    core = CORE.read_text(encoding="utf-8")
    words = set(re.findall(r"\b([a-z]+): '", core[core.index("const MARKET_WORDS"):core.index("const UAE_MARKET_WORDS")]))
    assert words == set(text_norm._FOREIGN_MARKETS) | set(text_norm._UAE_MARKETS)


@NEEDS_NODE
def test_the_chip_count_includes_the_products_kept_in_it(tmp_path):
    keep = product(9, "Almarai Milk 2L", curation_candidates=[
        cand("https://www.lulu.com/b1.jpg", "preselected", 1), cand("https://www.lulu.com/b2.jpg", "eligible", 0)])
    out = page(r"""
openRow(9);
press('x');
document.getElementById('rvRejectResearch').checked = false;
press('1');
await flush();
FIXTURE.products[1].curation_candidates = FIXTURE.products[1].curation_candidates.slice(1);
answer(requests('/api/reject_image')[0], { status: 'success', approval_kept: false, candidates_left: 1,
                                           queue_status: 'ready_for_review', current: {} });
await flush();
out.chip = document.querySelector('[data-filter="proposed"] .lq-filter__count').textContent;
out.listed = R.visibleItems().length;
""", tmp_path, fixture([picked(8, "Almarai Laban 1L"), keep]), config={"row": 9, "filter": "proposed"})
    assert out["chip"] == str(out["listed"]) == "2"


# ---------------------------------------------------------------------------
# A product without a barcode whose sheet write is pending still shows its approval (not «ما انبحث»)
# ---------------------------------------------------------------------------

def test_an_approval_of_a_product_without_a_barcode_is_found_by_its_key(tmp_path):
    import shutil as _shutil
    import test_products_cache as pc

    if _shutil.which("php") is None:
        pytest.skip("php is not installed")
    harness = pc.HARNESS
    harness = harness.replace(
        "public static function query() { return new \\FakeQuery([]); }",
        "public static function query() { return new \\FakeQuery($GLOBALS['RESOLVED'] ?? []); }")
    scenario_at = harness.index("    $out = [];")
    harness = harness[:scenario_at] + r"""
    $GLOBALS['RESOLVED'] = [(object) ['barcode' => '', 'sku_key' => 'sku-2', 'cloudinary_url' => 'https://res.cloudinary.com/x/milk.png',
                                      'verification_status' => 'human_approved', 'resolved_at' => null]];
    PythonBridge::$rows = [
        ['row_number' => 2, 'product_name' => 'Milk', 'brand' => 'Almarai', 'barcode' => '', 'sku_key' => 'sku-2',
         'existing_image_link' => '', 'needs_review' => false, 'has_error' => false, 'error_message' => ''],
        ['row_number' => 3, 'product_name' => 'Juice', 'brand' => 'Almarai', 'barcode' => '', 'sku_key' => 'sku-3',
         'existing_image_link' => '', 'needs_review' => false, 'has_error' => false, 'error_message' => ''],
    ];
    $r = (new ProductController())->getProductsJson(new Illuminate\Http\Request([]));
    echo json_encode(array_map(fn ($p) => [$p['row_number'], $p['cached_image'] ?? null], $r->data['products']));
}
"""
    root = tmp_path / "root"
    (root / "dashboard").mkdir(parents=True)
    script = tmp_path / "harness.php"
    script.write_text(harness, encoding="utf-8")
    import os
    env = dict(os.environ, HARNESS_ROOT=str(root), CONTROLLER_BASE=str(pc.CONTROLLERS / "Controller.php"),
               MATCHER_FILE=str(pc.MATCHER), PRODUCT_CONTROLLER=str(pc.PRODUCT))
    res = subprocess.run([_shutil.which("php"), str(script)], capture_output=True, text=True, timeout=60, env=env)
    assert res.returncode == 0, res.stdout + res.stderr
    assert json.loads(res.stdout) == [[2, "https://res.cloudinary.com/x/milk.png"], [3, None]]


def test_an_approval_is_found_by_the_products_key_before_its_barcode(tmp_path):
    """The approval in products-json is the one the server checks (cli_bridge._current_state reads it by sku_key):
    a barcode-first lookup missed an approval saved under another barcode text, or gave the row the approval of
    another product sharing its barcode cell, so the page sent an approved_url the server did not see as the
    approval and the reviewer's approval failed with already_approved."""
    import shutil as _shutil
    import test_products_cache as pc

    if _shutil.which("php") is None:
        pytest.skip("php is not installed")
    harness = pc.HARNESS
    harness = harness.replace(
        "public static function query() { return new \\FakeQuery([]); }",
        "public static function query() { return new \\FakeQuery($GLOBALS['RESOLVED'] ?? []); }")
    # the barcode lookup (keyBy('barcode')) as Laravel's: the last record of each barcode
    harness = harness.replace(
        "public function keyBy($k) { return []; }",
        "public function keyBy($k) { $o = []; foreach ($this->items as $i) { $o[$i->$k] = $i; } return $o; }")
    scenario_at = harness.index("    $out = [];")
    harness = harness[:scenario_at] + r"""
    $GLOBALS['RESOLVED'] = [
        (object) ['barcode' => '', 'sku_key' => 'sku-2', 'product_name' => 'Milk', 'cloudinary_url' => 'https://res.cloudinary.com/x/milk.png',
                  'verification_status' => 'human_approved', 'resolved_at' => null],
        (object) ['barcode' => 'N/A', 'sku_key' => 'sku-4', 'product_name' => 'Laban', 'cloudinary_url' => 'https://res.cloudinary.com/x/laban.png',
                  'verification_status' => 'human_approved', 'resolved_at' => null],
        (object) ['barcode' => '6281007031213', 'sku_key' => '', 'product_name' => 'Old', 'cloudinary_url' => 'https://res.cloudinary.com/x/old.png',
                  'verification_status' => 'legacy', 'resolved_at' => null],
    ];
    PythonBridge::$rows = [
        // the sheet has a barcode the approval was not saved with: found by its key
        ['row_number' => 2, 'product_name' => 'Milk', 'brand' => 'Almarai', 'barcode' => '6281007000011', 'sku_key' => 'sku-2',
         'existing_image_link' => '', 'needs_review' => false, 'has_error' => false, 'error_message' => ''],
        // another product with the same junk barcode cell: not Laban's approval
        ['row_number' => 3, 'product_name' => 'Juice', 'brand' => 'Almarai', 'barcode' => 'N/A', 'sku_key' => 'sku-3',
         'existing_image_link' => '', 'needs_review' => false, 'has_error' => false, 'error_message' => ''],
        ['row_number' => 4, 'product_name' => 'Laban', 'brand' => 'Almarai', 'barcode' => 'N/A', 'sku_key' => 'sku-4',
         'existing_image_link' => '', 'needs_review' => false, 'has_error' => false, 'error_message' => ''],
        // a legacy approval without a key is still found by its barcode
        ['row_number' => 5, 'product_name' => 'Old', 'brand' => 'Almarai', 'barcode' => '6281007031213', 'sku_key' => 'sku-5',
         'existing_image_link' => '', 'needs_review' => false, 'has_error' => false, 'error_message' => ''],
    ];
    $r = (new ProductController())->getProductsJson(new Illuminate\Http\Request([]));
    echo json_encode(array_map(fn ($p) => [$p['row_number'], $p['cached_image'] ?? null, $p['verification_status'] ?? null],
                               $r->data['products']));
}
"""
    root = tmp_path / "root"
    (root / "dashboard").mkdir(parents=True)
    script = tmp_path / "harness.php"
    script.write_text(harness, encoding="utf-8")
    import os
    env = dict(os.environ, HARNESS_ROOT=str(root), CONTROLLER_BASE=str(pc.CONTROLLERS / "Controller.php"),
               MATCHER_FILE=str(pc.MATCHER), PRODUCT_CONTROLLER=str(pc.PRODUCT))
    res = subprocess.run([_shutil.which("php"), str(script)], capture_output=True, text=True, timeout=60, env=env)
    assert res.returncode == 0, res.stdout + res.stderr
    assert json.loads(res.stdout) == [[2, "https://res.cloudinary.com/x/milk.png", "human_approved"],
                                      [3, None, None],
                                      [4, "https://res.cloudinary.com/x/laban.png", "human_approved"],
                                      [5, "https://res.cloudinary.com/x/old.png", "legacy"]]


@NEEDS_NODE
def test_an_approved_product_back_in_review_sends_its_approval_as_seen(tmp_path):
    """A product with a human approval whose sheet cell went empty (it was searched again and waits for review)
    shows its approval, and an approval of another picture sends that approval as expected_state.approved_url:
    the server sees a deliberate replacement, not an approval the reviewer never saw (already_approved)."""
    approved = "https://res.cloudinary.com/demo/image/upload/approved_63.png"
    prod = picked(63, "Almarai Milk 1L")
    prod.update(needs_review=False, cached_image=approved, verification_status="human_approved")
    out = page(r"""
openRow(63);
press('Enter');
await flush();
const c = requests('/api/select_image')[0].body;
out.sent = [c.image_url, c.expected_state, !!c.replace];
""", tmp_path, fixture([prod]), config={"row": 63})
    assert out["sent"] == ["https://www.carrefouruae.com/p63.jpg",
                           {"queue_status": "ready_for_review", "queue_updated_at": "2026-10-03 10:00:00",
                            "approved_url": approved, "queue_row": 63}, False]


@NEEDS_NODE
def test_the_reject_texts_say_what_the_server_does(tmp_path):
    out = page(r"""
openRow(9);
press('x');
out.note = document.querySelector('.rv-reasons__note').textContent;
""", tmp_path, fixture([picked(9, "Almarai Milk 2L")]), config={"row": 9})
    # C2: still waiting for review when images remain, back to the queue only when none is left
    assert "إذا ضل له صور ثانية بيضل بانتظار مراجعتك فيها، وإلا بيرجع للطابور" in out["note"]
    assert "المنتج بيرجع للطابور؛" not in out["note"]


# ---------------------------------------------------------------------------
# Facebook / Instagram «crawler» links: never requested, explained, never approvable
# ---------------------------------------------------------------------------

@NEEDS_NODE
def test_a_facebook_crawler_link_is_not_requested_and_cannot_be_approved(tmp_path):
    # lookaside.*/crawler/ answers only search-engine bots: the page must not fetch it (not even through the proxy)
    social = "https://lookaside.fbsbx.com/lookaside/crawler/media/?media_id=512510027658509"
    out = page(r"""
openRow(80);
await flush();
const pick = ws().querySelector('.rv-pick');
out.note = pick.querySelector('.rv-img-missing').textContent;
out.imgs = [...pick.querySelectorAll('img')].map(i => i.attrs.src).filter(s => s && s.includes('lookaside'));
out.can = R.single.canApprove();
press('Enter');
await flush();
out.sent = requests('/api/select_image').length;
""", tmp_path, fixture([picked(80, "Chaliyar Rice 5kg", url=social)]), config={"row": 80})
    assert "فيسبوك أو إنستغرام" in out["note"]
    assert out["imgs"] == []
    assert out["can"] is False and out["sent"] == 0


# ---------------------------------------------------------------------------
# Approvals on the server (approval_jobs): queued at once, the page can be left, the result settles as before
# ---------------------------------------------------------------------------

@NEEDS_NODE
def test_an_approval_queued_on_the_server_frees_the_page_and_settles_when_the_job_finishes(tmp_path):
    out = page(r"""
openRow(70);
await flush();
press('Enter');
await flush();
const sel = requests('/api/select_image')[0];
out.async = sel.body.async;
out.busyBefore = S().jobs.busy();
answer(sel, { status: 'queued', job_id: 7, existing: false }, 202);
await flush();
out.busyAfter = S().jobs.busy();                              // beforeunload no longer asks: the server has it
out.text = document.querySelector('.rv-jobs__text') ? document.querySelector('.rv-jobs__text').textContent : '';
approvalJobs = { status: 'success', jobs: [{ id: 7, status: 'running', http_status: null, result: null }] };
await sleep(40);
await flush();
out.stillRunning = S().local.get(itemOf(70).key);
approvalJobs = url => ({ status: 'success', jobs: url.includes('ids=7') ? [{ id: 7, status: 'done', http_status: 200,
    result: { status: 'success', image_link: 'https://res.cloudinary.com/demo/70.png', isolated: true, rows_written: [70] } }] : [] });
await sleep(60);
await flush();
out.settled = S().local.get(itemOf(70).key);
out.polls = requests('/api/approval-jobs').filter(c => c.url.includes('ids=7')).length;
""", tmp_path, fixture([picked(70, "Almarai Milk 1L"), picked(71, "Almarai Laban 1L")]),
               config={"row": 70, "approvalPollMs": 10})
    assert out["async"] == 1
    assert out["busyBefore"] is True and out["busyAfter"] is False
    assert "فيك تتنقّل أو تسكّر الصفحة" in out["text"]
    assert out["stillRunning"] == "approving"
    assert out["settled"] == "approved" and out["polls"] >= 2


@NEEDS_NODE
def test_a_page_opened_again_shows_the_server_s_running_approvals_as_approving(tmp_path):
    out = page(r"""
openRow(71);
await flush();
out.before = S().local.get(itemOf(70).key) || null;
approvalJobs = { status: 'success', jobs: [{ id: 9, sku_key: 'key-70', row_number: 70, label: 'Almarai Milk 1L', status: 'running' }] };
await R.loadData({ quiet: true });
await flush();
out.during = S().local.get(itemOf(70).key) || null;
approvalJobs = { status: 'success', jobs: [] };
await sleep(3200);
await flush();
out.after = S().local.get(itemOf(70).key) || null;
""", tmp_path, fixture([picked(70, "Almarai Milk 1L"), picked(71, "Almarai Laban 1L")]), config={"row": 71})
    assert out["before"] is None
    assert out["during"] == "approving"                         # not offered for a second approval meanwhile
    assert out["after"] is None
