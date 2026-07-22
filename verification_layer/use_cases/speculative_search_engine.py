# verification_layer/use_cases/speculative_search_engine.py
"""
Speculative Multi-Engine Async Search Pipeline with Circuit Breaker and RRF Consensus.
Operates async with zero external heavy frameworks.
"""

import asyncio
import time
from typing import List, Dict, Any, Tuple, Optional
from verification_layer.domain.nextgen_models import RRFResult


class CircuitBreakerOpenException(Exception):
    """Raised when the circuit breaker is OPEN to prevent cascading failures."""
    pass


class CircuitBreaker:
    def __init__(self, failure_threshold: int = 3, recovery_timeout: float = 5.0):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.failure_count = 0
        self.state = "CLOSED"  # CLOSED, OPEN, HALF-OPEN
        self.last_state_change = time.time()

    def allow_request(self) -> bool:
        now = time.time()
        if self.state == "CLOSED":
            return True
        if self.state == "OPEN":
            if now - self.last_state_change > self.recovery_timeout:
                self.state = "HALF-OPEN"
                self.last_state_change = now
                return True
            return False
        return True

    def record_success(self):
        self.failure_count = 0
        self.state = "CLOSED"

    def record_failure(self):
        self.failure_count += 1
        if self.failure_count >= self.failure_threshold:
            self.state = "OPEN"
            self.last_state_change = time.time()


class SpeculativeSearchEngine:
    def __init__(self, rrf_k: int = 60):
        self.rrf_k = rrf_k
        self.vector_breaker = CircuitBreaker()
        self.lexical_breaker = CircuitBreaker()

    async def _search_vector(self, query: str, simulate_failure: bool = False) -> List[str]:
        if not self.vector_breaker.allow_request():
            raise CircuitBreakerOpenException("Vector search circuit is OPEN")
        if simulate_failure:
            self.vector_breaker.record_failure()
            raise RuntimeError("Simulated vector search failure")
        
        await asyncio.sleep(0.01)
        self.vector_breaker.record_success()
        return [f"prod_{query}_101", f"prod_{query}_102", f"prod_{query}_105"]

    async def _search_lexical(self, query: str, simulate_failure: bool = False) -> List[str]:
        if not self.lexical_breaker.allow_request():
            raise CircuitBreakerOpenException("Lexical search circuit is OPEN")
        if simulate_failure:
            self.lexical_breaker.record_failure()
            raise RuntimeError("Simulated lexical search failure")

        await asyncio.sleep(0.01)
        self.lexical_breaker.record_success()
        return [f"prod_{query}_102", f"prod_{query}_101", f"prod_{query}_309"]

    def merge_rrf_rankings(
        self,
        engine_results: Dict[str, Tuple[List[str], float]]
    ) -> List[Tuple[str, float]]:
        """
        Calculates RRF Consensus score:
        RRF(d) = SUM ( w_m / (k + r_m(d)) )
        """
        rrf_map: Dict[str, float] = {}

        for engine_name, (rankings, weight) in engine_results.items():
            for rank_idx, doc in enumerate(rankings, 1):
                reciprocal_rank = weight / (self.rrf_k + rank_idx)
                rrf_map[doc] = rrf_map.get(doc, 0.0) + reciprocal_rank

        sorted_results = sorted(rrf_map.items(), key=lambda x: x[1], reverse=True)
        return sorted_results

    async def execute_speculative_search(
        self,
        query: str,
        fallback_gtin: Optional[str] = None,
        fail_vector: bool = False,
        fail_lexical: bool = False
    ) -> RRFResult:
        tasks = [
            self._search_vector(query, simulate_failure=fail_vector),
            self._search_lexical(query, simulate_failure=fail_lexical)
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        vec_res = results[0] if not isinstance(results[0], Exception) else []
        lex_res = results[1] if not isinstance(results[1], Exception) else []

        circuit_status = {
            "vector": self.vector_breaker.state,
            "lexical": self.lexical_breaker.state
        }

        if not vec_res and not lex_res:
            if fallback_gtin:
                return RRFResult(
                    query=query,
                    candidates=[(f"gtin_direct_{fallback_gtin}", 1.0)],
                    consensus_score=1.0,
                    source="gtin_barcode_fallback",
                    circuit_status=circuit_status
                )
            return RRFResult(
                query=query,
                candidates=[],
                consensus_score=0.0,
                source="circuit_open_fallback_none",
                circuit_status=circuit_status
            )

        engine_outputs = {
            "dense_vector": (vec_res, 1.2),
            "sparse_lexical": (lex_res, 1.0)
        }
        fused = self.merge_rrf_rankings(engine_outputs)
        top_score = fused[0][1] if fused else 0.0

        return RRFResult(
            query=query,
            candidates=fused,
            consensus_score=round(top_score, 5),
            source="speculative_rrf_fusion",
            circuit_status=circuit_status
        )
