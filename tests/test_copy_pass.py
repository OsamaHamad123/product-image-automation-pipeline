"""The owner-facing copy pass (Phase 0 of the redesign): short hints, vendor names in second place, one primary action.

- Long hints: every hint, intro and note the owner reads on Settings, Run, Health and Setup is one short line; what
  used to follow it sits in a native <details> («تفاصيل»), and the safety-relevant sentences (cost, what is never
  overwritten, what is never touched) are still on the page.
- Vendor names: the titles say what a service does (البحث عن الصور، عزل الخلفية، قارئ الملصق، رفع الصور) and keep
  the provider's name in a muted, secondary place; the keys rows still name the provider whose key goes there.
- One primary action: the Home header's review link is secondary and steps aside while «شو الخطوة الجاية؟» points to
  the same list; the Run page's new-run card has one primary (ابدأ التشغيل); the setup footer has none.
- Page headers: Health and Setup use <x-lq.page-header>; Home keeps its own markup (its live hooks sit on the eyebrow
  and the title, which the component has no slot for).
- Health: «روح على» sits under the «تفاصيل متقدمة» bar, not above it. Setup says «التقدّم محفوظ» once.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DASH = ROOT / "dashboard"
VIEWS = DASH / "resources" / "views"
PAGES = VIEWS / "dashboard"
SETTINGS = VIEWS / "settings"
CSS = DASH / "public" / "css"
JS = DASH / "public" / "js"
CONTROLLERS = DASH / "app" / "Http" / "Controllers"

INDEX, DIAG, SETUP = (PAGES / f"{n}.blade.php" for n in ("index", "diagnostics", "setup"))
BATCH = INDEX                     # «اليوم»: the run panel and the sheet tools moved into the Home view
HOME_CARD = VIEWS / "setup" / "home_card.blade.php"
PAGE_CSS = {INDEX: ("home.css", "run.css"), DIAG: ("health.css",), SETUP: ("setup.css",), HOME_CARD: ("home.css",)}
SETTINGS_VIEWS = sorted(SETTINGS.glob("*.blade.php"))
COPY_VIEWS = [DIAG, INDEX, SETUP, HOME_CARD] + SETTINGS_VIEWS

NODE = shutil.which("node")
NEEDS_NODE = pytest.mark.skipif(NODE is None, reason="node is not installed")

# the elements that carry a hint, an intro or a note under a field or a card
HINT_CLASSES = ("lq-field__hint", "lq-settings-card__intro", "lq-run-advanced__hint", "lq-run-stuck__what",
                "lq-run-export__text", "lq-health-publish__cost", "lq-health__check-note", "lq-card__meta",
                "lq-setup-note", "lq-autopub__hint", "lq-health-publish__desc")
MAX_WORDS = 25
VENDORS = ("Serper", "Gemini", "Claude", "PhotoRoom", "Cloudinary", "SerpApi", "remove.bg")


def read(path):
    return path.read_text(encoding="utf-8")


def _without_details(text):
    return re.sub(r"<details\b.*?</details>", "", text, flags=re.S)


def _words(html):
    text = re.sub(r"\{\{--.*?--\}\}", " ", html, flags=re.S)
    text = re.sub(r"\{\{.*?\}\}", " X ", text, flags=re.S)
    text = re.sub(r"@\w+(\s*\([^)]*\))?", " ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    return [w for w in text.split() if re.search(r"\w", w)]


def _hints(text):
    pattern = r'<(p|span) class="[^"]*\b(%s)\b[^"]*"[^>]*>(.*?)</\1>' % "|".join(map(re.escape, HINT_CLASSES))
    return [(m.group(2), m.group(3)) for m in re.finditer(pattern, _without_details(text), re.S)]


def _css_classes(*paths):
    classes = set()
    for p in paths:
        css = re.sub(r"/\*.*?\*/", "", read(p), flags=re.S)
        classes |= set(re.findall(r"\.(lq-[a-z0-9]+(?:(?:__|--|-)[a-z0-9]+)*)", css))
    return classes


# ---------------------------------------------------------------------------
# One short line, the rest under «تفاصيل»
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("view", COPY_VIEWS, ids=lambda p: p.name)
def test_every_visible_hint_is_one_short_line(view):
    for cls, inner in _hints(read(view)):
        words = _words(inner)
        assert len(words) <= MAX_WORDS, (view.name, cls, len(words), " ".join(words)[:120])


@pytest.mark.parametrize("view", COPY_VIEWS, ids=lambda p: p.name)
def test_the_disclosures_are_native_and_use_defined_classes(view):
    text = read(view)
    defined = _css_classes(CSS / "laqta.css", *(CSS / "pages" / n for n in PAGE_CSS.get(view, ("settings.css",))))
    for block in re.findall(r"<details\b.*?</details>", text, flags=re.S):
        assert "<summary" in block, (view.name, block[:80])
        for cls in re.findall(r'class="([^"]+)"', block):
            for name in cls.split():
                if name.startswith("lq-"):
                    assert name in defined, (view.name, name)
    assert "lq-more" not in text                      # not a class the stylesheet knows


def test_the_long_hints_moved_under_details():
    for view, short, detail in (
        (SETTINGS / "processing.blade.php", "إذا خلص رصيد خدمة العزل", "GrabCut ما بيشتغل تلقائياً أبداً"),
        (SETTINGS / "models.blade.php", "والقوي بياخد نظرة تانية بس لما الأساسي مش متأكد",
         "النظرة التانية ما بتقدر تقلب صورة مرفوضة لمقبولة"),
        (SETTINGS / "advanced.blade.php", "وصور الصفحات ما بتنشر تلقائياً أبداً: بتستنى مراجعتك",
         "Google Shopping"),
        (SETTINGS / "auto_publish.blade.php", "بس بعد ما تثبت دقتها بمراجعاتك", "قارئ الملصق (Gemini) أكّدها"),
        (BATCH, "وما بيحذف ولا منتج ولا قرار مراجعة", "ولا أي اقتراح أو قرار مراجعة"),
        (BATCH, "ما بيكلّف شي وما فيه أي مفتاح أو كلمة سر", "أفضل 8 صور مع أدلتها"),
        (DIAG, "ممكن يكلّف طلب عزل خلفية واحد", "بتنرفع على Cloudinary وبتنمسح"),
        (DIAG, "بدون ما يغيّر شي", "الصف اللي غيّرت صورته بإيدك ما منلمسه"),
        (SETUP, "ما بيكتب بالشيت وما بيكلّف شي", "وما بينكتب شي قبل ما تعتمده"),
        (SETUP, "{{ $keysNote }}", "قيمة المفتاح ما بتنعرض هون أبداً"),
    ):
        text = read(view)
        visible = _without_details(text)
        assert short in visible, (view.name, short)
        assert detail in text and detail not in visible, (view.name, detail)


def test_what_is_never_overwritten_stays_on_screen():
    # the short line itself, right under the «إعادة البحث» box (inside «خيارات متقدمة», not under another «تفاصيل»)
    hint = re.search(r'<p class="lq-run-advanced__hint">([^<]*)</p>', read(BATCH)).group(1)
    assert "ما بينكتب أبداً فوق صورة اعتمدها مراجع" in hint
    assert "الصورة المنشورة بتضل مكانها لحد ما تعتمد غيرها" in hint


def test_the_readiness_card_left_the_home_page():
    # «جاهزية النشر الآلي» lives in Settings › النشر الآلي; «اليوم» neither shows nor computes it
    assert "جاهزية النشر الآلي" not in read(INDEX)
    assert "function readiness(" not in read(CONTROLLERS / "OverviewController.php")


# ---------------------------------------------------------------------------
# Vendor names in second place
# ---------------------------------------------------------------------------

def test_the_keys_tab_hint_names_roles_not_vendors():
    settings = read(CONTROLLERS / "SettingsController.php")
    tabs = settings[settings.index("public const TABS"):settings.index("public const STRICT_LANE_KEY")]
    hint = re.search(r"'keys' => \[[^\]]*'hint' => '([^']*)'", tabs).group(1)
    assert hint and not any(v in hint for v in VENDORS), hint


def test_the_keys_rows_lead_with_the_role_and_still_name_the_provider():
    keys = read(SETTINGS / "keys.blade.php")
    title = re.search(r'<span class="lq-keys__title">(.*?)</span>', keys).group(1)
    assert "$row['use']" in title
    assert re.search(r'<span class="lq-keys__use"[^>]*>\{\{ \$row\[\'name\'\] \}\}</span>', keys)
    setup = read(CONTROLLERS / "SetupController.php")
    for label in ("'البحث عن الصور · Serper'", "'قارئ الملصق · Gemini'", "'عزل الخلفية · PhotoRoom'",
                  "'رفع الصور · Cloudinary'"):
        assert label in setup, label


def test_health_service_cards_are_titled_by_what_they_do():
    diag = read(DIAG)
    roles = re.search(r"\$serviceRoles = \[(.*?)\];", diag, re.S).group(1)
    for key, role in (("serper", "البحث عن الصور"), ("gemini", "قارئ الملصق"), ("photoroom", "عزل الخلفية"),
                      ("cloudinary", "رفع الصور")):
        assert f"'{key}' => '{role}'" in roles
    head = re.search(r'<h2 class="lq-health-service__name">(.*?)</h2>', diag).group(1)
    assert head.startswith("{{ $serviceRoles[$service['key']]")
    assert '<span class="lq-subtle" dir="ltr">{{ $service[\'name\'] }}</span>' in head


def test_the_primary_lines_say_roles_and_keep_vendors_muted():
    diag = read(DIAG)
    note = re.search(r'id="diagCheckNote">(.*?)<span class="lq-subtle">', diag, re.S).group(1)
    assert not any(v in note for v in VENDORS) and "بحث صور واحد" in note and "عزل خلفية واحد" in note
    cost = re.search(r'id="publishCheckNote">(.*?)</span>', diag, re.S).group(1)
    assert not any(v in cost for v in VENDORS)
    auto = _without_details(read(SETTINGS / "auto_publish.blade.php"))
    intro = re.search(r'<p class="lq-settings-card__intro">(.*?)</p>', auto, re.S).group(1)
    assert not any(v in intro for v in VENDORS)
    setup = read(CONTROLLERS / "SetupController.php")
    keys_note = re.search(r"KEYS_TEST_NOTE = '([^']*)'", setup).group(1)
    assert not any(v in keys_note for v in VENDORS)


# ---------------------------------------------------------------------------
# One primary action
# ---------------------------------------------------------------------------

def _primaries(html):
    return re.findall(r'lq-btn--primary|variant="primary"', html)


def test_home_review_link_is_secondary_and_the_setup_card_keeps_the_primary():
    index = read(INDEX)
    link = re.search(r'<a class="([^"]+)" href="[^"]*" data-home="review-link">', index).group(1)
    assert "lq-btn--secondary" in link and "lq-btn--primary" not in link
    go = re.search(r'<a class="([^"]+)" data-home="next-action"', index).group(1)
    assert "($setupCard ?? null) ? 'lq-btn--secondary' : 'lq-btn--primary'" in go
    assert "lq-btn--primary" in read(HOME_CARD)


def test_the_new_run_card_has_one_primary_and_the_tools_one_each():
    batch = read(BATCH)
    new_run = batch[batch.index('id="run-new"'):batch.index('id="run-current"')]
    assert len(_primaries(new_run)) == 1 and 'data-run="start"' in new_run
    assert re.search(r'lq-btn--secondary" data-run="bulk-approve"', batch)
    assert re.search(r'lq-btn--secondary" data-run="barcodes-write"', batch)
    assert re.search(r'lq-btn--primary" data-run="bulk-open"', batch)          # the brands tool keeps its primary


def test_the_setup_footer_has_no_primary_and_skip_looks_like_a_link():
    setup = read(SETUP)
    footer = setup[setup.index('<footer class="lq-setup-step__nav">'):setup.index("</footer>")]
    assert not _primaries(footer)
    skip = re.search(r'<button type="button" class="([^"]+)" data-setup-skip', footer).group(1)
    assert "lq-btn--ghost" in skip


@NEEDS_NODE
def test_the_header_review_link_steps_aside_while_the_next_step_points_there():
    home = read(JS / "home.js")
    start = home.index("        function samePlace(")
    body = home[start:home.index("        function renderBanner(")]
    script = r"""
const nodes = {};
function $(name) { return nodes[name] || (nodes[name] = { attrs: {}, text: '', hidden: false,
    getAttribute(k) { return this.attrs[k] === undefined ? null : this.attrs[k]; },
    setAttribute(k, v) { this.attrs[k] = String(v); } }); }
const C = { setHidden(n, h) { n.hidden = !!h; }, setText(n, t) { n.text = t; } };
$('review-link').setAttribute('href', 'http://127.0.0.1:8000/catalog?mode=bulk');
""" + body + r"""
const out = [];
renderNext({ key: 'review', title: 't', text: 'x', action: 'a', href: '/catalog?mode=bulk' });
out.push($('review-link').hidden);
renderNext({ key: 'brands', title: 't', text: 'x', action: 'a', href: '/#run-brands' });
out.push($('review-link').hidden);
renderNext(null);
out.push($('review-link').hidden);
console.log(JSON.stringify(out));
"""
    result = subprocess.run([NODE, "-"], input=script, capture_output=True, text=True, timeout=60, encoding="utf-8")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout.strip().splitlines()[-1]) == [True, False, False]


# ---------------------------------------------------------------------------
# Page headers, no «روح على» on Health, «التقدّم محفوظ» once
# ---------------------------------------------------------------------------

def test_health_and_setup_use_the_page_header_component():
    # Health and Setup are sections of «النظام»: one page header there, a section head in each partial
    system = read(PAGES / "system.blade.php")
    assert system.count("<x-lq.page-header") == 1 and 'title="النظام"' in system
    for view in (DIAG, SETUP):
        text = read(view)
        assert "<x-lq.page-header" not in text and '<header class="lq-page-header' not in text, view.name
    setup = read(SETUP)
    assert '<h2 class="lq-section-title">جهّز لقطة</h2>' in setup and 'data-setup="progress"' in setup
    index = read(INDEX)                     # Home: the eyebrow and the title carry home.js hooks the component lacks
    assert 'data-home="date"' in index and 'data-home="greeting"' in index


def test_health_has_no_collapsed_section_and_no_jump_row():
    # every former «تفاصيل متقدمة» card is on its own section of «النظام» (?tab=), so no «روح على» row either
    diag = read(DIAG)
    assert "lq-health-advanced" not in diag and "<x-lq.jump-links" not in diag
    for card in ("services", "last-run", "lanes", "search-ops", "logs", "publish-check", "local-index", "eval-export",
                 "reprocess"):
        assert f'id="{card}"' in diag, card


def test_progress_is_kept_is_said_once():
    texts = read(SETUP) + read(HOME_CARD)
    assert texts.count("التقدّم محفوظ") == 1
    header = read(SETUP)
    head = header[header.index('<div class="lq-setup__head">'):header.index('data-setup="progress"')]
    assert "التقدّم محفوظ" in head                                      # the section's head, under «النظام»
