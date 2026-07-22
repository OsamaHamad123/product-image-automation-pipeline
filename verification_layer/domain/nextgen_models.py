# verification_layer/domain/nextgen_models.py
"""
Next-Gen Engineering Frontiers Domain Models & Value Objects.
Pure Python dataclasses with zero external heavy ML dependencies.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Tuple, Dict, Any, Optional


class IntentType(Enum):
    SINGLE_UNIT = "SINGLE"
    MULTI_PACK = "BUNDLE"
    UNKNOWN = "UNKNOWN"


class ProxyState(Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


@dataclass(frozen=True)
class SearchQuery:
    raw_text: str
    cleaned_text: str
    parsed_units: int
    intent: IntentType


@dataclass(frozen=True)
class ProductVisualProfile:
    product_id: str
    title: str
    is_bundle_packaging: bool
    unit_count: int
    aspect_ratio: float


@dataclass
class ProxyNode:
    url: str
    state: ProxyState = ProxyState.CLOSED
    failure_count: int = 0
    success_count: int = 0
    last_failure_time: float = 0.0
    latency_history: List[float] = field(default_factory=list)

    @property
    def average_latency(self) -> float:
        if not self.latency_history:
            return 99.0
        return sum(self.latency_history) / len(self.latency_history)


@dataclass(frozen=True)
class LabColor:
    l_star: float
    a_star: float
    b_star: float
    label: str = "Standard Target"


@dataclass
class ProductBrandSpecs:
    gtin: str
    brand_name: str
    target_colors: List[LabColor] = field(default_factory=list)
    allowed_tolerance: float = 3.5


@dataclass
class Product:
    sku: str
    title_ar: str
    title_en: str
    specifications: Dict[str, Any]
    expected_brand: str
    expected_colors_lab: List[List[float]] = field(default_factory=list)


@dataclass
class ImageAuditResult:
    success: bool
    detected_mime: str
    colorfulness: float
    sharpness: float
    shadow_preserved: bool
    is_brand_matched: bool
    color_delta_e_deviations: List[float]
    spatial_packaging_ratio: float
    aesthetic_score: float
    spec_consistency_score: float
    live_metrics: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SearchCandidate:
    document_id: str
    title: str
    score: float
    source: str


@dataclass
class RRFResult:
    query: str
    candidates: List[Tuple[str, float]]
    consensus_score: float
    source: str
    circuit_status: Dict[str, str]


@dataclass
class SpecAuditResult:
    spec_text: str
    text_similarity: float
    matched_bounding_box: Optional[Tuple[int, int, int, int]]
    iou_score: float
    is_consistent: bool
    status: str


@dataclass
class PackagingDensityResult:
    hull_area: float
    bbox_area: float
    packaging_ratio: float
    is_efficient: bool
    status_label: str
    hull_vertex_count: int


@dataclass
class CAVIScore:
    focus_variance: float
    color_entropy: float
    canvas_cleanliness: float
    composite_cavi: float
    viability_rank: str


@dataclass
class DeltaECompliance:
    gtin: str
    brand_name: str
    delta_e2000: float
    perception_level: str
    brand_decision: str
    approved: bool
