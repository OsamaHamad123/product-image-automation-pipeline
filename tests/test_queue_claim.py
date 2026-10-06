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


def _batching_connection(fake_connection, existing):
    """FakeConnection whose cursor also records executemany; the FOR UPDATE read returns `existing`."""
    def responder(sql, params):
        return [dict(r) for r in existing] if "FOR UPDATE" in sql else None

    conn = fake_connection(responder)
    real_cursor = conn.cursor

    def cursor():
        c = real_cursor()
        c.executemany = lambda sql, rows: conn.executed.append((" ".join(sql.split()), list(rows)))
        return c

    conn.cursor = cursor
    return conn


def test_add_to_queue_is_an_upsert_that_protects_review_rows(ldb, monkeypatch, fake_connection):
    review_row = {"id": 1, "row_number": 5, "sku_key": "k", "product_name": "Fresh Milk", "status": "ready_for_review",
                  "failure_code": None, "fail_count": 0, "reverify_count": 0, "priority": 0, "task_kind": None,
                  "review_only": 0, "requeue_reason": None, "brand_fp": None, "live": 0, "has_next": 0, "due": 0}
    conn = _batching_connection(fake_connection, [review_row])
    monkeypatch.setattr(ldb, "get_db_connection", lambda: conn)
    ldb.add_to_queue(5, "6281007000028", "Fresh Milk", "Almarai", "q", payload={"name_ar": "حليب"}, sku_key="k")
    (read_sql, read_params), (sql, rows) = conn.executed
    assert read_sql.startswith("SELECT") and read_sql.endswith("FOR UPDATE") and read_params == (5,)
    assert "REPLACE" not in sql.upper() and "DELETE" not in sql.upper()
    assert "ON DUPLICATE KEY UPDATE" in sql
    # the review row of the same product is kept: only identity and payload columns are refreshed
    postfix = sql.split("ON DUPLICATE KEY UPDATE", 1)[1]
    assert "status" not in postfix and "payload_json = VALUES(payload_json)" in postfix
    assert '"name_ar": "حليب"' in rows[0][9]
    assert conn.commits == 1

    conn.executed.clear()
    ldb.add_to_queue(5, "", "Fresh Milk", "Almarai", "q", sku_key="k", reprocess=True)
    _, (sql, rows) = conn.executed
    assert "status = 'pending'" in sql.split("ON DUPLICATE KEY UPDATE", 1)[1]


def test_a_status_write_is_retried_after_a_deadlock(ldb, monkeypatch, fake_connection):
    """The result write locks the row and the waiting rows of the same product, so it can meet the claim scan in a
    deadlock; InnoDB then aborts one side. A lost result write would cost a second search after the lease."""
    calls = {"select": 0}

    def responder(sql, params):
        if sql.startswith("SELECT id, sku_key, task_kind"):
            calls["select"] += 1
            if calls["select"] == 1:
                raise pymysql.err.OperationalError(1213, "Deadlock found when trying to get lock")
            return [{"id": 7, "sku_key": None, "task_kind": None, "fail_count": 0, "down_count": 0, "priority": 0}]
        return None

    conns = []

    def connect():
        conns.append(fake_connection(responder))
        return conns[-1]

    monkeypatch.setattr(ldb, "get_db_connection", connect)
    monkeypatch.setattr("time.sleep", lambda s: None)
    assert ldb.update_task_status(7, "ready_for_review", claim_id="host:1#c") is True
    assert len(conns) == 2 and conns[1].commits == 1
    assert any(s.startswith("UPDATE automation_queue SET status") for s in conns[1].sql())

    def broken(sql, params):
        raise pymysql.err.OperationalError(2013, "Lost connection to MySQL server during query")

    conns.clear()
    monkeypatch.setattr(ldb, "get_db_connection", lambda: conns.append(fake_connection(broken)) or conns[-1])
    assert ldb.update_task_status(7, "ready_for_review", claim_id="host:1#c") is False
    # a lost connection is retried with a short backoff (runtime item 3), then the result is reported as lost
    assert len(conns) == 1 + len(ldb.STATUS_WRITE_RETRY_DELAYS_S)

    def bad_sql(sql, params):
        raise pymysql.err.ProgrammingError(1064, "You have an error in your SQL syntax")

    conns.clear()
    monkeypatch.setattr(ldb, "get_db_connection", lambda: conns.append(fake_connection(bad_sql)) or conns[-1])
    assert ldb.update_task_status(7, "ready_for_review", claim_id="host:1#c") is False
    assert len(conns) == 1                                   # an error that waiting cannot fix is not retried


def test_queue_upserts_are_sent_as_one_multi_row_statement():
    """pymysql batches executemany into one INSERT only when VALUES holds nothing but %s and no %s follows
    ON DUPLICATE KEY UPDATE; otherwise it silently falls back to one round trip per row (the old 25 ms/row)."""
    import pymysql.cursors

    import local_cache_db as ldb
    for sql in (ldb._QUEUE_KEEP_SQL, ldb._QUEUE_RESET_SQL):
        m = pymysql.cursors.RE_INSERT_VALUES.match(sql)
        assert m is not None, sql
        assert "%" not in m.group(1) and "%" not in m.group(3)
