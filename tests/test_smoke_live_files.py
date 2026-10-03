"""scripts/smoke_live.py without the Google Sheet: products from a file (--rows-file) and Brands Mapping
from a CSV (--brands-file), so a dry run can run on a machine with the keys but no credentials.json.

The committed files are the 60 products of the live run of 2026-10-03 (runs/2026-10-03/rows_2_61.csv,
taken from smoke_6.json) and the suggested Brands Mapping (runs/2026-10-03/brands_mapping_suggested.csv).
"""

import importlib.util
import json
from pathlib import Path

import pytest

from catalog_match.identity import build_sku_spec

REPO = Path(__file__).resolve().parents[1]
RUNS = REPO / "runs" / "2026-10-03"


@pytest.fixture(scope="module")
def script():
    spec = importlib.util.spec_from_file_location("smoke_live_files_under_test", REPO / "scripts" / "smoke_live.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_committed_rows_file_holds_the_60_products_of_the_live_run(script):
    rows = script.read_rows_file(RUNS / "rows_2_61.csv")
    assert [r["row_number"] for r in rows] == list(range(2, 62))
    assert rows[0]["name"] == "AIDA FRENCH FRIES 1KG" and rows[0]["brand"] == "AIDA"
    saved = {r["row"]: (r["name"], r["brand"]) for r in json.loads((RUNS / "smoke_6.json").read_text("utf-8"))}
    assert {r["row_number"]: (r["name"], r["brand"]) for r in rows} == saved


def test_rows_pick_rows_from_the_file_and_an_earlier_run_works_as_a_rows_file(script):
    picked = script.read_rows_file(RUNS / "rows_2_61.csv", script.parse_rows(["45", "49-50"]))
    assert [(r["row_number"], r["brand"]) for r in picked] == [(45, "RIO MARIE"), (49, "SUP/T"), (50, "SUPER T/")]
    from_run = script.read_rows_file(RUNS / "smoke_6.json", [16])
    assert from_run == [{"row_number": 16, "name": "MEHRAN PLAIN PARATHA 400GM 5S", "name_ar": "", "brand": "MEHRAN",
                         "brand_ar": "", "barcode": "", "category": "", "size": ""}]


def test_a_csv_with_the_sheets_own_headers_and_no_row_column(script, tmp_path):
    path = tmp_path / "rows.csv"
    path.write_text("﻿Item Name,Brand Name,EAN,Product Name Arabic\n"
                    "Almarai Full Fat Milk 1L,ALMARAI,6281007000000,حليب المراعي\n,,,\n"
                    "Rio Marie Tuna 70g,RIO MARIE,,\n", encoding="utf-8")
    rows = script.read_rows_file(path)
    assert [(r["row_number"], r["name"], r["brand"], r["barcode"]) for r in rows] == [
        (2, "Almarai Full Fat Milk 1L", "ALMARAI", "6281007000000"), (4, "Rio Marie Tuna 70g", "RIO MARIE", "")]
    assert rows[0]["name_ar"] == "حليب المراعي"


def test_the_brands_file_maps_the_live_runs_brands(script):
    mappings = script.read_brands_file(RUNS / "brands_mapping_suggested.csv")
    assert len(mappings) == 43
    rio = build_sku_spec({"name": "RIO MARIE LIGHT MEAT TUNA IN SUN OIL 3X70GM", "brand": "RIO MARIE"}, mappings)
    assert rio.brand_conf == "mapped" and "rio mare" in rio.match_brands
    sup = build_sku_spec({"name": "SUP/T WT/MEAT SOLIDTUNA SALTWATER 185GM", "brand": "SUP/T"}, mappings)
    assert sup.brand_canonical == "Super Tasty" and "california garden" in sup.competitors
    assert mappings["yumway"]["official_domains"] == ["yumwayfood.com"]


def test_a_file_run_needs_no_sheet(script, monkeypatch, capsys):
    monkeypatch.setattr(script, "open_sheet_read_only", lambda: pytest.fail("a file run must not open the sheet"))
    seen = {}

    def fake_row(row, mappings, *a, **kw):
        seen[row["row_number"]] = (row["brand"], len(mappings))
        return {"row": row["row_number"], "name": row["name"], "brand": row["brand"], "decision": "NOT_FOUND",
                "cost_usd": 0.0}

    monkeypatch.setattr(script, "run_row", fake_row)
    monkeypatch.setattr(script, "print_row", lambda r: None)
    monkeypatch.setattr(script, "summarize", lambda results: {})
    monkeypatch.setattr(script, "format_summary", lambda summary: "")
    code = script.main(["--rows-file", str(RUNS / "rows_2_61.csv"), "--rows", "45",
                        "--brands-file", str(RUNS / "brands_mapping_suggested.csv"), "--dry-run"])
    assert code in (0, None) and seen == {45: ("RIO MARIE", 43)}
    assert "brand mappings: " in capsys.readouterr().out
