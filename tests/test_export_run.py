"""«تصدير تقرير للتحليل»: scripts/export_run.py (real MariaDB, the test database; skipped when MariaDB is down).

Each test failed before its change (the script did not exist):

* one JSON file holds every row of the latest run (or a chosen run, or every row waiting for review): the sheet row,
  the barcode's presence, the decision and failure code, the no-pick reason and sentence, the sheet gaps, the top
  candidates with the label reader's reading and the size / variant evidence, the provider calls and the cost, and
  the run's metadata (git commit, settings with secret settings only as set / not set, its run_history row);
* not one configured secret appears anywhere in it, wherever a stored text carried it (a provider error, a page
  URL, a candidate title): every one is [hidden];
* it has scripts/smoke_live.py's --json shape, so scripts/compare_runs.py compares two exports, or an export with a
  dry run;
* cli_bridge 'export_run' writes it under temp/exports for the dashboard (and clears exports left behind).
"""

import json
import sys
from pathlib import Path

import pytest

from no_pick_fixtures import MAPPINGS

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))
ROW = 975000
SKU = "w2e-sku-"
RUN_ID = "w2e-run-0001"


def _sql(db, statement, params=()):
    conn = db.get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute(statement, params)
        rows = cur.fetchall()
        conn.commit()
        return rows
    finally:
        conn.close()


@pytest.fixture
def db(mariadb_or_skip):
    db = mariadb_or_skip

    def wipe():
        # the test database is ours alone (conftest)
        _sql(db, "DELETE FROM automation_queue")
        _sql(db, "DELETE FROM curation_candidates WHERE sku_key LIKE %s OR product_name LIKE 'W2E %%'", (SKU + "%",))
        _sql(db, "DELETE FROM run_history WHERE run_id = %s", (RUN_ID,))
        _sql(db, "UPDATE automation_state SET run_id = NULL WHERE `key` = 'active_session'")

    wipe()
    yield db
    wipe()


OLD_OUTCOME = {"decision": "REVIEW_UNSELECTED", "failure_code": None, "reject_counts": {"vlm:UNSURE": 2},
               "provider_health": [{"provider": "serper", "status": "ok", "http_status": 200, "query_id": "Q1"}],
               "queries": ["Mr John French Fries"], "searched_at": "2026-10-03T09:00:00+00:00"}
UNSURE_TIER2 = {"url": "https://cdn.carrefouruae.com/img/1.jpg", "status": "eligible", "title": "Mr John Fries",
                "page_url": "https://www.carrefouruae.com/mafuae/en/mr-john-fries/p/1", "domain": "carrefouruae.com",
                "reasons": ["vlm:UNSURE"], "evidence": {"tier": 2, "size": "unknown", "brand": True},
                "vlm": {"decision": "UNSURE", "brand_text": "Mr John", "size_match": "unsure"}}


def _insert(db, i, status, trace, name="W2E MR JOHN FRENCH FRIES", brand="MR JOHN", payload=None, failure_code=None,
            run_id=None, barcode=""):
    _sql(db, "INSERT INTO automation_queue (`row_number`, barcode, product_name, brand, search_query, status, "
             "failure_code, trace_json, sku_key, run_id, payload_json, updated_at) "
             "VALUES (%s, %s, %s, %s, 'q', %s, %s, %s, %s, %s, %s, '2026-10-03 09:00:00')",
         (ROW + i, barcode, name, brand, status, failure_code, None if trace is None else json.dumps(trace),
          f"{SKU}{i}", run_id,
          json.dumps(payload or {"size": ""})))


def _save_candidates(db, i, cands, name="W2E MR JOHN FRENCH FRIES"):
    assert db.save_curation_candidates(ROW + i, name, "MR JOHN", cands, sku_key=f"{SKU}{i}")


@pytest.fixture
def bridge(monkeypatch, tmp_path):
    import cli_bridge
    from catalog_match import explain

    monkeypatch.setattr(cli_bridge, "_load_brand_mappings", lambda: MAPPINGS)
    monkeypatch.setattr(explain, "PRODUCTS_CACHE", tmp_path / "products_cache.json")
    return cli_bridge


# ---------------------------------------------------------------------------
# The run export
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------

def _fake(tag):
    return f"W2E-FAKE-{tag}-7f3c9a1e"


SECRET_CONFIG = {"SERPER_API_KEY": _fake("SERPER"), "GEMINI_API_KEY": _fake("GEMINI"),
                 "ANTHROPIC_API_KEY": _fake("ANTHROPIC"), "SERPAPI_API_KEY": _fake("SERPAPI"),
                 "PHOTOROOM_API_KEY": _fake("PHOTOROOM"), "CLOUDINARY_API_KEY": _fake("CLOUDKEY"),
                 "CLOUDINARY_API_SECRET": _fake("CLOUDSECRET"), "TELEGRAM_BOT_TOKEN": _fake("TELEGRAM"),
                 "PROXY_URL": f"http://shopuser:{_fake('PROXYPASS')}@proxy.example:8080"}


@pytest.fixture
def run_rows(db, monkeypatch, tmp_path):
    """A run of three rows (no pick, not found, auto-published) with the configured keys leaking into every place a
    stored text can carry them: a provider error, a page URL, a candidate title."""
    import config
    from catalog_match import explain

    for name, value in SECRET_CONFIG.items():
        monkeypatch.setattr(config, name, value, raising=False)
    monkeypatch.setattr(explain, "PRODUCTS_CACHE", tmp_path / "products_cache.json")
    serper = SECRET_CONFIG["SERPER_API_KEY"]
    leaky = dict(OLD_OUTCOME, provider_health=[
        {"provider": "serper", "status": "ok", "http_status": 200, "query_id": "Q1"},
        {"provider": "serper_web", "status": "error", "http_status": None, "query_id": "X1",
         "error": f"ConnectionError: https://google.serper.dev/search?api_key={serper}&q=x"}],
        vlm_calls=2, vlm_usage=[{"role": "primary", "provider": "gemini", "model": "m", "usd": 0.002}])
    _insert(db, 0, "ready_for_review", {"outcome": leaky}, run_id=RUN_ID)
    _save_candidates(db, 0, [dict(UNSURE_TIER2, page_url=UNSURE_TIER2["page_url"] + f"?key={serper}",
                                  title=f"Mr John Fries {SECRET_CONFIG['GEMINI_API_KEY']}")])
    full = {"outcome": {"decision": "NOT_FOUND", "failure_code": "NO_RESULTS", "searched_at": "2026-10-03T09:01:00+00:00",
                        "provider_health": [{"provider": "serper", "status": "empty", "http_status": 200, "query_id": "Q1"}]}}
    _insert(db, 1, "failed", full, name="W2E TARGET CHICEKN LUNCHEON MEAT 340GM", brand="TARGET",
            failure_code="NO_RESULTS", run_id=RUN_ID)
    auto = {"outcome": {"decision": "AUTO_PUBLISH", "failure_code": None, "winner_url": "https://cdn.x.ae/milk.jpg",
                        "searched_at": "2026-10-03T09:02:00+00:00", "vlm_calls": 1,
                        "provider_health": [{"provider": "serper", "status": "ok", "http_status": 200, "query_id": "Q1"}]}}
    _insert(db, 2, "completed", auto, name="W2E ALMARAI FRESH MILK 1L", brand="ALMARAI", run_id=RUN_ID,
            barcode="6281007035309", payload={"size": "1L"})
    _save_candidates(db, 2, [{"url": "https://cdn.x.ae/milk.jpg", "status": "preselected", "domain": "x.ae",
                              "reasons": ["vlm:MATCH", "auto_publish"], "evidence": {"tier": 1, "size": "match"},
                              "vlm": {"decision": "MATCH", "brand_match": "yes", "size_match": "yes"}}],
                     name="W2E ALMARAI FRESH MILK 1L")
    _insert(db, 3, "ready_for_review", {"outcome": OLD_OUTCOME}, run_id="w2d-older-run")
    _insert(db, 4, "pending", None, name="W2E NOT SEARCHED YET", run_id=RUN_ID)        # the run has not reached it
    _sql(db, "INSERT INTO run_history (run_id, run_trigger, started_at, outcome, searched, spend_usd) "
             "VALUES (%s, 'dashboard', '2026-10-03 08:59:00', 'done', 3, 0.0123)", (RUN_ID,))
    return db


def test_the_export_holds_every_row_of_the_latest_run_and_no_secret(run_rows, tmp_path):
    import export_run
    import smoke_live

    path, n = export_run.write_export(str(tmp_path / export_run.default_name()), "latest", mappings=MAPPINGS)
    assert n == 3 and Path(path).name.startswith("laqta_run_")
    text = Path(path).read_text(encoding="utf-8")
    for value in list(SECRET_CONFIG.values()) + [_fake("PROXYPASS")]:
        assert value not in text, value                      # zero hits of any configured secret
    assert "api_key=[hidden]" in text and "[hidden]" in text
    doc = smoke_live.load_run(path)
    assert doc["format"] == "smoke_live/2" and doc["export"] == "laqta_run/1"
    meta = doc["meta"]
    assert (meta["scope"], meta["run_id"], meta["rows"], meta["not_searched"]) == ("latest", RUN_ID, 3, [ROW + 4])
    assert meta["git"]["commit"] and meta["run_history"]["spend_usd"] == pytest.approx(0.0123)
    settings = meta["settings"]
    assert settings["configured"]["SERPER_API_KEY"] is True and "SERPER_API_KEY" not in settings["values"]
    rows = {r["row"] - ROW: r for r in doc["rows"]}
    nopick = rows[0]
    assert (nopick["decision"], nopick["queue_status"], nopick["unselected_reason"]) == \
        ("REVIEW_UNSELECTED", "ready_for_review", "unsure")
    assert nopick["no_pick"]["key"] == "no_size" and nopick["no_pick"]["text"].startswith("الشيت ما فيه حجم")
    assert {i["key"] for i in nopick["sheet_issues"]} == {"no_size", "no_barcode"}
    top = nopick["top"][0]
    assert (top["tier"], top["status"], top["label_reader"]["decision"], top["size_evidence"]["size"]) == \
        (2, "eligible", "UNSURE", "unknown")
    assert nopick["cost"] == {"search": 0.001, "verifier": 0.002} and nopick["cost_known"] is True
    assert nopick["expansion"] == {"ran": True, "kind": "expand", "calls": 1}
    assert rows[1]["no_pick"]["key"] == "typo" and "«CHICEKN» قصدك «CHICKEN»" in rows[1]["no_pick"]["text"]
    assert rows[1]["has_barcode"] is False and rows[2]["has_barcode"] is True and rows[2]["barcode_valid"] is True
    assert (rows[2]["decision"], rows[2]["winner"], rows[2]["no_pick"]) == ("AUTO_PUBLISH", "https://cdn.x.ae/milk.jpg", None)
    assert doc["summary"]["rows"] == 3 and doc["summary"]["auto_publish"] == 1
    assert doc["summary"]["unselected"]["unsure"] == 1 and doc["summary"]["unselected"]["not_found"] == 1


def test_a_stored_search_exports_its_stage_timings_and_an_older_one_has_none(run_rows):
    """The outcome's per-stage milliseconds come out as the row's 'timings'; a trace saved before they were recorded
    gives {} and is left out of the summary block (p50 / p90 / total seconds per stage)."""
    import export_run

    timed = {"outcome": dict(OLD_OUTCOME, decision="NOT_FOUND", timings={
        "retrieval": 9000, "fetch": 4500, "quality": 200, "verify": 6000, "total": 20000})}
    _sql(run_rows, "UPDATE automation_queue SET trace_json = %s WHERE `row_number` = %s", (json.dumps(timed), ROW + 1))
    doc, _hidden = export_run.build_export("run", RUN_ID, mappings=MAPPINGS)
    rows = {r["row"] - ROW: r for r in doc["rows"]}
    assert rows[1]["timings"] == {"retrieval": 9000, "fetch": 4500, "quality": 200, "verify": 6000, "total": 20000}
    assert rows[0]["timings"] == {} and rows[2]["timings"] == {}
    summary = doc["summary"]["timings"]
    assert summary["rows"] == 1
    assert summary["stages"]["fetch"] == {"rows": 1, "p50_s": 4.5, "p90_s": 4.5, "total_s": 4.5}
    assert summary["stages"]["total"]["total_s"] == 20.0
    assert list(summary["stages"]) == ["retrieval", "fetch", "quality", "verify", "total"]


def test_compare_runs_reads_an_export(run_rows, tmp_path, capsys):
    import compare_runs
    import export_run

    old, _ = export_run.write_export(str(tmp_path / "old.json"), "run", RUN_ID, mappings=MAPPINGS)
    _sql(run_rows, "UPDATE automation_queue SET trace_json = %s WHERE `row_number` = %s",
         (json.dumps({"outcome": dict(OLD_OUTCOME, decision="REVIEW_PRESELECTED",
                                      winner_url="https://cdn.carrefouruae.com/img/1.jpg")}), ROW))
    _sql(run_rows, "UPDATE curation_candidates SET status = 'preselected', is_selected = 1 WHERE `row_number` = %s",
         (ROW,))
    new, _ = export_run.write_export(str(tmp_path / "new.json"), "run", RUN_ID, mappings=MAPPINGS)
    assert compare_runs.main([old, new]) == 0
    report = capsys.readouterr().out
    assert "ROWS WHOSE DECISION CHANGED (1)" in report and "gained a pick" in report
    assert compare_runs.main([str(ROOT / "runs" / "2026-10-03" / "smoke_6.json"), new]) == 0


def test_the_review_scope_takes_every_row_waiting_for_review(run_rows, tmp_path):
    import export_run

    doc, _hidden = export_run.build_export("review", mappings=MAPPINGS)
    assert sorted(r["row"] - ROW for r in doc["rows"]) == [0, 3] and doc["meta"]["run_id"] is None
    with pytest.raises(ValueError):
        export_run.build_export("run", "bad id; DROP")


def test_the_bridge_writes_the_export_for_the_dashboard(run_rows, bridge, monkeypatch, tmp_path):
    import os
    import time

    monkeypatch.setattr(bridge, "EXPORT_DIR", str(tmp_path / "exports"))
    os.makedirs(tmp_path / "exports")
    stale = tmp_path / "exports" / "laqta_run_2026-10-01_0900.json"
    stale.write_text("{}", encoding="utf-8")
    os.utime(stale, (time.time() - 7200, time.time() - 7200))
    out = bridge.action_export_run({"scope": "latest"})
    assert out["status"] == "success" and out["rows"] == 3
    written = tmp_path / "exports" / out["file"]
    assert written.is_file() and not stale.exists()                 # an export left behind is cleared after an hour
    assert _fake("SERPER") not in written.read_text(encoding="utf-8")
    assert bridge.action_export_run({"scope": "run", "run_id": "../../etc"})["status"] == "error"


def test_the_worker_publish_details_and_photoroom_uncertainty_are_exported(run_rows):
    """main.publish_report lands in the row's trace ('publish'): provider, flags and PhotoRoom's x-uncertainty-score."""
    import export_run

    published = {"outcome": {"decision": "AUTO_PUBLISH", "failure_code": None, "winner_url": "https://cdn.x.ae/milk.jpg"},
                 "publish": {"status": "needs_review", "error": "background_not_removed", "provider": "photoroom",
                             "isolated": False, "quality_flags": ["photoroom_unsure"], "quality_notes": [],
                             "uncertainty": 0.62, "canvas": [1228, 1228], "finish": {"background": "transparent"}}}
    _sql(run_rows, "UPDATE automation_queue SET trace_json = %s WHERE `row_number` = %s",
         (json.dumps(published), ROW + 2))
    doc, _hidden = export_run.build_export("run", RUN_ID, mappings=MAPPINGS)
    rows = {r["row"] - ROW: r for r in doc["rows"]}
    assert rows[2]["publish"]["uncertainty"] == 0.62 and rows[2]["publish"]["quality_flags"] == ["photoroom_unsure"]
    assert rows[0]["publish"] is None and rows[1]["publish"] is None


def test_the_pick_keeps_its_provider_query_and_phash_even_after_its_candidates_are_gone(run_rows):
    """Live exports of 2026-10-04/05 gave winner_providers {'?': N} and no expansion attribution: curation_candidates
    kept no provider, query id or pHash, and a published or approved row lost its top list and winner_detail."""
    import export_run

    # a review row: the candidate columns carry the provenance
    shop = dict(UNSURE_TIER2, status="preselected", provider="serper_shopping", query_id="X2",
                phash="c3c3a5a55a5a3c3c", reasons=["vlm:MATCH", "preselected:vlm_match"])
    _save_candidates(run_rows, 0, [shop])
    stored = run_rows.get_curation_candidates(ROW + 0, f"{SKU}0")
    assert (stored[0]["provider"], stored[0]["query_id"], stored[0]["phash"]) == \
        ("serper_shopping", "X2", "c3c3a5a55a5a3c3c")
    picked = dict(OLD_OUTCOME, decision="REVIEW_PRESELECTED", winner_url=UNSURE_TIER2["url"])
    _sql(run_rows, "UPDATE automation_queue SET trace_json = %s WHERE `row_number` = %s",
         (json.dumps({"outcome": picked}), ROW + 0))
    # a published row: no review candidate left, the outcome's own top list (facade) still has the pick
    _sql(run_rows, "DELETE FROM curation_candidates WHERE sku_key = %s", (f"{SKU}2",))
    auto = {"outcome": {"decision": "AUTO_PUBLISH", "failure_code": None, "winner_url": "https://cdn.x.ae/milk.jpg",
                        "provider_health": [{"provider": "serper", "status": "ok", "query_id": "Q1"}],
                        "top": [{"url": "https://cdn.x.ae/milk.jpg", "status": "preselected", "domain": "x.ae",
                                 "provider": "serper", "query_id": "Q1", "phash": "0f0f0f0f0f0f0f0f",
                                 "reasons": ["vlm:MATCH", "auto_publish"],
                                 "evidence": {"tier": 1, "size": "match", "url_gtin": "6281007035309",
                                              "same_picture_domains": ["noon.com", "x.ae"]},
                                 "vlm": {"decision": "MATCH", "brand_match": "yes"}}]}}
    _sql(run_rows, "UPDATE automation_queue SET trace_json = %s WHERE `row_number` = %s", (json.dumps(auto), ROW + 2))
    doc, _hidden = export_run.build_export("run", RUN_ID, mappings=MAPPINGS)
    rows = {r["row"] - ROW: r for r in doc["rows"]}
    assert (rows[0]["winner_provider"], rows[0]["winner_detail"]["query_id"], rows[0]["top"][0]["phash"]) == \
        ("serper_shopping", "X2", "c3c3a5a55a5a3c3c")
    assert rows[2]["top"] and rows[2]["winner_detail"]["tier"] == 1
    assert (rows[2]["winner_provider"], rows[2]["winner_detail"]["query_id"]) == ("serper", "Q1")
    assert rows[2]["winner_detail"]["url_gtin"] == "6281007035309"
    assert rows[2]["winner_detail"]["same_picture_domains"] == ["noon.com", "x.ae"]
    assert doc["summary"]["winner_providers"] == {"serper_shopping": 1, "serper": 1}
    assert doc["summary"]["expansion"]["winners"] == 1          # the shopping pick is the round's
