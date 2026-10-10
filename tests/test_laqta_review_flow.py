"""Review screen flow (feat/review-ux-batch4): fewer stalls for a reviewer approving images all day.

* a picture that did not load is asked for again twice (a new r=n each time) before «ما قدرنا نعرض الصورة» shows,
  with «جرّب مرة تانية»; once it loads, approving is possible again;
* a reject without «دوّر على بدائل» works like an approval: background queue, the next product at once, «تراجع» in
  the toast (no question), a failure in the approvals panel with «أعد المحاولة», no reload of the whole list;
* Enter while the picture is still loading approves once it has been shown, never a product the reviewer has not
  seen; any other reason shows beside the approve button; a mouse click in the list moves the focus to the workspace;
* single mode updates only the changed row of the list; list thumbnails are small (?w=96, or a Cloudinary transform);
* bulk mode moves the focus and ticks cards without redrawing the grid;
* no raw English codes as tooltips; the undo countdown is hidden from screen readers.
"""

import json
from pathlib import Path

import pytest

from laqta_review_harness import NODE, run

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "dashboard" / "public" / "js" / "review" / "app.js"

NEEDS_NODE = pytest.mark.skipif(NODE is None, reason="node is not installed")

EV = {"brand": True, "size": "match", "gtin": "match", "source_class": "uae_retailer", "tier": 1}
VLM = {"decision": "MATCH", "brand_match": "yes", "size_match": "yes", "variant_match": "yes", "view": "front_packshot"}
CLOUD = "https://res.cloudinary.com/demo/image/upload/v1712345678/products/p73.png"


def js(value):
    return json.dumps(value, ensure_ascii=False)


def cand(url, status="eligible", selected=0, **extra):
    row = {"image_url": url, "status": status, "is_selected": selected, "title": url.rsplit("/", 1)[-1], "reasons": [],
           "evidence": dict(EV)}
    row.update(extra)
    return row


def url_of(row):
    return f"https://www.carrefouruae.com/p{row}.jpg"


def picked(row, name, url=None):
    url = url or url_of(row)
    return {"row_number": row, "product_name": name, "brand": "Almarai", "barcode": "", "sku_key": f"key-{row}",
            "size": "1L", "product_name_ar": "", "brand_ar": "", "category": "Dairy", "sub_category": "", "origin": "",
            "existing_image_link": "", "needs_review": True, "preselected": True, "search_query": f"{name} Almarai",
            "curation_candidates": [cand(url, "preselected", 1, vlm=VLM)]}


def fixture(products):
    rows = [{"row_number": p["row_number"], "sku_key": p["sku_key"], "status": "ready_for_review", "failure_code": None,
             "product_name": p["product_name"], "brand": p["brand"], "updated_at": "2026-10-03 10:00:00"} for p in products]
    return {"products": products, "queue": {"status": "success", "ready_for_review": len(rows), "rows": rows}}


THREE = [picked(70, "Almarai Milk 1L"), picked(71, "Almarai Laban 1L"), picked(72, "Almarai Juice 1L")]


def page(scenario, tmp_path, fx=None, config=None):
    return run(f"await boot({js(config or {})});\n" + scenario, tmp_path, fx or fixture(THREE))


# ---------------------------------------------------------------------------
# 1. A picture that did not load is asked for again before the page gives up on it
# ---------------------------------------------------------------------------

@NEEDS_NODE
def test_a_failed_picture_is_retried_twice_then_offers_a_retry_button_and_approves_once_it_loads(tmp_path):
    out = page(r"""
imageFails.add('https://www.carrefouruae.com/p70.jpg');
openRow(70);
await flush();
const img = () => ws().querySelector('.rv-pick img');
out.first = [img().getAttribute('src'), img().getAttribute('data-img-state'), approveBtn().title,
             !!ws().querySelector('.rv-pick .rv-img-missing')];
await sleep(30);
await flush();
out.second = [img().getAttribute('src'), img().getAttribute('data-img-state')];
await sleep(70);
await flush();
const pick = ws().querySelector('.rv-pick');
out.note = [pick.querySelector('.rv-img-missing__text').textContent, pick.querySelector('.rv-img-retry').textContent,
            pick.querySelectorAll('img').length];
out.blocked = [R.single.canApprove(), approveBtn().title];
press('Enter');
await flush();
out.sent_blocked = requests('/api/select_image').length;
imageFails.clear();                                         // the store answers again
pick.querySelector('.rv-img-retry').click();
await flush();
out.after = [img().getAttribute('src'), img().hasAttribute('data-img-state'), R.single.canApprove(), approveBtn().disabled,
             !!pick.querySelector('.rv-img-missing')];
press('Enter');
await flush();
out.sent = requests('/api/select_image').map(c => c.body.row_number);
""", tmp_path, config={"row": 71, "imageRetryMs": [20, 40]})
    proxied = "/api/image-proxy?url=https%3A%2F%2Fwww.carrefouruae.com%2Fp70.jpg"
    # the first two errors are retries: the picture is still «loading», never «could not be shown»
    assert out["first"] == [proxied, "retrying", "الصورة لسا عم تتحمّل", False]
    assert out["second"] == [proxied + "&r=1", "retrying"]
    assert out["note"] == ["ما قدرنا نعرض الصورة", "جرّب مرة تانية", 0]
    assert out["blocked"][0] is False and "ما قدرنا نعرض هالصورة" in out["blocked"][1]
    assert out["sent_blocked"] == 0
    assert out["after"] == [proxied + "&r=3", False, True, False, False]
    assert out["sent"] == ["70"]


@NEEDS_NODE
def test_a_retry_that_loads_makes_the_pick_approvable_and_a_url_without_a_query_gets_one(tmp_path):
    out = page(r"""
imageFails.add('__CLOUD__');
openRow(73);
await flush();
const img = () => ws().querySelector('.rv-pick img');
out.first = [img().getAttribute('src'), R.single.canApprove()];
imageFails.clear();
await sleep(40);
await flush();
out.loaded = [img().getAttribute('src'), R.single.canApprove(), !!ws().querySelector('.rv-pick .rv-img-missing')];
""".replace("__CLOUD__", CLOUD), tmp_path, fixture([picked(70, "Almarai Milk 1L"), picked(73, "Almarai Ghee", CLOUD)]),
               config={"row": 70, "imageRetryMs": [20, 40]})
    assert out["first"] == [CLOUD, False]
    assert out["loaded"] == [CLOUD + "?r=1", True, False]


# ---------------------------------------------------------------------------
# 2. A reject without a new search works like an approval
# ---------------------------------------------------------------------------

REJECT_START = r"""
openRow(70);
press('x');
document.getElementById('rvRejectResearch').checked = false;
press('1');
await flush();
const reject = requests('/api/reject_image')[0];
"""


@NEEDS_NODE
def test_a_reject_without_research_moves_on_at_once_and_settles_in_the_background(tmp_path):
    out = page(REJECT_START + r"""
out.sent = [reject.body.row_number, reject.body.research, reject.body.image_url, reject.init.keepalive || false];
out.moved_on = [productName(), S().ws.state, itemOf(70).bucket];
out.row = listButton(70).querySelector('.rv-chip').textContent;
const t = toasts.find(x => x.action);
out.toast = [t.text, t.action.label, t.variant];
out.panel = jobsText();
const loads = requests('/api/products-json').length;
answer(reject, { status: 'success', approval_kept: false, candidates_left: 0, queue_status: 'pending',
                 current: { queue_status: 'pending' } });
await flush();
out.after = [itemOf(70).bucket, S().today.rejected, requests('/api/products-json').length - loads, productName()];
openRow(70);
out.back = [wsText().includes('رجع للطابور'), R.single.canApprove()];
""", tmp_path, config={"row": 70})
    assert out["sent"] == ["70", False, url_of(70), False]
    assert out["moved_on"] == ["Almarai Laban 1L", "results", "rejecting"]   # the next product, like an approval
    assert out["row"] == "جاري الرفض"
    assert out["toast"][1] == "تراجع" and "Almarai Milk 1L" in out["toast"][0]
    assert "ما انرفضت" not in out["panel"]
    # the server's answer updates the product; the whole list is not read again
    assert out["after"] == ["rejected", 1, 0, "Almarai Laban 1L"]
    assert out["back"][0] is True


@NEEDS_NODE
def test_undo_in_the_reject_toast_takes_the_rejection_back_without_a_question(tmp_path):
    out = page(REJECT_START + r"""
answer(reject, { status: 'success', approval_kept: false, candidates_left: 0, queue_status: 'pending' });
await flush();
const asked = confirms.length;
toasts.find(x => x.action).action.onClick();
await flush();
const undo = requests('/api/review/undo-reject');
out.undo = undo.map(c => [c.body.image_url, String(c.body.row_number), c.body.sku_key]);
out.state = [confirms.length - asked, productName(), itemOf(70).bucket, pickUrl()];
""", tmp_path, config={"row": 70})
    assert out["undo"] == [[url_of(70), "70", "key-70"]]
    assert out["state"] == [0, "Almarai Milk 1L", "proposed", url_of(70)]   # back on the product, as it was


@NEEDS_NODE
def test_undo_while_the_reject_is_still_running_waits_for_it(tmp_path):
    out = page(REJECT_START + r"""
toasts.find(x => x.action).action.onClick();
await flush();
out.before = requests('/api/review/undo-reject').length;
answer(reject, { status: 'success', approval_kept: false, candidates_left: 0, queue_status: 'pending' });
await flush();
out.after = requests('/api/review/undo-reject').map(c => c.body.image_url);
""", tmp_path, config={"row": 70})
    assert out["before"] == 0
    assert out["after"] == [url_of(70)]


@NEEDS_NODE
def test_a_failed_background_reject_shows_in_the_panel_and_can_be_retried(tmp_path):
    out = page(REJECT_START + r"""
answer(reject, { status: 'error', error: 'sheet write failed' }, 500);
await flush();
out.panel = [jobsText().includes('ما انرفضت'), document.querySelectorAll('#rvJobs button').map(b => b.textContent)];
out.bucket = itemOf(70).bucket;
openRow(70);
out.pick = pickUrl();                                       // the image is back on the product
document.querySelectorAll('#rvJobs button').find(b => b.textContent === 'أعد المحاولة').click();
await flush();
out.retry = [requests('/api/reject_image').length, itemOf(70).bucket, R.single.canApprove()];
answer(requests('/api/reject_image')[1], { status: 'success', approval_kept: false, candidates_left: 0, queue_status: 'pending' });
await flush();
out.done = itemOf(70).bucket;
""", tmp_path, config={"row": 70})
    assert out["panel"][0] is True and "أعد المحاولة" in out["panel"][1]
    assert out["bucket"] == "proposed"
    assert out["pick"] == url_of(70)
    assert out["retry"] == [2, "rejecting", False]
    assert out["done"] == "rejected"


@NEEDS_NODE
def test_a_reject_with_research_still_waits_on_the_product(tmp_path):
    out = page(r"""
openRow(70);
press('x');
document.getElementById('rvRejectResearch').checked = true;
press('1');
await flush();
out.state = [productName(), S().ws.state, requests('/api/reject_image')[0].body.research, S().jobs.state().jobs.length];
""", tmp_path, config={"row": 70})
    assert out["state"] == ["Almarai Milk 1L", "rejecting", True, 0]


def with_alts(row, name, pick=True, n=3):
    """A product with n candidates: the first is the system's pick (pick=True), or none is."""
    urls = [f"https://www.lulu.com/{row}-{i}.jpg" for i in range(1, n + 1)]
    p = picked(row, name)
    p["preselected"] = pick
    p["curation_candidates"] = [cand(u, "preselected" if pick and i == 0 else "eligible", 1 if pick and i == 0 else 0, vlm=VLM)
                                for i, u in enumerate(urls)]
    return p, urls


ALT_PRODUCT, ALT_URLS = with_alts(70, "Almarai Milk 1L")
NOPICK_PRODUCT, NOPICK_URLS = with_alts(70, "Almarai Milk 1L", pick=False)
LAST_PRODUCT, LAST_URLS = with_alts(70, "Almarai Milk 1L", pick=False, n=1)

REJECT_SHOWN = r"""
press('x');
document.getElementById('rvRejectResearch').checked = false;
press('1');
await flush();
"""


@NEEDS_NODE
def test_rejecting_an_alternative_stays_on_the_product_and_the_pick_can_be_approved_at_once(tmp_path):
    out = page(r"""
press('2');                                                 // the reviewer looks at an alternative: wrong
""" + REJECT_SHOWN + r"""
const reject = requests('/api/reject_image')[0];
out.state = [productName(), pickUrl(), altUrls(), itemOf(70).bucket, reject.body.image_url, reject.body.research];
out.toast = toasts.find(x => x.action).action.label;
press('Enter');                                             // then approves the pick while the reject is still running
await flush();
// queued at once; sent after the reject (one request at a time per product, jobs.js conflictKey)
out.queued = [itemOf(70).bucket, productName(), requests('/api/select_image').length];
answer(reject, { status: 'success', approval_kept: false, candidates_left: 2, queue_status: 'pending' });
await flush();
out.approve = requests('/api/select_image').map(c => [c.body.row_number, c.body.image_url]);
out.after = [itemOf(70).bucket, S().jobs.state().jobs.filter(j => j.state === 'failed').length];
""", tmp_path, fixture([ALT_PRODUCT, picked(71, "Almarai Laban 1L")]), config={"row": 70})
    assert out["state"] == ["Almarai Milk 1L", ALT_URLS[0], [ALT_URLS[0], ALT_URLS[2]], "proposed", ALT_URLS[1], False]
    assert out["toast"] == "تراجع"
    assert out["queued"] == ["approving", "Almarai Laban 1L", 0]
    assert out["approve"] == [["70", ALT_URLS[0]]]
    # the reject's answer does not cover the approval in flight with «back to the queue»
    assert out["after"] == ["approving", 0]


@NEEDS_NODE
def test_rejecting_one_of_several_images_without_a_pick_shows_the_next_one(tmp_path):
    out = page(r"""
press('1');
""" + REJECT_SHOWN + r"""
out.state = [productName(), pickUrl(), altUrls()];
""", tmp_path, fixture([NOPICK_PRODUCT, picked(71, "Almarai Laban 1L")]), config={"row": 70})
    assert out["state"] == ["Almarai Milk 1L", NOPICK_URLS[1], NOPICK_URLS[1:]]


@NEEDS_NODE
def test_rejecting_the_pick_or_the_last_image_moves_on(tmp_path):
    pick = page(REJECT_SHOWN + r"""
out.name = productName();
""", tmp_path, fixture([ALT_PRODUCT, picked(71, "Almarai Laban 1L")]), config={"row": 70})
    last = page(r"""
press('1');
""" + REJECT_SHOWN + r"""
out.name = productName();
out.bucket = itemOf(70).bucket;
""", tmp_path, fixture([LAST_PRODUCT, with_alts(71, "Almarai Laban 1L", pick=False)[0]]), config={"row": 70})
    assert pick["name"] == "Almarai Laban 1L"
    assert last == {"name": "Almarai Laban 1L", "bucket": "rejecting"}


# ---------------------------------------------------------------------------
# 3. Enter while the picture is loading, and why Enter did nothing
# ---------------------------------------------------------------------------

@NEEDS_NODE
def test_enter_while_the_picture_loads_approves_once_it_has_been_shown(tmp_path):
    out = page(r"""
imageMode = 'hold';
openRow(71);
await sleep(70);                                            // the product has been on screen
press('Enter');
await flush();
const note = document.getElementById('rvApproveNote');
out.waiting = [requests('/api/select_image').length, note.hidden, note.textContent];
releaseImages();
await flush();
out.just_shown = requests('/api/select_image').length;     // not before the picture has been seen 50 ms
await sleep(120);
await flush();
out.sent = requests('/api/select_image').map(c => c.body.row_number);
""", tmp_path, config={"row": 70, "approveSettleMs": 50})
    assert out["waiting"] == [0, False, "رح تنعتمد أول ما تظهر الصورة"]
    assert out["just_shown"] == 0
    assert out["sent"] == ["71"]


@NEEDS_NODE
def test_a_fast_enter_on_a_product_just_opened_is_not_kept(tmp_path):
    out = page(r"""
imageMode = 'hold';
openRow(71);
press('Enter');                                             // at once: the reviewer has not seen this product
await flush();
out.note = document.getElementById('rvApproveNote').textContent;
releaseImages();
await sleep(150);
await flush();
out.sent = requests('/api/select_image').length;
out.can = R.single.canApprove();
""", tmp_path, config={"row": 70, "approveSettleMs": 50})
    assert out["note"] == "الصورة لسا عم تتحمّل"
    assert out["sent"] == 0 and out["can"] is True


@NEEDS_NODE
def test_enter_kept_for_a_picture_is_dropped_when_another_product_opens(tmp_path):
    out = page(r"""
imageMode = 'hold';
openRow(71);
await sleep(70);
press('Enter');
openRow(72);
releaseImages();
await sleep(150);
await flush();
out.sent = requests('/api/select_image').length;
""", tmp_path, config={"row": 70, "approveSettleMs": 50})
    assert out["sent"] == 0


@NEEDS_NODE
def test_any_other_reason_enter_cannot_approve_shows_beside_the_button(tmp_path):
    out = page(r"""
imageFails.add('https://www.carrefouruae.com/p70.jpg');
openRow(70);
await flush();
press('Enter');
await flush();
const note = document.getElementById('rvApproveNote');
out.note = [note.hidden, note.textContent, note.getAttribute('role')];
out.sent = requests('/api/select_image').length;
""", tmp_path, config={"row": 70, "imageRetryMs": []})
    assert out["note"][0] is False and "ما قدرنا نعرض هالصورة" in out["note"][1] and out["note"][2] == "status"
    assert out["sent"] == 0


@NEEDS_NODE
def test_a_mouse_click_in_the_list_moves_the_focus_to_the_workspace(tmp_path):
    out = page(r"""
dispatch(listButton(71), { type: 'click', bubbles: true, detail: 1 });
await flush();
out.mouse = [document.activeElement === ws(), ws().getAttribute('tabindex'), productName()];
press('Enter');
await flush();
out.sent = requests('/api/select_image').map(c => c.body.row_number);
listButton(72).focus();
dispatch(listButton(72), { type: 'click', bubbles: true, detail: 0 });   // Enter / Space on the row itself
out.keyboard = document.activeElement === listButton(72);
""", tmp_path, config={"row": 70})
    assert out["mouse"] == [True, "-1", "Almarai Laban 1L"]
    assert out["sent"] == ["71"]
    assert out["keyboard"] is True


# ---------------------------------------------------------------------------
# 4. Single mode patches the changed row; list thumbnails are small
# ---------------------------------------------------------------------------

@NEEDS_NODE
def test_an_approval_patches_its_row_instead_of_redrawing_the_list(tmp_path):
    out = page(r"""
const row71 = listButton(71);
const row72 = listButton(72);
const count = () => document.getElementById('rvWaiting').textContent;
out.before = [count(), document.querySelector('[data-filter="proposed"] .lq-filter__count').textContent];
openRow(70);
press('Enter');
await flush();
out.after = [listButton(70), listButton(71) === row71, listButton(72) === row72, row71.isConnected, count(),
             document.querySelector('[data-filter="proposed"] .lq-filter__count').textContent];
""", tmp_path, config={"row": 70, "filter": "proposed"})
    assert out["before"] == ["3 بانتظار المراجعة", "3"]
    # the approved product leaves the chip; the other rows are the same nodes
    assert out["after"] == [None, True, True, True, "2 بانتظار المراجعة", "2"]


@NEEDS_NODE
def test_in_all_the_row_stays_and_shows_its_new_state(tmp_path):
    out = page(r"""
const row70 = listButton(70);
const row71 = listButton(71);
openRow(70);
press('Enter');
await flush();
out.row = [listButton(70) === row70, row70.getAttribute('data-bucket'), row70.querySelector('.rv-chip').textContent,
           listButton(71) === row71];
R.setFilter('proposed');                                    // a filter change still redraws the list
out.filtered = [listButton(70), listButton(71) === row71];
""", tmp_path, config={"row": 70, "filter": "all"})
    assert out["row"] == [True, "approving", "جاري الاعتماد", True]
    assert out["filtered"][0] is None and out["filtered"][1] is False


@NEEDS_NODE
def test_list_thumbnails_ask_for_a_small_picture(tmp_path):
    out = page(r"""
out.proxy = imgSrcs(listButton(70));
out.cloud = imgSrcs(listButton(73));
out.pick = imgSrcs(ws().querySelector('.rv-pick'));          // the workspace keeps the full picture
out.urls = [R.imageUrl('https://res.cloudinary.com/demo/image/upload/c_pad,w_800/v1/a.png', '/api/image-proxy', 96),
            R.imageUrl('https://res.cloudinary.com/demo/image/upload/v1/a.png?x=1', '/api/image-proxy', 96),
            R.imageUrl('https://res.cloudinary.com/demo/video/upload/v1/a.mp4', '/api/image-proxy', 96),
            R.imageUrl('https://www.lulu.com/a.jpg', '/api/image-proxy')];
""", tmp_path, fixture([picked(70, "Almarai Milk 1L"), picked(73, "Almarai Ghee", CLOUD)]), config={"row": 70})
    assert out["proxy"] == ["/api/image-proxy?url=https%3A%2F%2Fwww.carrefouruae.com%2Fp70.jpg&w=96"]
    assert out["cloud"] == ["https://res.cloudinary.com/demo/image/upload/c_limit,w_96,f_auto/v1712345678/products/p73.png"]
    assert out["pick"] == ["/api/image-proxy?url=https%3A%2F%2Fwww.carrefouruae.com%2Fp70.jpg"]
    # a Cloudinary URL with its own transformations (or a query, or not an image) is left as it is
    assert out["urls"] == ["https://res.cloudinary.com/demo/image/upload/c_pad,w_800/v1/a.png",
                           "https://res.cloudinary.com/demo/image/upload/v1/a.png?x=1",
                           "https://res.cloudinary.com/demo/video/upload/v1/a.mp4",
                           "/api/image-proxy?url=https%3A%2F%2Fwww.lulu.com%2Fa.jpg"]


# ---------------------------------------------------------------------------
# 5. Bulk mode: focus and ticks without redrawing the grid
# ---------------------------------------------------------------------------

@NEEDS_NODE
def test_bulk_focus_and_ticks_change_the_cards_in_place(tmp_path):
    out = page(r"""
R.setMode('bulk');
await flush();
const cards = () => document.querySelectorAll('.rv-card');
const before = cards();
const imgs = before.map(c => c.querySelector('img'));
const same = () => cards().length === before.length && cards().every((c, i) => c === before[i])
    && cards().every((c, i) => c.querySelector('img') === imgs[i]);
out.ticked0 = before.map(c => c.querySelector('input').checked);
press('ArrowLeft');
press('ArrowLeft');
out.focus = [same(), before.map(c => c.classList.contains('is-focused')), before[1].getAttribute('aria-current'),
             document.activeElement === before[1], before[0].hasAttribute('aria-current')];
press(' ');                                                 // untick the focused card
out.space = [same(), before[1].querySelector('input').checked, before[1].classList.contains('is-selected'),
             document.getElementById('rvBulkBar').querySelector('.rv-bulkbar__count').textContent];
before[2].querySelector('input').click();                   // the checkbox itself
out.box = [same(), before[2].querySelector('input').checked, before[2].classList.contains('is-selected'),
           before[2].classList.contains('is-focused'), before[1].classList.contains('is-focused'),
           document.getElementById('rvBulkApprove').textContent];
before[0].querySelector('.rv-card__body').click();          // a click in a card focuses it
out.click = [same(), before[0].classList.contains('is-focused')];
""", tmp_path, config={"mode": "bulk"})
    assert out["ticked0"] == [True, True, True]
    assert out["focus"] == [True, [False, True, False], "true", True, False]
    assert out["space"] == [True, False, False, "2 محددة من 3"]
    assert out["box"] == [True, False, False, True, False, "اعتماد صورة وحدة بلا تحذير"]
    assert out["click"] == [True, True]


@NEEDS_NODE
def test_bulk_approving_still_redraws_the_grid(tmp_path):
    out = page(r"""
R.setMode('bulk');
await flush();
const before = document.querySelectorAll('.rv-card');
press('ArrowLeft');
press('a');
await flush();
out.redrawn = document.querySelectorAll('.rv-card')[0] !== before[0];
out.jobs = S().jobs.state().jobs.map(j => j.ctx.row_number);
""", tmp_path, config={"mode": "bulk"})
    assert out["redrawn"] is True and out["jobs"] == ["70"]


# ---------------------------------------------------------------------------
# 6. Tooltips and screen readers
# ---------------------------------------------------------------------------

def test_no_raw_codes_as_tooltips_and_the_undo_countdown_is_not_read_out():
    app = APP.read_text(encoding="utf-8")
    assert "title: why.key" not in app and "title: c.key" not in app and "title: j.detail" not in app
    left = app[app.index("className: 'rv-undo__left'"):][:120]
    assert "'aria-hidden': 'true'" in left


@NEEDS_NODE
def test_the_undo_countdown_is_hidden_from_screen_readers(tmp_path):
    out = page(r"""
openRow(70);
press('Enter');
await flush();
const left = document.querySelector('#rvUndo .rv-undo__left');
out.left = [left.getAttribute('aria-hidden'), document.getElementById('rvUndo').getAttribute('aria-live')];
""", tmp_path, config={"row": 70, "approveUndoMs": 5000})
    assert out["left"] == ["true", "polite"]
