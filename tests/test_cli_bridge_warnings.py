"""cli_bridge search response: every candidate exposes its review 'warnings' (codes without 'warn:')."""

import pytest

pytestmark = pytest.mark.usefixtures("offline")

EXISTING_KEYS = {"url", "title", "page_url", "domain", "status", "reasons", "evidence", "vlm", "scores", "width",
                 "height", "content_sha256"}


@pytest.fixture
def bridge(offline, monkeypatch, tmp_path):
    import cli_bridge
    import google_sheets
    import local_cache_db

    monkeypatch.setattr(cli_bridge, "LOG_PATH", str(tmp_path / "search.log"))
    monkeypatch.setattr(google_sheets, "get_sheets_client", lambda: object())
    monkeypatch.setattr(google_sheets, "get_brand_mappings", lambda *a, **k: {})
    monkeypatch.setattr(local_cache_db, "get_task_by_row", lambda row: None)
    monkeypatch.setattr(local_cache_db, "get_rejections", lambda sku: ([], []))
    return cli_bridge


def _routed_pick():
    """A real decide.route outcome: Sunbulah Thin French Fries from Carrefour Kuwait, 450 px (live rows 4 and 11)."""
    from catalog_match import decide
    from catalog_match.identity import build_sku_spec
    from catalog_match.models import (
        Candidate, FetchedImage, ProviderHealth, QualityReport, RankedCandidate, VerificationResult,
        VlmImageVerdict,
    )
    from catalog_match.score import score_candidate

    spec = build_sku_spec({"name": "SUNBULAH FRENCH FRIES 1KG", "brand": "SUNBULAH"}, {})

    def ranked(cand, size, verdict):
        fi = FetchedImage(candidate=cand, ok=True, content_sha256="ab" * 32, width=size, height=size,
                          path_or_bytes=b"x")
        return RankedCandidate(candidate=cand, score=score_candidate(spec, cand), fetched=fi,
                               quality=QualityReport(hard_ok=True, quality_score=0.7), verdict=verdict)

    pick = ranked(Candidate(image_url="https://cdnprod.mafretailproxy.com/sys-master-root/sunbulah-thin.jpg",
                            page_url="https://www.carrefour.com.kw/mafkwt/en/sunbulah-thin-french-fries-1kg/p/1",
                            title="Sunbulah Thin French Fries 1 kg", provider="serper", rank=1),
                  450, VlmImageVerdict(index=0, brand_text="Sunbulah", variant_text="Thin French Fries",
                                       size_text="1 kg", view="front_packshot", brand_match="yes",
                                       variant_match="yes", size_match="yes", decision="MATCH"))
    other = ranked(Candidate(image_url="https://img.example.org/fries.jpg", page_url="https://blog.example.org/f",
                             title="Crispy fries", provider="serper", rank=2), 900,
                   VlmImageVerdict(index=1, decision="UNSURE"))
    return spec, decide.route(spec, [pick, other], VerificationResult(status="ok", calls=1),
                              [ProviderHealth("serper", "ok", 200)], set())


def test_search_response_lists_the_pick_warnings(bridge, monkeypatch):
    import image_search
    from catalog_match import facade

    spec, outcome = _routed_pick()
    assert outcome.decision == "REVIEW_PRESELECTED"

    def fake_search(query, name, brand, **kwargs):
        return facade.outcome_to_legacy(outcome, kwargs["trace"])

    monkeypatch.setattr(image_search, "search_best_product_image", fake_search)
    response = bridge.action_search({"product_name": spec.raw_name, "brand": spec.brand_raw})

    assert response["status"] == "review" and response["decision"] == "REVIEW_PRESELECTED"
    pick, other = response["candidates"]
    assert pick["status"] == "preselected"
    assert pick["warnings"] == ["sheet_silent:fries_cut=thin", "low_resolution", "foreign_store"]
    assert other["warnings"] == []
    # the existing keys are unchanged: the reasons still carry the prefixed codes
    assert set(pick) == EXISTING_KEYS | {"warnings"}
    assert [r for r in pick["reasons"] if r.startswith("warn:")] == [
        "warn:sheet_silent:fries_cut=thin", "warn:low_resolution", "warn:foreign_store"]
    assert response["selected_image"]["url"] == pick["url"]


def test_trace_or_cached_candidates_without_a_warnings_key(bridge):
    # Candidates from the trace fallback (or an older engine) have reasons only.
    rows = bridge._candidates_for_response(None, {"steps": [{"candidates": [
        {"url": "https://a.ae/1.jpg", "status": "preselected", "reasons": ["vlm:UNSURE", "warn:vlm_unsure"]},
        {"url": "https://a.ae/2.jpg", "reasons": ["tier T2"]},
        {"url": "https://a.ae/3.jpg"},
    ]}]})
    assert [r["warnings"] for r in rows] == [["vlm_unsure"], [], []]
    assert rows[0]["reasons"] == ["vlm:UNSURE", "warn:vlm_unsure"]
    # a 'warnings' list sent by the engine wins over the reasons
    row = bridge._serialize_candidate({"url": "https://a.ae/4.jpg", "reasons": ["warn:x"],
                                       "warnings": ["foreign_store"]})
    assert row["warnings"] == ["foreign_store"]
