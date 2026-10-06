import os
import sys
import unittest

# Make the pipeline modules at the repository root importable.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from PIL import Image, ImageEnhance

import image_dedup_bktree


def _random_image(seed: int) -> Image.Image:
    rng = np.random.default_rng(seed)
    # Smooth shapes rather than pure noise, so the low frequencies carry the image.
    base = rng.integers(0, 256, size=(8, 8, 3), dtype=np.uint8)
    return Image.fromarray(base).resize((256, 256), Image.Resampling.BICUBIC)


class TestPerceptualHash(unittest.TestCase):
    def test_brightness_term_is_not_a_bit(self):
        # Regression: the DC coefficient was excluded from the mean but still
        # emitted as the top bit, where it was 1 for nearly every image.
        for seed in range(20):
            self.assertLess(image_dedup_bktree.calculate_phash(_random_image(seed)), 2 ** 63)

    def test_an_edited_copy_is_close_and_a_different_image_is_far(self):
        original = _random_image(1)
        brighter = ImageEnhance.Brightness(original).enhance(1.15).resize((300, 300))
        other = _random_image(2)

        h_original = image_dedup_bktree.calculate_phash(original)
        h_brighter = image_dedup_bktree.calculate_phash(brighter)
        h_other = image_dedup_bktree.calculate_phash(other)

        distance = image_dedup_bktree.calculate_hamming_distance
        self.assertLessEqual(distance(h_original, h_brighter), 5)
        self.assertGreater(distance(h_original, h_other), 5)


if __name__ == "__main__":
    unittest.main()
