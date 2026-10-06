"""«تراجع عن الرفض»: a reviewer's rejection can be taken back (the owner rejected an image only to try the button).

- local_cache_db.undo_rejection removes one rejected_images row (sku_key + image URL), marks the matching
  review_decisions row undone (undone_at), gives a WRONG_BRAND rejection's count back to the store spelling it
  counted against (learned_alias) and puts the excluded candidate back among the suggestions;
- the review stats (get_review_decisions / review_stats) and the learned brand sources skip an undone rejection;
- cli_bridge 'undo_reject' and POST /api/review/undo-reject (CSRF like every POST) on the real test database;
- the review screen lists the product's rejected images with «تراجع عن الرفض» and asks «ترجع هالصورة للاقتراحات؟».
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from catalog_match import learning
from catalog_match.identity import build_sku_spec

ROOT = Path(__file__).resolve().parents[1]
DASH = ROOT / "dashboard"
PHP = shutil.which("php")
NODE = shutil.which("node")
CORE_JS = DASH / "public" / "js" / "review" / "core.js"
SINGLE_JS = DASH / "public" / "js" / "review" / "single.js"
APP_JS = DASH / "public" / "js" / "review" / "app.js"

ROW = 931019
NAME, BRAND = "UNDOTEST FANCY TUNA WATER 170GM", "UNDOTEST"
SKU = build_sku_spec({"name": NAME, "brand": BRAND, "size": "170GM"}).sku_key
URL = "https://www.example-grocer.com/media/undotest-fancy-tuna-water-170g.jpg?w=800"
OTHER = "https://www.example-grocer.com/media/undotest-fancy-tuna-oil-170g.jpg"
PAGE = "https://www.example-grocer.com/en/undotest-fancy-tuna-water-170g"
CANDIDATES = [
    {"url": URL, "title": "Undotest Fancy Tuna in Water 170g", "status": "preselected", "page_url": PAGE,
     "domain": "www.example-grocer.com", "reasons": ["tier T1", "warn:brand_spelling:Undo Test"],
     "evidence": {"tier": 1, "page_domain": "example-grocer.com"}, "vlm": {"decision": "UNSURE"}},
    {"url": OTHER, "title": "Undotest Fancy Tuna in Oil 170g", "status": "eligible", "page_url": PAGE + "-oil",
     "domain": "www.example-grocer.com", "reasons": ["tier T2"], "evidence": {"tier": 2}},
]


def _sql(db, statement, params=()):
    conn = db.get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(statement, params)
            rows = cur.fetchall()
        conn.commit()
        return rows
    finally:
        conn.close()


def _wipe(db):
    _sql(db, "DELETE FROM review_decisions WHERE sku_key = %s OR brand = %s", (SKU, BRAND))
    _sql(db, "DELETE FROM rejected_images WHERE sku_key = %s", (SKU,))
    _sql(db, "DELETE FROM curation_candidates WHERE `row_number` = %s OR sku_key = %s", (ROW, SKU))
    _sql(db, "DELETE FROM learned_brand_aliases WHERE sheet_brand = %s", (BRAND,))
    learning.clear_cache()


@pytest.fixture
def db(mariadb_or_skip):
    _wipe(mariadb_or_skip)
    yield mariadb_or_skip
    _wipe(mariadb_or_skip)


def _reject(db, reason="WRONG_BRAND", url=URL):
    """What cli_bridge.action_reject_image records: the rejected_images row, the review row, the excluded candidate."""
    import cli_bridge

    assert db.add_rejected_image(SKU, url, page_url=PAGE, reason_code=reason)
    cli_bridge._record_review("rejected", {"brand": BRAND, "product_name": NAME}, ROW, SKU, url, reason_code=reason)
    db.exclude_curation_candidate(ROW, url, sku_key=SKU)


def _decisions(db):
    return _sql(db, "SELECT action, image_url, reason_code, undone_at, learned_alias FROM review_decisions "
                    "WHERE sku_key = %s ORDER BY id", (SKU,))


def test_the_schema_has_the_undo_columns(db):
    columns = {r["COLUMN_NAME"] for r in _sql(db, "SELECT COLUMN_NAME FROM information_schema.COLUMNS WHERE "
                                                  "TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'review_decisions'")}
    assert {"undone_at", "learned_alias"} <= columns
    assert db.init_db()                                  # idempotent: the migration runs again without error


def test_undo_removes_the_rejection_marks_the_decision_and_brings_the_image_back(db):
    assert db.save_curation_candidates(ROW, NAME, BRAND, CANDIDATES, sku_key=SKU, run_id="undo-run") is True
    db.add_review_decision("approved", sku_key="undotest-other", brand=BRAND, page_domain="example-grocer.com")
    _reject(db)
    (decision,) = _decisions(db)
    assert decision["learned_alias"] == "Undo Test" and decision["undone_at"] is None
    alias = _sql(db, "SELECT approvals, rejections FROM learned_brand_aliases WHERE sheet_brand = %s", (BRAND,))[0]
    assert (alias["approvals"], alias["rejections"]) == (0, 1)        # the WRONG_BRAND rejection counted against it
    assert db.get_rejections(SKU)[0] == [URL]
    assert {c["image_url"]: c["status"] for c in db.get_curation_candidates(ROW, sku_key=SKU)}[URL] == "excluded"
    rows = {(b, d): (ok, bad) for b, d, ok, bad in db.get_brand_source_counts() if b == BRAND}
    assert rows[(BRAND, "example-grocer.com")] == (1, 1)

    # the screen sends the URL as it showed it; www / query differences are the same image (url_norm)
    out = db.undo_rejection(SKU, URL.replace("?w=800", "?w=1200"), row_number=ROW)
    assert out == {"removed": 1, "reason_code": "WRONG_BRAND", "decision_undone": True, "alias_restored": True,
                   "still_rejected": False, "candidates_restored": 1}
    assert db.get_rejections(SKU) == ([], [])                          # the next search may find it again
    (decision,) = _decisions(db)
    assert decision["undone_at"] is not None
    alias = _sql(db, "SELECT approvals, rejections FROM learned_brand_aliases WHERE sheet_brand = %s", (BRAND,))[0]
    assert (alias["approvals"], alias["rejections"]) == (0, 0)
    assert {c["image_url"]: c["status"] for c in db.get_curation_candidates(ROW, sku_key=SKU)}[URL] == "eligible"
    # the stats and the learned sources no longer count it
    assert all(r["sku_key"] != SKU for r in db.get_review_decisions())
    rows = {(b, d): (ok, bad) for b, d, ok, bad in db.get_brand_source_counts() if b == BRAND}
    assert rows[(BRAND, "example-grocer.com")] == (1, 0)
    # nothing left to undo
    assert db.undo_rejection(SKU, URL)["removed"] == 0


def test_one_undo_takes_back_one_rejection(db):
    _reject(db, "WRONG_SIZE")
    _reject(db, "WRONG_SIZE")
    first = db.undo_rejection(SKU, URL, row_number=ROW)
    assert (first["removed"], first["still_rejected"], first["alias_restored"]) == (1, True, False)
    assert db.get_rejections(SKU)[0] == [URL]
    assert [d["undone_at"] is not None for d in _decisions(db)] == [False, True]       # the latest one
    assert db.undo_rejection(SKU, URL)["still_rejected"] is False


def test_review_stats_skip_an_undone_row_even_when_handed_one():
    import local_cache_db

    rows = [{"id": 1, "created_at": "2026-10-05 10:00:00", "action": "rejected", "sku_key": "s1", "brand": "B",
             "engine_decision": "REVIEW_PRESELECTED", "was_preselected": 1, "reason_code": "WRONG_SIZE",
             "page_domain": "x.ae", "undone_at": "2026-10-05 10:05:00"},
            {"id": 2, "created_at": "2026-10-05 10:01:00", "action": "approved", "sku_key": "s2", "brand": "B",
             "engine_decision": "REVIEW_PRESELECTED", "was_preselected": 1, "page_domain": "x.ae"}]
    stats = local_cache_db.review_stats(rows)
    (brand,) = [b for b in stats["brands"] if b["brand"] == "B"]
    assert (brand["reviewed_skus"], brand["prechecked"], brand["accepted"]) == (1, 1, 1)


def test_the_bridge_action(db):
    import cli_bridge

    assert cli_bridge.ACTIONS["undo_reject"] is cli_bridge.action_undo_reject
    assert cli_bridge.action_undo_reject({"sku_key": SKU})["status"] == "error"
    assert cli_bridge.action_undo_reject({"image_url": URL})["status"] == "error"
    _reject(db, "NOT_PACKSHOT")
    out = cli_bridge.action_undo_reject({"sku_key": SKU, "image_url": URL, "row_number": ROW})
    assert out["status"] == "success" and out["removed"] == 1 and out["decision_undone"] is True
    again = cli_bridge.action_undo_reject({"sku_key": SKU, "image_url": URL})
    assert again["status"] == "not_found" and again["removed"] == 0


def test_a_database_error_is_a_plain_failure(monkeypatch):
    import cli_bridge

    def broken(*a, **k):
        raise RuntimeError("secret detail")

    monkeypatch.setattr(cli_bridge.local_cache_db, "undo_rejection", broken)
    out = cli_bridge.action_undo_reject({"sku_key": SKU, "image_url": URL})
    assert out["status"] == "failed" and "secret detail" not in json.dumps(out)


# ---------------------------------------------------------------------------
# POST /api/review/undo-reject through the Laravel kernel, the real bridge and the test database
# ---------------------------------------------------------------------------

@pytest.fixture
def app(db, tmp_path):
    from test_laqta_health import VENDOR_AUTOLOAD

    if PHP is None or not VENDOR_AUTOLOAD.exists():
        pytest.skip("php or dashboard/vendor is not installed")
    compiled = tmp_path / "views"
    compiled.mkdir()
    env = dict(os.environ)
    env.update({
        "APP_ENV": "testing", "APP_KEY": "base64:" + "A" * 43 + "=", "APP_DEBUG": "true",
        "SESSION_DRIVER": "array", "CACHE_STORE": "array", "LOG_CHANNEL": "stderr", "VIEW_COMPILED_PATH": str(compiled),
        "DB_CONNECTION": "mariadb", "DB_HOST": os.getenv("DB_HOST", "127.0.0.1"), "DB_PORT": os.getenv("DB_PORT", "3306"),
        "DB_DATABASE": os.environ["DB_DATABASE"], "DB_USERNAME": os.getenv("DB_USERNAME", "root"),
        "DB_PASSWORD": os.getenv("DB_PASSWORD", ""), "CLI_BRIDGE_PATH": str(ROOT / "cli_bridge.py"),
        "PYTHON_PATH": sys.executable,
    })
    return {"db": db, "env": env}


def _kernel(env, requests):
    from test_laqta_health import _kernel as kernel

    return kernel(env, requests)


def test_the_route_undoes_a_rejection_in_the_database(app):
    db, env = app["db"], app["env"]
    _reject(db, "WRONG_PRODUCT")
    bad, ok, gone = _kernel(env, [
        ["POST", "/api/review/undo-reject", {"sku_key": SKU, "image_url": "javascript:alert(1)"}],
        ["POST", "/api/review/undo-reject", {"sku_key": SKU, "image_url": URL, "row_number": str(ROW)}],
        ["POST", "/api/review/undo-reject", {"sku_key": SKU, "image_url": URL}],
    ])
    assert bad["status"] == 422 and json.loads(bad["body"])["status"] == "failed"
    body = json.loads(ok["body"])
    assert ok["status"] == 200 and body["status"] == "success" and body["decision_undone"] is True
    assert "«دوّر مرة ثانية»" in body["message"]
    assert gone["status"] == 404 and json.loads(gone["body"])["status"] == "not_found"
    assert db.get_rejections(SKU) == ([], [])
    assert [d["undone_at"] is not None for d in _decisions(db)] == [True]


def test_the_route_needs_the_csrf_token_like_every_post(app):
    db = app["db"]
    _reject(db, "WRONG_PRODUCT")
    (response,) = _kernel(dict(app["env"], APP_ENV="local"),
                          [["POST", "/api/review/undo-reject", {"sku_key": SKU, "image_url": URL}]])
    assert response["status"] == 419
    assert db.get_rejections(SKU)[0] == [URL]


# ---------------------------------------------------------------------------
# The review screen
# ---------------------------------------------------------------------------

def test_the_route_is_registered_and_the_screen_knows_it():
    routes = (DASH / "routes" / "web.php").read_text(encoding="utf-8")
    assert "Route::post('/api/review/undo-reject', [ReviewController::class, 'undoReject']);" in routes
    assert "validateCsrfTokens" not in (DASH / "bootstrap" / "app.php").read_text(encoding="utf-8")
    review = (DASH / "app" / "Http" / "Controllers" / "ReviewController.php").read_text(encoding="utf-8")
    assert "'undoReject' => url('/api/review/undo-reject')," in review and "PythonBridge::run('undo_reject'" in review
    assert "undoReject: '/api/review/undo-reject'" in APP_JS.read_text(encoding="utf-8")
    products = (DASH / "app" / "Http" / "Controllers" / "ProductController.php").read_text(encoding="utf-8")
    assert "$prod['rejected_images']" in products
    single = SINGLE_JS.read_text(encoding="utf-8")
    # asked in the page (R.ask, the in-page confirmation of the review screen), never with window.confirm
    assert "R.ask({ title: R.UNDO_REJECT_CONFIRM" in single and "rejectedPanel(item)" in single
    assert "root.confirm(" not in single


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_screen_lists_rejected_images_and_sends_the_bound_product():
    script = (
        "globalThis.window = globalThis;\n"
        + CORE_JS.read_text(encoding="utf-8") + "\n"
        + "const R = globalThis.LaqtaReview;\n"
        + "const product = { rejected_images: [{ url: 'https://a.ae/1.jpg', reason_code: 'WRONG_SIZE' },"
          " { url: 'javascript:x' }], curation_candidates: [{ image_url: 'https://a.ae/1.jpg', status: 'excluded' }] };\n"
        + "const listed = R.rejectedImages(product, new Set(['https://a.ae/1.jpg', 'https://a.ae/2.jpg']),"
          " new Map([['https://a.ae/2.jpg', 'WRONG_BRAND']]));\n"
        + "const body = R.undoRejectBody({ sku_key: 'k1', row_number: 7, product_name: 'X' }, 'https://a.ae/1.jpg');\n"
        + "R.forgetRejection(product, 'https://a.ae/1.jpg');\n"
        + "console.log(JSON.stringify({ listed, body, label: R.UNDO_REJECT_LABEL, confirm: R.UNDO_REJECT_CONFIRM,"
          " left: product.rejected_images, status: product.curation_candidates[0].status }));\n")
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as fh:
        fh.write(script)
        path = fh.name
    try:
        result = subprocess.run([NODE, path], capture_output=True, text=True, timeout=60, encoding="utf-8")
    finally:
        os.unlink(path)
    assert result.returncode == 0, result.stderr[-2000:]
    out = json.loads(result.stdout.strip().splitlines()[-1])
    assert out["listed"] == [{"url": "https://a.ae/1.jpg", "reason_code": "WRONG_SIZE"},
                             {"url": "https://a.ae/2.jpg", "reason_code": "WRONG_BRAND"}]
    assert out["body"] == {"sku_key": "k1", "image_url": "https://a.ae/1.jpg", "row_number": 7}
    assert (out["label"], out["confirm"]) == ("تراجع عن الرفض", "ترجع هالصورة للاقتراحات؟")
    assert out["left"] == [{"url": "javascript:x"}] and out["status"] == "eligible"
