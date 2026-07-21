# test_report_hybrid_graphrag_search.py
import numpy as np
from verification_layer.use_cases.graphrag_hnsw_hybrid_search import GraphRAGHNSWHybridSearchEngine
from verification_layer.use_cases.gs1_unspsc_taxonomy_classifier import GS1UNSPSCTaxonomyClassifier
from verification_layer.use_cases.grpo_query_reformulator import GRPOQueryReformulator
from verification_layer.use_cases.multiagent_react_swarm import MultiAgentReActSwarm
from verification_layer.use_cases.procrustes_crossmodal_aligner import SVDOrthogonalProcrustesAligner
from verification_layer.use_cases.pq_adc_compressor import ProductQuantizationADCCompressor


def test_graphrag_hnsw_hybrid_search():
    print("\n--- Test 1: GraphRAG & HNSW Hybrid Vector & Cypher Search Engine ---")
    engine = GraphRAGHNSWHybridSearchEngine(k_rrf=60)
    v1 = np.array([0.1, 0.9, 0.0, 0.5], dtype=np.float32)
    v2 = np.array([0.8, 0.1, 0.2, 0.0], dtype=np.float32)

    engine.add_item("item_1", v1, {"product_name": "Almarai Fresh Milk 1L", "gs1_code": "10000025"})
    engine.add_item("item_2", v2, {"product_name": "Lipton Yellow Label Tea 100s", "gs1_code": "10000043"})

    q_vec = np.array([0.1, 0.85, 0.05, 0.4], dtype=np.float32)
    res = engine.hybrid_rrf_search(q_vec, query_keyword="Milk", top_k=2)

    assert len(res) > 0
    assert res[0].document_id == "item_1"
    assert res[0].rrf_score > 0.0
    print(f"✅ Hybrid RRF Top Result: {res[0].product_name} | RRF Score: {res[0].rrf_score}")


def test_gs1_unspsc_taxonomy_classifier():
    print("\n--- Test 2: GS1 GPC & UNSPSC Rigid Taxonomy Classifier ---" )
    res1 = GS1UNSPSCTaxonomyClassifier.classify_product_taxonomy("Almarai Fresh Milk 1L")
    assert res1.gs1_gpc_brick_code == "10000025"
    assert res1.unspsc_code == "50131700"

    res2 = GS1UNSPSCTaxonomyClassifier.classify_product_taxonomy("Dettol Antibacterial Liquid Hand Wash 200ml")
    assert res2.gs1_gpc_brick_code == "10000350"
    assert res2.unspsc_code == "53131608"
    print(f"✅ GS1 GPC Brick: {res1.gs1_gpc_brick_code} ({res1.gs1_brick_title}) | UNSPSC: {res1.unspsc_code}")


def test_grpo_query_reformulator():
    print("\n--- Test 3: GRPO RLVR Advantage & Chain-of-Thought Query Reformulator ---")
    rewards = [0.2, 0.85, 0.95, 0.4]
    advantages = GRPOQueryReformulator.calculate_grpo_advantages(rewards)
    assert len(advantages) == 4
    assert advantages[2] > advantages[0]  # Higher reward -> higher advantage

    candidates = [
        ("Simple search milk", 0.4),
        ("<think>Identifying Almarai full fat 1L milk tin.</think><query>Almarai fresh milk 1L full fat packaging</query>", 0.95),
    ]

    ref_res = GRPOQueryReformulator.reformulate_query_cot("Almarai milk", candidates)
    assert ref_res.reformulated_query == "Almarai fresh milk 1L full fat packaging"
    assert ref_res.format_reward_passed is True
    print(f"✅ Reformulated Query: '{ref_res.reformulated_query}' | Format Reward Passed: {ref_res.format_reward_passed}")


def test_multiagent_react_swarm():
    print("\n--- Test 4: Multi-Agent ReAct & ODR Verification Swarm ---")
    meta = {"name": "Nido Fortigrow 2.5kg", "image_url": "http://img.com/nido.jpg", "category": "dairy"}
    swarm_rep = MultiAgentReActSwarm.execute_swarm_verification("prod_991", meta)

    assert swarm_rep.is_swarm_verified is True
    assert len(swarm_rep.agent_trace) == 3
    print(f"✅ Swarm Verification: {swarm_rep.final_answer} | Agent Steps: {len(swarm_rep.agent_trace)}")


def test_procrustes_crossmodal_aligner():
    print("\n--- Test 5: SVD Orthogonal Procrustes Alignment & InfoNCE Loss ---")
    np.random.seed(42)
    X = np.random.randn(8, 20).astype(np.float32)  # D=8, N=20
    # Create target by rotating X
    Q_true, _ = np.linalg.qr(np.random.randn(8, 8))
    Y = np.dot(Q_true, X)

    res = SVDOrthogonalProcrustesAligner.align_and_evaluate(X, Y)
    assert res.is_orthogonal is True
    assert res.alignment_frobenius_error < 1e-3
    print(f"✅ SVD Procrustes Orthogonal: {res.is_orthogonal} | Frobenius Error: {res.alignment_frobenius_error:.6f}")


def test_pq_adc_compressor():
    print("\n--- Test 6: Product Quantization (PQ) & Asymmetric Distance Computation (ADC) ---")
    np.random.seed(42)
    vectors = np.random.randn(100, 32).astype(np.float32)  # N=100, D=32
    compressor = ProductQuantizationADCCompressor(num_subspaces=4, num_centroids=16)

    pq_res = compressor.fit_quantizer(vectors)
    assert pq_res.compression_ratio_x == 32.0  # (32*4 bytes) / (4*1 byte) = 32x

    q_vec = vectors[0]
    adc_dist = compressor.compute_adc_distance(q_vec, pq_res.compressed_codes[0])
    assert adc_dist >= 0.0
    print(f"✅ PQ Compression Ratio: {pq_res.compression_ratio_x}x | ADC Distance: {adc_dist}")


if __name__ == "__main__":
    test_graphrag_hnsw_hybrid_search()
    test_gs1_unspsc_taxonomy_classifier()
    test_grpo_query_reformulator()
    test_multiagent_react_swarm()
    test_procrustes_crossmodal_aligner()
    test_pq_adc_compressor()
    print("\n🎉 ALL HYBRID GraphRAG, TAXONOMY & MULTI-AGENT TESTS PASSED SUCCESSFULLY!")
