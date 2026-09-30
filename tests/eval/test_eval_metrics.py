"""metrics.compute on hand-built outcomes, checked against numbers worked out by hand."""

import pytest

import metrics
from metrics import Outcome

pytestmark = pytest.mark.eval


def _labels():
    def lab(stratum, cands, no_correct=False):
        return {"stratum": stratum, "no_correct_candidate": no_correct, "named_case": None, "expected_v2": None,
                "candidates": cands, "download": {c: "ok" for c in cands}}

    return {
        "s1": lab("A", {"c1": "correct_exact", "c2": "wrong_variant"}),
        "s2": lab("A", {"c1": "correct_exact", "c2": "not_packshot"}),
        "s3": lab("A", {"c1": "correct_exact"}),
        "s4": lab("B", {"c1": "correct_exact", "c2": "wrong_size"}),
        "s5": lab("B", {"c1": "correct_exact", "c2": "wrong_size"}),
        "s6": lab("B", {"c1": "correct_exact"}),
        "s7": lab("B", {"c1": "correct_exact", "c2": "wrong_brand"}),
        "s8": lab("C", {"c1": "wrong_brand"}, no_correct=True),
        "s9": lab("C", {"c1": "not_packshot"}, no_correct=True),
        "s10": lab("C", {"c1": "correct_exact", "c2": "unusable"}),
    }


def _auto(sku, cid, pool, kills=None):
    return Outcome(sku_id=sku, engine="t", decision=metrics.AUTO, chosen_id=cid, chosen_url=f"u/{sku}/{cid}",
                   needs_review=False, auto=True, pool=pool, kills=kills or {})


def _pre(sku, cid, pool, kills=None):
    return Outcome(sku_id=sku, engine="t", decision=metrics.PRESELECTED, chosen_id=cid, chosen_url=f"u/{sku}/{cid}",
                   needs_review=True, auto=False, pool=pool, kills=kills or {})


def _none(sku, decision, pool, kills=None):
    return Outcome(sku_id=sku, engine="t", decision=decision, pool=pool, kills=kills or {})


def _outcomes():
    return [
        _auto("s1", "c1", ["c1", "c2"]),                                             # auto, correct
        _auto("s2", "c2", ["c1", "c2"], {"c1": ["Image overexposed", "Image too blurry"]}),  # auto, wrong
        _auto("s3", "c1", ["c1"]),                                                   # auto, correct
        _pre("s4", "c1", ["c1", "c2"]),                                              # preselected, correct
        _pre("s5", "c2", ["c2"]),                                                    # preselected, wrong; correct never pooled
        _none("s6", metrics.UNSELECTED, ["c1"], {"c1": ["quality:short_side_below_250"]}),
        _none("s7", metrics.NOT_FOUND, ["c1", "c2"],
              {"c1": ["Image overexposed", "brand_mismatch"], "c2": ["competitor_substring"]}),  # false NOT_FOUND
        _none("s8", metrics.NOT_FOUND, ["c1"], {"c1": ["competitor_brand"]}),       # true NOT_FOUND
        _none("s9", metrics.UNSELECTED, ["c1"]),
        # the chosen candidate is never counted as killed, even if an earlier round rejected it
        _auto("s10", "c1", ["c1", "c2"], {"c1": ["quality:foreground"], "c2": ["download_failed"]}),
    ]


def test_metrics_on_handmade_outcomes():
    m = metrics.compute(_outcomes(), _labels())

    # 10 SKUs, 8 with a correct candidate (s8, s9 have none)
    assert (m["n_skus"], m["n_with_correct"]) == (10, 8)
    # auto picks: s1 ok, s2 wrong, s3 ok, s10 ok -> 3/4
    assert (m["n_auto"], m["n_auto_correct"], m["n_auto_wrong"]) == (4, 3, 1)
    assert m["auto_accept_precision"] == 0.75
    assert m["wrong_auto_rate"] == 0.1                  # 1 wrong auto pick / 10 SKUs
    # correct picks among SKUs with a correct candidate: s1, s3, s4, s10 -> 4/8
    assert m["correct_pick_rate"] == 0.5
    # preselected picks: s4 ok, s5 wrong -> 1/2
    assert m["preselect_precision"] == 0.5
    # human review: s4, s5 (pre-selected) and s6, s9 (unselected) -> 4/10
    assert m["review_rate"] == 0.4
    assert m["not_found_rate"] == 0.2                    # s7, s8
    assert m["false_not_found_rate"] == 0.125           # s7 had a correct candidate: 1/8
    assert m["pool_recall"] == 0.875                    # s5 never pooled its correct candidate: 7/8
    # Wilson 95% lower bound for 3/4, worked out by hand: (0.75 + 0.4802 - 0.64083) / 1.9604
    assert m["auto_precision_wilson_lower"] == pytest.approx(0.3006, abs=1e-4)

    assert m["kill_attribution"] == {
        "Image overexposed": 2,              # s2/c1, s7/c1
        "Image too blurry": 1,               # s2/c1
        "brand_mismatch": 1,                 # s7/c1
        "quality:short_side_below_250": 1,   # s6/c1
    }                                        # s10/c1 is the pick; wrong candidates never count
    assert m["quality_kills_on_correct"] == 4
    assert m["decisions"] == {"AUTO_PUBLISH": 4, "NOT_FOUND": 2, "REVIEW_PRESELECTED": 2, "REVIEW_UNSELECTED": 2}

    a, b, c = m["per_stratum"]["A"], m["per_stratum"]["B"], m["per_stratum"]["C"]
    assert (a["auto_accept_precision"], a["wrong_auto_rate"], a["correct_pick_rate"]) == (0.6667, 0.3333, 0.6667)
    assert (b["correct_pick_rate"], b["preselect_precision"], b["false_not_found_rate"]) == (0.25, 0.5, 0.25)
    assert (c["correct_pick_rate"], c["not_found_rate"], c["false_not_found_rate"]) == (1.0, 0.3333, 0.0)


@pytest.mark.parametrize("successes,n,expected", [
    (9, 10, 0.5958),     # standard table value
    (50, 50, 0.9286),    # all correct still has an uncertain lower end: 1 / (1 + 1.96^2/50)
    (2, 3, 0.2077),
    (0, 5, 0.0),
])
def test_wilson_lower_bound_known_values(successes, n, expected):
    assert metrics.wilson_lower_bound(successes, n) == pytest.approx(expected, abs=1e-4)


def test_wilson_lower_bound_without_trials_is_none():
    assert metrics.wilson_lower_bound(0, 0) is None


def test_rates_without_picks_are_none_not_zero():
    labels = _labels()
    outcomes = [_none(s, metrics.UNSELECTED, []) for s in labels]
    m = metrics.compute(outcomes, labels)
    assert m["auto_accept_precision"] is None and m["preselect_precision"] is None
    assert m["auto_precision_wilson_lower"] is None
    assert m["wrong_auto_rate"] == 0.0 and m["correct_pick_rate"] == 0.0 and m["review_rate"] == 1.0


def test_unmapped_pick_counts_as_wrong():
    labels = {"s1": {"stratum": "A", "no_correct_candidate": False, "named_case": None, "expected_v2": None,
                     "candidates": {"c1": "correct_exact"}, "download": {"c1": "ok"}}}
    stray = Outcome(sku_id="s1", engine="t", decision=metrics.AUTO, chosen_id=None, chosen_url="https://cache/x.jpg",
                    needs_review=False, auto=True)
    m = metrics.compute([stray], labels)
    assert (m["n_auto"], m["n_auto_correct"], m["wrong_auto_rate"]) == (1, 0, 1.0)


def test_quality_rule_classification():
    assert metrics.is_quality_rule("Image overexposed")
    assert metrics.is_quality_rule("quality:noise")
    assert not metrics.is_quality_rule("size_conflict")
    assert not metrics.is_quality_rule("fetch:http_403")


def test_diff_metrics_flags_changes_beyond_tolerance():
    base = {"correct_pick_rate": 0.40, "wrong_auto_rate": 0.50, "kill_attribution": {"Image overexposed": 3}}
    same = {"correct_pick_rate": 0.405, "wrong_auto_rate": 0.50, "kill_attribution": {"Image overexposed": 3}}
    moved = {"correct_pick_rate": 0.45, "wrong_auto_rate": 0.50, "kill_attribution": {"Image overexposed": 2}}
    assert metrics.diff_metrics(same, base) == []
    problems = metrics.diff_metrics(moved, base)
    assert any("correct_pick_rate" in p for p in problems)
    assert any("Image overexposed" in p for p in problems)
