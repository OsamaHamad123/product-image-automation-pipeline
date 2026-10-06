"""Storage cleanup on the server: a dry run by default, --apply to remove.

    python3 scripts/prune_storage.py              # says what it would remove, removes nothing
    python3 scripts/prune_storage.py --apply      # removes it

The nightly run (scripts/run_nightly.py) runs it with --apply at the end of the night (NIGHTLY_PRUNE_ENABLED, on by
default) within PRUNE_MAX_SECONDS.

It removes only:

1. Candidate image files in the candidate store (CANDIDATE_STORE_DIR, temp/candidates: <sha256>.<ext>, and the
   '.<sha256>.<id>.tmp' files of an interrupted write) older than PRUNE_KEEP_DAYS (30) whose sha256 no
   curation_candidates row references. Any other file in that folder is left alone.
2. SYNCED rows of the Sheets outbox (sheet_updates) older than PRUNE_KEEP_DAYS that a newer SYNCED write of the same
   cell and the same product supersedes (same column, product fingerprint, row and original row, larger seq). The
   newest written value of every cell stays: the flusher reads it so that an older, delayed write never overwrites a
   newer value. PENDING, FAILED, DEAD, CONFLICT and SUPERSEDED rows are never touched.
3. temp/search.log above SEARCH_LOG_MAX_MB (50): rotated to search.log.1 .. search.log.N (SEARCH_LOG_KEEP, 3); older
   copies are dropped.

It never touches the review data (curation_candidates, review_decisions, rejected_images, resolved_products) or the
queue. When the database does not answer, steps 1 and 2 are skipped: a file is never removed without knowing that no
review row uses it. It stops between files and batches at the time cap (--max-seconds, PRUNE_MAX_SECONDS).

Exit code: 0, or 1 on an unexpected error.
"""

import argparse
import os
import re
import sys
import time

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

DAY_S = 86400
OUTBOX_BATCH = 500
_CANDIDATE_FILE = re.compile(r"^(?P<sha>[0-9a-f]{64})(\.[A-Za-z0-9]{1,8})?$")
_CANDIDATE_TMP = re.compile(r"^\.(?P<sha>[0-9a-f]{64})\.[0-9a-f]{8,64}\.tmp$")

# a newer SYNCED write of the same cell and product: everything the flusher compares (_older_than_written) is equal
_SUPERSEDED_SQL = (
    "FROM sheet_updates a WHERE a.sync_status = 'SYNCED' AND a.registered_at < NOW() - INTERVAL %s DAY "
    "AND (a.seq IS NULL OR a.col_key IS NULL OR EXISTS (SELECT 1 FROM sheet_updates b "
    "WHERE b.sync_status = 'SYNCED' AND b.col_key = a.col_key AND b.ident <=> a.ident "
    "AND b.`row_number` = a.`row_number` AND b.relocated_from <=> a.relocated_from AND b.seq > a.seq))"
)


class _Clock:
    def __init__(self, max_seconds, monotonic=time.monotonic):
        self._monotonic = monotonic
        self._end = monotonic() + max_seconds if max_seconds else None
        self.timed_out = False

    def over(self):
        if self._end is not None and self._monotonic() >= self._end:
            self.timed_out = True
        return self.timed_out


def _settings():
    from catalog_match import settings
    return settings


def _store_dir(root, store_dir=None):
    raw = store_dir or _settings().candidate_store_dir()
    return raw if os.path.isabs(raw) else os.path.join(root, raw)


def _connect():
    import db_connect
    return db_connect.connect()


def referenced_shas(connect=None):
    """Every content_sha256 a curation_candidates row holds (lower case)."""
    conn = (connect or _connect)()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT content_sha256 FROM curation_candidates WHERE content_sha256 IS NOT NULL")
        rows = cursor.fetchall() or []
    finally:
        conn.close()
    shas = set()
    for row in rows:
        value = row.get("content_sha256") if isinstance(row, dict) else row[0]
        if value:
            shas.add(str(value).strip().lower())
    return shas


def prune_candidates(store, referenced, keep_days, apply, clock, now, log):
    result = {"files": 0, "bytes": 0, "kept_in_use": 0, "kept_recent": 0}
    try:
        names = sorted(os.listdir(store))
    except FileNotFoundError:
        return result
    cutoff = now - keep_days * DAY_S
    for name in names:
        if clock.over():
            break
        match = _CANDIDATE_FILE.match(name) or _CANDIDATE_TMP.match(name)
        if not match:
            continue
        path = os.path.join(store, name)
        try:
            info = os.stat(path)
        except OSError:
            continue
        if not os.path.isfile(path):
            continue
        if info.st_mtime >= cutoff:
            result["kept_recent"] += 1
            continue
        if match.group("sha") in referenced and not name.endswith(".tmp"):
            result["kept_in_use"] += 1
            continue
        if apply:
            try:
                os.remove(path)
            except OSError as exc:
                log(f"storage cleanup: could not remove {name}: {type(exc).__name__}")
                continue
        result["files"] += 1
        result["bytes"] += info.st_size
    return result


def prune_outbox(keep_days, apply, clock, connect=None):
    """Superseded SYNCED rows older than keep_days: counted (dry run) or deleted in batches."""
    conn = (connect or _connect)()
    try:
        cursor = conn.cursor()
        if not apply:
            cursor.execute("SELECT COUNT(*) AS n " + _SUPERSEDED_SQL, (int(keep_days),))
            row = cursor.fetchone() or {}
            return {"rows": int(row.get("n") if isinstance(row, dict) else row[0])}
        deleted = 0
        while not clock.over():
            cursor.execute("SELECT a.id AS id " + _SUPERSEDED_SQL + " ORDER BY a.id LIMIT %s",
                           (int(keep_days), OUTBOX_BATCH))
            ids = [int(r["id"] if isinstance(r, dict) else r[0]) for r in cursor.fetchall() or []]
            if not ids:
                break
            cursor.execute(f"DELETE FROM sheet_updates WHERE sync_status = 'SYNCED' AND id IN "
                           f"({','.join(['%s'] * len(ids))})", tuple(ids))
            deleted += cursor.rowcount
            conn.commit()
            if len(ids) < OUTBOX_BATCH:
                break
        return {"rows": deleted}
    finally:
        conn.close()


def rotate_search_log(path, max_mb, keep, apply):
    """search.log above max_mb: search.log -> .1 -> .2 ... -> .keep; copies beyond keep are removed."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return {"rotated": False, "bytes": 0}
    if size <= max_mb * 1024 * 1024:
        return {"rotated": False, "bytes": size}
    if apply:
        folder, base = os.path.split(path)
        for name in os.listdir(folder or "."):
            suffix = name[len(base) + 1:] if name.startswith(base + ".") else ""
            if suffix.isdigit() and int(suffix) >= keep:
                os.remove(os.path.join(folder, name))
        for n in range(keep - 1, 0, -1):
            older = f"{path}.{n}"
            if os.path.exists(older):
                os.replace(older, f"{path}.{n + 1}")
        os.replace(path, f"{path}.1")      # a bridge process still writing keeps writing to search.log.1
    return {"rotated": True, "bytes": size}


def prune(apply=False, root=REPO_ROOT, keep_days=None, max_seconds=None, now=None, connect=None, log=print,
          store_dir=None, search_log=None, log_max_mb=None, log_keep=None):
    """
    The three steps above. Returns {apply, candidates, outbox, search_log, timed_out}; a step that could not run holds
    {"skipped": reason}. Never raises for a database that does not answer.
    """
    settings = _settings()
    keep_days = settings.prune_keep_days() if keep_days is None else max(1, int(keep_days))
    max_seconds = settings.prune_max_seconds() if max_seconds is None else max_seconds
    now = time.time() if now is None else now
    clock = _Clock(max_seconds)
    summary = {"apply": bool(apply), "keep_days": keep_days, "timed_out": False}
    verb = "removed" if apply else "would remove"

    store = _store_dir(root, store_dir)
    try:
        referenced = referenced_shas(connect)
    except Exception as exc:  # noqa: BLE001 - no database: no file is removed
        summary["candidates"] = {"skipped": f"database: {type(exc).__name__}"}
        log(f"storage cleanup: candidate files skipped, the database does not answer ({type(exc).__name__})")
    else:
        found = prune_candidates(store, referenced, keep_days, apply, clock, now, log)
        summary["candidates"] = found
        log(f"storage cleanup: candidate files {verb} {found['files']} ({found['bytes'] / 1048576:.1f} MB); "
            f"kept {found['kept_in_use']} in review data, {found['kept_recent']} newer than {keep_days} days")

    if clock.over():
        summary["outbox"] = {"skipped": "time cap"}
    else:
        try:
            summary["outbox"] = prune_outbox(keep_days, apply, clock, connect)
            log(f"storage cleanup: sheet outbox {verb} {summary['outbox']['rows']} superseded SYNCED rows older than "
                f"{keep_days} days")
        except Exception as exc:  # noqa: BLE001
            summary["outbox"] = {"skipped": f"database: {type(exc).__name__}"}
            log(f"storage cleanup: sheet outbox skipped ({type(exc).__name__})")

    path = search_log or os.path.join(root, "temp", "search.log")
    max_mb = settings.search_log_max_mb() if log_max_mb is None else log_max_mb
    keep = settings.search_log_keep() if log_keep is None else log_keep
    try:
        summary["search_log"] = rotate_search_log(path, max_mb, keep, apply)
        if summary["search_log"]["rotated"]:
            log(f"storage cleanup: temp/search.log is {summary['search_log']['bytes'] / 1048576:.1f} MB; "
                f"{'rotated' if apply else 'would rotate'} (keeping {keep} copies)")
    except OSError as exc:
        summary["search_log"] = {"skipped": type(exc).__name__}
        log(f"storage cleanup: temp/search.log could not be rotated ({type(exc).__name__})")

    summary["timed_out"] = clock.timed_out
    if clock.timed_out:
        log(f"storage cleanup: stopped at the {max_seconds:g}-second cap; the rest waits for the next night")
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="remove (default: a dry run that removes nothing)")
    mode.add_argument("--dry-run", action="store_true", help="say what would be removed (the default)")
    parser.add_argument("--keep-days", type=int, default=None, help="keep anything younger (PRUNE_KEEP_DAYS, 30)")
    parser.add_argument("--max-seconds", type=float, default=None, help="time cap (PRUNE_MAX_SECONDS, 300)")
    args = parser.parse_args(argv)
    os.chdir(REPO_ROOT)
    try:
        import config  # noqa: F401  (.env and the dashboard settings)
        summary = prune(apply=args.apply, keep_days=args.keep_days, max_seconds=args.max_seconds)
    except Exception as exc:  # noqa: BLE001
        print(f"storage cleanup failed: {type(exc).__name__}: {exc}")
        return 1
    if not summary["apply"]:
        print("dry run: nothing was removed (use --apply)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
