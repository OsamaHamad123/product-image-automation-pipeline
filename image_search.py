# image_search.py
# موديول للبحث عن صور المنتجات واختيار أفضل جودة وصلة بالمنتج والبراند

import os
import re
import tempfile
import requests
import urllib.parse
import config
import asyncio
import io
import aiohttp
import threading
try:
    import torch
    import torchvision.transforms as T
except ImportError:
    torch = None
    T = None
from PIL import Image
from bs4 import BeautifulSoup

# تهيئة شجرة BK-Tree العالمية لفحص التكرارات بصرياً بفعالية
_bktree = None
_bktree_lock = threading.Lock()

def get_bktree():
    global _bktree
    with _bktree_lock:
        if _bktree is None:
            try:
                import image_dedup_bktree
                print("⏳ [BKTree] Building BK-Tree from MariaDB for visual deduplication...")
                _bktree = image_dedup_bktree.build_bktree_from_db()
                print("✅ [BKTree] BK-Tree built successfully.")
            except Exception as e:
                print(f"⚠️ [BKTree Error] Failed to build BK-Tree: {e}")
                import image_dedup_bktree
                _bktree = image_dedup_bktree.BKTree()
        return _bktree

def run_coroutine_sync(coro):
    """
    تشغيل الكوروتين بشكل متزامن.
    إذا لم يكن هناك حلقة أحداث قيد التشغيل، يستخدم asyncio.run().
    إذا كانت حلقة أحداث قيد التشغيل بالفعل، يتم تشغيل الكوروتين في خيط مستقل
    باستخدام asyncio.run() لتفادي تعارض حلقة الأحداث وأخطاء aiohttp timeout.
    """
    try:
        asyncio.get_running_loop()
        result_holder = {}
        def _target():
            try:
                result_holder['result'] = asyncio.run(coro)
            except Exception as e:
                result_holder['error'] = e
        thread = threading.Thread(target=_target)
        thread.start()
        thread.join()
        if 'error' in result_holder:
            raise result_holder['error']
        return result_holder.get('result')
    except RuntimeError:
        return asyncio.run(coro)

class DINOv2EmbeddingEngine:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(DINOv2EmbeddingEngine, cls).__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self, use_gpu_if_available: bool = True):
        if self._initialized:
            return
        if getattr(config, "DISABLE_LOCAL_AI_MODELS", False) or torch is None or T is None:
            self.model = None
            self._initialized = True
            return
        self.device = torch.device(
            "cuda" if (torch.cuda.is_available() and use_gpu_if_available) else "cpu"
        )
        try:
            print("⏳ [DINOv2] Loading dinov2_vits14 model...")
            self.model = torch.hub.load("facebookresearch/dinov2", "dinov2_vits14")
            self.model.to(self.device)
            self.model.eval()
            if self.device.type == "cuda":
                self.model = self.model.half()
                self.data_type = torch.float16
            else:
                self.data_type = torch.float32

            self.transform = T.Compose([
                T.Resize(224, interpolation=T.InterpolationMode.BICUBIC),
                T.CenterCrop(224),
                T.ToTensor(),
                T.Normalize(
                    mean=[0.485, 0.456, 0.406], 
                    std=[0.229, 0.224, 0.225]
                )
            ])
            self._initialized = True
            print("✅ [DINOv2] Model loaded successfully.")
        except Exception as e:
            print(f"⚠️ [DINOv2 Error] Failed to load DINOv2: {e}")
            self.model = None

    def extract_features(self, pil_img: Image.Image) -> 'torch.Tensor':
        if self.model is None:
            return None
        try:
            if pil_img.mode != "RGB":
                pil_img = pil_img.convert("RGB")
            input_tensor = self.transform(pil_img).unsqueeze(0).to(self.device)
            if self.device.type == "cuda":
                input_tensor = input_tensor.half()
                
            with torch.no_grad():
                features = self.model(input_tensor)
                l2_norm = torch.linalg.norm(features, ord=2, dim=1, keepdim=True)
                normalized_features = features / l2_norm
            return normalized_features.squeeze(0).cpu()
        except Exception as e:
            print(f"⚠️ [DINOv2 Error] Failed to extract features: {e}")
            return None

    @staticmethod
    def calculate_distance(embedding_a: 'torch.Tensor', embedding_b: 'torch.Tensor') -> float:
        if embedding_a is None or embedding_b is None:
            return 0.0
        cosine_similarity = torch.dot(embedding_a, embedding_b)
        return float(cosine_similarity.item())

STOCKS_BLACKLIST_PAT = re.compile(
    r"^https?://(?:[a-zA-Z0-9-]+\.)*(?:"
    r"shutterstock|gettyimages|istockphoto|adobe|freepik|"
    r"alamy|dreamstime|vectorstock|depositphotos|123rf"
    r")\.[a-z]{2,6}/.*",
    re.IGNORECASE
)

DOMAIN_AUTHORITY_MULTIPLIERS = {
    "amazon.com": 1.5,
    "target.com": 1.4,
    "walmart.com": 1.4,
    "ebay.com": 1.1,
    "pinterest.com": 0.2,
    "istockphoto.com": 0.0,
}

class ParallelConsensusScraper:
    """
    محرك البحث التوافقي المتوازي الذي يستعلم محركات متعددة معاً
    ويقوم بتسجيل نقاط إجماع RRF Consensus وموثوقية النطاقات.
    """
    def __init__(self, google_key="", google_cx="", bing_key="", yandex_key=""):
        self.google_key = google_key
        self.google_cx = google_cx
        self.bing_key = bing_key
        self.yandex_key = yandex_key

    def _is_blacklisted(self, url):
        return bool(STOCKS_BLACKLIST_PAT.match(url))

    def _get_domain_authority(self, url):
        domain_match = re.search(r"https?://(?:www\.)?([^/]+)", url)
        if domain_match:
            domain = domain_match.group(1).lower()
            for pattern, weight in DOMAIN_AUTHORITY_MULTIPLIERS.items():
                if domain.endswith(pattern):
                    return weight
        return 1.0

    async def _fetch_google(self, session, query):
        if not self.google_key or not self.google_cx:
            return []
        url = "https://www.googleapis.com/customsearch/v1"
        params = {
            "key": self.google_key,
            "cx": self.google_cx,
            "q": query,
            "searchType": "image",
            "imgSize": "xxlarge",
            "fileType": "jpg|png",
            "gl": "ae"  # توطين نتائج البحث في دولة الإمارات (UAE Localization) لضمان جلب المنتجات المحلية
        }
        try:
            async with session.get(url, params=params, timeout=6) as r:
                if r.status == 200:
                    data = await r.json()
                    return [{
                        "url": item["link"],
                        "title": item.get("title", query),
                        "width": int(item.get("image", {}).get("width", 800)),
                        "height": int(item.get("image", {}).get("height", 800))
                    } for item in data.get("items", [])]
        except Exception:
            pass
        return []

    async def _fetch_bing(self, session, query):
        if self.bing_key:
            url = "https://api.bing.microsoft.com/v7.0/images/search"
            headers = {"Ocp-Apim-Subscription-Key": self.bing_key}
            params = {"q": query, "count": 15}
            try:
                async with session.get(url, headers=headers, params=params, timeout=6) as r:
                    if r.status == 200:
                        data = await r.json()
                        return [{
                            "url": item["contentUrl"],
                            "title": item.get("name", query),
                            "width": int(item.get("width", 800)),
                            "height": int(item.get("height", 800))
                        } for item in data.get("value", [])]
            except Exception:
                pass

        # fallback: Scrape Bing Images directly using curl_cffi Chrome Impersonation
        try:
            from curl_cffi.requests import AsyncSession
            import json
            encoded_query = urllib.parse.quote_plus(query)
            url = f"https://www.bing.com/images/search?q={encoded_query}&cc=AE"  # توطين نتائج محرك بحث بينج في الإمارات (cc=AE)
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.5"
            }
            proxies = {"http": config.PROXY_URL, "https": config.PROXY_URL} if getattr(config, "PROXY_URL", "") else None
            async with AsyncSession(impersonate="chrome120", proxies=proxies) as s:
                response = await s.get(url, headers=headers, timeout=8)
                if response.status_code == 200:
                    soup = BeautifulSoup(response.text, "html.parser")
                    links = soup.find_all("a", class_="iusc")
                    results = []
                    for link in links:
                        m_attr = link.get("m")
                        if m_attr:
                            try:
                                m_data = json.loads(m_attr)
                                results.append({
                                    "url": m_data["murl"],
                                    "title": m_data.get("desc", query),
                                    "width": int(m_data.get("w", 800)),
                                    "height": int(m_data.get("h", 800))
                                })
                            except Exception:
                                continue
                    return results[:15]
        except Exception as e:
            print(f"⚠️ [Bing Scrape Info] Cannot fallback to scraping: {e}")
        return []

    async def _fetch_yandex(self, query):
        try:
            from curl_cffi.requests import AsyncSession
            encoded_query = urllib.parse.quote_plus(query)
            url = f"https://yandex.com/images/search?text={encoded_query}"
            headers = {
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.5",
                "Host": "yandex.com",
                "Sec-Fetch-User": "?1",
            }
            proxies = {"http": config.PROXY_URL, "https": config.PROXY_URL} if getattr(config, "PROXY_URL", "") else None
            async with AsyncSession(impersonate="chrome120", proxies=proxies) as session:
                response = await session.get(url, headers=headers, timeout=8)
                if response.status_code != 200:
                    return []
                soup = BeautifulSoup(response.text, "html.parser")
                image_urls = []
                items = soup.find_all("div", class_=re.compile(r"serp-item"))
                for item in items:
                    if len(image_urls) >= 15:
                        break
                    data_bem = item.get("data-bem")
                    if not data_bem:
                        continue
                    try:
                        bem_json = json.loads(data_bem)
                        serp_data = bem_json.get("serp-item", {})
                        preview_list = serp_data.get("preview", [])
                        if preview_list:
                            origin_url = preview_list[0].get("origin", {}).get("url")
                            origin_w = preview_list[0].get("origin", {}).get("w", 800)
                            origin_h = preview_list[0].get("origin", {}).get("h", 800)
                            if origin_url:
                                image_urls.append({
                                    "url": origin_url,
                                    "title": query,
                                    "width": int(origin_w),
                                    "height": int(origin_h)
                                })
                    except Exception:
                        continue
                return image_urls
        except Exception as e:
            print(f"⚠️ [Yandex Scrape Info] Cannot fallback to scraping: {e}")
        return []

    async def _fetch_duckduckgo(self, query):
        try:
            from curl_cffi.requests import AsyncSession
            proxies = {"http": config.PROXY_URL, "https": config.PROXY_URL} if getattr(config, "PROXY_URL", "") else None
            async with AsyncSession(impersonate="chrome120", proxies=proxies) as session:
                encoded_query = urllib.parse.quote_plus(query)
                url_init = f"https://duckduckgo.com/?q={encoded_query}&iax=images&ia=images"
                headers_init = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                }
                response = await session.get(url_init, headers=headers_init, timeout=6)
                if response.status_code != 200:
                    return []
                match = re.search(r'vqd=["\']([0-9-]+)["\']', response.text)
                if not match:
                    match = re.search(r'vqd\s*=\s*([0-9]+)', response.text)
                if not match:
                    return []
                vqd = match.group(1)
                
                api_url = "https://duckduckgo.com/i.js"
                params = {
                    "l": "us-en",
                    "o": "json",
                    "q": query,
                    "vqd": vqd,
                    "f": ",,,",
                    "p": "1"
                }
                response2 = await session.get(api_url, params=params, headers={"X-Requested-With": "XMLHttpRequest"}, timeout=6)
                if response2.status_code == 200:
                    payload = response2.json()
                    results = payload.get("results", [])
                    return [{
                        "url": item["image"],
                        "title": item.get("title", query),
                        "width": int(item.get("width", 800)),
                        "height": int(item.get("height", 800))
                    } for item in results[:15]]
        except Exception as e:
            print(f"⚠️ [DuckDuckGo Scrape Info] Cannot fallback to scraping: {e}")
        return []

    async def aggregate_consensus_rankings(self, query):
        async with aiohttp.ClientSession() as session:
            tasks = [
                self._fetch_google(session, query),
                self._fetch_bing(session, query),
                self._fetch_yandex(query),
                self._fetch_duckduckgo(query)
            ]
            results = await asyncio.gather(*tasks)
            
        engine_outputs = {
            "google": results[0],
            "bing": results[1],
            "yandex": results[2],
            "duckduckgo": results[3]
        }
        
        engine_weights = {
            "google": 1.3,
            "bing": 1.1,
            "yandex": 0.9,
            "duckduckgo": 0.7
        }
        
        rrf_registry = {}
        k = 60
        
        for engine_name, candidates in engine_outputs.items():
            weight = engine_weights[engine_name]
            for idx, cand in enumerate(candidates):
                url = cand["url"]
                if self._is_blacklisted(url):
                    continue
                
                rank = idx + 1
                reciprocal_rank = weight / (k + rank)
                
                if url not in rrf_registry:
                    rrf_registry[url] = {
                        "cand": cand,
                        "score": reciprocal_rank
                    }
                else:
                    rrf_registry[url]["score"] += reciprocal_rank

        final_candidates = []
        for url, entry in rrf_registry.items():
            da_multiplier = self._get_domain_authority(url)
            if da_multiplier == 0.0:
                continue
                
            cand = entry["cand"]
            final_candidates.append({
                "url": url,
                "width": cand["width"],
                "height": cand["height"],
                "title": cand["title"],
                "rrf_score": entry["score"],
                "final_score": entry["score"] * da_multiplier
            })

        final_candidates.sort(key=lambda x: x["final_score"], reverse=True)
        return final_candidates

print = config.log_runner

# متغيرات جلوبال لتحميل نموذج CLIP مرة واحدة فقط وتوفير الذاكرة والوقت
_clip_model = None
_clip_processor = None

MAX_FILE_SIZE = 4 * 1024 * 1024      # 4 ميجابايت كحد أقصى للصور
MIN_FILE_SIZE = 10 * 1024            # 10 كيلوبايت كحد أدنى
SUPPORTED_MIME_TYPES = (
    "image/jpeg",
    "image/jpg",
    "image/png",
    "image/webp"
)

SPOOFED_BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive"
}

async def stream_and_validate_target(session, image_url, connection_timeout_seconds=8):
    """
    تستقبل جلسة العمل النشطة وتنفذ عملية تحقق متدفقة من ترويسات الاستجابة
    في طلب اتصال مفرد لتفادي عمليات المصافحة المتكررة غير الضرورية للشبكة.
    """
    client_timeout = aiohttp.ClientTimeout(total=connection_timeout_seconds)
    try:
        async with session.get(
            image_url,
            headers=SPOOFED_BROWSER_HEADERS,
            timeout=client_timeout,
            allow_redirects=True
        ) as http_response:
            if http_response.status != 200:
                return image_url, None, f"REJECTED_STATUS_{http_response.status}"
            
            response_headers = http_response.headers
            detected_content_type = response_headers.get("Content-Type", "").lower().strip()
            detected_content_length = response_headers.get("Content-Length")
            
            # التحقق الفوري من نوع الملف
            if not any(mime in detected_content_type for mime in SUPPORTED_MIME_TYPES):
                return image_url, None, f"UNSUPPORTED_MIME_TYPE_{detected_content_type}"
            
            # فحص حجم البيانات الأولي
            if detected_content_length is not None:
                try:
                    file_size_bytes = int(detected_content_length)
                    if file_size_bytes > MAX_FILE_SIZE:
                        return image_url, None, f"FILE_SIZE_EXCEEDS_LIMIT_{file_size_bytes}"
                    if file_size_bytes < MIN_FILE_SIZE:
                        return image_url, None, f"FILE_SIZE_BELOW_MINIMUM_{file_size_bytes}"
                except ValueError:
                    pass
            
            # القراءة التدريجية المتدفقة لضمان تصفية الأحجام الزائدة ديناميكياً
            downloaded_bytes_accumulator = bytearray()
            accumulated_size_counter = 0
            
            async for data_chunk in http_response.content.iter_chunked(65536):
                accumulated_size_counter += len(data_chunk)
                if accumulated_size_counter > MAX_FILE_SIZE:
                    return image_url, None, "DYNAMIC_STREAM_ABORT_OVERSIZE"
                downloaded_bytes_accumulator.extend(data_chunk)
            
            final_payload_size = len(downloaded_bytes_accumulator)
            if final_payload_size < MIN_FILE_SIZE:
                return image_url, None, f"FINAL_DOWNLOAD_SIZE_TOO_SMALL_{final_payload_size}"
                
            return image_url, bytes(downloaded_bytes_accumulator), "VERIFICATION_SUCCESS"
    except asyncio.TimeoutError:
        return image_url, None, "TIMEOUT_EXPIRED"
    except Exception as e:
        return image_url, None, f"PIPELINE_ERROR_{str(e)}"

async def execute_batch_processing(url_collection):
    """
    تدير عمليات التنفيذ المتوازي باستعمال موصلات مقابس محسنة
    تضمن تعظيم معدلات تدفق تجميع البيانات دون التسبب في إحباط اتصالات الشبكة.
    """
    optimized_network_connector = aiohttp.TCPConnector(
        limit=150,
        limit_per_host=10,
        ttl_dns_cache=300
    )
    execution_results = {}
    async with aiohttp.ClientSession(connector=optimized_network_connector) as http_client_session:
        active_pipeline_tasks = [
            asyncio.create_task(stream_and_validate_target(http_client_session, url)) 
            for url in url_collection
        ]
        batch_responses = await asyncio.gather(*active_pipeline_tasks, return_exceptions=False)
        for url, binary_data, processing_status in batch_responses:
            if binary_data is not None:
                try:
                    # التحقق من سلامة البنية الداخلية للصورة بالذاكرة
                    memory_stream = io.BytesIO(binary_data)
                    with Image.open(memory_stream) as validated_pil_image:
                        validated_pil_image.verify()
                    execution_results[url] = (binary_data, "VERIFICATION_SUCCESS")
                except Exception as file_corruption_err:
                    execution_results[url] = (None, f"IMAGE_CORRUPT_OR_UNREADABLE_{str(file_corruption_err)}")
            else:
                execution_results[url] = (None, processing_status)
    return execution_results

def extract_sizes(text):
    """
    استخراج الأحجام والأوزان والوحدات من النص وتوحيدها للمقارنة الرقمية.
    مثال: "Milk 1L" -> {'sizes': [(1000.0, 'ml')], 'packs': []}
    """
    if not text:
        return {"sizes": [], "packs": []}
    text = text.lower()
    
    # 1. استخراج الأبعاد والأحجام المعتادة (مل، لتر، جرام، كجم)
    # يدعم الصيغ مثل: 180ml, 1l, 500g, 1kg, 1.5 l, 1.5litre, 200 gm, 1.5ltr
    pattern = r'\b(\d+(?:\.\d+)?)\s*(ml|l|g|gm|kg|ltr|litre|grams|kilograms)\b'
    matches = re.findall(pattern, text)
    
    # 2. استخراج العبوات المتعددة (Packs) مثل: x6, pack of 6, 6pcs, 6s
    pack_pattern = r'\b(?:pack\s+of\s+|x\s*)(\d+)\b|\b(\d+)\s*(?:pcs|s|pack|packs)\b'
    pack_matches = re.findall(pack_pattern, text)
    
    sizes = []
    for val, unit in matches:
        try:
            val = float(val)
            # توحيد الوحدات السائلة إلى ml
            if unit in ['ml']:
                unit = 'ml'
            elif unit in ['l', 'ltr', 'litre']:
                val *= 1000.0
                unit = 'ml'
            # توحيد الوحدات الجافة إلى g
            elif unit in ['g', 'gm', 'grams']:
                unit = 'g'
            elif unit in ['kg', 'kilograms']:
                val *= 1000.0
                unit = 'g'
            sizes.append((val, unit))
        except ValueError:
            continue
            
    packs = []
    for m1, m2 in pack_matches:
        p_val = m1 or m2
        if p_val:
            try:
                packs.append(int(p_val))
            except ValueError:
                continue
                
    return {"sizes": sizes, "packs": packs}

def check_volume_clash(target_name, candidate_title, candidate_url=""):
    """
    التحقق مما إذا كان هناك تعارض صريح في الحجم أو الوزن بين المنتج المطلوب والصورة المرشحة.
    يرجع (True, reason) في حال وجود تعارض، و(False, "") إذا كانت متوافقة أو غير محددة.
    """
    target_data = extract_sizes(target_name)
    candidate_text = (candidate_title or "") + " " + (candidate_url or "")
    candidate_data = extract_sizes(candidate_text)
    
    # إذا لم يتم تحديد الحجم في الاسم المستهدف، فلا يوجد تعارض مؤكد
    if not target_data["sizes"]:
        return False, ""
        
    # مقارنة الأحجام المكتشفة
    for t_val, t_unit in target_data["sizes"]:
        for c_val, c_unit in candidate_data["sizes"]:
            if t_unit == c_unit:
                # إذا اختلفت القيم الرقمية بنسبة تزيد عن 20%
                ratio = max(t_val, c_val) / min(t_val, c_val) if min(t_val, c_val) > 0 else 1.0
                if ratio > 1.2:
                    unit_name = "مل" if t_unit == "ml" else "جرام"
                    t_display = f"{t_val/1000:.1f} لتر" if (t_unit == "ml" and t_val >= 1000) else f"{t_val:.0f} {unit_name}"
                    c_display = f"{c_val/1000:.1f} لتر" if (c_unit == "ml" and c_val >= 1000) else f"{c_val:.0f} {unit_name}"
                    return True, f"تعارض في الحجم/الوزن: المطلوب ({t_display}) والموجود بالصورة ({c_display})"
                    
    # مقارنة العبوات المتعددة (Packs) إذا كانت محددة بوضوح في الطرفين
    if target_data["packs"] and candidate_data["packs"]:
        t_pack = target_data["packs"][0]
        c_pack = candidate_data["packs"][0]
        if t_pack != c_pack:
            return True, f"تعارض في عدد العبوات: المطلوب ({t_pack} حبة) والموجود بالصورة ({c_pack} حبة)"
            
    return False, ""


def get_clip_model():
    """
    تحميل نموذج CLIP محلياً وبشكل كسول (Lazy Loading) عند أول استدعاء فقط.
    """
    global _clip_model, _clip_processor
    if getattr(config, "DISABLE_LOCAL_AI_MODELS", False):
        return None, None
    if _clip_model is None:
        local_dir = "clip_model"
        # نتحقق أن ملف الأوزان pytorch_model.bin موجود ومكتمل
        if os.path.exists(os.path.join(local_dir, "pytorch_model.bin")):
            try:
                print("⏳ جاري تحميل نموذج الذكاء الاصطناعي CLIP محلياً للفحص البصري...")
                from transformers import CLIPProcessor, CLIPModel
                import torch
                _clip_model = CLIPModel.from_pretrained(local_dir).to('cpu')
                _clip_processor = CLIPProcessor.from_pretrained(local_dir)
                print("✅ تم تحميل نموذج CLIP بنجاح!")
            except Exception as e:
                print(f"⚠️ فشل تحميل نموذج CLIP محلياً: {e}")
        else:
            print("ℹ️ نموذج CLIP المحلي غير متوفر أو لم يكتمل تنزيله بعد. سيتم تخطي الفحص الذكي.")
    return _clip_model, _clip_processor

_siglip_model = None
_siglip_processor = None
_blip_model = None
_blip_processor = None

def get_siglip_model():
    """
    تحميل نموذج SigLIP محلياً وبشكل كسول (Lazy Loading).
    """
    global _siglip_model, _siglip_processor
    if getattr(config, "DISABLE_LOCAL_AI_MODELS", False):
        return None, None
    if _siglip_model is None:
        try:
            print("⏳ جاري تحميل نموذج SigLIP للتحقق الدقيق من العلامة التجارية محلياً...")
            from transformers import AutoProcessor, AutoModel
            import torch
            model_id = config.SIGLIP_MODEL_ID
            _siglip_model = AutoModel.from_pretrained(model_id).to('cpu')
            _siglip_processor = AutoProcessor.from_pretrained(model_id)
            print("✅ تم تحميل نموذج SigLIP بنجاح!")
        except Exception as e:
            print(f"⚠️ فشل تحميل نموذج SigLIP: {e}")
    return _siglip_model, _siglip_processor

def get_blip_model():
    """
    تحميل نموذج BLIP التوليدي محلياً وبشكل كسول (Lazy Loading).
    """
    global _blip_model, _blip_processor
    if getattr(config, "DISABLE_LOCAL_AI_MODELS", False):
        return None, None
    if _blip_model is None:
        try:
            print("⏳ جاري تحميل نموذج BLIP لإنشاء تسميات وأوصاف الصور محلياً...")
            from transformers import BlipProcessor, BlipForConditionalGeneration
            import torch
            model_id = config.BLIP_MODEL_ID
            _blip_model = BlipForConditionalGeneration.from_pretrained(model_id).to('cpu')
            _blip_processor = BlipProcessor.from_pretrained(model_id)
            print("✅ تم تحميل نموذج BLIP بنجاح!")
        except Exception as e:
            print(f"⚠️ فشل تحميل نموذج BLIP: {e}")
    return _blip_model, _blip_processor

def check_image_relevance_via_siglip(pil_image, brand, product_name):
    """
    مقارنة الصورة مباشرة مع اسم المنتج النصي للتأكد من التطابق الدلالي بدقة عالية باستخدام SigLIP.
    """
    model, processor = get_siglip_model()
    if model is None or processor is None:
        return 1.0, None
        
    try:
        import torch
        prompts = [
            f"a product packaging of {brand} {product_name}",
            f"a photo of {brand} {product_name} packaging box, bottle, or bag",
            f"packaging label of {brand} {product_name}"
        ]
        
        inputs = processor(text=prompts, images=pil_image, return_tensors="pt", padding=True)
        with torch.no_grad():
            outputs = model(**inputs)
            
        logits_per_image = outputs.logits_per_image
        probs = logits_per_image.sigmoid()
        
        mean_score = torch.mean(probs).item()
        print(f"🤖 تحليل SigLIP لصلة الصورة بـ '{brand} {product_name}': {mean_score:.4f}")
        return mean_score, None
    except Exception as e:
        print(f"⚠️ خطأ أثناء فحص الصورة بـ SigLIP: {e}")
        return 1.0, None

def generate_image_caption_via_blip(pil_image):
    """
    توليد وصف نصي حر لمكونات الصورة باستخدام نموذج BLIP.
    """
    model, processor = get_blip_model()
    if model is None or processor is None:
        return ""
        
    try:
        import torch
        inputs = processor(images=pil_image, return_tensors="pt")
        with torch.no_grad():
            out = model.generate(**inputs)
        caption = processor.decode(out[0], skip_special_tokens=True)
        print(f"🤖 [BLIP Image Captioning] الوصف المولد: '{caption}'")
        return caption
    except Exception as e:
        print(f"⚠️ خطأ أثناء توليد وصف الصورة بـ BLIP: {e}")
        return ""

def verify_brand_alignment_via_caption(caption, brand, product_name):
    """
    التحقق من تطابق العلامة التجارية والمنتج دلالياً بناءً على الوصف المولد من BLIP لمنع الخلط بين الماركات.
    """
    if not caption or not brand:
        return True
        
    caption_lower = caption.lower()
    brand_lower = brand.lower()
    
    known_competitors = ["lays", "doritos", "pringles", "zwan", "ajmi", "nestle", "al ain", "sadia", "almarai", "nivea", "dove"]
    for comp in known_competitors:
        if comp in caption_lower and comp != brand_lower and brand_lower in known_competitors:
            print(f"❌ [BLIP Brand Conflict Detected] تعارض براند: الصورة لـ '{comp}' والمنتج المطلوب لـ '{brand}'")
            return False
            
    return True

_moondream_model = None
_moondream_tokenizer = None

def get_moondream_model():
    """
    تحميل نموذج Moondream2 محلياً وبشكل كسول (Lazy Loading).
    """
    global _moondream_model, _moondream_tokenizer
    if getattr(config, "DISABLE_LOCAL_AI_MODELS", False):
        return None, None
    if _moondream_model is None:
        try:
            print("⏳ جاري تحميل نموذج Moondream2 للفرز القاطع النهائي محلياً...")
            from transformers import AutoModelForCausalLM, AutoTokenizer
            model_id = config.MOONDREAM_MODEL_ID
            _moondream_model = AutoModelForCausalLM.from_pretrained(
                model_id, 
                trust_remote_code=True,
                revision="2024-08-02"
            ).to('cpu')
            _moondream_tokenizer = AutoTokenizer.from_pretrained(model_id)
            print("✅ تم تحميل نموذج Moondream2 بنجاح!")
        except Exception as e:
            print(f"⚠️ فشل تحميل نموذج Moondream2: {e}")
    return _moondream_model, _moondream_tokenizer

def verify_image_via_moondream(pil_image, brand, product_name):
    """
    التحقق الحتمي النهائي باستخدام Moondream2 للإجابة عن الأسئلة الدلالية الثلاثة.
    """
    model, tokenizer = get_moondream_model()
    if model is None or tokenizer is None:
        return True
        
    try:
        q1 = "Is this image a physical, packaged product box, bag, can or pouch? Answer yes or no."
        ans1 = model.answer_question(pil_image, q1, tokenizer).strip().lower()
        print(f"🤖 [Moondream2] Q: {q1} | A: {ans1}")
        if "no" in ans1 and "yes" not in ans1:
            print("❌ [Moondream2 Validation Failed] الصورة ليست لمنتج معبأ/مغلف مادي.")
            return False
            
        if brand:
            q2 = f"Is the brand name or logo of '{brand}' visible on the packaging label? Answer yes or no."
            ans2 = model.answer_question(pil_image, q2, tokenizer).strip().lower()
            print(f"🤖 [Moondream2] Q: {q2} | A: {ans2}")
            if "no" in ans2 and "yes" not in ans2:
                print(f"❌ [Moondream2 Validation Failed] اسم العلامة التجارية '{brand}' غير ظاهر على الكانفاس.")
                return False
                
        return True
    except Exception as e:
        print(f"⚠️ خطأ أثناء التحقق بـ Moondream2: {e}")
        return True

def download_temp_image(url):
    """
    تحميل الصورة مؤقتاً إلى ملف محلي لفحصها بـ CLIP.
    """
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        r = requests.get(url, headers=headers, timeout=5, stream=True)
        if r.status_code == 200:
            fd, temp_path = tempfile.mkstemp(suffix=".jpg")
            with os.fdopen(fd, 'wb') as f:
                for chunk in r.iter_content(chunk_size=128*1024):
                    f.write(chunk)
            return temp_path
    except Exception:
        pass
    return None

def is_image_real_product(image_path):
    """
    استخدام نموذج CLIP للتأكد من أن الصورة هي لمنتج تجاري حقيقي (علبة/زجاجة/كيس)
    وليست رسماً كرتونياً أو فكتورياً أو ملصقاً توضيحياً.
    """
    model, processor = get_clip_model()
    if model is None or processor is None:
        return True # كخيار احتياطي إذا لم يكن النموذج متوفراً
        
    try:
        from PIL import Image
        import torch
        
        image = Image.open(image_path).convert("RGB")
        
        # نصوص الوصف للمقارنة
        texts = [
            "a real photo of a physical commercial product packaging carton, bottle, can or bag",
            "a cartoon drawing, vector illustration, clipart graphics, cute character sticker"
        ]
        
        inputs = processor(text=texts, images=image, return_tensors="pt", padding=True)
        with torch.no_grad():
            outputs = model(**inputs)
            
        logits_per_image = outputs.logits_per_image
        probs = logits_per_image.softmax(dim=-1)
        
        real_prob = probs[0][0].item()
        cartoon_prob = probs[0][1].item()
        
        print(f"🤖 تحليل الذكاء الاصطناعي CLIP للصورة:")
        print(f"   - منتج حقيقي معبأ: {real_prob:.2%}")
        print(f"   - رسمة كرتونية/ملصق: {cartoon_prob:.2%}")
        
        # إذا كانت احتمالية الكرتون أكبر، نرفض الصورة
        if cartoon_prob > real_prob:
            return False
            
        return True
    except Exception as e:
        print(f"⚠️ خطأ أثناء فحص الصورة بـ CLIP: {e}")
        return True

def check_image_relevance_via_clip(image_path, brand, product_name):
    """
    مقارنة الصورة مباشرة مع اسم المنتج النصي للتأكد من التطابق الدلالي بدقة عالية باستخدام تجميع الاستعلامات (Prompt Ensembling).
    """
    model, processor = get_clip_model()
    if model is None or processor is None:
        return 1.0, None  # كخيار احتياطي إذا لم يكن النموذج متوفراً
        
    try:
        from PIL import Image
        import torch
        
        image = Image.open(image_path).convert("RGB")
        
        # تجميع استعلامات نصية متعددة (Prompt Ensembling)
        prompts = [
            f"a product packaging of {brand} {product_name}",
            f"a photo of {brand} {product_name} packaging box, bottle, or bag",
            f"packaged {brand} {product_name} commercial product item",
            f"a retail packaged product photo of {brand} {product_name}"
        ]
        
        inputs = processor(text=prompts, images=image, return_tensors="pt", padding=True)
        with torch.no_grad():
            outputs = model(**inputs)
            
        # استخراج وتطبيع متجهات الخصائص
        image_features = outputs.image_embeds
        text_features = outputs.text_embeds
        
        image_features = image_features / image_features.norm(dim=-1, keepdim=True)
        text_features = text_features / text_features.norm(dim=-1, keepdim=True)
        
        # حساب التشابه الجيبي لكل جملة وصفية ثم أخذ المتوسط
        similarities = (image_features @ text_features.T).squeeze(0)
        avg_similarity = similarities.mean().item()
        
        # تحويل المتجه لقائمة بايثون عادية (512 float)
        image_embedding = image_features.squeeze(0).tolist()
        
        print(f"🤖 [CLIP Ensemble Check] درجة التطابق المتوسطة مع عبوات '{brand} {product_name}': {avg_similarity:.4f}")
        return avg_similarity, image_embedding
    except Exception as e:
        print(f"⚠️ خطأ أثناء حساب تطابق النص المجمع بـ CLIP: {e}")
        return 1.0, None

def find_semantic_cache_match(product_name, brand, threshold=0.92):
    """
    مقارنة دلالية للمنتج الحالي مع أسماء المنتجات المخزنة في SQLite باستخدام المتجهات النصية لـ CLIP للتخطي الفوري للبحث.
    """
    if getattr(config, "DISABLE_LOCAL_AI_MODELS", False):
        return None
    try:
        import local_cache_db
        import torch
        import json
        # 1. جلب كافة المنتجات المسجلة بكود الكاش
        conn = local_cache_db.get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT product_name, brand, cloudinary_url, clip_score, metadata_json FROM resolved_products")
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
        
        if not rows:
            return None
            
        # 2. حساب متجه الاستعلام النصي باستخدام CLIP
        model, processor = get_clip_model()
        if model is None or processor is None:
            return None
            
        query_prompt = f"a product packaging of {brand} {product_name}"
        cached_prompts = [f"a product packaging of {row['brand']} {row['product_name']}" for row in rows]
        
        # ترميز الاستعلام وكافة المنتجات المخزنة دفعة واحدة في مصفوفة مدمجة
        all_texts = [query_prompt] + cached_prompts
        inputs = processor(text=all_texts, return_tensors="pt", padding=True)
        with torch.no_grad():
            text_features = model.get_text_features(**inputs)
            
        # تطبيع المتجهات النصية رياضياً
        text_features = text_features / text_features.norm(dim=-1, keepdim=True)
        query_vector = text_features[0]
        cached_vectors = text_features[1:]
        
        # 3. حساب تشابه جيب التمام مع كافة المتجهات النصية للمنتجات
        best_match = None
        max_similarity = -1.0
        
        for idx, row in enumerate(rows):
            # 1. التحقق من تطابق البراند بنسبة 100% لتفادي خلط المنتجات
            row_brand = row["brand"] or ""
            query_brand = brand or ""
            if row_brand.lower().strip() != query_brand.lower().strip():
                continue
                
            # 2. التحقق من توافق الكلمات المفتاحية الأساسية للمنتج لمنع الخلط بين النكهات/الأنواع
            n1 = product_name.lower()
            n2 = (row["product_name"] or "").lower()
            
            # منع الخلط بين الكلمات الدلالية المتعارضة
            conflicting_keywords = [
                "chocolate", "strawberry", "vanilla", "laban", "milk", "water", "juice", 
                "atta", "flour", "oil", "rice", "sugar", "salt", "tea", "coffee", "yogurt"
            ]
            conflict_detected = False
            for kw in conflicting_keywords:
                if (kw in n1 and kw not in n2) or (kw in n2 and kw not in n1):
                    conflict_detected = True
                    break
            if conflict_detected:
                continue
                
            emb = cached_vectors[idx]
            similarity = torch.dot(query_vector, emb).item()
            
            if similarity > max_similarity:
                max_similarity = similarity
                best_match = row
                
        if max_similarity >= threshold and best_match:
            print(f"⚡ [CLIP Vector Cache Hit] تم العثور على تطابق دلالي ذكي بنسبة {max_similarity:.2%} مع منتج الكاش: '{best_match['brand']} {best_match['product_name']}'")
            metadata = {}
            if best_match["metadata_json"]:
                try:
                    metadata = json.loads(best_match["metadata_json"])
                except Exception:
                    pass
            return {
                "url": best_match["cloudinary_url"],
                "clip_score": best_match["clip_score"],
                "metadata": metadata,
                "semantic_similarity": max_similarity
            }
            
    except Exception as e:
        print(f"⚠️ [CLIP Vector Cache Error] فشل الاستعلام الدلالي عن الكاش: {e}")
        
    return None




def validate_image_via_gemini_vision(image_path, product_name, brand):
    """
    استخدام Gemini Vision للتحقق البصري السريع والمؤكد من تطابق الصورة مع البراند والمنتج المطلوبين.
    """
    if not config.GEMINI_API_KEY or not config.ENABLE_GEMINI_PRE_VALIDATION:
        return True
        
    try:
        from PIL import Image
        import base64
        import io
        
        with Image.open(image_path) as img:
            if img.mode != "RGB":
                img = img.convert("RGB")
            img.thumbnail((300, 300))  # تصغير الحجم جداً لتوفير الباندويث والتكلفة
            buffer = io.BytesIO()
            img.save(buffer, format="JPEG", quality=70)
            img_data = base64.b64encode(buffer.getvalue()).decode("utf-8")
            
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{config.GEMINI_MODEL}:generateContent?key={config.GEMINI_API_KEY}"
        
        # Check if the brand has active learning feedback for background clutter
        import local_cache_db
        clutter_flag = local_cache_db.get_active_learning_clutter_flag(brand)
        
        prompt = (
            f"You are a catalog validation assistant.\n"
            f"Analyze the image and verify if it shows a commercial packaged product from the brand '{brand}' representing '{product_name}'.\n"
            f"Perform strict attribute validation checks:\n"
            f"1. Brand check: Must match '{brand}' or its verified synonyms/subsidiaries. Reject if it is a competitor brand (e.g., Almarai instead of Meliha, Mai Dubai instead of Masafi, etc.).\n"
            f"2. Flavor/Type check: If the target '{product_name}' mentions a specific flavor or type (e.g., 'Chocolate', 'Strawberry', 'Full Cream', 'Low Fat'), the package in the image MUST match this flavor/type. If the image shows a mismatch (e.g., target is Chocolate, image is Strawberry), set 'valid' to false.\n"
            f"3. Size/Volume check: Check if the product size/volume matches the target name. If the target is a single small pack (e.g., '180ml') and the image shows a large 1L bottle or a bulk box, reject it. If size is not clearly readable or is close enough, you can accept it but explain in reason.\n"
        )
        
        if clutter_flag:
            prompt += (
                f"4. Background/Clutter check: This brand '{brand}' has had issues with background clutter. "
                f"You MUST strictly reject the image (set 'valid' to false) if the background is cluttered, messy, "
                f"or not a clean product photo. Only accept clean, professional product images.\n"
            )
            print(f"💡 [Active Learning] تفعيل فحص تداخل الخلفية الصارم لـ Gemini Vision للبراند '{brand}' بسبب تكرار الرفض.")
            
        prompt += (
            f"Reply strictly in JSON format matching this schema:\n"
            f'{{\n'
            f'  "valid": true or false,\n'
            f'  "reason": "brief explanation in English explaining any mismatch or confirmation"\n'
            f'}}'
        )
        
        payload = {
            "contents": [
                {
                    "parts": [
                        {"text": prompt},
                        {
                            "inlineData": {
                                "mimeType": "image/jpeg",
                                "data": img_data
                            }
                        }
                    ]
                }
            ],
            "generationConfig": {
                "responseMimeType": "application/json"
            }
        }
        
        headers = {"Content-Type": "application/json"}
        print(f"🤖 [Gemini Pre-Validation] جاري التحقق البصري الدقيق من المنتج...")
        
        # تحديث عداد المكالمات
        if hasattr(config, "METRICS") and "gemini_api_calls" in config.METRICS:
            config.METRICS["gemini_api_calls"] += 1
            
        response = requests.post(url, headers=headers, json=payload, timeout=15)
        if response.status_code == 200:
            import json
            res_data = response.json()
            text_response = res_data['candidates'][0]['content']['parts'][0]['text'].strip()
            if text_response.startswith("```json"):
                text_response = text_response[7:]
            elif text_response.startswith("```"):
                text_response = text_response[3:]
            if text_response.endswith("```"):
                text_response = text_response[:-3]
            text_response = text_response.strip()
            
            result = json.loads(text_response)
            is_valid = result.get("valid", False)
            reason = result.get("reason", "")
            print(f"🤖 [Gemini Pre-Validation] النتيجة: {'مقبول ✅' if is_valid else 'مرفوض ❌'} | السبب: {reason}")
            return is_valid
    except Exception as e:
        print(f"⚠️ خطأ أثناء التحقق المسبق بـ Gemini Vision: {e}")
        
    return True  # إذا فشل الاتصال بالـ API نقبله كاحتياط

def yandex_image_search(query):
    """
    البحث عن صور باستخدام محرك بحث Yandex مع محاكاة كاملة لمتصفح Chrome بـ curl_cffi لتجنب الـ CAPTCHA.
    """
    try:
        from curl_cffi import requests as c_requests
    except ImportError:
        c_requests = requests
        
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8'
    }
    url = f"https://yandex.com/images/search?text={urllib.parse.quote(query)}"
    
    try:
        if hasattr(c_requests, "get") and "impersonate" in c_requests.get.__code__.co_varnames:
            res = c_requests.get(url, headers=headers, impersonate="chrome", timeout=10)
        else:
            res = c_requests.get(url, headers=headers, timeout=10)
            
        if res.status_code != 200:
            print(f"⚠️ خطأ أثناء الاتصال بـ Yandex (كود الاستجابة {res.status_code})")
            return []
            
        if "captcha" in res.text.lower() or "showcaptcha" in res.text.lower():
            print("❌ واجه Yandex تحدي CAPTCHA للتأكد من الروبوتات.")
            return []
            
        import html
        decoded = html.unescape(res.text)
        matches = re.finditer(r'"origUrl"\s*:\s*"([^"]+)"', decoded)
        results = []
        
        for match in matches:
            url_str = match.group(1)
            start = max(0, match.start() - 300)
            end = min(len(decoded), match.end() + 300)
            chunk = decoded[start:end]
            
            w_match = re.search(r'"origWidth"\s*:\s*(\d+)', chunk)
            h_match = re.search(r'"origHeight"\s*:\s*(\d+)', chunk)
            t_match = re.search(r'"title"\s*:\s*"([^"]+)"', chunk)
            
            width = int(w_match.group(1)) if w_match else 800
            height = int(h_match.group(1)) if h_match else 800
            title = t_match.group(1) if t_match else query
            
            results.append({
                'url': url_str,
                'width': width,
                'height': height,
                'title': title
            })
            
        return results
    except Exception as e:
        print(f"⚠️ خطأ أثناء البحث في Yandex: {e}")
        return []

def bing_image_search(query):
    """
    البحث عن صور باستخدام محرك بحث Bing مع محاكاة كاملة لمتصفح Chrome بـ curl_cffi لتجنب الحظر.
    """
    try:
        from curl_cffi import requests as c_requests
    except ImportError:
        c_requests = requests
        
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8'
    }
    url = 'https://www.bing.com/images/search'
    params = {'q': query}
    
    try:
        if hasattr(c_requests, "get") and "impersonate" in c_requests.get.__code__.co_varnames:
            res = c_requests.get(url, headers=headers, params=params, impersonate="chrome", timeout=10)
        else:
            res = c_requests.get(url, headers=headers, params=params, timeout=10)
            
        if res.status_code != 200:
            print(f"⚠️ خطأ أثناء الاتصال بـ Bing (كود الاستجابة {res.status_code})")
            return []
            
        import html
        import json
        decoded = html.unescape(res.text)
        pattern = r'class="iusc"[^>]+?m="({[^}]+?})".*?exph=(\d+).*?expw=(\d+)'
        matches = re.finditer(pattern, decoded, re.DOTALL)
        
        results = []
        for match in matches:
            json_str = match.group(1)
            exph = match.group(2)
            expw = match.group(3)
            
            try:
                data = json.loads(json_str)
                murl = data.get("murl")
                if murl:
                    murl_decoded = urllib.parse.unquote(murl)
                    title = data.get("t") or data.get("desc") or query
                    results.append({
                        'url': murl_decoded,
                        'width': int(expw),
                        'height': int(exph),
                        'title': title
                    })
            except Exception:
                pass
        return results
    except Exception as e:
        print(f"⚠️ خطأ أثناء البحث في Bing: {e}")
        return []

def duckduckgo_image_search(query):
    """
    البحث عن صور باستخدام مكتبة duckduckgo_search المجانية.
    """
    try:
        from duckduckgo_search import DDGS
        results = []
        with DDGS() as ddgs:
            # استخدام تابع images المدمج مع عوامل تصفية قياسية للمنتجات
            ddgs_generator = ddgs.images(
                keywords=query,
                region="wt-wt",
                safesearch="off",
                size="Medium"
            )
            count = 0
            for item in ddgs_generator:
                results.append({
                    'url': item.get('image'),
                    'width': int(item.get('width', 800)),
                    'height': int(item.get('height', 800)),
                    'title': item.get('title', query)
                })
                count += 1
                if count >= 15:
                    break
        return results
    except Exception as e:
        print(f"⚠️ خطأ أثناء البحث في DuckDuckGo: {e}")
        return []

def google_image_search_free(query):
    """
    البحث عن صور في Google بدون واجهة برمجة تطبيقات مدفوعة وبشكل متخفٍ بالكامل.
    """
    try:
        from curl_cffi import requests as c_requests
    except ImportError:
        c_requests = requests
        
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Connection': 'keep-alive'
        }
        url = f"https://www.google.com/search?q={urllib.parse.quote(query)}&tbm=isch"
        
        if hasattr(c_requests, "get") and "impersonate" in c_requests.get.__code__.co_varnames:
            res = c_requests.get(url, headers=headers, impersonate="chrome", timeout=10)
        else:
            res = c_requests.get(url, headers=headers, timeout=10)
            
        if res.status_code != 200:
            return []
            
        # استخراج مصفوفات الصور من AF_initDataCallback
        import json
        pattern = r"AF_initDataCallback\s*\(\s*\{.*?key:\s*'ds:1'.*?data:\s*(.*?)\}\s*\)\s*;"
        match = re.search(pattern, res.text, re.DOTALL)
        results = []
        
        if match:
            try:
                data_str = match.group(1).strip()
                if data_str.endswith(","):
                    data_str = data_str[:-1]
                data = json.loads(data_str)
                
                # استخراج الروابط من المصفوفات المتداخلة برمجياً
                def extract_img_nodes(lst):
                    for item in lst:
                        if isinstance(item, list):
                            extract_img_nodes(item)
                        elif isinstance(item, str):
                            if item.startswith("http") and any(item.lower().endswith(ext) for ext in [".jpg", ".jpeg", ".png", ".webp"]):
                                results.append({
                                    'url': item,
                                    'width': 800,
                                    'height': 800,
                                    'title': query
                                })
                extract_img_nodes(data)
            except Exception:
                pass
                
        # خيار بديل سريع بالبحث المباشر عن روابط imgurl
        if not results:
            matches = re.findall(r'imgurl=(http[^&]+)', res.text)
            for m in matches:
                url_str = urllib.parse.unquote(m)
                results.append({
                    'url': url_str,
                    'width': 800,
                    'height': 800,
                    'title': query
                })
                
        return results[:15]
    except Exception as e:
        print(f"⚠️ خطأ أثناء البحث في Google (بدون مفتاح): {e}")
        return []

def google_image_search(query):
    """
    البحث عن صور باستخدام Google Custom Search API الرسمي، مع تراجع تلقائي للنسخة المجانية في حال غياب المفاتيح.
    """
    if not config.GOOGLE_SEARCH_API_KEY or not config.GOOGLE_SEARCH_CX:
        # التراجع التلقائي للنسخة المجانية
        return google_image_search_free(query)
        
    url = "https://www.googleapis.com/customsearch/v1"
    params = {
        'key': config.GOOGLE_SEARCH_API_KEY,
        'cx': config.GOOGLE_SEARCH_CX,
        'q': query,
        'searchType': 'image',
        'num': 10
    }
    
    try:
        res = requests.get(url, params=params, timeout=5)
        if res.status_code != 200:
            return google_image_search_free(query)
            
        data = res.json()
        results = []
        for item in data.get('items', []):
            img_info = item.get('image', {})
            results.append({
                'url': item.get('link'),
                'width': int(img_info.get('width', 0)),
                'height': int(img_info.get('height', 0)),
                'title': item.get('title', query)
            })
        return results
    except Exception:
        return google_image_search_free(query)

def is_image_accessible(url):
    """
    التحقق من أن رابط الصورة يمكن الوصول إليه وتنزيله ولا يمنع الـ hotlinking.
    """
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        r = requests.head(url, headers=headers, timeout=3, allow_redirects=True)
        if r.status_code >= 400:
            r = requests.get(url, headers=headers, timeout=3, stream=True)
            
        if r.status_code == 200:
            content_type = r.headers.get('Content-Type', '')
            if 'image' in content_type.lower():
                return True
    except Exception:
        pass
    return False

def clean_product_name_for_search(name):
    """
    تنظيف اسم المنتج من الأحجام والوحدات والصيغ الخاصة لزيادة فرص العثور عليه
    """
    # إزالة الأوزان والأحجام الشائعة
    cleaned = re.sub(r'\b\d+(?:\.\d+)?\s*(?:ml|l|g|kg|pcs|pack|ltr|gm|oz|ozs|milliliter|liter|gram|kilogram|مل|لتر|جرام|جم|كجم|حبة)\b', '', name, flags=re.IGNORECASE)
    # إزالة الأقواس الفارغة أو علامات الترقيم الزائدة
    cleaned = re.sub(r'\s*[\(\[].*?[\)\]]', '', cleaned)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned

def get_fallback_query(product_name):
    """
    توليد استعلام بحث بديل يركز على شكل المنتج التجاري (مثل علبة، كرتونة، كيس)
    بدلاً من المكونات الخام لمنع جلب صور طبيعية غير ملائمة للمتجر.
    """
    cleaned_name = clean_product_name_for_search(product_name)
    name_lower = cleaned_name.lower()
    
    if "milk" in name_lower:
        if "carton" in name_lower or any(x in product_name.lower() for x in ["180ml", "200ml", "250ml"]):
            return f"{cleaned_name} carton"
        return f"{cleaned_name} bottle"
    elif "flour" in name_lower:
        return f"{cleaned_name} bag"
    elif "laban" in name_lower:
        return f"{cleaned_name} bottle"
    elif "kefir" in name_lower or "yogurt" in name_lower:
        return f"{cleaned_name} bottle"
        
    return f"{cleaned_name} product"

def evaluate_and_choose_best_image(results, product_name, brand, requires_brand_match=False, trace=None, step_name="البحث الأساسي", query=None, brand_mappings=None):
    """
    تقييم وترتيب نتائج البحث بناءً على صلتها بالبراند والمنتج ثم الدقة.
    إرجاع الصورة المختارة ونقاط الصلة الخاصة بها.
    """
    top_results = results[:15]  # استخدام أفضل 15 نتيجة صلة من محرك البحث
    scored_results = []
    brand_lower = brand.lower().strip()
    product_keywords = [w.lower().strip() for w in product_name.split() if len(w.strip()) > 1]
    
    # الحصول على المرادفات الخاصة بالبراند المعني لضمان مطابقة صارمة ذكية ودقيقة
    synonyms = [brand_lower]
    excluded_competitors = []
    if brand_mappings:
        # البحث عن البراند المعني في جدول المرادفات
        for k, mapping in brand_mappings.items():
            if k == brand_lower or mapping.get("brand", "").lower().strip() == brand_lower:
                synonyms.extend([s.lower().strip() for s in mapping.get("synonyms", [])])
                if config.FILTER_COMPETITORS:
                    excluded_competitors.extend([c.lower().strip() for c in mapping.get("excluded_competitors", [])])
                
    # إزالة التكرارات والفارغ
    synonyms = list(set([s for s in synonyms if s]))
    excluded_competitors = list(set([c for c in excluded_competitors if c]))
    
    candidates = []
    
    # التحقق من الكلمات الدلالية أو النطاقات الكرتونية والرسومات واستبعادها لضمان جلب صور منتجات حقيقية
    excluded_keywords = [
        "cartoon", "clipart", "vector", "illustration", "cute", "character", "drawing", 
        "sketch", "animated", "pngtree", "nicepng", "vecteezy", "freepik", "cleanpng", 
        "clipartmax", "favpng", "shutterstock/vectors", "vectorstock", "sticker", "kawaii", 
        "doodle", "wallpaper", "clip-art", "toy", "art", "paint", "graphics"
    ]
    excluded_domains = [
        "vecteezy.com", "freepik.com", "pngtree.com", "nicepng.com", "pngitem.com", 
        "clipartmax.com", "cleanpng.com", "favpng.com", "vectorstock.com", 
        "pinterest.com", "i.pinimg.com", "pinimg.com"
    ]

    for item in top_results:
        url_lower = item['url'].lower()
        title_lower = item.get('title', '').lower()
        
        # default candidate details
        relevance_score = 0
        has_brand_match = False
        reasons = []
        is_uae_source = any(x in url_lower or x in title_lower for x in [".ae", "uae", "dubai", "abu dhabi", "sharjah", "ajman", "fujairah", "um al quwain", "ras al khaimah", "الإمارات", "دبي"])
        
        # 1. check dimensions
        if item['width'] < config.MIN_IMAGE_WIDTH or item['height'] < config.MIN_IMAGE_HEIGHT:
            reasons.append(f"الصورة صغيرة جداً ({item['width']}x{item['height']}) - الحد الأدنى {config.MIN_IMAGE_WIDTH}x{config.MIN_IMAGE_HEIGHT}")
            candidates.append({
                "url": item['url'],
                "title": item.get('title', ''),
                "status": "rejected",
                "width": item['width'],
                "height": item['height'],
                "scores": {"relevance_score": 0, "is_uae_source": is_uae_source},
                "reasons": reasons
            })
            continue
            
        # 2. check cartoon
        is_cartoon_or_illustration = False
        for kw in excluded_keywords:
            if kw in url_lower or kw in title_lower:
                is_cartoon_or_illustration = True
                reasons.append(f"تطابق كلمة مستبعدة (كرتون/رسم): '{kw}'")
                break
        if not is_cartoon_or_illustration:
            for dom in excluded_domains:
                if dom in url_lower:
                    is_cartoon_or_illustration = True
                    reasons.append(f"تطابق نطاق مستبعد: '{dom}'")
                    break
                    
        if is_cartoon_or_illustration:
            candidates.append({
                "url": item['url'],
                "title": item.get('title', ''),
                "status": "rejected",
                "width": item['width'],
                "height": item['height'],
                "scores": {"relevance_score": 0, "is_uae_source": is_uae_source},
                "reasons": reasons
            })
            continue
            
        # 2.5. check competitor brand match
        has_competitor_match = False
        if excluded_competitors:
            for competitor in excluded_competitors:
                if competitor and (competitor in url_lower or competitor in title_lower):
                    has_competitor_match = True
                    reasons.append(f"تطابق مع منافس مستبعد: '{competitor}'")
                    break
        if has_competitor_match:
            candidates.append({
                "url": item['url'],
                "title": item.get('title', ''),
                "status": "rejected",
                "width": item['width'],
                "height": item['height'],
                "scores": {"relevance_score": 0, "is_uae_source": is_uae_source},
                "reasons": reasons
            })
            continue
            
        # 2.7. check size/volume clash (Stage 2: Volume and Unit Validation)
        has_clash, clash_reason = check_volume_clash(product_name, item.get('title', ''), item['url'])
        if has_clash:
            reasons.append(clash_reason)
            candidates.append({
                "url": item['url'],
                "title": item.get('title', ''),
                "status": "rejected",
                "width": item['width'],
                "height": item['height'],
                "scores": {"relevance_score": 0, "is_uae_source": is_uae_source},
                "reasons": reasons
            })
            continue
            
        # 3. relevance score
        # A. brand check
        if synonyms:
            for synonym in synonyms:
                if synonym in url_lower:
                    relevance_score += 15
                    has_brand_match = True
                    break
                if synonym in title_lower:
                    relevance_score += 15
                    has_brand_match = True
                    break
                
        # B. product keywords check
        for keyword in product_keywords:
            if keyword in url_lower:
                relevance_score += 3
            if keyword in title_lower:
                relevance_score += 3
                
        # 4. brand match required check
        if requires_brand_match and brand_lower and not has_brand_match:
            reasons.append(f"عدم مطابقة البراند المطلوبة '{brand_lower}'")
            candidates.append({
                "url": item['url'],
                "title": item.get('title', ''),
                "status": "rejected",
                "width": item['width'],
                "height": item['height'],
                "scores": {"relevance_score": relevance_score, "is_uae_source": is_uae_source},
                "reasons": reasons
            })
            continue
            
        resolution = item['width'] * item['height']
        
        # Add placeholder to candidates
        candidates.append({
            "url": item['url'],
            "title": item.get('title', ''),
            "status": "rejected", # default to rejected
            "width": item['width'],
            "height": item['height'],
            "scores": {"relevance_score": relevance_score, "is_uae_source": is_uae_source},
            "reasons": reasons
        })
        
        scored_results.append({
            'item': item,
            'relevance_score': relevance_score,
            'resolution': resolution,
            'has_brand_match': has_brand_match,
            'candidate_index': len(candidates) - 1
        })
        
    if not scored_results:
        if trace is not None:
            trace.setdefault('steps', []).append({
                "name": step_name,
                "query": query or "",
                "results_count": len(results),
                "candidates": candidates
            })
        return None, 0
        
    # الترتيب: الصلة أولاً، ثم الدقة
    scored_results.sort(key=lambda x: (x['relevance_score'], x['resolution']), reverse=True)
    
    # 1. التنزيل المتوازي والتحقق من الترويسات والصلاحية (Parallel Download & Header Validation)
    urls_to_fetch = [r['item']['url'] for r in scored_results]
    print(f"⏳ [Parallel Download] جاري فحص وتنزيل {len(urls_to_fetch)} صورة مرشحة بالتوازي...")
    
    batch_data = run_coroutine_sync(execute_batch_processing(urls_to_fetch))
    
    valid_candidates = []
    
    # فحص الروابط بالترتيب واختيار أول رابط يمكن الوصول إليه ويجتاز الفحوصات المتقدمة
    for r in scored_results:
        item = r['item']
        c_idx = r['candidate_index']
        reasons = candidates[c_idx]['reasons']
        
        url = item['url']
        binary_data, status_msg = batch_data.get(url, (None, "FAILED_DOWNLOAD"))
        
        if binary_data is None:
            reasons.append(f"مستبعدة: فشل التحميل المتوازي أو التحقق من الحجم/النوع ({status_msg})")
            continue
            
        try:
            # فتح الصورة مباشرة في الذاكرة لتجنب استهلاك القرص
            pil_img = Image.open(io.BytesIO(binary_data)).convert("RGB")
            
            # التحقق من التكرار البصري عبر pHash و BK-Tree
            try:
                import image_dedup_bktree
                img_hash = image_dedup_bktree.calculate_phash(pil_img)
                bktree = get_bktree()
                duplicates = bktree.search(img_hash, max_distance=5)
                if duplicates:
                    dup = duplicates[0]
                    dup_meta = dup["metadata"]
                    dup_url = dup_meta.get("cloudinary_url")
                    if dup_url:
                        print(f"👁️ [BK-Tree Deduplicator] كشف تكرار بصري مع منتج آخر: '{dup_meta.get('product_name')}' (مسافة: {dup['distance']}) -> إعادة استخدام رابط Cloudinary!")
                        return {
                            "url": dup_url,
                            "title": item.get("title", ""),
                            "width": item.get("width", 800),
                            "height": item.get("height", 800),
                            "clip_score": 1.0,
                            "metadata": {},
                            "source": "visual_duplicate",
                            "perceptual_hash": str(img_hash)
                        }, 15
            except Exception as bke:
                print(f"⚠️ [BK-Tree Duplicate Check Error] {bke}")
            
            # أ. تشغيل بوابة الفرز الرياضي غير التوليدي لجودة الصورة
            # حساب نتيجة الجاذبية البصرية وقيمة التماثل البصري DINOv2
            aesthetic_score_raw = 5.0
            try:
                from aesthetics_engine import AestheticPredictor
                predictor = AestheticPredictor()
                aesthetic_score_raw = predictor._heuristic_aesthetic_fallback(pil_img)
            except Exception:
                pass

            dinov2_similarity = 0.85
            try:
                engine = DINOv2EmbeddingEngine()
                cand_embedding = engine.extract_features(pil_img)
                if cand_embedding is not None:
                    import local_cache_db
                    ref_prod = local_cache_db.get_cached_product(brand=brand)
                    if ref_prod and ref_prod.get("cloudinary_url"):
                        ref_url = ref_prod["cloudinary_url"]
                        ref_res = requests.get(ref_url, timeout=5)
                        if ref_res.status_code == 200:
                            ref_pil = Image.open(io.BytesIO(ref_res.content))
                            ref_embedding = engine.extract_features(ref_pil)
                            if ref_embedding is not None:
                                dinov2_similarity = engine.calculate_distance(cand_embedding, ref_embedding)
            except Exception as e:
                print(f"⚠️ [DINOv2 Similarity Check Error] Failed to calculate DINOv2 similarity: {e}")

            from image_quality_gatekeeper import ImageQualityGatekeeper
            gatekeeper = ImageQualityGatekeeper(
                target_resolution=1600,
                laplacian_threshold=100.0,
                min_width=config.MIN_IMAGE_WIDTH,
                min_height=config.MIN_IMAGE_HEIGHT
            )
            eval_report = gatekeeper.evaluate_image(
                pil_img, 
                relevance_score_text=r['relevance_score'],
                dinov2_similarity=dinov2_similarity,
                aesthetic_score_raw=aesthetic_score_raw
            )
            
            if not eval_report["passes_gates"]:
                reasons.append(f"مستبعدة: فشل التحقق الهندسي لجودة الصورة ({', '.join(eval_report['gate_reasons'])})")
                continue
                
            # ب. الفحص الدلالي المطور بـ SigLIP أو CLIP
            clip_embedding = None
            if config.USE_SIGLIP_SEMANTIC_CHECK:
                relevance_score_clip, _ = check_image_relevance_via_siglip(pil_img, brand, product_name)
            else:
                # تراجع لـ CLIP القديم في الذاكرة
                model, processor = get_clip_model()
                if model is not None and processor is not None:
                    import torch
                    inputs = processor(text=[f"a packaging of {brand} {product_name}"], images=pil_img, return_tensors="pt", padding=True)
                    with torch.no_grad():
                        outputs = model(**inputs)
                    relevance_score_clip = outputs.logits_per_image.item() / 100.0
                else:
                    relevance_score_clip = 1.0
            
            # حساب ترميز CLIP للفحص البصري للمكررات
            model, processor = get_clip_model()
            if model is not None and processor is not None:
                import torch
                inputs = processor(images=pil_img, return_tensors="pt")
                with torch.no_grad():
                    clip_embedding = model.get_image_features(**inputs).cpu().numpy()[0].tolist()
            
            # التحقق من عتبة الصلة
            is_relevant = relevance_score_clip >= config.CLIP_RELEVANCE_THRESHOLD
            is_grey_zone = False
            
            if not is_relevant:
                if relevance_score_clip >= getattr(config, "CLIP_GREY_ZONE_THRESHOLD", 0.18):
                    is_grey_zone = True
                else:
                    reasons.append(f"مستبعدة: درجة المطابقة الدلالية منخفضة ({relevance_score_clip:.4f} < {config.CLIP_RELEVANCE_THRESHOLD})")
                    continue
                    
            # ج. التحقق التلقائي بـ BLIP لكشف تعارض الماركات
            if config.USE_BLIP_CAPTION_CHECK:
                caption = generate_image_caption_via_blip(pil_img)
                if not verify_brand_alignment_via_caption(caption, brand, product_name):
                    reasons.append("مستبعدة: تم كشف تعارض صريح في العلامة التجارية عبر BLIP")
                    continue
                    
            # التحقق التلقائي بـ Moondream2 في الفرز الحتمي النهائي
            if config.USE_MOONDREAM_CHECK:
                if not verify_image_via_moondream(pil_img, brand, product_name):
                    reasons.append("مستبعدة: تم رفض الصورة بواسطة نموذج Moondream2 لعدم مطابقة الشروط")
                    continue
            
            # كشف التكرار البصري المحلي لمنع رفع نفس الصورة لمنتجين مختلفين
            if clip_embedding:
                try:
                    import local_cache_db
                    duplicate = local_cache_db.find_visual_duplicate(clip_embedding, threshold=0.96)
                    if duplicate and duplicate["product_name"].lower() != product_name.lower():
                        print(f"⚠️ [Visual Duplicate] الصورة مكررة بصرياً مع منتج آخر: '{duplicate['product_name']}'")
                        reasons.append(f"مستبعدة: كشف تكرار بصري متطابق مع منتج آخر ({duplicate['product_name']})")
                        continue
                except Exception as e:
                    print(f"⚠️ خطأ أثناء فحص التكرار البصري في الكاش: {e}")
            
            # الفحص الذكي بـ Gemini Vision (يتم حفظ الصورة مؤقتاً هنا فقط لتوفير استهلاك القرص ومكالمات الـ API)
            fd, temp_img = tempfile.mkstemp(suffix=".jpg")
            with os.fdopen(fd, 'wb') as f:
                f.write(binary_data)
                
            is_valid_gemini = validate_image_via_gemini_vision(temp_img, product_name, brand)
            try:
                os.remove(temp_img)
            except Exception:
                pass
                
            if not is_valid_gemini:
                reasons.append("مستبعدة: تم رفض المطابقة البصرية عبر Gemini Vision (براند/منتج خاطئ)")
                continue
            
            # إذا اجتازت كافة بوابات التصفية والفحوصات، يتم إضافتها للمرشحين المقبولين
            valid_candidates.append({
                "item": item,
                "relevance_score": r['relevance_score'],
                "relevance_score_clip": relevance_score_clip,
                "clip_embedding": clip_embedding,
                "is_grey_zone": is_grey_zone,
                "eval_report": eval_report,
                "c_idx": c_idx
            })
            
        except Exception as e:
            reasons.append(f"⚠️ فشل تحليل الصورة في الذاكرة: {e}")
            continue

    chosen_item = None
    chosen_relevance = 0

    if valid_candidates:
        # Mark all valid candidates as accepted
        for vc in valid_candidates:
            candidates[vc["c_idx"]]['status'] = 'accepted'
            
        # ترتيب المرشحين المقبولين تنازلياً حسب النتيجة الموحدة (Unified Score)
        valid_candidates.sort(key=lambda x: x["eval_report"]["unified_score"], reverse=True)
        best_cand = valid_candidates[0]
        
        c_idx = best_cand["c_idx"]
        reasons = candidates[c_idx]['reasons']
        is_grey_zone = best_cand["is_grey_zone"]
        relevance_score_clip = best_cand["relevance_score_clip"]
        
        if is_grey_zone:
            reasons.append(f"مقبولة مراجعة: الصورة الحاصلة على أعلى تقييم هندسي موحد ({best_cand['eval_report']['unified_score']:.4f}) في المنطقة الرمادية (SigLIP/CLIP Similarity: {relevance_score_clip:.4f})")
            candidates[c_idx]['status'] = 'accepted'
            chosen_item = best_cand["item"]
            chosen_item['needs_review'] = True
            chosen_item['clip_score'] = relevance_score_clip
            chosen_item['clip_embedding'] = best_cand["clip_embedding"]
            chosen_relevance = best_cand["relevance_score"]
        else:
            reasons.append(f"مقبولة: الصورة الحاصلة على أعلى تقييم هندسي موحد ({best_cand['eval_report']['unified_score']:.4f}) مع مطابقة تامة وموثقة (SigLIP/CLIP Similarity: {relevance_score_clip:.4f})")
            candidates[c_idx]['status'] = 'accepted'
            chosen_item = best_cand["item"]
            chosen_item['needs_review'] = False
            chosen_item['clip_score'] = relevance_score_clip
            chosen_item['clip_embedding'] = best_cand["clip_embedding"]
            chosen_relevance = best_cand["relevance_score"]

    if chosen_item:
        if trace is not None:
            trace.setdefault('steps', []).append({
                "name": step_name,
                "query": query or "",
                "results_count": len(results),
                "candidates": candidates
            })
        return chosen_item, chosen_relevance
        
    # خيار أخير في حال لم تقبل أي صورة بسبب الفحص ولكنها اجتازت الفلترة الأولية
    r = scored_results[0]
    item = r['item']
    c_idx = r['candidate_index']
    candidates[c_idx]['reasons'].append("مقبولة: كخيار بديل أخير من نتائج التصفية الأولية")
    candidates[c_idx]['status'] = 'accepted'
    
    if trace is not None:
        trace.setdefault('steps', []).append({
            "name": step_name,
            "query": query or "",
            "results_count": len(results),
            "candidates": candidates
        })
    return item, r['relevance_score']

def expand_query_via_gemini(product_name, brand):
    """
    استخدام Gemini لإنشاء 3 جمل بحث محسنة ومختلفة لزيادة فرص العثور على صورة المنتج الصحيحة.
    """
    if not config.GEMINI_API_KEY:
        return [f"{brand} {product_name}".strip()]
        
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{config.GEMINI_MODEL}:generateContent?key={config.GEMINI_API_KEY}"
        
        prompt = (
            f"You are a shopping search optimization assistant.\n"
            f"Generate 3 diverse search engine queries for finding product images of: Brand: '{brand}' and Product Name: '{product_name}'.\n"
            f"Requirements:\n"
            f"1. Query 1: standard brand and product name in English.\n"
            f"2. Query 2: optimized shopping search string in English with 'packaging' or 'product pack' appended.\n"
            f"3. Query 3: optimized search string in Arabic containing the translated brand and product name.\n"
            f"Return strictly a JSON array of strings containing exactly 3 queries, like:\n"
            f'["query 1", "query 2", "query 3"]'
        )
        
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json"}
        }
        headers = {"Content-Type": "application/json"}
        
        if hasattr(config, "METRICS") and "gemini_api_calls" in config.METRICS:
            config.METRICS["gemini_api_calls"] += 1
            
        res = requests.post(url, headers=headers, json=payload, timeout=8)
        if res.status_code == 200:
            import json
            res_data = res.json()
            text = res_data['candidates'][0]['content']['parts'][0]['text'].strip()
            if text.startswith("```json"):
                text = text[7:]
            elif text.startswith("```"):
                text = text[3:]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()
            queries = json.loads(text)
            if isinstance(queries, list) and len(queries) >= 1:
                print(f"🤖 [Gemini Query Expansion] الاستعلامات المولدة: {queries}")
                return [q.strip() for q in queries if q.strip()]
    except Exception as e:
        print(f"⚠️ خطأ أثناء توليد الاستعلامات عبر Gemini: {e}")
        
    return [
        f"{brand} {product_name}".strip(),
        f"{brand} {product_name} packaging".strip(),
        f"{brand} {product_name}".strip()
    ]

def run_parallel_consensus_search(query):
    """
    تشغيل البحث المتوازي التوافقي باستخدام ParallelConsensusScraper.
    """
    import asyncio
    import time
    import random
    
    # تأخير عشوائي ذكي لحماية الـ IP وتفادي كشف الـ CAPTCHA
    delay = random.uniform(1.5, 4.0)
    print(f"⏱️ [Throttling] الانتظار لمدة {delay:.2f} ثانية لحماية الـ IP...")
    time.sleep(delay)
    
    scraper = ParallelConsensusScraper(
        google_key=config.GOOGLE_SEARCH_API_KEY,
        google_cx=config.GOOGLE_SEARCH_CX,
        bing_key=getattr(config, "BING_SEARCH_API_KEY", ""),
        yandex_key=getattr(config, "YANDEX_FOLDER_ID", "")
    )
    try:
        return run_coroutine_sync(scraper.aggregate_consensus_rankings(query))
    except Exception as e:
        print(f"⚠️ [Parallel Search Error] فشل البحث المتوازي التوافقي لـ '{query}': {e}")
        return []

def search_best_product_image(query, product_name, brand, **kwargs):
    """
    البحث واختيار الصورة الأمثل للمنتج، مع تطبيق خطة بديلة للبحث العام
    في حال لم تكن هناك صور خاصة بالبراند (لأن البراندات المحلية مثل Meliha قد لا تملك صوراً على محركات البحث).
    """
    trace = kwargs.get('trace')
    strict_brand_match = kwargs.get('strict_brand_match')
    brand_mappings = kwargs.get('brand_mappings')
    barcode = kwargs.get('barcode', '')
    skip_cache = kwargs.get('skip_cache', False)
    
    # 0. الاستعلام من قاعدة البيانات المحلية الكاش كخطوة أولى فائقة السرعة
    if not skip_cache:
        try:
            import local_cache_db
            cached = local_cache_db.get_cached_product(barcode=barcode, product_name=product_name, brand=brand)
            if cached:
                print(f"⚡ [SQLite Cache Hit] العثور على حل مخزن محلياً لـ '{product_name}'")
                return {
                    "url": cached["cloudinary_url"],
                    "title": "مسترجع من الكاش المحلي",
                    "width": 800,
                    "height": 800,
                    "clip_score": cached["clip_score"],
                    "metadata": cached["metadata"],
                    "source": "sqlite_cache"
                }
        except Exception as e:
            print(f"⚠️ خطأ أثناء قراءة الكاش المحلي: {e}")
            
        # ملاحظة: تم إيقاف الاستعلام الدلالي الذكي عبر المتجهات (Semantic Vector Cache) لمنع أي خلط بين أسماء المنتجات المختلفة من نفس البراند
        pass
    
    # افتراضياً، نقوم بتفعيل المطابقة الصارمة إذا كان البراند مدخلاً لمنع خلط المنتجات مع المنافسين
    if strict_brand_match is None and brand:
        strict_brand_match = True
        
    # جلب مرادفات البراندات بالخلفية إذا لم تكن ممررة
    if brand and not brand_mappings:
        try:
            import google_sheets
            sheets_client = google_sheets.get_sheets_client()
            if sheets_client:
                brand_mappings = google_sheets.get_brand_mappings(sheets_client, config.SPREADSHEET_NAME_OR_URL)
        except Exception as e:
            print(f"⚠️ فشل جلب مرادفات البراندات في محرك البحث: {e}")
            
    # 0. البحث بالباركود كخيار أول فائق الدقة
    barcode = kwargs.get('barcode', '')
    if barcode and str(barcode).strip():
        barcode_clean = str(barcode).strip()
        print(f"🔍 جاري البحث المخصص باستخدام الباركود للمنتج: '{barcode_clean}'...")
        
        barcode_results = run_parallel_consensus_search(barcode_clean)
            
        if barcode_results:
            best_image, brand_score = evaluate_and_choose_best_image(
                barcode_results, product_name, brand, requires_brand_match=True, trace=trace, 
                step_name="البحث بالباركود والبراند", query=barcode_clean, brand_mappings=brand_mappings
            )
            if best_image:
                print(f"🎯 تم العثور على صورة المنتج المطابقة بنجاح عبر الباركود: {best_image['title']}")
                return best_image
        print(f"ℹ️ لم يتم العثور على صورة مطابقة عبر الباركود. الانتقال للبحث النصي الأساسي...")
        
        
    # 1. البحث باستخدام الاستعلامات الموسعة بالذكاء الاصطناعي
    queries = expand_query_via_gemini(product_name, brand)
    
    best_image = None
    brand_score = 0
    all_primary_results = []
    
    for q_idx, q in enumerate(queries, start=1):
        print(f"🔍 [Gemini Query {q_idx}/3] جاري البحث بالاستعلام المحسن: '{q}'...")
        q_results = run_parallel_consensus_search(q)
            
        if q_results:
            all_primary_results.extend(q_results)
            best_image, brand_score = evaluate_and_choose_best_image(
                q_results, product_name, brand, requires_brand_match=True, trace=trace, 
                step_name=f"البحث بالاستعلام المحسن {q_idx}", query=q, brand_mappings=brand_mappings
            )
            if best_image:
                print(f"🎯 تم العثور على صورة مقبولة باستخدام الاستعلام المحسن: '{q}'")
                break
        
    # 2. خطة البحث البديل (Generic Fallback Search)
    # إذا لم نجد صورة مطابقة للبراند (brand_score = 0)، فهذا يعني أن البراند غير مفهرس.
    # في حالة تفعيل المطابقة الصارمة للبراند، نرفض الانتقال للبحث العام لحظر صور البراندات المنافسة.
    if not best_image or brand_score == 0:
        if strict_brand_match and brand:
            print(f"ℹ️ تم تفعيل المطابقة الصارمة للبراند. تخطي البحث العام البديل للبراند '{brand}' لحظر صور المنافسين.")
            
            # تسجيل خطوة تخطي في التتبع للشفافية في الواجهة
            if trace is not None:
                trace.setdefault('steps', []).append({
                    "name": "تخطي البحث العام (مطابقة صارمة للبراند)",
                    "query": query or "",
                    "results_count": 0,
                    "candidates": []
                })
            return None
            
        fallback_query = get_fallback_query(product_name)
        print(f"ℹ️ لم يتم العثور على صور مفهرسة للبراند '{brand}'. تم تشغيل البحث العام للمنتج: '{fallback_query}'...")
        
        fallback_results = run_parallel_consensus_search(fallback_query)
            
        if fallback_results:
            # تقييم الصور العامة (بدون اشتراط مطابقة البراند) واختيار الصورة الأعلى صلة بالمنتج
            best_image, _ = evaluate_and_choose_best_image(
                fallback_results, product_name, brand, requires_brand_match=False, trace=trace, 
                step_name="البحث العام البديل", query=fallback_query, brand_mappings=brand_mappings
            )
            if best_image:
                print(f"🎯 تم اختيار صورة للمنتج العام بدقة {best_image['width']}x{best_image['height']}: {best_image['title']}")
                return best_image
                
    if best_image:
        print(f"🎯 تم اختيار الصورة المطابقة للبراند بدقة {best_image['width']}x{best_image['height']}: {best_image['title']}")
        return best_image
        
    # إذا فشل كل شيء، نأخذ أول صورة من البحث الأساسي كخيار أخير جداً
    if all_primary_results:
        # إذا كانت المطابقة الصارمة مفعلة، لا يجب أن نأخذ أي صورة عشوائية لم تطابق البراند
        if strict_brand_match and brand:
            print("ℹ️ تم تفعيل المطابقة الصارمة للبراند. تخطي أخذ أي صورة لم تطابق البراند كخيار أخير.")
            return None
        print("⚠️ تحذير: لم نجد صورة براند مطابقة ولا صورة عامة مثالية. اختيار أول نتيجة بحث أساسي كخيار أخير.")
        return all_primary_results[0]
        
    print(f"❌ فشل العثور على أي صورة للمنتج '{product_name}'")
    return None
