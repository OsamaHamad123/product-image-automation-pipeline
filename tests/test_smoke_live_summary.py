"""The dry-run summary of scripts/smoke_live.py: the same numbers for every run, so runs compare.

tests/fixtures/smoke_runs/run_before.json is shaped like the owner's live output of 2026-09-30 (the older
format: a plain list of rows with the documented fields; two rows hit a local internet outage, one row
crashed). run_after.json is the same rows in this version's format (meta, summary, rows) with the
expansion round, a strong second look and per-model usage. The expected numbers below were counted by
hand from the rows.
"""

import importlib.util
import json
import types
from pathlib import Path

import pytest

from catalog_match.models import (
    Candidate, CandidateScore, FetchedImage, ProviderHealth, ProviderResult, RankedCandidate, SearchOutcome,
    VerificationResult, VlmImageVerdict,
)

REPO = Path(__file__).resolve().parents[1]
RUNS = REPO / "tests" / "fixtures" / "smoke_runs"


def _load_script():
    spec = importlib.util.spec_from_file_location("smoke_live_summary_under_test", REPO / "scripts" / "smoke_live.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def script():
    return _load_script()


def test_summary_of_a_run_in_the_older_list_format(script):
    run = script.load_run(RUNS / "run_before.json")
    assert run["format"] == "smoke_live/1" and len(run["rows"]) == 13
    s = script.summarize(run["rows"])
    assert (s["rows"], s["outage_rows"], s["errors"], s["measured"]) == (13, 2, 1, 10)
    assert s["outage_row_numbers"] == [20, 21] and s["error_row_numbers"] == [55]
    assert (s["preselected"], s["auto_publish"], s["coverage_pct"]) == (4, 0, 40.0)
    assert s["unselected_rows"] == {"verifier_mismatch": [3, 15], "unsure": [14, 61], "download_failed": [],
                                    "only_social": [30], "brand_not_found": [], "not_found": [50],
                                    "provider_down": [], "verifier_down": [], "weak_only": []}
    assert sum(s["unselected"].values()) == s["measured"] - s["preselected"]
    assert s["winner_providers"] == {"serper": 4}
    assert s["winner_domains"] == {"luluhypermarket.com": 1, "carrefouruae.com": 1, "amazon.ae": 1, "noon.com": 1}
    assert s["expansion"] == {"rows": 0, "expand": 0, "upgrade": 0, "calls": 0, "winners": 0}
    assert (s["strong_calls"], s["vlm_calls"], s["search_calls"]) == (0, 10, 30)
    # 12 rows ran (the crashed one has no cost): 8 x $0.004 + $0.005 + $0.003 + 2 outages x $0;
    # per product is over the 10 measured rows (the outage rows are not products the run measured)
    assert s["cost_usd"] == {"total": 0.04, "per_product": 0.004, "search": None, "verifier": None}
    assert s["warnings"] == {"low_resolution": 1, "vlm_unsure": 1} and s["picks_with_warnings"] == 2
    assert s["decisions"] == {"ERROR": 1, "NOT_FOUND": 1, "PROVIDER_DOWN": 2, "REVIEW_PRESELECTED": 4,
                              "REVIEW_UNSELECTED": 5}


def test_summary_of_a_run_in_this_format(script):
    run = script.load_run(RUNS / "run_after.json")
    s = script.summarize(run["rows"])
    assert run["summary"] == s
    assert (s["rows"], s["outage_rows"], s["errors"], s["measured"]) == (13, 0, 0, 13)
    assert (s["preselected"], s["coverage_pct"]) == (8, 61.5)
    assert {k: v for k, v in s["unselected_rows"].items() if v} == {
        "verifier_mismatch": [15], "unsure": [21], "only_social": [30], "not_found": [50],
        "brand_not_found": [55]}       # its one listing does not name the brand (tier 3)
    assert s["winner_providers"] == {"serper": 6, "page": 1, "lens_serper": 1}
    assert s["winner_domains"]["luluhypermarket.com"] == 3
    # rows 3 and 15 ran an expansion round (2 calls each), row 40 an upgrade (1 visual search);
    # the picks of rows 3 (a page image) and 40 (the larger copy) came from it
    assert s["expansion"] == {"rows": 3, "expand": 2, "upgrade": 1, "calls": 5, "winners": 2}
    assert (s["strong_calls"], s["strong_rows"], s["vlm_calls"], s["search_calls"]) == (2, 2, 15, 44)
    assert s["models"] == {"gemini:gemini-3.1-flash-lite": {"calls": 13, "usd": 0.0049, "strong": 0},
                           "gemini:gemini-3.5-flash": {"calls": 2, "usd": 0.004, "strong": 2}}
    assert s["cost_usd"] == {"total": 0.0529, "per_product": 0.0041, "search": 0.044, "verifier": 0.0089}
    assert s["warnings"] == {"vlm_unsure": 1} and s["picks_with_warnings"] == 1


def test_printed_summary_names_every_reason_and_the_cost(script):
    s = script.summarize(script.load_run(RUNS / "run_after.json")["rows"])
    text = script.format_summary(s)
    assert "pre-selected 8/13 = 61.5% (auto-publish 0)" in text
    for _key, label in script.UNSELECTED_REASONS:
        assert label in text
    assert "label reader saw another product" in text and "rows 15" in text
    assert "expansion rounds: 3 rows (expand 2, upgrade 1), 5 paid calls, 2 picks came from it" in text
    assert "strong model: 2 calls on 2 rows" in text
    assert ("estimated cost $0.0529 total, $0.0041 per measured product (search $0.0440, label reading $0.0089)"
            in text)
    old = script.format_summary(script.summarize(script.load_run(RUNS / "run_before.json")["rows"]))
    assert "outage rows excluded 2 (rows 20, 21)" in old and "crashed rows excluded 1 (rows 55)" in old


def test_empty_run_has_no_coverage(script):
    s = script.summarize([])
    assert s["rows"] == 0 and s["coverage_pct"] is None and s["cost_usd"]["per_product"] is None
    assert "pre-selected 0/0 = -" in script.format_summary(s)


# ---------------------------------------------------------------------------
# Row classification
# ---------------------------------------------------------------------------

def _call(status, http, error, provider="serper", query="q"):
    return {"provider": provider, "query": query, "status": status, "http_status": http, "error": error}


def test_an_outage_is_a_failure_without_any_http_answer(script):
    down = {"row": 1, "decision": "PROVIDER_DOWN", "provider_calls": [
        _call("error", None, "ConnectionError: Max retries exceeded"), _call("error", None, "timeout"),
        _call("ok", 200, None, provider="off", query="lookup")]}
    assert script.outage_reason(down) == "no connection to the search service"
    refused = dict(down, provider_calls=[_call("quota", 429, "http_429"), _call("error", None, "timeout")])
    assert script.outage_reason(refused) is None
    assert script.unselected_reason(refused) == "provider_down"
    assert script.outage_reason({"row": 2, "error": "ConnectionError: [Errno 11001] getaddrinfo failed"})
    assert script.outage_reason({"row": 3, "error": "KeyError: 'size'"}) is None


def test_a_label_reader_outage_needs_connection_errors(script):
    row = {"row": 4, "decision": "REVIEW_UNSELECTED", "failure_code": "VERIFIER_DOWN",
           "verifier_calls": [{"status": "unknown", "error": "connection_error:ConnectionError", "calls": 1},
                              {"status": "unknown", "error": "circuit_open", "calls": 0}]}
    assert script.outage_reason(row) == "no connection to the label reader"
    quota = dict(row, verifier_calls=[{"status": "unknown", "error": "http_429", "calls": 1}])
    assert script.outage_reason(quota) is None and script.unselected_reason(quota) == "verifier_down"
    older = dict(row)
    older.pop("verifier_calls")         # older files: no verifier record, so never an outage
    assert script.outage_reason(older) is None


def test_reasons_in_their_order(script):
    base = {"row": 5, "decision": "REVIEW_UNSELECTED", "failure_code": None, "top": [], "reject_counts": {}}
    assert script.unselected_reason(dict(base, decision="NOT_FOUND")) == "not_found"
    assert script.unselected_reason(dict(base, failure_code="DOWNLOAD_FAILED")) == "download_failed"
    assert script.unselected_reason(dict(base, verdicts={"UNSURE": 1, "MISMATCH": 2})) == "unsure"
    # the verifier read another brand on a tier-1 candidate: the UNSURE fallback was refused
    refuted = dict(base, verdicts={"UNSURE": 1}, reject_counts={"vlm:tier1_brand_refuted": 1})
    assert script.unselected_reason(refuted) == "verifier_mismatch"
    assert script.unselected_reason(dict(base, verdicts={"MISMATCH": 1})) == "verifier_mismatch"
    assert script.unselected_reason(dict(base, verdicts={})) == "weak_only"
    social = dict(base, verdicts={"UNSURE": 1}, only_social=True)
    assert script.unselected_reason(social) == "only_social"
    # as decide.route: with no pick, a label reader that did not answer comes first (was 'only_social')
    assert script.unselected_reason(dict(social, failure_code="VERIFIER_DOWN")) == "verifier_down"
    assert script.unselected_reason(dict(base, failure_code="SOCIAL_ONLY", only_social=False)) == "only_social"


def test_only_social_from_the_top_list_of_an_older_file(script):
    top = [{"status": "eligible", "reasons": [], "image_url": "https://scontent.cdninstagram.com/1.jpg",
            "page_url": "https://www.instagram.com/p/1", "domain": "instagram.com"},
           {"status": "rejected", "reasons": ["hard:brand_conflict"], "image_url": "https://shop.ae/2.jpg",
            "page_url": "https://shop.ae/p/2", "domain": "shop.ae"}]
    row = {"row": 6, "decision": "REVIEW_UNSELECTED", "top": top, "reject_counts": {}}
    assert script.unselected_reason(row) == "only_social"
    top.append({"status": "eligible", "reasons": [], "image_url": "https://cdn.lulu.ae/3.jpg", "page_url": "",
                "domain": "luluhypermarket.com"})
    assert script.unselected_reason(row) == "weak_only"


# ---------------------------------------------------------------------------
# One row through run_row: production verifier, the expansion round, usage and cost
# ---------------------------------------------------------------------------

class _Spec(types.SimpleNamespace):
    pass


def _ranked(url, provider, domain, decision, status, query_id="Q1", tier=1, reasons=()):
    cand = Candidate(image_url=url, page_url=f"https://{domain}/p", domain=domain, title="Aida French Fries 1kg",
                     provider=provider, query_id=query_id)
    return RankedCandidate(candidate=cand, score=CandidateScore(tier=tier),
                           fetched=FetchedImage(candidate=cand, ok=True, width=1200, height=1200),
                           verdict=VlmImageVerdict(index=0, decision=decision), status=status, reasons=list(reasons))


class _Verifier:
    def __init__(self):
        self.seen = 0

    def verify(self, spec, images):
        self.seen += 1
        usage = [{"role": "primary", "provider": "gemini", "model": "gemini-3.1-flash-lite", "usd": 0.0004}]
        if self.seen == 2:
            usage.append({"role": "strong", "provider": "gemini", "model": "gemini-3.5-flash", "usd": 0.002})
        return VerificationResult(status="ok", calls=len(usage), usage=usage) if _has_usage() else \
            VerificationResult(status="ok", calls=len(usage))


def _has_usage():
    return "usage" in VerificationResult.__dataclass_fields__


class _Provider:
    name, sanctioned = "serper", True

    def search(self, query, hl, spec):
        return ProviderResult(provider="serper", status="ok", http_status=200,
                              candidates=[Candidate(image_url="https://x.ae/1.jpg")])


def _modules(seen, with_expansion):
    verifier = _Verifier()
    winner = _ranked("https://page.luluhypermarket.com/big.jpg", "page", "luluhypermarket.com", "MATCH", "preselected",
                     query_id="X1", reasons=["vlm:MATCH", "preselected:vlm_match", "warn:low_resolution"])
    other = _ranked("https://f.nooncdn.com/2.jpg", "serper", "noon.com", "MISMATCH", "rejected",
                    reasons=["vlm:MISMATCH"])

    def find_product_image(spec, *, providers=None, verifier=None, **kwargs):
        seen["kwargs"] = kwargs
        seen["verifier"] = verifier
        for p in providers:
            p.search("Aida French Fries 1kg", "en", spec)
        verifier.verify(spec, [])
        verifier.verify(spec, [])
        health = [ProviderHealth(provider="serper", status="ok", http_status=200, query_id="Q1"),
                  ProviderHealth(provider="serper_web", status="ok", http_status=200, query_id="X1"),
                  ProviderHealth(provider="serper_shopping", status="empty", http_status=200, query_id="X2"),
                  ProviderHealth(provider="lens_serpapi", status="error", http_status=None, query_id="X3",
                                 error="SerpApiTransportError: api_key=[REDACTED] timed out")]
        return SearchOutcome(decision="REVIEW_PRESELECTED", winner=winner, ranked=[other, winner],
                             provider_health=health, queries=["Aida French Fries 1kg"], vlm_calls=3)

    if not with_expansion:
        def find_product_image(spec, *, providers=None, verifier=None, _f=find_product_image):  # noqa: F811
            return _f(spec, providers=providers, verifier=verifier)

    pipeline = types.SimpleNamespace(find_product_image=find_product_image, _default_verifier=lambda: verifier)
    identity = types.SimpleNamespace(build_sku_spec=lambda fields, mappings: _Spec(
        sku_key="aida-fries-1000g", brand_conf="sheet_raw", gtin_status="missing", variants={}))
    providers = types.SimpleNamespace(default_providers=lambda: [_Provider()])
    verify = types.SimpleNamespace(GeminiVerifier=lambda: pytest.fail("the production verifier must be used"))
    return identity, pipeline, providers, verify, verifier


def test_run_row_uses_the_production_verifier_and_asks_for_the_expansion_round(script):
    seen = {}
    identity, pipeline, providers, verify, verifier = _modules(seen, with_expansion=True)
    row = {"row_number": 2, "name": "AIDA FRENCH FRIES 1KG", "brand": "AIDA"}
    r = script.run_row(row, {}, identity, pipeline, providers, verify, 0.001, 0.001, expansion=True)
    assert seen["kwargs"] == {"expansion": True}
    assert seen["verifier"]._inner is verifier
    # normal Serper query + the round's answered calls (web ok, shopping empty); the failed SerpApi call is free
    assert [c["provider"] for c in r["provider_calls"]] == ["serper", "serper_web", "serper_shopping", "lens_serpapi"]
    assert r["serp_calls"] == 3
    assert r["expansion"] == {"ran": True, "kind": "expand", "calls": 3}
    assert r["winner_provider"] == "page" and r["winner_domain"] == "luluhypermarket.com"
    assert r["warnings"] == ["low_resolution"]
    assert r["vlm_calls"] == 3                # billed calls (primary + strong), not verify() invocations
    if _has_usage():                          # per-model usage: priced from the verifier's own estimate
        assert r["strong_calls"] == 1 and len(r["vlm_usage"]) == 3
        assert r["cost"] == {"search": 0.003, "verifier": 0.0028} and r["cost_usd"] == 0.0058
    else:                                     # no per-model usage: each billed call at --vlm-cost
        assert r["strong_calls"] == 0 and r["vlm_usage"] == []
        assert r["cost"] == {"search": 0.003, "verifier": 0.003} and r["cost_usd"] == 0.006
    assert [v["status"] for v in r["verifier_calls"]] == ["ok", "ok"]
    assert r["verdicts"] == {"MISMATCH": 1, "MATCH": 1} and r["only_social"] is False
    assert r["outage"] is None and r["unselected_reason"] is None
    s = script.summarize([r])
    assert s["expansion"]["winners"] == 1 and s["winner_providers"] == {"page": 1}


def test_run_row_does_not_pass_expansion_to_a_pipeline_without_it(script, capsys):
    seen = {}
    identity, pipeline, providers, verify, _verifier = _modules(seen, with_expansion=False)
    row = {"row_number": 2, "name": "AIDA FRENCH FRIES 1KG", "brand": "AIDA"}
    r = script.run_row(row, {}, identity, pipeline, providers, verify, 0.001, 0.001, expansion=True)
    assert "kwargs" in seen and seen["kwargs"] == {}
    script.print_row(r)
    printed = capsys.readouterr().out
    assert "[expansion]" in printed and "WARNINGS low_resolution" in printed
    assert "DECISION REVIEW_PRESELECTED" in printed


def test_the_verifier_fallback_when_the_pipeline_has_no_default(script):
    made = []
    pipeline = types.SimpleNamespace()
    verify = types.SimpleNamespace(GeminiVerifier=lambda: made.append(1) or "gemini")
    assert script.production_verifier(pipeline, verify) == "gemini" and made == [1]


def test_errors_in_the_json_file_carry_no_secret(script, tmp_path):
    key = "sk-" + "ab12" * 8
    rows = [{"row": 9, "name": "X", "error": script.redact(f"ConnectionError: /search.json?api_key={key}",
                                                         secrets=[key])}]
    text = json.dumps(rows)
    assert key not in text and "api_key=[hidden]" in text


# ---------------------------------------------------------------------------
# Review fixes
# ---------------------------------------------------------------------------

def test_cost_per_product_is_over_the_measured_rows(script):
    """Outage rows made no (or only a few) answered calls: counting them as products made the
    per-product cost look cheaper than a product really costs (the owner's goal is < $0.01)."""
    picked = {"decision": "REVIEW_PRESELECTED", "winner": "https://x.ae/1.jpg", "cost_usd": 0.004,
              "provider_calls": [_call("ok", 200, None)]}
    outage = {"decision": "PROVIDER_DOWN", "cost_usd": 0.0,
              "provider_calls": [_call("error", None, "timeout"), _call("error", None, "ConnectionError: refused")]}
    rows = [dict(picked, row=2), dict(picked, row=3)] + [dict(outage, row=n) for n in (4, 5, 6)]
    s = script.summarize(rows)
    assert s["outage_rows"] == 3 and s["measured"] == 2
    assert s["cost_usd"]["total"] == 0.008
    assert s["cost_usd"]["per_product"] == 0.004          # not 0.008 / 5 = 0.0016
    assert "$0.0040 per measured product" in script.format_summary(s)


def test_a_slow_label_reader_or_a_pick_is_never_an_outage(script):
    """Outage rows leave the coverage. A pick made while the label reader was down (the tier-1 fallback)
    is a measured result, and a label reader that only timed out after the search was answered had a
    connection: the model was slow (free tier), which the coverage must show, not hide."""
    down = {"decision": "REVIEW_UNSELECTED", "failure_code": "VERIFIER_DOWN", "top": [], "reject_counts": {},
            "provider_calls": [_call("ok", 200, None)],
            "verifier_calls": [{"status": "unknown", "error": "connection_error:ConnectionError", "calls": 1}]}
    assert script.outage_reason(dict(down, row=1)) == "no connection to the label reader"
    picked = dict(down, row=2, decision="REVIEW_PRESELECTED", winner="https://x.ae/1.jpg")
    slow = dict(down, row=3, verifier_calls=[{"status": "unknown", "error": "timeout", "calls": 1},
                                             {"status": "unknown", "error": "circuit_open", "calls": 0}])
    assert script.outage_reason(picked) is None and script.outage_reason(slow) is None
    assert script.unselected_reason(slow) == "verifier_down"
    s = script.summarize([dict(down, row=1), picked, slow])
    assert (s["outage_rows"], s["measured"], s["preselected"]) == (1, 2, 1)
    assert s["unselected_rows"]["verifier_down"] == [3] and s["coverage_pct"] == 50.0


def test_a_json_path_that_cannot_be_written_stops_before_any_paid_call(script, monkeypatch, tmp_path):
    """--json was written only after every row ran: a mistyped folder lost a whole paid dry run's results."""
    monkeypatch.setattr(script, "load_v2", lambda: pytest.fail("no row may run before the --json path is checked"))
    monkeypatch.setattr(script, "run_probe", lambda *a, **k: pytest.fail("the probe may not run either"))
    missing = tmp_path / "no-such-folder" / "run3.json"
    for argv in (["--rows", "2-61", "--dry-run", "--json", str(missing)], ["--probe", "--json", str(missing)],
                 ["--rows", "2", "--json", str(tmp_path)]):
        with pytest.raises(SystemExit) as stop:
            script.main(argv)
        assert stop.value.code == 2


def test_a_crashed_row_never_logs_a_key(script, monkeypatch, tmp_path, capsys, caplog):
    """The exception text of a crashed row can hold a request URL with a key in it: the row's error was
    redacted, but log.exception() printed the raw traceback to the console."""
    import logging
    import uuid

    key = "AIza" + uuid.uuid4().hex + "Zq9"
    values = {"GEMINI_API_KEY": key}
    monkeypatch.setattr(script, "setting", lambda name, default="": values.get(name, default))

    def find_product_image(spec, **kwargs):
        raise RuntimeError("unreadable answer from /v1beta/models/m:generateContent?key=" + key
                           + " (sent with " + key + ")")

    pipeline = types.SimpleNamespace(find_product_image=find_product_image, _default_verifier=lambda: object())
    identity = types.SimpleNamespace(build_sku_spec=lambda fields, mappings: _Spec(
        sku_key="k", brand_conf="sheet_raw", gtin_status="missing", variants={}))
    providers = types.SimpleNamespace(default_providers=lambda: [])
    settings = types.SimpleNamespace(gemini_model=lambda: "gemini-3.1-flash-lite", serper_api_key=lambda: "",
                                     gemini_api_key=lambda: key, auto_publish_enabled=lambda: False)
    verify = types.SimpleNamespace(GeminiVerifier=lambda: object())
    monkeypatch.setattr(script, "load_v2", lambda: (identity, pipeline, providers, settings, verify))
    monkeypatch.setattr(script, "open_sheet_read_only", lambda: (None, None))
    monkeypatch.setattr(script, "read_sheet_rows", lambda ws, numbers: [
        {"row_number": 2, "name": "AL ALALI FANCY TUNA WATER 170GM", "brand": "AL ALALI"}])
    monkeypatch.setattr(script, "read_brand_mappings", lambda sh: {})
    caplog.set_level(logging.DEBUG)

    out = tmp_path / "run.json"
    assert script.main(["--rows", "2", "--dry-run", "--json", str(out), "-v"]) == 0
    printed = capsys.readouterr()
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["rows"][0]["error"].startswith("RuntimeError: ") and doc["summary"]["errors"] == 1
    assert "row 2 failed" in caplog.text
    for text in (caplog.text, printed.out, printed.err, out.read_text(encoding="utf-8")):
        assert key not in text
