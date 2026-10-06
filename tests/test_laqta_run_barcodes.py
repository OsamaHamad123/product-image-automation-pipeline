"""«باركودات لقيناها من صفحات المتاجر» on the Run page (cli_bridge barcode_suggestions / barcode_write).

* GET /api/run/barcode-suggestions only reads (the bridge's barcode_suggestions) and keeps what the card shows;
  POST /api/run/barcode-write checks the rows (1 to 300, a sheet row, a key, 8 to 14 digits, no row twice) before the
  bridge writes, needs the CSRF token like every POST, and says what happened in Levantine Arabic; a sheet without a
  barcode column is refused with a clear message (the Laravel app through its HTTP kernel, stub cli_bridge);
* the catalog keeps the approval and the rejected images of a row that got its barcode after the approval (stored
  under the key without the barcode: get_products alt_sku_key);
* the card lists the rows ticked, the shared barcodes and filled cells apart without a live checkbox, asks with the
  owner's confirm text and sends only the ticked rows (public/js/run.js under node).
"""

import json

from laqta_kernel import DASH, NEEDS_LARAVEL, NEEDS_NODE, bridge_calls, kernel, run_page, sql, stub_env

GTIN, OTHER = "6281007035323", "6281007035330"
ROW_A = {"row": 12, "sku_key": "pbw-key-a", "name": "GOLDEN PRIZE TUNA 185G", "brand": "GOLDEN PRIZE", "size": "185G",
         "gtin": GTIN, "domain": "carrefouruae.com", "page_url": "https://www.carrefouruae.com/p/1", "sheet_barcode": "",
         "duplicate": False, "reason": None}
SUGGESTIONS = {"status": "success", "source": "sheet_cache", "count": 4, "rows": [ROW_A, dict(ROW_A, row=1), dict(ROW_A, gtin="12ab")],
               "duplicates": [dict(ROW_A, row=14, sku_key="pbw-key-b", gtin=OTHER, duplicate=True, reason="duplicate")],
               "filled": [dict(ROW_A, row=15, sku_key="pbw-key-c", sheet_barcode="N/A", reason="cell_not_empty")]}


def _post(env, requests):
    from test_laqta_health import _kernel

    return _kernel(env, requests)


def _env(tmp_path, name, products=None, **fixture):
    folder = tmp_path / name
    folder.mkdir()
    return stub_env(folder, extra=fixture, **({"products": products} if products is not None else {}))


# ---------------------------------------------------------------------------
# the routes
# ---------------------------------------------------------------------------

@NEEDS_LARAVEL
def test_the_list_only_reads_and_keeps_what_the_card_shows(mariadb_or_skip, tmp_path):
    env, calls = _env(tmp_path, "list", barcode_suggestions=SUGGESTIONS)
    (out,) = kernel(env, [["GET", "/api/run/barcode-suggestions"]])
    data = json.loads(out["body"])
    assert out["status"] == 200 and (data["status"], data["count"]) == ("success", 3)
    assert data["rows"] == [{"row": 12, "sku_key": "pbw-key-a", "name": "GOLDEN PRIZE TUNA 185G", "brand": "GOLDEN PRIZE",
                             "gtin": GTIN, "domain": "carrefouruae.com", "duplicate": False, "sheet_barcode": ""}]
    assert [r["row"] for r in data["duplicates"]] == [14] and data["duplicates"][0]["duplicate"] is True
    assert [(r["row"], r["sheet_barcode"]) for r in data["filled"]] == [(15, "N/A")]
    assert [c[0] for c in bridge_calls(calls)] == ["barcode_suggestions"]


@NEEDS_LARAVEL
def test_the_write_checks_the_rows_first_and_says_what_happened(mariadb_or_skip, tmp_path):
    result = {"status": "success", "written": 2, "queued": 1, "rows_written": [12, 13],
              "skipped": [{"row": 15, "reason": "cell_not_empty"}, {"row": 16, "reason": "row_changed"},
                          {"row": 17, "reason": "evil"}]}
    env, calls = _env(tmp_path, "write", barcode_write=result)
    good = {"items": [{"row": 12, "sku_key": " pbw-key-a ", "gtin": GTIN}, {"row": "13", "sku_key": "pbw-key-d", "gtin": OTHER}]}
    bad = [{}, {"items": []}, {"items": [{"row": 1, "sku_key": "k", "gtin": GTIN}]},
           {"items": [{"row": 12, "sku_key": "", "gtin": GTIN}]},
           {"items": [{"row": 12, "sku_key": "k", "gtin": GTIN}, {"row": 12, "sku_key": "k2", "gtin": GTIN}]},
           {"items": [{"row": 12, "sku_key": "k", "gtin": "62810070353x"}]},
           {"items": [{"row": 12 + i, "sku_key": "k", "gtin": GTIN} for i in range(301)]}]
    out = _post(env, [["POST", "/api/run/barcode-write", good]] + [["POST", "/api/run/barcode-write", b] for b in bad])
    data = json.loads(out[0]["body"])
    assert out[0]["status"] == 200 and (data["written"], data["queued"]) == (2, 1)
    assert data["message"] == ("انكتب الباركود بصفين بالشيت. صف واحد بالطابور: رح ينكتبوا أول ما يرد الشيت. "
                               "ما كتبنا بـ 3 صفوف: خلية الباركود مش فاضية (صف 15)؛ الصف تغيّر أو انتقل بالشيت (صف 16)؛ "
                               "الشيت رفض الكتابة (صف 17).")
    assert [o["status"] for o in out[1:]] == [422] * len(bad)
    assert [json.loads(o["body"])["code"] for o in out[1:]] == ["bad_items"] * 5 + ["bad_gtin", "bad_items"]
    assert bridge_calls(calls) == [["barcode_write", {"items": [{"row": 12, "sku_key": "pbw-key-a", "gtin": GTIN},
                                                               {"row": 13, "sku_key": "pbw-key-d", "gtin": OTHER}]}]]


@NEEDS_LARAVEL
def test_a_sheet_without_a_barcode_column_or_a_failed_bridge_says_so(mariadb_or_skip, tmp_path):
    item = {"items": [{"row": 12, "sku_key": "pbw-key-a", "gtin": GTIN}]}
    env, _calls = _env(tmp_path, "nocol", barcode_write={"status": "refused", "code": "no_barcode_column", "error": "x"})
    (out,) = _post(env, [["POST", "/api/run/barcode-write", item]])
    assert out["status"] == 409
    assert json.loads(out["body"])["message"] == ("الشيت ما فيه عمود باركود (Barcode أو EAN أو GTIN). ما كتبنا شي، "
                                                  "وما منضيف أعمدة للشيت.")
    env, _calls = _env(tmp_path, "broken", barcode_write={"status": "failed", "error": "internal detail"})
    (out,) = _post(env, [["POST", "/api/run/barcode-write", item]])
    assert out["status"] == 502 and "internal detail" not in out["body"]


@NEEDS_LARAVEL
def test_the_write_needs_the_csrf_token_like_every_post(mariadb_or_skip, tmp_path):
    env, calls = _env(tmp_path, "csrf", barcode_write={"status": "success", "written": 1})
    (out,) = _post(dict(env, APP_ENV="local"), [["POST", "/api/run/barcode-write",
                                                 {"items": [{"row": 12, "sku_key": "k", "gtin": GTIN}]}]])
    assert out["status"] == 419 and bridge_calls(calls) == []


@NEEDS_LARAVEL
def test_the_catalog_finds_the_approval_and_rejections_of_a_row_that_got_its_barcode(mariadb_or_skip, tmp_path):
    db = mariadb_or_skip
    old, new = "pbw-fingerprint-1", "0" + GTIN
    rejected = "https://shop.example/pbw-wrong.jpg"
    sql(db, "DELETE FROM resolved_products WHERE sku_key = %s", (old,))
    sql(db, "DELETE FROM rejected_images WHERE sku_key = %s", (old,))
    try:
        db.save_product_resolution("", "PBW TUNA 185G", "PBW", "https://a.ae/1.jpg", "https://res.cloudinary.com/pbw/1.png",
                                   verification_status="human_approved", approved_by="human", sku_key=old)
        db.add_rejected_image(old, rejected)
        products = [{"row_number": 12, "product_name": "PBW TUNA 185G", "brand": "PBW", "barcode": GTIN, "sku_key": new,
                     "alt_sku_key": old}]
        env, _calls = _env(tmp_path, "catalog", products=products)
        (out,) = kernel(env, [["GET", "/api/products-json?refresh=true"]])
        (prod,) = [p for p in json.loads(out["body"])["products"] if p["row_number"] == 12]
        assert prod["cached_image"] == "https://res.cloudinary.com/pbw/1.png"
        assert prod["verification_status"] == "human_approved"
        assert [r["url"] for r in prod["rejected_images"]] == [rejected]
    finally:
        sql(db, "DELETE FROM resolved_products WHERE sku_key = %s", (old,))
        sql(db, "DELETE FROM rejected_images WHERE sku_key = %s", (old,))


def test_the_routes_and_the_card_are_registered():
    routes = (DASH / "routes" / "web.php").read_text(encoding="utf-8")
    for line in ("Route::get('/api/run/barcode-suggestions', [RunController::class, 'barcodeSuggestions']);",
                 "Route::post('/api/run/barcode-write', [RunController::class, 'barcodeWrite']);"):
        assert line in routes
    view = (DASH / "resources" / "views" / "dashboard" / "batch_automation.blade.php").read_text(encoding="utf-8")
    for hook in ("barcodes", "barcodes-title", "barcodes-lead", "barcodes-list", "barcodes-apart", "barcodes-foot",
                 "barcodes-write", "barcodes-count", "barcodes-refresh", "barcodes-error", "barcodes-done"):
        assert f'data-run="{hook}"' in view
    assert "اكتب الباركودات المختارة بالشيت" in view and "باركودات لقيناها من صفحات المتاجر" in view
    assert ".lq-run-barcode__gtin" in (DASH / "public" / "css" / "pages" / "run.css").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# the card (public/js/run.js under node)
# ---------------------------------------------------------------------------

CARD = r"""
const calls = [];
let reply = {};
let confirmed = [];
let answer = true;
globalThis.confirm = (text) => { confirmed.push(text); return answer; };
const realFetch = globalThis.fetch;
globalThis.fetch = (url, init) => {
    if (!url.startsWith('/api/run/barcode')) return realFetch(url, init);
    calls.push({ url, method: init.method, body: init.body ? JSON.parse(init.body) : null });
    const r = typeof reply[url] === 'function' ? reply[url](calls[calls.length - 1]) : reply[url];
    return Promise.resolve({ ok: r.status < 400, status: r.status, json: async () => r.body });
};
const row = (n, sku, gtin, extra) => Object.assign({ row: n, sku_key: sku, name: 'TUNA ' + n, brand: 'GOLDEN PRIZE',
    gtin: gtin, domain: 'carrefouruae.com', duplicate: false, sheet_barcode: '' }, extra || {});
let lists = [{ status: 'success', count: 4, rows: [row(12, 'k12', '6281007035323'), row(13, 'k13', '6281007035347')],
               duplicates: [row(14, 'k14', '6281007035330', { duplicate: true })],
               filled: [row(15, 'k15', '6281007035354', { sheet_barcode: 'N/A' })] }];
reply['/api/run/barcode-suggestions'] = () => ({ status: 200, body: lists[lists.length - 1] });
island.textContent = 'null';
LaqtaRunPage.mount(document);
const card = (el) => ({ row: el.getAttribute('data-row'), checked: el.children[0].checked, disabled: el.children[0].disabled,
    name: el.children[1].children[0].textContent, gtin: el.children[1].children[1].children[0].textContent,
    store: el.children[1].children[1].children[1].textContent,
    note: el.children[1].children[2] ? el.children[1].children[2].textContent : '' });
"""


@NEEDS_NODE
def test_the_card_lists_the_rows_ticked_and_the_duplicates_apart():
    out = run_page(CARD + r"""
(async () => {
    await wait(60);
    console.log(JSON.stringify({
        title: hooks['barcodes-title'].textContent, state: hooks.barcodes.getAttribute('data-state'),
        rows: hooks['barcodes-list'].children.map(card),
        apartTitle: hooks['barcodes-apart'].children[0].textContent, apart: hooks['barcodes-apart'].children.slice(1).map(card),
        apartHidden: hooks['barcodes-apart'].hidden, foot: hooks['barcodes-foot'].hidden,
        count: hooks['barcodes-count'].textContent, write: hooks['barcodes-write'].disabled,
        get: calls.map(c => [c.method, c.url]) }));
    process.exit(0);
})();
""")
    assert out["title"] == "باركودات لقيناها من صفحات المتاجر (4)" and out["state"] == "ready"
    assert out["rows"] == [
        {"row": "12", "checked": True, "disabled": False, "name": "TUNA 12", "gtin": "6281007035323",
         "store": "من carrefouruae.com", "note": ""},
        {"row": "13", "checked": True, "disabled": False, "name": "TUNA 13", "gtin": "6281007035347",
         "store": "من carrefouruae.com", "note": ""}]
    assert out["apartTitle"] == "ما منكتبها (2)" and out["apartHidden"] is False
    assert [(a["row"], a["checked"], a["disabled"]) for a in out["apart"]] == [("14", False, True), ("15", False, True)]
    assert out["apart"][0]["note"].startswith("نفس الباركود لأكتر من صف")
    assert out["apart"][1]["note"] == "خلية الباركود فيها «N/A»: ما منكتب فوقها."
    assert out["foot"] is False and out["count"] == "اخترت صفين" and out["write"] is False
    assert out["get"] == [["GET", "/api/run/barcode-suggestions"]]               # the list reads; nothing was written


@NEEDS_NODE
def test_the_write_asks_first_sends_only_the_ticked_rows_and_shows_the_result():
    out = run_page(CARD + r"""
reply['/api/run/barcode-write'] = { status: 200, body: { status: 'success', written: 1, queued: 0, skipped: [],
    message: 'انكتب الباركود بصف واحد بالشيت.' } };
(async () => {
    await wait(60);
    const second = hooks['barcodes-list'].children[1].children[0];
    second.checked = false;
    second.fire('change');
    const count = hooks['barcodes-count'].textContent;
    answer = false;
    hooks['barcodes-write'].fire('click');                 // «لا»: nothing is sent
    await wait(20);
    const before = calls.filter(c => c.method === 'POST').length;
    answer = true;
    lists.push({ status: 'success', count: 0, rows: [], duplicates: [], filled: [] });
    hooks['barcodes-write'].fire('click');
    await wait(60);
    console.log(JSON.stringify({ count, before, confirmed,
        posts: calls.filter(c => c.method === 'POST'), done: hooks['barcodes-done'].textContent,
        doneHidden: hooks['barcodes-done'].hidden, toast: domToasts,
        title: hooks['barcodes-title'].textContent, lead: hooks['barcodes-lead'].textContent }));
    process.exit(0);
})();
""")
    assert out["count"] == "اخترت صف واحد" and out["before"] == 0
    assert out["confirmed"] == ["رح ننكتب الباركود بعمود الباركود للصفوف المختارة بس، وما منغيّر أي خلية فيها باركود"] * 2
    assert out["posts"] == [{"url": "/api/run/barcode-write", "method": "POST",
                             "body": {"items": [{"row": 12, "sku_key": "k12", "gtin": "6281007035323"}]}}]
    assert out["done"] == "انكتب الباركود بصف واحد بالشيت." and out["doneHidden"] is False
    assert out["toast"][-1] == ["انكتب الباركود بصف واحد بالشيت.", "success"]
    assert out["title"] == "باركودات لقيناها من صفحات المتاجر (0)"           # the list was read again
    assert out["lead"] == "ما في باركودات جديدة من صفحات المتاجر لصفوف بلا باركود."


@NEEDS_NODE
def test_a_refused_write_keeps_the_rows_and_says_why():
    out = run_page(CARD + r"""
reply['/api/run/barcode-write'] = { status: 409, body: { status: 'error', code: 'no_barcode_column',
    message: 'الشيت ما فيه عمود باركود (Barcode أو EAN أو GTIN). ما كتبنا شي، وما منضيف أعمدة للشيت.' } };
(async () => {
    await wait(60);
    hooks['barcodes-write'].fire('click');
    await wait(40);
    console.log(JSON.stringify({ error: hooks['barcodes-error'].textContent, hidden: hooks['barcodes-error'].hidden,
        rows: hooks['barcodes-list'].children.length, write: hooks['barcodes-write'].disabled,
        views: [LaqtaRunPage.describeBarcodes({ ok: false, status: 502, data: { status: 'error' } }).lead,
                LaqtaRunPage.barcodeResult({ ok: false, status: 0, data: null }).text,
                LaqtaRunPage.barcodeWriteBody([{ row: 2, sku_key: 'a', gtin: '1', checked: true, note: '' },
                                              { row: 3, sku_key: 'b', gtin: '2', checked: true, note: 'x' }]).items.length] }));
    process.exit(0);
})();
""")
    assert out["error"].startswith("الشيت ما فيه عمود باركود") and out["hidden"] is False
    assert out["rows"] == 2 and out["write"] is False
    assert out["views"] == ["ما قدرنا نقرأ الباركودات هلق.", "ما قدرنا نوصل للخادم. ما انكتب شي.", 1]
