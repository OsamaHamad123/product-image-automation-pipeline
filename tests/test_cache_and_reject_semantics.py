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
    assert calls["delete_curation_candidates"] == [((14,), {"sku_key": "06281007000024"})]
    (args, kwargs), = calls["status"]
    assert args[:2] == (14, "pending") and kwargs["sku_key"] == "06281007000024"
    # the sheet held exactly this URL (with the needs_review: prefix), so it is cleared
    (args, kwargs), = calls["update_image_link"]
    assert args[1] == 14 and args[2] == 3 and args[3] == ""
    assert kwargs["barcode"] == "6281007000024"          # the clear itself is identity-checked
    assert ws.cell_reads == [(14, 4)]
    assert result["sheet_cleared"] is True


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
