"""Fewer steps, no new pages: «روح لـ…», «روح على», «رجّع وشغّل», deep links to the exact card or key.

* «روح لـ…» (public/js/jump.js, the layout's #lqGotoIndex from App\\Services\\GotoIndex): Ctrl+K or «/» from any page,
  every page, card, setting and review list in one search; digits open that row, any text searches the review list
  (/catalog?q=…); a reviewer's list has none of the owner's pages;
* «روح على» (components/lq/jump-links): the Run and Health cards one click away; Health opens «تفاصيل متقدمة» for any
  #id inside it (on load and on a hash change);
* «رجّع وشغّل» in the review list: the rows go back to the queue and the Run page opens on those rows only
  (?scope=rows&rows=…), «قبل ما تبدأ» prices them and «ابدأ» is the one click left;
* the Health items for a key land on that key's row with its field open (?tab=keys#lq-key-serper);
* the review list takes ?q= and searches SKU too; bulk mode has its own search; Enter approves a bulk card;
* the home's next step says the failed rows and never sends a reviewer to the owner's pages.
"""

import re

import pytest

from laqta_review_harness import NODE, run
from test_laqta_run import COMMON_JS, HOME_JS, RUN_JS, _js
from test_laqta_site_ux import _node, role_pages  # noqa: F401  (the module-scoped role rendering fixture)
from test_laqta_ui import DASH, NEEDS_LARAVEL, read

NEEDS_NODE = pytest.mark.skipif(NODE is None, reason="node is not installed")
JUMP_JS = DASH / "public" / "js" / "jump.js"
GOTO = DASH / "app" / "Services" / "GotoIndex.php"
HEALTH_VIEW = DASH / "resources" / "views" / "dashboard" / "diagnostics.blade.php"
RUN_VIEW = DASH / "resources" / "views" / "dashboard" / "batch_automation.blade.php"
ATTENTION = DASH / "app" / "Http" / "Controllers" / "HealthAttentionController.php"


# ---------------------------------------------------------------------------
# «روح لـ…»
# ---------------------------------------------------------------------------

ENTRIES = r"""
const entries = [
    { title: 'الرئيسية', group: 'صفحات', href: '/', words: 'home' },
    { title: 'باركودات من صفحات المتاجر', group: 'التشغيل', href: '/batch-automation#run-barcodes', words: 'barcode gtin' },
    { title: 'مفتاح Serper', group: 'الإعدادات', href: '/settings?tab=keys#lq-key-serper', words: 'البحث عن الصور key api' },
    { title: 'فحص النشر', group: 'الصحة والتكلفة', href: '/system-diagnostics#publish-check', words: 'publish' },
];
"""


@NEEDS_NODE
def test_goto_search_finds_places_rows_and_products():
    out = _node(_js(JUMP_JS) + ENTRIES + r"""
const G = window.LaqtaGoto;
console.log(JSON.stringify({
    empty: G.search(entries, '').map(e => e.href),
    english: G.search(entries, 'serper').map(e => e.href),
    arabic: G.search(entries, 'باركود').map(e => e.href),
    hamza: G.search(entries, 'فحص').map(e => e.href),
    words: G.search(entries, 'gtin').map(e => e.title),
    row: G.search(entries, '١٢').map(e => e.href),
    text: G.search(entries, 'Almarai Laban').map(e => e.href),
}));
""")
    assert out["empty"] == ["/", "/batch-automation#run-barcodes", "/settings?tab=keys#lq-key-serper",
                            "/system-diagnostics#publish-check"]
    assert out["english"][0] == "/settings?tab=keys#lq-key-serper"
    assert out["arabic"][0] == "/batch-automation#run-barcodes"
    assert out["hamza"][0] == "/system-diagnostics#publish-check"
    assert out["words"][0] == "باركودات من صفحات المتاجر"
    # Arabic digits are a row number; the last entry always searches the review list
    assert out["row"][0] == "/catalog?row=12" and out["row"][-1] == "/catalog?q=%D9%A1%D9%A2"
    assert out["text"] == ["/catalog?q=Almarai%20Laban"]


@NEEDS_NODE
def test_goto_opens_with_ctrl_k_and_slash_but_not_while_typing():
    out = _node(r"""
const listeners = {};
const body = { children: [], appendChild(n) { this.children.push(n); n.parentNode = this; }, removeChild(n) {
    this.children = this.children.filter(c => c !== n); n.parentNode = null; } };
function node(tag) {
    return { tagName: tag.toUpperCase(), children: [], attrs: {}, listeners: {}, style: {}, firstChild: null, value: '',
        setAttribute(k, v) { this.attrs[k] = String(v); }, getAttribute(k) { return this.attrs[k] ?? null; },
        removeAttribute(k) { delete this.attrs[k]; },
        appendChild(c) { this.children.push(c); this.firstChild = this.children[0]; c.parentNode = this; return c; },
        removeChild(c) { this.children = this.children.filter(x => x !== c); this.firstChild = this.children[0] || null; },
        addEventListener(t, fn) { (this.listeners[t] = this.listeners[t] || []).push(fn); },
        querySelectorAll() { return []; }, focus() { document.activeElement = this; } };
}
globalThis.document = {
    body, activeElement: null,
    createElement: node, createTextNode: t => ({ textContent: t }),
    getElementById: id => id === 'lqGotoIndex' ? { textContent: JSON.stringify([{ title: 'الرئيسية', group: 'صفحات', href: '/', words: '' }]) } : null,
    addEventListener(t, fn) { (listeners[t] = listeners[t] || []).push(fn); },
    removeEventListener() {}, querySelector: () => null, contains: () => true,
};
globalThis.window = globalThis;
""" + read(JUMP_JS) + r"""
function key(props) {
    const e = Object.assign({ key: '', code: '', ctrlKey: false, metaKey: false, altKey: false, shiftKey: false, repeat: false,
        defaultPrevented: false, target: document.body, preventDefault() { this.defaultPrevented = true; }, stopPropagation() {} }, props);
    (listeners.keydown || []).forEach(fn => fn(e));
    return e;
}
const typing = key({ key: '/', code: 'Slash', target: { tagName: 'INPUT' } });
const afterTyping = body.children.length;
key({ key: 'ك', code: 'KeyK', ctrlKey: true });                          // Arabic layout: e.code is still KeyK
const afterCtrlK = body.children.length;
window.LaqtaGoto.close(false);
key({ key: '/', code: 'Slash' });
console.log(JSON.stringify({ afterTyping, typingPrevented: typing.defaultPrevented, afterCtrlK, afterSlash: body.children.length }));
""")
    assert out == {"afterTyping": 0, "typingPrevented": False, "afterCtrlK": 1, "afterSlash": 1}


def test_goto_index_lists_every_hidden_tool_and_marks_the_owners_ones():
    goto = read(GOTO)
    for href in ("/cutout-check", "/setup", "/batch-automation#run-barcodes", "/batch-automation#run-export",
                 "/system-diagnostics#search-ops", "/system-diagnostics#reprocess", "/system-diagnostics#eval-export",
                 "/catalog?filter=failed", "/catalog?mode=bulk"):
        assert f"'{href}'" in goto, href
    for owner in ("'/cutout-check', 'cutout recut background', true", "'/setup', 'setup wizard onboarding', true",
                  "'/batch-automation', 'run batch', true"):
        assert owner in goto
    assert "'/settings?tab=keys#lq-key-' . $id" in goto and "SettingsController::TABS" in goto


@NEEDS_LARAVEL
def test_the_layout_carries_the_places_for_each_role(role_pages):
    import json as _json
    found = {}
    for role in ("admin", "reviewer"):
        page = role_pages[role]
        island = re.search(r'<script type="application/json" id="lqGotoIndex">(.*?)</script>', page, re.S)
        assert island, role
        found[role] = {e["href"] for e in _json.loads(island.group(1))}
        assert re.search(r'<script src="[^"]*/js/jump\.js\?v=\d+"></script>', page)
        assert 'data-lq-goto-open' in page
    assert "/cutout-check" in found["admin"] and "/settings?tab=keys#lq-key-serper" in found["admin"]
    assert not any(h.startswith(("/batch-automation", "/settings", "/setup", "/cutout-check")) for h in found["reviewer"])
    assert "/system-diagnostics#eval-export" not in found["reviewer"] and "/catalog?filter=failed" in found["reviewer"]


# ---------------------------------------------------------------------------
# «روح على» and the deep links
# ---------------------------------------------------------------------------

def test_long_pages_have_jump_links_to_cards_that_exist():
    for view in (HEALTH_VIEW, RUN_VIEW):
        text = read(view)
        links = re.search(r"<x-lq\.jump-links[^>]*:links=\"\[(.*?)\]\"", text, re.S)
        assert links, view.name
        for anchor in re.findall(r"\['#([a-z-]+)'", links.group(1)):
            assert f'id="{anchor}"' in text, (view.name, anchor)


def test_health_items_for_a_key_land_on_that_key_and_run_items_on_their_card():
    attention = read(ATTENTION)
    assert "'href' => '/settings?tab=keys#lq-key-serper'" in attention
    assert "'href' => '/settings?tab=keys#lq-key-gemini'" in attention
    assert "'href' => '/batch-automation'])" not in attention
    settings = read(DASH / "public" / "js" / "settings.js")
    assert "function openKeyFromHash()" in settings and "openKeyForm(m[1], true)" in settings
    health = read(DASH / "public" / "js" / "health.js")
    assert "function revealHash(hash, scroll)" in health and "'hashchange'" in health


# ---------------------------------------------------------------------------
# «رجّع وشغّل» and the Run page opened on those rows
# ---------------------------------------------------------------------------

FAILED = [{"row_number": r, "product_name": f"Broken {r}", "brand": "Almarai", "barcode": "", "sku_key": f"key-{r}",
           "size": "1L", "product_name_ar": "", "brand_ar": "", "category": "", "existing_image_link": "",
           "needs_review": False, "has_error": True, "error_message": "SEARCH_ERROR: search raised an exception"}
          for r in (5, 6, 7, 9)]


@NEEDS_NODE
def test_retry_and_run_requeues_then_opens_the_run_on_those_rows(tmp_path):
    fixture = {"products": FAILED,
               "queue": {"status": "success", "ready_for_review": 0, "rows": [
                   {"row_number": p["row_number"], "sku_key": p["sku_key"], "status": "failed", "failure_code": "SEARCH_ERROR",
                    "product_name": p["product_name"], "brand": p["brand"]} for p in FAILED]},
               "retry": {"status": "success", "requeued": 4, "not_found": 0}}
    out = run(r"""
await boot({ filter: 'failed' });
const nav = [];
R.navigate = url => nav.push(url);
document.getElementById('rvRetryRun').click();
await flush();
out.nav = nav;
out.retried = requests('/api/failures/retry').map(c => c.body.barcodes.length);
out.confirms = confirms.length;
""", tmp_path, fixture)
    assert out["retried"] == [4] and out["confirms"] == 0
    assert out["nav"] == ["/batch-automation?scope=rows&rows=5-7%2C9"]


@NEEDS_NODE
def test_the_run_page_reads_its_scope_from_the_link():
    out = _node(_js(COMMON_JS, RUN_JS) + r"""
const P = window.LaqtaRunPage;
console.log(JSON.stringify({
    rows: P.linkedForm('?scope=rows&rows=5-7%2C9'),
    arabic: P.linkedForm('?scope=rows&rows=٥-٧،٩'),
    brand: P.linkedForm('?scope=brand&brand=Almarai'),
    junk: P.linkedForm('?scope=rows&rows=1;drop'),
    none: P.linkedForm(''),
    body: P.runBody(P.linkedForm('?scope=rows&rows=5-7,9')),
}));
""")
    assert out["rows"] == {"scope": "rows", "rows": "5-7,9", "brand": ""}
    assert out["arabic"]["rows"] == "5-7,9"
    assert out["brand"] == {"scope": "brand", "rows": "", "brand": "Almarai"}
    assert out["junk"] is None and out["none"] is None
    assert out["body"]["row_filter"] == "5-7,9" and out["body"]["brand_filter"] == ""


# ---------------------------------------------------------------------------
# The review list: ?q=, SKU, bulk search, Enter in bulk
# ---------------------------------------------------------------------------

def _product(row, name, sku):
    url = f"https://www.carrefouruae.com/p{row}.jpg"
    return {"row_number": row, "product_name": name, "brand": "Almarai", "barcode": "", "sku_key": sku, "size": "1L",
            "product_name_ar": "", "brand_ar": "", "category": "", "existing_image_link": "", "needs_review": True,
            "has_error": False, "curation_candidates": [
                {"image_url": url, "status": "preselected", "is_selected": 1, "title": name, "reasons": []}]}


THREE = [_product(10, "Laban 1L", "06281007035309"), _product(11, "Milk 2L", "abcd1234"), _product(12, "Juice 1L", "x9")]


def _fx(products):
    rows = [{"row_number": p["row_number"], "sku_key": p["sku_key"], "status": "ready_for_review", "failure_code": None,
             "product_name": p["product_name"], "brand": p["brand"], "updated_at": "2026-10-05 10:00:00"} for p in products]
    return {"products": products, "queue": {"status": "success", "ready_for_review": len(rows), "rows": rows}}


@NEEDS_NODE
def test_the_review_list_opens_on_a_search_from_the_link_and_finds_a_sku(tmp_path):
    out = run(r"""
await boot({ q: 'abcd', modeExplicit: true });
out.query = S().query;
out.box = document.getElementById('rvSearch').value;
out.shown = R.filterItems(S().items, S().filter, S().query).map(it => it.product.row_number);
out.short = R.filterItems(S().items, 'all', 'x9').map(it => it.product.row_number);
""", tmp_path, _fx(THREE))
    assert out["query"] == "abcd" and out["box"] == "abcd"
    assert out["shown"] == [11]
    assert out["short"] == []                         # a SKU matches from 4 characters on: «x9» is not a search for one


@NEEDS_NODE
def test_bulk_mode_searches_and_enter_approves_the_focused_card(tmp_path):
    out = run(r"""
await boot({ mode: 'bulk' });
const box = document.getElementById('rvBulkSearch');
box.value = 'milk';
dispatch(box, { type: 'input', bubbles: true });
await new Promise(r => setTimeout(r, 200));
await flush();
out.cards = document.querySelectorAll('.rv-card').map(c => c.getAttribute('data-key'));
out.keysButton = !!document.querySelector('.rv-keys-btn');
""", tmp_path, _fx(THREE))
    assert len(out["cards"]) == 1 and out["cards"][0].startswith("11|")
    assert out["keysButton"] is True
    bulk = read(DASH / "public" / "js" / "review" / "bulk.js")
    assert "if (key === 'a' || (key === 'Enter' && !(e && e.shiftKey)))" in bulk


# ---------------------------------------------------------------------------
# The home's next step
# ---------------------------------------------------------------------------

@NEEDS_NODE
def test_the_next_step_says_failed_rows_and_spares_the_reviewer_the_owners_pages():
    out = _node(_js(COMMON_JS, HOME_JS) + r"""
const H = window.LaqtaHomePage;
console.log(JSON.stringify({
    failed: H.nextStep(0, null, 3, 'idle', { failed: 4 }),
    reviewerDone: H.nextStep(0, null, 0, 'idle', { reviewer: true, failed: 4 }),
    reviewerNotFound: H.nextStep(0, null, 3, 'idle', { reviewer: true }),
    reviewerReview: H.nextStep(5, null, 3, 'idle', { reviewer: true }),
}));
""")
    assert out["failed"]["key"] == "failed" and out["failed"]["href"] == "/catalog?filter=failed"
    assert out["reviewerDone"]["key"] == "done" and not out["reviewerDone"]["href"].startswith("/batch-automation")
    assert out["reviewerNotFound"]["href"] == "/catalog?filter=not_found"
    assert out["reviewerReview"]["key"] == "review"
