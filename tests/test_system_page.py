"""«النظام» (phase 3 of the UI redesign): Health, Settings and Setup merged into one page with one side rail.

- /system?tab=… (SystemController, resources/views/dashboard/system.blade.php): four groups, «الحالة» (status, search,
  logs) for both roles, «الإعدادات» (the six settings tabs), «أدوات» (publish, index, eval, recut) and «التجهيز»
  (setup) for the owner. A reviewer's rail has «الحالة» only; asking for an admin section gives him the status one.
- The old addresses redirect (302): /system-diagnostics -> /system, /settings?tab=X -> /system?tab=X (no tab: sheet),
  /setup?step=Y -> /system?tab=setup&step=Y; POST /settings stays the save endpoint and lands on /system?tab=…#card.
- The «شو بدو منك» fixes link to the section that holds them (/system?tab=…&goto=…#card); an old #card link on the
  status section moves to the card's section (health.js cardTab), unless the rail does not have it.
- The nav has one «النظام» item for both roles in place of «الصحة والتكلفة» and «الإعدادات».
Kernel tests run the Laravel app against the MariaDB test database with a stub bridge (skipped without php, vendor or
MariaDB); the script parts run under node.
"""

import json
import re
import subprocess

import pytest

from laqta_kernel import DASH, NEEDS_LARAVEL, NEEDS_NODE, NODE, PHP, sql, stub_env
from test_dashboard_login import artisan, run
from test_health_attention import HEALTHY, ONLINE, _facts, _php, _v

VIEWS = DASH / "resources" / "views"
SYSTEM_VIEW = VIEWS / "dashboard" / "system.blade.php"
HEALTH_VIEW = VIEWS / "dashboard" / "diagnostics.blade.php"
LAYOUT = VIEWS / "layouts" / "laqta.blade.php"
CONTROLLERS = DASH / "app" / "Http" / "Controllers"
SYSTEM_PHP = CONTROLLERS / "SystemController.php"
LIMITS = DASH / "app" / "Http" / "Middleware" / "ReviewerLimits.php"
ROUTES = DASH / "routes" / "web.php"
HEALTH_JS = DASH / "public" / "js" / "health.js"

STATUS_TABS = ["status", "search", "logs"]
SETTINGS_TABS = ["sheet", "keys", "models", "auto-publish", "processing", "advanced"]
TOOL_TABS = ["publish", "index", "eval", "recut"]
ALL_TABS = STATUS_TABS + SETTINGS_TABS + TOOL_TABS + ["setup"]
CARD_TABS = {"services": "status", "last-run": "status", "lanes": "status", "search-ops": "search", "logs": "logs",
             "publish-check": "publish", "local-index": "index", "eval-export": "eval", "reprocess": "recut"}
REVIEWER = "pytest-system-reviewer"


def read(path):
    return path.read_text(encoding="utf-8")


def rail_tabs(body):
    return re.findall(r'data-system-tab="([a-z-]+)"', body)


def current_tab(body):
    found = re.findall(r'data-system-tab="([a-z-]+)"\s+aria-current="page"', body)
    assert len(found) == 1, found
    return found[0]


# ---------------------------------------------------------------------------
# Static: routes, the nav, the rail, the scripts
# ---------------------------------------------------------------------------

def test_routes_keep_the_old_names_as_redirects():
    routes = read(ROUTES)
    c = "\\App\\Http\\Controllers\\SystemController"
    for line in (f"Route::get('/system', [{c}::class, 'show'])->name('dashboard.system');",
                 f"Route::get('/system-diagnostics', [{c}::class, 'diagnostics'])->name('dashboard.diagnostics');",
                 f"Route::get('/settings', [{c}::class, 'settings'])->name('dashboard.settings');",
                 f"Route::get('/setup', [{c}::class, 'setup'])->name('dashboard.setup');",
                 "Route::post('/settings', [\\App\\Http\\Controllers\\SettingsController::class, 'save'])"
                 "->name('dashboard.save_settings');"):
        assert line in routes, line


def test_the_nav_has_one_system_item_for_both_roles():
    layout = read(LAYOUT)
    items = layout[layout.index("$lqNavItems = ["):layout.index("];", layout.index("$lqNavItems = ["))]
    assert items.count("'route' => 'dashboard.system'") == 1
    item = re.search(r"\[[^\[\]]*'route' => 'dashboard\.system'[^\[\]]*\]", items).group(0)
    assert "'label' => 'النظام'" in item and "'admin'" not in item and "'icon' => 'settings'" in item
    for gone in ("dashboard.diagnostics", "dashboard.settings", "الصحة والتكلفة", "'label' => 'الإعدادات'"):
        assert gone not in items, gone


def test_the_rail_groups_and_the_page_scripts():
    php = read(SYSTEM_PHP)
    groups = php[php.index("public const GROUPS"):php.index("public const HEALTH_TABS")]
    for key, label, admin in (("state", "الحالة", "false"), ("settings", "الإعدادات", "true"),
                              ("tools", "أدوات", "true"), ("setup", "التجهيز", "true")):
        assert f"'{key}' => ['label' => '{label}', 'admin' => {admin}" in groups, key
    assert "'tabs' => SettingsController::TABS" in groups           # the six settings tabs, unchanged
    for tab in STATUS_TABS + TOOL_TABS + ["setup"]:
        assert f"'{tab}' => ['label' =>" in groups, tab
    view = read(SYSTEM_VIEW)
    assert view.count("<x-lq.page-header") == 1 and 'title="النظام"' in view
    assert "data-system-setup-link" in view and "كمّل التجهيز (" in view
    # one script per kind of section: health.js on the health ones, settings.js on settings (and setup), setup.js
    scripts = view[view.index("@push('scripts')"):]
    assert re.search(r"@if \(\$systemKind === 'health'\)\s*<script src=\"\{\{ asset\('js/health\.js'\)", scripts)
    assert re.search(r"@else\s*<script src=\"\{\{ asset\('js/settings\.js'\)", scripts)
    assert re.search(r"@if \(\$systemKind === 'setup'\)\s*<script src=\"\{\{ asset\('js/setup\.js'\)", scripts)
    # the settings script's guard (and its rail fade) needs the page hook on the settings tabs only
    assert "@if ($systemKind === 'settings') data-settings-page @endif" in view
    # no «تفاصيل متقدمة» and no «روح على» any more
    health = read(HEALTH_VIEW)
    assert "lq-health-advanced" not in health and "x-lq.jump-links" not in health


def test_the_cards_and_fixes_map_the_same_on_the_server_and_in_the_script():
    php, js = read(SYSTEM_PHP), read(HEALTH_JS)
    card_php = php[php.index("public const CARD_TABS"):php.index("public const GOTO")]
    assert dict(re.findall(r"'([a-z-]+)' => '([a-z]+)'", card_php)) == CARD_TABS
    card_js = re.search(r"var CARD_TABS = \{(.*?)\};", js, re.S).group(1)
    assert dict(re.findall(r"'?([a-z-]+)'?: '([a-z]+)'", card_js)) == CARD_TABS
    goto_php = php[php.index("public const GOTO"):php.index("// Tabs and links")]
    goto = {k: (t, c) for k, t, c in re.findall(r"'([a-z-]+)' => \['([a-z]+)', '([a-z-]+)'\]", goto_php)}
    assert goto == {"services": ("status", "services"), "publish-check": ("publish", "publish-check"),
                    "bg-restore": ("publish", "publish-check"), "index-card": ("index", "local-index"),
                    "log-nightly": ("logs", "logs")}
    js_goto = re.search(r"var GOTO = \{(.*?)\};", js, re.S).group(1)
    assert set(re.findall(r"'?([a-z-]+)'?: \[", js_goto)) == set(goto)


def test_reviewer_protection_moved_from_paths_to_tabs():
    limits = read(LIMITS)
    pages = limits[limits.index("public const ADMIN_PAGES"):limits.index("public const DENIED")]
    # /settings and /setup are redirects now; «النظام» guards its admin tabs itself (SystemController::tabFor)
    assert "'settings'," not in pages and "'setup'," not in pages
    # the owner's reads and writes stay his: the setup API, the run page, the cut-out gallery; POST /settings is
    # not one of the reviewer's writes
    for path in ("'api/setup/*'", "'batch-automation'", "'api/cutout/*'", "'active-learning'"):
        assert path in pages, path
    writes = limits[limits.index("public const REVIEWER_WRITES"):limits.index("public const ADMIN_PAGES")]
    assert "'settings'" not in writes and "api/settings" not in writes and "api/setup" not in writes
    php = read(SYSTEM_PHP)
    assert "return $reviewer && self::isAdminTab($value) ? self::DEFAULT_TAB : $value;" in php
    assert "fn ($group) => !$reviewer || !$group['admin']" in php


# ---------------------------------------------------------------------------
# SystemController's helpers and the attention fixes (PHP, Laravel booted, no database)
# ---------------------------------------------------------------------------

@NEEDS_LARAVEL
def test_tab_rules_and_links():
    out = _php("""
use App\\Http\\Controllers\\SystemController as S;
$out['admin'] = array_map(fn ($t) => S::tabFor($t, false), ['keys', 'setup', 'recut', 'bogus', null, ['x'], 'status']);
$out['reviewer'] = array_map(fn ($t) => S::tabFor($t, true), ['keys', 'setup', 'recut', 'publish', 'logs', 'search']);
$out['groups'] = [array_keys(S::groups(false)), array_keys(S::groups(true))];
$out['url'] = [S::url('keys'), S::url('setup', ['step' => 'keys']), S::url('publish', [], 'publish-check')];
$out['goto'] = array_map(fn ($g) => S::gotoHref($g), ['services', 'publish-check', 'bg-restore', 'index-card', 'log-nightly', 'reload']);
$out['adminHref'] = array_map(fn ($h) => S::adminHref($h), ['/system?tab=keys#lq-key-serper', '/system?tab=logs&goto=log-nightly#logs',
    '/system?tab=publish', '/batch-automation#run-new', '/catalog?filter=failed', '/system', '/#run-new', '/#run-brands', '/']);
""")
    assert out["admin"] == ["keys", "setup", "recut", "status", "status", "status", "status"]
    assert out["reviewer"] == ["status", "status", "status", "status", "logs", "search"]
    assert out["groups"] == [["state", "settings", "tools", "setup"], ["state"]]
    assert out["url"] == ["/system?tab=keys", "/system?tab=setup&step=keys", "/system?tab=publish#publish-check"]
    assert out["goto"] == ["/system?tab=status&goto=services#services",
                           "/system?tab=publish&goto=publish-check#publish-check",
                           "/system?tab=publish&goto=bg-restore#publish-check",
                           "/system?tab=index&goto=index-card#local-index",
                           "/system?tab=logs&goto=log-nightly#logs", ""]
    assert out["adminHref"] == [True, False, True, True, False, False, True, True, False]   # «اليوم» run panel and tools: owner only


@NEEDS_LARAVEL
def test_attention_buttons_point_at_the_right_tab():
    down = dict(ONLINE, services=dict(ONLINE["services"], gemini={"status": "offline"}))
    cases = {
        "services": _facts(diagnostics=down),
        "publish": _facts(publish={"overall": "fail", "summary_ar": "x", "steps": []}),
        "bg": _facts(bg={"method": "none", "previous": "photoroom"}),
        "index": _facts(index_rows={"lulu": {"ok_age_s": 30 * 86400}}),
        "nightly": _facts(healthz=dict(HEALTHY, nightly_age_s=40 * 3600)),
        "keys": _facts(ops={"status": "success", "alerts": [{"code": "GEMINI_DOWN"}]}),
    }
    code = "".join(f"$out[{json.dumps(k)}] = A::view({_v(v)}, 1800000000, true)['items'][0]['action'];\n"
                   for k, v in cases.items())
    out = _php(code)
    hrefs = {k: v["href"] for k, v in out.items()}
    assert hrefs == {"services": "/system?tab=status&goto=services#services",
                     "publish": "/system?tab=publish&goto=publish-check#publish-check",
                     "bg": "/system?tab=publish&goto=bg-restore#publish-check",
                     "index": "/system?tab=index&goto=index-card#local-index",
                     "nightly": "/system?tab=logs&goto=log-nightly#logs",
                     "keys": "/system?tab=keys#lq-key-gemini"}
    # a fix on an admin section is the owner's: the reviewer's page hides it (data-lq-admin)
    assert {k: v["admin"] for k, v in out.items()} == {"services": False, "publish": True, "bg": True, "index": True,
                                                       "nightly": False, "keys": True}
    # the card and the button of each fix are on the section its link names
    view = read(HEALTH_VIEW)
    for tab, hooks in (("status", ['id="services"', 'data-health="run-check"']),
                       ("publish", ['id="publish-check"', 'data-health="publish-run"', 'data-health="bg-restore"']),
                       ("index", ['id="local-index"', 'data-health="index-refresh"']),
                       ("logs", ['id="logs"', 'id="tab-nightly"'])):
        section = view[view.index(f"@case('{tab}')"):view.index("@break", view.index(f"@case('{tab}')"))]
        for hook in hooks:
            assert hook in section, (tab, hook)
    # the rendered fix: a link with the goto key (health.js moves the focus when the card is on the page already)
    assert 'data-health-goto="{{ $item[\'action\'][\'goto\'] }}"' in view and "data-lq-admin @endif" in view


# ---------------------------------------------------------------------------
# The Laravel app: redirects, every section, the save, the reviewer
# ---------------------------------------------------------------------------

@pytest.fixture
def system_env(mariadb_or_skip, tmp_path):
    import local_cache_db
    environ, calls = stub_env(tmp_path, extra={
        "review_stats": dict({"status": "success"}, **local_cache_db.review_stats([])),
        "bg_methods": {"status": "success", "local": {"grabcut": True, "rembg": False}},
    })
    db = mariadb_or_skip
    saved = sql(db, "SELECT `value` FROM system_settings WHERE `key` = 'worker_concurrency'")
    yield environ, calls, db
    sql(db, "DELETE FROM system_settings WHERE `key` = 'worker_concurrency'")
    if saved:
        value = saved[0]["value"] if isinstance(saved[0], dict) else saved[0][0]
        sql(db, "INSERT INTO system_settings (`key`, `value`) VALUES ('worker_concurrency', %s)", (value,))


REDIRECTS = [
    ("/system-diagnostics", "/system"),
    ("/settings", "/system?tab=sheet"),
    ("/settings?tab=models", "/system?tab=models"),
    ("/settings?tab=auto-publish", "/system?tab=auto-publish"),
    ("/settings?tab=bogus", "/system?tab=sheet"),
    ("/setup", "/system?tab=setup"),
    ("/setup?step=3", "/system?tab=setup&step=3"),
    ("/setup?step=keys", "/system?tab=setup&step=keys"),
]


@NEEDS_LARAVEL
def test_the_old_addresses_redirect(system_env):
    environ, _calls, _db = system_env
    out = run(environ, [["GET", old] for old, _new in REDIRECTS])
    for (old, new), res in zip(REDIRECTS, out):
        assert res["status"] == 302, (old, res["status"])
        assert res["location"].split("://", 1)[-1].split("/", 1)[1] == new.lstrip("/"), (old, res["location"])


@NEEDS_LARAVEL
def test_every_section_opens_on_one_page_with_its_script(system_env):
    environ, _calls, _db = system_env
    out = run(environ, [["GET", f"/system?tab={tab}"] for tab in ALL_TABS] + [["GET", "/system"],
                                                                              ["GET", "/system?tab=setup&step=keys"]],
              body=600000)
    for tab, res in zip(ALL_TABS + ["status", "setup"], out):
        body = res["body"]
        assert res["status"] == 200, (tab, body[:2000])
        assert "<title>النظام · لقطة</title>" in body and body.count('class="lq-page-title"') == 1
        assert current_tab(body) == tab, tab
        assert rail_tabs(body) == ALL_TABS, tab                    # the owner's rail: every group
        assert re.search(r'<a href="[^"]+/system" class="lq-nav__item is-active"\s+aria-current="page"', body), tab
        scripts = re.findall(r'/js/(health|settings|setup)\.js\?v=', body)
        expected = ["health"] if tab in STATUS_TABS + TOOL_TABS else (["settings", "setup"] if tab == "setup" else ["settings"])
        assert scripts == expected, (tab, scripts)
        assert ("data-settings-page" in body) is (tab in SETTINGS_TABS), tab
        for leftover in ("{{", "{!!", "@props", "<x-lq", "@json", "@include"):
            assert leftover not in body, (tab, leftover)
        assert "تفاصيل متقدمة" not in body and "lq-health-advanced" not in body
    by_tab = dict(zip(ALL_TABS, out))
    # each card on its section, with its id kept for #links
    for card, tab in CARD_TABS.items():
        for other in STATUS_TABS + TOOL_TABS:
            if card == "last-run" and other == tab:
                continue                                  # shown only once a run wrote its report
            assert (f'id="{card}"' in by_tab[other]["body"]) is (other == tab), (card, other)
    assert "/catalog?mode=cutout" in by_tab["recut"]["body"]
    assert 'data-active="keys"' in out[-1]["body"]


@NEEDS_LARAVEL
def test_a_save_lands_on_its_card_in_the_system_page(system_env):
    environ, _calls, _db = system_env
    saved, refused = run(environ, [["POST", "/settings", {"section": "speed", "worker_concurrency": "3"}],
                                   ["POST", "/settings", {"section": "nope"}]])
    assert saved["status"] == 302 and saved["location"].endswith("/system?tab=advanced#lq-settings-speed")
    assert refused["status"] == 302 and refused["location"].endswith("/system?tab=keys")


@pytest.fixture
def reviewer_env(system_env):
    environ, calls, db = system_env
    sql(db, """CREATE TABLE IF NOT EXISTS users (
        id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY, name VARCHAR(255) NOT NULL, email VARCHAR(255) NOT NULL UNIQUE,
        email_verified_at TIMESTAMP NULL, password VARCHAR(255) NOT NULL, remember_token VARCHAR(100) NULL,
        created_at TIMESTAMP NULL, updated_at TIMESTAMP NULL)""")
    sql(db, """CREATE TABLE IF NOT EXISTS sessions (
        id VARCHAR(255) PRIMARY KEY, user_id BIGINT UNSIGNED NULL, ip_address VARCHAR(45) NULL, user_agent TEXT NULL,
        payload LONGTEXT NOT NULL, last_activity INT NOT NULL)""")
    from test_dashboard_roles import migrate
    sql(db, "DELETE FROM users WHERE name = %s", (REVIEWER,))
    environ = dict(environ, LAQTA_TEST_LOGIN="1")
    migrate(environ)
    out = artisan(environ, "laqta:user", REVIEWER, "--role=reviewer")
    password = re.search(r"كلمة السر: (\S+)", out).group(1)
    yield environ, calls, password
    sql(db, "DELETE FROM users WHERE name = %s", (REVIEWER,))


@NEEDS_LARAVEL
def test_a_reviewer_sees_only_the_status_group_and_is_bounced_from_admin_tabs(reviewer_env):
    environ, calls, password = reviewer_env
    requests = [["POST", "/login", {"username": REVIEWER, "password": password}]]
    requests += [["GET", f"/system?tab={tab}"] for tab in ALL_TABS]
    requests += [["GET", "/settings?tab=keys"], ["GET", "/setup?step=keys"], ["GET", "/system-diagnostics"],
                 ["GET", "/api/setup/state"], ["POST", "/api/setup/progress", {"action": "done"}],
                 ["POST", "/settings", {"section": "speed", "worker_concurrency": "9"}],
                 ["POST", "/api/settings/bg-method", {"method": "none"}],
                 ["POST", "/api/system/publish-check"], ["POST", "/api/system/local-index/refresh"],
                 ["POST", "/api/system/eval-export"], ["POST", "/api/system/reprocess/plan"]]
    out = run(environ, requests, body=600000)
    pages = dict(zip(ALL_TABS, out[1:1 + len(ALL_TABS)]))
    for tab, res in pages.items():
        body = res["body"]
        assert res["status"] == 200, (tab, res["status"], body[:500])
        assert 'data-lq-role="reviewer"' in body
        assert rail_tabs(body) == STATUS_TABS, tab                        # «الحالة» only
        assert current_tab(body) == (tab if tab in STATUS_TABS else "status"), tab
        assert "data-system-setup-link" not in body and "data-settings-page" not in body
        for admin_hook in ("data-key-form", "data-setup-page", 'id="publish-check"', 'id="local-index"',
                           'id="eval-export"', 'id="reprocess"', 'data-system-group="settings"'):
            assert admin_hook not in body, (tab, admin_hook)
    assert '/system" class="lq-nav__item' in pages["status"]["body"]          # «النظام» is in his nav too
    old_settings, old_setup, old_health = out[1 + len(ALL_TABS):4 + len(ALL_TABS)]
    assert old_settings["status"] == 302 and old_settings["location"].endswith("/system?tab=keys")
    assert old_setup["status"] == 302 and old_setup["location"].endswith("/system?tab=setup&step=keys")
    assert old_health["status"] == 302 and old_health["location"].endswith("/system")
    # every write (and the setup API, a read too) stays the owner's, exactly as before: 403, and no bridge call
    for res in out[4 + len(ALL_TABS):]:
        assert res["status"] == 403, res
    actions = {json.loads(line)[0] for line in calls.read_text(encoding="utf-8").splitlines()} if calls.exists() else set()
    assert not actions & {"publish_check", "local_index_refresh", "eval_export", "reprocess_plan", "save_settings"}


# ---------------------------------------------------------------------------
# health.js: old #card links move to the card's section
# ---------------------------------------------------------------------------

def _node(script):
    result = subprocess.run([NODE, "-"], input=script, capture_output=True, text=True, timeout=60, encoding="utf-8")
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


@NEEDS_NODE
def test_the_legacy_hash_mapping():
    out = _node("globalThis.window = globalThis;\n" + read(HEALTH_JS) + """
const H = window.LaqtaHealth;
console.log(JSON.stringify({
  status: ['#publish-check', '#local-index', '#eval-export', '#reprocess', '#search-ops', '#logs', '#services',
           '#lanes', '#last-run', '#card-serper', '', '#bogus'].map(h => H.cardTab(h, 'status')),
  onItsOwn: [H.cardTab('#publish-check', 'publish'), H.cardTab('#logs', 'logs')],
  fromSearch: H.cardTab('#services', 'search')
}));
""")
    assert out["status"] == ["publish", "index", "eval", "recut", "search", "logs", "", "", "", "", "", ""]
    assert out["onItsOwn"] == ["", ""] and out["fromSearch"] == "status"


LEGACY_DOM = r"""
const replaced = [];
function el(attrs) {
  return { attrs: attrs || {}, getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; },
           querySelector() { return null; }, querySelectorAll() { return []; }, addEventListener() {} };
}
const page = el({ 'data-health-tab': 'status' });
const rail = __RAIL__;
globalThis.window = globalThis;
globalThis.location = { hash: __HASH__, search: '', replace(url) { replaced.push(url); } };
globalThis.document = {
  querySelector(sel) {
    if (sel === '[data-health-page]') return page;
    const m = /^\[data-system-tab="([a-z-]+)"\]$/.exec(sel);
    if (m) return rail.indexOf(m[1]) !== -1 ? el() : null;
    return null;
  },
  querySelectorAll() { return []; }, getElementById() { return null; }, addEventListener() {},
  createElement() { return el(); }
};
"""


@NEEDS_NODE
@pytest.mark.parametrize("hash_, rail, expected", [
    ("#publish-check", ["status", "search", "logs", "publish"], ["/system?tab=publish#publish-check"]),
    ("#local-index", ["status", "index"], ["/system?tab=index#local-index"]),
    ("#eval-export", ["status", "eval"], ["/system?tab=eval#eval-export"]),
    ("#reprocess", ["status", "recut"], ["/system?tab=recut#reprocess"]),
    ("#search-ops", ["status", "search"], ["/system?tab=search#search-ops"]),
    ("#logs", ["status", "logs"], ["/system?tab=logs#logs"]),
    # a reviewer's rail has no «أدوات»: he stays on the status section (no loop through the server's bounce)
    ("#publish-check", ["status", "search", "logs"], []),
])
def test_an_old_card_link_moves_to_its_section(hash_, rail, expected):
    script = LEGACY_DOM.replace("__RAIL__", json.dumps(rail)).replace("__HASH__", json.dumps(hash_))
    script += read(HEALTH_JS)
    # the redirect happens before anything else on the page; a page that stays goes on and needs more DOM than this
    script = script.replace("    var DOTS = {", "    if (globalThis.__stop) return;\n    var DOTS = {", 1)
    out = _node("globalThis.__stop = true;\n" + script + "\nconsole.log(JSON.stringify(replaced));\n")
    assert out == expected
