# verification_layer/domain/models.py
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Dict, Any, List


class CatalogType(str, Enum):
    GLOBAL_BRAND = "global_brand"
    WHITE_LABEL = "white_label"


class SurfaceCurvature(str, Enum):
    FLAT = "flat"
    CURVED = "curved"


class OperationalDecision(str, Enum):
    AUTO_APPROVE = "AUTO_APPROVE"
    MANUAL_REVIEW = "MANUAL_REVIEW"
    AUTO_REJECT = "AUTO_REJECT"


class MatchStatus(str, Enum):
    EXACT = "exact"
    SYNONYM = "synonym"
    RELATED = "related"
    UNBRANDED = "unbranded"
    MISMATCH = "mismatch"
    NOT_FOUND = "not_found"


@dataclass
class CatalogProduct:
    asin_or_gtin: str
    brand: str
    product_class: str
    weight_volume: str
    packaging_type: str = "retail_box"
    catalog_type: CatalogType = CatalogType.GLOBAL_BRAND
    surface_curvature: SurfaceCurvature = SurfaceCurvature.FLAT


@dataclass
class ImageInput:
    image_bytes: Optional[bytes] = None
    image_url: Optional[str] = None
    file_path: Optional[str] = None
    width: int = 0
    height: int = 0


@dataclass
class GateEvaluation:
    gate_name: str
    passed: bool
    score: float
    reason: str
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class BrandEvaluation:
    detected_brand_text: Optional[str]
    match_status: MatchStatus
    similarity_score: float = 0.0


@dataclass
class ProductClassEvaluation:
    detected_class_text: Optional[str]
    match_status: MatchStatus
    similarity_score: float = 0.0


@dataclass
class WeightVolumeEvaluation:
    extracted_packaging_metrics: Optional[str]
    normalized_value_g_or_ml: Optional[float]
    match_status: MatchStatus


@dataclass
class PackagingIntegrityEvaluation:
    is_retail_packaging: bool
    packaging_style_detected: str
    rejection_flag_reason: Optional[str] = None


@dataclass
class AdjudicationReport:
    brand_evaluation: BrandEvaluation
    product_class_evaluation: ProductClassEvaluation
    weight_volume_evaluation: WeightVolumeEvaluation
    packaging_integrity_evaluation: PackagingIntegrityEvaluation
    adjudicator_score: float  # 0.0, 0.5, 1.0
    justification_narrative: str


@dataclass
class VerificationResult:
    overall_passed: bool
    decision: OperationalDecision
    fusion_score: float
    visual_similarity_score: float  # S_vis
    ocr_similarity_score: float     # S_ocr
    vlm_score: float                # S_vlm
    gate_evaluations: List[GateEvaluation] = field(default_factory=list)
    adjudication_report: Optional[AdjudicationReport] = None
    rejection_reasons: List[str] = field(default_factory=list)
