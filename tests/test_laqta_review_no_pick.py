"""Review screen: «بلا اقتراح» says why, and shows the images at once (dashboard/public/js/review/*.js under node).

Each test failed before its change:

* a product without a pick showed a big empty panel («ما في صورة مختارة لهالمنتج») with the images below the fold and
  no reason. It now shows the reason in one Arabic sentence (catalog_match.explain, from queue-state), the code only
  in a tooltip, and the images first, each with «لماذا لم تُختر» from its own evidence and its own warnings;
* nothing is pre-selected: Enter approves nothing until the reviewer picks one (1–9 or a click); then the usual
  approval (and its guards) applies to that image;
* a bulk «بلا اقتراح» card says why; the list names the reason under each such product;
* «السبب» chips filter the list by reason or by what the sheet row lacks (?reason= deep link), so the owner fixes a
  group at once;
* a not-found product and a live search without a pick say why too;
* rows saved before the reasons existed ask the server once to compute them (explain-backfill) and reload quietly;
* the page's reason names are catalog_match.explain's.
"""

import json
from pathlib import Path

import pytest

from laqta_review_harness import NODE, run

ROOT = Path(__file__).resolve().parents[1]
NEEDS_NODE = pytest.mark.skipif(NODE is None, reason="node is not installed")
pytestmark = NEEDS_NODE


def js(value):
    return json.dumps(value, ensure_ascii=False)


def cand(url, status="eligible", **extra):
    row = {"image_url": url, "status": status, "is_selected": 0, "title": url.rsplit("/", 1)[-1], "reasons": []}
    row.update(extra)
    return row


def product(row, name, brand="Mr John", size="", **extra):
    p = {"row_number": row, "product_name": name, "brand": brand, "barcode": "", "sku_key": f"key-{row}", "size": size,
         "product_name_ar": "", "brand_ar": "", "category": "Frozen", "sub_category": "", "origin": "",
         "existing_image_link": "", "needs_review": True, "search_query": f"{name} {brand}"}
    p.update(extra)
    return p


NO_SIZE = {"v": 1, "key": "no_size", "label": "حجم ناقص بالشيت", "engine": "unsure",
           "text": "الشيت ما فيه حجم ولا باركود لهالمنتج، فما في صورة قدرنا نتأكد إنها نفس العبوة، وقارئ الملصق ما تأكد "
                   "من صورة وحدة. أضف الحجم في الشيت ثم أعد البحث، أو اختر من الصور تحت.",
           "sheet": [{"key": "no_size", "text": "الحجم ناقص بالشيت"}, {"key": "no_barcode", "text": "الباركود ناقص بالشيت"}]}
UNSURE = {"key": "unsure", "label": "قارئ الملصق غير متأكد", "engine": "unsure",
          "text": "قارئ الملصق ما تأكد إنها نفس المنتج على صورتين للماركة. اختر من الصور تحت.",
          "sheet": [{"key": "no_barcode", "text": "الباركود ناقص بالشيت"}]}
TYPO = {"key": "typo", "label": "غلطة إملائية بالاسم", "engine": "not_found",
        "text": "يمكن في غلطة إملائية بالاسم: «CHICEKN» قصدك «CHICKEN»؟ صحّح الاسم في الشيت ثم أعد البحث.",
        "sheet": [{"key": "typo", "text": "يمكن «CHICEKN» قصدك «CHICKEN»", "word": "CHICEKN", "suggest": "CHICKEN"}]}

URLS = ["https://www.carrefouruae.com/np1.jpg", "https://www.noon.com/np3.jpg", "https://www.amazon.ae/np4.jpg"]
NP = product(50, "MR JOHN FRENCH FRIES", curation_candidates=[
    cand(URLS[2], "rejected", reasons=["vlm:MISMATCH"], vlm={"decision": "MISMATCH"}, identity_tier="2"),
    cand(URLS[0], reasons=["vlm:UNSURE"], identity_tier="2", evidence={"tier": 2, "brand": True, "size": "unknown"},
         vlm={"decision": "UNSURE", "size_match": "unsure"}),
    cand(URLS[1], reasons=["warn:foreign_store"], identity_tier="3", evidence={"tier": 3, "size": "unknown"})])
NP2 = product(51, "MR JOHN FRIES CRINKLE 900GM", size="900GM", curation_candidates=[
    cand("https://www.lulu.com/np2.jpg", reasons=["vlm:UNSURE"], identity_tier="2",
         evidence={"tier": 2, "brand": True, "size": "unknown"}, vlm={"decision": "UNSURE"})])
OLD = product(52, "MR JOHN WEDGES 750GM", size="750GM", curation_candidates=[
    cand("https://www.lulu.com/old.jpg", reasons=["vlm:UNSURE"], identity_tier="2", vlm={"decision": "UNSURE"})])
NFX = product(53, "TARGET CHICEKN LUNCHEON MEAT 340GM", brand="Target", needs_review=False, has_error=True,
              error_message="NO_RESULTS: No acceptable image found (NO_RESULTS)")
PICK = product(54, "ALMARAI MILK 1L", brand="Almarai", size="1L", preselected=True, curation_candidates=[
    cand("https://www.lulu.com/milk.jpg", "preselected", is_selected=1, reasons=["vlm:MATCH"],
         evidence={"size": "match", "tier": 1}, vlm={"decision": "MATCH", "size_match": "yes"})])


def qrow(p, status="ready_for_review", explain=None, failure_code=None):
    return {"row_number": p["row_number"], "sku_key": p["sku_key"], "status": status, "failure_code": failure_code,
            "product_name": p["product_name"], "brand": p["brand"], "updated_at": "2026-10-04 09:00:00",
            "explain": explain}


def fixture(missing=0, **extra):
    rows = [qrow(NP, explain=NO_SIZE), qrow(NP2, explain=UNSURE), qrow(OLD), qrow(PICK),
            qrow(NFX, "failed", TYPO, "NO_RESULTS")]
    fx = {"products": [NP, NP2, OLD, NFX, PICK],
          "queue": {"status": "success", "ready_for_review": 4, "rows": rows, "explain_missing": missing}}
    fx.update(extra)
    return fx


def page(scenario, tmp_path, fx=None, config=None):
    return run(f"await boot({js(config or {})});\n" + scenario, tmp_path, fx or fixture())


VISIBLE = r"""
function visibleText(node) {
    if (!node) return '';
    if (node.nodeType === 3) return node.data;
    if (node.hidden || node.tagName === 'SVG') return '';
    return node.childNodes.map(visibleText).join('');
}
const panels = () => ws().children.map(n => n.getAttribute('class') || '');
const whyLines = () => ws().querySelectorAll('.rv-alt').map(b => { const w = b.querySelector('.rv-alt__why'); return w ? w.textContent : null; });
"""


def test_a_product_without_a_pick_shows_why_and_its_images_first(tmp_path):
    out = page(VISIBLE + r"""
openRow(50);
const banner = ws().querySelector('.rv-nopick');
out.banner = [banner.textContent, banner.getAttribute('title'), banner.getAttribute('data-nopick')];
out.panels = panels();
out.empty = !!ws().querySelector('.rv-pick') || !!ws().querySelector('.rv-pick--empty');
out.order = altUrls();
out.pressed = pressedAlts();
out.why = whyLines();
out.notes = ws().querySelectorAll('.rv-alt').map(b => { const n = b.querySelector('.rv-alt__note'); return n ? n.textContent : null; });
out.visible = visibleText(ws());
out.approveDisabled = approveBtn().disabled;
press('Enter');
await flush();
out.selectsAfterEnter = requests('/api/select_image').length;
press('2');
await flush();
out.picked = [pickUrl(), approveBtn().disabled, pressedAlts()];
out.whyAfterPick = whyLines();
press('Enter');
await flush();
const sel = requests('/api/select_image');
out.approved = sel.map(c => [c.body.image_url, c.body.search_decision, c.body.candidate_status]);
""", tmp_path)
    text, title, key = out["banner"]
    assert text.startswith("ليش ما في اقتراح:") and NO_SIZE["text"] in text
    assert "وكمان بالشيت: الباركود ناقص بالشيت." in text          # the sheet's other gap, the reason's own is not repeated
    assert (title, key) == ("no_size · unsure", "no_size")             # the codes only in the tooltip
    for code in ("no_size", "unsure", "no_barcode", "REVIEW_UNSELECTED"):
        assert code not in out["visible"]
    # no empty «ما في صورة مختارة» panel: the product, the reason, then the images
    assert not out["empty"] and "ما في صورة مختارة لهالمنتج" not in out["visible"]
    first, banner, grid = out["panels"][:3]
    assert "rv-product" in first.split() and "rv-nopick" in banner.split() and "rv-alts--nopick" in grid.split()
    assert out["order"] == [URLS[0], URLS[1], URLS[2]]                  # best-ranked first, the set-aside one last
    assert out["pressed"] == [False, False, False]                      # nothing pre-selected
    assert out["why"] == ["لماذا لم تُختر: قارئ الملصق ما تأكد، والشيت ما فيه حجم",
                          "لماذا لم تُختر: الصفحة ما بتذكر الماركة",
                          None]                                 # the set-aside image's own line says why
    assert out["notes"] == [None, "الصورة من متجر خارج الإمارات (قد تختلف العبوة)", "نموذج القراءة شاف منتج مختلف"]
    assert out["approveDisabled"] is True and out["selectsAfterEnter"] == 0
    # an explicit pick (2) shows that image as the choice; only then Enter approves it, as any approval
    assert out["picked"] == [URLS[1], False, [False, True, False]]
    assert out["whyAfterPick"][0] == "لماذا لم تُختر: قارئ الملصق ما تأكد، والشيت ما فيه حجم"
    assert out["approved"] == [[URLS[1], "REVIEW_UNSELECTED", "eligible"]]


def test_a_pick_with_warnings_still_asks_before_approving(tmp_path):
    out = page(r"""
openRow(50);
press('2');
await flush();
press('Enter');
await flush();
out.confirms = confirms.slice();
out.selects = requests('/api/select_image').length;
""", tmp_path)
    assert out["selects"] == 1 and any("خارج الإمارات" in c for c in out["confirms"])


def test_a_product_saved_without_a_reason_says_so_honestly(tmp_path):
    out = page(r"""
openRow(52);
const banner = ws().querySelector('.rv-nopick');
out.text = banner.textContent;
out.title = banner.getAttribute('title');
out.grid = !!document.getElementById('rvNoPickGrid');
""", tmp_path)
    assert "النظام ما اختار صورة لهالمنتج، وسببه مش محفوظ. اختر من الصور تحت." in out["text"]
    assert out["title"] is None and out["grid"] is True


def test_the_list_and_the_reason_chips_group_products_by_reason(tmp_path):
    out = page(r"""
const chips = () => document.querySelectorAll('.rv-reason-chip').map(b => [b.textContent, b.getAttribute('aria-pressed')]);
const listed = () => R.visibleItems().map(it => it.product.row_number);
R.setFilter('none');
out.none = [chips(), listed()];
out.item = listButton(50).querySelector('.rv-item__why').textContent;
document.querySelectorAll('.rv-reason-chip').find(b => b.getAttribute('data-nopick-reason') === 'no_barcode').click();
await flush();
out.barcode = [listed(), S().reason, historyLog.slice(-1)[0][1]];
document.querySelectorAll('.rv-reason-chip').find(b => b.getAttribute('data-nopick-reason') === 'no_barcode').click();
out.cleared = [listed(), S().reason];
R.setFilter('all');
out.all = chips().map(c => c[0]);
R.setReason('typo');
out.typo = [listed(), productName()];
R.setFilter('proposed');
out.afterFilter = [S().reason, document.querySelector('.rv-reasons-filter').hidden];
""", tmp_path)
    chips, listed = out["none"]
    assert chips == [["كل الأسباب", "true"], ["باركود ناقص بالشيت2", "false"], ["حجم ناقص بالشيت1", "false"],
                     ["قارئ الملصق غير متأكد1", "false"]]
    assert sorted(listed) == [50, 51, 52]
    assert out["item"] == "حجم ناقص بالشيت"
    rows, reason, url = out["barcode"]
    assert sorted(rows) == [50, 51] and reason == "no_barcode" and "reason=no_barcode" in url
    assert sorted(out["cleared"][0]) == [50, 51, 52] and out["cleared"][1] == ""
    assert "غلطة إملائية بالاسم1" in out["all"]                       # «الكل» counts the not-found products too
    assert out["typo"] == [[53], NFX["product_name"]]
    assert out["afterFilter"] == ["", True]                            # another chip forgets the reason


def test_a_reason_deep_link_opens_its_products(tmp_path):
    out = page(r"""
out.state = [S().filter, S().reason, R.visibleItems().map(it => it.product.row_number), productName()];
""", tmp_path, config={"reason": "no_size", "filter": None})
    assert out["state"] == ["all", "no_size", [50], NP["product_name"]]


def test_a_bulk_card_without_a_pick_says_why(tmp_path):
    out = page(r"""
R.setMode('bulk');
await flush();
const card = row => document.querySelectorAll('.rv-card').find(c => c.getAttribute('data-key') === itemOf(row).key);
out.np = [card(50).querySelector('.rv-card__why').textContent, card(50).querySelector('.rv-card__why').getAttribute('title')];
out.old = card(52).querySelector('.rv-card__why').textContent;
out.pick = card(54).querySelector('.rv-card__why');
""", tmp_path)
    assert out["np"] == [NO_SIZE["text"], "no_size"]
    assert out["old"] == "النظام ما اختار صورة لهالمنتج، وسببه مش محفوظ. اختر من الصور تحت."
    assert out["pick"] is None


def test_a_not_found_product_says_why(tmp_path):
    out = page(r"""
openRow(53);
const banner = ws().querySelector('.rv-nopick');
out.banner = [banner.textContent, banner.getAttribute('title')];
out.retry = !!ws().querySelector('.rv-retry');
""", tmp_path)
    assert TYPO["text"] in out["banner"][0] and out["banner"][1] == "typo" and out["retry"] is True


def test_a_live_search_without_a_pick_shows_its_reason(tmp_path):
    idle = product(55, "MR JOHN STEAKHOUSE FRIES", needs_review=False)
    fx = fixture()
    fx["products"].append(idle)
    out = page(r"""
openRow(55);
await flush();
answer(requests('/api/search')[0], { status: 'review', decision: 'REVIEW_UNSELECTED', sku_key: 'key-55',
    candidates: [{ url: 'https://www.lulu.com/s1.jpg', status: 'eligible', reasons: ['vlm:UNSURE'], vlm: { decision: 'UNSURE' } }],
    explain: __UNSURE__ });
await flush();
const banner = ws().querySelector('.rv-nopick');
out.banner = banner ? banner.textContent : null;
out.grid = !!document.getElementById('rvNoPickGrid');
""".replace("__UNSURE__", js(UNSURE)), tmp_path, fx)
    assert UNSURE["text"] in out["banner"] and out["grid"] is True


def test_rows_saved_before_ask_once_for_their_reasons(tmp_path):
    out = page(r"""
await flush();
out.calls = [requests('/api/review/explain-backfill').length, requests('/api/products-json').length];
await R.loadData({});
await flush();
out.again = requests('/api/review/explain-backfill').length;
""", tmp_path, fixture(missing=2, backfill={"status": "success", "filled": 2, "checked": 2}))
    assert out["calls"] == [1, 2]                     # asked once, then the list was read again (quietly)
    assert out["again"] == 1                          # never twice in a session
    out = page(r"""
await flush();
out.calls = [requests('/api/review/explain-backfill').length, requests('/api/products-json').length];
""", tmp_path, fixture(missing=0))
    assert out["calls"] == [0, 1]


def test_why_not_picked_reads_each_images_evidence(tmp_path):
    out = page(r"""
const prod = { size: '1L', product_name: 'Almarai Milk 1L' };
const w = c => R.whyNotPicked(R.normalizeCandidate(Object.assign({ image_url: 'https://x.ae/a.jpg', status: 'eligible' }, c)), prod).text;
out.lines = [
  w({ vlm: { decision: 'UNSURE' }, identity_tier: '2', evidence: { size: 'match' } }),
  w({ vlm: { decision: 'UNSURE' }, identity_tier: '2', evidence: { size: 'unknown' } }),
  w({ identity_tier: '2', evidence: { size: 'match' } }),
  w({ vlm: { decision: 'MATCH' }, identity_tier: '2', evidence: { size: 'match' }, reasons: ['warn:foreign_store'] }),
  w({ vlm: { decision: 'MATCH' }, identity_tier: '2', evidence: { size: 'match' }, reasons: ['warn:barcode_conflict'] }),
  w({ vlm: { decision: 'MATCH' }, identity_tier: '2', evidence: { size: 'match' } }),
  w({ status: 'rejected', reasons: ['download:http_403'] })
];
out.manual = R.whyNotPicked({ source: 'manual', url: 'x' }, prod);
""", tmp_path)
    assert out["lines"] == ["قارئ الملصق ما تأكد", "قارئ الملصق ما تأكد، والحجم غير مكتوب في الصفحة",
                            "قارئ الملصق ما قرأها", "متجر خارج الإمارات", "باركود الصفحة مختلف عن الشيت",
                            "ما وصلت للثقة اللي بتخلينا نختارها لحالنا", "ما قدرنا نحمّلها"]
    assert out["manual"] is None


def test_the_pages_reason_names_are_the_engines(tmp_path):
    from catalog_match import explain

    out = run("out.labels = R.NO_PICK_LABELS;", tmp_path, fixture())
    assert out["labels"] == explain.REASON_LABELS
