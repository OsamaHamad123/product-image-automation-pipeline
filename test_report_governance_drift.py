# test_report_governance_drift.py
from PIL import Image, ImageDraw
import numpy as np

from verification_layer.use_cases.package_drift_detector import PackageDesignDriftDetector, GovernanceRiskLevel
from verification_layer.use_cases.typographic_defense import TypographicAttackDefender
from verification_layer.use_cases.fellegi_sunter_linker import FellegiSunterRecordLinker, RecordLinkageDecision, BulkUploadFailsafeTriggered
from verification_layer.use_cases.catalog_purity_metrics import CatalogPurityMetricsEngine


def test_package_design_drift_detector():
    print("\n--- Test 1: Package Design Drift Detector & Risk Escalation ---")
    # Case A: Allergen text change -> High Risk (IMMEDIATE_ESCALATION + Auto Rollback)
    res_high = PackageDesignDriftDetector.evaluate_package_drift(
        current_similarity_score=0.70, allergen_text_changed=True
    )
    assert res_high.has_drift is True
    assert res_high.risk_level == GovernanceRiskLevel.IMMEDIATE_ESCALATION
    assert res_high.trigger_auto_rollback is True
    print(f"✅ High Risk Allergen Drift -> Risk: {res_high.risk_level.value} (Auto Rollback: {res_high.trigger_auto_rollback})")

    # Case B: Certification doc update -> Medium Risk (TECHNICAL_REVIEW_HOLD)
    res_med = PackageDesignDriftDetector.evaluate_package_drift(
        current_similarity_score=0.80, certifications_changed=True
    )
    assert res_med.has_drift is True
    assert res_med.risk_level == GovernanceRiskLevel.TECHNICAL_REVIEW_HOLD
    print(f"✅ Medium Risk Cert Drift -> Risk: {res_med.risk_level.value}")

    # Case C: Minor visual design update -> Low Risk (SCHEDULED_QUEUE)
    res_low = PackageDesignDriftDetector.evaluate_package_drift(current_similarity_score=0.82)
    assert res_low.has_drift is True
    assert res_low.risk_level == GovernanceRiskLevel.SCHEDULED_QUEUE
    print(f"✅ Low Risk Visual Drift -> Risk: {res_low.risk_level.value}")


def test_typographic_attack_defense():
    print("\n--- Test 2: Typographic Visual Prompt Attack Defender ---")
    # Image with deceptive text overlay
    img = Image.new("RGB", (500, 500), (240, 240, 240))
    draw = ImageDraw.Draw(img)
    # Draw product object in center
    draw.ellipse([150, 150, 350, 350], fill=(200, 50, 50))
    # Draw deceptive text banner overlay "APPLE" across image
    draw.rectangle([50, 200, 450, 260], fill=(0, 0, 0))

    defended_img = TypographicAttackDefender.apply_dyslexify_masking(img)
    assert defended_img.size == (500, 500)
    print("✅ Typographic attack defense Dyslexify masking applied successfully.")


def test_fellegi_sunter_linker_and_failsafe():
    print("\n--- Test 3: Fellegi-Sunter Record Linker & Bulk Upload Failsafe ---")
    # Matching pair: GTIN, Brand, Product Name, Category match
    matches_gold = {
        "gtin": True,
        "brand": True,
        "product_name": True,
        "weight_volume": True,
        "category": True,
    }
    res_gold = FellegiSunterRecordLinker.evaluate_record_pair(matches_gold)
    assert res_gold.decision == RecordLinkageDecision.GOLD_MATCH
    assert res_gold.total_weight_R >= 8.0
    print(f"✅ Fellegi-Sunter Gold Match (Weight R: {res_gold.total_weight_R}) -> {res_gold.decision.value}")

    # Test Bulk Upload Failsafe (> 50% active catalog modification lock)
    try:
        FellegiSunterRecordLinker.audit_bulk_upload_failsafe(
            total_active_catalog_count=1000, affected_records_count=600, threshold_pct=0.50
        )
        assert False, "Failsafe should have raised BulkUploadFailsafeTriggered!"
    except BulkUploadFailsafeTriggered as ex:
        print(f"✅ Bulk Upload Failsafe caught bulk corruption attack: {ex}")


def test_catalog_purity_metrics_engine():
    print("\n--- Test 4: Catalog Purity Score & Error Rates ---")
    cer = CatalogPurityMetricsEngine.calculate_catalog_error_rate(incorrect_records=10, total_records=1000) # 1.0%
    assert cer == 1.0

    e1 = CatalogPurityMetricsEngine.calculate_value_accuracy_error_rate_e1(value_errors=2, total_evaluated_values=1000) # 0.2%
    assert e1 == 0.2

    # Test Combined Conversion Loss Ratio: L_combined = 1 - prod(1 - r_k)
    # r1 = 0.10 (bad desc), r2 = 0.15 (missing image), r3 = 0.05 (title mismatch)
    # L_combined = 1 - (0.90 * 0.85 * 0.95) = 1 - 0.72675 = 0.27325 -> 27.33%
    l_combined = CatalogPurityMetricsEngine.calculate_combined_conversion_loss([0.10, 0.15, 0.05])
    assert abs(l_combined - 0.2733) < 0.01

    importance_score = CatalogPurityMetricsEngine.calculate_importance_score(
        r_table=800, r_max=1000, c_table=25, d_stream=10, k_shared=5
    )
    assert importance_score > 0.0

    purity_report = CatalogPurityMetricsEngine.generate_catalog_purity_report(
        incorrect_records=10,
        total_records=1000,
        value_errors=2,
        total_values=1000,
        missing_required_fields=0,
        sample_count=100,
        normalization_errors=1,
        individual_loss_ratios=[0.02, 0.01],
    )

    assert purity_report.is_compliant is True
    assert purity_report.catalog_purity_score >= 95.0
    print(f"✅ Catalog Purity Score: {purity_report.catalog_purity_score}% | CER: {purity_report.catalog_error_rate_cer}% | Importance Score: {importance_score}")


if __name__ == "__main__":
    test_package_design_drift_detector()
    test_typographic_attack_defense()
    test_fellegi_sunter_linker_and_failsafe()
    test_catalog_purity_metrics_engine()
    print("\n🎉 ALL GOVERNANCE, DRIFT & anti-TYPOGRAPHIC TESTS PASSED SUCCESSFULLY!")
