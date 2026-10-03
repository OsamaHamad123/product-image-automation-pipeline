"""Sheet-write integrity: the right column (logical key resolved at write time), the right row (valid GTINs only
as sole keys; relocation to the single matching row), outcomes that are never silent (timed retries, outcome
hook and outbox_outcomes), transient Google errors retried, a grown sheet refreshed, and ordering across the
Redis and MariaDB channels.

Google Sheets and Redis are in-memory fakes. The outbox tests use the real MariaDB test database (local
connections only; skipped when MariaDB is down); every other connection is refused.
"""

import json
import os
import socket

import gspread
import pytest


# ---------------------------------------------------------------------------
# fakes
# ---------------------------------------------------------------------------

# valid GTIN-13s (GS1 check digit)
MILK, LABAN, JUICE, WATER = "6281007000024", "6281007000031", "6294001819097", "6291003000010"


class Resp:
    def __init__(self, code, message="error"):
        self.status_code = code
        self.text = message
        self._code, self._message = code, message

    def json(self):
        return {"error": {"code": self._code, "message": self._message, "status": "ERR"}}


def api_error(code, message="error"):
    return gspread.exceptions.APIError(Resp(code, message))


class Sheet:
    """A worksheet whose cells really change: inserting a column or a row moves the cells like Google Sheets."""

    def __init__(self, rows, title="Products", grid_rows=1000):
        self.rows = [list(r) for r in rows]
        self.title = title
        self.id = 7
        self.col_count = 26
        self.grid_rows = grid_rows
        self._properties = {"sheetId": 7, "gridProperties": {"rowCount": grid_rows}}   # cached at open, as gspread
        self.down = None              # an exception every send raises (Google outage)
        self.sends = 0
        self.metadata_calls = 0
        outer = self

        class Spreadsheet:
            def values_batch_update(self, body):
                outer.sends += 1
                if outer.down is not None:
                    raise outer.down
                for d in body["data"]:
                    r, c = gspread.utils.a1_to_rowcol(d["range"].split("!")[1])
                    outer.set(r, c, d["values"][0][0])

            def fetch_sheet_metadata(self, params=None):
                outer.metadata_calls += 1
                return {"sheets": [{"properties": {"sheetId": outer.id, "title": outer.title,
                                                   "gridProperties": {"rowCount": outer.grid_rows}}}]}

        self.spreadsheet = Spreadsheet()

    @property
    def row_count(self):
        return self._properties["gridProperties"]["rowCount"]

    # --- what the code under test calls
    def row_values(self, n):
        return list(self.rows[n - 1]) if n - 1 < len(self.rows) else []

    def get_all_values(self):
        return [list(r) for r in self.rows]

    def update_cell(self, row, col, value):
        self.set(row, col, value)

    def batch_get(self, ranges):
        out = []
        for rng in ranges:
            start, end = rng.split(":")
            r1, c1 = gspread.utils.a1_to_rowcol(start)
            r2 = gspread.utils.a1_to_rowcol(end)[0] if any(ch.isdigit() for ch in end) else len(self.rows)
            out.append([[self.cell(r, c1)] if self.cell(r, c1) != "" else [] for r in range(r1, r2 + 1)])
        return out

    # --- helpers for the tests
    def set(self, row, col, value):
        while len(self.rows) < row:
            self.rows.append([])
        while len(self.rows[row - 1]) < col:
            self.rows[row - 1].append("")
        self.rows[row - 1][col - 1] = value

    def cell(self, row, col):
        line = self.rows[row - 1] if row - 1 < len(self.rows) else []
        return line[col - 1] if col - 1 < len(line) else ""

    def column(self, header):
        return self.rows[0].index(header) + 1

    def value(self, row, header):
        return self.cell(row, self.column(header))

    def insert_column(self, index0, header):
        for i, line in enumerate(self.rows):
            while len(line) < index0:
                line.append("")
            line.insert(index0, header if i == 0 else "")

    def delete_column(self, index0):
        for line in self.rows:
            if index0 < len(line):
                del line[index0]

    def insert_row(self, row, values):
        self.rows.insert(row - 1, list(values))
        self.grid_rows += 1


HEADERS = ["Barcode", "Product Name", "Brand", "Size", "Origin", "Drive Image Link"]


def sheet():
    return Sheet([HEADERS,
                  [MILK, "Almarai Fresh Milk", "Almarai", "1L", "KSA", ""],        # row 2
                  [LABAN, "Almarai Laban", "Almarai", "1L", "KSA", ""],            # row 3
                  [JUICE, "Al Rawabi Juice", "Al Rawabi", "500ml", "UAE", ""]])    # row 4


class FakeRedis:
    def __init__(self):
        self.kv = {"writebehind:heartbeat": "1"}
        self.sets = {}

    def exists(self, key):
        return 1 if key in self.kv else 0

    def get(self, key):
        return self.kv.get(key)

    def set(self, key, value, ex=None):
        self.kv[key] = value

    def delete(self, key):
        self.kv.pop(key, None)

    def sadd(self, key, member):
        self.sets.setdefault(key, set()).add(member)

    def srem(self, key, member):
        self.sets.get(key, set()).discard(member)

    def smembers(self, key):
        return set(self.sets.get(key, set()))

    def smove(self, src, dst, member):
        self.srem(src, member)
        self.sadd(dst, member)


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def gs(monkeypatch, tmp_path):
    import google_sheets
    monkeypatch.setattr(google_sheets, "CACHE_DIR", str(tmp_path))
    google_sheets._header_cache.clear()
    reported = []
    monkeypatch.setattr(google_sheets, "_outcome_hook", reported.append)
    monkeypatch.setattr(google_sheets, "_sleep", lambda seconds: None)
    google_sheets.reported = reported
    clock = {"now": 1_800_000_000.0}
    monkeypatch.setattr(google_sheets, "_now", lambda: clock["now"])
    google_sheets.clock = clock
    monkeypatch.setattr(google_sheets, "_get_redis", lambda: None)
    return google_sheets


@pytest.fixture
def local_only(monkeypatch):
    """Only the local MariaDB may be reached; any other connection is refused."""
    allowed = {"127.0.0.1", "localhost", "::1", os.getenv("DB_HOST", "127.0.0.1")}
    real_create = socket.create_connection
    real_connect = socket.socket.connect

    def create_connection(address, *args, **kwargs):
        if address[0] not in allowed:
            raise OSError("network access is blocked in this test")
        return real_create(address, *args, **kwargs)

    def connect(self, address):
        if isinstance(address, tuple) and address[0] not in allowed:
            raise OSError("network access is blocked in this test")
        return real_connect(self, address)

    monkeypatch.setattr(socket, "create_connection", create_connection)
    monkeypatch.setattr(socket.socket, "connect", connect)


@pytest.fixture
def outbox(mariadb_or_skip, local_only, gs, monkeypatch):
    """A clean MariaDB outbox wired as the process queue (update_image_link queues into it)."""
    queue = gs.SQLiteTransactionQueue()
    conn = queue._connect()
    try:
        conn.cursor().execute("DELETE FROM sheet_updates")
        conn.commit()
    finally:
        conn.close()
    monkeypatch.setattr(gs, "_queue", queue)
    monkeypatch.setattr(gs, "_worker", object())
    return queue


def flush(gs, ws, queue):
    gs.GoogleSheetsBatchWorker(queue, None, None)._synchronize_pending_records(ws)


def rows_of(queue):
    conn = queue._connect()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM sheet_updates ORDER BY id")
        return {r["id"]: r for r in cursor.fetchall()}
    finally:
        conn.close()


def statuses(gs):
    return {o["id"]: o["status"] for o in gs.outbox_outcomes(limit=1000)}


# ---------------------------------------------------------------------------
# 1. the right column
# ---------------------------------------------------------------------------

def test_link_lands_in_the_link_column_after_columns_move_mid_run(gs, outbox):
    """The audit case: a column inserted left of the link column during a run must not turn the queued link
    into a write to 'Origin' (the header that slid into the link column's old position)."""
    ws = sheet()
    link_idx = gs.find_link_column(ws)                         # captured once at the start of the run
    assert link_idx == 5
    ws.insert_column(1, "Notes")                               # owner inserts a column at B mid-run
    gs._header_cache.clear()                                   # the 60-second header cache has expired
    assert gs.update_image_link(ws, 2, link_idx, "https://res/milk.png", barcode=MILK) is True
    flush(gs, ws, outbox)
    assert ws.value(2, "Drive Image Link") == "https://res/milk.png"
    assert ws.value(2, "Origin") == "KSA"                      # untouched

    # a column deleted left of the link column between queueing and flushing
    assert gs.update_image_link(ws, 3, link_idx, "https://res/laban.png", barcode=LABAN) is True
    ws.delete_column(1)                                        # 'Notes' removed again
    flush(gs, ws, outbox)
    assert ws.value(3, "Drive Image Link") == "https://res/laban.png"
    assert ws.value(3, "Origin") == "KSA"
    assert set(statuses(gs).values()) == {"SYNCED"}


def test_metadata_columns_are_resolved_at_write_time(gs, outbox):
    ws = sheet()
    assert gs.update_product_metadata(ws, 2, {"description_en": "Fresh milk"}, barcode=MILK) is True
    ws.insert_column(0, "Internal ID")                         # every column shifts right before the flush
    flush(gs, ws, outbox)
    assert ws.value(2, "Description EN") == "Fresh milk"
    assert ws.value(2, "Barcode") == MILK


def test_redis_payload_carries_the_logical_column_end_to_end(gs, outbox, monkeypatch):
    """Redis write-behind: the payload carries 'link'; sync_worker forwards it to the outbox, whose flush
    resolves the column at write time (a column inserted meanwhile does not redirect the link)."""
    import sync_worker
    r = FakeRedis()
    monkeypatch.setattr(gs, "_get_redis", lambda: r)
    ws = sheet()
    link_idx = gs.find_link_column(ws)
    assert gs.update_image_link(ws, 4, link_idx, "https://res/juice.png", barcode=JUICE) is True
    assert json.loads(r.kv["product:data:row_4"])["updates"] == {"link": "https://res/juice.png"}
    ws.insert_column(2, "Notes")
    assert sync_worker.run_sync_cycle(ws, r, queue=outbox) == 1
    assert ws.value(4, "Drive Image Link") == "https://res/juice.png" and ws.value(4, "Origin") == "UAE"
    assert "row_4" not in r.smembers("writebehind:dirty_set")


# ---------------------------------------------------------------------------
# 2. the right row: placeholders are never identity keys
# ---------------------------------------------------------------------------

PLACEHOLDERS = [
    ["Barcode", "Product Name", "Brand", "Size", "Drive Image Link"],
    ["N/A", "Tomato Paste 400g", "Al Alali", "400g", ""],             # row 2
    ["6.29E+12", "Chickpeas 400g", "California Garden", "400g", ""],  # row 3
    ["0", "Basmati Rice 5kg", "Abu Kass", "5kg", ""],                 # row 4
    ["-", "Sunflower Oil 1.5L", "Afia", "1.5L", ""],                  # row 5
]


def test_placeholder_barcodes_never_identify_a_row(gs, offline):
    ws = Sheet(PLACEHOLDERS)
    conflicts = gs.find_record_conflicts(ws, {
        # another product whose barcode cell is also a placeholder: the old digits-only compare saw '' == ''
        "dash_vs_na": (2, gs._expectation("-", "Sunflower Oil 1.5L", brand="Afia")),
        "zero_vs_zeros": (4, gs._expectation("000", "Tomato Paste 400g")),
        # Sheets collapses different barcodes to the same scientific notation
        "sci": (3, gs._expectation("6.29E+12", "Tomato Paste 400g")),
        # the right products, still verified by name (and brand when given)
        "ok_na": (2, gs._expectation("N/A", "Tomato Paste 400g", brand="Al Alali")),
        "ok_sci": (3, gs._expectation("6.29E+12", "chickpeas  400G")),
        "wrong_brand": (5, gs._expectation("-", "Sunflower Oil 1.5L", brand="Noor")),
    })
    assert set(conflicts) == {"dash_vs_na", "zero_vs_zeros", "sci", "wrong_brand"}
    assert "name mismatch" in conflicts["dash_vs_na"] and "brand mismatch" in conflicts["wrong_brand"]
    # a placeholder alone (no name) can never verify a row
    assert gs.find_record_conflicts(ws, {"bare": (2, gs._expectation("N/A"))})
    # a valid GTIN is the sole key: the owner may fix a product name without blocking the write
    gtin_sheet = Sheet([["Barcode", "Product Name"], [MILK, "Almarai Fresh Milk 1 Litre"]])
    assert gs.find_record_conflicts(gtin_sheet, {"g": (2, gs._expectation(MILK, "Almarai Fresh Milk 1L"))}) == {}


def test_review_publish_paths_pass_the_brand_into_the_row_identity(gs, offline, monkeypatch, tmp_path):
    """Without a valid GTIN the row is identified by name + size + brand: the review paths must send the brand."""
    import cli_bridge
    import local_cache_db
    seen = []

    class Pipeline:
        def publish_image(self, *args, **kwargs):
            seen.append(kwargs)
            return {"status": "failed", "error": "stop here"}

    monkeypatch.setattr(cli_bridge, "LOG_PATH", str(tmp_path / "search.log"))
    monkeypatch.setattr(cli_bridge, "_pipeline", lambda: Pipeline())
    monkeypatch.setattr(cli_bridge, "_identity_problem", lambda params, row: ("SKU", "", None))
    monkeypatch.setattr(cli_bridge, "_candidate_sha", lambda *a: None)
    monkeypatch.setattr(cli_bridge, "_product_changed", lambda params, task: False)
    monkeypatch.setattr(cli_bridge, "_open_sheet", lambda: object())
    monkeypatch.setattr(gs, "init_async_queue", lambda *a, **k: None)
    monkeypatch.setattr(gs, "stop_async_queue", lambda *a, **k: None)
    monkeypatch.setattr(gs, "find_link_column", lambda ws, create=True: 3)
    monkeypatch.setattr(local_cache_db, "get_task_by_row", lambda row: None)
    params = {"image_url": "https://shop/x.jpg", "product_name": "Sunflower Oil 1.5L", "brand": "Afia",
              "size": "1.5L", "row_number": 5, "sku_key": "SKU"}
    cli_bridge.action_select_image(dict(params))
    upload = tmp_path / "upload.png"
    upload.write_bytes(b"x")
    cli_bridge.action_upload_manual_image(dict(params, file_path=str(upload)))
    assert [(k["key_size"], k["key_brand"]) for k in seen] == [("1.5L", "Afia"), ("1.5L", "Afia")]


def test_placeholder_write_for_another_product_goes_to_that_products_row(gs, outbox):
    ws = Sheet(PLACEHOLDERS)
    assert gs.update_image_link(ws, 2, 4, "https://res/oil.png", barcode="-", product_name="Sunflower Oil 1.5L",
                                size="1.5L", brand="Afia") is True
    flush(gs, ws, outbox)
    assert ws.value(2, "Drive Image Link") == ""              # the tomato paste row is never touched
    assert ws.value(5, "Drive Image Link") == "https://res/oil.png"


# ---------------------------------------------------------------------------
# 3. relocate instead of losing
# ---------------------------------------------------------------------------

def test_shifted_row_is_relocated_to_the_single_matching_row(gs, outbox):
    ws = sheet()
    assert gs.update_image_link(ws, 3, 5, "https://res/laban.png", barcode=LABAN) is True
    ws.insert_row(2, [WATER, "Mai Dubai Water", "Mai Dubai", "500ml", "UAE", ""])   # Laban moves to row 4
    flush(gs, ws, outbox)
    assert ws.value(4, "Drive Image Link") == "https://res/laban.png"
    assert ws.value(3, "Drive Image Link") == ""               # the milk row (now row 3) is not touched
    (outcome,) = gs.outbox_outcomes(row_numbers=[3])
    assert (outcome["status"], outcome["row"], outcome["queued_row"]) == ("SYNCED", 4, 3)
    assert gs.reported == []


def test_two_matching_rows_stay_a_conflict_and_are_reported(gs, outbox):
    ws = sheet()
    assert gs.update_image_link(ws, 3, 5, "https://res/milk.png", barcode=MILK) is True
    ws.set(4, 1, MILK)                                         # the barcode now appears twice in the sheet
    ws.set(3, 1, WATER)                                        # and the queued row holds another product
    flush(gs, ws, outbox)
    assert all(ws.value(r, "Drive Image Link") == "" for r in (2, 3, 4))
    (outcome,) = gs.outbox_outcomes()
    assert outcome["status"] == "CONFLICT" and "2 rows match" in outcome["error"]
    (reported,) = gs.reported
    assert reported["status"] == "CONFLICT" and reported["row"] == 3 and reported["barcode"] == MILK


def test_outbox_outcomes_filters_by_row_and_since_id(gs, outbox):
    ws = sheet()
    first = outbox.append_update(2, 5, "https://res/milk.png", col_key="link", key_barcode=MILK)
    second = outbox.append_update(3, 5, "https://res/x.png", col_key="link", key_barcode=WATER)
    flush(gs, ws, outbox)
    assert {o["id"]: o["status"] for o in gs.outbox_outcomes(row_numbers=[2, 3])} == {first: "SYNCED",
                                                                                      second: "CONFLICT"}
    (later,) = gs.outbox_outcomes(since_id=first)
    assert later["id"] == second and later["column_key"] == "link" and later["attempts"] == 0
    assert set(later) >= {"id", "row", "column_key", "status", "error", "attempts"}


def test_default_outcome_hook_logs_and_never_raises(gs, monkeypatch, offline):
    import config
    monkeypatch.setattr(gs, "_outcome_hook", None)
    logged = []
    monkeypatch.setattr(config, "log_error_to_laravel", lambda msg, **k: logged.append((msg, k)))
    gs._report_outcome({"id": 9, "row": 4, "column_key": "link", "status": "DEAD", "error": "boom\nline",
                        "barcode": MILK, "product_name": "Milk", "brand": "Almarai"})
    (msg, kwargs), = logged
    assert "DEAD" in msg and "\n" not in msg and kwargs["barcode"] == MILK and kwargs["level"] == "ERROR"

    def broken(*a, **k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(config, "log_error_to_laravel", broken)
    gs._report_outcome({"id": 9, "status": "DEAD"})            # never raises
    monkeypatch.setattr(gs, "_outcome_hook", broken)
    gs._report_outcome({"id": 9, "status": "DEAD"})


# ---------------------------------------------------------------------------
# 4. timed retries: DEAD is not final after seconds
# ---------------------------------------------------------------------------

def test_failed_write_is_retried_on_a_timed_backoff_across_runs(gs, outbox):
    ws = sheet()
    ws.down = api_error(503, "backend unavailable")
    wid = outbox.append_update(2, 5, "https://res/milk.png", col_key="link", key_barcode=MILK)
    flush(gs, ws, outbox)
    row = rows_of(outbox)[wid]
    assert (row["sync_status"], row["attempts"]) == ("FAILED", 1)
    assert row["next_attempt_at"] == int(gs.clock["now"]) + 60
    sends = ws.sends

    gs.clock["now"] += 30                                       # not due yet: nothing is sent
    flush(gs, ws, outbox)
    assert ws.sends == sends and rows_of(outbox)[wid]["sync_status"] == "FAILED"

    ws.down = None                                              # Google is back; the next run retries on time
    gs.clock["now"] += 31
    flush(gs, ws, outbox)
    assert rows_of(outbox)[wid]["sync_status"] == "SYNCED"
    assert ws.value(2, "Drive Image Link") == "https://res/milk.png"
    assert gs.reported == []


def test_backoff_schedule_then_dead_and_reported(gs, outbox):
    ws = sheet()
    ws.down = api_error(500, "internal")
    wid = outbox.append_update(2, 5, "https://res/milk.png", col_key="link", key_barcode=MILK)
    delays = []
    for _ in range(gs.MAX_OUTBOX_ATTEMPTS):
        flush(gs, ws, outbox)
        row = rows_of(outbox)[wid]
        if row["sync_status"] == "FAILED":
            delays.append(row["next_attempt_at"] - int(gs.clock["now"]))
            gs.clock["now"] = row["next_attempt_at"]
    assert delays == [60, 300, 1800, 7200]                      # ~2.5 hours of retries, not ~25 seconds
    row = rows_of(outbox)[wid]
    assert (row["sync_status"], row["attempts"]) == ("DEAD", 5)
    (dead,) = gs.reported
    assert dead["status"] == "DEAD" and dead["id"] == wid and dead["attempts"] == 5


def test_502_and_504_are_transient(gs, offline):
    for code in (502, 504):
        calls = []

        def flaky():
            calls.append(1)
            if len(calls) < 3:
                raise api_error(code, "bad gateway")
            return "ok"

        assert gs.retry_gspread_on_429(max_retries=5)(flaky)() == "ok" and len(calls) == 3
    assert gs._is_transient(api_error(502)) and gs._is_transient(api_error(504))
    assert not gs._is_transient(api_error(400)) and not gs._is_transient(api_error(404))


def test_flush_treats_a_502_outage_as_transient(gs, outbox):
    ws = sheet()
    ws.down = api_error(502, "bad gateway")
    wid = outbox.append_update(2, 5, "https://res/milk.png", col_key="link", key_barcode=MILK)
    flush(gs, ws, outbox)
    assert ws.sends == 6                                        # one batch, retried in-call; no row-by-row storm
    assert rows_of(outbox)[wid]["sync_status"] == "FAILED"


# ---------------------------------------------------------------------------
# 5. transient Sheets errors at the start of a run
# ---------------------------------------------------------------------------

class Client:
    def __init__(self, failures, sheet_obj=None):
        self.failures = list(failures)
        self.sheet = sheet_obj
        self.calls = 0

    def open_by_url(self, url):
        self.calls += 1
        if self.failures:
            raise self.failures.pop(0)
        return self.sheet


class Book:
    def __init__(self, ws, failures=()):
        self.ws = ws
        self.failures = list(failures)

    def get_worksheet(self, index):
        if self.failures:
            raise self.failures.pop(0)
        return self.ws


@pytest.fixture
def no_tab(monkeypatch):
    import config
    monkeypatch.setattr(config, "SPREADSHEET_TAB_NAME", "")


def test_open_worksheet_retries_transient_errors(gs, offline, no_tab):
    ws = sheet()
    client = Client([api_error(429, "quota"), api_error(503)], Book(ws, [api_error(500)]))
    assert gs.open_worksheet(client, "https://docs.google.com/x") is ws
    assert client.calls == 3


def test_open_worksheet_reports_not_found_distinctly_from_transient(gs, offline, no_tab):
    # a real 'not found / no access' is not retried and still returns None (the caller says: check sharing)
    for missing in (gspread.exceptions.SpreadsheetNotFound("x"), PermissionError("no access")):
        client = Client([missing])
        assert gs.open_worksheet(client, "https://docs.google.com/x") is None and client.calls == 1
    # a quota error that does not clear is not 'not found': SheetTransientError says so
    client = Client([api_error(429, "quota")] * 10)
    with pytest.raises(gs.SheetTransientError) as exc:
        gs.open_worksheet(client, "https://docs.google.com/x")
    assert "429" in str(exc.value) and "ليس خطأ في رابط الشيت" in str(exc.value)
    assert client.calls == 6


def test_dashboard_product_list_says_transient_not_sharing(gs, offline, monkeypatch, tmp_path):
    import cli_bridge
    monkeypatch.setattr(cli_bridge, "LOG_PATH", str(tmp_path / "search.log"))

    def busy():
        raise gs.SheetTransientError("APIError: [429]: quota (secret detail)")

    monkeypatch.setattr(cli_bridge, "_open_sheet", busy)
    result = cli_bridge.action_get_products({})
    assert result["status"] == "failed" and "temporarily unavailable" in result["error"]
    assert "shared with the service account" not in result["error"] and "secret" not in result["error"]

    def missing():
        raise RuntimeError("Sheet not found: x")

    monkeypatch.setattr(cli_bridge, "_open_sheet", missing)
    assert "shared with the service account" in cli_bridge.action_get_products({})["error"]


def test_get_products_and_find_link_column_retry_transient_errors(gs, offline):
    ws = sheet()
    failures = {"get_all_values": [api_error(429), ConnectionResetError("reset")], "row_values": [api_error(503)]}

    def flaky(name, real):
        def call(*args, **kwargs):
            if failures[name]:
                raise failures[name].pop(0)
            return real(*args, **kwargs)
        return call

    ws.get_all_values = flaky("get_all_values", ws.get_all_values)
    ws.row_values = flaky("row_values", ws.row_values)
    products, link_idx = gs.get_products(ws)
    assert [p["row_number"] for p in products] == [2, 3, 4] and link_idx == 5
    assert gs.find_link_column(ws) == 5
    assert failures == {"get_all_values": [], "row_values": []}

    ws.get_all_values = flaky("get_all_values", ws.get_all_values)
    failures["get_all_values"] = [api_error(503)] * 10
    gs.clear_cache()
    with pytest.raises(gs.SheetTransientError):
        gs.get_products(ws)


# ---------------------------------------------------------------------------
# 6. a sheet that grew is not skipped
# ---------------------------------------------------------------------------

def test_grown_sheet_is_not_skipped_out_of_bounds(gs, outbox):
    ws = sheet()
    ws.grid_rows = ws._properties["gridProperties"]["rowCount"] = 4     # opened when the sheet had 4 rows
    ws.insert_row(5, ["6281007000055", "Almarai Cheese", "Almarai", "200g", "KSA", ""])   # grows to 5
    wid = outbox.append_update(5, 5, "https://res/cheese.png", col_key="link", key_barcode="6281007000055")
    plain = outbox.append_update(5, 4, "France", col_key="origin")     # no identity: could never be relocated
    flush(gs, ws, outbox)
    assert rows_of(outbox)[wid]["sync_status"] == "SYNCED" and rows_of(outbox)[wid]["relocated_from"] is None
    assert rows_of(outbox)[plain]["sync_status"] == "SYNCED"
    assert ws.value(5, "Drive Image Link") == "https://res/cheese.png" and ws.value(5, "Origin") == "France"
    assert ws.row_count == ws.grid_rows                          # the cached count was refreshed

    # a row really beyond the sheet is still skipped, and reported
    gone = outbox.append_update(50, 5, "https://res/gone.png", col_key="link", key_barcode="6281007000062")
    flush(gs, ws, outbox)
    assert rows_of(outbox)[gone]["sync_status"] == "SKIPPED_OUT_OF_BOUNDS"
    assert [o["status"] for o in gs.reported] == ["SKIPPED_OUT_OF_BOUNDS"]


def test_unreadable_row_count_leaves_the_write_pending(gs, outbox):
    ws = sheet()
    ws._properties["gridProperties"]["rowCount"] = 3

    def no_metadata(params=None):
        raise api_error(400, "bad request")

    ws.spreadsheet.fetch_sheet_metadata = no_metadata
    wid = outbox.append_update(4, 5, "https://res/juice.png", col_key="link", key_barcode=JUICE)
    flush(gs, ws, outbox)
    assert rows_of(outbox)[wid]["sync_status"] == "PENDING" and gs.reported == []


# ---------------------------------------------------------------------------
# 7. ordering: an older value never lands after a newer one
# ---------------------------------------------------------------------------

def test_older_redis_value_never_lands_after_a_newer_outbox_value(gs, outbox, monkeypatch):
    import sync_worker
    ws = sheet()
    r = FakeRedis()
    monkeypatch.setattr(gs, "_get_redis", lambda: r)
    # 1. the worker queues a needs_review link through Redis (sync_worker is slow to forward it)
    assert gs.update_image_link(ws, 2, 5, "needs_review:https://res/old.png", barcode=MILK)
    # 2. the reviewer approves the final image; the heartbeat lapsed, so it goes through the outbox
    monkeypatch.setattr(gs, "_get_redis", lambda: None)
    assert gs.update_image_link(ws, 2, 5, "https://res/approved.png", barcode=MILK)
    flush(gs, ws, outbox)
    assert ws.value(2, "Drive Image Link") == "https://res/approved.png"
    # 3. sync_worker finally forwards the older payload: it must not overwrite the approved link
    assert sync_worker.run_sync_cycle(ws, r, queue=outbox) == 1
    assert ws.value(2, "Drive Image Link") == "https://res/approved.png"
    assert sorted(statuses(gs).values()) == ["SUPERSEDED", "SYNCED"]


def test_older_write_retried_later_never_overwrites_a_newer_one(gs, outbox):
    ws = sheet()
    ws.down = api_error(503)
    old = outbox.append_update(3, 5, "needs_review:https://res/old.png", col_key="link", key_barcode=LABAN)
    flush(gs, ws, outbox)                                       # fails: retried in 60 s
    ws.down = None
    new = outbox.append_update(3, 5, "https://res/approved.png", col_key="link", key_barcode=LABAN)
    flush(gs, ws, outbox)                                       # only the new write is due
    assert ws.value(3, "Drive Image Link") == "https://res/approved.png"
    ws.insert_row(2, [WATER, "Mai Dubai Water", "Mai Dubai", "500ml", "UAE", ""])   # Laban moves to row 4
    gs.clock["now"] += 61
    flush(gs, ws, outbox)                                       # the old write is due again: relocated, then refused
    assert ws.value(4, "Drive Image Link") == "https://res/approved.png"
    assert statuses(gs) == {old: "SUPERSEDED", new: "SYNCED"}


def test_older_write_to_one_duplicate_row_still_lands_after_a_newer_write_to_the_other(gs, outbox):
    """The same product listed twice (rows 2 and 4): a newer write to row 4 must not cancel an older, still
    pending write to row 2 (the row test applies because the product has two rows)."""
    ws = sheet()
    ws.set(4, 1, MILK)
    ws.set(4, 2, "Almarai Fresh Milk")
    ws.down = api_error(503)
    older = outbox.append_update(2, 5, "https://res/milk-a.png", col_key="link", key_barcode=MILK)
    flush(gs, ws, outbox)                                       # fails: retried in 60 s
    ws.down = None
    newer = outbox.append_update(4, 5, "https://res/milk-b.png", col_key="link", key_barcode=MILK)
    flush(gs, ws, outbox)
    gs.clock["now"] += 61
    flush(gs, ws, outbox)
    assert statuses(gs) == {older: "SYNCED", newer: "SYNCED"}
    assert ws.value(2, "Drive Image Link") == "https://res/milk-a.png"
    assert ws.value(4, "Drive Image Link") == "https://res/milk-b.png"


def test_a_newer_write_for_another_product_does_not_cancel_an_older_write(gs, outbox):
    ws = sheet()
    ws.down = api_error(503)
    older = outbox.append_update(2, 5, "https://res/milk.png", col_key="link", key_barcode=MILK)
    flush(gs, ws, outbox)
    ws.down = None
    newer = outbox.append_update(3, 5, "https://res/laban.png", col_key="link", key_barcode=LABAN)
    flush(gs, ws, outbox)
    gs.clock["now"] += 61
    flush(gs, ws, outbox)
    assert statuses(gs) == {older: "SYNCED", newer: "SYNCED"}
    assert ws.value(2, "Drive Image Link") == "https://res/milk.png"


def test_same_batch_older_redis_value_loses_to_a_newer_outbox_value(gs, outbox, monkeypatch):
    """Both writes reach one flush; the forwarded Redis row has the larger id but the smaller seq."""
    import sync_worker
    ws = sheet()
    r = FakeRedis()
    monkeypatch.setattr(gs, "_get_redis", lambda: r)
    assert gs.update_image_link(ws, 2, 5, "needs_review:https://res/old.png", barcode=MILK)
    monkeypatch.setattr(gs, "_get_redis", lambda: None)
    assert gs.update_image_link(ws, 2, 5, "https://res/approved.png", barcode=MILK)
    assert sync_worker.run_sync_cycle(ws, r, queue=outbox) == 1        # forward, then one flush of both
    assert ws.value(2, "Drive Image Link") == "https://res/approved.png"


def test_same_batch_older_relocated_value_loses_to_a_newer_value_for_the_same_cell(gs, outbox, monkeypatch):
    """The older write (Redis, queued for the product's old row) is relocated onto the cell the newer write
    targets: the newest by seq wins even though the forwarded row has the larger id."""
    import sync_worker
    ws = sheet()
    r = FakeRedis()
    monkeypatch.setattr(gs, "_get_redis", lambda: r)
    assert gs.update_image_link(ws, 4, 5, "needs_review:https://res/old.png", barcode=JUICE)
    ws.insert_row(2, [WATER, "Mai Dubai Water", "Mai Dubai", "500ml", "UAE", ""])   # Juice moves to row 5
    monkeypatch.setattr(gs, "_get_redis", lambda: None)
    assert gs.update_image_link(ws, 5, 5, "https://res/approved.png", barcode=JUICE)
    assert sync_worker.run_sync_cycle(ws, r, queue=outbox) == 1
    assert ws.value(5, "Drive Image Link") == "https://res/approved.png"


# ---------------------------------------------------------------------------
# review fixes
# ---------------------------------------------------------------------------

TOMATO = [
    ["Barcode", "Product Name", "Brand", "Size", "Drive Image Link"],
    ["", "Tomato Paste", "", "", ""],                                   # row 2: no barcode, blank brand/size
    ["", "Chickpeas", "", "", ""],                                      # row 3
    ["", "Tomato Paste", "Al Alali", "400g", "https://res/alali-approved.png"],   # row 4: another product
]


def test_relocation_never_treats_a_blank_brand_or_size_as_a_wildcard(gs, outbox):
    ws = Sheet(TOMATO)
    wid = outbox.append_update(2, 4, "https://res/tomato.png", col_key="link", key_name="Tomato Paste")
    ws.rows.pop(1)                                              # row 2 deleted: Al Alali is now row 3
    flush(gs, ws, outbox)
    assert ws.value(3, "Drive Image Link") == "https://res/alali-approved.png"     # never overwritten
    assert rows_of(outbox)[wid]["sync_status"] == "CONFLICT"
    assert [o["status"] for o in gs.reported] == ["CONFLICT"]

    # the same with an empty link cell on the Al Alali row: still another product, never relocated onto it
    ws = Sheet(TOMATO)
    ws.set(4, 5, "")
    wid = outbox.append_update(2, 4, "https://res/tomato.png", col_key="link", key_name="Tomato Paste")
    ws.rows.pop(1)
    flush(gs, ws, outbox)
    assert ws.value(3, "Drive Image Link") == "" and rows_of(outbox)[wid]["sync_status"] == "CONFLICT"


def test_relocation_needs_every_identity_column_of_the_sheet(gs, outbox):
    """Without a Size column the name and brand alone are not a complete identity: no relocation."""
    ws = Sheet([["Product Name", "Brand", "Drive Image Link"],
                ["Chickpeas", "California Garden", ""],
                ["Tomato Paste", "Al Alali", ""]])
    wid = outbox.append_update(2, 2, "https://res/tomato.png", col_key="link", key_name="Tomato Paste",
                               key_brand="Al Alali")
    flush(gs, ws, outbox)
    assert ws.value(3, "Drive Image Link") == "" and rows_of(outbox)[wid]["sync_status"] == "CONFLICT"

    # a complete name + size + brand match (blank barcode = blank barcode) is still relocated
    ws = Sheet(TOMATO)
    ws.set(4, 5, "")
    moved = outbox.append_update(3, 4, "https://res/alali.png", col_key="link", key_name="Tomato Paste",
                                 key_brand="Al Alali", key_size="400g")
    flush(gs, ws, outbox)
    assert ws.value(4, "Drive Image Link") == "https://res/alali.png"
    assert (rows_of(outbox)[moved]["sync_status"], rows_of(outbox)[moved]["row_number"]) == ("SYNCED", 4)


def test_relocation_compares_the_barcode_cell_text_too(gs, outbox):
    """Without a valid GTIN the barcode cell is still part of the complete identity: 'N/A' only matches 'N/A'."""
    ws = Sheet(TOMATO)
    ws.set(4, 5, "")
    wid = outbox.append_update(3, 4, "https://res/alali.png", col_key="link", key_barcode="N/A",
                               key_name="Tomato Paste", key_brand="Al Alali", key_size="400g")
    flush(gs, ws, outbox)                                       # row 4 has the same name/brand/size, blank barcode
    assert ws.value(4, "Drive Image Link") == "" and rows_of(outbox)[wid]["sync_status"] == "CONFLICT"


def test_relocation_never_overwrites_a_different_value_in_the_target_cell(gs, outbox):
    ws = sheet()
    ws.set(3, 6, "https://res/laban-approved.png")             # Laban's link is already set
    wid = outbox.append_update(4, 5, "needs_review:https://res/laban-new.png", col_key="link", key_barcode=LABAN)
    flush(gs, ws, outbox)                                       # row 4 is Juice: the single Laban row is row 3
    assert ws.value(3, "Drive Image Link") == "https://res/laban-approved.png"
    assert rows_of(outbox)[wid]["sync_status"] == "CONFLICT"
    assert "already holds" in gs.reported[0]["error"]


def test_older_value_never_wins_when_the_identity_form_changes(gs, outbox):
    """No barcode when the old write was queued; the owner then types the GTIN and the reviewer approves (GTIN
    identity). The old write, retried later, must not land over the approval."""
    ws = Sheet([HEADERS, ["", "Rani Orange Juice", "Rani", "1L", "KSA", ""]])
    ws.down = api_error(503)
    old = outbox.append_update(2, 5, "needs_review:https://res/old.png", col_key="link",
                               key_name="Rani Orange Juice", key_brand="Rani", key_size="1L")
    flush(gs, ws, outbox)
    ws.down = None
    ws.set(2, 1, JUICE)
    new = outbox.append_update(2, 5, "https://res/approved.png", col_key="link", key_barcode=JUICE,
                               key_name="Rani Orange Juice", key_brand="Rani", key_size="1L")
    flush(gs, ws, outbox)
    gs.clock["now"] += 61
    flush(gs, ws, outbox)
    assert ws.value(2, "Drive Image Link") == "https://res/approved.png"
    assert statuses(gs) == {old: "SUPERSEDED", new: "SYNCED"}


def test_an_older_write_still_lands_when_the_newer_write_was_for_a_product_that_moved_away(gs, outbox):
    """The same-cell rule only applies while the newer write's product is still in that row."""
    ws = sheet()
    ws.down = api_error(503)
    old = outbox.append_update(2, 5, "https://res/milk.png", col_key="link", key_barcode=MILK)
    flush(gs, ws, outbox)                                       # Milk's write fails at row 2
    ws.down = None
    new = outbox.append_update(3, 5, "https://res/laban.png", col_key="link", key_barcode=LABAN)
    flush(gs, ws, outbox)                                       # Laban's link written at row 3
    ws.rows.insert(2, ws.rows.pop(1))                           # the owner swaps rows 2 and 3 (cells move along)
    gs.clock["now"] += 61
    flush(gs, ws, outbox)                                       # Milk is row 3 now, Laban (and its link) row 2
    assert ws.value(3, "Drive Image Link") == "https://res/milk.png"
    assert ws.value(2, "Drive Image Link") == "https://res/laban.png"
    assert statuses(gs) == {old: "SYNCED", new: "SYNCED"}


def test_older_value_never_wins_after_the_row_drifts_twice(gs, outbox):
    ws = sheet()
    ws.down = api_error(503)
    a = outbox.append_update(3, 5, "needs_review:https://res/old.png", col_key="link", key_barcode=LABAN)
    flush(gs, ws, outbox)                                       # A fails at row 3
    ws.down = None
    ws.insert_row(2, [WATER, "Mai Dubai Water", "Mai Dubai", "500ml", "UAE", ""])   # Laban -> row 4
    b = outbox.append_update(4, 5, "https://res/approved.png", col_key="link", key_barcode=LABAN)
    flush(gs, ws, outbox)                                       # B written at row 4
    ws.insert_row(2, ["6281007000055", "Almarai Cheese", "Almarai", "200g", "KSA", ""])   # Laban -> row 5
    gs.clock["now"] += 61
    flush(gs, ws, outbox)                                       # A is due: relocated 3 -> 5, then superseded
    assert ws.value(5, "Drive Image Link") == "https://res/approved.png"
    assert statuses(gs) == {a: "SUPERSEDED", b: "SYNCED"}
    assert gs.reported == []


def test_reforwarding_a_redis_payload_never_duplicates_outbox_rows(gs, outbox):
    import sync_worker
    payload = {"v": 2, "row_index": 2, "expect": {"barcode": MILK},
               "updates": {"link": "https://res/milk.png", "meta:description_en": "Milk"},
               "seqs": {"link": 11, "meta:description_en": 12}}
    r = FakeRedis()
    r.sadd("writebehind:dirty_set", "row_2")
    r.set("product:data:row_2", json.dumps(payload))

    class FailsOnSecond:
        calls = 0

        def append_update(self, *args, **kwargs):
            FailsOnSecond.calls += 1
            if FailsOnSecond.calls == 2:
                raise RuntimeError("MariaDB went away")
            return outbox.append_update(*args, **kwargs)

    assert sync_worker.run_sync_cycle(None, r, queue=FailsOnSecond(), flush=False) == 0   # partial forward
    assert sync_worker.run_sync_cycle(None, r, queue=outbox, flush=False) == 1
    assert sorted((row["col_key"], row["seq"]) for row in rows_of(outbox).values()) == [
        ("link", 11), ("meta:description_en", 12)]
    ws = sheet()
    ws.insert_column(6, "Description EN")
    flush(gs, ws, outbox)
    # the same payload forwarded again after it was written: not inserted, not re-sent over the owner's edit
    ws.set(2, 6, "https://res/typed-by-owner.png")
    r.sadd("writebehind:dirty_set", "row_2")
    r.set("product:data:row_2", json.dumps(dict(payload, updates={"link": "https://res/milk.png"},
                                                seqs={"link": 11})))
    assert sync_worker.run_sync_cycle(ws, r, queue=outbox) == 1
    assert len(rows_of(outbox)) == 2 and ws.value(2, "Drive Image Link") == "https://res/typed-by-owner.png"
    assert gs.reported == []


def test_default_outcome_hook_keeps_each_field_on_one_line(gs, monkeypatch, offline):
    import config
    monkeypatch.setattr(gs, "_outcome_hook", None)
    logged = []
    monkeypatch.setattr(config, "log_error_to_laravel", lambda msg, **k: logged.append(k))
    gs._report_outcome({"id": 1, "status": "CONFLICT", "error": "x", "barcode": "62\n[fake] local.ERROR: forged",
                        "product_name": "Milk\r\n[2026-01-01] local.ERROR: forged", "brand": "Al\nmarai"})
    (kwargs,) = logged
    assert not any("\n" in str(v) or "\r" in str(v) for v in kwargs.values())


def test_queued_seq_never_goes_below_the_outbox_high_water_mark(gs, outbox):
    """A clock step back (or two processes in one coarse clock tick) must not make a later write look older."""
    import time as _time
    ahead = _time.time_ns() + 10 * 1_000_000_000              # written by another process before the clock stepped back
    first = outbox.append_update(2, 5, "https://res/a.png", col_key="link", key_barcode=MILK, seq=ahead)
    later = outbox.append_update(2, 5, "https://res/b.png", col_key="link", key_barcode=MILK)
    rows = rows_of(outbox)
    assert rows[later]["seq"] > rows[first]["seq"]
    ws = sheet()
    flush(gs, ws, outbox)
    assert ws.value(2, "Drive Image Link") == "https://res/b.png"
