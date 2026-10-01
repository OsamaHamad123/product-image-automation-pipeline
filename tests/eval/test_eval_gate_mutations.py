"""The D2 gate goes red when a D4 identity rule, the D10 conflicting-match guard or a D8 query stops working.

Each test switches one rule off in catalog_match (a mutation), replays the adversarial
cases (fixtures/adversarial_skus.json) and runs the gate's own adversarial check on the
result. On the golden set alone these mutations passed the gate: every VLM error there is
shadowed by a better-ranked correct candidate, and every query surfaced the whole pool.
"""

from dataclasses import replace
from unittest import mock

import pytest

import catalog_match.decide as decide
import catalog_match.pipeline as pipeline
import catalog_match.retrieve as retrieve
import catalog_match.score as score
import catalog_match.variants as variants
from catalog_match.models import PlannedQuery
from conftest import timed_run
from test_eval_v2_gate import adversarial_problems

pytestmark = pytest.mark.eval


def _no_conflict(fn):
    def wrapped(*args, **kwargs):
        result = fn(*args, **kwargs)
        return "unknown" if result == "conflict" else result
    return wrapped


def _without_competitors(fn):
    def wrapped(spec, cand, negatives=None):
        return fn(replace(spec, competitors=()), cand, negatives)
    return wrapped


def _plan_without(*query_ids):
    real = retrieve.build_queries

    def build(spec, custom_query=None):
        return [q for q in real(spec, custom_query) if q.query_id not in query_ids]
    return build


MUTATIONS = {
    "size_conflict": lambda: [mock.patch.object(score, "compare", _no_conflict(score.compare))],
    "pack_conflict": lambda: [mock.patch.object(score, "compare_pack", _no_conflict(score.compare_pack))],
    "variant_conflict": lambda: [mock.patch.object(variants, "conflicts", lambda target, found: [])],
    "competitor_brand": lambda: [mock.patch.object(score, "score_candidate", _without_competitors(score.score_candidate)),
                                 mock.patch.object(pipeline, "score_candidate",
                                                   _without_competitors(pipeline.score_candidate))],
    "conflicting_match_guard": lambda: [mock.patch.object(decide, "identity_conflict", lambda *a, **k: None)],
    "query_plan:Q2": lambda: [mock.patch.object(retrieve, "build_queries", _plan_without("Q2"))],
    "query_plan:Q3": lambda: [mock.patch.object(retrieve, "build_queries", _plan_without("Q3"))],
    # the query text is ignored: every step sends the same unrelated words
    "query_text": lambda: [mock.patch.object(retrieve, "build_queries", lambda spec, custom_query=None: [
        PlannedQuery(query_id="Q1", text="zzz unrelated words", hl="en")])],
}


def test_unmutated_adversarial_cases_pass(v2_adversarial_run, adversarial):
    assert adversarial_problems(v2_adversarial_run, adversarial[0]) == []


def test_every_mutation_has_a_case(adversarial):
    rules = {sku["adversarial_rule"] for sku in adversarial[0]["skus"]}
    assert {m for m in MUTATIONS if m != "query_text"} <= rules


@pytest.mark.parametrize("mutation", sorted(MUTATIONS))
def test_gate_fails_when_a_rule_is_switched_off(mutation, adversarial):
    adv, cassette = adversarial
    patches = MUTATIONS[mutation]()
    for p in patches:
        p.start()
    try:
        run = timed_run("v2", golden=adv, cassette=cassette)
    finally:
        for p in reversed(patches):
            p.stop()
    problems = adversarial_problems(run, adv)
    assert problems, f"the gate still passes with {mutation} switched off"
    if mutation != "query_text":
        assert any(f"[{mutation}]" in p for p in problems), problems
