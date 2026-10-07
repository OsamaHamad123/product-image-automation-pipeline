"""sheet-save (cli_bridge.action_sheet_save): the Sheet link or name and the tab name are written to .env as
KEY="VALUE" lines. A line break or a quote in them used to add other keys to the file, and the file was rewritten in
place, so a crash in the middle left every credential gone. Now the values are checked (a Google Sheets link or a
sheet name; no control character, quote or backslash) and .env is replaced atomically with its mode kept.
"""

import os
import stat
import sys

import pytest

SHEET = "https://docs.google.com/spreadsheets/d/" + "1Ab" * 10 + "/edit#gid=0"
ENV_TEXT = ('# laqta\nDB_HOST=127.0.0.1\nSERPER_API_KEY="' + "-".join(("tst", "x", "7f3c9a1e")) + '"\n'
            'SPREADSHEET_NAME_OR_URL="old sheet"\nSPREADSHEET_TAB_NAME="Sheet1"\nGEMINI_MODEL=gemini\n')


@pytest.fixture
def bridge(offline, monkeypatch, tmp_path):
    import cli_bridge
    import config
    import google_sheets

    env = tmp_path / ".env"
    env.write_text(ENV_TEXT, encoding="utf-8")
    os.chmod(env, 0o640)
    monkeypatch.setattr(cli_bridge, "ENV_PATH", str(env))
    monkeypatch.setattr(cli_bridge, "LOG_PATH", str(tmp_path / "search.log"))
    monkeypatch.setattr(config, "SPREADSHEET_NAME_OR_URL", config.SPREADSHEET_NAME_OR_URL)
    monkeypatch.setattr(config, "SPREADSHEET_TAB_NAME", config.SPREADSHEET_TAB_NAME)
    cleared = []
    monkeypatch.setattr(google_sheets, "clear_cache", lambda: cleared.append(1))
    return cli_bridge, env, cleared


@pytest.mark.parametrize("url,tab", [
    (SHEET, ""), (SHEET, "Products 2026"), ("https://docs.google.com/spreadsheets/u/0/d/" + "x" * 30, "منتجات"),
    ("automation sheet", ""), ("كتالوج لقطة", "Sheet1"), (SHEET.replace("#gid=0", "?usp=sharing"), "A&B = C"),
])
def test_a_sheets_link_or_a_sheet_name_is_saved(bridge, url, tab):
    cli_bridge, env, cleared = bridge
    assert cli_bridge.action_sheet_save({"spreadsheet_url": f"  {url} ", "tab_name": tab})["status"] == "success"
    text = env.read_text(encoding="utf-8")
    assert f'SPREADSHEET_NAME_OR_URL="{url}"\n' in text and f'SPREADSHEET_TAB_NAME="{tab}"\n' in text
    keys = [line.split("=", 1)[0] for line in text.splitlines() if "=" in line and not line.startswith("#")]
    assert keys == ["DB_HOST", "SERPER_API_KEY", "SPREADSHEET_NAME_OR_URL", "SPREADSHEET_TAB_NAME", "GEMINI_MODEL"]
    import config
    assert (config.SPREADSHEET_NAME_OR_URL, config.SPREADSHEET_TAB_NAME) == (url, tab) and cleared == [1]


@pytest.mark.parametrize("url,tab", [
    (SHEET + '"\nCLOUDINARY_URL="cloudinary://attacker', ""),
    (SHEET, 'Sheet1"\nDB_HOST="10.0.0.5'),
    (SHEET + "\rX=1", ""),
    ("my sheet\nAPP_DEBUG=true", ""),
    ("it's mine", ""),
    (SHEET, "tab\\name"),
    (SHEET, "tab\x00name"),
    ("https://evil.example/spreadsheets/d/" + "x" * 30, ""),
    ("https://docs.google.com.evil.example/spreadsheets/d/" + "x" * 30, ""),
    ("http://docs.google.com/spreadsheets/d/" + "x" * 30, ""),
    ("https://docs.google.com/document/d/" + "x" * 30, ""),
    ("x" * 501, ""),
    (SHEET, "t" * 101),
    (["a", "b"], ""),
    (SHEET, {"x": 1}),
])
def test_a_value_that_could_change_other_keys_is_refused_and_env_is_untouched(bridge, url, tab):
    cli_bridge, env, cleared = bridge
    before = env.read_bytes()
    for action in (cli_bridge.action_sheet_save, cli_bridge.action_sheet_preview):
        result = action({"spreadsheet_url": url, "tab_name": tab})
        assert (result["status"], result["error_code"]) == ("failed", "invalid_sheet")
    assert env.read_bytes() == before and cleared == []


def test_an_empty_link_is_still_reported_as_missing(bridge):
    cli_bridge, env, _cleared = bridge
    assert cli_bridge.action_sheet_save({"spreadsheet_url": "  ", "tab_name": "x"})["error"] == \
        "Spreadsheet URL or name is required"
    assert cli_bridge.action_sheet_save({})["error"] == "Spreadsheet URL or name is required"


def test_env_is_replaced_atomically_with_its_mode_and_no_leftover(bridge, tmp_path):
    cli_bridge, env, _cleared = bridge
    assert cli_bridge.action_sheet_save({"spreadsheet_url": SHEET, "tab_name": "Products"})["status"] == "success"
    if sys.platform != "win32":             # Windows has no POSIX modes (chmod sets only the read-only bit)
        assert stat.S_IMODE(os.stat(env).st_mode) == 0o640
    assert sorted(p.name for p in tmp_path.iterdir() if p.name.startswith(".env")) == [".env"]
    lines = env.read_text(encoding="utf-8").splitlines()
    assert lines[:3] == ENV_TEXT.splitlines()[:3] and lines[-1] == "GEMINI_MODEL=gemini"   # everything else kept


def test_a_crash_while_saving_leaves_the_old_env_whole(bridge, monkeypatch, tmp_path):
    cli_bridge, env, cleared = bridge
    before = env.read_bytes()

    def crash(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(cli_bridge.os, "replace", crash)
    result = cli_bridge.action_sheet_save({"spreadsheet_url": SHEET, "tab_name": ""})
    assert result["status"] == "failed" and "disk full" not in str(result)
    assert env.read_bytes() == before and cleared == []
    assert sorted(p.name for p in tmp_path.iterdir() if p.name.startswith(".env")) == [".env"]


def test_keys_missing_from_env_are_appended(bridge):
    cli_bridge, env, _cleared = bridge
    env.write_text("DB_HOST=127.0.0.1", encoding="utf-8")         # no final newline, no sheet keys
    assert cli_bridge.action_sheet_save({"spreadsheet_url": "automation sheet", "tab_name": "T"})["status"] == "success"
    assert env.read_text(encoding="utf-8") == ('DB_HOST=127.0.0.1\nSPREADSHEET_NAME_OR_URL="automation sheet"\n'
                                               'SPREADSHEET_TAB_NAME="T"\n')
