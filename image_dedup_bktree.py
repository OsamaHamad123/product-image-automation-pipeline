# image_dedup_bktree.py
# كشف التكرارات البصرية عبر التشفير الإدراكي وشجرة BK (Perceptual Hashing & BK-Trees)
import os
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


# ---------------------------------------------------------------------------
# بصمة الألوان: pHash (32x32 رمادي) لا يرى اللون، فنفس العبوة بملصق أحمر وملصق أزرق مسافتها صفر
# ---------------------------------------------------------------------------

COLOR_SIGNATURE_VERSION = "h1"
COLOR_BINS = 12               # مدرج تدرجات اللون (hue): 30 درجة لكل خانة، بتوزيع ناعم بين الخانتين الأقرب
COLOR_SATURATION_MIN = 64     # بكسل «ملوّن»: تشبع وإضاءة كافيان (الأبيض والرمادي والأسود لا لون لها)
COLOR_VALUE_MIN = 48
COLOR_NEUTRAL = 0.03          # أقل من 3% من بكسلات المنتج ملوّنة: صورة بلا لون يُحتكم إليه
COLOR_HIST_FAR = 1.0          # مسافة L1 بين المدرجين (0..2) فوقها يختلف لون المنتجين بوضوح (~15 درجة لملصق بلون واحد)
_COLOR_LENGTH = len(COLOR_SIGNATURE_VERSION) + 2 + 2 * COLOR_BINS


def color_signature(image_input):
    """
    بصمة ألوان اللوحة النهائية (نص 'h1' + نسبة البكسلات الملوّنة + مدرج تدرجات اللون، hex)، أو None عند الخطأ.
    لا تعتمد على موضع المنتج في اللوحة ولا على إضاءته: مدرج تدرجات اللون للبكسلات الملوّنة فقط.
    """
    try:
        img = Image.open(image_input) if isinstance(image_input, str) else image_input
        hsv = np.asarray(img.convert("RGB").resize((64, 64), Image.Resampling.BOX).convert("HSV"), dtype=np.float64)
        h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
        product = ~((s < 16) & (v > 240))                    # كل ما ليس خلفية بيضاء
        colourful = (s >= COLOR_SATURATION_MIN) & (v >= COLOR_VALUE_MIN)
        frac = float(colourful.sum()) / max(float(product.sum()), 1.0)
        pos = h[colourful] / 256.0 * COLOR_BINS - 0.5        # مركز الخانة i عند i + 0.5
        low = np.floor(pos)
        upper_weight = pos - low
        low = low.astype(int) % COLOR_BINS
        hist = (np.bincount(low, weights=1.0 - upper_weight, minlength=COLOR_BINS)
                + np.bincount((low + 1) % COLOR_BINS, weights=upper_weight, minlength=COLOR_BINS))
        total = float(hist.sum())
        hist = hist / total if total > 0 else hist
        return (COLOR_SIGNATURE_VERSION + "%02x" % int(round(min(frac, 1.0) * 255))
                + "".join("%02x" % int(round(x * 255)) for x in hist))
    except Exception as e:
        print(f"تنبيه: تعذر حساب بصمة الألوان: {e}")
        return None


def _parse_color(value):
    text = str(value or "").strip().lower()
    if len(text) != _COLOR_LENGTH or not text.startswith(COLOR_SIGNATURE_VERSION):
        return None
    try:
        digits = text[len(COLOR_SIGNATURE_VERSION):]
        frac = int(digits[:2], 16) / 255.0
        hist = [int(digits[i:i + 2], 16) for i in range(2, len(digits), 2)]
    except ValueError:
        return None
    total = float(sum(hist))
    return frac, [x / total for x in hist] if total > 0 else [0.0] * COLOR_BINS


def colors_differ(a, b):
    """
    هل تختلف ألوان صورتين بوضوح (بصمتا color_signature)؟ True فقط عند يقين: بصمة غائبة أو غير مقروءة، أو صورتان
    بلا لون، أو فرق في المنطقة الرمادية = False (تبقيان «نفس الصورة» عند تطابق pHash: الأمان أولاً).
    """
    pa, pb = _parse_color(a), _parse_color(b)
    if pa is None or pb is None:
        return False
    (fa, ha), (fb, hb) = pa, pb
    if fa < COLOR_NEUTRAL and fb < COLOR_NEUTRAL:
        return False
    if fa < COLOR_NEUTRAL or fb < COLOR_NEUTRAL:
        # صورة بلا لون وأخرى ملوّنة بوضوح
        return max(fa, fb) >= 3 * COLOR_NEUTRAL
    return sum(abs(x - y) for x, y in zip(ha, hb)) > COLOR_HIST_FAR
