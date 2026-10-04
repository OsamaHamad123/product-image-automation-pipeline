"""«Why no pick» where the data is stored (real MariaDB, the test database; skipped when MariaDB is down).

Each test failed before its change:

* the worker saved no reason: a product left without a pick now has automation_queue.trace_json -> outcome.explain,
  written on the worker's own path (search -> facade -> pre_cache_product_candidates -> update_task_status);
* rows saved before that get their reason from what is stored (cli_bridge 'explain_backfill': no search, no cost),
  without changing their status or updated_at (the review page's snapshot compares it), never over a newer result,
  never on a sibling row without a trace, and only once (a row with a pick is marked so);
* a reject with a new search replaces the row's reason with the new search's.
"""

import json
from pathlib import Path

import pytest

from no_pick_fixtures import MAPPINGS, listing, reading, routed

ROOT = Path(__file__).resolve().parents[1]
ROW = 974000
SKU = "w2d-sku-"


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


@pytest.fixture
def db(mariadb_or_skip):
    db = mariadb_or_skip

    def wipe():
        # the test database is ours alone (conftest)
        _sql(db, "DELETE FROM automation_queue")
        _sql(db, "DELETE FROM curation_candidates WHERE sku_key LIKE %s OR product_name LIKE 'W2D %%'", (SKU + "%",))
        _sql(db, "DELETE FROM product_failures WHERE sku_key LIKE %s OR product_name LIKE 'W2D %%'", (SKU + "%",))
        _sql(db, "DELETE FROM search_spend")
        _sql(db, "UPDATE automation_state SET run_id = NULL WHERE `key` = 'active_session'")

    wipe()
    yield db
    wipe()


def _row(db, i):
    return _sql(db, "SELECT * FROM automation_queue WHERE `row_number` = %s", (ROW + i,))[0]


def _trace(db, i):
    raw = _row(db, i)["trace_json"]
    return json.loads(raw) if raw else None


# ---------------------------------------------------------------------------
# The worker's own path
# ---------------------------------------------------------------------------

@pytest.fixture
def worker_search(monkeypatch):
    """image_search.search_best_product_image for the worker: the real facade on an outcome routed by the real
    decide.route (the sheet row is what the worker passes)."""
    import google_sheets
    import image_search
    from catalog_match import explain, facade, settings
    from catalog_match.identity import build_sku_spec

    monkeypatch.setattr(settings, "_config", None)
    monkeypatch.setenv("AUTO_PUBLISH_ENABLED", "false")
    monkeypatch.setattr(explain, "PRODUCTS_CACHE", ROOT / "tests" / "no-such-products-cache.json")
    monkeypatch.setattr(google_sheets, "outbox_outcomes", lambda row_numbers=None, since_id=None, limit=500: [])
    rows = {}

    def search(query, name, brand, trace=None, **kw):
        spec = build_sku_spec(facade.to_sku_row(name, brand, kw), MAPPINGS)
        return facade.outcome_to_legacy(routed(spec, rows[name]), trace, spec=spec)

    monkeypatch.setattr(image_search, "search_best_product_image", search)
    return rows


def test_the_worker_saves_why_a_product_has_no_pick(db, worker_search):
    import main

    worker_search["W2D MR JOHN FRENCH FRIES"] = [(listing("Mr John French Fries 900g", 1), reading("UNSURE")),
                                                 (listing("Mr John Fries Value Pack", 2), reading("UNSURE"))]
    db.add_to_queue(ROW, "", "W2D MR JOHN FRENCH FRIES", "MR JOHN", "q", payload={"size": ""}, sku_key=SKU + "0")
    task = db.fetch_next_task("w2d-host:1")
    assert main.pre_cache_product_candidates(task, worksheet=object(), link_column_index=4,
                                             sleep=lambda s: None) == "success"
    row = _row(db, 0)
    assert row["status"] == "ready_for_review"
    why = json.loads(row["trace_json"])["outcome"]["explain"]
    assert why["key"] == "no_size" and why["engine"] == "unsure"
    assert why["text"].startswith("الشيت ما فيه حجم ولا باركود لهالمنتج")
    assert why["text"].endswith("أضف الحجم في الشيت ثم أعد البحث، أو اختر من الصور تحت.")
    # the candidates the reviewer chooses from are saved as before, none pre-selected
    saved = db.get_curation_candidates(ROW, SKU + "0")
    assert len(saved) == 2 and not any(c["is_selected"] for c in saved)


def test_a_not_found_product_saves_its_reason_with_its_failure(db, worker_search):
    import main

    worker_search["W2D ALMARAI FRESH MILK 1L"] = [(listing("Almarai Fresh Milk 2L", 1), None),
                                                 (listing("Almarai Fresh Milk 500ml", 2), None)]
    db.add_to_queue(ROW + 1, "", "W2D ALMARAI FRESH MILK 1L", "ALMARAI", "q", payload={}, sku_key=SKU + "1")
    task = db.fetch_next_task("w2d-host:1")
    assert main.pre_cache_product_candidates(task, worksheet=object(), link_column_index=4,
                                             sleep=lambda s: None) == "failed"
    row = _row(db, 1)
    assert (row["status"], row["failure_code"]) == ("failed", "ALL_CONFLICTED")
    why = json.loads(row["trace_json"])["outcome"]["explain"]
    assert why["key"] == "all_conflicted" and "(حجم مختلف)" in why["text"]


# ---------------------------------------------------------------------------
# Rows saved before: the backfill
# ---------------------------------------------------------------------------

OLD_OUTCOME = {"decision": "REVIEW_UNSELECTED", "failure_code": None, "reject_counts": {"vlm:UNSURE": 2},
               "provider_health": [{"provider": "serper", "status": "ok", "http_status": 200, "query_id": "Q1"}],
               "queries": ["Mr John French Fries"], "searched_at": "2026-10-03T09:00:00+00:00"}


def _insert(db, i, status, trace, name="W2D MR JOHN FRENCH FRIES", brand="MR JOHN", sku=None, payload=None,
            failure_code=None, run_id=None, barcode=""):
    _sql(db, "INSERT INTO automation_queue (`row_number`, barcode, product_name, brand, search_query, status, "
             "failure_code, trace_json, sku_key, run_id, payload_json, updated_at) "
             "VALUES (%s, %s, %s, %s, 'q', %s, %s, %s, %s, %s, %s, '2026-10-03 09:00:00')",
         (ROW + i, barcode, name, brand, status, failure_code, None if trace is None else json.dumps(trace),
          sku or f"{SKU}{i}", run_id, json.dumps(payload or {"size": ""})))


def _save_candidates(db, i, cands, name="W2D MR JOHN FRENCH FRIES", sku=None):
    assert db.save_curation_candidates(ROW + i, name, "MR JOHN", cands, sku_key=sku or f"{SKU}{i}")


UNSURE_TIER2 = {"url": "https://cdn.carrefouruae.com/img/1.jpg", "status": "eligible", "title": "Mr John Fries",
                "page_url": "https://www.carrefouruae.com/mafuae/en/mr-john-fries/p/1", "domain": "carrefouruae.com",
                "reasons": ["vlm:UNSURE"], "evidence": {"tier": 2, "size": "unknown", "brand": True},
                "vlm": {"decision": "UNSURE", "brand_text": "Mr John", "size_match": "unsure"}}


@pytest.fixture
def bridge(monkeypatch, tmp_path):
    import cli_bridge
    from catalog_match import explain

    monkeypatch.setattr(cli_bridge, "_load_brand_mappings", lambda: MAPPINGS)
    monkeypatch.setattr(explain, "PRODUCTS_CACHE", tmp_path / "products_cache.json")
    return cli_bridge


def test_rows_saved_before_get_their_reason_from_what_is_stored(db, bridge):
    _insert(db, 0, "ready_for_review", {"outcome": OLD_OUTCOME})                    # no pick, saved before
    _save_candidates(db, 0, [UNSURE_TIER2, dict(UNSURE_TIER2, url="https://cdn.carrefouruae.com/img/2.jpg")])
    full = {"outcome": {"decision": "NOT_FOUND", "failure_code": "ALL_CONFLICTED", "reject_counts": {"size_conflict": 3}},
            "steps": [{"candidates": [{"url": f"https://x.ae/{n}.jpg", "status": "rejected",
                                       "reasons": ["hard:size_conflict"], "evidence": {"tier": None}} for n in range(3)]}]}
    _insert(db, 1, "failed", full, name="W2D ALMARAI MILK 1L", brand="ALMARAI", failure_code="ALL_CONFLICTED")
    picked = {"outcome": dict(OLD_OUTCOME, decision="REVIEW_PRESELECTED", winner_url="https://cdn.x.ae/p.jpg")}
    _insert(db, 2, "ready_for_review", picked, payload={"size": "900g"})
    _save_candidates(db, 2, [dict(UNSURE_TIER2, url="https://cdn.x.ae/p.jpg", status="preselected")])
    _insert(db, 3, "ready_for_review", None, sku=f"{SKU}0")                         # a sibling: no trace of its own
    assert len(bridge.local_cache_db.queue_rows_missing_explain()) == 3

    out = bridge.action_explain_backfill({})
    assert out == {"status": "success", "filled": 3, "checked": 3}
    why = _trace(db, 0)["outcome"]["explain"]
    assert (why["key"], why["engine"], why["source"]) == ("no_size", "unsure", "stored")
    assert "قارئ الملصق ما تأكد من صورتين" in why["text"]
    assert _trace(db, 1)["outcome"]["explain"]["key"] == "all_conflicted"
    assert _trace(db, 1)["steps"]                                                 # the rest of the trace is kept
    assert "explain" in _trace(db, 2)["outcome"] and _trace(db, 2)["outcome"]["explain"] is None
    assert _row(db, 3)["trace_json"] is None
    for i in range(4):                                       # the page's snapshot (status, updated_at) is unchanged
        row = _row(db, i)
        assert str(row["updated_at"]) == "2026-10-03 09:00:00", i
    assert [_row(db, i)["status"] for i in range(4)] == ["ready_for_review", "failed", "ready_for_review", "ready_for_review"]
    assert bridge.action_explain_backfill({}) == {"status": "success", "filled": 0, "checked": 0}


def test_the_backfill_never_writes_over_a_newer_result(db):
    _insert(db, 0, "ready_for_review", {"outcome": OLD_OUTCOME})
    (seen,) = db.queue_rows_missing_explain()
    newer = {"outcome": dict(OLD_OUTCOME, searched_at="2026-10-04T10:00:00+00:00", explain={"key": "unsure"})}
    _sql(db, "UPDATE automation_queue SET trace_json = %s WHERE id = %s", (json.dumps(newer), seen["id"]))
    assert db.set_queue_explain(seen["id"], seen["trace_json"], {"key": "no_size"}) is False
    assert _trace(db, 0) == newer


def test_a_reject_with_a_new_search_stores_the_new_searchs_reason(db, bridge):
    """reject_image with research saves the new search's candidates (_save_research_candidates): the row's reason is
    the new search's, not the one the rejected candidates had."""
    _insert(db, 0, "ready_for_review", {"outcome": dict(OLD_OUTCOME, explain={"key": "unsure", "text": "old"})})
    task = db.get_task_by_row(ROW)
    found = {"best": {"url": None, "candidates": [dict(UNSURE_TIER2, url="https://cdn.x.ae/new.jpg")]},
             "trace": {"outcome": {"decision": "REVIEW_UNSELECTED", "explain": {"key": "no_size", "text": "new"}}}}
    saved = bridge._save_research_candidates(found, ROW, "W2D MR JOHN FRENCH FRIES", "MR JOHN", f"{SKU}0",
                                             "https://cdn.x.ae/rejected.jpg")
    assert saved == 1 and [c["image_url"] for c in db.get_curation_candidates(ROW, f"{SKU}0")] == ["https://cdn.x.ae/new.jpg"]
    assert _trace(db, 0)["outcome"]["explain"] == {"key": "no_size", "text": "new"}
    assert str(_row(db, 0)["updated_at"]) == str(task["updated_at"])
    # the new search found a pick: the row no longer has a reason
    found["trace"]["outcome"] = {"decision": "REVIEW_PRESELECTED"}
    assert bridge._save_research_candidates(found, ROW, "W2D MR JOHN FRENCH FRIES", "MR JOHN", f"{SKU}0", "x") == 1
    assert "explain" in _trace(db, 0)["outcome"] and _trace(db, 0)["outcome"]["explain"] is None
    # another product now at that row (the sheet moved): its trace is not touched
    _sql(db, "UPDATE automation_queue SET trace_json = %s WHERE `row_number` = %s",
         (json.dumps({"outcome": dict(OLD_OUTCOME, explain={"key": "unsure"})}), ROW))
    bridge._save_research_explain(ROW, f"{SKU}9", "W2D OTHER PRODUCT", None)
    assert _trace(db, 0)["outcome"]["explain"] == {"key": "unsure"}
