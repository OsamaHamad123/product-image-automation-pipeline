"""The review screen in a real browser (Chromium through Playwright for node; skipped when either is missing).

The page is the review scripts and stylesheets with the data calls answered by the test: real layout, real image
loading, a real viewport and real key presses. What the node harness can only simulate is checked here:

* bulk mode pre-ticks and approves only cards whose picture loaded while they were on screen (#9), never a card
  whose picture failed (#4), and two quick A presses approve one card (#4);
* single mode: a second Enter right after approving never approves the next product unseen, a pick with warnings
  is confirmed by name, and a reload that changes the open product blocks approval until it is shown again (#1, #4).
"""

import glob
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "dashboard" / "public"
NODE = shutil.which("node")
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
pytestmark = pytest.mark.skipif(PLAYWRIGHT is None or not CHROME, reason="node Playwright or Chromium is not installed")

PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="

DRIVER = r"""
const { chromium } = require(__PLAYWRIGHT__);
const fs = require('fs');
const D = __PUBLIC__;
const scripts = ['core', 'ui', 'jobs', 'single', 'bulk', 'app'].map(n => fs.readFileSync(`${D}/js/review/${n}.js`, 'utf8'));
const css = fs.readFileSync(`${D}/css/laqta.css`, 'utf8') + '\n' + fs.readFileSync(`${D}/css/pages/review.css`, 'utf8');
const FX = __FIXTURE__;
const PNG = Buffer.from(__PNG__, 'base64');
const html = cfg => `<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8"><meta name="csrf-token" content="t"><style>${css}</style></head>
<body class="page-review"><main class="lq-main lq-main--flush"><div id="rvApp" class="rv-app" data-config='${JSON.stringify(cfg)}'></div></main>
<script>window.__confirms=[];window.Laqta={toast(){return {close(){},update(){}}},onRunStatus(){}};
// the review screen asks in the page (R.ask): the question is recorded and «متأكد» clicked, like confirm() returning true
new MutationObserver(() => { const b = document.getElementById('rvAskConfirm'); if (!b || b.__auto) return; b.__auto = 1;
  const t = (document.getElementById('rvAskTitle') || {}).textContent || '', x = (document.getElementById('rvAskText') || {}).textContent || '';
  window.__confirms.push(t && x ? t + (/[؟?.:]$/.test(t) ? ' ' : ': ') + x : (t || x)); setTimeout(() => b.click(), 0);
}).observe(document.documentElement, { childList: true, subtree: true });</script>
${scripts.map(s => `<script>${s}</script>`).join('\n')}</body></html>`;
const out = {};
(async () => {
  const browser = await chromium.launch({ executablePath: __CHROME__ });
  async function open(cfg, viewport) {
    const page = await browser.newPage({ viewport: viewport || { width: 1400, height: 1000 } });
    const selects = [];
    await page.route('**/*', async route => {
      const u = new URL(route.request().url());
      if (u.pathname === '/') return route.fulfill({ contentType: 'text/html', body: html(cfg) });
      if (u.pathname === '/api/products-json') return route.fulfill({ json: { status: 'success', products: FX.products } });
      if (u.pathname === '/api/review/queue-state') return route.fulfill({ json: FX.queue });
      if (u.pathname === '/api/select_image') { selects.push(JSON.parse(route.request().postData())); return; }   // held
      if (u.pathname === '/api/image-proxy') {
        const src = u.searchParams.get('url') || '';
        return /broken/.test(src) ? route.fulfill({ status: 404, body: '' }) : route.fulfill({ contentType: 'image/png', body: PNG });
      }
      return route.fulfill({ json: { status: 'success' } });
    });
    await page.goto('http://localhost/');
    return { page, selects };
  }
  const approving = page => page.evaluate(() => [...LaqtaReview.S.local.entries()].filter(([, v]) => v === 'approving')
                                                  .map(([k]) => LaqtaReview.S.byKey.get(k).product.row_number));

  // bulk: only the cards seen on screen, never a broken picture
  {
    const { page } = await open({ mode: 'bulk', filter: 'all', canvas: 800, db: 'online', autoSearchDelayMs: 0, approveUndoMs: 0, urls: {} });
    await page.waitForSelector('.rv-card[data-key] img');
    await page.waitForTimeout(600);
    out.rendered = await page.evaluate(() => document.querySelectorAll('.rv-card').length);
    out.inView = await page.evaluate(() => [...document.querySelectorAll('.rv-card')]
        .filter(n => { const r = n.getBoundingClientRect(); return r.bottom > 0 && r.top < innerHeight; }).length);
    out.broken = await page.evaluate(() => document.querySelectorAll('.rv-card .rv-img-missing').length);
    out.tickedAtLoad = await page.evaluate(() => LaqtaReview.bulk.tickedKeys().map(k => LaqtaReview.S.byKey.get(k).product.row_number));
    await page.keyboard.press('Shift+A');
    await page.waitForTimeout(300);
    out.bulkConfirm = await page.evaluate(() => window.__confirms.slice(-1)[0] || '');
    out.bulkQueued = await approving(page);
    await page.close();
  }
  // bulk: two quick A presses approve one card
  {
    const { page } = await open({ mode: 'bulk', filter: 'all', canvas: 800, db: 'online', autoSearchDelayMs: 0, approveUndoMs: 0, urls: {} });
    await page.waitForSelector('.rv-card[data-key] img');
    await page.waitForTimeout(600);
    await page.keyboard.press('ArrowLeft');
    await page.keyboard.press('a');
    await page.keyboard.press('a');
    await page.waitForTimeout(200);
    out.doubleA = await approving(page);
    await page.close();
  }
  // single: Enter, Enter at once; then the next product (with a warning) after it has been on screen
  {
    const { page, selects } = await open({ mode: 'single', filter: 'all', row: 10, canvas: 800, db: 'online',
                                           autoSearchDelayMs: 0, approveUndoMs: 0, urls: {} });
    await page.waitForSelector('.rv-pick img');
    await page.waitForTimeout(600);
    await page.keyboard.press('Enter');
    await page.keyboard.press('Enter');
    await page.waitForTimeout(150);
    out.doubleEnter = [selects.map(s => s.row_number), await approving(page),
                       await page.evaluate(() => document.querySelector('.rv-product__name').textContent)];
    await page.waitForTimeout(600);
    await page.keyboard.press('Enter');                     // the next product, once it has been on screen
    await page.waitForTimeout(150);
    out.afterDelay = await approving(page);
    // the product with a warning, opened from the list: Enter asks first, naming it
    await page.evaluate(() => [...document.querySelectorAll('.rv-item')].find(b =>
        LaqtaReview.S.byKey.get(b.getAttribute('data-key')).product.row_number === 11).click());
    await page.waitForSelector('.rv-pick img');
    await page.waitForTimeout(600);
    await page.keyboard.press('Enter');
    await page.waitForTimeout(150);
    out.warnConfirm = await page.evaluate(() => window.__confirms.slice(-1)[0] || '');
    out.afterConfirm = await approving(page);
    out.firstExpected = selects[0] && selects[0].expected_state;
    await page.close();
  }
  // single: a reload that changes the open product blocks approval until it is shown again
  {
    const { page, selects } = await open({ mode: 'single', filter: 'all', row: 12, canvas: 800, db: 'online',
                                           autoSearchDelayMs: 0, approveUndoMs: 0, urls: {} });
    await page.waitForSelector('.rv-pick img');
    await page.waitForTimeout(600);
    FX.queue.rows = FX.queue.rows.map(r => r.row_number === 12 ? Object.assign({}, r, { updated_at: '2026-10-03 10:30:00' }) : r);
    await page.evaluate(() => LaqtaReview.loadData({ quiet: true }));
    await page.waitForSelector('#rvReopen');
    out.movedDisabled = await page.evaluate(() => document.getElementById('rvApprove').disabled);
    await page.keyboard.press('Enter');
    await page.waitForTimeout(150);
    out.movedSent = selects.length;
    await page.click('#rvReopen');
    await page.waitForTimeout(600);
    await page.keyboard.press('Enter');
    await page.waitForTimeout(150);
    out.afterReopen = selects.map(s => s.expected_state.queue_updated_at);
    await page.close();
  }
  await browser.close();
  console.log('__OUT__' + JSON.stringify(out));
})().catch(e => { console.error(e && e.stack || e); process.exit(3); });
"""


def _fixture():
    ev = {"brand": True, "size": "match", "gtin": "match", "source_class": "uae_retailer", "tier": 1}
    products = []
    for i in range(40):
        row = 10 + i
        url = f"https://img.example/{'broken' if row in (14, 15) else 'ok'}-{row}.png"
        reasons = ["warn:foreign_store"] if row == 11 else []
        products.append({
            "row_number": row, "product_name": f"Product {i}", "brand": "Almarai", "barcode": "", "sku_key": f"key-{row}",
            "size": "1L", "product_name_ar": "", "brand_ar": "", "category": "Dairy", "existing_image_link": "",
            "needs_review": True, "preselected": True,
            "curation_candidates": [{"image_url": url, "status": "preselected", "is_selected": 1, "reasons": reasons,
                                     "evidence": ev, "title": f"p{i}"}]})
    rows = [{"row_number": p["row_number"], "sku_key": p["sku_key"], "status": "ready_for_review", "failure_code": None,
             "product_name": p["product_name"], "brand": p["brand"], "updated_at": "2026-10-03 10:00:00"} for p in products]
    return {"products": products, "queue": {"status": "success", "ready_for_review": len(rows), "rows": rows}}


def test_the_review_screen_in_a_browser(tmp_path):
    script = DRIVER.replace("__PLAYWRIGHT__", json.dumps(PLAYWRIGHT)).replace("__PUBLIC__", json.dumps(str(PUBLIC))) \
        .replace("__FIXTURE__", json.dumps(_fixture())).replace("__PNG__", json.dumps(PNG)) \
        .replace("__CHROME__", json.dumps(CHROME[-1]))
    path = tmp_path / "browser.js"
    path.write_text(script, encoding="utf-8")
    env = dict(os.environ, PLAYWRIGHT_BROWSERS_PATH=BROWSERS)
    res = subprocess.run([NODE, str(path)], capture_output=True, text=True, timeout=240, env=env, encoding="utf-8")
    assert res.returncode == 0, res.stderr[-4000:]
    out = json.loads(next(line for line in res.stdout.splitlines() if line.startswith("__OUT__"))[len("__OUT__"):])

    # bulk: 40 cards drawn, a few on screen; only those (and never a broken picture) are ticked and approved
    assert out["rendered"] == 40 and 0 < out["inView"] < 40
    assert out["broken"] >= 1
    ticked = out["tickedAtLoad"]
    assert 0 < len(ticked) <= out["inView"] and not set(ticked) & {14, 15} and 11 not in ticked
    assert sorted(out["bulkQueued"]) == sorted(ticked)
    assert "ما ظهرت صورتها لك بعد" in out["bulkConfirm"]
    assert len(out["doubleA"]) == 1

    # single: the second Enter of a double tap never approves the next product
    sent, queued, shown = out["doubleEnter"]
    assert sent == ["10"] and queued == [10] and shown != "Product 0"
    assert out["firstExpected"]["queue_row"] == 10
    assert len(out["afterDelay"]) == 2
    assert out["warnConfirm"].startswith("«Product 1»: تأكد قبل الاعتماد")
    assert 11 in out["afterConfirm"] and len(out["afterConfirm"]) == 3

    # a reload changed the open product: blocked until shown again, then it carries what is shown now
    assert out["movedDisabled"] is True and out["movedSent"] == 0
    assert out["afterReopen"] == ["2026-10-03 10:30:00"]
