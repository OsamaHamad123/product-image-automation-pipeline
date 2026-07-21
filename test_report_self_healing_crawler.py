# test_report_self_healing_crawler.py
from verification_layer.use_cases.dom_self_healing import SimiloDOMSelfHealingEngine, DOMElementFingerprint
from verification_layer.use_cases.proxy_trust_scoring import BayesianProxyTrustScorer
from verification_layer.use_cases.hybrid_crawler_pool import TwoTierHybridCrawlerPool, CircuitBreakerTripped


def test_similo_dom_self_healing():
    print("\n--- Test 1: Similo Multi-Attribute Weighted DOM Matching & Pruning ---")
    # Target element historically stored in DB
    target_element = DOMElementFingerprint(
        tag_name="button",
        element_id="add-to-cart-btn",
        name="add_btn",
        visible_text="إضافة إلى السلة",
        neighbor_text="سعر المنتج 50 ريال شامل الضريبة",
        class_name="btn btn-primary cart-action-v1",
        xpath="/html/body/div[2]/div/button",
        x=250.0,
        y=400.0,
    )

    # Candidate A: Same element with dynamic class drift ("cart-action-v2_89a3")
    cand_a = DOMElementFingerprint(
        tag_name="button",
        element_id="add-to-cart-btn",
        name="add_btn",
        visible_text="أضف للسلة",  # Minor text drift
        neighbor_text="سعر المنتج 50 ريال شامل الضريبة",
        class_name="btn btn-primary cart-action-v2_89a3",
        xpath="/html/body/div[2]/div/button",
        x=252.0,
        y=402.0,
    )

    # Candidate B: Completely different element (e.g. search button)
    cand_b = DOMElementFingerprint(
        tag_name="button",
        element_id="search-btn",
        name="search",
        visible_text="بحث",
        neighbor_text="ابحث في المنتجات",
        class_name="btn-search",
        xpath="/html/body/header/form/button",
        x=900.0,
        y=50.0,
    )

    match_result = SimiloDOMSelfHealingEngine.find_best_self_healed_element(
        target_element, [cand_a, cand_b], min_score_threshold=0.50
    )

    assert match_result.matched_element is not None
    assert match_result.matched_element.element_id == "add-to-cart-btn"
    assert match_result.similarity_score >= 0.80
    print(f"✅ Similo self-healing matched target element with score: {match_result.similarity_score*100:.1f}%")

    # Test DOM Pruning for LLM Rescue
    raw_snippet = '<div class="container"><script>alert(1);</script><style>.a{color:red}</style><button id="btn" style="color:blue">Submit</button></div>'
    pruned = SimiloDOMSelfHealingEngine.prune_dom_tree_for_llm_rescue(raw_snippet)
    assert "<script>" not in pruned and "<style>" not in pruned and "style=" not in pruned
    print("✅ DOM Pruning executed correctly.")


def test_bayesian_proxy_scoring_and_gaussian_delay():
    print("\n--- Test 2: Bayesian Proxy Trust Scorer & Gaussian Pacing ---")
    scorer = BayesianProxyTrustScorer(mu=1.5, sigma=0.5)

    # Test Gaussian delay formula T_delay = mu + sigma * randn()
    delays = [scorer.calculate_gaussian_delay() for _ in range(20)]
    assert all(d >= 0.2 for d in delays)
    avg_delay = sum(delays) / float(len(delays))
    assert 0.8 <= avg_delay <= 2.2
    print(f"✅ Gaussian delay pacing verified (Average delay: {avg_delay:.2f}s).")

    # Test Bayesian score calculation for clean proxy vs soft-banned proxy
    proxy_clean = "http://residential.proxy.com:8080"
    for _ in range(10):
        scorer.record_response(proxy_clean, is_success=True)

    score_clean = scorer.compute_bayesian_trust_score(proxy_clean)
    assert score_clean.trust_score >= 0.85
    assert score_clean.is_quarantined is False

    proxy_bad = "http://datacenter.proxy.com:3128"
    for _ in range(4):
        scorer.record_response(proxy_bad, is_success=False, is_soft_ban=True)

    score_bad = scorer.compute_bayesian_trust_score(proxy_bad)
    assert score_bad.is_quarantined is True
    print(f"✅ Clean Proxy Score: {score_clean.trust_score} | Bad Soft-Banned Proxy Quarantined: {score_bad.is_quarantined}")


def test_two_tier_hybrid_crawler_pool_and_circuit_breaker():
    print("\n--- Test 3: Two-Tier Hybrid Session Pool & Circuit Breakers ---")
    pool = TwoTierHybridCrawlerPool()
    proxy = "http://127.0.0.1:8080"

    # Tier 1 Token Store
    token = pool.store_session_token(proxy, cf_clearance="cf_token_sample_123", user_agent="Mozilla/5.0 Chrome/120.0")
    assert token.cf_clearance == "cf_token_sample_123"

    retrieved_token = pool.get_valid_session_token(proxy)
    assert retrieved_token is not None and retrieved_token.is_active is True

    # Test Circuit Breakers (Trip if failure rate >= 90%)
    try:
        for _ in range(12):
            pool.record_request_outcome(is_success=False)
        assert False, "Circuit breaker should have TRIPPED!"
    except CircuitBreakerTripped as ex:
        print(f"✅ Circuit Breaker tripped successfully on high failure rate: {ex}")


if __name__ == "__main__":
    test_similo_dom_self_healing()
    test_bayesian_proxy_scoring_and_gaussian_delay()
    test_two_tier_hybrid_crawler_pool_and_circuit_breaker()
    print("\n🎉 ALL ANTI-BLOCKING & SELF-HEAVY CRAWLER TESTS PASSED SUCCESSFULLY!")
