"""Delivery links the app reads from the sheet: WebP with alpha and a width cap; old q_auto,f_auto links are the same image.

f_auto answered native clients (okhttp `Accept: image/*`, iOS CFNetwork, Dart) with a JPEG without alpha, a white box
in dark mode. New links are c_limit,w_1200,f_webp,q_auto; the code still recognises the old form everywhere it compares
a sheet link with a stored approval, and every write of a stored old link goes out in the new form.
"""

import pytest

import delivery_urls as du

OLD = "https://res.cloudinary.com/laqta/image/upload/q_auto,f_auto/v1700000000/products/dairy/0123abcd"
NEW = "https://res.cloudinary.com/laqta/image/upload/c_limit,w_1200,f_webp,q_auto/v1700000000/products/dairy/0123abcd"
WHITE = "https://res.cloudinary.com/laqta/image/upload/b_white,c_limit,w_1200,f_jpg,q_auto/v1700000000/products/dairy/0123abcd"


def test_the_new_transformation_is_webp_capped_and_never_f_auto():
    assert du.DELIVERY_TRANSFORMATION == "c_limit,w_1200,f_webp,q_auto"
    assert du.WHITE_TRANSFORMATION == "b_white,c_limit,w_1200,f_jpg,q_auto"
    assert "f_auto" not in du.DELIVERY_TRANSFORMATION + du.WHITE_TRANSFORMATION


def test_old_links_migrate_to_the_new_form_of_the_same_asset():
    assert du.migrated_delivery_url(OLD) == NEW
    # without a version too (links written before the version was in the URL)
    assert du.migrated_delivery_url(OLD.replace("v1700000000/", "")) == NEW.replace("v1700000000/", "")


@pytest.mark.parametrize("value", [
    NEW,                                                           # already new
    WHITE,                                                         # the white version is not a sheet link
    "needs_review:" + OLD,                                         # a legacy pending cell: approval or reject cleans it
    " " + OLD,                                                     # anything but the exact link stays untouched
    "https://www.lulu.com/q_auto,f_auto/image/upload/x.jpg",       # a store image
    "https://evil.example/laqta/image/upload/q_auto,f_auto/v1/x",  # another host with the same path
    "https://res.cloudinary.com/laqta/image/upload/w_300/v1/x",    # someone else's transformation
    "https://res.cloudinary.com/laqta/image/upload/q_auto,f_auto/",  # no asset
    "", None,
])
def test_only_our_old_delivery_links_are_rewritten(value):
    assert du.migrated_delivery_url(value) is None


def test_old_and_new_links_are_the_same_image():
    assert du.same_delivery_asset(OLD, NEW)
    assert du.same_delivery_asset("needs_review:" + OLD, NEW)
    assert not du.same_delivery_asset(OLD, OLD.replace("0123abcd", "ffff0000"))
    assert not du.same_delivery_asset(OLD, OLD.replace("v1700000000", "v1700000001"))
    assert not du.same_delivery_asset("", "")
    assert du.canonical_delivery_url(OLD) == du.canonical_delivery_url(NEW) == NEW
    assert du.canonical_delivery_url("https://www.lulu.com/x.jpg ") == "https://www.lulu.com/x.jpg"
    assert du.delivery_variants(NEW) == [NEW, OLD]
    assert du.delivery_variants(OLD) == [OLD, NEW]
    assert du.delivery_variants("https://src/x.jpg") == ["https://src/x.jpg"]
    assert du.cloud_name(OLD) == "laqta" and du.cloud_name("https://src/x.jpg") == ""


def test_the_white_version_comes_from_old_and_new_links():
    assert du.white_version_url(NEW) == du.white_version_url(OLD) == WHITE
    assert du.white_version_url(WHITE) is None


def test_url_norm_treats_old_and_new_links_as_one_image():
    import local_cache_db

    assert local_cache_db.url_norm(OLD) == local_cache_db.url_norm(NEW)
    assert local_cache_db.url_norm("https://www.Lulu.com/a/b.jpg?x=1") == "lulu.com/a/b.jpg"


def test_a_migrated_sheet_link_still_shows_the_stored_approval():
    """After the sheet holds the new link, the page sends it as expected_state.approved_url while the approval in the
    database keeps the old one: it is the same approval (no already_approved refusal) and a reject clears the cell."""
    import cli_bridge

    approval = {"cloudinary_url": OLD, "original_url": "https://www.lulu.com/milk.jpg"}
    assert cli_bridge._shows(NEW, approval)
    assert cli_bridge._shows("needs_review:" + NEW, approval)
    assert not cli_bridge._shows(NEW.replace("0123abcd", "ffff0000"), approval)
    assert cli_bridge._cell_holds(NEW, {OLD})
    assert cli_bridge._cell_holds("needs_review:" + OLD, NEW)
    assert not cli_bridge._cell_holds("", {OLD})
    assert cli_bridge._approval_matches(approval, NEW, None)


def test_the_enqueue_sees_the_new_form_write_of_an_old_approval():
    import main

    records = [{"value": NEW, "status": "SYNCED"}]
    assert main._link_write_state(records, OLD) == "SYNCED"
    assert main._link_write_state([{"value": "needs_review:" + NEW, "status": "SYNCED"}], OLD) is None
    assert main._link_write_state([{"value": "https://res/other.png", "status": "PENDING"}], OLD) is None


def test_a_stored_old_link_is_written_to_the_sheet_in_the_new_form(monkeypatch):
    """relink and the duplicate rows write the approval's stored link: the app must get the WebP one."""
    import google_sheets as gs

    sent = []

    class Outbox:
        def append_update(self, row, col, value, **k):
            sent.append((row, value))
            return len(sent)

    monkeypatch.setattr(gs, "_redis_write_behind", lambda *a, **k: False)
    monkeypatch.setattr(gs, "_queue", Outbox())
    monkeypatch.setattr(gs, "_worker", object())
    assert gs.update_image_link(None, 7, 4, OLD, barcode="6281007000000")
    assert gs.update_image_link(None, 8, 4, "https://www.lulu.com/x.jpg")
    assert gs.update_image_link(None, 9, 4, "")
    assert sent == [(7, NEW), (8, "https://www.lulu.com/x.jpg"), (9, "")]


def test_publish_finds_an_owner_stored_with_the_old_link(monkeypatch):
    """find_image_owners asks the database for the old and the new form of a freshly uploaded link."""
    import local_cache_db

    seen = {}

    class Cursor:
        def execute(self, sql, params):
            seen["params"] = params

        def fetchall(self):
            return [{"sku_key": "other", "product_name": "Other", "brand": "B", "cloudinary_url": OLD,
                     "perceptual_hash": None, "verification_status": "human_approved", "color_signature": None}]

    class Conn:
        def cursor(self):
            return Cursor()

        def close(self):
            pass

    monkeypatch.setattr(local_cache_db, "get_db_connection", lambda: Conn())
    owners = local_cache_db.find_image_owners(NEW, None, sku_key="mine", product_name="Mine")
    assert set(seen["params"]) == {NEW, OLD}
    assert [(o["sku_key"], o["match"]) for o in owners] == [("other", "url")]
