"""«عبّي جدول الماركات» on the Run page (cli_bridge brand_bulk_suggestions / brand_bulk_sites / brand_bulk_add /
brand_undo), inside the «ماركات ناقصة من Brands Mapping» card.

* GET /api/run/brand-bulk only reads; a proposal without evidence leaves the server unticked, unselectable and saying
  «ما لقينا دليل كافي»; POST /api/run/brand-bulk-add sends only brand NAMES to the bridge (which writes each one's own
  proposal), /brand-bulk-sites the capped searches, /brand-undo one brand; every POST needs the CSRF token (the Laravel
  app through its HTTP kernel, stub cli_bridge);
* the card (public/js/run.js under node): «عبّي جدول الماركات» shows one checkbox row per brand with its confidence and
  evidence, strong ones ticked, weak ones not, none disabled; «اعتمد المحدد» asks first and sends the ticked names
  only; each added brand gets «تراجع».
"""

import json

from laqta_kernel import DASH, NEEDS_LARAVEL, NEEDS_NODE, bridge_calls, kernel, run_page, stub_env

BULK = {"status": "success", "rows": 9, "counts": {"high": 1, "low": 1, "none": 1}, "sites_left": 2, "site_cap": 10,
        "index": True, "brands": [
            {"brand": "MELIHA", "rows": 3, "brand_ar": "مليحة", "synonyms": ["Mleiha"], "official_domain": "meliha.ae",
             "confidence": "high", "selectable": True, "checked": True, "site_searched": True,
             "evidence": [{"kind": "reviews", "text": "اعتمدت صورتين لهالماركة بالمراجعة."},
                          {"kind": "site", "text": "موقعها الرسمي: meliha.ae."}]},
            {"brand": "SUP/T", "rows": 1, "brand_ar": "", "synonyms": ["Super Tasty"], "official_domain": "https://x.com/y",
             "confidence": "low", "selectable": True, "checked": False,
             "evidence": [{"kind": "spellings", "text": "البحث لقاها بالمتاجر مكتوبة: Super Tasty."}]},
            {"brand": "ZZQX", "rows": 1, "brand_ar": "", "synonyms": [], "official_domain": "",
             "confidence": "none", "selectable": True, "checked": True, "evidence": []},
            {"brand": "ODD", "rows": 1, "confidence": "bogus", "evidence": [{"kind": "x", "text": "شي"}]},
            {"brand": "  ", "rows": 9}]}


def _post(env, requests):
    from test_laqta_health import _kernel

    return _kernel(env, requests)


def _env(tmp_path, name, **fixture):
    folder = tmp_path / name
    folder.mkdir()
    return stub_env(folder, extra=fixture)


@NEEDS_LARAVEL
def test_the_bulk_list_only_reads_and_never_offers_a_brand_without_evidence(mariadb_or_skip, tmp_path):
    env, calls = _env(tmp_path, "list", brand_bulk_suggestions=BULK)
    (out,) = kernel(env, [["GET", "/api/run/brand-bulk"]])
    data = json.loads(out["body"])
    assert out["status"] == 200 and data["count"] == 4 and data["counts"] == {"high": 1, "low": 1, "none": 2}
    by = {b["brand"]: b for b in data["brands"]}
    assert (by["MELIHA"]["checked"], by["MELIHA"]["selectable"], by["MELIHA"]["official_domain"]) == (True, True, "meliha.ae")
    assert (by["SUP/T"]["checked"], by["SUP/T"]["selectable"], by["SUP/T"]["official_domain"]) == (False, True, "")
    for name in ("ZZQX", "ODD"):           # none, or a confidence the server does not know: never ticked
        assert (by[name]["confidence"], by[name]["checked"], by[name]["selectable"]) == ("none", False, False)
        assert by[name]["evidence"] == [{"kind": "none", "text": "ما لقينا دليل كافي"}]
    assert (data["sites_left"], data["site_cap"], data["index"]) == (2, 10, True)
    assert [c[0] for c in bridge_calls(calls)] == ["brand_bulk_suggestions"]


@NEEDS_LARAVEL
def test_the_bulk_list_says_so_when_the_bridge_cannot_read(mariadb_or_skip, tmp_path):
    env, _calls = _env(tmp_path, "broken", brand_bulk_suggestions={"status": "failed", "error": "internal detail"})
    (out,) = kernel(env, [["GET", "/api/run/brand-bulk"]])
    assert out["status"] == 502 and "internal detail" not in out["body"]


@NEEDS_LARAVEL
def test_approve_sends_only_names_and_says_what_was_written(mariadb_or_skip, tmp_path):
    answer = {"status": "success", "added": ["MELIHA"], "harvest_queued": ["meliha.ae"],
              "skipped": [{"brand": "ZZQX", "reason": "no_evidence"}, {"brand": "OLD", "reason": "gone"}]}
    env, calls = _env(tmp_path, "add", brand_bulk_add=answer)
    ok, empty, notlist, bad = _post(env, [
        ["POST", "/api/run/brand-bulk-add", {"brands": [" MELIHA ", "ZZQX", "meliha", "OLD"],
                                             "synonyms": ["evil"], "official_domains": ["evil.com"]}],
        ["POST", "/api/run/brand-bulk-add", {"brands": []}],
        ["POST", "/api/run/brand-bulk-add", {"brands": "MELIHA"}],
        ["POST", "/api/run/brand-bulk-add", {"brands": ["ok", ""]}]])
    data = json.loads(ok["body"])
    assert ok["status"] == 200 and data["added"] == ["MELIHA"] and data["harvest_queued"] == 1
    assert data["skipped"] == [{"brand": "ZZQX", "reason": "ما إلها دليل كافي"},
                               {"brand": "OLD", "reason": "صارت موجودة بـ Brands Mapping أو ما عادت بالشيت"}]
    assert data["message"].startswith("انضافت 1 ماركة لـ Brands Mapping") and "ما انكتبت 2" in data["message"]
    assert [empty["status"], notlist["status"], bad["status"]] == [422, 422, 422]
    assert bridge_calls(calls) == [["brand_bulk_add", {"brands": ["MELIHA", "ZZQX", "OLD"]}]]   # names only


@NEEDS_LARAVEL
def test_approve_says_how_far_it_got_when_the_sheet_stopped(mariadb_or_skip, tmp_path):
    env, _calls = _env(tmp_path, "half", brand_bulk_add={"status": "failed", "added": ["A"], "error": "internal detail"})
    (out,) = _post(env, [["POST", "/api/run/brand-bulk-add", {"brands": ["A", "B"]}]])
    data = json.loads(out["body"])
    assert out["status"] == 502 and data["added"] == ["A"] and "انكتبت 1 قبل ما يوقف" in data["message"]
    assert "internal detail" not in out["body"]


@NEEDS_LARAVEL
def test_undo_and_the_site_search_say_what_happened(mariadb_or_skip, tmp_path):
    env, calls = _env(tmp_path, "undo", brand_undo={"status": "invalid", "code": "changed", "error": "x"},
                      brand_bulk_sites={"status": "success", "searched": 10, "found": 4, "left": 3, "queries": 10})
    changed, blank, sites = _post(env, [["POST", "/api/run/brand-undo", {"brand": "MELIHA"}],
                                        ["POST", "/api/run/brand-undo", {"brand": " "}],
                                        ["POST", "/api/run/brand-bulk-sites", {"limit": 99}]])
    assert changed["status"] == 409 and "تغيّر بالشيت" in json.loads(changed["body"])["message"]
    assert blank["status"] == 422
    data = json.loads(sites["body"])
    assert data["message"] == "دوّرنا على موقع 10 ماركة (10 بحث)، ولقينا موقع واضح لـ 4. ضل 3: اضغط مرة تانية إذا بدك."
    assert bridge_calls(calls) == [["brand_undo", {"brand": "MELIHA"}], ["brand_bulk_sites", {}]]   # no limit from the page
    env, _c = _env(tmp_path, "undone", brand_undo={"status": "success", "brand": "MELIHA"})
    (ok,) = _post(env, [["POST", "/api/run/brand-undo", {"brand": "MELIHA"}]])
    assert ok["status"] == 200 and json.loads(ok["body"])["message"].startswith("شلنا «MELIHA» من Brands Mapping")


@NEEDS_LARAVEL
def test_the_bulk_writes_need_the_csrf_token(mariadb_or_skip, tmp_path):
    env, calls = _env(tmp_path, "csrf", brand_bulk_add={"status": "success", "added": ["X"]},
                      brand_undo={"status": "success"}, brand_bulk_sites={"status": "success"})
    out = _post(dict(env, APP_ENV="local"), [["POST", "/api/run/brand-bulk-add", {"brands": ["X"]}],
                                             ["POST", "/api/run/brand-undo", {"brand": "X"}],
                                             ["POST", "/api/run/brand-bulk-sites", {}]])
    assert [o["status"] for o in out] == [419, 419, 419] and bridge_calls(calls) == []


def test_the_bulk_routes_and_hooks_are_registered():
    routes = (DASH / "routes" / "web.php").read_text(encoding="utf-8")
    for line in ("Route::get('/api/run/brand-bulk', [RunController::class, 'brandBulk']);",
                 "Route::post('/api/run/brand-bulk-sites', [RunController::class, 'brandBulkSites']);",
                 "Route::post('/api/run/brand-bulk-add', [RunController::class, 'brandBulkAdd']);",
                 "Route::post('/api/run/brand-undo', [RunController::class, 'brandUndo']);"):
        assert line in routes
    view = (DASH / "resources" / "views" / "dashboard" / "batch_automation.blade.php").read_text(encoding="utf-8")
    for hook in ("bulk", "bulk-open", "bulk-panel", "bulk-lead", "bulk-tools", "bulk-sites", "bulk-sites-note",
                 "bulk-list", "bulk-foot", "bulk-approve", "bulk-count", "bulk-added", "bulk-error", "bulk-done"):
        assert f'data-run="{hook}"' in view
    assert "عبّي جدول الماركات" in view and "اعتمد المحدد" in view
    assert ".lq-run-bulk__evidence" in (DASH / "public" / "css" / "pages" / "run.css").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# the card (public/js/run.js under node)
# ---------------------------------------------------------------------------

CARD = r"""
const calls = [];
let reply = {};
const confirms = [];
globalThis.confirm = (t) => { confirms.push(t); return true; };
const realFetch = globalThis.fetch;
globalThis.fetch = (url, init) => {
    if (!url.startsWith('/api/run/brand')) return realFetch(url, init);
    calls.push({ url, method: init.method, body: init.body ? JSON.parse(init.body) : null });
    const r = typeof reply[url] === 'function' ? reply[url](calls[calls.length - 1]) : reply[url];
    return Promise.resolve({ ok: r.status < 400, status: r.status, json: async () => r.body });
};
reply['/api/run/brand-suggestions'] = { status: 200, body: { status: 'success', rows: 0, brands: [] } };
reply['/api/run/brand-bulk'] = { status: 200, body: { status: 'success', count: 3, sites_left: 2, site_cap: 10, brands: [
    { brand: 'MELIHA', rows: 3, brand_ar: 'مليحة', synonyms: ['Mleiha'], official_domain: 'meliha.ae', confidence: 'high',
      selectable: true, checked: true, evidence: [{ kind: 'reviews', text: 'اعتمدت صورتين لهالماركة بالمراجعة.' }] },
    { brand: 'SUP/T', rows: 1, brand_ar: '', synonyms: ['Super Tasty'], official_domain: '', confidence: 'low',
      selectable: true, checked: false, evidence: [{ kind: 'spellings', text: 'البحث لقاها بالمتاجر مكتوبة: Super Tasty.' }] },
    { brand: 'ZZQX', rows: 1, brand_ar: '', synonyms: [], official_domain: '', confidence: 'none', selectable: false,
      checked: false, evidence: [{ kind: 'none', text: 'ما لقينا دليل كافي' }] } ] } };
island.textContent = 'null';
LaqtaRunPage.mount(document);
const rowOf = (i) => hooks['bulk-list'].children[i];
const part = (row) => ({ box: row.children[0], name: row.children[1].children[0].children[0].textContent,
    chip: row.children[1].children[0].children[1].textContent, meta: row.children[1].children[1].textContent,
    evidence: row.children[1].children[2].children.map(li => li.textContent) });
"""


@NEEDS_NODE
def test_the_bulk_card_ticks_strong_proposals_and_never_one_without_evidence():
    out = run_page(CARD + r"""
(async () => {
    await wait(30);
    const before = calls.length;
    hooks['bulk-open'].fire('click');
    await wait(30);
    const a = part(rowOf(0)), b = part(rowOf(1)), c = part(rowOf(2));
    console.log(JSON.stringify({
        before, lead: hooks['bulk-lead'].textContent, panelHidden: hooks['bulk-panel'].hidden,
        rows: hooks['bulk-list'].children.length, count: hooks['bulk-count'].textContent,
        approveDisabled: hooks['bulk-approve'].disabled, toolsHidden: hooks['bulk-tools'].hidden,
        note: hooks['bulk-sites-note'].textContent,
        a: { name: a.name, chip: a.chip, checked: a.box.checked, disabled: a.box.disabled, meta: a.meta, evidence: a.evidence },
        b: { chip: b.chip, checked: b.box.checked, disabled: b.box.disabled },
        c: { chip: c.chip, checked: c.box.checked, disabled: c.box.disabled, evidence: c.evidence },
        gets: calls.filter(x => x.url === '/api/run/brand-bulk').length, posts: calls.filter(x => x.method === 'POST').length }));
    process.exit(0);
})();
""")
    assert out["before"] == 1                                          # the card's own list; the bulk list waits for the click
    assert out["panelHidden"] is False and out["rows"] == 3 and out["gets"] == 1 and out["posts"] == 0
    assert out["lead"].startswith("لقينا 3 ماركة ناقصة: 1 إلها دليل قوي") and "ما منكتب ماركة بلا دليل" in out["lead"]
    assert out["a"] == {"name": "MELIHA", "chip": "دليل قوي", "checked": True, "disabled": False,
                        "meta": "3 صفوفمرادفات: مليحة، Mleihaالموقع: meliha.ae",
                        "evidence": ["اعتمدت صورتين لهالماركة بالمراجعة."]}
    assert out["b"] == {"chip": "دليل ضعيف", "checked": False, "disabled": False}
    assert out["c"] == {"chip": "ما في دليل", "checked": False, "disabled": True, "evidence": ["ما لقينا دليل كافي"]}
    assert out["count"] == "اخترت ماركة وحدة" and out["approveDisabled"] is False
    assert out["toolsHidden"] is False and "ضل 2 ماركة" in out["note"]


@NEEDS_NODE
def test_approve_sends_the_ticked_names_after_asking_and_offers_undo():
    out = run_page(CARD + r"""
reply['/api/run/brand-bulk-add'] = { status: 200, body: { status: 'success', added: ['MELIHA', 'SUP/T'], skipped: [],
    message: 'انضافت 2 ماركة لـ Brands Mapping. التشغيل الجاي بيعرفها.' } };
reply['/api/run/brand-undo'] = { status: 200, body: { status: 'success', brand: 'MELIHA', message: 'شلنا «MELIHA» من Brands Mapping. التشغيل الجاي ما بيعرفها.' } };
(async () => {
    await wait(30);
    hooks['bulk-open'].fire('click');
    await wait(30);
    const b = part(rowOf(1)), c = part(rowOf(2));
    b.box.checked = true; b.box.fire('change');                   // the owner ticks the weak one
    c.box.checked = true; c.box.fire('change');                   // a disabled box never counts
    const count = hooks['bulk-count'].textContent;
    hooks['bulk-approve'].fire('click');
    await wait(60);
    const added = hooks['bulk-added'].children.map(l => [l.children[0].textContent, l.children[1].textContent]);
    hooks['bulk-added'].children[0].children[1].fire('click');
    await wait(60);
    console.log(JSON.stringify({
        count, posts: calls.filter(x => x.method === 'POST').map(x => [x.url, x.body]), added,
        done: hooks['bulk-done'].textContent, confirms: confirms.length, firstConfirm: confirms[0] }));
    process.exit(0);
})();
""")
    assert out["count"] == "اخترت ماركتين"
    assert out["posts"] == [["/api/run/brand-bulk-add", {"brands": ["MELIHA", "SUP/T"]}],
                            ["/api/run/brand-undo", {"brand": "MELIHA"}]]
    assert out["added"] == [["MELIHA", "تراجع"], ["SUP/T", "تراجع"]]
    assert out["confirms"] == 2 and "(2)" in out["firstConfirm"] and "تتراجع" in out["firstConfirm"]
    assert out["done"].startswith("شلنا «MELIHA»")


@NEEDS_NODE
def test_the_bulk_helpers_are_pure():
    out = run_page(r"""
const P = LaqtaRunPage;
console.log(JSON.stringify({
    err: P.describeBulk({ ok: false, status: 502, data: { status: 'error', message: 'ما قدرنا نجهّز اقتراحات الماركات هلق. جرّب بعد شوي.' } }),
    clean: P.describeBulk({ ok: true, status: 200, data: { status: 'success', brands: [] } }).lead,
    body: P.bulkApproveBody([{ brand: 'A', selectable: true, checked: true }, { brand: 'B', selectable: false, checked: true },
                             { brand: 'C', selectable: true, checked: false }]),
    counts: [0, 1, 2, 3, 11].map(P.bulkCountText),
    failed: P.bulkResult({ ok: false, status: 502, data: { status: 'error', added: ['A'], message: 'ما قدرنا نكتب بورقة Brands Mapping هلق. جرّب بعد شوي. انكتبت 1 قبل ما يوقف، والباقي ما انكتب.' } }),
    network: P.bulkResult({ ok: false, status: 0, data: null }).text, sites: P.BULK_SITES_CONFIRM_TEXT }));
process.exit(0);
""")
    assert out["err"]["state"] == "error" and out["err"]["rows"] == [] and out["err"]["lead"].startswith("ما قدرنا نجهّز")
    assert out["clean"] == "كل ماركات الشيت موجودة بـ Brands Mapping."
    assert out["body"] == {"brands": ["A"]}
    assert out["counts"] == ["ما اخترت ولا ماركة", "اخترت ماركة وحدة", "اخترت ماركتين", "اخترت 3 ماركات", "اخترت 11 ماركة"]
    assert out["failed"]["ok"] is False and out["failed"]["added"] == ["A"] and "قبل ما يوقف" in out["failed"]["text"]
    assert out["network"] == "ما قدرنا نوصل للخادم. ما انكتب شي."
    assert "لحد 10 بحث" in out["sites"] and "ما بينكتب شي بالشيت" in out["sites"]
