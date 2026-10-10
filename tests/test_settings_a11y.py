"""Settings and login accessibility: a refused value is said next to its field, not only at the top of the page.

- Laravel (HTTP kernel, a cookie jar between requests so the flash reaches the next page): a settings save with a bad
  number marks that control aria-invalid="true" with aria-describedby on a message right under it, and the top flash
  stays; a failed login points both inputs at the role=alert box and keeps the focus on the username.
- settings.js under node with a few fake elements: the first marked field gets the focus (no smooth scroll with
  prefers-reduced-motion), and the phone tab row says which edge hides more tabs (data-more).
- The views: the model selects and price inputs point at their notes, and «متقدم» sends the owner to Health's
  «حدّث الفهرس هلق» instead of the command line.
"""

import json
import os
import random
import re
import subprocess
import tempfile
from pathlib import Path

import pytest

from laqta_kernel import DASH, NEEDS_LARAVEL, NEEDS_NODE, NODE, PHP, sql, stub_env

ROOT = Path(__file__).resolve().parents[1]
VIEWS = DASH / "resources" / "views"
SETTINGS_JS = DASH / "public" / "js" / "settings.js"
SETTINGS_CSS = DASH / "public" / "css" / "pages" / "settings.css"
SLUGS = ["gemini__gemini-3_1-flash-lite", "gemini__gemini-3_5-flash", "claude__claude-haiku-4-5",
         "claude__claude-sonnet-5-5", "claude__claude-opus-5-5"]
TOUCHED = ["worker_concurrency", "verifier_primary", "verifier_strong", "gemini_model", "query_normalizer",
           "verifier_monthly_budget_usd", "model_prices", "expansion_max_calls", "visual_search",
           "serpapi_lens_price_usd", "gtin_policy", "local_index_max_pages", "expansion_enabled", "local_index_enabled"]


def read(path):
    return Path(path).read_text(encoding="utf-8")


def run(env, requests_list):
    """[{status, location, body}] for [[method, uri, params]], cookies (the session) kept between the requests."""
    client = f"203.0.113.{random.randint(1, 254)}"
    dash = str(DASH).replace("\\", "/")
    script = f"""<?php
require '{dash}/vendor/autoload.php';
$app = require '{dash}/bootstrap/app.php';
$kernel = $app->make(Illuminate\\Contracts\\Http\\Kernel::class);
$jar = [];
$out = [];
foreach (json_decode(file_get_contents($argv[1]), true) as $r) {{
    $request = Illuminate\\Http\\Request::create($r[1], $r[0], $r[2] ?? [], $jar, [], ['REMOTE_ADDR' => '{client}']);
    $response = $kernel->handle($request);
    foreach ($response->headers->getCookies() as $c) {{ $jar[$c->getName()] = $c->getValue(); }}
    $out[] = ['status' => $response->getStatusCode(), 'location' => $response->headers->get('Location'),
              'body' => (string) $response->getContent()];
    $kernel->terminate($request, $response);
}}
echo json_encode($out, JSON_UNESCAPED_UNICODE);
"""
    with tempfile.TemporaryDirectory() as folder:
        path, data = os.path.join(folder, "run.php"), os.path.join(folder, "spec.json")
        Path(path).write_text(script, encoding="utf-8")
        Path(data).write_text(json.dumps(requests_list), encoding="utf-8")
        result = subprocess.run([PHP, path, data], cwd=DASH, env=env, capture_output=True, text=True, timeout=240,
                                encoding="utf-8")
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-3000:]
    return json.loads(result.stdout)


def control(body, name):
    """The opening tag of the control called name (the first one)."""
    m = re.search(r'<(?:input|select)\b[^>]*\bname="' + re.escape(name) + r'"[^>]*>', body)
    assert m, name
    return m.group(0)


@pytest.fixture
def settings_env(mariadb_or_skip, tmp_path):
    db = mariadb_or_skip
    rows = sql(db, "SELECT `key`, `value` FROM system_settings")
    saved = {r["key"]: r["value"] for r in rows if r["key"] in TOUCHED}
    env, _ = stub_env(tmp_path)
    yield env
    sql(db, "DELETE FROM system_settings WHERE `key` IN (" + ", ".join(["%s"] * len(TOUCHED)) + ")", tuple(TOUCHED))
    for key, value in saved.items():
        sql(db, "INSERT INTO system_settings (`key`, `value`, updated_at) VALUES (%s, %s, NOW())", (key, value))


@NEEDS_LARAVEL
def test_a_refused_value_is_marked_on_its_field_and_still_flashed(settings_env):
    prices_in = {s: "1" for s in SLUGS}
    prices_out = {s: "2" for s in SLUGS}
    prices_out[SLUGS[2]] = "abc"
    speed, advanced, models_save, models = run(settings_env, [
        ["POST", "/settings", {"section": "speed", "worker_concurrency": "99"}],
        ["GET", "/settings?tab=advanced"],
        ["POST", "/settings", {"section": "models", "verifier_primary": "gemini:gemini-3.1-flash-lite",
                               "verifier_strong": "off", "query_normalizer": "off",
                               "verifier_monthly_budget_usd": "lots", "price_input": prices_in,
                               "price_output": prices_out}],
        ["GET", "/settings?tab=models"],
    ])
    assert speed["status"] == 302 and speed["location"].endswith("#lq-settings-speed")
    body = advanced["body"]
    assert advanced["status"] == 200, body[:2000]
    tag = control(body, "worker_concurrency")
    assert 'aria-invalid="true"' in tag and 'aria-describedby="lq-settings-error-worker_concurrency"' in tag
    error = re.search(r'<span class="lq-field__error lq-settings-error" id="lq-settings-error-worker_concurrency"'
                      r' data-settings-field-error>([^<]+)</span>', body)
    assert error and "عدد المنتجات بنفس الوقت" in error.group(1)
    # right under the field (before its hint), and the top flash is still there
    assert body.index(tag) < error.start() < body.index("والمعتاد 5")
    assert 'data-settings-flash="warning"' in body and body.count("data-settings-field-error") == 1
    # untouched fields are not marked
    assert "aria-invalid" not in control(body, "expansion_max_calls")

    body = models["body"]
    assert models_save["status"] == 302 and models["status"] == 200, body[:2000]
    tag = control(body, "verifier_monthly_budget_usd")
    assert 'aria-invalid="true"' in tag
    assert 'aria-describedby="lq-settings-error-verifier_monthly_budget_usd lq-models-budget-help"' in tag
    assert 'id="lq-settings-error-verifier_monthly_budget_usd"' in body and 'id="lq-models-budget-help"' in body
    # the one bad price is marked and points at the one message under the table; the others only at the hint
    bad = control(body, f"price_output[{SLUGS[2]}]")
    assert 'aria-invalid="true"' in bad and 'aria-describedby="lq-settings-error-model_prices lq-models-prices-help"' in bad
    good = control(body, f"price_output[{SLUGS[0]}]")
    assert "aria-invalid" not in good and 'aria-describedby="lq-models-prices-help"' in good
    assert body.count('id="lq-settings-error-model_prices"') == 1
    # a valid choice is not marked; its select still points at its note
    primary = control(body, "verifier_primary")
    assert "aria-invalid" not in primary and 'aria-describedby="lq-models-primary-help"' in primary
    assert 'id="lq-models-primary-help"' in body
    # a page with nothing refused has no message and no mark
    clean = run(settings_env, [["GET", "/settings?tab=models"]])[0]["body"]
    assert "data-settings-field-error" not in clean and 'aria-invalid="true"' not in control(clean, "verifier_monthly_budget_usd")


@NEEDS_LARAVEL
def test_a_failed_login_points_the_fields_at_the_alert(mariadb_or_skip, tmp_path):
    db = mariadb_or_skip
    sql(db, """CREATE TABLE IF NOT EXISTS users (
        id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY, name VARCHAR(255) NOT NULL, email VARCHAR(255) NOT NULL UNIQUE,
        email_verified_at TIMESTAMP NULL, password VARCHAR(255) NOT NULL, remember_token VARCHAR(100) NULL,
        created_at TIMESTAMP NULL, updated_at TIMESTAMP NULL)""")
    env, _ = stub_env(tmp_path)
    env["LAQTA_TEST_LOGIN"] = "1"
    first, refused, page = run(env, [["GET", "/login"],
                                     ["POST", "/login", {"username": "pytest-a11y-nobody", "password": "nope"}],
                                     ["GET", "/login"]])
    assert "aria-invalid" not in first["body"] and "lq-login-error" not in first["body"]
    assert refused["status"] == 302 and refused["location"].endswith("/login")
    body = page["body"]
    assert re.search(r'role="alert" id="lq-login-error"', body) and "غلط" in body
    for name in ("username", "password"):
        tag = control(body, name)
        assert 'aria-invalid="true"' in tag and 'aria-describedby="lq-login-error"' in tag, tag
    assert "autofocus" in control(body, "username") and 'value="pytest-a11y-nobody"' in control(body, "username")


# A page with one refused field and the phone tab row, enough for the load-time code of settings.js.
FAKE_DOM = r"""
function makeEl(attrs) {
    const el = { attrs: Object.assign({}, attrs || {}), listeners: {}, disabled: false, type: 'number', calls: [] };
    el.getAttribute = n => (n in el.attrs ? el.attrs[n] : null);
    el.hasAttribute = n => n in el.attrs;
    el.setAttribute = (n, v) => { el.attrs[n] = String(v); };
    el.removeAttribute = n => { delete el.attrs[n]; };
    el.addEventListener = (type, fn) => { (el.listeners[type] = el.listeners[type] || []).push(fn); };
    el.focus = opts => { el.calls.push(['focus', opts || null]); };
    el.scrollIntoView = opts => { el.calls.push(['scroll', opts || null]); };
    return el;
}
const error = makeEl({ 'data-settings-field-error': '' });
error.id = 'lq-settings-error-worker_concurrency';
const field = makeEl({ 'aria-invalid': 'true', 'aria-describedby': error.id });
const tabs = makeEl({});
Object.assign(tabs, { scrollWidth: 900, clientWidth: 360, scrollLeft: 0 });
tabs.getBoundingClientRect = () => ({ left: 0, width: 360 });
const active = makeEl({});
active.getBoundingClientRect = () => ({ left: -300, width: 100 });     // RTL: an open tab off to the left
const map = { '[data-settings-field-error]': error, '.lq-settings__tabs': tabs, '.lq-settings__tab.is-active': active };
const page = makeEl({ 'data-settings-page': '' });
page.querySelector = sel => map[sel] || null;
page.querySelectorAll = sel => (sel === '[aria-describedby~="' + error.id + '"]' ? [field] : []);
globalThis.window = globalThis;
globalThis.document = { querySelector: sel => (sel === '[data-settings-page]' ? page : null) };
globalThis.matchMedia = q => ({ matches: q.indexOf('max-width') >= 0 || (q.indexOf('reduce') >= 0 && REDUCE) });
"""


def _node(reduce):
    script = f"const REDUCE = {'true' if reduce else 'false'};\n" + FAKE_DOM + read(SETTINGS_JS) + r"""
const before = tabs.attrs['data-more'] || '';
tabs.scrollLeft = -540; tabs.listeners.scroll.forEach(fn => fn());
const atEnd = tabs.attrs['data-more'] || '';
tabs.scrollLeft = -200; tabs.listeners.scroll.forEach(fn => fn());
console.log(JSON.stringify({ field: field.calls, before: before, atEnd: atEnd, middle: tabs.attrs['data-more'] || '',
                             centred: tabs.scrollLeft }));
"""
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as fh:
        fh.write(script)
        path = fh.name
    try:
        result = subprocess.run([NODE, path], capture_output=True, text=True, timeout=60, encoding="utf-8")
    finally:
        os.unlink(path)
    assert result.returncode == 0, result.stderr[-3000:]
    return json.loads(result.stdout.strip().splitlines()[-1])


@NEEDS_NODE
def test_the_first_refused_field_gets_the_focus_and_the_tab_row_shows_more():
    out = _node(reduce=False)
    assert out["field"][0] == ["focus", {"preventScroll": True}]
    assert out["field"][1] == ["scroll", {"block": "center", "behavior": "smooth"}]
    # the open tab was scrolled to the middle of the row (-300 + 50 - 180), then the row says which edge hides tabs
    assert out["before"] == "both"
    assert out["atEnd"] == "start" and out["middle"] == "both"
    assert _node(reduce=True)["field"][1] == ["scroll", {"block": "center", "behavior": "auto"}]


def test_views_point_controls_at_their_notes_and_send_the_index_to_health():
    models = read(VIEWS / "settings" / "models.blade.php")
    for note in ("primary", "strong", "normalizer", "budget", "prices"):
        ident = f"lq-models-{note}-help"
        assert f"'{ident}'" in models, ident                               # a control points at it
        assert models.count(f'id="{ident}"') >= 1, ident                   # and the note carries it
    assert models.count("'lq-models-prices-help', 'model_prices'") == 2     # both price inputs
    advanced = read(VIEWS / "settings" / "advanced.blade.php")
    assert advanced.count("route('dashboard.diagnostics') }}#local-index") == 2
    assert "حدّث الفهرس هلق" in advanced
    assert "للتحديث: <code" not in advanced                                  # no command line as the owner's way
    css = read(SETTINGS_CSS)
    assert '[data-more="end"]' in css and '[dir="rtl"] .lq-settings__tabs[data-more="end"]' in css
    login = read(VIEWS / "auth" / "login.blade.php")
    assert 'id="lq-login-error"' in login and login.count('aria-describedby="lq-login-error"') == 2
