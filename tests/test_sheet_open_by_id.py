"""Opening the sheet by name is a Drive search (~4.5 s on the server, most of the review list's reload after every
approval). The id found by name is remembered for SHEET_IDS_TTL and the next opens go by id; an id that no longer
opens falls back to the name at once."""

import types

import pytest

import google_sheets


class NotFound(Exception):
    pass


def _client(**methods):
    # a stand-in that passes for gspread's own client (only the real client remembers ids)
    cls = type("Client", (), {k: staticmethod(v) for k, v in methods.items()})
    cls.__module__ = "gspread.client"
    return cls()


@pytest.fixture(autouse=True)
def cache_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(google_sheets, "CACHE_DIR", str(tmp_path))


def test_the_second_open_goes_by_the_remembered_id():
    calls = []
    sheet = types.SimpleNamespace(id="key-1")
    client = _client(open=lambda name: calls.append(("name", name)) or sheet,
                     open_by_key=lambda key: calls.append(("key", key)) or sheet)
    assert google_sheets._open_spreadsheet(client, "automation sheet") is sheet
    assert google_sheets._open_spreadsheet(client, "automation sheet") is sheet
    assert calls == [("name", "automation sheet"), ("key", "key-1")]


def test_an_id_that_no_longer_opens_falls_back_to_the_name_and_is_replaced():
    calls = []

    def by_key(key):
        calls.append(("key", key))
        raise NotFound(key)

    client = _client(open=lambda name: calls.append(("name", name)) or types.SimpleNamespace(id="key-2"),
                     open_by_key=by_key)
    google_sheets._remember_sheet_id("automation sheet", "gone")
    assert google_sheets._open_spreadsheet(client, "automation sheet").id == "key-2"
    assert calls == [("key", "gone"), ("name", "automation sheet")]
    assert google_sheets._remembered_sheet_id("automation sheet") == "key-2"


def test_another_sheet_name_is_looked_up_by_its_name(monkeypatch):
    google_sheets._remember_sheet_id("old sheet", "key-old")
    assert google_sheets._remembered_sheet_id("new sheet") is None
    monkeypatch.setattr(google_sheets, "SHEET_IDS_TTL", -1)              # expired: the name again
    assert google_sheets._remembered_sheet_id("old sheet") is None


def test_a_fake_client_never_writes_the_id_file(tmp_path):
    fake = types.SimpleNamespace(open=lambda name: types.SimpleNamespace(id="k"))
    google_sheets._open_spreadsheet(fake, "automation sheet")
    assert not (tmp_path / google_sheets.SHEET_IDS_CACHE).exists()
