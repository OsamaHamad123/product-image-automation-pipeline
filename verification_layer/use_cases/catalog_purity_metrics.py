# verification_layer/use_cases/catalog_purity_metrics.py
import math
from dataclasses import dataclass
from typing import List, Dict, Any


@dataclass
class CatalogPurityReport:
    catalog_error_rate_cer: float         # CER <= 1.5%
    value_accuracy_error_rate_e1: float  # E1 <= 0.5%
    structure_completeness_error_rate_e2: float  # E2 == 0.0 (100% complete)
    normalization_consistency_error_rate_e3: float  # E3 <= 2.0%
    combined_conversion_loss_ratio: float # L_combined
    catalog_purity_score: float           # 100 - L_combined * 100
    is_compliant: bool
    reasons: List[str]


class CatalogPurityMetricsEngine:
    """
    مرصد تقييم سلامة ونقاء الكتالوج الرقمي (Catalog Purity & Quality Metrics Engine)
    - CER: Catalog Error Rate
    - E1: Value Accuracy Error Rate
    - E2: Structure Completeness Error Rate
    - E3: Normalization Consistency Error Rate
    - IS: Record Importance Score
    - L_combined = 1 - prod(1 - r_k)
    """

    MAX_CER_THRESHOLD = 1.5   # %1.5
    MAX_E1_THRESHOLD = 0.5    # %0.5
    MAX_E2_THRESHOLD = 0.0    # 100% complete
    MAX_E3_THRESHOLD = 2.0    # %2.0

    @classmethod
    def calculate_catalog_error_rate(cls, incorrect_records: int, total_records: int) -> float:
        if total_records <= 0:
            return 0.0
        return float(round((incorrect_records / float(total_records)) * 100.0, 2))

    @classmethod
    def calculate_value_accuracy_error_rate_e1(cls, value_errors: int, total_evaluated_values: int) -> float:
        if total_evaluated_values <= 0:
            return 0.0
        return float(round((value_errors / float(total_evaluated_values)) * 100.0, 2))

    @classmethod
    def calculate_importance_score(
        cls, r_table: int, r_max: int, c_table: int, d_stream: int, k_shared: int
    ) -> float:
        """
        IS = [ 200 * (R_table / R_max) * C_table * 0.1 ] + [ D_stream * 7 ] + [ K_shared * 3 ]
        """
        if r_max <= 0:
            r_max = max(1, r_table)
        ratio = r_table / float(r_max)
        part1 = 200.0 * ratio * c_table * 0.1
        part2 = d_stream * 7.0
        part3 = k_shared * 3.0
        return float(round(part1 + part2 + part3, 2))

    @classmethod
    def calculate_combined_conversion_loss(cls, individual_loss_ratios: List[float]) -> float:
        """
        L_combined = 1 - prod_{k=1}^K (1 - r_k)
        حساب الأثر الاقتصادي التراكمي للخسائر دمجاً وبصورة ضربية لمنع الاحتساب المزدوج.
        """
        if not individual_loss_ratios:
            return 0.0

        prod = 1.0
        for r_k in individual_loss_ratios:
            clamped_r = max(0.0, min(1.0, r_k))
            prod *= (1.0 - clamped_r)

        l_combined = 1.0 - prod
        return float(round(l_combined, 4))

    @classmethod
    def generate_catalog_purity_report(
        cls,
        incorrect_records: int,
        total_records: int,
        value_errors: int,
        total_values: int,
        missing_required_fields: int,
        sample_count: int,
        normalization_errors: int,
        individual_loss_ratios: List[float],
    ) -> CatalogPurityReport:
        cer = cls.calculate_catalog_error_rate(incorrect_records, total_records)
        e1 = cls.calculate_value_accuracy_error_rate_e1(value_errors, total_values)

        e2 = float(round(missing_required_fields / float(max(1, sample_count)), 2))
        e3 = float(round((normalization_errors / float(max(1, sample_count))) * 100.0, 2))

        l_combined = cls.calculate_combined_conversion_loss(individual_loss_ratios)
        purity_score = float(round((1.0 - l_combined) * 100.0, 2))

        is_compliant = True
        reasons = []

        if cer > cls.MAX_CER_THRESHOLD:
            is_compliant = False
            reasons.append(f"CER ({cer}%) exceeds maximum allowed threshold ({cls.MAX_CER_THRESHOLD}%).")

        if e1 > cls.MAX_E1_THRESHOLD:
            is_compliant = False
            reasons.append(f"Value Accuracy Error Rate E1 ({e1}%) exceeds maximum allowed threshold ({cls.MAX_E1_THRESHOLD}%).")

        if e2 > cls.MAX_E2_THRESHOLD:
            is_compliant = False
            reasons.append(f"Completeness Error Rate E2 ({e2}) indicates missing mandatory fields.")

        if e3 > cls.MAX_E3_THRESHOLD:
            is_compliant = False
            reasons.append(f"Normalization Error Rate E3 ({e3}%) exceeds maximum allowed threshold ({cls.MAX_E3_THRESHOLD}%).")

        return CatalogPurityReport(
            catalog_error_rate_cer=cer,
            value_accuracy_error_rate_e1=e1,
            structure_completeness_error_rate_e2=e2,
            normalization_consistency_error_rate_e3=e3,
            combined_conversion_loss_ratio=l_combined,
            catalog_purity_score=purity_score,
            is_compliant=is_compliant,
            reasons=reasons,
        )
