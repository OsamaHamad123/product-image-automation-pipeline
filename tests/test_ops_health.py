"""ops_health: the search health and cost panel of the diagnostics page and the worker's outage notice.

The aggregation is a pure function over automation_queue rows, tested here with hand-made rows (including
malformed JSON and missing keys) and with outcomes built by catalog_match.facade itself. The loader runs
against MariaDB; the cli_bridge action and main.py's end-of-run notice are exercised offline.
"""

import base64
import json
from datetime import datetime, timedelta, timezone

import pytest

import ops_health

HOUR = 3600
DAY = 24 * HOUR
NOW = datetime(2026, 9, 30, 23, 0, tzinfo=timezone.utc)
SERPER_ALERT = "رصيد Serper انتهى أو المفتاح مرفوض"
GEMINI_ALERT = "Gemini لا يستجيب"


def health(provider, status, http=200, query_id="Q1"):
    return {"provider": provider, "status": status, "http_status": http, "latency_ms": 420,
            "error": None if status in ("ok", "empty") else f"http_{http}", "query_id": query_id}


def outcome(decision, failure_code=None, providers=(), vlm_calls=0, **extra):
    """The trace['outcome'] shape written by catalog_match.facade.outcome_summary."""
    doc = {"decision": decision, "failure_code": failure_code, "provider_health": list(providers),
           "queries": ["AIDA FRENCH FRIES 1KG"], "sku_key": "aida|french fries|1000g", "vlm_calls": vlm_calls,
           "reject_counts": {}, "winner_url": None}
    doc.update(extra)
    return doc


def row(age_s, out=None, status="ready_for_review", failure_code=None, raw=None, has_trace=True):
    """One automation_queue row as ops_health.load_rows returns it."""
    return {"status": status, "failure_code": failure_code, "has_trace": 1 if has_trace else 0, "age_s": age_s,
            "outcome_json": raw if raw is not None else (json.dumps(out, ensure_ascii=False) if out else None)}


SERPER_OK = [health("serper", "ok", query_id="Q1"), health("serper", "ok", query_id="Q2"),
             health("off", "empty", query_id="lookup")]
SERPER_QUOTA = [health("serper", "quota", 400, "Q1"), health("serper", "quota", 400, "Q2")]


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def test_windows_count_decisions_providers_verifier_and_cost():
    rows = [
        row(10 * 60, outcome("REVIEW_PRESELECTED", providers=SERPER_OK, vlm_calls=1)),
        row(2 * HOUR, outcome("REVIEW_UNSELECTED", "VERIFIER_DOWN", providers=SERPER_OK, vlm_calls=2),
            failure_code="VERIFIER_DOWN"),
        row(5 * HOUR, outcome("NOT_FOUND", "NO_RESULTS", providers=[health("serper", "empty", query_id="Q1"),
                                                                   health("serper", "error", 500, "Q2"),
                                                                   health("bing_html", "blocked", 200, "Q2")]),
            status="failed", failure_code="NO_RESULTS"),
        row(3 * DAY, outcome("AUTO_PUBLISH", providers=SERPER_OK, vlm_calls=1), status="completed"),
        row(6 * DAY, outcome("NOT_FOUND", "ALL_CONFLICTED", providers=SERPER_OK), status="failed",
            failure_code="ALL_CONFLICTED"),
    ]
    report = ops_health.summarize(rows, now=NOW)

    day = report["windows"]["24h"]
    assert day["searches"] == 3 and day["unreadable"] == 0
    assert day["decisions"] == {"NOT_FOUND": 1, "REVIEW_PRESELECTED": 1, "REVIEW_UNSELECTED": 1}
    assert day["providers"]["serper"] == {"ok": 4, "empty": 1, "error": 1, "quota": 0, "blocked": 0}
    assert day["providers"]["bing_html"]["blocked"] == 1
    assert day["providers"]["off"]["empty"] == 2
    # billable Serper queries are the ones Serper answered (ok / empty), not the errors
    assert day["serper_queries"] == 5
    assert day["verifier"] == {"calls": 3, "searches": 2, "down": 1}
    assert day["cost_usd"] == {"serper": 0.005, "gemini": 0.003, "total": 0.008}
    assert day["failure_codes"] == [{"code": "NO_RESULTS", "count": 1}, {"code": "VERIFIER_DOWN", "count": 1}]

    week = report["windows"]["7d"]
    assert week["searches"] == 5
    assert week["decisions"]["AUTO_PUBLISH"] == 1 and week["decisions"]["NOT_FOUND"] == 2
    assert week["serper_queries"] == 9 and week["verifier"]["calls"] == 4
    assert week["cost_usd"]["total"] == round(9 * 0.001 + 4 * 0.001, 4)
    assert [c["code"] for c in week["failure_codes"]] == ["ALL_CONFLICTED", "NO_RESULTS", "VERIFIER_DOWN"]

    assert report["scanned"] == 5 and report["truncated"] is False
    assert report["latest_age_s"] == 600
    assert report["alerts"] == []
    assert report["prices"] == {"serper_per_query": 0.001, "gemini_per_call": 0.001}


def test_outcomes_written_by_the_facade_are_read():
    """The panel reads what catalog_match.facade really writes (dataclass health entries serialised)."""
    from catalog_match import facade
    from catalog_match.models import ProviderHealth, SearchOutcome

    out = SearchOutcome(decision="REVIEW_UNSELECTED", failure_code="VERIFIER_DOWN", vlm_calls=1,
                        sku_key="sku", queries=["q1", "q2"],
                        provider_health=[ProviderHealth("serper", "ok", 200, 300, None, "Q1"),
                                         ProviderHealth("serper", "quota", 429, 50, "http_429", "Q2")])
    trace = {}
    facade.outcome_to_legacy(out, trace)
    # main.py stores trace={'outcome': ...}; MariaDB's JSON_EXTRACT returns the outcome object as JSON text
    stored = json.loads(json.dumps(trace, default=str))["outcome"]
    report = ops_health.summarize([row(60, stored)], now=NOW)
    day = report["windows"]["24h"]
    assert day["providers"]["serper"]["ok"] == 1 and day["providers"]["serper"]["quota"] == 1
    assert day["verifier"] == {"calls": 1, "searches": 1, "down": 1}
    assert day["decisions"] == {"REVIEW_UNSELECTED": 1}


def test_malformed_json_and_missing_keys_are_counted_never_invented():
    rows = [
        row(60, raw="{not json"),                            # corrupt trace (JSON_EXTRACT would give NULL)
        row(70, raw="[1, 2]"),                               # not an object
        row(80, raw="null"),
        row(90, raw='"text"'),
        row(95, raw=b'{"decision": "NOT_FOUND"}'),           # bytes from the driver, no other keys
        row(100, outcome("REVIEW_UNSELECTED", providers=["serper", None, {"provider": "serper"},
                                                         {"provider": "serper", "status": "teapot"}],
                         vlm_calls="two")),
        row(110, {}),                                        # empty outcome: nothing to read
        row(120, None, status="failed", failure_code="WORKER_ERROR", has_trace=False),   # no trace at all
        row(None, outcome("AUTO_PUBLISH")),                  # no age: cannot be placed in a window
        "not a row",
        {"outcome_json": json.dumps(outcome("NOT_FOUND")), "age_s": "abc"},
    ]
    report = ops_health.summarize(rows, now=NOW)
    day = report["windows"]["24h"]

    assert report["scanned"] == len(rows)
    assert day["searches"] == 2
    # corrupt / non-object / empty outcomes with a stored trace; the WORKER_ERROR row had no trace
    assert day["unreadable"] == 5
    assert day["decisions"] == {"NOT_FOUND": 1, "REVIEW_UNSELECTED": 1}
    # a health entry without a status is not a known answer; unknown statuses land in 'other'
    assert day["providers"] == {"serper": {"ok": 0, "empty": 0, "error": 0, "quota": 0, "blocked": 0, "other": 2}}
    assert day["serper_queries"] == 0
    assert day["verifier"] == {"calls": 0, "searches": 0, "down": 0}
    assert day["cost_usd"]["total"] == 0
    assert day["failure_codes"] == [{"code": "WORKER_ERROR", "count": 1}]
    assert report["alerts"] == []


def test_empty_queue_gives_an_empty_report():
    report = ops_health.summarize([], now=NOW)
    assert report["scanned"] == 0 and report["latest_age_s"] is None and report["alerts"] == []
    for window in report["windows"].values():
        assert window["searches"] == 0 and window["decisions"] == {} and window["providers"] == {}
        assert window["cost_usd"] == {"serper": 0, "gemini": 0, "total": 0}
        assert window["failure_codes"] == []


def test_searched_at_wins_over_the_row_update_time():
    """Re-enqueueing re-dates review rows (updated_at) without a new search; a searched_at timestamp is exact."""
    old = (NOW - timedelta(days=3)).isoformat()
    rows = [
        row(60, outcome("REVIEW_PRESELECTED", providers=SERPER_OK, searched_at=old)),
        row(120, outcome("REVIEW_UNSELECTED", providers=SERPER_OK, searched_at="2026-09-27T10:00:00")),  # naive
        row(180, outcome("NOT_FOUND", providers=SERPER_OK, searched_at="yesterday")),
    ]
    report = ops_health.summarize(rows, now=NOW)
    assert report["windows"]["24h"]["decisions"] == {"NOT_FOUND": 1, "REVIEW_UNSELECTED": 1}
    assert report["windows"]["7d"]["searches"] == 3
    assert report["timed_by_row_update"] == 2


def test_truncation_is_reported():
    rows = [row(i, outcome("NOT_FOUND", "NO_RESULTS")) for i in range(1, 4)]
    assert ops_health.summarize(rows, now=NOW, limit=3)["truncated"] is True
    assert ops_health.summarize(rows, now=NOW, limit=4)["truncated"] is False


# ---------------------------------------------------------------------------
# Alert rules
# ---------------------------------------------------------------------------

def _codes(rows):
    return [a["code"] for a in ops_health.summarize(rows, now=NOW)["alerts"]]


def test_serper_credit_alert_after_consecutive_refused_searches():
    rows = [
        row(60, outcome("PROVIDER_DOWN", "PROVIDER_DOWN", providers=SERPER_QUOTA), status="pending",
            failure_code="PROVIDER_DOWN"),
        # a rejected key (401) counts like spent credit; the Bing fallback answering does not make Serper healthy
        row(120, outcome("REVIEW_UNSELECTED", providers=[health("serper", "error", 401, "Q1"),
                                                          health("bing_html", "ok", 200, "Q1")])),
        row(3 * HOUR, outcome("REVIEW_PRESELECTED", providers=SERPER_OK, vlm_calls=1)),
    ]
    report = ops_health.summarize(rows, now=NOW)
    (alert,) = report["alerts"]
    assert alert["code"] == "SERPER_CREDIT" and alert["message"] == SERPER_ALERT and alert["searches"] == 2
    assert "Serper" in alert["detail"]


def test_serper_alert_clears_once_serper_answers_again():
    rows = [row(60, outcome("REVIEW_PRESELECTED", providers=SERPER_OK, vlm_calls=1))]
    rows += [row(120 + i, outcome("PROVIDER_DOWN", "PROVIDER_DOWN", providers=SERPER_QUOTA)) for i in range(5)]
    assert _codes(rows) == []


def test_one_refused_search_or_plain_errors_do_not_alert():
    one = [row(60, outcome("PROVIDER_DOWN", "PROVIDER_DOWN", providers=SERPER_QUOTA)),
           row(120, outcome("REVIEW_PRESELECTED", providers=SERPER_OK, vlm_calls=1))]
    assert _codes(one) == []
    timeouts = [row(60 + i, outcome("PROVIDER_DOWN", "PROVIDER_DOWN",
                                    providers=[health("serper", "error", None, "Q1")])) for i in range(4)]
    assert _codes(timeouts) == []


def test_old_outages_do_not_alert():
    rows = [row(2 * DAY + i, outcome("PROVIDER_DOWN", "PROVIDER_DOWN", providers=SERPER_QUOTA)) for i in range(3)]
    assert _codes(rows) == []
    assert ops_health.summarize(rows, now=NOW)["windows"]["7d"]["providers"]["serper"]["quota"] == 6


def test_gemini_alert_when_every_recent_verification_failed():
    rows = [
        row(60, outcome("REVIEW_PRESELECTED", "VERIFIER_DOWN", providers=SERPER_OK, vlm_calls=0)),   # breaker open
        row(90, outcome("NOT_FOUND", "NO_RESULTS", providers=SERPER_OK)),       # did not need the verifier
        row(120, outcome("REVIEW_UNSELECTED", "VERIFIER_DOWN", providers=SERPER_OK, vlm_calls=1)),   # timeout
        row(4 * HOUR, outcome("REVIEW_PRESELECTED", providers=SERPER_OK, vlm_calls=1)),
    ]
    report = ops_health.summarize(rows, now=NOW)
    (alert,) = report["alerts"]
    assert alert["code"] == "GEMINI_DOWN" and alert["message"] == GEMINI_ALERT and alert["searches"] == 2


def test_gemini_alert_needs_the_latest_verification_to_fail():
    rows = [
        row(60, outcome("REVIEW_PRESELECTED", providers=SERPER_OK, vlm_calls=1)),
        row(120, outcome("REVIEW_UNSELECTED", "VERIFIER_DOWN", providers=SERPER_OK, vlm_calls=1)),
        row(180, outcome("REVIEW_UNSELECTED", "VERIFIER_DOWN", providers=SERPER_OK, vlm_calls=1)),
    ]
    assert _codes(rows) == []


def test_alerts_use_the_newest_rows_whatever_the_input_order():
    rows = [
        row(5 * HOUR, outcome("REVIEW_PRESELECTED", providers=SERPER_OK, vlm_calls=1)),
        row(60, outcome("PROVIDER_DOWN", "PROVIDER_DOWN", providers=SERPER_QUOTA)),
        row(30, outcome("PROVIDER_DOWN", "PROVIDER_DOWN", providers=SERPER_QUOTA)),
    ]
    assert _codes(rows) == ["SERPER_CREDIT"]


def test_both_outages_are_reported_together():
    rows = [row(60 + i, outcome("REVIEW_UNSELECTED", "VERIFIER_DOWN", vlm_calls=1,
                                providers=[health("serper", "quota", 429, "Q1"), health("bing_html", "ok")]))
            for i in range(3)]
    assert _codes(rows) == ["SERPER_CREDIT", "GEMINI_DOWN"]


def test_outage_notice_reads_only_this_workers_rows(monkeypatch):
    seen = {}

    def fake_load(since_seconds=None, limit=None, worker_id=None):
        seen.update(since_seconds=since_seconds, worker_id=worker_id)
        return [row(30 + i, outcome("PROVIDER_DOWN", "PROVIDER_DOWN", providers=SERPER_QUOTA)) for i in range(2)]

    monkeypatch.setattr(ops_health, "load_rows", fake_load)
    assert ops_health.outage_notice(900, worker_id="SHOP-PC:4242") == f"SERPER_CREDIT: {SERPER_ALERT}"
    assert seen == {"since_seconds": 900, "worker_id": "SHOP-PC:4242"}

    monkeypatch.setattr(ops_health, "load_rows", lambda **k: [row(30, outcome("REVIEW_PRESELECTED",
                                                                              providers=SERPER_OK))])
    assert ops_health.outage_notice(900) == ""


# ---------------------------------------------------------------------------
# Loader against MariaDB
# ---------------------------------------------------------------------------

LIVE_ROWS = json.load(open(__import__("pathlib").Path(__file__).parent / "catalog_match" / "fixtures"
                           / "live_rows_2026_09_30.json", encoding="utf-8"))["rows"]
DB_ROW_BASE = 930000


@pytest.fixture
def queue(mariadb_or_skip):
    db = mariadb_or_skip
    db.clear_queue()           # the loader reads the whole table; the test database is ours alone
    yield db
    db.clear_queue()


def _insert(db, row_number, *, trace_json, failure_code=None, status="ready_for_review", worker_id=None,
            days_ago=0):
    live = LIVE_ROWS[row_number % len(LIVE_ROWS)]
    conn = db.get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO automation_queue (`row_number`, product_name, brand, status, failure_code, trace_json, "
            "worker_id, updated_at) VALUES (%s, %s, %s, %s, %s, %s, %s, NOW() - INTERVAL %s DAY)",
            (DB_ROW_BASE + row_number, live["name"], live["brand"], status, failure_code, trace_json, worker_id,
             days_ago))
        conn.commit()
    finally:
        conn.close()


def test_load_rows_extracts_outcomes_in_the_database(queue):
    db = queue
    full_trace = {"outcome": outcome("NOT_FOUND", "ALL_CONFLICTED", providers=SERPER_OK),
                  "steps": [{"step_name": "catalog_match v2", "candidates": [{"url": f"https://x.ae/{i}.jpg",
                                                                              "evidence": {"tier": 3}}
                                                                             for i in range(40)]}]}
    _insert(db, 1, trace_json=json.dumps(full_trace), failure_code="ALL_CONFLICTED", status="failed",
            worker_id="SHOP_PC:11#aaaa")
    _insert(db, 2, trace_json=json.dumps({"outcome": outcome("PROVIDER_DOWN", "PROVIDER_DOWN",
                                                             providers=SERPER_QUOTA)}),
            failure_code="PROVIDER_DOWN", status="pending", worker_id="SHOPXPC:11#bbbb")
    _insert(db, 3, trace_json="{truncated trace", worker_id="SHOP_PC:11#cccc")
    _insert(db, 4, trace_json=None, failure_code="WORKER_ERROR", status="failed")
    _insert(db, 5, trace_json=json.dumps({"outcome": outcome("REVIEW_PRESELECTED", providers=SERPER_OK,
                                                             vlm_calls=1)}), days_ago=10)
    _insert(db, 6, trace_json=None)                      # queued, never searched: not a search, not a failure

    rows = ops_health.load_rows()
    assert len(rows) == 4
    by_code = {r["failure_code"]: r for r in rows}
    extracted = json.loads(by_code["ALL_CONFLICTED"]["outcome_json"])
    assert extracted["decision"] == "NOT_FOUND" and "steps" not in extracted     # only the outcome travels
    assert by_code["WORKER_ERROR"]["has_trace"] == 0
    corrupt = next(r for r in rows if r["failure_code"] is None)
    assert corrupt["outcome_json"] is None and corrupt["has_trace"] == 1
    assert all(0 <= r["age_s"] < 600 for r in rows)

    report = ops_health.summarize(rows)
    day = report["windows"]["24h"]
    assert day["searches"] == 2 and day["unreadable"] == 1
    assert day["decisions"] == {"NOT_FOUND": 1, "PROVIDER_DOWN": 1}
    assert {c["code"] for c in day["failure_codes"]} == {"ALL_CONFLICTED", "PROVIDER_DOWN", "WORKER_ERROR"}

    # this worker only: 'SHOP_PC:11' must not match 'SHOPXPC:11' ('_' is a LIKE wildcard)
    mine = ops_health.load_rows(since_seconds=3600, worker_id="SHOP_PC:11")
    assert sorted(r["failure_code"] or "" for r in mine) == ["", "ALL_CONFLICTED"]
    assert len(ops_health.load_rows(limit=2)) == 2


def test_worker_written_trace_is_read_back(queue):
    """The row path main.py uses: add_to_queue -> fetch_next_task -> update_task_status(trace=...)."""
    db = queue
    live = LIVE_ROWS[0]
    db.add_to_queue(DB_ROW_BASE + 50, "", live["name"], live["brand"], f"{live['name']} {live['brand']}",
                    payload={}, sku_key="live-row-2")
    task = db.fetch_next_task("SHOP-PC:77")
    stored = outcome("REVIEW_UNSELECTED", "VERIFIER_DOWN", providers=SERPER_QUOTA, vlm_calls=1)
    assert db.update_task_status(task["id"], "ready_for_review", failure_code="VERIFIER_DOWN",
                                 trace={"outcome": stored}, claim_id=task["worker_id"])
    (loaded,) = ops_health.load_rows(since_seconds=600, worker_id="SHOP-PC:77")
    assert json.loads(loaded["outcome_json"]) == stored
    assert ops_health.load_rows(since_seconds=600, worker_id="SHOP-PC:78") == []


# ---------------------------------------------------------------------------
# cli_bridge action
# ---------------------------------------------------------------------------

@pytest.fixture
def bridge(offline, monkeypatch, tmp_path):
    import cli_bridge

    monkeypatch.setattr(cli_bridge, "LOG_PATH", str(tmp_path / "search.log"))
    return cli_bridge


def _call(cli_bridge, capsys, action="ops_health", params=None):
    arg = base64.b64encode(json.dumps(params or {}).encode("utf-8")).decode("ascii")
    code = cli_bridge.main(["cli_bridge.py", action, arg])
    lines = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert len(lines) == 1, lines
    return code, json.loads(lines[0])


def test_bridge_action_returns_the_report(bridge, monkeypatch, capsys):
    rows = [row(60 + i, outcome("REVIEW_UNSELECTED", "VERIFIER_DOWN", providers=SERPER_QUOTA, vlm_calls=1))
            for i in range(2)]
    monkeypatch.setattr(ops_health, "load_rows", lambda **k: rows)
    code, response = _call(bridge, capsys)
    assert code == 0 and response["status"] == "success"
    assert response["windows"]["24h"]["searches"] == 2
    assert [a["message"] for a in response["alerts"]] == [SERPER_ALERT, GEMINI_ALERT]


def test_bridge_action_hides_database_errors(bridge, monkeypatch, capsys):
    import pymysql

    def broken(**kwargs):
        raise pymysql.err.OperationalError(1045, "Access denied for user 'root'@'localhost' (using password: YES)")

    monkeypatch.setattr(ops_health, "load_rows", broken)
    code, response = _call(bridge, capsys)
    assert response == {"status": "failed",
                        "error": "Could not read the automation queue (details in temp/search.log)."}


def test_bridge_action_is_last_in_the_table():
    import cli_bridge

    # ops_health was appended to the table; the run_control action (run start / stop / reset) was appended after it
    assert list(cli_bridge.ACTIONS)[-2:] == ["ops_health", "run_control"]
    assert cli_bridge.ACTIONS["ops_health"] is cli_bridge.action_ops_health


# ---------------------------------------------------------------------------
# main.py: the outage cause reaches automation_state.notice at the end of a worker run
# ---------------------------------------------------------------------------

@pytest.fixture
def worker_run(offline, monkeypatch, tmp_path):
    """run_worker_mode with the sheet, the queue and the search replaced by recorders."""
    import time as time_mod

    import google_sheets
    import local_cache_db
    import main

    monkeypatch.chdir(tmp_path)
    real_sleep = time_mod.sleep
    monkeypatch.setattr(time_mod, "sleep", lambda s: real_sleep(0.005))
    rec = {"states": [], "claims": [], "loads": [], "tasks": [], "results": [], "ready": 0}
    monkeypatch.setattr(main, "_another_worker_running", lambda lock: False)
    monkeypatch.setattr(main, "load_run_config", lambda: None)
    monkeypatch.setattr(main, "check_verifier", lambda: "")
    monkeypatch.setattr(local_cache_db, "resume_automation", lambda: True)
    monkeypatch.setattr(google_sheets, "get_sheets_client", lambda: object())
    monkeypatch.setattr(google_sheets, "open_worksheet", lambda client, name: object())
    monkeypatch.setattr(google_sheets, "find_link_column", lambda ws: 7)
    monkeypatch.setattr(google_sheets, "get_brand_mappings", lambda *a: {})
    monkeypatch.setattr(google_sheets, "init_async_queue", lambda *a: None)
    monkeypatch.setattr(google_sheets, "stop_async_queue", lambda: None)
    monkeypatch.setattr(local_cache_db, "update_automation_state",
                        lambda status, **kw: rec["states"].append(dict(kw, status=status)) or True)
    monkeypatch.setattr(local_cache_db, "get_queue_statistics",
                        lambda: {"total": 6, "completed": 0, "failed": 0, "ready_for_review": 0, "pending": 6})
    monkeypatch.setattr(local_cache_db, "get_automation_state", lambda: {"pause_requested": 0})

    def fetch(worker_id):
        rec["claims"].append(worker_id)
        return rec["tasks"].pop(0) if rec["tasks"] else None

    monkeypatch.setattr(local_cache_db, "fetch_next_task", fetch)
    monkeypatch.setattr(local_cache_db, "count_open_tasks", lambda: len(rec["tasks"]))
    monkeypatch.setattr(local_cache_db, "get_ready_for_review_count", lambda: rec["ready"])
    monkeypatch.setattr(main, "pre_cache_product_candidates",
                        lambda task, *a, **k: rec["results"][task["id"] % len(rec["results"])])

    def run(result, rows, n_tasks=6, ready=0, load_error=None):
        rec["tasks"] = [{"id": i, "row_number": 100 + i, "product_name": LIVE_ROWS[i]["name"]}
                        for i in range(n_tasks)]
        rec["results"], rec["ready"] = [result], ready

        def load(since_seconds=None, limit=None, worker_id=None):
            rec["loads"].append({"since_seconds": since_seconds, "worker_id": worker_id})
            if load_error:
                raise load_error
            return rows

        monkeypatch.setattr(ops_health, "load_rows", load)
        main.run_worker_mode()
        return rec

    return run


def test_provider_down_stop_names_the_serper_credit_cause(worker_run):
    rows = [row(10 + i, outcome("PROVIDER_DOWN", "PROVIDER_DOWN", providers=SERPER_QUOTA)) for i in range(5)]
    rec = worker_run("provider_down", rows)
    final = rec["states"][-1]
    assert final["status"] == "provider_down"
    assert final["notice"] == ("PROVIDER_DOWN: search providers unavailable; remaining rows stay pending | "
                               f"SERPER_CREDIT: {SERPER_ALERT}")
    # only the rows this worker claimed, and only for the time it ran
    (load,) = rec["loads"]
    assert load["worker_id"] == rec["claims"][0] and "#" not in load["worker_id"]
    assert 0 < load["since_seconds"] < 600


def test_finished_run_reports_a_silent_gemini_outage(worker_run):
    rows = [row(10 + i, outcome("REVIEW_UNSELECTED", "VERIFIER_DOWN", providers=SERPER_OK, vlm_calls=1))
            for i in range(3)]
    rec = worker_run("success", rows, n_tasks=3, ready=3)
    final = rec["states"][-1]
    assert final["status"] == "curation_pending"
    assert final["notice"] == f"GEMINI_DOWN: {GEMINI_ALERT}"


def test_healthy_run_leaves_the_notice_alone(worker_run):
    rows = [row(10, outcome("REVIEW_PRESELECTED", providers=SERPER_OK, vlm_calls=1))]
    rec = worker_run("success", rows, n_tasks=2, ready=0)
    final = rec["states"][-1]
    assert final["status"] == "idle" and final.get("notice") is None


def test_a_failing_health_check_never_blocks_the_final_state(worker_run):
    rec = worker_run("success", [], n_tasks=1, ready=1, load_error=RuntimeError("db gone"))
    final = rec["states"][-1]
    assert final["status"] == "curation_pending" and final.get("notice") is None
