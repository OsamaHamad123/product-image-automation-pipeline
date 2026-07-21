# verification_layer/use_cases/bilingual_text_processor.py
import re
from typing import Optional, List, Tuple
from verification_layer.domain.value_objects import NormalizedMetric, MetricUnit


class BilingualTextProcessor:
    """
    محرك استخراج النصوص ثنائية اللغة وتطبيع المقاييس (Bilingual Metric Extraction & Normalization)
    - ترجمة الأرقام الشرقية (الأرقام الهندية: ٠-٩) إلى أرقام غربية (0-9).
    - استخراج وحدات الوزن والحجم بالإنجليزية والعربية وتوحيدها إلى غرام (g) أو مليلتر (ml).
    """

    EASTERN_TO_WESTERN_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")

    # Weight Patterns
    ENGLISH_WEIGHT_PATTERN = re.compile(
        r"(?i)\b(\d+(?:\.\d+)?)\s*(g|gm|gms|grams|kg|kgs|kilograms)\b"
    )
    ARABIC_WEIGHT_PATTERN = re.compile(
        r"([\u0660-\u0669\d]+(?:\.[\u0660-\u0669\d]+)?)\s*(غرام|جرام|جم|كيلوغرام|كجم|كغم)"
    )

    # Volume Patterns
    ENGLISH_VOLUME_PATTERN = re.compile(
        r"(?i)\b(\d+(?:\.\d+)?)\s*(ml|milliliters|l|liters|oz|ounces|fl\.?\s*oz)\b"
    )
    ARABIC_VOLUME_PATTERN = re.compile(
        r"([\u0660-\u0669\d]+(?:\.[\u0660-\u0669\d]+)?)\s*(مل|مليلتر|لتر)"
    )

    @classmethod
    def convert_eastern_arabic_numerals(cls, text: str) -> str:
        """ترجمة الأرقام الهندية (٠-٩) إلى الأرقام الغربية (0-9)."""
        if not text:
            return ""
        return text.translate(cls.EASTERN_TO_WESTERN_DIGITS)

    @classmethod
    def extract_and_normalize_metric(cls, text: str) -> Optional[NormalizedMetric]:
        """
        استخراج وتطبيع معلومات الوزن أو الحجم من السلسلة النصية وإرجاع القيمة الموحدة.
        """
        if not text:
            return None

        # Step 1: Normalize numerals first
        clean_text = cls.convert_eastern_arabic_numerals(text)

        # Step 2: Try Weight (English)
        m = cls.ENGLISH_WEIGHT_PATTERN.search(clean_text)
        if m:
            val = float(m.group(1))
            unit_str = m.group(2).lower()
            if unit_str in ("kg", "kgs", "kilograms"):
                val *= 1000.0
            return NormalizedMetric(raw_text=m.group(0), numeric_value=val, unit=MetricUnit.GRAM)

        # Step 3: Try Weight (Arabic)
        m = cls.ARABIC_WEIGHT_PATTERN.search(clean_text)
        if m:
            val = float(cls.convert_eastern_arabic_numerals(m.group(1)))
            unit_str = m.group(2)
            if unit_str in ("كيلوغرام", "كجم", "كغم"):
                val *= 1000.0
            return NormalizedMetric(raw_text=m.group(0), numeric_value=val, unit=MetricUnit.GRAM)

        # Step 4: Try Volume (English)
        m = cls.ENGLISH_VOLUME_PATTERN.search(clean_text)
        if m:
            val = float(m.group(1))
            unit_str = m.group(2).lower()
            if unit_str in ("l", "liters"):
                val *= 1000.0
            elif "oz" in unit_str:
                val *= 29.5735  # fl oz to ml
            return NormalizedMetric(raw_text=m.group(0), numeric_value=val, unit=MetricUnit.MILLILITER)

        # Step 5: Try Volume (Arabic)
        m = cls.ARABIC_VOLUME_PATTERN.search(clean_text)
        if m:
            val = float(cls.convert_eastern_arabic_numerals(m.group(1)))
            unit_str = m.group(2)
            if unit_str == "لتر":
                val *= 1000.0
            return NormalizedMetric(raw_text=m.group(0), numeric_value=val, unit=MetricUnit.MILLILITER)

        return None

    @classmethod
    def compare_metrics(cls, text_a: str, text_b: str) -> float:
        """
        مقارنة مقياسين مستخرجين وإرجاع 1.0 للمطابقة التامة، 0.0 لعدم المطابقة، أو 0.5 عند عدم العثور.
        """
        metric_a = cls.extract_and_normalize_metric(text_a)
        metric_b = cls.extract_and_normalize_metric(text_b)

        if not metric_a or not metric_b:
            return 0.5  # Neutral when metric is missing on packaging

        if metric_a.unit != metric_b.unit:
            return 0.0  # Unit mismatch (g vs ml)

        # Allow 2% numerical tolerance for rounding differences
        diff_ratio = abs(metric_a.numeric_value - metric_b.numeric_value) / max(
            metric_a.numeric_value, metric_b.numeric_value
        )
        return 1.0 if diff_ratio <= 0.02 else 0.0
