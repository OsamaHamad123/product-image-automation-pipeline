"""Review screen: server approvals the reviewer is told about (approval_jobs), and an expired session.

* «اعتمادات ما زبطت»: approvals that failed on the server (GET /api/approval-jobs?failed=1, often after the reviewer
  left the page) are listed on top of the review list with the product and a plain Arabic reason; «افتح المنتج» opens
  it in single mode (from bulk too), «تجاهل» dismisses it (POST /api/approval-jobs/{id}/dismiss); the panel is
  hidden when there are none, and read again when the sidebar's failed_open count changes;
* a job the server no longer has (10 answers in a row without it) settles as failed, with a retry, instead of being
  followed forever;
* a 401 / 419 shows ONE persistent banner (sign in again) naming what was not sent, and the background loops stop
  hammering: following an approval backs off, the active-approvals poll stops.
"""

import pytest

from laqta_review_harness import NODE, run
from test_laqta_review_guards import fixture, js, picked

NEEDS_NODE = pytest.mark.skipif(NODE is None, reason="node is not installed")

EXPIRED = "{ __status: 401, status: 'error', error: 'انتهت الجلسة: سجّل دخول من جديد.' }"
FAILED = r"""
approvalJobs = url => url.includes('failed=1') ? { status: 'success', jobs: [
    { id: 31, sku_key: 'key-70', row_number: 70, label: 'Almarai Milk 1L', created_by: 'owner',
      finished_at: '2026-10-10 09:00:00', error: 'photoroom_402', error_code: 'photoroom_402', quality_flags: [] },
    { id: 32, sku_key: 'key-72', row_number: 72, label: 'Almarai Juice 1L', created_by: 'owner',
      finished_at: '2026-10-10 08:00:00', error: 'state_changed', error_code: 'state_changed', quality_flags: [] }
] } : { status: 'success', jobs: [] };
"""
PANEL = r"""
const panel = () => document.getElementById('rvFailed');
const panelText = () => panel().textContent;
const failedBtn = (attr, id) => panel().querySelector(`[${attr}="${id}"]`);
"""


def products():
    return [picked(70, "Almarai Milk 1L"), picked(71, "Almarai Laban 1L"), picked(72, "Almarai Juice 1L")]


def scenario(script, tmp_path, config, before=""):
    return run(before + PANEL + f"await boot({js(config)});\n" + script, tmp_path, fixture(products()))


@NEEDS_NODE
def test_failed_server_approvals_are_listed_with_their_reason_opened_and_dismissed(tmp_path):
    out = scenario(r"""
await flush();
out.hidden = panel().hidden;
out.text = panelText();
out.inQueue = !!panel().closest('.rv-queue');
failedBtn('data-failed-open', 31).click();
await flush();
out.opened = S().openKey === itemOf(70).key;
out.name = productName();
failedBtn('data-failed-dismiss', 32).click();
await flush();
const dismiss = requests('/api/approval-jobs/32/dismiss')[0];
out.dismissMethod = dismiss && dismiss.init.method;
out.afterOne = panelText();
failedBtn('data-failed-dismiss', 31).click();
await flush();
out.hiddenAfterAll = panel().hidden;
// the sidebar's count (batch-status approvals.failed_open) changes: the list is read again; the same count does not
const reads = () => requests('/api/approval-jobs').filter(c => c.url.includes('failed=1')).length;
out.readsAtBoot = reads();
runStatusListeners.forEach(fn => fn({ reviewCount: null, approvals: { failed_open: 0 } }));
runStatusListeners.forEach(fn => fn({ reviewCount: null, approvals: { failed_open: 0 } }));
await flush();
out.readsSame = reads();
runStatusListeners.forEach(fn => fn({ reviewCount: null, approvals: { failed_open: 1 } }));
await flush();
out.readsChanged = reads();
out.shownAgain = !panel().hidden;
""", tmp_path, {"row": 71}, before=FAILED)
    assert out["hidden"] is False
    assert "اعتمادات ما زبطت (2)" in out["text"]
    assert "Almarai Milk 1L" in out["text"] and "Almarai Juice 1L" in out["text"]
    assert "رصيد PhotoRoom خلص" in out["text"]                      # plain Arabic, not the code
    assert "تغيّرت حالة المنتج" in out["text"] and "photoroom_402" not in out["text"]
    assert out["inQueue"] is True
    assert out["opened"] is True and out["name"] == "Almarai Milk 1L"
    assert out["dismissMethod"] == "POST"
    assert "اعتمادات ما زبطت (1)" in out["afterOne"] and "Almarai Juice 1L" not in out["afterOne"]
    assert out["hiddenAfterAll"] is True
    assert out["readsAtBoot"] == 1
    assert out["readsSame"] == 1                                     # the first count is the one boot already read
    assert out["readsChanged"] == 2 and out["shownAgain"] is True


@NEEDS_NODE
def test_open_from_bulk_mode_switches_to_single_mode_on_that_product(tmp_path):
    out = scenario(r"""
await flush();
out.modeBefore = S().mode;
out.inBulk = !!panel().closest('.rv-bulk');
failedBtn('data-failed-open', 32).click();
await flush();
out.mode = S().mode;
out.opened = S().openKey === itemOf(72).key;
out.inQueue = !!panel().closest('.rv-queue');
""", tmp_path, {"mode": "bulk"}, before=FAILED)
    assert out["modeBefore"] == "bulk" and out["inBulk"] is True
    assert out["mode"] == "single" and out["opened"] is True and out["inQueue"] is True


@NEEDS_NODE
def test_no_failures_no_panel(tmp_path):
    out = scenario(r"""
await flush();
out.hidden = panel().hidden;
out.text = panelText();
""", tmp_path, {"row": 70})
    assert out["hidden"] is True and out["text"] == ""


@NEEDS_NODE
def test_a_job_the_server_no_longer_has_settles_as_failed_with_a_retry(tmp_path):
    out = scenario(r"""
openRow(70);
await flush();
press('Enter');
await flush();
approvalJobs = { status: 'success', jobs: [] };                    // the row was pruned (or never written)
answer(requests('/api/select_image')[0], { status: 'queued', job_id: 7, existing: false }, 202);
for (let i = 0; i < 60 && S().jobs.state().jobs.some(j => j.state === 'running'); i++) { await sleep(10); await flush(); }
const job = S().jobs.state().jobs[0];
out.state = job.state;
out.error = job.error;
out.polls = requests('/api/approval-jobs').filter(c => c.url.includes('ids=7')).length;
out.local = S().local.get(itemOf(70).key) || null;
out.jobsText = jobsText();
""", tmp_path, {"row": 70, "approvalPollMs": 10})
    assert out["state"] == "failed"
    assert "ما لقينا هالاعتماد على الخادم" in out["error"]
    assert out["polls"] == 10
    assert out["local"] != "approving"
    assert "ما لقينا هالاعتماد على الخادم" in out["jobsText"]


@NEEDS_NODE
def test_a_401_while_following_an_approval_shows_one_banner_and_polling_backs_off(tmp_path):
    out = scenario(r"""
openRow(70);
await flush();
press('Enter');
await flush();
approvalJobs = """ + EXPIRED + r""";
answer(requests('/api/select_image')[0], { status: 'queued', job_id: 7, existing: false }, 202);
await sleep(60);
await flush();
const polls = () => requests('/api/approval-jobs').filter(c => c.url.includes('ids=7')).length;
out.pollsFirst = polls();
await sleep(200);
await flush();
out.pollsLater = polls();
await R.requestJson('/api/approval-jobs?ids=7');                   // another 401: still the same one banner
await flush();
out.banners = document.querySelectorAll('#lqSessionExpired').length;
const banner = document.getElementById('lqSessionExpired');
out.text = banner.textContent;
out.href = banner.querySelector('a').getAttribute('href');
out.expired = R.sessionExpired();
// the active-approvals poll (a page opened again) does not ask while the session is expired (every call answers 401)
FIXTURE.status = { '/api/products-json': 401, '/api/review/queue-state': 401 };
const active = requests('/api/approval-jobs').filter(c => c.url.includes('active=1')).length;
await R.loadData({ quiet: true });
await flush();
out.activeAsked = requests('/api/approval-jobs').filter(c => c.url.includes('active=1')).length - active;
out.stillExpired = R.sessionExpired();
// signed in again from another tab: the next answer that works takes the banner away
FIXTURE.status = {};
approvalJobs = { status: 'success', jobs: [{ id: 7, status: 'done', http_status: 200,
    result: { status: 'success', image_link: 'https://res.cloudinary.com/demo/70.png', rows_written: [70] } }] };
await R.requestJson('/api/approval-jobs?ids=7');
await flush();
out.restored = !R.sessionExpired() && !document.getElementById('lqSessionExpired');
""", tmp_path, {"row": 70, "approvalPollMs": 10})
    assert out["expired"] is True
    assert out["banners"] == 1
    assert "انتهت الجلسة. سجّل دخول من جديد لتكمّل" in out["text"]
    assert out["href"] == "/catalog"                                  # RequireLogin sends it to /login and back here
    assert out["pollsFirst"] == 1 and out["pollsLater"] == 1           # backs off (30 s) instead of every 10 ms
    assert out["activeAsked"] == 0 and out["stillExpired"] is True
    assert out["restored"] is True


@NEEDS_NODE
def test_the_banner_names_the_approvals_that_were_not_sent(tmp_path):
    out = scenario(r"""
await flush();
approvalJobs = """ + EXPIRED + r""";
await R.requestJson('/api/approval-jobs?active=1');                 // e.g. a background poll: 401
out.expiredAtBoot = R.sessionExpired();
out.pendingBefore = document.querySelector('[data-lq-session-pending]').hidden;
openRow(71);
await flush();
press('Enter');                                                    // held for «تراجع»: not sent yet
await flush();
const pending = document.querySelector('[data-lq-session-pending]');
out.pendingHidden = pending.hidden;
out.pending = pending.textContent;
out.banners = document.querySelectorAll('#lqSessionExpired').length;
""", tmp_path, {"row": 71, "approveUndoMs": 100000})
    assert out["expiredAtBoot"] is True and out["pendingBefore"] is True
    assert out["pendingHidden"] is False and "Almarai Laban 1L" in out["pending"]
    assert "اعتمده من جديد بعد الدخول" in out["pending"]
    assert out["banners"] == 1
