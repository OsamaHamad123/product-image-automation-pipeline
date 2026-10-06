# recut.py
# «أعد القص»: نسخة جديدة لصورة منشورة من نفس الصورة المصدر، بنفس عزل الخلفية والتشطيب، منشورة كأصل جديد. بيستعمله
# scripts/reprocess_transparent.py («أعد معالجتها شفافة»: الصور القديمة البيضا) وصفحة «فحص القص» (cli_bridge recut_*).
#
# - مين أصله مش شفاف: resolved_products.master_background (بينكتب مع كل اعتماد من هلق: facts_from_publish). اعتماد
#   قديم ما إله قيمة بينفحص أصله نفسه (probe): رابط التسليم الجديد (f_webp) بيحفظ الشفافية، فالأصل الأبيض بيبين أبيض.
# - المصدر (أبداً الأصل الأبيض لما في مصدر): ملف مخزن المرشحات ببصمة الصورة اللي انعتمدت (approved_embeddings)، أو
#   تنزيل رابطها الأصلي والتأكد إنه نفس البايتات (البصمة) أو إنه نفس الصورة المنشورة (same_picture: pHash وألوان
#   المنتج نفسه). بايتات تغيرت عن اللي انعتمدت، أو رابط ما عاد بيرد، أو صورة تانية: الأصل المنشور نفسه هو المصدر.
# - العزل: نفس image_processor.process_product_image_result بطريقة الإعدادات (PhotoRoom وبديله المحلي متل ما هم) أو
#   الطريقة المطلوبة («فحص القص»: photoroom أو rembg)، ثم cutout_finish (سد الثقوب، إزالة التسرب، فحص الهالة).
# - النشر (publish): أصل جديد على Cloudinary (اسمه md5 بايتاته، والرابط فيه رقم نسخة جديد فكاش التطبيق بيتجدد)، الحل
#   المعتمد بيلحقه (local_cache_db.replace_master، الحالة والمراجِع ووقت الاعتماد متل ما هم)، والرابط الجديد بيروح للشيت
#   عبر طابور الكتابة بس للخلايا اللي لسا فيها الرابط القديم (expect_value): خلية غيّرها المالك بإيده ما بتنلمس.
# - كل تبديل بيتسجّل بـ recut_log قبل ما يلمس شي (uploaded)، فتشغيل انقطع بالنص بيكمّله اللي بعده (finish_publish)،
#   والمالك بيقدر يرجّع القديم (undo) بنفس الطريق.
# ما في أي حذف: الأصل القديم بيضل على Cloudinary (التراجع بيرجعله).

import io
import json
import logging
import os
import re
import time
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from PIL import Image, ImageChops

import cutout_finish
import delivery_urls

logger = logging.getLogger(__name__)

ROOT = os.path.dirname(os.path.abspath(__file__))
TRY_DIR = os.path.join(ROOT, "temp", "recut")        # «جرّب»: القص الجديد قبل ما يعتمده المالك (save_try)
TOKEN_RE = re.compile(r"^[0-9a-f]{32}$")
TRY_TTL_S = 2 * 86400
PREVIEW_SIDE = 720

TRANSPARENT, WHITE, OPAQUE = "transparent", "white", "opaque"
BACKGROUNDS = (TRANSPARENT, WHITE, OPAQUE)
# علامات صفحة «فحص القص»: white (الأصل مش شفاف)، dark_halo، photoroom_unsure، glass (عبوة شفافة أو زجاج)، dark_rim،
# low_res (المنتج انكبّر من مصدر صغير)
GALLERY_FLAGS = ("white", "dark_halo", "photoroom_unsure", "glass", "dark_rim", "low_res")
FINISH_KEYS = ("background", "halo", "halo_retry", "dark_rim", "holes_filled", "holes_left", "hole_fill",
               "uncertainty", "defringed")
SOURCE_ERROR_PREFIXES = ("download_", "source_", "candidate_")
SAME_PICTURE_BITS = 12          # نفس الصورة: pHash المنتج نفسه (مقصوص على حدوده) على مسافة 12 من 64 أو أقل...
SAME_PICTURE_RATIO = 0.15       # ...ونسبة أبعاده ضمن 15%، وألوانه مش مختلفة بوضوح (image_dedup_bktree.colors_differ)
CONTENT_LEVEL = 16              # بكسل «من المنتج» على الأبيض: أغمق من الأبيض بـ 16 درجة
MAX_CALLS_PER_PICTURE = 3       # أسوأ حالة لصورة: عزل، إعادة بلا صندوق Gemini أو بالمزوّد التاني، وإعادة للهالة
PROBE_THREADS = 8
LOW_RES_PRODUCT_PX = 400        # المنتج على الأصل المنشور أصغر من هيك: دقة قليلة


# ---------------------------------------------------------------------------
# ما منعرفه عن الأصل المنشور
# ---------------------------------------------------------------------------

def facts_from_publish(res) -> Optional[dict]:
    """ما قاله فحص القص عن لوحة انتشرت (main.publish_image)، للعمود resolved_products.cutout_json؛ None بلا لوحة."""
    if not isinstance(res, dict) or not res.get("width"):
        return None
    finish = dict(res.get("finish") or {})
    facts = {"provider": res.get("provider"), "flags": [str(f) for f in res.get("quality_flags") or []],
             "notes": [str(n) for n in res.get("quality_notes") or []], "canvas": [res.get("width"), res.get("height")],
             "finish": {k: finish[k] for k in FINISH_KEYS if k in finish}}
    if res.get("bg_fallback"):
        facts["bg_fallback"] = dict(res["bg_fallback"])
    return facts


def background_of(res) -> Optional[str]:
    """خلفية لوحة انتشرت: transparent | opaque (بلا عزل) | white (OUTPUT_BACKGROUND = white)؛ None إذا ما منعرف."""
    if not isinstance(res, dict):
        return None
    finish = res.get("finish") or {}
    if finish.get("background") == TRANSPARENT:
        return TRANSPARENT
    if res.get("bg_skipped") or finish.get("background") == "opaque":
        return OPAQUE
    if str((res.get("profile") or {}).get("background") or "") == WHITE:
        return WHITE
    return None


def master_facts(res) -> dict:
    """{master_background, cutout} للوحة انتشرت، لـ local_cache_db.save_product_resolution (القيم المجهولة بتنترك)."""
    out = {}
    background = background_of(res)
    if background:
        out["master_background"] = background
    facts = facts_from_publish(res)
    if facts:
        out["cutout"] = facts
    return out


def loads(value, default=None):
    if isinstance(value, dict):
        return value
    try:
        out = json.loads(value) if value else default
    except (TypeError, ValueError):
        return default
    return out if isinstance(out, dict) else default


def known_background(row) -> Optional[str]:
    """خلفية الأصل كما انحفظت (master_background، أو فحص سابق للأصل نفسه: cutout_json.measured)، وإلا None."""
    value = str(row.get("master_background") or "").strip().lower()
    if value in BACKGROUNDS:
        return value
    measured = (loads(row.get("cutout_json"), {}) or {}).get("measured") or {}
    value = str(measured.get("background") or "").strip().lower()
    return value if value in BACKGROUNDS else None


def _content_box(img: Image.Image):
    """حدود المنتج: البكسلات المرئية بالأصل الشفاف، أو اللي مش بيضا بالأصل الأبيض."""
    import image_processor

    rgba = img.convert("RGBA")
    if image_processor.has_meaningful_transparency(rgba):
        return rgba.getchannel("A").point(lambda a: 255 if a >= 8 else 0).getbbox()
    flat = cutout_finish.flatten_on_white(img).convert("RGB")
    diff = ImageChops.difference(flat, Image.new("RGB", flat.size, (255, 255, 255))).convert("L")
    return diff.point(lambda v: 255 if v > CONTENT_LEVEL else 0).getbbox()


def measure(img: Image.Image) -> dict:
    """
    فحص الأصل المنشور نفسه: background (transparent أو white: أي أصل معتم)، الهالة على الغامق والحافة الغامقة على
    الأبيض (للشفاف بس، cutout_finish)، أبعاد اللوحة، وأطول ضلع للمنتج عليها (product_px).
    """
    import image_processor

    rgba = img.convert("RGBA")
    out = {"canvas": [img.width, img.height], "measured_at": int(time.time())}
    if image_processor.has_meaningful_transparency(rgba):
        out["background"] = TRANSPARENT
        out["halo"] = round(cutout_finish.halo_score(rgba), 4)
        out["dark_rim"] = round(cutout_finish.dark_rim_score(rgba), 4)
    else:
        out["background"] = WHITE
    box = _content_box(rgba)
    out["product_px"] = max(box[2] - box[0], box[3] - box[1]) if box else 0
    return out


def fetch_bytes(url) -> Optional[bytes]:
    """تنزيل رابط (image_processor._download_bytes: نفس الحماية والمهل)؛ None عند الفشل."""
    import image_processor

    data, _error = image_processor._download_bytes(url)
    return data


def open_image(data) -> Optional[Image.Image]:
    if not data:
        return None
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
        return img
    except Exception:  # noqa: BLE001 - مش صورة
        return None


def master_image(url, fetch=None) -> Optional[Image.Image]:
    """الأصل المنشور كما بيوصل للتطبيق (رابط التسليم الجديد f_webp: بيحفظ الشفافية)، أو None."""
    return open_image((fetch or fetch_bytes)(delivery_urls.canonical_delivery_url(url)))


def probe(url, fetch=None) -> Optional[dict]:
    """measure() للأصل المنشور على هالرابط، أو None إذا ما انقرأ."""
    img = master_image(url, fetch)
    return measure(img) if img is not None else None


def master_source_url(url) -> Optional[str]:
    """الأصل المرفوع نفسه بلا أي تحويل (بنفس الحساب والنسخة)، أو None لرابط مش إلنا."""
    parts = delivery_urls.split_delivery_url(url)
    if parts is None:
        return None
    head, _transformation, tail = parts
    return head + tail


def asset_folder(url) -> str:
    """مجلد الأصل على Cloudinary من رابطه (products/<l1>/<l2>)؛ النسخة الجديدة بتنرفع بنفس المجلد."""
    parts = delivery_urls.split_delivery_url(url)
    tail = parts[2] if parts else ""
    segments = [s for s in tail.split("/") if s]
    if segments and re.match(r"^v\d+$", segments[0]):
        segments = segments[1:]
    return "/".join(segments[:-1]) or "products"


def _categories(row) -> List[str]:
    meta = loads(row.get("metadata_json"), {}) or {}
    return [str(meta.get(k) or "") for k in ("category_l1_en", "category_l2_en", "category_l3_en")]


def is_clear(row) -> bool:
    """عبوة شفافة أو زجاج (categories.is_clear_packaging من تصنيف المنتج المحفوظ مع الاعتماد)."""
    import categories

    return categories.is_clear_packaging(*_categories(row))


def quality_flags(row, facts=None) -> List[str]:
    """
    علامات «فحص القص» لصورة منشورة (GALLERY_FLAGS)، من اللي انحفظ وقت النشر (cutout_json) ومن فحص الأصل نفسه
    (measured)، وعدم تأكد PhotoRoom بحده بالإعدادات.
    """
    from catalog_match import settings

    facts = facts if facts is not None else (loads(row.get("cutout_json"), {}) or {})
    finish = facts.get("finish") or {}
    measured = facts.get("measured") or {}
    marks = set(facts.get("flags") or []) | set(facts.get("notes") or [])
    out = []
    if known_background(row) in (WHITE, OPAQUE):
        out.append("white")
    halo = max(float(finish.get("halo") or 0), float(measured.get("halo") or 0))
    if "dark_halo" in marks or halo > cutout_finish.HALO_MAX:
        out.append("dark_halo")
    unsure = finish.get("uncertainty")
    if "photoroom_unsure" in marks or (unsure is not None and float(unsure) > settings.photoroom_uncertainty_max()):
        out.append("photoroom_unsure")
    if is_clear(row) or finish.get("hole_fill") == "skipped_clear":
        out.append("glass")
    rim = max(float(finish.get("dark_rim") or 0), float(measured.get("dark_rim") or 0))
    if "dark_rim" in marks or rim > cutout_finish.DARK_RIM_MAX:
        out.append("dark_rim")
    product_px = measured.get("product_px")
    if marks & {"upscaled", "too_small_on_canvas"} or (product_px and int(product_px) < LOW_RES_PRODUCT_PX):
        out.append("low_res")
    return out


# ---------------------------------------------------------------------------
# نفس الصورة؟ (مصدر بلا بصمة: الرابط الأصلي ممكن يكون صار صورة تانية)
# ---------------------------------------------------------------------------

def _product(img: Image.Image) -> Image.Image:
    """المنتج كما يبان على الأبيض، مقصوص على البكسلات اللي مش بيضا (نفس القاعدة للأصل الأبيض واللوحة الشفافة: غطا
    أبيض بيضيع من الاتنين متل بعض)."""
    flat = cutout_finish.flatten_on_white(img).convert("RGB")
    diff = ImageChops.difference(flat, Image.new("RGB", flat.size, (255, 255, 255))).convert("L")
    box = diff.point(lambda v: 255 if v > CONTENT_LEVEL else 0).getbbox()
    return flat.crop(box) if box else flat


def same_picture(old: Image.Image, new: Image.Image, max_bits: int = SAME_PICTURE_BITS) -> bool:
    """
    هل اللوحة الجديدة هي نفس الصورة المنشورة؟ المنتج نفسه بكل وحدة (مقصوص على حدوده، على الأبيض): نسبة أبعاد متقاربة،
    pHash على مسافة max_bits أو أقل، وألوان مش مختلفة بوضوح. أي شك: لا (المصدر بيصير الأصل المنشور نفسه).
    """
    import image_dedup_bktree
    from catalog_match.fetch import phash_distance, phash_hex

    a, b = _product(old), _product(new)
    if min(a.size + b.size) < 8:
        return False
    ra, rb = a.width / a.height, b.width / b.height
    if abs(ra - rb) > SAME_PICTURE_RATIO * max(ra, rb):
        return False
    side = (256, 256)
    distance = phash_distance(phash_hex(a.resize(side)), phash_hex(b.resize(side)))
    if distance is None or distance > max_bits:
        return False
    return not image_dedup_bktree.colors_differ(image_dedup_bktree.color_signature(a),
                                                image_dedup_bktree.color_signature(b))


# ---------------------------------------------------------------------------
# إعادة العزل
# ---------------------------------------------------------------------------

@dataclass
class Recut:
    """نتيجة recut(): لوحة PNG شفافة جديدة (path) أو رمز خطأ، ومن وين اجت وكم طلب مدفوع كلّفت."""
    path: Optional[str]
    error: Optional[str] = None
    provider: str = ""
    isolated: bool = False
    flags: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    finish: dict = field(default_factory=dict)
    source: str = ""
    width: int = 0
    height: int = 0
    paid_calls: int = 0
    tried: List[list] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        """القص الجديد معزول ونظيف (بلا علامات): بينشر لحاله بالدفعة؛ غير هيك بيستنى عين المالك («فحص القص»)."""
        return self.path is not None and self.isolated and not self.flags

    def facts(self) -> dict:
        facts = {"provider": self.provider, "flags": list(self.flags), "notes": list(self.notes),
                 "canvas": [self.width, self.height], "finish": {k: self.finish[k] for k in FINISH_KEYS
                                                                  if k in self.finish},
                 "recut_source": self.source}
        return facts


def is_source_error(code) -> bool:
    return str(code or "").startswith(SOURCE_ERROR_PREFIXES)


def _paid_total() -> int:
    import image_processor

    return sum(image_processor.paid_calls().values())


def recut(row, method=None, process=None, fetch=None, sha_lookup=None, profile=None) -> Recut:
    """
    يعزل الصورة المنشورة row (صف published_masters) من جديد إلى لوحة شفافة (انظر رأس الملف لترتيب المصادر). method:
    طريقة العزل (None = الإعدادات). process / fetch / sha_lookup للاختبارات. لا يرفع استثناءات.
    """
    import image_processor
    import local_cache_db
    import processing_profile

    profile = profile or processing_profile.current()
    method = str(method or profile.bg_method or "").strip().lower()
    if method in processing_profile.NO_REMOVAL_METHODS:
        return Recut(None, error="bg_removal_off")
    process = process or image_processor.process_product_image_result
    sha_lookup = sha_lookup or local_cache_db.approval_source_sha
    url = str(row.get("cloudinary_url") or "")
    name, brand = str(row.get("product_name") or ""), str(row.get("brand") or "")
    original = str(row.get("original_url") or "").strip()
    original = original if original.lower().startswith(("http://", "https://")) else ""
    attempts = []
    if original:
        sha = sha_lookup(row.get("sku_key"), url)
        attempts.append(("candidate" if sha else "original", original, sha))
    master = master_source_url(url)
    if master:
        attempts.append(("master", master, None))
    if not attempts:
        return Recut(None, error="no_source")
    before = _paid_total()
    tried, old = [], None
    clear = is_clear(row)
    for source, src, sha in attempts:
        try:
            res = process(src, name, brand, target_width=profile.canvas, target_height=profile.canvas, bg_method=method,
                          candidate_sha256=sha, enhance=profile.enhance, background=TRANSPARENT, clear=clear)
        except Exception as exc:  # noqa: BLE001 - المعالجة ما بترفع عادةً؛ أي خطأ = فشل بلا نشر
            logger.warning("[Recut] فشلت المعالجة (%s): %s", source, type(exc).__name__)
            res = None
        if res is None or not getattr(res, "path", None):
            code = (getattr(res, "error", None) if res is not None else None) or "processing_failed"
            tried.append([source, code])
            if source != "master" and is_source_error(code):
                continue        # المصدر ما انقرأ أو تغيّرت بايتاته: الأصل المنشور نفسه
            return Recut(None, error=code, paid_calls=_paid_total() - before, tried=tried, source=source)
        if source == "original":
            old = old if old is not None else master_image(url, fetch)
            try:
                with Image.open(res.path) as made:
                    made.load()
                    same = old is not None and same_picture(old, made)
            except Exception:  # noqa: BLE001
                same = False
            if not same:
                image_processor.cleanup_processed_image(res.path)
                tried.append([source, "source_differs"])
                continue
        tried.append([source, "ok"])
        return Recut(res.path, provider=str(res.provider or ""), isolated=bool(res.isolated),
                     flags=[str(f) for f in res.quality_flags or []], notes=[str(n) for n in res.quality_notes or []],
                     finish=dict(getattr(res, "finish", None) or {}), source=source, width=int(res.width or 0),
                     height=int(res.height or 0), paid_calls=_paid_total() - before, tried=tried)
    return Recut(None, error=(tried[-1][1] if tried else "no_source"), paid_calls=_paid_total() - before, tried=tried)


def canvas_prints(path):
    """(pHash، بصمة الألوان) للوحة كما تبان على الأبيض: نفس بصمات النشر (main._canvas_phash)."""
    import image_dedup_bktree
    from catalog_match.fetch import phash_hex

    try:
        with Image.open(path) as img:
            img.load()
            flat = cutout_finish.flatten_on_white(img)
        return phash_hex(flat), image_dedup_bktree.color_signature(flat)
    except Exception:  # noqa: BLE001
        return None, None


# ---------------------------------------------------------------------------
# «جرّب» (صفحة «فحص القص»): القص الجديد بيستنى بـ temp/recut لحد ما المالك يعتمده أو يخلّي القديم
# ---------------------------------------------------------------------------

def _try_paths(token):
    if not TOKEN_RE.match(str(token or "")):
        return None
    base = os.path.join(TRY_DIR, str(token))
    return {"canvas": base + ".png", "preview": base + "_preview.png", "meta": base + ".json"}


def save_try(row, result: Recut, method) -> dict:
    """
    يحفظ قص «جرّب» (اللوحة الكاملة ونسخة معاينة صغيرة ووصفه) ويرجع {token, provider, flags, notes, finish, source,
    paid_calls, clean, width, height}. اللوحة المؤقتة من image_processor بتنمسح.
    """
    import atomic_file
    import image_processor

    os.makedirs(TRY_DIR, exist_ok=True)
    prune_tries()
    token = uuid.uuid4().hex
    paths = _try_paths(token)
    with Image.open(result.path) as img:
        img.load()
        canvas = img.convert("RGBA")
    canvas.save(paths["canvas"], format="PNG")
    preview = canvas.copy()
    preview.thumbnail((PREVIEW_SIDE, PREVIEW_SIDE), Image.Resampling.LANCZOS)
    preview.save(paths["preview"], format="PNG")
    image_processor.cleanup_processed_image(result.path)
    meta = {"token": token, "row_id": int(row["id"]), "old_url": row.get("cloudinary_url"), "method": method,
            "provider": result.provider, "isolated": result.isolated, "flags": result.flags, "notes": result.notes,
            "finish": {k: result.finish[k] for k in FINISH_KEYS if k in result.finish}, "source": result.source,
            "paid_calls": result.paid_calls, "width": result.width, "height": result.height,
            "created_at": int(time.time())}
    atomic_file.write_json(paths["meta"], meta, ensure_ascii=False)
    return dict(meta, clean=result.clean)


def load_try(token) -> Optional[dict]:
    """وصف قص «جرّب» محفوظ مع مسار لوحته، أو None (رمز غلط، أو انمسح، أو أقدم من TRY_TTL_S)."""
    paths = _try_paths(token)
    if paths is None or not os.path.isfile(paths["meta"]) or not os.path.isfile(paths["canvas"]):
        return None
    try:
        with open(paths["meta"], encoding="utf-8") as fh:
            meta = json.load(fh)
    except (OSError, ValueError):
        return None
    if time.time() - float(meta.get("created_at") or 0) > TRY_TTL_S:
        return None
    return dict(meta, canvas_path=paths["canvas"], preview_path=paths["preview"])


def discard_try(token) -> bool:
    """«خلّي القديم»: يمسح قص «جرّب» (ما في شي انرفع ولا انكتب)."""
    paths = _try_paths(token)
    if paths is None:
        return False
    removed = False
    for path in paths.values():
        try:
            os.remove(path)
            removed = True
        except OSError:
            pass
    return removed


def prune_tries(now=None):
    """يمسح قصات «جرّب» الأقدم من TRY_TTL_S (المالك ما اعتمدها ولا خلّى القديم)."""
    now = time.time() if now is None else now
    try:
        names = os.listdir(TRY_DIR)
    except OSError:
        return 0
    removed = 0
    for name in names:
        path = os.path.join(TRY_DIR, name)
        try:
            if now - os.path.getmtime(path) > TRY_TTL_S:
                os.remove(path)
                removed += 1
        except OSError:
            continue
    return removed


# ---------------------------------------------------------------------------
# الشيت: الخلايا اللي فيها رابط إلنا
# ---------------------------------------------------------------------------

def _cell(row, idx):
    return str(row[idx]).strip() if 0 <= idx < len(row) and row[idx] is not None else ""


def sheet_links(values):
    """
    (عمود الرابط، {الرابط المقارن: [{row_number, cell, barcode, product_name, size, brand}]}) لكل خلية رابط فيها بالضبط
    رابط تسليم إلنا (قديم أو جديد؛ بلا بادئة needs_review: ولا مسافات). عمود الرابط -1 إذا ما في.
    """
    import google_sheets

    out: Dict[str, list] = {}
    if not values:
        return -1, out
    cols = google_sheets.resolve_columns(values[0])
    link = cols.get("link", -1)
    if link == -1:
        return -1, out
    for number, row in enumerate(values[1:], start=2):
        raw = "" if link >= len(row) or row[link] is None else str(row[link])
        if not raw or raw != raw.strip() or raw.startswith(delivery_urls.REVIEW_PREFIX):
            continue
        if not delivery_urls.is_delivery_url(raw):
            continue
        out.setdefault(delivery_urls.canonical_delivery_url(raw), []).append({
            "row_number": number, "cell": raw, "barcode": _cell(row, cols.get("barcode", -1)),
            "product_name": _cell(row, cols.get("name", -1)), "size": _cell(row, cols.get("size", -1)) or None,
            "brand": _cell(row, cols.get("brand", -1)) or None})
    return link, out


def busy_rows(row_numbers):
    """صفوف عندها كتابة رابط لسا بطابور الكتابة (PENDING / FAILED): صورة أحدث بالطريق، فما منلمسها هالمرة. None عند خطأ."""
    import google_sheets

    numbers = sorted({int(r) for r in row_numbers or []})
    if not numbers:
        return set()
    try:
        records = google_sheets.outbox_outcomes(numbers, limit=len(numbers) * 4 + 500)
    except Exception:  # noqa: BLE001
        return None
    busy = set()
    for rec in records:
        if rec.get("column_key") not in (None, "", google_sheets.LINK_KEY):
            continue
        if str(rec.get("status") or "").upper() in ("PENDING", "FAILED"):
            busy.update(int(r) for r in (rec.get("row"), rec.get("queued_row")) if r is not None)
    return busy


# ---------------------------------------------------------------------------
# النشر والتراجع
# ---------------------------------------------------------------------------

def _old_facts(rows) -> dict:
    first = rows[0]
    facts = loads(first.get("cutout_json"), {}) or {}
    return dict(facts, phash=first.get("perceptual_hash"), color_signature=first.get("color_signature"))


def publish(rows, canvas_path, cutout, origin, sheet_rows, batch_id=None, provider=None, source=None, paid_calls=0,
            upload=None, old_background=None) -> dict:
    """
    ينشر لوحة جديدة مكان الأصل المنشور لصفوف resolved_products هي (rows: نفس الرابط): يرفع أصلاً جديداً بنفس المجلد،
    يسجّله (recut_log: uploaded)، ثم finish_publish (الحل المعتمد، وطابور الكتابة للخلايا sheet_rows اللي لسا فيها
    الرابط القديم). old_background: خلفية الأصل القديم إذا انعرفت بفحص (للتراجع). يرجع {status: done | skipped |
    failed, code, new_url, log_id, outbox}. لا يفرّغ الطابور.
    """
    import cloudinary_storage
    import local_cache_db

    upload = upload or cloudinary_storage.upload_product_image
    first = rows[0]
    old_url = str(first.get("cloudinary_url") or "")
    up = upload(canvas_path, first.get("product_name"), first.get("brand"), folder=asset_folder(old_url))
    new_url = getattr(up, "url", None)
    if not new_url:
        return {"status": "failed", "code": getattr(up, "error", None) or "upload_failed", "new_url": None,
                "log_id": None, "outbox": {}}
    if delivery_urls.same_delivery_asset(new_url, old_url):
        return {"status": "skipped", "code": "same_picture", "new_url": new_url, "log_id": None, "outbox": {}}
    phash, color = canvas_prints(canvas_path)
    facts = dict(cutout or {}, phash=phash, color_signature=color)
    log_id = local_cache_db.log_recut({
        "batch_id": batch_id, "origin": origin, "status": "uploaded", "resolved_ids": [r["id"] for r in rows],
        "sku_key": first.get("sku_key"), "product_name": first.get("product_name"), "brand": first.get("brand"),
        "old_url": old_url, "new_url": new_url, "old_background": old_background or known_background(first),
        "old_cutout_json": _old_facts(rows), "new_cutout_json": facts, "provider": provider, "source": source,
        "paid_calls": paid_calls, "rows_json": list(sheet_rows or [])})
    return finish_publish(local_cache_db.recut_entry(log_id))


def _queue(items):
    import google_sheets

    return {str(row): wid for row, wid in google_sheets.queue_link_writes(items).items()} if items else {}


def finish_publish(entry) -> dict:
    """
    يكمّل نشر مسجّل uploaded (أول مرة أو بعد انقطاع): الحل المعتمد لصفوفه بيصير الأصل الجديد، وكتابة الرابط الجديد
    للخلايا اللي لسا فيها القديم. صفوف اعتمد لها المالك صورة تانية بالنص: ما في شي بينكتب (changed_meanwhile).
    """
    import local_cache_db

    old_url, new_url = entry["old_url"], entry["new_url"]
    facts = entry.get("new_cutout") or {}
    changed = local_cache_db.replace_master(entry["resolved_ids"], old_url, new_url, TRANSPARENT, facts,
                                            facts.get("phash"), facts.get("color_signature"))
    if not changed:
        still = [r for r in (local_cache_db.published_master(i) for i in entry["resolved_ids"]) if r]
        if not any(delivery_urls.same_delivery_asset(r.get("cloudinary_url"), new_url) for r in still):
            local_cache_db.update_recut(entry["id"], status="skipped", code="changed_meanwhile")
            return {"status": "skipped", "code": "changed_meanwhile", "new_url": new_url, "log_id": entry["id"],
                    "outbox": {}}
    items = [{"row_number": r["row_number"], "value": new_url, "barcode": r.get("barcode") or "",
              "product_name": r.get("product_name"), "size": r.get("size"), "brand": r.get("brand"),
              "replace": r.get("cell") or old_url} for r in entry.get("rows") or []]
    outbox = _queue(items)
    local_cache_db.update_recut(entry["id"], status="done", code=None, outbox_json=outbox)
    return {"status": "done", "code": None, "new_url": new_url, "log_id": entry["id"], "outbox": outbox}


def resume_unfinished(limit=100) -> List[dict]:
    """يكمّل كل نشر انقطع بعد الرفع (recut_log: uploaded). يرجع نتايجهن."""
    import local_cache_db

    return [finish_publish(e) for e in reversed(local_cache_db.recut_entries(statuses=("uploaded",), limit=limit))]


def undo(log_id) -> dict:
    """
    «رجّع القديم»: الحل المعتمد بيرجع للأصل القديم، والرابط القديم (بشكل التسليم الجديد) بيروح للخلايا اللي لسا فيها
    الرابط الجديد (خلية غيّرها المالك من بعد ما بتنلمس). بس لتبديل done؛ إذا المالك اعتمد صورة تانية من بعده: refused.
    يرجع {status: undone | refused, code, outbox}. لا يفرّغ الطابور.
    """
    import local_cache_db

    entry = local_cache_db.recut_entry(log_id)
    if not entry:
        return {"status": "refused", "code": "not_found", "outbox": {}}
    if entry["status"] != "done":
        return {"status": "refused", "code": f"status_{entry['status']}", "outbox": {}}
    old = entry.get("old_cutout") or {}
    background = entry.get("old_background")
    changed = local_cache_db.replace_master(entry["resolved_ids"], entry["new_url"], entry["old_url"], background,
                                            {k: v for k, v in old.items() if k not in ("phash", "color_signature")}
                                            or None, old.get("phash"), old.get("color_signature"))
    if not changed:
        return {"status": "refused", "code": "changed_meanwhile", "outbox": {}}
    back = delivery_urls.migrated_delivery_url(entry["old_url"]) or entry["old_url"]
    items = [{"row_number": r["row_number"], "value": back, "barcode": r.get("barcode") or "",
              "product_name": r.get("product_name"), "size": r.get("size"), "brand": r.get("brand"),
              "replace": entry["new_url"]} for r in entry.get("rows") or []]
    outbox = _queue(items)
    local_cache_db.update_recut(entry["id"], status="undone", undone=True, outbox_json=outbox)
    return {"status": "undone", "code": None, "outbox": outbox}


# ---------------------------------------------------------------------------
# «أعد معالجتها شفافة»: مين لازم ينعاد
# ---------------------------------------------------------------------------

@dataclass
class Group:
    """أصل منشور واحد (رابط واحد) مع صفوف resolved_products اللي بتخدمه وخلايا الشيت اللي فيها رابطه."""
    url: str
    rows: List[dict]
    sheet_rows: List[dict]
    background: Optional[str] = None
    state: str = ""              # transparent | todo | not_in_sheet | skipped_before | probe_failed
    measured: Optional[dict] = None

    @property
    def name(self) -> str:
        return str(self.rows[0].get("product_name") or "")


PLAN_STATES = ("todo", "transparent", "not_in_sheet", "skipped_before", "probe_failed")


def plan(values, masters, skipped_urls=(), probe_fn=None, threads=PROBE_THREADS) -> dict:
    """
    مين من الصور المنشورة أصله مش شفاف ولازم ينعاد: {link_col, groups: [Group] (الأحدث أولاً), counts}. masters:
    local_cache_db.published_masters(). أصل خلفيته مش معروفة بينفحص (probe_fn، افتراضياً probe) بخيوط متوازية؛ ما
    بينحفظ شي هون. skipped_urls: أصول وقفت عندها دفعة قبل (ما انقص شي: مصدر أو علامة) ما بتنعاد إلا بطلب.
    """
    link_col, links = sheet_links(values)
    groups: Dict[str, Group] = {}
    for row in masters:
        key = delivery_urls.canonical_delivery_url(row.get("cloudinary_url"))
        if not key:
            continue
        group = groups.get(key)
        if group is None:
            group = groups[key] = Group(url=str(row.get("cloudinary_url")), rows=[], sheet_rows=links.get(key, []))
        group.rows.append(row)
    skipped = {delivery_urls.canonical_delivery_url(u) for u in skipped_urls or ()}
    unknown = []
    for key, group in groups.items():
        group.background = next((b for b in (known_background(r) for r in group.rows) if b), None)
        if not group.sheet_rows:
            group.state = "not_in_sheet"
        elif group.background == TRANSPARENT:
            group.state = "transparent"
        elif key in skipped:
            group.state = "skipped_before"
        elif group.background in (WHITE, OPAQUE):
            group.state = "todo"
        else:
            unknown.append(group)
    if unknown:
        probe_fn = probe_fn or probe
        with ThreadPoolExecutor(max_workers=max(1, int(threads))) as pool:
            found = list(pool.map(lambda g: _safe_probe(probe_fn, g.url), unknown))
        for group, measured in zip(unknown, found):
            group.measured = measured
            if not measured:
                group.state = "probe_failed"
                continue
            group.background = measured.get("background")
            group.state = "transparent" if group.background == TRANSPARENT else "todo"
    ordered = list(groups.values())
    counts = Counter({s: 0 for s in PLAN_STATES})
    counts.update(g.state for g in ordered)
    return {"link_col": link_col, "groups": ordered, "counts": counts,
            "todo": [g for g in ordered if g.state == "todo"]}


def _safe_probe(probe_fn, url):
    try:
        return probe_fn(url)
    except Exception:  # noqa: BLE001 - فحص فشل: الأصل بيضل «مش معروف»
        return None


def remember_measured(groups):
    """يحفظ نتيجة فحص الأصل (الخلفية وما قاسه) لصفوف كل أصل انفحص (بس إذا رابطها ما تغيّر). يرجع عدد الصفوف."""
    import local_cache_db

    saved = 0
    for group in groups:
        if not group.measured:
            continue
        facts = loads(group.rows[0].get("cutout_json"), {}) or {}
        facts["measured"] = group.measured
        try:
            saved += local_cache_db.set_master_facts([r["id"] for r in group.rows], group.url,
                                                     background=group.measured.get("background"), cutout=facts)
        except Exception as exc:  # noqa: BLE001 - حفظ الفحص تحسين بس
            logger.warning("[Recut] تعذر حفظ فحص الأصل: %s", type(exc).__name__)
    return saved


def estimate(count, method) -> dict:
    """تقدير الكلفة: {calls, price, usd, worst_usd} لـ count صورة بطريقة العزل method (طلب واحد بالعادة لكل صورة)."""
    from catalog_match import settings

    price = settings.isolation_price_usd(method)
    calls = int(count) if price > 0 else 0
    return {"calls": calls, "price": price, "usd": round(calls * price, 4),
            "worst_usd": round(calls * MAX_CALLS_PER_PICTURE * price, 4)}


# ---------------------------------------------------------------------------
# صفحة «فحص القص»: الصور المنشورة الأحدث مع علاماتها
# ---------------------------------------------------------------------------

GALLERY_PAGE = 24
MEASURE_BATCH = 24


def needs_measure(row) -> bool:
    """صورة ما منعرف عنها كفاية (خلفيتها، أو لا فحص قص وقت النشر ولا فحص للأصل نفسه): بتنفحص مرة وبينحفظ الفحص."""
    facts = loads(row.get("cutout_json"), {}) or {}
    return known_background(row) is None or not (facts.get("finish") or facts.get("measured"))


def measure_rows(rows, fetch=None, limit=MEASURE_BATCH, threads=PROBE_THREADS) -> int:
    """يفحص لحد limit أصل ناقص (needs_measure، الأحدث أولاً) بخيوط متوازية وبيحفظ الفحص. يرجع كم صورة انفحصت."""
    groups: Dict[str, Group] = {}
    for row in rows:
        if not needs_measure(row):
            continue
        key = delivery_urls.canonical_delivery_url(row.get("cloudinary_url"))
        if key not in groups:
            if len(groups) >= limit:
                continue
            groups[key] = Group(url=str(row.get("cloudinary_url")), rows=[], sheet_rows=[])
        groups[key].rows.append(row)
    if not groups:
        return 0
    with ThreadPoolExecutor(max_workers=max(1, int(threads))) as pool:
        found = list(pool.map(lambda g: _safe_probe(lambda u: probe(u, fetch), g.url), groups.values()))
    for group, measured in zip(groups.values(), found):
        group.measured = measured
    remember_measured(groups.values())
    return sum(1 for m in found if m)


def gallery_item(row) -> dict:
    """صورة منشورة كما بتعرضها صفحة «فحص القص»."""
    facts = loads(row.get("cutout_json"), {}) or {}
    finish, measured = facts.get("finish") or {}, facts.get("measured") or {}
    return {"id": int(row["id"]), "sku_key": row.get("sku_key"), "product_name": row.get("product_name"),
            "brand": row.get("brand"), "url": delivery_urls.canonical_delivery_url(row.get("cloudinary_url")),
            "resolved_at": str(row.get("resolved_at") or ""), "status": row.get("verification_status"),
            "background": known_background(row), "flags": quality_flags(row, facts), "provider": facts.get("provider"),
            "halo": finish.get("halo", measured.get("halo")), "dark_rim": finish.get("dark_rim", measured.get("dark_rim")),
            "uncertainty": finish.get("uncertainty"), "canvas": facts.get("canvas") or measured.get("canvas"),
            "checked": not needs_measure(row)}


def gallery(flag=None, page=1, measure=True, fetch=None, masters=None, page_size=GALLERY_PAGE) -> dict:
    """
    {items (صفحة page)، total (بعد العلامة)، counts (لكل علامة و all)، page، pages، unchecked (لسا ما انفحصت)، measured}.
    measure: يفحص أول MEASURE_BATCH صورة ناقصة قبل ما يعدّ (الصفحة بتطلب «افحص أكتر» لتكمّل).
    """
    import local_cache_db

    rows = masters if masters is not None else local_cache_db.published_masters()
    done = measure_rows(rows, fetch) if measure else 0
    if done and masters is None:
        rows = local_cache_db.published_masters()
    items = [gallery_item(r) for r in rows]
    counts = Counter({f: 0 for f in GALLERY_FLAGS})
    for item in items:
        counts.update(item["flags"])
    counts["all"] = len(items)
    chosen = [i for i in items if not flag or flag in i["flags"]]
    pages = max(1, -(-len(chosen) // page_size))
    page = min(max(1, int(page or 1)), pages)
    return {"items": chosen[(page - 1) * page_size: page * page_size], "total": len(chosen), "counts": dict(counts),
            "page": page, "pages": pages, "unchecked": sum(1 for i in items if not i["checked"]), "measured": done}


def json_safe(value):
    """قيم قاعدة البيانات (تواريخ، Decimal) كنص لـ JSON."""
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))
