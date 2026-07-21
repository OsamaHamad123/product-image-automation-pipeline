# verification_layer/use_cases/string_distance_calculator.py
from verification_layer.domain.models import SurfaceCurvature
from verification_layer.domain.value_objects import StringDistanceScore


class StringDistanceCalculator:
    """
    حساب نسب التسامح ومسافات النص للعبوات المستوية والمنحنية/الأسطوانية.
    - مسافة ليفنشتاين المقيسة للطول: S_lev = 1 - Levenshtein(s1, s2) / max(|s1|, |s2|)
    - خوارزمية جارو-وينكلر: S_jw = S_j + p * l * (1 - S_j)
    """

    @staticmethod
    def levenshtein_distance(s1: str, s2: str) -> int:
        if not s1:
            return len(s2)
        if not s2:
            return len(s1)

        m, n = len(s1), len(s2)
        dp = [[0] * (n + 1) for _ in range(m + 1)]

        for i in range(m + 1):
            dp[i][0] = i
        for j in range(n + 1):
            dp[0][j] = j

        for i in range(1, m + 1):
            for j in range(1, n + 1):
                cost = 0 if s1[i - 1] == s2[j - 1] else 1
                dp[i][j] = min(
                    dp[i - 1][j] + 1,      # Deletion
                    dp[i][j - 1] + 1,      # Insertion
                    dp[i - 1][j - 1] + cost # Substitution
                )
        return dp[m][n]

    @classmethod
    def length_normalized_levenshtein(cls, s1: str, s2: str) -> float:
        str1 = (s1 or "").strip().lower()
        str2 = (s2 or "").strip().lower()

        if not str1 and not str2:
            return 1.0
        max_len = max(len(str1), len(str2))
        if max_len == 0:
            return 1.0

        dist = cls.levenshtein_distance(str1, str2)
        score = 1.0 - (dist / float(max_len))
        return max(0.0, min(1.0, score))

    @staticmethod
    def jaro_winkler_similarity(s1: str, s2: str, p: float = 0.1) -> float:
        str1 = (s1 or "").strip().lower()
        str2 = (s2 or "").strip().lower()

        if str1 == str2:
            return 1.0
        len1, len2 = len(str1), len(str2)
        if len1 == 0 or len2 == 0:
            return 0.0

        match_distance = (max(len1, len2) // 2) - 1
        if match_distance < 0:
            match_distance = 0

        str1_matches = [False] * len1
        str2_matches = [False] * len2

        matches = 0
        transpositions = 0

        for i in range(len1):
            start = max(0, i - match_distance)
            end = min(i + match_distance + 1, len2)
            for j in range(start, end):
                if str2_matches[j]:
                    continue
                if str1[i] != str2[j]:
                    continue
                str1_matches[i] = True
                str2_matches[j] = True
                matches += 1
                break

        if matches == 0:
            return 0.0

        k = 0
        for i in range(len1):
            if not str1_matches[i]:
                continue
            while not str2_matches[k]:
                k += 1
            if str1[i] != str2[k]:
                transpositions += 1
            k += 1

        t = transpositions / 2.0
        s_j = (1.0 / 3.0) * (
            (matches / float(len1))
            + (matches / float(len2))
            + ((matches - t) / float(matches))
        )

        # Common prefix length up to 4 characters
        l = 0
        for i in range(min(4, len1, len2)):
            if str1[i] == str2[i]:
                l += 1
            else:
                break

        s_jw = s_j + (l * p * (1.0 - s_j))
        return max(0.0, min(1.0, s_jw))

    @classmethod
    def compute_all_distances(cls, s1: str, s2: str) -> StringDistanceScore:
        lev = cls.levenshtein_distance(s1, s2)
        norm_lev = cls.length_normalized_levenshtein(s1, s2)
        jw = cls.jaro_winkler_similarity(s1, s2)
        return StringDistanceScore(
            levenshtein_distance=lev,
            length_normalized_levenshtein=round(norm_lev, 4),
            jaro_winkler_similarity=round(jw, 4),
        )

    @classmethod
    def is_brand_matched(
        cls, s1: str, s2: str, surface: SurfaceCurvature = SurfaceCurvature.FLAT
    ) -> bool:
        score = cls.jaro_winkler_similarity(s1, s2)
        threshold = 0.95 if surface == SurfaceCurvature.FLAT else 0.88
        return score >= threshold
