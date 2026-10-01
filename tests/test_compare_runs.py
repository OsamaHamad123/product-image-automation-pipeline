"""scripts/compare_runs.py on two dry runs: the rows that changed decision first, the summary delta, plain
UTF-8 output (Arabic names intact) and a Markdown table the owner can paste. The older run is the plain
list format of the owner's 2026-09-30 output; the newer one is this version's format."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
RUNS = REPO / "tests" / "fixtures" / "smoke_runs"
BEFORE, AFTER = RUNS / "run_before.json", RUNS / "run_after.json"
ARABIC = "حليب المراعي كامل الدسم 1 لتر"


@pytest.fixture(scope="module")
def compare():
    scripts = str(REPO / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    spec = importlib.util.spec_from_file_location("compare_runs_under_test", REPO / "scripts" / "compare_runs.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _runs(compare):
    return compare.smoke_live.load_run(BEFORE), compare.smoke_live.load_run(AFTER)


def test_rows_that_changed_decision_come_first(compare):
    old, new = _runs(compare)
    diff = compare.diff_rows(old["rows"], new["rows"])
    order = [(c["row"], c["kind"]) for c in diff["changed"]]
    assert order == [(3, "gained"), (14, "gained"), (20, "gained"), (61, "gained"),
                     (21, "decision"), (55, "decision"),
                     (40, "image"), (8, "warnings")]
    assert diff["unchanged"] == [2, 15, 30, 45, 50]
    assert diff["only_old"] == [] and diff["only_new"] == []
    by_row = {c["row"]: c for c in diff["changed"]}
    assert by_row[3]["old"]["reason"] == "verifier_mismatch" and by_row[3]["new"]["domain"] == "luluhypermarket.com"
    assert by_row[20]["old"]["outage"] is True and by_row[20]["new"]["warnings"] == ["vlm_unsure"]
    assert by_row[55]["old"]["decision"] == "ERROR" and by_row[55]["new"]["reason"] == "weak_only"
    assert by_row[40]["old"]["domain"] == by_row[40]["new"]["domain"] == "amazon.ae"


def test_a_lost_pick_and_rows_in_one_run_only(compare):
    old, new = _runs(compare)
    swapped = compare.diff_rows(new["rows"], old["rows"][:-1])        # row 61 only in the first run
    kinds = [c["kind"] for c in swapped["changed"]]
    assert kinds[:2] == ["lost", "lost"]                              # rows 3 and 14 lose their pick
    assert [v["row"] for v in swapped["only_old"]] == [61] and swapped["only_new"] == []


def test_summary_delta(compare):
    old, new = _runs(compare)
    s_old, s_new = compare.smoke_live.summarize(old["rows"]), compare.smoke_live.summarize(new["rows"])
    delta = {label: (o, n, c) for label, o, n, c in compare.summary_delta(s_old, s_new)}
    assert delta["coverage %"] == ("40.0", "61.5", "+21.5")
    assert delta["pre-selected"] == ("4", "8", "+4")
    assert delta["outage rows excluded"] == ("2", "0", "-2")
    assert delta["no pick: label reader saw another product"] == ("2", "1", "-1")
    assert delta["no pick: only social-media images"] == ("1", "1", "=")
    assert delta["picks found by the expansion round"] == ("0", "2", "+2")
    # per measured product: the old run's two outage rows are not in its average
    assert delta["estimated cost $ per measured product"] == ("0.0040", "0.0041", "+0.0001")
    assert delta["picks by provider: page"] == ("0", "1", "+1")
    assert delta["warning on picks: low_resolution"] == ("1", "0", "-1")


def test_plain_report_is_utf8_with_arabic_names(compare, tmp_path, capsys):
    out = tmp_path / "compare.txt"
    assert compare.main([str(BEFORE), str(AFTER), "--out", str(out)]) == 0
    text = out.read_text(encoding="utf-8")
    assert ARABIC in text
    first_changed = text.index("ROWS WHOSE DECISION CHANGED")
    assert text.index("row 3 ") > first_changed
    assert text.index("SAME DECISION, OTHER PICK") > text.index("row 55 ")
    assert "REVIEW_UNSELECTED -> REVIEW_PRESELECTED | site - -> luluhypermarket.com" in text
    assert "PROVIDER_DOWN (outage) -> REVIEW_PRESELECTED" in text
    assert "UNCHANGED ROWS (5): 2, 15, 30, 45, 50" in text
    assert "comparison written to" in capsys.readouterr().out

    assert compare.main([str(BEFORE), str(AFTER)]) == 0
    assert ARABIC in capsys.readouterr().out


def test_markdown_tables(compare, tmp_path, capsys):
    assert compare.main([str(BEFORE), str(AFTER), "--md"]) == 0
    md = capsys.readouterr().out
    assert "| metric | old | new | change |" in md and "| coverage % | 40.0 | 61.5 | +21.5 |" in md
    assert "### Rows whose decision changed (6)" in md
    assert "| 3 | gained a pick | REVIEW_UNSELECTED -> REVIEW_PRESELECTED | - -> luluhypermarket.com |" in md
    assert ARABIC in md
    lines = [ln for ln in md.splitlines() if ln.startswith("| 3 |")]
    assert len(lines) == 1 and lines[0].count("|") == 8          # 7 cells: no stray pipe from the data


def test_markdown_escapes_pipes_in_names(compare, tmp_path):
    rows = json.loads(BEFORE.read_text(encoding="utf-8"))
    rows[0]["name"] = "AIDA | FRIES 1KG"
    old = tmp_path / "old.json"
    old.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    new_doc = json.loads(AFTER.read_text(encoding="utf-8"))
    new_doc["rows"][0]["decision"] = "REVIEW_UNSELECTED"
    new_doc["rows"][0]["name"] = "AIDA | FRIES 1KG"
    new = tmp_path / "new.json"
    new.write_text(json.dumps(new_doc, ensure_ascii=False), encoding="utf-8")
    report = compare.format_markdown(compare.smoke_live.load_run(old), compare.smoke_live.load_run(new), "o", "n")
    line = next(ln for ln in report.splitlines() if ln.startswith("| 2 |"))
    assert "AIDA \\| FRIES 1KG" in line and "lost its pick" in line


def test_a_row_that_holds_another_product_is_flagged(compare, tmp_path):
    """Rows are matched by sheet row number: when the sheet changed between the runs (a row inserted or
    deleted), the same number holds another product and the row diff is not like for like."""
    new_doc = json.loads(AFTER.read_text(encoding="utf-8"))
    row8 = next(r for r in new_doc["rows"] if r["row"] == 8)
    old_name = row8["name"]
    row8["name"] = "MEHRAN MIXED PICKLE 1KG"
    row15 = next(r for r in new_doc["rows"] if r["row"] == 15)
    row15["name"] = "  " + row15["name"].lower() + " "            # case and spacing alone are the same product
    new = tmp_path / "new.json"
    new.write_text(json.dumps(new_doc, ensure_ascii=False), encoding="utf-8")
    old_doc, new_doc = compare.smoke_live.load_run(BEFORE), compare.smoke_live.load_run(new)
    diff = compare.diff_rows(old_doc["rows"], new_doc["rows"])
    assert diff["renamed"] == [{"row": 8, "old": old_name, "new": "MEHRAN MIXED PICKLE 1KG"}]
    plain = compare.format_plain(old_doc, new_doc, "old.json", "new.json")
    assert "ROWS THAT HOLD ANOTHER PRODUCT (1)" in plain
    assert f"row 8     {old_name} -> MEHRAN MIXED PICKLE 1KG" in plain
    assert plain.index("ROWS THAT HOLD ANOTHER PRODUCT") < plain.index("ROWS WHOSE DECISION CHANGED")
    md = compare.format_markdown(old_doc, new_doc, "old.json", "new.json")
    assert "Rows that hold another product (1)" in md and "MEHRAN MIXED PICKLE 1KG" in md
    same = compare.format_plain(old_doc, compare.smoke_live.load_run(AFTER), "o", "n")
    assert "ANOTHER PRODUCT" not in same


def test_a_file_that_is_not_a_run_is_refused(compare, tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"hello": 1}), encoding="utf-8")
    assert compare.main([str(bad), str(AFTER)]) == 2
    assert "cannot read the runs" in capsys.readouterr().err
