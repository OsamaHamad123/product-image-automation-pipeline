"""deploy/ubuntu/update.sh: one-command update of an installed Laqta (static checks; it runs as root on Ubuntu).

It must stay an app-only update (the server may host other sites), go forward only, back up first, and put the old
commit back by itself when a step or the health check fails."""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "deploy" / "ubuntu" / "update.sh"
TEXT = SCRIPT.read_text(encoding="utf-8")
CODE = "\n".join(line for line in TEXT.splitlines() if not line.lstrip().startswith("#"))


def _bash():
    """A bash that runs (on Windows `bash` can be the WSL stub without a distribution: skip then)."""
    for candidate in (shutil.which("bash"), r"C:\Program Files\Git\bin\bash.exe"):
        if candidate and Path(candidate).exists():
            try:
                if subprocess.run([candidate, "-c", "true"], capture_output=True, timeout=30).returncode == 0:
                    return candidate
            except (OSError, subprocess.TimeoutExpired):
                pass
    return None


def test_the_script_parses():
    bash = _bash()
    if bash is None:
        pytest.skip("no working bash")
    result = subprocess.run([bash, "-n", SCRIPT.as_posix()], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_it_never_touches_the_web_server_the_pool_or_the_units():
    for forbidden in ("nginx", "php-fpm", "php8", "systemctl reload", "systemctl restart", "daemon-reload",
                      "/etc/systemd", "certbot"):
        assert forbidden not in CODE, forbidden
    assert not re.search(r"bash\s+\S*install\.sh", CODE)                # never re-runs the full installer


def test_it_goes_forward_only_and_refuses_local_edits():
    assert "merge -q --ff-only" in CODE and "merge-base --is-ancestor" in CODE
    assert "status --porcelain --untracked-files=no" in CODE


def test_a_failure_after_the_code_moved_puts_the_old_commit_back():
    assert "set -euo pipefail" in CODE and "set -o errtrace" in CODE     # the ERR trap also fires inside functions
    assert re.search(r"trap rollback ERR\n", CODE)
    assert 'reset -q --hard "$OLD"' in CODE
    trap_at, merge_at, health_at = CODE.index("trap rollback ERR"), CODE.index("--ff-only \"$NEW\""), CODE.index("/healthz answered")
    assert trap_at < merge_at < health_at                                  # armed before the code moves, until the check
    assert CODE.index("trap - ERR") > health_at


def test_backup_first_and_never_during_a_nightly_run():
    assert CODE.index("laqta-backup.service") < CODE.index("trap rollback ERR")
    assert "is-active --quiet laqta-nightly.service" in CODE


def test_dependencies_only_when_their_lock_files_changed_and_python_tables_after_migrate():
    assert "changed requirements.txt" in CODE and "changed dashboard/composer.lock" in CODE
    assert CODE.index("migrate --force") < CODE.index("ensure_schema()")
