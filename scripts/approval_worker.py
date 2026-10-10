# approval_worker.py
"""
Runs the approvals the review page queued (approval_jobs), one after another, then exits.

Started by the bridge action approval_enqueue in a process of its own (approval_jobs.start_worker; on the Ubuntu
server by laqta-approvals.path, and every minute by laqta-approvals.timer for anything left queued). At most
approval_jobs.MAX_WORKERS run at a time: each holds one MariaDB GET_LOCK slot for its whole life, and a worker that
finds every slot taken exits at once (the running ones will take its job). Each job is the very same
cli_bridge.action_select_image call the page used to make, with the same parameters; its result document and the http
status the dashboard would have answered (200 on success, else 500, as ApiController::selectImage) are stored for the
page. A job that raises is stored as failed with a generic reason (the details go to this worker's log only).
While a job runs its row gets a heartbeat every HEARTBEAT_SECONDS, so a long approval is never taken again as stale.
"""

import os
import sys
import threading
import time
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

import approval_jobs  # noqa: E402
import config  # noqa: E402,F401 - loads .env and the settings page's values before the bridge
import db_connect  # noqa: E402


HEARTBEAT_SECONDS = 60
FINISH_TRIES = 3


def _slot(conn):
    """A free worker slot (GET_LOCK held by this connection), or None when MAX_WORKERS already run."""
    db = os.getenv("DB_DATABASE", "automation_db")
    cur = conn.cursor()
    for n in range(approval_jobs.MAX_WORKERS):
        cur.execute("SELECT GET_LOCK(%s, 0) AS got", (f"lq_approval_worker:{db}:{n}"[:64],))
        if (cur.fetchone() or {}).get("got") == 1:
            return n
    return None


def run_job(job, select_image=None):
    """(result document, http status) of one job, never raising."""
    if select_image is None:
        import cli_bridge
        select_image = cli_bridge.action_select_image
    try:
        result = select_image(dict(job["params"]))
    except Exception as exc:  # noqa: BLE001 - the page gets a reason, the log the details
        print(f"[approval] job {job['id']} raised {type(exc).__name__}: {exc}", flush=True)
        result = {"status": "error", "error": "The approval failed on the server; try again."}
    if not isinstance(result, dict):
        result = {"status": "error", "error": "The approval returned nothing; try again."}
    return result, 200 if result.get("status") == "success" else 500


def run_with_heartbeat(job, run=run_job, beat=None, every=HEARTBEAT_SECONDS):
    """run(job) while a thread keeps the job's row alive; a failed beat is logged and tried again on the next tick."""
    beat = beat or approval_jobs.heartbeat
    stop = threading.Event()

    def _beat():
        while not stop.wait(every):
            try:
                beat(job)
            except Exception as exc:  # noqa: BLE001 - a missed beat only shortens the stale window
                print(f"[approval] job {job['id']} heartbeat failed: {type(exc).__name__}", flush=True)

    thread = threading.Thread(target=_beat, name=f"approval-heartbeat-{job['id']}", daemon=True)
    thread.start()
    try:
        return run(job)
    finally:
        stop.set()
        thread.join(timeout=5)


def finish_with_retry(job, result, http, finish=None, tries=FINISH_TRIES, pause=2.0):
    """
    Stores the result, trying again when the database hiccups: the approval already happened (the image is
    uploaded and the sheet written), so losing its result would leave the page waiting until the job went stale and
    ran a second time. None when every try failed (the stale sweep then retries the job).
    """
    finish = finish or approval_jobs.finish
    for attempt in range(1, tries + 1):
        try:
            return finish(job, result, http)
        except Exception as exc:  # noqa: BLE001 - logged; the next try or the stale sweep recovers
            print(f"[approval] job {job['id']} result not stored (try {attempt}/{tries}): {type(exc).__name__}",
                  flush=True)
            if attempt < tries:
                time.sleep(pause * attempt)
    return None


def _drop_kick():
    """This worker answers the pending request: a new request after this point starts the path unit again."""
    try:
        approval_jobs.KICK_PATH.unlink()
    except OSError:
        pass


def main():
    _drop_kick()
    approval_jobs.ensure_schema()
    lock_conn = db_connect.connect()
    try:
        slot = _slot(lock_conn)
        if slot is None:
            print("[approval] every worker slot is busy; the running workers take the queue", flush=True)
            return 0
        approval_jobs.give_up_stale()
        token = uuid.uuid4().hex
        done = 0
        while True:
            job = approval_jobs.claim(token)
            if job is None:
                break
            result, http = run_with_heartbeat(job)
            status = finish_with_retry(job, result, http)
            done += 1
            print(f"[approval] slot {slot}: job {job['id']} row {job.get('row_number')} -> {status}", flush=True)
        print(f"[approval] slot {slot}: {done} job(s), queue empty", flush=True)
        return 0
    finally:
        lock_conn.close()      # releases the slot


if __name__ == "__main__":
    sys.exit(main())
