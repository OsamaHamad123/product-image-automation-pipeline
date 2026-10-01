"""Binding merge gate for the v2 engine (decision D2).

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
    per SKU                             <= 4 provider queries in total, <= 2 verifier calls

The same bars are applied where the golden set alone cannot see a failure:

    vlm_noisy        confident model misreads of siblings (fixtures/vlm_noisy.json):
                     0 wrong auto picks, preselect precision >= 0.85
    bing_only        the no-key provider set (Bing HTML is the only search source):
                     nothing but a sanctioned lookup record is ever auto-published,
                     preselect precision >= 0.85
    correct absent   every golden SKU with its correct listings removed, with the
                     recorded and the noisy readings: 0 auto picks (all would be wrong)
    adversarial      fixtures/adversarial_skus.json: each case has its expected
                     decision class and there is no wrong auto pick. The cases fail
                     when a D4 identity rule (size, pack, variant, competitor), the D10
                     conflicting-match guard or a D8 plan query stops working
                     (test_eval_gate_mutations.py proves it).

The v2 imports are direct: a broken catalog_match import fails the gate instead of skipping it.
"""

import pytest

import catalog_match.pipeline  # noqa: F401  (a broken v2 import must fail the binding gate, not skip it)
import metrics

pytestmark = pytest.mark.eval

MAX_PROVIDER_QUERIES = 4
MAX_VERIFIER_CALLS = 2
PRESELECT_BAR = 0.85


def decision_class_ok(expected, outcome, lab):
    """Whether an outcome satisfies a named or adversarial case's expected decision class."""
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
    if expected == "no_auto":
        return outcome["decision"] != metrics.AUTO and not outcome["auto"] and outcome["decision"] != metrics.ERROR
    if expected == "no_wrong_pick":
        return (not picked or label == "correct_exact") and outcome["decision"] != metrics.ERROR
    raise AssertionError(f"unknown expected class {expected!r}")


def _budget_problems(report):
    """Total search-provider requests per SKU (every provider summed; the GTIN lookup is not a query)."""
    problems = []
    for o in report["outcomes"]:
        serp = {k: v for k, v in o["provider_calls"].items() if k != "off"}
        if sum(serp.values()) > MAX_PROVIDER_QUERIES:
            problems.append(f"{o['sku_id']}: provider requests {serp} > {MAX_PROVIDER_QUERIES} in total")
        if o["vlm_calls"] > MAX_VERIFIER_CALLS:
            problems.append(f"{o['sku_id']}: {o['vlm_calls']} verifier calls > {MAX_VERIFIER_CALLS}")
    return problems


def _run_health(name, run):
    report, _, blocked = run
    problems = []
    if blocked or report["network_attempts"]:
        problems.append(f"{name}: network attempts {blocked or report['network_attempts']}")
    if report["metrics"]["n_error"]:
        problems.append(f"{name}: engine errors: " + "; ".join(
            f"{o['sku_id']}: {o['error']}" for o in report["outcomes"] if o["error"]))
    return problems + [f"{name} {p}" for p in _budget_problems(report)]


def _label_of(labels, o):
    return labels[o["sku_id"]]["candidates"].get(o["chosen_id"]) if o["chosen_id"] else None


def _wrong_picks(report, labels, auto):
    return [f"{o['sku_id']}:{o['chosen_id']}({_label_of(labels, o)})" for o in report["outcomes"]
            if o["chosen_id"] and bool(o["auto"]) == auto and _label_of(labels, o) != "correct_exact"]


def golden_problems(v2_run, baseline, labels, golden):
    """The D2 bars on the golden set (normal scenario, production provider set)."""
    report = v2_run[0]
    m = report["metrics"]
    problems = _run_health("normal", v2_run)

    def check(ok, message):
        if not ok:
            problems.append(message)

    check(m["wrong_auto_rate"] == 0,
          f"wrong auto-publish rate {m['wrong_auto_rate']} must be 0: {_wrong_picks(report, labels, True)}")
    check(m["auto_accept_precision"] is None or m["auto_accept_precision"] >= 0.98,
          f"auto-accept precision {m['auto_accept_precision']} < 0.98")
    base_pick = baseline["metrics"]["correct_pick_rate"]
    check(m["correct_pick_rate"] is not None and m["correct_pick_rate"] >= 0.85,
          f"correct-pick rate {m['correct_pick_rate']} < 0.85")
    check(m["correct_pick_rate"] is not None and m["correct_pick_rate"] >= base_pick + 0.25,
          f"correct-pick rate {m['correct_pick_rate']} < v1 baseline {base_pick} + 0.25")
    check(m["n_preselected"] == 0 or m["preselect_precision"] >= PRESELECT_BAR,
          f"preselect precision {m['preselect_precision']} < {PRESELECT_BAR}; "
          f"wrong preselections: {_wrong_picks(report, labels, False)}")
    check(m["quality_kills_on_correct"] == 0,
          f"image-quality rules rejected correct images: "
          f"{ {k: v for k, v in m['kill_attribution'].items() if metrics.is_quality_rule(k)} }")
    check(m["false_not_found_rate"] is not None and m["false_not_found_rate"] <= 0.05,
          f"false NOT_FOUND rate {m['false_not_found_rate']} > 0.05")

    by_id = {o["sku_id"]: o for o in report["outcomes"]}
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
    return problems


def gemini_down_problems(down_run):
    dm = down_run[0]["metrics"]
    problems = _run_health("gemini_down", down_run)
    if dm["n_auto"]:
        problems.append(f"gemini_down: {dm['n_auto']} auto picks while the verifier is down")
    # With the verifier down, text evidence alone decides the pre-checked pick: it must meet the same bar.
    if dm["n_preselected"] and dm["preselect_precision"] < PRESELECT_BAR:
        problems.append(f"gemini_down: preselect precision {dm['preselect_precision']} < {PRESELECT_BAR}")
    return problems


def noisy_problems(noisy_run, labels):
    """Confident misreads of siblings (vlm_noisy.json): the text evidence has to keep the bars."""
    report = noisy_run[0]
    m = report["metrics"]
    problems = _run_health("vlm_noisy", noisy_run)
    if m["n_auto_wrong"]:
        problems.append(f"vlm_noisy: wrong auto picks {_wrong_picks(report, labels, True)}")
    if m["n_preselected"] and m["preselect_precision"] < PRESELECT_BAR:
        problems.append(f"vlm_noisy: preselect precision {m['preselect_precision']} < {PRESELECT_BAR}; "
                        f"wrong preselections: {_wrong_picks(report, labels, False)}")
    return problems


def bing_only_problems(bing_run, labels):
    """No sanctioned search key (D7): Bing results are review-only; the bars on what is pre-checked hold."""
    report = bing_run[0]
    m = report["metrics"]
    problems = _run_health("bing_only", bing_run)
    for o in report["outcomes"]:
        if o["auto"] and (labels[o["sku_id"]]["provider"].get(o["chosen_id"]) != "off"
                          or _label_of(labels, o) != "correct_exact"):
            problems.append(f"bing_only: {o['sku_id']} auto-published {o['chosen_id']} "
                            f"({_label_of(labels, o)}), which is not a correct sanctioned lookup record")
    if m["n_preselected"] and m["preselect_precision"] < PRESELECT_BAR:
        problems.append(f"bing_only: preselect precision {m['preselect_precision']} < {PRESELECT_BAR}; "
                        f"wrong preselections: {_wrong_picks(report, labels, False)}")
    return problems


def absent_problems(absent_runs):
    """With the correct listings removed every auto-publish is a wrong one."""
    problems = []
    for name, run in absent_runs.items():
        problems += _run_health(f"correct-absent/{name}", run)
        autos = [f"{o['sku_id']}:{o['chosen_id']}" for o in run[0]["outcomes"] if o["auto"]]
        if autos:
            problems.append(f"correct-absent/{name}: auto-published although no correct listing exists: {autos}")
    return problems


def adversarial_problems(adv_run, adv_golden):
    report = adv_run[0]
    labels = metrics.labels_from_golden(adv_golden)
    problems = _run_health("adversarial", adv_run)
    if report["metrics"]["n_auto_wrong"]:
        problems.append(f"adversarial: wrong auto picks {_wrong_picks(report, labels, True)}")
    by_id = {o["sku_id"]: o for o in report["outcomes"]}
    for sku in adv_golden["skus"]:
        o, lab = by_id[sku["id"]], labels[sku["id"]]
        if not decision_class_ok(sku["expected_v2"], o, lab):
            problems.append(f"adversarial {sku['id']} [{sku['adversarial_rule']}]: expected {sku['expected_v2']}, "
                            f"got {o['decision']} pick={o['chosen_id']} ({_label_of(labels, o)})")
    return problems


def test_v2_gate(v2_run, v2_gemini_down_run, v2_noisy_run, v2_bing_only_run, v2_adversarial_run, v2_absent_runs,
                 adversarial, baseline, labels, golden):
    problems = golden_problems(v2_run, baseline, labels, golden)
    problems += gemini_down_problems(v2_gemini_down_run)
    problems += noisy_problems(v2_noisy_run, labels)
    problems += bing_only_problems(v2_bing_only_run, labels)
    problems += absent_problems(v2_absent_runs)
    problems += adversarial_problems(v2_adversarial_run, adversarial[0])
    assert not problems, "v2 fails the D2 merge gate:\n  " + "\n  ".join(problems)


def test_budget_counts_every_search_provider():
    """Serper 4 + Bing 3 on one SKU is 7 requests: over the per-SKU budget even though no provider alone is."""
    report = {"outcomes": [{"sku_id": "x", "provider_calls": {"serper": 4, "bing_html": 3, "off": 1}, "vlm_calls": 1},
                           {"sku_id": "y", "provider_calls": {"serper": 3, "off": 1}, "vlm_calls": 2}]}
    assert _budget_problems(report) == ["x: provider requests {'serper': 4, 'bing_html': 3} > 4 in total"]


def test_new_expected_classes():
    lab = {"candidates": {"c1": "correct_exact", "c2": "wrong_size"}}
    auto = {"chosen_id": "c1", "chosen_url": "u", "decision": metrics.AUTO, "auto": True}
    wrong_pre = {"chosen_id": "c2", "chosen_url": "u", "decision": metrics.PRESELECTED, "auto": False}
    none = {"chosen_id": None, "chosen_url": None, "decision": metrics.UNSELECTED, "auto": False}
    assert not decision_class_ok("no_auto", auto, lab)
    assert decision_class_ok("no_auto", wrong_pre, lab) and decision_class_ok("no_auto", none, lab)
    assert decision_class_ok("no_wrong_pick", auto, lab) and decision_class_ok("no_wrong_pick", none, lab)
    assert not decision_class_ok("no_wrong_pick", wrong_pre, lab)
