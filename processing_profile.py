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

from dataclasses import dataclass

import config


@dataclass(frozen=True)
class ProcessingProfile:
    canvas: int          # ضلع اللوحة البيضاء المربعة بالبكسل
    enhance: bool        # تحسين الألوان (enable_image_enhancement)
    bg_method: str       # طريقة عزل الخلفية (bg_removal_method)

    @property
    def target(self):
        return (self.canvas, self.canvas)

    def as_dict(self):
        return {"canvas": self.canvas, "enhance": self.enhance, "bg_method": self.bg_method}


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
                             bg_method=method)
