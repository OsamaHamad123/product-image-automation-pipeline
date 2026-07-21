# verification_layer/use_cases/fellegi_sunter_linker.py
import math
from dataclasses import dataclass
from enum import Enum
from typing import List, Dict, Any, Tuple


class RecordLinkageDecision(str, Enum):
    GOLD_MATCH = "GOLD_MATCH"          # Automatic Golden Record update
    PROBABLE_MATCH = "PROBABLE_MATCH"  # Conditional match for non-core fields
    NEEDS_REVIEW = "NEEDS_REVIEW"      # Flagged for fast manual review
    DECLINED = "DECLINED"              # Declined to protect catalog integrity


class BulkUploadFailsafeTriggered(Exception):
    """صمام أمان الرفع: يتفعل عند محاولة تعديل/حذف أكثر من 50% من المنتجات النشطة دفعة واحدة."""
    pass


@dataclass
class FellegiSunterResult:
    total_weight_R: float
    decision: RecordLinkageDecision
    feature_weights: Dict[str, float]


class FellegiSunterRecordLinker:
    """
    نموذج الترابط الاحتمالي للسجلات لـ "فيليجي-سونتر" (Fellegi-Sunter Probabilistic Record Linkage Model)
    w_j = log2( m_j / u_j ) عند التطابق
    w'_j = log2( (1 - m_j) / (1 - u_j) ) عند عدم التطابق
    R = sum(w_j)
    """

    GOLD_THRESHOLD = 8.0
    PROBABLE_THRESHOLD = 4.0
    REVIEW_THRESHOLD = 1.0

    # Default m_j and u_j parameters for e-commerce features
    FEATURE_PARAMS = {
        "gtin": {"m": 0.99, "u": 0.0001},
        "brand": {"m": 0.95, "u": 0.02},
        "product_name": {"m": 0.90, "u": 0.01},
        "weight_volume": {"m": 0.92, "u": 0.05},
        "category": {"m": 0.88, "u": 0.10},
    }

    @classmethod
    def calculate_feature_weight(cls, feature_name: str, is_matched: bool) -> float:
        params = cls.FEATURE_PARAMS.get(feature_name, {"m": 0.90, "u": 0.05})
        m = params["m"]
        u = params["u"]

        if is_matched:
            weight = math.log2(m / u)
        else:
            weight = math.log2((1.0 - m) / (1.0 - u))
        return float(round(weight, 4))

    @classmethod
    def evaluate_record_pair(cls, feature_matches: Dict[str, bool]) -> FellegiSunterResult:
        total_R = 0.0
        feature_weights = {}

        for feat, matched in feature_matches.items():
            w = cls.calculate_feature_weight(feat, matched)
            feature_weights[feat] = w
            total_R += w

        total_R = round(total_R, 4)

        if total_R >= cls.GOLD_THRESHOLD:
            decision = RecordLinkageDecision.GOLD_MATCH
        elif total_R >= cls.PROBABLE_THRESHOLD:
            decision = RecordLinkageDecision.PROBABLE_MATCH
        elif total_R >= cls.REVIEW_THRESHOLD:
            decision = RecordLinkageDecision.NEEDS_REVIEW
        else:
            decision = RecordLinkageDecision.DECLINED

        return FellegiSunterResult(
            total_weight_R=total_R,
            decision=decision,
            feature_weights=feature_weights,
        )

    @staticmethod
    def audit_bulk_upload_failsafe(
        total_active_catalog_count: int, affected_records_count: int, threshold_pct: float = 0.50
    ) -> bool:
        """
        صمام أمان الرفع التراكمي (Catalog Upload Failsafe):
        الرفض الآلي التام والتجميد الفوري لأي عملية رفع تراكمية تؤدي لتعديل/حذف > 50% من المنتجات النشطة.
        """
        if total_active_catalog_count <= 0:
            return True

        affect_ratio = float(affected_records_count) / float(total_active_catalog_count)
        if affect_ratio > threshold_pct:
            raise BulkUploadFailsafeTriggered(
                f"🚨 Bulk Upload Failsafe Triggered! Action would modify/delete {affect_ratio*100:.1f}% "
                f"of active catalog records ({affected_records_count}/{total_active_catalog_count}). "
                f"Operation frozen automatically."
            )
        return True
