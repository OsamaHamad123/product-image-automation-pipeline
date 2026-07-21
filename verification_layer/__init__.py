# verification_layer/__init__.py
"""
Verification and Validation Layer (V&V Layer)
Clean Architecture implementation for product catalog image matching and quality control.
"""

from verification_layer.domain.models import (
    CatalogProduct,
    VerificationResult,
    OperationalDecision,
    CatalogType,
)
from verification_layer.use_cases.catalog_verifier import CatalogVerificationPipeline

__all__ = [
    "CatalogProduct",
    "VerificationResult",
    "OperationalDecision",
    "CatalogType",
    "CatalogVerificationPipeline",
]
