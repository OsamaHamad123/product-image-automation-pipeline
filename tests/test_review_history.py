"""Review screen speed: reject + re-search without waiting, «آخر قراراتك», and Ctrl+Z.

* a reject with «دوّر على بدائل» (the default) goes through the background queue: the next product opens at once,
  approvals keep going while the search runs, and when the server saved alternatives the product comes back to the
  list in its chip with «لقينا بدائل لـ …»; rejecting an alternative (not the system's pick) still stays on the product;
* «آخر قراراتك» lists this session's last 20 decisions (approve / reject / upload / skip) with «افتح», «تراجع» for a
  held one and «تراجع عن الرفض» for a sent reject, and survives a reload (sessionStorage);
* Ctrl+Z (⌘Z) undoes a held job first, else the latest sent reject after a one-line question; never while typing;
  it is listed in «?».
"""

import json

import pytest

from laqta_review_harness import NODE, run

NEEDS_NODE = pytest.mark.skipif(NODE is None, reason="node is not installed")

EV = {"brand": True, "size": "match", "gtin": "match", "source_class": "uae_retailer", "tier": 1}
VLM = {"decision": "MATCH", "brand_match": "yes", "size_match": "yes", "variant_match": "yes", "view": "front_packshot"}


def js(value):
    return json.dumps(value, ensure_ascii=False)


def cand(url, status="eligible", selected=0):
    return {"image_url": url, "status": status, "is_selected": selected, "title": url.rsplit("/", 1)[-1], "reasons": [],
            "evidence": dict(EV), "vlm": VLM}


def picked(row, name, urls=None):
    urls = urls or [f"https://www.carrefouruae.com/p{row}.jpg"]
    return {"row_number": row, "product_name": name, "brand": "Almarai", "barcode": "", "sku_key": f"key-{row}",
            "size": "1L", "product_name_ar": "", "brand_ar": "", "category": "Dairy", "sub_category": "", "origin": "",
            "existing_image_link": "", "needs_review": True, "preselected": True, "search_query": f"{name} Almarai",
            "curation_candidates": [cand(u, "preselected" if i == 0 else "eligible", 1 if i == 0 else 0)
                                    for i, u in enumerate(urls)]}


def fixture(products):
    rows = [{"row_number": p["row_number"], "sku_key": p["sku_key"], "status": "ready_for_review", "failure_code": None,
             "product_name": p["product_name"], "brand": p["brand"], "updated_at": "2026-10-03 10:00:00"} for p in products]
    return {"products": products, "queue": {"status": "success", "ready_for_review": len(rows), "rows": rows}}


THREE = [picked(70, "Almarai Milk 1L"), picked(71, "Almarai Laban 1L"), picked(72, "Almarai Juice 1L")]
ALT_URLS = [f"https://www.lulu.com/70-{i}.jpg" for i in (1, 2, 3)]
NEW_URL = "https://www.lulu.com/new70.jpg"

# sessionStorage for the page (the harness has none): `seed` is what an earlier page left there
STORAGE = r"""
const store = new Map(Object.entries(__SEED__));
globalThis.sessionStorage = { getItem: k => (store.has(k) ? store.get(k) : null), setItem: (k, v) => store.set(k, String(v)),
                              removeItem: k => store.delete(k) };
const announced = () => document.getElementById('rvAnnounce').textContent;
const history = () => document.querySelectorAll('#rvHistoryList li').map(li => li.textContent);
const historyButtons = i => document.querySelectorAll('#rvHistoryList li')[i].querySelectorAll('button').map(b => b.textContent);
const ctrlZ = mods => press('z', Object.assign({ ctrlKey: true }, mods || {}));
"""


def page(scenario, tmp_path, fx=None, config=None, seed=None):
    head = STORAGE.replace("__SEED__", js(seed or {}))
    return run(head + f"await boot({js(config or {})});\n" + scenario, tmp_path, fx or fixture(THREE))


# ---------------------------------------------------------------------------
# 1. Reject + re-search without waiting
# ---------------------------------------------------------------------------

@NEEDS_NODE
def test_reject_with_research_moves_on_at_once_and_the_product_comes_back_with_alternatives(tmp_path):
    out = page(r"""
openRow(70);
press('x');
out.research_default = document.getElementById('rvRejectResearch').checked;
press('1');
await flush();
const reject = requests('/api/reject_image')[0];
out.sent = [reject.body.research, reject.body.image_url];
out.moved = [productName(), itemOf(70).bucket, R.visibleItems().map(it => it.product.row_number)];
out.toast = toasts.find(t => t.action);
out.toast = [out.toast.text, out.toast.action.label];
press('Enter');                                             // the reviewer approves the next one while the search runs
await flush();
out.approving = requests('/api/select_image').map(c => c.body.row_number);
FIXTURE.products[0].curation_candidates = [{ image_url: '__NEW__', status: 'eligible', is_selected: 0, reasons: [] }];
answer(reject, { status: 'review', decision: 'REVIEW_UNSELECTED', sku_key: 'key-70', candidates_saved: 1,
                 candidates: [{ url: '__NEW__', status: 'eligible', title: 'new' }],
                 rejection: { approval_kept: false, queue_status: 'ready_for_review', candidates_left: 0 } });
await flush();
out.back = [R.visibleItems().map(it => it.product.row_number).includes(70), itemOf(70).bucket, productName()];
out.note = toasts.slice(-1)[0].text;
openRow(70);
out.alts = altUrls();
""".replace("__NEW__", NEW_URL), tmp_path, config={"row": 70, "filter": "proposed"})
    assert out["research_default"] is True
    assert out["sent"] == [True, "https://www.carrefouruae.com/p70.jpg"]
    # the next product at once; the rejected one leaves the chip while its search runs
    assert out["moved"] == ["Almarai Laban 1L", "rejecting", [71, 72]]
    assert out["toast"] == ["رفضت صورة «Almarai Milk 1L» («منتج مختلف»)، وعم ندوّر على بدائل.", "تراجع"]
    # a research reject takes no approval slot: the approval goes out beside it
    assert out["approving"] == ["71"]
    # the answer: back in the same chip (S.keep), the reviewer is not moved
    assert out["back"] == [True, "none", "Almarai Juice 1L"]
    assert out["note"] == "لقينا بدائل لـ «Almarai Milk 1L»، ورجع بانتظار مراجعتك."
    assert out["alts"] == [NEW_URL]


@NEEDS_NODE
def test_rejecting_an_alternative_with_research_still_stays_on_the_product(tmp_path):
    out = page(r"""
press('2');
press('x');
press('1');
await flush();
const reject = requests('/api/reject_image')[0];
out.state = [productName(), pickUrl(), reject.body.image_url, reject.body.research, itemOf(70).bucket];
""", tmp_path, fixture([picked(70, "Almarai Milk 1L", ALT_URLS), picked(71, "Almarai Laban 1L")]), config={"row": 70})
    assert out["state"] == ["Almarai Milk 1L", ALT_URLS[0], ALT_URLS[1], True, "proposed"]


# ---------------------------------------------------------------------------
# 2. «آخر قراراتك»
# ---------------------------------------------------------------------------

@NEEDS_NODE
def test_history_lists_decisions_newest_first_with_open_and_undo(tmp_path):
    out = page(r"""
const panel = document.getElementById('rvHistory');
out.empty = panel.hidden;
openRow(70);
press('x');
document.getElementById('rvRejectResearch').checked = false;
press('1');                                                 // reject 70 → 71 opens
await flush();
answer(requests('/api/reject_image')[0], { status: 'success', approval_kept: false, candidates_left: 0, queue_status: 'pending' });
await flush();
press('s');                                                 // skip 71 → 72
await flush();
press('Enter');                                             // approve 72: held for «تراجع»
await flush();
const toggle = document.getElementById('rvHistoryToggle');
out.closed = [panel.hidden, toggle.getAttribute('aria-expanded'), document.getElementById('rvHistoryList').hidden,
              toggle.getAttribute('aria-controls')];
toggle.click();
out.open = [toggle.getAttribute('aria-expanded'), document.getElementById('rvHistoryList').hidden, toggle.textContent];
out.items = history();
out.buttons = [historyButtons(0), historyButtons(1), historyButtons(2)];
out.stored = JSON.parse(sessionStorage.getItem('laqta.review.history')).map(e => [e.kind, e.row]);
document.querySelectorAll('#rvHistoryList li')[0].querySelectorAll('button')[1].click();     // «تراجع» the held approval
await flush();
out.cancelled = [requests('/api/select_image').length, history()[0].includes('تراجعت'), productName(), announced()];
document.querySelectorAll('#rvHistoryList li')[1].querySelectorAll('button')[0].click();     // «افتح» the skipped one
await flush();
out.opened = productName();
""", tmp_path, config={"row": 70, "approveUndoMs": 5000})
    assert out["empty"] is True
    assert out["closed"] == [False, "false", True, "rvHistoryList"]
    assert out["open"] == ["true", False, "آخر قراراتك (3)"]
    assert out["items"][0].startswith("اعتمدت Almarai Juice 1L · صف 72 · ") and "بتنبعت بعد شوي" in out["items"][0]
    assert out["items"][1].startswith("تخطيت Almarai Laban 1L · صف 71 · ")
    assert out["items"][2].startswith("رفضت Almarai Milk 1L · صف 70 · ")
    assert out["buttons"] == [["افتح", "تراجع"], ["افتح"], ["افتح", "تراجع عن الرفض"]]
    assert out["stored"] == [["approve", "72"], ["skip", "71"], ["reject", "70"]]
    assert out["cancelled"] == [0, True, "Almarai Juice 1L", "تراجعت عن «Almarai Juice 1L»."]
    assert out["opened"] == "Almarai Laban 1L"


@NEEDS_NODE
def test_history_keeps_the_last_20_and_survives_a_reload(tmp_path):
    first = page(r"""
for (let i = 0; i < 25; i++) R.recordDecision('skip', itemOf(70 + (i % 3)));
document.getElementById('rvHistoryToggle').click();
out.count = [history().length, document.getElementById('rvHistoryToggle').textContent];
out.seed = { 'laqta.review.history': sessionStorage.getItem('laqta.review.history'),
             'laqta.review.historyOpen': sessionStorage.getItem('laqta.review.historyOpen') };
""", tmp_path, config={"row": 70})
    second = page(r"""
out.items = history().length;
out.open = document.getElementById('rvHistoryToggle').getAttribute('aria-expanded');
out.first = history()[0];
""", tmp_path, config={"row": 70}, seed=first["seed"])
    broken = page(r"""
out.items = history().length;
out.hidden = document.getElementById('rvHistory').hidden;
""", tmp_path, config={"row": 70}, seed={"laqta.review.history": "{not json"})
    assert first["count"] == [20, "آخر قراراتك (20)"]
    assert second["items"] == 20 and second["open"] == "true"
    assert second["first"].startswith("تخطيت Almarai Milk 1L · صف 70 · ")     # the 25th decision (i = 24) is first
    assert broken == {"items": 0, "hidden": True}


# ---------------------------------------------------------------------------
# 3. Ctrl+Z
# ---------------------------------------------------------------------------

@NEEDS_NODE
def test_ctrl_z_undoes_a_held_job_first_then_the_latest_sent_reject_after_asking(tmp_path):
    out = page(r"""
openRow(70);
press('x');
document.getElementById('rvRejectResearch').checked = false;
press('1');                                                 // reject 70 (sent) → 71
await flush();
answer(requests('/api/reject_image')[0], { status: 'success', approval_kept: false, candidates_left: 0, queue_status: 'pending' });
await flush();
press('Enter');                                             // approve 71: held
await flush();
document.getElementById('rvSearch').focus();
ctrlZ();                                                    // typing in the search box: the browser's own undo
await flush();
out.typing = [S().jobs.state().heldCount, asked.length];
ws().focus();
ctrlZ();
await flush();
out.first = [S().jobs.state().heldCount, requests('/api/select_image').length, productName(), announced(), asked.length];
ctrlZ({ ctrlKey: false, metaKey: true });                   // ⌘Z: the sent reject, after a one-line question
await flush();
const undo = requests('/api/review/undo-reject');
out.second = [asked.map(a => a.title), undo.map(c => [c.body.row_number, c.body.image_url]), announced(),
              history().find(t => t.startsWith('رفضت')).includes('تراجعت')];
ctrlZ();
await flush();
out.nothing = [announced(), requests('/api/review/undo-reject').length];
""", tmp_path, config={"row": 70, "approveUndoMs": 5000})
    assert out["typing"] == [1, 0]
    assert out["first"] == [0, 0, "Almarai Laban 1L", "تراجعت عن «Almarai Laban 1L».", 0]
    assert out["second"][0] == ["ترجّع صورة «Almarai Milk 1L» للاقتراحات؟"]
    assert out["second"][1] == [["70", "https://www.carrefouruae.com/p70.jpg"]]
    assert out["second"][2] == "تراجعت عن رفض «Almarai Milk 1L»."
    assert out["second"][3] is True
    assert out["nothing"] == ["ما في شي تتراجع عنه هلق.", 1]


@NEEDS_NODE
def test_ctrl_z_is_listed_in_the_shortcuts(tmp_path):
    out = page(r"""
press('?');
await flush();
out.text = document.getElementById('rvDialog').textContent;
""", tmp_path, config={"row": 70})
    assert "Ctrl+Z أو ⌘Z" in out["text"] and "تراجع عن آخر قرار" in out["text"]


@NEEDS_NODE
def test_a_late_undo_does_not_pull_the_reviewer_back(tmp_path):
    """«تراجع» while the new search still runs: the undo lands when the answer comes (maybe a minute later). By then the
    reviewer is on another product, so the screen stays there and a toast says the picture is back."""
    out = page(r"""
openRow(70);
press('x');
press('1');
await flush();
const reject = requests('/api/reject_image')[0];
out.before = productName();
toasts.find(t => t.action).action.onClick();                 // «تراجع» before the search answers
await flush();
answer(reject, { status: 'review', decision: 'REVIEW_UNSELECTED', sku_key: 'key-70', candidates_saved: 0,
                 rejection: { approval_kept: false, queue_status: 'ready_for_review', candidates_left: 0 } });
await flush();
const undo = requests('/api/review/undo-reject')[0];
if (undo) answer(undo, { status: 'success' });
await flush();
out.after = productName();
out.undone = !!undo;
out.note = toasts.slice(-1)[0].text;
""", tmp_path, config={"row": 70, "filter": "proposed"})
    assert out["before"] == "Almarai Laban 1L"
    assert out["undone"] is True
    assert out["after"] == "Almarai Laban 1L"                  # still on the product the reviewer moved to
    assert out["note"] == "تراجعت عن رفض صورة «Almarai Milk 1L»: رجعت للاقتراحات."
