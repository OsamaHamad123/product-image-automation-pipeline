"""Download, decode and store candidate images (stage 'fetch').

HttpFetcher.fetch(cands, spec) -> list[FetchedImage]
    * takes the first 8 candidates (the caller passes them in pre-download rank order)
      and downloads them in parallel on a pool of 6 threads;
    * the client is curl_cffi with a Chrome TLS fingerprint when it is installed (retailer CDNs
      behind Akamai/Cloudflare, e.g. Lulu, stall plain Python clients until they time out),
      else `requests`;
    * the first attempt is always direct. One more attempt after a timeout, a 5xx or a connection
      error, through PROXY_URL when one is set (it is a fallback, never the only route: a slow
      proxy must not fail every download). A refusal (403/429, or a page instead of an image: the
      empty HTML Carrefour's Akamai sends any non-browser) goes through the proxy when one is set,
      up to PROXY_ATTEMPTS times: the proxy gives a new exit address per connection and some of its
      addresses are blocked too;
    * timeout 10 s per attempt;
    * body size window 3 KB .. 15 MB;
    * Accept 'image/avif,image/webp,image/png,image/jpeg;q=0.9,*/*;q=0.5' and
      Referer = candidate.page_url when there is one (many CDNs block hotlinks);
    * HTML, SVG and anything else PIL cannot decode is rejected as 'not_image';
    * the decoded image is EXIF-transposed, so width/height are what a viewer sees;
    * the raw bytes are stored content-addressed as <store>/<sha256>.<ext>;
    * phash is the 64-bit pHash of image_dedup_bktree.calculate_phash, as 16 hex digits.

The result list has one FetchedImage per attempted candidate, in input order.
Error codes: bad_url, timeout, connection_error, http_<status>, too_large,
too_small, not_image, decode_error, host_slow, blocked_url.

SSRF guard (net_guard): redirects are not left to the client. The URL and every redirect hop are checked before
they are requested (http(s) only, a host that resolves to public addresses only); a refused URL is 'blocked_url',
never retried and never counted by the slow-host breaker. A direct curl_cffi request is pinned to the addresses
that were checked (no second DNS answer). The hops of one attempt share its timeout.

Slow-host breaker (HostBreaker, one per process, shared by every worker thread): a host whose downloads
ended in 'timeout' or 'connection_error' twice within 15 minutes with no download of it coming back in between
(three times for a UAE retailer of data/trusted_domains.json) is skipped for the next 15 minutes, except for a
candidate whose page or image host is a UAE retailer, which is always downloaded (HostBreaker.exempt): its candidates come back at once as
'host_slow' (counted in the outcome's reject_counts as 'download:host_slow', like any download error)
instead of costing 10 s per attempt and a second attempt each, row after row. Other errors (a 403, a 404,
a page that is not an image) mean the host answered and never count. The breaker is off whenever a
cassette is installed: a recorded run must see every download, and a replay must not skip one the
recording answered. reset_host_breaker() clears it (tests, a fresh run).

load_image(fetched) re-opens a stored image for later stages (quality, verify).
"""

from __future__ import annotations

import hashlib
import io
import logging
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import requests
from PIL import Image, ImageOps, UnidentifiedImageError

try:  # browser TLS fingerprint; a hard dependency in requirements.txt, optional here
    from curl_cffi import requests as _curl_requests
except Exception:  # pragma: no cover - depends on the environment
    _curl_requests = None

import net_guard

from . import cassette, settings
from .models import Candidate, FetchedImage, SkuSpec
from .text_norm import domain_matches, url_host

logger = logging.getLogger(__name__)

ACCEPT = "image/avif,image/webp,image/png,image/jpeg;q=0.9,*/*;q=0.5"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
IMPERSONATE = "chrome"
MAX_FETCH = 8
WORKERS = 6
TIMEOUT_S = 10.0
MIN_BYTES = 3 * 1024
MAX_BYTES = 15 * 1024 * 1024
CHUNK = 64 * 1024

# slow-host breaker (HostBreaker)
HOST_SLOW = "host_slow"
HOST_FAILURES = ("timeout", "connection_error")      # the download errors that say the host did not answer
HOST_FAIL_LIMIT = 2                                   # failures within HOST_WINDOW_S that pause a host
HOST_FAIL_LIMIT_TRUSTED = 3                           # the same for a UAE retailer (data/trusted_domains.json)
HOST_WINDOW_S = 15 * 60
HOST_PAUSE_S = 15 * 60

_EXT = {
    "JPEG": "jpg", "MPO": "jpg", "PNG": "png", "WEBP": "webp", "GIF": "gif", "BMP": "bmp",
    "TIFF": "tif", "AVIF": "avif", "HEIF": "heic", "ICO": "ico",
}
_MARKUP_PREFIXES = (b"<",)     # HTML, XML and SVG bodies all start with '<'
_REPO_ROOT = Path(__file__).resolve().parent.parent


def request_headers(page_url: Optional[str] = None) -> dict:
    """The image request headers of this fetch (Accept with AVIF first; Referer = the candidate's page).
    The publish-time re-download (image_processor) sends the same, so a CDN that picks the format per request
    returns the bytes that were verified. The User-Agent is left to each client (it must match its TLS fingerprint)."""
    headers = {"Accept": ACCEPT, "Accept-Language": "en-US,en;q=0.9,ar;q=0.8"}
    if page_url:
        headers["Referer"] = page_url
    return headers


def _store_dir(explicit: Optional[str]) -> Path:
    raw = explicit if explicit else settings.candidate_store_dir()
    path = Path(raw)
    if not path.is_absolute():
        path = _REPO_ROOT / path     # independent of the caller's working directory
    return path


def _is_timeout(exc: BaseException) -> bool:
    text = f"{type(exc).__name__} {exc}".lower()
    return "timeout" in text or "timed out" in text


def _retryable(error: Optional[str]) -> bool:
    """Transient failures: the one extra attempt may succeed."""
    return error in ("timeout", "connection_error") or bool(error and error.startswith("http_5"))


# The source refused this client (a 403/429, or a page instead of an image): only a different route (the proxy) can
# help, and a refused proxy address is worth another try on a new one (up to PROXY_ATTEMPTS).
BLOCKED_ERRORS = ("http_403", "http_429", "not_image")
PROXY_ATTEMPTS = 3


def _blocked(error: Optional[str]) -> bool:
    """The source refused this client; only a different route (the proxy) can help."""
    return error in BLOCKED_ERRORS


def _markup(body: Optional[bytes], ctype: str) -> bool:
    """An HTML / SVG answer where an image was asked for (a bot challenge or an error page)."""
    if not body:
        return False
    head = body[:256].lstrip(b"\xef\xbb\xbf \t\r\n").lower()
    return "html" in (ctype or "") or "svg" in (ctype or "") or head.startswith(_MARKUP_PREFIXES)


def _trusted_retailer(host: str) -> bool:
    """A UAE retailer's page host, or the image CDN its own product pages use (uae_retailer_image_hosts)."""
    try:
        from .score import trusted_domains
        data = trusted_domains()
        hosts = list(data.get("uae_retailers") or []) + list(data.get("uae_retailer_image_hosts") or [])
        return domain_matches(host, hosts)
    except Exception:  # an unreadable list only means the stricter limit for everyone
        return False


class HostBreaker:
    """Per host: remembers downloads that timed out or lost the connection and pauses a host that keeps doing so.

    record_failure(host) after a download ended in a HOST_FAILURES error; blocked(host) before starting one.
    HOST_FAIL_LIMIT failures within HOST_WINDOW_S pause the host for HOST_PAUSE_S (HOST_FAIL_LIMIT_TRUSTED for a
    UAE retailer); when the pause is over the host starts again with a clean record. Thread-safe; the clock is
    injectable, so tests never sleep.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic,
                 trusted: Callable[[str], bool] = _trusted_retailer) -> None:
        self._clock = clock
        self._trusted = trusted
        self._lock = threading.Lock()
        self._failures: Dict[str, List[float]] = {}
        self._until: Dict[str, float] = {}

    def limit(self, host: str) -> int:
        return HOST_FAIL_LIMIT_TRUSTED if self._trusted(host) else HOST_FAIL_LIMIT

    def blocked(self, host: str) -> bool:
        if not host:
            return False
        with self._lock:
            until = self._until.get(host)
            if until is None:
                return False
            if self._clock() >= until:
                del self._until[host]
                return False
            return True

    def record_failure(self, host: str) -> bool:
        """Count one failed download of the host; True when it just paused the host."""
        if not host:
            return False
        limit = self.limit(host)
        with self._lock:
            now = self._clock()
            until = self._until.get(host)
            if until is not None and now < until:
                return False                     # already paused: a download that was in flight before it
            recent = [t for t in self._failures.get(host, ()) if now - t < HOST_WINDOW_S]
            recent.append(now)
            if len(recent) >= limit:
                self._failures.pop(host, None)
                self._until[host] = now + HOST_PAUSE_S
                logger.warning("fetch: %s failed %d downloads within %d min (timeout / connection error); "
                               "its downloads are skipped for %d min", host, len(recent), HOST_WINDOW_S // 60,
                               HOST_PAUSE_S // 60)
                return True
            self._failures[host] = recent
            return False

    def record_success(self, host: str) -> None:
        """A download of the host came back: its earlier failures were a passing hiccup, not a slow host."""
        if not host:
            return
        with self._lock:
            self._failures.pop(host, None)

    def exempt(self, *hosts: str) -> bool:
        """A candidate from a UAE retailer (its page or its image host) is never skipped: the right picture is most
        often there, and a CDN such as m.media-amazon.com or a retailer's image host is shared by every one of its
        listings, so pausing it would drop the store from the rest of the run."""
        return any(h and self._trusted(h) for h in hosts)

    def reset(self) -> None:
        with self._lock:
            self._failures.clear()
            self._until.clear()


_HOST_BREAKER = HostBreaker()


def host_breaker() -> HostBreaker:
    """The process-wide breaker every HttpFetcher uses unless it was given its own."""
    return _HOST_BREAKER


def reset_host_breaker() -> None:
    """Forget every host's failures and pauses (tests; the start of a fresh run)."""
    _HOST_BREAKER.reset()


class HttpFetcher:
    """Fetcher protocol implementation over plain `requests`."""

    def __init__(self, store_dir: Optional[str] = None, max_items: int = MAX_FETCH,
                 workers: int = WORKERS, timeout: float = TIMEOUT_S,
                 min_bytes: int = MIN_BYTES, max_bytes: int = MAX_BYTES,
                 session: Optional[requests.Session] = None, breaker: Optional[HostBreaker] = None):
        self.breaker = breaker
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
        return dict(request_headers(cand.page_url), **{"User-Agent": USER_AGENT})

    def _get(self, url: str, headers: dict, proxy: Optional[str] = None):
        # redirects are followed by net_guard, which checks the URL and every hop before requesting it (SSRF)
        kwargs = {"headers": dict(headers), "timeout": self.timeout, "stream": True, "allow_redirects": False}
        pin = False
        if self.session is not None:
            getter = self.session.get
        elif _curl_requests is not None:
            getter = _curl_requests.get
            kwargs["impersonate"] = IMPERSONATE
            kwargs["headers"].pop("User-Agent", None)   # the impersonated browser sends its own, matching the TLS fingerprint
            pin = not proxy                             # direct: curl connects to the checked addresses only
        else:
            getter = requests.get
        if proxy:
            kwargs["proxies"] = {"http": proxy, "https": proxy}
        deadline: List[float] = []

        def hop(hop_url: str, target: net_guard.Target):
            timeout = self.timeout
            if deadline:                                 # a redirect: the hops share the attempt's timeout
                timeout = deadline[0] - time.monotonic()
                if timeout <= 0:
                    raise TimeoutError(f"timed out after redirects: {hop_url[:120]}")
            else:
                deadline.append(time.monotonic() + self.timeout)
            pinned = net_guard.curl_resolve(target) if pin else {}
            return getter(hop_url, **dict(kwargs, timeout=timeout, **({"curl_options": pinned} if pinned else {})))

        return cassette.http("fetch", "GET", url, lambda: net_guard.follow(url, hop), headers=headers,
                             proxy=bool(proxy), stream=True, max_bytes=self.max_bytes)   # record / replay

    def _download(self, url: str, headers: dict, proxy: Optional[str] = None) -> Tuple[Optional[bytes], Optional[str], str]:
        """(body, error, content_type) for one attempt. Never raises."""
        try:
            resp = self._get(url, headers, proxy)
        except net_guard.BlockedURL as exc:
            logger.info("fetch: refused, %s", exc)
            return None, net_guard.BLOCKED, ""
        except Exception as exc:
            logger.debug("fetch %s%s: %s", url, " via proxy" if proxy else "", type(exc).__name__)
            return None, cassette.miss_code(exc) or ("timeout" if _is_timeout(exc) else "connection_error"), ""
        try:
            status = int(getattr(resp, "status_code", 0) or 0)
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
            except Exception as exc:
                return None, "timeout" if _is_timeout(exc) else "connection_error", ctype
            return bytes(buf), None, ctype
        finally:
            close = getattr(resp, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:  # pragma: no cover - best effort
                    pass

    def _breaker(self) -> Optional[HostBreaker]:
        """The slow-host breaker in use; None while a cassette is installed (it must see every download)."""
        return None if cassette.active() is not None else (self.breaker or _HOST_BREAKER)

    def _fetch_one(self, cand: Candidate) -> FetchedImage:
        url = (cand.image_url or "").strip()
        if not url.lower().startswith(("http://", "https://")):
            return FetchedImage(candidate=cand, ok=False, error="bad_url")
        breaker, host = self._breaker(), url_host(url)
        # a UAE retailer's candidate is always downloaded (HostBreaker.exempt); others skip a paused host
        skippable = breaker is not None and not breaker.exempt(host, url_host(cand.page_url or ""))
        if skippable and breaker.blocked(host):
            return FetchedImage(candidate=cand, ok=False, error=HOST_SLOW)
        headers = self._headers(cand)
        proxy = settings.proxy_url() or None
        body, error, ctype = self._attempt(url, headers)
        if body is None and (_retryable(error) or (proxy and _blocked(error))) \
                and not (skippable and error in HOST_FAILURES and breaker.blocked(host)):
            logger.debug("fetch %s: %s, retrying%s", url, error, " via proxy" if proxy else "")
            for _ in range(PROXY_ATTEMPTS if proxy and _blocked(error) else 1):
                body, error, ctype = self._attempt(url, headers, proxy)
                if body is not None or not (proxy and _blocked(error)):
                    break
        if body is None:
            if breaker is not None and error in HOST_FAILURES:
                breaker.record_failure(host)
            return FetchedImage(candidate=cand, ok=False, error=error or "error")
        if breaker is not None:
            breaker.record_success(host)
        return self._decode_and_store(cand, body, ctype)

    def _attempt(self, url: str, headers: dict, proxy: Optional[str] = None) -> Tuple[Optional[bytes], Optional[str], str]:
        """One download; a page instead of an image counts as a refusal (not_image) so the proxy can try it."""
        body, error, ctype = self._download(url, headers, proxy)
        if body is not None and _markup(body, ctype):
            return None, "not_image", ctype
        return body, error, ctype

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
