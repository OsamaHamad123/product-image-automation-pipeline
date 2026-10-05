"""«تجاوز عزل الخلفية»: publishing without background removal when the owner chose it (WP-NOBG, Fix 1), offline.

The Health page's «فحص النشر» showed PhotoRoom's credit had run out (photoroom_402): every approval failed and nothing
reached the sheet. Choosing «بدون عزل الخلفية» in Settings did not help, because a canvas the method 'none' returns
is not isolated and approvals refused it as «الخلفية لم تُعزل» (the worker wrote needs_review:).

Now the owner's own choice is not a failure: with the Settings method 'none' (processing_profile.skips_background)
and a canvas the method 'none' returned, main.publish_image publishes it clean on every path (the reviewer's
approval, a manual upload, the worker's auto-publish and the legacy sequential mode), with bg_skipped=True so the
approval response and the run report say «انتشرت بدون عزل الخلفية». A real isolation failure under any other method
(photoroom_402, or an unisolated canvas) is refused / needs_review exactly as before.
"""

import pytest

from test_cli_bridge_contract import SELECT_PARAMS, V2_RESULT, _canvas, bridge, select_env  # noqa: F401 - fixtures

pytestmark = pytest.mark.usefixtures("offline")

CLOUD_LINK = "https://res.cloudinary.com/demo/image/upload/q_auto,f_auto/products/dairy/abc.png"


@pytest.fixture
def method(monkeypatch):
    """Set the background-removal method the Settings page saved (config.BG_REMOVAL_METHOD)."""
    import config

    def set_method(name):
        monkeypatch.setattr(config, "BG_REMOVAL_METHOD", name, raising=False)

    set_method("photoroom")
    return set_method


def _links(events):
    return [e[3] for e in events if e[0] == "link"]


# ---------------------------------------------------------------------------
# processing_profile
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name, skips", [("none", True), ("NONE", True), ("off", True), ("no", True),
                                         ("photoroom", False), ("remove_bg_api", False), ("grabcut", False),
                                         ("rembg", False), ("", False)])
def test_the_profile_says_when_the_owner_turned_background_removal_off(name, skips):
    import processing_profile

    profile = processing_profile.ProcessingProfile(canvas=800, enhance=False, bg_method=name)
    assert profile.skips_background is skips


# ---------------------------------------------------------------------------
# The reviewer's approval and a manual upload (unclean='refuse')
# ---------------------------------------------------------------------------

def test_an_approval_with_background_removal_off_publishes_clean_and_says_so(select_env, method):
    bridge, events, state = select_env
    method("none")
    state["isolated"] = False                       # the method 'none' never claims isolation (provider 'none')
    result = bridge.action_select_image(dict(SELECT_PARAMS))
    assert result["status"] == "success" and result["bg_skipped"] is True
    assert result["sheet_value"] == CLOUD_LINK and not result["sheet_value"].startswith("needs_review:")
    assert "warning" not in result and "warnings" not in result      # the owner's choice is not a warning
    assert _links(events) == [CLOUD_LINK]
    _, args, kwargs = next(e for e in events if e[0] == "resolution")
    assert kwargs["verification_status"] == "human_approved"            # a real approval, the row is done


def test_a_manual_upload_with_background_removal_off_publishes_clean(select_env, method, tmp_path):
    bridge, events, state = select_env
    method("none")
    state["isolated"] = False
    upload = tmp_path / "manual.png"
    _canvas(upload, (300, 300))
    params = {k: SELECT_PARAMS[k] for k in ("row_number", "product_name", "brand", "barcode", "sku_key")}
    result = bridge.action_upload_manual_image(dict(params, file_path=str(upload)))
    assert result["status"] == "success" and result["bg_skipped"] is True and _links(events) == [CLOUD_LINK]


def test_an_isolated_approval_never_says_it_skipped_the_background(select_env, method):
    bridge, events, state = select_env
    result = bridge.action_select_image(dict(SELECT_PARAMS))
    assert result["status"] == "success" and "bg_skipped" not in result


def test_an_unisolated_canvas_under_photoroom_is_still_refused(select_env, method):
    # a canvas whose background was not removed while the owner asked for PhotoRoom is a failure, not a skip
    bridge, events, state = select_env
    state["isolated"] = False
    result = bridge.action_select_image(dict(SELECT_PARAMS))
    assert (result["status"], result["error_code"]) == ("failed", "background_failed")
    assert not any(e[0] in ("upload", "link", "resolution") for e in events)


@pytest.mark.parametrize("code", ["photoroom_402", "photoroom_401", "removebg_429"])
def test_a_provider_failure_under_photoroom_still_fails_with_its_code(select_env, method, monkeypatch, code):
    import image_processor

    bridge, events, state = select_env
    monkeypatch.setattr(image_processor, "process_product_image_result",
                        lambda *a, **k: image_processor.ProcessResult(None, False, "photoroom", code))
    result = bridge.action_select_image(dict(SELECT_PARAMS))
    assert result == {"status": "failed", "error": code, "isolated": False}
    assert not any(e[0] in ("upload", "link", "resolution") for e in events)


def test_none_with_quality_flags_from_another_method_is_not_a_skip(select_env, method, monkeypatch, tmp_path):
    # only the method 'none' itself (provider 'none') is the owner's choice: a PhotoRoom canvas with flags is not
    import image_processor

    bridge, events, state = select_env
    method("none")
    monkeypatch.setattr(image_processor, "process_product_image_result",
                        lambda *a, **k: image_processor.ProcessResult(_canvas(tmp_path / "c.png"), False, "photoroom",
                                                                      None, 800, 800, quality_flags=["edge_clipped"]))
    result = bridge.action_select_image(dict(SELECT_PARAMS))
    assert (result["status"], result["error_code"]) == ("failed", "background_failed")


# ---------------------------------------------------------------------------
# main.publish_image directly: the worker (unclean='review') and the sequential mode
# ---------------------------------------------------------------------------

@pytest.fixture
def publish(monkeypatch, tmp_path):
    import cloudinary_storage
    import google_sheets
    import image_processor
    import local_cache_db
    import main
    import processing_profile

    sheet = []
    state = {"provider": "none", "isolated": False, "error": None}

    def processing(*a, **k):
        if state["error"]:
            return image_processor.ProcessResult(None, False, state["provider"], state["error"])
        return image_processor.ProcessResult(_canvas(tmp_path / "canvas.png"), state["isolated"], state["provider"],
                                             None, 800, 800)

    monkeypatch.setattr(image_processor, "process_product_image_result", processing)
    monkeypatch.setattr(image_processor, "extract_metadata_from_image", lambda *a, **k: {})
    monkeypatch.setattr(cloudinary_storage, "upload_product_image_to_cloudinary", lambda *a, **k: CLOUD_LINK)
    monkeypatch.setattr(google_sheets, "update_image_link", lambda *a, **k: sheet.append(a[3]) or True)
    monkeypatch.setattr(local_cache_db, "find_image_owners", lambda *a, **k: [])

    def run(bg_method, **kwargs):
        profile = processing_profile.ProcessingProfile(canvas=800, enhance=False, bg_method=bg_method)
        return main.publish_image("https://x/milk.jpg", "Almarai Milk 1L", "Almarai", 4, object(), 3,
                                  profile=profile, **kwargs)

    return run, sheet, state


@pytest.mark.parametrize("unclean", ["review", "refuse"])
def test_publish_image_with_method_none_publishes_clean_and_records_bg_skipped(publish, unclean):
    run, sheet, state = publish
    res = run("none", unclean=unclean)
    assert res["status"] == "published" and res["bg_skipped"] is True and res["isolated"] is False
    assert res["sheet_value"] == CLOUD_LINK and sheet == [CLOUD_LINK]


def test_publish_image_under_photoroom_keeps_needs_review_for_the_worker(publish):
    run, sheet, state = publish
    res = run("photoroom", unclean="review")
    assert res["status"] == "needs_review" and res["bg_skipped"] is False
    assert sheet == ["needs_review:" + CLOUD_LINK]


def test_publish_image_under_photoroom_refuses_the_approval(publish):
    run, sheet, state = publish
    res = run("photoroom", unclean="refuse")
    assert (res["status"], res["error"]) == ("quality_refused", "background_failed") and sheet == []


def test_publish_image_with_a_provider_failure_fails_whatever_the_method(publish):
    run, sheet, state = publish
    state.update(error="photoroom_402", provider="photoroom")
    for name in ("photoroom", "none"):
        res = run(name, unclean="refuse")
        assert (res["status"], res["error"]) == ("failed", "photoroom_402") and sheet == []


def test_review_flags_still_apply_to_a_skipped_background(publish):
    # force_review (the sequential mode's non-AUTO_PUBLISH pick) still writes needs_review: the skip is not a review
    run, sheet, state = publish
    res = run("none", unclean="review", force_review=True)
    assert res["status"] == "needs_review" and sheet == ["needs_review:" + CLOUD_LINK]


# ---------------------------------------------------------------------------
# The worker's auto-publish and the run report
# ---------------------------------------------------------------------------

def test_the_worker_auto_publishes_without_removal_and_counts_it(select_env, method, monkeypatch):
    import local_cache_db
    import main

    bridge, events, state = select_env
    method("none")
    state["isolated"] = False
    monkeypatch.setattr(local_cache_db, "get_cached_product", lambda **k: None)
    monkeypatch.setattr(local_cache_db, "find_image_owners", lambda *a, **k: [])
    monkeypatch.setattr(local_cache_db, "delete_product_failure", lambda *a, **k: True)
    main.bg_skipped_count(reset=True)
    task = {"id": 7, "row_number": 4, "product_name": SELECT_PARAMS["product_name"], "brand": "Almarai",
            "barcode": SELECT_PARAMS["barcode"], "payload_json": "{}", "sku_key": SELECT_PARAMS["sku_key"]}
    assert main.auto_approve_product(task, {"url": V2_RESULT["url"]}, object(), 3,
                                     sku_key=SELECT_PARAMS["sku_key"]) == "published"
    assert _links(events) == [CLOUD_LINK]
    _, args, kwargs = next(e for e in events if e[0] == "resolution")
    assert kwargs["verification_status"] == "auto_verified"
    assert main.bg_skipped_count() == 1 and main.bg_skipped_count(reset=True) == 1 and main.bg_skipped_count() == 0

    # PhotoRoom chosen and the background not removed: needs review, nothing counted
    method("photoroom")
    events.clear()
    assert main.auto_approve_product(task, {"url": V2_RESULT["url"]}, object(), 3,
                                     sku_key=SELECT_PARAMS["sku_key"]) == "needs_review"
    assert main.bg_skipped_count() == 0


def test_the_run_report_says_how_many_were_published_without_removal():
    import run_report

    class DB:
        @staticmethod
        def run_outcome_counts(**k):
            return {"enqueued": 5, "searched": 5, "auto_published": 3, "ready_for_review": 2}

        @staticmethod
        def db_available():
            return True

    report = run_report.build_report("nightly", [{"stop_reason": "provider_down", "run_id": "r1", "bg_skipped": 2},
                                                 {"stop_reason": None, "run_id": "r2", "bg_skipped": 1}],
                                     1000.0, 1600.0, db=DB, sheets=None)
    assert report["bg_skipped"] == 3
    assert "انتشر بدون عزل الخلفية 3 (عزل الخلفية متوقف بالإعدادات)" in run_report.telegram_text(report)
    quiet = run_report.build_report("manual", [{"stop_reason": None, "run_id": "r3"}], 1000.0, 1600.0, db=DB)
    assert quiet["bg_skipped"] == 0 and "عزل الخلفية" not in run_report.telegram_text(quiet)
