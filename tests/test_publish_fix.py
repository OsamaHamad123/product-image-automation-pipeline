"""Publish path review fixes (wp/p4-publish-fix) on the real MariaDB test database (skips when MariaDB is down).

The sheet, the image processing and the upload are recorders or stand-ins; the queue, the approvals, the
rejections, the candidates and the publish lock are the real tables and the real MariaDB named locks.
Each test failed before its fix.
"""

import json
import os
import socket
import threading
import time

import pytest

ROWS = (950001, 950002, 950003, 950004)
GTIN = "6281007000024"
MILK = {"product_name": "Fresh Milk Full Fat 1L", "brand": "Almarai"}
LABAN = {"product_name": "Laban Up Strawberry 200ml", "brand": "Nadec"}
CLOUD = "https://res.cloudinary.com/demo/image/upload/products/"


def _sql(db, statement, params=()):
    conn = db.get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute(statement, params)
        rows = cur.fetchall()
        conn.commit()
        return rows
    finally:
        conn.close()


def _key(name, brand, barcode="", payload=None):
    import main
    return main.compute_sku_key(main.sku_row(name, brand, barcode, payload or {}))


KEYS = (_key(MILK["product_name"], MILK["brand"], GTIN),
        _key("Almarai Milk 1L", "Almarai", "", {"name_ar": "حليب كامل الدسم"}))


@pytest.fixture
def db_only(monkeypatch):
    real_connect = socket.socket.connect
    hosts = {"127.0.0.1", "::1", "localhost", os.getenv("DB_HOST", "127.0.0.1")}
    port = int(os.getenv("DB_PORT", "3306"))

    def guarded(sock, address):
        if isinstance(address, tuple) and address[0] in hosts and address[1] == port:
            return real_connect(sock, address)
        raise OSError(f"network access is blocked in this test: {address!r}")

    monkeypatch.setattr(socket.socket, "connect", guarded)
    return guarded


@pytest.fixture
def db(db_only, mariadb_or_skip):
    db = mariadb_or_skip
    marks = ", ".join(["%s"] * len(ROWS))
    keys = ", ".join(["%s"] * len(KEYS))

    def wipe():
        _sql(db, f"DELETE FROM automation_queue WHERE `row_number` IN ({marks}) OR sku_key IN ({keys})", ROWS + KEYS)
        _sql(db, f"DELETE FROM curation_candidates WHERE `row_number` IN ({marks}) OR sku_key IN ({keys})",
             ROWS + KEYS)
        for table in ("resolved_products", "rejected_images", "review_decisions"):
            _sql(db, f"DELETE FROM {table} WHERE sku_key IN ({keys})", KEYS)
        _sql(db, f"DELETE FROM active_learning_feedback WHERE `row_number` IN ({marks})", ROWS)

    wipe()
    yield db
    wipe()


def _queue(db, row, product, barcode="", status="ready_for_review", payload=None):
    sku = _key(product["product_name"], product["brand"], barcode, payload)
    db.add_to_queue(row, barcode, product["product_name"], product["brand"], "q", payload=payload or {}, sku_key=sku)
    _sql(db, "UPDATE automation_queue SET status = %s WHERE `row_number` = %s", (status, row))
    return sku


def _status(db, row):
    return (db.get_task_by_row(row) or {}).get("status")


def _candidate_urls(db, row):
    return [r["image_url"] for r in _sql(db, "SELECT image_url FROM curation_candidates WHERE `row_number` = %s "
                                             "ORDER BY id", (row,))]


@pytest.fixture
def bridge(db, monkeypatch, tmp_path):
    """cli_bridge on the real database; the sheet, processing and upload are recorders."""
    import cli_bridge
    import cloudinary_storage
    import google_sheets
    import image_processor
    from PIL import Image

    env = {"sheet": [], "cells": {}, "link": CLOUD + "milk.png", "write_delay": 0.0, "color": "white"}

    def processing(*a, **k):
        out = tmp_path / f"canvas_{os.urandom(4).hex()}.png"
        Image.new("RGB", (800, 800), env["color"]).save(out)
        return image_processor.ProcessResult(str(out), True, "photoroom", None, 800, 800)

    class Cell:
        def __init__(self, value):
            self.value = value

    class Sheet:
        title = "Products"

        def cell(self, row, col):
            return Cell(env["cells"].get(row, ""))

    def write(ws, row, col, value, **identity):
        if env["write_delay"]:
            time.sleep(env["write_delay"])
        env["sheet"].append((row, value, identity))
        env["cells"][row] = value
        return True

    monkeypatch.setattr(cli_bridge, "LOG_PATH", str(tmp_path / "search.log"))
    monkeypatch.setattr(image_processor, "process_product_image_result", processing)
    monkeypatch.setattr(image_processor, "extract_metadata_from_image", lambda *a, **k: {})
    monkeypatch.setattr(cloudinary_storage, "upload_product_image_to_cloudinary", lambda *a, **k: env["link"])
    monkeypatch.setattr(google_sheets, "init_async_queue", lambda *a, **k: None)
    monkeypatch.setattr(google_sheets, "stop_async_queue", lambda *a, **k: None)
    monkeypatch.setattr(google_sheets, "outbox_outcomes", lambda *a, **k: [])
    monkeypatch.setattr(google_sheets, "get_sheets_client", lambda: object())
    monkeypatch.setattr(google_sheets, "get_brand_mappings", lambda *a, **k: {})
    monkeypatch.setattr(google_sheets, "open_worksheet", lambda client, name: Sheet())
    monkeypatch.setattr(google_sheets, "find_link_column", lambda worksheet, create=True: 3)
    monkeypatch.setattr(google_sheets, "update_image_link", write)
    monkeypatch.setattr(google_sheets, "update_product_metadata", lambda *a, **k: True)
    return cli_bridge, env


def _approve_params(row, product, url, sku, barcode="", **extra):
    return dict({"image_url": url, "product_name": product["product_name"], "brand": product["brand"],
                 "row_number": row, "barcode": barcode, "sku_key": sku}, **extra)


# ---------------------------------------------------------------------------
# #2 / A: a shared barcode (or a shared key without the Arabic name) is not the same product
# ---------------------------------------------------------------------------

def test_approving_one_product_never_writes_another_product_sharing_its_barcode(db, bridge):
    cli_bridge, env = bridge
    milk_row, laban_row = ROWS[0], ROWS[1]
    sku = _queue(db, milk_row, MILK, GTIN)
    assert _queue(db, laban_row, LABAN, GTIN) == sku                    # the same GTIN key
    assert db.save_curation_candidates(milk_row, MILK["product_name"], MILK["brand"],
                                       [{"url": "https://x/milk.jpg"}], sku_key=sku)
    assert db.save_curation_candidates(laban_row, LABAN["product_name"], LABAN["brand"],
                                       [{"url": "https://x/laban.jpg"}], sku_key=sku)
    # the worker saving the milk's candidates again keeps the laban's
    assert db.save_curation_candidates(milk_row, MILK["product_name"], MILK["brand"],
                                       [{"url": "https://x/milk2.jpg"}], sku_key=sku)
    assert _candidate_urls(db, laban_row) == ["https://x/laban.jpg"]

    result = cli_bridge.action_select_image(_approve_params(milk_row, MILK, "https://x/milk2.jpg", sku, GTIN))
    assert result["status"] == "success" and result["rows_written"] == [milk_row]
    assert [r for r, _, _ in env["sheet"]] == [milk_row]
    assert (_status(db, milk_row), _status(db, laban_row)) == ("completed", "ready_for_review")
    assert _candidate_urls(db, laban_row) == ["https://x/laban.jpg"]


def test_rejecting_one_product_never_requeues_another_product_sharing_its_barcode(db, bridge):
    cli_bridge, env = bridge
    milk_row, laban_row = ROWS[0], ROWS[1]
    sku = _queue(db, milk_row, MILK, GTIN)
    _queue(db, laban_row, LABAN, GTIN)
    env["cells"][laban_row] = "https://x/milk.jpg"                       # the other product's cell is not ours
    assert db.save_curation_candidates(milk_row, MILK["product_name"], MILK["brand"],
                                       [{"url": "https://x/milk.jpg", "status": "preselected"}], sku_key=sku)
    assert db.save_curation_candidates(laban_row, LABAN["product_name"], LABAN["brand"],
                                       [{"url": "https://x/laban.jpg", "status": "preselected"}], sku_key=sku)
    result = cli_bridge.action_reject_image(dict(_approve_params(milk_row, MILK, "https://x/milk.jpg", sku, GTIN),
                                                 reason_code="WRONG_VARIANT"))
    assert result["status"] == "success" and result["queue_status"] == "pending"
    assert result["candidates_left"] == 0                                # the laban's candidate is not ours
    assert (_status(db, milk_row), _status(db, laban_row)) == ("pending", "ready_for_review")
    assert env["sheet"] == []


def test_no_barcode_rows_with_another_arabic_variant_are_another_product(db, bridge):
    cli_bridge, env = bridge
    full = {"product_name": "Almarai Milk 1L", "brand": "Almarai"}
    sku = _queue(db, ROWS[0], full, payload={"name_ar": "حليب كامل الدسم", "size": "1L"})
    assert _queue(db, ROWS[1], full, payload={"name_ar": "حليب قليل الدسم", "size": "1L"}) == sku
    assert _queue(db, ROWS[2], full, payload={"name_ar": "حليب كامل الدسم", "size": "1L"}) == sku    # a duplicate
    result = cli_bridge.action_select_image(_approve_params(ROWS[0], full, "https://x/full.jpg", sku, size="1L",
                                                            product_name_ar="حليب كامل الدسم"))
    assert result["status"] == "success" and result["rows_written"] == [ROWS[0], ROWS[2]]
    assert [_status(db, r) for r in ROWS[:3]] == ["completed", "ready_for_review", "completed"]


def test_the_worker_never_writes_or_settles_another_product_sharing_its_barcode(db, monkeypatch):
    import google_sheets
    import image_search
    import main

    nido = {"product_name": "Nido Milk Powder 900g", "brand": "Nido"}
    maggi = {"product_name": "Maggi Chicken Stock Cubes 20g", "brand": "Maggi"}
    sku = _queue(db, ROWS[0], nido, GTIN, status="pending")
    _queue(db, ROWS[1], maggi, GTIN, status="pending")
    _queue(db, ROWS[2], dict(nido, product_name="NIDO MILK POWDER 900G"), GTIN, status="pending")   # a duplicate
    leader = db.fetch_next_task("host:1")
    assert leader["row_number"] == ROWS[0]
    # another product does not wait for the nido's search (one claim per product, not per barcode)
    other = db.fetch_next_task("host:2")
    assert other is not None and other["row_number"] == ROWS[1]
    _sql(db, "UPDATE automation_queue SET status = 'pending', worker_id = NULL WHERE `row_number` = %s", (ROWS[1],))

    link = CLOUD + "nido.png"

    def fake_search(query, name, brand, trace=None, **kw):
        trace["outcome"] = {"decision": "AUTO_PUBLISH"}
        return {"url": "https://shop/nido.jpg", "decision": "AUTO_PUBLISH", "source": "serper",
                "candidates": [{"url": "https://shop/nido.jpg", "status": "preselected"}]}

    def fake_auto(task, best_image, ws, col, sku_key=None):
        db.save_product_resolution(GTIN, task["product_name"], task["brand"], best_image["url"], link,
                                   verification_status="auto_verified", approved_by="auto", sku_key=sku_key)
        return "published"

    links = []
    monkeypatch.setattr(image_search, "search_best_product_image", fake_search)
    monkeypatch.setattr(main, "auto_approve_product", fake_auto)
    monkeypatch.setattr(google_sheets, "update_image_link",
                        lambda ws, row, col, value, **k: links.append((row, value)) or True)
    monkeypatch.setattr(google_sheets, "update_product_metadata", lambda *a, **k: True)
    assert main.pre_cache_product_candidates(leader, worksheet=object(), link_column_index=5,
                                             sleep=lambda s: None) == "success"
    assert links == [(ROWS[2], link)]                                       # the nido duplicate only
    assert [_status(db, r) for r in ROWS[:3]] == ["completed", "pending", "completed"]

    # a result that is not a publish (ready for review) is not inherited by the other product either
    _sql(db, "UPDATE automation_queue SET status = 'pending' WHERE `row_number` IN (%s, %s)", (ROWS[0], ROWS[2]))
    task = db.fetch_next_task("host:1")
    assert task["row_number"] == ROWS[0]
    assert db.update_task_status(task["id"], "ready_for_review", claim_id=task["worker_id"])
    assert [_status(db, r) for r in ROWS[:3]] == ["ready_for_review", "pending", "ready_for_review"]


# ---------------------------------------------------------------------------
# #1: two reviewers approving at once; the worker's save racing a reviewer's
# ---------------------------------------------------------------------------

def _page_view(db, row):
    task = db.get_task_by_row(row)
    return {"queue_status": task["status"], "queue_updated_at": str(task["updated_at"]), "approved_url": None}


def test_two_reviewers_approving_at_once_the_second_is_refused_and_db_and_sheet_agree(db, bridge, monkeypatch,
                                                                                         tmp_path):
    import cloudinary_storage
    import hashlib
    import image_processor
    import local_cache_db
    from PIL import Image

    cli_bridge, env = bridge
    row = ROWS[0]
    sku = _queue(db, row, MILK, GTIN)
    seen = _page_view(db, row)                     # both pages opened before either approval
    both_checked = threading.Barrier(2, timeout=10)

    def processing(image_url, *a, **k):
        both_checked.wait()                        # both passed the first C1 check: they race for the publish lock
        tag = hashlib.md5(image_url.encode()).hexdigest()[:8]
        out = tmp_path / f"canvas_{tag}_{os.urandom(3).hex()}.png"
        Image.new("RGB", (800, 800), "white").save(out)
        return image_processor.ProcessResult(str(out), True, "photoroom", None, 800, 800)

    real_save = local_cache_db.save_product_resolution

    def slow_save(*a, **k):
        time.sleep(0.2)                            # the decision record takes a moment (a busy database)
        return real_save(*a, **k)

    env["write_delay"] = 0.15                      # the sheet enqueue takes 150 ms
    monkeypatch.setattr(image_processor, "process_product_image_result", processing)
    monkeypatch.setattr(cloudinary_storage, "upload_product_image_to_cloudinary",
                        lambda path, *a, **k: CLOUD + os.path.basename(path).split("_")[1] + ".png")
    monkeypatch.setattr(local_cache_db, "save_product_resolution", slow_save)
    results = {}

    def approve(who, url):
        results[who] = cli_bridge.action_select_image(_approve_params(row, MILK, url, sku, GTIN, expected_state=seen))

    threads = [threading.Thread(target=approve, args=(who, f"https://x/{who}.jpg")) for who in ("a", "b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    statuses = sorted(r["status"] for r in results.values())
    assert statuses == ["failed", "success"], results
    refused = next(r for r in results.values() if r["status"] == "failed")
    assert refused["error_code"] in ("already_approved", "state_changed")
    assert len(env["sheet"]) == 1                                   # one write, the winner's
    approval = db.get_cached_product(sku_key=sku)
    assert approval["verification_status"] == "human_approved"
    assert approval["cloudinary_url"] == env["cells"][row]          # the database and the sheet agree
    assert refused["current"]["approved_url"] == env["cells"][row]


def test_a_workers_late_save_never_overwrites_a_human_approval_being_written(db):
    """The guard and the write are one transaction: the worker's save waits for the reviewer's and then sees it."""
    sku = KEYS[0]
    assert db.save_product_resolution(GTIN, MILK["product_name"], MILK["brand"], "https://src/auto1.jpg",
                                      CLOUD + "auto1.png", verification_status="auto_verified", approved_by="auto",
                                      sku_key=sku)
    reviewer = db.get_db_connection()
    try:
        cur = reviewer.cursor()
        # the reviewer's approval is being written (not committed yet)
        cur.execute("UPDATE resolved_products SET verification_status = 'human_approved', approved_by = 'human', "
                    "cloudinary_url = %s, original_url = 'https://src/human.jpg' WHERE sku_key = %s",
                    (CLOUD + "human.png", sku))
        result = {}
        worker = threading.Thread(target=lambda: result.update(saved=db.save_product_resolution(
            GTIN, MILK["product_name"], MILK["brand"], "https://src/auto2.jpg", CLOUD + "auto2.png",
            verification_status="auto_verified", approved_by="auto", sku_key=sku)))
        worker.start()
        time.sleep(0.5)                             # the worker reads the row now, while the approval is in flight
        reviewer.commit()
        worker.join(30)
    finally:
        reviewer.close()
    assert result["saved"] is False
    cached = db.get_cached_product(sku_key=sku)
    assert (cached["cloudinary_url"], cached["verification_status"]) == (CLOUD + "human.png", "human_approved")
