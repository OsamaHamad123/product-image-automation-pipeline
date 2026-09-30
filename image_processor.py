# image_processor.py
# معالجة صورة المنتج المعتمدة وتحويلها إلى لوحة نشر نهائية:
# تحميل (أو قراءة من مخزن المرشحات) -> تصحيح اتجاه EXIF -> اقتصاص Gemini اختياري ومتحقق منه
# -> عزل الخلفية بالطريقة الممررة كمعامل -> تركيب على لوحة بيضاء معتمة ثابتة الأبعاد (افتراضياً 800x800).
#
# المبادئ الملزمة:
# - فشل عزل الخلفية يعيد isolated=False مع رمز خطأ، ولا يعيد الصورة الخام أبداً كأنها نجاح.
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
import types
import uuid
from dataclasses import dataclass
from typing import List, Optional, Tuple

import requests
from PIL import Image, ImageEnhance, ImageOps

import categories
import config
from catalog_match import settings
from edge_shadow_engine import (
    CANVAS_FILL_RATIO,
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
    isolated: True فقط إذا تم عزل المنتج عن خلفيته فعلاً.
    provider: photoroom | remove_bg_api | grabcut | rembg | source_alpha | none | ...
    error: رمز خطأ واضح (مثل photoroom_402، download_not_image) أو None.
    width/height: أبعاد اللوحة النهائية (0 عند الفشل).
    """

    path: Optional[str]
    isolated: bool
    provider: str
    error: Optional[str] = None
    width: int = 0
    height: int = 0


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


def _download_bytes(url: str) -> Tuple[Optional[bytes], Optional[str]]:
    from http_client import ImpersonateClient

    proxy = settings.proxy_url()
    client = ImpersonateClient(use_proxy=bool(proxy), proxy_url=proxy or None)
    fetched = client.fetch_image(url, timeout=15, max_bytes=MAX_DOWNLOAD_BYTES)
    if fetched.content is None:
        return None, f"download_{fetched.error or 'failed'}"
    return fetched.content, None


def _load_source(image_url_or_path, candidate_sha256=None) -> Tuple[Optional[bytes], Optional[str], str]:
    """يعيد (البيانات، رمز الخطأ، نوع المصدر: candidate|download|local)."""
    data = _load_from_candidate_store(candidate_sha256) if candidate_sha256 else None
    if data is not None:
        return data, None, "candidate"

    source = str(image_url_or_path or "").strip()
    if not source:
        return None, "source_missing", "local"
    lowered = source.lower()
    if lowered.startswith(("http://", "https://")):
        data, err = _download_bytes(source)
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


def _crop_to_box(img: Image.Image, box, margin: float = BOX_MARGIN_FRACTION) -> Image.Image:
    ymin, xmin, ymax, xmax = (float(v) for v in box)
    my = (ymax - ymin) * margin
    mx = (xmax - xmin) * margin
    ymin, xmin = max(0.0, ymin - my), max(0.0, xmin - mx)
    ymax, xmax = min(1000.0, ymax + my), min(1000.0, xmax + mx)
    w, h = img.size
    left = max(0, min(int(xmin / 1000.0 * w), w - 1))
    top = max(0, min(int(ymin / 1000.0 * h), h - 1))
    right = max(left + 1, min(int(round(xmax / 1000.0 * w)), w))
    bottom = max(top + 1, min(int(round(ymax / 1000.0 * h)), h))
    return img.crop((left, top, right, bottom))


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
        "crop": "true" if getattr(config, "PHOTOROOM_CROP", False) else "false",
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


def _isolate_rembg(img: Image.Image):
    try:
        from rembg import new_session, remove
    except ImportError:
        return None, "rembg_not_installed"
    try:
        data, _, _ = _encode_for_upload(img)
        output = remove(data, session=new_session("isnet-general-use"))
        cutout = _decode_cutout(output if isinstance(output, (bytes, bytearray)) else b"")
        if cutout is None and isinstance(output, Image.Image):
            cutout = output.convert("RGBA")
        if cutout is None:
            return None, "rembg_bad_output"
        return cutout, None
    except Exception as exc:  # noqa: BLE001
        logger.warning("فشل العزل بـ rembg: %s", exc)
        return None, "rembg_failed"


def _isolate(img: Image.Image, method: str):
    """يستدعي مزوّد العزل المطلوب. الطرق غير المدعومة تفشل بوضوح ولا تنسخ الأصل أبداً."""
    if method == "photoroom":
        return _isolate_photoroom(img)
    if method == "remove_bg_api":
        return _isolate_remove_bg(img)
    if method == "grabcut":
        return _isolate_grabcut(img)
    if method == "rembg":
        return _isolate_rembg(img)
    if method == "bria_rmbg":
        # نموذج Bria يتطلب torch وترخيصه غير تجاري؛ غير مدعوم في هذا المسار
        return None, "bria_rmbg_unsupported"
    return None, "unknown_bg_method"


def _enhance_rgb(rgba: Image.Image) -> Image.Image:
    """تحسين خفيف للتباين والألوان على قنوات RGB فقط (قناة الشفافية لا تتغير)."""
    rgba = rgba.convert("RGBA")
    alpha = rgba.getchannel("A")
    rgb = rgba.convert("RGB")
    rgb = ImageEnhance.Contrast(rgb).enhance(1.08)
    rgb = ImageEnhance.Color(rgb).enhance(1.05)
    out = rgb.convert("RGBA")
    out.putalpha(alpha)
    return out


def _count_gemini_call() -> None:
    try:
        config.METRICS["gemini_api_calls"] += 1
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# الواجهة الرئيسية
# ---------------------------------------------------------------------------

def process_product_image_result(image_url_or_path, product_name, brand, target_width=0, target_height=0,
                                 bg_method=None, candidate_sha256=None, enhance=False) -> ProcessResult:
    """
    يحوّل صورة المنتج المعتمدة إلى لوحة نشر نهائية: PNG بخلفية بيضاء معتمة RGB بالأبعاد المطلوبة
    (0 أو 'dynamic' = OUTPUT_CANVAS_SIZE، افتراضياً 800x800) والمنتج يملأ 88% وموسّط.
    لا يرفع استثناءات: كل فشل يعود كـ ProcessResult(path=None, isolated=False, error=<رمز>).
    """
    method = _normalise_method(bg_method)
    try:
        canvas_size = _resolve_canvas_size(target_width, target_height)

        data, error, origin = _load_source(image_url_or_path, candidate_sha256)
        if data is None:
            logger.warning("تعذر الحصول على الصورة المصدر: %s", error)
            return ProcessResult(None, False, method, error)

        img, error = _decode_image(data)
        if img is None:
            code = {"download": "download_", "candidate": "candidate_"}.get(origin, "source_") + error
            logger.warning("المصدر ليس صورة صالحة: %s", code)
            return ProcessResult(None, False, method, code)
        img = _limit_work_size(img)

        if method == "none":
            # 'none' تعني فعلاً بدون عزل: الصورة كما هي (بعد تصحيح الاتجاه) على اللوحة، ولا ندّعي العزل أبداً
            cutout, provider, isolated = img.convert("RGBA"), "none", False
        elif has_meaningful_transparency(img):
            # الخلفية مزالة مسبقاً في المصدر؛ لا حاجة لاستدعاء مزوّد مدفوع
            cutout, provider, isolated = img, "source_alpha", True
        else:
            box = _locate_product_box(img, product_name, brand)
            if box is not None and _sane_box(box):
                img = _crop_to_box(img, box)
            elif box is not None:
                logger.info("تم تجاهل صندوق Gemini غير المعقول: %s", box)
            cutout, error = _isolate(img, method)
            if cutout is None:
                logger.warning("فشل عزل الخلفية بطريقة %s: %s", method, error)
                return ProcessResult(None, False, method, error)
            provider, isolated = method, True

        cutout = EdgeShadowEngine.process_mask(cutout)
        if alpha_bbox(cutout) is None:
            return ProcessResult(None, False, provider, f"{provider}_empty_cutout")
        if enhance:
            cutout = _enhance_rgb(cutout)

        if getattr(config, "ENABLE_STUDIO_SHADOWS", False):
            canvas = EdgeShadowEngine.apply_studio_shadows(cutout, canvas_size)
        else:
            canvas = compose_on_white_canvas(cutout, canvas_size, CANVAS_FILL_RATIO)

        job_dir = tempfile.mkdtemp(prefix="imgproc_")
        out_path = os.path.join(job_dir, f"{uuid.uuid4().hex}.png")
        canvas.save(out_path, format="PNG")
        return ProcessResult(out_path, isolated, provider, None, canvas.width, canvas.height)
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
