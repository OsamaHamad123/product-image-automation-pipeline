"""Dark mode, part 1: the token sets in laqta.css, the boot script, the theme switch and the standalone pages.

* laqta.css carries the dark set twice with identical values: :root[data-theme="dark"] (forced) and
  @media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) } (follow the system). Every colour token of
  the light :root has a dark value, unless it is theme-independent on purpose (THEME_INDEPENDENT: the palette
  primitives the aliases point at, and the colours that sit on the always-dark sidebar).
* Contrast (WCAG, computed here) in BOTH themes: body text >= 7:1, secondary and muted text >= 4.5:1 on the page and
  card surfaces, each status ink on its own background, text on solid buttons.
* public/js/theme-boot.js: the first script in <head>, before the CSS (no flash), survives blocked storage.
* public/js/layout.js window.Laqta.setTheme: sets / removes <html data-theme>, persists «laqta.theme», updates the
  color-scheme and theme-color metas and the pressed button of every [data-lq-theme-switch]; no argument cycles.
* login and forbidden load theme-boot.js and say color-scheme "light dark".
"""

import re

import pytest

from test_laqta_site_ux import LAYOUT_DOM, NEEDS_NODE, _node
from test_laqta_ui import CSS, DASH, LAYOUT, LAYOUT_JS, VIEWS, read

THEME_BOOT = DASH / "public" / "js" / "theme-boot.js"
LOGIN = VIEWS / "auth" / "login.blade.php"
FORBIDDEN = VIEWS / "auth" / "forbidden.blade.php"
UI_KIT = VIEWS / "dashboard" / "ui_kit.blade.php"

# Colour tokens that keep one value in both themes, on purpose: the palette primitives (the semantic aliases that
# point at them are redefined instead) and what sits on the sidebar, which is dark ink in both themes.
THEME_INDEPENDENT = {
    "--lq-teal-900", "--lq-teal-800", "--lq-teal-700", "--lq-teal-500", "--lq-teal-300", "--lq-teal-100",
    "--lq-teal-soft", "--lq-teal-50", "--lq-teal-glow",
    "--lq-saffron-500", "--lq-saffron-700", "--lq-saffron-800", "--lq-saffron-200", "--lq-saffron-50",
    "--lq-saffron-ink",
    "--lq-ink-text", "--lq-ink-muted", "--lq-ink-fg", "--lq-text-on-dark", "--lq-focus-on-dark",
    "--lq-danger-on-dark", "--lq-running-on-dark", "--lq-running-dot",
    "--lq-runcard-paused-text", "--lq-runcard-error-bg", "--lq-runcard-error-border", "--lq-runcard-error-text",
}

TOKEN_RE = re.compile(r"(--lq-[a-z0-9-]+)\s*:\s*([^;]+);")
COLOUR_RE = re.compile(r"#[0-9A-Fa-f]{3,8}\b|rgba?\(|\btransparent\b")


def _block(pattern: str) -> str:
    match = re.search(pattern, read(CSS), re.DOTALL)
    assert match, pattern
    return match.group(1)


def light_block() -> str:
    return _block(r"\n:root\s*\{(.*?)\n\}")


def dark_forced_block() -> str:
    return _block(r"\n:root\[data-theme=\"dark\"\]\s*\{(.*?)\n\}")


def dark_system_block() -> str:
    return _block(r"@media \(prefers-color-scheme: dark\)\s*\{\s*:root:not\(\[data-theme=\"light\"\]\)\s*\{(.*?)\n    \}\s*\}")


def _tokens(block: str) -> dict:
    return {name: " ".join(value.split()) for name, value in TOKEN_RE.findall(block)}


def _scheme(block: str) -> str:
    match = re.search(r"(?<![-\w])color-scheme:\s*([a-z ]+);", block)
    return match.group(1) if match else ""


def theme(name: str) -> dict:
    values = _tokens(light_block())
    if name == "dark":
        values.update(_tokens(dark_forced_block()))
    return values


def resolve(values: dict, value: str) -> str:
    seen = set()
    while True:
        match = re.fullmatch(r"var\((--lq-[a-z0-9-]+)\)", value.strip())
        if not match:
            return value.strip()
        assert match.group(1) not in seen, value
        seen.add(match.group(1))
        value = values[match.group(1)]


def _luminance(hex_value: str) -> float:
    hex_value = hex_value.lstrip("#")
    channels = [int(hex_value[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(fg: str, bg: str) -> float:
    a, b = sorted((_luminance(fg), _luminance(bg)), reverse=True)
    return (a + 0.05) / (b + 0.05)


# ---------------------------------------------------------------------------
# The token sets
# ---------------------------------------------------------------------------

def test_both_dark_selectors_carry_the_same_tokens():
    forced, system = _tokens(dark_forced_block()), _tokens(dark_system_block())
    assert forced, "empty dark set"
    assert forced == system
    assert _scheme(dark_forced_block()) == _scheme(dark_system_block()) == "dark"
    assert _scheme(light_block()) == "light"
    # the light block comes first: the dark blocks override it, never the other way round
    css = read(CSS)
    assert css.index("\n:root {") < css.index(':root[data-theme="dark"]') < css.index("prefers-color-scheme: dark")


def test_every_light_colour_token_has_a_dark_value_or_is_theme_independent():
    light, dark = _tokens(light_block()), _tokens(dark_forced_block())
    assert not sorted(set(dark) - set(light)), "dark-only tokens: define them in the light :root too"
    assert not sorted(THEME_INDEPENDENT & set(dark)), "an allow-listed token is redefined: drop it from the list"
    assert THEME_INDEPENDENT <= set(light), sorted(THEME_INDEPENDENT - set(light))
    missing = []
    for name, value in light.items():
        if name in THEME_INDEPENDENT or name in dark:
            continue
        refs = re.findall(r"var\((--lq-[a-z0-9-]+)\)", value)
        # a raw colour, or an alias of a colour that stays the same in dark: needs its own dark value
        if COLOUR_RE.search(value) or "url(" in value or any(ref in THEME_INDEPENDENT for ref in refs):
            missing.append(name)
    assert not missing, missing


def test_the_semantic_aliases_follow_the_theme():
    light, dark = theme("light"), theme("dark")
    for name in ("--lq-accent", "--lq-on-accent", "--lq-focus", "--lq-scrim", "--lq-shadow-card", "--lq-shadow-dialog",
                 "--lq-inverse-bg", "--lq-inverse-fg", "--lq-theme-color", "--lq-select-chevron"):
        assert resolve(light, light[name]) != resolve(dark, dark[name]), name
    # the select arrow's stroke is the secondary text of its theme
    for values in (light, dark):
        stroke = re.search(r"stroke='%23([0-9A-Fa-f]{6})'", values["--lq-select-chevron"]).group(1)
        assert ("#" + stroke).upper() == resolve(values, "var(--lq-text-2)").upper()


def test_the_sidebar_keeps_its_own_elevation_in_dark():
    dark = theme("dark")
    ink, bg = resolve(dark, "var(--lq-ink-900)"), resolve(dark, "var(--lq-bg)")
    assert ink.upper() != bg.upper() and _luminance(ink) < _luminance(bg), "a step darker than the page"
    edge = resolve(dark, "var(--lq-sidebar-edge)")
    assert re.fullmatch(r"#[0-9A-Fa-f]{6}", edge) and edge.upper() != ink.upper(), "and a visible edge"
    sidebar = re.search(r"\n\.lq-sidebar \{([^}]*)\}", read(CSS)).group(1)
    assert "border-inline-end: 1px solid var(--lq-sidebar-edge)" in sidebar
    assert theme("light")["--lq-sidebar-edge"] == "transparent"
    assert dark["--lq-theme-color"].upper() == ink.upper()


def test_image_frames_keep_a_light_neutral_surface():
    css = read(CSS)
    frame = re.search(r"\n\.lq-image-frame \{([^}]*)\}", css).group(1)
    assert "var(--lq-image-surface)" in frame and "var(--lq-image-check)" in frame
    assert "filter" not in frame and "invert(" not in css
    for name in ("light", "dark"):
        surface = resolve(theme(name), "var(--lq-image-surface)")
        assert _luminance(surface) > 0.7, (name, surface)       # a packshot reads as on paper in both themes


# ---------------------------------------------------------------------------
# Contrast in both themes
# ---------------------------------------------------------------------------

PAIRS = [
    # (foreground, background, minimum)
    *[("--lq-text", bg, 7.0) for bg in ("--lq-bg", "--lq-surface", "--lq-surface-2")],
    *[("--lq-text-2", bg, 4.5) for bg in ("--lq-bg", "--lq-surface", "--lq-surface-2", "--lq-neutral-bg")],
    *[("--lq-text-3", bg, 4.5) for bg in ("--lq-bg", "--lq-surface", "--lq-surface-2")],
    ("--lq-success", "--lq-success-bg", 4.5), ("--lq-success-ink", "--lq-success-bg", 4.5),
    ("--lq-warning", "--lq-warning-bg", 4.5), ("--lq-warning-ink", "--lq-warning-bg", 4.5),
    ("--lq-danger-text", "--lq-danger-bg", 4.5), ("--lq-danger-ink", "--lq-danger-bg", 4.5),
    ("--lq-info-text", "--lq-info-bg", 4.5),
    ("--lq-text-success", "--lq-surface", 4.5), ("--lq-text-warning", "--lq-surface", 4.5),
    ("--lq-text-danger", "--lq-surface", 4.5), ("--lq-text-info", "--lq-surface", 4.5),
    ("--lq-accent", "--lq-surface", 4.5), ("--lq-accent", "--lq-bg", 4.5), ("--lq-accent", "--lq-accent-soft", 4.5),
    ("--lq-on-accent", "--lq-accent", 4.5), ("--lq-on-accent", "--lq-accent-hover", 4.5),
    ("--lq-on-accent", "--lq-danger", 4.5), ("--lq-on-accent", "--lq-danger-text", 4.5),
    ("--lq-inverse-fg", "--lq-inverse-bg", 4.5),
    ("--lq-ink-text", "--lq-ink-900", 4.5), ("--lq-ink-muted", "--lq-ink-900", 4.5),
    ("--lq-ink-muted", "--lq-ink-800", 4.5), ("--lq-text-on-dark", "--lq-ink-700", 4.5),
]


@pytest.mark.parametrize("name", ["light", "dark"])
@pytest.mark.parametrize("fg, bg, minimum", PAIRS, ids=[f"{f[5:]}-on-{b[5:]}" for f, b, _ in PAIRS])
def test_contrast(name, fg, bg, minimum):
    values = theme(name)
    fg_hex, bg_hex = resolve(values, f"var({fg})"), resolve(values, f"var({bg})")
    assert re.fullmatch(r"#[0-9A-Fa-f]{6}", fg_hex) and re.fullmatch(r"#[0-9A-Fa-f]{6}", bg_hex), (fg_hex, bg_hex)
    ratio = contrast(fg_hex, bg_hex)
    assert ratio >= minimum, (name, fg, bg, round(ratio, 2))


# ---------------------------------------------------------------------------
# Boot script, layout and standalone pages
# ---------------------------------------------------------------------------

BOOT_TAG = "<script src=\"{{ asset('js/theme-boot.js') }}?v={{ @filemtime(public_path('js/theme-boot.js')) ?: '1' }}\"></script>"


@pytest.mark.parametrize("path", [LAYOUT, LOGIN, FORBIDDEN], ids=lambda p: p.name)
def test_theme_boot_is_the_first_script_before_the_css(path):
    page = read(path)
    head = page[:page.index("</head>")]
    assert BOOT_TAG in head, path.name
    assert head.index(BOOT_TAG) == head.index("<script"), "the first script of the page"
    assert head.index(BOOT_TAG) < head.index('<link rel="stylesheet"'), "before the CSS: no flash"
    assert '<meta name="color-scheme" content="light dark">' in head
    assert 'content="light">' not in head


def test_the_switch_sits_in_the_sidebar_foot_and_the_account_sheet():
    layout = read(LAYOUT)
    foot = re.search(r'<form class="lq-sidebar__user".*?</form>', layout, re.DOTALL).group(0)
    sheet = re.search(r'<div class="lq-account-sheet".*?</div>', layout, re.DOTALL).group(0)
    for part in (foot, sheet):
        assert re.search(r'<x-lq\.segmented label="المظهر" :options="\$lqThemes"[^>]*data-lq-theme-switch', part), part
    assert "lq-segmented--ink" in foot and "lq-segmented--ink" not in sheet
    assert "$lqThemes = ['light' => 'فاتح', 'dark' => 'غامق', 'system' => 'تلقائي'];" in layout
    # the sheet still opens on «روح لـ…» (its first button), the switch comes after it
    assert sheet.index("data-lq-goto-open") < sheet.index("data-lq-theme-switch") < sheet.index("lqLogoutForm")
    assert 'data-lq-theme-switch' in read(UI_KIT) and 'id="kit-theme"' in read(UI_KIT)


BOOT_DOM = r"""
const attrs = {};
const meta = { content: 'light dark', setAttribute(k, v) { this[k] = v; } };
globalThis.window = globalThis;
globalThis.document = {
    documentElement: {
        setAttribute: (k, v) => { attrs[k] = String(v); },
        removeAttribute: k => { delete attrs[k]; }
    },
    querySelector: sel => (sel === 'meta[name="color-scheme"]' ? meta : null)
};
"""


@NEEDS_NODE
@pytest.mark.parametrize("storage, theme_attr, scheme", [
    ("{ getItem: () => 'dark' }", "dark", "dark"),
    ("{ getItem: () => 'light' }", "light", "light"),
    ("{ getItem: () => 'system' }", None, "light dark"),
    ("{ getItem: () => null }", None, "light dark"),
    ("{ getItem: () => 'purple' }", None, "light dark"),
    ("{ getItem: () => { throw new Error('SecurityError'); } }", None, "light dark"),
    ("THROWS", None, "light dark"),
])
def test_theme_boot_applies_the_stored_choice_and_survives_blocked_storage(storage, theme_attr, scheme):
    if storage == "THROWS":
        setup = "Object.defineProperty(globalThis, 'localStorage', { get() { throw new Error('denied'); } });"
    else:
        setup = f"globalThis.localStorage = {storage};"
    out = _node(BOOT_DOM + setup + "\nattrs['data-theme'] = 'stale';\n" + read(THEME_BOOT)
                + "\nconsole.log(JSON.stringify({ theme: attrs['data-theme'] || null, scheme: meta.content }));")
    assert out == {"theme": theme_attr, "scheme": scheme}


def test_theme_boot_is_tiny_and_self_contained():
    js = read(THEME_BOOT)
    assert "'laqta.theme'" in js and "try {" in js and "catch (e)" in js
    assert "document.write" not in js and "eval(" not in js and "fetch(" not in js
    assert len(js.encode("utf-8")) < 2048


THEME_SETUP = r"""
const html = new El('html');
document.documentElement = html;
const store = {};
let storageBlocked = false;
globalThis.localStorage = {
    getItem: k => { if (storageBlocked) throw new Error('denied'); return k in store ? store[k] : null; },
    setItem: (k, v) => { if (storageBlocked) throw new Error('denied'); store[k] = String(v); }
};
STORED
const schemeMeta = new El('meta'); schemeMeta.setAttribute('content', 'light dark');
const tintMeta = new El('meta'); tintMeta.setAttribute('content', '#0B2226');
nodes['meta[name="color-scheme"]'] = schemeMeta;
nodes['meta[name="theme-color"]'] = tintMeta;
globalThis.getComputedStyle = el => ({
    getPropertyValue: name => (name === '--lq-theme-color' ? (el.getAttribute('data-theme') === 'dark' ? ' #060D10' : ' #0B2226') : '')
});
const SEL = '[data-lq-theme-switch] .lq-segmented__item';
const group = new El('group');
const items = ['light', 'dark', 'system'].map(v => { const b = new El('button', [SEL]); b.setAttribute('data-value', v); b.parent = group; return b; });
group.querySelectorAll = () => items;
document.querySelectorAll = sel => (sel === '[data-lq-theme-switch]' ? [group] : []);
const state = () => ({
    theme: html.getAttribute('data-theme'), stored: store['laqta.theme'] || null,
    scheme: schemeMeta.getAttribute('content'), tint: tintMeta.getAttribute('content'),
    pressed: items.filter(b => b.getAttribute('aria-pressed') === 'true').map(b => b.getAttribute('data-value'))
});
"""


@NEEDS_NODE
def test_set_theme_cycles_persists_and_updates_the_page():
    setup = THEME_SETUP.replace("STORED", "store['laqta.theme'] = 'dark';")
    out = _layout_theme(setup, r"""
const out = { load: state() };
out.light = [window.Laqta.setTheme('light'), state()];
out.cycle = [window.Laqta.setTheme(), window.Laqta.setTheme(), window.Laqta.setTheme()];
out.afterCycle = state();
out.bogus = [window.Laqta.setTheme('purple'), state()];
click(items[1]);
out.click = state();
storageBlocked = true;
out.blocked = [window.Laqta.setTheme('light'), state().theme];
console.log(JSON.stringify(out));
""")
    assert out["load"] == {"theme": "dark", "stored": "dark", "scheme": "dark", "tint": "#060D10", "pressed": ["dark"]}
    assert out["light"] == ["light", {"theme": "light", "stored": "light", "scheme": "light", "tint": "#0B2226",
                                      "pressed": ["light"]}]
    assert out["cycle"] == ["dark", "system", "light"]
    assert out["afterCycle"]["theme"] == "light" and out["afterCycle"]["stored"] == "light"
    assert out["bogus"] == ["system", {"theme": None, "stored": "system", "scheme": "light dark", "tint": "#0B2226",
                                       "pressed": ["system"]}]
    assert out["click"]["theme"] == "dark" and out["click"]["stored"] == "dark" and out["click"]["pressed"] == ["dark"]
    assert out["blocked"] == ["light", "light"], "blocked storage: the page still switches"


@NEEDS_NODE
def test_the_layout_starts_on_system_when_storage_is_blocked():
    setup = THEME_SETUP.replace("STORED", "storageBlocked = true;")
    out = _layout_theme(setup, "console.log(JSON.stringify(state()));")
    assert out == {"theme": None, "stored": None, "scheme": "light dark", "tint": "#0B2226", "pressed": ["system"]}


def _layout_theme(setup: str, script: str):
    return _node("globalThis.window = globalThis;\n" + LAYOUT_DOM + setup + "\n" + read(LAYOUT_JS) + "\n" + script)
