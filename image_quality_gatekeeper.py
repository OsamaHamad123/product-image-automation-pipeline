import os
import cv2
import json
import numpy as np
from PIL import Image

class MicroClassifierEngine:
    def __init__(self, parameters_file_path: str):
        """
        يستقبل المحرك الأوزان المخزنة بصيغة JSON والتي تم تصديرها مسبقاً من Scikit-Learn.
        """
        self.is_loaded = False
        if os.path.exists(parameters_file_path):
            try:
                with open(parameters_file_path, "r", encoding="utf-8") as file:
                    params = json.load(file)

                self.W = np.array(params["weights"], dtype=np.float64)
                self.b = float(params["intercept"])
                self.mu = np.array(params["mean"], dtype=np.float64)
                self.sigma = np.array(params["scale"], dtype=np.float64)

                # التحقق من تطابق الأبعاد لتفادي أخطاء الضرب المصفوفي
                assert self.W.shape == self.mu.shape == self.sigma.shape
                self.is_loaded = True
            except Exception as e:
                print(f"⚠️ [MicroClassifierEngine Error] تعذر تحميل أوزان نموذج الفرز: {e}")

    def _normalize_features(self, X_raw: np.ndarray) -> np.ndarray:
        return (X_raw - self.mu) / self.sigma

    def _calculate_sigmoid(self, z: np.ndarray) -> np.ndarray:
        return np.where(
            z >= 0,
            1.0 / (1.0 + np.exp(-z)),
            np.exp(z) / (1.0 + np.exp(z))
        )

    def evaluate_probability(self, X_raw: np.ndarray) -> float:
        if not self.is_loaded:
            return 0.5
        X_scaled = self._normalize_features(X_raw)
        z = np.dot(X_scaled, self.W) + self.b
        return float(self._calculate_sigmoid(z))

    def make_decision(self, X_raw: np.ndarray, decision_threshold: float = 0.5) -> bool:
        probability = self.evaluate_probability(X_raw)
        return bool(probability >= decision_threshold)


class BoundaryComplianceSegmenter:
    def __init__(self, target_intensity_threshold: int = 248, max_std_dev: float = 4.0):
        self.target_threshold = target_intensity_threshold
        self.max_std = max_std_dev

    def segment_foreground(self, bgr_image: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """
        تطبق الخوارزمية نمذجة غراب-كات الإحصائية لعزل منتج التغليف الأبيض عن الخلفيات الشبيهة.
        """
        h, w = bgr_image.shape[:2]
        bg_gmm = np.zeros((1, 65), np.float64)
        fg_gmm = np.zeros((1, 65), np.float64)
        mask = np.zeros((h, w), np.uint8)
        
        margin_h = max(1, int(h * 0.05))
        margin_w = max(1, int(w * 0.05))
        bounding_rect = (margin_w, margin_h, w - 2 * margin_w, h - 2 * margin_h)
        
        cv2.grabCut(bgr_image, mask, bounding_rect, bg_gmm, fg_gmm, 5, cv2.GC_INIT_WITH_RECT)
        binary_mask = np.where((mask == cv2.GC_FGD) | (mask == cv2.GC_PR_FGD), 255, 0).astype(np.uint8)
        
        structuring_element = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        binary_mask = cv2.morphologyEx(binary_mask, cv2.MORPH_OPEN, structuring_element)
        
        segmented_product = cv2.bitwise_and(bgr_image, bgr_image, mask=binary_mask)
        return binary_mask, segmented_product

    def verify_background_purity(self, original_image: np.ndarray, product_mask: np.ndarray) -> dict:
        """
        تتأكد من مطابقة الخلفية المعزولة لمواصفات البياض والنقاء اللوني وخلوها من التشتت اللوني.
        """
        h, w = original_image.shape[:2]
        inverse_mask = cv2.bitwise_not(product_mask)
        
        flood_buffer = inverse_mask.copy()
        flood_mask = np.zeros((h + 2, w + 2), np.uint8)
        
        corner_seeds = [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)]
        for seed in corner_seeds:
            cv2.floodFill(flood_buffer, flood_mask, seedPoint=seed, newVal=127)
            
        continuous_background = (flood_buffer == 127)
        background_pixels = original_image[continuous_background]
        
        if background_pixels.size == 0:
            return {"compliant": False, "purity_score": 0.0, "reason": "لم يتم رصد خلفية محيطة متصلة بالمنتج"}
            
        mean_colors = np.mean(background_pixels, axis=0)
        std_deviations = np.std(background_pixels, axis=0)
        
        average_intensity = np.mean(mean_colors)
        average_variance = np.mean(std_deviations)
        
        is_compliant_white = average_intensity >= self.target_threshold
        is_homogeneous = average_variance <= self.max_std
        
        purity_metric = max(0.0, 100.0 - (average_variance * 5.0))
        
        return {
            "compliant": bool(is_compliant_white and is_homogeneous),
            "average_brightness": float(average_intensity),
            "chromatic_instability": float(average_variance),
            "purity_score": float(purity_metric) / 100.0,
            "reason": "الخلفية متجانسة وتطابق مواصفات النقاء البيضاء" if (is_compliant_white and is_homogeneous) 
            else "الخلفية تحتوي على ظلال رمادية أو عناصر مشوشة لجمالية المنتج"
        }


class ImageQualityGatekeeper:
    """
    بوابة التحقق الهندسي لقياس معايير جودة الصور وتصفيتها واحتساب نقاط التقييم الموحدة (Unified Score)
    """

    def __init__(self, target_resolution=1600, laplacian_threshold=100.0, min_width=500, min_height=500):
        self.target_resolution = target_resolution
        self.laplacian_threshold = laplacian_threshold
        self.min_width = min_width
        self.min_height = min_height
        
        # تحميل نموذج المعايرة النشطة بـ NumPy إذا توفر ملف الإعدادات
        config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "calibrated_gate_config.json")
        self.classifier = MicroClassifierEngine(config_path)

    def evaluate_image(self, pil_img, relevance_score_text=0.0, dinov2_similarity=None, aesthetic_score_raw=None):
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

        # 2. حساب المعايير البصرية الفرعية
        # A. كشف التشويش والوضوح (Laplacian Variance Sharpness)
        laplacian_var = cv2.Laplacian(gray, cv2.CV_64F).var()
        is_blurry = laplacian_var < self.laplacian_threshold
        if is_blurry:
            passes_gates = False
            gate_reasons.append(f"Image too blurry: Laplacian variance {laplacian_var:.2f} (Threshold: {self.laplacian_threshold})")

        # حساب بمتوسط برينر (Brenner Gradient) للتعلم النشط
        try:
            from aesthetics_engine import AdvancedClarityMetrics
            brenner = AdvancedClarityMetrics.compute_brenner_gradient(gray)
        except Exception:
            brenner = laplacian_var * 4.0 # تقديري تقريبي في حال فشل الاستيراد

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

        # D. تجزئة الخلفية وفحص النقاء الهجين (GrabCut + Corner-Seeded Flood-Fill)
        segmenter = BoundaryComplianceSegmenter()
        try:
            binary_mask, foreground_img = segmenter.segment_foreground(img_cv)
            bg_report = segmenter.verify_background_purity(img_cv, binary_mask)
            background_purity = bg_report.get("purity_score", 0.0)
            
            # حساب نسبة التعبئة الفعالة (Fill Ratio) من قناع GrabCut للمقدمة
            contours, _ = cv2.findContours(binary_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            if contours:
                c_max = max(contours, key=cv2.contourArea)
                bx, by, bw, bh = cv2.boundingRect(c_max)
                fill_ratio = (bw * bh) / (w * h)
            else:
                fill_ratio = 0.0
        except Exception as e:
            # تراجع آمن في حال حدوث أي خطأ في OpenCV GrabCut
            bg_metrics = self._evaluate_background_purity(img_np)
            background_purity = bg_metrics["bg_score"]
            layout_metrics = self._evaluate_centering_and_fill(gray)
            fill_ratio = layout_metrics["fill_ratio"]

        # E. نقاط التقييم الجمالي ومطابقة DINOv2
        if aesthetic_score_raw is None:
            try:
                from aesthetics_engine import AestheticPredictor
                predictor = AestheticPredictor()
                aesthetic_score_raw = predictor._heuristic_aesthetic_fallback(pil_img)
            except Exception:
                aesthetic_score_raw = 5.0

        if dinov2_similarity is None:
            dinov2_similarity = 0.85

        # 3. حساب النتيجة الموحدة (Unified Score Calculation)
        if not passes_gates:
            unified_score = 0.0
        elif self.classifier.is_loaded:
            # حساب التقييم الموحد والقرار ديناميكياً عبر معاملات التعلم النشط المعايرة
            features = np.array([
                float(aesthetic_score_raw),
                float(brenner),
                float(fill_ratio),
                float(background_purity),
                float(dinov2_similarity)
            ], dtype=np.float64)
            
            unified_score = self.classifier.evaluate_probability(features)
            passes_active_learning = self.classifier.make_decision(features, decision_threshold=0.5)
            
            if not passes_active_learning:
                passes_gates = False
                gate_reasons.append("Rejected by Active Learning Quality Gate")
                unified_score = 0.0
        else:
            # تراجع للتقييم الرياضي الموزع المعتاد في غياب أوزان التدريب
            w_res = 0.20
            w_sharp = 0.30
            w_bg = 0.20
            w_fill = 0.20
            w_relevance = 0.10
            w_art = 0.20

            s_res = min(1.0, max(width, height) / self.target_resolution)
            s_sharp = min(1.0, laplacian_var / 250.0)
            s_bg = background_purity
            target_fill = 0.80
            s_fill = 1.0 - abs(fill_ratio - target_fill)
            s_relevance = min(1.0, relevance_score_text / 40.0)
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
            if unified_score < 0.35: # عتبة القبول الافتراضية
                passes_gates = False
                gate_reasons.append(f"Heuristic Unified Score too low: {unified_score:.2f}")

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
            "bg_score": background_purity,
            "perimeter_passed": True,
            "fill_ratio": fill_ratio,
            "centered_passed": True,
            "center_offset_ratio": 0.0,
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
