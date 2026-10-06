"""Node harness for the review screen scripts (dashboard/public/js/review/*.js).

The scripts run unchanged under node against a small DOM (elements, attributes, classes, data-*, bubbling events,
simple selectors, focus) with a scripted fetch: each request is recorded, the page's data calls answer at once from
FIXTURE, and search / approve / reject / upload wait until the test answers them. Used by
tests/test_catalog_review_race.py (the review safety rules) and tests/test_laqta_review.py.

Pictures: an <img> fires `load` as soon as it is attached to the document (a cached picture), or `error` when its src
is in `imageFails`; with `imageMode = 'hold'` it fires nothing until the test calls `releaseImages()`. An
IntersectionObserver reports every observed node as visible (`ioVisible(node)` decides; `ioRefresh()` reports again).
The page boots with approveSettleMs 0 and approveUndoMs 0 (an approval is sent at once, no «تراجع» hold) unless the
test asks for the real delays, and with the mode it is given (modeExplicit) unless the test asks for the automatic choice.

Questions (R.ask, the review screen's in-page confirmation that replaced window.confirm) are answered like the old
confirm: the question (title and text) is recorded in `confirms`, and «متأكد» or «إلغاء» is clicked by `confirmAnswer`
(at the time of asking) unless `askAuto = false`, when the test clicks it itself.
"""

import json
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REVIEW_JS = ROOT / "dashboard" / "public" / "js" / "review"
# theme_preview.js first, as catalog.blade.php loads it (single.js calls it while drawing the published image)
SCRIPTS = ["theme_preview", "core", "ui", "jobs", "single", "bulk", "app"]
NODE = shutil.which("node")


def scripts_source() -> str:
    return "\n".join((REVIEW_JS / f"{name}.js").read_text(encoding="utf-8") for name in SCRIPTS)


SHIM = r"""
'use strict';
const kebab = s => s.replace(/[A-Z]/g, m => '-' + m.toLowerCase());
const camel = s => s.replace(/-([a-z])/g, (_, c) => c.toUpperCase());

class ShimClassList {
    constructor(node) { this.node = node; }
    _all() { return String(this.node.getAttribute('class') || '').split(/\s+/).filter(Boolean); }
    _set(list) { this.node.setAttribute('class', list.join(' ')); }
    add(...names) { const all = this._all(); names.forEach(n => { if (!all.includes(n)) all.push(n); }); this._set(all); }
    remove(...names) { this._set(this._all().filter(n => !names.includes(n))); }
    contains(name) { return this._all().includes(name); }
    toggle(name, force) {
        const want = force === undefined ? !this.contains(name) : !!force;
        if (want) this.add(name); else this.remove(name);
        return want;
    }
}

class ShimStyle {
    setProperty(k, v) { this[k] = String(v); }
    removeProperty(k) { delete this[k]; }
}

class ShimText {
    constructor(text) { this.nodeType = 3; this.data = String(text); this.parentNode = null; this.tagName = '#TEXT'; }
    get textContent() { return this.data; }
    set textContent(v) { this.data = String(v); }
}

let shimDocument = null;

class ShimElement {
    constructor(tag) {
        this.nodeType = 1;
        this.tagName = String(tag || 'div').toUpperCase();
        this.childNodes = [];
        this.parentNode = null;
        this.attrs = {};
        this.listeners = {};
        this.style = new ShimStyle();
        this.classList = new ShimClassList(this);
        this.disabled = false;
        this.checked = false;
        this._value = '';
        this.files = null;
        this.offsetHeight = 0;
        const self = this;
        this.dataset = new Proxy({}, {
            get: (_, k) => (typeof k === 'string' ? (self.attrs['data-' + kebab(k)] === undefined ? undefined : self.attrs['data-' + kebab(k)]) : undefined),
            set: (_, k, v) => { self.attrs['data-' + kebab(k)] = String(v); return true; },
            deleteProperty: (_, k) => { delete self.attrs['data-' + kebab(k)]; return true; },
            ownKeys: () => Object.keys(self.attrs).filter(a => a.startsWith('data-')).map(a => camel(a.slice(5))),
            getOwnPropertyDescriptor: () => ({ enumerable: true, configurable: true })
        });
    }
    get children() { return this.childNodes.filter(c => c.nodeType === 1); }
    get firstChild() { return this.childNodes[0] || null; }
    get id() { return this.attrs.id || ''; }
    set id(v) { this.attrs.id = String(v); }
    get className() { return this.attrs['class'] || ''; }
    set className(v) { this.attrs['class'] = String(v); }
    get hidden() { return 'hidden' in this.attrs; }
    set hidden(v) { if (v) this.attrs.hidden = ''; else delete this.attrs.hidden; }
    get value() { return this._value; }
    set value(v) { this._value = v === undefined || v === null ? '' : String(v); }
    get type() { return this.attrs.type || (this.tagName === 'INPUT' ? 'text' : this.tagName === 'BUTTON' ? 'submit' : ''); }
    set type(v) { this.attrs.type = String(v); }
    get href() { return this.attrs.href || ''; }
    get src() { return this.attrs.src || ''; }
    get isConnected() { let n = this; while (n.parentNode) n = n.parentNode; return n === shimDocument; }
    get isContentEditable() { return false; }
    get textContent() { return this.childNodes.map(c => c.textContent).join(''); }
    set textContent(v) { this.childNodes.forEach(c => { c.parentNode = null; }); this.childNodes = []; if (String(v) !== '') this.appendChild(new ShimText(v)); }
    get innerText() { return this.textContent; }
    set innerHTML(v) { throw new Error('innerHTML is not allowed in the review scripts'); }
    get innerHTML() { return this.textContent; }
    getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
    setAttribute(k, v) {
        v = String(v);
        this.attrs[k] = v;
        if (k === 'disabled') this.disabled = true;
        if (k === 'checked') this.checked = true;
        if (k === 'value') this._value = v;
    }
    removeAttribute(k) { delete this.attrs[k]; if (k === 'disabled') this.disabled = false; }
    hasAttribute(k) { return k in this.attrs; }
    appendChild(n) {
        if (n.parentNode) n.parentNode.removeChild(n);
        n.parentNode = this;
        this.childNodes.push(n);
        fireImages(n);
        return n;
    }
    insertBefore(n, ref) {
        if (!ref) return this.appendChild(n);
        if (n.parentNode) n.parentNode.removeChild(n);
        n.parentNode = this;
        this.childNodes.splice(this.childNodes.indexOf(ref), 0, n);
        fireImages(n);
        return n;
    }
    removeChild(n) {
        const i = this.childNodes.indexOf(n);
        if (i >= 0) this.childNodes.splice(i, 1);
        n.parentNode = null;
        return n;
    }
    replaceChild(n, old) {
        const i = this.childNodes.indexOf(old);
        if (n.parentNode) n.parentNode.removeChild(n);
        this.childNodes[i] = n;
        n.parentNode = this;
        old.parentNode = null;
        return old;
    }
    remove() { if (this.parentNode) this.parentNode.removeChild(this); }
    contains(n) { while (n) { if (n === this) return true; n = n.parentNode; } return false; }
    addEventListener(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); }
    removeEventListener(type, fn) { this.listeners[type] = (this.listeners[type] || []).filter(f => f !== fn); }
    dispatchEvent(ev) { return dispatch(this, ev); }
    click() {
        if (this.disabled) return;
        if (this.tagName === 'INPUT' && (this.type === 'checkbox' || this.type === 'radio')) {
            this.checked = this.type === 'radio' ? true : !this.checked;
            dispatch(this, { type: 'click', bubbles: true });
            dispatch(this, { type: 'change', bubbles: true });
            return;
        }
        const ev = dispatch(this, { type: 'click', bubbles: true });
        if (!ev.defaultPrevented && this.tagName === 'BUTTON' && this.type === 'submit') {
            const form = this.closest('form');
            if (form) dispatch(form, { type: 'submit', bubbles: true });
        }
    }
    focus() { shimDocument.activeElement = this; }
    blur() { if (shimDocument.activeElement === this) shimDocument.activeElement = shimDocument.body; }
    scrollIntoView() {}
    _descendants(out = []) {
        this.children.forEach(c => { out.push(c); c._descendants(out); });
        return out;
    }
    matches(selector) { return parseSelector(selector).some(chain => matchesChain(this, chain)); }
    closest(selector) { let n = this; while (n && n.nodeType === 1) { if (n.matches(selector)) return n; n = n.parentNode; } return null; }
    querySelectorAll(selector) {
        const groups = parseSelector(selector);
        return this._descendants().filter(n => groups.some(chain => matchesChain(n, chain, this)));
    }
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
}

// Pictures attached to the document load at once (or fail when their src is in imageFails); 'hold' keeps them waiting
let imageMode = 'auto';
const imageFails = new Set();
const heldImages = [];
function fireImage(img) {
    if (img._imgFired || !img.attrs.src) return;
    img._imgFired = true;
    if (imageMode === 'hold') { heldImages.push(img); return; }
    const src = img.attrs.src;
    const failed = [...imageFails].some(f => src === f || src.includes(encodeURIComponent(f)) || src.includes(f));
    dispatch(img, { type: failed ? 'error' : 'load', bubbles: false });
}
function fireImages(n) {
    if (!n || n.nodeType !== 1 || !n.isConnected) return;
    const imgs = (n.tagName === 'IMG' ? [n] : []).concat(n._descendants().filter(c => c.tagName === 'IMG'));
    imgs.forEach(fireImage);
}
function releaseImages() {
    const mode = imageMode;
    imageMode = 'auto';
    heldImages.splice(0).forEach(img => { img._imgFired = false; fireImage(img); });
    imageMode = mode;
}

function parseSelector(selector) {
    return selector.split(',').map(g => g.trim().split(/\s+/).map(parseCompound));
}

function parseCompound(part) {
    const c = { tag: null, id: null, classes: [], attrs: [], checked: false };
    const m = part.match(/^[a-zA-Z][a-zA-Z0-9]*/);
    let rest = part;
    if (m) { c.tag = m[0].toUpperCase(); rest = part.slice(m[0].length); }
    const re = /#([\w-]+)|\.([\w-]+)|\[([\w-]+)(?:="([^"]*)")?\]|(:checked)/g;
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
    if (!n || n.nodeType !== 1) return false;
    if (c.tag && n.tagName !== c.tag) return false;
    if (c.id && n.id !== c.id) return false;
    if (c.classes.some(k => !n.classList.contains(k))) return false;
    if (c.attrs.some(([k, v]) => v === undefined ? !n.hasAttribute(k) : n.getAttribute(k) !== v)) return false;
    if (c.checked && !n.checked) return false;
    return true;
}

function matchesChain(n, chain, scope) {
    if (!matchesCompound(n, chain[chain.length - 1])) return false;
    let i = chain.length - 2;
    let p = n.parentNode;
    while (i >= 0 && p && p !== (scope ? scope.parentNode : null)) {
        if (matchesCompound(p, chain[i])) i--;
        p = p.parentNode;
    }
    return i < 0;
}

function dispatch(target, ev) {
    ev.target = ev.target || target;
    ev.defaultPrevented = false;
    let stopped = false;
    ev.preventDefault = () => { ev.defaultPrevented = true; };
    ev.stopPropagation = () => { stopped = true; };
    let node = target;
    while (node && !stopped) {
        ev.currentTarget = node;
        (node.listeners[ev.type] || []).slice().forEach(fn => fn(ev));
        if (!ev.bubbles) break;
        node = node.parentNode;
    }
    return ev;
}

class ShimDocument extends ShimElement {
    constructor() {
        super('#document');
        this.readyState = 'complete';
        this.body = new ShimElement('body');
        this.appendChild(this.body);
        this.activeElement = this.body;
        this.documentElement = this;
    }
    createElement(tag) { return new ShimElement(tag); }
    createElementNS(ns, tag) { return new ShimElement(tag); }
    createTextNode(text) { return new ShimText(text); }
    getElementById(id) { return this.body._descendants().find(n => n.id === id) || null; }
    querySelector(sel) { return sel.startsWith('meta[') ? { content: 'csrf-token', getAttribute: () => 'csrf-token' } : super.querySelector(sel); }
}
shimDocument = new ShimDocument();
globalThis.document = shimDocument;

const windowListeners = {};
globalThis.window = globalThis;
globalThis.location = { origin: 'http://localhost', pathname: '/catalog', search: '', href: 'http://localhost/catalog' };
const historyLog = [];
globalThis.history = {
    pushState: (s, t, url) => { historyLog.push(['push', url]); },
    replaceState: (s, t, url) => { historyLog.push(['replace', url]); }
};
globalThis.addEventListener = (type, fn) => { (windowListeners[type] = windowListeners[type] || []).push(fn); };
globalThis.scrollTo = () => {};
const confirms = [];
let confirmAnswer = true;
globalThis.confirm = msg => { confirms.push(String(msg)); return confirmAnswer; };
globalThis.alert = msg => { throw new Error('alert() is not used by the review screen: ' + msg); };
const toasts = [];
const runStatusListeners = [];
globalThis.Laqta = {
    toast: (msg, opts) => { toasts.push({ text: String(msg), variant: (opts || {}).variant }); return { close() {}, update() {} }; },
    onRunStatus: fn => { runStatusListeners.push(fn); }
};
globalThis.LAQTA_REVIEW_MANUAL_BOOT = true;

// IntersectionObserver: every observed node is reported (asynchronously) as visible unless ioVisible says otherwise
const ioObservers = [];
let ioVisible = () => true;
class ShimIntersectionObserver {
    constructor(callback) { this.callback = callback; this.nodes = []; ioObservers.push(this); }
    observe(node) {
        this.nodes.push(node);
        setImmediate(() => { if (this.nodes.includes(node)) this.callback([{ target: node, isIntersecting: !!ioVisible(node) }], this); });
    }
    unobserve(node) { this.nodes = this.nodes.filter(n => n !== node); }
    disconnect() { this.nodes = []; const i = ioObservers.indexOf(this); if (i >= 0) ioObservers.splice(i, 1); }
}
globalThis.IntersectionObserver = ShimIntersectionObserver;
const ioRefresh = () => ioObservers.slice().forEach(o => o.callback(o.nodes.map(n => ({ target: n, isIntersecting: !!ioVisible(n) })), o));

// fetch: every request is recorded; the page's data calls answer at once from FIXTURE, search / select / reject /
// upload wait until the test answers them (answer(call, data, status)).
const FIXTURE = __FIXTURE__;
const calls = [];
let fetchHonoursAbort = true;
let inFlight = 0;
let maxInFlight = { select: 0 };
function response(data, status = 200) {
    return { ok: status < 400, status, headers: { get: () => null }, json: async () => data };
}
const HELD = /^\/api\/(search|select_image|reject_image|upload_manual_image)$/;
globalThis.fetch = (url, init = {}) => {
    const call = { url: String(url), init, aborted: false, settled: false };
    call.body = typeof init.body === 'string' ? JSON.parse(init.body)
        : (init.body instanceof FormData ? Object.fromEntries([...init.body.entries()].filter(([k]) => k !== 'file')) : null);
    call.file = init.body instanceof FormData ? init.body.get('file') : null;
    call.promise = new Promise((resolve, reject) => { call.resolve = resolve; call.reject = reject; });
    if (init.signal) {
        const onAbort = () => {
            call.aborted = true;
            if (fetchHonoursAbort) call.reject(Object.assign(new Error('The operation was aborted'), { name: 'AbortError' }));
        };
        if (init.signal.aborted) onAbort(); else init.signal.addEventListener('abort', onAbort);
    }
    calls.push(call);
    const path = call.url.split('?')[0];
    if (/^\/api\/(select_image|upload_manual_image)$/.test(path)) {
        inFlight += 1;
        maxInFlight.select = Math.max(maxInFlight.select, inFlight);
        call.promise.then(() => { inFlight -= 1; }, () => { inFlight -= 1; });
    }
    if (!HELD.test(path)) {
        let data = { status: 'success' };
        if (path === '/api/products-json') data = { status: 'success', products: JSON.parse(JSON.stringify(FIXTURE.products)) };
        else if (path === '/api/review/queue-state') data = JSON.parse(JSON.stringify(FIXTURE.queue));
        else if (path === '/api/failures/retry') data = FIXTURE.retry || { status: 'success', requeued: (call.body.barcodes || []).length, not_found: 0 };
        else if (path === '/api/system/review-lanes') data = FIXTURE.lanes || { status: 'success' };
        else if (path === '/api/review/explain-backfill') data = FIXTURE.backfill || { status: 'success', filled: 0, checked: 0 };
        else if (path === '/api/settings/bg-method') data = FIXTURE.bgMethod || { status: 'success', method: (call.body || {}).method, previous: 'photoroom' };
        call.resolve(response(data, FIXTURE.status && FIXTURE.status[path] || 200));
    }
    return call.promise;
};
const answer = (call, data, status = 200) => call.resolve(response(data, status));
const flush = async () => { for (let i = 0; i < 30; i++) await new Promise(r => setImmediate(r)); };
const sleep = ms => new Promise(r => setTimeout(r, ms));
const requests = path => calls.filter(c => c.url.split('?')[0] === path);
"""

HELPERS = r"""
const R = window.LaqtaReview;
const S = () => R.S;
let askAuto = true;
const asked = [];
const askNative = R.ask;
R.ask = opts => {
    const o = typeof opts === 'string' ? { title: opts } : (opts || {});
    // «العنوان: النص» (متل نص confirm القديم)، أو «العنوان؟ النص» لما العنوان سؤال
    confirms.push(o.title && o.text ? o.title + (/[؟?.:]$/.test(o.title) ? ' ' : ': ') + o.text : (o.title || o.text || ''));
    asked.push(o);
    const promise = askNative(opts);
    const answerNow = confirmAnswer;
    if (askAuto) {
        setImmediate(() => {
            const b = document.getElementById(answerNow ? 'rvAskConfirm' : 'rvAskCancel');
            if (b) b.click();
        });
    }
    return promise;
};
async function boot(config) {
    const root = document.createElement('div');
    root.setAttribute('id', 'rvApp');
    root.setAttribute('data-config', JSON.stringify(Object.assign({ mode: 'single', filter: 'all', canvas: 800, db: 'online',
                                                                     autoSearchDelayMs: 0, approveSettleMs: 0, approveUndoMs: 0,
                                                                     modeExplicit: true }, config || {})));
    document.body.appendChild(root);
    R.boot();
    await flush();
}
const itemOf = row => S().items.find(it => String(it.product.row_number) === String(row) && !it.orphan);
const listButton = row => { const it = itemOf(row); return document.querySelectorAll('.rv-item').find(b => b.getAttribute('data-key') === it.key) || null; };
function openRow(row) {
    const btn = listButton(row);
    if (btn) btn.click(); else R.single.openItem(itemOf(row).key);
}
function press(key, mods) {
    const code = /^[a-z]$/i.test(key) ? 'Key' + key.toUpperCase() : (/^[0-9]$/.test(key) ? 'Digit' + key : '');
    const target = document.activeElement || document.body;
    return dispatch(target, Object.assign({ type: 'keydown', key, code, bubbles: true }, mods || {}));
}
const ws = () => document.getElementById('rvWsBody');
const wsText = () => ws().textContent;
const productName = () => { const h = ws().querySelector('.rv-product__name'); return h ? h.textContent : null; };
const altUrls = () => ws().querySelectorAll('.rv-alt').map(b => b.getAttribute('data-url'));
const pressedAlts = () => ws().querySelectorAll('.rv-alt').map(b => b.getAttribute('aria-pressed') === 'true');
const pickUrl = () => { const p = ws().querySelector('.rv-pick'); return p ? p.getAttribute('data-url') : null; };
const approveBtn = () => document.getElementById('rvApprove');
const rejectBtn = () => document.getElementById('rvReject');
const imgSrcs = node => node.querySelectorAll('img').map(i => i.getAttribute('src'));
const jobsText = () => document.getElementById('rvJobs').textContent;
const identity = body => body && { row_number: String(body.row_number), sku_key: body.sku_key, product_name: body.product_name,
                                   brand: body.brand, barcode: body.barcode, size: body.size,
                                   product_name_ar: body.product_name_ar, brand_ar: body.brand_ar };
function chooseReason(n) { press(String(n)); }
function openNotFound() { const b = document.getElementById('rvNotFoundToggle'); if (b.getAttribute('aria-expanded') !== 'true') b.click(); }
function submitForm(form) { dispatch(form, { type: 'submit', bubbles: true }); }
const nfForms = () => ws().querySelectorAll('.rv-nf form');
const out = {};
"""


def run(scenario: str, tmp_path, fixture: dict) -> dict:
    """Run one scenario (async JS body) against the page scripts; returns the `out` object it filled."""
    shim = SHIM.replace("__FIXTURE__", json.dumps(fixture, ensure_ascii=False))
    source = shim + "\n" + scripts_source() + "\n" + HELPERS + "\n(async () => {\n" + scenario + \
        "\nconsole.log('__OUT__' + JSON.stringify(out));\n})().catch(e => { console.error(e && e.stack || e); process.exit(3); });\n"
    path = tmp_path / "review_page.js"
    path.write_text(source, encoding="utf-8")
    result = subprocess.run([NODE, str(path)], capture_output=True, text=True, timeout=90, encoding="utf-8")
    assert result.returncode == 0, result.stderr[-4000:] + result.stdout[-2000:]
    line = next(line for line in result.stdout.splitlines() if line.startswith("__OUT__"))
    return json.loads(line[len("__OUT__"):])
