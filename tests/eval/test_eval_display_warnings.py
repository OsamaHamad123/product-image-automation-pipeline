"""The review screen's per-candidate warnings are display only: the D2 gate sees exactly the same engine.

decide.candidate_warnings() is computed for every reviewable candidate after routing (facade, the search
response, the saved curation rows). This replays the golden set with every decide.route() call (the pipeline's
and the expansion rounds') followed by candidate_warnings() on every candidate, the way the facade calls it, and
checks that nothing it reads was written and that every SKU's decision, pick and auto-publish flag is the plain
run's (the v2_run fixture of test_eval_v2_gate).
"""

import pytest

from catalog_match import decide

from conftest import timed_run

pytestmark = pytest.mark.eval


def _per_sku(report):
    return {o["sku_id"]: (o["decision"], o["chosen_id"], bool(o["auto"]), o["n_preselected"])
            for o in report["outcomes"]}


def test_candidate_warnings_change_nothing_the_gate_measures(v2_run, monkeypatch):
    real_route = decide.route
    mutated, codes = [], {}
    seen = {"candidates": 0}

    def route_then_display(spec, ranked, *args, **kwargs):
        outcome = real_route(spec, ranked, *args, **kwargs)
        pool = list(outcome.ranked or [])
        replaced = next((rc for rc in pool if f"{decide.RESOLUTION_PREFIX}:replaced" in rc.reasons), None)
        before = [(rc.status, list(rc.reasons)) for rc in pool]
        decision, winner = outcome.decision, outcome.winner
        for rc in pool:
            reading_of = replaced if (replaced is not None and rc.status == "preselected") else None
            for code in decide.candidate_warnings(spec, rc, reading_of):
                codes[code.split(":", 1)[0]] = codes.get(code.split(":", 1)[0], 0) + 1
            seen["candidates"] += 1
        after = [(rc.status, list(rc.reasons)) for rc in pool]
        if before != after or outcome.decision != decision or outcome.winner is not winner:
            mutated.append(spec.sku_key)
        return outcome

    monkeypatch.setattr(decide, "route", route_then_display)
    report, _, blocked = timed_run("v2")
    assert not blocked and not report["network_attempts"]
    assert mutated == []
    assert seen["candidates"] > 0 and codes, "the replay computed no display warning at all"
    plain = v2_run[0]
    assert _per_sku(report) == _per_sku(plain)
    for key in ("correct_pick_rate", "wrong_auto_rate", "n_auto", "n_preselected", "preselect_precision"):
        assert report["metrics"][key] == plain["metrics"][key], key
