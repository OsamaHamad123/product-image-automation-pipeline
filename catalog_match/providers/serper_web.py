"""Serper.dev Google web search: UAE retailer product pages (sources package, P3).

POST https://google.serper.dev/search with the same X-API-KEY header and gl='ae' as the
images provider (providers/serper.py). The query is the SKU's own Q1 text (brand +
written-out product name + size) scoped with site: OR over a small group of UAE retailer
domains and the brand's official domains. An account that refuses site: operators is
handled exactly like serper.py: the refusal is learned once and remembered across processes
(serper.site_operators_blocked, shared with the images provider: it is the same account) and the plain form ('... UAE') is sent instead.

The results are PAGE LINKS, not images: every organic result becomes a Candidate with
image_url '' and the page evidence Google returned (link -> page_url, title -> title,
snippet -> snippet, domain, position -> rank). catalog_match.expand follows the links
through catalog_match.pages to read the product's image, name and GTIN. A result
thumbnail (organic imageUrl) is never used as an image.

The provider is not a retrieval provider (kind 'expansion'): Retriever never sends it
the planned queries.
"""

from __future__ import annotations

import logging
from typing import Any, List, Optional, Sequence

import requests

from .. import cassette, settings
from ..models import Candidate, SkuSpec
from .base import BaseProvider, ProviderHTTPError, page_domain, response_text, to_int
from .serper import (_pattern_not_allowed, has_site_operators, hedged_http, remember_site_operators_blocked,
                     site_operators_blocked, without_site_operators)

logger = logging.getLogger(__name__)

SERPER_SEARCH_URL = "https://google.serper.dev/search"
MAX_RESULTS = 10


def site_query(text: str, domains: Sequence[str]) -> str:
    """'<text> (site:a OR site:b ...)'; the text alone when there is no domain."""
    sites = [d.strip().lower() for d in domains if d and d.strip()]
    sites = list(dict.fromkeys(sites))
    if not sites:
        return " ".join((text or "").split())
    return f"{' '.join((text or '').split())} (" + " OR ".join(f"site:{d}" for d in sites) + ")"


class SerperEndpoint(BaseProvider):
    """Shared request / error handling of the Serper endpoints the expansion round uses."""

    name = "serper_endpoint"
    sanctioned = True
    kind = "expansion"
    endpoint = ""
    rate_per_min = 120.0
    burst = 5
    timeout = 10.0

    def __init__(self, api_key: Optional[str] = None, session: Any = None, bucket: Any = None,
                 timeout: Optional[float] = None, num: int = MAX_RESULTS, gl: str = "ae") -> None:
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

    def _payload(self, query: str, hl: str) -> dict:
        return {"q": query, "gl": self.gl, "hl": hl or "en", "num": self.num}

    def _post(self, key: str, payload: dict):
        http = self._session or requests
        return hedged_http(self, self.endpoint, payload, lambda: http.post(
            self.endpoint,
            headers={"X-API-KEY": key, "Content-Type": "application/json"},
            json=payload,
            timeout=self.timeout,
        ))

    def _request(self, query: str, hl: str) -> Any:
        """The decoded JSON body; site: refusals are retried once in the plain form."""
        key = self.api_key()
        if not key:
            raise RuntimeError("no SERPER_API_KEY configured")
        if has_site_operators(query) and site_operators_blocked():
            query = without_site_operators(query)
        resp = self._post(key, self._payload(query, hl))
        if resp.status_code != 200 and has_site_operators(query) \
                and _pattern_not_allowed(resp.status_code, response_text(resp)):
            remember_site_operators_blocked()
            logger.warning("%s: this account does not allow site: operators (free plan); "
                           "retailer-scoped queries are sent without them from now on", self.name)
            resp = self._post(key, self._payload(without_site_operators(query), hl))
        if resp.status_code != 200:
            raise ProviderHTTPError(resp.status_code, response_text(resp))
        return resp.json()


class SerperWebProvider(SerperEndpoint):
    name = "serper_web"
    endpoint = SERPER_SEARCH_URL

    def _search(self, query: str, hl: str, spec: SkuSpec) -> List[Candidate]:
        return parse_organic(self._request(query, hl))


def parse_organic(data: Any) -> List[Candidate]:
    """Page-link candidates (image_url '') from a Serper /search response body."""
    if not isinstance(data, dict):
        raise ValueError("Serper response is not a JSON object")
    out: List[Candidate] = []
    for i, item in enumerate(data.get("organic") or [], start=1):
        if not isinstance(item, dict):
            continue
        link = str(item.get("link") or "").strip()
        if not link.lower().startswith(("http://", "https://")):
            continue
        title = " ".join(str(item.get("title") or "").split())
        out.append(Candidate(
            image_url="",
            page_url=link,
            title=title,
            snippet=" ".join(str(item.get("snippet") or "").split())[:300],
            domain=page_domain(link, str(item.get("domain") or "")),
            rank=to_int(item.get("position")) or i,
            sanctioned=True,
        ))
    return out
