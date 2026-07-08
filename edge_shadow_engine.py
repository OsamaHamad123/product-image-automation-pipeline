# edge_shadow_engine.py
# موديول تنعيم حواف التقطيع وتوليد ظلال الاستوديو المركبة (Guided Filtering & Studio Shadows)

import cv2
import numpy as np
from PIL import Image, ImageFilter, ImageOps

class EdgeShadowEngine:
    """
    محرك التحقق الرياضي وإصلاح حواف التقطيع وتوليد ظلال استوديو ناعمة وتفاعلية.
    """

    @staticmethod
    def guided_filter(I, p, r=4, eps=1e-3):
        """
        تطبيق فلتر التوجيه لكايمينغ هي (Kaiming He's Guided Filter) لمطابقة قناع التقطيع
        مع حواف الصورة الأصلية بدقة متناهية.
        """
        I = I.astype(np.float64) / 255.0
        p = p.astype(np.float64) / 255.0

        # حساب المتوسطات
        mean_I = cv2.boxFilter(I, cv2.CV_64F, (r, r))
        mean_p = cv2.boxFilter(p, cv2.CV_64F, (r, r))
        mean_Ip = cv2.boxFilter(I * p, cv2.CV_64F, (r, r))
        
        # التباين المشترك
        cov_Ip = mean_Ip - mean_I * mean_p
        
        # تباين الإشارة المرشدة
        mean_II = cv2.boxFilter(I * I, cv2.CV_64F, (r, r))
        var_I = mean_II - mean_I * mean_I

        # معامل التحويل الخطي a و b
        a = cov_Ip / (var_I + eps)
        b = mean_p - a * mean_I

        # تنعيم المعاملات بالمتوسط المحلي
        mean_a = cv2.boxFilter(a, cv2.CV_64F, (r, r))
        mean_b = cv2.boxFilter(b, cv2.CV_64F, (r, r))

        # القناع النهائي النظيف
        q = mean_a * I + mean_b
        return (np.clip(q, 0.0, 1.0) * 255.0).astype(np.uint8)

    @staticmethod
    def clean_alpha_matte_via_cca(alpha_mask, min_noise_area=25):
        """
        تطبيق Connected Component Analysis (CCA) لإزالة الجزيئات المعزولة من القناع (Orphans).
        """
        # تحويل القناع لنوع ثنائي
        _, thresh = cv2.threshold(alpha_mask, 15, 255, cv2.THRESH_BINARY)
        
        # تحليل المكونات المتصلة 8-Connectivity
        num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(thresh, connectivity=8)
        if num_labels <= 1:
            return alpha_mask

        # المكون الأكبر مساحة (وهو جسم المنتج الرئيسي باستثناء الخلفية)
        sizes = stats[:, cv2.CC_STAT_AREA]
        
        # نجد ترتيب المكونات ونترك الخلفية (المكون 0 غالباً الأكبر مساحة بالخلفيات)
        # لذا نبحث عن أكبر مكون بعد الخلفية
        main_label = 1
        max_size = 0
        for i in range(1, num_labels):
            if sizes[i] > max_size:
                max_size = sizes[i]
                main_label = i

        # تنظيف أي أجزاء صغيرة طائرة وعزلها
        cleaned_alpha = alpha_mask.copy()
        for i in range(1, num_labels):
            if i != main_label:
                # إذا كانت المساحة صغيرة جداً، نعتبرها تشويه ونلغيها
                if sizes[i] <= min_noise_area:
                    cleaned_alpha[labels == i] = 0
                    
        return cleaned_alpha

    @staticmethod
    def smooth_edges_morphology(alpha_mask, radius=3):
        """
        تطبيق عمليات التآكل والتمدد المورفولوجي وتنعيم الحواف بالـ Gaussian.
        """
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (radius, radius))
        eroded = cv2.erode(alpha_mask, kernel)
        dilated = cv2.dilate(alpha_mask, kernel)
        
        # إيجاد حافة التباين
        boundary = cv2.subtract(dilated, eroded)
        
        # تنعيم الحدود فقط لمنع القص الحاد
        smoothed = alpha_mask.copy()
        blur = cv2.GaussianBlur(alpha_mask, (radius*2+1, radius*2+1), 0)
        
        # تطبيق التنعيم على الحواف
        smoothed[boundary > 0] = blur[boundary > 0]
        return smoothed

    @classmethod
    def process_mask(cls, original_rgb_path, raw_alpha_mask, target_path):
        """
        معالجة القناع بالكامل ودمجه مع الصورة الأصلية لإنتاج صورة شفافة عالية الدقة والنعومة.
        """
        original_img = cv2.imread(original_rgb_path)
        if original_img is None:
            return False

        # قراءة القناع كصورة تدرج رمادي
        if isinstance(raw_alpha_mask, str):
            alpha = cv2.imread(raw_alpha_mask, cv2.IMREAD_GRAYSCALE)
        else:
            alpha = raw_alpha_mask
            
        if alpha is None:
            return False

        # 1. إزالة الأجزاء المعزولة CCA
        alpha_cleaned = cls.clean_alpha_matte_via_cca(alpha)

        # 2. تنعيم الحواف ومطابقتها بفلتر التوجيه Guided Filter
        gray_guidance = cv2.cvtColor(original_img, cv2.COLOR_BGR2GRAY)
        alpha_refined = cls.guided_filter(gray_guidance, alpha_cleaned)

        # 3. معالجة مورفولوجية خفيفة للتنعيم النهائي
        alpha_final = cls.smooth_edges_morphology(alpha_refined)

        # 4. دمج القناع مع الصورة كصورة شفافة RGBA
        b, g, r = cv2.split(original_img)
        rgba = cv2.merge([b, g, r, alpha_final])
        cv2.imwrite(target_path, rgba)
        return True

    @classmethod
    def apply_studio_shadows(cls, input_rgba_path, output_webp_path, target_size=(800, 800)):
        """
        تطبيق الظلال الاستوديو التفاعلية المركبة (ظل تلامس + ظل سقوط 45 درجة)
        وتوسيط المنتج وتعبئة الإطار.
        """
        try:
            import config
            with Image.open(input_rgba_path) as img:
                img = img.convert("RGBA")
                
                # 1. تغيير الحجم بشكل متناسب مع الهامش
                enable_shadows = getattr(config, 'ENABLE_STUDIO_SHADOWS', False)
                scale = 0.88 if not enable_shadows else 0.82
                img.thumbnail((int(target_size[0] * scale), int(target_size[1] * scale)), Image.Resampling.LANCZOS)
                
                alpha = img.getchannel('A')
                
                # 2. إنشاء لوحة استوديو بيضاء بالكامل
                studio_canvas = Image.new("RGBA", target_size, (255, 255, 255, 255))

                # 3. حساب مواقع التوسيط للمنتج
                x = (target_size[0] - img.width) // 2
                y = (target_size[1] - img.height) // 2
                
                if enable_shadows:
                    # --- أ. إنشاء ظل التلامس الوثيق (Contact Shadow) ---
                    # هو ظل مسطح ضيق تحت جسم المنتج مباشرة
                    contact_shadow_h = max(2, int(img.height * 0.08))
                    contact_shadow_w = int(img.width * 0.95)
                    
                    contact_shadow = Image.new("RGBA", (contact_shadow_w, contact_shadow_h), (20, 20, 20, 255))
                    # إنشاء قناع بيضاوي ناعم جداً
                    ellipse_mask = Image.new("L", (contact_shadow_w, contact_shadow_h), 0)
                    draw_cv = np.zeros((contact_shadow_h, contact_shadow_w), dtype=np.uint8)
                    cv2.ellipse(draw_cv, (contact_shadow_w//2, contact_shadow_h//2), (contact_shadow_w//2, contact_shadow_h//2), 0, 0, 360, 255, -1)
                    ellipse_mask = Image.fromarray(cv2.GaussianBlur(draw_cv, (5, 5), 0))
                    
                    contact_shadow_alpha = ellipse_mask.point(lambda p: int(p * 0.65)) # عتامة 65% للظل الملامس
                    contact_shadow.putalpha(contact_shadow_alpha)
                    
                    # --- ب. إنشاء ظل السقوط الناعم (Cast Soft Shadow) ---
                    # يمثل اتجاه الضوء الساقط بزاوية 45 درجة (يمين وأسفل)
                    cast_shadow = Image.new("RGBA", img.size, (25, 25, 25, 255))
                    cast_shadow.putalpha(alpha)
                    
                    # إزاحة وتشويه هندسي خفيف لمحاكاة زاوية الضوء
                    shadow_large = cast_shadow.resize((img.width + 16, img.height + 16), Image.Resampling.BILINEAR)
                    shadow_blurred = shadow_large.filter(ImageFilter.GaussianBlur(radius=20))
                    
                    # تخفيف الظل ليصبح ناعماً وشفافاً (عتامة 16%)
                    shadow_alpha = shadow_blurred.getchannel('A')
                    shadow_alpha = shadow_alpha.point(lambda p: int(p * 0.16))
                    shadow_blurred.putalpha(shadow_alpha)

                    # حساب مواقع التوسيط للظلال
                    cx = (target_size[0] - contact_shadow_w) // 2
                    cy = y + img.height - (contact_shadow_h // 2)
                    sx = (target_size[0] - shadow_large.width) // 2 + 10
                    sy = (target_size[1] - shadow_large.height) // 2 + 18

                    # دمج طبقات الظلال بالترتيب
                    # أولاً: ظل السقوط الناعم
                    studio_canvas.paste(shadow_blurred, (sx, sy), mask=shadow_blurred)
                    # ثانياً: ظل التلامس الوثيق
                    studio_canvas.paste(contact_shadow, (cx, cy), mask=contact_shadow)
                
                # ثالثاً: المنتج المعزول ذو الحواف الناعمة (دائماً)
                studio_canvas.paste(img, (x, y), mask=img)

                # 5. الحفظ بصيغة WebP خفيفة ومحسنة
                studio_canvas.convert("RGB").save(output_webp_path, "WEBP", quality=85, method=4)
                if enable_shadows:
                    print("✅ [Studio Shadow Engine] تم دمج حواف المنتج وتطبيق ظلال استوديو تفاعلية مركبة بنجاح!")
                else:
                    print("✅ [Studio Shadow Engine] تم دمج حواف المنتج وتوسيطه على خلفية بيضاء نقية بدون ظلال بنجاح!")
                return True
        except Exception as e:
            print(f"❌ [Studio Shadow Engine Error] خطأ أثناء تطبيق المعالجة والتوسيط: {e}")
            return False
