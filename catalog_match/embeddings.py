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
    Both are the int8 ONNX exports of onnx-community (dynamic quantisation). Measured on the shared 4-core sandbox
    (load average 3-4), the brand look check of one SKU (8 downloaded pictures: decode, preprocess, one batch) takes
    0.28 s median (p90 0.33 s) with DINOv2-small int8 on 2 threads, against 1.1 s for the fp32 export; the first SKU
    of a process adds ~0.4-0.65 s (session load and the sha256 check). Brands with fewer than 3 approved pictures
    cost nothing (the model is not even loaded). The int8 vectors agree with fp32's (cosine median 0.98, p5 0.96 on
    159 real packshots) and separate brands as well (see Thresholds).

Uses (all evidence only; none picks, flips or rejects anything):
    BrandLook / annotate()   the brand look check of a SKU's downloaded candidates -> FetchedImage.look, read by
                             decide ('brand_look_mismatch': a review warning and an auto-publish blocker)
    remember_approval()      the vector of every approved picture (approved_embeddings), the check's references
    pair_similarity()        the cosine next to pHash, for a near-duplicate dedupe
    scripts/backfill_embeddings.py  vectors for the approvals made before, --setup, --calibrate

Thresholds (THRESHOLDS, per model; cosines of unit vectors)
    dinov2: same_max 0.45, other_min 0.70, margin 0.20, near_dup 0.90. Calibrated in the sandbox on 159 public front
    packshots of 13 brands (Open Food Facts, fetched at test time, never committed), int8, CLS token (it separated
    brands slightly better than CLS + mean of the patch tokens):
      * closest approved picture of the SAME brand (another product of it): median 0.70, p25 0.56, p5 0.37;
      * closest picture of ANOTHER brand: median 0.62, p95 0.79;
      * the same picture re-encoded / resized / re-cropped / padded / rotated: min 0.82, p5 0.86, median 0.96;
        two different products reach 0.90 in 0.15 % of the pairs (near_dup 0.90);
      * the rule (same < 0.45 and other >= 0.70 and other - same >= 0.20, with >= 3 approved pictures of the brand)
        warned on 2 of 159 right-brand pictures: one was the same Pringles photo filed under 'kelloggs' and
        'pringles' (a parent brand: real approvals file it under one sheet brand), one a real look-alike (Milka
        hazelnut next to Nutella). A pack of another brand was warned in 67 % of the trials when that very picture
        is approved under its own brand (stores reuse packshots), 28 % when only other products of it are.
    The synthetic tests (tests/catalog_match/test_cm_brand_look.py) pin the rule itself with the colour fake.
    siglip2: not calibrated; its cosines run higher for any two packshots, so its values are kept strict.
    Re-tuning once real approvals accumulate (a few hundred, several brands with 10+):
      1. python3 scripts/backfill_embeddings.py --apply (vectors for every approval), then --calibrate: it prints the
         closest same-brand and other-brand cosines of the approved pictures (leave one out) and how many of them
         the rule would flag; every flag there is a false warning. Keep that under ~1 %.
      2. The reviews tell the rest: the candidates' evidence carries 'look' (same, other, other_brand), so the
         stored candidates of approvals and of WRONG_BRAND rejections (review_decisions) give the cosines of right
         and wrong picks. Raise same_max / lower other_min while WRONG_BRAND rejections are still missed, lower
         same_max when right picks get the warning. Change THRESHOLDS here; a new model file (a new sha256) starts
         a new set of vectors (model_id), so re-run the backfill after changing the model, not after a threshold.

The model file is pinned: Hugging Face repository + commit + file + sha256 (ModelSpec). It is downloaded once into
settings.embeddings_model_dir() (install.sh does it ahead of time; otherwise the worker does it on first use),
through net_guard (every redirect hop is checked), checked against the sha256 before it is used, and never
committed. A file whose sha256 differs is never loaded.

Embedder interface: .model_id (the pinned file, stored next to every vector: vectors of two models never meet),
.dim, .embed(images) -> one unit vector (numpy float32) or None per picture. OnnxEmbedder loads the session lazily
on the first embed() and runs one inference at a time in the process (a semaphore: the worker searches several
products at once and each inference already uses the threads it is given, half the cores); pictures go through in
batches of BATCH_SIZE. get_embedder() is the process-wide embedder of the setting, or None.

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
from dataclasses import dataclass
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
        if not allow_download:
            raise ModelUnavailable(f"{path}: sha256 differs from the pinned one; not used")
        logger.warning("embeddings: %s does not match its pinned sha256; downloading it again", path)
        path = download_model(spec, model_dir)          # replaced atomically, and checked again while downloading
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
        # half the cores: the worker searches several products at once and may run rembg; on a busy 4-core machine
        # 2 threads embed 8 pictures in ~0.34 s where 4 threads (oversubscribed) take ~0.76 s
        self.threads = threads or max(1, min(4, (os.cpu_count() or 2) // 2))
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
    """Forget the fake and every loaded model, and the cached references (tests)."""
    with _STATE_LOCK:
        _STATE["override"] = None
        _STATE["embedders"].clear()
    set_reference_loader(None)


# ---------------------------------------------------------------------------
# Approved pictures: the references of the brand look check
# ---------------------------------------------------------------------------

# the approved pictures are read from the database at most this often per process (an approval made by this process
# clears the cache at once; the dashboard's approvals reach a running worker within this time)
REFERENCE_TTL_S = 600.0
# a failed read is retried after this long (meanwhile there are no references: no warning)
REFERENCE_RETRY_S = 60.0


def brand_key(text: Any) -> str:
    """The brand as approvals and searches both key it: the sheet's brand cell, folded (text_norm.match_key)."""
    from .text_norm import match_key
    return match_key(str(text or ""))


@dataclass
class Reference:
    sku_key: str
    brand_key: str
    brand: str
    vector: Any


class ReferenceSet:
    """The approved pictures of one model, grouped by brand key, as one matrix per brand."""

    def __init__(self, refs: Sequence[Reference] = ()):
        np = _np()
        groups: dict = {}
        for ref in refs or ():
            v = unit(ref.vector)
            if v is None or not ref.brand_key:
                continue
            groups.setdefault(ref.brand_key, ([], []))
            groups[ref.brand_key][0].append(v)
            groups[ref.brand_key][1].append(ref.brand or ref.brand_key)
        self._dims = {len(vs[0]) for vs, _ in groups.values()}
        self._brands = {k: (np.stack(vs), names) for k, (vs, names) in groups.items()}

    def __len__(self) -> int:
        return sum(len(names) for _, names in self._brands.values())

    def keys(self) -> List[str]:
        return list(self._brands)

    def count(self, keys) -> int:
        return sum(len(self._brands[k][1]) for k in keys if k in self._brands)

    def best(self, vector, keys) -> Tuple[Optional[float], str]:
        """(highest cosine to an approved picture of these brand keys, that picture's brand); (None, '') for none."""
        v = unit(vector)
        best, name = None, ""
        if v is None:
            return None, ""
        for k in keys:
            entry = self._brands.get(k)
            if entry is None or entry[0].shape[1] != v.shape[0]:
                continue
            sims = entry[0] @ v
            i = int(sims.argmax())
            if best is None or float(sims[i]) > best:
                best, name = float(sims[i]), entry[1][i]
        return best, name


def _db_references(model_id: str) -> List[Reference]:
    import local_cache_db

    return [Reference(sku_key=str(r.get("sku_key") or ""), brand_key=str(r.get("brand_key") or ""),
                      brand=str(r.get("brand") or ""), vector=from_blob(r.get("vector"), r.get("dim")))
            for r in local_cache_db.get_approved_embeddings(model_id)]


_REFS: dict = {"loader": None, "cache": {}}
_REFS_LOCK = threading.Lock()


def set_reference_loader(loader) -> None:
    """loader(model_id) -> [Reference] instead of the database (tests, a calibration run); None restores it. Clears
    the cache."""
    with _REFS_LOCK:
        _REFS["loader"] = loader
        _REFS["cache"].clear()


def clear_references() -> None:
    with _REFS_LOCK:
        _REFS["cache"].clear()


def references(model_id: str, clock=time.monotonic) -> ReferenceSet:
    """The approved pictures of this model (cached REFERENCE_TTL_S); an empty set when they cannot be read."""
    now = clock()
    with _REFS_LOCK:
        hit = _REFS["cache"].get(model_id)
        if hit is not None and hit[0] > now:
            return hit[1]
        loader = _REFS["loader"] or _db_references
    try:
        refs, ttl = ReferenceSet(loader(model_id)), REFERENCE_TTL_S
    except Exception as exc:
        logger.warning("embeddings: the approved pictures could not be read (%s); no brand look check for now",
                       type(exc).__name__)
        refs, ttl = ReferenceSet(), REFERENCE_RETRY_S
    with _REFS_LOCK:
        _REFS["cache"][model_id] = (now + ttl, refs)
    return refs


# ---------------------------------------------------------------------------
# The brand look check (evidence only: a review warning, never a decision)
# ---------------------------------------------------------------------------

# the check runs only for a brand with at least this many approved pictures (fewer cannot say how the brand looks)
MIN_BRAND_REFERENCES = 3
LOOK_MISMATCH = "brand_look_mismatch"


def _compact(key: str) -> str:
    return key.replace(" ", "")


def related_keys(a: str, b: str) -> bool:
    """Two brand keys that may name the same brand: equal without spaces, or one inside the other (4+ letters)
    ('al alali' / 'alali', 'sup t' / 'supt'). Such a brand is never 'another brand' for the check."""
    ca, cb = _compact(a), _compact(b)
    if not ca or not cb:
        return False
    if ca == cb:
        return True
    short, long_ = sorted((ca, cb), key=len)
    return len(short) >= 4 and short in long_


def spec_brand_keys(spec) -> Tuple[List[str], List[str]]:
    """(the SKU's own brand keys, the related keys that are never 'another brand'): own = the sheet brand, its
    mapped and discovered spellings; related adds the brand's phrases, required and sibling sub-brands."""
    own = {brand_key(b) for b in (getattr(spec, "brand_raw", ""), getattr(spec, "brand_canonical", ""),
                                  *(getattr(spec, "discovered_brands", ()) or ()))}
    related = set(own)
    for b in (*(getattr(spec, "match_brands", ()) or ()), *(getattr(spec, "required_brands", ()) or ()),
              *(getattr(spec, "sibling_brands", ()) or ())):
        related.add(brand_key(b))
    own.discard("")
    related.discard("")
    return sorted(own), sorted(related)


@dataclass
class LookVerdict:
    """What the vectors say about one candidate: its closest approved picture of its own brand (same, from n_same
    pictures) and of another brand (other, other_brand); mismatch per the model's Thresholds."""
    same: float
    other: Optional[float]
    other_brand: str
    n_same: int
    mismatch: bool
    model: str = ""

    def as_dict(self) -> dict:
        return {"same": round(self.same, 3), "other": None if self.other is None else round(self.other, 3),
                "other_brand": self.other_brand, "n_same": self.n_same, "mismatch": self.mismatch,
                "model": self.model}


def judge(vector, refs: ReferenceSet, own: Sequence[str], related: Sequence[str], thresholds: Thresholds,
          min_refs: int = MIN_BRAND_REFERENCES, model: str = "") -> Optional[LookVerdict]:
    """The brand look verdict of one picture, or None when it cannot be judged (no vector, too few approved
    pictures of the brand).

    mismatch: far from EVERY approved picture of its brand (same < same_max) AND close to an approved picture of
    another brand (other >= other_min) AND that brand clearly closer (other - same >= margin).
    """
    if vector is None:
        return None
    own_keys = [k for k in refs.keys() if any(related_keys(k, o) for o in own)]
    n_same = refs.count(own_keys)
    if n_same < min_refs:
        return None
    same, _ = refs.best(vector, own_keys)
    if same is None:
        return None
    others = [k for k in refs.keys() if k not in own_keys and not any(related_keys(k, r) for r in related)]
    other, other_brand = refs.best(vector, others)
    mismatch = (other is not None and same < thresholds.same_max and other >= thresholds.other_min
                and other - same >= thresholds.margin)
    return LookVerdict(same=same, other=other, other_brand=other_brand, n_same=n_same, mismatch=bool(mismatch),
                       model=model)


class BrandLook:
    """The brand look check of one SKU (for_spec): reads the approved pictures once, embeds candidates in batches."""

    def __init__(self, spec, embedder, refs: ReferenceSet, thresholds: Thresholds):
        self.spec = spec
        self.embedder = embedder
        self.refs = refs
        self.thresholds = thresholds
        self.own, self.related = spec_brand_keys(spec)

    @classmethod
    def for_spec(cls, spec) -> Optional["BrandLook"]:
        """None (no check, nothing embedded) when EMBEDDINGS is off, the SKU has no brand, or its brand has fewer
        than MIN_BRAND_REFERENCES approved pictures: the model is not even loaded for such a brand."""
        embedder = get_embedder()
        if embedder is None:
            return None
        look = cls(spec, embedder, references(embedder.model_id), _thresholds_of(embedder))
        if not look.own:
            return None
        own_keys = [k for k in look.refs.keys() if any(related_keys(k, o) for o in look.own)]
        if look.refs.count(own_keys) < MIN_BRAND_REFERENCES:
            return None
        return look

    def annotate(self, ranked) -> int:
        """Embed every downloaded candidate that has no vector yet (one batch) and write its verdict to
        fetched.look; returns how many pictures were embedded. Never changes a status, a reason or the order."""
        from .fetch import load_image

        todo = [rc.fetched for rc in ranked or ()
                if rc.fetched is not None and rc.fetched.ok and getattr(rc.fetched, "embedding", None) is None]
        if todo:
            vectors = self.embedder.embed([load_image(f) for f in todo])
            for fetched, vector in zip(todo, vectors):
                fetched.embedding = vector
        for rc in ranked or ():
            fetched = rc.fetched
            if fetched is None or not fetched.ok or getattr(fetched, "embedding", None) is None:
                continue
            verdict = judge(fetched.embedding, self.refs, self.own, self.related, self.thresholds,
                            model=self.embedder.model_id)
            fetched.look = verdict.as_dict() if verdict is not None else None
        return len(todo)


def _thresholds_of(embedder) -> Thresholds:
    key = str(getattr(embedder, "model_id", "") or "").split(":", 1)[0]
    return THRESHOLDS.get(key) or THRESHOLDS[settings.embeddings_mode() if enabled() else "dinov2"]


def annotate(spec, ranked) -> int:
    """The brand look evidence on a SKU's downloaded candidates (pipeline): how many pictures were embedded.

    0 and nothing touched when EMBEDDINGS is off or the brand has too few approved pictures. Never raises: a failure
    is logged and the candidates simply carry no verdict (no warning).
    """
    if not enabled():
        return 0
    try:
        look = BrandLook.for_spec(spec)
        return look.annotate(ranked) if look is not None else 0
    except Exception:
        logger.exception("embeddings: the brand look check failed for %s", getattr(spec, "sku_key", ""))
        return 0


def look_mismatch(fetched) -> bool:
    """The candidate's picture looks like another brand's approved pack (fetched.look, set by annotate)."""
    look = getattr(fetched, "look", None) if fetched is not None else None
    return bool(isinstance(look, dict) and look.get("mismatch"))


# ---------------------------------------------------------------------------
# Near duplicates: the cosine next to pHash (a helper for a dedupe; it never drops anything itself)
# ---------------------------------------------------------------------------

def ensure_vectors(fetched_list: Sequence[Any]) -> int:
    """Embed, in one batch, every downloaded picture of the list without a vector (fetched.embedding); how many.
    0 with EMBEDDINGS off or no embedder; never raises."""
    embedder = get_embedder()
    if embedder is None:
        return 0
    try:
        from .fetch import load_image

        todo = [f for f in fetched_list or () if f is not None and getattr(f, "ok", False)
                and getattr(f, "embedding", None) is None]
        if todo:
            for f, v in zip(todo, embedder.embed([load_image(f) for f in todo])):
                f.embedding = v
        return len(todo)
    except Exception:
        logger.exception("embeddings: the near-duplicate vectors failed")
        return 0


def near_duplicate_min(embedder=None) -> float:
    """The cosine at or above which two pictures are the same picture, for the model in use."""
    return _thresholds_of(embedder or get_embedder()).near_dup


def pair_similarity(a, b, embed_missing: bool = False) -> dict:
    """How alike two downloaded pictures (FetchedImage) are, for a dedupe that reports both signals:

        {'phash_distance': Hamming distance of the 64-bit pHashes (None when either is missing),
         'cosine': cosine of the embeddings (None with EMBEDDINGS off or without vectors),
         'near_duplicate': cosine >= the model's near_dup threshold (None without a cosine)}

    embed_missing: embed a picture that has no vector yet (one batch for both). The cosine is evidence next to pHash:
    the dedupe's own pHash rule stays what decides (pHash misses a re-cropped or re-coloured copy that the cosine
    sees, and the cosine calls two flavours of one pack design alike that pHash keeps apart).
    """
    from .fetch import phash_distance

    out = {"phash_distance": phash_distance(getattr(a, "phash", None), getattr(b, "phash", None)),
           "cosine": None, "near_duplicate": None}
    if embed_missing:
        ensure_vectors([a, b])
    c = cosine(getattr(a, "embedding", None), getattr(b, "embedding", None))
    if c is not None:
        out["cosine"] = round(c, 4)
        out["near_duplicate"] = c >= near_duplicate_min()
    return out


# ---------------------------------------------------------------------------
# Storing an approval's vector
# ---------------------------------------------------------------------------

def _decode(data: Optional[bytes]):
    if not data:
        return None
    import io
    from PIL import Image, ImageOps

    try:
        with Image.open(io.BytesIO(data)) as probe:
            probe.verify()
        img = Image.open(io.BytesIO(data))
        img.load()
        return ImageOps.exif_transpose(img)
    except Exception:
        return None


def approved_picture(sha256: Optional[str] = None, path: Optional[str] = None, url: Optional[str] = None,
                     page_url: Optional[str] = None, cloudinary_url: Optional[str] = None) -> Tuple[Any, str]:
    """(the approved picture, where it came from) or (None, ''): the verified candidate bytes in the candidate store
    (sha256), else a local file (an upload), else a download of the source URL, else of the Cloudinary copy. The
    downloads go through image_processor._download_bytes (http_client with net_guard: the SSRF guard)."""
    import image_processor

    if sha256:
        img = _decode(image_processor._load_from_candidate_store(sha256))
        if img is not None:
            return img, "candidate"
    if path:
        try:
            img = _decode(Path(path).read_bytes()) if Path(path).is_file() else None
        except OSError:
            img = None
        if img is not None:
            return img, "upload"
    for link, source in ((url, "original"), (cloudinary_url, "cloudinary")):
        if link and str(link).lower().startswith(("http://", "https://")):
            data, _error = image_processor._download_bytes(str(link), page_url if source == "original" else None)
            img = _decode(data)
            if img is not None:
                return img, source
    return None, ""


def remember_approval(*, sku_key: Optional[str], brand: Optional[str], cloudinary_url: Optional[str],
                      sha256: Optional[str] = None, path: Optional[str] = None, url: Optional[str] = None,
                      page_url: Optional[str] = None, allow_model_download: bool = False, embedder=None) -> bool:
    """Store the vector of a published approval (a reviewer's or the worker's AUTO_PUBLISH) in approved_embeddings.

    Called after the approval is written; it never raises and never changes the approval. False (nothing stored)
    when EMBEDDINGS is off, the approval has no SKU, brand or Cloudinary link, the picture cannot be read or the
    model is not ready (the dashboard never downloads it: allow_model_download False). scripts/backfill_embeddings.py
    fills in what was skipped.
    """
    if not enabled():
        return False
    key = brand_key(brand)
    if not (sku_key and key and cloudinary_url):
        return False
    try:
        embedder = embedder or get_embedder(allow_download=allow_model_download)
        if embedder is None or not getattr(embedder, "available", lambda: True)():
            return False                     # the model cannot run: no picture is read or downloaded for nothing
        img, source = approved_picture(sha256, path, url, page_url, cloudinary_url)
        if img is None:
            logger.info("embeddings: the approved picture of %s could not be read; the backfill will try again",
                        sku_key)
            return False
        vector = embedder.embed([img])[0]
        if vector is None:
            return False
        import local_cache_db

        local_cache_db.save_approved_embedding(embedder.model_id, sku_key, key, brand, cloudinary_url, source,
                                               to_blob(vector), len(vector),
                                               content_sha256=sha256 if source == "candidate" else None)
        clear_references()
        return True
    except Exception:
        logger.exception("embeddings: the vector of the approval of %s was not stored", sku_key)
        return False
