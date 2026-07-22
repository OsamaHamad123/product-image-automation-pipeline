# verification_layer/use_cases/cavi_aesthetic_engine.py
"""
Visual Conversion & Aesthetic Viability Index (CAVI) Engine.
Computes Laplacian Variance Focus Sharpness, Color Entropy, and Canvas Cleanliness.
Operates on pure NumPy and Pillow with zero heavy dependencies.
"""

import numpy as np
from typing import Optional
from verification_layer.domain.nextgen_models import CAVIScore


def compute_laplacian_variance(gray_img: np.ndarray) -> float:
    """
    Computes Variance of Laplacian using 3x3 digital kernel.
    [[0, 1, 0], [1, -4, 1], [0, 1, 0]]
    """
    h, w = gray_img.shape
    if h < 3 or w < 3:
        return 0.0

    gray_f = gray_img.astype(np.float32)
    laplacian = (
        gray_f[1:h-1, 2:w] +
        gray_f[1:h-1, 0:w-2] +
        gray_f[2:h, 1:w-1] +
        gray_f[0:h-2, 1:w-1] -
        4.0 * gray_f[1:h-1, 1:w-1]
    )
    return float(np.var(laplacian))


def calculate_color_entropy(rgb_img: np.ndarray, bins: int = 16) -> float:
    """Calculates 3D RGB color histogram entropy H(C)."""
    if rgb_img.ndim != 3 or rgb_img.shape[2] != 3:
        return 0.0

    hist, _ = np.histogramdd(rgb_img.reshape(-1, 3), bins=(bins, bins, bins))
    total = np.sum(hist)
    if total == 0:
        return 0.0
    probs = hist / total
    probs = probs[probs > 0]
    return float(-np.sum(probs * np.log2(probs)))


class CAVIAestheticEngineUseCase:
    def __init__(
        self,
        w1: float = 0.40,
        w2: float = 0.35,
        w3: float = 0.25,
        pass_threshold: float = 6.5
    ):
        self.w1 = w1
        self.w2 = w2
        self.w3 = w3
        self.pass_threshold = pass_threshold

    def evaluate_image_cavi(
        self,
        rgb_img: np.ndarray,
        alpha_mask: Optional[np.ndarray] = None
    ) -> CAVIScore:
        # Convert RGB to grayscale
        if rgb_img.ndim == 3 and rgb_img.shape[2] == 3:
            gray = np.dot(rgb_img[..., :3], [0.2989, 0.5870, 0.1140]).astype(np.uint8)
        else:
            gray = rgb_img

        focus_var = compute_laplacian_variance(gray)
        lap_score = float(np.log(max(focus_var, 1e-5)))

        entropy = calculate_color_entropy(rgb_img)
        # Normalize entropy against max theoretical log2(16^3) = 12.0
        entropy_score = entropy / 12.0

        if alpha_mask is not None:
            canvas_cleanliness = float(np.sum(alpha_mask == 0) / alpha_mask.size)
        else:
            # Measure pure white background pixels (>=250)
            canvas_cleanliness = float(np.sum(gray >= 250) / gray.size)

        raw_cavi = (self.w1 * lap_score) + (self.w2 * entropy_score) + (self.w3 * canvas_cleanliness)
        composite_cavi = float(np.clip((raw_cavi + 2.0) * 1.25, 0.0, 10.0))

        if composite_cavi >= 8.5:
            rank = "EXCELLENT_HIGH_CONVERSION"
        elif composite_cavi >= self.pass_threshold:
            rank = "ACCEPTABLE_GOOD_QUALITY"
        else:
            rank = "SUBPAR_LOW_AESTHETIC_REJECTED"

        return CAVIScore(
            focus_variance=round(focus_var, 2),
            color_entropy=round(entropy, 4),
            canvas_cleanliness=round(canvas_cleanliness, 4),
            composite_cavi=round(composite_cavi, 2),
            viability_rank=rank
        )
