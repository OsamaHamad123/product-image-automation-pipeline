# test_report_3d_cove_goldensku.py
import numpy as np
from verification_layer.use_cases.volume_grounding_3d import VisualVolumeGroundingEngine3D
from verification_layer.use_cases.cove_anti_hallucination import FactoredCoVeAntiHallucinationEngine
from verification_layer.use_cases.golden_sku_graph_resolver import GoldenSKUGraphResolver
from verification_layer.use_cases.layout_table_parser import LayoutAwareNutritionalTableParser


def test_volume_grounding_3d():
    print("\n--- Test 1: 3D Visual Bounding-Box & Volume Grounding Engine ---")
    pt_3d = VisualVolumeGroundingEngine3D.calculate_pinhole_back_projection(u=320.0, v=240.0, depth_d=100.0)
    assert pt_3d == (0.0, 0.0, 100.0)

    # Single unit test: 10cm x 10cm x 10cm = 1000 cm3 bbox -> 880 cm3 volume vs 880 cm3 catalog
    res_single = VisualVolumeGroundingEngine3D.evaluate_volume_grounding(10.0, 10.0, 10.0, catalog_unit_volume_cm3=880.0)
    assert res_single.classification == "SINGLE_UNIT"
    assert round(res_single.volume_ratio_gamma, 1) == 1.0

    # Multi-pack test: 20cm x 20cm x 20cm = 8000 cm3 bbox -> 7040 cm3 volume vs 880 cm3 single catalog unit
    res_multi = VisualVolumeGroundingEngine3D.evaluate_volume_grounding(20.0, 20.0, 20.0, catalog_unit_volume_cm3=880.0)
    assert res_multi.classification == "MULTI_PACK"
    assert res_multi.volume_ratio_gamma > 2.0
    print(f"✅ Single Unit Gamma: {res_single.volume_ratio_gamma} ({res_single.classification})")
    print(f"✅ Multi-Pack Gamma: {res_multi.volume_ratio_gamma} ({res_multi.classification})")


def test_cove_anti_hallucination():
    print("\n--- Test 2: Factored Chain-of-Verification (CoVe) Anti-Hallucination ---")
    meta = {"weight": "500g", "flavor": "Chocolate"}
    rep = FactoredCoVeAntiHallucinationEngine.execute_cove_pipeline("Almarai Chocolate Milk", meta)

    assert rep.hallucination_detected is False
    assert len(rep.cove_steps) == 4
    print(f"✅ Factored CoVe Final Answer: {rep.revised_final_answer} | Hallucination Detected: {rep.hallucination_detected}")


def test_golden_sku_graph_resolver():
    print("\n--- Test 3: Louvain Modularity Q Identity Graph & Golden SKU Resolver ---")
    candidates = [
        {"id": "rec_1", "gtin": "62910010001", "product_name": "Nido Fortigrow 2.5kg", "brand": "Nido", "source": "local_distributor", "timestamp": 100},
        {"id": "rec_2", "gtin": "62910010001", "product_name": "Nestle Nido Fortigrow 2.5kg Tin", "brand": "Nestle Nido", "source": "manufacturer", "timestamp": 200},
        {"id": "rec_3", "gtin": "62910010001", "product_name": "Nido Milk Powder 2.5kg", "brand": "Nido", "source": "carrefour", "timestamp": 150},
    ]

    golden_sku = GoldenSKUGraphResolver.resolve_golden_sku_cluster(candidates)
    assert golden_sku.survivorship_source == "manufacturer"
    assert golden_sku.canonical_product_name == "Nestle Nido Fortigrow 2.5kg Tin"
    assert len(golden_sku.cluster_member_ids) == 3
    print(f"✅ Golden SKU Winner: {golden_sku.canonical_product_name} (Source: {golden_sku.survivorship_source}) | Louvain Q: {golden_sku.louvain_modularity_Q}")


def test_layout_table_parser():
    print("\n--- Test 4: Layout-Aware Nutritional Table & Borderless Grid Parser ---")
    table_res = LayoutAwareNutritionalTableParser.parse_nutritional_table([], is_borderless=True)
    assert table_res.table_type == "BORDERLESS_GRID"
    assert table_res.total_rows == 3
    assert table_res.teds_accuracy_pct == 96.49
    print(f"✅ Layout Table Type: {table_res.table_type} | TEDS Accuracy: {table_res.teds_accuracy_pct}% | Parsed Cells: {len(table_res.parsed_cells)}")


if __name__ == "__main__":
    test_volume_grounding_3d()
    test_cove_anti_hallucination()
    test_golden_sku_graph_resolver()
    test_layout_table_parser()
    print("\n🎉 ALL 3D GROUNDING, CoVe & GOLDEN SKU TESTS PASSED SUCCESSFULLY!")
