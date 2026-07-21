# verification_layer/use_cases/gs1_unspsc_taxonomy_classifier.py
from dataclasses import dataclass
from typing import Dict, Any, List, Optional


@dataclass
class TaxonomyClassificationResult:
    gs1_gpc_brick_code: str
    gs1_brick_title: str
    unspsc_code: str
    unspsc_title: str
    confidence_score: float
    is_rigid_schema_compliant: bool


class GS1UNSPSCTaxonomyClassifier:
    """
    التصنيف التلقائي للأنطولوجيا الموزعة وفق معايير GS1 GPC و UNSPSC
    - GS1 GPC Brick Codes (مثال: 10000043 للمستحضرات أو 10000150 للأغذية).
    - UNSPSC Codes (مثال: 43211503 أو 50131700).
    """

    TAXONOMY_MAPPINGS = {
        "milk": {
            "gs1_code": "10000025",
            "gs1_title": "Milk and Milk Substitutes (Perishable)",
            "unspsc": "50131700",
            "unspsc_title": "Milk and butter and cream products",
        },
        "chocolate": {
            "gs1_code": "10000043",
            "gs1_title": "Confectionery Products",
            "unspsc": "50161813",
            "unspsc_title": "Chocolates and chocolate products",
        },
        "hand wash": {
            "gs1_code": "10000350",
            "gs1_title": "Personal Cleanliness / Hand Sanitizer",
            "unspsc": "53131608",
            "unspsc_title": "Hand handwash soap",
        },
        "oil": {
            "gs1_code": "10000150",
            "gs1_title": "Edible Plant Oils",
            "unspsc": "50151500",
            "unspsc_title": "Edible vegetable oils",
        },
    }

    @classmethod
    def classify_product_taxonomy(cls, product_name: str, category_hint: Optional[str] = None) -> TaxonomyClassificationResult:
        p_lower = (product_name + " " + (category_hint or "")).lower()

        matched_key = None
        for key in cls.TAXONOMY_MAPPINGS:
            if key in p_lower:
                matched_key = key
                break

        if matched_key:
            data = cls.TAXONOMY_MAPPINGS[matched_key]
            return TaxonomyClassificationResult(
                gs1_gpc_brick_code=data["gs1_code"],
                gs1_brick_title=data["gs1_title"],
                unspsc_code=data["unspsc"],
                unspsc_title=data["unspsc_title"],
                confidence_score=0.98,
                is_rigid_schema_compliant=True,
            )

        # Fallback general classification
        return TaxonomyClassificationResult(
            gs1_gpc_brick_code="10000000",
            gs1_brick_title="General Groceries & CPG",
            unspsc_code="50000000",
            unspsc_title="Food Beverage and Tobacco Products",
            confidence_score=0.75,
            is_rigid_schema_compliant=True,
        )
