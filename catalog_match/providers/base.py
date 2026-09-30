"""BaseProvider: the shared error handling behind every image source (decision D7).

A provider subclass implements _search(query, hl, spec) and returns its candidates,
raising ProviderHTTPError for a non-200 response. BaseProvider.search():

* never raises: every exception becomes a ProviderResult with an explicit status;
* maps HTTP 429 to 'quota', 401/403 to 'error' with http_status, timeouts and
  other failures to 'error';
* returns 'ok' only when at least one candidate was parsed, else 'empty';
* takes one token from the provider's shared rate-limit bucket before the call;
* logs exactly one structured line per call (provider, status, count, latency).
"""

from __future__ import annotations

import logging
import time
from dataclasses import replace
from typing import Any, List, Optional

from ..models import Candidate, ProviderResult, SkuSpec
from .. import ratelimit
from ..text_norm import url_host
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

logger = logging.getLogger("catalog_match.providers")

STATUSES = ("ok", "empty", "error", "quota", "blocked")


class ProviderHTTPError(Exception):
    """A non-200 HTTP response; body is kept (truncated) for the log."""

    def __init__(self, status: int, body: str = "") -> None:
        super().__init__(f"http_{status}")
        self.status = int(status)
        self.body = (body or "")[:500]


class ProviderBlocked(Exception):
    """The source answered with a captcha / consent / bot-check page."""


class ProviderEmpty(Exception):
    """The source answered normally but has nothing for this request."""


def is_timeout(exc: BaseException) -> bool:
    name = type(exc).__name__.lower()
    return "timeout" in name or "timed out" in str(exc).lower()


def response_text(resp: Any) -> str:
    try:
        return resp.text or ""
    except Exception:  # pragma: no cover - defensive
        return ""


def to_int(value: Any) -> Optional[int]:
    """Positive int or None: providers must never invent dimensions."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


_AMAZON_IMAGE_HOSTS = ("media-amazon.com", "ssl-images-amazon.com", "images-amazon.com")
# /images/I/<id>.<modifiers>.<ext>: the modifiers resize the image and can draw overlays onto it
# (star rating, review count, "Amazon's Choice" badges), e.g. ._BO30,255,255,255_..._PIRIOFOUR-medium..._.jpg
_AMAZON_MODIFIED = re.compile(r"^(/images/[IG]/[^/.]+)\.[^/]+\.(jpe?g|png|gif|webp)$", re.I)
# Query parameters that only ask a retailer CDN for a smaller or re-encoded copy.
_RESIZE_PARAMS = {
    "nooncdn.com": {"width", "height", "format", "quality"},
    "mafrservices.com": {"im"},              # Carrefour UAE (Akamai Image Manager policies)
}


def canonical_image_url(url: str) -> str:
    """The original, unmodified image behind a retailer CDN URL (unknown hosts are returned unchanged)."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    host = (parts.hostname or "").lower()
    if any(host == h or host.endswith("." + h) for h in _AMAZON_IMAGE_HOSTS):
        m = _AMAZON_MODIFIED.match(parts.path)
        if m:
            return urlunsplit((parts.scheme, parts.netloc, f"{m.group(1)}.{m.group(2)}", "", ""))
        return url
    for suffix, params in _RESIZE_PARAMS.items():
        if host == suffix or host.endswith("." + suffix):
            kept = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k.lower() not in params]
            return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(kept), parts.fragment))
    return url


def page_domain(page_url: str, domain: str = "", image_url: str = "") -> str:
    """Page domain (no 'www.'): the provider-reported domain, else the page host, else the image host."""
    return url_host(domain) or url_host(page_url) or url_host(image_url)


class BaseProvider:
    """Implements models.Provider. Subclasses set name/sanctioned and implement _search()."""

    name: str = "base"
    sanctioned: bool = True
    kind: str = "search"          # 'search' (per query) | 'lookup' (once per SKU, e.g. GTIN)
    fallback: bool = False        # True: only used when the primary search providers are down
    rate_per_min: float = 60.0
    burst: int = 5
    timeout: float = 15.0

    def __init__(self, session: Any = None, bucket: Any = None, timeout: Optional[float] = None) -> None:
        self._session = session
        self._bucket = bucket
        if timeout is not None:
            self.timeout = float(timeout)

    # ------------------------------------------------------------------ hooks

    def _search(self, query: str, hl: str, spec: SkuSpec) -> List[Candidate]:  # pragma: no cover
        raise NotImplementedError

    def _status_for_http(self, status: int, body: str) -> str:
        """Map a non-200 HTTP status to a ProviderResult status."""
        if status == 429:
            return "quota"
        return "error"

    # --------------------------------------------------------------- helpers

    def bucket(self):
        if self._bucket is None:
            self._bucket = ratelimit.get_bucket(self.name, self.rate_per_min, self.burst)
        return self._bucket

    def _finish(self, cands: List[Candidate]) -> List[Candidate]:
        """Stamp provider identity on every candidate; a scraped source is never sanctioned."""
        out = []
        for c in cands:
            out.append(replace(c, image_url=canonical_image_url(c.image_url), provider=self.name,
                               sanctioned=bool(self.sanctioned and c.sanctioned)))
        return out

    # ----------------------------------------------------------------- public

    def search(self, query: str, hl: str, spec: SkuSpec) -> ProviderResult:
        http_status: Optional[int] = None
        error: Optional[str] = None
        cands: List[Candidate] = []
        body = ""
        start = time.monotonic()
        try:
            self.bucket().acquire()
            start = time.monotonic()  # latency is the source's, not our own rate-limit wait
            cands = self._finish(list(self._search(query, hl, spec) or []))
            status = "ok" if cands else "empty"
            http_status = 200
        except ProviderHTTPError as exc:
            http_status = exc.status
            body = exc.body
            status = self._status_for_http(exc.status, exc.body)
            error = f"http_{exc.status}"
            if status == "empty":
                error = None
        except ProviderBlocked as exc:
            status, error, http_status = "blocked", str(exc) or "blocked", 200
        except ProviderEmpty as exc:
            status, error = "empty", (str(exc) or None)
        except Exception as exc:  # never let one source break the search
            status = "error"
            error = "timeout" if is_timeout(exc) else f"{type(exc).__name__}: {exc}"[:300]
        latency_ms = int((time.monotonic() - start) * 1000)
        level = logging.INFO if status in ("ok", "empty") else logging.WARNING
        logger.log(
            level,
            "provider=%s status=%s count=%d latency_ms=%d http_status=%s error=%s query=%r%s",
            self.name, status, len(cands), latency_ms, http_status, error, query,
            f" body={body!r}" if body and status not in ("ok", "empty") else "",
        )
        return ProviderResult(
            provider=self.name,
            status=status,
            http_status=http_status,
            latency_ms=latency_ms,
            candidates=cands,
            error=error,
        )
