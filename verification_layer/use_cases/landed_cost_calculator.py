# verification_layer/use_cases/landed_cost_calculator.py
from dataclasses import dataclass
from typing import Optional, List, Dict, Any


@dataclass
class LandedCostResult:
    base_price: float
    applied_coupon_discount: float
    volume_tier_discount: float
    shipping_freight_fee: float
    effective_landed_cost: float
    competitor_in_stock: bool
    should_defend_margin: bool  # True if competitor is out of stock (keep prices high)


class LandedCostCalculator:
    """
    محرك احتساب السعر الفعال الفعلي ومحاكي السلة (Effective Landed Cost Calculator & Stock Defense)
    - Effective Landed Cost = Base Price - Coupon - Volume Discount + Shipping & Freight
    - فحص توفر المخزون (Stock Availability): الحفاظ على الأسعار عند نفاد مخزون المنافس دون المخاطرة بفقدان الحصة السوقية.
    """

    @classmethod
    def calculate_effective_landed_cost(
        cls,
        base_price: float,
        coupon_discount: float = 0.0,
        volume_tier_discount: float = 0.0,
        shipping_fee: float = 0.0,
        competitor_stock_qty: int = 10,
    ) -> LandedCostResult:
        eff_cost = base_price - coupon_discount - volume_tier_discount + shipping_fee
        eff_cost = max(0.01, round(eff_cost, 2))

        in_stock = competitor_stock_qty > 0
        # If competitor stock is 0 or approaching zero (<= 2), defend margin and keep internal price steady
        should_defend = not in_stock or competitor_stock_qty <= 2

        return LandedCostResult(
            base_price=base_price,
            applied_coupon_discount=coupon_discount,
            volume_tier_discount=volume_tier_discount,
            shipping_freight_fee=shipping_fee,
            effective_landed_cost=eff_cost,
            competitor_in_stock=in_stock,
            should_defend_margin=should_defend,
        )
