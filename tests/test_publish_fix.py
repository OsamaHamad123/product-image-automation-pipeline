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


def _alt_key(name, brand, barcode):
    import main
    return main.compute_alt_sku_key(main.sku_row(name, brand, barcode, {}))


KEYS = (_key(MILK["product_name"], MILK["brand"], GTIN),
        _key("Almarai Milk 1L", "Almarai", "", {"name_ar": "حليب كامل الدسم"}),
        _alt_key(MILK["product_name"], MILK["brand"], GTIN))


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
        _sql(db, f"DELETE FROM sheet_updates WHERE `row_number` IN ({marks})", ROWS)

    import google_sheets
    google_sheets.SQLiteTransactionQueue()            # the outbox table exists
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


# ---------------------------------------------------------------------------
# #3 / B: a rejection voids only the approval of the rejected image, judged from the approval record and the
# outbox's pending link writes (not only the live cell); it holds under the row's alternate key too
# ---------------------------------------------------------------------------

import google_sheets as _google_sheets  # noqa: E402

REAL_OUTBOX_OUTCOMES = _google_sheets.outbox_outcomes


@pytest.fixture
def outbox(bridge, monkeypatch):
    """The real outbox: an approval's link write waits there (PENDING); the live cell does not see it yet."""
    import google_sheets
    cli_bridge, env = bridge
    queue = google_sheets.SQLiteTransactionQueue()

    def enqueue(ws, row, col, value, **identity):
        env["sheet"].append((row, value, identity))
        queue.append_update(row, col, value, col_key="link", key_barcode=identity.get("barcode"),
                            key_name=identity.get("product_name"), key_size=identity.get("size"),
                            key_brand=identity.get("brand"))
        return True

    monkeypatch.setattr(google_sheets, "update_image_link", enqueue)
    monkeypatch.setattr(google_sheets, "outbox_outcomes", REAL_OUTBOX_OUTCOMES)
    return cli_bridge, env


def _reject_params(row, product, url, sku, barcode=GTIN, **extra):
    return dict(_approve_params(row, product, url, sku, barcode), reason_code="WRONG_VARIANT", **extra)


def test_a_stale_page_rejecting_the_old_image_never_voids_a_fresh_approval_of_another(db, outbox):
    cli_bridge, env = outbox
    row = ROWS[0]
    sku = _queue(db, row, MILK, GTIN, status="completed")
    old_link = CLOUD + "x.png"
    assert db.save_product_resolution(GTIN, MILK["product_name"], MILK["brand"], "https://x/x.jpg", old_link,
                                      verification_status="auto_verified", approved_by="auto", sku_key=sku)
    env["cells"][row] = old_link                                  # X is published
    stale_view = {"queue_status": "completed", "queue_updated_at": str(db.get_task_by_row(row)["updated_at"]),
                  "approved_url": old_link}

    # A approves Y: its link waits in the outbox, the cell still shows X
    env["link"] = CLOUD + "y.png"
    approved = cli_bridge.action_select_image(_approve_params(row, MILK, "https://x/y.jpg", sku, GTIN,
                                                              expected_state=stale_view))
    assert approved["status"] == "success"
    assert env["cells"][row] == old_link

    # B's page still shows X and rejects it
    result = cli_bridge.action_reject_image(_reject_params(row, MILK, old_link, sku))
    assert result["status"] == "success"
    assert result["superseded"] == 0 and result["approval_kept"] is True and result["sheet_cleared"] is False
    approval = db.get_cached_product(sku_key=sku)
    assert (approval["original_url"], approval["verification_status"]) == ("https://x/y.jpg", "human_approved")
    assert [v for _, v, _ in env["sheet"]] == [CLOUD + "y.png"]   # nothing cleared after A's write
    assert _status(db, row) == "completed"


def test_rejecting_an_approved_image_by_its_source_url_clears_its_cloudinary_link(db, outbox):
    cli_bridge, env = outbox
    row = ROWS[0]
    sku = _queue(db, row, MILK, GTIN, status="completed")
    link = CLOUD + "x.png"
    assert db.save_product_resolution(GTIN, MILK["product_name"], MILK["brand"], "https://x/x.jpg", link,
                                      verification_status="human_approved", approved_by="human", sku_key=sku)
    env["cells"][row] = link
    result = cli_bridge.action_reject_image(_reject_params(row, MILK, "https://x/x.jpg", sku))
    assert result["superseded"] == 1 and result["sheet_cleared"] is True
    assert [(r, v) for r, v, _ in env["sheet"]] == [(row, "")]
    assert db.get_cached_product(sku_key=sku) is None


def test_the_rejections_final_sheet_flush_runs_after_the_publish_lock_is_released(db, outbox, monkeypatch):
    """The reject checks and writes under the publish lock, but the outbox's final flush (Google calls, up to a few
    seconds) happens after it: another publish of the product does not wait for it."""
    import google_sheets
    cli_bridge, env = outbox
    row = ROWS[0]
    sku = _queue(db, row, MILK, GTIN, status="completed")
    link = CLOUD + "x.png"
    assert db.save_product_resolution(GTIN, MILK["product_name"], MILK["brand"], "https://x/x.jpg", link,
                                      verification_status="human_approved", approved_by="human", sku_key=sku)
    env["cells"][row] = link
    seen = []

    def flush(*a, **k):
        with db.sku_publish_lock(sku, timeout=0) as state:
            seen.append(state)

    monkeypatch.setattr(google_sheets, "stop_async_queue", flush)
    result = cli_bridge.action_reject_image(_reject_params(row, MILK, link, sku))
    assert result["sheet_cleared"] is True and seen == ["held"]


def test_a_rejection_voids_the_approval_stored_under_the_rows_key_before_its_barcode(db, outbox):
    import main
    cli_bridge, env = outbox
    row = ROWS[0]
    sku, alt = KEYS[0], KEYS[2]
    db.add_to_queue(row, GTIN, MILK["product_name"], MILK["brand"], "q", payload={}, sku_key=sku, alt_sku_key=alt)
    _sql(db, "UPDATE automation_queue SET status = 'completed' WHERE `row_number` = %s", (row,))
    link = CLOUD + "x.png"
    # approved before the owner added the barcode: stored under the name key
    assert db.save_product_resolution("", MILK["product_name"], MILK["brand"], "https://x/x.jpg", link,
                                      verification_status="human_approved", approved_by="human", sku_key=alt)
    assert main._servable_resolution(sku, alt, MILK["product_name"], MILK["brand"], None)["cloudinary_url"] == link
    env["cells"][row] = link
    result = cli_bridge.action_reject_image(_reject_params(row, MILK, link, sku))
    assert result["superseded"] == 1 and result["sheet_cleared"] is True
    # the relink path (the worker writing an approved link back) finds nothing to write
    assert main._servable_resolution(sku, alt, MILK["product_name"], MILK["brand"], None) is None
    assert link in db.get_rejections(alt)[0] and link in db.get_rejections(sku)[0]


# ---------------------------------------------------------------------------
# #4: the sheet outcome (C3) is this approval's own link write, read from the real outbox
# ---------------------------------------------------------------------------

def _set_outbox_status(db, ids, status):
    if not ids:
        return
    _sql(db,f"UPDATE sheet_updates SET sync_status = %s WHERE id IN ({', '.join(['%s'] * len(ids))})",
         (status,) + tuple(ids))


def _row_history(db, row):
    """The row's outbox history from earlier approvals: an old CONFLICT link write, a DEAD metadata write, and an
    earlier link write that a newer one superseded."""
    import google_sheets
    queue = google_sheets.SQLiteTransactionQueue()
    ident = {"key_barcode": GTIN, "key_name": MILK["product_name"], "key_brand": MILK["brand"]}
    conflict = queue.append_update(row, 3, CLOUD + "old1.png", col_key="link", **ident)
    dead = queue.append_update(row, 5, "Dairy", col_key="meta:category_l1_en", **ident)
    superseded = queue.append_update(row, 3, CLOUD + "old2.png", col_key="link", **ident)
    _set_outbox_status(db, [conflict], "CONFLICT")
    _set_outbox_status(db, [dead], "DEAD")
    _set_outbox_status(db, [superseded], "SUPERSEDED")


@pytest.fixture
def final_flush(db, outbox, monkeypatch):
    """stop_async_queue stands for the final flush: every pending write of the test rows is written, after
    `before` (a write that lands in the same flush) has run."""
    import google_sheets
    cli_bridge, env = outbox
    hooks = {"before": None}

    def flush(*a, **k):
        if hooks["before"]:
            hooks["before"]()
        marks = ", ".join(["%s"] * len(ROWS))
        pending = [r["id"] for r in _sql(db, f"SELECT id FROM sheet_updates WHERE sync_status = 'PENDING' "
                                             f"AND `row_number` IN ({marks}) ORDER BY id", ROWS)]
        if pending:
            _set_outbox_status(db, pending[:-1], "SUPERSEDED")       # one cell: the newest write wins
            _set_outbox_status(db, pending[-1:], "SYNCED")

    monkeypatch.setattr(google_sheets, "stop_async_queue", flush)
    return cli_bridge, env, hooks


def test_the_rows_old_outbox_history_does_not_change_this_approvals_outcome(db, final_flush):
    cli_bridge, env, hooks = final_flush
    row = ROWS[0]
    sku = _queue(db, row, MILK, GTIN)
    _row_history(db, row)
    result = cli_bridge.action_select_image(_approve_params(row, MILK, "https://x/a.jpg", sku, GTIN))
    assert result["status"] == "success" and result["sheet"] == "written"


def test_this_approvals_write_superseded_in_the_same_flush_is_not_reported_written(db, final_flush):
    import google_sheets
    cli_bridge, env, hooks = final_flush
    row = ROWS[0]
    sku = _queue(db, row, MILK, GTIN)
    _row_history(db, row)
    # another approval's write of the same cell lands after this one, in the same flush: the sheet holds the other
    hooks["before"] = lambda: google_sheets.SQLiteTransactionQueue().append_update(
        row, 3, CLOUD + "other.png", col_key="link", key_barcode=GTIN, key_name=MILK["product_name"],
        key_brand=MILK["brand"])
    result = cli_bridge.action_select_image(_approve_params(row, MILK, "https://x/a.jpg", sku, GTIN))
    assert result["status"] == "success" and result["sheet"] == "conflict"


def test_an_approval_whose_write_is_not_in_the_outbox_is_not_judged_by_the_rows_history(db, final_flush,
                                                                                       monkeypatch):
    import google_sheets
    cli_bridge, env, hooks = final_flush
    row = ROWS[0]
    sku = _queue(db, row, MILK, GTIN)
    _row_history(db, row)
    # the link went another way (the Redis write-behind channel): this request has no outbox record yet
    monkeypatch.setattr(google_sheets, "update_image_link", lambda *a, **k: True)
    result = cli_bridge.action_select_image(_approve_params(row, MILK, "https://x/a.jpg", sku, GTIN))
    assert result["status"] == "success" and result["sheet"] == "unknown"


# ---------------------------------------------------------------------------
# #5: C1 sees a rejection made after the page opened
# ---------------------------------------------------------------------------

def test_approving_an_image_another_reviewer_rejected_after_the_page_opened_is_refused(db, bridge):
    cli_bridge, env = bridge
    row = ROWS[0]
    sku = _queue(db, row, MILK, GTIN)
    pick, other = "https://x/pick.jpg", "https://x/other.jpg"
    assert db.save_curation_candidates(row, MILK["product_name"], MILK["brand"], [
        {"url": pick, "status": "preselected"}, {"url": other, "status": "eligible"}], sku_key=sku)
    seen = _page_view(db, row)                                  # both pages open
    rejected = cli_bridge.action_reject_image(dict(_approve_params(row, MILK, pick, sku, GTIN),
                                                   reason_code="WRONG_VARIANT", phash="0f0ff0f03c3ca5a5"))
    assert rejected["status"] == "success" and rejected["queue_status"] == "ready_for_review"
    assert _page_view(db, row) == seen                          # the rejection leaves the row as the pages saw it

    result = cli_bridge.action_select_image(_approve_params(row, MILK, pick, sku, GTIN, expected_state=seen))
    assert (result["status"], result["error_code"], result["reason"]) == ("failed", "state_changed", "image_rejected")
    assert result["current"]["rejected_image"] is True and result["current"]["queue_status"] == "ready_for_review"
    # replace (the stale-approval confirmation) does not re-approve a rejected image
    result = cli_bridge.action_select_image(_approve_params(row, MILK, pick, sku, GTIN, expected_state=seen,
                                                            replace=True))
    assert result["reason"] == "image_rejected"
    # the same picture under another address (its pHash) is the rejected image too
    result = cli_bridge.action_select_image(_approve_params(row, MILK, "https://mirror/pick.jpg", sku, GTIN,
                                                            expected_state=seen, phash="0f0ff0f03c3ca5a4"))
    assert result["reason"] == "image_rejected"
    assert env["sheet"] == [] and db.get_cached_product(sku_key=sku) is None
    # another image of the product is approved as usual
    assert cli_bridge.action_select_image(_approve_params(row, MILK, other, sku, GTIN,
                                                          expected_state=seen))["status"] == "success"


# ---------------------------------------------------------------------------
# #6 and the worker's unfinished outcomes: a pick rejected while it was processed, a busy lock, an approval
# ---------------------------------------------------------------------------

PICK, ALT_PICK, PICK_PHASH = "https://shop/milk-pick.jpg", "https://shop/milk-alt.jpg", "0f0ff0f03c3ca5a5"


@pytest.fixture
def worker(db, monkeypatch, tmp_path):
    """main.pre_cache_product_candidates on the real queue: an AUTO_PUBLISH search, stand-in processing and upload,
    a recorded sheet; env["during"] runs while the image is processed."""
    import cloudinary_storage
    import google_sheets
    import image_processor
    import image_search
    import local_cache_db
    import main
    from PIL import Image

    env = {"sheet": [], "during": None}

    def fake_search(query, name, brand, trace=None, **kw):
        trace["outcome"] = {"decision": "AUTO_PUBLISH"}
        return {"url": PICK, "decision": "AUTO_PUBLISH", "source": "serper", "phash": PICK_PHASH,
                "candidates": [{"url": PICK, "status": "preselected", "phash": PICK_PHASH},
                               {"url": ALT_PICK, "status": "eligible"}]}

    def processing(*a, **k):
        if env["during"]:
            env["during"]()
        out = tmp_path / f"canvas_{os.urandom(3).hex()}.png"
        Image.new("RGB", (800, 800), "white").save(out)
        return image_processor.ProcessResult(str(out), True, "photoroom", None, 800, 800)

    monkeypatch.setattr(image_search, "search_best_product_image", fake_search)
    monkeypatch.setattr(image_processor, "process_product_image_result", processing)
    monkeypatch.setattr(image_processor, "extract_metadata_from_image", lambda *a, **k: {})
    monkeypatch.setattr(cloudinary_storage, "upload_product_image_to_cloudinary", lambda *a, **k: CLOUD + "auto.png")
    monkeypatch.setattr(google_sheets, "update_image_link", lambda ws, row, col, value, **k: env["sheet"].append(
        (row, value)) or True)
    monkeypatch.setattr(google_sheets, "update_product_metadata", lambda *a, **k: True)
    monkeypatch.setattr(local_cache_db, "record_search_spend", lambda *a, **k: None)
    sku = _queue(db, ROWS[0], MILK, GTIN, status="pending")
    task = db.fetch_next_task("host:1")
    assert task["row_number"] == ROWS[0]

    def run():
        return main.pre_cache_product_candidates(task, worksheet=object(), link_column_index=5, sleep=lambda s: None)

    return run, env, sku


@pytest.mark.parametrize("by", ["url", "phash"])
def test_the_worker_never_publishes_a_pick_rejected_while_it_was_processed(db, worker, by):
    run, env, sku = worker
    if by == "url":
        env["during"] = lambda: db.add_rejected_image(sku, PICK, reason_code="WRONG_VARIANT")
    else:   # the same picture rejected under another address
        env["during"] = lambda: db.add_rejected_image(sku, "https://mirror/milk.jpg", phash=PICK_PHASH,
                                                      reason_code="WRONG_VARIANT")
    assert run() == "success"
    assert env["sheet"] == [] and db.get_cached_product(sku_key=sku) is None
    # the remaining candidate waits for a reviewer, without the rejected pick
    assert _status(db, ROWS[0]) == "ready_for_review"
    assert _candidate_urls(db, ROWS[0]) == [ALT_PICK]


def test_a_busy_publish_lock_requeues_the_row_instead_of_leaving_it_processing(db, worker, monkeypatch):
    import local_cache_db
    run, env, sku = worker
    real_lock = local_cache_db.sku_publish_lock
    monkeypatch.setattr(local_cache_db, "sku_publish_lock", lambda key, timeout=None: real_lock(key, timeout=1))
    holder = db.get_db_connection()
    try:
        holder.cursor().execute("SELECT GET_LOCK(%s, 0)",
                                (f"lq_publish:{sku}:{os.getenv('DB_DATABASE', 'automation_db')}"[:64],))
        assert run() == "success"
    finally:
        holder.close()
    task = db.get_task_by_row(ROWS[0])
    assert (task["status"], task["failure_code"]) == ("pending", "PUBLISH_BUSY")
    assert env["sheet"] == []


def test_an_approval_during_processing_finishes_the_workers_row(db, worker):
    run, env, sku = worker
    # a reviewer approved another image while the worker processed (without taking the worker's claim)
    env["during"] = lambda: db.save_product_resolution(GTIN, MILK["product_name"], MILK["brand"], "https://x/h.jpg",
                                                       CLOUD + "human.png", verification_status="human_approved",
                                                       approved_by="human", sku_key=sku)
    assert run() == "success"
    assert env["sheet"] == [] and _status(db, ROWS[0]) == "completed"
    assert db.get_cached_product(sku_key=sku)["cloudinary_url"] == CLOUD + "human.png"


# ---------------------------------------------------------------------------
# #7: sibling variants (same bottle, another label colour) are not the same image
# ---------------------------------------------------------------------------

def _bottle(path, label):
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (800, 800), "white")
    draw = ImageDraw.Draw(img)
    draw.rectangle([330, 100, 470, 160], fill=(240, 240, 240))           # cap
    draw.rectangle([300, 160, 500, 700], fill=(235, 235, 245))           # bottle
    draw.rectangle([300, 330, 500, 520], fill=label)                     # the flavour's label
    img.save(path)
    return str(path)


@pytest.fixture
def variants(db, monkeypatch, tmp_path):
    """auto_approve_product with the real database; the processing returns state['canvas']."""
    import cloudinary_storage
    import google_sheets
    import image_processor
    import local_cache_db
    import main

    state = {"canvas": None, "link": None, "sheet": []}

    def processing(*a, **k):
        out = tmp_path / f"canvas_{os.urandom(3).hex()}.png"
        with open(state["canvas"], "rb") as src, open(out, "wb") as dst:
            dst.write(src.read())
        return image_processor.ProcessResult(str(out), True, "photoroom", None, 800, 800)

    monkeypatch.setattr(image_processor, "process_product_image_result", processing)
    monkeypatch.setattr(image_processor, "extract_metadata_from_image", lambda *a, **k: {})
    monkeypatch.setattr(cloudinary_storage, "upload_product_image_to_cloudinary", lambda *a, **k: state["link"])
    monkeypatch.setattr(google_sheets, "update_image_link", lambda *a, **k: state["sheet"].append((a[1], a[3])) or True)
    monkeypatch.setattr(local_cache_db, "delete_product_failure", lambda *a, **k: True)

    def publish(n, name, canvas, link):
        state["canvas"], state["link"] = canvas, link
        task = {"id": 990000 + n, "row_number": ROWS[n], "product_name": name, "brand": "Almarai",
                "payload_json": "{}"}
        return main.auto_approve_product(task, {"url": f"https://shop/{n}.jpg"}, object(), 5, sku_key=KEYS[n])

    return publish, state


def test_a_sibling_variant_with_another_label_colour_is_not_a_duplicate(db, variants, tmp_path):
    from catalog_match.fetch import phash_hex
    from PIL import Image
    publish, state = variants
    strawberry = _bottle(tmp_path / "strawberry.png", (200, 30, 40))
    blueberry = _bottle(tmp_path / "blueberry.png", (40, 50, 170))
    with Image.open(strawberry) as a, Image.open(blueberry) as b:
        assert phash_hex(a.convert("RGB")) == phash_hex(b.convert("RGB"))     # pHash cannot tell them apart
    assert publish(0, "Juice Strawberry 1L", strawberry, CLOUD + "strawberry.png") == "published"
    assert publish(1, "Juice Blueberry 1L", blueberry, CLOUD + "blueberry.png") == "published"
    assert db.get_cached_product(sku_key=KEYS[0])["color_signature"]          # stored with the approval
    # the same strawberry picture for another product is still a duplicate (no auto-publish)
    assert publish(2, "Juice Mango 1L", strawberry, CLOUD + "strawberry-again.png") == "needs_review"
    assert [link for _, link in state["sheet"]] == [CLOUD + "strawberry.png", CLOUD + "blueberry.png"]


def test_the_colour_signature_survives_re_encoding_and_tells_label_colours_apart(tmp_path):
    import io
    import image_dedup_bktree as dedup
    from PIL import Image
    strawberry = Image.open(_bottle(tmp_path / "s.png", (200, 30, 40))).convert("RGB")
    buf = io.BytesIO()
    strawberry.save(buf, "JPEG", quality=60)
    copies = [Image.open(io.BytesIO(buf.getvalue())).convert("RGB"), strawberry.resize((300, 300)).resize((800, 800))]
    sig = dedup.color_signature(strawberry)
    assert not any(dedup.colors_differ(sig, dedup.color_signature(c)) for c in copies)
    for label in ((40, 50, 170), (40, 160, 60), (230, 120, 30)):                 # blue, green, orange
        other = Image.open(_bottle(tmp_path / "o.png", label)).convert("RGB")
        assert dedup.colors_differ(sig, dedup.color_signature(other))
    assert not dedup.colors_differ(sig, None) and not dedup.colors_differ("garbage", sig)


def test_an_image_published_before_colour_signatures_still_blocks_a_matching_phash(db, variants, tmp_path):
    publish, state = variants
    strawberry = _bottle(tmp_path / "strawberry.png", (200, 30, 40))
    blueberry = _bottle(tmp_path / "blueberry.png", (40, 50, 170))
    assert publish(0, "Juice Strawberry 1L", strawberry, CLOUD + "strawberry.png") == "published"
    _sql(db, "UPDATE resolved_products SET color_signature = NULL WHERE sku_key = %s", (KEYS[0],))
    # no colour to compare with: fail closed, as before
    assert publish(1, "Juice Blueberry 1L", blueberry, CLOUD + "blueberry.png") == "needs_review"
    # the same Cloudinary link is always the same image
    assert publish(2, "Juice Mango 1L", blueberry, CLOUD + "strawberry.png") == "needs_review"


# ---------------------------------------------------------------------------
# C: a human approval of a cut-out with gate flags: the flags are said, presentation-only flags can be published
# after an explicit confirmation, and the database never says approved while the sheet says needs_review:
# ---------------------------------------------------------------------------

@pytest.fixture
def gate(monkeypatch, tmp_path):
    """The image processing stand-in returns a canvas with state['isolated'] / state['flags'] (a perfectly cut-out
    400 px bottle comes back upscaled, not isolated)."""
    import image_processor
    from PIL import Image

    state = {"isolated": False, "flags": ["upscaled"], "notes": None, "uploads": 0}

    def processing(*a, **k):
        out = tmp_path / f"canvas_{os.urandom(3).hex()}.png"
        Image.new("RGB", (800, 800), "white").save(out)
        result = image_processor.ProcessResult(str(out), state["isolated"], "photoroom", None, 800, 800,
                                               quality_flags=list(state["flags"]))
        if state["notes"] is not None:
            result.quality_notes = list(state["notes"])     # the image package's non-blocking notes, when present
        return result

    monkeypatch.setattr(image_processor, "process_product_image_result", processing)
    return state


def test_publish_image_for_a_human_names_the_flags_and_publishes_clean_only_when_confirmed(db, monkeypatch, gate):
    import cloudinary_storage
    import google_sheets
    import image_processor
    import main

    sheet = []
    monkeypatch.setattr(image_processor, "extract_metadata_from_image", lambda *a, **k: {})
    monkeypatch.setattr(cloudinary_storage, "upload_product_image_to_cloudinary",
                        lambda *a, **k: gate.update(uploads=gate["uploads"] + 1) or CLOUD + "bottle.png")
    monkeypatch.setattr(google_sheets, "update_image_link", lambda *a, **k: sheet.append(a[3]) or True)

    def publish(**kw):
        return main.publish_image("https://x/bottle.jpg", MILK["product_name"], MILK["brand"], ROWS[0], object(), 3,
                                  sku_key=KEYS[0], unclean="refuse", **kw)

    res = publish()
    assert (res["status"], res["error"], res["quality_flags"]) == ("quality_refused", "quality_flags", ["upscaled"])
    assert res["publish_anyway_allowed"] is True and sheet == [] and gate["uploads"] == 0
    res = publish(publish_anyway=True)
    assert (res["status"], res["sheet_value"], res["quality_flags"]) == ("published", CLOUD + "bottle.png", ["upscaled"])
    assert res["published_anyway"] is True and sheet == [CLOUD + "bottle.png"]
    # a clipped product is never published clean, confirmed or not
    gate["flags"] = ["upscaled", "edge_clipped"]
    res = publish(publish_anyway=True)
    assert (res["status"], res["publish_anyway_allowed"]) == ("quality_refused", False)
    # the worker path is unchanged: written needs_review:, flags returned
    gate["flags"], gate["notes"] = ["alpha_haze"], ["soft_shadow"]
    res = main.publish_image("https://x/bottle.jpg", MILK["product_name"], MILK["brand"], ROWS[0], object(), 3,
                             sku_key=KEYS[0])
    assert res["status"] == "needs_review" and res["sheet_value"].startswith("needs_review:")
    assert res["quality_flags"] == ["alpha_haze"] and res["quality_notes"] == ["soft_shadow"]


def test_an_approval_with_presentation_flags_waits_for_the_reviewers_confirmation(db, bridge, gate):
    cli_bridge, env = bridge
    row = ROWS[0]
    sku = _queue(db, row, MILK, GTIN)
    params = _approve_params(row, MILK, "https://x/bottle.jpg", sku, GTIN)

    result = cli_bridge.action_select_image(dict(params))
    assert (result["status"], result["error_code"]) == ("failed", "quality_flags")
    assert result["quality_flags"] == ["upscaled"] and result["publish_anyway_allowed"] is True
    # nothing written and nothing recorded: the product is still waiting for review
    assert env["sheet"] == [] and db.get_cached_product(sku_key=sku) is None
    assert _status(db, row) == "ready_for_review" and result["current"]["queue_status"] == "ready_for_review"

    result = cli_bridge.action_select_image(dict(params, publish_anyway=True))
    assert result["status"] == "success" and result["published_anyway"] is True
    assert result["quality_flags"] == ["upscaled"] and result["warnings"] == ["quality_flags"]
    assert result["sheet_value"] == env["link"] and [v for _, v, _ in env["sheet"]] == [env["link"]]
    approval = db.get_cached_product(sku_key=sku)
    assert (approval["verification_status"], approval["cloudinary_url"]) == ("human_approved", env["link"])
    assert _status(db, row) == "completed"


def test_an_upload_whose_background_removal_failed_is_never_recorded_as_approved(db, bridge, gate, tmp_path):
    from PIL import Image
    cli_bridge, env = bridge
    row = ROWS[0]
    sku = _queue(db, row, MILK, GTIN)
    gate["flags"] = []                                    # no isolation at all
    upload = tmp_path / "manual.png"
    Image.new("RGB", (400, 400), "white").save(upload)
    params = {"file_path": str(upload), "row_number": row, "product_name": MILK["product_name"],
              "brand": MILK["brand"], "barcode": GTIN, "sku_key": sku, "publish_anyway": "1"}
    result = cli_bridge.action_upload_manual_image(params)
    assert (result["status"], result["error_code"], result["publish_anyway_allowed"]) == (
        "failed", "background_failed", False)
    assert env["sheet"] == [] and db.get_cached_product(sku_key=sku) is None
    assert _status(db, row) == "ready_for_review"
