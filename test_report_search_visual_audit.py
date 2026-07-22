# test_report_search_visual_audit.py
"""
Automated Test Suite for Search Engine & Visual Audit Architecture.
Validates 100% test assertions for Search Intent Resolution, SERP Schema Extraction & MAS,
Adaptive Proxy Pool Circuit Breaker, Byte-Range Image Parsing (with EXIF Orientation),
and Catalog Visual Audit Orchestrator.
"""

import sys
import os
import struct
import time
from typing import Tuple

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from verification_layer.domain.nextgen_models import IntentType, ProductVisualProfile, ProxyState
from verification_layer.use_cases.search_intent_resolver import SearchIntentResolverUseCase
from verification_layer.use_cases.serp_schema_extractor import SERPProductSchemaExtractor
from verification_layer.use_cases.byte_range_image_parser import ByteRangeImageParser, AspectRatioFilterUseCase
from verification_layer.infrastructure.adaptive_proxy_pool import AdaptiveProxyPool
from verification_layer.use_cases.catalog_visual_audit_orchestrator import CatalogVisualAuditOrchestrator


def print_banner(title: str):
    print(f"\n=======================================================")
    print(f"🚀 {title}")
    print(f"=======================================================")


def construct_mock_png(width: int, height: int) -> bytes:
    signature = b'\x89PNG\r\n\x1a\n'
    ihdr_chunk_len = b'\x00\x00\x00\r'
    ihdr_type = b'IHDR'
    dimensions_chunk = struct.pack('>II', width, height)
    other_ihdr_bytes = b'\x08\x06\x00\x00\x00'
    crc_dummy = b'\x11\x22\x33\x44'
    return signature + ihdr_chunk_len + ihdr_type + dimensions_chunk + other_ihdr_bytes + crc_dummy


def construct_mock_gif(width: int, height: int) -> bytes:
    signature = b'GIF89a'
    dimensions_chunk = struct.pack('<HH', width, height)
    return signature + dimensions_chunk + b'\x00\x00\x00'


def test_search_intent_resolver():
    print_banner("TEST 1: Search Intent Resolver (Single vs Multi-Pack/Bundle)")
    resolver = SearchIntentResolverUseCase()

    # Test Bundle Query Intent
    q_bundle = resolver.resolve_query_intent("كرتونة مياه صحية 12 حبة")
    assert q_bundle.intent == IntentType.MULTI_PACK, f"Expected BUNDLE intent, got {q_bundle.intent}"
    assert q_bundle.parsed_units == 12, f"Expected 12 units, got {q_bundle.parsed_units}"
    print(f"  ✅ Bundle Intent Resolved: intent={q_bundle.intent.value}, units={q_bundle.parsed_units}")

    # Test Single Unit Query Intent
    q_single = resolver.resolve_query_intent("زجاجة مياه غازية 500 مل")
    assert q_single.intent == IntentType.SINGLE_UNIT, f"Expected SINGLE intent, got {q_single.intent}"
    assert q_single.parsed_units == 1, f"Expected 1 unit, got {q_single.parsed_units}"
    print(f"  ✅ Single Unit Intent Resolved: intent={q_single.intent.value}, units={q_single.parsed_units}")

    # Test Visual Match Penalties
    single_profile = ProductVisualProfile(
        product_id="P1", title="Single Bottle", is_bundle_packaging=False, unit_count=1, aspect_ratio=0.6
    )
    bundle_profile = ProductVisualProfile(
        product_id="P2", title="Water Case", is_bundle_packaging=True, unit_count=12, aspect_ratio=1.0
    )

    score_mismatch = resolver.score_visual_match(q_bundle, single_profile)
    assert score_mismatch == 0.02, f"Expected mismatch score penalty 0.02, got {score_mismatch}"

    score_match = resolver.score_visual_match(q_bundle, bundle_profile)
    assert score_match >= 1.5, f"Expected matched score >= 1.5, got {score_match}"
    print(f"  ✅ Visual Match Scoring Penalties & Bonuses Verified (Mismatch=0.02, Match={score_match})")


def test_serp_schema_extractor():
    print_banner("TEST 2: SERP Schema Extractor & Merchant Authority Score (MAS)")
    extractor = SERPProductSchemaExtractor()

    json_ld_schema = """
    {
        "@context": "https://schema.org/",
        "@type": "Product",
        "name": "مياه معبأة كرتونة 12 زجاجة",
        "image": "https://cdn.shopify.com/s/files/1/001/water_pack.png",
        "gtin": "6281000102030",
        "brand": "Aquafina",
        "offers": {
            "@type": "Offer",
            "price": "24.50",
            "priceCurrency": "SAR"
        }
    }
    """

    parsed = extractor.parse_json_ld_schema(json_ld_schema)
    assert parsed["has_product_schema"] is True, "Expected Product schema to be found"
    assert parsed["gtin"] == "6281000102030", f"Expected GTIN 6281000102030, got {parsed['gtin']}"
    assert parsed["brand"] == "Aquafina", f"Expected brand Aquafina, got {parsed['brand']}"
    assert parsed["completeness_score"] >= 0.8, f"Expected high completeness score, got {parsed['completeness_score']}"
    print(f"  ✅ JSON-LD Product Schema Parsed: GTIN={parsed['gtin']}, Completeness={parsed['completeness_score']}")

    # CDN Tier Evaluation
    cdn_tier_shopify = extractor.evaluate_cdn_tier("https://cdn.shopify.com/s/files/1/001/img.png")
    cdn_tier_magento = extractor.evaluate_cdn_tier("https://store.com/media/catalog/product/a/b/img.jpg")
    cdn_tier_generic = extractor.evaluate_cdn_tier("https://generic-host.com/images/img.jpg")

    assert cdn_tier_shopify == 1.0, "Shopify CDN tier must be 1.0"
    assert cdn_tier_magento == 0.8, "Magento CDN tier must be 0.8"
    assert cdn_tier_generic == 0.3, "Generic CDN tier must be 0.3"
    print("  ✅ CDN Tier Evaluations Verified (Shopify=1.0, Magento=0.8, Generic=0.3)")

    mas = extractor.calculate_merchant_authority(
        page_url="https://healthy-shop.myshopify.com/products/water-12",
        image_url="https://cdn.shopify.com/s/files/1/001/water_pack.png",
        schema_completeness=parsed["completeness_score"]
    )
    assert mas >= 0.80, f"Expected high MAS >= 0.80 for Shopify store, got {mas}"
    print(f"  ✅ Merchant Authority Score (MAS) Calculated: MAS={mas}")


def test_adaptive_proxy_pool_circuit_breaker():
    print_banner("TEST 3: Adaptive Proxy Pool & Circuit Breaker")
    proxy_urls = ["http://12.34.56.78:3128", "http://98.76.54.32:8080"]
    pool = AdaptiveProxyPool(proxy_urls, failure_threshold=2, cooldown_period=0.2)

    node1 = pool.nodes[0]
    assert node1.state == ProxyState.CLOSED, "Initial proxy state must be CLOSED"

    # Record failures to trip circuit breaker
    pool.record_failure(node1, 429)
    pool.record_failure(node1, 429)
    assert node1.state == ProxyState.OPEN, f"Expected state OPEN after threshold failures, got {node1.state}"
    print("  ✅ Proxy Circuit Breaker Tripped to OPEN State")

    # Fast-forward cooldown to test HALF-OPEN state
    time.sleep(0.25)
    next_node = pool.get_next_proxy()
    assert next_node.url == node1.url and next_node.state == ProxyState.HALF_OPEN, "Expected proxy to transition to HALF_OPEN"
    print("  ✅ Proxy Transitioned to HALF_OPEN after cooldown")

    # Record success in HALF-OPEN state -> CLOSED
    pool.record_success(node1, 0.05)
    assert node1.state == ProxyState.CLOSED, "Expected proxy state to recover to CLOSED"
    print("  ✅ Proxy Recovered to CLOSED State")


def test_byte_range_image_parser_and_filter():
    print_banner("TEST 4: Byte-Range Image Parser & Aspect Ratio Filter")

    # Test PNG dimensions from mock bytes
    png_bytes = construct_mock_png(600, 400)
    png_dims = ByteRangeImageParser.get_dimensions(png_bytes)
    assert png_dims == (600, 400), f"Expected PNG dimensions (600, 400), got {png_dims}"
    print(f"  ✅ PNG Byte-Range Dimensions Parsed: {png_dims}")

    # Test GIF dimensions from mock bytes
    gif_bytes = construct_mock_gif(350, 350)
    gif_dims = ByteRangeImageParser.get_dimensions(gif_bytes)
    assert gif_dims == (350, 350), f"Expected GIF dimensions (350, 350), got {gif_dims}"
    print(f"  ✅ GIF Byte-Range Dimensions Parsed: {gif_dims}")

    # Test Aspect Ratio Filter
    aspect_filter = AspectRatioFilterUseCase(min_width=300, min_height=300)
    valid_size, _ = aspect_filter.is_valid_commercial_image(600, 400)
    invalid_small, _ = aspect_filter.is_valid_commercial_image(50, 50)
    invalid_aspect, _ = aspect_filter.is_valid_commercial_image(1200, 100)

    assert valid_size is True, "Expected (600, 400) image to be valid"
    assert invalid_small is False, "Expected (50, 50) thumbnail image to be rejected"
    assert invalid_aspect is False, "Expected (1200, 100) banner image to be rejected"
    print("  ✅ Aspect Ratio & Commercial Resolution Filters Verified")


def test_catalog_visual_audit_orchestrator():
    print_banner("TEST 5: Catalog Visual Audit Orchestrator")
    orchestrator = CatalogVisualAuditOrchestrator()

    shopify_schema = """
    {
        "@context": "https://schema.org/",
        "@type": "Product",
        "name": "مياه معبأة كرتونة 12 زجاجة",
        "image": "https://cdn.shopify.com/s/files/1/001/water_pack.png",
        "gtin": "6281000102030",
        "brand": "Aquafina"
    }
    """
    mock_png = construct_mock_png(600, 600)

    res = orchestrator.audit_serp_product_image(
        user_query_text="كرتونة مياه صحية 12 حبة",
        page_url="https://healthy-shop.myshopify.com/products/water-12",
        image_url="https://cdn.shopify.com/s/files/1/001/water_pack.png",
        pre_fetched_bytes=mock_png,
        json_ld_schema_str=shopify_schema
    )

    assert res["decision"] == "ACCEPT", f"Expected ACCEPT decision, got {res['decision']}"
    assert res["detected_intent"] == "BUNDLE", f"Expected BUNDLE intent, got {res['detected_intent']}"
    assert res["units_matched"] == 12, f"Expected 12 units matched, got {res['units_matched']}"
    assert res["merchant_authority"] >= 0.80, f"Expected high merchant authority, got {res['merchant_authority']}"
    assert orchestrator.metrics["audits_passed"] == 1, "Expected 1 passed audit in metrics"
    assert orchestrator.metrics["bandwidth_saved_bytes"] > 0, "Expected bandwidth saved in metrics"

    print("  ✅ Catalog Visual Audit Orchestrator Verified!")
    print(f"     Decision={res['decision']}, Intent={res['detected_intent']}, MatchingScore={res['matching_score']}, MAS={res['merchant_authority']}")
    print(f"     Bandwidth Saved={orchestrator.metrics['bandwidth_saved_bytes']} bytes")


def main():
    print_banner("STARTING SEARCH & VISUAL AUDIT AUTOMATED TEST SUITE")

    test_search_intent_resolver()
    test_serp_schema_extractor()
    test_adaptive_proxy_pool_circuit_breaker()
    test_byte_range_image_parser_and_filter()
    test_catalog_visual_audit_orchestrator()

    print_banner("🎉 ALL SEARCH & VISUAL AUDIT TESTS PASSED SUCCESSFULLY (100% ASSERTIONS) 🎉")


if __name__ == "__main__":
    main()
