"""The worker on a server: database connections and the final status write (runtime package, item 3), and the row
leases a long product keeps (item 2).

- One connection helper (db_connect.connect) with connect, read and write timeouts: a database that hangs raises
  after the timeout instead of hanging the worker. Every Python module uses it.
- update_task_status retries a lost connection with a short backoff, and logs RESULT NOT SAVED when the result is
  still lost (the row is searched again after its lease: a second paid search). A retry whose first COMMIT did land
  is a success, not a lost lease.
- renew_leases / release_claims touch only this worker's 'processing' rows (real MariaDB; skipped when it is down).
"""

import logging
import os
import re

import pymysql
import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROW = 978000
SKU = "wrt-sku-"


# ---------------------------------------------------------------------------
# db_connect
# ---------------------------------------------------------------------------

@pytest.fixture
def captured(monkeypatch):
    import db_connect

    calls = []
    monkeypatch.setattr(pymysql, "connect", lambda **kw: calls.append(kw) or object())
    for name in ("DB_CONNECT_TIMEOUT_S", "DB_READ_TIMEOUT_S", "DB_WRITE_TIMEOUT_S"):
        monkeypatch.delenv(name, raising=False)
    return db_connect, calls


def test_every_connection_has_connect_read_and_write_timeouts(captured, monkeypatch):
    db_connect, calls = captured
    monkeypatch.setenv("DB_DATABASE", "automation_test_runtime_probe")
    db_connect.connect()
    kw = calls[-1]
    assert kw["connect_timeout"] == db_connect.DEFAULT_CONNECT_TIMEOUT_S == 10
    # longer than the longest wait the code asks for on purpose: GET_LOCK(60) and InnoDB's 50 s lock wait
    assert kw["read_timeout"] == db_connect.DEFAULT_READ_TIMEOUT_S == 120
    assert kw["write_timeout"] == db_connect.DEFAULT_WRITE_TIMEOUT_S == 60
    assert kw["database"] == "automation_test_runtime_probe" and kw["charset"] == "utf8mb4"
    assert kw["cursorclass"] is pymysql.cursors.DictCursor


def test_the_timeouts_come_from_the_environment_and_are_clamped(captured, monkeypatch):
    db_connect, calls = captured
    monkeypatch.setenv("DB_CONNECT_TIMEOUT_S", "3")
    monkeypatch.setenv("DB_READ_TIMEOUT_S", "99999")
    monkeypatch.setenv("DB_WRITE_TIMEOUT_S", "not a number")
    assert db_connect.timeouts() == {"connect_timeout": 3, "read_timeout": 3600, "write_timeout": 60}
    monkeypatch.setenv("DB_CONNECT_TIMEOUT_S", "0")
    assert db_connect.timeouts()["connect_timeout"] == 1


def test_a_server_connection_and_a_plain_cursor_and_overrides(captured):
    db_connect, calls = captured
    db_connect.connect(database=False, dict_cursor=False, connect_timeout=5)
    kw = calls[-1]
    assert "database" not in kw and "cursorclass" not in kw and kw["connect_timeout"] == 5
    assert kw["read_timeout"] == 120
    db_connect.connect(database="information_schema")
    assert calls[-1]["database"] == "information_schema"


def test_the_modules_connect_through_the_helper(captured, monkeypatch):
    import db_connect
    import google_sheets
    import local_cache_db

    _, calls = captured
    calls.clear()                                    # a first import of config reads the settings table
    local_cache_db.get_db_connection()
    google_sheets._db_connect()
    assert len(calls) == 2 and all(kw["read_timeout"] == 120 for kw in calls)
    assert local_cache_db.init_db() is False        # the fake connection has no cursor: init_db reports False
    assert calls[-1]["connect_timeout"] == 5 and "database" not in calls[-1]
    assert db_connect.connect is not None


def test_no_python_module_opens_its_own_connection():
    """pymysql.connect appears only in db_connect.py (tests and vendored code aside)."""
    offenders = []
    for folder, dirs, files in os.walk(REPO_ROOT):
        dirs[:] = [d for d in dirs if d not in (".git", "tests", "vendor", "node_modules", "temp", ".venv", "venv",
                                                "__pycache__", ".claude")]
        for name in files:
            if not name.endswith(".py") or name == "db_connect.py":
                continue
            path = os.path.join(folder, name)
            with open(path, encoding="utf-8", errors="replace") as fh:
                if re.search(r"pymysql\.connect\(", fh.read()):
                    offenders.append(os.path.relpath(path, REPO_ROOT))
    assert offenders == []


# ---------------------------------------------------------------------------
# the final status write
# ---------------------------------------------------------------------------

@pytest.fixture
def ldb(offline, monkeypatch):
    import local_cache_db

    monkeypatch.setattr("time.sleep", lambda s: None)
    return local_cache_db


def test_a_status_write_that_landed_before_the_connection_dropped_is_not_lost(ldb, monkeypatch, fake_connection):
    """The COMMIT reached the server, the answer was lost: the retry finds the row already in the new status with
    our claim, and reports success instead of 'no longer claimed'."""
    state = {"committed": False}

    def responder(sql, params):
        if sql.startswith("SELECT id, sku_key, task_kind"):
            if state["committed"]:
                return []                   # no longer 'processing'
            return [{"id": 7, "sku_key": None, "task_kind": None, "fail_count": 0, "down_count": 0, "priority": 0}]
        if sql.startswith("SELECT status FROM automation_queue WHERE id"):
            assert params == (7, "host:1#c")
            return [{"status": "ready_for_review"}]
        return None

    class DropsAfterCommit(fake_connection):
        def commit(self):
            super().commit()
            state["committed"] = True
            raise pymysql.err.OperationalError(2013, "Lost connection to MySQL server during query")

    conns = []
    monkeypatch.setattr(ldb, "get_db_connection",
                        lambda: conns.append((DropsAfterCommit if not conns else fake_connection)(responder))
                        or conns[-1])
    assert ldb.update_task_status(7, "ready_for_review", claim_id="host:1#c") is True
    assert len(conns) == 2 and conns[1].commits == 0


def test_a_status_write_lost_for_good_is_logged_loudly(ldb, monkeypatch, fake_connection, caplog):
    def broken(sql, params):
        raise pymysql.err.InterfaceError(0, "")

    sleeps = []
    monkeypatch.setattr("time.sleep", sleeps.append)
    monkeypatch.setattr(ldb, "get_db_connection", lambda: fake_connection(broken))
    with caplog.at_level(logging.WARNING, logger="local_cache_db"):
        assert ldb.update_task_status(7, "failed", "x", failure_code="NO_RESULTS", claim_id="host:1#c") is False
    assert sleeps == list(ldb.STATUS_WRITE_RETRY_DELAYS_S) and sum(sleeps) <= 15
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1 and "RESULT NOT SAVED" in errors[0].getMessage()


def test_a_lost_lease_is_not_retried(ldb, monkeypatch, fake_connection):
    conns = []
    monkeypatch.setattr(ldb, "get_db_connection", lambda: conns.append(fake_connection(lambda s, p: [])) or conns[-1])
    assert ldb.update_task_status(7, "failed", claim_id="host:1#c") is False
    assert len(conns) == 1
    assert not any(s.startswith("SELECT status FROM") for s in conns[0].sql())   # first attempt: no extra read


def test_leases_and_releases_need_no_connection_without_claims(ldb, monkeypatch):
    monkeypatch.setattr(ldb, "get_db_connection", lambda: pytest.fail("no connection expected"))
    assert ldb.renew_leases([]) == 0 and ldb.renew_leases([None, ""]) == 0
    assert ldb.release_claims(()) == 0


def test_a_database_error_while_renewing_is_reported_as_none(ldb, monkeypatch, fake_connection):
    def broken(sql, params):
        raise pymysql.err.OperationalError(2006, "MySQL server has gone away")

    monkeypatch.setattr(ldb, "get_db_connection", lambda: fake_connection(broken))
    assert ldb.renew_leases(["host:1#a"]) is None
    assert ldb.release_claims(["host:1#a"]) is None


# ---------------------------------------------------------------------------
# leases on the real database
# ---------------------------------------------------------------------------

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
        _sql(db, "DELETE FROM automation_queue WHERE `row_number` BETWEEN %s AND %s", (ROW, ROW + 99))

    wipe()
    yield db
    wipe()


def _claim(db, i, claim, minutes):
    db.add_to_queue(ROW + i, "", f"Product {i}", "Brand", "q", sku_key=f"{SKU}{i}")
    _sql(db, "UPDATE automation_queue SET status = 'processing', worker_id = %s, "
             "lease_until = NOW() + INTERVAL %s MINUTE WHERE `row_number` = %s", (claim, minutes, ROW + i))


def _lease_minutes(db, i):
    row = _sql(db, "SELECT status, worker_id, TIMESTAMPDIFF(SECOND, NOW(), lease_until) AS left_s "
                   "FROM automation_queue WHERE `row_number` = %s", (ROW + i,))[0]
    return row


def test_the_heartbeat_renews_only_this_workers_rows(db):
    _claim(db, 1, "host:1#aaa", 1)                 # ours, about to expire
    _claim(db, 2, "host:1#bbb", 1)                 # ours
    _claim(db, 3, "other:9#ccc", 1)                # another worker's
    assert db.renew_leases(["host:1#aaa", "host:1#bbb", "host:1#gone"]) == 2
    assert _lease_minutes(db, 1)["left_s"] > 14 * 60 and _lease_minutes(db, 2)["left_s"] > 14 * 60
    assert _lease_minutes(db, 3)["left_s"] <= 60
    _sql(db, "UPDATE automation_queue SET status = 'ready_for_review' WHERE `row_number` = %s", (ROW + 2,))
    assert db.renew_leases(["host:1#bbb"]) == 0     # a finished row keeps no lease


def test_a_stopping_worker_hands_its_rows_back_at_once(db):
    _claim(db, 1, "host:1#aaa", 15)
    _claim(db, 2, "other:9#ccc", 15)
    assert db.release_claims(["host:1#aaa"]) == 1
    mine, other = _lease_minutes(db, 1), _lease_minutes(db, 2)
    assert mine["status"] == "pending" and mine["worker_id"] is None and mine["left_s"] is None
    assert other["status"] == "processing" and other["worker_id"] == "other:9#ccc"
