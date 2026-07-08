# aesthetics_engine.py
# موديول التقييم الجمالي ومقاييس الوضوح المتقدمة (AI Aesthetics & Clarity Engine)

import os
import cv2
import numpy as np
from PIL import Image

class AestheticPredictor:
    """
    نموذج مبسط لتقييم المظهر الجمالي المعتمد على متجهات نموذج CLIP
    وفقاً لبنية الطبقات الخطية مسبقة التدريب.
    """
    def __init__(self, device="cpu"):
        self.device = device
        self.model = None

    def load_model(self, weights_path):
        """
        تحميل الأوزان مسبقة التدريب في الذاكرة.
        """
        import torch
        import torch.nn as nn
        
        class PyTorchAestheticModel(nn.Module):
            def __init__(self):
                super().__init__()
                self.layers = nn.Sequential(
                    nn.Linear(768, 1024),
                    nn.Dropout(0.2),
                    nn.Linear(1024, 128),
                    nn.Dropout(0.2),
                    nn.Linear(128, 64),
                    nn.Dropout(0.1),
                    nn.Linear(64, 16),
                    nn.Linear(16, 1)
                )
            def forward(self, x):
                return self.layers(x)
                
        try:
            self.model = PyTorchAestheticModel()
            state_dict = torch.load(weights_path, map_location=self.device)
            self.model.load_state_dict(state_dict)
            self.model.to(self.device)
            self.model.eval()
            print("✅ [Aesthetics Model] تم تحميل أوزان نموذج التقييم الجمالي بنجاح.")
            return True
        except Exception as e:
            print(f"⚠️ [Aesthetics Model Warning] تعذر تحميل أوزان النموذج الجمالي: {e}. سيتم التراجع للتقييم الرياضي المساعد.")
            self.model = None
            return False

    def predict(self, pil_img, clip_model=None, preprocess=None):
        """
        حساب نقاط الجاذبية الجمالية للصورة من 1 إلى 10.
        """
        if self.model is None or clip_model is None or preprocess is None:
            # التراجع للتقييم الرياضي المساعد المعتمد على نقاء الألوان والتباين
            return self._heuristic_aesthetic_fallback(pil_img)
            
        import torch
        try:
            image_input = preprocess(pil_img).unsqueeze(0).to(self.device)
            with torch.no_grad():
                image_features = clip_model.encode_image(image_input)
                # تطبيع المتجهات لمطابقة فضاء CLIP القياسي
                image_features = image_features / image_features.norm(dim=-1, keepdim=True)
                score = self.model(image_features.float())
            return float(score.item())
        except Exception as e:
            print(f"⚠️ [Aesthetics Predictor Error] فشل التنبؤ الجمالي بـ AI: {e}")
            return self._heuristic_aesthetic_fallback(pil_img)

    def _heuristic_aesthetic_fallback(self, pil_img):
        """
        تقييم رياضي بديل لجمالية الصورة في حال عدم توفر نموذج الأوزان
        يعتمد على مستويات التشبع والتباين والتوزيع اللوني.
        """
        img_np = np.array(pil_img)
        hsv = cv2.cvtColor(img_np, cv2.COLOR_RGB2HSV)
        h, s, v = cv2.split(hsv)
        
        # الصور الأكثر جاذبية تحتوي على مستويات جيدة من التشبع والتباين والسطوع اللطيف
        mean_sat = np.mean(s)
        mean_val = np.mean(v)
        
        # حساب التباين الرياضي في القيمة (Luminance)
        contrast = np.std(v)
        
        # معادلة نقاط بديلة بين 1 و 10
        score = 5.0 + (mean_sat / 50.0) + (contrast / 30.0)
        # التأكد من التوسط والحدود المقبولة للإضاءة لمنع التوهج
        if mean_val > 235 or mean_val < 35:
            score -= 2.0
            
        return max(1.0, min(10.0, score))


class AdvancedClarityMetrics:
    """
    مقاييس الوضوح الكلاسيكية الأربعة لفرز الصور بدقة متناهية وسرعة فائقة.
    """

    @staticmethod
    def compute_laplacian_variance(gray):
        """
        مقياس تباين اللابلاسيان: ممتاز لقياس حواف التباين.
        """
        return float(cv2.Laplacian(gray, cv2.CV_64F).var())

    @staticmethod
    def compute_tenengrad_sobel(gray):
        """
        مقياس تينينجراد: مجموع مربع تباين حواف Sobel بالاتجاهين الأفقي والعمودي.
        """
        gx = cv2.Sobel(gray, cv2.CV_64F, 1, 0, ksize=3)
        gy = cv2.Sobel(gray, cv2.CV_64F, 0, 1, ksize=3)
        mag = gx**2 + gy**2
        return float(np.mean(mag))

    @staticmethod
    def compute_brenner_gradient(gray):
        """
        مقياس برينر: حساب الفروقات الأفقية للبكسلات بفاصل خطوتين (أسرع الخوارزميات).
        """
        gray_f = gray.astype(np.float64)
        diff = gray_f[:, 2:] - gray_f[:, :-2]
        return float((diff ** 2).mean())

    @staticmethod
    def compute_fft_ratio(gray, r_mask=15):
        """
        مقياس تحويل فورير السريع (FFT): قياس نسبة الترددات العالية لفلترة الضبابية.
        """
        h, w = gray.shape
        cy, cx = h // 2, w // 2
        
        # تحويل فوريير السريع وتوسيط الترددات
        f_transform = np.fft.fft2(gray)
        f_shift = np.fft.fftshift(f_transform)
        magnitude_spectrum = np.abs(f_shift)**2
        
        # إنشاء قناع للترددات المنخفضة بالوسط
        y, x = np.ogrid[-cy:h-cy, -cx:w-cx]
        mask_low = x**2 + y**2 <= r_mask**2
        
        total_energy = np.sum(magnitude_spectrum)
        if total_energy == 0:
            return 0.0
            
        low_energy = np.sum(magnitude_spectrum[mask_low])
        high_energy_ratio = 1.0 - (low_energy / total_energy)
        return float(high_energy_ratio)


class BackgroundComplianceChecker:
    """
    التحقق البرمجي التلقائي من خلفية ونسب تعبئة الصورة.
    """

    @staticmethod
    def analyze_compliance_and_fill(bgr_img, white_threshold=253):
        """
        حساب نسبة التعبئة ونسبة بياض أطراف الإطار الخارجي بالكامل.
        """
        h, w, _ = bgr_img.shape
        gray = cv2.cvtColor(bgr_img, cv2.COLOR_BGR2GRAY)
        
        # الكشف عن قناع المنتج (Foreground)
        _, thresh = cv2.threshold(gray, white_threshold, 255, cv2.THRESH_BINARY_INV)
        contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        
        if not contours:
            return 0.0, 0.0
            
        # إيجاد أكبر كائن وهو المنتج الأساسي
        c_max = max(contours, key=cv2.contourArea)
        bx, by, bw, bh = cv2.boundingRect(c_max)
        
        # نسبة تعبئة المنتج للإطار
        fill_ratio = (bw * bh) / (w * h)
        
        # استخراج عينات أطراف الإطار بالكامل
        top_border = bgr_img[0, :, :]
        bottom_border = bgr_img[-1, :, :]
        left_border = bgr_img[:, 0, :]
        right_border = bgr_img[:, -1, :]
        
        all_borders = np.concatenate([
            top_border.flatten(),
            bottom_border.flatten(),
            left_border.flatten(),
            right_border.flatten()
        ])
        
        # نسبة نقاء بياض الحدود
        pure_white_ratio = np.sum(all_borders >= white_threshold) / len(all_borders)
        
        return fill_ratio, pure_white_ratio


def calculate_composite_score(img_path, raw_aesthetic=None, weights=None):
    """
    حساب نقاط التقييم الجمالي والهندسي الموحد للصورة (Composite Quality Score).
    """
    if weights is None:
        # التوزيع القياسي لأوزان التقييم (مجموع الأوزان = 1.0)
        weights = {"aes": 0.40, "shp": 0.20, "res": 0.20, "comp": 0.20}
        
    img = cv2.imread(img_path)
    if img is None:
        return 0.0
        
    h, w, _ = img.shape
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    
    # 1. التقييم الجمالي (Aesthetic Score)
    if raw_aesthetic is None:
        # في حال عدم مرور القيمة مسبقاً، نستخدم التقييم الرياضي المساعد
        predictor = AestheticPredictor()
        with Image.open(img_path) as pil_img:
            raw_aesthetic = predictor.predict(pil_img)
            
    # تطبيع النتيجة الجمالية للنطاق [0, 1]
    s_aes = (raw_aesthetic - 1.0) / 9.0

    # 2. تقييم الوضوح والحدة المتقدم (Advanced Sharpness Score)
    brenner = AdvancedClarityMetrics.compute_brenner_gradient(gray)
    # التطبيع اللوغاريتمي لعتبات Brenner
    s_shp = min(1.0, max(0.0, (np.log(brenner + 1) - np.log(100)) / (np.log(10000) - np.log(100))))

    # 3. تقييم الدقة والأبعاد (Resolution Score)
    s_res = min(1.0, max(h, w) / 3840.0) # التطبيع مقارنة بمعيار دقة 4K

    # 4. تقييم الامتثال ومساحة التعبئة (Background Compliance & Fill Score)
    fill_ratio, pure_white = BackgroundComplianceChecker.analyze_compliance_and_fill(img)
    # حساب نسبة انحراف التعبئة عن العتبة المثالية (0.875)
    fill_score = max(0.0, 1.0 - abs(fill_ratio - 0.875) / 0.125)
    
    # دمج التعبئة ونقاء بياض الحدود بالتساوي
    c_bg = 0.5 * pure_white + 0.5 * fill_score

    # حساب النتيجة النهائية الموزعة
    composite = (
        weights["aes"] * s_aes +
        weights["shp"] * s_shp +
        weights["res"] * s_res +
        weights["comp"] * c_bg
    )
    return float(composite)
