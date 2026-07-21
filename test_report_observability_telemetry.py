# test_report_observability_telemetry.py
import time
from verification_layer.use_cases.opentelemetry_kafka_tracer import OpenTelemetryW3CTracer
from verification_layer.use_cases.soda_ge_quality_auditor import SodaGEDataQualityAuditor
from verification_layer.use_cases.envoy_hysteresis_router import EnvoyHysteresisRouter
from verification_layer.use_cases.sre_freshness_observatory import SREDataFreshnessObservatory


def test_opentelemetry_w3c_tracer():
    print("\n--- Test 1: OpenTelemetry W3C Trace Context Propagator & Latency_E2E ---")
    ctx = OpenTelemetryW3CTracer.generate_w3c_trace_context(
        conversation_id="tx-catalog-8812", baggage={"tenant": "global_store"}
    )
    assert ctx.trace_parent_header.startswith("00-")
    assert ctx.conversation_id == "tx-catalog-8812"

    headers = OpenTelemetryW3CTracer.inject_kafka_headers(ctx, destination_topic="global-catalog-updates")
    assert headers["traceparent"] == ctx.trace_parent_header
    assert headers["messaging.system"] == "kafka"

    t_prod = time.time() - 0.25  # 250ms ago
    latency_e2e = OpenTelemetryW3CTracer.calculate_end_to_end_latency(t_prod)
    assert latency_e2e >= 0.20
    print(f"✅ W3C Traceparent Header: {headers['traceparent']} | Latency_E2E: {latency_e2e*1000:.1f}ms")


def test_soda_ge_data_quality_auditor():
    print("\n--- Test 2: SodaCL & Great Expectations Quality Auditor ---")
    records = [
        {"id": "prod_1", "name": "Milk", "price": 10.0},
        {"id": "prod_2", "name": "Bread", "price": 5.0},
        {"id": "prod_3", "name": "", "price": None},  # Null values
    ]
    null_rate = SodaGEDataQualityAuditor.audit_null_rates(records, essential_keys=["id", "name", "price"])
    assert null_rate > 0.0

    # Silent Captcha Page Detection
    captcha_html = "<html><body><h1>Verify you are human - Cloudflare Turnstile</h1></body></html>"
    is_captcha = SodaGEDataQualityAuditor.detect_silent_captcha_page(200, captcha_html)
    assert is_captcha is True
    print(f"✅ Null Rate: {null_rate}% | Silent Captcha Detected: {is_captcha}")


def test_envoy_hysteresis_router():
    print("\n--- Test 3: Envoy Outlier Detection & Hysteresis Router ---")
    router = EnvoyHysteresisRouter(hysteresis_delta=15.0)

    # Provider Primary stats
    for _ in range(5):
        router.update_provider_telemetry("provider_primary", success=True)

    # Candidate Secondary stats
    for _ in range(10):
        router.update_provider_telemetry("provider_secondary", success=True)

    # Test initial routing (Primary is 100.0, Secondary is 100.0 -> delta threshold 15.0 not met, stay Primary)
    dec1 = router.select_best_provider_with_hysteresis("provider_primary")
    assert dec1.selected_provider_id == "provider_primary"
    assert dec1.did_switch is False

    # Simulate Primary degraded by 5xx errors -> Outlier Ejection
    router.update_provider_telemetry("provider_primary", success=False, is_5xx=True)
    router.update_provider_telemetry("provider_primary", success=False, is_5xx=True)

    dec2 = router.select_best_provider_with_hysteresis("provider_primary")
    assert dec2.selected_provider_id == "provider_secondary"
    assert dec2.did_switch is True
    print(f"✅ Envoy Failover: {dec2.reason}")


def test_sre_data_freshness_observatory():
    print("\n--- Test 4: SRE Data Freshness SLI/SLO & 4-Stage Lag Decomposition ---")
    now = time.time()
    t_evt = now - 12.0
    t_ing = now - 9.0
    t_prc = now - 4.0
    t_avl = now

    lags_report = SREDataFreshnessObservatory.decompose_pipeline_lags(
        t_event=t_evt, t_ingestion=t_ing, t_processing=t_prc, t_availability=t_avl
    )

    assert lags_report.capture_lag_sec == 3.0
    assert lags_report.pipeline_lag_sec == 5.0
    assert lags_report.destination_lag_sec == 4.0
    assert lags_report.total_end_to_end_lag_sec == 12.0

    # Freshness SLI calculation
    lags_sample = [10.0, 15.0, 20.0, 50.0, 120.0]  # All < 600s -> 100% SLI
    sli_rep = SREDataFreshnessObservatory.calculate_freshness_sli(lags_sample)
    assert sli_rep.freshness_sli_pct == 100.0
    assert sli_rep.meets_slo_benchmark is True
    print(f"✅ Freshness SLI: {sli_rep.freshness_sli_pct}% (SLO Target: {sli_rep.slo_target_pct}%) | E2E Lag: {lags_report.total_end_to_end_lag_sec}s")


if __name__ == "__main__":
    test_opentelemetry_w3c_tracer()
    test_soda_ge_data_quality_auditor()
    test_envoy_hysteresis_router()
    test_sre_data_freshness_observatory()
    print("\n🎉 ALL OBSERVABILITY, TELEMETRY & FAILOVER TESTS PASSED SUCCESSFULLY!")
