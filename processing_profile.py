# processing_profile.py
# ملف معالجة الصورة الواحد لكل نشر: النشر التلقائي في العامل، واعتماد المراجع، والرفع اليدوي، والوضع
# التسلسلي القديم كلها تأخذ أبعاد اللوحة وتحسين الألوان وطريقة عزل الخلفية من هنا، فلا تختلف الصورة المنشورة
# لنفس المنتج باختلاف الزر أو المسار الذي نشرها.
#
# المصدر الوحيد: صفحة الإعدادات (تبويب «معالجة الصور»: system_settings.output_canvas_size و
# enable_image_enhancement و bg_removal_method، يقرؤها config.load_db_config)، و .env عند غيابها.
# متى تُقرأ: كل عملية تقرأ صفحة الإعدادات مرة عند بدئها. الجسر (cli_bridge) عملية جديدة لكل طلب، فيرى آخر حفظ؛
# العامل يقرؤها عند بدء التشغيل (main.load_run_config، ومعها تجاوزات run_config.json: aiEnhance و bgRemovalMethod)،
# فتغيير الإعدادات أثناء تشغيل العامل يسري على تشغيله التالي ولا تختلف لوحات التشغيل الواحد.
# - اللوحة: OUTPUT_CANVAS_SIZE كمربع (catalog_match.settings.output_canvas_size، افتراضياً 800).
#   IMAGE_TARGET_SIZE (800x800 ثابتة في config.py) لم تعد تحدد لوحة النشر.
# - قيم الطلب (target_width / target_height / enhance / bg_removal_method) لا تغيّر الملف.
# - bg_fallback (تبويب «معالجة الصور»: system_settings.bg_fallback، config.BG_FALLBACK، catalog_match.settings.bg_fallback):
#   'local' (الافتراضي) لما تفشل الطريقة السحابية بالرصيد أو المفتاح أو الحصة تجرّب image_processor طريقة محلية مجانية
#   منزّلة (rembg بموديل BiRefNet، REMBG_MODEL؛ بدون GrabCut أبداً) وتمررها على بوابة القص نفسها؛ 'off' السلوك القديم. لا تنزل أبداً لـ «بدون عزل» تلقائياً.
# - «بدون عزل الخلفية» (bg_removal_method = none، وزر «تجاوز عزل الخلفية» بصفحة الصحة): اختيار المالك نفسه، فلوحة
#   النشر هي الصورة كما هي على لوحة بيضا وتُنشر نظيفة (main.publish_image: bg_skipped). skips_background يقولها.
# - الخلفية: OUTPUT_BACKGROUND (catalog_match.settings.output_background): 'transparent' (الافتراضي، PNG شفافة بلا ظل
#   للتطبيق بالوضع الغامق والفاتح، cutout_finish) أو 'white' (اللوحة البيضا المعتمة متل قبل).

from dataclasses import dataclass

import config

# أسماء «بدون عزل» كما يوحدها image_processor._METHOD_ALIASES ('no' و 'off' تعني 'none')
NO_REMOVAL_METHODS = frozenset({"none", "no", "off"})


@dataclass(frozen=True)
class ProcessingProfile:
    canvas: int          # ضلع اللوحة البيضاء المربعة بالبكسل
    enhance: bool        # تحسين الألوان (enable_image_enhancement)
    bg_method: str       # طريقة عزل الخلفية (bg_removal_method)
    bg_fallback: str = "local"   # لما يخلص رصيد المزوّد السحابي: 'local' طريقة محلية مجانية منزّلة، أو 'off' (bg_fallback)
    background: str = None   # خلفية اللوحة (output_background): 'transparent' أو 'white'؛ None = الإعداد وقت المعالجة

    @property
    def target(self):
        return (self.canvas, self.canvas)

    @property
    def skips_background(self):
        """المالك اختار «بدون عزل الخلفية»: الصورة تُنشر كما هي على لوحة بيضا، وهذا ليس فشلاً في العزل."""
        return str(self.bg_method or "").strip().lower() in NO_REMOVAL_METHODS

    def as_dict(self):
        from catalog_match import settings

        return {"canvas": self.canvas, "enhance": self.enhance, "bg_method": self.bg_method,
                "bg_fallback": self.bg_fallback, "background": self.background or settings.output_background()}


def current():
    """
    الملف من قيم config كما حمّلتها هذه العملية (يُحسب عند كل نشر، ولا يعيد قراءة قاعدة البيانات): للجسر آخر حفظ
    لصفحة الإعدادات، وللعامل الإعدادات عند بدء تشغيله (انظر رأس الملف).
    """
    from catalog_match import settings

    try:
        side = int(settings.output_canvas_size())
    except (TypeError, ValueError):
        side = 0
    if side <= 0:
        side = int(settings.DEFAULTS["OUTPUT_CANVAS_SIZE"])
    method = str(getattr(config, "BG_REMOVAL_METHOD", "") or "").strip().lower() or "photoroom"
    return ProcessingProfile(canvas=side, enhance=bool(getattr(config, "ENABLE_IMAGE_ENHANCEMENT", False)),
                             bg_method=method, bg_fallback=settings.bg_fallback(),
                             background=settings.output_background())
