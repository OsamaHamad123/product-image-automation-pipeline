"""Laqta design tokens: one type scale, tone-named chips and texts, three button sizes, flat vs raised cards, the
page layout tokens and the semantic colour aliases (a later dark theme redefines only those).

Offline checks on public/css/laqta.css and the page styles that build on it (home, run, health, settings, setup),
plus the UI-kit page that shows every token. review.css and cutout.css keep their own rules and are not checked here.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DASH = ROOT / "dashboard"
CSS_DIR = DASH / "public" / "css"
LAQTA_CSS = CSS_DIR / "laqta.css"
PAGE_CSS = {name: CSS_DIR / "pages" / f"{name}.css" for name in ("home", "run", "health", "settings", "setup")}
UI_KIT = DASH / "resources" / "views" / "dashboard" / "ui_kit.blade.php"
CHIP = DASH / "resources" / "views" / "components" / "lq" / "chip.blade.php"
CARD = DASH / "resources" / "views" / "components" / "lq" / "card.blade.php"

TYPE_SCALE = ["2xs", "xs", "sm", "md", "lg", "xl", "2xl", "3xl", "kpi"]
LINE_HEIGHTS = ["none", "tight", "snug", "normal", "base", "relaxed", "loose"]
TONES = ["success", "warning", "danger", "info", "neutral", "teal"]
TEXT_TONES = ["success", "warning", "danger", "info", "muted"]
# the product-status chip names the views and scripts build ('lq-chip--' + status): kept as aliases
STATUS_CHIPS = ["proposed", "warning", "none", "not-found", "approved", "error"]


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def strip_comments(css: str) -> str:
    return re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)


def root_block() -> str:
    match = re.search(r":root\s*\{(.*?)\n\}", read(LAQTA_CSS), re.DOTALL)
    assert match, "laqta.css has no :root block"
    return match.group(1)


def tokens() -> dict:
    return {name: value.strip() for name, value in re.findall(r"(--lq-[a-z0-9-]+)\s*:\s*([^;]+);", root_block())}


def px(name: str) -> float:
    """A size token's value in px, following var() aliases."""
    value = tokens()[name]
    seen = set()
    while value.startswith("var("):
        ref = re.fullmatch(r"var\((--lq-[a-z0-9-]+)\)", value).group(1)
        assert ref not in seen, ref
        seen.add(ref)
        value = tokens()[ref]
    assert value.endswith("px"), (name, value)
    return float(value[:-2])


def body_without_root(path: Path) -> str:
    """The stylesheet without its :root token definitions (laqta.css keeps raw values only there: the light set and
    the two copies of the dark set, :root[data-theme="dark"] and :root:not([data-theme="light"]))"""
    css = strip_comments(read(path))
    if path == LAQTA_CSS:
        css = re.sub(r":root(?:\[[^\]]*\]|:not\(\[[^\]]*\]\))?\s*\{[^}]*\}", "", css)
    return css


def rules(css: str):
    """(selector, declarations) for every plain rule, media blocks flattened."""
    css = strip_comments(css)
    for selector, body in re.findall(r"([^{}@]+)\{([^{}]*)\}", css):
        yield " ".join(selector.split()), body


ALL_CSS = [LAQTA_CSS] + list(PAGE_CSS.values())


# ---------------------------------------------------------------------------
# 1. Type scale
# ---------------------------------------------------------------------------

def test_the_type_scale_is_defined_and_strictly_increasing():
    sizes = [px(f"--lq-fs-{step}") for step in TYPE_SCALE]
    assert sizes == sorted(sizes) and len(set(sizes)) == len(sizes), dict(zip(TYPE_SCALE, sizes))
    assert px("--lq-fs-xs") != px("--lq-fs-sm")
    assert min(sizes) >= 12, "no text under 12px"
    # the body size is a name for a step on the scale, not a size of its own
    assert tokens()["--lq-fs-base"] == "var(--lq-fs-md)"


def test_line_height_tokens_exist():
    values = [float(tokens()[f"--lq-lh-{name}"]) for name in LINE_HEIGHTS]
    assert values == sorted(values) and len(set(values)) == len(values), values


@pytest.mark.parametrize("path", ALL_CSS, ids=lambda p: p.name)
def test_no_raw_font_size_or_line_height(path):
    css = body_without_root(path)
    raw_sizes = re.findall(r"font-size:\s*([0-9.]+(?:px|rem|em|%))", css)
    assert not raw_sizes, f"{path.name}: use a --lq-fs-* token, found {raw_sizes}"
    raw_heights = re.findall(r"line-height:\s*([0-9.]+(?:px|rem|em|%)?)\s*[;}]", css)
    assert not raw_heights, f"{path.name}: use a --lq-lh-* token, found {raw_heights}"
    for size in re.findall(r"font-size:\s*([^;]+);", css):
        assert size.strip() in ("inherit",) or re.fullmatch(r"var\(--lq-fs-[a-z0-9]+\)", size.strip()), size


def test_one_heading_system():
    laqta = read(LAQTA_CSS)
    for selector, token in ((".lq-page-title", "--lq-fs-2xl"), (".lq-section-title", "--lq-fs-xl"),
                            (".lq-card__title", "--lq-fs-lg")):
        blocks = re.findall(r"\n" + re.escape(selector) + r" \{([^}]*)\}", laqta)
        assert any(f"font-size: var({token})" in b for b in blocks), selector
    assert px("--lq-fs-2xl") > px("--lq-fs-xl") > px("--lq-fs-lg")
    # pages do not resize the shared headings
    for name, path in PAGE_CSS.items():
        for selector, body in rules(read(path)):
            if re.search(r"lq-page-title|lq-home__title|lq-section-title|lq-card__title", selector):
                assert "font-size" not in body, (name, selector)


# ---------------------------------------------------------------------------
# 2. Tones
# ---------------------------------------------------------------------------

def _classes(path: Path) -> set:
    return set(re.findall(r"\.(lq-[a-z0-9]+(?:(?:__|--|-)[a-z0-9]+)*)", strip_comments(read(path))))


def test_tone_named_chips_and_their_aliases():
    defined = _classes(LAQTA_CSS)
    for tone in TONES:
        assert f"lq-chip--{tone}" in defined, tone
    for status in STATUS_CHIPS:
        assert f"lq-chip--{status}" in defined, status
    css = read(LAQTA_CSS)
    # an alias shares its tone's rule
    for tone, alias in (("success", "proposed"), ("danger", "error"), ("info", "not-found"), ("neutral", "none"),
                        ("teal", "approved")):
        assert re.search(rf"\.lq-chip--{tone},\s*\.lq-chip--{re.escape(alias)} \{{", css), (tone, alias)
    chip = read(CHIP)
    assert "'tone' => null" in chip and all(f"'{tone}'" in chip for tone in TONES)


def test_text_tones_share_one_set_of_values():
    css = read(LAQTA_CSS)
    for tone in TEXT_TONES:
        assert f"--lq-text-{tone}:" in root_block(), tone
        assert re.search(rf"\.lq-text--{tone} \{{ color: var\(--lq-text-{tone}\); \}}", css), tone
    # the per-page status texts take the same tokens (their class names keep working)
    sets = {
        "settings": ["lq-settings-form__status--", "lq-settings-columns__item--", "lq-keys__state--"],
        "health": ["lq-health-provider__problems--"],
    }
    for page, prefixes in sets.items():
        for selector, body in rules(read(PAGE_CSS[page])):
            if any(p in selector for p in prefixes) and re.search(r"(?<![-\w])color:", body):
                colours = re.findall(r"(?<![-\w])color:\s*([^;]+);", body)
                assert colours and all(re.fullmatch(r"var\(--lq-text-(success|warning|danger|info|muted)\)", c)
                                       for c in colours), (page, selector, colours)


# ---------------------------------------------------------------------------
# 3. Buttons
# ---------------------------------------------------------------------------

def test_buttons_have_three_sizes_only():
    t = tokens()
    assert (t["--lq-btn-h-sm"], t["--lq-btn-h"], t["--lq-btn-h-lg"]) == ("36px", "44px", "48px")
    for selector, body in rules(read(LAQTA_CSS)):
        if ".lq-btn" in selector:
            for value in re.findall(r"(?<![-\w])(?:min-)?height:\s*([^;]+);", body):
                assert value.strip() in ("var(--lq-btn-h-sm)", "var(--lq-btn-h)", "var(--lq-btn-h-lg)"), (selector, value)


@pytest.mark.parametrize("name", list(PAGE_CSS), ids=str)
def test_pages_do_not_resize_buttons(name):
    for selector, body in rules(read(PAGE_CSS[name])):
        if ".lq-btn" in selector:
            for prop in ("height", "min-height", "border-radius", "font-size", "padding", "padding-inline"):
                assert not re.search(rf"(?<![-\w]){prop}\s*:", body), (name, selector, prop)


# ---------------------------------------------------------------------------
# 4. Cards
# ---------------------------------------------------------------------------

def test_flat_and_raised_cards():
    css = read(LAQTA_CSS)
    raised = re.search(r"\n\.lq-card \{([^}]*)\}", css).group(1)
    assert "box-shadow: var(--lq-shadow-card)" in raised and "padding: var(--lq-card-pad)" in raised
    flat = re.search(r"\n\.lq-card--flat \{([^}]*)\}", css).group(1)
    assert "box-shadow: none" in flat and "var(--lq-surface-2)" in flat and "var(--lq-border-soft)" in flat
    assert "'flat' => 'lq-card--flat'" in read(CARD)


@pytest.mark.parametrize("name", list(PAGE_CSS), ids=str)
def test_pages_keep_the_card_padding(name):
    """A card (lq-card + a page class) keeps the shared padding: the page classes below are cards in the views."""
    card_classes = ("lq-settings-card", "lq-health-now", "lq-setup-step", "lq-setup-ready", "lq-health-log")
    for selector, body in rules(read(PAGE_CSS[name])):
        if any(re.search(rf"\.{c}(?![\w-])", selector) for c in card_classes):
            assert not re.search(r"(?<![-\w])padding\s*:", body), (name, selector)


# ---------------------------------------------------------------------------
# 5. Layout
# ---------------------------------------------------------------------------

def test_main_column_and_page_roots_use_the_layout_tokens():
    css = read(LAQTA_CSS)
    t = tokens()
    assert t["--lq-content-max"] == "1280px" and t["--lq-measure"] == "68ch"
    main = re.search(r"\n\.lq-main \{([^}]*)\}", css).group(1)
    assert "gap: var(--lq-page-gap)" in main
    assert "padding: var(--lq-main-pad-top) var(--lq-main-pad-inline) var(--lq-main-pad-bottom)" in main
    assert re.search(r"\.lq-main:not\(\.lq-main--flush\) > \* \{\s*max-width: var\(--lq-content-max\);", css)
    assert re.search(r"\.lq-page-header__desc,\s*\.lq-card__lead \{\s*max-width: var\(--lq-measure\);", css)
    # run.css has no page root of its own: the run panel and the tools sit in «اليوم»'s .lq-home
    roots = {"home": ".lq-home", "health": ".lq-health", "settings": ".lq-settings",
             "setup": ".lq-setup"}
    for name, selector in roots.items():
        block = re.search(r"\n" + re.escape(selector) + r" \{([^}]*)\}", read(PAGE_CSS[name]))
        assert block and "gap: var(--lq-page-gap)" in block.group(1), name


def test_the_hidden_rule_lives_once_in_laqta_css():
    assert re.search(r"\.lq-body \[hidden\] \{\s*display: none !important;", read(LAQTA_CSS))
    for name, path in PAGE_CSS.items():
        assert not re.search(r"\[hidden\]\s*\{\s*display:\s*none\s*!important", read(path)), name


def test_page_css_has_no_stray_text():
    for name, path in PAGE_CSS.items():
        css = strip_comments(read(path))
        assert "/bin/" not in css and "not found" not in css, name


# ---------------------------------------------------------------------------
# 6. Semantic colour aliases
# ---------------------------------------------------------------------------

def test_semantic_aliases_are_defined():
    t = tokens()
    for name in ("--lq-accent", "--lq-accent-hover", "--lq-accent-soft", "--lq-on-accent", "--lq-scrim",
                 "--lq-shadow-card", "--lq-shadow-raised", "--lq-shadow-toast", "--lq-shadow-dialog",
                 "--lq-shadow-focus", "--lq-runcard-error-bg", "--lq-runcard-paused-text"):
        assert name in t, name
    assert t["--lq-accent"] == "var(--lq-teal-700)"


@pytest.mark.parametrize("path", ALL_CSS, ids=lambda p: p.name)
def test_no_raw_colours_outside_the_tokens(path):
    css = body_without_root(path)
    css = re.sub(r"url\(\"data:[^\"]*\"\)", "", css)                       # the select arrow's inline SVG
    css = re.sub(r"(?:-webkit-)?mask-image:[^;]*;", "", css)                # masks: #000 is opacity, not a colour
    assert not re.findall(r"#[0-9A-Fa-f]{3,8}\b", css), path.name
    assert not re.findall(r"rgba?\(", css), path.name
    # the palette's teal shades are reached through the accent aliases (the bright 500 is a progress colour)
    assert not re.findall(r"var\(--lq-teal-(?:700|800|900|soft|100|300|50)\)", css), path.name


# ---------------------------------------------------------------------------
# 7. Unused classes are gone
# ---------------------------------------------------------------------------

def test_unused_classes_are_removed():
    defined = _classes(LAQTA_CSS)
    for name in ("lq-divider", "lq-grid--3", "lq-grid--5", "lq-select--sm", "lq-stack--lg", "lq-skeleton--block",
                 "lq-skeleton--circle"):
        assert name not in defined, name
    for name in ("lq-grid--2", "lq-grid--4", "lq-input--sm", "lq-stack--sm", "lq-skeleton--thumb"):
        assert name in defined, name


# ---------------------------------------------------------------------------
# 8. UI kit
# ---------------------------------------------------------------------------

def test_ui_kit_shows_the_tokens():
    kit = read(UI_KIT)
    for anchor in ("kit-type", "kit-spacing", "kit-tones", "kit-cards", "kit-dialog"):
        assert f'id="{anchor}"' in kit and f'href="#{anchor}"' in kit, anchor
    for step in TYPE_SCALE:
        assert f"'--lq-fs-{step}'" in kit and f".kit-fs--{step} " in kit, step
    assert ':tone="$kitTone[0]"' in kit and 'variant="flat"' in kit
    for tone in TEXT_TONES:
        assert f'class="lq-text--{tone}"' in kit, tone
    assert 'class="lq-dialog"' in kit and "window.Laqta.ask(" in kit
    # inline styles only where a token's own value is the point (a colour swatch, a radius, a space bar)
    for style in re.findall(r'style="([^"]*)"', kit):
        assert re.fullmatch(r"[a-z-]+: var\(\{\{ \$kit[A-Za-z]+\[\d\] \}\}\)", style), style
    raw = re.findall(r"font-size:\s*([0-9.]+px)", kit)
    assert set(raw) <= {"52px", "40px"}, f"the kit uses the scale too (the logo lock-up is the one exception): {raw}"
