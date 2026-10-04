"""Nightly run (scripts/run_nightly.py) and its Windows Task Scheduler script (scripts/schedule_nightly.ps1).

The runner is exercised offline with main.py's entry points replaced by recorders; the PowerShell script is
checked statically (and parsed when PowerShell is installed). The retry waits (15 and 60 minutes after an outage)
go through an injected sleeper, never a real sleep.
"""

import datetime
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "run_nightly.py"
SCHEDULER = ROOT / "scripts" / "schedule_nightly.ps1"
PWSH = shutil.which("pwsh") or shutil.which("powershell")


def _load_runner():
    spec = importlib.util.spec_from_file_location("run_nightly", RUNNER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def nightly(offline, monkeypatch, tmp_path):
    """The runner in a scratch working directory, with main's entry points and state reads recorded.

    rec["workers"]: what each worker call leaves in main.LAST_WORKER (consumed in order; an empty list leaves
    LAST_WORKER empty, and the runner then reads the automation state rec["state"]). runner.run() sleeps through
    rec["sleeps"] and keeps the night's report in rec["reports"].
    """
    import config
    import local_cache_db
    import main
    import run_report

    monkeypatch.chdir(tmp_path)
    runner = _load_runner()
    rec = {"calls": [], "state": {"status": "curation_pending", "notice": ""}, "workers": [], "sleeps": [],
           "reports": [], "prepared": True, "db": True, "telegram": []}
    # restored after the test: pin_nightly_settings replaces main.load_run_config
    monkeypatch.setattr(main, "load_run_config", main.load_run_config)
    for name in ("ROW_FILTER", "BRAND_FILTER", "FORCE_OVERWRITE_IMAGES", "AUTO_PUBLISH_ENABLED", "AUTO_PUBLISH_BRANDS",
                 "BG_REMOVAL_METHOD", "CURATION_MODE"):
        monkeypatch.setattr(config, name, getattr(config, name, None))
    monkeypatch.setattr(main, "_another_worker_running", lambda lock: False)
    monkeypatch.setattr(local_cache_db, "get_automation_state", lambda: dict(rec["state"]))
    monkeypatch.setattr(local_cache_db, "prepare_run", lambda: rec["prepared"])
    monkeypatch.setattr(local_cache_db, "db_available", lambda: rec["db"])
    monkeypatch.setattr(local_cache_db, "run_outcome_counts", lambda **kw: {
        "enqueued": 3, "searched": 3, "auto_published": 0, "ready_for_review": 2, "not_found": 1, "failed": 0,
        "provider_down": 0, "pending_left": 0})
    monkeypatch.setattr(local_cache_db, "save_run_history", lambda entry: rec.setdefault("history", []).append(entry) or 7)
    monkeypatch.setattr(run_report, "worker_health", lambda worker_id, since: None)
    publish = run_report.publish

    def recording_publish(report, **kw):
        rec["reports"].append(report)
        return publish(report, **kw)

    monkeypatch.setattr(run_report, "publish", recording_publish)
    real_run = runner.run
    monkeypatch.setattr(runner, "run", lambda **kw: real_run(**dict({"sleep": rec["sleeps"].append}, **kw)))

    def fake_enqueue():
        main.load_run_config()
        with open(runner.LOCK_FILE) as fh:
            lock = json.load(fh)
        rec["calls"].append(("enqueue", lock["pid"], lock["role"], config.ROW_FILTER, config.FORCE_OVERWRITE_IMAGES))

    def fake_worker(trigger="manual", report=True, deadline_ts=None):
        main.load_run_config()
        rec["calls"].append(("worker", config.AUTO_PUBLISH_ENABLED, trigger, report))
        rec.setdefault("deadlines", []).append(deadline_ts)
        if rec["workers"]:
            main.LAST_WORKER.update(rec["workers"].pop(0))
        os.remove(runner.LOCK_FILE)           # the real worker removes its lock in its finally block

    monkeypatch.setattr(main, "run_enqueue_mode", fake_enqueue)
    monkeypatch.setattr(main, "run_worker_mode", fake_worker)
    return runner, main, config, rec


def _owner_settings(monkeypatch, main, config):
    """The owner switched auto-publish on in the settings page and last ran rows 5-9 of one brand with reprocess."""
    os.makedirs("temp", exist_ok=True)
    with open(os.path.join("temp", "run_config.json"), "w", encoding="utf-8") as fh:
        json.dump({"autoPublish": True, "auto_publish_brands": "*", "row_filter": "5-9", "brand_filter": "Almarai",
                   "forceOverwrite": True, "bgRemovalMethod": "grabcut", "curation_mode": True}, fh)

    def load_db_config():
        config.AUTO_PUBLISH_ENABLED = True

    monkeypatch.setattr(config, "load_db_config", load_db_config)


def _last_report():
    with open(os.path.join("temp", "nightly", "last_report.json"), encoding="utf-8") as fh:
        return json.load(fh)


def test_pinned_settings_win_over_db_settings_and_the_last_run_config(nightly, monkeypatch):
    runner, main, config, _ = nightly
    _owner_settings(monkeypatch, main, config)
    from catalog_match import settings

    main.load_run_config()              # what the worker would run with, without the nightly pin
    assert config.AUTO_PUBLISH_ENABLED is True and config.ROW_FILTER == "5-9"

    runner.pin_nightly_settings(main, config)
    main.load_run_config()
    assert config.AUTO_PUBLISH_ENABLED is False and settings.auto_publish_enabled() is False
    assert config.ROW_FILTER == "" and config.BRAND_FILTER == "" and config.FORCE_OVERWRITE_IMAGES is False
    # the owner's other run preferences still apply
    assert config.BG_REMOVAL_METHOD == "grabcut" and config.CURATION_MODE is True


def test_run_enqueues_then_works_the_queue_with_auto_publish_off(nightly, monkeypatch):
    runner, main, config, rec = nightly
    _owner_settings(monkeypatch, main, config)

    assert runner.run() == 0
    # the lock is JSON with our PID during the enqueue; the worker runs for the nightly and leaves the report to us
    assert rec["calls"] == [("enqueue", os.getpid(), "nightly", "", False), ("worker", False, "nightly", False)]
    assert not os.path.exists(runner.LOCK_FILE)
    assert rec["sleeps"] == [] and len(rec["reports"]) == 1


@pytest.mark.parametrize("status, notice, code", [
    ("idle", "", 0),
    ("curation_pending", "", 0),
    ("provider_down", "PROVIDER_DOWN: search providers unavailable", 2),
    ("db_unavailable", "", 2),
    ("error", "SHEET_CONFIG: sheet not found: Products", 1),
])
def test_exit_code_follows_the_worker_state(nightly, status, notice, code):
    """A worker that left no LAST_WORKER (older main.py): the automation state decides. 'idle' from a database that
    did not answer used to count as a finished night (exit 0); get_automation_state now says 'db_unavailable'."""
    runner, _, _, rec = nightly
    rec["state"] = {"status": status, "notice": notice}
    assert runner.run() == code
    assert rec["reports"][-1]["exit_code"] == code


@pytest.mark.parametrize("stop_reason, outcome, code", [
    (None, "done", 0),
    ("stopped", "stopped", 3),
    ("BUDGET_REACHED", "stopped", 3),
    ("SERPER_CREDIT", "stopped", 3),
    ("SOME_NEW_REASON", "stopped", 3),         # a stop reason this runner does not know: shown as it is
    ("sheet_config", "failed", 1),
])
def test_exit_code_follows_the_worker_stop_reason(nightly, stop_reason, outcome, code):
    runner, _, _, rec = nightly
    rec["workers"] = [{"stop_reason": stop_reason, "run_id": "run-1", "worker_id": "host:1"}]
    assert runner.run() == code
    report = rec["reports"][-1]
    assert (report["outcome"], report["exit_code"], report["stop_reason"]) == (outcome, code, stop_reason)
    assert rec["sleeps"] == [], "only an outage is retried"
    if stop_reason == "BUDGET_REACHED":
        assert "DAILY_BUDGET_USD" in report["reason_text"] and report["reason_text"] != stop_reason
    if stop_reason == "SOME_NEW_REASON":
        assert report["reason_text"] == "SOME_NEW_REASON"
    assert _last_report()["exit_code"] == code


def test_an_outage_retries_the_whole_run_after_15_and_60_minutes(nightly):
    runner, _, _, rec = nightly
    rec["workers"] = [{"stop_reason": "db_unavailable", "run_id": "run-1"},
                      {"stop_reason": "provider_down", "run_id": "run-2"},
                      {"stop_reason": None, "run_id": "run-3"}]
    assert runner.run() == 0
    assert rec["sleeps"] == [15 * 60, 60 * 60]
    assert [c[0] for c in rec["calls"]] == ["enqueue", "worker"] * 3
    report = rec["reports"][-1]
    assert report["attempts"] == 3 and report["attempt_reasons"] == ["db_unavailable", "provider_down"]
    assert report["run_ids"] == ["run-1", "run-2", "run-3"] and report["outcome"] == "done"


def test_an_outage_that_outlasts_the_retries_is_reported_as_a_failure(nightly, monkeypatch):
    runner, main, _, rec = nightly
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:test")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    import config
    monkeypatch.setattr(config, "send_telegram_alert", lambda text: rec["telegram"].append(text) or True)
    rec["workers"] = [{"stop_reason": "provider_down", "run_id": f"run-{i}"} for i in range(3)]

    assert runner.run() == 2
    assert rec["sleeps"] == [15 * 60, 60 * 60] and len(rec["calls"]) == 6
    report = _last_report()
    assert report["outcome"] == "outage" and report["exit_code"] == 2 and report["attempts"] == 3
    (message,) = rec["telegram"]                      # one message for the night, not one per attempt
    assert "انقطاع" in message and "محركات البحث غير متاحة" in message and "المحاولات: 3" in message


def test_a_database_that_does_not_answer_is_never_a_finished_night(nightly):
    """The audit's case: with MariaDB down, get_automation_state() said 'idle' and the nightly exited 0."""
    runner, _, _, rec = nightly
    rec["prepared"], rec["db"] = False, False
    assert runner.run() == 2
    assert rec["calls"] == [], "no attempt reads the sheet while the database is down"
    assert rec["sleeps"] == [15 * 60, 60 * 60]
    report = _last_report()
    assert report["stop_reason"] == "db_unavailable" and report["outcome"] == "outage"
    assert report["counts"] is None and "قاعدة البيانات لا ترد" in report["reason_text"]


@pytest.mark.parametrize("reason, retried, code", [
    ("sheets_unavailable", True, 2),
    ("sheet_not_found", False, 1),        # review fix C3: not found / not shared does not fix itself by waiting
    ("sheet_config", False, 1),           # e.g. a missing credentials file
    ("db_unavailable", True, 2),
    ("enqueue_failed", False, 1),
])
def test_an_enqueue_failure_is_retried_only_for_an_outage(nightly, monkeypatch, reason, retried, code):
    runner, main, _, rec = nightly

    def broken_enqueue():
        main.LAST_ENQUEUE.update(reason=reason, message="تعذر الاتصال")
        raise SystemExit(1)

    monkeypatch.setattr(main, "run_enqueue_mode", broken_enqueue)
    assert runner.run() == code
    assert rec["sleeps"] == ([15 * 60, 60 * 60] if retried else [])
    assert not any(c[0] == "worker" for c in rec["calls"]) and not os.path.exists(runner.LOCK_FILE)
    assert rec["reports"][-1]["stop_reason"] == reason


def test_run_is_skipped_while_a_worker_holds_the_lock(nightly, monkeypatch):
    runner, main, _, rec = nightly
    os.makedirs("temp", exist_ok=True)
    with open(runner.LOCK_FILE, "w") as fh:
        fh.write("STARTING")                  # the dashboard is enqueueing right now
    assert runner.run() == 0
    assert rec["calls"] == [] and open(runner.LOCK_FILE).read() == "STARTING"
    assert rec["reports"][-1]["outcome"] == "skipped"

    with open(runner.LOCK_FILE, "w") as fh:
        fh.write("4242")
    monkeypatch.setattr(main, "_another_worker_running", lambda lock: True)
    assert runner.run() == 0
    assert rec["calls"] == [] and open(runner.LOCK_FILE).read() == "4242"


def test_a_worker_started_during_the_wait_ends_the_night(nightly, monkeypatch):
    runner, main, _, rec = nightly
    rec["workers"] = [{"stop_reason": "provider_down", "run_id": "run-1"}]

    def sleep(seconds):
        rec["sleeps"].append(seconds)
        with open(runner.LOCK_FILE, "w") as fh:                                   # the owner pressed run
            fh.write(json.dumps({"pid": 4242, "role": "worker"}))
        monkeypatch.setattr(main, "_another_worker_running", lambda lock: True)

    assert runner.run(sleep=sleep) == 0
    assert rec["sleeps"] == [15 * 60] and [c[0] for c in rec["calls"]] == ["enqueue", "worker"]
    report = rec["reports"][-1]
    # review fix C7: this used to be 'skipped' («لم يبدأ», no counts) although attempt 1 had worked the queue
    assert report["outcome"] == "handed_over" and report["exit_code"] == 0
    assert report["attempts"] == 2 and report["attempt_reasons"] == ["provider_down"]
    assert report["run_ids"] == ["run-1"] and report["counts"]["ready_for_review"] == 2
    assert "تشغيل آخر" in report["reason_text"] and _last_report()["outcome"] == "handed_over"


class Clock:
    """A fake clock: runner.run's now(); its sleep() records the wait and moves the clock on."""

    def __init__(self, rec, start=1_790_000_000.0):
        self.rec, self.t, self.start = rec, start, start

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.rec["sleeps"].append(seconds)
        self.t += seconds

    def hours(self):
        return (self.t - self.start) / 3600


def _timed_workers(main, runner, clock, plan):
    """run_worker_mode stand-in: each call works plan[i] = (hours, stop_reason) on the fake clock."""
    deadlines = []

    def worker(trigger="manual", report=True, deadline_ts=None):
        hours, reason = plan.pop(0)
        deadlines.append(deadline_ts)
        clock.t += hours * 3600
        main.LAST_WORKER.update(stop_reason=reason, run_id=f"run-{len(deadlines)}", worker_id="host:1")
        os.remove(runner.LOCK_FILE)

    return worker, deadlines


def test_the_night_stays_inside_task_schedulers_time_limit(nightly, monkeypatch):
    """Review fix C4: attempt 1 worked 6.5 h and stopped on provider_down; the runner slept 15 min and started attempt
    2, which Task Scheduler killed at its 8-hour ExecutionTimeLimit: no run_history row, no Telegram, last_report.json
    and the card showed the previous night, the lock and processing rows left behind. The worker now gets a deadline
    15 minutes before the limit, and a retry that cannot start 45 minutes before it is skipped."""
    runner, main, _, rec = nightly
    clock = Clock(rec)
    worker, deadlines = _timed_workers(main, runner, clock, [(6.5, "provider_down"), (0.25, "provider_down")])
    monkeypatch.setattr(main, "run_worker_mode", worker)

    assert runner.run(sleep=clock.sleep, now=clock.now, max_hours=8) == 2

    limit = clock.start + 8 * 3600
    # retry 1 starts at 6.75 h (before 7.25 h): it runs, with the same deadline (7.75 h) as attempt 1
    assert rec["sleeps"] == [15 * 60] and deadlines == [limit - 15 * 60] * 2
    # retry 2 would start at 8 h: skipped, and the night is reported at 7 h, well before the limit
    report = rec["reports"][-1]
    assert report["attempts"] == 2 and report["outcome"] == "outage" and report["exit_code"] == 2
    assert any(n.startswith("NO_RETRY: ") and "8 ساعات" in n for n in report["notices"])
    assert clock.hours() == pytest.approx(7.0) and _last_report()["attempts"] == 2


def test_a_retry_that_cannot_start_in_time_is_skipped(nightly, monkeypatch):
    runner, main, _, rec = nightly
    clock = Clock(rec)
    worker, deadlines = _timed_workers(main, runner, clock, [(7.5, "db_unavailable")])
    monkeypatch.setattr(main, "run_worker_mode", worker)
    assert runner.run(sleep=clock.sleep, now=clock.now, max_hours=8) == 2
    assert rec["sleeps"] == [] and len(deadlines) == 1
    # a longer limit leaves room for both retries
    clock = Clock(rec)
    rec["sleeps"] = []
    worker, deadlines = _timed_workers(main, runner, clock, [(7.5, "db_unavailable"), (1, "provider_down"), (1, None)])
    monkeypatch.setattr(main, "run_worker_mode", worker)
    assert runner.run(sleep=clock.sleep, now=clock.now, max_hours=12) == 0
    assert rec["sleeps"] == [15 * 60, 60 * 60] and deadlines == [clock.start + 12 * 3600 - 15 * 60] * 3


@pytest.mark.parametrize("argv, env, hours", [
    ([], {}, 8.0),
    (["--max-hours", "12"], {}, 12.0),
    (["--max-hours=3"], {"NIGHTLY_MAX_HOURS": "5"}, 3.0),
    ([], {"NIGHTLY_MAX_HOURS": "5"}, 5.0),
    (["--max-hours", "abc"], {}, 8.0),
    (["--max-hours", "0"], {}, 8.0),
    (["--max-hours", "40"], {"NIGHTLY_MAX_HOURS": "6"}, 6.0),
])
def test_the_time_limit_comes_from_the_scheduled_task(argv, env, hours):
    runner = _load_runner()
    assert runner.max_hours_from(argv, env) == hours


def test_stale_starting_lock_does_not_block_the_run(nightly):
    runner, _, _, rec = nightly
    os.makedirs("temp", exist_ok=True)
    with open(runner.LOCK_FILE, "w") as fh:
        fh.write("STARTING")
    old = time.time() - runner.STARTING_GRACE_S - 5
    os.utime(runner.LOCK_FILE, (old, old))
    assert runner.run() == 0
    assert [c[0] for c in rec["calls"]] == ["enqueue", "worker"]


def test_failed_enqueue_does_not_start_the_worker(nightly, monkeypatch):
    runner, main, _, rec = nightly

    def broken_enqueue():
        raise SystemExit(1)                   # e.g. the row filter is invalid

    monkeypatch.setattr(main, "run_enqueue_mode", broken_enqueue)
    assert runner.run() == 1
    assert rec["calls"] == [] and not os.path.exists(runner.LOCK_FILE)


def test_empty_sheet_still_works_the_leftover_queue(nightly, monkeypatch):
    """run_enqueue_mode exits 0 when the sheet has no product; like the dashboard, the worker still runs."""
    runner, main, _, rec = nightly

    def no_products():
        raise SystemExit(0)

    monkeypatch.setattr(main, "run_enqueue_mode", no_products)
    assert runner.run() == 0
    assert rec["calls"] == [("worker", False, "nightly", False)]


def test_the_night_report_lands_in_run_history_and_last_report(nightly):
    runner, _, _, rec = nightly
    rec["workers"] = [{"stop_reason": None, "run_id": "run-9", "worker_id": "host:1",
                       "notice": "GEMINI_DOWN: Gemini لا يستجيب"}]
    assert runner.run() == 0
    (entry,) = rec["history"]
    assert entry["run_trigger"] == "nightly" and entry["run_id"] == "run-9" and entry["outcome"] == "done"
    assert (entry["enqueued"], entry["ready_for_review"], entry["not_found"]) == (3, 2, 1)
    assert entry["notices"] == "GEMINI_DOWN: Gemini لا يستجيب"
    report = _last_report()
    assert report["history_id"] == 7 and report["counts"]["ready_for_review"] == 2
    assert report["telegram_sent"] is False             # Telegram is not configured in the tests


def test_main_logs_to_temp_nightly_and_restores_the_console(nightly, monkeypatch, tmp_path):
    runner, _, _, _ = nightly
    monkeypatch.setattr(runner, "REPO_ROOT", str(tmp_path))

    def fake_run(**kwargs):
        print("worker output line")
        assert kwargs == {"max_hours": 8.0}
        return 0

    monkeypatch.setattr(runner, "run", fake_run)
    stdout, stderr = sys.stdout, sys.stderr
    assert runner.main() == 0
    assert sys.stdout is stdout and sys.stderr is stderr
    log = tmp_path / "temp" / "nightly" / f"nightly_{datetime.date.today().isoformat()}.log"
    text = log.read_text(encoding="utf-8")
    assert "nightly run started" in text and "worker output line" in text and "(exit 0)" in text


def test_old_logs_are_pruned(tmp_path):
    runner = _load_runner()
    log_dir = tmp_path / "nightly"
    log_dir.mkdir()
    first = datetime.date(2026, 8, 1)
    for i in range(34):                                  # 2026-08-01 .. 2026-09-03
        (log_dir / f"nightly_{first + datetime.timedelta(days=i)}.log").write_text("x", encoding="utf-8")
    (log_dir / "unrelated.log").write_text("x", encoding="utf-8")
    stream, path = runner.open_log(str(log_dir), today=datetime.date(2026, 9, 30), keep=30)
    stream.close()
    names = sorted(p.name for p in log_dir.glob("nightly_*.log"))
    assert len(names) == 30 and names[-1] == "nightly_2026-09-30.log" == Path(path).name
    assert "nightly_2026-08-05.log" not in names and "nightly_2026-08-06.log" in names
    assert (log_dir / "unrelated.log").exists()


# ---------------------------------------------------------------------------
# Static checks
# ---------------------------------------------------------------------------

def test_runner_reuses_main_entry_points_and_writes_no_sheet():
    text = RUNNER.read_text(encoding="utf-8")
    assert "main_module.run_enqueue_mode()" in text
    assert 'main_module.run_worker_mode(trigger="nightly", report=False, deadline_ts=deadline_ts)' in text
    assert '"AUTO_PUBLISH_ENABLED": False' in text and '"FORCE_OVERWRITE_IMAGES": False' in text
    for forbidden in ("google_sheets", "update_image_link", "update_cell", "subprocess", "AUTO_PUBLISH_ENABLED\": True"):
        assert forbidden not in text, forbidden
    assert 'os.path.join("temp", "nightly")' in text


def _scheduler_text():
    return SCHEDULER.read_bytes().decode("utf-8-sig")


def test_scheduler_script_is_utf8_with_bom():
    """Windows PowerShell 5.1 reads a BOM-less script as ANSI: the Arabic 'ل' (UTF-8 D9 84) turns into '„',
    which PowerShell treats as a quote, and the script no longer parses."""
    assert SCHEDULER.read_bytes().startswith(b"\xef\xbb\xbf")


def test_scheduler_uses_the_venv_python_from_the_repository_folder():
    text = _scheduler_text()
    assert '$repoRoot = Split-Path -Parent $PSScriptRoot' in text
    assert 'Join-Path $repoRoot ".venv\\Scripts\\python.exe"' in text
    assert 'Join-Path $repoRoot "scripts\\run_nightly.py"' in text
    # both paths quoted for a repository folder with spaces; 'Start in' must stay unquoted
    assert '-Execute "`"$pythonPath`""' in text
    # Task Scheduler's limit reaches the runner, which stays inside it (review fix C4)
    assert '-Argument "-X utf8 `"$runnerPath`" --max-hours $MaxHours"' in text
    assert "-ExecutionTimeLimit (New-TimeSpan -Hours $MaxHours)" in text
    assert "-WorkingDirectory $repoRoot" in text
    assert "Register-ScheduledTask" in text and "Unregister-ScheduledTask" in text
    assert "New-ScheduledTaskTrigger -Daily -At $at" in text
    assert re.search(r'\[string\]\$Time = "\d\d:\d\d"', text)
    assert "[switch]$Unregister" in text
    assert "-MultipleInstances IgnoreNew" in text


def test_scheduler_has_no_secrets_and_never_touches_auto_publish():
    text = _scheduler_text()
    assert not re.search(r"AIza[0-9A-Za-z_\-]{20,}", text)
    assert not re.search(r"\b[0-9a-f]{32,}\b", text)
    for forbidden in ("-Password", "Get-Credential", "ConvertTo-SecureString", "API_KEY", "AUTO_PUBLISH", "autoPublish",
                      ".env\"", "Set-Content", "Out-File"):
        assert forbidden not in text, forbidden


@pytest.mark.skipif(PWSH is None, reason="PowerShell is not installed")
def test_scheduler_script_parses():
    command = ("$errors = $null; [System.Management.Automation.Language.Parser]::ParseFile("
               f"'{SCHEDULER}', [ref]$null, [ref]$errors) | Out-Null; $errors.Count")
    result = subprocess.run([PWSH, "-NoProfile", "-Command", command], capture_output=True, text=True, timeout=120)
    assert result.returncode == 0 and result.stdout.strip() == "0", result.stdout + result.stderr
