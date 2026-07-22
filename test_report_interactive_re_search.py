# test_report_interactive_re_search.py
"""
Automated Test Suite for Interactive Relevance Feedback & Hard Negative Exclusion Architecture.
Validates Rocchio vector drift calculation (alpha=1.0, gamma=0.45),
pHash 64-bit Hex Hamming distance exclusion (d <= 10), and Cosine distance thresholding (< 0.15).
"""

import sys
import os
import numpy as np

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from verification_layer.use_cases.interactive_re_search_use_case import (
    SearchContext,
    ProductCandidate,
    ReSearchUseCase
)


def print_banner(title: str):
    print(f"\n=======================================================")
    print(f"🚀 {title}")
    print(f"=======================================================")


def test_rocchio_vector_drift():
    print_banner("TEST 1: Rocchio Query Modification & L2 Normalization")

    use_case = ReSearchUseCase()

    q0 = [0.1] * 512
    v_rej = [0.2] * 512

    q_final = use_case.calculate_rocchio_drift(q0, v_rej, alpha=1.0, gamma=0.45)

    assert len(q_final) == 512, "Vector dimension mismatch"
    l2_norm = float(np.linalg.norm(q_final, ord=2))
    assert abs(l2_norm - 1.0) < 1e-5, f"Expected L2 norm = 1.0, got {l2_norm}"

    print(f"  ✅ Rocchio Vector Drift Verified: L2 norm = {l2_norm:.6f}")


def test_phash_hamming_distance_exclusion():
    print_banner("TEST 2: pHash 64-bit Hex Hamming Distance Exclusion")

    use_case = ReSearchUseCase()

    hash_a = "ffff0000ffff0000"
    hash_near = "ffff0000ffff0001"  # 1 bit difference (d = 1 <= 10) -> Should be rejected
    hash_far = "0000ffff0000ffff"   # 32 bits difference (d = 32 > 10) -> Should pass

    d_near = use_case.hamming_distance(hash_a, hash_near)
    d_far = use_case.hamming_distance(hash_a, hash_far)

    assert d_near == 1, f"Expected Hamming distance 1, got {d_near}"
    assert d_far == 64, f"Expected Hamming distance 64, got {d_far}"

    print(f"  ✅ Hamming Distance Verified: Near={d_near} bits (Rejected), Far={d_far} bits (Passed).")


def test_cosine_distance_semantic_filtering():
    print_banner("TEST 3: Dense Embedding Cosine Distance Thresholding (< 0.15)")

    use_case = ReSearchUseCase()

    v1 = [1.0] + [0.0] * 511
    v_similar = [0.99] + [0.01] * 511  # Very close (Distance < 0.15) -> Should be rejected
    v_orthogonal = [0.0] + [1.0] * 511 # Distance = 1.0 (> 0.15) -> Should pass

    d_sim = use_case.cosine_distance(v1, v_similar)
    d_orth = use_case.cosine_distance(v1, v_orthogonal)

    assert d_sim < 0.15, f"Expected Cosine distance < 0.15, got {d_sim:.4f}"
    assert d_orth > 0.15, f"Expected Cosine distance > 0.15, got {d_orth:.4f}"

    print(f"  ✅ Cosine Distance Thresholding Verified: Similar={d_sim:.4f} (Rejected), Orthogonal={d_orth:.4f} (Passed).")


def test_re_search_use_case_execution():
    print_banner("TEST 4: Full ReSearchUseCase Pipeline Execution")

    use_case = ReSearchUseCase()

    context = SearchContext(
        session_id="session_test_999",
        query_vector=[0.1] * 512,
        rejected_product_id="prod_rejected",
        rejected_vector=[1.0] + [0.0] * 511,
        rejected_phash="ffff0000ffff0000"
    )

    candidates = [
        ProductCandidate(
            product_id="cand_valid",
            score=0.95,
            image_url="https://example.com/valid.jpg",
            phash="0000ffff0000ffff",
            vector=[0.0] + [1.0] * 511
        ),
        ProductCandidate(
            product_id="prod_rejected",
            score=0.80,
            image_url="https://example.com/rejected.jpg",
            phash="ffff0000ffff0000",
            vector=[1.0] + [0.0] * 511
        ),
        ProductCandidate(
            product_id="cand_near_phash",
            score=0.90,
            image_url="https://example.com/near_phash.jpg",
            phash="ffff0000ffff0001",
            vector=[0.0] + [1.0] * 511
        )
    ]

    result = use_case.execute(context, candidates)

    passed = result["passed_candidates"]
    assert len(passed) == 1, f"Expected 1 passed candidate, got {len(passed)}"
    assert passed[0]["product_id"] == "cand_valid", f"Expected cand_valid, got {passed[0]['product_id']}"

    print(f"  ✅ Full ReSearchUseCase Execution Verified: 1/3 candidates passed filtration.")


def main():
    print_banner("STARTING INTERACTIVE RE-SEARCH AUTOMATED TEST SUITE")

    test_rocchio_vector_drift()
    test_phash_hamming_distance_exclusion()
    test_cosine_distance_semantic_filtering()
    test_re_search_use_case_execution()

    print_banner("🎉 ALL INTERACTIVE RE-SEARCH TESTS PASSED SUCCESSFULLY (100% ASSERTIONS) 🎉")


if __name__ == "__main__":
    main()
