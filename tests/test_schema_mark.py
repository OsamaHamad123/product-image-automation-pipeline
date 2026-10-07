"""schema_mark: the tables are set up once per version of the module's file, not on every import (every dashboard
request imports local_cache_db and used to run dozens of CREATE/ALTER statements)."""

import sys

import pytest

import schema_mark


def test_setup_runs_once_per_fingerprint(mariadb_or_skip, monkeypatch):
    db = mariadb_or_skip
    calls = []
    monkeypatch.setattr(db, "init_db", lambda: calls.append(1) or True)
    monkeypatch.setattr(schema_mark, "fingerprint", lambda path: "a" * 40)

    assert db.ensure_schema() is True and calls == [1]          # new fingerprint: set up and marked
    assert db.ensure_schema() is True and calls == [1]          # same fingerprint: nothing runs
    monkeypatch.setattr(schema_mark, "fingerprint", lambda path: "b" * 40)
    assert db.ensure_schema() is True and calls == [1, 1]       # the code changed: set up once more


def test_a_failed_setup_is_not_marked(mariadb_or_skip, monkeypatch):
    db = mariadb_or_skip
    monkeypatch.setattr(schema_mark, "fingerprint", lambda path: "c" * 40)
    monkeypatch.setattr(db, "init_db", lambda: False)
    assert db.ensure_schema() is False
    assert schema_mark.is_current("local_cache_db", "c" * 40) is False


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
    monkeypatch.setattr(schema_mark, "fingerprint", lambda path: "e" * 40)
    google_sheets.SQLiteTransactionQueue()
    google_sheets.SQLiteTransactionQueue()
    assert runs == [1]


def test_importing_the_bridge_does_not_load_gspread():
    import subprocess

    code = "import sys, google_sheets; print('gspread' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert out.stdout.strip().splitlines()[-1] == "False", out.stderr[-2000:]
