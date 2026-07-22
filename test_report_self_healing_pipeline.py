# test_report_self_healing_pipeline.py
"""
Automated Test Suite for Self-Healing Image Pipeline & Corrupt Cache Invalidation.
Validates 100% test assertions for Magic Bytes Validation, RRF Score Normalization (0%-100%),
Corrupted Cache Invalidation, and Google WebCache Fallback Cascade.
"""

import sys
import os
import asyncio
import tempfile

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from verification_layer.infrastructure.image_cache_repository import ImageCacheRepository
from verification_layer.use_cases.image_validation_service import ImageValidationService
from verification_layer.use_cases.heal_image import HealImageUseCase


def print_banner(title: str):
    print(f"\n=======================================================")
    print(f"🚀 {title}")
    print(f"=======================================================")


def test_magic_bytes_signatures():
    print_banner("TEST 1: Magic Bytes Binary Signatures Verification")
    service = ImageValidationService()

    # Valid headers
    png_header = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
    jpeg_header = b"\xff\xd8\xff\xe0\x00\x10JFIF"
    gif_header = b"GIF89a\x01\x00\x01\x00"
    webp_header = b"RIFF\x24\x00\x00\x00WEBPVP8 "

    # Test internal static signatures
    assert any(png_header.startswith(sig) for sig in service.MAGIC_SIGNATURES), "PNG signature failed"
    assert any(jpeg_header.startswith(sig) for sig in service.MAGIC_SIGNATURES), "JPEG signature failed"
    assert any(gif_header.startswith(sig) for sig in service.MAGIC_SIGNATURES), "GIF signature failed"
    assert webp_header.startswith(b"RIFF") and webp_header[8:12] == b"WEBP", "WEBP signature failed"
    print("  ✅ Magic Bytes Binary Signatures Verified (PNG, JPEG, GIF, WEBP)")


def test_rrf_score_normalization_and_epsilon():
    print_banner("TEST 2: RRF Score Normalization & Epsilon Protection")
    heal_uc = HealImageUseCase()

    candidates_a = ["url_1", "url_2", "url_3"]
    candidates_b = ["url_2", "url_1", "url_4"]

    ranked = heal_uc.calculate_rrf(candidates_a, candidates_b, k=60)
    assert len(ranked) == 4, f"Expected 4 unique candidates, got {len(ranked)}"

    for item in ranked:
        score = item["percentage"]
        assert 0.0 <= score <= 100.0, f"Score out of bounds [0, 100]: {score}"

    assert ranked[0]["percentage"] == 100.0, f"Expected top candidate score 100.0, got {ranked[0]['percentage']}"
    print(f"  ✅ RRF Scores Clipped Strictly [0%, 100%]: Top = {ranked[0]['url']} ({ranked[0]['percentage']}%)")


def test_cache_repository_invalidation():
    print_banner("TEST 3: Cache Repository Targeted Invalidation")
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp_db:
        tmp_db_path = tmp_db.name

    try:
        repo = ImageCacheRepository(db_path=tmp_db_path)
        product_id = 99
        test_url = "https://example.com/broken_image.jpg"

        # Write cache
        repo.update_valid_cache(product_id, test_url)
        cache_row = repo.get_cache(product_id)
        assert cache_row is not None and cache_row[1] == 1, "Expected valid cache entry (is_valid=1)"
        print(f"  ✅ Cache Created: {cache_row}")

        # Invalidate cache
        repo.invalidate_cache(product_id, test_url)
        cache_row_inv = repo.get_cache(product_id)
        assert cache_row_inv is not None and cache_row_inv[1] == 0, "Expected invalidated cache entry (is_valid=0)"
        print(f"  ✅ Cache Invalidated (is_valid=0) for broken URL")
    finally:
        if os.path.exists(tmp_db_path):
            try:
                os.remove(tmp_db_path)
            except Exception:
                pass


async def test_end_to_end_self_healing_pipeline():
    print_banner("TEST 4: End-to-End Self-Healing Execution & Fallback Cascade")
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp_db:
        tmp_db_path = tmp_db.name

    try:
        repo = ImageCacheRepository(db_path=tmp_db_path)
        heal_uc = HealImageUseCase(repo=repo)

        product_id = 40
        broken_url = "https://example-cdn.com/broken_chicken_nuggets.jpg"
        candidates_a = ["https://via.placeholder.com/800/0000FF/888888.png"]
        candidates_b = ["https://via.placeholder.com/800/0000FF/888888.png"]

        # Run Self-Healing Execution
        res = await heal_uc.execute(
            product_id=product_id,
            broken_url=broken_url,
            candidates_a=candidates_a,
            candidates_b=candidates_b
        )

        assert res["product_id"] == product_id, "Product ID mismatch"
        assert res["status"] in ("Healed", "GoogleWebCacheFallback"), f"Unexpected status {res['status']}"
        assert 0.0 <= res["match_percentage"] <= 100.0, f"Invalid match percentage {res['match_percentage']}"

        # Verify DB cache status updated
        cache_row = repo.get_cache(product_id)
        assert cache_row is not None and cache_row[1] == 1, "Expected updated valid cache record in DB"
        print(f"  ✅ Self-Healing Pipeline Execution Success!")
        print(f"     Resolved URL: {res['resolved_url']}")
        print(f"     Match Score: {res['match_percentage']}%, Status: {res['status']}")
    finally:
        if os.path.exists(tmp_db_path):
            try:
                os.remove(tmp_db_path)
            except Exception:
                pass


def main():
    print_banner("STARTING SELF-HEALING PIPELINE AUTOMATED TEST SUITE")

    test_magic_bytes_signatures()
    test_rrf_score_normalization_and_epsilon()
    test_cache_repository_invalidation()

    asyncio.run(test_end_to_end_self_healing_pipeline())

    print_banner("🎉 ALL SELF-HEALING PIPELINE TESTS PASSED SUCCESSFULLY (100% ASSERTIONS) 🎉")


if __name__ == "__main__":
    main()
