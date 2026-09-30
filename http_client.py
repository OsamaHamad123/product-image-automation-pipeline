# http_client.py
# عميل HTTP لتنزيل صور المنتجات بمحاكاة متصفح Chrome مع التحقق الكامل من شهادات TLS.
#
# - ترويسة Accept خاصة بالصور فقط (لا نطلب صفحات HTML).
# - إعادة المحاولة فقط عند 429 أو أخطاء 5xx أو انتهاء المهلة، بحد أقصى محاولتين إضافيتين،
#   ولا يوجد انتظار بعد المحاولة الأخيرة.
# - حد أقصى لحجم الملف المنزّل.

import logging
import random
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

try:  # curl_cffi يحاكي بصمة TLS لمتصفح Chrome ويتحقق من الشهادات افتراضياً
    from curl_cffi import requests as _http
    _HAS_CURL_CFFI = True
except Exception:  # pragma: no cover - يعتمد على البيئة
    import requests as _http  # type: ignore
    _HAS_CURL_CFFI = False

logger = logging.getLogger(__name__)

IMAGE_ACCEPT = "image/webp,image/png,image/jpeg,image/*;q=0.8"
MAX_IMAGE_BYTES = 15 * 1024 * 1024
MAX_RETRIES = 2
BACKOFF_BASE_SECONDS = 1.0
MAX_BACKOFF_SECONDS = 8.0
IMPERSONATE = "chrome120"

_BASE_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"),
    "Accept": IMAGE_ACCEPT,
    "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
    "Sec-Fetch-Dest": "image",
    "Sec-Fetch-Mode": "no-cors",
    "Sec-Fetch-Site": "cross-site",
}

# بصمات بداية ملفات الصور المدعومة (تحقق احتياطي عندما تكون ترويسة Content-Type مضللة)
_IMAGE_MAGIC = (b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n", b"GIF87a", b"GIF89a", b"BM", b"II*\x00", b"MM\x00*")


@dataclass
class FetchResult:
    """نتيجة تنزيل صورة: البيانات أو رمز خطأ واضح."""

    content: Optional[bytes] = None
    status: Optional[int] = None
    content_type: str = ""
    error: Optional[str] = None      # http_<code> | timeout | connection_error | too_large | not_image
    attempts: int = 0


def _new_session():
    """إنشاء جلسة HTTP (دالة منفصلة ليتمكن الاختبار من استبدالها دون شبكة)."""
    return _http.Session()


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


def _is_timeout(exc: BaseException) -> bool:
    name = type(exc).__name__.lower()
    text = str(exc).lower()
    return "timeout" in name or "timed out" in text or "timeout" in text


def _looks_like_image(content_type: str, head: bytes) -> bool:
    ctype = (content_type or "").split(";")[0].strip().lower()
    if ctype.startswith("image/") and "svg" not in ctype:
        return True
    if head.startswith(_IMAGE_MAGIC):
        return True
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return True
    if head[4:8] == b"ftyp":  # AVIF / HEIF
        return True
    return False


class ImpersonateClient:
    """عميل تنزيل صور بمحاكاة متصفح، بإعادة محاولة محدودة وآمنة."""

    def __init__(self, use_proxy: bool = False, proxy_url: Optional[str] = None,
                 max_retries: int = MAX_RETRIES, backoff_base: float = BACKOFF_BASE_SECONDS):
        self.use_proxy = use_proxy
        self.proxy_url = proxy_url
        self.max_retries = max(0, int(max_retries))
        self.backoff_base = float(backoff_base)
        self.session = _new_session()

    # ------------------------------------------------------------------
    def _headers(self, extra: Optional[Dict[str, str]], referer: Optional[str]) -> Dict[str, str]:
        headers = dict(_BASE_HEADERS)
        if referer:
            headers["Referer"] = referer
        if extra:
            headers.update(extra)
        return headers

    def _request_kwargs(self, headers: Dict[str, str], timeout: float, stream: bool) -> Dict[str, Any]:
        kwargs: Dict[str, Any] = {"headers": headers, "timeout": timeout, "allow_redirects": True}
        if stream:
            kwargs["stream"] = True
        if self.use_proxy and self.proxy_url:
            kwargs["proxies"] = {"http": self.proxy_url, "https": self.proxy_url}
        if _HAS_CURL_CFFI:
            kwargs["impersonate"] = IMPERSONATE
        return kwargs

    def _delay(self, attempt: int, response: Any = None) -> float:
        retry_after = None
        if response is not None:
            try:
                retry_after = float(response.headers.get("Retry-After"))
            except (TypeError, ValueError, AttributeError):
                retry_after = None
        if retry_after is not None and retry_after >= 0:
            return min(retry_after, MAX_BACKOFF_SECONDS)
        return min(self.backoff_base * (2 ** attempt), MAX_BACKOFF_SECONDS) + random.uniform(0.0, 0.25)

    def _get_with_retries(self, url: str, headers: Dict[str, str], timeout: float,
                          stream: bool) -> Tuple[Any, Optional[str], int]:
        """
        يعيد (الاستجابة، رمز الخطأ، عدد المحاولات). يعيد المحاولة فقط عند 429 أو 5xx أو انتهاء المهلة.
        """
        kwargs = self._request_kwargs(headers, timeout, stream)
        total = self.max_retries + 1
        for attempt in range(total):
            is_last = attempt == total - 1
            try:
                response = self.session.get(url, **kwargs)
            except Exception as exc:  # noqa: BLE001 - نصنّف الخطأ ثم نقرر
                if _is_timeout(exc):
                    if is_last:
                        logger.warning("[HTTP Client] انتهت المهلة بعد %d محاولات: %s", attempt + 1, url)
                        return None, "timeout", attempt + 1
                    _sleep(self._delay(attempt))
                    continue
                logger.warning("[HTTP Client] فشل الاتصال (بدون إعادة محاولة): %s: %s", url, exc)
                return None, "connection_error", attempt + 1

            status = int(getattr(response, "status_code", 0) or 0)
            if (status == 429 or 500 <= status <= 599) and not is_last:
                logger.info("[HTTP Client] استجابة %d، إعادة المحاولة %d/%d: %s",
                            status, attempt + 1, self.max_retries, url)
                delay = self._delay(attempt, response)
                _close(response)
                _sleep(delay)
                continue
            return response, None, attempt + 1
        return None, "connection_error", total  # pragma: no cover - لا يصل التنفيذ إلى هنا

    # ------------------------------------------------------------------
    def get(self, url: str, headers: Optional[Dict[str, str]] = None, timeout: float = 30,
            referer: Optional[str] = None):
        """طلب GET مع سياسة إعادة المحاولة. يعيد الاستجابة الأخيرة أو None عند فشل الاتصال."""
        response, _, _ = self._get_with_retries(url, self._headers(headers, referer), timeout, stream=False)
        return response

    def fetch_image(self, url: str, timeout: float = 15, max_bytes: int = MAX_IMAGE_BYTES,
                    referer: Optional[str] = None) -> FetchResult:
        """تنزيل صورة مع حد أقصى للحجم وتحقق أولي من النوع. لا يرفع استثناءات."""
        response, error, attempts = self._get_with_retries(
            url, self._headers(None, referer), timeout, stream=True)
        if response is None:
            return FetchResult(error=error or "connection_error", attempts=attempts)
        try:
            status = int(getattr(response, "status_code", 0) or 0)
            content_type = str(response.headers.get("Content-Type", "") or "")
            if status != 200:
                return FetchResult(status=status, content_type=content_type,
                                   error=f"http_{status}", attempts=attempts)
            try:
                declared = int(response.headers.get("Content-Length") or 0)
            except (TypeError, ValueError):
                declared = 0
            if declared > max_bytes:
                return FetchResult(status=status, content_type=content_type, error="too_large", attempts=attempts)

            body, too_large = _read_body(response, max_bytes)
            if too_large:
                return FetchResult(status=status, content_type=content_type, error="too_large", attempts=attempts)
            if not body or not _looks_like_image(content_type, body[:16]):
                logger.warning("[HTTP Client] المحتوى ليس صورة (Content-Type: %s): %s", content_type, url)
                return FetchResult(status=status, content_type=content_type, error="not_image", attempts=attempts)
            return FetchResult(content=body, status=status, content_type=content_type, attempts=attempts)
        except Exception as exc:  # noqa: BLE001
            if _is_timeout(exc):
                return FetchResult(error="timeout", attempts=attempts)
            logger.warning("[HTTP Client] خطأ أثناء قراءة الاستجابة: %s: %s", url, exc)
            return FetchResult(error="connection_error", attempts=attempts)
        finally:
            _close(response)

    def download_image(self, url: str, timeout: float = 15) -> Optional[bytes]:
        """واجهة توافقية: تعيد بيانات الصورة أو None."""
        return self.fetch_image(url, timeout=timeout).content


def _read_body(response: Any, max_bytes: int) -> Tuple[bytes, bool]:
    """يقرأ جسم الاستجابة على دفعات ويتوقف عند تجاوز الحد. يعيد (البيانات، هل تجاوز الحد)."""
    iter_content = getattr(response, "iter_content", None)
    if callable(iter_content):
        chunks = []
        size = 0
        for chunk in iter_content(chunk_size=65536):
            if not chunk:
                continue
            size += len(chunk)
            if size > max_bytes:
                return b"", True
            chunks.append(chunk)
        return b"".join(chunks), False
    body = response.content or b""
    return (b"", True) if len(body) > max_bytes else (body, False)


def _close(response: Any) -> None:
    close = getattr(response, "close", None)
    if callable(close):
        try:
            close()
        except Exception:  # noqa: BLE001
            pass
