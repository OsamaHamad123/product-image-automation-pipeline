"""Visual search: listings that show the same packshot as a seed image (sources package, P3).

Two backends behind one interface (VisualSearch.search(image_url, spec, query_id)):

    lens_serper   POST https://google.serper.dev/lens {"url": <image>, "gl": "ae", "hl": "en"}
                  with the Serper key. A plan that does not include Lens (HTTP 404/405, or
                  400/402/403 saying the endpoint or plan is not available) is learned once
                  per process, like serper.py's site: refusal, and not asked again.
    lens_serpapi  GET https://serpapi.com/search.json?engine=google_lens&url=...&api_key=...
                  ('visual_matches': title, link, source, thumbnail, image). SerpApi wants
                  the key in the query string, so every transport error is re-raised with
                  the key and any 'api_key=' value removed: the message ends up in
                  provider_health, the trace and the dashboard.

VISUAL_SEARCH: 'auto' tries Serper Lens, then SerpApi when SERPAPI_API_KEY is set (also
when Serper failed for this image); 'serper' / 'serpapi' use one backend; 'off' (and any
unknown value) disables visual search. build_visual_search() returns None when nothing
is configured, so tests and the offline eval never reach the network.

Every match keeps its page evidence (title, link -> page_url, source -> snippet, the
page host or a known store's domain -> domain, position -> rank). image_url is the
full-size image when the backend gives one, else its thumbnail: the expansion round
follows a thumbnail-only match through catalog_match.pages to read the full image.
Fields are parsed defensively; a match with neither an image nor a link is dropped.
"""

from __future__ import annotations

import logging
import re
from typing import Any, List, Optional

import requests

from .. import cassette, settings
from ..models import Candidate, ProviderResult, SkuSpec
from .base import BaseProvider, ProviderEmpty, ProviderHTTPError, page_domain, response_text, to_int
from .serper_shopping import store_domain, unwrap_link
from .serper_web import SerperEndpoint

logger = logging.getLogger(__name__)

SERPER_LENS_URL = "https://google.serper.dev/lens"
SERPAPI_URL = "https://serpapi.com/search.json"
MAX_MATCHES = 20
_UNSUPPORTED_WORDS = ("not allowed", "not available", "not supported", "unsupported", "upgrade", "plan",
                      "not enabled", "endpoint")
_QUOTA_WORDS = ("credit", "quota", "run out of searches", "searches per month", "limit")
_NO_RESULTS_WORDS = ("hasn't returned any results", "has not returned any results", "no results")


def lens_unsupported(status: int, body: str) -> bool:
    """True when the Serper account's plan does not include the Lens endpoint."""
    text = (body or "").lower()
    if any(w in text for w in ("credit", "quota")):
        return False
    if status in (404, 405):
        return True
    return status in (400, 402, 403) and any(w in text for w in _UNSUPPORTED_WORDS)


class SerperLensProvider(SerperEndpoint):
    name = "lens_serper"
    endpoint = SERPER_LENS_URL
    hedge = False            # visual search answers slowly by nature and costs more: never sent twice
    # Learned once per process: this Serper plan has no Lens endpoint.
    unsupported = False

    def _payload(self, query: str, hl: str) -> dict:
        return {"url": query, "gl": self.gl, "hl": hl or "en"}

    def _search(self, query: str, hl: str, spec: SkuSpec) -> List[Candidate]:
        key = self.api_key()
        if not key:
            raise RuntimeError("no SERPER_API_KEY configured")
        resp = self._post(key, self._payload(query, hl))
        if resp.status_code != 200:
            body = response_text(resp)
            if lens_unsupported(resp.status_code, body):
                if not SerperLensProvider.unsupported:
                    logger.warning("lens_serper: this Serper plan does not include Lens (HTTP %s); "
                                   "visual search uses SerpApi when its key is set", resp.status_code)
                SerperLensProvider.unsupported = True
            raise ProviderHTTPError(resp.status_code, body)
        return parse_matches(resp.json())


def _redact(text: str, key: str) -> str:
    text = str(text or "")
    if key:
        text = text.replace(key, "[REDACTED]")
    return re.sub(r"(?i)(api_?key=)[^&\s'\"]+", r"\1[REDACTED]", text)


class _RedactApiKey(logging.Filter):
    """Removes 'api_key=...' from HTTP client log records (urllib3 logs request URLs at DEBUG)."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # pragma: no cover - a malformed record is left to logging
            return True
        if "api_key=" in message.lower() or "apikey=" in message.lower():
            record.msg, record.args = _redact(message, ""), ()
        return True


_REDACT_FILTER = _RedactApiKey()


def _guard_http_logs() -> None:
    """Install the key filter on the HTTP client loggers once (DEBUG logging must not print the key)."""
    for name in ("urllib3.connectionpool", "urllib3", "requests"):
        log = logging.getLogger(name)
        if _REDACT_FILTER not in log.filters:
            log.addFilter(_REDACT_FILTER)


class SerpApiTransportError(Exception):
    """A SerpApi transport failure, with the key removed from the message."""


class SerpApiTimeout(SerpApiTransportError):
    pass


class SerpApiLensProvider(BaseProvider):
    name = "lens_serpapi"
    sanctioned = True
    kind = "expansion"
    rate_per_min = 30.0
    burst = 2
    timeout = 30.0

    def __init__(self, api_key: Optional[str] = None, session: Any = None, bucket: Any = None,
                 timeout: Optional[float] = None, country: str = "ae") -> None:
        super().__init__(session=session, bucket=bucket, timeout=timeout)
        self._api_key = api_key
        self.country = country
        _guard_http_logs()

    def api_key(self) -> str:
        return (self._api_key if self._api_key is not None else settings.serpapi_api_key()).strip()

    def _status_for_http(self, status: int, body: str) -> str:
        if status == 429 or any(w in (body or "").lower() for w in _QUOTA_WORDS):
            return "quota"
        return "error"

    def _search(self, query: str, hl: str, spec: SkuSpec) -> List[Candidate]:
        key = self.api_key()
        if not key:
            raise RuntimeError("no SERPAPI_API_KEY configured")
        params = {"engine": "google_lens", "url": query, "hl": hl or "en", "country": self.country,
                  "api_key": key}
        http = self._session or requests
        try:
            resp = cassette.http(self.name, "GET", SERPAPI_URL,
                                 lambda: http.get(SERPAPI_URL, params=params, timeout=self.timeout), params=params)
        except Exception as exc:
            text = f"{type(exc).__name__}: {exc}"
            timed_out = "timeout" in text.lower() or "timed out" in text.lower()
            cls = SerpApiTimeout if timed_out else SerpApiTransportError
            raise cls(_redact(text, key)[:300]) from None
        body = _redact(response_text(resp), key)
        if resp.status_code != 200:
            raise ProviderHTTPError(resp.status_code, body)
        try:
            data = resp.json()
        except ValueError:
            raise SerpApiTransportError("SerpApi answered with a body that is not JSON") from None
        if isinstance(data, dict) and data.get("error"):
            message = _redact(str(data.get("error")), key).lower()
            if any(w in message for w in _NO_RESULTS_WORDS):
                raise ProviderEmpty("no visual matches")
            raise ProviderHTTPError(429 if any(w in message for w in _QUOTA_WORDS) else 400, message)
        return parse_matches(data)


def _first_url(item: dict, keys) -> str:
    for key in keys:
        value = item.get(key)
        if isinstance(value, str) and value.strip().lower().startswith(("http://", "https://")):
            return value.strip()
    return ""


def parse_matches(data: Any) -> List[Candidate]:
    """Match candidates from a Serper /lens or SerpApi google_lens response body."""
    if not isinstance(data, dict):
        raise ValueError("visual search response is not a JSON object")
    items = None
    for key in ("visual_matches", "visualMatches", "organic", "matches", "images", "results"):
        if isinstance(data.get(key), list):
            items = data[key]
            break
    out: List[Candidate] = []
    for i, item in enumerate(items or [], start=1):
        if len(out) >= MAX_MATCHES:
            break
        if not isinstance(item, dict):
            continue
        title = " ".join(str(item.get("title") or "").split())
        link = unwrap_link(str(item.get("link") or item.get("url") or ""))
        image = _first_url(item, ("image", "imageUrl", "imageURL", "original", "image_url"))
        thumb = _first_url(item, ("thumbnail", "thumbnailUrl", "thumbnailURL"))
        if not title or not (image or thumb or link):
            continue
        source = item.get("source")
        source = " ".join(str(source.get("name") if isinstance(source, dict) else source or "").split())
        width = to_int(item.get("image_width") or item.get("imageWidth")) if image else \
            to_int(item.get("thumbnail_width") or item.get("thumbnailWidth"))
        height = to_int(item.get("image_height") or item.get("imageHeight")) if image else \
            to_int(item.get("thumbnail_height") or item.get("thumbnailHeight"))
        out.append(Candidate(
            image_url=image or thumb,
            page_url=link,
            title=title,
            snippet=source,
            domain=page_domain(link, str(item.get("domain") or ""), "") if link else store_domain(source),
            width=width,
            height=height,
            rank=to_int(item.get("position")) or i,
            sanctioned=True,
        ))
    return out


class VisualSearch:
    """One interface over the configured backends, tried in order for each seed image."""

    def __init__(self, backends: List[BaseProvider], mode: str = "auto") -> None:
        self.backends = list(backends)
        self.mode = mode

    @property
    def names(self) -> List[str]:
        return [b.name for b in self.backends]

    def _usable(self, backend) -> bool:
        return not (isinstance(backend, SerperLensProvider) and SerperLensProvider.unsupported)

    def available(self) -> bool:
        return any(self._usable(b) for b in self.backends)

    def next_backend(self) -> Optional[str]:
        """The backend the next search would try first (None when none is usable)."""
        return next((b.name for b in self.backends if self._usable(b)), None)

    def search(self, image_url: str, spec: SkuSpec, query_id: str = "", max_calls: int = 2) -> List[ProviderResult]:
        """Results of the backends asked for this seed: the first that answers ends the chain."""
        results: List[ProviderResult] = []
        for backend in self.backends:
            if len(results) >= max(0, int(max_calls)):
                break
            if not self._usable(backend):
                continue
            res = backend.search(image_url, "en", spec)
            res.query_id = query_id
            results.append(res)
            if res.status in ("ok", "empty"):
                break
        return results


def build_visual_search(mode: Optional[str] = None, serper_key: Optional[str] = None,
                        serpapi_key: Optional[str] = None) -> Optional[VisualSearch]:
    """The configured visual search, or None (VISUAL_SEARCH off, or no key for the chosen backend)."""
    mode = (mode or settings.visual_search_mode()).strip().lower()
    serper_key = settings.serper_api_key() if serper_key is None else serper_key.strip()
    serpapi_key = settings.serpapi_api_key() if serpapi_key is None else serpapi_key.strip()
    backends: List[BaseProvider] = []
    if mode in ("auto", "serper") and serper_key:
        backends.append(SerperLensProvider(api_key=serper_key))
    if mode in ("auto", "serpapi") and serpapi_key:
        backends.append(SerpApiLensProvider(api_key=serpapi_key))
    if not backends:
        return None
    logger.info("visual search (%s): %s", mode, ", ".join(b.name for b in backends))
    return VisualSearch(backends, mode)


def reset_run_state() -> None:
    """Forget what was learned about the Serper plan (tests; a worker restart does the same)."""
    SerperLensProvider.unsupported = False
