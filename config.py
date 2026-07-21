# config.py
# ملف الإعدادات الخاص بنظام الأتمتة

import os

# وظيفة بسيطة لقراءة ملف .env وتعيين المتغيرات البيئية يدوياً بدون مكتبات خارجية
def _load_env(env_path=".env"):
    base_dir = os.path.dirname(os.path.abspath(__file__))
    env_file = os.path.join(base_dir, env_path)
    if os.path.exists(env_file):
        with open(env_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, val = line.split("=", 1)
                    key = key.strip()
                    val = val.strip().strip('"').strip("'")
                    os.environ[key] = val

_load_env()

# 1. إعدادات Google Sheets
# يمكن وضع اسم الشيت أو الرابط الكامل له
SPREADSHEET_NAME_OR_URL = os.getenv("SPREADSHEET_NAME_OR_URL", "automation sheet")

# 2. إعدادات Google Drive
# معرف المجلد (Folder ID) الذي سيتم رفع الصور إليه على Drive.
# إذا تركته فارغاً ""، سيتم رفع الصور إلى المجلد الرئيسي للـ Service Account.
# يرجى مشاركة هذا المجلد مع البريد الإلكتروني للـ Service Account:
# outomation-agent@boulevard-a50a0.iam.gserviceaccount.com
DRIVE_FOLDER_ID = os.getenv("DRIVE_FOLDER_ID", "")

# 3. إعدادات البحث عن الصور (Google Image Search)
# للحصول على نتائج دقيقة ورسمية، أدخل بيانات Google Custom Search API أدناه.
# يدعم النظام إدخال عدة مفاتيح مفصولة بفاصلة (,) للتدوير التلقائي عند نفاد الحصة (Quota Rotation).
GOOGLE_SEARCH_API_KEYS = [k.strip() for k in os.getenv("GOOGLE_SEARCH_API_KEY", "").split(",") if k.strip()]
GOOGLE_SEARCH_CX_LIST = [c.strip() for c in os.getenv("GOOGLE_SEARCH_CX", "").split(",") if c.strip()]

GOOGLE_SEARCH_API_KEY = GOOGLE_SEARCH_API_KEYS[0] if GOOGLE_SEARCH_API_KEYS else ""
GOOGLE_SEARCH_CX = GOOGLE_SEARCH_CX_LIST[0] if GOOGLE_SEARCH_CX_LIST else ""

# إذا كانت بيانات Google غير متوفرة، سيقوم النظام تلقائياً بالاعتماد على محركات البحث الهجينة
USE_FALLBACK_SEARCH = True

# 4. إعدادات معالجة الصور وتحجيمها
# الأبعاد الافتراضية المطلوبة لجميع الصور بشكل ديناميكي (مثال: 800×800)
IMAGE_TARGET_SIZE = (800, 800)

# خيار إزالة خلفية الصورة. الخيارات المتاحة:
# "none" -> تخطي إزالة الخلفية والقيام بالتحجيم فقط (مفيد للاختبار السريع)
# "bria_rmbg" -> استخدام نموذج Bria RMBG 1.4 المحلي المجاني وفائق الدقة (مستحسن)
# "rembg" -> استخدام مكتبة rembg المحلية المجانية تماماً (تتطلب تثبيت pip install rembg)
# "remove_bg_api" -> استخدام خدمة remove.bg السحابية (تتطلب إدخال مفتاح API أدناه)
# "photoroom" -> استخدام خدمة PhotoRoom السحابية لإزالة الخلفية مع القص التلقائي الاحترافي للهوامش
BG_REMOVAL_METHOD = os.getenv("BG_REMOVAL_METHOD", "photoroom")

# مفتاح API الخاص بخدمة remove.bg (مطلوب فقط إذا اخترت "remove_bg_api")
REMOVE_BG_API_KEY = os.getenv("REMOVE_BG_API_KEY", "")

# مفتاح API الخاص بخدمة PhotoRoom (مطلوب فقط إذا اخترت "photoroom")
PHOTOROOM_API_KEY = os.getenv("PHOTOROOM_API_KEY", "")

# إعدادات واجهة برمجة تطبيقات إزالة الخلفية لـ PhotoRoom (v1/segment API)
PHOTOROOM_SIZE = "full"       # دقة الصورة المستردة: preview, medium, hd, full
PHOTOROOM_CROP = False        # قص الهوامش الشفافة الزائدة لجعل الكائن ممتداً على كامل الحدود (False لترك مساحة الحواف الأصلية)
PHOTOROOM_DESPILL = True      # تفعيل تقنية تصحيح الحواف وإزالة تسرب الألوان من الخلفية الأصلية (Chroma key)

# 5. ملف اعتمادات Google Service Account
CREDENTIALS_FILE = os.getenv("CREDENTIALS_FILE", os.path.join(os.path.dirname(os.path.abspath(__file__)), "credentials.json"))

# 6. إعدادات Cloudinary
CLOUDINARY_CLOUD_NAME = os.getenv("CLOUDINARY_CLOUD_NAME", "")
CLOUDINARY_API_KEY = os.getenv("CLOUDINARY_API_KEY", "")
CLOUDINARY_API_SECRET = os.getenv("CLOUDINARY_API_SECRET", "")

# تفعيل إزالة الخلفية عبر الذكاء الاصطناعي لـ Cloudinary (يتطلب تفعيل الإضافة في حسابك)
CLOUDINARY_BG_REMOVAL = False

# الأبعاد المستهدفة سحابياً لإعادة الاحتواء وتوحيد الأبعاد (recontainment)
CLOUDINARY_TARGET_SIZE = (800, 800)

# الحد الأدنى لأبعاد الصورة المقبولة (لتجنب المصغرات والصور منخفضة الجودة)
MIN_IMAGE_WIDTH = 100
MIN_IMAGE_HEIGHT = 100
ENABLE_ASPECT_RATIO_CHECK = True
MIN_ASPECT_RATIO = 0.4
MAX_ASPECT_RATIO = 2.5


# إعدادات تحسين الجودة سحابياً عبر Cloudinary
CLOUDINARY_QUALITY = "auto:best" # درجة جودة الضغط سحابياً (مثل auto أو auto:best أو auto:good للمحافظة على أقصى دقة)
CLOUDINARY_AUTO_QUALITY = True  # تفعيل الضغط والتحسين التلقائي للحجم (q_auto)
CLOUDINARY_AUTO_FORMAT = True   # تفعيل تحويل الصيغة التلقائي للأسرع للويب (f_auto)
CLOUDINARY_AI_ENHANCE = False    # إيقاف تحسين الألوان السحابي التلقائي لمنع التشويه والألوان الفاقعة
CLOUDINARY_SHARPEN = 20          # قوة حدة الصورة سحابياً (0 للإيقاف، تم استخدام 20 لإبراز تفاصيل النصوص دون التسبب بتشويه)
CLOUDINARY_TRIM_TOLERANCE = 5  # سماحية الاقتصاص لـ Cloudinary لمنع قص حواف المنتجات اللامعة أو الدائرية (0-100)

# إعدادات الظلال والتجاوز الذكي للخلفيات البيضاء المجهزة مسبقاً
ENABLE_STUDIO_SHADOWS = False    # تعطيل ظلال الاستوديو لتلبية طلب العميل بعدم وجود ظلال
BYPASS_WHITE_BACKGROUND_CHECK = os.getenv("BYPASS_WHITE_BACKGROUND_CHECK", "False").lower() == "true"  # تخطي إزالة الخلفية والقص إذا كانت الصورة الأصلية بالفعل بخلفية بيضاء نقية وجودة عالية
WHITE_BACKGROUND_THRESHOLD = 0.96      # النسبة المقبولة للبكسلات البيضاء على إطار الصورة (96%) للاعتبار كخلفية بيضاء
ENABLE_IMAGE_ENHANCEMENT = False       # تعطيل تحسين/تنعيم الألوان والصور الذكائي الافتراضي لمنع بهتان الألوان وجعلها اختيارية


# 7. إعدادات تخطي أو استبدال الصور
# إذا كان True، سيقوم النظام بالبحث عن الصور وتحديثها حتى لو كانت الخلية تحتوي على رابط سابق.
# إذا كان False، سيتم تخطي أي صف يحتوي بالفعل على رابط صورة لتوفير الموارد.
FORCE_OVERWRITE_IMAGES = True

# وضع المراجعة والاعتماد اليدوي (Curation Mode).
# إذا كان True، فسيتم إرسال روابط الصور المكتشفة إلى الشيت مع بادئة مراجعة 'needs_review:' دون استهلاك رصيد PhotoRoom.
# بعد ذلك، يعتمد المستخدم الصورة المناسبة من لوحة التحكم، وسيتم إرسالها لـ PhotoRoom لإزالة الخلفية وتحديث الشيت مباشرة.
CURATION_MODE = os.getenv("CURATION_MODE", "True").lower() == "true"

# 8. إعدادات التحقق المتقدم للبحث والصور
# مفتاح API الخاص بـ Gemini (من Google AI Studio) للتحقق البصري المتقدم
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")

# إيقاف تحميل وتشغيل نماذج الذكاء الاصطناعي المحلية (CLIP, SigLIP, BLIP, Moondream2, DINOv2) لتوفير الذاكرة والعمل سحابياً بتكلفة منخفضة
DISABLE_LOCAL_AI_MODELS = os.getenv("DISABLE_LOCAL_AI_MODELS", "True").lower() == "true"

ENABLE_GEMINI_VISION = True
ENABLE_LOCAL_OCR = False
STRICT_BRAND_MATCH = os.getenv("STRICT_BRAND_MATCH", "True").lower() == "true"

# إعدادات التطوير الجديدة لزيادة الدقة
CLIP_RELEVANCE_THRESHOLD = float(os.getenv("CLIP_RELEVANCE_THRESHOLD", "0.22"))
CLIP_GREY_ZONE_THRESHOLD = float(os.getenv("CLIP_GREY_ZONE_THRESHOLD", "0.18"))
ENABLE_GEMINI_PRE_VALIDATION = os.getenv("ENABLE_GEMINI_PRE_VALIDATION", "True").lower() == "true"
FILTER_COMPETITORS = os.getenv("FILTER_COMPETITORS", "True").lower() == "true"
# إعدادات النماذج المحلية للتحقق البصري المطور (Hugging Face Models)
SIGLIP_MODEL_ID = "google/siglip-base-patch16-224"
BLIP_MODEL_ID = "Salesforce/blip-image-captioning-base"
MOONDREAM_MODEL_ID = "vikhyatk/moondream2"

USE_SIGLIP_SEMANTIC_CHECK = True
USE_BLIP_CAPTION_CHECK = True
USE_MOONDREAM_CHECK = False  # يمكن تفعيله يدوياً لتشغيل Moondream2 في الفرز الحتمي النهائي

# نطاقات المواقع الإماراتية الموثوقة للتجارة الإلكترونية لتحديد نطاق البحث المبدئي
TRUSTED_UAE_DOMAINS = ["kibsons.com", "carrefouruae.com", "luluhypermarket.com", "noon.com", "amazon.ae"]

# 9. إعدادات الترقيات المتقدمة (البروكسي والتنبيهات)
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "7974160066:AAFdgG1HZuu_822sCTwzYDNmk_-ZnebKrYc")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
PROXY_URL = os.getenv("PROXY_URL", "")

# تتبع استهلاك الـ API محلياً في الذاكرة
METRICS = {
    "gemini_api_calls": 0,
    "cloudinary_uploads": 0,
    "successful_runs": 0,
    "failed_runs": 0,
    "semantic_cache_savings": 0
}

# 10. إعدادات تسريع الأداء وتحسين الكفاءة للبحث
MAX_PARALLEL_DOWNLOADS = 4      # عدد التنزيلات المتوازية للصور المرشحة
SEARCH_CACHE_ENABLED = True     # تفعيل التخزين المؤقت لنتائج محرك البحث لمنع التكرار
SEARCH_CACHE_TTL = 86400        # عمر التخزين المؤقت للبحث (24 ساعة بالثواني)

RUNNER_LOGS = []
_redis_available = (os.getenv("RUN_WITH_REDIS") == "1")

def log_runner(*args):
    """
    تدوين رسالة مع التوقيت وعرضها في السجل الحي للوحة التحكم.
    """
    global _redis_available
    from datetime import datetime
    import builtins
    import json
    msg = " ".join(str(a) for a in args)
    time_str = datetime.now().strftime("%H:%M:%S")
    formatted = f"[{time_str}] {msg}"
    builtins.print(formatted)
    
    # الإضافة للقائمة المشتركة في الذاكرة مع تحديد حد أقصى 100 سطر لمنع تسرب الذاكرة
    RUNNER_LOGS.append(formatted)
    if len(RUNNER_LOGS) > 100:
        RUNNER_LOGS.pop(0)

    # بث الرسالة لـ Redis Pub/Sub لتغذية خوادم البث المباشر (SSE) لـ Gunicorn
    if _redis_available is not False:
        try:
            import redis
            # استخدام مهلة منخفضة جداً (0.2 ثانية) للفحص السريع لمنع تعليق الكونسول
            r = redis.Redis(host='localhost', port=6379, db=0, socket_timeout=0.2)
            payload = {
                "timestamp": datetime.now().timestamp(),
                "log": formatted,
                "pipeline_metrics": {
                    "progress_percentage": 100 if "بنجاح" in msg or "نجح" in msg else (10 if "البدء" in msg else 50),
                    "active_sku_id": "Ingesting..."
                },
                "telemetry": {
                    "queue_delay_seconds": 1.5,
                    "gemini_api_tokens": METRICS.get("gemini_api_calls", 0) * 150
                }
            }
            r.publish("tenant_stream:enterprise_tenant_102", json.dumps(payload))
            _redis_available = True
        except Exception:
            _redis_available = False
            # طباعة رسالة تنبيهية خفيفة مرة واحدة
            builtins.print("ℹ️ [System Notice] خادم Redis غير متصل محلياً؛ تم إيقاف محاولات البث المباشر لتسريع المعالجة.")

def log_error_to_laravel(error_message, barcode=None, product_name=None, brand=None, level="ERROR"):
    """
    تدوين رسائل الأخطاء وتفاصيلها مباشرة في ملف سجلات لارافيل `dashboard/storage/logs/laravel.log`.
    """
    import threading
    from datetime import datetime
    
    # تحديد مسار ملف السجلات بشكل نسبي
    base_dir = os.path.dirname(os.path.abspath(__file__))
    log_file_path = os.path.join(base_dir, "dashboard", "storage", "logs", "laravel.log")
    
    # التأكد من وجود المجلد
    log_dir = os.path.dirname(log_file_path)
    try:
        os.makedirs(log_dir, exist_ok=True)
    except Exception:
        pass
        
    time_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # بناء تفاصيل المنتج إن وجدت
    prod_details = []
    if product_name:
        prod_details.append(f"Product: {product_name}")
    if brand:
        prod_details.append(f"Brand: {brand}")
    if barcode:
        prod_details.append(f"Barcode: {barcode}")
        
    context_str = f" - [{', '.join(prod_details)}]" if prod_details else ""
    
    # صياغة السطر بتنسيق لارافيل
    formatted_log = f"[{time_str}] local.{level}: Python Pipeline{context_str}: {error_message}\n"
    
    # استخدام قفل محلي لحماية الكتابة المتزامنة في نفس العملية
    if not hasattr(log_error_to_laravel, "_lock"):
        log_error_to_laravel._lock = threading.Lock()
        
    with log_error_to_laravel._lock:
        try:
            with open(log_file_path, "a", encoding="utf-8") as f:
                f.write(formatted_log)
        except Exception as e:
            import builtins
            builtins.print(f"⚠️ فشل الكتابة في ملف سجلات لارافيل: {e}")

def send_telegram_alert(message):
    """
    إرسال إشعار فوري عبر بوت Telegram للمشرف
    """
    token = os.getenv("TELEGRAM_BOT_TOKEN", TELEGRAM_BOT_TOKEN)
    chat_id = os.getenv("TELEGRAM_CHAT_ID", TELEGRAM_CHAT_ID)
    if not token or not chat_id:
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML"
    }
    try:
        import requests
        requests.post(url, json=payload, timeout=5)
        return True
    except Exception:
        return False

def log_and_fail(barcode, product_name, brand, error_message):
    """
    تدوين الخطأ في الكونسول وتخزينه في جدول أخطاء SQLite.
    """
    log_runner(f"❌ فشل أتمتة المنتج '{product_name}': {error_message}")
    
    # تدوين الفشل في ملف سجلات لارافيل
    log_error_to_laravel(error_message, barcode=barcode, product_name=product_name, brand=brand, level="ERROR")
    
    try:
        import local_cache_db
        local_cache_db.save_product_failure(barcode, product_name, brand, error_message)
    except Exception as e:
        import builtins
        builtins.print(f"⚠️ خطأ أثناء حفظ سجل الفشل: {e}")

    # إرسال إشعار تليجرام في حال وجود أخطاء متعلقة بالاشتراكات أو الحصص أو الـ APIs
    lower_err = error_message.lower()
    quota_keywords = ["quota", "limit", "402", "429", "unauthorized", "api_key", "expired", "exhausted", "billing", "payment", "credentials", "connection failed"]
    if any(k in lower_err for k in quota_keywords):
        alert_msg = (
            f"🚨 <b>تنبيه خطأ أتمتة حرج (Subscription/API Error)</b>\n\n"
            f"📦 <b>المنتج:</b> {product_name}\n"
            f"🏷️ <b>الماركة:</b> {brand}\n"
            f"🔢 <b>الباركود:</b> {barcode or 'N/A'}\n"
            f"❌ <b>الخطأ المكتشف:</b> <code>{error_message}</code>\n\n"
            f"💡 <i>يرجى مراجعة إعدادات الاشتراك أو مفاتيح الـ API في ملف .env لحل المشكلة.</i>"
        )
        send_telegram_alert(alert_msg)


# 11. إعدادات خادم Redis
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_DB = int(os.getenv("REDIS_DB", "0"))

# 12. إعدادات فلاتر الأتمتة الجماعية المتقدمة
BRAND_FILTER = ""
ROW_FILTER = ""
AUTO_APPROVE_THRESHOLD = 0.0 # 0.0 تعني تعطيل الاعتماد التلقائي

def load_db_config():
    """
    تحميل الإعدادات ديناميكياً من قاعدة البيانات لتجنب تعديل ملفات البيئة يدوياً.
    """
    import os
    db_host = os.getenv("DB_HOST", "127.0.0.1")
    db_port = int(os.getenv("DB_PORT", "3306"))
    db_user = os.getenv("DB_USERNAME", "root")
    db_pass = os.getenv("DB_PASSWORD", "")
    db_name = os.getenv("DB_DATABASE", "automation_db")
    
    try:
        import pymysql
        conn = pymysql.connect(
            host=db_host,
            port=db_port,
            user=db_user,
            password=db_pass,
            database=db_name,
            charset='utf8mb4',
            cursorclass=pymysql.cursors.DictCursor
        )
        cursor = conn.cursor()
        cursor.execute("SHOW TABLES LIKE 'system_settings'")
        if cursor.fetchone():
            cursor.execute("SELECT `key`, `value` FROM system_settings")
            rows = cursor.fetchall()
            db_keys = {}
            for r in rows:
                db_keys[r['key']] = r['value']
            
            global PHOTOROOM_API_KEY, GEMINI_API_KEY, GEMINI_MODEL
            global CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, CLOUDINARY_API_SECRET
            global GOOGLE_SEARCH_API_KEYS, GOOGLE_SEARCH_CX_LIST, GOOGLE_SEARCH_API_KEY, GOOGLE_SEARCH_CX
            global CLIP_RELEVANCE_THRESHOLD, CLIP_GREY_ZONE_THRESHOLD, STRICT_BRAND_MATCH, ENABLE_GEMINI_PRE_VALIDATION, FILTER_COMPETITORS, BYPASS_WHITE_BACKGROUND_CHECK, PROXY_URL
            
            if "photoroom_api_key" in db_keys and db_keys["photoroom_api_key"]:
                PHOTOROOM_API_KEY = db_keys["photoroom_api_key"]
            if "gemini_api_key" in db_keys and db_keys["gemini_api_key"]:
                GEMINI_API_KEY = db_keys["gemini_api_key"]
            if "gemini_model" in db_keys and db_keys["gemini_model"]:
                GEMINI_MODEL = db_keys["gemini_model"]
            if "cloudinary_cloud_name" in db_keys and db_keys["cloudinary_cloud_name"]:
                CLOUDINARY_CLOUD_NAME = db_keys["cloudinary_cloud_name"]
            if "cloudinary_api_key" in db_keys and db_keys["cloudinary_api_key"]:
                CLOUDINARY_API_KEY = db_keys["cloudinary_api_key"]
            if "cloudinary_api_secret" in db_keys and db_keys["cloudinary_api_secret"]:
                CLOUDINARY_API_SECRET = db_keys["cloudinary_api_secret"]
            if "google_search_api_key" in db_keys and db_keys["google_search_api_key"]:
                keys = [k.strip() for k in db_keys["google_search_api_key"].split(",") if k.strip()]
                if keys:
                    GOOGLE_SEARCH_API_KEYS = keys
                    GOOGLE_SEARCH_API_KEY = keys[0]
            if "google_search_cx" in db_keys and db_keys["google_search_cx"]:
                cxs = [c.strip() for c in db_keys["google_search_cx"].split(",") if c.strip()]
                if cxs:
                    GOOGLE_SEARCH_CX_LIST = cxs
                    GOOGLE_SEARCH_CX = cxs[0]
            if "clip_relevance_threshold" in db_keys and db_keys["clip_relevance_threshold"]:
                CLIP_RELEVANCE_THRESHOLD = float(db_keys["clip_relevance_threshold"])
            if "clip_grey_zone_threshold" in db_keys and db_keys["clip_grey_zone_threshold"]:
                CLIP_GREY_ZONE_THRESHOLD = float(db_keys["clip_grey_zone_threshold"])
            if "strict_brand_match" in db_keys:
                STRICT_BRAND_MATCH = db_keys["strict_brand_match"].lower() == "true"
            if "enable_gemini_pre_validation" in db_keys:
                ENABLE_GEMINI_PRE_VALIDATION = db_keys["enable_gemini_pre_validation"].lower() == "true"
            if "filter_competitors" in db_keys:
                FILTER_COMPETITORS = db_keys["filter_competitors"].lower() == "true"
            if "bypass_white_background_check" in db_keys:
                BYPASS_WHITE_BACKGROUND_CHECK = db_keys["bypass_white_background_check"].lower() == "true"
            if "proxy_url" in db_keys and db_keys["proxy_url"]:
                PROXY_URL = db_keys["proxy_url"]
            
            import sys
            if "--json" not in sys.argv:
                import builtins
                builtins.print("⚙️ [Config Loader] تم تحميل الإعدادات وتجاوز قيم بيئة .env ديناميكياً من قاعدة البيانات.")
        conn.close()
    except Exception as e:
        import sys
        if "--json" not in sys.argv:
            import builtins
            builtins.print(f"⚠️ [Config Loader] تنبيه أثناء تحميل الإعدادات من قاعدة البيانات (قد لا تكون مهيأة بعد): {e}")

load_db_config()


