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
GOOGLE_SEARCH_API_KEY = os.getenv("GOOGLE_SEARCH_API_KEY", "")
GOOGLE_SEARCH_CX = os.getenv("GOOGLE_SEARCH_CX", "")

# إذا كانت بيانات Google غير متوفرة، سيقوم النظام تلقائياً بالاعتماد على DuckDuckGo للبحث
USE_FALLBACK_SEARCH = True

# 4. إعدادات معالجة الصور وتحجيمها
# الأبعاد الافتراضية المطلوبة لجميع الصور بشكل ديناميكي (مثال: 800×800)
IMAGE_TARGET_SIZE = (800, 800)

# خيار إزالة خلفية الصورة. الخيارات المتاحة:
# "none" -> تخطي إزالة الخلفية والقيام بالتحجيم فقط (مفيد للاختبار السريع)
# "rembg" -> استخدام مكتبة rembg المحلية المجانية تماماً (تتطلب تثبيت pip install rembg)
# "remove_bg_api" -> استخدام خدمة remove.bg السحابية (تتطلب إدخال مفتاح API أدناه)
BG_REMOVAL_METHOD = "rembg"

# مفتاح API الخاص بخدمة remove.bg (مطلوب فقط إذا اخترت "remove_bg_api")
REMOVE_BG_API_KEY = os.getenv("REMOVE_BG_API_KEY", "")

# 5. ملف اعتمادات Google Service Account
CREDENTIALS_FILE = os.getenv("CREDENTIALS_FILE", "credentials.json")

# 6. إعدادات Cloudinary
CLOUDINARY_CLOUD_NAME = os.getenv("CLOUDINARY_CLOUD_NAME", "")
CLOUDINARY_API_KEY = os.getenv("CLOUDINARY_API_KEY", "")
CLOUDINARY_API_SECRET = os.getenv("CLOUDINARY_API_SECRET", "")

# تفعيل إزالة الخلفية عبر الذكاء الاصطناعي لـ Cloudinary (يتطلب تفعيل الإضافة في حسابك)
CLOUDINARY_BG_REMOVAL = True

# الأبعاد المستهدفة سحابياً لإعادة الاحتواء وتوحيد الأبعاد (recontainment)
CLOUDINARY_TARGET_SIZE = (800, 800)

# الحد الأدنى لأبعاد الصورة المقبولة (لتجنب المصغرات والصور منخفضة الجودة)
MIN_IMAGE_WIDTH = 500
MIN_IMAGE_HEIGHT = 500

# إعدادات تحسين الجودة سحابياً عبر Cloudinary
CLOUDINARY_AUTO_QUALITY = True  # تفعيل الضغط والتحسين التلقائي للحجم (q_auto)
CLOUDINARY_AUTO_FORMAT = True   # تفعيل تحويل الصيغة التلقائي للأسرع للويب (f_auto)
CLOUDINARY_AI_ENHANCE = True    # تفعيل تحسين الألوان والتعريض والتباين الذكي (e_enhance)
CLOUDINARY_SHARPEN = 100        # قوة حدة الصورة (e_sharpen) لتوضيح التفاصيل (0 للإيقاف)
