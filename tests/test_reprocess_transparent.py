"""«أعد معالجتها شفافة»: recut.py and scripts/reprocess_transparent.py.

Fakes only: a recorded worksheet (test_sheet_write_integrity.Sheet), the local MariaDB (resolved_products, recut_log
and the sheet outbox), an in-memory "Cloudinary" (fetch and upload) and a fake background removal that counts its paid
calls like image_processor does. No real sheet, Cloudinary or PhotoRoom is ever reached.
"""

import hashlib
import importlib.util
import io
import json
import os
import tempfile

import numpy as np
import pytest
from PIL import Image, ImageDraw

_SPEC = importlib.util.spec_from_file_location(
    "reprocess_transparent",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "reprocess_transparent.py"))
rpt = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(rpt)

from test_sheet_write_integrity import (HEADERS, JUICE, LABAN, MILK, WATER, Sheet, gs, local_only,  # noqa: E402,F401
                                        outbox, statuses)

BASE = "https://res.cloudinary.com/laqta/image/upload/"
OLD = "q_auto,f_auto/"
NEW = "c_limit,w_1200,f_webp,q_auto/"
MILK_URL = BASE + OLD + "v1700000001/products/dairy/aaaa1111"           # an old white master, old link form
LABAN_URL = BASE + NEW + "v1700000002/products/dairy/bbbb2222"          # an old white master, migrated link
JUICE_URL = BASE + NEW + "v1700000003/products/juice/cccc3333"          # already transparent
WATER_URL = BASE + NEW + "v1700000004/products/water/dddd4444"          # the owner pasted another link in its row


def new_form(url):
    return url.replace("/" + OLD, "/" + NEW)


# ---------------------------------------------------------------------------
# pictures
# ---------------------------------------------------------------------------

def bottle(colour=(200, 40, 40), label=(30, 60, 200), size=(260, 520)):
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    w, h = size
    d.rounded_rectangle((10, 60, w - 10, h - 5), radius=30, fill=colour + (255,))
    d.rectangle((w // 2 - 30, 5, w // 2 + 30, 70), fill=(150, 150, 150, 255))
    d.rectangle((25, h // 3, w - 25, h // 3 + 140), fill=label + (255,))
    return img


def canvas(product, transparent, side=800, fill=0.88):
    scale = side * fill / max(product.size)
    p = product.resize((max(1, int(product.width * scale)), max(1, int(product.height * scale))))
    out = Image.new("RGBA", (side, side), (0, 0, 0, 0) if transparent else (255, 255, 255, 255))
    out.paste(p, ((side - p.width) // 2, (side - p.height) // 2), p)
    return out if transparent else out.convert("RGB")


def cut_white(img):
    """What a background removal makes of a white canvas: the white goes, the product stays."""
    arr = np.asarray(img.convert("RGBA")).copy()
    arr[(arr[..., :3] >= 245).all(-1), 3] = 0
    return Image.fromarray(arr, "RGBA")


def trim(img):
    box = img.getchannel("A").getbbox()
    return img.crop(box) if box else img


def png(img, fmt="PNG"):
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    return buf.getvalue()


class Cloud:
    """The masters as Cloudinary would deliver them, and the uploads of new versions."""

    def __init__(self):
        self.files = {}
        self.uploads = []
        self.fail_upload = False

    def put(self, url, img):
        from delivery_urls import canonical_delivery_url
        import recut
        self.files[canonical_delivery_url(url)] = png(img, "WEBP" if img.mode == "RGBA" else "PNG")
        self.files[recut.master_source_url(url)] = png(img)

    def fetch(self, url):
        return self.files.get(url)

    def upload(self, path, name, brand, folder=None, **kw):
        import cloudinary_storage
        data = open(path, "rb").read()
        if self.fail_upload:
            return cloudinary_storage.UploadResult(None, error="upload_failed", cause="ServerError")
        md5 = hashlib.md5(data).hexdigest()
        url = f"{BASE}{NEW}v1800000{len(self.uploads):03d}/{folder}/{md5}"
        self.uploads.append({"path": path, "folder": folder, "url": url, "name": name})
        self.put(url, Image.open(io.BytesIO(data)))
        return cloudinary_storage.UploadResult(url, public_id=f"{folder}/{md5}", content_md5=md5)


class Isolation:
    """process_product_image_result stand-in: a transparent canvas of the picture the source shows, one paid call."""

    def __init__(self, cloud, sources):
        self.cloud, self.sources, self.calls = cloud, sources, []
        self.error = None
        self.flags = []

    def __call__(self, src, name, brand, **kw):
        import image_processor
        self.calls.append({"src": src, "sha": kw.get("candidate_sha256"), "bg_method": kw.get("bg_method"),
                           "background": kw.get("background")})
        picture = self.sources.get(src)
        if picture is None and src in self.cloud.files:
            picture = cut_white(Image.open(io.BytesIO(self.cloud.files[src])))
        if picture is None:
            return image_processor.ProcessResult(None, False, kw.get("bg_method"), "download_http_404")
        image_processor._count_paid_call("photoroom")
        if self.error:
            return image_processor.ProcessResult(None, False, "photoroom", self.error)
        made = canvas(trim(picture), True)
        path = os.path.join(tempfile.mkdtemp(prefix="imgproc_"), "c.png")
        made.save(path)
        return image_processor.ProcessResult(path, not self.flags, "photoroom", None, made.width, made.height,
                                             quality_flags=list(self.flags),
                                             finish={"background": "transparent", "halo": 0.01, "dark_rim": 0.0})


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db(mariadb_or_skip, monkeypatch):
    import config
    ldb = mariadb_or_skip
    conn = ldb.get_db_connection()
    try:
        cur = conn.cursor()
        for table in ("resolved_products", "recut_log", "approved_embeddings"):
            cur.execute(f"DELETE FROM {table}")
        conn.commit()
    finally:
        conn.close()
    monkeypatch.setattr(config, "BG_REMOVAL_METHOD", "photoroom", raising=False)
    monkeypatch.setattr(config, "OUTPUT_BACKGROUND", "transparent", raising=False)
    return ldb


def approve(ldb, barcode, name, brand, url, background=None, status="human_approved", original=None):
    assert ldb.save_product_resolution(barcode, name, brand, original or f"https://shop.ae/{name}.jpg", url,
                                       verification_status=status, approved_by="human", sku_key=f"sku-{name}",
                                       master_background=background)
    return next(r for r in ldb.published_masters() if r["product_name"] == name)


def the_sheet():
    return Sheet([HEADERS,
                  [MILK, "Almarai Fresh Milk", "Almarai", "1L", "KSA", MILK_URL],                  # row 2
                  [LABAN, "Almarai Laban", "Almarai", "1L", "KSA", LABAN_URL],                     # row 3
                  [JUICE, "Al Rawabi Juice", "Al Rawabi", "500ml", "UAE", JUICE_URL],              # row 4
                  [WATER, "Mai Dubai Water", "Mai Dubai", "500ml", "UAE", "https://shop.ae/hand.jpg"],  # row 5
                  ["", "Almarai Fresh Milk", "Almarai", "1L", "KSA", "needs_review:" + MILK_URL]])  # row 6


@pytest.fixture
def world(db, gs, outbox, monkeypatch, tmp_path):
    cloud = Cloud()
    milk, laban = bottle((200, 40, 40)), bottle((40, 160, 60), label=(220, 200, 30))
    cloud.put(MILK_URL, canvas(milk, False))
    cloud.put(LABAN_URL, canvas(laban, False))
    cloud.put(JUICE_URL, canvas(bottle((240, 140, 20)), True))
    approve(db, MILK, "Almarai Fresh Milk", "Almarai", MILK_URL)                       # unknown: probed white
    approve(db, LABAN, "Almarai Laban", "Almarai", LABAN_URL, background="white")
    approve(db, JUICE, "Al Rawabi Juice", "Al Rawabi", JUICE_URL)                      # unknown: probed transparent
    approve(db, WATER, "Mai Dubai Water", "Mai Dubai", WATER_URL, background="white")  # not in the sheet any more
    iso = Isolation(cloud, {"https://shop.ae/Almarai Fresh Milk.jpg": milk, "https://shop.ae/Almarai Laban.jpg": laban})
    import recut
    monkeypatch.setattr(rpt, "run_active", lambda: False)
    monkeypatch.setattr(recut, "fetch_bytes", cloud.fetch)
    state = tmp_path / "state.json"

    def run(argv, sheet=None):
        sheet = sheet or the_sheet()
        code = rpt.main(argv, worksheet=sheet, probe_fn=lambda url: recut.probe(url, cloud.fetch),
                        recut_fn=lambda row, method=None: recut.recut(row, method, process=iso, fetch=cloud.fetch),
                        publish_fn=lambda *a, **k: recut.publish(*a, upload=cloud.upload, **k), state_path=str(state))
        return code, sheet

    return {"db": db, "cloud": cloud, "iso": iso, "run": run, "state": state, "gs": gs}


def read_state(world):
    return json.loads(world["state"].read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# what is recorded at publish time
# ---------------------------------------------------------------------------

def test_publish_results_record_the_master_background_and_the_cut():
    import recut

    res = {"status": "published", "width": 1024, "height": 1024, "provider": "photoroom", "quality_flags": [],
           "quality_notes": ["upscaled"], "finish": {"background": "transparent", "halo": 0.02, "uncertainty": 0.1,
                                                     "secret": "x"}, "profile": {"background": "transparent"}}
    facts = recut.master_facts(res)
    assert facts["master_background"] == "transparent"
    assert facts["cutout"]["notes"] == ["upscaled"] and facts["cutout"]["finish"] == {
        "background": "transparent", "halo": 0.02, "uncertainty": 0.1}
    assert recut.background_of({"bg_skipped": True, "finish": {"background": "opaque"}}) == "opaque"
    assert recut.background_of({"finish": {}, "profile": {"background": "white"}}) == "white"
    assert recut.master_facts({"status": "failed"}) == {}


def test_save_product_resolution_keeps_the_master_facts(db):
    row = approve(db, MILK, "Milk", "Almarai", MILK_URL, background="transparent")
    assert row["master_background"] == "transparent"
    assert db.save_product_resolution(MILK, "Milk", "Almarai", "https://x/y.jpg", LABAN_URL,
                                      verification_status="human_approved", sku_key="sku-Milk",
                                      cutout={"provider": "photoroom"})
    again = db.published_masters()[0]
    assert again["cloudinary_url"] == LABAN_URL and again["master_background"] is None   # a new picture: unknown
    assert json.loads(again["cutout_json"]) == {"provider": "photoroom"}


def test_main_master_facts_never_breaks_an_approval(monkeypatch):
    import main
    import recut

    monkeypatch.setattr(recut, "master_facts", lambda res: 1 / 0)
    assert main.master_facts({"width": 1}) == {}


# ---------------------------------------------------------------------------
# the outbox: replace only the value we wrote
# ---------------------------------------------------------------------------

def test_a_replace_write_lands_only_on_the_cell_that_still_holds_the_old_link(gs, outbox):
    ws = the_sheet()
    new_milk = BASE + NEW + "v1800000001/products/dairy/eeee5555"
    gs.queue_link_writes([
        {"row_number": 2, "value": new_milk, "barcode": MILK, "product_name": "Almarai Fresh Milk", "size": "1L",
         "brand": "Almarai", "replace": MILK_URL},
        {"row_number": 3, "value": new_milk, "barcode": LABAN, "product_name": "Almarai Laban", "size": "1L",
         "brand": "Almarai", "replace": LABAN_URL}])
    ws.set(3, 6, "https://shop.ae/the-owner-pasted-this.jpg")          # changed by hand after the plan read it
    gs.flush_outbox(ws)
    assert ws.value(2, "Drive Image Link") == new_milk
    assert ws.value(3, "Drive Image Link") == "https://shop.ae/the-owner-pasted-this.jpg"
    outcome = {o["row"]: o for o in gs.outbox_outcomes(limit=10)}
    assert outcome[2]["status"] == "SYNCED" and outcome[3]["status"] == "CONFLICT"
    assert "cell_changed" in outcome[3]["error"]
    assert [r["status"] for r in gs.reported] == ["CONFLICT"]


def test_the_old_and_new_delivery_form_of_the_same_asset_both_count_as_the_old_link(gs, outbox):
    ws = the_sheet()
    ws.set(2, 6, new_form(MILK_URL))                 # migrate_delivery_urls rewrote it meanwhile
    gs.queue_link_writes([{"row_number": 2, "value": JUICE_URL, "barcode": MILK, "product_name": "Almarai Fresh Milk",
                           "size": "1L", "brand": "Almarai", "replace": MILK_URL}])
    gs.flush_outbox(ws)
    assert ws.value(2, "Drive Image Link") == JUICE_URL


def test_a_replace_write_already_in_the_cell_is_synced_without_a_send(gs, outbox):
    ws = the_sheet()
    gs.queue_link_writes([{"row_number": 4, "value": JUICE_URL, "barcode": JUICE, "product_name": "Al Rawabi Juice",
                           "size": "500ml", "brand": "Al Rawabi", "replace": LABAN_URL}])
    gs.flush_outbox(ws)
    assert ws.sends == 0 and list(statuses(gs).values()) == ["SYNCED"]


def test_a_moved_row_is_followed_when_its_cell_still_holds_the_old_link(gs, outbox):
    ws = the_sheet()
    new_milk = BASE + NEW + "v1800000001/products/dairy/eeee5555"
    gs.queue_link_writes([{"row_number": 2, "value": new_milk, "barcode": MILK, "product_name": "Almarai Fresh Milk",
                           "size": "1L", "brand": "Almarai", "replace": MILK_URL}])
    ws.insert_row(2, ["", "New Product", "X", "", "UAE", ""])        # milk moves to row 3
    gs.flush_outbox(ws)
    assert ws.value(3, "Drive Image Link") == new_milk and ws.value(2, "Drive Image Link") == ""


def test_a_plain_link_write_is_unchanged_by_the_guard(gs, outbox):
    ws = the_sheet()
    ws.set(2, 6, "https://shop.ae/hand.jpg")
    gs.queue_link_writes([{"row_number": 2, "value": JUICE_URL, "barcode": MILK, "product_name": "Almarai Fresh Milk",
                           "size": "1L", "brand": "Almarai"}])
    gs.flush_outbox(ws)
    assert ws.value(2, "Drive Image Link") == JUICE_URL


# ---------------------------------------------------------------------------
# recut: the source and the same-picture check
# ---------------------------------------------------------------------------

def test_same_picture_ignores_framing_but_not_another_picture():
    import recut

    milk = bottle((200, 40, 40))
    assert recut.same_picture(canvas(milk, False, 800, 0.88), canvas(milk, True, 1300, 0.80))
    assert not recut.same_picture(canvas(milk, False), canvas(bottle((40, 160, 60), label=(220, 200, 30)), True))
    assert not recut.same_picture(canvas(milk, False), canvas(bottle(size=(520, 260)), True))     # another shape


def test_recut_prefers_the_verified_source_then_falls_back_to_the_master(db, monkeypatch):
    import recut

    cloud = Cloud()
    milk = bottle()
    cloud.put(MILK_URL, canvas(milk, False))
    row = approve(db, MILK, "Milk", "Almarai", MILK_URL)
    iso = Isolation(cloud, {"https://shop.ae/Milk.jpg": milk})
    got = recut.recut(row, process=iso, fetch=cloud.fetch, sha_lookup=lambda sku, url: "ab" * 32)
    assert got.clean and got.source == "candidate" and got.paid_calls == 1
    assert iso.calls[0]["sha"] == "ab" * 32 and iso.calls[0]["background"] == "transparent"
    assert iso.calls[0]["bg_method"] == "photoroom"

    # the store lost the file and the URL now serves other bytes: never that picture, the master instead
    iso.calls.clear()
    monkeypatch.setattr(iso, "sources", {})
    got = recut.recut(row, process=iso, fetch=cloud.fetch, sha_lookup=lambda sku, url: "ab" * 32)
    assert got.source == "master" and [c["src"] for c in iso.calls] == [
        "https://shop.ae/Milk.jpg", recut.master_source_url(MILK_URL)]
    assert got.tried == [["candidate", "download_http_404"], ["master", "ok"]]


def test_an_unverified_source_that_now_shows_another_picture_is_never_used(db):
    import recut

    cloud = Cloud()
    cloud.put(MILK_URL, canvas(bottle(), False))
    row = approve(db, MILK, "Milk", "Almarai", MILK_URL)
    other = bottle((40, 160, 60), label=(220, 200, 30))
    iso = Isolation(cloud, {"https://shop.ae/Milk.jpg": other})
    got = recut.recut(row, process=iso, fetch=cloud.fetch, sha_lookup=lambda sku, url: None)
    assert got.source == "master" and got.tried[0] == ["original", "source_differs"]
    assert got.paid_calls == 2


def test_recut_returns_the_isolation_error_without_trying_the_master(db):
    import recut

    cloud = Cloud()
    cloud.put(MILK_URL, canvas(bottle(), False))
    row = approve(db, MILK, "Milk", "Almarai", MILK_URL)
    iso = Isolation(cloud, {"https://shop.ae/Milk.jpg": bottle()})
    iso.error = "photoroom_402"
    got = recut.recut(row, process=iso, fetch=cloud.fetch, sha_lookup=lambda sku, url: None)
    assert got.path is None and got.error == "photoroom_402" and len(iso.calls) == 1


def test_recut_refuses_while_background_removal_is_off(db, monkeypatch):
    import config
    import recut

    monkeypatch.setattr(config, "BG_REMOVAL_METHOD", "none")
    got = recut.recut({"cloudinary_url": MILK_URL}, process=lambda *a, **k: pytest.fail("processed"))
    assert got.error == "bg_removal_off"


def test_probe_reads_the_master_and_measures_it():
    import recut

    cloud = Cloud()
    cloud.put(MILK_URL, canvas(bottle(), False))
    cloud.put(JUICE_URL, canvas(bottle(), True))
    white, clear = recut.probe(MILK_URL, cloud.fetch), recut.probe(JUICE_URL, cloud.fetch)
    assert white["background"] == "white" and clear["background"] == "transparent"
    assert "halo" in clear and clear["canvas"] == [800, 800] and clear["product_px"] > 600
    assert recut.probe(WATER_URL, cloud.fetch) is None
    assert recut.asset_folder(MILK_URL) == "products/dairy"


# ---------------------------------------------------------------------------
# the script
# ---------------------------------------------------------------------------

def test_the_dry_run_lists_and_prices_and_changes_nothing(world, capsys, monkeypatch):
    import recut
    monkeypatch.setattr(world["gs"], "queue_link_writes", lambda items: pytest.fail("the dry run queued a write"))
    monkeypatch.setattr(world["cloud"], "upload", lambda *a, **k: pytest.fail("the dry run uploaded"))
    monkeypatch.setattr(world["db"], "set_master_facts", lambda *a, **k: pytest.fail("the dry run saved"))
    monkeypatch.setattr(recut, "publish", lambda *a, **k: pytest.fail("the dry run published"))
    before = {r["id"]: (r["cloudinary_url"], r["master_background"]) for r in world["db"].published_masters()}
    code, sheet = world["run"]([])
    out = capsys.readouterr().out
    assert code == 0 and "Dry run: nothing was changed" in out
    assert "white to redo: 2" in out and "transparent already: 1" in out and "not in the sheet" in out
    assert "about 2 photoroom calls x $0.020 = $0.04" in out
    assert world["iso"].calls == [] and world["cloud"].uploads == []
    assert {r["id"]: (r["cloudinary_url"], r["master_background"]) for r in world["db"].published_masters()} == before
    assert sheet.value(2, "Drive Image Link") == MILK_URL and not world["state"].exists()


def test_apply_redoes_the_white_masters_through_the_outbox(world):
    code, sheet = world["run"](["--apply", "--max", "10", "--max-usd", "1"])
    assert code == 0
    uploads = world["cloud"].uploads
    assert len(uploads) == 2 and {u["folder"] for u in uploads} == {"products/dairy"}
    by_name = {r["product_name"]: r for r in world["db"].published_masters()}
    milk, laban = by_name["Almarai Fresh Milk"], by_name["Almarai Laban"]
    assert milk["master_background"] == laban["master_background"] == "transparent"
    assert milk["verification_status"] == "human_approved"                         # the approval is unchanged
    assert sheet.value(2, "Drive Image Link") == milk["cloudinary_url"] != MILK_URL  # a new versioned asset
    assert "/v1800000" in milk["cloudinary_url"]
    assert sheet.value(3, "Drive Image Link") == laban["cloudinary_url"]
    assert sheet.value(4, "Drive Image Link") == JUICE_URL                          # transparent already
    assert sheet.value(6, "Drive Image Link") == "needs_review:" + MILK_URL         # a pending review cell stays
    assert by_name["Al Rawabi Juice"]["master_background"] == "transparent"         # the probe was remembered
    assert by_name["Mai Dubai Water"]["cloudinary_url"] == WATER_URL                # not in the sheet: untouched
    log = world["db"].recut_entries()
    assert sorted((e["status"], e["product_name"]) for e in log) == [("done", "Almarai Fresh Milk"),
                                                                    ("done", "Almarai Laban")]
    assert all(e["origin"] == "reprocess" and e["rows"] and e["outbox"] for e in log)
    state = read_state(world)
    assert (state["state"], state["done"], state["calls"], state["spent_usd"]) == ("done", 2, 2, 0.04)

    # a second run finds nothing left to redo
    code, _ = world["run"](["--apply"], sheet)
    assert code == 0 and read_state(world)["planned"] == 0 and len(world["cloud"].uploads) == 2


def test_apply_never_touches_a_row_changed_by_hand_after_the_plan(world, monkeypatch):
    import recut
    sheet = the_sheet()
    real = recut.publish

    def publish_then_edit(rows, *a, **k):
        if rows[0]["product_name"] == "Almarai Laban":
            sheet.set(3, 6, "https://shop.ae/owner-choice.jpg")
        return real(rows, *a, upload=world["cloud"].upload, **k)

    code = rpt.main(["--apply"], worksheet=sheet, probe_fn=lambda url: recut.probe(url, world["cloud"].fetch),
                    recut_fn=lambda row, method=None: recut.recut(row, method, process=world["iso"],
                                                                  fetch=world["cloud"].fetch),
                    publish_fn=publish_then_edit, state_path=str(world["state"]))
    assert code == 0
    assert sheet.value(3, "Drive Image Link") == "https://shop.ae/owner-choice.jpg"
    assert "CONFLICT" in statuses(world["gs"]).values()


def test_apply_stops_at_the_picture_cap_and_the_cost_cap(world):
    world["run"](["--apply", "--max", "1", "--max-usd", "5"])
    state = read_state(world)
    assert (state["state"], state["done"], state["planned"]) == ("max", 1, 2)
    world["run"](["--apply", "--max", "5", "--max-usd", "0.05"])     # 3 calls x $0.02 > $0.05: nothing starts
    state = read_state(world)
    assert (state["state"], state["done"]) == ("budget", 0) and len(world["cloud"].uploads) == 1
    world["run"](["--apply", "--max", "5", "--max-usd", "0.06"])
    assert read_state(world)["done"] == 1 and len(world["cloud"].uploads) == 2


def test_apply_stops_on_an_error_and_the_next_run_goes_on(world):
    world["iso"].error = "photoroom_402"
    code, sheet = world["run"](["--apply"])
    state = read_state(world)
    assert code == 3 and state["state"] == "stopped" and state["last_error"] == "photoroom_402"
    assert state["done"] == 0 and len(world["iso"].calls) == 1                       # stopped at the first picture
    assert [e["status"] for e in world["db"].recut_entries()] == ["failed"]
    world["iso"].error = None
    code, sheet = world["run"](["--apply"], sheet)
    assert code == 0 and read_state(world)["done"] == 2                              # a failure is retried


def test_an_upload_failure_stops_the_batch(world):
    world["cloud"].fail_upload = True
    code, sheet = world["run"](["--apply"])
    assert code == 3 and read_state(world)["last_error"] == "upload_failed"
    assert sheet.value(2, "Drive Image Link") == MILK_URL


def test_a_cut_with_flags_waits_for_the_owner_and_is_not_retried(world):
    world["iso"].flags = ["dark_halo"]
    code, sheet = world["run"](["--apply"])
    state = read_state(world)
    assert code == 0 and state["needs_look"] == 2 and state["done"] == 0
    assert world["cloud"].uploads == [] and sheet.value(2, "Drive Image Link") == MILK_URL
    assert {e["code"] for e in world["db"].recut_entries()} == {"needs_look:dark_halo"}
    world["iso"].flags = []
    world["run"](["--apply"], sheet)
    assert read_state(world)["planned"] == 0                                        # skipped before: left alone
    world["run"](["--apply", "--retry-skipped"], sheet)
    assert read_state(world)["done"] == 2


def test_apply_refuses_during_a_run_and_while_another_batch_runs(world, monkeypatch, capsys):
    monkeypatch.setattr(rpt, "run_active", lambda: True)
    assert world["run"](["--apply"])[0] == 2
    assert "automation run is active" in capsys.readouterr().out
    monkeypatch.setattr(rpt, "run_active", lambda: False)
    rpt.write_state({"state": "running", "pid": -1}, str(world["state"]))
    assert world["run"](["--apply"])[0] == 2
    assert "Another reprocess batch is running" in capsys.readouterr().out


def test_a_busy_row_is_left_for_the_next_run(world):
    newer = BASE + NEW + "v1900000000/products/dairy/ffff6666"
    world["gs"].SQLiteTransactionQueue().append_update(2, None, newer, col_key="link", key_barcode=MILK)
    code, sheet = world["run"](["--apply", "--no-flush"])
    state = read_state(world)
    assert code == 0 and state["busy"] == 1 and state["done"] == 1


def test_an_interrupted_publish_is_finished_by_the_next_run(world, monkeypatch):
    import recut
    real = recut.finish_publish
    monkeypatch.setattr(recut, "finish_publish", lambda entry: (_ for _ in ()).throw(RuntimeError("killed")))
    with pytest.raises(RuntimeError):
        rpt.apply(type("A", (), {"trigger": "manual", "max": 1, "max_usd": 1.0, "retry_skipped": False,
                                 "no_flush": False})(), worksheet=the_sheet(),
                  probe_fn=lambda url: recut.probe(url, world["cloud"].fetch),
                  recut_fn=lambda row, method=None: recut.recut(row, method, process=world["iso"],
                                                                fetch=world["cloud"].fetch),
                  publish_fn=lambda *a, **k: recut.publish(*a, upload=world["cloud"].upload, **k),
                  state_path=str(world["state"]))
    assert [e["status"] for e in world["db"].recut_entries()] == ["uploaded"]
    monkeypatch.setattr(recut, "finish_publish", real)
    code, sheet = world["run"](["--apply", "--max", "0"])
    assert code == 0 and read_state(world)["resumed"] == 1
    entry = world["db"].recut_entries()[0]
    assert entry["status"] == "done"
    assert sheet.value(2, "Drive Image Link") == entry["new_url"] or sheet.value(3, "Drive Image Link") == entry[
        "new_url"]


def test_undo_puts_the_old_picture_back_through_the_outbox(world):
    import recut
    code, sheet = world["run"](["--apply", "--max", "1"])
    entry = world["db"].recut_entries()[0]
    row = entry["rows"][0]["row_number"]
    assert sheet.value(row, "Drive Image Link") == entry["new_url"]
    out = recut.undo(entry["id"])
    assert out["status"] == "undone" and out["outbox"]
    world["gs"].flush_outbox(sheet)
    assert sheet.value(row, "Drive Image Link") == new_form(entry["old_url"])
    master = world["db"].published_master(entry["resolved_ids"][0])
    assert master["cloudinary_url"] == entry["old_url"] and master["master_background"] == entry["old_background"]
    assert world["db"].recut_entry(entry["id"])["status"] == "undone"
    assert recut.undo(entry["id"])["code"] == "status_undone"


def test_undo_refuses_after_another_picture_was_approved(world):
    import recut
    world["run"](["--apply", "--max", "1"])
    entry = world["db"].recut_entries()[0]
    master = world["db"].published_master(entry["resolved_ids"][0])
    assert world["db"].save_product_resolution(master["barcode"], master["product_name"], master["brand"],
                                               "https://x/other.jpg", JUICE_URL, verification_status="human_approved",
                                               sku_key=master["sku_key"])
    assert recut.undo(entry["id"]) == {"status": "refused", "code": "changed_meanwhile", "outbox": {}}


def test_systemic_codes_stop_and_picture_codes_skip():
    assert rpt.systemic("photoroom_402") and rpt.systemic("photoroom_timeout") and rpt.systemic("photoroom_503")
    assert rpt.systemic("download_http_404") and rpt.systemic("upload_failed")
    assert not rpt.systemic("photoroom_empty_cutout") and not rpt.systemic("photoroom_bad_output")
