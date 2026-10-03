# edge_shadow_engine.py
# تركيب المنتج المعزول على لوحة بيضاء ثابتة الأبعاد (مع ظلال استوديو اختيارية).
#
# المبادئ:
# - نستخدم مخرجات مزوّد العزل (RGBA) كما هي: لا نعيد بناء الصورة من ألوان الصورة الخام،
#   ولا نملأ الثقوب الداخلية (مقابض العبوات تبقى شفافة فتظهر بيضاء على اللوحة).
# - اللوحة النهائية دائماً RGB معتمة بيضاء بالأبعاد المطلوبة، والمنتج (حدود قناة الشفافية)
#   محجّم ليملأ 88% من الضلع المقيِّد وموسّط.

import logging
from typing import Optional, Tuple, Union

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

logger = logging.getLogger(__name__)

# نسبة إشغال المنتج من اللوحة (هامش 6% من كل جهة)
CANVAS_FILL_RATIO = 0.88
# بكسلات بشفافية أقل من هذا الحد لا تدخل في حساب حدود المنتج (ضباب الحواف)
ALPHA_BBOX_THRESHOLD = 8
# مكوّن (بكسلات شفافيتها فوق ALPHA_BBOX_THRESHOLD) لا يحتوي أي بكسل أعتم من هذا الحد هو ضباب:
# شبه غير مرئي على الأبيض (12.5% أو أقل) لكنه كان يوسّع حدود المنتج فيصغر المنتج ويخرج عن المركز
HAZE_ALPHA_MAX = 32
WHITE_RGBA = (255, 255, 255, 255)

ImageLike = Union[str, Image.Image]


def _as_rgba(image: ImageLike) -> Image.Image:
    """يفتح مساراً أو يستخدم صورة PIL ويعيد نسخة RGBA محمّلة في الذاكرة."""
    if isinstance(image, Image.Image):
        return image.convert("RGBA")
    with Image.open(image) as img:
        img.load()
        return img.convert("RGBA")


def alpha_bbox(rgba: Image.Image, threshold: int = ALPHA_BBOX_THRESHOLD) -> Optional[Tuple[int, int, int, int]]:
    """حدود المنتج (left, top, right, bottom) من قناة الشفافية، أو None إذا كانت الصورة فارغة."""
    alpha = np.asarray(rgba.getchannel("A"))
    ys, xs = np.nonzero(alpha > threshold)
    if ys.size == 0:
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def fit_cutout(cutout: ImageLike, canvas_size: Tuple[int, int],
               fill: float = CANVAS_FILL_RATIO) -> Tuple[Image.Image, Tuple[int, int]]:
    """
    يقص المنتج على حدود الشفافية ويحجّمه ليملأ fill من اللوحة مع الحفاظ على التناسب.
    يعيد (صورة RGBA المحجّمة، موضع اللصق الموسّط). يرفع ValueError('empty_cutout') إذا لم يوجد منتج.
    """
    rgba = _as_rgba(cutout)
    box = alpha_bbox(rgba)
    if box is None:
        raise ValueError("empty_cutout")
    product = rgba.crop(box)

    canvas_w, canvas_h = int(canvas_size[0]), int(canvas_size[1])
    max_w = max(1, int(canvas_w * fill))
    max_h = max(1, int(canvas_h * fill))
    scale = min(max_w / product.width, max_h / product.height)
    new_w = min(max_w, max(1, int(round(product.width * scale))))
    new_h = min(max_h, max(1, int(round(product.height * scale))))
    if (new_w, new_h) != product.size:
        # Pillow يحجّم RGBA بقيم مضروبة مسبقاً بالشفافية، فلا تتسرب ألوان البكسلات الشفافة إلى الحواف
        product = product.resize((new_w, new_h), Image.Resampling.LANCZOS)
    x = (canvas_w - new_w) // 2
    y = (canvas_h - new_h) // 2
    return product, (x, y)


def compose_on_white_canvas(cutout: ImageLike, canvas_size: Tuple[int, int],
                            fill: float = CANVAS_FILL_RATIO) -> Image.Image:
    """يركّب المنتج المعزول على لوحة بيضاء معتمة ويعيد صورة RGB بالأبعاد المطلوبة تماماً."""
    product, (x, y) = fit_cutout(cutout, canvas_size, fill)
    canvas = Image.new("RGBA", (int(canvas_size[0]), int(canvas_size[1])), WHITE_RGBA)
    canvas.alpha_composite(product, (x, y))
    return canvas.convert("RGB")


class EdgeShadowEngine:
    """
    أدوات ما بعد العزل: تنظيف نقاط القناع المعزولة الصغيرة، وتوليد ظلال استوديو اختيارية
    على نفس اللوحة البيضاء الثابتة.
    """

    @staticmethod
    def clean_alpha_matte_via_cca(alpha_mask: np.ndarray, min_noise_area: int = 25) -> np.ndarray:
        """
        يزيل المكونات المتصلة الصغيرة جداً (نقاط متناثرة) من قناع الشفافية دون لمس المكون الرئيسي
        أو ملء أي ثقب داخلي.
        """
        import cv2

        alpha = np.ascontiguousarray(alpha_mask, dtype=np.uint8)
        _, thresh = cv2.threshold(alpha, 15, 255, cv2.THRESH_BINARY)
        num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(thresh, connectivity=8)
        if num_labels <= 2:
            return alpha

        sizes = stats[:, cv2.CC_STAT_AREA]
        main_label = 1 + int(np.argmax(sizes[1:]))
        # جدول بحث متجه بدل حلقة على كل مكون (قد تكون آلاف النقاط)
        drop = sizes <= min_noise_area
        drop[0] = False
        drop[main_label] = False
        if not drop.any():
            return alpha
        cleaned = alpha.copy()
        cleaned[drop[labels]] = 0
        return cleaned

    @staticmethod
    def remove_alpha_haze(alpha_mask: np.ndarray, threshold: int = ALPHA_BBOX_THRESHOLD,
                          haze_max: int = HAZE_ALPHA_MAX) -> np.ndarray:
        """
        يزيل الضباب: كل مكوّن متصل من البكسلات التي تحسبها alpha_bbox ضمن المنتج (شفافية > threshold)
        ولا يحتوي أي بكسل أعتم من haze_max. بهذا يتفق التنظيف وحساب حدود المنتج على ما هو منتج.
        الضباب الملتصق بالمنتج يبقى (تحكم عليه بوابة الجودة)، ولا يتغير شيء إذا لم يبق غير الضباب.
        """
        import cv2

        alpha = np.ascontiguousarray(alpha_mask, dtype=np.uint8)
        num_labels, labels = cv2.connectedComponents((alpha > threshold).astype(np.uint8), connectivity=8)
        if num_labels <= 1:
            return alpha
        keep = np.zeros(num_labels, dtype=bool)
        keep[np.unique(labels[alpha > haze_max])] = True
        keep[0] = True
        if keep.all() or not keep[1:].any():
            return alpha
        cleaned = alpha.copy()
        cleaned[~keep[labels]] = 0
        return cleaned

    @classmethod
    def process_mask(cls, provider_rgba: ImageLike, target_path: Optional[str] = None) -> Image.Image:
        """
        يعيد مخرجات مزوّد العزل كما هي (ألوانه وقناعه) بعد إزالة النقاط المعزولة الصغيرة والضباب المنفصل فقط.
        لا يعيد بناء الصورة من الألوان الخام ولا يملأ الثقوب.
        """
        rgba = _as_rgba(provider_rgba)
        alpha = np.asarray(rgba.getchannel("A"))
        foreground = int(np.count_nonzero(alpha > 15))
        # الحد الأدنى نسبي لحجم المنتج: 0.1% من البكسلات الأمامية أو 25 بكسل
        min_area = max(25, int(foreground * 0.001))
        cleaned = cls.clean_alpha_matte_via_cca(alpha, min_noise_area=min_area)
        cleaned = cls.remove_alpha_haze(cleaned)
        if not np.array_equal(cleaned, alpha):
            rgba = rgba.copy()
            rgba.putalpha(Image.fromarray(cleaned))
        if target_path:
            rgba.save(target_path, "PNG")
        return rgba

    @classmethod
    def apply_studio_shadows(cls, cutout: ImageLike, target_size: Tuple[int, int] = (800, 800),
                             output_path: Optional[str] = None) -> Image.Image:
        """
        نفس اللوحة البيضاء الثابتة (نفس الأبعاد ونفس إشغال 88% والتوسيط) مع ظل تلامس
        وظل سقوط ناعمين تحت المنتج. تُستخدم فقط عند تفعيل ENABLE_STUDIO_SHADOWS.
        """
        canvas_w, canvas_h = int(target_size[0]), int(target_size[1])
        product, (x, y) = fit_cutout(cutout, (canvas_w, canvas_h))
        alpha = product.getchannel("A")

        canvas = Image.new("RGBA", (canvas_w, canvas_h), WHITE_RGBA)

        # ظل السقوط: صورة ظلية للمنتج مموهة بعتامة 16% ومزاحة للأسفل واليمين
        cast = Image.new("RGBA", product.size, (25, 25, 25, 255))
        cast.putalpha(alpha.point(lambda p: int(p * 0.16)))
        pad = 24
        cast_layer = Image.new("RGBA", (product.width + 2 * pad, product.height + 2 * pad), (0, 0, 0, 0))
        cast_layer.paste(cast, (pad, pad))
        cast_layer = cast_layer.filter(ImageFilter.GaussianBlur(radius=12))
        canvas.alpha_composite(cast_layer, _clip_dest(canvas, cast_layer, x - pad + 8, y - pad + 12))

        # ظل التلامس: قطع ناقص ناعم عند قاعدة المنتج
        contact_w = max(2, int(product.width * 0.9))
        contact_h = max(2, int(product.height * 0.06))
        ellipse = Image.new("L", (contact_w + 16, contact_h + 16), 0)
        ImageDraw.Draw(ellipse).ellipse((8, 8, 8 + contact_w, 8 + contact_h), fill=int(255 * 0.5))
        ellipse = ellipse.filter(ImageFilter.GaussianBlur(radius=4))
        contact = Image.new("RGBA", ellipse.size, (20, 20, 20, 255))
        contact.putalpha(ellipse)
        cx = (canvas_w - ellipse.width) // 2
        cy = y + product.height - ellipse.height // 2
        canvas.alpha_composite(contact, _clip_dest(canvas, contact, cx, cy))

        canvas.alpha_composite(product, (x, y))
        result = canvas.convert("RGB")
        if output_path:
            result.save(output_path, "PNG")
        return result


def _clip_dest(canvas: Image.Image, layer: Image.Image, x: int, y: int) -> Tuple[int, int]:
    """alpha_composite لا يقبل إحداثيات سالبة؛ نحصر موضع الطبقة داخل اللوحة."""
    x = max(0, min(int(x), max(0, canvas.width - layer.width)))
    y = max(0, min(int(y), max(0, canvas.height - layer.height)))
    return x, y
