# verification_layer/use_cases/heal_image.py
"""
Self-Healing Image Pipeline Use Case.
Calculates RRF scores with epsilon-protected normalization clipped to [0%, 100%].
Invalidates corrupted cache records and falls back to Google WebCache if all candidates fail.
"""

from typing import List, Dict, Any, Tuple, Optional
from verification_layer.infrastructure.image_cache_repository import ImageCacheRepository
from verification_layer.use_cases.image_validation_service import ImageValidationService


class HealImageUseCase:
    def __init__(self, repo: Optional[ImageCacheRepository] = None):
        self.repo = repo if repo else ImageCacheRepository()

    def calculate_rrf(
        self,
        list_a: List[str],
        list_b: List[str],
        k: int = 60
    ) -> List[Dict[str, Any]]:
        """
        Calculates Reciprocal Rank Fusion: RRF(d) = SUM ( 1 / (k + r_m(d)) )
        Applies epsilon-protected score normalization clipped strictly between [0%, 100%].
        """
        scores: Dict[str, float] = {}
        for rank, url in enumerate(list_a, start=1):
            scores[url] = scores.get(url, 0.0) + (1.0 / (k + rank))
        for rank, url in enumerate(list_b, start=1):
            scores[url] = scores.get(url, 0.0) + (1.0 / (k + rank))

        if not scores:
            return []

        sorted_scores = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        max_score = sorted_scores[0][1]
        min_score = sorted_scores[-1][1]
        score_range = max_score - min_score
        epsilon = 1e-9

        normalized_candidates = []
        for url, val in sorted_scores:
            if score_range == 0.0:
                normalized = 100.0
            else:
                normalized = ((val - min_score) / (score_range + epsilon)) * 100.0

            clipped = min(100.0, max(0.0, float(normalized)))
            normalized_candidates.append({
                "url": url,
                "percentage": round(clipped, 2)
            })
        return normalized_candidates

    async def execute(
        self,
        product_id: int,
        broken_url: str,
        candidates_a: List[str],
        candidates_b: List[str]
    ) -> Dict[str, Any]:
        # 1. Invalidate corrupted cache record immediately
        self.repo.invalidate_cache(product_id, broken_url)

        # 2. RRF ranking & score normalization
        ranked_list = self.calculate_rrf(candidates_a, candidates_b)

        # 3. Sequential verification of alternative candidates
        for item in ranked_list:
            if item["url"] == broken_url:
                continue
            is_valid, _ = await ImageValidationService.verify_integrity(item["url"])
            if is_valid:
                self.repo.update_valid_cache(product_id, item["url"])
                return {
                    "product_id": product_id,
                    "resolved_url": item["url"],
                    "match_percentage": item["percentage"],
                    "status": "Healed"
                }

        # 4. Fallback cascade to Google WebCache if all candidates fail
        fallback_web_cache = f"https://webcache.googleusercontent.com/search?q=cache:{broken_url}"
        self.repo.update_valid_cache(product_id, fallback_web_cache)
        return {
            "product_id": product_id,
            "resolved_url": fallback_web_cache,
            "match_percentage": 100.0,
            "status": "GoogleWebCacheFallback"
        }
