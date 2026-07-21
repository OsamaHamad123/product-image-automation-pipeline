# verification_layer/use_cases/contribution_margin_calculator.py
from dataclasses import dataclass
from typing import Dict, Any, List


@dataclass
class ContributionMarginReport:
    revenue: float
    cm1_amount: float
    cm1_ratio_pct: float
    cm2_amount: float
    cm2_ratio_pct: float
    cm3_amount: float
    cm3_ratio_pct: float
    is_healthy_margin: bool
    reasons: List[str]


class ContributionMarginCalculator:
    """
    الهيكلية الرياضية لهامش المساهمة متعدد المراحل (Multi-Stage Contribution Margin Engine)
    - CM1 = Revenue - COGS - Platform Referral Fees
    - CM2 = CM1 - (Shipping Cost + Fulfillment Cost + Payment Fees)
    - CM3 = CM2 - (Allocated Ad Spend + Reverse Logistics Returns + Promos & Coupons)
    - CM_ratio = (CM / Revenue) * 100
    """

    # Target CM3 benchmarks for 2026 categories
    CATEGORY_CM3_TARGETS = {
        "beauty_wellness": {"min_cm3_pct": 50.0, "max_cm3_pct": 70.0},
        "home_kitchen": {"min_cm3_pct": 30.0, "max_cm3_pct": 50.0},
        "consumer_electronics": {"min_cm3_pct": 15.0, "max_cm3_pct": 30.0},
        "grocery": {"min_cm3_pct": 10.0, "max_cm3_pct": 18.0},
        "pet_supplies": {"min_cm3_pct": 35.0, "max_cm3_pct": 55.0},
        "default": {"min_cm3_pct": 20.0, "max_cm3_pct": 50.0},
    }

    @classmethod
    def calculate_multi_stage_margins(
        cls,
        revenue: float,
        cogs: float,
        platform_referral_fees: float,
        shipping_cost: float,
        fulfillment_cost: float,
        payment_fees: float,
        allocated_ad_spend: float,
        returns_reverse_logistics: float,
        promos_coupons: float,
        category: str = "default",
    ) -> ContributionMarginReport:
        if revenue <= 0.0:
            return ContributionMarginReport(
                revenue=0.0,
                cm1_amount=0.0,
                cm1_ratio_pct=0.0,
                cm2_amount=0.0,
                cm2_ratio_pct=0.0,
                cm3_amount=0.0,
                cm3_ratio_pct=0.0,
                is_healthy_margin=False,
                reasons=["Revenue is zero or negative."],
            )

        # CM1: Vitality & Product Validity Margin
        cm1 = revenue - cogs - platform_referral_fees
        cm1_pct = (cm1 / revenue) * 100.0

        # CM2: Logistic & Operational Margin
        cm2 = cm1 - (shipping_cost + fulfillment_cost + payment_fees)
        cm2_pct = (cm2 / revenue) * 100.0

        # CM3: True Net Growth Margin
        cm3 = cm2 - (allocated_ad_spend + returns_reverse_logistics + promos_coupons)
        cm3_pct = (cm3 / revenue) * 100.0

        targets = cls.CATEGORY_CM3_TARGETS.get(category.lower(), cls.CATEGORY_CM3_TARGETS["default"])
        min_target = targets["min_cm3_pct"]

        is_healthy = cm3_pct >= min_target
        reasons = []

        if not is_healthy:
            reasons.append(
                f"CM3 Net Growth Margin ({cm3_pct:.1f}%) is below minimum target benchmark ({min_target:.1f}%) for {category}."
            )
        else:
            reasons.append(f"CM3 Net Growth Margin ({cm3_pct:.1f}%) meets target benchmark for {category}.")

        return ContributionMarginReport(
            revenue=round(revenue, 2),
            cm1_amount=round(cm1, 2),
            cm1_ratio_pct=round(cm1_pct, 2),
            cm2_amount=round(cm2, 2),
            cm2_ratio_pct=round(cm2_pct, 2),
            cm3_amount=round(cm3, 2),
            cm3_ratio_pct=round(cm3_pct, 2),
            is_healthy_margin=is_healthy,
            reasons=reasons,
        )
