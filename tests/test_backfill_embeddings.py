"""scripts/backfill_embeddings.py: embeds the approved pictures that have no vector yet (a dry run by default, a cap
and a time cap), sets the model up (--setup) and prints the spread of the stored vectors (--calibrate).

Offline with a fake embedder and fake database for the logic; one test runs the whole --apply path on the real
MariaDB test database (skips when it is down). Nothing is downloaded: the picture reader is replaced.
"""

import importlib.util
import os

import pytest

from catalog_match import embeddings, settings
from embed_fakes import ColourEmbedder, packshot

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# loaded by path: scripts/ on sys.path would shadow repository modules of the same name (publish_check)
_spec = importlib.util.spec_from_file_location("backfill_embeddings",
                                               os.path.join(ROOT, "scripts", "backfill_embeddings.py"))
bf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bf)

RED, BLUE = (200, 20, 20), (20, 20, 200)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setattr(settings, "_config", None)
    monkeypatch.setenv("EMBEDDINGS", "dinov2")
    embeddings.reset()
    yield
    embeddings.reset()


class FakeDb:
    def __init__(self, rows):
        self.rows = list(rows)
        self.saved = []
        self.limits = []

    def approvals_missing_embedding(self, model, limit=None):
        self.limits.append((model, limit))
        return self.rows[:limit]

    def save_approved_embedding(self, *args, **kw):
        self.saved.append(args)


def _rows(n, brand="Puck"):
    return [{"id": i, "sku_key": f"sku{i}", "brand": brand, "original_url": f"https://shop.ae/{i}.jpg",
             "cloudinary_url": f"https://res.cloudinary.com/x/{i}.png", "verification_status": "human_approved"}
            for i in range(n)]


def _reader(colour=RED, fail=()):
    asked = []

    def picture(url=None, cloudinary_url=None, **kw):
        asked.append((url, cloudinary_url))
        return (None, "") if url in fail else (packshot(colour), "original")

    picture.asked = asked
    return picture


def test_a_dry_run_lists_and_embeds_nothing(capsys):
    db, emb = FakeDb(_rows(3)), ColourEmbedder()
    summary = bf.backfill(apply=False, limit=10, db=db, picture=_reader(), embedder=emb)
    assert summary["missing"] == 3 and summary["embedded"] == 0 and db.saved == [] and emb.calls == []
    assert "would embed sku0 (Puck)" in capsys.readouterr().out


def test_apply_embeds_up_to_the_cap_and_counts_what_it_cannot_read():
    rows = _rows(5) + [dict(_rows(1)[0], sku_key="nobrand", brand="  ")]
    db, emb = FakeDb(rows), ColourEmbedder()
    reader = _reader(fail={"https://shop.ae/1.jpg"})
    summary = bf.backfill(apply=True, limit=4, db=db, picture=reader, embedder=emb)
    assert db.limits == [("fake:colour", 4)]
    assert summary == {"missing": 4, "embedded": 3, "unreadable": 1, "skipped": 0, "stopped": None}
    model, sku, key, brand, link, source, blob, dim = db.saved[0]
    assert (model, sku, key, brand, link, source, dim) == ("fake:colour", "sku0", "puck", "Puck",
                                                           "https://res.cloudinary.com/x/0.png", "original", 3)
    assert reader.asked[0] == ("https://shop.ae/0.jpg", "https://res.cloudinary.com/x/0.png")
    summary = bf.backfill(apply=True, limit=10, db=FakeDb(rows[5:]), picture=_reader(), embedder=emb)
    assert summary["skipped"] == 1 and summary["embedded"] == 0          # no brand: never a reference


def test_the_time_cap_stops_between_approvals():
    ticks = iter([0.0, 0.0, 5.0, 11.0, 12.0])
    db = FakeDb(_rows(4))
    summary = bf.backfill(apply=True, limit=10, max_seconds=10, clock=lambda: next(ticks), db=db, picture=_reader(),
                          embedder=ColourEmbedder())
    assert summary["embedded"] == 2 and summary["stopped"] == "max_seconds"


def test_main_says_so_when_embeddings_are_off(monkeypatch, capsys):
    monkeypatch.setenv("EMBEDDINGS", "off")
    assert bf.main([]) == 2
    assert "EMBEDDINGS=off" in capsys.readouterr().out


def test_setup_checks_the_model_before_turning_it_on(monkeypatch, tmp_path):
    saved = []
    monkeypatch.setattr(embeddings, "onnxruntime_module", lambda: None)
    assert bf.setup("dinov2", str(tmp_path), save=saved.append) == 1 and saved == []

    monkeypatch.setattr(embeddings, "onnxruntime_module", lambda: object())
    monkeypatch.setattr(embeddings, "ensure_model", lambda spec, d, allow_download: tmp_path / "m.onnx")

    class Ready(embeddings.OnnxEmbedder):
        def embed(self, images):
            return [embeddings.unit([1.0] * self.dim) for _ in images]

    monkeypatch.setattr(embeddings, "OnnxEmbedder", Ready)
    assert bf.setup("dinov2", str(tmp_path), save=saved.append) == 0 and saved == ["dinov2"]

    class Broken(embeddings.OnnxEmbedder):
        def embed(self, images):
            self.error = "bad graph"
            return [None for _ in images]

    monkeypatch.setattr(embeddings, "OnnxEmbedder", Broken)
    assert bf.setup("dinov2", str(tmp_path), save=saved.append) == 1 and saved == ["dinov2"]
    assert bf.setup("off", str(tmp_path), save=saved.append) == 0 and saved == ["dinov2", "off"]


def test_calibration_counts_right_brand_pictures_the_rule_would_flag(capsys):
    fake = ColourEmbedder()
    v = lambda c: fake.vector(packshot(c))  # noqa: E731
    refs = [embeddings.Reference(f"s{i}", k, k, v(c)) for i, (k, c) in enumerate(
        [("puck", BLUE), ("puck", (30, 30, 210)), ("puck", (25, 30, 190)), ("puck", RED),
         ("kiri", RED), ("kiri", (190, 30, 30))])]
    result = bf.print_calibration("fake:colour", embeddings.THRESHOLDS["dinov2"], refs)
    # puck's red picture is far from puck's blue ones and close to kiri's red: one false warning
    assert result["judged"] == 4 and result["flagged"] == 1
    assert "would flag (false warnings): 1 of 4" in capsys.readouterr().out


E_SKUS = ("embbf-sku-a", "embbf-sku-b")


def test_apply_on_the_real_database(mariadb_or_skip, capsys):
    db = mariadb_or_skip

    def cleanup():
        conn = db.get_db_connection()
        try:
            cur = conn.cursor()
            cur.execute("DELETE FROM approved_embeddings WHERE sku_key IN (%s, %s)", E_SKUS)
            cur.execute("DELETE FROM resolved_products WHERE sku_key IN (%s, %s)", E_SKUS)
            conn.commit()
        finally:
            conn.close()

    cleanup()
    try:
        for sku in E_SKUS:
            assert db.save_product_resolution("", f"Product {sku}", "Puck", f"https://shop.ae/{sku}.jpg",
                                              f"https://res.cloudinary.com/x/{sku}.png",
                                              verification_status="human_approved", sku_key=sku)
        emb = ColourEmbedder()
        embeddings.set_embedder(emb)
        summary = bf.backfill(apply=True, limit=1000, picture=_reader(BLUE), embedder=emb)
        assert summary["embedded"] >= 2
        ours = [r for r in db.get_approved_embeddings("fake:colour") if r["sku_key"] in E_SKUS]
        assert len(ours) == 2 and {r["brand_key"] for r in ours} == {"puck"}
        again = bf.backfill(apply=True, limit=1000, picture=_reader(BLUE), embedder=emb)
        assert not [r for r in db.approvals_missing_embedding("fake:colour") if r["sku_key"] in E_SKUS]
        assert again["embedded"] == summary["embedded"] - 2 or again["missing"] < summary["missing"]
    finally:
        cleanup()
