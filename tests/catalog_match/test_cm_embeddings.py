"""catalog_match.embeddings, the embedder itself: the setting, the pinned model file, the ONNX backend's batching and
its one-at-a-time inference, the preprocessing, and the code paths with EMBEDDINGS off or onnxruntime missing.

The logic runs on fakes (no onnxruntime, no model file, no network). One optional real-model test is marked slow
and skips unless onnxruntime is installed and the pinned DINOv2-small file is already in EMBEDDINGS_MODEL_DIR.
"""

import hashlib
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from PIL import Image

from catalog_match import embeddings, settings
from embed_fakes import ColourEmbedder, packshot

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setattr(settings, "_config", None)
    monkeypatch.delenv("EMBEDDINGS", raising=False)
    monkeypatch.delenv("EMBEDDINGS_MODEL_DIR", raising=False)
    embeddings.reset()
    yield
    embeddings.reset()


# ---------------------------------------------------------------------------
# The setting
# ---------------------------------------------------------------------------

def test_embeddings_are_off_by_default_and_an_unknown_value_reads_off(monkeypatch):
    assert settings.DEFAULTS["EMBEDDINGS"] == "off"
    assert settings.embeddings_mode() == "off"
    for value, expected in (("dinov2", "dinov2"), ("SIGLIP2", "siglip2"), ("clip", "off"), ("", "off"),
                            ("on", "off")):
        monkeypatch.setenv("EMBEDDINGS", value)
        assert settings.embeddings_mode() == expected


def test_the_model_folder_is_the_shared_models_folder_on_a_server_else_temp(monkeypatch, tmp_path):
    monkeypatch.delenv("U2NET_HOME", raising=False)
    assert settings.embeddings_model_dir() == str(ROOT / "temp" / "models")
    monkeypatch.setenv("U2NET_HOME", str(tmp_path))
    assert settings.embeddings_model_dir() == str(tmp_path / "embeddings")
    monkeypatch.setenv("EMBEDDINGS_MODEL_DIR", str(tmp_path / "x"))
    assert settings.embeddings_model_dir() == str(tmp_path / "x")


def test_off_means_no_embedder_even_with_a_fake_installed(monkeypatch):
    embeddings.set_embedder(ColourEmbedder())
    assert embeddings.get_embedder() is None and not embeddings.enabled()
    monkeypatch.setenv("EMBEDDINGS", "dinov2")
    assert isinstance(embeddings.get_embedder(), ColourEmbedder)


def test_the_real_embedder_is_one_per_process_and_loads_nothing_until_used(monkeypatch, tmp_path):
    monkeypatch.setenv("EMBEDDINGS", "dinov2")
    monkeypatch.setenv("EMBEDDINGS_MODEL_DIR", str(tmp_path))
    first = embeddings.get_embedder()
    assert isinstance(first, embeddings.OnnxEmbedder) and first is embeddings.get_embedder()
    assert first.model_id == embeddings.MODELS["dinov2"].model_id and first.dim == 384
    assert first._session is None and not list(tmp_path.iterdir())
    # the dashboard's approval path never starts a download: its own embedder says so
    assert embeddings.get_embedder(allow_download=False).allow_download is False


def test_every_model_is_pinned_to_a_commit_a_file_and_its_sha256():
    for spec in embeddings.MODELS.values():
        assert len(spec.revision) == 40 and all(c in "0123456789abcdef" for c in spec.revision)
        assert len(spec.sha256) == 64 and spec.url.endswith(f"/resolve/{spec.revision}/{spec.filename}")
        assert spec.license == "Apache-2.0" and spec.filename.endswith(".onnx")
        assert spec.model_id.startswith(spec.key + ":")
    assert embeddings.MODELS["dinov2"].repo == "onnx-community/dinov2-small"


# ---------------------------------------------------------------------------
# The model file: download through net_guard, sha256 check, never a wrong file
# ---------------------------------------------------------------------------

class _Resp:
    def __init__(self, status, body=b"", location=None):
        self.status_code = status
        self.body = body
        self.headers = {"Location": location} if location else {}
        self.closed = False

    def iter_content(self, chunk_size=1):
        for i in range(0, len(self.body), 7):
            yield self.body[i:i + 7]

    def close(self):
        self.closed = True


def _spec_for(body, **kw):
    spec = embeddings.MODELS["dinov2"]
    fields = dict(spec.__dict__, sha256=hashlib.sha256(body).hexdigest(), size_bytes=len(body))
    fields.update(kw)
    return embeddings.ModelSpec(**fields)


def test_the_download_follows_the_redirect_through_net_guard_and_checks_the_sha256(tmp_path, monkeypatch):
    import net_guard

    body = b"onnx-bytes-" * 50
    spec = _spec_for(body)
    seen, checked = [], []
    real_follow = net_guard.follow

    def follow(url, send, **kw):
        def checked_send(hop, target):
            checked.append(hop)
            return send(hop, target)
        return real_follow(url, checked_send, **kw)

    monkeypatch.setattr(net_guard, "follow", follow)

    def get(url, **kw):
        seen.append((url, kw.get("allow_redirects"), kw.get("stream")))
        if "huggingface.co" in url:
            return _Resp(302, location="https://cdn-lfs.example-cdn.com/model.onnx")
        return _Resp(200, body)

    path = embeddings.download_model(spec, str(tmp_path), get=get)
    assert path.read_bytes() == body and path.name == spec.local_name
    assert [u for u, _, _ in seen] == [spec.url, "https://cdn-lfs.example-cdn.com/model.onnx"] == checked
    assert all(r is False and s is True for _, r, s in seen)       # redirects are net_guard's, never the client's
    assert [p.name for p in tmp_path.iterdir()] == [spec.local_name]


@pytest.mark.parametrize("served", [b"tampered" * 20, b"x" * 2000], ids=["other_sha256", "too_large"])
def test_a_download_whose_bytes_are_not_the_pinned_file_leaves_nothing(tmp_path, served):
    spec = _spec_for(b"the-real-model" * 10)
    with pytest.raises(embeddings.ModelUnavailable):
        embeddings.download_model(spec, str(tmp_path), get=lambda url, **kw: _Resp(200, served))
    assert not list(tmp_path.iterdir())


def test_a_model_url_that_is_not_public_is_refused(tmp_path, monkeypatch):
    import net_guard
    from net_fakes import fake_getaddrinfo

    monkeypatch.setattr(net_guard, "getaddrinfo", fake_getaddrinfo({"huggingface.co": "10.0.0.5"}))
    net_guard.reset_cache()
    try:
        with pytest.raises(embeddings.ModelUnavailable):
            embeddings.download_model(_spec_for(b"m" * 30), str(tmp_path),
                                      get=lambda url, **kw: pytest.fail("a private address was requested"))
    finally:
        net_guard.reset_cache()


def test_a_local_file_with_another_sha256_is_never_loaded(tmp_path):
    spec = _spec_for(b"pinned-model" * 10)
    embeddings.model_path(spec, str(tmp_path)).write_bytes(b"something else")
    with pytest.raises(embeddings.ModelUnavailable, match="sha256"):
        embeddings.ensure_model(spec, str(tmp_path), allow_download=False)


def test_a_missing_file_is_not_downloaded_when_downloads_are_off(tmp_path):
    with pytest.raises(embeddings.ModelUnavailable, match="missing"):
        embeddings.ensure_model(embeddings.MODELS["dinov2"], str(tmp_path), allow_download=False)


# ---------------------------------------------------------------------------
# The ONNX backend: unavailable never raises, batches, one inference at a time
# ---------------------------------------------------------------------------

def test_without_onnxruntime_the_embedder_returns_no_vectors_and_retries_only_later(monkeypatch, tmp_path):
    now = [1000.0]
    monkeypatch.setattr(embeddings, "onnxruntime_module", lambda: None)
    emb = embeddings.OnnxEmbedder(embeddings.MODELS["dinov2"], str(tmp_path), clock=lambda: now[0])
    assert emb.embed([packshot((200, 0, 0)), None]) == [None, None]
    assert "onnxruntime is not installed" in emb.error
    tries = []
    monkeypatch.setattr(embeddings, "onnxruntime_module", lambda: tries.append(1))
    emb.embed([packshot((200, 0, 0))])
    assert not tries                                   # within RETRY_AFTER_S: not tried again
    now[0] += embeddings.RETRY_AFTER_S + 1
    emb.embed([packshot((200, 0, 0))])
    assert tries == [1]


class _FakeSession:
    def __init__(self, dim=384, cls=True, delay=0.0):
        self.batches, self.dim, self.cls, self.delay = [], dim, cls, delay
        self.running = 0
        self.overlap = False

    def run(self, outputs, feeds):
        import numpy as np

        self.running += 1
        self.overlap = self.overlap or self.running > 1
        try:
            time.sleep(self.delay)
            batch = feeds["pixel_values"]
            assert batch.shape[1:] == (3, 224, 224) and batch.dtype == np.float32
            self.batches.append(batch.shape[0])
            base = batch.reshape(batch.shape[0], -1)[:, : self.dim].astype(np.float32) + 3.0
            if self.cls:
                return [np.stack([base, -base], axis=1)]          # (batch, tokens, dim): token 0 is used
            return [base]
        finally:
            self.running -= 1


def _ready(emb, session):
    emb._session = session
    return emb


def test_pictures_go_through_in_batches_of_eight_and_come_back_as_unit_vectors(tmp_path):
    session = _FakeSession()
    emb = _ready(embeddings.OnnxEmbedder(embeddings.MODELS["dinov2"], str(tmp_path)), session)
    images = [packshot((20 * i, 50, 200)) for i in range(11)]
    images[3] = None
    vectors = emb.embed(images)
    assert session.batches == [8, 2]
    assert vectors[3] is None and all(v is not None for i, v in enumerate(vectors) if i != 3)
    assert all(abs(float((v @ v)) - 1.0) < 1e-5 and v.shape == (384,) for v in vectors if v is not None)


def test_only_one_inference_runs_at_a_time_in_the_process(tmp_path):
    session = _FakeSession(delay=0.05)
    first = _ready(embeddings.OnnxEmbedder(embeddings.MODELS["dinov2"], str(tmp_path)), session)
    second = _ready(embeddings.OnnxEmbedder(embeddings.MODELS["dinov2"], str(tmp_path)), session)
    threads = [threading.Thread(target=e.embed, args=([packshot((10, 10, 10))] * 3,)) for e in (first, second) * 3]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(session.batches) == 6 and not session.overlap


def test_a_failed_inference_costs_its_batch_only(tmp_path):
    class Flaky(_FakeSession):
        def run(self, outputs, feeds):
            if not self.batches and not getattr(self, "failed", False):
                self.failed = True
                raise RuntimeError("onnx")
            return super().run(outputs, feeds)

    emb = _ready(embeddings.OnnxEmbedder(embeddings.MODELS["dinov2"], str(tmp_path)), Flaky())
    vectors = emb.embed([packshot((90, 10, 10))] * 9)
    assert vectors[:8] == [None] * 8 and vectors[8] is not None


# ---------------------------------------------------------------------------
# Preprocessing and vectors
# ---------------------------------------------------------------------------

def test_preprocessing_compares_the_pack_not_its_margin_or_its_transparency():
    import numpy as np

    spec = embeddings.MODELS["dinov2"]
    roomy = packshot((180, 30, 30), size=(900, 900), margin=300)
    tight = roomy.crop((280, 280, 620, 620))
    cutout = Image.new("RGBA", (340, 340), (0, 0, 0, 0))
    cutout.paste(Image.new("RGBA", (300, 300), (180, 30, 30, 255)), (20, 20))
    a, b, c = (embeddings.preprocess(i, spec) for i in (roomy, tight, cutout))
    assert a.shape == (3, 224, 224) and a.dtype == np.float32
    assert float(np.abs(a - b).mean()) < 0.05 and float(np.abs(a - c).mean()) < 0.05
    # a blank picture keeps its frame (nothing to trim) instead of failing
    assert embeddings.preprocess(Image.new("RGB", (50, 80), "white"), spec).shape == (3, 224, 224)


def test_vectors_round_trip_through_the_database_bytes_and_cosine_is_safe():
    import numpy as np

    v = embeddings.unit([3.0, 4.0, 0.0])
    blob = embeddings.to_blob(v)
    assert len(blob) == 12 and np.allclose(embeddings.from_blob(blob, 3), v)
    assert embeddings.from_blob(blob, 4) is None and embeddings.from_blob(b"abc") is None
    assert embeddings.cosine([1, 0], [0, 1]) == 0.0 and embeddings.cosine([1, 0], [2, 0]) == pytest.approx(1.0)
    assert embeddings.cosine([1, 0], [1, 0, 0]) is None and embeddings.cosine(None, [1]) is None
    assert embeddings.unit([0, 0]) is None and embeddings.unit([float("nan"), 1]) is None


def test_the_fake_embedder_sees_pack_colours():
    fake = ColourEmbedder()
    red, red2, blue = fake.embed([packshot((200, 20, 20)), packshot((190, 30, 25), size=(300, 300)),
                                  packshot((20, 20, 200))])
    assert embeddings.cosine(red, red2) > 0.99 and embeddings.cosine(red, blue) < 0


# ---------------------------------------------------------------------------
# No onnxruntime, EMBEDDINGS off: no import errors, nothing loaded
# ---------------------------------------------------------------------------

def test_the_pipeline_imports_and_embeds_nothing_without_onnxruntime():
    code = (
        "import sys; sys.modules['onnxruntime'] = None\n"
        "import os; os.environ['EMBEDDINGS'] = 'dinov2'; os.environ['EMBEDDINGS_MODEL_DIR'] = sys.argv[1]\n"
        "from catalog_match import settings; settings._config = None\n"
        "from catalog_match import embeddings, pipeline, decide, expand, facade\n"
        "from PIL import Image\n"
        "emb = embeddings.get_embedder()\n"
        "assert emb.embed([Image.new('RGB', (40, 40), 'red')]) == [None], 'a vector without onnxruntime'\n"
        "assert 'onnxruntime is not installed' in emb.error\n"
        "os.environ['EMBEDDINGS'] = 'off'\n"
        "assert embeddings.get_embedder() is None\n"
        "print('ok')\n"
    )
    env = dict(os.environ, DB_PORT="1", PYTHONPATH=str(ROOT))
    proc = subprocess.run([sys.executable, "-c", code, str(ROOT / "temp" / "no-such-models")], cwd=str(ROOT),
                          env=env, capture_output=True, text=True, timeout=180)
    assert proc.returncode == 0 and proc.stdout.strip().endswith("ok"), proc.stderr[-2000:]


def test_off_never_imports_onnxruntime():
    code = (
        "import os, sys; os.environ['EMBEDDINGS'] = 'off'\n"
        "from catalog_match import settings; settings._config = None\n"
        "from catalog_match import embeddings, pipeline\n"
        "assert embeddings.get_embedder() is None\n"
        "assert 'onnxruntime' not in sys.modules\n"
        "print('ok')\n"
    )
    env = dict(os.environ, DB_PORT="1", PYTHONPATH=str(ROOT))
    proc = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT), env=env, capture_output=True, text=True,
                          timeout=180)
    assert proc.returncode == 0 and proc.stdout.strip().endswith("ok"), proc.stderr[-2000:]


# ---------------------------------------------------------------------------
# The real model (optional)
# ---------------------------------------------------------------------------

@pytest.mark.slow
def test_the_real_dinov2_model_tells_a_pack_from_another_brands_pack(monkeypatch):
    """Runs only where onnxruntime is installed and the pinned file is already in EMBEDDINGS_MODEL_DIR (it never
    downloads): python scripts/backfill_embeddings.py --setup dinov2, or install.sh --with-embeddings."""
    if embeddings.onnxruntime_module() is None:
        pytest.skip("onnxruntime is not installed")
    spec = embeddings.MODELS["dinov2"]
    folder = os.environ.get("LAQTA_TEST_EMBEDDINGS_DIR") or settings.embeddings_model_dir()
    if not embeddings.model_path(spec, folder).is_file():
        pytest.skip(f"the pinned model is not in {folder}")
    from PIL import ImageDraw

    def pack(body, band, word, size=(500, 700), shift=0):
        img = Image.new("RGB", size, "white")
        d = ImageDraw.Draw(img)
        d.rounded_rectangle((100 + shift, 80, 400 + shift, 640), radius=40, fill=body)
        d.rectangle((100 + shift, 250, 400 + shift, 420), fill=band)
        for i, ch in enumerate(word):
            d.ellipse((130 + shift + 45 * i, 300, 160 + shift + 45 * i, 360), fill=(255, 255, 255) if ch else body)
        return img

    emb = embeddings.OnnxEmbedder(spec, folder, allow_download=False)
    started = time.perf_counter()
    a, a_copy, b = emb.embed([pack((200, 20, 30), (250, 210, 0), [1, 1, 0, 1]),
                              pack((200, 20, 30), (250, 210, 0), [1, 1, 0, 1], size=(800, 900), shift=40)
                              .resize((400, 450)),
                              pack((20, 90, 40), (240, 240, 240), [0, 1, 1, 0])])
    elapsed = time.perf_counter() - started
    assert a is not None and a.shape == (spec.dim,)
    same, other = embeddings.cosine(a, a_copy), embeddings.cosine(a, b)
    # a resized, re-framed copy is the same picture; another design is further (flat drawings are all alike to the
    # model, so the absolute cross-design value is calibrated on real packshots, not here)
    assert same >= spec.thresholds.near_dup and other < same - 0.02, (same, other)
    assert elapsed < 30
