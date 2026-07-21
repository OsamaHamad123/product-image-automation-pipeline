# image_dedup_bktree.py
# كشف التكرارات البصرية عبر التشفير الإدراكي وشجرة BK (Perceptual Hashing & BK-Trees)
import os
from PIL import Image
import numpy as np
import scipy.fftpack


def calculate_hamming_distance(hash_a: int, hash_b: int) -> int:
    """حساب مسافة الهامينج بأعلى كفاءة عبر عمليات بيتية سريعة."""
    return bin(hash_a ^ hash_b).count('1')


def calculate_phash(image_input) -> int:
    """
    حساب الهاش الإدراكي (Perceptual Hash - pHash) للصور:
    1. تقليص الأبعاد لـ 32x32 رمادي.
    2. تطبيق تحويل جيب التمام المتقطع (2D DCT).
    3. أخذ المصفوفة الفرعية 8x8 للترددات المنخفضة.
    4. استبعاد معامل DC وحساب المتوسط وتوليد بصمة 64 بت ثنائية.
    """
    try:
        if isinstance(image_input, str):
            img = Image.open(image_input)
        else:
            img = image_input

        img = img.convert('L').resize((32, 32), Image.Resampling.LANCZOS)
        img_array = np.array(img, dtype=np.float32)

        # 2D Discrete Cosine Transform (DCT)
        dct = scipy.fftpack.dct(scipy.fftpack.dct(img_array, axis=0, norm='ortho'), axis=1, norm='ortho')

        # أخذ العناصر 8x8 من الركن العلوي الأيسر
        dct_low = dct[0:8, 0:8]
        dct_low_no_dc = dct_low.copy()
        dct_low_no_dc[0, 0] = 0

        mean_val = np.mean(dct_low_no_dc)
        binary_string = "".join("1" if val > mean_val else "0" for val in dct_low.flatten())
        return int(binary_string, 2)
    except Exception as e:
        print(f"⚠️ [pHash Error] Failed to calculate pHash: {e}")
        return 0


class BKNode:
    def __init__(self, phash_value: int, image_id: str, metadata: dict = None):
        self.phash_value = phash_value
        self.image_id = image_id
        self.metadata = metadata or {}
        self.children = {}  # يربط مسافة الهامينج بالعقدة الابنة المقابلة


class PerceptualDeduplicationTree:
    """
    هيكل بيانات شجرة BK (Burkhard-Keller Tree) المترية للبحث اللوغاريتمي O(log N) في بصمات pHash.
    """

    def __init__(self):
        self.root = None

    def insert_node(self, phash_value: int, image_id: str, metadata: dict = None):
        if phash_value == 0:
            return
        if self.root is None:
            self.root = BKNode(phash_value, image_id, metadata)
            return

        current_node = self.root
        while True:
            distance = calculate_hamming_distance(current_node.phash_value, phash_value)
            if distance == 0:
                # تطابق إدراكي تام، الصورة مكررة بالفعل ولا يتم تكرار إدخالها
                if metadata:
                    current_node.metadata.update(metadata)
                return

            if distance in current_node.children:
                current_node = current_node.children[distance]
            else:
                current_node.children[distance] = BKNode(phash_value, image_id, metadata)
                break

    def query_duplicates(self, query_hash: int, tolerance_threshold: int = 5) -> list:
        if self.root is None or query_hash == 0:
            return []

        found_duplicates = []
        search_candidates = [self.root]

        while search_candidates:
            node = search_candidates.pop()
            distance = calculate_hamming_distance(node.phash_value, query_hash)

            if distance <= tolerance_threshold:
                found_duplicates.append({
                    "image_id": node.image_id,
                    "distance": distance,
                    "metadata": node.metadata
                })

            # تطبيق قاعدة التفاوت المثلثي لقص فروع شجرة البحث وتجنب فحص العقد غير المطابقة
            lower_bound = max(0, distance - tolerance_threshold)
            upper_bound = distance + tolerance_threshold

            for step in range(lower_bound, upper_bound + 1):
                if step in node.children:
                    search_candidates.append(node.children[step])

        return found_duplicates


# Wrapper helper for legacy imports
def build_bktree_from_db():
    tree = PerceptualDeduplicationTree()
    return tree
