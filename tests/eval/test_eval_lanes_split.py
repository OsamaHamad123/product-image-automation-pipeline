"""Per-lane metrics and the fixed held-out split (metrics.lane_table, metrics.split_of, scripts/eval_split.py).

The lane numbers are checked against counts worked out by hand; the split must be the same for the same row in
every set and run, keep every row a regression test is named after out of the held-out share, and hold out about
HOLDOUT_SHARE of the rest.
"""

import importlib.util
import json
from pathlib import Path

import pytest

import harness
import metrics
import runners
from metrics import Outcome

pytestmark = pytest.mark.eval

REPO = Path(__file__).resolve().parent.parent.parent


def _script(name):
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _labels():
    def lab(cands, split="dev"):
        return {"stratum": "x", "no_correct_candidate": False, "named_case": None, "expected_v2": None,
                "split": split, "candidates": cands}

    return {
        "a": lab({"c1": "correct_exact", "c2": "wrong_size"}),
        "b": lab({"c1": "correct_exact", "c2": "wrong_size"}),
        "c": lab({"c1": "correct_exact", "c2": "wrong_brand"}, "held_out"),
        "d": lab({"c1": "correct_exact"}, "held_out"),
        "e": lab({"c1": "correct_exact", "c2": "wrong_variant"}),
        "f": lab({"c1": "correct_exact"}),
    }


def _pick(sku, cid, lane, auto=False):
    return Outcome(sku_id=sku, engine="t", decision=metrics.AUTO if auto else metrics.PRESELECTED, chosen_id=cid,
                   chosen_url=f"u/{sku}/{cid}", auto=auto, needs_review=not auto, lane=lane)


def _outcomes():
    return [
        _pick("a", "c1", "strict", auto=True),         # strict, auto, correct
        _pick("b", "c2", "strict", auto=True),         # strict, auto, WRONG
        _pick("c", "c1", "strict"),                    # strict, review, correct (held out)
        _pick("d", "c1", "unsure"),                    # unsure, correct (held out)
        _pick("e", "c2", "other"),                     # other, wrong
        Outcome(sku_id="f", engine="t", decision=metrics.UNSELECTED),   # no pick
    ]


def test_lane_table_counts_precision_wilson_coverage_and_wrong_auto():
    lanes = metrics.lane_table(_outcomes(), _labels())
    strict = lanes["strict"]
    assert (strict["n_picks"], strict["n_correct"], strict["n_auto"], strict["n_auto_wrong"]) == (3, 2, 2, 1)
    assert strict["precision"] == round(2 / 3, 4)
    assert strict["precision_wilson_lower"] == round(metrics.wilson_lower_bound(2, 3), 4)
    assert strict["coverage"] == 0.5 and strict["wrong_auto_rate"] == round(1 / 6, 4)
    assert (lanes["unsure"]["n_picks"], lanes["unsure"]["precision"]) == (1, 1.0)
    assert (lanes["other"]["n_picks"], lanes["other"]["precision"], lanes["other"]["n_auto_wrong"]) == (1, 0.0, 0)
    assert metrics.NO_LANE not in lanes                  # every pick carried a lane


def test_a_pick_without_a_lane_is_listed_as_unknown_and_an_empty_lane_has_no_precision():
    lanes = metrics.lane_table([_pick("a", "c1", None)], _labels())
    assert lanes[metrics.NO_LANE]["n_picks"] == 1
    assert lanes["strict"]["n_picks"] == 0 and lanes["strict"]["precision"] is None
    assert lanes["strict"]["precision_wilson_lower"] is None


def test_compute_breaks_the_lanes_down_per_split():
    m = metrics.compute(_outcomes(), _labels())
    assert m["per_lane"]["strict"]["n_picks"] == 3
    held, dev = m["per_split"]["held_out"], m["per_split"]["dev"]
    assert (held["n_skus"], dev["n_skus"]) == (2, 4)
    assert held["per_lane"]["strict"]["n_picks"] == 1 and held["per_lane"]["unsure"]["n_picks"] == 1
    assert dev["per_lane"]["strict"]["n_auto_wrong"] == 1 and dev["n_auto_wrong"] == 1
    table = harness.lane_table(m)
    assert "held_out (2)" in table and "dev (4)" in table and "ALL (6)" in table


def test_the_split_is_fixed_per_row_and_holds_out_about_its_share():
    names = [f"BRAND{i} PRODUCT {i} {i % 7}00GM" for i in range(2000)]
    first = [metrics.split_of({"name_en": n}, tuned=set()) for n in names]
    again = [metrics.split_of({"name_en": "  " + n.lower() + " ", "id": "other-id"}, tuned=set()) for n in names]
    assert first == again                                # case, spacing and the set's own id never move a row
    share = first.count("held_out") / len(first)
    assert abs(share - metrics.HOLDOUT_SHARE) < 0.04


def test_a_row_named_by_a_regression_test_is_never_held_out_and_counts_as_a_leak():
    held = next(f"ROW {i} 100GM" for i in range(1000) if metrics.in_holdout_share(metrics.row_key(f"ROW {i} 100GM")))
    assert metrics.split_of({"name_en": held}, tuned=set()) == "held_out"
    tuned = {metrics.row_key(held)}
    assert metrics.split_of({"name_en": held}, tuned=tuned) == "dev"
    assert metrics.holdout_leaks([{"name_en": held}], tuned) == [metrics.row_key(held)]
    # a set may pin a row (a recorded export keeps the split it was exported with)
    assert metrics.split_of({"name_en": held, "split": "held_out"}, tuned=tuned) == "held_out"


def test_the_committed_tuned_rows_are_named_by_committed_tests():
    doc = json.loads(metrics.TUNED_ROWS_PATH.read_text(encoding="utf-8"))
    rows = doc["rows"]
    assert len(rows) >= 50
    eval_split = _script("eval_split")
    named = {metrics.row_key(r["name"]) for r in eval_split.named_by_tests(rows)}
    assert named == {metrics.row_key(r["name"]) for r in rows}
    # only names: no link, image or reading ever goes into the list
    assert all(set(r) <= {"name", "brand", "tests"} for r in rows)
    assert "ZWAN CHICKEN LUNCHEON MEAT 340GM" in metrics.tuned_rows()


def test_key_words_need_the_brand_and_two_product_words_on_one_line():
    eval_split = _script("eval_split")
    assert eval_split.key_words("ZWAN CHICKEN LUNCHEON MEAT 340GM", "ZWAN") == ("ZWAN", "CHICKEN", "LUNCHEON")
    lines = [("t1.py", {"ZWAN", "CHICKEN"}), ("t2.py", {"ZWAN", "CHICKEN", "LUNCHEON", "X"})]
    assert eval_split.named_by_tests([{"name": "ZWAN CHICKEN LUNCHEON MEAT 340GM", "brand": "ZWAN"}], lines) == [
        {"name": "ZWAN CHICKEN LUNCHEON MEAT 340GM", "brand": "ZWAN", "tests": ["t2.py"]}]


def test_the_v2_replay_records_the_lane_of_its_pick(golden, cassette):
    sku = next(s for s in golden["skus"] if s["id"] == "uae-001-almarai-full-fat-milk-1l")
    with runners.network_blocked():
        out = runners.run_v2(sku, cassette)
    assert out.chosen_id == "c1" and out.lane in metrics.LANES
    if out.auto:
        assert out.lane == "strict"
    none = next(s for s in golden["skus"] if s["no_correct_candidate"])
    with runners.network_blocked():
        empty = runners.run_v2(none, cassette)
    assert empty.chosen_id is not None or empty.lane is None
