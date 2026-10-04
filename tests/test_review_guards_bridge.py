"""The review guards on the bridge side (wp/p4-review-fix), on the real MariaDB test database (skips when MariaDB is
down) with the sheet, the processing and the upload as recorders (tests/test_publish_fix.py fixtures).

Each test failed before its change:

* #2  «replace» skipped the comparison with expected_state: a reviewer who confirmed replacing B overwrote C;
* #3  a sheet row moved after the queue was built: the bridge read the queue row at the request's row number (another
      product or none), so every approval was refused with a false «state changed»;
* #12 another product's approval under a shared barcode key was «this product's approval» (refused, offered for
      replacement);
* #18 a phash / sha256 sent with the approval skipped the pHash half of the rejected-image check and chose the bytes
      published from the candidate store;
* #14 (catalog_match) one store page found by two providers counted as two sources, and a page matching one of two
      variant axes the sheet states was «variant matched».
"""

import hashlib
import io
import types

import pytest

from test_publish_fix import (  # noqa: F401  (pytest fixtures)
    CLOUD, GTIN, LABAN, MILK, ROWS, _approve_params, _queue, _status, bridge, db, db_only,
)


def _page_view(db, row, **extra):
    import cli_bridge
    task = db.get_task_by_row(row)
    return dict({"queue_status": task["status"], "queue_updated_at": cli_bridge._ts(task["updated_at"]),
                 "approved_url": None, "queue_row": int(row)}, **extra)


# ---------------------------------------------------------------------------
# #2 replace compares what the reviewer confirmed
# ---------------------------------------------------------------------------

def test_replace_is_refused_when_the_approval_moved_again_after_the_confirmation(db, bridge):
    cli_bridge, env = bridge
    row = ROWS[0]
    sku = _queue(db, row, MILK)
    seen = _page_view(db, row)
    env["link"] = CLOUD + "b.png"
    assert cli_bridge.action_select_image(_approve_params(row, MILK, "https://shop/b.jpg", sku,
                                                          expected_state=seen))["status"] == "success"
    # reviewer A is refused (B was approved after their page opened) and confirms replacing B ...
    env["link"] = CLOUD + "a.png"
    refused = cli_bridge.action_select_image(_approve_params(row, MILK, "https://shop/a.jpg", sku, expected_state=seen))
    assert refused["error_code"] == "already_approved" and refused["current"]["approved_url"] == CLOUD + "b.png"
    confirmed_b = dict(refused["current"])
    # ... while reviewer C replaces B with C
    env["link"] = CLOUD + "c.png"
    assert cli_bridge.action_select_image(_approve_params(row, MILK, "https://shop/c.jpg", sku, replace=True,
                                                          expected_state=confirmed_b))["status"] == "success"
    # A's replace (confirmed for B) must not overwrite C: refused again, with what is approved now
    env["link"] = CLOUD + "a.png"
    again = cli_bridge.action_select_image(_approve_params(row, MILK, "https://shop/a.jpg", sku, replace=True,
                                                           expected_state=confirmed_b))
    assert again["status"] == "failed" and again["error_code"] == "already_approved"
    assert again["current"]["approved_url"] == CLOUD + "c.png"
    assert db.get_cached_product(sku_key=sku)["cloudinary_url"] == CLOUD + "c.png"
    # confirmed against what is approved now: replaced
    done = cli_bridge.action_select_image(_approve_params(row, MILK, "https://shop/a.jpg", sku, replace=True,
                                                          expected_state=again["current"]))
    assert done["status"] == "success" and db.get_cached_product(sku_key=sku)["cloudinary_url"] == CLOUD + "a.png"


# ---------------------------------------------------------------------------
# #3 a shifted sheet row is judged by its own queue row
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("send_queue_row", [True, False])
def test_an_approval_for_a_shifted_sheet_row_is_judged_by_the_products_queue_row(db, bridge, send_queue_row):
    cli_bridge, env = bridge
    queued, now = ROWS[0], ROWS[1]
    sku = _queue(db, queued, MILK)                               # queued at ROWS[0]; the product now sits at ROWS[1]
    seen = _page_view(db, queued)
    if not send_queue_row:
        seen.pop("queue_row")
    current = cli_bridge._current_state(sku, now, MILK["product_name"],
                                        cli_bridge._review_scope(dict(MILK, expected_state=seen), sku, now))[0]
    assert current["queue_status"] == "ready_for_review" and current["queue_row"] == queued
    result = cli_bridge.action_select_image(_approve_params(now, MILK, "https://shop/a.jpg", sku, expected_state=seen))
    assert result["status"] == "success", result
    assert _status(db, queued) == "completed" and result["current"]["queue_row"] == queued
    # a change of that queue row after the page opened is still seen
    sku2 = _queue(db, ROWS[2], LABAN)
    seen2 = _page_view(db, ROWS[2])
    db.update_task_status_by_row(ROWS[2], "processing", sku_key=sku2)
    refused = cli_bridge.action_select_image(_approve_params(ROWS[3], LABAN, "https://shop/l.jpg", sku2,
                                                             expected_state=seen2))
    assert refused["error_code"] == "state_changed" and refused["current"]["queue_status"] == "processing"


# ---------------------------------------------------------------------------
# #12 another product's approval under a shared barcode is not this product's
# ---------------------------------------------------------------------------

def test_another_products_approval_under_a_shared_barcode_key_is_not_this_products(db, bridge):
    cli_bridge, env = bridge
    milk_row, laban_row = ROWS[0], ROWS[1]
    sku = _queue(db, milk_row, MILK, GTIN)
    assert _queue(db, laban_row, LABAN, GTIN) == sku              # the same GTIN key
    laban_seen = _page_view(db, laban_row)
    env["link"] = CLOUD + "milk.png"
    assert cli_bridge.action_select_image(_approve_params(milk_row, MILK, "https://x/milk.jpg", sku, GTIN))["status"] \
        == "success"
    scope = cli_bridge._review_scope(dict(LABAN, barcode=GTIN), sku, laban_row)
    current = cli_bridge._current_state(sku, laban_row, LABAN["product_name"], scope)[0]
    assert current["approved_url"] is None and current["key_approval_of"] == MILK["product_name"]
    milk_current = cli_bridge._current_state(sku, milk_row, MILK["product_name"])[0]
    assert milk_current["approved_url"] == CLOUD + "milk.png" and milk_current["approved_for"] == MILK["product_name"]
    # the laban page never saw an approval of its own: nothing to refuse, nothing to offer for replacement
    env["link"] = CLOUD + "laban.png"
    result = cli_bridge.action_select_image(_approve_params(laban_row, LABAN, "https://x/laban.jpg", sku, GTIN,
                                                            expected_state=laban_seen))
    assert result["status"] == "success", result
    assert result["current"]["approved_for"] == LABAN["product_name"]
    assert [r for r, _, _ in env["sheet"]] == [milk_row, laban_row]   # each approval wrote its own row only


# ---------------------------------------------------------------------------
# #18 the rejected-image check and the published bytes never come from the request
# ---------------------------------------------------------------------------

def _png(kind):
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (64, 64), "white")
    draw = ImageDraw.Draw(img)
    if kind == "bottle":
        draw.rectangle([8, 8, 40, 56], fill="red")
    else:                                                       # another picture altogether
        for y in range(0, 64, 16):
            draw.rectangle([0, y, 63, y + 7], fill="blue")
        draw.ellipse([30, 20, 60, 50], fill="black")
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture
def store(monkeypatch, tmp_path):
    """A candidate store with two pictures, and the processing's candidate_sha256 recorded."""
    import config
    import image_processor
    from catalog_match import settings
    from catalog_match.fetch import phash_hex
    from PIL import Image

    folder = tmp_path / "candidates"
    folder.mkdir()
    monkeypatch.setattr(config, "CANDIDATE_STORE_DIR", str(folder), raising=False)
    monkeypatch.setattr(settings, "candidate_store_dir", lambda: str(folder))
    out = {"downloads": []}
    for name, kind in (("rejected", "bottle"), ("other", "stripes")):
        data = _png(kind)
        sha = hashlib.sha256(data).hexdigest()
        (folder / f"{sha}.png").write_bytes(data)
        with Image.open(io.BytesIO(data)) as img:
            out[name] = {"sha": sha, "phash": phash_hex(img.convert("RGB")), "bytes": data}
    out["shas"] = []
    original = image_processor.process_product_image_result

    def recording(*a, **k):
        out["shas"].append(k.get("candidate_sha256"))
        return original(*a, **k)

    monkeypatch.setattr(image_processor, "process_product_image_result", recording)
    out["download"] = None

    def download(url, page_url=None):
        out["downloads"].append(url)
        return (out["download"], None) if out["download"] else (None, "download_blocked")

    monkeypatch.setattr(image_processor, "_download_bytes", download)
    return out


def test_a_phash_or_sha256_sent_with_the_approval_never_skips_the_rejected_image_check(db, bridge, store):
    cli_bridge, env = bridge
    row = ROWS[0]
    sku = _queue(db, row, MILK)
    mirror = "https://mirror/pick.jpg"
    assert db.save_curation_candidates(row, MILK["product_name"], MILK["brand"], [
        {"url": mirror, "status": "preselected", "content_sha256": store["rejected"]["sha"]}], sku_key=sku)
    rejected = cli_bridge.action_reject_image(dict(_approve_params(row, MILK, "https://shop/pick.jpg", sku),
                                                   reason_code="WRONG_VARIANT", phash=store["rejected"]["phash"]))
    assert rejected["status"] == "success"
    seen = _page_view(db, row)
    # the same picture at another address, sent with another picture's phash and sha256: still the rejected image
    result = cli_bridge.action_select_image(_approve_params(row, MILK, mirror, sku, expected_state=seen,
                                                            phash=store["other"]["phash"],
                                                            candidate_sha256=store["other"]["sha"]))
    assert (result["status"], result.get("reason")) == ("failed", "image_rejected"), result
    # a sent phash near the rejected one is not a rejection either: the request never decides the check
    other = "https://shop/other.jpg"
    assert db.save_curation_candidates(row, MILK["product_name"], MILK["brand"], [
        {"url": other, "status": "eligible", "content_sha256": store["other"]["sha"]}], sku_key=sku)
    result = cli_bridge.action_select_image(_approve_params(row, MILK, other, sku, expected_state=seen,
                                                            phash=store["rejected"]["phash"]))
    assert result["status"] == "success", result
    assert [v for _, v, _ in env["sheet"]] == [env["link"]] and store["shas"] == [store["other"]["sha"]]
    assert store["downloads"] == []                             # no extra download for the check


def test_the_published_bytes_are_the_stored_candidates_never_a_sha256_from_the_request(db, bridge, store):
    cli_bridge, env = bridge
    row = ROWS[0]
    sku = _queue(db, row, MILK)
    stored = "https://shop/stored.jpg"
    assert db.save_curation_candidates(row, MILK["product_name"], MILK["brand"], [
        {"url": stored, "status": "preselected", "content_sha256": store["other"]["sha"]}], sku_key=sku)
    seen = _page_view(db, row)
    result = cli_bridge.action_select_image(_approve_params(row, MILK, stored, sku, expected_state=seen,
                                                            candidate_sha256=store["rejected"]["sha"]))
    assert result["status"] == "success"
    assert store["shas"] == [store["other"]["sha"]]              # the stored candidate's bytes, not the sent ones
    # a picture without a stored candidate (a live search on the review screen): no bytes chosen by the request
    result = cli_bridge.action_select_image(_approve_params(row, MILK, "https://shop/live.jpg", sku,
                                                            expected_state=result["current"],
                                                            candidate_sha256=store["rejected"]["sha"]))
    assert result["status"] == "success" and store["shas"][-1] is None


# ---------------------------------------------------------------------------
# #14 «why this image»: sources are sites, and a variant is matched only on every axis the sheet states
# ---------------------------------------------------------------------------

class _Stub:
    kind = "search"
    sanctioned = True
    fallback = False

    def __init__(self, name, cands):
        self.name = name
        self.cands = cands

    def search(self, query, hl, spec):
        from catalog_match.models import ProviderResult
        return ProviderResult(provider=self.name, status="ok", candidates=list(self.cands))


def test_one_store_page_found_by_two_providers_is_one_source(monkeypatch):
    import socket
    from catalog_match.identity import build_sku_spec
    from catalog_match.models import Candidate
    from catalog_match.retrieve import retrieve

    monkeypatch.setattr(socket.socket, "connect", lambda *a, **k: (_ for _ in ()).throw(OSError("blocked")))
    spec = build_sku_spec({"name": "Almarai Fresh Milk Full Fat 1L", "brand": "Almarai"})
    page = "https://www.carrefouruae.com/mafuae/en/almarai-fresh-milk-full-fat-1l/p/1"
    img = "https://cdn.mafrservices.com/milk.jpg"
    one = retrieve(spec, [_Stub("serper", [Candidate(image_url=img, page_url=page)]),
                          _Stub("cse", [Candidate(image_url=img, page_url=page)])], max_queries=1)
    assert one.pool[0].consensus_count == 1
    two = retrieve(spec, [_Stub("serper", [Candidate(image_url=img, page_url=page)]),
                          _Stub("cse", [Candidate(image_url=img, page_url="https://www.noon.com/uae-en/milk/p/2")])],
                   max_queries=1)
    assert two.pool[0].consensus_count == 2


def test_a_variant_is_matched_only_when_every_axis_the_sheet_states_matches():
    from catalog_match import facade
    from catalog_match.models import Candidate, RankedCandidate

    score = types.SimpleNamespace(matched={"variants": ["fat"]}, hard_reject=(), conflicts=(), tier=2,
                                  size_status="match", url_only_size_conflict=False)
    rc = RankedCandidate(candidate=Candidate(image_url="https://x/a.jpg"), score=score, status="eligible")
    two_axes = types.SimpleNamespace(variants={"fat": "low", "flavour": "strawberry"})
    one_axis = types.SimpleNamespace(variants={"fat": "low"})
    assert facade.evidence(rc, two_axes)["variant_status"] == "partial"
    assert facade.evidence(rc, one_axis)["variant_status"] == "match"
    assert facade.evidence(rc)["variant_status"] == "match"      # no sheet to compare with: as before


# ---------------------------------------------------------------------------
# #5 a canvas where nothing was removed (opaque_fill) is a background failure, never «published anyway»
# ---------------------------------------------------------------------------

def test_opaque_fill_is_never_published_anyway(db, monkeypatch, tmp_path):
    import cloudinary_storage
    import google_sheets
    import image_processor
    import main
    from PIL import Image

    assert "opaque_fill" not in main.PRESENTATION_FLAGS
    sheet = []

    def processing(*a, **k):
        out = tmp_path / "canvas.png"
        Image.new("RGB", (800, 800), "white").save(out)
        return image_processor.ProcessResult(str(out), False, "photoroom", None, 800, 800, quality_flags=["opaque_fill"])

    monkeypatch.setattr(image_processor, "process_product_image_result", processing)
    monkeypatch.setattr(image_processor, "extract_metadata_from_image", lambda *a, **k: {})
    monkeypatch.setattr(cloudinary_storage, "upload_product_image_to_cloudinary", lambda *a, **k: CLOUD + "x.png")
    monkeypatch.setattr(google_sheets, "update_image_link", lambda *a, **k: sheet.append(a[3]) or True)
    for anyway in (False, True):
        res = main.publish_image("https://x/milk.jpg", MILK["product_name"], MILK["brand"], ROWS[0], object(), 3,
                                 unclean="refuse", publish_anyway=anyway)
        assert (res["status"], res["error"], res["publish_anyway_allowed"]) == \
            ("quality_refused", "background_failed", False)
    assert sheet == []
