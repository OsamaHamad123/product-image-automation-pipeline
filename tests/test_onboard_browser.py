"""«جهّز لقطة» and the Home card in a real browser (Chromium through Playwright for node; skipped when php,
dashboard/vendor, node Playwright, Chromium or MariaDB is missing).

The pages are rendered by the Laravel app through its HTTP kernel against the MariaDB test database with a stub
bridge, after real live checks of the sheet and the brands. The answers the buttons get (the keys test, the publish
rehearsal, the first-run plan, its start and progress) are also the app's own answers, computed through the kernel
beforehand, then served to the page. At 1440 and 390 px: every step, the buttons' results, no horizontal scroll, no
console error. Screenshots go to the test's tmp dir (and LAQTA_ONBOARD_SHOTS when set).
"""

import glob
import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from test_setup_wizard import (DIAGNOSTICS, ROOT, _kernel, aside_results, setup_app)  # noqa: F401 - fixtures

DASH = ROOT / "dashboard"
PUBLIC = DASH / "public"
NODE = shutil.which("node")
PHP = shutil.which("php")
BROWSERS = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/opt/pw-browsers")
CHROME = sorted(glob.glob(os.path.join(BROWSERS, "chromium-*", "chrome-linux", "chrome")))


def _playwright_module():
    if NODE is None:
        return None
    for candidate in ("playwright", "/opt/node22/lib/node_modules/playwright"):
        res = subprocess.run([NODE, "-e", f"console.log(require.resolve({json.dumps(candidate)}))"],
                             capture_output=True, text=True)
        if res.returncode == 0:
            return res.stdout.strip()
    return None


PLAYWRIGHT = _playwright_module()
pytestmark = pytest.mark.skipif(PLAYWRIGHT is None or not CHROME or PHP is None or not (DASH / "vendor" / "autoload.php").exists(),
                                reason="node Playwright, Chromium, php or dashboard/vendor is missing")

WIZARD = {"setup_sheet": "/setup?step=sheet", "setup_keys": "/setup?step=keys", "setup_brands": "/setup?step=brands",
          "setup_publish": "/setup?step=publish", "setup_run": "/setup?step=run", "setup_ready": "/setup?step=ready",
          "home": "/"}
NOW = datetime.now(timezone.utc).isoformat(timespec="seconds")
PUBLISH_RESULT = {
    "ok": True, "overall": "ok", "started_at": NOW, "finished_at": NOW, "duration_ms": 41000,
    "summary_ar": "النشر شغّال: نزّلنا الصورة وعزلنا خلفيتها ورفعناها وكتبنا بالشيت.",
    "sample": {"kind": "bundled", "product_name": "", "row_number": None}, "notes": [],
    "steps": [{"key": k, "status": "ok", "ms": ms, "title_ar": "", "detail_ar": "", "action_ar": "", "code": ""}
              for k, ms in (("download", 1200), ("process", 6400), ("upload", 2100), ("sheet", 900))],
}
LAST_REPORT = {"trigger": "dashboard", "started_at": NOW, "outcome": "done", "stop_reason": None, "attempts": 1,
               "counts": {"ready_for_review": 4, "not_found": 1, "auto_published": 0, "failed": 0, "pending_left": 0},
               "reason_text": ""}
LIVE = [{"status": "success", "batch": {"phase": "running"}, "run": {"processed": 2, "total": 5, "counts": {}}},
        {"status": "success", "batch": {"phase": "idle"},
         "run": {"processed": 5, "total": 5, "counts": {"proposed": 4, "not_found": 1}}}]


def _wizard_fixture(app, pages_dir):
    env = app["env"]
    # real live checks first: the pages show what the server saved
    _kernel(env, [["POST", "/api/setup/check", {"step": "sheet"}], ["POST", "/api/setup/check", {"step": "brands"}]])
    rendered = _kernel(env, [["GET", uri, {}] for uri in WIZARD.values()])
    for (name, _), out in zip(WIZARD.items(), rendered):
        assert out["status"] == 200, (name, out["body"][:2000])
        (pages_dir / f"{name}.html").write_text(out["body"], encoding="utf-8")
    # what the buttons get: the app's own answers
    stub = app["tmp"] / "python_stub.sh"
    stub.write_text("#!/bin/sh\ncat <<'EOF'\n" + json.dumps(DIAGNOSTICS) + "\nEOF\n", encoding="utf-8")
    stub.chmod(0o755)
    (keys,) = _kernel(dict(env, PYTHON_PATH=str(stub)), [["POST", "/api/setup/check", {"step": "keys", "test": "1"}]])
    publish_file = ROOT / "temp" / "publish_check_last.json"
    publish_file.parent.mkdir(parents=True, exist_ok=True)
    publish_file.write_text(json.dumps(PUBLISH_RESULT, ensure_ascii=False), encoding="utf-8")
    publish, publish_now, plan, started = _kernel(env, [
        ["POST", "/api/setup/check", {"step": "publish"}], ["GET", "/api/system/publish-check", {}],
        ["POST", "/api/setup/check", {"step": "run"}], ["POST", "/api/setup/progress", {"action": "run_started", "rows": "2, 4-7"}]])
    report = ROOT / "temp" / "nightly" / "last_report.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(LAST_REPORT, ensure_ascii=False), encoding="utf-8")
    (final,) = _kernel(env, [["GET", "/api/setup/state", {}]])
    answers = {"check": {"keys": json.loads(keys["body"]), "publish": json.loads(publish["body"]),
                         "run": json.loads(plan["body"])},
               "/api/system/publish-check": json.loads(publish_now["body"]),
               "/api/setup/progress": json.loads(started["body"]),
               "/api/setup/state": json.loads(final["body"]),
               "/api/run-all": {"status": "success", "message": "started"},
               "/api/batch-status": {"status": "idle", "phase": "idle", "ready_for_review": 0}}
    assert answers["check"]["keys"]["steps"][1]["status"] == "ok"
    assert answers["check"]["publish"]["steps"][3]["status"] == "ok"
    assert answers["check"]["run"]["plan"]["rows"] == "2, 4-7"
    assert answers["/api/setup/state"]["steps"][4]["status"] == "ok"
    return answers


DRIVER = r"""
const { chromium } = require(__PLAYWRIGHT__);
const fs = require('fs');
const path = require('path');
const PAGES = __PAGES__, PUB = __PUBLIC__, API = __API__, LIVE = __LIVE__, SHOTS = __SHOTS__, DIR = __DIR__;
const MIME = { '.css': 'text/css', '.js': 'application/javascript', '.svg': 'image/svg+xml', '.png': 'image/png' };
const out = { errors: {}, overflow: {}, posts: [] };
(async () => {
  const browser = await chromium.launch({ executablePath: __CHROME__ });
  async function open(name, width) {
    const ctx = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 900 }, deviceScaleFactor: 1 });
    const page = await ctx.newPage();
    const errors = out.errors[`${name}@${width}`] = [];
    page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
    page.on('pageerror', e => errors.push('pageerror: ' + e.message));
    const live = LIVE.slice();
    await page.route('**/*', async route => {
      const req = route.request();
      const u = new URL(req.url());
      if (u.hostname !== 'localhost') return route.fulfill({ contentType: 'text/css', body: '' });
      if (req.resourceType() === 'document') {
        const key = Object.keys(PAGES).find(k => PAGES[k] === u.pathname + u.search) || name;
        return route.fulfill({ contentType: 'text/html', body: fs.readFileSync(path.join(DIR, key + '.html'), 'utf8') });
      }
      const file = path.join(PUB, u.pathname);
      if (/^\/(css|js)\//.test(u.pathname) && fs.existsSync(file)) {
        return route.fulfill({ contentType: MIME[path.extname(file)] || 'text/plain', body: fs.readFileSync(file) });
      }
      if (req.method() === 'POST') out.posts.push([u.pathname, req.postData()]);
      if (u.pathname === '/api/setup/check') {
        const step = JSON.parse(req.postData() || '{}').step;
        return route.fulfill({ json: API.check[step] });
      }
      if (u.pathname === '/api/run/live') return route.fulfill({ json: live.length > 1 ? live.shift() : live[0] });
      if (API[u.pathname]) return route.fulfill({ json: API[u.pathname] });
      return route.fulfill({ json: { status: 'success' } });
    });
    await page.goto('http://localhost' + PAGES[name]);
    await page.waitForTimeout(400);
    out.overflow[`${name}@${width}`] = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    return { page, ctx };
  }
  const shot = (page, file) => page.screenshot({ path: path.join(SHOTS, file), fullPage: true });
  const status = (page, key) => page.getAttribute(`[data-setup-step="${key}"]`, 'data-status');
  for (const width of [1440, 390]) {
    for (const name of Object.keys(PAGES)) {
      const { page, ctx } = await open(name, width);
      await shot(page, `${name}_${width}.png`);
      if (name === 'setup_keys') {
        out[`keysBefore@${width}`] = await status(page, 'keys');
        await page.click('[data-setup="keys-test"]');
        await page.waitForSelector('[data-setup-step="keys"][data-status="ok"]', { timeout: 5000 });
        out[`keysAfter@${width}`] = await page.evaluate(() => [...document.querySelectorAll('[data-setup-step="keys"] .lq-setup-check__state')].map(e => e.textContent));
        await shot(page, `setup_keys_tested_${width}.png`);
      }
      if (name === 'setup_publish') {
        await page.click('[data-setup="publish-run"]');
        await page.waitForSelector('[data-setup-step="publish"][data-status="ok"]', { timeout: 5000 });
        out[`publish@${width}`] = await page.textContent('[data-setup-step="publish"] [data-setup-part="summary"]');
        await shot(page, `setup_publish_done_${width}.png`);
      }
      if (name === 'setup_run') {
        await page.click('[data-setup="run-plan"]');
        await page.waitForSelector('[data-setup="plan"]:not([hidden])');
        out[`plan@${width}`] = await page.textContent('[data-setup="plan-text"]');
        await shot(page, `setup_run_plan_${width}.png`);
        await page.click('[data-setup="run-start"]');
        await page.waitForSelector('[data-setup="review-link"]:not([hidden])', { timeout: 15000 });
        out[`run@${width}`] = [await status(page, 'run'), await page.textContent('[data-setup="live-text"]'),
                               await page.evaluate(() => location.search)];
        await shot(page, `setup_run_done_${width}.png`);
      }
      if (name === 'setup_sheet' && width === 1440) {
        // the stepper moves between steps without a reload and keeps ?step= in the address
        await page.click('[data-setup-tab="brands"]');
        out.stepper = await page.evaluate(() => [location.search, !document.querySelector('[data-setup-step="brands"]').hidden,
                                                 document.querySelector('[data-setup-step="sheet"]').hidden]);
        await page.click('[data-setup-step="brands"] [data-setup-go="next"]');
        out.next = await page.evaluate(() => location.search);
      }
      await ctx.close();
    }
  }
  await browser.close();
  console.log('__OUT__' + JSON.stringify(out));
})().catch(e => { console.error(e && e.stack || e); process.exit(3); });
"""


def _run_driver(tmp_path, pages, uris, api, shots):
    script = DRIVER.replace("__PLAYWRIGHT__", json.dumps(PLAYWRIGHT)).replace("__PAGES__", json.dumps(uris)) \
        .replace("__PUBLIC__", json.dumps(str(PUBLIC))).replace("__API__", json.dumps(api, ensure_ascii=False)) \
        .replace("__LIVE__", json.dumps(LIVE)).replace("__SHOTS__", json.dumps(str(shots))) \
        .replace("__CHROME__", json.dumps(CHROME[-1])).replace("__DIR__", json.dumps(str(pages)))
    path = tmp_path / "onboard_browser.js"
    path.write_text(script, encoding="utf-8")
    env = dict(os.environ, PLAYWRIGHT_BROWSERS_PATH=BROWSERS)
    res = subprocess.run([NODE, str(path)], capture_output=True, text=True, timeout=400, env=env, encoding="utf-8")
    assert res.returncode == 0, res.stderr[-4000:]
    return json.loads(next(line for line in res.stdout.splitlines() if line.startswith("__OUT__"))[len("__OUT__"):])


def test_the_wizard_in_a_browser(setup_app, tmp_path):
    pages = tmp_path / "pages"
    pages.mkdir()
    api = _wizard_fixture(setup_app, pages)
    shots = Path(os.environ.get("LAQTA_ONBOARD_SHOTS") or (tmp_path / "shots"))
    shots.mkdir(parents=True, exist_ok=True)
    out = _run_driver(tmp_path, pages, WIZARD, api, shots)

    assert all(not errs for errs in out["errors"].values()), out["errors"]
    assert all(v <= 0 for v in out["overflow"].values()), out["overflow"]
    for width in (1440, 390):
        assert out[f"keysBefore@{width}"] == "todo"
        assert out[f"keysAfter@{width}"] == ["محفوظ · يعمل"] * 4
        assert out[f"publish@{width}"].startswith("النشر شغّال")
        assert out[f"plan@{width}"].startswith("رح ندوّر على صور 5 منتجات من الشيت (الصفوف 2, 4-7)")
        status, live, search = out[f"run@{width}"]
        assert status == "ok" and live == "خلص التشغيل: 4 مقترحة · 1 ما انلقت." and search == "?step=run"
    assert out["stepper"] == ["?step=brands", True, True] and out["next"] == "?step=publish"
    posts = [p[0] for p in out["posts"]]
    assert "/api/run-all" in posts and json.loads(dict(out["posts"])["/api/run-all"])["row_filter"] == "2, 4-7"
    for name in list(WIZARD) + ["setup_keys_tested", "setup_publish_done", "setup_run_plan", "setup_run_done"]:
        for width in (1440, 390):
            assert (shots / f"{name}_{width}.png").stat().st_size > 1000, (name, width)
