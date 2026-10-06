"""Integration: the real v2 pipeline driven through the production callers.

The golden-set doubles (fixture providers, fetcher, cassette verifier) are injected
into catalog_match.pipeline.find_product_image; everything above it is production
code: image_search.search_best_product_image -> facade -> cli_bridge.action_search
(the dashboard's JSON contract) and main.pre_cache_product_candidates /
main.process_single_product (the worker). Each caller result is compared with the
decision and pick the pipeline itself produced for the same SKU (runners.run_v2),
so a caller that drops an identity field, misreads the facade dict or publishes a
non-pick fails here. No network, no API keys, no database (DB calls are stubbed).
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

import pytest

import cli_bridge  # noqa: E402
import config  # noqa: E402
import image_search  # noqa: E402
import local_cache_db  # noqa: E402
import main  # noqa: E402
import runners  # noqa: E402
from catalog_match import pipeline as cm_pipeline  # noqa: E402

pytestmark = pytest.mark.eval

AUTO = "uae-001-almarai-full-fat-milk-1l"
PRESELECTED = "uae-019-almarai-fresh-milk-full-fat-2-85l"
ARABIC_ONLY = "uae-041-arabic-only-al-foah-khalas-dates-1kg"
UNSELECTED = "uae-006-al-rawabi-fresh-milk-full-fat-2l"
NOT_FOUND = "uae-052-no-results-healthy-farms-eggs-30"
CASES = (AUTO, PRESELECTED, ARABIC_ONLY, UNSELECTED, NOT_FOUND)


@pytest.fixture(scope="module")
def mappings():
    return runners.load_mappings()


@pytest.fixture(scope="module")
def golden_by_id(golden):
    return {s["id"]: s for s in golden["skus"]}


@pytest.fixture(scope="module")
def reference(golden_by_id, cassette, mappings):
    """What the pipeline itself decides for each case (the eval runner, no callers involved)."""
    out = {}
    with runners.network_blocked():
        for sku_id in CASES:
            out[sku_id] = runners.run_v2(golden_by_id[sku_id], cassette, mappings=mappings)
    return out


@pytest.fixture
def wired(monkeypatch, golden_by_id, cassette, mappings):
    """Route the production search entry point to the fixture doubles of one SKU."""
    real_find = cm_pipeline.find_product_image
    state: Dict[str, Any] = {}

    def use(sku_id):
        sku = golden_by_id[sku_id]
        models = runners._v2_modules()[2]
        state["doubles"] = dict(
            providers=runners.build_providers(models, sku),
            fetcher=runners.FixtureFetcher(models, sku),
            verifier=runners.CassetteVerifier(models, sku, cassette, "normal"),
        )
        return sku

    def find(spec, **kwargs):
        state["spec"] = spec
        state["kwargs"] = kwargs
        return real_find(spec, **state["doubles"], **kwargs)

    monkeypatch.setattr(cm_pipeline, "find_product_image", find)
    monkeypatch.setattr(image_search, "_load_brand_mappings_for_search", lambda: mappings)
    monkeypatch.setattr(local_cache_db, "get_cached_product", lambda *a, **k: None)
    monkeypatch.setattr(local_cache_db, "get_rejections", lambda sku_key: ([], []))
    monkeypatch.setattr(local_cache_db, "get_task_by_row", lambda row: None)
    state["use"] = use
    return state


def _params(sku, row_number=7):
    row = runners.sku_row(sku)
    return {"product_name": row["name"], "brand": row["brand"], "barcode": row["barcode"],
            "product_name_ar": row["name_ar"], "brand_ar": row["brand_ar"], "category": row["category"],
            "size": row["size"], "row_number": row_number}


def _task(sku, row_number=7):
    row = runners.sku_row(sku)
    payload = {"name_ar": row["name_ar"], "brand_ar": row["brand_ar"], "category": row["category"],
               "size": row["size"]}
    return {"id": 99, "row_number": row_number, "product_name": row["name"], "brand": row["brand"],
            "barcode": row["barcode"], "search_query": "", "payload_json": json.dumps(payload)}


@pytest.mark.parametrize("sku_id", CASES)
def test_cli_bridge_search_matches_pipeline(sku_id, wired, reference, mappings):
    sku = wired["use"](sku_id)
    ref = reference[sku_id]
    with runners._v2_settings(True), runners.network_blocked() as attempts:
        resp = cli_bridge.action_search(_params(sku), brand_mappings=mappings)
    assert runners.outbound_attempts(attempts) == []

    # the stdout contract must be plain JSON (the PHP bridge json_decodes it)
    decoded = json.loads(json.dumps(resp))
    assert decoded["decision"] == ref.decision
    expected_status = {"AUTO_PUBLISH": "success", "REVIEW_PRESELECTED": "review",
                       "REVIEW_UNSELECTED": "review", "NOT_FOUND": "not_found"}[ref.decision]
    assert decoded["status"] == expected_status
    assert decoded["sku_key"] == wired["spec"].sku_key

    selected = decoded["selected_image"]
    preselected = [c for c in decoded["candidates"] if c["status"] == "preselected"]
    if ref.decision in ("AUTO_PUBLISH", "REVIEW_PRESELECTED"):
        assert selected is not None and selected["url"] == ref.chosen_url
        assert [c["url"] for c in preselected] == [ref.chosen_url]
    else:
        assert selected is None
        assert preselected == []
    if ref.decision == "NOT_FOUND":
        assert decoded["candidates"] == [] or all(c["status"] != "preselected" for c in decoded["candidates"])
        return

    assert decoded["candidates"], "a review decision must give the reviewer candidates"
    for c in decoded["candidates"]:
        ev = c["evidence"]
        # the keys the dashboard's evidence chips read
        assert ev["size"] in ("match", "conflict", "ambiguous", "unknown")
        # 'partial': the page matched some, not all, of the variant axes the sheet states (never «match»)
        assert ev["variant_status"] in ("match", "partial", "conflict", "unknown")
        assert isinstance(ev["variants"], list)
        assert c["domain"]
        if c["vlm"] is not None:
            assert {"decision", "brand_text", "variant_text", "size_text", "pack_count", "view"} <= set(c["vlm"])


def test_cli_bridge_passes_arabic_identity_fields(wired, reference, mappings, golden_by_id):
    """The Arabic-only row keeps its Arabic name and brand on the way to the spec."""
    sku = wired["use"](ARABIC_ONLY)
    with runners._v2_settings(True), runners.network_blocked():
        cli_bridge.action_search(_params(sku), brand_mappings=mappings)
    spec = wired["spec"]
    assert spec.name_ar == golden_by_id[ARABIC_ONLY]["name_ar"]
    assert spec.raw_name == runners.sku_row(sku)["name"]


@pytest.mark.parametrize("sku_id", CASES)
def test_worker_precache_routes_by_decision(sku_id, wired, reference, mappings, monkeypatch):
    sku = wired["use"](sku_id)
    ref = reference[sku_id]
    calls: Dict[str, List[Any]] = {"status": [], "saved": [], "auto": [], "failure": []}
    monkeypatch.setattr(local_cache_db, "update_task_status",
                        lambda task_id, status, *a, **k: calls["status"].append((status, k.get("failure_code"))))
    monkeypatch.setattr(local_cache_db, "save_curation_candidates",
                        lambda row, name, brand, cands, best_url=None, **k: calls["saved"].append(
                            (list(cands), best_url)) or True)
    monkeypatch.setattr(local_cache_db, "save_product_failure",
                        lambda *a, **k: calls["failure"].append(a))

    def fake_auto(task, best, worksheet, link_column_index, sku_key=None):
        calls["auto"].append(best)
        return "published"

    monkeypatch.setattr(main, "auto_approve_product", fake_auto)

    with runners._v2_settings(True), runners.network_blocked() as attempts:
        result = main.pre_cache_product_candidates(_task(sku), worksheet=object(), link_column_index=3,
                                                   brand_mappings=mappings, sleep=lambda s: None)
    assert runners.outbound_attempts(attempts) == []

    if ref.decision == "AUTO_PUBLISH":
        assert result == "success"
        assert [b["url"] for b in calls["auto"]] == [ref.chosen_url]
        assert calls["saved"] == []
        assert calls["status"][-1][0] == "completed"
    elif ref.decision == "NOT_FOUND":
        assert result == "failed"
        assert calls["auto"] == [] and calls["saved"] == []
        assert calls["status"][-1][0] == "failed"
        assert calls["status"][-1][1] == ref.failure_code
    else:
        assert result == "success"
        assert calls["auto"] == [], "a review decision must never be auto-published"
        (cands, best_url), = calls["saved"]
        pre = [c["url"] for c in cands if c.get("status") == "preselected"]
        if ref.decision == "REVIEW_PRESELECTED":
            assert pre == [ref.chosen_url] and best_url == ref.chosen_url
        else:
            assert pre == [] and best_url is None
        assert calls["status"][-1][0] == "ready_for_review"


def test_legacy_sequential_mode_never_publishes_unselected(wired, mappings, monkeypatch):
    """REVIEW_UNSELECTED has url=None: nothing may be processed, published or written to the sheet."""
    sku = wired["use"](UNSELECTED)
    task = _task(sku)
    prod = {"row_number": task["row_number"], "product_name": task["product_name"], "brand": task["brand"],
            "barcode": task["barcode"], **json.loads(task["payload_json"])}
    prod["product_name_ar"] = prod.pop("name_ar")
    touched: Dict[str, List[Any]] = {"publish": [], "sheet": [], "saved": []}
    monkeypatch.setattr(config, "CURATION_MODE", False, raising=False)
    monkeypatch.setattr(config, "FORCE_OVERWRITE_IMAGES", False, raising=False)
    monkeypatch.setattr(main, "publish_image", lambda *a, **k: touched["publish"].append(a) or {"status": "failed"})
    monkeypatch.setattr(main.google_sheets, "update_image_link", lambda *a, **k: touched["sheet"].append(a) or True)
    monkeypatch.setattr(local_cache_db, "save_curation_candidates",
                        lambda row, name, brand, cands, best_url=None, **k: touched["saved"].append(cands) or True)

    with runners._v2_settings(True), runners.network_blocked():
        result = main.process_single_product(prod, worksheet=object(), link_column_index=3,
                                             brand_mappings=mappings)
    assert result == "success"
    assert touched["publish"] == [] and touched["sheet"] == []
    assert len(touched["saved"]) == 1 and touched["saved"][0]
    assert all(c.get("status") != "preselected" for c in touched["saved"][0])


@pytest.mark.parametrize("sku_id", (AUTO, ARABIC_ONLY, UNSELECTED))
def test_get_products_sku_key_joins_search_candidates(sku_id, wired, mappings, monkeypatch):
    """The dashboard joins candidates to products by sku_key: get_products must emit the key the search used."""
    sku = wired["use"](sku_id)
    row = runners.sku_row(sku)
    product = {"row_number": 7, "product_name": row["name"], "product_name_ar": row["name_ar"],
               "brand": row["brand"], "brand_ar": row["brand_ar"], "barcode": row["barcode"],
               "category": row["category"], "size": row["size"]}
    monkeypatch.setattr(cli_bridge, "_open_sheet", lambda: object())
    monkeypatch.setattr(cli_bridge.google_sheets, "get_products", lambda ws: ([dict(product)], 3))
    monkeypatch.setattr(cli_bridge, "_load_brand_mappings", lambda: mappings)
    monkeypatch.setattr(local_cache_db, "get_product_failures", lambda: {})
    listed = cli_bridge.action_get_products({})
    assert listed["status"] == "success"

    with runners._v2_settings(True), runners.network_blocked():
        resp = cli_bridge.action_search(_params(sku), brand_mappings=mappings)
    assert listed["products"][0]["sku_key"] == resp["sku_key"] == wired["spec"].sku_key
