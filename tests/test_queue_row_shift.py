"""A product that only moved rows in the sheet keeps its queue state (no paid search again).

The queue is keyed by the sheet row number. Inserting or deleting a sheet row shifts every product below it;
without relocation each of them looked like a new product at its row and was searched again, losing its review
state, its candidates and its not-found schedule. main.plan_queue_moves finds products that moved (same sku_key,
once in the sheet and once in the queue) and local_cache_db.relocate_queue_rows moves their rows before the upsert.
"""

import pytest

ROW = 964000


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


def _prod(i, name):
    return {"row_number": ROW + i, "product_name": f"Shift {name}", "brand": "Shift Brand", "barcode": "",
            "existing_image_link": ""}


@pytest.fixture
def db(mariadb_or_skip):
    db = mariadb_or_skip

    def wipe():
        _sql(db, "DELETE FROM automation_queue")
        _sql(db, "DELETE FROM curation_candidates WHERE product_name LIKE 'Shift %%'")

    wipe()
    yield db
    wipe()


def _enqueue(db, prods, whole_sheet=True):
    import main
    rows, stats = main.plan_enqueue(prods, whole_sheet=whole_sheet)
    moved = db.relocate_queue_rows(stats["moves"])
    counts = db.add_many_to_queue(rows)
    return {r["row_number"]: r["sku_key"] for r in rows}, moved, counts


def _row(db, i):
    rows = _sql(db, "SELECT * FROM automation_queue WHERE `row_number` = %s", (ROW + i,))
    return rows[0] if rows else None


def _review(db, i, key):
    """The row is ready for review with one preselected candidate."""
    _sql(db, "UPDATE automation_queue SET status = 'ready_for_review', searched_at = NOW() - INTERVAL 1 DAY, "
             "trace_json = '{\"t\": 1}' WHERE `row_number` = %s", (ROW + i,))
    row = _row(db, i)
    assert db.save_curation_candidates(ROW + i, row["product_name"], row["brand"],
                                       [{"url": f"https://shift.example/{i}.jpg", "status": "preselected"}],
                                       sku_key=key)


def _not_found(db, i):
    """Not found once, the next try in 3 days."""
    _sql(db, "UPDATE automation_queue SET status = 'failed', failure_code = 'NO_RESULTS', fail_count = 1, "
             "searched_at = NOW() - INTERVAL 1 DAY, next_attempt_at = NOW() + INTERVAL 2 DAY "
             "WHERE `row_number` = %s", (ROW + i,))


def _state(db, i):
    r = _row(db, i)
    return (r["product_name"], r["status"], r["failure_code"], r["fail_count"], r["next_attempt_at"],
            r["searched_at"], r["trace_json"]) if r else None


def _candidate_rows(db, key):
    return [r["row_number"] for r in _sql(db, "SELECT `row_number` FROM curation_candidates WHERE sku_key = %s",
                                          (key,))]


def _claimable(db):
    out = []
    while True:
        task = db.fetch_next_task("host:1")
        if not task:
            return out
        out.append(task["product_name"])


def _setup(db):
    keys, _, _ = _enqueue(db, [_prod(0, "Alpha"), _prod(1, "Bravo"), _prod(2, "Charlie")])
    _review(db, 0, keys[ROW])
    _not_found(db, 1)
    _review(db, 2, keys[ROW + 2])
    return keys


def test_a_row_inserted_above_moves_every_product_with_its_state(db):
    keys = _setup(db)
    before = [_state(db, i) for i in range(3)]
    new_keys, moved, counts = _enqueue(db, [_prod(0, "New"), _prod(1, "Alpha"), _prod(2, "Bravo"),
                                            _prod(3, "Charlie")])
    assert moved == 3
    assert counts == {"insert": 1, "keep": 3, "reset": 0}
    assert [_state(db, i) for i in (1, 2, 3)] == before
    assert _candidate_rows(db, keys[ROW]) == [ROW + 1]
    assert _candidate_rows(db, keys[ROW + 2]) == [ROW + 3]
    assert (_row(db, 0)["product_name"], _row(db, 0)["status"]) == ("Shift New", "pending")
    assert _claimable(db) == ["Shift New"]                     # only the new product is searched
    # a second enqueue of the same sheet changes nothing
    _, moved, counts = _enqueue(db, [_prod(0, "New"), _prod(1, "Alpha"), _prod(2, "Bravo"), _prod(3, "Charlie")])
    assert moved == 0 and counts["reset"] == 0 and [_state(db, i) for i in (1, 2, 3)] == before


def test_a_row_deleted_above_moves_every_product_up(db):
    keys = _setup(db)
    before = [_state(db, i) for i in (1, 2)]
    _, moved, counts = _enqueue(db, [_prod(0, "Bravo"), _prod(1, "Charlie")])
    assert moved == 2
    assert counts == {"insert": 0, "keep": 2, "reset": 0}
    assert [_state(db, i) for i in (0, 1)] == before
    assert _row(db, 2) is None
    assert _candidate_rows(db, keys[ROW + 2]) == [ROW + 1]
    assert _claimable(db) == []


def test_a_deleted_row_with_a_row_filter_moves_only_what_is_known(db):
    """Without the whole sheet, a product whose old row was not read may still be there: today's behaviour."""
    import main
    _setup(db)
    queue = db.queue_snapshot()
    keys = {ROW + i: _row(db, i)["sku_key"] for i in range(3)}
    sheet = {ROW: keys[ROW + 1], ROW + 1: keys[ROW + 2]}
    assert main.plan_queue_moves(sheet, queue, whole_sheet=False) == [(ROW + 1, ROW, keys[ROW + 1])]
    assert sorted(main.plan_queue_moves(sheet, queue, whole_sheet=True)) == [
        (ROW + 1, ROW, keys[ROW + 1]), (ROW + 2, ROW + 1, keys[ROW + 2])]


def test_two_products_that_swapped_rows_keep_their_states(db):
    keys = _setup(db)
    a, b = _state(db, 0), _state(db, 1)
    _, moved, counts = _enqueue(db, [_prod(0, "Bravo"), _prod(1, "Alpha"), _prod(2, "Charlie")])
    assert moved == 2 and counts == {"insert": 0, "keep": 3, "reset": 0}
    assert (_state(db, 0), _state(db, 1)) == (b, a)
    assert _candidate_rows(db, keys[ROW]) == [ROW + 1]
    assert _claimable(db) == []


def test_a_new_product_at_a_row_is_still_searched_from_scratch(db):
    _setup(db)
    _, moved, counts = _enqueue(db, [_prod(0, "Delta"), _prod(1, "Bravo"), _prod(2, "Charlie")])
    assert moved == 0 and counts == {"insert": 0, "keep": 2, "reset": 1}
    row = _row(db, 0)
    assert (row["product_name"], row["status"], row["fail_count"], row["trace_json"]) == \
        ("Shift Delta", "pending", 0, None)
    assert _claimable(db) == ["Shift Delta"]


def test_a_duplicated_product_is_left_to_the_old_behaviour(db):
    import main
    _setup(db)
    queue = db.queue_snapshot()
    key = _row(db, 0)["sku_key"]
    # the same product on two sheet rows, or on two queue rows: ambiguous, nothing moves
    assert main.plan_queue_moves({ROW + 5: key, ROW + 6: key}, queue, whole_sheet=True) == []
    queue[ROW + 9] = dict(queue[ROW])
    assert main.plan_queue_moves({ROW + 5: key}, queue, whole_sheet=True) == []


def test_a_row_held_by_a_worker_is_not_moved(db):
    _setup(db)
    _sql(db, "UPDATE automation_queue SET status = 'processing', worker_id = 'host:9', "
             "lease_until = NOW() + INTERVAL 10 MINUTE WHERE `row_number` = %s", (ROW + 2,))
    key = _row(db, 2)["sku_key"]
    assert db.relocate_queue_rows([(ROW + 2, ROW + 3, key)]) == 0
    assert _row(db, 2)["status"] == "processing" and _row(db, 3) is None


def test_a_move_into_a_held_row_is_dropped_and_the_others_still_run(db):
    _setup(db)
    keys = {i: _row(db, i)["sku_key"] for i in range(3)}
    _sql(db, "UPDATE automation_queue SET status = 'processing', worker_id = 'host:9', "
             "lease_until = NOW() + INTERVAL 10 MINUTE WHERE `row_number` = %s", (ROW + 2,))
    # Bravo -> 2 is blocked by the held row (left to the upsert, as before); Alpha -> 1 still moves
    assert db.relocate_queue_rows([(ROW + 1, ROW + 2, keys[1]), (ROW, ROW + 1, keys[0])]) == 1
    assert [_row(db, i) and _row(db, i)["sku_key"] for i in range(3)] == [None, keys[0], keys[2]]
    assert _row(db, 2)["status"] == "processing"
