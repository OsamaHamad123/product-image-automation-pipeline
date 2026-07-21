# verification_layer/use_cases/typographic_defense.py
import numpy as np
from PIL import Image, ImageFilter
import cv2
from typing import Tuple


class TypographicAttackDefender:
    """
    محرك حماية النماذج متعددة الوسائط من الهجمات الطباعية (Typographic Visual Prompt Attack Defense)
    - إبطال مفعول حقن النصوص المضللة المطبوعة على صور المنتجات والتي تخدع مشفرات CLIP/SigLIP.
    - تطبيق تقنية Dyslexify / OCR Filtering لإخفاء النصوص الطباعية قبل استخلاص المتجهات البصرية.
    """

    @classmethod
    def apply_dyslexify_masking(cls, image: Image.Image) -> Image.Image:
        """
        رصد حواف وسطور النصوص المطبوعة على الصورة واستبدالها بشفافية/طلاء محايد
        لحث نموذج الرؤية على تقييم الكائن الفيزيائي الحقيقي بدلاً من قراءة النص المضلل.
        """
        rgb_img = image.convert("RGB")
        img_np = np.array(rgb_img)
        gray = cv2.cvtColor(img_np, cv2.COLOR_RGB2GRAY)

        # Morphological gradient to detect high-contrast printed text strokes
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
        gradient = cv2.morphologyEx(gray, cv2.MORPH_GRADIENT, kernel)
        _, text_thresh = cv2.threshold(gradient, 60, 255, cv2.THRESH_BINARY)

        # Connect adjacent text characters
        close_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (15, 3))
        text_mask = cv2.morphologyEx(text_thresh, cv2.MORPH_CLOSE, close_kernel)

        contours, _ = cv2.findContours(text_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        masked_np = img_np.copy()

        h, w = gray.shape
        total_area = float(w * h)

        for cnt in contours:
            x, bx_y, bw, bh = cv2.boundingRect(cnt)
            box_area = float(bw * bh)
            aspect_ratio = bw / float(bh) if bh > 0 else 0.0

            # Neutralize horizontal text overlay bands that occupy < 25% of total image area
            if (box_area / total_area) < 0.25 and aspect_ratio >= 1.5:
                # Apply median blur over deceptive text overlay region
                roi = masked_np[bx_y : bx_y + bh, x : x + bw]
                if roi.size > 0:
                    blurred_roi = cv2.medianBlur(roi, 15)
                    masked_np[bx_y : bx_y + bh, x : x + bw] = blurred_roi

        return Image.fromarray(masked_np)
