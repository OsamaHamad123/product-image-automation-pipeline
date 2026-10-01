"""Month-scoped spend counter of the label readers (MariaDB table verifier_spend).

One row per (month, role, provider, model): calls, tokens and the estimated USD from pricing. The
cascade reads the strong model's month spend before a second look (VERIFIER_MONTHLY_BUDGET_USD) and
adds every billed call after it. Months are UTC ('2026-10'), like the providers' invoices.

The table is created on first use (CREATE TABLE IF NOT EXISTS), by the first budget read or the first
write. Reads raise on a database error, including a missing table that cannot be created, so the caller
can stay on the safe side (no paid second look when the spend is unknown); writes never raise.
MemorySpendStore has the same interface for tests and for runs without a database.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional

logger = logging.getLogger(__name__)

TABLE = "verifier_spend"
CREATE_SQL = f"""
    CREATE TABLE IF NOT EXISTS {TABLE} (
        month CHAR(7) NOT NULL,
        role VARCHAR(16) NOT NULL,
        provider VARCHAR(16) NOT NULL,
        model VARCHAR(96) NOT NULL,
        calls INT NOT NULL DEFAULT 0,
        input_tokens BIGINT NOT NULL DEFAULT 0,
        output_tokens BIGINT NOT NULL DEFAULT 0,
        usd DECIMAL(14,6) NOT NULL DEFAULT 0,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (month, role, provider, model)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""
ADD_SQL = f"""
    INSERT INTO {TABLE} (month, role, provider, model, calls, input_tokens, output_tokens, usd)
    VALUES (%s, %s, %s, %s, 1, %s, %s, %s)
    ON DUPLICATE KEY UPDATE calls = calls + 1, input_tokens = input_tokens + VALUES(input_tokens),
        output_tokens = output_tokens + VALUES(output_tokens), usd = usd + VALUES(usd)
"""
ROLE_SPEND_SQL = f"SELECT COALESCE(SUM(usd), 0) AS usd FROM {TABLE} WHERE month = %s AND role = %s"
MONTH_SQL = f"""
    SELECT role, provider, model, calls, input_tokens, output_tokens, usd FROM {TABLE}
    WHERE month = %s ORDER BY usd DESC, role, provider, model
"""


def current_month(now: Optional[datetime] = None) -> str:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is not None:
        now = now.astimezone(timezone.utc)
    return now.strftime("%Y-%m")


def _row(entry: Mapping[str, Any]):
    return (str(entry.get("role") or "primary")[:16], str(entry.get("provider") or "")[:16],
            str(entry.get("model") or "")[:96], max(0, int(entry.get("input_tokens") or 0)),
            max(0, int(entry.get("output_tokens") or 0)), max(0.0, float(entry.get("usd") or 0.0)))


class MariaDbSpendStore:
    """verifier_spend through local_cache_db.get_db_connection() (the DB_* settings of the worker)."""

    _ready_for: set = set()
    _lock = threading.Lock()

    def __init__(self, connect=None):
        self._connect = connect

    def _conn(self):
        if self._connect is not None:
            return self._connect()
        import local_cache_db
        return local_cache_db.get_db_connection()

    def _ensure(self, conn, force: bool = False) -> None:
        import os
        key = (os.getenv("DB_HOST", "127.0.0.1"), os.getenv("DB_DATABASE", "automation_db"), id(self._connect))
        if key in self._ready_for and not force:
            return
        cursor = conn.cursor()
        cursor.execute(CREATE_SQL)
        with self._lock:
            self._ready_for.add(key)

    def role_spend(self, role: str = "strong", month: Optional[str] = None) -> float:
        """Estimated USD spent this month by `role` (0 before the first billed call). Raises on a database error.

        A missing table means "nothing spent" only while spend CAN be recorded: the table is created here,
        and when that fails (no CREATE privilege, read-only database) this raises like any other database
        error, so a database that cannot count the spend never turns into an unlimited budget.
        """
        conn = self._conn()
        try:
            cursor = conn.cursor()
            try:
                cursor.execute(ROLE_SPEND_SQL, (month or current_month(), role))
            except Exception as exc:
                if not _missing_table(exc):
                    raise
                self._ensure(conn, force=True)      # raises when the table cannot be created
                return 0.0
            row = cursor.fetchone() or {}
            value = row.get("usd") if isinstance(row, dict) else (row[0] if row else 0)
            return float(value or 0.0)
        finally:
            _close(conn)

    def add(self, entry: Mapping[str, Any], month: Optional[str] = None) -> bool:
        """Count one billed call. Never raises: a lost row is logged, the search goes on."""
        try:
            conn = self._conn()
        except Exception as exc:
            logger.warning("verifiers: spend not recorded (%s)", type(exc).__name__)
            return False
        try:
            self._ensure(conn)
            cursor = conn.cursor()
            cursor.execute(ADD_SQL, (month or current_month(),) + _row(entry))
            conn.commit()
            return True
        except Exception as exc:
            logger.warning("verifiers: spend not recorded (%s)", type(exc).__name__)
            return False
        finally:
            _close(conn)

    def month_rows(self, month: Optional[str] = None) -> List[Dict[str, Any]]:
        """Every row of the month (role, provider, model, calls, tokens, usd); read only, [] before the
        first billed call. Raises on a database error."""
        conn = self._conn()
        try:
            cursor = conn.cursor()
            try:
                cursor.execute(MONTH_SQL, (month or current_month(),))
            except Exception as exc:
                if _missing_table(exc):
                    return []
                raise
            out = []
            for r in cursor.fetchall() or []:
                r = dict(r)
                out.append({"role": r.get("role"), "provider": r.get("provider"), "model": r.get("model"),
                            "calls": int(r.get("calls") or 0), "input_tokens": int(r.get("input_tokens") or 0),
                            "output_tokens": int(r.get("output_tokens") or 0),
                            "usd": round(float(r.get("usd") or 0.0), 6)})
            return out
        finally:
            _close(conn)


class MemorySpendStore:
    """In-process spend counter with the MariaDbSpendStore interface (tests, runs without a database)."""

    def __init__(self, spent: Optional[Dict[str, float]] = None, fail_reads: bool = False):
        self._rows: Dict[tuple, Dict[str, Any]] = {}
        self._base = dict(spent or {})          # role -> usd already spent this month
        self.fail_reads = fail_reads
        self.added: List[Dict[str, Any]] = []
        self._lock = threading.Lock()

    def role_spend(self, role: str = "strong", month: Optional[str] = None) -> float:
        if self.fail_reads:
            raise RuntimeError("spend store unavailable")
        month = month or current_month()
        with self._lock:
            extra = sum(r["usd"] for (m, rl, _p, _mo), r in self._rows.items() if m == month and rl == role)
        return float(self._base.get(role, 0.0)) + extra

    def add(self, entry: Mapping[str, Any], month: Optional[str] = None) -> bool:
        month = month or current_month()
        role, provider, model, tin, tout, usd = _row(entry)
        with self._lock:
            row = self._rows.setdefault((month, role, provider, model), {
                "role": role, "provider": provider, "model": model, "calls": 0, "input_tokens": 0,
                "output_tokens": 0, "usd": 0.0})
            row["calls"] += 1
            row["input_tokens"] += tin
            row["output_tokens"] += tout
            row["usd"] = round(row["usd"] + usd, 6)
            self.added.append(dict(entry))
        return True

    def month_rows(self, month: Optional[str] = None) -> List[Dict[str, Any]]:
        if self.fail_reads:
            raise RuntimeError("spend store unavailable")
        month = month or current_month()
        with self._lock:
            rows = [dict(r) for (m, *_), r in self._rows.items() if m == month]
        return sorted(rows, key=lambda r: (-r["usd"], r["role"], r["provider"], r["model"]))


def _close(conn) -> None:
    try:
        conn.close()
    except Exception:
        pass


def _missing_table(exc: Exception) -> bool:
    """MariaDB error 1146 (table doesn't exist): nothing was billed yet, the table comes with the first call."""
    args = getattr(exc, "args", None) or ()
    return bool(args) and args[0] == 1146
