"""Sheet-write integrity: transient Google errors (429 / 5xx / connection) are retried when the sheet is opened and
read, and a real 'not found / no access' is reported distinctly from a transient error.

Google Sheets is an in-memory fake; every connection is refused.
"""

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
    monkeypatch.setattr(google_sheets, "_sleep", lambda seconds: None)
    return google_sheets


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

