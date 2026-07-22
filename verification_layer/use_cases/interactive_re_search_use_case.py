# verification_layer/use_cases/interactive_re_search_use_case.py
"""
Interactive Relevance Feedback & Hard Negative Exclusion Use Case.
Implements Rocchio query vector modification (alpha=1.0, gamma=0.45),
pHash 64-bit Hamming distance exclusion (d <= 10), and Cosine distance thresholding (< 0.15).
"""

import numpy as np
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field


class SearchContext(BaseModel):
    session_id: str = Field(..., description="Unique user session identifier")
    query_vector: List[float] = Field(..., description="L2 normalized float vector representing the original query")
    rejected_product_id: str = Field(..., description="The unique identifier of the blacklisted product")
    rejected_vector: List[float] = Field(..., description="Embedding vector of the rejected product")
    rejected_phash: str = Field(..., description="Hexadecimal perceptual hash of the rejected item")


class ProductCandidate(BaseModel):
    product_id: str
    score: float
    image_url: str
    phash: str
    vector: List[float]


class InMemoryBlacklistCache:
    """In-memory fallback cache for session blacklists."""
    def __init__(self):
        self._store: Dict[str, List[Dict[str, Any]]] = {}

    def get_session_blacklist(self, session_id: str) -> List[Dict[str, Any]]:
        return self._store.get(session_id, [])

    def append_to_blacklist(self, session_id: str, product_id: str, phash: str, vector: List[float]) -> None:
        if session_id not in self._store:
            self._store[session_id] = []
        self._store[session_id].append({
            "product_id": product_id,
            "phash": phash,
            "vector": vector
        })


class ReSearchUseCase:
    def __init__(self, cache: Optional[InMemoryBlacklistCache] = None):
        self.cache = cache or InMemoryBlacklistCache()

    def calculate_rocchio_drift(
        self,
        query_vector: List[float],
        rejected_vector: List[float],
        alpha: float = 1.0,
        gamma: float = 0.45
    ) -> List[float]:
        """Calculates Rocchio query modification and applies L2 normalization."""
        q0 = np.array(query_vector, dtype=np.float32)
        v_rej = np.array(rejected_vector, dtype=np.float32)

        q_new = alpha * q0 - gamma * v_rej
        norm = np.linalg.norm(q_new, ord=2)

        if norm > 1e-12:
            q_final = q_new / norm
        else:
            q_final = q0

        return q_final.tolist()

    @staticmethod
    def hamming_distance(hex_hash1: str, hex_hash2: str) -> int:
        """Calculates 64-bit Hex Hamming distance via bitwise XOR popcount."""
        h1 = int(hex_hash1, 16)
        h2 = int(hex_hash2, 16)
        return bin(h1 ^ h2).count('1')

    @staticmethod
    def cosine_distance(vec1: List[float], vec2: List[float]) -> float:
        """Calculates Cosine distance D_Cosine = 1 - (u . v) / (||u|| ||v||)."""
        v1 = np.array(vec1, dtype=np.float32)
        v2 = np.array(vec2, dtype=np.float32)

        dot = np.dot(v1, v2)
        norm1 = np.linalg.norm(v1, ord=2)
        norm2 = np.linalg.norm(v2, ord=2)

        if norm1 == 0 or norm2 == 0:
            return 2.0

        return float(1.0 - (dot / (norm1 * norm2)))

    def filter_candidates(
        self,
        candidates: List[ProductCandidate],
        blacklist: List[Dict[str, Any]],
        hamming_threshold: int = 10,
        cosine_threshold: float = 0.15
    ) -> List[ProductCandidate]:
        """Applies dual-layer photometric (pHash Hamming) and semantic (Cosine distance) exclusion."""
        filtered: List[ProductCandidate] = []

        for cand in candidates:
            # 1. Direct ID match check
            if any(item["product_id"] == cand.product_id for item in blacklist):
                continue

            rejected = False
            for item in blacklist:
                # 2. Photometric pHash Hamming distance filter
                if self.hamming_distance(cand.phash, item["phash"]) <= hamming_threshold:
                    rejected = True
                    break

                # 3. Semantic Cosine distance filter
                if self.cosine_distance(cand.vector, item["vector"]) < cosine_threshold:
                    rejected = True
                    break

            if not rejected:
                filtered.append(cand)

        return filtered

    def execute(
        self,
        context: SearchContext,
        candidates_pool: List[ProductCandidate],
        alpha: float = 1.0,
        gamma: float = 0.45,
        hamming_threshold: int = 10,
        cosine_threshold: float = 0.15
    ) -> Dict[str, Any]:
        # 1. Calculate Rocchio vector drift
        q_final = self.calculate_rocchio_drift(context.query_vector, context.rejected_vector, alpha, gamma)

        # 2. Append rejected item to session blacklist
        self.cache.append_to_blacklist(
            context.session_id,
            context.rejected_product_id,
            context.rejected_phash,
            context.rejected_vector
        )

        # 3. Retrieve active blacklist
        blacklist = self.cache.get_session_blacklist(context.session_id)

        # 4. Filter candidates pool
        filtered = self.filter_candidates(candidates_pool, blacklist, hamming_threshold, cosine_threshold)

        return {
            "session_id": context.session_id,
            "refined_query_vector": q_final,
            "blacklisted_count": len(blacklist),
            "passed_candidates": [c.model_dump() for c in filtered]
        }
