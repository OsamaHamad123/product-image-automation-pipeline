"""«اليوم» and «النظام» use the shared design system (public/css/laqta.css) instead of page copies of it.

Offline checks on the five page stylesheets (home, run, health, settings, setup), their views and scripts:
  pills      a status pill is the shared chip (lq-chip lq-chip--<tone>, <x-lq.chip>), never a page rule
  tones      a status said in words carries lq-text--*; the per-page tone sets are gone
  cards      raised lq-card for a block you act in, lq-card--flat for one you read (the service tiles included)
  headings   lq-page-title / lq-section-title / lq-card__title; no page title class restates a size
  spacing    gaps, margins and paddings are --lq-space-* tokens (a short allow-list of hairline nudges stays raw)
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DASH = ROOT / "dashboard"
PAGES = DASH / "public" / "css" / "pages"
PAGE_CSS = {name: PAGES / f"{name}.css" for name in ("home", "run", "health", "settings", "setup")}
JS = DASH / "public" / "js"
VIEWS = DASH / "resources" / "views"
INDEX = VIEWS / "dashboard" / "index.blade.php"
DIAG = VIEWS / "dashboard" / "diagnostics.blade.php"
SETUP = VIEWS / "dashboard" / "setup.blade.php"
KEYS = VIEWS / "settings" / "keys.blade.php"
SHEET = VIEWS / "settings" / "sheet.blade.php"
SOURCES = [INDEX, DIAG, SETUP, VIEWS / "dashboard" / "system.blade.php", VIEWS / "dashboard" / "settings.blade.php",
           *sorted((VIEWS / "settings").glob("*.blade.php")),
           *(JS / f"{name}.js" for name in ("home", "run", "run-common", "health", "settings", "setup"))]


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def rules(css: str):
    """(selector, declarations) for every plain rule, comments dropped and media blocks flattened."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    for selector, body in re.findall(r"([^{}@]+)\{([^{}]*)\}", css):
        yield " ".join(selector.split()), body


def rule(name: str, selector: str) -> str:
    bodies = [b for s, b in rules(read(PAGE_CSS[name])) if s == selector]
    assert bodies, (name, selector)
    return " ".join(bodies)


# ---------------------------------------------------------------------------
# Pills
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", list(PAGE_CSS), ids=str)
def test_no_page_defines_its_own_pill(name):
    """A pill radius with padding is a chip: pages use lq-chip, they do not draw one."""
    for selector, body in rules(read(PAGE_CSS[name])):
        pill = re.search(r"border-radius:\s*(?:var\(--lq-radius-pill\)|9{3,}px)", body)
        padded = re.search(r"(?<![-\w])padding(?:-[a-z-]+)?\s*:", body)
        assert not (pill and padded), (name, selector)


def test_the_status_pills_are_shared_chips():
    run = read(JS / "run.js")
    for hook in ("lq-run-quality__count", "lq-run-brand__rows"):
        assert re.search(r"'lq-chip lq-chip--[^']*" + hook, run), hook
    assert "'lq-chip lq-chip--sm lq-chip--' + (BULK_CHIP_TONE[r.confidence] || 'neutral') + ' lq-run-bulk__chip'" in run
    assert "'lq-chip lq-chip--teal lq-run-bulk__added'" in run
    assert "'lq-chip lq-chip--warning lq-chip--sm lq-settings-dirty'" in read(JS / "settings.js")
    keys = read(KEYS)
    assert '<span class="lq-keys__state">' in keys
    assert "<x-lq.chip :tone=\"$keyChips[$row['tone']] ?? 'neutral'\" size=\"sm\">" in keys
    diag = read(DIAG)
    assert re.search(r'<x-lq\.chip [^>]*class="lq-health-service__state" data-service-state>', diag)
    assert "'lq-chip lq-chip--sm lq-chip--' + (CHIPS[view.tone] || 'neutral') + ' lq-health-service__state'" \
        in read(JS / "health.js")
    # the hooks the scripts and tests use stay
    for hook in ("data-service-state", "data-service-dot", "data-key-toggle"):
        assert hook in diag + keys, hook
    assert "data-settings-dirty" in read(JS / "settings.js")


# ---------------------------------------------------------------------------
# Tones
# ---------------------------------------------------------------------------

REMOVED = ["lq-settings-form__status--", "lq-settings-columns__item--", "lq-keys__state--",
           "lq-health-provider__problems", "lq-health-provider__ok", "lq-health-provider__empty",
           "lq-run-bulk__chip--", "lq-settings-preview__title", "lq-health-providers__title", "lq-run-recent__title",
           "lq-health-now__title"]


@pytest.mark.parametrize("prefix", REMOVED, ids=str)
def test_the_page_tone_sets_and_title_copies_are_gone(prefix):
    for path in [*PAGE_CSS.values(), *SOURCES]:
        assert prefix not in read(path), (path.name, prefix)


def test_status_texts_use_the_shared_text_tones():
    settings = read(JS / "settings.js")
    assert "status.className = 'lq-settings-form__status lq-text--' + (tone || 'muted');" in settings
    assert "'lq-settings-columns__value' + (c.tone === 'muted' ? ' lq-subtle' : ' lq-text--' + c.tone)" in settings
    assert 'class="lq-settings-form__status lq-text--muted"' in read(SHEET)
    health = read(JS / "health.js")
    assert "make('span', 'lq-text--success', p.ok)" in health and "make('span', textTone(p.tone), p.problems)" in health
    assert "'lq-setup-check__state ' + stateTone(row.tone)" in read(JS / "setup.js")
    assert 'class="lq-setup-check__state {{ $setupText($row[\'tone\']) }}"' in read(SETUP)
    # the hooks keep no colour of their own (a page rule would win over .lq-text--*: it loads later)
    for name, selector in (("settings", ".lq-settings-form__status"), ("settings", ".lq-settings-columns__value"),
                           ("settings", ".lq-keys__state"), ("setup", ".lq-setup-check__state")):
        assert not re.search(r"(?<![-\w])color\s*:", rule(name, selector)), selector
    assert not re.search(r"\.lq-setup-check\[data-tone=[^\]]*\] \.lq-setup-check__state", read(PAGE_CSS["setup"]))


# ---------------------------------------------------------------------------
# Cards
# ---------------------------------------------------------------------------

def test_service_tiles_are_flat_cards():
    assert '<article class="lq-card lq-card--flat lq-card--compact lq-health-service"' in read(DIAG)
    body = rule("health", ".lq-health-service")
    for prop in ("padding", "border", "border-radius", "background", "box-shadow"):
        assert not re.search(rf"(?<![-\w]){prop}\s*:", body), prop
    # the name is the card title
    assert '<h2 class="lq-card__title lq-health-service__name">' in read(DIAG)


def test_raised_for_action_flat_for_reference():
    index = read(INDEX)
    for raised in ('class="lq-card lq-run-new"', 'class="lq-card lq-run-current"', 'class="lq-card lq-today-tools"'):
        assert raised in index, raised
    assert '<section class="lq-card lq-card--flat" aria-labelledby="lq-home-funnel-title"' in index
    diag = read(DIAG)
    assert 'class="lq-card lq-health-now"' in diag
    for flat in ('id="last-run"', 'id="lanes"', "lq-health-results", "lq-health-cost", "lq-health-reasons"):
        assert re.search(r'class="lq-card lq-card--(?:compact lq-card--)?flat[^"]*"[^>]*' + re.escape(flat)
                         + r'|class="lq-card lq-card--(?:compact lq-card--)?flat[^"]*' + re.escape(flat), diag), flat
    for tool in ('class="lq-card lq-health-publish"', 'class="lq-card" id="local-index"',
                 'class="lq-card lq-health-eval"', 'class="lq-card lq-health-reprocess"'):
        assert tool in diag, tool


# ---------------------------------------------------------------------------
# Headings
# ---------------------------------------------------------------------------

def test_section_heads_of_the_system_tabs():
    """Each «النظام» tab opens on a section title: the settings cards, the setup steps and the health sections alike."""
    diag = read(DIAG)
    for head in ('id="lq-health-now-title"', 'id="lq-health-search-title"', 'id="lq-health-log-title"',
                 'id="publish-check-title"', ">فهرس المتاجر المحلي</h2>", 'id="lq-health-eval-title"',
                 'id="reprocess-title"'):
        assert re.search(r'<h2 class="lq-section-title"[^>]*' + re.escape(head) if head.startswith("id=")
                         else r'<h2 class="lq-section-title"' + re.escape(head), diag), head
    for path in sorted((VIEWS / "settings").glob("*.blade.php")):
        text = read(path)
        for h2 in re.findall(r"<h2 class=\"([^\"]*)\"", text):
            assert h2 == "lq-section-title", (path.name, h2)
    setup = read(SETUP)
    assert setup.count('<h2 class="lq-section-title lq-setup-step__title"') == 2
    # the dark log card keeps its title light
    assert "color: var(--lq-text-on-dark)" in rule("health", ".lq-health-log .lq-section-title")


@pytest.mark.parametrize("name", list(PAGE_CSS), ids=str)
def test_title_hooks_do_not_restate_the_type(name):
    for selector, body in rules(read(PAGE_CSS[name])):
        if re.search(r"lq-setup-step__title|lq-health-service__name|lq-home__title", selector):
            for prop in ("font-size", "font-weight", "font-family"):
                assert prop not in body, (name, selector, prop)


# ---------------------------------------------------------------------------
# Spacing
# ---------------------------------------------------------------------------

# hairline nudges (optical alignment of an icon or a checkbox, a 1-3px gap between two lines of one item) and the
# indent that lines a hint up with its checkbox's text: not on the 4px scale on purpose
RAW_SPACING_ALLOWED = {"1px", "2px", "3px"}
RAW_SPACING_EXCEPTIONS = {("run", ".lq-run-advanced__hint", "padding-inline-start", "28px")}
SPACING = re.compile(r"(?<![-\w])((?:row-|column-)?gap|margin(?:-[a-z-]+)?|padding(?:-[a-z-]+)?|"
                     r"scroll-margin(?:-[a-z-]+)?)\s*:\s*([^;]+);")


@pytest.mark.parametrize("name", list(PAGE_CSS), ids=str)
def test_spacing_uses_the_scale(name):
    found = []
    for selector, body in rules(read(PAGE_CSS[name])):
        for prop, value in SPACING.findall(body):
            for raw in re.findall(r"(?<![\w.-])\d+(?:\.\d+)?px\b", value):
                if raw in RAW_SPACING_ALLOWED or (name, selector, prop, raw) in RAW_SPACING_EXCEPTIONS:
                    continue
                found.append((selector, prop, raw))
    assert not found, found
