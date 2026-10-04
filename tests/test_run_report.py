"""The owner always knows what happened (package P4b, items 3 and 4).

- A database outage is never a finished night: get_automation_state() says 'db_unavailable' (it said 'idle'),
  get_ready_for_review_count() says None (it said 0), the worker stops at once with stop reason db_unavailable and
  `python main.py --worker` exits 2 (it exited 0).
- After every run: a run_history row, temp/nightly/last_report.json, and a short Arabic Telegram message only when
  Telegram is configured (the helper existed but no run called it).
- Exit codes say what happened: 0 done / skipped, 1 failed, 2 outage, 3 stopped before the queue was empty; any stop
  reason another package adds (BUDGET_REACHED, SERPER_CREDIT) is shown as it is.

Real-MariaDB tests use the test database (conftest) and skip when MariaDB is down; the rest is offline.
"""

import datetime
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RUN = "p4ops-run-"
ROWS = tuple(range(940001, 940010))


def _sql(db, statement, params=()):
    conn = db.get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute(statement, params)
        rows = cur.fetchall()
        conn.commit()
        return rows
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# A database outage is explicit
# ---------------------------------------------------------------------------

def test_a_database_that_does_not_answer_is_not_idle(offline):
    import local_cache_db

    state = local_cache_db.get_automation_state()          # `offline` refuses the connection
    assert state["status"] == "db_unavailable" and "OperationalError" in state["db_error"]
    assert local_cache_db.get_ready_for_review_count() is None
    assert local_cache_db.db_available() is False


def test_the_worker_stops_at_once_when_the_database_is_down(offline, monkeypatch, tmp_path):
    import google_sheets
    import main
    import run_report

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(main, "load_run_config", lambda: None)
    monkeypatch.setattr(main, "check_verifier", lambda: pytest.fail("no verifier check without a database"))
    monkeypatch.setattr(google_sheets, "get_sheets_client", lambda: pytest.fail("the sheet is not opened"))
    reports = []
    monkeypatch.setattr(run_report, "report_worker_run", lambda info, trigger: reports.append((info, trigger)))

    main.run_worker_mode(trigger="dashboard")

    assert main.LAST_WORKER["stop_reason"] == "db_unavailable"
    assert run_report.exit_code(main.LAST_WORKER["stop_reason"]) == 2
    ((info, trigger),) = reports
    assert trigger == "dashboard" and info["notice"].startswith("DB_UNAVAILABLE: ")
    assert not os.path.exists(main.LOCK_FILE)


def test_worker_command_line_exits_2_and_writes_the_report_when_the_database_is_down(tmp_path):
    """`python main.py --worker` with MariaDB unreachable (port 1 on this machine): exit 2, not 0."""
    env = dict(os.environ, DB_HOST="127.0.0.1", DB_PORT="1", TELEGRAM_BOT_TOKEN="", TELEGRAM_CHAT_ID="",
               PYTHONIOENCODING="utf-8")
    result = subprocess.run([sys.executable, str(ROOT / "main.py"), "--worker", "--trigger=dashboard"], cwd=tmp_path,
                            env=env, capture_output=True, timeout=120)
    assert result.returncode == 2, result.stdout.decode(errors="replace")[-2000:]
    report = json.loads((tmp_path / "temp" / "nightly" / "last_report.json").read_text(encoding="utf-8"))
    assert report["trigger"] == "dashboard" and report["outcome"] == "outage" and report["exit_code"] == 2
    assert report["database"] == "unavailable" and report["history_id"] is None
    assert not (tmp_path / "temp" / "pipeline.lock").exists()


# ---------------------------------------------------------------------------
# Stop reason -> outcome and exit code
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("reason, outcome, code", [
    (None, "done", 0), ("", "done", 0), ("another_worker", "skipped", 0),
    ("db_unavailable", "outage", 2), ("sheets_unavailable", "outage", 2),
    ("sheet_not_found", "failed", 1),         # review fix C3: not found / not shared is a setting, not an outage
    ("provider_down", "outage", 2),
    ("sheet_config", "failed", 1), ("enqueue_failed", "failed", 1), ("worker_error", "failed", 1),
    ("stopped", "stopped", 3), ("BUDGET_REACHED", "stopped", 3), ("SERPER_CREDIT", "stopped", 3),
    ("something_new", "stopped", 3),
])
def test_exit_codes_mean_what_they_say(reason, outcome, code):
    import run_report

    assert (run_report.outcome_of(reason), run_report.exit_code(reason)) == (outcome, code)
    if reason == "something_new":
        assert run_report.reason_text(reason) == reason          # an unknown reason is shown as the worker wrote it
    if reason in ("BUDGET_REACHED", "SERPER_CREDIT"):
        assert run_report.reason_text(reason) != reason          # the queue package's stops have an Arabic text


# ---------------------------------------------------------------------------
# Counts and run_history (real MariaDB)
# ---------------------------------------------------------------------------

@pytest.fixture
def db(mariadb_or_skip):
    db = mariadb_or_skip

    def wipe():
        _sql(db, "DELETE FROM automation_queue WHERE `row_number` BETWEEN %s AND %s", (ROWS[0], ROWS[-1]))
        _sql(db, "DELETE FROM run_history WHERE run_id LIKE %s", (RUN + "%",))

    wipe()
    yield db
    wipe()


def _queue_row(db, row, status, code=None, decision=None, run_id=RUN + "a", worker="hostA:77#c1"):
    trace = json.dumps({"outcome": {"decision": decision}}) if decision else None
    _sql(db, "INSERT INTO automation_queue (`row_number`, product_name, status, failure_code, trace_json, run_id, "
             "worker_id) VALUES (%s, %s, %s, %s, %s, %s, %s)", (row, f"P{row}", status, code, trace, run_id, worker))


def test_run_counts_come_from_the_rows_of_the_run(db):
    _queue_row(db, ROWS[0], "completed", decision="AUTO_PUBLISH")
    _queue_row(db, ROWS[1], "completed", decision="REVIEW_PRESELECTED")      # approved by a reviewer during the run
    _queue_row(db, ROWS[2], "ready_for_review", decision="REVIEW_PRESELECTED")
    _queue_row(db, ROWS[3], "ready_for_review", decision="REVIEW_UNSELECTED")
    _queue_row(db, ROWS[4], "failed", code="NO_RESULTS", decision="NOT_FOUND")
    _queue_row(db, ROWS[5], "failed", code="SEARCH_ERROR")
    _queue_row(db, ROWS[6], "pending", code="PROVIDER_DOWN")
    _queue_row(db, ROWS[7], "pending")
    _queue_row(db, ROWS[8], "ready_for_review", run_id=RUN + "b", worker="hostA:77#c9")     # an earlier attempt

    counts = db.run_outcome_counts(run_ids=[RUN + "a"])
    assert counts == {"enqueued": 8, "searched": 7, "auto_published": 1, "ready_for_review": 2, "not_found": 1,
                      "failed": 1, "provider_down": 1, "pending_left": 2}
    both = db.run_outcome_counts(run_ids=[RUN + "a", RUN + "b"])
    assert both["enqueued"] == 9 and both["ready_for_review"] == 3
    # a worker started by hand without an enqueue has no run_id: its own claims in the time it ran
    assert db.run_outcome_counts(worker_id="hostA:77", since_seconds=600)["enqueued"] == 9
    assert db.run_outcome_counts(worker_id="hostB:77", since_seconds=600)["enqueued"] == 0


def test_a_worker_run_is_written_to_run_history_and_last_report(db, tmp_path):
    import run_report

    _queue_row(db, ROWS[0], "ready_for_review", decision="REVIEW_PRESELECTED")
    _queue_row(db, ROWS[1], "failed", code="NO_MATCH")
    info = {"stop_reason": None, "run_id": RUN + "a", "worker_id": "hostA:77", "started_ts": 1_790_000_000,
            "ended_ts": 1_790_000_600, "notice": "VERIFIER_NOT_CONFIGURED: no Gemini key",
            "health": {"windows": {"7d": {"cost_usd": {"total": 0.0123}}}}}
    path = str(tmp_path / "last_report.json")
    report = run_report.report_worker_run(info, trigger="dashboard", path=path)

    (row,) = [r for r in db.get_run_history(limit=50) if r["run_id"] == RUN + "a"]
    assert row["id"] == report["history_id"] and row["run_trigger"] == "dashboard" and row["outcome"] == "done"
    assert (row["enqueued"], row["searched"], row["ready_for_review"], row["not_found"], row["failed"]) == (2, 2, 1, 1, 0)
    assert row["exit_code"] == 0 and row["attempts"] == 1 and row["stop_reason"] is None
    assert float(row["spend_usd"]) == pytest.approx(0.0123) and row["spend_source"] == "estimate"
    assert row["notices"] == "VERIFIER_NOT_CONFIGURED: no Gemini key"
    assert row["started_at"] == datetime.datetime.fromtimestamp(1_790_000_000)
    assert row["ended_at"] == datetime.datetime.fromtimestamp(1_790_000_600)
    saved = json.loads(Path(path).read_text(encoding="utf-8"))
    assert saved["history_id"] == row["id"] and saved["counts"]["ready_for_review"] == 1
    assert saved["telegram_sent"] is False


# ---------------------------------------------------------------------------
# Telegram: only when configured, Arabic, escaped
# ---------------------------------------------------------------------------

class FakeDb:
    def __init__(self, counts=None, fail=False):
        self.counts, self.fail, self.saved = counts, fail, []

    def run_outcome_counts(self, **kw):
        if self.fail:
            raise RuntimeError("db gone")
        return self.counts

    def db_available(self):
        return not self.fail

    def save_run_history(self, entry):
        self.saved.append(entry)
        return None if self.fail else 11


COUNTS = {"enqueued": 120, "searched": 118, "auto_published": 0, "ready_for_review": 90, "not_found": 20,
          "failed": 8, "provider_down": 2, "pending_left": 2}


def test_telegram_is_sent_only_when_configured(monkeypatch, tmp_path):
    import run_report

    sent = []
    info = {"stop_reason": None, "run_id": "r1", "started_ts": 1_790_000_000, "ended_ts": 1_790_004_320}
    path = str(tmp_path / "last_report.json")

    report = run_report.report_worker_run(info, trigger="nightly", db=FakeDb(COUNTS), sender=sent.append, path=path)
    assert sent == [] and report["telegram_sent"] is False          # not configured: nothing is sent

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:test")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "")
    run_report.report_worker_run(info, trigger="nightly", db=FakeDb(COUNTS), sender=sent.append, path=path)
    assert sent == []                                                 # a token without a chat is not configured

    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    report = run_report.report_worker_run(info, trigger="nightly", db=FakeDb(COUNTS),
                                          sender=lambda text: sent.append(text) or True, path=path)
    (text,) = sent
    assert report["telegram_sent"] is True
    assert "✅ اكتمل — التشغيل الليلي" in text and "بانتظار المراجعة 90" in text and "لم يُعثر على صورة 20" in text
    assert "المدة 1 س 12 د" in text and "بقي في الانتظار 2" in text


def test_another_run_taking_over_after_an_outage_is_not_a_night_that_did_not_start():
    """Review fix C7: attempts [db_unavailable, another_worker] gave 'skipped' («لم يبدأ»), exit 0 and no counts."""
    import run_report

    attempts = [{"stop_reason": "db_unavailable", "run_id": "r1", "worker_id": "host:1"},
                {"stop_reason": "another_worker"}]
    report = run_report.build_report("nightly", attempts, 1_790_000_000, 1_790_000_900, db=FakeDb(COUNTS),
                                     sheets=object())
    assert (report["outcome"], report["exit_code"], report["stop_reason"]) == ("handed_over", 0, "another_worker")
    assert report["attempts"] == 2 and report["attempt_reasons"] == ["db_unavailable"]
    assert report["run_ids"] == ["r1"] and report["counts"] == COUNTS
    assert report["reason_text"] == run_report.HANDED_OVER_TEXT
    text = run_report.telegram_text(report)
    assert "↪️ سلّم الطابور لتشغيل آخر" in text and "لم يبدأ" not in text and "بانتظار المراجعة 90" in text
    assert "المحاولات" not in text                     # one run, then the hand-over: nothing was re-run
    assert run_report.history_entry(report)["outcome"] == "handed_over"
    # a night that found another run from the start still did not start
    alone = run_report.build_report("nightly", [{"stop_reason": "another_worker"}], 1, 2, db=FakeDb(COUNTS))
    assert (alone["outcome"], alone["exit_code"], alone["counts"]) == ("skipped", 0, None)


def test_telegram_text_escapes_notices_and_says_when_the_database_is_down():
    import run_report

    report = run_report.build_report("dashboard", [{"stop_reason": "db_unavailable", "notice": "<b>x</b> & y"}],
                                     1_790_000_000, 1_790_000_060, db=FakeDb(fail=True))
    text = run_report.telegram_text(report)
    assert "&lt;b&gt;x&lt;/b&gt; &amp; y" in text and "<b>x</b>" not in text
    assert "🔌 انقطاع" in text and "قاعدة البيانات لا ترد" in text and "الأرقام غير متاحة" in text


def test_a_report_never_breaks_when_the_database_is_down(tmp_path):
    import run_report

    path = str(tmp_path / "nested" / "last_report.json")
    db = FakeDb(fail=True)
    report = run_report.report_worker_run({"stop_reason": None, "run_id": "r1"}, trigger="manual", db=db, path=path)
    assert report["counts"] is None and report["history_id"] is None and report["database"] == "unavailable"
    assert json.loads(Path(path).read_text(encoding="utf-8"))["outcome"] == "done"
    assert db.saved and db.saved[0]["run_trigger"] == "manual"


def test_spend_prefers_a_ledger_and_outbox_counts_are_optional():
    import run_report

    class Ledger(FakeDb):
        def get_run_spend(self, run_id):
            return {"usd": 0.25} if run_id == "r1" else 0.5

    class Sheets:
        since = []

        @staticmethod
        def outbox_summary(since_ts):
            Sheets.since.append(since_ts)          # only this run's writes, not every night's
            return {"PENDING": 3, "conflict": 1, "dead": 0, "other": "x"}

    report = run_report.build_report("nightly", [{"run_id": "r1"}, {"run_id": "r2", "stop_reason": None}],
                                     1, 2, health={"windows": {"7d": {"cost_usd": {"total": 9.0}}}},
                                     db=Ledger(COUNTS), sheets=Sheets)
    assert report["spend"] == {"usd": 0.75, "source": "ledger"}
    assert report["outbox"] == {"pending": 3, "conflict": 1, "dead": 0} and Sheets.since == [1]
    no_ledger = run_report.build_report("nightly", [{"run_id": "r1"}], 1, 2,
                                        health={"windows": {"7d": {"cost_usd": {"total": 9.0}}}},
                                        db=FakeDb(COUNTS), sheets=object())
    assert no_ledger["spend"] == {"usd": 9.0, "source": "estimate"} and no_ledger["outbox"] is None


def test_send_telegram_alert_reports_a_refused_message(monkeypatch, caplog):
    import requests

    import config

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:SECRET-TOKEN-VALUE")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    calls = []

    class Response:
        def __init__(self, code):
            self.status_code = code

    monkeypatch.setattr(requests, "post", lambda url, json, timeout: calls.append(json) or Response(400))
    assert config.send_telegram_alert("x") is False
    monkeypatch.setattr(requests, "post", lambda url, json, timeout: calls.append(json) or Response(200))
    assert config.send_telegram_alert("x") is True
    assert calls[0]["chat_id"] == "42" and calls[0]["parse_mode"] == "HTML"
    assert "SECRET-TOKEN-VALUE" not in caplog.text


# ---------------------------------------------------------------------------
# The worker reports every dashboard / manual run; the nightly reports once per night
# ---------------------------------------------------------------------------

def _crashing_worker(monkeypatch, tmp_path, raise_in):
    """run_worker_mode offline, with `raise_in` (init_async_queue | fetch_next_task) raising the given exception."""
    import config
    import google_sheets
    import local_cache_db
    import main

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "DAILY_BUDGET_USD", 0, raising=False)
    monkeypatch.setattr(main, "load_run_config", lambda: None)
    monkeypatch.setattr(main, "check_verifier", lambda: "")
    monkeypatch.setattr(main.time, "sleep", lambda s: None)
    monkeypatch.setattr(local_cache_db, "resume_automation", lambda: True)
    monkeypatch.setattr(local_cache_db, "get_automation_state", lambda: {"stop_requested": 0, "pause_requested": 0,
                                                                       "run_id": None})
    states, stops = [], []
    monkeypatch.setattr(local_cache_db, "update_automation_state", lambda *a, **k: states.append(k) or True)
    monkeypatch.setattr(local_cache_db, "stop_run", lambda worker_active=False: stops.append(worker_active))
    monkeypatch.setattr(local_cache_db, "get_ready_for_review_count", lambda: 0)
    monkeypatch.setattr(local_cache_db, "get_queue_statistics", lambda: {"total": 1, "completed": 0, "failed": 0,
                                                                         "ready_for_review": 0})
    monkeypatch.setattr(local_cache_db, "count_open_tasks", lambda: 1)
    monkeypatch.setattr(local_cache_db, "park_verifier_rechecks", lambda: 0)
    monkeypatch.setattr(local_cache_db, "requeue_verifier_down", lambda run_id: 0)
    monkeypatch.setattr(google_sheets, "get_sheets_client", lambda: object())
    monkeypatch.setattr(google_sheets, "open_worksheet", lambda client, name: object())
    monkeypatch.setattr(google_sheets, "find_link_column", lambda ws: 7)
    monkeypatch.setattr(google_sheets, "get_brand_mappings", lambda *a: {})
    monkeypatch.setattr(google_sheets, "stop_async_queue", lambda: None)
    monkeypatch.setattr(google_sheets, "init_async_queue", lambda *a: None)

    def boom(*args, **kwargs):
        raise raise_in[1]

    target = google_sheets if raise_in[0] == "init_async_queue" else local_cache_db
    monkeypatch.setattr(target, raise_in[0], boom)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:test")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    sent = []
    monkeypatch.setattr(config, "send_telegram_alert", lambda text: sent.append(text) or True)
    return main, states, stops, sent


def test_a_crashed_dashboard_worker_is_reported_as_failed_not_done(offline, monkeypatch, tmp_path):
    """Review fix C6: run_worker_mode's try had only a finally, so a worker that crashed (here init_async_queue raising
    RuntimeError) kept stop_reason None: last_report.json said done, exit 0, and Telegram said «اكتمل»."""
    main, states, _, sent = _crashing_worker(monkeypatch, tmp_path, ("init_async_queue", RuntimeError("queue thread")))

    with pytest.raises(RuntimeError):
        main.run_worker_mode(trigger="dashboard")

    assert main.LAST_WORKER["stop_reason"] == "worker_error"
    report = json.loads((tmp_path / "temp" / "nightly" / "last_report.json").read_text(encoding="utf-8"))
    assert (report["outcome"], report["exit_code"], report["trigger"]) == ("failed", 1, "dashboard")
    assert "RuntimeError: queue thread" in report["notices"][0] and report["notices"][0].startswith("WORKER_ERROR: ")
    (text,) = sent
    assert "❌ فشل" in text and "اكتمل" not in text
    assert states[-1]["status"] == "error" and states[-1]["notice"].startswith("WORKER_ERROR: ")
    assert not (tmp_path / "temp" / "pipeline.lock").exists()


def test_ctrl_c_stops_a_manual_worker_as_stopped(offline, monkeypatch, tmp_path):
    main, _, stops, sent = _crashing_worker(monkeypatch, tmp_path, ("fetch_next_task", KeyboardInterrupt()))

    with pytest.raises(KeyboardInterrupt):
        main.run_worker_mode(trigger="manual")

    assert main.LAST_WORKER["stop_reason"] == "stopped" and stops == [False]   # processing rows back to pending
    report = json.loads((tmp_path / "temp" / "nightly" / "last_report.json").read_text(encoding="utf-8"))
    assert (report["outcome"], report["exit_code"]) == ("stopped", 3)


def test_the_worker_reports_its_run_unless_the_nightly_does(offline, monkeypatch, tmp_path):
    import google_sheets
    import local_cache_db
    import main
    import run_report

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(main, "load_run_config", lambda: None)
    monkeypatch.setattr(main, "check_verifier", lambda: "")
    monkeypatch.setattr(local_cache_db, "resume_automation", lambda: True)
    monkeypatch.setattr(local_cache_db, "get_automation_state", lambda: {"stop_requested": 0, "run_id": "r1"})
    monkeypatch.setattr(local_cache_db, "update_automation_state", lambda *a, **k: True)
    monkeypatch.setattr(google_sheets, "get_sheets_client", lambda: None)
    reports = []
    monkeypatch.setattr(run_report, "report_worker_run", lambda info, trigger: reports.append((info, trigger)))

    main.run_worker_mode(trigger="dashboard")
    ((info, trigger),) = reports
    # no credentials (get_sheets_client None) is a setting since review fix C3 (it was sheets_unavailable, an outage)
    assert trigger == "dashboard" and info["stop_reason"] == "sheet_config" and info["run_id"] == "r1"

    main.run_worker_mode(trigger="nightly", report=False)
    assert len(reports) == 1


@pytest.mark.parametrize("failure, reason, code, notice", [
    ("tab", "sheet_config", 1, "SHEET_CONFIG: التبويب 'Products' غير موجود"),
    # review fix C3: open_worksheet returns None only for a sheet that is not found or not shared (a transient error
    # raises SheetTransientError), so it is a failure (exit 1), not an outage retried for 75 minutes
    ("none", "sheet_not_found", 1, "SHEET_CONFIG: sheet not found: My Sheet"),
    ("no_credentials", "sheet_config", 1, "SHEET_CONFIG: تعذر تحميل بيانات اعتماد Google من الملف «missing.json» "
                                          "(مفقود أو تالف)"),
    ("busy", "sheets_unavailable", 2, "SHEETS_UNAVAILABLE: 503 busy"),
    ("transient", "sheets_unavailable", 2, "SHEETS_UNAVAILABLE: Google Sheets غير متاح مؤقتاً"),
    ("bug", "worker_error", 1, "WORKER_ERROR: خطأ غير متوقع أوقف العامل (KeyError: 'link')؛ بقيت الصفوف المتبقية في "
                               "الانتظار"),
])
def test_a_sheet_the_worker_cannot_open_is_reported_with_its_reason(offline, monkeypatch, tmp_path, failure, reason,
                                                                    code, notice):
    """A wrong tab, missing credentials or a sheet that is not found / not shared is a setting (exit 1, no retry); only
    a Google that does not answer is an outage the nightly retries (exit 2); any other error is a worker error (exit 1).
    The report carries the detail the dashboard shows."""
    import config
    import google_sheets
    import local_cache_db
    import main
    import run_report

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(main, "load_run_config", lambda: None)
    monkeypatch.setattr(main, "check_verifier", lambda: "")
    monkeypatch.setattr(config, "SPREADSHEET_NAME_OR_URL", "My Sheet")
    monkeypatch.setattr(config, "CREDENTIALS_FILE", "missing.json")
    monkeypatch.setattr(local_cache_db, "resume_automation", lambda: True)
    monkeypatch.setattr(local_cache_db, "get_automation_state", lambda: {"stop_requested": 0, "run_id": None})
    states = []
    monkeypatch.setattr(local_cache_db, "update_automation_state", lambda *a, **k: states.append(k) or True)
    monkeypatch.setattr(local_cache_db, "get_ready_for_review_count", lambda: 0)
    monkeypatch.setattr(google_sheets, "get_sheets_client", lambda: None if failure == "no_credentials" else object())

    def open_worksheet(client, name):
        if failure == "tab":
            raise google_sheets.SheetConfigError("التبويب 'Products' غير موجود")
        if failure == "transient":
            raise google_sheets.SheetTransientError("Google Sheets غير متاح مؤقتاً")
        return None if failure == "none" else object()

    def find_link_column(ws):
        if failure == "bug":
            raise KeyError("link")
        raise ConnectionError("503 busy")

    monkeypatch.setattr(google_sheets, "open_worksheet", open_worksheet)
    monkeypatch.setattr(google_sheets, "find_link_column", find_link_column)

    main.run_worker_mode(report=False)

    assert main.LAST_WORKER["stop_reason"] == reason and run_report.exit_code(reason) == code
    assert main.LAST_WORKER["notice"] == notice and states[-1]["notice"] == notice


@pytest.mark.parametrize("case, reason", [
    ("bad_row_filter", "enqueue_failed"),
    # review fix C3: a missing / broken credentials file is a setting (gspread.service_account makes no network call)
    ("no_client", "sheet_config"),
    ("no_worksheet", "sheet_not_found"),
    ("schema", "sheet_config"),
    ("google_503", "sheets_unavailable"),
    ("transient", "sheets_unavailable"),
    ("requests_timeout", "sheets_unavailable"),
    ("requests_connection", "sheets_unavailable"),
    # review fix C3: every other exception was "sheets_unavailable" and retried twice over 75 minutes
    ("type_error", "enqueue_failed"),
    ("key_error", "enqueue_failed"),
    ("permission_error", "enqueue_failed"),
    ("db_down", "db_unavailable"),
    ("queue_bug", "enqueue_failed"),
])
def test_an_enqueue_failure_says_whether_it_is_an_outage(offline, monkeypatch, tmp_path, case, reason):
    """The nightly retries an enqueue that failed on an outage (main.LAST_ENQUEUE['reason']), not a setting or a bug;
    run_report.is_outage agrees."""
    import requests

    import run_report

    import config
    import google_sheets
    import local_cache_db
    import main

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(main, "load_run_config", lambda: None)
    monkeypatch.setattr(google_sheets, "clear_cache", lambda: None)
    monkeypatch.setattr(local_cache_db, "update_automation_state", lambda *a, **k: True)
    monkeypatch.setattr(config, "ROW_FILTER", "5-x" if case == "bad_row_filter" else "")
    monkeypatch.setattr(config, "BRAND_FILTER", "")
    monkeypatch.setattr(google_sheets, "get_sheets_client", lambda: None if case == "no_client" else object())
    monkeypatch.setattr(google_sheets, "open_worksheet", lambda c, n: None if case == "no_worksheet" else object())
    monkeypatch.setattr(google_sheets, "get_brand_mappings", lambda *a: {})

    errors = {"schema": google_sheets.SheetSchemaError("no product name column"),
              "google_503": ConnectionError("503 Service Unavailable"),
              "transient": google_sheets.SheetTransientError("Google Sheets غير متاح مؤقتاً"),
              "requests_timeout": requests.exceptions.ReadTimeout("read timed out"),
              "requests_connection": requests.exceptions.ConnectionError("connection reset"),
              "type_error": TypeError("'NoneType' object is not subscriptable"),
              "key_error": KeyError("product_name"),
              "permission_error": PermissionError(13, "Permission denied", "credentials.json")}

    def products(ws):
        if case in errors:
            raise errors[case]
        return [{"row_number": 5, "product_name": "Laban Up 180ml", "brand": "Al Rawabi", "barcode": ""}], 9

    def add(*a, **k):
        raise RuntimeError("queue write failed")

    monkeypatch.setattr(google_sheets, "get_products", products)
    monkeypatch.setattr(local_cache_db, "add_to_queue", add)
    monkeypatch.setattr(local_cache_db, "db_available", lambda: case != "db_down")
    main.LAST_ENQUEUE.clear()

    with pytest.raises(SystemExit) as exc:
        main.run_enqueue_mode()

    assert exc.value.code == 1 and main.LAST_ENQUEUE["reason"] == reason and main.LAST_ENQUEUE["message"]
    assert run_report.is_outage(reason) is (reason in ("sheets_unavailable", "db_unavailable"))


@pytest.mark.parametrize("argv, trigger", [
    (["main.py", "--worker"], "manual"),
    (["main.py", "--worker", "--trigger=dashboard"], "dashboard"),
    (["main.py", "--worker", "--trigger", "nightly"], "nightly"),
    (["main.py", "--worker", "--trigger=evil"], "manual"),
])
def test_the_trigger_comes_from_the_command_line(offline, argv, trigger):
    import main

    assert main._cli_trigger(argv) == trigger


def test_the_dashboard_starts_its_worker_as_a_dashboard_run():
    text = (ROOT / "dashboard" / "app" / "Http" / "Controllers" / "ApiController.php").read_text(encoding="utf-8")
    run_all = text[text.index("public function runAll("):text.index("private function pipelineProcess")]
    assert run_all.count("--worker --trigger=dashboard") == 2          # the Windows and the Linux command


# ---------------------------------------------------------------------------
# Item 6: each run keeps its cost (the queue rows keep only their last search)
# ---------------------------------------------------------------------------

def test_runs_cost_summary_per_window():
    import ops_health

    rows = [{"age_s": 3600, "spend_usd": 0.10, "spend_source": "estimate"},
            {"age_s": 3 * 86400, "spend_usd": "0.2000", "spend_source": "ledger"},
            {"age_s": 600, "spend_usd": None, "spend_source": None},          # a run whose cost was not saved
            {"age_s": 9 * 86400, "spend_usd": 5.0, "spend_source": "estimate"}]
    assert ops_health.runs_cost_summary(rows) == {
        "24h": {"usd": 0.1, "runs": 2, "priced_runs": 1, "estimated_runs": 1},
        "7d": {"usd": 0.3, "runs": 3, "priced_runs": 2, "estimated_runs": 1},
    }


def test_a_re_searched_row_keeps_the_cost_of_both_runs(db):
    """Run A searched row R (2 answered Serper queries, ~$0.002), then run B searched R again and overwrote its trace.
    The queue window now sees one search; run_history kept both runs' cost."""
    import ops_health
    import run_report

    def search(run_id, worker):
        outcome = {"decision": "REVIEW_PRESELECTED", "provider_health": [
            {"provider": "serper", "status": "ok"}, {"provider": "serper", "status": "ok"}], "vlm_calls": 0}
        _sql(db, "DELETE FROM automation_queue WHERE `row_number` = %s", (ROWS[0],))
        _sql(db, "INSERT INTO automation_queue (`row_number`, product_name, status, trace_json, run_id, worker_id) "
                 "VALUES (%s, 'R', 'ready_for_review', %s, %s, %s)",
             (ROWS[0], json.dumps({"outcome": outcome}), run_id, worker + "#c1"))
        health = ops_health.summarize(ops_health.load_rows(since_seconds=600, worker_id=worker))
        now = datetime.datetime.now().timestamp()
        info = {"stop_reason": None, "run_id": run_id, "worker_id": worker, "started_ts": now - 60, "ended_ts": now,
                "health": health}
        return run_report.report_worker_run(info, trigger="manual", path=os.devnull)

    first = search(RUN + "a", "p4ops-host:1")
    second = search(RUN + "b", "p4ops-host:2")
    assert first["spend"] == second["spend"] == {"usd": 0.002, "source": "estimate"}

    rows = ops_health.load_rows(since_seconds=600)
    assert [r for r in rows if r["status"] == "ready_for_review"]      # the queue holds R's second search only
    ours = [r for r in db.get_run_history(limit=50) if str(r["run_id"]).startswith(RUN)]
    assert len(ours) == 2 and sum(float(r["spend_usd"]) for r in ours) == pytest.approx(0.004)
    runs = ops_health.runs_cost()["24h"]
    assert runs["priced_runs"] >= 2 and runs["usd"] >= 0.004 - 1e-9
    assert ops_health.health_report()["runs_cost"]["24h"] == runs


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_health_page_shows_the_run_history_cost():
    js = (ROOT / "dashboard" / "public" / "js" / "health.js").read_text(encoding="utf-8")
    report = {"scanned": 3, "windows": {"24h": {"searches": 2, "serper_queries": 2, "verifier": {"calls": 0},
                                                "cost_usd": {"serper": 0.002, "gemini": 0, "total": 0.002}}},
              "prices": {"serper_per_query": 0.001, "gemini_per_call": 0.001},
              "runs_cost": {"24h": {"usd": 0.004, "runs": 2, "priced_runs": 2, "estimated_runs": 2}}}
    script = ("globalThis.window = globalThis;\n" + js + "\nconst H = window.LaqtaHealth;\n"
              f"console.log(JSON.stringify(H.opsView({json.dumps(report)}, '24h').cost));")
    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=60, encoding="utf-8")
    assert result.returncode == 0, result.stderr
    cost = json.loads(result.stdout.strip().splitlines()[-1])
    assert "حسب سجل التشغيلات" in cost["note"] and "تشغيلين" in cost["note"]


def test_the_real_ledger_and_outbox_give_this_runs_spend_and_unwritten_writes(mariadb_or_skip):
    """local_cache_db.run_spend (P4a's ledger) and google_sheets.outbox_summary (P1's outbox) as run_report reads them."""
    import time

    import google_sheets
    import run_report

    db = mariadb_or_skip
    conn = db.get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM search_spend WHERE run_id IN ('rr-ledger-1', 'rr-ledger-2')")
            cur.execute("INSERT INTO search_spend (day, run_id, provider, calls, usd) VALUES "
                        "(CURDATE(), 'rr-ledger-1', 'serper', 4, 0.004), (CURDATE(), 'rr-ledger-1', 'gemini', 2, 0.02)")
        conn.commit()
    finally:
        conn.close()
    assert abs(db.run_spend("rr-ledger-1") - 0.024) < 1e-9
    assert db.run_spend("rr-ledger-2") is None and db.run_spend("") is None
    assert run_report.ledger_spend(["rr-ledger-1", "rr-ledger-2"], db) == 0.024

    google_sheets._queue = None
    google_sheets.SQLiteTransactionQueue()          # creates / migrates sheet_updates in the test database
    conn = db.get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM sheet_updates WHERE `row_number` BETWEEN 990001 AND 990010")
            rows = [(990001, "PENDING", 0), (990002, "FAILED", 0), (990003, "CONFLICT", 0), (990004, "DEAD", 0),
                    (990005, "SYNCED", 0), (990006, "DEAD", 2 * 86400)]           # the last one is from 2 days ago
            for row, status, age in rows:
                cur.execute("INSERT INTO sheet_updates (`row_number`, `col_index`, `value`, sync_status, registered_at) "
                            "VALUES (%s, 0, 'x', %s, FROM_UNIXTIME(%s))", (row, status, int(time.time()) - age))
        conn.commit()
        since = int(time.time()) - 3600
        summary = google_sheets.outbox_summary(since)
        mine = {k: v for k, v in summary.items()}
        assert mine["dead"] >= 1 and mine["pending"] >= 2 and mine["conflict"] >= 1 and mine["written"] >= 1
        older = google_sheets.outbox_summary(None)
        assert older["dead"] >= mine["dead"] + 1              # the old DEAD write is not this run's
    finally:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM sheet_updates WHERE `row_number` BETWEEN 990001 AND 990010")
        conn.commit()
        conn.close()
