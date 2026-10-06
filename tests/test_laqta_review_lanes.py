"""Lane badge, the «مؤكدة تماماً» filter and the progress line of the review screen.

* every engine pick carries its lane (catalog_match.decide.lane_of, stored as 'lane:<name>' in the candidate's reasons);
  the screen shows «مؤكدة تماماً» (strict, green) or «القارئ مش متأكد» (unsure) on a bulk card and in single mode,
  and nothing for lane other / a plain candidate / a cache hit; the JS reading is the same as decide.lane_of;
* the bulk filter «مؤكدة تماماً» keeps strict picks only;
* the progress line at the top of bulk mode takes the strict lane's numbers from /api/system/review-lanes: accepted,
  reviewed and how many more consecutive accepts reach the lower bound (local_cache_db.more_needed, Wilson).
"""

import json

import pytest

from laqta_review_harness import NODE, run
from test_laqta_review import cand, fixture, product, queue_rows

NEEDS_NODE = pytest.mark.skipif(NODE is None, reason="node is not installed")

STRICT = ["vlm:MATCH", "preselected:vlm_match", "lane:strict", "auto_blocked:auto_publish_disabled"]
UNSURE = ["vlm:UNSURE", "preselected:tier1_unsure", "lane:unsure", "auto_blocked:vlm_not_match"]
OTHER = ["vlm:MATCH", "preselected:vlm_match", "lane:other", "auto_blocked:not_tier1"]


def picked(row, name, reasons, **extra):
    return product(row, name, curation_candidates=[
        cand(f"https://www.luluhypermarket.com/p{row}.jpg", "preselected", 1, reasons=list(reasons), evidence={"size": "match"}, **extra)],
        needs_review=True, preselected=True)


S1 = picked(51, "ALALI STRICT TUNA 170GM", STRICT)
S2 = picked(52, "ALALI STRICT TUNA 85GM", STRICT)
U1 = picked(53, "ALALI UNSURE TUNA 170GM", UNSURE)
O1 = picked(54, "ALALI OTHER TUNA 170GM", OTHER)
PLAIN = product(55, "ALALI PLAIN TUNA 170GM", curation_candidates=[
    cand("https://www.noon.com/p55.jpg", "preselected", 1, reasons=["vlm:MATCH"])], needs_review=True, preselected=True)
LANES = [S1, S2, U1, O1, PLAIN]


def lanes_fx(strict=None, products=LANES):
    fx = fixture(products=products, rows=queue_rows(products))
    if strict is not None:
        fx["lanes"] = {"status": "success", "lanes": {"strict": strict, "unsure": {}, "other": {}}, "unlaned": 0}
    return fx


def page(scenario, tmp_path, fx, config=None):
    return run(f"await boot({json.dumps(config or {})});\n" + scenario, tmp_path, fx)


@NEEDS_NODE
def test_a_bulk_card_shows_the_lane_of_its_pick(tmp_path):
    out = page(r"""
R.setMode('bulk');
await flush();
out.cards = document.querySelectorAll('.rv-card').map(c => [c.querySelector('.rv-card__name').textContent,
    c.querySelectorAll('.rv-lane').map(b => [b.textContent, b.getAttribute('data-lane'), b.classList.contains('rv-lane--strict')])]);
""", tmp_path, lanes_fx())
    badges = {name: b for name, b in out["cards"]}
    assert badges["ALALI STRICT TUNA 170GM"] == [["مؤكدة تماماً", "strict", True]]
    assert badges["ALALI STRICT TUNA 85GM"] == [["مؤكدة تماماً", "strict", True]]
    assert badges["ALALI UNSURE TUNA 170GM"] == [["القارئ مش متأكد", "unsure", False]]
    assert badges["ALALI OTHER TUNA 170GM"] == []                # lane other: no badge
    assert badges["ALALI PLAIN TUNA 170GM"] == []                # not stored with a lane / preselected reason


@NEEDS_NODE
def test_single_mode_shows_the_lane_of_the_system_pick(tmp_path):
    out = page(r"""
const badge = () => ws().querySelectorAll('.rv-pick .rv-lane').map(b => b.textContent);
openRow(51);
out.strict = badge();
openRow(53);
out.unsure = badge();
openRow(54);
out.other = badge();
openRow(55);
out.plain = badge();
""", tmp_path, lanes_fx())
    assert out["strict"] == ["مؤكدة تماماً"] and out["unsure"] == ["القارئ مش متأكد"]
    assert out["other"] == [] and out["plain"] == []


@NEEDS_NODE
def test_the_lane_reading_matches_decide_lane_of(tmp_path):
    """The screen reads a candidate's lane like catalog_match.decide.lane_of, also for picks stored before the lanes."""
    from catalog_match.decide import lane_of

    cases = [
        STRICT, UNSURE, OTHER,
        ["preselected:vlm_match"],                                                       # stored before the lanes
        ["preselected:vlm_match", "auto_blocked:auto_publish_disabled"],
        ["preselected:vlm_match", "auto_blocked:auto_publish_off_for_brand", "auto_blocked:brand_conf_0.62"],
        ["preselected:vlm_match", "auto_blocked:not_tier1"],
        ["preselected:tier1_unsure", "auto_blocked:auto_publish_disabled"],              # tier1_unsure is strict if only the setting blocks
        ["preselected:tier1_unsure", "auto_blocked:vlm_not_match"],
        ["preselected:vlm_match", "auto_blocked:auto_publish_disabled", "warn:foreign_store"],   # a warning keeps it out of strict
        ["preselected:tier2_corroborated", "auto_blocked:not_tier1"],
        ["vlm:MATCH"],                                                                   # not the engine's pick
        [],
    ]
    expected = [lane_of(r) for r in cases]
    out = run(f"""
const cases = {json.dumps(cases)};
out.lanes = cases.map(reasons => R.laneOf(R.normalizeCandidate({{ status: 'preselected', is_selected: 1, reasons: reasons }})));
const base = {{ status: 'preselected', is_selected: 1, reasons: {json.dumps(STRICT)} }};
out.cache = [R.laneOf(R.normalizeCandidate(Object.assign({{}}, base, {{ evidence: {{ source: 'cache' }} }}))),
             R.laneOf(R.normalizeCandidate(Object.assign({{}}, base, {{ reasons: base.reasons.concat(['cache_hit']) }}))),
             R.laneOf(R.normalizeCandidate(Object.assign({{}}, base, {{ status: 'eligible' }}))),
             R.laneOf(R.normalizeCandidate(Object.assign({{}}, base, {{ is_selected: 0 }}))),
             R.laneOf(null)];
// the screen's own warnings (a sheet size nobody confirmed) take a pick out of strict too
out.warned = R.laneOf(R.normalizeCandidate(Object.assign({{}}, base, {{ warnings: ['size_unverified'] }})));
""", tmp_path, fixture(products=[]))
    assert out["lanes"] == expected == ["strict", "unsure", "other", "strict", "strict", "strict", "other", "strict", "unsure",
                                        "other", "other", None, None]
    assert out["cache"] == [None, None, None, None, None] and out["warned"] == "other"


def test_the_payload_keeps_the_lane_of_the_pick():
    """The search response's candidates keep their reasons, so the screen reads the lane from the data it already gets;
    the response's own lane (what the catalog page sends with the decision) comes from the same reasons."""
    import cli_bridge

    candidates = cli_bridge._candidates_for_response({"candidates": [
        {"url": "https://a.ae/1.jpg", "status": "preselected", "reasons": list(STRICT)},
        {"url": "https://a.ae/2.jpg", "status": "eligible", "reasons": ["vlm:UNSURE"]}]}, {})
    assert "lane:strict" in candidates[0]["reasons"] and not any(r.startswith("lane:") for r in candidates[1]["reasons"])
    assert cli_bridge._pick_lane(candidates) == "strict"
    unsure = cli_bridge._candidates_for_response({"candidates": [
        {"url": "https://a.ae/1.jpg", "status": "preselected", "reasons": list(UNSURE)}]}, {})
    assert cli_bridge._pick_lane(unsure) == "unsure"


@NEEDS_NODE
def test_the_strict_filter_keeps_only_strict_picks(tmp_path):
    out = page(r"""
R.setMode('bulk');
await flush();
const filters = () => document.querySelectorAll('.rv-bulk__filters .lq-filter').map(b => [b.querySelector('span').textContent,
    b.querySelector('.lq-filter__count').textContent]);
const names = () => document.querySelectorAll('.rv-card').map(c => c.querySelector('.rv-card__name').textContent);
out.filters = filters();
const strictBtn = () => document.querySelectorAll('.rv-bulk__filters .lq-filter').find(b => b.getAttribute('data-bulk-filter') === 'strict');
strictBtn().click();
await flush();
out.strict = names();
out.pressed = strictBtn().getAttribute('aria-pressed');
out.cards = document.querySelectorAll('.rv-card').map(c => c.getAttribute('data-kind'));
""", tmp_path, lanes_fx())
    # «مؤكدة تماماً» sits right after «مقترحة بلا تحذير»
    assert [f[0] for f in out["filters"]] == ["الكل", "مقترحة بلا تحذير", "مؤكدة تماماً", "فيها تحذير", "بلا اقتراح"]
    assert dict(out["filters"])["مؤكدة تماماً"] == "2"
    assert sorted(out["strict"]) == ["ALALI STRICT TUNA 170GM", "ALALI STRICT TUNA 85GM"]
    assert out["pressed"] == "true" and out["cards"] == ["eligible", "eligible"]


# ---------------------------------------------------------------------------
# The progress line
# ---------------------------------------------------------------------------

HEAD = "لحتى ينفتح النشر التلقائي لكل الماركات المؤكدة: "


@NEEDS_NODE
def test_the_progress_line_says_how_far_the_strict_lane_is(tmp_path):
    strict = {"prechecked": 40, "accepted": 39, "lower_bound": 0.87, "ready": False, "more_needed": 150}
    out = page(r"""
R.setMode('bulk');
await flush();
const line = document.querySelector('.rv-bulk__lane');
out.text = line.textContent;
out.hidden = line.hidden;
out.calls = requests('/api/system/review-lanes').length;
R.bulk.render();
R.bulk.render();
await flush();
out.calls_after = requests('/api/system/review-lanes').length;
""", tmp_path, lanes_fx(strict))
    assert out["text"] == HEAD + "اعتمدت 39 من 40 اقتراح مؤكد تماماً، وبعد 150 اعتماد متتالي بلا رفض"
    assert out["hidden"] is False and out["calls"] == 1 and out["calls_after"] == 1      # one cached call, not one per redraw


@NEEDS_NODE
def test_the_progress_line_when_the_lane_is_ready_or_unknown(tmp_path):
    ready = {"prechecked": 189, "accepted": 189, "lower_bound": 0.98, "ready": True, "more_needed": 0}
    out = page(r"""
R.setMode('bulk');
await flush();
const line = document.querySelector('.rv-bulk__lane');
out.text = line.textContent;
out.ready_class = line.classList.contains('is-ready');
""", tmp_path, lanes_fx(ready))
    assert out["text"] == "النشر التلقائي جاهز للتشغيل من الإعدادات ← النشر الآلي" and out["ready_class"] is True

    # no numbers (the call failed, or the bridge sent none): no line rather than an invented figure
    down = page(r"""
R.setMode('bulk');
await flush();
out.hidden = document.querySelector('.rv-bulk__lane').hidden;
out.text = document.querySelector('.rv-bulk__lane').textContent;
""", tmp_path, lanes_fx(None))
    assert down["hidden"] is True and down["text"] == ""

    out = run(r"""
out.cases = [R.bulk.laneLine({ prechecked: 0, accepted: 0, ready: false, more_needed: 189 }),
             R.bulk.laneLine({ prechecked: 100, accepted: 50, ready: false, more_needed: null }),
             R.bulk.laneLine(null), R.bulk.laneLine({})];
""", tmp_path, fixture(products=[]))
    assert out["cases"] == [HEAD + "اعتمدت 0 من 0 اقتراح مؤكد تماماً، وبعد 189 اعتماد متتالي بلا رفض",
                            HEAD + "اعتمدت 50 من 100 اقتراح مؤكد تماماً، بس دقتها هلق أقل من الحد المطلوب", None, None]


@NEEDS_NODE
def test_the_progress_line_is_not_asked_for_in_single_mode(tmp_path):
    out = page(r"""
out.calls = requests('/api/system/review-lanes').length;
""", tmp_path, lanes_fx({"prechecked": 1, "accepted": 1, "ready": False, "more_needed": 188}))
    assert out["calls"] == 0


# ---------------------------------------------------------------------------
# «وبعد Z»: the Wilson arithmetic behind the line (local_cache_db.more_needed)
# ---------------------------------------------------------------------------

def _brute(prechecked, accepted):
    """The definition: the fewest further accepted picks after which the Wilson lower bound reaches the threshold."""
    import local_cache_db as db

    for k in range(0, 5000):
        n = prechecked + k
        if n >= db.AUTO_PUBLISH_MIN_REVIEWED and db.wilson_lower_bound(accepted + k, n) >= db.AUTO_PUBLISH_MIN_LOWER_BOUND:
            return k
    return None


def test_more_needed_at_the_boundaries(offline):
    import local_cache_db as db

    assert db.more_needed(0, 0) == 189 == db.reviews_needed(0, 0)       # no reviews yet: the perfect-record count
    assert db.more_needed(189, 189) == 0 and db.more_needed(300, 300) == 0     # all accepted and over the line
    assert db.more_needed(188, 188) == 1                                # one short of the perfect record
    assert db.more_needed(100, 100) == 89
    assert db.more_needed(30, 30) == 159                                # the minimum count alone is not enough
    one_reject = db.more_needed(189, 188)                               # one rejection: the line moves out
    assert one_reject == _brute(189, 188) and one_reject > 0
    assert db.more_needed(10, 9) > db.more_needed(10, 10)              # a rejection pushes the line out
    for prechecked, accepted in [(0, 0), (1, 1), (29, 29), (30, 30), (50, 49), (120, 118), (189, 188), (200, 199), (400, 396)]:
        assert db.more_needed(prechecked, accepted) == _brute(prechecked, accepted), (prechecked, accepted)
    assert db.more_needed(5000, 2500) is None                           # accuracy far under the threshold: no near path


def test_the_lane_rows_carry_more_needed(offline):
    import local_cache_db as db

    def rows(plan):
        out, n = [], 0
        for sku, (action, lane) in enumerate(plan):
            n += 1
            out.append({"id": n, "created_at": f"2026-09-30 10:{n // 60 % 60:02d}:{n % 60:02d}", "action": action,
                        "sku_key": f"sku-{sku}", "brand": "ALMARAI", "image_url": f"https://img.ae/{sku}.jpg", "was_preselected": 1,
                        "engine_decision": "REVIEW_PRESELECTED", "reason_code": None if action == "approved" else "WRONG_SIZE",
                        "lane": lane})
        return out

    none = db.review_stats([])["lanes"]["strict"]
    assert (none["prechecked"], none["accepted"], none["more_needed"], none["ready"]) == (0, 0, 189, False)
    full = db.review_stats(rows([("approved", "strict")] * 189))["lanes"]["strict"]
    assert (full["prechecked"], full["accepted"], full["more_needed"], full["ready"]) == (189, 189, 0, True)
    one = db.review_stats(rows([("approved", "strict")] * 188 + [("rejected", "strict")]))["lanes"]["strict"]
    assert (one["prechecked"], one["accepted"], one["ready"]) == (189, 188, False)
    assert one["more_needed"] == _brute(189, 188) > 0
    other = db.review_stats(rows([("approved", "strict")] * 5 + [("approved", "unsure")] * 3))["lanes"]
    assert other["strict"]["more_needed"] == 184 and other["unsure"]["more_needed"] == 186     # each lane on its own count
