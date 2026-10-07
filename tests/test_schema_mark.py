"""schema_mark: the tables are set up once per version of the module's file, not on every import (every dashboard
request imports local_cache_db and used to run dozens of CREATE/ALTER statements)."""

import sys
import uuid

import pytest

import schema_mark


@pytest.fixture(autouse=True)
def fresh_marks(request):
    """Each test starts and ends without marks (a mark left behind would read as «already set up» next time)."""
    if "mariadb_or_skip" not in request.fixturenames:
        yield
        return
    db = request.getfixturevalue("mariadb_or_skip")
    from laqta_kernel import sql

    def clear():
        try:
            sql(db, "DELETE FROM schema_marks WHERE name IN ('local_cache_db', 'sheet_updates')")
        except Exception:  # noqa: BLE001 - the table does not exist yet
            pass

    clear()
    yield
    clear()


def fp():
    return uuid.uuid4().hex[:40].ljust(40, "0")


def test_setup_runs_once_per_fingerprint(mariadb_or_skip, monkeypatch):
    db = mariadb_or_skip
    calls = []
    monkeypatch.setattr(db, "init_db", lambda: calls.append(1) or True)
    first, second = fp(), fp()
    monkeypatch.setattr(schema_mark, "fingerprint", lambda path: first)

    assert db.ensure_schema() is True and calls == [1]          # new fingerprint: set up and marked
    assert db.ensure_schema() is True and calls == [1]          # same fingerprint: nothing runs
    monkeypatch.setattr(schema_mark, "fingerprint", lambda path: second)
    assert db.ensure_schema() is True and calls == [1, 1]       # the code changed: set up once more


def test_a_failed_setup_is_not_marked(mariadb_or_skip, monkeypatch):
    db = mariadb_or_skip
    mark = fp()
    monkeypatch.setattr(schema_mark, "fingerprint", lambda path: mark)
    monkeypatch.setattr(db, "init_db", lambda: False)
    assert db.ensure_schema() is False
    assert schema_mark.is_current("local_cache_db", mark) is False


def test_an_unreachable_database_reads_as_not_current(monkeypatch):
    import db_connect

    def down(*a, **k):
        raise OSError("down")

    monkeypatch.setattr(db_connect, "connect", down)
    assert schema_mark.is_current("local_cache_db", "d" * 40) is False
    schema_mark.save("local_cache_db", "d" * 40)               # logs, never raises


def test_the_sheet_queue_sets_its_table_up_once(mariadb_or_skip, monkeypatch):
    import google_sheets

    runs = []
    monkeypatch.setattr(google_sheets.SQLiteTransactionQueue, "_setup_schema", lambda self: runs.append(1))
    mark = fp()
    monkeypatch.setattr(schema_mark, "fingerprint", lambda path: mark)
    google_sheets.SQLiteTransactionQueue()
    google_sheets.SQLiteTransactionQueue()
    assert runs == [1]


def test_importing_the_bridge_does_not_load_gspread():
    import subprocess

    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    code = "import sys; sys.path.insert(0, '.'); import google_sheets; print('gspread' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=120)
    assert out.stdout.strip().splitlines()[-1] == "False", out.stderr[-2000:]
