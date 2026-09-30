"""Running the automation never destroys work and says what is happening (work package R).

- Stop / reset (local_cache_db.stop_run / reset_run, cli_bridge run_control): nothing is deleted; rows in
  'processing' go back to 'pending'; a stop during the enqueue is a request the worker honours when it starts.
- Progress per run: each enqueue starts a run (run_id); its progress counts only the rows it will work.
- 'curation_pending' ends when no row waits for review any more.
- An enqueue failure releases the dashboard's 'STARTING' lock at once and reports an Arabic error.

Real-MariaDB tests use the `automation_test*` database (conftest) and skip when MariaDB is down; the worker and
bridge wiring tests are offline with recorders.
"""

import base64
import json
import os

import pytest

ROWS = tuple(range(930001, 930008))
SKU = "wpr-sku-"


# ---------------------------------------------------------------------------
# MariaDB fixtures
# ---------------------------------------------------------------------------

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
        # the test database is ours alone (conftest); the run state and the review counts are global
        _sql(db, "DELETE FROM automation_queue")
        _sql(db, "DELETE FROM curation_candidates WHERE sku_key LIKE %s", (SKU + "%",))
        _sql(db, "DELETE FROM review_decisions WHERE sku_key LIKE %s", (SKU + "%",))
        _sql(db, "DELETE FROM rejected_images WHERE sku_key LIKE %s", (SKU + "%",))
        _sql(db, "DELETE FROM resolved_products WHERE sku_key LIKE %s", (SKU + "%",))
        _sql(db, "UPDATE automation_state SET status = 'idle', stop_requested = 0, pause_requested = 0, run_id = NULL, "
                 "notice = NULL, current_product_name = '', total_items = 0, processed_items = 0, success_count = 0, "
                 "failed_count = 0 WHERE `key` = 'active_session'")

    wipe()
    yield db
    wipe()


def _seed(db, statuses, run_id=None):
    """One queue row per status (rows ROWS[0..]); 'processing' rows hold a live claim."""
    for i, status in enumerate(statuses):
        db.add_to_queue(ROWS[i], "", f"Product {i}", "Brand", "q", sku_key=f"{SKU}{i}")
        lease = "NOW() + INTERVAL 10 MINUTE" if status == "processing" else "NULL"
        worker = "'host:1#claim'" if status == "processing" else "NULL"
        _sql(db, f"UPDATE automation_queue SET status = %s, worker_id = {worker}, lease_until = {lease}, run_id = %s "
                 "WHERE `row_number` = %s", (status, run_id, ROWS[i]))


def _review_work(db, row_index):
    """Candidates, a reviewer decision, a rejection and an approved image for the product at ROWS[row_index]."""
    sku = f"{SKU}{row_index}"
    assert db.save_curation_candidates(ROWS[row_index], f"Product {row_index}", "Brand",
                                       [{"url": "https://x.ae/a.jpg", "status": "preselected"},
                                        {"url": "https://x.ae/b.jpg", "status": "eligible"}], sku_key=sku)
    db.add_review_decision("rejected", sku_key=sku, row_number=ROWS[row_index], image_url="https://x.ae/c.jpg",
                           reason_code="WRONG_SIZE")
    assert db.add_rejected_image(sku, "https://x.ae/c.jpg", reason_code="WRONG_SIZE")
    assert db.save_product_resolution("", f"Product {row_index}", "Brand", "https://x.ae/old.jpg",
                                      "https://res.cloudinary.com/old.png", verification_status="human_approved",
                                      approved_by="human", sku_key=sku)


def _review_work_counts(db):
    return {table: _sql(db, f"SELECT COUNT(*) AS n FROM {table} WHERE sku_key LIKE %s", (SKU + "%",))[0]["n"]
            for table in ("curation_candidates", "review_decisions", "rejected_images", "resolved_products")}


def _queue(db):
    return {r["row_number"]: r for r in _sql(db, "SELECT * FROM automation_queue")}


def _set_state(db, **columns):
    sets = ", ".join(f"{k} = %s" for k in columns)
    _sql(db, f"UPDATE automation_state SET {sets} WHERE `key` = 'active_session'", tuple(columns.values()))


STATUSES = ["pending", "processing", "ready_for_review", "completed", "failed"]


# ---------------------------------------------------------------------------
# stop
# ---------------------------------------------------------------------------

def test_stop_keeps_every_row_and_returns_processing_rows_to_pending(db):
    _seed(db, STATUSES, run_id="run-a")
    _review_work(db, 2)
    before = _review_work_counts(db)
    _set_state(db, status="pre_caching", pause_requested=1, current_product_name="Product 1", run_id="run-a")

    result = db.stop_run(worker_active=False)

    rows = _queue(db)
    assert len(rows) == len(STATUSES), "stop must not delete queue rows"
    assert [rows[ROWS[i]]["status"] for i in range(5)] == ["pending", "pending", "ready_for_review", "completed",
                                                           "failed"]
    released = rows[ROWS[1]]
    assert released["worker_id"] is None and released["lease_until"] is None
    assert _review_work_counts(db) == before == {"curation_candidates": 2, "review_decisions": 1,
                                                 "rejected_images": 1, "resolved_products": 1}
    state = db.get_automation_state()
    assert state["status"] == "curation_pending"            # a row still waits for review
    assert (state["stop_requested"], state["pause_requested"], state["current_product_name"]) == (0, 0, "")
    assert state["run_id"] == "run-a"                        # the finished run's progress stays readable
    assert result["released"] == 1 and result["stop_requested"] is False
    assert result["status"] == "curation_pending"
    assert result["queue"]["pending"] == 2 and result["queue"]["ready_for_review"] == 1
    assert result["queue"]["total"] == 5


def test_stop_without_review_rows_settles_to_idle(db):
    _seed(db, ["processing", "completed", "failed"])
    _set_state(db, status="pre_caching")
    result = db.stop_run(worker_active=False)
    assert db.get_automation_state()["status"] == "idle"
    assert result["status"] == "idle" and result["released"] == 1


def test_stop_during_the_enqueue_is_a_request_the_worker_honours(db):
    """No worker yet (the lock says STARTING): nothing changes except the request."""
    _seed(db, STATUSES)
    _set_state(db, status="starting")
    result = db.stop_run(worker_active=True)
    state = db.get_automation_state()
    assert state["stop_requested"] == 1 and state["status"] == "starting"
    assert [r["status"] for r in _queue(db).values()] == STATUSES      # untouched, nothing deleted
    assert result["released"] == 0 and result["stop_requested"] is True

    # the next run from the run button starts clean
    assert db.prepare_run() is True
    assert db.get_automation_state()["stop_requested"] == 0


# ---------------------------------------------------------------------------
# reset («إصلاح تشغيل عالق»)
# ---------------------------------------------------------------------------

def test_reset_clears_a_stuck_run_but_never_review_work(db):
    _seed(db, STATUSES, run_id="run-old")
    _review_work(db, 2)
    _review_work(db, 3)
    before = _review_work_counts(db)
    _set_state(db, status="pre_caching", pause_requested=1, stop_requested=1, run_id="run-old",
               notice="PROVIDER_DOWN: search providers unavailable", total_items=5, processed_items=3,
               current_product_name="Product 1")

    result = db.reset_run(worker_active=False)

    rows = _queue(db)
    assert len(rows) == 5
    assert [rows[ROWS[i]]["status"] for i in range(5)] == ["pending", "pending", "ready_for_review", "completed",
                                                           "failed"]
    assert _review_work_counts(db) == before
    state = db.get_automation_state()
    assert state["status"] == "curation_pending"
    assert (state["pause_requested"], state["stop_requested"]) == (0, 0)
    assert state["run_id"] is None and state["notice"] is None
    assert (state["total_items"], state["processed_items"], state["current_product_name"]) == (0, 0, "")
    assert result["released"] == 1


def test_reset_while_an_enqueue_may_still_run_requests_a_stop(db):
    _seed(db, ["pending"])
    _set_state(db, status="starting")
    result = db.reset_run(worker_active=True)
    state = db.get_automation_state()
    assert state["stop_requested"] == 1 and state["status"] == "idle"
    assert result["stop_requested"] is True
    assert len(_queue(db)) == 1


# ---------------------------------------------------------------------------
# progress per run
# ---------------------------------------------------------------------------

def test_progress_counts_only_the_rows_of_this_run(db):
    # 30 rows from earlier runs: done, waiting for review, failed
    old = ["completed"] * 20 + ["ready_for_review"] * 6 + ["failed"] * 4
    for i, status in enumerate(old):
        row = 940001 + i
        db.add_to_queue(row, "", f"Old {i}", "Brand", "q", sku_key=f"{SKU}old{i}")
        _sql(db, "UPDATE automation_queue SET status = %s, run_id = 'run-last-week' WHERE `row_number` = %s",
             (status, row))

    assert db.prepare_run() is True
    state = db.get_automation_state()
    assert state["status"] == "starting" and state["run_id"] is None and state["total_items"] == 0

    # this enqueue: 5 new rows, 2 old failed rows queued again, 1 old review row the upsert keeps as it is
    for i in range(5):
        db.add_to_queue(ROWS[i], "", f"New {i}", "Brand", "q", sku_key=f"{SKU}new{i}")
    for i in (26, 27):
        db.add_to_queue(940001 + i, "", f"Old {i}", "Brand", "q", sku_key=f"{SKU}old{i}")
    db.add_to_queue(940001 + 20, "", "Old 20", "Brand", "q", sku_key=f"{SKU}old20")

    run_id = db.new_run_id()
    assert db.begin_run(run_id) == 7
    state = db.get_automation_state()
    assert state["run_id"] == run_id and state["total_items"] == 7

    stats = db.get_run_statistics(run_id)
    assert (stats["total"], stats["processed"], stats["pending"]) == (7, 0, 7)       # starts at 0%, not ~94%
    assert db.get_queue_statistics()["total"] == 35                                   # the whole queue
    kept = _sql(db, "SELECT status, run_id FROM automation_queue WHERE `row_number` = %s", (940001 + 20,))[0]
    assert kept == {"status": "ready_for_review", "run_id": "run-last-week"}

    # the worker finishes three rows
    for status in ("ready_for_review", "completed", "failed"):
        task = db.fetch_next_task("host:9")
        assert task["run_id"] == run_id
        assert db.update_task_status(task["id"], status, claim_id=task["worker_id"])
    stats = db.get_run_statistics(run_id)
    assert (stats["total"], stats["processed"], stats["ready_for_review"], stats["completed"], stats["failed"],
            stats["pending"]) == (7, 3, 1, 1, 1, 4)


def test_refresh_state_writes_the_run_numbers(db):
    import main

    _seed(db, ["ready_for_review", "completed", "pending", "pending"], run_id="run-b")
    db.add_to_queue(ROWS[5], "", "Other", "Brand", "q", sku_key=f"{SKU}other")      # not part of run-b
    _sql(db, "UPDATE automation_queue SET status = 'completed', run_id = 'run-a' WHERE `row_number` = %s", (ROWS[5],))
    main._refresh_state("pre_caching", run_id="run-b")
    state = db.get_automation_state()
    assert (state["total_items"], state["processed_items"], state["success_count"], state["failed_count"]) == \
        (4, 2, 2, 0)


# ---------------------------------------------------------------------------
# curation_pending ends when the last review row is decided
# ---------------------------------------------------------------------------

def test_curation_pending_becomes_idle_after_the_last_review(db):
    _seed(db, ["ready_for_review", "ready_for_review", "failed"])
    _set_state(db, status="curation_pending", current_product_name="x")

    assert db.update_task_status_by_row(ROWS[0], "completed", sku_key=f"{SKU}0")
    assert db.get_automation_state()["status"] == "curation_pending"          # one row still waits

    # a rejection sends the row back to the queue: nothing waits for review any more
    assert db.update_task_status_by_row(ROWS[1], "pending", "rejected by reviewer: WRONG_SIZE",
                                        failure_code="REJECTED", sku_key=f"{SKU}1")
    state = db.get_automation_state()
    assert state["status"] == "idle" and state["current_product_name"] == ""


def test_review_decisions_do_not_touch_a_running_state(db):
    _seed(db, ["ready_for_review"])
    _set_state(db, status="pre_caching")
    assert db.update_task_status_by_row(ROWS[0], "completed", sku_key=f"{SKU}0")
    assert db.get_automation_state()["status"] == "pre_caching"


# ---------------------------------------------------------------------------
# enqueue failure: lock released at once, Arabic error in the state
# ---------------------------------------------------------------------------

@pytest.fixture
def enqueue_env(monkeypatch, tmp_path):
    import config
    import google_sheets
    import main

    monkeypatch.chdir(tmp_path)
    (tmp_path / "temp").mkdir()
    monkeypatch.setattr(main, "load_run_config", lambda: None)
    monkeypatch.setattr(google_sheets, "clear_cache", lambda: None)
    monkeypatch.setattr(config, "ROW_FILTER", "")
    monkeypatch.setattr(config, "SPREADSHEET_NAME_OR_URL", "My Products Sheet")
    lock = tmp_path / "temp" / "pipeline.lock"
    lock.write_text("STARTING")
    return main, config, google_sheets, lock


def test_bad_row_filter_releases_the_lock_and_reports_in_arabic(db, enqueue_env, monkeypatch):
    main, config, google_sheets, lock = enqueue_env
    monkeypatch.setattr(config, "ROW_FILTER", "5-x")
    monkeypatch.setattr(google_sheets, "get_sheets_client", lambda: pytest.fail("the sheet must not be opened"))
    _set_state(db, status="starting")

    with pytest.raises(SystemExit) as exc:
        main.run_enqueue_mode()

    assert exc.value.code == 1
    assert not lock.exists(), "the STARTING lock must be released at once"
    state = db.get_automation_state()
    assert state["status"] == "error"
    assert state["notice"].startswith("ENQUEUE_FAILED: فلتر الصفوف غير صالح: «5-x»")


def test_missing_sheet_releases_the_lock_and_names_the_sheet(db, enqueue_env, monkeypatch):
    main, config, google_sheets, lock = enqueue_env
    monkeypatch.setattr(google_sheets, "get_sheets_client", lambda: object())
    monkeypatch.setattr(google_sheets, "open_worksheet", lambda client, name: None)

    with pytest.raises(SystemExit) as exc:
        main.run_enqueue_mode()

    assert exc.value.code == 1 and not lock.exists()
    notice = db.get_automation_state()["notice"]
    assert notice.startswith("ENQUEUE_FAILED: لم يُعثر على الشيت «My Products Sheet»")
    assert "لم يتغير الطابور" in notice


def test_enqueue_failure_clears_a_stop_request_made_while_the_sheet_was_read(db, enqueue_env, monkeypatch):
    """Review fix: the owner pressed stop during the enqueue and the enqueue then failed. The run is over, so the
    request must not survive: `main.py --enqueue && main.py --worker` by hand (README) never calls prepare_run, and
    its worker stopped before any product on the stale request."""
    main, config, google_sheets, lock = enqueue_env
    monkeypatch.setattr(config, "ROW_FILTER", "5-x")
    _set_state(db, status="starting")
    db.stop_run(worker_active=True)
    assert db.get_automation_state()["stop_requested"] == 1

    with pytest.raises(SystemExit):
        main.run_enqueue_mode()

    state = db.get_automation_state()
    assert state["status"] == "error" and state["stop_requested"] == 0
    assert state["notice"].startswith("ENQUEUE_FAILED: ")


def test_enqueue_failure_keeps_a_lock_held_by_a_process(offline, enqueue_env, monkeypatch):
    """The nightly runner holds the lock with its own PID during the enqueue and releases it itself."""
    import local_cache_db

    main, config, google_sheets, lock = enqueue_env
    lock.write_text("4242")
    monkeypatch.setattr(config, "ROW_FILTER", "9-2")
    monkeypatch.setattr(local_cache_db, "update_automation_state", lambda *a, **k: True)
    with pytest.raises(SystemExit):
        main.run_enqueue_mode()
    assert lock.read_text() == "4242"


def test_successful_enqueue_starts_a_run(db, enqueue_env, monkeypatch):
    main, config, google_sheets, lock = enqueue_env
    products = [{"row_number": ROWS[0], "product_name": "Laban Up 180ml", "brand": "Al Rawabi", "barcode": "",
                 "existing_image_link": ""},
                {"row_number": ROWS[1], "product_name": "Fresh Milk 1L", "brand": "Almarai", "barcode": "",
                 "existing_image_link": ""}]
    monkeypatch.setattr(config, "BRAND_FILTER", "")
    monkeypatch.setattr(config, "FORCE_OVERWRITE_IMAGES", False)
    monkeypatch.setattr(google_sheets, "get_sheets_client", lambda: object())
    monkeypatch.setattr(google_sheets, "open_worksheet", lambda client, name: object())
    monkeypatch.setattr(google_sheets, "get_products", lambda ws: (products, 9))
    monkeypatch.setattr(google_sheets, "get_brand_mappings", lambda *a: {})
    db.prepare_run()

    main.run_enqueue_mode()

    state = db.get_automation_state()
    assert state["run_id"] and state["total_items"] == 2
    assert {r["run_id"] for r in _queue(db).values()} == {state["run_id"]}
    assert lock.read_text() == "STARTING"          # the worker takes the lock over; the enqueue leaves it


# ---------------------------------------------------------------------------
# the worker honours the stop request
# ---------------------------------------------------------------------------

@pytest.fixture
def worker(offline, monkeypatch, tmp_path):
    """run_worker_mode with the sheet, the queue and the search replaced by recorders."""
    import time as time_mod

    import google_sheets
    import local_cache_db
    import main

    monkeypatch.chdir(tmp_path)
    real_sleep = time_mod.sleep
    monkeypatch.setattr(time_mod, "sleep", lambda s: real_sleep(0.005))
    rec = {"state": {"pause_requested": 0, "stop_requested": 0, "run_id": "run-w"}, "claims": [], "worked": [],
           "stops": [], "run_stats": [], "verifier": 0, "tasks": []}
    monkeypatch.setattr(main, "_another_worker_running", lambda lock: False)
    monkeypatch.setattr(main, "load_run_config", lambda: None)

    def verifier():
        rec["verifier"] += 1
        return ""

    monkeypatch.setattr(main, "check_verifier", verifier)
    monkeypatch.setattr(local_cache_db, "resume_automation", lambda: True)
    monkeypatch.setattr(local_cache_db, "get_automation_state", lambda: dict(rec["state"]))
    monkeypatch.setattr(local_cache_db, "update_automation_state", lambda *a, **k: True)
    monkeypatch.setattr(local_cache_db, "stop_run", lambda worker_active=False: rec["stops"].append(worker_active))
    monkeypatch.setattr(local_cache_db, "get_run_statistics",
                        lambda run_id: rec["run_stats"].append(run_id) or
                        {"total": 6, "completed": 0, "failed": 0, "ready_for_review": len(rec["worked"])})
    monkeypatch.setattr(local_cache_db, "get_queue_statistics", lambda: pytest.fail("the run's numbers are used"))
    monkeypatch.setattr(local_cache_db, "get_ready_for_review_count", lambda: 0)
    monkeypatch.setattr(google_sheets, "get_sheets_client", lambda: object())
    monkeypatch.setattr(google_sheets, "open_worksheet", lambda client, name: object())
    monkeypatch.setattr(google_sheets, "find_link_column", lambda ws: 7)
    monkeypatch.setattr(google_sheets, "get_brand_mappings", lambda *a: {})
    monkeypatch.setattr(google_sheets, "init_async_queue", lambda *a: None)
    monkeypatch.setattr(google_sheets, "stop_async_queue", lambda: None)
    monkeypatch.setattr(local_cache_db, "count_open_tasks", lambda: len(rec["tasks"]))
    monkeypatch.setattr(main, "pre_cache_product_candidates",
                        lambda task, *a, **k: rec["worked"].append(task["row_number"]) or "success")
    rec["tasks"] = [{"id": i, "row_number": 100 + i, "product_name": f"P{i}"} for i in range(6)]
    os.makedirs("temp", exist_ok=True)
    return main, local_cache_db, rec


def test_stop_requested_during_the_enqueue_stops_the_worker_before_any_product(worker, monkeypatch):
    main, local_cache_db, rec = worker
    rec["state"]["stop_requested"] = 1
    monkeypatch.setattr(local_cache_db, "fetch_next_task", lambda worker_id: pytest.fail("no task may be claimed"))

    main.run_worker_mode()

    assert rec["worked"] == [] and rec["verifier"] == 0
    assert rec["stops"] == [False]            # rows back to pending, request cleared, state settled
    assert not os.path.exists(main.LOCK_FILE)


def test_worker_stops_between_products(worker, monkeypatch):
    main, local_cache_db, rec = worker

    def fetch(worker_id):
        rec["claims"].append(worker_id)
        task = rec["tasks"].pop(0)
        rec["state"]["stop_requested"] = 1        # the owner pressed stop while the first product is searched
        return task

    monkeypatch.setattr(local_cache_db, "fetch_next_task", fetch)

    main.run_worker_mode()

    assert len(rec["claims"]) == 1 and rec["worked"] == [100]     # the product in progress is finished
    assert len(rec["tasks"]) == 5                                  # the rest stay in the queue
    assert rec["stops"] == [False]
    assert rec["run_stats"] and set(rec["run_stats"]) == {"run-w"}  # progress from this run's rows only


def test_a_stop_request_arriving_as_the_worker_finishes_does_not_outlive_the_run(worker, monkeypatch):
    """Review fix: the queue ran empty just as a stop was recorded (e.g. PHP could not kill the worker). The final
    state write clears the request, so it cannot stop the next worker started by hand before any product."""
    main, local_cache_db, rec = worker
    states = []
    monkeypatch.setattr(local_cache_db, "update_automation_state",
                        lambda status, **kw: states.append(dict(kw, status=status)) or True)
    monkeypatch.setattr(local_cache_db, "fetch_next_task",
                        lambda worker_id: rec["tasks"].pop(0) if rec["tasks"] else None)

    def open_tasks():
        if not rec["tasks"]:
            rec["state"]["stop_requested"] = 1        # recorded after the worker's last look at the state
        return len(rec["tasks"])

    monkeypatch.setattr(local_cache_db, "count_open_tasks", open_tasks)

    main.run_worker_mode()

    assert len(rec["worked"]) == 6 and rec["stops"] == []          # a normal end, not a stop
    assert states[-1]["status"] == "idle" and states[-1]["stop_requested"] == 0


# ---------------------------------------------------------------------------
# cli_bridge run_control
# ---------------------------------------------------------------------------

@pytest.fixture
def bridge(monkeypatch, tmp_path):
    import cli_bridge

    monkeypatch.setattr(cli_bridge, "LOG_PATH", str(tmp_path / "search.log"))
    return cli_bridge


def _run_main(cli_bridge, capsys, params):
    arg = base64.b64encode(json.dumps(params).encode("utf-8")).decode("ascii")
    code = cli_bridge.main(["cli_bridge.py", "run_control", arg])
    lines = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert len(lines) == 1, lines
    return code, json.loads(lines[0])


def test_bridge_action_is_registered_last(bridge):
    assert list(bridge.ACTIONS)[-1] == "run_control"
    assert bridge.ACTIONS["run_control"] is bridge.action_run_control


def test_bridge_stop_contract_against_the_database(db, bridge, capsys):
    _seed(db, STATUSES)
    _review_work(db, 2)
    code, out = _run_main(bridge, capsys, {"op": "stop", "worker": "killed"})
    assert code == 0 and out["status"] == "success" and out["op"] == "stop"
    assert out["released"] == 1 and out["stop_requested"] is False and out["state"] == "curation_pending"
    assert out["queue"]["total"] == 5
    assert out["message"].startswith("تم إيقاف التشغيل. أُعيد 1 صف كان قيد المعالجة إلى الانتظار")
    assert "لم يُحذف أي صف: 1 منتج بانتظار المراجعة، 2 صف في الانتظار، 1 معتمد، 1 فاشل." in out["message"]
    assert len(_queue(db)) == 5 and _review_work_counts(db)["curation_candidates"] == 2

    code, out = _run_main(bridge, capsys, {"op": "stop", "worker": "starting"})
    assert out["stop_requested"] is True and "يقرأ الشيت" in out["message"]
    assert db.get_automation_state()["stop_requested"] == 1

    code, out = _run_main(bridge, capsys, {"op": "reset", "worker": "none"})
    assert out["status"] == "success" and out["op"] == "reset"
    assert out["message"].startswith("تم إصلاح حالة التشغيل")
    assert "لم يُحذف أي صف" in out["message"]
    assert db.get_automation_state()["stop_requested"] == 0

    code, out = _run_main(bridge, capsys, {"op": "start"})
    assert out["status"] == "success" and db.get_automation_state()["status"] == "starting"


@pytest.mark.parametrize("worker, expected", [
    ("starting", "سُجل طلب الإيقاف: التشغيل ما زال يقرأ الشيت"),
    ("running", "سُجل طلب الإيقاف: سيتوقف العامل بعد إنهاء المنتجات الجارية"),
    ("killed", "تم إيقاف التشغيل. أُعيد 2 صف"),
    ("none", "لم يكن هناك تشغيل نشط. أُعيد 2 صف عالق"),
])
def test_bridge_stop_messages(offline, bridge, monkeypatch, worker, expected):
    import local_cache_db

    calls = []
    queue = {"total": 9, "pending": 4, "processing": 0, "ready_for_review": 3, "completed": 1, "failed": 1}
    monkeypatch.setattr(local_cache_db, "stop_run", lambda worker_active=False: calls.append(worker_active) or
                        {"released": 2, "stop_requested": worker_active, "status": "idle", "queue": queue})
    out = bridge.action_run_control({"op": "stop", "worker": worker})
    assert out["status"] == "success" and out["message"].startswith(expected)
    assert "لم يُحذف أي صف" in out["message"]
    assert calls == [worker in ("starting", "running")]


@pytest.mark.parametrize("worker, stop_requested, expected", [
    ("none", False, None),
    ("killed", False, "وأُنهي العامل الذي كان ما زال يعمل."),
    ("starting", True, "وسُجل طلب إيقاف للتشغيل الذي كان يقرأ الشيت"),
    ("running", True, "وسُجل طلب إيقاف للعامل الذي تعذر إنهاؤه"),       # PHP could not kill it
])
def test_bridge_reset_messages(offline, bridge, monkeypatch, worker, stop_requested, expected):
    import local_cache_db

    calls = []
    queue = {"total": 3, "pending": 1, "processing": 0, "ready_for_review": 1, "completed": 1, "failed": 0}
    monkeypatch.setattr(local_cache_db, "reset_run", lambda worker_active=False: calls.append(worker_active) or
                        {"released": 1, "stop_requested": worker_active, "status": "curation_pending", "queue": queue})
    out = bridge.action_run_control({"op": "reset", "worker": worker})
    assert calls == [stop_requested] and out["stop_requested"] is stop_requested
    assert out["message"].startswith("تم إصلاح حالة التشغيل: حُذف ملف القفل")
    assert out["message"].endswith("لم يُحذف أي صف: 1 منتج بانتظار المراجعة، 1 صف في الانتظار، 1 معتمد، 0 فاشل.")
    if expected:
        assert expected in out["message"]


def test_bridge_rejects_unknown_ops_and_reports_database_errors(offline, bridge, monkeypatch, capsys):
    import local_cache_db

    assert bridge.action_run_control({"op": "wipe"})["status"] == "error"
    assert bridge.action_run_control({"op": "stop", "worker": "zombie"})["status"] == "error"

    def broken(worker_active=False):
        raise RuntimeError("Lost connection to MySQL server; secret=abc")

    monkeypatch.setattr(local_cache_db, "reset_run", broken)
    code, out = _run_main(bridge, capsys, {"op": "reset", "worker": "none"})
    assert out["status"] == "failed" and "لم يتغير أي صف" in out["error"]
    assert "secret" not in json.dumps(out) and "Traceback" not in json.dumps(out)

    monkeypatch.setattr(local_cache_db, "prepare_run", lambda: False)
    out = bridge.action_run_control({"op": "start"})
    assert out["status"] == "failed" and "لم يبدأ أي تشغيل" in out["error"]


# ---------------------------------------------------------------------------
# nightly runner: a new run clears stale stop / pause requests before its enqueue
# ---------------------------------------------------------------------------

def test_nightly_prepares_the_run_before_the_enqueue(offline, monkeypatch, tmp_path):
    import importlib.util
    from pathlib import Path

    import local_cache_db
    import main

    monkeypatch.chdir(tmp_path)
    spec = importlib.util.spec_from_file_location(
        "run_nightly", Path(__file__).resolve().parents[1] / "scripts" / "run_nightly.py")
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    order = []
    monkeypatch.setattr(main, "load_run_config", main.load_run_config)
    monkeypatch.setattr(main, "_another_worker_running", lambda lock: False)
    monkeypatch.setattr(local_cache_db, "prepare_run", lambda: order.append("prepare") or True)
    monkeypatch.setattr(local_cache_db, "get_automation_state", lambda: {"status": "idle"})
    monkeypatch.setattr(main, "run_enqueue_mode", lambda: order.append("enqueue"))

    def fake_worker():
        order.append("worker")
        os.remove(runner.LOCK_FILE)

    monkeypatch.setattr(main, "run_worker_mode", fake_worker)
    assert runner.run() == 0
    assert order == ["prepare", "enqueue", "worker"]
