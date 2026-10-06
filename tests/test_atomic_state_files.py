"""JSON state files are written atomically (runtime package, item 7).

products_cache.json / brand_mappings_cache.json (google_sheets._write_cache) and temp/batch_progress.json
(main.save_progress) used open(path, "w"): the file is emptied first, so a reader in another process or a power cut
during the write left an empty or half-written file. Now: a uniquely named temporary file, then os.replace.
"""

import json
import os

import pytest

import atomic_file


def _no_temp_files(folder):
    return [n for n in os.listdir(folder) if n.endswith(".tmp")] == []


def test_write_json_replaces_the_file_in_one_step(tmp_path):
    path = tmp_path / "state" / "x.json"
    atomic_file.write_json(str(path), {"a": "ب"}, ensure_ascii=False)
    assert json.loads(path.read_text(encoding="utf-8")) == {"a": "ب"}
    assert _no_temp_files(path.parent)


def test_a_write_that_fails_keeps_the_old_file_whole(tmp_path):
    path = tmp_path / "x.json"
    atomic_file.write_json(str(path), {"old": 1})
    with pytest.raises(TypeError):
        atomic_file.write_json(str(path), {"bad": object()})
    assert json.loads(path.read_text(encoding="utf-8")) == {"old": 1}
    assert _no_temp_files(tmp_path)


def test_a_reader_holding_the_file_on_windows_is_waited_for(tmp_path, monkeypatch):
    path = tmp_path / "x.json"
    real = os.replace
    refused = {"n": 2}

    def replace(src, dst):
        if refused["n"]:
            refused["n"] -= 1
            raise PermissionError(13, "in use")
        return real(src, dst)

    monkeypatch.setattr(atomic_file.os, "replace", replace)
    monkeypatch.setattr(atomic_file.time, "sleep", lambda s: None)
    atomic_file.write_json(str(path), {"n": 1})
    assert json.loads(path.read_text()) == {"n": 1}

    refused["n"] = 99
    with pytest.raises(PermissionError):
        atomic_file.write_json(str(path), {"n": 2})
    assert json.loads(path.read_text()) == {"n": 1} and _no_temp_files(tmp_path)


def _crash_mid_write(monkeypatch, module):
    """json.dump writes half of the document, then the process 'dies'."""
    real_dump = json.dump

    def dump(obj, fh, **kw):
        fh.write('{"products": [')
        raise OSError("power cut")

    monkeypatch.setattr(module.json, "dump", dump)
    return real_dump


def test_the_sheet_cache_survives_a_crash_during_its_write(offline, monkeypatch, tmp_path):
    import google_sheets

    monkeypatch.setattr(google_sheets, "CACHE_DIR", str(tmp_path))
    google_sheets._write_cache("products_cache.json", {"products": [{"row_number": 5}], "link_idx": 7},
                               google_sheets.PRODUCTS_CACHE_VERSION)
    assert google_sheets.cached_products() == [{"row_number": 5}]
    _crash_mid_write(monkeypatch, atomic_file)
    google_sheets._write_cache("products_cache.json", {"products": [], "link_idx": 7},
                               google_sheets.PRODUCTS_CACHE_VERSION)               # logged, never raised
    assert google_sheets.cached_products() == [{"row_number": 5}], "the cache was left half-written"
    assert _no_temp_files(tmp_path)


def test_the_batch_progress_survives_a_crash_during_its_write(offline, monkeypatch, tmp_path):
    import main

    monkeypatch.chdir(tmp_path)
    main.save_progress(3, 10, 2, 1, "حليب")
    path = tmp_path / "temp" / "batch_progress.json"
    assert json.loads(path.read_text(encoding="utf-8"))["current_product"] == "حليب"
    _crash_mid_write(monkeypatch, atomic_file)
    main.save_progress(4, 10, 3, 1, "لبن")
    assert json.loads(path.read_text(encoding="utf-8"))["current"] == 3
    assert _no_temp_files(path.parent)
