# verification_layer/use_cases/serp_schema_extractor.py
"""
SERP Product Schema & Microdata Extractor.
Parses schema.org/Product JSON-LD, evaluates CDN tier, and calculates Merchant Authority Score (MAS).
"""

import json
from urllib.parse import urlparse
from typing import Optional, Dict, Any


class SERPProductSchemaExtractor:
    """
    Extracts JSON-LD microdata and evaluates domain authority & CDN trustworthiness.
    """
    def __init__(self, domain_da_map: Optional[Dict[str, int]] = None):
        self.domain_da = domain_da_map or {
            "shopify.com": 96,
            "myshopify.com": 96,
            "cdn.shopify.com": 96,
            "magento.com": 90,
            "adobe.com": 97,
            "amazon.com": 96,
            "walmart.com": 92
        }

    def _extract_domain(self, url: str) -> str:
        try:
            netloc = urlparse(url).netloc
            clean = netloc.lower()
            if clean.startswith("www."):
                clean = clean[4:]
            return clean
        except Exception:
            return ""

    def evaluate_cdn_tier(self, image_url: str) -> float:
        """
        Evaluates CDN tier score: Shopify CDN = 1.0, Magento = 0.8, Generic = 0.3.
        """
        if "cdn.shopify.com" in image_url:
            return 1.0
        if "media/catalog/product" in image_url:
            return 0.8
        return 0.3

    def parse_json_ld_schema(self, json_ld_str: str) -> Dict[str, Any]:
        extracted = {
            "has_product_schema": False,
            "gtin": None,
            "brand": None,
            "price_currency": None,
            "completeness_score": 0.0
        }
        try:
            data = json.loads(json_ld_str)
            if isinstance(data, list):
                product_data = next((item for item in data if isinstance(item, dict) and item.get("@type") == "Product"), None)
            else:
                product_data = data if (isinstance(data, dict) and data.get("@type") == "Product") else None

            if product_data:
                extracted["has_product_schema"] = True
                extracted["gtin"] = product_data.get("gtin") or product_data.get("gtin13") or product_data.get("gtin8")

                brand_data = product_data.get("brand")
                if isinstance(brand_data, dict):
                    extracted["brand"] = brand_data.get("name")
                else:
                    extracted["brand"] = brand_data

                offers = product_data.get("offers")
                if isinstance(offers, dict):
                    extracted["price_currency"] = offers.get("priceCurrency")
                elif isinstance(offers, list) and len(offers) > 0:
                    extracted["price_currency"] = offers[0].get("priceCurrency")

                fields = ["gtin", "brand", "price_currency", "name", "image"]
                found_fields = sum(1 for field in fields if product_data.get(field) or extracted.get(field))
                extracted["completeness_score"] = round(found_fields / len(fields), 2)

        except Exception:
            pass
        return extracted

    def calculate_merchant_authority(self, page_url: str, image_url: str, schema_completeness: float) -> float:
        domain = self._extract_domain(page_url)
        
        # Match domain or parent domain (e.g. healthy-shop.myshopify.com -> shopify.com)
        da_score = 15
        for known_domain, score in self.domain_da.items():
            if domain == known_domain or domain.endswith("." + known_domain):
                da_score = score
                break

        da_factor = max(0.1, min(1.0, (da_score / 100.0)))
        cdn_factor = self.evaluate_cdn_tier(image_url)

        w_da, w_schema, w_cdn = 0.4, 0.4, 0.2
        mas = (w_da * da_factor) + (w_schema * schema_completeness) + (w_cdn * cdn_factor)
        return round(mas, 3)
