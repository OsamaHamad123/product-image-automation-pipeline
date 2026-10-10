# approval_jobs.py
# Approvals run on the server, not in the reviewer's page (owner, 2026-10-08: «ليش ما بقدر اتنقل»).
#
# The review page used to send each approval and wait for it (processing, Cloudinary upload, sheet write: seconds), so
# leaving the page asked «Leave site?» and could stop it. Now an approval is a row of approval_jobs: the bridge action
# approval_enqueue adds it and starts a worker (scripts/approval_worker.py) in a process of its own, and answers at
# once; the page follows the job by its id (/api/approval-jobs, read by PHP straight from this table) and can be left.
#
# - The job runs the very same cli_bridge.action_select_image with the very same parameters the page sent (decision
#   D12: one path, no second transport): its result document, its refusals (already_approved / state_changed /
#   quality) and its http status are stored as they are, and the page settles the job from them as before.
# - One product, one job: an approval of a product (sku_key) that already has a queued or running job returns that job.
# - Workers: at most MAX_WORKERS at a time (MariaDB GET_LOCK slots), each takes the oldest queued job with an atomic
#   UPDATE (no SKIP LOCKED: the owner's PC runs MariaDB 10.4) and exits when nothing is left. A job whose worker died
#   (running, untouched for STALE_MINUTES) is taken again, at most MAX_ATTEMPTS times in all.
# - Finished jobs are kept KEEP_DAYS days (the dashboard shows the recent failures), then deleted.

import json
import logging
import os
import subprocess
import sys
import uuid
from pathlib import Path

import db_connect
import schema_mark

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent
WORKER = ROOT / "scripts" / "approval_worker.py"
LOG_PATH = ROOT / "temp" / "approval_worker.log"

KINDS = ("select",)
ACTIVE = ("queued", "running")
MAX_WORKERS = 2               # same as the page's former concurrency: each product is serialised by its publish lock
STALE_MINUTES = 15            # a running job no worker touched for this long had its worker die
MAX_ATTEMPTS = 3
KEEP_DAYS = 7

_SCHEMA = """
    CREATE TABLE IF NOT EXISTS approval_jobs (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        kind VARCHAR(16) NOT NULL,
        sku_key VARCHAR(64) NULL,
        `row_number` INT NULL,
        label VARCHAR(255) NULL,
        params_json MEDIUMTEXT NOT NULL,
        status VARCHAR(16) NOT NULL DEFAULT 'queued',
        result_json MEDIUMTEXT NULL,
        http_status INT NULL,
        created_by VARCHAR(100) NULL,
        claim_token CHAR(32) NULL,
        attempts INT NOT NULL DEFAULT 0,
        cache_cleared TINYINT NOT NULL DEFAULT 0,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        started_at TIMESTAMP NULL,
        finished_at TIMESTAMP NULL,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        INDEX idx_approval_jobs_status (status),
        INDEX idx_approval_jobs_sku (sku_key)
    ) ENGINE=InnoDB CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
"""


def ensure_schema():
    """The table, once per version of this file (schema_mark)."""
    mark = schema_mark.fingerprint(__file__)
    if schema_mark.is_current("approval_jobs", mark):
        return True
    conn = db_connect.connect(dict_cursor=False)
    try:
        cur = conn.cursor()
        cur.execute(_SCHEMA)
        # «تجاهل» اعتماد فشل (لوحة «اعتمادات ما زبطت» بصفحة المراجعة، ApiController::dismissApprovalJob). جدول الخادم
        # الموجود من قبل بياخد العمود هون (MariaDB 10.4+: IF NOT EXISTS)
        cur.execute("ALTER TABLE approval_jobs ADD COLUMN IF NOT EXISTS dismissed_at TIMESTAMP NULL")
        conn.commit()
    finally:
        conn.close()
    schema_mark.save("approval_jobs", mark)
    return True


def _conn():
    ensure_schema()
    return db_connect.connect()


def enqueue(kind, params, created_by=None):
    """
    Add an approval job (or return the active one of the same product). params: what the page sent for
    action_select_image. Returns {'job_id', 'existing'}.
    """
    if kind not in KINDS:
        raise ValueError(f"unknown approval job kind {kind!r}")
    sku = str(params.get("sku_key") or "").strip() or None
    try:
        row = int(params.get("row_number"))
    except (TypeError, ValueError):
        row = None
    label = str(params.get("product_name") or "").strip()[:255] or None
    conn = _conn()
    try:
        cur = conn.cursor()
        if sku:
            cur.execute("SELECT id FROM approval_jobs WHERE sku_key = %s AND status IN ('queued', 'running') "
                        "ORDER BY id LIMIT 1", (sku,))
            found = cur.fetchone()
            if found:
                return {"job_id": int(found["id"]), "existing": True}
        cur.execute("INSERT INTO approval_jobs (kind, sku_key, `row_number`, label, params_json, created_by) "
                    "VALUES (%s, %s, %s, %s, %s, %s)",
                    (kind, sku, row, label, json.dumps(params, ensure_ascii=False), (created_by or None)))
        conn.commit()
        return {"job_id": int(cur.lastrowid), "existing": False}
    finally:
        conn.close()


def claim(token=None):
    """The oldest queued job (or one whose worker died), marked running for this worker; None when nothing is left."""
    token = token or uuid.uuid4().hex
    conn = _conn()
    try:
        cur = conn.cursor()
        for _ in range(5):     # another worker may take the same row first: try the next one
            cur.execute("SELECT id FROM approval_jobs WHERE status = 'queued' OR (status = 'running' AND attempts < %s "
                        "AND updated_at < NOW() - INTERVAL %s MINUTE) ORDER BY id LIMIT 1",
                        (MAX_ATTEMPTS, STALE_MINUTES))
            row = cur.fetchone()
            if not row:
                return None
            cur.execute("UPDATE approval_jobs SET status = 'running', claim_token = %s, attempts = attempts + 1, "
                        "started_at = NOW() WHERE id = %s AND (status = 'queued' OR (status = 'running' AND "
                        "updated_at < NOW() - INTERVAL %s MINUTE))", (token, row["id"], STALE_MINUTES))
            conn.commit()
            if cur.rowcount == 1:
                cur.execute("SELECT * FROM approval_jobs WHERE id = %s", (row["id"],))
                job = cur.fetchone()
                job["params"] = json.loads(job.pop("params_json") or "{}")
                return job
        return None
    finally:
        conn.close()


def finish(job, result, http_status):
    """
    Store the job's result document as the page would have received it: 'done' or 'failed'. 'superseded' when this
    worker no longer holds the job (it was taken again after this worker looked dead): nothing is written.
    """
    status = "done" if (result or {}).get("status") == "success" else "failed"
    conn = _conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE approval_jobs SET status = %s, result_json = %s, http_status = %s, finished_at = NOW() "
            "WHERE id = %s AND claim_token = %s",
            (status, json.dumps(result or {}, ensure_ascii=False, default=str), int(http_status), job["id"],
             job["claim_token"]))
        conn.commit()
        return status if cur.rowcount == 1 else "superseded"
    finally:
        conn.close()


def give_up_stale():
    """Jobs whose worker died MAX_ATTEMPTS times: failed with a reason the page shows, instead of running forever."""
    conn = _conn()
    try:
        conn.cursor().execute(
            "UPDATE approval_jobs SET status = 'failed', http_status = 500, finished_at = NOW(), result_json = %s "
            "WHERE status = 'running' AND attempts >= %s AND updated_at < NOW() - INTERVAL %s MINUTE",
            (json.dumps({"status": "error", "error": "The approval stopped in the middle several times; try again."}),
             MAX_ATTEMPTS, STALE_MINUTES))
        conn.cursor().execute("DELETE FROM approval_jobs WHERE status IN ('done', 'failed') "
                              "AND finished_at < NOW() - INTERVAL %s DAY", (KEEP_DAYS,))
        conn.commit()
    finally:
        conn.close()


def start_worker(python=None, popen=subprocess.Popen):
    """A worker in a process of its own (the same way as the local-index refresh), so the bridge answers at once."""
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
        options = {"cwd": str(ROOT), "stdin": subprocess.DEVNULL, "close_fds": True, "env": env}
        if os.name == "nt":      # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP: no console, not tied to the bridge
            options["creationflags"] = 0x00000008 | 0x00000200
        else:
            options["start_new_session"] = True
        with open(LOG_PATH, "a", encoding="utf-8") as log:
            popen([python or sys.executable, "-X", "utf8", str(WORKER)], stdout=log, stderr=subprocess.STDOUT,
                  **options)
        return True
    except Exception as exc:  # noqa: BLE001 - the job stays queued; the next approval starts a worker again
        logger.warning("approval worker could not start (%s)", type(exc).__name__)
        return False
