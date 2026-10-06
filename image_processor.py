# image_processor.py
# معالجة صورة المنتج المعتمدة وتحويلها إلى لوحة نشر نهائية:
# تحميل (أو قراءة من مخزن المرشحات) -> تصحيح اتجاه EXIF -> اقتصاص Gemini اختياري ومتحقق منه
# -> عزل الخلفية بالطريقة الممررة كمعامل -> تركيب على لوحة بيضاء معتمة ثابتة الأبعاد (افتراضياً 800x800).
#
# المبادئ الملزمة:
# - فشل عزل الخلفية يعيد isolated=False مع رمز خطأ، ولا يعيد الصورة الخام أبداً كأنها نجاح.
# - كل قص يمر ببوابة جودة (assess_cutout)؛ عند علامة نعيد المحاولة بما يمكن أن يصلحها فقط (جدول _FLAG_REMEDY:
#   بدون صندوق Gemini، ثم المزوّد المدفوع الآخر المهيأ)، وإذا بقيت العلامة تعود اللوحة isolated=False مع
#   quality_flags (للمراجعة، لا نشر تلقائي). ملاحظات لا تمنع النشر تعود في quality_notes.
# - لا يتم تعديل config.BG_REMOVAL_METHOD إطلاقاً (الطريقة تمرر كمعامل).
# - كل المعالجة في الذاكرة؛ الملف الوحيد المكتوب هو اللوحة النهائية باسم uuid داخل مجلد tempfile.mkdtemp().
# - لا يوجد اقتصاص مربع تلقائي، ولا فحص ضبابية، ولا ملء للثقوب، ولا مسح للأطراف.

import base64
import glob
import hashlib
import io
import json
import logging
import os
import re
import shutil
import tempfile
import threading
import time
import types
import uuid
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

import requests
from PIL import Image, ImageEnhance, ImageOps

import categories
import config
import cutout_finish
from catalog_match import cassette, settings
from edge_shadow_engine import (
    CANVAS_FILL_RATIO,
    HAZE_ALPHA_MAX,
    EdgeShadowEngine,
    alpha_bbox,
    compose_on_white_canvas,
)

logger = logging.getLogger(__name__)

# حدود المصادر
MAX_DOWNLOAD_BYTES = 15 * 1024 * 1024
MAX_LOCAL_BYTES = 40 * 1024 * 1024
# أقصى ضلع لصورة العمل المرسلة لمزوّد العزل (المخرج النهائي 800 افتراضياً فلا فائدة من أكبر)
MAX_WORK_SIDE = 3000
MAX_CANVAS_SIDE = 4000
MIN_CANVAS_SIDE = 64
# صندوق Gemini يُقبل فقط إذا غطى بين 5% و100% من الصورة
BOX_MIN_AREA_FRACTION = 0.05
BOX_MARGIN_FRACTION = 0.06
# الصورة تعتبر معزولة مسبقاً فقط إذا كان أكثر من 5% من بكسلات إطارها شفافاً
TRANSPARENT_BORDER_MIN_RATIO = 0.05
TRANSPARENT_ALPHA_MAX = 32
METADATA_IMAGE_SIDE = 1024

PHOTOROOM_URL = "https://sdk.photoroom.com/v1/segment"
PHOTOROOM_TIMEOUT = 30
REMOVE_BG_URL = "https://api.remove.bg/v1.0/removebg"
REMOVE_BG_TIMEOUT = 30
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

# بوابة جودة القص (assess_cutout)
SOLID_ALPHA = 128               # بكسل "صلب" من المنتج
OPAQUE_FILL_MAX = 0.97          # أكثر من 97% من الإطار معتم: المزوّد لم يزل شيئاً...
OPAQUE_FILL_BAND = 0.02         # ...إذا كان شريط الإطار (2%، و3 بكسل على الأقل) خلفية موحدة محايدة اللون،
OPAQUE_FILL_PRODUCT_MAX = 0.85  # أو موحدة ملونة وصندوق Gemini أقل من 85% من الإطار (إطار القص بهامشه ~80%)
EDGE_TOUCH_MIN = 0.02           # المنتج يغطي أكثر من 2% من خط قص داخلي: الصندوق قص جزءاً منه
EDGE_RUN_MIN_PX = 3             # أو جزء رفيع (شفاطة) عرضه 3 بكسل يلمس الخط ويمتد 3 بكسل للداخل
HAZE_GROWTH_MAX = 0.08          # البكسلات شبه الشفافة توسّع حدود المنتج بأكثر من 8% (و4 بكسل على الأقل)
HAZE_GROWTH_MIN_PX = 4
# ظل مرئي بقي مع المنتج: البكسلات المرئية (شفافية > 32) تتجاوز حدود الجزء الصلب (>= 128) بأكثر من 3% (و4 بكسل)،
# وأغلب هذا الامتداد رمادي داكن (لون الظل)؛ جسم عبوة شفافة يمتد بنفس الطريقة لكنه فاتح أو ملون
SHADOW_GROWTH_MAX = 0.03
SHADOW_VALUE_MAX = 170
SHADOW_CHROMA_MAX = 40
SHADOW_SHARE_MIN = 0.5
SECOND_OBJECT_MIN = 0.01        # الأجسام الأخرى معاً (غير المنتج ومجموعته) 1% أو أكثر من الجسم الرئيسي
GROUP_ALPHA = 24                # الأجزاء المتصلة عبر شفافية > 24 جسم واحد (جسم عبوة شفافة يصل الغطاء بالملصق)
GROUP_AREA_MIN = 0.40           # عبوة متعددة: كل قطعة 40% على الأقل من الأكبر وبارتفاع مماثل (80%) = منتج واحد
GROUP_HEIGHT_MIN = 0.80
MAX_UPSCALE = 3.0               # تكبير المنتج على اللوحة أكثر من 3 أضعاف: علامة تمنع النشر التلقائي
UPSCALE_NOTE_MIN = 2.0          # بين ضعفين و3 أضعاف: ينشر مع ملاحظة (quality_notes) يراها المراجع والتقرير
MIN_MAIN_EXTENT = 0.80          # مجموعة المنتج تشغل أقل من 80% من مساحة الإشغال المتاحة
FRAME_ASPECT_TOLERANCE = 0.02   # مخرج المزوّد بنفس نسبة أبعاد الإطار المرسل (لم يقصه المزوّد)
# خلفية معتمة بقيت حول المنتج (ورقة/صندوق تصوير رمادي في PNG شفاف أو في مخرج المزوّد)
BACKDROP_RECT_MIN = 0.95
BACKDROP_TOLERANCE = 16
BACKDROP_RING_UNIFORM = 0.95
BACKDROP_OBJECT_DIFF = 48
BACKDROP_OBJECT_MIN = 0.05
BACKDROP_CHROMA_MAX = 40        # لون الحافة محايد (رمادي/أبيض/أسود)؛ واجهة علبة مطبوعة بلون مشبع ليست خلفية
BACKDROP_COVER_MIN = 0.50       # ولون الحافة نفسه يغطي نصف القناع على الأقل
# مقارنة مخرج المزوّد بمنطقة المنتج المعروفة: شفافية المصدر نفسها، أو صندوق Gemini
SOURCE_MATCH_IOU = 0.95
BOX_MATCH_IOU = 0.85

FLAG_OPAQUE_FILL = "opaque_fill"
FLAG_EDGE_CLIPPED = "edge_clipped"
FLAG_ALPHA_HAZE = "alpha_haze"
FLAG_SECOND_OBJECT = "second_object"
FLAG_UPSCALED = "upscaled"
FLAG_TOO_SMALL = "too_small_on_canvas"
FLAG_OPAQUE_BACKDROP = "opaque_backdrop"
FLAG_KEPT_SHADOW = "kept_shadow"
# ملاحظات لا تمنع النشر (ProcessResult.quality_notes): نفس رمز العلامة، لكن في القائمة غير الحاجبة
NOTE_UPSCALED = FLAG_UPSCALED
NON_BLOCKING_NOTES = frozenset({NOTE_UPSCALED})
# ما يمكن أن يصلح كل علامة حاجبة (جدول واحد يحكم المحاولات المدفوعة؛ لا نصرف على نتيجة لن تُنشر أبداً):
#   العلامة              بدون صندوق Gemini    المزوّد الآخر   السبب
#   edge_clipped         نعم                  نعم             الصندوق قص المنتج؛ الإطار الكامل يعيده
#   too_small_on_canvas  نعم                  نعم             شيء آخر داخل إطار الصندوق يحدد الحجم
#   opaque_fill          لا                   نعم             المزوّد لم يُزل خلفية التصوير
#   opaque_backdrop      لا                   نعم             المزوّد أبقى ورقة التصوير (للمصدر خلفية حقيقية)
#   alpha_haze           لا                   نعم             ضباب من قناع هذا المزوّد
#   kept_shadow          لا                   نعم             ظل أبقاه هذا المزوّد
#   second_object        لا                   لا              جسم آخر في الصورة نفسها: نهائي (ولشفافية المصدر)
#   upscaled             لا                   لا              المصدر نفسه صغير: نهائي
# مع edge_clipped كل علامات المحاولة نفسها مشكوك فيها (القص قد يفصل جزءاً أو يصغّر المنتج): يعاد الإطار الكامل.
# مع opaque_fill لا يُفحص edge_clipped (الإطار المعتم كله يلمس خطوط القص): المزوّد الآخر بنفس الإطار المقصوص.
_FLAG_REMEDY = {
    FLAG_EDGE_CLIPPED: (True, True),
    FLAG_TOO_SMALL: (True, True),
    FLAG_OPAQUE_FILL: (False, True),
    FLAG_OPAQUE_BACKDROP: (False, True),
    FLAG_ALPHA_HAZE: (False, True),
    FLAG_KEPT_SHADOW: (False, True),
    FLAG_SECOND_OBJECT: (False, False),
    FLAG_UPSCALED: (False, False),
}
# علامات تستحق إعادة نفس المزوّد على الإطار الكامل بدون صندوق Gemini
_BOX_FLAGS = frozenset(flag for flag, (without_box, _other) in _FLAG_REMEDY.items() if without_box)
# لاختيار أفضل محاولة عندما تبقى العلامات بعد كل البدائل (الأقل وزناً تُعرض على المراجع)
_FLAG_WEIGHT = {FLAG_OPAQUE_FILL: 4, FLAG_OPAQUE_BACKDROP: 4, FLAG_EDGE_CLIPPED: 3, FLAG_KEPT_SHADOW: 3,
                FLAG_SECOND_OBJECT: 2, FLAG_TOO_SMALL: 2, FLAG_ALPHA_HAZE: 1, FLAG_UPSCALED: 1}
_NO_CROP = (False, False, False, False)
# البديل التلقائي عند علامة جودة: المزوّدان المدفوعان فقط (GrabCut/rembg لا تُستخدم كبديل أبداً)
_PAID_METHODS = ("photoroom", "remove_bg_api")

# مصدر بخلفية بيضاء نظيفة: قص محلي بدل المزوّد المدفوع (WHITE_SOURCE_MODE = off | log | on)
WHITE_SOURCE_MODES = ("off", "log", "on")
WHITE_SOURCE_MIN_CHANNEL = 248
WHITE_SOURCE_BAND = 0.02
WHITE_SOURCE_BORDER_MIN = 0.995
WHITE_SOURCE_MAIN_SHARE = 0.98
WHITE_SOURCE_ANALYSIS_SIDE = 1000   # الأهلية تُحسم على نسخة مصغرة: وضع log لا يكلف أكثر من جزء من الثانية
WHITE_SOURCE_EDGE_STEP = 32         # حافة المنتج: أغمق من مستوى الخلفية بـ 32 درجة خلال بكسلين...
WHITE_SOURCE_EDGE_MIN = 0.90        # ...في 90% من محيطه (ظل ناعم أو انعكاس يتلاشى بلا حافة)
WHITE_SOURCE_STRICT_AREA = 1.25     # المنتج بعتبة صارمة أكبر بـ 25%، أو تمتد حدوده 8%: أجزاء بيضاء ابتلعتها الخلفية
WHITE_SOURCE_STRICT_GROWTH = 0.08

_ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP", "GIF", "BMP", "TIFF", "MPO", "AVIF", "HEIF"}
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_METHOD_ALIASES = {
    "bria": "bria_rmbg",
    "removebg": "remove_bg_api",
    "remove.bg": "remove_bg_api",
    "remove_bg": "remove_bg_api",
    "no": "none",
    "off": "none",
}


@dataclass
class ProcessResult:
    """
    نتيجة معالجة صورة للنشر.
    path: مسار لوحة PNG النهائية (None عند أي فشل؛ لا يوجد ملف قابل للنشر).
    isolated: True فقط إذا تم عزل المنتج عن خلفيته فعلاً واجتاز القص بوابة الجودة.
    provider: photoroom | remove_bg_api | grabcut | rembg | source_alpha | white_source | none | ...
    error: رمز خطأ واضح (مثل photoroom_402، download_not_image، source_changed) أو None.
    width/height: أبعاد اللوحة النهائية (0 عند الفشل).
    quality_flags: علامات بوابة الجودة للوحة المعادة ([] = نظيفة). عند وجودها تكون isolated=False
        مع path موجود: نفس حالة "الخلفية لم تُعزل" (رابط needs_review: ولا نشر تلقائي).
        dark_halo (اللوحة الشفافة فقط، cutout_finish): حواف فاتحة بتبين على الوضع الغامق، للمراجعة.
    quality_notes: ملاحظات لا تمنع النشر (NON_BLOCKING_NOTES)، مثل 'upscaled': المنتج كُبّر بين ضعفين و3 أضعاف
        على اللوحة (مصدر ويب صغير). اللوحة تُنشر كالمعتاد والملاحظة للمراجع والتقرير فقط.
    white_source: ما فعله/كان سيفعله كشف الخلفية البيضاء: None (معطل أو لم يُفحص) | 'used' |
        'eligible' (وضع log: كان سيُستخدم) | 'ineligible:<سبب>' | 'flagged:<علامات>'.
    fallback_from: None، أو {"method": الطريقة السحابية، "code": رمز فشلها} لما خلص رصيدها أو مفتاحها أو حصتها فعزلت
        طريقة محلية مجانية (BG_FALLBACK=local) بدلاً منها: provider عندها rembg أو grabcut، واللوحة اجتازت بوابة القص نفسها.
    finish: تشطيب اللوحة الشفافة (cutout_finish.finish): background، holes_filled، holes_left، defringed، halo،
        halo_retry. فارغ للوحة البيضا (OUTPUT_BACKGROUND = white).
    """

    path: Optional[str]
    isolated: bool
    provider: str
    error: Optional[str] = None
    width: int = 0
    height: int = 0
    quality_flags: List[str] = field(default_factory=list)
    white_source: Optional[str] = None
    quality_notes: List[str] = field(default_factory=list)
    fallback_from: Optional[Dict[str, str]] = None
    finish: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# توافق مع المستدعين القدامى
# ---------------------------------------------------------------------------

def __getattr__(name):
    # كان LAST_PROCESSING_STATUS قاموساً عاماً قابلاً للتعديل تتشاركه الطلبات المتزامنة.
    # تم حذفه؛ المصدر الصحيح لحالة العزل هو ProcessResult.isolated / .error.
    # نعيد قاموساً فارغاً للقراءة فقط كي لا ينكسر المستدعون الحاليون قبل إعادة ربطهم.
    if name == "LAST_PROCESSING_STATUS":
        logger.warning("LAST_PROCESSING_STATUS محذوف؛ استخدم process_product_image_result().isolated")
        return types.MappingProxyType({})
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def send_telegram_notification(text):
    """تنبيهات Telegram متوقفة بناءً على طلب العميل (دالة فارغة للتوافق)."""
    return False


# ---------------------------------------------------------------------------
# أدوات عامة
# ---------------------------------------------------------------------------

def _normalise_method(bg_method) -> str:
    """يوحّد اسم طريقة العزل؛ عند غيابها نقرأ الإعداد الافتراضي دون تعديله."""

    def _clean(value) -> str:
        name = str(value or "").strip().lower()
        return _METHOD_ALIASES.get(name, name)

    method = _clean(bg_method)
    if not method:
        method = _clean(getattr(config, "BG_REMOVAL_METHOD", "photoroom"))
    return method or "photoroom"


def _resolve_canvas_size(target_width, target_height) -> Tuple[int, int]:
    """0 أو None أو 'dynamic' تعني OUTPUT_CANVAS_SIZE (افتراضياً 800) كمربع."""

    def _as_int(value) -> int:
        if value is None:
            return 0
        if isinstance(value, str):
            value = value.strip().lower()
            if not value or value in ("dynamic", "auto"):
                return 0
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return 0

    w, h = _as_int(target_width), _as_int(target_height)
    if w <= 0 and h <= 0:
        side = settings.output_canvas_size()
        w = h = side
    elif w <= 0:
        w = h
    elif h <= 0:
        h = w
    w = max(MIN_CANVAS_SIDE, min(MAX_CANVAS_SIDE, w))
    h = max(MIN_CANVAS_SIDE, min(MAX_CANVAS_SIDE, h))
    return w, h


def _adaptive_canvas(cutout: Image.Image, canvas_size: Tuple[int, int], fill: float) -> Tuple[int, int]:
    """
    ضلع اللوحة حسب دقة المنتج نفسه: round(الضلع الأطول للمنتج بالبكسل / fill) محصوراً بين الضلع المطلوب (OUTPUT_CANVAS_SIZE،
    افتراضياً 800) و OUTPUT_CANVAS_MAX (افتراضياً 2048). مصدر كبير بيحتفظ بتفاصيله لشاشة موبايل 3x (~1170 بكسل)، ومصدر صغير
    بياخد نفس اللوحة متل قبل بالضبط (ما في تكبير زيادة). لوحة مش مربعة (أبعاد صريحة قديمة) بتضل متل ما هي.
    """
    w, h = int(canvas_size[0]), int(canvas_size[1])
    top = settings.output_canvas_max()
    if w != h or top <= w:
        return w, h
    box = alpha_bbox(cutout)
    if box is None:
        return w, h
    long_side = max(box[2] - box[0], box[3] - box[1])
    side = int(round(long_side / max(float(fill), 1e-6)))
    side = max(w, min(top, MAX_CANVAS_SIDE, side))
    return side, side


def _to_rgb_or_rgba(img: Image.Image) -> Image.Image:
    has_alpha = img.mode in ("RGBA", "LA", "PA", "RGBa", "La") or "transparency" in img.info
    return img.convert("RGBA") if has_alpha else img.convert("RGB")


def _decode_image(data: bytes) -> Tuple[Optional[Image.Image], Optional[str]]:
    """يتحقق من أن البيانات صورة نقطية حقيقية ويعيدها بعد تصحيح اتجاه EXIF."""
    if not data:
        return None, "not_image"
    try:
        with Image.open(io.BytesIO(data)) as probe:
            fmt = (probe.format or "").upper()
            probe.verify()
        if fmt not in _ALLOWED_FORMATS:
            return None, "not_image"
        img = Image.open(io.BytesIO(data))
        img.load()
        try:
            img = ImageOps.exif_transpose(img)
        except Exception:  # noqa: BLE001 - بيانات EXIF تالفة لا تجعل الصورة غير صالحة
            logger.info("تعذر قراءة اتجاه EXIF؛ سيتم استخدام الصورة كما خُزّنت")
        img = _to_rgb_or_rgba(img)
        if img.width < 1 or img.height < 1:
            return None, "not_image"
        return img, None
    except Image.DecompressionBombError:
        return None, "image_too_large"
    except Exception:  # noqa: BLE001 - أي فشل في فك الترميز يعني أنها ليست صورة صالحة
        return None, "not_image"


def has_meaningful_transparency(img: Image.Image) -> bool:
    """True إذا كان أكثر من 5% من بكسلات إطار الصورة شفافاً (خلفية مزالة فعلاً)."""
    if img.mode != "RGBA":
        return False
    import numpy as np

    alpha = np.asarray(img.getchannel("A"))
    border = np.concatenate([alpha[0, :], alpha[-1, :], alpha[:, 0], alpha[:, -1]])
    if border.size == 0:
        return False
    return float((border <= TRANSPARENT_ALPHA_MAX).mean()) > TRANSPARENT_BORDER_MIN_RATIO


def is_background_already_removed(image_path) -> bool:
    """واجهة توافقية: فحص ملف على القرص بنفس قاعدة الـ 5% من بكسلات الإطار."""
    try:
        with open(image_path, "rb") as fh:
            img, _ = _decode_image(fh.read())
        return bool(img is not None and has_meaningful_transparency(img))
    except OSError:
        return False


def _limit_work_size(img: Image.Image) -> Image.Image:
    if max(img.size) <= MAX_WORK_SIDE:
        return img
    img = img.copy()
    img.thumbnail((MAX_WORK_SIDE, MAX_WORK_SIDE), Image.Resampling.LANCZOS)
    return img


def _encode_for_upload(img: Image.Image) -> Tuple[bytes, str, str]:
    """ترميز صورة العمل لإرسالها لمزوّد العزل: PNG إذا كانت بشفافية، وإلا JPEG بجودة 95."""
    buf = io.BytesIO()
    if img.mode == "RGBA":
        img.save(buf, format="PNG")
        return buf.getvalue(), "image/png", "image.png"
    img.convert("RGB").save(buf, format="JPEG", quality=95)
    return buf.getvalue(), "image/jpeg", "image.jpg"


def _decode_cutout(data: bytes) -> Optional[Image.Image]:
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
        return img.convert("RGBA")
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# مصدر الصورة: مخزن المرشحات أو التنزيل أو ملف محلي
# ---------------------------------------------------------------------------

def _load_from_candidate_store(candidate_sha256) -> Optional[bytes]:
    sha = str(candidate_sha256 or "").strip().lower()
    if not _SHA256_RE.match(sha):
        return None
    store = settings.candidate_store_dir()
    if store and not os.path.isabs(store):
        # مثل catalog_match.fetch: المسار النسبي من مجلد المشروع، لا من مجلد العملية الحالي
        store = os.path.join(os.path.dirname(os.path.abspath(__file__)), store)
    if not store or not os.path.isdir(store):
        return None
    matches = sorted(glob.glob(os.path.join(glob.escape(store), sha + ".*")))
    exact = os.path.join(store, sha)
    if os.path.isfile(exact):
        matches.insert(0, exact)
    for path in matches:
        try:
            with open(path, "rb") as fh:
                data = fh.read(MAX_LOCAL_BYTES + 1)
        except OSError:
            continue
        if len(data) > MAX_LOCAL_BYTES:
            continue
        if hashlib.sha256(data).hexdigest() == sha:
            return data
        logger.warning("ملف مخزن المرشحات لا يطابق بصمته sha256 وسيتم تجاهله: %s", path)
    return None


# أخطاء تنزيل قد ينجح بعدها طريق آخر (البروكسي): انقطاع، مهلة، 5xx، أو رفض المصدر لهذا العميل (403 / 429)
_PROXY_MAY_HELP = ("timeout", "connection_error", "http_403", "http_429")


def _proxy_may_help(error: Optional[str]) -> bool:
    return bool(error) and (error in _PROXY_MAY_HELP or str(error).startswith("http_5"))


def _download_bytes(url: str, page_url=None, info=None) -> Tuple[Optional[bytes], Optional[str]]:
    """
    نفس ترويسات تنزيل المرشح الأصلي (catalog_match.fetch.request_headers: Accept بـ AVIF أولاً، و Referer = صفحة
    المرشح إن عُرفت): شبكات توزيع تختار الصيغة لكل طلب كانت تعيد بايتات أخرى فيفشل الاعتماد بـ source_changed.
    ونفس الطريق: المحاولة الأولى مباشرة دائماً، و PROXY_URL بديل بعد فشل أو رفض فقط (catalog_match.fetch). كان
    التنزيل يمر بالبروكسي وحده عند ضبطه، فبروكسي بطيء أو معطل كان يُفشل كل اعتماد.
    info: قاموس اختياري يملؤه التنزيل بالطريق الذي جرّبه (فحص النشر، publish_check): route (direct | proxy | None)،
    direct_error، proxy_tried، proxy_error. القيمة المعادة لا تتغير.
    """
    from catalog_match.fetch import request_headers
    from http_client import ImpersonateClient

    trace = info if isinstance(info, dict) else {}
    trace.update(route=None, direct_error=None, proxy_tried=False, proxy_error=None)
    headers = request_headers(str(page_url or "").strip() or None)
    fetched = ImpersonateClient(use_proxy=False).fetch_image(url, timeout=15, max_bytes=MAX_DOWNLOAD_BYTES,
                                                             headers=headers)
    route = "direct"
    trace["direct_error"] = None if fetched.content is not None else (fetched.error or "failed")
    proxy = settings.proxy_url()
    if fetched.content is None and proxy and _proxy_may_help(fetched.error):
        logger.info("تنزيل الصورة المعتمدة فشل مباشرة (%s)؛ محاولة عبر البروكسي", fetched.error)
        trace["proxy_tried"], route = True, "proxy"
        fetched = ImpersonateClient(use_proxy=True, proxy_url=proxy).fetch_image(
            url, timeout=15, max_bytes=MAX_DOWNLOAD_BYTES, headers=headers)
        trace["proxy_error"] = None if fetched.content is not None else (fetched.error or "failed")
    if fetched.content is None:
        return None, f"download_{fetched.error or 'failed'}"
    trace["route"] = route
    return fetched.content, None


def _load_source(image_url_or_path, candidate_sha256=None,
                 page_url=None) -> Tuple[Optional[bytes], Optional[str], str]:
    """
    يعيد (البيانات، رمز الخطأ، نوع المصدر: candidate|download|local).
    إذا مُررت بصمة sha256 لبايتات تم التحقق منها ولم يوجد ملفها في مخزن المرشحات، تُقرأ الصورة من الرابط
    من جديد ويجب أن تطابق البايتات تلك البصمة؛ وإلا نفشل بإغلاق (source_changed) ولا ننشر صورة لم يُتحقق منها.
    """
    data = _load_from_candidate_store(candidate_sha256) if candidate_sha256 else None
    if data is not None:
        return data, None, "candidate"

    data, error, origin = _read_source(image_url_or_path, page_url)
    sha = str(candidate_sha256 or "").strip().lower()
    if data is not None and _SHA256_RE.match(sha) and hashlib.sha256(data).hexdigest() != sha:
        logger.warning("بايتات المصدر تغيرت منذ التحقق منها (البصمة لا تطابق)؛ لن تتم معالجتها: %s",
                       str(image_url_or_path)[:200])
        return None, "source_changed", origin
    return data, error, origin


def _read_source(image_url_or_path, page_url=None) -> Tuple[Optional[bytes], Optional[str], str]:
    source = str(image_url_or_path or "").strip()
    if not source:
        return None, "source_missing", "local"
    lowered = source.lower()
    if lowered.startswith(("http://", "https://")):
        data, err = _download_bytes(source, page_url)
        return data, err, "download"
    if "://" in source or lowered.startswith("data:"):
        return None, "download_bad_scheme", "download"
    if not os.path.isfile(source):
        return None, "source_not_found", "local"
    try:
        if os.path.getsize(source) > MAX_LOCAL_BYTES:
            return None, "source_too_large", "local"
        with open(source, "rb") as fh:
            return fh.read(), None, "local"
    except OSError:
        return None, "source_unreadable", "local"


# ---------------------------------------------------------------------------
# تحديد موقع المنتج عبر Gemini (اختياري ومتحقق منه)
# ---------------------------------------------------------------------------

def _parse_box(text: str) -> Optional[List[float]]:
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    try:
        result = json.loads(text.strip())
    except (TypeError, ValueError):
        return None
    if isinstance(result, list) and result and isinstance(result[0], dict):
        result = result[0]
    if isinstance(result, dict):
        box = result.get("box", result.get("box_2d"))
    else:
        box = result
    if isinstance(box, list) and len(box) == 4 and all(isinstance(v, (int, float)) for v in box):
        return [float(v) for v in box]
    return None


def _sane_box(box) -> bool:
    """[ymin, xmin, ymax, xmax] بمقياس 0-1000 ومساحة بين 5% و100% من الصورة."""
    if not isinstance(box, (list, tuple)) or len(box) != 4:
        return False
    try:
        ymin, xmin, ymax, xmax = (float(v) for v in box)
    except (TypeError, ValueError):
        return False
    if not all(0 <= v <= 1000 for v in (ymin, xmin, ymax, xmax)):
        return False
    if ymax <= ymin or xmax <= xmin:
        return False
    area = (ymax - ymin) * (xmax - xmin) / 1_000_000.0
    return BOX_MIN_AREA_FRACTION <= area <= 1.0


def _locate_product_box(img: Image.Image, product_name, brand) -> Optional[List[float]]:
    """يطلب من Gemini صندوق المنتج الرئيسي. يعيد None عند غياب المفتاح أو أي خطأ."""
    api_key = settings.gemini_api_key()
    if not api_key:
        return None
    try:
        thumb = img.convert("RGB")
        thumb.thumbnail((400, 400))
        buf = io.BytesIO()
        thumb.save(buf, format="JPEG", quality=80)
        prompt = (
            f"Locate the main commercial packaged product of the brand '{brand}' for '{product_name}' in this image. "
            "Return the single bounding box enclosing ONLY that product package (carton, bottle, tub, bag), "
            "including its cap and base. Coordinates are [ymin, xmin, ymax, xmax] normalised to 0-1000. "
            'Reply strictly as JSON: {"box": [ymin, xmin, ymax, xmax]}'
        )
        payload = {
            "contents": [{"parts": [
                {"text": prompt},
                {"inlineData": {"mimeType": "image/jpeg", "data": base64.b64encode(buf.getvalue()).decode("ascii")}},
            ]}],
            "generationConfig": {"responseMimeType": "application/json"},
        }
        _count_gemini_call()
        response = requests.post(
            GEMINI_URL.format(model=settings.gemini_model()),
            headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
            json=payload,
            timeout=15,
        )
        if response.status_code != 200:
            logger.warning("فشل تحديد موقع المنتج عبر Gemini (كود %s)", response.status_code)
            return None
        text = response.json()["candidates"][0]["content"]["parts"][0]["text"]
        return _parse_box(text)
    except Exception as exc:  # noqa: BLE001
        logger.warning("خطأ أثناء تحديد موقع المنتج عبر Gemini: %s", exc)
        return None


def _box_rect(size, box, margin: float = BOX_MARGIN_FRACTION) -> Tuple[int, int, int, int]:
    """صندوق Gemini (0-1000) مع الهامش بإحداثيات البكسل (left, top, right, bottom)."""
    ymin, xmin, ymax, xmax = (float(v) for v in box)
    my = (ymax - ymin) * margin
    mx = (xmax - xmin) * margin
    ymin, xmin = max(0.0, ymin - my), max(0.0, xmin - mx)
    ymax, xmax = min(1000.0, ymax + my), min(1000.0, xmax + mx)
    w, h = size
    left = max(0, min(int(xmin / 1000.0 * w), w - 1))
    top = max(0, min(int(ymin / 1000.0 * h), h - 1))
    right = max(left + 1, min(int(round(xmax / 1000.0 * w)), w))
    bottom = max(top + 1, min(int(round(ymax / 1000.0 * h)), h))
    return left, top, right, bottom


def _crop_to_box(img: Image.Image, box, margin: float = BOX_MARGIN_FRACTION) -> Image.Image:
    return img.crop(_box_rect(img.size, box, margin))


def _crop_with_sides(img: Image.Image, box):
    """
    يقص حسب صندوق Gemini ويعيد (الصورة المقصوصة، الجوانب التي هي خطوط قص داخلية (left, top, right, bottom)،
    مستطيل القص بإحداثيات الصورة). جانب عند حافة الصورة الأصلية ليس خط قص: لمس المنتج له لا يعني أن الصندوق قصه.
    يعيد None إذا كان الصندوق يغطي الصورة كلها (لا فائدة من محاولة منفصلة).
    """
    left, top, right, bottom = _box_rect(img.size, box)
    sides = (left > 0, top > 0, right < img.width, bottom < img.height)
    if not any(sides):
        return None
    return img.crop((left, top, right, bottom)), sides, (left, top, right, bottom)


def get_product_bounding_box(image_path, product_name, brand):
    """واجهة توافقية: صندوق Gemini لملف على القرص، فقط إذا اجتاز فحص المعقولية."""
    try:
        with open(image_path, "rb") as fh:
            img, _ = _decode_image(fh.read())
    except OSError:
        return None
    if img is None:
        return None
    box = _locate_product_box(img, product_name, brand)
    return box if _sane_box(box) else None


def crop_image_by_box(image_path, box, output_path):
    """واجهة توافقية: اقتصاص ملف حسب صندوق Gemini (مع هامش 6%) وحفظه بدون فقد."""
    if not _sane_box(box):
        return False
    try:
        with open(image_path, "rb") as fh:
            img, _ = _decode_image(fh.read())
        if img is None:
            return False
        _save_lossless(_crop_to_box(img, box), output_path)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("خطأ أثناء اقتصاص الصورة: %s", exc)
        return False


def _save_lossless(img: Image.Image, output_path: str) -> None:
    ext = os.path.splitext(str(output_path))[1].lower()
    if ext == ".webp":
        img.save(output_path, format="WEBP", lossless=True)
    elif ext in (".jpg", ".jpeg"):
        img.convert("RGB").save(output_path, format="JPEG", quality=95)
    else:
        img.save(output_path, format="PNG")


# ---------------------------------------------------------------------------
# مزوّدو عزل الخلفية: كل دالة تعيد (صورة RGBA، None) أو (None، رمز خطأ)
# ---------------------------------------------------------------------------

def _isolate_photoroom(img: Image.Image):
    api_key = str(getattr(config, "PHOTOROOM_API_KEY", "") or "").strip()
    if not api_key:
        return None, "photoroom_no_key"
    data, mime, filename = _encode_for_upload(img)
    form = {
        "format": "png",
        "channels": "rgba",
        "size": str(getattr(config, "PHOTOROOM_SIZE", "full") or "full"),
        "crop": "true" if _photoroom_crop() else "false",
        "despill": "true" if getattr(config, "PHOTOROOM_DESPILL", True) else "false",
    }
    try:
        response = requests.post(
            PHOTOROOM_URL,
            headers={"x-api-key": api_key},
            files={"image_file": (filename, data, mime)},
            data=form,
            timeout=PHOTOROOM_TIMEOUT,
        )
    except requests.exceptions.Timeout:
        return None, "photoroom_timeout"
    except requests.exceptions.RequestException as exc:
        logger.warning("تعذر الاتصال بـ PhotoRoom: %s", exc)
        return None, "photoroom_connection_error"
    if response.status_code != 200:
        logger.warning("فشل PhotoRoom (كود %s)", response.status_code)
        return None, f"photoroom_{response.status_code}"
    cutout = _decode_cutout(response.content)
    if cutout is None:
        return None, "photoroom_bad_output"
    return cutout, None


def _isolate_remove_bg(img: Image.Image):
    api_key = str(getattr(config, "REMOVE_BG_API_KEY", "") or "").strip()
    if not api_key:
        return None, "removebg_no_key"
    data, mime, filename = _encode_for_upload(img)
    try:
        response = requests.post(
            REMOVE_BG_URL,
            headers={"X-Api-Key": api_key},
            files={"image_file": (filename, data, mime)},
            data={"size": "auto", "format": "png"},
            timeout=REMOVE_BG_TIMEOUT,
        )
    except requests.exceptions.Timeout:
        return None, "removebg_timeout"
    except requests.exceptions.RequestException as exc:
        logger.warning("تعذر الاتصال بـ remove.bg: %s", exc)
        return None, "removebg_connection_error"
    if response.status_code != 200:
        logger.warning("فشل remove.bg (كود %s)", response.status_code)
        return None, f"removebg_{response.status_code}"
    cutout = _decode_cutout(response.content)
    if cutout is None:
        return None, "removebg_bad_output"
    return cutout, None


def _isolate_grabcut(img: Image.Image):
    """عزل محلي بخوارزمية GrabCut على مصفوفة في الذاكرة (لا مسارات ملفات لـ OpenCV)."""
    try:
        import cv2
        import numpy as np

        rgb = img.convert("RGB")
        work = rgb.copy()
        work.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
        bgr = cv2.cvtColor(np.asarray(work), cv2.COLOR_RGB2BGR)
        h, w = bgr.shape[:2]
        mask = np.zeros((h, w), np.uint8)
        rect = (int(w * 0.05), int(h * 0.05), max(1, int(w * 0.9)), max(1, int(h * 0.9)))
        bgd_model = np.zeros((1, 65), np.float64)
        fgd_model = np.zeros((1, 65), np.float64)
        cv2.grabCut(bgr, mask, rect, bgd_model, fgd_model, 5, cv2.GC_INIT_WITH_RECT)
        fg = np.where((mask == cv2.GC_FGD) | (mask == cv2.GC_PR_FGD), 255, 0).astype(np.uint8)
        if not fg.any():
            return None, "grabcut_empty"
        alpha = Image.fromarray(fg)
        if alpha.size != rgb.size:
            alpha = alpha.resize(rgb.size, Image.Resampling.BILINEAR)
        cutout = rgb.convert("RGBA")
        cutout.putalpha(alpha)
        return cutout, None
    except Exception as exc:  # noqa: BLE001
        logger.warning("فشل العزل بـ GrabCut: %s", exc)
        return None, "grabcut_failed"


MANUAL_REMBG_MODEL = "birefnet-general"       # الافتراضي فقط: rembg (يدوياً أو بديلاً تلقائياً) يستعمل REMBG_MODEL (BiRefNet)
_REMBG_SESSIONS: Dict[str, object] = {}         # الجلسة تُنشأ مرة بالعملية لكل موديل وتُعاد استعمالها (تحميل الموديل ثقيل)
_REMBG_FAILED: set = set()                      # موديل فشل إنشاء جلسته (غير منزّل، لا إنترنت): لا نعيد المحاولة كل منتج
_REMBG_LOCK = threading.Lock()


def _rembg_session(model: str):
    """جلسة rembg للموديل، تُنشأ مرة بالعملية؛ None إذا فشل إنشاؤها (reset_cloud_breaker يسمح بمحاولة جديدة)."""
    with _REMBG_LOCK:
        session = _REMBG_SESSIONS.get(model)
        if session is not None or model in _REMBG_FAILED:
            return session
        try:
            from rembg import new_session
            session = new_session(model)
        except Exception as exc:  # noqa: BLE001 - ImportError، موديل غير منزّل، تعذر التنزيل
            logger.warning("تعذر تجهيز موديل rembg %s: %s", model, exc)
            _REMBG_FAILED.add(model)
            return None
        _REMBG_SESSIONS[model] = session
        return session


def _isolate_rembg(img: Image.Image, model: str = MANUAL_REMBG_MODEL):
    try:
        from rembg import remove
    except ImportError:
        return None, "rembg_not_installed"
    try:
        session = _rembg_session(model)
        if session is None:
            return None, "rembg_failed"
        data, _, _ = _encode_for_upload(img)
        output = remove(data, session=session)
        cutout = _decode_cutout(output if isinstance(output, (bytes, bytearray)) else b"")
        if cutout is None and isinstance(output, Image.Image):
            cutout = output.convert("RGBA")
        if cutout is None:
            return None, "rembg_bad_output"
        return cutout, None
    except Exception as exc:  # noqa: BLE001
        logger.warning("فشل العزل بـ rembg: %s", exc)
        return None, "rembg_failed"


def local_methods_available() -> dict:
    """
    طرق العزل المحلية المجانية التي تعمل على هذا الجهاز، بنفس ما يقرره _isolate عند الاستدعاء: GrabCut يحتاج OpenCV
    (cv2، من requirements.txt، وفحص القص نفسه يستعمله) و rembg مكتبة اختيارية (بدونها rembg_not_installed).
    find_spec فقط: لا يُستورد أي نموذج ولا مكتبة ثقيلة. صفحة الإعدادات لا تَعِد بطريقة غير منزّلة.
    """
    import importlib.util

    def installed(name):
        try:
            return importlib.util.find_spec(name) is not None
        except (ImportError, ValueError):
            return False

    return {"grabcut": installed("cv2"), "rembg": installed("rembg")}


def _isolate(img: Image.Image, method: str):
    """يستدعي مزوّد العزل المطلوب. الطرق غير المدعومة تفشل بوضوح ولا تنسخ الأصل أبداً."""
    if method == "photoroom":
        return _isolate_photoroom(img)
    if method == "remove_bg_api":
        return _isolate_remove_bg(img)
    if method == "grabcut":
        return _isolate_grabcut(img)
    if method == "rembg":
        # نفس موديل البديل التلقائي (REMBG_MODEL، BiRefNet): الموديلات الصغيرة (u2net، isnet) بتاكل العلب البيضا
        return _isolate_rembg(img, settings.rembg_model())
    if method == "bria_rmbg":
        # نموذج Bria يتطلب torch وترخيصه غير تجاري؛ غير مدعوم في هذا المسار
        return None, "bria_rmbg_unsupported"
    return None, "unknown_bg_method"


# ---------------------------------------------------------------------------
# بديل محلي مجاني لما يخلص رصيد المزوّد السحابي (BG_FALLBACK=local، الافتراضي)
# ---------------------------------------------------------------------------

# رصيد أو مفتاح أو حصة PhotoRoom / remove.bg: نفس القاعدة بـ publish_check.BG_SKIP_CODE_RE وبالصحة وشاشة المراجعة. مهلة
# الشبكة وأخطاء الخادم والمخرج التالف ليست منها: لا بديل لها، وتبقى فشلاً كما كانت.
BILLING_CODE_RE = r"^(photoroom|removebg)_(no_key|401|402|403|429)$"
# البديل التلقائي rembg بموديل REMBG_MODEL (BiRefNet) فقط: GrabCut و rembg بموديله الصغير (u2net) بياكلوا العلب البيضا (كرتونة
# الحليب)، فهم اختيار المالك اليدوي بالإعدادات ولا يُجرَّبون تلقائياً أبداً
CLOUD_BREAKER_PAUSE_S = 30 * 60


def is_billing_code(code) -> bool:
    """هل رمز فشل العزل رصيد أو مفتاح أو حصة مزوّد سحابي (لا مهلة ولا شبكة ولا خطأ خادم)؟"""
    return bool(re.match(BILLING_CODE_RE, str(code or "")))


class CloudBreaker:
    """
    لكل طريقة سحابية: بعد فشلها برصيد أو مفتاح أو حصة نتجاوزها 30 دقيقة (CLOUD_BREAKER_PAUSE_S) في هذه العملية ونذهب
    للبديل المحلي مباشرة، فتشغيل بمئة منتج يدفع طلب PhotoRoom الفاشل مرة وليس مئة. paused_code(method) يعيد رمز
    الفشل أثناء الإيقاف (وإلا None، وبعد المهلة تبدأ الطريقة بسجل نظيف). آمن بين الخيوط، والساعة قابلة للحقن فلا تنام
    الاختبارات. reset() يصفّر كل شيء (بداية تشغيل جديد، والاختبارات).
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic, pause_s: float = CLOUD_BREAKER_PAUSE_S) -> None:
        self._clock = clock
        self._pause_s = pause_s
        self._lock = threading.Lock()
        self._paused: Dict[str, Tuple[float, str]] = {}

    def paused_code(self, method: str) -> Optional[str]:
        with self._lock:
            entry = self._paused.get(method)
            if entry is None:
                return None
            until, code = entry
            if self._clock() >= until:
                del self._paused[method]
                return None
            return code

    def trip(self, method: str, code: str) -> bool:
        """Pause the method after a billing failure; True when this call started the pause."""
        with self._lock:
            now = self._clock()
            entry = self._paused.get(method)
            if entry is not None and now < entry[0]:
                return False                     # a call that was already in flight when another thread tripped it
            self._paused[method] = (now + self._pause_s, str(code))
        logger.warning("عزل الخلفية: %s فشل برمز %s (رصيد أو مفتاح أو حصة)؛ نتجاوزه %d دقيقة ونعزل بطريقة محلية",
                       method, code, int(self._pause_s // 60))
        return True

    def reset(self) -> None:
        with self._lock:
            self._paused.clear()


_CLOUD_BREAKER = CloudBreaker()


def cloud_breaker() -> CloudBreaker:
    """القاطع الواحد لهذه العملية."""
    return _CLOUD_BREAKER


def reset_cloud_breaker() -> None:
    """ينسى كل إيقاف (بداية تشغيل جديد للعامل: الرصيد ربما شُحن، والاختبارات)."""
    _CLOUD_BREAKER.reset()


def _local_fallback_ready() -> bool:
    """هل البديل التلقائي شغّال: BG_FALLBACK=local ومكتبة rembg منزّلة (بدونها لا يتغير شيء). GrabCut لا يدخل أبداً."""
    return settings.bg_fallback() == "local" and bool(local_methods_available().get("rembg"))


def reset_rembg_sessions(everything: bool = False) -> None:
    """ينسى إنشاء جلسة فشل (بداية تشغيل جديد: ربما نُزّل الموديل)؛ الجلسات الجاهزة تبقى محمّلة إلا مع everything (الاختبارات)."""
    with _REMBG_LOCK:
        _REMBG_FAILED.clear()
        if everything:
            _REMBG_SESSIONS.clear()


def _isolate_with_fallback(img: Image.Image, method: str):
    """
    _isolate مع البديل المحلي: يعيد (صورة RGBA أو None، رمز الخطأ، الطريقة التي أنتجتها، fallback_from أو None).
    الطريقة السحابية (PhotoRoom / remove.bg) إذا فشلت برمز رصيد أو مفتاح أو حصة (is_billing_code) نعزل نفس الصورة بـ rembg
    بموديل REMBG_MODEL (BiRefNet، جلسة واحدة بالعملية)، والمخرج يمرّ على بوابة القص مثل أي عزل. أي فشل آخر (مهلة، شبكة،
    خطأ خادم، مخرج تالف) يعود كما هو بلا بديل. بدون rembg منزّلة، أو BG_FALLBACK=off، أو لو فشل rembg نفسه، لا يتغير شيء:
    الرمز الأصلي يعود. GrabCut و«بدون عزل» ليسا بديلاً أبداً (الأول اختيار يدوي، والثاني زر المالك).
    بعد فشل برصيد (401 و402 و403 و429، لا no_key) تُتجاوز الطريقة السحابية 30 دقيقة (CloudBreaker)، وتحت cassette
    لا قاطع (التسجيل والإعادة يرون كل طلب).
    """
    paid = method in _PAID_METHODS
    breaker = _CLOUD_BREAKER if paid and cassette.active() is None else None
    code = breaker.paused_code(method) if breaker is not None else None
    if code is not None and not _local_fallback_ready():
        code = None                      # أُطفئ الخيار أو لم تعد rembg منزّلة بعد الإيقاف: السلوك القديم
    if code is None:
        cutout, error = _isolate(img, method)
        if cutout is not None or not paid or not is_billing_code(error) or not _local_fallback_ready():
            return cutout, error, method, None
        code = error
        if breaker is not None and not str(code).endswith("_no_key"):
            breaker.trip(method, code)
    model = settings.rembg_model()
    cutout, local_error = _isolate_rembg(img, model)
    if cutout is None:
        logger.warning("البديل المحلي rembg (%s) لم يعزل الصورة: %s", model, local_error)
        return None, code, method, None
    logger.info("عزل الخلفية: %s فشل برمز %s؛ انعزلت الصورة بـ rembg (%s)", method, code, model)
    return cutout, None, "rembg", {"method": method, "code": str(code)}


def _enhance_rgb(rgba: Image.Image) -> Image.Image:
    """
    تحسين خفيف للتباين والألوان على قنوات RGB فقط (قناة الشفافية لا تتغير).
    التباين حول متوسط سطوع المنتج نفسه (موزوناً بالشفافية) كما يفعل ImageEnhance.Contrast لصورة معتمة؛
    متوسط الإطار كله كان يحسب البكسلات الشفافة فتتغير النتيجة حسب الهامش الذي أعاده المزوّد أو صندوق Gemini.
    """
    import numpy as np

    rgba = rgba.convert("RGBA")
    alpha = rgba.getchannel("A")
    rgb = rgba.convert("RGB")
    luminance = np.asarray(rgb.convert("L"), dtype=np.float64)
    weights = np.asarray(alpha, dtype=np.float64)
    total = float(weights.sum())
    mean = float((luminance * weights).sum() / total) if total > 0 else float(luminance.mean())
    level = int(mean + 0.5)
    rgb = Image.blend(Image.new("RGB", rgb.size, (level, level, level)), rgb, 1.08)
    rgb = ImageEnhance.Color(rgb).enhance(1.05)
    out = rgb.convert("RGBA")
    out.putalpha(alpha)
    return out


def _count_gemini_call() -> None:
    try:
        config.METRICS["gemini_api_calls"] += 1
    except Exception:  # noqa: BLE001
        pass


def _as_bool(value) -> bool:
    """'false' و'0' و'' نصوصاً تعني False (bool('false') كان True فيُحسّن اللون دون طلب)."""
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def _photoroom_crop() -> bool:
    """PHOTOROOM_CROP: يطلب من PhotoRoom قص الهوامش الشفافة (مطفأ افتراضياً)."""
    return _as_bool(getattr(config, "PHOTOROOM_CROP", False))


def _flatten_on_white(img: Image.Image) -> Image.Image:
    """الصورة كما تبدو على خلفية بيضاء (RGB)."""
    if img.mode != "RGBA":
        return img.convert("RGB")
    flat = Image.new("RGB", img.size, (255, 255, 255))
    flat.paste(img, mask=img.getchannel("A"))
    return flat


# ---------------------------------------------------------------------------
# بوابة جودة القص
# ---------------------------------------------------------------------------

def _mask_bbox(mask) -> Optional[Tuple[int, int, int, int]]:
    import numpy as np

    ys, xs = np.nonzero(mask)
    if ys.size == 0:
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def _same_frame(cutout_size, frame_size) -> bool:
    """مخرج المزوّد يغطي نفس الإطار المرسل (نفس الأبعاد أو نفس النسبة بدقة أخرى)، أي أن المزوّد لم يقصه."""
    if frame_size is None:
        return True
    cw, ch = cutout_size
    fw, fh = frame_size
    if (cw, ch) == (fw, fh):
        return True
    if min(cw, ch, fw, fh) <= 0:
        return False
    return abs((cw / ch) / (fw / fh) - 1.0) <= FRAME_ASPECT_TOLERANCE


def _chroma(colour) -> float:
    """الفرق بين أعلى وأدنى قناة: 0 للرمادي والأبيض والأسود، كبير للون المشبع."""
    return float(max(colour) - min(colour))


def _longest_run(line) -> int:
    """أطول سلسلة True متتالية في مصفوفة أحادية."""
    import numpy as np

    if not line.any():
        return 0
    edges = np.diff(np.concatenate(([0], line.astype(np.int8), [0])))
    return int((np.nonzero(edges == -1)[0] - np.nonzero(edges == 1)[0]).max())


def _edge_clipped(solid, crop_sides) -> bool:
    """
    المنتج يلمس خط قص داخلي (left, top, right, bottom): أكثر من 2% من الخط صلب، أو سلسلة صلبة من 3 بكسل
    متتالية على الخط تمتد 3 بكسل للداخل. نسبة 2% وحدها لا ترى شفاطة بعرض 4 بكسل على خط طوله 300 بكسل.
    """
    d = EDGE_RUN_MIN_PX
    lines = (solid[:, 0], solid[0, :], solid[:, -1], solid[-1, :])
    deep = (solid[:, :d].all(axis=1), solid[:d, :].all(axis=0), solid[:, -d:].all(axis=1), solid[-d:, :].all(axis=0))
    return any(side and (float(line.mean()) > EDGE_TOUCH_MIN or _longest_run(run) >= d)
               for side, line, run in zip(crop_sides, lines, deep))


def _uniform_backdrop_band(rgb, product_share=None) -> bool:
    """
    شريط إطار الصورة (2%، و3 بكسل على الأقل) بلون واحد: خلفية تصوير كان على المزوّد إزالتها. منتج يملأ الإطار
    بهامش 0-3 بكسل يجعل الشريط خليطاً من الهامش والمنتج. شريط موحد بلون مشبع قد يكون حافة واجهة علبة مطبوعة تملأ
    الإطار، فلا يُعتبر خلفية إلا إذا قال صندوق Gemini إن المنتج أصغر بوضوح من الإطار (product_share < 85%؛
    إطار قص Gemini بهامش 6% من كل جهة نسبته نحو 80%، فشريطه هامش خلفية دائماً).
    """
    import numpy as np

    h, w = rgb.shape[:2]
    band = max(3, int(round(OPAQUE_FILL_BAND * min(h, w))))
    if 2 * band >= min(h, w):
        return False
    ring = np.concatenate([rgb[:band].reshape(-1, 3), rgb[-band:].reshape(-1, 3),
                           rgb[band:-band, :band].reshape(-1, 3), rgb[band:-band, -band:].reshape(-1, 3)])
    ring = ring.astype(np.int16)
    ref = np.median(ring, axis=0)
    if float((np.abs(ring - ref).max(axis=1) <= BACKDROP_TOLERANCE).mean()) < BACKDROP_RING_UNIFORM:
        return False
    if _chroma(ref) <= BACKDROP_CHROMA_MAX:
        return True
    return product_share is not None and product_share < OPAQUE_FILL_PRODUCT_MAX


def _has_opaque_backdrop(rgb, main_mask, bbox_area: int) -> bool:
    """
    الجسم الرئيسي مستطيل معتم حوافه بلون واحد محايد (رمادي/أبيض/أسود) يغطي نصف القناع على الأقل، وبداخله جسم
    آخر مختلف اللون: ورقة/صندوق تصوير بقي حول المنتج. علبة مطبوعة بحافة ملونة (أحمر، أزرق...) ليست خلفية.
    علبة بيضاء مطبوعة تبقى ملتبسة هنا؛ يحسمها تطابق القناع مع منطقة المنتج المعروفة (_region_match).
    """
    import cv2
    import numpy as np

    area = int(main_mask.sum())
    if area == 0 or area < BACKDROP_RECT_MIN * bbox_area:
        return False
    eroded = cv2.erode(main_mask.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool)
    ring = main_mask & ~eroded
    if int(ring.sum()) < 8:
        return False
    ring_rgb = rgb[ring].astype(np.int16)
    ref = np.median(ring_rgb, axis=0)
    if (np.abs(ring_rgb - ref).max(axis=1) <= BACKDROP_TOLERANCE).mean() < BACKDROP_RING_UNIFORM:
        return False
    if _chroma(ref) > BACKDROP_CHROMA_MAX:
        return False
    diff = np.abs(rgb.astype(np.int16) - ref).max(axis=2)
    if int((main_mask & (diff <= BACKDROP_TOLERANCE)).sum()) < BACKDROP_COVER_MIN * area:
        return False
    distinct = main_mask & (diff > BACKDROP_OBJECT_DIFF)
    return int(distinct.sum()) >= BACKDROP_OBJECT_MIN * area


def _kept_shadow(rgba: Image.Image, alpha, solid) -> bool:
    """
    الحجم والمركز الحقيقيان للمنتج هما حدود جزئه الصلب (شفافية >= 128). إذا امتدت البكسلات المرئية (> 32) خارجها
    بأكثر من 3% (و4 بكسل) وكان أغلب الامتداد رمادياً داكناً فهو ظل بقي مع المنتج (مزوّد أبقاه، أو مدمج في PNG).
    """
    import numpy as np

    box = _mask_bbox(solid)
    if box is None:
        return False
    tx = max(HAZE_GROWTH_MIN_PX, int(SHADOW_GROWTH_MAX * (box[2] - box[0])))
    ty = max(HAZE_GROWTH_MIN_PX, int(SHADOW_GROWTH_MAX * (box[3] - box[1])))
    outside = alpha > HAZE_ALPHA_MAX
    outside[max(0, box[1] - ty):box[3] + ty, max(0, box[0] - tx):box[2] + tx] = False
    count = int(outside.sum())
    if count < max(20, 0.002 * int(solid.sum())):
        return False
    rgb = np.asarray(rgba.convert("RGB"))[outside].astype(np.int16)
    value = rgb.max(axis=1)
    shadowy = (value <= SHADOW_VALUE_MAX) & (value - rgb.min(axis=1) <= SHADOW_CHROMA_MAX)
    return float(shadowy.mean()) >= SHADOW_SHARE_MIN


def _product_group(areas, heights, main):
    """
    مجموعة المنتج: الجسم الرئيسي وكل جسم مساحته 40% على الأقل منه وارتفاعه مماثل (عبوة ثنائية بينها فراغ).
    جسم أصغر بوضوح (بطاقة سعر، غطاء منفصل، حروف) ليس من المجموعة.
    """
    import numpy as np

    tall = np.maximum(heights, heights[main])
    group = (areas >= GROUP_AREA_MIN * areas[main]) & (np.minimum(heights, heights[main]) >= GROUP_HEIGHT_MIN * tall)
    group[0] = False
    group[main] = True
    return group


@dataclass
class _Assessment:
    flags: List[str]
    main_rect: Optional[Tuple[int, int, int, int]] = None   # حدود الجسم الرئيسي بإحداثيات القص
    notes: List[str] = field(default_factory=list)          # ملاحظات لا تمنع النشر


def assess_cutout(cutout: Image.Image, frame_size=None, crop_sides=_NO_CROP, canvas_size=(800, 800),
                  fill: float = CANVAS_FILL_RATIO, check_backdrop: bool = False) -> List[str]:
    """
    بوابة جودة القص: تعيد قائمة علامات ([] = نظيف) للقص كما سيُركّب على اللوحة.
    frame_size: أبعاد الصورة التي أُرسلت للمزوّد. فحوص الإطار (opaque_fill و edge_clipped) تُجرى فقط إذا
        غطى المخرج نفس الإطار (PHOTOROOM_CROP مطفأ)؛ القص من المزوّد يجعل لمس الحواف طبيعياً فلا يمكن كشفه.
    crop_sides: (left, top, right, bottom) أي جوانب الإطار خطوط قص داخلية من صندوق Gemini.
    العلامات:
      opaque_fill        أكثر من 97% من الإطار معتم وشريط الإطار خلفية موحدة محايدة: لم يُزل شيء. منتج يملأ
                         الإطار (علبة مقصوصة بإحكام) ليس "لم يُزل شيء".
      edge_clipped       المنتج يلمس خط قص داخلي: الصندوق قص جزءاً منه (الغطاء، أو شفاطة بعرض 3 بكسل فأكثر).
      alpha_haze         بكسلات شبه شفافة (غير مرئية تقريباً) توسّع حدود المنتج بوضوح.
      kept_shadow        ظل مرئي (شفافية > 32، رمادي داكن) يمتد خارج حدود الجزء الصلب: اللوحة تُحجّم على كل
                         البكسلات المرئية فيصغر المنتج ويخرج عن المركز (ظلال الاستوديو معطلة بطلب العميل).
      second_object      أجسام أخرى (مجموع مساحتها الصلبة) 1% أو أكثر من الجسم الرئيسي. قطع متقاربة الحجم
                         والارتفاع (عبوتان متجاورتان) مجموعة منتج واحدة وليست جسماً ثانياً.
      upscaled           المنتج سيُكبّر أكثر من 3 أضعاف على اللوحة (بين ضعفين و3: ملاحظة فقط، _assess().notes).
      too_small_on_canvas مجموعة المنتج تشغل أقل من 80% من مساحة الإشغال (شيء آخر يحدد الحجم).
      opaque_backdrop    (مع check_backdrop، ودائماً في مسار العزل) ورقة/صندوق تصوير محايد اللون بقي حول المنتج.
    """
    return _assess(cutout, frame_size, crop_sides, canvas_size, fill, check_backdrop).flags


def _assess(cutout: Image.Image, frame_size=None, crop_sides=_NO_CROP, canvas_size=(800, 800),
            fill: float = CANVAS_FILL_RATIO, check_backdrop: bool = False, frame_checks: bool = True,
            pixel_scale: float = 1.0, product_share=None) -> _Assessment:
    """
    assess_cutout مع ما يحتاجه مسار العزل. frame_checks=False: المزوّد طُلب منه قص الإطار (crop=true).
    pixel_scale: كم بكسلاً من المصدر يمثل بكسل القص (قص نسخة مصغرة) لحساب التكبير الحقيقي على اللوحة.
    product_share: نسبة صندوق Gemini من الإطار المرسل (None بلا صندوق).
    """
    import cv2
    import numpy as np

    rgba = cutout if cutout.mode == "RGBA" else cutout.convert("RGBA")
    alpha = np.asarray(rgba.getchannel("A"))
    solid = alpha >= SOLID_ALPHA
    box = alpha_bbox(rgba)
    if box is None or not solid.any():
        return _Assessment([FLAG_ALPHA_HAZE])
    flags = []

    if frame_checks and _same_frame(rgba.size, frame_size):
        if float(solid.mean()) > OPAQUE_FILL_MAX and _uniform_backdrop_band(np.asarray(_flatten_on_white(rgba)),
                                                                            product_share):
            # لم يُزل شيء: الإطار كله يلمس خطوط القص، فهذا لا يقول شيئاً عن صندوق Gemini
            flags.append(FLAG_OPAQUE_FILL)
        elif _edge_clipped(solid, crop_sides):
            flags.append(FLAG_EDGE_CLIPPED)

    box_w, box_h = box[2] - box[0], box[3] - box[1]
    visible = _mask_bbox(alpha > HAZE_ALPHA_MAX)
    if visible is not None:
        vis_w, vis_h = visible[2] - visible[0], visible[3] - visible[1]
        if (box_w - vis_w > max(HAZE_GROWTH_MIN_PX, HAZE_GROWTH_MAX * vis_w)
                or box_h - vis_h > max(HAZE_GROWTH_MIN_PX, HAZE_GROWTH_MAX * vis_h)):
            flags.append(FLAG_ALPHA_HAZE)
    if _kept_shadow(rgba, alpha, solid):
        flags.append(FLAG_KEPT_SHADOW)

    # الأجسام: مكونات متصلة على شفافية > 24 (جسم عبوة شفافة لا ينفصل غطاؤها عن ملصقها) تحوي بكسلاً صلباً،
    # ومساحة كل جسم = بكسلاته الصلبة
    count, labels, stats, _ = cv2.connectedComponentsWithStats((alpha > GROUP_ALPHA).astype(np.uint8),
                                                               connectivity=8)
    areas = np.bincount(labels[solid], minlength=count)
    areas[0] = 0
    main = int(np.argmax(areas))
    group = _product_group(areas, stats[:, cv2.CC_STAT_HEIGHT], main)
    # كل ما ليس من مجموعة المنتج معاً: تسع حروف علامة مائية بـ 0.5% لكل منها جسم ثانٍ
    if int(areas[~group].sum()) >= SECOND_OBJECT_MIN * int(areas[main]):
        flags.append(FLAG_SECOND_OBJECT)

    # نفس حساب fit_cutout: الحجم يُحدد من حدود الشفافية كلها
    max_w = max(1, int(int(canvas_size[0]) * fill))
    max_h = max(1, int(int(canvas_size[1]) * fill))
    scale = min(max_w / box_w, max_h / box_h)
    upscale = scale / max(pixel_scale, 1e-6)
    notes = []
    if upscale > MAX_UPSCALE:
        flags.append(FLAG_UPSCALED)
    elif upscale > UPSCALE_NOTE_MIN:
        notes.append(NOTE_UPSCALED)
    left, top = stats[group, cv2.CC_STAT_LEFT], stats[group, cv2.CC_STAT_TOP]
    group_w = int((left + stats[group, cv2.CC_STAT_WIDTH]).max() - left.min())
    group_h = int((top + stats[group, cv2.CC_STAT_HEIGHT]).max() - top.min())
    if max(group_w * scale / max_w, group_h * scale / max_h) < MIN_MAIN_EXTENT:
        flags.append(FLAG_TOO_SMALL)

    main_x, main_y = int(stats[main, cv2.CC_STAT_LEFT]), int(stats[main, cv2.CC_STAT_TOP])
    main_w, main_h = int(stats[main, cv2.CC_STAT_WIDTH]), int(stats[main, cv2.CC_STAT_HEIGHT])
    main_solid = (labels == main) & solid
    # المستطيل أولاً: أغلب المنتجات ليست مستطيلاً معتماً فلا داعي لتحويل الألوان
    if check_backdrop and int(main_solid.sum()) >= BACKDROP_RECT_MIN * main_w * main_h:
        if _has_opaque_backdrop(np.asarray(rgba.convert("RGB")), main_solid, main_w * main_h):
            flags.append(FLAG_OPAQUE_BACKDROP)
    return _Assessment(flags, (main_x, main_y, main_x + main_w, main_y + main_h), notes)


# ---------------------------------------------------------------------------
# مصدر بخلفية بيضاء نظيفة: قص محلي بدون مزوّد مدفوع
# ---------------------------------------------------------------------------

def _white_source_mode() -> str:
    """WHITE_SOURCE_MODE: off | log (افتراضي: يكشف ويسجل فقط) | on. قيمة غير معروفة = log."""
    value = getattr(config, "WHITE_SOURCE_MODE", None)
    if value is None:
        value = os.getenv("WHITE_SOURCE_MODE", "log")
    value = str(value or "").strip().lower()
    return value if value in WHITE_SOURCE_MODES else "log"


def _white_background(white):
    """البكسلات البيضاء (white) المتصلة بإطار الصورة (اتصال رباعي): الخلفية. البياض داخل المنتج يبقى."""
    import cv2
    import numpy as np

    _, labels = cv2.connectedComponents(white.astype(np.uint8), connectivity=4)
    edge_labels = np.unique(np.concatenate([labels[0], labels[-1], labels[:, 0], labels[:, -1]]))
    return np.isin(labels, edge_labels[edge_labels != 0])


def _white_source_mask(rgb):
    """
    (قناع المنتج، None) أو (None، السبب) لصورة RGB على خلفية بيضاء. الشروط:
    - 99.5% من شريط 2% حول الإطار شبه أبيض (>= 248)، والمنتج لا يلمس الإطار، وجسم واحد يحمل 98% من المقدمة.
    - soft_edge: حافة المنتج واضحة في 90% من محيطه على الأقل (أغمق من الخلفية بـ 32 درجة خلال بكسلين). ظل ناعم
      أو انعكاس يتلاشى يلتصق بالخلفية بتدرج رمادي بلا حافة، وكان يُخبز في المنتج كجزء معتم.
    - white_parts: المنتج بعتبة صارمة (درجة واحدة تحت مستوى الخلفية) ليس أكبر بوضوح: جسم أبيض بلا حد داكن
      (برطمان 249-253) أو شفاطة بيضاء ابتلعتها الخلفية عند عتبة 248.
    """
    import cv2
    import numpy as np

    h, w = rgb.shape[:2]
    if min(h, w) < 16:
        return None, "too_small"
    band = max(2, int(round(WHITE_SOURCE_BAND * min(h, w))))

    def ring(values):
        return np.concatenate([values[:band].ravel(), values[-band:].ravel(),
                               values[band:-band, :band].ravel(), values[band:-band, -band:].ravel()])

    darkest = np.ascontiguousarray(rgb.min(axis=2))
    if float(ring(darkest >= WHITE_SOURCE_MIN_CHANNEL).mean()) < WHITE_SOURCE_BORDER_MIN:
        return None, "background_not_white"
    background = _white_background(darkest >= WHITE_SOURCE_MIN_CHANNEL)
    foreground = ~background
    fg_box = _mask_bbox(foreground)
    if fg_box is None:
        return None, "empty"
    if fg_box[0] < 2 or fg_box[1] < 2 or fg_box[2] > w - 2 or fg_box[3] > h - 2:
        return None, "product_touches_frame"
    count, labels, stats, _ = cv2.connectedComponentsWithStats(foreground.astype(np.uint8), connectivity=8)
    areas = stats[1:, cv2.CC_STAT_AREA]
    if int(areas.max()) < WHITE_SOURCE_MAIN_SHARE * int(areas.sum()):
        return None, "several_objects"
    main = labels == 1 + int(np.argmax(areas))

    # مستوى الخلفية بعد تنعيم ضوضاء JPEG (أدنى 1% من الشريط)
    smooth = cv2.blur(darkest, (3, 3))
    level = float(np.percentile(ring(smooth), 1))
    kernel = np.ones((3, 3), np.uint8)
    rim = main & cv2.dilate(background.astype(np.uint8), kernel).astype(bool)
    near_min = cv2.erode(darkest, np.ones((5, 5), np.uint8))
    if rim.any() and float((near_min[rim] <= level - WHITE_SOURCE_EDGE_STEP).mean()) < WHITE_SOURCE_EDGE_MIN:
        return None, "soft_edge"

    strict = ~_white_background(smooth >= max(WHITE_SOURCE_MIN_CHANNEL, level - 1))
    count, labels = cv2.connectedComponents(strict.astype(np.uint8), connectivity=8)
    overlap = np.bincount(labels[main], minlength=count)
    overlap[0] = 0
    strict_main = labels == int(np.argmax(overlap)) if overlap.any() else main
    box, strict_box = _mask_bbox(main), _mask_bbox(strict_main)
    bw, bh = box[2] - box[0], box[3] - box[1]
    growth = max(box[0] - strict_box[0], strict_box[2] - box[2]) / bw
    growth = max(growth, max(box[1] - strict_box[1], strict_box[3] - box[3]) / bh)
    if int(strict_main.sum()) > WHITE_SOURCE_STRICT_AREA * int(main.sum()) or growth > WHITE_SOURCE_STRICT_GROWTH:
        return None, "white_parts"
    return foreground, None


def _white_source_cutout(img: Image.Image, full: bool = True):
    """
    إذا كانت الصورة لقطة منتج على خلفية بيضاء نظيفة يُبنى القص محلياً: الخلفية = البكسلات شبه البيضاء المتصلة
    بالإطار (البياض داخل المنتج يبقى). الأهلية (_white_source_mask) تُحسم على نسخة مصغرة (<= 1000 بكسل) حتى يبقى
    وضع log رخيصاً؛ full=True يبني القناع بالدقة الكاملة (للنشر في وضع on)، و full=False يعيد قص النسخة المصغرة.
    يعيد (RGBA، None) أو (None، السبب).
    """
    import numpy as np

    flat = _flatten_on_white(img)
    factor = max(1, -(-max(flat.size) // WHITE_SOURCE_ANALYSIS_SIDE))
    small = flat.reduce(factor) if factor > 1 else flat
    rgb = np.asarray(small)
    foreground, reason = _white_source_mask(rgb)
    if foreground is None:
        return None, reason
    if full and factor > 1:
        rgb = np.asarray(flat)
        foreground = ~_white_background(rgb.min(axis=2) >= WHITE_SOURCE_MIN_CHANNEL)
    # قناع حاد بلا تنعيم: حواف المنتج في المصدر ممزوجة بالأبيض أصلاً، فتظهر على اللوحة البيضاء كما في المصدر
    # (وتنعيم القناع كان سيضيف هالة بكسل تدخل في حساب حدود المنتج)
    alpha = np.where(foreground, 255, 0).astype(np.uint8)
    cutout = Image.fromarray(np.ascontiguousarray(rgb), "RGB").convert("RGBA")
    cutout.putalpha(Image.fromarray(alpha))
    return cutout, None


# ---------------------------------------------------------------------------
# العزل مع بوابة الجودة والبدائل التلقائية
# ---------------------------------------------------------------------------

@dataclass
class _Attempt:
    cutout: Optional[Image.Image]
    provider: str
    flags: List[str] = field(default_factory=list)
    error: Optional[str] = None
    label: str = ""
    matches_source: bool = False     # قناع المزوّد يطابق شفافية المصدر (IoU > 0.95)
    main_rect: Optional[Tuple[int, int, int, int]] = None
    notes: List[str] = field(default_factory=list)
    frame_size: Optional[Tuple[int, int]] = None              # الإطار المرسل وموضعه في صورة العمل
    frame_rect: Optional[Tuple[int, int, int, int]] = None
    fallback_from: Optional[Dict[str, str]] = None            # الطريقة السحابية التي خلص رصيدها وهذه بديلتها المحلية


def _flags_final(flags) -> bool:
    """
    لا داعي لمحاولة مدفوعة أخرى: القص نظيف، أو فيه علامة لا تصلحها أي إعادة عزل (_FLAG_REMEDY) فلن يُنشر تلقائياً
    مهما دفعنا. مع edge_clipped لا شيء نهائي بعد: الإطار الكامل قد يغيّر كل العلامات.
    """
    flags = set(flags)
    if not flags or FLAG_EDGE_CLIPPED in flags:
        return not flags
    # علامة غير معروفة تُعامل كقابلة للإصلاح بمزوّد آخر
    return any(not any(_FLAG_REMEDY.get(flag, (False, True))) for flag in flags)


def _rect_iou(a, b) -> float:
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def _placed_mask(cutout: Image.Image, frame_size, frame_rect, shape):
    """الجزء الصلب من مخرج المزوّد موضوعاً في مكان الإطار المرسل داخل صورة العمل (shape = (h, w))."""
    import numpy as np

    alpha = cutout.getchannel("A")
    if alpha.size != tuple(frame_size):
        alpha = alpha.resize(tuple(frame_size), Image.Resampling.NEAREST)
    mask = np.zeros(shape, dtype=bool)
    left, top, right, bottom = frame_rect
    mask[top:bottom, left:right] = np.asarray(alpha) >= SOLID_ALPHA
    return mask


def _region_match(cutout, main_rect, frame_size, frame_rect, source_mask, product_rect):
    """
    (يطابق شفافية المصدر، يطابق صندوق Gemini). مخرج يطابق منطقة المنتج المعروفة ليس صندوق تصوير بقي حوله:
    رأيان مستقلان (المصدر والمزوّد، أو Gemini والمزوّد) يريان نفس المستطيل منتجاً، أي علبة مطبوعة.
    لا مقارنة إذا لم يكن الإطار معروفاً أو قصه المزوّد (PHOTOROOM_CROP): إحداثياته لم تعد إحداثيات الإطار.
    """
    if frame_rect is None or frame_size is None or not _same_frame(cutout.size, frame_size):
        return False, False
    matches_source = False
    if source_mask is not None:
        placed = _placed_mask(cutout, frame_size, frame_rect, source_mask.shape)
        union = int((placed | source_mask).sum())
        matches_source = union > 0 and int((placed & source_mask).sum()) / union > SOURCE_MATCH_IOU
    matches_box = False
    if product_rect is not None and main_rect is not None:
        fx, fy = frame_size[0] / cutout.width, frame_size[1] / cutout.height
        left, top = frame_rect[0], frame_rect[1]
        rect = (left + main_rect[0] * fx, top + main_rect[1] * fy, left + main_rect[2] * fx, top + main_rect[3] * fy)
        matches_box = _rect_iou(rect, product_rect) >= BOX_MATCH_IOU
    return matches_source, matches_box


def _same_mask(a: _Attempt, b: _Attempt, shape) -> bool:
    """مخرجان يغطيان نفس بكسلات صورة العمل (IoU > 0.95)."""
    placed = []
    for attempt in (a, b):
        if attempt.frame_rect is None or not _same_frame(attempt.cutout.size, attempt.frame_size):
            return False
        placed.append(_placed_mask(attempt.cutout, attempt.frame_size, attempt.frame_rect, shape))
    union = int((placed[0] | placed[1]).sum())
    return union > 0 and int((placed[0] & placed[1]).sum()) / union > SOURCE_MATCH_IOU


def _gated(cutout, provider, frame_size, crop_sides, canvas_size, label, frame_rect=None, source_mask=None,
           product_rect=None, frame_checks=True, pixel_scale=1.0) -> _Attempt:
    """
    ينظف القناع ويمرره على البوابة (مع فحص صندوق الخلفية دائماً). source_mask: الجزء الصلب من شفافية المصدر
    بإحداثيات صورة العمل؛ product_rect: صندوق Gemini بلا هامش. frame_rect: موضع الإطار المرسل في صورة العمل
    (None إذا قص المزوّد الإطار: لا فحوص إطار ولا مقارنة بالمواضع).
    """
    cutout = EdgeShadowEngine.process_mask(cutout)
    if alpha_bbox(cutout) is None:
        return _Attempt(None, provider, [], f"{provider}_empty_cutout", label)
    product_share = None
    if product_rect is not None and frame_rect is not None:
        inside = (max(product_rect[0], frame_rect[0]), max(product_rect[1], frame_rect[1]),
                  min(product_rect[2], frame_rect[2]), min(product_rect[3], frame_rect[3]))
        frame_area = (frame_rect[2] - frame_rect[0]) * (frame_rect[3] - frame_rect[1])
        product_share = max(0, inside[2] - inside[0]) * max(0, inside[3] - inside[1]) / max(1, frame_area)
    found = _assess(cutout, frame_size, crop_sides, canvas_size, check_backdrop=True, frame_checks=frame_checks,
                    pixel_scale=pixel_scale, product_share=product_share)
    flags = list(found.flags)
    matches_source = False
    if source_mask is not None or product_rect is not None:
        matches_source, matches_box = _region_match(cutout, found.main_rect, frame_size, frame_rect, source_mask,
                                                    product_rect)
        if FLAG_OPAQUE_BACKDROP in flags and (matches_source or matches_box):
            flags.remove(FLAG_OPAQUE_BACKDROP)
    return _Attempt(cutout, provider, flags, None, label, matches_source, found.main_rect, list(found.notes),
                    tuple(frame_size) if frame_size else None, frame_rect)


def _provider_attempt(frame, method, crop_sides, frame_rect, canvas_size, source_mask=None,
                      product_rect=None) -> _Attempt:
    cutout, error, provider, fallback_from = _isolate_with_fallback(frame, method)
    label = provider + ("+box" if any(crop_sides) else "")
    if cutout is None:
        return _Attempt(None, method, [], error, label)
    if provider == "photoroom" and _photoroom_crop():
        # طلبنا من PhotoRoom القص: لمس الحواف والإطار المعتم طبيعيان، ونسبة الأبعاد لا تكشف ذلك (مع صندوق Gemini
        # يكون للإطار نسبة أبعاد المنتج نفسها)، فلا فحوص إطار ولا مقارنة بالمواضع
        return _gated(cutout, provider, None, _NO_CROP, canvas_size, label, None, None, product_rect,
                      frame_checks=False)
    attempt = _gated(cutout, provider, frame.size, crop_sides, canvas_size, label, frame_rect, source_mask,
                     product_rect)
    if fallback_from is not None:
        if attempt.cutout is None:
            # البديل المحلي لم ينتج قصاً (قناع فارغ): يبقى الفشل الأصلي برصيد المزوّد، ومعه زر «تجاوز عزل الخلفية»
            return _Attempt(None, method, [], fallback_from["code"], label)
        attempt.fallback_from = fallback_from
    return attempt


def _method_has_key(method: str) -> bool:
    name = {"photoroom": "PHOTOROOM_API_KEY", "remove_bg_api": "REMOVE_BG_API_KEY"}.get(method)
    return bool(name and str(getattr(config, name, "") or "").strip())


def _fallback_method(method: str) -> Optional[str]:
    """المزوّد المدفوع الآخر إذا كان مفتاحه مهيأ (PhotoRoom <-> remove.bg). لا بديل للطرق المحلية."""
    if method not in _PAID_METHODS:
        return None
    for other in _PAID_METHODS:
        if other != method and _method_has_key(other):
            return other
    return None


def _best_attempt(attempts) -> Optional[_Attempt]:
    usable = [a for a in attempts if a.cutout is not None]
    if not usable:
        return None
    return min(usable, key=lambda a: (sum(_FLAG_WEIGHT.get(f, 1) for f in a.flags), usable.index(a)))


def _isolate_checked(img: Image.Image, method: str, product_name, brand, canvas_size):
    """
    يعزل المنتج ويمرر كل قص على بوابة الجودة. الترتيب:
    1. شفافية المصدر (source_alpha) إن وجدت؛ عند علامة تُعامل الصورة كما تبدو على الأبيض.
    2. كشف الخلفية البيضاء (WHITE_SOURCE_MODE): في 'on' يُستخدم القص المحلي النظيف دون Gemini ولا مزوّد مدفوع.
    3. المزوّد المطلوب مع صندوق Gemini، ثم بدون الصندوق (فقط لعلامة يصلحها الإطار الكامل: edge_clipped أو
       too_small_on_canvas)، ثم المزوّد المدفوع الآخر المهيأ. نتوقف عند أول قص نظيف، أو عند علامة لا يصلحها شيء
       (_FLAG_REMEDY: second_object، upscaled): لا نصرف على نتيجة ستذهب للمراجعة على أي حال.
    فحص صندوق الخلفية يُجرى على كل مخرج، ويسقط إذا طابق القناع منطقة المنتج المعروفة (شفافية المصدر أو صندوق
    Gemini). إذا طابق مخرج المزوّد شفافية المصدر فالمصدر كان صحيحاً: تُقبل شفافيته (علبة مطبوعة، لا ورقة خلفية)،
    إلا إذا حدد صندوق Gemini المنتج داخل ذلك المستطيل (رأي ثالث يقول إنه ورقة خلفية). بلا صندوق Gemini يكفي
    أن يعيد المزوّدان المدفوعان نفس المستطيل.
    القص المحلي للخلفية البيضاء مشتق من بكسلات المصدر نفسها فلا يُحسب رأياً مستقلاً.
    يعيد (المحاولة المختارة أو محاولة خطأ، isolated، ملاحظة الخلفية البيضاء).
    """
    import numpy as np

    attempts: List[_Attempt] = []
    work, source, source_mask = img, None, None

    if has_meaningful_transparency(img):
        source = _gated(img, "source_alpha", img.size, _NO_CROP, canvas_size, "source_alpha")
        if source.cutout is None:
            return source, False, None
        if not source.flags:
            return source, True, None
        attempts.append(source)
        if _flags_final(source.flags):
            return source, False, None
        logger.info("شفافية المصدر ليست قصاً نظيفاً للمنتج (%s)؛ سيتم عزله من جديد", ",".join(source.flags))
        source_mask = np.asarray(source.cutout.getchannel("A")) >= SOLID_ALPHA
        work = _flatten_on_white(img)
    whole = (0, 0, work.width, work.height)

    def source_accepted() -> bool:
        return source is not None and not source.flags

    def confirm_source(attempt: _Attempt) -> None:
        # مزوّد مستقل رأى نفس شكل المصدر: علامة صندوق الخلفية على المصدر كانت خاطئة
        if source is not None and attempt.matches_source and FLAG_OPAQUE_BACKDROP in source.flags:
            source.flags.remove(FLAG_OPAQUE_BACKDROP)
            logger.info("مخرج %s يطابق شفافية المصدر؛ المصدر ليس صندوق خلفية", attempt.label)

    white_note = None
    mode = _white_source_mode()
    if mode != "off":
        # وضع log: التحليل والبوابة على النسخة المصغرة فقط؛ وضع on: القص بالدقة الكاملة
        white_cut, reason = _white_source_cutout(work, full=(mode == "on"))
        if white_cut is None:
            white_note = f"ineligible:{reason}"
        else:
            white = _gated(white_cut, "white_source", white_cut.size, _NO_CROP, canvas_size, "white_source",
                           pixel_scale=work.width / white_cut.width)
            if white.cutout is not None and not white.flags:
                if mode == "on":
                    return white, True, "used"
                white_note = "eligible"
            else:
                white_note = "flagged:" + ",".join(white.flags or [white.error or "empty"])
                if mode == "on" and white.cutout is not None:
                    attempts.append(white)
        logger.info("كشف الخلفية البيضاء (%s): %s", mode, white_note)

    box = _locate_product_box(work, product_name, brand)
    cropped, product_rect = None, None
    if box is not None and _sane_box(box):
        cropped = _crop_with_sides(work, box)
        product_rect = _box_rect(work.size, box, margin=0.0)
    elif box is not None:
        logger.info("تم تجاهل صندوق Gemini غير المعقول: %s", box)
    full = (work, _NO_CROP, whole)
    if source_mask is not None and product_rect is not None and source.main_rect is not None \
            and _rect_iou(source.main_rect, product_rect) < BOX_MATCH_IOU:
        source_mask = None   # Gemini يرى المنتج داخل مستطيل المصدر: مطابقة المصدر لا تثبت شيئاً

    def attempt_with(spec, provider) -> _Attempt:
        frame, sides, rect = spec
        attempt = _provider_attempt(frame, provider, sides, rect, canvas_size, source_mask, product_rect)
        if attempt.cutout is not None:
            attempts.append(attempt)
            confirm_source(attempt)
        return attempt

    def finish(error_attempt=None):
        if source_accepted():
            return source, True, white_note
        best = _best_attempt(attempts)
        if best is None:
            return error_attempt, False, white_note
        if best.flags:
            logger.warning("القص لم يجتز بوابة الجودة بعد كل البدائل (%s): %s؛ يحال للمراجعة",
                           " -> ".join(a.label for a in attempts), ",".join(best.flags))
        return best, not best.flags, white_note

    first = attempt_with(cropped or full, method)
    if first.cutout is None:
        return finish(first)
    if source_accepted() or _flags_final(first.flags):
        return finish()
    logger.info("القص (%s) لم يجتز بوابة الجودة: %s؛ إعادة المحاولة", first.label, ",".join(first.flags))

    box_problem = bool(_BOX_FLAGS & set(first.flags))
    if cropped is not None and box_problem:
        retry = attempt_with(full, method)
        if retry.cutout is None:
            logger.warning("فشلت إعادة العزل بدون صندوق Gemini: %s", retry.error)
        elif source_accepted() or _flags_final(retry.flags):
            return finish()

    fallback = _fallback_method(method)
    if fallback:
        # الصندوق لم يكن المشكلة: المزوّد الآخر يأخذ نفس الإطار المقصوص
        other = attempt_with(cropped if cropped is not None and not box_problem else full, fallback)
        if other.cutout is None:
            logger.warning("فشل العزل بالمزوّد البديل %s: %s", fallback, other.error)
        elif product_rect is None and FLAG_OPAQUE_BACKDROP in other.flags:
            # بلا صندوق Gemini: المزوّدان المدفوعان المستقلان أعادا نفس المستطيل، فهو المنتج (علبة بيضاء مطبوعة)
            for earlier in attempts[:-1]:
                if earlier.provider in _PAID_METHODS and earlier.provider != other.provider \
                        and FLAG_OPAQUE_BACKDROP in earlier.flags \
                        and _same_mask(earlier, other, (work.height, work.width)):
                    earlier.flags.remove(FLAG_OPAQUE_BACKDROP)
                    other.flags.remove(FLAG_OPAQUE_BACKDROP)
                    logger.info("المزوّدان أعادا نفس المستطيل؛ ليس صندوق خلفية")
                    break
    return finish()


# ---------------------------------------------------------------------------
# الواجهة الرئيسية
# ---------------------------------------------------------------------------

def process_product_image_result(image_url_or_path, product_name, brand, target_width=0, target_height=0,
                                 bg_method=None, candidate_sha256=None, enhance=False, page_url=None,
                                 background=None) -> ProcessResult:
    """
    يحوّل صورة المنتج المعتمدة إلى لوحة نشر نهائية بالأبعاد المطلوبة (0 أو 'dynamic' = OUTPUT_CANVAS_SIZE، افتراضياً
    800x800). اللوحة المربعة بتكبر مع دقة المنتج لحد OUTPUT_CANVAS_MAX (_adaptive_canvas): الضلع المطلوب هو الأدنى، ومصدر
    صغير بياخده متل قبل. background (None = OUTPUT_BACKGROUND): 'transparent' = PNG شفافة RGBA بلا ظل، المنتج مقصوص ومشطّب
    (cutout_finish) ويملأ OUTPUT_PRODUCT_FILL وموسّط؛ صورة بلا عزل (none) بتضل معتمة على الأبيض. 'white' = PNG بخلفية
    بيضاء معتمة RGB والمنتج يملأ 88% وموسّط (متل قبل).
    لا يرفع استثناءات: كل فشل يعود كـ ProcessResult(path=None, isolated=False, error=<رمز>).
    قص لم يجتز بوابة الجودة بعد كل البدائل يعود بلوحة (path) مع isolated=False و quality_flags.
    عند تمرير الأبعاد و enhance و bg_method صراحةً تكون اللوحة دالة لها وللمصدر فقط (ملف معالجة موحد).
    page_url: صفحة المرشح (Referer لإعادة التنزيل كما في التنزيل الأصلي)؛ اختياري.
    """
    method = _normalise_method(bg_method)
    try:
        canvas_size = _resolve_canvas_size(target_width, target_height)

        data, error, origin = _load_source(image_url_or_path, candidate_sha256, page_url)
        if data is None:
            logger.warning("تعذر الحصول على الصورة المصدر: %s", error)
            return ProcessResult(None, False, method, error)

        img, error = _decode_image(data)
        if img is None:
            code = {"download": "download_", "candidate": "candidate_"}.get(origin, "source_") + error
            logger.warning("المصدر ليس صورة صالحة: %s", code)
            return ProcessResult(None, False, method, code)
        img = _limit_work_size(img)

        flags, notes, white_note, fallback_from = [], [], None, None
        if method == "none":
            # 'none' تعني فعلاً بدون عزل: الصورة كما هي (بعد تصحيح الاتجاه) على اللوحة، ولا ندّعي العزل أبداً
            cutout, provider, isolated = EdgeShadowEngine.process_mask(img.convert("RGBA")), "none", False
            if alpha_bbox(cutout) is None:
                return ProcessResult(None, False, provider, f"{provider}_empty_cutout")
            attempt = None
        else:
            attempt, isolated, white_note = _isolate_checked(img, method, product_name, brand, canvas_size)
            if attempt.cutout is None:
                logger.warning("فشل عزل الخلفية بطريقة %s: %s", attempt.provider, attempt.error)
                return ProcessResult(None, False, attempt.provider, attempt.error, white_source=white_note)
            cutout, provider, flags, notes = attempt.cutout, attempt.provider, list(attempt.flags), list(attempt.notes)
            fallback_from = attempt.fallback_from
        finish = {}
        transparent = cutout_finish.output_background(background) == cutout_finish.TRANSPARENT
        # بوابة القص فحصت بالضلع الأدنى (العلامات متل قبل)؛ اللوحة نفسها بدقة المنتج (_adaptive_canvas)
        fill = settings.output_product_fill() if transparent and method != "none" and provider != "none" \
            else CANVAS_FILL_RATIO
        canvas_size = _adaptive_canvas(cutout, canvas_size, fill)
        if transparent:
            # PNG شفافة بلا ظل: سد الثقوب، إزالة التسرب، فحص الهالة (وعزل واحد بـ PhotoRoom لها) ثم اللوحة
            isolated_by = provider
            done = cutout_finish.finish(img, cutout, attempt, provider, isolated, flags, notes, canvas_size,
                                        enhance=_as_bool(enhance), method=method)
            canvas, provider, isolated, flags, notes, finish = (done.canvas, done.provider, done.isolated,
                                                                done.flags, done.notes, done.info)
            if provider != isolated_by:
                fallback_from = None     # إعادة العزل للهالة (PhotoRoom) استبدلت عزل البديل المحلي
        else:
            if _as_bool(enhance):
                cutout = _enhance_rgb(cutout)

            if getattr(config, "ENABLE_STUDIO_SHADOWS", False):
                canvas = EdgeShadowEngine.apply_studio_shadows(cutout, canvas_size)
            else:
                canvas = compose_on_white_canvas(cutout, canvas_size, CANVAS_FILL_RATIO)

        job_dir = tempfile.mkdtemp(prefix="imgproc_")
        out_path = os.path.join(job_dir, f"{uuid.uuid4().hex}.png")
        canvas.save(out_path, format="PNG")
        return ProcessResult(out_path, isolated, provider, None, canvas.width, canvas.height,
                             quality_flags=flags, white_source=white_note, quality_notes=notes,
                             fallback_from=fallback_from, finish=finish)
    except Exception as exc:  # noqa: BLE001 - لا نسمح لأي خطأ غير متوقع بأن يصبح نشراً صامتاً
        logger.exception("خطأ غير متوقع أثناء معالجة الصورة: %s", exc)
        return ProcessResult(None, False, method, "processing_failed")


def cleanup_processed_image(path) -> None:
    """يحذف لوحة المعالجة ومجلدها المؤقت بعد الرفع."""
    if not path:
        return
    try:
        if os.path.isfile(path):
            os.remove(path)
        parent = os.path.dirname(path)
        if os.path.basename(parent).startswith("imgproc_") and not os.listdir(parent):
            shutil.rmtree(parent, ignore_errors=True)
    except OSError:
        pass


def process_product_image(image_url, product_name, brand, bg_removal_method=None, enhance=False,
                          target_width=None, target_height=None, padding_ratio=None, bypass_heuristics=False):
    """
    واجهة توافقية للمستدعين الحاليين: تعيد مسار اللوحة النهائية أو None.
    عند فشل العزل تعيد None (فشل مغلق) ولا تعيد الصورة الخام أبداً.
    padding_ratio و bypass_heuristics لم يعد لهما أثر (الإشغال ثابت 88%).
    """
    result = process_product_image_result(
        image_url, product_name, brand,
        target_width=target_width or 0,
        target_height=target_height or 0,
        bg_method=bg_removal_method,
        enhance=bool(enhance),
    )
    if result.path is None:
        logger.warning("لم يتم إنتاج صورة قابلة للنشر لـ '%s': %s", product_name, result.error)
    return result.path


def remove_background(input_path, output_path, target_width=None, target_height=None, padding_ratio=None,
                      bg_method=None):
    """
    واجهة توافقية: تعزل خلفية ملف وتحفظ النتيجة PNG شفافة.
    تعيد True فقط عند عزل حقيقي؛ عند الفشل أو الطريقة 'none' تعيد False ولا تكتب نسخة من الأصل.
    """
    try:
        with open(input_path, "rb") as fh:
            img, _ = _decode_image(fh.read())
    except OSError:
        return False
    if img is None:
        return False
    method = _normalise_method(bg_method)
    if method == "none":
        return False
    if has_meaningful_transparency(img):
        img.save(output_path, format="PNG")
        return True
    cutout, error = _isolate(_limit_work_size(img), method)
    if cutout is None:
        logger.warning("فشل عزل الخلفية (%s): %s", method, error)
        return False
    EdgeShadowEngine.process_mask(cutout).save(output_path, format="PNG")
    return True


def enhance_image_quality(image_path, output_path):
    """
    تحسين خفيف للتباين والألوان على قنوات RGB فقط وبعمليات متجهة.
    (تم حذف حلقة مسح الأطراف التي كانت تقص 5% من كل جانب من المنتج.)
    """
    try:
        with open(image_path, "rb") as fh:
            img, _ = _decode_image(fh.read())
        if img is None:
            return False
        _save_lossless(_enhance_rgb(img), output_path)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("خطأ أثناء تحسين الصورة: %s", exc)
        return False


# ---------------------------------------------------------------------------
# استخراج البيانات الوصفية
# ---------------------------------------------------------------------------

_NULL_STRINGS = {"", "null", "none", "n/a", "na", "not visible", "not legible", "unknown", "-"}
_NULLABLE_FIELDS = ("nutrition", "ingredients", "tags_en", "tags_ar", "description_en", "description_ar")


def build_metadata_prompt(product_name, brand) -> str:
    """نص الطلب لاستخراج البيانات الوصفية؛ يمنع اختلاق القيم الغذائية والمكونات."""
    taxonomy_lines = []
    for l1_en, l1_data in categories.CATEGORIES.items():
        for l2_en, l2_data in l1_data["subs"].items():
            for l3_en, l3_ar in l2_data["sub_subs"].items():
                taxonomy_lines.append(
                    f"- {l1_en} ({l1_data['ar']}) > {l2_en} ({l2_data['ar']}) > {l3_en} ({l3_ar})")
    taxonomy_str = "\n".join(taxonomy_lines)
    return (
        f"You are an e-commerce catalog assistant. Analyse this product package image for '{brand} - {product_name}'.\n"
        "Honesty rules (mandatory):\n"
        "- Report nutrition facts, ingredients and allergens ONLY if that text is clearly legible in THIS image.\n"
        "- If it is not legible (for example the panel is on another side of the pack, too small or blurred), "
        "return null for that field. Never guess, estimate, or use typical values for this kind of product.\n"
        "- The marketing descriptions must not state nutrition values, ingredients, allergens or health claims "
        "unless they are legible in the image.\n"
        "Tasks:\n"
        "1. nutrition: the Nutrition Facts exactly as printed (keep the printed basis, e.g. per serving or per 100 ml), "
        "as a short English summary, or null.\n"
        "2. ingredients: the ingredients list and any allergen statement exactly as printed, or null.\n"
        "3. description_en: a short, factual e-commerce description in English.\n"
        "4. description_ar: the same description in Arabic.\n"
        "5. A 3-level category path (L1 > L2 > L3) chosen strictly from this taxonomy:\n"
        f"{taxonomy_str}\n"
        "6. tags_en / tags_ar: 3 to 6 comma-separated attributes that are printed on the pack or obvious from the "
        "product type, in English and Arabic, or null.\n\n"
        "Reply strictly as JSON with exactly these keys. Use JSON null (not a string) for any value you cannot "
        "read from the image:\n"
        "{\n"
        '  "nutrition": string or null,\n'
        '  "ingredients": string or null,\n'
        '  "description_en": string or null,\n'
        '  "description_ar": string or null,\n'
        '  "category_l1_en": string, "category_l2_en": string, "category_l3_en": string,\n'
        '  "category_l1_ar": string, "category_l2_ar": string, "category_l3_ar": string,\n'
        '  "tags_en": string or null,\n'
        '  "tags_ar": string or null\n'
        "}"
    )


def _clean_metadata(result: dict) -> dict:
    for key in _NULLABLE_FIELDS:
        value = result.get(key)
        if value is None:
            continue
        if not isinstance(value, str) or value.strip().lower() in _NULL_STRINGS:
            result[key] = None
        else:
            result[key] = value.strip()
    return result


def extract_metadata_from_image(image_path, product_name, brand):
    """
    استخراج القيم الغذائية والمكونات (فقط إن كانت مقروءة) والوصف والتصنيفات عبر Gemini.
    تُرسل الصورة بدقة 1024 بكسل. يعيد dict أو None.
    """
    api_key = settings.gemini_api_key()
    if not api_key:
        return None
    try:
        with open(image_path, "rb") as fh:
            img, _ = _decode_image(fh.read())
        if img is None:
            return None
        if img.mode == "RGBA":
            flat = Image.new("RGB", img.size, (255, 255, 255))
            flat.paste(img, mask=img.getchannel("A"))
            img = flat
        img.thumbnail((METADATA_IMAGE_SIDE, METADATA_IMAGE_SIDE), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=90)

        payload = {
            "contents": [{"parts": [
                {"text": build_metadata_prompt(product_name, brand)},
                {"inlineData": {"mimeType": "image/jpeg", "data": base64.b64encode(buf.getvalue()).decode("ascii")}},
            ]}],
            "generationConfig": {"responseMimeType": "application/json"},
        }
        _count_gemini_call()
        response = requests.post(
            GEMINI_URL.format(model=settings.gemini_model()),
            headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
            json=payload,
            timeout=60,
        )
        if response.status_code != 200:
            logger.warning("فشل استخراج البيانات الوصفية عبر Gemini (كود %s)", response.status_code)
            return None
        text = response.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.lower().startswith("json"):
                text = text[4:]
        result = json.loads(text.strip())
        if not isinstance(result, dict):
            return None
        result = _clean_metadata(result)
        result.update(categories.normalize_category_path(
            result.get("category_l1_en") or "",
            result.get("category_l2_en") or "",
            result.get("category_l3_en") or "",
        ))
        return result
    except Exception as exc:  # noqa: BLE001
        logger.warning("خطأ أثناء استخراج البيانات الوصفية عبر Gemini: %s", exc)
        return None


# ---------------------------------------------------------------------------
# دالة قديمة مؤجل حذفها (راجع قرار التأجيل في الخطة؛ مستخدمة في الاختبارات فقط)
# ---------------------------------------------------------------------------

def optimize_and_center_product_image(source_path: str, destination_path: str, canvas_dimension: int = 1000) -> str:
    """
    تحسين وتوسيط صورة المنتج وتوليد ظلال طبيعية وحفظها بصيغة WebP موحدة:
    - بناء لوحة مربعة 1:1 بأبعاد 1000px.
    - هامش أمان 12% (تعبئة المنتج 88%).
    - توليد ظل واقعي ناعم ببيانات الشفافية.
    - تصدير بضغط WebP عالي الجودة (quality=85).
    """
    try:
        import numpy as np
        import cv2
        from PIL import ImageFilter

        original_image = Image.open(source_path).convert("RGBA")

        # عزل المقدمة إذا لم تكن تحتوي على قناة شفافية مفعلة
        img_np = np.array(original_image)
        alpha = img_np[:, :, 3]

        if np.all(alpha == 255):
            # استخدام OpenCV GrabCut الخفيف كبديل محلي آمن بدون نماذج ثقيلة
            bgr = cv2.cvtColor(img_np[:, :, :3], cv2.COLOR_RGB2BGR)
            h, w = bgr.shape[:2]
            mask = np.zeros((h, w), np.uint8)
            bgdModel = np.zeros((1, 65), np.float64)
            fgdModel = np.zeros((1, 65), np.float64)
            rect = (max(1, int(w * 0.05)), max(1, int(h * 0.05)), int(w * 0.90), int(h * 0.90))
            cv2.grabCut(bgr, mask, rect, bgdModel, fgdModel, 3, cv2.GC_INIT_WITH_RECT)
            bin_mask = np.where((mask == 2) | (mask == 0), 0, 255).astype(np.uint8)
            foreground_extracted = original_image.copy()
            foreground_extracted.putalpha(Image.fromarray(bin_mask))
        else:
            foreground_extracted = original_image

        # قص المربع المحيط بالمنتج
        bounding_box = foreground_extracted.getbbox()
        if bounding_box:
            cropped_foreground = foreground_extracted.crop(bounding_box)
        else:
            cropped_foreground = foreground_extracted

        crop_w, crop_h = cropped_foreground.size

        # بناء اللوحة المربعة 1000x1000
        production_canvas = Image.new("RGBA", (canvas_dimension, canvas_dimension), (255, 255, 255, 255))

        # حساب الحجم بنسبة تعبئة 88% (هامش أمان 12%)
        max_internal_size = int(canvas_dimension * 0.88)
        scaling_ratio = min(max_internal_size / float(crop_w), max_internal_size / float(crop_h))
        scaled_width, scaled_height = max(1, int(crop_w * scaling_ratio)), max(1, int(crop_h * scaling_ratio))

        resized_product = cropped_foreground.resize((scaled_width, scaled_height), Image.Resampling.LANCZOS)

        # توليد الظل الواقعي الناعم عبر قناة الشفافية
        product_alpha = resized_product.split()[-1]
        blur_radius = 20
        blurred_shadow_alpha = product_alpha.filter(ImageFilter.GaussianBlur(blur_radius))

        shadow_layer = Image.new("RGBA", (scaled_width, scaled_height), (40, 40, 40, 110))
        shadow_layer.putalpha(blurred_shadow_alpha)

        shadow_offset = (12, 18)
        center_x = (canvas_dimension - scaled_width) // 2
        center_y = (canvas_dimension - scaled_height) // 2

        production_canvas.paste(
            shadow_layer,
            (center_x + shadow_offset[0], center_y + shadow_offset[1]),
            shadow_layer
        )
        production_canvas.paste(resized_product, (center_x, center_y), resized_product)

        final_rgb = production_canvas.convert("RGB")
        os.makedirs(os.path.dirname(destination_path), exist_ok=True)
        final_rgb.save(
            destination_path,
            format="WEBP",
            quality=85,
            method=6
        )
        return destination_path
    except Exception as e:
        logger.warning("[Image Optimization Error] Fallback to raw copy: %s", e)
        try:
            with Image.open(source_path) as img:
                img.convert("RGB").save(destination_path, format="WEBP", quality=85)
            return destination_path
        except Exception:
            return source_path
