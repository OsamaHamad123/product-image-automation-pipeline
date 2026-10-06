"""A short Telegram message that a systemd unit failed. Started by laqta-alert@.service (OnFailure= of the nightly
run, the sync worker and the backup):

    python scripts/unit_alert.py laqta-nightly.service

The message holds the unit name, the host name and systemd's result word (exit-code, timeout, oom-kill ...) and the exit
status, nothing from .env or from the unit's output. It goes through the same notifier as every other alert
(config.send_telegram_alert); without TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID it only prints a line for the journal.

Exit code: 0 = sent (or Telegram is not set up), 1 = bad unit name, 2 = Telegram refused the message.
"""

import html
import os
import re
import socket
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Only Laqta's own units, and never an alert unit itself (no "@"): an alert about an alert would loop.
UNIT_RE = re.compile(r"^laqta-[A-Za-z0-9_.-]{1,60}\.(service|timer|path)$")
WORD_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")

RESULT_TEXT = {
    "exit-code": "انتهت برمز خطأ",
    "signal": "انقتلت بإشارة",
    "timeout": "تجاوزت المهلة",
    "oom-kill": "انقتلت لأن الذاكرة خلصت",
    "core-dump": "وقعت (core dump)",
    "watchdog": "ما ردّت على المراقب",
    "start-limit-hit": "تكرر فشل تشغيلها",
    "resources": "ما لقت موارد تشتغل فيها",
}


def unit_facts(unit, run=subprocess.run):
    """{result, status}: systemd's Result= and ExecMainStatus= words for the unit; {} when systemctl cannot say."""
    try:
        done = run(["systemctl", "show", unit, "-p", "Result", "-p", "ExecMainStatus", "--no-pager"],
                   capture_output=True, text=True, timeout=15)
    except Exception:  # noqa: BLE001 - an alert must never fail on its own helper
        return {}
    facts = {}
    for line in (getattr(done, "stdout", "") or "").splitlines():
        key, _, value = line.partition("=")
        value = value.strip()
        if key == "Result" and WORD_RE.match(value):
            facts["result"] = value
        elif key == "ExecMainStatus" and value.isdigit():
            facts["status"] = value
    return facts


def build_message(unit, host, facts):
    """The Telegram text (HTML parse mode): only the validated unit name, the host and systemd's words."""
    result = facts.get("result")
    lines = ["🚨 <b>خدمة فشلت على السيرفر</b>", "",
             f"⚙️ <b>الخدمة:</b> <code>{html.escape(unit)}</code>",
             f"🖥️ <b>السيرفر:</b> {html.escape(host)}"]
    if result and result != "success":
        lines.append("❌ <b>السبب:</b> " + html.escape(RESULT_TEXT.get(result, result)))
    if facts.get("status") not in (None, "0"):
        lines.append("🔢 <b>رمز الخروج:</b> " + html.escape(facts["status"]))
    short = unit.rsplit(".", 1)[0]
    lines += ["", f"💡 <i>الأمر للسجل على السيرفر: journalctl -u {html.escape(short)} -e</i>"]
    return "\n".join(lines)


def main(argv=None, send=None, run=subprocess.run, hostname=None, configured=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 1 or not UNIT_RE.match(argv[0]):
        print("usage: unit_alert.py laqta-<name>.service|timer|path", file=sys.stderr)
        return 1
    unit = argv[0]
    if send is None or configured is None:
        sys.path.insert(0, ROOT)
        import config
        send = send or config.send_telegram_alert
        if configured is None:
            configured = config.telegram_configured()
    if not configured:
        print(f"unit_alert: {unit} failed, but Telegram is not set up (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID): no message sent")
        return 0
    message = build_message(unit, hostname or socket.gethostname(), unit_facts(unit, run))
    if send(message):
        print(f"unit_alert: alert sent for {unit}")
        return 0
    print(f"unit_alert: Telegram did not accept the alert for {unit}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
