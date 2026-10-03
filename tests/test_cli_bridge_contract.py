"""cli_bridge action contract used by the dashboard (search / select_image), offline."""

import base64
import json

import pytest

pytestmark = pytest.mark.usefixtures("offline")


@pytest.fixture
def bridge(offline, monkeypatch, tmp_path):
    import cli_bridge
    import google_sheets
    import local_cache_db

    monkeypatch.setattr(cli_bridge, "LOG_PATH", str(tmp_path / "search.log"))
    monkeypatch.setattr(google_sheets, "get_sheets_client", lambda: object())
    monkeypatch.setattr(google_sheets, "get_brand_mappings", lambda *a, **k: {})
    monkeypatch.setattr(google_sheets, "align_brand_via_gemini",
                        lambda *a, **k: pytest.fail("align_brand_via_gemini must not be called"))
    monkeypatch.setattr(local_cache_db, "get_task_by_row", lambda row: None)
    monkeypatch.setattr(local_cache_db, "get_rejections", lambda sku: (["https://old.ae/rejected.jpg"], ["0f0f0f0f0f0f0f0f"]))
    return cli_bridge


V2_RESULT = {
    "url": "https://www.luluhypermarket.com/medias/almarai-fresh-milk-1l.jpg",
    "title": "Almarai Fresh Milk Full Fat 1L", "width": 1000, "height": 1000, "source": "serper",
    "needs_review": True, "preselect": True, "clip_score": None, "decision": "REVIEW_PRESELECTED",
    "failure_code": None, "sku_key": "06281007000028", "content_sha256": "ab" * 32,
    "page_url": "https://www.luluhypermarket.com/en-ae/almarai-fresh-milk-1l/p/1",
    "candidates": [
        {"url": "https://www.luluhypermarket.com/medias/almarai-fresh-milk-1l.jpg", "title": "Almarai Fresh Milk 1L",
         "page_url": "https://www.luluhypermarket.com/p/1", "domain": "luluhypermarket.com", "status": "preselected",
         "reasons": ["tier T1", "vlm MATCH"], "evidence": {"brand": True, "size": "1000ml"},
         "vlm": {"decision": "MATCH", "size_text": "1L"},
         "scores": {"identity_score": 0.94, "quality_score": 0.8, "relevance_score": 0.94}},
        {"url": "https://www.noon.com/almarai-low-fat-1l.jpg", "title": "Almarai Low Fat Milk 1L",
         "page_url": "https://www.noon.com/p/2", "domain": "noon.com", "status": "rejected",
         "reasons": ["variant conflict: fat"], "evidence": {"brand": True}, "vlm": None,
         "scores": {"identity_score": 0.2, "quality_score": 0.7, "relevance_score": 0.2}},
    ],
}


def _run_main(cli_bridge, capsys, action, params):
    arg = base64.b64encode(json.dumps(params).encode("utf-8")).decode("ascii")
    code = cli_bridge.main(["cli_bridge.py", action, arg])
    out = capsys.readouterr().out
    return code, out


def test_search_contract(bridge, monkeypatch, capsys):
    import image_search
    seen = {}

    def fake_search(query, name, brand, **kwargs):
        seen.update(kwargs, query=query)
        print("library chatter that must not reach stdout")        # noqa: T201 - simulates legacy prints
        kwargs["trace"]["outcome"] = {"decision": "REVIEW_PRESELECTED", "failure_code": None,
                                      "provider_health": [{"provider": "serper", "status": "ok"}],
                                      "queries": ["q1"], "sku_key": "06281007000028"}
        kwargs["trace"]["steps"] = [{"step_name": "v2", "candidates": V2_RESULT["candidates"]}]
        return dict(V2_RESULT)

    monkeypatch.setattr(image_search, "search_best_product_image", fake_search)
    params = {"product_name": "Fresh Milk Full Fat 1L", "brand": "Almarai", "barcode": "6281007000028",
              "custom_query": "المراعي حليب طازج كامل الدسم 1 لتر", "product_name_ar": "حليب طازج",
              "category": "Dairy", "exclude_urls": ["https://ui.ae/excluded.jpg"], "row_number": 4}
    code, out = _run_main(bridge, capsys, "search", params)

    assert code == 0
    # exactly one JSON document on stdout, nothing else
    lines = [line for line in out.splitlines() if line.strip()]
    assert len(lines) == 1
    response = json.loads(lines[0])
    assert "library chatter" not in out

    assert seen["custom_query"] == "المراعي حليب طازج كامل الدسم 1 لتر"
    assert seen["exclude_urls"] == ["https://ui.ae/excluded.jpg", "https://old.ae/rejected.jpg"]
    assert seen["exclude_phashes"] == ["0f0f0f0f0f0f0f0f"]
    assert seen["product_name_ar"] == "حليب طازج" and seen["category"] == "Dairy"

    assert response["status"] == "review"
    assert response["decision"] == "REVIEW_PRESELECTED"
    assert "failure_code" in response and response["failure_code"] is None
    assert response["sku_key"] == "06281007000028"
    assert response["selected_image"]["url"] == V2_RESULT["url"]
    assert response["provider_health"] == [{"provider": "serper", "status": "ok"}]
    cands = response["candidates"]
    assert [c["status"] for c in cands] == ["preselected", "rejected"]
    assert cands[1]["reasons"] == ["variant conflict: fat"]
    assert cands[0]["evidence"] == {"brand": True, "size": "1000ml"}
    assert cands[0]["vlm"]["decision"] == "MATCH"
    assert set(cands[0]) >= {"url", "title", "page_url", "domain", "status", "reasons", "evidence", "vlm", "scores"}


def test_default_query_is_not_sent_as_custom(bridge, monkeypatch):
    import image_search
    seen = {}

    def fake_search(query, name, brand, **kwargs):
        seen.update(kwargs, query=query)
        kwargs["trace"]["outcome"] = {"decision": "PROVIDER_DOWN", "failure_code": "PROVIDER_DOWN",
                                      "provider_health": [{"provider": "serper", "status": "quota"}]}
        return None

    monkeypatch.setattr(image_search, "search_best_product_image", fake_search)
    response = bridge.action_search({"product_name": "Fresh Milk 1L", "brand": "Almarai",
                                     "custom_query": "Fresh Milk 1L  Almarai"})
    assert seen["custom_query"] is None
    assert seen["query"] == "Fresh Milk 1L Almarai"
    assert response["status"] == "provider_down" and response["selected_image"] is None
    assert response["failure_code"] == "PROVIDER_DOWN"


def test_unselected_review_has_no_selected_image(bridge, monkeypatch):
    import image_search

    def fake_search(query, name, brand, **kwargs):
        kwargs["trace"]["outcome"] = {"decision": "REVIEW_UNSELECTED", "failure_code": None}
        return dict(V2_RESULT, decision="REVIEW_UNSELECTED", preselect=False)

    monkeypatch.setattr(image_search, "search_best_product_image", fake_search)
    response = bridge.action_search({"product_name": "Fresh Milk 1L", "brand": "Almarai"})
    assert response["status"] == "review"
    assert response["selected_image"] is None
    assert len(response["candidates"]) == 2


def _canvas(path, size=(800, 800)):
    from PIL import Image
    Image.new("RGB", size, "white").save(path)
    return str(path)


@pytest.fixture
def select_env(bridge, monkeypatch, tmp_path):
    import cloudinary_storage
    import google_sheets
    import image_processor
    import local_cache_db
    from PIL import Image

    events = []
    state = {"isolated": True}

    def fake_process(image_url_or_path, product_name, brand, target_width=0, target_height=0, bg_method=None,
                     candidate_sha256=None, enhance=False):
        events.append(("process", image_url_or_path, candidate_sha256))
        path = _canvas(tmp_path / "canvas.png")
        return image_processor.ProcessResult(path, state["isolated"], "photoroom" if state["isolated"] else "none",
                                             None, 800, 800)

    def fake_upload(local_path, product_name, brand, folder=None, tags=None, **kwargs):
        with Image.open(local_path) as img:
            events.append(("upload", img.size, folder))
        return "https://res.cloudinary.com/demo/image/upload/q_auto,f_auto/products/dairy/abc.png"

    class WS:
        title = "Products"

        def row_values(self, n):
            return ["Barcode", "Product Name", "Brand", "Drive Image Link"]

    monkeypatch.setattr(image_processor, "process_product_image_result", fake_process)
    monkeypatch.setattr(image_processor, "extract_metadata_from_image",
                        lambda *a, **k: events.append(("metadata_read",)) or {"ingredients": "Fresh cow milk"})
    monkeypatch.setattr(cloudinary_storage, "upload_product_image_to_cloudinary", fake_upload)
    monkeypatch.setattr(google_sheets, "init_async_queue", lambda *a, **k: None)
    monkeypatch.setattr(google_sheets, "stop_async_queue", lambda *a, **k: None)
    monkeypatch.setattr(google_sheets, "open_worksheet", lambda c, n: WS())
    monkeypatch.setattr(google_sheets, "update_image_link",
                        lambda ws, row, col, value, **k: events.append(("link", row, col, value, k)) or True)
    monkeypatch.setattr(google_sheets, "update_product_metadata",
                        lambda ws, row, md, **k: events.append(("metadata_write", row, dict(md), k)) or True)
    monkeypatch.setattr(local_cache_db, "save_product_resolution",
                        lambda *a, **k: events.append(("resolution", a, k)) or True)
    monkeypatch.setattr(local_cache_db, "update_task_status_by_row", lambda *a, **k: True)
    monkeypatch.setattr(local_cache_db, "delete_curation_candidates", lambda *a, **k: True)
    return bridge, events, state


# A valid GTIN, so the product's key is its GTIN-14 (the bridge refuses a sku_key the product fields do not give).
SELECT_PARAMS = {"image_url": V2_RESULT["url"], "product_name": "Fresh Milk Full Fat 1L", "brand": "Almarai",
                 "row_number": 4, "barcode": "6281007000024", "sku_key": "06281007000024",
                 "content_sha256": "ab" * 32, "category_l1_en": "Dairy & Eggs", "category_l2_en": "Milk",
                 "upscale": True, "target_width": 800, "target_height": 800}


def test_select_no_upscale(select_env):
    bridge, events, state = select_env
    result = bridge.action_select_image(dict(SELECT_PARAMS))

    assert result["status"] == "success" and "warning" not in result
    kinds = [e[0] for e in events]
    upload = next(e for e in events if e[0] == "upload")
    assert upload[1] == (800, 800), "the published file is the 800x800 canvas, not an upscaled copy"
    assert events[0] == ("process", V2_RESULT["url"], "ab" * 32)
    # metadata reaches the sheet only after the upload succeeded
    assert kinds.index("upload") < kinds.index("metadata_write")
    link = next(e for e in events if e[0] == "link")
    assert link[3] == "https://res.cloudinary.com/demo/image/upload/q_auto,f_auto/products/dairy/abc.png"
    assert link[4]["barcode"] == "6281007000024"
    md = next(e for e in events if e[0] == "metadata_write")[2]
    assert md.get("category_l1_en") and md.get("ingredients") == "Fresh cow milk"
    _, args, kwargs = next(e for e in events if e[0] == "resolution")
    assert args[0] == "6281007000024"
    assert kwargs["verification_status"] == "human_approved"
    assert kwargs["approved_by"] == "human"
    assert kwargs["sku_key"] == "06281007000024"


@pytest.mark.parametrize("sent, saved, used", [
    (None, True, True),        # the review page sends nothing: the saved «تحسين الألوان» setting applies
    (None, False, False),
    ("false", True, True),     # one processing profile: a value in the request no longer changes it
    ("true", False, False),
])
def test_select_uses_the_saved_enhancement_setting(select_env, monkeypatch, sent, saved, used):
    import config
    import image_processor

    bridge, events, state = select_env
    original = image_processor.process_product_image_result
    seen = {}

    def spy(*args, **kwargs):
        seen["enhance"] = kwargs.get("enhance")
        return original(*args, **kwargs)

    monkeypatch.setattr(image_processor, "process_product_image_result", spy)
    monkeypatch.setattr(config, "ENABLE_IMAGE_ENHANCEMENT", saved, raising=False)
    params = {k: v for k, v in SELECT_PARAMS.items() if k != "enhance"}
    if sent is not None:
        params["enhance"] = sent
    assert bridge.action_select_image(params)["status"] == "success"
    assert seen["enhance"] is used


def test_one_processing_profile_for_auto_publish_approval_and_upload(select_env, monkeypatch, tmp_path):
    """The worker's auto-publish, the reviewer's approval and a manual upload all process with the Settings
    profile: the Settings canvas (not IMAGE_TARGET_SIZE), the saved enhancement and background method; what a
    request asks for does not change it."""
    import config
    import image_processor
    import local_cache_db
    import main

    bridge, events, state = select_env
    monkeypatch.setattr(config, "OUTPUT_CANVAS_SIZE", 1000, raising=False)
    monkeypatch.setattr(config, "IMAGE_TARGET_SIZE", (800, 800), raising=False)
    monkeypatch.setattr(config, "ENABLE_IMAGE_ENHANCEMENT", True, raising=False)
    monkeypatch.setattr(config, "BG_REMOVAL_METHOD", "remove_bg_api", raising=False)
    monkeypatch.setattr(local_cache_db, "get_cached_product", lambda **k: None)
    monkeypatch.setattr(local_cache_db, "delete_product_failure", lambda *a, **k: True)
    original = image_processor.process_product_image_result
    seen = []

    def spy(src, name, brand, target_width=0, target_height=0, bg_method=None, candidate_sha256=None, enhance=False):
        seen.append((image_processor._resolve_canvas_size(target_width, target_height),
                     image_processor._normalise_method(bg_method), enhance))
        return original(src, name, brand, target_width=target_width, target_height=target_height,
                        bg_method=bg_method, candidate_sha256=candidate_sha256, enhance=enhance)

    monkeypatch.setattr(image_processor, "process_product_image_result", spy)
    task = {"id": 7, "row_number": 4, "product_name": SELECT_PARAMS["product_name"], "brand": "Almarai",
            "barcode": SELECT_PARAMS["barcode"], "payload_json": "{}", "sku_key": SELECT_PARAMS["sku_key"]}
    assert main.auto_approve_product(task, {"url": V2_RESULT["url"]}, object(), 3,
                                     sku_key=SELECT_PARAMS["sku_key"]) == "published"
    sent = dict(SELECT_PARAMS, target_width=800, target_height=800, enhance="false", bg_removal_method="none")
    assert bridge.action_select_image(sent)["status"] == "success"
    upload = tmp_path / "manual.png"
    _canvas(upload, (300, 300))
    params = {k: SELECT_PARAMS[k] for k in ("row_number", "product_name", "brand", "barcode", "sku_key")}
    assert bridge.action_upload_manual_image(dict(params, file_path=str(upload), enhance="false",
                                                  target_width=640, target_height=640))["status"] == "success"
    assert seen == [((1000, 1000), "remove_bg_api", True)] * 3


def test_approval_and_upload_write_with_the_brand_identity(select_env, tmp_path):
    """Without the brand in the row identity, a stale row number now holding a same-name product of another brand
    would receive this image; the link and the metadata writes carry the sheet brand (as auto-publish does)."""
    bridge, events, state = select_env
    assert bridge.action_select_image(dict(SELECT_PARAMS, size="1L"))["status"] == "success"
    upload = tmp_path / "manual.png"
    _canvas(upload, (300, 300))
    params = {k: SELECT_PARAMS[k] for k in ("row_number", "product_name", "brand", "barcode", "sku_key")}
    assert bridge.action_upload_manual_image(dict(params, file_path=str(upload)))["status"] == "success"
    writes = [e[-1] for e in events if e[0] in ("link", "metadata_write")]
    assert len(writes) == 4
    assert all(w["brand"] == "Almarai" and w["barcode"] == "6281007000024" for w in writes)
    assert writes[0]["size"] == "1L"


def test_select_not_isolated_writes_needs_review(select_env):
    bridge, events, state = select_env
    state["isolated"] = False
    result = bridge.action_select_image(dict(SELECT_PARAMS))
    assert result["status"] == "success"
    assert result["warning"] == "background_not_removed"
    link = next(e for e in events if e[0] == "link")
    assert link[3].startswith("needs_review:https://res.cloudinary.com/")


def test_select_requires_identity(select_env, monkeypatch):
    bridge, events, state = select_env
    import local_cache_db
    params = dict(SELECT_PARAMS)
    params.pop("sku_key")
    assert bridge.action_select_image(params)["status"] == "failed"

    monkeypatch.setattr(local_cache_db, "get_task_by_row",
                        lambda row: {"barcode": "6281007000024", "sku_key": "06281007000024"})
    params = dict(SELECT_PARAMS)
    params["barcode"] = ""
    result = bridge.action_select_image(params)
    assert result["status"] == "failed" and "barcode" in result["error"]
    assert not any(e[0] == "upload" for e in events)


def test_select_upload_failure_writes_nothing(select_env, monkeypatch):
    bridge, events, state = select_env
    import cloudinary_storage
    monkeypatch.setattr(cloudinary_storage, "upload_product_image_to_cloudinary", lambda *a, **k: None)
    result = bridge.action_select_image(dict(SELECT_PARAMS))
    assert result["status"] == "failed"
    assert not any(e[0] in ("link", "metadata_write", "resolution") for e in events)


def test_select_uses_the_ui_candidate_sha256(select_env):
    """The dashboards post candidate_sha256: the verified bytes must be published, not a re-download."""
    bridge, events, state = select_env
    params = dict(SELECT_PARAMS)
    params["candidate_sha256"] = params.pop("content_sha256")
    assert bridge.action_select_image(params)["status"] == "success"
    assert events[0] == ("process", V2_RESULT["url"], "ab" * 32)


def test_select_ignores_a_queue_row_of_another_product(select_env, monkeypatch):
    """After a sheet row shift the queue row at that number belongs to another product; it neither blocks
    the approval nor is the product whose queue row / candidates get cleaned up."""
    bridge, events, state = select_env
    import local_cache_db
    cleaned = []
    monkeypatch.setattr(local_cache_db, "get_task_by_row",
                        lambda row: {"barcode": "6291003000013", "sku_key": "06291003000013", "product_name": "Masafi"})
    monkeypatch.setattr(local_cache_db, "update_task_status_by_row", lambda *a, **k: cleaned.append(("status", a, k)))
    monkeypatch.setattr(local_cache_db, "delete_curation_candidates", lambda *a, **k: cleaned.append(("delete", a, k)))
    result = bridge.action_select_image(dict(SELECT_PARAMS))
    assert result["status"] == "success"
    assert all(k["sku_key"] == "06281007000024" for _, _, k in cleaned) and len(cleaned) == 2


def test_select_after_owner_fixed_the_barcode(select_env, monkeypatch):
    """The queue still holds the old (corrupt) barcode of the same product: approval is not refused."""
    bridge, events, state = select_env
    import local_cache_db
    monkeypatch.setattr(local_cache_db, "get_task_by_row",
                        lambda row: {"barcode": "6.28E+12", "sku_key": "06281007000024", "product_name": "Fresh Milk"})
    assert bridge.action_select_image(dict(SELECT_PARAMS))["status"] == "success"


# ---------------------------------------------------------------------------
# Error payloads never carry exception text (fastapi_server returns them over HTTP)
# ---------------------------------------------------------------------------

SECRET = "db-password-in-a-traceback"


def _boom(*a, **k):
    raise RuntimeError(SECRET)


def test_search_error_hides_exception_text(bridge, monkeypatch):
    import image_search
    monkeypatch.setattr(image_search, "search_best_product_image", _boom)
    result = bridge.action_search({"product_name": "Almarai Fresh Milk 1L", "brand": "Almarai"})
    assert result["status"] == "error" and result["failure_code"] == "SEARCH_ERROR"
    assert SECRET not in json.dumps(result, ensure_ascii=False)


def test_sheet_actions_hide_exception_text(bridge, monkeypatch):
    import google_sheets
    monkeypatch.setattr(google_sheets, "get_sheets_client", _boom)
    monkeypatch.setattr(bridge, "_open_sheet", _boom)
    for result in (bridge.action_sheet_preview({"spreadsheet_url": "https://docs.google.com/x"}),
                   bridge.action_get_products({})):
        assert result["status"] == "failed" and result["error"]
        assert SECRET not in json.dumps(result, ensure_ascii=False)


def test_main_hides_exception_text(bridge, monkeypatch, capsys):
    monkeypatch.setitem(bridge.ACTIONS, "search", _boom)
    monkeypatch.setattr(bridge.config, "log_error_to_laravel", lambda *a, **k: None)
    _, out = _run_main(bridge, capsys, "search", {"product_name": "x"})
    assert json.loads(out)["status"] == "error"
    assert SECRET not in out


def test_log_values_stay_on_one_line():
    import google_sheets
    assert google_sheets._one_line("sheet\r\nFAKE LOG LINE\nx\ry") == "sheet FAKE LOG LINE x y"
