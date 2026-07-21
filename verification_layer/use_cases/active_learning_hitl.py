# verification_layer/use_cases/active_learning_hitl.py
import math
import numpy as np
from typing import List, Dict, Any, Tuple


class UncertaintySamplingEngine:
    """
    محرك أخذ عينات عدم اليقين للمراجعة البشرية (Uncertainty-Aware Sampling Engine)
    - أخذ عينات الأقل ثقة (Least Confidence)
    - أخذ عينات هامش الشك الأدنى (Smallest Margin)
    - حساب اعتلاج عدم اليقين (Entropy Reduction)
    """

    @staticmethod
    def least_confidence_score(class_probabilities: List[float]) -> float:
        """
        x_LC = 1 - max(P(y_hat | x))
        يرجع درجة عالية عندما تكون ثقة الفئة الأولى ضعيفة.
        """
        if not class_probabilities:
            return 1.0
        max_p = max(class_probabilities)
        return float(1.0 - max_p)

    @staticmethod
    def smallest_margin_score(class_probabilities: List[float]) -> float:
        """
        x_M = 1 - (P(y_1 | x) - P(y_2 | x))
        يرجع درجة عالية عندما يتقارب الاحتمال بين الفئتين الأولى والثانية (غموض حاد).
        """
        if len(class_probabilities) < 2:
            return 0.0
        sorted_p = sorted(class_probabilities, reverse=True)
        margin = sorted_p[0] - sorted_p[1]
        return float(1.0 - margin)

    @staticmethod
    def entropy_score(class_probabilities: List[float]) -> float:
        """
        x_H = - sum P(y_i | x) * log(P(y_i | x))
        حساب الاعتلاج والـ Entropy لقياس تشتت قرار النموذج عبر كافة الفئات.
        """
        entropy = 0.0
        for p in class_probabilities:
            if p > 1e-12:
                entropy -= p * math.log(p)
        return float(entropy)

    @classmethod
    def prioritize_samples_for_human_review(
        cls, candidate_samples: List[Dict[str, Any]], top_n: int = 50
    ) -> List[Dict[str, Any]]:
        """
        ترتيب الحالات الواردة حسب أولوية الحاجة للمراجعة البشرية استناداً لمقاييس عدم اليقين.
        """
        prioritized = []
        for sample in candidate_samples:
            probs = sample.get("class_probabilities", [0.5, 0.5])
            lc = cls.least_confidence_score(probs)
            sm = cls.smallest_margin_score(probs)
            ent = cls.entropy_score(probs)

            # Combined uncertainty priority score
            priority_score = (0.4 * lc) + (0.4 * sm) + (0.2 * ent)
            item_copy = dict(sample)
            item_copy["uncertainty_priority_score"] = round(priority_score, 4)
            item_copy["least_confidence"] = round(lc, 4)
            item_copy["smallest_margin"] = round(sm, 4)
            item_copy["entropy"] = round(ent, 4)
            prioritized.append(item_copy)

        prioritized.sort(key=lambda x: x["uncertainty_priority_score"], reverse=True)
        return prioritized[:top_n]


class ConformalPredictionRecalibrator:
    """
    إعادة المعايرة الديناميكية لعتبات القرار عبر التنبؤ التوافقي (Conformal Prediction)
    تضمين ضمانات رياضية لضبط عتبات القبول والرفض عند معدل خطأ مسموح alpha = 0.05
    """

    @staticmethod
    def compute_conformal_threshold(calibration_scores: List[float], alpha: float = 0.05) -> float:
        """
        Q(1 - alpha) = Quantile( ceil((1 - alpha)*(n + 1)) / n; {s_i} )
        حيث s_i = 1 - P(Y_correct | X)
        """
        if not calibration_scores:
            return 0.88  # Default threshold

        n = len(calibration_scores)
        sorted_scores = sorted(calibration_scores)
        target_quantile = math.ceil((1.0 - alpha) * (n + 1)) / float(n)
        target_quantile = min(1.0, max(0.0, target_quantile))

        quantile_idx = min(n - 1, max(0, int(math.ceil(target_quantile * n)) - 1))
        return float(sorted_scores[quantile_idx])
