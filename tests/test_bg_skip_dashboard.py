"""«تجاوز عزل الخلفية» and the active tab on the dashboard (WP-NOBG, Fixes 1 and 2).

The Health page's «فحص النشر» showed PhotoRoom's credit had run out: every approval failed. Now:
- POST /api/settings/bg-method {method} (SettingsController::setBgMethod): method must be one of BG_METHODS; stopping
  background removal stores the previous method in system_settings.bg_removal_method_previous; CSRF like every POST.
- The «فحص النشر» card offers «تجاوز عزل الخلفية» (after a confirm saying what it does) when the processing step failed
  on PhotoRoom / remove.bg credit, key or quota, and «رجّع عزل الخلفية (<previous>)» while the method is none. The same
  view on the server (HealthController::bgSkipView) and in the page script (health.js bgView); nothing re-runs.
- Settings → «معالجة الصور» says what «بدون عزل الخلفية» does, names GrabCut / rembg only when installed, and offers the
  restore button while removal is off.
- The review screen's failed-approvals panel says what happened for the provider codes and offers the same skip.
- The Run page says which tab the run reads, without asking Google.
"""

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
# tests/catalog_match/test_cm_sitemaps.py puts scripts/ first on sys.path, and scripts/publish_check.py is the terminal
# script of the same name: the repository's own publish_check comes first (as tests/test_phash_dedup.py does)
if sys.path[0] != str(ROOT):
    sys.path.insert(0, str(ROOT))

import publish_check as pc  # noqa: E402
from laqta_review_harness import NODE
from test_laqta_health import (DASH, PHP, SECRETS, _js, _kernel, _node, _php, _put, _settings, _sql,  # noqa: F401
                               app_env, php_value)
from test_laqta_review_guards import fixture, page, picked
from test_publish_check import TAB_TITLES

CONTROLLERS = DASH / "app" / "Http" / "Controllers"
SETTINGS_PHP = CONTROLLERS / "SettingsController.php"
HEALTH_PHP = CONTROLLERS / "HealthController.php"
RUN_PHP = CONTROLLERS / "RunController.php"
REVIEW_PHP = CONTROLLERS / "ReviewController.php"
ROUTES = DASH / "routes" / "web.php"
VIEWS = DASH / "resources" / "views"
HEALTH_VIEW = VIEWS / "dashboard" / "diagnostics.blade.php"
RUN_VIEW = VIEWS / "dashboard" / "batch_automation.blade.php"
PROCESSING_VIEW = VIEWS / "settings" / "processing.blade.php"
HEALTH_JS = DASH / "public" / "js" / "health.js"
SETTINGS_JS = DASH / "public" / "js" / "settings.js"
CORE_JS = DASH / "public" / "js" / "review" / "core.js"
APP_JS = DASH / "public" / "js" / "review" / "app.js"

NEEDS_PHP = pytest.mark.skipif(PHP is None, reason="php is not installed")
NEEDS_NODE = pytest.mark.skipif(NODE is None, reason="node is not installed")


def read(path):
    return path.read_text(encoding="utf-8")


def _result(code="photoroom_402", status="fail"):
    """A saved «فحص النشر» result whose processing step ended with code."""
    return {"ok": False, "overall": "fail" if status == "fail" else "ok", "steps": [
        {"key": "download", "status": "ok", "ms": 10, "title_ar": "تنزيل الصورة", "detail_ar": "", "action_ar": "", "code": ""},
        {"key": "process", "status": status, "ms": 20, "title_ar": "عزل الخلفية والمعالجة", "detail_ar": "x",
         "action_ar": "y", "code": code},
        {"key": "upload", "status": "skipped", "ms": 0, "title_ar": "", "detail_ar": "", "action_ar": "", "code": ""},
        {"key": "sheet", "status": "ok", "ms": 5, "title_ar": "", "detail_ar": "", "action_ar": "", "code": ""}],
        "summary_ar": "s", "finished_at": "2026-10-05T19:51:00+00:00"}


# ---------------------------------------------------------------------------
# Static: one rule, one endpoint
# ---------------------------------------------------------------------------

def test_the_endpoint_is_a_post_route_of_the_web_group():
    routes = read(ROUTES)
    assert ("Route::post('/api/settings/bg-method', [\\App\\Http\\Controllers\\SettingsController::class, "
            "'setBgMethod']);") in routes
    # CSRF like the other POST routes: nothing is excluded from token verification
    assert "validateCsrfTokens" not in read(DASH / "bootstrap" / "app.php")
    method = read(SETTINGS_PHP)
    body = method[method.index("public function setBgMethod("):method.index("private static function jsonResponse(")]
    assert "in_array($method, self::BG_METHODS, true)" in body and "422" in body and "503" in body
    assert "self::BG_PREVIOUS_KEY" in body and "PythonBridge" not in body


def test_the_skip_codes_are_one_rule_in_python_php_and_both_scripts():
    python = pc.BG_SKIP_CODE_RE
    php = re.search(r"BG_SKIP_PATTERN = '/(.*?)/';", read(HEALTH_PHP)).group(1)
    health = re.search(r"var BG_SKIP_RE = /(.*?)/;", read(HEALTH_JS)).group(1)
    review = re.search(r"const BG_SKIP_RE = /(.*?)/;", read(CORE_JS)).group(1)
    assert python == php == health == review


@NEEDS_PHP
@NEEDS_NODE
def test_method_labels_are_the_same_on_the_server_and_in_the_page():
    php = _php("$out = SettingsController::BG_METHOD_LABELS;")
    js = _node(_js(HEALTH_JS) + "console.log(JSON.stringify(window.LaqtaHealth.BG_METHOD_LABELS));")
    assert php == js and list(php) == ["photoroom", "remove_bg_api", "grabcut", "rembg", "none"]


def test_the_card_carries_the_box_its_hooks_and_the_confirm_text():
    view = read(HEALTH_VIEW)
    card = view[view.index('id="publish-check"'):view.index('id="publish-check-initial"')]
    for hook in ("bg-box", "bg-text", "bg-skip", "bg-restore", "bg-restore-label"):
        assert f'data-health="{hook}"' in card, hook
    assert 'data-confirm="{{ $bgConfirm }}"' in card and ">تجاوز عزل الخلفية</button>" in card
    assert "'bgConfirm' => SettingsController::BG_SKIP_CONFIRM" in read(HEALTH_PHP)
    confirm = re.search(r"BG_SKIP_CONFIRM = (.*?);\n", read(SETTINGS_PHP), re.S).group(1)
    for words in ("بتنتشر متل ما هي على لوحة بيضا", "صورة خلفيتها مش بيضا بتبين خلفيتها", "ما بنعيد أي فحص",
                  "فيك ترجّع عزل الخلفية"):
        assert words in confirm, words
    # the page and the bridge: opening the page reads the method from the database, it never runs anything
    page_body = read(HEALTH_PHP)
    page_fn = page_body[page_body.index("public function page()"):page_body.index("public static function bgProblem(")]
    assert "currentBgState()" in page_fn and "PythonBridge::run" not in page_fn


def test_the_review_screen_gets_the_method_the_confirm_and_the_endpoint():
    review = read(REVIEW_PHP)
    assert "'bgMethod' => url('/api/settings/bg-method')," in review
    assert "'bg' => $dbOnline ? self::bgConfig() : null," in review
    assert "bgMethod: '/api/settings/bg-method'" in read(APP_JS)


# ---------------------------------------------------------------------------
# The «فحص النشر» box on the server and in the page script
# ---------------------------------------------------------------------------

BG_CASES = [
    (None, None),                                                       # no database: no button
    (_result("photoroom_402"), None),
    (_result("photoroom_402"), {"method": "photoroom", "previous": ""}),
    (_result("photoroom_401"), {"method": "photoroom", "previous": ""}),
    (_result("photoroom_403"), {"method": "photoroom", "previous": ""}),
    (_result("photoroom_429"), {"method": "photoroom", "previous": ""}),
    (_result("photoroom_no_key"), {"method": "photoroom", "previous": ""}),
    (_result("removebg_402"), {"method": "remove_bg_api", "previous": ""}),
    (_result("photoroom_timeout"), {"method": "photoroom", "previous": ""}),      # down, not credit: no skip
    (_result("processing_failed"), {"method": "photoroom", "previous": ""}),
    (_result("", "ok"), {"method": "photoroom", "previous": ""}),
    (None, {"method": "photoroom", "previous": ""}),
    (_result("photoroom_402"), {"method": "none", "previous": "photoroom"}),       # off: restore wins
    (None, {"method": "none", "previous": "remove_bg_api"}),
    (None, {"method": "none", "previous": ""}),
    (None, {"method": "none", "previous": "none"}),
    (_result("photoroom_402"), {"method": "", "previous": ""}),
]


@NEEDS_PHP
@NEEDS_NODE
def test_the_skip_box_says_the_same_on_the_server_and_in_the_script():
    php = _php("foreach (" + php_value([[r, b] for r, b in BG_CASES]) + " as [$r, $b]) {"
               " $out[] = HealthController::bgSkipView($r, $b); }")
    js = _node(_js(HEALTH_JS) + "const H = window.LaqtaHealth; console.log(JSON.stringify("
               + json.dumps([[r, b] for r, b in BG_CASES], ensure_ascii=False) + ".map(([r, b]) => H.bgView(r, b))));")
    assert php == js
    states = [v["state"] for v in php]
    assert states == ["hidden", "hidden", "offer", "offer", "offer", "offer", "offer", "offer", "hidden", "hidden",
                      "hidden", "hidden", "off", "off", "off", "off", "hidden"]
    assert php[2]["text"].startswith("رصيد PhotoRoom خلص أو الاشتراك موقوف، فكل اعتماد رح يفشل بنفس الشكل")
    assert php[3]["text"].startswith("PhotoRoom رفض المفتاح") and php[5]["text"].startswith("PhotoRoom رافض طلبات كتير")
    assert php[6]["text"].startswith("مفتاح PhotoRoom مش محفوظ") and php[7]["text"].startswith("رصيد remove.bg خلص")
    assert php[12]["restore_label"] == "رجّع عزل الخلفية (PhotoRoom)" and php[12]["restore"] == "photoroom"
    assert php[13]["restore_label"] == "رجّع عزل الخلفية (remove.bg)" and php[13]["restore"] == "remove_bg_api"
    assert php[14]["restore"] == php[15]["restore"] == "photoroom"
    assert php[12]["text"].startswith("عزل الخلفية متوقف: الصور اللي بتعتمدها بتنتشر متل ما هي على لوحة بيضا")


def _check(deps_extra, scenario):
    """createPublishCheck with recorded fetches answered by `answers` (path -> list of responses, in order)."""
    return _node(_js(HEALTH_JS) + "const H = window.LaqtaHealth; const fetched = [], bgViews = [], toasts = [], confirms = [];\n"
                 "let confirmAnswer = true;\n"
                 + deps_extra + "\n"
                 "const c = H.createPublishCheck(Object.assign({ fetchJson: (url, opts) => { fetched.push([url, opts.method, opts.body]);"
                 " return Promise.resolve((answers[url] || []).shift()); },\n"
                 "  renderPublish: () => {}, renderBg: v => bgViews.push(v), setPublishBusy: () => {}, setBgBusy: () => {},\n"
                 "  confirm: t => { confirms.push(t); return confirmAnswer; }, confirmText: 'CONFIRM-TEXT',\n"
                 "  toast: (t, v) => toasts.push([t, v]), now: () => 0 }, deps));\n"
                 "c.start();\n(async () => {\n" + scenario
                 + "\nconsole.log(JSON.stringify({ fetched, toasts, confirms, states: bgViews.map(v => v.state),"
                   " last: bgViews[bgViews.length - 1], bg: c.state.bg }));\n})();")


@NEEDS_NODE
def test_the_skip_button_asks_first_then_saves_none_and_never_reruns_the_check():
    out = _check(
        "const deps = { initial: " + json.dumps(_result("photoroom_402")) + ", bg: { method: 'photoroom', previous: '' } };\n"
        "const answers = { '/api/settings/bg-method': [\n"
        "  { ok: true, status: 200, data: { status: 'success', method: 'none', previous: 'photoroom', message: 'SAVED-NONE' } },\n"
        "  { ok: true, status: 200, data: { status: 'success', method: 'photoroom', previous: 'photoroom', message: 'SAVED-BACK' } }] };",
        "confirmAnswer = false; await c.skipBg();\n"
        "confirmAnswer = true; await c.skipBg();\n"
        "await c.skipBg();\n"                                 # off now: nothing to skip
        "await c.restoreBg();")
    assert out["confirms"] == ["CONFIRM-TEXT", "CONFIRM-TEXT"]     # declined once, accepted once, never asked when off
    assert out["fetched"] == [["/api/settings/bg-method", "POST", {"method": "none"}],
                              ["/api/settings/bg-method", "POST", {"method": "photoroom"}]]
    assert out["states"] == ["offer", "off", "offer"]              # the last check still says the credit is out
    assert out["toasts"] == [["SAVED-NONE", "success"], ["SAVED-BACK", "success"]]
    assert out["bg"] == {"method": "photoroom", "previous": "photoroom"}
    assert not [f for f in out["fetched"] if f[0] == "/api/system/publish-check"]


@NEEDS_NODE
def test_a_refused_save_keeps_the_state_and_says_why():
    out = _check(
        "const deps = { initial: " + json.dumps(_result("photoroom_402")) + ", bg: { method: 'photoroom', previous: '' } };\n"
        "const answers = { '/api/settings/bg-method': [\n"
        "  { ok: false, status: 503, data: { status: 'failed', error: 'ما انحفظ: قاعدة البيانات ما ردّت. جرّب كمان شوي.' } },\n"
        "  { ok: false, status: 419, data: null }] };",
        "await c.skipBg(); await c.skipBg();")
    assert out["states"] == ["offer"] and out["bg"] == {"method": "photoroom", "previous": ""}
    assert out["toasts"] == [["ما انحفظ: ما انحفظ: قاعدة البيانات ما ردّت. جرّب كمان شوي.", "danger"],
                             ["ما انحفظ: انتهت صلاحية الصفحة. حدّثها وجرّب مرة تانية.", "danger"]]


@NEEDS_NODE
def test_without_a_known_method_there_is_no_button_to_press():
    out = _check(
        "const deps = { initial: " + json.dumps(_result("photoroom_402")) + ", bg: null };\nconst answers = {};",
        "await c.skipBg(); await c.restoreBg();")
    assert out["states"] == ["hidden"] and out["fetched"] == [] and out["confirms"] == []


# ---------------------------------------------------------------------------
# Settings → «معالجة الصور», the last-run card and the Run page line (PHP helpers)
# ---------------------------------------------------------------------------

@NEEDS_PHP
def test_the_processing_tab_explains_no_removal_and_names_only_installed_local_methods():
    stored = {"bg_removal_method": {"value": "photoroom"}}
    out = _php(
        f"$s = {php_value(stored)};"
        "$out['unknown'] = SettingsController::processingData($s, null);"
        "$out['grabcut'] = SettingsController::processingData($s, ['grabcut' => true, 'rembg' => false]);"
        "$out['both'] = SettingsController::processingData($s, ['grabcut' => true, 'rembg' => true]);"
        "$out['rembg_current'] = SettingsController::processingData(['bg_removal_method' => ['value' => 'rembg']], ['grabcut' => false, 'rembg' => false]);"
        "$out['off'] = SettingsController::processingData(['bg_removal_method' => ['value' => 'none'],"
        " 'bg_removal_method_previous' => ['value' => 'remove_bg_api']], null);"
        "$out['removebg'] = SettingsController::processingData(['bg_removal_method' => ['value' => 'remove_bg_api']], null);")
    none = "بدون عزل الخلفية: الصورة متل ما هي على لوحة بيضا وبتنتشر مباشرة"
    assert list(out["unknown"]["methods"]) == ["photoroom", "none"] and out["unknown"]["methods"]["none"] == none
    assert "صورة خلفيتها مش بيضا بتبين خلفيتها" in out["unknown"]["hint"]
    assert "GrabCut" not in out["unknown"]["hint"] and "rembg" not in out["unknown"]["hint"]     # nothing promised
    assert list(out["grabcut"]["methods"]) == ["photoroom", "grabcut", "none"]
    assert "GrabCut طريقة محلية مجانية منزّلة" in out["grabcut"]["hint"] and "rembg" not in out["grabcut"]["hint"]
    assert list(out["both"]["methods"]) == ["photoroom", "grabcut", "rembg", "none"]
    assert "GrabCut وrembg طرق محلية مجانية منزّلة" in out["both"]["hint"]
    assert "rembg" in out["rembg_current"]["methods"]                      # the saved method is always shown
    assert out["off"]["bg"] == {"method": "none", "off": True, "previous": "remove_bg_api", "previous_label": "remove.bg"}
    assert out["off"]["method"] == "none" and out["unknown"]["bg"]["off"] is False
    assert "remove_bg_api" in out["removebg"]["methods"] and "remove_bg_api" not in out["unknown"]["methods"]


@NEEDS_PHP
def test_the_previous_method_defaults_to_photoroom():
    out = _php("$out[] = SettingsController::bgState([]);"
               "$out[] = SettingsController::bgState(['bg_removal_method' => ['value' => 'none']]);"
               "$out[] = SettingsController::bgState(['bg_removal_method' => ['value' => 'none'], 'bg_removal_method_previous' => ['value' => 'none']]);"
               "$out[] = SettingsController::bgState(['bg_removal_method' => ['value' => 'magic'], 'bg_removal_method_previous' => ['value' => 'grabcut']]);")
    assert [(s["method"], s["previous"], s["previous_label"]) for s in out] == [
        ("photoroom", "photoroom", "PhotoRoom"), ("none", "photoroom", "PhotoRoom"), ("none", "photoroom", "PhotoRoom"),
        ("photoroom", "grabcut", "GrabCut")]


@NEEDS_PHP
def test_the_last_run_card_says_how_many_went_out_without_removal():
    row = {"outcome": "done", "run_trigger": "nightly", "started_at": "2026-10-05 02:00:00", "auto_published": 5,
           "ready_for_review": 2, "report_json": {"reason_text": "", "bg_skipped": 3}}
    out = _php(f"$out[] = HealthController::lastRunCard({php_value(row)});"
               f"$out[] = HealthController::lastRunCard({php_value(dict(row, report_json={'bg_skipped': 0}))});")
    assert "انتشر بدون عزل الخلفية 3" in out[0]["summary"] and "عزل الخلفية" not in out[1]["summary"]


def _php_run(code):
    """Like _php, with RunController loaded too (its helpers called with explicit values: no Laravel)."""
    settings, health, run = (str(p).replace("\\", "/") for p in (SETTINGS_PHP, HEALTH_PHP, RUN_PHP))
    script = ("<?php\nnamespace App\\Http\\Controllers { abstract class Controller {} }\nnamespace {\n"
              "function base_path($p = '') { return '/nonexistent/lq/' . ltrim($p, '/'); }\n"
              f"require '{settings}';\nrequire '{health}';\nrequire '{run}';\n"
              "use App\\Http\\Controllers\\RunController;\n$out = [];\n" + code
              + "\necho json_encode($out, JSON_UNESCAPED_UNICODE);\n}\n")
    with tempfile.NamedTemporaryFile("w", suffix=".php", delete=False, encoding="utf-8") as fh:
        fh.write(script)
        path = fh.name
    try:
        result = subprocess.run([PHP, path], capture_output=True, text=True, timeout=60, encoding="utf-8")
    finally:
        os.unlink(path)
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


@NEEDS_PHP
def test_proposal_and_backup_tab_names_follow_the_python_rule():
    titles = list(TAB_TITLES)
    out = _php_run("foreach (" + php_value(titles) + " as $t) { $out[] = RunController::looksLikeBackupTab($t); }")
    assert dict(zip(titles, out)) == {t: pc._looks_like_backup(t) for t in titles} == TAB_TITLES


@NEEDS_PHP
def test_the_run_page_line_names_the_tab_without_asking_google():
    out = _php_run("$out[] = RunController::sheetTab('Products', null);"
                   "$out[] = RunController::sheetTab('', 'منتجات جديدة مقترحة 2');"
                   "$out[] = RunController::sheetTab('', '');"
                   "$out[] = RunController::sheetTab('Copy of Products', 'Sheet1');")
    configured, first, unknown, backup = out
    assert configured == {"title": "Products", "configured": True, "text": "التشغيل بيقرأ من تبويب «Products»",
                          "suspicious": False}
    assert first["text"] == "التشغيل بيقرأ من تبويب «منتجات جديدة مقترحة 2» (أول تبويب بالشيت)" and first["suspicious"]
    assert unknown == {"title": "", "configured": False, "text": "التشغيل بيقرأ من أول تبويب بالشيت", "suspicious": False}
    assert backup["title"] == "Copy of Products" and backup["suspicious"] is True
    view = read(RUN_VIEW)
    assert 'data-run="sheet-tab"' in view and "{{ $sheetTab['text'] }}" in view
    assert "إذا مش تبويب منتجاتك، اختار التبويب الصح من الإعدادات" in view and "?tab=sheet" in view
    # the page reads the tab cheaply: the cached sheet read or the last publish check, never the bridge
    run = read(RUN_PHP)
    known = run[run.index("public static function knownFirstTab("):run.index("public static function looksLikeBackupTab(")]
    assert "PythonBridge" not in known and "SHEET_ROWS_CACHE_KEY" in known and "lastPublishCheck()" in known


def test_the_sheet_read_remembers_its_tab_title():
    import cli_bridge

    class WS:
        title = "منتجات جديدة مقترحة 2"

    assert cli_bridge._tab_title(WS()) == "منتجات جديدة مقترحة 2" and cli_bridge._tab_title(object()) == ""
    product = read(CONTROLLERS / "ProductController.php")
    assert "'tab' => is_string($result['sheet_tab'] ?? null) ? $result['sheet_tab'] : null" in product


def test_the_bridge_says_which_local_methods_are_installed(monkeypatch):
    import cli_bridge
    import image_processor

    out = cli_bridge.action_bg_methods({})
    assert out["status"] == "success" and set(out["local"]) == {"grabcut", "rembg"}
    import importlib.util
    assert out["local"]["grabcut"] is (importlib.util.find_spec("cv2") is not None)
    assert out["local"]["rembg"] is (importlib.util.find_spec("rembg") is not None)
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    assert image_processor.local_methods_available() == {"grabcut": False, "rembg": False}


# ---------------------------------------------------------------------------
# The review screen: the sentence for the provider codes and the skip in the failed-approvals panel
# ---------------------------------------------------------------------------

PROVIDER_TEXT = {
    "photoroom_402": "رصيد PhotoRoom خلص",
    "photoroom_401": "PhotoRoom رفض المفتاح",
    "photoroom_403": "PhotoRoom رفض المفتاح",
    "photoroom_429": "PhotoRoom رافض طلبات كتير هلق",
    "photoroom_no_key": "مفتاح PhotoRoom مش محفوظ",
    "photoroom_timeout": "PhotoRoom ما ردّ",
    "photoroom_connection_error": "PhotoRoom ما ردّ",
    "photoroom_502": "PhotoRoom ما ردّ",
    "photoroom_bad_output": "PhotoRoom رجّع خطأ",
    "removebg_402": "رصيد remove.bg خلص",
    "removebg_401": "remove.bg رفض المفتاح",
    "removebg_429": "remove.bg رافض طلبات كتير هلق",
    "removebg_timeout": "remove.bg ما ردّ",
    "removebg_no_key": "مفتاح remove.bg مش محفوظ",
    "rembg_not_installed": "مكتبة rembg مش منزّلة",
    "grabcut_empty": "عزل الخلفية المحلي ما طلّع المنتج",
    "none_empty_cutout": "عزل الخلفية المحلي ما طلّع المنتج",
}


@NEEDS_NODE
def test_every_provider_code_has_an_arabic_sentence():
    out = _node("globalThis.window = globalThis;\n" + read(CORE_JS) + "\nconst R = window.LaqtaReview;\n"
                "console.log(JSON.stringify({ text: " + json.dumps(list(PROVIDER_TEXT)) + ".map(c => R.plainError(c, 'FALLBACK')),"
                " skip: " + json.dumps(list(PROVIDER_TEXT)) + ".map(c => R.bgSkipCode(c)) }));")
    for code, text, skip in zip(PROVIDER_TEXT, out["text"], out["skip"]):
        assert PROVIDER_TEXT[code] in text and code not in text, (code, text)
        assert skip is pc.bg_skip_offered(code), code


@NEEDS_NODE
@pytest.mark.parametrize("code", ["photoroom_402", "removebg_401"])
def test_a_credit_failure_offers_the_skip_and_a_retry_publishes(tmp_path, code):
    out = page(r"""
openRow(30);
press('Enter');
await flush();
answer(requests('/api/select_image')[0], { status: 'failed', error: '__CODE__', isolated: false }, 500);
await flush();
out.text = jobsText();
out.buttons = document.querySelectorAll('#rvJobs button').map(b => b.textContent).filter(t => t);
document.querySelectorAll('.rv-jobs__skipbg')[0].click();
await flush();
out.confirms = confirms.slice();
out.saved = requests('/api/settings/bg-method').map(c => c.body);
out.toasts = toasts.map(t => [t.variant, t.text]);
out.after = jobsText();
out.afterButtons = document.querySelectorAll('#rvJobs button').map(b => b.textContent).filter(t => t);
document.querySelectorAll('#rvJobs button').find(b => b.textContent === 'أعد المحاولة').click();
await flush();
out.retried = requests('/api/select_image').length;
""".replace("__CODE__", code), tmp_path, fixture([picked(30, "Almarai Milk 1L"), picked(31, "Almarai Laban 1L")]),
               config={"row": 30, "bg": {"method": "photoroom", "previous": "photoroom", "confirm": "CONFIRM-SKIP"}})
    assert PROVIDER_TEXT[code] in out["text"] and code not in out["text"]
    assert "تجاوز عزل الخلفية…" in out["buttons"] and "أعد المحاولة" in out["buttons"]
    assert out["confirms"] == ["تجاوز عزل الخلفية؟ CONFIRM-SKIP"] and out["saved"] == [{"method": "none"}]
    assert ["success", "عزل الخلفية متوقف: اضغط «أعد المحاولة» على الصور اللي ما مشيت لتنتشر متل ما هي على لوحة بيضا."] \
        in out["toasts"]
    assert "تجاوز عزل الخلفية…" not in out["afterButtons"]
    assert "عزل الخلفية متوقف هلق: «أعد المحاولة» بيعتمدها متل ما هي." in out["after"]
    assert out["retried"] == 2                                            # «أعد المحاولة» sends the approval again


@NEEDS_NODE
def test_no_skip_for_other_failures_a_declined_confirm_or_an_unknown_method(tmp_path):
    out = page(r"""
openRow(30);
press('Enter');
await flush();
answer(requests('/api/select_image')[0], { status: 'failed', error: 'download_timeout' }, 500);
await flush();
out.download = document.querySelectorAll('.rv-jobs__skipbg').length;
openRow(31);
press('Enter');
await flush();
answer(requests('/api/select_image')[1], { status: 'failed', error: 'photoroom_402' }, 500);
await flush();
confirmAnswer = false;
document.querySelectorAll('.rv-jobs__skipbg')[0].click();
await flush();
out.declined = requests('/api/settings/bg-method').length;
out.confirms = confirms.length;
""", tmp_path, fixture([picked(30, "Almarai Milk 1L"), picked(31, "Almarai Laban 1L")]),
               config={"row": 30, "bg": {"method": "photoroom", "previous": "", "confirm": "CONFIRM-SKIP"}})
    assert out["download"] == 0 and out["declined"] == 0 and out["confirms"] == 1

    out = page(r"""
openRow(30);
press('Enter');
await flush();
answer(requests('/api/select_image')[0], { status: 'failed', error: 'photoroom_402' }, 500);
await flush();
out.skip = document.querySelectorAll('.rv-jobs__skipbg').length;
out.check = document.querySelectorAll('.rv-jobs__check').length;
""", tmp_path, fixture([picked(30, "Almarai Milk 1L")]), config={"row": 30})       # no database: method unknown
    assert out["skip"] == 0 and out["check"] == 1                       # the link to «فحص النشر» stays


@NEEDS_NODE
def test_an_approval_published_without_removal_says_so(tmp_path):
    out = page(r"""
openRow(30);
press('Enter');
await flush();
answer(requests('/api/select_image')[0], { status: 'success', image_link: 'https://res.cloudinary.com/demo/b.png',
                                           sheet_value: 'https://res.cloudinary.com/demo/b.png', isolated: false,
                                           bg_skipped: true, sheet: 'written' });
await flush();
openRow(31);
press('Enter');
await flush();
answer(requests('/api/select_image')[1], { status: 'success', image_link: 'https://res.cloudinary.com/demo/c.png',
                                           isolated: false, bg_skipped: true, sheet: 'written' });
await flush();
out.toasts = toasts.map(t => [t.variant, t.text]);
openRow(30);
out.text = wsText();
""", tmp_path, fixture([picked(30, "Almarai Milk 1L"), picked(31, "Almarai Laban 1L")]), config={"row": 30})
    said = [t for v, t in out["toasts"] if "بدون عزل الخلفية" in t]
    assert len(said) == 1 and said[0].startswith("انتشرت صورة «")             # once per session, as information
    assert [v for v, t in out["toasts"] if "بدون عزل الخلفية" in t] == ["info"]
    assert "تم الاعتماد." in out["text"] and "انتشرت بدون عزل الخلفية:" in out["text"]
    assert "الخلفية لم تُعزل" not in out["text"]


# ---------------------------------------------------------------------------
# The Laravel app (MariaDB and dashboard/vendor; skipped without them)
# ---------------------------------------------------------------------------

@pytest.fixture
def bg_app(app_env):
    db = app_env["db"]
    _sql(db, "DELETE FROM system_settings WHERE `key` = %s", ("bg_removal_method_previous",))
    _put(db, {"bg_removal_method": "photoroom"})
    return app_env


def test_the_endpoint_validates_and_stores_then_restores_the_previous_method(bg_app):
    db, env = bg_app["db"], bg_app["env"]
    out = _kernel(env, [
        ["POST", "/api/settings/bg-method", {"method": "magic"}],
        ["POST", "/api/settings/bg-method", {"method": ["none"]}],
        ["POST", "/api/settings/bg-method", {}],
        ["POST", "/api/settings/bg-method", {"method": "bria_rmbg"}],
    ])
    for response in out:
        body = json.loads(response["body"])
        assert response["status"] == 422 and body["status"] == "failed" and "مش مدعومة" in body["error"]
    assert _settings(db)["bg_removal_method"] == "photoroom" and "bg_removal_method_previous" not in _settings(db)

    skip, again, health, processing, restore = _kernel(env, [
        ["POST", "/api/settings/bg-method", {"method": " NONE "}],
        ["POST", "/api/settings/bg-method", {"method": "none"}],
        ["GET", "/system-diagnostics", {}],
        ["GET", "/settings?tab=processing", {}],
        ["POST", "/api/settings/bg-method", {"method": "photoroom"}],
    ])
    body = json.loads(skip["body"])
    assert skip["status"] == 200 and body["status"] == "success" and body["changed"] is True
    assert (body["method"], body["previous"], body["previous_label"]) == ("none", "photoroom", "PhotoRoom")
    assert "عزل الخلفية متوقف" in body["message"] and "افحص النشر" in body["message"]
    assert json.loads(again["body"])["changed"] is False                 # already off: the previous method is kept
    assert 'data-health="bg-box" data-state="off" data-method="none" data-previous="photoroom"' in health["body"]
    assert "رجّع عزل الخلفية (PhotoRoom)" in health["body"]
    assert "عزل الخلفية متوقف:" in processing["body"] and 'data-bg-restore="photoroom"' in processing["body"]
    assert "بدون عزل الخلفية: الصورة متل ما هي على لوحة بيضا وبتنتشر مباشرة" in processing["body"]
    body = json.loads(restore["body"])
    assert restore["status"] == 200 and body["method"] == "photoroom" and body["changed"] is True
    after = _settings(db)
    assert after["bg_removal_method"] == "photoroom" and after["bg_removal_method_previous"] == "photoroom"
    # every key stays on the server; nothing secret in any answer
    for response in (skip, again, health, processing, restore):
        for value in SECRETS.values():
            assert value not in response["body"]


def test_a_failed_check_offers_the_skip_and_the_previous_method_is_remembered(bg_app, tmp_path):
    db, env = bg_app["db"], bg_app["env"]
    _put(db, {"bg_removal_method": "remove_bg_api"})
    skip, health = _kernel(env, [["POST", "/api/settings/bg-method", {"method": "none"}],
                                 ["GET", "/system-diagnostics", {}]])
    assert json.loads(skip["body"])["previous_label"] == "remove.bg"
    assert "رجّع عزل الخلفية (remove.bg)" in health["body"]
    # the settings form choosing «بدون عزل الخلفية» remembers the method too
    _put(db, {"bg_removal_method": "photoroom"})
    _sql(db, "DELETE FROM system_settings WHERE `key` = %s", ("bg_removal_method_previous",))
    _kernel(env, [["POST", "/settings", {"section": "processing", "output_canvas_size": "800", "bg_removal_method": "none"}]])
    after = _settings(db)
    assert after["bg_removal_method"] == "none" and after["bg_removal_method_previous"] == "photoroom"


def test_the_endpoint_needs_the_csrf_token_like_every_post(bg_app):
    env = dict(bg_app["env"], APP_ENV="local")
    (response,) = _kernel(env, [["POST", "/api/settings/bg-method", {"method": "none"}]])
    assert response["status"] == 419
    assert _settings(bg_app["db"])["bg_removal_method"] == "photoroom"


def test_without_the_database_the_endpoint_says_so(bg_app):
    env = dict(bg_app["env"], DB_PORT="1")
    (response,) = _kernel(env, [["POST", "/api/settings/bg-method", {"method": "none"}]])
    body = json.loads(response["body"])
    assert response["status"] == 503 and "قاعدة البيانات" in body["error"]


def test_the_run_page_says_which_tab_the_run_reads(bg_app):
    (plain,) = _kernel(bg_app["env"], [["GET", "/batch-automation", {}]])
    assert plain["status"] == 200 and "التشغيل بيقرأ من" in plain["body"] and 'data-run="sheet-tab"' in plain["body"]
    (proposals,) = _kernel(dict(bg_app["env"], SPREADSHEET_TAB_NAME="منتجات جديدة مقترحة 2"),
                           [["GET", "/batch-automation", {}]])
    body = proposals["body"]
    assert "التشغيل بيقرأ من تبويب «منتجات جديدة مقترحة 2»" in body and 'data-suspicious="true"' in body
    assert "إذا مش تبويب منتجاتك، اختار التبويب الصح من الإعدادات" in body
