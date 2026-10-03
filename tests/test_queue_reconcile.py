"""Reconcile at enqueue, stable keys and failure records (work package P4a).

(a) a row without a final link whose product has an approved / auto-verified image gets that link written
    (a 'relink' task, no search); a write still in the sheet outbox is left alone; a link that landed and was
    then cleared by someone is searched again for review only.
(b) a row whose link was published for another product (the row was edited) is searched again for review
    only, never auto-published over; with a human approval for the new product its link is written.
(c) a completed row whose sheet write ended CONFLICT / DEAD gets the write again; without an approval it is
    searched for review.
Stable keys: decisions stored under the key a row had before a valid barcode was added still apply.
product_failures: one record per product (sku_key), never per junk barcode cell.
"""

import json

import pytest

ROW = 962000
LINK = "https://res.cloudinary.com/p4q/approved.png"


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


def _prod(i, name, brand="P4Q Brand", barcode="", link="", size="", needs_review=False):
    return {"row_number": ROW + i, "product_name": name, "brand": brand, "barcode": barcode, "size": size,
            "existing_image_link": link, "needs_review": needs_review}


def _keys(prod):
    import main
    row = main.sku_row(prod["product_name"], prod["brand"], prod["barcode"], {"size": prod.get("size", "")})
    return main.compute_sku_key(row), main.compute_alt_sku_key(row)


@pytest.fixture
def db(mariadb_or_skip, monkeypatch):
    import google_sheets
    db = mariadb_or_skip

    def wipe():
        _sql(db, "DELETE FROM automation_queue")
        _sql(db, "DELETE FROM resolved_products WHERE product_name LIKE 'P4Q %%'")
        _sql(db, "DELETE FROM rejected_images WHERE original_url LIKE 'https://p4q.example/%%'")
        _sql(db, "DELETE FROM curation_candidates WHERE product_name LIKE 'P4Q %%'")
        _sql(db, "DELETE FROM product_failures WHERE product_name LIKE 'P4Q %%'")

    wipe()
    # the sheet outbox of the tests: {row_number: [records]} (google_sheets.outbox_outcomes of the sheets package)
    outbox = {}
    monkeypatch.setattr(google_sheets, "outbox_outcomes", lambda rows: {r: outbox[r] for r in rows if r in outbox},
                        raising=False)
    db.test_outbox = outbox
    yield db
    wipe()


def _approve(db, prod, status="human_approved", link=LINK, key=None):
    assert db.save_product_resolution(prod["barcode"], prod["product_name"], prod["brand"], "https://p4q.example/a.jpg",
                                      link, verification_status=status, approved_by="human",
                                      sku_key=key or _keys(prod)[0])


def _queue_row(db, i):
    rows = _sql(db, "SELECT * FROM automation_queue WHERE `row_number` = %s", (ROW + i,))
    return rows[0] if rows else None


@pytest.fixture
def sheet(monkeypatch):
    """google_sheets writes and the search, recorded; the search must not run in a relink."""
    import google_sheets
    import image_search

    rec = {"links": [], "meta": [], "searches": []}
    monkeypatch.setattr(google_sheets, "update_image_link",
                        lambda ws, row, col, value, **k: rec["links"].append((row, value, k)) or True)
    monkeypatch.setattr(google_sheets, "update_product_metadata",
                        lambda ws, row, md, **k: rec["meta"].append((row, md)) or True)

    def search(query, name, brand, trace=None, **kw):
        rec["searches"].append(kw)
        trace["outcome"] = rec.get("outcome", {"decision": "REVIEW_PRESELECTED"})
        return rec.get("best")

    monkeypatch.setattr(image_search, "search_best_product_image", search)
    return rec


# ---------------------------------------------------------------------------
# (a) approved image, no link in the row
# ---------------------------------------------------------------------------

def test_row_without_link_gets_the_approved_image_without_a_search(db, sheet):
    import main
    prod = _prod(0, "P4Q Fresh Laban 1L", size="1L")
    _approve(db, prod)
    rows, stats = main.plan_enqueue([prod])
    (row,) = rows
    assert (row["task_kind"], row["requeue_reason"], row["review_only"]) == ("relink", "APPROVED_IMAGE", 0)
    assert stats["relink"] == 1
    db.add_many_to_queue(rows)
    task = db.fetch_next_task("host:1")
    assert main.pre_cache_product_candidates(task, worksheet=object(), link_column_index=4,
                                             sleep=lambda s: None) == "success"
    assert sheet["searches"] == []                                              # no money spent
    assert sheet["links"] == [(ROW, LINK, {"barcode": "", "product_name": "P4Q Fresh Laban 1L", "size": "1L",
                                           "brand": "P4Q Brand"})]
    assert _queue_row(db, 0)["status"] == "completed"


def test_a_review_cell_is_relinked_only_from_a_human_approval(db):
    import main
    prod = _prod(0, "P4Q Fresh Laban 1L", needs_review=True)
    _approve(db, prod, status="auto_verified")
    (row,), _ = main.plan_enqueue([prod])
    assert row["task_kind"] is None                                             # a normal search for review
    _approve(db, prod, status="human_approved")
    (row,), _ = main.plan_enqueue([prod])
    assert row["task_kind"] == "relink"


def test_a_relink_whose_approval_was_withdrawn_searches_instead(db, sheet):
    import main
    prod = _prod(0, "P4Q Fresh Laban 1L")
    _approve(db, prod)
    rows, _ = main.plan_enqueue([prod])
    db.add_many_to_queue(rows)
    db.supersede_resolution(_keys(prod)[0])                                    # a reviewer rejected it meanwhile
    task = db.fetch_next_task("host:1")
    main.pre_cache_product_candidates(task, worksheet=object(), link_column_index=4, sleep=lambda s: None)
    assert len(sheet["searches"]) == 1 and sheet["links"] == []


@pytest.mark.parametrize("state, expected", [
    ("PENDING", None),                                       # still in the outbox: left alone
    ("FAILED", None),
    ("SYNCED", ("search", 1, "LINK_CLEARED")),               # landed, then someone cleared the cell
    ("CONFLICT", ("relink", 0, "SHEET_WRITE_RETRY")),
    ("DEAD", ("relink", 0, "SHEET_WRITE_RETRY")),
])
def test_outbox_outcome_decides_what_happens_to_a_missing_link(db, state, expected):
    import main
    prod = _prod(0, "P4Q Fresh Laban 1L")
    _approve(db, prod)
    db.test_outbox[ROW] = [{"value": "needs_review:https://old/x.png", "sync_status": "SYNCED", "id": 1},
                           {"value": LINK, "sync_status": state, "id": 2}]
    rows, stats = main.plan_enqueue([prod])
    if expected is None:
        assert rows == [] and stats["in_flight"] == 1
        return
    (row,) = rows
    assert (row["task_kind"] or "search", row["review_only"], row["requeue_reason"]) == expected


def test_outbox_outcomes_in_other_shapes_and_the_table_fallback(db, monkeypatch):
    import google_sheets
    import local_cache_db
    import main
    monkeypatch.setattr(google_sheets, "outbox_outcomes", lambda rows: [{"row_number": ROW, "status": "dead"}])
    assert main._outbox_records([ROW]) == {ROW: [{"value": None, "status": "DEAD", "id": 0}]}
    monkeypatch.setattr(google_sheets, "outbox_outcomes", lambda: {ROW: "CONFLICT"})       # no argument
    assert main._link_write_state(main._outbox_records([ROW])[ROW], LINK) == "CONFLICT"
    monkeypatch.delattr(google_sheets, "outbox_outcomes")
    monkeypatch.setattr(local_cache_db, "outbox_link_writes",
                        lambda rows: [{"id": 9, "row_number": ROW, "value": LINK, "sync_status": "SYNCED"}])
    assert main._link_write_state(main._outbox_records([ROW])[ROW], LINK) == "SYNCED"


# ---------------------------------------------------------------------------
# (b) the row was edited after its image was published
# ---------------------------------------------------------------------------

def test_a_row_edited_after_publishing_is_searched_for_review_only(db):
    import main
    old = _prod(0, "P4Q Almarai Fresh Milk 1L", brand="Almarai")
    _approve(db, old, link="https://res.cloudinary.com/p4q/milk.png")
    edited = dict(old, product_name="P4Q Almarai Laban 1L", existing_image_link="https://res.cloudinary.com/p4q/milk.png")
    (row,), stats = main.plan_enqueue([edited])
    assert (row["task_kind"], row["review_only"], row["requeue_reason"]) == (None, 1, "ROW_EDITED")
    assert stats["edited"] == 1

    # its own published link: skipped as before
    rows, stats = main.plan_enqueue([dict(old, existing_image_link="https://res.cloudinary.com/p4q/milk.png")])
    assert rows == [] and stats["skipped_final"] == 1
    # the stored key differs (older key formula) but the product did not change: not an edit
    _sql(db, "UPDATE resolved_products SET sku_key = 'p4q-legacy-key' WHERE product_name = %s", (old["product_name"],))
    rows, stats = main.plan_enqueue([dict(old, existing_image_link="https://res.cloudinary.com/p4q/milk.png")])
    assert rows == [] and stats["edited"] == 0
    # a link nobody published (typed by hand): skipped
    rows, _ = main.plan_enqueue([dict(edited, existing_image_link="https://elsewhere.example/x.png")])
    assert rows == []


def test_an_edited_row_whose_new_product_is_approved_gets_that_link(db):
    import main
    old = _prod(0, "P4Q Almarai Fresh Milk 1L", brand="Almarai")
    _approve(db, old, link="https://res.cloudinary.com/p4q/milk.png")
    edited = dict(old, product_name="P4Q Almarai Laban 1L", existing_image_link="https://res.cloudinary.com/p4q/milk.png")
    _approve(db, edited, link="https://res.cloudinary.com/p4q/laban.png")
    (row,), _ = main.plan_enqueue([edited])
    assert (row["task_kind"], row["requeue_reason"]) == ("relink", "ROW_EDITED")


def test_a_review_only_row_is_never_auto_published(offline, monkeypatch):
    import image_search
    import local_cache_db
    import main
    statuses, auto = [], []
    monkeypatch.setattr(local_cache_db, "update_task_status", lambda *a, **k: statuses.append(a[1]) or True)
    monkeypatch.setattr(local_cache_db, "get_rejections", lambda sku: ([], []))
    monkeypatch.setattr(local_cache_db, "save_curation_candidates", lambda *a, **k: True)
    monkeypatch.setattr(main, "auto_approve_product", lambda *a, **k: auto.append(a) or "published")
    # offline: an unreadable approval cache fails closed (no auto-publish at all), so the cache answers "no approval"
    monkeypatch.setattr(main, "_has_human_approval", lambda *a, **k: False)

    def search(query, name, brand, trace=None, **kw):
        trace["outcome"] = {"decision": "AUTO_PUBLISH"}
        return {"url": "https://shop/x.jpg", "decision": "AUTO_PUBLISH", "source": "serper",
                "candidates": [{"url": "https://shop/x.jpg", "status": "preselected"}]}

    monkeypatch.setattr(image_search, "search_best_product_image", search)
    task = {"id": 1, "row_number": 9, "product_name": "Laban 1L", "brand": "B", "sku_key": "k", "review_only": 1,
            "requeue_reason": "ROW_EDITED"}
    main.pre_cache_product_candidates(task, worksheet=object(), link_column_index=3, sleep=lambda s: None)
    assert auto == [] and statuses == ["ready_for_review"]
    main.pre_cache_product_candidates(dict(task, review_only=0), worksheet=object(), link_column_index=3,
                                      sleep=lambda s: None)
    assert len(auto) == 1                                      # the same result on a normal row publishes


def test_rows_of_a_product_follow_its_review_only_row(db):
    import main
    old = _prod(0, "P4Q Almarai Fresh Milk 1L", brand="Almarai")
    _approve(db, old, link="https://res.cloudinary.com/p4q/milk.png", status="superseded")
    edited = dict(old, product_name="P4Q Almarai Laban 1L", existing_image_link="https://res.cloudinary.com/p4q/milk.png")
    twin = _prod(1, "P4Q Almarai Laban 1L", brand="Almarai")          # the same product on another row, no link
    rows, _ = main.plan_enqueue([edited, twin])
    assert [r["review_only"] for r in rows] == [1, 1]


# ---------------------------------------------------------------------------
# (c) completed rows whose link never reached the sheet
# ---------------------------------------------------------------------------

def test_a_completed_row_whose_write_died_gets_it_again(db):
    import main
    prod = _prod(0, "P4Q Shan Meat Masala 100g")
    _approve(db, prod)
    key = _keys(prod)[0]
    db.add_to_queue(ROW, "", prod["product_name"], prod["brand"], "q", sku_key=key)
    _sql(db, "UPDATE automation_queue SET status = 'completed' WHERE `row_number` = %s", (ROW,))
    db.test_outbox[ROW] = [{"value": LINK, "sync_status": "DEAD", "id": 3}]
    rows, _ = main.plan_enqueue([prod])
    counts = db.add_many_to_queue(rows)
    assert counts["reset"] == 1
    row = _queue_row(db, 0)
    assert (row["status"], row["task_kind"], row["requeue_reason"]) == ("pending", "relink", "SHEET_WRITE_RETRY")


def test_a_completed_row_without_link_or_approval_is_searched_for_review(db):
    import main
    prod = _prod(0, "P4Q Shan Meat Masala 100g")
    key = _keys(prod)[0]
    db.add_to_queue(ROW, "", prod["product_name"], prod["brand"], "q", sku_key=key)
    _sql(db, "UPDATE automation_queue SET status = 'completed' WHERE `row_number` = %s", (ROW,))
    rows, stats = main.plan_enqueue([prod])
    assert stats["missing"] == 1
    db.add_many_to_queue(rows)
    row = _queue_row(db, 0)
    assert (row["status"], row["review_only"], row["requeue_reason"]) == ("pending", 1, "LINK_MISSING")
    # a completed row without the evidence stays completed (the old rule)
    _sql(db, "UPDATE automation_queue SET status = 'completed' WHERE `row_number` = %s", (ROW,))
    db.add_to_queue(ROW, "", prod["product_name"], prod["brand"], "q", sku_key=key)
    assert _queue_row(db, 0)["status"] == "completed"


# ---------------------------------------------------------------------------
# stable keys: a valid barcode added later
# ---------------------------------------------------------------------------

def test_the_alternative_key_is_the_key_the_row_had_before_its_barcode():
    import main
    row = main.sku_row("Coca Cola Regular 330ml", "Coca Cola", "", {"size": ""})
    before = main.compute_sku_key(row)
    with_barcode = dict(row, barcode="5449000000996")
    assert main.compute_sku_key(with_barcode) == "05449000000996"
    assert main.compute_alt_sku_key(with_barcode) == before
    assert main.compute_alt_sku_key(row) == before
    arabic_brand = main.sku_row("كوكا كولا 330 مل", "", "5449000000996", {"brand_ar": "كوكا كولا"})
    assert main.compute_alt_sku_key(arabic_brand) == main.compute_sku_key(dict(arabic_brand, barcode=""))


def test_decisions_under_the_key_before_the_barcode_still_apply(db, sheet, monkeypatch):
    import main
    before = _prod(0, "P4Q Cola Regular 330ml")
    old_key = _keys(before)[0]
    assert db.add_rejected_image(old_key, "https://p4q.example/wrong-can.jpg", phash="ffff0000ffff0000",
                                 reason_code="WRONG_SIZE")
    after = dict(before, barcode="5449000000996")
    new_key, alt_key = _keys(after)
    assert new_key == "05449000000996" and alt_key == old_key

    # the enqueue keeps the old key next to the new one, and the search excludes the old rejection
    rows, _ = main.plan_enqueue([after])
    db.add_many_to_queue(rows)
    task = db.fetch_next_task("host:1")
    assert (task["sku_key"], task["alt_sku_key"]) == (new_key, old_key)
    sheet["best"] = {"url": "https://shop/x.jpg", "decision": "AUTO_PUBLISH", "source": "serper",
                     "candidates": [{"url": "https://shop/x.jpg", "status": "preselected"}]}
    sheet["outcome"] = {"decision": "AUTO_PUBLISH"}
    _approve(db, before, key=old_key, link="https://res.cloudinary.com/p4q/cola.png")   # a human approval, old key
    assert main._has_human_approval(new_key, alt_key) is True
    assert main._has_human_approval(new_key) is False
    published = []
    monkeypatch.setattr(main, "auto_approve_product", lambda *a, **k: published.append(a) or "published")
    main.pre_cache_product_candidates(task, worksheet=object(), link_column_index=4, sleep=lambda s: None)
    (kw,) = sheet["searches"]
    assert kw["exclude_urls"] == ["https://p4q.example/wrong-can.jpg"]
    assert kw["exclude_phashes"] == ["ffff0000ffff0000"]
    assert published == []                           # never auto-published over the human approval
    assert _queue_row(db, 0)["status"] == "ready_for_review"


def test_an_approval_under_the_old_key_is_written_to_the_row_with_its_new_barcode(db):
    import main
    before = _prod(0, "P4Q Cola Regular 330ml")
    _approve(db, before)                              # approved while the row had no barcode
    (row,), _ = main.plan_enqueue([dict(before, barcode="5449000000996")])
    assert row["task_kind"] == "relink" and row["sku_key"] == "05449000000996"


# ---------------------------------------------------------------------------
# product_failures: one record per product
# ---------------------------------------------------------------------------

def _failures(db):
    return {r["barcode"]: (r["sku_key"], r["error_message"])
            for r in _sql(db, "SELECT * FROM product_failures WHERE product_name LIKE 'P4Q %%'")}


def test_failures_of_products_sharing_a_junk_barcode_do_not_overwrite_each_other(db):
    db.save_product_failure("N/A", "P4Q One", "B1", "NO_RESULTS: one", sku_key="p4q-f1")
    db.save_product_failure("N/A", "P4Q Two", "B2", "NO_RESULTS: two", sku_key="p4q-f2")
    assert _failures(db) == {"ERR_P4Q_One_B1": ("p4q-f1", "NO_RESULTS: one"),
                             "ERR_P4Q_Two_B2": ("p4q-f2", "NO_RESULTS: two")}
    listed = db.get_product_failures()
    assert listed["p4q-f1"]["error_message"] == "NO_RESULTS: one" and "N/A" not in listed
    db.save_product_failure("N/A", "P4Q One", "B1", "ALL_CONFLICTED: again", sku_key="p4q-f1")
    assert len(_failures(db)) == 2 and _failures(db)["ERR_P4Q_One_B1"][1] == "ALL_CONFLICTED: again"
    db.delete_product_failure("N/A", sku_key="p4q-f1")
    assert list(_failures(db)) == ["ERR_P4Q_Two_B2"]


def test_same_name_and_brand_with_another_size_keeps_both_records(db):
    db.save_product_failure("", "P4Q Juice", "B", "NO_RESULTS: 1L", sku_key="p4q-j1")
    db.save_product_failure("", "P4Q Juice", "B", "NO_RESULTS: 2L", sku_key="p4q-j2")
    assert sorted(v for v in _failures(db).values()) == [("p4q-j1", "NO_RESULTS: 1L"), ("p4q-j2", "NO_RESULTS: 2L")]


def test_a_valid_barcode_keys_its_record_and_success_clears_it(db):
    db.save_product_failure("6281007000024", "P4Q Fresh Milk", "Almarai", "NO_RESULTS: x", sku_key="06281007000024")
    assert list(_failures(db)) == ["6281007000024"]
    assert db.save_product_resolution("6281007000024", "P4Q Fresh Milk", "Almarai", "https://p4q.example/m.jpg",
                                      "https://res/m.png", verification_status="human_approved",
                                      sku_key="06281007000024")
    assert _failures(db) == {}


def test_a_legacy_junk_barcode_record_is_moved_to_its_product_key(db):
    _sql(db, "INSERT INTO product_failures (barcode, product_name, brand, error_message) "
             "VALUES ('N/A', 'P4Q Legacy', 'B', 'NO_RESULTS: old')")
    assert db.init_db()
    assert _failures(db) == {"ERR_P4Q_Legacy_B": (None, "NO_RESULTS: old")}
    assert db.init_db() and _failures(db) == {"ERR_P4Q_Legacy_B": (None, "NO_RESULTS: old")}   # idempotent


def test_the_worker_records_a_failure_under_the_product_key(offline, monkeypatch):
    import image_search
    import local_cache_db
    import main
    saved = []
    monkeypatch.setattr(local_cache_db, "update_task_status", lambda *a, **k: True)
    monkeypatch.setattr(local_cache_db, "get_rejections", lambda sku: ([], []))
    monkeypatch.setattr(local_cache_db, "save_product_failure", lambda *a, **k: saved.append((a, k)) or True)

    def not_found(query, name, brand, trace=None, **kw):
        trace["outcome"] = {"decision": "NOT_FOUND", "failure_code": "NO_RESULTS", "provider_health": []}
        return None

    monkeypatch.setattr(image_search, "search_best_product_image", not_found)
    task = {"id": 1, "row_number": 2, "product_name": "Milk", "brand": "B", "barcode": "N/A", "sku_key": "k-milk"}
    assert main.pre_cache_product_candidates(task, sleep=lambda s: None) == "failed"
    ((args, kwargs),) = saved
    assert args[0] == "N/A" and kwargs == {"sku_key": "k-milk"}


# ---------------------------------------------------------------------------
# the worker's search outcome handling
# ---------------------------------------------------------------------------

def _wired(monkeypatch, outcome, best=None):
    import image_search
    import local_cache_db
    rec = {"status": [], "failures": []}
    monkeypatch.setattr(local_cache_db, "update_task_status",
                        lambda task_id, status, *a, **k: rec["status"].append((status, k.get("failure_code"))) or True)
    monkeypatch.setattr(local_cache_db, "get_rejections", lambda sku: ([], []))
    monkeypatch.setattr(local_cache_db, "save_product_failure", lambda *a, **k: rec["failures"].append(a) or True)
    monkeypatch.setattr(local_cache_db, "save_curation_candidates", lambda *a, **k: True)

    def search(query, name, brand, trace=None, **kw):
        trace["outcome"] = dict(outcome)
        return best

    monkeypatch.setattr(image_search, "search_best_product_image", search)
    return rec


TASK = {"id": 1, "row_number": 2, "product_name": "Milk", "brand": "B", "sku_key": "k-milk"}
CREDIT_GONE = {"decision": "NOT_FOUND", "failure_code": "NO_RESULTS",
               "provider_health": [{"provider": "serper", "status": "quota", "http_status": 400},
                                   {"provider": "cse_legacy", "status": "empty"}]}


def test_not_found_while_serper_refused_every_query_is_an_outage_not_a_product_failure(offline, monkeypatch):
    import main
    rec = _wired(monkeypatch, CREDIT_GONE)
    report = {}
    assert main.pre_cache_product_candidates(dict(TASK), sleep=lambda s: None, report=report) == "provider_down"
    assert rec["status"] == [("pending", "PROVIDER_DOWN")] and rec["failures"] == []
    assert report == {"searched": True, "serper_credit": True}


@pytest.mark.parametrize("health, expected", [
    ([], None),
    ([{"provider": "serper", "status": "ok"}, {"provider": "serper", "status": "quota"}], False),
    ([{"provider": "serper", "status": "error", "http_status": 401}], True),
    ([{"provider": "serper", "status": "error", "http_status": 500}], False),
    ([{"provider": "serper_web", "status": "quota"}], None),
])
def test_serper_credit_rule_matches_ops_health(health, expected):
    import main
    assert main.serper_credit_refused({"outcome": {"provider_health": health}}) is expected


def test_a_recheck_that_finds_nothing_goes_back_to_review(offline, monkeypatch):
    import main
    rec = _wired(monkeypatch, {"decision": "NOT_FOUND", "failure_code": "NO_RESULTS", "provider_health": []})
    task = dict(TASK, requeue_reason="VERIFIER_RECHECK")
    assert main.pre_cache_product_candidates(task, sleep=lambda s: None) == "success"
    assert rec["status"] == [("ready_for_review", "VERIFIER_DOWN")] and rec["failures"] == []


def test_worker_parks_or_requeues_rechecks_by_the_reader_state(offline, monkeypatch):
    import local_cache_db
    import main
    calls = []
    monkeypatch.setattr(local_cache_db, "park_verifier_rechecks", lambda: calls.append("park") or 2)
    monkeypatch.setattr(local_cache_db, "requeue_verifier_down", lambda run_id=None: calls.append(("requeue", run_id)) or 1)
    main._prepare_verifier_rechecks("VERIFIER_UNAVAILABLE: x", "run-1")
    main._prepare_verifier_rechecks("", "run-1")
    assert calls == ["park", ("requeue", "run-1")]


def test_payload_json_keeps_unicode(db):
    db.add_to_queue(ROW, "", "P4Q لبن", "P4Q Brand", "q", payload={"name_ar": "لبن"}, sku_key="p4q-u")
    assert json.loads(_queue_row(db, 0)["payload_json"]) == {"name_ar": "لبن"}


def test_the_reconcile_reads_the_real_outbox_records(mariadb_or_skip):
    """main._outbox_records over google_sheets.outbox_outcomes as the sheets package returns it ({row, column_key,
    status, value}): the link writes of the row, never its metadata writes."""
    import google_sheets
    import main

    db = mariadb_or_skip
    google_sheets._queue = None
    google_sheets.SQLiteTransactionQueue()
    conn = db.get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM sheet_updates WHERE `row_number` = 990077")
            cur.execute("INSERT INTO sheet_updates (`row_number`, `col_index`, `value`, sync_status, col_key) VALUES "
                        "(990077, 0, 'https://res.cloudinary.com/x/a.jpg', 'DEAD', 'link'), "
                        "(990077, 0, 'Dairy', 'SYNCED', 'meta:category_l1_en')")
        conn.commit()
        records = main._outbox_records([990077])
        assert [r["status"] for r in records[990077]] == ["DEAD"]
        assert main._link_write_state(records[990077], "https://res.cloudinary.com/x/a.jpg") == "DEAD"
    finally:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM sheet_updates WHERE `row_number` = 990077")
        conn.commit()
        conn.close()
