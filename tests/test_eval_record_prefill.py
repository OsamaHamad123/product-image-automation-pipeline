"""scripts/eval_record.py --prefill-labels-from-db: the dashboard's review decisions become labels in a
recording's labels.csv. Approvals label the image correct_exact; rejections are labelled by their reason
code; the rest stays empty for the owner. The first tests feed decisions directly; the last one seeds the
MariaDB test database (it skips when MariaDB is down) and runs the command line."""

import csv
import importlib.util
import json
import uuid
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SHA_A, SHA_B, SHA_C = "a" * 64, "b" * 64, "c" * 64


@pytest.fixture(scope="module")
def script():
    spec = importlib.util.spec_from_file_location("eval_record_under_test", REPO / "scripts" / "eval_record.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _line(sku_id, row, cid, url, sha="", label="", notes=""):
    return {"sku_id": sku_id, "row_number": str(row), "name": "AL ALALI FANCY TUNA WATER 170GM", "name_ar": "",
            "brand": "AL ALALI", "size": "170GM", "barcode": "", "candidate_id": cid, "provider": "serper",
            "query_kind": "Q1", "rank": "1", "title": "", "page_title": "", "page_url": "", "image_url": url,
            "image_file": f"blobs/{sha}.jpg" if sha else "", "width": "1200", "height": "1200", "download": "ok",
            "vlm_decision": "", "label": label, "notes": notes}


LULU = "https://lulu.akinoncloudcdn.com/products/alali-water-170.jpg"
COPY = "https://cdn.mafrservices.com/sys-master/alali-water-170-copy.jpg"       # same file as LULU (same sha)
OIL = "https://f.nooncdn.com/p/alali-oil-170.jpg"
SMALL = "https://www.amazon.ae/images/alali-small.jpg"
OTHER = "https://www.luluhypermarket.com/medias/emborg.jpg"


def _recording(tmp_path, script, lines):
    folder = tmp_path / "recorded"
    folder.mkdir()
    (folder / "golden_skus.json").write_text(json.dumps({"skus": [
        {"id": "rec-00019-a", "sku_key": "key-alali-water-170", "candidates": []},
        {"id": "rec-00057-b", "sku_key": "key-virginia-oil-170", "candidates": []},
        {"id": "rec-00060-c", "sku_key": "", "candidates": []},
    ]}), encoding="utf-8")
    path = folder / "labels.csv"
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=script.CSV_FIELDS)
        writer.writeheader()
        writer.writerows(lines)
    return path


def _read(path):
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return {(r["sku_id"], r["candidate_id"]): r for r in csv.DictReader(fh)}


def _decision(i, action, url, sku_key=None, row=None, reason=None, when="2026-10-01 10:00:00"):
    return {"id": i, "created_at": when, "action": action, "sku_key": sku_key, "row_number": row,
            "image_url": url, "reason_code": reason}


@pytest.mark.parametrize("code,label", [
    ("WRONG_PRODUCT", "wrong_product"), ("WRONG_BRAND", "wrong_brand"), ("WRONG_VARIANT", "wrong_variant"),
    ("WRONG_SIZE", "wrong_size"), ("WRONG_PACK", "wrong_pack"), ("NOT_PACKSHOT", "not_packshot"),
    ("LOW_QUALITY", "unusable"), ("BRAND_STYLE_MISMATCH", "wrong_brand"),
    ("HALO_ARTIFACT", None), ("BACKGROUND_BLEED", None), ("CROP_MARGIN_CLIPPING", None), (None, None),
])
def test_reject_reason_mapping(script, code, label):
    assert script.review_label({"action": "rejected", "reason_code": code}) == label
    assert set(script.REVIEW_LABELS.values()) <= set(script.LABELS)


def test_approve_and_manual_upload(script):
    assert script.review_label({"action": "approved"}) == "correct_exact"
    assert script.review_label({"action": "manual_upload"}) is None


def test_prefill_matches_product_and_image_and_keeps_existing_labels(script, tmp_path, capsys):
    path = _recording(tmp_path, script, [
        _line("rec-00019-a", 19, "c1", LULU, SHA_A),
        _line("rec-00019-a", 19, "c2", COPY, SHA_A),                  # same file as c1
        _line("rec-00019-a", 19, "c3", OIL, SHA_B),
        _line("rec-00019-a", 19, "c4", SMALL, SHA_C, label="wrong_brand", notes="checked by hand"),
        _line("rec-00019-a", 19, "c5", OTHER + "?w=600"),
        _line("rec-00057-b", 57, "c1", LULU, SHA_A),                  # same URL, another product: untouched
        _line("rec-00060-c", 60, "c1", OTHER),                        # no sku_key: matched by the sheet row
        _line("rec-00060-c", 60, "c2", "https://x.ae/halo.jpg"),
    ])
    decisions = [
        _decision(1, "approved", LULU, sku_key="key-alali-water-170", row=19),
        _decision(2, "rejected", OIL, sku_key="key-alali-water-170", reason="WRONG_VARIANT"),
        _decision(3, "rejected", SMALL, sku_key="key-alali-water-170", reason="WRONG_PRODUCT"),
        _decision(4, "rejected", OTHER + "?w=300", sku_key="key-alali-water-170", reason="LOW_QUALITY"),
        _decision(5, "rejected", OTHER, row=60, reason="NOT_PACKSHOT"),
        _decision(6, "rejected", "https://x.ae/halo.jpg", row=60, reason="HALO_ARTIFACT"),
        _decision(7, "approved", "https://elsewhere.ae/1.jpg", sku_key="key-nothing", row=99),
        _decision(8, "manual_upload", None, sku_key="key-alali-water-170", row=19),
    ]
    result = script.prefill_labels_from_db(path, decisions=decisions)
    labels = {k: (v["label"], v["notes"]) for k, v in _read(path).items()}
    assert labels[("rec-00019-a", "c1")] == ("correct_exact", "prefilled from dashboard review: approved")
    assert labels[("rec-00019-a", "c2")] == ("correct_exact",
                                             "prefilled from dashboard review: same image file as a reviewed candidate")
    assert labels[("rec-00019-a", "c3")] == ("wrong_variant", "prefilled from dashboard review: rejected WRONG_VARIANT")
    assert labels[("rec-00019-a", "c4")] == ("wrong_brand", "checked by hand")          # never overwritten
    # host + path match (the query string differs) and it is the only such image of the product
    assert labels[("rec-00019-a", "c5")][0] == "unusable"
    assert labels[("rec-00057-b", "c1")] == ("", "")
    assert labels[("rec-00060-c", "c1")][0] == "not_packshot"
    assert labels[("rec-00060-c", "c2")] == ("", "")                     # cosmetic rejection: no product label
    assert (result["prefilled"], result["approved"], result["rejected"], result["same_file"]) == (5, 1, 3, 1)
    assert (result["kept"], result["empty"]) == (1, 2)
    assert result["unmatched"] == 1 and result["not_mapped"] == 1 and result["matched"] == 5
    assert result["disagree"] == ["rec-00019-a/c4: file says wrong_brand, dashboard says wrong_product "
                                  "(rejected WRONG_PRODUCT)"]

    script.print_prefill(result, path)
    printed = capsys.readouterr().out
    assert "5 labels prefilled from dashboard reviews (1 approved -> correct_exact, 3 from rejections, " \
           "1 same image file)" in printed
    assert "2 candidates are still empty for you to label" in printed
    assert "1 labels in the file differ from the dashboard review" in printed

    # the file stays importable: every prefilled label is a known label
    assert {v[0] for v in labels.values()} <= set(script.LABELS) | {""}


def test_later_review_wins_and_ambiguous_urls_are_not_guessed(script, tmp_path):
    # two candidates share host + path (only the query differs): a review of a third variant is not guessed
    path = _recording(tmp_path, script, [
        _line("rec-00019-a", 19, "c1", LULU, SHA_A),
        _line("rec-00019-a", 19, "c2", "https://cdn.example.com/p/alali-170.jpg?v=1"),
        _line("rec-00019-a", 19, "c3", "https://cdn.example.com/p/alali-170.jpg?v=2"),
    ])
    decisions = [
        _decision(2, "rejected", LULU, sku_key="key-alali-water-170", reason="WRONG_SIZE", when="2026-10-02 09:00:00"),
        _decision(1, "approved", LULU, sku_key="key-alali-water-170", when="2026-10-01 09:00:00"),
        _decision(3, "approved", "https://cdn.example.com/p/alali-170.jpg?v=7", sku_key="key-alali-water-170"),
    ]
    result = script.prefill_labels_from_db(path, decisions=decisions)
    labels = {k[1]: v["label"] for k, v in _read(path).items()}
    assert labels == {"c1": "wrong_size", "c2": "", "c3": ""}
    assert result["conflicts"] == 1 and result["ambiguous"] == 1


def test_host_and_path_match_only_when_the_path_names_the_image_file(script, tmp_path):
    """Behind an image proxy the query string names the image ('/_next/image?url=...', 'img.php?id=...'):
    a review of another image behind the same path must not label the one candidate that has that path."""
    proxy = "https://www.noon.com/_next/image?url=https%3A%2F%2Ff.nooncdn.com%2Fp%2Falali-water-170.jpg&w=640"
    path = _recording(tmp_path, script, [
        _line("rec-00019-a", 19, "c1", proxy),
        _line("rec-00019-a", 19, "c2", "https://cdn.example.com/img.php?id=1"),
        _line("rec-00019-a", 19, "c3", LULU + "?w=600"),
    ])
    decisions = [
        _decision(1, "approved", proxy.replace("alali-water-170", "alali-oil-170"), sku_key="key-alali-water-170"),
        _decision(2, "rejected", "https://cdn.example.com/img.php?id=7", sku_key="key-alali-water-170",
                  reason="WRONG_SIZE"),
        _decision(3, "approved", LULU + "?w=300", sku_key="key-alali-water-170"),   # same file, other size
    ]
    result = script.prefill_labels_from_db(path, decisions=decisions)
    labels = {k[1]: v["label"] for k, v in _read(path).items()}
    assert labels == {"c1": "", "c2": "", "c3": "correct_exact"}
    assert result["unmatched"] == 2 and result["matched"] == 1


def test_the_owners_label_of_the_same_file_stops_the_copy(script, tmp_path):
    """A label the owner put on one copy of a downloaded file wins over the dashboard for its other
    copies too: the same-file copy happens only when every label known for that file agrees."""
    path = _recording(tmp_path, script, [
        _line("rec-00019-a", 19, "c1", LULU, SHA_A, label="wrong_size", notes="owner: this is the 1L can"),
        _line("rec-00019-a", 19, "c2", COPY, SHA_A),
        _line("rec-00019-a", 19, "c3", OIL, SHA_B),
        _line("rec-00019-a", 19, "c4", SMALL, SHA_B, label="wrong_pack"),
        _line("rec-00019-a", 19, "c5", OTHER, SHA_B),
        _line("rec-00019-a", 19, "c6", "https://x.ae/c6.jpg", SHA_C, label="wrong_brand"),
        _line("rec-00019-a", 19, "c7", "https://x.ae/c7.jpg", SHA_C),
    ])
    decisions = [
        _decision(1, "approved", LULU, sku_key="key-alali-water-170"),
        _decision(2, "approved", OIL, sku_key="key-alali-water-170"),
    ]
    result = script.prefill_labels_from_db(path, decisions=decisions)
    labels = {k[1]: v["label"] for k, v in _read(path).items()}
    assert labels == {"c1": "wrong_size", "c2": "", "c3": "correct_exact", "c4": "wrong_pack", "c5": "",
                      "c6": "wrong_brand", "c7": ""}     # c7: the owner's own label is not copied either
    assert result["same_file"] == 0 and result["prefilled"] == 1
    assert len(result["disagree"]) == 1 and result["disagree"][0].startswith("rec-00019-a/c1:")


def test_command_line_explains_a_locked_file_or_a_database_that_is_down(script, tmp_path, monkeypatch, capsys):
    """labels.csv open in Excel (Windows locks it) or MariaDB down: a plain sentence and exit 1, the file
    unchanged and no temporary file left behind, instead of a traceback."""
    path = _recording(tmp_path, script, [_line("rec-00019-a", 19, "c1", LULU, SHA_A)])
    before = path.read_bytes()
    monkeypatch.setattr(script, "load_review_decisions",
                        lambda: [_decision(1, "approved", LULU, sku_key="key-alali-water-170")])

    def locked(src, dst):
        raise PermissionError(13, "The process cannot access the file because it is being used by another process")

    monkeypatch.setattr(script.os, "replace", locked)
    assert script.main(["--prefill-labels-from-db", str(path)]) == 1
    err = capsys.readouterr().err
    assert "could not write" in err and "close it" in err
    assert path.read_bytes() == before and sorted(p.name for p in path.parent.iterdir()) == [
        "golden_skus.json", "labels.csv"]
    monkeypatch.undo()

    def down():
        raise ConnectionRefusedError("Can't connect to MySQL server on '127.0.0.1'")

    monkeypatch.setattr(script, "load_review_decisions", down)
    assert script.main(["--prefill-labels-from-db", str(path)]) == 1
    assert "could not read the dashboard's review decisions" in capsys.readouterr().err
    assert path.read_bytes() == before


def test_command_line_reads_the_seeded_database(script, mariadb_or_skip, tmp_path, capsys):
    db = mariadb_or_skip
    tag = uuid.uuid4().hex[:10]
    key_a, key_b = f"test-prefill-a-{tag}", f"test-prefill-b-{tag}"
    path = tmp_path / "labels.csv"
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=script.CSV_FIELDS)
        writer.writeheader()
        writer.writerows([_line("rec-a", 930019, "c1", LULU, SHA_A), _line("rec-a", 930019, "c2", OIL, SHA_B),
                          _line("rec-b", 930057, "c1", SMALL), _line("rec-b", 930057, "c2", OTHER)])
    (tmp_path / "golden_skus.json").write_text(json.dumps({"skus": [{"id": "rec-a", "sku_key": key_a},
                                                                    {"id": "rec-b", "sku_key": key_b}]}),
                                               encoding="utf-8")
    try:
        db.add_review_decision("approved", sku_key=key_a, row_number=930019, image_url=LULU,
                               engine_decision="REVIEW_PRESELECTED", was_preselected=True)
        db.add_review_decision("rejected", sku_key=key_a, row_number=930019, image_url=OIL, reason_code="WRONG_PACK")
        db.add_review_decision("rejected", sku_key=key_b, row_number=930057, image_url=SMALL,
                               reason_code="LOW_QUALITY")
        assert script.main(["--prefill-labels-from-db", str(path)]) == 0
    finally:
        conn = db.get_db_connection()
        try:
            conn.cursor().execute("DELETE FROM review_decisions WHERE sku_key IN (%s, %s)", (key_a, key_b))
            conn.commit()
        finally:
            conn.close()
    labels = {k: v["label"] for k, v in _read(path).items()}
    assert labels == {("rec-a", "c1"): "correct_exact", ("rec-a", "c2"): "wrong_pack",
                      ("rec-b", "c1"): "unusable", ("rec-b", "c2"): ""}
    printed = capsys.readouterr().out
    assert "3 labels prefilled from dashboard reviews (1 approved -> correct_exact, 2 from rejections" in printed
    assert "1 candidates are still empty for you to label" in printed
