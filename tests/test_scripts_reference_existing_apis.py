"""Standalone scripts are not imported by the app, so a renamed or deleted function breaks them silently
(scripts/smoke_live.py called google_sheets.get_product_columns_indices after it was removed). This checks
statically that every `module.attribute` a script uses on a repository module still exists."""

import ast
import importlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
REPO_MODULES = {p.stem for p in ROOT.glob("*.py")} | {"catalog_match"}
SCRIPTS = sorted(ROOT.glob("scripts/*.py")) + [ROOT / "verify_cloud_services.py"]


def _repo_aliases(tree):
    """Local name -> dotted repository module, for `import x`, `import x as y` and `from catalog_match import z`."""
    aliases = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name.split(".")[0] in REPO_MODULES:
                    aliases[a.asname or a.name.split(".")[0]] = a.name if a.asname else a.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[0] in REPO_MODULES:
            for a in node.names:
                candidate = f"{node.module}.{a.name}"
                try:
                    importlib.import_module(candidate)
                except ImportError:
                    continue
                aliases[a.asname or a.name] = candidate
    return aliases


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_script_uses_only_existing_attributes(script):
    tree = ast.parse(script.read_text(encoding="utf-8"))
    aliases = _repo_aliases(tree)
    missing = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in aliases:
            module = importlib.import_module(aliases[node.value.id])
            if isinstance(node.ctx, ast.Load) and not hasattr(module, node.attr):
                missing.append(f"{aliases[node.value.id]}.{node.attr} (line {node.lineno})")
    assert not missing, f"{script.name} uses attributes that no longer exist: {missing}"
