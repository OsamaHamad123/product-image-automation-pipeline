"""Legacy v1 replays offline and matches the recorded 'before' baseline.

baseline_v1.json stores what the unmodified v1 search did on the golden set
(aggregate metrics and the pick per SKU). While the legacy source files are
the ones the baseline was recorded from (same fingerprint), a live replay must
reproduce it. Once v1 is hot-fixed the fingerprint changes: the stored data
still documents the defects, and the live run must not publish more wrong
images automatically than the baseline did.
"""

import pytest

import harness
import metrics

pytestmark = pytest.mark.eval

CASE_1 = "uae-001-almarai-full-fat-milk-1l"


def _legacy_unchanged(baseline):
    return harness.legacy_fingerprint()["sha256"] == baseline["legacy_fingerprint"]["sha256"]


def test_legacy_runs_offline_and_matches_baseline(legacy_run, baseline, golden):
    report, seconds, blocked = legacy_run

    assert seconds < 120, f"legacy replay took {seconds:.1f}s"
    assert blocked == [] and report["network_attempts"] == [], "the replay tried to reach the network"
    assert report["n_skus"] == len(golden["skus"])
    assert report["metrics"]["n_error"] == 0, [o["error"] for o in report["outcomes"] if o["error"]]
    assert report["fixture_fingerprint"]["sha256"] == baseline["fixture_fingerprint"]["sha256"], (
        "golden fixtures changed after baseline_v1.json was recorded; regenerate it with "
        "'python scripts/eval_report.py --engine v1 --write-baseline' from a checkout of the baseline's legacy_commit")

    live, stored = report["metrics"], baseline["metrics"]
    if _legacy_unchanged(baseline):
        problems = metrics.diff_metrics(live, stored, tol=0.01)
        picks = {o["sku_id"]: o["chosen_id"] for o in report["outcomes"]}
        problems += [f"{sid}: v1 now picks {picks.get(sid)}, baseline {row['chosen_id']}"
                     for sid, row in baseline["per_sku"].items() if picks.get(sid) != row["chosen_id"]]
        assert not problems, "live v1 no longer matches baseline_v1.json:\n" + "\n".join(problems)
    else:
        # v1 was changed (the rollback hot-fixes): it may only get safer, never publish more wrong images.
        assert live["wrong_auto_rate"] <= stored["wrong_auto_rate"] + 0.01, (
            f"hot-fixed v1 auto-publishes more wrong images: {live['wrong_auto_rate']} > {stored['wrong_auto_rate']}")


def test_legacy_documents_known_defects(legacy_run, baseline, labels):
    # The recorded 'before' state: white-background packshots killed as overexposed, and the
    # bare-GTIN almarai.com hero banner that answers 403 returned as the answer for case (1).
    stored = baseline["metrics"]
    assert stored["kill_attribution"].get("Image overexposed", 0) > 0
    assert stored["quality_kills_on_correct"] >= stored["kill_attribution"]["Image overexposed"]
    case1 = baseline["per_sku"][CASE_1]
    assert case1["chosen_id"] == "c5" and case1["auto"] is True
    assert labels[CASE_1]["candidates"]["c5"] == "not_packshot" and labels[CASE_1]["download"]["c5"] == "403"
    assert case1["queries"] == ["6281007000000"], "the barcode round won before any text query ran"

    report, _, _ = legacy_run
    if not _legacy_unchanged(baseline):
        pytest.skip("legacy v1 has been modified since the baseline; the recorded defects above stay asserted")
    live = report["metrics"]
    assert live["kill_attribution"].get("Image overexposed", 0) > 0
    live_case1 = next(o for o in report["outcomes"] if o["sku_id"] == CASE_1)
    assert live_case1["chosen_id"] == "c5" and live_case1["auto"]
    assert "almarai.com" in live_case1["chosen_url"]
