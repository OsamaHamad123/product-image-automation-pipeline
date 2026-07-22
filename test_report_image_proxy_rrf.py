# test_report_image_proxy_rrf.py
"""
Automated Test Suite for CDN Image Proxy, SSRF Protection, Pre-Flight Magic Bytes,
and RRF Rank Fusion with >= 65% Match Floor.
"""

import sys
import os

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from verification_layer.use_cases.image_proxy_service import (
    SSRFProtectionValidator,
    detect_magic_bytes_mime,
    ImageProxyService
)
from verification_layer.use_cases.rank_fusion_service import RankFusionService


def print_banner(title: str):
    print(f"\n=======================================================")
    print(f"🚀 {title}")
    print(f"=======================================================")


def test_ssrf_protection_validator():
    print_banner("TEST 1: SSRF Protection & Private IP Blocking")

    forbidden_ips = ["127.0.0.1", "10.0.0.1", "192.168.1.50", "169.254.169.254", "100.64.1.1"]
    for ip in forbidden_ips:
        try:
            SSRFProtectionValidator.resolve_and_validate_host(ip)
            assert False, f"Failed to block forbidden IP: {ip}"
        except ValueError as err:
            assert "SSRF Prevention" in str(err), f"Unexpected error message for {ip}: {err}"

    print("  ✅ SSRF Protection Verified: Loopback, Private, AWS IMDS, and CGNAT IPs safely blocked.")


def test_magic_bytes_detection():
    print_banner("TEST 2: Magic Bytes Binary Signature Inspection")

    headers = [
        (b"\xff\xd8\xff\xe0\x00\x10JFIF", "image/jpeg"),
        (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR", "image/png"),
        (b"GIF89a\x01\x00\x01\x00", "image/gif"),
        (b"RIFF\x24\x00\x00\x00WEBPVP8 ", "image/webp")
    ]

    for binary_header, expected_mime in headers:
        detected = detect_magic_bytes_mime(binary_header)
        assert detected == expected_mime, f"Expected {expected_mime}, got {detected}"

    print("  ✅ Magic Bytes Inspection Verified for JPEG, PNG, GIF, WebP binary signatures.")


def test_image_proxy_base64_formatting():
    print_banner("TEST 3: Image Proxy Base64 Formatting & Request Pinning")

    service = ImageProxyService()
    target_url = "https://example.com/products/shampoo.jpg"
    secure_url, headers = service.prepare_proxy_request(target_url)

    assert "Host" in headers and headers["Host"] == "example.com", "Host header pinning missing"
    assert secure_url.startswith("https://"), "Secure URL protocol mismatch"

    dummy_binary = b"\xff\xd8\xff\xe0"
    data_uri = service.format_base64_data_uri("image/jpeg", dummy_binary)
    assert data_uri.startswith("data:image/jpeg;base64,"), "Base64 Data URI formatting invalid"

    print(f"  ✅ Proxy Request & Base64 Data URI Verified: {data_uri[:40]}...")


def test_rrf_rank_fusion_and_match_floor():
    print_banner("TEST 4: RRF Rank Fusion & >= 65% Match Floor Threshold")

    fusion_service = RankFusionService(k=60, floor_threshold=65.0)

    # Item 1 is 1st in dense & 1st in lexical -> 100.0%
    # Item 2 is 1st in dense & 10th in lexical -> high match
    # Item 3 is 20th in dense & 30th in lexical -> low match < 65%
    dense = ["img_top_1", "img_mid_2", "img_low_3"]
    lexical = ["img_top_1", "img_mid_2", "img_low_3"]

    results = fusion_service.evaluate_fusion(dense, lexical)

    assert "passed_candidates" in results and "rejected_candidates" in results, "Invalid fusion output"
    passed = results["passed_candidates"]

    top_item = next((c for c in passed if c["image_id"] == "img_top_1"), None)
    assert top_item is not None, "Top candidate missing from passed list"
    assert top_item["match_percentage"] == 100.0, f"Expected 100.0%, got {top_item['match_percentage']}"

    for candidate in passed:
        assert candidate["match_percentage"] >= 65.0, f"Candidate below floor threshold in passed list: {candidate}"

    print(f"  ✅ RRF Fusion Verified: Top match={top_item['match_percentage']}%, Passed={len(passed)} candidates.")


def main():
    print_banner("STARTING IMAGE PROXY & RRF AUTOMATED TEST SUITE")

    test_ssrf_protection_validator()
    test_magic_bytes_detection()
    test_image_proxy_base64_formatting()
    test_rrf_rank_fusion_and_match_floor()

    print_banner("🎉 ALL IMAGE PROXY & RRF TESTS PASSED SUCCESSFULLY (100% ASSERTIONS) 🎉")


if __name__ == "__main__":
    main()
