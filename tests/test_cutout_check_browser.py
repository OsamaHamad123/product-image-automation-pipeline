"""«فحص القص» in a real browser: the page as the Laravel app renders it (through its HTTP kernel, no database), the real
CSS and scripts (theme_preview.js, ui.js, cutout.js, the layout's), the data calls answered by the test (Chromium
through Playwright for node; skipped when php, dashboard/vendor, node Playwright or Chromium is missing).

* every picture on dark, light and checker side by side; a filter shows only its pictures;
* «أعد القص بـPhotoRoom» shows before / after and waits; «اعتمد الجديد» sends the token once and reloads; nothing is
  sent before the owner's click;
* «رجّع القديم» asks first;
* 1440 and 390 px: no console error, no horizontal scroll at 390. Screenshots go to the test's tmp dir (and to
  LAQTA_CUTOUT_SHOTS when set).
"""

import base64
import glob
import io
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

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


def _render(tmp_path):
    """The page as the Laravel app renders it (no database: DB_PORT=1)."""
    compiled = tmp_path / "views"
    compiled.mkdir(exist_ok=True)
    script = f"""<?php
require '{DASH}/vendor/autoload.php';
$app = require '{DASH}/bootstrap/app.php';
$kernel = $app->make(Illuminate\\Contracts\\Http\\Kernel::class);
$request = Illuminate\\Http\\Request::create('/cutout-check', 'GET');
$response = $kernel->handle($request);
echo json_encode(['status' => $response->getStatusCode(), 'body' => $response->getContent()], JSON_UNESCAPED_UNICODE);
"""
    with tempfile.NamedTemporaryFile("w", suffix=".php", delete=False, encoding="utf-8") as fh:
        fh.write(script)
        path = fh.name
    env = dict(os.environ, APP_ENV="testing", APP_KEY="base64:" + "A" * 43 + "=", APP_DEBUG="true", SESSION_DRIVER="array",
               CACHE_STORE="array", LOG_CHANNEL="stderr", VIEW_COMPILED_PATH=str(compiled), DB_CONNECTION="mariadb",
               DB_HOST="127.0.0.1", DB_PORT="1", DB_DATABASE="automation_test_offline")
    try:
        res = subprocess.run([PHP, path], cwd=DASH, env=env, capture_output=True, text=True, timeout=240, encoding="utf-8")
    finally:
        os.unlink(path)
    assert res.returncode == 0, res.stdout[-3000:] + res.stderr[-3000:]
    page = json.loads(res.stdout)
    assert page["status"] == 200
    out = tmp_path / "page.html"
    out.write_text(page["body"], encoding="utf-8")
    return out


def _png(img):
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _bottle(colour, halo=False, side=600):
    img = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if halo:
        d.rounded_rectangle((176, 56, 424, 572), radius=44, fill=(235, 235, 235, 200))
    d.rounded_rectangle((185, 70, 415, 560), radius=36, fill=colour + (255,))
    d.rectangle((265, 28, 335, 90), fill=(120, 120, 120, 255))
    d.rectangle((200, 230, 400, 370), fill=(250, 250, 250, 255))
    d.rectangle((200, 300, 400, 330), fill=(30, 60, 160, 255))
    return img


def _images():
    clean = _bottle((196, 32, 44))
    white = Image.new("RGB", (600, 600), (255, 255, 255))
    white.paste(_bottle((30, 120, 70)), (0, 0), _bottle((30, 120, 70)))
    return {"clean": _png(clean), "white": _png(white), "halo": _png(_bottle((20, 20, 24), halo=True)),
            "new": _png(_bottle((30, 120, 70)))}


CDN = "https://res.cloudinary.com/laqta/image/upload/c_limit,w_1200,f_webp,q_auto/"


def _fixture():
    names = [("Almarai Fresh Milk Full Fat 1L", "Almarai", "white", ["white"]),
             ("Pepsi Black Can 330ml", "Pepsi", "halo", ["dark_halo"]),
             ("Al Ain Water Glass Bottle 750ml", "Al Ain", "clean", ["glass", "photoroom_unsure"]),
             ("Lurpak Butter Unsalted 200g", "Lurpak", "clean", []),
             ("Nido Fortified Milk Powder 900g", "Nestle", "white", ["white", "low_res"]),
             ("Coca-Cola Zero 1.5L", "Coca-Cola", "clean", ["dark_rim"])]
    items = [{"id": 100 + i, "sku_key": f"sku-{i}", "product_name": n, "brand": b, "url": f"{CDN}v170000000{i}/products/x/{kind}{i}",
              "resolved_at": "2026-10-05 10:00:00", "status": "human_approved", "background": "white" if kind == "white" else "transparent",
              "flags": flags, "provider": "photoroom", "checked": True} for i, (n, b, kind, flags) in enumerate(names)]
    counts = {"all": len(items)}
    for item in items:
        for f in item["flags"]:
            counts[f] = counts.get(f, 0) + 1
    replaced = [{"id": 7, "product_name": "Almarai Laban 1L", "brand": "Almarai", "origin": "reprocess", "status": "done",
                 "created_at": "2026-10-06 09:12:00", "old_url": f"{CDN}v1/products/x/white9", "new_url": f"{CDN}v2/products/x/new9",
                 "rows": [14]},
                {"id": 6, "product_name": "Puck Cream 170g", "brand": "Puck", "origin": "gallery", "status": "undone",
                 "created_at": "2026-10-05 18:40:00", "old_url": f"{CDN}v1/products/x/clean8", "new_url": f"{CDN}v2/products/x/halo8",
                 "rows": [22, 23]}]
    gallery = {"status": "success", "items": items, "total": len(items), "counts": counts, "page": 1, "pages": 1,
               "unchecked": 12, "measured": 6, "replaced": replaced,
               "methods": {"photoroom": True, "photoroom_reason": None, "local": False}}
    token = "ab" * 16
    tried = {"status": "success", "token": token, "preview": f"/api/cutout/preview/{token}", "provider": "photoroom",
             "flags": [], "notes": [], "source": "candidate", "paid_calls": 1, "clean": True, "can_apply": True}
    applied = {"status": "success", "new_url": f"{CDN}v1800000000/products/x/new0", "log_id": 8, "rows": [2],
               "busy": [], "sheet": {"written": 1, "pending": 0, "conflict": 0}}
    return {"gallery": gallery, "try": tried, "apply": applied}


DRIVER = r"""
const { chromium } = require(__PLAYWRIGHT__);
const fs = require('fs');
const path = require('path');
const HTML = fs.readFileSync(__HTML__, 'utf8'), PUB = __PUBLIC__, FX = __FIXTURE__, IMG = __IMAGES__, SHOTS = __SHOTS__;
const MIME = { '.css': 'text/css', '.js': 'application/javascript', '.svg': 'image/svg+xml', '.png': 'image/png' };
const out = { errors: {}, posts: [] };
(async () => {
  const browser = await chromium.launch({ executablePath: __CHROME__ });
  async function open(width, height) {
    const ctx = await browser.newContext({ viewport: { width, height }, deviceScaleFactor: 1 });
    const page = await ctx.newPage();
    const errors = out.errors[width] = [];
    page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
    page.on('pageerror', e => errors.push('pageerror: ' + e.message));
    await page.route('**/*', async route => {
      const req = route.request();
      const u = new URL(req.url());
      if (u.hostname === 'res.cloudinary.com') {
        const kind = (u.pathname.match(/(white|halo|clean|new)\d*$/) || [null, 'clean'])[1];
        return route.fulfill({ contentType: 'image/png', body: Buffer.from(IMG[kind], 'base64') });
      }
      if (u.hostname !== 'localhost') return route.fulfill({ contentType: 'text/css', body: '' });
      if (req.resourceType() === 'document') return route.fulfill({ contentType: 'text/html', body: HTML });
      const file = path.join(PUB, u.pathname);
      if (/^\/(css|js)\//.test(u.pathname) && fs.existsSync(file)) {
        return route.fulfill({ contentType: MIME[path.extname(file)] || 'text/plain', body: fs.readFileSync(file) });
      }
      if (u.pathname.startsWith('/api/cutout/preview/')) return route.fulfill({ contentType: 'image/png', body: Buffer.from(IMG.new, 'base64') });
      if (u.pathname === '/api/cutout/gallery') {
        const flag = u.searchParams.get('flag');
        const g = JSON.parse(JSON.stringify(FX.gallery));
        if (flag) { g.items = g.items.filter(i => i.flags.includes(flag)); g.total = g.items.length; g.flag = flag; }
        out.posts.push(['GET', u.pathname + u.search]);
        return route.fulfill({ json: g });
      }
      if (req.method() === 'POST') {
        out.posts.push(['POST', u.pathname, req.postData()]);
        if (u.pathname === '/api/cutout/try') return route.fulfill({ json: FX.try });
        if (u.pathname === '/api/cutout/apply') return route.fulfill({ json: FX.apply });
      }
      return route.fulfill({ json: { status: 'success' } });
    });
    await page.goto('http://localhost/cutout-check');
    await page.waitForSelector('.cq-card[data-id] .cq-stage img');
    await page.waitForFunction(() => [...document.querySelectorAll('.cq-card[data-id]')].slice(0, 1)
      .every(c => [...c.querySelectorAll('img')].every(i => i.complete && i.naturalWidth > 0)));
    await page.waitForTimeout(400);
    return { page, ctx };
  }
  async function shot(page, file, full) {
    if (full) {                                   // the lazy pictures below the fold load on the way down
      const y = await page.evaluate(() => window.scrollY);
      for (let top = 0; top < await page.evaluate(() => document.documentElement.scrollHeight); top += 600) {
        await page.evaluate(t => window.scrollTo(0, t), top);
        await page.waitForTimeout(80);
      }
      await page.waitForFunction(() => [...document.images].every(i => i.complete));
      await page.evaluate(t => window.scrollTo(0, t), y);
    }
    await page.screenshot({ path: path.join(SHOTS, file), fullPage: !!full });
  }

  // 1440: the grid, a filter, a try, the compare view, «اعتمد الجديد»
  {
    const { page } = await open(1440, 900);
    out.cards1440 = await page.evaluate(() => document.querySelectorAll('.cq-card[data-id]').length);
    out.themes = await page.evaluate(() => [...document.querySelectorAll('.cq-card[data-id]')][0]
      .querySelectorAll('.cq-stage').length === 3 && [...[...document.querySelectorAll('.cq-card[data-id]')][0]
      .querySelectorAll('.cq-stage')].map(s => s.dataset.previewTheme));
    out.stageBg = await page.evaluate(() => [...[...document.querySelectorAll('.cq-card[data-id]')][0]
      .querySelectorAll('.cq-stage')].map(s => getComputedStyle(s).backgroundColor));
    out.postsBeforeClick = out.posts.filter(p => p[0] === 'POST').length;
    await shot(page, 'cutout_1440.png');
    await shot(page, 'cutout_1440_full.png', true);
    await page.click('.lq-filter[data-flag="dark_halo"]');
    await page.waitForFunction(() => document.querySelectorAll('.cq-card[data-id]').length === 1);
    out.filtered = await page.evaluate(() => document.querySelector('.cq-card[data-id] .cq-card__name').textContent);
    await page.click('.lq-filter[data-flag="all"]');
    await page.waitForFunction(() => document.querySelectorAll('.cq-card[data-id]').length === 6);
    await page.click('.cq-card[data-id="100"] [data-action="photoroom"]');
    await page.waitForSelector('.cq-card[data-id="100"] .cq-card__new img');
    await page.waitForTimeout(400);
    out.compare = await page.evaluate(() => {
      const card = document.querySelector('.cq-card[data-id="100"]');
      return [card.querySelectorAll('.cq-stage').length, card.querySelector('.cq-card__new .cq-card__msg').textContent];
    });
    out.localDisabled = await page.evaluate(() => document.querySelector('.cq-card[data-id="101"] [data-action="local"]').disabled);
    await page.evaluate(() => document.querySelector('.cq-card[data-id="100"]').scrollIntoView({ block: 'start' }));
    await shot(page, 'cutout_compare_1440.png');
    const before = out.posts.length;
    await page.click('.cq-card[data-id="100"] [data-action="apply"]');
    await page.waitForTimeout(600);
    out.afterApply = out.posts.slice(before).map(p => p[1]);
    // «رجّع القديم» asks first; «لا، رجوع» sends nothing
    await page.click('.cq-log__item[data-id="7"] button');
    await page.waitForSelector('#lqAskConfirm');
    out.ask = await page.evaluate(() => document.getElementById('lqAskTitle').textContent);
    await page.click('#lqAskCancel');
    await page.waitForTimeout(200);
    out.undoSent = out.posts.filter(p => p[1] === '/api/cutout/undo').length;
    await page.close();
  }
  // 390: one column, no horizontal scroll, the compare view still fits
  {
    const { page } = await open(390, 844);
    out.phone = await page.evaluate(() => ({ scrollW: document.documentElement.scrollWidth, innerW: innerWidth,
      columns: getComputedStyle(document.querySelector('.cq__grid')).gridTemplateColumns.split(' ').length,
      stageW: document.querySelector('.cq-stage').getBoundingClientRect().width }));
    await shot(page, 'cutout_390.png');
    await shot(page, 'cutout_390_full.png', true);
    await page.click('.cq-card[data-id="100"] [data-action="photoroom"]');
    await page.waitForSelector('.cq-card[data-id="100"] .cq-card__new img');
    await page.waitForTimeout(400);
    await page.evaluate(() => document.querySelector('.cq-card[data-id="100"] .cq-card__new').scrollIntoView({ block: 'center' }));
    out.phoneAfter = await page.evaluate(() => document.documentElement.scrollWidth);
    await shot(page, 'cutout_compare_390.png');
    await page.close();
  }
  await browser.close();
  console.log('__OUT__' + JSON.stringify(out));
})().catch(e => { console.error(e && e.stack || e); process.exit(3); });
"""


def test_the_gallery_in_a_browser(tmp_path):
    html = _render(tmp_path)
    shots = Path(os.environ.get("LAQTA_CUTOUT_SHOTS") or tmp_path / "shots")
    shots.mkdir(parents=True, exist_ok=True)
    script = DRIVER.replace("__PLAYWRIGHT__", json.dumps(PLAYWRIGHT)).replace("__HTML__", json.dumps(str(html))) \
        .replace("__PUBLIC__", json.dumps(str(PUBLIC))).replace("__FIXTURE__", json.dumps(_fixture())) \
        .replace("__IMAGES__", json.dumps(_images())).replace("__SHOTS__", json.dumps(str(shots))) \
        .replace("__CHROME__", json.dumps(CHROME[-1]))
    path = tmp_path / "browser.js"
    path.write_text(script, encoding="utf-8")
    env = dict(os.environ, PLAYWRIGHT_BROWSERS_PATH=BROWSERS)
    res = subprocess.run([NODE, str(path)], capture_output=True, text=True, timeout=240, env=env, encoding="utf-8")
    assert res.returncode == 0, res.stderr[-4000:]
    out = json.loads(next(line for line in res.stdout.splitlines() if line.startswith("__OUT__"))[len("__OUT__"):])

    assert out["errors"] == {"1440": [], "390": []}
    assert out["cards1440"] == 6 and out["themes"] == ["dark", "light", "checker"]
    assert out["stageBg"][:2] == ["rgb(18, 18, 18)", "rgb(255, 255, 255)"]
    assert out["postsBeforeClick"] == 0                                   # opening the page replaces nothing
    assert out["filtered"] == "Pepsi Black Can 330ml"
    assert out["compare"][0] == 6 and out["compare"][1].startswith("قص PhotoRoom من الصورة الأصلية المحفوظة")
    assert out["localDisabled"] is True
    assert out["afterApply"][0] == "/api/cutout/apply" and "/api/cutout/gallery" in out["afterApply"][1]
    assert out["ask"] == "نرجّع الصورة القديمة؟" and out["undoSent"] == 0
    phone = out["phone"]
    assert phone["scrollW"] <= phone["innerW"] and phone["columns"] == 1 and phone["stageW"] > 80
    assert out["phoneAfter"] <= phone["innerW"]
    for name in ("cutout_1440.png", "cutout_compare_1440.png", "cutout_390.png", "cutout_compare_390.png"):
        assert (shots / name).stat().st_size > 5000, name
