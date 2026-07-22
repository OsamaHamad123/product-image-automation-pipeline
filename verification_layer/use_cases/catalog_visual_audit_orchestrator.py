# verification_layer/use_cases/catalog_visual_audit_orchestrator.py
"""
Catalog Visual Audit Orchestrator Use Case.
Combines Search Intent Resolution, Byte-Range Image Parsing, Aspect Ratio Filtering,
SERP JSON-LD Schema Extraction, Merchant Authority Scoring, and Adaptive Proxy Management.
"""

import time
from typing import Dict, Any, Optional

from verification_layer.domain.nextgen_models import ProductVisualProfile
from verification_layer.use_cases.search_intent_resolver import SearchIntentResolverUseCase
from verification_layer.use_cases.serp_schema_extractor import SERPProductSchemaExtractor
from verification_layer.use_cases.byte_range_image_parser import ByteRangeImageParser, AspectRatioFilterUseCase
from verification_layer.infrastructure.adaptive_proxy_pool import AdaptiveProxyPool


class CatalogVisualAuditOrchestrator:
    """
    Central orchestrator for catalog search & visual matching under Clean Architecture.
    """
    def __init__(
        self,
        intent_resolver: Optional[SearchIntentResolverUseCase] = None,
        schema_extractor: Optional[SERPProductSchemaExtractor] = None,
        aspect_filter: Optional[AspectRatioFilterUseCase] = None,
        proxy_pool: Optional[AdaptiveProxyPool] = None
    ):
        self.intent_resolver = intent_resolver if intent_resolver else SearchIntentResolverUseCase()
        self.schema_extractor = schema_extractor if schema_extractor else SERPProductSchemaExtractor()
        self.aspect_filter = aspect_filter if aspect_filter else AspectRatioFilterUseCase()
        self.proxy_pool = proxy_pool

        self.metrics = {
            "audits_attempted": 0,
            "audits_passed": 0,
            "audits_failed": 0,
            "bandwidth_saved_bytes": 0,
            "total_execution_time": 0.0
        }

    def audit_serp_product_image(
        self,
        user_query_text: str,
        page_url: str,
        image_url: str,
        pre_fetched_bytes: bytes,
        json_ld_schema_str: Optional[str] = None
    ) -> Dict[str, Any]:
        start_time = time.time()
        self.metrics["audits_attempted"] += 1

        # 1. Resolve text search intent
        query = self.intent_resolver.resolve_query_intent(user_query_text)

        # 2. Parse image binary header dimensions
        dimensions = ByteRangeImageParser.get_dimensions(pre_fetched_bytes)
        if not dimensions:
            self.metrics["audits_failed"] += 1
            return {"decision": "REJECT", "reason": "Failed to parse image binary headers"}

        width, height = dimensions
        aspect_ratio = width / float(height) if height > 0 else 0.0

        # Calculate estimated bandwidth saved
        self.metrics["bandwidth_saved_bytes"] += max(0, 250 * 1024 - len(pre_fetched_bytes))

        # 3. Filter dimensions & aspect ratio
        is_valid_size, size_reason = self.aspect_filter.is_valid_commercial_image(width, height)
        if not is_valid_size:
            self.metrics["audits_failed"] += 1
            return {
                "decision": "REJECT",
                "reason": size_reason,
                "dimensions": f"{width}x{height}",
                "aspect_ratio": round(aspect_ratio, 2)
            }

        # 4. Extract schema microdata & Merchant Authority Score
        schema_data = {"completeness_score": 0.0}
        if json_ld_schema_str:
            schema_data = self.schema_extractor.parse_json_ld_schema(json_ld_schema_str)

        merchant_authority = self.schema_extractor.calculate_merchant_authority(
            page_url=page_url,
            image_url=image_url,
            schema_completeness=schema_data["completeness_score"]
        )

        # 5. Visual packaging & intent matching score
        is_bundle_packaging = (0.85 <= aspect_ratio <= 1.25)
        visual_profile = ProductVisualProfile(
            product_id="PRD-AUTO-TEST",
            title="Simulated Product Image",
            is_bundle_packaging=is_bundle_packaging,
            unit_count=query.parsed_units,
            aspect_ratio=aspect_ratio
        )

        matching_score = self.intent_resolver.score_visual_match(query, visual_profile)
        final_score = (matching_score * 0.6) + (merchant_authority * 0.4)

        decision = "ACCEPT" if final_score >= 0.65 else "REJECT"
        if decision == "ACCEPT":
            self.metrics["audits_passed"] += 1
        else:
            self.metrics["audits_failed"] += 1

        self.metrics["total_execution_time"] += (time.time() - start_time)

        return {
            "decision": decision,
            "matching_score": round(matching_score, 2),
            "merchant_authority": merchant_authority,
            "final_score": round(final_score, 2),
            "dimensions": f"{width}x{height}",
            "detected_intent": query.intent.value,
            "units_matched": query.parsed_units
        }
