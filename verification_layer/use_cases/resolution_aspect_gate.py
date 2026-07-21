# verification_layer/use_cases/resolution_aspect_gate.py
from PIL import Image
from verification_layer.domain.models import GateEvaluation


class ResolutionAspectGate:
    """
    بوابة الأبعاد والدقة الهيكلية (Resolution & Aspect Ratio Gate)
    - البعد الأطول للصورة لا يقل عن 1000 بكسل (ويُفضل بين 1600 و 2000 بكسل).
    - نسبة العرض إلى الارتفاع (AR) تقع ضمن المدى الرياضي: 0.8 <= AR <= 1.25
    """

    def __init__(self, min_resolution: int = 1000, min_ar: float = 0.8, max_ar: float = 1.25):
        self.min_resolution = min_resolution
        self.min_ar = min_ar
        self.max_ar = max_ar

    def evaluate(self, image: Image.Image) -> GateEvaluation:
        width, height = image.size
        max_dim = max(width, height)
        aspect_ratio = width / height if height > 0 else 0.0

        reasons = []
        passed = True

        if max_dim < self.min_resolution:
            passed = False
            reasons.append(
                f"Longest dimension ({max_dim}px) is less than required minimum ({self.min_resolution}px)."
            )

        if not (self.min_ar <= aspect_ratio <= self.max_ar):
            passed = False
            reasons.append(
                f"Aspect ratio ({aspect_ratio:.2f}) outside allowed range ({self.min_ar} <= AR <= {self.max_ar})."
            )

        # Compute resolution score normalized to optimal 1600px
        score = min(1.0, max_dim / 1600.0) if passed else 0.0

        return GateEvaluation(
            gate_name="Resolution & Aspect Ratio Gate",
            passed=passed,
            score=round(score, 4),
            reason="Passed structural resolution and aspect ratio checks."
            if passed
            else " | ".join(reasons),
            details={
                "width": width,
                "height": height,
                "max_dimension": max_dim,
                "aspect_ratio": round(aspect_ratio, 4),
            },
        )
