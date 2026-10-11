"""The review screen's copy pass (the earlier pass skipped it): short lines, role words, one dialect.

* Long texts: no visible string in the review scripts is over 25 words. Where the old line said more (the reject
  reasons note, the preview card, the bulk reject dialog, the «background not removed» box), the short line keeps the
  safety-relevant part (what is never touched) and the rest sits in a native <details class="rv-details"> «تفاصيل»;
  «ليش ما انختارت» under each image is one clause, the size note goes to its tooltip.
* Vendor names: the cut-check labels and buttons say what the service does («عزل الخلفية»، «القص المحلي»،
  «قارئ الملصق»، «رفع الصورة»); the name stays in a tooltip or in «تفاصيل».
* One dialect: the MSA phrases the screen mixed in («لم تُعزل»، «وجدها البحث»، «لن نقترحها»، «ما زالت»…) are gone.
"""

import json
import re
import subprocess
from pathlib import Path

import pytest

from laqta_review_harness import NODE, run

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "dashboard" / "public" / "js" / "review"
FILES = ["app.js", "single.js", "bulk.js", "cutout.js", "core.js", "jobs.js", "ui.js"]
MAX_WORDS = 25
NEEDS_NODE = pytest.mark.skipif(NODE is None, reason="node is not installed")

LITERAL = re.compile(r"'(?:[^'\\\n]|\\.)*'|`(?:[^`\\]|\\.)*`")
ARABIC = re.compile("[؀-ۿ]")


def literals(name):
    """Arabic string literals of a review script outside comments: [(line, text)]."""
    src = (JS / name).read_text(encoding="utf-8")
    lines = src.split("\n")
    out = []
    for m in LITERAL.finditer(src):
        text = m.group(0)[1:-1]
        line = src.count("\n", 0, m.start()) + 1
        head = lines[line - 1].lstrip()
        if head.startswith("//") or head.startswith("*") or head.startswith("/*") or not ARABIC.search(text):
            continue
        out.append((line, text))
    return out


def words(text):
    return len(re.sub(r"\$\{[^}]*\}", "X", text).split())


def all_literals():
    return [(name, line, text) for name in FILES for line, text in literals(name)]


def test_no_visible_string_is_over_the_word_limit():
    long = [(n, l, words(t)) for n, l, t in all_literals() if words(t) > MAX_WORDS]
    assert not long, long


@pytest.mark.parametrize("phrase", ["لم تُعزل", "وجدها البحث", "لن نقترحها", "ما زالت", "اعتُمدت صورة", "لم تُعتمد",
                                    "اعتُمدت هذه الصورة", "تستطيع", "إن كانت الصورة", "تجدها في"])
def test_the_msa_phrases_are_gone(phrase):
    hits = [(n, l) for n, l, t in all_literals() if phrase in t]
    assert not hits, (phrase, hits)


def test_the_levantine_replacements_are_there():
    text = " ".join(t for _, _, t in all_literals())
    for phrase in ("الخلفية ما انعزلت", "صور ثانية لقاها البحث", "وما رح نقترحها", "باقي الصور لسا للمراجعة"):
        assert phrase in text, phrase


def test_the_label_reader_is_named_by_its_role():
    hits = [(n, l, t) for n, l, t in all_literals() if "نموذج القراءة" in t or "نموذج قراءة" in t]
    assert not hits, hits
    single = " ".join(t for _, t in literals("single.js"))
    assert "قارئ الملصق ما قرأ هالصورة" in single and "قارئ الملصق مش متاح هلق:" in single


def test_the_cut_check_labels_say_what_the_service_does():
    labels = " | ".join(t for _, t in literals("cutout.js"))
    for old in ("PhotoRoom مش متأكد", "أعد القص بـPhotoRoom", "عم نقص بـPhotoRoom", "PhotoRoom بيكلّف", "(BiRefNet)",
                "PhotoRoom موقوف", "PhotoRoom مش جاهز"):
        assert old not in labels, old
    for new in ("العزل مش متأكد", "أعد عزل الخلفية", "عم نعزل الخلفية", "إعادة العزل بتكلّف طلب عزل واحد بالعادة.",
                "بالقص المحلي", "بالقص المحلي البسيط"):
        assert new in labels, new
    src = (JS / "cutout.js").read_text(encoding="utf-8")
    # the vendor names are kept, as a tooltip only
    assert "PROVIDER_NAMES = { photoroom: 'PhotoRoom', rembg: 'rembg · BiRefNet', remove_bg_api: 'remove.bg', grabcut: 'GrabCut' }" in src
    assert "title: PROVIDER_NAMES[attempt.provider] || null" in src
    assert "title: prNote || 'بـPhotoRoom'" in src


def test_cloudinary_is_only_in_the_details_of_the_preview_card():
    hits = [(l, t) for l, t in literals("single.js") if "Cloudinary" in t]
    assert len(hits) == 1
    src = (JS / "single.js").read_text(encoding="utf-8").split("\n")
    assert src[hits[0][0] - 1].lstrip().startswith("moreDetails(")
    core = " ".join(t for _, t in literals("core.js"))
    assert "Cloudinary" not in core


# ---------------------------------------------------------------------------
# The short lines on the page, the rest under «تفاصيل»
# ---------------------------------------------------------------------------

EV = {"brand": True, "size": "match", "gtin": "match", "source_class": "uae_retailer", "tier": 1}
VLM = {"decision": "MATCH", "brand_match": "yes", "size_match": "yes", "variant_match": "yes", "view": "front_packshot"}


def product(row, name):
    return {"row_number": row, "product_name": name, "brand": "Almarai", "barcode": "", "sku_key": f"key-{row}",
            "size": "1L", "product_name_ar": "", "brand_ar": "", "category": "Dairy", "existing_image_link": "",
            "needs_review": True, "preselected": True,
            "curation_candidates": [{"image_url": f"https://www.carrefouruae.com/p{row}.jpg", "status": "preselected",
                                     "is_selected": 1, "title": name, "reasons": [], "evidence": dict(EV), "vlm": VLM}]}


def fixture(products):
    rows = [{"row_number": p["row_number"], "sku_key": p["sku_key"], "status": "ready_for_review", "failure_code": None,
             "product_name": p["product_name"], "brand": p["brand"], "updated_at": "2026-10-05 10:00:00"} for p in products]
    return {"products": products, "queue": {"status": "success", "ready_for_review": len(rows), "rows": rows}}


TWO = [product(60, "Almarai Milk 1L"), product(61, "Almarai Laban 1L")]

VISIBLE = r"""
// what the page shows before «تفاصيل» is opened
function shown(node) {
    if (!node) return '';
    if (node.nodeType === 3) return node.data;
    if (node.hidden || node.tagName === 'SVG') return '';
    if (node.tagName === 'DETAILS') return node.querySelector('summary').textContent;
    return node.childNodes.map(shown).join('');
}
const detailsOf = node => node.querySelectorAll('details').map(d => [d.getAttribute('class'), d.querySelector('summary').textContent,
                                                                     d.textContent]);
"""


def js(value):
    return json.dumps(value, ensure_ascii=False)


def page(scenario, tmp_path, config):
    return run(VISIBLE + f"await boot({js(config)});\nawait flush();\n" + scenario, tmp_path, fixture(TWO))


def count_words(text):
    return len(text.split())


@NEEDS_NODE
def test_the_reject_reasons_note_and_the_preview_card_are_short(tmp_path):
    out = page(r"""
openRow(60);
await flush();
const preview = ws().querySelector('.rv-preview');
out.preview = [shown(preview.querySelector('.rv-preview__text')), detailsOf(preview)];
press('x');
await flush();
const box = document.querySelector('.rv-reasons');
out.notes = box.querySelectorAll('span.rv-reasons__note').map(n => n.textContent);
out.details = detailsOf(box);
""", tmp_path, {"row": 60})
    text, details = out["preview"]
    assert "Cloudinary" not in text and "هيك هي بمصدرها، مش النتيجة النهائية." in text
    assert len(details) == 1 and details[0][0] == "rv-details" and details[0][1] == "تفاصيل"
    assert "800×800" in details[0][2] and "Cloudinary" in details[0][2]
    assert out["notes"] == ["بنسجّل السبب وما منرجع نقترحها لهالمنتج. الصورة المعتمدة قبل (إذا في) ما بتنلمس."]
    assert out["details"] == [["rv-details rv-reasons__note", "تفاصيل",
                               "تفاصيلإذا ضل إله صور ثانية بيضل بانتظار مراجعتك فيها، وإلا بيرجع للطابور."]]


@NEEDS_NODE
def test_the_bulk_reject_dialog_is_one_short_line_and_enter_on_details_does_not_reject(tmp_path):
    out = page(r"""
document.getElementById('rvBulkReject').click();
await flush();
const dialog = document.getElementById('rvDialog');
out.line = dialog.querySelector('.rv-dialog__text').textContent;
out.details = detailsOf(dialog);
press('2');                                                  // a reason is chosen
const summary = dialog.querySelector('summary');
summary.focus();
press('Enter');                                              // on «تفاصيل»: the browser opens it, no reject
await flush();
out.after = [!dialog.hidden, S().jobs.state().heldCount, requests('/api/reject_image').length];
""", tmp_path, {"mode": "bulk", "approveUndoMs": 5000})
    assert out["line"] == "رح نرفض الصورة المقترحة لـ 2 منتجات. الصور المعتمدة قبل ما بتنلمس."
    assert count_words(out["line"]) <= 12
    assert len(out["details"]) == 1 and out["details"][0][:2] == ["rv-details", "تفاصيل"]
    assert "بيضل بانتظار مراجعتك فيها" in out["details"][0][2] and "بيرجع للطابور" in out["details"][0][2]
    assert out["after"] == [True, 0, 0]


@NEEDS_NODE
def test_why_not_picked_is_one_clause_and_the_size_note_is_in_the_tooltip(tmp_path):
    out = run(r"""
const prod = { size: '', product_name: 'Almarai Milk' };
const c = R.normalizeCandidate({ image_url: 'https://x.ae/a.jpg', status: 'eligible', vlm: { decision: 'UNSURE' },
                                 identity_tier: '2', evidence: { size: 'match' } });
out.why = R.whyNotPicked(c, prod);
""", tmp_path, fixture(TWO))
    assert out["why"] == {"code": "vlm_unsure", "tone": "warning", "text": "قارئ الملصق ما تأكد", "more": "الشيت ما فيه حجم"}
    src = (JS / "single.js").read_text(encoding="utf-8")
    assert "title: [why.more, why.code].filter(Boolean).join(' · ')" in src


@NEEDS_NODE
def test_the_new_cut_note_names_the_role_not_the_vendor():
    script = ("globalThis.window = globalThis;\n" + (JS / "ui.js").read_text(encoding="utf-8") + "\n"
              + (JS / "cutout.js").read_text(encoding="utf-8") + r"""
const C = window.LaqtaReview.cutout;
console.log(JSON.stringify({ texts: ['photoroom', 'rembg', 'grabcut', 'remove_bg_api', 'mystery']
    .map(p => C.tryText({ provider: p, source: 'candidate', flags: [], can_apply: true })) }));
""")
    res = subprocess.run([NODE, "-"], input=script, capture_output=True, text=True, timeout=60, encoding="utf-8")
    assert res.returncode == 0, res.stderr
    out = json.loads(res.stdout.strip().splitlines()[-1])
    assert out["texts"] == ["قص جديد بعزل الخلفية من الصورة الأصلية المحفوظة. بلا ملاحظات.",
                            "قص جديد بالقص المحلي من الصورة الأصلية المحفوظة. بلا ملاحظات.",
                            "قص جديد بالقص المحلي البسيط من الصورة الأصلية المحفوظة. بلا ملاحظات.",
                            "قص جديد بعزل الخلفية من الصورة الأصلية المحفوظة. بلا ملاحظات.",
                            "قص جديد من الصورة الأصلية المحفوظة. بلا ملاحظات."]
    for text in out["texts"]:
        assert not re.search(r"PhotoRoom|BiRefNet|GrabCut|rembg|remove\.bg", text)
