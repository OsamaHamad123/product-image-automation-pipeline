"""Stage timings of one search (WP speed, change 1): measured in pipeline.find_product_image, stored with the
outcome, exported per row and summarised by scripts/export_run.py.

The clock is a fake one (pipeline._now) that only the stage doubles advance, so the numbers are exact.
Sockets are blocked for every test.
"""

import socket
import sys
from pathlib import Path

import pytest

from catalog_match import facade, pipeline, settings
from catalog_match.models import SearchOutcome
from test_cm_pipeline import READ_MATCH, SPEC, StubFetcher, StubProvider, StubVerifier, cand, packshot_png

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

PACK = cand(2, "Buy Almarai Full Fat Fresh Milk 1L Online - Carrefour UAE",
            "https://www.carrefouruae.com/mafuae/en/almarai-full-fat-fresh-milk-1l/p/108596")


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError(f"network access attempted: {args!r}")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    monkeypatch.setattr(settings, "_config", None)
    monkeypatch.setenv("AUTO_PUBLISH_ENABLED", "false")
    monkeypatch.setenv("AUTO_PUBLISH_BRANDS", "")


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


class SlowProvider(StubProvider):
    def __init__(self, clock, seconds, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.clock, self.seconds = clock, seconds

    def search(self, query, hl, spec):
        self.clock.advance(self.seconds)
        return super().search(query, hl, spec)


class SlowFetcher(StubFetcher):
    def __init__(self, clock, seconds, bodies):
        super().__init__(bodies)
        self.clock, self.seconds = clock, seconds

    def fetch(self, cands, spec):
        self.clock.advance(self.seconds)
        return super().fetch(cands, spec)


class SlowVerifier(StubVerifier):
    def __init__(self, clock, seconds, readings):
        super().__init__(readings)
        self.clock, self.seconds = clock, seconds

    def verify(self, spec, images):
        self.clock.advance(self.seconds)
        return super().verify(spec, images)


def run(monkeypatch, clock, **kwargs):
    monkeypatch.setattr(pipeline, "_now", clock)
    provider = SlowProvider(clock, 2.0, "serper", [PACK])
    fetcher = SlowFetcher(clock, 3.0, {PACK.image_url: packshot_png(1)})
    verifier = SlowVerifier(clock, 4.0, {PACK.image_url: READ_MATCH})
    outcome = pipeline.find_product_image(SPEC, providers=[provider], fetcher=fetcher, verifier=verifier, **kwargs)
    return outcome, provider


def test_every_stage_is_timed_and_the_stages_add_up_to_the_total(monkeypatch):
    outcome, provider = run(monkeypatch, Clock())
    t = outcome.timings
    assert set(t) == {"retrieval", "fetch", "quality", "verify", "total"}      # no expansion round ran
    assert t["fetch"] == 3000 and t["quality"] == 0 and t["verify"] == 4000
    assert len(provider.calls) >= 1 and t["retrieval"] == 2000 * len(provider.calls)
    assert t["total"] == t["retrieval"] + t["fetch"] + t["quality"] + t["verify"]
    assert all(isinstance(v, int) for v in t.values())


def test_a_second_verifier_call_adds_to_the_same_stage(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(pipeline, "_now", clock)
    other = cand(3, "Buy Almarai Full Fat Fresh Milk 1L Online - Noon UAE",
                 "https://www.noon.com/uae-en/almarai-full-fat-fresh-milk-1l/p/")
    pics = {PACK.image_url: packshot_png(1), other.image_url: packshot_png(2)}
    reads = {PACK.image_url: dict(READ_MATCH, view="lifestyle"), other.image_url: dict(READ_MATCH, view="lifestyle")}
    outcome = pipeline.find_product_image(
        SPEC, providers=[SlowProvider(clock, 1.0, "serper", [PACK, other])], fetcher=SlowFetcher(clock, 0.5, pics),
        verifier=SlowVerifier(clock, 4.0, reads))
    assert outcome.vlm_calls >= 1
    assert outcome.timings["verify"] == 4000 * outcome.vlm_calls


def test_the_outcome_summary_carries_the_timings_and_an_old_outcome_has_none(monkeypatch):
    outcome, _ = run(monkeypatch, Clock())
    summary = facade.outcome_summary(outcome)
    assert summary["timings"] == outcome.timings and summary["timings"]["fetch"] == 3000
    assert facade.outcome_summary(SearchOutcome(decision="NOT_FOUND"))["timings"] == {}


def test_a_search_with_nothing_to_download_still_has_a_total(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(pipeline, "_now", clock)
    outcome = pipeline.find_product_image(
        SPEC, providers=[SlowProvider(clock, 1.5, "serper", [])], fetcher=SlowFetcher(clock, 3.0, {}),
        verifier=SlowVerifier(clock, 4.0, {}))
    assert outcome.timings["fetch"] == 0 and outcome.timings["verify"] == 0
    assert outcome.timings["total"] == outcome.timings["retrieval"] > 0


# ---------------------------------------------------------------------------
# scripts/export_run.py: per-row timings and the summary block
# ---------------------------------------------------------------------------

def test_row_timings_reads_only_whole_milliseconds_and_an_old_trace_has_none():
    import export_run

    stored = {"timings": {"fetch": 3000, "verify": "4000", "x": "bad", "neg": -5, "none": None}}
    assert export_run.row_timings(stored) == {"fetch": 3000, "verify": 4000}
    for missing in ({}, {"timings": None}, {"timings": []}, None, "text"):
        assert export_run.row_timings(missing) == {}


def test_the_summary_gives_p50_p90_and_total_seconds_per_stage_and_skips_rows_without_timings():
    import export_run

    rows = [{"timings": {"retrieval": 1000 * (i + 1), "fetch": 100, "total": 1500 * (i + 1)}} for i in range(10)]
    rows.append({"timings": {"retrieval": 1000, "expansion": 7000, "total": 9000}})
    rows += [{"timings": {}}, {"name": "an old export row"}, {"error": "x"}]
    s = export_run.timings_summary(rows)
    assert s["rows"] == 11
    assert list(s["stages"]) == ["retrieval", "fetch", "expansion", "total"]            # the stage order, not the dict's
    assert s["stages"]["retrieval"] == {"rows": 11, "p50_s": 5.0, "p90_s": 9.0, "total_s": 56.0}
    assert s["stages"]["expansion"] == {"rows": 1, "p50_s": 7.0, "p90_s": 7.0, "total_s": 7.0}
    assert s["stages"]["fetch"]["total_s"] == 1.0 and s["stages"]["total"]["p90_s"] == 13.5


def test_a_summary_of_rows_without_timings_is_empty():
    import export_run

    assert export_run.timings_summary([]) == {"rows": 0, "stages": {}}
    assert export_run.timings_summary([{"row": 1}, {"row": 2, "timings": {}}]) == {"rows": 0, "stages": {}}
