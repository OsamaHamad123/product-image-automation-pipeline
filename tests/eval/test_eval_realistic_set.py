"""The realistic synthetic set (fixtures/realistic): modelled on the owner's live rows, replayed as its own set.

Integrity: 25-35 SKUs shaped like the live rows (a brand cell, no barcode, size, category or Arabic name, not
dairy), every live failure pattern present and drawn as it says (a listing naming the brand over another brand's
pack, a size not on the front, one picture on several domains, packshots on a non-white backdrop, generic and
out-of-scope rows), a reading for every candidate and misreads that point at real candidates.

Replay: offline, no network, no engine error and never a wrong auto-publish, with the recorded readings, the
recorded misreads (vlm_noisy) and the reader down. The committed golden set and its numbers are not touched.
"""

import importlib.util
import io
import json
from pathlib import Path

import pytest
from PIL import Image

import harness
import imagegen
import imagegen_extra
import metrics
import runners
from conftest import timed_run

pytestmark = pytest.mark.eval

REPO = Path(__file__).resolve().parent.parent.parent
REALISTIC = harness.set_paths("realistic")
PATTERNS = {"brand_page_wrong_pack", "size_not_on_front", "near_duplicate", "non_white_backdrop", "generic",
            "out_of_scope"}
DAIRY_WORDS = {"MILK", "LABAN", "YOGHURT", "YOGURT", "CHEESE", "LABNEH", "CREAM", "BUTTER"}


@pytest.fixture(scope="module")
def realistic():
    return harness.load_golden(REALISTIC["golden"])


@pytest.fixture(scope="module")
def realistic_cassette():
    return harness.load_cassette(REALISTIC["cassette"])


def _script(name):
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _image(sku, cand):
    return Image.open(io.BytesIO(runners.candidate_image(sku, cand))).convert("RGB")


# ---------------------------------------------------------------------------
# Integrity
# ---------------------------------------------------------------------------

def test_the_set_is_shaped_like_the_live_rows(realistic):
    skus = realistic["skus"]
    assert 25 <= len(skus) <= 35
    assert REALISTIC["cassette"] and REALISTIC["mappings"] and REALISTIC["noisy"]
    for sku in skus:
        assert sku["brand"] and sku["name_en"] == sku["name_en"].upper(), sku["id"]
        assert (sku["barcode"], sku["size"], sku["category"], sku["name_ar"], sku["brand_ar"]) == ("",) * 5, sku["id"]
        words = set(metrics.row_key(sku["name_en"]).split())
        assert not (words & DAIRY_WORDS) or "GHEE" in words or "ICE" in words, sku["id"]
        assert not sku["stratum"].startswith("dairy"), sku["id"]
    strata = {s["stratum"] for s in skus}
    assert {"frozen_fries", "canned_tuna", "spices", "tissues", "out_of_scope", "generic"} <= strata
    # mapped and unmapped brands, as in the live sheet
    mapped = {m["brand"].upper() for m in harness.load_mappings(REALISTIC["mappings"]).values()}
    assert 0 < sum(1 for s in skus if s["brand"] in mapped) < len(skus) // 2


def test_every_live_failure_pattern_is_in_the_set(realistic):
    seen = {p for s in realistic["skus"] for p in s["patterns"]}
    assert PATTERNS <= seen
    labels = set(realistic["labels"])
    for sku in realistic["skus"]:
        assert all(c["label"] in labels for c in sku["candidates"]), sku["id"]
        assert sku["no_correct_candidate"] == (not any(c["label"] == "correct_exact" for c in sku["candidates"]))
        assert sku["expected_v2"] in ("correct_pick", "no_wrong_pick", "no_pick", "no_auto"), sku["id"]


def test_a_brand_page_with_another_brands_pack_names_the_brand_and_reads_as_the_other(realistic, realistic_cassette):
    found = 0
    for sku in realistic["skus"]:
        if "brand_page_wrong_pack" not in sku["patterns"]:
            continue
        wrong = [c for c in sku["candidates"] if c["label"] == "wrong_brand"]
        assert wrong, sku["id"]
        for c in wrong:
            first_word = sku["brand"].split()[0].lower()
            assert first_word in c["title"].lower(), (sku["id"], c["title"])          # the listing names the brand
            printed = c["image_recipe"]["label_text"].split("|")[0]
            assert first_word not in printed.lower()                                   # the pack is another brand
            reading = realistic_cassette["verdicts"][sku["id"]][c["id"]]
            assert reading["brand_match"] == "no" and reading["brand_text"].upper() == printed
            found += 1
    assert found >= 4


def test_a_size_not_on_the_front_is_read_as_unsure(realistic, realistic_cassette):
    for sku in realistic["skus"]:
        if "size_not_on_front" not in sku["patterns"]:
            continue
        right = next(c for c in sku["candidates"] if c["label"] == "correct_exact")
        assert not any(ch.isdigit() for ch in right["image_recipe"]["label_text"]), sku["id"]
        reading = realistic_cassette["verdicts"][sku["id"]][right["id"]]
        assert (reading["size_text"], reading["size_match"]) == ("", "unsure")


def test_near_duplicates_are_one_picture_on_several_domains(realistic):
    from catalog_match.fetch import phash_distance
    import image_dedup_bktree

    pairs = 0
    for sku in realistic["skus"]:
        by_id = {c["id"]: c for c in sku["candidates"]}
        for c in sku["candidates"]:
            if not c.get("seed_of"):
                continue
            src = by_id[c["seed_of"]]
            assert c["domain"] != src["domain"] and c["label"] == src["label"], (sku["id"], c["id"])
            a, b = runners.candidate_image(sku, src), runners.candidate_image(sku, c)
            assert a != b                                                          # re-encoded: other bytes
            ha = format(image_dedup_bktree.calculate_phash(Image.open(io.BytesIO(a)).convert("RGB")), "016x")
            hb = format(image_dedup_bktree.calculate_phash(Image.open(io.BytesIO(b)).convert("RGB")), "016x")
            assert phash_distance(ha, hb) <= 6, (sku["id"], c["id"])               # ... of the same picture
            pairs += 1
    assert pairs >= 4


def test_backdrop_packshots_sit_on_a_uniform_non_white_colour(realistic):
    n = 0
    for sku in realistic["skus"]:
        for c in sku["candidates"]:
            recipe = c["image_recipe"]
            if recipe.get("kind") != "packshot_backdrop":
                continue
            im = _image(sku, c)
            w, h = im.size
            corners = [im.getpixel(p) for p in ((2, 2), (w - 3, 2), (2, h - 3), (w - 3, h - 3))]
            want = imagegen._rgb(recipe["background_color"], (0, 0, 0))
            for px in corners:
                assert max(abs(a - b) for a, b in zip(px, want)) <= 6, (sku["id"], c["id"], px, want)
            assert min(want) < 245                                                   # not white
            n += 1
    assert n >= 5


def test_the_extra_recipes_are_deterministic_and_leave_imagegen_alone():
    recipe = {"kind": "packshot_backdrop", "size": [400, 400], "shape": "can", "label_text": "A|B|1KG",
              "background_color": "#1f4e79"}
    assert imagegen_extra.generate(recipe, 7) == imagegen_extra.generate(recipe, 7)
    assert imagegen_extra.mime_type(recipe) == "image/jpeg"
    plain = {"kind": "packshot_white", "size": [300, 300], "label_text": "A|B"}
    assert imagegen_extra.generate(plain, 3) == imagegen.generate(plain, 3)
    assert "packshot_backdrop" not in imagegen.RECIPES


def test_every_candidate_has_a_reading_and_the_misreads_point_at_real_candidates(realistic, realistic_cassette):
    for sku in realistic["skus"]:
        readings = realistic_cassette["verdicts"][sku["id"]]
        assert set(readings) == {c["id"] for c in sku["candidates"]}, sku["id"]
    noisy = json.loads(REALISTIC["noisy"].read_text(encoding="utf-8"))
    by_id = {s["id"]: s for s in realistic["skus"]}
    for sku_id, per in noisy["misreads"].items():
        cands = {c["id"] for c in by_id[sku_id]["candidates"]}
        assert set(per) <= cands and set(noisy["notes"][sku_id]) == set(per)
        assert all("why" not in f for f in per.values())


def test_the_committed_golden_set_is_not_touched():
    stored = harness.load_baseline()["fixture_fingerprint"]["sha256"]
    assert harness.fixture_fingerprint()["sha256"] == stored


# ---------------------------------------------------------------------------
# Replay
# ---------------------------------------------------------------------------

def _realistic_run(scenario):
    golden = harness.load_golden(REALISTIC["golden"])
    return timed_run("v2", scenario, golden=golden, cassette=harness.load_cassette(REALISTIC["cassette"]),
                     mappings=harness.load_mappings(REALISTIC["mappings"]), set_name="realistic",
                     noisy_path=REALISTIC["noisy"])


@pytest.fixture(scope="module")
def realistic_runs():
    return {s: _realistic_run(s) for s in ("normal", "vlm_noisy", "gemini_down")}


def test_the_realistic_set_replays_offline_and_never_auto_publishes_a_wrong_image(realistic_runs):
    for scenario, (report, _seconds, blocked) in realistic_runs.items():
        assert report["set"] == "realistic" and report["n_skus"] >= 25
        assert not blocked and not report["network_attempts"], scenario
        assert not [o for o in report["outcomes"] if o["error"]], scenario
        m = report["metrics"]
        assert m["n_auto_wrong"] == 0, scenario
        assert m["per_lane"]["strict"]["n_auto_wrong"] == 0
    assert realistic_runs["gemini_down"][0]["metrics"]["n_auto"] == 0
    normal = realistic_runs["normal"][0]["metrics"]
    assert normal["n_auto"] >= 3 and normal["correct_pick_rate"] >= 0.8


def test_misreads_change_what_the_engine_reads(realistic_runs):
    normal, noisy = (realistic_runs[s][0]["outcomes"] for s in ("normal", "vlm_noisy"))
    assert [o["decision"] for o in normal] != [o["decision"] for o in noisy] or \
        [o["chosen_id"] for o in normal] != [o["chosen_id"] for o in noisy]


def test_eval_report_replays_a_named_set_with_its_own_files(monkeypatch):
    eval_report = _script("eval_report")
    seen = {}

    def fake_run_all(engine, scenario, **kwargs):
        seen.update(kwargs, scenario=scenario)
        raise SystemExit(0)

    monkeypatch.setattr(eval_report.harness, "run_all", fake_run_all)
    with pytest.raises(SystemExit):
        eval_report.main(["--engine", "v2", "--set", "realistic", "--scenario", "vlm_noisy"])
    assert seen["set_name"] == "realistic" and Path(seen["noisy_path"]) == REALISTIC["noisy"]
    assert seen["mappings"] == harness.load_mappings(REALISTIC["mappings"])
    assert {s["id"] for s in seen["golden"]["skus"]} == {s["id"] for s in harness.load_golden(REALISTIC["golden"])["skus"]}
    with pytest.raises(SystemExit) as exc:
        eval_report.main(["--engine", "v2", "--set", "no-such-set"])
    assert exc.value.code == 2
