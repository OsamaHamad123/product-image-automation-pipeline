# verification_layer/use_cases/bilingual_reranker.py
"""
Bilingual Dual-Index Cross-Reranker.
Reranks Arabic and English search candidates combining raw engine scores with token overlap.
"""

from typing import List
from verification_layer.domain.nextgen_models import SearchCandidate
from verification_layer.use_cases.vector_semantic_cache import VectorSemanticCache


class BilingualReranker:
    def __init__(self):
        self.cache_engine = VectorSemanticCache()

    def rerank(self, query: str, candidates: List[SearchCandidate]) -> List[SearchCandidate]:
        normalized_query = self.cache_engine.normalize_text(query)
        query_words = set(normalized_query.split())

        scored_candidates = []
        for cand in candidates:
            normalized_title = self.cache_engine.normalize_text(cand.title)
            title_words = set(normalized_title.split())
            intersection = len(query_words.intersection(title_words))

            matching_score = cand.score + (intersection * 0.2)
            scored_candidates.append(
                SearchCandidate(
                    document_id=cand.document_id,
                    title=cand.title,
                    score=round(matching_score, 4),
                    source=cand.source
                )
            )

        return sorted(scored_candidates, key=lambda x: x.score, reverse=True)
