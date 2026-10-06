# config.py
# ملف الإعدادات الخاص بنظام الأتمتة

import os
import logging

logger = logging.getLogger(__name__)

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
                    # متغيرات البيئة الفعلية (مثل DB_DATABASE في الاختبارات أو CI) لها الأولوية على ملف .env
                    os.environ.setdefault(key, val)

_load_env()

# 1. إعدادات Google Sheets
# يمكن وضع اسم الشيت أو الرابط الكامل له
SPREADSHEET_NAME_OR_URL = os.getenv("SPREADSHEET_NAME_OR_URL", "automation sheet")
SPREADSHEET_TAB_NAME = os.getenv("SPREADSHEET_TAB_NAME", "")


# 3. إعدادات البحث عن الصور (Google Image Search)
# للحصول على نتائج دقيقة ورسمية، أدخل بيانات Google Custom Search API أدناه.
# يدعم النظام إدخال عدة مفاتيح مفصولة بفاصلة (,) للتدوير التلقائي عند نفاد الحصة (Quota Rotation).
GOOGLE_SEARCH_API_KEYS = [k.strip() for k in os.getenv("GOOGLE_SEARCH_API_KEY", "").split(",") if k.strip()]
GOOGLE_SEARCH_CX_LIST = [c.strip() for c in os.getenv("GOOGLE_SEARCH_CX", "").split(",") if c.strip()]

GOOGLE_SEARCH_API_KEY = GOOGLE_SEARCH_API_KEYS[0] if GOOGLE_SEARCH_API_KEYS else ""
GOOGLE_SEARCH_CX = GOOGLE_SEARCH_CX_LIST[0] if GOOGLE_SEARCH_CX_LIST else ""

# محرك البحث: 'v2' (catalog_match، الافتراضي) أو 'v1' (المسار القديم للتراجع فقط لمدة 30 يوماً)
SEARCH_ENGINE = os.getenv("SEARCH_ENGINE", "v2").strip().lower() or "v2"

# مفتاح Serper.dev (Google Images عبر API، المصدر الأساسي للبحث في v2)
SERPER_API_KEY = os.getenv("SERPER_API_KEY", "")

# النشر التلقائي: معطل افتراضياً. يُفعّل فقط لبراندات محددة بعد أن تثبت مجموعة الاختبار الذهبية دقة >= 98%
AUTO_PUBLISH_ENABLED = os.getenv("AUTO_PUBLISH_ENABLED", "False").strip().lower() in ("1", "true", "yes", "on")
AUTO_PUBLISH_BRANDS = [b.strip() for b in os.getenv("AUTO_PUBLISH_BRANDS", "").split(",") if b.strip()]
# النشر الآلي لكل الماركات المؤكدة (فئة strict في catalog_match.decide): شغّال افتراضياً (موافقة المالك)، بس الفئة ما
# بتنشر شي لحالها قبل ما تثبت مراجعاتها دقتها (decide.strict_lane_readiness: 30 مراجعة عالأقل وحد ويلسون >= 98%).
# القيمة اللي بيحفظها المالك من الإعدادات (system_settings.auto_publish_strict_lane) بتغلب هالافتراضي
AUTO_PUBLISH_STRICT_LANE = os.getenv("AUTO_PUBLISH_STRICT_LANE", "True").strip().lower() in ("1", "true", "yes", "on")

# --- identity package (P3) --------------------------------------------------------------------
# مدى الثقة بالباركود: 'evidence' (الافتراضي: البراند والاسم هما الهوية والباركود دليل مساعد فقط)،
# 'strict' (القاعدة السابقة: أي باركود مختلف بصفحة المتجر يرفض المرشح)، 'off' (الباركود لا يُستخدم كدليل)
GTIN_POLICY = os.getenv("GTIN_POLICY", "evidence").strip().lower() or "evidence"


# --- sources package (P3): جولة البحث الموسّع (صفحات المتاجر، Google Shopping، البحث بالصورة) ---
# EXPANSION_ENABLED: جولة إضافية واحدة للمنتجات التي لم يُحدَّد لها اختيار واثق
# EXPANSION_MAX_CALLS: أقصى عدد استدعاءات مدفوعة في هذه الجولة لكل منتج
# VISUAL_SEARCH: auto (Serper lens ثم SerpApi إن وُجد مفتاحه) | off | serper | serpapi
# SERPAPI_API_KEY: سري؛ يُكتب فقط من حقل الإعدادات المخفي ولا يُطبع ولا يُسجَّل
EXPANSION_ENABLED = os.getenv("EXPANSION_ENABLED", "true").strip().lower() in ("1", "true", "yes", "on")
EXPANSION_MAX_CALLS = os.getenv("EXPANSION_MAX_CALLS", "4").strip() or "4"
VISUAL_SEARCH = os.getenv("VISUAL_SEARCH", "auto").strip().lower() or "auto"
SERPAPI_API_KEY = os.getenv("SERPAPI_API_KEY", "").strip()
SERPAPI_LENS_PRICE_USD = os.getenv("SERPAPI_LENS_PRICE_USD", "0.015").strip() or "0.015"
# الفهرس المحلي (catalog_match/local_index.py): صفحات منتجات المتاجر من خرائط مواقعها، يبنيه
# scripts/build_catalog_index.py. LOCAL_INDEX_MAX_PAGES: صفحات بتنقرا لكل منتج (مجاناً)، 0 = موقف.
# LOCAL_INDEX_PAGE_TTL_DAYS: كم يوم بينحفظ ما قالته الصفحة (الصورة والاسم والباركود) قبل ما تنقرا من جديد
LOCAL_INDEX_ENABLED = os.getenv("LOCAL_INDEX_ENABLED", "true").strip().lower() in ("1", "true", "yes", "on")
LOCAL_INDEX_MAX_PAGES = os.getenv("LOCAL_INDEX_MAX_PAGES", "3").strip() or "3"
LOCAL_INDEX_PAGE_TTL_DAYS = os.getenv("LOCAL_INDEX_PAGE_TTL_DAYS", "30").strip() or "30"
# LOCAL_INDEX_REFRESH_DAYS: متجر آخر جمع كامل له أقدم من هيك بينجمع من جديد بالخلفية أول التشغيل الليلي أو تشغيل العامل
# (catalog_match/index_refresh.py). LOCAL_INDEX_REFRESH_MAX_S: أقصى ثواني للتحديث الواحد، 0 = بلا تحديث تلقائي
LOCAL_INDEX_REFRESH_DAYS = os.getenv("LOCAL_INDEX_REFRESH_DAYS", "7").strip() or "7"
LOCAL_INDEX_REFRESH_MAX_S = os.getenv("LOCAL_INDEX_REFRESH_MAX_S", "300").strip() or "300"
# --- end sources package ---

# --- queue package (P4a): حدود الصرف للعامل ---
# DAILY_BUDGET_USD: أقصى صرف تقديري للبحث في اليوم (Serper / SerpApi / قراءة الملصق، بأسعار ops_health).
#   يُفحص قبل سحب كل منتج؛ عند بلوغه يتوقف التشغيل (BUDGET_REACHED) وتبقى الصفوف في الانتظار. 0 = بلا حد.
# SERPER_CREDIT_STOP_SEARCHES: يتوقف التشغيل بعد هذا العدد من عمليات البحث المتتالية التي رفض فيها Serper
#   كل استعلاماته بسبب الرصيد أو المفتاح (quota / 401 / 403)، وتبقى الصفوف في الانتظار. 0 = لا إيقاف.
def _number_env(name, default):
    try:
        return max(0.0, float(os.getenv(name, str(default)).strip() or default))
    except ValueError:
        logger.warning("قيمة %s غير صالحة؛ تُستخدم %s.", name, default)
        return float(default)


DAILY_BUDGET_USD = _number_env("DAILY_BUDGET_USD", 0)
SERPER_CREDIT_STOP_SEARCHES = int(_number_env("SERPER_CREDIT_STOP_SEARCHES", 3))
# --- end queue package ---

# --- speed package: سرعة العامل ---
# WORKER_CONCURRENCY: كم منتج بيشتغل بنفس الوقت (1 إلى 8، الافتراضي 5)؛ الأعلى أسرع لكن يصرف رصيد البحث أسرع.
#   من system_settings.worker_concurrency (لوحة التحكم، تبويب «متقدم»)، وإلا من .env.
# SERPER_HEDGE_AFTER_S: طلب Serper ما رد خلال هالمدة بيُرسل مرة تانية ويُستخدم أول رد سليم (0 = موقف). الطلب الثاني
#   رصيد إضافي من Serper ومسجل بالمكالمة (hedges).
WORKER_CONCURRENCY = os.getenv("WORKER_CONCURRENCY", "5").strip() or "5"
SERPER_HEDGE_AFTER_S = os.getenv("SERPER_HEDGE_AFTER_S", "4.5").strip() or "4.5"
# --- end speed package ---

# 4. إعدادات معالجة الصور وتحجيمها
# الأبعاد الافتراضية المطلوبة لجميع الصور بشكل ديناميكي (مثال: 800×800)
IMAGE_TARGET_SIZE = (800, 800)

# أبعاد لوحة النشر النهائية (مربع أبيض معتم) عند طلب 0 أو 'dynamic'
try:
    OUTPUT_CANVAS_SIZE = int(os.getenv("OUTPUT_CANVAS_SIZE", "800"))
except ValueError:
    OUTPUT_CANVAS_SIZE = 800

# خلفية لوحة النشر: 'transparent' (الافتراضي: PNG شفافة، التطبيق بيعرضها على الوضع الغامق والفاتح) أو 'white'
# (اللوحة البيضا المعتمة القديمة). تتجاوزها صفحة الإعدادات (system_settings.output_background).
OUTPUT_BACKGROUNDS = ("transparent", "white")
OUTPUT_BACKGROUND = (os.getenv("OUTPUT_BACKGROUND", "transparent") or "").strip().lower()
if OUTPUT_BACKGROUND not in OUTPUT_BACKGROUNDS:
    OUTPUT_BACKGROUND = "transparent"

# خيار إزالة خلفية الصورة. الخيارات المتاحة:
# "none" -> تخطي إزالة الخلفية والقيام بالتحجيم فقط (مفيد للاختبار السريع)
# "bria_rmbg" -> استخدام نموذج Bria RMBG 1.4 المحلي المجاني وفائق الدقة (مستحسن)
# "rembg" -> استخدام مكتبة rembg المحلية المجانية تماماً (تتطلب تثبيت pip install rembg)
# "remove_bg_api" -> استخدام خدمة remove.bg السحابية (تتطلب إدخال مفتاح API أدناه)
# "photoroom" -> استخدام خدمة PhotoRoom السحابية لإزالة الخلفية مع القص التلقائي الاحترافي للهوامش
BG_REMOVAL_METHOD = os.getenv("BG_REMOVAL_METHOD", "photoroom")
# الطرق التي تقبلها صفحة الإعدادات من system_settings.bg_removal_method (نفس main.SUPPORTED_BG_METHODS)
BG_REMOVAL_METHODS = ("photoroom", "remove_bg_api", "grabcut", "rembg", "none")
# لما تفشل طريقة العزل السحابية بسبب الرصيد أو المفتاح أو الحصة (image_processor، publish_check.BG_SKIP_CODE_RE):
# "local" (الافتراضي) تعزل نفس الصورة بـ rembg المنزّلة (موديل REMBG_MODEL، بدون GrabCut أبداً) وتمرّرها على بوابة القص
# نفسها، و"off" السلوك القديم (الفشل يبقى فشلاً). «بدون عزل الخلفية» لا تُختار تلقائياً أبداً: هي زر المالك.
BG_FALLBACK = os.getenv("BG_FALLBACK", "local")
BG_FALLBACKS = ("local", "off")   # نفس system_settings.bg_fallback
# موديل rembg للبديل المحلي التلقائي (image_processor): BiRefNet يحافظ على العلب البيضا (كرتونة الحليب) بعكس u2net/isnet.
# "birefnet-general" (الافتراضي) أو "birefnet-general-lite" (أخف وأسرع)؛ لازم يكون منزّلاً على الجهاز (rembg d <الموديل>)
REMBG_MODEL = os.getenv("REMBG_MODEL", "birefnet-general")
REMBG_MODELS = ("birefnet-general", "birefnet-general-lite")   # نفس system_settings.rembg_model

# مفتاح API الخاص بخدمة remove.bg (مطلوب فقط إذا اخترت "remove_bg_api")
REMOVE_BG_API_KEY = os.getenv("REMOVE_BG_API_KEY", "")

# مفتاح API الخاص بخدمة PhotoRoom (مطلوب فقط إذا اخترت "photoroom")
PHOTOROOM_API_KEY = os.getenv("PHOTOROOM_API_KEY", "")

# إعدادات واجهة برمجة تطبيقات إزالة الخلفية لـ PhotoRoom (v1/segment API)
PHOTOROOM_SIZE = "full"       # دقة الصورة المستردة: preview, medium, hd, full
# قص PhotoRoom للهوامش الشفافة: مطفأ افتراضياً. اللوحة النهائية تقص المنتج وتوسّطه محلياً على أي حال،
# والإطار الكامل يسمح لبوابة الجودة بكشف منتج قصه صندوق Gemini (مع القص يلمس كل منتج الحواف فلا يُكشف شيء)
PHOTOROOM_CROP = os.getenv("PHOTOROOM_CROP", "False").lower() == "true"
PHOTOROOM_DESPILL = True      # تفعيل تقنية تصحيح الحواف وإزالة تسرب الألوان من الخلفية الأصلية (Chroma key)

# صور المصدر بخلفية بيضاء نظيفة (image_processor._white_source_cutout): قص محلي مجاني بدل المزوّد المدفوع.
# 'off' = لا فحص | 'log' (افتراضي) = يكشف ويسجل ما كان سيفعله في نتيجة المعالجة لكن يبقى المزوّد المدفوع
# | 'on' = يستخدم القص المحلي (بعد اجتيازه بوابة الجودة) دون PhotoRoom ولا صندوق Gemini
WHITE_SOURCE_MODE = os.getenv("WHITE_SOURCE_MODE", "log").strip().lower()

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
CLOUDINARY_AUTO_FORMAT = True   # قديم وما إله أثر: التسليم WebP ثابت (f_webp، delivery_urls) لأن f_auto بيعطي التطبيق JPEG بلا شفافية
CLOUDINARY_AI_ENHANCE = False    # إيقاف تحسين الألوان السحابي التلقائي لمنع التشويه والألوان الفاقعة
CLOUDINARY_SHARPEN = 20          # قوة حدة الصورة سحابياً (0 للإيقاف، تم استخدام 20 لإبراز تفاصيل النصوص دون التسبب بتشويه)
CLOUDINARY_TRIM_TOLERANCE = 5  # سماحية الاقتصاص لـ Cloudinary لمنع قص حواف المنتجات اللامعة أو الدائرية (0-100)

# إعدادات الظلال والتجاوز الذكي للخلفيات البيضاء المجهزة مسبقاً
ENABLE_STUDIO_SHADOWS = False    # تعطيل ظلال الاستوديو لتلبية طلب العميل بعدم وجود ظلال
BYPASS_WHITE_BACKGROUND_CHECK = os.getenv("BYPASS_WHITE_BACKGROUND_CHECK", "False").lower() == "true"  # تخطي إزالة الخلفية والقص إذا كانت الصورة الأصلية بالفعل بخلفية بيضاء نقية وجودة عالية
WHITE_BACKGROUND_THRESHOLD = 0.96      # النسبة المقبولة للبكسلات البيضاء على إطار الصورة (96%) للاعتبار كخلفية بيضاء
ENABLE_IMAGE_ENHANCEMENT = False       # تعطيل تحسين/تنعيم الألوان والصور الذكائي الافتراضي لمنع بهتان الألوان وجعلها اختيارية


# 7. إعدادات تخطي أو استبدال الصور
# إذا كان True، سيقوم النظام بالبحث عن الصور وتحديثها حتى لو كانت الخلية تحتوي على رابط نهائي سابق
# (ويعيد معالجة الصفوف الجاهزة للمراجعة أو المكتملة في الطابور).
# الافتراضي False: لا نعيد البحث عن صف له رابط نهائي ولا نمسح اختيارات المراجعين.
FORCE_OVERWRITE_IMAGES = os.getenv("FORCE_OVERWRITE_IMAGES", "False").strip().lower() in ("1", "true", "yes", "on")

# وضع المراجعة والاعتماد اليدوي (Curation Mode).
# إذا كان True، فسيتم إرسال روابط الصور المكتشفة إلى الشيت مع بادئة مراجعة 'needs_review:' دون استهلاك رصيد PhotoRoom.
# بعد ذلك، يعتمد المستخدم الصورة المناسبة من لوحة التحكم، وسيتم إرسالها لـ PhotoRoom لإزالة الخلفية وتحديث الشيت مباشرة.
CURATION_MODE = os.getenv("CURATION_MODE", "True").lower() == "true"

# 8. إعدادات التحقق المتقدم للبحث والصور
# مفتاح API الخاص بـ Gemini (من Google AI Studio) للتحقق البصري المتقدم
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")

# النماذج المحلية (CLIP, SigLIP, BLIP, Moondream2, DINOv2) أزيلت من كل مسارات القرار (D6).
# هذا الثابت يبقى فقط لأن مسار التراجع v1 في image_search.py ما زال يقرؤه؛ لم يعد قابلاً للضبط.
DISABLE_LOCAL_AI_MODELS = True

ENABLE_GEMINI_VISION = True
ENABLE_LOCAL_OCR = False
# مطابقة البراند الصارمة (D9): المرشح الذي يحمل براند منافس معروف دون البراند المطلوب يُرفض
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

USE_SIGLIP_SEMANTIC_CHECK = True  # يقرؤه مسار v1 فقط؛ بدون نموذج محلي تعود الدرجة None
USE_BLIP_CAPTION_CHECK = True
USE_MOONDREAM_CHECK = False  # يمكن تفعيله يدوياً لتشغيل Moondream2 في الفرز الحتمي النهائي

# نطاقات المواقع الإماراتية الموثوقة للتجارة الإلكترونية لتحديد نطاق البحث المبدئي
TRUSTED_UAE_DOMAINS = ["kibsons.com", "carrefouruae.com", "luluhypermarket.com", "noon.com", "amazon.ae"]

# 9. إعدادات الترقيات المتقدمة (البروكسي والتنبيهات)
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
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
    # كل طباعة main.py تمر من هنا (print = config.log_runner): نص استثناء قد يحمل مفتاحاً أو رابطاً بمفتاحه
    msg = redact(" ".join(str(a) for a in args))
    time_str = datetime.now().strftime("%H:%M:%S")
    formatted = f"[{time_str}] {msg}"
    builtins.print(formatted)
    
    # الإضافة للقائمة المشتركة في الذاكرة مع تحديد حد أقصى 100 سطر لمنع تسرب الذاكرة
    RUNNER_LOGS.append(formatted)
    if len(RUNNER_LOGS) > 100:
        RUNNER_LOGS.pop(0)

    # بث السطر كما هو لـ Redis Pub/Sub (إن وُجد) دون أي مؤشرات مختلقة
    if _redis_available is not False:
        try:
            import redis
            # مهلة منخفضة جداً (0.2 ثانية) للفحص السريع لمنع تعليق الكونسول
            r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=REDIS_DB, socket_timeout=0.2)
            payload = {"timestamp": datetime.now().timestamp(), "log": formatted}
            r.publish("pipeline_log", json.dumps(payload, ensure_ascii=False))
            _redis_available = True
        except Exception:
            _redis_available = False
            logger.debug("Redis غير متصل محلياً؛ تم إيقاف بث السجل المباشر.")

def redact(text):
    """النص بلا أسرار (run_report.redact: قيم المفاتيح، key= في الروابط، كلمة مرور قاعدة البيانات). لا يرفع أبداً."""
    text = "" if text is None else str(text)
    try:
        import run_report       # عند الاستدعاء: run_report -> verify_cloud_services -> config
        return run_report.redact(text)
    except Exception:
        import re
        return re.sub(r"(?i)((?:api_?)?key=)[^&\s'\"]+", r"\1[REDACTED]", text)


# ملف سجلات لارافيل (صفحة الأعطال تقرؤه)، بمسار نسبي لمجلد المشروع
LARAVEL_LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard", "storage", "logs", "laravel.log")


def log_error_to_laravel(error_message, barcode=None, product_name=None, brand=None, level="ERROR"):
    """
    تدوين رسائل الأخطاء وتفاصيلها مباشرة في ملف سجلات لارافيل `dashboard/storage/logs/laravel.log`.
    السطر يمر على redact: نصوص الاستثناءات قد تحمل رابطاً بمفتاحه أو قيمة مفتاح.
    """
    import threading
    from datetime import datetime
    
    log_file_path = LARAVEL_LOG_PATH
    
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
    formatted_log = redact(f"[{time_str}] local.{level}: Python Pipeline{context_str}: {error_message}") + "\n"
    
    # استخدام قفل محلي لحماية الكتابة المتزامنة في نفس العملية
    if not hasattr(log_error_to_laravel, "_lock"):
        log_error_to_laravel._lock = threading.Lock()
        
    with log_error_to_laravel._lock:
        try:
            with open(log_file_path, "a", encoding="utf-8") as f:
                f.write(formatted_log)
        except Exception as e:
            logger.warning("فشل الكتابة في ملف سجلات لارافيل: %s", e)

def _telegram_credentials():
    return (os.getenv("TELEGRAM_BOT_TOKEN", TELEGRAM_BOT_TOKEN) or "").strip(), \
        (os.getenv("TELEGRAM_CHAT_ID", TELEGRAM_CHAT_ID) or "").strip()


def telegram_configured():
    """هل ضُبط بوت Telegram (TELEGRAM_BOT_TOKEN و TELEGRAM_CHAT_ID)؟ تقرير كل تشغيل يُرسل فقط عندها."""
    token, chat_id = _telegram_credentials()
    return bool(token and chat_id)


def send_telegram_alert(message):
    """
    إرسال إشعار فوري عبر بوت Telegram للمشرف. تعيد True فقط إذا قبل Telegram الرسالة (HTTP 2xx)؛
    لا يُطبع المفتاح أبداً (الرابط يحتويه).
    """
    token, chat_id = _telegram_credentials()
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
        response = requests.post(url, json=payload, timeout=10)
        if 200 <= int(getattr(response, "status_code", 0) or 0) < 300:
            return True
        logger.warning("Telegram رفض الرسالة (HTTP %s).", getattr(response, "status_code", "?"))
        return False
    except Exception as e:
        logger.warning("تعذر إرسال رسالة Telegram (%s).", type(e).__name__)
        return False

def log_and_fail(barcode, product_name, brand, error_message):
    """
    تدوين الخطأ في الكونسول وتخزينه في جدول أخطاء SQLite. النص يمر على redact قبل أي مكان (السجل، الجدول، Telegram)،
    ورسالة Telegram (parse_mode HTML) تُهرَّب قيمها: اسم منتج أو خطأ فيه < أو & كان يكسر الرسالة أو يغيّر تنسيقها.
    """
    import html
    error_message = redact(error_message)
    log_runner(f"❌ فشل أتمتة المنتج '{product_name}': {error_message}")
    
    # تدوين الفشل في ملف سجلات لارافيل
    log_error_to_laravel(error_message, barcode=barcode, product_name=product_name, brand=brand, level="ERROR")
    
    try:
        import local_cache_db
        local_cache_db.save_product_failure(barcode, product_name, brand, error_message)
    except Exception as e:
        logger.warning("خطأ أثناء حفظ سجل الفشل: %s", e)

    # إرسال إشعار تليجرام في حال وجود أخطاء متعلقة بالاشتراكات أو الحصص أو الـ APIs
    lower_err = error_message.lower()
    quota_keywords = ["quota", "limit", "402", "429", "unauthorized", "api_key", "expired", "exhausted", "billing", "payment", "credentials", "connection failed"]
    if any(k in lower_err for k in quota_keywords):
        alert_msg = (
            f"🚨 <b>تنبيه خطأ أتمتة حرج (Subscription/API Error)</b>\n\n"
            f"📦 <b>المنتج:</b> {html.escape(str(product_name or ''))}\n"
            f"🏷️ <b>الماركة:</b> {html.escape(str(brand or ''))}\n"
            f"🔢 <b>الباركود:</b> {html.escape(str(barcode or 'N/A'))}\n"
            f"❌ <b>الخطأ المكتشف:</b> <code>{html.escape(error_message)}</code>\n\n"
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

# ---------------------------------------------------------------------------
# حزمة المحقق (verifier, P3): نماذج قراءة الملصق، والنموذج القوي وميزانيته الشهرية (catalog_match.verifiers).
# المعرّف "<provider>:<model>" حيث provider هو gemini أو claude. المفتاح السري لا يُطبع ولا يُسجَّل أبداً.
# ---------------------------------------------------------------------------
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
VERIFIER_PRIMARY = os.getenv("VERIFIER_PRIMARY", "")                  # فارغ = "gemini:<GEMINI_MODEL>"
VERIFIER_STRONG = os.getenv("VERIFIER_STRONG", "gemini:gemini-3.5-flash")   # أو "claude:<model>" أو "off"
VERIFIER_MONTHLY_BUDGET_USD = os.getenv("VERIFIER_MONTHLY_BUDGET_USD", "5")
VERIFIER_STRONG_MAX_CALLS = os.getenv("VERIFIER_STRONG_MAX_CALLS", "1")
# إعادة حكم واحدة بالنموذج القوي لكل منتج على MISMATCH سببه الوحيد variant/size (1 = شغّال، 0 = موقّف، الحد 1)
VERIFIER_REJUDGE_MAX_CALLS = os.getenv("VERIFIER_REJUDGE_MAX_CALLS", "1")
MODEL_PRICES = os.getenv("MODEL_PRICES", "")                         # JSON: دولار لكل مليون token (إدخال/إخراج)
# قارئ أسماء الشيت المختصرة (catalog_match.normalizer): gemini أو off. يكتب كلمات بحث أفضل فقط، ولا يُعدّ دليلاً أبداً
QUERY_NORMALIZER = os.getenv("QUERY_NORMALIZER", "gemini")
QUERY_NORMALIZER_RUN_BUDGET_USD = os.getenv("QUERY_NORMALIZER_RUN_BUDGET_USD", "0.5")   # سقف تكلفته لكل تشغيل

# مفاتيح system_settings التي تكتبها صفحة الإعدادات -> اسم الإعداد هنا
VERIFIER_DB_KEYS = {
    "anthropic_api_key": "ANTHROPIC_API_KEY",
    "verifier_primary": "VERIFIER_PRIMARY",
    "verifier_strong": "VERIFIER_STRONG",
    "verifier_monthly_budget_usd": "VERIFIER_MONTHLY_BUDGET_USD",
    "verifier_strong_max_calls": "VERIFIER_STRONG_MAX_CALLS",
    "verifier_rejudge_max_calls": "VERIFIER_REJUDGE_MAX_CALLS",
    "model_prices": "MODEL_PRICES",
    "query_normalizer": "QUERY_NORMALIZER",
    "query_normalizer_run_budget_usd": "QUERY_NORMALIZER_RUN_BUDGET_USD",
}


def _apply_verifier_settings(db_keys):
    """قيم حزمة المحقق من system_settings (قيمة فارغة تُبقي قيمة .env). يُستدعى من load_db_config."""
    for db_key, name in VERIFIER_DB_KEYS.items():
        value = db_keys.get(db_key)
        if value is None or str(value).strip() == "":
            continue
        globals()[name] = str(value).strip()



# --- sources package (P3): قيم جولة البحث الموسّع من system_settings (تتجاوز .env) ---
def _load_sources_settings(db_keys):
    global EXPANSION_ENABLED, EXPANSION_MAX_CALLS, VISUAL_SEARCH, SERPAPI_API_KEY, SERPAPI_LENS_PRICE_USD
    if "expansion_enabled" in db_keys and db_keys["expansion_enabled"] is not None:
        EXPANSION_ENABLED = str(db_keys["expansion_enabled"]).strip().lower() in ("1", "true", "yes", "on")
    if db_keys.get("expansion_max_calls") not in (None, ""):
        try:
            EXPANSION_MAX_CALLS = str(max(0, int(str(db_keys["expansion_max_calls"]).strip())))
        except (TypeError, ValueError):
            logger.warning("قيمة expansion_max_calls غير صالحة: %r", db_keys["expansion_max_calls"])
    if db_keys.get("visual_search"):
        mode = str(db_keys["visual_search"]).strip().lower()
        if mode in ("auto", "off", "serper", "serpapi"):
            VISUAL_SEARCH = mode
        else:
            logger.warning("قيمة visual_search غير مدعومة: %r", db_keys["visual_search"])
    if db_keys.get("serpapi_api_key"):
        SERPAPI_API_KEY = str(db_keys["serpapi_api_key"]).strip()   # لا يُطبع أبداً
    if db_keys.get("serpapi_lens_price_usd"):
        SERPAPI_LENS_PRICE_USD = str(db_keys["serpapi_lens_price_usd"]).strip()
    global LOCAL_INDEX_ENABLED, LOCAL_INDEX_MAX_PAGES
    if "local_index_enabled" in db_keys and db_keys["local_index_enabled"] is not None:
        LOCAL_INDEX_ENABLED = str(db_keys["local_index_enabled"]).strip().lower() in ("1", "true", "yes", "on")
    if db_keys.get("local_index_max_pages") not in (None, ""):
        try:
            LOCAL_INDEX_MAX_PAGES = str(max(0, int(str(db_keys["local_index_max_pages"]).strip())))
        except (TypeError, ValueError):
            logger.warning("قيمة local_index_max_pages غير صالحة: %r", db_keys["local_index_max_pages"])
# --- end sources package ---


# --- queue package (P4a): حدود الصرف من system_settings (daily_budget_usd، serper_credit_stop_searches) ---
def _load_queue_settings(db_keys):
    global DAILY_BUDGET_USD, SERPER_CREDIT_STOP_SEARCHES
    for key, name in (("daily_budget_usd", "DAILY_BUDGET_USD"), ("serper_credit_stop_searches",
                                                                 "SERPER_CREDIT_STOP_SEARCHES")):
        value = db_keys.get(key)
        if value is None or str(value).strip() == "":
            continue
        try:
            number = max(0.0, float(str(value).strip()))
        except ValueError:
            logger.warning("قيمة %s غير صالحة: %r", key, value)
            continue
        globals()[name] = int(number) if name == "SERPER_CREDIT_STOP_SEARCHES" else number
    global WORKER_CONCURRENCY
    if db_keys.get("worker_concurrency") not in (None, ""):
        try:
            WORKER_CONCURRENCY = str(min(8, max(1, int(str(db_keys["worker_concurrency"]).strip()))))
        except (TypeError, ValueError):
            logger.warning("قيمة worker_concurrency غير صالحة: %r", db_keys["worker_concurrency"])
# --- end queue package ---


def load_db_config():
    """
    تحميل الإعدادات ديناميكياً من قاعدة البيانات لتجنب تعديل ملفات البيئة يدوياً.
    """
    try:
        # اتصال المشروع الموحد بإعدادات DB_* ومهل الاتصال والقراءة والكتابة
        import db_connect
        conn = db_connect.connect()
        cursor = conn.cursor()
        cursor.execute("SHOW TABLES LIKE 'system_settings'")
        if cursor.fetchone():
            cursor.execute("SELECT `key`, `value` FROM system_settings")
            rows = cursor.fetchall()
            db_keys = {}
            for r in rows:
                db_keys[r['key']] = r['value']
            
            global PHOTOROOM_API_KEY, GEMINI_API_KEY, GEMINI_MODEL, PHOTOROOM_CROP
            global CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, CLOUDINARY_API_SECRET
            global GOOGLE_SEARCH_API_KEYS, GOOGLE_SEARCH_CX_LIST, GOOGLE_SEARCH_API_KEY, GOOGLE_SEARCH_CX
            global CLIP_RELEVANCE_THRESHOLD, CLIP_GREY_ZONE_THRESHOLD, STRICT_BRAND_MATCH, ENABLE_GEMINI_PRE_VALIDATION, FILTER_COMPETITORS, BYPASS_WHITE_BACKGROUND_CHECK, PROXY_URL
            global SEARCH_ENGINE, SERPER_API_KEY, AUTO_PUBLISH_ENABLED, AUTO_PUBLISH_BRANDS, OUTPUT_CANVAS_SIZE
            global OUTPUT_BACKGROUND
            global AUTO_PUBLISH_STRICT_LANE
            global BG_REMOVAL_METHOD, ENABLE_IMAGE_ENHANCEMENT, BG_FALLBACK, REMBG_MODEL

            if "photoroom_api_key" in db_keys and db_keys["photoroom_api_key"]:
                PHOTOROOM_API_KEY = db_keys["photoroom_api_key"]
            if "photoroom_crop" in db_keys:
                PHOTOROOM_CROP = db_keys["photoroom_crop"].lower() == "true"
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
            # إعدادات محرك البحث v2 والنشر التلقائي (D1/D7/D10/D14)
            if db_keys.get("search_engine"):
                SEARCH_ENGINE = str(db_keys["search_engine"]).strip().lower()
            if db_keys.get("serper_api_key"):
                SERPER_API_KEY = str(db_keys["serper_api_key"]).strip()
            if "auto_publish_enabled" in db_keys and db_keys["auto_publish_enabled"] is not None:
                AUTO_PUBLISH_ENABLED = str(db_keys["auto_publish_enabled"]).strip().lower() in ("1", "true", "yes", "on")
            if "auto_publish_brands" in db_keys and db_keys["auto_publish_brands"] is not None:
                AUTO_PUBLISH_BRANDS = [b.strip() for b in str(db_keys["auto_publish_brands"]).split(",") if b.strip()]
            # خلية فاضية = ما حفظ المالك شي: الافتراضي (شغّال) بيضل
            if str(db_keys.get("auto_publish_strict_lane") or "").strip():
                AUTO_PUBLISH_STRICT_LANE = str(db_keys["auto_publish_strict_lane"]).strip().lower() in ("1", "true", "yes", "on")
            if db_keys.get("output_canvas_size"):
                try:
                    OUTPUT_CANVAS_SIZE = int(db_keys["output_canvas_size"])
                except (TypeError, ValueError):
                    logger.warning("قيمة output_canvas_size غير صالحة: %r", db_keys["output_canvas_size"])
            if db_keys.get("output_background"):
                background = str(db_keys["output_background"]).strip().lower()
                if background in OUTPUT_BACKGROUNDS:
                    OUTPUT_BACKGROUND = background
                else:
                    logger.warning("قيمة output_background غير مدعومة: %r", db_keys["output_background"])
            # معالجة الصور من صفحة الإعدادات (تبويب «معالجة الصور»)؛ run_config.json للتشغيل يبقى أعلى أولوية
            if db_keys.get("bg_removal_method"):
                method = str(db_keys["bg_removal_method"]).strip().lower()
                if method in BG_REMOVAL_METHODS:
                    BG_REMOVAL_METHOD = method
                else:
                    logger.warning("قيمة bg_removal_method غير مدعومة: %r", db_keys["bg_removal_method"])
            if db_keys.get("bg_fallback"):
                fallback = str(db_keys["bg_fallback"]).strip().lower()
                if fallback in BG_FALLBACKS:
                    BG_FALLBACK = fallback
                else:
                    logger.warning("قيمة bg_fallback غير مدعومة: %r", db_keys["bg_fallback"])
            if db_keys.get("rembg_model"):
                rembg_model = str(db_keys["rembg_model"]).strip().lower()
                if rembg_model in REMBG_MODELS:
                    REMBG_MODEL = rembg_model
                else:
                    logger.warning("قيمة rembg_model غير مدعومة: %r", db_keys["rembg_model"])
            if "enable_image_enhancement" in db_keys and db_keys["enable_image_enhancement"] is not None:
                ENABLE_IMAGE_ENHANCEMENT = str(db_keys["enable_image_enhancement"]).strip().lower() in (
                    "1", "true", "yes", "on")
            _apply_verifier_settings(db_keys)   # حزمة المحقق (verifier, P3)

            # --- identity package (P3): سياسة الباركود (gtin_policy) ---
            if db_keys.get("gtin_policy"):
                policy = str(db_keys["gtin_policy"]).strip().lower()
                if policy in ("evidence", "strict", "off"):
                    globals()["GTIN_POLICY"] = policy
                else:
                    logger.warning("قيمة gtin_policy غير مدعومة: %r", db_keys["gtin_policy"])

            # --- embeddings package: نموذج المتجهات (catalog_match.embeddings)؛ يكتبه
            # scripts/backfill_embeddings.py --setup (install.sh --with-embeddings) ---
            if db_keys.get("embeddings"):
                mode = str(db_keys["embeddings"]).strip().lower()
                if mode in ("off", "dinov2", "siglip2"):
                    globals()["EMBEDDINGS"] = mode
                else:
                    logger.warning("قيمة embeddings غير مدعومة: %r", db_keys["embeddings"])


            _load_sources_settings(db_keys)   # sources package (P3)
            _load_queue_settings(db_keys)     # queue package (P4a)

            logger.info("[Config Loader] تم تحميل الإعدادات من قاعدة البيانات (تتجاوز قيم .env).")
        conn.close()
    except Exception as e:
        logger.warning("[Config Loader] تعذر تحميل الإعدادات من قاعدة البيانات (قد لا تكون مهيأة بعد): %s", e)

load_db_config()


