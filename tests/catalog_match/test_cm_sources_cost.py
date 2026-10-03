"""Cost of the expansion sources in ops_health (sources package).

Serper bills every endpoint (images, web search, shopping, lens) per answered call;
SerpApi Google Lens is priced from SERPAPI_LENS_PRICE_USD. Refused or failed calls are
not billed. The outcome shape is the one catalog_match.facade writes, built here both
by hand and from a real expansion round.
"""

import json
from datetime import datetime, timezone

import pytest

import ops_health
from catalog_match import facade, settings
from catalog_match.models import ProviderHealth, SearchOutcome

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    monkeypatch.setattr(settings, "_config", None)
    monkeypatch.delenv("SERPAPI_LENS_PRICE_USD", raising=False)


def h(provider, status, query_id, http=200):
    return {"provider": provider, "status": status, "http_status": http, "latency_ms": 300,
            "error": None if status in ("ok", "empty") else f"http_{http}", "query_id": query_id}


def row(providers, vlm_calls=0, age_s=600):
    out = {"decision": "REVIEW_PRESELECTED", "failure_code": None, "provider_health": providers, "queries": [],
           "sku_key": "k", "vlm_calls": vlm_calls, "reject_counts": {}, "winner_url": None}
    return {"status": "ready_for_review", "failure_code": None, "has_trace": 1, "age_s": age_s,
            "outcome_json": json.dumps(out)}


EXPANDED = [h("serper", "ok", "Q1"), h("serper", "empty", "Q3"), h("serper_web", "ok", "X1"),
            h("serper_shopping", "empty", "X2"), h("lens_serper", "error", "X3", 404),
            h("lens_serpapi", "ok", "X3")]


def test_every_paid_source_is_priced_per_answered_call(monkeypatch):
    monkeypatch.setenv("SERPAPI_LENS_PRICE_USD", "0.02")
    report = ops_health.summarize([row(EXPANDED, vlm_calls=2), row([h("serper", "ok", "Q1")], vlm_calls=1)],
                                  now=NOW)
    day = report["windows"]["24h"]
    # Serper: images x3 + web + shopping (the refused lens call is not billed)
    assert day["serper_queries"] == 5
    assert day["sources"] == {
        "lens_serpapi": {"calls": 1, "cost_usd": 0.02},
        "serper": {"calls": 3, "cost_usd": 0.003},
        "serper_shopping": {"calls": 1, "cost_usd": 0.001},
        "serper_web": {"calls": 1, "cost_usd": 0.001},
    }
    assert day["cost_usd"] == {"serper": 0.005, "gemini": 0.003, "serpapi": 0.02, "total": 0.028}
    # the per-source counts the health page lists
    assert day["providers"]["serper_web"]["ok"] == 1 and day["providers"]["lens_serper"]["error"] == 1
    assert report["source_prices"] == {"serper": 0.001, "serper_web": 0.001, "serper_shopping": 0.001,
                                       "lens_serper": 0.001, "lens_serpapi": 0.02}
    # the existing price block is unchanged for the dashboard
    assert report["prices"] == {"serper_per_query": 0.001, "gemini_per_call": 0.001}


def test_without_serpapi_the_cost_block_keeps_its_shape():
    day = ops_health.summarize([row([h("serper", "ok", "Q1"), h("serper_web", "ok", "X1")], vlm_calls=1)],
                               now=NOW)["windows"]["24h"]
    assert day["cost_usd"] == {"serper": 0.002, "gemini": 0.001, "total": 0.003}


def test_bad_price_setting_falls_back(monkeypatch):
    monkeypatch.setenv("SERPAPI_LENS_PRICE_USD", "cheap")
    assert ops_health.source_prices()["lens_serpapi"] == 0.015


def test_outcome_written_by_the_facade_is_priced():
    outcome = SearchOutcome(decision="NOT_FOUND", failure_code="NO_RESULTS", provider_health=[
        ProviderHealth(provider="serper", status="empty", http_status=200, query_id="Q1"),
        ProviderHealth(provider="serper_web", status="ok", http_status=200, query_id="X1"),
        ProviderHealth(provider="serper_shopping", status="quota", http_status=429, query_id="X2"),
    ])
    trace = {}
    facade.outcome_to_legacy(outcome, trace)
    raw = {"status": "failed", "failure_code": "NO_RESULTS", "has_trace": 1, "age_s": 60,
           "outcome_json": json.dumps(trace["outcome"])}
    day = ops_health.summarize([raw])["windows"]["24h"]
    assert day["sources"] == {"serper": {"calls": 1, "cost_usd": 0.001},
                              "serper_web": {"calls": 1, "cost_usd": 0.001}}
    assert day["cost_usd"]["total"] == 0.002
