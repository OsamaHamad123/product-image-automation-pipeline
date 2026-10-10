"""Laqta Studio UI foundation: design tokens, lq- Blade components, the new shell and the UI-kit page.

Offline checks in the style of test_dashboard_static.py (file reads, `node --check`, a node harness around the
layout's script), plus two checks that boot the Laravel app through the PHP CLI: every Blade view compiles to
valid PHP, and /ui-kit renders through the HTTP kernel. Those are skipped when php or dashboard/vendor is missing.
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
from blade_scripts import inline_scripts

ROOT = Path(__file__).resolve().parents[1]
DASH = ROOT / "dashboard"
VIEWS = DASH / "resources" / "views"
COMPONENTS = VIEWS / "components" / "lq"
LAYOUT = VIEWS / "layouts" / "laqta.blade.php"
LAYOUT_JS = DASH / "public" / "js" / "layout.js"       # the shell's script, loaded by the layout (cached: ?v=)
UI_KIT = VIEWS / "dashboard" / "ui_kit.blade.php"
CSS = DASH / "public" / "css" / "laqta.css"
ROUTES = DASH / "routes" / "web.php"
VENDOR_AUTOLOAD = DASH / "vendor" / "autoload.php"

PHP = shutil.which("php")
NODE = shutil.which("node")
NEEDS_LARAVEL = pytest.mark.skipif(PHP is None or not VENDOR_AUTOLOAD.exists(),
                                   reason="php or dashboard/vendor is not installed")

REQUIRED_COMPONENTS = ["icon", "button", "chip", "card", "kpi", "alert", "progress", "check-row", "empty-state"]

REQUIRED_ICONS = ["home", "review", "run", "health", "settings", "search", "check", "x", "alert", "external",
                  "refresh", "play", "pause", "stop", "grid", "upload", "link", "shield", "barcode", "sparkle"]

# Token -> value from the approved identity (None: only has to exist).
REQUIRED_TOKENS = {
    "--lq-teal-700": "#0F766E", "--lq-teal-800": "#0B5F58", "--lq-teal-500": "#14B8A6", "--lq-teal-soft": "#E7F1F0",
    "--lq-saffron-500": "#F59E0B", "--lq-saffron-700": "#B45309", "--lq-saffron-50": "#FFF7E6",
    "--lq-ink-900": "#0B2226", "--lq-ink-800": "#12313A", "--lq-ink-700": "#1C4450",
    "--lq-ink-text": "#B8CDD1", "--lq-ink-muted": "#8FB0B6",
    "--lq-bg": "#F3F6F8", "--lq-surface": "#FFFFFF", "--lq-surface-2": "#F8FAFB",
    "--lq-border": "#E2E8EC", "--lq-border-strong": "#CBD5DB",
    "--lq-text": "#0E1C24", "--lq-text-2": "#4F6270", "--lq-text-3": None,
    "--lq-success": "#15803D", "--lq-success-bg": "#ECFDF3",
    "--lq-warning": "#B45309", "--lq-warning-bg": "#FFF7E6",
    "--lq-danger": "#C8313A", "--lq-danger-text": "#B42330", "--lq-danger-bg": "#FEF1F2",
    "--lq-info": "#2F6FEB", "--lq-info-bg": "#EEF4FF",
    "--lq-radius-sm": "8px", "--lq-radius-md": "10px", "--lq-radius-lg": "12px", "--lq-radius-xl": "16px",
    "--lq-space-1": "4px", "--lq-space-2": "8px", "--lq-space-3": "12px", "--lq-space-4": "16px",
    "--lq-font-ui": None, "--lq-font-display": None, "--lq-shadow-card": None,
}

REQUIRED_CLASSES = [
    # shell
    "lq-body", "lq-shell", "lq-sidebar", "lq-brand", "lq-nav", "lq-nav__item", "lq-nav__badge", "lq-runcard",
    "lq-main", "lq-page-header", "lq-page-title",
    # surfaces and numbers
    "lq-card", "lq-kpi", "lq-kpi__value", "lq-stat",
    # buttons and keys
    "lq-btn", "lq-btn--primary", "lq-btn--secondary", "lq-btn--danger", "lq-btn--ghost", "lq-btn--sm",
    "lq-btn--lg", "lq-kbd",
    # chips and alerts
    "lq-chip", "lq-chip--proposed", "lq-chip--warning", "lq-chip--none", "lq-chip--not-found",
    "lq-chip--approved", "lq-chip--error",
    "lq-alert", "lq-alert--info", "lq-alert--warning", "lq-alert--danger", "lq-alert--success",
    # forms
    "lq-input", "lq-select", "lq-check", "lq-switch", "lq-segmented",
    # progress, table, check row
    "lq-progress", "lq-progress__bar", "lq-progress--stacked", "lq-progress__seg", "lq-table", "lq-check-row",
    # feedback (the layout script builds lq-toast--<variant> at runtime)
    "lq-toast", "lq-toast--success", "lq-toast--warning", "lq-toast--danger", "lq-toast--info", "lq-spinner",
    "lq-empty", "lq-skeleton",
]

NAV_ROUTES = ["dashboard.index", "dashboard.catalog", "dashboard.batch_automation", "dashboard.diagnostics",
              "dashboard.settings"]

CLASS_RE = re.compile(r"(?<![\w-])lq-[a-z0-9]+(?:(?:__|--|-)[a-z0-9]+)*")
CSS_CLASS_RE = re.compile(r"\.(lq-[a-z0-9]+(?:(?:__|--|-)[a-z0-9]+)*)")
TOKEN_RE = re.compile(r"--lq-[a-z0-9]+(?:-[a-z0-9]+)*")


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def component_files():
    return sorted(COMPONENTS.glob("*.blade.php"))


def new_blade_files():
    return component_files() + [LAYOUT, UI_KIT]


def css_tokens() -> dict:
    root = re.search(r":root\s*\{(.*?)\n\}", read(CSS), re.DOTALL)
    assert root, "laqta.css has no :root block"
    return {name: value.strip() for name, value in re.findall(r"(--lq-[a-z0-9-]+)\s*:\s*([^;]+);", root.group(1))}


def css_classes() -> set:
    css = re.sub(r"/\*.*?\*/", "", read(CSS), flags=re.DOTALL)
    return set(CSS_CLASS_RE.findall(css))


def icon_names() -> list:
    return re.findall(r"^\s*'([a-z-]+)' => 'M", read(COMPONENTS / "icon.blade.php"), re.MULTILINE)


# ---------------------------------------------------------------------------
# Tokens and CSS
# ---------------------------------------------------------------------------

def test_css_defines_the_identity_tokens():
    tokens = css_tokens()
    for name, value in REQUIRED_TOKENS.items():
        assert name in tokens, name
        if value is not None:
            assert tokens[name].upper() == value.upper(), (name, tokens[name])


def test_every_custom_property_used_is_defined():
    css = read(CSS)
    # :root tokens, plus the few component-scoped ones (e.g. --lq-chip-dot set per chip variant)
    defined = set(re.findall(r"(--lq-[a-z0-9-]+)\s*:", css))
    used = set(re.findall(r"var\((--lq-[a-z0-9-]+)", css))
    for path in new_blade_files():
        used.update(TOKEN_RE.findall(read(path)))
    missing = sorted(used - defined)
    assert not missing, missing


def test_css_defines_every_component_class_in_use():
    defined = css_classes()
    for name in REQUIRED_CLASSES:
        assert name in defined, name
    for path in new_blade_files():
        used = set(CLASS_RE.findall(read(path)))
        missing = sorted(used - defined)
        assert not missing, f"{path.name}: {missing}"


def test_css_is_rtl_ready_and_accessible():
    css = read(CSS)
    assert ":focus-visible" in css
    assert "@media (max-width: 980px)" in css
    assert "prefers-reduced-motion" in css
    # logical properties instead of physical left/right margins and offsets
    for prop in ("margin-inline", "inset-inline", "padding-inline"):
        assert prop in css, prop
    for physical in ("margin-left", "margin-right", "padding-left", "padding-right"):
        assert physical not in css, physical
    assert not re.search(r"(?<![-\w])(left|right)\s*:", css), "use inset-inline-start/end"


def _hex_luminance(hex_value: str) -> float:
    hex_value = hex_value.lstrip("#")
    channels = [int(hex_value[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast(fg: str, bg: str) -> float:
    a, b = sorted((_hex_luminance(fg), _hex_luminance(bg)), reverse=True)
    return (a + 0.05) / (b + 0.05)


@pytest.mark.parametrize("fg, bg", [
    ("--lq-text", "--lq-bg"), ("--lq-text-2", "--lq-bg"), ("--lq-text-3", "--lq-bg"),
    ("--lq-text-3", "--lq-surface"), ("--lq-text-3", "--lq-surface-2"), ("--lq-text-2", "--lq-neutral-bg"),
    ("#FFFFFF", "--lq-teal-700"), ("#FFFFFF", "--lq-teal-800"), ("#FFFFFF", "--lq-danger"),
    ("--lq-teal-700", "--lq-surface"), ("--lq-teal-700", "--lq-teal-soft"),
    ("--lq-success", "--lq-success-bg"), ("--lq-warning", "--lq-warning-bg"), ("--lq-warning", "--lq-surface"),
    ("--lq-danger-text", "--lq-danger-bg"), ("--lq-info-text", "--lq-info-bg"),
    ("--lq-success-ink", "--lq-success-bg"), ("--lq-warning-ink", "--lq-warning-bg"),
    ("--lq-danger-ink", "--lq-danger-bg"),
    ("--lq-ink-text", "--lq-ink-900"), ("--lq-ink-muted", "--lq-ink-900"), ("--lq-ink-muted", "--lq-ink-800"),
    ("--lq-saffron-ink", "--lq-saffron-500"),
])
def test_text_colours_meet_contrast_4_5(fg, bg):
    tokens = css_tokens()
    fg_hex = tokens.get(fg, fg)
    bg_hex = tokens.get(bg, bg)
    assert re.fullmatch(r"#[0-9A-Fa-f]{6}", fg_hex) and re.fullmatch(r"#[0-9A-Fa-f]{6}", bg_hex), (fg_hex, bg_hex)
    assert _contrast(fg_hex, bg_hex) >= 4.5, (fg, bg, round(_contrast(fg_hex, bg_hex), 2))


# ---------------------------------------------------------------------------
# Components
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", REQUIRED_COMPONENTS)
def test_required_component_exists_with_props(name):
    path = COMPONENTS / f"{name}.blade.php"
    assert path.exists(), path
    assert "@props([" in read(path), f"{name} declares no props"


def test_components_escape_all_data():
    for path in new_blade_files():
        raw = re.findall(r"\{!!(.*?)!!\}", read(path))
        if path.name == "icon.blade.php":
            assert [r.strip() for r in raw] == ["$lqIconPath"], raw
        else:
            assert not raw, f"{path.name} prints unescaped output: {raw}"


def test_icon_set_is_complete_and_every_used_icon_exists():
    names = icon_names()
    for name in REQUIRED_ICONS:
        assert name in names, name
    assert len(names) == len(set(names)), "duplicate icon name"

    kit = read(UI_KIT)
    kit_list = re.search(r"\$kitIcons = \[(.*?)\];", kit, re.DOTALL).group(1)
    assert re.findall(r"'([a-z-]+)'", kit_list) == names, "the UI kit must show every icon, in order"

    used = set()
    for path in new_blade_files():
        text = read(path)
        used.update(re.findall(r"<x-lq\.icon[^>]*?\sname=\"([a-z-]+)\"", text))
        used.update(re.findall(r"<x-lq\.[a-z-]+[^>]*?\sicon(?:-end)?=\"([a-z-]+)\"", text))
        used.update(re.findall(r"'icon' => '([a-z-]+)'", text))
    used.update(re.findall(r"=> \['lq-[a-z-]+', '([a-z-]+)'", read(COMPONENTS / "alert.blade.php")))
    used.update(re.findall(r"'lq-check-row__status--[a-z]+', '([a-z-]+)'", read(COMPONENTS / "check-row.blade.php")))
    assert used, "no icon usage found"
    assert not sorted(used - set(names)), sorted(used - set(names))


def test_new_files_have_no_emoji():
    emoji = re.compile("[\U0001F300-\U0001FAFF☀-➿️]")
    for path in new_blade_files() + [CSS]:
        found = emoji.findall(read(path))
        assert not found, f"{path.name}: {found}"


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------

def test_layout_is_arabic_rtl_shell():
    layout = read(LAYOUT)
    assert '<html lang="ar" dir="rtl">' in layout
    assert '<meta name="csrf-token" content="{{ csrf_token() }}">' in layout
    for directive in ("@yield('content')", "@stack('styles')", "@stack('scripts')"):
        assert directive in layout, directive
    for text in ("لقطة", "استوديو صور المنتجات", "الرئيسية", "المراجعة", "التشغيل", "الصحة والتكلفة", "الإعدادات"):
        assert text in layout, text
    positions = [layout.index(f"'route' => '{name}'") for name in NAV_ROUTES]
    assert positions == sorted(positions), "nav order must follow the designs"
    assert "request()->routeIs(" in layout and 'aria-current="page"' in layout
    assert "<x-lq.run-card live" in layout and "data-lq-review-count" in layout


def test_layout_loads_only_google_fonts_and_local_css():
    layout = read(LAYOUT)
    hosts = set(re.findall(r"(?:href|src)=\"(?:https?:)?//([^/\"]+)", layout))
    assert hosts == {"fonts.googleapis.com", "fonts.gstatic.com"}, hosts
    # one script: the shell's own file, versioned so the browser caches it; no inline block
    assert re.findall(r"<script[^>]*>", layout, flags=re.IGNORECASE) == [
        "<script src=\"{{ asset('js/layout.js') }}?v={{ @filemtime(public_path('js/layout.js')) ?: '1' }}\">"]
    assert not inline_scripts(layout)
    assert layout.index("js/layout.js") < layout.index("@yield('scripts')") < layout.index("@stack('scripts')")
    stylesheets = re.findall(r"<link rel=\"stylesheet\" href=\"([^\"]+)\"", layout)
    assert len(stylesheets) == 2, stylesheets
    fonts, local = stylesheets
    assert fonts.startswith("https://fonts.googleapis.com/css2?")
    assert "family=Alexandria:wght@500;600;700" in fonts and "family=Readex+Pro:wght@400;500;600;700" in fonts
    assert local.startswith("{{ asset('css/laqta.css') }}")

    css = read(CSS)
    assert "@import" not in css
    assert not re.search(r"url\(\s*['\"]?(?:https?:)?//", css), "laqta.css must not load remote files"
    for path in component_files() + [UI_KIT]:
        assert not re.search(r"(?:href|src)=\"(?:https?:)?//", read(path)), path.name


def _script_blocks(path: Path):
    blocks = inline_scripts(read(path))
    assert blocks, f"no inline <script> block found in {path.name}"
    # Blade echo tags are replaced by a string literal, as they would be after rendering.
    return [re.sub(r"\{\{.*?\}\}", "''", block) for block in blocks]


@pytest.mark.skipif(NODE is None, reason="node is not installed")
@pytest.mark.parametrize("path", [LAYOUT_JS, UI_KIT], ids=lambda p: p.name)
def test_inline_js_parses(path, tmp_path):
    for i, block in enumerate([read(path)] if path.suffix == ".js" else _script_blocks(path)):
        js = tmp_path / f"{path.stem}_{i}.js"
        js.write_text(block, encoding="utf-8")
        result = subprocess.run([NODE, "--check", str(js)], capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, f"{path.name} script block {i + 1}: {result.stderr}"


def test_run_card_copy_matches_the_layout_script():
    """The static component and the live script must word every state the same way."""
    php = {state: rest for state, *rest in re.findall(
        r"'(\w+)' => \['label' => '([^']*)', 'text' => '([^']*)', 'link' => '([^']*)'\]",
        read(COMPONENTS / "run-card.blade.php"))}
    js = {state: rest for state, *rest in re.findall(
        r"(\w+): \{ label: '([^']*)', text: '([^']*)', link: '([^']*)' \}", read(LAYOUT_JS))}
    assert set(php) == {"loading", "idle", "running", "paused", "stopping", "error", "stuck", "unknown"}
    assert php == js
    assert php["idle"][1] == "ما في تشغيل هلق" and php["idle"][2] == "ابدأ تشغيل جديد ←"


def test_layout_script_is_plain_and_safe():
    script = read(LAYOUT_JS)
    assert "'/api/batch-status'" in script and "POLL_MS = 5000" in script
    assert "innerHTML" not in script, "status text is written with textContent only"
    assert "eval(" not in script and "new Function" not in script
    assert "import " not in script and "require(" not in script


RUN_STATUS_CASES = {
    # current /api/batch-status shape (ApiController::batchStatus)
    "idle": {"is_running": False, "status": "idle", "total": 0, "current": 0, "success": 0, "failed": 0,
             "current_product": "", "pause_requested": 0, "queue": {"ready_for_review": 33},
             "ready_for_review": 33, "approved": 18, "failed_by_code": {}, "notice": ""},
    "curation_pending": {"is_running": False, "status": "curation_pending", "total": 40, "current": 40,
                         "ready_for_review": 5, "notice": "SERPER_CREDIT: رصيد Serper انتهى أو المفتاح مرفوض"},
    "running": {"is_running": True, "status": "running", "total": 40, "current": 24, "pause_requested": "0",
                "ready_for_review": "19"},
    "starting": {"is_running": True, "status": "idle", "total": 0, "current": 0},
    "pre_caching": {"is_running": True, "status": "pre_caching", "total": 10, "current": 3},
    "paused": {"is_running": True, "status": "running", "total": 10, "current": 5, "pause_requested": "1"},
    "sheets_error": {"is_running": False, "status": "error",
                     "notice": "SHEETS_UNAVAILABLE: Google Sheets connection failed"},
    "provider_down": {"is_running": False, "status": "provider_down",
                      "notice": "PROVIDER_DOWN: search providers unavailable; remaining rows stay pending"
                                " | SERPER_CREDIT: رصيد Serper انتهى أو المفتاح مرفوض"},
    "provider_down_silent": {"status": "provider_down", "notice": ""},
    "unknown_code": {"status": "error", "notice": "SOMETHING_NEW: boom at line 3"},
    # a future shape with a `run` object and separate counts
    "future_running": {"run": {"state": "running", "processed": 12, "total": 30, "rows": {"from": 62, "to": 101}},
                       "counts": {"ready_for_review": 7}},
    "future_rows_text": {"run": {"status": "running", "done": 3, "total_items": 4, "rows": "5، 9، 14"}},
    "future_error": {"run": {"status": "provider_down", "notice": "GEMINI_DOWN: Gemini لا يستجيب"}},
    "future_idle": {"run": None, "counts": {"ready_for_review": 0}},
    "overflow": {"is_running": True, "status": "running", "total": 10, "current": 12},
    # the fields Phase 1 and the Run page added: phase, the Arabic alert, and why a run is stuck
    "phase_stopping": {"is_running": True, "status": "running", "phase": "stopping", "total": 8, "current": 3,
                       "stop_requested": 1},
    "phase_review": {"is_running": False, "status": "curation_pending", "phase": "review", "ready_for_review": 4},
    "phase_error_alert": {"is_running": False, "status": "error", "phase": "error",
                          "alert": "تعذر الوصول إلى Google Sheet: تأكد من الرابط.",
                          "notice": "SHEETS_UNAVAILABLE: Google Sheets connection failed"},
    "stuck_dead_worker": {"is_running": False, "status": "running", "phase": "idle", "total": 5, "current": 2,
                          "stuck": "الحالة بتقول إنو في تشغيل، بس ما في عامل شغّال بالخلفية."},
    # garbage never throws
    "null": None,
    "list": [1, 2],
    "string": "oops",
}


@pytest.fixture(scope="module")
def run_status():
    if NODE is None:
        pytest.skip("node is not installed")
    harness = """
globalThis.window = globalThis;
globalThis.document = {
    body: null, hidden: false,
    querySelector: () => null, querySelectorAll: () => [],
    addEventListener: () => {}, dispatchEvent: () => true,
};
""" + read(LAYOUT_JS) + f"""
const cases = {json.dumps(RUN_STATUS_CASES, ensure_ascii=False)};
const out = {{}};
for (const [name, data] of Object.entries(cases)) {{
    const n = window.Laqta.normalizeRunStatus(data);
    out[name] = {{ n, view: window.Laqta.describeRunStatus(n) }};
}}
console.log(JSON.stringify(out));
"""
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as fh:
        fh.write(harness)
        path = fh.name
    try:
        result = subprocess.run([NODE, path], capture_output=True, text=True, timeout=60)
    finally:
        os.unlink(path)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_run_card_idle_states(run_status):
    for name in ("idle", "curation_pending", "future_idle", "null", "list", "string"):
        view = run_status[name]["view"]
        assert view["state"] == "idle", name
        assert view["text"] == "ما في تشغيل هلق", name
        assert view["pct"] is None and view["count"] == "", name


def test_run_card_running_and_paused(run_status):
    running = run_status["running"]["view"]
    assert (running["state"], running["label"], running["count"], running["pct"]) == ("running", "يعمل", "24 / 40", 60)
    assert run_status["pre_caching"]["view"]["count"] == "3 / 10"

    starting = run_status["starting"]["view"]
    assert starting["state"] == "running" and starting["pct"] is None and starting["count"] == ""

    paused = run_status["paused"]["view"]
    assert (paused["state"], paused["label"], paused["pct"]) == ("paused", "متوقف مؤقتاً", 50)

    assert run_status["overflow"]["view"]["pct"] == 100 and run_status["overflow"]["view"]["count"] == "10 / 10"


def test_run_card_error_states_speak_plain_arabic(run_status):
    sheets = run_status["sheets_error"]["view"]
    assert sheets["state"] == "error" and sheets["text"] == "تعذّر الاتصال بـ Google Sheet."

    down = run_status["provider_down"]["view"]
    assert down["state"] == "error"
    assert "مصادر البحث غير متاحة" in down["text"] and "رصيد Serper انتهى" in down["text"]
    assert "PROVIDER_DOWN" not in down["text"] and "SERPER_CREDIT" not in down["text"]

    assert "مصادر البحث غير متاحة" in run_status["provider_down_silent"]["view"]["text"]
    assert run_status["unknown_code"]["view"]["text"] == "توقف التشغيل بسبب عطل."
    assert run_status["future_error"]["view"]["text"] == "Gemini لا يستجيب."
    for name in ("sheets_error", "provider_down", "future_error"):
        assert run_status[name]["view"]["pct"] is None, name


def test_run_card_follows_the_run_pages_phase_alert_and_stuck(run_status):
    """The sidebar says what the Run page says (same /api/batch-status fields): no «يعمل» on a stuck run."""
    stopping = run_status["phase_stopping"]["view"]
    assert (stopping["state"], stopping["label"], stopping["count"]) == ("stopping", "عم يوقف", "3 / 8")
    review = run_status["phase_review"]
    assert review["view"]["state"] == "idle" and review["n"]["reviewCount"] == 4
    error = run_status["phase_error_alert"]["view"]
    assert error["state"] == "error" and error["text"] == "تعذر الوصول إلى Google Sheet: تأكد من الرابط."
    stuck = run_status["stuck_dead_worker"]["view"]
    assert (stuck["state"], stuck["label"]) == ("stuck", "عالق")
    assert stuck["text"] == "الحالة بتقول إنو في تشغيل، بس ما في عامل شغّال بالخلفية."
    assert stuck["pct"] is None and stuck["count"] == ""


def test_run_card_reads_the_future_run_object(run_status):
    view = run_status["future_running"]["view"]
    assert (view["state"], view["text"], view["count"], view["pct"]) == ("running", "الصفوف 62–101", "12 / 30", 40)
    assert run_status["future_rows_text"]["view"]["text"] == "الصفوف 5، 9، 14"
    assert run_status["future_rows_text"]["view"]["count"] == "3 / 4"


def test_review_badge_count_from_either_shape(run_status):
    assert run_status["idle"]["n"]["reviewCount"] == 33
    assert run_status["running"]["n"]["reviewCount"] == 19
    assert run_status["future_running"]["n"]["reviewCount"] == 7
    assert run_status["future_idle"]["n"]["reviewCount"] == 0
    assert run_status["null"]["n"]["reviewCount"] is None


# ---------------------------------------------------------------------------
# UI kit page and route
# ---------------------------------------------------------------------------

def test_ui_kit_route_is_appended_last():
    lines = [line for line in read(ROUTES).splitlines() if line.strip()]
    assert lines[-1].startswith("Route::view('/ui-kit', 'dashboard.ui_kit')->name('dashboard.ui_kit');")
    assert read(ROUTES).count("'/ui-kit'") == 1


def test_ui_kit_shows_every_component():
    kit = read(UI_KIT)
    assert kit.startswith("@extends('layouts.laqta')")
    for name in [p.name[:-len(".blade.php")] for p in component_files()]:
        assert f"<x-lq.{name}" in kit, name
    for status in ("proposed", "warning", "none", "not-found", "approved", "error"):
        assert f'<x-lq.chip status="{status}"' in kit, status
    for variant in ("info", "warning", "danger", "success"):
        assert f'<x-lq.alert variant="{variant}"' in kit, variant
    for state in ("idle", "running", "paused", "error"):
        assert f'<x-lq.run-card state="{state}"' in kit, state


# ---------------------------------------------------------------------------
# Laravel: every view compiles, and /ui-kit renders
# ---------------------------------------------------------------------------

def _laravel_env(tmp_path: Path) -> dict:
    env = dict(os.environ)
    env.update({
        "APP_ENV": "testing",
        "APP_KEY": "base64:" + "A" * 43 + "=",
        "APP_DEBUG": "true",
        "SESSION_DRIVER": "array",
        "CACHE_STORE": "array",
        "LOG_CHANNEL": "stderr",
        "VIEW_COMPILED_PATH": str(tmp_path),
    })
    return env


@NEEDS_LARAVEL
def test_every_blade_view_compiles_to_valid_php(tmp_path):
    compiled = tmp_path / "views"
    compiled.mkdir()
    result = subprocess.run([PHP, "artisan", "view:cache"], cwd=DASH, env=_laravel_env(compiled),
                            capture_output=True, text=True, timeout=180)
    assert result.returncode == 0, result.stdout + result.stderr
    files = sorted(compiled.glob("*.php"))
    sources = "\n".join(read(f) for f in files)
    for path in new_blade_files():
        assert path.name in sources, f"{path.name} was not compiled"
    for f in files:
        lint = subprocess.run([PHP, "-l", str(f)], capture_output=True, text=True, timeout=60)
        assert lint.returncode == 0, lint.stdout + lint.stderr


@pytest.fixture(scope="module")
def rendered(tmp_path_factory):
    if PHP is None or not VENDOR_AUTOLOAD.exists():
        pytest.skip("php or dashboard/vendor is not installed")
    compiled = tmp_path_factory.mktemp("compiled_views")
    dash = str(DASH).replace("\\", "/")
    script = f"""<?php
require '{dash}/vendor/autoload.php';
$app = require '{dash}/bootstrap/app.php';
$kernel = $app->make(Illuminate\\Contracts\\Http\\Kernel::class);
$response = $kernel->handle(Illuminate\\Http\\Request::create('/ui-kit', 'GET'));
$out = ['status' => $response->getStatusCode(), 'body' => $response->getContent()];

// The active nav item follows the current route.
$request = Illuminate\\Http\\Request::create('/settings', 'GET');
$route = $app['router']->getRoutes()->match($request);
$request->setRouteResolver(fn () => $route);
$app->instance('request', $request);
$out['settings'] = view('layouts.laqta')->render();

// Hostile data is escaped by every component.
$x = '<script>alert(1)</script>';
$blade = Illuminate\\Support\\Facades\\Blade::class;
$out['escaped'] = $blade::render(
    '<x-lq.chip :label="$x" /><x-lq.kpi :label="$x" :value="$x" :note="$x" />'
    . '<x-lq.check-row :label="$x" :sheet="$x" :image="$x" />'
    . '<x-lq.alert :title="$x">{{{{ $x }}}}</x-lq.alert><x-lq.button icon="x" :label="$x" />'
    . '<x-lq.progress :label="$x" :title="$x" :value="5" /><x-lq.empty-state :title="$x" :text="$x" />'
    . '<x-lq.card :title="$x" :meta="$x">ok</x-lq.card><x-lq.run-card state="error" :text="$x" />',
    ['x' => $x]
);
$out['unknown_icon'] = $blade::render('<x-lq.icon name="nope" />');
$out['titled'] = $blade::render(
    "@extends('layouts.laqta')\n@section('title', \\$t)\n@section('lq_nav', 'review')\n"
    . "@section('content')<p>محتوى</p>@endsection\n@push('scripts')<script>window.pushed = 1;</script>@endpush",
    ['t' => 'أ & ب <b>']
);
$out['stacked'] = $blade::render(
    '<x-lq.progress label="نتائج" :max="40" :segments="$s" />',
    ['s' => [['label' => 'مقترحة', 'value' => 13, 'tone' => 'success'], ['label' => 'بلا اقتراح', 'value' => 7]]]
);
echo json_encode($out, JSON_UNESCAPED_UNICODE);
"""
    with tempfile.NamedTemporaryFile("w", suffix=".php", delete=False, encoding="utf-8") as fh:
        fh.write(script)
        path = fh.name
    try:
        result = subprocess.run([PHP, path], cwd=DASH, env=_laravel_env(compiled),
                                capture_output=True, text=True, timeout=120)
    finally:
        os.unlink(path)
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-3000:]
    return json.loads(result.stdout)


def test_ui_kit_renders(rendered):
    assert rendered["status"] == 200, rendered["body"][:2000]
    body = rendered["body"]
    assert body.lstrip().startswith("<!DOCTYPE html>")
    assert '<html lang="ar" dir="rtl">' in body
    assert "<title>مكوّنات الواجهة · لقطة" in body
    assert 'class="lq-body' in body and 'class="lq-sidebar"' in body and "data-lq-runcard" in body
    assert not re.search(r"lq-nav__item[^>]*aria-current", body), "the UI kit is not a nav destination"
    for marker in ("lq-kpi", "lq-check-row", "lq-empty", "lq-progress--stacked", 'role="progressbar"',
                   "lq-chip--not-found", "lq-switch__input", "lq-segmented__option", 'data-lq-segmented',
                   'data-lq-confirm="', 'aria-keyshortcuts="Enter"'):
        assert marker in body, marker
    for leftover in ("{{", "{!!", "@props", "<x-lq", "</x-lq", "x-slot"):
        assert leftover not in body, leftover


def test_nav_marks_the_current_route(rendered):
    html = rendered["settings"]
    current = re.findall(r"<a href=\"([^\"]+)\"[^>]*aria-current=\"page\"", html)
    assert len(current) == 1 and current[0].endswith("/settings"), current
    assert "is-active" in re.search(r"<a href=\"[^\"]+/settings\"[^>]*>", html).group(0)


def test_page_opts_into_the_layout(rendered):
    html = rendered["titled"]
    assert "<title>أ &amp; ب &lt;b&gt; · لقطة</title>" in html, "the title is escaped exactly once"
    assert re.search(r"<main id=\"lq-main\"[^>]*>\s*<p>محتوى</p>", html)
    assert "<script>window.pushed = 1;</script>" in html
    current = re.findall(r"<a href=\"([^\"]+)\"[^>]*aria-current=\"page\"", html)
    assert len(current) == 1 and current[0].endswith("/catalog"), "lq_nav overrides the route"
    assert re.search(r"<title>\s*لقطة · استوديو صور المنتجات</title>", rendered["settings"])


def test_components_escape_hostile_data(rendered):
    html = rendered["escaped"]
    assert "<script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert rendered["unknown_icon"].strip() == ""


def test_stacked_progress_widths(rendered):
    html = rendered["stacked"]
    assert re.findall(r"style=\"width: ([\d.]+)%\"", html) == ["32.5", "17.5"]
    assert 'aria-label="نتائج: مقترحة 13، بلا اقتراح 7"' in html
