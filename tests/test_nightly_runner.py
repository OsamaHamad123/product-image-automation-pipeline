"""Nightly run (scripts/run_nightly.py) and its Windows Task Scheduler script (scripts/schedule_nightly.ps1).

The runner is exercised offline with main.py's entry points replaced by recorders; the PowerShell script is
checked statically (and parsed when PowerShell is installed).
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
    """The runner in a scratch working directory, with main's entry points and state reads recorded."""
    import config
    import local_cache_db
    import main

    monkeypatch.chdir(tmp_path)
    runner = _load_runner()
    rec = {"calls": [], "state": {"status": "curation_pending", "notice": ""}}
    # restored after the test: pin_nightly_settings replaces main.load_run_config
    monkeypatch.setattr(main, "load_run_config", main.load_run_config)
    for name in ("ROW_FILTER", "BRAND_FILTER", "FORCE_OVERWRITE_IMAGES", "AUTO_PUBLISH_ENABLED", "AUTO_PUBLISH_BRANDS",
                 "BG_REMOVAL_METHOD", "CURATION_MODE"):
        monkeypatch.setattr(config, name, getattr(config, name, None))
    monkeypatch.setattr(main, "_another_worker_running", lambda lock: False)
    monkeypatch.setattr(local_cache_db, "get_automation_state", lambda: dict(rec["state"]))

    def fake_enqueue():
        main.load_run_config()
        with open(runner.LOCK_FILE) as fh:
            rec["calls"].append(("enqueue", fh.read().strip(), config.ROW_FILTER, config.FORCE_OVERWRITE_IMAGES))

    def fake_worker():
        main.load_run_config()
        rec["calls"].append(("worker", config.AUTO_PUBLISH_ENABLED))
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
    assert rec["calls"] == [("enqueue", str(os.getpid()), "", False), ("worker", False)]
    assert not os.path.exists(runner.LOCK_FILE)


@pytest.mark.parametrize("status, code", [("idle", 0), ("curation_pending", 0), ("provider_down", 1), ("error", 1)])
def test_exit_code_follows_the_worker_state(nightly, status, code):
    runner, _, _, rec = nightly
    rec["state"] = {"status": status, "notice": "PROVIDER_DOWN: search providers unavailable"}
    assert runner.run() == code


def test_run_is_skipped_while_a_worker_holds_the_lock(nightly, monkeypatch):
    runner, main, _, rec = nightly
    os.makedirs("temp", exist_ok=True)
    with open(runner.LOCK_FILE, "w") as fh:
        fh.write("STARTING")                  # the dashboard is enqueueing right now
    assert runner.run() == 0
    assert rec["calls"] == [] and open(runner.LOCK_FILE).read() == "STARTING"

    with open(runner.LOCK_FILE, "w") as fh:
        fh.write("4242")
    monkeypatch.setattr(main, "_another_worker_running", lambda lock: True)
    assert runner.run() == 0
    assert rec["calls"] == [] and open(runner.LOCK_FILE).read() == "4242"


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
        raise SystemExit(1)                   # e.g. the sheet could not be opened

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
    assert rec["calls"] == [("worker", False)]


def test_main_logs_to_temp_nightly_and_restores_the_console(nightly, monkeypatch, tmp_path):
    runner, _, _, _ = nightly
    monkeypatch.setattr(runner, "REPO_ROOT", str(tmp_path))

    def fake_run():
        print("worker output line")
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
    assert "main.run_enqueue_mode()" in text and "main.run_worker_mode()" in text
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
    assert '-Argument "-X utf8 `"$runnerPath`""' in text
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
