# image_dedup_bktree.py
# موديول التجزئة الإدراكية (pHash) وشجرة Burkhard-Keller (BK-Tree) لكشف وفك تكرار الصور بصرياً بزمن قياسي

import os
import sqlite3
from PIL import Image
import numpy as np
import scipy.fftpack

# حساب مسافة هامنج بين بصمتين ثنائيتين
def hamming_distance(hash1: int, hash2: int) -> int:
    return bin(hash1 ^ hash2).count('1')

def calculate_phash(image_path_or_pil) -> int:
    """
    حساب الهاش الإدراكي (Perceptual Hash - pHash) للصور:
    1. تقليص الأبعاد لـ 32x32 رمادي.
    2. تطبيق تحويل جيب التمام المتقطع (2D DCT).
    3. أخذ المصفوفة الفرعية 8x8 للترددات المنخفضة.
    4. مقارنة معاملات المصفوفة بمتوسط القيم لتوليد بصمة 64 بت.
    """
    try:
        # فتح الصورة أو استخدام كائن PIL مباشرة
        if isinstance(image_path_or_pil, str):
            img = Image.open(image_path_or_pil)
        else:
            img = image_path_or_pil
            
        img = img.convert('L').resize((32, 32), Image.Resampling.LANCZOS)
        img_array = np.array(img, dtype=np.float32)
        
        # 2D Discrete Cosine Transform (DCT)
        dct = scipy.fftpack.dct(scipy.fftpack.dct(img_array, axis=0, norm='ortho'), axis=1, norm='ortho')
        
        # الاحتفاظ بالمصفوفة الطيفية 8x8 من الركن العلوي الأيسر
        dct_low = dct[0:8, 0:8]
        
        # استبعاد معامل DC (العنصر [0,0]) لتقليل الحساسية لمستويات الإضاءة العامة
        dct_low_no_dc = dct_low.copy()
        dct_low_no_dc[0, 0] = 0
        
        # حساب متوسط المعاملات
        mean = np.mean(dct_low_no_dc)
        
        # توليد السلسلة الثنائية 64 بت وتحويلها لعدد صحيح
        binary_string = "".join("1" if val > mean else "0" for val in dct_low.flatten())
        return int(binary_string, 2)
    except Exception as e:
        print(f"⚠️ [pHash Error] Failed to calculate hash for {image_path}: {e}")
        return 0

class BKTreeNode:
    def __init__(self, item: int, metadata: dict = None):
        self.item = item  # البصمة العددية للهاش الإدراكي
        self.metadata = metadata or {}  # بيانات وصفية إضافية (الاسم، البراند، رابط Cloudinary)
        self.children = {}  # قاموس يربط مسافة هامنج بعقدة فرعية

class BKTree:
    def __init__(self):
        self.root = None

    def insert(self, item: int, metadata: dict = None):
        if item == 0:
            return
        if self.root is None:
            self.root = BKTreeNode(item, metadata)
            return

        current = self.root
        while True:
            dist = hamming_distance(item, current.item)
            if dist == 0:
                # البصمة مكررة بالفعل، نقوم بتحديث البيانات الوصفية فقط
                current.metadata.update(metadata or {})
                return
            if dist in current.children:
                current = current.children[dist]
            else:
                current.children[dist] = BKTreeNode(item, metadata)
                break

    def search(self, item: int, max_distance: int = 5) -> list:
        """
        البحث التفرعي السريع عن مطابقات بصرية بمسافة هامنج تقل عن أو تساوي max_distance.
        يستخدم المتباينة المثلثية لتقليص مسار الاستعلام اللوغاريتمي O(log N).
        """
        if self.root is None or item == 0:
            return []

        results = []
        candidates = [self.root]
        
        while candidates:
            current = candidates.pop()
            dist = hamming_distance(item, current.item)
            
            if dist <= max_distance:
                results.append({
                    "hash": current.item,
                    "distance": dist,
                    "metadata": current.metadata
                })
                
            # فحص العقد الفرعية فقط التي تقع ضمن النطاق المسموح بالمتباينة المثلثية
            for child_dist, child_node in current.children.items():
                if abs(child_dist - dist) <= max_distance:
                    candidates.append(child_node)
                    
        return results

# دالة مساعدة لبناء الشجرة استناداً لبيانات الكاش المحلي في SQLite
def build_bktree_from_db(db_path: str) -> BKTree:
    tree = BKTree()
    if not os.path.exists(db_path):
        return tree
        
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # التحقق من وجود عمود البصمة الإدراكية في الجدول
        cursor.execute("PRAGMA table_info(resolved_products)")
        columns = [col[1] for col in cursor.fetchall()]
        if "perceptual_hash" in columns:
            cursor.execute("SELECT product_name, brand, cloudinary_url, perceptual_hash FROM resolved_products WHERE perceptual_hash IS NOT NULL AND perceptual_hash != ''")
            rows = cursor.fetchall()
            for row in rows:
                try:
                    hash_val = int(row["perceptual_hash"])
                    tree.insert(hash_val, {
                        "product_name": row["product_name"],
                        "brand": row["brand"],
                        "cloudinary_url": row["cloudinary_url"]
                    })
                except ValueError:
                    pass
        conn.close()
    except Exception as e:
        print(f"⚠️ [BKTree Builder Error] Failed to load from database: {e}")
        
    return tree
