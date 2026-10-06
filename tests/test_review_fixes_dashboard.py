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


REVIEW_JS = DASH / "public" / "js" / "review"


def review_js(*names) -> str:
    """The review screen's scripts (catalog.blade.php loads public/js/review/*.js)."""
    names = names or ("core", "ui", "jobs", "single", "bulk", "app")
    return "\n".join(read(REVIEW_JS / f"{n}.js") for n in names)


def _js_function(source: str, name: str) -> str:
    """Source of one top-level function of a review script (up to its closing brace at 4 spaces)."""
    start = source.index(f"function {name}(")
    return source[start:source.index("\n    }\n", start) + 6]


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
    # The batch page's review grid moved to the review screen (/catalog, public/js/review).
    core = review_js("core")

    # the identity the review screen binds to a product (and to its search results) carries the size identity
    ctx = _js_function(core, "productIdentity")
    for key in ("size", "sub_category", "origin"):
        assert re.search(rf"\b{key}:", ctx), key
    assert "size: prod.size" in ctx
    assert "sub_category: prod.sub_category" in ctx

    search = _js_function(core, "searchBody")
    reject = _js_function(core, "rejectBody")
    for body in (search, reject):
        for token in ("size: ctx.size", "sub_category: ctx.sub_category", "origin: ctx.origin"):
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
    layout = read(VIEWS / "layouts" / "laqta.blade.php")
    if name == "catalog":
        # the review screen: its Blade shell and the scripts that build the page
        text += read(VIEWS / "review" / "shell.blade.php") + review_js()
    ids = set(re.findall(r'id="([^"{]+)"', text + layout)) | set(re.findall(r"id:\s*'([^']+)'", text))
    refs = set(re.findall(r"getElementById\('([^'\s]+)'\)", text))
    assert not refs - ids, sorted(refs - ids)


def test_batch_progress_counts_lives_in_the_progress_panel():
    # Home (Laqta): the current run's counts are written by public/js/home.js into the live block of the
    # «آخر تشغيل» card, which exists in the page.
    index = read(VIEWS / "dashboard" / "index.blade.php")
    panel = index[index.index('data-home="lastrun-live"'):index.index('data-home="lastrun-summary"')]
    assert 'data-home="live-counts"' in panel
    home = read(DASH / "public" / "js" / "home.js")
    assert "$('live-counts')" in home


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
    # The Run page (Laqta) no longer has a background-removal selector (the worker uses the configured method);
    # everything it does post must reach main.load_run_config, so no choice on the page is silently ignored.
    run_js = read(DASH / "public" / "js" / "run.js")
    body = run_js[run_js.index("function runBody(form)"):]
    body = body[: body.index("\n    }\n")]
    keys = re.findall(r"^\s*([A-Za-z_]+):", body, re.MULTILINE)
    assert keys == ["row_filter", "brand_filter", "forceOverwrite", "skipCache"]
    import inspect
    import main
    loader = inspect.getsource(main.load_run_config)
    for key in keys:
        assert f'"{key}"' in loader, key
    assert "fetchJson('/api/run-all', { method: 'POST', body: body })" in run_js


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


def test_catalog_reject_with_research_leaves_saving_the_fresh_candidates_to_the_server():
    """Contract C2: reject_image with research=true saves the new candidates itself and puts the product back to
    ready_for_review (candidates_saved). The page saved them a second time through save-candidates, from its own copy."""
    single = review_js("single")
    submit = single[single.index("async function rejectCurrent"):]
    submit = submit[: submit.index("function skip()")]
    assert "persistResearchCandidates" not in single
    assert "save-candidates" not in single and "saveCandidates" not in single
    assert "requeueForReview(item, data, candidate.url)" in submit and "savedCount(data)" in submit


@pytest.mark.skipif(PHP is None, reason="php is not installed")
def test_save_candidates_keeps_the_provider_query_and_phash():
    """The Python writer stores where a candidate came from (provider, query id) and its pHash; the dashboard's
    save-candidates keeps them too, cut to the column widths, and leaves them NULL when unknown."""
    service = str(DASH / "app" / "Services" / "CandidateRow.php").replace("\\", "/")
    candidates = [
        {"url": "https://lulu.ae/a.jpg", "provider": "serper_shopping", "query_id": "X2", "phash": "c3c3a5a55a5a3c3c"},
        {"url": "https://lulu.ae/b.jpg", "provider": "  ", "query_id": None},
        {"url": "https://lulu.ae/c.jpg", "provider": "p" * 40, "query_id": "Q" * 20},
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
    assert (rows[0]["provider"], rows[0]["query_id"], rows[0]["phash"]) == ("serper_shopping", "X2", "c3c3a5a55a5a3c3c")
    assert (rows[1]["provider"], rows[1]["query_id"], rows[1]["phash"]) == (None, None, None)
    assert len(rows[2]["provider"]) == 32 and len(rows[2]["query_id"]) == 16
