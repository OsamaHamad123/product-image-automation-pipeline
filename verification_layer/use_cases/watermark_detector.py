# verification_layer/use_cases/watermark_detector.py
"""
High-Frequency Background Watermark & Intellectual Property Anomaly Detector.
Applies 3x3 high-pass laplacian convolution filter on background pixels (>240).
"""

import numpy as np
from PIL import Image


class WatermarkDetector:
    """
    Lightweight watermark detector measuring high-frequency background noise.
    """
    @staticmethod
    def has_watermark(img_pil: Image.Image, frequency_threshold: float = 4.5) -> bool:
        gray = img_pil.convert("L")
        arr = np.array(gray, dtype=np.float32)
        
        # 3x3 high-pass laplacian filter
        kernel = np.array([[-1, -1, -1], [-1, 8, -1], [-1, -1, -1]], dtype=np.float32)
        h, w = arr.shape
        if h < 3 or w < 3:
            return False

        convolved = np.zeros((h - 2, w - 2), dtype=np.float32)
        for i in range(3):
            for j in range(3):
                convolved += arr[i:i + h - 2, j:j + w - 2] * kernel[i, j]

        # Isolate background pixels (> 240)
        background_mask = arr[1:h - 1, 1:w - 1] > 240
        if np.sum(background_mask) == 0:
            return False

        high_frequencies = np.abs(convolved[background_mask])
        mean_freq = float(np.mean(high_frequencies))
        return bool(mean_freq > frequency_threshold)
