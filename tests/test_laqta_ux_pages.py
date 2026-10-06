"""Home, Run, Settings and the shared helpers of wp/ux under node (the page scripts unchanged, fake DOM pieces):

* the questions are the page's own dialog (Laqta.ask, a Promise) when the layout gives one: the Run controller and
  Settings wait for the answer, send nothing on «لا», and go on after «أكيد» (an auto-publish switch saves itself);
* Home's next step: ready to review → missing brands → not found → a run going → start a run;
* every time is said in the shop's zone (body[data-lq-tz]) whatever the computer's zone; without it, local time.
"""

import json
import os
import subprocess
from pathlib import Path

import pytest

from test_laqta_health import FAKE_SETTINGS_DOM, SETTINGS_JS
from test_laqta_run import COMMON_JS, HARNESS, HOME_JS, NODE, RUN_JS, _js

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")


def _node(script, tz="UTC"):
    env = dict(os.environ, TZ=tz)
    result = subprocess.run([NODE, "-"], input=script, capture_output=True, text=True, timeout=60, encoding="utf-8", env=env)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_the_run_controls_wait_for_the_pages_question():
    out = _node(_js(COMMON_JS, RUN_JS) + HARNESS + r"""
lives = [{ status: 'success', batch: { phase: 'idle', run: {}, ready_for_review: 0 }, run: null, recent: [] }];
let answer = false;
const asked = [];
deps.confirm = t => { asked.push(t); return new Promise(r => setTimeout(() => r(answer), 5)); };
const ctl = LaqtaRunPage.createController(deps);
(async () => {
    const posts = () => fetchLog.filter(f => f.method === 'POST').map(f => f.url);
    Object.assign(ctl.state.form, { scope: 'all', force: true });
    const declined = [await ctl.start(), await ctl.stop(), await ctl.reset(), posts().length];
    answer = true;
    const accepted = [await ctl.start(), await ctl.stop(), await ctl.reset(), posts()];
    console.log(JSON.stringify({ declined, accepted, asked: asked.length }));
})();
""")
    assert out["declined"] == [False, False, False, 0]
    assert out["accepted"][0] is True and out["accepted"][3] == ["/api/run-all", "/api/stop-batch", "/api/batch/reset"]
    assert out["asked"] == 6


def test_the_live_card_offers_the_ready_pictures_while_the_run_goes():
    out = _node(_js(COMMON_JS, RUN_JS) + r"""
const P = window.LaqtaRunPage;
const live = phase => ({ status: 'success', recent: [], run: { current: true, total: 20, processed: 5, counts: {} },
                         batch: { phase: phase, ready_for_review: 4, run: { total: 20, processed: 5 } } });
const running = P.describeLive(live('running'), 1790000000);
const none = P.describeLive({ status: 'success', recent: [], run: null, batch: { phase: 'running', ready_for_review: 0,
                                                                            run: { total: 20, processed: 1 } } }, 1790000000);
const done = P.describeLive({ status: 'success', recent: [], run: { total: 20, processed: 20, counts: { proposed: 4 } },
                              batch: { phase: 'idle', ready_for_review: 4 } }, 1790000000);
console.log(JSON.stringify({ now: running.progress.reviewNow, none: none.progress.reviewNow,
                             finished: [done.finished.reviewLabel, done.finished.reviewHref] }));
""")
    assert out["now"] == {"label": "راجع الجاهز هلق (4)", "href": "/catalog?mode=bulk"}
    assert out["none"] is None
    assert out["finished"] == ["راجع النتائج (4)", "/catalog?mode=bulk"]


def test_settings_wait_for_the_pages_question_then_submit():
    out = _node(FAKE_SETTINGS_DOM + r"""
let dialogAnswer = false;
const dialogAsked = [];
globalThis.Laqta = { ask: t => { dialogAsked.push(t); return Promise.resolve(dialogAnswer); } };
""" + SETTINGS_JS.read_text(encoding="utf-8") + r"""
(async () => {
const out = {};
const tick = () => new Promise(r => setTimeout(r, 0));
clearBox.checked = true;
out.clearNo = keyForm.fire('submit');                             // held while the question is open
await tick();
out.clearNoAfter = [dialogAsked.length, keyForm.submitted];
dialogAnswer = true;
out.clearYes = keyForm.fire('submit');
await tick();
out.clearYesAfter = keyForm.submitted;
out.again = keyForm.fire('submit');                               // the submit after «أكيد» goes through
apSwitch.checked = true;
dialogAnswer = false;
apSwitch.fire('change');
await tick();
out.switchNo = [apSwitch.checked, apForm.submitted];
dialogAnswer = true;
apSwitch.checked = true;
apSwitch.fire('change');
await tick();
out.switchYes = [apSwitch.checked, apForm.submitted, dialogAsked.slice(-1)[0]];
out.nativeAsked = asked.length;                                   // window.confirm never used
console.log(JSON.stringify(out));
})();
""")
    assert out["clearNo"] is True and out["clearNoAfter"] == [1, 0]
    assert out["clearYes"] is True and out["clearYesAfter"] == 1
    assert out["again"] is False
    assert out["switchNo"] == [False, 0]
    assert out["switchYes"] == [True, 1, "ON-TEXT"]                    # saved right after «أكيد», no «حفظ» click
    assert out["nativeAsked"] == 0


def test_home_says_one_next_step():
    out = _node(_js(COMMON_JS, HOME_JS) + r"""
const H = window.LaqtaHomePage;
console.log(JSON.stringify({
    review: H.nextStep(38, null, 5, 'idle'),
    unknown: H.nextStep(null, null, 5, 'idle'),
    waitBrands: H.nextStep(0, null, 5, 'idle'),
    brands: H.nextStep(0, 5, 3, 'idle'),
    brandsDown: H.nextStep(0, false, 3, 'idle'),
    running: H.nextStep(0, 0, 0, 'running'),
    run: H.nextStep(0, 0, 0, 'idle')
}));
""")
    assert out["review"]["title"] == "38 صورة جاهزة للمراجعة." and out["review"]["href"] == "/catalog?mode=bulk"
    assert out["unknown"] is None and out["waitBrands"] is None          # never a guess while a number is unknown
    assert out["brands"]["title"] == "5 ماركات ناقصة من جدول الماركات." and out["brands"]["href"] == "/batch-automation#run-brands"
    assert out["brandsDown"]["key"] == "not_found" and out["brandsDown"]["href"] == "/catalog?filter=not_found"
    assert out["running"]["key"] == "running" and out["run"]["action"] == "ابدأ تشغيل"


DUBAI = r"""
globalThis.document = { body: { getAttribute: n => (n === 'data-lq-tz' ? 'Asia/Dubai' : null) } };
"""


@pytest.mark.parametrize("tz", ["UTC", "America/New_York"])
def test_times_are_said_in_the_shops_zone_whatever_the_computers_zone(tz):
    script = r"""
const C = window.LaqtaRunCommon;
const at = iso => Date.parse(iso) / 1000;
const now = Date.parse('2026-10-06T08:00:00Z');                    // 12:00 in Dubai
console.log(JSON.stringify({
    today: C.whenText(at('2026-10-05T22:30:00Z'), now),            // 02:30 on the 6th in Dubai
    yesterday: C.whenText(at('2026-10-05T19:00:00Z'), now),        // 23:00 on the 5th
    older: C.whenText(at('2026-09-28T05:05:00Z'), now),
    clock: C.clockText(at('2026-10-06T08:00:00Z')),
    date: C.dateLine(new Date('2026-10-05T21:00:00Z')),             // already Tuesday the 6th in Dubai
    hour: C.zoneHour(new Date('2026-10-05T21:00:00Z'))
}));
"""
    dubai = _node(DUBAI + _js(COMMON_JS) + script, tz=tz)
    assert dubai == {"today": "اليوم 02:30", "yesterday": "مبارح 23:00", "older": "28 أيلول 09:05", "clock": "12:00",
                     "date": "الثلاثاء، 6 تشرين الأول", "hour": 1}
    if tz == "UTC":
        local = _node(_js(COMMON_JS) + script, tz=tz)                 # no zone given: the computer's own time
        assert local["today"] == "مبارح 22:30" and local["clock"] == "08:00"


def test_health_times_are_in_the_shops_zone_too():
    health = Path(SETTINGS_JS).with_name("health.js")
    out = _node(DUBAI + "globalThis.window = globalThis;\n" + health.read_text(encoding="utf-8") + r"""
const H = window.LaqtaHealth;
const now = Date.parse('2026-10-06T08:00:00Z');
console.log(JSON.stringify([H.whenText(Date.parse('2026-10-05T22:30:00Z'), now), H.whenText(Date.parse('2026-10-05T19:00:00Z'), now)]));
""", tz="America/New_York")
    assert out == ["اليوم 02:30", "مبارح 23:00"]
