"""Binding merge gate for the v2 engine (decision D2). Skips until catalog_match.pipeline exists.

v2 replays the golden set offline with auto-publish enabled for every brand,
so the gate measures what AUTO_PUBLISH would really publish. The bars are the
plan's and are not to be softened:

    wrong auto-publish                  == 0
    auto-accept precision               >= 0.98 (or no auto picks)
    correct pick                        >= 0.85 and >= v1 baseline + 0.25
    preselect precision                 >= 0.85
    quality-rule kills of correct_exact == 0
    false NOT_FOUND                     <= 0.05
    every named regression case has its expected decision class
    verifier down (gemini_down)         -> 0 auto picks, preselect precision >= 0.85
    per SKU                             <= 4 provider queries, <= 2 verifier calls
"""

import pytest

pytest.importorskip("catalog_match.pipeline")

import metrics  # noqa: E402

pytestmark = pytest.mark.eval

MAX_PROVIDER_QUERIES = 4
MAX_VERIFIER_CALLS = 2


def decision_class_ok(expected, outcome, lab):
    """Whether an outcome satisfies a named case's expected decision class."""
    label = lab["candidates"].get(outcome["chosen_id"]) if outcome["chosen_id"] else None
    picked = outcome["chosen_url"] is not None
    if expected == "correct_pick":
        return label == "correct_exact" and outcome["decision"] in (metrics.AUTO, metrics.PRESELECTED)
    if expected == "no_pick":
        return not picked and outcome["decision"] in (metrics.UNSELECTED, metrics.NOT_FOUND)
    if expected == "review_unselected":
        return not picked and outcome["decision"] == metrics.UNSELECTED
    if expected == "not_found":
        return not picked and outcome["decision"] == metrics.NOT_FOUND
    raise AssertionError(f"unknown expected class {expected!r}")


def _budget_problems(report):
    problems = []
    for o in report["outcomes"]:
        serp = {k: v for k, v in o["provider_calls"].items() if k != "off"}
        if serp and max(serp.values()) > MAX_PROVIDER_QUERIES:
            problems.append(f"{o['sku_id']}: provider queries {serp} > {MAX_PROVIDER_QUERIES}")
        if o["vlm_calls"] > MAX_VERIFIER_CALLS:
            problems.append(f"{o['sku_id']}: {o['vlm_calls']} verifier calls > {MAX_VERIFIER_CALLS}")
    return problems


def test_v2_gate(v2_run, v2_gemini_down_run, baseline, labels, golden):
    report, seconds, blocked = v2_run
    m = report["metrics"]
    outcomes = report["outcomes"]
    problems = []

    def check(ok, message):
        if not ok:
            problems.append(message)

    def picks(predicate):
        return [f"{o['sku_id']}:{o['chosen_id']}({labels[o['sku_id']]['candidates'].get(o['chosen_id'])})"
                for o in outcomes if predicate(o)]

    check(not blocked and not report["network_attempts"], f"network attempts: {blocked or report['network_attempts']}")
    check(m["n_error"] == 0, "engine errors: " + "; ".join(f"{o['sku_id']}: {o['error']}" for o in outcomes if o["error"]))

    wrong_auto = picks(lambda o: o["auto"] and labels[o["sku_id"]]["candidates"].get(o["chosen_id"]) != "correct_exact")
    check(m["wrong_auto_rate"] == 0, f"wrong auto-publish rate {m['wrong_auto_rate']} must be 0: {wrong_auto}")
    check(m["auto_accept_precision"] is None or m["auto_accept_precision"] >= 0.98,
          f"auto-accept precision {m['auto_accept_precision']} < 0.98")
    base_pick = baseline["metrics"]["correct_pick_rate"]
    check(m["correct_pick_rate"] is not None and m["correct_pick_rate"] >= 0.85,
          f"correct-pick rate {m['correct_pick_rate']} < 0.85")
    check(m["correct_pick_rate"] is not None and m["correct_pick_rate"] >= base_pick + 0.25,
          f"correct-pick rate {m['correct_pick_rate']} < v1 baseline {base_pick} + 0.25")
    wrong_pre = picks(lambda o: o["chosen_id"] and not o["auto"]
                      and labels[o["sku_id"]]["candidates"].get(o["chosen_id"]) != "correct_exact")
    check(m["n_preselected"] == 0 or m["preselect_precision"] >= 0.85,
          f"preselect precision {m['preselect_precision']} < 0.85; wrong preselections: {wrong_pre}")
    check(m["quality_kills_on_correct"] == 0,
          f"image-quality rules rejected correct images: "
          f"{ {k: v for k, v in m['kill_attribution'].items() if metrics.is_quality_rule(k)} }")
    check(m["false_not_found_rate"] is not None and m["false_not_found_rate"] <= 0.05,
          f"false NOT_FOUND rate {m['false_not_found_rate']} > 0.05")

    by_id = {o["sku_id"]: o for o in outcomes}
    for sku in golden["skus"]:
        o, lab = by_id[sku["id"]], labels[sku["id"]]
        if sku.get("named_case") is not None:
            check(decision_class_ok(lab["expected_v2"], o, lab),
                  f"named case ({sku['named_case']}) {sku['id']}: expected {lab['expected_v2']}, got {o['decision']} "
                  f"pick={o['chosen_id']} ({lab['candidates'].get(o['chosen_id'])})")
        if not sku["candidates"]:
            check(o["decision"] == metrics.NOT_FOUND, f"{sku['id']}: empty pool with healthy providers must be NOT_FOUND")
        if o["chosen_id"]:
            check(lab["download"].get(o["chosen_id"]) == "ok",
                  f"{sku['id']}: picked {o['chosen_id']}, which never downloaded")
        check(o["n_preselected"] <= 1, f"{sku['id']}: {o['n_preselected']} candidates pre-checked")
        if o["auto"]:
            # D7/D10: scraped (unsanctioned) results never auto-publish; D9: an unmapped brand never auto-publishes
            check(lab["provider"].get(o["chosen_id"]) != "bing_html",
                  f"{sku['id']}: auto-published an unsanctioned Bing result {o['chosen_id']}")
            check(lab["auto_publish_allowed"], f"{sku['id']}: auto-published although the brand is not in the mapping")

    problems += _budget_problems(report)

    down, _, down_blocked = v2_gemini_down_run
    dm = down["metrics"]
    check(dm["n_error"] == 0, "gemini_down engine errors: " + "; ".join(
        f"{o['sku_id']}: {o['error']}" for o in down["outcomes"] if o["error"]))
    check(dm["n_auto"] == 0, f"gemini_down: {dm['n_auto']} auto picks while the verifier is down")
    # With the verifier down, text evidence alone decides the pre-checked pick: it must meet the same bar.
    check(dm["n_preselected"] == 0 or dm["preselect_precision"] >= 0.85,
          f"gemini_down: preselect precision {dm['preselect_precision']} < 0.85")
    check(not down_blocked and not down["network_attempts"], "gemini_down run tried the network")
    problems += ["gemini_down " + p for p in _budget_problems(down)]

    assert not problems, "v2 fails the D2 merge gate:\n  " + "\n  ".join(problems)
