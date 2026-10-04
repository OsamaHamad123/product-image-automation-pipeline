"""«جودة بيانات الشيت» on the Run page. Each test failed before its change (there was no card):

* GET /api/run/sheet-quality reads only the sheet rows already cached (never the bridge or Google) unless asked to read
  the sheet again, and groups the rows' sheet_issues (no size, barcode, unknown brand, typos, duplicate barcodes) with
  their rows; rows cached before the check say so (the Laravel app through its HTTP kernel, stub cli_bridge);
* the card, loaded after «قبل ما تبدأ», lists each group's rows as links to the review screen, the brands of the
  unknown-brand group and how many rows are not listed (public/js/run.js under node).
"""

import json

from laqta_kernel import NEEDS_LARAVEL, NEEDS_NODE, OLD_SHEET, bridge_calls, kernel, run_page, stub_env


# ---------------------------------------------------------------------------
# Sheet data quality
# ---------------------------------------------------------------------------

@NEEDS_LARAVEL
def test_sheet_quality_reads_only_cached_rows_unless_asked_to_read_again(mariadb_or_skip, tmp_path):
    env, calls = stub_env(tmp_path)
    cached, fresh = kernel(env, [["GET", "/api/run/sheet-quality"], ["GET", "/api/run/sheet-quality?refresh=1"]])
    assert json.loads(cached["body"]) == {"status": "not_loaded"}
    data = json.loads(fresh["body"])
    assert [c[0] for c in bridge_calls(calls)] == ["get_products"]        # only the explicit «read again» used the bridge
    assert (data["status"], data["total"], data["checked"]) == ("success", 5, 5)
    groups = {g["key"]: g for g in data["groups"]}
    assert [g["key"] for g in data["groups"]] == ["no_size", "no_barcode", "brand_unknown", "typo", "duplicate_barcode"]
    assert (groups["no_size"]["count"], groups["no_barcode"]["count"], groups["brand_unknown"]["count"]) == (1, 2, 1)
    assert groups["typo"]["rows"] == [{"row": 3, "name": "TARGET CHICEKN LUNCHEN MEAT", "href": "/catalog?row=3",
                                       "text": "يمكن «CHICEKN» قصدك «CHICKEN»، يمكن «LUNCHEN» قصدك «LUNCHEON»"}]
    assert groups["typo"]["count"] == 1
    assert groups["brand_unknown"]["brands"] == [{"brand": "TARGET", "count": 1}]
    assert [r["row"] for r in groups["duplicate_barcode"]["rows"]] == [4, 5]


@NEEDS_LARAVEL
def test_rows_cached_before_the_check_say_so(mariadb_or_skip, tmp_path):
    env, _calls_file = stub_env(tmp_path, products=OLD_SHEET)
    (fresh,) = kernel(env, [["GET", "/api/run/sheet-quality?refresh=1"]])
    assert json.loads(fresh["body"])["status"] == "stale"


@NEEDS_NODE
def test_the_run_page_lists_the_sheets_gaps_with_links_to_the_rows():
    out = run_page(r"""
const quality = { status: 'success', total: 5, checked: 5, groups: [
    { key: 'no_size', label: 'حجم ناقص', count: 1, rows: [{ row: 2, name: 'MR JOHN FRENCH FRIES', text: 'الحجم ناقص بالشيت' }] },
    { key: 'brand_unknown', label: 'ماركة مش موجودة في Brands Mapping', count: 3, brands: [{ brand: 'TARGET', count: 3 }],
      rows: [{ row: 3, name: 'TARGET CHICKEN', text: 'x' }] },
    { key: 'typo', label: 'غلطة إملائية محتملة بالاسم', count: 0, rows: [] } ] };
const realFetch = globalThis.fetch;
globalThis.fetch = (url, init) => url.startsWith('/api/run/sheet-quality')
    ? (net.push({ url }), Promise.resolve({ ok: true, status: 200, json: async () => quality }))
    : realFetch(url, init);
island.textContent = 'null';
LaqtaRunPage.mount(document);
(async () => {
    await wait(50);
    const groups = hooks['quality-groups'].children;
    const links = groups.map(g => g.children[g.children.length - 1].children.filter(li => li.children.length)
                                     .map(li => li.children[0].getAttribute('href')));
    const more = groups.map(g => g.children[g.children.length - 1].children.filter(li => !li.children.length).map(li => li.textContent));
    console.log(JSON.stringify({
        order: net.filter(n => n.url.startsWith('/api/run/')).map(n => n.url.split('?')[0]),
        state: hooks.quality.getAttribute('data-state'), lead: hooks['quality-lead'].textContent,
        groups: groups.map(g => [g.getAttribute('data-issue'), g.children[0].textContent]), links, more,
        brands: groups[1].children[1].textContent,
        views: [LaqtaRunPage.describeQuality({ ok: true, data: { status: 'not_loaded' } }).state,
                LaqtaRunPage.describeQuality({ ok: true, data: { status: 'stale' } }).state,
                LaqtaRunPage.describeQuality({ ok: true, data: { status: 'success', total: 4, groups: [] } }).lead,
                LaqtaRunPage.describeQuality({ ok: false, data: { status: 'error', message: 'ما قدرنا نقرأ الشيت هلق. جرّب بعد شوي.' } }).lead]
    }));
    process.exit(0);
})();
""")
    assert out["order"][:2] == ["/api/run/plan", "/api/run/sheet-quality"]       # after the plan, from its cache
    assert out["state"] == "ready" and out["lead"].startswith("هالصفوف بتمنع اختيار واثق")
    assert out["groups"] == [["no_size", "حجم ناقص1"], ["brand_unknown", "ماركة مش موجودة في Brands Mapping3"]]
    assert out["links"] == [["/catalog?row=2"], ["/catalog?row=3"]]
    assert out["more"] == [[], ["و2 صف غيرهم"]]                       # the count is the full number
    assert out["brands"] == "الماركات: TARGET (3)"
    assert out["views"][:2] == ["not_loaded", "stale"]
    assert out["views"][2].startswith("كل صفوف الشيت (4 منتجات) فيها حجم وباركود")
    assert out["views"][3] == "ما قدرنا نقرأ الشيت هلق. جرّب بعد شوي."
