"""The offline replay measures what production does (review findings on the eval harness).

* fixture providers answer per query: an unrelated query returns nothing, and a candidate
  recorded for some queries is only served to those queries;
* the replay runs the provider sets production builds (Bing HTML is fallback-only next to
  Serper; the no-key set is Bing alone), counts every provider request against the budget,
  serves legacy CSE candidates and fails loudly on an unknown provider;
* a recorded golden set is replayed with its own brand mappings and cassette, and the
  recorder stores the query ids that returned each candidate;
* a broken v2 import fails instead of skipping the gate.
"""

import copy
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

import harness
import runners
from catalog_match import identity, models

pytestmark = pytest.mark.eval

REPO_ROOT = Path(__file__).resolve().parents[2]
CASE_1 = "uae-001-almarai-full-fat-milk-1l"
LABAN_UP = "uae-005-al-rawabi-laban-up-180ml"


def _script(name):
    spec = importlib.util.spec_from_file_location(f"_{name}", REPO_ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sku(golden, sku_id):
    return copy.deepcopy(next(s for s in golden["skus"] if s["id"] == sku_id))


def _spec(sku):
    return identity.build_sku_spec(runners.sku_row(sku), runners.load_mappings())


# ---------------------------------------------------------------------------
# Query-aware fixture providers
# ---------------------------------------------------------------------------

def test_unrelated_query_returns_nothing(golden):
    sku = _sku(golden, "uae-048-carrefour-full-cream-milk-1l")
    provider = runners.FixtureProvider(models, sku, "serper", True)
    assert provider.search("zzz totally unrelated query", "en", _spec(sku)).candidates == []
    assert provider.search("Carrefour Full Cream Milk 1L", "en", _spec(sku)).candidates


def test_candidates_tagged_with_query_ids_are_served_per_query(golden):
    from catalog_match.query_plan import build_queries

    sku = _sku(golden, LABAN_UP)
    spec = _spec(sku)
    plan = {q.query_id: q for q in build_queries(spec)}
    for c in sku["candidates"]:
        c["surfaced_by"] = ["Q2"] if c["id"] == "c1" else ["Q1"]
    provider = runners.FixtureProvider(models, sku, "serper", True)
    assert provider.query_id(plan["Q1"].text, spec) == "Q1" and provider.query_id("anything else", spec) == "custom"
    q1 = {runners.UrlIndex(sku).cid(c.image_url) for c in provider.search(plan["Q1"].text, "en", spec).candidates}
    q2 = {runners.UrlIndex(sku).cid(c.image_url) for c in provider.search(plan["Q2"].text, "ar", spec).candidates}
    assert "c1" not in q1 and q2 == {"c1"}
    # a staff custom query was never recorded: nothing tagged for the plan answers it
    assert provider.search("Al Rawabi Laban Up", "en", spec).candidates == []
    # the v1 replay (no query id) reads the ids as their query kind
    assert {c["id"] for c in runners.surfaced(sku, "serper", plan["Q1"].text)} == {
        c["id"] for c in sku["candidates"] if c["provider"] == "serper"}


def test_query_plan_is_measured(golden, cassette):
    """Drop Q2 from the plan and the Arabic-only listing is no longer found."""
    import catalog_match.retrieve as retrieve

    sku = _sku(golden, LABAN_UP)
    sku["candidates"] = [c for c in sku["candidates"] if c["id"] != "c2"]
    for c in sku["candidates"]:
        c["surfaced_by"] = ["Q2"] if c["id"] == "c1" else ["Q1", "Q2", "R1", "R2"]
    with runners.network_blocked():
        found = runners.run_v2(sku, cassette)
        real = retrieve.build_queries
        with mock.patch.object(retrieve, "build_queries",
                               lambda spec, custom=None: [q for q in real(spec, custom) if q.query_id != "Q2"]):
            missed = runners.run_v2(sku, cassette)
    assert found.chosen_id == "c1"
    assert missed.chosen_id is None and "c1" not in missed.pool


# ---------------------------------------------------------------------------
# Provider sets
# ---------------------------------------------------------------------------

def test_default_set_matches_production_with_a_serper_key(golden):
    providers = runners.build_providers(models, _sku(golden, CASE_1))
    shape = [(p.name, getattr(p, "kind", "search"), getattr(p, "fallback", False), p.sanctioned) for p in providers]
    assert shape == [("serper", "search", False, True), ("off", "lookup", False, True),
                     ("bing_html", "search", True, False)]


def test_bing_is_not_called_while_serper_answers(golden, cassette):
    with runners.network_blocked():
        outcome = runners.run_v2(_sku(golden, CASE_1), cassette)
    assert outcome.provider_calls["bing_html"] == 0 and outcome.provider_calls["serper"] >= 1


def test_bing_only_set_serves_every_listing_unsanctioned(golden, cassette):
    sku = _sku(golden, CASE_1)
    providers = runners.build_providers(models, sku, "bing_only")
    assert [(p.name, getattr(p, "fallback", False)) for p in providers] == [("off", False), ("bing_html", False)]
    result = providers[1].search("Almarai Full Fat Milk 1L", "en", _spec(sku))
    ids = {runners.UrlIndex(sku).cid(c.image_url) for c in result.candidates}
    assert {"c1", "c8"} <= ids
    assert all(c.provider == "bing_html" and not c.sanctioned and c.width is None for c in result.candidates)
    with runners.network_blocked():
        outcome = runners.run_v2(sku, cassette, provider_set="bing_only")
    assert not outcome.auto and set(outcome.provider_calls) == {"bing_html"}


def test_cse_legacy_candidates_are_replayed(golden, cassette):
    """Serper results relabelled as the legacy CSE adapter's still reach the pool (they used to vanish)."""
    sku = _sku(golden, CASE_1)
    for c in sku["candidates"]:
        if c["provider"] == "serper":
            c["provider"] = "cse_legacy"
    names = [p.name for p in runners.build_providers(models, sku)]
    assert names == ["serper", "off", "cse_legacy", "bing_html"]
    with runners.network_blocked():
        as_serper = runners.run_v2(_sku(golden, CASE_1), cassette)
        as_cse = runners.run_v2(sku, cassette)
    assert (as_cse.decision, as_cse.chosen_id) == (as_serper.decision, as_serper.chosen_id) == (
        "REVIEW_PRESELECTED", "c1"), (as_cse.decision, as_cse.chosen_id, as_cse.pool)
    assert sorted(as_cse.pool) == sorted(as_serper.pool) and as_cse.provider_calls["cse_legacy"] >= 1


def test_unknown_provider_fails_loudly(golden, cassette):
    sku = _sku(golden, CASE_1)
    sku["candidates"][0]["provider"] = "yandex"
    with pytest.raises(ValueError, match="unknown provider"):
        runners.run_v2(sku, cassette)


# ---------------------------------------------------------------------------
# Recorded sets (evaluation layer 3)
# ---------------------------------------------------------------------------

def test_recorded_set_replays_with_its_own_mappings_and_cassette(tmp_path, golden, cassette, monkeypatch):
    eval_report = _script("eval_report")
    recorded = {"version": 1, "named_cases": {}, "skus": [_sku(golden, CASE_1)]}
    (tmp_path / "golden_skus.json").write_text(json.dumps(recorded), encoding="utf-8")
    (tmp_path / "vlm_cassette.json").write_text(json.dumps(cassette), encoding="utf-8")
    mappings = {"almarai": {"canonical": "Almarai", "synonyms": ["Almarai"], "competitors": []}}
    (tmp_path / "brand_mappings.json").write_text(json.dumps({"mappings": mappings}), encoding="utf-8")

    golden_path = str(tmp_path / "golden_skus.json")
    assert eval_report.companion_paths(golden_path) == (str(tmp_path / "vlm_cassette.json"),
                                                        str(tmp_path / "brand_mappings.json"))
    assert eval_report.companion_paths(golden_path, "c.json", "m.json") == ("c.json", "m.json")
    assert eval_report.companion_paths(None) == (None, None)

    seen = {}

    def fake_run_all(engine, scenario, **kwargs):
        seen.update(kwargs)
        raise SystemExit(0)

    monkeypatch.setattr(eval_report.harness, "run_all", fake_run_all)
    with pytest.raises(SystemExit):
        eval_report.main(["--engine", "v2", "--golden", golden_path])
    assert seen["mappings"] == mappings, "a recorded set must not be scored with the committed fixture brand table"
    assert seen["provider_set"] == "serper"


def test_recorder_stores_the_query_ids_per_candidate(tmp_path, monkeypatch):
    eval_record = _script("eval_record")
    plan = [models.PlannedQuery(query_id="Q1", text="Brand Milk 1L", hl="en"),
            models.PlannedQuery(query_id="Q2", text="حليب براند", hl="ar")]
    relax = [models.PlannedQuery(query_id="R1", text="Brand Milk", hl="en", relaxed=True)]
    answers = {"Q1": ["https://a/1.jpg"], "Q2": ["https://a/1.jpg", "https://a/2.jpg"], "R1": ["https://a/3.jpg"]}

    def cand(url, provider="serper"):
        return models.Candidate(image_url=url, page_url="", page_title="", title="", snippet="", domain="a",
                                width=10, height=10, provider=provider, query_id="", rank=1, gtin_on_page=None,
                                sanctioned=True)

    class Search:
        name = "serper"

        def search(self, text, hl, spec):
            qid = next(q.query_id for q in plan + relax if q.text == text)
            return models.ProviderResult(provider="serper", status="ok", candidates=[cand(u) for u in answers[qid]])

    class Lookup:
        name = "off"

        def lookup(self, spec):
            return models.ProviderResult(provider="off", status="ok", candidates=[cand("https://off/9.jpg", "off")])

    stages = (
        SimpleNamespace(build_sku_spec=lambda row, mappings: SimpleNamespace(sku_key="k")),
        SimpleNamespace(build_queries=lambda spec: plan, relaxations=lambda spec: relax),
        SimpleNamespace(default_providers=lambda: [Search(), Lookup()]),
        SimpleNamespace(HttpFetcher=lambda: SimpleNamespace(fetch=lambda cands, spec: [])),
        SimpleNamespace(GeminiVerifier=lambda: SimpleNamespace(verify=lambda spec, batch: None)),
    )
    sku, _ = eval_record.record_row({"row_number": 7, "name": "Brand Milk 1L"}, {}, stages, tmp_path, [])
    tags = {c["image_url"]: c["surfaced_by"] for c in sku["candidates"]}
    assert tags == {"https://a/1.jpg": ["Q1", "Q2"], "https://a/2.jpg": ["Q2"], "https://a/3.jpg": ["R1"],
                    "https://off/9.jpg": ["gtin"]}


# ---------------------------------------------------------------------------
# A broken v2 import fails the gate
# ---------------------------------------------------------------------------

def test_broken_v2_import_raises_instead_of_skipping():
    with mock.patch.dict(sys.modules, {"catalog_match.pipeline": None}):
        with pytest.raises(ImportError):
            runners._v2_modules()
    for name in ("test_eval_v2_gate.py", "test_eval_caller_integration.py", "runners.py"):
        assert "importorskip" not in (Path(__file__).parent / name).read_text(encoding="utf-8"), name
    assert "Skipped" not in (REPO_ROOT / "scripts" / "eval_report.py").read_text(encoding="utf-8")


def test_scenarios_and_provider_sets_are_known():
    assert {"normal", "gemini_down", "vlm_noisy"} <= set(harness.SCENARIOS)
    assert set(harness.PROVIDER_SETS) == {"serper", "bing_only"}
    with pytest.raises(ValueError):
        harness.run_all("v2", provider_set="yandex")


def test_noisy_and_adversarial_fixtures_point_at_real_candidates(golden, cassette):
    doc = json.loads(harness.VLM_NOISY_PATH.read_text(encoding="utf-8"))
    by_id = {s["id"]: s for s in golden["skus"]}
    for sku_id, per_cand in {**doc["misreads"], **{k: dict.fromkeys(v) for k, v in doc["not_covered"].items()}}.items():
        labels = {c["id"]: c["label"] for c in by_id[sku_id]["candidates"]}
        for cid in per_cand:
            assert labels[cid] in ("wrong_size", "wrong_pack", "wrong_variant"), (sku_id, cid)
    noisy = harness.noisy_cassette(cassette)
    assert noisy["verdicts"]["uae-027-nido-fortified-2-25kg"]["c4"]["size_text"] == "2.25kg"
    assert cassette["verdicts"]["uae-027-nido-fortified-2-25kg"]["c4"]["size_text"] == "900g", "the overlay copies"
    adv, adv_cassette = harness.load_adversarial()
    assert len(adv["skus"]) >= 10 and all(s["id"] in adv_cassette["verdicts"] for s in adv["skus"])
