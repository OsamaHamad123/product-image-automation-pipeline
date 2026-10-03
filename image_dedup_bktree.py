# image_dedup_bktree.py
# كشف التكرارات البصرية عبر التشفير الإدراكي وشجرة BK (Perceptual Hashing & BK-Trees)
import os
import threading
from PIL import Image
import numpy as np
import scipy.fftpack


def calculate_hamming_distance(hash_a: int, hash_b: int) -> int:
    """حساب مسافة الهامينج بأعلى كفاءة عبر عمليات بيتية سريعة."""
    return bin(hash_a ^ hash_b).count('1')


# v2 (catalog_match.fetch.phash_hex) stores the hash as exactly 16 hex digits; the legacy v1
# path stored str(int), a decimal number. calculate_phash never sets the top bit (63 bits).
PHASH_HEX_DIGITS = 16
_PHASH_LIMIT = 2 ** 63


def parse_phash(value):
    """
    A stored pHash as an int, or None when it is missing or malformed.
    Accepts an int, a v2 hex string ('0a1b...', 16 digits, optional '0x') and a legacy decimal string.
    A 16-character string is read as hex (the current format) unless that value is too large to be a
    pHash; any other all-digit string is the legacy decimal form.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    text = str(value).strip().lower()
    if text.startswith("0x"):
        text, base = text[2:], 16
    elif any(c in "abcdef" for c in text):
        base = 16
    elif len(text) == PHASH_HEX_DIGITS and text.isdigit() and int(text, 16) < _PHASH_LIMIT:
        base = 16
    else:
        base = 10
    try:
        parsed = int(text, base)
    except ValueError:
        return None
    return parsed if parsed > 0 else None


def calculate_phash(image_input) -> int:
    """
    حساب الهاش الإدراكي (Perceptual Hash - pHash) للصور:
    1. تقليص الأبعاد لـ 32x32 رمادي.
    2. تطبيق تحويل جيب التمام المتقطع (2D DCT).
    3. أخذ المصفوفة الفرعية 8x8 للترددات المنخفضة.
    4. استبعاد معامل DC وحساب المتوسط وتوليد بصمة من 63 بت.

    The DC coefficient is the image's average brightness. It is left out of the
    bits as well as the mean: it is almost always far above the mean of the AC
    terms, so as a bit it was 1 for nearly every image and told images apart
    not at all.
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

        # أخذ العناصر 8x8 من الركن العلوي الأيسر، بدون معامل DC
        ac_terms = dct[0:8, 0:8].flatten()[1:]

        mean_val = np.mean(ac_terms)
        binary_string = "".join("1" if val > mean_val else "0" for val in ac_terms)
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

    def search(self, query_hash: int, max_distance: int = 5) -> list:
        """Alias for query_duplicates for backward compatibility."""
        return self.query_duplicates(query_hash=query_hash, tolerance_threshold=max_distance)

    def add(self, phash_value: int, image_id: str = "", metadata: dict = None):
        """Alias for insert_node for backward compatibility."""
        self.insert_node(phash_value=phash_value, image_id=image_id, metadata=metadata)

    def insert(self, phash_value: int, image_id: str = "", metadata: dict = None):
        """Alias for insert_node for backward compatibility."""
        self.insert_node(phash_value=phash_value, image_id=image_id, metadata=metadata)


def build_bktree_from_db():
    """
    Builds the tree from every resolved product that has a stored hash, so a new
    candidate is compared against the whole catalogue, not only against images
    seen since the process started. If the database cannot be read, the tree
    starts empty and fills as products are saved.
    """
    tree = PerceptualDeduplicationTree()
    try:
        import local_cache_db

        conn = local_cache_db.get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, product_name, cloudinary_url, perceptual_hash FROM resolved_products "
                "WHERE perceptual_hash IS NOT NULL AND perceptual_hash <> ''"
            )
            rows = cursor.fetchall()
        finally:
            conn.close()
    except Exception as e:
        print(f"⚠️ [BKTree] Could not load hashes from MariaDB, starting empty: {e}")
        return tree

    for row in rows:
        phash_value = parse_phash(row["perceptual_hash"])
        if phash_value is None:
            continue
        tree.insert_node(
            phash_value,
            str(row["id"]),
            {"cloudinary_url": row["cloudinary_url"], "product_name": row["product_name"]},
        )
    return tree


_shared_tree = None
_shared_tree_lock = threading.Lock()


def get_shared_tree() -> PerceptualDeduplicationTree:
    """The process-wide tree, built from MariaDB on first use."""
    global _shared_tree
    with _shared_tree_lock:
        if _shared_tree is None:
            _shared_tree = build_bktree_from_db()
        return _shared_tree


def remember_image(phash_value, image_id: str, cloudinary_url: str, product_name: str):
    """
    Adds a newly saved image to the shared tree, so the next product in the same
    run is checked against it. Does nothing until the tree has been built: the
    first build reads this row from the database anyway.
    """
    phash_int = parse_phash(phash_value)
    if phash_int is None:
        return
    with _shared_tree_lock:
        if _shared_tree is not None:
            _shared_tree.insert_node(
                phash_int, image_id, {"cloudinary_url": cloudinary_url, "product_name": product_name}
            )

