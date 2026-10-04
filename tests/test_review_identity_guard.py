"""cli_bridge refuses an approve / reject / upload whose sku_key is not the key of the product fields sent with it.

The catalog page could send product A's sku_key with product B's row and fields (a late search response for A
overwrote the form while B was open). The bridge recomputes the key from the sent fields with the worker's own
function (main.compute_sku_key(main.sku_row(...))) and writes nothing when it differs. The dashboards' real
request bodies (catalog, batch page, CurationController, the upload whitelist) are still accepted.

The first part is offline (every write is recorded); the last test runs on the real MariaDB test database.
"""

import os
import socket

import pytest

import main

CHANGED = "المنتج تغيّر أثناء المراجعة؛ افتحه من جديد"
LINK = "https://res.cloudinary.com/demo/image/upload/products/dairy/milk.png"
IMAGE_A = "https://www.luluhypermarket.com/medias/almarai-milk-1l.jpg"

ROW_A, ROW_B = 930011, 930012
A = {"product_name": "Almarai Fresh Milk Full Fat 1L", "brand": "Almarai", "barcode": "", "size": "1L",
     "product_name_ar": "حليب المراعي طازج كامل الدسم 1 لتر", "brand_ar": "المراعي", "category": "Dairy"}
B = {"product_name": "Almarai Fresh Milk Full Fat 2L", "brand": "Almarai", "barcode": "", "size": "2L",
     "product_name_ar": "حليب المراعي طازج كامل الدسم 2 لتر", "brand_ar": "المراعي", "category": "Dairy"}
# The key depends on a field the batch page does not send: no English brand, so the Arabic brand keys the row.
AR_ONLY = {"product_name": "Fresh Laban 1L", "brand": "", "barcode": "", "size": "1L",
           "product_name_ar": "لبن طازج 1 لتر", "brand_ar": "المراعي", "category": "Dairy"}
# The size is only in the size column: the upload whitelist (ApiController) does not forward it.
SIZE_ONLY = {"product_name": "Almarai Laban Plain", "brand": "Almarai", "barcode": "", "size": "2L",
             "product_name_ar": "لبن المراعي", "brand_ar": "المراعي", "category": "Dairy"}


def key_of(p):
    """The product's key exactly as get_products computes it for the page (and run_enqueue_mode for the queue)."""
    return main.compute_sku_key(main.sku_row(p["product_name"], p["brand"], p["barcode"], {
        "name_ar": p["product_name_ar"], "brand_ar": p["brand_ar"], "category": p["category"], "size": p["size"]}))


def task_of(p, row):
    """automation_queue row as run_enqueue_mode writes it (payload_json holds the Arabic fields and the size)."""
    import json
    payload = {"name_ar": p["product_name_ar"], "brand_ar": p["brand_ar"], "category": p["category"],
               "sub_category": "", "origin": "", "size": p["size"]}
    return {"row_number": row, "product_name": p["product_name"], "brand": p["brand"], "barcode": p["barcode"],
            "sku_key": key_of(p), "payload_json": json.dumps(payload, ensure_ascii=False)}


# The request bodies the dashboards send (see the blade views and the controllers).

def catalog_approve(p, row, url=IMAGE_A, **extra):
    body = {"image_url": url, "page_url": "", "candidate_sha256": None, "row_number": row, "sku_key": key_of(p),
            "product_name": p["product_name"], "brand": p["brand"], "barcode": p["barcode"], "size": p["size"],
            "product_name_ar": p["product_name_ar"], "brand_ar": p["brand_ar"], "category": p["category"],
            "search_decision": "REVIEW_PRESELECTED", "candidate_status": "preselected", "enhance": False,
            "bg_removal_method": "photoroom", "target_width": 0, "target_height": 0}
    body.update(extra)
    return body


def catalog_reject(p, row, url=IMAGE_A, **extra):
    body = {"row_number": row, "image_url": url, "page_url": "", "candidate_sha256": None, "sku_key": key_of(p),
            "product_name": p["product_name"], "brand": p["brand"], "barcode": p["barcode"],
            "reason_code": "WRONG_SIZE", "rejection_reasons": ["WRONG_SIZE"], "research": False,
            "product_name_ar": p["product_name_ar"], "brand_ar": p["brand_ar"], "category": p["category"],
            "size": p["size"], "sub_category": "", "origin": "", "custom_query": ""}
    body.update(extra)
    return body


def batch_approve(p, row, url=IMAGE_A):
    """batch_automation.blade.php submitBatchApproval: no Arabic fields, no category."""
    return {"image_url": url, "page_url": "", "candidate_sha256": None, "product_name": p["product_name"],
            "brand": p["brand"], "row_number": row, "barcode": p["barcode"], "sku_key": key_of(p), "size": p["size"],
            "enhance": False, "bg_removal_method": "photoroom", "target_width": 0, "target_height": 0}


def batch_bulk_reject(p, row, url=IMAGE_A):
    """batch_automation.blade.php runBatchRejection."""
    return {"row_number": row, "image_url": url, "product_name": p["product_name"], "brand": p["brand"],
            "barcode": p["barcode"], "sku_key": key_of(p), "size": p["size"], "reason_code": "WRONG_PRODUCT",
            "rejection_reasons": ["WRONG_PRODUCT"], "research": False}


def curation_reject(p, row, url=IMAGE_A):
    """CurationController::rejectAndReSearch (the batch page's single reject): every identity field, research on."""
    return {"row_number": row, "image_url": url, "product_name": p["product_name"], "brand": p["brand"],
            "barcode": p["barcode"], "sku_key": key_of(p), "reason_code": "WRONG_VARIANT", "research": True,
            "product_name_ar": p["product_name_ar"], "brand_ar": p["brand_ar"], "category": p["category"],
            "size": p["size"], "sub_category": "", "origin": "", "custom_query": "", "skip_cache": True}


def upload(p, row, tmp_path, **extra):
    """ApiController::uploadManualImage forwards only its whitelist (no size, no Arabic fields) plus file_path."""
    path = tmp_path / "manual_upload.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\n")
    body = {"row_number": row, "product_name": p["product_name"], "brand": p["brand"], "barcode": p["barcode"],
            "sku_key": key_of(p), "target_width": 0, "target_height": 0, "enhance": "false",
            "search_decision": "", "file_path": str(path)}
    body.update(extra)
    return body


def stale(body, other):
    """The race: the form shows this product but carries the other product's sku_key."""
    return dict(body, sku_key=key_of(other))


def test_the_fixture_products_have_different_keys():
    keys = [key_of(p) for p in (A, B, AR_ONLY, SIZE_ONLY)]
    assert len(set(keys)) == 4
    # the Arabic brand and the size column are part of these two keys
    assert key_of(dict(AR_ONLY, brand_ar="")) != key_of(AR_ONLY)
    assert key_of(dict(SIZE_ONLY, size="")) != key_of(SIZE_ONLY)


@pytest.fixture
def sheet(monkeypatch, tmp_path):
    """cli_bridge with the sheet, the publishing and the web search replaced by recorders."""
    import cli_bridge
    import google_sheets
    import image_search

    writes = []

    def fake_publish(image_url, name, brand, row_number, worksheet, link_column_index, **kwargs):
        writes.append("publish")
        res = {"status": "published", "isolated": True, "provider": "photoroom", "metadata": {},
               "width": 800, "height": 800, "link": LINK, "sheet_value": LINK}
        if kwargs.get("after_write"):
            kwargs["after_write"](res)        # the decision is recorded under the publish lock
        return res

    def fake_search(query, name, brand, **kwargs):
        writes.append("search")
        kwargs["trace"]["outcome"] = {"decision": "NOT_FOUND", "failure_code": "NO_MATCH"}
        return None

    class Cell:
        value = ""

    class Sheet:
        title = "Products"

        def cell(self, row, col):
            return Cell()

    monkeypatch.setattr(cli_bridge, "LOG_PATH", str(tmp_path / "search.log"))
    monkeypatch.setattr(main, "publish_image", fake_publish)
    monkeypatch.setattr(image_search, "search_best_product_image", fake_search)
    monkeypatch.setattr(google_sheets, "init_async_queue", lambda *a, **k: None)
    monkeypatch.setattr(google_sheets, "stop_async_queue", lambda *a, **k: None)
    monkeypatch.setattr(google_sheets, "get_sheets_client", lambda: object())
    monkeypatch.setattr(google_sheets, "get_brand_mappings", lambda *a, **k: {})
    monkeypatch.setattr(google_sheets, "open_worksheet", lambda client, name: Sheet())
    monkeypatch.setattr(google_sheets, "find_link_column", lambda worksheet, create=True: 3)
    monkeypatch.setattr(google_sheets, "update_image_link", lambda *a, **k: writes.append("update_image_link") or True)
    return cli_bridge, writes


# ---------------------------------------------------------------------------
# Offline: every database write is recorded too
# ---------------------------------------------------------------------------

@pytest.fixture
def bridge(offline, sheet, monkeypatch):
    import local_cache_db

    cli_bridge, writes = sheet
    state = {"tasks": {}}

    def record(name, result=True):
        return lambda *a, **k: writes.append(name) or result

    monkeypatch.setattr(local_cache_db, "get_task_by_row", lambda row: state["tasks"].get(row))
    monkeypatch.setattr(local_cache_db, "get_curation_candidates", lambda row, sku_key=None, **k: [])
    monkeypatch.setattr(local_cache_db, "get_cached_product", lambda **k: None)
    monkeypatch.setattr(local_cache_db, "get_rejections", lambda sku: ([], []))
    for name in ("save_product_resolution", "update_task_status_by_row", "delete_curation_candidates",
                 "add_rejected_image", "save_feedback", "add_review_decision"):
        monkeypatch.setattr(local_cache_db, name, record(name))
    monkeypatch.setattr(local_cache_db, "supersede_resolution", record("supersede_resolution", 1))
    return cli_bridge, writes, state


@pytest.mark.parametrize("queued", [False, True], ids=["no-queue-row", "queue-row-of-B"])
def test_approve_with_another_products_key_is_refused_and_writes_nothing(bridge, queued):
    cli_bridge, writes, state = bridge
    if queued:
        state["tasks"][ROW_B] = task_of(B, ROW_B)
    result = cli_bridge.action_select_image(stale(catalog_approve(B, ROW_B), A))
    assert result == {"status": "failed", "error": CHANGED}
    assert writes == []


def test_approve_with_the_products_own_key_is_accepted(bridge):
    cli_bridge, writes, state = bridge
    result = cli_bridge.action_select_image(catalog_approve(B, ROW_B))
    assert result["status"] == "success" and result["sku_key"] == key_of(B)
    assert writes[0] == "publish"


def test_a_gtin_key_follows_the_barcode_not_the_name(bridge):
    cli_bridge, writes, state = bridge
    gtin = dict(A, barcode="6281007000024")
    assert key_of(gtin) == "06281007000024"
    # another product's GTIN key with this product's barcode
    assert cli_bridge.action_select_image(stale(catalog_approve(B, ROW_B, barcode="6281007000031"), gtin)) == {
        "status": "failed", "error": CHANGED}
    # same barcode, the name was edited: still the same GTIN-keyed product
    assert cli_bridge.action_select_image(catalog_approve(gtin, ROW_A, product_name="Almarai Milk 1L"))["status"] \
        == "success"


@pytest.mark.parametrize("research", [False, True])
def test_reject_with_another_products_key_is_refused_and_writes_nothing(bridge, research):
    cli_bridge, writes, state = bridge
    result = cli_bridge.action_reject_image(stale(catalog_reject(B, ROW_B, research=research), A))
    assert result == {"status": "error", "error": CHANGED}
    assert writes == []                        # no rejection, no supersede, no requeue, no sheet write, no search


def test_reject_with_the_products_own_key_is_accepted(bridge):
    cli_bridge, writes, state = bridge
    result = cli_bridge.action_reject_image(catalog_reject(B, ROW_B))
    assert result["status"] == "success" and result["sku_key"] == key_of(B)
    assert "add_rejected_image" in writes


def test_upload_with_another_products_key_is_refused_and_writes_nothing(bridge, tmp_path):
    cli_bridge, writes, state = bridge
    state["tasks"][ROW_B] = task_of(B, ROW_B)
    result = cli_bridge.action_upload_manual_image(stale(upload(B, ROW_B, tmp_path), A))
    assert result == {"status": "failed", "error": CHANGED}
    assert writes == []


def test_upload_with_the_products_own_key_is_accepted(bridge, tmp_path):
    cli_bridge, writes, state = bridge
    result = cli_bridge.action_upload_manual_image(upload(A, ROW_A, tmp_path))
    assert result["status"] == "success" and result["sku_key"] == key_of(A)


def test_a_request_without_a_key_or_a_name_keeps_the_old_behaviour(bridge):
    """No sku_key: the bridge still derives it (queue row of the same product); no name: nothing to compare."""
    cli_bridge, writes, state = bridge
    state["tasks"][ROW_B] = task_of(B, ROW_B)
    result = cli_bridge.action_select_image(dict(catalog_approve(B, ROW_B), sku_key=""))
    assert result["status"] == "success" and result["sku_key"] == key_of(B)
    result = cli_bridge.action_reject_image(dict(catalog_reject(B, ROW_B), product_name="", brand=""))
    assert result["status"] == "success" and result["sku_key"] == key_of(B)


# The batch page and the controllers send fewer fields: a field absent from the request comes from the
# product's own queue row (the batch page only shows queued products).

@pytest.mark.parametrize("product", [A, AR_ONLY, SIZE_ONLY], ids=["plain", "arabic-brand", "size-column"])
def test_batch_page_bodies_are_still_accepted(bridge, product, tmp_path):
    cli_bridge, writes, state = bridge
    state["tasks"][ROW_A] = task_of(product, ROW_A)
    assert cli_bridge.action_select_image(batch_approve(product, ROW_A))["status"] == "success"
    assert cli_bridge.action_reject_image(batch_bulk_reject(product, ROW_A))["status"] == "success"
    research = cli_bridge.action_reject_image(curation_reject(product, ROW_A))
    assert research["rejection"]["sku_key"] == key_of(product) and research["status"] == "not_found"
    assert cli_bridge.action_upload_manual_image(upload(product, ROW_A, tmp_path))["status"] == "success"


def test_batch_page_body_with_another_products_key_is_refused(bridge):
    cli_bridge, writes, state = bridge
    state["tasks"][ROW_B] = task_of(B, ROW_B)
    assert cli_bridge.action_select_image(stale(batch_approve(B, ROW_B), A)) == {"status": "failed",
                                                                                 "error": CHANGED}
    assert cli_bridge.action_reject_image(stale(batch_bulk_reject(B, ROW_B), A)) == {"status": "error",
                                                                                     "error": CHANGED}
    assert writes == []


def test_a_queue_row_of_another_product_never_fills_the_missing_fields(bridge):
    """The Arabic brand is absent from the request; the row's queue entry belongs to another product (the sheet
    shifted), so it is not used: the key cannot be confirmed and the approval is refused."""
    cli_bridge, writes, state = bridge
    state["tasks"][ROW_A] = task_of(A, ROW_A)
    assert cli_bridge.action_select_image(batch_approve(AR_ONLY, ROW_A)) == {"status": "failed", "error": CHANGED}
    assert writes == []


# ---------------------------------------------------------------------------
# Real MariaDB: a refused request leaves every table untouched
# ---------------------------------------------------------------------------

@pytest.fixture
def db(monkeypatch, mariadb_or_skip):
    """Sockets may reach the MariaDB server only."""
    real_connect = socket.socket.connect
    hosts = {"127.0.0.1", "::1", "localhost", os.getenv("DB_HOST", "127.0.0.1")}
    port = int(os.getenv("DB_PORT", "3306"))

    def guarded(sock, address):
        if isinstance(address, tuple) and address[0] in hosts and address[1] == port:
            return real_connect(sock, address)
        raise OSError(f"network access is blocked in this test: {address!r}")

    monkeypatch.setattr(socket.socket, "connect", guarded)
    db = mariadb_or_skip
    keys, rows = (key_of(A), key_of(B)), (ROW_A, ROW_B)

    def cleanup():
        conn = db.get_db_connection()
        try:
            cur = conn.cursor()
            cur.execute("DELETE FROM review_decisions WHERE sku_key IN (%s, %s) OR `row_number` IN (%s, %s)",
                        keys + rows)
            cur.execute("DELETE FROM rejected_images WHERE sku_key IN (%s, %s)", keys)
            cur.execute("DELETE FROM resolved_products WHERE sku_key IN (%s, %s)", keys)
            cur.execute("DELETE FROM active_learning_feedback WHERE `row_number` IN (%s, %s)", rows)
            cur.execute("DELETE FROM curation_candidates WHERE `row_number` IN (%s, %s)", rows)
            cur.execute("DELETE FROM automation_queue WHERE `row_number` IN (%s, %s)", rows)
            conn.commit()
        finally:
            conn.close()

    cleanup()
    yield db
    cleanup()


def _snapshot(db):
    keys, rows = (key_of(A), key_of(B)), (ROW_A, ROW_B)
    conn = db.get_db_connection()
    try:
        cur = conn.cursor()
        out = {}
        for table, where, args in (
                ("rejected_images", "sku_key IN (%s, %s)", keys),
                ("resolved_products", "sku_key IN (%s, %s)", keys),
                ("review_decisions", "sku_key IN (%s, %s) OR `row_number` IN (%s, %s)", keys + rows),
                ("active_learning_feedback", "`row_number` IN (%s, %s)", rows),
                ("curation_candidates", "`row_number` IN (%s, %s)", rows),
                ("automation_queue", "`row_number` IN (%s, %s)", rows)):
            cur.execute(f"SELECT * FROM {table} WHERE {where} ORDER BY 1", args)
            out[table] = [dict(r) for r in cur.fetchall()]
        return out
    finally:
        conn.close()


def test_refused_requests_leave_the_database_untouched(db, sheet, tmp_path):
    cli_bridge, writes = sheet
    for p, row in ((A, ROW_A), (B, ROW_B)):
        db.add_to_queue(row, p["barcode"], p["product_name"], p["brand"], p["product_name"],
                        payload=main.task_payload(task_of(p, row)), sku_key=key_of(p))
        assert db.save_curation_candidates(row, p["product_name"], p["brand"],
                                           [{"url": IMAGE_A, "status": "preselected", "reasons": []}],
                                           sku_key=key_of(p), run_id="guard-run")
    before = _snapshot(db)
    assert len(before["automation_queue"]) == 2 and len(before["curation_candidates"]) == 2

    assert cli_bridge.action_select_image(stale(catalog_approve(B, ROW_B), A))["error"] == CHANGED
    assert cli_bridge.action_reject_image(stale(catalog_reject(B, ROW_B), A))["error"] == CHANGED
    assert cli_bridge.action_upload_manual_image(stale(upload(B, ROW_B, tmp_path), A))["error"] == CHANGED
    assert _snapshot(db) == before
    assert writes == []

    # the batch page's approval of the same row (no Arabic fields sent) goes through, under B's key only
    assert cli_bridge.action_select_image(batch_approve(B, ROW_B))["status"] == "success"
    assert db.get_cached_product(sku_key=key_of(B))["verification_status"] == "human_approved"
    assert db.get_cached_product(sku_key=key_of(A)) is None
