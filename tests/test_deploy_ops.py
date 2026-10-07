"""Server operations around the Ubuntu deployment kit (deploy/ubuntu): the outbox flush between runs
(scripts/flush_sheets_sync.py and google_sheets.outbox_due_count), the dashboard run launcher (RunLauncher.php,
ApiController) and the /healthz endpoint (HealthzController.php).

systemctl is a fake, nothing reaches the network. The outbox and /healthz tests use the
real MariaDB test database (they skip, loudly named, when MariaDB is down); the PHP tests need php and
dashboard/vendor, like the other dashboard tests.
"""

import importlib.util
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
DASH = REPO / "dashboard"
PHP = shutil.which("php")
NEEDS_PHP = pytest.mark.skipif(PHP is None, reason="php is not installed")
NEEDS_LARAVEL = pytest.mark.skipif(PHP is None or not (DASH / "vendor" / "autoload.php").exists(),
                                   reason="php or dashboard/vendor is missing")


def load_script(name):
    spec = importlib.util.spec_from_file_location(f"{name}_under_test", REPO / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# google_sheets.outbox_due_count + scripts/flush_sheets_sync.py
# ---------------------------------------------------------------------------

@pytest.fixture
def outbox(mariadb_or_skip):
    """An empty sheet_updates table of the test database."""
    import google_sheets
    google_sheets.SQLiteTransactionQueue()                    # creates / upgrades the table
    conn = google_sheets._db_connect()
    try:
        conn.cursor().execute("DELETE FROM sheet_updates")
        conn.commit()
    finally:
        conn.close()

    def add(status, n=1, next_attempt_at=None, name="P", error=None, age_days=0):
        c = google_sheets._db_connect()
        try:
            cur = c.cursor()
            for i in range(n):
                cur.execute(
                    "INSERT INTO sheet_updates (`row_number`, col_index, `value`, sync_status, next_attempt_at, "
                    "key_name, last_error, registered_at) VALUES (%s, 1, 'v', %s, %s, %s, %s, "
                    "NOW() - INTERVAL %s DAY)", (10 + i, status, next_attempt_at, name, error, age_days))
            c.commit()
        finally:
            c.close()

    add.google_sheets = google_sheets
    return add


def test_due_count_is_pending_and_failed_past_their_retry_time_only(outbox):
    gs = outbox.google_sheets
    now = 1_800_000_000
    outbox("PENDING", 2)
    outbox("FAILED", 1, next_attempt_at=now - 5)         # due
    outbox("FAILED", 1, next_attempt_at=None)            # due (never scheduled)
    outbox("FAILED", 3, next_attempt_at=now + 600)       # waits for its retry time
    for status in ("SYNCED", "DEAD", "CONFLICT", "SUPERSEDED", "SKIPPED_OUT_OF_BOUNDS"):
        outbox(status, 2)
    assert gs.outbox_due_count(now=now) == 4
    assert gs.outbox_due_count(now=now + 3600) == 7      # the waiting ones are due an hour later


def test_flush_main_skips_google_when_nothing_is_due(outbox, monkeypatch, capsys):
    flush_script = load_script("flush_sheets_sync")
    called = []
    monkeypatch.setattr(flush_script, "flush", lambda: called.append("flush"))
    outbox("SYNCED", 3)
    assert flush_script.main() == 0
    assert called == [] and "لم نفتح الشيت" in capsys.readouterr().out


def test_flush_main_flushes_when_a_write_is_due(outbox, monkeypatch):
    flush_script = load_script("flush_sheets_sync")
    called = []
    monkeypatch.setattr(flush_script, "flush", lambda: called.append("flush"))
    outbox("PENDING", 1)
    assert flush_script.main() == 0 and called == ["flush"]


def test_flush_main_survives_a_database_that_does_not_answer(monkeypatch, capsys):
    flush_script = load_script("flush_sheets_sync")

    def down():
        raise OSError("connection refused")

    monkeypatch.setattr(flush_script.google_sheets, "outbox_due_count", down)
    monkeypatch.setattr(flush_script, "flush", lambda: pytest.fail("flush must not run without a database"))
    assert flush_script.main() == 0                       # a normal exit: the unit must not "fail" every 2 minutes
    assert "قاعدة البيانات لا ترد" in capsys.readouterr().out


def test_flush_main_does_not_hide_an_unexpected_flush_error(outbox, monkeypatch):
    flush_script = load_script("flush_sheets_sync")

    def broken():
        raise RuntimeError("bug in the flush")

    monkeypatch.setattr(flush_script, "flush", broken)
    outbox("PENDING", 1)
    with pytest.raises(RuntimeError):
        flush_script.main()                                # the unit shows as failed
