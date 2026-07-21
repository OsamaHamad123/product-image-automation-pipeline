# verification_layer/use_cases/fusion_engine.py
from verification_layer.domain.models import OperationalDecision, SurfaceCurvature
from verification_layer.use_cases.string_distance_calculator import StringDistanceCalculator
from verification_layer.use_cases.bilingual_text_processor import BilingualTextProcessor


class MultiModalFusionEngine:
    """
    محرك الدمج الرياضي وبوابات اتخاذ القرار التشغيلية (Multi-Modal Fusion Engine)
    - S_ocr = 0.5 * S_brand_jw + 0.3 * S_class_jw + 0.2 * S_weight_match
    - S_fusion = (0.4 * S_vis + 0.6 * S_ocr) * S_vlm
    - 0.88 <= S_fusion <= 1.00 -> AUTO_APPROVE
    - 0.65 <= S_fusion < 0.88  -> MANUAL_REVIEW
    - 0.00 <= S_fusion < 0.65  -> AUTO_REJECT
    """

    AUTO_APPROVE_THRESHOLD = 0.88
    MANUAL_REVIEW_THRESHOLD = 0.65

    @classmethod
    def calculate_ocr_score(
        cls,
        detected_brand: str,
        target_brand: str,
        detected_class: str,
        target_class: str,
        extracted_metric: str,
        target_metric: str,
        surface: SurfaceCurvature = SurfaceCurvature.FLAT,
    ) -> float:
        if not detected_brand and not detected_class and not extracted_metric:
            return 0.85  # Neutral fallback when OCR text is unextracted or unavailable

        s_brand_jw = StringDistanceCalculator.jaro_winkler_similarity(detected_brand, target_brand)
        s_class_jw = StringDistanceCalculator.jaro_winkler_similarity(detected_class, target_class)
        s_weight_match = BilingualTextProcessor.compare_metrics(extracted_metric, target_metric)

        s_ocr = (0.5 * s_brand_jw) + (0.3 * s_class_jw) + (0.2 * s_weight_match)
        return float(max(0.0, min(1.0, s_ocr)))


    @classmethod
    def calculate_fusion_score(
        cls, s_vis: float, s_ocr: float, s_vlm: float
    ) -> float:
        """
        S_fusion = (0.4 * S_vis + 0.6 * S_ocr) * S_vlm
        """
        vis_clamped = max(0.0, min(1.0, s_vis))
        ocr_clamped = max(0.0, min(1.0, s_ocr))
        vlm_clamped = max(0.0, min(1.0, s_vlm))

        s_fusion = (0.4 * vis_clamped + 0.6 * ocr_clamped) * vlm_clamped
        return float(round(s_fusion, 4))

    @classmethod
    def determine_decision(cls, s_fusion: float) -> OperationalDecision:
        if s_fusion >= cls.AUTO_APPROVE_THRESHOLD:
            return OperationalDecision.AUTO_APPROVE
        elif s_fusion >= cls.MANUAL_REVIEW_THRESHOLD:
            return OperationalDecision.MANUAL_REVIEW
        else:
            return OperationalDecision.AUTO_REJECT
