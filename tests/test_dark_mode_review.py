"""Dark mode on the review screen (/catalog): review.css and cutout.css take every colour from a token.

The app theme is <html data-theme="light|dark"> (no attribute = follow the system); laqta.css holds the --lq-* dark set.
review.css adds --rv-* tokens: the theme-aware ones get a dark value under BOTH dark selectors, and the preview-stage
ones (--rv-stage-*, the «غامق / فاتح / مربعات» backgrounds behind a transparent packshot) are fixed on purpose.
"""
import re
from pathlib import Path

DASH = Path(__file__).resolve().parent.parent / "dashboard"
PAGES = DASH / "public" / "css" / "pages"
REVIEW_CSS = PAGES / "review.css"
CUTOUT_CSS = PAGES / "cutout.css"
REVIEW_JS = DASH / "public" / "js" / "review"

DARK_ATTR = ':root[data-theme="dark"]'
DARK_MEDIA = '@media (prefers-color-scheme: dark)'
DARK_SYSTEM = ':root:not([data-theme="light"])'
COLOR = re.compile(r"#[0-9a-fA-F]{3,8}\b|\b(?:rgba?|hsla?)\(|(?<![\w-])(?:white|black)(?![\w-])")
DECL = re.compile(r"(--rv-[a-z0-9-]+)\s*:\s*([^;]+);")


def css(path):
    return re.sub(r"/\*.*?\*/", "", path.read_text(encoding="utf-8"), flags=re.DOTALL)


def block(text, start):
    """The body of the {...} that follows `start` (balanced braces)."""
    i = text.index("{", text.index(start))
    depth = 0
    for j in range(i, len(text)):
        depth += {"{": 1, "}": -1}.get(text[j], 0)
        if depth == 0:
            return text[i + 1:j]
    raise AssertionError(start)


def light_blocks(text):
    """Every plain `:root { ... }` body (the light / fixed definitions)."""
    return [m.group(1) for m in re.finditer(r"(?<![\w\]\)])\s*:root\s*\{([^{}]*)\}", text)]


def dark_blocks(text):
    attr = block(text, DARK_ATTR)
    media = block(text, DARK_MEDIA)
    system = block(media, DARK_SYSTEM)
    return dict(DECL.findall(attr)), dict(DECL.findall(system))


def without_token_blocks(text):
    out = re.sub(re.escape(DARK_MEDIA) + r"\s*\{\s*" + re.escape(DARK_SYSTEM) + r"\s*\{[^{}]*\}\s*\}", "", text)
    out = re.sub(re.escape(DARK_ATTR) + r"\s*\{[^{}]*\}", "", out)
    return re.sub(r"(?<![\w\]\)]):root\s*\{[^{}]*\}", "", out)


def test_no_raw_colour_outside_the_token_blocks():
    for path in (REVIEW_CSS, CUTOUT_CSS):
        rest = without_token_blocks(css(path))
        rest = re.sub(r"white-space\s*:", "", rest)
        found = [m.group(0) for m in COLOR.finditer(rest)]
        assert not found, (path.name, found)


def test_token_blocks_only_define_tokens():
    text = css(REVIEW_CSS)
    for body in light_blocks(text):
        for line in filter(None, (l.strip() for l in body.split(";"))):
            assert line.startswith("--rv-"), line
    assert not light_blocks(css(CUTOUT_CSS)), "cutout.css uses the review tokens, it defines none"


def test_every_theme_aware_token_has_a_dark_value_under_both_selectors():
    text = css(REVIEW_CSS)
    light = {}
    for body in light_blocks(text):
        light.update(DECL.findall(body))
    attr, system = dark_blocks(text)
    aware = {name for name in light if not name.startswith("--rv-stage-")}
    assert aware, "no theme-aware --rv-* tokens found"
    assert not sorted(aware - set(attr)), sorted(aware - set(attr))
    assert not sorted(aware - set(system)), sorted(aware - set(system))
    assert attr == system, "the two dark blocks must hold the same values"
    assert not sorted(set(attr) - set(light)), "a dark-only token has no light value"


def test_preview_stage_tokens_do_not_follow_the_app_theme():
    text = css(REVIEW_CSS)
    attr, system = dark_blocks(text)
    light = {}
    for body in light_blocks(text):
        light.update(DECL.findall(body))
    stage = {n for n in light if n.startswith("--rv-stage-")}
    assert {"--rv-stage-dark", "--rv-stage-light", "--rv-stage-checker-a", "--rv-stage-checker-b"} <= stage
    assert not [n for n in attr if n.startswith("--rv-stage-")]
    assert not [n for n in system if n.startswith("--rv-stage-")]
    # the stage stays a dark grey, white and a light checker in either app theme
    assert light["--rv-stage-dark"].upper() == "#121212" and light["--rv-stage-light"].upper() == "#FFFFFF"
    for path in (REVIEW_CSS, CUTOUT_CSS):
        body = css(path)
        for mode in ("dark", "light", "checker"):
            assert f"rv-theme--{mode}" in body, (path.name, mode)
        assert "var(--rv-stage-checker-b)" in body and "var(--rv-stage-dark)" in body, path.name


def test_every_rv_token_used_is_defined():
    defined = set(re.findall(r"(--rv-[a-z0-9-]+)\s*:", css(REVIEW_CSS)))
    # layout values the scripts set at run time (single.js: --rv-jobs-bottom)
    defined |= {n for p in REVIEW_JS.glob("*.js") for n in re.findall(r"setProperty\(\s*'(--rv-[a-z0-9-]+)'", p.read_text(encoding="utf-8"))}
    for path in (REVIEW_CSS, CUTOUT_CSS):
        used = set(re.findall(r"var\((--rv-[a-z0-9-]+)", css(path)))
        assert not sorted(used - defined), (path.name, sorted(used - defined))


def test_images_are_never_filtered():
    for path in (REVIEW_CSS, CUTOUT_CSS):
        body = css(path)
        assert not re.search(r"(?<![\w-])filter\s*:", body), path.name
        assert "mix-blend-mode" not in body, path.name


def test_review_scripts_set_no_inline_colours():
    for path in sorted(REVIEW_JS.glob("*.js")):
        src = re.sub(r"/\*.*?\*/", "", path.read_text(encoding="utf-8"), flags=re.DOTALL)
        src = re.sub(r"(?m)^\s*//.*$", "", src)
        literal = re.search(r"#[0-9a-fA-F]{3,8}\b|\b(?:rgba?|hsla?)\(", src)    # flag keys like `white` are data, not colours
        assert not literal, (path.name, literal and literal.group(0))
        assert not re.search(r"\.style\.(?:color|background\w*|border\w*Color|outlineColor|fill|stroke)\b", src), path.name
        assert not re.search(r"style\s*=\s*['\"`][^'\"`]*(?:color|background)", src), path.name
        for name in re.findall(r"setProperty\(\s*'([^']+)'", src):
            assert "color" not in name and "bg" not in name, (path.name, name)
