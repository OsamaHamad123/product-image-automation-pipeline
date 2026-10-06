"""eval_record --from-db: the reviewed products of the database as a labelled set, and its offline replay.

A stand-in reader returns review decisions, queue rows and stored candidates the way the database does (the
MariaDB round trip is in tests/test_eval_export_dashboard.py). The export must label what the reviews say and
nothing else, keep small image copies the replay serves back at their recorded size, keep the label reader's
recorded readings, strip anything secret, and replay offline with no model call.
"""

import importlib.util
import io
import json
import zipfile
from pathlib import Path

import pytest
from PIL import Image

import harness
import imagegen
import metrics
import runners

pytestmark = pytest.mark.eval

REPO = Path(__file__).resolve().parent.parent.parent
SECRET = "-".join(("tst", "x", "7f3c9a1e", "b2d4"))            # a configured secret, built at run time


def _script(name):
    spec = importlib.util.spec_from_file_location(f"{name}_under_test", REPO / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def eval_record():
    return _script("eval_record")


SHA1, SHA2, SHA3 = "a" * 64, "b" * 64, "c" * 64
LULU = "https://gcc.luluhypermarket.com/medias/zwan-luncheon-340.jpg"
LULU_COPY = "https://cdn.example-mirror.ae/zwan-luncheon-340.jpg"
SIZE_SIB = "https://www.carrefouruae.com/img/zwan-luncheon-200.jpg"
BRAND_PAGE = "https://www.amazon.ae/images/I/71virginia-tuna.jpg"
OTHER = "https://www.noon.com/p/virginia-light-170.jpg"
GONE = "https://www.talabat.com/img/aseel-ghee-1-6kg.jpg"


def _reading(brand, variant, size, b, v, s, decision):
    return json.dumps({"index": 0, "brand_text": brand, "variant_text": variant, "size_text": size, "pack_count": 1,
                       "view": "front_packshot", "brand_match": b, "variant_match": v, "size_match": s,
                       "decision": decision})


class FakeReader:
    def review_decisions(self):
        return [
            {"id": 1, "created_at": "2026-10-05 10:00:00", "action": "approved", "sku_key": "kzwan340", "row_number": 12,
             "image_url": LULU, "page_domain": "gcc.luluhypermarket.com", "engine_decision": "REVIEW_PRESELECTED",
             "was_preselected": 1, "vlm_decision": "MATCH", "reason_code": None, "lane": "strict"},
            {"id": 2, "created_at": "2026-10-05 10:01:00", "action": "rejected", "sku_key": "kzwan340",
             "row_number": 12, "image_url": SIZE_SIB, "reason_code": "WRONG_SIZE"},
            {"id": 3, "created_at": "2026-10-05 10:02:00", "action": "rejected", "sku_key": None, "row_number": 30,
             "image_url": BRAND_PAGE, "reason_code": "WRONG_BRAND", "engine_decision": "REVIEW_PRESELECTED",
             "lane": "other"},
            {"id": 4, "created_at": "2026-10-05 10:03:00", "action": "approved", "sku_key": "kaseel", "row_number": 41,
             "image_url": GONE + f"?token={SECRET}", "page_domain": "talabat.com", "vlm_decision": "MATCH",
             "engine_decision": "REVIEW_PRESELECTED", "lane": "unsure"},
            {"id": 5, "created_at": "2026-10-05 10:04:00", "action": "manual_upload", "sku_key": "kaseel",
             "row_number": 41, "image_url": "https://res.cloudinary.com/x/manual.png"},
            {"id": 6, "created_at": "2026-10-05 10:05:00", "action": "rejected", "sku_key": "kaseel",
             "row_number": 41, "image_url": "https://example.ae/halo.jpg", "reason_code": "HALO"},
        ]

    def queue_rows(self):
        return [
            {"id": 7, "row_number": 12, "barcode": "", "product_name": "ZWAN CHICKEN LUNCHEON MEAT 340GM",
             "brand": "ZWAN", "payload_json": json.dumps({"name_ar": "", "category": "", "size": ""}),
             "sku_key": "kzwan340", "status": "ready_for_review",
             "trace_json": json.dumps({"outcome": {"decision": "REVIEW_PRESELECTED", "winner_url": LULU,
                                                   "queries": ["ZWAN CHICKEN LUNCHEON MEAT 340g"]}})},
            {"id": 8, "row_number": 30, "barcode": "", "product_name": "VIRGINIA W/MEAT TUNA BRINE 170GM",
             "brand": "VIRGINIA", "payload_json": None, "sku_key": "kvirginia", "status": "ready_for_review",
             "trace_json": None},
            {"id": 9, "row_number": 41, "barcode": "", "product_name": "ASEEL BUTTER GHEE 1.6KG", "brand": "ASEEL",
             "payload_json": "{}", "sku_key": "kaseel", "status": "completed",
             "trace_json": json.dumps({"outcome": {"decision": "REVIEW_PRESELECTED", "winner_url": GONE}})},
        ]

    def curation_rows(self):
        evidence = json.dumps({"tier": 1, "sanctioned": True, "page_domain": "gcc.luluhypermarket.com"})
        return [
            {"id": 11, "row_number": 12, "image_url": LULU, "title": "Zwan Chicken Luncheon Meat 340g",
             "width": 1200, "height": 1200, "source_domain": "gcc.luluhypermarket.com", "is_selected": 1,
             "status": "preselected", "sku_key": "kzwan340",
             "reasons_json": json.dumps(["vlm:MATCH", "preselected:vlm_match", "auto_blocked:auto_publish_disabled",
                                         "lane:strict"]),
             "evidence_json": evidence,
             "vlm_json": _reading("Zwan", "Chicken Luncheon Meat", "340g", "yes", "yes", "yes", "MATCH"),
             "content_sha256": SHA1, "identity_tier": "1",
             "page_url": f"https://gcc.luluhypermarket.com/en-ae/zwan-340/p/1?srsltid=abc&key={SECRET}"},
            {"id": 12, "row_number": 12, "image_url": SIZE_SIB, "title": f"Zwan Chicken Luncheon Meat 200g {SECRET}",
             "width": 800, "height": 800, "source_domain": "carrefouruae.com", "is_selected": 0, "status": "rejected",
             "sku_key": "kzwan340", "reasons_json": "[]", "evidence_json": evidence,
             "vlm_json": _reading("Zwan", "Chicken Luncheon Meat", "200g", "yes", "yes", "no", "MISMATCH"),
             "content_sha256": SHA2, "identity_tier": "1", "page_url": "https://www.carrefouruae.com/p/200"},
            {"id": 13, "row_number": 12, "image_url": LULU_COPY, "title": "Zwan Luncheon Chicken 340 g",
             "width": 1200, "height": 1200, "source_domain": "example-mirror.ae", "is_selected": 0,
             "status": "eligible", "sku_key": "kzwan340", "reasons_json": "[]",
             "evidence_json": json.dumps({"tier": 2, "sanctioned": False}), "vlm_json": None, "content_sha256": SHA1,
             "identity_tier": "2", "page_url": "https://cdn.example-mirror.ae/p/zwan"},
            {"id": 14, "row_number": 30, "image_url": BRAND_PAGE, "title": "Virginia White Meat Tuna in Brine 170g",
             "width": 1500, "height": 1500, "source_domain": "amazon.ae", "is_selected": 1, "status": "preselected",
             "sku_key": "kvirginia", "reasons_json": json.dumps(["preselected:vlm_match", "lane:other"]),
             "evidence_json": evidence,
             "vlm_json": _reading("Virginia", "White Meat Tuna", "170g", "yes", "yes", "yes", "MATCH"),
             "content_sha256": SHA3, "identity_tier": "1", "page_url": "https://www.amazon.ae/dp/B0V"},
            {"id": 15, "row_number": 30, "image_url": OTHER, "title": "Virginia Light Meat Tuna 170g",
             "width": 1000, "height": 1000, "source_domain": "noon.com", "is_selected": 0, "status": "eligible",
             "sku_key": "kvirginia", "reasons_json": "[]", "evidence_json": evidence, "vlm_json": None,
             "content_sha256": "d" * 64, "identity_tier": "1", "page_url": "https://www.noon.com/p/v"},
        ]


@pytest.fixture(scope="module")
def store(tmp_path_factory):
    folder = tmp_path_factory.mktemp("candidates")
    recipe = {"kind": "packshot_white", "size": [1200, 1200], "shape": "can",
              "label_text": "ZWAN|CHICKEN LUNCHEON|340G"}
    (folder / f"{SHA1}.jpg").write_bytes(imagegen.generate(recipe, 1))
    png = imagegen.generate(dict(recipe, size=[800, 800], label_text="ZWAN|CHICKEN LUNCHEON|200G",
                                 background="transparent"), 2)
    (folder / f"{SHA2}.png").write_bytes(png)
    (folder / f"{SHA3}.webp").write_bytes(b"not an image")          # a broken file: no blob, never a crash
    return folder


@pytest.fixture(scope="module")
def exported(eval_record, store, tmp_path_factory):
    out = tmp_path_factory.mktemp("recorded") / "2026-10-06"
    zip_path = out.parent / "exports" / "laqta_eval_set_2026-10-06.zip"
    manifest = eval_record.export_from_db(out, reader=FakeReader(), store_dir=store,
                                          mappings={"zwan": {"brand": "Zwan", "synonyms": ["Zwan"]}},
                                          zip_path=zip_path, today="2026-10-06", secrets=[SECRET])
    golden = json.loads((out / "golden_skus.json").read_text(encoding="utf-8"))
    cassette = json.loads((out / "vlm_cassette.json").read_text(encoding="utf-8"))
    return {"out": out, "manifest": manifest, "golden": golden, "cassette": cassette, "zip": zip_path}


def _sku(golden, row):
    return next(s for s in golden["skus"] if s["row_number"] == row)


def test_each_reviewed_product_is_one_sku_with_its_sheet_row(exported):
    golden = exported["golden"]
    assert [s["row_number"] for s in golden["skus"]] == [12, 30, 41]
    zwan = _sku(golden, 12)
    assert (zwan["name_en"], zwan["brand"], zwan["sku_key"], zwan["stratum"]) == (
        "ZWAN CHICKEN LUNCHEON MEAT 340GM", "ZWAN", "kzwan340", "recorded_db")
    assert zwan["id"].startswith("db-00012-")
    assert [c["image_url"] for c in zwan["candidates"]] == [LULU, SIZE_SIB, LULU_COPY]


def test_labels_come_from_the_reviews_and_the_same_file(exported):
    golden = exported["golden"]
    zwan, virginia, aseel = (_sku(golden, r) for r in (12, 30, 41))
    assert [c["label"] for c in zwan["candidates"]] == ["correct_exact", "wrong_size", "correct_exact"]
    assert zwan["candidates"][2]["note"] == "same image file as a reviewed candidate"
    assert [c["label"] for c in virginia["candidates"]] == ["wrong_brand", ""]       # the other one is unreviewed
    assert virginia["no_correct_candidate"] is True and zwan["no_correct_candidate"] is False
    # an approval whose candidates the database no longer keeps: the reviewed images themselves; a cosmetic
    # rejection (HALO) says nothing about the product, so its image stays unlabelled
    assert [(c["label"], c["download"]) for c in aseel["candidates"]] == [("correct_exact", "not_recorded"),
                                                                          ("", "not_recorded")]
    m = exported["manifest"]
    assert (m["products"], m["candidates"], m["labelled"], m["decisions_used"]) == (3, 7, 5, 4)
    assert m["decisions_without_label"] == 1                    # the cosmetic HALO rejection; uploads are skipped


def test_images_are_small_copies_that_the_replay_serves_at_their_recorded_size(exported):
    golden, out = exported["golden"], exported["out"]
    zwan = _sku(golden, 12)
    first, sibling, copy = zwan["candidates"]
    assert first["image_file"] == copy["image_file"]                       # one file, one blob
    for cand in (first, sibling):
        with Image.open(out / cand["image_file"]) as im:
            assert max(im.size) <= 384
    assert sibling["mime"] == "image/png" and first["mime"] == "image/jpeg"
    assert first["recorded_size"] == [1200, 1200] and sibling["recorded_size"] == [800, 800]
    sku = dict(zwan, _base_dir=str(out))
    served = runners._served_bytes(runners.candidate_image(sku, first), first)
    with Image.open(io.BytesIO(served)) as im:
        assert im.size == (1200, 1200)
    broken = _sku(golden, 30)["candidates"][0]
    assert broken["download"] == "not_recorded" and "image_file" not in broken
    assert exported["manifest"]["images"] == 3 and exported["manifest"]["images_missing"] == 4


def test_the_recorded_readings_and_the_live_pick_are_kept(exported):
    golden, cassette = exported["golden"], exported["cassette"]
    zwan = _sku(golden, 12)
    readings = cassette["verdicts"][zwan["id"]]
    assert readings["c1"]["brand_match"] == "yes" and readings["c1"]["recorded_decision"] == "MATCH"
    assert readings["c2"]["size_match"] == "no" and "c3" not in readings
    assert cassette["missing"] == "UNKNOWN"
    assert zwan["recorded"]["pick"] == "c1" and zwan["recorded"]["lane"] == "strict"
    assert zwan["recorded"]["queries"] == ["ZWAN CHICKEN LUNCHEON MEAT 340g"]
    aseel = _sku(golden, 41)
    assert cassette["verdicts"][aseel["id"]] == {"c1": {"recorded_decision": "MATCH"}}
    assert aseel["recorded"]["pick"] == "c1" and aseel["recorded"]["lane"] == "unsure"


def test_nothing_secret_leaves_with_the_set(exported):
    out = exported["out"]
    for path in out.rglob("*"):
        if path.is_file() and path.suffix in (".json", ".csv"):
            text = path.read_text(encoding="utf-8-sig")
            assert SECRET not in text, path.name
    zwan = _sku(exported["golden"], 12)
    assert "key=" not in zwan["candidates"][0]["page_url"] and "srsltid=abc" in zwan["candidates"][0]["page_url"]
    assert "token=" not in _sku(exported["golden"], 41)["candidates"][0]["image_url"]
    assert "[hidden]" in zwan["candidates"][1]["title"]
    keys = set()

    def walk(doc):
        if isinstance(doc, dict):
            keys.update(k.lower() for k in doc)
            for v in doc.values():
                walk(v)
        elif isinstance(doc, list):
            for v in doc:
                walk(v)

    walk(exported["golden"])
    assert not [k for k in keys if "user" in k or "reviewer" in k or "email" in k or "ip" == k]


def test_the_folder_has_a_manifest_labels_and_one_zip_to_send(exported, eval_record):
    out, m = exported["out"], exported["manifest"]
    assert {p.name for p in out.iterdir()} >= {"golden_skus.json", "vlm_cassette.json", "brand_mappings.json",
                                                "labels.csv", "manifest.json", "blobs"}
    stored = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert stored["format"] == "laqta_eval_set/1" and stored["labels"]["correct_exact"] == 3
    assert sum(stored["split"].values()) == 3
    with zipfile.ZipFile(exported["zip"]) as zf:
        names = set(zf.namelist())
    assert "2026-10-06/golden_skus.json" in names and "2026-10-06/manifest.json" in names
    assert any(n.startswith("2026-10-06/blobs/") for n in names)
    assert m["zip"] == str(exported["zip"])
    # a second export of the same day never overwrites the first (its labels.csv may be half filled in)
    assert eval_record._next_free(out) == Path(f"{out}-2")


def test_the_set_replays_offline_with_the_recorded_readings(exported):
    out = exported["out"]
    paths = harness.set_paths(out / "golden_skus.json")
    golden = harness.load_golden(paths["golden"])
    report = harness.run_all("v2", golden=golden, cassette=harness.load_cassette(paths["cassette"]),
                             mappings=harness.load_mappings(paths["mappings"]), set_name="2026-10-06")
    assert not report["network_attempts"] and not [o for o in report["outcomes"] if o["error"]]
    by_row = {s["id"]: s["row_number"] for s in golden["skus"]}
    outcome = {by_row[o["sku_id"]]: o for o in report["outcomes"]}
    assert outcome[12]["chosen_id"] in ("c1", "c3")                # the right can (c3 is its unread copy)
    assert outcome[30]["chosen_id"] != "c2" or outcome[30]["decision"] != metrics.AUTO
    assert outcome[41]["chosen_id"] is None                   # its only image was never stored: nothing to pick


def test_eval_report_scores_the_live_picks_and_says_what_is_unlabelled(exported, capsys):
    eval_report = _script("eval_report")
    code = eval_report.main(["--engine", "v2", "--golden", str(exported["out"] / "golden_skus.json"),
                             "--out", str(exported["out"].parent / "report.json")])
    printed = capsys.readouterr().out
    assert code == 0
    assert "as recorded live (the engine's pick then, scored by the reviews): correct pick 2/2" in printed
    assert "wrong picks 1" in printed
    assert "picks landed on unlabelled candidates" in printed or "had no recorded reading" in printed


def test_the_live_reader_is_never_called_without_a_yes(exported, monkeypatch, capsys):
    eval_report = _script("eval_report")
    import verifier_configs

    def boom(*a, **k):
        raise AssertionError("a live reader was built")

    monkeypatch.setattr(verifier_configs, "live_verifier", boom)
    monkeypatch.setattr("builtins.input", lambda *a: "no")
    code = eval_report.main(["--engine", "v2", "--golden", str(exported["out"] / "golden_skus.json"),
                             "--live-verifier"])
    printed = capsys.readouterr().out
    assert code == 1 and "nothing was called" in printed and "products: at most $" in printed
    with pytest.raises(SystemExit):
        eval_report.main(["--engine", "v2", "--scenario", "gemini_down", "--live-verifier"])


def test_the_command_line_says_when_the_database_is_down(eval_record, monkeypatch, capsys, tmp_path):
    class Down:
        def review_decisions(self):
            raise OSError("refused")

    monkeypatch.setattr(eval_record, "DbReader", Down)
    assert eval_record.main(["--from-db", "--out", str(tmp_path / "x")]) == 1
    assert "is MariaDB running" in capsys.readouterr().err
    assert not (tmp_path / "x").exists()


def test_clean_url_drops_only_credential_like_parameters(eval_record):
    url = "https://cdn.x.ae/a.jpg?w=800&X-Amz-Signature=zz&Expires=9&im=Resize%3D1500&token=t"
    assert eval_record.clean_url(url) == "https://cdn.x.ae/a.jpg?w=800&im=Resize%3D1500"
    assert eval_record.clean_url("https://x.ae/a.jpg") == "https://x.ae/a.jpg"
