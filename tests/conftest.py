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
# The local background-removal fallback (BG_FALLBACK, default 'local') depends on what is installed on the machine
# (cv2 / rembg); the suite must not. Tests that exercise it set config.BG_FALLBACK = 'local' and stub
# image_processor.local_methods_available themselves (tests/test_bg_fallback.py).
os.environ["BG_FALLBACK"] = "off"
# The nightly run and the worker refresh the local catalog index in a background thread (catalog_match.index_refresh);
# no test may start one by accident (it would read real sites). The tests of the refresh turn it on by themselves.
os.environ["LOCAL_INDEX_REFRESH_MAX_S"] = "0"


@pytest.fixture(autouse=True)
def _fresh_host_breaker():
    """catalog_match.fetch keeps a process-wide record of hosts whose downloads timed out; a test must not inherit
    (or leave) another test's slow hosts."""
    try:
        from catalog_match import fetch
    except Exception:  # pragma: no cover - a test environment without the image libraries
        yield
        return
    fetch.reset_host_breaker()
    yield
    fetch.reset_host_breaker()


@pytest.fixture(autouse=True)
def _fresh_cloud_breaker():
    """image_processor pauses a cloud isolation method for 30 minutes after a credit / key / quota failure; a test must
    not inherit (or leave) another test's pause. Nothing to reset while the module was never imported."""
    module = sys.modules.get("image_processor")
    if module is not None:
        module.reset_cloud_breaker()
        module.reset_rembg_sessions(everything=True)
    yield
    module = sys.modules.get("image_processor")
    if module is not None:
        module.reset_cloud_breaker()
        module.reset_rembg_sessions(everything=True)


@pytest.fixture(autouse=True, scope="session")
def _public_dns():
    """net_guard (the SSRF guard) resolves the host of every URL a download may reach. No test looks a name up for
    real: every name is a public address here (fake_getaddrinfo), for the whole session, so module-scoped fixtures
    (a recording run) see it too. A test of the guard sets its own mapping with monkeypatch."""
    try:
        import net_guard
        from net_fakes import fake_getaddrinfo
    except Exception:  # pragma: no cover - a checkout without the module
        yield
        return
    real = net_guard.getaddrinfo
    net_guard.getaddrinfo = fake_getaddrinfo()
    yield
    net_guard.getaddrinfo = real


@pytest.fixture(autouse=True)
def _fresh_dns_cache():
    """net_guard caches DNS answers for a minute; a test must not inherit (or leave) another test's answers."""
    module = sys.modules.get("net_guard")
    if module is not None:
        module.reset_cache()
    yield
    module = sys.modules.get("net_guard")
    if module is not None:
        module.reset_cache()


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
