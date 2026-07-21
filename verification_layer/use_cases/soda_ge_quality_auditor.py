# verification_layer/use_cases/soda_ge_quality_auditor.py
from dataclasses import dataclass
from typing import List, Dict, Any, Optional


@dataclass
class QualityAuditReport:
    null_rate_pct: float
    has_schema_drift: bool
    is_silent_empty_captcha: bool
    is_data_quality_passed: bool
    reasons: List[str]


class SodaGEDataQualityAuditor:
    """
    مدقق جودة البيانات ورصد الانزياح الهيكلي (SodaCL & Great Expectations Quality Auditor)
    - فحص نسب القيم الفارغة (Null Rates).
    - رصد انزياح الـ Schema والـ DOM.
    - كشف النتائج الفارغة الصامتة (Silent HTTP 200 Captcha / Soft-Ban pages).
    """

    CAPTCHA_KEYWORDS = ["captcha", "access denied", "cloudflare", "turnstile", "verify you are human", "security check"]

    @classmethod
    def audit_null_rates(cls, records: List[Dict[str, Any]], essential_keys: List[str]) -> float:
        if not records or not essential_keys:
            return 0.0

        total_checks = len(records) * len(essential_keys)
        null_count = 0

        for rec in records:
            for k in essential_keys:
                val = rec.get(k)
                if val is None or val == "" or val == []:
                    null_count += 1

        null_rate = (null_count / float(total_checks)) * 100.0
        return float(round(null_rate, 2))

    @classmethod
    def detect_silent_captcha_page(cls, http_status_code: int, html_text: str) -> bool:
        """
        كشف النتائج الفارغة الصامتة: استجابة HTTP 200 تم ترحيلها بنجاح لكن المحتوى عبارة عن صفحة كابتشا/حظر.
        """
        if http_status_code != 200 or not html_text:
            return False

        lower_html = html_text.lower()
        for kw in cls.CAPTCHA_KEYWORDS:
            if kw in lower_html:
                return True
        return False

    @classmethod
    def detect_schema_drift(cls, current_schema_keys: List[str], expected_schema_keys: List[str]) -> bool:
        current_set = set(current_schema_keys)
        expected_set = set(expected_schema_keys)
        return not expected_set.issubset(current_set)

    @classmethod
    def run_full_quality_audit(
        cls,
        records: List[Dict[str, Any]],
        essential_keys: List[str],
        expected_schema_keys: List[str],
        sample_http_status: int = 200,
        sample_html_text: str = "",
    ) -> QualityAuditReport:
        null_rate = cls.audit_null_rates(records, essential_keys)
        current_keys = list(records[0].keys()) if records else []
        schema_drift = cls.detect_schema_drift(current_keys, expected_schema_keys)
        is_silent_captcha = cls.detect_silent_captcha_page(sample_http_status, sample_html_text)

        reasons = []
        is_passed = True

        if null_rate > 5.0:
            is_passed = False
            reasons.append(f"Null Rate ({null_rate}%) exceeds maximum allowable threshold (5.0%).")

        if schema_drift:
            is_passed = False
            reasons.append("Schema drift detected: missing expected essential catalog keys.")

        if is_silent_captcha:
            is_passed = False
            reasons.append("Silent HTTP 200 Captcha / WAF soft-ban page detected in response body.")

        if is_passed:
            reasons.append("Data quality audit passed all SodaCL & Great Expectations assertions.")

        return QualityAuditReport(
            null_rate_pct=null_rate,
            has_schema_drift=schema_drift,
            is_silent_empty_captcha=is_silent_captcha,
            is_data_quality_passed=is_passed,
            reasons=reasons,
        )
