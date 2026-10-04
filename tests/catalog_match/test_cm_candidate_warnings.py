"""Display-only warnings for every candidate (decide.candidate_warnings), the review screen's evidence.

Before, 'warn:' reasons existed only on the pick: an alternative from a '.sa' store, with a page barcode that
differs from the sheet's, from a social network or below 500 px showed no caution when the reviewer chose it,
and a REVIEW_UNSELECTED product showed none at all. Two codes are new and display-only: size_unverified and
variant_unverified (the sheet states a size / a variant and neither the listing nor the label reading confirmed
it, e.g. a tier-2 pick read with size_match 'unsure').

They must never change routing: route() keeps its winner, tiers, decision and reasons, candidate_warnings()
writes nothing, and the eval gate runs unchanged (tests/eval/test_eval_display_warnings.py).
"""

import copy

import pytest

import main
from catalog_match import decide, facade, settings
from catalog_match.identity import build_sku_spec
from catalog_match.models import (
    Candidate, FetchedImage, ProviderHealth, QualityReport, RankedCandidate, VerificationResult, VlmImageVerdict,
)
from catalog_match.score import score_candidate

HEALTHY = [ProviderHealth("serper", "ok", 200)]
OK = VerificationResult(status="ok", calls=1)
MAPPINGS = {"almarai": {"brand": "Almarai", "synonyms": ["المراعي"], "official_domains": ["almarai.com"]}}
MILK = build_sku_spec({"name": "Almarai Full Fat Milk 1L", "brand": "Almarai", "category": "Dairy",
                       "barcode": "6281007000024"}, MAPPINGS)
LULU = "https://www.luluhypermarket.com/en-ae/almarai-full-fat-milk-1l/p/2"
PLAIN_PAGE = "https://www.luluhypermarket.com/en-ae/almarai-dairy/p/2"     # the slug names no size, fat or pack


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    monkeypatch.setattr(settings, "_config", None)
    monkeypatch.setenv("AUTO_PUBLISH_ENABLED", "false")
    monkeypatch.setenv("AUTO_PUBLISH_BRANDS", "")


def cand(image_url, page_url=LULU, title="Almarai Full Fat Milk 1L Online at Best Price | Lulu UAE", **kw):
    return Candidate(image_url=image_url, page_url=page_url, title=title, page_title=title, provider="serper",
                     query_id="Q1", rank=kw.pop("rank", 1), **kw)


def reading(decision="MATCH", size="yes", variant="yes", brand="yes", pack_count=None):
    return VlmImageVerdict(index=0, brand_text="Almarai", variant_text="Full Fat Milk", size_text="1 L",
                           pack_count=pack_count, view="front_packshot", brand_match=brand, variant_match=variant,
                           size_match=size, decision=decision)


def ranked(spec, c, verdict=None, size=(800, 800)):
    fi = FetchedImage(candidate=c, ok=True, content_sha256="ab" * 32, width=size[0], height=size[1],
                      path_or_bytes=b"x")
    return RankedCandidate(candidate=c, score=score_candidate(spec, c), fetched=fi,
                           quality=QualityReport(hard_ok=True, quality_score=0.8), verdict=verdict)


def snapshot(pool):
    return [(rc.status, tuple(rc.reasons), rc.score.tier) for rc in pool]


def alternatives_pool():
    winner = ranked(MILK, cand("https://www.luluhypermarket.com/medias/almarai-milk.jpg"), reading())
    saudi = ranked(MILK, cand("https://www.danube.com.sa/media/almarai-milk.jpg",
                              page_url="https://www.danube.com.sa/en/product/almarai-full-fat-milk-1l",
                              title="Almarai Full Fat Milk 1L | Danube", rank=2), reading())
    other_code = ranked(MILK, cand("https://www.luluhypermarket.com/medias/almarai-milk-2.jpg",
                                   page_url="https://www.luluhypermarket.com/en-ae/almarai-full-fat-milk-1l/p/3",
                                   gtin_on_page="6281007000031", rank=3), reading())
    social = ranked(MILK, cand("https://scontent.cdninstagram.com/v/almarai-milk.jpg",
                               page_url="https://www.instagram.com/p/almarai-full-fat-milk-1l/",
                               title="Almarai Full Fat Milk 1L", rank=4), reading())
    small = ranked(MILK, cand("https://www.luluhypermarket.com/medias/almarai-milk-small.jpg", rank=5), reading(),
                   size=(300, 300))
    return [winner, saudi, other_code, social, small]


def test_every_eligible_alternative_carries_its_own_warning_and_routing_is_untouched():
    pool = alternatives_pool()
    outcome = decide.route(MILK, pool, OK, HEALTHY, set())
    winner, saudi, other_code, social, small = pool
    assert outcome.winner is winner and outcome.decision == "REVIEW_PRESELECTED"
    before = snapshot(pool)
    # route() still writes 'warn:' reasons on the pick only
    assert [r for rc in pool[1:] for r in rc.reasons if r.startswith("warn:")] == []

    shown = {rc.candidate.image_url: decide.candidate_warnings(MILK, rc) for rc in pool}
    assert shown[winner.candidate.image_url] == []
    assert "foreign_store" in shown[saudi.candidate.image_url]
    assert "barcode_conflict" in shown[other_code.candidate.image_url]
    assert "social_media" in shown[social.candidate.image_url]
    assert "low_resolution" in shown[small.candidate.image_url]

    # display only: nothing written, and routing again gives the same winner, decision and reasons
    assert snapshot(pool) == before
    again = decide.route(MILK, pool, OK, HEALTHY, set())
    assert again.winner is winner and again.decision == outcome.decision and snapshot(pool) == before


def test_a_tier2_pick_read_with_size_unsure_is_size_unverified_without_changing_the_decision():
    # the listing names no size (tier 2) and the label reader could not read it: nothing confirmed 1 L
    c = cand("https://www.luluhypermarket.com/medias/almarai-milk.jpg", page_url=PLAIN_PAGE,
             title="Almarai Full Fat Milk | Lulu UAE")
    pick = ranked(MILK, c, reading(size="unsure"))
    assert pick.score.tier == 2 and pick.score.size_status != "match"
    outcome = decide.route(MILK, [pick], OK, HEALTHY, set())
    assert outcome.winner is pick and outcome.decision == "REVIEW_PRESELECTED"
    reasons = list(pick.reasons)
    assert decide.candidate_warnings(MILK, pick) == ["size_unverified"]
    # never a 'warn:' reason: resolution_upgrade and expand read those
    assert "warn:size_unverified" not in pick.reasons and pick.reasons == reasons
    assert decide.review_warnings(MILK, pick) == []

    # confirmed by the label reading, by the listing, or by the sheet's own barcode on the page: no warning
    assert decide.candidate_warnings(MILK, ranked(MILK, c, reading(size="yes"))) == []
    listed = ranked(MILK, cand("https://www.luluhypermarket.com/medias/m.jpg"), reading(size="unsure"))
    assert listed.score.size_status == "match" and decide.candidate_warnings(MILK, listed) == []
    coded = ranked(MILK, cand("https://www.luluhypermarket.com/medias/m.jpg", page_url=PLAIN_PAGE,
                              title="Almarai Full Fat Milk | Lulu", gtin_on_page="6281007000024"),
                   reading(size="unsure"))
    assert decide.candidate_warnings(MILK, coded) == []


def test_a_variant_neither_the_listing_nor_the_label_confirmed_is_variant_unverified():
    assert MILK.variants == {"fat": "full"}
    c = cand("https://www.luluhypermarket.com/medias/almarai-milk.jpg", page_url=PLAIN_PAGE,
             title="Almarai Milk 1L | Lulu UAE")
    rc = ranked(MILK, c, reading(variant="unsure"))
    rc.status = "eligible"
    assert decide.candidate_warnings(MILK, rc) == ["variant_unverified"]
    assert decide.candidate_warnings(MILK, ranked(MILK, c, reading(variant="yes"))) == []


def test_a_pack_the_sheet_states_needs_the_listing_or_the_label_count():
    spec = build_sku_spec({"name": "Almarai Laban 6x200ml", "brand": "Almarai"}, MAPPINGS)
    assert spec.pack_count == 6
    c = cand("https://www.luluhypermarket.com/medias/laban.jpg", page_url=PLAIN_PAGE, title="Almarai Laban 200ml")
    assert "size_unverified" in decide.candidate_warnings(spec, ranked(spec, c, reading(pack_count=1)))
    assert "size_unverified" not in decide.candidate_warnings(spec, ranked(spec, c, reading(pack_count=6)))


def test_an_alternative_the_verifier_never_read_is_not_called_unsure_and_rejected_ones_get_nothing():
    c = cand("https://www.danube.com.sa/media/m.jpg",
             page_url="https://www.danube.com.sa/en/product/almarai-full-fat-milk-1l", title="Almarai Full Fat Milk 1L")
    unread = ranked(MILK, c, None)
    unread.status = "eligible"
    shown = decide.candidate_warnings(MILK, unread)
    assert "foreign_store" in shown and "vlm_unsure" not in shown
    unsure = ranked(MILK, c, reading("UNSURE"))
    unsure.status = "eligible"
    assert "vlm_unsure" in decide.candidate_warnings(MILK, unsure)
    for status in ("rejected", "excluded"):
        r = ranked(MILK, c, reading("MISMATCH"))
        r.status = status
        assert decide.candidate_warnings(MILK, r) == []


def test_a_best_resolution_copy_is_judged_on_the_reading_of_the_winner_it_replaced():
    c = cand("https://www.luluhypermarket.com/medias/m-large.jpg", page_url=PLAIN_PAGE,
             title="Almarai Full Fat Milk | Lulu UAE")
    copy_rc = ranked(MILK, c, None, size=(1200, 1200))
    copy_rc.status = "preselected"
    replaced = ranked(MILK, cand("https://www.luluhypermarket.com/medias/m.jpg"), reading(size="yes"))
    assert decide.candidate_warnings(MILK, copy_rc) == ["size_unverified"]
    assert decide.candidate_warnings(MILK, copy_rc, reading_of=replaced) == []


def test_facade_with_the_spec_sends_every_candidates_warnings_and_without_it_keeps_the_old_shape():
    pool = alternatives_pool()
    outcome = decide.route(MILK, pool, OK, HEALTHY, set())
    before = snapshot(pool)
    trace = {}
    legacy = facade.outcome_to_legacy(outcome, trace, spec=MILK)
    by_url = {c["url"]: c for c in legacy["candidates"]}
    assert by_url[pool[0].candidate.image_url]["warnings"] == []
    assert "foreign_store" in by_url[pool[1].candidate.image_url]["warnings"]
    assert "barcode_conflict" in by_url[pool[2].candidate.image_url]["warnings"]
    steps = {c["url"]: c["warnings"] for c in trace["steps"][0]["candidates"]}
    assert steps == {u: c["warnings"] for u, c in by_url.items()}
    # the reasons stay route()'s own; the decision fields are unchanged
    assert all(not any(r.startswith("warn:") for r in by_url[rc.candidate.image_url]["reasons"]) for rc in pool[1:])
    assert snapshot(pool) == before
    old = facade.outcome_to_legacy(outcome, None)
    assert {c["url"]: c["warnings"] for c in old["candidates"]} == {rc.candidate.image_url: [] for rc in pool}
    assert (old["decision"], old["url"], old["preselect"]) == (legacy["decision"], legacy["url"], legacy["preselect"])


def test_the_search_entry_point_passes_the_spec_so_alternatives_carry_their_warnings(monkeypatch):
    import image_search
    from catalog_match import pipeline

    pool = alternatives_pool()
    seen = []

    def find(spec, **kwargs):
        seen.append(spec.sku_key)
        return decide.route(spec, pool, OK, HEALTHY, set())

    monkeypatch.setattr(pipeline, "find_product_image", find)
    result = image_search.search_best_product_image_v2(
        "Almarai Full Fat Milk 1L", "Almarai Full Fat Milk 1L", "Almarai", barcode="6281007000024", category="Dairy",
        skip_cache=True, brand_mappings=MAPPINGS)
    assert seen == [MILK.sku_key]
    by_url = {c["url"]: c["warnings"] for c in result["candidates"]}
    assert "foreign_store" in by_url[pool[1].candidate.image_url]
    assert "social_media" in by_url[pool[3].candidate.image_url]
    assert result["url"] == pool[0].candidate.image_url and result["decision"] == "REVIEW_PRESELECTED"


def test_a_display_warning_failure_never_breaks_the_search_result(monkeypatch):
    pool = alternatives_pool()
    outcome = decide.route(MILK, pool, OK, HEALTHY, set())

    def boom(*args, **kwargs):
        raise RuntimeError("display warnings broke")

    monkeypatch.setattr(facade, "candidate_warnings", boom)
    legacy = facade.outcome_to_legacy(outcome, None, spec=MILK)
    assert legacy["url"] == pool[0].candidate.image_url and len(legacy["candidates"]) == 5


def test_the_worker_saves_each_candidates_warnings_in_its_reasons():
    best = {"url": "https://x/1.jpg", "preselect": True, "candidates": [
        {"url": "https://x/1.jpg", "status": "preselected", "reasons": ["vlm:MATCH", "warn:low_resolution"],
         "warnings": ["low_resolution", "size_unverified"]},
        {"url": "https://x/2.jpg", "status": "eligible", "reasons": ["vlm:UNSURE"],
         "warnings": ["foreign_store", "brand_spelling:Rio Mare"]},
        {"url": "https://x/3.jpg", "status": "eligible", "reasons": [], "warnings": []},
    ]}
    original = copy.deepcopy(best)
    saved = main.collect_candidates(best, {})
    assert saved[0]["reasons"] == ["vlm:MATCH", "warn:low_resolution", "warn:size_unverified"]
    assert saved[1]["reasons"] == ["vlm:UNSURE", "warn:foreign_store", "warn:brand_spelling:Rio Mare"]
    assert saved[2]["reasons"] == []
    assert best == original                       # the search result itself is not touched


def test_every_code_is_listed_for_the_dashboard():
    assert set(decide.DISPLAY_ONLY_WARNING_CODES) <= set(decide.WARNING_CODES)
    assert {"size_unverified", "variant_unverified"} == set(decide.DISPLAY_ONLY_WARNING_CODES)
