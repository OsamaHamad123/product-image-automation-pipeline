"""Serper.dev Google Shopping listings (sources package, P3).

POST https://google.serper.dev/shopping with the same key, header and gl='ae' as the
other Serper endpoints. Each listing keeps its evidence:
    title -> title, source (the store) -> snippet; link -> page_url (Google redirect links
    are unwrapped to the store page; a google.com product page is not a store page and is
    dropped); domain = the page's host, or for a listing without a store page, the domain of
    a known UAE store name; imageUrl -> image_url; position -> rank.

Shopping images are usually Google thumbnails (encrypted-tbn*.gstatic.com): such a
listing is followed through catalog_match.pages by the expansion round to read the
store page's full-size image. A listing whose imageUrl is a real, full-size image is a
direct candidate (sanctioned: the image came from the API).
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List
from urllib.parse import parse_qsl, urlsplit

from ..models import Candidate, SkuSpec
from ..text_norm import url_host
from .base import to_int
from .serper_web import SerperEndpoint

logger = logging.getLogger(__name__)

SERPER_SHOPPING_URL = "https://google.serper.dev/shopping"

# Store names Google Shopping shows -> the store's domain (only exact, known UAE stores).
STORE_DOMAINS: Dict[str, str] = {
    "carrefour": "carrefouruae.com", "carrefour uae": "carrefouruae.com", "carrefouruae.com": "carrefouruae.com",
    "lulu": "luluhypermarket.com", "lulu hypermarket": "luluhypermarket.com", "lulu uae": "luluhypermarket.com",
    "luluhypermarket.com": "luluhypermarket.com", "lulu hypermarket uae": "luluhypermarket.com",
    "noon": "noon.com", "noon.com": "noon.com", "noon uae": "noon.com",
    "amazon.ae": "amazon.ae", "amazon ae": "amazon.ae", "amazon uae": "amazon.ae",
    "talabat": "talabat.com", "talabat mart": "talabat.com", "talabat uae": "talabat.com",
    "kibsons": "kibsons.com", "spinneys": "spinneys.com", "spinneys uae": "spinneys.com",
    "union coop": "unioncoop.ae", "unioncoop": "unioncoop.ae", "choithrams": "choithrams.com",
}
_REDIRECT_PARAMS = ("url", "q", "adurl", "u")


def store_domain(source: str) -> str:
    """The domain of a known UAE store name ('Carrefour UAE', 'Amazon.ae - Seller'), else ''."""
    name = re.split(r"\s+[-–|]\s+", str(source or "").strip().lower(), maxsplit=1)[0]
    name = " ".join(re.sub(r"[^\w.\s]", " ", name).split())
    return STORE_DOMAINS.get(name, "")


def _is_google(host: str) -> bool:
    labels = host.split(".")
    return "google" in labels[:-1] or host.endswith("googleadservices.com")


def unwrap_link(link: str) -> str:
    """The store page behind a Google redirect link; '' for a google.com page with no target."""
    link = (link or "").strip()
    if not link.lower().startswith(("http://", "https://")):
        return ""
    host = url_host(link)
    if not _is_google(host):
        return link
    try:
        params = dict(parse_qsl(urlsplit(link).query, keep_blank_values=False))
    except ValueError:
        return ""
    for key in _REDIRECT_PARAMS:
        target = params.get(key, "")
        if target.lower().startswith(("http://", "https://")) and not _is_google(url_host(target)):
            return target
    return ""


class SerperShoppingProvider(SerperEndpoint):
    name = "serper_shopping"
    endpoint = SERPER_SHOPPING_URL

    def _payload(self, query: str, hl: str) -> dict:
        return {"q": query, "gl": self.gl, "hl": hl or "en", "num": self.num}

    def _search(self, query: str, hl: str, spec: SkuSpec) -> List[Candidate]:
        return parse_shopping(self._request(query, hl))


def parse_shopping(data: Any) -> List[Candidate]:
    """Listing candidates from a Serper /shopping response body (image_url may be a thumbnail)."""
    if not isinstance(data, dict):
        raise ValueError("Serper response is not a JSON object")
    out: List[Candidate] = []
    for i, item in enumerate(data.get("shopping") or [], start=1):
        if not isinstance(item, dict):
            continue
        title = " ".join(str(item.get("title") or "").split())
        if not title:
            continue
        source = " ".join(str(item.get("source") or "").split())
        page_url = unwrap_link(str(item.get("link") or ""))
        image_url = str(item.get("imageUrl") or "").strip()
        if not image_url.lower().startswith(("http://", "https://")):
            image_url = ""
        if not image_url and not page_url:
            continue
        # The page's own host when there is a page: a store NAME never overrides a URL ('Carrefour' on a
        # carrefourksa.com page is not carrefouruae.com, and the review page shows this domain).
        domain = url_host(page_url) if page_url else store_domain(source)
        out.append(Candidate(
            image_url=image_url,
            page_url=page_url,
            title=title,
            snippet=source,
            domain=domain,
            width=to_int(item.get("imageWidth")),
            height=to_int(item.get("imageHeight")),
            rank=to_int(item.get("position")) or i,
            sanctioned=True,
        ))
    return out
