"""Server operations around the Ubuntu deployment kit (deploy/ubuntu): the failure alert of a systemd unit
(scripts/unit_alert.py), the outbox flush between runs with its DEAD alert (scripts/flush_sheets_sync.py and
google_sheets.outbox_due_count), the dashboard run launcher (RunLauncher.php, ApiController) and the /healthz
endpoint (HealthzController.php).

Telegram is a fake everywhere, systemctl is a fake, nothing reaches the network. The outbox and /healthz tests use the
real MariaDB test database (they skip, loudly named, when MariaDB is down); the PHP tests need php and
dashboard/vendor, like the other dashboard tests.
"""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[1]
DASH = REPO / "dashboard"
PHP = shutil.which("php")
NEEDS_PHP = pytest.mark.skipif(PHP is None, reason="php is not installed")
NEEDS_LARAVEL = pytest.mark.skipif(PHP is None or not (DASH / "vendor" / "autoload.php").exists(),
                                   reason="php or dashboard/vendor is missing")


def load_script(name):
    spec = importlib.util.spec_from_file_location(f"{name}_under_test", REPO / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fake_value(tag):
    """A throw-away value built at run time: the source never holds a credential-looking literal."""
    return "-".join(("tst", tag, "never", "printed", "7f3c9a1e"))


# ---------------------------------------------------------------------------
# scripts/unit_alert.py (laqta-alert@.service)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("unit", ["laqta-nightly.service", "laqta-backup.service", "laqta-sync-worker.service",
                                  "laqta-outbox-flush.timer", "laqta-run.path"])
def test_alert_accepts_only_laqta_units(unit):
    ua = load_script("unit_alert")
    assert ua.UNIT_RE.match(unit)


@pytest.mark.parametrize("unit", ["sshd.service", "laqta-alert@laqta-nightly.service", "laqta-x.service; reboot",
                                  "../laqta-x.service", "laqta-.service ", "", "laqta-nightly", "laqta-a b.service",
                                  "laqta-" + "x" * 80 + ".service"])
def test_alert_refuses_anything_else_including_itself(unit):
    ua = load_script("unit_alert")
    sent = []
    assert not ua.UNIT_RE.match(unit)
    assert ua.main([unit], send=sent.append, configured=True) == 1 and not sent    # and nothing is sent for it
    assert ua.main([], send=sent.append, configured=True) == 1
    assert ua.main(["laqta-a.service", "laqta-b.service"], send=sent.append, configured=True) == 1
    assert not sent


def test_alert_message_names_the_unit_the_host_and_the_result_only():
    ua = load_script("unit_alert")
    secret = fake_value("secret")
    env_before = dict(os.environ)
    os.environ["TELEGRAM_BOT_TOKEN"] = secret
    try:
        text = ua.build_message("laqta-nightly.service", "srv-1", {"result": "oom-kill", "status": "137"})
    finally:
        os.environ.clear()
        os.environ.update(env_before)
    assert "laqta-nightly.service" in text and "srv-1" in text and "137" in text
    assert "الذاكرة" in text                                      # oom-kill in plain words
    assert "journalctl -u laqta-nightly -e" in text
    assert secret not in text
    assert len(text) < 500                                         # short


def test_alert_escapes_html_in_the_host_name():
    ua = load_script("unit_alert")
    text = ua.build_message("laqta-backup.service", "<b>host</b>&co", {})
    assert "<b>host</b>" not in text and "&lt;b&gt;host&lt;/b&gt;&amp;co" in text


def test_alert_reads_systemd_result_words_and_ignores_anything_odd():
    ua = load_script("unit_alert")

    def run(cmd, **kwargs):
        assert cmd[:3] == ["systemctl", "show", "laqta-nightly.service"]
        return SimpleNamespace(stdout="Result=exit-code\nExecMainStatus=1\nOther=x\n")

    assert ua.unit_facts("laqta-nightly.service", run) == {"result": "exit-code", "status": "1"}

    def odd(cmd, **kwargs):
        return SimpleNamespace(stdout="Result=<script>alert(1)</script>\nExecMainStatus=1; rm\n")

    assert ua.unit_facts("laqta-nightly.service", odd) == {}

    def broken(cmd, **kwargs):
        raise FileNotFoundError("systemctl")

    assert ua.unit_facts("laqta-nightly.service", broken) == {}


def test_alert_main_sends_once_and_reports_each_outcome(capsys):
    ua = load_script("unit_alert")
    sent = []
    run = lambda cmd, **kw: SimpleNamespace(stdout="Result=timeout\nExecMainStatus=0\n")   # noqa: E731

    assert ua.main(["laqta-nightly.service"], send=lambda m: sent.append(m) or True, run=run, hostname="srv",
                   configured=True) == 0
    assert len(sent) == 1 and "laqta-nightly.service" in sent[0] and "تجاوزت المهلة" in sent[0]

    # Telegram not set up: a journal line, no message, not a failure
    sent.clear()
    assert ua.main(["laqta-nightly.service"], send=lambda m: sent.append(m) or True, run=run, configured=False) == 0
    assert not sent and "Telegram is not set up" in capsys.readouterr().out

    # Telegram refuses: the alert unit itself fails (visible in systemctl --failed), and no loop starts
    assert ua.main(["laqta-nightly.service"], send=lambda m: False, run=run, hostname="srv", configured=True) == 2


# ---------------------------------------------------------------------------
# google_sheets.outbox_due_count + scripts/flush_sheets_sync.py
# ---------------------------------------------------------------------------

@pytest.fixture
def outbox(mariadb_or_skip):
    """An empty sheet_updates table of the test database."""
    import google_sheets
    google_sheets.SQLiteTransactionQueue()                    # creates / upgrades the table
    conn = google_sheets._db_connect()
    try:
        conn.cursor().execute("DELETE FROM sheet_updates")
        conn.commit()
    finally:
        conn.close()

    def add(status, n=1, next_attempt_at=None, name="P", error=None, age_days=0):
        c = google_sheets._db_connect()
        try:
            cur = c.cursor()
            for i in range(n):
                cur.execute(
                    "INSERT INTO sheet_updates (`row_number`, col_index, `value`, sync_status, next_attempt_at, "
                    "key_name, last_error, registered_at) VALUES (%s, 1, 'v', %s, %s, %s, %s, "
                    "NOW() - INTERVAL %s DAY)", (10 + i, status, next_attempt_at, name, error, age_days))
            c.commit()
        finally:
            c.close()

    add.google_sheets = google_sheets
    return add


def test_due_count_is_pending_and_failed_past_their_retry_time_only(outbox):
    gs = outbox.google_sheets
    now = 1_800_000_000
    outbox("PENDING", 2)
    outbox("FAILED", 1, next_attempt_at=now - 5)         # due
    outbox("FAILED", 1, next_attempt_at=None)            # due (never scheduled)
    outbox("FAILED", 3, next_attempt_at=now + 600)       # waits for its retry time
    for status in ("SYNCED", "DEAD", "CONFLICT", "SUPERSEDED", "SKIPPED_OUT_OF_BOUNDS"):
        outbox(status, 2)
    assert gs.outbox_due_count(now=now) == 4
    assert gs.outbox_due_count(now=now + 3600) == 7      # the waiting ones are due an hour later


def test_flush_main_skips_google_when_nothing_is_due(outbox, monkeypatch, capsys):
    flush_script = load_script("flush_sheets_sync")
    called = []
    monkeypatch.setattr(flush_script, "flush", lambda: called.append("flush"))
    monkeypatch.setattr(flush_script, "check_dead_alert", lambda: called.append("alert"))
    outbox("SYNCED", 3)
    assert flush_script.main() == 0
    assert called == ["alert"] and "لم نفتح الشيت" in capsys.readouterr().out


def test_flush_main_flushes_when_a_write_is_due_and_still_checks_for_dead_rows(outbox, monkeypatch):
    flush_script = load_script("flush_sheets_sync")
    called = []
    monkeypatch.setattr(flush_script, "flush", lambda: called.append("flush"))
    monkeypatch.setattr(flush_script, "check_dead_alert", lambda: called.append("alert"))
    outbox("PENDING", 1)
    assert flush_script.main() == 0 and called == ["flush", "alert"]


def test_flush_main_survives_a_database_that_does_not_answer(monkeypatch, capsys):
    flush_script = load_script("flush_sheets_sync")

    def down():
        raise OSError("connection refused")

    monkeypatch.setattr(flush_script.google_sheets, "outbox_due_count", down)
    monkeypatch.setattr(flush_script, "flush", lambda: pytest.fail("flush must not run without a database"))
    assert flush_script.main() == 0                       # a normal exit: the unit must not "fail" every 2 minutes
    assert "قاعدة البيانات لا ترد" in capsys.readouterr().out


def test_a_failing_dead_check_never_breaks_the_flush(outbox, monkeypatch):
    flush_script = load_script("flush_sheets_sync")
    called = []
    monkeypatch.setattr(flush_script, "flush", lambda: called.append("flush"))

    def broken():
        raise RuntimeError("boom")

    monkeypatch.setattr(flush_script, "check_dead_alert", broken)
    outbox("PENDING", 1)
    assert flush_script.main() == 0 and called == ["flush"]


def test_flush_main_does_not_hide_an_unexpected_flush_error(outbox, monkeypatch):
    flush_script = load_script("flush_sheets_sync")
    alerts = []
    monkeypatch.setattr(flush_script, "check_dead_alert", lambda: alerts.append("alert"))

    def broken():
        raise RuntimeError("bug in the flush")

    monkeypatch.setattr(flush_script, "flush", broken)
    outbox("PENDING", 1)
    with pytest.raises(RuntimeError):
        flush_script.main()                                # the unit shows as failed ...
    assert alerts == ["alert"]                             # ... and the dead-row check still ran


# --- the DEAD alert: once per increase -------------------------------------------------------------------------

def test_dead_alert_fires_once_per_increase(tmp_path):
    fs = load_script("flush_sheets_sync")
    state = tmp_path / "state.json"
    sent = []

    def send(message):
        sent.append(message)
        return True

    def check(count):
        return fs.check_dead_alert(count=count, state_path=str(state), send=send, configured=True)

    assert check(0) is False and not sent                  # nothing dead, nothing sent
    assert check(3) is True and len(sent) == 1             # 0 -> 3
    assert "3" in sent[0] and "كان 0" in sent[0]
    assert check(3) is False and len(sent) == 1            # same count: silent, every 2 minutes
    assert check(3) is False and len(sent) == 1
    assert check(5) is True and len(sent) == 2             # 3 -> 5: one more message
    assert "5" in sent[1] and "كان 3" in sent[1]
    assert check(2) is False and len(sent) == 2            # fewer (rows cleaned up): recorded silently ...
    assert check(3) is True and len(sent) == 3             # ... so the next increase alerts again
    assert json.loads(state.read_text())["dead"] == 3


def test_dead_alert_is_retried_while_telegram_refuses_and_dropped_when_not_set_up(tmp_path):
    fs = load_script("flush_sheets_sync")
    state = tmp_path / "state.json"
    attempts = []

    def refuse(message):
        attempts.append(message)
        return False

    assert fs.check_dead_alert(count=2, state_path=str(state), send=refuse, configured=True) is False
    assert fs.check_dead_alert(count=2, state_path=str(state), send=refuse, configured=True) is False
    assert len(attempts) == 2 and not state.exists()       # not recorded: it is retried by the next run

    assert fs.check_dead_alert(count=2, state_path=str(state), send=lambda m: True, configured=True) is True

    other = tmp_path / "other.json"                         # Telegram not set up: no send, state still moves on
    assert fs.check_dead_alert(count=4, state_path=str(other), send=refuse, configured=False) is False
    assert json.loads(other.read_text())["dead"] == 4 and len(attempts) == 2


def test_dead_alert_treats_a_damaged_state_file_as_zero(tmp_path):
    fs = load_script("flush_sheets_sync")
    state = tmp_path / "state.json"
    for junk in ("", "not json", "[1, 2]", '{"dead": "many"}', '{"dead": -4}'):
        state.write_text(junk)
        sent = []
        assert fs.check_dead_alert(count=1, state_path=str(state), send=lambda m: sent.append(m) or True,
                                   configured=True) is True and len(sent) == 1


def test_dead_alert_text_holds_numbers_only(outbox, tmp_path):
    """Product names and error texts of the dead rows (which may quote a key from a URL) never reach Telegram."""
    fs = load_script("flush_sheets_sync")
    secret = fake_value("apikey")
    outbox("DEAD", 2, name="SECRET PRODUCT NAME 500G", error=f"APIError 400 key={secret}")
    sent = []
    assert fs.check_dead_alert(state_path=str(tmp_path / "state.json"), send=lambda m: sent.append(m) or True,
                               configured=True) is True      # the count comes from the real outbox table
    assert len(sent) == 1 and "2" in sent[0]
    assert "SECRET PRODUCT" not in sent[0] and secret not in sent[0] and "key=" not in sent[0]


def test_dead_alert_uses_the_existing_telegram_notifier(monkeypatch, tmp_path):
    fs = load_script("flush_sheets_sync")
    sent = []
    monkeypatch.setattr(fs.config, "telegram_configured", lambda: True)
    monkeypatch.setattr(fs.config, "send_telegram_alert", lambda message: sent.append(message) or True)
    assert fs.check_dead_alert(count=1, state_path=str(tmp_path / "s.json")) is True and len(sent) == 1
