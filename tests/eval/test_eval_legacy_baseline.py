"""Legacy v1 replays offline and matches its recorded baseline.

Two v1 records exist:

* baseline_v1.json is the ORIGINAL v1, before any fix. It is never regenerated: it is
  the 'before' state the v2 merge gate compares against (correct pick >= v1 + 0.25)
  and the evidence of the known defects.
* baseline_v1_hotfixed.json is v1 after the rollback hot-fixes (WP-4a). While the
  legacy source files keep the fingerprint it was recorded from, a live replay must
  reproduce it exactly. When they change again, the live run may only get safer.
"""

import pytest

import harness
import metrics

pytestmark = pytest.mark.eval

CASE_1 = "uae-001-almarai-full-fat-milk-1l"


@pytest.fixture(scope="module")
def hotfixed():
    return harness.load_hotfixed_baseline()


def _live_fingerprint():
    return harness.legacy_fingerprint()["sha256"]


def test_legacy_runs_offline_and_matches_hotfixed_baseline(legacy_run, baseline, hotfixed, golden):
    report, seconds, blocked = legacy_run

    assert seconds < 120, f"legacy replay took {seconds:.1f}s"
    assert blocked == [] and report["network_attempts"] == [], "the replay tried to reach the network"
    assert report["n_skus"] == len(golden["skus"])
    assert report["metrics"]["n_error"] == 0, [o["error"] for o in report["outcomes"] if o["error"]]
    for name, stored in (("baseline_v1.json", baseline), ("baseline_v1_hotfixed.json", hotfixed)):
        assert report["fixture_fingerprint"]["sha256"] == stored["fixture_fingerprint"]["sha256"], (
            f"golden fixtures changed after {name} was recorded")

    live = report["metrics"]
    if _live_fingerprint() == hotfixed["legacy_fingerprint"]["sha256"]:
        problems = metrics.diff_metrics(live, hotfixed["metrics"], tol=0.01)
        picks = {o["sku_id"]: o["chosen_id"] for o in report["outcomes"]}
        problems += [f"{sid}: v1 now picks {picks.get(sid)}, hot-fixed baseline {row['chosen_id']}"
                     for sid, row in hotfixed["per_sku"].items() if picks.get(sid) != row["chosen_id"]]
        assert not problems, "live v1 no longer matches baseline_v1_hotfixed.json:\n" + "\n".join(problems)
    else:
        # v1 changed again after the hot-fix record: it may only get safer, never publish more wrong images.
        assert live["wrong_auto_rate"] <= hotfixed["metrics"]["wrong_auto_rate"] + 0.01, (
            f"v1 auto-publishes more wrong images than the hot-fixed record: {live['wrong_auto_rate']}")
    assert live["wrong_auto_rate"] <= baseline["metrics"]["wrong_auto_rate"] + 0.01


def test_hotfixed_v1_is_safer_than_original(baseline, hotfixed, labels):
    # The hot-fixes removed the hard exposure/blur gates and the unverified last-resort pick.
    orig, fixed = baseline["metrics"], hotfixed["metrics"]
    assert fixed["quality_kills_on_correct"] == 0 < orig["quality_kills_on_correct"]
    assert fixed["wrong_auto_rate"] < orig["wrong_auto_rate"]
    assert fixed["correct_pick_rate"] > orig["correct_pick_rate"]
    # No recorded hot-fixed pick is an image whose download failed.
    for sid, row in hotfixed["per_sku"].items():
        if row["chosen_id"]:
            assert labels[sid]["download"][row["chosen_id"]] == "ok", (sid, row["chosen_id"])


def test_legacy_documents_known_defects(legacy_run, baseline, labels):
    # The recorded 'before' state (original v1), asserted on its stored per-SKU data:
    # white-background packshots killed as overexposed, and the bare-GTIN almarai.com hero
    # banner that answers 403 returned as the answer for case (1).
    stored = baseline["metrics"]
    assert stored["kill_attribution"].get("Image overexposed", 0) > 0
    assert stored["quality_kills_on_correct"] >= stored["kill_attribution"]["Image overexposed"]
    case1 = baseline["per_sku"][CASE_1]
    assert case1["chosen_id"] == "c5" and case1["auto"] is True
    assert labels[CASE_1]["candidates"]["c5"] == "not_packshot" and labels[CASE_1]["download"]["c5"] == "403"
    assert case1["queries"] == ["6281007000000"], "the barcode round won before any text query ran"

    overexposed_correct = [
        sid for sid, row in baseline["per_sku"].items()
        for cid, rules in row["kills"].items()
        if "Image overexposed" in rules and labels[sid]["candidates"].get(cid) == "correct_exact"
    ]
    assert overexposed_correct, "the original record must show correct packshots killed as overexposed"
    undownloaded_auto = [sid for sid, row in baseline["per_sku"].items()
                         if row["auto"] and row["chosen_id"] and labels[sid]["download"][row["chosen_id"]] != "ok"]
    assert CASE_1 in undownloaded_auto, "the original v1 auto-published an image that never downloaded"

    # The live (hot-fixed) v1 no longer shows these defects.
    report, _, _ = legacy_run
    live = report["metrics"]
    assert live["kill_attribution"].get("Image overexposed", 0) == 0
    live_case1 = next(o for o in report["outcomes"] if o["sku_id"] == CASE_1)
    assert live_case1["chosen_id"] != "c5"
    for o in report["outcomes"]:
        if o["chosen_id"]:
            assert labels[o["sku_id"]]["download"][o["chosen_id"]] == "ok", (o["sku_id"], o["chosen_id"])
