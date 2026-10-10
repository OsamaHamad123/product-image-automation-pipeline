"""Nightly run: queue the sheet rows that still have no final image link, then run the worker until the queue is empty.

Windows Task Scheduler starts it every night; register it with scripts/schedule_nightly.ps1. By hand, from the
repository root:

    .venv\\Scripts\\python.exe -X utf8 scripts\\run_nightly.py [--max-hours 8]

It reuses main.py's entry points in one process and in the same order as the dashboard's "run all" button:
main.run_enqueue_mode(), then main.run_worker_mode() when the enqueue succeeded. The night never reads
temp/run_config.json, the options of the last run started from the dashboard (its row range, reprocess, skip cache,
...: a test run on one row must not shape the next night); it takes the settings page's values. Five settings are
pinned right after main.load_run_config(), so neither .env nor the settings page can change them:

    ROW_FILTER = ''                  every sheet row, not the last run's row range
    BRAND_FILTER = ''                every brand
    FORCE_OVERWRITE_IMAGES = False   a row that already has a final image link is never queued
    AUTO_PUBLISH_ENABLED = False     never publishes: results wait for review in the dashboard
    AUTO_PUBLISH_STRICT_LANE = False nor through the strict lane (on by default for the dashboard's runs, and it
                                     publishes without AUTO_PUBLISH_ENABLED once its reviews prove it)

The run writes nothing to the sheet beyond what those entry points already write (the enqueue adds the image-link
column header when it is missing); with auto-publish off the worker writes no image link at all. It is skipped
when a live worker already holds temp/pipeline.lock (main._another_worker_running: a stale lock left by a crashed
or killed run, whose PID now belongs to another process, is removed and never skips a night).

When a run stops on an outage (the database does not answer, Google Sheets does not answer: a timeout, a lost
connection, HTTP 429 / 5xx; the search providers are down) the whole run starts again 15 minutes later, and once more
60 minutes after that. A missing or broken credentials file, a sheet that is not found or not shared with the service
account, or any other error is a failure (exit 1) that waiting does not fix: it is not retried.

The night stays inside Task Scheduler's time limit (schedule_nightly.ps1 -MaxHours, passed here as --max-hours;
default 8): the worker takes no new row 15 minutes before it and stops at a row boundary (stop reason time_limit,
exit 3), and a retry that would start less than 45 minutes before it is skipped, so the night's report is always
written before Task Scheduler ends the task. Then one report is
written for the night (run_report.py): a row in the run_history table and temp/nightly/last_report.json.

Output goes to temp/nightly/nightly_YYYY-MM-DD.log (the newest 30 logs are kept; the dashboard shows them on the
health page). Exit code (Task Scheduler's "Last Run Result"):

    0  the queue was worked to the end, or another live worker holds the lock
    1  failed: a sheet setting is wrong or an unexpected error (see the log)
    2  an outage that was still there after the two retries
    3  stopped before the queue was empty: the owner pressed stop (the dashboard asks the worker to stop; it
       finishes the products in progress, and this run writes the night's report and exits 3), or a limit stopped
       the worker (daily budget, Serper credit, the task's time limit)

A worker that has not stopped 90 seconds after the stop request is ended by the dashboard: the process is killed, so
Task Scheduler shows 1 (taskkill's code), but the night still gets its report, written by the dashboard
(cli_bridge run_control) from the lock with the outcome "stopped".

On a server (systemd): SIGTERM / SIGHUP (systemctl stop, a reboot) stop the night like the stop button, at once: the
worker takes no new row, gives the products in progress SHUTDOWN_GRACE_S seconds and hands the rest back to the queue;
there is no retry, the night's report is written (stop reason 'shutdown') and the exit code is 3.

After the report, the storage cleanup runs (scripts/prune_storage.py --apply; NIGHTLY_PRUNE_ENABLED, on by default)
within PRUNE_MAX_SECONDS and inside the time limit. With HEALTHCHECK_URL set (a healthchecks.io-style ping URL), the
night pings <url>/start when it starts, then <url> when it ends with exit code 0 or 3, or <url>/fail with 1 or 2
(5-second timeout; a ping that fails never changes the night).
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

import stop_signals  # noqa: E402  (no import side effects; config is imported later, after the log is open)

LOG_DIR = os.path.join("temp", "nightly")
KEEP_LOGS = 30
LOCK_FILE = os.path.join("temp", "pipeline.lock")      # main.run_worker_mode's lock
# The dashboard writes 'STARTING' to the lock while its enqueue runs (ApiController::runAll, same grace period).
STARTING_GRACE_S = 300
FINISHED_STATUSES = ("idle", "curation_pending")
# After a run stopped on an outage: wait 15 minutes and run again, then 60 minutes and run a last time.
RETRY_WAITS_S = (15 * 60, 60 * 60)
# Task Scheduler ends the task after -MaxHours (schedule_nightly.ps1 passes it as --max-hours; NIGHTLY_MAX_HOURS by
# hand). Killed at that limit the night would have no report, so the runner works inside it:
DEFAULT_MAX_HOURS = 8.0
# a retry is skipped when it would start less than this before the limit,
RETRY_MARGIN_S = 45 * 60
# and the worker takes no new row this long before the limit (the products in progress finish, the report is written).
FINISH_MARGIN_S = 15 * 60

HEALTHCHECK_TIMEOUT_S = 5
# the night's exit codes that ping the healthcheck as a success: the queue was worked, or a limit / stop ended it
HEALTHY_EXIT_CODES = (0, 3)

NIGHTLY_SETTINGS = {
    "ROW_FILTER": "",
    "BRAND_FILTER": "",
    "FORCE_OVERWRITE_IMAGES": False,
    "AUTO_PUBLISH_ENABLED": False,
    "AUTO_PUBLISH_STRICT_LANE": False,
}


def max_hours_from(argv=None, environ=None):
    """--max-hours N / --max-hours=N, else NIGHTLY_MAX_HOURS, else DEFAULT_MAX_HOURS; a value outside 1..23 is ignored."""
    argv = list(sys.argv[1:] if argv is None else argv)
    environ = os.environ if environ is None else environ
    candidates = []
    for i, arg in enumerate(argv):
        if arg.startswith("--max-hours="):
            candidates.append(arg.split("=", 1)[1])
        elif arg == "--max-hours" and i + 1 < len(argv):
            candidates.append(argv[i + 1])
    candidates.append(environ.get("NIGHTLY_MAX_HOURS"))
    for value in candidates:
        try:
            hours = float(str(value).strip())
        except (TypeError, ValueError):
            continue
        if 1 <= hours <= 23:
            return hours
    return DEFAULT_MAX_HOURS


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
    """Wrap main.load_run_config: no temp/run_config.json (the dashboard's per-run options), and the nightly settings
    win over .env and the DB settings."""
    original = main_module.load_run_config

    def load_run_config(run_file=None):
        original(run_file=None)
        for name, value in settings.items():
            setattr(config_module, name, value)
        say("settings pinned: every row without a final link, no reprocessing, auto-publish OFF")

    main_module.load_run_config = load_run_config
    return load_run_config


def worker_busy(main_module, lock_file=LOCK_FILE, now=time.time):
    """True while a live worker (started from the dashboard or an earlier nightly run) holds the pipeline lock."""
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


def _hold_lock(main_module, lock_file=LOCK_FILE, now=time.time):
    """Our lock during the enqueue, so the dashboard shows a run and refuses to start a second one.
    False when another process took the lock first (it is created exclusively), including a dashboard run whose
    STARTING appeared after worker_busy looked; a STARTING older than STARTING_GRACE_S is an abandoned enqueue."""
    lock = main_module.read_lock(lock_file)
    if lock is not None and lock["kind"] == "starting" and now() - lock["mtime"] >= STARTING_GRACE_S:
        main_module._remove_lock_if_unchanged(lock_file, lock["raw"])
    return main_module.acquire_lock("nightly", lock_file, trigger="nightly", take_starting=False)


def _release_lock(main_module, lock_file=LOCK_FILE):
    """Removes the lock only while it is ours (the worker, in this process, may already have removed it)."""
    main_module.release_own_lock(lock_file)


def reason_from_state(state):
    """The stop reason the automation state describes, for a worker that left no main.LAST_WORKER."""
    status = (state or {}).get("status")
    notice = str((state or {}).get("notice") or "")
    if status in FINISHED_STATUSES:
        return None
    if status == "db_unavailable" or notice.startswith("DB_UNAVAILABLE"):
        return "db_unavailable"
    if status == "provider_down":
        return "provider_down"
    if notice.startswith("SHEETS_UNAVAILABLE"):
        return "sheets_unavailable"
    if notice.startswith("SHEET_CONFIG"):
        return "sheet_config"
    return "worker_error"


def run_once(main_module, db, deadline_ts=None):
    """
    One whole run, like the dashboard's run button: prepare the run state, enqueue, work the queue.
    deadline_ts: the worker takes no new row after it (stop reason time_limit).
    Returns the attempt {stop_reason, run_id, worker_id, notice, message}; stop_reason None = the queue was worked.
    """
    main_module.LAST_ENQUEUE.clear()
    main_module.LAST_WORKER.clear()
    if not _hold_lock(main_module):
        say("another run took the lock just now; it works the queue")
        return {"stop_reason": "another_worker"}
    # A new run, like the dashboard's run button: a stop or pause request left over from an earlier run must not
    # stop tonight's worker, and the dashboard shows 'reading the sheet' instead of the last run's numbers.
    if not db.prepare_run():
        if not db.db_available():
            say("the database does not answer; the run is not started")
            _release_lock(main_module)
            return {"stop_reason": "db_unavailable", "message": main_module.DB_UNAVAILABLE_NOTICE}
        say("could not reset the run state in the database; continuing (the enqueue reports database errors)")
    try:
        say("enqueue: reading the sheet")
        main_module.run_enqueue_mode()
    except KeyboardInterrupt:
        say("stop signal during the enqueue; the worker is not started")
        _release_lock(main_module)
        with stop_signals.deferred():
            try:
                db.stop_run(worker_active=False)
            except Exception:
                say("could not settle the run state after the stop:\n" + traceback.format_exc())
        return {"stop_reason": _stop_reason()}
    except SystemExit as exc:
        if exc.code not in (0, None):
            reason = main_module.LAST_ENQUEUE.get("reason") or "enqueue_failed"
            say(f"enqueue failed (exit {exc.code}, {reason}); the worker is not started")
            _release_lock(main_module)
            message = main_module.LAST_ENQUEUE.get("message")
            return {"stop_reason": reason, "message": f"ENQUEUE_FAILED: {message}" if message else None}
    except Exception:
        say("enqueue raised:\n" + traceback.format_exc())
        _release_lock(main_module)
        return {"stop_reason": "enqueue_error" if db.db_available() else "db_unavailable"}

    say("worker: searching the queued rows")
    crashed = None
    stopped = False
    try:
        main_module.run_worker_mode(trigger="nightly", report=False, deadline_ts=deadline_ts)
    except KeyboardInterrupt:
        # the worker handed the rows in progress back and settled the run state (its "stopped" path)
        say("stop signal: the worker stopped")
        stopped = True
    except SystemExit as exc:
        if exc.code not in (0, None):
            say(f"worker exited with {exc.code}")
            crashed = "worker_error"
    except Exception:
        say("worker raised:\n" + traceback.format_exc())
        crashed = "worker_error" if db.db_available() else "db_unavailable"
    finally:
        _release_lock(main_module)
    info = dict(main_module.LAST_WORKER)
    info.pop("health", None)
    if crashed:
        info["stop_reason"] = crashed
    elif stopped and not info.get("stop_reason"):
        info["stop_reason"] = _stop_reason()
    elif not info:
        state = db.get_automation_state()
        info = {"stop_reason": reason_from_state(state), "run_id": state.get("run_id"), "notice": state.get("notice")}
    say(f"worker finished: stop={info.get('stop_reason') or 'queue empty'} notice={info.get('notice') or '-'}")
    return info


def _stop_reason():
    """'shutdown' after SIGTERM / SIGHUP, 'stopped' after Ctrl+C."""
    return "shutdown" if stop_signals.requested() else "stopped"


def ping_healthcheck(kind="", url=None, get=None):
    """
    Dead-man's switch: GET <HEALTHCHECK_URL>/start, <HEALTHCHECK_URL> (success) or <HEALTHCHECK_URL>/fail, with a
    HEALTHCHECK_TIMEOUT_S timeout. No URL: nothing is sent. Returns True on a 2xx answer; never raises, and the URL
    is never logged (it identifies the check).
    """
    label = kind or "success"
    try:
        if url is None:
            from catalog_match import settings as cm_settings
            url = cm_settings.healthcheck_url()
        if not url:
            return False
        if get is None:
            import requests
            get = requests.get
        response = get(url.rstrip("/") + (f"/{kind}" if kind else ""), timeout=HEALTHCHECK_TIMEOUT_S)
        status = int(getattr(response, "status_code", 0) or 0)
        say(f"healthcheck ping ({label}): HTTP {status}")
        return 200 <= status < 300
    except Exception as exc:  # noqa: BLE001 - a ping never changes the night
        say(f"healthcheck ping ({label}) failed ({type(exc).__name__}); the run is not affected")
        return False


def _load_prune():
    import importlib.util
    spec = importlib.util.spec_from_file_location("prune_storage", os.path.join(REPO_ROOT, "scripts", "prune_storage.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def prune_storage(seconds_left=None, prune=None):
    """
    The storage cleanup at the end of the night (scripts/prune_storage.py --apply) when NIGHTLY_PRUNE_ENABLED, within
    PRUNE_MAX_SECONDS and the seconds left before the task's time limit. Returns its summary, or None (off, no time,
    stopped, or it failed). Never raises.
    """
    try:
        from catalog_match import settings as cm_settings
        if not cm_settings.nightly_prune_enabled():
            say("storage cleanup: off (NIGHTLY_PRUNE_ENABLED)")
            return None
        cap = float(cm_settings.prune_max_seconds())
        if seconds_left is not None:
            cap = min(cap, seconds_left - 60)
        if cap < 10:
            say("storage cleanup: skipped, too close to the task's time limit")
            return None
        if stop_signals.requested():
            say("storage cleanup: skipped after the stop signal")
            return None
        prune = prune or _load_prune().prune
        return prune(apply=True, max_seconds=cap, log=say)
    except KeyboardInterrupt:
        say("storage cleanup: stopped by a stop signal")
        return None
    except Exception:
        say("storage cleanup failed:\n" + traceback.format_exc())
        return None


def start_index_refresh():
    """
    The local catalog index is refreshed in the background when a store's last complete harvest is older than
    LOCAL_INDEX_REFRESH_DAYS (catalog_match.index_refresh): a thread that never delays the enqueue or the search, within
    LOCAL_INDEX_REFRESH_MAX_S seconds. Returns the thread, or None (off, or nothing to start). Never raises.
    """
    try:
        from catalog_match import index_refresh
        thread = index_refresh.start_background("nightly")
        if thread is not None:
            say("local catalog index: refreshing stale stores in the background")
        return thread
    except Exception as exc:
        say(f"local catalog index: the refresh could not start ({type(exc).__name__})")
        return None


def run(sleep=time.sleep, now=time.time, max_hours=DEFAULT_MAX_HOURS):
    """
    Enqueue, then work the queue until it is empty; the whole run again after an outage (RETRY_WAITS_S).
    Everything happens inside Task Scheduler's limit (max_hours from the start): the worker stops taking rows
    FINISH_MARGIN_S before it, and a retry that would start less than RETRY_MARGIN_S before it is skipped.
    Writes the night's report (run_report.publish) and returns the process exit code (run_report.EXIT_CODES).
    sleep / now are injected by the tests.
    """
    import config
    import local_cache_db
    import main
    import run_report

    started = now()
    limit = started + max_hours * 3600
    worker_deadline = limit - FINISH_MARGIN_S
    attempts = []
    notes = []
    if worker_busy(main):
        say(f"a worker is already running ({LOCK_FILE}); nothing to do tonight")
        attempts.append({"stop_reason": "another_worker"})
    else:
        pin_nightly_settings(main, config)
        start_index_refresh()
        for number in range(1, len(RETRY_WAITS_S) + 2):
            try:
                attempt = run_once(main, local_cache_db, deadline_ts=worker_deadline)
            except KeyboardInterrupt:
                say("stop signal: the night stops")
                attempts.append({"stop_reason": _stop_reason()})
                break
            attempts.append(attempt)
            reason = attempt.get("stop_reason")
            if stop_signals.requested():
                say("stop signal: no retry tonight")
                break
            if not run_report.is_outage(reason) or number > len(RETRY_WAITS_S):
                break
            wait = RETRY_WAITS_S[number - 1]
            if now() + wait > limit - RETRY_MARGIN_S:
                say(f"attempt {number} stopped on an outage ({reason}); no retry: it would start less than "
                    f"{RETRY_MARGIN_S // 60} minutes before the task's {max_hours:g}-hour limit")
                notes.append(f"NO_RETRY: لم تُعد المحاولة بعد الانقطاع لأنها كانت ستبدأ قبل أقل من {RETRY_MARGIN_S // 60} "
                             f"دقيقة من حد جدولة المهام ({max_hours:g} ساعات)")
                break
            say(f"attempt {number} stopped on an outage ({reason}); the whole run starts again in {wait // 60} minutes")
            try:
                sleep(wait)
            except KeyboardInterrupt:
                say("stop signal while waiting for the retry: the night stops")
                attempts.append({"stop_reason": _stop_reason()})
                break
            if worker_busy(main):
                say("a worker started while we waited; it works the queue, nothing more to do tonight")
                attempts.append({"stop_reason": "another_worker"})
                break
    # the report is written whatever happens now: a stop signal is recorded, not raised (stop_signals.deferred)
    with stop_signals.deferred():
        worker_id = next((a.get("worker_id") for a in reversed(attempts) if a.get("worker_id")), None)
        ended = now()
        health = run_report.worker_health(worker_id, ended - started + 60)
        report = run_report.build_report("nightly", attempts, started, ended, health=health)
        report["notices"].extend(notes)
        run_report.publish(report)
    prune_storage(seconds_left=limit - now())
    return report["exit_code"]


def main(argv=None):
    os.chdir(REPO_ROOT)
    log_stream, path = open_log()
    saved = sys.stdout, sys.stderr
    # Before main.py is imported: config prints at import time and the scheduled task has no console to read.
    sys.stdout = sys.stderr = log_stream
    handler = logging.StreamHandler(log_stream)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.getLogger().addHandler(handler)
    import run_report
    run_report.install_log_redaction()       # no key or password reaches the night's log
    try:
        say(f"nightly run started in {REPO_ROOT} with {sys.executable}; log {path}")
        installed = stop_signals.install()
        if installed:
            say(f"stop signals handled: {', '.join(installed)} (a clean stop: report written, exit 3)")
        started = time.time()
        ping_healthcheck("start")
        try:
            hours = max_hours_from(argv)
            say(f"time limit: {hours:g} hours (NIGHTLY_MAX_HOURS / --max-hours); no new row after "
                f"{hours * 60 - FINISH_MARGIN_S // 60:g} minutes")
            code = run(max_hours=hours)
        except KeyboardInterrupt:
            say("stop signal: the nightly run stopped")
            code = 3
            with stop_signals.deferred():
                try:
                    import run_report
                    run_report.publish(run_report.build_report(
                        "nightly", [{"stop_reason": _stop_reason()}], started, time.time()))
                except Exception:
                    say("the night's report could not be written:\n" + traceback.format_exc())
        except Exception:
            say("nightly run failed:\n" + traceback.format_exc())
            code = 1
            try:
                import run_report
                run_report.publish(run_report.build_report(
                    "nightly", [{"stop_reason": "worker_error", "message": traceback.format_exc(limit=1)[-200:]}],
                    started, time.time()))
            except Exception:
                say("the night's report could not be written:\n" + traceback.format_exc())
        with stop_signals.deferred():
            ping_healthcheck("" if code in HEALTHY_EXIT_CODES else "fail")
        say(f"nightly run finished (exit {code})")
        return code
    finally:
        logging.getLogger().removeHandler(handler)
        sys.stdout, sys.stderr = saved
        log_stream.close()


if __name__ == "__main__":
    exit_code = main()
    worker_module = sys.modules.get("main")
    if worker_module is not None and hasattr(worker_module, "exit_process"):
        worker_module.exit_process(exit_code)       # never waits for a product thread left hung
    sys.exit(exit_code)
