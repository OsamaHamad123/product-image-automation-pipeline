"""approved_embeddings: the vector of every approved picture (catalog_match.embeddings.remember_approval), stored
from the reviewer's approval and upload (cli_bridge) and the worker's AUTO_PUBLISH (main), in the table
local_cache_db.init_db creates. Evidence only: storing a vector never changes, delays on failure or blocks an
approval, and EMBEDDINGS=off (the default) stores nothing and reads nothing.

The first half is offline (fake embedder, the database replaced by recorders); the second half runs on the real
MariaDB test database and skips when it is down.
"""

import hashlib
import io

import pytest

from catalog_match import embeddings, settings
from embed_fakes import ColourEmbedder, packshot
from test_review_decisions import (  # noqa: F401  (the bridge with the sheet and the processing replaced)
    BRAND, CANDIDATES, LINK, PRE_URL, SKU, _approve_params, _upload_params, db_only, recorder, sheet,
)
from test_worker_wiring import _best, _task, race  # noqa: F401  (the worker's real auto-publish path)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setattr(settings, "_config", None)
    monkeypatch.delenv("EMBEDDINGS", raising=False)
    embeddings.reset()
    yield
    embeddings.reset()


@pytest.fixture
def fake(monkeypatch):
    """EMBEDDINGS on with the colour embedder; remember_approval's calls are recorded (and still run)."""
    monkeypatch.setenv("EMBEDDINGS", "dinov2")
    emb = ColourEmbedder()
    embeddings.set_embedder(emb)
    return emb


def _png(colour):
    buf = io.BytesIO()
    packshot(colour).save(buf, "PNG")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Offline: where the vector comes from, and that it can never break an approval
# ---------------------------------------------------------------------------

def test_off_stores_nothing_and_reads_nothing(monkeypatch):
    import local_cache_db

    monkeypatch.setattr(local_cache_db, "save_approved_embedding", lambda *a, **k: pytest.fail("stored"))
    embeddings.set_embedder(ColourEmbedder())
    assert embeddings.remember_approval(sku_key="s1", brand="Puck", cloudinary_url=LINK, sha256="ab" * 32) is False


def test_the_vector_comes_from_the_verified_candidate_bytes_first(fake, monkeypatch, tmp_path):
    import image_processor
    import local_cache_db

    data = _png((200, 20, 20))
    sha = hashlib.sha256(data).hexdigest()
    (tmp_path / f"{sha}.png").write_bytes(data)
    monkeypatch.setenv("CANDIDATE_STORE_DIR", str(tmp_path))
    monkeypatch.setattr(image_processor, "_download_bytes", lambda *a, **k: pytest.fail("downloaded"))
    saved = []
    monkeypatch.setattr(local_cache_db, "save_approved_embedding", lambda *a, **k: saved.append((a, k)) or True)
    assert embeddings.remember_approval(sku_key="s1", brand="AL ALALI", cloudinary_url=LINK, sha256=sha,
                                        url="https://x.ae/a.jpg")
    (args, kw), = saved
    model, sku, key, brand, link, source, blob, dim = args
    assert (model, sku, key, brand, link, source, dim) == ("fake:colour", "s1", "al alali", "AL ALALI", LINK,
                                                           "candidate", 3)
    assert kw == {"content_sha256": sha}
    assert embeddings.cosine(embeddings.from_blob(blob, 3), fake.vector(packshot((200, 20, 20)))) > 0.999


def test_without_stored_bytes_the_source_then_the_cloudinary_copy_is_downloaded(fake, monkeypatch, tmp_path):
    import image_processor
    import local_cache_db

    monkeypatch.setenv("CANDIDATE_STORE_DIR", str(tmp_path))
    asked = []

    def download(url, page_url=None, info=None):
        asked.append((url, page_url))
        return (None, "download_http_403") if "x.ae" in url else (_png((20, 20, 200)), None)

    monkeypatch.setattr(image_processor, "_download_bytes", download)
    saved = []
    monkeypatch.setattr(local_cache_db, "save_approved_embedding", lambda *a, **k: saved.append(a) or True)
    assert embeddings.remember_approval(sku_key="s1", brand="Puck", cloudinary_url=LINK, sha256="cd" * 32,
                                        url="https://x.ae/a.jpg", page_url="https://x.ae/p")
    assert asked == [("https://x.ae/a.jpg", "https://x.ae/p"), (LINK, None)]
    assert saved[0][5] == "cloudinary"


@pytest.mark.parametrize("missing", ["sku_key", "brand", "cloudinary_url"])
def test_an_approval_without_sku_brand_or_link_is_skipped(fake, monkeypatch, missing):
    import local_cache_db

    monkeypatch.setattr(local_cache_db, "save_approved_embedding", lambda *a, **k: pytest.fail("stored"))
    kw = dict(sku_key="s1", brand="Puck", cloudinary_url=LINK, path=None)
    kw[missing] = "" if missing != "brand" else "  "
    assert embeddings.remember_approval(**kw) is False


def test_a_database_or_model_failure_is_logged_never_raised(fake, monkeypatch, tmp_path, caplog):
    import local_cache_db

    upload = tmp_path / "up.png"
    upload.write_bytes(_png((10, 120, 10)))

    def broken(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(local_cache_db, "save_approved_embedding", broken)
    assert embeddings.remember_approval(sku_key="s1", brand="Puck", cloudinary_url=LINK, path=str(upload)) is False
    assert "was not stored" in caplog.text
    embeddings.set_embedder(None)
    monkeypatch.setattr(embeddings, "onnxruntime_module", lambda: None)
    monkeypatch.setenv("EMBEDDINGS_MODEL_DIR", str(tmp_path / "models"))
    monkeypatch.setattr(embeddings, "approved_picture", lambda *a, **k: pytest.fail("a picture read for nothing"))
    assert embeddings.remember_approval(sku_key="s1", brand="Puck", cloudinary_url=LINK, path=str(upload),
                                        url="https://x.ae/a.jpg") is False
    assert not (tmp_path / "models").exists()          # the approval path never downloads the model


# ---------------------------------------------------------------------------
# The hooks: reviewer approval and upload (cli_bridge), worker auto-publish (main)
# ---------------------------------------------------------------------------

def _remembered(monkeypatch, events, result=True):
    calls = []

    def remember(**kw):
        exists = kw.get("path") is not None and __import__("os").path.isfile(kw["path"])
        calls.append(dict(kw, path_existed=exists))
        events.append(("remember_approval",))
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(embeddings, "remember_approval", remember)
    return calls


def test_a_reviewer_approval_stores_the_verified_bytes_before_the_candidates_go(recorder, fake, monkeypatch):
    cli_bridge, events, state = recorder
    calls = _remembered(monkeypatch, events)
    assert cli_bridge.action_select_image(_approve_params())["status"] == "success"
    (call,) = calls
    assert (call["sku_key"], call["brand"], call["cloudinary_url"], call["sha256"], call["url"]) == (
        SKU, BRAND, LINK, CANDIDATES[0]["content_sha256"], PRE_URL)
    names = [e[0] for e in events]
    assert names.index("remember_approval") < names.index("delete_curation_candidates")


def test_a_manual_upload_stores_the_uploaded_file_before_it_is_removed(recorder, fake, monkeypatch, tmp_path):
    cli_bridge, events, state = recorder
    calls = _remembered(monkeypatch, events)
    params = _upload_params(tmp_path)
    assert cli_bridge.action_upload_manual_image(params)["status"] == "success"
    (call,) = calls
    assert call["path_existed"] and call["sku_key"] == SKU and call["cloudinary_url"] == LINK


def test_off_the_bridge_reads_nothing_for_it(recorder, monkeypatch):
    cli_bridge, events, state = recorder
    calls = _remembered(monkeypatch, events)
    reads = []
    import local_cache_db
    real = local_cache_db.get_curation_candidates
    monkeypatch.setattr(local_cache_db, "get_curation_candidates", lambda *a, **k: reads.append(1) or real(*a, **k))
    assert cli_bridge.action_select_image(_approve_params())["status"] == "success"
    off_reads = len(reads)
    assert calls == []
    monkeypatch.setenv("EMBEDDINGS", "dinov2")
    embeddings.set_embedder(ColourEmbedder())
    reads.clear()
    cli_bridge.action_select_image(_approve_params())
    assert len(reads) == off_reads + 1                 # the stored candidate's sha256, only when it is on


def test_a_failing_vector_never_changes_the_approval(recorder, fake, monkeypatch):
    cli_bridge, events, state = recorder
    _remembered(monkeypatch, events, result=RuntimeError("boom"))
    result = cli_bridge.action_select_image(_approve_params())
    assert result["status"] == "success" and result["image_link"] == LINK


def test_an_auto_publish_stores_the_candidate_bytes(race, fake, monkeypatch):
    main, rec = race
    calls = _remembered(monkeypatch, [])
    best = _best("AUTO_PUBLISH", page_url="https://www.carrefouruae.com/p/1", content_sha256="ef" * 32)
    task = dict(_task(), worker_id="w1#claim")
    assert main.auto_approve_product(task, best, object(), 5, sku_key="sku-laban-up") == "published"
    (call,) = calls
    assert (call["sku_key"], call["brand"], call["cloudinary_url"], call["sha256"], call["url"]) == (
        "sku-laban-up", "Al Rawabi", "https://res/a.png", "ef" * 32, best["url"])
    assert call["allow_model_download"] is True        # the worker may fetch the model (it searches with it too)


def test_a_superseded_auto_publish_stores_nothing(race, fake, monkeypatch):
    main, rec = race
    calls = _remembered(monkeypatch, [])
    rec["claim"] = False                               # the row is no longer this worker's: nothing is written
    task = dict(_task(), worker_id="w1#claim")
    assert main.auto_approve_product(task, _best("AUTO_PUBLISH"), object(), 5, sku_key="sku-laban-up") != "published"
    assert calls == []


# ---------------------------------------------------------------------------
# MariaDB: the table, the join with the served approvals, the backfill list
# ---------------------------------------------------------------------------

E_SKUS = ("embtest-sku-a", "embtest-sku-b", "embtest-sku-c")


@pytest.fixture
def db(db_only, mariadb_or_skip):
    db = mariadb_or_skip

    def cleanup():
        conn = db.get_db_connection()
        try:
            cur = conn.cursor()
            marks = ",".join(["%s"] * len(E_SKUS))
            cur.execute(f"DELETE FROM approved_embeddings WHERE sku_key IN ({marks})", E_SKUS)
            cur.execute(f"DELETE FROM resolved_products WHERE sku_key IN ({marks})", E_SKUS)
            conn.commit()
        finally:
            conn.close()

    cleanup()
    yield db
    cleanup()


def _approve(db, sku, brand, link, status="human_approved"):
    assert db.save_product_resolution("", f"Product {sku}", brand, f"https://shop.ae/{sku}.jpg", link,
                                      verification_status=status, approved_by="human", sku_key=sku)


def _vec(*values):
    return embeddings.to_blob(embeddings.unit(values))


def test_the_table_is_created_by_init_db_with_its_keys(db):
    assert db.init_db() is True                                # idempotent
    conn = db.get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("SHOW INDEX FROM approved_embeddings")
        names = {r["Key_name"] for r in cur.fetchall()}
    finally:
        conn.close()
    assert {"uq_approved_embedding", "idx_approved_embedding_brand"} <= names


def test_only_the_vectors_of_approvals_still_served_are_references(db):
    _approve(db, E_SKUS[0], "Puck", "https://res.cloudinary.com/x/a1.png")
    _approve(db, E_SKUS[1], "Kiri", "https://res.cloudinary.com/x/b1.png")
    db.save_approved_embedding("m:1", E_SKUS[0], "puck", "Puck", "https://res.cloudinary.com/x/a1.png", "candidate",
                               _vec(1, 0, 0), 3)
    db.save_approved_embedding("m:1", E_SKUS[1], "kiri", "Kiri", "https://res.cloudinary.com/x/b1.png", "cloudinary",
                               _vec(0, 1, 0), 3)
    db.save_approved_embedding("m:2", E_SKUS[1], "kiri", "Kiri", "https://res.cloudinary.com/x/b1.png", "cloudinary",
                               _vec(0, 0, 1), 3)
    rows = {r["sku_key"]: r for r in db.get_approved_embeddings("m:1") if r["sku_key"] in E_SKUS}
    assert set(rows) == {E_SKUS[0], E_SKUS[1]}
    assert embeddings.from_blob(rows[E_SKUS[0]]["vector"], 3).tolist() == [1.0, 0.0, 0.0]
    # another approval replaces the picture: its old vector is no reference any more (nothing deleted)
    _approve(db, E_SKUS[0], "Puck", "https://res.cloudinary.com/x/a2.png")
    # a rejected approval is superseded: same
    assert db.supersede_resolution(E_SKUS[1]) == 1
    assert [r for r in db.get_approved_embeddings("m:1") if r["sku_key"] in E_SKUS] == []
    # the same approval stored again updates its row
    db.save_approved_embedding("m:1", E_SKUS[0], "puck", "Puck", "https://res.cloudinary.com/x/a2.png", "upload",
                               _vec(0, 1, 1), 3)
    db.save_approved_embedding("m:1", E_SKUS[0], "puck", "Puck", "https://res.cloudinary.com/x/a2.png", "upload",
                               _vec(1, 1, 0), 3)
    (row,) = [r for r in db.get_approved_embeddings("m:1") if r["sku_key"] in E_SKUS]
    assert embeddings.cosine(embeddings.from_blob(row["vector"], 3), [1, 1, 0]) == pytest.approx(1.0)


def test_the_backfill_list_is_the_served_approvals_without_a_vector(db):
    _approve(db, E_SKUS[0], "Puck", "https://res.cloudinary.com/x/a1.png")
    _approve(db, E_SKUS[1], "Kiri", "https://res.cloudinary.com/x/b1.png", status="auto_verified")
    _approve(db, E_SKUS[2], "Lu", "https://res.cloudinary.com/x/c1.png")
    db.supersede_resolution(E_SKUS[2])
    db.save_approved_embedding("m:1", E_SKUS[0], "puck", "Puck", "https://res.cloudinary.com/x/a1.png", "candidate",
                               _vec(1, 0, 0), 3)
    missing = [r["sku_key"] for r in db.approvals_missing_embedding("m:1") if r["sku_key"] in E_SKUS]
    assert missing == [E_SKUS[1]]
    both = [r["sku_key"] for r in db.approvals_missing_embedding("m:2") if r["sku_key"] in E_SKUS]
    assert set(both) == {E_SKUS[0], E_SKUS[1]}
    assert len(db.approvals_missing_embedding("m:2", limit=1)) == 1


def test_remember_approval_end_to_end_feeds_the_references(db, fake, tmp_path):
    _approve(db, E_SKUS[0], "SUP/T", "https://res.cloudinary.com/x/a1.png")
    upload = tmp_path / "u.png"
    upload.write_bytes(_png((200, 20, 20)))
    assert embeddings.references("fake:colour").count(["sup t"]) == 0
    assert embeddings.remember_approval(sku_key=E_SKUS[0], brand="SUP/T",
                                        cloudinary_url="https://res.cloudinary.com/x/a1.png", path=str(upload))
    refs = embeddings.references("fake:colour")                # the cache was cleared by the approval
    assert refs.count([embeddings.brand_key("sup/t")]) == 1
    best, brand = refs.best(fake.vector(packshot((210, 10, 10))), ["sup t"])
    assert best > 0.99 and brand == "SUP/T"
