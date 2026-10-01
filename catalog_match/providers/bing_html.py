"""Bing Images HTML scraper: the unsanctioned fallback (decisions D7, RC-3, RC-6).

Used only when no sanctioned search API is configured, or when it is configured,
ENABLE_BING_HTML_FALLBACK is on and the API returned error/quota for a query.
Its candidates are sanctioned=False, so they can never be auto-published.

Parsing (salvaged from the legacy image_search.bing_image_search / _fetch_bing,
with their bugs fixed). Each result is an <a class="iusc"> whose 'm' attribute is
HTML-escaped JSON:
    t    -> title and page_title   (the legacy code read 'desc' as the title)
    desc -> snippet
    purl -> page_url               (the legacy code dropped it)
    murl -> image_url
The title is NEVER defaulted to the query: it is '' when both t and desc are
missing. Width/height are None unless the result link carries real expw/exph
values (the legacy code invented 800x800).

A 200 page with no a.iusc and a captcha / consent / unusual-traffic marker is
'blocked', not 'empty', so a bot check is never reported as "no image found".
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, List, Optional
from urllib.parse import parse_qs, urlsplit

import requests
from bs4 import BeautifulSoup

from .. import settings
from ..models import Candidate, SkuSpec
from .base import BaseProvider, ProviderBlocked, ProviderHTTPError, page_domain, response_text, to_int

logger = logging.getLogger(__name__)

try:  # curl_cffi impersonates a real Chrome TLS fingerprint; plain requests is the fallback
    from curl_cffi import requests as _cffi_requests  # type: ignore
except Exception:  # pragma: no cover - depends on the environment
    _cffi_requests = None

BING_IMAGES_URL = "https://www.bing.com/images/search"
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
}
MAX_RESULTS = 30

_BLOCK_MARKERS = ("captcha", "unusual traffic", "consent")
_BLOCK_URL_RE = re.compile(r"captcha|challenge|consent", re.I)


class BingHtmlProvider(BaseProvider):
    name = "bing_html"
    sanctioned = False
    rate_per_min = 12.0
    burst = 2
    timeout = 10.0

    def __init__(self, fallback: bool = False, session: Any = None, bucket: Any = None,
                 timeout: Optional[float] = None, proxy_url: Optional[str] = None) -> None:
        super().__init__(session=session, bucket=bucket, timeout=timeout)
        self.fallback = bool(fallback)
        self._proxy_url = proxy_url

    def _get(self, params: dict, headers: dict):
        proxy = self._proxy_url if self._proxy_url is not None else settings.proxy_url()
        proxies = {"http": proxy, "https": proxy} if proxy else None
        if self._session is not None:
            return self._session.get(BING_IMAGES_URL, params=params, headers=headers,
                                     timeout=self.timeout, proxies=proxies)
        if _cffi_requests is not None:
            return _cffi_requests.get(BING_IMAGES_URL, params=params, headers=headers,
                                      timeout=self.timeout, proxies=proxies, impersonate="chrome")
        return requests.get(BING_IMAGES_URL, params=params, headers=headers,
                            timeout=self.timeout, proxies=proxies)

    def _search(self, query: str, hl: str, spec: SkuSpec) -> List[Candidate]:
        lang = "ar" if (hl or "").lower().startswith("ar") else "en"
        headers = dict(HEADERS)
        headers["Accept-Language"] = "ar-AE,ar;q=0.9,en;q=0.8" if lang == "ar" else "en-US,en;q=0.9"
        params = {"q": query, "form": "HDRSC2", "first": "1", "cc": "AE", "setlang": lang}
        resp = self._get(params, headers)
        if resp.status_code != 200:
            raise ProviderHTTPError(resp.status_code, response_text(resp))
        final_url = str(getattr(resp, "url", "") or "")
        return parse_html(response_text(resp), final_url=final_url)


def _dims_from_href(href: str):
    """expw/exph from the result link's query string (the real image size), else (None, None)."""
    if not href:
        return None, None
    try:
        qs = parse_qs(urlsplit(href).query)
    except ValueError:
        return None, None
    w = to_int((qs.get("expw") or [None])[0])
    h = to_int((qs.get("exph") or [None])[0])
    if w is None or h is None:
        return None, None
    return w, h


def is_block_page(html: str, final_url: str = "") -> bool:
    """True for a captcha / consent / bot-check page (only meaningful when no results parsed)."""
    if final_url:
        parts = urlsplit(final_url)
        if _BLOCK_URL_RE.search((parts.netloc or "") + (parts.path or "")):
            return True
    low = (html or "").lower()
    if any(marker in low for marker in _BLOCK_MARKERS):
        return True
    try:
        soup = BeautifulSoup(html or "", "html.parser")
    except Exception:  # pragma: no cover - defensive
        return False
    return soup.select_one("form#b_captcha") is not None


def parse_html(html: str, final_url: str = "") -> List[Candidate]:
    """Candidates from a Bing Images result page; raises ProviderBlocked for a bot-check page."""
    soup = BeautifulSoup(html or "", "html.parser")
    anchors = soup.select("a.iusc")
    if not anchors:
        if is_block_page(html, final_url):
            raise ProviderBlocked("captcha_or_consent_page")
        return []
    out: List[Candidate] = []
    for a in anchors:
        raw = a.get("m")
        if not raw:
            continue
        try:
            m = json.loads(raw)
        except (TypeError, ValueError):
            logger.debug("bing_html: unparsable m attribute skipped")
            continue
        if not isinstance(m, dict):
            continue
        image_url = str(m.get("murl") or "").strip()
        if not image_url.lower().startswith(("http://", "https://")):
            continue
        t = str(m.get("t") or "").strip()
        desc = str(m.get("desc") or "").strip()
        page_url = str(m.get("purl") or "").strip()
        width, height = _dims_from_href(a.get("href") or "")
        out.append(Candidate(
            image_url=image_url,
            page_url=page_url,
            page_title=t,
            title=t or desc,
            snippet=desc,
            domain=page_domain(page_url, "", image_url),
            width=width,
            height=height,
            rank=len(out) + 1,
            sanctioned=False,
        ))
        if len(out) >= MAX_RESULTS:
            break
    return out
