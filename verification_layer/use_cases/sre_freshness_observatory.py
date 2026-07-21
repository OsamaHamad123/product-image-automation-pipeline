# verification_layer/use_cases/sre_freshness_observatory.py
import time
from dataclasses import dataclass
from typing import List, Dict, Any, Tuple


@dataclass
class LagDecompositionReport:
    t_event: float
    t_ingestion: float
    t_processing: float
    t_availability: float
    capture_lag_sec: float     # T_ingestion - T_event
    pipeline_lag_sec: float    # T_processing - T_ingestion
    destination_lag_sec: float # T_availability - T_processing
    total_end_to_end_lag_sec: float # T_availability - T_event


@dataclass
class FreshnessSLIReport:
    freshness_sli_pct: float   # Target SLO >= 99.95%
    slo_target_pct: float      # 99.95%
    meets_slo_benchmark: bool
    average_lag_sec: float
    total_samples_evaluated: int
    reasons: List[str]


class SREDataFreshnessObservatory:
    """
    مرصد مؤشرات الأداء الحيوية SRE وحداثة البيانات (SRE Data Freshness SLI/SLO Observatory)
    - Freshness SLI = ( sum(T_fresh) / sum(T_total) ) * 100
    - 4-stage lag decomposition:
      1. Capture Lag = T_ingestion - T_event
      2. Pipeline Lag = T_processing - T_ingestion
      3. Destination Lag = T_availability - T_processing
    """

    MAX_FRESHNESS_AGE_THRESHOLD_SEC = 600.0  # 10 minutes max allowable age
    SLO_TARGET_PCT = 99.95                  # SLO >= 99.95%

    @classmethod
    def decompose_pipeline_lags(
        cls, t_event: float, t_ingestion: float, t_processing: float, t_availability: float
    ) -> LagDecompositionReport:
        capture_lag = max(0.0, t_ingestion - t_event)
        pipeline_lag = max(0.0, t_processing - t_ingestion)
        dest_lag = max(0.0, t_availability - t_processing)
        total_lag = max(0.0, t_availability - t_event)

        return LagDecompositionReport(
            t_event=t_event,
            t_ingestion=t_ingestion,
            t_processing=t_processing,
            t_availability=t_availability,
            capture_lag_sec=round(capture_lag, 3),
            pipeline_lag_sec=round(pipeline_lag, 3),
            destination_lag_sec=round(dest_lag, 3),
            total_end_to_end_lag_sec=round(total_lag, 3),
        )

    @classmethod
    def calculate_freshness_sli(
        cls,
        lags_list: List[float],
        max_age_threshold_sec: float = MAX_FRESHNESS_AGE_THRESHOLD_SEC,
        slo_target_pct: float = SLO_TARGET_PCT,
    ) -> FreshnessSLIReport:
        if not lags_list:
            return FreshnessSLIReport(
                freshness_sli_pct=100.0,
                slo_target_pct=slo_target_pct,
                meets_slo_benchmark=True,
                average_lag_sec=0.0,
                total_samples_evaluated=0,
                reasons=["No lag measurements evaluated."],
            )

        fresh_count = sum(1 for lag in lags_list if lag <= max_age_threshold_sec)
        total_count = len(lags_list)

        freshness_sli = (fresh_count / float(total_count)) * 100.0
        avg_lag = float(sum(lags_list) / float(total_count))

        meets_slo = freshness_sli >= slo_target_pct
        reasons = []

        if not meets_slo:
            reasons.append(
                f"Freshness SLI ({freshness_sli:.2f}%) fell below SLO target benchmark ({slo_target_pct}%). "
                f"Average end-to-end lag: {avg_lag:.1f}s."
            )
        else:
            reasons.append(
                f"Freshness SLI ({freshness_sli:.2f}%) meets or exceeds SLO target benchmark ({slo_target_pct}%)."
            )

        return FreshnessSLIReport(
            freshness_sli_pct=round(freshness_sli, 4),
            slo_target_pct=slo_target_pct,
            meets_slo_benchmark=meets_slo,
            average_lag_sec=round(avg_lag, 2),
            total_samples_evaluated=total_count,
            reasons=reasons,
        )
