"""Legacy Google Custom Search (image) adapter, kept only for an existing key (D7, SRC-11).

Constructed only when a key and a cx are configured and today <= CSE_SUNSET_DATE
(Google closes the JSON API to existing customers on 2027-01-01). Request params are
{q, cx, key, searchType: 'image', num: 10, gl: 'ae', cr: 'countryAE', hl}; the
legacy imgSize=xxlarge and fileType filters are gone. Every non-200 response is
logged with its body. On 429/403/400 the next configured key is tried.

Mapping: link -> image_url, image.contextLink -> page_url, title -> title and
page_title, snippet -> snippet, displayLink -> domain, image.width/height.
"""

from __future__ import annotations

import datetime as _dt
import logging
from typing import Any, List, Optional, Sequence

import requests

from .. import cassette, settings
from ..models import Candidate, SkuSpec
from .base import BaseProvider, ProviderHTTPError, page_domain, response_text, to_int

logger = logging.getLogger(__name__)

CSE_URL = "https://www.googleapis.com/customsearch/v1"
_ROTATE_ON = (400, 403, 429)


def cse_allowed(keys: Sequence[str], cxs: Sequence[str], today: Optional[_dt.date] = None,
                sunset: Optional[_dt.date] = None) -> bool:
    """True when a key and a cx exist and today is on or before the sunset date."""
    keys = [k for k in (keys or []) if str(k).strip()]
    cxs = [c for c in (cxs or []) if str(c).strip()]
    if not keys or not cxs:
        return False
    today = today or _dt.date.today()
    sunset = sunset or settings.cse_sunset_date()
    return today <= sunset


class CseLegacyProvider(BaseProvider):
    name = "cse_legacy"
    sanctioned = True
    rate_per_min = 60.0
    burst = 3
    timeout = 10.0
    # Set once per process when every key is refused with 403 (Custom Search API not enabled for the
    # project, or the key restricted): create() then returns None, so later SKUs stop paying the round trip.
    disabled_reason: Optional[str] = None

    def __init__(self, api_keys: Sequence[str], cx_list: Sequence[str], today: Optional[_dt.date] = None,
                 sunset: Optional[_dt.date] = None, session: Any = None, bucket: Any = None,
                 timeout: Optional[float] = None) -> None:
        if not cse_allowed(api_keys, cx_list, today, sunset):
            raise ValueError("Google CSE needs a key and a cx, and is disabled after CSE_SUNSET_DATE")
        super().__init__(session=session, bucket=bucket, timeout=timeout)
        self.api_keys = [str(k).strip() for k in api_keys if str(k).strip()]
        self.cx_list = [str(c).strip() for c in cx_list if str(c).strip()]
        self.sunset = sunset or settings.cse_sunset_date()
        self._today = today

    @classmethod
    def create(cls, api_keys: Optional[Sequence[str]] = None, cx_list: Optional[Sequence[str]] = None,
               today: Optional[_dt.date] = None, sunset: Optional[_dt.date] = None,
               **kwargs: Any) -> Optional["CseLegacyProvider"]:
        """The provider, or None when no key is configured or the sunset date has passed."""
        keys = settings.google_search_api_keys() if api_keys is None else list(api_keys)
        cxs = settings.google_search_cx_list() if cx_list is None else list(cx_list)
        if not cse_allowed(keys, cxs, today, sunset) or cls.disabled_reason:
            return None
        return cls(keys, cxs, today=today, sunset=sunset, **kwargs)

    def params_for(self, query: str, hl: str, key: str, cx: str) -> dict:
        return {
            "q": query,
            "cx": cx,
            "key": key,
            "searchType": "image",
            "num": 10,
            "gl": "ae",
            "cr": "countryAE",
            "hl": hl or "en",
        }

    def _search(self, query: str, hl: str, spec: SkuSpec) -> List[Candidate]:
        if (self._today or _dt.date.today()) > self.sunset:  # a long-running worker crossed the date
            raise RuntimeError("Google CSE is past CSE_SUNSET_DATE")
        http = self._session or requests
        last: Optional[ProviderHTTPError] = None
        statuses: List[int] = []
        for idx, key in enumerate(self.api_keys):
            cx = self.cx_list[idx] if idx < len(self.cx_list) else self.cx_list[0]
            params = self.params_for(query, hl, key, cx)   # the cassette stores them without key and cx
            resp = cassette.http(self.name, "GET", CSE_URL,
                                 lambda: http.get(CSE_URL, params=params, timeout=self.timeout), params=params)
            if resp.status_code == 200:
                return parse_items(resp.json())
            body = response_text(resp)
            logger.warning("cse_legacy: key #%d http_status=%s body=%r", idx, resp.status_code, body[:500])
            last = ProviderHTTPError(resp.status_code, body)
            statuses.append(resp.status_code)
            if resp.status_code not in _ROTATE_ON:
                break
        if statuses and len(statuses) == len(self.api_keys) and all(s == 403 for s in statuses):
            CseLegacyProvider.disabled_reason = "http_403"
            logger.warning("cse_legacy: every key was refused with 403 (Custom Search API not enabled for the "
                           "project, or the key is restricted); Google CSE is skipped for the rest of this run")
        raise last or ProviderHTTPError(0, "no key tried")


def parse_items(data: Any) -> List[Candidate]:
    """Candidates from a CSE image-search response body (dict)."""
    if not isinstance(data, dict):
        raise ValueError("CSE response is not a JSON object")
    out: List[Candidate] = []
    for i, item in enumerate(data.get("items") or [], start=1):
        if not isinstance(item, dict):
            continue
        image_url = str(item.get("link") or "").strip()
        if not image_url.lower().startswith(("http://", "https://")):
            continue
        image = item.get("image") if isinstance(item.get("image"), dict) else {}
        page_url = str(image.get("contextLink") or "").strip()
        title = str(item.get("title") or "").strip()
        out.append(Candidate(
            image_url=image_url,
            page_url=page_url,
            page_title=title,
            title=title,
            snippet=str(item.get("snippet") or "").strip(),
            domain=page_domain(page_url, str(item.get("displayLink") or ""), image_url),
            width=to_int(image.get("width")),
            height=to_int(image.get("height")),
            rank=i,
            sanctioned=True,
        ))
    return out
