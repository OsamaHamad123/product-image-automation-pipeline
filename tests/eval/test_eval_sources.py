"""The expansion round (X0-X5), the P0 page reads and the local index measured through injected fakes (sources.py).

- The default replay is untouched: without --expansion / --local-index the pipeline gets no expansion or page
  reader and no index provider, exactly as before (the scenarios' baselines stay comparable).
- The overlays point at real SKUs, carry a reading for every candidate they add and only use source providers.
- With the fakes the round really runs: X1 web pages, X2 shopping, X3/X4 visual search, the local index lookup and
  its page reads, each counted with its cost and the live time it adds; a row with no usable brand spends nothing.
"""

import contextlib
import importlib.util
import io
import json
from pathlib import Path

import pytest

import harness
import metrics
import runners
import sources

pytestmark = pytest.mark.eval

REPO = Path(__file__).resolve().parent.parent.parent
REALISTIC = harness.set_paths("realistic")


def _script(name):
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def golden_overlaid():
    return sources.apply_overlay(harness.load_golden(), harness.load_cassette(),
                                 sources.load_overlay(sources.GOLDEN_OVERLAY))


@pytest.fixture(scope="module")
def realistic_overlaid():
    golden = harness.load_golden(REALISTIC["golden"])
    return sources.apply_overlay(golden, harness.load_cassette(REALISTIC["cassette"]),
                                 sources.load_overlay(sources.overlay_path(REALISTIC["golden"])))


def _sku(doc, prefix):
    return next(s for s in doc["skus"] if s["id"].startswith(prefix))


def _run(sku, cassette, mappings=None, **flags):
    with runners.network_blocked() as attempts:
        out = runners.run_v2(sku, cassette, mappings=mappings, sources=flags or None)
    assert not runners.outbound_attempts(attempts)
    return out


# ---------------------------------------------------------------------------
# The default replay is untouched
# ---------------------------------------------------------------------------

def test_without_the_flags_the_pipeline_gets_no_round_page_reader_or_index(monkeypatch, golden, cassette):
    from catalog_match import pipeline

    seen = []
    real = pipeline.find_product_image

    def spy(spec, **kwargs):
        seen.append(kwargs)
        return real(spec, **kwargs)

    monkeypatch.setattr(pipeline, "find_product_image", spy)
    sku = _sku(golden, "uae-052")
    plain = _run(sku, cassette)
    off = _run(sku, cassette, expansion=False, index=False)
    assert {"expansion", "pages"}.isdisjoint(seen[0]) and {"expansion", "pages"}.isdisjoint(seen[1])
    assert [p.name for p in seen[0]["providers"]] == [p.name for p in seen[1]["providers"]]
    assert "local_index" not in [p.name for p in seen[0]["providers"]]
    assert (plain.decision, plain.chosen_id, plain.sources) == (off.decision, off.chosen_id, {})
    assert plain.decision == metrics.NOT_FOUND


def test_overlay_candidates_never_reach_the_normal_providers(golden_overlaid):
    golden, cassette = golden_overlaid
    sku = _sku(golden, "uae-006")
    out = _run(sku, cassette)
    assert not [cid for cid in out.pool if cid.startswith("x")]
    assert out.decision == metrics.UNSELECTED


def test_an_unknown_provider_outside_an_overlay_still_fails_loudly(golden, cassette):
    sku = json.loads(json.dumps(_sku(golden, "uae-001")))
    sku["candidates"][0]["provider"] = "serper_web"            # a source provider, but not from an overlay
    with pytest.raises(ValueError, match="unknown provider"):
        runners.run_v2(sku, cassette)


# ---------------------------------------------------------------------------
# Overlays
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("which", ["golden", "realistic"])
def test_the_overlays_point_at_real_skus_and_read_every_candidate_they_add(which):
    path = sources.GOLDEN_OVERLAY if which == "golden" else sources.overlay_path(REALISTIC["golden"])
    overlay = sources.load_overlay(path)
    base = harness.load_golden(None if which == "golden" else REALISTIC["golden"])
    by_id = {s["id"]: s for s in base["skus"]}
    assert overlay["skus"]
    for sku_id, extra in overlay["skus"].items():
        listings = {c["page_url"] for c in by_id[sku_id]["candidates"]}
        for c in extra["candidates"]:
            assert c["provider"] in sources.SOURCE_PROVIDERS and c["label"] in base["labels"]
            assert c["id"] in overlay["readings"][sku_id], (sku_id, c["id"])
            if c["provider"] == "page":
                assert c["page_url"] in listings, (sku_id, c["id"])   # a page of a listing the normal flow found
    providers = {c["provider"] for e in overlay["skus"].values() for c in e["candidates"]}
    if which == "golden":
        assert {"serper_web", "serper_shopping", "lens_serper", "local_index"} <= providers
    else:
        assert {"serper_web", "serper_shopping", "page", "local_index"} <= providers


def test_apply_overlay_copies_and_recounts_the_correct_candidates(golden, cassette):
    overlay = sources.load_overlay(sources.GOLDEN_OVERLAY)
    before = json.dumps(golden, sort_keys=True)
    merged, cas = sources.apply_overlay(golden, cassette, overlay)
    assert json.dumps(golden, sort_keys=True) == before
    kale = _sku(merged, "uae-053")
    assert kale["no_correct_candidate"] is False and _sku(golden, "uae-053")["no_correct_candidate"] is True
    assert "i1" in cas["verdicts"][kale["id"]] and "i1" not in cassette["verdicts"][kale["id"]]
    with pytest.raises(ValueError, match="unknown SKU"):
        sources.apply_overlay(golden, cassette, {"skus": {"nope": {"candidates": []}}})
    with pytest.raises(ValueError, match="not a source"):
        sources.apply_overlay(golden, cassette, {"skus": {kale["id"]: {"candidates": [
            {"id": "z", "provider": "serper", "label": "correct_exact"}]}}})


def test_the_fake_page_reader_serves_overlay_pages_and_counts_every_read(golden_overlaid):
    golden, _ = golden_overlaid
    pages = sources.FakePages(_sku(golden, "uae-006"))
    info = pages.fetch_page("https://gcc.luluhypermarket.com/en-ae/al-rawabi-fresh-milk-full-fat-2l/p/90021", "")
    assert info.ok and info.images[0].url.endswith("90021-al-rawabi-full-fat-2l.jpg")
    assert not pages.fetch_page_now("https://www.carrefouruae.com/some/other/page").ok
    assert not pages.fetch_page("https://example.ae/x").ok
    assert pages.by_kind == {"p0": 1, "index": 1, "expansion": 1} and len(pages.reads) == 3


# ---------------------------------------------------------------------------
# The sources at work
# ---------------------------------------------------------------------------

def test_x1_web_page_and_x2_shopping_are_paid_calls_with_their_cost(golden_overlaid):
    golden, cassette = golden_overlaid
    out = _run(_sku(golden, "uae-006"), cassette, expansion=True)
    assert (out.decision, out.chosen_id) == (metrics.PRESELECTED, "x1")     # page images are never auto-published
    src = out.sources
    assert src["paid_calls"].get("serper_web") and src["paid_calls"].get("serper_shopping")
    assert src["usd"] == pytest.approx(src["n_paid_calls"] * 0.001)
    assert src["page_reads_by"]["expansion"] >= 1
    assert src["est_live_s"] >= sources.LIVE_CALL_S["serper_web"] + sources.LIVE_CALL_S["serper_shopping"]
    assert "x1" in out.pool and not out.auto


def test_the_shopping_listing_alone_answers_a_row_with_no_results(golden_overlaid):
    golden, cassette = golden_overlaid
    out = _run(_sku(golden, "uae-052"), cassette, expansion=True)
    assert (out.decision, out.chosen_id) == (metrics.PRESELECTED, "x1")
    assert out.sources["paid_calls"].get("serper_shopping") == 1


def test_visual_search_finds_a_larger_copy_from_a_near_match(golden_overlaid):
    golden, cassette = golden_overlaid
    out = _run(_sku(golden, "uae-054"), cassette, expansion=True)
    assert out.chosen_id == "v1" and out.sources["paid_calls"].get("lens_serper", 0) >= 1


def test_the_local_index_is_a_free_lookup_that_reads_its_pages(golden_overlaid):
    golden, cassette = golden_overlaid
    out = _run(_sku(golden, "uae-053"), cassette, index=True)
    assert (out.decision, out.chosen_id) == (metrics.PRESELECTED, "i1")
    src = out.sources
    assert src["index_lookups"] >= 1 and src["page_reads_by"]["index"] == 1
    assert src["n_paid_calls"] == 0 and src["usd"] == 0.0


def test_the_index_finds_a_row_only_in_the_stores_spelling_after_brand_discovery(realistic_overlaid):
    golden, cassette = realistic_overlaid
    mappings = harness.load_mappings(REALISTIC["mappings"])
    out = _run(_sku(golden, "real-07"), cassette, mappings=mappings, index=True)
    assert "i1" in out.pool                                    # the 'super-tasty-...' slug, not 'sup t'
    assert out.sources["index_lookups"] >= 2                   # asked again once the spelling was known


def test_a_row_with_no_usable_brand_spends_no_paid_call(realistic_overlaid):
    golden, cassette = realistic_overlaid
    mappings = harness.load_mappings(REALISTIC["mappings"])
    out = _run(_sku(golden, "real-26"), cassette, mappings=mappings, expansion=True)
    assert out.sources["n_paid_calls"] == 0 and out.decision == metrics.UNSELECTED


def test_an_out_of_scope_row_spends_the_round_and_picks_nothing(realistic_overlaid):
    golden, cassette = realistic_overlaid
    mappings = harness.load_mappings(REALISTIC["mappings"])
    out = _run(_sku(golden, "real-28"), cassette, mappings=mappings, expansion=True)
    assert out.sources["n_paid_calls"] >= 2 and out.chosen_id is None


def test_p0_offers_the_pages_own_main_image(realistic_overlaid):
    golden, cassette = realistic_overlaid
    mappings = harness.load_mappings(REALISTIC["mappings"])
    out = _run(_sku(golden, "real-03"), cassette, mappings=mappings, expansion=True)
    assert "p1" in out.pool and out.sources["page_reads_by"]["p0"] >= 1
    # Carrefour's own page shows the same twin pack: not offered twice, never picked
    gp = _run(_sku(golden, "real-10"), cassette, mappings=mappings, expansion=True)
    assert gp.chosen_id == "c2" and "p1" not in gp.pool


def test_summarize_adds_up_the_rows():
    rows = [{"sources": {"paid_calls": {"serper_web": 2}, "n_paid_calls": 2, "usd": 0.002, "page_reads": 3,
                         "page_reads_by": {"p0": 2, "expansion": 1}, "index_lookups": 1, "est_live_s": 5.7,
                         "replay_ms": {"expansion": 12}}},
            {"sources": {"paid_calls": {}, "n_paid_calls": 0, "usd": 0.0, "page_reads": 0, "page_reads_by": {},
                         "index_lookups": 1, "est_live_s": 0.0, "replay_ms": {}}},
            {"sources": {}}]
    s = sources.summarize(rows)
    assert (s["skus"], s["n_paid_calls"], s["skus_with_paid_calls"], s["usd"], s["usd_per_100"]) == (2, 2, 1, 0.002,
                                                                                                    0.1)
    assert s["page_reads_by"] == {"p0": 2, "expansion": 1} and s["index_lookups"] == 2
    assert sources.summarize([{"sources": {}}]) == {}


def test_eval_report_prints_the_effect_cost_and_time(capsys, tmp_path):
    eval_report = _script("eval_report")
    code = eval_report.main(["--engine", "v2", "--expansion", "--local-index", "--sku",
                             "uae-006-al-rawabi-fresh-milk-full-fat-2l", "--sku", "uae-001-almarai-full-fat-milk-1l",
                             "--out", str(tmp_path / "r.json")])
    out = capsys.readouterr().out
    assert code == 0
    assert "effect   correct pick 1/2 -> 2/2 (+1)" in out
    assert "paid search calls" in out and "per 100 SKUs" in out and "more live" in out
    report = json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))
    assert report["sources"] == {"expansion": True, "index": True}
    assert report["sources_summary"]["n_paid_calls"] >= 2 and report["without_sources"]["metrics"]
