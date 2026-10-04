"""The dashboard shows the pick's review warnings: every code decide.route emits has an Arabic sentence.

Static checks on the review screen's labels (public/js/review/core.js, used by /catalog in both modes), plus the
label function run under Node (skipped when node is not installed).
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
REVIEW_JS = ROOT / "dashboard" / "public" / "js" / "review"
REVIEW_CORE = REVIEW_JS / "core.js"
# the review screen (catalog.blade.php loads public/js/review/*.js); the Run page shows no candidates
BLADES = [REVIEW_CORE]
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
    parts.append(re.search(r"const UNKNOWN_WARNING = .*?;\n", text).group(0))
    parts.append(re.search(r"function warningText\(code\) \{.*?\n    \}\n", text, re.DOTALL).group(0))
    return "\n".join(parts)


@pytest.mark.parametrize("path", BLADES, ids=lambda p: p.name)
def test_every_warning_code_and_variant_axis_has_an_arabic_label(path):
    text = read(path)
    # every code decide.route emits, and main.DUPLICATE_WARNING (the same image is published for another product)
    import main
    assert js_object_keys(text, "REVIEW_WARNING_LABELS") == set(decide.WARNING_CODES) | {
        main.DUPLICATE_WARNING[len("warn:"):]}
    assert set(variants_mod.lexicon().axes) <= js_object_keys(text, "VARIANT_AXIS_LABELS")
    # the notice is built with el()/textContent; warnings never reach innerHTML
    assert "warningText(" in text
    assert not re.search(r"innerHTML[^;\n]*(warningText|\.warnings\b)", text)
    # stored curation rows carry only reasons: the view reads 'warn:' reasons when 'warnings' is absent
    assert "startsWith('warn:')" in text


def test_the_pick_cards_show_the_notice():
    core = read(REVIEW_CORE)
    single = read(REVIEW_JS / "single.js")
    bulk = read(REVIEW_JS / "bulk.js")
    # «تأكد قبل الاعتماد» under the selected image: every warning of the pick, in Arabic
    cautions = core[core.index("function cautionsFor"):]
    assert "c.warnings.map(w => warningText(w))" in cautions[:cautions.index("\n    }\n")]
    checks = single[single.index("function checksCard"):]
    assert "R.cautionsFor(pick)" in checks[:checks.index("function altButton")]
    # every other image names its first warning (and lists the others), and each bulk card shows every warning
    note = core[core.index("function candidateNote"):]
    assert "warningText(c.warnings[0])" in note[:note.index("\n    }\n")]
    alt = single[single.index("function altButton"):]
    assert "c.warnings.slice(1).map(w => el('span', { className: 'rv-alt__warn', text: R.warningText(w) })" in \
        alt[:alt.index("function altsCard")]
    card = bulk[bulk.index("function card("):]
    assert "sel.warnings.map(w => R.warningText(w))" in card[:card.index("function render(")]


@pytest.mark.skipif(NODE is None, reason="node is not installed")
@pytest.mark.parametrize("path", BLADES, ids=lambda p: p.name)
def test_warning_sentences(path, tmp_path):
    codes = ["sheet_silent:fries_cut=thin", "sheet_silent:cheese_form=grated+shredded", "sheet_silent:new_axis=x",
             "vlm_unsure", "low_resolution", "chat_or_screenshot", "social_media", "foreign_store", "barcode_conflict",
             "mystery_code", "duplicate_image", "multipack_unit_image", "size_close:840g/850g", "size_close"]
    script = tmp_path / "labels.js"
    call = f"\nconsole.log(JSON.stringify({json.dumps(codes)}.map(warningText)));\n"
    script.write_text(label_code(read(path)) + call, encoding="utf-8")
    result = subprocess.run([NODE, str(script)], capture_output=True, text=True, timeout=60, encoding="utf-8")
    assert result.returncode == 0, result.stderr
    out = dict(zip(codes, json.loads(result.stdout)))
    assert out["sheet_silent:fries_cut=thin"] == "الشيت ما حدد طريقة التقطيع: thin"
    assert out["sheet_silent:cheese_form=grated+shredded"] == "الشيت ما حدد شكل الجبن: grated / shredded"
    assert out["sheet_silent:new_axis=x"] == "الشيت ما حدد النوع: x"
    assert out["vlm_unsure"] == "نموذج القراءة غير متأكد من المطابقة"
    assert out["low_resolution"].startswith("صورة منخفضة الدقة")
    assert out["chat_or_screenshot"] == "صورة من واتساب أو لقطة شاشة"
    assert "مواقع التواصل" in out["social_media"]
    assert out["foreign_store"].startswith("الصورة من متجر خارج الإمارات")
    assert out["barcode_conflict"] == "الباركود بالشيت مختلف عن باركود صفحة المتجر: تأكد من المنتج"
    # a code the page does not know yet gets a generic Arabic sentence; the raw code stays in the tooltip only
    assert out["mystery_code"] == "تحذير آخر على هالصورة: راجعها بعناية قبل الاعتماد"
    assert out["duplicate_image"].startswith("الصورة نفسها منشورة لمنتج آخر")
    # decide: one unit of a multipack SKU (one can of a 3-can pack)
    assert out["multipack_unit_image"] == "الصورة لعبوة وحدة، والمنتج باكيت من أكثر من حبة: تأكد إنها مناسبة"
    # decide: the printed size is the sheet's within the tolerance, not exactly ('840ge' on an 850G SKU)
    assert out["size_close:840g/850g"] == "الحجم على العلبة قريب من الشيت بس مش نفسه (840g مقابل 850g): تأكد وصحّح الشيت"
    assert out["size_close"] == "الحجم على العلبة قريب من الشيت بس مش نفسه: تأكد وصحّح الشيت"


def test_catalog_page_sends_the_reviewers_view_with_approve_reject_and_upload():
    page = "\n".join(read(REVIEW_JS / f"{name}.js") for name in ("core", "single", "bulk", "app"))
    assert "function reviewedCandidateView(" in page
    assert page.count("...reviewedCandidateView(") == 2            # approve and reject
    assert "['search_decision', ctx.search_decision" in page       # manual upload
    assert "R.uploadFields(job.ctx).forEach(([name, value]) => form.append(name" in page
    assert "target.search_decision = String(data.decision" in page
    api = (ROOT / "dashboard/app/Http/Controllers/ApiController.php").read_text(encoding="utf-8")
    assert "'search_decision'" in api                             # the upload whitelist forwards it


def test_approvals_send_the_pictures_warnings_so_a_store_spelling_can_be_learned():
    """catalog_match.learning: approving a 'brand_spelling:<spelling>' pick teaches the spelling. The live search
    screen keeps no stored candidates, so the review screen sends the warnings it showed with the decision."""
    core = (Path(__file__).resolve().parents[1] / "dashboard" / "public" / "js" / "review" / "core.js").read_text(
        encoding="utf-8")
    view = re.search(r"function reviewedCandidateView\(c, ctx\) \{.*?\n    \}\n", core, re.DOTALL).group(0)
    assert "candidate_warnings: (c.warnings || []).map(w => String(w)).join('|')" in view
    # 'brand_spelling:Rio Mare' shows the label of its code
    labels = re.search(r"const REVIEW_WARNING_LABELS = \{.*?\n    \};", core, re.DOTALL).group(0)
    assert "brand_spelling:" in labels
