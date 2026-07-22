# verification_layer/use_cases/vector_semantic_cache.py
"""
Vector Semantic Query Cache & Text Normalization.
Normalizes Arabic and English queries and caches results using Jaccard Similarity (threshold >= 0.92).
"""

import re
from typing import Dict, List, Optional
from verification_layer.domain.nextgen_models import SearchCandidate


class VectorSemanticCache:
    def __init__(self, similarity_threshold: float = 0.92):
        self.similarity_threshold = similarity_threshold
        self.cache: Dict[str, List[SearchCandidate]] = {}

    def normalize_text(self, text: str) -> str:
        """
        Normalizes Arabic & English search strings:
        - Converts to lowercase
        - Separates numbers from measurement units (e.g., 500ml -> 500 ml, 500مل -> 500 مل)
        - Strips punctuation and diacritics
        - Normalizes alef, ta-marbuta, and ya
        """
        text = text.lower()
        # Separate digits from concatenated letters (e.g. 500ml -> 500 ml, 500مل -> 500 مل)
        text = re.sub(r"(\d+)([a-zA-Z\u0600-\u06ff])", r"\1 \2", text)
        text = re.sub(r"([a-zA-Z\u0600-\u06ff])(\d+)", r"\1 \2", text)
        # Remove Arabic diacritics
        text = re.sub(r"[\u064b-\u0652]", "", text)
        # Remove non-alphanumeric punctuation
        text = re.sub(r"[^\w\s]", "", text)
        # Normalize Arabic characters
        text = re.sub(r"[إأآا]", "ا", text)
        text = re.sub(r"[ة]", "ه", text)
        text = re.sub(r"[ى]", "ي", text)
        return text.strip()

    def get_jaccard_similarity(self, query1: str, query2: str) -> float:
        set1 = set(self.normalize_text(query1).split())
        set2 = set(self.normalize_text(query2).split())
        if not set1 and not set2:
            return 1.0
        union_len = len(set1.union(set2))
        if union_len == 0:
            return 0.0
        return len(set1.intersection(set2)) / union_len

    def lookup(self, query: str) -> Optional[List[SearchCandidate]]:
        normalized_q = self.normalize_text(query)
        for cached_query, results in self.cache.items():
            if self.get_jaccard_similarity(normalized_q, cached_query) >= self.similarity_threshold:
                return results
        return None

    def store(self, query: str, results: List[SearchCandidate]):
        normalized_q = self.normalize_text(query)
        self.cache[normalized_q] = results
