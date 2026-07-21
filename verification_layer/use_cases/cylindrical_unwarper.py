# verification_layer/use_cases/cylindrical_unwarper.py
import cv2
import numpy as np
from PIL import Image
from typing import Union


class CylindricalUnwarper:
    """
    خوارزمية الإسقاط العكسي الأسطواني (Cylindrical Back-Projection)
    لإلغاء التشوه الهندسي للملصقات والنصوص الملتفة حول عبوات أسطوانية ودائرية.
    """

    @staticmethod
    def cylindrical_unwarp_image(
        image_input: Union[np.ndarray, Image.Image],
        focal_length: float,
        radius: float
    ) -> np.ndarray:
        """
        يقوم بإلغاء التشوه الهندسي للملصقات الملتفة حول عبوات أسطوانية.
        """
        if isinstance(image_input, Image.Image):
            img_np = np.array(image_input.convert("RGB"))
            image = cv2.cvtColor(img_np, cv2.COLOR_RGB2BGR)
        else:
            image = image_input

        h, w = image.shape[:2]
        half_w = w / 2.0
        half_h = h / 2.0

        if radius**2 < half_w**2:
            # Adjust radius safely if smaller than half width
            radius = half_w * 1.05

        z0 = focal_length - np.sqrt(radius**2 - half_w**2)

        # توليد مصفوفات الإحداثيات للصورة المسطحة المستهدفة
        x_s, y_s = np.meshgrid(np.arange(w), np.arange(h))

        # تحويل الإحداثيات المستوية إلى زوايا وارتفاعات أسطوانية
        theta = (x_s - half_w) / float(focal_length)
        X_c = radius * np.sin(theta)
        Y_c = y_s - half_h
        Z_c = z0 + radius * np.cos(theta)

        # الإسقاط العكسي على مستشعر الكاميرا للحصول على إحداثيات الصورة المشوهة
        map_x = (focal_length * X_c / Z_c) + half_w
        map_y = (focal_length * Y_c / Z_c) + half_h

        map_x = map_x.astype(np.float32)
        map_y = map_y.astype(np.float32)

        invalid_mask = (map_x < 0) | (map_x >= w) | (map_y < 0) | (map_y >= h)
        map_x[invalid_mask] = -1
        map_y[invalid_mask] = -1

        unwarped_bgr = cv2.remap(
            image,
            map_x,
            map_y,
            interpolation=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=(0, 0, 0, 0),
        )
        return unwarped_bgr

    @classmethod
    def unwarp_pil(cls, pil_img: Image.Image, focal_length: float = 1000.0, radius: float = 600.0) -> Image.Image:
        unwarped_bgr = cls.cylindrical_unwarp_image(pil_img, focal_length, radius)
        rgb = cv2.cvtColor(unwarped_bgr, cv2.COLOR_BGR2RGB)
        return Image.fromarray(rgb)
