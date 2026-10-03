"""main.py worker wiring: payload -> search kwargs, retry policy, auto-publish gate, candidate persistence.

search_best_product_image, the queue writes and the rejections are replaced by recorders;
no network, keys or database are used.
"""

import json

import pytest

pytestmark = pytest.mark.usefixtures("offline")


@pytest.fixture
def wiring(offline, monkeypatch):
    import main
    import local_cache_db

    rec = {"status": [], "failures": [], "saved": [], "sleeps": [], "auto": []}
    monkeypatch.setattr(local_cache_db, "update_task_status",
                        lambda task_id, status, error_message=None, failure_code=None, trace=None, claim_id=None:
                        rec["status"].append({"id": task_id, "status": status, "error": error_message,
                                              "failure_code": failure_code, "trace": trace}) or True)
    monkeypatch.setattr(local_cache_db, "save_product_failure",
                        lambda *a, **k: rec["failures"].append(a) or True)
    monkeypatch.setattr(local_cache_db, "get_rejections",
                        lambda sku: (["https://www.noon.com/rejected-sibling.jpg"], ["00000000ffffffff"]))
    monkeypatch.setattr(local_cache_db, "save_curation_candidates",
                        lambda *a, **k: rec["saved"].append((a, k)) or True)
    monkeypatch.setattr(main, "auto_approve_product",
                        lambda task, best, ws, col, sku_key=None: rec["auto"].append(best) or "published")
    return main, local_cache_db, rec


def _task(**payload):
    return {"id": 41, "row_number": 17, "barcode": "", "product_name": "Laban Up Strawberry 180ml",
            "brand": "Al Rawabi", "search_query": "Laban Up Strawberry 180ml Al Rawabi",
            "sku_key": "sku-laban-up", "payload_json": json.dumps(payload, ensure_ascii=False)}


def _search_returning(results, calls):
    """Fake search that returns results[i] on call i; a result may be (best, outcome) or an exception."""

    def fake(query, name, brand, **kwargs):
        calls.append({"query": query, "name": name, "brand": brand, **kwargs})
        item = results[min(len(calls) - 1, len(results) - 1)]
        if isinstance(item, Exception):
            raise item
        best, outcome = item
        kwargs["trace"]["outcome"] = outcome
        return best

    return fake


def _best(decision, **extra):
    base = {"url": "https://www.carrefouruae.com/laban-up.jpg", "title": "Al Rawabi Laban Up Strawberry 180ml",
            "source": "serper", "decision": decision, "needs_review": decision != "AUTO_PUBLISH",
            "preselect": decision in ("AUTO_PUBLISH", "REVIEW_PRESELECTED"), "clip_score": None,
            "failure_code": None, "sku_key": "sku-laban-up",
            "candidates": [{"url": "https://www.carrefouruae.com/laban-up.jpg", "status": "preselected",
                            "reasons": ["T1"], "evidence": {"tier": "T1"}},
                           {"url": "https://www.noon.com/laban-up-mango.jpg", "status": "rejected",
                            "reasons": ["variant_conflict"], "evidence": {}}]}
    base.update(extra)
    return base


def test_payload_reaches_search(wiring, monkeypatch):
    main, _, rec = wiring
    import image_search
    calls = []
    monkeypatch.setattr(image_search, "search_best_product_image",
                        _search_returning([(_best("REVIEW_PRESELECTED"), {"decision": "REVIEW_PRESELECTED"})], calls))

    result = main.pre_cache_product_candidates(
        _task(name_ar="لبن أب", brand_ar="الروابي", category="Dairy", size="180 ml"), sleep=lambda s: None)

    assert result == "success"
    (kw,) = calls
    assert kw["product_name_ar"] == "لبن أب"
    assert kw["brand_ar"] == "الروابي"
    assert kw["category"] == "Dairy"
    assert kw["size_text"] == "180 ml"
    assert kw["exclude_urls"] == ["https://www.noon.com/rejected-sibling.jpg"]
    assert kw["exclude_phashes"] == ["00000000ffffffff"]
    assert kw["custom_query"] is None                 # the default "name brand" query is not a custom query


def test_custom_sheet_query_is_passed_as_custom_query(wiring, monkeypatch):
    main, _, rec = wiring
    import image_search
    calls = []
    monkeypatch.setattr(image_search, "search_best_product_image",
                        _search_returning([(_best("REVIEW_PRESELECTED"), {"decision": "REVIEW_PRESELECTED"})], calls))
    task = _task()
    task["search_query"] = "الروابي لبن أب فراولة"
    main.pre_cache_product_candidates(task, sleep=lambda s: None)
    assert calls[0]["custom_query"] == "الروابي لبن أب فراولة"


def test_no_retry_on_clean_not_found(wiring, monkeypatch):
    main, _, rec = wiring
    import image_search
    calls = []
    outcome = {"decision": "NOT_FOUND", "failure_code": "NO_RESULTS", "provider_health": [], "queries": ["q"],
               "sku_key": "sku-laban-up"}
    monkeypatch.setattr(image_search, "search_best_product_image", _search_returning([(None, outcome)], calls))

    result = main.pre_cache_product_candidates(_task(), sleep=lambda s: rec["sleeps"].append(s))

    assert result == "failed"
    assert len(calls) == 1 and rec["sleeps"] == []
    (final,) = rec["status"]
    assert final["status"] == "failed" and final["failure_code"] == "NO_RESULTS"
    assert final["trace"]["outcome"]["failure_code"] == "NO_RESULTS"     # trace_json is written
    assert len(rec["failures"]) == 1 and "NO_RESULTS" in rec["failures"][0][3]


def test_retry_on_provider_down(wiring, monkeypatch):
    main, _, rec = wiring
    import image_search
    calls = []
    outcome = {"decision": "PROVIDER_DOWN", "failure_code": "PROVIDER_DOWN",
               "provider_health": [{"provider": "serper", "status": "quota"}], "queries": [], "sku_key": "sku-laban-up"}
    monkeypatch.setattr(image_search, "search_best_product_image", _search_returning([(None, outcome)], calls))

    result = main.pre_cache_product_candidates(_task(), sleep=lambda s: rec["sleeps"].append(s))

    assert result == "provider_down"
    assert len(calls) == 3
    traces = [c["trace"] for c in calls]
    assert len({id(t) for t in traces}) == 3, "each attempt must get a fresh trace dict"
    assert rec["sleeps"] == [2.0, 4.0]
    (final,) = rec["status"]
    assert final["status"] == "pending" and final["failure_code"] == "PROVIDER_DOWN"
    assert rec["failures"] == [], "a provider outage is not a product failure"


def test_retry_on_exception_then_success(wiring, monkeypatch):
    main, _, rec = wiring
    import image_search
    calls = []
    monkeypatch.setattr(image_search, "search_best_product_image", _search_returning(
        [TimeoutError("read timed out"), (_best("REVIEW_UNSELECTED", preselect=False),
                                          {"decision": "REVIEW_UNSELECTED"})], calls))
    result = main.pre_cache_product_candidates(_task(), sleep=lambda s: rec["sleeps"].append(s))
    assert result == "success" and len(calls) == 2
    assert rec["status"][-1]["status"] == "ready_for_review"


def test_auto_approve_only_on_decision(wiring, monkeypatch):
    main, _, rec = wiring
    import image_search

    def run(best):
        calls = []
        monkeypatch.setattr(image_search, "search_best_product_image",
                            _search_returning([(best, {"decision": best.get("decision")})], calls))
        return main.pre_cache_product_candidates(_task(), worksheet=object(), link_column_index=5,
                                                 sleep=lambda s: None)

    # a perfect legacy score is irrelevant: only the decision counts
    run(_best("REVIEW_PRESELECTED", clip_score=1.0))
    assert rec["auto"] == []
    assert rec["status"][-1]["status"] == "ready_for_review"

    run(_best("AUTO_PUBLISH"))
    assert len(rec["auto"]) == 1
    assert rec["status"][-1]["status"] == "completed"

    # a cache hit is never auto-published, whatever its decision says
    run(_best("AUTO_PUBLISH", source="sqlite_cache"))
    assert len(rec["auto"]) == 1
    assert rec["status"][-1]["status"] == "ready_for_review"


def test_candidates_saved_with_status_and_sku(wiring, monkeypatch):
    main, _, rec = wiring
    import image_search
    monkeypatch.setattr(image_search, "search_best_product_image",
                        _search_returning([(_best("REVIEW_PRESELECTED"), {"decision": "REVIEW_PRESELECTED"})], []))
    main.pre_cache_product_candidates(_task(), sleep=lambda s: None)
    (args, kwargs), = rec["saved"]
    candidates = args[3]
    assert [c["status"] for c in candidates] == ["preselected", "rejected"]
    assert candidates[1]["reasons"] == ["variant_conflict"]
    assert kwargs["sku_key"] == "sku-laban-up" and kwargs["run_id"]


def test_candidate_save_failure_marks_failed(wiring, monkeypatch):
    main, local_cache_db, rec = wiring
    import image_search
    monkeypatch.setattr(image_search, "search_best_product_image",
                        _search_returning([(_best("REVIEW_PRESELECTED"), {"decision": "REVIEW_PRESELECTED"})], []))
    monkeypatch.setattr(local_cache_db, "save_curation_candidates", lambda *a, **k: False)

    result = main.pre_cache_product_candidates(_task(), sleep=lambda s: None)

    assert result == "failed"
    (final,) = rec["status"]
    assert final["status"] == "failed" and final["failure_code"] == "CANDIDATE_SAVE_FAILED"
    assert not any(s["status"] == "ready_for_review" for s in rec["status"])


def test_auto_approve_not_isolated_is_not_cached(offline, monkeypatch, tmp_path):
    """AUTO_PUBLISH whose background removal failed is written as needs_review: and never cached."""
    import main
    import local_cache_db
    import google_sheets
    import cloudinary_storage
    import image_processor
    from PIL import Image

    canvas = tmp_path / "canvas.png"
    Image.new("RGB", (800, 800), "white").save(canvas)
    monkeypatch.setattr(image_processor, "process_product_image_result",
                        lambda *a, **k: image_processor.ProcessResult(str(canvas), False, "none", None, 800, 800))
    monkeypatch.setattr(image_processor, "extract_metadata_from_image", lambda *a, **k: None)
    monkeypatch.setattr(cloudinary_storage, "upload_product_image_to_cloudinary", lambda *a, **k: "https://res/x.png")
    written, cached = [], []
    monkeypatch.setattr(google_sheets, "update_image_link", lambda ws, row, col, value, **k: written.append(value) or True)
    monkeypatch.setattr(local_cache_db, "save_product_resolution", lambda *a, **k: cached.append(k) or True)

    status = main.auto_approve_product(_task(), _best("AUTO_PUBLISH"), object(), 5, sku_key="sku-laban-up")

    assert status == "needs_review"
    assert written == ["needs_review:https://res/x.png"]
    assert cached == []


def test_auto_approve_writes_with_size_and_brand_identity(offline, monkeypatch, tmp_path):
    """Without a barcode, the sheet write carries the row's size and brand so a stale row number that
    now points at a same-name sibling (other size or brand) is refused at flush time."""
    import main
    import local_cache_db
    import google_sheets
    import cloudinary_storage
    import image_processor
    from PIL import Image

    canvas = tmp_path / "canvas.png"
    Image.new("RGB", (800, 800), "white").save(canvas)
    monkeypatch.setattr(image_processor, "process_product_image_result",
                        lambda *a, **k: image_processor.ProcessResult(str(canvas), True, "none", None, 800, 800))
    monkeypatch.setattr(image_processor, "extract_metadata_from_image", lambda *a, **k: {"description_en": "Milk"})
    monkeypatch.setattr(cloudinary_storage, "upload_product_image_to_cloudinary", lambda *a, **k: "https://res/x.png")
    links, metas = [], []
    monkeypatch.setattr(google_sheets, "update_image_link", lambda ws, row, col, value, **k: links.append(k) or True)
    monkeypatch.setattr(google_sheets, "update_product_metadata", lambda ws, row, md, **k: metas.append(k) or True)
    monkeypatch.setattr(local_cache_db, "save_product_resolution", lambda *a, **k: True)
    monkeypatch.setattr(local_cache_db, "delete_product_failure", lambda *a, **k: True)

    status = main.auto_approve_product(_task(size="180ml"), _best("AUTO_PUBLISH"), object(), 5, sku_key="sku-laban-up")

    assert status == "published"
    expected = {"barcode": "", "product_name": "Laban Up Strawberry 180ml", "size": "180ml", "brand": "Al Rawabi"}
    assert links == [expected] and metas == [expected]


def test_enqueue_validates_filter_before_touching_queue(offline, monkeypatch):
    import main
    import config
    import local_cache_db
    import google_sheets

    touched = []
    monkeypatch.setattr(main, "load_run_config", lambda: None)
    monkeypatch.setattr(config, "ROW_FILTER", "5-x")
    monkeypatch.setattr(local_cache_db, "clear_queue", lambda: touched.append("clear"))
    monkeypatch.setattr(local_cache_db, "add_to_queue", lambda *a, **k: touched.append("add"))
    monkeypatch.setattr(google_sheets, "get_sheets_client", lambda: touched.append("sheets") or object())
    with pytest.raises(SystemExit) as exc:
        main.run_enqueue_mode()
    assert exc.value.code == 1
    assert "add" not in touched and "clear" not in touched


def test_enqueue_payload_and_final_links(offline, monkeypatch):
    import main
    import config
    import local_cache_db
    import google_sheets

    products = [
        {"row_number": 2, "product_name": "Laban Up Strawberry 180ml", "brand": "Al Rawabi", "barcode": "",
         "product_name_ar": "لبن أب فراولة", "brand_ar": "الروابي", "category": "Dairy", "sub_category": "Laban",
         "origin": "UAE", "size": "180ml", "existing_image_link": "", "search_query": "Laban Up Strawberry 180ml Al Rawabi"},
        {"row_number": 3, "product_name": "Almarai Fresh Milk 1L", "brand": "Almarai", "barcode": "6281007000028",
         "existing_image_link": "https://res.cloudinary.com/final.png", "search_query": "x"},
    ]
    added = []
    monkeypatch.setattr(main, "load_run_config", lambda: None)
    monkeypatch.setattr(config, "ROW_FILTER", "")
    monkeypatch.setattr(config, "BRAND_FILTER", "")
    monkeypatch.setattr(config, "FORCE_OVERWRITE_IMAGES", False)
    monkeypatch.setattr(google_sheets, "clear_cache", lambda: None)
    monkeypatch.setattr(google_sheets, "get_sheets_client", lambda: object())
    monkeypatch.setattr(google_sheets, "open_worksheet", lambda c, n: object())
    monkeypatch.setattr(google_sheets, "get_products", lambda ws: (products, 9))
    monkeypatch.setattr(google_sheets, "get_brand_mappings", lambda *a: {})
    monkeypatch.setattr(local_cache_db, "clear_queue", lambda: pytest.fail("enqueue must not clear the queue"))
    monkeypatch.setattr(local_cache_db, "add_to_queue", lambda *a, **k: pytest.fail("enqueue writes in batches"))
    monkeypatch.setattr(local_cache_db, "add_many_to_queue",
                        lambda rows, reprocess=False, totals=None: added.append((list(rows), reprocess)) or totals)
    monkeypatch.setattr(local_cache_db, "resolution_snapshot", lambda: {"by_key": {}, "by_url": {}})
    monkeypatch.setattr(local_cache_db, "queue_snapshot", lambda: {})
    monkeypatch.setattr(local_cache_db, "get_queue_statistics", lambda: {})

    main.run_enqueue_mode()

    ((row,), reprocess), = added            # row 3 has a final link and is skipped
    assert row["row_number"] == 2
    assert json.loads(row["payload_json"]) == {"name_ar": "لبن أب فراولة", "brand_ar": "الروابي", "category": "Dairy",
                                               "sub_category": "Laban", "origin": "UAE", "size": "180ml"}
    assert row["sku_key"] and len(row["sku_key"]) == 16
    assert row["alt_sku_key"] == row["sku_key"]          # no valid barcode: the fingerprint is the key
    assert row["task_kind"] is None and row["review_only"] == 0
    assert reprocess is False


def test_auto_publish_never_overwrites_a_human_approval(wiring, monkeypatch):
    """A re-queued row whose SKU has a human-approved image goes to review, not AUTO_PUBLISH."""
    main, local_cache_db, rec = wiring
    import image_search
    monkeypatch.setattr(local_cache_db, "get_cached_product",
                        lambda **k: {"cloudinary_url": "https://res/human.png", "verification_status": "human_approved"}
                        if k.get("sku_key") == "sku-laban-up" else None)
    monkeypatch.setattr(image_search, "search_best_product_image",
                        _search_returning([(_best("AUTO_PUBLISH"), {"decision": "AUTO_PUBLISH"})], []))
    main.pre_cache_product_candidates(_task(), worksheet=object(), link_column_index=5, sleep=lambda s: None)
    assert rec["auto"] == []
    assert rec["status"][-1]["status"] == "ready_for_review"


def test_lost_claim_discards_worker_result(wiring, monkeypatch):
    """A reviewer approved the row while the worker searched: no candidate replacement, no status write."""
    main, local_cache_db, rec = wiring
    import image_search
    monkeypatch.setattr(local_cache_db, "is_claim_held", lambda task_id, claim_id: claim_id != "w1#lost")
    monkeypatch.setattr(image_search, "search_best_product_image",
                        _search_returning([(_best("AUTO_PUBLISH"), {"decision": "AUTO_PUBLISH"})], []))
    task = dict(_task(), worker_id="w1#lost")
    main.pre_cache_product_candidates(task, worksheet=object(), link_column_index=5, sleep=lambda s: None)
    assert rec["auto"] == [] and rec["saved"] == [] and rec["status"] == []


def test_llm_localisation_never_feeds_search_identity(wiring, monkeypatch):
    """QueryRefiner output is written back to the sheet only; search gets the sheet's own Arabic fields."""
    main, _, rec = wiring
    import config
    import google_sheets
    import image_search
    import query_refiner
    written = []
    monkeypatch.setattr(query_refiner.QueryRefiner, "refine_product_metadata",
                        staticmethod(lambda *a, **k: {"cleaned_title_ar": "حليب المراعي", "canonical_brand_ar": "المراعي"}))
    monkeypatch.setattr(google_sheets, "update_product_localization", lambda *a, **k: written.append(a) or True)
    monkeypatch.setattr(config, "log_and_fail", lambda *a, **k: None, raising=False)
    calls = []
    monkeypatch.setattr(image_search, "search_best_product_image",
                        _search_returning([(None, {"decision": "NOT_FOUND", "failure_code": "NO_RESULTS"})], calls))
    prod = {"row_number": 9, "product_name": "Fresh Laban 1L", "brand": "Unmapped Dairy", "barcode": ""}
    main.process_single_product(prod, object(), 5)
    assert written and written[0][2] == "حليب المراعي"                 # localisation still written back
    assert calls and calls[0]["product_name_ar"] == "" and calls[0]["brand_ar"] == ""
