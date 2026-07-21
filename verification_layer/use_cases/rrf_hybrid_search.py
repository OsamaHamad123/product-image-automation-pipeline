# verification_layer/use_cases/rrf_hybrid_search.py
from typing import List, Dict, Any, Tuple


class ReciprocalRankFusionEngine:
    """
    محرك الدمج التبادلي للرتب (Reciprocal Rank Fusion - RRF)
    دمج نتائج محركات البحث النصية الصارمة (BM25) مع نتائج محركات التضمين البصري الدلالي (SigLIP-2 / CLIP)
    RRF(d) = sum_{m in M} 1 / (k + r_m(d))
    حيث k = 60 لتقليل انحياز الرتب المتقدمة.
    """

    def __init__(self, k: int = 60):
        self.k = k

    def combine_rankings(
        self, ranking_lists: List[List[Dict[str, Any]]], id_key: str = "id"
    ) -> List[Dict[str, Any]]:
        """
        تستقبل قوائم النتائج المصنفة مرتبة من كل نموذج، وتحسب نتيجة RRF الموحدة لكل عنصر.
        """
        rrf_scores: Dict[str, float] = {}
        item_metadata: Dict[str, Dict[str, Any]] = {}

        for rank_list in ranking_lists:
            for rank_idx, item in enumerate(rank_list, start=1):
                item_id = str(item.get(id_key))
                if not item_id:
                    continue

                if item_id not in item_metadata:
                    item_metadata[item_id] = item

                rrf_score = 1.0 / (self.k + rank_idx)
                rrf_scores[item_id] = rrf_scores.get(item_id, 0.0) + rrf_score

        # Sorting results by highest RRF score
        sorted_ids = sorted(rrf_scores.keys(), key=lambda x: rrf_scores[x], reverse=True)

        final_ranked_results = []
        for doc_id in sorted_ids:
            merged_item = dict(item_metadata[doc_id])
            merged_item["rrf_score"] = round(rrf_scores[doc_id], 6)
            final_ranked_results.append(merged_item)

        return final_ranked_results
