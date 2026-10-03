"""Google Sheets safety: header resolution, outbox identity checks and isolation, brand
mapping columns, missing tab, Redis heartbeat gate and sync_worker payload handling.
Everything runs against in-memory fakes; no Google API, Redis or database is contacted.
"""

import json

import gspread
import pytest


pytestmark = pytest.mark.usefixtures("offline")


@pytest.fixture
def gs(offline, monkeypatch, tmp_path):
    import google_sheets
    monkeypatch.setattr(google_sheets, "CACHE_DIR", str(tmp_path))      # never read the real products cache
    google_sheets._header_cache.clear()
    google_sheets.reported = []                                          # unwritten outcomes, not the laravel log
    monkeypatch.setattr(google_sheets, "_outcome_hook", google_sheets.reported.append)
    monkeypatch.setattr(google_sheets, "_sleep", lambda seconds: None)
    return google_sheets


class FakeWorksheet:
    """Just enough of gspread.Worksheet for the code under test."""

    def __init__(self, rows, title="Products"):
        self.rows = [list(r) for r in rows]
        self.title = title
        self.id = id(self)
        self.row_count = 1000
        self.col_count = 26
        self.updated_cells = []
        self.batch_get_calls = []
        self.sent_bodies = []
        self.poison = set()
        self.fail_whole_batch = False
        outer = self

        class Spreadsheet:
            def values_batch_update(self, body):
                ranges = [d["range"] for d in body["data"]]
                if outer.fail_whole_batch and len(ranges) > 1:
                    raise RuntimeError("batch rejected")
                if any(r in outer.poison for r in ranges):
                    raise RuntimeError(f"invalid range {ranges}")
                outer.sent_bodies.append(body)

        self.spreadsheet = Spreadsheet()

    def get_all_values(self):
        return [list(r) for r in self.rows]

    def row_values(self, n):
        return list(self.rows[n - 1]) if n - 1 < len(self.rows) else []

    def update_cell(self, row, col, value):
        self.updated_cells.append((row, col, value))
        while len(self.rows) < row:
            self.rows.append([])
        while len(self.rows[row - 1]) < col:
            self.rows[row - 1].append("")
        self.rows[row - 1][col - 1] = value

    def batch_get(self, ranges):
        self.batch_get_calls.append(list(ranges))
        out = []
        for rng in ranges:
            start, end = rng.split(":")
            r1, c1 = gspread.utils.a1_to_rowcol(start)
            # an open-ended range ('A2:A') runs to the last row of the sheet
            r2 = gspread.utils.a1_to_rowcol(end)[0] if any(ch.isdigit() for ch in end) else len(self.rows)
            block = []
            for r in range(r1, r2 + 1):
                row = self.rows[r - 1] if r - 1 < len(self.rows) else []
                block.append([row[c1 - 1]] if c1 - 1 < len(row) and row[c1 - 1] != "" else [])
            out.append(block)
        return out


# ---------------------------------------------------------------------------
# headers
# ---------------------------------------------------------------------------

def test_headers(gs):
    cols = gs.resolve_columns(['Barcode ', 'Product Name ', 'Brand ', 'Size', 'Category'])
    assert (cols["barcode"], cols["name"], cols["brand"], cols["size"], cols["category"]) == (0, 1, 2, 3, 4)

    cols = gs.resolve_columns(['SKU', 'EAN', 'Item Name', 'Brand Name', 'Image'])
    assert cols["barcode"] == 1 and cols["name"] == 2 and cols["brand"] == 3
    assert cols["link"] == -1

    ws = FakeWorksheet([["SKU", "Brand", "Price"], ["1", "Almarai", "5"]])
    with pytest.raises(gs.SheetSchemaError) as exc:
        gs.get_products(ws)
    assert "SKU" in str(exc.value) and "Price" in str(exc.value)        # the error lists the headers it saw

    ws = FakeWorksheet([["Barcode", "Product Name", "Brand", "Weight"],
                        ["6281007000028", "Tomato Paste 400g", "", "400 g"]])
    products, link_idx = gs.get_products(ws)
    assert products[0]["brand"] == ""                                     # no brand invented from the first word
    assert products[0]["product_name"] == "Tomato Paste 400g"
    assert products[0]["size"] == "400 g"
    assert link_idx == 4 and ws.updated_cells == [(1, 5, "Drive Image Link")]
    assert not hasattr(gs, "extract_brand_from_start") and not hasattr(gs, "extract_brand_from_name")


def test_headers_arabic_and_punctuation(gs):
    cols = gs.resolve_columns(["الباركود", "اسم المنتج", "الماركة", "اسم المنتج بالعربي", "Brand_Arabic", "Drive Image Link"])
    assert (cols["barcode"], cols["name"], cols["brand"], cols["name_ar"], cols["brand_ar"], cols["link"]) == (0, 1, 2, 3, 4, 5)
    cols = gs.resolve_columns(["Barcode No.", "ProductName", "Pack-Size"])
    assert (cols["barcode"], cols["name"], cols["size"]) == (0, 1, 2)


# ---------------------------------------------------------------------------
# outbox
# ---------------------------------------------------------------------------

# valid GTIN-13s (GS1 check digit): only a valid GTIN may be the sole identity key of a row
MILK, LABAN, JUICE = "6281007000024", "6281007000031", "6294001819097"
OTHER_SKU, MASAFI = "6281007000048", "6291003000010"

SHEET = [
    ["Barcode", "Product Name", "Brand", "Drive Image Link"],
    [MILK, "Almarai Fresh Milk 1L", "Almarai", ""],                    # row 2
    [LABAN, "Almarai Laban 1L", "Almarai", ""],                        # row 3
    [JUICE, "Al Rawabi Juice", "Al Rawabi", ""],                       # row 4
]


def _outbox_row(id_, row, value, barcode=None, name=None, attempts=0, col_name=None, col_index=3,
                size=None, brand=None, col_key="link", seq=None):
    return {"id": id_, "row_number": row, "col_index": col_index, "value": value, "col_name": col_name,
            "col_key": col_key, "seq": id_ if seq is None else seq, "ident": None, "relocated_from": None,
            "key_barcode": barcode, "key_name": name, "key_size": size, "key_brand": brand, "attempts": attempts}


def _outbox_responder(pending, lock_free=True, written=()):
    def respond(sql, params):
        if "GET_LOCK" in sql:
            return [{"got": 1 if lock_free else 0}]
        if "RELEASE_LOCK" in sql:
            return [{"released": 1}]
        if "sync_status = 'SYNCED'" in sql:
            return list(written)                  # values already written by earlier flushes
        return pending if sql.startswith("SELECT") else None
    return respond


def _flush(gs, ws, pending, fake_connection, lock_free=True):
    conn = fake_connection(_outbox_responder(pending, lock_free))

    class Queue:
        def _connect(self):
            return conn

    worker = gs.GoogleSheetsBatchWorker(Queue(), None, None)
    worker._synchronize_pending_records(ws)
    return conn


def _final_status(conn):
    """id -> last status written by the flush, read back from the recorded SQL."""
    status = {}
    for sql, params in conn.executed:
        if sql.startswith("UPDATE sheet_updates SET sync_status = %s"):
            for rid in params[2:]:
                status[rid] = params[0]
        elif sql.startswith("UPDATE sheet_updates SET attempts"):
            status[params[4]] = (params[2], params[0])
    return status


def test_outbox_identity_and_isolation(gs, fake_connection):
    ws = FakeWorksheet(SHEET)
    pending = [
        _outbox_row(1, 2, "https://res/milk.png", barcode=MILK),
        _outbox_row(2, 3, "https://res/laban.png", barcode=OTHER_SKU),        # row 3 now holds another SKU
        _outbox_row(3, 4, "https://res/juice.png", name="Al Rawabi Juice"),
    ]
    conn = _flush(gs, ws, pending, fake_connection)

    assert "GET_LOCK" in conn.executed[0][0]
    select_sql = conn.executed[1][0]
    assert "ORDER BY id" in select_sql
    assert "RELEASE_LOCK" in conn.executed[-1][0]
    status = _final_status(conn)
    assert status[2] == "CONFLICT"
    assert status[1] == "SYNCED" and status[3] == "SYNCED"
    written = [d["range"] for body in ws.sent_bodies for d in body["data"]]
    assert "'Products'!D3" not in written
    assert set(written) == {"'Products'!D2", "'Products'!D4"}
    assert ws.batch_get_calls, "the key column must be re-read before writing"


def test_outbox_resolves_column_by_header_name_and_dedupes(gs, fake_connection):
    # the link column moved from D to E since the updates were queued (col_index 3 is stale)
    ws = FakeWorksheet([["Barcode", "Product Name", "Brand", "Notes", "Drive Image Link"],
                        [MILK, "Almarai Fresh Milk 1L", "Almarai", "", ""]])
    for logical in (True, False):                    # logical key 'link', and a legacy row queued with a header name
        ws.sent_bodies.clear()
        kind = {} if logical else {"col_key": None, "col_name": "Drive Image Link"}
        pending = [_outbox_row(10, 2, "https://res/old.png", barcode=MILK, **kind),
                   _outbox_row(11, 2, "https://res/new.png", barcode=MILK, **kind)]
        conn = _flush(gs, ws, pending, fake_connection)
        status = _final_status(conn)
        assert status[10] == "SUPERSEDED" and status[11] == "SYNCED"
        (body,) = ws.sent_bodies
        assert body["data"] == [{"range": "'Products'!E2", "values": [["https://res/new.png"]]}]


def test_outbox_poison_row_does_not_block_others_and_dies(gs, fake_connection):
    ws = FakeWorksheet(SHEET)
    ws.fail_whole_batch = True
    ws.poison = {"'Products'!D3"}
    pending = [
        _outbox_row(1, 2, "https://res/milk.png", barcode=MILK),
        _outbox_row(2, 3, "https://res/laban.png", barcode=LABAN, attempts=0),
        _outbox_row(3, 4, "https://res/juice.png", barcode=JUICE),
    ]
    status = _final_status(_flush(gs, ws, pending, fake_connection))
    assert status[1] == "SYNCED" and status[3] == "SYNCED"
    assert status[2] == ("FAILED", 1)

    assert gs.reported == []                       # a FAILED write waits for its timed retry; nothing is final yet

    ws.sent_bodies.clear()
    pending = [_outbox_row(2, 3, "https://res/laban.png", barcode=LABAN, attempts=4)]
    status = _final_status(_flush(gs, ws, pending, fake_connection))
    assert status[2] == ("DEAD", 5)
    (dead,) = gs.reported                          # giving up is never silent
    assert (dead["id"], dead["status"], dead["row"], dead["column_key"]) == (2, "DEAD", 3, "link")


def test_outbox_identity_is_checked_per_write(gs, fake_connection):
    """Row 3 now holds Almarai Laban. A stale write for another product at row 3 must not ride on the
    verdict of the correct write, in either order."""
    ws = FakeWorksheet(SHEET)
    for stale_first in (True, False):
        ws.sent_bodies.clear()
        stale = _outbox_row(1 if stale_first else 2, 3, "https://res/masafi.png", barcode=MASAFI)
        good = _outbox_row(2 if stale_first else 1, 3, "https://res/laban.png", barcode=LABAN)
        status = _final_status(_flush(gs, ws, [stale, good], fake_connection))
        assert status[stale["id"]] == "CONFLICT" and status[good["id"]] == "SYNCED"
        written = [(d["range"], d["values"][0][0]) for body in ws.sent_bodies for d in body["data"]]
        assert written == [("'Products'!D3", "https://res/laban.png")]


def test_outbox_single_flusher(gs, fake_connection):
    """A process that cannot take the flush lock sends nothing (a delayed flusher cannot erase a newer value)."""
    ws = FakeWorksheet(SHEET)
    conn = _flush(gs, ws, [_outbox_row(1, 2, "", barcode=MILK)], fake_connection, lock_free=False)
    assert ws.sent_bodies == []
    assert not any("FROM sheet_updates" in s for s, _ in conn.executed)


# ---------------------------------------------------------------------------
# brand mapping sheet and tabs
# ---------------------------------------------------------------------------

def test_brand_mapping_columns(gs):
    rows = [["Brand", "Synonyms", "Excluded Competitors", "Sub-brands", "Official domains"],
            ["Nestle", "نستله, Nestlé", "Almarai", "Nido, KitKat", "nestle.ae, www.nestle-family.com"],
            ["Almarai", "المراعي", "", "", ""]]
    mappings = gs.parse_brand_mapping_rows(rows)
    assert mappings["nestle"]["sub_brands"] == ["Nido", "KitKat"]
    assert mappings["nestle"]["official_domains"] == ["nestle.ae", "www.nestle-family.com"]
    assert mappings["nestle"]["synonyms"][0] == "Nestle"
    assert mappings["almarai"]["sub_brands"] == [] and mappings["almarai"]["excluded_competitors"] == []

    class Sheet:
        def worksheet(self, title):
            assert title == "Brands Mapping"
            return FakeWorksheet(rows)

    class Client:
        def open_by_url(self, url):
            return Sheet()

    assert gs.get_brand_mappings(Client(), "https://docs.google.com/x")["nestle"]["sub_brands"] == ["Nido", "KitKat"]

    # the sub-brands reach the brand index used by the search engine
    from catalog_match.brand_index import build_index
    assert build_index(mappings).resolve("Nestle", "Nido Fortified Milk Powder 900g", "").conf == "mapped"


def test_open_worksheet_missing_tab_raises(gs, monkeypatch):
    import config
    monkeypatch.setattr(config, "SPREADSHEET_TAB_NAME", "Products 2026")

    class Tab:
        title = "Sheet1"

    class Sheet:
        def worksheet(self, title):
            raise gspread.exceptions.WorksheetNotFound(title)

        def worksheets(self):
            return [Tab()]

        def get_worksheet(self, idx):
            pytest.fail("must not fall back to the first tab")

    class Client:
        def open_by_url(self, url):
            return Sheet()

    with pytest.raises(gs.SheetConfigError) as exc:
        gs.open_worksheet(Client(), "https://docs.google.com/x")
    assert "Products 2026" in str(exc.value) and "Sheet1" in str(exc.value)


# ---------------------------------------------------------------------------
# Redis heartbeat gate and sync_worker
# ---------------------------------------------------------------------------

class FakeRedis:
    def __init__(self, heartbeat=False):
        self.kv = {}
        self.sets = {}
        if heartbeat:
            self.kv["writebehind:heartbeat"] = "1"

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

    def hincrby(self, key, field, amount=1):
        h = self.kv.setdefault(key, {})
        h[field] = int(h.get(field, 0)) + amount
        return h[field]

    def hdel(self, key, field):
        self.kv.get(key, {}).pop(field, None)


class RecordingQueue:
    def __init__(self):
        self.appended = []

    def append_update(self, *args, **kwargs):
        self.appended.append((args, kwargs))


def test_redis_heartbeat_gate(gs, monkeypatch):
    ws = FakeWorksheet(SHEET)
    queue = RecordingQueue()
    monkeypatch.setattr(gs, "_queue", queue)
    monkeypatch.setattr(gs, "_worker", object())

    no_beat = FakeRedis(heartbeat=False)
    monkeypatch.setattr(gs, "_get_redis", lambda: no_beat)
    assert gs.update_image_link(ws, 2, 3, "https://res/milk.png", barcode=MILK) is True
    (args, kwargs), = queue.appended
    assert args == (2, 3, "https://res/milk.png")
    # the column travels as the logical key 'link' (resolved at write time), never as a header name or index
    assert kwargs["col_key"] == "link" and "col_name" not in kwargs and kwargs["key_barcode"] == MILK
    assert no_beat.sets == {}

    live = FakeRedis(heartbeat=True)
    monkeypatch.setattr(gs, "_get_redis", lambda: live)
    assert gs.update_image_link(ws, 3, 3, "https://res/laban.png", barcode=LABAN) is True
    assert len(queue.appended) == 1                       # the outbox was not used this time
    payload = json.loads(live.kv["product:data:row_3"])
    assert payload["v"] == 2 and payload["row_index"] == 3
    assert payload["updates"] == {"link": "https://res/laban.png"}
    assert set(payload["seqs"]) == {"link"} and payload["seqs"]["link"] > 0
    assert payload["expect"] == {"barcode": LABAN}
    assert live.sets["writebehind:dirty_set"] == {"row_3"}


def test_update_image_link_reraises_api_errors(gs, monkeypatch):
    class Resp:
        status_code = 400
        text = "bad"

        def json(self):
            return {"error": {"code": 400, "message": "bad range", "status": "INVALID_ARGUMENT"}}

    class BrokenWS(FakeWorksheet):
        def update_cell(self, row, col, value):
            raise gspread.exceptions.APIError(Resp())

    monkeypatch.setattr(gs, "_queue", None)
    monkeypatch.setattr(gs, "_worker", None)
    monkeypatch.setattr(gs, "_get_redis", lambda: None)
    with pytest.raises(gspread.exceptions.APIError):
        gs.update_image_link(BrokenWS(SHEET), 2, 3, "https://res/milk.png")


def _v2(row, updates, expect=None, seq=7):
    payload = {"v": 2, "row_index": row, "updates": updates, "seqs": {k: seq for k in updates}}
    if expect:
        payload["expect"] = expect
    return json.dumps(payload)


def test_sync_worker_forwards_to_the_outbox_and_keeps_unknown_payloads(gs):
    """sync_worker never writes the sheet itself: every Redis payload becomes outbox rows (one write channel)."""
    import sync_worker

    r = FakeRedis()
    r.sadd("writebehind:dirty_set", "row_2")
    r.set("product:data:row_2", _v2(2, {"link": "https://res/milk.png"}, {"barcode": MILK}, seq=41))
    legacy = {"row_index": 5, "barcode": "", "cloudinary_url": "https://res/x.png"}   # old FastAPI shape
    r.sadd("writebehind:dirty_set", MILK)
    r.set(f"product:data:{MILK}", json.dumps(legacy))
    queue = RecordingQueue()

    assert sync_worker.run_sync_cycle(None, r, queue=queue, flush=False) == 1

    assert r.kv.get("writebehind:heartbeat")                       # the cycle refreshed the heartbeat
    (args, kwargs), = queue.appended
    assert args == (2, -1, "https://res/milk.png")
    assert kwargs["col_key"] == "link" and kwargs["seq"] == 41 and kwargs["key_barcode"] == MILK
    assert "row_2" not in r.smembers("writebehind:dirty_set") and "product:data:row_2" not in r.kv
    # the unknown payload is left exactly where it was
    assert MILK in r.smembers("writebehind:dirty_set")
    assert json.loads(r.kv[f"product:data:{MILK}"]) == legacy


def test_sync_worker_parks_legacy_positional_payloads(gs):
    """A payload from before logical column keys names its column by position: it cannot be verified, so it is
    parked and reported, never written."""
    import sync_worker

    r = FakeRedis()
    raw = json.dumps({"row_index": 2, "updates": {"3": "https://res/milk.png"}, "expect": {"barcode": MILK}})
    r.sadd("writebehind:dirty_set", "row_2")
    r.set("product:data:row_2", raw)
    queue = RecordingQueue()
    assert sync_worker.run_sync_cycle(None, r, queue=queue, flush=False) == 0
    assert queue.appended == []
    assert "row_2" in r.smembers("writebehind:conflicts") and "row_2" not in r.smembers("writebehind:dirty_set")
    assert r.kv["writebehind:conflict_payload:row_2"] == raw and "product:data:row_2" not in r.kv
    (outcome,) = gs.reported
    assert outcome["status"] == "CONFLICT" and outcome["row"] == 2 and "by position" in outcome["error"]


def test_sync_worker_keeps_key_when_forwarding_fails(gs):
    import sync_worker

    r = FakeRedis()
    raw = _v2(2, {"link": "https://res/a.png"})
    r.sadd("writebehind:dirty_set", "row_2")
    r.set("product:data:row_2", raw)

    class DownQueue:
        def append_update(self, *args, **kwargs):
            raise RuntimeError("MariaDB is down")

    assert sync_worker.run_sync_cycle(None, r, queue=DownQueue(), flush=False) == 0
    assert "row_2" in r.smembers("writebehind:dirty_set") and r.kv["product:data:row_2"] == raw
    queue = RecordingQueue()                                       # the database is back: forwarded next cycle
    assert sync_worker.run_sync_cycle(None, r, queue=queue, flush=False) == 1 and len(queue.appended) == 1


def test_sync_worker_payload_of_another_product_is_not_merged_into_the_next_write(gs, monkeypatch):
    """A pending payload for another product must not ride on a later write to that row."""
    import sync_worker
    r = FakeRedis(heartbeat=True)
    monkeypatch.setattr(gs, "_get_redis", lambda: r)
    assert gs._redis_write_behind(3, {"link": "https://res/masafi.png", "meta:description_en": "Water"},
                                  {"barcode": MASAFI})
    queue = RecordingQueue()
    assert sync_worker.run_sync_cycle(None, r, queue=queue, flush=False) == 1
    # the reviewer now publishes the product that really is in row 3: a fresh payload with its own identity
    assert gs._redis_write_behind(3, {"link": "https://res/laban.png"}, {"barcode": LABAN})
    assert sync_worker.run_sync_cycle(None, r, queue=queue, flush=False) == 1
    forwarded = [(a[2], k["col_key"], k["key_barcode"]) for a, k in queue.appended]
    assert forwarded == [("https://res/masafi.png", "link", MASAFI), ("Water", "meta:description_en", MASAFI),
                         ("https://res/laban.png", "link", LABAN)]


def test_redis_write_behind_refuses_to_merge_other_identity(gs, monkeypatch):
    r = FakeRedis(heartbeat=True)
    monkeypatch.setattr(gs, "_get_redis", lambda: r)
    assert gs._redis_write_behind(3, {"link": "https://res/a.png"}, {"barcode": MASAFI})
    assert gs._redis_write_behind(3, {"link": "https://res/b.png"}, {"barcode": LABAN}) is False
    # a legacy positional payload pending for the row is never merged into either
    r.set("product:data:row_4", json.dumps({"row_index": 4, "updates": {"3": "https://res/old.png"}}))
    assert gs._redis_write_behind(4, {"link": "https://res/c.png"}, {"barcode": JUICE}) is False


def test_sync_worker_keeps_an_update_that_arrived_during_the_forward(gs):
    import sync_worker
    r = FakeRedis()
    r.sadd("writebehind:dirty_set", "row_2")
    r.set("product:data:row_2", _v2(2, {"link": "https://res/a.png"}))

    class RacingQueue(RecordingQueue):
        def append_update(self, *args, **kwargs):
            super().append_update(*args, **kwargs)
            # metadata for the same row lands while the link is being forwarded
            r.set("product:data:row_2", _v2(2, {"link": "https://res/a.png", "meta:description_en": "desc"}))

    sync_worker.run_sync_cycle(None, r, queue=RacingQueue(), flush=False)
    assert "row_2" in r.smembers("writebehind:dirty_set")
    assert json.loads(r.kv["product:data:row_2"])["updates"]["meta:description_en"] == "desc"


# ---------------------------------------------------------------------------
# same-name siblings without a barcode: size and brand are part of the row identity
# ---------------------------------------------------------------------------

SIBLINGS = [
    ["Product Name", "Brand", "Size", "Drive Image Link"],
    ["Fresh Milk", "Almarai", "1L", ""],          # row 2
    ["Fresh Milk", "Almarai", "2L", ""],          # row 3
    ["Fresh Milk", "Al Rawabi", "2L", ""],        # row 4
]


def test_identity_without_barcode_compares_size_and_brand(gs):
    ws = FakeWorksheet(SIBLINGS)
    one_litre = gs._expectation(None, "Fresh Milk", "1L", "Almarai")
    almarai_2l = gs._expectation(None, "Fresh Milk", "2L", "Almarai")
    conflicts = gs.find_record_conflicts(ws, {
        "shifted_size": (3, one_litre),              # stale row number: the 1L write lands on the 2L sibling
        "shifted_brand": (4, almarai_2l),            # same name and size, other brand
        "ok": (2, gs._expectation(None, " fresh  MILK", "1 l", "ALMARAI")),   # whitespace/case do not matter
        "name_only": (3, gs._expectation(None, "Fresh Milk")),                # legacy expectation still accepted
    })
    assert set(conflicts) == {"shifted_size", "shifted_brand"}
    assert "size mismatch" in conflicts["shifted_size"] and "brand mismatch" in conflicts["shifted_brand"]

    # a sheet without Size/Brand columns falls back to the name alone instead of refusing every write
    no_cols = FakeWorksheet([["Product Name", "Drive Image Link"], ["Fresh Milk", ""]])
    assert gs.find_record_conflicts(no_cols, {"w": (2, one_litre)}) == {}


def test_outbox_sibling_write_with_stale_row_is_relocated_or_a_conflict(gs, fake_connection):
    ws = FakeWorksheet(SIBLINGS)
    stale = _outbox_row(1, 3, "https://res/milk-1l.png", name="Fresh Milk", size="1L", brand="Almarai")
    good = _outbox_row(2, 3, "https://res/milk-2l.png", name="Fresh Milk", size="2L", brand="Almarai")
    conn = _flush(gs, ws, [stale, good], fake_connection)
    status = _final_status(conn)
    # the sibling's write is neither superseded by nor lent the verdict of the correct write: it goes to the one
    # row that really holds the 1L product
    assert status[1] == "SYNCED" and status[2] == "SYNCED"
    written = sorted((d["range"], d["values"][0][0]) for body in ws.sent_bodies for d in body["data"])
    assert written == [("'Products'!D2", "https://res/milk-1l.png"), ("'Products'!D3", "https://res/milk-2l.png")]
    assert ("UPDATE sheet_updates SET `row_number` = %s, relocated_from = %s WHERE id = %s", (2, 3, 1)) in conn.executed

    # without the size, the name and brand match two rows: never guessed, stays a CONFLICT
    ws.sent_bodies.clear()
    vague = _outbox_row(3, 4, "https://res/milk.png", name="Fresh Milk", brand="Almarai")
    status = _final_status(_flush(gs, ws, [vague], fake_connection))
    assert status[3] == "CONFLICT" and ws.sent_bodies == []
    assert "2 rows match" in gs.reported[-1]["error"]


def test_writes_carry_size_and_brand_to_outbox_and_redis(gs, monkeypatch):
    import sync_worker

    ws = FakeWorksheet(SIBLINGS)
    queue = RecordingQueue()
    monkeypatch.setattr(gs, "_queue", queue)
    monkeypatch.setattr(gs, "_worker", object())
    monkeypatch.setattr(gs, "_get_redis", lambda: None)
    assert gs.update_image_link(ws, 3, 3, "https://res/milk-1l.png", product_name="Fresh Milk",
                                size="1L", brand="Almarai") is True
    (_, kwargs), = queue.appended
    assert (kwargs["key_name"], kwargs["key_size"], kwargs["key_brand"]) == ("Fresh Milk", "1L", "Almarai")

    r = FakeRedis(heartbeat=True)
    monkeypatch.setattr(gs, "_get_redis", lambda: r)
    assert gs.update_image_link(ws, 3, 3, "https://res/milk-1l.png", product_name="Fresh Milk",
                                size="1L", brand="Almarai") is True
    assert json.loads(r.kv["product:data:row_3"])["expect"] == {"name": "Fresh Milk", "size": "1L",
                                                                "brand": "Almarai"}
    forwarded = RecordingQueue()
    assert sync_worker.run_sync_cycle(None, r, queue=forwarded, flush=False) == 1
    (_, kwargs), = forwarded.appended                     # the outbox flush then verifies size and brand
    assert (kwargs["key_name"], kwargs["key_size"], kwargs["key_brand"]) == ("Fresh Milk", "1L", "Almarai")


def test_outbox_schema_stores_size_and_brand(gs, fake_connection):
    conn = fake_connection(lambda sql, params: None)
    queue = gs.SQLiteTransactionQueue.__new__(gs.SQLiteTransactionQueue)
    queue._connect = lambda: conn
    queue._setup_schema()
    queue.append_update(3, 3, "https://res/milk-1l.png", col_name="Drive Image Link", key_name="Fresh Milk",
                        key_size="1L", key_brand="Almarai")
    ddl = " ".join(sql for sql, _ in conn.executed)
    assert "ADD COLUMN IF NOT EXISTS key_size" in ddl and "ADD COLUMN IF NOT EXISTS key_brand" in ddl
    sql, params = conn.executed[-1]
    assert sql.startswith("INSERT INTO sheet_updates") and params[-2:] == ("1L", "Almarai")
