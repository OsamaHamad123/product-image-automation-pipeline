"""Nightly run: queue the sheet rows that still have no final image link, then run the worker until the queue is empty.

Windows Task Scheduler starts it every night; register it with scripts/schedule_nightly.ps1. By hand, from the
repository root:

    .venv\\Scripts\\python.exe -X utf8 scripts\\run_nightly.py

It reuses main.py's entry points in one process and in the same order as the dashboard's "run all" button:
main.run_enqueue_mode(), then main.run_worker_mode() when the enqueue succeeded. Four settings are pinned right
after main.load_run_config(), so neither .env, the settings page nor the last dashboard run's
temp/run_config.json can change them:

    ROW_FILTER = ''                  every sheet row, not the last run's row range
    BRAND_FILTER = ''                every brand
    FORCE_OVERWRITE_IMAGES = False   a row that already has a final image link is never queued
    AUTO_PUBLISH_ENABLED = False     never publishes: results wait for review in the dashboard

The run writes nothing to the sheet beyond what those entry points already write (the enqueue adds the image-link
column header when it is missing); with auto-publish off the worker writes no image link at all. It is skipped
when a worker already holds temp/pipeline.lock. Output goes to temp/nightly/nightly_YYYY-MM-DD.log (the newest 30
logs are kept). Exit code 0 when the queue was worked or the run was skipped, 1 otherwise.
"""

import datetime
import glob
import logging
import os
import sys
import time
import traceback

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

LOG_DIR = os.path.join("temp", "nightly")
KEEP_LOGS = 30
LOCK_FILE = os.path.join("temp", "pipeline.lock")      # main.run_worker_mode's lock
# The dashboard writes 'STARTING' to the lock while its enqueue runs (ApiController::runAll, same grace period).
STARTING_GRACE_S = 300
FINISHED_STATUSES = ("idle", "curation_pending")

NIGHTLY_SETTINGS = {
    "ROW_FILTER": "",
    "BRAND_FILTER": "",
    "FORCE_OVERWRITE_IMAGES": False,
    "AUTO_PUBLISH_ENABLED": False,
}


def say(message):
    print(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] [Nightly] {message}", flush=True)


def open_log(log_dir=LOG_DIR, today=None, keep=KEEP_LOGS):
    """Today's log file (UTF-8, appended); older nightly logs beyond the newest `keep` are deleted."""
    os.makedirs(log_dir, exist_ok=True)
    path = os.path.join(log_dir, f"nightly_{(today or datetime.date.today()).isoformat()}.log")
    older = sorted(p for p in glob.glob(os.path.join(log_dir, "nightly_*.log"))
                   if os.path.abspath(p) != os.path.abspath(path))
    for stale in older[:max(0, len(older) - (keep - 1))]:
        try:
            os.remove(stale)
        except OSError:
            pass
    return open(path, "a", encoding="utf-8", errors="replace"), path


def pin_nightly_settings(main_module, config_module, settings=NIGHTLY_SETTINGS):
    """Wrap main.load_run_config so the nightly settings win over .env, the DB settings and run_config.json."""
    original = main_module.load_run_config

    def load_run_config():
        original()
        for name, value in settings.items():
            setattr(config_module, name, value)
        say("settings pinned: every row without a final link, no reprocessing, auto-publish OFF")

    main_module.load_run_config = load_run_config
    return load_run_config


def worker_busy(main_module, lock_file=LOCK_FILE, now=time.time):
    """True while a worker (started from the dashboard or an earlier nightly run) holds the pipeline lock."""
    try:
        with open(lock_file, "r", encoding="utf-8", errors="replace") as fh:
            content = fh.read().strip()
    except OSError:
        return False
    if content == "STARTING":
        try:
            return now() - os.path.getmtime(lock_file) < STARTING_GRACE_S
        except OSError:
            return False
    return main_module._another_worker_running(lock_file)


def _hold_lock(lock_file=LOCK_FILE):
    """Our PID in the lock during the enqueue, so the dashboard shows a run and refuses to start a second one."""
    os.makedirs(os.path.dirname(lock_file) or ".", exist_ok=True)
    with open(lock_file, "w") as fh:
        fh.write(str(os.getpid()))


def _release_lock(lock_file=LOCK_FILE):
    try:
        with open(lock_file, "r") as fh:
            mine = fh.read().strip() == str(os.getpid())
        if mine:
            os.remove(lock_file)
    except OSError:
        pass


def run():
    """Enqueue, then work the queue until it is empty. Returns the process exit code."""
    import config
    import local_cache_db
    import main

    if worker_busy(main):
        say(f"a worker is already running ({LOCK_FILE}); nothing to do tonight")
        return 0
    pin_nightly_settings(main, config)
    _hold_lock()
    # A new run, like the dashboard's run button: a stop or pause request left over from an earlier run must not
    # stop tonight's worker, and the dashboard shows 'reading the sheet' instead of the last run's numbers.
    if not local_cache_db.prepare_run():
        say("could not reset the run state in the database; continuing (the enqueue reports database errors)")
    try:
        say("enqueue: reading the sheet")
        main.run_enqueue_mode()
    except SystemExit as exc:
        if exc.code not in (0, None):
            say(f"enqueue failed (exit {exc.code}); the worker is not started")
            _release_lock()
            return 1
    except Exception:
        say("enqueue raised:\n" + traceback.format_exc())
        _release_lock()
        return 1

    say("worker: searching the queued rows")
    try:
        main.run_worker_mode()
    except SystemExit as exc:
        if exc.code not in (0, None):
            say(f"worker exited with {exc.code}")
            return 1
    finally:
        _release_lock()
    state = local_cache_db.get_automation_state()
    status = state.get("status")
    say(f"worker finished: status={status} notice={state.get('notice') or '-'}")
    return 0 if status in FINISHED_STATUSES else 1


def main(argv=None):
    os.chdir(REPO_ROOT)
    log_stream, path = open_log()
    saved = sys.stdout, sys.stderr
    # Before main.py is imported: config prints at import time and the scheduled task has no console to read.
    sys.stdout = sys.stderr = log_stream
    handler = logging.StreamHandler(log_stream)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.getLogger().addHandler(handler)
    try:
        say(f"nightly run started in {REPO_ROOT} with {sys.executable}; log {path}")
        try:
            code = run()
        except Exception:
            say("nightly run failed:\n" + traceback.format_exc())
            code = 1
        say(f"nightly run finished (exit {code})")
        return code
    finally:
        logging.getLogger().removeHandler(handler)
        sys.stdout, sys.stderr = saved
        log_stream.close()


if __name__ == "__main__":
    sys.exit(main())
