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
import threading
import time

import pytest

ROW = 963000
SKU = "p4f-sku-"


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
        _sql(db, "DELETE FROM rejected_images WHERE sku_key LIKE %s OR original_url LIKE 'https://p4f.example/%%'",
             (SKU + "%",))
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
