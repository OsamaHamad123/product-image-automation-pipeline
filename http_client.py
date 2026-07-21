# http_client.py
# موديول جلب البيانات المعزز بمحاكاة المتصفحات (Impersonation Client) لتجنب الحظر

import time
import random
from curl_cffi import requests

class ImpersonateClient:
    """
    عميل HTTP معزز بمحاكاة متصفح Chrome 120 لتجاوز جدران الحماية WAF (مثل Cloudflare).
    يحتوي على تدوير الترويسات (Headers) وإدارة الـ Sticky Sessions وإعادة المحاولة التلقائية.
    """

    def __init__(self, use_proxy=False, proxy_url=None):
        self.use_proxy = use_proxy
        self.proxy_url = proxy_url
        self.session = requests.Session()

    def get(self, url, headers=None, timeout=30):
        """
        إجراء طلب GET متكامل بمحاكاة Chrome 120 وإعادة المحاولة عند مواجهة الخطأ 429.
        """
        # الترويسات الافتراضية المرتبة لمتصفح Chrome
        import urllib.parse
        host_netloc = urllib.parse.urlparse(url).netloc
        
        default_headers = {
            "Host": host_netloc,
            "Connection": "keep-alive",
            "sec-ch-ua": '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "Upgrade-Insecure-Requests": "1",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-User": "?1",
            "Sec-Fetch-Dest": "document",
            "Accept-Encoding": "gzip, deflate, br",
            "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
        }

        if headers:
            default_headers.update(headers)

        proxies = {"http": self.proxy_url, "https": self.proxy_url} if (self.use_proxy and self.proxy_url) else None

        attempts = 0
        max_attempts = 5
        base_delay = 2.0

        while attempts < max_attempts:
            try:
                response = self.session.get(
                    url,
                    headers=default_headers,
                    impersonate="chrome120", # محاكاة كاملة لـ BoringSSL Chrome 120
                    proxies=proxies,
                    timeout=timeout
                )
                
                # التعامل مع رمز 429 (Too Many Requests)
                if response.status_code == 429:
                    attempts += 1
                    jitter = random.uniform(0.1, 1.0)
                    delay = base_delay * (2 ** attempts) + jitter
                    print(f"⚠️ [HTTP Client] واجهنا الخطأ 429 (كثير من الطلبات). جاري إعادة المحاولة خلال {delay:.2f} ثانية...")
                    time.sleep(delay)
                    continue
                    
                return response
            except Exception as e:
                attempts += 1
                jitter = random.uniform(0.1, 1.0)
                delay = base_delay * (2 ** attempts) + jitter
                print(f"⚠️ [HTTP Client] خطأ أثناء جلب الرابط ({url}): {e}. إعادة المحاولة {attempts}/{max_attempts} خلال {delay:.2f} ثانية...")
                time.sleep(delay)

        print(f"❌ [HTTP Client] فشل جلب الرابط ({url}) بعد {max_attempts} محاولات.")
        return None

    def download_image(self, url, timeout=15):
        """
        تنزيل محتوى الصورة الثنائي مباشرة.
        """
        response = self.get(url, timeout=timeout)
        if response and response.status_code == 200:
            # التحقق من أن الملف المحمل هو صورة فعلاً
            content_type = response.headers.get("Content-Type", "")
            if "image" in content_type:
                return response.content
            else:
                # التحقق كخيار احتياطي من بداية البيانات الثنائية
                if response.content.startswith(b'\xff\xd8') or response.content.startswith(b'\x89PNG') or response.content.startswith(b'RIFF'):
                    return response.content
                print(f"⚠️ [HTTP Client] الرابط لا يشير إلى ملف صورة صالح (Content-Type: {content_type})")
        return None
