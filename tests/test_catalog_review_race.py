"""catalog.blade.php: a reviewer can never publish the wrong product's image, or the same image twice.

The page's whole inline script runs under node against a small DOM shim (the static elements are the
ids of the page's own markup) with a scripted fetch: each test drives the page the way a reviewer does
(open a product, press keys, click buttons) and holds or answers each request itself.

* a search answer for a product that is no longer open is dropped: it never touches the form or the results;
* the results are bound to the product they were found for: approve / reject / upload send that identity;
* while an approval runs every approve control is disabled; after it the card says "تم الاعتماد ✓";
* keys 1-9 only select a candidate, A / Enter approves the selected one;
* a product that already has a final image shows it, with "إعادة البحث", and starts no (paid) search.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from blade_scripts import inline_scripts

ROOT = Path(__file__).resolve().parents[1]
VIEW = ROOT / "dashboard" / "resources" / "views" / "dashboard" / "catalog.blade.php"
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")

APPROVED = "تم الاعتماد ✓"


def _page():
    text = VIEW.read_text(encoding="utf-8")
    markup = text[text.index("@section('content')"):text.index("@section('scripts')")]
    script = inline_scripts(text)[-1]
    script = re.sub(r"\{\{.*?\}\}", "''", script)
    static = [{"tag": tag, "id": ident} for tag, ident in re.findall(r'<([a-z][a-z0-9]*)\b[^>]*\bid="([^"]+)"', markup)]
    radios = re.findall(r'<input type="radio" name="([^"]+)" value="([^"]+)"', markup)
    return script, static, radios


SHIM = r"""
'use strict';
class ShimClassList {
    constructor(node) { this.node = node; }
    _all() { return String(this.node.className || '').split(/\s+/).filter(Boolean); }
    add(...names) { const all = this._all(); names.forEach(n => { if (!all.includes(n)) all.push(n); }); this.node.className = all.join(' '); }
    remove(...names) { this.node.className = this._all().filter(n => !names.includes(n)).join(' '); }
    contains(name) { return this._all().includes(name); }
    toggle(name, force) {
        const want = force === undefined ? !this.contains(name) : !!force;
        if (want) this.add(name); else this.remove(name);
        return want;
    }
}

class ShimNode {
    constructor(tag) {
        this.tagName = String(tag || 'div').toUpperCase();
        this.children = [];
        this.parentNode = null;
        this.className = '';
        this.dataset = {};
        this.style = {};
        this.attrs = {};
        this.listeners = {};
        this.disabled = false;
        this.checked = false;
        this.value = '';
        this.title = '';
        this.id = '';
        this.onclick = null;
        this._text = '';
        this._html = '';
        this.classList = new ShimClassList(this);
    }
    get value() { return this._value; }
    set value(v) { this._value = v === undefined || v === null ? '' : String(v); }    // always a string, as in a browser
    get childNodes() { return this.children; }
    get firstChild() { return this.children[0] || null; }
    get isConnected() { let n = this; while (n.parentNode) n = n.parentNode; return n === document.body; }
    get nextElementSibling() {
        if (!this.parentNode) return null;
        const sibs = this.parentNode.children.filter(c => c.tagName !== '#TEXT');
        return sibs[sibs.indexOf(this) + 1] || null;
    }
    get textContent() { return this._text + this._html + this.children.map(c => c.textContent).join(''); }
    set textContent(v) { this._detachAll(); this._text = String(v); this._html = ''; }
    get innerText() { return this.textContent; }
    set innerText(v) { this.textContent = v; }
    get innerHTML() { return this.textContent; }
    set innerHTML(v) { this._detachAll(); this._text = ''; this._html = String(v); }
    _detachAll() { this.children.forEach(c => { c.parentNode = null; }); this.children = []; }
    appendChild(n) {
        if (n.parentNode) n.parentNode.removeChild(n);
        n.parentNode = this;
        this.children.push(n);
        return n;
    }
    insertBefore(n, ref) {
        if (!ref) return this.appendChild(n);
        if (n.parentNode) n.parentNode.removeChild(n);
        n.parentNode = this;
        this.children.splice(this.children.indexOf(ref), 0, n);
        return n;
    }
    removeChild(n) {
        const i = this.children.indexOf(n);
        if (i >= 0) this.children.splice(i, 1);
        n.parentNode = null;
        return n;
    }
    remove() { if (this.parentNode) this.parentNode.removeChild(this); }
    replaceWith(n) {
        const parent = this.parentNode;
        if (!parent) return;
        if (n.parentNode) n.parentNode.removeChild(n);
        parent.children[parent.children.indexOf(this)] = n;
        n.parentNode = parent;
        this.parentNode = null;
    }
    setAttribute(k, v) {
        v = String(v);
        this.attrs[k] = v;
        if (k === 'id') this.id = v;
        else if (k === 'class') this.className = v;
        else if (k === 'disabled') this.disabled = true;
        else if (k === 'value') this.value = v;
        else if (k === 'title') this.title = v;
        else if (k === 'src' || k === 'href' || k === 'type' || k === 'name') this[k] = v;
    }
    getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
    addEventListener(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); }
    dispatchEvent(ev) {
        ev.target = ev.target || this;
        ev.currentTarget = this;
        ev.preventDefault = ev.preventDefault || (() => {});
        (this.listeners[ev.type] || []).forEach(fn => fn(ev));
        if (ev.type === 'click' && typeof this.onclick === 'function') this.onclick(ev);
    }
    click() {
        if (this.disabled) return;                        // a disabled button does nothing, as in a browser
        if (this.id === 'submitBtn') { document.getElementById('searchForm').dispatchEvent({ type: 'submit' }); return; }
        this.dispatchEvent({ type: 'click' });
    }
    scrollIntoView() {}
    focus() {}
    getContext() { return new Proxy({}, { get: () => () => {} }); }
    _descendants(out = []) {
        this.children.forEach(c => { if (c.tagName !== '#TEXT') { out.push(c); c._descendants(out); } });
        return out;
    }
    querySelectorAll(selector) {
        const groups = selector.split(',').map(g => g.trim().split(/\s+/).map(parseCompound));
        return this._descendants().filter(n => groups.some(chain => matchesChain(n, chain, this)));
    }
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
}

class ShimText extends ShimNode {
    constructor(text) { super('#text'); this._text = String(text); }
}

function parseCompound(part) {
    const c = { tag: null, id: null, classes: [], attrs: [], checked: false };
    const m = part.match(/^[a-zA-Z][a-zA-Z0-9]*/);
    let rest = part;
    if (m) { c.tag = m[0].toUpperCase(); rest = part.slice(m[0].length); }
    const re = /#([\w-]+)|\.([\w-]+)|\[([\w-]+)="([^"]*)"\]|(:checked)/g;
    let t;
    while ((t = re.exec(rest))) {
        if (t[1]) c.id = t[1];
        else if (t[2]) c.classes.push(t[2]);
        else if (t[3]) c.attrs.push([t[3], t[4]]);
        else if (t[5]) c.checked = true;
    }
    return c;
}

function matchesCompound(n, c) {
    if (c.tag && n.tagName !== c.tag) return false;
    if (c.id && n.id !== c.id) return false;
    if (c.classes.some(k => !n.classList.contains(k))) return false;
    if (c.attrs.some(([k, v]) => n.getAttribute(k) !== v)) return false;
    if (c.checked && !n.checked) return false;
    return true;
}

function matchesChain(n, chain, scope) {
    if (!matchesCompound(n, chain[chain.length - 1])) return false;
    let i = chain.length - 2;
    let p = n.parentNode;
    while (i >= 0 && p && p !== scope.parentNode) {
        if (matchesCompound(p, chain[i])) i--;
        p = p.parentNode;
    }
    return i < 0;
}

const shimBody = new ShimNode('body');
const windowListeners = {};
globalThis.document = {
    body: shimBody,
    activeElement: shimBody,
    createElement: tag => new ShimNode(tag),
    createTextNode: text => new ShimText(text),
    getElementById: id => shimBody._descendants().find(n => n.id === id) || null,
    querySelector: sel => sel.startsWith('meta[') ? { content: 'csrf-token' } : shimBody.querySelector(sel),
    querySelectorAll: sel => shimBody.querySelectorAll(sel),
    addEventListener: () => {}
};
globalThis.window = {
    location: { origin: 'http://localhost' },
    addEventListener: (type, fn) => { (windowListeners[type] = windowListeners[type] || []).push(fn); }
};
const alerts = [];
globalThis.alert = msg => { alerts.push(String(msg)); };
globalThis.confirm = () => true;
globalThis.localStorage = { getItem: () => null, setItem: () => {} };

for (const { tag, id } of __STATIC__) {
    const n = new ShimNode(tag);
    n.setAttribute('id', id);
    shimBody.appendChild(n);
}
for (const [name, value] of __RADIOS__) {
    const r = new ShimNode('input');
    r.setAttribute('type', 'radio');
    r.setAttribute('name', name);
    r.setAttribute('value', value);
    shimBody.appendChild(r);
}
document.getElementById('outputPreset').value = 'dynamic';
document.getElementById('rejectReasonModal').style.display = 'none';
document.getElementById('editorModal').style.display = 'none';

// fetch: every request is recorded; /api/search, /api/select_image, /api/reject_image and
// /api/upload_manual_image wait until the test answers them, the page's bookkeeping calls answer at once.
const calls = [];
let fetchHonoursAbort = true;
function response(data, status = 200) {
    return { ok: status < 400, status, headers: { get: () => null }, json: async () => data };
}
globalThis.fetch = (url, init = {}) => {
    const call = { url: String(url), init, aborted: false };
    call.body = typeof init.body === 'string' ? JSON.parse(init.body)
        : (init.body instanceof FormData ? Object.fromEntries([...init.body.entries()].filter(([k]) => k !== 'file')) : null);
    call.promise = new Promise((resolve, reject) => { call.resolve = resolve; call.reject = reject; });
    if (init.signal) {
        const onAbort = () => {
            call.aborted = true;
            if (fetchHonoursAbort) call.reject(Object.assign(new Error('The operation was aborted'), { name: 'AbortError' }));
        };
        if (init.signal.aborted) onAbort(); else init.signal.addEventListener('abort', onAbort);
    }
    calls.push(call);
    if (!/^\/api\/(search|select_image|reject_image|upload_manual_image)$/.test(call.url)) {
        call.resolve(response(call.url.startsWith('/api/products-json') ? { status: 'success', products: [] } : { status: 'success' }));
    }
    return call.promise;
};
const answer = (call, data, status = 200) => call.resolve(response(data, status));
const flush = async () => { for (let i = 0; i < 20; i++) await new Promise(r => setImmediate(r)); };
const requests = path => calls.filter(c => c.url === path);
"""

HELPERS = r"""
function productItem() {
    const item = document.createElement('div');
    item.className = 'product-item';
    document.getElementById('productList').appendChild(item);
    return item;
}
function openProductRow(prod) { selectProduct(prod, productItem()); }
function press(key) {
    (windowListeners.keydown || []).forEach(fn => fn({ key, code: '', preventDefault() {} }));
}
const text = id => { const n = document.getElementById(id); return n ? n.textContent : null; };
const cards = () => document.querySelectorAll('#candidatesContainer .candidate-card');
const cardUrls = () => cards().map(c => c.dataset.url);
const approveButtons = () => document.querySelectorAll('#confirmImageBtn, .js-approve-candidate');
const imgSrcs = id => document.getElementById(id).querySelectorAll('img').map(i => i.src);

const A = { row_number: 5, product_name: 'Almarai Fresh Milk 1L', brand: 'Almarai', barcode: '', sku_key: 'key-a',
            size: '1L', product_name_ar: 'حليب المراعي 1 لتر', brand_ar: 'المراعي', category: 'Dairy',
            sub_category: 'Milk', origin: 'KSA', existing_image_link: '', needs_review: false };
const B_URLS = ['https://www.lulu.com/b1.jpg', 'https://www.lulu.com/b2.jpg', 'https://www.lulu.com/b3.jpg'];
const B = { row_number: 9, product_name: 'Almarai Fresh Milk 2L', brand: 'Almarai', barcode: '6281007000024',
            sku_key: '06281007000024', size: '2L', product_name_ar: 'حليب المراعي 2 لتر', brand_ar: 'المراعي',
            category: 'Dairy', sub_category: 'Milk', origin: 'KSA', existing_image_link: '', needs_review: true,
            preselected: true, needs_review_url: B_URLS[0],
            curation_candidates: B_URLS.map((u, i) => ({ image_url: u, status: i === 0 ? 'preselected' : 'eligible',
                                                           title: 'B candidate ' + (i + 1) })) };
const D = Object.assign({}, A, { row_number: 12, product_name: 'Almarai Laban 1L', sku_key: 'key-d', size: '1L' });
const C_LINK = 'https://res.cloudinary.com/demo/image/upload/products/c.png';
const C = Object.assign({}, A, { row_number: 7, product_name: 'Almarai Cheese 200g', sku_key: 'key-c', size: '200g',
                                 existing_image_link: C_LINK });
const A_URL = 'https://www.carrefouruae.com/a1.jpg';
const A_ANSWER = { status: 'review', decision: 'REVIEW_PRESELECTED', sku_key: 'key-a', selected_image: { url: A_URL },
                   candidates: [{ url: A_URL, status: 'preselected', title: 'A result' },
                                { url: 'https://www.carrefouruae.com/a2.jpg', status: 'eligible', title: 'A second' }] };
const D_URL = 'https://www.carrefouruae.com/d1.jpg';
const D_ANSWER = { status: 'review', decision: 'REVIEW_PRESELECTED', sku_key: 'key-d', selected_image: { url: D_URL },
                   candidates: [{ url: D_URL, status: 'preselected', title: 'D result' }] };
const identity = body => body && { row_number: String(body.row_number), sku_key: body.sku_key, product_name: body.product_name,
                                   brand: body.brand, barcode: body.barcode, size: body.size,
                                   product_name_ar: body.product_name_ar, brand_ar: body.brand_ar };
const out = {};
"""


def run(scenario: str, tmp_path) -> dict:
    script, static, radios = _page()
    shim = SHIM.replace("__STATIC__", json.dumps(static)).replace("__RADIOS__", json.dumps(radios))
    source = shim + "\n" + script + "\n" + HELPERS + "\n(async () => {\n" + scenario + \
        "\nconsole.log('__OUT__' + JSON.stringify(out));\n})().catch(e => { console.error(e && e.stack || e); process.exit(3); });\n"
    path = tmp_path / "catalog_page.js"
    path.write_text(source, encoding="utf-8")
    result = subprocess.run([NODE, str(path)], capture_output=True, text=True, timeout=60, encoding="utf-8")
    assert result.returncode == 0, result.stderr + result.stdout
    line = next(line for line in result.stdout.splitlines() if line.startswith("__OUT__"))
    return json.loads(line[len("__OUT__"):])


def identity_of(p):
    return {"row_number": str(p["row_number"]), "sku_key": p["sku_key"], "product_name": p["product_name"],
            "brand": p["brand"], "barcode": p["barcode"], "size": p["size"],
            "product_name_ar": p["product_name_ar"], "brand_ar": p["brand_ar"]}


B = {"row_number": 9, "product_name": "Almarai Fresh Milk 2L", "brand": "Almarai", "barcode": "6281007000024",
     "sku_key": "06281007000024", "size": "2L", "product_name_ar": "حليب المراعي 2 لتر", "brand_ar": "المراعي"}
A = {"row_number": 5, "product_name": "Almarai Fresh Milk 1L", "brand": "Almarai", "barcode": "", "sku_key": "key-a",
     "size": "1L", "product_name_ar": "حليب المراعي 1 لتر", "brand_ar": "المراعي"}
B_URLS = ["https://www.lulu.com/b1.jpg", "https://www.lulu.com/b2.jpg", "https://www.lulu.com/b3.jpg"]
A_URL = "https://www.carrefouruae.com/a1.jpg"
D_URL = "https://www.carrefouruae.com/d1.jpg"


# ---------------------------------------------------------------------------
# A late search answer for a product that is no longer open
# ---------------------------------------------------------------------------

STALE_THEN_APPROVE = r"""
fetchHonoursAbort = __HONOUR__;
openProductRow(A);                                  // live search for A (15-60 s)
const searchA = requests('/api/search')[0];
out.search_a_row = searchA.body.row_number;
openProductRow(B);                                  // B has stored candidates: shown at once
out.search_a_aborted = searchA.aborted;
answer(searchA, A_ANSWER);                          // A's answer arrives late
await flush();
out.form_sku = document.getElementById('searchForm').dataset.skuKey;
out.form_name = document.getElementById('productName').value;
out.card_urls = cardUrls();
out.recommended_url = (document.querySelector('#recommendedContainer .recommended-card') || { dataset: {} }).dataset.url;
out.recommended_text = text('recommendedContainer');
out.search_count = requests('/api/search').length;
document.querySelectorAll('.js-approve-candidate')[1].click();       // approve B's second candidate
await flush();
const approve = requests('/api/select_image')[0];
out.approve_identity = identity(approve.body);
out.approve_url = approve.body.image_url;
out.approve_decision = approve.body.search_decision;
"""


@pytest.mark.parametrize("honour_abort", ["true", "false"], ids=["aborted", "answer-arrives-anyway"])
def test_a_late_answer_for_another_product_is_dropped(tmp_path, honour_abort):
    out = run(STALE_THEN_APPROVE.replace("__HONOUR__", honour_abort), tmp_path)
    assert out["search_a_row"] == "5"
    assert out["search_a_aborted"] is True                    # the previous request is aborted
    # the form and the results still show B, with B's key
    assert out["form_sku"] == B["sku_key"] and out["form_name"] == B["product_name"]
    assert out["card_urls"] == B_URLS
    assert out["recommended_url"] == B_URLS[0]
    assert B["product_name"] in out["recommended_text"] and A["product_name"] not in out["recommended_text"]
    assert out["search_count"] == 1
    # the approval publishes B's image into B's row with B's identity
    assert out["approve_identity"] == identity_of(B)
    assert out["approve_url"] == B_URLS[1]
    assert out["approve_decision"] == "REVIEW_PRESELECTED"


def test_a_late_answer_does_not_replace_the_newer_search(tmp_path):
    out = run(r"""
fetchHonoursAbort = false;                           // the old answer is delivered even though it was aborted
openProductRow(A);
openProductRow(D);                                   // D has no stored candidates: its own live search
const [searchA, searchD] = requests('/api/search');
out.rows = [searchA.body.row_number, searchD.body.row_number];
answer(searchD, D_ANSWER);
await flush();
answer(searchA, A_ANSWER);
await flush();
out.card_urls = cardUrls();
out.form_sku = document.getElementById('searchForm').dataset.skuKey;
document.getElementById('confirmImageBtn').click();
await flush();
out.approve_identity = identity(requests('/api/select_image')[0].body);
out.approve_url = requests('/api/select_image')[0].body.image_url;
""", tmp_path)
    assert out["rows"] == ["5", "12"]
    assert out["card_urls"] == [D_URL]
    assert out["form_sku"] == "key-d"
    assert out["approve_identity"]["row_number"] == "12" and out["approve_identity"]["sku_key"] == "key-d"
    assert out["approve_url"] == D_URL


def test_switching_product_removes_the_old_results_and_buttons(tmp_path):
    out = run(r"""
openProductRow(A);
answer(requests('/api/search')[0], A_ANSWER);
await flush();
out.before = cardUrls().length;
openProductRow(D);                                   // D's search is still running
out.after_cards = cardUrls();
out.after_buttons = approveButtons().length;
out.results_display = document.getElementById('resultsContent').style.display;
press('a');
press('1');
press('Enter');
await flush();
out.approvals = requests('/api/select_image').length;
""", tmp_path)
    assert out["before"] == 2
    assert out["after_cards"] == [] and out["after_buttons"] == 0
    assert out["results_display"] == "none"
    assert out["approvals"] == 0


# ---------------------------------------------------------------------------
# The results carry the identity they were found for
# ---------------------------------------------------------------------------

def test_approve_and_reject_send_the_bound_sheet_identity_not_the_edited_form(tmp_path):
    out = run(r"""
openProductRow(A);
const searchA = requests('/api/search')[0];
// the reviewer edits the search fields (a refined name, another brand); the search sends them
document.getElementById('productName').value = 'milk full fat';
document.getElementById('brand').value = 'Other';
answer(searchA, A_ANSWER);
await flush();
out.search_name = searchA.body.product_name;
out.card_text = text('recommendedContainer');
document.getElementById('confirmImageBtn').click();
await flush();
const approve = requests('/api/select_image')[0];
out.approve_identity = identity(approve.body);
out.approve_category = approve.body.category;
answer(approve, { status: 'failed', error: 'upload_failed' }, 500);
await flush();
document.querySelectorAll('.js-reject-candidate')[1].click();
document.querySelector('input[name="reject_reason_code"][value="WRONG_SIZE"]').checked = true;
document.getElementById('rejectResearch').checked = false;
const rejecting = submitReject();
await flush();
const reject = requests('/api/reject_image')[0];
out.reject_identity = identity(reject.body);
out.reject_extra = [reject.body.image_url, reject.body.category, reject.body.sub_category, reject.body.origin];
answer(reject, { status: 'success' });
await rejecting;
""", tmp_path)
    assert out["search_name"] == "Almarai Fresh Milk 1L"        # the edit came after the search was sent
    assert "Almarai Fresh Milk 1L" in out["card_text"] and "milk full fat" not in out["card_text"]
    assert out["reject_identity"] == identity_of(A)
    assert out["reject_extra"] == ["https://www.carrefouruae.com/a2.jpg", "Dairy", "Milk", "KSA"]
    assert out["approve_identity"] == identity_of(A)
    assert out["approve_category"] == "Dairy"


def test_a_search_for_a_product_without_a_listed_key_takes_the_answers_key(tmp_path):
    out = run(r"""
openProductRow(Object.assign({}, A, { sku_key: '' }));
answer(requests('/api/search')[0], A_ANSWER);
await flush();
out.form_sku = document.getElementById('searchForm').dataset.skuKey;
document.getElementById('confirmImageBtn').click();
await flush();
out.approve_sku = requests('/api/select_image')[0].body.sku_key;
""", tmp_path)
    assert out["form_sku"] == "key-a" and out["approve_sku"] == "key-a"


def test_upload_sends_the_open_products_identity(tmp_path):
    out = run(r"""
openProductRow(B);
document.getElementById('productName').value = 'edited name';
uploadFile = { name: 'photo.png' };
document.getElementById('editorCanvas').toBlob = cb => cb(new Blob(['png']));
await commitEditorUpload();
await flush();
const upload = requests('/api/upload_manual_image')[0];
out.upload_identity = identity(upload.body);
out.during = approveButtons().map(b => b.disabled);
answer(upload, { status: 'success', image_link: 'https://res.cloudinary.com/demo/b.png', sheet_value: 'x', isolated: true });
await flush();
out.after_text = text('recommendedContainer');
out.confirm_button = !!document.getElementById('confirmImageBtn');
press('a');
await flush();
out.approvals = requests('/api/select_image').length;
""", tmp_path)
    assert out["upload_identity"] == identity_of(B)
    assert out["during"] and all(out["during"])
    assert APPROVED in out["after_text"] and out["confirm_button"] is False
    assert out["approvals"] == 0


# ---------------------------------------------------------------------------
# One approval at a time, and never twice
# ---------------------------------------------------------------------------

def test_every_approve_control_is_disabled_while_an_approval_runs(tmp_path):
    out = run(r"""
openProductRow(B);
document.getElementById('confirmImageBtn').click();
await flush();
out.first = requests('/api/select_image').length;
out.disabled = approveButtons().map(b => b.disabled);
// every way to approve again while it runs
press('a'); press('Enter'); press('2'); press('a');
document.querySelectorAll('.js-approve-candidate').forEach(b => b.click());
await approveCandidate({ url: B_URLS[2] }, bindToOpenProduct({}), null);
await flush();
out.while_running = requests('/api/select_image').length;
answer(requests('/api/select_image')[0], { status: 'success', image_link: 'https://res.cloudinary.com/demo/b.png',
                                           sheet_value: 'x', isolated: true });
await flush();
out.alerts = alerts.slice();
out.recommended_text = text('recommendedContainer');
out.confirm_button = !!document.getElementById('confirmImageBtn');
out.grid_text = text('candidatesContainer');
out.grid_buttons = document.querySelectorAll('.js-approve-candidate').map(b => b.disabled);
press('a'); press('Enter'); press('3'); press('a');
document.querySelectorAll('.js-approve-candidate').forEach(b => b.click());
await flush();
out.after = requests('/api/select_image').length;
""", tmp_path)
    assert out["first"] == 1
    assert len(out["disabled"]) == 4 and all(out["disabled"])       # the card's button and the 3 grid buttons
    assert out["while_running"] == 1
    assert any("تم رفع الصورة وتحديث الصف 9" in a for a in out["alerts"])
    assert APPROVED in out["recommended_text"] and out["confirm_button"] is False
    assert APPROVED in out["grid_text"]                               # the approved grid card is marked
    assert out["grid_buttons"] == [True, True]                        # the other two stay locked
    assert out["after"] == 1


def test_a_refused_approval_frees_the_controls_and_shows_the_reason(tmp_path):
    out = run(r"""
openProductRow(B);
document.getElementById('confirmImageBtn').click();
await flush();
answer(requests('/api/select_image')[0], { status: 'failed', error: 'المنتج تغيّر أثناء المراجعة؛ افتحه من جديد' }, 500);
await flush();
out.alerts = alerts.slice();
out.disabled = approveButtons().map(b => b.disabled);
press('a');
await flush();
out.approvals = requests('/api/select_image').length;
""", tmp_path)
    assert any("المنتج تغيّر أثناء المراجعة؛ افتحه من جديد" in a for a in out["alerts"])
    assert out["disabled"] and not any(out["disabled"])
    assert out["approvals"] == 2                                     # an explicit new approval is possible


def test_an_approval_finishing_after_a_product_switch_leaves_the_new_product_alone(tmp_path):
    out = run(r"""
openProductRow(B);
document.getElementById('confirmImageBtn').click();
await flush();
openProductRow(D);                                   // the reviewer moves on while B's approval runs
answer(requests('/api/search')[0], D_ANSWER);
await flush();
out.d_disabled = approveButtons().map(b => b.disabled);
answer(requests('/api/select_image')[0], { status: 'success', image_link: 'https://res.cloudinary.com/demo/b.png',
                                           sheet_value: 'x', isolated: true });
await flush();
out.card_urls = cardUrls();
out.recommended_text = text('recommendedContainer');
out.d_after = approveButtons().map(b => b.disabled);
document.getElementById('confirmImageBtn').click();
await flush();
out.second = identity(requests('/api/select_image')[1].body);
""", tmp_path)
    assert out["d_disabled"] == [True, True]            # D's buttons, drawn while B's approval ran, are disabled
    assert out["card_urls"] == [D_URL] and APPROVED not in out["recommended_text"]
    assert out["d_after"] == [False, False]
    assert out["second"]["row_number"] == "12" and out["second"]["sku_key"] == "key-d"


# ---------------------------------------------------------------------------
# Keys 1-9 select, A / Enter approve the selected candidate
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("approve_key", ["a", "Enter"])
def test_number_keys_only_select_and_the_approve_key_approves_the_selection(tmp_path, approve_key):
    out = run(r"""
openProductRow(B);
press('1'); press('3'); press('2');
await flush();
out.after_numbers = requests('/api/select_image').length;
out.highlighted = cards().map(c => c.classList.contains('confirming-active'));
press('__KEY__');
await flush();
out.approvals = requests('/api/select_image').map(c => c.body.image_url);
""".replace("__KEY__", approve_key), tmp_path)
    assert out["after_numbers"] == 0
    assert out["highlighted"] == [False, True, False]
    assert out["approvals"] == [B_URLS[1]]


def test_without_a_selection_a_approves_the_recommended_card_and_enter_on_a_button_is_left_to_the_browser(tmp_path):
    out = run(r"""
openProductRow(B);
press('9');                                          // no ninth candidate: nothing selected
document.activeElement = document.getElementById('rejectImageBtn');
press('Enter');                                      // the browser activates the focused button itself
await flush();
out.enter_on_button = requests('/api/select_image').length;
document.activeElement = document.body;
press('a');
await flush();
out.approvals = requests('/api/select_image').map(c => c.body.image_url);
""", tmp_path)
    assert out["enter_on_button"] == 0
    assert out["approvals"] == [B_URLS[0]]


def test_the_grid_buttons_no_longer_promise_a_number_key_approval(tmp_path):
    out = run(r"""
openProductRow(B);
out.labels = document.querySelectorAll('.js-approve-candidate').map(b => b.textContent);
out.hints = cards().map(c => c.querySelector('.candidate-meta').textContent);
""", tmp_path)
    assert all(label == "🎯 اعتماد" for label in out["labels"])
    assert [h.startswith(f"[{i + 1}]") for i, h in enumerate(out["hints"])] == [True, True, True]


# ---------------------------------------------------------------------------
# A product with a final image link: show it, no automatic search
# ---------------------------------------------------------------------------

def test_a_completed_product_shows_its_sheet_image_and_searches_only_on_request(tmp_path):
    out = run(r"""
openProductRow(A);                                   // a search is running for A
const searchA = requests('/api/search')[0];
openProductRow(C);
out.search_a_aborted = searchA.aborted;
out.searches = requests('/api/search').length;
out.images = imgSrcs('recommendedContainer');
out.text = text('recommendedContainer');
out.banner = text('outcomeBanner');
out.status = text('overallStatus');
out.confirm_button = !!document.getElementById('confirmImageBtn');
out.results_display = document.getElementById('resultsContent').style.display;
out.loading_display = document.getElementById('loading').style.display;
answer(searchA, A_ANSWER);                           // A's late answer does not replace C's view
await flush();
out.images_after_late_answer = imgSrcs('recommendedContainer');
document.getElementById('researchCompletedBtn').click();
await flush();
out.research = requests('/api/search').map(c => c.body.row_number);
""", tmp_path)
    assert out["search_a_aborted"] is True
    assert out["searches"] == 1                                       # only A's
    assert out["images"] == ["https://res.cloudinary.com/demo/image/upload/products/c.png"]
    assert "الصورة الحالية في الشيت" in out["text"] and "إعادة البحث" in out["text"]
    assert "Almarai Cheese 200g" in out["text"]
    assert "صورة نهائية" in out["banner"] and "لم يُجرَ بحث تلقائي" in out["banner"]
    assert out["status"] == "له صورة نهائية في الشيت"
    assert out["confirm_button"] is False
    assert out["results_display"] == "block" and out["loading_display"] == "none"
    assert out["images_after_late_answer"] == out["images"]
    assert out["research"] == ["5", "7"]                              # "إعادة البحث" searches C, on request


def test_a_product_without_a_final_link_still_searches_at_once(tmp_path):
    out = run(r"""
openProductRow(D);
out.searches = requests('/api/search').map(c => c.body.row_number);
""", tmp_path)
    assert out["searches"] == ["12"]


# ---------------------------------------------------------------------------
# Review fixes: keyboard paths that still published, and approve buttons that came back after an approval
# ---------------------------------------------------------------------------

def test_browser_shortcuts_with_ctrl_cmd_or_alt_never_approve_or_select(tmp_path):
    """Ctrl/Cmd+A (select all) used to publish the recommended image; Ctrl+2 (switch tab) selected a candidate."""
    out = run(r"""
openProductRow(B);
const withMods = (key, mods) => (windowListeners.keydown || []).forEach(fn => fn(Object.assign(
    { key, code: '', preventDefault() {} }, mods)));
withMods('a', { ctrlKey: true }); withMods('a', { metaKey: true }); withMods('a', { altKey: true });
withMods('2', { ctrlKey: true });
out.highlighted = cards().map(c => c.classList.contains('confirming-active'));
press('3');
withMods('Enter', { ctrlKey: true }); withMods('Enter', { metaKey: true });
await flush();
out.approvals = requests('/api/select_image').length;
press('a');
await flush();
out.plain_a = requests('/api/select_image').map(c => c.body.image_url);
""", tmp_path)
    assert out["highlighted"] == [False, False, False]
    assert out["approvals"] == 0
    assert out["plain_a"] == [B_URLS[2]]                    # the plain key still approves the selection


def test_enter_without_a_selected_candidate_approves_nothing(tmp_path):
    out = run(r"""
openProductRow(B);
press('Enter');
await flush();
out.without_selection = requests('/api/select_image').length;
press('2');
press('Enter');
await flush();
out.approvals = requests('/api/select_image').map(c => c.body.image_url);
""", tmp_path)
    assert out["without_selection"] == 0
    assert out["approvals"] == [B_URLS[1]]


def test_keys_never_approve_or_reject_results_the_reviewer_cannot_see(tmp_path):
    """A re-search hides the old results behind the spinner (and a failed search behind the placeholder): the
    old recommended card and grid stay in the page, but A / 1-9 / Enter / X must not act on them."""
    out = run(r"""
openProductRow(B);
document.getElementById('submitBtn').click();        // re-search: the old results are hidden
out.hidden = document.getElementById('resultsContent').style.display;
press('a'); press('2'); press('Enter'); press('a'); press('x');
await flush();
out.while_searching = requests('/api/select_image').length;
out.highlighted = cards().map(c => c.classList.contains('confirming-active'));
out.reject_modal = document.getElementById('rejectReasonModal').style.display;
requests('/api/search')[0].reject(new Error('network down'));
await flush();
out.hidden_after_failure = document.getElementById('resultsContent').style.display;
press('a'); press('1'); press('Enter');
await flush();
out.after_failure = requests('/api/select_image').length;
""", tmp_path)
    assert out["hidden"] == "none" and out["hidden_after_failure"] == "none"
    assert out["while_searching"] == 0 and out["after_failure"] == 0
    assert out["highlighted"] == [False, False, False]
    assert out["reject_modal"] == "none"


def test_a_search_started_before_an_approval_cannot_bring_back_approve_buttons(tmp_path):
    """The automatic search of D runs; the reviewer approves a pasted URL. D's answer, arriving after the
    approval, used to redraw live approve buttons over "تم الاعتماد ✓" (A then published a second image)."""
    out = run(r"""
openProductRow(D);
const searchD = requests('/api/search')[0];
document.getElementById('manualImageUrl').value = 'https://www.lulu.com/manual-d.jpg';
previewManualImage();
document.getElementById('confirmImageBtn').click();
await flush();
answer(requests('/api/select_image')[0], { status: 'success', image_link: 'https://res.cloudinary.com/demo/d.png',
                                           sheet_value: 'x', isolated: true });
await flush();
out.search_aborted = searchD.aborted;
answer(searchD, D_ANSWER);
await flush();
out.recommended_text = text('recommendedContainer');
out.confirm_button = !!document.getElementById('confirmImageBtn');
press('a'); press('1'); press('a');
await flush();
out.approvals = requests('/api/select_image').length;
""", tmp_path)
    assert out["search_aborted"] is True
    assert APPROVED in out["recommended_text"] and out["confirm_button"] is False
    assert out["approvals"] == 1


def test_a_search_clicked_while_an_approval_runs_leaves_the_approved_card_visible(tmp_path):
    out = run(r"""
fetchHonoursAbort = false;                           // the search answer is delivered even though it was aborted
openProductRow(B);
document.getElementById('confirmImageBtn').click();
await flush();
document.getElementById('submitBtn').click();        // a re-search while the approval runs
const search = requests('/api/search')[0];
answer(requests('/api/select_image')[0], { status: 'success', image_link: 'https://res.cloudinary.com/demo/b.png',
                                           sheet_value: 'x', isolated: true });
await flush();
out.search_aborted = search.aborted;
out.results = document.getElementById('resultsContent').style.display;
out.loading = document.getElementById('loading').style.display;
answer(search, { status: 'review', decision: 'REVIEW_UNSELECTED', sku_key: B.sku_key,
                 candidates: [{ url: 'https://www.lulu.com/b9.jpg', status: 'eligible', title: 'late' }] });
await flush();
out.recommended_text = text('recommendedContainer');
out.card_urls = cardUrls();
press('a'); press('1'); press('Enter');
await flush();
out.approvals = requests('/api/select_image').length;
""", tmp_path)
    assert out["search_aborted"] is True
    assert out["results"] == "block" and out["loading"] == "none"
    assert APPROVED in out["recommended_text"]
    assert out["card_urls"] == B_URLS
    assert out["approvals"] == 1


def test_a_reject_research_answer_after_an_approval_is_dropped(tmp_path):
    """Reject + re-search is a search too: if the reviewer closed the dialog (Escape) and approved another image
    before its answer came, the answer must not redraw approve buttons over the approved card."""
    out = run(r"""
openProductRow(B);
document.querySelectorAll('.js-reject-candidate')[2].click();
document.querySelector('input[name="reject_reason_code"][value="WRONG_SIZE"]').checked = true;
document.getElementById('rejectResearch').checked = true;
const rejecting = submitReject();
await flush();
closeRejectModal();                                  // Escape while the re-search runs
document.getElementById('confirmImageBtn').click();
await flush();
answer(requests('/api/select_image')[0], { status: 'success', image_link: 'https://res.cloudinary.com/demo/b.png',
                                           sheet_value: 'x', isolated: true });
await flush();
answer(requests('/api/reject_image')[0], { status: 'review', decision: 'REVIEW_UNSELECTED', sku_key: B.sku_key,
                                           candidates: [{ url: 'https://www.lulu.com/b7.jpg', status: 'eligible', title: 'new' }] });
await rejecting;
await flush();
out.recommended_text = text('recommendedContainer');
out.confirm_button = !!document.getElementById('confirmImageBtn');
out.card_urls = cardUrls();
press('a'); press('1'); press('a');
await flush();
out.approvals = requests('/api/select_image').length;
""", tmp_path)
    assert APPROVED in out["recommended_text"] and out["confirm_button"] is False
    assert out["card_urls"] == B_URLS
    assert out["approvals"] == 1


def test_a_reject_research_answer_is_still_shown_when_nothing_happened_in_between(tmp_path):
    out = run(r"""
openProductRow(B);
document.querySelectorAll('.js-reject-candidate')[2].click();
document.querySelector('input[name="reject_reason_code"][value="WRONG_SIZE"]').checked = true;
document.getElementById('rejectResearch').checked = true;
const rejecting = submitReject();
await flush();
answer(requests('/api/reject_image')[0], { status: 'review', decision: 'REVIEW_UNSELECTED', sku_key: B.sku_key,
                                           candidates: [{ url: 'https://www.lulu.com/b7.jpg', status: 'eligible', title: 'new' }] });
await rejecting;
await flush();
out.card_urls = cardUrls();
out.buttons = approveButtons().map(b => b.disabled);
""", tmp_path)
    assert out["card_urls"] == ["https://www.lulu.com/b7.jpg"]
    assert out["buttons"] == [False]


def test_a_pasted_url_preview_clears_the_number_key_selection(tmp_path):
    """After 2 selected a grid card, previewing a pasted URL shows a new image in the main card: A must approve
    that image (the one on screen, whose button says [A]), not the grid card selected before."""
    out = run(r"""
openProductRow(B);
press('2');
document.getElementById('manualImageUrl').value = 'https://www.lulu.com/manual-b.jpg';
previewManualImage();
out.highlighted = cards().map(c => c.classList.contains('confirming-active'));
press('Enter');
await flush();
out.after_enter = requests('/api/select_image').length;
press('a');
await flush();
out.approvals = requests('/api/select_image').map(c => c.body.image_url);
""", tmp_path)
    assert out["highlighted"] == [False, False, False]
    assert out["after_enter"] == 0
    assert out["approvals"] == ["https://www.lulu.com/manual-b.jpg"]
