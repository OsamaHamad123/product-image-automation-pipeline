# verification_layer/use_cases/hybrid_crawler_pool.py
import asyncio
import time
from dataclasses import dataclass, field
from typing import Optional, Dict, Any


class CircuitBreakerTripped(Exception):
    """قاطع التيار البرمجي: يتفعل لحماية أسطول البروكسيات والشبكة عند تجاوز نسب الفشل للعتبة الأمنية (90%)."""
    pass


@dataclass
class SessionToken:
    proxy_url: str
    cf_clearance: str
    user_agent: str
    created_at: float
    is_active: bool = True


class TwoTierHybridCrawlerPool:
    """
    أسطول الزحف المزدوج الموزع وقواطع التيار (Two-Tier Hybrid Session Crawler Pool)
    - Tier 1 (Token Generator): حل تحديات جافا سكريبت وتوليد كعكات الجلسات cf_clearance.
    - Tier 2 (Fast Lightweight Fetcher): إعادة استخدام كعكات الجلسات مع مكتبات الاتصال السريعة curl_cffi / aiohttp بمحاكاة بصمات JA4.
    - Circuit Breaker: قاطع التيار الآلي عند تسجيل معدل فشل يتجاوز 90%.
    """

    MAX_FAILURE_RATE = 0.90  # %90
    MIN_ATTEMPTS_BEFORE_TRIP = 10

    def __init__(self):
        self._session_store: Dict[str, SessionToken] = {}
        self._total_attempts = 0
        self._failed_attempts = 0
        self.circuit_breaker_tripped = False

    def store_session_token(self, proxy_url: str, cf_clearance: str, user_agent: str) -> SessionToken:
        token = SessionToken(
            proxy_url=proxy_url,
            cf_clearance=cf_clearance,
            user_agent=user_agent,
            created_at=time.time(),
            is_active=True,
        )
        self._session_store[proxy_url] = token
        return token

    def get_valid_session_token(self, proxy_url: str, max_age_seconds: float = 1800.0) -> Optional[SessionToken]:
        token = self._session_store.get(proxy_url)
        if not token or not token.is_active:
            return None

        # Check token expiration
        if (time.time() - token.created_at) > max_age_seconds:
            token.is_active = False
            return None

        return token

    def record_request_outcome(self, is_success: bool):
        self._total_attempts += 1
        if not is_success:
            self._failed_attempts += 1

        if self._total_attempts >= self.MIN_ATTEMPTS_BEFORE_TRIP:
            failure_rate = self._failed_attempts / float(self._total_attempts)
            if failure_rate >= self.MAX_FAILURE_RATE:
                self.circuit_breaker_tripped = True
                raise CircuitBreakerTripped(
                    f"⚡ Circuit Breaker Tripped! Failure rate ({failure_rate*100:.1f}%) exceeds maximum threshold (90%). "
                    f"Proxy sector traffic automatically halted."
                )

    def execute_fast_lightweight_fetch(self, target_url: str, proxy_url: str) -> Tuple[bool, str]:
        """
        استدعاء سريع منخفض التكلفة الحسابية باستعمال curl_cffi / aiohttp وبصمة JA4 المحاكاة.
        """
        if self.circuit_breaker_tripped:
            raise CircuitBreakerTripped("Circuit breaker is currently TRIPPED. Execution blocked.")

        token = self.get_valid_session_token(proxy_url)
        headers = {
            "User-Agent": token.user_agent if token else "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/123.0.0.0 Safari/537.36",
            "Accept-Language": "ar-SA,ar;q=0.9,en-US;q=0.8,en;q=0.7",
        }
        if token and token.cf_clearance:
            headers["Cookie"] = f"cf_clearance={token.cf_clearance}"

        try:
            # Try lightweight curl_cffi or fallback requests
            try:
                from curl_cffi import requests as curl_req
                resp = curl_req.get(target_url, headers=headers, proxies={"http": proxy_url, "https": proxy_url}, timeout=10, impersonate="chrome120")
                is_success = resp.status_code == 200
                html_text = resp.text
            except Exception:
                import requests
                resp = requests.get(target_url, headers=headers, timeout=10)
                is_success = resp.status_code == 200
                html_text = resp.text

            self.record_request_outcome(is_success)
            return is_success, html_text
        except CircuitBreakerTripped:
            raise
        except Exception as e:
            self.record_request_outcome(is_success=False)
            return False, str(e)
