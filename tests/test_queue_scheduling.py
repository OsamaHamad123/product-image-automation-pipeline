"""Queue scheduling (work package P4a).

- Claim order: new rows first, then re-verifications (VERIFIER_DOWN), then retries whose time has come.
- NOT_FOUND / ALL_CONFLICTED: retried after 3, 7 and 30 days, then the row stays failed; sooner when the
  brand's Brands Mapping entry or the local index changed.
- PROVIDER_DOWN for one row: 10 minutes doubling, parked for the next run after 3 attempts in a run; the
  worker neither spins on it nor ends the run as "providers unavailable".
- One product, one search: rows sharing a sku_key wait while one of them is searched and take its result.
- The worker stops when Serper refused N searches in a row (credit / key) or the daily budget is reached.
- The enqueue writes thousands of rows in batches.

Real-MariaDB tests use the test database (conftest) and skip when MariaDB is down; the worker loop tests are
offline with recorders.
"""

import itertools
import json
import os
import threading
import time

import pytest

ROW = 961000
SKU = "p4q-sku-"


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
        _sql(db, "DELETE FROM curation_candidates WHERE sku_key LIKE %s", (SKU + "%",))
        _sql(db, "DELETE FROM resolved_products WHERE sku_key LIKE %s", (SKU + "%",))
        _sql(db, "DELETE FROM product_failures WHERE sku_key LIKE %s", (SKU + "%",))
        _sql(db, "DELETE FROM search_spend")
        _sql(db, "UPDATE automation_state SET status = 'idle', stop_requested = 0, pause_requested = 0, run_id = NULL, "
                 "notice = NULL WHERE `key` = 'active_session'")

    wipe()
    yield db
    wipe()


def _add(db, i, sku=None, **kw):
    db.add_to_queue(ROW + i, "", f"Product {i}", "Brand", "q", sku_key=sku or f"{SKU}{i}", **kw)


def _row(db, i):
    return _sql(db, "SELECT *, TIMESTAMPDIFF(MINUTE, NOW(), next_attempt_at) AS due_in_min FROM automation_queue "
                    "WHERE `row_number` = %s", (ROW + i,))[0]


def _claim_finish(db, status, failure_code=None, worker="host:1"):
    task = db.fetch_next_task(worker)
    assert task is not None
    assert db.update_task_status(task["id"], status, failure_code=failure_code, claim_id=task["worker_id"])
    return task


def _make_due(db, i):
    _sql(db, "UPDATE automation_queue SET next_attempt_at = NOW() - INTERVAL 1 MINUTE WHERE `row_number` = %s",
         (ROW + i,))


# ---------------------------------------------------------------------------
# claim order
# ---------------------------------------------------------------------------

def test_claim_order_is_new_rows_then_rechecks_then_retries(db):
    _add(db, 0)                                    # night 1: a search error -> a retry tomorrow
    _claim_finish(db, "failed", "SEARCH_ERROR")
    _add(db, 1)                                    # night 1: the label reader was down -> a re-verification
    _claim_finish(db, "ready_for_review", "VERIFIER_DOWN")

    # night 2: the enqueue sees both old rows again, and the owner added a product at the bottom
    for i in (0, 1, 2):
        _add(db, i)
    assert db.requeue_verifier_down() == 1         # the worker found the reader healthy
    order = []
    while True:
        task = db.fetch_next_task("host:1")
        if task is None:
            break
        order.append(task["row_number"] - ROW)
        db.update_task_status(task["id"], "ready_for_review", claim_id=task["worker_id"])
    assert order == [2, 1, 0]


def test_unfindable_rows_do_not_come_back_every_night(db):
    """queue_order probe: the not-found rows of last night no longer go first, nor at all before their date."""
    for i in (0, 1, 2):
        _add(db, i)
        _claim_finish(db, "failed", "NO_RESULTS")
    for i in (0, 1, 2, 3, 4):                     # next enqueue: every sheet row again, 2 new ones
        _add(db, i)
    order = []
    while True:
        task = db.fetch_next_task("host:1")
        if task is None:
            break
        order.append(task["row_number"] - ROW)
        db.update_task_status(task["id"], "ready_for_review", claim_id=task["worker_id"])
    assert order == [3, 4]
    assert {_row(db, i)["status"] for i in (0, 1, 2)} == {"failed"}


# ---------------------------------------------------------------------------
# NOT_FOUND schedule
# ---------------------------------------------------------------------------

def test_not_found_is_retried_after_3_7_and_30_days_then_stays_failed(db):
    _add(db, 0)
    for n, days in enumerate((3, 7, 30), start=1):
        _claim_finish(db, "failed", "NO_RESULTS")
        row = _row(db, 0)
        assert row["status"] == "failed" and row["fail_count"] == n
        assert days * 1440 - 2 <= row["due_in_min"] <= days * 1440
        _add(db, 0)                                # the next nightly enqueue: not due yet
        assert _row(db, 0)["status"] == "failed"
        assert db.fetch_next_task("host:1") is None
        _make_due(db, 0)
        _add(db, 0)                                # due: back in the queue as a retry
        row = _row(db, 0)
        assert (row["status"], row["priority"], row["requeue_reason"]) == ("pending", 2, "SCHEDULED_RETRY")
    _claim_finish(db, "failed", "ALL_CONFLICTED")  # the fourth result: no more retries
    row = _row(db, 0)
    assert row["fail_count"] == 4 and row["next_attempt_at"] is None and row["failure_code"] == "ALL_CONFLICTED"
    _add(db, 0)
    assert _row(db, 0)["status"] == "failed"        # kept failed with its code: visible, not lost
    _add(db, 0, reprocess=True)                     # the owner can still ask for it
    row = _row(db, 0)
    assert row["status"] == "pending" and row["fail_count"] == 0


def test_not_found_is_retried_early_when_the_brand_mapping_changes(db):
    _add(db, 0, brand_fp="fp-a")
    _claim_finish(db, "failed", "NO_RESULTS")
    _add(db, 0, brand_fp="fp-a")
    assert _row(db, 0)["status"] == "failed"
    _add(db, 0, brand_fp=None)                      # the mapping did not load tonight: not a change
    assert _row(db, 0)["status"] == "failed"
    _add(db, 0, brand_fp="fp-b")                    # the owner edited the brand's mapping entry
    row = _row(db, 0)
    assert (row["status"], row["requeue_reason"], row["priority"]) == ("pending", "BRAND_MAPPING_CHANGED", 2)
    assert row["brand_fp"] == "fp-b"
    _claim_finish(db, "failed", "NO_RESULTS")
    assert _row(db, 0)["fail_count"] == 2           # the schedule goes on
    _add(db, 0, brand_fp="fp-b")
    assert _row(db, 0)["status"] == "failed"
    _add(db, 0, brand_fp="fp-b", requeue_reason="LOCAL_INDEX_CHANGED")
    assert (_row(db, 0)["status"], _row(db, 0)["requeue_reason"]) == ("pending", "LOCAL_INDEX_CHANGED")


@pytest.fixture
def catalog(db):
    def wipe():
        _sql(db, "DELETE FROM catalog_products WHERE store = 'p4qtest'")

    wipe()
    yield db
    wipe()


def _catalog_row(db, url, token, age_sql):
    _sql(db, "INSERT INTO catalog_products (store, url, url_hash, slug_text, first_seen, last_seen) "
             f"VALUES ('p4qtest', %s, SHA1(%s), %s, {age_sql}, NOW())", (url, url, token))
    pid = _sql(db, "SELECT id FROM catalog_products WHERE url = %s", (url,))[0]["id"]
    _sql(db, "INSERT INTO catalog_tokens (token, product_id) VALUES (%s, %s)", (token, pid))


def test_enqueue_sees_new_local_index_pages_for_the_brand_of_a_not_found_row(catalog, monkeypatch):
    import main
    db = catalog
    prod = {"row_number": ROW, "product_name": "Zwanzig Beef Luncheon Meat 850g", "brand": "Zwanzig",
            "barcode": "", "existing_image_link": ""}
    rows, _ = main.plan_enqueue([prod])
    db.add_many_to_queue(rows)
    task = db.fetch_next_task("host:1")
    db.update_task_status(task["id"], "failed", failure_code="NO_RESULTS", claim_id=task["worker_id"])
    _sql(db, "UPDATE automation_queue SET searched_at = NOW() - INTERVAL 1 DAY WHERE id = %s", (task["id"],))

    _catalog_row(db, "https://shop.example/zwanzig-old-can", "zwanzig", "NOW() - INTERVAL 3 DAY")
    rows, stats = main.plan_enqueue([prod])
    assert rows[0]["requeue_reason"] is None and stats["index_changed"] == 0     # nothing new since the search

    _catalog_row(db, "https://shop.example/zwanzig-luncheon-850g", "zwanzig", "NOW()")
    rows, stats = main.plan_enqueue([prod])
    assert rows[0]["requeue_reason"] == "LOCAL_INDEX_CHANGED" and stats["index_changed"] == 1
    db.add_many_to_queue(rows)
    assert _sql(db, "SELECT status FROM automation_queue WHERE id = %s", (task["id"],))[0]["status"] == "pending"


# ---------------------------------------------------------------------------
# PROVIDER_DOWN backoff and the per-run cap
# ---------------------------------------------------------------------------

def test_provider_down_backs_off_and_is_parked_after_three_attempts_in_a_run(db):
    _add(db, 0)
    for n, minutes in enumerate((10, 20), start=1):
        _claim_finish(db, "pending", "PROVIDER_DOWN")
        row = _row(db, 0)
        assert row["down_count"] == n and minutes - 2 <= row["due_in_min"] <= minutes
        assert db.fetch_next_task("host:1") is None      # not before its time
        assert db.count_open_tasks() == 1                # the worker waits for it (within the horizon)
        _make_due(db, 0)
    _claim_finish(db, "pending", "PROVIDER_DOWN")        # the third attempt of this run
    row = _row(db, 0)
    assert row["status"] == "pending" and row["down_count"] == 3 and row["due_in_min"] >= 12 * 60 - 2
    assert db.count_open_tasks() == 0                    # parked: the worker does not wait for it

    _add(db, 0)                                          # the next run's enqueue: due at once
    row = _row(db, 0)
    assert row["down_count"] == 0 and row["next_attempt_at"] is None
    assert db.fetch_next_task("host:1")["row_number"] == ROW


def test_a_retry_from_the_dashboard_is_claimed_at_once(db):
    """ApiController::retryFailures resets status and failure_code only; an old backoff must not hold the row."""
    _add(db, 0)
    _claim_finish(db, "pending", "PROVIDER_DOWN")
    _sql(db, "UPDATE automation_queue SET status = 'pending', failure_code = NULL WHERE `row_number` = %s", (ROW,))
    assert db.fetch_next_task("host:1")["row_number"] == ROW


@pytest.fixture
def real_worker(db, monkeypatch, tmp_path):
    """run_worker_mode on the real queue with the sheet and the search replaced."""
    import google_sheets
    import main

    monkeypatch.chdir(tmp_path)
    os.makedirs("temp", exist_ok=True)
    real_sleep = time.sleep
    monkeypatch.setattr(time, "sleep", lambda s: real_sleep(0.01))
    monkeypatch.setattr(main, "_another_worker_running", lambda lock: False)
    monkeypatch.setattr(main, "load_run_config", lambda: None)
    monkeypatch.setattr(main, "check_verifier", lambda: "")
    monkeypatch.setattr(main, "_outage_notice", lambda worker_id, since, base=None, **k: base)
    monkeypatch.setattr(google_sheets, "get_sheets_client", lambda: object())
    monkeypatch.setattr(google_sheets, "open_worksheet", lambda client, name: object())
    monkeypatch.setattr(google_sheets, "find_link_column", lambda ws: 5)
    monkeypatch.setattr(google_sheets, "get_brand_mappings", lambda *a: {})
    monkeypatch.setattr(google_sheets, "init_async_queue", lambda *a: None)
    monkeypatch.setattr(google_sheets, "stop_async_queue", lambda: None)

    def watchdog():
        # the old worker never ended on its own with such a row: ask it to stop
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and not done.is_set():
            real_sleep(0.1)
        if not done.is_set():
            db.update_automation_state(status="pre_caching", stop_requested=1)

    done = threading.Event()
    thread = threading.Thread(target=watchdog, daemon=True)
    thread.start()
    yield main
    done.set()


def test_one_unreachable_row_does_not_spin_the_worker(real_worker, db, monkeypatch):
    """worker_spin probe: one row whose search always comes back PROVIDER_DOWN while the others succeed was claimed
    over and over (15 times in a run) and the run ended as 'providers unavailable'."""
    import local_cache_db
    main = real_worker
    monkeypatch.setattr(local_cache_db, "OPEN_TASK_HORIZON_MINUTES", 5)     # shorter than the first backoff
    _add(db, 0, sku=SKU + "poison")
    for i in range(1, 6):
        _add(db, i)
    claims = {"poison": 0, "normal": 0}

    def fake(task, *a, **k):
        if task["sku_key"] == SKU + "poison":
            claims["poison"] += 1
            main._finish_task(task, "pending", "Search providers unavailable", failure_code="PROVIDER_DOWN")
            return "provider_down"
        claims["normal"] += 1
        main._finish_task(task, "ready_for_review")
        return "success"

    monkeypatch.setattr(main, "pre_cache_product_candidates", fake)
    main.run_worker_mode()
    assert claims == {"poison": 1, "normal": 5}
    assert db.get_automation_state()["status"] == "curation_pending"          # a normal end, not provider_down
    row = _row(db, 0)
    assert row["status"] == "pending" and row["failure_code"] == "PROVIDER_DOWN"   # left for later, not lost


# ---------------------------------------------------------------------------
# one product, one search
# ---------------------------------------------------------------------------

def _statuses(db):
    return {r["row_number"] - ROW: (r["status"], r["failure_code"])
            for r in _sql(db, "SELECT `row_number`, status, failure_code FROM automation_queue")}


def test_rows_of_one_product_are_searched_once_and_all_take_the_result(db):
    for i in range(3):
        _add(db, i, sku=SKU + "dup")
    _add(db, 3)
    first = db.fetch_next_task("host:1")
    second = db.fetch_next_task("host:1")
    assert first["row_number"] == ROW and second["row_number"] == ROW + 3   # the duplicates wait
    assert db.fetch_next_task("host:1") is None
    db.update_task_status(first["id"], "ready_for_review", failure_code="VERIFIER_DOWN", claim_id=first["worker_id"])
    assert _statuses(db) == {0: ("ready_for_review", "VERIFIER_DOWN"), 1: ("ready_for_review", "VERIFIER_DOWN"),
                             2: ("ready_for_review", "VERIFIER_DOWN"), 3: ("processing", None)}
    assert db.fetch_next_task("host:1") is None                             # nothing searched twice


def test_a_not_found_product_schedules_all_its_rows(db):
    for i in range(2):
        _add(db, i, sku=SKU + "dup")
    _claim_finish(db, "failed", "NO_RESULTS")
    rows = [_row(db, i) for i in range(2)]
    assert {(r["status"], r["failure_code"], r["fail_count"]) for r in rows} == {("failed", "NO_RESULTS", 1)}
    assert all(3 * 1440 - 2 <= r["due_in_min"] <= 3 * 1440 for r in rows)


def test_provider_down_defers_the_waiting_rows_but_not_a_scheduled_failure(db):
    for i in range(2):
        _add(db, i, sku=SKU + "dup")
    _sql(db, "UPDATE automation_queue SET status = 'failed', failure_code = 'NO_RESULTS', fail_count = 2, "
             "next_attempt_at = NOW() + INTERVAL 5 DAY WHERE `row_number` = %s", (ROW + 1,))
    _add(db, 2, sku=SKU + "dup")
    task = _claim_finish(db, "pending", "PROVIDER_DOWN")
    assert task["row_number"] == ROW
    assert _row(db, 2)["status"] == "pending" and 8 <= _row(db, 2)["due_in_min"] <= 10
    assert db.fetch_next_task("host:1") is None                              # the waiting row is deferred too
    row1 = _row(db, 1)
    assert (row1["status"], row1["failure_code"], row1["fail_count"]) == ("failed", "NO_RESULTS", 2)


def test_auto_publish_writes_every_row_of_the_product(db, monkeypatch):
    import google_sheets
    import image_search
    import main

    for i in range(3):
        db.add_to_queue(ROW + i, "", "Laban Up Strawberry 180ml", "Al Rawabi", "q", payload={"size": "180ml"},
                        sku_key=SKU + "laban")
    leader = db.fetch_next_task("host:1")
    link = "https://res.cloudinary.com/p4q/laban.png"
    best = {"url": "https://shop.example/laban.jpg", "decision": "AUTO_PUBLISH", "source": "serper",
            "candidates": [{"url": "https://shop.example/laban.jpg", "status": "preselected"}]}

    def fake_search(query, name, brand, trace=None, **kw):
        trace["outcome"] = {"decision": "AUTO_PUBLISH"}
        return dict(best)

    def fake_auto(task, best_image, ws, col, sku_key=None):
        db.save_product_resolution("", task["product_name"], task["brand"], best_image["url"], link,
                                   verification_status="auto_verified", approved_by="auto", sku_key=sku_key)
        return "published"

    links = []
    monkeypatch.setattr(image_search, "search_best_product_image", fake_search)
    monkeypatch.setattr(main, "auto_approve_product", fake_auto)
    monkeypatch.setattr(google_sheets, "update_image_link",
                        lambda ws, row, col, value, **k: links.append((row, value, k)) or True)
    monkeypatch.setattr(google_sheets, "update_product_metadata", lambda *a, **k: True)

    assert main.pre_cache_product_candidates(leader, worksheet=object(), link_column_index=5,
                                             sleep=lambda s: None) == "success"
    assert sorted(row for row, _, _ in links) == [ROW + 1, ROW + 2]
    assert {value for _, value, _ in links} == {link}
    assert links[0][2] == {"barcode": "", "product_name": "Laban Up Strawberry 180ml", "size": "180ml",
                           "brand": "Al Rawabi"}
    assert {s for s, _ in _statuses(db).values()} == {"completed"}
    assert db.fetch_next_task("host:1") is None


def test_a_sibling_whose_link_cannot_be_queued_is_marked_for_a_rewrite(db, monkeypatch):
    import google_sheets
    import main

    for i in range(2):
        _add(db, i, sku=SKU + "dup")
    leader = db.fetch_next_task("host:1")
    db.save_product_resolution("", "Product 0", "Brand", "https://src/x.jpg", "https://res/x.png",
                               verification_status="auto_verified", approved_by="auto", sku_key=SKU + "dup")
    monkeypatch.setattr(google_sheets, "update_image_link", lambda *a, **k: False)
    assert main._publish_to_siblings(leader, SKU + "dup", object(), 5) == []
    assert _row(db, 1)["status"] == "failed" and _row(db, 1)["failure_code"] == "SHEET_WRITE_FAILED"
    assert _row(db, 0)["status"] == "processing"                            # the leader is untouched


# ---------------------------------------------------------------------------
# VERIFIER_DOWN rows are re-checked on the next run
# ---------------------------------------------------------------------------

def test_verifier_down_rows_are_rechecked_twice_at_most_and_parked_while_the_reader_is_down(db):
    _add(db, 0)
    _claim_finish(db, "ready_for_review", "VERIFIER_DOWN")
    _add(db, 1)
    _claim_finish(db, "ready_for_review", None)                  # a normal review row is not touched
    assert db.park_verifier_rechecks() == 0
    for attempt in (1, 2):
        assert db.requeue_verifier_down("run-x") == 1
        row = _row(db, 0)
        assert (row["status"], row["priority"], row["requeue_reason"], row["run_id"]) == (
            "pending", 1, "VERIFIER_RECHECK", "run-x")
        if attempt == 1:
            assert db.park_verifier_rechecks() == 1                # the reader is still down this run
            assert _row(db, 0)["status"] == "ready_for_review"
            assert db.requeue_verifier_down("run-x") == 1
        _claim_finish(db, "ready_for_review", "VERIFIER_DOWN")
    assert db.requeue_verifier_down("run-x") == 0                  # twice is enough: the reviewer decides
    assert _row(db, 1)["status"] == "ready_for_review"


# ---------------------------------------------------------------------------
# spend ledger and budget
# ---------------------------------------------------------------------------

OUTCOME = {"provider_health": [{"provider": "serper", "status": "ok"}, {"provider": "serper", "status": "empty"},
                               {"provider": "serper", "status": "quota"}, {"provider": "lens_serpapi", "status": "ok"},
                               {"provider": "local_index", "status": "ok"}],
           "vlm_usage": [{"role": "primary", "provider": "gemini", "model": "m", "usd": 0.002}]}


def test_spend_ledger_counts_answered_paid_calls_per_day(db):
    items = {p: (c, round(u, 6)) for p, c, u in db.spend_from_outcome(OUTCOME)}
    assert items == {"serper": (2, 0.002), "lens_serpapi": (1, 0.015), "gemini": (1, 0.002)}
    assert db.spend_today() == 0
    assert db.record_search_spend(OUTCOME, "run-1") == 3
    assert db.record_search_spend(OUTCOME, "run-1") == 3
    assert db.spend_today() == pytest.approx(2 * 0.019)
    rows = {(r["provider"], r["calls"]) for r in db.spend_by_day(1)}
    assert rows == {("serper", 4), ("lens_serpapi", 2), ("gemini", 2)}
    assert db.spend_from_outcome({"vlm_calls": 3}) == [("gemini", 3, 0.003)]   # legacy outcome without usage


def test_every_search_attempt_is_written_to_the_ledger(offline, monkeypatch):
    import image_search
    import local_cache_db
    import main

    recorded = []
    monkeypatch.setattr(local_cache_db, "record_search_spend", lambda outcome, run_id=None: recorded.append(
        (outcome.get("decision"), run_id)))
    monkeypatch.setattr(local_cache_db, "update_task_status", lambda *a, **k: True)
    monkeypatch.setattr(local_cache_db, "get_rejections", lambda sku: ([], []))

    def down(query, name, brand, trace=None, **kw):
        trace["outcome"] = {"decision": "PROVIDER_DOWN", "failure_code": "PROVIDER_DOWN"}
        return None

    monkeypatch.setattr(image_search, "search_best_product_image", down)
    task = {"id": 1, "row_number": 2, "product_name": "Milk", "brand": "B", "sku_key": "k", "run_id": "run-7"}
    assert main.pre_cache_product_candidates(task, sleep=lambda s: None) == "provider_down"
    assert recorded == [("PROVIDER_DOWN", "run-7")] * 3


@pytest.fixture
def worker(offline, monkeypatch, tmp_path):
    """run_worker_mode with the sheet, the queue and the search replaced by recorders."""
    import config
    import google_sheets
    import local_cache_db
    import main

    monkeypatch.chdir(tmp_path)
    os.makedirs("temp", exist_ok=True)
    real_sleep = time.sleep
    monkeypatch.setattr(time, "sleep", lambda s: real_sleep(0.005))
    rec = {"state": {"pause_requested": 0, "stop_requested": 0, "run_id": "run-w"}, "states": [], "worked": [],
           "tasks": [{"id": i, "row_number": 100 + i, "product_name": f"P{i}"} for i in range(10)]}
    monkeypatch.setattr(config, "DAILY_BUDGET_USD", 0)
    monkeypatch.setattr(config, "SERPER_CREDIT_STOP_SEARCHES", 3)
    monkeypatch.setattr(main, "_another_worker_running", lambda lock: False)
    monkeypatch.setattr(main, "load_run_config", lambda: None)
    monkeypatch.setattr(main, "check_verifier", lambda: "")
    monkeypatch.setattr(main, "_outage_notice", lambda worker_id, since, base=None, **k: base)
    monkeypatch.setattr(main, "_refresh_state", lambda *a, **k: None)
    monkeypatch.setattr(local_cache_db, "resume_automation", lambda: True)
    monkeypatch.setattr(local_cache_db, "get_automation_state", lambda: dict(rec["state"]))
    monkeypatch.setattr(local_cache_db, "update_automation_state",
                        lambda status, **kw: rec["states"].append(dict(kw, status=status)) or True)
    monkeypatch.setattr(local_cache_db, "get_ready_for_review_count", lambda: 0)
    monkeypatch.setattr(local_cache_db, "requeue_verifier_down", lambda run_id=None: 0)
    monkeypatch.setattr(local_cache_db, "park_verifier_rechecks", lambda: 0)
    monkeypatch.setattr(local_cache_db, "fetch_next_task", lambda worker_id: rec["tasks"].pop(0) if rec["tasks"] else None)
    monkeypatch.setattr(local_cache_db, "count_open_tasks", lambda: len(rec["tasks"]))
    for name in ("get_sheets_client", "stop_async_queue"):
        monkeypatch.setattr(google_sheets, name, lambda *a: object())
    monkeypatch.setattr(google_sheets, "open_worksheet", lambda client, name: object())
    monkeypatch.setattr(google_sheets, "find_link_column", lambda ws: 7)
    monkeypatch.setattr(google_sheets, "get_brand_mappings", lambda *a: {})
    monkeypatch.setattr(google_sheets, "init_async_queue", lambda *a: None)
    return main, local_cache_db, config, rec


def test_worker_stops_after_n_searches_refused_by_serper(worker, monkeypatch):
    main, _, _, rec = worker

    def precache(task, *a, report=None, **k):
        rec["worked"].append(task["row_number"])
        report.update(searched=True, serper_credit=True)
        return "success"             # other providers still answered: only Serper's credit is gone

    monkeypatch.setattr(main, "pre_cache_product_candidates", precache)
    main.run_worker_mode()
    assert 3 <= len(rec["worked"]) <= 5           # three in a row stop the claims (two may already run)
    assert len(rec["tasks"]) >= 5                 # the rest stay pending for the next run
    assert rec["states"][-1]["status"] == "idle" and rec["states"][-1]["stop_requested"] == 0


def test_a_search_answered_by_serper_resets_the_credit_streak(worker, monkeypatch):
    main, _, _, rec = worker
    answers = itertools.cycle([True, True, False])

    def precache(task, *a, report=None, **k):
        rec["worked"].append(task["row_number"])
        report.update(searched=True, serper_credit=next(answers))
        return "success"

    monkeypatch.setattr(main, "pre_cache_product_candidates", precache)
    monkeypatch.setattr(main, "MAX_PROVIDER_DOWN_STREAK", 99)
    rec["tasks"] = rec["tasks"][:1]
    for i in range(9):
        rec["tasks"].append({"id": 50 + i, "row_number": 200 + i, "product_name": "x"})
    # one at a time keeps the order of the answers
    import concurrent.futures
    real_pool = concurrent.futures.ThreadPoolExecutor
    monkeypatch.setattr(concurrent.futures, "ThreadPoolExecutor", lambda max_workers: real_pool(max_workers=1))
    main.run_worker_mode()
    assert len(rec["worked"]) == 10 and rec["tasks"] == []
    assert main._next_credit_streak(2, {"serper_credit": None}) == 2           # no Serper call: no change
    assert main._next_credit_streak(2, {}) == 2                                # a link write: no search


def test_worker_stops_at_the_daily_budget_and_warns_at_80_percent(worker, monkeypatch):
    main, local_cache_db, config, rec = worker
    printed = []
    monkeypatch.setattr(main, "print", lambda *a: printed.append(" ".join(str(x) for x in a)))
    monkeypatch.setattr(config, "DAILY_BUDGET_USD", 1.0)
    spent = itertools.chain([0.5, 0.85, 0.9], itertools.repeat(1.0))
    monkeypatch.setattr(local_cache_db, "spend_today", lambda: next(spent))
    monkeypatch.setattr(main, "pre_cache_product_candidates",
                        lambda task, *a, **k: rec["worked"].append(task["row_number"]) or "success")
    main.run_worker_mode()
    assert rec["worked"] == [100, 101, 102]
    assert len(rec["tasks"]) == 7                                              # left pending
    final = rec["states"][-1]
    assert final["status"] == "idle" and final["notice"].startswith("BUDGET_REACHED: ")
    assert any("80%" in line for line in printed)


def test_an_unreadable_spend_never_becomes_an_open_budget(worker, monkeypatch):
    main, local_cache_db, config, rec = worker
    monkeypatch.setattr(config, "DAILY_BUDGET_USD", 1.0)
    monkeypatch.setattr(main, "MAX_DB_OUTAGE_SECONDS", 0)

    def broken():
        raise RuntimeError("database down")

    monkeypatch.setattr(local_cache_db, "spend_today", broken)
    monkeypatch.setattr(main, "pre_cache_product_candidates",
                        lambda task, *a, **k: rec["worked"].append(task["row_number"]) or "success")
    main.run_worker_mode()
    assert rec["worked"] == [] and len(rec["tasks"]) == 10


# ---------------------------------------------------------------------------
# batched enqueue
# ---------------------------------------------------------------------------

def test_enqueue_of_a_thousand_rows_is_batched(db):
    rows = [db.queue_input(ROW + i, "", f"Product {i}", "Brand", "q", payload={"size": ""}, sku_key=f"{SKU}{i}")
            for i in range(1000)]
    started = time.monotonic()
    assert db.add_many_to_queue(rows) == {"insert": 1000, "keep": 0, "reset": 0}
    elapsed = time.monotonic() - started
    assert elapsed < 5, f"1000 rows took {elapsed:.1f}s (one connection and commit per row took ~25 s)"
    assert _sql(db, "SELECT COUNT(*) AS n FROM automation_queue")[0]["n"] == 1000
    assert json.loads(_row(db, 7)["payload_json"]) == {"size": ""}
    assert db.add_many_to_queue(rows) == {"insert": 0, "keep": 0, "reset": 1000}   # pending rows: queued again
