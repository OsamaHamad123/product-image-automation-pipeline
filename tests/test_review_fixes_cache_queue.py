"""Regression tests for reviewer findings on the cache, queue and review actions.

Real-MariaDB tests use the `mariadb_or_skip` fixture (they skip when MariaDB is
down); the others use the recording FakeConnection and block every socket.
"""

import pytest

ROWS = (920001, 920002, 920003)
SKUS = ("rf-sku-a", "rf-sku-b", "rf-sku-c")


@pytest.fixture
def db(mariadb_or_skip):
    db = mariadb_or_skip

    def cleanup():
        conn = db.get_db_connection()
        try:
            cur = conn.cursor()
            cur.execute("DELETE FROM automation_queue WHERE `row_number` IN (%s, %s, %s)", ROWS)
            cur.execute("DELETE FROM curation_candidates WHERE `row_number` IN (%s, %s, %s)", ROWS)
            cur.execute("DELETE FROM resolved_products WHERE sku_key IN (%s, %s, %s) "
                        "OR barcode IN ('N/A', '6.29E+12', '0')", SKUS)
            conn.commit()
        finally:
            conn.close()

    cleanup()
    yield db
    cleanup()


# ---------------------------------------------------------------------------
# Junk barcode cells never key the cache (blocker)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("junk", ["N/A", "6.29E+12", "0"])
def test_junk_barcode_never_cross_serves_or_overwrites(db, junk):
    assert db.save_product_resolution(junk, "Almarai Full Fat Milk 1L", "Almarai", "https://src/a.jpg",
                                      "https://res/a.png", verification_status="human_approved",
                                      sku_key=SKUS[0])
    # Another product with the same junk cell: no cache hit ...
    assert db.get_cached_product(barcode=junk, product_name="Al Rawabi Laban 500ml", brand="Al Rawabi",
                                 sku_key=SKUS[1]) is None
    # ... and approving it does not overwrite A's approval.
    assert db.save_product_resolution(junk, "Al Rawabi Laban 500ml", "Al Rawabi", "https://src/b.jpg",
                                      "https://res/b.png", verification_status="human_approved",
                                      sku_key=SKUS[1])
    hit_a = db.get_cached_product(barcode=junk, product_name="Almarai Full Fat Milk 1L", brand="Almarai",
                                  sku_key=SKUS[0])
    hit_b = db.get_cached_product(barcode=junk, product_name="Al Rawabi Laban 500ml", brand="Al Rawabi",
                                  sku_key=SKUS[1])
    assert hit_a["cloudinary_url"] == "https://res/a.png"
    assert hit_b["cloudinary_url"] == "https://res/b.png"
    # A reject of B leaves A's approval in place.
    assert db.supersede_resolution(SKUS[1], barcode=junk) == 1
    assert db.get_cached_product(barcode=junk, product_name="x", brand="y", sku_key=SKUS[0]) is not None


def test_valid_gtin_still_used_for_legacy_rows(ldb_fake):
    ldb, conn = ldb_fake
    ldb.get_cached_product(barcode="6281007000024", sku_key="06281007000024")
    selects = [(s, p) for s, p in conn.executed if s.startswith("SELECT")]
    assert [p for _, p in selects] == [("06281007000024",), ("6281007000024",)]


def test_junk_barcode_issues_no_barcode_query(ldb_fake):
    ldb, conn = ldb_fake
    ldb.get_cached_product(barcode="N/A", product_name="Lays Salted 50g", brand="Lays", sku_key="k_lays")
    ldb.supersede_resolution("k_lays", barcode="N/A")
    ldb.save_product_resolution("N/A", "Lays Salted 50g", "Lays", "u", "c", verification_status="human_approved",
                                sku_key="k_lays")
    assert not any("barcode = %s" in s for s, _ in conn.executed), conn.executed


@pytest.fixture
def ldb_fake(offline, monkeypatch, fake_connection):
    import local_cache_db
    conn = fake_connection(lambda sql, params: [])
    monkeypatch.setattr(local_cache_db, "get_db_connection", lambda: conn)
    monkeypatch.setattr(local_cache_db, "delete_product_failure", lambda *a, **k: None)
    return local_cache_db, conn


# ---------------------------------------------------------------------------
# Queue: legacy 'processing' rows and claim ownership
# ---------------------------------------------------------------------------

def _set_row(db, row_number, **cols):
    conn = db.get_db_connection()
    try:
        cur = conn.cursor()
        sets = ", ".join(f"{k} = %s" for k in cols)
        cur.execute(f"UPDATE automation_queue SET {sets} WHERE `row_number` = %s", tuple(cols.values()) + (row_number,))
        conn.commit()
    finally:
        conn.close()


def _drain_other_rows(db):
    """Park every other open row of the shared test database so fetch_next_task only sees ours."""
    conn = db.get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM automation_queue WHERE `row_number` NOT IN (%s, %s, %s) "
                    "AND status IN ('pending','processing')", ROWS)
        conn.commit()
    finally:
        conn.close()


def test_processing_row_with_null_lease_is_claimable(db):
    """Rows the old worker left in 'processing' (lease_until NULL) are claimed, so the worker can finish."""
    _drain_other_rows(db)
    db.add_to_queue(ROWS[0], "", "Tomato Paste 400g", "Al Alali", "q", sku_key=SKUS[0])
    _set_row(db, ROWS[0], status="processing", lease_until=None, worker_id="old-worker")
    assert db.count_open_tasks() == 1
    task = db.fetch_next_task("new-worker")
    assert task is not None and task["row_number"] == ROWS[0]
    assert db.update_task_status(task["id"], "ready_for_review", claim_id=task["worker_id"]) is True
    assert db.count_open_tasks() == 0


def test_worker_status_write_respects_human_completion(db):
    _drain_other_rows(db)
    db.add_to_queue(ROWS[1], "", "Basmati Rice 5kg", "India Gate", "q", sku_key=SKUS[1])
    task = db.fetch_next_task("w")
    assert task["row_number"] == ROWS[1]
    assert db.is_claim_held(task["id"], task["worker_id"]) is True
    db.update_task_status_by_row(ROWS[1], "completed")          # reviewer approved meanwhile
    assert db.is_claim_held(task["id"], task["worker_id"]) is False
    assert db.update_task_status(task["id"], "ready_for_review", claim_id=task["worker_id"]) is False
    conn = db.get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT status FROM automation_queue WHERE id = %s", (task["id"],))
        assert cur.fetchone()["status"] == "completed"
    finally:
        conn.close()


def test_cleanup_after_a_row_shift_touches_only_the_approved_product(db):
    """A was row 920001 at enqueue, B row 920002. After an insert A is approved at row 920002."""
    db.add_to_queue(ROWS[0], "", "Product A", "Brand", "q", sku_key=SKUS[0])
    db.add_to_queue(ROWS[1], "", "Product B", "Brand", "q", sku_key=SKUS[1])
    cand = [{"url": "https://x/a.jpg", "title": "a", "status": "eligible"}]
    assert db.save_curation_candidates(ROWS[0], "Product A", "Brand", cand, sku_key=SKUS[0])
    assert db.save_curation_candidates(ROWS[1], "Product B", "Brand",
                                       [{"url": "https://x/b.jpg", "title": "b", "status": "eligible"}],
                                       sku_key=SKUS[1])
    # approve A, whose current sheet row is ROWS[1]
    db.update_task_status_by_row(ROWS[1], "completed", sku_key=SKUS[0])
    db.delete_curation_candidates(ROWS[1], sku_key=SKUS[0])
    conn = db.get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT `row_number`, status FROM automation_queue WHERE `row_number` IN (%s, %s)", ROWS[:2])
        status = {r["row_number"]: r["status"] for r in cur.fetchall()}
    finally:
        conn.close()
    assert status == {ROWS[0]: "completed", ROWS[1]: "pending"}
    assert db.get_curation_candidates(ROWS[0]) == []
    assert [c["image_url"] for c in db.get_curation_candidates(ROWS[1])] == ["https://x/b.jpg"]
