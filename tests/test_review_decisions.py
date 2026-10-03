"""review_decisions: one row per reviewer approve / reject / manual upload, written by cli_bridge from
what the engine showed (the product's curation candidates, read before they are deleted).

The first half is offline (the database is replaced by recorders). The second half runs on the real
MariaDB test database (skips when it is down); sockets may reach the database only.
"""

import base64
import json
import logging
import os
import socket

import pytest

from catalog_match.identity import build_sku_spec

ROW = 920019            # AL ALALI FANCY TUNA WATER 170GM (live row 19), shifted clear of the other DB tests
ROW_B = 920057          # VIRGINIA L/MEAT TUNA S/F OIL 170GM (live row 57)
NAME, BRAND = "AL ALALI FANCY TUNA WATER 170GM", "AL ALALI"
NAME_B, BRAND_B = "VIRGINIA L/MEAT TUNA S/F OIL 170GM", "VIRGINIA"
# The products' real keys (main.compute_sku_key): cli_bridge refuses a sku_key the sent product fields do not give.
SKU = build_sku_spec({"name": NAME, "brand": BRAND, "size": "170GM"}).sku_key
SKU_B = build_sku_spec({"name": NAME_B, "brand": BRAND_B, "size": "170GM"}).sku_key
PRE_URL = "https://www.luluhypermarket.com/medias/al-alali-fancy-tuna-water-170g.jpg"
OTHER_URL = "https://f.nooncdn.com/p/al-alali-fancy-tuna-oil-170g.jpg"
LINK = "https://res.cloudinary.com/demo/image/upload/products/canned/alali.png"

CANDIDATES = [
    {"url": PRE_URL, "title": "Al Alali Fancy Tuna in Water 170g", "status": "preselected",
     "page_url": "https://www.luluhypermarket.com/en-ae/al-alali-fancy-tuna/p/1", "domain": "www.luluhypermarket.com",
     "reasons": ["tier T1", "vlm MATCH"], "evidence": {"tier": 1, "page_domain": "luluhypermarket.com"},
     "vlm": {"decision": "MATCH", "brand_text": "AL ALALI"}, "content_sha256": "ab" * 32},
    {"url": OTHER_URL, "title": "Al Alali Fancy Tuna in Oil 170g", "status": "eligible",
     "page_url": "https://www.noon.com/uae-en/al-alali-tuna-oil/p/2", "domain": "noon.com",
     "reasons": ["variant unknown"], "evidence": {"tier": 2}, "vlm": {"decision": "UNSURE"}},
]


def _stored(candidates=CANDIDATES, row=ROW, sku=SKU, name=NAME, brand=BRAND):
    """Candidates as local_cache_db.get_curation_candidates returns them after save_curation_candidates."""
    out = []
    for c in candidates:
        tier = (c.get("evidence") or {}).get("tier")
        out.append({"row_number": row, "product_name": name, "brand": brand, "image_url": c["url"],
                    "source_domain": c.get("domain"), "status": c["status"], "sku_key": sku,
                    "reasons": c.get("reasons", []), "evidence": c.get("evidence", {}), "vlm": c.get("vlm"),
                    "identity_tier": str(tier) if tier is not None else None, "page_url": c.get("page_url"),
                    "content_sha256": c.get("content_sha256")})
    return out


class FakeCell:
    def __init__(self, value):
        self.value = value


class FakeWorksheet:
    title = "Products"

    def __init__(self, link_value=""):
        self.link_value = link_value

    def row_values(self, n):
        return ["Barcode", "Product Name", "Brand", "Drive Image Link"]

    def cell(self, row, col):
        return FakeCell(self.link_value)


@pytest.fixture
def sheet(monkeypatch, tmp_path):
    """cli_bridge with the sheet, the image processing and the upload replaced by recorders."""
    import cli_bridge
    import google_sheets
    import main

    events = []
    ws = FakeWorksheet()

    def fake_publish(image_url, name, brand, row_number, worksheet, link_column_index, **kwargs):
        events.append(("publish", image_url))
        return {"status": "published", "isolated": True, "provider": "photoroom", "metadata": {},
                "width": 800, "height": 800, "link": LINK, "sheet_value": LINK}

    monkeypatch.setattr(cli_bridge, "LOG_PATH", str(tmp_path / "search.log"))
    monkeypatch.setattr(main, "publish_image", fake_publish)
    monkeypatch.setattr(google_sheets, "init_async_queue", lambda *a, **k: None)
    monkeypatch.setattr(google_sheets, "stop_async_queue", lambda *a, **k: None)
    monkeypatch.setattr(google_sheets, "get_sheets_client", lambda: object())
    monkeypatch.setattr(google_sheets, "get_brand_mappings", lambda *a, **k: {})
    monkeypatch.setattr(google_sheets, "open_worksheet", lambda client, name: ws)
    monkeypatch.setattr(google_sheets, "find_link_column", lambda worksheet, create=True: 3)
    monkeypatch.setattr(google_sheets, "update_image_link",
                        lambda *a, **k: events.append(("update_image_link", a[1], a[3])) or True)
    return cli_bridge, events, ws


def _approve_params(url=PRE_URL, **extra):
    params = {"image_url": url, "page_url": "", "candidate_sha256": "ab" * 32, "product_name": NAME, "brand": BRAND,
              "row_number": ROW, "barcode": "", "sku_key": SKU, "size": "170GM", "enhance": False,
              "bg_removal_method": "photoroom", "target_width": 0, "target_height": 0}
    params.update(extra)
    return params


def _reject_params(url=PRE_URL, reason="WRONG_VARIANT", **extra):
    params = {"row_number": ROW, "image_url": url, "product_name": NAME, "brand": BRAND, "barcode": "",
              "sku_key": SKU, "size": "170GM", "reason_code": reason, "rejection_reasons": [reason], "research": False}
    params.update(extra)
    return params


def _upload_params(tmp_path, **extra):
    path = tmp_path / "manual_upload.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\n")
    params = {"file_path": str(path), "row_number": ROW, "product_name": NAME, "brand": BRAND, "barcode": "",
              "sku_key": SKU, "target_width": 0, "target_height": 0, "enhance": False}
    params.update(extra)
    return params


# ---------------------------------------------------------------------------
# Offline: what the bridge records, and that recording can never break the action
# ---------------------------------------------------------------------------

@pytest.fixture
def recorder(sheet, offline, monkeypatch):
    """The database is replaced: stored candidates come from `state`, every write is recorded in order."""
    import local_cache_db

    cli_bridge, events, ws = sheet
    state = {"candidates": _stored(), "approved": None}

    def record(name, result=True):
        return lambda *a, **k: events.append((name, a, k)) or result

    monkeypatch.setattr(local_cache_db, "get_task_by_row", lambda row: None)
    monkeypatch.setattr(local_cache_db, "get_curation_candidates", lambda row, sku_key=None: list(state["candidates"]))
    monkeypatch.setattr(local_cache_db, "get_cached_product", lambda **k: state["approved"])
    monkeypatch.setattr(local_cache_db, "get_rejections", lambda sku: ([], []))
    for name in ("save_product_resolution", "update_task_status_by_row", "delete_curation_candidates",
                 "add_rejected_image", "save_feedback"):
        monkeypatch.setattr(local_cache_db, name, record(name))
    monkeypatch.setattr(local_cache_db, "supersede_resolution", record("supersede_resolution", 1))
    monkeypatch.setattr(local_cache_db, "add_review_decision",
                        lambda action, **k: events.append(("add_review_decision", action, k)) or True)
    return cli_bridge, events, state


def _reviews(events):
    return [(e[1], e[2]) for e in events if e[0] == "add_review_decision"]


def _names(events):
    return [e[0] for e in events]


def test_approving_the_precheck_records_it_before_the_candidates_are_deleted(recorder):
    cli_bridge, events, state = recorder
    result = cli_bridge.action_select_image(_approve_params())

    assert result == {"status": "success", "image_link": LINK, "sheet_value": LINK, "isolated": True,
                      "provider": "photoroom", "sku_key": SKU, "rows_written": [ROW]}
    (action, row), = _reviews(events)
    assert action == "approved"
    assert row == {"sku_key": SKU, "row_number": ROW, "brand": BRAND, "product_name": NAME, "image_url": PRE_URL,
                   "page_domain": "luluhypermarket.com", "identity_tier": "1", "engine_decision": "REVIEW_PRESELECTED",
                   "was_preselected": True, "vlm_decision": "MATCH", "reason_code": None}
    names = _names(events)
    assert names.index("add_review_decision") < names.index("delete_curation_candidates")


def test_approving_another_candidate_records_the_overridden_precheck(recorder):
    cli_bridge, events, state = recorder
    assert cli_bridge.action_select_image(_approve_params(OTHER_URL))["status"] == "success"
    (action, row), = _reviews(events)
    assert (row["was_preselected"], row["engine_decision"]) == (False, "REVIEW_PRESELECTED")
    assert (row["page_domain"], row["identity_tier"], row["vlm_decision"]) == ("noon.com", "2", "UNSURE")


def test_approving_without_a_precheck(recorder):
    cli_bridge, events, state = recorder
    state["candidates"] = _stored([dict(CANDIDATES[0], status="eligible"), CANDIDATES[1]])
    cli_bridge.action_select_image(_approve_params())
    (action, row), = _reviews(events)
    assert (row["was_preselected"], row["engine_decision"]) == (False, "REVIEW_UNSELECTED")


def test_catalog_approval_without_stored_candidates_is_unknown(recorder):
    """The catalog page searches live and stores nothing: what was pre-checked is not known, never guessed."""
    cli_bridge, events, state = recorder
    state["candidates"] = []
    cli_bridge.action_select_image(_approve_params(page_url="https://www.carrefouruae.com/mafuae/en/p/9"))
    (action, row), = _reviews(events)
    assert (row["was_preselected"], row["engine_decision"], row["identity_tier"]) == (None, None, None)
    assert row["page_domain"] == "carrefouruae.com"


def test_an_image_outside_the_stored_run_is_unknown(recorder):
    cli_bridge, events, state = recorder
    cli_bridge.action_select_image(_approve_params("https://www.carrefouruae.com/img/live-search-result.jpg"))
    (action, row), = _reviews(events)
    assert (row["was_preselected"], row["engine_decision"]) == (None, None)


def test_rejecting_the_precheck_records_the_reason(recorder):
    cli_bridge, events, state = recorder
    result = cli_bridge.action_reject_image(_reject_params(reason="WRONG_SIZE"))
    assert result["status"] == "success" and result["reason_code"] == "WRONG_SIZE"
    (action, row), = _reviews(events)
    assert action == "rejected" and row["reason_code"] == "WRONG_SIZE"
    assert (row["was_preselected"], row["engine_decision"], row["page_domain"]) == (True, "REVIEW_PRESELECTED",
                                                                                   "luluhypermarket.com")
    names = _names(events)
    assert names.index("add_review_decision") < names.index("delete_curation_candidates")
    assert names.index("add_rejected_image") < names.index("add_review_decision")


def test_rejecting_an_auto_published_image_counts_against_the_engine(recorder):
    cli_bridge, events, state = recorder
    state["candidates"] = []
    state["approved"] = {"cloudinary_url": LINK, "original_url": PRE_URL, "verification_status": "auto_verified"}
    cli_bridge.action_reject_image(_reject_params(LINK, reason="WRONG_PRODUCT"))
    (action, row), = _reviews(events)
    assert (row["engine_decision"], row["was_preselected"], row["reason_code"]) == ("AUTO_PUBLISH", True,
                                                                                    "WRONG_PRODUCT")


def test_rejecting_a_human_approval_is_not_mistaken_for_auto_publish(recorder):
    cli_bridge, events, state = recorder
    state["candidates"] = []
    state["approved"] = {"cloudinary_url": LINK, "original_url": PRE_URL, "verification_status": "human_approved"}
    cli_bridge.action_reject_image(_reject_params(LINK, reason="WRONG_PACK"))
    (action, row), = _reviews(events)
    assert (row["engine_decision"], row["was_preselected"]) == (None, None)


def test_manual_upload_is_recorded_as_not_the_precheck(recorder, tmp_path):
    cli_bridge, events, state = recorder
    result = cli_bridge.action_upload_manual_image(_upload_params(tmp_path))
    assert result == {"status": "success", "image_link": LINK, "sheet_value": LINK, "isolated": True, "sku_key": SKU,
                      "rows_written": [ROW]}
    (action, row), = _reviews(events)
    assert action == "manual_upload"
    assert (row["image_url"], row["page_domain"]) == (None, None)            # the local file path is never stored
    assert (row["was_preselected"], row["engine_decision"]) == (False, "REVIEW_PRESELECTED")
    names = _names(events)
    assert names.index("add_review_decision") < names.index("delete_curation_candidates")


def _cache_hit_candidates():
    """What the worker stores when the search returns a cached approval (main.collect_candidates)."""
    import main
    best = {"source": "sqlite_cache", "url": LINK, "decision": "REVIEW_PRESELECTED", "preselect": True,
            "original_url": PRE_URL, "width": 800, "height": 800}
    cands = main.collect_candidates(best, None)
    assert [c["status"] for c in cands] == ["preselected"]       # shown pre-checked, but not an engine pick
    return [dict(c, domain="res.cloudinary.com") for c in cands]


def test_approving_a_cached_approval_is_not_an_engine_precheck(recorder):
    """A cache hit re-offers an earlier approval: it never auto-publishes, so it is no evidence for the engine."""
    cli_bridge, events, state = recorder
    state["candidates"] = _stored(_cache_hit_candidates())
    # a request without the brand carries the key of that brand-less product (else it is refused as another product)
    cli_bridge.action_select_image(_approve_params(LINK, brand="", product_name=NAME, sku_key=build_sku_spec(
        {"name": NAME, "brand": "", "size": "170GM"}).sku_key))
    (action, row), = _reviews(events)
    assert (row["engine_decision"], row["was_preselected"]) == (None, None)
    assert row["page_domain"] is None                                 # not the CDN host of the cached copy
    assert row["brand"] == BRAND                                      # the stored rows still name the product


def test_other_image_or_upload_over_a_cached_approval_is_not_held_against_the_engine(recorder, tmp_path):
    cli_bridge, events, state = recorder
    state["candidates"] = _stored(_cache_hit_candidates())
    cli_bridge.action_select_image(_approve_params(OTHER_URL))
    cli_bridge.action_upload_manual_image(_upload_params(tmp_path))
    (_, approved), (_, uploaded) = _reviews(events)
    assert (approved["engine_decision"], approved["was_preselected"]) == (None, None)
    assert (uploaded["engine_decision"], uploaded["was_preselected"]) == (None, False)


def test_cached_approvals_do_not_turn_into_accepted_prechecks(recorder, tmp_path):
    """First review: no pre-check shown. The same SKU comes back later as a cache hit and is approved again:
    the brand must still have no reviewed pre-check."""
    import local_cache_db
    cli_bridge, events, state = recorder
    state["candidates"] = _stored([dict(CANDIDATES[0], status="eligible"), CANDIDATES[1]])
    cli_bridge.action_select_image(_approve_params())
    state["candidates"] = _stored(_cache_hit_candidates())
    cli_bridge.action_select_image(_approve_params(LINK))
    rows = [dict(k, id=n, action=a, created_at=f"2026-09-30 10:00:0{n}") for n, (a, k) in enumerate(_reviews(events))]
    rows = [dict(r, was_preselected=None if r["was_preselected"] is None else int(r["was_preselected"]))
            for r in rows]
    (brand,) = local_cache_db.review_stats(rows)["brands"]
    assert (brand["reviewed_skus"], brand["prechecked"], brand["accepted"]) == (1, 0, 0)


def test_brand_and_name_fall_back_to_the_stored_candidates(recorder):
    cli_bridge, events, state = recorder
    cli_bridge.action_reject_image(_reject_params(brand="", product_name=""))
    (action, row), = _reviews(events)
    assert (row["brand"], row["product_name"]) == (BRAND, NAME)


def _boom(*a, **k):
    raise RuntimeError("review_decisions is locked")


@pytest.mark.parametrize("failing", ["add_review_decision", "get_curation_candidates"])
def test_a_logging_failure_does_not_change_the_approval(recorder, monkeypatch, caplog, failing):
    import local_cache_db
    cli_bridge, events, state = recorder
    expected = cli_bridge.action_select_image(_approve_params())
    events.clear()

    monkeypatch.setattr(local_cache_db, failing, _boom)
    with caplog.at_level(logging.ERROR, logger="cli_bridge"):
        result = cli_bridge.action_select_image(_approve_params())

    assert result == expected
    assert {"save_product_resolution", "update_task_status_by_row", "delete_curation_candidates"} <= set(_names(events))
    logged = [r for r in caplog.records if r.name == "cli_bridge" and r.exc_info]
    assert logged and "review_decisions is locked" in str(logged[-1].exc_info[1])


def test_a_logging_failure_does_not_change_the_rejection(recorder, monkeypatch, caplog):
    import local_cache_db
    cli_bridge, events, state = recorder
    expected = cli_bridge.action_reject_image(_reject_params())
    events.clear()
    monkeypatch.setattr(local_cache_db, "add_review_decision", _boom)
    with caplog.at_level(logging.ERROR, logger="cli_bridge"):
        result = cli_bridge.action_reject_image(_reject_params())
    assert result == expected
    assert {"supersede_resolution", "delete_curation_candidates", "update_task_status_by_row"} <= set(_names(events))
    assert any(r.exc_info for r in caplog.records if r.name == "cli_bridge")


def test_a_logging_failure_does_not_change_the_upload(recorder, monkeypatch, tmp_path, caplog):
    import local_cache_db
    cli_bridge, events, state = recorder
    expected = cli_bridge.action_upload_manual_image(_upload_params(tmp_path))
    events.clear()
    monkeypatch.setattr(local_cache_db, "add_review_decision", _boom)
    with caplog.at_level(logging.ERROR, logger="cli_bridge"):
        result = cli_bridge.action_upload_manual_image(_upload_params(tmp_path))
    assert result == expected and "delete_curation_candidates" in _names(events)
    logged = [r for r in caplog.records if r.name == "cli_bridge" and r.exc_info]
    assert logged and "review_decisions is locked" in str(logged[-1].exc_info[1])


def test_nothing_is_recorded_when_the_approval_fails(recorder, monkeypatch):
    import main
    cli_bridge, events, state = recorder
    monkeypatch.setattr(main, "publish_image", lambda *a, **k: {"status": "failed", "error": "upload_failed"})
    assert cli_bridge.action_select_image(_approve_params())["status"] == "failed"
    assert _reviews(events) == []


def test_review_stats_action_contract(recorder, monkeypatch, capsys):
    import local_cache_db
    cli_bridge, events, state = recorder
    rows = [{"id": 1, "created_at": "2026-09-30 10:00:00", "action": "approved", "sku_key": SKU, "brand": BRAND,
             "image_url": PRE_URL, "page_domain": "luluhypermarket.com", "was_preselected": 1,
             "engine_decision": "REVIEW_PRESELECTED", "reason_code": None}]
    monkeypatch.setattr(local_cache_db, "get_review_decisions", lambda: rows)
    arg = base64.b64encode(json.dumps({}).encode("utf-8")).decode("ascii")
    assert cli_bridge.main(["cli_bridge.py", "review_stats", arg]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "success"
    assert out["overall"]["prechecked"] == 1 and out["brands"][0]["brand"] == BRAND
    assert out["thresholds"]["min_reviewed"] == local_cache_db.AUTO_PUBLISH_MIN_REVIEWED

    monkeypatch.setattr(local_cache_db, "get_review_decisions", _boom)
    assert cli_bridge.main(["cli_bridge.py", "review_stats", arg]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "failed" and "locked" not in json.dumps(out)


# ---------------------------------------------------------------------------
# Real MariaDB: the table and the writes from select / reject / upload
# ---------------------------------------------------------------------------

@pytest.fixture
def db_only(monkeypatch):
    """Sockets may reach the MariaDB server only; any other connection is refused."""
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

    def cleanup():
        conn = db.get_db_connection()
        try:
            cur = conn.cursor()
            skus, rows = (SKU, SKU_B), (ROW, ROW_B)
            cur.execute("DELETE FROM review_decisions WHERE sku_key IN (%s, %s) OR `row_number` IN (%s, %s)",
                        skus + rows)
            cur.execute("DELETE FROM curation_candidates WHERE `row_number` IN (%s, %s)", rows)
            cur.execute("DELETE FROM rejected_images WHERE sku_key IN (%s, %s)", skus)
            cur.execute("DELETE FROM resolved_products WHERE sku_key IN (%s, %s)", skus)
            cur.execute("DELETE FROM active_learning_feedback WHERE `row_number` IN (%s, %s)", rows)
            cur.execute("DELETE FROM automation_queue WHERE `row_number` IN (%s, %s)", rows)
            conn.commit()
        finally:
            conn.close()

    cleanup()
    yield db
    cleanup()


def _db_reviews(db, sku=SKU):
    return [r for r in db.get_review_decisions() if r["sku_key"] == sku]


def test_table_is_created_idempotently_with_its_indexes(db):
    conn = db.get_db_connection()
    try:
        conn.cursor().execute("DROP TABLE IF EXISTS review_decisions")    # the test database only
        conn.commit()
    finally:
        conn.close()
    assert db.init_db() is True and db.init_db() is True
    conn = db.get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT COLUMN_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() "
                    "AND TABLE_NAME = 'review_decisions'")
        columns = {r["COLUMN_NAME"] for r in cur.fetchall()}
        cur.execute("SELECT COLUMN_NAME FROM information_schema.STATISTICS WHERE TABLE_SCHEMA = DATABASE() "
                    "AND TABLE_NAME = 'review_decisions' AND INDEX_NAME <> 'PRIMARY'")
        indexed = {r["COLUMN_NAME"] for r in cur.fetchall()}
    finally:
        conn.close()
    assert columns == {"id", "created_at", "action", "sku_key", "row_number", "brand", "product_name", "image_url",
                       "page_domain", "identity_tier", "engine_decision", "was_preselected", "vlm_decision",
                       "reason_code"}
    assert {"sku_key", "brand", "created_at"} <= indexed


def test_add_review_decision_roundtrip(db):
    tricky = "AL ALALI'); DROP TABLE review_decisions; --"
    assert db.add_review_decision("rejected", sku_key=SKU, row_number=ROW, brand=tricky, product_name=NAME,
                                  image_url=PRE_URL, page_domain="luluhypermarket.com", identity_tier=1,
                                  engine_decision="REVIEW_PRESELECTED", was_preselected=True, vlm_decision="MATCH",
                                  reason_code="WRONG_SIZE") is True
    assert db.add_review_decision("approved", sku_key=SKU, row_number=str(ROW), image_url=OTHER_URL) is True
    first, second = _db_reviews(db)
    assert first["brand"] == tricky                                   # parameterised: stored verbatim
    assert (first["action"], first["reason_code"], first["was_preselected"], first["identity_tier"]) == (
        "rejected", "WRONG_SIZE", 1, "1")
    assert first["created_at"] is not None and first["id"] < second["id"]
    assert (second["was_preselected"], second["engine_decision"], second["brand"], second["row_number"]) == (
        None, None, None, ROW)
    with pytest.raises(ValueError):
        db.add_review_decision("deleted", sku_key=SKU)


def _db_candidates(db, row=ROW, sku=SKU, name=NAME, brand=BRAND, candidates=CANDIDATES):
    assert db.save_curation_candidates(row, name, brand, candidates, sku_key=sku, run_id="wpd-run") is True


def test_select_writes_the_review_from_the_stored_candidates(db, sheet):
    cli_bridge, events, ws = sheet
    _db_candidates(db)
    result = cli_bridge.action_select_image(_approve_params())
    assert result["status"] == "success"

    (row,) = _db_reviews(db)
    assert (row["action"], row["row_number"], row["brand"], row["product_name"], row["image_url"]) == (
        "approved", ROW, BRAND, NAME, PRE_URL)
    assert (row["engine_decision"], row["was_preselected"], row["identity_tier"], row["vlm_decision"]) == (
        "REVIEW_PRESELECTED", 1, "1", "MATCH")
    assert row["page_domain"] == "luluhypermarket.com" and row["reason_code"] is None
    assert db.get_curation_candidates(ROW, sku_key=SKU) == []           # still deleted after the review is kept
    assert db.get_cached_product(sku_key=SKU)["verification_status"] == "human_approved"


def test_reject_writes_the_review_with_its_reason(db, sheet):
    cli_bridge, events, ws = sheet
    _db_candidates(db)
    result = cli_bridge.action_reject_image(_reject_params(OTHER_URL, reason="WRONG_VARIANT"))
    assert result["status"] == "success"

    (row,) = _db_reviews(db)
    assert (row["action"], row["reason_code"], row["image_url"]) == ("rejected", "WRONG_VARIANT", OTHER_URL)
    assert (row["engine_decision"], row["was_preselected"], row["page_domain"]) == ("REVIEW_PRESELECTED", 0,
                                                                                   "noon.com")
    assert db.get_curation_candidates(ROW, sku_key=SKU) == []
    assert db.get_rejections(SKU)[0] == [OTHER_URL]


def test_upload_writes_a_manual_upload_review(db, sheet, tmp_path):
    cli_bridge, events, ws = sheet
    _db_candidates(db)
    result = cli_bridge.action_upload_manual_image(_upload_params(tmp_path))
    assert result["status"] == "success"

    (row,) = _db_reviews(db)
    assert (row["action"], row["image_url"], row["page_domain"]) == ("manual_upload", None, None)
    assert (row["engine_decision"], row["was_preselected"]) == ("REVIEW_PRESELECTED", 0)


def test_approving_a_stored_cache_hit_is_not_an_engine_precheck(db, sheet):
    cli_bridge, events, ws = sheet
    _db_candidates(db, candidates=_cache_hit_candidates())
    assert db.get_curation_candidates(ROW, sku_key=SKU)[0]["status"] == "preselected"
    assert cli_bridge.action_select_image(_approve_params(LINK))["status"] == "success"

    (row,) = _db_reviews(db)
    assert (row["action"], row["image_url"], row["brand"]) == ("approved", LINK, BRAND)
    assert (row["engine_decision"], row["was_preselected"], row["page_domain"]) == (None, None, None)
    assert db.review_stats([row])["overall"]["prechecked"] == 0


def test_a_failing_insert_does_not_break_the_approval(db, sheet, monkeypatch, caplog):
    import pymysql
    cli_bridge, events, ws = sheet
    _db_candidates(db)

    def locked(*a, **k):
        raise pymysql.err.OperationalError(1205, "Lock wait timeout exceeded")

    monkeypatch.setattr(db, "add_review_decision", locked)
    with caplog.at_level(logging.ERROR, logger="cli_bridge"):
        result = cli_bridge.action_select_image(_approve_params())
    assert result == {"status": "success", "image_link": LINK, "sheet_value": LINK, "isolated": True,
                      "provider": "photoroom", "sku_key": SKU, "rows_written": [ROW]}
    assert _db_reviews(db) == []
    assert db.get_curation_candidates(ROW, sku_key=SKU) == []
    assert db.get_cached_product(sku_key=SKU)["verification_status"] == "human_approved"
    assert any(r.exc_info for r in caplog.records if r.name == "cli_bridge")


def test_a_missing_table_does_not_break_approve_reject_or_upload(db, sheet, tmp_path, caplog):
    """The real failure, not a stand-in: review_decisions does not exist (e.g. init_db could not create it)."""
    cli_bridge, events, ws = sheet
    conn = db.get_db_connection()
    try:
        conn.cursor().execute("DROP TABLE IF EXISTS review_decisions")    # the test database only
        conn.commit()
    finally:
        conn.close()
    try:
        _db_candidates(db)
        with caplog.at_level(logging.ERROR, logger="cli_bridge"):
            approved = cli_bridge.action_select_image(_approve_params())
            _db_candidates(db)
            rejected = cli_bridge.action_reject_image(_reject_params(OTHER_URL))
            _db_candidates(db)
            # replace: the approval above is seconds old (an old client may not overwrite it without replace)
            uploaded = cli_bridge.action_upload_manual_image(_upload_params(tmp_path, replace=True))
        assert approved == {"status": "success", "image_link": LINK, "sheet_value": LINK, "isolated": True,
                            "provider": "photoroom", "sku_key": SKU, "rows_written": [ROW]}
        assert rejected["status"] == "success" and rejected["reason_code"] == "WRONG_VARIANT"
        assert uploaded == {"status": "success", "image_link": LINK, "sheet_value": LINK, "isolated": True,
                            "sku_key": SKU, "rows_written": [ROW]}
        assert db.get_curation_candidates(ROW, sku_key=SKU) == []
        failures = [r for r in caplog.records if r.name == "cli_bridge" and r.exc_info]
        assert len(failures) == 3 and all("review_decisions" in str(r.exc_info[1]) for r in failures)
    finally:
        assert db.init_db() is True


def test_stats_from_real_reviews(db, sheet):
    """Reject the pre-check of one SKU, approve the pre-check of another: the stats read them back."""
    cli_bridge, events, ws = sheet
    _db_candidates(db)
    _db_candidates(db, ROW_B, SKU_B, NAME_B, BRAND_B, [
        dict(CANDIDATES[0], url="https://www.luluhypermarket.com/medias/virginia-lmeat-tuna-oil.jpg")])
    assert cli_bridge.action_reject_image(_reject_params(reason="WRONG_SIZE"))["status"] == "success"
    assert cli_bridge.action_select_image(dict(
        _approve_params("https://www.luluhypermarket.com/medias/virginia-lmeat-tuna-oil.jpg"),
        row_number=ROW_B, sku_key=SKU_B, product_name=NAME_B, brand=BRAND_B))["status"] == "success"

    mine = [r for r in db.get_review_decisions() if r["sku_key"] in (SKU, SKU_B)]
    stats = db.review_stats(mine)
    brands = {b["brand"]: b for b in stats["brands"]}
    assert (brands[BRAND]["prechecked"], brands[BRAND]["accepted"]) == (1, 0)
    assert brands[BRAND]["top_reject_reasons"] == [("WRONG_SIZE", 1)]
    assert (brands[BRAND_B]["prechecked"], brands[BRAND_B]["accepted"]) == (1, 1)
    assert stats["domains"] == [{"domain": "luluhypermarket.com", "approved": 1, "rejected": 1}]


# ---------------------------------------------------------------------------
# The catalog page sends what the reviewer saw of a live search (integration of the review work)
# ---------------------------------------------------------------------------

LIVE = {"search_decision": "REVIEW_PRESELECTED", "candidate_status": "preselected", "candidate_cache_hit": False,
        "identity_tier": "1", "vlm_decision": "MATCH"}


def test_catalog_approval_of_the_live_precheck_counts(recorder):
    cli_bridge, events, state = recorder
    state["candidates"] = []
    cli_bridge.action_select_image(_approve_params("https://www.carrefouruae.com/img/live.jpg",
                                                   page_url="https://www.carrefouruae.com/mafuae/en/p/9", **LIVE))
    (action, row), = _reviews(events)
    assert (row["engine_decision"], row["was_preselected"]) == ("REVIEW_PRESELECTED", True)
    assert (row["identity_tier"], row["vlm_decision"], row["page_domain"]) == ("1", "MATCH", "carrefouruae.com")


def test_what_the_reviewer_saw_wins_over_an_older_stored_run(recorder):
    # The stored worker run pre-checked PRE_URL and listed OTHER_URL as a plain candidate. A newer live search
    # on the catalog page pre-checked OTHER_URL, and the reviewer approved it: that is an accepted pre-check.
    cli_bridge, events, state = recorder
    cli_bridge.action_select_image(_approve_params(OTHER_URL, **LIVE))
    (action, row), = _reviews(events)
    assert (row["engine_decision"], row["was_preselected"]) == ("REVIEW_PRESELECTED", True)
    assert (row["identity_tier"], row["vlm_decision"]) == ("1", "MATCH")


def test_catalog_rejection_and_upload_use_the_live_view(recorder, tmp_path):
    cli_bridge, events, state = recorder
    state["candidates"] = []
    cli_bridge.action_reject_image(_reject_params("https://www.carrefouruae.com/img/live.jpg", **LIVE))
    cli_bridge.action_upload_manual_image(_upload_params(tmp_path, search_decision="REVIEW_UNSELECTED"))
    (a1, reject), (a2, upload) = _reviews(events)
    assert (a1, reject["engine_decision"], reject["was_preselected"]) == ("rejected", "REVIEW_PRESELECTED", True)
    assert (a2, upload["engine_decision"], upload["was_preselected"]) == ("manual_upload", "REVIEW_UNSELECTED", False)


@pytest.mark.parametrize("view", [
    dict(LIVE, candidate_cache_hit=True),       # a cached earlier approval, not the engine's pick
    dict(LIVE, search_decision="NOT_A_DECISION"),
    dict(LIVE, search_decision=""),
])
def test_a_cache_hit_or_unknown_decision_from_the_page_is_not_evidence(recorder, view):
    cli_bridge, events, state = recorder
    state["candidates"] = []
    cli_bridge.action_select_image(_approve_params("https://www.carrefouruae.com/img/live.jpg", **view))
    (action, row), = _reviews(events)
    assert (row["engine_decision"], row["was_preselected"]) == (None, None)
