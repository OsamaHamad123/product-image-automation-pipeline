# verification_layer/use_cases/dom_self_healing.py
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from verification_layer.use_cases.string_distance_calculator import StringDistanceCalculator


@dataclass
class DOMElementFingerprint:
    tag_name: str
    element_id: str = ""
    name: str = ""
    visible_text: str = ""
    neighbor_text: str = ""
    class_name: str = ""
    xpath: str = ""
    x: float = 0.0
    y: float = 0.0


@dataclass
class SimiloMatchResult:
    matched_element: Optional[DOMElementFingerprint]
    similarity_score: float
    is_exact_match: bool


class SimiloDOMSelfHealingEngine:
    """
    خوارزمية Similo لحساب درجة التقارب والتشابه بين عناصر الـ DOM للشفاء الذاتي
    - Tag Name: weight 1.5
    - ID: weight 1.5
    - Name: weight 1.5
    - Visible Text: Levenshtein weight 1.5
    - Neighbor Text: Word Intersection weight 1.5
    - Class Name: Levenshtein weight 0.5
    - Absolute XPath: Levenshtein weight 0.5
    - Coordinates: Euclidean Distance weight 0.5
    """

    FEATURE_WEIGHTS = {
        "tag": 1.5,
        "id": 1.5,
        "name": 1.5,
        "text": 1.5,
        "neighbor": 1.5,
        "class": 0.5,
        "xpath": 0.5,
        "coords": 0.5,
    }

    TOTAL_WEIGHT = sum(FEATURE_WEIGHTS.values())  # 9.0

    @classmethod
    def calculate_similo_score(
        cls, target: DOMElementFingerprint, candidate: DOMElementFingerprint
    ) -> float:
        weighted_score = 0.0

        # 1. Tag Name Match (1.5)
        if target.tag_name.lower() == candidate.tag_name.lower():
            weighted_score += cls.FEATURE_WEIGHTS["tag"]

        # 2. Element ID Match (1.5)
        if target.element_id and candidate.element_id:
            if target.element_id == candidate.element_id:
                weighted_score += cls.FEATURE_WEIGHTS["id"]

        # 3. Element Name Match (1.5)
        if target.name and candidate.name:
            if target.name == candidate.name:
                weighted_score += cls.FEATURE_WEIGHTS["name"]

        # 4. Visible Text Levenshtein Similarity (1.5)
        if target.visible_text or candidate.visible_text:
            text_sim = StringDistanceCalculator.length_normalized_levenshtein(
                target.visible_text, candidate.visible_text
            )
            weighted_score += cls.FEATURE_WEIGHTS["text"] * text_sim

        # 5. Neighbor Text Intersection Similarity (1.5)
        if target.neighbor_text or candidate.neighbor_text:
            t_words = set(target.neighbor_text.lower().split())
            c_words = set(candidate.neighbor_text.lower().split())
            if t_words or c_words:
                intersection = len(t_words.intersection(c_words))
                union = len(t_words.union(c_words))
                jaccard = intersection / float(union) if union > 0 else 0.0
                weighted_score += cls.FEATURE_WEIGHTS["neighbor"] * jaccard

        # 6. Class Name Levenshtein Similarity (0.5)
        if target.class_name or candidate.class_name:
            class_sim = StringDistanceCalculator.length_normalized_levenshtein(
                target.class_name, candidate.class_name
            )
            weighted_score += cls.FEATURE_WEIGHTS["class"] * class_sim

        # 7. Absolute XPath Levenshtein Similarity (0.5)
        if target.xpath or candidate.xpath:
            xpath_sim = StringDistanceCalculator.length_normalized_levenshtein(
                target.xpath, candidate.xpath
            )
            weighted_score += cls.FEATURE_WEIGHTS["xpath"] * xpath_sim

        # 8. Coordinates Euclidean Distance Similarity (0.5)
        dx = target.x - candidate.x
        dy = target.y - candidate.y
        dist = np.sqrt(dx**2 + dy**2) if 'np' in globals() else (dx**2 + dy**2)**0.5
        coords_sim = max(0.0, 1.0 - (dist / 1000.0))
        weighted_score += cls.FEATURE_WEIGHTS["coords"] * coords_sim

        normalized_score = weighted_score / cls.TOTAL_WEIGHT
        return float(round(normalized_score, 4))

    @classmethod
    def find_best_self_healed_element(
        cls, target: DOMElementFingerprint, candidates: List[DOMElementFingerprint], min_score_threshold: float = 0.50
    ) -> SimiloMatchResult:
        if not candidates:
            return SimiloMatchResult(matched_element=None, similarity_score=0.0, is_exact_match=False)

        best_cand = None
        best_score = 0.0

        for cand in candidates:
            score = cls.calculate_similo_score(target, cand)
            if score > best_score:
                best_score = score
                best_cand = cand

        if best_score >= min_score_threshold:
            is_exact = best_score >= 0.98
            return SimiloMatchResult(matched_element=best_cand, similarity_score=best_score, is_exact_match=is_exact)

        return SimiloMatchResult(matched_element=None, similarity_score=best_score, is_exact_match=False)

    @staticmethod
    def prune_dom_tree_for_llm_rescue(raw_html_snippet: str) -> str:
        """
        اختزال شجرة كائن المستند (DOM Pruning) بإزالة السكريات والتنسيقات غير المؤثرة
        والإبقاء فقط على العناصر الهيكلية وسمات الاختيار.
        """
        import re
        # Strip script, style, and svg tags
        clean_html = re.sub(r"(?is)<(script|style|svg).*?>.*?</\1>", "", raw_html_snippet)
        # Strip inline style attributes
        clean_html = re.sub(r'(?i)\s+style="[^"]*"', "", clean_html)
        return clean_html.strip()
