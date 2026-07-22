# verification_layer/use_cases/rank_fusion_service.py
"""
Reciprocal Rank Fusion (RRF) & Match Floor Filtering Service.
Combines dense image embeddings and lexical text search rankings using k=60,
normalizes scores [0%, 100%] against RRF_max = 2/61, and enforces a strict >= 65% match floor.
"""

from typing import List, Dict, Any, Tuple


class RankFusionService:
    """Service computing RRF rank fusion scores and enforcing match floor thresholds."""

    def __init__(self, k: int = 60, floor_threshold: float = 65.0):
        self.k = k
        self.floor_threshold = floor_threshold
        # Maximum theoretical RRF score for item ranked 1st in both lists (Q=2)
        self.rrf_max = (1.0 / (k + 1)) + (1.0 / (k + 1))

    def evaluate_fusion(
        self,
        dense_results: List[str],
        lexical_results: List[str]
    ) -> Dict[str, Any]:
        scores: Dict[str, float] = {}

        # 1. Dense visual rank scoring
        for rank, doc_id in enumerate(dense_results, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + (1.0 / (self.k + rank))

        # 2. Lexical text rank scoring
        for rank, doc_id in enumerate(lexical_results, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + (1.0 / (self.k + rank))

        passed_candidates: List[Dict[str, Any]] = []
        rejected_candidates: List[Dict[str, Any]] = []

        for doc_id, raw_score in scores.items():
            # Normalized score percentage [0%, 100%]
            normalized_pct = min(100.0, max(0.0, (raw_score / self.rrf_max) * 100.0))
            
            candidate = {
                "image_id": doc_id,
                "raw_rrf_score": round(raw_score, 6),
                "match_percentage": round(normalized_pct, 2)
            }

            if normalized_pct >= self.floor_threshold:
                passed_candidates.append(candidate)
            else:
                rejected_candidates.append(candidate)

        passed_candidates.sort(key=lambda x: x["match_percentage"], reverse=True)
        rejected_candidates.sort(key=lambda x: x["match_percentage"], reverse=True)

        self_healing_required = len(passed_candidates) == 0

        return {
            "self_healing_required": self_healing_required,
            "floor_threshold": self.floor_threshold,
            "passed_candidates": passed_candidates,
            "rejected_candidates": rejected_candidates
        }
