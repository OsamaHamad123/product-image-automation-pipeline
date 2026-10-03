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
    monkeypatch.setattr(local_cache_db, "find_image_owners", lambda *a, **k: [])
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


def test_an_explicit_approval_of_another_products_image_is_written_with_a_warning(select_env, monkeypatch):
    """Auto-publish refuses an image another product already owns; the reviewer's explicit approval goes through
    but names the other product, and the canvas hash is stored with the approval."""
    import local_cache_db
    bridge, events, state = select_env
    owner = {"sku_key": "06291003000013", "product_name": "Masafi Water 1.5L", "brand": "Masafi",
             "cloudinary_url": "https://res.cloudinary.com/demo/masafi.png", "verification_status": "human_approved",
             "match": "phash", "distance": 2}
    seen = []
    monkeypatch.setattr(local_cache_db, "find_image_owners",
                        lambda url, phash, sku_key=None, product_name=None, **k: seen.append((url, sku_key)) or [owner])
    monkeypatch.setattr(main_module(), "_canvas_phash", lambda path: "00ff00ff00ff00ff")
    result = bridge.action_select_image(dict(SELECT_PARAMS))
    assert result["status"] == "success"
    assert result["warning"] == "duplicate_image" and result["warnings"] == ["duplicate_image"]
    assert result["duplicate_of"] == [owner]
    assert seen == [(result["image_link"], "06281007000024")]
    assert any(e[0] == "link" for e in events)
    _, args, kwargs = next(e for e in events if e[0] == "resolution")
    assert kwargs["perceptual_hash"] == "00ff00ff00ff00ff" and kwargs["verification_status"] == "human_approved"


def main_module():
    import main
    return main


def test_a_product_on_two_rows_gets_the_approval_on_both(select_env, monkeypatch, tmp_path):
    """The same product twice in the sheet: the approval (and the upload) is written to every row of its key, each
    write carrying that row's own identity, so the second row is not left empty while the queue says completed."""
    import local_cache_db
    bridge, events, state = select_env
    sku = SELECT_PARAMS["sku_key"]
    rows = [{"row_number": 4, "sku_key": sku, "barcode": "6281007000024", "product_name": "Fresh Milk Full Fat 1L",
             "brand": "Almarai", "payload_json": '{"size": "1L"}'},
            {"row_number": 9, "sku_key": sku, "barcode": "6281007000024", "product_name": "FRESH MILK FULL FAT 1 L",
             "brand": "ALMARAI", "payload_json": '{"size": "1 L"}'}]
    asked = []
    monkeypatch.setattr(local_cache_db, "get_tasks_by_sku", lambda key: asked.append(key) or [dict(r) for r in rows])
    result = bridge.action_select_image(dict(SELECT_PARAMS, size="1L"))
    assert result["status"] == "success" and result["rows_written"] == [4, 9] and asked == [sku]
    links = [(e[1], e[4]) for e in events if e[0] == "link"]
    assert links == [(4, {"barcode": "6281007000024", "product_name": "Fresh Milk Full Fat 1L", "size": "1L",
                          "brand": "Almarai"}),
                     (9, {"barcode": "6281007000024", "product_name": "FRESH MILK FULL FAT 1 L", "size": "1 L",
                          "brand": "ALMARAI"})]
    assert [e[1] for e in events if e[0] == "metadata_write"] == [4, 9]

    events.clear()
    upload = tmp_path / "manual.png"
    _canvas(upload, (300, 300))
    params = {k: SELECT_PARAMS[k] for k in ("row_number", "product_name", "brand", "barcode", "sku_key")}
    result = bridge.action_upload_manual_image(dict(params, file_path=str(upload)))
    assert result["rows_written"] == [4, 9] and [e[1] for e in events if e[0] == "link"] == [4, 9]


def test_select_not_isolated_is_refused_and_writes_nothing(select_env):
    """An approval whose background was not removed at all is not published (it used to be written needs_review:
    while the approval was saved human_approved and the row completed): the product stays in review."""
    bridge, events, state = select_env
    state["isolated"] = False
    for anyway in (False, True):                      # a failed background removal is never published anyway
        result = bridge.action_select_image(dict(SELECT_PARAMS, publish_anyway=anyway))
        assert (result["status"], result["error_code"]) == ("failed", "background_failed")
        assert result["publish_anyway_allowed"] is False and result["quality_flags"] == [] and result["error"]
    assert not any(e[0] in ("upload", "link", "resolution") for e in events)


def test_a_cutout_that_failed_the_quality_gate_is_refused_with_its_flags(select_env, monkeypatch):
    """The image package returns a cutout that failed its quality gate as not isolated (with quality_flags): never a
    clean publish nor a needs_review: approval, and the flags reach the page."""
    import image_processor
    bridge, events, state = select_env
    original = image_processor.process_product_image_result

    def gated(*a, **k):
        result = original(*a, **k)
        result.isolated = False
        result.quality_flags = ["halo_fringe"]
        return result

    monkeypatch.setattr(image_processor, "process_product_image_result", gated)
    result = bridge.action_select_image(dict(SELECT_PARAMS, publish_anyway=True))
    assert (result["status"], result["error_code"]) == ("failed", "background_failed")
    assert result["quality_flags"] == ["halo_fringe"] and result["publish_anyway_allowed"] is False
    assert not any(e[0] in ("link", "resolution") for e in events)


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
# C1: an approval or upload never replaces a decision the reviewer's page did not show
# ---------------------------------------------------------------------------

HUMAN = {"cloudinary_url": "https://res.cloudinary.com/demo/other-reviewer.png", "original_url": "https://lulu.ae/x.jpg",
         "verification_status": "human_approved", "approved_by": "human"}


@pytest.fixture
def stale_env(select_env, monkeypatch):
    import datetime
    import local_cache_db
    bridge, events, state = select_env
    state.update(approval=None, task=None, fenced=[])
    monkeypatch.setattr(local_cache_db, "get_cached_product", lambda **k: state["approval"])
    monkeypatch.setattr(local_cache_db, "get_task_by_row", lambda row: state["task"])
    monkeypatch.setattr(local_cache_db, "release_worker_claims",
                        lambda row, sku_key=None, **k: state["fenced"].append((row, sku_key)) or 1)
    state["now"] = datetime.datetime.now().replace(microsecond=0)
    return bridge, events, state


def _approved(state, seconds_ago, **extra):
    import datetime
    return dict(HUMAN, resolved_at=state["now"] - datetime.timedelta(seconds=seconds_ago), **extra)


def _queue_row(status="ready_for_review", updated_at="2026-10-03 10:00:00"):
    return {"row_number": 4, "sku_key": SELECT_PARAMS["sku_key"], "product_name": SELECT_PARAMS["product_name"],
            "barcode": SELECT_PARAMS["barcode"], "status": status, "updated_at": updated_at}


def _writes(events):
    return [e for e in events if e[0] in ("process", "link", "resolution")]


def test_an_approval_the_page_did_not_show_is_not_replaced(stale_env):
    bridge, events, state = stale_env
    state["approval"] = _approved(state, 3600)
    state["task"] = _queue_row("completed")
    expected = {"queue_status": None, "queue_updated_at": None, "approved_url": None}
    result = bridge.action_select_image(dict(SELECT_PARAMS, expected_state=expected))
    assert result["status"] == "failed" and result["error_code"] == "already_approved" and result["error"]
    assert result["current"]["approved_url"] == HUMAN["cloudinary_url"]
    assert result["current"]["approval_status"] == "human_approved"
    assert result["current"]["queue_status"] == "completed"
    assert _writes(events) == [] and state["fenced"] == []

    # the page shows it (the sheet link, with or without the needs_review: prefix): the approval replaces it
    shown = dict(expected, approved_url="needs_review:" + HUMAN["cloudinary_url"])
    assert bridge.action_select_image(dict(SELECT_PARAMS, expected_state=shown))["status"] == "success"
    # or the reviewer confirmed the replacement
    assert bridge.action_select_image(dict(SELECT_PARAMS, expected_state=expected, replace=True))["status"] == "success"


def test_a_queue_row_that_changed_since_the_page_opened_refuses(stale_env):
    bridge, events, state = stale_env
    state["task"] = _queue_row("processing", "2026-10-03 10:05:00")
    expected = {"queue_status": "ready_for_review", "queue_updated_at": "2026-10-03 10:00:00", "approved_url": None}
    result = bridge.action_select_image(dict(SELECT_PARAMS, expected_state=expected))
    assert result["error_code"] == "state_changed"
    assert result["current"]["queue_status"] == "processing"
    assert result["current"]["queue_updated_at"] == "2026-10-03 10:05:00"
    assert _writes(events) == []

    state["task"] = _queue_row("ready_for_review", "2026-10-03 10:07:00")      # same status, newer row
    assert bridge.action_select_image(dict(SELECT_PARAMS, expected_state=expected))["error_code"] == "state_changed"

    # unchanged (the page's ISO form of the same time), or replace: approved; the worker's claim is taken
    import datetime
    state["task"] = _queue_row("ready_for_review", datetime.datetime(2026, 10, 3, 10, 0, 0))
    iso = dict(expected, queue_updated_at="2026-10-03T10:00:00")
    assert bridge.action_select_image(dict(SELECT_PARAMS, expected_state=iso))["status"] == "success"
    assert state["fenced"] == [(4, SELECT_PARAMS["sku_key"])]
    state["task"] = _queue_row("processing", "2026-10-03 10:05:00")
    assert bridge.action_select_image(dict(SELECT_PARAMS, expected_state=expected, replace="true"))["status"] == "success"


def test_the_expected_state_may_arrive_as_json_text(stale_env):
    """The upload form posts fields as text."""
    import json as _json
    bridge, events, state = stale_env
    state["approval"] = _approved(state, 3600)
    expected = _json.dumps({"queue_status": None, "queue_updated_at": None, "approved_url": ""})
    assert bridge.action_select_image(dict(SELECT_PARAMS, expected_state=expected))["error_code"] == "already_approved"


@pytest.mark.parametrize("seconds_ago, same_image, refused", [
    (30, False, True),           # another approval seconds ago: an old client may not replace it
    (30, True, False),           # the same image approved again (a retried request)
    (600, False, False),         # older than two minutes: the old behaviour
])
def test_an_old_client_never_replaces_a_fresh_approval(stale_env, seconds_ago, same_image, refused):
    bridge, events, state = stale_env
    state["approval"] = _approved(state, seconds_ago, **({"original_url": V2_RESULT["url"]} if same_image else {}))
    result = bridge.action_select_image(dict(SELECT_PARAMS))
    assert (result.get("error_code") == "already_approved") is refused
    assert (result["status"] == "success") is (not refused)
    assert bridge.action_select_image(dict(SELECT_PARAMS, replace=True))["status"] == "success"


def test_an_approval_made_during_processing_is_not_overwritten(stale_env, monkeypatch):
    """The re-check under the publish lock: another reviewer approved while this image was processed."""
    import image_processor
    bridge, events, state = stale_env
    original = image_processor.process_product_image_result

    def slow(*a, **k):
        state["approval"] = _approved(state, 1)
        return original(*a, **k)

    monkeypatch.setattr(image_processor, "process_product_image_result", slow)
    expected = {"queue_status": None, "queue_updated_at": None, "approved_url": None}
    result = bridge.action_select_image(dict(SELECT_PARAMS, expected_state=expected))
    assert result["error_code"] == "already_approved"
    assert not any(e[0] in ("link", "metadata_write", "resolution") for e in events)
    assert state["fenced"] == []


def test_a_manual_upload_follows_the_same_rule(stale_env, tmp_path):
    bridge, events, state = stale_env
    state["approval"] = _approved(state, 10)
    upload = tmp_path / "manual.png"
    _canvas(upload, (300, 300))
    params = {k: SELECT_PARAMS[k] for k in ("row_number", "product_name", "brand", "barcode", "sku_key")}
    params["file_path"] = str(upload)
    assert bridge.action_upload_manual_image(dict(params))["error_code"] == "already_approved"
    expected = {"queue_status": None, "queue_updated_at": None, "approved_url": None}
    assert bridge.action_upload_manual_image(dict(params, expected_state=expected))["error_code"] == "already_approved"
    assert _writes(events) == []
    assert bridge.action_upload_manual_image(dict(params, expected_state=expected, replace=True))["status"] == "success"


def test_a_publish_lock_held_elsewhere_writes_nothing(stale_env, monkeypatch):
    import contextlib
    import local_cache_db
    bridge, events, state = stale_env

    @contextlib.contextmanager
    def busy(sku_key, timeout=None):
        yield "busy"

    monkeypatch.setattr(local_cache_db, "sku_publish_lock", busy)
    result = bridge.action_select_image(dict(SELECT_PARAMS))
    assert result["status"] == "failed" and result["error_code"] == "busy"
    assert not any(e[0] in ("link", "resolution") for e in events)


# ---------------------------------------------------------------------------
# C3: what happened to the sheet write, read after the outbox's final flush
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("answer, sheet", [
    ({4: "written"}, "written"),
    ({4: "SYNCED", 9: "synced"}, "written"),
    ({4: "written", 9: "pending"}, "pending"),
    ({4: "FAILED"}, "pending"),
    ({4: "pending", 9: "conflict"}, "conflict"),
    ({4: "DEAD"}, "conflict"),
    ([{"row_number": 4, "outcome": "written"}], "written"),
    ("conflict", "conflict"),
    ({4: "something new"}, "unknown"),
    ({}, "unknown"),
])
def test_the_sheet_outcome_follows_the_final_flush(select_env, monkeypatch, answer, sheet):
    import google_sheets
    import local_cache_db
    bridge, events, state = select_env
    monkeypatch.setattr(local_cache_db, "get_tasks_by_sku", lambda key: [
        {"row_number": 9, "sku_key": key, "barcode": "6281007000024", "product_name": "Fresh Milk Full Fat 1L",
         "brand": "Almarai", "payload_json": "{}"}])
    monkeypatch.setattr(google_sheets, "stop_async_queue", lambda *a, **k: events.append(("flushed",)))
    monkeypatch.setattr(google_sheets, "outbox_outcomes",
                        lambda rows: events.append(("outcomes", list(rows))) or answer, raising=False)
    result = bridge.action_select_image(dict(SELECT_PARAMS))
    assert result["status"] == "success" and result["sheet"] == sheet
    kinds = [e[0] for e in events]
    assert kinds.index("flushed") < kinds.index("outcomes")         # read after the final flush
    assert ("outcomes", [4, 9]) in events


def test_the_sheet_outcome_reads_only_this_approvals_link_write_in_the_outbox_records(select_env, monkeypatch):
    # google_sheets.outbox_outcomes returns every queued write of the rows: an old CONFLICT of row 4 from an earlier
    # approval, and metadata writes, must not make this approval's (SYNCED) write look failed
    import google_sheets
    import local_cache_db
    bridge, events, state = select_env
    monkeypatch.setattr(local_cache_db, "get_tasks_by_sku", lambda key: [])
    records = [
        {"id": 3, "row": 4, "queued_row": 4, "column_key": "link", "status": "CONFLICT"},     # weeks ago
        {"id": 40, "row": 4, "queued_row": 4, "column_key": "meta:category_l1_en", "status": "DEAD"},
        {"id": 41, "row": 4, "queued_row": 4, "column_key": "link", "status": "SYNCED"},      # this approval
    ]
    monkeypatch.setattr(google_sheets, "outbox_outcomes", lambda rows: list(records), raising=False)
    assert bridge.action_select_image(dict(SELECT_PARAMS))["sheet"] == "written"
    records.append({"id": 42, "row": 4, "queued_row": 4, "column_key": "link", "status": "SUPERSEDED"})
    assert bridge.action_select_image(dict(SELECT_PARAMS, replace=True))["sheet"] == "conflict"


def test_the_sheet_outcome_is_unknown_without_the_outbox_api(select_env, monkeypatch, tmp_path):
    import google_sheets
    bridge, events, state = select_env
    if hasattr(google_sheets, "outbox_outcomes"):
        monkeypatch.setattr(google_sheets, "outbox_outcomes", None)
    assert bridge.action_select_image(dict(SELECT_PARAMS))["sheet"] == "unknown"

    def broken(rows):
        raise RuntimeError("outbox unreadable")

    monkeypatch.setattr(google_sheets, "outbox_outcomes", broken, raising=False)
    upload = tmp_path / "manual.png"
    _canvas(upload, (300, 300))
    params = {k: SELECT_PARAMS[k] for k in ("row_number", "product_name", "brand", "barcode", "sku_key")}
    result = bridge.action_upload_manual_image(dict(params, file_path=str(upload)))
    assert result["status"] == "success" and result["sheet"] == "unknown"


def test_the_upload_reports_the_sheet_outcome(select_env, monkeypatch, tmp_path):
    import google_sheets
    bridge, events, state = select_env
    monkeypatch.setattr(google_sheets, "outbox_outcomes", lambda rows: {r: "conflict" for r in rows}, raising=False)
    upload = tmp_path / "manual.png"
    _canvas(upload, (300, 300))
    params = {k: SELECT_PARAMS[k] for k in ("row_number", "product_name", "brand", "barcode", "sku_key")}
    assert bridge.action_upload_manual_image(dict(params, file_path=str(upload)))["sheet"] == "conflict"


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
