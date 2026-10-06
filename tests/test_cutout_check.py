"""«فحص القص»: the cut-out QA gallery (RecutController page and endpoints, js/review/cutout.js, cli_bridge cutout_gallery
and recut_try / recut_apply / recut_discard / recut_undo).

- Static: the routes, the page's hooks and scripts (theme_preview.js reused), the classes it uses exist.
- The page script's helpers under node: plain Arabic for the counts, a new cut and the result of «اعتمد الجديد».
- The bridge actions on the MariaDB test database with fakes (the sheet, Cloudinary, the background removal): the
  gallery measures and flags, a try waits and replaces nothing, «اعتمد الجديد» goes through the outbox with the
  replace guard and is logged, «خلّي القديم» discards, «رجّع القديم» undoes.
- The Laravel app through its HTTP kernel with a stub bridge: the page, the relays, the validation, the preview file.
"""

import json
import subprocess

import pytest
from PIL import Image
from laqta_review_harness import NODE
from test_laqta_health import DASH, PHP, ROOT, _kernel, app_env  # noqa: F401
from test_reprocess_transparent import (BASE, JUICE, JUICE_URL, LABAN, LABAN_URL, MILK, MILK_URL, NEW, Cloud,  # noqa: F401
                                        Isolation, approve, bottle, canvas, db, gs, local_only, outbox, statuses,
                                        the_sheet)

CONTROLLER = DASH / "app" / "Http" / "Controllers" / "RecutController.php"
ROUTES = DASH / "routes" / "web.php"
VIEW = DASH / "resources" / "views" / "dashboard" / "cutout_check.blade.php"
JS = DASH / "public" / "js" / "review" / "cutout.js"
CSS = DASH / "public" / "css" / "pages" / "cutout.css"
REVIEW_CSS = DASH / "public" / "css" / "pages" / "review.css"
LAQTA_CSS = DASH / "public" / "css" / "laqta.css"

NEEDS_NODE = pytest.mark.skipif(NODE is None, reason="node is not installed")
NEEDS_PHP = pytest.mark.skipif(PHP is None, reason="php is not installed")


def read(path):
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Static
# ---------------------------------------------------------------------------

def test_routes_page_and_scripts():
    routes = read(ROUTES)
    c = "\\App\\Http\\Controllers\\RecutController"
    for line in (f"Route::get('/cutout-check', [{c}::class, 'page'])->name('dashboard.cutout_check');",
                 f"Route::get('/api/cutout/gallery', [{c}::class, 'gallery']);",
                 f"Route::post('/api/cutout/try', [{c}::class, 'tryCut']);",
                 f"Route::post('/api/cutout/apply', [{c}::class, 'apply']);",
                 f"Route::post('/api/cutout/discard', [{c}::class, 'discard']);",
                 f"Route::post('/api/cutout/undo', [{c}::class, 'undo']);",
                 f"Route::get('/api/cutout/preview/{{token}}', [{c}::class, 'preview'])"):
        assert line in routes, line
    view, js = read(VIEW), read(JS)
    assert "@section('lq_nav', 'review')" in view and "فحص القص" in view
    for name in ("theme_preview.js", "ui.js", "cutout.js"):
        assert f"js/review/{name}" in view, name
    assert view.index("'js/review/theme_preview.js'") < view.index("'js/review/cutout.js'")
    for hook in set(__import__("re").findall(r"part\('([a-z-]+)'\)", js)):
        assert f'data-cq="{hook}"' in view, hook
    assert "innerHTML" not in js and "insertAdjacentHTML" not in js and "eval(" not in js
    assert "R.themePreview.apply(stage, mode)" in js                       # the review screen's preview, reused
    import cli_bridge
    for action in ("cutout_gallery", "recut_try", "recut_apply", "recut_discard", "recut_undo"):
        assert cli_bridge.ACTIONS[action] is getattr(cli_bridge, f"action_{action}")
    assert list(cli_bridge.ACTIONS)[-2:] == ["ops_health", "run_control"]


def test_every_class_the_page_uses_is_defined():
    import re
    defined = set()
    for path in (CSS, REVIEW_CSS, LAQTA_CSS):
        css = re.sub(r"/\*.*?\*/", "", read(path), flags=re.DOTALL)
        defined |= set(re.findall(r"\.([a-z][a-z0-9_]*(?:(?:__|--|-)[a-z0-9_]+)*)", css))
    view, js = read(VIEW), read(JS)
    groups = re.findall(r'class="([^"]+)"', view) + re.findall(r"className: '([^']+)'", js)
    used = {c for g in groups for c in g.split() if not c.endswith("-")}
    assert {"cq-card", "cq-stage", "cq-flag", "cq-log__item", "lq-filter"} <= used
    missing = sorted(used - defined)
    assert not missing, missing
    text = read(CSS)
    assert not re.search(r"--lq-[a-z0-9-]+\s*:", text), "page CSS must not define tokens"
    tokens = set(re.findall(r"(--lq-[a-z0-9-]+)\s*:", read(LAQTA_CSS)))
    assert not set(re.findall(r"var\((--lq-[a-z0-9-]+)", text)) - tokens
    for physical in ("margin-left", "margin-right", "padding-left", "padding-right", "border-left", "border-right"):
        assert physical not in text, physical
    assert "@media (max-width: 560px)" in text


@NEEDS_PHP
def test_the_controller_parses():
    out = subprocess.run([PHP, "-l", str(CONTROLLER)], capture_output=True, text=True)
    assert out.returncode == 0, out.stdout + out.stderr


# ---------------------------------------------------------------------------
# The page script's helpers under node
# ---------------------------------------------------------------------------

@NEEDS_NODE
def test_the_page_says_it_in_plain_arabic():
    script = ("globalThis.window = globalThis;\n" + read(DASH / "public" / "js" / "review" / "ui.js") + "\n" + read(JS)
              + r"""
const C = window.LaqtaReview.cutout;
const out = {
  all: C.statusText({ counts: { all: 40 }, unchecked: 7, total: 40 }),
  flag: C.statusText({ counts: { all: 40 }, flag: 'dark_halo', total: 2, unchecked: 0 }),
  empty: C.statusText({ counts: { all: 0 } }),
  written: C.applyText({ rows: [4, 9], sheet: { written: 2 } }),
  pending: C.applyText({ rows: [4], sheet: { pending: 1, conflict: 1 }, busy: [] }),
  none: C.applyText({ rows: [], sheet: {} }),
  cut: C.tryText({ provider: 'photoroom', source: 'candidate', flags: [], paid_calls: 1, can_apply: true }),
  flagged: C.tryText({ provider: 'rembg', source: 'master', flags: ['dark_halo', 'opaque_fill'], paid_calls: 0, can_apply: false }),
  unknown: C.flagText('something_new'),
  pr: [C.photoroomNote({ photoroom: false, photoroom_reason: 'no_key' }), C.photoroomNote({ photoroom: true })],
  plural: [C.pictures(1), C.pictures(2), C.pictures(5), C.pictures(40)],
  expired: C.errorText({ status: 419, data: null }, 'x'),
  english: C.errorText({ status: 500, data: { error: 'Traceback' } }, 'ما زبط')
};
console.log(JSON.stringify(out));
""")
    res = subprocess.run([NODE, "-"], input=script, capture_output=True, text=True, timeout=60, encoding="utf-8")
    assert res.returncode == 0, res.stderr
    out = json.loads(res.stdout.strip().splitlines()[-1])
    assert out["all"] == "40 صورة منشورة، الأحدث أول. لسا 7 صور ما انفحصت (منفحص 24 بكل مرة)."
    assert out["flag"] == "صورتين فيها «حواف فاتحة على الغامق» من أصل 40 صورة."
    assert out["empty"] == "ما في صور منشورة لسا."
    assert out["written"] == "انتشرت النسخة الجديدة. انكتبت بـالصفوف 4، 9."
    assert "رح تنكتب بالشيت بعد شوي" in out["pending"] and "غيّرت صورته بإيدك" in out["pending"]
    assert "ما انكتب شي بالشيت" in out["none"]
    assert out["cut"] == "قص PhotoRoom من الصورة الأصلية المحفوظة. بلا ملاحظات. كلّف طلب عزل واحد."
    assert "حواف فاتحة على الغامق، الخلفية ما انشالت" in out["flagged"] and "ما منقدر ننشره" in out["flagged"]
    assert out["unknown"] == "ملاحظة بالقص"
    assert out["pr"] == ["مفتاح PhotoRoom مش محفوظ.", ""]
    assert out["plural"] == ["صورة وحدة", "صورتين", "5 صور", "40 صورة"]
    assert "انتهت صلاحية الصفحة" in out["expired"] and out["english"] == "ما زبط"


# ---------------------------------------------------------------------------
# The bridge actions (MariaDB test database, fakes for the rest)
# ---------------------------------------------------------------------------

@pytest.fixture
def bridge(db, gs, outbox, monkeypatch, tmp_path):
    import cli_bridge
    import cloudinary_storage
    import cutout_finish
    import image_processor
    import recut

    cloud = Cloud()
    milk, laban = bottle((200, 40, 40)), bottle((40, 160, 60), label=(220, 200, 30))
    cloud.put(MILK_URL, canvas(milk, False))
    cloud.put(LABAN_URL, canvas(laban, True))
    approve(db, MILK, "Almarai Fresh Milk", "Almarai", MILK_URL)
    approve(db, LABAN, "Almarai Laban", "Almarai", LABAN_URL)
    iso = Isolation(cloud, {"https://shop.ae/Almarai Fresh Milk.jpg": milk, "https://shop.ae/Almarai Laban.jpg": laban})
    sheet = the_sheet()
    monkeypatch.setattr(recut, "fetch_bytes", cloud.fetch)
    monkeypatch.setattr(recut, "TRY_DIR", str(tmp_path / "recut"))
    monkeypatch.setattr(image_processor, "process_product_image_result", iso)
    monkeypatch.setattr(image_processor, "local_methods_available", lambda: {"grabcut": True, "rembg": True})
    monkeypatch.setattr(cutout_finish, "photoroom_ready", lambda: None)
    monkeypatch.setattr(cloudinary_storage, "upload_product_image", cloud.upload)
    monkeypatch.setattr(cli_bridge, "_open_sheet", lambda: sheet)
    monkeypatch.setattr(cli_bridge, "LOG_PATH", str(tmp_path / "search.log"))
    ids = {r["product_name"]: r["id"] for r in db.published_masters()}
    return {"bridge": cli_bridge, "cloud": cloud, "iso": iso, "sheet": sheet, "ids": ids, "db": db, "gs": gs,
            "tmp": tmp_path}


def test_the_gallery_measures_flags_and_pages(bridge):
    out = bridge["bridge"].action_cutout_gallery({"measure": True})
    assert out["status"] == "success" and out["measured"] == 2 and out["unchecked"] == 0
    by_name = {i["product_name"]: i for i in out["items"]}
    assert by_name["Almarai Fresh Milk"]["flags"] == ["white"] and by_name["Almarai Fresh Milk"]["background"] == "white"
    assert by_name["Almarai Laban"]["background"] == "transparent" and "white" not in by_name["Almarai Laban"]["flags"]
    assert by_name["Almarai Laban"]["url"] == LABAN_URL and by_name["Almarai Fresh Milk"]["url"].endswith("aaaa1111")
    assert "/c_limit,w_1200,f_webp,q_auto/" in by_name["Almarai Fresh Milk"]["url"]   # the delivery that keeps alpha
    assert out["counts"]["all"] == 2 and out["counts"]["white"] == 1
    assert out["methods"] == {"photoroom": True, "photoroom_reason": None, "local": True}
    white = bridge["bridge"].action_cutout_gallery({"flag": "white", "measure": False})
    assert [i["product_name"] for i in white["items"]] == ["Almarai Fresh Milk"] and white["total"] == 1
    assert bridge["bridge"].action_cutout_gallery({"flag": "bogus"})["status"] == "invalid"
    # the measurement was remembered: a second look downloads nothing
    bridge["cloud"].files.clear()
    again = bridge["bridge"].action_cutout_gallery({"measure": True})
    assert again["measured"] == 0 and again["counts"]["white"] == 1


def test_a_try_waits_and_replaces_nothing(bridge):
    b = bridge["bridge"]
    out = b.action_recut_try({"id": bridge["ids"]["Almarai Fresh Milk"], "method": "photoroom"})
    assert out["status"] == "success" and out["clean"] is True and out["can_apply"] is True
    assert out["preview"] == f"/api/cutout/preview/{out['token']}" and out["paid_calls"] == 1
    assert (bridge["tmp"] / "recut" / f"{out['token']}_preview.png").exists()
    assert bridge["cloud"].uploads == [] and bridge["db"].recut_entries() == []
    assert bridge["sheet"].value(2, "Drive Image Link") == MILK_URL
    local = b.action_recut_try({"id": bridge["ids"]["Almarai Fresh Milk"], "method": "local"})
    assert local["status"] == "success" and bridge["iso"].calls[-1]["bg_method"] == "rembg"
    assert b.action_recut_try({"id": bridge["ids"]["Almarai Fresh Milk"], "method": "magic"})["status"] == "invalid"
    assert b.action_recut_try({"id": 999999999, "method": "photoroom"})["error_code"] == "not_published"
    bridge["iso"].error = "photoroom_402"
    failed = b.action_recut_try({"id": bridge["ids"]["Almarai Fresh Milk"], "method": "photoroom"})
    assert failed == {"status": "failed", "error_code": "photoroom_402", "paid_calls": 1}


def test_the_local_cut_needs_rembg(bridge, monkeypatch):
    import image_processor
    monkeypatch.setattr(image_processor, "local_methods_available", lambda: {"grabcut": True, "rembg": False})
    out = bridge["bridge"].action_recut_try({"id": bridge["ids"]["Almarai Fresh Milk"], "method": "local"})
    assert out == {"status": "failed", "error_code": "local_missing"} and bridge["iso"].calls == []


def test_apply_publishes_a_new_version_through_the_outbox_and_logs_it(bridge):
    b = bridge["bridge"]
    tried = b.action_recut_try({"id": bridge["ids"]["Almarai Fresh Milk"], "method": "photoroom"})
    out = b.action_recut_apply({"token": tried["token"]})
    assert out["status"] == "success" and out["rows"] == [2] and out["sheet"] == {"written": 1, "pending": 0,
                                                                                  "conflict": 0}
    new_url = out["new_url"]
    assert "/v1800000" in new_url and bridge["sheet"].value(2, "Drive Image Link") == new_url
    assert bridge["sheet"].value(6, "Drive Image Link") == "needs_review:" + MILK_URL     # a review cell stays
    master = bridge["db"].published_master(bridge["ids"]["Almarai Fresh Milk"])
    assert master["cloudinary_url"] == new_url and master["master_background"] == "transparent"
    (entry,) = bridge["db"].recut_entries()
    assert (entry["origin"], entry["status"], entry["old_url"]) == ("gallery", "done", MILK_URL)
    assert not (bridge["tmp"] / "recut" / f"{tried['token']}.png").exists()             # used once
    assert b.action_recut_apply({"token": tried["token"]}) == {"status": "failed", "error_code": "expired"}

    undone = b.action_recut_undo({"log_id": entry["id"]})
    assert undone["status"] == "success" and undone["sheet"]["written"] == 1
    assert bridge["sheet"].value(2, "Drive Image Link") == MILK_URL.replace("/q_auto,f_auto/", "/" + NEW)
    assert bridge["db"].published_master(bridge["ids"]["Almarai Fresh Milk"])["cloudinary_url"] == MILK_URL
    assert b.action_recut_undo({"log_id": entry["id"]}) == {"status": "failed", "error_code": "status_undone"}
    assert b.action_recut_undo({"log_id": "x"})["status"] == "invalid"


def test_apply_never_writes_over_a_cell_changed_by_hand(bridge, monkeypatch):
    b = bridge["bridge"]
    tried = b.action_recut_try({"id": bridge["ids"]["Almarai Laban"], "method": "photoroom"})
    sheet = bridge["sheet"]
    real = bridge["gs"].flush_outbox

    def edit_then_flush(ws, *a, **k):
        ws.set(3, 6, "https://shop.ae/owner.jpg")             # the owner pastes another picture meanwhile
        return real(ws, *a, **k)

    monkeypatch.setattr(bridge["gs"], "flush_outbox", edit_then_flush)
    out = b.action_recut_apply({"token": tried["token"]})
    assert out["status"] == "success" and out["sheet"] == {"written": 0, "pending": 0, "conflict": 1}
    assert sheet.value(3, "Drive Image Link") == "https://shop.ae/owner.jpg"


def test_apply_refuses_a_cut_whose_background_was_not_removed_and_a_changed_product(bridge):
    b = bridge["bridge"]
    bridge["iso"].flags = ["opaque_fill"]
    tried = b.action_recut_try({"id": bridge["ids"]["Almarai Fresh Milk"], "method": "photoroom"})
    assert tried["can_apply"] is False
    assert b.action_recut_apply({"token": tried["token"]}) == {"status": "failed", "error_code": "not_publishable"}
    bridge["iso"].flags = ["dark_halo"]                    # a presentation flag: the owner may publish it after a look
    tried = b.action_recut_try({"id": bridge["ids"]["Almarai Fresh Milk"], "method": "photoroom"})
    assert tried["can_apply"] is True
    master = bridge["db"].published_master(bridge["ids"]["Almarai Fresh Milk"])
    assert bridge["db"].save_product_resolution(master["barcode"], master["product_name"], master["brand"],
                                                "https://x/y.jpg", JUICE_URL, verification_status="human_approved",
                                                sku_key=master["sku_key"])
    assert b.action_recut_apply({"token": tried["token"]}) == {"status": "failed", "error_code": "changed_meanwhile"}
    assert bridge["cloud"].uploads == []


def test_keep_the_old_one_discards_the_try(bridge):
    b = bridge["bridge"]
    tried = b.action_recut_try({"id": bridge["ids"]["Almarai Fresh Milk"], "method": "photoroom"})
    assert b.action_recut_discard({"token": tried["token"]}) == {"status": "success", "removed": True}
    assert b.action_recut_discard({"token": "../../.env"}) == {"status": "success", "removed": False}
    assert b.action_recut_apply({"token": tried["token"]})["error_code"] == "expired"
    assert bridge["cloud"].uploads == []


# ---------------------------------------------------------------------------
# The Laravel app through its HTTP kernel (a stub bridge)
# ---------------------------------------------------------------------------

TOKEN = "ab" * 16


@pytest.fixture
def cutout_app(app_env, tmp_path):
    calls = tmp_path / "cq_calls.txt"
    stub = tmp_path / "cq_bridge.py"
    stub.write_text(
        "import base64, json, os, sys\n"
        "action = sys.argv[1] if len(sys.argv) > 1 else ''\n"
        "params = json.loads(base64.b64decode(sys.argv[2]).decode('utf-8')) if len(sys.argv) > 2 else {}\n"
        f"open({str(calls)!r}, 'a').write(json.dumps([action, params]) + '\\n')\n"
        "mode = os.environ.get('LQ_STUB_MODE')\n"
        "if mode == 'down':\n"
        "    print(json.dumps({'status': 'failed', 'error': 'stub bridge is down'}))\n"
        "elif mode == 'credit':\n"
        "    print(json.dumps({'status': 'failed', 'error_code': 'photoroom_402', 'paid_calls': 1}))\n"
        "elif action == 'cutout_gallery':\n"
        "    print(json.dumps({'status': 'success', 'items': [], 'counts': {'all': 0}, 'total': 0, 'page': 1,\n"
        "                      'pages': 1, 'unchecked': 0, 'measured': 0, 'replaced': [], 'methods': {}}))\n"
        "elif action == 'recut_try':\n"
        f"    print(json.dumps({{'status': 'success', 'token': {TOKEN!r}, 'preview': '/api/cutout/preview/{TOKEN}',\n"
        "                      'flags': [], 'clean': True, 'can_apply': True, 'raw': 'never shown'}))\n"
        "else:\n"
        "    print(json.dumps({'status': 'success'}))\n", encoding="utf-8")
    preview = ROOT / "temp" / "recut" / f"{TOKEN}_preview.png"
    preview.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", (8, 8), (200, 0, 0, 255)).save(preview)
    yield {"env": dict(app_env["env"], CLI_BRIDGE_PATH=str(stub)), "calls": calls}
    preview.unlink(missing_ok=True)


def calls_of(app):
    return [json.loads(line) for line in app["calls"].read_text(encoding="utf-8").splitlines()] \
        if app["calls"].exists() else []


def test_the_page_renders_and_runs_nothing(cutout_app):
    (page,) = _kernel(cutout_app["env"], [["GET", "/cutout-check", {}]])
    assert page["status"] == 200
    body = page["body"]
    assert 'id="cqApp"' in body and "فحص القص" in body and "js/review/cutout.js" in body
    config = json.loads(__import__("html").unescape(body.split('data-config="', 1)[1].split('"', 1)[0]))
    assert config["urls"]["gallery"].endswith("/api/cutout/gallery") and config["flags"][0] == "white"
    assert calls_of(cutout_app) == []


def test_the_endpoints_relay_and_validate(cutout_app):
    env = cutout_app["env"]
    gallery, bad_flag, tried, bad_try, bad_apply, apply_ok, undo_bad = _kernel(env, [
        ["GET", "/api/cutout/gallery?flag=dark_halo&page=2&measure=0", {}],
        ["GET", "/api/cutout/gallery?flag=<script>", {}],
        ["POST", "/api/cutout/try", {"id": "5", "method": "photoroom"}],
        ["POST", "/api/cutout/try", {"id": "5", "method": "magic"}],
        ["POST", "/api/cutout/apply", {"token": "../../.env"}],
        ["POST", "/api/cutout/apply", {"token": TOKEN}],
        ["POST", "/api/cutout/undo", {"log_id": "-1"}]])
    assert gallery["status"] == 200 and bad_flag["status"] == 200
    assert tried["status"] == 200 and "raw" not in json.loads(tried["body"])
    assert bad_try["status"] == 422 and bad_apply["status"] == 422 and undo_bad["status"] == 422
    assert apply_ok["status"] == 200
    assert calls_of(cutout_app) == [["cutout_gallery", {"page": 2, "measure": False, "flag": "dark_halo"}],
                                    ["cutout_gallery", {"page": 1, "measure": True}],
                                    ["recut_try", {"id": 5, "method": "photoroom"}],
                                    ["recut_apply", {"token": TOKEN}]]


def test_a_failure_is_said_in_plain_arabic(cutout_app):
    (credit,) = _kernel(dict(cutout_app["env"], LQ_STUB_MODE="credit"),
                        [["POST", "/api/cutout/try", {"id": "5", "method": "photoroom"}]])
    body = json.loads(credit["body"])
    assert credit["status"] == 409 and body["error"] == "رصيد PhotoRoom خلص أو الاشتراك موقوف." and body["paid_calls"] == 1
    (down,) = _kernel(dict(cutout_app["env"], LQ_STUB_MODE="down"), [["GET", "/api/cutout/gallery", {}]])
    assert down["status"] == 500 and "ما قدرنا نقرا الصور المنشورة" in json.loads(down["body"])["error"]
    assert "stub" not in down["body"]


def test_the_preview_file_is_served_only_for_a_token(cutout_app):
    ok, missing, bad = _kernel(cutout_app["env"], [["GET", f"/api/cutout/preview/{TOKEN}", {}],
                                                    ["GET", "/api/cutout/preview/" + "cd" * 16, {}],
                                                    ["GET", "/api/cutout/preview/..%2F.env", {}]])
    assert ok["status"] == 200 and missing["status"] == 404 and bad["status"] == 404


def test_the_posts_need_the_csrf_token(cutout_app):
    env = dict(cutout_app["env"], APP_ENV="local")
    (response,) = _kernel(env, [["POST", "/api/cutout/apply", {"token": TOKEN}]])
    assert response["status"] == 419 and calls_of(cutout_app) == []
