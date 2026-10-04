"""The worker lock (temp/pipeline.lock) never skips a night because of a stale lock (package P4b, item 1), and never
lets two workers run at once (review fixes C1, C2, P1, P2, P4, P5).

Audit (lock_pid_reuse.py): main._another_worker_running accepted ANY live process with the lock's PID. After a crash
or a kill the PID is reused by another program, and the nightly logged "a worker is already running; nothing to do
tonight" and exited 0. The lock is JSON {pid, host, started_at, started_ts, heartbeat_ts, proc_created, role, trigger,
cmd} (the old plain-PID format is still read) and counts only for a Python process running main.py / run_nightly.py on
this host whose start time is the lock writer's; anything else is removed with the reason in the log.

Review fixes: a live worker was judged stale after 24 hours (the lock was written once and aged before any process
check), and a failed process check (PowerShell / tasklist timing out) deleted a live lock. The worker now refreshes
heartbeat_ts from its loop (also while paused or waiting for the database); a lock whose process identity verifies
(command line and start time) is never aged out; a failed check is retried once and keeps the lock (only a positive
"gone / another process" verdict frees it); a lock whose identity cannot be verified ages out by its heartbeat
(LOCK_STALE_HEARTBEAT_SECONDS). The lock is written atomically and a new one is created exclusively.
The dashboard (ApiController::pipelineProcess) asks Python for the verdict (cli_bridge lock_state) instead of
re-implementing the rule (review fix C9); it is checked under the PHP CLI.

Real processes are used where the platform allows it (/proc on Linux); the rest goes through an injected
process_info, and the Windows probe through a fake subprocess.run.
"""

import importlib.util
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CONTROLLER = ROOT / "dashboard" / "app" / "Http" / "Controllers" / "ApiController.php"
PHP = shutil.which("php")
LINUX_PROC = os.path.isdir("/proc") and sys.platform.startswith("linux")


@pytest.fixture
def main_mod(offline, monkeypatch, tmp_path):
    import main
    monkeypatch.chdir(tmp_path)
    os.makedirs("temp", exist_ok=True)
    return main


@pytest.fixture
def lock_log(main_mod, monkeypatch):
    """What main.py logs. main's print is config.log_runner, which cli_bridge (imported by other tests) turns into a
    logger call: record main.print itself instead of reading stdout."""
    lines = []
    monkeypatch.setattr(main_mod, "print", lambda *args: lines.append(" ".join(str(a) for a in args)))
    return lines


@pytest.fixture
def fake_worker_script(tmp_path):
    """A python process whose command line is `python .../main.py` (stands in for a live worker)."""
    folder = tmp_path / "worker_bin"
    folder.mkdir()
    script = folder / "main.py"
    script.write_text("import time\ntime.sleep(60)\n", encoding="utf-8")
    procs = []

    def start(name="main.py"):
        path = folder / name
        if not path.exists():
            path.write_text("import time\ntime.sleep(60)\n", encoding="utf-8")
        proc = subprocess.Popen([sys.executable, str(path)])
        procs.append(proc)
        time.sleep(0.2)                 # /proc/<pid>/cmdline is filled once exec is done
        return proc

    yield start
    for proc in procs:
        proc.kill()
        proc.wait()


def _write(path, content):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)


def _json_lock(pid, **extra):
    data = {"pid": pid, "host": socket.gethostname(), "started_at": "2026-10-03T02:00:00",
            "started_ts": time.time(), "role": "worker", "cmd": "main.py --worker"}
    data.update(extra)
    return json.dumps(data)


# ---------------------------------------------------------------------------
# Real processes (Linux /proc)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not LINUX_PROC, reason="needs Linux /proc")
def test_a_reused_pid_of_another_program_does_not_skip_the_night(main_mod, lock_log):
    """The audit's reproduction: the lock holds the PID of `sleep` (not a pipeline worker)."""
    other = subprocess.Popen(["sleep", "30"])
    try:
        for content in (str(other.pid), _json_lock(other.pid)):
            _write(main_mod.LOCK_FILE, content)
            assert main_mod._another_worker_running(main_mod.LOCK_FILE) is False
            assert not os.path.exists(main_mod.LOCK_FILE), "the stale lock is removed"
        assert "ليست بايثون" in "\n".join(lock_log)
    finally:
        other.kill()
        other.wait()


@pytest.mark.skipif(not LINUX_PROC, reason="needs Linux /proc")
def test_a_python_process_that_is_not_the_worker_does_not_hold_the_lock(main_mod, fake_worker_script, lock_log):
    proc = fake_worker_script("other_tool.py")
    _write(main_mod.LOCK_FILE, _json_lock(proc.pid))
    assert main_mod._another_worker_running(main_mod.LOCK_FILE) is False
    assert not os.path.exists(main_mod.LOCK_FILE)
    assert "ليست عامل الأتمتة" in "\n".join(lock_log)


@pytest.mark.skipif(not LINUX_PROC, reason="needs Linux /proc")
def test_a_genuine_live_worker_holds_the_lock_in_both_formats(main_mod, fake_worker_script):
    proc = fake_worker_script("main.py")
    for content in (_json_lock(proc.pid), str(proc.pid)):          # the new JSON lock and the old plain PID
        _write(main_mod.LOCK_FILE, content)
        assert main_mod._another_worker_running(main_mod.LOCK_FILE) is True
        assert open(main_mod.LOCK_FILE, encoding="utf-8").read() == content, "a live worker's lock is kept"


@pytest.mark.skipif(not LINUX_PROC, reason="needs Linux /proc")
def test_a_worker_that_started_after_the_lock_was_written_is_a_reused_pid(main_mod, fake_worker_script, lock_log):
    proc = fake_worker_script("run_nightly.py")
    _write(main_mod.LOCK_FILE, _json_lock(proc.pid, started_ts=time.time() - 3600))
    assert main_mod._another_worker_running(main_mod.LOCK_FILE) is False
    assert "بدأت بعد كتابة القفل" in "\n".join(lock_log)


@pytest.mark.skipif(not LINUX_PROC, reason="needs Linux /proc")
def test_the_nightly_runs_over_a_stale_lock(main_mod):
    """run_nightly.worker_busy with the real check: the probe's `sleep` lock no longer means "nothing to do"."""
    spec = importlib.util.spec_from_file_location("run_nightly", ROOT / "scripts" / "run_nightly.py")
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    other = subprocess.Popen(["sleep", "30"])
    try:
        _write(runner.LOCK_FILE, str(other.pid))
        assert runner.worker_busy(main_mod) is False
    finally:
        other.kill()
        other.wait()


# ---------------------------------------------------------------------------
# Rules (injected process_info)
# ---------------------------------------------------------------------------

def _info(alive=True, name="python.exe", cmdline='"C:\\repo\\.venv\\Scripts\\python.exe" -u "C:\\repo\\main.py" --worker',
          created=None):
    return lambda pid: {"alive": alive, "name": name, "cmdline": cmdline, "created": created}


def test_a_dead_pid_is_stale(main_mod):
    _write(main_mod.LOCK_FILE, _json_lock(4242))
    assert main_mod._another_worker_running(main_mod.LOCK_FILE, process_info=_info(alive=False)) is False
    assert not os.path.exists(main_mod.LOCK_FILE)


def test_a_lock_from_another_computer_is_stale_without_looking_at_this_computers_processes(main_mod, lock_log):
    _write(main_mod.LOCK_FILE, _json_lock(4242, host="OTHER-PC"))

    def never(pid):
        raise AssertionError("a PID from another computer means nothing here")

    assert main_mod._another_worker_running(main_mod.LOCK_FILE, process_info=never) is False
    assert not os.path.exists(main_mod.LOCK_FILE)
    assert "OTHER-PC" in "\n".join(lock_log)


def test_an_unverified_lock_ages_out_by_its_heartbeat_only(main_mod):
    """Only the image name is known (tasklist fallback): the lock is live while the worker beats, stale once its
    heartbeat is older than LOCK_STALE_HEARTBEAT_SECONDS (it used to age by its start time, 24 h)."""
    name_only = _info(cmdline=None)
    fresh = time.time() - 60
    old = time.time() - main_mod.LOCK_STALE_HEARTBEAT_SECONDS - 60
    _write(main_mod.LOCK_FILE, _json_lock(4242, started_ts=time.time() - 3 * 86400, heartbeat_ts=fresh))
    assert main_mod._another_worker_running(main_mod.LOCK_FILE, process_info=name_only) is True
    _write(main_mod.LOCK_FILE, _json_lock(4242, started_ts=old, heartbeat_ts=old))
    assert main_mod._another_worker_running(main_mod.LOCK_FILE, process_info=name_only) is False
    # the old plain-PID format beats by the file's modification time
    _write(main_mod.LOCK_FILE, "4242")
    os.utime(main_mod.LOCK_FILE, (old, old))
    assert main_mod._another_worker_running(main_mod.LOCK_FILE, process_info=name_only) is False


def test_a_lock_whose_process_identity_verifies_is_never_aged_out(main_mod):
    """Review fix C1: a paused worker, or a dashboard run over a large sheet or across a laptop sleep, outlived the
    24-hour limit; the nightly then deleted its lock and started a second worker. Command line and start time verify."""
    created = time.time() - 3 * 86400
    verified = _info(created=created)
    two_days = time.time() - 2 * 86400
    _write(main_mod.LOCK_FILE, _json_lock(4242, started_ts=two_days, heartbeat_ts=two_days, proc_created=created))
    assert main_mod._another_worker_running(main_mod.LOCK_FILE, process_info=verified) is True
    assert os.path.exists(main_mod.LOCK_FILE)
    verdict = main_mod.lock_verdict(main_mod.read_lock(main_mod.LOCK_FILE), process_info=verified)
    assert verdict["verified"] is True and verdict["stale"] is None


@pytest.mark.skipif(not LINUX_PROC, reason="needs Linux /proc")
def test_a_real_worker_running_for_days_keeps_its_lock(main_mod, fake_worker_script):
    proc = fake_worker_script("main.py")
    created = main_mod._proc_process_info(proc.pid)["created"]
    two_days = time.time() - 2 * 86400
    _write(main_mod.LOCK_FILE, _json_lock(proc.pid, started_ts=two_days, heartbeat_ts=two_days, proc_created=created))
    assert main_mod._another_worker_running(main_mod.LOCK_FILE) is True
    # the old plain-PID lock whose file is older than the process: the process started after it (reused PID)
    _write(main_mod.LOCK_FILE, str(proc.pid))
    os.utime(main_mod.LOCK_FILE, (two_days, two_days))
    assert main_mod._another_worker_running(main_mod.LOCK_FILE) is False


def test_a_failed_process_check_keeps_the_lock_and_is_retried_once(main_mod, lock_log):
    """Review fix C2: PowerShell / tasklist timing out became a "stale" reason, the live lock was deleted and a
    second worker started. A failed check is retried once, logged, and keeps the lock (fail closed)."""
    calls = []

    def broken(pid):
        calls.append(pid)
        raise subprocess.TimeoutExpired("powershell", 30)

    _write(main_mod.LOCK_FILE, _json_lock(4242))
    assert main_mod._another_worker_running(main_mod.LOCK_FILE, process_info=broken) is True
    assert calls == [4242, 4242] and os.path.exists(main_mod.LOCK_FILE)
    assert "تعذر فحص العملية 4242 مرتين" in "\n".join(lock_log)

    flaky = iter([RuntimeError("tasklist failed"), {"alive": False, "name": None, "cmdline": None, "created": None}])

    def once_then_gone(pid):
        item = next(flaky)
        if isinstance(item, Exception):
            raise item
        return item

    assert main_mod._another_worker_running(main_mod.LOCK_FILE, process_info=once_then_gone) is False
    assert not os.path.exists(main_mod.LOCK_FILE), "the retry's positive verdict (gone) frees the lock"

    # a dead worker whose check keeps failing is still freed once its heartbeat is old
    old = time.time() - main_mod.LOCK_STALE_HEARTBEAT_SECONDS - 60
    _write(main_mod.LOCK_FILE, _json_lock(4242, started_ts=old, heartbeat_ts=old))
    assert main_mod._another_worker_running(main_mod.LOCK_FILE, process_info=broken) is False
    assert not os.path.exists(main_mod.LOCK_FILE)


def test_the_start_time_is_compared_like_with_like(main_mod):
    """Review fix P2: the lock keeps the writer's own start time as the probe reads it (proc_created). A probe that
    reads every start time an hour off (WMI's local-time conversion across a DST switch) no longer turns the live
    worker into a "reused PID"; a process that really started at another time is still one."""
    started = time.time() - 120
    shifted = started - 2 + 3600                       # the probe's reading of the writer's start, an hour off
    _write(main_mod.LOCK_FILE, _json_lock(4242, started_ts=started, proc_created=shifted))
    assert main_mod._another_worker_running(main_mod.LOCK_FILE, process_info=_info(created=shifted + 1)) is True
    assert main_mod._another_worker_running(main_mod.LOCK_FILE, process_info=_info(created=shifted + 600)) is False


def test_windows_command_lines_and_the_image_name_fallback(main_mod):
    _write(main_mod.LOCK_FILE, _json_lock(4242))
    assert main_mod._another_worker_running(main_mod.LOCK_FILE, process_info=_info()) is True
    nightly = '"C:\\My Repo\\.venv\\Scripts\\python.exe" -X utf8 "C:\\My Repo\\scripts\\run_nightly.py"'
    assert main_mod._another_worker_running(main_mod.LOCK_FILE, process_info=_info(cmdline=nightly)) is True
    # only the image name is known (tasklist fallback): a python process counts, as before
    assert main_mod._another_worker_running(main_mod.LOCK_FILE, process_info=_info(cmdline=None)) is True
    assert main_mod._another_worker_running(main_mod.LOCK_FILE, process_info=_info(name="chrome.exe", cmdline=None)) is False
    assert not os.path.exists(main_mod.LOCK_FILE)


def test_a_script_named_like_main_py_inside_another_name_does_not_count(main_mod):
    _write(main_mod.LOCK_FILE, _json_lock(4242))
    assert main_mod._another_worker_running(
        main_mod.LOCK_FILE, process_info=_info(cmdline="python /srv/app/domain.py --serve")) is False


def test_our_own_lock_the_dashboards_starting_lock_and_garbage(main_mod, lock_log):
    _write(main_mod.LOCK_FILE, _json_lock(os.getpid(), role="nightly"))     # the nightly holds it during the enqueue
    assert main_mod._another_worker_running(main_mod.LOCK_FILE, process_info=_info(alive=False)) is False
    assert os.path.exists(main_mod.LOCK_FILE)
    _write(main_mod.LOCK_FILE, "STARTING")                                  # the worker takes it over from the enqueue
    assert main_mod._another_worker_running(main_mod.LOCK_FILE) is False
    assert open(main_mod.LOCK_FILE).read() == "STARTING"
    _write(main_mod.LOCK_FILE, "not a lock")
    assert main_mod._another_worker_running(main_mod.LOCK_FILE) is False
    assert not os.path.exists(main_mod.LOCK_FILE)
    assert "محتوى غير مفهوم" in "\n".join(lock_log)


def test_write_read_and_release_only_our_own_lock(main_mod):
    main_mod.write_lock("worker", main_mod.LOCK_FILE, trigger="dashboard")
    lock = main_mod.read_lock(main_mod.LOCK_FILE)
    assert lock["kind"] == "json" and lock["pid"] == os.getpid() and lock["role"] == "worker"
    assert lock["trigger"] == "dashboard"
    assert lock["host"] == socket.gethostname() and abs(lock["started_ts"] - time.time()) < 5
    assert lock["heartbeat_ts"] == lock["started_ts"]
    raw = json.loads(open(main_mod.LOCK_FILE, encoding="utf-8").read())
    assert set(raw) >= {"pid", "host", "started_at", "started_ts", "heartbeat_ts", "proc_created", "role", "cmd"}
    if LINUX_PROC:
        assert abs(raw["proc_created"] - main_mod._proc_process_info(os.getpid())["created"]) < 1
    main_mod.release_own_lock(main_mod.LOCK_FILE)
    assert not os.path.exists(main_mod.LOCK_FILE)
    # another worker's lock (it started after ours was judged stale) is left alone
    _write(main_mod.LOCK_FILE, _json_lock(4242))
    main_mod.release_own_lock(main_mod.LOCK_FILE)
    assert os.path.exists(main_mod.LOCK_FILE)


def test_the_lock_is_replaced_atomically_and_created_exclusively(main_mod, monkeypatch):
    """Review fix P5: the lock was truncated and then written (a reader could see it empty and delete it), and two
    workers that both saw no lock both wrote one."""
    replaced = []
    real_replace = os.replace
    monkeypatch.setattr(main_mod.os, "replace", lambda src, dst: replaced.append(dst) or real_replace(src, dst))
    main_mod.write_lock("worker", main_mod.LOCK_FILE)
    assert replaced == [main_mod.LOCK_FILE]
    assert [p for p in os.listdir("temp") if p.endswith(".tmp")] == []

    os.remove(main_mod.LOCK_FILE)
    assert main_mod.acquire_lock("worker", main_mod.LOCK_FILE) is True          # created: it did not exist
    _write(main_mod.LOCK_FILE, _json_lock(4242))
    assert main_mod.acquire_lock("worker", main_mod.LOCK_FILE) is False         # another process got there first
    assert json.loads(open(main_mod.LOCK_FILE, encoding="utf-8").read())["pid"] == 4242
    _write(main_mod.LOCK_FILE, "STARTING")                                       # the dashboard's enqueue
    assert main_mod.acquire_lock("worker", main_mod.LOCK_FILE, trigger="dashboard") is True
    assert main_mod.read_lock(main_mod.LOCK_FILE)["pid"] == os.getpid()
    assert main_mod.acquire_lock("nightly", main_mod.LOCK_FILE) is True          # our own lock (the nightly's enqueue)

    # the exclusive create loses a race it did not see: the lock appeared between the read and the create
    os.remove(main_mod.LOCK_FILE)
    real_read = main_mod.read_lock

    def racing_read(path):
        lock = real_read(path)
        _write(path, _json_lock(4343))
        return lock

    monkeypatch.setattr(main_mod, "read_lock", racing_read)
    assert main_mod.acquire_lock("worker", main_mod.LOCK_FILE) is False
    assert json.loads(open(main_mod.LOCK_FILE, encoding="utf-8").read())["pid"] == 4343
    assert [p for p in os.listdir("temp") if p.endswith(".tmp")] == []



def test_a_lock_rewrite_waits_for_a_reader_to_close_the_file(main_mod, monkeypatch):
    """On Windows os.replace fails while another process (the dashboard polls the lock every few seconds) has the lock
    open: the takeover of the dashboard's STARTING lock or a heartbeat is retried instead of being lost."""
    real_replace = os.replace
    busy = [2]

    def replace(src, dst):
        if busy[0]:
            busy[0] -= 1
            raise PermissionError(13, "The process cannot access the file because it is being used by another process")
        return real_replace(src, dst)

    monkeypatch.setattr(main_mod.os, "replace", replace)
    monkeypatch.setattr(main_mod.time, "sleep", lambda s: None)
    _write(main_mod.LOCK_FILE, "STARTING")
    assert main_mod.acquire_lock("worker", main_mod.LOCK_FILE) is True
    assert main_mod.read_lock(main_mod.LOCK_FILE)["pid"] == os.getpid()
    busy[0] = 99
    with pytest.raises(PermissionError):
        main_mod.write_lock("worker", main_mod.LOCK_FILE)
    assert [p for p in os.listdir("temp") if p.endswith(".tmp")] == []

def test_an_empty_lock_being_created_is_not_deleted(main_mod):
    """The exclusive-create fallback (no hard links) writes after creating: a reader may see it empty for a moment."""
    _write(main_mod.LOCK_FILE, "")
    assert main_mod._another_worker_running(main_mod.LOCK_FILE) is True and os.path.exists(main_mod.LOCK_FILE)
    old = time.time() - 60
    os.utime(main_mod.LOCK_FILE, (old, old))
    assert main_mod._another_worker_running(main_mod.LOCK_FILE) is False and not os.path.exists(main_mod.LOCK_FILE)


def test_refresh_beats_only_our_own_lock(main_mod):
    main_mod.write_lock("worker", main_mod.LOCK_FILE)
    before = main_mod.read_lock(main_mod.LOCK_FILE)
    assert main_mod.refresh_lock(main_mod.LOCK_FILE, now=before["started_ts"] + 90, worker_id="host:1") is True
    after = main_mod.read_lock(main_mod.LOCK_FILE)
    assert after["heartbeat_ts"] == pytest.approx(before["started_ts"] + 90) and after["worker_id"] == "host:1"
    assert after["started_ts"] == before["started_ts"]
    _write(main_mod.LOCK_FILE, _json_lock(4242))
    assert main_mod.refresh_lock(main_mod.LOCK_FILE) is False                  # never over another process's lock
    assert json.loads(open(main_mod.LOCK_FILE, encoding="utf-8").read())["pid"] == 4242
    os.remove(main_mod.LOCK_FILE)
    assert main_mod.refresh_lock(main_mod.LOCK_FILE) is False and not os.path.exists(main_mod.LOCK_FILE)


def _worker_env(main_mod, monkeypatch, states):
    """run_worker_mode with the sheet, the queue and the search replaced; get_automation_state serves `states`
    (the last one repeats) and records the lock's heartbeat at each call."""
    import google_sheets
    import local_cache_db

    real_sleep = time.sleep
    monkeypatch.setattr(main_mod.time, "sleep", lambda s: real_sleep(0.01))
    monkeypatch.setattr(main_mod, "load_run_config", lambda: None)
    monkeypatch.setattr(main_mod, "check_verifier", lambda: "")
    monkeypatch.setattr(local_cache_db, "resume_automation", lambda: True)
    monkeypatch.setattr(local_cache_db, "update_automation_state", lambda *a, **k: True)
    monkeypatch.setattr(local_cache_db, "stop_run", lambda worker_active=False: None)
    monkeypatch.setattr(local_cache_db, "get_ready_for_review_count", lambda: 0)
    monkeypatch.setattr(local_cache_db, "get_run_statistics", lambda run_id: {"total": 0, "completed": 0, "failed": 0,
                                                                              "ready_for_review": 0})
    monkeypatch.setattr(local_cache_db, "fetch_next_task", lambda worker_id: None)
    monkeypatch.setattr(local_cache_db, "count_open_tasks", lambda: 1)
    monkeypatch.setattr(local_cache_db, "park_verifier_rechecks", lambda: 0)
    monkeypatch.setattr(local_cache_db, "requeue_verifier_down", lambda run_id: 0)
    monkeypatch.setattr(google_sheets, "get_sheets_client", lambda: object())
    monkeypatch.setattr(google_sheets, "open_worksheet", lambda client, name: object())
    monkeypatch.setattr(google_sheets, "find_link_column", lambda ws: 7)
    monkeypatch.setattr(google_sheets, "get_brand_mappings", lambda *a: {})
    monkeypatch.setattr(google_sheets, "init_async_queue", lambda *a: None)
    monkeypatch.setattr(google_sheets, "stop_async_queue", lambda: None)
    monkeypatch.setattr(main_mod, "LOCK_HEARTBEAT_SECONDS", 0)
    beats = []

    def state():
        lock = main_mod.read_lock(main_mod.LOCK_FILE)
        beats.append(lock["heartbeat_ts"] if lock else None)
        item = states.pop(0) if len(states) > 1 else states[0]
        if isinstance(item, Exception):
            raise item
        return dict(item)

    monkeypatch.setattr(local_cache_db, "get_automation_state", state)
    return beats


def test_the_worker_beats_while_paused_and_while_the_database_is_away(main_mod, monkeypatch):
    """Review fix C1: the lock was written once and never refreshed. The worker's loop beats every
    LOCK_HEARTBEAT_SECONDS, also while paused and while it waits for the database."""
    paused = {"stop_requested": 0, "pause_requested": 1, "run_id": "r1"}
    states = [{"stop_requested": 0, "pause_requested": 0, "run_id": "r1"}] + [paused] * 4 \
        + [ConnectionError("db away")] * 3 + [{"stop_requested": 1, "pause_requested": 0, "run_id": "r1"}]
    beats = _worker_env(main_mod, monkeypatch, states)
    main_mod.run_worker_mode(report=False)
    seen = [b for b in beats[1:] if b is not None]
    assert len(seen) >= 7 and all(later > earlier for earlier, later in zip(seen, seen[1:])), beats
    assert main_mod.LAST_WORKER["stop_reason"] == "stopped" and not os.path.exists(main_mod.LOCK_FILE)


def test_the_worker_takes_over_a_stale_lock_and_writes_the_json_lock(main_mod, monkeypatch):
    """run_worker_mode over a stale lock: it runs (no 'already running' exit) and holds a JSON lock while it works."""
    import google_sheets
    import local_cache_db

    _write(main_mod.LOCK_FILE, _json_lock(4242, host="OTHER-PC"))
    seen = {}
    monkeypatch.setattr(main_mod, "load_run_config", lambda: None)
    monkeypatch.setattr(local_cache_db, "resume_automation", lambda: True)
    monkeypatch.setattr(local_cache_db, "get_automation_state", lambda: {"stop_requested": 0, "run_id": None})
    monkeypatch.setattr(local_cache_db, "update_automation_state", lambda *a, **k: True)
    monkeypatch.setattr(main_mod, "check_verifier", lambda: "")

    def no_sheets():
        seen["lock"] = main_mod.read_lock(main_mod.LOCK_FILE)
        return None

    monkeypatch.setattr(google_sheets, "get_sheets_client", no_sheets)
    main_mod.run_worker_mode(report=False)
    assert seen["lock"]["kind"] == "json" and seen["lock"]["pid"] == os.getpid()
    assert main_mod.LAST_WORKER["stop_reason"] == "sheet_config"            # no credentials: a setting (C3)
    assert not os.path.exists(main_mod.LOCK_FILE)



# ---------------------------------------------------------------------------
# The Windows probe (_windows_process_info) through a fake subprocess.run (review fix P1: it had no test at all)
# ---------------------------------------------------------------------------

class _Done:
    def __init__(self, returncode=0, stdout=b""):
        self.returncode, self.stdout = returncode, stdout


def _fake_run(powershell, tasklist=None):
    """subprocess.run stand-in: each answer is a _Done or an exception to raise; records the commands."""
    calls = []

    def run(argv, **kwargs):
        calls.append(argv[0])
        answer = powershell if argv[0] == "powershell" else tasklist
        if isinstance(answer, BaseException):
            raise answer
        if answer is None:
            raise AssertionError(f"{argv[0]} was not expected")
        return answer

    run.calls = calls
    return run


def _probe(main_mod, monkeypatch, run, pid=4242):
    monkeypatch.setattr(subprocess, "run", run)
    return main_mod._windows_process_info(pid)


WORKER_CMD = r'"C:\Users\owner\مشروع الصور\.venv\Scripts\python.exe" -u "C:\Users\owner\مشروع الصور\main.py" --worker'


@pytest.mark.parametrize("stdout", [
    # what the old script printed: [Console]::OutputEncoding = UTF8 (with BOM) set before the first line
    "\ufeffNAME=python.exe\r\nCREATED=1790000000\r\nCMD=" + WORKER_CMD + "\r\n",
    # the probe now: no BOM, a PROBE marker first
    "PROBE=1\r\nNAME=python.exe\r\nCREATED=1790000000\r\nCMD=" + WORKER_CMD + "\r\n",
    "\ufeffPROBE=1\nNAME=python.exe\nCREATED=1790000000\nCMD=" + WORKER_CMD + "\n",
], ids=["bom-before-name", "crlf", "bom-lf"])
def test_the_windows_probe_reads_a_live_worker(main_mod, monkeypatch, stdout):
    run = _fake_run(_Done(0, stdout.encode("utf-8")))
    info = _probe(main_mod, monkeypatch, run)
    assert info == {"alive": True, "name": "python.exe", "created": 1790000000.0,
                    "cmdline": WORKER_CMD}
    assert run.calls == ["powershell"]


def test_the_windows_probe_says_gone_only_when_it_ran(main_mod, monkeypatch):
    # PowerShell ran and found no such process: a positive "gone"
    assert _probe(main_mod, monkeypatch, _fake_run(_Done(0, b"PROBE=1\r\n")))["alive"] is False
    # PowerShell printed nothing it understands: tasklist decides (Arabic Windows, OEM bytes in the memory column)
    csv = b'"python.exe","4242","Console","1","45\xa0320 \xe3\xc8"\r\n'
    run = _fake_run(_Done(0, b"\r\n"), _Done(0, csv))
    assert _probe(main_mod, monkeypatch, run) == {"alive": True, "name": "python.exe", "cmdline": None,
                                                            "created": None}
    assert run.calls == ["powershell", "tasklist"]
    # PowerShell timed out, tasklist answered "no task" in the system language: gone
    run = _fake_run(subprocess.TimeoutExpired("powershell", 30), _Done(0, "معلومات: لا توجد مهام قيد التشغيل".encode("cp1256")))
    assert _probe(main_mod, monkeypatch, run)["alive"] is False
    # another PID that only contains ours is not ours
    run = _fake_run(_Done(1, b""), _Done(0, b'"python.exe","42420","Console","1","1 K"\r\n'))
    assert _probe(main_mod, monkeypatch, run)["alive"] is False


@pytest.mark.parametrize("tasklist", [subprocess.TimeoutExpired("tasklist", 30), OSError("not found"), _Done(1, b"")],
                         ids=["timeout", "missing", "error"])
def test_a_windows_probe_that_cannot_answer_raises_instead_of_saying_gone(main_mod, monkeypatch, tasklist):
    """Review fix C2: tasklist was not wrapped; its timeout became a "stale" reason. Now both probes failing is an
    error (unknown), which lock_verdict retries once and then treats as a live lock."""
    run = _fake_run(subprocess.TimeoutExpired("powershell", 30), tasklist)
    with pytest.raises(RuntimeError):
        _probe(main_mod, monkeypatch, run)


def test_the_windows_probe_asks_for_utc_without_a_bom(main_mod, monkeypatch):
    seen = {}

    def run(argv, **kwargs):
        seen["script"] = argv[-1]
        return _Done(0, b"PROBE=1\r\n")

    _probe(main_mod, monkeypatch, run)
    script = seen["script"]
    assert "New-Object System.Text.UTF8Encoding $false" in script
    assert script.index("OutputEncoding") < script.index("'PROBE=1'")
    assert "CreationDate.ToUniversalTime()" in script and "ProcessId = 4242" in script


# ---------------------------------------------------------------------------
# Dashboard: ApiController::pipelineProcess asks Python (cli_bridge lock_state), so both sides use one rule
# ---------------------------------------------------------------------------

BRIDGE = ROOT / "cli_bridge.py"

# Framework stand-ins: Cache is an in-memory store; PythonBridge runs the real cli_bridge.py (BRIDGE_MODE=real),
# answers like a broken bridge (broken), or answers a fixed verdict from BRIDGE_VERDICT (fixed). It counts its calls.
PHP_LOCK_HARNESS = r"""<?php
namespace App\Http\Controllers { class Controller {} }
namespace Illuminate\Support\Facades {
    class Cache {
        public static $store = [];
        public static function get($k, $d = null) { return self::$store[$k] ?? $d; }
        public static function put($k, $v, $ttl = null) { self::$store[$k] = $v; return true; }
    }
}
namespace App\Services {
    class PythonBridge {
        public static $calls = [];
        public static function run($action, $params = []) {
            self::$calls[] = $action;
            $mode = getenv('BRIDGE_MODE');
            if ($mode === 'broken') { return ['status' => 'error', 'error' => 'Invalid JSON output from Python bridge']; }
            if ($mode === 'fixed') { return json_decode(getenv('BRIDGE_VERDICT'), true); }
            $cmd = escapeshellarg(getenv('PYTHON_BIN')) . ' ' . escapeshellarg(getenv('BRIDGE_PATH')) . ' '
                . $action . ' ' . escapeshellarg(json_encode($params ?: new \stdClass()));
            $lines = preg_split('/\r?\n/', trim((string) shell_exec($cmd . ' 2>/dev/null')));
            return json_decode((string) end($lines), true) ?: ['status' => 'error'];
        }
        public static function pythonPath() { return 'python'; }
    }
    class QueueStats {}
}
namespace {
    function base_path($p = '') { return getenv('HARNESS_ROOT') . '/dashboard' . ($p !== '' ? '/' . $p : ''); }
    require getenv('API_CONTROLLER');
    $c = new App\Http\Controllers\ApiController();
    $m = new ReflectionMethod($c, 'pipelineProcess');
    $m->setAccessible(true);
    $out = [];
    foreach (explode(',', getenv('CALLS') ?: 'fresh') as $call) {
        if ($call === 'touch') {
            file_put_contents(getenv('HARNESS_ROOT') . '/temp/pipeline.lock', getenv('LOCK_AFTER'));
            continue;
        }
        $p = $m->invoke($c, $call === 'fresh');
        $out[] = ['state' => $p['state'], 'pid' => $p['pid'], 'verified' => $p['verified']];
    }
    echo json_encode(['results' => $out, 'calls' => App\Services\PythonBridge::$calls]);
}
"""


def _run_lock_harness(tmp_path, content, mtime=None, mode="real", verdict=None, calls="fresh", after=""):
    root = tmp_path / "php_root"
    (root / "dashboard").mkdir(parents=True, exist_ok=True)
    (root / "temp").mkdir(exist_ok=True)
    lock = root / "temp" / "pipeline.lock"
    lock.write_text(content, encoding="utf-8")
    if mtime is not None:
        os.utime(lock, (mtime, mtime))
    script = tmp_path / "lock_harness.php"
    script.write_text(PHP_LOCK_HARNESS, encoding="utf-8")
    env = dict(os.environ, HARNESS_ROOT=str(root), API_CONTROLLER=str(CONTROLLER), BRIDGE_MODE=mode,
               BRIDGE_VERDICT=json.dumps(verdict or {}), PYTHON_BIN=sys.executable, BRIDGE_PATH=str(BRIDGE),
               CALLS=calls, LOCK_AFTER=after)
    result = subprocess.run([PHP, str(script)], capture_output=True, text=True, timeout=120, encoding="utf-8", env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


def _pipeline_process(tmp_path, content, mtime=None, mode="real"):
    return _run_lock_harness(tmp_path, content, mtime=mtime, mode=mode)["results"][0]


@pytest.mark.skipif(PHP is None or not LINUX_PROC, reason="needs the PHP CLI and Linux /proc")
def test_dashboard_reads_the_lock_with_pythons_rule(tmp_path, fake_worker_script):
    """Review fix C9 (and C1 on the dashboard side): ApiController re-implemented the rule (24-hour age, no start-time
    check, every process 'automation' where /proc is missing). It now asks cli_bridge lock_state."""
    worker = fake_worker_script("main.py")
    created = _proc_created(worker.pid)
    other = subprocess.Popen(["sleep", "30"])
    try:
        live = _json_lock(worker.pid, proc_created=created)
        assert _pipeline_process(tmp_path, live) == {"state": "running", "pid": str(worker.pid), "verified": True}
        assert _pipeline_process(tmp_path, str(worker.pid))["state"] == "running"         # the old format
        assert _pipeline_process(tmp_path, _json_lock(other.pid))["state"] == "none"       # not a python worker
        assert _pipeline_process(tmp_path, _json_lock(worker.pid, host="OTHER-PC"))["state"] == "none"
        # a worker running for two days is still running (the dashboard said 'none' after 24 h and Run started a
        # second worker over the live one)
        old = time.time() - 2 * 86400
        assert _pipeline_process(tmp_path, _json_lock(worker.pid, started_ts=old, heartbeat_ts=old,
                                                      proc_created=created))["state"] == "running"
        # a PID reused by another main.py-like process: the start time is not the lock writer's
        assert _pipeline_process(tmp_path, _json_lock(worker.pid, proc_created=created - 3600))["state"] == "none"
        assert _pipeline_process(tmp_path, str(worker.pid), mtime=old)["state"] == "none"
        assert _pipeline_process(tmp_path, "STARTING")["state"] == "starting"
        assert _pipeline_process(tmp_path, "garbage") == {"state": "none", "pid": None, "verified": False}
    finally:
        other.kill()
        other.wait()


def _proc_created(pid):
    import main
    return main._proc_process_info(pid)["created"]


@pytest.mark.skipif(PHP is None, reason="needs the PHP CLI")
def test_dashboard_fails_closed_when_python_does_not_answer(tmp_path):
    """No verdict from Python: a lock is a run (no second run starts) whose identity is not confirmed (nothing is
    killed)."""
    assert _pipeline_process(tmp_path, _json_lock(4242), mode="broken") == {"state": "running", "pid": None,
                                                                            "verified": False}


@pytest.mark.skipif(PHP is None, reason="needs the PHP CLI")
def test_status_polls_reuse_the_verdict_for_the_same_lock_content(tmp_path):
    """batchStatus (pipelineProcess(false)) is polled every few seconds by every page: one bridge call per lock
    content and LOCK_STATE_CACHE_S; Run / Stop / Reset always ask afresh. No lock or STARTING needs no bridge."""
    verdict = {"status": "success", "state": "running", "pid": 4242, "verified": True}
    out = _run_lock_harness(tmp_path, _json_lock(4242), mode="fixed", verdict=verdict,
                            calls="cached,cached,touch,cached,fresh", after=_json_lock(4242, heartbeat_ts=time.time() + 30))
    assert [r["state"] for r in out["results"]] == ["running"] * 4
    assert out["calls"] == ["lock_state"] * 3          # the second poll is cached; a new heartbeat and fresh ask again
    assert _run_lock_harness(tmp_path, "STARTING", mode="broken")["calls"] == []
