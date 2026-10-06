"""The dashboard's server side of wp/ux (the Laravel app through its HTTP kernel on the MariaDB test database with a
stub cli_bridge; skipped without php, dashboard/vendor or MariaDB):

* /api/review/queue-state says what reviewers did today (review_decisions since midnight in the shop's zone), for the
  «all done» recap;
* the review page's config says whether the mode came from the link (the page may choose otherwise), the undo
  window, and accepts the shared filters (eligible, strict) in deep links;
* every page carries the shop's time zone; Health's server-rendered times are in it (Asia/Dubai, not the server's UTC);
* Home's tiles and review button deep-link (bulk mode, not-found, failures) and Home has the next-step banner; the Run
  page has «راجع الجاهز هلق» in the live card and the anchor Home's «missing brands» step links to.
"""

import html
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from laqta_kernel import NEEDS_LARAVEL, kernel, sql, stub_env

ROOT = Path(__file__).resolve().parents[1]
HEALTH_PHP = ROOT / "dashboard" / "app" / "Http" / "Controllers" / "HealthController.php"
PHP = shutil.which("php")


def _config_of(body):
    m = re.search(r'id="rvApp"[^>]*data-config="([^"]+)"', body)
    assert m, body[:2000]
    return json.loads(html.unescape(m.group(1)))


def _today(env):
    state = kernel(env, [["GET", "/api/review/queue-state"]])[0]
    assert state["status"] == 200, state["body"][:2000]
    return json.loads(state["body"])["today"]


@NEEDS_LARAVEL
def test_queue_state_counts_todays_decisions_in_the_shops_zone(mariadb_or_skip, tmp_path):
    db = mariadb_or_skip
    env, _ = stub_env(tmp_path)
    sql(db, "DELETE FROM review_decisions WHERE sku_key LIKE %s", ("ux-today-%",))
    before = _today(env)
    try:
        for action, when in [("approved", "NOW()"), ("approved", "NOW()"), ("manual_upload", "NOW()"),
                             ("rejected", "NOW()"), ("approved", "NOW() - INTERVAL 2 DAY"),
                             ("rejected", "NOW() - INTERVAL 3 DAY")]:
            sql(db, f"INSERT INTO review_decisions (created_at, action, sku_key, `row_number`) VALUES ({when}, %s, %s, 1)",
                (action, f"ux-today-{action}"))
        after = _today(env)
    finally:
        sql(db, "DELETE FROM review_decisions WHERE sku_key LIKE %s", ("ux-today-%",))
    # an upload is an approval too; decisions of earlier days are not today's
    assert after["approved"] - before["approved"] == 3
    assert after["rejected"] - before["rejected"] == 1


@NEEDS_LARAVEL
def test_the_pages_deep_link_and_carry_the_shops_zone(mariadb_or_skip, tmp_path):
    env, _ = stub_env(tmp_path)
    plain, single, eligible, strict, home, run = kernel(env, [
        ["GET", "/catalog"], ["GET", "/catalog?mode=single"], ["GET", "/catalog?filter=eligible"],
        ["GET", "/catalog?mode=bulk&filter=strict"], ["GET", "/"], ["GET", "/batch-automation"]])
    for page in (plain, home, run):
        assert page["status"] == 200, page["body"][:2000]
        assert 'data-lq-tz="Asia/Dubai"' in page["body"]
    cfg = _config_of(plain["body"])
    assert cfg["modeExplicit"] is False and cfg["mode"] == "single" and cfg["approveUndoMs"] == 8000
    assert _config_of(single["body"])["modeExplicit"] is True
    assert _config_of(eligible["body"])["filter"] == "eligible"
    assert (_config_of(strict["body"])["mode"], _config_of(strict["body"])["filter"]) == ("bulk", "strict")

    body = home["body"]
    tiles = dict(re.findall(r'<a class="lq-kpi" href="([^"]+)" data-kpi="([a-z_]+)"', body)[i][::-1] for i in range(4))
    assert tiles["waiting"].endswith("/catalog?mode=bulk")
    assert tiles["not_found"].endswith("/catalog?filter=not_found") and tiles["failed"].endswith("/catalog?filter=failed")
    assert re.search(r'href="[^"]*/catalog\?mode=bulk" data-home="review-link"', body)
    assert 'data-home="next"' in body and 'data-home="next-action"' in body

    body = run["body"]
    assert 'data-run="review-now"' in body and re.search(r'href="[^"]*/catalog\?mode=bulk" data-run="review-now"', body)
    assert 'data-run="notify"' in body and 'id="run-brands"' in body
    assert "ماركات ناقصة من جدول الماركات" in body


@pytest.mark.skipif(PHP is None, reason="php is not installed")
def test_health_prints_times_in_the_shops_zone_not_the_servers_utc():
    script = ("<?php\nnamespace App\\Http\\Controllers { abstract class Controller {} }\nnamespace {\n"
              f"require '{str(HEALTH_PHP)}';\n"
              "echo json_encode([App\\Http\\Controllers\\HealthController::stamp(0),"
              " App\\Http\\Controllers\\HealthController::stamp(1790000000)]);\n}\n")
    with tempfile.NamedTemporaryFile("w", suffix=".php", delete=False, encoding="utf-8") as fh:
        fh.write(script)
        path = fh.name
    try:
        def stamps(**extra):
            env = dict(os.environ)
            env.pop("LAQTA_TIMEZONE", None)
            env.update(extra)
            res = subprocess.run([PHP, path], capture_output=True, text=True, timeout=60, env=env)
            assert res.returncode == 0, res.stdout + res.stderr
            return json.loads(res.stdout)

        assert stamps() == ["1970-01-01 04:00", "2026-09-21 18:13"]                  # Asia/Dubai, UTC+4
        assert stamps(LAQTA_TIMEZONE="UTC") == ["1970-01-01 00:00", "2026-09-21 14:13"]
        assert stamps(LAQTA_TIMEZONE="Not/AZone") == ["1970-01-01 04:00", "2026-09-21 18:13"]
    finally:
        os.unlink(path)
