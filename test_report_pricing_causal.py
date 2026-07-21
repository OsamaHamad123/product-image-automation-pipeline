# test_report_pricing_causal.py
import numpy as np

from verification_layer.use_cases.landed_cost_calculator import LandedCostCalculator
from verification_layer.use_cases.pricing_anomaly_detector import PricingAnomalyDetector, PricingAnomalyType
from verification_layer.use_cases.multimodal_arcface_matcher import MultimodalArcFaceSKUMatcher
from verification_layer.use_cases.causal_iqa_estimator import CausalIQAEstimator
from verification_layer.use_cases.dynamic_pricing_bandit import MonotonicDynamicPricingBandit
from verification_layer.use_cases.contribution_margin_calculator import ContributionMarginCalculator


def test_landed_cost_calculator():
    print("\n--- Test 1: Effective Landed Cost Calculator & Stock Defense ---")
    res_in_stock = LandedCostCalculator.calculate_effective_landed_cost(
        base_price=100.0, coupon_discount=10.0, volume_tier_discount=5.0, shipping_fee=15.0, competitor_stock_qty=50
    )
    # Effective landed cost = 100 - 10 - 5 + 15 = 100.0
    assert res_in_stock.effective_landed_cost == 100.0
    assert res_in_stock.should_defend_margin is False
    print(f"✅ In-Stock Landed Cost: ${res_in_stock.effective_landed_cost} (Should Defend Margin: {res_in_stock.should_defend_margin})")

    res_out_stock = LandedCostCalculator.calculate_effective_landed_cost(
        base_price=90.0, coupon_discount=0.0, volume_tier_discount=0.0, shipping_fee=10.0, competitor_stock_qty=0
    )
    assert res_out_stock.should_defend_margin is True
    print(f"✅ Out-of-Stock Competitor -> Defensive Margin Preserved (Should Defend: True).")


def test_pricing_anomaly_detector():
    print("\n--- Test 2: Pricing Anomaly Detector & MAP Compliance ---")
    # MAP Violation Test
    res_map = PricingAnomalyDetector.audit_pricing_anomaly(
        current_effective_price=80.0, historical_prices=[100.0, 102.0, 99.0], map_contract_floor=90.0
    )
    assert res_map.anomaly_type == PricingAnomalyType.MAP_VIOLATION
    print(f"✅ MAP Violation caught: {res_map.anomaly_type.value}")

    # Severe Pricing Error (> 3 sigma drop)
    hist = [100.0, 101.0, 99.0, 100.5, 99.5] # mean ~100, std ~0.7
    res_err = PricingAnomalyDetector.audit_pricing_anomaly(
        current_effective_price=50.0, historical_prices=hist # Drop of 50 is > 3 sigma
    )
    assert res_err.anomaly_type == PricingAnomalyType.PRICING_ERROR
    print(f"✅ Severe Pricing Error caught (Drop > 3 sigma): {res_err.anomaly_type.value}")

    # Collusive Pricing (r_xy -> 1)
    merchant_a = [100.0, 105.0, 110.0, 115.0]
    merchant_b = [200.0, 210.0, 220.0, 230.0]
    r_xy = PricingAnomalyDetector.detect_collusive_pricing(merchant_a, merchant_b)
    assert r_xy >= 0.99
    print(f"✅ Collusive Pricing Pearson correlation: r_xy = {r_xy}")


def test_multimodal_arcface_matcher():
    print("\n--- Test 3: Multimodal ArcFace Angular Margin Loss Matcher ---")
    title_a = "Nido Powder Milk 2.25kg Can"
    title_b = "Nido Whole Milk Powder 2250g Tin"

    match_res = MultimodalArcFaceSKUMatcher.match_competitor_sku(
        internal_title=title_a, competitor_title=title_b, visual_similarity_score=0.90
    )

    assert match_res.is_matched is True
    assert match_res.confidence_percentage >= 80.0
    assert match_res.arcface_loss_value >= 0.0
    print(f"✅ ArcFace Match Confidence: {match_res.confidence_percentage}% (Loss: {match_res.arcface_loss_value})")


def test_causal_iqa_estimator():
    print("\n--- Test 4: NIMA Score & Double ML Neyman-Orthogonal Causal ATE ---")
    # NIMA aesthetic score calculation
    probs = [0.01, 0.02, 0.02, 0.05, 0.10, 0.20, 0.30, 0.20, 0.08, 0.02]
    nima = CausalIQAEstimator.calculate_nima_score(probs)
    assert 6.0 <= nima <= 8.0
    print(f"✅ NIMA Aesthetic Score: {nima}/10.0")

    # Double ML Causal ATE estimation
    N = 100
    np.random.seed(42)
    X = np.random.randn(N, 3).tolist()
    # True causal effect theta = 0.15
    T = [float(0.5 * X[i][0] + np.random.randn() * 0.1) for i in range(N)]
    Y = [1 if (0.15 * T[i] + 0.3 * X[i][0] + np.random.randn() * 0.1) > 0 else 0 for i in range(N)]

    dml_res = CausalIQAEstimator.estimate_double_ml_causal_effect(
        converters_y=Y, catalog_quality_t=T, confounders_x=X
    )
    assert dml_res.sample_size_n == N
    print(f"✅ Double ML Causal ATE: theta = {dml_res.average_treatment_effect_ate:.4f} (95% CI: {dml_res.confidence_interval_95})")


def test_dynamic_pricing_bandit():
    print("\n--- Test 5: Monotonic Dynamic Pricing Bandit & Volatility Clamp ---")
    bandit = MonotonicDynamicPricingBandit(
        candidate_prices=[80.0, 90.0, 100.0, 110.0, 120.0],
        unit_cost=50.0,
        floor_price=70.0,
        ceiling_price=130.0,
    )

    rec = bandit.recommend_optimal_price(current_price=100.0)
    # Check max +/- 5% change from 100.0 -> price between 95.0 and 105.0
    assert 95.0 <= rec.recommended_price <= 105.0
    print(f"✅ Monotonic Dynamic Pricing Recommended: ${rec.recommended_price} (Prev: ${rec.previous_price}, Profit: ${rec.expected_profit})")


def test_contribution_margin_calculator():
    print("\n--- Test 6: Multi-Stage Contribution Margin Math (CM1, CM2, CM3) ---")
    report = ContributionMarginCalculator.calculate_multi_stage_margins(
        revenue=100.0,
        cogs=40.0,
        platform_referral_fees=10.0,
        shipping_cost=5.0,
        fulfillment_cost=3.0,
        payment_fees=2.0,
        allocated_ad_spend=10.0,
        returns_reverse_logistics=2.0,
        promos_coupons=3.0,
        category="beauty_wellness",
    )

    # CM1 = 100 - 40 - 10 = 50 (50%)
    # CM2 = 50 - (5 + 3 + 2) = 40 (40%)
    # CM3 = 40 - (10 + 2 + 3) = 25 (25%)
    assert report.cm1_amount == 50.0 and report.cm1_ratio_pct == 50.0
    assert report.cm2_amount == 40.0 and report.cm2_ratio_pct == 40.0
    assert report.cm3_amount == 25.0 and report.cm3_ratio_pct == 25.0
    print(f"✅ Multi-Stage Margins verified: CM1={report.cm1_ratio_pct}%, CM2={report.cm2_ratio_pct}%, CM3={report.cm3_ratio_pct}%")


if __name__ == "__main__":
    test_landed_cost_calculator()
    test_pricing_anomaly_detector()
    test_multimodal_arcface_matcher()
    test_causal_iqa_estimator()
    test_dynamic_pricing_bandit()
    test_contribution_margin_calculator()
    print("\n🎉 ALL PRICING, ANOMALY & CAUSAL ANALYTICS TESTS PASSED SUCCESSFULLY!")
