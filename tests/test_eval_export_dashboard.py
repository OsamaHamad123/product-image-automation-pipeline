"""«صدّر مجموعة اختبار» in the Health page's advanced section (HealthController::exportEvalSet, cli_bridge eval_export,
scripts/eval_record.py --from-db).

- Static: the routes, the section's hooks, the page still loads one script.
- PHP helper under the PHP CLI: the owner's sentence (how many products and images, where the file is).
- The page script under node: one POST per click, the status line and the download link, plain Arabic on failure.
- The Laravel app through its HTTP kernel with a stub bridge: the POST through the bridge with CSRF, the download of
  the zip, a bad file name refused. Skipped without php, dashboard/vendor or MariaDB.
- The bridge action against the MariaDB test database: seeded reviews, queue row and stored candidates become a
  labelled set and one zip, read only.
"""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import uuid
import zipfile
from pathlib import Path

import pytest
from laqta_review_harness import NODE
from test_laqta_health import DASH, PHP, ROOT, _js, _kernel, _node, _sql, app_env  # noqa: F401

CONTROLLER = DASH / "app" / "Http" / "Controllers" / "HealthController.php"
ROUTES = DASH / "routes" / "web.php"
HEALTH_VIEW = DASH / "resources" / "views" / "dashboard" / "diagnostics.blade.php"
HEALTH_JS = DASH / "public" / "js" / "health.js"
EXPORTS = ROOT / "temp" / "exports"

NEEDS_PHP = pytest.mark.skipif(PHP is None, reason="php is not installed")
NEEDS_NODE = pytest.mark.skipif(NODE is None, reason="node is not installed")


def read(path):
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Static
# ---------------------------------------------------------------------------

def test_routes_hooks_and_one_script():
    routes = read(ROUTES)
    c = "\\App\\Http\\Controllers\\HealthController"
    assert f"Route::post('/api/system/eval-export', [{c}::class, 'exportEvalSet']);" in routes
    assert f"Route::get('/api/system/eval-export/{{file}}', [{c}::class, 'downloadEvalSet'])" in routes
    view, js = read(HEALTH_VIEW), read(HEALTH_JS)
    # the export is a card of the Health page's advanced section («تفاصيل متقدمة», collapsed by default)
    advanced = view[view.index('<details class="lq-health-advanced" data-health="advanced">'):view.rindex("</details>")]
    assert '<span class="lq-health-advanced__label">تفاصيل متقدمة</span>' in advanced
    assert "صدّر مجموعة اختبار" in advanced and 'data-health="eval-card"' in advanced
    for hook in ("eval-export", "eval-export-status", "eval-export-label", "eval-export-link"):
        assert f'data-health="{hook}"' in view, hook
    assert "function evalExportView(" in js and "function createEvalExport(" in js
    assert "'/api/system/eval-export'" in js
    assert [line for line in view.splitlines() if "asset('js/" in line and ".js" in line].__len__() == 1
    import cli_bridge
    assert cli_bridge.ACTIONS["eval_export"] is cli_bridge.action_eval_export
    assert list(cli_bridge.ACTIONS)[-2:] == ["ops_health", "run_control"]


@NEEDS_PHP
def test_the_controller_parses():
    out = subprocess.run([PHP, "-l", str(CONTROLLER)], capture_output=True, text=True)
    assert out.returncode == 0, out.stdout + out.stderr


def _php(code):
    controller = str(CONTROLLER).replace("\\", "/")
    script = ("<?php\nnamespace App\\Http\\Controllers { abstract class Controller {} }\n"
              "namespace App\\Services { class PythonBridge {} }\nnamespace {\n"
              f"require '{controller}';\nuse App\\Http\\Controllers\\HealthController as H;\n"
              "$out = [];\n" + code + "\necho json_encode($out, JSON_UNESCAPED_UNICODE);\n}\n")
    with tempfile.NamedTemporaryFile("w", suffix=".php", delete=False, encoding="utf-8") as fh:
        fh.write(script)
        path = fh.name
    try:
        result = subprocess.run([PHP, path], capture_output=True, text=True, encoding="utf-8", timeout=60)
    finally:
        os.unlink(path)
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


@NEEDS_PHP
def test_the_owners_sentence_says_how_many_products_and_where_the_file_is():
    out = _php("""
$out['none'] = H::evalExportText(['products' => 0]);
$out['some'] = H::evalExportText(['products' => 12, 'candidates' => 70, 'labelled' => 31,
    'zip_path' => '/srv/laqta/temp/exports/laqta_eval_set_2026-10-06_1015.zip']);
$out['ok_name'] = preg_match(H::EVAL_SET_FILE, 'laqta_eval_set_2026-10-06_1015.zip');
$out['bad_name'] = preg_match(H::EVAL_SET_FILE, '../.env');
""")
    assert "ما في منتجات راجعتها لسا" in out["none"]
    assert out["some"].startswith("جاهز: 12 منتج راجعتهم، فيهم 70 صورة (31 منها معروف إذا صح أو غلط).")
    assert "/srv/laqta/temp/exports/laqta_eval_set_2026-10-06_1015.zip" in out["some"]
    assert "ابعته للفريق" in out["some"]
    assert (out["ok_name"], out["bad_name"]) == (1, 0)


# ---------------------------------------------------------------------------
# The page script under node
# ---------------------------------------------------------------------------

@NEEDS_NODE
def test_the_button_posts_once_and_shows_where_the_file_is():
    out = _node(_js(HEALTH_JS) + r"""
const H = window.LaqtaHealth;
const log = { renders: [], toasts: [], fetches: [] };
function make(responses) {
  return H.createEvalExport({
    fetchJson: (url, opts) => { log.fetches.push([opts.method, url]); const r = responses.shift();
      return r instanceof Error ? Promise.reject(r) : Promise.resolve(r); },
    render: v => log.renders.push(v),
    toast: (t, v) => log.toasts.push([v, t])
  });
}
(async () => {
  const out = {};
  const ok = { ok: true, data: { status: 'success', products: 3, download: '/api/system/eval-export/f.zip',
                                 message: 'جاهز: 3 منتج راجعتهم' } };
  const c = make([ok]);
  const pending = c.run();
  const again = await c.run();                          // a second click while it works: nothing more is sent
  const view = await pending;
  out.ok = [again, log.fetches.slice(), log.renders[0].disabled, log.renders[0].label, view, log.toasts[0]];
  const empty = make([{ ok: true, data: { status: 'success', products: 0, download: '/x', message: 'ما في منتجات' } }]);
  out.empty = [await empty.run(), log.toasts[log.toasts.length - 1][0]];
  const bad = make([{ ok: false, status: 500, data: { status: 'failed', error: 'ما قدرنا نجهّز مجموعة الاختبار' } }]);
  out.bad = await bad.run();
  const down = make([new Error('network')]);
  out.down = await down.run();
  const expired = make([{ ok: false, status: 419, data: null }]);
  out.expired = await expired.run();
  console.log(JSON.stringify(out));
})();
""")
    again, fetches, busy, label, view, toast = out["ok"]
    assert again is None and fetches == [["POST", "/api/system/eval-export"]]
    assert busy is True and label == "عم يجهّز…"
    assert view == {"text": "جاهز: 3 منتج راجعتهم", "link": "/api/system/eval-export/f.zip",
                    "label": "صدّر مجموعة اختبار", "disabled": False, "tone": "success"}
    assert toast == ["success", "مجموعة الاختبار جاهزة."]
    assert out["empty"][0]["link"] == "" and out["empty"][1] == "warning"
    assert out["bad"]["tone"] == "danger" and out["bad"]["text"] == "ما قدرنا نجهّز مجموعة الاختبار"
    assert out["down"]["tone"] == "danger" and "الخادم ما ردّ" in out["down"]["text"]
    assert "انتهت صلاحية الصفحة" in out["expired"]["text"]


# ---------------------------------------------------------------------------
# The Laravel app through its HTTP kernel (a stub bridge)
# ---------------------------------------------------------------------------

@pytest.fixture
def eval_app(app_env, tmp_path):
    calls = tmp_path / "ee_calls.txt"
    stub = tmp_path / "ee_bridge.py"
    name = "laqta_eval_set_2026-10-06_1015.zip"
    stub.write_text(
        "import json, os, sys\n"
        f"open({str(calls)!r}, 'a').write((sys.argv[1] if len(sys.argv) > 1 else '') + '\\n')\n"
        "if os.environ.get('LQ_STUB_MODE') == 'down':\n"
        "    print(json.dumps({'status': 'failed', 'error': 'stub bridge is down'}))\n"
        "elif sys.argv[1] == 'eval_export':\n"
        f"    print(json.dumps({{'status': 'success', 'file': {name!r}, 'zip_path': '/x/temp/exports/{name}',\n"
        "                      'folder': 'tests/eval/fixtures/recorded/2026-10-06', 'products': 4, 'candidates': 19,\n"
        "                      'labelled': 9, 'images': 15}))\n"
        "else:\n"
        "    print(json.dumps({'status': 'success', 'scanned': 0, 'windows': {}, 'alerts': []}))\n", encoding="utf-8")
    EXPORTS.mkdir(parents=True, exist_ok=True)
    zip_file = EXPORTS / name
    with zipfile.ZipFile(zip_file, "w") as zf:
        zf.writestr("2026-10-06/manifest.json", "{}")
    yield {"env": dict(app_env["env"], CLI_BRIDGE_PATH=str(stub)), "calls": calls, "name": name}
    zip_file.unlink(missing_ok=True)


def test_the_page_has_the_advanced_section_and_opening_it_runs_nothing(eval_app):
    (page,) = _kernel(eval_app["env"], [["GET", "/system-diagnostics", {}]])
    assert page["status"] == 200 and 'data-health="advanced"' in page["body"]
    assert "صدّر مجموعة اختبار" in page["body"] and "مجموعة اختبار من مراجعاتك" in page["body"]
    assert "eval_export" not in (eval_app["calls"].read_text(encoding="utf-8") if eval_app["calls"].exists() else "")


def test_the_button_endpoint_runs_the_export_and_gives_the_download_link(eval_app):
    env = eval_app["env"]
    (done,) = _kernel(env, [["POST", "/api/system/eval-export", {}]])
    body = json.loads(done["body"])
    assert done["status"] == 200 and body["status"] == "success"
    assert body["download"] == "/api/system/eval-export/" + eval_app["name"]
    assert body["message"].startswith("جاهز: 4 منتج راجعتهم، فيهم 19 صورة (9 منها")
    assert eval_app["calls"].read_text(encoding="utf-8").split() == ["eval_export"]
    (down,) = _kernel(dict(env, LQ_STUB_MODE="down"), [["POST", "/api/system/eval-export", {}]])
    assert down["status"] == 500 and "ما قدرنا نجهّز مجموعة الاختبار" in json.loads(down["body"])["error"]
    assert "stub" not in down["body"]


def test_the_zip_downloads_and_any_other_name_is_refused(eval_app):
    env = eval_app["env"]
    got, missing, bad = _kernel(env, [["GET", "/api/system/eval-export/" + eval_app["name"], {}],
                                      ["GET", "/api/system/eval-export/laqta_eval_set_2020-01-01_0000.zip", {}],
                                      ["GET", "/api/system/eval-export/..%2F..%2F.env", {}]])
    assert got["status"] == 200
    assert missing["status"] == 404 and "صدّر المجموعة من جديد" in json.loads(missing["body"])["message"]
    assert bad["status"] == 404


def test_the_button_endpoint_needs_the_csrf_token(eval_app):
    env = dict(eval_app["env"], APP_ENV="local")
    (response,) = _kernel(env, [["POST", "/api/system/eval-export", {}]])
    assert response["status"] == 419
    assert not eval_app["calls"].exists()


# ---------------------------------------------------------------------------
# The bridge action on the MariaDB test database
# ---------------------------------------------------------------------------

def _eval_record_module():
    spec = importlib.util.spec_from_file_location("eval_record", ROOT / "scripts" / "eval_record.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_bridge_action_exports_the_reviewed_products_read_only(mariadb_or_skip, monkeypatch, tmp_path):
    import cli_bridge
    from catalog_match import fetch

    db = mariadb_or_skip
    tag = uuid.uuid4().hex[:10]
    key, row = f"ee-{tag}", 987001
    sha = "e" * 63 + "1"
    store = tmp_path / "candidates"
    store.mkdir()
    from PIL import Image
    Image.new("RGB", (900, 900), (250, 250, 250)).save(store / f"{sha}.jpg", quality=90)
    good = f"https://gcc.luluhypermarket.com/medias/{tag}-zwan.jpg"
    other = f"https://www.carrefouruae.com/img/{tag}-zwan-200.jpg"
    record = _eval_record_module()
    monkeypatch.setitem(sys.modules, "eval_record", record)
    monkeypatch.setattr(record, "RECORDED_DIR", tmp_path / "recorded")
    monkeypatch.setattr(record, "load_cached_mappings", lambda: {})
    monkeypatch.setattr(fetch, "_store_dir", lambda explicit=None: store)
    monkeypatch.setattr(cli_bridge, "EXPORT_DIR", str(tmp_path / "exports"))
    try:
        _sql(db, "INSERT INTO automation_queue (`row_number`, barcode, product_name, brand, search_query, status, "
                 "sku_key, payload_json, trace_json) VALUES (%s, '', 'ZWAN CHICKEN LUNCHEON MEAT 340GM', 'ZWAN', 'q', "
                 "'ready_for_review', %s, %s, %s)",
             (row, key, json.dumps({"size": ""}),
              json.dumps({"outcome": {"decision": "REVIEW_PRESELECTED", "winner_url": good}})))
        assert db.save_curation_candidates(row, "ZWAN CHICKEN LUNCHEON MEAT 340GM", "ZWAN", [
            {"url": good, "title": "Zwan Chicken Luncheon Meat 340g", "status": "preselected",
             "page_url": "https://gcc.luluhypermarket.com/p/1", "domain": "gcc.luluhypermarket.com",
             "reasons": ["preselected:vlm_match", "lane:strict"], "evidence": {"tier": 1, "sanctioned": True},
             "vlm": {"decision": "MATCH", "brand_text": "Zwan", "brand_match": "yes", "variant_match": "yes",
                     "size_match": "yes", "view": "front_packshot", "size_text": "340g"},
             "content_sha256": sha, "width": 900, "height": 900},
            {"url": other, "title": "Zwan Chicken Luncheon Meat 200g", "status": "eligible",
             "evidence": {"tier": 1, "sanctioned": True}},
        ], sku_key=key)
        db.add_review_decision("rejected", sku_key=key, row_number=row, image_url=other, reason_code="WRONG_SIZE")
        db.add_review_decision("approved", sku_key=key, row_number=row, image_url=good,
                               engine_decision="REVIEW_PRESELECTED", was_preselected=True, lane="strict")
        before = _sql(db, "SELECT COUNT(*) AS n FROM curation_candidates WHERE sku_key = %s", (key,))[0]["n"]
        out = cli_bridge.action_eval_export({})
        after = _sql(db, "SELECT COUNT(*) AS n FROM curation_candidates WHERE sku_key = %s", (key,))[0]["n"]
    finally:
        _sql(db, "DELETE FROM review_decisions WHERE sku_key = %s", (key,))
        _sql(db, "DELETE FROM curation_candidates WHERE sku_key = %s", (key,))
        _sql(db, "DELETE FROM automation_queue WHERE sku_key = %s", (key,))
    assert out["status"] == "success" and out["file"].startswith("laqta_eval_set_") and out["file"].endswith(".zip")
    assert before == after == 2                                         # read only
    assert Path(out["zip_path"]).is_file() and out["products"] >= 1
    folder = tmp_path / "recorded" / Path(out["folder"]).name
    golden = json.loads((folder / "golden_skus.json").read_text(encoding="utf-8"))
    sku = next(s for s in golden["skus"] if s["sku_key"] == key)
    assert sku["name_en"] == "ZWAN CHICKEN LUNCHEON MEAT 340GM" and sku["row_number"] == row
    assert [(c["image_url"], c["label"]) for c in sku["candidates"]] == [(good, "correct_exact"),
                                                                         (other, "wrong_size")]
    assert sku["candidates"][0]["image_file"].startswith("blobs/") and sku["recorded"]["lane"] == "strict"
    with zipfile.ZipFile(out["zip_path"]) as zf:
        assert any(n.endswith("/golden_skus.json") for n in zf.namelist())
