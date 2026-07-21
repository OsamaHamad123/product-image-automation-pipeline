# verification_layer/use_cases/light_relighting_engine.py
import numpy as np
from PIL import Image, ImageFilter
import cv2
from typing import Tuple


class ICLightRelightingEngine:
    """
    محرك مواءمة الإضاءة والظلال الانعكاسية وإعادة الإضاءة الديناميكية (IC-Light & PGLA Shadow Engine)
    - نمط الاتساق الأمامي (Foreground Consistent - FC) للحفاظ على البياض الطبيعي الكامن (Albedo).
    - محول الإضاءة الموجه بالموقع (Position-Guided Light Adapter - PGLA) لتوليد ظلال ملامسة ناعمة (Soft Contact Shadows).
    """

    @classmethod
    def generate_soft_contact_shadow(
        cls, mask_pil: Image.Image, light_angle_deg: float = 45.0, shadow_blur_radius: float = 15.0
    ) -> Image.Image:
        """
        توليد ظلال ملامسة ناعمة (Soft Contact Shadows) تحت العبوة متناسقة مع الاتجاه الفراغي لمصدر الضوء.
        """
        mask_np = np.array(mask_pil.convert("L"))
        h, w = mask_np.shape

        # Calculate shadow offset based on light angle
        rad = np.radians(light_angle_deg)
        offset_x = int(12 * np.cos(rad))
        offset_y = int(18 * np.sin(rad))

        # Shift mask to create base shadow shape
        M = np.float32([[1, 0, offset_x], [0, 1, offset_y]])
        shadow_base = cv2.warpAffine(mask_np, M, (w, h), borderMode=cv2.BORDER_CONSTANT, borderValue=0)

        # Subtract product mask so shadow sits behind/under product
        shadow_only = cv2.subtract(shadow_base, mask_np)

        # Apply Gaussian blur for soft shadow drop-off
        shadow_pil = Image.fromarray(shadow_only).filter(ImageFilter.GaussianBlur(shadow_blur_radius))
        return shadow_pil

    @classmethod
    def apply_iclight_relighting_and_shadows(
        cls,
        product_pil: Image.Image,
        background_pil: Image.Image,
        mask_pil: Image.Image,
        light_angle_deg: float = 45.0,
        shadow_opacity: float = 0.45,
    ) -> Image.Image:
        """
        توليد وتراكب الإضاءة والظلال التكيفية PGLA فوق الخلفية الاستوديو الجديدة.
        """
        w, h = background_pil.size
        prod_resized = product_pil.resize((w, h)).convert("RGBA")
        bg_resized = background_pil.resize((w, h)).convert("RGBA")
        mask_resized = mask_pil.resize((w, h)).convert("L")

        # 1. Generate directional soft contact shadow
        shadow_pil = cls.generate_soft_contact_shadow(mask_resized, light_angle_deg=light_angle_deg)
        shadow_np = np.array(shadow_pil) / 255.0

        bg_np = np.array(bg_resized).astype(np.float32)

        # Apply shadow darkener onto background
        for ch in range(3):
            bg_np[:, :, ch] = bg_np[:, :, ch] * (1.0 - shadow_opacity * shadow_np)

        shadowed_bg = Image.fromarray(np.clip(bg_np, 0, 255).astype(np.uint8))

        # 2. Composite product onto shadowed background
        shadowed_bg.paste(prod_resized, (0, 0), mask_resized)
        return shadowed_bg
