"""Serper.dev Google Images: the primary, sanctioned image source (decision D7).

POST https://google.serper.dev/images with the X-API-KEY header and the JSON body
{q, gl: 'ae', hl, num: 20}. Google operators such as site: pass through in q.
Every image keeps the page evidence Google returned with it:
    imageUrl -> image_url, link -> page_url, title -> title and page_title,
    domain (or the link host) -> domain, imageWidth/imageHeight -> width/height,
    position -> rank.
"""

from __future__ import annotations

import logging
import re
from typing import Any, List, Optional

import requests

from .. import cassette, settings
from ..models import Candidate, SkuSpec
from .base import BaseProvider, ProviderHTTPError, page_domain, response_text, to_int

logger = logging.getLogger(__name__)

SERPER_IMAGES_URL = "https://google.serper.dev/images"

# Free Serper accounts answer 400 "Query pattern not allowed for free accounts" to site:/OR queries.
_SITE_CLAUSE = re.compile(r"\(?\s*site:\S+(?:\s+OR\s+site:\S+)*\s*\)?", re.I)


def has_site_operators(query: str) -> bool:
    return bool(re.search(r"(?i)\bsite:", query or ""))


def without_site_operators(query: str) -> str:
    """The query with its site:/OR clause replaced by a plain 'UAE' hint (still aimed at UAE retailer pages)."""
    plain = re.sub(r"\s+", " ", _SITE_CLAUSE.sub(" ", query or "")).strip()
    return plain if re.search(r"(?i)\buae\b", plain) else f"{plain} UAE".strip()


def _pattern_not_allowed(status: int, body: str) -> bool:
    return status == 400 and "not allowed" in (body or "").lower()


class SerperImagesProvider(BaseProvider):
    name = "serper"
    sanctioned = True
    rate_per_min = 120.0
    burst = 5
    timeout = 15.0
    # Learned once per process: this account refuses site: operators, so send the plain form directly.
    operators_blocked = False

    def __init__(self, api_key: Optional[str] = None, session: Any = None, bucket: Any = None,
                 timeout: Optional[float] = None, num: int = 20, gl: str = "ae") -> None:
        super().__init__(session=session, bucket=bucket, timeout=timeout)
        self._api_key = api_key
        self.num = int(num)
        self.gl = gl

    def api_key(self) -> str:
        return (self._api_key if self._api_key is not None else settings.serper_api_key()).strip()

    def _status_for_http(self, status: int, body: str) -> str:
        if status == 429:
            return "quota"
        # Serper answers 400 {"message": "Not enough credits"} when the balance is spent.
        if status in (400, 402) and ("credit" in body.lower() or "quota" in body.lower()):
            return "quota"
        return "error"

    def _post(self, key: str, query: str, hl: str):
        payload = {"q": query, "gl": self.gl, "hl": hl or "en", "num": self.num}
        http = self._session or requests
        return cassette.http(self.name, "POST", SERPER_IMAGES_URL, lambda: http.post(
            SERPER_IMAGES_URL,
            headers={"X-API-KEY": key, "Content-Type": "application/json"},
            json=payload,
            timeout=self.timeout,
        ), body=payload)

    def _search(self, query: str, hl: str, spec: SkuSpec) -> List[Candidate]:
        key = self.api_key()
        if not key:
            raise RuntimeError("no SERPER_API_KEY configured")
        if SerperImagesProvider.operators_blocked and has_site_operators(query):
            query = without_site_operators(query)
        resp = self._post(key, query, hl)
        if resp.status_code != 200 and has_site_operators(query) \
                and _pattern_not_allowed(resp.status_code, response_text(resp)):
            SerperImagesProvider.operators_blocked = True
            logger.warning("serper: this account does not allow site: operators (free plan); "
                           "retailer-scoped queries are sent without them from now on")
            query = without_site_operators(query)
            resp = self._post(key, query, hl)
        if resp.status_code != 200:
            raise ProviderHTTPError(resp.status_code, response_text(resp))
        data = resp.json()
        return parse_images(data)


def parse_images(data: Any) -> List[Candidate]:
    """Candidates from a Serper /images response body (dict)."""
    if not isinstance(data, dict):
        raise ValueError("Serper response is not a JSON object")
    images = data.get("images") or []
    out: List[Candidate] = []
    for i, item in enumerate(images, start=1):
        if not isinstance(item, dict):
            continue
        image_url = str(item.get("imageUrl") or "").strip()
        if not image_url.lower().startswith(("http://", "https://")):
            continue
        page_url = str(item.get("link") or "").strip()
        title = str(item.get("title") or "").strip()
        out.append(Candidate(
            image_url=image_url,
            page_url=page_url,
            page_title=title,
            title=title,
            domain=page_domain(page_url, str(item.get("domain") or ""), image_url),
            width=to_int(item.get("imageWidth")),
            height=to_int(item.get("imageHeight")),
            rank=to_int(item.get("position")) or i,
            sanctioned=True,
        ))
    return out
