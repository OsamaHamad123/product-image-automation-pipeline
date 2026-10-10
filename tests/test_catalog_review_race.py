"""The review screen (/catalog, dashboard/public/js/review): a reviewer can never publish the wrong product's image,
or the same image twice.

The page's scripts run under node against a small DOM shim (tests/laqta_review_harness.py) with a scripted fetch:
each test drives the page the way a reviewer does (open a product from the list, press keys, click buttons) and
holds or answers each request itself.

* a search answer for a product that is no longer open, or for an older search, is dropped: it never touches the
  workspace, and a search is aborted when its product is left;
* results are bound to the product they were found for: approve / reject / upload send that identity (and what the
  reviewer saw), never the words typed into the search box;
* an approval goes to the background queue: the same product is never queued twice, requests go out one at a time,
  and after it the workspace says "تم الاعتماد." with no way to approve again;
* keys 1-9 only select an image; Enter approves only the visible selected image; modifier keys are ignored;
* a product that already has a final image shows it, with "دوّر على صورة بديلة", and starts no (paid) search.
"""

import pytest

from laqta_review_harness import NODE, run

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")

APPROVED = "تم الاعتماد."
CHANGED = "المنتج تغيّر أثناء المراجعة؛ افتحه من جديد"

A = {"row_number": 5, "product_name": "Almarai Fresh Milk 1L", "brand": "Almarai", "barcode": "", "sku_key": "key-a",
     "size": "1L", "product_name_ar": "حليب المراعي 1 لتر", "brand_ar": "المراعي", "category": "Dairy",
     "sub_category": "Milk", "origin": "KSA", "existing_image_link": "", "needs_review": False,
     "search_query": "Almarai Fresh Milk 1L Almarai"}
B_URLS = ["https://www.lulu.com/b1.jpg", "https://www.lulu.com/b2.jpg", "https://www.lulu.com/b3.jpg"]
B = {"row_number": 9, "product_name": "Almarai Fresh Milk 2L", "brand": "Almarai", "barcode": "6281007000024",
     "sku_key": "06281007000024", "size": "2L", "product_name_ar": "حليب المراعي 2 لتر", "brand_ar": "المراعي",
     "category": "Dairy", "sub_category": "Milk", "origin": "KSA", "existing_image_link": "", "needs_review": True,
     "preselected": True, "needs_review_url": B_URLS[0],
     "curation_candidates": [{"image_url": u, "status": "preselected" if i == 0 else "eligible", "is_selected": 1 if i == 0 else 0,
                              "title": f"B candidate {i + 1}", "reasons": []} for i, u in enumerate(B_URLS)]}
D = dict(A, row_number=12, product_name="Almarai Laban 1L", sku_key="key-d", search_query="Almarai Laban 1L Almarai")
C_LINK = "https://res.cloudinary.com/demo/image/upload/products/c.png"
C = dict(A, row_number=7, product_name="Almarai Cheese 200g", sku_key="key-c", size="200g", existing_image_link=C_LINK)
E_URLS = ["https://www.carrefouruae.com/e1.jpg", "https://www.carrefouruae.com/e2.jpg"]
E = dict(A, row_number=14, product_name="Almarai Butter 400g", sku_key="key-e", size="400g", needs_review=True,
         curation_candidates=[{"image_url": u, "status": "eligible", "is_selected": 0, "title": f"E {i + 1}", "reasons": []}
                              for i, u in enumerate(E_URLS)])

A_URL = "https://www.carrefouruae.com/a1.jpg"
A_ANSWER = {"status": "review", "decision": "REVIEW_PRESELECTED", "sku_key": "key-a", "selected_image": {"url": A_URL},
            "candidates": [{"url": A_URL, "status": "preselected", "title": "A result"},
                           {"url": "https://www.carrefouruae.com/a2.jpg", "status": "eligible", "title": "A second",
                            "page_url": "https://www.carrefouruae.com/p/a2", "content_sha256": "a2" * 32}]}
D_URL = "https://www.carrefouruae.com/d1.jpg"
D_ANSWER = {"status": "review", "decision": "REVIEW_PRESELECTED", "sku_key": "key-d", "selected_image": {"url": D_URL},
            "candidates": [{"url": D_URL, "status": "preselected", "title": "D result"}]}
OK = {"status": "success", "image_link": "https://res.cloudinary.com/demo/b.png", "sheet_value": "x", "isolated": True}


def fixture(*products, ready=(B, E)):
    return {
        "products": list(products) or [A, B, C, D, E],
        "queue": {"status": "success", "ready_for_review": len(ready),
                  "rows": [{"row_number": p["row_number"], "sku_key": p["sku_key"], "status": "ready_for_review",
                            "failure_code": None, "product_name": p["product_name"], "brand": p["brand"]} for p in ready]},
    }


def page(scenario, tmp_path, fx=None, config=None):
    boot = f"await boot({config or {}});\n".replace("True", "true").replace("False", "false")
    return run(boot + scenario, tmp_path, fx or fixture())


def identity_of(p):
    return {"row_number": str(p["row_number"]), "sku_key": p["sku_key"], "product_name": p["product_name"],
            "brand": p["brand"], "barcode": p["barcode"], "size": p["size"],
            "product_name_ar": p["product_name_ar"], "brand_ar": p["brand_ar"]}


def js(value):
    import json
    return json.dumps(value, ensure_ascii=False)


# ---------------------------------------------------------------------------
# A late search answer for a product that is no longer open
# ---------------------------------------------------------------------------

STALE_THEN_APPROVE = r"""
fetchHonoursAbort = __HONOUR__;
openRow(5);                                          // A has no stored images: live search (15-60 s)
const searchA = requests('/api/search')[0];
out.search_a_row = searchA.body.row_number;
openRow(9);                                          // B has stored images: shown at once
out.search_a_aborted = searchA.aborted;
answer(searchA, __A_ANSWER__);                       // A's answer arrives late
await flush();
out.open_name = productName();
out.open_sku = S().open.sku_key;
out.alt_urls = altUrls();
out.pick_url = pickUrl();
out.ws_text = wsText();
out.search_count = requests('/api/search').length;
press('2');                                          // B's second image, then approve it
press('Enter');
await flush();
const approve = requests('/api/select_image')[0];
out.approve_identity = identity(approve.body);
out.approve_url = approve.body.image_url;
out.approve_decision = approve.body.search_decision;
out.approve_status = approve.body.candidate_status;
"""


@pytest.mark.parametrize("honour_abort", ["true", "false"], ids=["aborted", "answer-arrives-anyway"])
def test_a_late_answer_for_another_product_is_dropped(tmp_path, honour_abort):
    out = page(STALE_THEN_APPROVE.replace("__HONOUR__", honour_abort).replace("__A_ANSWER__", js(A_ANSWER)), tmp_path)
    assert out["search_a_row"] == "5"
    assert out["search_a_aborted"] is True                    # the previous request is aborted
    # the workspace still shows B, with B's key and B's images
    assert out["open_name"] == B["product_name"] and out["open_sku"] == B["sku_key"]
    assert out["alt_urls"] == B_URLS
    assert out["pick_url"] == B_URLS[0]
    assert A["product_name"] not in out["ws_text"] and A_URL not in str(out["alt_urls"])
    assert out["search_count"] == 1
    # the approval publishes B's image into B's row with B's identity
    assert out["approve_identity"] == identity_of(B)
    assert out["approve_url"] == B_URLS[1]
    assert out["approve_decision"] == "REVIEW_PRESELECTED" and out["approve_status"] == "eligible"


def test_a_late_answer_does_not_replace_the_newer_search(tmp_path):
    out = page(r"""
fetchHonoursAbort = false;                           // the old answer is delivered even though it was aborted
openRow(5);
openRow(12);                                         // D: its own live search
const [searchA, searchD] = requests('/api/search');
out.rows = [searchA.body.row_number, searchD.body.row_number];
answer(searchD, __D__);
await flush();
answer(searchA, __A__);
await flush();
out.alt_urls = altUrls();
out.open_sku = S().open.sku_key;
press('Enter');
await flush();
out.approve_identity = identity(requests('/api/select_image')[0].body);
out.approve_url = requests('/api/select_image')[0].body.image_url;
""".replace("__D__", js(D_ANSWER)).replace("__A__", js(A_ANSWER)), tmp_path)
    assert out["rows"] == ["5", "12"]
    assert out["alt_urls"] == [D_URL]
    assert out["open_sku"] == "key-d"
    assert out["approve_identity"]["row_number"] == "12" and out["approve_identity"]["sku_key"] == "key-d"
    assert out["approve_url"] == D_URL


def test_an_older_search_of_the_same_product_never_replaces_the_newer_one(tmp_path):
    out = page(r"""
fetchHonoursAbort = false;                           // the first answer is delivered even though it was aborted
openRow(9);
openNotFound();
nfForms()[0].querySelector('input').value = 'first words';
submitForm(nfForms()[0]);
await flush();
openNotFound();
ws().querySelectorAll('button').find(b => b.textContent === 'وقّف البحث').click();
await flush();
openNotFound();
nfForms()[0].querySelector('input').value = 'second words';
submitForm(nfForms()[0]);
await flush();
const [first, second] = requests('/api/search');
out.queries = [first.body.custom_query, second.body.custom_query, first.aborted];
answer(second, { status: 'review', decision: 'REVIEW_UNSELECTED', sku_key: __SKU__,
                 candidates: [{ url: 'https://www.lulu.com/second.jpg', status: 'eligible', title: 'second' }] });
await flush();
answer(first, { status: 'review', decision: 'REVIEW_UNSELECTED', sku_key: __SKU__,
                candidates: [{ url: 'https://www.lulu.com/first.jpg', status: 'eligible', title: 'first' }] });
await flush();
out.alts = altUrls();
press('1'); press('Enter');
await flush();
out.approved = requests('/api/select_image').map(c => c.body.image_url);
""".replace("__SKU__", js(B["sku_key"])), tmp_path)
    assert out["queries"] == ["first words", "second words", True]
    assert out["alts"] == ["https://www.lulu.com/second.jpg"]
    assert out["approved"] == ["https://www.lulu.com/second.jpg"]


def test_results_are_dropped_when_the_open_product_changes_in_the_sheet(tmp_path):
    """The list is read again (after approvals, or "اقرأ الشيت من جديد") and the open row now holds another product
    identity (the owner fixed the barcode): the images found for the old identity are not shown or approved for it."""
    out = page(r"""
openRow(5);
answer(requests('/api/search')[0], __A__);
await flush();
out.before = altUrls();
FIXTURE.products.find(p => p.row_number === 5).barcode = '6281007000017';
FIXTURE.products.find(p => p.row_number === 5).sku_key = '06281007000017';
await R.loadData({ quiet: true });
await flush();
out.open = [S().open.barcode, S().open.sku_key];
out.after = altUrls();
ws().querySelector('.rv-research').click();          // a new search is for the new identity
await flush();
out.searches = requests('/api/search').map(c => [c.body.row_number, c.body.sku_key]);
out.toasts = toasts.map(t => t.text);
""".replace("__A__", js(A_ANSWER)), tmp_path)
    assert out["before"] == [A_URL, "https://www.carrefouruae.com/a2.jpg"]
    assert out["open"] == ["6281007000017", "06281007000017"]
    assert A_URL not in out["after"]
    assert out["searches"] == [["5", "key-a"], ["5", "06281007000017"]]
    assert any("تغيّرت بالشيت" in t for t in out["toasts"])


def test_switching_product_removes_the_old_results_and_their_keys(tmp_path):
    out = page(r"""
openRow(5);
answer(requests('/api/search')[0], __A__);
await flush();
out.before = altUrls().length;
openRow(12);                                         // D's search is still running
out.after_alts = altUrls();
out.state = S().ws.state;
out.approve_disabled = approveBtn().disabled;
press('Enter'); press('1'); press('Enter'); press('x');
await flush();
out.approvals = requests('/api/select_image').length;
out.reasons_open = S().reasonsOpen;
""".replace("__A__", js(A_ANSWER)), tmp_path)
    assert out["before"] == 2
    assert out["after_alts"] == [] and out["state"] == "searching" and out["approve_disabled"] is True
    assert out["approvals"] == 0 and out["reasons_open"] is False


# ---------------------------------------------------------------------------
# The results carry the identity they were found for
# ---------------------------------------------------------------------------

def test_approve_and_reject_send_the_bound_sheet_identity_not_the_typed_query(tmp_path):
    out = page(r"""
openRow(5);
const searchA = requests('/api/search')[0];
answer(searchA, __A__);
await flush();
out.search_name = searchA.body.product_name;
openNotFound();                                      // the reviewer types other words (not searched yet)
const query = nfForms()[0].querySelector('input');
query.value = 'milk full fat';
dispatch(query, { type: 'input', bubbles: true });
press('2');                                          // reject the second image, without a new search
press('x');
document.getElementById('rvRejectResearch').checked = false;
press('4');
await flush();
const reject = requests('/api/reject_image')[0];
out.reject_identity = identity(reject.body);
out.reject_extra = [reject.body.image_url, reject.body.category, reject.body.sub_category, reject.body.origin,
                    reject.body.reason_code, reject.body.research, reject.body.custom_query];
out.reject_view = [reject.body.search_decision, reject.body.candidate_status];
out.reject_bytes = [reject.body.candidate_sha256, reject.body.page_url];
answer(reject, { status: 'success', sku_key: 'key-a', approval_kept: false });
await flush();
openRow(5);                                          // a reject without research moved on, like an approval: back to A
out.alts_after_reject = altUrls();
out.text_after_reject = wsText();
press('Enter');                                      // A's pick is still shown: approve it
await flush();
const approve = requests('/api/select_image')[0];
out.approve_identity = identity(approve.body);
out.approve_category = approve.body.category;
out.approve_url = approve.body.image_url;
""".replace("__A__", js(A_ANSWER)), tmp_path)
    assert out["search_name"] == "Almarai Fresh Milk 1L"
    assert out["reject_identity"] == identity_of(A)
    assert out["reject_extra"] == ["https://www.carrefouruae.com/a2.jpg", "Dairy", "Milk", "KSA", "WRONG_SIZE", False,
                                   "milk full fat"]
    assert out["reject_view"] == ["REVIEW_PRESELECTED", "eligible"]
    # the catalog stores no curation rows for a live search: the reject carries the bytes' sha for the bridge's pHash
    assert out["reject_bytes"] == ["a2" * 32, "https://www.carrefouruae.com/p/a2"]
    assert out["alts_after_reject"] == [A_URL]
    # an alternative, not the system pick: the pick and the other images stay for review (contract C2)
    assert "باقي الصور ما زالت للمراجعة" in out["text_after_reject"] and "رجع للطابور" not in out["text_after_reject"]
    assert out["approve_identity"] == identity_of(A)
    assert out["approve_category"] == "Dairy" and out["approve_url"] == A_URL


def test_a_search_for_a_product_without_a_listed_key_takes_the_answers_key(tmp_path):
    out = page(r"""
openRow(5);
answer(requests('/api/search')[0], __A__);
await flush();
out.open_sku = S().open.sku_key;
press('Enter');
await flush();
out.approve_sku = requests('/api/select_image')[0].body.sku_key;
""".replace("__A__", js(A_ANSWER)), tmp_path, fixture(dict(A, sku_key=""), B, C, D, E))
    assert out["open_sku"] == "key-a" and out["approve_sku"] == "key-a"


def test_upload_sends_the_open_products_identity_and_is_never_sent_twice(tmp_path):
    out = page(r"""
openRow(9);
openNotFound();
const file = ws().querySelector('input[type="file"]');
file.files = [new Blob(['png'], { type: 'image/png' })];
dispatch(file, { type: 'change', bubbles: true });
out.pick_source = R.single.currentPick(itemOf(9)).source;
press('Enter');
await flush();
const upload = requests('/api/upload_manual_image')[0];
out.upload_identity = identity(upload.body);
out.upload_decision = upload.body.search_decision;
out.upload_has_file = !!upload.file;
out.during = approveBtn().disabled;
press('Enter');
await flush();
out.uploads_while_running = requests('/api/upload_manual_image').length;
answer(upload, { status: 'success', image_link: 'https://res.cloudinary.com/demo/b.png', sheet_value: 'x', isolated: true });
await flush();
out.after_text = wsText();
out.approve_disabled = approveBtn().disabled;
press('Enter');
await flush();
out.approvals = requests('/api/select_image').length + requests('/api/upload_manual_image').length;
""", tmp_path, config={"filter": "proposed"})
    assert out["pick_source"] == "upload"
    assert out["upload_identity"] == identity_of(B)
    assert out["upload_decision"] == "REVIEW_PRESELECTED" and out["upload_has_file"] is True
    assert out["during"] is True and out["uploads_while_running"] == 1
    assert APPROVED in out["after_text"] and out["approve_disabled"] is True
    assert out["approvals"] == 1


# ---------------------------------------------------------------------------
# One approval request at a time, and never twice
# ---------------------------------------------------------------------------

def test_every_approve_control_is_locked_while_the_products_approval_runs(tmp_path):
    out = page(r"""
openRow(9);
press('Enter');
await flush();
out.first = requests('/api/select_image').length;
out.disabled = [approveBtn().disabled, rejectBtn().disabled];
out.jobs = jobsText();
// every way to approve B again while it runs
press('Enter'); press('2'); press('Enter');
out.direct = R.single.approveCurrent();
R.bulk.approveOne(itemOf(9).key);
await flush();
out.while_running = requests('/api/select_image').length;
answer(requests('/api/select_image')[0], __OK__);
await flush();
out.ws_text = wsText();
out.after_disabled = approveBtn().disabled;
out.jobs_done = jobsText();
press('Enter'); press('3'); press('Enter');
R.bulk.approveOne(itemOf(9).key);
await flush();
out.after = requests('/api/select_image').length;
""".replace("__OK__", js(OK)), tmp_path, config={"filter": "proposed"})
    assert out["first"] == 1
    assert out["disabled"] == [True, True]
    assert "جاري اعتماد صورة وحدة بالخلفية" in out["jobs"]
    assert out["direct"] is False and out["while_running"] == 1
    assert APPROVED in out["ws_text"] and out["after_disabled"] is True
    assert "جاهزة. بتقدر تكمل شغلك." in out["jobs_done"]
    assert out["after"] == 1


def test_a_refused_approval_frees_the_product_and_shows_the_reason(tmp_path):
    out = page(r"""
openRow(9);
press('Enter');
await flush();
const loadsBefore = requests('/api/products-json').length;
answer(requests('/api/select_image')[0], { status: 'failed', error: __CHANGED__ }, 500);
await flush();
out.jobs = jobsText();
out.toasts = toasts.map(t => t.text);
out.approve_disabled = approveBtn().disabled;
out.reloaded = requests('/api/products-json').length > loadsBefore;
press('Enter');
await flush();
out.approvals = requests('/api/select_image').length;
""".replace("__CHANGED__", js(CHANGED)), tmp_path, config={"filter": "proposed"})
    assert CHANGED in out["jobs"] and "أعد المحاولة" in out["jobs"]
    assert any(CHANGED in t for t in out["toasts"])
    assert out["approve_disabled"] is False
    assert out["reloaded"] is True                               # the product list is read again
    assert out["approvals"] == 2                                 # an explicit new approval is possible


def test_approvals_of_other_products_wait_their_turn_and_keep_their_identity(tmp_path):
    out = page(r"""
openRow(9);
press('Enter');                                      // B's approval runs; the next product opens
await flush();
openRow(12);                                         // the reviewer moves on to D and searches it
answer(requests('/api/search').find(c => c.body.row_number === '12'), __D__);
await flush();
out.d_alts = altUrls();
out.d_approvable = !approveBtn().disabled;
press('Enter');                                      // D goes out next to B (two products at a time)
await flush();
out.sent_while_b_runs = requests('/api/select_image').length;
answer(requests('/api/select_image')[0], __OK__);
await flush();
out.sent_after_b = requests('/api/select_image').length;
out.second = identity(requests('/api/select_image')[1].body);
out.second_url = requests('/api/select_image')[1].body.image_url;
answer(requests('/api/select_image')[1], __OK__);
await flush();
out.max_in_flight = maxInFlight.select;
""".replace("__D__", js(D_ANSWER)).replace("__OK__", js(OK)), tmp_path)
    assert out["d_alts"] == [D_URL] and out["d_approvable"] is True
    assert out["sent_while_b_runs"] == 2 and out["sent_after_b"] == 2
    assert out["second"]["row_number"] == "12" and out["second"]["sku_key"] == "key-d"
    assert out["second_url"] == D_URL
    assert out["max_in_flight"] == 2                       # B and D together: two products at a time


# ---------------------------------------------------------------------------
# Keys 1-9 select, Enter approves the visible selected image
# ---------------------------------------------------------------------------

def test_number_keys_only_select_and_enter_approves_the_selection(tmp_path):
    out = page(r"""
openRow(9);
press('1'); press('3'); press('2');
await flush();
out.after_numbers = requests('/api/select_image').length;
out.selected = pressedAlts();
out.pick = pickUrl();
press('a');                                          // A is not an approve key
await flush();
out.after_a = requests('/api/select_image').length;
press('Enter');
await flush();
out.approvals = requests('/api/select_image').map(c => c.body.image_url);
""", tmp_path)
    assert out["after_numbers"] == 0
    assert out["selected"] == [False, True, False] and out["pick"] == B_URLS[1]
    assert out["after_a"] == 0
    assert out["approvals"] == [B_URLS[1]]


def test_enter_approves_the_system_pick_and_enter_on_a_button_is_left_to_the_browser(tmp_path):
    out = page(r"""
openRow(9);
press('9');                                          // no ninth image: nothing changes
rejectBtn().focus();
press('Enter');                                      // the browser activates the focused button itself
await flush();
out.enter_on_button = requests('/api/select_image').length;
document.body.focus();
press('Enter');
await flush();
out.approvals = requests('/api/select_image').map(c => c.body.image_url);
""", tmp_path)
    assert out["enter_on_button"] == 0
    assert out["approvals"] == [B_URLS[0]]


def test_enter_without_a_selected_image_approves_nothing(tmp_path):
    out = page(r"""
openRow(14);                                         // E: images, but the system selected none
out.pick = pickUrl();
out.approve_disabled = approveBtn().disabled;
press('Enter');
await flush();
out.without_selection = requests('/api/select_image').length;
press('2');
press('Enter');
await flush();
out.approvals = requests('/api/select_image').map(c => c.body.image_url);
out.decision = requests('/api/select_image')[0].body.search_decision;
""", tmp_path)
    assert out["pick"] is None and out["approve_disabled"] is True
    assert out["without_selection"] == 0
    assert out["approvals"] == [E_URLS[1]]
    assert out["decision"] == "REVIEW_UNSELECTED"


def test_the_image_strip_only_selects(tmp_path):
    out = page(r"""
openRow(9);
out.shortcuts = ws().querySelectorAll('.rv-alt').map(b => b.getAttribute('aria-keyshortcuts'));
out.hint = ws().querySelector('.rv-alts__hint').textContent;
ws().querySelectorAll('.rv-alt')[2].click();
await flush();
out.pick = pickUrl();
out.approvals = requests('/api/select_image').length;
""", tmp_path)
    assert out["shortcuts"] == ["1", "2", "3"]
    assert out["hint"] == "اضغط 1–3 لعرض صورة، و Enter لاعتمادها"
    assert out["pick"] == B_URLS[2] and out["approvals"] == 0


# ---------------------------------------------------------------------------
# A product with a final image link: show it, no automatic search
# ---------------------------------------------------------------------------

def test_a_completed_product_shows_its_sheet_image_and_searches_only_on_request(tmp_path):
    out = page(r"""
openRow(5);                                          // a search is running for A
const searchA = requests('/api/search')[0];
openRow(7);
out.search_a_aborted = searchA.aborted;
out.searches = requests('/api/search').length;
const final = document.getElementById('rvCurrentSheetImage');
out.images = imgSrcs(final);
out.text = wsText();
out.state = S().ws.state;
out.approve_disabled = approveBtn().disabled;
answer(searchA, __A__);                              // A's late answer does not replace C's view
await flush();
out.images_after_late_answer = imgSrcs(document.getElementById('rvCurrentSheetImage'));
ws().querySelector('.rv-research').click();
await flush();
out.research = requests('/api/search').map(c => c.body.row_number);
""".replace("__A__", js(A_ANSWER)), tmp_path)
    assert out["search_a_aborted"] is True
    assert out["searches"] == 1                                       # only A's
    assert out["images"] == [C_LINK]
    assert "الصورة الحالية بالشيت" in out["text"] and "دوّر على صورة بديلة" in out["text"]
    assert "لهالمنتج صورة نهائية بالشيت" in out["text"] and "ما دوّرنا تلقائياً" in out["text"]
    assert out["state"] == "final" and out["approve_disabled"] is True
    assert out["images_after_late_answer"] == out["images"]
    assert out["research"] == ["5", "7"]                              # "دوّر على صورة بديلة" searches C, on request


def test_a_product_never_searched_is_searched_when_opened(tmp_path):
    out = page(r"""
openRow(12);
out.searches = requests('/api/search').map(c => c.body.row_number);
""", tmp_path)
    assert out["searches"] == ["12"]


def test_quick_navigation_does_not_fire_a_paid_search_for_every_product(tmp_path):
    out = page(r"""
openRow(12);                                         // D, never searched: waits a moment before searching
openRow(9);                                          // ...but the reviewer moves on at once
await sleep(80);
out.after_passing = requests('/api/search').length;
openRow(12);
await sleep(80);
out.after_staying = requests('/api/search').map(c => c.body.row_number);
""", tmp_path, config={"autoSearchDelayMs": 30})
    assert out["after_passing"] == 0
    assert out["after_staying"] == ["12"]


# ---------------------------------------------------------------------------
# Keyboard paths that published, and approve buttons that came back after an approval
# ---------------------------------------------------------------------------

def test_browser_shortcuts_with_ctrl_cmd_or_alt_never_approve_or_select(tmp_path):
    out = page(r"""
openRow(9);
press('Enter', { ctrlKey: true }); press('Enter', { metaKey: true }); press('Enter', { altKey: true });
press('2', { ctrlKey: true }); press('3', { altKey: true }); press('x', { metaKey: true });
out.selected = pressedAlts();
out.reasons = S().reasonsOpen;
await flush();
out.approvals = requests('/api/select_image').length;
press('3');
press('Enter');
await flush();
out.plain = requests('/api/select_image').map(c => c.body.image_url);
""", tmp_path)
    assert out["selected"] == [True, False, False]                 # still the system pick
    assert out["reasons"] is False and out["approvals"] == 0
    assert out["plain"] == [B_URLS[2]]                              # the plain keys still work


def test_typing_in_a_field_never_reaches_the_shortcuts(tmp_path):
    out = page(r"""
openRow(9);
openNotFound();
const query = nfForms()[0].querySelector('input');
query.focus();
press('2'); press('x'); press('s'); press('Enter');
await flush();
out.selected = pressedAlts();
out.reasons = S().reasonsOpen;
out.open_row = itemOf(9).key === S().openKey;
out.approvals = requests('/api/select_image').length;
""", tmp_path)
    assert out["selected"] == [True, False, False] and out["reasons"] is False
    assert out["open_row"] is True and out["approvals"] == 0


def test_keys_never_act_on_results_the_reviewer_cannot_see(tmp_path):
    """A new search hides the images behind the waiting panel: Enter / 1-9 / X must not act on the hidden ones."""
    out = page(r"""
openRow(9);
openNotFound();
nfForms()[0].querySelector('input').value = 'almarai milk 2 litre';
submitForm(nfForms()[0]);
await flush();
out.state = S().ws.state;
out.alts_while_searching = altUrls();
press('Enter'); press('2'); press('Enter'); press('x');
await flush();
out.while_searching = requests('/api/select_image').length;
out.reasons = S().reasonsOpen;
out.search_body = [requests('/api/search')[0].body.custom_query, requests('/api/search')[0].body.skip_cache,
                   requests('/api/search')[0].body.row_number];
requests('/api/search')[0].reject(new Error('network down'));
await flush();
out.after_failure_text = wsText();
out.after_failure_alts = altUrls();
""", tmp_path)
    assert out["state"] == "searching" and out["alts_while_searching"] == []
    assert out["while_searching"] == 0 and out["reasons"] is False
    assert out["search_body"] == ["almarai milk 2 litre", True, "9"]
    # the failure is said plainly, and B's stored images come back (visible again)
    assert "فشل البحث" in out["after_failure_text"]
    assert out["after_failure_alts"] == B_URLS


def test_a_stopped_search_answering_late_cannot_bring_back_approve_buttons(tmp_path):
    """The reviewer searches again, stops the search and approves the stored pick; the stopped search answers late
    (the request was already running): the answer must not redraw images over the approved product."""
    out = page(r"""
fetchHonoursAbort = false;
openRow(9);
openNotFound();
submitForm(nfForms()[0]);
await flush();
const search = requests('/api/search')[0];
ws().querySelectorAll('button').find(b => b.textContent === 'وقّف البحث').click();
await flush();
out.search_aborted = search.aborted;
press('Enter');
await flush();
answer(requests('/api/select_image')[0], __OK__);
await flush();
answer(search, { status: 'review', decision: 'REVIEW_UNSELECTED', sku_key: __SKU__,
                 candidates: [{ url: 'https://www.lulu.com/b9.jpg', status: 'eligible', title: 'late' }] });
await flush();
out.text = wsText();
out.alts = altUrls();
out.approve_disabled = approveBtn().disabled;
press('1'); press('Enter');
await flush();
out.approvals = requests('/api/select_image').length;
""".replace("__OK__", js(OK)).replace("__SKU__", js(B["sku_key"])), tmp_path, config={"filter": "proposed"})
    assert out["search_aborted"] is True
    assert APPROVED in out["text"] and out["alts"] == [] and out["approve_disabled"] is True
    assert out["approvals"] == 1


def test_no_search_starts_while_the_products_approval_runs(tmp_path):
    out = page(r"""
openRow(9);
press('Enter');
await flush();
R.single.startSearch(itemOf(9), { customQuery: 'x' });
await flush();
out.searches = requests('/api/search').length;
answer(requests('/api/select_image')[0], __OK__);
await flush();
out.text = wsText();
ws().querySelector('.rv-research').click();          // "دوّر على صورة بديلة" after the approval: an explicit new search
await flush();
answer(requests('/api/search')[0], { status: 'review', decision: 'REVIEW_UNSELECTED', sku_key: __SKU__,
                                     candidates: [{ url: 'https://www.lulu.com/b8.jpg', status: 'eligible', title: 'new' }] });
await flush();
out.alts = altUrls();
press('1'); press('Enter');
await flush();
out.second = requests('/api/select_image').map(c => c.body.image_url);
""".replace("__OK__", js(OK)).replace("__SKU__", js(B["sku_key"])), tmp_path, config={"filter": "proposed"})
    assert out["searches"] == 0
    assert APPROVED in out["text"]
    assert out["alts"] == ["https://www.lulu.com/b8.jpg"]
    assert out["second"] == [B_URLS[0], "https://www.lulu.com/b8.jpg"]   # replacing an approved image is explicit


def test_a_reject_research_answer_after_an_approval_of_the_same_product_is_dropped(tmp_path):
    """Reject + re-search is a search too. If the product was approved (from bulk mode) before the answer came,
    the answer must not store new candidates for it nor redraw approve buttons."""
    out = page(r"""
openRow(9);
press('3');
press('x');
document.getElementById('rvRejectResearch').checked = true;
press('4');                                          // reject B's third image and search again
await flush();
R.setMode('bulk');
await flush();                                       // its card is on screen and its picture loaded: approvable
R.bulk.approveOne(itemOf(9).key);                    // approve B's system pick from bulk mode meanwhile
await flush();
answer(requests('/api/select_image')[0], __OK__);
await flush();
answer(requests('/api/reject_image')[0], { status: 'review', decision: 'REVIEW_UNSELECTED', sku_key: __SKU__,
    candidates: [{ url: 'https://www.lulu.com/b7.jpg', status: 'eligible', title: 'new' }],
    rejection: { approval_kept: false } });
await flush();
out.saved = requests('/api/v1/curation/save-candidates').length;
R.setMode('single', { key: itemOf(9).key });
await flush();
out.text = wsText();
out.alts = altUrls();
out.approve_disabled = approveBtn().disabled;
press('1'); press('Enter');
await flush();
out.approvals = requests('/api/select_image').length;
""".replace("__OK__", js(OK)).replace("__SKU__", js(B["sku_key"])), tmp_path)
    assert out["saved"] == 0
    assert APPROVED in out["text"] and out["alts"] == [] and out["approve_disabled"] is True
    assert out["approvals"] == 1


def test_a_reject_research_answer_stays_with_its_product_when_the_reviewer_moved_on(tmp_path):
    out = page(r"""
openRow(9);
press('x');
document.getElementById('rvRejectResearch').checked = true;
press('1');                                          // WRONG_PRODUCT, search again
await flush();
const reject = requests('/api/reject_image')[0];
out.reject_body = [reject.body.research, reject.body.reason_code, reject.body.image_url];
openRow(14);                                         // the reviewer moves on to E
answer(reject, { status: 'review', decision: 'REVIEW_UNSELECTED', sku_key: __SKU__,
                 candidates: [{ url: 'https://www.lulu.com/b7.jpg', status: 'eligible', title: 'new' }],
                 rejection: { approval_kept: false } });
await flush();
out.e_alts = altUrls();
out.e_name = productName();
const save = requests('/api/v1/curation/save-candidates')[0];
out.saved = save && [save.body.row_number, save.body.sku_key, save.body.candidates.map(c => c.url || c.image_url)];
openRow(9);
out.b_alts = altUrls();
press('1'); press('Enter');
await flush();
out.approval = requests('/api/select_image').map(c => [c.body.row_number, c.body.image_url]);
""".replace("__SKU__", js(B["sku_key"])), tmp_path)
    assert out["reject_body"] == [True, "WRONG_PRODUCT", B_URLS[0]]
    assert out["e_alts"] == E_URLS and out["e_name"] == E["product_name"]
    assert out.get("saved") is None                       # the server saves the new candidates (contract C2)
    assert out["b_alts"] == ["https://www.lulu.com/b7.jpg"]
    assert out["approval"] == [["9", "https://www.lulu.com/b7.jpg"]]


def test_a_pasted_url_becomes_the_selected_image(tmp_path):
    """After 2 selected a stored image, previewing a pasted URL shows the new image as the pick: Enter approves the
    image on screen, not the one selected before."""
    out = page(r"""
openRow(9);
press('2');
openNotFound();
const form = nfForms()[1];
form.querySelector('input').value = 'https://www.lulu.com/manual-b.jpg';
submitForm(form);
await flush();
out.pick = pickUrl();
out.selected = pressedAlts();
press('Enter');
await flush();
out.approvals = requests('/api/select_image').map(c => [c.body.image_url, c.body.candidate_status]);
""", tmp_path)
    assert out["pick"] == "https://www.lulu.com/manual-b.jpg"
    assert out["selected"] == [True, False, False, False]
    assert out["approvals"] == [["https://www.lulu.com/manual-b.jpg", "pending"]]
