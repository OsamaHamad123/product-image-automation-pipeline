"""The review screen of wp/ux in a real browser: the pages rendered by the Laravel app (through its HTTP kernel, no
database), the real CSS and scripts, the data calls answered by the test (Chromium through Playwright for node;
skipped when php, dashboard/vendor, node Playwright or Chromium is missing).

* the bulk bar stays in view: at the top on a desktop, at the bottom (above the tab bar) on a phone;
* bulk opens by default with 10+ pictures proposed without a warning, and the reviewer's own choice is remembered;
* the in-page question: Tab stays inside, Esc cancels and sends nothing, Enter confirms;
* «تراجع» takes held approvals back; a held approval is sent at once (keepalive) when the page is hidden;
* the lightbox: Z, ← → (RTL), wheel zoom, Esc gives the focus back;
* at 390px: no horizontal scroll, the page opens at the top (the stacked list never scrolls it), a bottom tab bar;
* 1440 / 820 / 390: no console error. Screenshots go to the test's tmp dir (and LAQTA_UX_SHOTS when set).
"""

import glob
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
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

PAGES = {"auto": "/catalog", "single": "/catalog?mode=single", "bulk": "/catalog?mode=bulk", "home": "/"}


def _render(tmp_path):
    """The pages as the Laravel app renders them (no database: DB_PORT=1)."""
    compiled = tmp_path / "views"
    compiled.mkdir(exist_ok=True)
    script = f"""<?php
require '{DASH}/vendor/autoload.php';
$app = require '{DASH}/bootstrap/app.php';
$kernel = $app->make(Illuminate\\Contracts\\Http\\Kernel::class);
$out = [];
foreach (json_decode($argv[1], true) as $name => $uri) {{
    $request = Illuminate\\Http\\Request::create($uri, 'GET');
    $response = $kernel->handle($request);
    $out[$name] = $response->getContent();
    $kernel->terminate($request, $response);
}}
echo json_encode($out, JSON_UNESCAPED_UNICODE);
"""
    with tempfile.NamedTemporaryFile("w", suffix=".php", delete=False, encoding="utf-8") as fh:
        fh.write(script)
        path = fh.name
    env = dict(os.environ, APP_ENV="testing", APP_KEY="base64:" + "A" * 43 + "=", APP_DEBUG="true", SESSION_DRIVER="array",
               CACHE_STORE="array", LOG_CHANNEL="stderr", VIEW_COMPILED_PATH=str(compiled), DB_CONNECTION="mariadb",
               DB_HOST="127.0.0.1", DB_PORT="1", DB_DATABASE="automation_test_offline")
    try:
        res = subprocess.run([PHP, path, json.dumps(PAGES)], cwd=DASH, env=env, capture_output=True, text=True,
                             timeout=240, encoding="utf-8")
    finally:
        os.unlink(path)
    assert res.returncode == 0, res.stdout[-3000:] + res.stderr[-3000:]
    pages = json.loads(res.stdout)
    out = tmp_path / "pages"
    out.mkdir(exist_ok=True)
    for name, body in pages.items():
        (out / f"{name}.html").write_text(body, encoding="utf-8")
    return out


def _fixture():
    ev = {"brand": True, "size": "match", "gtin": "match", "source_class": "uae_retailer", "tier": 1}
    products, rows = [], []
    for i in range(30):
        row = 10 + i
        warned = i >= 24
        cands = [{"image_url": f"https://www.luluhypermarket.com/p{row}.jpg", "status": "preselected", "is_selected": 1,
                  "reasons": ["warn:foreign_store"] if warned else ["lane:strict"], "evidence": ev, "title": f"Milk {i}",
                  "width": 1200, "height": 1200}]
        cands += [{"image_url": f"https://www.carrefouruae.com/a{row}-{j}.jpg", "status": "eligible", "is_selected": 0,
                   "reasons": [], "evidence": ev, "title": f"Milk {i} alt {j}"} for j in range(5)]
        products.append({"row_number": row, "product_name": f"ALMARAI FRESH MILK FULL FAT {i} 1L", "brand": "Almarai",
                         "barcode": "", "sku_key": f"key-{row}", "size": "1L", "product_name_ar": "", "brand_ar": "",
                         "category": "Dairy", "existing_image_link": "https://res.cloudinary.com/demo/old.png" if i == 0 else "",
                         "needs_review": True, "preselected": True, "curation_candidates": cands})
        rows.append({"row_number": row, "sku_key": f"key-{row}", "status": "ready_for_review", "failure_code": None,
                     "product_name": products[-1]["product_name"], "brand": "Almarai", "updated_at": "2026-10-05 10:00:00"})
    queue = {"status": "success", "ready_for_review": len(rows), "rows": rows, "today": {"approved": 0, "rejected": 0}}
    lanes = {"status": "success", "lanes": {"strict": {"prechecked": 41, "accepted": 38, "ready": False, "more_needed": 12}}}
    return {"/api/products-json": {"status": "success", "products": products}, "/api/review/queue-state": queue,
            "/api/system/review-lanes": lanes, "/api/batch-status": {"status": "idle", "ready_for_review": len(rows)}}


PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="

DRIVER = r"""
const { chromium } = require(__PLAYWRIGHT__);
const fs = require('fs');
const path = require('path');
const PAGES = __PAGES__, PUB = __PUBLIC__, API = __API__, SHOTS = __SHOTS__;
const PNG = Buffer.from(__PNG__, 'base64');
const MIME = { '.css': 'text/css', '.js': 'application/javascript', '.svg': 'image/svg+xml', '.png': 'image/png' };
const out = { errors: {} };
(async () => {
  const browser = await chromium.launch({ executablePath: __CHROME__ });
  async function open(name, width, height, ctx) {
    ctx = ctx || await browser.newContext({ viewport: { width, height: height || 900 }, deviceScaleFactor: 1 });
    const page = await ctx.newPage();
    const errors = out.errors[`${name}@${width}`] = [];
    page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
    page.on('pageerror', e => errors.push('pageerror: ' + e.message));
    const selects = [];
    await page.addInitScript(() => {
      window.__fetches = [];
      const f = window.fetch.bind(window);
      window.fetch = (u, i) => { window.__fetches.push([String(u), !!(i && i.keepalive)]); return f(u, i); };
    });
    await page.route('**/*', async route => {
      const u = new URL(route.request().url());
      if (u.hostname !== 'localhost') return route.fulfill({ contentType: 'text/css', body: '' });
      if (route.request().resourceType() === 'document') {
        const key = Object.keys(PAGES).find(k => PAGES[k] === u.pathname + u.search) || name;
        return route.fulfill({ contentType: 'text/html', body: fs.readFileSync(path.join(__DIR__, key + '.html'), 'utf8') });
      }
      const file = path.join(PUB, u.pathname);
      if (/^\/(css|js)\//.test(u.pathname) && fs.existsSync(file)) {
        return route.fulfill({ contentType: MIME[path.extname(file)] || 'text/plain', body: fs.readFileSync(file) });
      }
      if (u.pathname === '/api/image-proxy') return route.fulfill({ contentType: 'image/png', body: PNG });
      if (u.pathname === '/api/select_image') { selects.push(JSON.parse(route.request().postData())); return; }     // held
      if (API[u.pathname]) return route.fulfill({ json: API[u.pathname] });
      return route.fulfill({ json: { status: 'success' } });
    });
    await page.goto('http://localhost' + PAGES[name]);
    return { page, selects, ctx };
  }
  const shot = (page, file) => page.screenshot({ path: path.join(SHOTS, file) });
  const rect = (page, sel) => page.evaluate(s => { const r = document.querySelector(s).getBoundingClientRect(); return { top: r.top, bottom: r.bottom, h: innerHeight }; }, sel);

  // 1440: bulk by default (24 clean pictures), the sticky bar, the question, undo
  {
    const { page, selects, ctx } = await open('auto', 1440, 900);
    await page.waitForSelector('.rv-card img');
    await page.waitForTimeout(700);
    out.autoMode = await page.evaluate(() => [LaqtaReview.S.mode, location.search]);
    await shot(page, 'bulk_1440.png');
    await page.mouse.wheel(0, 1500);
    await page.waitForTimeout(300);
    out.stickyTop = await rect(page, '.rv-bulk > .rv-bulkbar');
    out.scrolled = await page.evaluate(() => window.scrollY);
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.waitForTimeout(400);
    await page.keyboard.press('Shift+A');
    await page.waitForSelector('#rvAskConfirm');
    out.ask = await page.evaluate(() => [document.activeElement.id, document.getElementById('rvAskTitle').textContent]);
    await page.keyboard.press('Tab');
    out.tab1 = await page.evaluate(() => document.activeElement.id);
    await page.keyboard.press('Tab');
    out.tab2 = await page.evaluate(() => document.activeElement.id);
    await page.keyboard.press('Escape');
    await page.waitForTimeout(100);
    out.escaped = await page.evaluate(() => [!!document.getElementById('rvAskConfirm'), LaqtaReview.bulk.tickedKeys().length]);
    await page.keyboard.press('Shift+A');
    await page.waitForSelector('#rvAskConfirm');
    await page.waitForTimeout(500);
    await page.keyboard.press('Enter');
    await page.waitForSelector('#rvUndo:not([hidden])');
    await shot(page, 'bulk_undo_1440.png');
    out.held = [selects.length, await page.evaluate(() => document.getElementById('rvUndo').innerText)];
    await page.click('#rvUndo [data-undo]');
    await page.waitForTimeout(200);
    out.undone = [selects.length, await page.evaluate(() => document.querySelectorAll('.rv-card__overlay').length),
                  await page.evaluate(() => document.getElementById('rvUndo').hidden)];
    // one card approved with A, then the tab goes to the background: it is sent at once, with keepalive
    await page.keyboard.press('ArrowLeft');
    await page.waitForTimeout(500);
    await page.keyboard.press('a');
    await page.waitForTimeout(150);
    out.heldOne = selects.length;
    await page.evaluate(() => {
      Object.defineProperty(document, 'visibilityState', { value: 'hidden', configurable: true });
      document.dispatchEvent(new Event('visibilitychange'));
    });
    await page.waitForTimeout(200);
    out.hiddenSent = [selects.length, await page.evaluate(() => window.__fetches.filter(f => /select_image/.test(f[0])).map(f => f[1]))];
    // the mode toggle: the choice is remembered over a reload
    await page.evaluate(() => Object.defineProperty(document, 'visibilityState', { value: 'visible', configurable: true }));
    await page.evaluate(() => [...document.querySelectorAll('.rv-modes__item')].find(b => b.dataset.mode === 'single' && b.offsetParent).click());
    await page.waitForTimeout(300);
    const again = await open('auto', 1440, 900, ctx);
    await again.page.waitForSelector('.rv-pick img');
    out.remembered = await again.page.evaluate(() => LaqtaReview.S.mode);
    await ctx.close();
  }
  // 1440 single: the lightbox
  {
    const { page, ctx } = await open('single', 1440, 900);
    await page.waitForSelector('.rv-pick img');
    await page.waitForTimeout(600);
    await shot(page, 'single_1440.png');
    await page.focus('#rvApprove').catch(() => {});
    out.openerId = await page.evaluate(() => document.activeElement && document.activeElement.id);
    await page.keyboard.press('z');
    await page.waitForSelector('#rvLightbox .rv-lb__img');
    await page.waitForTimeout(200);
    out.lbOpen = await page.evaluate(() => [document.querySelector('.rv-lb__count').textContent, document.activeElement.id,
                                            document.querySelector('.rv-lb__side').innerText.includes('الصورة الحالية بالشيت')]);
    await shot(page, 'lightbox_1440.png');
    await page.keyboard.press('ArrowLeft');
    out.lbLeft = await page.evaluate(() => document.querySelector('.rv-lb__count').textContent);
    await page.keyboard.press('ArrowRight');
    out.lbRight = await page.evaluate(() => document.querySelector('.rv-lb__count').textContent);
    const box = await page.locator('.rv-lb__stage').boundingBox();
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    await page.mouse.wheel(0, -300);
    await page.waitForTimeout(150);
    out.lbZoom = await page.evaluate(() => LaqtaReview.lightboxOpen().scale);
    await page.keyboard.press('Escape');
    out.lbClosed = await page.evaluate(() => [!!document.getElementById('rvLightbox'), document.activeElement && document.activeElement.id]);
    await ctx.close();
  }
  // Home: the layout's question (Laqta.ask) and the tab title's count
  {
    const { page, ctx } = await open('home', 1440, 900);
    await page.waitForTimeout(800);
    out.homeTitle = await page.title();
    await page.evaluate(() => { window.__answers = []; Laqta.ask('ما في ولا صف بينمسح.\nبدك توقف التشغيل؟').then(v => window.__answers.push(v)); });
    await page.waitForSelector('#lqAskConfirm');
    out.layoutAsk = await page.evaluate(() => [document.getElementById('lqAskTitle').textContent, document.getElementById('lqAskText').textContent,
                                               document.activeElement.id]);
    await page.keyboard.press('Enter');                       // at once: never a «yes»
    out.layoutFast = await page.evaluate(() => [window.__answers.slice(), !!document.getElementById('lqAskConfirm')]);
    await page.keyboard.press('Escape');
    await page.waitForTimeout(50);
    await page.evaluate(() => { Laqta.ask('رح ننكتب الباركود؟').then(v => window.__answers.push(v)); });
    await page.waitForTimeout(500);
    await page.keyboard.press('Enter');
    await page.waitForTimeout(50);
    out.layoutAnswers = await page.evaluate(() => [window.__answers, !!document.querySelector('.lq-dialog-backdrop')]);
    await ctx.close();
  }
  // 820 and 390: the pages, horizontal scroll, the scroll position, the phone bars
  for (const width of [820, 390]) {
    for (const name of ['single', 'bulk', 'home']) {
      const { page, ctx } = await open(name, width, width === 390 ? 844 : 1180);
      await page.waitForTimeout(1200);
      await shot(page, `${name}_${width}.png`);
      out[`${name}@${width}`] = await page.evaluate(() => ({ overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
                                                              scrollY: window.scrollY }));
      if (name === 'bulk' && width === 390) {
        await page.mouse.wheel(0, 1200);
        await page.waitForTimeout(300);
        out.phoneBar = await rect(page, '.rv-bulk > .rv-bulkbar');
        out.tabbar = await rect(page, '.lq-sidebar');
      }
      if (name === 'single' && width === 390) {
        out.phoneActions = await page.evaluate(() => ['#rvApprove', '#rvReject', '#rvMore', '#rvSkip', '#rvNotFoundToggle']
          .map(s => { const r = document.querySelector(s).getBoundingClientRect(); return r.width > 0 ? Math.round(r.top) : null; }));
      }
      await ctx.close();
    }
  }
  await browser.close();
  console.log('__OUT__' + JSON.stringify(out));
})().catch(e => { console.error(e && e.stack || e); process.exit(3); });
"""


def test_the_review_ux_in_a_browser(tmp_path):
    pages = _render(tmp_path)
    shots = Path(os.environ.get("LAQTA_UX_SHOTS") or (tmp_path / "shots"))
    shots.mkdir(parents=True, exist_ok=True)
    script = DRIVER.replace("__PLAYWRIGHT__", json.dumps(PLAYWRIGHT)).replace("__PAGES__", json.dumps(PAGES)) \
        .replace("__PUBLIC__", json.dumps(str(PUBLIC))).replace("__API__", json.dumps(_fixture(), ensure_ascii=False)) \
        .replace("__SHOTS__", json.dumps(str(shots))).replace("__PNG__", json.dumps(PNG)) \
        .replace("__CHROME__", json.dumps(CHROME[-1])).replace("__DIR__", json.dumps(str(pages)))
    path = tmp_path / "ux_browser.js"
    path.write_text(script, encoding="utf-8")
    env = dict(os.environ, PLAYWRIGHT_BROWSERS_PATH=BROWSERS)
    res = subprocess.run([NODE, str(path)], capture_output=True, text=True, timeout=300, env=env, encoding="utf-8")
    assert res.returncode == 0, res.stderr[-4000:]
    out = json.loads(next(line for line in res.stdout.splitlines() if line.startswith("__OUT__"))[len("__OUT__"):])

    # no console error on any page at any width
    assert all(not errs for errs in out["errors"].values()), out["errors"]
    # bulk by default; the bar stays at the top while the cards scroll
    assert out["autoMode"] == ["bulk", "?mode=bulk&filter=eligible"]
    assert out["scrolled"] > 1000 and 0 <= out["stickyTop"]["top"] <= 2
    # the question: focus on «أكيد», Tab stays inside, Esc sends nothing; Enter confirms
    assert out["ask"][0] == "rvAskConfirm" and out["ask"][1].startswith("اعتماد")
    assert (out["tab1"], out["tab2"]) == ("rvAskCancel", "rvAskConfirm")
    assert out["escaped"][0] is False and out["escaped"][1] > 0
    # held for «تراجع», taken back: nothing sent; a held one goes out with keepalive when the tab is hidden
    assert out["held"][0] == 0 and "تراجع" in out["held"][1]
    assert out["undone"] == [0, 0, True]
    assert out["heldOne"] == 0
    assert out["hiddenSent"] == [1, [True]]
    assert out["remembered"] == "single"
    # the lightbox
    assert out["lbOpen"] == ["1 من 6", "rvLbClose", True]
    assert out["lbLeft"] == "2 من 6" and out["lbRight"] == "1 من 6"
    assert out["lbZoom"] > 1
    assert out["lbClosed"] == [False, out["openerId"]]
    # 820 / 390: no horizontal scroll, the page opens at the top
    for width in (820, 390):
        for name in ("single", "bulk", "home"):
            assert out[f"{name}@{width}"]["overflow"] <= 0, (name, width, out[f"{name}@{width}"])
    assert out["single@390"]["scrollY"] == 0 and out["single@820"]["scrollY"] == 0
    # 390: the bulk bar sits right above the bottom tab bar while the cards scroll; one compact action row
    assert abs(out["phoneBar"]["bottom"] - out["tabbar"]["top"]) <= 1 and out["tabbar"]["bottom"] == out["tabbar"]["h"]
    approve, reject, more, skip, not_found = out["phoneActions"]
    assert approve == reject == more and skip is None and not_found is None
    # the layout: the review count in the tab title; the page's own question
    assert out["homeTitle"] == "(30) الرئيسية · لقطة"
    assert out["layoutAsk"] == ["بدك توقف التشغيل؟", "ما في ولا صف بينمسح.", "lqAskConfirm"]
    assert out["layoutFast"] == [[], True]
    assert out["layoutAnswers"] == [[False, True], False]
    for name in ("bulk_1440", "single_1440", "lightbox_1440", "single_390", "bulk_390", "home_390"):
        assert (shots / f"{name}.png").stat().st_size > 1000
