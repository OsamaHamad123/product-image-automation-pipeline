# cloudinary_storage.py
# رفع لوحة المنتج النهائية (800x800 بيضاء معتمة) إلى Cloudinary وتوليد رابط التسليم.
#
# - اللوحة جاهزة محلياً، لذلك رابط التسليم يحتوي فقط q_auto,f_auto
#   (بدون e_trim / c_fit / c_pad / e_sharpen، وبدون مشتقات eager).
# - مهلة 60 ثانية لكل رفع، وإعادة المحاولة مرتين عند الاستثناءات المؤقتة أو أخطاء 5xx.
# - مقاطع المجلد تحوَّل إلى [a-z0-9_-] فقط ('100% Juice' -> '100_juice').
# - استجابة الرفع تُفحص: الحجم بالبايت والأبعاد و etag (بصمة md5) يجب أن تطابق ما أُرسل عندما تذكرها
#   Cloudinary؛ أي اختلاف يعني أن الأصل المخزن ليس الصورة التي تحققنا منها فلا يُعاد رابطه.
# - existing=True في الاستجابة يعني أن نفس البايتات مرفوعة سابقاً (public_id = md5 البايتات)؛ تُكشف
#   للمستدعي عبر upload_product_image() ليعرف إذا نُشرت نفس الصورة لمنتجين.

import hashlib
import io
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Iterable, Optional, Tuple

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


@dataclass
class UploadResult:
    """
    نتيجة رفع اللوحة.
    url: رابط التسليم، أو None عند أي فشل (بما فيه استجابة لا تطابق ما أُرسل).
    public_id: معرّف الأصل في Cloudinary.
    existing: True إذا كانت نفس البايتات مرفوعة سابقاً في نفس المجلد (Cloudinary أعاد الأصل الموجود).
    content_md5: بصمة md5 للبايتات المرسلة (نفسها اسم الأصل)؛ تكشف نفس الصورة عبر مجلدات مختلفة.
    error: upload_file_missing | upload_not_image | upload_failed | upload_bytes_mismatch |
           upload_size_mismatch | upload_etag_mismatch، أو None.
    cause: اسم صنف آخر استثناء أفشل الرفع (مثل AuthorizationRequired: المفتاح مرفوض)، مع upload_failed فقط؛
           فحص النشر (publish_check) يقول منه ما العمل.
    """

    url: Optional[str]
    public_id: Optional[str] = None
    existing: bool = False
    content_md5: Optional[str] = None
    error: Optional[str] = None
    cause: Optional[str] = None


_MD5_RE = re.compile(r"^[0-9a-f]{32}$")


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
    يعيد (مصدر الرفع، البيانات، (العرض، الارتفاع)) أو (None، None، None) إذا لم يكن الملف صورة.
    """
    from PIL import Image

    with open(local_path, "rb") as fh:
        data = fh.read()
    try:
        with Image.open(io.BytesIO(data)) as img:
            img.load()
            size = img.size
            has_alpha = img.mode in ("RGBA", "LA", "PA") or "transparency" in img.info
            rgba = img.convert("RGBA") if has_alpha else None
    except Exception as exc:  # noqa: BLE001
        logger.error("[Cloudinary] الملف ليس صورة صالحة ولن يتم رفعه: %s (%s)", local_path, exc)
        return None, None, None

    if rgba is None or rgba.getchannel("A").getextrema()[0] == 255:
        return local_path, data, size

    from catalog_match import settings
    from edge_shadow_engine import compose_on_white_canvas

    side = settings.output_canvas_size()
    try:
        canvas = compose_on_white_canvas(rgba, (side, side))
    except ValueError as exc:  # صورة شفافة بالكامل: لا يوجد منتج لنشره
        logger.error("[Cloudinary] الصورة لا تحتوي منتجاً مرئياً ولن يتم رفعها: %s (%s)", local_path, exc)
        return None, None, None
    buf = io.BytesIO()
    canvas.save(buf, format="PNG")
    logger.warning("[Cloudinary] الصورة تحتوي شفافية؛ تم تسطيحها على لوحة بيضاء %dx%d قبل الرفع", side, side)
    flat = buf.getvalue()
    return io.BytesIO(flat), flat, canvas.size


def _response_int(response: dict, key: str) -> Optional[int]:
    value = response.get(key)
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _verify_upload(response: dict, data: bytes, size: Tuple[int, int], md5: str) -> Optional[str]:
    """
    يطابق ما قالته Cloudinary عن الأصل المخزن مع ما أُرسل: الحجم بالبايت، الأبعاد، و etag (md5 البايتات).
    حقل غائب أو غير مقروء لا يُفشل الرفع (السلوك الحالي)؛ حقل موجود ومختلف يعيد رمز خطأ.
    """
    stored = _response_int(response, "bytes")
    if stored is not None and stored != len(data):
        return "upload_bytes_mismatch"
    width, height = _response_int(response, "width"), _response_int(response, "height")
    if (width is not None and width != size[0]) or (height is not None and height != size[1]):
        return "upload_size_mismatch"
    etag = str(response.get("etag") or "").strip().strip('"').lower()
    if _MD5_RE.match(etag) and etag != md5:
        return "upload_etag_mismatch"
    return None


def _upload_with_retries(source, options: dict, failure: Optional[dict] = None):
    """الاستجابة، أو None عند الفشل؛ failure (قاموس اختياري) يأخذ آخر استثناء في 'exception'."""
    failure = failure if failure is not None else {}
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
            failure["exception"] = exc
            logger.error("[Cloudinary] رفض نهائي للرفع (بدون إعادة محاولة): %s", exc)
            return None
        except Exception as exc:  # noqa: BLE001 - أخطاء الشبكة و5xx تعاد محاولتها
            failure["exception"] = exc
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


def upload_product_image(local_path, product_name, brand, folder=None,
                         tags: Optional[Iterable[str]] = None, *, public_id=None, overwrite=False,
                         timeout=None) -> UploadResult:
    """
    يرفع اللوحة النهائية ويتحقق من استجابة Cloudinary، ويعيد UploadResult (url=None عند أي فشل).
    existing=True: نفس البايتات مرفوعة سابقاً في نفس المجلد (قد تكون صورة منتج آخر).
    public_id / overwrite / timeout لفحص النشر فقط (upload_selftest_image: اسم ثابت يُستبدل في مجلد الفحص)؛ النشر
    يتركها: الاسم md5 البايتات، بلا استبدال، ومهلة UPLOAD_TIMEOUT_SECONDS.
    """
    if not local_path or not os.path.exists(local_path):
        logger.error("[Cloudinary] ملف الصورة المحلي غير موجود: %s", local_path)
        return UploadResult(None, error="upload_file_missing")

    try:
        source, data, size = _prepare_payload(local_path)
    except OSError as exc:
        logger.error("[Cloudinary] تعذر قراءة الملف المحلي: %s", exc)
        return UploadResult(None, error="upload_file_missing")
    if source is None:
        return UploadResult(None, error="upload_not_image")

    md5 = hashlib.md5(data).hexdigest()
    options = {
        "public_id": public_id or md5,
        "folder": slugify_folder(folder or DEFAULT_FOLDER),
        "overwrite": bool(overwrite),
        "invalidate": True,
        "resource_type": "image",
        "timeout": timeout or UPLOAD_TIMEOUT_SECONDS,
    }
    tag_list = [str(t).strip() for t in (tags or []) if str(t).strip()]
    if tag_list:
        options["tags"] = tag_list

    failure = {}
    response = _upload_with_retries(source, options, failure)
    if response is None:
        cause = type(failure["exception"]).__name__ if failure.get("exception") is not None else None
        return UploadResult(None, content_md5=md5, error="upload_failed", cause=cause)

    existing = response.get("existing") is True or str(response.get("existing")).strip().lower() == "true"
    mismatch = _verify_upload(response, data, size, md5)
    if mismatch:
        logger.error("[Cloudinary] الأصل المخزن لا يطابق الصورة المرسلة (%s، existing=%s، public_id=%s)؛ "
                     "لن يُستخدم رابطه. إذا تكرر هذا لكل رفع فتحقق من عدم وجود upload preset افتراضي يعدّل الصور.",
                     mismatch, existing, response.get("public_id"))
        return UploadResult(None, public_id=response.get("public_id"), existing=existing, content_md5=md5,
                            error=mismatch)

    url = delivery_url(response["public_id"], response.get("version"))
    try:
        config.METRICS["cloudinary_uploads"] += 1
    except Exception:  # noqa: BLE001
        pass
    if existing:
        logger.warning("[Cloudinary] نفس البايتات مرفوعة سابقاً (existing)؛ أُعيد الأصل الموجود: %s", url)
    else:
        logger.info("[Cloudinary] تم الرفع: %s", url)
    return UploadResult(url, public_id=response["public_id"], existing=existing, content_md5=md5)


def upload_product_image_to_cloudinary(local_path, product_name, brand, folder=None, tags: Optional[Iterable[str]] = None,
                                       target_width=800, target_height=800, padding_ratio=0.85, bg_color="ffffff"):
    """
    يرفع اللوحة النهائية ويعيد رابط التسليم أو None (واجهة متوافقة؛ upload_product_image تعيد التفاصيل).
    target_width / target_height / padding_ratio / bg_color مقبولة للتوافق فقط ولا أثر لها:
    اللوحة (الأبعاد، الإشغال 88%، الخلفية البيضاء) تُبنى محلياً في image_processor.
    """
    return upload_product_image(local_path, product_name, brand, folder=folder, tags=tags).url


# ---------------------------------------------------------------------------
# فحص النشر (publish_check): رفع اللوحة التجريبية إلى مكان ثابت ثم مسحها، بنفس مسار رفع النشر
# ---------------------------------------------------------------------------

SELFTEST_FOLDER = "laqta_selftest"
SELFTEST_PUBLIC_ID = "publish_check"
SELFTEST_TIMEOUT_SECONDS = 20
# المعرّفات الوحيدة التي يقبل destroy_selftest_image مسحها: مع المجلدات الثابتة يحمل المعرّف اسم المجلد، ومع
# المجلدات الديناميكية قد يعيده Cloudinary بلا مجلد. صور المنتجات اسمها md5 بايتاتها فلا تطابق أياً منهما.
SELFTEST_PUBLIC_IDS = (f"{SELFTEST_FOLDER}/{SELFTEST_PUBLIC_ID}", SELFTEST_PUBLIC_ID)


def upload_selftest_image(local_path) -> UploadResult:
    """
    يرفع لوحة فحص النشر بـ upload_product_image نفسها (نفس التجهيز والإعادة والتحقق من الاستجابة) إلى
    laqta_selftest/publish_check، باسم ثابت يُستبدل كل مرة: لا تتراكم صور تجريبية حتى لو تعذر مسح إحداها.
    """
    return upload_product_image(local_path, "publish check", "", folder=SELFTEST_FOLDER,
                                public_id=SELFTEST_PUBLIC_ID, overwrite=True, timeout=SELFTEST_TIMEOUT_SECONDS)


def destroy_selftest_image(public_id) -> Optional[str]:
    """
    يمسح صورة فحص النشر من Cloudinary. يرفض أي معرّف غير SELFTEST_PUBLIC_IDS (لا يلمس صورة منتج أبداً).
    يعيد None عند المسح، أو رمزاً: destroy_refused | destroy_not_found | destroy_<اسم الاستثناء>.
    """
    if str(public_id or "") not in SELFTEST_PUBLIC_IDS:
        logger.error("[Cloudinary] رُفض مسح معرّف ليس صورة فحص النشر: %s", public_id)
        return "destroy_refused"
    try:
        response = cloudinary.uploader.destroy(public_id, resource_type="image", invalidate=True,
                                               timeout=SELFTEST_TIMEOUT_SECONDS)
    except Exception as exc:  # noqa: BLE001 - يُبلَّغ كرمز؛ فحص النشر يقول ما العمل
        logger.warning("[Cloudinary] تعذر مسح صورة فحص النشر: %s", exc)
        return f"destroy_{type(exc).__name__}"
    result = str((response or {}).get("result") or "") if isinstance(response, dict) else ""
    if result == "ok":
        return None
    return "destroy_not_found" if result == "not found" else "destroy_failed"
