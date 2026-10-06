# delivery_urls.py
# روابط تسليم Cloudinary اللي منكتبها بالشيت: شكلها ومقارنتها وتحويلها، نص بس (بلا Cloudinary SDK وبلا شبكة)، فبيستعملها
# الرفع (cloudinary_storage) والمقارنات (local_cache_db.url_norm و cli_bridge) وسكربت الترحيل بدون أي إعداد.
#
# - الرابط الجديد: https://res.cloudinary.com/<الحساب>/image/upload/c_limit,w_1200,f_webp,q_auto/v<النسخة>/<المعرّف>
#   f_webp مش f_auto: التطبيق الأصلي (okhttp بـ Accept: image/*، CFNetwork، Dart) بياخد من f_auto صورة JPEG بلا شفافية
#   (مربع أبيض بالوضع الغامق)، و f_webp بيحفظ الشفافية لكل العملاء. c_limit بيصغّر الأعرض من 1200 بس، وما بيكبّر.
# - الرابط القديم (q_auto,f_auto) لنفس الأصل والنسخة هو نفس الصورة: المقارنات بتعتبرهم واحد (canonical_delivery_url)،
#   والنسخة البيضا بتنطلع من الاتنين، و migrated_delivery_url بيحوّل القديم للجديد (scripts/migrate_delivery_urls.py).
# - رابط مش إلنا (رابط متجر، Cloudinary بتحويل تاني، مضيف تاني) ما بيتغيّر أبداً.

from typing import List, Optional, Tuple

# أعرض صورة بيسلّمها الرابط (بكسل): شاشة موبايل 3x بعرض كامل ~1170. اللوحة الأصلية ممكن تكون أكبر (OUTPUT_CANVAS_MAX)
DELIVERY_MAX_WIDTH = 1200
DELIVERY_TRANSFORMATION = f"c_limit,w_{DELIVERY_MAX_WIDTH},f_webp,q_auto"
# روابط تسليم قديمة لنفس الأصول (مكتوبة بالشيت وبقاعدة البيانات قبل f_webp)
LEGACY_DELIVERY_TRANSFORMATIONS = ("q_auto,f_auto",)
# النسخة البيضا المعتمة من نفس الأصل الشفاف (للتطبيق أو أي مكان بده مربع أبيض): الخلفية بيضا، و JPEG ما فيه شفافية
WHITE_TRANSFORMATION = f"b_white,c_limit,w_{DELIVERY_MAX_WIDTH},f_jpg,q_auto"
LEGACY_WHITE_TRANSFORMATIONS = ("b_white,q_auto,f_jpg",)

DELIVERY_FORMS = (DELIVERY_TRANSFORMATION,) + LEGACY_DELIVERY_TRANSFORMATIONS
WHITE_FORMS = (WHITE_TRANSFORMATION,) + LEGACY_WHITE_TRANSFORMATIONS
CLOUDINARY_HOST = "res.cloudinary.com"
REVIEW_PREFIX = "needs_review:"
_UPLOAD_MARKER = "/image/upload/"


def bare_link(url) -> str:
    """الرابط بلا مسافات وبلا بادئة needs_review: (خلايا قديمة بالشيت كانت مراجعة معلقة)."""
    text = str(url or "").strip()
    if text.startswith(REVIEW_PREFIX):
        text = text[len(REVIEW_PREFIX):].strip()
    return text


def split_delivery_url(url) -> Optional[Tuple[str, str, str]]:
    """
    (https://res.cloudinary.com/<الحساب>/image/upload/، التحويل، النسخة والمعرّف) لرابط تسليم إلنا: تحويل التسليم الجديد
    أو قديم، أو النسخة البيضا (جديدة أو قديمة). None لأي رابط تاني. البادئة needs_review: بتنشال قبل الفحص.
    """
    text = bare_link(url)
    head_end = text.find(_UPLOAD_MARKER)
    if head_end < 0:
        return None
    head = text[:head_end + len(_UPLOAD_MARKER)]
    scheme, sep, rest = head.partition("://")
    if not sep or scheme.lower() not in ("https", "http"):
        return None
    host, _, account = rest.partition("/")
    if host.lower() != CLOUDINARY_HOST or not account.strip("/") or "/" in account.strip("/").split("/image")[0]:
        return None
    component, sep, tail = text[len(head):].partition("/")
    if not sep or not tail.strip("/") or component not in DELIVERY_FORMS + WHITE_FORMS:
        return None
    return head, component, tail


def cloud_name(url) -> str:
    """اسم حساب Cloudinary من رابط تسليم إلنا، أو ''."""
    parts = split_delivery_url(url)
    if parts is None:
        return ""
    return parts[0].partition("://")[2].split("/")[1]


def is_delivery_url(url) -> bool:
    """رابط تسليم إلنا (جديد أو قديم، مش النسخة البيضا)."""
    parts = split_delivery_url(url)
    return parts is not None and parts[1] in DELIVERY_FORMS


def canonical_delivery_url(url) -> str:
    """
    الرابط للمقارنة: رابط تسليم إلنا (قديم أو جديد) بالتحويل الجديد، بلا بادئة needs_review:؛ أي رابط تاني كما هو (بلا
    مسافات). رابطان لنفس الأصل والنسخة بيعطوا نفس النتيجة.
    """
    text = bare_link(url)
    parts = split_delivery_url(text)
    if parts is None or parts[1] not in DELIVERY_FORMS:
        return text
    head, _, tail = parts
    return f"{head}{DELIVERY_TRANSFORMATION}/{tail}"


def delivery_variants(url) -> List[str]:
    """
    كل روابط التسليم لنفس الأصل (الرابط نفسه أول شي، ثم الجديد ثم القديمة): للبحث بقاعدة البيانات عن رابط محفوظ بشكل
    تاني لنفس الصورة. رابط مش رابط تسليم إلنا: [الرابط] بس.
    """
    text = bare_link(url)
    parts = split_delivery_url(text)
    if parts is None or parts[1] not in DELIVERY_FORMS:
        return [text] if text else []
    head, _, tail = parts
    return list(dict.fromkeys([text] + [f"{head}{form}/{tail}" for form in DELIVERY_FORMS]))


def same_delivery_asset(a, b) -> bool:
    """هل الرابطان نفس الصورة؟ متطابقين، أو رابطا تسليم لنفس الأصل بتحويل جديد وقديم (البادئة needs_review: ما بتفرق)."""
    a, b = canonical_delivery_url(a), canonical_delivery_url(b)
    return bool(a) and a == b


def migrated_delivery_url(url) -> Optional[str]:
    """
    الرابط القديم (q_auto,f_auto) بالتحويل الجديد لنفس الأصل والنسخة، أو None إذا الرابط مش رابط تسليم قديم إلنا (جديد من
    قبل، أو مش إلنا، أو فيه بادئة أو مسافات: الخلية ما بتتغيّر). تبديل نص بس: بلا رفع جديد.
    """
    text = str(url or "")
    if not text or text != text.strip() or text != bare_link(text):
        return None
    parts = split_delivery_url(text)
    if parts is None or parts[1] not in LEGACY_DELIVERY_TRANSFORMATIONS:
        return None
    head, _, tail = parts
    return f"{head}{DELIVERY_TRANSFORMATION}/{tail}"


def white_version_url(url) -> Optional[str]:
    """
    رابط النسخة البيضا المعتمة (JPEG) من رابط تسليم إلنا لنفس الأصل، الجديد (c_limit,w_1200,f_webp,q_auto) أو القديم
    (q_auto,f_auto): التحويل بيصير b_white,c_limit,w_1200,f_jpg,q_auto. التطبيق بيطلبها هيك متى بده مربع أبيض (مشاركة،
    طباعة). None إذا الرابط مش رابط تسليم منعرفه.
    """
    parts = split_delivery_url(url)
    if parts is None or parts[1] not in DELIVERY_FORMS:
        return None
    head, _, tail = parts
    return f"{head}{WHITE_TRANSFORMATION}/{tail}"
