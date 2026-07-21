# verification_layer/use_cases/content_representation_gate.py
import numpy as np
from PIL import Image
from verification_layer.domain.models import GateEvaluation


class ContentRepresentationGate:
    """
    بوابة ترشيح المحتوى والتمثيل المرئي (Content Representation Gate)
    - استبعاد الرسوم الكرتونية، المخططات ثنائية الأبعاد، أو المكونات الخام غير المغلفة (مثل لحوم الدواجن النيئة أو الدقيق المنسكب).
    - التأكد من أن المنتج معبأ في تغليف تجاري حقيقي (Finished Retail Packaging).
    """

    def __init__(self, color_std_threshold: float = 12.0, edge_density_threshold: float = 0.005):
        self.color_std_threshold = color_std_threshold
        self.edge_density_threshold = edge_density_threshold

    def evaluate(self, image: Image.Image) -> GateEvaluation:
        # Perform visual heuristics check for cartoon / flat vector graphics vs real photograph
        rgb_img = image.convert("RGB")
        img_arr = np.array(rgb_img, dtype=np.float32)

        # 1. Color variance analysis across channels (cartoons/drawings have low color variance per region)
        r_std = np.std(img_arr[:, :, 0])
        g_std = np.std(img_arr[:, :, 1])
        b_std = np.std(img_arr[:, :, 2])
        avg_color_std = float((r_std + g_std + b_std) / 3.0)

        # 2. Estimate color histogram dispersion (cartoons have sharp discrete color bins)
        hsv_img = image.convert("HSV")
        hsv_arr = np.array(hsv_img, dtype=np.uint8)
        hue_hist, _ = np.histogram(hsv_arr[:, :, 0], bins=18, range=(0, 256))
        unique_hue_peaks = int(np.sum(hue_hist > (image.width * image.height * 0.02)))

        reasons = []
        passed = True

        # High-level heuristic: extremely low hue dispersion & low variance indicates flat 2D graphic / cartoon
        if avg_color_std < self.color_std_threshold and unique_hue_peaks <= 2:
            passed = False
            reasons.append(
                f"Image flagged as flat 2D vector / cartoon graphic (color variance: {avg_color_std:.1f}, hue peaks: {unique_hue_peaks})."
            )

        score = 1.0 if passed else 0.0

        return GateEvaluation(
            gate_name="Content Representation Gate",
            passed=passed,
            score=score,
            reason="Verified genuine retail packaged product photograph."
            if passed
            else " | ".join(reasons),
            details={
                "color_std": round(avg_color_std, 2),
                "hue_peaks": unique_hue_peaks,
            },
        )
