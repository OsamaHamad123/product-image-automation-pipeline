"""Review screen, phase 4 (P5): complete evidence, honest labels, faster review.

Each test failed before its change:

* every candidate carries its own warnings: an alternative from a foreign store, with a differing page barcode or
  from a social network showed no caution, nor did any image of a product without a pick; a bulk card showed only
  the first warning;
* size_unverified / variant_unverified have Arabic labels and keep a pick out of «approve the ones without warning»
  (also for rows saved before the server computed them);
* «why this image» chips from the evidence the engine computed, on the pick, each alternative and each bulk card;
  the store spelling of brand_spelling:<spelling> is shown;
* the «final preview» claimed to be the cut-out sent to Cloudinary while it showed the raw source;
* approvals whose background removal failed (needs_review:<Cloudinary link>) were hidden under «All»; they have a
  chip with a count, and the cosmetic reject reasons the backend accepts;
* the waiting products come in order of confidence, each brand together; bulk mode has keys; a product put back to
  review after a reject with research stays in its chip;
* contracts with the backend: C1 the approval carries what the page showed and a refused one is replaced only after
  an explicit confirmation; C2 the page never saves candidates after a reject; C3 the sheet outcome is said as it is.
"""

import json
from pathlib import Path

import pytest

from laqta_review_harness import NODE, run
from test_laqta_review import NEEDS_LARAVEL, _config_of, _http

ROOT = Path(__file__).resolve().parents[1]
DASH = ROOT / "dashboard"
CONTROLLERS = DASH / "app" / "Http" / "Controllers"

NEEDS_NODE = pytest.mark.skipif(NODE is None, reason="node is not installed")

CLOUD = "https://res.cloudinary.com/demo/image/upload/products/bg.png"


def js(value):
    return json.dumps(value, ensure_ascii=False)


def cand(url, status="eligible", selected=0, **extra):
    # the store page confirmed the sheet's size unless a test says otherwise: a saved row without evidence is
    # «size unverified» (review/core.js unverifiedWarnings, tested on its own below)
    row = {"image_url": url, "status": status, "is_selected": selected, "title": url.rsplit("/", 1)[-1], "reasons": [],
           "evidence": {"size": "match"}}
    row.update(extra)
    return row


def product(row, name, brand="Almarai", **extra):
    p = {"row_number": row, "product_name": name, "brand": brand, "barcode": "", "sku_key": f"key-{row}", "size": "1L",
         "product_name_ar": "", "brand_ar": "", "category": "Dairy", "sub_category": "", "origin": "",
         "existing_image_link": "", "needs_review": False, "search_query": f"{name} {brand}"}
    p.update(extra)
    return p


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


GOOD_EVIDENCE = {"brand": True, "size": "match", "pack": "match", "gtin": "match", "consensus_count": 3,
                 "source_class": "uae_retailer", "tier": 1}
GOOD_VLM = {"decision": "MATCH", "brand_match": "yes", "size_match": "yes", "variant_match": "yes",
            "view": "front_packshot"}

# a pick without warnings, an alternative from a Saudi store, one with a differing page barcode and from a social network
MILK = product(10, "Almarai Fresh Milk 1L", needs_review=True, preselected=True, curation_candidates=[
    cand("https://www.luluhypermarket.com/milk.jpg", "preselected", 1, reasons=["vlm:MATCH"], evidence=GOOD_EVIDENCE,
         vlm=GOOD_VLM),
    cand("https://www.danube.com.sa/milk.jpg", reasons=["vlm:MATCH", "warn:foreign_store"],
         evidence={"brand": True, "size": "match", "source_class": "other_retail"}),
    cand("https://scontent.cdninstagram.com/milk.jpg", reasons=["vlm:UNSURE", "warn:barcode_conflict", "warn:social_media"]),
])
# nothing pre-selected (REVIEW_UNSELECTED): every image still carries its warnings
LABAN = product(11, "Almarai Laban 1L", needs_review=True, curation_candidates=[
    cand("https://www.danube.com.sa/laban.jpg", reasons=["warn:foreign_store", "warn:low_resolution"]),
    cand("https://www.carrefouruae.com/laban.jpg", reasons=[]),
])


# ---------------------------------------------------------------------------
# 1. Warnings for every candidate
# ---------------------------------------------------------------------------

@NEEDS_NODE
def test_every_alternative_shows_its_own_warnings_and_picking_it_shows_them_before_approval(tmp_path):
    out = page(r"""
const alt = url => ws().querySelectorAll('.rv-alt').find(b => b.getAttribute('data-url') === url);
const caution = () => { const c = ws().querySelector('.rv-caution'); return c ? c.textContent : ''; };
openRow(10);
out.pick_caution = caution();
out.saudi_note = alt('https://www.danube.com.sa/milk.jpg').querySelector('.rv-alt__note').textContent;
const social = alt('https://scontent.cdninstagram.com/milk.jpg');
out.social = [social.querySelector('.rv-alt__note').textContent, social.querySelectorAll('.rv-alt__warn').map(w => w.textContent)];
press('3');
out.picked_caution = caution();
openRow(11);
// a product without a pick: under each image its first warning (if any), «لماذا لم تُختر», then the other warnings
const noteOf = b => { const n = b.querySelector('.rv-alt__note'); return n ? n.textContent : null; };
out.unselected = ws().querySelectorAll('.rv-alt').map(b => [noteOf(b), b.querySelectorAll('.rv-alt__warn').map(w => w.textContent),
                                                           b.querySelector('.rv-alt__why') !== null]);
press('1');
out.unselected_caution = caution();
""", tmp_path, fixture([MILK, LABAN]))
    assert out["pick_caution"] == ""
    assert out["saudi_note"].startswith("الصورة من متجر خارج الإمارات")
    assert out["social"][0].startswith("الباركود بالشيت مختلف") and out["social"][1] == ["الصورة من مواقع التواصل الاجتماعي"]
    assert "الباركود بالشيت مختلف" in out["picked_caution"] and "مواقع التواصل" in out["picked_caution"]
    assert out["unselected"][0][0].startswith("الصورة من متجر خارج الإمارات")
    assert out["unselected"][0][1] == ["صورة منخفضة الدقة (أقل من 500 بكسل)"] and out["unselected"][0][2] is True
    assert out["unselected"][1] == [None, [], True]          # no warning: «لماذا لم تُختر» says it all
    assert "خارج الإمارات" in out["unselected_caution"] and "منخفضة الدقة" in out["unselected_caution"]


@NEEDS_NODE
def test_a_bulk_card_shows_every_warning_of_its_pick(tmp_path):
    two = product(12, "Almarai Cheese 200g", needs_review=True, preselected=True, curation_candidates=[
        cand("https://www.danube.com.sa/cheese.jpg", "preselected", 1,
             reasons=["vlm:MATCH", "warn:foreign_store", "warn:brand_spelling:Al Marai"])])
    out = page(r"""
R.setMode('bulk');
await flush();
out.warns = document.querySelectorAll('.rv-card').find(c => c.getAttribute('data-kind') === 'warning')
    .querySelectorAll('.rv-card__warn').map(w => w.textContent);
""", tmp_path, fixture([two]))
    assert len(out["warns"]) == 2
    assert out["warns"][0].startswith("الصورة من متجر خارج الإمارات")
    assert "«Al Marai»" in out["warns"][1]                      # the store spelling is shown


# ---------------------------------------------------------------------------
# 2. Unconfirmed size / variant
# ---------------------------------------------------------------------------

@NEEDS_NODE
def test_unverified_size_or_variant_keeps_a_pick_out_of_the_bulk_approval(tmp_path):
    clean = product(20, "Almarai Milk 1L", needs_review=True, preselected=True, curation_candidates=[
        cand("https://www.luluhypermarket.com/m20.jpg", "preselected", 1, reasons=["vlm:MATCH"],
             evidence={"size": "match", "tier": 1}, vlm=GOOD_VLM)])
    size = product(21, "Almarai Milk 2L", needs_review=True, preselected=True, curation_candidates=[
        cand("https://www.luluhypermarket.com/m21.jpg", "preselected", 1, reasons=["vlm:MATCH", "warn:size_unverified"])])
    variant = product(22, "Almarai Full Fat Milk 1L", needs_review=True, preselected=True, curation_candidates=[
        cand("https://www.luluhypermarket.com/m22.jpg", "preselected", 1, reasons=["vlm:MATCH", "warn:variant_unverified"])])
    # saved before the server computed it: tier 2, the label reader unsure of the size, the page silent about it
    old = product(23, "Almarai Milk 500ml", size="500ml", needs_review=True, preselected=True, curation_candidates=[
        cand("https://www.luluhypermarket.com/m23.jpg", "preselected", 1, reasons=["vlm:MATCH"],
             evidence={"size": "unknown", "tier": 2}, vlm=dict(GOOD_VLM, size_match="unsure"))])
    out = page(r"""
out.labels = ['size_unverified', 'variant_unverified'].map(R.warningText);
out.eligible = [20, 21, 22, 23].map(r => R.bulkEligible(R.storedSelected(itemOf(r).product)));
out.buckets = [20, 21, 22, 23].map(r => itemOf(r).bucket);
R.setMode('bulk');
await flush();
out.cards = document.querySelectorAll('.rv-card').map(c => [c.getAttribute('data-kind'), c.classList.contains('is-selected')]);
out.label = document.getElementById('rvBulkApprove').textContent;
document.getElementById('rvBulkApprove').click();
await flush();
out.sent = requests('/api/select_image').map(c => c.body.row_number);
""", tmp_path, fixture([clean, size, variant, old]))
    assert out["labels"] == ["الحجم غير مؤكد: لم تؤكده صفحة المتجر ولا قراءة الملصق",
                             "النوع غير مؤكد: لم تؤكده صفحة المتجر ولا قراءة الملصق"]
    assert out["eligible"] == [True, False, False, False]
    assert out["buckets"] == ["proposed", "warning", "warning", "warning"]
    assert out["cards"][0] == ["eligible", True] and all(k == ["warning", False] for k in out["cards"][1:])
    assert out["label"] == "اعتماد صورة وحدة بلا تحذير"
    assert out["sent"] == ["20"]


# ---------------------------------------------------------------------------
# 3. Why this image, in plain Arabic
# ---------------------------------------------------------------------------

@NEEDS_NODE
def test_explain_pick_lines_come_from_the_computed_evidence_only(tmp_path):
    out = run(r"""
const c = R.normalizeCandidate({ url: 'https://x/1.jpg', status: 'preselected', evidence: __EV__, vlm: __VLM__ });
out.full = R.explainPick(c);
out.none = R.explainPick(R.normalizeCandidate({ url: 'https://x/2.jpg', evidence: { brand: false, size: 'unknown', consensus_count: 1 } }));
out.official = R.explainPick(R.normalizeCandidate({ url: 'https://x/3.jpg', evidence: { source_class: 'official', consensus_count: 2 } }));
out.cache = R.explainPick(R.normalizeCandidate({ url: 'https://x/4.jpg', reasons: ['cache_hit'] }));
out.spelling = R.warningText('brand_spelling:Rio Mare');
""".replace("__EV__", js(GOOD_EVIDENCE)).replace("__VLM__", js(GOOD_VLM)), tmp_path, fixture([]))
    assert out["full"] == [
        {"key": "gtin", "text": "باركود الصفحة يطابق الشيت"},
        {"key": "consensus", "text": "الصورة نفسها في 3 مصادر"},
        {"key": "source", "text": "متجر في الإمارات"},
        {"key": "page", "text": "الصفحة تذكر: الماركة، الحجم، العبوات"},
        {"key": "label", "text": "الملصق يطابق: الماركة، الحجم، النوع"},
    ]
    assert out["none"] == []                                   # no evidence, nothing claimed
    assert out["official"] == [{"key": "consensus", "text": "الصورة نفسها في مصدرين"},
                               {"key": "source", "text": "موقع الماركة الرسمي"}]
    assert out["cache"] == [{"key": "cache", "text": "اعتُمدت لهذا المنتج سابقاً"}]
    assert "«Rio Mare»" in out["spelling"]


@NEEDS_NODE
def test_the_reasons_show_as_chips_on_the_pick_each_alternative_and_each_bulk_card(tmp_path):
    out = page(r"""
openRow(10);
const chips = node => node.querySelectorAll('.rv-explain__chip').map(c => c.getAttribute('data-explain'));
out.pick = chips(ws().querySelector('.rv-pick'));
out.alts = ws().querySelectorAll('.rv-alt').map(chips);
R.setMode('bulk');
await flush();
out.card = chips(document.querySelector('.rv-card'));
""", tmp_path, fixture([MILK]))
    assert out["pick"] == ["gtin", "consensus", "source", "page", "label"]
    assert out["alts"][0] == ["gtin", "consensus", "source", "page", "label"]     # the system pick among the images
    assert out["alts"][1] == ["page"] and out["alts"][2] == ["page"]
    assert out["card"] == ["gtin", "consensus", "source", "page", "label"]


# ---------------------------------------------------------------------------
# 4. An honest preview
# ---------------------------------------------------------------------------

@NEEDS_NODE
def test_the_preview_says_it_is_the_source_before_background_removal(tmp_path):
    out = page(r"""
openRow(10);
const p = ws().querySelector('.rv-preview');
out.text = p.textContent;
out.label = p.getAttribute('aria-label');
out.alt = p.querySelector('img').getAttribute('alt');
""", tmp_path, fixture([MILK]))
    assert out["label"] == "المصدر قبل عزل الخلفية" and "المصدر قبل عزل الخلفية" in out["text"]
    assert "وليست النتيجة النهائية" in out["text"]
    assert "المعاينة النهائية" not in out["text"] and "هيك رح تنرفع" not in out["text"]
    assert out["alt"] == "الصورة المصدر قبل عزل الخلفية"


# ---------------------------------------------------------------------------
# 5. Approvals whose background removal failed
# ---------------------------------------------------------------------------

BG = product(30, "Almarai Butter 400g", existing_image_link="needs_review:" + CLOUD, needs_review=True,
             needs_review_url=CLOUD, preselected=False, curation_candidates=[])
LEGACY = product(31, "Almarai Cream 200ml", existing_image_link="needs_review:https://www.lulu.com/cream.jpg",
                 needs_review=True, needs_review_url="https://www.lulu.com/cream.jpg", curation_candidates=[])


@NEEDS_NODE
def test_background_failures_have_their_own_chip_banner_and_cosmetic_reasons(tmp_path):
    out = page(r"""
out.buckets = [itemOf(30).bucket, itemOf(31).bucket];
const chip = document.querySelector('[data-filter="bg_failed"]');
out.chip = [chip.textContent, chip.querySelector('.lq-filter__count').textContent];
chip.click();
await flush();
out.list = R.visibleItems().map(it => it.product.row_number);
out.name = productName();
out.text = wsText();
out.badge = ws().querySelector('.rv-badge').textContent;
out.sheet_box = imgSrcs(ws().querySelector('.rv-sheetimg'));
press('x');
out.reasons = document.querySelectorAll('.rv-reason').map(b => b.getAttribute('data-reason'));
out.note = document.querySelector('.rv-reasons').textContent;
document.getElementById('rvRejectResearch').checked = false;
press('1');
await flush();
const reject = requests('/api/reject_image')[0];
out.reject = [reject.body.reason_code, reject.body.image_url, reject.body.search_decision];
""", tmp_path, fixture([BG, LEGACY], rows=[]), config={"filter": "all"})
    assert out["buckets"] == ["bg_failed", "stale"]               # a legacy needs_review source link is not one
    assert out["chip"] == ["الخلفية لم تُعزل1", "1"]
    assert out["list"] == [30] and out["name"] == BG["product_name"]
    assert "الخلفية لم تُعزل:" in out["text"] and "بحاجة مراجعة" in out["text"]
    assert out["badge"] == "معتمدة · الخلفية لم تُعزل"
    assert out["sheet_box"] == [CLOUD]                            # the sheet does hold this image
    assert out["reasons"][:3] == ["HALO_ARTIFACT", "BACKGROUND_BLEED", "CROP_MARGIN_CLIPPING"]
    assert out["reasons"][3:] == ["WRONG_PRODUCT", "WRONG_BRAND", "WRONG_VARIANT", "WRONG_SIZE", "WRONG_PACK",
                                  "NOT_PACKSHOT", "LOW_QUALITY"]
    assert "تخص المعالجة" in out["note"]
    assert out["reject"] == ["HALO_ARTIFACT", CLOUD, ""]


@NEEDS_NODE
def test_the_bg_failed_chip_is_a_deep_link(tmp_path):
    out = page(r"""
out.state = [S().filter, productName()];
""", tmp_path, fixture([product(1, "Waiting Milk 1L"), BG], rows=[]), config={"filter": "bg_failed"})
    assert out["state"] == ["bg_failed", BG["product_name"]]


def test_the_review_page_accepts_the_bg_failed_filter():
    review = (CONTROLLERS / "ReviewController.php").read_text(encoding="utf-8")
    assert "'bg_failed'" in review[review.index("public const FILTERS"):][:200]


@NEEDS_LARAVEL
def test_the_bg_failed_deep_link_reaches_the_page_config(tmp_path):
    res = _http(tmp_path, ["/catalog?filter=bg_failed"], DB_PORT="1")
    assert res[0]["status"] == 200, res[0]["body"][:2000]
    assert _config_of(res[0]["body"])["filter"] == "bg_failed"


# ---------------------------------------------------------------------------
# 6. Faster review: order, bulk keys, requeued products stay listed
# ---------------------------------------------------------------------------

def pick(row, name, brand, kind):
    url = f"https://www.luluhypermarket.com/{row}.jpg"
    if kind == "eligible":
        cands = [cand(url, "preselected", 1, reasons=["vlm:MATCH"])]
    elif kind == "own":
        cands = [cand(url, "eligible", 1)]
    elif kind == "warning":
        cands = [cand(url, "preselected", 1, reasons=["vlm:UNSURE", "warn:vlm_unsure"])]
    else:
        cands = [cand(url, "eligible", 0)]
    return product(row, name, brand=brand, needs_review=True, preselected=kind in ("eligible", "warning"),
                   curation_candidates=cands)


ORDERED = [pick(40, "Zeta Milk", "Zeta", "eligible"), pick(41, "Alpha Laban", "Alpha", "warning"),
           pick(42, "Alpha Milk", "Alpha", "eligible"), pick(43, "Cosmo Juice", "Cosmo", "none"),
           pick(44, "Alpha Cream", "Alpha", "own"), pick(45, "Zeta Laban", "Zeta", "eligible"),
           pick(46, "Beta Water", "Beta", "none")]


@NEEDS_NODE
def test_waiting_products_come_in_order_of_confidence_each_brand_together(tmp_path):
    out = page(r"""
out.single = R.visibleItems().map(it => it.product.row_number);
R.setMode('bulk');
await flush();
out.bulk = document.querySelectorAll('.rv-card').map(c => S().byKey.get(c.getAttribute('data-key')).product.row_number);
""", tmp_path, fixture(ORDERED), config={"filter": "all"})
    # pre-selected without warning (Alpha, then Zeta), the reviewer's pick, with a warning, nothing proposed (by brand)
    assert out["single"] == [42, 40, 45, 44, 41, 46, 43]
    assert out["bulk"] == out["single"]


@NEEDS_NODE
def test_bulk_keys_move_tick_and_approve_like_the_buttons(tmp_path):
    out = page(r"""
R.setMode('bulk');
await flush();
const focused = () => { const c = document.querySelector('.rv-card.is-focused'); return c ? S().byKey.get(c.getAttribute('data-key')).product.row_number : null; };
const ticked = () => R.bulk.tickedKeys().map(k => S().byKey.get(k).product.row_number).sort();
out.start = [focused(), ticked()];
press('ArrowDown');
out.first = [focused(), document.activeElement.getAttribute('data-key') === itemOf(42).key];
press('ArrowLeft');                                   // right-to-left grid: left is the next card
out.second = focused();
press('ArrowRight');
out.back = focused();
press(' ');                                           // un-tick the focused card
out.unticked = ticked();
press(' ');
out.reticked = ticked();
press('ArrowDown'); press('ArrowDown'); press('ArrowDown'); press('ArrowDown');   // the card with a warning (row 41)
out.on_warning = focused();
confirmAnswer = false;
press('a');                                           // its warning is asked first: refused, nothing sent
await flush();
out.refused = [requests('/api/select_image').length, confirms.slice(-1)[0]];
confirmAnswer = true;
press('ArrowUp'); press('ArrowUp'); press('ArrowUp'); press('ArrowUp');           // back to row 42
press('a', { repeat: true });                         // a held key never approves
await flush();
out.repeat = requests('/api/select_image').length;
press('a');
await flush();
out.approved = [requests('/api/select_image').map(c => c.body.row_number), focused()];
press('A', { shiftKey: true });                       // the bulk button: the ticked ones without a warning
await flush();
out.bulk_confirm = confirms.slice(-1)[0];
answer(requests('/api/select_image')[0], { status: 'success', image_link: 'https://res.cloudinary.com/a.png', isolated: true, sheet: 'written' });
await flush();
answer(requests('/api/select_image')[1], { status: 'success', image_link: 'https://res.cloudinary.com/b.png', isolated: true, sheet: 'written' });
await flush();
out.all_sent = requests('/api/select_image').map(c => c.body.row_number);
""", tmp_path, fixture(ORDERED))
    assert out["start"] == [None, [40, 42, 45]]
    assert out["first"] == [42, True]
    assert out["second"] == 40 and out["back"] == 42
    assert out["unticked"] == [40, 45] and out["reticked"] == [40, 42, 45]
    assert out["on_warning"] == 41
    # the confirmation names the product it is about
    assert out["refused"][0] == 0 and out["refused"][1].startswith("«Alpha Laban»: تأكد قبل الاعتماد: نموذج القراءة غير متأكد")
    assert out["repeat"] == 0
    assert out["approved"] == [["42"], 40]                     # the next card is focused after an approval
    assert out["bulk_confirm"].startswith("اعتماد صورتين؟")
    assert out["all_sent"] == ["42", "40", "45"]


B_URLS = ["https://www.lulu.com/b1.jpg", "https://www.lulu.com/b2.jpg"]
B = product(9, "Almarai Fresh Milk 2L", needs_review=True, preselected=True, curation_candidates=[
    cand(B_URLS[0], "preselected", 1, reasons=["vlm:MATCH"]), cand(B_URLS[1], "eligible", 0)])
OTHER = pick(8, "Almarai Laban 2L", "Almarai", "eligible")


@NEEDS_NODE
def test_a_reject_with_research_saves_nothing_from_the_page_and_the_product_stays_in_its_chip(tmp_path):
    out = page(r"""
openRow(9);
out.filter = S().filter;
press('x');
document.getElementById('rvRejectResearch').checked = true;
press('1');
await flush();
const reject = requests('/api/reject_image')[0];
out.research = reject.body.research;
// the server saved the new candidates (no pick this time) and put the product back to review
const answerData = { status: 'review', decision: 'REVIEW_UNSELECTED', sku_key: 'key-9', candidates_saved: 1,
                     candidates: [{ url: 'https://www.lulu.com/b7.jpg', status: 'eligible', title: 'new', warnings: ['foreign_store'] }],
                     rejection: { approval_kept: false, queue_status: 'ready_for_review', candidates_left: 0 } };
FIXTURE.products[0].curation_candidates = [{ image_url: 'https://www.lulu.com/b7.jpg', status: 'eligible', is_selected: 0,
                                             reasons: ['warn:foreign_store'] }];
answer(reject, answerData);
await flush();
out.saved = requests('/api/v1/curation/save-candidates').length;
out.bucket = itemOf(9).bucket;
out.listed = R.visibleItems().map(it => it.product.row_number);
out.alts = altUrls();
out.toast = toasts.slice(-1)[0].text;
out.reloads = requests('/api/products-json').length;
document.querySelector('[data-filter="none"]').click();
document.querySelector('[data-filter="proposed"]').click();
out.after_filter_change = R.visibleItems().map(it => it.product.row_number);
""", tmp_path, fixture([B, OTHER]), config={"filter": "proposed"})
    assert out["filter"] == "proposed" and out["research"] is True
    assert out["saved"] == 0                                   # contract C2: no save-candidates call
    assert out["bucket"] == "none"                             # waiting again, not «back to the queue»
    assert out["listed"] == [8, 9]                             # still in the chip it was reviewed under
    assert out["alts"] == ["https://www.lulu.com/b7.jpg"]
    assert "بانتظار مراجعتك" in out["toast"]
    assert out["reloads"] >= 2                                 # the server's saved state is read back
    assert out["after_filter_change"] == [8]                   # a new chip choice shows the real buckets


@NEEDS_NODE
def test_rejecting_an_alternative_keeps_the_pick_and_the_product_waiting(tmp_path):
    out = page(r"""
openRow(9);
press('2');
press('x');
document.getElementById('rvRejectResearch').checked = false;
press('3');
await flush();
answer(requests('/api/reject_image')[0], { status: 'success', approval_kept: false });
await flush();
out.bucket = itemOf(9).bucket;
out.text = wsText();
out.alts = altUrls();
out.pick = pickUrl();
openRow(8);
openRow(9);
press('x');
press('1');                                           // now the system pick itself
await flush();
answer(requests('/api/reject_image')[1], { status: 'success', approval_kept: false });
await flush();
out.pick_bucket = itemOf(9).bucket;
out.pick_text = wsText();
""", tmp_path, fixture([B, OTHER]), config={"filter": "proposed"})
    assert out["bucket"] == "proposed"
    assert "باقي الصور ما زالت للمراجعة" in out["text"]
    assert out["alts"] == [B_URLS[0]] and out["pick"] == B_URLS[0]
    assert out["pick_bucket"] == "rejected" and "رجع للطابور" in out["pick_text"]


# ---------------------------------------------------------------------------
# C1: stale approvals are replaced only after an explicit confirmation
# ---------------------------------------------------------------------------

@NEEDS_NODE
def test_an_approval_carries_what_the_page_showed_and_a_changed_product_needs_an_explicit_replace(tmp_path):
    out = page(r"""
openRow(9);
press('Enter');
await flush();
const first = requests('/api/select_image')[0];
out.expected = first.body.expected_state;
out.replace = first.body.replace;
answer(first, { status: 'failed', error_code: 'already_approved', error: 'already approved',
                current: { approved_url: 'https://res.cloudinary.com/demo/other.png', queue_status: null, queue_updated_at: null } }, 409);
await flush();
out.toast = toasts.slice(-1)[0];
out.panel = jobsText();
out.buttons = document.querySelectorAll('#rvJobs button').map(b => b.textContent);
out.bucket = itemOf(9).bucket;
const replace = () => document.querySelectorAll('#rvJobs button').find(b => b.textContent === 'استبدال المعتمدة…');
replace().click();
await flush();
// the confirmation shows the image approved now (and for which product) next to the reviewer's pick
const dialog = document.getElementById('rvDialog');
out.dialog = [dialog.hidden, dialog.textContent, imgSrcs(dialog)];
document.getElementById('rvAskCancel').click();
await flush();
out.after_no = [requests('/api/select_image').length, dialog.hidden];
replace().click();
await flush();
document.getElementById('rvAskConfirm').click();
await flush();
const second = requests('/api/select_image')[1];
out.second = [second.body.replace, second.body.expected_state, second.body.image_url];
out.retry_bucket = itemOf(9).bucket;
answer(second, { status: 'success', image_link: 'https://res.cloudinary.com/demo/b.png', isolated: true, sheet: 'written' });
await flush();
out.done = itemOf(9).bucket;
""", tmp_path, fixture([B, OTHER]))
    assert out["expected"] == {"queue_status": "ready_for_review", "queue_updated_at": "2026-10-03 10:00:00",
                               "approved_url": None, "queue_row": 9}
    assert out.get("replace") is None                          # never sent without the reviewer's confirmation
    assert out["toast"]["variant"] == "danger" and "اعتُمدت لهذا المنتج صورة بعد فتح الصفحة" in out["toast"]["text"]
    assert "صار له صورة معتمدة" in out["panel"] and "حالته كانت «بانتظار المراجعة» وصارت «ليس في الطابور»" in out["panel"]
    assert "استبدال المعتمدة…" in out["buttons"] and "أعد المحاولة" not in out["buttons"]
    assert out["bucket"] == "proposed"                          # still waiting: nothing was published
    hidden, text, imgs = out["dialog"]
    assert hidden is False and "استبدال صورة «Almarai Fresh Milk 2L»" in text
    assert "المعتمدة الآن لـ «Almarai Fresh Milk 2L»" in text and "الصورة التي اخترتها" in text
    assert imgs[0] == "https://res.cloudinary.com/demo/other.png" and "b1.jpg" in imgs[1]
    assert out["after_no"] == [1, True]                         # cancelled: nothing sent
    # what the confirmation showed is what the server compares (replace no longer skips the comparison)
    assert out["second"] == [True, {"queue_status": None, "queue_updated_at": None,
                                    "approved_url": "https://res.cloudinary.com/demo/other.png", "queue_row": 9},
                             B_URLS[0]]
    assert out["retry_bucket"] == "approving" and out["done"] == "approved"


@NEEDS_NODE
def test_a_state_changed_answer_says_what_changed_and_an_upload_carries_the_expected_state(tmp_path):
    final = product(7, "Almarai Cheese 200g", existing_image_link="https://res.cloudinary.com/demo/c.png")
    out = page(r"""
openRow(9);
press('Enter');
await flush();
answer(requests('/api/select_image')[0], { status: 'failed', error_code: 'state_changed', error: 'state changed',
                                           current: { queue_status: 'pending', approved_url: null } }, 409);
await flush();
out.panel = jobsText();
openRow(7);                                           // a product with a final image: the upload replaces it
openNotFound();
const file = ws().querySelector('input[type="file"]');
file.files = [new Blob(['png'], { type: 'image/png' })];
dispatch(file, { type: 'change', bubbles: true });
press('Enter');
await flush();
const upload = requests('/api/upload_manual_image')[0];
out.upload_expected = JSON.parse(upload.body.expected_state);
out.upload_replace = upload.body.replace === undefined ? null : upload.body.replace;
""", tmp_path, fixture([B, final], rows=ready_rows([B])))
    assert "تغيّرت حالة المنتج بعد فتح الصفحة" in out["panel"]
    assert "حالته كانت «بانتظار المراجعة» وصارت «في الطابور»" in out["panel"]
    assert out["upload_expected"] == {"queue_status": None, "queue_updated_at": None,
                                      "approved_url": "https://res.cloudinary.com/demo/c.png", "queue_row": None}
    assert out["upload_replace"] is None


# ---------------------------------------------------------------------------
# C3: the sheet outcome is said as the server reports it
# ---------------------------------------------------------------------------

@NEEDS_NODE
@pytest.mark.parametrize("sheet,expect", [
    ("written", "رُفعت الصورة وكُتب رابطها في الشيت."),
    ("pending", "بالانتظار"),
    ("conflict", "لم يُكتب رابطها في الشيت"),
    ("unknown", "لا نعرف إن كُتب رابطها في الشيت"),
    (None, "رُفعت الصورة."),
])
def test_the_sheet_outcome_is_never_claimed_unless_written(tmp_path, sheet, expect):
    reply = {"status": "success", "image_link": "https://res.cloudinary.com/demo/b.png", "isolated": True}
    if sheet:
        reply["sheet"] = sheet
    out = page(r"""
openRow(9);
press('Enter');
await flush();
answer(requests('/api/select_image')[0], __REPLY__);
await flush();
openRow(9);
out.text = wsText();
out.toasts = toasts.map(t => [t.variant, t.text]);
""".replace("__REPLY__", js(reply)), tmp_path, fixture([B, OTHER]))
    assert "تم الاعتماد." in out["text"] and expect in out["text"]
    written_claims = [t for _, t in out["toasts"] if "كُتب رابطها" in t and "لم يُكتب" not in t and "لا نعرف" not in t]
    if sheet != "written":
        assert "وكُتب رابطها في الشيت" not in out["text"]
        assert written_claims == []
    if sheet in ("pending", "conflict", "unknown"):
        variants = {"pending": "warning", "conflict": "danger", "unknown": "warning"}
        assert [v for v, t in out["toasts"] if expect in t] == [variants[sheet]]
    else:
        assert not [t for _, t in out["toasts"] if "الشيت" in t]   # nothing to warn about


@NEEDS_NODE
def test_a_background_failure_toast_says_only_what_the_sheet_reported(tmp_path):
    out = page(r"""
openRow(9);
press('Enter');
await flush();
answer(requests('/api/select_image')[0], { status: 'success', image_link: 'needs_review:https://res.cloudinary.com/demo/b.png',
                                           isolated: false, warning: 'background_not_removed', sheet: 'pending' });
await flush();
out.toast = toasts.slice(-1)[0].text;
""", tmp_path, fixture([B, OTHER]))
    assert "لم تُعزل خلفيتها" in out["toast"] and "بالانتظار" in out["toast"]
    assert "وكُتب رابطها" not in out["toast"]


# ---------------------------------------------------------------------------
# The backend's answers as implemented (P3): busy, current, duplicate_image / duplicate_of, quality_flags,
# rejection.queue_status / candidates_left
# ---------------------------------------------------------------------------

@NEEDS_NODE
def test_rejecting_the_pick_while_other_images_remain_keeps_the_product_waiting(tmp_path):
    three = product(9, "Almarai Fresh Milk 2L", needs_review=True, preselected=True, curation_candidates=[
        cand(B_URLS[0], "preselected", 1, reasons=["vlm:MATCH"]), cand(B_URLS[1], "eligible", 0),
        cand("https://www.lulu.com/b3.jpg", "eligible", 0)])
    out = page(r"""
openRow(9);
press('x');
document.getElementById('rvRejectResearch').checked = false;
press('1');
await flush();
FIXTURE.products[0].curation_candidates = FIXTURE.products[0].curation_candidates.slice(1);   // what the server kept
answer(requests('/api/reject_image')[0], { status: 'success', approval_kept: false, candidates_left: 2,
                                           queue_status: 'ready_for_review', current: { queue_status: 'ready_for_review' } });
await flush();
out.waiting = [itemOf(9).bucket, R.visibleItems().map(it => it.product.row_number)];
out.text = wsText();
out.alts = altUrls();
press('1');
press('x');
press('1');
await flush();
FIXTURE.products[0].curation_candidates = [];
FIXTURE.queue.rows = FIXTURE.queue.rows.map(r => r.row_number === 9 ? Object.assign({}, r, { status: 'pending', failure_code: 'REJECTED' }) : r);
answer(requests('/api/reject_image')[1], { status: 'success', approval_kept: false, candidates_left: 1, queue_status: 'pending' });
await flush();
out.requeued = itemOf(9).bucket;
""", tmp_path, fixture([three, OTHER]), config={"filter": "proposed"})
    assert out["waiting"] == ["none", [8, 9]]                  # no pick left, still waiting and still in its chip
    assert "باقي الصور ما زالت للمراجعة (2)" in out["text"] and "رجع للطابور" not in out["text"]
    assert out["alts"] == [B_URLS[1], "https://www.lulu.com/b3.jpg"]
    assert out["requeued"] in ("rejected", "requeued")         # the server put it back to the queue


@NEEDS_NODE
def test_a_bulk_reject_says_back_to_the_queue_only_when_the_server_requeued(tmp_path):
    two = product(9, "Almarai Fresh Milk 2L", needs_review=True, preselected=True, curation_candidates=[
        cand(B_URLS[0], "preselected", 1, reasons=["vlm:MATCH"]), cand(B_URLS[1], "eligible", 0)])
    out = page(r"""
R.setMode('bulk');
await flush();
R.bulk.rejectList([itemOf(9), itemOf(8)], 'WRONG_SIZE');
await flush();
const overlays = () => Object.fromEntries(document.querySelectorAll('.rv-card').map(c => [
    S().byKey.get(c.getAttribute('data-key')).product.row_number,
    (c.querySelector('.rv-card__overlay') || { textContent: '' }).textContent]));
FIXTURE.products[0].curation_candidates = FIXTURE.products[0].curation_candidates.slice(1);   // what the server kept
answer(requests('/api/reject_image')[0], { status: 'success', approval_kept: false, candidates_left: 1, queue_status: 'ready_for_review' });
await flush();
out.first = [itemOf(9).bucket, overlays()[9]];
answer(requests('/api/reject_image')[1], { status: 'success', approval_kept: false, candidates_left: 0, queue_status: 'pending' });
await flush();
out.second = [itemOf(8).bucket, overlays()[8]];
""", tmp_path, fixture([two, OTHER]))
    assert out["first"] == ["none", ""]                       # still waiting, without its rejected pick
    assert out["second"] == ["rejected", "رجعت للطابور"]


@NEEDS_NODE
def test_a_busy_refusal_is_retried_not_replaced(tmp_path):
    out = page(r"""
openRow(9);
press('Enter');
await flush();
answer(requests('/api/select_image')[0], { status: 'failed', error_code: 'busy',
                                           error: 'نشر آخر لهذا المنتج ما زال يكتب في الشيت؛ حاول بعد قليل' }, 500);
await flush();
out.panel = jobsText();
out.buttons = document.querySelectorAll('#rvJobs button').map(b => b.textContent).filter(t => t);
document.querySelectorAll('#rvJobs button').find(b => b.textContent === 'أعد المحاولة').click();
await flush();
const again = requests('/api/select_image')[1];
out.again = [again.body.replace === undefined, again.body.expected_state.queue_status];
""", tmp_path, fixture([B, OTHER]))
    assert "نشر آخر لهذا المنتج ما زال يكتب في الشيت" in out["panel"]
    assert "أعد المحاولة" in out["buttons"] and "استبدال المعتمدة…" not in out["buttons"]
    assert out["again"] == [True, "ready_for_review"]


@NEEDS_NODE
def test_duplicate_and_cutout_flags_are_said_and_a_duplicate_is_not_called_a_background_failure(tmp_path):
    out = page(r"""
openRow(9);
press('Enter');
await flush();
answer(requests('/api/select_image')[0], { status: 'success', image_link: 'https://res.cloudinary.com/demo/b.png', isolated: true,
    sheet: 'written', warning: 'duplicate_image', warnings: ['duplicate_image'],
    duplicate_of: [{ sku_key: 'k-x', product_name: 'Almarai Milk 2L Twin Pack', cloudinary_url: 'https://res.cloudinary.com/demo/b.png' }] });
await flush();
out.dup_toasts = toasts.map(t => t.text);
openRow(9);
out.dup_text = wsText();
openRow(8);
press('Enter');
await flush();
answer(requests('/api/select_image')[1], { status: 'success', image_link: 'needs_review:https://res.cloudinary.com/demo/l.png',
    isolated: false, sheet: 'written', warning: 'background_not_removed', warnings: ['background_not_removed'],
    quality_flags: ['edge_clipped', 'alpha_haze', 'brand_new_flag'] });
await flush();
out.bg_toast = toasts.slice(-1)[0].text;
openRow(8);
out.bg_text = wsText();
out.bg_title = ws().querySelectorAll('.rv-alert').map(a => a.getAttribute('title')).filter(Boolean);
""", tmp_path, fixture([B, OTHER]))
    assert not any("لم تُعزل خلفيتها" in t for t in out["dup_toasts"])
    assert any("نفسها منشورة لمنتج آخر: «Almarai Milk 2L Twin Pack»" in t for t in out["dup_toasts"])
    assert "الصورة نفسها لمنتج آخر:" in out["dup_text"] and "الخلفية لم تُعزل:" not in out["dup_text"]
    assert "المنتج مقصوص عند حافة الصورة" in out["bg_toast"] and "هالة أو ضباب حول حواف المنتج" in out["bg_toast"]
    assert "ملاحظة أخرى من فحص القص" in out["bg_toast"]
    assert "edge_clipped" not in out["bg_toast"] + out["bg_text"] and "brand_new_flag" not in out["bg_text"]
    assert "فحص القص:" in out["bg_text"] and out["bg_title"] == ["edge_clipped · alpha_haze · brand_new_flag"]


@NEEDS_NODE
def test_the_next_approval_of_the_same_product_carries_the_state_the_server_returned(tmp_path):
    current = {"queue_status": "completed", "queue_updated_at": "2026-10-03 11:00:00",
               "approved_url": "https://res.cloudinary.com/demo/b.png", "approval_status": "human_approved"}
    out = page(r"""
openRow(9);
press('Enter');
await flush();
answer(requests('/api/select_image')[0], { status: 'success', image_link: 'https://res.cloudinary.com/demo/b.png',
                                           isolated: true, sheet: 'written', current: __CUR__ });
await flush();
openRow(9);
ws().querySelector('.rv-research').click();          // replace the approved image: an explicit new search
await flush();
answer(requests('/api/search')[0], { status: 'review', decision: 'REVIEW_UNSELECTED', sku_key: 'key-9',
                                     candidates: [{ url: 'https://www.lulu.com/b8.jpg', status: 'eligible', title: 'new' }] });
await flush();
press('1'); press('Enter');
await flush();
out.second = requests('/api/select_image')[1].body.expected_state;
""".replace("__CUR__", js(current)), tmp_path, fixture([B, OTHER]))
    assert out["second"] == {"queue_status": "completed", "queue_updated_at": "2026-10-03 11:00:00",
                             "approved_url": "https://res.cloudinary.com/demo/b.png", "queue_row": None}


# ---------------------------------------------------------------------------
# wp/p4-publish-fix: an image another reviewer rejected after the page opened (state_changed, image_rejected)
# ---------------------------------------------------------------------------

@NEEDS_NODE
def test_an_image_another_reviewer_rejected_is_said_and_never_offered_for_replacement(tmp_path):
    out = page(r"""
openRow(9);
press('Enter');
await flush();
answer(requests('/api/select_image')[0], { status: 'failed', error_code: 'state_changed', reason: 'image_rejected',
    error: 'هذه الصورة رفضها مراجع آخر لهذا المنتج؛ اختر صورة أخرى',
    current: { queue_status: 'ready_for_review', queue_updated_at: '2026-10-03 10:00:00', approved_url: null,
               rejected_image: true } }, 500);
await flush();
out.toast = toasts.slice(-1)[0];
out.panel = jobsText();
out.buttons = document.querySelectorAll('#rvJobs button').map(b => b.textContent).filter(t => t);
out.bucket = itemOf(9).bucket;
""", tmp_path, fixture([B, OTHER]))
    assert out["toast"]["variant"] == "danger"
    assert "رفضها مراجع آخر لهذا المنتج" in out["toast"]["text"] and "استبدال" not in out["toast"]["text"]
    assert "رفضها مراجع آخر لهذا المنتج" in out["panel"] and "تغيّرت حالة المنتج" not in out["panel"]
    assert "استبدال المعتمدة…" not in out["buttons"] and "أعد المحاولة" not in out["buttons"]
    assert out["bucket"] == "proposed"                          # nothing was published: still waiting


# ---------------------------------------------------------------------------
# wp/p4-publish-fix C: the cut-out check's flags are named, and presentation-only flags are published only after an
# explicit confirmation (publish_anyway, the same pattern as replace)
# ---------------------------------------------------------------------------

@NEEDS_NODE
def test_presentation_flags_are_named_and_published_only_after_an_explicit_confirmation(tmp_path):
    out = page(r"""
openRow(9);
press('Enter');
await flush();
const first = requests('/api/select_image')[0];
out.first_anyway = first.body.publish_anyway === undefined ? null : first.body.publish_anyway;
answer(first, { status: 'failed', error_code: 'quality_flags', error: 'quality', quality_flags: ['upscaled', 'too_small_on_canvas'],
                publish_anyway_allowed: true, current: { queue_status: 'ready_for_review' } }, 500);
await flush();
out.toast = toasts.slice(-1)[0];
out.panel = jobsText();
const anyway = () => document.querySelectorAll('#rvJobs button').find(b => b.textContent === 'اعتمدها رغم هيك…');
out.buttons = document.querySelectorAll('#rvJobs button').map(b => b.textContent).filter(t => t);
out.bucket = itemOf(9).bucket;
confirmAnswer = false;
anyway().click();
await flush();
out.after_no = [requests('/api/select_image').length, confirms.slice(-1)[0]];
confirmAnswer = true;
anyway().click();
await flush();
const second = requests('/api/select_image')[1];
out.second = [second.body.publish_anyway, second.body.replace === undefined, second.body.image_url];
answer(second, { status: 'success', image_link: 'https://res.cloudinary.com/demo/b.png', isolated: false, sheet: 'written',
                 published_anyway: true, warning: 'quality_flags', warnings: ['quality_flags'], quality_flags: ['upscaled'] });
await flush();
out.done = [itemOf(9).bucket, toasts.slice(-1)[0].text];
""", tmp_path, fixture([B, OTHER]))
    assert out["first_anyway"] is None                          # never sent without the reviewer's confirmation
    assert out["toast"]["variant"] == "danger" and "ما انعتمدت صورة" in out["toast"]["text"]
    assert "الصورة المصدر صغيرة فكُبّرت" in out["panel"] and "المنتج صغير على اللوحة" in out["panel"]
    assert "upscaled" not in out["panel"] and "ما انعزلت خلفية" not in out["panel"]
    assert "اعتمدها رغم هيك…" in out["buttons"] and "أعد المحاولة" not in out["buttons"]
    assert out["bucket"] == "proposed"                          # nothing was published: still waiting
    assert out["after_no"][0] == 1 and "الصورة المصدر صغيرة فكُبّرت" in out["after_no"][1]
    assert out["second"] == [True, True, B_URLS[0]]
    assert out["done"][0] == "approved" and "رغم ملاحظات فحص القص: الصورة المصدر صغيرة فكُبّرت" in out["done"][1]
    assert "لم تُعزل خلفيتها" not in out["done"][1]


@NEEDS_NODE
def test_a_failed_background_removal_is_named_and_never_offered_for_publishing(tmp_path):
    out = page(r"""
openRow(9);
press('Enter');
await flush();
answer(requests('/api/select_image')[0], { status: 'failed', error_code: 'background_failed', error: 'bg',
                                           quality_flags: ['edge_clipped'], publish_anyway_allowed: false }, 500);
await flush();
out.toast = toasts.slice(-1)[0].text;
out.panel = jobsText();
out.buttons = document.querySelectorAll('#rvJobs button').map(b => b.textContent).filter(t => t);
""", tmp_path, fixture([B, OTHER]))
    assert "ما انعزلت خلفية الصورة (المنتج مقصوص عند حافة الصورة)" in out["panel"]
    assert "edge_clipped" not in out["panel"] and "رغم هيك" not in out["toast"]
    assert "اعتمدها رغم هيك…" not in out["buttons"] and "أعد المحاولة" not in out["buttons"]


@NEEDS_NODE
def test_an_upload_publishes_anyway_only_after_the_confirmation(tmp_path):
    final = product(7, "Almarai Cheese 200g", existing_image_link="https://res.cloudinary.com/demo/c.png")
    out = page(r"""
openRow(7);
openNotFound();
const file = ws().querySelector('input[type="file"]');
file.files = [new Blob(['png'], { type: 'image/png' })];
dispatch(file, { type: 'change', bubbles: true });
press('Enter');
await flush();
const first = requests('/api/upload_manual_image')[0];
out.first = first.body.publish_anyway === undefined ? null : first.body.publish_anyway;
answer(first, { status: 'failed', error_code: 'quality_flags', quality_flags: ['alpha_haze'], publish_anyway_allowed: true }, 500);
await flush();
confirmAnswer = true;
document.querySelectorAll('#rvJobs button').find(b => b.textContent === 'اعتمدها رغم هيك…').click();
await flush();
out.second = requests('/api/upload_manual_image')[1].body.publish_anyway;
""", tmp_path, fixture([B, final], rows=ready_rows([B])))
    assert out["first"] is None and out["second"] == "1"


@NEEDS_NODE
def test_a_kept_shadow_is_named_and_an_upscaled_note_is_a_note_not_a_warning(tmp_path):
    out = page(r"""
openRow(9);
press('Enter');
await flush();
answer(requests('/api/select_image')[0], { status: 'failed', error_code: 'quality_flags', quality_flags: ['kept_shadow'],
                                           publish_anyway_allowed: true }, 500);
await flush();
out.panel = jobsText();
out.buttons = document.querySelectorAll('#rvJobs button').map(b => b.textContent).filter(t => t);
openRow(8);
press('Enter');
await flush();
answer(requests('/api/select_image')[1], { status: 'success', image_link: 'https://res.cloudinary.com/demo/l.png',
                                           isolated: true, sheet: 'written', quality_notes: ['upscaled'] });
await flush();
out.toasts = toasts.map(t => [t.variant, t.text]);
openRow(8);
out.text = wsText();
""", tmp_path, fixture([B, OTHER]))
    assert "بقي ظل ظاهر مع المنتج" in out["panel"] and "kept_shadow" not in out["panel"]
    assert "اعتمدها رغم هيك…" in out["buttons"]
    assert "ملاحظة من فحص القص:" in out["text"] and "الصورة المصدر صغيرة فكُبّرت لتملأ اللوحة" in out["text"]
    assert "الخلفية لم تُعزل" not in out["text"]
    assert not [t for v, t in out["toasts"] if "كُبّرت" in t]     # a note is not a warning toast
