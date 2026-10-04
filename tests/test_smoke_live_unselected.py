"""scripts/smoke_live.py, honest about the live run of 2026-10-03 (runs/2026-10-03/smoke_6.json).

* Why a row has no pick is read on the listings that name the brand (tier 1 or 2): rows 38 and 41 were
  'label reader saw another product' because it had rejected other brands' store listings, while only
  social-network posts had shown the product; rows 3 and 26 had no listing of the brand at all.
* The run says how many Brands Mapping entries it uses and warns when none of its brands is mapped:
  smoke_6.json ran without any mapping and nothing said so.
"""

import csv
import importlib.util
import json
import types
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
RUN = REPO / "runs" / "2026-10-03"


@pytest.fixture(scope="module")
def script():
    spec = importlib.util.spec_from_file_location("smoke_live_unselected_under_test", REPO / "scripts" / "smoke_live.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def live(script):
    rows = script.load_run(RUN / "smoke_6.json")["rows"]
    return {r["row"]: r for r in rows}


@pytest.mark.parametrize("row, reason, before", [
    (38, "only_social", "verifier_mismatch"),     # KABANI: Facebook posts; the Lulu MISMATCHes were other brands
    (41, "only_social", "verifier_mismatch"),     # MAHRA: Instagram posts; Eastern's Lulu listing was read
    (29, "only_social", "download_failed"),       # CHALIYAR: Instagram and Facebook posts only
    (3, "brand_not_found", "verifier_mismatch"),  # BARTS TRADITON: Emborg, Simplot, Fresh St listings only
    (26, "brand_not_found", "verifier_mismatch"),  # AMERICAN GOLD: Goody, Americana listings only
    (16, "verifier_mismatch", "verifier_mismatch"),  # MEHRAN: its own listings were read as other products
    (28, "unsure", "unsure"),                     # AMERICAN LIGHT: the brand's listings were read UNSURE
])
def test_why_no_pick_is_read_on_the_brands_own_listings(script, live, row, reason, before):
    assert script.unselected_reason(live[row]) == reason, (row, before)


def test_the_summary_of_the_live_run(script, live):
    s = script.summarize(list(live.values()))
    rows = s["unselected_rows"]
    assert {29, 38, 41} <= set(rows["only_social"]) and {3, 26} <= set(rows["brand_not_found"])
    assert not {3, 26, 38, 41} & set(rows["verifier_mismatch"])
    text = script.format_summary(s)
    assert "brand not found (no listing names it)" in text and "only social-media images" in text


def _identity():
    from catalog_match import identity
    return types.SimpleNamespace(build_sku_spec=identity.build_sku_spec)


def _rows():
    with open(RUN / "rows_2_61.csv", encoding="utf-8-sig", newline="") as fh:
        return [{"row_number": int(r["row"]), "name": r["name"], "brand": r["brand"]} for r in csv.DictReader(fh)]


def test_a_run_without_any_brands_mapping_says_so(script):
    lines = script.mapping_report(_rows(), {}, _identity())
    assert lines[0] == "Brands Mapping: 0 entries loaded | 0 of 60 rows have a mapped brand"
    assert lines[1].startswith("WARNING: none of this run's brands is in the Brands Mapping")
    assert "--brands-file" in lines[1]


def test_a_run_with_the_suggested_mapping_counts_its_entries_and_rows(script):
    mappings = script.read_brands_file(str(RUN / "brands_mapping_suggested.csv"))
    lines = script.mapping_report(_rows(), mappings, _identity())
    assert lines == [f"Brands Mapping: {len(mappings)} entries loaded | 60 of 60 rows have a mapped brand"]
    learned = dict(mappings, **{"x": {"brand": "X", "learned": True}})
    assert "+ 1 learned from reviews" in script.mapping_report(_rows()[:1], learned, _identity())[0]


def test_a_new_record_says_whether_the_brand_was_found(script):
    record = {"row": 7, "decision": "REVIEW_UNSELECTED", "failure_code": None, "top": [], "reject_counts": {},
              "verdicts": {}, "brand_found": False, "only_social": False}
    assert script.unselected_reason(record) == "brand_not_found"
    social = dict(record, failure_code="SOCIAL_ONLY", brand_found=True)
    assert script.unselected_reason(social) == "only_social"
    assert json.loads(json.dumps(record)) == record
