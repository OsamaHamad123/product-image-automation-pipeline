# verification_layer/use_cases/pricing_anomaly_detector.py
import numpy as np
from dataclasses import dataclass
from enum import Enum
from typing import List, Dict, Any, Optional


class PricingAnomalyType(str, Enum):
    PRICING_ERROR = "PRICING_ERROR"          # Drop > 3 sigma_P (Ignore auto-matching)
    FAKE_PROMOTION = "FAKE_PROMOTION"        # Spike followed by immediate drop within <14 days
    COLLUSIVE_PRICING = "COLLUSIVE_PRICING"  # Pearson correlation r_xy -> 1 across merchants
    MAP_VIOLATION = "MAP_VIOLATION"          # Effective price below Minimum Advertised Price
    NORMAL = "NORMAL"


@dataclass
class AnomalyAuditResult:
    anomaly_type: PricingAnomalyType
    is_anomaly: bool
    recommended_action: str
    details: Dict[str, Any]


class PricingAnomalyDetector:
    """
    محرك رصد الشذوذ الزمني والتحليل التنبئي للسلوك الاحتكاري (Temporal Pricing Anomaly & Fake Promo Detector)
    1. Pricing Error: Delta P < -3 * sigma_P (Ignore auto-matching).
    2. Fake Promotion: Spike followed by immediate drop within < 14 days.
    3. Collusive Pricing: Pearson correlation r_xy -> 1 in GNN network.
    4. MAP Violation: Effective Landed Cost < MAP Floor Contract Price.
    """

    @classmethod
    def detect_pricing_error(cls, current_price: float, historical_prices: List[float]) -> bool:
        if len(historical_prices) < 3:
            return False
        mean_p = float(np.mean(historical_prices))
        std_p = float(np.std(historical_prices))
        if std_p <= 0.0:
            return False

        delta_p = current_price - mean_p
        return delta_p < (-3.0 * std_p)

    @classmethod
    def detect_fake_promotion(cls, price_history_timeline: List[Dict[str, Any]]) -> bool:
        """
        قفزة حادة يتبعها مباشرة هبوط متطابق في السعر المرجعي خلال نافذة زمنية قصيرة (<14 يوماً).
        price_history_timeline: list of {"days_ago": int, "reference_price": float, "effective_price": float}
        """
        if len(price_history_timeline) < 3:
            return False

        # Sort timeline by days_ago descending (oldest to newest)
        sorted_history = sorted(price_history_timeline, key=lambda x: x["days_ago"], reverse=True)

        for i in range(len(sorted_history) - 2):
            p_old = sorted_history[i]["reference_price"]
            p_mid = sorted_history[i + 1]["reference_price"]
            p_new = sorted_history[i + 2]["reference_price"]
            days_span = sorted_history[i]["days_ago"] - sorted_history[i + 2]["days_ago"]

            # Artificial spike followed by immediate drop within 14 days
            if days_span <= 14 and p_mid >= (p_old * 1.25) and p_new <= (p_old * 1.05):
                return True
        return False

    @classmethod
    def detect_collusive_pricing(cls, merchant_a_prices: List[float], merchant_b_prices: List[float]) -> float:
        """
        معامل ارتباط بيرسون اللحظي بين أسعار تجار متعددين يقترب من الواحد: r_xy -> 1
        """
        if len(merchant_a_prices) < 3 or len(merchant_a_prices) != len(merchant_b_prices):
            return 0.0

        r_xy = float(np.corrcoef(merchant_a_prices, merchant_b_prices)[0, 1])
        return float(round(r_xy, 4))

    @classmethod
    def audit_pricing_anomaly(
        cls,
        current_effective_price: float,
        historical_prices: List[float],
        map_contract_floor: Optional[float] = None,
        price_history_timeline: Optional[List[Dict[str, Any]]] = None,
        peer_merchant_prices: Optional[List[float]] = None,
    ) -> AnomalyAuditResult:
        # 1. MAP Violation Check
        if map_contract_floor is not None and current_effective_price < map_contract_floor:
            return AnomalyAuditResult(
                anomaly_type=PricingAnomalyType.MAP_VIOLATION,
                is_anomaly=True,
                recommended_action="Send automated compliance report to Vendor Management system for legal enforcement.",
                details={"effective_price": current_effective_price, "map_floor": map_contract_floor},
            )

        # 2. Severe Pricing Error (> 3 sigma_P drop)
        if cls.detect_pricing_error(current_effective_price, historical_prices):
            return AnomalyAuditResult(
                anomaly_type=PricingAnomalyType.PRICING_ERROR,
                is_anomaly=True,
                recommended_action="Ignore competitor price drop and avoid automated price matching to protect profit margins.",
                details={"current_price": current_effective_price},
            )

        # 3. Fake Promotion Detection
        if price_history_timeline and cls.detect_fake_promotion(price_history_timeline):
            return AnomalyAuditResult(
                anomaly_type=PricingAnomalyType.FAKE_PROMOTION,
                is_anomaly=True,
                recommended_action="Maintain value-based pricing strategy and launch counter campaign highlighting price stability.",
                details={},
            )

        # 4. Collusive Pricing Detection
        if peer_merchant_prices and len(peer_merchant_prices) == len(historical_prices):
            r_xy = cls.detect_collusive_pricing(historical_prices, peer_merchant_prices)
            if r_xy >= 0.95:
                return AnomalyAuditResult(
                    anomaly_type=PricingAnomalyType.COLLUSIVE_PRICING,
                    is_anomaly=True,
                    recommended_action="Activate dynamic pricing scenarios based on individual customer demand elasticity to break duopoly.",
                    details={"pearson_correlation": r_xy},
                )

        return AnomalyAuditResult(
            anomaly_type=PricingAnomalyType.NORMAL,
            is_anomaly=False,
            recommended_action="Standard price tracking active.",
            details={},
        )
