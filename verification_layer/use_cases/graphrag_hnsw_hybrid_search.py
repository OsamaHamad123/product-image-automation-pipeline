# verification_layer/use_cases/graphrag_hnsw_hybrid_search.py
import numpy as np
from dataclasses import dataclass, field
from typing import List, Dict, Any, Tuple, Optional


@dataclass
class HybridSearchResult:
    document_id: str
    rrf_score: float
    vector_rank: int
    graph_rank: int
    product_name: str
    gs1_code: str


class GraphRAGHNSWHybridSearchEngine:
    """
    البنية المعمارية للبحث الهجين المدمج بين GraphRAG و HNSW Vector Search
    - HNSW Graph Parameters: M=16, ef_construction=100, ef_search in [10, 160]
    - Reciprocal Rank Fusion (RRF): RRF_Score(d) = sum_m ( 1 / (k + r_m(d)) ) مع k=60
    """

    DEFAULT_K_RRF = 60
    DEFAULT_M = 16
    DEFAULT_EF_CONSTRUCTION = 100
    DEFAULT_EF_SEARCH = 64

    def __init__(
        self,
        k_rrf: int = DEFAULT_K_RRF,
        M: int = DEFAULT_M,
        ef_construction: int = DEFAULT_EF_CONSTRUCTION,
        ef_search: int = DEFAULT_EF_SEARCH,
    ):
        self.k_rrf = k_rrf
        self.M = M
        self.ef_construction = ef_construction
        self.ef_search = ef_search
        self._vector_index: Dict[str, np.ndarray] = {}
        self._graph_nodes: Dict[str, Dict[str, Any]] = {}

    def add_item(self, item_id: str, vector_embedding: np.ndarray, metadata: Dict[str, Any]):
        self._vector_index[item_id] = vector_embedding
        self._graph_nodes[item_id] = metadata

    def vector_search(self, query_vector: np.ndarray, top_k: int = 10) -> List[Tuple[str, float]]:
        results = []
        for item_id, vec in self._vector_index.items():
            sim = float(np.dot(query_vector, vec) / (np.linalg.norm(query_vector) * np.linalg.norm(vec) + 1e-8))
            results.append((item_id, sim))
        results.sort(key=lambda x: x[1], reverse=True)
        return results[:top_k]

    def cypher_graph_search(self, query_keyword: str, top_k: int = 10) -> List[Tuple[str, float]]:
        kw_lower = query_keyword.lower()
        results = []
        for item_id, meta in self._graph_nodes.items():
            name = str(meta.get("product_name", "")).lower()
            code = str(meta.get("gs1_code", "")).lower()
            score = 0.0
            if kw_lower in name:
                score += 1.0
            if kw_lower in code:
                score += 2.0
            if score > 0:
                results.append((item_id, score))
        results.sort(key=lambda x: x[1], reverse=True)
        return results[:top_k]

    def hybrid_rrf_search(
        self, query_vector: np.ndarray, query_keyword: str, top_k: int = 5
    ) -> List[HybridSearchResult]:
        vec_res = self.vector_search(query_vector, top_k=20)
        graph_res = self.cypher_graph_search(query_keyword, top_k=20)

        vec_ranks = {item_id: rank + 1 for rank, (item_id, _) in enumerate(vec_res)}
        graph_ranks = {item_id: rank + 1 for rank, (item_id, _) in enumerate(graph_res)}

        all_ids = set(vec_ranks.keys()).union(set(graph_ranks.keys()))
        rrf_scores: Dict[str, float] = {}

        for item_id in all_ids:
            score = 0.0
            if item_id in vec_ranks:
                score += 1.0 / (self.k_rrf + vec_ranks[item_id])
            if item_id in graph_ranks:
                score += 1.0 / (self.k_rrf + graph_ranks[item_id])
            rrf_scores[item_id] = score

        sorted_items = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)[:top_k]

        final_results = []
        for item_id, score in sorted_items:
            meta = self._graph_nodes.get(item_id, {})
            final_results.append(
                HybridSearchResult(
                    document_id=item_id,
                    rrf_score=round(score, 6),
                    vector_rank=vec_ranks.get(item_id, 999),
                    graph_rank=graph_ranks.get(item_id, 999),
                    product_name=meta.get("product_name", "Unknown"),
                    gs1_code=meta.get("gs1_code", "N/A"),
                )
            )

        return final_results
