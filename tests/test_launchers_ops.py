"""The launchers never kill a running worker, and the desktop app window keeps its server (package P4b, item 2).

- setup_and_launch.ps1 ran `Stop-Process -Name python -Force`: every Python process, including a nightly run or a
  dashboard worker holding temp/pipeline.lock, was killed at each launch. The launchers now stop only the processes
  they own (the dashboard's PHP server and the Sheets sync worker), found by their command line, and never one
  running main.py or run_nightly.py.
- launch_desktop.ps1 started `chrome.exe --app=...` without a separate --user-data-dir. With Chrome already open the
  new process hands the URL to the open browser and exits at once, so WaitForExit returned and the script stopped
  the Laravel server under the window. It now uses its own profile folder like setup_and_launch.ps1.

PowerShell is not installed here: the scripts are checked as text, and parsed when PowerShell is available.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SETUP = ROOT / "setup_and_launch.ps1"
DESKTOP = ROOT / "launch_desktop.ps1"
LAUNCHERS = [SETUP, DESKTOP, ROOT / "start_all.bat", ROOT / "setup_and_launch.bat", ROOT / "launch_desktop.bat"]
PWSH = shutil.which("pwsh") or shutil.which("powershell")


def read(path):
    return path.read_bytes().decode("utf-8-sig")


def code_lines(text):
    """Lines that are not PowerShell / batch comments."""
    return [line for line in text.splitlines() if not line.lstrip().startswith(("#", "rem ", "REM ", "::"))]


@pytest.mark.parametrize("path", LAUNCHERS, ids=lambda p: p.name)
def test_no_launcher_stops_processes_by_name(path):
    code = "\n".join(code_lines(read(path)))
    assert not re.search(r"Stop-Process\s+-Name", code, re.IGNORECASE), path.name
    assert not re.search(r"taskkill\s+(/F\s+)?/IM\s+python", code, re.IGNORECASE), path.name
    assert not re.search(r"taskkill\s+(/F\s+)?/IM\s+php", code, re.IGNORECASE), path.name


@pytest.mark.parametrize("path", [SETUP, DESKTOP], ids=lambda p: p.name)
def test_launchers_stop_only_their_own_servers_and_never_the_worker(path):
    text = read(path)
    block = text[text.index("Get-CimInstance Win32_Process -Filter \"Name = 'php.exe'"):]
    block = block[:block.index("ForEach-Object { Stop-Process -Id $_.ProcessId")]
    # what the launcher started: the dashboard server and the Sheets sync worker
    assert "*sync_worker.py*" in block and "*server.php*" in block.replace("dashboard/", "").replace("dashboard\\", "")
    # never the automation worker or the nightly run, even if a command line also matched
    assert "$cmd -notlike '*main.py*'" in block and "$cmd -notlike '*run_nightly.py*'" in block


def test_setup_and_launch_keeps_the_dashboard_server_it_starts():
    text = read(SETUP)
    stop_old = text.index("Get-CimInstance Win32_Process -Filter \"Name = 'php.exe'")
    start_new = text.index("$laravelProcess = Start-Process")
    assert stop_old < start_new, "old servers are stopped before the new one starts"
    # at exit only the processes this launch started are stopped, by their own PID
    tail = text[text.index("# ----------------- 7."):]
    assert "Stop-Process -Id $laravelProcess.Id" in tail and "Stop-Process -Id $syncWorkerProcess.Id" in tail


@pytest.mark.parametrize("path", [SETUP, DESKTOP], ids=lambda p: p.name)
def test_chrome_app_window_has_its_own_profile_and_the_server_waits_for_it(path):
    text = read(path)
    starts = [line for line in code_lines(text) if "--app=http://127.0.0.1:8000/" in line]
    assert starts, path.name
    for line in starts:
        assert "--user-data-dir=`\"$chromeProfile`\"" in line, line
    assert 'Join-Path $PSScriptRoot "temp\\chrome_profile"' in text
    # a window of the same profile left open by an earlier launch: wait for it before stopping the server
    wait = text[text.index(".WaitForExit()"):]
    assert "Where-Object { ([string]$_.CommandLine).Contains($chromeProfile) }" in wait
    assert wait.index("Contains($chromeProfile)") < wait.index("Stop-Process -Id $laravelProcess.Id")


@pytest.mark.parametrize("path", [SETUP, DESKTOP], ids=lambda p: p.name)
def test_launchers_keep_their_utf8_bom(path):
    assert path.read_bytes().startswith(b"\xef\xbb\xbf")


@pytest.mark.skipif(PWSH is None, reason="PowerShell is not installed")
@pytest.mark.parametrize("path", [SETUP, DESKTOP], ids=lambda p: p.name)
def test_launchers_parse(path):
    command = ("$errors = $null; [System.Management.Automation.Language.Parser]::ParseFile("
               f"'{path}', [ref]$null, [ref]$errors) | Out-Null; $errors.Count")
    result = subprocess.run([PWSH, "-NoProfile", "-Command", command], capture_output=True, text=True, timeout=120)
    assert result.returncode == 0 and result.stdout.strip() == "0", result.stdout + result.stderr
