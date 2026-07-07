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
SPREADSHEET_NAME_OR_URL = "automation sheet"

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
BG_REMOVAL_METHOD = "bria_rmbg"

# مفتاح API الخاص بخدمة remove.bg (مطلوب فقط إذا اخترت "remove_bg_api")
REMOVE_BG_API_KEY = os.getenv("REMOVE_BG_API_KEY", "")

# 5. ملف اعتمادات Google Service Account
CREDENTIALS_FILE = os.getenv("CREDENTIALS_FILE", os.path.join(os.path.dirname(os.path.abspath(__file__)), "credentials.json"))

# 6. إعدادات Cloudinary
CLOUDINARY_CLOUD_NAME = os.getenv("CLOUDINARY_CLOUD_NAME", "")
CLOUDINARY_API_KEY = os.getenv("CLOUDINARY_API_KEY", "")
CLOUDINARY_API_SECRET = os.getenv("CLOUDINARY_API_SECRET", "")

# تفعيل إزالة الخلفية عبر الذكاء الاصطناعي لـ Cloudinary (يتطلب تفعيل الإضافة في حسابك)
CLOUDINARY_BG_REMOVAL = True

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

# 7. إعدادات تخطي أو استبدال الصور
# إذا كان True، سيقوم النظام بالبحث عن الصور وتحديثها حتى لو كانت الخلية تحتوي على رابط سابق.
# إذا كان False، سيتم تخطي أي صف يحتوي بالفعل على رابط صورة لتوفير الموارد.
FORCE_OVERWRITE_IMAGES = False

# 8. إعدادات التحقق المتقدم للبحث والصور
# مفتاح API الخاص بـ Gemini (من Google AI Studio) للتحقق البصري المتقدم
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
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

# كونسول العمليات في الخلفية المشترك في الذاكرة
RUNNER_LOGS = []

def log_runner(*args):
    """
    تدوين رسالة مع التوقيت وعرضها في السجل الحي للوحة التحكم.
    """
    from datetime import datetime
    import builtins
    msg = " ".join(str(a) for a in args)
    time_str = datetime.now().strftime("%H:%M:%S")
    formatted = f"[{time_str}] {msg}"
    builtins.print(formatted)
    
    # الإضافة للقائمة المشتركة في الذاكرة مع تحديد حد أقصى 100 سطر لمنع تسرب الذاكرة
    RUNNER_LOGS.append(formatted)
    if len(RUNNER_LOGS) > 100:
        RUNNER_LOGS.pop(0)

def log_and_fail(barcode, product_name, brand, error_message):
    """
    تدوين الخطأ في الكونسول وتخزينه في جدول أخطاء SQLite.
    """
    log_runner(f"❌ فشل أتمتة المنتج '{product_name}': {error_message}")
    try:
        import local_cache_db
        local_cache_db.save_product_failure(barcode, product_name, brand, error_message)
    except Exception as e:
        import builtins
        builtins.print(f"⚠️ خطأ أثناء حفظ سجل الفشل: {e}")
