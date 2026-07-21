# test_report_bilingual_kg.py
from PIL import Image, ImageDraw
import numpy as np


from verification_layer.domain.bilingual_schema import (
    BilingualProductCatalogModel,
    BrandEntity,
    BilingualText,
    BilingualIngredients,
    NutritionFacts,
    ServingSize,
    Macronutrients,
    CarbohydrateFacts,
    FatFacts,
)
from verification_layer.use_cases.atwater_nutrition_verifier import AtwaterNutritionVerifier
from verification_layer.use_cases.cylindrical_unwarper import CylindricalUnwarper
from verification_layer.use_cases.gs1_digital_link_parser import GS1DigitalLinkParser
from verification_layer.use_cases.knowledge_graph_resolver import KnowledgeGraphEntityResolver, CatalogKnowledgeGraphNode


def test_atwater_nutrition_verification():
    print("\n--- Test 1: Atwater Energy System & Mass Balance Verification ---")
    # Valid nutrition facts: 100g serving, 10g Protein, 20g Total Carb (2g Fiber), 5g Fat, 0g Alcohol
    # E_est = (4 * 10) + (4 * (20 - 2)) + (9 * 5) + (2 * 2) + (7 * 0) = 40 + 72 + 45 + 4 = 161 kcal
    nutrition_valid = NutritionFacts(
        serving_size=ServingSize(value=100.0, unit="g"),
        calories=161.0,
        macronutrients=Macronutrients(
            proteins=10.0,
            carbohydrates=CarbohydrateFacts(total=20.0, sugar=5.0, fiber=2.0),
            fats=FatFacts(total=5.0, saturated=1.0),
            alcohol=0.0,
        ),
    )

    res_valid = AtwaterNutritionVerifier.verify_nutrition_facts(nutrition_valid)
    assert res_valid.is_valid is True
    assert abs(res_valid.estimated_calories - 161.0) < 0.1
    assert res_valid.deviation_percentage <= 5.0
    assert res_valid.mass_balance_passed is True
    print(f"✅ Valid nutrition verified: {res_valid.estimated_calories} kcal (Nutri-Score: {res_valid.nutri_score_rating})")

    # Invalid nutrition facts: Stated calories 500 kcal vs calculated 161 kcal (deviation > 5%)
    nutrition_invalid = NutritionFacts(
        serving_size=ServingSize(value=100.0, unit="g"),
        calories=500.0,  # False calorie declaration
        macronutrients=Macronutrients(
            proteins=10.0,
            carbohydrates=CarbohydrateFacts(total=20.0, sugar=5.0, fiber=2.0),
            fats=FatFacts(total=5.0, saturated=1.0),
            alcohol=0.0,
        ),
    )

    res_invalid = AtwaterNutritionVerifier.verify_nutrition_facts(nutrition_invalid)
    assert res_invalid.is_valid is False
    assert res_invalid.deviation_percentage > 5.0
    print(f"✅ Invalid nutrition rejected correctly (Deviation: {res_invalid.deviation_percentage:.1f}%).")


def test_cylindrical_unwarper():
    print("\n--- Test 2: Cylindrical Back-Projection Unwarper ---")
    img = Image.new("RGB", (400, 300), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    draw.rectangle([50, 50, 350, 250], fill=(0, 150, 100))

    unwarped_img = CylindricalUnwarper.unwarp_pil(img, focal_length=500.0, radius=300.0)
    assert unwarped_img.size == (400, 300)
    print("✅ Cylindrical label de-warping executed successfully.")


def test_gs1_digital_link_parser():
    print("\n--- Test 3: GS1 Digital Link URI Parser ---")
    raw_link = "https://id.gs1.org/01/06291001000142/10/BATCH2026A/21/SERIAL998"
    parsed = GS1DigitalLinkParser.parse_uri(raw_link)

    assert parsed.is_valid is True
    assert parsed.gtin_14 == "06291001000142"
    assert parsed.batch_lot == "BATCH2026A"
    assert parsed.serial_number == "SERIAL998"
    assert "https://id.gs1.org/01/06291001000142" in parsed.canonical_url
    print(f"✅ GS1 Digital Link parsed: GTIN={parsed.gtin_14}, Batch={parsed.batch_lot}, Serial={parsed.serial_number}")


def test_knowledge_graph_entity_resolution():
    print("\n--- Test 4: Knowledge Graph Entity Resolution & RDF/Turtle Generation ---")
    resolver = KnowledgeGraphEntityResolver()

    # Brand Resolution (Arabic variant -> Canonical English brand)
    brand_res = resolver.resolve_brand("نيدو")
    assert brand_res.canonical_name == "Nido"
    assert brand_res.is_merged is True

    # Ingredient Resolution
    ing_res = resolver.resolve_ingredient("الأسبرتام")
    assert ing_res.canonical_name == "aspartame"
    assert ing_res.is_merged is True

    # RDF/Turtle Generation
    kg_node = CatalogKnowledgeGraphNode(
        gtin="06291001000142",
        brand=brand_res,
        product_name_ar="حليب مجفف نيدو",
        product_name_en="Nido Milk Powder",
        gs1_link="https://id.gs1.org/01/06291001000142",
    )

    turtle_rdf = resolver.generate_rdf_turtle(kg_node)
    assert "@prefix schema:" in turtle_rdf
    assert "ex:manufacturedBy ex:brand_Nido" in turtle_rdf
    print("✅ Knowledge Graph entity resolution & RDF/Turtle ontology generation verified.")


if __name__ == "__main__":
    test_atwater_nutrition_verification()
    test_cylindrical_unwarper()
    test_gs1_digital_link_parser()
    test_knowledge_graph_entity_resolution()
    print("\n🎉 ALL BILINGUAL EXTRACTION & KNOWLEDGE GRAPH TESTS PASSED SUCCESSFULLY!")
