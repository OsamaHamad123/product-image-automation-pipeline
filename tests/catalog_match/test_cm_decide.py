"""catalog_match.decide.route: decision routing exactly as D10 states it."""

import pytest

from catalog_match import decide, settings
from catalog_match.identity import build_sku_spec
from catalog_match.models import (
    Candidate, FetchedImage, ProviderHealth, ProviderResult, QualityReport, RankedCandidate,
    VerificationResult, VlmImageVerdict,
)
from catalog_match.score import rank, score_candidate

MAPPINGS = {"almarai": {"brand": "Almarai", "synonyms": ["المراعي", "Al Marai"],
                        "excluded_competitors": ["Al Ain", "Nada"]}}
ROW = {"name": "Almarai Full Fat Milk 1L", "brand": "Almarai", "category": "Dairy"}
SPEC = build_sku_spec(ROW, MAPPINGS)
HEALTHY = [ProviderHealth("serper", "ok", 200), ProviderHealth("off", "empty", 404)]
PAGE = "https://www.carrefouruae.com/mafuae/en/almarai-full-fat-milk-1l/p/{}"


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    """Read settings from the environment only (never from config / the dashboard DB)."""
    monkeypatch.setattr(settings, "_config", None)
    monkeypatch.setenv("AUTO_PUBLISH_ENABLED", "false")
    monkeypatch.setenv("AUTO_PUBLISH_BRANDS", "")


def auto_on(monkeypatch, brands="Almarai"):
    monkeypatch.setenv("AUTO_PUBLISH_ENABLED", "true")
    monkeypatch.setenv("AUTO_PUBLISH_BRANDS", brands)


# -- candidate builders -------------------------------------------------------

def t1(n=1, provider="serper", sanctioned=True, query_id="Q1"):
    return Candidate(image_url=f"https://cdn.carrefouruae.com/{n}.jpg", page_url=PAGE.format(n),
                     title="Almarai Full Fat Milk 1L", domain="carrefouruae.com", provider=provider,
                     sanctioned=sanctioned, query_id=query_id, rank=n)


def t2(n=10, query_id="Q1"):
    return Candidate(image_url=f"https://blog.example.com/{n}.jpg", page_url=f"https://blog.example.com/milk-{n}",
                     title="Almarai milk", provider="serper", query_id=query_id, rank=n)


def t3(n=20):
    return Candidate(image_url=f"https://cdn.example.com/{n}.jpg", provider="serper", rank=n)


def sibling(n=30):
    return Candidate(image_url=f"https://cdn.noon.com/{n}.jpg", page_url=f"https://www.noon.com/almarai-low-fat-milk-1l-{n}",
                     title="Almarai Low Fat Milk 1L", provider="serper", rank=n)


def competitor(n=40):
    return Candidate(image_url=f"https://cdn.noon.com/{n}.jpg", page_url=f"https://www.noon.com/al-ain-milk-{n}",
                     title="Al Ain Full Fat Milk 1L", provider="serper", rank=n)


def verdict(decision, size_text="1 L", variant_text="Full Fat", pack=None):
    return VlmImageVerdict(index=0, brand_text="Almarai", variant_text=variant_text, size_text=size_text,
                           pack_count=pack, view="front_packshot" if decision == "MATCH" else "lifestyle",
                           brand_match="yes" if decision != "MISMATCH" else "no",
                           decision=decision)


def rc(cand, v=None, spec=SPEC, fetch_ok=True, fetch_error=None, quality_ok=True, fetched=True):
    score = score_candidate(spec, cand)
    fi = None
    if fetched:
        fi = FetchedImage(candidate=cand, ok=fetch_ok, error=None if fetch_ok else (fetch_error or "http_403"),
                          content_sha256="ab" * 32 if fetch_ok else None, width=800, height=800,
                          path_or_bytes=b"x" if fetch_ok else None)
    q = QualityReport(hard_ok=quality_ok, hard_reasons=[] if quality_ok else ["short_side<250"],
                      quality_score=0.8 if quality_ok else 0.0)
    return RankedCandidate(candidate=cand, score=score, fetched=fi, quality=q if fetched and fetch_ok else None,
                           verdict=v)


def ranked(*rcs):
    order = rank([(r.candidate, r.score) for r in rcs])
    by_id = {id(r.candidate): r for r in rcs}
    return [by_id[id(c)] for c, _ in order]


OK = VerificationResult(status="ok", calls=1)
DOWN = VerificationResult(status="unknown", calls=1, error="http_429")


def preselected(outcome):
    return [r for r in outcome.ranked if r.status == "preselected"]


# -- the D10 table ---------------------------------------------------------------

def test_sanity_of_fixture_tiers():
    assert SPEC.brand_conf == "mapped"
    assert score_candidate(SPEC, t1()).tier == 1
    assert score_candidate(SPEC, t2()).tier == 2
    assert score_candidate(SPEC, t3()).tier == 3
    assert score_candidate(SPEC, sibling()).hard_reject == ("variant_conflict:fat",)
    assert score_candidate(SPEC, competitor()).hard_reject == ("competitor_brand",)


def test_t1_match_sanctioned_auto_enabled_is_auto_publish(monkeypatch):
    auto_on(monkeypatch)
    win = rc(t1(), verdict("MATCH"))
    other = rc(t2(), verdict("UNSURE"))
    out = decide.route(SPEC, ranked(win, other), OK, HEALTHY, set())
    assert out.decision == "AUTO_PUBLISH"
    assert out.failure_code is None
    assert out.winner is win and win.status == "preselected"
    assert other.status == "eligible"
    assert preselected(out) == [win]
    assert out.sku_key == SPEC.sku_key and out.vlm_calls == 1


def test_auto_disabled_gives_review_preselected():
    win = rc(t1(), verdict("MATCH"))
    out = decide.route(SPEC, [win], OK, HEALTHY, set())
    assert out.decision == "REVIEW_PRESELECTED"
    assert out.winner is win and win.status == "preselected"
    assert "auto_blocked:auto_publish_disabled" in win.reasons


def test_brand_allow_list(monkeypatch):
    auto_on(monkeypatch, "Al Rawabi, Masafi")
    assert decide.route(SPEC, [rc(t1(), verdict("MATCH"))], OK, HEALTHY, set()).decision == "REVIEW_PRESELECTED"
    auto_on(monkeypatch, "*")
    assert decide.route(SPEC, [rc(t1(), verdict("MATCH"))], OK, HEALTHY, set()).decision == "AUTO_PUBLISH"
    auto_on(monkeypatch, "category:dairy")
    assert decide.route(SPEC, [rc(t1(), verdict("MATCH"))], OK, HEALTHY, set()).decision == "AUTO_PUBLISH"


def test_bing_sourced_t1_match_is_review_preselected(monkeypatch):
    auto_on(monkeypatch, "*")
    win = rc(t1(provider="bing_html", sanctioned=False), verdict("MATCH"))
    assert win.score.tier == 1
    out = decide.route(SPEC, [win], OK, HEALTHY, set())
    assert out.decision == "REVIEW_PRESELECTED"
    assert "auto_blocked:unsanctioned_source" in win.reasons


def test_t1_unknown_is_preselected_with_verifier_down(monkeypatch):
    auto_on(monkeypatch, "*")
    win = rc(t1(), VlmImageVerdict(index=0, decision="UNKNOWN"))
    other = rc(t2(), VlmImageVerdict(index=1, decision="UNKNOWN"))
    out = decide.route(SPEC, ranked(win, other), DOWN, HEALTHY, set())
    assert out.decision == "REVIEW_PRESELECTED"
    assert out.failure_code == "VERIFIER_DOWN"
    assert out.winner is win and preselected(out) == [win]


def test_verifier_down_without_t1_is_review_unselected():
    out = decide.route(SPEC, [rc(t2(), VlmImageVerdict(index=0, decision="UNKNOWN"))], DOWN, HEALTHY, set())
    assert (out.decision, out.failure_code) == ("REVIEW_UNSELECTED", "VERIFIER_DOWN")
    assert preselected(out) == [] and out.winner is None


def test_all_mismatch_is_review_unselected():
    rcs = ranked(rc(t1(1), verdict("MISMATCH")), rc(t1(2), verdict("MISMATCH")), rc(t2(), verdict("MISMATCH")))
    out = decide.route(SPEC, rcs, OK, HEALTHY, set())
    assert out.decision == "REVIEW_UNSELECTED"
    assert out.failure_code is None
    assert out.winner is None
    assert all(r.status != "preselected" for r in out.ranked)
    assert all(r.status == "rejected" and "vlm:MISMATCH" in r.reasons for r in out.ranked)
    assert out.reject_counts["vlm:MISMATCH"] == 3


def test_empty_pool_with_healthy_providers_is_not_found():
    out = decide.route(SPEC, [], None, HEALTHY, set())
    assert (out.decision, out.failure_code) == ("NOT_FOUND", "NO_RESULTS")


def test_all_conflicted_counts_per_rule():
    rcs = [rc(sibling(30)), rc(sibling(31)), rc(competitor())]
    out = decide.route(SPEC, rcs, None, HEALTHY, set())
    assert (out.decision, out.failure_code) == ("NOT_FOUND", "ALL_CONFLICTED")
    assert out.reject_counts == {"variant_conflict:fat": 2, "competitor_brand": 1}
    assert all(r.status == "rejected" for r in out.ranked)


def test_all_providers_blocked_is_provider_down():
    health = [ProviderResult("serper", "quota", 429).health(), ProviderHealth("bing_html", "blocked", 200)]
    out = decide.route(SPEC, [], None, health, set())
    assert (out.decision, out.failure_code) == ("PROVIDER_DOWN", "PROVIDER_DOWN")
    # The OFF GTIN lookup answering 'empty' does not make an image-search outage healthy.
    out = decide.route(SPEC, [], None, [ProviderHealth("serper", "error", 500), ProviderHealth("off", "empty")], set())
    assert out.decision == "PROVIDER_DOWN"
    # One search provider healthy: a real NOT_FOUND.
    out = decide.route(SPEC, [], None, [ProviderHealth("serper", "empty", 200), ProviderHealth("bing_html", "blocked")],
                       set())
    assert (out.decision, out.failure_code) == ("NOT_FOUND", "NO_RESULTS")
    # The main query (Q1) was never answered: later 'empty' answers do not prove the product
    # is missing, so this is a retryable outage, not a product failure.
    out = decide.route(SPEC, [], None, [ProviderHealth("serper", "error", 500, query_id="Q1"),
                                        ProviderHealth("serper", "empty", 200, query_id="Q2")], set())
    assert out.decision == "PROVIDER_DOWN"
    # Q1 answered 'empty' and a later query failed: a real NOT_FOUND.
    out = decide.route(SPEC, [], None, [ProviderHealth("serper", "empty", 200, query_id="Q1"),
                                        ProviderHealth("serper", "error", 500, query_id="Q2")], set())
    assert out.decision == "NOT_FOUND"
    # A staff custom query that errored is the main query too.
    out = decide.route(SPEC, [], None, [ProviderHealth("serper", "error", 503, query_id="custom")], set())
    assert out.decision == "PROVIDER_DOWN"


def test_relaxed_query_winner_never_auto_publishes(monkeypatch):
    auto_on(monkeypatch, "*")
    win = rc(t1(query_id="R1"), verdict("MATCH"))
    out = decide.route(SPEC, [win], OK, HEALTHY, {"R1"})
    assert out.decision == "REVIEW_PRESELECTED"
    assert "auto_blocked:relaxed_query" in win.reasons


def test_sheet_raw_brand_never_auto_publishes(monkeypatch):
    auto_on(monkeypatch, "*")
    raw_spec = build_sku_spec(ROW, None)
    assert raw_spec.brand_conf == "sheet_raw"
    win = rc(t1(), verdict("MATCH"), spec=raw_spec)
    assert win.score.tier == 1
    out = decide.route(raw_spec, [win], OK, HEALTHY, set())
    assert out.decision == "REVIEW_PRESELECTED"
    assert "auto_blocked:brand_conf_sheet_raw" in win.reasons


def test_cache_hit_never_auto_publishes(monkeypatch):
    auto_on(monkeypatch, "*")
    out = decide.route(SPEC, [rc(t1(), verdict("MATCH"))], OK, HEALTHY, set(), cache_hit=True)
    assert out.decision == "REVIEW_PRESELECTED"


def test_t2_match_is_preselected_but_never_auto(monkeypatch):
    auto_on(monkeypatch, "*")
    win = rc(t2(), verdict("MATCH"))
    out = decide.route(SPEC, [win], OK, HEALTHY, set())
    assert out.decision == "REVIEW_PRESELECTED" and out.winner is win
    assert "auto_blocked:not_tier1" in win.reasons


def test_t1_unsure_preselected_t2_unsure_and_t3_match_are_not():
    out = decide.route(SPEC, [rc(t1(), verdict("UNSURE"))], OK, HEALTHY, set())
    assert out.decision == "REVIEW_PRESELECTED" and out.failure_code is None
    out = decide.route(SPEC, [rc(t2(), verdict("UNSURE"))], OK, HEALTHY, set())
    assert out.decision == "REVIEW_UNSELECTED" and preselected(out) == []
    out = decide.route(SPEC, [rc(t3(), verdict("MATCH"))], OK, HEALTHY, set())
    assert out.decision == "REVIEW_UNSELECTED" and preselected(out) == []


def test_match_beats_unverified_t1():
    t1_unsure = rc(t1(1), verdict("UNSURE"))
    t2_match = rc(t2(), verdict("MATCH"))
    out = decide.route(SPEC, ranked(t1_unsure, t2_match), OK, HEALTHY, set())
    assert out.ranked[0] is t1_unsure              # ranked first by identity...
    assert out.winner is t2_match                  # ...but a verified MATCH is preselected
    assert t1_unsure.status == "eligible"


def test_failed_download_or_quality_is_never_preselected(monkeypatch):
    auto_on(monkeypatch, "*")
    dead = rc(t1(1), verdict("MATCH"), fetch_ok=False, fetch_error="http_403")
    tiny = rc(t1(2), verdict("MATCH"), quality_ok=False)
    good = rc(t2(), verdict("MATCH"))
    out = decide.route(SPEC, [dead, tiny, good], OK, HEALTHY, set())
    assert out.winner is good
    assert dead.status == "rejected" and "download:http_403" in dead.reasons
    assert tiny.status == "rejected" and "quality:short_side<250" in tiny.reasons
    assert out.reject_counts["download:http_403"] == 1


def test_every_download_failed():
    rcs = [rc(t1(1), fetch_ok=False, fetch_error="http_403"), rc(t2(), fetch_ok=False, fetch_error="timeout"),
           rc(t3(), fetched=False)]
    out = decide.route(SPEC, rcs, None, HEALTHY, set())
    assert (out.decision, out.failure_code) == ("REVIEW_UNSELECTED", "DOWNLOAD_FAILED")
    assert preselected(out) == [] and out.winner is None


def test_conflicting_match_blocks_auto_publish(monkeypatch):
    auto_on(monkeypatch, "*")
    win = rc(t1(1), verdict("MATCH", size_text="1 L"))
    other = rc(t1(2), verdict("MATCH", size_text="2 L"))
    out = decide.route(SPEC, [win, other], OK, HEALTHY, set())
    assert out.decision == "REVIEW_PRESELECTED"
    assert "auto_blocked:conflicting_match:size" in win.reasons
    # Two MATCH readings that agree do not block.
    win2 = rc(t1(3), verdict("MATCH", size_text="1 L"))
    same = rc(t1(4), verdict("MATCH", size_text="1000 ml"))
    assert decide.route(SPEC, [win2, same], OK, HEALTHY, set()).decision == "AUTO_PUBLISH"
    # A variant clash between two MATCH readings blocks too.
    win3 = rc(t1(5), verdict("MATCH", variant_text="Full Fat"))
    low = rc(t1(6), verdict("MATCH", variant_text="Low Fat"))
    out = decide.route(SPEC, [win3, low], OK, HEALTHY, set())
    assert "auto_blocked:conflicting_match:variant:fat" in win3.reasons


def test_route_is_idempotent_and_keeps_pipeline_reasons():
    excluded = rc(t1(9))
    excluded.status = "excluded"
    excluded.reasons.append("reviewer_negative:phash")
    win = rc(t1(1), verdict("MATCH"))
    rcs = [excluded, win, rc(sibling())]
    first = decide.route(SPEC, rcs, OK, HEALTHY, set())
    snapshot = [(r.status, list(r.reasons)) for r in first.ranked]
    second = decide.route(SPEC, rcs, OK, HEALTHY, set())
    assert [(r.status, list(r.reasons)) for r in second.ranked] == snapshot
    assert excluded.status == "excluded" and excluded.reasons == ["reviewer_negative:phash"]
    assert second.reject_counts == {"excluded": 1, "variant_conflict:fat": 1}


# -- live run 2026-09-30: an unconfirmed tier-1 image was pre-checked (sheet row 34) -------------

PARTIAL = [OK, DOWN]      # the first verifier call answered, the second one failed


def size_mismatch():
    """The right brand is printed, the size is not the SKU's: MISMATCH that says nothing about the brand."""
    return VlmImageVerdict(index=0, brand_text="Almarai", size_text="2 L", view="front_packshot",
                           brand_match="yes", size_match="no", decision="MISMATCH")


def test_t1_the_verifier_never_saw_is_not_preselected_while_it_is_up():
    # The call answered but skipped this image (or it could not be decoded): UNKNOWN is not a reading.
    unseen = rc(t1(2), VlmImageVerdict(index=1, decision="UNKNOWN"))
    out = decide.route(SPEC, ranked(rc(t1(1), size_mismatch()), unseen), OK, HEALTHY, set())
    assert (out.decision, out.failure_code) == ("REVIEW_UNSELECTED", None)
    assert out.winner is None and preselected(out) == []
    assert unseen.status == "eligible"          # still shown to the reviewer, just not pre-checked


def test_failed_second_call_is_verifier_down_and_nothing_unseen_is_preselected():
    first = rc(t1(1), size_mismatch())
    unseen = rc(t1(2), VlmImageVerdict(index=0, decision="UNKNOWN"))
    out = decide.route(SPEC, ranked(first, unseen), PARTIAL, HEALTHY, set())
    assert (out.decision, out.failure_code) == ("REVIEW_UNSELECTED", "VERIFIER_DOWN")
    assert preselected(out) == []


def test_match_with_a_failed_second_call_is_preselected_but_never_auto(monkeypatch):
    auto_on(monkeypatch, "*")
    win = rc(t1(1), verdict("MATCH"))
    unseen = rc(t1(2), VlmImageVerdict(index=0, decision="UNKNOWN"))
    out = decide.route(SPEC, ranked(win, unseen), PARTIAL, HEALTHY, set())
    assert out.decision == "REVIEW_PRESELECTED" and out.winner is win and out.failure_code is None
    assert "auto_blocked:verifier_partial" in win.reasons


def test_another_brand_on_a_tier1_image_turns_the_tier1_fallback_off():
    # The listing text named the brand, the label shows another one: tier 1 is not evidence here.
    other_brand = rc(t1(1), verdict("MISMATCH"))                  # brand_match 'no'
    unsure = rc(t1(2), verdict("UNSURE"))
    out = decide.route(SPEC, ranked(other_brand, unsure), OK, HEALTHY, set())
    assert out.decision == "REVIEW_UNSELECTED" and out.winner is None
    assert "vlm:tier1_brand_refuted" in unsure.reasons
    assert out.reject_counts["vlm:tier1_brand_refuted"] == 1
    # route() is idempotent: the reason is not duplicated on a second pass.
    decide.route(SPEC, out.ranked, OK, HEALTHY, set())
    assert unsure.reasons.count("vlm:tier1_brand_refuted") == 1


def test_a_verified_match_still_wins_when_another_tier1_shows_another_brand():
    other_brand = rc(t1(1), verdict("MISMATCH"))
    match = rc(t1(2), verdict("MATCH"))
    out = decide.route(SPEC, ranked(other_brand, match), OK, HEALTHY, set())
    assert out.decision == "REVIEW_PRESELECTED" and out.winner is match


def test_a_size_mismatch_does_not_refute_the_brand():
    unsure = rc(t1(2), verdict("UNSURE"))
    out = decide.route(SPEC, ranked(rc(t1(1), size_mismatch()), unsure), OK, HEALTHY, set())
    assert out.decision == "REVIEW_PRESELECTED" and out.winner is unsure
    assert "preselected:tier1_unsure" in unsure.reasons
