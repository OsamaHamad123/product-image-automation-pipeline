"""scripts/prune_storage.py: the server's storage cleanup (runtime package, item 5).

A dry run by default; --apply removes. Only old candidate files no review row uses, old SYNCED outbox rows that a
newer SYNCED write of the same cell and product supersedes, and temp/search.log beyond its size cap. Never PENDING /
FAILED / DEAD rows, never review data, never a file while the database does not answer.
"""

import hashlib
import importlib.util
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DAY = 86400
ROW = 978500
SKU = "wrp-sku-"


def _load():
    spec = importlib.util.spec_from_file_location("prune_storage", ROOT / "scripts" / "prune_storage.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def prune():
    return _load()


def _sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def _file(folder, name, days_old, size=10):
    path = folder / name
    path.write_bytes(b"x" * size)
    stamp = time.time() - days_old * DAY
    os.utime(path, (stamp, stamp))
    return path


@pytest.fixture
def store(tmp_path):
    folder = tmp_path / "candidates"
    folder.mkdir()
    files = {
        "used_old": _file(folder, f"{_sha('used')}.jpg", 90),
        "unused_old": _file(folder, f"{_sha('unused')}.webp", 90, size=1000),
        "unused_new": _file(folder, f"{_sha('fresh')}.png", 3),
        "bare_old": _file(folder, _sha("bare"), 90),
        "tmp_old": _file(folder, f".{_sha('used')}.{'ab' * 16}.tmp", 90),
        "other_old": _file(folder, "notes.txt", 90),
        "sha_like_old": _file(folder, f"{_sha('x')}.jpg.bak", 90),
    }
    (folder / f"{_sha('dir')}.jpg").mkdir()
    return folder, files


def _never_over():
    class Clock:
        timed_out = False

        def over(self):
            return False

    return Clock()


def test_only_old_candidate_files_no_review_row_uses_are_removed(prune, store):
    folder, files = store
    referenced = {_sha("used"), _sha("bare_is_not")}
    found = prune.prune_candidates(str(folder), referenced, 30, False, _never_over(), time.time(), print)
    assert found["files"] == 3 and found["kept_in_use"] == 1 and found["kept_recent"] == 1
    assert all(p.exists() for p in files.values())                     # a dry run removes nothing

    found = prune.prune_candidates(str(folder), referenced, 30, True, _never_over(), time.time(), print)
    assert found["files"] == 3 and found["bytes"] == 1020
    gone = {k for k, p in files.items() if not p.exists()}
    assert gone == {"unused_old", "bare_old", "tmp_old"}
    assert (folder / f"{_sha('dir')}.jpg").is_dir()


def test_a_missing_store_is_nothing_to_do(prune, tmp_path):
    assert prune.prune_candidates(str(tmp_path / "none"), set(), 30, True, _never_over(), time.time(), print)["files"] == 0


def test_no_database_no_file_is_removed(prune, store, tmp_path):
    folder, files = store

    def down():
        raise OSError("database down")

    lines = []
    summary = prune.prune(apply=True, root=str(tmp_path), connect=down, store_dir=str(folder), log=lines.append,
                          keep_days=30, max_seconds=60)
    assert summary["candidates"] == {"skipped": "database: OSError"}
    assert summary["outbox"] == {"skipped": "database: OSError"}
    assert all(p.exists() for p in files.values())
    assert any("does not answer" in line for line in lines)


def test_the_time_cap_stops_between_files(prune, store, tmp_path):
    folder, files = store
    summary = prune.prune(apply=True, root=str(tmp_path), connect=lambda: _NoRefs(), store_dir=str(folder),
                          log=lambda line: None, keep_days=30, max_seconds=1e-9)
    assert summary["timed_out"] is True and summary["candidates"]["files"] == 0
    assert summary["outbox"] == {"skipped": "time cap"}
    assert all(p.exists() for p in files.values())


class _NoRefs:
    def cursor(self):
        return self

    def execute(self, sql, params=None):
        return 0

    def fetchall(self):
        return []

    def close(self):
        pass


def test_search_log_is_rotated_above_its_cap_keeping_n_copies(prune, tmp_path):
    log = tmp_path / "search.log"
    log.write_bytes(b"n" * (1024 * 1024 + 1))
    (tmp_path / "search.log.1").write_text("one")
    (tmp_path / "search.log.2").write_text("two")
    (tmp_path / "search.log.5").write_text("old setting")
    assert prune.rotate_search_log(str(log), 1, 2, apply=False) == {"rotated": True, "bytes": 1024 * 1024 + 1}
    assert log.exists()
    prune.rotate_search_log(str(log), 1, 2, apply=True)
    assert not log.exists()
    assert (tmp_path / "search.log.1").stat().st_size == 1024 * 1024 + 1
    assert (tmp_path / "search.log.2").read_text() == "one"
    assert not (tmp_path / "search.log.5").exists()
    assert prune.rotate_search_log(str(log), 1, 2, apply=True) == {"rotated": False, "bytes": 0}
    small = tmp_path / "small.log"
    small.write_text("ok")
    assert prune.rotate_search_log(str(small), 1, 2, apply=True)["rotated"] is False and small.exists()


def test_the_settings_and_their_bounds(monkeypatch):
    from catalog_match import settings

    monkeypatch.setattr(settings, "_config", None)
    for name in ("PRUNE_MAX_SECONDS", "PRUNE_KEEP_DAYS", "SEARCH_LOG_MAX_MB", "SEARCH_LOG_KEEP"):
        monkeypatch.delenv(name, raising=False)
    assert (settings.prune_max_seconds(), settings.prune_keep_days(), settings.search_log_max_mb(),
            settings.search_log_keep()) == (300, 30, 50, 3)
    monkeypatch.setenv("PRUNE_KEEP_DAYS", "1")
    monkeypatch.setenv("PRUNE_MAX_SECONDS", "0")
    assert settings.prune_keep_days() == 7 and settings.prune_max_seconds() == 10


def test_the_command_line_is_a_dry_run_by_default(tmp_path):
    folder = tmp_path / "candidates"
    folder.mkdir()
    old = _file(folder, f"{_sha('cli')}.jpg", 400)
    env = dict(os.environ, CANDIDATE_STORE_DIR=str(folder), SEARCH_LOG_MAX_MB="10240")
    result = subprocess.run([sys.executable, str(ROOT / "scripts" / "prune_storage.py")], capture_output=True,
                            text=True, timeout=120, env=env, encoding="utf-8")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "dry run" in result.stdout and old.exists()
    bad = subprocess.run([sys.executable, str(ROOT / "scripts" / "prune_storage.py"), "--apply", "--dry-run"],
                         capture_output=True, text=True, timeout=120, env=env)
    assert bad.returncode == 2 and old.exists()


# ---------------------------------------------------------------------------
# the real database: review references and the outbox
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
    import google_sheets

    google_sheets.SQLiteTransactionQueue()            # the outbox table
    db = mariadb_or_skip

    def wipe():
        _sql(db, "DELETE FROM sheet_updates WHERE `row_number` BETWEEN %s AND %s", (ROW, ROW + 99))
        _sql(db, "DELETE FROM curation_candidates WHERE sku_key LIKE %s", (SKU + "%",))

    wipe()
    yield db
    wipe()


def _outbox(db, row, status, days_old, seq, col_key="image_link", ident="a" * 40, relocated=None):
    _sql(db, "INSERT INTO sheet_updates (`row_number`, `col_index`, `value`, registered_at, sync_status, col_key, "
             "ident, seq, relocated_from) VALUES (%s, -1, 'v', NOW() - INTERVAL %s DAY, %s, %s, %s, %s, %s)",
         (row, days_old, status, col_key, ident, seq, relocated))
    return _sql(db, "SELECT MAX(id) AS id FROM sheet_updates")[0]["id"]


def test_only_superseded_old_synced_rows_leave_the_outbox(db, prune):
    rows = {
        "superseded": _outbox(db, ROW + 1, "SYNCED", 60, 100),
        "newest": _outbox(db, ROW + 1, "SYNCED", 50, 200),                 # the value in the cell: kept
        "pending": _outbox(db, ROW + 1, "PENDING", 90, 50),
        "failed": _outbox(db, ROW + 1, "FAILED", 90, 60),
        "dead": _outbox(db, ROW + 1, "DEAD", 90, 70),
        "conflict": _outbox(db, ROW + 1, "CONFLICT", 90, 80),
        "young": _outbox(db, ROW + 2, "SYNCED", 5, 100),
        "young_newer": _outbox(db, ROW + 2, "SYNCED", 4, 200),
        "other_product": _outbox(db, ROW + 3, "SYNCED", 60, 100, ident="b" * 40),
        "other_product_newer_elsewhere": _outbox(db, ROW + 4, "SYNCED", 50, 200, ident="b" * 40),
        "no_seq": _outbox(db, ROW + 5, "SYNCED", 60, None),
        "other_column": _outbox(db, ROW + 6, "SYNCED", 60, 100, col_key="barcode"),
        "moved": _outbox(db, ROW + 6, "SYNCED", 60, 100, relocated=ROW + 9),
        "moved_newer_unmoved": _outbox(db, ROW + 6, "SYNCED", 50, 200),
    }

    class Clock:
        timed_out = False

        def over(self):
            return False

    counted = prune.prune_outbox(30, False, Clock())["rows"]
    assert counted >= 2                                     # other tests' rows may share the table
    assert len(_sql(db, "SELECT id FROM sheet_updates WHERE `row_number` BETWEEN %s AND %s", (ROW, ROW + 99))) == 14
    prune.prune_outbox(30, True, Clock())
    left = {r["id"] for r in _sql(db, "SELECT id FROM sheet_updates WHERE `row_number` BETWEEN %s AND %s",
                                  (ROW, ROW + 99))}
    assert {k for k, i in rows.items() if i not in left} == {"superseded", "no_seq"}


def test_files_of_review_rows_are_kept_on_the_real_database(db, prune, store, tmp_path):
    folder, files = store
    _sql(db, "INSERT INTO curation_candidates (`row_number`, product_name, image_url, status, sku_key, content_sha256) "
             "VALUES (%s, 'P', 'https://img.example/a.jpg', 'pending', %s, %s)", (ROW, SKU + "1", _sha("used")))
    assert _sha("used") in prune.referenced_shas()
    summary = prune.prune(apply=True, root=str(tmp_path), store_dir=str(folder), log=lambda line: None,
                          keep_days=30, max_seconds=60, search_log=str(tmp_path / "search.log"))
    assert files["used_old"].exists() and not files["unused_old"].exists()
    assert summary["candidates"]["kept_in_use"] == 1 and "rows" in summary["outbox"]
