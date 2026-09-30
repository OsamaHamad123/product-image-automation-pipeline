"""The dashboard shows the pick's review warnings: every code decide.route emits has an Arabic sentence.

Static checks on catalog.blade.php and batch_automation.blade.php, plus the label function run
under Node (skipped when node is not installed).
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from catalog_match import decide
from catalog_match import variants as variants_mod

ROOT = Path(__file__).resolve().parents[1]
VIEWS = ROOT / "dashboard" / "resources" / "views" / "dashboard"
BLADES = [VIEWS / "catalog.blade.php", VIEWS / "batch_automation.blade.php"]
NODE = shutil.which("node")


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def js_object_keys(text: str, name: str):
    m = re.search(r"const " + name + r" = \{(.*?)\n    \};", text, re.DOTALL)
    assert m, f"{name} not found"
    return set(re.findall(r"^\s*([a-z_]+):", m.group(1), re.MULTILINE))


def label_code(text: str) -> str:
    """The two label tables and warningText() as they appear in the view."""
    parts = []
    for name in ("REVIEW_WARNING_LABELS", "VARIANT_AXIS_LABELS"):
        parts.append(re.search(r"const " + name + r" = \{.*?\n    \};", text, re.DOTALL).group(0))
    parts.append(re.search(r"function warningText\(code\) \{.*?\n    \}\n", text, re.DOTALL).group(0))
    return "\n".join(parts)


@pytest.mark.parametrize("path", BLADES, ids=lambda p: p.name)
def test_every_warning_code_and_variant_axis_has_an_arabic_label(path):
    text = read(path)
    assert js_object_keys(text, "REVIEW_WARNING_LABELS") == set(decide.WARNING_CODES)
    assert set(variants_mod.lexicon().axes) <= js_object_keys(text, "VARIANT_AXIS_LABELS")
    # the notice is built with el()/textContent; warnings never reach innerHTML
    assert "warningText(" in text
    assert not re.search(r"innerHTML[^;\n]*(warningText|\.warnings\b)", text)
    # stored curation rows carry only reasons: the view reads 'warn:' reasons when 'warnings' is absent
    assert "startsWith('warn:')" in text


def test_the_pick_cards_show_the_notice():
    catalog = read(VIEWS / "catalog.blade.php")
    card = catalog[catalog.index("function buildCandidateCard"):]
    assert "renderWarnings(c)" in card[:card.index("function renderCandidatesGrid")]
    recommended = catalog[catalog.index("function renderRecommendedCard"):]
    assert "renderWarnings(c)" in recommended[:recommended.index("function showProcessedPreview")]
    batch = read(VIEWS / "batch_automation.blade.php")
    row = batch[batch.index("function buildCurationRow"):]
    assert "rowWarningsEl(selected)" in row[:row.index("function renderBatchCurationGrid")]
    # a reviewer's other choice replaces the notice
    select = batch[batch.index("function selectCurationThumb"):]
    assert "rowWarningsEl(chosen)" in select[:select.index("openBatchRejectModal")]


@pytest.mark.skipif(NODE is None, reason="node is not installed")
@pytest.mark.parametrize("path", BLADES, ids=lambda p: p.name)
def test_warning_sentences(path, tmp_path):
    codes = ["sheet_silent:fries_cut=thin", "sheet_silent:cheese_form=grated+shredded", "sheet_silent:new_axis=x",
             "vlm_unsure", "low_resolution", "chat_or_screenshot", "social_media", "foreign_store", "mystery_code"]
    script = tmp_path / "labels.js"
    call = f"\nconsole.log(JSON.stringify({json.dumps(codes)}.map(warningText)));\n"
    script.write_text(label_code(read(path)) + call, encoding="utf-8")
    result = subprocess.run([NODE, str(script)], capture_output=True, text=True, timeout=60, encoding="utf-8")
    assert result.returncode == 0, result.stderr
    out = dict(zip(codes, json.loads(result.stdout)))
    assert out["sheet_silent:fries_cut=thin"] == "الشيت ما حدد طريقة التقطيع: thin"
    assert out["sheet_silent:cheese_form=grated+shredded"] == "الشيت ما حدد شكل الجبن: grated / shredded"
    assert out["sheet_silent:new_axis=x"] == "الشيت ما حدد النوع: x"
    assert out["vlm_unsure"] == "Gemini غير متأكد من المطابقة"
    assert out["low_resolution"].startswith("صورة منخفضة الدقة")
    assert out["chat_or_screenshot"] == "صورة من واتساب أو لقطة شاشة"
    assert "مواقع التواصل" in out["social_media"]
    assert out["foreign_store"].startswith("الصورة من متجر خارج الإمارات")
    assert out["mystery_code"] == "mystery_code"          # unknown codes are shown raw


def test_catalog_page_sends_the_reviewers_view_with_approve_reject_and_upload():
    page = (ROOT / "dashboard/resources/views/dashboard/catalog.blade.php").read_text(encoding="utf-8")
    assert "function reviewedCandidateView(" in page
    assert page.count("...reviewedCandidateView(") == 2            # approve and reject
    assert "formData.append('search_decision'" in page            # manual upload
    assert "dataset.searchDecision = String(data.decision" in page
    api = (ROOT / "dashboard/app/Http/Controllers/ApiController.php").read_text(encoding="utf-8")
    assert "'search_decision'" in api                             # the upload whitelist forwards it
