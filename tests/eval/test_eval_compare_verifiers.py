"""scripts/compare_verifiers.py and tests/eval/verifier_configs.py: the label reader A/B on recorded answers.

- The three configurations come from the settings; the real CascadeVerifier runs over readers that answer from the
  recorded readings of their own model, priced like live calls; an image a model never read is UNKNOWN and counted.
- --live reads only the unrecorded images, and only after the printed estimate got a yes; nothing is called
  otherwise. No test here reaches a real model: the live reader is a stand-in.
"""

import importlib.util
import json
from pathlib import Path

import pytest

import harness
import metrics
import runners
import verifier_configs as vc

pytestmark = pytest.mark.eval

REPO = Path(__file__).resolve().parent.parent.parent
REALISTIC = harness.set_paths("realistic")
STRONG = "gemini:gemini-3.5-flash"


def _script(name):
    spec = importlib.util.spec_from_file_location(f"{name}_under_test", REPO / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def realistic():
    return (harness.load_golden(REALISTIC["golden"]), harness.load_cassette(REALISTIC["cassette"]),
            harness.load_mappings(REALISTIC["mappings"]))


def _sku(golden, prefix):
    return next(s for s in golden["skus"] if s["id"].startswith(prefix))


def test_the_three_configurations_follow_the_settings():
    cur, strong, more = vc.configs()
    assert (cur.name, strong.name, more.name) == vc.CONFIG_NAMES
    assert cur.primary.startswith("gemini:") and cur.strong == STRONG and cur.strong_max_calls == 1
    assert (strong.primary, strong.strong, strong.strong_max_calls) == (STRONG, None, 0)
    assert (more.primary, more.strong, more.strong_max_calls, more.rejudge_max_calls) == (
        cur.primary, STRONG, 2, cur.rejudge_max_calls)
    with pytest.raises(ValueError, match="unknown verifier config"):
        vc.configs(["current", "best"])
    assert "no second look" in strong.describe() and "2 second look(s)" in more.describe()


def test_a_recorded_reader_answers_with_its_own_models_readings_and_counts_the_rest(realistic):
    from catalog_match.identity import build_sku_spec

    golden, cassette, mappings = realistic
    sku = _sku(golden, "real-04")
    spec = build_sku_spec(runners.sku_row(sku), mappings)
    fetched = runners.FixtureFetcher(__import__("catalog_match.models", fromlist=["x"]),
                                     sku).fetch([runners._to_candidate(
                                         __import__("catalog_match.models", fromlist=["x"]), c)
                                         for c in sku["candidates"]], spec)
    lite = vc.RecordedReader(vc.recorded_model(cassette), vc.readings_for(cassette, vc.recorded_model(cassette)), sku)
    strong = vc.RecordedReader(STRONG, vc.readings_for(cassette, STRONG), sku)
    a, b = lite.verify(spec, fetched), strong.verify(spec, fetched)
    # the 9 mm bag: the primary's reading is a MATCH, the strong model's (variant 'no') is not
    assert a.verdicts[0].decision == "MATCH" and b.verdicts[0].decision != "MATCH"
    assert a.usage[0]["images"] == 3 and a.usage[0]["model"] == "gemini-3.1-flash-lite"
    empty = vc.RecordedReader(STRONG, {}, sku)
    res = empty.verify(spec, fetched)
    assert [v.decision for v in res.verdicts] == ["UNKNOWN"] * 3 and empty.not_recorded == 3
    assert res.usage[0]["images"] == 3                    # priced as a live call would be, recorded or not
    down = vc.RecordedReader(STRONG, vc.readings_for(cassette, STRONG), sku, scenario="gemini_down").verify(spec, fetched)
    assert down.status == "unknown"


def test_live_fills_only_the_unrecorded_images_in_one_call(realistic):
    from catalog_match.identity import build_sku_spec
    from catalog_match.models import VerificationResult, VlmImageVerdict
    import catalog_match.models as models

    golden, cassette, mappings = realistic
    sku = _sku(golden, "real-04")
    spec = build_sku_spec(runners.sku_row(sku), mappings)
    fetched = runners.FixtureFetcher(models, sku).fetch([runners._to_candidate(models, c) for c in sku["candidates"]],
                                                        spec)
    asked = []

    class Live:
        def verify(self, spec, images):
            asked.append([f.candidate.image_url for f in images])
            return VerificationResult(status="ok", calls=1, verdicts=[VlmImageVerdict(index=0, decision="MATCH")],
                                      usage=[{"provider": "gemini", "model": "gemini-3.5-flash", "images": 1,
                                              "input_tokens": 2000, "output_tokens": 500, "estimated": False}])

    partial = {sku["id"]: {k: v for k, v in vc.readings_for(cassette, STRONG)[sku["id"]].items() if k != "c3"}}
    reader = vc.RecordedReader(STRONG, partial, sku, live=Live())
    res = reader.verify(spec, fetched)
    assert asked == [[sku["candidates"][2]["image_url"]]]          # only the image without an answer
    assert res.verdicts[2].decision == "MATCH" and res.verdicts[2].index == 2
    assert (reader.live_calls, reader.live_images, reader.not_recorded) == (1, 1, 0)
    assert [u.get("live", False) for u in res.usage] == [False, True] and res.usage[0]["images"] == 2


def test_the_real_cascade_takes_a_second_look_from_the_strong_models_readings(realistic):
    golden, cassette, mappings = realistic
    sku = _sku(golden, "real-12")                           # the size is not on the front: the primary is unsure
    cas = json.loads(json.dumps(cassette))
    cas["readings_by_model"][STRONG][sku["id"]]["c1"].update(size_text="160g", size_match="yes")
    cur = vc.configs(["current"])[0]
    with runners.network_blocked():
        out = runners.run_v2(sku, cas, mappings=mappings, verifier=vc.replay_verifier(cur, cas, sku))
        none = runners.run_v2(sku, cas, mappings=mappings,
                              verifier=vc.replay_verifier(vc.configs(["strong_primary"])[0], {"verdicts": {}}, sku))
    stats = out.verifier["stats"]
    assert stats["strong_calls"] == 1 and stats["not_recorded"] == 0
    assert any(u.get("role") == "strong" and u.get("usd", 0) > 0 for u in out.verifier["usage"])
    assert out.chosen_id == "c1"
    # no recorded answer at all: nothing is picked from an unread image
    assert none.chosen_id is None and none.verifier["stats"]["not_recorded"] >= 1


def test_the_estimates_are_upper_bounds_and_zero_when_everything_is_recorded(realistic):
    golden, cassette, _ = realistic
    cur, strong, _more = vc.configs()
    assert vc.missing_answers(cur, cassette, golden) == {"primary": 0, "strong": 0}
    assert vc.estimate_gap_usd(cur, cassette, golden) == 0.0
    no_strong = {k: v for k, v in cassette.items() if k != "readings_by_model"}
    gaps = vc.missing_answers(strong, no_strong, golden)
    assert gaps["primary"] > 0 and vc.estimate_gap_usd(strong, no_strong, golden) > 0
    assert 0 < vc.estimate_usd(cur, 10) < vc.estimate_usd(cur, 100)
    assert vc.estimate_usd(strong, 10) != vc.estimate_usd(cur, 10)


def test_compare_verifiers_prints_accuracy_lanes_cost_and_coverage(capsys, tmp_path):
    tool = _script("compare_verifiers")
    golden = harness.load_golden(REALISTIC["golden"])
    skus = [_sku(golden, p)["id"] for p in ("real-04", "real-12", "real-16")]
    args = ["--json", str(tmp_path / "ab.json")] + [a for s in skus for a in ("--sku", s)]
    assert tool.main(args) == 0
    out = capsys.readouterr().out
    for word in ("correct pick", "$/100", "answers recorded", "strict", "current", "strong_primary", "strong_max2"):
        assert word in out, word
    rows = {r["config"]: r for r in json.loads((tmp_path / "ab.json").read_text(encoding="utf-8"))["configs"]}
    assert rows["strong_primary"]["correct_pick"] > rows["current"]["correct_pick"]     # the 9 mm sibling
    assert rows["current"]["usd_per_100"] > 0 and rows["strong_primary"]["strong_calls"] == 0
    assert all(r["answers_recorded"] == 1.0 for r in rows.values())
    assert set(rows["current"]["per_lane"]) >= set(metrics.LANES)


def test_unrecorded_answers_are_flagged_not_hidden(capsys):
    tool = _script("compare_verifiers")
    golden = harness.load_golden()
    assert tool.main(["--set", "golden", "--configs", "strong_primary", "--sku", golden["skus"][0]["id"]]) == 0
    out = capsys.readouterr().out
    assert "0.0%" in out and "answers recorded < 100%" in out


def test_live_calls_need_the_estimate_and_a_yes(monkeypatch, capsys):
    tool = _script("compare_verifiers")
    from catalog_match.verifiers import cascade
    from catalog_match.models import VerificationResult, VlmImageVerdict

    def no_reader(*a, **k):
        raise AssertionError("a real reader was built")

    monkeypatch.setattr(cascade, "build_reader", no_reader)
    monkeypatch.setattr("builtins.input", lambda *a: "")
    golden = harness.load_golden()
    sku = golden["skus"][0]["id"]
    assert tool.main(["--set", "golden", "--configs", "strong_primary", "--sku", sku, "--live"]) == 1
    out = capsys.readouterr().out
    assert "nothing was called" in out and "at most $" in out and "without a recorded primary answer" in out

    class Fake:
        def __init__(self):
            self.calls = 0

        def verify(self, spec, images):
            self.calls += 1
            return VerificationResult(status="ok", calls=1, verdicts=[VlmImageVerdict(index=i, decision="UNSURE")
                                                                      for i in range(len(images))],
                                      usage=[{"provider": "gemini", "model": "gemini-3.5-flash",
                                              "images": len(images), "input_tokens": 100, "output_tokens": 10}])

    fakes = []
    monkeypatch.setattr(cascade, "build_reader", lambda ref, role="primary": fakes.append(Fake()) or fakes[-1])
    assert tool.main(["--set", "golden", "--configs", "strong_primary", "--sku", sku, "--live", "--yes"]) == 0
    out = capsys.readouterr().out
    assert sum(f.calls for f in fakes) >= 1 and "live calls" in out
