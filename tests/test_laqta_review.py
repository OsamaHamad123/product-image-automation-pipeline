"""Review package (Laqta design): /catalog in two modes, the numbers behind it, and the review-flow safety rules
that are not covered by tests/test_catalog_review_race.py.

* routes: /catalog renders on layouts.laqta (single and ?mode=bulk, deep links ?row=N and ?filter=failed),
  /errors and /rich-catalog redirect, the CSV export stays; the page prints no stored secret;
* one number from one source: «بانتظار المراجعة» counts the automation_queue rows that are ready_for_review, the same
  rows (and query) as the sidebar badge (/api/batch-status); every chip count equals the rows it lists;
* bulk approval takes only images the system pre-selected WITHOUT a warning, one request at a time in the background,
  with progress, failures with their reason, a retry, and a prompt before leaving while approvals run;
* plain Arabic: no raw decision / failure / Gemini codes as visible text; truthful loading, error and empty states.

The page scripts run under node (tests/laqta_review_harness.py); the Laravel checks boot the app through the PHP CLI
and are skipped when php or dashboard/vendor is missing (the database checks also need MariaDB).
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
from blade_scripts import script_sources

from laqta_review_harness import NODE, REVIEW_JS, run

ROOT = Path(__file__).resolve().parents[1]
DASH = ROOT / "dashboard"
VIEWS = DASH / "resources" / "views"
CATALOG = VIEWS / "dashboard" / "catalog.blade.php"
SHELL = VIEWS / "review" / "shell.blade.php"
CSS = DASH / "public" / "css" / "pages" / "review.css"
LAQTA_CSS = DASH / "public" / "css" / "laqta.css"
CONTROLLERS = DASH / "app" / "Http" / "Controllers"
REVIEW_CONTROLLER = CONTROLLERS / "ReviewController.php"
ROUTES = DASH / "routes" / "web.php"
VENDOR_AUTOLOAD = DASH / "vendor" / "autoload.php"
PHP = shutil.which("php")

NEEDS_NODE = pytest.mark.skipif(NODE is None, reason="node is not installed")
NEEDS_LARAVEL = pytest.mark.skipif(PHP is None or not VENDOR_AUTOLOAD.exists(), reason="php or dashboard/vendor is not installed")

SCRIPT_FILES = [REVIEW_JS / f"{n}.js" for n in ("core", "ui", "jobs", "single", "bulk", "app")]

# Codes the pipeline emits that must never be the visible text of the review screen.
RAW_CODES = re.compile(r"REVIEW_PRESELECTED|REVIEW_UNSELECTED|AUTO_PUBLISH|NOT_FOUND|NO_RESULTS|ALL_CONFLICTED|"
                       r"PROVIDER_DOWN|VERIFIER_DOWN|SEARCH_ERROR|WRONG_[A-Z]+|NOT_PACKSHOT|LOW_QUALITY|vlm:|warn:|"
                       r"preselected:|auto_blocked|sheet_silent|front_packshot|\bMATCH\b|\bMISMATCH\b|\bUNSURE\b|"
                       r"\bT[1-4]\b|\beligible\b|\bpreselected\b")


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def js(value):
    return json.dumps(value, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Fixture products (the shapes /api/products-json and /api/review/queue-state return)
# ---------------------------------------------------------------------------

def cand(url, status="eligible", selected=0, **extra):
    row = {"image_url": url, "status": status, "is_selected": selected, "title": url.rsplit("/", 1)[-1], "reasons": []}
    row.update(extra)
    return row


def product(row, name, brand="Al Alali", **extra):
    p = {"row_number": row, "product_name": name, "brand": brand, "barcode": "", "sku_key": f"key-{row}", "size": "85GM",
         "product_name_ar": "", "brand_ar": "", "category": "Canned > Tuna", "sub_category": "", "origin": "",
         "existing_image_link": "", "needs_review": False, "search_query": f"{name} {brand}"}
    p.update(extra)
    return p


P1 = product(19, "AL ALALI FANCY TUNA WATER 170GM", curation_candidates=[
    cand("https://www.luluhypermarket.com/p19.jpg", "preselected", 1, reasons=["vlm:MATCH", "preselected:vlm_match", "auto_blocked:not_tier1"],
         identity_tier=1, evidence={"brand": "Al Alali", "size": "match", "tier": 1}, vlm={"decision": "MATCH", "brand_text": "Al Alali", "size_match": "yes", "brand_match": "yes",
                               "variant_match": "yes", "view": "front_packshot", "size_text": "170g"}),
    cand("https://www.noon.com/p19b.jpg", reasons=["vlm:UNSURE"])], needs_review=True, preselected=True)
P2 = product(20, "ALALALI FANCY TUNA S/F OIL 85GM", curation_candidates=[
    cand("https://www.luluhypermarket.com/p20.jpg", "preselected", 1, reasons=["vlm:MATCH"], evidence={"size": "match"})],
    needs_review=True, preselected=True)
W = product(21, "ALALALI FANCY TUNA WATER 85GM", curation_candidates=[
    cand("https://www.carrefouruae.com/p21.jpg", "preselected", 1, reasons=["vlm:UNSURE", "warn:vlm_unsure"],
         vlm={"decision": "UNSURE", "size_match": "unsure"})], needs_review=True, preselected=True)
N = product(22, "ALALALI WHITE TUNA S/F OIL 85GM", curation_candidates=[
    cand("https://www.amazon.ae/p22.jpg", "rejected", 0, reasons=["vlm:MISMATCH"])], needs_review=True)
OWN = product(23, "ALALALI WHITE TUNA WATER 170GM", brand="Other Brand", curation_candidates=[
    cand("https://www.talabat.com/p23.jpg", "eligible", 1, evidence={"size": "match"})], needs_review=True)
NF = product(30, "Healthy Farms Fresh Eggs 30 pcs", brand="Healthy Farms", has_error=True,
             error_message="NO_RESULTS: No acceptable image found (NO_RESULTS)")
FAIL = product(31, "Broken Juice 1L", brand="Juicy", barcode="6291003000017", has_error=True,
               error_message="SEARCH_ERROR: search raised an exception")
STALE = product(40, "Stale Milk 1L", brand="Almarai", curation_candidates=[cand("https://www.lulu.com/s.jpg", "preselected", 1)],
                needs_review=True)
DONE = product(41, "Done Cheese 200g", brand="Almarai", existing_image_link="https://res.cloudinary.com/demo/done.png")
IDLE = product(42, "Idle Laban 1L", brand="Almarai")

READY = [P1, P2, W, N, OWN]


def queue_rows(ready=READY, extra=()):
    rows = [{"row_number": p["row_number"], "sku_key": p["sku_key"], "status": "ready_for_review", "failure_code": None,
             "product_name": p["product_name"], "brand": p["brand"]} for p in ready]
    rows += list(extra)
    return rows


ORPHAN = {"row_number": 99, "sku_key": "key-99", "status": "ready_for_review", "failure_code": None,
          "product_name": "Gone From Sheet 1KG", "brand": "Gone"}
EXTRA_ROWS = [
    {"row_number": 30, "sku_key": "key-30", "status": "failed", "failure_code": "NO_RESULTS", "product_name": NF["product_name"], "brand": NF["brand"]},
    {"row_number": 31, "sku_key": "key-31", "status": "failed", "failure_code": "SEARCH_ERROR", "product_name": FAIL["product_name"], "brand": FAIL["brand"]},
    {"row_number": 40, "sku_key": "key-40", "status": "pending", "failure_code": "REJECTED", "product_name": STALE["product_name"], "brand": STALE["brand"]},
]


def fixture(products=None, rows=None, ready_count=None, **extra):
    products = list(products if products is not None else READY + [NF, FAIL, STALE, DONE, IDLE])
    rows = rows if rows is not None else queue_rows(extra=EXTRA_ROWS + [ORPHAN])
    ready = ready_count if ready_count is not None else sum(1 for r in rows if r["status"] == "ready_for_review")
    fx = {"products": products, "queue": {"status": "success", "ready_for_review": ready, "rows": rows}}
    fx.update(extra)
    return fx


def page(scenario, tmp_path, fx=None, config=None):
    return run(f"await boot({js(config or {})});\n" + scenario, tmp_path, fx or fixture())


VISIBLE_TEXT = r"""
function visibleText(node) {
    if (!node) return '';
    if (node.nodeType === 3) return node.data;
    if (node.hidden || node.classList.contains('rv-details') || node.tagName === 'SVG') return '';
    return node.childNodes.map(visibleText).join(' ');
}
"""


# ---------------------------------------------------------------------------
# The page and its routes (static)
# ---------------------------------------------------------------------------

def test_catalog_extends_the_laqta_layout_and_loads_the_review_scripts():
    page_src = read(CATALOG)
    body = re.sub(r"\{\{--.*?--\}\}", "", page_src, flags=re.DOTALL).strip()
    assert body.startswith("@extends('layouts.laqta')")
    for directive in ("@section('title', 'المراجعة')", "@section('lq_nav', 'review')",
                      "@section('lq_main_class', 'lq-main--flush')", "@push('styles')", "@push('scripts')",
                      "@include('review.shell'"):
        assert directive in page_src, directive
    assert "css/pages/review.css" in page_src
    assert "['core', 'ui', 'jobs', 'single', 'bulk', 'app']" in page_src
    assert "<script>" not in page_src and "layouts.layout" not in page_src
    shell = read(SHELL)
    assert 'id="rvApp"' in shell and "data-config=\"{{ json_encode($config" in shell
    assert "{!!" not in page_src + shell
    for path in SCRIPT_FILES:
        assert path.exists(), path


def test_routes_point_at_the_review_screen():
    routes = read(ROUTES)
    assert "Route::get('/catalog', [ReviewController::class, 'page'])->name('dashboard.catalog');" in routes
    assert "Route::get('/api/review/queue-state', [ReviewController::class, 'queueState']);" in routes
    # the old pages are redirects now, and keep their names (old links and the old layout still use them)
    assert "->name('dashboard.errors')" in routes and "->name('dashboard.rich_catalog')" in routes
    assert "->name('dashboard.rich_catalog.export')" in routes
    for gone in ("errors.blade.php", "rich_catalog.blade.php"):
        assert not (VIEWS / "dashboard" / gone).exists(), gone
    product = read(CONTROLLERS / "ProductController.php")
    errors = product[product.index("public function errors()"):]
    assert "redirect()->route('dashboard.catalog', ['filter' => 'failed'])" in errors[:400]
    rich = product[product.index("public function richCatalog()"):]
    assert "redirect()->route('dashboard.catalog')" in rich[:300]


def test_the_waiting_count_and_the_badge_come_from_one_query():
    review = read(REVIEW_CONTROLLER)
    api = read(CONTROLLERS / "ApiController.php")
    batch = api[api.index("public function batchStatus()"):api.index("public function pauseBatch()")]
    assert "QueueStats::counters()" in batch and "$counters['by_status']['ready_for_review']" in batch
    queue_state = review[review.index("public function queueState()"):review.index("public static function presentQueueRow")]
    assert "QueueStats::counters()" in queue_state
    assert "'ready_for_review' => (int) ($counters['by_status']['ready_for_review'] ?? 0)" in queue_state
    ready = review[review.index("public static function readyForReview()"):review.index("public static function canvasSize()")]
    assert "QueueStats::counters()" in ready
    page_method = review[review.index("public function page("):review.index("public function queueState()")]
    assert "'lqReviewCount' => $readyForReview" in page_method        # the badge's first number is this one
    # the page counts a product as waiting only when its queue row is ready_for_review
    core = read(REVIEW_JS / "core.js")
    classify = core[core.index("function classify("):core.index("const WAITING")]
    assert "if (q && q.status === 'ready_for_review') return waitingBucket;" in classify


def test_review_scripts_are_safe_and_contain_no_secrets():
    for path in SCRIPT_FILES:
        text = read(path)
        assert "innerHTML" not in text.replace("never innerHTML", ""), path.name
        assert "eval(" not in text and "new Function" not in text, path.name
        assert "insertAdjacentHTML" not in text and "document.write" not in text, path.name
        assert not re.search(r"api[_-]?key|secret|password", text, re.IGNORECASE), path.name
        assert "alert(" not in text, path.name                      # messages go to the page, not to a blocking alert


def test_css_uses_the_design_tokens_and_logical_properties():
    css = read(CSS)
    laqta = read(LAQTA_CSS)
    defined = set(re.findall(r"(--lq-[a-z0-9-]+)\s*:", laqta)) | set(re.findall(r"(--rv-[a-z0-9-]+)", css))
    used = set(re.findall(r"var\((--lq-[a-z0-9-]+)", css))
    assert not sorted(used - defined), sorted(used - defined)
    for physical in ("margin-left", "margin-right", "padding-left", "padding-right"):
        assert physical not in css, physical
    assert not re.search(r"(?<![-\w])(left|right)\s*:", css), "use inset-inline-start/end"
    assert "@media (max-width: 980px)" in css and "@media (max-width: 560px)" in css
    # every lq- class the review files use exists in laqta.css, every rv- class in review.css
    sources = "\n".join(read(p) for p in SCRIPT_FILES + [CATALOG, SHELL])
    # classes built from a value (rv-tone--${tone}, lq-chip--${chip}) are checked through their values below
    sources = re.sub(r"(?:rv|lq)-[a-z0-9_-]*--\$\{[^}]*\}", "", sources)
    class_re = r"(?<![\w-])({0}-[a-z0-9]+(?:(?:__|--|-)[a-z0-9]+)*)"
    lq_used = set(re.findall(class_re.format("lq"), sources)) - {"lq-review-count", "lq-status-url"}
    lq_defined = set(re.findall(r"\.(lq-[a-z0-9]+(?:(?:__|--|-)[a-z0-9]+)*)", laqta))
    assert not sorted(lq_used - lq_defined), sorted(lq_used - lq_defined)
    rv_used = set(re.findall(class_re.format("rv"), sources))
    rv_defined = set(re.findall(r"\.(rv-[a-z0-9]+(?:(?:__|--|-)[a-z0-9]+)*)", css))
    assert not sorted(rv_used - rv_defined), sorted(rv_used - rv_defined)
    core = read(REVIEW_JS / "core.js")
    for chip in set(re.findall(r"chip: '([a-z-]+)'", core)):
        assert f".lq-chip--{chip}" in laqta, chip
    for tone in ("success", "warning", "danger", "info", "muted"):
        assert f".rv-tone--{tone}" in css
    for tone in ("missing", "muted"):
        assert f".rv-fact--{tone}" in css


def test_review_files_have_no_emoji():
    emoji = re.compile("[\U0001F300-\U0001FAFF☀-➿️]")
    for path in SCRIPT_FILES + [CATALOG, SHELL, CSS]:
        assert not emoji.findall(read(path)), path.name


# ---------------------------------------------------------------------------
# Numbers: one source, and every chip equals the rows it lists
# ---------------------------------------------------------------------------

@NEEDS_NODE
def test_waiting_count_equals_the_ready_for_review_rows(tmp_path):
    out = page(r"""
out.counts = S().counts;
out.ready = FIXTURE.queue.ready_for_review;
out.header = document.getElementById('rvWaiting').textContent;
out.buckets = Object.fromEntries(S().items.map(it => [it.product.row_number, it.bucket]));
out.chips = document.querySelectorAll('.rv-filter').map(b => [b.getAttribute('data-filter'), b.querySelector('.lq-filter__count').textContent]);
out.list_lengths = Object.fromEntries(R.FILTERS.map(f => [f.key, R.filterItems(S().items, f.key, '').length]));
""", tmp_path)
    c = out["counts"]
    # five ready rows in the sheet + one ready row whose product left the sheet = the badge's number
    assert out["ready"] == 6 and c["waiting"] == 6
    assert out["header"] == "6 بانتظار المراجعة"
    assert out["buckets"]["19"] == "proposed" and out["buckets"]["21"] == "warning" and out["buckets"]["22"] == "none"
    assert out["buckets"]["23"] == "proposed" and out["buckets"]["99"] == "none"
    assert out["buckets"]["30"] == "not_found" and out["buckets"]["31"] == "failed"
    # stored images but the queue row went back to pending: not counted as waiting
    assert out["buckets"]["40"] == "requeued" and out["buckets"]["41"] == "approved" and out["buckets"]["42"] == "idle"
    assert c["proposed"] == 4 and c["warning"] == 1 and c["none"] == 2 and c["not_found"] == 1 and c["failed"] == 1
    assert c["proposed"] + c["none"] == c["waiting"]
    # each chip shows exactly the number of rows it lists
    chips = dict(out["chips"])
    for key, length in out["list_lengths"].items():
        assert chips[key] == str(length), key
    assert chips["all"] == str(len(READY) + 5 + 1)


@NEEDS_NODE
def test_the_counts_say_when_the_queue_state_is_unknown(tmp_path):
    fx = fixture(status={"/api/review/queue-state": 503})
    fx["queue"] = {"status": "unavailable", "error": "قاعدة البيانات غير متاحة: لا يمكن معرفة ما ينتظر المراجعة الآن."}
    out = page(r"""
out.header = document.getElementById('rvWaiting').textContent;
out.note = document.querySelector('.rv-queue__note').textContent;
out.waiting = S().counts.waiting;
""", tmp_path, fx)
    assert out["header"].endswith("(تقدير)")
    assert "قاعدة البيانات غير متاحة" in out["note"] and "ممكن تختلف عن الشارة" in out["note"]
    assert out["waiting"] == 6                                  # estimated from the stored images


@NEEDS_NODE
def test_loading_and_error_states_are_explicit(tmp_path):
    # the sheet cannot be read but the queue can: its ready rows are not shown as "missing from the sheet"
    fx = fixture(status={"/api/products-json": 500})
    fx["products"] = []
    out = run(r"""
const root = document.createElement('div');
root.setAttribute('id', 'rvApp');
root.setAttribute('data-config', JSON.stringify({ mode: 'single', db: 'online', autoSearchDelayMs: 0 }));
document.body.appendChild(root);
const realFetch = globalThis.fetch;
let release;
const gate = new Promise(r => { release = r; });
globalThis.fetch = (url, init) => (String(url).startsWith('/api/products-json') ? gate.then(() => realFetch(url, init)) : realFetch(url, init));
R.boot();
await flush();
out.loading_header = document.getElementById('rvWaiting').textContent;
out.loading_state = S().ws.state;
out.loading_chips = document.querySelectorAll('.lq-filter__count').map(n => n.textContent);
release();
await flush();
out.error_header = document.getElementById('rvWaiting').textContent;
out.error_text = document.getElementById('rvList').textContent;
out.ws_text = wsText();
""", tmp_path, dict(fx, products=[], status={"/api/products-json": 500}))
    assert out["loading_header"] == "لحظة…" and out["loading_state"] == "loading"
    assert set(out["loading_chips"]) == {"…"}                    # never a zero while loading
    assert out["error_header"] == "—"
    assert "ما انفتحت القائمة" in out["error_text"] and "جرّب مرة ثانية" in out["error_text"]
    assert "ما قدرنا نفتح قائمة المراجعة" in out["ws_text"] and "مش موجود بالشيت" not in out["ws_text"]


@NEEDS_NODE
def test_empty_queue_says_nothing_is_waiting(tmp_path):
    out = page(r"""
out.text = wsText();
out.list = document.getElementById('rvList').textContent;
""", tmp_path, fixture(products=[DONE], rows=[]), config={"filter": "proposed"})
    # nothing waiting: the recap of the day and the next steps, not a dead end
    assert "خلصت المراجعة" in out["text"] and "اليوم: اعتمدت 0، رفضت 0" in out["text"] and "تشغيل جديد" in out["text"]
    assert "ما في صور مقترحة بانتظارك" in out["list"]


@NEEDS_NODE
def test_a_ready_row_missing_from_the_sheet_is_listed_not_hidden(tmp_path):
    out = page(r"""
const orphan = S().items.find(it => it.orphan);
R.single.openItem(orphan.key);
out.text = wsText();
out.approve_disabled = approveBtn().disabled;
out.in_list = document.querySelectorAll('.rv-item').some(b => b.getAttribute('data-key') === orphan.key);
""", tmp_path, config={"filter": "none"})
    assert "مش موجود بالشيت الحالي" in out["text"] and "صف 99" in out["text"]
    assert out["approve_disabled"] is True and out["in_list"] is True


# ---------------------------------------------------------------------------
# Plain Arabic and the warning before approval
# ---------------------------------------------------------------------------

@NEEDS_NODE
def test_no_raw_codes_are_shown(tmp_path):
    out = page(VISIBLE_TEXT + r"""
const texts = [];
for (const row of [19, 21, 22, 30, 31, 40, 41]) {
    R.single.openItem(itemOf(row).key);
    await flush();
    texts.push(visibleText(document.getElementById('rvApp')));
}
R.setMode('bulk');
await flush();
texts.push(visibleText(document.getElementById('rvApp')));
out.texts = texts;
""", tmp_path)
    for text in out["texts"]:
        found = RAW_CODES.findall(text)
        assert not found, (found, text[:400])
    joined = " ".join(out["texts"])
    for arabic in ("مقترحة من النظام", "الشيت مقابل الصورة", "قراءة الملصق", "ما انلقت:", "عطل:", "رجعت للطابور",
                   "الصورة الحالية بالشيت", "نموذج القراءة شاف منتج مختلف", "مقترحة · ماركة وحجم مطابقين"):
        assert arabic in joined, arabic


@NEEDS_NODE
def test_the_warning_is_shown_before_approval_in_plain_arabic(tmp_path):
    out = page(r"""
R.single.openItem(itemOf(21).key);
out.text = wsText();
out.caution = ws().querySelector('.rv-caution').textContent;
out.checks = ws().querySelectorAll('.rv-check').map(r => [r.getAttribute('data-check'), r.getAttribute('data-status')]);
out.requests = requests('/api/select_image').length;
""", tmp_path)
    assert out["caution"].startswith("تأكد قبل الاعتماد: ") and "نموذج القراءة غير متأكد من المطابقة" in out["caution"]
    assert ["size", "unsure"] in out["checks"]
    assert out["requests"] == 0


# ---------------------------------------------------------------------------
# Bulk mode
# ---------------------------------------------------------------------------

@NEEDS_NODE
def test_bulk_approves_only_proposed_images_without_a_warning(tmp_path):
    out = page(r"""
R.setMode('bulk');
await flush();
out.cards = document.querySelectorAll('.rv-card').map(c => [c.getAttribute('data-kind'), c.classList.contains('is-selected')]);
out.label = document.getElementById('rvBulkApprove').textContent;
out.count = document.querySelector('.rv-bulkbar__count').textContent;
// the reviewer also ticks the card with a warning and the reviewer-picked one (each click redraws the grid)
for (const it of S().items) {
    const box = document.querySelectorAll('.rv-card input[type="checkbox"]').find(b => b.getAttribute('data-select') === it.key);
    if (box && !box.checked && !box.disabled) box.click();
}
await flush();
out.label_after = document.getElementById('rvBulkApprove').textContent;
out.note = document.querySelector('.rv-bulkbar__note').textContent;
document.getElementById('rvBulkApprove').click();
await flush();
out.confirm = confirms.slice();
out.sent = requests('/api/select_image').map(c => c.body.row_number);
answer(requests('/api/select_image')[0], { status: 'success', image_link: 'https://res.cloudinary.com/x.png', isolated: true });
await flush();
out.sent_after_first = requests('/api/select_image').map(c => [c.body.row_number, c.body.image_url, c.body.search_decision, c.body.candidate_status]);
answer(requests('/api/select_image')[1], { status: 'success', image_link: 'https://res.cloudinary.com/y.png', isolated: true });
await flush();
out.max_in_flight = maxInFlight.select;
out.total = requests('/api/select_image').length;
""", tmp_path, fixture(products=READY, rows=queue_rows()))
    # in order of confidence: rows 19, 20 pre-selected without a warning (selected); 23 the reviewer's earlier pick;
    # 21 with a warning; 22 nothing proposed
    assert out["cards"] == [["eligible", True], ["eligible", True], ["proposed", False], ["warning", False], ["none", False]]
    assert out["label"] == "اعتماد صورتين بلا تحذير" and out["count"] == "2 محددة من 5"
    assert out["label_after"] == "اعتماد صورتين بلا تحذير"
    assert out["note"] == "2 من المحددة ما بتنعتمد من هون (فيها تحذير أو مش من اقتراح النظام)"
    assert len(out["confirm"]) == 1 and out["confirm"][0].startswith("اعتماد صورتين؟ بتنحط بالشيت وبتقدر تكمل شغلك.")
    assert "اللي فيها تحذير أو بلا اقتراح ما رح تنلمس" in out["confirm"][0]
    assert out["sent"] == ["19", "20"]                            # two requests at a time (different products)
    assert out["sent_after_first"] == [["19", "https://www.luluhypermarket.com/p19.jpg", "REVIEW_PRESELECTED", "preselected"],
                                       ["20", "https://www.luluhypermarket.com/p20.jpg", "REVIEW_PRESELECTED", "preselected"]]
    assert out["max_in_flight"] == 2 and out["total"] == 2


@NEEDS_NODE
def test_a_bulk_card_with_a_warning_is_approved_only_after_the_warning(tmp_path):
    out = page(r"""
R.setMode('bulk');
await flush();
out.card_warning = document.querySelectorAll('.rv-card').find(c => c.getAttribute('data-kind') === 'warning').querySelector('.rv-card__warn').textContent;
confirmAnswer = false;
R.bulk.approveOne(itemOf(21).key);
await flush();
out.refused = requests('/api/select_image').length;
confirmAnswer = true;
R.bulk.approveOne(itemOf(21).key);
await flush();
out.confirms = confirms.slice();
out.sent = requests('/api/select_image').map(c => c.body.row_number);
out.none_button = document.querySelectorAll('.rv-card').find(c => c.getAttribute('data-kind') === 'none').querySelector('.rv-card__approve').disabled;
""", tmp_path, fixture(products=READY, rows=queue_rows()))
    assert out["card_warning"] == "نموذج القراءة غير متأكد من المطابقة"
    assert out["refused"] == 0
    assert all("تأكد قبل الاعتماد: نموذج القراءة غير متأكد من المطابقة" in c for c in out["confirms"])
    assert out["sent"] == ["21"]
    assert out["none_button"] is True                             # nothing proposed: open it instead


@NEEDS_NODE
def test_bulk_reject_asks_for_a_reason_and_says_what_happens(tmp_path):
    out = page(r"""
R.setMode('bulk');
await flush();
document.getElementById('rvBulkReject').click();
await flush();
out.dialog = document.getElementById('rvDialog').textContent;
out.confirm_disabled = document.getElementById('rvRejectConfirm').disabled;
document.querySelectorAll('input[name="rv_bulk_reason"]')[3].click();
out.confirm_enabled = !document.getElementById('rvRejectConfirm').disabled;
document.getElementById('rvRejectConfirm').click();
await flush();
out.first = requests('/api/reject_image').map(c => [c.body.row_number, c.body.reason_code, c.body.research, c.body.sku_key, c.body.image_url]);
answer(requests('/api/reject_image')[0], { status: 'success', approval_kept: false });
await flush();
answer(requests('/api/reject_image')[1], { status: 'success', approval_kept: false });
await flush();
out.all = requests('/api/reject_image').length;
out.overlays = document.querySelectorAll('.rv-card__overlay').map(o => o.textContent);
out.jobs = jobsText();
""", tmp_path, fixture(products=READY, rows=queue_rows()))
    assert "ليش ترفضها؟" in out["dialog"]
    assert "رح نرفض الصورة المقترحة لـ 2 منتجات" in out["dialog"]
    # C2: back to the queue only when no image is left; otherwise still waiting for review with the remaining ones
    assert "بيضل بانتظار مراجعتك فيها" in out["dialog"] and "ما ضل إله صور بيرجع للطابور" in out["dialog"]
    assert "الصور المعتمدة قبل ما بتنلمس" in out["dialog"]
    assert out["confirm_disabled"] is True and out["confirm_enabled"] is True
    # two products at a time (the server locks each product while it writes)
    assert out["first"] == [["19", "WRONG_SIZE", False, "key-19", "https://www.luluhypermarket.com/p19.jpg"],
                            ["20", "WRONG_SIZE", False, "key-20", "https://www.luluhypermarket.com/p20.jpg"]]
    assert out["all"] == 2
    assert out["overlays"] == ["رجعت للطابور", "رجعت للطابور"]
    assert "جاهزة. بتقدر تكمل شغلك." in out["jobs"]


@NEEDS_NODE
def test_open_from_bulk_switches_to_the_single_product(tmp_path):
    out = page(r"""
R.setMode('bulk');
await flush();
document.querySelectorAll('.rv-card__open')[3].click();   // the card with the warning (confidence order)
await flush();
out.mode = S().mode;
out.name = productName();
out.history = historyLog.slice(-1)[0];
out.single_hidden = document.querySelector('.rv-single').hidden;
out.bulk_hidden = document.querySelector('.rv-bulk').hidden;
""", tmp_path)
    assert out["mode"] == "single" and out["name"] == W["product_name"]
    assert out["history"][0] == "push" and out["history"][1].startswith("/catalog?row=21")
    assert out["single_hidden"] is False and out["bulk_hidden"] is True


# ---------------------------------------------------------------------------
# Background approvals: progress, failure with its reason, retry, leaving the page
# ---------------------------------------------------------------------------

@NEEDS_NODE
def test_background_panel_progress_failure_and_retry(tmp_path):
    out = page(r"""
R.setMode('bulk');
await flush();
document.getElementById('rvBulkApprove').click();
await flush();
out.running = jobsText();
const leave = { preventDefault() { this.prevented = true; } };
windowListeners.beforeunload.forEach(fn => fn(leave));
out.leave_prompt = [!!leave.prevented, leave.returnValue];
answer(requests('/api/select_image')[0], { status: 'success', image_link: 'https://res.cloudinary.com/x.png', isolated: true });
await flush();
out.progress = jobsText();
answer(requests('/api/select_image')[1], { status: 'failed', error: 'Publishing the image failed (details on the Errors page).' }, 500);
await flush();
out.failed = jobsText();
out.retry_buttons = document.querySelectorAll('#rvJobs button').filter(b => b.textContent === 'أعد المحاولة').length;
const calm = { preventDefault() { this.prevented = true; } };
windowListeners.beforeunload.forEach(fn => fn(calm));
out.calm = !!calm.prevented;
document.querySelectorAll('#rvJobs button').find(b => b.textContent === 'أعد المحاولة').click();
await flush();
const retry = requests('/api/select_image')[2];
out.retry_body_same = JSON.stringify(retry.body) === JSON.stringify(requests('/api/select_image')[1].body);
answer(retry, { status: 'success', image_link: 'https://res.cloudinary.com/y.png', isolated: true });
await flush();
out.done = jobsText();
""", tmp_path, fixture(products=READY, rows=queue_rows()))
    assert "جاري اعتماد صورتين بالخلفية · 0 من 2 جاهزة. بتقدر تكمل شغلك." == out["running"]
    assert out["leave_prompt"][0] is True and "بالخلفية" in out["leave_prompt"][1]
    assert "1 من 2" in out["progress"]
    assert "ما انعتمدت: " in out["failed"] and P2["product_name"] in out["failed"]
    assert "فشل النشر (عزل الخلفية أو الرفع أو الكتابة بالشيت)." in out["failed"]
    assert "Publishing the image failed" not in out["failed"]    # the raw server text stays in the tooltip
    assert out["retry_buttons"] == 1 and out["calm"] is False
    assert out["retry_body_same"] is True
    assert out["done"].startswith("جاهزة. بتقدر تكمل شغلك.")


# ---------------------------------------------------------------------------
# Deep links other pages use
# ---------------------------------------------------------------------------

@NEEDS_NODE
def test_deep_links_open_the_row_the_filter_and_the_mode(tmp_path):
    out = page(r"""
out.row = [productName(), S().filter];
""", tmp_path, config={"row": 42, "filter": None})
    assert out["row"] == [IDLE["product_name"], "all"]           # not in the default chip: the filter opens up
    out = page(r"""
out.failed = [S().filter, productName(), document.querySelectorAll('.rv-item').length];
""", tmp_path, config={"filter": "failed"})
    assert out["failed"] == ["failed", FAIL["product_name"], 1]
    out = page(r"""
out.bulk = [S().mode, document.querySelector('.rv-bulk').hidden, document.querySelector('.rv-single').hidden,
            document.querySelectorAll('.rv-card').length];
""", tmp_path, config={"mode": "bulk"})
    assert out["bulk"] == ["bulk", False, True, 6]               # the five waiting products and the orphan row


# ---------------------------------------------------------------------------
# Laravel: the page renders on the Laqta layout, the redirects, and the queue state (database)
# ---------------------------------------------------------------------------

def _laravel_env(tmp_path: Path, **overrides) -> dict:
    env = dict(os.environ)
    env.update({
        "APP_ENV": "testing",
        "APP_KEY": "base64:" + "A" * 43 + "=",
        "APP_DEBUG": "true",
        "SESSION_DRIVER": "array",
        "CACHE_STORE": "array",
        "LOG_CHANNEL": "stderr",
        "VIEW_COMPILED_PATH": str(tmp_path),
        "DB_CONNECTION": "mariadb",
        "DB_HOST": os.getenv("DB_HOST", "127.0.0.1"),
        "DB_PORT": os.getenv("DB_PORT", "3306"),
        "DB_DATABASE": os.environ.get("DB_DATABASE", "automation_test"),
        "DB_USERNAME": os.getenv("DB_USERNAME", "root"),
        "DB_PASSWORD": os.getenv("DB_PASSWORD", ""),
    })
    env.update(overrides)
    return env


def _http(tmp_path, requests_list, **env) -> list:
    dash = str(DASH).replace("\\", "/")
    script = f"""<?php
require '{dash}/vendor/autoload.php';
$app = require '{dash}/bootstrap/app.php';
$kernel = $app->make(Illuminate\\Contracts\\Http\\Kernel::class);
$out = [];
foreach (json_decode({json.dumps(json.dumps(requests_list))}, true) as $uri) {{
    $request = Illuminate\\Http\\Request::create($uri, 'GET');
    $response = $kernel->handle($request);
    $out[] = ['uri' => $uri, 'status' => $response->getStatusCode(), 'location' => $response->headers->get('Location'),
              'body' => $response->getContent()];
    $kernel->terminate($request, $response);
}}
echo json_encode($out, JSON_UNESCAPED_UNICODE);
"""
    compiled = tmp_path / "views"
    compiled.mkdir(exist_ok=True)
    with tempfile.NamedTemporaryFile("w", suffix=".php", delete=False, encoding="utf-8") as fh:
        fh.write(script)
        path = fh.name
    try:
        result = subprocess.run([PHP, path], cwd=DASH, env=_laravel_env(compiled, **env), capture_output=True, text=True,
                                timeout=180)
    finally:
        os.unlink(path)
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-3000:]
    return json.loads(result.stdout)


def _config_of(body: str) -> dict:
    import html
    m = re.search(r'id="rvApp"[^>]*data-config="([^"]+)"', body)
    assert m, body[:2000]
    return json.loads(html.unescape(m.group(1)))


@NEEDS_LARAVEL
def test_catalog_renders_truthfully_without_a_database(tmp_path):
    res = _http(tmp_path, ["/catalog", "/catalog?mode=bulk&row=12&filter=failed", "/catalog?filter=bogus",
                           "/errors", "/rich-catalog", "/api/review/queue-state"], DB_PORT="1")
    catalog, bulk, bogus, errors, rich, queue = res
    assert catalog["status"] == 200, catalog["body"][:3000]
    body = catalog["body"]
    assert '<html lang="ar" dir="rtl">' in body and "<title>المراجعة · لقطة</title>" in body
    assert 'class="lq-body page-review' in body and 'class="lq-main lq-main--flush"' in body
    current = re.findall(r"<a href=\"([^\"]+)\"[^>]*aria-current=\"page\"", body)
    assert len(current) == 1 and current[0].endswith("/catalog")
    scripts = [m.group(1) for m in (re.search(r"/js/review/([a-z]+)\.js\?v=", src) for src in script_sources(body)) if m]
    assert scripts == ["core", "ui", "jobs", "single", "bulk", "app"]
    assert re.search(r'<link rel="stylesheet" href="[^"]*/css/pages/review\.css\?v=', body)
    cfg = _config_of(body)
    assert cfg["db"] == "offline" and cfg["readyForReview"] is None and cfg["mode"] == "single"
    assert cfg["canvas"] == 800 and cfg["urls"]["queueState"].endswith("/api/review/queue-state")
    assert "قاعدة البيانات مش متاحة" in body                      # not an idle zero
    assert re.search(r'<span class="lq-nav__badge" data-lq-review-count\s+hidden', body)
    cfg = _config_of(bulk["body"])
    assert (cfg["mode"], cfg["row"], cfg["filter"]) == ("bulk", 12, "failed")
    assert _config_of(bogus["body"])["filter"] is None
    assert errors["status"] == 302 and errors["location"].endswith("/catalog?filter=failed")
    assert rich["status"] == 302 and rich["location"].endswith("/catalog")
    assert queue["status"] == 503 and json.loads(queue["body"])["status"] == "unavailable"


@NEEDS_LARAVEL
def test_catalog_and_queue_state_read_the_same_rows_as_the_badge(tmp_path, mariadb_or_skip):
    import pymysql

    conn = pymysql.connect(host=os.getenv("DB_HOST", "127.0.0.1"), port=int(os.getenv("DB_PORT", "3306")),
                           user=os.getenv("DB_USERNAME", "root"), password=os.getenv("DB_PASSWORD", ""),
                           database=os.environ["DB_DATABASE"], autocommit=True)
    rows = [(971001, "ready_for_review"), (971002, "ready_for_review"), (971003, "ready_for_review"),
            (971004, "failed"), (971005, "completed"), (971006, "pending")]
    secret = "sk-review-page-SECRET-4417"
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM automation_queue")
            for row, status in rows:
                cur.execute("INSERT INTO automation_queue (`row_number`, product_name, brand, status, sku_key, failure_code) "
                            "VALUES (%s, %s, %s, %s, %s, %s)",
                            (row, f"Product {row}", "Brand", status, f"key-{row}", "NO_RESULTS" if status == "failed" else None))
            cur.execute("SELECT `key`, value FROM system_settings WHERE `key` IN ('output_canvas_size', 'serper_api_key')")
            saved = dict(cur.fetchall())
            cur.execute("REPLACE INTO system_settings (`key`, value) VALUES ('output_canvas_size', '1000'), ('serper_api_key', %s)",
                        (secret,))
        res = _http(tmp_path, ["/catalog", "/api/review/queue-state", "/api/batch-status"])
    finally:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM automation_queue WHERE `row_number` BETWEEN 971001 AND 971006")
            for key in ("output_canvas_size", "serper_api_key"):
                if key in saved:
                    cur.execute("REPLACE INTO system_settings (`key`, value) VALUES (%s, %s)", (key, saved[key]))
                else:
                    cur.execute("DELETE FROM system_settings WHERE `key` = %s", (key,))
        conn.close()
    catalog, queue, batch = res
    assert catalog["status"] == 200, catalog["body"][:3000]
    cfg = _config_of(catalog["body"])
    assert cfg["db"] == "online" and cfg["readyForReview"] == 3 and cfg["canvas"] == 1000
    assert re.search(r'data-lq-review-count\s*>3</span>', catalog["body"])       # the badge starts at the same number
    assert "3 بانتظار المراجعة" in catalog["body"]
    assert secret not in catalog["body"] and secret not in queue["body"]
    state = json.loads(queue["body"])
    assert state["status"] == "success" and state["ready_for_review"] == 3
    assert sorted(r["row_number"] for r in state["rows"]) == [971001, 971002, 971003, 971004, 971006]
    assert sum(r["status"] == "ready_for_review" for r in state["rows"]) == state["ready_for_review"]
    assert {r["row_number"]: r["failure_code"] for r in state["rows"]}[971004] == "NO_RESULTS"
    assert json.loads(batch["body"])["ready_for_review"] == state["ready_for_review"]   # the sidebar badge's number


# ---------------------------------------------------------------------------
# Independent review fixes (each test failed before its fix)
# ---------------------------------------------------------------------------

MILK = product(50, "Almarai Full Fat Milk 1L", brand="Almarai", size="1L", needs_review=True, preselected=True,
               curation_candidates=[
                   cand("https://www.carrefouruae.com/milk.jpg", "preselected", 1, reasons=["vlm:MATCH"],
                        evidence={"variants": ["fat"], "variant_status": "match",
                                  "variants_found": {"title": {"fat": "full", "form": "fresh"}}},
                        vlm={"decision": "MATCH", "variant_text": "Full Fat Milk", "variant_match": "yes",
                             "brand_match": "yes", "size_match": "yes", "view": "front_packshot"}),
                   cand("https://images.openfoodfacts.org/milk-life.jpg", "eligible", 0, reasons=["vlm:UNSURE"],
                        vlm={"decision": "UNSURE", "view": "lifestyle", "size_match": "unsure"})])


@NEEDS_NODE
def test_the_variant_row_shows_the_sheet_variant_in_arabic_not_the_axis_code(tmp_path):
    """evidence.variants holds axis codes (fat, form ...): the «بالشيت» cell said «fat»."""
    out = page(VISIBLE_TEXT + r"""
const row = ws().querySelector('[data-check="variant"]');
out.sheet = visibleText(row.querySelector('.lq-check-row__sheet')).replace('في الشيت: ', '').trim();
out.status = row.getAttribute('data-status');
""", tmp_path, fixture(products=[MILK], rows=queue_rows([MILK])))
    assert out["sheet"] == "نسبة الدسم: full"
    assert not re.search(r"\bfat\b", out["sheet"])
    assert out["status"] == "match"


@NEEDS_NODE
def test_the_caution_box_names_geminis_doubt_about_the_reviewers_own_pick(tmp_path):
    """A picked alternative that Gemini was unsure of, shot as a lifestyle photo, had no «تأكد قبل الاعتماد»."""
    out = page(r"""
const caution = () => { const c = ws().querySelector('.rv-caution'); return c ? c.textContent : ''; };
out.system = caution();
press('2');
out.pick = pickUrl();
out.own = caution();
""", tmp_path, fixture(products=[MILK], rows=queue_rows([MILK])))
    assert out["system"] == ""                                   # the system pick: Gemini matched a front packshot
    assert out["pick"] == "https://images.openfoodfacts.org/milk-life.jpg"
    assert "تأكد قبل الاعتماد" in out["own"]
    assert "نموذج القراءة غير متأكد من المطابقة" in out["own"] and "صورة استخدام" in out["own"]
    assert not RAW_CODES.search(out["own"])


@NEEDS_NODE
def test_the_position_follows_the_list_after_a_filter_change_or_a_search(tmp_path):
    """«N من M» kept the count of the chip the product was opened under."""
    out = page(r"""
const pos = () => { const p = ws().querySelector('.rv-product__pos'); return p && !p.hidden ? p.textContent : ''; };
out.start = [S().filter, productName(), pos()];
document.querySelector('[data-filter="all"]').click();
await flush();
out.all = [productName(), pos(), R.visibleItems().length];
const search = document.getElementById('rvSearch');
search.value = 'ALALALI FANCY';
dispatch(search, { type: 'input', bubbles: true });
await sleep(200);
out.searched = [productName(), pos(), R.visibleItems().map(it => it.product.row_number)];
""", tmp_path, config={"filter": None})
    assert out["start"] == ["proposed", P1["product_name"], "1 من 4"]
    assert out["all"] == [P1["product_name"], "1 من 11", 11]
    assert out["searched"][2] == [20, 21]                         # the open product is not in the searched list
    assert out["searched"][:2] == [P1["product_name"], ""]


@NEEDS_NODE
def test_bulk_approval_takes_only_cards_the_reviewer_can_see(tmp_path):
    """With more waiting products than one page of cards, the hidden ones were selected and published too."""
    many = [product(100 + i, f"TUNA CHUNKS {i} 85GM", curation_candidates=[
        cand(f"https://www.luluhypermarket.com/t{i}.jpg", "preselected", 1, reasons=["vlm:MATCH"], evidence={"size": "match"})],
        needs_review=True, preselected=True) for i in range(52)]
    out = page(r"""
R.setMode('bulk');
await flush();
out.cards = document.querySelectorAll('.rv-card').length;
out.count = document.querySelector('.rv-bulkbar__count').textContent;
out.label = document.getElementById('rvBulkApprove').textContent;
document.getElementById('rvBulkApprove').click();
await flush();
const st = S().jobs.state();
out.jobs = st.total;
out.hidden_jobs = st.jobs.map(j => parseInt(j.row, 10)).filter(row => row >= 148);
out.confirm = confirms[confirms.length - 1];
""", tmp_path, fixture(products=many, rows=queue_rows(many)))
    assert out["cards"] == 48
    assert out["count"] == "48 محددة من 52"
    assert out["label"] == "اعتماد 48 صورة بلا تحذير"
    assert out["jobs"] == 48 and out["hidden_jobs"] == []
    assert out["confirm"].startswith("اعتماد 48 صورة؟")


@NEEDS_NODE
def test_the_progress_counts_only_the_running_batch_and_a_retry_shows_the_product_as_approving(tmp_path):
    """A failed approval left in the panel inflated the next batch («جاري اعتماد 2 صور · 1 من 2» for one image),
    and a retried approval showed its product as still waiting."""
    out = page(r"""
R.setMode('bulk');
await flush();
document.getElementById('rvBulkApprove').click();
await flush();
answer(requests('/api/select_image')[0], { status: 'success', image_link: 'https://res.cloudinary.com/x.png', isolated: true });
await flush();
answer(requests('/api/select_image')[1], { status: 'failed', error: 'Publishing the image failed (details on the Errors page).' }, 500);
await flush();
R.setMode('single', { key: itemOf(21).key });
await flush();
R.single.approveCurrent();
await flush();
out.next_batch = jobsText();
answer(requests('/api/select_image')[2], { status: 'success', image_link: 'https://res.cloudinary.com/w.png', isolated: true });
await flush();
document.querySelectorAll('#rvJobs button').find(b => b.textContent === 'أعد المحاولة').click();
await flush();
out.retry_bucket = itemOf(20).bucket;
out.retry_text = jobsText();
""", tmp_path, fixture(products=READY, rows=queue_rows()))
    assert out["next_batch"].startswith("جاري اعتماد صورة وحدة بالخلفية · 0 من 1 جاهزة.")
    assert "ما انعتمدت: " in out["next_batch"] and P2["product_name"] in out["next_batch"]   # still listed with its retry
    assert out["retry_bucket"] == "approving"
    assert out["retry_text"].startswith("جاري اعتماد صورة وحدة بالخلفية · 0 من 1")


@NEEDS_NODE
def test_holding_enter_approves_only_the_product_it_was_pressed_on(tmp_path):
    """After an approval the next product opens; the key's auto-repeat approved that one unseen."""
    out = page(r"""
openRow(19);
press('Enter');
await flush();
out.after_first = [productName(), S().jobs.state().total];
press('Enter', { repeat: true });
press('Enter', { repeat: true });
await flush();
out.after_repeat = S().jobs.state().jobs.map(j => j.row);
press('Enter');
await flush();
out.after_press = S().jobs.state().jobs.map(j => j.row);
""", tmp_path, fixture(products=READY, rows=queue_rows()))
    assert out["after_first"] == [P2["product_name"], 1]
    assert out["after_repeat"] == ["19"]
    assert out["after_press"] == ["19", "20"]                     # a new, deliberate press still approves


@NEEDS_NODE
def test_the_reject_reasons_say_what_happens_and_what_is_kept(tmp_path):
    out = page(VISIBLE_TEXT + r"""
openRow(19);
press('x');
out.text = visibleText(document.querySelector('.rv-reasons'));
""", tmp_path, fixture(products=READY, rows=queue_rows()))
    assert "ليش ترفضها؟" in out["text"]
    assert "بيرجع للطابور" in out["text"] and "الصورة المعتمدة" in out["text"] and "ما بتنلمس" in out["text"]


@NEEDS_NODE
def test_the_csv_export_is_named_for_what_it_holds(tmp_path):
    """The export lists every saved resolution with its status (human, automatic, replaced), not only approvals."""
    out = page(r"""
R.setMode('bulk');
await flush();
const a = document.querySelector('.rv-bulk__export');
out.export = [a.textContent, a.getAttribute('title'), a.getAttribute('href')];
""", tmp_path, fixture(products=READY, rows=queue_rows()))
    assert out["export"][0] == "تصدير سجل الصور (CSV)"
    assert "مستبدلة" in out["export"][1]
    assert out["export"][2].endswith("/rich-catalog/export")


@NEEDS_NODE
def test_unreadable_data_never_shows_as_zero_in_either_mode(tmp_path):
    """Products and queue state both unavailable (database down): the chips said «0», bulk said «0 بانتظار المراجعة»,
    and the queue note claimed the numbers came from the saved images although none were read."""
    fx = fixture(products=[], status={"/api/products-json": 500, "/api/review/queue-state": 503})
    fx["queue"] = {"status": "unavailable", "error": "قاعدة البيانات غير متاحة: لا يمكن معرفة ما ينتظر المراجعة الآن."}
    out = page(r"""
out.header = document.getElementById('rvWaiting').textContent;
out.chips = document.querySelectorAll('.rv-filters .lq-filter__count').map(n => n.textContent);
out.note = document.querySelector('.rv-queue__note').textContent;
R.setMode('bulk');
await flush();
out.bulk_count = document.querySelector('.rv-bulk__count').textContent;
out.bulk_chips = document.querySelectorAll('.rv-bulk__filters .lq-filter__count').map(n => n.textContent);
out.brands = document.querySelector('.rv-brand__select').textContent;
out.selected = document.querySelector('.rv-bulkbar__count').textContent;
""", tmp_path, fx)
    assert out["header"] == "—"
    assert set(out["chips"]) == {"—"}
    assert "قاعدة البيانات غير متاحة" in out["note"] and "محسوبة من الصور المحفوظة" not in out["note"]
    assert out["bulk_count"] == "—" and set(out["bulk_chips"]) == {"—"}
    assert "(0)" not in out["brands"] and out["selected"] == ""


@NEEDS_LARAVEL
def test_catalog_ignores_array_query_parameters(tmp_path):
    """/catalog?row[]=1 raised «Array to string conversion» (HTTP 500)."""
    res = _http(tmp_path, ["/catalog?row[]=1&mode[]=bulk&filter[]=failed"], DB_PORT="1")
    assert res[0]["status"] == 200, res[0]["body"][:2000]
    cfg = _config_of(res[0]["body"])
    assert (cfg["row"], cfg["mode"], cfg["filter"]) == (None, "single", None)
