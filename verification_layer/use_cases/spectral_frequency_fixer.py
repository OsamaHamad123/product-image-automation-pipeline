# verification_layer/use_cases/spectral_frequency_fixer.py
import numpy as np
from PIL import Image, ImageFilter
import cv2
from typing import Tuple


class SpectralFrequencyFixer:
    """
    محرك فصل الترددات الطيفية (Spectral Frequency Separation Engine)
    للحفاظ المطلق على هوية العبوات والملصقات والنصوص المطبوعة دون هلوسة بصريّة (%100 ثبات للنص للشعار)
    I_orig_HF = I_orig - G_sigma(I_orig)
    I_relit_LF = G_sigma(I_relit)
    I_final = I_relit_LF + I_orig_HF
    """

    DEFAULT_SIGMA_RADIUS = 3.0  # Gaussian blur radius for frequency splitting

    @classmethod
    def apply_low_pass_filter(cls, image_np: np.ndarray, sigma_radius: float = DEFAULT_SIGMA_RADIUS) -> np.ndarray:
        """
        تطبيق مرشح غاوس للتمرير منخفض التردد (Low-Pass Gaussian Filter G_sigma)
        لاستخلاص تدرجات الإضاءة والألوان فقط دون التفاصيل الدقيقة.
        """
        ksize = int(2 * round(3 * sigma_radius) + 1)
        if ksize % 2 == 0:
            ksize += 1
        blurred = cv2.GaussianBlur(image_np, (ksize, ksize), sigma_radius)
        return blurred

    @classmethod
    def extract_high_frequency_component(
        cls, original_np: np.ndarray, sigma_radius: float = DEFAULT_SIGMA_RADIUS
    ) -> np.ndarray:
        """
        I_orig_HF = I_orig - G_sigma(I_orig) + 128 (Neutral mid-gray offset)
        استخلاص تفاصيل التردد العالي (النصوص المطبوعة، الشعارات، والملمس الدقيق).
        """
        low_pass = cls.apply_low_pass_filter(original_np, sigma_radius)
        high_pass = cv2.subtract(original_np.astype(np.int16), low_pass.astype(np.int16)) + 128
        return np.clip(high_pass, 0, 255).astype(np.uint8)

    @classmethod
    def fuse_spectral_frequency_layers(
        cls,
        original_pil: Image.Image,
        relit_generated_pil: Image.Image,
        mask_pil: Image.Image,
        sigma_radius: float = DEFAULT_SIGMA_RADIUS,
    ) -> Image.Image:
        """
        إعادة تركيب الصورة النهائية للمنتج بنقل الطبقة عالية التردد الأصلية (I_orig_HF)
        ودمجها فوق مخرجات الإضاءة منخفضة التردد التوليدية (I_relit_LF).
        """
        orig_np = np.array(original_pil.convert("RGB"))
        relit_np = np.array(relit_generated_pil.resize(original_pil.size).convert("RGB"))
        mask_np = np.array(mask_pil.resize(original_pil.size).convert("L")) / 255.0

        # 1. Low-pass relit image (Color & Light only)
        relit_lf = cls.apply_low_pass_filter(relit_np, sigma_radius).astype(np.float32)

        # 2. High-pass original image (Printed text & texture only)
        orig_lf = cls.apply_low_pass_filter(orig_np, sigma_radius).astype(np.float32)
        orig_hf = orig_np.astype(np.float32) - orig_lf

        # 3. Spectral fusion: I_final = I_relit_LF + I_orig_HF
        fused_product_np = np.clip(relit_lf + orig_hf, 0, 255).astype(np.uint8)

        # Composite fused product onto generated background using product mask
        mask_3ch = np.stack([mask_np] * 3, axis=-1)
        final_composite_np = (fused_product_np * mask_3ch + relit_np * (1.0 - mask_3ch)).astype(np.uint8)

        return Image.fromarray(final_composite_np)
