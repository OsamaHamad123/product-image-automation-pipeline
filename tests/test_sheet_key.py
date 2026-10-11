"""sheet_key: every cache, DB row and pending sheet write knows which spreadsheet and tab it belongs to.

Switching the Google Sheet (or its tab) used to leave stale data behind: products_cache.json was served for an hour
whatever sheet was configured, and DB rows keyed by row number only let row 19 of the new sheet pick up old row 19's
candidates, queue row and failure; worst of all a pending sheet write for the old sheet could land in the new sheet's
row. Here: the key itself (spreadsheet id + gid, never names), the caches, the bridge, the writers, the outbox flush,
the one-time backfill of legacy rows and a run whose sheet changes while it is running.
"""

import json
import types
import uuid

import pytest

from test_sheets_safety import SHEET, FakeRedis, FakeWorksheet, RecordingQueue, _final_status, _outbox_row

PRODUCTS = [["Barcode", "Product Name", "Brand", "Drive Image Link"],
            ["6281007000024", "Almarai Fresh Milk 1L", "Almarai", ""]]


def _ws(rows=PRODUCTS, spreadsheet="SHEET-A", gid=0, title="Products"):
    ws = FakeWorksheet(rows, title=title)
    ws.spreadsheet.id = spreadsheet
    ws.id = gid
    return ws


@pytest.fixture
def sheets(monkeypatch, tmp_path):
    """google_sheets with its caches in tmp_path, sheet A configured in the process and in .env (google_sheets.env)."""
    import config
    import google_sheets
    import local_cache_db
    monkeypatch.setattr(google_sheets, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(google_sheets, "_opened", {"source": None, "sheet_key": None})
    monkeypatch.setattr(google_sheets, "_frozen_reason", None)
    monkeypatch.setattr(google_sheets, "_reported_foreign_keys", set())
    monkeypatch.setattr(google_sheets, "_sleep", lambda seconds: None)
    google_sheets._header_cache.clear()
    google_sheets.reported = []
    monkeypatch.setattr(google_sheets, "_outcome_hook", google_sheets.reported.append)
    monkeypatch.setattr(local_cache_db, "_run_sheet_key", None)
    monkeypatch.setattr(config, "SPREADSHEET_NAME_OR_URL", "sheet A")
    monkeypatch.setattr(config, "SPREADSHEET_TAB_NAME", "Products")
    env = {"SPREADSHEET_NAME_OR_URL": "sheet A", "SPREADSHEET_TAB_NAME": "Products"}
    monkeypatch.setattr(config, "env_sheet_setting", lambda env_path=".env": dict(env))
    google_sheets.env = env          # tests edit this dict to "save another sheet in .env"
    return google_sheets


@pytest.fixture
def gs(offline, sheets):
    """The same, with every socket refused (no Google, no database)."""
    return sheets


# ---------------------------------------------------------------------------
# the key
# ---------------------------------------------------------------------------

def test_the_key_is_the_spreadsheet_id_and_the_gid_never_the_names(gs):
    ws = _ws(gid=0)
    assert gs.sheet_key(ws) == "SHEET-A:0"
    ws.title = "Renamed tab"
    assert gs.sheet_key(ws) == "SHEET-A:0"                      # renaming the tab keeps the key
    assert gs.sheet_key(_ws(gid=1234)) == "SHEET-A:1234"
    assert gs.sheet_key(FakeWorksheet(PRODUCTS)) is None        # no spreadsheet id: unknown, not a made-up key
    assert gs.sheet_key(object()) is None


def test_env_sheet_setting_reads_the_file_not_the_process(offline, tmp_path):
    import config
    env = tmp_path / ".env"
    env.write_text('SPREADSHEET_NAME_OR_URL="https://docs/x"\n# SPREADSHEET_TAB_NAME=old\nOTHER=1\n', encoding="utf-8")
    assert config.env_sheet_setting(str(env)) == {"SPREADSHEET_NAME_OR_URL": "https://docs/x"}   # absolute: as is
    assert config.env_sheet_setting(str(tmp_path / "missing.env")) is None


# ---------------------------------------------------------------------------
# products_cache.json and brand_mappings_cache.json
# ---------------------------------------------------------------------------

def test_the_products_cache_is_served_only_to_the_same_sheet_and_tab(gs):
    ws_a = _ws()
    products, link_idx = gs.get_products(ws_a)
    assert [p["product_name"] for p in products] == ["Almarai Fresh Milk 1L"] and link_idx == 3
    cache = json.load(open(gs._cache_path("products_cache.json"), encoding="utf-8"))
    assert cache["sheet_key"] == "SHEET-A:0" and cache["version"] == gs.PRODUCTS_CACHE_VERSION == 3

    ws_a.rows[1][1] = "Edited In Google"                         # the cache hit does not read the sheet
    ws_a.title = "Renamed"
    assert gs.get_products(ws_a)[0][0]["product_name"] == "Almarai Fresh Milk 1L"

    other_tab = _ws([PRODUCTS[0], ["", "Laban 1L", "Almarai", ""]], gid=77)
    assert gs.get_products(other_tab)[0][0]["product_name"] == "Laban 1L"           # miss: read, then overwrite
    assert json.load(open(gs._cache_path("products_cache.json"), encoding="utf-8"))["sheet_key"] == "SHEET-A:77"

    other_sheet = _ws([PRODUCTS[0], ["", "Juice", "Al Rawabi", ""]], spreadsheet="SHEET-B", gid=77)
    assert gs.get_products(other_sheet)[0][0]["product_name"] == "Juice"


def test_cached_products_and_current_key_follow_the_configured_sheet(gs, monkeypatch):
    import config
    ws = _ws()
    gs._note_worksheet(ws, "sheet A", "Products")                   # what open_worksheet does
    gs.get_products(ws)
    assert [p["row_number"] for p in gs.cached_products()] == [2]
    assert gs.current_sheet_key() == "SHEET-A:0"
    gs.clear_cache()                                                 # every sheet write deletes the products cache
    assert gs.current_sheet_key() == "SHEET-A:0"                     # the key survives in its own small file
    gs.get_products(ws)

    monkeypatch.setattr(config, "SPREADSHEET_NAME_OR_URL", "sheet B")
    monkeypatch.setattr(gs, "_opened", {"source": None, "sheet_key": None})   # another process
    assert gs.cached_products() is None                                # rows of another sheet are never served
    assert gs.current_sheet_key() is None
    monkeypatch.setattr(config, "SPREADSHEET_NAME_OR_URL", "sheet A")
    monkeypatch.setattr(config, "SPREADSHEET_TAB_NAME", "Other tab")
    assert gs.cached_products() is None


def test_a_worksheet_opened_before_the_sheet_changed_does_not_write_the_cache(gs):
    ws = _ws()
    gs._note_worksheet(ws, "sheet A", "Products")
    gs.env["SPREADSHEET_NAME_OR_URL"] = "sheet B"                     # saved in the settings meanwhile
    products, _ = gs.get_products(ws)
    assert products                                                    # the caller still gets its rows
    import os
    assert not os.path.exists(gs._cache_path("products_cache.json"))


def test_the_brand_mapping_cache_is_per_spreadsheet(gs):
    opened = []

    class Book:
        def __init__(self, name):
            self.name = name

        def worksheet(self, title):
            return types.SimpleNamespace(get_all_values=lambda: [
                ["Brand", "Synonyms"], [f"Brand of {self.name}", ""]])

    client = types.SimpleNamespace(open=lambda name: opened.append(name) or Book(name))
    assert "Brand of sheet A" in json.dumps(gs._read_brand_sheet(client, "sheet A"))
    gs._read_brand_sheet(client, "sheet A")
    assert opened == ["sheet A"]                                         # hit
    assert "Brand of sheet B" in json.dumps(gs._read_brand_sheet(client, "sheet B"))
    assert opened == ["sheet A", "sheet B"]                              # another sheet: never the old mappings


# ---------------------------------------------------------------------------
# the bridge
# ---------------------------------------------------------------------------

def test_bridge_get_products_returns_the_sheet_key_and_backfills(gs, monkeypatch):
    import cli_bridge
    import local_cache_db
    ws = _ws()
    backfilled = []
    monkeypatch.setattr(cli_bridge, "_open_sheet", lambda: ws)
    monkeypatch.setattr(gs, "get_products", lambda worksheet: ([], -1))
    monkeypatch.setattr(local_cache_db, "get_product_failures", lambda: {})
    monkeypatch.setattr(local_cache_db, "backfill_sheet_key", backfilled.append)
    out = cli_bridge.action_get_products({})
    assert out["status"] == "success" and out["sheet_tab"] == "Products" and out["sheet_key"] == "SHEET-A:0"
    assert backfilled == ["SHEET-A:0"]


def test_bridge_refuses_an_approval_shown_from_another_sheet(gs, monkeypatch):
    import cli_bridge
    monkeypatch.setattr(gs, "current_sheet_key", lambda: "SHEET-B:0")
    monkeypatch.setattr(gs, "init_async_queue", lambda *a: pytest.fail("nothing may be queued"))
    params = {"image_url": "https://x.ae/a.jpg", "product_name": "Milk", "row_number": 19, "sheet_key": "SHEET-A:0"}
    out = cli_bridge.action_select_image(params)
    assert out["status"] == "failed" and out["error_code"] == "sheet_changed"
    assert cli_bridge._sheet_refusal({"sheet_key": "SHEET-B:0"}) is None
    assert cli_bridge._sheet_refusal({}) is None


# ---------------------------------------------------------------------------
# writers stamp the key
# ---------------------------------------------------------------------------

def test_outbox_rows_carry_the_sheet_key(gs, monkeypatch, fake_connection):
    conn = fake_connection(lambda sql, params: None)
    queue = gs.SQLiteTransactionQueue.__new__(gs.SQLiteTransactionQueue)
    queue._connect = lambda: conn
    queue._setup_schema()
    ddl = " ".join(sql for sql, _ in conn.executed)
    assert "ADD COLUMN IF NOT EXISTS sheet_key VARCHAR(128) NULL" in ddl
    assert "ADD INDEX IF NOT EXISTS idx_sheet_updates_sheet (sheet_key)" in ddl

    queue.append_update(3, None, "https://res/a.png", col_key="link", key_name="Milk", sheet_key="SHEET-A:0")
    sql, params = conn.executed[-1]
    assert "sheet_key" in sql and params[8] == "SHEET-A:0" and params[-1] is None

    monkeypatch.setattr(gs, "current_sheet_key", lambda: "SHEET-A:9")      # no key passed: the configured sheet
    queue.append_update(3, None, "https://res/a.png", col_key="link")
    assert conn.executed[-1][1][8] == "SHEET-A:9"


def test_link_writes_take_the_key_of_the_worksheet_through_both_channels(gs, monkeypatch):
    import sync_worker
    ws = _ws(SHEET, gid=5)
    queue = RecordingQueue()
    monkeypatch.setattr(gs, "_queue", queue)
    monkeypatch.setattr(gs, "_worker", object())
    monkeypatch.setattr(gs, "_get_redis", lambda: None)
    assert gs.update_image_link(ws, 2, 3, "https://res/milk.png", barcode="6281007000024") is True
    assert queue.appended[-1][1]["sheet_key"] == "SHEET-A:5"

    r = FakeRedis(heartbeat=True)
    monkeypatch.setattr(gs, "_get_redis", lambda: r)
    assert gs.update_image_link(ws, 2, 3, "https://res/milk.png", barcode="6281007000024") is True
    assert json.loads(r.kv["product:data:row_2"])["sheet_key"] == "SHEET-A:5"
    # the same row number of another sheet is never merged into that payload: it goes to the outbox on its own
    before = len(queue.appended)
    assert gs.update_image_link(_ws(SHEET, spreadsheet="SHEET-B", gid=5), 2, 3, "https://res/other.png",
                                barcode="6281007000024") is True
    assert len(queue.appended) == before + 1 and queue.appended[-1][1]["sheet_key"] == "SHEET-B:5"

    forwarded = RecordingQueue()
    assert sync_worker.run_sync_cycle(None, r, queue=forwarded, flush=False) == 1
    assert forwarded.appended[-1][1]["sheet_key"] == "SHEET-A:5"


def test_queued_barcode_and_link_writes_pass_an_explicit_key(gs, monkeypatch):
    queue = RecordingQueue()
    queue.append_update = lambda *a, **k: queue.appended.append((a, k)) or len(queue.appended)
    monkeypatch.setattr(gs, "_queue", queue)
    gs.queue_barcode_writes([{"row_number": 9, "value": "6281007000024", "product_name": "Milk",
                              "sheet_key": "SHEET-A:0"}])
    gs.queue_link_writes([{"row_number": 9, "value": "https://res/a.png", "product_name": "Milk"}])
    assert queue.appended[0][1]["sheet_key"] == "SHEET-A:0"
    assert "sheet_key" not in queue.appended[1][1]          # append_update stamps the configured sheet itself


# ---------------------------------------------------------------------------
# the flush writes only the rows of the worksheet it opened
# ---------------------------------------------------------------------------

def _keyed_responder(pending):
    def respond(sql, params):
        if "GET_LOCK" in sql:
            return [{"got": 1}]
        if "RELEASE_LOCK" in sql:
            return [{"released": 1}]
        if "sync_status = 'SYNCED'" in sql:
            return []
        if "GROUP BY sheet_key" in sql:
            counts = {}
            for r in pending:
                if r.get("sheet_key") and r["sheet_key"] != params[-1]:
                    counts[r["sheet_key"]] = counts.get(r["sheet_key"], 0) + 1
            return [{"sheet_key": k, "n": n} for k, n in counts.items()]
        if sql.startswith("SELECT"):
            key = params[1]
            return [r for r in pending if r.get("sheet_key") in (None, key)]
        return None
    return respond


def test_the_flush_skips_writes_queued_for_another_sheet(gs, fake_connection, caplog):
    ws = _ws(SHEET, gid=0)
    pending = [
        dict(_outbox_row(1, 2, "https://res/milk.png", barcode="6281007000024"), sheet_key="SHEET-A:0"),
        dict(_outbox_row(2, 3, "https://res/old-sheet.png", name="Almarai Laban 1L"), sheet_key="SHEET-OLD:0"),
        _outbox_row(3, 4, "https://res/juice.png", name="Al Rawabi Juice"),        # legacy NULL: today's behaviour
    ]
    conn = fake_connection(_keyed_responder(pending))

    class Queue:
        def _connect(self):
            return conn

    with caplog.at_level("WARNING", logger="google_sheets"):
        gs.flush_outbox(ws, Queue())
        gs.flush_outbox(ws, Queue())
    select = next((sql, params) for sql, params in conn.executed
                  if sql.startswith("SELECT id, `row_number`"))
    assert "(sheet_key IS NULL OR sheet_key = %s)" in select[0] and select[1][1] == "SHEET-A:0"
    written = {d["range"] for body in ws.sent_bodies for d in body["data"]}
    assert "'Products'!D3" not in written and {"'Products'!D2", "'Products'!D4"} <= written
    status = _final_status(conn)
    assert 2 not in status                                       # the other sheet's write stays PENDING, untouched
    foreign = [r for r in caplog.records if "SHEET-OLD:0" in r.getMessage()]
    assert len(foreign) == 1 and "لن تُكتب" in foreign[0].getMessage()      # logged once, in Arabic


def test_due_count_for_the_flush_timer_is_per_sheet(gs, fake_connection, monkeypatch):
    conn = fake_connection(lambda sql, params: [{"n": 0}])
    monkeypatch.setattr(gs, "_db_connect", lambda: conn)
    monkeypatch.setattr(gs, "_queue", None)
    gs.outbox_due_count(now=100, sheet_key="SHEET-A:0")
    sql, params = conn.executed[-1]
    assert sql.endswith("AND (sheet_key IS NULL OR sheet_key = %s)") and params == (100, "SHEET-A:0")


# ---------------------------------------------------------------------------
# MariaDB: writers, scoping and the one-time backfill
# ---------------------------------------------------------------------------

ROW = 987600


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
def db(sheets, mariadb_or_skip, monkeypatch):
    db = mariadb_or_skip
    monkeypatch.setattr(db, "_backfill_checked", set())
    name = f"SheetKey Product {uuid.uuid4().hex[:6]}"

    def wipe():
        _sql(db, "DELETE FROM automation_queue WHERE `row_number` BETWEEN %s AND %s", (ROW, ROW + 50))
        _sql(db, "DELETE FROM curation_candidates WHERE `row_number` BETWEEN %s AND %s", (ROW, ROW + 50))
        _sql(db, "DELETE FROM product_failures WHERE product_name LIKE 'SheetKey Product %%'")

    wipe()
    db.test_name = name
    yield db
    wipe()


def test_db_writers_stamp_the_sheet_key(db):
    name = db.test_name
    db.use_sheet_key("SHEET-A:0")
    assert db.save_curation_candidates(ROW, name, "Brand", [{"url": "https://x.ae/a.jpg", "status": "eligible"}],
                                       sku_key=f"sk-{name}")
    assert db.save_product_failure("", name, "Brand", "NO_RESULTS: x", sku_key=f"sk-{name}")
    db.add_many_to_queue([db.queue_input(ROW, "", name, "Brand", name, sku_key=f"sk-{name}")])
    assert _sql(db, "SELECT sheet_key FROM curation_candidates WHERE `row_number` = %s", (ROW,)) == [
        {"sheet_key": "SHEET-A:0"}]
    assert _sql(db, "SELECT sheet_key FROM product_failures WHERE product_name = %s", (name,)) == [
        {"sheet_key": "SHEET-A:0"}]
    assert _sql(db, "SELECT sheet_key FROM automation_queue WHERE `row_number` = %s", (ROW,)) == [
        {"sheet_key": "SHEET-A:0"}]
    # an explicit key wins over the run's
    assert db.save_curation_candidates(ROW + 1, name, "Brand", [{"url": "https://x.ae/b.jpg"}], sheet_key="SHEET-B:3")
    assert _sql(db, "SELECT sheet_key FROM curation_candidates WHERE `row_number` = %s", (ROW + 1,)) == [
        {"sheet_key": "SHEET-B:3"}]


def test_row_19_of_the_new_sheet_does_not_get_old_row_19s_data(db):
    name = db.test_name
    db.use_sheet_key("SHEET-OLD:0")
    db.save_curation_candidates(ROW + 2, "Old Product", "Brand", [{"url": "https://x.ae/old.jpg"}])
    db.add_many_to_queue([db.queue_input(ROW + 2, "", "Old Product", "Brand", "q", sku_key="sk-old")])
    _sql(db, "UPDATE automation_queue SET status = 'ready_for_review' WHERE `row_number` = %s", (ROW + 2,))

    db.use_sheet_key("SHEET-NEW:0")
    assert db.get_curation_candidates(ROW + 2) == []               # the old sheet's candidates are not this row's
    # the new sheet's row 19 replaces the old one from scratch, even for the same product key
    db.add_many_to_queue([db.queue_input(ROW + 2, "", name, "Brand", "q", sku_key="sk-old")])
    row = _sql(db, "SELECT status, sheet_key, product_name FROM automation_queue WHERE `row_number` = %s", (ROW + 2,))
    assert row == [{"status": "pending", "sheet_key": "SHEET-NEW:0", "product_name": name}]
    # saving the new row's candidates leaves the old sheet's alone
    db.save_curation_candidates(ROW + 2, name, "Brand", [{"url": "https://x.ae/new.jpg"}])
    keys = sorted(r["sheet_key"] for r in _sql(db, "SELECT sheet_key FROM curation_candidates WHERE `row_number` = %s",
                                               (ROW + 2,)))
    assert keys == ["SHEET-NEW:0", "SHEET-OLD:0"]
    assert db.count_open_tasks(sheet_key="SHEET-NEW:0") >= 1


def test_backfill_runs_once(db, monkeypatch):
    name = db.test_name
    saved = _sql(db, "SELECT `value` FROM system_settings WHERE `key` = %s", (db.LEGACY_SHEET_KEY_SETTING,))
    _sql(db, "DELETE FROM system_settings WHERE `key` = %s", (db.LEGACY_SHEET_KEY_SETTING,))
    try:
        _sql(db, "INSERT INTO product_failures (barcode, product_name, brand, error_message, sheet_key) "
                 "VALUES (%s, %s, 'B', 'x', NULL)", (f"ERR_{name}_1", name))
        assert db.backfill_sheet_key("SHEET-FIRST:0") is True
        assert _sql(db, "SELECT sheet_key FROM product_failures WHERE barcode = %s", (f"ERR_{name}_1",)) == [
            {"sheet_key": "SHEET-FIRST:0"}]
        assert _sql(db, "SELECT `value` FROM system_settings WHERE `key` = 'legacy_sheet_key'") == [
            {"value": "SHEET-FIRST:0"}]

        _sql(db, "INSERT INTO product_failures (barcode, product_name, brand, error_message, sheet_key) "
                 "VALUES (%s, %s, 'B', 'x', NULL)", (f"ERR_{name}_2", name))
        monkeypatch.setattr(db, "_backfill_checked", set())             # another process, after a sheet switch
        assert db.backfill_sheet_key("SHEET-SECOND:0") is False
        assert _sql(db, "SELECT sheet_key FROM product_failures WHERE barcode = %s", (f"ERR_{name}_2",)) == [
            {"sheet_key": None}]
        assert _sql(db, "SELECT `value` FROM system_settings WHERE `key` = 'legacy_sheet_key'") == [
            {"value": "SHEET-FIRST:0"}]
        assert db.backfill_sheet_key(None) is False
    finally:
        _sql(db, "DELETE FROM system_settings WHERE `key` = %s", (db.LEGACY_SHEET_KEY_SETTING,))
        if saved:
            _sql(db, "INSERT INTO system_settings (`key`, `value`) VALUES (%s, %s)",
                 (db.LEGACY_SHEET_KEY_SETTING, saved[0]["value"]))


def test_the_worker_claims_and_counts_only_its_sheets_tasks(offline, fake_connection, monkeypatch):
    import local_cache_db
    conn = fake_connection(lambda sql, params: [{"cnt": 0}] if "COUNT" in sql else [])
    monkeypatch.setattr(local_cache_db, "get_db_connection", lambda: conn)
    local_cache_db.fetch_next_task("host:1", sheet_key="SHEET-A:0")
    claim = next((sql, params) for sql, params in conn.executed if sql.startswith("UPDATE automation_queue"))
    assert "AND (q.sheet_key IS NULL OR q.sheet_key = %s) ORDER BY" in claim[0] and claim[1][-1] == "SHEET-A:0"
    local_cache_db.count_open_tasks(sheet_key="SHEET-A:0")
    sql, params = conn.executed[-1]
    assert sql.endswith("(sheet_key IS NULL OR sheet_key = %s)") and params == ("SHEET-A:0",)
    local_cache_db.count_open_tasks()
    assert "sheet_key" not in conn.executed[-1][0]                     # no key: every open task, as before


def test_migrations_are_additive():
    import local_cache_db
    added = [m for m in local_cache_db._SCHEMA_MIGRATIONS if "sheet_key" in m]
    assert len(added) == 6 and all(" ADD COLUMN IF NOT EXISTS " in m or " ADD INDEX IF NOT EXISTS " in m
                                   for m in added)
    assert {m.split()[2] for m in added} == {"curation_candidates", "automation_queue", "product_failures"}


# ---------------------------------------------------------------------------
# a run whose sheet changes while it is running
# ---------------------------------------------------------------------------

@pytest.fixture
def worker_run(gs, monkeypatch, tmp_path):
    import time as time_mod

    import local_cache_db
    import main

    monkeypatch.chdir(tmp_path)
    real_sleep = time_mod.sleep
    monkeypatch.setattr(time_mod, "sleep", lambda s: real_sleep(0.005))
    ws = _ws(SHEET)
    rec = {"states": [], "claims": [], "processed": [], "writes": [], "stopped": 0, "used": [], "backfilled": []}
    tasks = [{"id": i, "row_number": 2 + i, "product_name": f"P{i}"} for i in range(4)]
    monkeypatch.setattr(main, "_another_worker_running", lambda lock: False)
    monkeypatch.setattr(main, "load_run_config", lambda: None)
    monkeypatch.setattr(main, "check_verifier", lambda: "")
    monkeypatch.setattr(main, "_run_health", lambda *a, **k: None)
    monkeypatch.setattr(main, "_prepare_verifier_rechecks", lambda *a: 0)
    monkeypatch.setattr(local_cache_db, "resume_automation", lambda: True)
    monkeypatch.setattr(local_cache_db, "use_sheet_key", rec["used"].append)
    monkeypatch.setattr(local_cache_db, "backfill_sheet_key", rec["backfilled"].append)
    monkeypatch.setattr(gs, "get_sheets_client", lambda: object())
    monkeypatch.setattr(gs, "open_worksheet", lambda client, name: ws)
    monkeypatch.setattr(gs, "find_link_column", lambda worksheet: 3)
    monkeypatch.setattr(gs, "get_brand_mappings", lambda *a: {})
    monkeypatch.setattr(gs, "init_async_queue", lambda *a: None)
    monkeypatch.setattr(gs, "stop_async_queue", lambda: None)
    outbox = RecordingQueue()
    monkeypatch.setattr(gs, "_queue", outbox)
    monkeypatch.setattr(gs, "_worker", object())
    monkeypatch.setattr(gs, "_get_redis", lambda: None)
    monkeypatch.setattr(local_cache_db, "update_automation_state",
                        lambda status, **kw: rec["states"].append(dict(kw, status=status)) or True)
    monkeypatch.setattr(local_cache_db, "get_automation_state", lambda: {"pause_requested": 0})
    monkeypatch.setattr(local_cache_db, "get_ready_for_review_count", lambda: 0)

    def stop_run(worker_active=False):
        rec["stopped"] += 1
        return {"status": "idle"}

    monkeypatch.setattr(local_cache_db, "stop_run", stop_run)

    def fetch(worker_id, sheet_key=None):
        rec["claims"].append(sheet_key)
        return tasks.pop(0) if tasks else None

    monkeypatch.setattr(local_cache_db, "fetch_next_task", fetch)
    monkeypatch.setattr(local_cache_db, "count_open_tasks", lambda sheet_key=None: len(tasks))

    def pre_cache(task, worksheet, link_column_index, *a, **k):
        rec["processed"].append(task["row_number"])
        if task["id"] == 0:
            gs.env["SPREADSHEET_NAME_OR_URL"] = "sheet B"          # the owner saves another sheet mid-run
        rec["writes"].append(gs.update_image_link(worksheet, task["row_number"], 3, "https://res/x.png"))
        return "success"

    monkeypatch.setattr(main, "pre_cache_product_candidates", pre_cache)
    rec["outbox"] = outbox
    return main, rec


def test_a_sheet_change_mid_run_stops_the_run_and_its_writes(worker_run):
    main, rec = worker_run
    main.run_worker_mode(report=False)
    assert rec["used"] == ["SHEET-A:0"] and rec["backfilled"] == ["SHEET-A:0"]
    assert set(rec["claims"]) == {"SHEET-A:0"}                       # only the opened sheet's tasks are claimed
    assert rec["processed"] == [2]                                    # no new product after the change
    assert main.LAST_WORKER["stop_reason"] == "sheet_changed"
    assert rec["stopped"] == 1                                        # the stop path returns rows to pending
    final = rec["states"][-1]
    assert final["notice"].startswith("SHEET_CHANGED: الشيت تغيّر أثناء التشغيل: وقّفنا التشغيل")
    import google_sheets
    assert google_sheets.writes_frozen()
    before = len(rec["outbox"].appended)
    assert google_sheets.update_image_link(_ws(SHEET), 2, 3, "https://res/late.png") is False
    assert len(rec["outbox"].appended) == before                       # a product still running writes nothing


def test_a_sheet_change_during_enqueue_queues_nothing(gs, monkeypatch, tmp_path):
    import config
    import local_cache_db
    import main

    monkeypatch.chdir(tmp_path)
    ws = _ws()
    states = []
    monkeypatch.setattr(main, "load_run_config", lambda: None)
    monkeypatch.setattr(main, "_release_starting_lock", lambda *a: None)
    monkeypatch.setattr(config, "ROW_FILTER", "", raising=False)
    monkeypatch.setattr(config, "BRAND_FILTER", "", raising=False)
    monkeypatch.setattr(gs, "get_sheets_client", lambda: object())
    monkeypatch.setattr(gs, "open_worksheet", lambda client, name: ws)
    monkeypatch.setattr(gs, "get_brand_mappings", lambda *a: {})
    monkeypatch.setattr(local_cache_db, "use_sheet_key", lambda key: None)
    monkeypatch.setattr(local_cache_db, "backfill_sheet_key", lambda key: None)
    monkeypatch.setattr(local_cache_db, "update_automation_state",
                        lambda status, **kw: states.append(dict(kw, status=status)) or True)
    monkeypatch.setattr(local_cache_db, "add_many_to_queue", lambda *a, **k: pytest.fail("nothing may be queued"))

    def read(worksheet):
        gs.env["SPREADSHEET_TAB_NAME"] = "Another tab"
        return [{"row_number": 2, "product_name": "Milk", "brand": "", "barcode": ""}], 3

    monkeypatch.setattr(gs, "get_products", read)
    with pytest.raises(SystemExit):
        main.run_enqueue_mode()
    assert main.LAST_ENQUEUE["reason"] == "sheet_changed"
    assert "الشيت تغيّر أثناء التشغيل" in states[-1]["notice"]
