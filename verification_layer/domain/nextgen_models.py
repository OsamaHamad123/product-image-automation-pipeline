# verification_layer/domain/nextgen_models.py
"""
Next-Gen Engineering Frontiers Domain Models & Value Objects.
Pure Python dataclasses with zero external heavy ML dependencies.
"""

from dataclasses import dataclass, field
from typing import List, Tuple, Dict, Any, Optional


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
