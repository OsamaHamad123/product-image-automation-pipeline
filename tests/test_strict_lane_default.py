"""AUTO_PUBLISH_STRICT_LANE is on by default (the owner approved it; the lane still publishes nothing before its
reviews prove it, test_review_lanes.py). init_db seeds it on, and once (local_cache_db.STRICT_LANE_DEFAULT_MARKER)
turns the old seed's 'false' on; after that the value the owner saves in the Settings page wins at every start.
"""


def test_the_old_seeded_false_turns_on_once_then_the_saved_value_wins(mariadb_or_skip):
    db = mariadb_or_skip

    def sql(statement, params=()):
        conn = db.get_db_connection()
        try:
            cur = conn.cursor()
            cur.execute(statement, params)
            rows = cur.fetchall()
            conn.commit()
            return rows
        finally:
            conn.close()

    def lane():
        rows = sql("SELECT `value` FROM system_settings WHERE `key` = 'auto_publish_strict_lane'")
        return rows[0]["value"] if rows else None

    saved = lane()
    try:
        # an install from before: the old seed 'false', the migration not run yet
        sql("UPDATE system_settings SET `value` = 'false' WHERE `key` = 'auto_publish_strict_lane'")
        sql("DELETE FROM system_settings WHERE `key` = %s", (db.STRICT_LANE_DEFAULT_MARKER,))
        assert db.init_db() and lane() == "true"
        # the owner switches it off in the Settings page: every later start keeps that
        sql("UPDATE system_settings SET `value` = 'false' WHERE `key` = 'auto_publish_strict_lane'")
        assert db.init_db() and db.init_db() and lane() == "false"
        # a fresh install is seeded on
        sql("DELETE FROM system_settings WHERE `key` IN ('auto_publish_strict_lane', %s)",
            (db.STRICT_LANE_DEFAULT_MARKER,))
        assert db.init_db() and lane() == "true"
    finally:
        if saved is not None:
            sql("UPDATE system_settings SET `value` = %s WHERE `key` = 'auto_publish_strict_lane'", (saved,))


def test_the_dashboard_reads_the_same_default():
    from pathlib import Path

    from catalog_match import settings

    php = Path(__file__).resolve().parents[1] / "dashboard" / "app" / "Http" / "Controllers" / "SettingsController.php"
    assert "public const STRICT_LANE_DEFAULT = true;" in php.read_text(encoding="utf-8")
    assert settings.DEFAULTS["AUTO_PUBLISH_STRICT_LANE"] is True


def test_review_stats_says_whether_the_switch_is_on(monkeypatch, offline):
    import cli_bridge
    import local_cache_db
    from catalog_match import settings

    monkeypatch.setattr(local_cache_db, "get_review_decisions", lambda: [])
    monkeypatch.setattr(settings, "auto_publish_strict_lane", lambda: True)
    out = cli_bridge.action_review_stats({})
    assert out["status"] == "success" and out["strict_lane_enabled"] is True
    assert out["lanes"]["strict"]["status"] == "needs_reviews"
    monkeypatch.setattr(settings, "auto_publish_strict_lane", lambda: False)
    assert cli_bridge.action_review_stats({})["strict_lane_enabled"] is False
