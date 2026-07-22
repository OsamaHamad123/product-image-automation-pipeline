# verification_layer/use_cases/hasler_susstrunk_colorfulness.py
"""
Hasler-Süsstrunk Colorfulness Metric Computation.
Computes color vibrancy in rg, yb opponent color space.
Accepted commercial range: [15.0, 95.0].
"""

import numpy as np
from PIL import Image
from typing import Dict, Any


def calculate_hasler_susstrunk_colorfulness(img_pil: Image.Image) -> float:
    """
    Computes Hasler-Süsstrunk colorfulness metric:
    C = sqrt(std_rg^2 + std_yb^2) + 0.3 * sqrt(mean_rg^2 + mean_yb^2)
    """
    rgb_img = img_pil.convert("RGB")
    arr = np.array(rgb_img, dtype=np.float32)
    R, G, B = arr[..., 0], arr[..., 1], arr[..., 2]

    rg = R - G
    yb = 0.5 * (R + G) - B

    std_rg = np.std(rg)
    std_yb = np.std(yb)
    mean_rg = np.mean(rg)
    mean_yb = np.mean(yb)

    std_root = np.sqrt(std_rg ** 2 + std_yb ** 2)
    mean_root = np.sqrt(mean_rg ** 2 + mean_yb ** 2)

    colorfulness = float(std_root + 0.3 * mean_root)
    return round(colorfulness, 2)


def evaluate_colorfulness_compliance(colorfulness: float) -> Dict[str, Any]:
    min_val, max_val = 15.0, 95.0
    is_compliant = (colorfulness >= min_val) and (colorfulness <= max_val)
    return {
        "colorfulness": colorfulness,
        "is_compliant": is_compliant,
        "range": [min_val, max_val],
        "status": "VIBRANT_COMMERCIAL_COLOR" if is_compliant else "SHADOW_DULL_OR_OVER_SATURATED"
    }
