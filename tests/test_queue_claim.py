"""Queue claim, statistics and error propagation, against a recording fake connection."""

import pymysql
import pytest


pytestmark = pytest.mark.usefixtures("offline")


@pytest.fixture
def ldb(offline):
    import local_cache_db
    return local_cache_db


def test_fetch_next_task_is_one_atomic_claim(ldb, monkeypatch, fake_connection):
    claimed = {"id": 7, "row_number": 12, "status": "processing", "worker_id": None}

    def responder(sql, params):
        if sql.startswith("SELECT * FROM automation_queue WHERE worker_id"):
            return [dict(claimed, worker_id=params[0])]
        return None

    conn = fake_connection(responder)
    monkeypatch.setattr(ldb, "get_db_connection", lambda: conn)

    task = ldb.fetch_next_task("host:1")

    updates = [(s, p) for s, p in conn.executed if s.upper().startswith("UPDATE")]
    assert len(updates) == 1, "the claim must be a single UPDATE statement"
    sql, params = updates[0]
    assert "status='pending' OR" in sql
    assert "lease_until" in sql
    assert "attempts=attempts+1" in sql
    # the claimed row is read back by the unique claim id written by that UPDATE
    select_sql, select_params = conn.executed[-1]
    assert select_sql.startswith("SELECT * FROM automation_queue WHERE worker_id")
    assert select_params == (params[0],)
    assert params[0].startswith("host:1#")
    assert task["row_number"] == 12 and task["worker_id"] == params[0]
    assert conn.commits == 1 and conn.closed


def test_fetch_next_task_returns_none_when_nothing_claimed(ldb, monkeypatch, fake_connection):
    conn = fake_connection(lambda sql, params: [] if sql.startswith("SELECT") else None)
    monkeypatch.setattr(ldb, "get_db_connection", lambda: conn)
    assert ldb.fetch_next_task() is None


def test_fetch_next_task_propagates_db_errors(ldb, monkeypatch, fake_connection):
    def responder(sql, params):
        raise pymysql.err.OperationalError(2013, "Lost connection to MySQL server during query")

    conn = fake_connection(responder)
    monkeypatch.setattr(ldb, "get_db_connection", lambda: conn)
    with pytest.raises(pymysql.err.OperationalError):
        ldb.fetch_next_task()
    assert conn.closed


def test_queue_statistics_include_ready_for_review_and_raise(ldb, monkeypatch, fake_connection):
    rows = [{"status": "pending", "cnt": 2}, {"status": "ready_for_review", "cnt": 5}, {"status": "failed", "cnt": 1}]
    conn = fake_connection(lambda sql, params: rows)
    monkeypatch.setattr(ldb, "get_db_connection", lambda: conn)
    stats = ldb.get_queue_statistics()
    assert stats["ready_for_review"] == 5
    assert stats["pending"] == 2 and stats["failed"] == 1 and stats["completed"] == 0
    assert stats["total"] == 8

    def broken():
        raise pymysql.err.OperationalError(2003, "Can't connect to MySQL server")

    monkeypatch.setattr(ldb, "get_db_connection", broken)
    with pytest.raises(pymysql.err.OperationalError):
        ldb.get_queue_statistics()
    with pytest.raises(pymysql.err.OperationalError):
        ldb.count_open_tasks()


def test_add_to_queue_is_an_upsert_that_protects_review_rows(ldb, monkeypatch, fake_connection):
    conn = fake_connection()
    monkeypatch.setattr(ldb, "get_db_connection", lambda: conn)
    ldb.add_to_queue(5, "6281007000028", "Fresh Milk", "Almarai", "q", payload={"name_ar": "حليب"}, sku_key="k")
    (sql, params), = conn.executed
    assert "REPLACE" not in sql.upper() and "DELETE" not in sql.upper()
    assert "ON DUPLICATE KEY UPDATE" in sql
    assert "status IN ('ready_for_review','completed')" in sql
    # every status-dependent assignment comes before `status` is overwritten
    assert sql.index("status = IF(") > sql.index("attempts = IF(")
    assert sql.index("status = IF(") < sql.index("sku_key = VALUES(sku_key)")
    assert params[-7:] == (0,) * 7
    assert '"name_ar": "حليب"' in params[6]

    conn.executed.clear()
    ldb.add_to_queue(5, "", "Fresh Milk", "Almarai", "q", reprocess=True)
    assert conn.executed[0][1][-7:] == (1,) * 7
