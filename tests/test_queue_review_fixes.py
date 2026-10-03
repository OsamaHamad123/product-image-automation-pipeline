"""Review fixes of the queue package (P4a, wp/p4-queue-fix).

- A finished task forgets why it was queued: a stale task_kind='relink' or requeue_reason='VERIFIER_RECHECK' never
  overrides a reviewer's later decision, and a relink never writes an image a reviewer rejected.
- Re-verification only parks or returns rows that still have candidates; a recheck that finds nothing for a row
  without candidates is a normal NOT_FOUND. Rechecks are requeued only when the worker is about to claim, and go
  back to review when the run stops before reaching them.
- Rows the old worker left pending + PROVIDER_DOWN without a date are claimed and counted.
- A GTIN row whose size is only in the SIZE column gets its own approval relinked.

Real-MariaDB tests use the test database (conftest) and skip when MariaDB is down.
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import pytest

ROW = 963000
SKU = "p4f-sku-"

ROOT = Path(__file__).resolve().parents[1]
DASH = ROOT / "dashboard"
QUEUE_STATS = DASH / "app" / "Services" / "QueueStats.php"
PHP = shutil.which("php")
NODE = shutil.which("node")
NEEDS_PHP = pytest.mark.skipif(PHP is None, reason="php is not installed")
NEEDS_NODE = pytest.mark.skipif(NODE is None, reason="node is not installed")


def _queue_stats(calls: str):
    """QueueStats pure functions under the PHP CLI: $out (assigned by calls) as JSON."""
    service = str(QUEUE_STATS).replace("\\", "/")
    script = (f"<?php\nrequire '{service}';\nuse App\\Services\\QueueStats;\n$out = [];\n{calls}\n"
              "echo json_encode($out, JSON_UNESCAPED_UNICODE);\n")
    with tempfile.NamedTemporaryFile("w", suffix=".php", delete=False, encoding="utf-8") as fh:
        fh.write(script)
        path = fh.name
    try:
        result = subprocess.run([PHP, path], capture_output=True, text=True, timeout=60, encoding="utf-8")
    finally:
        os.unlink(path)
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


def _sql(db, statement, params=()):
    conn = db.get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute(statement, params)
        rows = cur.fetchall()
        conn.commit()
        return rows
    finally:
        conn.close()


@pytest.fixture
def db(mariadb_or_skip):
    db = mariadb_or_skip

    def wipe():
        # the test database is ours alone (conftest)
        _sql(db, "DELETE FROM automation_queue")
        _sql(db, "DELETE FROM curation_candidates WHERE sku_key LIKE %s OR product_name LIKE 'P4F %%'", (SKU + "%",))
        _sql(db, "DELETE FROM resolved_products WHERE sku_key LIKE %s OR product_name LIKE 'P4F %%'", (SKU + "%",))
        _sql(db, "DELETE FROM rejected_images WHERE sku_key LIKE %s OR original_url LIKE 'https://p4f.example/%%' "
                 "OR original_url LIKE 'https://res.cloudinary.com/p4f/%%'", (SKU + "%",))
        _sql(db, "DELETE FROM product_failures WHERE sku_key LIKE %s OR product_name LIKE 'P4F %%'", (SKU + "%",))
        _sql(db, "DELETE FROM search_spend")
        _sql(db, "UPDATE automation_state SET status = 'idle', stop_requested = 0, pause_requested = 0, run_id = NULL, "
                 "notice = NULL WHERE `key` = 'active_session'")

    wipe()
    yield db
    wipe()


def _add(db, i, sku=None, **kw):
    db.add_to_queue(ROW + i, "", f"P4F Product {i}", "Brand", "q", sku_key=sku or f"{SKU}{i}", **kw)


def _row(db, i):
    return _sql(db, "SELECT * FROM automation_queue WHERE `row_number` = %s", (ROW + i,))[0]


LINK = "https://res.cloudinary.com/p4f/approved.png"
GTIN = "5449000000996"


def _prod(i, name, brand="P4F Brand", barcode="", size=""):
    return {"row_number": ROW + i, "product_name": name, "brand": brand, "barcode": barcode, "size": size,
            "existing_image_link": ""}


def _keys(prod):
    import main
    row = main.sku_row(prod["product_name"], prod["brand"], prod["barcode"], {"size": prod.get("size", "")})
    return main.compute_sku_key(row), main.compute_alt_sku_key(row)


def _candidates(db, i, sku, urls):
    assert db.save_curation_candidates(ROW + i, f"P4F Product {i}", "Brand",
                                       [{"url": u, "status": "eligible"} for u in urls], sku_key=sku)


@pytest.fixture
def sheet(monkeypatch):
    """google_sheets writes and the search, recorded. The sheet outbox has nothing for these rows (its real shape:
    a list of records)."""
    import google_sheets
    import image_search

    rec = {"links": [], "searches": []}
    monkeypatch.setattr(google_sheets, "update_image_link",
                        lambda ws, row, col, value, **k: rec["links"].append((row, value)) or True)
    monkeypatch.setattr(google_sheets, "update_product_metadata", lambda *a, **k: True)
    monkeypatch.setattr(google_sheets, "outbox_outcomes", lambda row_numbers=None, since_id=None, limit=500: [])

    def search(query, name, brand, trace=None, **kw):
        rec["searches"].append(kw)
        trace["outcome"] = rec.get("outcome", {"decision": "NOT_FOUND", "failure_code": "NO_RESULTS",
                                               "provider_health": []})
        return rec.get("best")

    monkeypatch.setattr(image_search, "search_best_product_image", search)
    return rec


def _work(main, task):
    return main.pre_cache_product_candidates(task, worksheet=object(), link_column_index=4, sleep=lambda s: None)


# ---------------------------------------------------------------------------
# C2: a reviewer's rejection is never overwritten by a stale relink
# ---------------------------------------------------------------------------

def _approved_before_the_barcode(db):
    """A row approved while it had no barcode; the owner then typed its valid barcode (the approval stays under the
    old key, the row's alternative key)."""
    before = _prod(0, "P4F Cola Regular 330ml")
    old_key = _keys(before)[0]
    assert db.save_product_resolution("", before["product_name"], before["brand"], "https://p4f.example/cola.jpg",
                                      LINK, verification_status="human_approved", approved_by="human", sku_key=old_key)
    after = dict(before, barcode=GTIN)
    new_key, alt_key = _keys(after)
    assert alt_key == old_key and new_key != old_key
    return after, new_key


def test_a_rejected_relinked_image_is_not_written_again_by_the_next_claim(db, sheet):
    """Probe C2: the row got its image by relink from the approval under its pre-barcode key; the reviewer rejected
    it (recorded and superseded under the row's key, the row back to pending REJECTED). The row kept
    task_kind='relink', so the next claim wrote the rejected image again without a search."""
    import main
    after, new_key = _approved_before_the_barcode(db)
    rows, _ = main.plan_enqueue([after])
    db.add_many_to_queue(rows)
    task = db.fetch_next_task("host:1")
    assert task["task_kind"] == "relink"
    assert _work(main, task) == "success" and sheet["links"] == [(ROW, LINK)] and sheet["searches"] == []
    assert (_row(db, 0)["status"], _row(db, 0)["task_kind"], _row(db, 0)["requeue_reason"]) == ("completed", None, None)

    # the reviewer rejects the published image (cli_bridge.action_reject_image: rejection, supersede, back to pending)
    assert db.add_rejected_image(new_key, LINK, reason_code="WRONG_PRODUCT")
    db.supersede_resolution(new_key, barcode=GTIN)
    db.update_task_status_by_row(ROW, "pending", "rejected by reviewer: WRONG_PRODUCT", failure_code="REJECTED",
                                 sku_key=new_key)
    sheet["links"].clear()
    task = db.fetch_next_task("host:1")
    assert task["task_kind"] is None
    _work(main, task)
    assert sheet["links"] == []                                   # the rejected image is not written again
    (kw,) = sheet["searches"]                                     # a normal search, the rejection excluded
    assert LINK in kw["exclude_urls"]


def test_a_relink_skips_an_approval_whose_image_a_reviewer_rejected(db, sheet):
    """The enqueue still sees the approval under the old key and plans a relink each night; the worker must not
    write an image rejected under the row's key (or its alternative key)."""
    import main
    after, new_key = _approved_before_the_barcode(db)
    assert db.add_rejected_image(new_key, LINK, reason_code="WRONG_PRODUCT")
    rows, _ = main.plan_enqueue([after])
    assert rows[0]["task_kind"] == "relink"
    db.add_many_to_queue(rows)
    _work(main, db.fetch_next_task("host:1"))
    assert sheet["links"] == [] and len(sheet["searches"]) == 1


def test_a_relink_skips_an_approval_whose_canvas_phash_was_rejected(offline, monkeypatch):
    import local_cache_db
    import main
    monkeypatch.setattr(local_cache_db, "get_rejections",
                        lambda key: (["https://elsewhere.example/x.jpg"], ["ffff0000ffff0003"]) if key == "alt" else ([], []))
    res = {"original_url": "https://p4f.example/a.jpg", "cloudinary_url": LINK, "perceptual_hash": "ffff0000ffff0000"}
    assert main._rejected_resolution(res, "gtin", "alt") is True                # 2 bits apart
    assert main._rejected_resolution(dict(res, perceptual_hash="0000ffff0000ffff"), "gtin", "alt") is False
    assert main._rejected_resolution(dict(res, perceptual_hash=None), "gtin", "alt") is False


def test_a_finished_task_forgets_why_it_was_queued(db):
    _add(db, 0, task_kind="relink", requeue_reason="APPROVED_IMAGE")
    task = db.fetch_next_task("host:1")
    db.update_task_status(task["id"], "pending", "Search providers unavailable", failure_code="PROVIDER_DOWN",
                          claim_id=task["worker_id"])
    row = _row(db, 0)
    assert (row["task_kind"], row["requeue_reason"]) == ("relink", "APPROVED_IMAGE")   # the same task tries again
    _sql(db, "UPDATE automation_queue SET next_attempt_at = NOW() - INTERVAL 1 MINUTE WHERE `row_number` = %s", (ROW,))
    task = db.fetch_next_task("host:1")
    db.update_task_status(task["id"], "completed", claim_id=task["worker_id"])
    row = _row(db, 0)
    assert (row["status"], row["task_kind"], row["requeue_reason"]) == ("completed", None, None)

    # a reviewer's write forgets them too (a row waiting for review after a recheck, rejected by the reviewer)
    _add(db, 1)
    _sql(db, "UPDATE automation_queue SET status = 'ready_for_review', requeue_reason = 'VERIFIER_RECHECK', "
             "task_kind = 'relink' WHERE `row_number` = %s", (ROW + 1,))
    db.update_task_status_by_row(ROW + 1, "pending", "rejected by reviewer: WRONG_PRODUCT", failure_code="REJECTED",
                                 sku_key=SKU + "1")
    row = _row(db, 1)
    assert (row["status"], row["task_kind"], row["requeue_reason"]) == ("pending", None, None)


# ---------------------------------------------------------------------------
# C4: re-verification never leaves an empty review row
# ---------------------------------------------------------------------------

def test_a_rejection_after_a_recheck_is_not_parked_back_into_review(db):
    """Probe C4 (outcome A): a recheck found a new pick, the reviewer rejected the last candidate (pending REJECTED),
    and the next run started with the label reader down: park_verifier_rechecks turned the row into an empty
    VERIFIER_DOWN review row because it still carried requeue_reason='VERIFIER_RECHECK'."""
    sku = SKU + "0"
    _add(db, 0)
    task = db.fetch_next_task("host:1")
    db.update_task_status(task["id"], "ready_for_review", failure_code="VERIFIER_DOWN", claim_id=task["worker_id"])
    _candidates(db, 0, sku, ["https://p4f.example/old.jpg"])
    assert db.requeue_verifier_down("run-1") == 1
    task = db.fetch_next_task("host:1")
    _candidates(db, 0, sku, ["https://p4f.example/new.jpg"])        # the recheck's new pick
    db.update_task_status(task["id"], "ready_for_review", claim_id=task["worker_id"])
    db.exclude_curation_candidate(ROW, "https://p4f.example/new.jpg", sku_key=sku)
    db.update_task_status_by_row(ROW, "pending", "rejected by reviewer: WRONG_PRODUCT", failure_code="REJECTED",
                                 sku_key=sku)
    assert db.park_verifier_rechecks() == 0
    row = _row(db, 0)
    assert (row["status"], row["failure_code"]) == ("pending", "REJECTED")


def test_only_rechecks_with_candidates_left_are_parked(db):
    for i in (0, 1):
        _add(db, i)
        _sql(db, "UPDATE automation_queue SET requeue_reason = 'VERIFIER_RECHECK', reverify_count = 1 "
                 "WHERE `row_number` = %s", (ROW + i,))
    _candidates(db, 0, SKU + "0", ["https://p4f.example/a.jpg"])
    _candidates(db, 1, SKU + "1", ["https://p4f.example/b.jpg"])
    db.exclude_curation_candidate(ROW + 1, "https://p4f.example/b.jpg", sku_key=SKU + "1")   # nothing left to review
    assert db.park_verifier_rechecks() == 1
    assert (_row(db, 0)["status"], _row(db, 0)["failure_code"], _row(db, 0)["reverify_count"]) == (
        "ready_for_review", "VERIFIER_DOWN", 0)
    assert _row(db, 1)["status"] == "pending"                       # searched by the worker instead


def test_a_recheck_without_candidates_that_finds_nothing_is_a_normal_not_found(db, sheet):
    """Probe C4 (outcome B): the recheck branch returned the row to review with no candidate; once reverify_count
    reached 2 it stayed there every night (never failed, never scheduled, never in product_failures)."""
    import main
    _add(db, 0)
    _sql(db, "UPDATE automation_queue SET requeue_reason = 'VERIFIER_RECHECK', reverify_count = 2 "
             "WHERE `row_number` = %s", (ROW,))
    assert _work(main, db.fetch_next_task("host:1")) == "failed"
    row = _row(db, 0)
    assert (row["status"], row["failure_code"], row["fail_count"]) == ("failed", "NO_RESULTS", 1)
    assert row["next_attempt_at"] is not None and row["requeue_reason"] is None
    assert _sql(db, "SELECT COUNT(*) AS n FROM product_failures WHERE sku_key = %s", (SKU + "0",))[0]["n"] == 1


def test_a_recheck_with_candidates_that_finds_nothing_waits_for_the_reviewer(db, sheet):
    import main
    _add(db, 0)
    _sql(db, "UPDATE automation_queue SET requeue_reason = 'VERIFIER_RECHECK', reverify_count = 1 "
             "WHERE `row_number` = %s", (ROW,))
    _candidates(db, 0, SKU + "0", ["https://p4f.example/a.jpg"])
    assert _work(main, db.fetch_next_task("host:1")) == "success"
    row = _row(db, 0)
    assert (row["status"], row["failure_code"], row["requeue_reason"]) == ("ready_for_review", "RECHECK_NOT_FOUND", None)
    assert db.requeue_verifier_down("run-2") == 0                  # a healthy reader found nothing: no third look


@NEEDS_PHP
def test_the_new_queue_codes_have_plain_arabic_texts():
    """The run page's «why» (QueueStats::FAILURE_TEXT) and the review screen (core.js FAILURE_TEXT) read the queue's
    failure code: a recheck that found nothing with a healthy reader is not «the label reader did not answer», and a
    link that could not be queued for the sheet had no text at all."""
    out = _queue_stats("$out = QueueStats::FAILURE_TEXT;")
    for code in ("RECHECK_NOT_FOUND", "SHEET_WRITE_FAILED"):
        assert re.search(r"[؀-ۿ]", out.get(code, "")), code
    assert out["RECHECK_NOT_FOUND"] != out["VERIFIER_DOWN"]
    core = (DASH / "public" / "js" / "review" / "core.js").read_text(encoding="utf-8")
    table = core[core.index("const FAILURE_TEXT = {"):]
    table = table[:table.index("};")]
    for code in ("RECHECK_NOT_FOUND", "SHEET_WRITE_FAILED"):
        assert re.search(code + r": '[^']*[؀-ۿ][^']*'", table), code


# ---------------------------------------------------------------------------
# C6: legacy PROVIDER_DOWN rows without a date
# ---------------------------------------------------------------------------

def test_a_legacy_provider_down_row_without_a_date_is_claimed_and_counted(db):
    """The old worker left rows pending + PROVIDER_DOWN; the migration added next_attempt_at as NULL. NOT(... AND
    next_attempt_at > NOW()) is NULL for them: begin_run counted the row, the claim and count_open_tasks did not, so
    the run ended with the row still waiting."""
    _add(db, 0)
    _sql(db, "UPDATE automation_queue SET status = 'pending', failure_code = 'PROVIDER_DOWN', next_attempt_at = NULL "
             "WHERE `row_number` = %s", (ROW,))
    assert db.begin_run("p4f-legacy") == 1
    assert db.count_open_tasks() == 1
    task = db.fetch_next_task("host:1")
    assert task is not None and task["row_number"] == ROW
    # a dated backoff still holds the row (the predicate only stopped dropping NULL)
    _add(db, 1)
    _sql(db, "UPDATE automation_queue SET status = 'pending', failure_code = 'PROVIDER_DOWN', "
             "next_attempt_at = NOW() + INTERVAL 2 HOUR WHERE `row_number` = %s", (ROW + 1,))
    assert db.fetch_next_task("host:1") is None
    assert db.count_open_tasks() == 1                     # the claimed row only; the parked one is beyond the horizon


# ---------------------------------------------------------------------------
# C11: a reviewer's status write survives a deadlock with the claim
# ---------------------------------------------------------------------------

def test_a_reviewer_status_write_is_retried_after_a_deadlock(offline, monkeypatch, fake_connection):
    """The claim scan and a multi-row result write can deadlock (InnoDB 1213 aborts one side). The worker's write
    retried; the reviewer's write (approve / reject / upload) did not, and the decision's queue status was lost."""
    import pymysql
    import local_cache_db

    calls = {"update": 0}

    def responder(sql, params):
        if sql.startswith("UPDATE automation_queue SET status"):
            calls["update"] += 1
            if calls["update"] == 1:
                raise pymysql.err.OperationalError(1213, "Deadlock found when trying to get lock")
        return None

    conns = []
    monkeypatch.setattr(local_cache_db, "get_db_connection", lambda: conns.append(fake_connection(responder)) or conns[-1])
    monkeypatch.setattr("time.sleep", lambda s: None)
    assert local_cache_db.update_task_status_by_row(12, "completed", sku_key="k") is True
    assert len(conns) == 2 and conns[1].commits == 1 and conns[0].commits == 0

    def lost(sql, params):
        raise pymysql.err.OperationalError(2013, "Lost connection to MySQL server during query")

    conns.clear()
    monkeypatch.setattr(local_cache_db, "get_db_connection", lambda: conns.append(fake_connection(lost)) or conns[-1])
    assert local_cache_db.update_task_status_by_row(12, "completed", sku_key="k") is False
    assert len(conns) == 1                                   # other errors are not retried


# ---------------------------------------------------------------------------
# C7: a GTIN row whose size is only in the SIZE column gets its own approval
# ---------------------------------------------------------------------------

MILK_GTIN = "6281007000024"


def _approve_gtin(db, prod, link=LINK, name=None):
    key = _keys(prod)[0]
    assert db.save_product_resolution(prod["barcode"], name or prod["product_name"], prod["brand"],
                                      "https://p4f.example/milk.jpg", link, verification_status="human_approved",
                                      approved_by="human", sku_key=key)
    return key


def test_a_gtin_row_with_a_size_column_gets_its_own_approval_relinked(db, sheet):
    """Probe C7: the stored resolution keeps only the name, so a size stated only in the SIZE column never matched,
    not even for the row's own approval: a paid search and a second human review instead of a free rewrite."""
    import main
    prod = _prod(0, "P4F Almarai Fresh Milk", brand="Almarai", barcode=MILK_GTIN, size="1L")
    _approve_gtin(db, prod)
    rows, stats = main.plan_enqueue([prod])
    assert rows[0]["task_kind"] == "relink" and stats["relink"] == 1
    db.add_many_to_queue(rows)
    assert _work(main, db.fetch_next_task("host:1")) == "success"
    assert sheet["links"] == [(ROW, LINK)] and sheet["searches"] == []
    # spelling of the name and brand does not matter, a different product under the same barcode does
    assert main._gtin_resolution_fits({"product_name": "p4f almarai  FRESH milk", "brand": "AL-MARAI"},
                                      "P4F Almarai Fresh Milk", "Al Marai", "1 L")
    assert not main._gtin_resolution_fits({"product_name": "P4F Almarai Laban", "brand": "Almarai"},
                                          "P4F Almarai Fresh Milk", "Almarai", "1L")


def test_rows_sharing_a_barcode_with_other_sizes_keep_the_identity_guard(db, sheet):
    """Two rows with one barcode and one name but 1L / 2L in the SIZE column: the barcode is wrong on one of them,
    so the name alone does not give either the other's image (the guard of the cache identity check)."""
    import main
    one = _prod(0, "P4F Almarai Fresh Milk", brand="Almarai", barcode=MILK_GTIN, size="1L")
    two = _prod(1, "P4F Almarai Fresh Milk", brand="Almarai", barcode=MILK_GTIN, size="2L")
    _approve_gtin(db, one)
    rows, _ = main.plan_enqueue([one, two])
    assert [r["task_kind"] for r in rows] == [None, None]
    # the worker checks the queue rows of the barcode as well
    db.add_many_to_queue(rows)
    assert main._servable_resolution(_keys(one)[0], _keys(one)[1], one["product_name"], "Almarai", "1L") is None
    _sql(db, "DELETE FROM automation_queue WHERE `row_number` = %s", (ROW + 1,))
    res = main._servable_resolution(_keys(one)[0], _keys(one)[1], one["product_name"], "Almarai", "1L")
    assert res is not None and res["cloudinary_url"] == LINK
    # another product under the same barcode never fits
    assert main._servable_resolution(_keys(one)[0], None, "P4F Almarai Laban", "Almarai", "1L") is None


# ---------------------------------------------------------------------------
# C5: rows waiting for review stay there when a run stops early
# ---------------------------------------------------------------------------

@pytest.fixture
def real_worker(db, monkeypatch, tmp_path):
    """run_worker_mode on the real queue with the sheet replaced (the search is replaced by each test)."""
    import config
    import google_sheets
    import main

    monkeypatch.chdir(tmp_path)
    os.makedirs("temp", exist_ok=True)
    real_sleep = time.sleep
    monkeypatch.setattr(time, "sleep", lambda s: real_sleep(0.01))
    monkeypatch.setattr(config, "DAILY_BUDGET_USD", 0)
    monkeypatch.setattr(config, "SERPER_CREDIT_STOP_SEARCHES", 3)
    monkeypatch.setattr(main, "_another_worker_running", lambda lock: False)
    monkeypatch.setattr(main, "load_run_config", lambda: None)
    monkeypatch.setattr(main, "check_verifier", lambda: "")
    monkeypatch.setattr(main, "_outage_notice", lambda worker_id, since, base=None, **k: base)
    monkeypatch.setattr(google_sheets, "get_sheets_client", lambda: object())
    monkeypatch.setattr(google_sheets, "open_worksheet", lambda client, name: object())
    monkeypatch.setattr(google_sheets, "find_link_column", lambda ws: 5)
    monkeypatch.setattr(google_sheets, "get_brand_mappings", lambda *a: {})
    monkeypatch.setattr(google_sheets, "init_async_queue", lambda *a: None)
    monkeypatch.setattr(google_sheets, "stop_async_queue", lambda: None)
    done = threading.Event()

    def watchdog():
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and not done.is_set():
            real_sleep(0.1)
        if not done.is_set():
            db.update_automation_state(status="pre_caching", stop_requested=1)

    threading.Thread(target=watchdog, daemon=True).start()
    yield main
    done.set()


def _verifier_down_review_row(db, i):
    _add(db, i)
    task = db.fetch_next_task("host:1")
    db.update_task_status(task["id"], "ready_for_review", failure_code="VERIFIER_DOWN", claim_id=task["worker_id"])
    _candidates(db, i, f"{SKU}{i}", [f"https://p4f.example/{i}.jpg"])


def test_a_run_stopped_by_the_budget_before_any_claim_keeps_the_review_rows(real_worker, db, monkeypatch):
    """Probe C5: today's spend already at the budget. The worker requeued the VERIFIER_DOWN review row (pending,
    priority 1) and then stopped on BUDGET_REACHED: the review count dropped from 1 to 0 until the next run."""
    import config
    main = real_worker
    _verifier_down_review_row(db, 0)
    _add(db, 1)                                                    # other work is waiting
    _sql(db, "INSERT INTO search_spend (day, run_id, provider, calls, usd) VALUES (CURDATE(), 'p4f', 'serper', 1, 5)")
    monkeypatch.setattr(config, "DAILY_BUDGET_USD", 1.0)
    monkeypatch.setattr(main, "pre_cache_product_candidates", lambda *a, **k: pytest.fail("nothing may be claimed"))
    main.run_worker_mode(report=False)
    assert main.LAST_WORKER["stop_reason"] == "budget_reached"
    row = _row(db, 0)
    assert (row["status"], row["failure_code"], row["reverify_count"]) == ("ready_for_review", "VERIFIER_DOWN", 0)
    assert db.get_ready_for_review_count() == 1
    assert db.get_automation_state()["status"] == "curation_pending"


def test_rechecks_the_run_did_not_reach_go_back_to_review(real_worker, db, monkeypatch):
    """The budget is reached after the first recheck: the second one waits for review again, not pending."""
    import config
    import local_cache_db
    main = real_worker
    _verifier_down_review_row(db, 0)
    _verifier_down_review_row(db, 1)
    monkeypatch.setattr(config, "DAILY_BUDGET_USD", 1.0)
    spent = iter([0.0])
    monkeypatch.setattr(local_cache_db, "spend_today", lambda: next(spent, 5.0))
    worked = []

    def recheck(task, *a, **k):
        worked.append(task["row_number"] - ROW)
        main._finish_task(task, "ready_for_review")
        return "success"

    monkeypatch.setattr(main, "pre_cache_product_candidates", recheck)
    main.run_worker_mode(report=False)
    assert worked == [0] and main.LAST_WORKER["stop_reason"] == "budget_reached"
    row = _row(db, 1)
    assert (row["status"], row["failure_code"], row["reverify_count"]) == ("ready_for_review", "VERIFIER_DOWN", 0)
    assert db.get_ready_for_review_count() == 2


# ---------------------------------------------------------------------------
# C8: the budget stop speaks Arabic everywhere and keeps the reader notice
# ---------------------------------------------------------------------------

VERIFIER_NOTICE = "VERIFIER_UNAVAILABLE: not_found (m); every result goes to human review"


def test_a_budget_stop_keeps_the_label_reader_notice_of_the_same_run(real_worker, db, monkeypatch):
    """The BUDGET_REACHED notice replaced the VERIFIER_* notice found at the start of the run: the owner was not told
    that every result of that run went to human review. Both stay, the stop reason first."""
    import config
    main = real_worker
    _add(db, 0)
    _sql(db, "INSERT INTO search_spend (day, run_id, provider, calls, usd) VALUES (CURDATE(), 'p4f', 'serper', 1, 5)")
    monkeypatch.setattr(config, "DAILY_BUDGET_USD", 1.0)
    monkeypatch.setattr(main, "check_verifier", lambda: VERIFIER_NOTICE)
    main.run_worker_mode(report=False)
    notice = db.get_automation_state()["notice"]
    parts = [p.split(":", 1)[0] for p in notice.split(" | ")]
    assert parts == ["BUDGET_REACHED", "VERIFIER_UNAVAILABLE"], notice
    assert main.LAST_WORKER["notice"] == notice


@NEEDS_PHP
def test_a_run_that_stopped_with_rows_left_does_not_say_nothing_waits():
    out = _queue_stats(f"""
$out['idle'] = QueueStats::phaseText('idle', 0, 0, 7);
$out['idle_empty'] = QueueStats::phaseText('idle', 0, 0, 0);
$out['review'] = QueueStats::phaseText('review', 0, 12, 1);
$out['review_only'] = QueueStats::phaseText('review', 0, 12);
$out['alert'] = QueueStats::alertText('idle', {json.dumps('BUDGET_REACHED: daily search budget 5.00 USD reached (spent 5.01) | ' + VERIFIER_NOTICE)}, false);
""")
    assert "7 منتجات بانتظار التشغيل التالي" in out["idle"] and "لا توجد منتجات بانتظار" not in out["idle"]
    assert out["idle_empty"] == "لا يوجد تشغيل حالياً، ولا توجد منتجات بانتظار المراجعة."
    assert out["review"] == "انتهى التحضير: 12 منتج بانتظار المراجعة، وفي الطابور منتج واحد بانتظار التشغيل التالي."
    assert out["review_only"] == "انتهى التحضير: 12 منتج بانتظار المراجعة."
    budget, reader = out["alert"].split(" | ")
    assert budget.startswith("بلغ صرف اليوم الميزانية اليومية") and reader.startswith("نموذج Gemini غير متاح")


@NEEDS_NODE
def test_the_layout_card_reads_the_budget_and_database_notices_in_arabic():
    """The sidebar run card's own map (used when /api/batch-status carries no Arabic alert) had no text for
    BUDGET_REACHED or DB_UNAVAILABLE."""
    from blade_scripts import inline_scripts
    layout = (DASH / "resources" / "views" / "layouts" / "laqta.blade.php").read_text(encoding="utf-8")
    script = re.sub(r"\{\{.*?\}\}", "''", inline_scripts(layout)[0])
    harness = ("globalThis.window = globalThis;\nglobalThis.document = { body: null, hidden: false, "
               "querySelector: () => null, querySelectorAll: () => [], addEventListener: () => {}, "
               "dispatchEvent: () => true };\n" + script + "\nconsole.log(JSON.stringify({"
               "budget: window.Laqta.plainNotice('BUDGET_REACHED: daily search budget 5.00 USD reached (spent 5.01)'),"
               "db: window.Laqta.describeRunStatus(window.Laqta.normalizeRunStatus("
               "{status: 'error', notice: 'DB_UNAVAILABLE: database unreachable'})).text}));")
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as fh:
        fh.write(harness)
        path = fh.name
    try:
        result = subprocess.run([NODE, path], capture_output=True, text=True, timeout=60, encoding="utf-8")
    finally:
        os.unlink(path)
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    assert out["budget"].startswith("بلغ صرف اليوم الميزانية اليومية للبحث")
    assert out["db"].startswith("تعذّر الوصول إلى قاعدة البيانات")


# ---------------------------------------------------------------------------
# C9: waiting for a PROVIDER_DOWN retry is not a stuck run
# ---------------------------------------------------------------------------

def test_the_worker_beats_while_it_waits_for_a_retry(offline, monkeypatch, tmp_path):
    """During a 10-20 minute PROVIDER_DOWN backoff the loop never wrote automation_state, so the dashboard showed
    «worker running, no progress» after 600 s and offered «fix stuck run» (which stops the worker)."""
    import config
    import google_sheets
    import local_cache_db
    import main

    monkeypatch.chdir(tmp_path)
    os.makedirs("temp", exist_ok=True)
    real_sleep = time.sleep
    monkeypatch.setattr(time, "sleep", lambda s: real_sleep(0.005))
    monkeypatch.setattr(config, "DAILY_BUDGET_USD", 0)
    for name, value in (("_another_worker_running", lambda lock: False), ("load_run_config", lambda: None),
                        ("check_verifier", lambda: ""), ("WAIT_HEARTBEAT_SECONDS", 0.05),
                        ("_outage_notice", lambda worker_id, since, base=None, **k: base),
                        ("_run_health", lambda worker_id, since: None)):
        monkeypatch.setattr(main, name, value, raising=name != "WAIT_HEARTBEAT_SECONDS")
    beats = []
    monkeypatch.setattr(main, "_refresh_state", lambda status, run_id=None, **kw: beats.append((status, kw)))
    monkeypatch.setattr(local_cache_db, "resume_automation", lambda: True)
    monkeypatch.setattr(local_cache_db, "get_automation_state",
                        lambda: {"pause_requested": 0, "stop_requested": 0, "run_id": "run-b"})
    monkeypatch.setattr(local_cache_db, "update_automation_state", lambda status, **kw: True)
    monkeypatch.setattr(local_cache_db, "get_ready_for_review_count", lambda: 0)
    monkeypatch.setattr(local_cache_db, "requeue_verifier_down", lambda run_id=None: 0)
    monkeypatch.setattr(local_cache_db, "park_verifier_rechecks", lambda: 0)
    monkeypatch.setattr(local_cache_db, "fetch_next_task", lambda worker_id: None)    # the row waits for its time
    until = time.monotonic() + 0.6
    monkeypatch.setattr(local_cache_db, "count_open_tasks", lambda: 1 if time.monotonic() < until else 0)
    monkeypatch.setattr(google_sheets, "get_sheets_client", lambda: object())
    monkeypatch.setattr(google_sheets, "open_worksheet", lambda client, name: object())
    monkeypatch.setattr(google_sheets, "find_link_column", lambda ws: 7)
    monkeypatch.setattr(google_sheets, "get_brand_mappings", lambda *a: {})
    monkeypatch.setattr(google_sheets, "init_async_queue", lambda *a: None)
    monkeypatch.setattr(google_sheets, "stop_async_queue", lambda: None)
    main.run_worker_mode(report=False)
    waiting = [kw for status, kw in beats if status == "pre_caching" and kw.get("current_product") == ""]
    assert len(waiting) >= 3, beats                               # one beat per interval while it waited
    assert main.LAST_WORKER["stop_reason"] is None


@NEEDS_PHP
def test_a_retry_wait_is_not_a_stuck_run_and_the_page_says_it_waits():
    import local_cache_db
    out = _queue_stats("""
$out['stuck'] = QueueStats::stuckReason('running', 'running', 'pre_caching', 0, 1200, 0);
$out['waiting'] = QueueStats::stuckReason('running', 'running', 'pre_caching', 0, 1200, 0, 540);
$out['busy'] = QueueStats::stuckReason('running', 'running', 'pre_caching', 1, 1200, 0, 540);
$out['text'] = QueueStats::phaseText('running', 0, 0, 3, 540);
$out['plain'] = QueueStats::phaseText('running', 0, 0, 3);
$out['minutes'] = array_map(fn ($s) => QueueStats::minutesText($s), [5, 60, 61, 125, 600, 1200]);
$out['horizon'] = QueueStats::RETRY_HORIZON_MINUTES;
""")
    assert "20 دقيقة" in out["stuck"]
    assert out["waiting"] == ""                                   # waiting for a retry, not stuck
    assert out["busy"] != ""                                      # a row being searched for 20 minutes is
    assert out["text"] == "مصادر البحث لم تستجب لبعض المنتجات؛ العامل ينتظر ويعيد المحاولة بعد 9 دقائق."
    assert out["plain"] == "جاري تحضير المرشحات…"
    assert out["minutes"] == ["دقيقة", "دقيقة", "دقيقتين", "3 دقائق", "10 دقائق", "20 دقيقة"]
    assert out["horizon"] == local_cache_db.OPEN_TASK_HORIZON_MINUTES


LARAVEL = PHP is not None and (DASH / "vendor" / "autoload.php").exists()


@pytest.mark.skipif(not LARAVEL, reason="php or dashboard/vendor is not installed")
def test_the_dashboard_reads_when_the_next_retry_is_due(db):
    """QueueStats::retryWaitS against the real queue: the earliest PROVIDER_DOWN retry within the horizon."""
    _add(db, 0)
    _add(db, 1)
    _add(db, 2)
    _sql(db, "UPDATE automation_queue SET failure_code = 'PROVIDER_DOWN', next_attempt_at = NOW() + INTERVAL 9 MINUTE "
             "WHERE `row_number` = %s", (ROW,))
    _sql(db, "UPDATE automation_queue SET failure_code = 'PROVIDER_DOWN', next_attempt_at = NOW() + INTERVAL 12 HOUR "
             "WHERE `row_number` = %s", (ROW + 1,))                  # parked for the next run: not waited for
    dash = str(DASH).replace("\\", "/")
    # this checkout's QueueStats (an optimised composer classmap may point to another copy of the app)
    script = (f"<?php\nrequire '{dash}/vendor/autoload.php';\nrequire_once '{dash}/app/Services/QueueStats.php';\n"
              f"$app = require '{dash}/bootstrap/app.php';\n"
              "$app->make(Illuminate\\Contracts\\Console\\Kernel::class)->bootstrap();\n"
              "echo json_encode(App\\Services\\QueueStats::retryWaitS());\n")
    env = dict(os.environ, APP_ENV="testing", APP_KEY="base64:" + "A" * 43 + "=", CACHE_STORE="array",
               SESSION_DRIVER="array", LOG_CHANNEL="stderr", DB_CONNECTION="mariadb",
               DB_HOST=os.getenv("DB_HOST", "127.0.0.1"), DB_PORT=os.getenv("DB_PORT", "3306"),
               DB_DATABASE=os.environ["DB_DATABASE"], DB_USERNAME=os.getenv("DB_USERNAME", "root"),
               DB_PASSWORD=os.getenv("DB_PASSWORD", ""))
    with tempfile.NamedTemporaryFile("w", suffix=".php", delete=False, encoding="utf-8") as fh:
        fh.write(script)
        path = fh.name
    try:
        def wait():
            result = subprocess.run([PHP, path], cwd=DASH, env=env, capture_output=True, text=True, timeout=120,
                                    encoding="utf-8")
            assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]
            return json.loads(result.stdout.strip().splitlines()[-1])
        assert 8 * 60 <= wait() <= 9 * 60
        _sql(db, "UPDATE automation_queue SET next_attempt_at = NULL WHERE `row_number` = %s", (ROW,))
        assert wait() is None
    finally:
        os.unlink(path)


# ---------------------------------------------------------------------------
# C10: failure records of one name and brand in two sizes
# ---------------------------------------------------------------------------

def _juice(i, size):
    return _prod(i, "P4F Juice", brand="B", size=size)


def _failures(db):
    return {r["barcode"]: (r["sku_key"], r["error_message"])
            for r in _sql(db, "SELECT * FROM product_failures WHERE product_name LIKE 'P4F %%'")}


@pytest.mark.parametrize("first", ["1L", "2L"])
def test_approving_one_size_keeps_the_other_sizes_failure(db, first):
    """delete_product_failure deleted LIKE '<key>%': approving the 1L removed ERR_..#<2L sku>; when the 2L failed
    first and holds the plain key, the exact delete of the display key removed the 2L's record."""
    one, two = _keys(_juice(0, "1L"))[0], _keys(_juice(1, "2L"))[0]
    order = [("1L", one), ("2L", two)] if first == "1L" else [("2L", two), ("1L", one)]
    for size, key in order:
        assert db.save_product_failure("", "P4F Juice", "B", f"NO_RESULTS: {size}", sku_key=key)
    assert len(_failures(db)) == 2
    db.delete_product_failure("", sku_key=one, product_name="P4F Juice", brand="B")     # the 1L was approved
    assert list(_failures(db).values()) == [(two, "NO_RESULTS: 2L")]
    # '_' is not a wildcard and a key is not a prefix: «P4F A» / «B» never touches «P4F A» / «Bread»
    assert db.save_product_failure("", "P4F A", "Bread", "NO_RESULTS: bread", sku_key=SKU + "bread")
    db.delete_product_failure("", sku_key=SKU + "a", product_name="P4F A", brand="B")
    db.delete_product_failure("ERR_P4F_A_B")
    assert "ERR_P4F_A_Bread" in _failures(db)


def test_the_products_list_shows_each_rows_own_failure(db, monkeypatch):
    """cli_bridge.get_products looked failures up by barcode and the ERR_ key only: the 2L row showed the 1L row's
    error and its own record (ERR_..#<2L sku>) was unreachable."""
    import cli_bridge
    import google_sheets
    rows = [dict(_juice(0, "1L"), barcode=""), dict(_juice(1, "2L"), barcode="")]
    one, two = _keys(rows[0])[0], _keys(rows[1])[0]
    assert db.save_product_failure("", "P4F Juice", "B", "NO_RESULTS: 1L", sku_key=one)
    assert db.save_product_failure("", "P4F Juice", "B", "ALL_CONFLICTED: 2L", sku_key=two)
    monkeypatch.setattr(cli_bridge, "_open_sheet", lambda: object())
    monkeypatch.setattr(google_sheets, "get_products", lambda ws: ([dict(r) for r in rows], 5))
    monkeypatch.setattr(google_sheets, "get_sheets_client", lambda: None)
    out = cli_bridge.action_get_products({})
    shown = {p["sku_key"]: (p["has_error"], p["error_message"]) for p in out["products"]}
    assert shown == {one: (True, "NO_RESULTS: 1L"), two: (True, "ALL_CONFLICTED: 2L")}
    db.delete_product_failure("", sku_key=two, product_name="P4F Juice", brand="B")        # the 2L was approved
    shown = {p["sku_key"]: p["has_error"] for p in cli_bridge.action_get_products({})["products"]}
    assert shown == {one: True, two: False}                     # not the 1L's error under the shared display key


@pytest.mark.skipif(not LARAVEL, reason="php or dashboard/vendor is not installed")
def test_retry_from_the_errors_page_maps_each_record_to_its_own_row(db, tmp_path):
    """ApiController::retryFailures could not map ERR_..#<sku_key> keys («not found in sheet»), and the shared display
    key requeued whichever row came last under it."""
    import sys
    rows = [_juice(1, "1L"), _juice(2, "2L")]
    for r in rows:
        r["sku_key"] = _keys(r)[0]
    one, two = rows[0]["sku_key"], rows[1]["sku_key"]
    assert db.save_product_failure("", "P4F Juice", "B", "NO_RESULTS: 1L", sku_key=one)
    assert db.save_product_failure("", "P4F Juice", "B", "NO_RESULTS: 2L", sku_key=two)
    keys = {sku: key for key, (sku, _) in _failures(db).items()}
    assert keys[two].endswith("#" + two)
    stub = tmp_path / "stub_bridge.py"
    stub.write_text("import json, sys\nprint(json.dumps({'status': 'success', 'products': "
                    + repr(rows) + "}, ensure_ascii=False))\n", encoding="utf-8")
    dash = str(DASH).replace("\\", "/")
    script = f"""<?php
require '{dash}/vendor/autoload.php';
require_once '{dash}/app/Services/QueueStats.php';
require_once '{dash}/app/Http/Controllers/ApiController.php';
$app = require '{dash}/bootstrap/app.php';
$kernel = $app->make(Illuminate\\Contracts\\Http\\Kernel::class);
$out = [];
foreach (json_decode($argv[1], true) as $keys) {{
    $request = Illuminate\\Http\\Request::create('/api/failures/retry', 'POST', ['barcodes' => $keys], [], [],
        ['HTTP_ACCEPT' => 'application/json']);
    $out[] = json_decode($kernel->handle($request)->getContent(), true);
}}
echo json_encode($out, JSON_UNESCAPED_UNICODE);
"""
    env = dict(os.environ, APP_ENV="testing", APP_KEY="base64:" + "A" * 43 + "=", CACHE_STORE="array",
               SESSION_DRIVER="array", LOG_CHANNEL="stderr", DB_CONNECTION="mariadb",
               DB_HOST=os.getenv("DB_HOST", "127.0.0.1"), DB_PORT=os.getenv("DB_PORT", "3306"),
               DB_DATABASE=os.environ["DB_DATABASE"], DB_USERNAME=os.getenv("DB_USERNAME", "root"),
               DB_PASSWORD=os.getenv("DB_PASSWORD", ""), CLI_BRIDGE_PATH=str(stub), PYTHON_PATH=sys.executable,
               VIEW_COMPILED_PATH=str(tmp_path))
    with tempfile.NamedTemporaryFile("w", suffix=".php", delete=False, encoding="utf-8") as fh:
        fh.write(script)
        path = fh.name
    try:
        result = subprocess.run([PHP, path, json.dumps([[keys[two]], [keys[one]]])], cwd=DASH, env=env,
                                capture_output=True, text=True, timeout=240, encoding="utf-8")
    finally:
        os.unlink(path)
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-3000:]
    first, second = json.loads(result.stdout.strip().splitlines()[-1])
    assert first["status"] == "success" and first["requeued"] == 1 and first["not_found"] == 0, first
    assert second["status"] == "success" and second["requeued"] == 1, second
    queued = {r["row_number"]: r["sku_key"] for r in _sql(db, "SELECT `row_number`, sku_key FROM automation_queue")}
    assert queued == {ROW + 2: two, ROW + 1: one}                 # each record requeued its own row
    assert _failures(db) == {}


def test_stopping_from_the_dashboard_returns_unclaimed_rechecks_to_review(db):
    """The dashboard's stop ends the worker process (its finally may not run); stop_run settles the queue."""
    _verifier_down_review_row(db, 0)
    assert db.requeue_verifier_down("run-s") == 1
    result = db.stop_run(worker_active=False)
    assert _row(db, 0)["status"] == "ready_for_review" and result["status"] == "curation_pending"
    assert db.requeue_verifier_down("run-r") == 1
    db.reset_run(worker_active=False)
    assert _row(db, 0)["status"] == "ready_for_review"
