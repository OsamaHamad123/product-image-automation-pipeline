"""image_search.search_best_product_image (catalog_match, the only engine), and catalog_match.facade.

The pipeline is stubbed where the test is about the adapter (the outcome it returns is
built from REAL scores of real candidates); one test runs the real pipeline behind the
public function with stub stages. No network, no API key, no database.
"""

import io
import json
import socket
from unittest import mock

import pytest
from PIL import Image, ImageDraw

import google_sheets
import image_search
import local_cache_db
from catalog_match import facade, pipeline, settings
from catalog_match.identity import build_sku_spec
from catalog_match.models import (
    Candidate, FetchedImage, ProviderHealth, ProviderResult, QualityReport, RankedCandidate, SearchOutcome,
    VerificationResult, VlmImageVerdict,
)
from catalog_match.score import score_candidate
from catalog_match.verify import make_verdict

MAPPINGS = {"almarai": {"brand": "Almarai", "synonyms": ["المراعي"], "excluded_competitors": ["Al Ain"]}}
NAME, BRAND = "Almarai Full Fat Fresh Milk 1L", "Almarai"
SPEC = build_sku_spec({"name": NAME, "brand": BRAND}, MAPPINGS)
PACKSHOT = Candidate(image_url="https://cdn.mafrservices.com/108596_main.jpg",
                     page_url="https://www.carrefouruae.com/mafuae/en/almarai-full-fat-fresh-milk-1l/p/108596",
                     title="Buy Almarai Full Fat Fresh Milk 1L Online - Carrefour UAE", domain="carrefouruae.com",
                     provider="serper", query_id="Q1", rank=1)
SIBLING = Candidate(image_url="https://f.nooncdn.com/low-fat.jpg",
                    page_url="https://www.noon.com/uae-en/almarai-low-fat-milk-1l/p/",
                    title="Almarai Low Fat Milk 1L", domain="noon.com", provider="serper", query_id="Q1", rank=2)
BLOG = Candidate(image_url="https://blog.example.org/almarai.jpg", page_url="https://blog.example.org/almarai",
                 title="Almarai milk", provider="serper", query_id="Q1", rank=3)
READ_MATCH = {"brand_text": "Almarai", "variant_text": "Full Fat Milk", "size_text": "1 L", "pack_count": 1,
              "view": "front_packshot", "brand_match": "yes", "variant_match": "yes", "size_match": "yes"}


@pytest.fixture(autouse=True)
def _v2(monkeypatch):
    """No network, no cache hit, no Sheets."""
    def refuse(*args, **kwargs):
        raise AssertionError(f"network access attempted: {args!r}")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    monkeypatch.setattr(settings, "_config", None)
    monkeypatch.setenv("AUTO_PUBLISH_ENABLED", "false")
    monkeypatch.setenv("AUTO_PUBLISH_BRANDS", "")
    monkeypatch.setattr(local_cache_db, "get_cached_product", mock.Mock(return_value=None))
    monkeypatch.setattr(google_sheets, "get_sheets_client", mock.Mock(return_value=object()))
    monkeypatch.setattr(google_sheets, "get_brand_mappings", mock.Mock(return_value=MAPPINGS))


def ranked_row(cand, status, verdict=None, fetched=True, reasons=()):
    fi = FetchedImage(candidate=cand, ok=True, content_sha256="cd" * 32, width=1500, height=1500,
                      path_or_bytes=b"...", phash="0f0f0f0f0f0f0f0f") if fetched else None
    return RankedCandidate(candidate=cand, score=score_candidate(SPEC, cand), fetched=fi,
                           quality=QualityReport(hard_ok=True, soft={"white_border_ratio": 1.0}, quality_score=0.9),
                           verdict=verdict, status=status, reasons=list(reasons))


def preselected_outcome():
    winner = ranked_row(PACKSHOT, "preselected", VlmImageVerdict(index=0, decision="MATCH", brand_text="Almarai",
                                                                 size_text="1 L", view="front_packshot"),
                        reasons=["vlm:MATCH", "preselected:vlm_match"])
    sibling = ranked_row(SIBLING, "rejected", reasons=["hard:variant_conflict:fat"])
    blog = ranked_row(BLOG, "eligible", VlmImageVerdict(index=1, decision="UNSURE"), reasons=["vlm:UNSURE"])
    return SearchOutcome(decision="REVIEW_PRESELECTED", winner=winner, ranked=[winner, blog, sibling],
                         provider_health=[ProviderHealth("serper", "ok", 200)], queries=["Almarai Full Fat Milk 1L"],
                         vlm_calls=1, sku_key=SPEC.sku_key)


# ---------------------------------------------------------------------------
# search_best_product_image (v2 wiring)
# ---------------------------------------------------------------------------

def test_v2_returns_legacy_dict_with_decision(monkeypatch, _v2):
    find = mock.Mock(return_value=preselected_outcome())
    monkeypatch.setattr(pipeline, "find_product_image", find)
    trace = {}
    result = image_search.search_best_product_image(
        f"{NAME} {BRAND}", NAME, BRAND, product_name_ar="حليب المراعي كامل الدسم 1 لتر", brand_ar="المراعي",
        category="Dairy", barcode="", custom_query="almarai red cap milk", exclude_urls=["https://x/y.jpg"],
        exclude_phashes=["00ff00ff00ff00ff"], trace=trace, origin="UAE", strict_brand_match=True)

    for key in ("url", "needs_review", "clip_score", "decision", "sku_key", "preselect", "candidates"):
        assert key in result, key
    assert result["url"] == PACKSHOT.image_url and result["preselect"] is True
    assert result["needs_review"] is True and result["clip_score"] is None
    assert result["decision"] == "REVIEW_PRESELECTED" and result["sku_key"] == SPEC.sku_key
    assert result["width"] == 1500 and result["source"] == "serper" and result["page_url"] == PACKSHOT.page_url
    # the pipeline received the full row and the human-loop inputs
    (spec,), kwargs = find.call_args
    assert spec.name_ar == "حليب المراعي كامل الدسم 1 لتر" and spec.brand_conf == "mapped"
    assert spec.category == "Dairy"
    assert kwargs["custom_query"] == "almarai red cap milk"
    assert list(kwargs["exclude_urls"]) == ["https://x/y.jpg"]
    assert list(kwargs["exclude_phashes"]) == ["00ff00ff00ff00ff"]
    # the v1 Gemini brand alignment is gone with the v1 engine: the sheet's brand is the brand (D8)
    assert not hasattr(google_sheets, "align_brand_via_gemini")
    google_sheets.get_brand_mappings.assert_called_once()     # mappings were not passed: loaded once
    # staff steering (custom query / exclusions) must run the search, never serve the cache
    local_cache_db.get_cached_product.assert_not_called()


def test_v2_trace_candidates_carry_status_reasons_evidence_scores(monkeypatch):
    monkeypatch.setattr(pipeline, "find_product_image", mock.Mock(return_value=preselected_outcome()))
    trace = {}
    image_search.search_best_product_image(NAME, NAME, BRAND, trace=trace, brand_mappings=MAPPINGS)

    google_sheets.get_brand_mappings.assert_not_called()      # passed in: not fetched again
    assert trace["outcome"]["decision"] == "REVIEW_PRESELECTED"
    assert trace["outcome"]["provider_health"][0]["provider"] == "serper"
    assert trace["outcome"]["sku_key"] == SPEC.sku_key
    step = trace["steps"][-1]
    assert step["step_name"] and step["name"] and step["query"] == "Almarai Full Fat Milk 1L"
    cands = {c["url"]: c for c in step["candidates"]}
    assert set(cands) == {PACKSHOT.image_url, SIBLING.image_url, BLOG.image_url}
    for c in cands.values():
        for key in ("status", "reasons", "evidence", "scores", "page_url", "domain", "vlm"):
            assert key in c, key
        assert c["scores"]["relevance_score"] == c["scores"]["identity_score"]
    win = cands[PACKSHOT.image_url]
    assert win["status"] == "preselected" and "preselected:vlm_match" in win["reasons"]
    assert win["evidence"]["tier"] == 1 and win["evidence"]["size"] == "match"
    assert win["evidence"]["source_class"] == "uae_retailer"
    assert win["scores"]["relevance_score"] > 0 and win["vlm"]["decision"] == "MATCH"
    sib = cands[SIBLING.image_url]
    assert sib["status"] == "rejected" and sib["evidence"]["hard_reject"] == ["variant_conflict:fat"]
    json.dumps(trace, ensure_ascii=False)                     # the CLI prints it as JSON


def test_v2_not_found_returns_none_and_records_failure(monkeypatch):
    outcome = SearchOutcome(decision="NOT_FOUND", failure_code="NO_RESULTS", ranked=[],
                            provider_health=[ProviderHealth("serper", "empty", 200)], queries=["q1", "q2"],
                            sku_key=SPEC.sku_key)
    monkeypatch.setattr(pipeline, "find_product_image", mock.Mock(return_value=outcome))
    trace = {}
    assert image_search.search_best_product_image(NAME, NAME, BRAND, trace=trace) is None
    assert trace["outcome"]["failure_code"] == "NO_RESULTS"
    assert trace["outcome"]["decision"] == "NOT_FOUND"
    assert trace["outcome"]["queries"] == ["q1", "q2"]


def test_v2_cache_hit_is_review_only_and_skip_cache(monkeypatch):
    local_cache_db.get_cached_product.return_value = {"cloudinary_url": "https://res.cloudinary.com/x/1.png",
                                                      "clip_score": 1.0, "metadata": {"a": 1}}
    find = mock.Mock(return_value=preselected_outcome())
    monkeypatch.setattr(pipeline, "find_product_image", find)
    trace = {}
    hit = image_search.search_best_product_image(NAME, NAME, BRAND, barcode="6281007000000", trace=trace)
    assert hit["source"] == "sqlite_cache" and hit["needs_review"] is True
    assert hit["decision"] == "REVIEW_PRESELECTED" and hit["clip_score"] is None
    assert hit["url"] == "https://res.cloudinary.com/x/1.png"
    assert hit["sku_key"] == "06281007000000"
    assert trace["outcome"]["decision"] == "REVIEW_PRESELECTED"
    find.assert_not_called()
    kwargs = local_cache_db.get_cached_product.call_args.kwargs
    assert kwargs["barcode"] == "6281007000000" and kwargs["product_name"] == NAME

    local_cache_db.get_cached_product.reset_mock()
    result = image_search.search_best_product_image(NAME, NAME, BRAND, barcode="6281007000000", skip_cache=True)
    local_cache_db.get_cached_product.assert_not_called()
    assert result["source"] == "serper"
    find.assert_called_once()


def test_no_engine_switch_catalog_match_always_runs(monkeypatch):
    """SEARCH_ENGINE is gone: a leftover SEARCH_ENGINE=v1 in .env (or the settings table) changes nothing."""
    monkeypatch.setenv("SEARCH_ENGINE", "v1")
    find = mock.Mock(return_value=preselected_outcome())
    monkeypatch.setattr(pipeline, "find_product_image", find)
    result = image_search.search_best_product_image("q", NAME, BRAND, barcode="", brand_mappings=MAPPINGS)
    find.assert_called_once()
    assert result["decision"] == "REVIEW_PRESELECTED" and result["url"] == PACKSHOT.image_url
    assert "SEARCH_ENGINE" not in settings.DEFAULTS and not hasattr(settings, "search_engine")
    for name in ("search_best_product_image_v1", "search_best_product_image_v2", "evaluate_and_choose_best_image"):
        assert not hasattr(image_search, name), name


def test_v2_real_pipeline_behind_public_function(monkeypatch):
    """The real pipeline with stub stages: the public function returns the verified packshot."""
    img = Image.new("RGB", (900, 900), "white")
    ImageDraw.Draw(img).rectangle([250, 100, 650, 800], fill=(27, 94, 32))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    body = buf.getvalue()

    class Serper:
        name, sanctioned, kind, fallback = "serper", True, "search", False

        def search(self, query, hl, spec):
            return ProviderResult(provider="serper", status="ok", http_status=200,
                                  candidates=[SIBLING, PACKSHOT])

    class Fetcher:
        def fetch(self, cands, spec):
            return [FetchedImage(candidate=c, ok=True, content_sha256=str(i) * 64, width=900, height=900,
                                 path_or_bytes=body, phash=f"{i:016x}") for i, c in enumerate(cands)]

    class Verifier:
        def verify(self, spec, images):
            return VerificationResult(status="ok", calls=1, verdicts=[
                make_verdict(spec, i, READ_MATCH if f.candidate is PACKSHOT or
                             f.candidate.image_url == PACKSHOT.image_url else {}) for i, f in enumerate(images)])

    monkeypatch.setattr(pipeline, "_default_providers", lambda: [Serper()])
    monkeypatch.setattr(pipeline, "_default_fetcher", Fetcher)
    monkeypatch.setattr(pipeline, "_default_verifier", Verifier)
    trace = {}
    result = image_search.search_best_product_image(NAME, NAME, BRAND, trace=trace, brand_mappings=MAPPINGS)
    assert result["url"] == PACKSHOT.image_url and result["decision"] == "REVIEW_PRESELECTED"
    assert result["clip_score"] is None and result["needs_review"] is True
    statuses = {c["url"]: c["status"] for c in trace["steps"][-1]["candidates"]}
    assert statuses[PACKSHOT.image_url] == "preselected" and statuses[SIBLING.image_url] == "rejected"


# ---------------------------------------------------------------------------
# facade unit behaviour
# ---------------------------------------------------------------------------

def test_to_sku_row_maps_legacy_kwargs():
    row = facade.to_sku_row(" Laban 180ml ", "Al Rawabi", {"product_name_ar": "لبن", "brand_ar": "الروابي",
                                                           "barcode": 6291234567895, "category": "Dairy",
                                                           "size_text": "180 ml"})
    assert row == {"name": "Laban 180ml", "name_ar": "لبن", "brand": "Al Rawabi", "brand_ar": "الروابي",
                   "barcode": "6291234567895", "category": "Dairy", "size": "180 ml"}


def test_review_unselected_claims_no_pick():
    blog = ranked_row(BLOG, "eligible", VlmImageVerdict(index=0, decision="UNSURE"))
    rejected = ranked_row(PACKSHOT, "rejected", VlmImageVerdict(index=1, decision="MISMATCH"),
                          reasons=["vlm:MISMATCH"])
    outcome = SearchOutcome(decision="REVIEW_UNSELECTED", failure_code=None, ranked=[rejected, blog],
                            sku_key=SPEC.sku_key)
    result = facade.outcome_to_legacy(outcome, {})
    assert result["url"] is None and result["preselect"] is False and result["needs_review"] is True
    assert result["decision"] == "REVIEW_UNSELECTED"
    assert [c["status"] for c in result["candidates"]] == ["rejected", "eligible"]
    assert not any(c["status"] == "preselected" for c in result["candidates"])


def test_no_reviewable_candidates_and_provider_down_return_none():
    only_conflicts = SearchOutcome(decision="REVIEW_UNSELECTED", ranked=[ranked_row(SIBLING, "rejected")],
                                   sku_key="k")
    assert facade.outcome_to_legacy(only_conflicts) is None
    trace = {}
    down = SearchOutcome(decision="PROVIDER_DOWN", failure_code="PROVIDER_DOWN",
                         provider_health=[ProviderHealth("bing_html", "blocked", 200, error="captcha")], sku_key="k")
    assert facade.outcome_to_legacy(down, trace) is None
    assert trace["outcome"]["failure_code"] == "PROVIDER_DOWN"
    assert trace["outcome"]["provider_health"][0]["status"] == "blocked"


def test_auto_publish_is_the_only_no_review_result():
    out = preselected_outcome()
    out.decision = "AUTO_PUBLISH"
    result = facade.outcome_to_legacy(out)
    assert result["needs_review"] is False and result["decision"] == "AUTO_PUBLISH" and result["preselect"]
    assert len(result["candidates"]) <= facade.LEGACY_TOP_N


@pytest.mark.parametrize("steer", [{"custom_query": "almarai red cap"}, {"exclude_urls": ["https://x/y.jpg"]},
                                   {"exclude_phashes": ["00ff00ff00ff00ff"]}])
def test_v2_steering_bypasses_cache(monkeypatch, steer):
    local_cache_db.get_cached_product.return_value = {"cloudinary_url": "https://res.cloudinary.com/x/1.png"}
    find = mock.Mock(return_value=preselected_outcome())
    monkeypatch.setattr(pipeline, "find_product_image", find)
    result = image_search.search_best_product_image(NAME, NAME, BRAND, barcode="6281007000000", **steer)
    local_cache_db.get_cached_product.assert_not_called()
    find.assert_called_once()
    assert result["source"] == "serper"


def test_v2_invalid_barcode_is_not_a_cache_key(monkeypatch):
    local_cache_db.get_cached_product.return_value = None
    monkeypatch.setattr(pipeline, "find_product_image", mock.Mock(return_value=preselected_outcome()))
    image_search.search_best_product_image(NAME, NAME, BRAND, barcode="6.29E+12")
    assert local_cache_db.get_cached_product.call_args.kwargs["barcode"] == ""


def test_outcome_summary_is_stamped_with_the_search_time():
    from datetime import datetime, timezone

    from catalog_match.facade import outcome_summary
    from catalog_match.models import SearchOutcome

    stamp = outcome_summary(SearchOutcome(decision="NOT_FOUND"))["searched_at"]
    when = datetime.fromisoformat(stamp)
    assert when.tzinfo is not None and abs((datetime.now(timezone.utc) - when).total_seconds()) < 60


def test_outcome_summary_keeps_the_social_posts_for_the_reviewer():
    # SOCIAL_ONLY rows had their post links in the outcome but not in the production trace
    from catalog_match.facade import outcome_summary
    from catalog_match.models import SearchOutcome

    links = ["https://www.instagram.com/p/abc/", "https://www.facebook.com/kabani/photos/1"]
    out = SearchOutcome(decision="REVIEW_UNSELECTED", failure_code="SOCIAL_ONLY", social_links=list(links))
    assert outcome_summary(out)["social_links"] == links
    assert outcome_summary(SearchOutcome(decision="NOT_FOUND"))["social_links"] == []


def test_the_stored_outcome_keeps_its_own_top_list_with_provenance():
    """The worker stores only trace['outcome'] for a published or reviewed row, and a reviewer's approval deletes the
    review candidates: the outcome keeps the top reviewable candidates (provider, query id, pHash, evidence) so the run
    export can still say where the pick came from. Hard-rejected listings are not reviewable and stay out."""
    trace = {}
    facade.outcome_to_legacy(preselected_outcome(), trace=trace, spec=SPEC)
    top = trace["outcome"]["top"]
    assert [c["url"] for c in top] == [PACKSHOT.image_url, BLOG.image_url]
    first = top[0]
    assert (first["provider"], first["query_id"], first["phash"], first["status"]) == \
        ("serper", "Q1", "0f0f0f0f0f0f0f0f", "preselected")
    assert first["evidence"]["tier"] == 1 and first["vlm"]["decision"] == "MATCH"
    assert "quality" not in first and "scores" not in first          # the heavy blocks stay in the full trace only
    json.dumps(trace["outcome"])                                     # plain JSON for trace_json
    # a pick ranked below the first eight is put in front
    out = preselected_outcome()
    fillers = [ranked_row(Candidate(image_url=f"https://cdn.x.ae/{i}.jpg", page_url=f"https://x.ae/p/{i}",
                                    title="Almarai Full Fat Fresh Milk 1L", provider="serper", query_id="Q3"),
                          "eligible") for i in range(9)]
    out.ranked = fillers + out.ranked
    trace = {}
    facade.outcome_to_legacy(out, trace=trace, spec=SPEC)
    assert len(trace["outcome"]["top"]) == facade.LEGACY_TOP_N
    assert trace["outcome"]["top"][0]["url"] == PACKSHOT.image_url
