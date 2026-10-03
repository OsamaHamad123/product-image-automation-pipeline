"""The worker lock (temp/pipeline.lock) never skips a night because of a stale lock (package P4b, item 1).

Audit (lock_pid_reuse.py): main._another_worker_running accepted ANY live process with the lock's PID. After a crash
or a kill the PID is reused by another program, and the nightly logged "a worker is already running; nothing to do
tonight" and exited 0. The lock is now JSON {pid, host, started_at, started_ts, role, cmd} (the old plain-PID format
is still read) and counts only for a Python process running main.py / run_nightly.py on this host, started before
the lock was written and not older than MAX_LOCK_AGE_SECONDS; anything else is removed with the reason in the log.
The dashboard (ApiController::pipelineProcess) applies the same rules; it is checked under the PHP CLI.

Real processes are used where the platform allows it (/proc on Linux); the rest goes through an injected
process_info.
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
def test_a_reused_pid_of_another_program_does_not_skip_the_night(main_mod, capsys):
    """The audit's reproduction: the lock holds the PID of `sleep` (not a pipeline worker)."""
    other = subprocess.Popen(["sleep", "30"])
    try:
        for content in (str(other.pid), _json_lock(other.pid)):
            _write(main_mod.LOCK_FILE, content)
            assert main_mod._another_worker_running(main_mod.LOCK_FILE) is False
            assert not os.path.exists(main_mod.LOCK_FILE), "the stale lock is removed"
        assert "ليست بايثون" in capsys.readouterr().out
    finally:
        other.kill()
        other.wait()


@pytest.mark.skipif(not LINUX_PROC, reason="needs Linux /proc")
def test_a_python_process_that_is_not_the_worker_does_not_hold_the_lock(main_mod, fake_worker_script, capsys):
    proc = fake_worker_script("other_tool.py")
    _write(main_mod.LOCK_FILE, _json_lock(proc.pid))
    assert main_mod._another_worker_running(main_mod.LOCK_FILE) is False
    assert not os.path.exists(main_mod.LOCK_FILE)
    assert "ليست عامل الأتمتة" in capsys.readouterr().out


@pytest.mark.skipif(not LINUX_PROC, reason="needs Linux /proc")
def test_a_genuine_live_worker_holds_the_lock_in_both_formats(main_mod, fake_worker_script):
    proc = fake_worker_script("main.py")
    for content in (_json_lock(proc.pid), str(proc.pid)):          # the new JSON lock and the old plain PID
        _write(main_mod.LOCK_FILE, content)
        assert main_mod._another_worker_running(main_mod.LOCK_FILE) is True
        assert open(main_mod.LOCK_FILE, encoding="utf-8").read() == content, "a live worker's lock is kept"


@pytest.mark.skipif(not LINUX_PROC, reason="needs Linux /proc")
def test_a_worker_that_started_after_the_lock_was_written_is_a_reused_pid(main_mod, fake_worker_script, capsys):
    proc = fake_worker_script("run_nightly.py")
    _write(main_mod.LOCK_FILE, _json_lock(proc.pid, started_ts=time.time() - 3600))
    assert main_mod._another_worker_running(main_mod.LOCK_FILE) is False
    assert "بدأت بعد كتابة القفل" in capsys.readouterr().out


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


def test_a_lock_from_another_computer_is_stale_without_looking_at_this_computers_processes(main_mod, capsys):
    _write(main_mod.LOCK_FILE, _json_lock(4242, host="OTHER-PC"))

    def never(pid):
        raise AssertionError("a PID from another computer means nothing here")

    assert main_mod._another_worker_running(main_mod.LOCK_FILE, process_info=never) is False
    assert not os.path.exists(main_mod.LOCK_FILE)
    assert "OTHER-PC" in capsys.readouterr().out


def test_a_lock_older_than_the_maximum_is_stale(main_mod):
    old = time.time() - main_mod.MAX_LOCK_AGE_SECONDS - 60
    _write(main_mod.LOCK_FILE, _json_lock(4242, started_ts=old))
    assert main_mod._another_worker_running(main_mod.LOCK_FILE, process_info=_info()) is False
    # the old plain-PID format is aged by the file's modification time
    _write(main_mod.LOCK_FILE, "4242")
    os.utime(main_mod.LOCK_FILE, (old, old))
    assert main_mod._another_worker_running(main_mod.LOCK_FILE, process_info=_info()) is False


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


def test_our_own_lock_the_dashboards_starting_lock_and_garbage(main_mod, capsys):
    _write(main_mod.LOCK_FILE, _json_lock(os.getpid(), role="nightly"))     # the nightly holds it during the enqueue
    assert main_mod._another_worker_running(main_mod.LOCK_FILE, process_info=_info(alive=False)) is False
    assert os.path.exists(main_mod.LOCK_FILE)
    _write(main_mod.LOCK_FILE, "STARTING")                                  # the worker takes it over from the enqueue
    assert main_mod._another_worker_running(main_mod.LOCK_FILE) is False
    assert open(main_mod.LOCK_FILE).read() == "STARTING"
    _write(main_mod.LOCK_FILE, "not a lock")
    assert main_mod._another_worker_running(main_mod.LOCK_FILE) is False
    assert not os.path.exists(main_mod.LOCK_FILE)
    assert "محتوى غير مفهوم" in capsys.readouterr().out


def test_write_read_and_release_only_our_own_lock(main_mod):
    main_mod.write_lock("worker", main_mod.LOCK_FILE)
    lock = main_mod.read_lock(main_mod.LOCK_FILE)
    assert lock["kind"] == "json" and lock["pid"] == os.getpid() and lock["role"] == "worker"
    assert lock["host"] == socket.gethostname() and abs(lock["started_ts"] - time.time()) < 5
    raw = json.loads(open(main_mod.LOCK_FILE, encoding="utf-8").read())
    assert set(raw) >= {"pid", "host", "started_at", "started_ts", "role", "cmd"}
    main_mod.release_own_lock(main_mod.LOCK_FILE)
    assert not os.path.exists(main_mod.LOCK_FILE)
    # another worker's lock (it started after ours was judged stale) is left alone
    _write(main_mod.LOCK_FILE, _json_lock(4242))
    main_mod.release_own_lock(main_mod.LOCK_FILE)
    assert os.path.exists(main_mod.LOCK_FILE)


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
    assert main_mod.LAST_WORKER["stop_reason"] == "sheets_unavailable"
    assert not os.path.exists(main_mod.LOCK_FILE)


# ---------------------------------------------------------------------------
# Dashboard: ApiController::pipelineProcess follows the same rules
# ---------------------------------------------------------------------------

def _pipeline_process(tmp_path, content, mtime=None):
    root = tmp_path / "php_root"
    (root / "dashboard").mkdir(parents=True, exist_ok=True)
    (root / "temp").mkdir(exist_ok=True)
    lock = root / "temp" / "pipeline.lock"
    lock.write_text(content, encoding="utf-8")
    if mtime is not None:
        os.utime(lock, (mtime, mtime))
    controller = str(CONTROLLER).replace("\\", "/")
    base = str(root / "dashboard").replace("\\", "/")
    script = f"""<?php
namespace App\\Http\\Controllers {{ class Controller {{}} }}
namespace {{
function base_path($p = '') {{ return '{base}' . ($p !== '' ? '/' . $p : ''); }}
require '{controller}';
$c = new App\\Http\\Controllers\\ApiController();
$m = new ReflectionMethod($c, 'pipelineProcess');
$m->setAccessible(true);
$p = $m->invoke($c);
echo json_encode(['state' => $p['state'], 'pid' => $p['pid']]);
}}
"""
    with tempfile.NamedTemporaryFile("w", suffix=".php", delete=False, encoding="utf-8") as fh:
        fh.write(script)
        path = fh.name
    try:
        result = subprocess.run([PHP, path], capture_output=True, text=True, timeout=60, encoding="utf-8")
    finally:
        os.unlink(path)
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


@pytest.mark.skipif(PHP is None or not LINUX_PROC, reason="needs the PHP CLI and Linux /proc")
def test_dashboard_reads_the_json_lock_with_the_same_rules(tmp_path, fake_worker_script):
    worker = fake_worker_script("main.py")
    other = subprocess.Popen(["sleep", "30"])
    try:
        assert _pipeline_process(tmp_path, _json_lock(worker.pid)) == {"state": "running", "pid": str(worker.pid)}
        assert _pipeline_process(tmp_path, str(worker.pid))["state"] == "running"         # the old format
        assert _pipeline_process(tmp_path, _json_lock(other.pid))["state"] == "none"       # not a python worker
        assert _pipeline_process(tmp_path, _json_lock(worker.pid, host="OTHER-PC"))["state"] == "none"
        old = time.time() - 2 * 86400
        assert _pipeline_process(tmp_path, _json_lock(worker.pid, started_ts=old))["state"] == "none"
        assert _pipeline_process(tmp_path, str(worker.pid), mtime=old)["state"] == "none"
        assert _pipeline_process(tmp_path, "STARTING")["state"] == "starting"
        assert _pipeline_process(tmp_path, "garbage") == {"state": "none", "pid": None}
    finally:
        other.kill()
        other.wait()
