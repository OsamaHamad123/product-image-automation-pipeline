# verification_layer/use_cases/package_drift_detector.py
from dataclasses import dataclass
from enum import Enum
from typing import Optional, List, Dict, Any


class GovernanceRiskLevel(str, Enum):
    IMMEDIATE_ESCALATION = "IMMEDIATE_ESCALATION"  # Allergen/ingredient changes (High Risk)
    TECHNICAL_REVIEW_HOLD = "TECHNICAL_REVIEW_HOLD"  # Certification/regulatory doc updates (Medium Risk)
    SCHEDULED_QUEUE = "SCHEDULED_QUEUE"              # Minor visual/font design updates (Low Risk)
    NO_DRIFT = "NO_DRIFT"


@dataclass
class DriftEvaluationResult:
    has_drift: bool
    drift_score: float  # 0.0 (no drift) to 1.0 (complete drift)
    risk_level: GovernanceRiskLevel
    trigger_auto_rollback: bool
    reasons: List[str]


class PackageDesignDriftDetector:
    """
    كاشف الانحراف البصري في ملصقات المنتجات والعبوات الجديدة (Package Design Drift Detector)
    - كشف التغير في الهوية البصرية مقارنة بالسجل الذهبي (Golden Record).
    - فرز الانحرافات إلى 3 مسارات حوكمة:
      1. IMMEDIATE_ESCALATION: تغير مسببات الحساسية أو المكونات الحرجة (تفعيل صمام الاسترداد Auto-rollback).
      2. TECHNICAL_REVIEW_HOLD: تراجع أو تغير الشهادات والوثائق التنظيمية.
      3. SCHEDULED_QUEUE: التحديثات الشكلية الطفيفة والخطوط.
    """

    DRIFT_THRESHOLD = 0.15  # Visual cosine distance drift threshold (>0.15 indicates drift)

    @classmethod
    def evaluate_package_drift(
        cls,
        current_similarity_score: float,
        allergen_text_changed: bool = False,
        ingredients_changed: bool = False,
        certifications_changed: bool = False,
    ) -> DriftEvaluationResult:
        drift_score = max(0.0, min(1.0, 1.0 - current_similarity_score))
        has_drift = drift_score >= cls.DRIFT_THRESHOLD
        reasons = []

        if not has_drift and not allergen_text_changed and not ingredients_changed and not certifications_changed:
            return DriftEvaluationResult(
                has_drift=False,
                drift_score=round(drift_score, 4),
                risk_level=GovernanceRiskLevel.NO_DRIFT,
                trigger_auto_rollback=False,
                reasons=["No package design drift detected."],
            )

        # High Risk: Allergen or critical ingredient changes
        if allergen_text_changed or ingredients_changed:
            reasons.append("Critical drift detected in allergen/ingredient labeling!")
            return DriftEvaluationResult(
                has_drift=True,
                drift_score=round(drift_score, 4),
                risk_level=GovernanceRiskLevel.IMMEDIATE_ESCALATION,
                trigger_auto_rollback=True,
                reasons=reasons,
            )

        # Medium Risk: Certification / regulatory changes
        if certifications_changed:
            reasons.append("Regulatory certification drift detected on product label.")
            return DriftEvaluationResult(
                has_drift=True,
                drift_score=round(drift_score, 4),
                risk_level=GovernanceRiskLevel.TECHNICAL_REVIEW_HOLD,
                trigger_auto_rollback=False,
                reasons=reasons,
            )

        # Low Risk: Minor visual packaging styling drift
        reasons.append("Minor visual packaging design update detected.")
        return DriftEvaluationResult(
            has_drift=True,
            drift_score=round(drift_score, 4),
            risk_level=GovernanceRiskLevel.SCHEDULED_QUEUE,
            trigger_auto_rollback=False,
            reasons=reasons,
        )
