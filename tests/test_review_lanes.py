"""Lanes of the engine's pick (shadow mode for auto-publish across brands).

- catalog_match.decide.pick_lane / lane_of: 'strict' when the only auto blockers are the settings and brand_conf_*,
  'unsure' for a tier-1 UNSURE pick, 'other' for the rest; route() writes 'lane:<name>' on every pick.
- AUTO_PUBLISH_STRICT_LANE: a strict pick of a mapped brand auto-publishes without AUTO_PUBLISH_BRANDS, only with
  auto-publish on; every other blocker, and an unmapped brand, still holds it back. Off by default.
- local_cache_db.review_stats: per-lane reviewed pre-checks, accepted / replaced / rejected, precision, Wilson lower
  bound and readiness on the brand thresholds; scripts/review_stats.py prints them.

Offline: no database, no network (the bridge writes are in test_review_decisions.py, the settings page in
test_laqta_health.py).
"""

import dataclasses
import importlib.util
import sys
from pathlib import Path

import pytest

from catalog_match import decide, settings
from catalog_match.identity import build_sku_spec
from catalog_match.models import (Candidate, FetchedImage, ProviderHealth, QualityReport, RankedCandidate,
                                  VerificationResult, VlmImageVerdict)
from catalog_match.score import score_candidate

ROOT = Path(__file__).resolve().parents[1]
MAPPINGS = {"almarai": {"brand": "Almarai", "synonyms": ["المراعي"], "excluded_competitors": ["Al Ain"]}}
ROW = {"name": "Almarai Full Fat Milk 1L", "brand": "Almarai", "category": "Dairy"}
SPEC = build_sku_spec(ROW, MAPPINGS)
HEALTHY = [ProviderHealth("serper", "ok", 200)]
OK = VerificationResult(status="ok", calls=1)

pytestmark = pytest.mark.usefixtures("offline")


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    monkeypatch.setattr(settings, "_config", None)
    for name in ("AUTO_PUBLISH_ENABLED", "AUTO_PUBLISH_BRANDS", "AUTO_PUBLISH_STRICT_LANE"):
        monkeypatch.delenv(name, raising=False)


def _set(monkeypatch, enabled=False, brands="", lane=False):
    monkeypatch.setenv("AUTO_PUBLISH_ENABLED", "true" if enabled else "false")
    monkeypatch.setenv("AUTO_PUBLISH_BRANDS", brands)
    monkeypatch.setenv("AUTO_PUBLISH_STRICT_LANE", "true" if lane else "false")


def _cand(n=1, sanctioned=True, query_id="Q1", tier1=True):
    if tier1:
        return Candidate(image_url=f"https://cdn.carrefouruae.com/{n}.jpg",
                         page_url=f"https://www.carrefouruae.com/mafuae/en/almarai-full-fat-milk-1l/p/{n}",
                         title="Almarai Full Fat Milk 1L", domain="carrefouruae.com", provider="serper",
                         sanctioned=sanctioned, query_id=query_id, rank=n)
    return Candidate(image_url=f"https://blog.example.com/{n}.jpg", page_url=f"https://blog.example.com/milk-{n}",
                     title="Almarai milk", provider="serper", query_id=query_id, rank=n)


def _rc(cand, decision="MATCH", spec=SPEC, size_text="1 L"):
    v = VlmImageVerdict(index=0, brand_text="Almarai", variant_text="Full Fat", size_text=size_text,
                        view="front_packshot" if decision == "MATCH" else "lifestyle",
                        brand_match="yes", decision=decision)
    fi = FetchedImage(candidate=cand, ok=True, content_sha256="ab" * 32, width=800, height=800, path_or_bytes=b"x")
    return RankedCandidate(candidate=cand, score=score_candidate(spec, cand), fetched=fi,
                           quality=QualityReport(hard_ok=True, quality_score=0.8), verdict=v)


def _lanes(rc):
    return [r for r in rc.reasons if r.startswith("lane:")]


# ---------------------------------------------------------------------------
# The lane from the blocker lists
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("why, blockers, lane", [
    ("vlm_match", [], "strict"),                                          # auto-published: every rule passed
    ("vlm_match", ["auto_publish_disabled"], "strict"),
    ("vlm_match", ["auto_publish_off_for_brand"], "strict"),
    ("vlm_match", ["auto_publish_disabled", "brand_conf_sheet_raw"], "strict"),
    ("vlm_match", ["auto_publish_off_for_brand", "brand_conf_learned"], "strict"),
    ("vlm_match", ["auto_publish_disabled", "cache_hit"], "other"),
    ("vlm_match", ["auto_publish_disabled", "not_tier1"], "other"),
    ("vlm_match", ["auto_publish_disabled", "unsanctioned_source"], "other"),
    ("vlm_match", ["auto_publish_disabled", "reviewed_source"], "other"),
    ("vlm_match", ["auto_publish_disabled", "relaxed_query"], "other"),
    ("vlm_match", ["auto_publish_disabled", "barcode_conflict"], "other"),
    ("vlm_match", ["auto_publish_disabled", "verifier_partial"], "other"),
    ("vlm_match", ["auto_publish_disabled", "conflicting_match:size"], "other"),
    ("tier1_unsure", ["auto_publish_disabled", "not_vlm_match"], "unsure"),
    ("tier1_unsure", ["auto_publish_disabled", "not_vlm_match", "brand_conf_none"], "unsure"),
    ("tier1_unknown", ["auto_publish_disabled", "not_vlm_match", "verifier_down"], "other"),
    ("tier2_corroborated", ["auto_publish_disabled", "not_tier1", "not_vlm_match"], "other"),
])
def test_pick_lane(why, blockers, lane):
    assert decide.pick_lane(why, blockers) == lane


def test_lane_of_reads_the_lane_reason_or_derives_it():
    assert decide.lane_of(["preselected:vlm_match", "lane:unsure"]) == "unsure"         # the recorded lane wins
    assert decide.lane_of(["preselected:vlm_match", "auto_blocked:auto_publish_disabled"]) == "strict"
    assert decide.lane_of(["preselected:vlm_match", "auto_publish"]) == "strict"
    assert decide.lane_of(["preselected:tier1_unsure", "auto_blocked:not_vlm_match"]) == "unsure"
    assert decide.lane_of(["preselected:vlm_match", "auto_blocked:conflicting_match:size"]) == "other"
    assert decide.lane_of(["cache_hit"]) is None and decide.lane_of([]) is None and decide.lane_of(None) is None
    assert decide.lane_of(["lane:bogus"]) is None
    assert decide.LANES == ("strict", "unsure", "other")
    import local_cache_db
    assert local_cache_db.LANES == decide.LANES


# ---------------------------------------------------------------------------
# route(): the lane on every pick, and AUTO_PUBLISH_STRICT_LANE
# ---------------------------------------------------------------------------

def test_route_writes_the_lane_on_every_pick():
    strict = _rc(_cand(1))
    assert decide.route(SPEC, [strict], OK, HEALTHY, set()).decision == "REVIEW_PRESELECTED"
    assert _lanes(strict) == ["lane:strict"]
    unsure = _rc(_cand(2), "UNSURE")
    decide.route(SPEC, [unsure], OK, HEALTHY, set())
    assert _lanes(unsure) == ["lane:unsure"] and "preselected:tier1_unsure" in unsure.reasons
    t2 = _rc(_cand(3, tier1=False))
    decide.route(SPEC, [t2], OK, HEALTHY, set())
    assert _lanes(t2) == ["lane:other"]
    # idempotent: a second route keeps one lane
    decide.route(SPEC, [strict], OK, HEALTHY, set())
    assert _lanes(strict) == ["lane:strict"]


def test_the_lane_is_off_by_default():
    assert settings.DEFAULTS["AUTO_PUBLISH_STRICT_LANE"] is False and settings.auto_publish_strict_lane() is False
    import config
    assert getattr(config, "AUTO_PUBLISH_STRICT_LANE", False) is False


def test_a_strict_pick_of_a_mapped_brand_auto_publishes_only_with_the_lane_on(monkeypatch):
    _set(monkeypatch, enabled=True, brands="Masafi")                     # the brand is not listed
    win = _rc(_cand(1))
    assert decide.route(SPEC, [win], OK, HEALTHY, set()).decision == "REVIEW_PRESELECTED"
    assert "auto_blocked:auto_publish_off_for_brand" in win.reasons

    _set(monkeypatch, enabled=True, brands="", lane=True)
    win = _rc(_cand(1))
    out = decide.route(SPEC, [win], OK, HEALTHY, set())
    assert out.decision == "AUTO_PUBLISH" and out.winner is win
    assert {"auto_publish", decide.LANE_PUBLISH_REASON, "lane:strict"} <= set(win.reasons)
    assert not [r for r in win.reasons if r.startswith("auto_blocked:")]


def test_the_lane_needs_auto_publish_switched_on(monkeypatch):
    _set(monkeypatch, enabled=False, lane=True)
    win = _rc(_cand(1))
    assert decide.route(SPEC, [win], OK, HEALTHY, set()).decision == "REVIEW_PRESELECTED"
    assert "auto_blocked:auto_publish_disabled" in win.reasons and _lanes(win) == ["lane:strict"]


def test_a_listed_brand_publishes_as_before_without_the_lane_reason(monkeypatch):
    _set(monkeypatch, enabled=True, brands="Almarai", lane=True)
    win = _rc(_cand(1))
    assert decide.route(SPEC, [win], OK, HEALTHY, set()).decision == "AUTO_PUBLISH"
    assert decide.LANE_PUBLISH_REASON not in win.reasons and _lanes(win) == ["lane:strict"]


@pytest.mark.parametrize("brand_conf", ["learned", "sheet_raw", "none"])
def test_an_unmapped_brand_never_auto_publishes_through_the_lane(monkeypatch, brand_conf):
    _set(monkeypatch, enabled=True, brands="", lane=True)
    spec = dataclasses.replace(SPEC, brand_conf=brand_conf)
    win = _rc(_cand(1), spec=spec)
    out = decide.route(spec, [win], OK, HEALTHY, set())
    assert out.decision == "REVIEW_PRESELECTED"
    assert f"auto_blocked:brand_conf_{brand_conf}" in win.reasons and _lanes(win) == ["lane:strict"]


@pytest.mark.parametrize("case, blocker", [
    ("unsanctioned", "unsanctioned_source"),
    ("relaxed", "relaxed_query"),
    ("cache_hit", "cache_hit"),
    ("tier2", "not_tier1"),
    ("unsure", "not_vlm_match"),
    ("conflict", "conflicting_match:size"),
])
def test_every_other_blocker_still_holds_with_the_lane_on(monkeypatch, case, blocker):
    _set(monkeypatch, enabled=True, brands="", lane=True)
    win = _rc(_cand(1, sanctioned=case != "unsanctioned", query_id="R1" if case == "relaxed" else "Q1",
                    tier1=case != "tier2"), "UNSURE" if case == "unsure" else "MATCH")
    ranked = [win]
    if case == "conflict":
        ranked.append(_rc(_cand(2), size_text="2 L"))
    out = decide.route(SPEC, ranked, OK, HEALTHY, {"R1"} if case == "relaxed" else set(),
                       cache_hit=case == "cache_hit")
    assert out.decision == "REVIEW_PRESELECTED"
    assert f"auto_blocked:{blocker}" in out.winner.reasons
    assert "auto_blocked:auto_publish_off_for_brand" in out.winner.reasons
    assert decide.LANE_PUBLISH_REASON not in out.winner.reasons


# ---------------------------------------------------------------------------
# Lane stats (local_cache_db.review_stats)
# ---------------------------------------------------------------------------

@pytest.fixture
def ldb():
    import local_cache_db
    return local_cache_db


class Rows:
    def __init__(self):
        self.rows = []

    def add(self, action, sku, lane, pre=1, decision="REVIEW_PRESELECTED", url=None, undone=False):
        n = len(self.rows) + 1
        self.rows.append({"id": n, "created_at": f"2026-10-05 {n // 3600:02d}:{n // 60 % 60:02d}:{n % 60:02d}",
                          "action": action, "sku_key": sku, "brand": "ALMARAI",
                          "image_url": url or f"https://img.ae/{sku}.jpg", "was_preselected": pre,
                          "engine_decision": decision, "reason_code": "WRONG_SIZE" if action == "rejected" else None,
                          "page_domain": "lulu.ae", "lane": lane, "undone_at": "2026-10-05 12:00:00" if undone else None})
        return self


def test_lane_counts_accepted_replaced_and_rejected(ldb):
    rows = Rows()
    for i in range(12):
        rows.add("approved", f"s-{i}", "strict")
    rows.add("approved", "s-other-img", "strict", pre=0, url="https://img.ae/another.jpg")      # replaced
    rows.add("manual_upload", "s-upload", "strict", pre=0, url=None)                              # replaced
    rows.add("rejected", "s-bad", "strict")                                                       # rejected
    rows.add("rejected", "s-undone", "strict", undone=True)                                       # never counts
    rows.add("approved", "u-1", "unsure").add("approved", "u-2", "unsure").add("rejected", "u-3", "unsure")
    rows.add("approved", "o-1", "other")
    rows.add("approved", "legacy", None)                                                          # before the lanes
    rows.add("approved", "no-pick", None, pre=0, decision="REVIEW_UNSELECTED")                    # not a pre-check
    stats = ldb.review_stats(rows.rows)
    strict, unsure, other = (stats["lanes"][k] for k in ("strict", "unsure", "other"))
    assert (strict["prechecked"], strict["accepted"], strict["replaced"], strict["rejected"]) == (15, 12, 2, 1)
    assert strict["precision"] == pytest.approx(12 / 15)
    assert strict["lower_bound"] == pytest.approx(ldb.wilson_lower_bound(12, 15))
    assert (unsure["prechecked"], unsure["accepted"], unsure["rejected"]) == (3, 2, 1)
    assert (other["prechecked"], other["accepted"]) == (1, 1)
    assert stats["unlaned_prechecked"] == 1
    assert stats["overall"]["prechecked"] == 20          # the brand numbers are unchanged by the lanes


def test_lane_of_the_deciding_row_and_the_fallback(ldb):
    rows = Rows()
    rows.add("rejected", "a", None, pre=0, url="https://img.ae/sibling.jpg")       # a sibling, no lane recorded
    rows.add("approved", "a", "strict")
    rows.add("approved", "b", None)                                                  # the lane on another row
    rows.add("rejected", "b", "unsure", pre=0, url="https://img.ae/b-sibling.jpg")
    lanes = ldb.review_stats(rows.rows)["lanes"]
    assert (lanes["strict"]["prechecked"], lanes["strict"]["accepted"]) == (1, 1)
    assert (lanes["unsure"]["prechecked"], lanes["unsure"]["accepted"]) == (1, 1)


@pytest.mark.parametrize("n, accepted, status", [
    (0, 0, "needs_reviews"),
    (29, 29, "needs_reviews"),
    (30, 30, "needs_reviews"),          # enough reviews, but the lower bound is still below 98%
    (188, 188, "needs_reviews"),
    (189, 189, "ready"),                # the first perfect record that reaches the bound
    (189, 188, "needs_reviews"),
    (30, 29, "low_precision"),
])
def test_strict_lane_readiness_on_the_brand_thresholds(ldb, n, accepted, status):
    rows = Rows()
    for i in range(n):
        rows.add("approved" if i < accepted else "rejected", f"s-{i}", "strict")
    strict = ldb.review_stats(rows.rows)["lanes"]["strict"]
    assert strict["status"] == status == ldb.brand_status(n, accepted)
    assert strict["ready"] is (status == "ready")
    assert strict["reviews_needed"] == ldb.reviews_needed(n, accepted)


def test_no_rows_give_empty_lanes(ldb):
    stats = ldb.review_stats([])
    assert set(stats["lanes"]) == {"strict", "unsure", "other"}
    assert stats["lanes"]["strict"] == {"prechecked": 0, "accepted": 0, "replaced": 0, "rejected": 0,
                                        "precision": None, "lower_bound": None, "status": "needs_reviews",
                                        "ready": False, "reviews_needed": 189, "more_needed": 189}
    assert stats["unlaned_prechecked"] == 0


def test_script_prints_the_lanes(ldb):
    spec = importlib.util.spec_from_file_location("review_stats_lanes", ROOT / "scripts" / "review_stats.py")
    script = importlib.util.module_from_spec(spec)
    sys.modules["review_stats_lanes"] = script
    spec.loader.exec_module(script)
    rows = Rows()
    for i in range(189):
        rows.add("approved", f"s-{i}", "strict")
    rows.add("approved", "u-1", "unsure").add("rejected", "u-2", "unsure")
    text = script.format_report(ldb.review_stats(rows.rows))
    assert "Per lane" in text
    assert "strict  189         189       0         0         100.0%" in text
    assert "unsure  2           1         0         1         50.0%" in text and "display only" in text
    assert "Lane 'strict' is ready" in text


# ---------------------------------------------------------------------------
# A review warning keeps a pick out of 'strict' (integration review)
# ---------------------------------------------------------------------------

def test_a_pick_with_a_review_warning_is_lane_other_and_never_published_by_the_lane(monkeypatch):
    monkeypatch.setattr(decide, "review_warnings", lambda spec, rc, reading_of=None: ["low_resolution"])
    _set(monkeypatch, enabled=True, brands="", lane=True)
    win = _rc(_cand(1))
    out = decide.route(SPEC, [win], OK, HEALTHY, set())
    assert out.decision == "REVIEW_PRESELECTED" and out.winner is win
    assert _lanes(win) == ["lane:other"] and "auto_blocked:review_warning" in win.reasons
    assert "auto_publish" not in win.reasons and decide.LANE_PUBLISH_REASON not in win.reasons
    assert "warn:low_resolution" in win.reasons


def test_a_warned_pick_with_the_lane_off_is_recorded_as_other(monkeypatch):
    monkeypatch.setattr(decide, "review_warnings", lambda spec, rc, reading_of=None: ["foreign_store"])
    win = _rc(_cand(1))
    assert decide.route(SPEC, [win], OK, HEALTHY, set()).decision == "REVIEW_PRESELECTED"
    assert _lanes(win) == ["lane:other"] and "auto_blocked:review_warning" not in win.reasons


def test_lane_of_a_stored_pick_with_a_warning_is_other():
    assert decide.lane_of(["preselected:vlm_match", "auto_blocked:auto_publish_disabled",
                           "warn:sheet_silent:flavour=chili"]) == "other"
