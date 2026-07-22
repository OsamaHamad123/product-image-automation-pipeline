# test_report_nextgen_frontiers.py
"""
Automated Test Runner for the 5 Next-Gen Engineering Frontiers.
Validates 100% test assertions for Speculative Search, Multi-Modal Spec Audit,
Spatial Packaging Density, CAVI Aesthetic Engine, and Spectral Color Fidelity.
"""

import sys
import os
import asyncio
import numpy as np

# Ensure root workspace is in sys.path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from verification_layer.domain.nextgen_models import LabColor, ProductBrandSpecs
from verification_layer.use_cases.speculative_search_engine import SpeculativeSearchEngine, CircuitBreaker
from verification_layer.use_cases.multimodal_spec_audit import (
    MultiModalSpecAuditUseCase,
    calculate_levenshtein_distance,
    calculate_normalized_levenshtein_similarity,
    calculate_iou
)
from verification_layer.use_cases.spatial_packaging_density import (
    SpatialPackagingDensityUseCase,
    graham_scan_convex_hull,
    calculate_shoelace_area
)
from verification_layer.use_cases.cavi_aesthetic_engine import (
    CAVIAestheticEngineUseCase,
    compute_laplacian_variance,
    calculate_color_entropy
)
from verification_layer.use_cases.spectral_color_fidelity import (
    SpectralColorFidelityUseCase,
    calculate_ciede2000,
    rgb_to_lab
)


def print_banner(title: str):
    print(f"\n=======================================================")
    print(f"🚀 {title}")
    print(f"=======================================================")


async def test_frontier_1_speculative_search():
    print_banner("TEST 1: Speculative Search Pipeline & Circuit Breaker")
    engine = SpeculativeSearchEngine(rrf_k=60)

    # 1. Normal execution with RRF Fusion
    res1 = await engine.execute_speculative_search("kettle_1.5L")
    assert res1.source == "speculative_rrf_fusion", f"Expected fusion source, got {res1.source}"
    assert res1.consensus_score > 0.0, f"Expected positive consensus score, got {res1.consensus_score}"
    assert len(res1.candidates) > 0, "Expected candidates list to be non-empty"
    print(f"  ✅ RRF Fusion Ranking Success: Top candidate = {res1.candidates[0]}")

    # 2. Circuit Breaker Failure & Fallback
    res2 = await engine.execute_speculative_search(
        "kettle_1.5L",
        fallback_gtin="629104100999",
        fail_vector=True,
        fail_lexical=True
    )
    assert res2.source == "gtin_barcode_fallback", f"Expected GTIN fallback, got {res2.source}"
    assert res2.consensus_score == 1.0, f"Expected fallback score 1.0, got {res2.consensus_score}"
    assert "gtin_direct_629104100999" in res2.candidates[0][0], "Expected GTIN candidate"
    print(f"  ✅ Circuit Breaker & GTIN Fallback Success: {res2.candidates[0]}")


def test_frontier_2_multimodal_spec_audit():
    print_banner("TEST 2: Multi-Modal Spec-Visual Consistency Audit")
    
    # 1. Test Levenshtein distance & similarity
    dist = calculate_levenshtein_distance("American Garden", "Amercan Gardn")
    sim = calculate_normalized_levenshtein_similarity("American Garden", "Amercan Gardn")
    assert dist == 2, f"Expected distance 2, got {dist}"
    assert sim >= 0.85, f"Expected similarity >= 0.85, got {sim}"
    print(f"  ✅ Levenshtein Similarity Match: dist={dist}, sim={sim}")

    # 2. Test IoU Box calculation
    boxA = (10, 10, 100, 100)
    boxB = (20, 20, 100, 100)
    iou = calculate_iou(boxA, boxB)
    assert iou > 0.60, f"Expected IoU > 0.60, got {iou}"
    print(f"  ✅ IoU Box Match: iou={iou}")

    # 3. Test Full Audit Use Case
    audit_uc = MultiModalSpecAuditUseCase(text_threshold=0.75, iou_threshold=0.50)
    audit_res = audit_uc.audit_spec_consistency(
        target_spec_text="1.5L Stainless Steel Kettle",
        detected_text_boxes=[
            {"text": "1.5L Stainless Steel Kettle", "box": (15, 15, 105, 105)}
        ],
        expected_bounding_box=(10, 10, 100, 100)
    )
    assert audit_res.is_consistent is True, f"Expected spec audit consistency True, got {audit_res.is_consistent}"
    print(f"  ✅ Spec Audit Consistency Verified: sim={audit_res.text_similarity}, iou={audit_res.iou_score}")


def test_frontier_3_spatial_packaging_density():
    print_banner("TEST 3: Spatial Packaging & Volumetric Density Engine")

    # Synthetic binary mask (square box)
    mask = np.zeros((100, 100), dtype=np.uint8)
    mask[20:80, 20:80] = 1

    density_uc = SpatialPackagingDensityUseCase(min_efficiency_ratio=0.45)
    res = density_uc.evaluate_mask_density(mask)

    assert res.is_efficient is True, "Expected packaging ratio to be efficient"
    assert res.packaging_ratio > 0.90, f"Expected ratio > 0.90 for square box, got {res.packaging_ratio}"
    assert res.hull_vertex_count >= 4, f"Expected at least 4 vertices, got {res.hull_vertex_count}"
    print(f"  ✅ Packaging Density Evaluated: ratio={res.packaging_ratio}, status={res.status_label}")


def test_frontier_4_cavi_aesthetic_engine():
    print_banner("TEST 4: Visual Conversion & Aesthetic Viability Index (CAVI)")

    # Synthetic sharp image with background
    np.random.seed(42)
    rgb_img = np.random.randint(50, 200, (100, 100, 3), dtype=np.uint8)

    cavi_uc = CAVIAestheticEngineUseCase(pass_threshold=5.0)
    res = cavi_uc.evaluate_image_cavi(rgb_img)

    assert res.composite_cavi >= 0.0 and res.composite_cavi <= 10.0, "CAVI score must be in range 0-10"
    assert res.focus_variance >= 0.0, "Focus variance must be non-negative"
    assert res.color_entropy >= 0.0, "Color entropy must be non-negative"
    print(f"  ✅ CAVI Score Evaluated: score={res.composite_cavi}/10, rank={res.viability_rank}")


def test_frontier_5_spectral_color_fidelity():
    print_banner("TEST 5: Spectral Delta-E Color Fidelity & Brand Gatekeeper")

    # Convert sRGB to CIELAB
    red_rgb = (255, 0, 0)
    lab_red = rgb_to_lab(red_rgb)
    print(f"  ℹ️ sRGB (255,0,0) -> CIELAB: {lab_red}")

    # Calculate CIEDE2000 distance between identical color
    de_self = calculate_ciede2000(lab_red, lab_red)
    assert de_self == 0.0, f"Expected 0.0 distance for identical color, got {de_self}"

    # Calculate distance between slightly different reds
    lab_red_variant = (lab_red[0] + 1.5, lab_red[1] + 1.0, lab_red[2] - 0.5)
    de_variant = calculate_ciede2000(lab_red, lab_red_variant)
    assert de_variant > 0.0 and de_variant < 3.0, f"Expected small Delta-E, got {de_variant}"
    print(f"  ✅ CIEDE2000 Delta-E Calculated: {de_variant}")

    # Test Brand Gatekeeper Decision
    color_uc = SpectralColorFidelityUseCase()
    specs = ProductBrandSpecs(
        gtin="6291041001234",
        brand_name="TestBrand",
        target_colors=[LabColor(l_star=lab_red[0], a_star=lab_red[1], b_star=lab_red[2], label="Brand Red")],
        allowed_tolerance=3.5
    )
    compliance = color_uc.verify_brand_color_compliance(lab_red_variant, specs)
    assert compliance.approved is True, "Expected brand color compliance to pass"
    print(f"  ✅ Brand Gatekeeper Compliance Approved: decision={compliance.brand_decision}")


def main():
    print_banner("STARTING NEXT-GEN FRONTIERS AUTOMATED TEST SUITE")
    
    # Run Async Test 1
    asyncio.run(test_frontier_1_speculative_search())
    
    # Run Sync Tests 2-5
    test_frontier_2_multimodal_spec_audit()
    test_frontier_3_spatial_packaging_density()
    test_frontier_4_cavi_aesthetic_engine()
    test_frontier_5_spectral_color_fidelity()

    print_banner("🎉 ALL 5 NEXT-GEN FRONTIERS TESTED SUCCESSFULLY (100% PASSED) 🎉")


if __name__ == "__main__":
    main()
