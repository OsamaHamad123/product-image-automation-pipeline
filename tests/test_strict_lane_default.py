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
