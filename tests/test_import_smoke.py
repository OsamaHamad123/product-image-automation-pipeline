"""Import smoke tests: the entry points import cleanly, verification_layer and the v1 search engine are gone,
cli_bridge survives a legacy Windows console encoding, and the suite collects without errors."""

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _offline_env(**extra):
    env = dict(os.environ)
    env.update(extra)
    # nothing in an import may need a live service: point the database at a closed local port
    env["DB_PORT"] = "1"
    env["DB_DATABASE"] = "automation_test"
    env.pop("PYTHONUTF8", None)
    return env


def test_entry_points_import(offline):
    import main
    import cli_bridge
    import image_search

    assert callable(main.run_worker_mode) and callable(cli_bridge.action_reject_image)
    assert callable(image_search.search_best_product_image)


def test_verification_layer_is_gone():
    vl = ROOT / "verification_layer"
    # git leaves untracked __pycache__ folders behind after a pull; no source file may remain
    assert not vl.exists() or not any(vl.rglob("*.py")), "verification_layer must be deleted"
    for retired in ("celery_config.py", "distributed_lock.py", "catalog_dedup.py", "self_healing.py", "google_drive.py"):
        assert not (ROOT / retired).exists(), retired


def test_the_v1_search_engine_is_gone():
    """catalog_match is the only engine: the v1 modules, the dev-only HTTP wrapper and the engine switch are deleted."""
    import config
    import image_search

    for retired in ("aesthetics_engine.py", "image_quality_gatekeeper.py", "query_refiner.py", "fastapi_server.py"):
        assert not (ROOT / retired).exists(), retired
    for name in ("search_best_product_image_v1", "search_best_product_image_v2", "ParallelConsensusScraper",
                 "evaluate_and_choose_best_image", "validate_image_via_gemini_vision"):
        assert not hasattr(image_search, name), name
    assert not hasattr(config, "SEARCH_ENGINE")
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8").split()
    for package in ("fastapi", "uvicorn[standard]", "aiohttp"):
        assert package not in requirements, package


def test_cli_bridge_imports_under_cp1256():
    proc = subprocess.run([sys.executable, "-c", "import cli_bridge; print('ok \\u2713 \\u062d\\u0644\\u064a\\u0628')"],
                          cwd=str(ROOT), env=_offline_env(PYTHONIOENCODING="cp1256"),
                          capture_output=True, timeout=180)
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")[-2000:]


def test_cli_bridge_script_prints_one_json_document(tmp_path):
    proc = subprocess.run([sys.executable, str(ROOT / "cli_bridge.py"), "no-such-action"],
                          cwd=str(tmp_path), env=_offline_env(PYTHONIOENCODING="cp1256"),
                          capture_output=True, timeout=180)
    lines = [line for line in proc.stdout.decode("utf-8", "replace").splitlines() if line.strip()]
    assert len(lines) == 1, proc.stdout
    assert json.loads(lines[0])["status"] == "error"


def test_collect_only_has_no_errors():
    proc = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider", "tests"],
                          cwd=str(ROOT), env=_offline_env(), capture_output=True, timeout=300)
    out = proc.stdout.decode("utf-8", "replace")
    assert proc.returncode == 0, out[-3000:]
    summary = [line for line in out.splitlines() if line.strip()][-1]
    assert "error" not in summary.lower(), summary
