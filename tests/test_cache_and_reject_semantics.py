"""Cache lookup rules (D11) and the reviewer reject flow, with no database or network."""

import pytest


pytestmark = pytest.mark.usefixtures("offline")


@pytest.fixture
def ldb(offline):
    import local_cache_db
    return local_cache_db


def test_barcode_strict(ldb, monkeypatch, fake_connection):
    conn = fake_connection(lambda sql, params: [])        # every lookup misses
    monkeypatch.setattr(ldb, "get_db_connection", lambda: conn)

    assert ldb.get_cached_product(barcode="6281007000024", product_name="Fresh Milk", brand="Almarai") is None

    selects = [(s, p) for s, p in conn.executed if s.startswith("SELECT")]
    assert len(selects) == 1, f"expected only the barcode lookup, got {selects}"
    sql, params = selects[0]
    assert "WHERE barcode = %s" in sql and params == ("6281007000024",)
    assert not any("product_name" in s.lower() for s, _ in selects), "no name/brand fallback after a barcode miss"
    for s, _ in selects:
        assert "verification_status IN ('human_approved','auto_verified')" in s


def test_every_cache_query_filters_verification_status(ldb, monkeypatch, fake_connection):
    conn = fake_connection(lambda sql, params: [])
    monkeypatch.setattr(ldb, "get_db_connection", lambda: conn)
    ldb.get_cached_product(barcode="", product_name="Tomato Paste", brand="")           # name path
    ldb.get_cached_product(barcode=None, product_name="Tomato Paste", brand="", sku_key="abc123")  # sku path
    ldb.get_cached_product(barcode="6281007000024", sku_key="06281007000024")          # sku then barcode
    selects = [s for s, _ in conn.executed if s.startswith("SELECT")]
    assert len(selects) == 4
    assert all("verification_status IN ('human_approved','auto_verified')" in s for s in selects)
    # an sku_key without a barcode never falls back to the name
    assert sum("product_name" in s.lower() for s in selects) == 1


def test_cache_hit_returns_the_row(ldb, monkeypatch, fake_connection):
    # The row found by barcode is the requested product (same brand and name): served as before.
    # (Phase 3: a barcode-keyed row must also name the same product; a row without a stored
    # name or brand can no longer be checked and is not served, see the next test.)
    row = {"cloudinary_url": "https://res/x.png", "original_url": "https://src/x.jpg", "clip_score": None,
           "metadata_json": '{"ingredients": "milk"}', "sku_key": "06281007000024",
           "verification_status": "human_approved", "approved_by": "human", "perceptual_hash": "",
           "product_name": "Fresh Milk", "brand": "Almarai"}
    conn = fake_connection(lambda sql, params: [row])
    monkeypatch.setattr(ldb, "get_db_connection", lambda: conn)
    hit = ldb.get_cached_product("6281007000024", "Fresh Milk", "Almarai")
    assert hit["cloudinary_url"] == "https://res/x.png"
    assert hit["metadata"] == {"ingredients": "milk"}
    assert hit["verification_status"] == "human_approved"


@pytest.mark.parametrize("stored", [
    {"product_name": "Laban Up", "brand": "Almarai"},          # another product of the brand
    {"product_name": "Fresh Milk", "brand": "Al Rawabi"},      # another brand
    {"product_name": "", "brand": ""},                         # nothing to check against
])
def test_barcode_cache_hit_for_another_product_is_ignored(ldb, monkeypatch, fake_connection, stored):
    row = {"cloudinary_url": "https://res/x.png", "original_url": "https://src/x.jpg", "clip_score": None,
           "metadata_json": "", "sku_key": "06281007000024", "verification_status": "human_approved",
           "approved_by": "human", "perceptual_hash": "", **stored}
    conn = fake_connection(lambda sql, params: [row])
    monkeypatch.setattr(ldb, "get_db_connection", lambda: conn)
    assert ldb.get_cached_product("6281007000024", "Fresh Milk", "Almarai") is None
    assert ldb.get_cached_product(barcode="6281007000024", product_name="Fresh Milk", brand="Almarai",
                                  sku_key="06281007000024") is None
    # an approval-status lookup by sku_key alone (no name to compare) is unchanged
    assert ldb.get_cached_product(sku_key="06281007000024")["cloudinary_url"] == "https://res/x.png"


class FakeCell:
    def __init__(self, value):
        self.value = value


class FakeWorksheet:
    title = "Products"

    def __init__(self, link_value):
        self.link_value = link_value
        self.cell_reads = []

    def row_values(self, n):
        return ["Barcode", "Product Name", "Brand", "Drive Image Link"]

    def cell(self, row, col):
        self.cell_reads.append((row, col))
        return FakeCell(self.link_value)


@pytest.fixture
def bridge(offline, monkeypatch, tmp_path):
    import cli_bridge
    import google_sheets
    import local_cache_db

    calls = {"add_rejected_image": [], "supersede_resolution": [], "delete_curation_candidates": [],
             "update_image_link": [], "search": []}

    monkeypatch.setattr(cli_bridge, "LOG_PATH", str(tmp_path / "search.log"))
    monkeypatch.setattr(local_cache_db, "get_task_by_row", lambda row: {"sku_key": "06281007000024", "barcode": "6281007000024",
                                                                         "product_name": "Laban Up"})
    monkeypatch.setattr(local_cache_db, "get_curation_candidates", lambda row, sku_key=None: [])
    monkeypatch.setattr(local_cache_db, "get_cached_product", lambda **k: calls["approved"])
    calls["approved"] = None
    calls["status"] = []
    monkeypatch.setattr(local_cache_db, "add_rejected_image",
                        lambda *a, **k: calls["add_rejected_image"].append((a, k)) or True)
    monkeypatch.setattr(local_cache_db, "supersede_resolution",
                        lambda *a, **k: calls["supersede_resolution"].append((a, k)) or 1)
    monkeypatch.setattr(local_cache_db, "delete_curation_candidates",
                        lambda *a, **k: calls["delete_curation_candidates"].append((a, k)) or True)
    monkeypatch.setattr(local_cache_db, "update_task_status_by_row",
                        lambda *a, **k: calls["status"].append((a, k)) or True)
    monkeypatch.setattr(local_cache_db, "save_feedback", lambda *a, **k: True)
    monkeypatch.setattr(google_sheets, "init_async_queue", lambda *a, **k: None)
    monkeypatch.setattr(google_sheets, "stop_async_queue", lambda *a, **k: None)
    monkeypatch.setattr(google_sheets, "get_sheets_client", lambda: object())
    monkeypatch.setattr(google_sheets, "update_image_link",
                        lambda *a, **k: calls["update_image_link"].append((a, k)) or True)
    return cli_bridge, google_sheets, calls


def _reject(cli_bridge, url, **extra):
    # a valid GTIN: the product's key is its GTIN-14 (the bridge refuses a sku_key the product fields do not give)
    params = {"row_number": 14, "image_url": url, "product_name": "Laban Up", "brand": "Al Rawabi",
              "barcode": "6281007000024", "sku_key": "06281007000024", "reason_code": "WRONG_VARIANT"}
    params.update(extra)
    return cli_bridge.action_reject_image(params)


def test_reject_flow(bridge, monkeypatch):
    cli_bridge, google_sheets, calls = bridge
    url = "https://www.carrefouruae.com/img/laban-up-strawberry.jpg"
    ws = FakeWorksheet(f"needs_review:{url}")
    monkeypatch.setattr(google_sheets, "open_worksheet", lambda client, name: ws)

    result = _reject(cli_bridge, url)

    assert result["status"] == "success"
    (args, kwargs), = calls["add_rejected_image"]
    assert args[0] == "06281007000024" and args[1] == url and kwargs["reason_code"] == "WRONG_VARIANT"
    (args, kwargs), = calls["supersede_resolution"]
    assert args[0] == "06281007000024"
    assert calls["delete_curation_candidates"] == []      # C2: a rejection never wipes the candidate set
    (args, kwargs), = calls["status"]
    assert args[:2] == (14, "pending") and kwargs["sku_key"] == "06281007000024"
    # the sheet held exactly this URL (with the needs_review: prefix), so it is cleared
    (args, kwargs), = calls["update_image_link"]
    assert args[1] == 14 and args[2] == 3 and args[3] == ""
    assert kwargs["barcode"] == "6281007000024"          # the clear itself is identity-checked
    assert ws.cell_reads == [(14, 4)]
    assert result["sheet_cleared"] is True


def test_reject_clears_the_cell_with_the_brand_identity(bridge, monkeypatch):
    """The clear is identity-checked with the sheet brand too, so a same-name product of another brand that moved
    into this row number keeps its image."""
    cli_bridge, google_sheets, calls = bridge
    url = "https://www.carrefouruae.com/img/laban-up-strawberry.jpg"
    monkeypatch.setattr(google_sheets, "open_worksheet", lambda client, name: FakeWorksheet(url))
    assert _reject(cli_bridge, url, size="180ml")["sheet_cleared"] is True
    (args, kwargs), = calls["update_image_link"]
    assert kwargs["brand"] == "Al Rawabi" and kwargs["size"] == "180ml" and kwargs["product_name"] == "Laban Up"


def test_reject_does_not_clear_a_different_url(bridge, monkeypatch):
    cli_bridge, google_sheets, calls = bridge
    ws = FakeWorksheet("https://res.cloudinary.com/demo/approved-other.png")
    monkeypatch.setattr(google_sheets, "open_worksheet", lambda client, name: ws)

    result = _reject(cli_bridge, "https://www.carrefouruae.com/img/laban-up-strawberry.jpg")

    assert result["status"] == "success"
    assert calls["update_image_link"] == []
    assert result["sheet_cleared"] is False
    assert len(calls["add_rejected_image"]) == 1          # the negative is still recorded


def test_reject_rejects_unknown_reason_code(bridge, monkeypatch):
    cli_bridge, google_sheets, calls = bridge
    monkeypatch.setattr(google_sheets, "open_worksheet", lambda client, name: FakeWorksheet(""))
    result = _reject(cli_bridge, "https://x.ae/a.jpg", reason_code="LOOKS_BAD")
    assert result["status"] == "error"
    assert calls["add_rejected_image"] == [] and calls["supersede_resolution"] == []


def test_reject_with_research_returns_search_with_exclusions(bridge, monkeypatch):
    cli_bridge, google_sheets, calls = bridge
    import image_search
    import local_cache_db

    url = "https://www.noon.com/img/wrong.jpg"
    monkeypatch.setattr(google_sheets, "open_worksheet", lambda client, name: FakeWorksheet(url))
    monkeypatch.setattr(google_sheets, "get_brand_mappings", lambda *a, **k: {})
    monkeypatch.setattr(local_cache_db, "get_rejections", lambda sku: (["https://old.ae/rejected.jpg"], ["ffff0000ffff0000"]))
    seen = {}

    def fake_search(query, name, brand, **kwargs):
        seen.update(kwargs)
        kwargs["trace"]["outcome"] = {"decision": "NOT_FOUND", "failure_code": "NO_RESULTS", "provider_health": [],
                                      "queries": [], "sku_key": "06281007000024"}
        return None

    monkeypatch.setattr(image_search, "search_best_product_image", fake_search)
    result = _reject(cli_bridge, url, research=True)
    assert result["status"] == "not_found" and result["failure_code"] == "NO_RESULTS"
    assert result["rejection"]["reason_code"] == "WRONG_VARIANT"
    assert url in seen["exclude_urls"] and "https://old.ae/rejected.jpg" in seen["exclude_urls"]
    assert seen["exclude_phashes"] == ["ffff0000ffff0000"]
    assert seen["skip_cache"] is True


def test_reject_of_a_sibling_keeps_the_human_approval(bridge, monkeypatch):
    """Rejecting another candidate on an approved SKU never re-queues it (the worker could auto-publish over it)."""
    cli_bridge, google_sheets, calls = bridge
    calls["approved"] = {"cloudinary_url": "https://res.cloudinary.com/demo/approved.png",
                         "original_url": "https://www.carrefouruae.com/img/laban-up.jpg",
                         "verification_status": "human_approved"}
    monkeypatch.setattr(google_sheets, "open_worksheet",
                        lambda client, name: FakeWorksheet("https://res.cloudinary.com/demo/approved.png"))
    result = _reject(cli_bridge, "https://www.noon.com/img/laban-up-mango.jpg")
    assert result["status"] == "success" and result["approval_kept"] is True
    assert calls["supersede_resolution"] == [] and calls["status"] == []
    assert len(calls["add_rejected_image"]) == 1


def test_reject_of_the_approved_image_supersedes_and_requeues(bridge, monkeypatch):
    cli_bridge, google_sheets, calls = bridge
    approved_url = "https://res.cloudinary.com/demo/approved.png"
    calls["approved"] = {"cloudinary_url": approved_url, "original_url": "https://x.ae/src.jpg",
                         "verification_status": "human_approved"}
    monkeypatch.setattr(google_sheets, "open_worksheet", lambda client, name: FakeWorksheet(approved_url))
    result = _reject(cli_bridge, approved_url)
    assert result["approval_kept"] is False and result["sheet_cleared"] is True
    assert len(calls["supersede_resolution"]) == 1
    assert calls["status"][0][0][:2] == (14, "pending")


def test_reject_records_phash_from_the_sent_candidate_bytes(bridge, monkeypatch):
    """The catalog page stores no curation rows: the pHash comes from the candidate store by sha."""
    import io
    from PIL import Image, ImageDraw
    import image_processor
    cli_bridge, google_sheets, calls = bridge
    img = Image.new("RGB", (300, 300), "white")
    ImageDraw.Draw(img).rectangle([80, 40, 220, 260], fill=(200, 30, 30))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    monkeypatch.setattr(image_processor, "_load_from_candidate_store", lambda sha: buf.getvalue() if sha == "ab" * 32 else None)
    monkeypatch.setattr(google_sheets, "open_worksheet", lambda client, name: FakeWorksheet(""))
    _reject(cli_bridge, "https://www.noon.com/img/wrong.jpg", candidate_sha256="ab" * 32, page_url="https://www.noon.com/p/1")
    (args, kwargs), = calls["add_rejected_image"]
    assert kwargs["phash"] and len(kwargs["phash"]) == 16
    assert kwargs["page_url"] == "https://www.noon.com/p/1"


# ---------------------------------------------------------------------------
# C2: a rejection keeps the review alive
# ---------------------------------------------------------------------------

PICK = "https://www.carrefouruae.com/img/laban-up.jpg"
ALT = "https://www.noon.com/img/laban-up-180.jpg"
ALT2 = "https://www.lulu.ae/img/laban-up.jpg"


def _stored(*items):
    return [{"image_url": url, "status": status, "is_selected": int(status == "preselected"), "reasons": [],
             "evidence": {}, "sku_key": "06281007000024", "row_number": 14} for url, status in items]


@pytest.fixture
def review(bridge, monkeypatch):
    cli_bridge, google_sheets, calls = bridge
    import local_cache_db
    calls["stored"] = []
    calls["excluded"] = []
    calls["saved"] = []
    monkeypatch.setattr(local_cache_db, "get_curation_candidates", lambda row, sku_key=None: list(calls["stored"]))
    monkeypatch.setattr(local_cache_db, "exclude_curation_candidate",
                        lambda row, url, sku_key=None: calls["excluded"].append((row, url, sku_key)) or 1)
    monkeypatch.setattr(local_cache_db, "save_curation_candidates",
                        lambda *a, **k: calls["saved"].append((a, k)) or True)
    monkeypatch.setattr(local_cache_db, "get_tasks_by_sku", lambda sku: [])
    monkeypatch.setattr(local_cache_db, "get_rejections", lambda sku: ([], []))
    monkeypatch.setattr(google_sheets, "open_worksheet", lambda client, name: FakeWorksheet(""))
    return cli_bridge, google_sheets, calls


def test_rejecting_an_alternative_excludes_only_that_image(review):
    cli_bridge, google_sheets, calls = review
    calls["stored"] = _stored((PICK, "preselected"), (ALT, "eligible"), (ALT2, "eligible"))
    result = _reject(cli_bridge, ALT)
    assert result["status"] == "success"
    assert calls["excluded"] == [(14, ALT, "06281007000024")]
    assert calls["delete_curation_candidates"] == []
    assert calls["status"] == [] and calls["supersede_resolution"] == []      # the queue row and the pick stay
    assert result["queue_status"] is None and result["candidates_left"] == 2


def test_rejecting_the_pick_keeps_reviewing_the_remaining_candidates(review):
    cli_bridge, google_sheets, calls = review
    calls["stored"] = _stored((PICK, "preselected"), (ALT, "eligible"), (ALT2, "rejected"))
    result = _reject(cli_bridge, PICK)
    assert calls["excluded"] == [(14, PICK, "06281007000024")]
    (args, kwargs), = calls["status"]
    assert args[:2] == (14, "ready_for_review") and kwargs["sku_key"] == "06281007000024"
    assert result["queue_status"] == "ready_for_review" and result["candidates_left"] == 1


def test_rejecting_the_pick_with_nothing_eligible_left_requeues(review):
    cli_bridge, google_sheets, calls = review
    calls["stored"] = _stored((PICK, "preselected"), (ALT, "rejected"), (ALT2, "excluded"))
    result = _reject(cli_bridge, PICK)
    (args, kwargs), = calls["status"]
    assert args[:2] == (14, "pending") and kwargs["failure_code"] == "REJECTED"
    assert result["queue_status"] == "pending"
    assert calls["supersede_resolution"] == []          # nothing was approved or published: nothing to void


def test_rejecting_a_new_pick_never_voids_another_approved_image(review):
    """An auto-published image stays approved when the reviewer rejects another image (a research pick)."""
    cli_bridge, google_sheets, calls = review
    calls["approved"] = {"cloudinary_url": "https://res.cloudinary.com/demo/auto.png", "original_url": PICK,
                         "verification_status": "auto_verified"}
    calls["stored"] = _stored((ALT, "preselected"))
    result = _reject(cli_bridge, ALT)
    assert calls["supersede_resolution"] == [] and result["superseded"] == 0
    # rejecting the auto-published image itself voids it
    _reject(cli_bridge, PICK)
    assert len(calls["supersede_resolution"]) == 1


def test_rejecting_the_last_eligible_candidate_requeues(review):
    """The pick was rejected earlier (excluded); the reviewer now rejects the only candidate left: nothing remains
    to review, so the product goes back to the queue instead of waiting forever."""
    cli_bridge, google_sheets, calls = review
    calls["stored"] = _stored((PICK, "excluded"), (ALT, "eligible"), (ALT2, "rejected"))
    assert _reject(cli_bridge, ALT)["queue_status"] == "pending"
    (args, kwargs), = calls["status"]
    assert args[:2] == (14, "pending")


def test_the_catalog_pages_pick_counts_without_stored_candidates(review):
    """The catalog page searches live (nothing stored): it says which image was the pick."""
    cli_bridge, google_sheets, calls = review
    assert _reject(cli_bridge, ALT, candidate_status="eligible")["queue_status"] is None
    assert calls["status"] == []
    assert _reject(cli_bridge, PICK, candidate_status="preselected")["queue_status"] == "pending"


def test_a_status_already_set_is_not_written_again(review, monkeypatch):
    """Rewriting the same status would change the row's updated_at, and the page's next approval would be refused
    as state_changed."""
    import local_cache_db
    cli_bridge, google_sheets, calls = review
    monkeypatch.setattr(local_cache_db, "get_task_by_row", lambda row: {
        "sku_key": "06281007000024", "barcode": "6281007000024", "product_name": "Laban Up",
        "status": "ready_for_review", "updated_at": "2026-10-03 10:00:00"})
    calls["stored"] = _stored((PICK, "preselected"), (ALT, "eligible"))
    result = _reject(cli_bridge, PICK)
    assert result["queue_status"] == "ready_for_review" and calls["status"] == []
    assert result["current"]["queue_updated_at"] == "2026-10-03 10:00:00"


def test_research_saves_the_fresh_candidates_and_keeps_the_review(review, monkeypatch):
    cli_bridge, google_sheets, calls = review
    import image_search
    calls["stored"] = _stored((PICK, "preselected"))
    fresh = [{"url": ALT, "status": "preselected", "reasons": ["tier T1"], "page_url": "https://www.noon.com/p/1"},
             {"url": ALT2, "status": "eligible", "reasons": []},
             {"url": PICK, "status": "eligible", "reasons": []}]

    def fake_search(query, name, brand, **kwargs):
        assert PICK in kwargs["exclude_urls"] and kwargs["skip_cache"] is True
        kwargs["trace"]["outcome"] = {"decision": "REVIEW_PRESELECTED", "failure_code": None}
        return {"url": ALT, "decision": "REVIEW_PRESELECTED", "source": "serper", "preselect": True,
                "candidates": fresh}

    monkeypatch.setattr(image_search, "search_best_product_image", fake_search)
    monkeypatch.setattr(google_sheets, "get_brand_mappings", lambda *a, **k: {})
    result = _reject(cli_bridge, PICK, research=True)
    assert result["status"] == "review" and result["candidates_saved"] == 2
    (args, kwargs), = calls["saved"]
    assert args[0] == 14 and [c["url"] for c in args[3]] == [ALT, ALT2] and args[4] == ALT
    assert kwargs["sku_key"] == "06281007000024" and kwargs["run_id"].startswith("research-")
    (args, kwargs), = calls["status"]
    assert args[:2] == (14, "ready_for_review")
    assert result["rejection"]["queue_status"] == "ready_for_review"


def test_research_saves_each_alternatives_own_warnings(review, monkeypatch):
    """The server-side research save (contract C2) goes through main.collect_candidates: every fresh candidate
    keeps its own display warnings as warn:<code> reasons, which the review screen reads."""
    cli_bridge, google_sheets, calls = review
    import image_search
    calls["stored"] = _stored((PICK, "preselected"))
    fresh = [{"url": ALT, "status": "preselected", "reasons": ["tier T1"], "warnings": ["size_unverified"]},
             {"url": ALT2, "status": "eligible", "reasons": [], "warnings": ["foreign_store", "low_resolution"]}]

    def fake_search(query, name, brand, **kwargs):
        kwargs["trace"]["outcome"] = {"decision": "REVIEW_PRESELECTED", "failure_code": None}
        return {"url": ALT, "decision": "REVIEW_PRESELECTED", "source": "serper", "preselect": True,
                "candidates": fresh}

    monkeypatch.setattr(image_search, "search_best_product_image", fake_search)
    monkeypatch.setattr(google_sheets, "get_brand_mappings", lambda *a, **k: {})
    assert _reject(cli_bridge, PICK, research=True)["candidates_saved"] == 2
    (args, kwargs), = calls["saved"]
    reasons = {c["url"]: c["reasons"] for c in args[3]}
    assert reasons[ALT] == ["tier T1", "warn:size_unverified"]
    assert reasons[ALT2] == ["warn:foreign_store", "warn:low_resolution"]


def test_research_that_finds_nothing_keeps_the_remaining_candidates(review, monkeypatch):
    cli_bridge, google_sheets, calls = review
    import image_search
    calls["stored"] = _stored((PICK, "preselected"), (ALT, "eligible"))

    def fake_search(query, name, brand, **kwargs):
        kwargs["trace"]["outcome"] = {"decision": "NOT_FOUND", "failure_code": "NO_RESULTS"}
        return None

    monkeypatch.setattr(image_search, "search_best_product_image", fake_search)
    monkeypatch.setattr(google_sheets, "get_brand_mappings", lambda *a, **k: {})
    result = _reject(cli_bridge, PICK, research=True)
    assert result["status"] == "not_found" and result["candidates_saved"] == 0 and calls["saved"] == []
    (args, kwargs), = calls["status"]
    assert args[:2] == (14, "ready_for_review")                  # ALT is still there to review


def test_research_never_reopens_a_kept_approval(review, monkeypatch):
    cli_bridge, google_sheets, calls = review
    import image_search
    calls["approved"] = {"cloudinary_url": "https://res.cloudinary.com/demo/approved.png",
                         "original_url": "https://x.ae/src.jpg", "verification_status": "human_approved"}

    def fake_search(query, name, brand, **kwargs):
        kwargs["trace"]["outcome"] = {"decision": "REVIEW_UNSELECTED"}
        return {"url": None, "decision": "REVIEW_UNSELECTED", "source": "serper",
                "candidates": [{"url": ALT2, "status": "eligible"}]}

    monkeypatch.setattr(image_search, "search_best_product_image", fake_search)
    monkeypatch.setattr(google_sheets, "get_brand_mappings", lambda *a, **k: {})
    result = _reject(cli_bridge, ALT, research=True)
    assert result["candidates_saved"] == 1 and result["rejection"]["approval_kept"] is True
    assert calls["status"] == [] and calls["supersede_resolution"] == []


def test_the_rejected_image_is_cleared_from_every_row_of_the_product(review, monkeypatch):
    import local_cache_db
    cli_bridge, google_sheets, calls = review

    class Sheet(FakeWorksheet):
        def cell(self, row, col):
            self.cell_reads.append((row, col))
            return FakeCell({14: f"needs_review:{PICK}", 30: PICK, 31: "https://res/other.png"}.get(row, ""))

    ws = Sheet("")
    monkeypatch.setattr(google_sheets, "open_worksheet", lambda client, name: ws)
    monkeypatch.setattr(local_cache_db, "get_tasks_by_sku", lambda sku: [
        {"row_number": 14, "sku_key": sku, "barcode": "6281007000024", "product_name": "Laban Up", "brand": "Al Rawabi"},
        {"row_number": 30, "sku_key": sku, "barcode": "6281007000024", "product_name": "LABAN UP", "brand": "AL RAWABI",
         "payload_json": '{"size": "180 ml"}'},
        {"row_number": 31, "sku_key": sku, "barcode": "6281007000024", "product_name": "Laban Up", "brand": "Al Rawabi"}])
    result = _reject(cli_bridge, PICK)
    assert result["sheet_cleared"] is True
    cleared = [(a[1], a[3], k) for a, k in calls["update_image_link"]]
    assert [c[:2] for c in cleared] == [(14, ""), (30, "")]
    assert cleared[1][2] == {"barcode": "6281007000024", "product_name": "LABAN UP", "size": "180 ml",
                             "brand": "AL RAWABI"}
