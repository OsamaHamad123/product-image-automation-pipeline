# image_quality_gatekeeper.py
# موديول التحقق الهندسي غير التوليدي والتقييم الرياضي لجودة صور المنتجات

import os
import cv2
import numpy as np
from PIL import Image

class ImageQualityGatekeeper:
    """
    بوابة التحقق الهندسي لقياس معايير جودة الصور وتصفيتها واحتساب نقاط التقييم الموحدة (Unified Score)
    لضمان اختيار الصورة الأصلية الأكثر دقة ووضوحاً بدون استخدام ترقيات الذكاء الاصطناعي التوليدية.
    """

    def __init__(self, target_resolution=1600, laplacian_threshold=100.0, min_width=500, min_height=500):
        self.target_resolution = target_resolution
        self.laplacian_threshold = laplacian_threshold
        self.min_width = min_width
        self.min_height = min_height

    def evaluate_image(self, pil_img, relevance_score_text=0.0):
        """
        تقييم الصورة وإرجاع تقرير تفصيلي بالمعايير الهندسية وحساب النتيجة الإجمالية الموحدة.
        """
        width, height = pil_img.size
        resolution = width * height
        aspect_ratio = width / height if height > 0 else 0.0

        # التحويل لـ OpenCV للعمليات الرياضية
        img_np = np.array(pil_img)
        img_cv = cv2.cvtColor(img_np, cv2.COLOR_RGB2BGR)
        gray = cv2.cvtColor(img_cv, cv2.COLOR_BGR2GRAY)

        # 1. اختبارات البوابة غير القابلة للتفاوض (Non-negotiable Gates)
        passes_gates = True
        gate_reasons = []

        # A. أبعاد الصورة
        if width < self.min_width or height < self.min_height:
            passes_gates = False
            gate_reasons.append(f"Dimensions below minimum: {width}x{height} (Min: {self.min_width}x{self.min_height})")

        # B. نسبة الارتفاع للعرض (Aspect Ratio Drift)
        if aspect_ratio < 0.4 or aspect_ratio > 2.5:
            passes_gates = False
            gate_reasons.append(f"Aspect ratio drift: {aspect_ratio:.2f} (Allowed: 0.4 - 2.5)")

        # 2. حساب المعايير الرياضية الفرعية
        # A. كشف التشويش والوضوح (Laplacian Variance Sharpness)
        laplacian_var = cv2.Laplacian(gray, cv2.CV_64F).var()
        is_blurry = laplacian_var < self.laplacian_threshold
        if is_blurry:
            passes_gates = False
            gate_reasons.append(f"Image too blurry: Laplacian variance {laplacian_var:.2f} (Threshold: {self.laplacian_threshold})")

        # B. كشف توازن الإضاءة والتباين (Exposure & Contrast)
        exposure_metrics = self._compute_exposure_and_contrast(gray)
        if exposure_metrics["is_underexposed"]:
            passes_gates = False
            gate_reasons.append("Image underexposed")
        if exposure_metrics["is_overexposed"]:
            passes_gates = False
            gate_reasons.append("Image overexposed")
        if exposure_metrics["is_low_contrast"]:
            passes_gates = False
            gate_reasons.append("Low contrast detected")

        # C. كشف عيوب الضغط وفقدان التفاصيل (Blockiness Index)
        blockiness_index = self._compute_blockiness(gray)
        is_over_compressed = blockiness_index >= 2.5
        if is_over_compressed:
            passes_gates = False
            gate_reasons.append(f"Image over-compressed: Blockiness index {blockiness_index:.2f} (Threshold: 2.5)")

        # D. التحقق من بياض ونقاء الخلفية (Pure White Background Verification)
        bg_metrics = self._evaluate_background_purity(img_np)
        
        # E. التحقق من مركزية التوجيه والمساحة المستغلة (Centering & Fill Ratio)
        layout_metrics = self._evaluate_centering_and_fill(gray)

        # 3. حساب النتيجة الموحدة (Unified Score Calculation)
        if not passes_gates:
            unified_score = 0.0
        else:
            # معايير وتوزيع أوزان التقييم الموحدة (Sum of weights = 1.0)
            w_res = 0.20
            w_sharp = 0.30
            w_bg = 0.20
            w_fill = 0.20
            w_relevance = 0.10
            w_art = 0.20 # عقوبة عيوب الضغط

            # نقاط الأبعاد (Resolution Score)
            s_res = min(1.0, max(width, height) / self.target_resolution)

            # نقاط الوضوح (Sharpness Score)
            s_sharp = min(1.0, laplacian_var / 250.0) # 250.0 كعتبة ممتازة للوضوح الكامل

            # نقاط الخلفية البيضاء (Background Compliance Score)
            s_bg = bg_metrics["bg_score"]

            # نقاط نسبة تعبئة الإطار (Product Fill Score)
            target_fill = 0.80
            s_fill = 1.0 - abs(layout_metrics["fill_ratio"] - target_fill)

            # نقاط الصلة النصية (Normalized Text Relevance)
            s_relevance = min(1.0, relevance_score_text / 40.0)

            # عقوبة عيوب الضغط (Compression Artifact Penalty)
            s_art = max(0.0, min(1.0, blockiness_index - 1.0))

            unified_score = (
                w_res * s_res +
                w_sharp * s_sharp +
                w_bg * s_bg +
                w_fill * s_fill +
                w_relevance * s_relevance -
                w_art * s_art
            )
            unified_score = max(0.0, min(1.0, unified_score))

        return {
            "passes_gates": passes_gates,
            "gate_reasons": gate_reasons,
            "width": width,
            "height": height,
            "aspect_ratio": aspect_ratio,
            "laplacian_var": laplacian_var,
            "blockiness_index": blockiness_index,
            "mean_luminance": exposure_metrics["mean_y"],
            "contrast_ratio": exposure_metrics["c_ratio"],
            "bg_score": bg_metrics["bg_score"],
            "perimeter_passed": bg_metrics["perimeter_passed"],
            "fill_ratio": layout_metrics["fill_ratio"],
            "centered_passed": layout_metrics["centered_passed"],
            "center_offset_ratio": layout_metrics["center_offset_ratio"],
            "unified_score": unified_score
        }

    def _compute_exposure_and_contrast(self, gray):
        """
        حساب توازن توزيع الإضاءة ومستويات التباين اللوني
        """
        mean_y = np.mean(gray)
        std_y = np.std(gray)
        
        # النسب المئوية
        p1 = np.percentile(gray, 1)
        p90 = np.percentile(gray, 90)
        p99 = np.percentile(gray, 99)
        
        active_range = p99 - p1
        c_ratio = active_range / 255.0
        
        # نسبة البكسلات البيضاء الصارخة
        clamped_255_ratio = np.sum(gray >= 254) / gray.size
        
        is_underexposed = (mean_y < 35) or (p90 < 64)
        is_overexposed = (mean_y > 235) or (clamped_255_ratio > 0.20)
        is_low_contrast = c_ratio < 0.15
        
        return {
            "mean_y": mean_y,
            "c_ratio": c_ratio,
            "is_underexposed": is_underexposed,
            "is_overexposed": is_overexposed,
            "is_low_contrast": is_low_contrast
        }

    def _compute_blockiness(self, gray):
        """
        كشف التشوهات الهيكلية الناتجة عن الضغط المفرط للصور (JPEG Blockiness Artifacts)
        بناءً على حساب فروقات بكسلات حواف الكتل 8x8.
        """
        h, w = gray.shape
        cols_blocks = w // 8
        rows_blocks = h // 8
        
        if cols_blocks <= 1 or rows_blocks <= 1:
            return 1.0

        # الفروقات الأفقية
        dh = np.abs(gray[:, :-1].astype(float) - gray[:, 1:].astype(float))
        
        # حواف الكتل الأفقية
        h_boundaries = [8 * k for k in range(1, cols_blocks)]
        boundary_diffs_h = []
        intra_diffs_h = []
        
        for col in h_boundaries:
            if col < w - 1:
                boundary_diffs_h.append(dh[:, col - 1])
                # بكسلات داخل الكتل
                for offset in range(1, 7):
                    if col + offset < w - 1:
                        intra_diffs_h.append(dh[:, col - 1 + offset])
                        
        if boundary_diffs_h and intra_diffs_h:
            z_boundary_h = np.mean(boundary_diffs_h)
            z_intra_h = np.mean(intra_diffs_h)
            bh = z_boundary_h / z_intra_h if z_intra_h > 0 else 1.0
        else:
            bh = 1.0

        # الفروقات العمودية
        dv = np.abs(gray[:-1, :].astype(float) - gray[1:, :].astype(float))
        
        # حواف الكتل العمودية
        v_boundaries = [8 * k for k in range(1, rows_blocks)]
        boundary_diffs_v = []
        intra_diffs_v = []
        
        for row in v_boundaries:
            if row < h - 1:
                boundary_diffs_v.append(dv[row - 1, :])
                for offset in range(1, 7):
                    if row + offset < h - 1:
                        intra_diffs_v.append(dv[row - 1 + offset, :])
                        
        if boundary_diffs_v and intra_diffs_v:
            z_boundary_v = np.mean(boundary_diffs_v)
            z_intra_v = np.mean(intra_diffs_v)
            bv = z_boundary_v / z_intra_v if z_intra_v > 0 else 1.0
        else:
            bv = 1.0

        return (bh + bv) / 2.0

    def _evaluate_background_purity(self, img_np, thickness=6):
        """
        التحقق من بياض ونقاء خلفية الصورة عن طريق فحص الإطار الخارجي (Perimeter Sampling)
        """
        h, w, c = img_np.shape
        
        # إنشاء قناع للإطار الخارجي فقط
        mask_border = np.zeros((h, w), dtype=bool)
        mask_border[0:thickness, :] = True
        mask_border[h-thickness:h, :] = True
        mask_border[:, 0:thickness] = True
        mask_border[:, w-thickness:w] = True
        
        border_pixels = img_np[mask_border]
        total_pixels = border_pixels.shape[0]
        
        # الفكسل يُعتبر أبيضاً إذا كانت كافة قنواته R,G,B >= 250
        white_border_pixels = np.sum(np.all(border_pixels >= 250, axis=-1))
        non_white_ratio = (total_pixels - white_border_pixels) / total_pixels
        
        # ناجح إذا كانت نسبة البكسلات غير البيضاء بالحدود أقل من 0.5%
        perimeter_passed = non_white_ratio <= 0.005
        bg_score = white_border_pixels / total_pixels
        
        return {
            "perimeter_passed": perimeter_passed,
            "bg_score": bg_score
        }

    def _evaluate_centering_and_fill(self, gray):
        """
        كشف مدى تعبئة المنتج للصورة (Fill Ratio) وتوسيطه (Centering Alignment)
        """
        h, w = gray.shape
        
        # Foreground هو البكسلات الأغمق من 248
        _, fg_mask = cv2.threshold(gray, 248, 255, cv2.THRESH_BINARY_INV)
        
        contours, _ = cv2.findContours(fg_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return {
                "centered_passed": False,
                "fill_ratio": 0.0,
                "center_offset_ratio": 1.0
            }
            
        # استخراج المربع المحيط الكلي المحتوي لكافة العناصر
        x_min, y_min = w, h
        x_max, y_max = 0, 0
        for cnt in contours:
            x, y, cw, ch = cv2.boundingRect(cnt)
            x_min = min(x_min, x)
            y_min = min(y_min, y)
            x_max = max(x_max, x + cw)
            y_max = max(y_max, y + ch)
            
        w_box = max(0, x_max - x_min)
        h_box = max(0, y_max - y_min)
        
        # نسبة تعبئة الإطار (Fill Ratio)
        fill_ratio = (w_box * h_box) / (w * h)
        
        # حساب مركز الثقل الجغرافي للمنتج (Centroid)
        moments = cv2.moments(fg_mask)
        if moments["m00"] > 0:
            cx = moments["m10"] / moments["m00"]
            cy = moments["m01"] / moments["m00"]
        else:
            cx, cy = w / 2, h / 2
            
        # إزاحة المركز عن المركز الهندسي للصورة كنسبة مئوية
        offset_x = abs(cx - w / 2) / w
        offset_y = abs(cy - h / 2) / h
        center_offset_ratio = max(offset_x, offset_y)
        
        # مقبول التوسيط إذا كانت الإزاحة في حدود 10%
        centered_passed = (offset_x <= 0.10) and (offset_y <= 0.10)
        
        return {
            "centered_passed": centered_passed,
            "fill_ratio": fill_ratio,
            "center_offset_ratio": center_offset_ratio
        }
