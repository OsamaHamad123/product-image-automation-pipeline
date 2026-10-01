"""Reviewer evidence for auto-publish: the pure stats over review_decisions rows, the Wilson bound,
scripts/review_stats.py (read-only) and the auto-publish tab of Settings (was the active-learning page) that
shows them.

Everything here is offline: the stats are pure functions and the script reads through a fake
connection. The DB round trip and the bridge writes are in test_review_decisions.py.
"""

import importlib.util
import json
import random
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DASH = ROOT / "dashboard"
PAGE = DASH / "resources" / "views" / "settings" / "auto_publish.blade.php"
CONTROLLER = DASH / "app" / "Http" / "Controllers" / "SettingsController.php"
SETTINGS_JS = DASH / "public" / "js" / "settings.js"
LIVE_ROWS = ROOT / "tests" / "catalog_match" / "fixtures" / "live_rows_2026_09_30.json"
NODE = shutil.which("node")

pytestmark = pytest.mark.usefixtures("offline")


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module          # dataclasses resolve their module through sys.modules
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def ldb(offline):
    import local_cache_db
    return local_cache_db


class Rows:
    """Builds review_decisions rows in time order (ids and created_at increase)."""

    def __init__(self):
        self.rows = []

    def add(self, action, sku, brand, url=None, pre=None, decision=None, reason=None, domain=None):
        n = len(self.rows) + 1
        self.rows.append({"id": n, "created_at": f"2026-09-30 {n // 3600:02d}:{n // 60 % 60:02d}:{n % 60:02d}",
                          "action": action, "sku_key": sku, "brand": brand, "image_url": url,
                          "was_preselected": pre, "engine_decision": decision, "reason_code": reason,
                          "page_domain": domain})
        return self

    def accepted(self, sku, brand, domain="luluhypermarket.com"):
        return self.add("approved", sku, brand, f"https://img.ae/{sku}.jpg", 1, "REVIEW_PRESELECTED", domain=domain)

    def refused(self, sku, brand, reason="WRONG_PRODUCT", domain="noon.com"):
        return self.add("rejected", sku, brand, f"https://img.ae/{sku}.jpg", 1, "REVIEW_PRESELECTED", reason, domain)


# ---------------------------------------------------------------------------
# Wilson bound and the readiness rule
# ---------------------------------------------------------------------------

def test_wilson_matches_the_eval_metric(ldb):
    """Production reimplements tests/eval/metrics.wilson_lower_bound; both must give the same numbers."""
    metrics = _load("eval_metrics_for_review_stats", ROOT / "tests" / "eval" / "metrics.py")
    for n in (1, 2, 5, 29, 30, 31, 60, 188, 189, 500):
        for successes in sorted({0, 1, n // 2, n - 1, n} - {-1}):
            assert ldb.wilson_lower_bound(successes, n) == pytest.approx(metrics.wilson_lower_bound(successes, n),
                                                                       abs=1e-12), (successes, n)
    assert ldb.wilson_lower_bound(0, 0) is None and metrics.wilson_lower_bound(0, 0) is None


def test_wilson_known_values(ldb):
    # 3/4 worked out by hand in tests/eval/test_eval_metrics.py: (0.75 + 0.4802 - 0.64083) / 1.9604
    assert ldb.wilson_lower_bound(3, 4) == pytest.approx(0.3006, abs=1e-4)
    # a perfect record of 30 is far from 98%: the bound is n / (n + z^2)
    assert ldb.wilson_lower_bound(30, 30) == pytest.approx(30 / (30 + 1.96 ** 2))
    assert ldb.wilson_lower_bound(188, 188) < 0.98 <= ldb.wilson_lower_bound(189, 189)


def test_readiness_constants_live_in_one_place(ldb):
    assert ldb.AUTO_PUBLISH_MIN_REVIEWED == 30
    assert ldb.AUTO_PUBLISH_MIN_LOWER_BOUND == 0.98
    stats = ldb.review_stats([])
    assert stats["thresholds"] == {"min_reviewed": 30, "min_lower_bound": 0.98, "perfect_record_reviews": 189}


@pytest.mark.parametrize("prechecked, accepted, status", [
    (189, 189, "ready"),
    (600, 597, "ready"),               # 99.5% on a large sample
    (30, 30, "needs_reviews"),         # 100% but the bound is 0.886: more reviews, not "low precision"
    (188, 188, "needs_reviews"),
    (30, 29, "low_precision"),         # 96.7% < 98%
    (200, 195, "low_precision"),
    (10, 5, "needs_reviews"),          # below the minimum count
    (0, 0, "needs_reviews"),
])
def test_brand_status(ldb, prechecked, accepted, status):
    assert ldb.brand_status(prechecked, accepted) == status


def test_reviews_needed(ldb):
    assert ldb.reviews_needed(0, 0) == 189          # a perfect record from the start
    assert ldb.reviews_needed(12, 12) == 189
    assert ldb.reviews_needed(200, 200) == 200      # already ready: no more needed
    assert ldb.reviews_needed(10, 9) == 280         # one wrong pre-check costs ~90 more accepted ones
    needed = ldb.reviews_needed(10, 9)
    assert ldb.wilson_lower_bound(needed - 1, needed) >= 0.98 > ldb.wilson_lower_bound(needed - 2, needed - 1)
    assert ldb.reviews_needed(3000, 1000) is None   # out of reach


# ---------------------------------------------------------------------------
# One verdict per SKU on the engine's pre-check
# ---------------------------------------------------------------------------

def _verdict(ldb, rows):
    return ldb._precheck_verdict(rows.rows)


def test_verdict_approved_precheck_is_accepted(ldb):
    assert _verdict(ldb, Rows().accepted("s", "AIDA")) is True


def test_verdict_rejected_precheck_is_refused(ldb):
    assert _verdict(ldb, Rows().refused("s", "AIDA")) is False


def test_verdict_another_image_or_upload_while_a_precheck_existed_is_refused(ldb):
    other = Rows().add("approved", "s", "AIDA", "https://img.ae/other.jpg", 0, "REVIEW_PRESELECTED")
    assert _verdict(ldb, other) is False
    upload = Rows().add("manual_upload", "s", "AIDA", None, 0, "REVIEW_PRESELECTED")
    assert _verdict(ldb, upload) is False


def test_verdict_is_none_without_a_precheck_or_when_unknown(ldb):
    unselected = Rows().add("approved", "s", "AIDA", "https://img.ae/1.jpg", 0, "REVIEW_UNSELECTED")
    assert _verdict(ldb, unselected) is None
    # catalog page: live search, nothing stored, so the bridge could not tell what was pre-checked
    unknown = Rows().add("approved", "s", "AIDA", "https://img.ae/1.jpg", None, None)
    assert _verdict(ldb, unknown) is None
    sibling_reject = Rows().add("rejected", "s", "AIDA", "https://img.ae/2.jpg", 0, "REVIEW_PRESELECTED", "WRONG_SIZE")
    assert _verdict(ldb, sibling_reject) is None


def test_verdict_reject_then_research_then_approve_is_refused(ldb):
    """Auto-publish gets one shot: the first pre-check was wrong even if the re-search found the right one."""
    rows = Rows().refused("s", "AIDA").add("approved", "s", "AIDA", "https://img.ae/second.jpg", 1,
                                           "REVIEW_PRESELECTED")
    assert _verdict(ldb, rows) is False


def test_verdict_later_reject_of_the_approved_precheck_is_refused(ldb):
    rows = Rows().accepted("s", "AIDA").add("rejected", "s", "AIDA", "https://img.ae/s.jpg", None, None, "WRONG_PACK")
    assert _verdict(ldb, rows) is False


def test_verdict_rejecting_a_sibling_then_approving_the_precheck_is_accepted(ldb):
    rows = Rows().add("rejected", "s", "AIDA", "https://img.ae/sib.jpg", 0, "REVIEW_PRESELECTED", "WRONG_VARIANT")
    assert _verdict(ldb, rows.accepted("s", "AIDA")) is True


def test_verdict_rejected_auto_publish_is_refused(ldb):
    rows = Rows().add("rejected", "s", "AIDA", "https://res.cloudinary.com/x.png", 1, "AUTO_PUBLISH", "WRONG_PRODUCT")
    assert _verdict(ldb, rows) is False


def test_verdict_uses_the_decision_of_each_row(ldb):
    """An approval made when nothing was pre-checked is not held against a later pre-check."""
    rows = Rows().add("approved", "s", "AIDA", "https://img.ae/a.jpg", 0, "REVIEW_UNSELECTED").accepted("s", "AIDA")
    assert _verdict(ldb, rows) is True


# ---------------------------------------------------------------------------
# review_stats on the live-run corpus
# ---------------------------------------------------------------------------

def _live_rows():
    return json.loads(LIVE_ROWS.read_text(encoding="utf-8"))["rows"]


def _corpus_rows():
    """Reviewer decisions over the 60 live sheet rows: every AL ALALI pre-check approved, one VIRGINIA
    pre-check rejected for its size, every other brand approved once with no pre-check shown."""
    rows = Rows()
    for r in _live_rows():
        sku = f"live-{r['row']}"
        if r["brand"] == "AL ALALI":
            rows.accepted(sku, r["brand"])
        elif r["brand"] == "VIRGINIA" and r["row"] == min(x["row"] for x in _live_rows() if x["brand"] == "VIRGINIA"):
            rows.refused(sku, r["brand"], "WRONG_SIZE")
            rows.add("approved", sku, r["brand"], f"https://img.ae/{sku}-b.jpg", 0, "REVIEW_UNSELECTED",
                     domain="carrefouruae.com")
        elif r["brand"] == "VIRGINIA":
            rows.accepted(sku, r["brand"], domain="carrefouruae.com")
        else:
            rows.add("approved", sku, r["brand"], f"https://img.ae/{sku}.jpg", 0, "REVIEW_UNSELECTED",
                     domain="luluhypermarket.com")
    return rows.rows


def test_stats_per_brand_on_the_live_corpus(ldb):
    stats = ldb.review_stats(_corpus_rows())
    brands = {b["brand"]: b for b in stats["brands"]}
    live = _live_rows()
    assert sum(b["reviewed_skus"] for b in stats["brands"]) == len(live) == stats["overall"]["reviewed_skus"]

    alali = brands["AL ALALI"]
    assert (alali["reviewed_skus"], alali["prechecked"], alali["accepted"]) == (6, 6, 6)
    assert alali["precision"] == 1.0
    assert alali["lower_bound"] == pytest.approx(ldb.wilson_lower_bound(6, 6))
    assert alali["status"] == "needs_reviews" and alali["reviews_needed"] == 189

    virginia = brands["VIRGINIA"]
    assert (virginia["reviewed_skus"], virginia["prechecked"], virginia["accepted"]) == (4, 4, 3)
    assert virginia["precision"] == 0.75
    assert virginia["top_reject_reasons"] == [("WRONG_SIZE", 1)]

    mccain = brands["MCCAIN"]
    assert (mccain["reviewed_skus"], mccain["prechecked"], mccain["accepted"]) == (2, 0, 0)
    assert mccain["precision"] is None and mccain["lower_bound"] is None

    # brands with pre-check evidence come first
    assert [b["brand"] for b in stats["brands"][:2]] == ["AL ALALI", "VIRGINIA"]
    overall = stats["overall"]
    assert overall["actions"] == len(live) + 1
    assert (overall["approved"], overall["rejected"], overall["manual_upload"]) == (len(live), 1, 0)
    assert (overall["prechecked"], overall["accepted"]) == (10, 9)
    assert stats["ready_brands"] == [] and stats["suggested_auto_publish_brands"] == ""


def test_stats_per_domain(ldb):
    stats = ldb.review_stats(_corpus_rows() + Rows().add("manual_upload", "x", "AIDA", None, 0, None).rows)
    domains = {d["domain"]: d for d in stats["domains"]}
    assert set(domains) == {"luluhypermarket.com", "carrefouruae.com", "noon.com"}   # uploads carry no domain
    assert domains["noon.com"] == {"domain": "noon.com", "approved": 0, "rejected": 1}
    assert domains["carrefouruae.com"]["approved"] == 4
    assert domains["luluhypermarket.com"]["approved"] == len(_live_rows()) - 4
    assert stats["domains"][0]["domain"] == "luluhypermarket.com"          # busiest domain first


def test_a_brand_opens_only_on_enough_evidence(ldb):
    rows = Rows()
    for n in range(189):
        rows.accepted(f"alali-{n}", "AL ALALI" if n % 2 else "Al Alali")   # same brand, other spelling
    for n in range(40):
        rows.accepted(f"mccain-{n}", "MCCAIN")
    for n in range(189):
        rows.accepted(f"family-{n}", "FAMILY")
    rows.refused("family-bad", "FAMILY", "WRONG_VARIANT")
    stats = ldb.review_stats(rows.rows)
    brands = {b["brand"].upper(): b for b in stats["brands"]}

    assert len(stats["brands"]) == 3                                      # 'Al Alali' grouped with 'AL ALALI'
    assert brands["AL ALALI"]["prechecked"] == 189 and brands["AL ALALI"]["status"] == "ready"
    assert brands["MCCAIN"]["status"] == "needs_reviews"                  # 40/40: 100%, bound 0.912
    assert brands["FAMILY"]["status"] == "needs_reviews"                  # 189/190: one miss needs more
    assert brands["FAMILY"]["top_reject_reasons"] == [("WRONG_VARIANT", 1)]
    assert len(stats["ready_brands"]) == 1 and stats["ready_brands"][0].upper() == "AL ALALI"
    assert stats["suggested_auto_publish_brands"] == stats["ready_brands"][0]


def test_a_brand_without_a_name_is_never_suggested(ldb):
    rows = Rows()
    for n in range(200):
        rows.accepted(f"anon-{n}", "")
    stats = ldb.review_stats(rows.rows)
    assert stats["brands"][0]["brand"] == "" and stats["brands"][0]["status"] == "needs_reviews"
    assert stats["ready_brands"] == [] and stats["suggested_auto_publish_brands"] == ""


@pytest.mark.parametrize("name", ["Nestle, Middle East", "*", "category:Dairy", " CATEGORY:dairy "])
def test_a_brand_the_setting_cannot_hold_is_never_suggested(ldb, name):
    """AUTO_PUBLISH_BRANDS is comma-separated, '*' opens every brand and 'category:' a whole category:
    pasting such a name would open auto-publish for brands nobody reviewed."""
    rows = Rows()
    for n in range(200):
        rows.accepted(f"odd-{n}", name)
    for n in range(189):
        rows.accepted(f"alali-{n}", "AL ALALI")
    stats = ldb.review_stats(rows.rows)
    odd = next(b for b in stats["brands"] if b["brand"] == name.strip())
    assert (odd["prechecked"], odd["accepted"]) == (200, 200)
    assert odd["status"] == "needs_reviews" and odd["reviews_needed"] is None
    assert stats["ready_brands"] == ["AL ALALI"]
    # read back the way config.py parses the setting: exactly the one proven brand
    entries = [b.strip() for b in stats["suggested_auto_publish_brands"].split(",") if b.strip()]
    assert entries == ["AL ALALI"]


def test_stats_do_not_depend_on_row_order(ldb):
    rows = _corpus_rows() + Rows().refused("live-2", "AIDA").rows
    rows[-1].update(id=999, created_at="2026-10-01 09:00:00")
    shuffled = list(rows)
    random.Random(7).shuffle(shuffled)
    assert ldb.review_stats(shuffled) == ldb.review_stats(rows)
    aida = next(b for b in ldb.review_stats(rows)["brands"] if b["brand"] == "AIDA")
    assert (aida["prechecked"], aida["accepted"]) == (1, 0)


def test_stats_on_no_rows(ldb):
    stats = ldb.review_stats([])
    assert stats["overall"] == {"actions": 0, "approved": 0, "rejected": 0, "manual_upload": 0, "reviewed_skus": 0,
                                "prechecked": 0, "accepted": 0, "precision": None, "lower_bound": None}
    assert stats["brands"] == [] and stats["domains"] == [] and stats["suggested_auto_publish_brands"] == ""


def test_lower_bound_is_never_negative(ldb):
    stats = ldb.review_stats(Rows().refused("a", "AIDA").refused("b", "AIDA").rows)
    assert stats["brands"][0]["lower_bound"] == 0.0 and stats["overall"]["lower_bound"] == 0.0


# ---------------------------------------------------------------------------
# scripts/review_stats.py (read-only)
# ---------------------------------------------------------------------------

@pytest.fixture
def script(ldb):
    return _load("review_stats_script", ROOT / "scripts" / "review_stats.py")


def _fake_db(ldb, monkeypatch, fake_connection, rows):
    conns = []

    def responder(sql, params):
        return rows if sql.upper().startswith("SELECT") else None

    def connect():
        conns.append(fake_connection(responder))
        return conns[-1]

    monkeypatch.setattr(ldb, "get_db_connection", connect)
    return conns


def test_script_prints_the_tables_and_the_suggestion(script, ldb, monkeypatch, fake_connection, capsys, tmp_path):
    rows = Rows()
    for n in range(189):
        rows.accepted(f"alali-{n}", "AL ALALI")
    rows.rows += _corpus_rows()
    conns = _fake_db(ldb, monkeypatch, fake_connection, rows.rows)
    out_json = tmp_path / "stats.json"

    assert script.main(["--json", str(out_json)]) == 0
    out = capsys.readouterr().out
    assert "Per brand" in out and "Per page domain" in out
    assert re.search(r"AL ALALI\s+195\s+195\s+195\s+100\.0%", out)
    assert "needs more reviews (4/" in out                                  # VIRGINIA 3/4
    assert "WRONG_SIZE 1" in out
    assert "Suggested AUTO_PUBLISH_BRANDS=AL ALALI" in out
    assert json.loads(out_json.read_text(encoding="utf-8"))["ready_brands"] == ["AL ALALI"]
    # read-only: nothing but SELECT reached the database, and nothing was committed
    executed = [sql for conn in conns for sql in conn.sql()]
    assert executed and all(sql.upper().startswith("SELECT") for sql in executed)
    assert sum(conn.commits for conn in conns) == 0


def test_script_on_an_empty_table(script, ldb, monkeypatch, fake_connection, capsys):
    _fake_db(ldb, monkeypatch, fake_connection, [])
    assert script.main([]) == 0
    out = capsys.readouterr().out
    assert "No reviewer decisions yet" in out
    assert "Suggested AUTO_PUBLISH_BRANDS= (empty: no brand is ready yet)" in out


def test_format_report_marks_a_brand_below_target(script, ldb):
    rows = Rows()
    for n in range(40):
        (rows.accepted if n % 10 else rows.refused)(f"v-{n}", "VIRGINIA")
    text = script.format_report(ldb.review_stats(rows.rows))
    assert "precision below target" in text and "90.0%" in text


# ---------------------------------------------------------------------------
# The auto-publish tab of Settings (was the active-learning page) shows the real stats, not fabricated rules
# ---------------------------------------------------------------------------

FABRICATED = (
    "padding_ratio", "0.70 (", "0.75 (", "0.85 (الافتراضي)", "clutter_check", "cropping_alert", "clutter_alert",
    "Brand Overrides", "قواعد المواءمة الذاتية", "ذاتية التصحيح", "يتعلم ويصحح تلقائياً", "قواعد قص الحواف",
    "قواعد فرز الخلفيات", "resetBrandLearning", "activeLearningChart",
)


def test_python_ignores_the_padding_the_page_claimed(offline):
    """The removed rules were never applied: the canvas occupancy is fixed, padding_ratio has no effect."""
    import inspect
    import image_processor
    source = inspect.getsource(image_processor.process_product_image)
    assert "padding_ratio" not in source.split('"""')[-1]      # accepted for compatibility, never used


def test_page_no_longer_shows_fabricated_rules():
    page = PAGE.read_text(encoding="utf-8")
    controller = CONTROLLER.read_text(encoding="utf-8")
    for token in FABRICATED:
        assert token not in page, token
        assert token not in controller, token
    assert not (DASH / "resources" / "views" / "dashboard" / "active_learning.blade.php").exists()


def test_page_shows_the_review_stats():
    page = PAGE.read_text(encoding="utf-8")
    controller = CONTROLLER.read_text(encoding="utf-8")
    show = controller[controller.index("public function show"):controller.index("public function save")]
    assert "PythonBridge::run('review_stats')" in show
    # per brand: the reviewed suggestions, precision, Wilson lower bound and the status, from the bridge
    for key in ("$row['reviews']", "$row['precision']", "$row['lower_bound']", "$row['chip']"):
        assert key in page, key
    row = controller[controller.index("public static function brandRow"):controller.index("public static function listedOnlyRow")]
    for text in ("'prechecked'", "'lower_bound'", "'reviews_needed'", "'جاهزة'", "'تحتاج '", "'دقة أقل من المطلوب'"):
        assert text in row, text
    # empty and unavailable states are said, never shown as zero
    assert "لسا ما في مراجعات" in page and "ما قدرنا نحسب دقة الماركات" in page
    # the thresholds come from the bridge (local_cache_db), never hard-coded in the page or the controller
    assert "$thresholds['min_lower_bound']" in controller and "$thresholds['perfect_record_reviews']" in controller
    for text in (page, controller):
        assert "/30)" not in text and "0.98" not in text and "189" not in text


def test_review_stats_is_a_valid_bridge_action():
    import cli_bridge
    assert cli_bridge.ACTIONS["review_stats"] is cli_bridge.action_review_stats
    # PythonBridge::buildCommand only accepts [a-z][a-z_-]* action names
    assert re.fullmatch(r"[a-z][a-z_-]*", "review_stats")


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_page_inline_js_parses(tmp_path):
    # the settings page keeps its script in public/js/settings.js; the tab itself has no inline script
    assert "<script" not in PAGE.read_text(encoding="utf-8")
    result = subprocess.run([NODE, "--check", str(SETTINGS_JS)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
