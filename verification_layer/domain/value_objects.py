# verification_layer/domain/value_objects.py
from dataclasses import dataclass
from typing import Optional
from enum import Enum


class MetricUnit(str, Enum):
    GRAM = "g"
    MILLILITER = "ml"


@dataclass(frozen=True)
class NormalizedMetric:
    raw_text: str
    numeric_value: float
    unit: MetricUnit

    def to_standard_str(self) -> str:
        if self.unit == MetricUnit.GRAM:
            return f"{self.numeric_value:.1f}g"
        return f"{self.numeric_value:.1f}ml"


@dataclass(frozen=True)
class StringDistanceScore:
    levenshtein_distance: int
    length_normalized_levenshtein: float  # S_lev in [0, 1]
    jaro_winkler_similarity: float        # S_jw in [0, 1]
