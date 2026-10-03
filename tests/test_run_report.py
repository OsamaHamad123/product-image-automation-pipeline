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
    ("db_unavailable", "outage", 2), ("sheets_unavailable", "outage", 2), ("sheet_not_found", "outage", 2),
    ("provider_down", "outage", 2),
    ("sheet_config", "failed", 1), ("enqueue_failed", "failed", 1), ("worker_error", "failed", 1),
    ("stopped", "stopped", 3), ("BUDGET_REACHED", "stopped", 3), ("SERPER_CREDIT", "stopped", 3),
    ("something_new", "stopped", 3),
])
def test_exit_codes_mean_what_they_say(reason, outcome, code):
    import run_report

    assert (run_report.outcome_of(reason), run_report.exit_code(reason)) == (outcome, code)
    if reason in ("BUDGET_REACHED", "something_new"):
        assert run_report.reason_text(reason) == reason


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
        @staticmethod
        def outbox_outcomes():
            return {"PENDING": 3, "conflict": 1, "dead": 0, "other": "x"}

    report = run_report.build_report("nightly", [{"run_id": "r1"}, {"run_id": "r2", "stop_reason": None}],
                                     1, 2, health={"windows": {"7d": {"cost_usd": {"total": 9.0}}}},
                                     db=Ledger(COUNTS), sheets=Sheets)
    assert report["spend"] == {"usd": 0.75, "source": "ledger"}
    assert report["outbox"] == {"pending": 3, "conflict": 1, "dead": 0}
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
    assert trigger == "dashboard" and info["stop_reason"] == "sheets_unavailable" and info["run_id"] == "r1"

    main.run_worker_mode(trigger="nightly", report=False)
    assert len(reports) == 1


@pytest.mark.parametrize("failure, reason, code, notice", [
    ("tab", "sheet_config", 1, "SHEET_CONFIG: التبويب 'Products' غير موجود"),
    ("none", "sheet_not_found", 2, "SHEET_CONFIG: sheet not found: My Sheet"),
    ("busy", "sheets_unavailable", 2, "SHEETS_UNAVAILABLE: 503 busy"),
])
def test_a_sheet_the_worker_cannot_open_is_reported_with_its_reason(offline, monkeypatch, tmp_path, failure, reason,
                                                                    code, notice):
    """A wrong tab is a setting (exit 1, no retry); a sheet Google does not open or answer is an outage the nightly
    retries (exit 2). The report carries the detail the dashboard shows."""
    import config
    import google_sheets
    import local_cache_db
    import main
    import run_report

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(main, "load_run_config", lambda: None)
    monkeypatch.setattr(main, "check_verifier", lambda: "")
    monkeypatch.setattr(config, "SPREADSHEET_NAME_OR_URL", "My Sheet")
    monkeypatch.setattr(local_cache_db, "resume_automation", lambda: True)
    monkeypatch.setattr(local_cache_db, "get_automation_state", lambda: {"stop_requested": 0, "run_id": None})
    states = []
    monkeypatch.setattr(local_cache_db, "update_automation_state", lambda *a, **k: states.append(k) or True)
    monkeypatch.setattr(google_sheets, "get_sheets_client", lambda: object())

    def open_worksheet(client, name):
        if failure == "tab":
            raise google_sheets.SheetConfigError("التبويب 'Products' غير موجود")
        return None if failure == "none" else object()

    def find_link_column(ws):
        raise ConnectionError("503 busy")

    monkeypatch.setattr(google_sheets, "open_worksheet", open_worksheet)
    monkeypatch.setattr(google_sheets, "find_link_column", find_link_column)

    main.run_worker_mode(report=False)

    assert main.LAST_WORKER["stop_reason"] == reason and run_report.exit_code(reason) == code
    assert main.LAST_WORKER["notice"] == notice and states[-1]["notice"] == notice


@pytest.mark.parametrize("case, reason", [
    ("bad_row_filter", "enqueue_failed"),
    ("no_client", "sheets_unavailable"),
    ("no_worksheet", "sheet_not_found"),
    ("schema", "sheet_config"),
    ("google_503", "sheets_unavailable"),
    ("db_down", "db_unavailable"),
    ("queue_bug", "enqueue_failed"),
])
def test_an_enqueue_failure_says_whether_it_is_an_outage(offline, monkeypatch, tmp_path, case, reason):
    """The nightly retries an enqueue that failed on an outage (main.LAST_ENQUEUE['reason']), not a setting."""
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

    def products(ws):
        if case == "schema":
            raise google_sheets.SheetSchemaError("no product name column")
        if case == "google_503":
            raise ConnectionError("503 Service Unavailable")
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
