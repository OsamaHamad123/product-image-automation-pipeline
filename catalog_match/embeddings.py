"""Image embeddings on the CPU (ONNX Runtime): EVIDENCE ONLY.

An embedding is a short vector per picture; two pictures of the same pack design have vectors that point the same
way (cosine close to 1). Nothing here ever picks, flips or rejects a candidate on its own: the vectors only add a
review warning (catalog_match.decide 'brand_look_mismatch') and a number next to pHash.

Setting EMBEDDINGS (catalog_match.settings): 'off' (the default) | 'dinov2' | 'siglip2'.
    'off' never imports onnxruntime, never loads or downloads a model and never reads the approved_embeddings table;
    the search, the decisions and every approval are exactly what they are without this module. onnxruntime is an
    optional dependency (deploy/ubuntu/install.sh --with-embeddings installs it): without it every call here is a
    no-op too, with one log line.

Model choice: DINOv2-small ('dinov2', the default of --with-embeddings), Apache-2.0
    The failure this serves is "the listing names the right brand, its picture shows ANOTHER brand's pack". That is a
    question of design (colours, logo shape, layout of the pack), not of meaning. DINOv2 is trained without text to
    tell instances apart: two photos of the same pack land close, two brands' tuna cans do not. SigLIP2 is trained to
    match captions, so it puts "a can of tuna" close to "a can of tuna" whatever the brand; its text tower would
    only help a query by words, which the search already does. DINOv2-small is also ~4x cheaper on a CPU (22M
    parameters, 384-d vectors) than SigLIP2-base's vision tower (86M, 768-d). 'siglip2' is kept as a pinned option
    for an experiment; its thresholds are not calibrated (see THRESHOLDS below).
    Both are the int8 ONNX exports of onnx-community (dynamic quantisation): on a 4-core CPU a batch of 8 pictures
    takes about 0.25 s with DINOv2-small int8 against 0.7 s for its fp32 export, and its vectors agree with fp32's
    (see Thresholds below).

The model file is pinned: Hugging Face repository + commit + file + sha256 (ModelSpec). It is downloaded once into
settings.embeddings_model_dir() (install.sh does it ahead of time; otherwise the worker does it on first use),
through net_guard (every redirect hop is checked), checked against the sha256 before it is used, and never
committed. A file whose sha256 differs is never loaded.

Embedder interface: .model_id (the pinned file, stored next to every vector: vectors of two models never meet),
.dim, .embed(images) -> one unit vector (numpy float32) or None per picture. OnnxEmbedder loads the session lazily
on the first embed() and runs one inference at a time in the process (a semaphore: the worker searches several
products at once and each inference already uses every core it is given); pictures go through in batches of
BATCH_SIZE. get_embedder() is the process-wide embedder of the setting, or None.

Preprocessing (preprocess): a transparent picture is put on white, the near-white margin is trimmed, the pack is
centred on a white square and resized to the model's input. A raw store packshot and an approved cutout (trimmed,
centred, transparent: image_processor) then compare as pictures of the pack, not of their margins.
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Optional, Sequence, Tuple

from . import settings

logger = logging.getLogger(__name__)

BATCH_SIZE = 8
# a failed model load (no onnxruntime, a failed download, a file whose sha256 differs) is tried again after this long
RETRY_AFTER_S = 600.0
DOWNLOAD_TIMEOUT_S = (10.0, 60.0)        # connect, read (per chunk)
WHITE_CUTOFF = 245                       # a pixel lighter than this on every channel is margin (preprocess)


@dataclass(frozen=True)
class Thresholds:
    """Cosine thresholds of one model for the brand look check (BrandLook) and the near-duplicate helper.

    same_max    a candidate is 'far from every approved image of its brand' below this
    other_min   ... and 'close to another brand's approved image' at or above this
    margin      ... and the other brand is at least this much closer than its own brand
    near_dup    two pictures at or above this are the same picture (re-encoded, resized, re-cropped)
    """
    same_max: float
    other_min: float
    margin: float
    near_dup: float


@dataclass(frozen=True)
class ModelSpec:
    key: str                  # the EMBEDDINGS value
    repo: str                 # Hugging Face repository
    revision: str             # its commit (never a branch name)
    filename: str             # the ONNX file in that commit
    sha256: str               # of that file
    size_bytes: int
    dim: int
    image_size: int
    mean: Tuple[float, float, float]
    std: Tuple[float, float, float]
    input_name: str
    output_name: str
    pooling: str              # 'cls': token 0 of last_hidden_state; 'pooled': a (batch, dim) output as is
    thresholds: Thresholds
    license: str = "Apache-2.0"

    @property
    def model_id(self) -> str:
        """What is stored with every vector: the model and the exact file (a new file never meets old vectors)."""
        return f"{self.key}:{self.revision[:10]}:{self.sha256[:12]}"

    @property
    def url(self) -> str:
        return f"https://huggingface.co/{self.repo}/resolve/{self.revision}/{self.filename}"

    @property
    def local_name(self) -> str:
        return f"{self.key}-{self.sha256[:16]}.onnx"


# How they were chosen and how to re-tune them: the Thresholds section of the module docstring.
THRESHOLDS = {
    "dinov2": Thresholds(same_max=0.45, other_min=0.70, margin=0.20, near_dup=0.90),
    # not calibrated: SigLIP2 cosines run higher for any two packshots; kept strict so it warns only on near copies
    "siglip2": Thresholds(same_max=0.60, other_min=0.92, margin=0.25, near_dup=0.95),
}

MODELS = {
    "dinov2": ModelSpec(
        key="dinov2", repo="onnx-community/dinov2-small", revision="8b1f705a3a7f6f062f6bdd21986c1583d3ef105d",
        filename="onnx/model_int8.onnx",
        sha256="dfce54a839b491f395c516350ebb4a78f947e9170a6beac0f2bc5638e0f09d61", size_bytes=24446700,
        dim=384, image_size=224, mean=(0.485, 0.456, 0.406), std=(0.229, 0.224, 0.225),
        input_name="pixel_values", output_name="last_hidden_state", pooling="cls", thresholds=THRESHOLDS["dinov2"]),
    "siglip2": ModelSpec(
        key="siglip2", repo="onnx-community/siglip2-base-patch16-224-ONNX",
        revision="ba1f3b0843f24bc5417d38e19c37b287d719b2f4", filename="onnx/vision_model_int8.onnx",
        sha256="0dd31785a2713f1113ef2272472165c69d580473dae38d7b47568ac587795e70", size_bytes=94553333,
        dim=768, image_size=224, mean=(0.5, 0.5, 0.5), std=(0.5, 0.5, 0.5),
        input_name="pixel_values", output_name="pooler_output", pooling="pooled", thresholds=THRESHOLDS["siglip2"]),
}


class ModelUnavailable(RuntimeError):
    """The model cannot be used: onnxruntime missing, the file missing (and no download), a bad sha256, ..."""


# ---------------------------------------------------------------------------
# Vectors
# ---------------------------------------------------------------------------

def _np():
    import numpy
    return numpy


def unit(vector: Any) -> Optional[Any]:
    """The vector scaled to length 1 (numpy float32), or None for an empty, zero or non-finite one."""
    if vector is None:
        return None
    np = _np()
    try:
        v = np.asarray(vector, dtype=np.float32).reshape(-1)
    except (TypeError, ValueError):
        return None
    norm = float(np.linalg.norm(v)) if v.size else 0.0
    if not norm or norm != norm or norm == float("inf"):
        return None
    return v / norm


def cosine(a: Any, b: Any) -> Optional[float]:
    """Cosine of two vectors (-1..1), None when either is missing or their lengths differ."""
    ua, ub = unit(a), unit(b)
    if ua is None or ub is None or ua.shape != ub.shape:
        return None
    return float(max(-1.0, min(1.0, float(ua @ ub))))


def to_blob(vector: Any) -> bytes:
    """float32 little-endian bytes of a vector (the approved_embeddings.vector column)."""
    np = _np()
    return np.asarray(vector, dtype="<f4").reshape(-1).tobytes()


def from_blob(blob: Any, dim: Optional[int] = None) -> Optional[Any]:
    """The unit vector stored by to_blob, or None for bytes of the wrong length."""
    np = _np()
    if blob is None:
        return None
    data = bytes(blob)
    if not data or len(data) % 4 or (dim and len(data) != 4 * int(dim)):
        return None
    return unit(np.frombuffer(data, dtype="<f4"))


# ---------------------------------------------------------------------------
# Preprocessing
# ---------------------------------------------------------------------------

def _on_white(img):
    from PIL import Image

    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        canvas = Image.new("RGB", rgba.size, (255, 255, 255))
        canvas.paste(rgba, mask=rgba.split()[3])
        return canvas
    return img.convert("RGB")


def _content_box(img) -> Optional[Tuple[int, int, int, int]]:
    """The box of what is not near-white margin, with a 2 % border; None when it would be (almost) the whole image."""
    gray = img.convert("L")
    mask = gray.point(lambda p: 255 if p < WHITE_CUTOFF else 0)
    box = mask.getbbox()
    if box is None:
        return None
    w, h = img.size
    bw, bh = box[2] - box[0], box[3] - box[1]
    if bw * bh < 0.01 * w * h:            # a few dark pixels on a white picture: keep the picture as it is
        return None
    pad = int(round(0.02 * max(bw, bh)))
    box = (max(0, box[0] - pad), max(0, box[1] - pad), min(w, box[2] + pad), min(h, box[3] + pad))
    return None if box == (0, 0, w, h) else box


def preprocess(img, spec: ModelSpec):
    """One picture as the model's input (3, size, size) float32: on white, margin trimmed, centred on a square."""
    from PIL import Image

    np = _np()
    rgb = _on_white(img)
    box = _content_box(rgb)
    if box is not None:
        rgb = rgb.crop(box)
    w, h = rgb.size
    side = max(w, h, 1)
    square = Image.new("RGB", (side, side), (255, 255, 255))
    square.paste(rgb, ((side - w) // 2, (side - h) // 2))
    square = square.resize((spec.image_size, spec.image_size), Image.BICUBIC)
    arr = np.asarray(square, dtype=np.float32) / 255.0
    arr = (arr - np.asarray(spec.mean, dtype=np.float32)) / np.asarray(spec.std, dtype=np.float32)
    return arr.transpose(2, 0, 1).astype(np.float32)


# ---------------------------------------------------------------------------
# The model file
# ---------------------------------------------------------------------------

def _sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def model_path(spec: ModelSpec, model_dir: Optional[str] = None) -> Path:
    return Path(model_dir or settings.embeddings_model_dir()) / spec.local_name


def download_model(spec: ModelSpec, model_dir: Optional[str] = None, *, get=None) -> Path:
    """Download the pinned file into the model folder (atomic rename after the sha256 check); its path.

    Every hop of the download (Hugging Face answers with a redirect to its CDN) goes through net_guard.follow, which
    checks it before it is requested. Raises ModelUnavailable. get: the HTTP GET to use (tests).
    """
    import net_guard

    dest = model_path(spec, model_dir)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if get is None:
        import requests
        get = requests.get
    tmp = dest.with_name(f".{dest.name}.{uuid.uuid4().hex}.part")
    digest, size = hashlib.sha256(), 0
    try:
        resp = net_guard.follow(spec.url, lambda hop, _target: get(
            hop, stream=True, timeout=DOWNLOAD_TIMEOUT_S, allow_redirects=False,
            headers={"User-Agent": "laqta-embeddings/1"}))
    except Exception as exc:
        raise ModelUnavailable(f"download of {spec.filename} failed: {type(exc).__name__}") from exc
    try:
        status = int(getattr(resp, "status_code", 0) or 0)
        if status != 200:
            raise ModelUnavailable(f"download of {spec.filename} failed: http_{status}")
        with open(tmp, "wb") as fh:
            for chunk in resp.iter_content(chunk_size=1 << 20):
                if not chunk:
                    continue
                size += len(chunk)
                if size > spec.size_bytes:
                    raise ModelUnavailable(f"{spec.filename} is larger than the pinned {spec.size_bytes} bytes")
                digest.update(chunk)
                fh.write(chunk)
        if digest.hexdigest() != spec.sha256:
            raise ModelUnavailable(f"{spec.filename}: sha256 differs from the pinned one; not used")
        os.replace(tmp, dest)
    except ModelUnavailable:
        raise
    except Exception as exc:
        raise ModelUnavailable(f"download of {spec.filename} failed: {type(exc).__name__}") from exc
    finally:
        close = getattr(resp, "close", None)
        if callable(close):
            try:
                close()
            except Exception:  # pragma: no cover - best effort
                pass
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:  # pragma: no cover - best effort
                pass
    logger.info("embeddings: %s downloaded to %s", spec.model_id, dest)
    return dest


def ensure_model(spec: ModelSpec, model_dir: Optional[str] = None, allow_download: bool = True) -> Path:
    """The verified local model file, downloaded first when missing and allowed; raises ModelUnavailable."""
    path = model_path(spec, model_dir)
    if not path.is_file():
        if not allow_download:
            raise ModelUnavailable(f"{path} is missing (scripts/backfill_embeddings.py --setup downloads it)")
        path = download_model(spec, model_dir)
    if _sha256_of(path) != spec.sha256:
        raise ModelUnavailable(f"{path}: sha256 differs from the pinned one; not used")
    return path


def onnxruntime_module():
    """The onnxruntime module, or None when it is not installed (an optional dependency)."""
    try:
        import onnxruntime  # type: ignore
    except Exception:
        return None
    return onnxruntime


# ---------------------------------------------------------------------------
# Embedders
# ---------------------------------------------------------------------------

# one inference at a time in the process, whatever embedder runs it (each uses every thread it is given)
_INFERENCE = threading.BoundedSemaphore(1)


class OnnxEmbedder:
    """Embedder over a pinned ONNX model with onnxruntime on the CPU. Lazy: nothing is loaded before embed()."""

    def __init__(self, spec: ModelSpec, model_dir: Optional[str] = None, allow_download: bool = True,
                 threads: Optional[int] = None, clock=time.monotonic):
        self.spec = spec
        self.model_dir = model_dir
        self.allow_download = allow_download
        self.threads = threads or max(1, min(4, os.cpu_count() or 1))
        self._clock = clock
        self._lock = threading.Lock()
        self._session = None
        self._failed_at: Optional[float] = None
        self.error: Optional[str] = None

    @property
    def model_id(self) -> str:
        return self.spec.model_id

    @property
    def dim(self) -> int:
        return self.spec.dim

    def _load(self):
        with self._lock:
            if self._session is not None:
                return self._session
            if self._failed_at is not None and self._clock() - self._failed_at < RETRY_AFTER_S:
                raise ModelUnavailable(self.error or "unavailable")
            try:
                ort = onnxruntime_module()
                if ort is None:
                    raise ModelUnavailable("onnxruntime is not installed (install.sh --with-embeddings)")
                path = ensure_model(self.spec, self.model_dir, self.allow_download)
                options = ort.SessionOptions()
                options.intra_op_num_threads = self.threads
                options.inter_op_num_threads = 1
                options.log_severity_level = 3
                self._session = ort.InferenceSession(str(path), sess_options=options,
                                                     providers=["CPUExecutionProvider"])
            except Exception as exc:
                self._failed_at = self._clock()
                self.error = str(exc) if isinstance(exc, ModelUnavailable) else f"{type(exc).__name__}: {exc}"
                logger.warning("embeddings: %s unavailable: %s", self.spec.key, self.error)
                raise ModelUnavailable(self.error) from exc
            self._failed_at, self.error = None, None
            return self._session

    def available(self) -> bool:
        """Loads the model when needed; False when it cannot be used (the reason is in .error)."""
        try:
            self._load()
            return True
        except ModelUnavailable:
            return False

    def _run(self, batch):
        out = self._session.run([self.spec.output_name], {self.spec.input_name: batch})[0]
        if self.spec.pooling == "cls":
            out = out[:, 0, :]
        return out

    def embed(self, images: Sequence[Any]) -> List[Optional[Any]]:
        """One unit vector per picture (None for a picture that cannot be read); all None when unavailable."""
        images = list(images or [])
        out: List[Optional[Any]] = [None] * len(images)
        if not images or not self.available():
            return out
        np = _np()
        arrays, where = [], []
        for i, img in enumerate(images):
            if img is None:
                continue
            try:
                arrays.append(preprocess(img, self.spec))
                where.append(i)
            except Exception as exc:  # a broken picture never costs the others their vectors
                logger.debug("embeddings: picture %d not readable: %s", i, exc)
        for start in range(0, len(arrays), BATCH_SIZE):
            batch = np.stack(arrays[start:start + BATCH_SIZE])
            try:
                with _INFERENCE:
                    vectors = self._run(batch)
            except Exception as exc:
                logger.warning("embeddings: inference failed: %s", type(exc).__name__)
                continue
            for j, vec in enumerate(vectors):
                out[where[start + j]] = unit(vec)
        return out


# ---------------------------------------------------------------------------
# The process-wide embedder of the setting
# ---------------------------------------------------------------------------

_STATE = {"override": None, "embedders": {}}
_STATE_LOCK = threading.Lock()


def model_spec(mode: Optional[str] = None) -> Optional[ModelSpec]:
    """The pinned model of an EMBEDDINGS value (the setting by default); None for 'off'."""
    return MODELS.get(mode if mode is not None else settings.embeddings_mode())


def enabled() -> bool:
    return settings.embeddings_mode() != "off"


def get_embedder(allow_download: bool = True):
    """The embedder of the EMBEDDINGS setting, or None when it is 'off'.

    One OnnxEmbedder per model and folder in the process (its session loads on the first embed()). allow_download
    False (the dashboard's approval path) never starts a download: a missing file is a skipped vector there, filled
    in later by scripts/backfill_embeddings.py. Tests install a fake with set_embedder().
    """
    mode = settings.embeddings_mode()
    if mode == "off":
        return None
    if _STATE["override"] is not None:
        return _STATE["override"]
    spec = MODELS[mode]
    folder = settings.embeddings_model_dir()
    key = (mode, folder, bool(allow_download))
    with _STATE_LOCK:
        embedder = _STATE["embedders"].get(key)
        if embedder is None:
            embedder = OnnxEmbedder(spec, folder, allow_download=allow_download)
            _STATE["embedders"][key] = embedder
    return embedder


def set_embedder(embedder) -> None:
    """Use this embedder while EMBEDDINGS is not 'off' (tests, a calibration run); None goes back to the real one."""
    _STATE["override"] = embedder


def reset() -> None:
    """Forget the fake and every loaded model (tests)."""
    with _STATE_LOCK:
        _STATE["override"] = None
        _STATE["embedders"].clear()
