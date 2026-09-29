import os
import sys
import unittest
import uuid

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

    def test_remember_image_waits_for_the_tree_to_be_built(self):
        saved = image_dedup_bktree._shared_tree
        try:
            image_dedup_bktree._shared_tree = None
            image_dedup_bktree.remember_image(12345, "1", "https://example.test/a.png", "A")
            self.assertIsNone(image_dedup_bktree._shared_tree)
        finally:
            image_dedup_bktree._shared_tree = saved


def _database_reachable() -> bool:
    try:
        import local_cache_db
        local_cache_db.get_db_connection().close()
        return True
    except Exception:
        return False


# CI always has MariaDB, so there a missing database is a failure, not a skip.
@unittest.skipUnless(os.getenv("CI") or _database_reachable(), "needs MariaDB (DB_* variables)")
class TestDuplicateIndexFromDatabase(unittest.TestCase):
    def setUp(self):
        import local_cache_db
        self.db = local_cache_db
        self.db.init_db()
        self.barcodes = []
        self.saved_tree = image_dedup_bktree._shared_tree
        image_dedup_bktree._shared_tree = None

    def tearDown(self):
        image_dedup_bktree._shared_tree = self.saved_tree
        conn = self.db.get_db_connection()
        try:
            cursor = conn.cursor()
            for barcode in self.barcodes:
                cursor.execute("DELETE FROM resolved_products WHERE barcode = %s", (barcode,))
            conn.commit()
        finally:
            conn.close()

    def _save(self, phash_value: int, url: str) -> None:
        barcode = f"test-{uuid.uuid4().hex[:12]}"
        self.barcodes.append(barcode)
        ok = self.db.save_product_resolution(
            barcode, f"Product {barcode}", "Brand", url, url, 1.0, {}, None, perceptual_hash=phash_value
        )
        self.assertTrue(ok)

    def test_the_tree_is_built_from_stored_hashes(self):
        # Regression: build_bktree_from_db() returned an empty tree, so the
        # duplicate check could never match anything.
        phash_value = image_dedup_bktree.calculate_phash(_random_image(10))
        self._save(phash_value, "https://example.test/stored.png")

        matches = image_dedup_bktree.get_shared_tree().query_duplicates(phash_value, tolerance_threshold=0)

        self.assertIn("https://example.test/stored.png", [m["metadata"]["cloudinary_url"] for m in matches])

    def test_an_image_saved_after_the_build_joins_the_tree(self):
        tree = image_dedup_bktree.get_shared_tree()
        phash_value = image_dedup_bktree.calculate_phash(_random_image(11))

        self._save(phash_value, "https://example.test/new.png")

        matches = tree.query_duplicates(phash_value, tolerance_threshold=0)
        self.assertIn("https://example.test/new.png", [m["metadata"]["cloudinary_url"] for m in matches])


if __name__ == "__main__":
    unittest.main()
