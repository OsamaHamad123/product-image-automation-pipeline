"""«ماركات ناقصة من Brands Mapping» on the Run page (cli_bridge brand_suggestions / brand_official_site / brand_add).

* GET /api/run/brand-suggestions only reads (the bridge's brand_suggestions); POST /api/run/brand-official-site sends
  the one search the owner asked for; POST /api/run/brand-add and /brand-add-all write one row per brand after the
  server has checked the brand, at most ten synonyms and that every site is a bare host, and they need the CSRF token
  like every POST (the Laravel app through its HTTP kernel, stub cli_bridge);
* the card lists each missing brand with its row count, a synonyms field prefilled with the Arabic name and the store
  spellings, an official-site field, «اقترح الموقع الرسمي» (one search) and «أضف»; «أضف الكل بدون مواقع» asks first
  and sends no site; success says «انضافت الماركة. التشغيل الجاي بيعرفها.» (public/js/run.js under node).
"""

import json

from laqta_kernel import DASH, NEEDS_LARAVEL, NEEDS_NODE, bridge_calls, kernel, run_page, stub_env

ADDED = "انضافت الماركة. التشغيل الجاي بيعرفها."
SUGGESTIONS = {"status": "success", "rows": 12, "brands": [
    {"brand": "MELIHA", "rows": 5, "brand_ar": "مليحة", "synonyms": ["Mleiha", "Maliha"], "official_domain": ""},
    {"brand": "SABA SANABEL", "rows": 2, "brand_ar": "", "synonyms": [], "official_domain": "", "evil": "<b>x</b>"},
    {"brand": "  ", "rows": 9, "synonyms": []},
]}


def _post(env, requests):
    from test_laqta_health import _kernel

    return _kernel(env, requests)


def _env(tmp_path, name, **fixture):
    folder = tmp_path / name
    folder.mkdir()
    env, calls = stub_env(folder, extra=fixture)
    return env, calls


# ---------------------------------------------------------------------------
# the routes
# ---------------------------------------------------------------------------

@NEEDS_LARAVEL
def test_the_list_only_reads_and_drops_what_the_card_does_not_use(mariadb_or_skip, tmp_path):
    env, calls = _env(tmp_path, "list", brand_suggestions=SUGGESTIONS)
    (out,) = kernel(env, [["GET", "/api/run/brand-suggestions"]])
    data = json.loads(out["body"])
    assert out["status"] == 200 and (data["status"], data["count"], data["rows"]) == ("success", 2, 12)
    assert data["brands"][0] == {"brand": "MELIHA", "rows": 5, "brand_ar": "مليحة", "synonyms": ["Mleiha", "Maliha"],
                                 "official_domain": ""}
    assert "evil" not in data["brands"][1]
    assert [c[0] for c in bridge_calls(calls)] == ["brand_suggestions"]


@NEEDS_LARAVEL
def test_the_list_says_so_when_the_bridge_cannot_read(mariadb_or_skip, tmp_path):
    env, _calls = _env(tmp_path, "broken", brand_suggestions={"status": "failed", "error": "internal detail"})
    (out,) = kernel(env, [["GET", "/api/run/brand-suggestions"]])
    assert out["status"] == 502 and "internal detail" not in out["body"]
    assert json.loads(out["body"])["message"] == "ما قدرنا نقرأ الماركات الناقصة هلق. جرّب بعد شوي."


@NEEDS_LARAVEL
def test_the_official_site_is_one_bridge_call_with_up_to_two_candidates(mariadb_or_skip, tmp_path):
    answer = {"status": "success", "brand": "MELIHA", "queries": 1, "candidates": [
        {"title": "Meliha | Official", "domain": "meliha.ae", "url": "https://meliha.ae/"},
        {"title": "Meliha dairy", "domain": "www.meliha.com", "url": "https://www.meliha.com/"},
        {"title": "third", "domain": "third.com", "url": "https://third.com/"},
        {"title": "bad", "domain": "https://x.com/y"}]}
    env, calls = _env(tmp_path, "site", brand_official_site=answer)
    ok, blank, long_name = _post(env, [
        ["POST", "/api/run/brand-official-site", {"brand": " MELIHA "}],
        ["POST", "/api/run/brand-official-site", {"brand": "  "}],
        ["POST", "/api/run/brand-official-site", {"brand": "x" * 101}]])
    data = json.loads(ok["body"])
    assert ok["status"] == 200 and [c["domain"] for c in data["candidates"]] == ["meliha.ae", "www.meliha.com"]
    assert blank["status"] == 422 and long_name["status"] == 422
    assert bridge_calls(calls) == [["brand_official_site", {"brand": "MELIHA"}]]      # the refused ones cost nothing


@NEEDS_LARAVEL
def test_the_official_site_without_a_key_or_an_answer_says_so(mariadb_or_skip, tmp_path):
    env, _c = _env(tmp_path, "nokey", brand_official_site={"status": "unavailable", "code": "no_key", "error": "x"})
    (nokey,) = _post(env, [["POST", "/api/run/brand-official-site", {"brand": "MELIHA"}]])
    assert nokey["status"] == 503
    env, _c = _env(tmp_path, "down", brand_official_site={"status": "failed", "error": "internal detail"})
    (down,) = _post(env, [["POST", "/api/run/brand-official-site", {"brand": "MELIHA"}]])
    assert down["status"] == 502 and "internal detail" not in down["body"]
    env, _c = _env(tmp_path, "none", brand_official_site={"status": "success", "candidates": []})
    (none,) = _post(env, [["POST", "/api/run/brand-official-site", {"brand": "MELIHA"}]])
    assert json.loads(none["body"])["candidates"] == [] and "بإيدك" in json.loads(none["body"])["message"]


@NEEDS_LARAVEL
def test_add_sends_one_checked_row_to_the_bridge(mariadb_or_skip, tmp_path):
    env, calls = _env(tmp_path, "add", brand_add={"status": "success", "added": ["MELIHA"], "skipped": [],
                                                  "harvest_queued": ["meliha.ae"]})
    (out,) = _post(env, [["POST", "/api/run/brand-add", {
        "brand": " MELIHA ", "synonyms": "مليحة، Mleiha, mleiha,\nMeliha,  Maliha ", "official_domains": [" Meliha.AE "]}]])
    data = json.loads(out["body"])
    assert out["status"] == 200 and data["status"] == "success" and data["message"] == ADDED
    assert data["added"] == ["MELIHA"] and data["harvest_queued"] == 1
    assert bridge_calls(calls) == [["brand_add", {"brand": "MELIHA", "synonyms": ["مليحة", "Mleiha", "Maliha"],
                                                  "official_domains": ["meliha.ae"]}]]   # the brand itself and repeats dropped


@NEEDS_LARAVEL
def test_add_refuses_what_the_server_can_see_is_wrong_before_calling_the_bridge(mariadb_or_skip, tmp_path):
    env, calls = _env(tmp_path, "bad", brand_add={"status": "success", "added": ["X"]})
    bad = [
        {"brand": ""}, {"brand": "x" * 101}, {"brand": "a\x07b"}, {"brand": ["list"]},
        {"brand": "Meliha", "official_domains": ["https://meliha.ae"]},
        {"brand": "Meliha", "official_domains": ["meliha.ae/shop"]},
        {"brand": "Meliha", "official_domains": ["meliha"]},
        {"brand": "Meliha", "official_domains": "a.com, b.com, c.com, d.com"},
        {"brand": "Meliha", "synonyms": [f"s{i}" for i in range(11)]},
        {"brand": "Meliha", "synonyms": ["y" * 81]},
    ]
    out = _post(env, [["POST", "/api/run/brand-add", params] for params in bad])
    assert [o["status"] for o in out] == [422] * len(bad)
    codes = [json.loads(o["body"])["code"] for o in out]
    assert codes == ["invalid_brand"] * 4 + ["invalid_domain"] * 4 + ["too_many_synonyms", "invalid_synonym"]
    assert all(json.loads(o["body"])["message"] for o in out)
    assert bridge_calls(calls) == []                                               # nothing reached the sheet


@NEEDS_LARAVEL
def test_add_says_what_the_bridge_refused_or_could_not_do(mariadb_or_skip, tmp_path):
    expected = {"duplicate": ("duplicate", 409), "invalid": ("blocked_domain", 422), "failed": (None, 502)}
    for status, (code, http) in expected.items():
        answer = {"status": status, "code": code, "error": "internal detail", "skipped": []}
        env, _calls = _env(tmp_path, status, brand_add=answer)
        (out,) = _post(env, [["POST", "/api/run/brand-add", {"brand": "Meliha"}]])
        assert out["status"] == http and "internal detail" not in out["body"], status
        assert json.loads(out["body"])["status"] == "error"
        if code:
            assert json.loads(out["body"])["code"] == code


@NEEDS_LARAVEL
def test_add_all_sends_every_brand_with_its_synonyms_and_never_a_site(mariadb_or_skip, tmp_path):
    env, calls = _env(tmp_path, "all", brand_add={"status": "success", "added": ["A", "B"], "skipped": [{"brand": "C"}]})
    ok, empty, bad, big = _post(env, [
        ["POST", "/api/run/brand-add-all", {"items": [
            {"brand": "A", "synonyms": ["a1", "a2"], "official_domains": ["a.com"]},
            {"brand": "B", "synonyms": "b1، b2"}, {"brand": "C"}]}],
        ["POST", "/api/run/brand-add-all", {"items": []}],
        ["POST", "/api/run/brand-add-all", {"items": [{"brand": "A"}, {"brand": ""}]}],
        ["POST", "/api/run/brand-add-all", {"items": [{"brand": f"B{i}"} for i in range(201)]}]])
    data = json.loads(ok["body"])
    assert ok["status"] == 200 and data["added"] == ["A", "B"] and data["skipped"] == 1 and data["message"] == ADDED
    assert [empty["status"], bad["status"], big["status"]] == [422, 422, 422]
    assert bridge_calls(calls) == [["brand_add", {"items": [
        {"brand": "A", "synonyms": ["a1", "a2"], "official_domains": []},
        {"brand": "B", "synonyms": ["b1", "b2"], "official_domains": []},
        {"brand": "C", "synonyms": [], "official_domains": []}]}]]                  # a site in the request is ignored


@NEEDS_LARAVEL
def test_the_writing_routes_need_the_csrf_token_like_every_post(mariadb_or_skip, tmp_path):
    env, calls = _env(tmp_path, "csrf", brand_add={"status": "success", "added": ["X"]},
                      brand_official_site={"status": "success", "candidates": []})
    local = dict(env, APP_ENV="local")
    out = _post(local, [["POST", "/api/run/brand-add", {"brand": "Meliha"}],
                        ["POST", "/api/run/brand-add-all", {"items": [{"brand": "Meliha"}]}],
                        ["POST", "/api/run/brand-official-site", {"brand": "Meliha"}]])
    assert [o["status"] for o in out] == [419, 419, 419]
    assert bridge_calls(calls) == []


def test_the_routes_and_the_card_are_registered():
    routes = (DASH / "routes" / "web.php").read_text(encoding="utf-8")
    for line in ("Route::get('/api/run/brand-suggestions', [RunController::class, 'brandSuggestions']);",
                 "Route::post('/api/run/brand-official-site', [RunController::class, 'brandOfficialSite']);",
                 "Route::post('/api/run/brand-add', [RunController::class, 'brandAdd']);",
                 "Route::post('/api/run/brand-add-all', [RunController::class, 'brandAddAll']);"):
        assert line in routes
    assert "validateCsrfTokens" not in (DASH / "bootstrap" / "app.php").read_text(encoding="utf-8")
    view = (DASH / "resources" / "views" / "dashboard" / "batch_automation.blade.php").read_text(encoding="utf-8")
    for hook in ("brands", "brands-title", "brands-lead", "brands-list", "brands-foot", "brands-add-all", "brands-refresh",
                 "brands-error", "brands-done"):
        assert f'data-run="{hook}"' in view
    assert "أضف الكل بدون مواقع" in view and "ماركات ناقصة من جدول الماركات" in view
    assert ".lq-run-brand__actions" in (DASH / "public" / "css" / "pages" / "run.css").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# the card (public/js/run.js under node)
# ---------------------------------------------------------------------------

CARD = r"""
const calls = [];
let reply = {};
const realFetch = globalThis.fetch;
globalThis.fetch = (url, init) => {
    if (!url.startsWith('/api/run/brand')) return realFetch(url, init);
    calls.push({ url, method: init.method, body: init.body ? JSON.parse(init.body) : null });
    const r = typeof reply[url] === 'function' ? reply[url](calls[calls.length - 1]) : reply[url];
    return Promise.resolve({ ok: r.status < 400, status: r.status, json: async () => r.body });
};
reply['/api/run/brand-suggestions'] = { status: 200, body: { status: 'success', rows: 9, brands: [
    { brand: 'MELIHA', rows: 5, brand_ar: 'مليحة', synonyms: ['Mleiha', 'meliha', 'Maliha'], official_domain: '' },
    { brand: 'SABA SANABEL', rows: 1, brand_ar: '', synonyms: [], official_domain: '' } ] } };
island.textContent = 'null';
LaqtaRunPage.mount(document);
const rowOf = (i) => hooks['brands-list'].children[i];
const part = (row) => ({ name: row.children[0].children[0].textContent, rows: row.children[0].children[1].textContent,
    syn: row.children[1].children[1], site: row.children[2].children[1], chips: row.children[3],
    suggest: row.children[4].children[0], cost: row.children[4].children[1].textContent, add: row.children[4].children[2],
    error: row.children[5] });
"""


@NEEDS_NODE
def test_the_card_lists_each_missing_brand_with_its_fields_and_buttons():
    out = run_page(CARD + r"""
(async () => {
    await wait(50);
    const a = part(rowOf(0)), b = part(rowOf(1));
    console.log(JSON.stringify({
        title: hooks['brands-title'].textContent, state: hooks.brands.getAttribute('data-state'),
        lead: hooks['brands-lead'].textContent, rows: hooks['brands-list'].children.length,
        footHidden: hooks['brands-foot'].hidden, addAll: hooks['brands-add-all'] !== undefined,
        a: { name: a.name, rows: a.rows, syn: a.syn.value, site: a.site.value, cost: a.cost,
             suggest: a.suggest.textContent, add: a.add.textContent, chipsHidden: a.chips.hidden },
        b: { name: b.name, rows: b.rows, syn: b.syn.value },
        get: calls.map(c => [c.method, c.url]) }));
    process.exit(0);
})();
""")
    assert out["title"] == "ماركات ناقصة من جدول الماركات (2)" and out["state"] == "ready"
    # «جدول الماركات», explained once as the sheet's «Brands Mapping» tab
    assert out["lead"].startswith("هالماركات بالطابور وما إلها صف بجدول الماركات (ورقة «Brands Mapping» بالشيت)") and out["rows"] == 2
    assert out["footHidden"] is False
    # the Arabic name first, then the store spellings, without the repeat «meliha» or an empty item
    assert out["a"] == {"name": "MELIHA", "rows": "5 صفوف", "syn": "مليحة، Mleiha، Maliha", "site": "", "cost": "بتكلّف بحث واحد",
                        "suggest": "اقترح الموقع الرسمي", "add": "أضف", "chipsHidden": True}
    assert out["b"] == {"name": "SABA SANABEL", "rows": "صف واحد", "syn": ""}
    assert out["get"] == [["GET", "/api/run/brand-suggestions"]]                  # the list reads; nothing was written


@NEEDS_NODE
def test_suggesting_the_site_is_one_search_that_fills_the_field_and_cannot_be_repeated_by_accident():
    out = run_page(CARD + r"""
reply['/api/run/brand-official-site'] = { status: 200, body: { status: 'success', brand: 'MELIHA', candidates: [
    { title: 'Meliha | Official', domain: 'meliha.ae' }, { title: 'Meliha dairy', domain: 'meliha.com' }] } };
(async () => {
    await wait(50);
    const a = part(rowOf(0));
    a.suggest.fire('click');
    a.suggest.fire('click');                       // a second click while it searches is the same search
    await wait(30);
    a.chips.children[1].fire('click');             // the second candidate is picked instead
    const picked = a.site.value;
    console.log(JSON.stringify({
        posts: calls.filter(c => c.method === 'POST'), site: picked, label: a.suggest.children[0].textContent,
        disabled: a.suggest.disabled, chips: a.chips.children.map(c => c.children[0].textContent),
        chipsHidden: a.chips.hidden, error: a.error.hidden }));
    process.exit(0);
})();
""")
    assert out["posts"] == [{"url": "/api/run/brand-official-site", "method": "POST", "body": {"brand": "MELIHA"}}]
    assert out["chips"] == ["meliha.ae", "meliha.com"] and out["chipsHidden"] is False
    assert out["site"] == "meliha.com" and out["label"] == "تم البحث (بحث واحد)" and out["disabled"] is True


@NEEDS_NODE
def test_a_failed_search_can_be_tried_again_and_says_why():
    out = run_page(CARD + r"""
reply['/api/run/brand-official-site'] = { status: 503, body: { status: 'unavailable', message: 'ما في مفتاح بحث (Serper) مضبوط بالإعدادات.' } };
(async () => {
    await wait(50);
    const a = part(rowOf(0));
    a.suggest.fire('click');
    await wait(30);
    console.log(JSON.stringify({ error: a.error.textContent, hidden: a.error.hidden, disabled: a.suggest.disabled,
                                 label: a.suggest.children[0].textContent }));
    process.exit(0);
})();
""")
    assert out == {"error": "ما في مفتاح بحث (Serper) مضبوط بالإعدادات.", "hidden": False, "disabled": False,
                   "label": "اقترح الموقع الرسمي"}


@NEEDS_NODE
def test_adding_a_brand_sends_the_fields_as_edited_and_removes_it_with_the_success_message():
    out = run_page(CARD + r"""
reply['/api/run/brand-add'] = { status: 200, body: { status: 'success', added: ['MELIHA'], message: 'انضافت الماركة. التشغيل الجاي بيعرفها.' } };
(async () => {
    await wait(50);
    const a = part(rowOf(0));
    a.syn.value = 'مليحة, Mleiha ،  Maliha, mleiha';
    a.site.value = ' HTTPS://www.Meliha.ae/en/shop?x=1 ';         // a pasted address becomes a bare host
    a.add.fire('click');
    await wait(30);
    console.log(JSON.stringify({
        post: calls.filter(c => c.url === '/api/run/brand-add'), left: hooks['brands-list'].children.map(r => r.getAttribute('data-brand')),
        title: hooks['brands-title'].textContent, done: hooks['brands-done'].textContent, doneHidden: hooks['brands-done'].hidden,
        toast: domToasts, foot: hooks['brands-foot'].hidden }));
    process.exit(0);
})();
""")
    assert out["post"] == [{"url": "/api/run/brand-add", "method": "POST", "body": {
        "brand": "MELIHA", "synonyms": ["مليحة", "Mleiha", "Maliha"], "official_domains": ["www.meliha.ae"]}}]
    assert out["left"] == ["SABA SANABEL"] and out["title"] == "ماركات ناقصة من جدول الماركات (1)"
    assert out["done"] == "انضافت الماركة. التشغيل الجاي بيعرفها." and out["doneHidden"] is False
    assert out["toast"][-1] == ["انضافت الماركة. التشغيل الجاي بيعرفها.", "success"] and out["foot"] is False


@NEEDS_NODE
def test_a_refused_add_keeps_the_brand_with_the_servers_reason():
    out = run_page(CARD + r"""
reply['/api/run/brand-add'] = { status: 422, body: { status: 'error', code: 'invalid_domain',
    message: 'الموقع لازم يكون اسم نطاق بس (متل almarai.com) بدون http ولا مسار.' } };
(async () => {
    await wait(50);
    const a = part(rowOf(0));
    a.site.value = 'not a site';
    a.add.fire('click');
    await wait(30);
    console.log(JSON.stringify({ error: a.error.textContent, hidden: a.error.hidden, disabled: a.add.disabled,
                                 rows: hooks['brands-list'].children.length, done: hooks['brands-done'].hidden }));
    process.exit(0);
})();
""")
    assert out["error"].startswith("الموقع لازم يكون اسم نطاق") and out["hidden"] is False
    assert out["disabled"] is False and out["rows"] == 2 and out["done"] is True


@NEEDS_NODE
def test_add_all_asks_first_sends_synonyms_only_and_clears_the_list():
    out = run_page(CARD + r"""
reply['/api/run/brand-add-all'] = { status: 200, body: { status: 'success', added: ['MELIHA', 'SABA SANABEL'], skipped: 0 } };
(async () => {
    await wait(50);
    const a = part(rowOf(0));
    a.site.value = 'meliha.ae';                     // a site typed on a row is not sent by «أضف الكل»
    a.syn.value = 'مليحة, Mleiha';
    let asked = '';
    globalThis.confirm = (text) => { asked = text; return false; };
    hooks['brands-add-all'].fire('click');
    await wait(20);
    const declined = calls.filter(c => c.url === '/api/run/brand-add-all').length;
    globalThis.confirm = (text) => { asked = text; return true; };
    hooks['brands-add-all'].fire('click');
    await wait(30);
    console.log(JSON.stringify({ declined, asked, post: calls.filter(c => c.url === '/api/run/brand-add-all'),
        rows: hooks['brands-list'].children.length, title: hooks['brands-title'].textContent,
        lead: hooks['brands-lead'].textContent, done: hooks['brands-done'].textContent, foot: hooks['brands-foot'].hidden,
        state: hooks.brands.getAttribute('data-state') }));
    process.exit(0);
})();
""")
    assert out["declined"] == 0                                                     # «cancel» writes nothing
    assert "أضف الكل بدون مواقع" in out["asked"] and "(2)" in out["asked"] and out["asked"].endswith("بدك تضيفها؟")
    assert out["post"] == [{"url": "/api/run/brand-add-all", "method": "POST", "body": {"items": [
        {"brand": "MELIHA", "synonyms": ["مليحة", "Mleiha"]}, {"brand": "SABA SANABEL", "synonyms": []}]}}]
    assert out["rows"] == 0 and out["title"] == "ماركات ناقصة من جدول الماركات (0)" and out["foot"] is True
    assert out["lead"] == "كل ماركات الطابور موجودة بجدول الماركات." and out["state"] == "clean"
    assert out["done"] == "انضافت ماركتين. التشغيل الجاي بيعرفهم."


@NEEDS_NODE
def test_the_card_says_when_nothing_is_missing_or_the_list_cannot_be_read():
    out = run_page(CARD + r"""
(async () => {
    await wait(50);
    const view = LaqtaRunPage;
    console.log(JSON.stringify({
        clean: view.describeBrands({ ok: true, data: { status: 'success', brands: [] } }),
        error: view.describeBrands({ ok: false, data: { status: 'error', message: 'ما قدرنا نقرأ الماركات الناقصة هلق. جرّب بعد شوي.' } }),
        none: view.describeBrands(null).lead,
        body: view.brandBody('X', 'a, b', ''), many: view.addAllBody([{ brand: 'X', synonymsText: 'a' }]),
        host: ['almarai.com', 'https://www.almarai.com/en', 'HTTP://Almarai.com:8080/x', ''].map(view.bareHost),
        added: [1, 2, 3, 11].map(view.addedText) }));
    process.exit(0);
})();
""")
    assert out["clean"] == {"state": "clean", "count": 0, "title": "ماركات ناقصة من جدول الماركات (0)", "brands": [],
                            "lead": "كل ماركات الطابور موجودة بجدول الماركات."}
    assert out["error"]["state"] == "error" and out["error"]["lead"] == "ما قدرنا نقرأ الماركات الناقصة هلق. جرّب بعد شوي."
    assert out["none"] == "ما قدرنا نقرأ الماركات الناقصة هلق."
    assert out["body"] == {"brand": "X", "synonyms": ["a", "b"], "official_domains": []}
    assert out["many"] == {"items": [{"brand": "X", "synonyms": ["a"]}]}
    assert out["host"] == ["almarai.com", "www.almarai.com", "almarai.com:8080", ""]   # the server refuses a port
    assert out["added"] == ["انضافت الماركة. التشغيل الجاي بيعرفها.", "انضافت ماركتين. التشغيل الجاي بيعرفهم.",
                            "انضافت 3 ماركات. التشغيل الجاي بيعرفها.", "انضافت 11 ماركة. التشغيل الجاي بيعرفها."]
