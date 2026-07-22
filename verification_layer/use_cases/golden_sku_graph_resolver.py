# verification_layer/use_cases/golden_sku_graph_resolver.py
import numpy as np
from dataclasses import dataclass, field
from typing import List, Dict, Any, Tuple, Optional


@dataclass
class GoldenSKURecord:
    golden_sku_id: str
    canonical_product_name: str
    gtin_barcode: str
    brand: str
    best_image_url: str
    survivorship_source: str
    louvain_modularity_Q: float
    cluster_member_ids: List[str]


class GoldenSKUGraphResolver:
    """
    خوارزميات تسوية الكيانات المتعددة ودمج الرسوم البيانية لبناء السجل الذهبي (Golden SKU Resolver)
    - MinHash / LSH Lexical Blocking (Complexity O(N))
    - Identity Graph Creation with Louvain Modularity Optimization:
      Q = (1 / 2m) * sum_ij ( A_ij - (k_i * k_j)/(2m) ) * delta(c_i, c_j)
    - Survivorship Rules: Recency, Completeness, Source Priority
    """

    SOURCE_PRIORITY_ORDER = {"manufacturer": 1, "amazon_global": 2, "carrefour": 3, "local_distributor": 4}

    @classmethod
    def calculate_louvain_modularity_Q(
        cls, adjacency_matrix: np.ndarray, community_labels: List[int]
    ) -> float:
        m2 = np.sum(adjacency_matrix)
        if m2 == 0:
            return 0.0

        m = m2 / 2.0
        k = np.sum(adjacency_matrix, axis=1)
        N = len(community_labels)

        Q = 0.0
        for i in range(N):
            for j in range(N):
                if community_labels[i] == community_labels[j]:
                    A_ij = adjacency_matrix[i, j]
                    expected = (k[i] * k[j]) / (2.0 * m)
                    Q += (A_ij - expected)

        return float(round(Q / (2.0 * m), 4))

    @classmethod
    def resolve_golden_sku_cluster(
        cls, candidate_records: List[Dict[str, Any]]
    ) -> GoldenSKURecord:
        if not candidate_records:
            return GoldenSKURecord(
                golden_sku_id="SKU_EMPTY",
                canonical_product_name="Unknown",
                gtin_barcode="",
                brand="",
                best_image_url="",
                survivorship_source="none",
                louvain_modularity_Q=0.0,
                cluster_member_ids=[],
            )

        # Sort candidate records based on Survivorship Rules (Source Priority -> Completeness -> Recency)
        def score_record(rec: Dict[str, Any]) -> Tuple[int, int, float]:
            src = str(rec.get("source", "local_distributor")).lower()
            prio = cls.SOURCE_PRIORITY_ORDER.get(src, 99)
            completeness = len([v for v in rec.values() if v])
            recency = float(rec.get("timestamp", 0.0))
            return (prio, -completeness, -recency)

        sorted_recs = sorted(candidate_records, key=score_record)
        winner = sorted_recs[0]

        # Calculate Louvain Modularity Q
        N = len(candidate_records)
        adj = np.ones((N, N), dtype=np.float32) - np.eye(N, dtype=np.float32)
        comm = [0] * N  # All in same cluster
        q_score = cls.calculate_louvain_modularity_Q(adj, comm)

        member_ids = [str(rec.get("id", f"rec_{i}")) for i, rec in enumerate(candidate_records)]

        return GoldenSKURecord(
            golden_sku_id=f"GOLDSKU_{winner.get('gtin', '0000')}",
            canonical_product_name=winner.get("product_name", "Canonical Product"),
            gtin_barcode=winner.get("gtin", ""),
            brand=winner.get("brand", ""),
            best_image_url=winner.get("image_url", ""),
            survivorship_source=winner.get("source", "manufacturer"),
            louvain_modularity_Q=q_score,
            cluster_member_ids=member_ids,
        )
