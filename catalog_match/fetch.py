"""Download, decode and store candidate images (stage 'fetch').

HttpFetcher.fetch(cands, spec) -> list[FetchedImage]
    * takes the first 8 candidates (the caller passes them in pre-download rank order)
      and downloads them in parallel on a pool of 6 threads with `requests`;
    * timeout 10 s; one retry on a timeout or a 5xx, no other retries;
    * body size window 3 KB .. 15 MB;
    * Accept 'image/avif,image/webp,image/png,image/jpeg;q=0.9,*/*;q=0.5' and
      Referer = candidate.page_url when there is one (many CDNs block hotlinks);
    * HTML, SVG and anything else PIL cannot decode is rejected as 'not_image';
    * the decoded image is EXIF-transposed, so width/height are what a viewer sees;
    * the raw bytes are stored content-addressed as <store>/<sha256>.<ext>;
    * phash is the 64-bit pHash of image_dedup_bktree.calculate_phash, as 16 hex digits.

The result list has one FetchedImage per attempted candidate, in input order.
Error codes: bad_url, timeout, connection_error, http_<status>, too_large,
too_small, not_image, decode_error.

load_image(fetched) re-opens a stored image for later stages (quality, verify).
"""

from __future__ import annotations

import hashlib
import io
import logging
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import List, Optional, Tuple

import requests
from PIL import Image, ImageOps, UnidentifiedImageError

from . import settings
from .models import Candidate, FetchedImage, SkuSpec

logger = logging.getLogger(__name__)

ACCEPT = "image/avif,image/webp,image/png,image/jpeg;q=0.9,*/*;q=0.5"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
MAX_FETCH = 8
WORKERS = 6
TIMEOUT_S = 10.0
MIN_BYTES = 3 * 1024
MAX_BYTES = 15 * 1024 * 1024
CHUNK = 64 * 1024

_EXT = {
    "JPEG": "jpg", "MPO": "jpg", "PNG": "png", "WEBP": "webp", "GIF": "gif", "BMP": "bmp",
    "TIFF": "tif", "AVIF": "avif", "HEIF": "heic", "ICO": "ico",
}
_MARKUP_PREFIXES = (b"<",)     # HTML, XML and SVG bodies all start with '<'
_REPO_ROOT = Path(__file__).resolve().parent.parent


def _store_dir(explicit: Optional[str]) -> Path:
    raw = explicit if explicit else settings.candidate_store_dir()
    path = Path(raw)
    if not path.is_absolute():
        path = _REPO_ROOT / path     # independent of the caller's working directory
    return path


class _Retry(Exception):
    """Internal: the attempt failed in a way that earns the single retry."""

    def __init__(self, error: str):
        super().__init__(error)
        self.error = error


class HttpFetcher:
    """Fetcher protocol implementation over plain `requests`."""

    def __init__(self, store_dir: Optional[str] = None, max_items: int = MAX_FETCH,
                 workers: int = WORKERS, timeout: float = TIMEOUT_S,
                 min_bytes: int = MIN_BYTES, max_bytes: int = MAX_BYTES,
                 session: Optional[requests.Session] = None):
        self.store_dir = store_dir
        self.max_items = max_items
        self.workers = workers
        self.timeout = timeout
        self.min_bytes = min_bytes
        self.max_bytes = max_bytes
        self.session = session

    # -- public ------------------------------------------------------------

    def fetch(self, cands: List[Candidate], spec: Optional[SkuSpec] = None) -> List[FetchedImage]:
        todo = list(cands or [])[: self.max_items]
        if not todo:
            return []
        with ThreadPoolExecutor(max_workers=max(1, min(self.workers, len(todo)))) as pool:
            results = list(pool.map(self._fetch_one, todo))
        ok = sum(1 for r in results if r.ok)
        logger.info("fetch: %d/%d candidate images downloaded", ok, len(results))
        return results

    # -- one candidate -------------------------------------------------------

    def _headers(self, cand: Candidate) -> dict:
        headers = {"Accept": ACCEPT, "User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9,ar;q=0.8"}
        if cand.page_url:
            headers["Referer"] = cand.page_url
        return headers

    def _get(self, url: str, headers: dict):
        getter = self.session.get if self.session is not None else requests.get
        proxy = settings.proxy_url()
        kwargs = {"headers": headers, "timeout": self.timeout, "stream": True, "allow_redirects": True}
        if proxy:
            kwargs["proxies"] = {"http": proxy, "https": proxy}
        return getter(url, **kwargs)

    def _download(self, url: str, headers: dict) -> Tuple[Optional[bytes], Optional[str], str]:
        """(body, error, content_type) for one attempt; raises _Retry for timeout / 5xx."""
        try:
            resp = self._get(url, headers)
        except requests.Timeout:
            raise _Retry("timeout")
        except requests.RequestException as exc:
            if "timed out" in str(exc).lower():
                raise _Retry("timeout")
            logger.debug("fetch %s: %s", url, exc)
            return None, "connection_error", ""
        try:
            status = int(getattr(resp, "status_code", 0) or 0)
            if status >= 500:
                raise _Retry(f"http_{status}")
            if status != 200:
                return None, f"http_{status}", ""
            headers_in = getattr(resp, "headers", None) or {}
            ctype = str(headers_in.get("Content-Type", "") or "").lower()
            length = headers_in.get("Content-Length")
            if length is not None:
                try:
                    if int(length) > self.max_bytes:
                        return None, "too_large", ctype
                except (TypeError, ValueError):
                    pass
            buf = bytearray()
            try:
                for chunk in resp.iter_content(chunk_size=CHUNK):
                    if not chunk:
                        continue
                    buf.extend(chunk)
                    if len(buf) > self.max_bytes:
                        return None, "too_large", ctype
            except requests.Timeout:
                raise _Retry("timeout")
            except requests.RequestException as exc:
                if "timed out" in str(exc).lower():
                    raise _Retry("timeout")
                return None, "connection_error", ctype
            return bytes(buf), None, ctype
        finally:
            close = getattr(resp, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:  # pragma: no cover - best effort
                    pass

    def _fetch_one(self, cand: Candidate) -> FetchedImage:
        url = (cand.image_url or "").strip()
        if not url.lower().startswith(("http://", "https://")):
            return FetchedImage(candidate=cand, ok=False, error="bad_url")
        headers = self._headers(cand)
        body: Optional[bytes] = None
        error: Optional[str] = None
        ctype = ""
        for attempt in (1, 2):
            try:
                body, error, ctype = self._download(url, headers)
                break
            except _Retry as retry:
                error = retry.error
                if attempt == 2:
                    body = None
                logger.debug("fetch %s: %s (attempt %d)", url, retry.error, attempt)
        if body is None:
            return FetchedImage(candidate=cand, ok=False, error=error or "error")
        return self._decode_and_store(cand, body, ctype)

    def _decode_and_store(self, cand: Candidate, body: bytes, ctype: str) -> FetchedImage:
        if len(body) < self.min_bytes:
            return FetchedImage(candidate=cand, ok=False, error="too_small")
        head = body[:256].lstrip(b"\xef\xbb\xbf \t\r\n").lower()
        if "html" in ctype or "svg" in ctype or head.startswith(_MARKUP_PREFIXES):
            return FetchedImage(candidate=cand, ok=False, error="not_image")
        try:
            with Image.open(io.BytesIO(body)) as probe:
                fmt = probe.format or ""
                probe.verify()
            img = Image.open(io.BytesIO(body))
            img.load()
            img = ImageOps.exif_transpose(img)
        except UnidentifiedImageError:
            return FetchedImage(candidate=cand, ok=False, error="not_image")
        except Image.DecompressionBombError:
            return FetchedImage(candidate=cand, ok=False, error="too_large")
        except Exception as exc:
            logger.debug("fetch %s: decode failed: %s", cand.image_url, exc)
            return FetchedImage(candidate=cand, ok=False, error="decode_error")

        sha = hashlib.sha256(body).hexdigest()
        stored = self._persist(sha, _EXT.get(fmt.upper(), "img"), body)
        width, height = img.size
        return FetchedImage(
            candidate=cand,
            ok=True,
            content_sha256=sha,
            width=width,
            height=height,
            path_or_bytes=stored if stored is not None else body,
            phash=phash_hex(img),
        )

    def _persist(self, sha: str, ext: str, body: bytes) -> Optional[str]:
        try:
            store = _store_dir(self.store_dir)
            store.mkdir(parents=True, exist_ok=True)
            path = store / f"{sha}.{ext}"
            if not path.exists():
                tmp = store / f".{sha}.{uuid.uuid4().hex}.tmp"
                tmp.write_bytes(body)
                try:
                    os.replace(tmp, path)
                except OSError:
                    # Another thread stored the same content first (Windows locks); keep theirs.
                    if tmp.exists():
                        tmp.unlink()
                    if not path.exists():
                        raise
            return str(path)
        except OSError as exc:
            logger.warning("fetch: cannot store candidate %s: %s", sha[:12], exc)
            return None


# ---------------------------------------------------------------------------
# Helpers shared with later stages
# ---------------------------------------------------------------------------

def phash_hex(img: Image.Image) -> Optional[str]:
    """pHash of image_dedup_bktree.calculate_phash as 16 hex digits, None when unavailable."""
    try:
        import image_dedup_bktree  # repo-root module; pure numpy/scipy
        value = int(image_dedup_bktree.calculate_phash(img))
    except Exception as exc:  # pragma: no cover - depends on the environment
        logger.debug("pHash unavailable: %s", exc)
        return None
    return f"{value:016x}" if value else None


def phash_distance(a: Optional[str], b: Optional[str]) -> Optional[int]:
    """Hamming distance of two hex pHashes, None when either is missing or malformed."""
    if not a or not b:
        return None
    try:
        return bin(int(str(a), 16) ^ int(str(b), 16)).count("1")
    except ValueError:
        return None


def load_image(fetched: FetchedImage) -> Optional[Image.Image]:
    """Open a fetched image (stored path or raw bytes), EXIF-transposed and fully loaded."""
    if fetched is None or not fetched.ok or fetched.path_or_bytes is None:
        return None
    src = fetched.path_or_bytes
    try:
        if isinstance(src, Image.Image):
            img = src
        else:
            # Read the bytes first: no file handle stays open (Windows cannot delete open files).
            data = bytes(src) if isinstance(src, (bytes, bytearray)) else Path(str(src)).read_bytes()
            img = Image.open(io.BytesIO(data))
        img.load()
        return ImageOps.exif_transpose(img)
    except Exception as exc:
        logger.warning("cannot re-open fetched image %s: %s", fetched.content_sha256, exc)
        return None
