"""Real-MariaDB tests for the queue, cache, curation and rejection tables.

They run against `automation_test` (see conftest.py) and skip when MariaDB is not
reachable. The fake-connection tests elsewhere check which SQL is issued; these
check that the SQL actually does the right thing on MariaDB.
"""

import pytest

ROWS = (910001, 910002, 910003)
SKU = "wp5-test-sku-0001"
GTIN_SKU = "06281007000028"


@pytest.fixture
def db(mariadb_or_skip):
    db = mariadb_or_skip

    def cleanup():
        conn = db.get_db_connection()
        try:
            cur = conn.cursor()
            cur.execute("DELETE FROM automation_queue WHERE `row_number` IN (%s, %s, %s)", ROWS)
            cur.execute("DELETE FROM curation_candidates WHERE `row_number` IN (%s, %s, %s)", ROWS)
            cur.execute("DELETE FROM rejected_images WHERE sku_key IN (%s, %s)", (SKU, GTIN_SKU))
            cur.execute("DELETE FROM resolved_products WHERE sku_key IN (%s, %s) OR barcode = %s",
                        (SKU, GTIN_SKU, "6281007000028"))
            conn.commit()
        finally:
            conn.close()

    cleanup()
    yield db
    cleanup()


def _queue_rows(db):
    conn = db.get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM automation_queue WHERE `row_number` IN (%s, %s, %s) ORDER BY `row_number`", ROWS)
        return {r["row_number"]: r for r in cur.fetchall()}
    finally:
        conn.close()


def test_init_db_is_idempotent(db):
    assert db.init_db() is True
    assert db.init_db() is True


def test_automation_state_crud(db):
    assert db.update_automation_state(status='pre_caching', total=10, processed=2, success=1, failed=1,
                                      current_product='Test Product', notice='VERIFIER_NOT_CONFIGURED')
    state = db.get_automation_state()
    assert state['status'] == 'pre_caching'
    assert state['total_items'] == 10
    assert state['processed_items'] == 2
    assert state['success_count'] == 1
    assert state['failed_count'] == 1
    assert state['current_product_name'] == 'Test Product'
    assert state['notice'] == 'VERIFIER_NOT_CONFIGURED'


def test_pause_resume_mechanics(db):
    assert db.pause_automation()
    assert db.get_automation_state()['pause_requested'] == 1
    assert db.resume_automation()
    assert db.get_automation_state()['pause_requested'] == 0


def test_enqueue_upsert_keeps_review_rows(db):
    db.add_to_queue(ROWS[0], "6281007000028", "Fresh Milk 1L", "Almarai", "q",
                    payload={"name_ar": "حليب طازج"}, sku_key=SKU)
    db.update_task_status(_queue_rows(db)[ROWS[0]]["id"], "ready_for_review", failure_code="VERIFIER_DOWN")

    # Re-enqueueing the same product must not reset the reviewer's row ...
    db.add_to_queue(ROWS[0], "6281007000028", "Fresh Milk 1L", "Almarai", "q",
                    payload={"name_ar": "حليب طازج كامل الدسم"}, sku_key=SKU)
    row = _queue_rows(db)[ROWS[0]]
    assert row["status"] == "ready_for_review"
    assert row["failure_code"] == "VERIFIER_DOWN"
    assert "كامل الدسم" in row["payload_json"]          # ... but the payload is refreshed

    # ... unless reprocess is requested ...
    db.add_to_queue(ROWS[0], "6281007000028", "Fresh Milk 1L", "Almarai", "q", sku_key=SKU, reprocess=True)
    assert _queue_rows(db)[ROWS[0]]["status"] == "pending"

    # ... or a different product now sits in that sheet row.
    db.update_task_status(_queue_rows(db)[ROWS[0]]["id"], "completed")
    db.add_to_queue(ROWS[0], "999", "Other Product", "Other", "q", sku_key="another-sku")
    assert _queue_rows(db)[ROWS[0]]["status"] == "pending"


def test_claim_is_exclusive_and_leased(db):
    conn = db.get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("UPDATE automation_queue SET status='completed' WHERE status IN ('pending','processing')")
        conn.commit()
    finally:
        conn.close()
    db.add_to_queue(ROWS[1], "", "Juice A", "Masafi", "q", sku_key="sku-a")
    db.add_to_queue(ROWS[2], "", "Juice B", "Masafi", "q", sku_key="sku-b")

    first = db.fetch_next_task("worker-1")
    second = db.fetch_next_task("worker-2")
    assert {first["row_number"], second["row_number"]} == {ROWS[1], ROWS[2]}
    assert first["worker_id"] != second["worker_id"]
    assert first["status"] == "processing" and first["lease_until"] is not None
    assert first["attempts"] == 1
    assert db.fetch_next_task("worker-3") is None            # nothing left to claim
    assert db.count_open_tasks() >= 2

    # An expired lease makes the row claimable again (a crashed worker's task is not lost).
    conn = db.get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("UPDATE automation_queue SET lease_until = NOW() - INTERVAL 1 MINUTE WHERE id = %s", (first["id"],))
        conn.commit()
    finally:
        conn.close()
    again = db.fetch_next_task("worker-4")
    assert again["id"] == first["id"]
    assert again["attempts"] == 2

    stats = db.get_queue_statistics()
    assert "ready_for_review" in stats and stats["processing"] >= 2


def test_cache_serves_only_verified_and_is_barcode_strict(db):
    assert db.save_product_resolution("6281007000028", "Fresh Milk", "Almarai", "https://src/1.jpg",
                                      "https://res.cloudinary.com/x/1.png", None, {"a": 1},
                                      verification_status="legacy", sku_key=GTIN_SKU)
    assert db.get_cached_product("6281007000028", "Fresh Milk", "Almarai") is None      # legacy is never served

    assert db.save_product_resolution("6281007000028", "Fresh Milk", "Almarai", "https://src/1.jpg",
                                      "https://res.cloudinary.com/x/2.png", None, {"a": 1},
                                      verification_status="human_approved", approved_by="human", sku_key=GTIN_SKU)
    hit = db.get_cached_product("6281007000028", "Fresh Milk", "Almarai")
    assert hit["cloudinary_url"].endswith("2.png") and hit["verification_status"] == "human_approved"

    # A sibling with another barcode but the same name gets nothing (no name fallback).
    assert db.get_cached_product("6281007000035", "Fresh Milk", "Almarai") is None

    assert db.supersede_resolution(GTIN_SKU) == 1
    assert db.get_cached_product("6281007000028", "Fresh Milk", "Almarai") is None


def test_rejections_roundtrip(db):
    assert db.add_rejected_image(SKU, "https://www.shop.ae/img/a.jpg?w=300", "https://shop.ae/p/1",
                                 "00ff00ff00ff00ff", "WRONG_VARIANT")
    assert db.add_rejected_image(SKU, "https://cdn.ae/b.jpg", None, None, "HALO_ARTIFACT")
    urls, phashes = db.get_rejections(SKU)
    assert urls == ["https://www.shop.ae/img/a.jpg?w=300"]         # cosmetic codes do not exclude a source
    assert phashes == ["00ff00ff00ff00ff"]
    with pytest.raises(ValueError):
        db.add_rejected_image(SKU, "https://x/y.jpg", reason_code="NOT_A_CODE")


def test_curation_candidates_roundtrip(db):
    long_title = "Almarai Fresh Milk " * 40
    ok = db.save_curation_candidates(ROWS[0], "Fresh Milk", "Almarai", [
        {"url": "https://a.ae/1.jpg", "title": long_title, "status": "preselected", "reasons": ["T1"],
         "evidence": {"tier": "T1", "brand": True}, "vlm": {"decision": "MATCH"},
         "scores": {"identity_score": 0.93}, "content_sha256": "ab" * 32, "page_url": "https://a.ae/p"},
        {"url": "https://b.ae/2.jpg", "title": "B", "status": "rejected", "reasons": ["size_conflict"]},
    ], "https://b.ae/2.jpg", sku_key=SKU, run_id="run-1")
    assert ok is True
    rows = db.get_curation_candidates(ROWS[0])
    assert [r["image_url"] for r in rows] == ["https://a.ae/1.jpg", "https://b.ae/2.jpg"]
    assert rows[0]["is_selected"] == 1 and rows[1]["is_selected"] == 0   # best_url no longer preselects
    assert rows[0]["evidence"]["tier"] == "T1" and rows[0]["identity_tier"] == "T1"
    assert rows[1]["reasons"] == ["size_conflict"]
    assert rows[0]["sku_key"] == SKU and rows[0]["run_id"] == "run-1"
    assert len(rows[0]["title"]) <= 250
