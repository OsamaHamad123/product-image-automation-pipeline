"""Open Food Facts API v2: a structured GTIN lookup, used only for a valid GTIN (D3, D7).

GET https://world.openfoodfacts.org/api/v2/product/{code}
    ?fields=code,product_name,brands,quantity,image_front_url
with an identifying User-Agent, rate-limited to 15 requests per minute.

A found product with a front image gives one sanctioned Candidate whose
gtin_on_page is the GTIN (the page IS the barcode record) and whose page title is
'{brands} {product_name} {quantity}'. status 0 (or HTTP 404) means 'empty'. An
invalid or missing GTIN makes no HTTP call.
"""

from __future__ import annotations

import logging
from typing import Any, List, Optional

import requests

from ..gtin import gtin13, is_restricted, normalize_gtin
from ..models import Candidate, ProviderResult, SkuSpec
from .base import BaseProvider, ProviderEmpty, ProviderHTTPError, response_text

logger = logging.getLogger(__name__)

OFF_PRODUCT_URL = "https://world.openfoodfacts.org/api/v2/product/{code}"
OFF_PAGE_URL = "https://world.openfoodfacts.org/product/{code}"
OFF_FIELDS = "code,product_name,brands,quantity,image_front_url"
USER_AGENT = "ProductImageAutomationPipeline/2.0 (catalog_match; grocery image lookup)"


class OffProvider(BaseProvider):
    name = "off"
    sanctioned = True
    kind = "lookup"
    rate_per_min = 15.0
    burst = 1
    timeout = 10.0

    def lookup(self, spec: SkuSpec) -> ProviderResult:
        """One GTIN lookup for the SKU; never raises."""
        return self.search("", "", spec)

    def _status_for_http(self, status: int, body: str) -> str:
        if status == 404:  # API v2 answers 404 {"status": 0, "status_verbose": "product not found"}
            return "empty"
        return super()._status_for_http(status, body)

    def search(self, query: str, hl: str, spec: SkuSpec) -> ProviderResult:
        gtin14 = _valid_gtin(spec)
        if gtin14 is None:
            logger.info("provider=off status=empty count=0 skipped=no_valid_gtin")
            return ProviderResult(provider=self.name, status="empty", error="no_valid_gtin")
        return super().search(query, hl, spec)

    def _search(self, query: str, hl: str, spec: SkuSpec) -> List[Candidate]:
        gtin14 = _valid_gtin(spec)
        if gtin14 is None:  # pragma: no cover - search() guards this
            return []
        code = gtin13(gtin14)
        http = self._session or requests
        resp = http.get(
            OFF_PRODUCT_URL.format(code=code),
            params={"fields": OFF_FIELDS},
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            timeout=self.timeout,
        )
        if resp.status_code != 200:
            raise ProviderHTTPError(resp.status_code, response_text(resp))
        return parse_product(resp.json(), gtin14)


def _valid_gtin(spec: SkuSpec) -> Optional[str]:
    if not spec.gtin:
        return None
    gtin14, status = normalize_gtin(spec.gtin)
    # In-store / restricted-circulation codes are not global: another product owns them on OFF.
    return gtin14 if status == "ok" and not is_restricted(gtin14) else None


def parse_product(data: Any, gtin14: str) -> List[Candidate]:
    """The front-image Candidate of an OFF product response, [] when there is none."""
    if not isinstance(data, dict):
        raise ValueError("Open Food Facts response is not a JSON object")
    product = data.get("product")
    if str(data.get("status", "")) != "1" or not isinstance(product, dict):
        raise ProviderEmpty(str(data.get("status_verbose") or "product not found"))
    image_url = str(product.get("image_front_url") or "").strip()
    if not image_url.lower().startswith(("http://", "https://")):
        raise ProviderEmpty("product has no front image")
    page_gtin, status = normalize_gtin(product.get("code") or data.get("code"))
    if status != "ok":
        page_gtin = gtin14
    title = " ".join(
        str(product.get(k) or "").strip() for k in ("brands", "product_name", "quantity")
        if str(product.get(k) or "").strip()
    )
    code = gtin13(page_gtin)
    return [Candidate(
        image_url=image_url,
        page_url=OFF_PAGE_URL.format(code=code),
        page_title=title,
        title=title,
        domain="openfoodfacts.org",
        width=None,
        height=None,
        rank=1,
        gtin_on_page=page_gtin,
        sanctioned=True,
    )]
