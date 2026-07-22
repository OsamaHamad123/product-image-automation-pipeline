# test_report_full_blueprint_audit.py
"""
Automated Test Suite for Executive Engineering Blueprint Integration.
Validates 100% test assertions for Hasler-Süsstrunk Colorfulness, Watermark Detector,
GTIN-13 Checksum, Magic Bytes Decoder, Vector Semantic Cache, Bilingual Reranker,
and ProcessCatalogAuditUseCase Master Pipeline.
"""

import sys
import os
import numpy as np
from PIL import Image

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from verification_layer.domain.nextgen_models import Product, SearchCandidate
from verification_layer.use_cases.hasler_susstrunk_colorfulness import (
    calculate_hasler_susstrunk_colorfulness,
    evaluate_colorfulness_compliance
)
from verification_layer.use_cases.watermark_detector import WatermarkDetector
from verification_layer.use_cases.gtin_checksum_verifier import HybridBarcodeEngine
from verification_layer.use_cases.vector_semantic_cache import VectorSemanticCache
from verification_layer.use_cases.bilingual_reranker import BilingualReranker
from verification_layer.infrastructure.resilient_image_fetcher import ResilientImageFetcher
from verification_layer.use_cases.process_catalog_audit_pipeline import ProcessCatalogAuditUseCase


def print_banner(title: str):
    print(f"\n=======================================================")
    print(f"🚀 {title}")
    print(f"=======================================================")


def test_hasler_susstrunk_colorfulness():
    print_banner("TEST 1: Hasler-Süsstrunk Colorfulness Metric")
    # Synthetic RGB image
    np.random.seed(42)
    rgb_arr = np.random.randint(50, 200, (100, 100, 3), dtype=np.uint8)
    img_pil = Image.fromarray(rgb_arr)

    colorfulness = calculate_hasler_susstrunk_colorfulness(img_pil)
    eval_res = evaluate_colorfulness_compliance(colorfulness)

    assert colorfulness > 0.0, f"Expected colorfulness > 0.0, got {colorfulness}"
    assert eval_res["is_compliant"] is True, f"Expected colorfulness compliance True, got {eval_res}"
    print(f"  ✅ Hasler-Süsstrunk Metric: {colorfulness}, Status: {eval_res['status']}")


def test_watermark_detector():
    print_banner("TEST 2: High-Pass Background Watermark Detector")
    # Clean background image (>240)
    clean_bg = Image.new("L", (100, 100), color=250)
    has_wm_clean = WatermarkDetector.has_watermark(clean_bg)
    assert has_wm_clean is False, "Expected clean background to have no watermark"
    print(f"  ✅ Clean Background Verified: watermark={has_wm_clean}")

    # Background with high-frequency noise watermark
    noisy_bg_arr = np.full((100, 100), 250, dtype=np.uint8)
    noisy_bg_arr[10:90:2, 10:90:2] = 50  # Grid pattern noise
    noisy_bg = Image.fromarray(noisy_bg_arr)
    has_wm_noisy = WatermarkDetector.has_watermark(noisy_bg)
    assert has_wm_noisy is True, "Expected noisy grid background to trigger watermark detector"
    print(f"  ✅ Watermark Pattern Detected: watermark={has_wm_noisy}")


def test_gtin_checksum_verifier():
    print_banner("TEST 3: GTIN-13 Checksum Verifier")
    # Valid EAN-13 barcode: sum=51, check_digit=9
    valid_gtin = "6291041001239"
    invalid_gtin = "6291041001234"  # Wrong check digit

    assert HybridBarcodeEngine.is_valid_gtin13(valid_gtin) is True, f"Expected GTIN {valid_gtin} to be valid"
    assert HybridBarcodeEngine.is_valid_gtin13(invalid_gtin) is False, f"Expected GTIN {invalid_gtin} to be invalid"
    print(f"  ✅ GTIN-13 Checksum Verification Passed: Valid={valid_gtin}, Invalid={invalid_gtin}")


def test_magic_bytes_decoder():
    print_banner("TEST 4: Resilient Magic Bytes Format Decoder")
    fetcher = ResilientImageFetcher()

    jpeg_bytes = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01"
    png_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
    webp_bytes = b"RIFF\x00\x00\x00\x00WEBPVP8 "
    avif_bytes = b"\x00\x00\x00\x1cftypavif\x00\x00\x00\x00"

    assert fetcher.decode_magic_bytes(jpeg_bytes) == "image/jpeg", "Expected image/jpeg"
    assert fetcher.decode_magic_bytes(png_bytes) == "image/png", "Expected image/png"
    assert fetcher.decode_magic_bytes(webp_bytes) == "image/webp", "Expected image/webp"
    assert fetcher.decode_magic_bytes(avif_bytes) == "image/avif", "Expected image/avif"
    print("  ✅ Magic Bytes Decoding Passed for JPEG, PNG, WebP, AVIF")


def test_vector_semantic_cache_and_reranker():
    print_banner("TEST 5: Vector Semantic Query Cache & Bilingual Reranker")
    cache = VectorSemanticCache(similarity_threshold=0.90)

    # Arabic normalization test
    norm = cache.normalize_text("زَيْتُ زَيْتُونٍ رَحْمَة 500 مل")
    assert "زيت زيتون رحمه 500 مل" in norm, f"Expected normalized text, got {norm}"
    print(f"  ✅ Arabic Text Normalization: '{norm}'")

    # Jaccard similarity cache store and lookup
    candidates = [
        SearchCandidate(document_id="p1", title="زيت زيتون رحمة 500 مل", score=0.85, source="vector"),
        SearchCandidate(document_id="p2", title="زيت عباد الشمس 1 لتر", score=0.40, source="lexical")
    ]
    cache.store("زيت زيتون رحمة 500 مل", candidates)
    cached_hit = cache.lookup("زيت زيتون رحمه 500مل")
    assert cached_hit is not None, "Expected semantic cache hit"
    print(f"  ✅ Vector Semantic Cache Hit: Found {len(cached_hit)} candidates")

    # Bilingual Reranker test
    reranker = BilingualReranker()
    reranked = reranker.rerank("زيت زيتون رحمة", candidates)
    assert reranked[0].document_id == "p1", "Expected p1 to rank first"
    print(f"  ✅ Bilingual Reranking Passed: Top item = {reranked[0].title} (score={reranked[0].score})")


def test_master_catalog_audit_pipeline():
    print_banner("TEST 6: ProcessCatalogAuditUseCase Master Pipeline")

    # Create synthetic product image with white background & object
    img_arr = np.full((200, 200, 3), 255, dtype=np.uint8)
    img_arr[30:170, 30:170] = [200, 50, 50]  # Red product box
    img_arr[150:175, 40:160] = [180, 180, 180]  # Natural shadow region (160-235)
    img_pil = Image.fromarray(img_arr)

    prod = Product(
        sku="SKU-KETTLE-99",
        title_ar="غلاية ماء 1.5 لتر",
        title_en="1.5L Water Kettle",
        specifications={"capacity_l": 1.5},
        expected_brand="Rahma",
        expected_colors_lab=[[50.0, 20.0, -10.0]]
    )

    pipeline = ProcessCatalogAuditUseCase()
    res = pipeline.audit_image_pil(img_pil, prod)

    assert res.success is True, "Expected audit pipeline success True"
    assert res.colorfulness > 0.0, "Expected colorfulness score"
    assert res.sharpness >= 0.0, "Expected sharpness score"
    assert res.shadow_preserved is True, "Expected shadow preservation True"
    assert res.spatial_packaging_ratio > 0.40, "Expected packaging ratio > 0.40"
    assert res.aesthetic_score > 0.0, "Expected aesthetic score > 0.0"
    assert res.live_metrics["execution_latency_ms"] < 100.0, "Expected CPU latency < 100ms"
    print(f"  ✅ Master Catalog Audit Pipeline Success!")
    print(f"     Metrics: Colorfulness={res.colorfulness}, Sharpness={res.sharpness}, PackagingRatio={res.spatial_packaging_ratio}")
    print(f"     Aesthetic Score={res.aesthetic_score}, ShadowPreserved={res.shadow_preserved}, Latency={res.live_metrics['execution_latency_ms']}ms")


def main():
    print_banner("STARTING FULL BLUEPRINT AUTOMATED TEST SUITE")

    test_hasler_susstrunk_colorfulness()
    test_watermark_detector()
    test_gtin_checksum_verifier()
    test_magic_bytes_decoder()
    test_vector_semantic_cache_and_reranker()
    test_master_catalog_audit_pipeline()

    print_banner("🎉 ALL BLUEPRINT TESTS PASSED SUCCESSFULLY (100% ASSERTIONS) 🎉")


if __name__ == "__main__":
    main()
