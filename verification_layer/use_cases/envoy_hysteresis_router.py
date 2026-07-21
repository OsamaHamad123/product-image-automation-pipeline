# verification_layer/use_cases/envoy_hysteresis_router.py
from dataclasses import dataclass
from typing import Dict, Any, List, Optional


@dataclass
class ProviderHealthStatus:
    provider_id: str
    health_score: float  # 0.0 to 100.0
    consecutive_5xx_errors: int
    is_ejected: bool  # Envoy passive health check outlier ejection


@dataclass
class RoutingDecision:
    selected_provider_id: str
    previous_provider_id: str
    did_switch: bool
    hysteresis_buffer_delta: float
    reason: str


class EnvoyHysteresisRouter:
    """
    محرك التعافي الصامت لبوابة Envoy بالتباعد الترددي (Envoy Passive Outlier Detection & Hysteresis Router)
    - Envoy Passive Health Checking: Eject provider on consecutive 5xx or local origin failures.
    - Hysteresis Routing Rule: Score(v_n) > Score(v_c) + delta
      حيث v_c المزود الحالي، v_n المزود المرشح، delta هامش الأمان التشغيلي لمنع رفرفة الإشارات (Flapping).
    """

    DEFAULT_HYSTERESIS_DELTA = 15.0  # Hysteresis safety margin buffer delta
    MAX_CONSECUTIVE_ERRORS = 2

    def __init__(self, hysteresis_delta: float = DEFAULT_HYSTERESIS_DELTA):
        self.hysteresis_delta = hysteresis_delta
        self.active_provider_id: Optional[str] = None
        self._provider_stats: Dict[str, ProviderHealthStatus] = {}

    def update_provider_telemetry(self, provider_id: str, success: bool, is_5xx: bool = False):
        if provider_id not in self._provider_stats:
            self._provider_stats[provider_id] = ProviderHealthStatus(
                provider_id=provider_id, health_score=100.0, consecutive_5xx_errors=0, is_ejected=False
            )

        status = self._provider_stats[provider_id]
        if success:
            status.consecutive_5xx_errors = 0
            status.health_score = min(100.0, status.health_score + 5.0)
            if status.health_score >= 50.0:
                status.is_ejected = False
        else:
            if is_5xx:
                status.consecutive_5xx_errors += 1
            status.health_score = max(0.0, status.health_score - 20.0)

            if status.consecutive_5xx_errors >= self.MAX_CONSECUTIVE_ERRORS or status.health_score < 30.0:
                status.is_ejected = True

    def select_best_provider_with_hysteresis(self, current_provider_id: str) -> RoutingDecision:
        if current_provider_id not in self._provider_stats:
            self.update_provider_telemetry(current_provider_id, success=True)

        current_status = self._provider_stats[current_provider_id]
        current_score = 0.0 if current_status.is_ejected else current_status.health_score

        # Find best candidate provider
        best_candidate_id = current_provider_id
        best_candidate_score = current_score

        for p_id, p_status in self._provider_stats.items():
            if p_status.is_ejected:
                continue
            if p_status.health_score > best_candidate_score:
                best_candidate_score = p_status.health_score
                best_candidate_id = p_id

        # Apply Hysteresis Condition: Score(v_n) > Score(v_c) + delta
        did_switch = False
        reason = f"Maintained current provider {current_provider_id} (Score: {current_score:.1f})."

        if current_status.is_ejected:
            # Emergency switch if current is ejected
            did_switch = True
            selected_id = best_candidate_id
            reason = f"Emergency Failover! Provider {current_provider_id} ejected by Envoy Outlier Detection. Switched to {selected_id}."
        elif best_candidate_id != current_provider_id and best_candidate_score > (current_score + self.hysteresis_delta):
            did_switch = True
            selected_id = best_candidate_id
            reason = (
                f"Hysteresis threshold met: Score({selected_id}) = {best_candidate_score:.1f} > "
                f"Score({current_provider_id}) = {current_score:.1f} + delta({self.hysteresis_delta:.1f}). Switched."
            )
        else:
            selected_id = current_provider_id

        self.active_provider_id = selected_id

        return RoutingDecision(
            selected_provider_id=selected_id,
            previous_provider_id=current_provider_id,
            did_switch=did_switch,
            hysteresis_buffer_delta=self.hysteresis_delta,
            reason=reason,
        )
