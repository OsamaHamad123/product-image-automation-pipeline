# test_verification_pipeline.py
from PIL import Image, ImageDraw
import numpy as np


from verification_layer.domain.models import (
    CatalogProduct,
    CatalogType,
    SurfaceCurvature,
    OperationalDecision,
)
from verification_layer.use_cases.bilingual_text_processor import BilingualTextProcessor
from verification_layer.use_cases.string_distance_calculator import StringDistanceCalculator
from verification_layer.use_cases.resolution_aspect_gate import ResolutionAspectGate
from verification_layer.use_cases.white_background_gate import PureWhiteBackgroundGate
from verification_layer.use_cases.content_representation_gate import ContentRepresentationGate
from verification_layer.use_cases.fusion_engine import MultiModalFusionEngine
from verification_layer.use_cases.catalog_verifier import CatalogVerificationPipeline


def test_bilingual_numeral_translation():
    print("\n--- Test 1: Eastern Arabic Numeral Translation ---")
    raw_ar = "الوزن الصافي ٨٠٠ غرام والحجم ١.٥ لتر"
    translated = BilingualTextProcessor.convert_eastern_arabic_numerals(raw_ar)
    assert "800" in translated
    assert "1.5" in translated
    print(f"✅ Translated: '{raw_ar}' -> '{translated}'")


def test_metric_normalization():
    print("\n--- Test 2: Bilingual Metric Extraction & Normalization ---")
    m1 = BilingualTextProcessor.extract_and_normalize_metric("0.8 kg bottle")
    assert m1 is not None and m1.numeric_value == 800.0 and m1.unit.value == "g"

    m2 = BilingualTextProcessor.extract_and_normalize_metric("٨٠٠ غرام")
    assert m2 is not None and m2.numeric_value == 800.0 and m2.unit.value == "g"

    m3 = BilingualTextProcessor.extract_and_normalize_metric("١.٥ لتر")
    assert m3 is not None and m3.numeric_value == 1500.0 and m3.unit.value == "ml"

    comp = BilingualTextProcessor.compare_metrics("0.8kg", "٨٠٠ جرام")
    assert comp == 1.0
    print("✅ Metric extraction & normalization verified.")


def test_string_distance_metrics():
    print("\n--- Test 3: String Distance Math (Levenshtein & Jaro-Winkler) ---")
    # Levenshtein
    lev = StringDistanceCalculator.length_normalized_levenshtein("Pepsi Cola", "Pepsi Colz")
    assert lev >= 0.90, f"Expected lev >= 0.90, got {lev}"

    # Jaro-Winkler
    jw = StringDistanceCalculator.jaro_winkler_similarity("Nestle Pure Life", "Nestle Pure Life")
    assert jw == 1.0

    match_flat = StringDistanceCalculator.is_brand_matched("Nido Fortified", "Nido Fortified", SurfaceCurvature.FLAT)
    assert match_flat is True
    print("✅ String distance math algorithms verified.")


def test_structural_resolution_gate():
    print("\n--- Test 4: Resolution & Aspect Ratio Gate ---")
    gate = ResolutionAspectGate(min_resolution=1000)

    # Valid image: 1200x1200
    valid_img = Image.new("RGB", (1200, 1200), "white")
    eval_res = gate.evaluate(valid_img)
    assert eval_res.passed is True

    # Invalid image: small resolution 400x400
    small_img = Image.new("RGB", (400, 400), "white")
    eval_small = gate.evaluate(small_img)
    assert eval_small.passed is False
    print("✅ Structural resolution gate verified.")


def test_white_background_and_fill_ratio_gate():
    print("\n--- Test 5: Pure White Background & Fill Ratio Gate ---")
    gate = PureWhiteBackgroundGate(border_width=10, min_purity=0.98, min_fill_ratio=0.50)

    # Create white image with centered blue retail box occupying 80% area
    img = Image.new("RGB", (1000, 1000), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    draw.rectangle([100, 100, 900, 900], fill=(0, 100, 200))

    eval_bg = gate.evaluate(img)
    assert eval_bg.passed is True
    assert eval_bg.details["border_purity"] >= 0.98
    print(f"✅ White background gate passed with purity {eval_bg.details['border_purity']*100:.1f}%.")


def test_score_fusion_math():
    print("\n--- Test 6: Multi-Modal Fusion Score Math ---")
    # S_fusion = (0.4 * S_vis + 0.6 * S_ocr) * S_vlm
    s_vis = 0.90
    s_ocr = 0.95
    s_vlm = 1.0

    s_fusion = MultiModalFusionEngine.calculate_fusion_score(s_vis, s_ocr, s_vlm)
    # Expected: (0.4 * 0.90 + 0.6 * 0.95) * 1.0 = (0.36 + 0.57) * 1.0 = 0.93
    assert abs(s_fusion - 0.93) < 0.001
    decision = MultiModalFusionEngine.determine_decision(s_fusion)
    assert decision == OperationalDecision.AUTO_APPROVE
    print(f"✅ Fusion score: {s_fusion} -> Decision: {decision.value}")


def test_end_to_end_verification_pipeline():
    print("\n--- Test 7: End-to-End Verification Pipeline ---")
    pipeline = CatalogVerificationPipeline()

    img = Image.new("RGB", (1600, 1600), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    draw.rectangle([50, 50, 1550, 1550], fill=(20, 80, 180))


    catalog = CatalogProduct(
        asin_or_gtin="B08N5WRWNW",
        brand="Nido",
        product_class="Milk Powder",
        weight_volume="800g",
        catalog_type=CatalogType.GLOBAL_BRAND,
        surface_curvature=SurfaceCurvature.FLAT,
    )

    result = pipeline.verify(img, catalog)
    print(f"DEBUG Result: passed={result.overall_passed}, decision={result.decision}, fusion={result.fusion_score}, rejections={result.rejection_reasons}")
    for g in result.gate_evaluations:
        print(f"  Gate: {g.gate_name}, passed={g.passed}, score={g.score}, reason={g.reason}")
    assert result.overall_passed is True

    assert result.decision in (OperationalDecision.AUTO_APPROVE, OperationalDecision.MANUAL_REVIEW)
    print(f"✅ End-to-end pipeline decision: {result.decision.value} (Fusion Score: {result.fusion_score})")


if __name__ == "__main__":
    test_bilingual_numeral_translation()
    test_metric_normalization()
    test_string_distance_metrics()
    test_structural_resolution_gate()
    test_white_background_and_fill_ratio_gate()
    test_score_fusion_math()
    test_end_to_end_verification_pipeline()
    print("\n🎉 ALL VERIFICATION PIPELINE TESTS PASSED SUCCESSFULLY!")
