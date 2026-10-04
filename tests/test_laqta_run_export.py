"""«تصدير تقرير للتحليل» on the Run page. Each test failed before its change (there was no export):

* GET /api/run/export downloads laqta_run_<date>_<time>.json made by the bridge (export_run) and deletes it once sent;
  an Arabic message when it cannot be made (or the bridge names a file outside temp/exports); a bad run id never
  reaches the bridge (the Laravel app through its HTTP kernel with a stub cli_bridge);
* the Run page button downloads the file under the server's name (never another name), says how many rows it holds,
  or shows the server's Arabic error (public/js/run.js under node).
"""

import json

import pytest

from laqta_kernel import EXPORT_NAME, NEEDS_LARAVEL, NEEDS_NODE, ROOT, bridge_calls, kernel, run_page, stub_env


# ---------------------------------------------------------------------------
# The run export
# ---------------------------------------------------------------------------

@NEEDS_LARAVEL
def test_the_export_downloads_the_bridges_file_and_deletes_it(mariadb_or_skip, tmp_path):
    env, calls = stub_env(tmp_path)
    target = ROOT / "temp" / "exports" / EXPORT_NAME
    latest, review, bad = kernel(env, [["GET", "/api/run/export"], ["GET", "/api/run/export?scope=review"],
                                        ["GET", "/api/run/export?scope=run&run_id=../../etc"]])
    assert latest["status"] == 200 and latest["type"].startswith("application/json")
    assert latest["disposition"] == f"attachment; filename={EXPORT_NAME}" and latest["rows"] == "3"
    assert json.loads(latest["body"]) == {"format": "smoke_live/2", "rows": []}
    assert not target.exists()                                 # deleted once sent
    assert review["status"] == 200
    assert bad["status"] == 422 and json.loads(bad["body"])["message"] == "رقم التشغيل مش صحيح."
    assert [c for c in bridge_calls(calls)] == [["export_run", {"scope": "latest", "run_id": ""}],
                                          ["export_run", {"scope": "review", "run_id": ""}]]


@NEEDS_LARAVEL
@pytest.mark.parametrize("answer", [{"status": "failed", "error": "Could not export the run"},
                                    {"status": "success", "file": "../../.env", "rows": 1}])
def test_an_export_that_cannot_be_made_says_so_in_arabic(mariadb_or_skip, tmp_path, answer):
    env, _calls_file = stub_env(tmp_path, export=answer)
    (res,) = kernel(env, [["GET", "/api/run/export"]])
    assert res["status"] == 500 and res["disposition"] is None
    assert json.loads(res["body"])["message"].startswith("ما قدرنا نجهّز التقرير هلق")


@NEEDS_NODE
def test_the_export_button_downloads_the_file_under_the_servers_name():
    out = run_page(r"""
const saved = [];
globalThis.URL = { createObjectURL: b => 'blob:' + b.size, revokeObjectURL() {} };
document.body = new El('body');
const answers = [
    { ok: true, status: 200, headers: { 'Content-Disposition': 'attachment; filename=laqta_run_2026-10-04_1530.json', 'X-Laqta-Rows': '100' },
      blob: async () => ({ size: 42 }) },
    { ok: false, status: 500, headers: { 'Content-Type': 'application/json' },
      json: async () => ({ status: 'error', message: 'ما قدرنا نجهّز التقرير هلق. تأكد إنو قاعدة البيانات شغّالة وجرّب مرة ثانية.' }) }
];
const realFetch = globalThis.fetch;
const exportCalls = [];
globalThis.fetch = (url, init) => {
    if (!url.startsWith('/api/run/export')) return realFetch(url, init);
    exportCalls.push(url);
    const a = answers.shift();
    return Promise.resolve(Object.assign({ headers: { get: k => (a.headers || {})[k] === undefined ? null : a.headers[k] } },
                                         { ok: a.ok, status: a.status, blob: a.blob, json: a.json }));
};
const origCreate = document.createElement;
document.createElement = t => { const n = origCreate(t); if (t === 'a') n.click = () => saved.push([n.getAttribute('href'), n.getAttribute('download')]); return n; };
island.textContent = 'null';
LaqtaRunPage.mount(document);
(async () => {
    page.querySelector('[data-run="export-scope"]').value = 'review';
    hooks.export.fire('click');
    await wait(30);
    const first = [hooks['export-done'].textContent, hooks['export-done'].hidden, hooks['export-error'].hidden, hooks.export.disabled];
    hooks['export-scope'].value = 'latest';
    hooks.export.fire('click');
    await wait(30);
    console.log(JSON.stringify({ calls: exportCalls, saved, first,
        second: [hooks['export-error'].textContent, hooks['export-error'].hidden, hooks['export-text'].textContent],
        names: [LaqtaRunPage.exportFileName('attachment; filename="laqta_run_2026-10-04_1530.json"'),
                LaqtaRunPage.exportFileName('attachment; filename=../../evil.sh')] }));
    process.exit(0);
})();
""")
    assert out["calls"] == ["/api/run/export?scope=review", "/api/run/export?scope=latest"]
    assert out["saved"] == [["blob:42", "laqta_run_2026-10-04_1530.json"]]
    assert out["first"] == ["نزل الملف laqta_run_2026-10-04_1530.json (100 صف). ابعته للمطوّر.", False, True, False]
    assert out["second"] == ["ما قدرنا نجهّز التقرير هلق. تأكد إنو قاعدة البيانات شغّالة وجرّب مرة ثانية.", False,
                             "تصدير تقرير للتحليل"]
    assert out["names"] == ["laqta_run_2026-10-04_1530.json", "laqta_run.json"]
