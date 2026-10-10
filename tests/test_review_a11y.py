"""Review screen accessibility and fewer steps (node DOM harness, plus the failed list's retry body through Laravel):

* «اعتمادات ما زبطت» → «أعد المحاولة» sends the same approval again (same image, same product identity, the
  expected_state sent the first time, async=1) from the list, without opening the product; the old failure is
  dismissed; a product that changed since is refused by the server and the reason is said in plain Arabic; a picture
  another reviewer rejected for that product offers no retry;
* opening the reject reasons moves the focus to the first reason; Esc (or «إلغاء») gives it back to «رفض»;
* moving on by itself (approve / reject / skip) or with ↑ ↓ is announced in a polite live region, the focus stays;
* the alternatives say their index (the 1–9 shortcut) and store, the list thumbnails are decorative;
* a smooth scroll is instant when the device asks for reduced motion;
* the shortcuts dialog (no text) has no aria-describedby pointing at nothing.
"""

import json

import pytest

from laqta_kernel import NEEDS_LARAVEL, sql, stub_env
from laqta_review_harness import NODE, run
from test_laqta_review_guards import cand, fixture, js, picked, product

NEEDS_NODE = pytest.mark.skipif(NODE is None, reason="node is not installed")

RETRY_BODY = {"image_url": "https://www.carrefouruae.com/old70.jpg", "row_number": "70", "sku_key": "key-70",
              "product_name": "Almarai Milk 1L", "brand": "Almarai", "barcode": "", "size": "1L",
              "expected_state": {"queue_status": "ready_for_review", "approved_url": None,
                                 "queue_updated_at": "2026-10-03 10:00:00", "queue_row": 70}}
FAILED = r"""
approvalJobs = url => url.includes('failed=1') ? { status: 'success', jobs: [
    { id: 31, sku_key: 'key-70', row_number: 70, label: 'Almarai Milk 1L', created_by: 'owner',
      finished_at: '2026-10-10 09:00:00', error: 'photoroom_402', error_code: 'photoroom_402', quality_flags: [],
      retry: __RETRY__ },
    { id: 33, sku_key: 'key-72', row_number: 72, label: 'Almarai Juice 1L', created_by: 'owner',
      finished_at: '2026-10-10 08:00:00', error: 'state_changed', error_code: 'state_changed', reason: 'image_rejected',
      quality_flags: [], retry: { image_url: 'https://www.carrefouruae.com/x72.jpg', sku_key: 'key-72', row_number: '72' } }
] } : { status: 'success', jobs: [] };
""".replace("__RETRY__", json.dumps(RETRY_BODY))
PANEL = r"""
const panel = () => document.getElementById('rvFailed');
const failedBtn = (attr, id) => panel().querySelector(`[${attr}="${id}"]`);
const announced = () => document.getElementById('rvAnnounce').textContent;
"""


def products():
    return [picked(70, "Almarai Milk 1L"), picked(71, "Almarai Laban 1L"), picked(72, "Almarai Juice 1L")]


def scenario(script, tmp_path, config, before="", fx=None):
    return run(before + PANEL + f"await boot({js(config)});\n" + script, tmp_path, fx or fixture(products()))


# ---------------------------------------------------------------------------
# «أعد المحاولة» in «اعتمادات ما زبطت»
# ---------------------------------------------------------------------------

@NEEDS_NODE
def test_retry_resends_the_same_approval_in_place_and_dismisses_the_old_failure(tmp_path):
    out = scenario(r"""
await flush();
out.retry31 = !!failedBtn('data-failed-retry', 31);
out.retry33 = !!failedBtn('data-failed-retry', 33);          // a picture another reviewer rejected: pick another one
out.label = failedBtn('data-failed-retry', 31).getAttribute('aria-label');
const openBefore = S().openKey;
failedBtn('data-failed-retry', 31).focus();
failedBtn('data-failed-retry', 31).click();
await flush();
const sent = requests('/api/select_image');
out.sent = sent.length;
out.body = sent[0] && sent[0].body;
out.dismissed = requests('/api/approval-jobs/31/dismiss').length;
out.stillListed = !!panel().querySelector('[data-failed-id="31"]');
out.sameOpen = S().openKey === openBefore;
out.local = S().local.get(itemOf(70).key) || null;
out.focusInPanel = panel().contains(document.activeElement);
// the product changed since: the server refuses it, and says why
answer(sent[0], { status: 'error', error_code: 'state_changed',
                  current: { queue_status: 'searching', approved_url: null } }, 409);
await flush();
const job = S().jobs.state().jobs[0];
out.state = job.state;
out.stale = !!job.stale;
out.toasts = toasts.map(t => t.text).join(' | ');
out.localAfter = S().local.get(itemOf(70).key) || null;
""", tmp_path, {"row": 71}, before=FAILED)
    assert out["retry31"] is True and out["retry33"] is False
    assert "Almarai Milk 1L" in out["label"]
    assert out["sent"] == 1
    body = out["body"]
    assert body["image_url"] == RETRY_BODY["image_url"]                     # the same image, not the one shown now
    assert (body["sku_key"], body["row_number"], body["product_name"]) == ("key-70", "70", "Almarai Milk 1L")
    assert body["expected_state"] == RETRY_BODY["expected_state"]           # what the reviewer was shown then
    assert body["async"] == 1 and "replace" not in body and "publish_anyway" not in body
    assert out["dismissed"] == 1 and out["stillListed"] is False
    assert out["sameOpen"] is True                                          # nothing opened
    assert out["local"] == "approving"
    assert out["focusInPanel"] is True                                      # the next failure's button, not body
    assert out["state"] == "failed" and out["stale"] is True
    assert "تغيّرت حالة المنتج" in out["toasts"] and "state_changed" not in out["toasts"]
    assert out["localAfter"] != "approving"


@NEEDS_NODE
def test_retry_of_a_product_no_longer_listed_says_so_and_sends_nothing(tmp_path):
    out = scenario(r"""
await flush();
failedBtn('data-failed-retry', 31).click();
await flush();
out.sent = requests('/api/select_image').length;
out.toasts = toasts.map(t => t.text).join(' | ');
""", tmp_path, {"row": 71}, before=FAILED,
        fx=fixture([picked(71, "Almarai Laban 1L"), picked(72, "Almarai Juice 1L")]))
    assert out["sent"] == 0
    assert "ما لقينا هالمنتج بالقائمة" in out["toasts"]


# ---------------------------------------------------------------------------
# Reject reasons: focus in, Esc gives it back
# ---------------------------------------------------------------------------

@NEEDS_NODE
def test_reject_reasons_take_the_focus_and_esc_gives_it_back_to_reject(tmp_path):
    out = scenario(r"""
await flush();
press('x');
await flush();
const a = document.activeElement;
out.focusReason = !!(a && a.classList && a.classList.contains('rv-reason'));
out.firstReason = a === document.querySelector('.rv-reasons .rv-reason');
press('Escape');
await flush();
out.closed = !S().reasonsOpen;
out.backToReject = document.activeElement === rejectBtn();
rejectBtn().click();
await flush();
document.querySelectorAll('.rv-reasons button').find(b => b.textContent.includes('إلغاء')).click();
await flush();
out.cancelBack = document.activeElement === rejectBtn();
// a reason chosen: the focus goes to the workspace (Enter there approves), not to a removed button
press('x');
await flush();
press('1');
await flush();
out.afterReason = document.activeElement === ws();
""", tmp_path, {"row": 70})
    assert out["focusReason"] is True and out["firstReason"] is True
    assert out["closed"] is True and out["backToReject"] is True
    assert out["cancelBack"] is True
    assert out["afterReason"] is True


# ---------------------------------------------------------------------------
# Live region: which product is open now
# ---------------------------------------------------------------------------

@NEEDS_NODE
def test_moving_on_is_announced_politely_without_moving_the_focus(tmp_path):
    out = scenario(r"""
await flush();
const region = document.getElementById('rvAnnounce');
out.live = region.getAttribute('aria-live');
out.atBoot = announced();
press('ArrowDown');
await flush();
out.down = announced();
press('ArrowUp');
await flush();
out.up = announced();
approveBtn().focus();
press('s');
await flush();
out.skip = announced();
out.focusKept = document.activeElement === approveBtn();
""", tmp_path, {"row": 70})
    assert out["live"] == "polite"
    assert out["atBoot"] == ""                                              # opening the page is not announced
    assert out["down"] == "المنتج الجاي: Almarai Laban 1L (صف 71)"
    assert out["up"] == "المنتج اللي قبل: Almarai Milk 1L (صف 70)"
    assert out["skip"] == "المنتج الجاي: Almarai Laban 1L (صف 71)"
    assert out["focusKept"] is True


@NEEDS_NODE
def test_auto_advance_after_approve_is_announced(tmp_path):
    out = scenario(r"""
await flush();
press('Enter');
await flush();
out.text = announced();
""", tmp_path, {"row": 70})
    assert out["text"] == "المنتج الجاي: Almarai Laban 1L (صف 71)"


# ---------------------------------------------------------------------------
# Image names
# ---------------------------------------------------------------------------

@NEEDS_NODE
def test_alternatives_name_their_index_and_store_and_list_thumbnails_are_decorative(tmp_path):
    p = product(80, "Almarai Milk 2L", curation_candidates=[
        cand("https://www.carrefouruae.com/p80.jpg", "preselected", 1),
        cand("https://www.noon.com/uae-en/a80.jpg"),
    ])
    out = scenario(r"""
await flush();
const alts = ws().querySelectorAll('.rv-alt');
out.alts = alts.map(b => ({ alt: b.querySelector('img') && b.querySelector('img').getAttribute('alt'),
                            numHidden: b.querySelector('.rv-alt__num').getAttribute('aria-hidden'),
                            store: b.querySelector('.rv-alt__store').textContent,
                            keys: b.getAttribute('aria-keyshortcuts') }));
const thumb = document.querySelector('.rv-item .rv-thumb');
out.thumbHidden = thumb.getAttribute('aria-hidden');
""", tmp_path, {"row": 80}, fx=fixture([p]))
    assert len(out["alts"]) >= 2
    for i, a in enumerate(out["alts"]):
        assert a["alt"] == f"صورة مقترحة {i + 1}"
        assert a["numHidden"] == "true" and a["keys"] == str(i + 1)
        assert a["store"]
    assert out["thumbHidden"] == "true"


# ---------------------------------------------------------------------------
# Reduced motion, the shortcuts dialog
# ---------------------------------------------------------------------------

@NEEDS_NODE
def test_smooth_scroll_is_instant_when_reduced_motion_is_asked(tmp_path):
    out = scenario(r"""
const seen = [];
const node = { scrollIntoView(o) { seen.push(o.behavior || null); } };
R.scrollIntoView(node, { block: 'nearest', behavior: 'smooth' });
globalThis.matchMedia = q => ({ matches: /prefers-reduced-motion: reduce/.test(q) });
R.scrollIntoView(node, { block: 'nearest', behavior: 'smooth' });
out.seen = seen;
""", tmp_path, {"row": 70})
    assert out["seen"] == ["smooth", "auto"]


@NEEDS_NODE
def test_shortcuts_dialog_has_no_dangling_describedby(tmp_path):
    out = scenario(r"""
await flush();
press('?');
await flush();
const box = document.querySelector('.rv-dialog');
out.describedby = box.getAttribute('aria-describedby');
out.text = !!document.getElementById('rvAskText');
""", tmp_path, {"row": 70})
    assert out["text"] is False and out["describedby"] is None


# ---------------------------------------------------------------------------
# Laravel: the failed list carries the request to resend
# ---------------------------------------------------------------------------

@NEEDS_LARAVEL
def test_failed_list_carries_the_retry_body_without_confirmations(mariadb_or_skip, tmp_path):
    from test_dashboard_login import run as http
    import approval_jobs

    db = mariadb_or_skip
    approval_jobs.ensure_schema()
    sql(db, "DELETE FROM approval_jobs")
    params = dict(RETRY_BODY, replace=True, publish_anyway=True, created_by="owner", phash="x")
    try:
        sql(db, "INSERT INTO approval_jobs (id, kind, sku_key, `row_number`, label, params_json, status, result_json, "
                "http_status, finished_at) VALUES (701, 'select', 'key-70', 70, 'Milk', %s, 'failed', %s, 500, NOW()), "
                "(702, 'select', 'key-71', 71, 'Laban', '{}', 'failed', %s, 500, NOW())",
            (json.dumps(params), json.dumps({"status": "failed", "error": "photoroom_402", "error_code": "photoroom_402"}),
             json.dumps({"status": "failed", "error_code": "state_changed", "reason": "image_rejected"})))
        environ, _calls = stub_env(tmp_path)
        failed, = http(environ, [["GET", "/api/approval-jobs?failed=1"]])
        jobs = {j["id"]: j for j in json.loads(failed["body"])["jobs"]}
    finally:
        sql(db, "DELETE FROM approval_jobs")
    retry = jobs[701]["retry"]
    assert retry["image_url"] == RETRY_BODY["image_url"] and retry["sku_key"] == "key-70"
    assert retry["expected_state"] == RETRY_BODY["expected_state"]
    for key in ("replace", "publish_anyway", "created_by", "phash"):
        assert key not in retry
    assert jobs[702]["retry"] is None and jobs[702]["reason"] == "image_rejected"
