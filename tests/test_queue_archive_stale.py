"""Queue rows that left the sheet are archived by a whole-sheet enqueue, not counted as waiting work.

Production kept 60 rows (row_number 42-101) from July in automation_queue: no sku_key, no run_id, and the sheet now
has 40 rows. They showed as 72 "waiting" instead of 20 and 10 "failed" in the dashboard. A whole-sheet enqueue now
archives (status 'archived', the row stays in the table) every row without a sku_key whose row is past the sheet's
last row or holds another product today; a row with a sku_key, a completed row, a live one and one a reviewer
approved are left alone (local_cache_db.archive_stale_queue_rows, main.run_enqueue_mode).
"""

import json

import pytest

from laqta_kernel import NEEDS_LARAVEL

ROW = 965000


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
        _sql(db, "DELETE FROM automation_queue")
        _sql(db, "DELETE FROM review_decisions WHERE `row_number` >= %s", (ROW,))

    wipe()
    yield db
    wipe()


def _put(db, i, name, status="pending", sku_key=None, lease=False):
    _sql(db, "INSERT INTO automation_queue (`row_number`, product_name, brand, status, sku_key, lease_until) "
             "VALUES (%s, %s, 'Old Brand', %s, %s, " + ("NOW() + INTERVAL 10 MINUTE" if lease else "NULL") + ")",
         (ROW + i, name, status, sku_key))


def _status(db, i):
    rows = _sql(db, "SELECT status FROM automation_queue WHERE `row_number` = %s", (ROW + i,))
    return rows[0]["status"] if rows else None


def _sheet(*names):
    return {ROW + i: name for i, name in enumerate(names)}


def test_rows_past_the_sheet_or_holding_another_product_are_archived(db):
    _put(db, 0, "Kept Product")                               # still the product of its row
    _put(db, 1, "July Product", status="failed")              # another product in this row today
    _put(db, 5, "Gone Product")                               # past the sheet's last row
    _put(db, 6, "Gone Review", status="ready_for_review")
    _put(db, 7, "Gone Stuck", status="processing")            # a dead worker's lease
    assert db.archive_stale_queue_rows(_sheet("kept product ", "New Product", "Third")) == 4
    assert [_status(db, i) for i in (0, 1, 5, 6, 7)] == ["pending", "archived", "archived", "archived", "archived"]
    assert _sql(db, "SELECT COUNT(*) AS n FROM automation_queue")[0]["n"] == 5        # archived, not deleted
    # neither searched again nor counted with the waiting rows
    assert db.fetch_next_task("host:1")["product_name"] == "Kept Product"
    assert db.fetch_next_task("host:1") is None
    # a second enqueue has nothing left to archive
    assert db.archive_stale_queue_rows(_sheet("Kept Product", "New Product", "Third")) == 0


def test_keyed_completed_live_and_approved_rows_are_left_alone(db):
    _put(db, 5, "Keyed Product", sku_key="sku-keyed")
    _put(db, 6, "Done Product", status="completed")
    _put(db, 7, "Live Product", status="processing", lease=True)
    _put(db, 8, "Approved Product", status="ready_for_review")
    _sql(db, "INSERT INTO review_decisions (action, `row_number`, product_name) VALUES ('approved', %s, %s)",
         (ROW + 8, "Approved Product"))
    assert db.archive_stale_queue_rows(_sheet("Only Row")) == 0
    assert [_status(db, i) for i in (5, 6, 7, 8)] == ["pending", "completed", "processing", "ready_for_review"]


def test_an_empty_sheet_archives_nothing(db):
    _put(db, 5, "Gone Product")
    assert db.archive_stale_queue_rows({}) == 0
    assert _status(db, 5) == "pending"


def test_a_product_back_in_an_archived_row_is_queued_again(db):
    _put(db, 5, "Gone Product", status="failed")
    assert db.archive_stale_queue_rows(_sheet("Only Row")) == 1
    counts = db.add_many_to_queue([db.queue_input(ROW + 5, "", "Back Product", "Brand", "Back Product Brand",
                                                  sku_key="sku-back")])
    assert counts["reset"] == 1
    row = _sql(db, "SELECT status, sku_key, product_name FROM automation_queue WHERE `row_number` = %s", (ROW + 5,))[0]
    assert (row["status"], row["sku_key"], row["product_name"]) == ("pending", "sku-back", "Back Product")


def _enqueue_offline(monkeypatch, row_filter="", brand_filter=""):
    """main.run_enqueue_mode with the sheet and the queue writes replaced; returns the archive calls."""
    import config
    import google_sheets
    import local_cache_db
    import main

    products = [{"row_number": 2, "product_name": "Laban Up 180ml", "brand": "Al Rawabi", "barcode": "",
                 "existing_image_link": "", "search_query": "Laban Up 180ml Al Rawabi"},
                {"row_number": 3, "product_name": "Fresh Milk 1L", "brand": "Almarai", "barcode": "",
                 "existing_image_link": "", "search_query": "Fresh Milk 1L Almarai"}]
    calls = []
    monkeypatch.setattr(main, "load_run_config", lambda: None)
    monkeypatch.setattr(config, "ROW_FILTER", row_filter)
    monkeypatch.setattr(config, "BRAND_FILTER", brand_filter)
    monkeypatch.setattr(config, "FORCE_OVERWRITE_IMAGES", False)
    monkeypatch.setattr(google_sheets, "clear_cache", lambda: None)
    monkeypatch.setattr(google_sheets, "get_sheets_client", lambda: object())
    monkeypatch.setattr(google_sheets, "open_worksheet", lambda c, n: object())
    monkeypatch.setattr(google_sheets, "get_products", lambda ws: (products, 9))
    monkeypatch.setattr(google_sheets, "get_brand_mappings", lambda *a: {})
    monkeypatch.setattr(local_cache_db, "add_many_to_queue", lambda rows, reprocess=False, totals=None: totals)
    monkeypatch.setattr(local_cache_db, "resolution_snapshot", lambda: {"by_key": {}, "by_url": {}})
    monkeypatch.setattr(local_cache_db, "queue_snapshot", lambda: {})
    monkeypatch.setattr(local_cache_db, "get_queue_statistics", lambda: {})
    monkeypatch.setattr(local_cache_db, "archive_stale_queue_rows", lambda rows: calls.append(dict(rows)) or 60)
    monkeypatch.setattr(local_cache_db, "new_run_id", lambda: "run-x")
    monkeypatch.setattr(local_cache_db, "begin_run", lambda run_id: None)
    main.run_enqueue_mode()
    return calls


def test_a_whole_sheet_enqueue_archives_and_logs_the_count(offline, monkeypatch, capsys):
    assert _enqueue_offline(monkeypatch) == [{2: "Laban Up 180ml", 3: "Fresh Milk 1L"}]
    assert "60 صف قديم في الطابور لم يعد في الشيت: أُرشف" in capsys.readouterr().out


@pytest.mark.parametrize("row_filter, brand_filter", [("2", ""), ("", "Almarai")])
def test_a_filtered_enqueue_archives_nothing(offline, monkeypatch, row_filter, brand_filter):
    """Only part of the sheet was read: a row outside it may still be in the sheet."""
    assert _enqueue_offline(monkeypatch, row_filter, brand_filter) == []


@NEEDS_LARAVEL
def test_the_dashboard_neither_lists_nor_counts_archived_rows(db, tmp_path):
    """/api/review/queue-state lists no archived row and /api/batch-status counts none with the failures."""
    from laqta_kernel import stub_env
    from test_dashboard_login import run

    _put(db, 0, "Live Row", status="failed", sku_key="sku-live")
    _put(db, 5, "Gone Row", status="failed")
    _sql(db, "UPDATE automation_queue SET failure_code = 'NO_RESULTS' WHERE `row_number` IN (%s, %s)", (ROW, ROW + 5))
    assert db.archive_stale_queue_rows(_sheet("Live Row")) == 1
    environ, _calls = stub_env(tmp_path)
    state, status = run(environ, [["GET", "/api/review/queue-state"], ["GET", "/api/batch-status"]])
    rows = json.loads(state["body"])["rows"]
    assert [r["row_number"] for r in rows] == [ROW]
    assert json.loads(status["body"])["failed_by_code"] == {"NO_RESULTS": 1}
