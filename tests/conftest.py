"""Shared test setup.

* The pipeline modules live at the repository root, one level above tests/.
* Every test run uses the MariaDB database `automation_test` (never the owner's
  `automation_db`). This is set before any pipeline module is imported, because
  config.py and local_cache_db.py touch the database at import time.
* `mariadb_or_skip` gives real-database tests a clean skip when MariaDB is down.
* `offline` blocks every outbound socket connection for tests that must not
  reach the network (or the database).
* `FakeConnection` (fixture `fake_connection`) records the SQL sent to a pymysql-like
  connection; `responder(sql, params)` returns the rows the next fetch sees, or raises.
"""

import os
import re
import socket
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["DB_DATABASE"] = os.environ.get("TEST_DB_DATABASE", "automation_test")


@pytest.fixture
def offline(monkeypatch):
    """Refuse every socket connection (network and database)."""

    def refuse(*args, **kwargs):
        raise OSError("network access is blocked in this test")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    return refuse


@pytest.fixture
def mariadb_or_skip():
    """local_cache_db bound to a freshly migrated `automation_test`; skips when MariaDB is unreachable."""
    import pymysql

    try:
        conn = pymysql.connect(
            host=os.getenv("DB_HOST", "127.0.0.1"),
            port=int(os.getenv("DB_PORT", "3306")),
            user=os.getenv("DB_USERNAME", "root"),
            password=os.getenv("DB_PASSWORD", ""),
            connect_timeout=2,
        )
        conn.close()
    except Exception as exc:  # pragma: no cover - depends on the environment
        pytest.skip(f"MariaDB is not reachable: {exc}")
    assert os.environ["DB_DATABASE"] != "automation_db", "tests must never touch the production database"
    import local_cache_db

    assert local_cache_db.init_db(), "init_db failed although MariaDB is reachable"
    return local_cache_db


# ---------------------------------------------------------------------------
# Recording stand-in for a pymysql connection
# ---------------------------------------------------------------------------


def squash(sql):
    return re.sub(r"\s+", " ", sql).strip()


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self._rows = []
        self.rowcount = 0

    def execute(self, sql, params=None):
        sql = squash(sql)
        self.conn.executed.append((sql, params))
        rows = self.conn.responder(sql, params) if self.conn.responder else None
        self._rows = list(rows or [])
        self.rowcount = len(self._rows) if sql.upper().startswith("SELECT") else (1 if rows is None else len(self._rows))
        return self.rowcount

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class FakeConnection:
    def __init__(self, responder=None):
        self.responder = responder
        self.executed = []
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True

    def sql(self):
        return [s for s, _ in self.executed]


@pytest.fixture
def fake_connection():
    """The FakeConnection class: FakeConnection(responder) records every executed statement."""
    return FakeConnection


@pytest.fixture(autouse=True)
def _no_telegram(monkeypatch):
    """Every run writes a report and sends it to Telegram when configured (run_report.notify). The owner's .env may
    hold a real bot token: no test may send a message, so Telegram reads as not configured unless a test sets it."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "")


@pytest.fixture(autouse=True)
def _reset_learned_provider_state():
    """Providers learn per-process facts from live answers (a free Serper plan refuses site:, a CSE key gets
    403). Tests must not leak that learning into each other."""
    yield
    try:
        from catalog_match.providers.cse_legacy import CseLegacyProvider
        from catalog_match.providers.serper import SerperImagesProvider
    except Exception:  # pragma: no cover - catalog_match not importable
        return
    SerperImagesProvider.operators_blocked = False
    CseLegacyProvider.disabled_reason = None
