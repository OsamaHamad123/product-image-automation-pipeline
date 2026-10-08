"""«باركودات من صفحات المتاجر»: the barcode a store page stated, written into the sheet's barcode column on request.

* barcode_suggestions (read only, from the cached sheet rows, else the queue): approved rows whose page_gtin is set and
  whose barcode cell is empty; a row with a valid barcode is left out; a GTIN two rows share (or another row already
  holds) and a cell holding text are listed apart and never written;
* barcode_write: the checksum and the stored page_gtin are checked again, the barcode column is found by its header
  (no column: refused, nothing added), one outbox write per row carries the row's identity (name, size, brand), and
  the flush skips a row whose product moved or whose barcode cell is no longer empty (google_sheets FILL_ONLY_KEYS);
* after the write the product keeps its approval, its rejections and its review decisions: the queue row takes the
  barcode key with the old one as its alternative (local_cache_db.rekey_queue_rows, also done by the nightly enqueue),
  and the worker, the review screen and «تراجع عن الرفض» look under both keys.
"""

import pytest

from test_sheets_safety import FakeWorksheet, _final_status, _flush, _outbox_row, gs  # noqa: F401 (fixture)

PAGE = "https://www.carrefouruae.com/mafuae/en/p/123"
HEAD = ["Barcode", "Product Name", "Brand", "Size", "Drive Image Link"]
GTIN_A, GTIN_B, GTIN_C, GTIN_D = "6281007035323", "6281007035330", "6281007035347", "6281007035354"
OWN_GTIN, SHEET_GTIN = "6281007035309", "6281007035316"
BAD_CHECK = "6281007035324"
# row -> (barcode cell, name, brand, size); row 3 got a barcode after its approval, 4 and 5 share one page barcode,
# row 6 holds text in its barcode cell
SHEET = {2: ("", "PBW GOLDEN PRIZE TUNA 185G", "PBW GOLDEN PRIZE", "185G"),
         3: (SHEET_GTIN, "PBW BAYARA CHICKPEAS 400G", "PBW BAYARA", "400G"),
         4: ("", "PBW ALALI TUNA 170G", "PBW ALALI", "170G"),
         5: ("", "PBW ALALI TUNA IN OIL 170G", "PBW ALALI", "170G"),
         6: ("N/A", "PBW CALIFORNIA GARDEN BEANS 400G", "PBW CALIFORNIA GARDEN", "400G"),
         7: ("", "PBW PUCK CREAM 170G", "PBW PUCK", "170G")}
PAGE_GTINS = {2: GTIN_A, 3: OWN_GTIN, 4: GTIN_B, 5: GTIN_B, 6: GTIN_C, 7: GTIN_D}


def _key(row, barcode=None):
    import main
    cell, name, brand, size = SHEET[row]
    return main.compute_sku_key(main.sku_row(name, brand, cell if barcode is None else barcode,
                                             {"name_ar": "", "brand_ar": "", "category": "", "size": size}))


def _sheet_rows(overrides=None):
    rows = [list(HEAD)]
    for row in sorted(SHEET):
        cell, name, brand, size = SHEET[row]
        rows.append([cell, name, brand, size, "https://res.cloudinary.com/pbw/%d.png" % row])
    for row, values in (overrides or {}).items():
        rows[row - 1] = list(values)
    return rows


def _cache(gs):
    products = [{"row_number": row, "product_name": name, "product_name_ar": "", "brand": brand, "brand_ar": "",
                 "size": size, "category": "", "barcode": cell} for row, (cell, name, brand, size) in SHEET.items()]
    gs._write_cache("products_cache.json", {"products": products, "link_idx": 4}, gs.PRODUCTS_CACHE_VERSION)


def _sql(db, statement, params=()):
    conn = db.get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(statement, params)
            rows = cur.fetchall()
        conn.commit()
        return rows
    finally:
        conn.close()


@pytest.fixture
def world(mariadb_or_skip, monkeypatch, tmp_path):
    import google_sheets
    db = mariadb_or_skip
    keys = [_key(row, "") for row in SHEET]
    gtin_keys = ["0" + g for g in (GTIN_A, GTIN_D)]
    google_sheets.SQLiteTransactionQueue()                      # the outbox table (sheet_updates)

    def wipe():
        marks = ",".join(["%s"] * len(keys + gtin_keys))
        _sql(db, f"DELETE FROM resolved_products WHERE sku_key IN ({marks})", tuple(keys + gtin_keys))
        _sql(db, f"DELETE FROM rejected_images WHERE sku_key IN ({marks})", tuple(keys + gtin_keys))
        _sql(db, f"DELETE FROM review_decisions WHERE sku_key IN ({marks})", tuple(keys + gtin_keys))
        _sql(db, "DELETE FROM automation_queue WHERE `row_number` BETWEEN 2 AND 7")
        _sql(db, "DELETE FROM sheet_updates WHERE sync_status IN ('PENDING', 'FAILED') OR `row_number` BETWEEN 2 AND 7")

    wipe()
    monkeypatch.setattr(google_sheets, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(google_sheets, "_queue", None)
    monkeypatch.setattr(google_sheets, "_outcome_hook", lambda outcome: None)
    google_sheets._header_cache.clear()
    for row, gtin in PAGE_GTINS.items():
        cell, name, brand, _size = SHEET[row]
        db.save_product_resolution("", name, brand, "https://a.ae/%d.jpg" % row, "https://res.cloudinary.com/pbw/%d.png" % row,
                                   verification_status="human_approved", approved_by="human", sku_key=_key(row, ""),
                                   page_gtin=gtin, page_gtin_url=PAGE)
    _cache(google_sheets)
    yield db, google_sheets
    wipe()


def _bridge(monkeypatch, sheet):
    import cli_bridge
    monkeypatch.setattr(cli_bridge, "_open_sheet", lambda: sheet)
    return cli_bridge


# ---------------------------------------------------------------------------
# suggestions
# ---------------------------------------------------------------------------

def test_suggestions_leave_out_rows_with_a_barcode_and_list_duplicates_and_filled_cells_apart(world, monkeypatch):
    db, gs = world
    cli_bridge = _bridge(monkeypatch, None)
    out = cli_bridge.action_barcode_suggestions({})
    assert out["status"] == "success" and out["source"] == "sheet_cache"
    assert [r["row"] for r in out["rows"]] == [2, 7]
    assert out["rows"][0] == {"row": 2, "sku_key": _key(2), "name": SHEET[2][1], "brand": SHEET[2][2], "size": "185G",
                              "gtin": GTIN_A, "domain": "carrefouruae.com", "page_url": PAGE, "sheet_barcode": "",
                              "duplicate": False, "reason": None}
    assert [(r["row"], r["duplicate"]) for r in out["duplicates"]] == [(4, True), (5, True)]
    assert [(r["row"], r["sheet_barcode"], r["reason"]) for r in out["filled"]] == [(6, "N/A", "cell_not_empty")]
    assert 3 not in {r["row"] for r in out["rows"] + out["duplicates"] + out["filled"]}     # it has a barcode already
    assert out["count"] == 5

    # a page barcode another row of the sheet already holds is a duplicate too
    SHEET_ROWS = dict(SHEET)
    monkeypatch.setitem(SHEET, 3, (GTIN_D, SHEET_ROWS[3][1], SHEET_ROWS[3][2], SHEET_ROWS[3][3]))
    _cache(gs)
    out = cli_bridge.action_barcode_suggestions({})
    assert [r["row"] for r in out["rows"]] == [2] and [r["row"] for r in out["duplicates"]] == [4, 5, 7]

    # no cached sheet rows: the queue's copy of each row, never Google
    monkeypatch.setitem(SHEET, 3, SHEET_ROWS[3])
    gs.clear_cache()
    for row in (2, 3):
        cell, name, brand, size = SHEET[row]
        db.add_many_to_queue([db.queue_input(row, cell, name, brand, name, payload={"size": size},
                                             sku_key=_key(row), alt_sku_key=_key(row, ""))])
    out = cli_bridge.action_barcode_suggestions({})
    mine = [r for r in out["rows"] if r["sku_key"] in {_key(row, "") for row in SHEET}]
    assert out["source"] == "queue" and [r["row"] for r in mine] == [2] and mine[0]["size"] == "185G"


# ---------------------------------------------------------------------------
# the write
# ---------------------------------------------------------------------------

def _item(row, gtin=None):
    return {"row": row, "sku_key": _key(row, ""), "gtin": gtin or PAGE_GTINS[row]}


def test_the_write_refuses_a_bad_checksum_a_mismatch_a_duplicate_and_a_filled_cell(world, monkeypatch):
    def no_google():
        raise AssertionError("nothing to write: Google is not opened")
    cli_bridge = _bridge(monkeypatch, None)
    monkeypatch.setattr(cli_bridge, "_open_sheet", no_google)
    out = cli_bridge.action_barcode_write({"items": [_item(2, BAD_CHECK), _item(7, GTIN_A), _item(4), _item(6),
                                                     _item(3)]})
    assert (out["status"], out["written"], out["queued"]) == ("success", 0, 0)
    assert [(s["row"], s["reason"]) for s in out["skipped"]] == [
        (2, "bad_checksum"), (7, "gtin_mismatch"), (4, "duplicate"), (6, "cell_not_empty"), (3, "row_changed")]
    for bad in (None, [], [{"row": 1, "sku_key": "x", "gtin": GTIN_A}], [{"row": 2, "gtin": GTIN_A}], ["2"],
                [_item(2)] * 301):
        assert cli_bridge.action_barcode_write({"items": bad})["status"] == "invalid"


def test_a_sheet_without_a_barcode_column_is_refused_and_nothing_is_queued(world, monkeypatch):
    db, _gs = world
    sheet = FakeWorksheet([[c for c in r[1:]] for r in _sheet_rows()])          # no Barcode column
    cli_bridge = _bridge(monkeypatch, sheet)
    before = db.outbox_max_id()
    out = cli_bridge.action_barcode_write({"items": [_item(2)]})
    assert (out["status"], out["code"]) == ("refused", "no_barcode_column")
    assert out["error"] == cli_bridge.NO_BARCODE_COLUMN_ERROR and "ما منضيف أعمدة" in out["error"]
    assert db.outbox_max_id() == before and sheet.sent_bodies == [] and sheet.updated_cells == []


def test_the_write_queues_the_row_identity_and_writes_only_empty_cells_of_the_same_product(world, monkeypatch):
    db, _gs = world
    # row 7 moved: another product now sits there and it is nowhere else; nothing is written over it
    sheet = FakeWorksheet(_sheet_rows({7: ["", "PBW OTHER PRODUCT", "PBW OTHER", "1KG", ""]}))
    cli_bridge = _bridge(monkeypatch, sheet)
    out = cli_bridge.action_barcode_write({"items": [_item(2), _item(7)]})
    assert (out["written"], out["queued"], out["rows_written"]) == (1, 0, [2])
    assert out["skipped"] == [{"row": 7, "sku_key": _key(7, ""), "gtin": GTIN_D, "reason": "row_changed"}]
    sent = [(d["range"], d["values"]) for body in sheet.sent_bodies for d in body["data"]]
    assert sent == [("'Products'!A2", [[GTIN_A]])]
    queued = {r["row_number"]: r for r in _sql(db, "SELECT * FROM sheet_updates WHERE `row_number` IN (2, 7)")}
    assert (queued[2]["col_key"], queued[2]["key_name"], queued[2]["key_size"], queued[2]["key_brand"],
            queued[2]["key_barcode"]) == ("barcode", SHEET[2][1], "185G", SHEET[2][2], None)
    assert queued[7]["sync_status"] == "CONFLICT" and "mismatch" in queued[7]["last_error"]


def test_a_cell_filled_since_the_rows_were_read_is_not_overwritten(world, monkeypatch):
    sheet = FakeWorksheet(_sheet_rows({7: [OWN_GTIN] + _sheet_rows()[6][1:]}))
    cli_bridge = _bridge(monkeypatch, sheet)
    out = cli_bridge.action_barcode_write({"items": [_item(7)]})
    assert out["written"] == 0 and [s["reason"] for s in out["skipped"]] == ["cell_not_empty"]
    assert sheet.sent_bodies == []


def test_the_flush_writes_a_barcode_only_into_an_empty_cell(gs, fake_connection):
    ws = FakeWorksheet([HEAD, ["", "Fresh Milk", "Almarai", "1L", ""], ["123", "Laban", "Almarai", "1L", ""],
                        [GTIN_A, "Juice", "Almarai", "1L", ""]])
    pending = [_outbox_row(1, 2, GTIN_A, name="Fresh Milk", size="1L", brand="Almarai", col_key="barcode"),
               _outbox_row(2, 3, GTIN_B, name="Laban", size="1L", brand="Almarai", col_key="barcode"),
               _outbox_row(3, 4, GTIN_A, name="Juice", size="1L", brand="Almarai", col_key="barcode")]
    status = _final_status(_flush(gs, ws, pending, fake_connection))
    assert status == {1: "SYNCED", 2: "CONFLICT", 3: "SYNCED"}                  # 3: the same value is there already
    assert [d["range"] for body in ws.sent_bodies for d in body["data"]] == ["'Products'!A2"]
    assert gs.reported[-1]["error"].startswith("cell_not_empty")


def test_queued_barcode_writes_carry_the_identity_and_skip_redis(gs, monkeypatch):
    calls = []

    class Queue:
        def append_update(self, *args, **kwargs):
            calls.append((args, kwargs))
            return len(calls)

    monkeypatch.setattr(gs, "_queue", Queue())
    monkeypatch.setattr(gs, "_redis_write_behind", lambda *a, **k: pytest.fail("barcode writes never go through Redis"))
    ids = gs.queue_barcode_writes([{"row_number": 9, "value": GTIN_A, "barcode": "", "product_name": "Milk",
                                    "size": "1L", "brand": "Almarai"}])
    assert ids == {9: 1}
    assert calls == [((9, None, GTIN_A), {"col_key": "barcode", "key_barcode": None, "key_name": "Milk",
                                          "key_size": "1L", "key_brand": "Almarai"})]


# ---------------------------------------------------------------------------
# after the write: the product keeps its approval, rejections and review decisions
# ---------------------------------------------------------------------------

def test_after_the_write_the_approval_and_rejections_are_found_under_the_barcode_key(world, monkeypatch):
    import main
    db, _gs = world
    old_key, new_key = _key(2, ""), "0" + GTIN_A
    cell, name, brand, size = SHEET[2]
    db.add_many_to_queue([db.queue_input(2, "", name, brand, name, payload={"size": size}, sku_key=old_key)])
    _sql(db, "UPDATE automation_queue SET status = 'completed' WHERE `row_number` = 2")
    rejected = "https://shop.example/wrong-tuna.jpg"
    assert db.add_rejected_image(old_key, rejected, reason_code="WRONG_PRODUCT")
    db.add_review_decision("rejected", sku_key=old_key, row_number=2, brand=brand, product_name=name,
                           image_url=rejected, reason_code="WRONG_PRODUCT")
    cli_bridge = _bridge(monkeypatch, FakeWorksheet(_sheet_rows()))
    assert cli_bridge.action_barcode_write({"items": [_item(2)]})["written"] == 1

    task = db.get_task_by_row(2)
    assert (task["sku_key"], task["alt_sku_key"], task["barcode"]) == (new_key, old_key, GTIN_A)
    row = main.sku_row(name, brand, GTIN_A, {"size": size})
    assert main.compute_sku_key(row) == new_key and main.compute_alt_sku_key(row) == old_key    # the nightly enqueue
    alt = main._task_alt_key(task, row, new_key)
    assert alt == old_key and main._has_human_approval(new_key, alt)
    assert any("wrong-tuna" in u for u in main._rejections(new_key, alt)[0])
    assert main.rejected_image(new_key, alt, rejected) and not main.rejected_image(new_key, None, rejected)
    assert {r["sku_key"]: r["row_number"] for r in db.get_page_barcodes()}[old_key] == 2   # export keeps the row

    # the review screen: the approval guard and «تراجع عن الرفض» (rejected_images and review_decisions)
    current, approval = cli_bridge._current_state(new_key, 2, name)
    assert approval and current["approved_url"] == "https://res.cloudinary.com/pbw/2.png"
    undone = cli_bridge.action_undo_reject({"sku_key": new_key, "image_url": rejected, "row_number": 2})
    assert (undone["status"], undone["removed"], undone["decision_undone"]) == ("success", 1, True)

    # written now: it is no longer suggested
    monkeypatch.setitem(SHEET, 2, (GTIN_A,) + SHEET[2][1:])
    _cache(_gs)
    assert 2 not in {r["row"] for r in cli_bridge.action_barcode_suggestions({})["rows"]}


def test_the_nightly_enqueue_rekeys_a_finished_row_that_got_a_barcode(world):
    import main
    db, _gs = world
    old_key = _key(7, "")
    cell, name, brand, size = SHEET[7]
    db.add_many_to_queue([db.queue_input(7, "", name, brand, name, payload={"size": size}, sku_key=old_key)])
    prod = {"row_number": 7, "product_name": name, "brand": brand, "barcode": GTIN_D, "size": size,
            "existing_image_link": "https://res.cloudinary.com/pbw/7.png", "needs_review": False}
    rows, stats = main.plan_enqueue([prod])
    assert rows == [] and stats["skipped_final"] == 1
    assert stats["rekey"] == [(7, old_key, "0" + GTIN_D, GTIN_D)]
    assert db.rekey_queue_rows(stats["rekey"]) == 1 and db.rekey_queue_rows(stats["rekey"]) == 0
    assert db.get_task_by_row(7)["alt_sku_key"] == old_key


# ---------------------------------------------------------------------------
# The store's own category columns: filled from the approval's category, only when empty (owner decision 2026-10-08)
# ---------------------------------------------------------------------------

CAT_HEAD = ["Barcode", "Product Name", "Brand", "Size", "Category", "Category Arabic", "Sub Category",
            "Sub Category Arabic", "Sub Sub Category", "Sub Sub Category Arabic", "Drive Image Link"]


def test_the_flush_fills_an_empty_store_category_and_keeps_the_owners_value_without_a_conflict(gs, fake_connection):
    ws = FakeWorksheet([CAT_HEAD, ["", "Fresh Milk", "Almarai", "1L", "", "", "", "", "", "", ""],
                        ["", "Laban", "Almarai", "1L", "Dairy (owner)", "", "", "", "", "", ""]])
    pending = [_outbox_row(1, 2, "Eggs & Dairy", name="Fresh Milk", size="1L", brand="Almarai", col_key="category"),
               _outbox_row(2, 2, "حليب", name="Fresh Milk", size="1L", brand="Almarai", col_key="sub_category_ar"),
               _outbox_row(3, 3, "Eggs & Dairy", name="Laban", size="1L", brand="Almarai", col_key="category")]
    status = _final_status(_flush(gs, ws, pending, fake_connection))
    assert status == {1: "SYNCED", 2: "SYNCED", 3: "SYNCED"}                    # 3: the owner's value stays, no conflict
    assert sorted(d["range"] for body in ws.sent_bodies for d in body["data"]) == ["'Products'!E2", "'Products'!H2"]


def test_approval_metadata_queues_the_store_category_columns_that_exist(gs, monkeypatch):
    ws = FakeWorksheet([CAT_HEAD, ["", "Fresh Milk", "Almarai", "1L", "", "", "", "", "", "", ""]])
    queued = []

    class Queue:
        def append_update(self, row, col, value, col_name=None, col_key=None, **_identity):
            queued.append((col_key, col_name, value))

    monkeypatch.setattr(gs, "_queue", Queue())
    monkeypatch.setattr(gs, "_worker", object())
    monkeypatch.setattr(gs, "_redis_write_behind", lambda *a, **k: False)
    meta = {"category_l1_en": "Eggs & Dairy", "category_l1_ar": "الألبان والبيض", "category_l2_en": "milk",
            "category_l2_ar": "حليب", "category_l3_en": "Fresh", "category_l3_ar": "طازج"}
    assert gs.update_product_metadata(ws, 2, meta, product_name="Fresh Milk", size="1L", brand="Almarai")
    store = {k: (n, v) for k, n, v in queued if not k.startswith(gs.META_PREFIX)}
    assert store == {"category": ("Category", "Eggs & Dairy"), "category_ar": ("Category Arabic", "الألبان والبيض"),
                     "sub_category": ("Sub Category", "milk"), "sub_category_ar": ("Sub Category Arabic", "حليب"),
                     "sub_sub_category": ("Sub Sub Category", "Fresh"),
                     "sub_sub_category_ar": ("Sub Sub Category Arabic", "طازج")}


def test_a_sheet_without_store_category_columns_gets_none_added(gs, monkeypatch):
    ws = FakeWorksheet([HEAD, ["", "Fresh Milk", "Almarai", "1L", ""]])
    queued = []

    class Queue:
        def append_update(self, row, col, value, col_name=None, col_key=None, **_identity):
            queued.append(col_key)

    monkeypatch.setattr(gs, "_queue", Queue())
    monkeypatch.setattr(gs, "_worker", object())
    monkeypatch.setattr(gs, "_redis_write_behind", lambda *a, **k: False)
    assert gs.update_product_metadata(ws, 2, {"category_l1_en": "Eggs & Dairy"}, product_name="Fresh Milk")
    assert all(k.startswith(gs.META_PREFIX) for k in queued)                    # only «Category L1 EN» (created)
    assert "Category" not in ws.row_values(1)                                   # the store column is never created
