"""Regression tests for the dashboard+publishing review findings (offline).

- Dashboard searches carry size / sub_category / origin, so size identity survives without a queue row.
- index.blade.php only writes to elements that exist (batchProgressCounts).
- The batch page's background-removal selector reaches config.BG_REMOVAL_METHOD.
- save-candidates keeps page_url and the identity tier (sent by the bridge in evidence.tier).
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DASH = ROOT / "dashboard"
VIEWS = DASH / "resources" / "views"
PHP = shutil.which("php")


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _json_body(source: str, anchor: str, span: int = 2500) -> str:
    """The JSON.stringify({...}) body of the first fetch after `anchor`."""
    start = source.index(anchor)
    body = source[start:]
    body = body[body.index("JSON.stringify(") :]
    return body[: body.index("})") + 2][:span]


# ---------------------------------------------------------------------------
# size identity on interactive searches
# ---------------------------------------------------------------------------

@pytest.fixture
def bridge(offline, monkeypatch, tmp_path):
    import cli_bridge
    import google_sheets
    import local_cache_db

    monkeypatch.setattr(cli_bridge, "LOG_PATH", str(tmp_path / "search.log"))
    monkeypatch.setattr(google_sheets, "get_sheets_client", lambda: object())
    monkeypatch.setattr(google_sheets, "get_brand_mappings", lambda *a, **k: {})
    # no queue row: stopBatch / resetBatch delete automation_queue
    monkeypatch.setattr(local_cache_db, "get_task_by_row", lambda row: None)
    monkeypatch.setattr(local_cache_db, "get_rejections", lambda sku: ([], []))
    return cli_bridge


def test_search_uses_size_sub_category_and_origin_from_the_request(bridge, monkeypatch):
    import image_search
    seen = {}

    def fake_search(query, name, brand, **kwargs):
        seen.update(kwargs)
        kwargs["trace"]["outcome"] = {"decision": "NOT_FOUND", "failure_code": "NO_MATCH"}
        return None

    monkeypatch.setattr(image_search, "search_best_product_image", fake_search)
    bridge.action_search({"product_name": "Fresh Milk Full Fat", "brand": "Almarai", "row_number": 7,
                          "size": "2L", "sub_category": "Fresh Milk", "origin": "Saudi Arabia"})
    assert seen["size_text"] == "2L"
    assert seen["sub_category"] == "Fresh Milk"
    assert seen["origin"] == "Saudi Arabia"


def test_search_falls_back_to_the_queue_payload(bridge, monkeypatch):
    import image_search
    import local_cache_db
    seen = {}

    def fake_search(query, name, brand, **kwargs):
        seen.update(kwargs)
        return None

    monkeypatch.setattr(image_search, "search_best_product_image", fake_search)
    monkeypatch.setattr(local_cache_db, "get_task_by_row", lambda row: {
        "product_name": "Fresh Milk Full Fat",
        "payload_json": json.dumps({"size": "1L", "sub_category": "Milk", "origin": "UAE"})})
    bridge.action_search({"product_name": "Fresh Milk Full Fat", "brand": "Almarai", "row_number": 7})
    assert (seen["size_text"], seen["sub_category"], seen["origin"]) == ("1L", "Milk", "UAE")


def test_dashboard_search_bodies_send_size_identity():
    catalog = read(VIEWS / "dashboard" / "catalog.blade.php")
    batch = read(VIEWS / "dashboard" / "batch_automation.blade.php")

    ctx = catalog[catalog.index("function currentProductContext"):]
    ctx = ctx[: ctx.index("};") + 2]
    for key in ("size", "sub_category", "origin"):
        assert re.search(rf"\b{key}:", ctx), key
    select = catalog[catalog.index("function selectProduct"):]
    select = select[: select.index("activeRowNumber")]
    assert "form.dataset.size = prod.size" in select
    assert "form.dataset.subCategory = prod.sub_category" in select

    search = _json_body(catalog, "fetch('/api/search'")
    reject = _json_body(catalog, "async function submitReject")
    for body in (search, reject):
        for token in ("size: ctx.size", "sub_category: ctx.sub_category", "origin: ctx.origin"):
            assert token in body, (token, body)

    inline = _json_body(batch, "async function triggerInlineSearch")
    single_reject = _json_body(batch, "async function rejectAndReSearchCandidate")
    for body in (inline, single_reject):
        for token in ("size: p.size", "sub_category: p.sub_category", "origin: p.origin"):
            assert token in body, (token, body)


def test_curation_reject_forwards_size_identity_to_the_bridge():
    text = read(DASH / "app" / "Http" / "Controllers" / "CurationController.php")
    method = text[text.index("function rejectAndReSearch"):text.index("function selectCandidate")]
    for key in ("size", "sub_category", "origin"):
        assert f"'{key}' => 'nullable|string'" in method, key
        assert f"'{key}' => $validated['{key}'] ?? ''" in method, key


# ---------------------------------------------------------------------------
# index.blade.php: no writes to missing elements
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["index", "catalog", "batch_automation"])
def test_every_element_id_the_script_reads_exists(name):
    text = read(VIEWS / "dashboard" / f"{name}.blade.php")
    layout = read(VIEWS / "layouts" / "layout.blade.php")
    ids = set(re.findall(r'id="([^"{]+)"', text + layout)) | set(re.findall(r"id:\s*'([^']+)'", text))
    refs = set(re.findall(r"getElementById\('([^'\s]+)'\)", text))
    assert not refs - ids, sorted(refs - ids)


def test_batch_progress_counts_lives_in_the_progress_panel():
    index = read(VIEWS / "dashboard" / "index.blade.php")
    panel = index[index.index('id="batchProgressPanel"'):index.index('id="stopBatchBtn"')]
    assert 'id="batchProgressCounts"' in panel


# ---------------------------------------------------------------------------
# batch background-removal selector reaches the publisher
# ---------------------------------------------------------------------------

@pytest.fixture
def run_config(monkeypatch, tmp_path):
    import config
    import main

    monkeypatch.chdir(tmp_path)
    (tmp_path / "temp").mkdir()
    monkeypatch.setattr(config, "load_db_config", lambda: None)
    monkeypatch.setattr(config, "BG_REMOVAL_METHOD", "photoroom")

    def write(overrides):
        (tmp_path / "temp" / "run_config.json").write_text(json.dumps(overrides), encoding="utf-8")
        main.load_run_config()
        return config.BG_REMOVAL_METHOD

    return write


@pytest.mark.parametrize("sent, expected", [
    ("none", "none"), ("grabcut", "grabcut"), ("remove.bg", "remove_bg_api"), ("PhotoRoom", "photoroom"),
])
def test_batch_bg_removal_method_is_applied(run_config, sent, expected):
    assert run_config({"bgRemovalMethod": sent}) == expected


def test_unknown_bg_removal_method_is_ignored(run_config):
    assert run_config({"bgRemovalMethod": "magic"}) == "photoroom"
    assert run_config({}) == "photoroom"


def test_batch_page_posts_the_selected_method():
    batch = read(VIEWS / "dashboard" / "batch_automation.blade.php")
    run_all = batch[batch.index("async function runAllAutomation"):]
    run_all = run_all[: run_all.index("fetch('/api/run-all'")]
    assert "bgRemovalMethod: document.getElementById('bgRemovalMethod').value" in run_all


# ---------------------------------------------------------------------------
# save-candidates keeps page_url and the tier
# ---------------------------------------------------------------------------

def _run_php(script: str) -> dict:
    with tempfile.NamedTemporaryFile("w", suffix=".php", delete=False, encoding="utf-8") as fh:
        fh.write(script)
        path = fh.name
    try:
        result = subprocess.run([PHP, path], capture_output=True, text=True, timeout=60)
    finally:
        os.unlink(path)
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


@pytest.mark.skipif(PHP is None, reason="php is not installed")
def test_save_candidates_keeps_page_url_and_tier():
    service = str(DASH / "app" / "Services" / "CandidateRow.php").replace("\\", "/")
    candidates = [
        # shape sent by the bridge / normalisers: tier inside evidence
        {"url": "https://lulu.ae/a.jpg", "page_url": "https://lulu.ae/p/1", "status": "preselected",
         "evidence": {"tier": 1, "size": "match"}, "reasons": ["tier T1"]},
        # identity_tier set explicitly wins; page_url only in evidence
        {"image_url": "https://noon.com/b.jpg", "identity_tier": 2, "evidence": {"tier": 3, "page_url": "https://noon.com/p/2"}},
        # nothing known
        {"image_url": "https://x.ae/c.jpg", "page_url": "", "evidence": {"tier": None}},
        # over-long page_url is truncated to the column budget
        {"image_url": "https://x.ae/d.jpg", "page_url": "https://x.ae/" + "a" * 3000},
    ]
    script = f"""<?php
require '{service}';
use App\\Services\\CandidateRow;
$cands = json_decode({json.dumps(json.dumps(candidates))}, true);
$out = [];
foreach ($cands as $c) {{ $out[] = CandidateRow::optionalColumns($c, 'sku123', 'ui-run'); }}
echo json_encode($out);
"""
    rows = _run_php(script)
    assert rows[0]["page_url"] == "https://lulu.ae/p/1" and rows[0]["identity_tier"] == 1
    assert rows[1]["page_url"] == "https://noon.com/p/2" and rows[1]["identity_tier"] == 2
    assert rows[2]["page_url"] is None and rows[2]["identity_tier"] is None
    assert len(rows[3]["page_url"]) == 2048
    assert all(r["sku_key"] == "sku123" and r["run_id"] == "ui-run" for r in rows)
    assert json.loads(rows[0]["evidence_json"]) == {"tier": 1, "size": "match"}


def test_save_candidates_uses_the_shared_row_builder():
    text = read(DASH / "app" / "Http" / "Controllers" / "CurationController.php")
    assert "CandidateRow::optionalColumns($c, $skuKey, $runId)" in text


def test_batch_normaliser_maps_the_tier_from_evidence():
    batch = read(VIEWS / "dashboard" / "batch_automation.blade.php")
    norm = batch[batch.index("function normalizeCurationCandidate"):]
    norm = norm[: norm.index("\n    }\n")]
    assert "identity_tier: c.identity_tier || ev.tier" in norm
    assert "page_url: String(c.page_url || ev.page_url" in norm


def test_catalog_reject_with_research_persists_the_fresh_candidates():
    catalog = read(VIEWS / "dashboard" / "catalog.blade.php")
    submit = catalog[catalog.index("async function submitReject"):]
    submit = submit[: submit.index("function showRejectedState")]
    assert "await persistResearchCandidates(ctx, data, candidate.url)" in submit
    persist = catalog[catalog.index("async function persistResearchCandidates"):]
    persist = persist[: persist.index("\n    }\n")]
    assert "/api/v1/curation/save-candidates" in persist
    assert "c.url !== rejectedUrl" in persist
    assert "sku_key: data.sku_key || ctx.sku_key" in persist
