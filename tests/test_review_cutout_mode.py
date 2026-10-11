"""«فحص القص» as the review screen's third mode (/catalog?mode=cutout), instead of its own page (/cutout-check).

* The review scripts under node (laqta_review_harness, with cutout.js loaded as catalog.blade.php loads it): an admin's
  toggle reads «منتج واحد | بالجملة | فحص القص»; ?mode=cutout mounts cutout.js once into a box of the review screen
  (the gallery is asked for, the other two modes are hidden, the URL keeps ?mode=cutout); switching away and back keeps
  the same mount and is not remembered as the reviewer's mode; without the cutout config (a reviewer) there is no third
  item and ?mode=cutout falls back to «منتج واحد». R.cutout.mount / unmount on their own.
* Static: /cutout-check redirects, GotoIndex and the Health card point at the mode, ReviewController knows the mode.
* Through the Laravel kernel with logins (test_dashboard_roles): an admin gets the mode, a reviewer the plain review
  screen (no 403 page) and a 403 for every «فحص القص» click.
"""

import json
import re

import pytest

import laqta_review_harness
from laqta_review_harness import NODE, run
from laqta_kernel import NEEDS_LARAVEL
from test_dashboard_login import artisan
from test_dashboard_login import run as kernel
from test_dashboard_roles import ADMIN, DENIED, REVIEWER, env, login, password_of  # noqa: F401  (env: the fixture)
from test_laqta_review import fixture, js

ROOT = laqta_review_harness.ROOT
DASH = ROOT / "dashboard"
NEEDS_NODE = pytest.mark.skipif(NODE is None, reason="node is not installed")

CUTOUT = {"urls": {"gallery": "/api/cutout/gallery", "try": "/api/cutout/try", "apply": "/api/cutout/apply",
                   "discard": "/api/cutout/discard", "undo": "/api/cutout/undo"},
          "flags": ["white", "dark_halo", "photoroom_unsure", "glass", "dark_rim", "low_res"]}


def read(path):
    return path.read_text(encoding="utf-8")


@pytest.fixture
def with_cutout(monkeypatch):
    """The scripts in the page's order (catalog.blade.php): cutout.js after bulk.js, before app.js."""
    scripts = list(laqta_review_harness.SCRIPTS)
    if "cutout" not in scripts:
        scripts.insert(scripts.index("app"), "cutout")
    monkeypatch.setattr(laqta_review_harness, "SCRIPTS", scripts)


def page(scenario, tmp_path, config):
    return run(f"await boot({js(config)});\n" + scenario, tmp_path, fixture())


PROBE = r"""
const box = () => document.querySelector('.cq-mode');
const toggleOf = node => node ? node.querySelectorAll('.rv-modes__item').map(b => [b.getAttribute('data-mode'), b.getAttribute('aria-pressed')]) : null;
const galleryCalls = () => calls.filter(c => c.url.split('?')[0] === '/api/cutout/gallery').length;
const lastUrl = () => (historyLog[historyLog.length - 1] || [null, null])[1];
"""


@NEEDS_NODE
def test_an_admin_opens_the_cutout_mode_inside_the_review_screen(tmp_path, with_cutout):
    out = page(PROBE + r"""
out.mode = S().mode;
out.toggle = toggleOf(box());
out.singleToggle = toggleOf(document.querySelector('.rv-queue'));
out.hidden = [document.querySelector('.rv-single').hidden, document.querySelector('.rv-bulk').hidden, box().hidden];
out.title = box().querySelector('.cq__h1').textContent;
out.parts = ['filters', 'status', 'grid', 'pager', 'log'].map(p => !!box().querySelector('[data-cq="' + p + '"]'));
out.gallery = galleryCalls();
out.dataMode = document.getElementById('rvApp').getAttribute('data-mode');
// to «منتج واحد» with the toggle, and back: the same box, mounted once, not remembered as the reviewer's mode
const stored = {};
globalThis.localStorage = { getItem: k => (k in stored ? stored[k] : null), setItem: (k, v) => { stored[k] = String(v); } };
box().querySelector('[data-mode="single"]').click();
await flush();
out.afterSingle = [S().mode, box().hidden, document.querySelector('.rv-single').hidden, lastUrl()];
document.querySelector('.rv-queue [data-mode="cutout"]').click();
await flush();
out.back = [S().mode, box().hidden, document.querySelectorAll('.cq-mode').length, galleryCalls(), lastUrl()];
out.stored = stored['laqta.review.mode'] || null;
""", tmp_path, {"mode": "cutout", "cutout": CUTOUT})
    assert out["mode"] == "cutout" and out["dataMode"] == "cutout"
    assert out["toggle"] == [["single", "false"], ["bulk", "false"], ["cutout", "true"]]
    assert out["singleToggle"] == [["single", "false"], ["bulk", "false"], ["cutout", "true"]]
    assert out["hidden"] == [True, True, False]
    assert out["title"] == "فحص القص" and out["parts"] == [True] * 5
    assert out["gallery"] == 1
    assert out["afterSingle"][:3] == ["single", True, False] and "mode=cutout" not in (out["afterSingle"][3] or "")
    assert out["back"] == ["cutout", False, 1, 1, "/catalog?mode=cutout"]
    assert out["stored"] == "single"                     # «فحص القص» itself is never the remembered mode


@NEEDS_NODE
def test_without_the_cutout_config_there_is_no_third_mode(tmp_path, with_cutout):
    """A reviewer: ReviewController sends cutout null (and mode single); even a hand-made ?mode=cutout falls back."""
    out = page(PROBE + r"""
out.mode = S().mode;
out.toggle = toggleOf(document.querySelector('.rv-queue'));
out.box = !!box();
out.gallery = galleryCalls();
R.chooseMode('cutout');
await flush();
out.afterChoose = [S().mode, !!box()];
""", tmp_path, {"mode": "cutout", "cutout": None})
    assert out["mode"] == "single"
    assert out["toggle"] == [["single", "true"], ["bulk", "false"]]
    assert out["box"] is False and out["gallery"] == 0 and out["afterChoose"] == ["single", False]


@NEEDS_NODE
def test_mount_and_unmount_on_their_own(tmp_path, with_cutout):
    out = run(PROBE + r"""
const C = window.LaqtaReview.cutout;
const holder = document.createElement('div');
document.body.appendChild(holder);
const h1 = C.mount(holder, { config: { urls: { gallery: '/api/cutout/gallery' } } });
const h2 = C.mount(holder, {});
await flush();
out.once = h1 === h2 && galleryCalls() === 1;
out.built = !!holder.querySelector('.cq [data-cq="grid"]');
C.unmount(holder);
out.cleared = holder.children.length === 0 && !holder.__cq && h1.state.dead === true;
await h1.load({ measure: false });
out.noLoadAfter = galleryCalls() === 1;
""", tmp_path, fixture())
    assert out == {"once": True, "built": True, "cleared": True, "noLoadAfter": True}


# ---------------------------------------------------------------------------
# Static
# ---------------------------------------------------------------------------

def test_the_old_page_redirects_and_every_link_points_at_the_mode():
    recut = read(DASH / "app" / "Http" / "Controllers" / "RecutController.php")
    review = read(DASH / "app" / "Http" / "Controllers" / "ReviewController.php")
    assert "return redirect()->route('dashboard.catalog', ['mode' => 'cutout']);" in recut
    assert "public const MODES = ['single', 'bulk', 'cutout'];" in review
    assert "'cutout' => $reviewer ? null : RecutController::cutoutConfig()," in review
    assert "'cutoutCheck' => route('dashboard.catalog', ['mode' => 'cutout'])," in review
    assert "'/catalog?mode=cutout', 'cutout recut background', true" in read(DASH / "app" / "Services" / "GotoIndex.php")
    assert "route('dashboard.catalog', ['mode' => 'cutout'])" in read(DASH / "resources" / "views" / "dashboard" / "diagnostics.blade.php")
    assert not (DASH / "resources" / "views" / "dashboard" / "cutout_check.blade.php").exists()
    assert "'cutout-check'" not in read(DASH / "app" / "Http" / "Middleware" / "ReviewerLimits.php")
    # the cutout clicks stay the owner's: every one is a POST that is not a reviewer write
    limits = read(DASH / "app" / "Http" / "Middleware" / "ReviewerLimits.php")
    assert "api/cutout" not in limits
    routes = read(DASH / "routes" / "web.php")
    for action in ("try", "apply", "discard", "undo"):
        assert f"Route::post('/api/cutout/{action}'" in routes


def test_the_phone_swipes_the_three_backgrounds():
    css = re.sub(r"/\*.*?\*/", "", read(DASH / "public" / "css" / "pages" / "cutout.css"), flags=re.DOTALL)
    assert "minmax(min(100%, 340px), 1fr)" in css
    phone = css[css.index("@media (max-width: 560px)"):]
    assert "scroll-snap-type: x mandatory" in phone and "overflow-x: auto" in phone


# ---------------------------------------------------------------------------
# Through the kernel, with logins
# ---------------------------------------------------------------------------

def _config(body):
    import html
    match = re.search(r'id="rvApp"[^>]*data-config="([^"]+)"', body) or re.search(r'data-config="([^"]+)"[^>]*id="rvApp"', body)
    return json.loads(html.unescape(match.group(1)))


@NEEDS_LARAVEL
def test_each_role_through_the_kernel(env):
    environ, _calls, _db = env
    reviewer = password_of(artisan(environ, "laqta:user", REVIEWER, "--role=reviewer"))
    admin = password_of(artisan(environ, "laqta:user", ADMIN))
    out = kernel(environ, [
        login(REVIEWER, reviewer), ["GET", "/catalog?mode=cutout"], ["GET", "/cutout-check"],
        ["POST", "/api/cutout/try", {"id": "5", "method": "photoroom"}], ["POST", "/api/cutout/apply", {"token": "ab" * 16}],
        ["POST", "/api/cutout/undo", {"log_id": "1"}], ["POST", "/logout"],
        login(ADMIN, admin), ["GET", "/catalog?mode=cutout"], ["GET", "/cutout-check"],
    ], body=400000)
    as_reviewer, old_reviewer, *clicks = out[1:6]
    assert as_reviewer["status"] == 200 and DENIED not in as_reviewer["body"]
    config = _config(as_reviewer["body"])
    assert config["mode"] == "single" and config["cutout"] is None and config["urls"]["cutoutCheck"] == ""
    assert 'data-mode="single"' in as_reviewer["body"]
    assert old_reviewer["status"] == 302 and old_reviewer["location"].endswith("/catalog?mode=cutout")
    for res in clicks:
        assert res["status"] == 403 and json.loads(res["body"]) == {"status": "error", "error": DENIED}
    as_admin, old_admin = out[8:10]
    assert as_admin["status"] == 200 and 'data-mode="cutout"' in as_admin["body"]
    config = _config(as_admin["body"])
    assert config["mode"] == "cutout" and config["cutout"]["urls"]["gallery"].endswith("/api/cutout/gallery")
    assert config["urls"]["cutoutCheck"].endswith("/catalog?mode=cutout")
    assert old_admin["status"] == 302 and old_admin["location"].endswith("/catalog?mode=cutout")
