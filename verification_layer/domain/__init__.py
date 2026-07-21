# verification_layer/domain/__init__.py
from verification_layer.domain.models import (
    CatalogProduct,
    ImageInput,
    GateEvaluation,
    VerificationResult,
    AdjudicationReport,
    CatalogType,
    SurfaceCurvature,
    OperationalDecision,
    MatchStatus,
)
from verification_layer.domain.value_objects import (
    NormalizedMetric,
    MetricUnit,
    StringDistanceScore,
)

__all__ = [
    "CatalogProduct",
    "ImageInput",
    "GateEvaluation",
    "VerificationResult",
    "AdjudicationReport",
    "CatalogType",
    "SurfaceCurvature",
    "OperationalDecision",
    "MatchStatus",
    "NormalizedMetric",
    "MetricUnit",
    "StringDistanceScore",
]
