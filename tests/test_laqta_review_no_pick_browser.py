"""A «بلا اقتراح» product in a real browser (Chromium through Playwright for node; skipped when either is missing).

The page is the review scripts and stylesheets with the data calls answered by the test, at the size of an office
screen (1366 x 768). Failed before the change: the product showed a big empty panel, its images below the fold and
no reason. Now the reason sentence is on screen, the first row of images and why each was not chosen are visible
without scrolling (above the action bar) and nothing is pre-selected, so Enter approves nothing until the reviewer picks one (1–9); the pick then loads on screen and Enter
approves exactly that image. A screenshot of each state is written next to the test's files.
"""

import json
import os
import subprocess

from test_laqta_review_browser import BROWSERS, CHROME, NODE, PLAYWRIGHT, PNG, PUBLIC, pytestmark  # noqa: F401

REASON = {"key": "no_size", "label": "حجم ناقص بالشيت", "engine": "unsure",
          "text": "الشيت ما فيه حجم ولا باركود لهالمنتج، فما في صورة قدرنا نتأكد إنها نفس العبوة، وقارئ الملصق ما تأكد "
                  "من 4 صور. أضف الحجم في الشيت ثم أعد البحث، أو اختر من الصور تحت.",
          "sheet": [{"key": "no_size", "text": "الحجم ناقص بالشيت"}, {"key": "no_barcode", "text": "الباركود ناقص بالشيت"}]}

DRIVER = r"""
const { chromium } = require(__PLAYWRIGHT__);
const fs = require('fs');
const D = __PUBLIC__;
const scripts = ['core', 'ui', 'jobs', 'single', 'bulk', 'app'].map(n => fs.readFileSync(`${D}/js/review/${n}.js`, 'utf8'));
const css = fs.readFileSync(`${D}/css/laqta.css`, 'utf8') + '\n' + fs.readFileSync(`${D}/css/pages/review.css`, 'utf8');
const FX = __FIXTURE__;
const PNG = Buffer.from(__PNG__, 'base64');
const SHOTS = __SHOTS__;
const cfg = { mode: 'single', filter: 'all', row: 50, canvas: 800, db: 'online', autoSearchDelayMs: 0, urls: {} };
const html = `<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8"><meta name="csrf-token" content="t"><style>${css}</style></head>
<body class="page-review"><main class="lq-main lq-main--flush"><div id="rvApp" class="rv-app" data-config='${JSON.stringify(cfg)}'></div></main>
<script>window.confirm=()=>true;window.Laqta={toast(){return {close(){},update(){}}},onRunStatus(){}};</script>
${scripts.map(s => `<script>${s}</script>`).join('\n')}</body></html>`;
const out = {};
(async () => {
  const browser = await chromium.launch({ executablePath: __CHROME__ });
  const page = await browser.newPage({ viewport: { width: 1366, height: 768 } });
  const selects = [];
  await page.route('**/*', async route => {
    const u = new URL(route.request().url());
    if (u.pathname === '/') return route.fulfill({ contentType: 'text/html', body: html });
    if (u.pathname === '/api/products-json') return route.fulfill({ json: { status: 'success', products: FX.products } });
    if (u.pathname === '/api/review/queue-state') return route.fulfill({ json: FX.queue });
    if (u.pathname === '/api/select_image') { selects.push(JSON.parse(route.request().postData())); return; }   // held
    if (u.pathname === '/api/image-proxy') return route.fulfill({ contentType: 'image/png', body: PNG });
    return route.fulfill({ json: { status: 'success' } });
  });
  await page.goto('http://localhost/');
  await page.waitForSelector('#rvNoPickGrid .rv-alt img');
  await page.waitForTimeout(500);
  out.reason = await page.evaluate(() => {
    const b = document.querySelector('.rv-nopick');
    const r = b.getBoundingClientRect();
    return { text: b.innerText, title: b.getAttribute('title'), onScreen: r.top >= 0 && r.bottom <= innerHeight };
  });
  // what the reviewer sees without scrolling: above the action bar fixed at the bottom
  out.grid = await page.evaluate(() => {
    const fold = Math.min(innerHeight, document.getElementById('rvActionbar').getBoundingClientRect().top);
    const seen = el => { const r = el.getBoundingClientRect(); return r.top >= 0 && r.bottom <= fold; };
    return [...document.querySelectorAll('#rvNoPickGrid .rv-alt')].map(n => ({
      top: Math.round(n.getBoundingClientRect().top), thumbSeen: seen(n.querySelector('.rv-alt__thumb')),
      whySeen: !!n.querySelector('.rv-alt__why') && seen(n.querySelector('.rv-alt__why')),
      why: (n.querySelector('.rv-alt__why') || {}).innerText || '', pressed: n.getAttribute('aria-pressed') }));
  });
  out.emptyPanel = await page.evaluate(() => !!document.querySelector('.rv-pick--empty, .rv-pick'));
  out.scrollY = await page.evaluate(() => window.scrollY);
  await page.screenshot({ path: SHOTS + '/no_pick_before.png' });
  out.approveBefore = await page.evaluate(() => document.getElementById('rvApprove').disabled);
  await page.keyboard.press('Enter');
  await page.waitForTimeout(200);
  out.sentBefore = selects.length;
  await page.keyboard.press('2');
  await page.waitForSelector('.rv-pick img');
  await page.waitForTimeout(700);
  out.picked = await page.evaluate(() => [document.querySelector('.rv-pick').getAttribute('data-url'),
                                          document.getElementById('rvApprove').disabled]);
  await page.screenshot({ path: SHOTS + '/no_pick_picked.png' });
  await page.keyboard.press('Enter');
  await page.waitForTimeout(300);
  out.sent = selects.map(s => s.image_url);
  await browser.close();
  console.log('__OUT__' + JSON.stringify(out));
})().catch(e => { console.error(e && e.stack || e); process.exit(3); });
"""


def _fixture():
    cands = []
    for i, (decision, tier, size) in enumerate([("UNSURE", 2, "unknown"), ("UNSURE", 2, "unknown"), (None, 3, "unknown"),
                                                ("UNSURE", 2, "match"), (None, 2, "unknown"), ("UNSURE", 2, "unknown")]):
        cands.append({"image_url": f"https://www.carrefouruae.com/np-{i}.png", "status": "eligible", "is_selected": 0,
                      "title": f"Mr John Fries {i}", "identity_tier": str(tier),
                      "reasons": [f"vlm:{decision}"] if decision else [],
                      "evidence": {"tier": tier, "brand": tier < 3, "size": size},
                      "vlm": {"decision": decision} if decision else None})
    product = {"row_number": 50, "product_name": "MR JOHN FRENCH FRIES", "brand": "MR JOHN", "barcode": "",
               "sku_key": "key-50", "size": "", "product_name_ar": "", "brand_ar": "", "category": "Frozen > Fries",
               "existing_image_link": "", "needs_review": True, "curation_candidates": cands}
    rows = [{"row_number": 50, "sku_key": "key-50", "status": "ready_for_review", "failure_code": None,
             "product_name": product["product_name"], "brand": "MR JOHN", "updated_at": "2026-10-04 09:00:00",
             "explain": REASON}]
    return {"products": [product], "queue": {"status": "success", "ready_for_review": 1, "rows": rows}}


def test_a_product_without_a_pick_in_a_browser(tmp_path):
    shots = tmp_path / "shots"
    shots.mkdir()
    script = DRIVER.replace("__PLAYWRIGHT__", json.dumps(PLAYWRIGHT)).replace("__PUBLIC__", json.dumps(str(PUBLIC))) \
        .replace("__FIXTURE__", json.dumps(_fixture(), ensure_ascii=False)).replace("__PNG__", json.dumps(PNG)) \
        .replace("__CHROME__", json.dumps(CHROME[-1])).replace("__SHOTS__", json.dumps(str(shots)))
    path = tmp_path / "browser.js"
    path.write_text(script, encoding="utf-8")
    env = dict(os.environ, PLAYWRIGHT_BROWSERS_PATH=BROWSERS)
    res = subprocess.run([NODE, str(path)], capture_output=True, text=True, timeout=240, env=env, encoding="utf-8")
    assert res.returncode == 0, res.stderr[-4000:]
    out = json.loads(next(line for line in res.stdout.splitlines() if line.startswith("__OUT__"))[len("__OUT__"):])

    assert REASON["text"] in out["reason"]["text"] and out["reason"]["onScreen"] is True
    assert out["reason"]["title"] == "no_size · unsure"
    assert out["scrollY"] == 0 and not out["emptyPanel"]
    # no scrolling needed: the first row of images (five at this width) and why each was not chosen are on screen
    first_row = [c for c in out["grid"] if c["top"] == out["grid"][0]["top"]]
    assert len(out["grid"]) == 6 and len(first_row) == 5, out["grid"]
    assert all(c["thumbSeen"] and c["whySeen"] for c in first_row), out["grid"]
    assert all(c["pressed"] == "false" for c in out["grid"])                                 # nothing pre-selected
    assert out["grid"][0]["why"] == "لماذا لم تُختر: قارئ الملصق ما تأكد، والشيت ما فيه حجم"
    assert out["approveBefore"] is True and out["sentBefore"] == 0
    assert out["picked"] == ["https://www.carrefouruae.com/np-1.png", False]
    assert out["sent"] == ["https://www.carrefouruae.com/np-1.png"]
    assert (shots / "no_pick_before.png").stat().st_size > 1000
