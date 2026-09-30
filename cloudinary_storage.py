# cloudinary_storage.py
# رفع لوحة المنتج النهائية (800x800 بيضاء معتمة) إلى Cloudinary وتوليد رابط التسليم.
#
# - اللوحة جاهزة محلياً، لذلك رابط التسليم يحتوي فقط q_auto,f_auto
#   (بدون e_trim / c_fit / c_pad / e_sharpen، وبدون مشتقات eager).
# - مهلة 60 ثانية لكل رفع، وإعادة المحاولة مرتين عند الاستثناءات المؤقتة أو أخطاء 5xx.
# - مقاطع المجلد تحوَّل إلى [a-z0-9_-] فقط ('100% Juice' -> '100_juice').

import hashlib
import io
import logging
import os
import re
import time
from typing import Iterable, Optional

import cloudinary
import cloudinary.exceptions
import cloudinary.uploader
import cloudinary.utils

import config

logger = logging.getLogger(__name__)

UPLOAD_TIMEOUT_SECONDS = 60
UPLOAD_RETRIES = 2
RETRY_BACKOFF_SECONDS = 1.5
DELIVERY_TRANSFORMATION = "q_auto,f_auto"
DEFAULT_FOLDER = "products"

# أخطاء نهائية من جهة العميل (400/401/403/404/409): إعادة المحاولة لن تغير النتيجة
_NON_RETRYABLE = (
    cloudinary.exceptions.BadRequest,
    cloudinary.exceptions.AuthorizationRequired,
    cloudinary.exceptions.NotAllowed,
    cloudinary.exceptions.NotFound,
    cloudinary.exceptions.AlreadyExists,
)

# تهيئة إعدادات Cloudinary
cloudinary.config(
    cloud_name=config.CLOUDINARY_CLOUD_NAME,
    api_key=config.CLOUDINARY_API_KEY,
    api_secret=config.CLOUDINARY_API_SECRET,
    secure=True,
)


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


def slugify_segment(text) -> str:
    """مقطع مجلد آمن: أحرف صغيرة [a-z0-9_-] فقط، بدون فواصل علوية، و'&' تصبح 'and'."""
    value = str(text or "").strip().lower()
    value = re.sub(r"['’‘`]", "", value)
    value = value.replace("&", " and ")
    value = re.sub(r"[^a-z0-9_-]", "_", value)
    value = re.sub(r"_+", "_", value)
    return value.strip("_-")


def slugify_folder(folder) -> str:
    """يطبّق slugify_segment على كل مقطع من مسار المجلد ويحذف المقاطع الفارغة."""
    parts = re.split(r"[\\/]+", str(folder or ""))
    segments = [s for s in (slugify_segment(p) for p in parts) if s]
    return "/".join(segments) or DEFAULT_FOLDER


def product_folder(category_l1=None, category_l2=None, root: str = DEFAULT_FOLDER) -> str:
    """مجلد المنتج من التصنيف: products/<l1>/<l2> بمقاطع آمنة."""
    return slugify_folder("/".join(str(p) for p in (root, category_l1, category_l2) if p))


def _prepare_payload(local_path: str):
    """
    يقرأ الملف ويتأكد أنه صورة. إذا كان يحتوي شفافية (مثلاً من مسار الرفع اليدوي القديم)
    يتم تسطيحه محلياً على لوحة بيضاء معتمة بالحجم القياسي، لأن التسليم لم يعد يضيف خلفية.
    يعيد (مصدر الرفع، البيانات) أو (None، None) إذا لم يكن الملف صورة.
    """
    from PIL import Image

    with open(local_path, "rb") as fh:
        data = fh.read()
    try:
        with Image.open(io.BytesIO(data)) as img:
            img.load()
            has_alpha = img.mode in ("RGBA", "LA", "PA") or "transparency" in img.info
            rgba = img.convert("RGBA") if has_alpha else None
    except Exception as exc:  # noqa: BLE001
        logger.error("[Cloudinary] الملف ليس صورة صالحة ولن يتم رفعه: %s (%s)", local_path, exc)
        return None, None

    if rgba is None or rgba.getchannel("A").getextrema()[0] == 255:
        return local_path, data

    from catalog_match import settings
    from edge_shadow_engine import compose_on_white_canvas

    side = settings.output_canvas_size()
    try:
        canvas = compose_on_white_canvas(rgba, (side, side))
    except ValueError as exc:  # صورة شفافة بالكامل: لا يوجد منتج لنشره
        logger.error("[Cloudinary] الصورة لا تحتوي منتجاً مرئياً ولن يتم رفعها: %s (%s)", local_path, exc)
        return None, None
    buf = io.BytesIO()
    canvas.save(buf, format="PNG")
    logger.warning("[Cloudinary] الصورة تحتوي شفافية؛ تم تسطيحها على لوحة بيضاء %dx%d قبل الرفع", side, side)
    flat = buf.getvalue()
    return io.BytesIO(flat), flat


def _upload_with_retries(source, options: dict):
    attempts = 1 + UPLOAD_RETRIES
    for attempt in range(attempts):
        is_last = attempt == attempts - 1
        if hasattr(source, "seek"):
            source.seek(0)
        try:
            response = cloudinary.uploader.upload(source, **options)
            if not isinstance(response, dict) or not response.get("public_id"):
                raise cloudinary.exceptions.Error("upload response without public_id")
            return response
        except _NON_RETRYABLE as exc:
            logger.error("[Cloudinary] رفض نهائي للرفع (بدون إعادة محاولة): %s", exc)
            return None
        except Exception as exc:  # noqa: BLE001 - أخطاء الشبكة و5xx تعاد محاولتها
            if is_last:
                logger.error("[Cloudinary] فشل الرفع بعد %d محاولات: %s", attempts, exc)
                return None
            logger.warning("[Cloudinary] خطأ مؤقت أثناء الرفع (%s)، إعادة المحاولة %d/%d",
                           exc, attempt + 1, UPLOAD_RETRIES)
            _sleep(RETRY_BACKOFF_SECONDS * (2 ** attempt))
    return None


def delivery_url(public_id: str, version=None) -> str:
    """رابط التسليم النهائي: q_auto,f_auto فقط (اللوحة نهائية ولا تحتاج تحويلات هندسية)."""
    url, _ = cloudinary.utils.cloudinary_url(
        public_id,
        secure=True,
        version=version,
        raw_transformation=DELIVERY_TRANSFORMATION,
    )
    return url


def upload_product_image_to_cloudinary(local_path, product_name, brand, folder=None, tags: Optional[Iterable[str]] = None,
                                       target_width=800, target_height=800, padding_ratio=0.85, bg_color="ffffff"):
    """
    يرفع اللوحة النهائية ويعيد رابط التسليم أو None.
    target_width / target_height / padding_ratio / bg_color مقبولة للتوافق فقط ولا أثر لها:
    اللوحة (الأبعاد، الإشغال 88%، الخلفية البيضاء) تُبنى محلياً في image_processor.
    """
    if not local_path or not os.path.exists(local_path):
        logger.error("[Cloudinary] ملف الصورة المحلي غير موجود: %s", local_path)
        return None

    try:
        source, data = _prepare_payload(local_path)
    except OSError as exc:
        logger.error("[Cloudinary] تعذر قراءة الملف المحلي: %s", exc)
        return None
    if source is None:
        return None

    options = {
        "public_id": hashlib.md5(data).hexdigest(),
        "folder": slugify_folder(folder or DEFAULT_FOLDER),
        "overwrite": False,
        "invalidate": True,
        "resource_type": "image",
        "timeout": UPLOAD_TIMEOUT_SECONDS,
    }
    tag_list = [str(t).strip() for t in (tags or []) if str(t).strip()]
    if tag_list:
        options["tags"] = tag_list

    response = _upload_with_retries(source, options)
    if response is None:
        return None

    url = delivery_url(response["public_id"], response.get("version"))
    try:
        config.METRICS["cloudinary_uploads"] += 1
    except Exception:  # noqa: BLE001
        pass
    logger.info("[Cloudinary] تم الرفع: %s", url)
    return url
