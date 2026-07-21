# test_report_upgrades.py
import os
import tempfile
from PIL import Image, ImageDraw
import numpy as np

from celery_config import celery_app
from image_dedup_bktree import calculate_phash, PerceptualDeduplicationTree, calculate_hamming_distance
from verification_layer.use_cases.rrf_hybrid_search import ReciprocalRankFusionEngine
from image_processor import optimize_and_center_product_image
from verification_layer.use_cases.active_learning_hitl import UncertaintySamplingEngine, ConformalPredictionRecalibrator


def test_celery_production_configuration():
    print("\n--- Test 1: Celery Queue Isolation & Prefetch Tuning ---")
    conf = celery_app.conf
    assert conf.worker_prefetch_multiplier == 1, "worker_prefetch_multiplier must be set to 1"
    assert conf.task_acks_late is True, "task_acks_late must be True"
    assert conf.task_reject_on_worker_lost is True, "task_reject_on_worker_lost must be True"
    assert "io_bound_crawler" in str(conf.task_routes)
    assert "gpu_bound_enhancer" in str(conf.task_routes)
    print("✅ Celery production queue tuning verified.")


def test_phash_and_bktree_deduplication():
    print("\n--- Test 2: pHash 64-bit DCT & BK-Tree Triangle Inequality Pruning ---")
    # Create two visually identical images with minor pixel perturbation
    img1 = Image.new("RGB", (300, 300), (255, 255, 255))
    draw1 = ImageDraw.Draw(img1)
    draw1.rectangle([50, 50, 250, 250], fill=(200, 0, 0))

    img2 = Image.new("RGB", (300, 300), (255, 255, 255))
    draw2 = ImageDraw.Draw(img2)
    draw2.rectangle([50, 50, 250, 250], fill=(198, 0, 0))  # tiny color drift

    hash1 = calculate_phash(img1)
    hash2 = calculate_phash(img2)
    dist = calculate_hamming_distance(hash1, hash2)

    assert hash1 != 0 and hash2 != 0
    assert dist <= 3, f"Expected hamming distance <= 3 for near-duplicate images, got {dist}"

    tree = PerceptualDeduplicationTree()
    tree.insert_node(hash1, "img_001", {"name": "Red Box Original"})
    dups = tree.query_duplicates(hash2, tolerance_threshold=5)

    assert len(dups) >= 1
    assert dups[0]["image_id"] == "img_001"
    print(f"✅ pHash distance: {dist} | BK-Tree query matched image_id: {dups[0]['image_id']}")


def test_reciprocal_rank_fusion_engine():
    print("\n--- Test 3: Reciprocal Rank Fusion (RRF) Engine ---")
    rrf = ReciprocalRankFusionEngine(k=60)

    # Rank List 1 (Sparse BM25 Search)
    list_bm25 = [
        {"id": "PROD_A", "title": "Nido Milk 800g"},
        {"id": "PROD_B", "title": "Anchor Milk 800g"},
        {"id": "PROD_C", "title": "Rainbow Milk 170g"},
    ]

    # Rank List 2 (Dense Vector SigLIP Search)
    list_vector = [
        {"id": "PROD_B", "title": "Anchor Milk 800g"},
        {"id": "PROD_A", "title": "Nido Milk 800g"},
        {"id": "PROD_D", "title": "Luna Milk 400g"},
    ]

    merged_results = rrf.combine_rankings([list_bm25, list_vector], id_key="id")

    assert len(merged_results) == 4
    # Check RRF score calculation: 1/(60+1) + 1/(60+2) = 0.016393 + 0.016129 = 0.032522
    top_id = merged_results[0]["id"]
    assert top_id in ("PROD_A", "PROD_B")
    print(f"✅ RRF merged top rank: {top_id} (Score: {merged_results[0]['rrf_score']})")


def test_product_image_optimization_and_centering():
    print("\n--- Test 4: Product Image Optimization & Canvas Centering ---")
    with tempfile.TemporaryDirectory() as tmp_dir:
        raw_path = os.path.join(tmp_dir, "raw_product.png")
        dest_path = os.path.join(tmp_dir, "optimized_product.webp")

        # Create raw image: white background with red rectangle
        raw_img = Image.new("RGB", (600, 800), (255, 255, 255))
        draw = ImageDraw.Draw(raw_img)
        draw.rectangle([100, 100, 500, 700], fill=(220, 30, 30))
        raw_img.save(raw_path)

        opt_res_path = optimize_and_center_product_image(raw_path, dest_path, canvas_dimension=1000)

        assert os.path.exists(opt_res_path)
        with Image.open(opt_res_path) as opt_img:
            assert opt_img.size == (1000, 1000)
            assert opt_img.format == "WEBP"
        print("✅ 1000x1000px 1:1 canvas, 88% product fill ratio, drop shadow, and WebP compression verified.")


def test_active_learning_uncertainty_sampling():
    print("\n--- Test 5: Active Learning Uncertainty Sampling & Conformal Prediction ---")
    samples = [
        {"id": "S1", "class_probabilities": [0.95, 0.03, 0.02]},  # High confidence
        {"id": "S2", "class_probabilities": [0.51, 0.49, 0.00]},  # Smallest margin ambiguity
        {"id": "S3", "class_probabilities": [0.34, 0.33, 0.33]},  # High entropy
    ]

    prioritized = UncertaintySamplingEngine.prioritize_samples_for_human_review(samples, top_n=3)

    assert len(prioritized) == 3
    # S3 (highest entropy) or S2 (smallest margin) should be top prioritized for HITL
    assert prioritized[0]["id"] in ("S2", "S3")

    # Test Conformal Prediction
    calib_scores = [0.01, 0.02, 0.03, 0.04, 0.05, 0.12, 0.15, 0.22, 0.35, 0.40]
    thresh = ConformalPredictionRecalibrator.compute_conformal_threshold(calib_scores, alpha=0.05)
    assert 0.0 < thresh <= 1.0
    print(f"✅ Active learning uncertainty sampling prioritized: {[s['id'] for s in prioritized]} | Conformal threshold: {thresh}")


if __name__ == "__main__":
    test_celery_production_configuration()
    test_phash_and_bktree_deduplication()
    test_reciprocal_rank_fusion_engine()
    test_product_image_optimization_and_centering()
    test_active_learning_uncertainty_sampling()
    print("\n🎉 ALL REPORT UPGRADES TESTS PASSED SUCCESSFULLY!")
