"""The review screen's data for «why no pick» (the Laravel app through its HTTP kernel on the MariaDB test database
with a stub cli_bridge; skipped without php, dashboard/vendor or MariaDB). Each test failed before its change:

* /api/review/queue-state serves each review / not-found row's reason (outcome.explain from trace_json) and nothing
  else of the trace: only the reason's key (letters and '_'), its Arabic sentence and the sheet notes; a sibling row
  without a trace of its own shows its product's reason; the rows saved before reasons existed are counted;
* POST /api/review/explain-backfill asks the bridge to compute them (explain_backfill);
* the review page's config carries ?reason= (validated) and the backfill URL.
"""

import json
import re

import pytest

from laqta_kernel import NEEDS_LARAVEL, ROW, bridge_calls, kernel, sql, stub_env


# ---------------------------------------------------------------------------
# The review screen's data
# ---------------------------------------------------------------------------

REASON = {"v": 1, "key": "no_size", "label": "حجم ناقص بالشيت", "engine": "unsure", "fact": "الشيت ما فيه حجم",
          "action": "أضف الحجم في الشيت ثم أعد البحث.", "text": "الشيت ما فيه حجم. أضف الحجم في الشيت ثم أعد البحث.",
          "sheet": [{"key": "no_size", "text": "الحجم ناقص بالشيت", "evil": "<script>"}, {"key": "bad key!", "text": "x"}],
          "source": "search"}


@pytest.fixture
def queue(mariadb_or_skip):
    db = mariadb_or_skip
    sql(db, "DELETE FROM automation_queue")
    secret = "W2D-PROVIDER-SECRET-91c2"
    outcome = {"decision": "REVIEW_UNSELECTED", "explain": REASON,
               "provider_health": [{"provider": "serper", "status": "error", "error": f"api_key={secret}"}]}
    rows = [
        (0, "ready_for_review", None, {"outcome": outcome}, "w2d-k0"),
        (1, "ready_for_review", None, None, "w2d-k0"),                                    # sibling, no trace
        (2, "failed", "NO_RESULTS", {"outcome": {"decision": "NOT_FOUND", "explain": dict(REASON, key="Not-Valid")}}, "w2d-k2"),
        (3, "ready_for_review", None, {"outcome": {"decision": "REVIEW_UNSELECTED"}}, "w2d-k3"),  # saved before
        (4, "failed", "NO_RESULTS", {"outcome": {"decision": "NOT_FOUND"}}, "w2d-k4"),           # saved before
        (5, "pending", "PROVIDER_DOWN", {"outcome": {"decision": "PROVIDER_DOWN"}}, "w2d-k5"),     # not counted
        (6, "ready_for_review", None, {"outcome": {"decision": "REVIEW_PRESELECTED", "explain": None}}, "w2d-k6"),
    ]
    for i, status, code, trace, sku in rows:
        sql(db, "INSERT INTO automation_queue (`row_number`, product_name, brand, status, failure_code, trace_json, "
                 "sku_key) VALUES (%s, %s, 'Brand', %s, %s, %s, %s)",
             (ROW + i, f"W2D Product {i}", status, code, None if trace is None else json.dumps(trace), sku))
    yield db, secret
    sql(db, "DELETE FROM automation_queue")


@NEEDS_LARAVEL
def test_queue_state_serves_each_rows_reason_and_nothing_else_of_the_trace(queue, tmp_path):
    db, secret = queue
    env, calls = stub_env(tmp_path)
    state, backfill, catalog, bogus = kernel(env, [
        ["GET", "/api/review/queue-state"], ["POST", "/api/review/explain-backfill"],
        ["GET", "/catalog?reason=no_size"], ["GET", "/catalog?reason=../x"]])
    body = json.loads(state["body"])
    by_row = {r["row_number"] - ROW: r for r in body["rows"]}
    assert by_row[0]["explain"] == {"key": "no_size", "label": "حجم ناقص بالشيت", "engine": "unsure",
                                    "text": REASON["text"], "fact": REASON["fact"], "action": REASON["action"],
                                    "sheet": [{"key": "no_size", "text": "الحجم ناقص بالشيت", "known": False}]}
    assert by_row[1]["explain"] == by_row[0]["explain"]        # the sibling shows its product's reason
    assert by_row[2]["explain"] is None                        # an invalid key never reaches the page
    assert by_row[3]["explain"] is None and by_row[5]["explain"] is None and by_row[6]["explain"] is None
    assert body["explain_missing"] == 2                        # rows 3 and 4: saved before reasons existed
    assert secret not in state["body"] and "provider_health" not in state["body"] and "<script>" not in state["body"]
    # the backfill: one bridge call, its counts
    assert backfill["status"] == 200 and json.loads(backfill["body"]) == {"status": "success", "filled": 4, "checked": 5}
    assert [c[0] for c in bridge_calls(calls)] == ["explain_backfill"]
    # the page's config: the reason deep link (validated) and the backfill URL
    cfg = _config_of(catalog["body"])
    assert cfg["reason"] == "no_size" and cfg["urls"]["explainBackfill"].endswith("/api/review/explain-backfill")
    assert _config_of(bogus["body"])["reason"] is None


def _config_of(body):
    import html

    m = re.search(r'id="rvApp"[^>]*data-config="([^"]+)"', body)
    assert m, body[:2000]
    return json.loads(html.unescape(m.group(1)))
