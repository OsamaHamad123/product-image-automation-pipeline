"""Static and CLI-level checks for the Laravel dashboard and the launchers.

Everything here runs offline: file reads, `php -l`, `node --check` and small PHP CLI
harnesses around framework-free service classes. PHP or Node checks are skipped when
the binary is not installed.
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
from blade_scripts import asset_scripts, inline_scripts

ROOT = Path(__file__).resolve().parents[1]
DASH = ROOT / "dashboard"
VIEWS = DASH / "resources" / "views"
CONTROLLERS = DASH / "app" / "Http" / "Controllers"
SERVICES = DASH / "app" / "Services"
MIGRATION = DASH / "database" / "migrations" / "2026_10_01_000001_add_identity_and_evidence_columns.php"

LAUNCHERS = [
    ROOT / "setup_and_launch.ps1",
    ROOT / "setup_and_launch.bat",
    ROOT / "launch_desktop.ps1",
    ROOT / "launch_desktop.bat",
    ROOT / "start_all.bat",
]

CHANGED_PHP = [
    CONTROLLERS / "ApiController.php",
    CONTROLLERS / "ProductController.php",
    CONTROLLERS / "CurationController.php",
    SERVICES / "PythonBridge.php",
    SERVICES / "CandidateMatcher.php",
    SERVICES / "QueueStats.php",
    DASH / "routes" / "web.php",
    MIGRATION,
]

CHANGED_BLADES = [
    VIEWS / "dashboard" / "catalog.blade.php",
    VIEWS / "dashboard" / "batch_automation.blade.php",
    VIEWS / "dashboard" / "index.blade.php",
    VIEWS / "dashboard" / "settings.blade.php",
    VIEWS / "dashboard" / "diagnostics.blade.php",
    VIEWS / "layouts" / "laqta.blade.php",
]

# The review screen (catalog.blade.php) loads its JavaScript from files instead of an inline block.
REVIEW_JS = sorted((DASH / "public" / "js" / "review").glob("*.js"))

PHP = shutil.which("php")
NODE = shutil.which("node")


def review_page() -> str:
    """catalog.blade.php, its Blade shell and the review scripts it loads."""
    parts = [VIEWS / "dashboard" / "catalog.blade.php", VIEWS / "review" / "shell.blade.php"] + REVIEW_JS
    return "\n".join(read(p) for p in parts)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# php -l
# ---------------------------------------------------------------------------

def _all_dashboard_php():
    files = set(CHANGED_PHP)
    for sub in ("app", "routes", "database/migrations"):
        files.update((DASH / sub).rglob("*.php"))
    return sorted(files)


@pytest.mark.skipif(PHP is None, reason="php is not installed")
@pytest.mark.parametrize("path", _all_dashboard_php(), ids=lambda p: str(p.relative_to(ROOT)))
def test_php_lint(path):
    result = subprocess.run([PHP, "-l", str(path)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr


# ---------------------------------------------------------------------------
# Inline JavaScript in the changed views must parse
# ---------------------------------------------------------------------------

@pytest.mark.skipif(NODE is None, reason="node is not installed")
@pytest.mark.parametrize("path", [p for p in CHANGED_BLADES if p.name not in ("settings.blade.php", "catalog.blade.php")],
                         ids=lambda p: p.name)
def test_inline_js_parses(path, tmp_path):
    blocks = inline_scripts(read(path))
    # Laqta pages keep their script in public/js/<page>.js (loaded with asset()): those files must parse too.
    blocks += [read(DASH / "public" / "js" / name) for name in asset_scripts(read(path))]
    assert blocks, f"no inline <script> block found in {path.name}"
    for i, block in enumerate(blocks):
        # Blade echo tags are replaced by a string literal, as they would be after rendering.
        block = re.sub(r"\{\{.*?\}\}", "''", block)
        js = tmp_path / f"{path.stem}_{i}.js"
        js.write_text(block, encoding="utf-8")
        result = subprocess.run([NODE, "--check", str(js)], capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, f"{path.name} script block {i + 1}: {result.stderr}"


@pytest.mark.skipif(NODE is None, reason="node is not installed")
@pytest.mark.parametrize("path", REVIEW_JS, ids=lambda p: p.name)
def test_review_scripts_parse(path):
    """catalog.blade.php has no inline script: its JavaScript is public/js/review/*.js, which must parse."""
    assert len(REVIEW_JS) >= 6
    result = subprocess.run([NODE, "--check", str(path)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, f"{path.name}: {result.stderr}"


# ---------------------------------------------------------------------------
# Contract text checks
# ---------------------------------------------------------------------------

def test_contracts():
    catalog = review_page()
    batch = read(VIEWS / "dashboard" / "batch_automation.blade.php")
    index = read(VIEWS / "dashboard" / "index.blade.php")
    settings = read(VIEWS / "dashboard" / "settings.blade.php")
    routes = read(DASH / "routes" / "web.php")

    assert "% Match" not in batch
    assert "% Match" not in catalog

    for token in ("sku_key", "WRONG_VARIANT", "product_name_ar", "brand_ar"):
        assert token in catalog, token
    # The old page's category override came from a three-entry demo taxonomy that fell back to its first entry
    # for any product. The review screen sends no override: the published metadata keeps the pipeline's category.
    assert "category_l1_en" not in catalog and "taxonomyData" not in catalog

    assert "Array(512)" not in batch
    assert "0.98" not in batch
    assert "97.02" not in index
    assert "gemini-2.0-flash-lite" not in settings

    assert "fix-broken-image-link" not in routes
    assert "CatalogHealingController" not in routes

    for name in ("ApiController.php", "ProductController.php"):
        text = read(CONTROLLERS / name)
        assert "127.0.0.1:8001" not in text, name
        assert "f:\\automation" not in text.lower(), name
        assert "f:/automation" not in text.lower(), name
        assert "insert or replace" not in text.lower(), name

    for launcher in LAUNCHERS:
        text = read(launcher)
        assert "PYTHONUTF8" in text, launcher.name
        assert "fastapi_server.py" not in text, launcher.name
        assert "run_fastapi.bat" not in text, launcher.name


def test_catalog_reject_sends_the_candidate_bytes():
    """The catalog stores no curation rows, so the reject must carry the sha for the bridge's pHash."""
    catalog = review_page()
    body = catalog[catalog.index("function rejectBody("):]
    body = body[:body.index("\n    }\n")]
    assert "candidate_sha256: candidate.content_sha256" in body
    assert "R.rejectBody(ctx, candidate, reasonCode" in catalog and "R.rejectBody(ctx, sel, code" in catalog


def test_fake_flows_removed():
    for path in (
        CONTROLLERS / "CatalogHealingController.php",
        SERVICES / "SelectiveSearchService.php",
        SERVICES / "ImageValidationAndProxyService.php",
        DASH / "app" / "Jobs" / "SelfHealingProductImageJob.php",
        ROOT / "run_fastapi.bat",
    ):
        assert not path.exists(), f"{path.relative_to(ROOT)} should be deleted"

    routes = read(DASH / "routes" / "web.php")
    for gone in ("enterprise-metrics", "start-flask", "stop-flask", "curation/mutate"):
        assert gone not in routes, gone
    assert "function mutate" not in read(CONTROLLERS / "CurationController.php")
    assert "SelectiveSearchService" not in read(CONTROLLERS / "CurationController.php")

    batch = read(VIEWS / "dashboard" / "batch_automation.blade.php")
    for gone in ("BRAND_STYLE_MISMATCH", "EventSource", "curation/stream", "autoApproveThreshold", "clip_score"):
        assert gone not in batch, gone

    index = read(VIEWS / "dashboard" / "index.blade.php")
    for gone in ("BiRefNet", "GraphRAG", "Swarm", "GRPO", "0.923", "enterprise-metrics", "EventSource", "8001"):
        assert gone not in index, gone

    assert not (VIEWS / "layouts" / "layout.blade.php").exists()   # every page uses layouts/laqta
    layout = read(VIEWS / "layouts" / "laqta.blade.php")
    assert "fix-broken-image-link" not in layout
    assert "data-healing-active" not in layout

    catalog = review_page()
    # no CLIP score anywhere (the reject reason code CROP_MARGIN_CLIPPING is not one)
    assert not re.search(r"\bCLIP\b|clip_score", catalog)
    assert "compareOverlay" not in catalog  # the raw-vs-raw compare slider is gone


def test_views_do_not_inline_urls_in_handlers():
    """Candidate URLs/titles must not be interpolated into inline onclick handlers (stored XSS)."""
    for path in [VIEWS / "dashboard" / "catalog.blade.php", VIEWS / "dashboard" / "batch_automation.blade.php",
                 VIEWS / "review" / "shell.blade.php"] + REVIEW_JS:
        text = read(path)
        assert not re.search(r"onclick=\"[^\"]*\$\{[^}]*(url|title)", text, re.IGNORECASE), path.name
        assert not re.search(r"onclick=\"[^\"]*'\$\{", text), path.name
        assert "title=\"${c.title" not in text, path.name


def test_settings_never_echo_secrets():
    # The Laqta settings page is settings.blade.php plus one partial per tab (resources/views/settings).
    views = [VIEWS / "dashboard" / "settings.blade.php"] + sorted((VIEWS / "settings").glob("*.blade.php"))
    settings = "\n".join(read(p) for p in views)
    controller = read(CONTROLLERS / "SettingsController.php")
    providers = controller[controller.index("public const PROVIDERS"):controller.index("public const LEGACY_SECRETS")]
    for key in ("photoroom_api_key", "gemini_api_key", "cloudinary_api_key", "cloudinary_api_secret",
                "google_search_api_key", "serper_api_key", "proxy_url"):
        assert f"$settings['{key}']" not in settings, key
        assert f"['{key}']['value']" not in settings, key
        # every secret is still reachable: a literal field («متقدم») or one per provider key (keys tab loop)
        assert f'name="{key}"' in settings or f"'{key}' =>" in providers, key
    assert 'name="{{ $field }}"' in settings and 'type="password"' in settings
    assert 'name="search_engine"' not in settings and "data-engine" not in settings   # no v1 engine to pick

    # Not even the last characters of a stored key are printed any more (no mask on the page).
    assert "maskSecret" not in controller and "$masked" not in settings and "maskSecret" not in settings
    # A blank secret field keeps the stored key instead of overwriting it with a mask.
    assert re.search(r"if \(\$val === '' && !\$clear\)\s*\{\s*continue;", controller)


def test_migration_mirrors_identity_columns():
    text = read(MIGRATION)
    for column in ("sku_key", "payload_json", "worker_id", "lease_until", "failure_code", "trace_json",
                   "run_id", "reasons_json", "evidence_json", "vlm_json", "content_sha256", "identity_tier",
                   "verification_status", "approved_by"):
        assert f"'{column}'" in text, column
    assert "rejected_images" in text
    assert "hasColumn" in text and "hasTable" in text
    for status in ("human_approved", "auto_verified", "superseded", "legacy"):
        assert status in text


def test_retry_failures_uses_transactional_upsert():
    text = read(CONTROLLERS / "ApiController.php")
    body = text[text.index("public function retryFailures"):text.index("public function previewSheet")]
    assert "DB::transaction" in body
    assert "->upsert(" in body
    # the failure record is deleted inside the transaction, after the upsert
    assert body.index("->upsert(") < body.index("product_failures')->whereIn")


# ---------------------------------------------------------------------------
# Behaviour of the framework-free PHP services, exercised through the PHP CLI
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
def test_candidate_matcher_joins_by_sku_key():
    matcher = str(SERVICES / "CandidateMatcher.php").replace("\\", "/")
    products = [
        # valid GTIN-13 -> sku_key 06281007000116; its candidates were stored at row 99
        {"row_number": 4, "product_name": "Juice 250ml", "barcode": "6281007000116"},
        # legacy row: candidates without sku_key, none selected
        {"row_number": 5, "product_name": "Legacy", "barcode": ""},
        # invalid barcode: key found through a keyed candidate at the same row with the same name
        {"row_number": 6, "product_name": "Tomato Paste 400g", "barcode": "123"},
        # sheet link flagged needs_review, no candidates
        {"row_number": 7, "product_name": "Ghost", "barcode": "",
         "existing_image_link": "needs_review:https://x/sheet.jpg"},
        # key sent by Python wins
        {"row_number": 8, "product_name": "Keyed", "barcode": "", "sku_key": "feedfacefeedface"},
    ]
    rows = [
        {"id": 1, "row_number": 99, "product_name": "Juice 250ml", "image_url": "old.jpg", "sku_key": "06281007000116",
         "run_id": "r1", "is_selected": 1, "status": "preselected", "clip_score": 24},
        {"id": 2, "row_number": 99, "product_name": "Juice 250ml", "image_url": "s1.jpg", "sku_key": "06281007000116",
         "run_id": "r2", "is_selected": 0, "status": "eligible", "clip_score": 24,
         "reasons_json": "[\"size:conflict\"]", "evidence_json": "{\"brand\":\"masafi\"}", "vlm_json": "{\"decision\":\"UNSURE\"}"},
        {"id": 3, "row_number": 99, "product_name": "Juice 250ml", "image_url": "s2.jpg", "sku_key": "06281007000116",
         "run_id": "r2", "is_selected": 0, "status": "rejected"},
        {"id": 4, "row_number": 5, "product_name": "Legacy", "image_url": "l1.jpg", "sku_key": None, "run_id": None,
         "is_selected": 0, "status": "pending"},
        {"id": 5, "row_number": 5, "product_name": "Legacy", "image_url": "l2.jpg", "sku_key": None, "run_id": None,
         "is_selected": 0, "status": "pending"},
        # keyed candidate of another SKU parked at row 5 must not attach to row 5
        {"id": 6, "row_number": 5, "product_name": "Other", "image_url": "decoy.jpg", "sku_key": "06291000000013",
         "run_id": "r9", "is_selected": 1, "status": "preselected"},
        {"id": 7, "row_number": 6, "product_name": "Tomato Paste 400g", "image_url": "h1.jpg",
         "sku_key": "abcdef0123456789", "run_id": "r3", "is_selected": 1, "status": "preselected"},
        {"id": 8, "row_number": 2, "product_name": "Somewhere", "image_url": "k1.jpg", "sku_key": "feedfacefeedface",
         "run_id": "r4", "is_selected": 0, "status": "eligible"},
    ]
    script = f"""<?php
require '{matcher}';
use App\\Services\\CandidateMatcher;
$products = json_decode({json.dumps(json.dumps(products))}, true);
$rows = json_decode({json.dumps(json.dumps(rows))}, true);
$out = CandidateMatcher::attach($products, $rows);
echo json_encode([
    'out' => $out,
    'gtin' => [
        CandidateMatcher::gtin14('6281007000116'),
        CandidateMatcher::gtin14('6281007000117'),
        CandidateMatcher::gtin14('6.28101E+12'),
        CandidateMatcher::gtin14("'6281007000116"),
        CandidateMatcher::gtin14('٦٢٨١٠٠٧٠٠٠١١٦'),
        CandidateMatcher::gtin14('00000000'),
        CandidateMatcher::gtin14('96385074'),
    ],
]);
"""
    data = _run_php(script)
    out = {p["row_number"]: p for p in data["out"]}

    # sku_key join ignores the stale row number and keeps only the latest run
    juice = out[4]
    assert juice["sku_key"] == "06281007000116"
    assert [c["image_url"] for c in juice["curation_candidates"]] == ["s1.jpg", "s2.jpg"]
    assert juice["needs_review"] is True
    # nothing in the latest run is selected: no fallback to the first candidate
    assert juice["needs_review_url"] == "" and juice["preselected"] is False
    first = juice["curation_candidates"][0]
    assert first["reasons"] == ["size:conflict"] and first["evidence"] == {"brand": "masafi"}
    assert first["vlm"] == {"decision": "UNSURE"} and first["status"] == "eligible"
    assert "clip_score" not in first and "reasons_json" not in first

    # legacy rows join by row number only, and the keyed decoy stays out
    assert [c["image_url"] for c in out[5]["curation_candidates"]] == ["l1.jpg", "l2.jpg"]
    assert out[5]["needs_review_url"] == ""

    # hashed key recovered from the same row + same product name; preselected candidate is the review URL
    assert out[6]["sku_key"] == "abcdef0123456789"
    assert out[6]["needs_review_url"] == "h1.jpg" and out[6]["preselected"] is True

    # sheet 'needs_review:' link without candidates
    assert out[7]["needs_review"] is True and out[7]["needs_review_url"] == "https://x/sheet.jpg"
    assert out[7]["curation_candidates"] == []

    # sku_key from Python is used as-is
    assert [c["image_url"] for c in out[8]["curation_candidates"]] == ["k1.jpg"]

    assert data["gtin"] == [
        "06281007000116",   # valid GTIN-13
        None,               # bad check digit
        None,               # scientific notation lost digits
        "06281007000116",   # leading apostrophe from the sheet
        "06281007000116",   # Eastern-Arabic digits
        None,               # all zero
        "00000096385074",   # valid GTIN-8
    ]


@pytest.mark.skipif(PHP is None, reason="php is not installed")
def test_candidate_matcher_falls_back_when_the_key_differs():
    """The worker stored candidates under another sku_key (brand mapping changed or failed to load):
    same row + same product name still attaches them; another product at that row never does."""
    matcher = str(SERVICES / "CandidateMatcher.php").replace("\\", "/")
    products = [
        {"row_number": 12, "product_name": "Al Marai Fresh Milk Full Fat 1L", "barcode": "",
         "sku_key": "7cdf131b51639759"},
        {"row_number": 13, "product_name": "Tomato Paste 400g", "barcode": "", "sku_key": "1111111111111111"},
    ]
    rows = [
        {"id": 1, "row_number": 12, "product_name": "Al Marai Fresh Milk Full Fat 1L", "image_url": "m1.jpg",
         "sku_key": "4b5431eb5edc585c", "run_id": "r1", "is_selected": 1, "status": "preselected"},
        {"id": 2, "row_number": 13, "product_name": "Basmati Rice 5kg", "image_url": "decoy.jpg",
         "sku_key": "2222222222222222", "run_id": "r2", "is_selected": 1, "status": "preselected"},
    ]
    script = f"""<?php
require '{matcher}';
use App\\Services\\CandidateMatcher;
$products = json_decode({json.dumps(json.dumps(products))}, true);
$rows = json_decode({json.dumps(json.dumps(rows))}, true);
echo json_encode(CandidateMatcher::attach($products, $rows));
"""
    out = {p["row_number"]: p for p in _run_php(script)}
    assert [c["image_url"] for c in out[12]["curation_candidates"]] == ["m1.jpg"]
    assert out[12]["needs_review"] is True and out[12]["needs_review_url"] == "m1.jpg"
    assert out[13]["curation_candidates"] == [] and not out[13].get("needs_review_url")


@pytest.mark.skipif(PHP is None, reason="php is not installed")
def test_python_bridge_output_decoding_and_status_mapping():
    bridge = str(SERVICES / "PythonBridge.php").replace("\\", "/")
    script = f"""<?php
require '{bridge}';
use App\\Services\\PythonBridge;
$cases = [
    'clean' => "{{\\"status\\": \\"review\\", \\"decision\\": \\"REVIEW_UNSELECTED\\"}}\\n",
    'noise' => "warning: something\\nmore noise\\n{{\\"status\\": \\"not_found\\", \\"candidates\\": []}}\\n",
    'arabic' => "{{\\"status\\": \\"success\\", \\"brand\\": \\"المراعي\\"}}",
    'traceback' => "Traceback (most recent call last):\\n  File x\\nValueError: boom\\n",
    'empty' => "",
];
$decoded = [];
foreach ($cases as $k => $v) {{
    $decoded[$k] = PythonBridge::decodeOutput($v);
}}
$codes = [];
foreach (['success', 'review', 'not_found', 'provider_down', 'error', 'failed'] as $s) {{
    $codes[$s] = [PythonBridge::httpStatus(['status' => $s]), PythonBridge::isError(['status' => $s])];
}}
echo json_encode(['decoded' => $decoded, 'codes' => $codes, 'missing' => PythonBridge::httpStatus([])]);
"""
    data = _run_php(script)
    d = data["decoded"]
    assert d["clean"] == {"status": "review", "decision": "REVIEW_UNSELECTED"}
    assert d["noise"] == {"status": "not_found", "candidates": []}
    assert d["arabic"]["brand"] == "المراعي"
    assert d["traceback"] is None and d["empty"] is None
    codes = data["codes"]
    assert codes["success"] == [200, False]
    assert codes["review"] == [200, False]
    assert codes["not_found"] == [200, False]
    assert codes["provider_down"] == [503, False]
    assert codes["error"] == [500, True]
    assert codes["failed"] == [500, True]
    assert data["missing"] == 500


def test_launcher_keeps_the_dashboard_on_the_workers_mariadb():
    """Owner's launch log, 2026-10-03: 'Cannot index into a null array' at $Matches[1], then the dashboard .env was
    rewritten to SQLite. -match on Get-Content's line array filters lines and leaves $Matches empty, so the old block
    always took the SQLite path. Python only speaks MariaDB (pymysql): the dashboard must use the root .env DB_*."""
    ps1 = read(ROOT / "setup_and_launch.ps1")
    assert "DB_CONNECTION=sqlite" not in ps1 and "local_cache.db" not in ps1.replace("(local_cache.db)", "")
    block = ps1[ps1.index("$dbKeys = @("):ps1.index("# ----------------- 6.")]
    for key in ("DB_CONNECTION", "DB_HOST", "DB_PORT", "DB_DATABASE", "DB_USERNAME", "DB_PASSWORD"):
        assert f"'{key}'" in block, key
    assert "ReadAllLines($rootEnv)" in block and "ReadAllLines($dashboardEnv)" in block
    assert "UTF8Encoding($false)" in block                       # no BOM: Laravel would read '﻿APP_NAME'
    # $Matches is read only after a -match on a single line, never on a Get-Content array
    for i, line in enumerate(ps1.splitlines()):
        if "$Matches[" in line:
            context = "\n".join(ps1.splitlines()[max(0, i - 3):i])
            assert "Get-Content" not in context, line


def test_launcher_generates_the_app_key_only_when_missing():
    ps1 = read(ROOT / "setup_and_launch.ps1")
    check = ps1[ps1.index("# توليد مفتاح التطبيق"):ps1.index("key:generate")]
    assert "ReadAllText($dashboardEnv)" in check and "Get-Content" not in check


def test_launchers_pass_no_powershell_switches_to_native_programs():
    """'-ErrorAction' after a python/pip call is an argument to pip ('no such option: -E'), not to PowerShell."""
    ps1 = read(ROOT / "setup_and_launch.ps1")
    for line in ps1.splitlines():
        if line.lstrip().startswith("& $") and "-ErrorAction" in line:
            raise AssertionError(line)


def test_launcher_cache_cleanup_keeps_the_folders_gitignore():
    """Remove-Item '<dir>\\*' also deleted data/.gitignore at every launch, and the owner's next commit
    carried the deletion (twice, 2026-10-03). The cleanup must skip it, and the file must be in the repo."""
    ps1 = read(ROOT / "setup_and_launch.ps1")
    block = ps1[ps1.index("$laravelCacheDir = "):ps1.index("# ----------------- 5.")]
    assert 'Join-Path $laravelCacheDir "*"' not in block
    assert '$_.Name -ne ".gitignore"' in block
    kept = ROOT / "dashboard" / "storage" / "framework" / "cache" / "data" / ".gitignore"
    assert kept.read_text(encoding="utf-8").split() == ["*", "!.gitignore"]


def test_batch_titles_and_echoes_have_no_bare_ampersand():
    """'title Setup & Launcher' made cmd run 'Launcher' as a command (owner's log, 2026-10-03)."""
    for bat in sorted(ROOT.glob("*.bat")):
        for line in read(bat).splitlines():
            stripped = line.strip().lower()
            if stripped.startswith(("title ", "echo ")):
                assert not re.search(r"(?<!\^)&(?!&)", line.replace("&&", "")), (bat.name, line)
