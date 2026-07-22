# verification_layer/use_cases/multimodal_spec_audit.py
"""
Multi-Modal Spec-Visual Consistency Audit Engine.
Calculates Normalized Levenshtein Text Similarity & IoU Geometry Alignment.
"""

from typing import Tuple, List, Dict, Any
from verification_layer.domain.nextgen_models import SpecAuditResult


def calculate_levenshtein_distance(s1: str, s2: str) -> int:
    """Calculates Levenshtein distance between two strings using DP array."""
    if len(s1) < len(s2):
        return calculate_levenshtein_distance(s2, s1)
    if len(s2) == 0:
        return len(s1)

    previous_row = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        current_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (c1 != c2)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row

    return previous_row[-1]


def calculate_normalized_levenshtein_similarity(s1: str, s2: str) -> float:
    """Calculates Normalized Levenshtein Similarity: Sim = 1 - (Lev / max_len)."""
    clean_s1 = s1.strip().lower()
    clean_s2 = s2.strip().lower()
    max_len = max(len(clean_s1), len(clean_s2))
    if max_len == 0:
        return 1.0
    dist = calculate_levenshtein_distance(clean_s1, clean_s2)
    return round(1.0 - (dist / max_len), 4)


def calculate_iou(boxA: Tuple[int, int, int, int], boxB: Tuple[int, int, int, int]) -> float:
    """
    Calculates Intersection over Union (IoU) of two bounding boxes.
    Format: (x1, y1, x2, y2)
    """
    xA = max(boxA[0], boxB[0])
    yA = max(boxA[1], boxB[1])
    xB = min(boxA[2], boxB[2])
    yB = min(boxA[3], boxB[3])

    interArea = max(0, xB - xA) * max(0, yB - yA)
    boxAArea = max(0, boxA[2] - boxA[0]) * max(0, boxA[3] - boxA[1])
    boxBArea = max(0, boxB[2] - boxB[0]) * max(0, boxB[3] - boxB[1])

    unionArea = float(boxAArea + boxBArea - interArea)
    if unionArea == 0.0:
        return 0.0
    return round(float(interArea / unionArea), 4)


class MultiModalSpecAuditUseCase:
    def __init__(self, text_threshold: float = 0.80, iou_threshold: float = 0.50):
        self.text_threshold = text_threshold
        self.iou_threshold = iou_threshold

    def audit_spec_consistency(
        self,
        target_spec_text: str,
        detected_text_boxes: List[Dict[str, Any]],
        expected_bounding_box: Tuple[int, int, int, int] = (10, 10, 200, 200)
    ) -> SpecAuditResult:
        best_sim = 0.0
        best_matched_box = None
        best_iou = 0.0

        for item in detected_text_boxes:
            detected_text = item.get("text", "")
            box = item.get("box", (0, 0, 0, 0))

            sim = calculate_normalized_levenshtein_similarity(target_spec_text, detected_text)
            iou = calculate_iou(expected_bounding_box, box)

            if sim > best_sim:
                best_sim = sim
                best_matched_box = box
                best_iou = iou

        is_consistent = (best_sim >= self.text_threshold) and (best_iou >= self.iou_threshold)
        status = "PASSED_SPEC_MATCH" if is_consistent else "FAILED_SPEC_DISCREPANCY"

        return SpecAuditResult(
            spec_text=target_spec_text,
            text_similarity=best_sim,
            matched_bounding_box=best_matched_box,
            iou_score=best_iou,
            is_consistent=is_consistent,
            status=status
        )
