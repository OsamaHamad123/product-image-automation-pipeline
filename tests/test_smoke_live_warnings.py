"""scripts/smoke_live.py reports the pick's review warnings (decide.route 'warn:' reasons)."""

import importlib.util
from pathlib import Path

from catalog_match.models import Candidate, CandidateScore, RankedCandidate

REPO = Path(__file__).resolve().parents[1]


def _load_script():
    spec = importlib.util.spec_from_file_location("smoke_live", REPO / "scripts" / "smoke_live.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_pick_warnings_are_described_and_printed(capsys):
    script = _load_script()
    rc = RankedCandidate(candidate=Candidate(image_url="https://www.carton.sa/storage/WhatsApp%20Image.jpeg",
                                             title="Mukalla White Tuna 185g", provider="serper", domain="carton.sa"),
                         score=CandidateScore(tier=2), status="preselected",
                         reasons=["vlm:UNSURE", "preselected:tier1_unsure", "warn:vlm_unsure",
                                  "warn:chat_or_screenshot", "warn:foreign_store"])
    pick = script._describe(rc, 1)
    assert pick["warnings"] == ["vlm_unsure", "chat_or_screenshot", "foreign_store"]

    script.print_row({"row": 42, "name": "MUKALLA WHITE TUNA WITH VEG 185GM", "brand": "MUKALLA",
                      "brand_conf": "sheet_raw", "gtin_status": "missing", "variants": {}, "queries": [],
                      "provider_calls": [], "top": [pick], "winner_detail": pick, "decision": "REVIEW_PRESELECTED",
                      "failure_code": None, "winner": pick["image_url"], "warnings": pick["warnings"],
                      "cost_usd": 0.0, "serp_calls": 0, "vlm_calls": 0, "seconds": 0.0})
    printed = capsys.readouterr().out
    assert "  WARNINGS vlm_unsure chat_or_screenshot foreign_store" in printed


def test_no_warnings_line_without_a_pick_or_warnings(capsys):
    script = _load_script()
    base = {"row": 2, "name": "AIDA FRENCH FRIES 1KG", "brand": "AIDA", "brand_conf": "sheet_raw",
            "gtin_status": "missing", "variants": {}, "queries": [], "provider_calls": [], "top": [],
            "failure_code": None, "cost_usd": 0.0, "serp_calls": 0, "vlm_calls": 0, "seconds": 0.0}
    script.print_row(dict(base, winner_detail=None, winner=None, decision="REVIEW_UNSELECTED"))
    clean = {"rank": 1, "status": "preselected", "reasons": ["preselected:vlm_match"], "warnings": [],
             "provider": "serper", "domain": "carrefouruae.com", "title": "Aida French Fries 1kg",
             "image_url": "https://x.ae/1.jpg", "page_url": "", "evidence": "tier=1", "vlm": None}
    script.print_row(dict(base, top=[clean], winner_detail=clean, winner=clean["image_url"],
                          decision="REVIEW_PRESELECTED"))
    assert "WARNINGS" not in capsys.readouterr().out
