# verification_layer/infrastructure/cv_gatekeeper_adapter.py
import cv2
import numpy as np
from PIL import Image
from typing import Tuple, Dict, Any


class CVGatekeeperAdapter:
    """
    محول عمليات معالجة الصور الرقمية وتجزئة الخلفية باستخدام OpenCV و PIL
    """

    @staticmethod
    def segment_foreground(pil_image: Image.Image) -> Tuple[np.ndarray, np.ndarray]:
        img_np = np.array(pil_image.convert("RGB"))
        bgr_image = cv2.cvtColor(img_np, cv2.COLOR_RGB2BGR)

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
