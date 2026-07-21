# verification_layer/use_cases/white_background_gate.py
import numpy as np
from PIL import Image
from verification_layer.domain.models import GateEvaluation


class PureWhiteBackgroundGate:
    """
    بوابة التحقق من الخلفية البيضاء النقية (Pure White Background Gate)
    - فحص بكسلات الإطار الخارجي بعرض 10 بكسل للتأكد من مطابقتها للون الأبيض النقائي (RGB: 255, 255, 255) بنسبة >= 98%.
    - التأكد من أن المنتج يشغل مساحة لا تقل عن 85% من إجمالي إطار الصورة (fill ratio >= 0.85).
    """

    def __init__(
        self,
        border_width: int = 10,
        min_purity: float = 0.98,
        min_fill_ratio: float = 0.85,
        color_tolerance: int = 4,  # allow RGB values >= (255 - tolerance)
    ):
        self.border_width = border_width
        self.min_purity = min_purity
        self.min_fill_ratio = min_fill_ratio
        self.color_tolerance = color_tolerance

    def evaluate(self, image: Image.Image) -> GateEvaluation:
        # Convert image to RGB array
        rgb_img = image.convert("RGB")
        img_arr = np.array(rgb_img, dtype=np.uint8)
        h, w, _ = img_arr.shape

        if h <= 2 * self.border_width or w <= 2 * self.border_width:
            return GateEvaluation(
                gate_name="Pure White Background Gate",
                passed=False,
                score=0.0,
                reason=f"Image dimensions ({w}x{h}) are too small for border extraction.",
                details={"width": w, "height": h},
            )

        # 1. Extract 10px outer frame mask
        top_border = img_arr[: self.border_width, :, :]
        bottom_border = img_arr[-self.border_width :, :, :]
        left_border = img_arr[:, : self.border_width, :]
        right_border = img_arr[:, -self.border_width :, :]

        border_pixels = np.vstack(
            [
                top_border.reshape(-1, 3),
                bottom_border.reshape(-1, 3),
                left_border.reshape(-1, 3),
                right_border.reshape(-1, 3),
            ]
        )

        min_rgb_threshold = 255 - self.color_tolerance
        pure_white_mask = (
            (border_pixels[:, 0] >= min_rgb_threshold)
            & (border_pixels[:, 1] >= min_rgb_threshold)
            & (border_pixels[:, 2] >= min_rgb_threshold)
        )
        purity_score = float(np.mean(pure_white_mask))

        # 2. Foreground product bounding box fill ratio check
        # Non-white pixels represent foreground product
        non_white_mask = ~(
            (img_arr[:, :, 0] >= min_rgb_threshold)
            & (img_arr[:, :, 1] >= min_rgb_threshold)
            & (img_arr[:, :, 2] >= min_rgb_threshold)
        )
        y_indices, x_indices = np.where(non_white_mask)

        if len(y_indices) > 0:
            min_x, max_x = np.min(x_indices), np.max(x_indices)
            min_y, max_y = np.min(y_indices), np.max(y_indices)
            bbox_area = float((max_x - min_x + 1) * (max_y - min_y + 1))
            total_area = float(w * h)
            fill_ratio = bbox_area / total_area if total_area > 0 else 0.0
        else:
            fill_ratio = 0.0

        reasons = []
        passed = True

        if purity_score < self.min_purity:
            passed = False
            reasons.append(
                f"Outer border background purity ({purity_score*100:.1f}%) is below minimum required ({self.min_purity*100:.1f}%)."
            )

        if fill_ratio < self.min_fill_ratio:
            passed = False
            reasons.append(
                f"Product fill ratio ({fill_ratio*100:.1f}%) is below minimum required ({self.min_fill_ratio*100:.1f}%)."
            )

        gate_score = float((purity_score + fill_ratio) / 2.0) if passed else 0.0

        return GateEvaluation(
            gate_name="Pure White Background Gate",
            passed=passed,
            score=round(gate_score, 4),
            reason="Passed background purity and product fill ratio standards."
            if passed
            else " | ".join(reasons),
            details={
                "border_purity": round(purity_score, 4),
                "fill_ratio": round(fill_ratio, 4),
                "border_width_px": self.border_width,
            },
        )
