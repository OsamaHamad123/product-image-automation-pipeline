# cutout_finish.py
# اللوحة الشفافة للنشر (OUTPUT_BACKGROUND = transparent) والتشطيب اللي بياخده كل قص معزول قبلها، أياً كان المزوّد.
#
# الصور بتنعرض بتطبيق فيه وضع غامق ووضع فاتح: المربع الأبيض بيبين مكسور بالغامق، فالنسخة الأصلية بـ Cloudinary
# PNG شفافة (RGBA) بلا ظل، والتطبيق بيضيف ظله وخلفيته حسب الوضع؛ النسخة البيضا بتنطلب برابط (cloudinary_storage.
# white_version_url). image_processor.process_product_image_result بيستدعي finish() من مكان واحد بعد العزل.
#
# التشطيب (finish_cutout):
#   1. سد الثقوب المغلقة: منطقة شفافة تماماً محاطة بجسم المنتج (ما بتوصل لحافة الصورة عبر الشفافية) بتنعبى من بكسلات
#      المصدر بعتامة كاملة، بس إذا كانت من المنتج: بكسلات المصدر فيها مختلفة عن مستوى خلفية المصدر (مقدّر من حلقة
#      الإطار). ثقب بكسلاته موحدة وبمستوى الخلفية (ضمن ~2 درجة) بيضل شفاف: فتحة حقيقية متل مقبض غالون الحليب.
#      لوحة بيضا مطبوعة على علبة كرتون (أفتح أو أغمق بشوي من الخلفية، أو فيها تظليل) بتنعبى.
#   2. إزالة تسرب لون الخلفية عن الحواف شبه الشفافة: F = (C − (1−a)·B)/a لكل 0 < a < 1، بس لما بتقرّب اللون من
#      لون المنتج جنبه (مزوّد عمل despill من قبل ما بيتغير).
#   3. فحص الهالة على الغامق (halo_score على #121212): حافة فاتحة حول المنتج بتبين بالوضع الغامق. فوق الحد: إذا
#      المزوّد مش PhotoRoom و PhotoRoom مهيأ ومش موقوف برصيد، عزل واحد جديد بـ PhotoRoom (image_processor._isolate)؛
#      وإلا العلامة dark_halo (للمراجعة: «حواف فاتحة بتبين على الوضع الغامق»).
#   4. والعكس على الأبيض (dark_rim_score): حافة غامقة حول منتج فاتح بتبين بالوضع الفاتح (بقايا خلفية غامقة). بحد أحفظ
#      من الهالة؛ فوقه العلامة dark_rim للمراجعة بس، بلا أي طلب عزل مدفوع.
# عبوة شفافة أو زجاج (categories.is_clear_packaging، clear=True): ما في سد ثقوب، لأن المصدر جوّا الثقب هو الخلفية
# شايفينها من ورا الزجاج، وسدها بيعمل بلاطة رمادية معتمة. الثقوب بتضل شفافة متل ما أعادها المزوّد.
# صورة بلا عزل (الطريقة none، bg_skipped) ما بتصير شفافة: بتضل معتمة على لوحة بيضا مع الملاحظة not_cut_out.

import logging
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import numpy as np
from PIL import Image

from catalog_match import settings
from edge_shadow_engine import CANVAS_FILL_RATIO, compose_on_white_canvas, fit_cutout

logger = logging.getLogger(__name__)

TRANSPARENT = "transparent"
WHITE = "white"
DARK_PREVIEW = (18, 18, 18)        # #121212: خلفية الوضع الغامق اللي بنفحص عليها الهالة
NOTE_NOT_CUT_OUT = "not_cut_out"   # ملاحظة: الصورة انتشرت بلا عزل فبتبين بخلفيتها بالتطبيق
FLAG_DARK_HALO = "dark_halo"       # علامة عرض: حواف فاتحة بتبين على الوضع الغامق (للمراجعة)
FLAG_DARK_RIM = "dark_rim"         # علامة عرض: حواف غامقة بتبين على الوضع الفاتح (للمراجعة)
LIGHT_PREVIEW = (255, 255, 255)    # خلفية الوضع الفاتح اللي بنفحص عليها الحافة الغامقة

HOLE_ALPHA = 16            # بكسل شفافيته أقل من هيك شفاف (جزء من ثقب)
HOLE_LEVEL_TOL = 2.5       # الثقب بمستوى الخلفية: وسيط ألوانه ضمن ~2 درجة من لون الخلفية...
HOLE_TEXTURE_MAX = 3.0     # ...وموحد (انحراف السطوع المعياري)، وإلا هو من المنتج فبينعبى
HOLE_EDGE_PX = 2           # الثقب المسدود بياخد معه الحافة شبه الشفافة حوله
RING_BAND = 0.02           # حلقة الإطار لتقدير الخلفية: 2% من الضلع الأقصر (3 بكسل على الأقل)
RING_TOL = 12              # بكسل الحلقة من الخلفية إذا كان ضمن 12 درجة من وسيطها...
RING_UNIFORM_MIN = 0.6     # ...و60% من الحلقة كذلك، وإلا الخلفية مش معروفة (لا سد ولا إزالة تسرب)
DEFRINGE_RADIUS = 3        # لون المنتج جنب الحافة: متوسط البكسلات المعتمة ضمن 3 بكسل
OPAQUE_ALPHA = 250
HALO_RIM_PX = 3.0          # حافة المنتج على اللوحة: أول 3 بكسل من حد الجزء المرئي (alpha >= 8)
HALO_INNER_PX = (4.0, 12.0)  # والمنتج جنبها: البكسلات المعتمة من 4 لـ 12 بكسل للداخل (حلقة معتمة رفيعة بتبين)
HALO_STEP = 24             # بكسل الحافة على #121212 أفتح من المنتج جنبه بـ 24 درجة...
HALO_VISIBLE = 48          # ...وفاتح بشكل ظاهر
HALO_MAX = 0.10            # نسبة بكسلات الحافة الفاتحة: فوقها dark_halo
HALO_MIN_RIM = 20          # أقل من هيك بكسلات حافة: ما في شي نقيسه
DARK_RIM_STEP = 48         # بكسل الحافة على الأبيض أغمق من المنتج جنبه بـ 48 درجة (ضعف خطوة الهالة: حذر)...
DARK_RIM_VISIBLE = 128     # ...وغامق بشكل ظاهر (أقل من 128)
DARK_RIM_MAX = 0.15        # نسبة بكسلات الحافة الغامقة: فوقها dark_rim (أعلى من HALO_MAX: إطار مطبوع مش هالة)
BLEED_RADIUS = 2           # ألوان البكسلات الشفافة تماماً جنب المنتج = لون حافته (بلا هالة عند التحجيم)


@dataclass
class Backdrop:
    """لون خلفية المصدر (RGB) ونعومتها، من حلقة إطار صورة العمل."""
    colour: np.ndarray
    texture: float


@dataclass
class Finished:
    """نتيجة finish(): اللوحة الشفافة والمزوّد والعلامات والملاحظات بعد التشطيب، و info للتقرير."""
    canvas: Image.Image
    cutout: Image.Image
    provider: str
    isolated: bool
    flags: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    info: dict = field(default_factory=dict)


def output_background(value=None) -> str:
    """'transparent' أو 'white': القيمة المعطاة إذا كانت مدعومة، وإلا الإعداد (OUTPUT_BACKGROUND)."""
    name = str(value or "").strip().lower()
    return name if name in settings.OUTPUT_BACKGROUNDS else settings.output_background()


def flatten_on_white(img: Image.Image) -> Image.Image:
    """الصورة كما تبان على الأبيض (RGB): لبصمات التكرار (pHash والألوان) فتضل متل اللوحات البيضا القديمة."""
    if img.mode not in ("RGBA", "LA", "PA") and "transparency" not in img.info:
        return img.convert("RGB")
    rgba = img.convert("RGBA")
    flat = Image.new("RGB", rgba.size, (255, 255, 255))
    flat.paste(rgba, mask=rgba.getchannel("A"))
    return flat


def _luminance(rgb: np.ndarray) -> np.ndarray:
    return rgb[..., 0] * 0.299 + rgb[..., 1] * 0.587 + rgb[..., 2] * 0.114


def estimate_backdrop(work: Image.Image) -> Optional[Backdrop]:
    """لون الخلفية من حلقة الإطار إذا كانت موحدة (60% منها ضمن 12 درجة من وسيطها)، وإلا None."""
    rgb = np.asarray(work.convert("RGB"), dtype=np.uint8)
    h, w = rgb.shape[:2]
    band = max(3, int(round(min(h, w) * RING_BAND)))
    if h <= 2 * band or w <= 2 * band:
        return None
    ring = np.concatenate([rgb[:band].reshape(-1, 3), rgb[-band:].reshape(-1, 3),
                           rgb[band:-band, :band].reshape(-1, 3),
                           rgb[band:-band, -band:].reshape(-1, 3)]).astype(np.float32)
    median = np.median(ring, axis=0)
    close = np.abs(ring - median).max(axis=1) <= RING_TOL
    if float(close.mean()) < RING_UNIFORM_MIN:
        return None
    return Backdrop(colour=median, texture=float(_luminance(ring[close]).std()))


def aligned_source(work: Image.Image, attempt, cutout: Image.Image, provider: str) -> Optional[np.ndarray]:
    """
    بكسلات المصدر (RGB) بمكان كل بكسل من القص: الإطار المرسل للمزوّد (attempt.frame_rect) محجّم لأبعاد مخرجه. None إذا
    القص هو شفافية المصدر نفسها (ما في بكسلات مستقلة) أو الإطار مش معروف (PHOTOROOM_CROP قص المخرج).
    """
    if provider == "source_alpha":
        return None
    rect = getattr(attempt, "frame_rect", None)
    if rect is None:
        if cutout.size != work.size:
            return None
        rect = (0, 0, work.width, work.height)
    frame = work.convert("RGB").crop(tuple(int(v) for v in rect))
    if frame.size != cutout.size:
        fw, fh = frame.size
        cw, ch = cutout.size
        if fw <= 0 or fh <= 0 or abs(fw / fh - cw / ch) > 0.02 * (fw / fh):
            return None
        frame = frame.resize(cutout.size, Image.Resampling.BILINEAR)
    return np.asarray(frame, dtype=np.uint8)


def fill_enclosed_holes(rgba: np.ndarray, source: Optional[np.ndarray], backdrop: Optional[Backdrop]):
    """
    يسد الثقوب المغلقة اللي هي من المنتج (انظر رأس الملف). يعيد (المصفوفة، المسدودة، الباقية شفافة). بلا مصدر أو بلا
    خلفية معروفة ما بينسد شي (الثقوب كلها بتضل متل ما أعادها المزوّد).
    """
    import cv2

    see = (rgba[..., 3] < HOLE_ALPHA).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(see, connectivity=8)
    if count <= 1:
        return rgba, 0, 0
    outside = set(np.unique(np.concatenate([labels[0], labels[-1], labels[:, 0], labels[:, -1]])).tolist())
    holes = [k for k in range(1, count) if k not in outside]
    if not holes:
        return rgba, 0, 0
    if source is None or backdrop is None:
        return rgba, 0, len(holes)
    out = rgba.copy()
    h, w = see.shape
    kernel = np.ones((2 * HOLE_EDGE_PX + 1, 2 * HOLE_EDGE_PX + 1), np.uint8)
    texture_max = max(HOLE_TEXTURE_MAX, backdrop.texture * 1.5)
    filled = left = 0
    for k in holes:
        x, y, bw, bh = (int(v) for v in stats[k, :4])
        x0, y0 = max(0, x - HOLE_EDGE_PX), max(0, y - HOLE_EDGE_PX)
        x1, y1 = min(w, x + bw + HOLE_EDGE_PX), min(h, y + bh + HOLE_EDGE_PX)
        region = labels[y0:y1, x0:x1] == k
        pixels = source[y0:y1, x0:x1][region].astype(np.float32)
        level = float(np.abs(np.median(pixels, axis=0) - backdrop.colour).max())
        texture = float(_luminance(pixels).std())
        if level <= HOLE_LEVEL_TOL and texture <= texture_max:
            left += 1          # فتحة حقيقية: المصدر فيها هو الخلفية نفسها
            continue
        grown = cv2.dilate(region.astype(np.uint8), kernel).astype(bool) & (out[y0:y1, x0:x1, 3] < 255)
        patch = out[y0:y1, x0:x1]
        patch[grown, :3] = source[y0:y1, x0:x1][grown]
        patch[grown, 3] = 255
        filled += 1
    return out, filled, left


def defringe(rgba: np.ndarray, backdrop: Optional[Backdrop]):
    """
    يشيل لون الخلفية المتسرب للحواف شبه الشفافة: F = (C − (1−a)·B)/a محصوراً بـ 0..255، لكل 0 < a < 1. يُقبل F بس إذا
    كان أقرب من C للون المنتج جنبه (متوسط البكسلات المعتمة ضمن DEFRINGE_RADIUS): حافة نظيفة من قبل ما بتغمق.
    يعيد (المصفوفة، عدد البكسلات اللي تغيرت).
    """
    import cv2

    if backdrop is None:
        return rgba, 0
    alpha = rgba[..., 3]
    edge = (alpha > 0) & (alpha < 255)
    if not edge.any():
        return rgba, 0
    ys, xs = np.nonzero(edge)
    colour = rgba[ys, xs, :3].astype(np.float32)
    a = alpha[ys, xs].astype(np.float32)[:, None] / 255.0
    unblended = np.clip((colour - (1.0 - a) * backdrop.colour[None, :]) / np.maximum(a, 1e-3), 0, 255)
    opaque = (alpha >= OPAQUE_ALPHA).astype(np.float32)
    size = (2 * DEFRINGE_RADIUS + 1, 2 * DEFRINGE_RADIUS + 1)
    den = cv2.boxFilter(opaque, -1, size, normalize=False)[ys, xs]
    near = np.stack([cv2.boxFilter(rgba[..., c].astype(np.float32) * opaque, -1, size, normalize=False)[ys, xs]
                     for c in range(3)], axis=-1) / np.maximum(den, 1e-6)[:, None]
    better = np.abs(unblended - near).sum(axis=-1) < np.abs(colour - near).sum(axis=-1)
    take = (den > 0) & better
    if not take.any():
        return rgba, 0
    out = rgba.copy()
    out[ys[take], xs[take], :3] = np.clip(np.rint(unblended[take]), 0, 255).astype(np.uint8)
    return out, int(take.sum())


def _rim_on(canvas: Image.Image, backdrop):
    """
    (حافة المنتج المقيسة، سطوعها على backdrop، سطوع المنتج المعتم جنبها) على اللوحة كما تنشر، أو None لمنتج بلا حافة
    كافية للقياس (أقل من HALO_MIN_RIM بكسل).
    """
    import cv2

    rgba = np.asarray(canvas.convert("RGBA"), dtype=np.float32)
    rgba = np.pad(rgba, ((8, 8), (8, 8), (0, 0)))          # المنتج ممكن يلمس الإطار: الخارج شفاف دائماً
    alpha = rgba[..., 3]
    visible = alpha >= 8
    if not visible.any():
        return None
    dist = cv2.distanceTransform(visible.astype(np.uint8), cv2.DIST_L2, 3)
    rim = visible & (dist <= HALO_RIM_PX)
    inner = visible & (dist > HALO_INNER_PX[0]) & (dist <= HALO_INNER_PX[1]) & (alpha >= OPAQUE_ALPHA)
    lum = _luminance(rgba[..., :3])
    a = alpha / 255.0
    behind = _luminance(np.asarray(backdrop, dtype=np.float32))
    on_backdrop = a * lum + (1.0 - a) * behind
    size = (2 * int(HALO_INNER_PX[1]) + 1,) * 2
    den = cv2.boxFilter(inner.astype(np.float32), -1, size, normalize=False)
    near = cv2.boxFilter(np.where(inner, lum, 0).astype(np.float32), -1, size, normalize=False) / np.maximum(den, 1e-6)
    measured = rim & (den > 0)
    if int(measured.sum()) < HALO_MIN_RIM:
        return None
    return measured, on_backdrop, near


def halo_score(canvas: Image.Image) -> float:
    """
    نسبة بكسلات حافة المنتج اللي بتبان على #121212 أفتح بوضوح من المنتج جنبها (HALO_STEP، وفاتحة HALO_VISIBLE)، على
    اللوحة كما تنشر. 0 لمنتج بلا حافة كافية للقياس.
    """
    found = _rim_on(canvas, DARK_PREVIEW)
    if found is None:
        return 0.0
    measured, on_dark, near = found
    bright = measured & (on_dark - near > HALO_STEP) & (on_dark > HALO_VISIBLE)
    return float(bright.sum()) / int(measured.sum())


def dark_rim_score(canvas: Image.Image) -> float:
    """
    المرآة: نسبة بكسلات حافة المنتج اللي بتبان على الأبيض أغمق بوضوح من المنتج جنبها (DARK_RIM_STEP، وغامقة
    DARK_RIM_VISIBLE): بقايا خلفية غامقة حول منتج فاتح بتبين بالوضع الفاتح. منتج غامق للآخر ما إله حافة أغمق منه: 0.
    """
    found = _rim_on(canvas, LIGHT_PREVIEW)
    if found is None:
        return 0.0
    measured, on_white, near = found
    dark = measured & (near - on_white > DARK_RIM_STEP) & (on_white < DARK_RIM_VISIBLE)
    return float(dark.sum()) / int(measured.sum())


def _bleed(canvas: Image.Image) -> Image.Image:
    """البكسلات الشفافة تماماً جنب المنتج بتاخد لون حافته (غير مرئية؛ بتمنع هالة لما برنامج يحجّم بلا ضرب مسبق)."""
    import cv2

    arr = np.asarray(canvas, dtype=np.float32).copy()
    alpha = arr[..., 3]
    solid = (alpha >= 128).astype(np.float32)
    size = (2 * BLEED_RADIUS + 1,) * 2
    den = cv2.boxFilter(solid, -1, size, normalize=False)
    clear = alpha == 0
    arr[..., :3][clear] = 0
    target = clear & (den > 0)
    if target.any():
        for c in range(3):
            near = cv2.boxFilter(np.ascontiguousarray(arr[..., c] * solid), -1, size, normalize=False) / np.maximum(den, 1e-6)
            arr[..., c][target] = near[target]
    return Image.fromarray(np.clip(np.rint(arr), 0, 255).astype(np.uint8), "RGBA")


def transparent_canvas(cutout, canvas_size: Tuple[int, int], fill: Optional[float] = None) -> Image.Image:
    """
    لوحة RGBA شفافة بالأبعاد المطلوبة: المنتج مقصوص على حدود شفافيته، ضلعه الأطول بيعبّي fill من المربع
    (OUTPUT_PRODUCT_FILL، افتراضياً 0.88) وموسّط. بلا ظل. يرفع ValueError('empty_cutout') لقص فارغ.
    """
    fill = settings.output_product_fill() if fill is None else float(fill)
    product, (x, y) = fit_cutout(cutout, canvas_size, fill)
    canvas = Image.new("RGBA", (int(canvas_size[0]), int(canvas_size[1])), (0, 0, 0, 0))
    canvas.paste(product, (x, y))
    return _bleed(canvas)


def finish_cutout(work: Image.Image, cutout: Image.Image, attempt, provider: str, backdrop=None,
                  fill_holes: bool = True):
    """
    سد الثقوب ثم إزالة التسرب على القص بدقته الأصلية. يعيد (القص RGBA، info). fill_holes=False (عبوة شفافة أو زجاج):
    الثقوب بتنعد بس وبتضل شفافة (hole_fill: skipped_clear).
    """
    backdrop = backdrop if backdrop is not None else estimate_backdrop(work)
    rgba = np.asarray(cutout.convert("RGBA"), dtype=np.uint8)
    source = aligned_source(work, attempt, cutout, provider) if fill_holes else None
    rgba, filled, left = fill_enclosed_holes(rgba, source, backdrop if fill_holes else None)
    rgba, changed = defringe(rgba, backdrop)
    out = Image.fromarray(np.ascontiguousarray(rgba), "RGBA")
    info = {"holes_filled": filled, "holes_left": left, "defringed": changed,
            "backdrop": [int(round(v)) for v in backdrop.colour] if backdrop is not None else None}
    if not fill_holes:
        info["hole_fill"] = "skipped_clear"
    return out, info


def photoroom_ready() -> Optional[str]:
    """None إذا ممكن نعيد العزل بـ PhotoRoom، وإلا السبب: no_key أو paused:<رمز> (قاطع الرصيد إذا موجود)."""
    import image_processor as ip

    if not ip._method_has_key("photoroom"):
        return "no_key"
    breaker = getattr(ip, "cloud_breaker", None)
    if callable(breaker):
        try:
            code = breaker().paused_code("photoroom")
        except Exception:  # noqa: BLE001 - قاطع بشكل مختلف: نعامله كغير موقوف
            code = None
        if code:
            return f"paused:{code}"
    return None


def _photoroom_retry(work: Image.Image, canvas_size):
    """عزل واحد جديد بـ PhotoRoom على صورة العمل كاملة، ممرر على بوابة القص. يعيد (المحاولة أو None، رمز)."""
    import image_processor as ip

    cutout, error = ip._isolate(work, "photoroom")
    if cutout is None:
        return None, str(error or "photoroom_failed")
    whole = (0, 0, work.width, work.height)
    if ip._photoroom_crop():
        attempt = ip._gated(cutout, "photoroom", None, ip._NO_CROP, canvas_size, "photoroom+halo", None,
                            frame_checks=False)
    else:
        attempt = ip._gated(cutout, "photoroom", work.size, ip._NO_CROP, canvas_size, "photoroom+halo", whole)
    if attempt.cutout is None:
        return None, str(attempt.error or "photoroom_empty_cutout")
    if attempt.flags:
        return None, "flags:" + ",".join(attempt.flags)
    attempt.uncertainty = cutout.info.get("uncertainty_score")
    return attempt, None


def _render(work, cutout, attempt, provider, canvas_size, enhance, backdrop, fill_holes=True):
    import image_processor as ip

    finished, info = finish_cutout(work, cutout, attempt, provider, backdrop, fill_holes=fill_holes)
    if enhance:
        finished = ip._enhance_rgb(finished)
    canvas = transparent_canvas(finished, canvas_size)
    info["halo"] = round(halo_score(canvas), 4)
    return finished, canvas, info


def finish(img: Image.Image, cutout: Image.Image, attempt, provider: str, isolated: bool, flags, notes,
           canvas_size, enhance: bool = False, method: Optional[str] = None, clear: bool = False) -> Finished:
    """
    لوحة النشر الشفافة لقص معزول (أو لوحة بيضا معتمة لصورة بلا عزل، method none). img: الصورة المصدر كما دخلت العزل؛
    attempt: محاولة العزل المختارة (frame_rect / frame_size)، أو None. enhance: تحسين الألوان بعد التشطيب.
    clear: عبوة شفافة أو زجاج (categories.is_clear_packaging): بلا سد ثقوب.
    """
    import image_processor as ip

    flags, notes = list(flags or []), list(notes or [])
    if method == "none" or provider == "none":
        rgba = ip._enhance_rgb(cutout) if enhance else cutout
        canvas = compose_on_white_canvas(rgba, canvas_size, CANVAS_FILL_RATIO)
        if NOTE_NOT_CUT_OUT not in notes:
            notes.append(NOTE_NOT_CUT_OUT)
        return Finished(canvas, rgba, provider, isolated, flags, notes, {"background": "opaque"})

    done = _finish_transparent(img, cutout, attempt, provider, isolated, flags, notes, canvas_size, enhance,
                               fill_holes=not clear)
    # المرآة على الأبيض: حافة غامقة بتبين بالوضع الفاتح. للمراجعة بس، بلا أي عزل مدفوع
    score = dark_rim_score(done.canvas)
    done.info["dark_rim"] = round(score, 4)
    if score > DARK_RIM_MAX:
        logger.info("حواف غامقة على الأبيض (dark_rim=%.3f، %s)", score, done.provider)
        if FLAG_DARK_RIM not in done.flags:
            done.flags.append(FLAG_DARK_RIM)
        done.isolated = False
    return done


def _finish_transparent(img, cutout, attempt, provider, isolated, flags, notes, canvas_size, enhance,
                        fill_holes=True) -> Finished:
    """التشطيب واللوحة الشفافة وفحص الهالة (وعزل PhotoRoom الواحد لها)، لقص معزول."""
    import image_processor as ip

    work = ip._flatten_on_white(img) if ip.has_meaningful_transparency(img) else img.convert("RGB")
    backdrop = estimate_backdrop(work)
    finished, canvas, info = _render(work, cutout, attempt, provider, canvas_size, enhance, backdrop, fill_holes)
    info["background"] = TRANSPARENT
    if info["halo"] <= HALO_MAX:
        return Finished(canvas, finished, provider, isolated, flags, notes, info)

    logger.info("حواف فاتحة على الغامق (halo=%.3f، %s)", info["halo"], provider)
    retry_ok = isolated and provider != "photoroom"
    why = photoroom_ready() if retry_ok else None
    if retry_ok and why is None:
        retry, error = _photoroom_retry(work, canvas_size)
        if retry is not None:
            r_finished, r_canvas, r_info = _render(work, retry.cutout, retry, "photoroom", canvas_size, enhance,
                                                   backdrop, fill_holes)
            if r_info["halo"] <= HALO_MAX:
                logger.info("أُعيد العزل بـ PhotoRoom بسبب حواف فاتحة (%s: %.3f -> %.3f)", provider, info["halo"],
                            r_info["halo"])
                r_info.update(background=TRANSPARENT, halo_retry="photoroom", halo_before=info["halo"],
                              provider_before=provider)
                if getattr(retry, "uncertainty", None) is not None:
                    r_info["uncertainty"] = retry.uncertainty      # تقدير PhotoRoom للعزل الجديد (image_processor)
                return Finished(r_canvas, r_finished, "photoroom", True, list(retry.flags),
                                list(retry.notes), r_info)
            error = f"halo:{r_info['halo']}"
        info["halo_retry"] = f"photoroom_failed:{error}"
    elif retry_ok:
        info["halo_retry"] = f"skipped:{why}"
    if FLAG_DARK_HALO not in flags:
        flags.append(FLAG_DARK_HALO)
    return Finished(canvas, finished, provider, False, flags, notes, info)
