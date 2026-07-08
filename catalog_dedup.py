# catalog_dedup.py
# موديول منع التكرار البصري والتحقق المزدوج بالنصوص (DINOv2 Embeddings & OCR Gate)

import os
import sqlite3
import numpy as np
from PIL import Image

class DINOv2FeatureExtractor:
    """
    مستخرج بصمات الصور التابع لنموذج DINOv2 الذاتي التعلم والمطور من Meta.
    """

    def __init__(self, model_id="facebook/dinov2-base"):
        self.model_id = model_id
        self.processor = None
        self.model = None

    def _load_model(self):
        """
        تحميل النموذج عند أول حاجة (Lazy Loading) لتقليل زمن إقلاع السكربتات.
        """
        if self.model is not None:
            return
            
        import torch
        from transformers import AutoImageProcessor, AutoModel
        
        print(f"⏳ [DINOv2] جاري تحميل نموذج المقارنة البصرية '{self.model_id}'...")
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.processor = AutoImageProcessor.from_pretrained(self.model_id)
        self.model = AutoModel.from_pretrained(self.model_id).to(self.device)
        self.model.eval()
        print("✅ [DINOv2] تم تحميل نموذج البصمة البصرية بنجاح.")

    def extract_vector(self, pil_img):
        """
        استخراج متجه السمات الموحد (Embedding Vector) ذو الـ 768 بعداً والمطبع ليكون جاهزاً لحساب جيب التمام.
        """
        self._load_model()
        import torch
        
        try:
            inputs = self.processor(images=pil_img.convert("RGB"), return_tensors="pt").to(self.device)
            with torch.no_grad():
                outputs = self.model(**inputs)
            
            # استخراج المتجه الأساسي [CLS] المميز للصورة بالكامل
            cls_token = outputs.last_hidden_state[:, 0, :]
            vector = cls_token.cpu().numpy()[0]
            
            # تطبيع المتجه (L2 Normalization) ليكون طوله = 1 لتبسيط حساب Cosine Similarity بالضرب النقطي
            norm = np.linalg.norm(vector)
            if norm > 0:
                vector = vector / norm
            return vector.tolist()
        except Exception as e:
            print(f"⚠️ [DINOv2 Error] فشل استخراج بصمة الصورة: {e}")
            return None


class LocalVectorIndex:
    """
    قاعدة بيانات متجهية محلية مدمجة داخل SQLite لحفظ بصمات الصور DINOv2
    وحساب التشابه البصري (Cosine Similarity) بكفاءة عالية وبدون الاعتماد على خدمات خارجية.
    """

    def __init__(self, db_path="dinov2_index.db"):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS visual_catalog (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                barcode TEXT,
                product_name TEXT,
                brand TEXT,
                image_url TEXT,
                vector_blob BLOB,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
        conn.close()

    def save_vector(self, barcode, product_name, brand, image_url, vector):
        """
        حفظ متجه البصمة البصرية للمنتج
        """
        if not vector:
            return False
            
        vector_np = np.array(vector, dtype=np.float32)
        vector_blob = vector_np.tobytes()
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        # إزالة السجلات القديمة لنفس الباركود لمنع التكرار
        cursor.execute("DELETE FROM visual_catalog WHERE barcode = ?", (barcode,))
        cursor.execute("""
            INSERT INTO visual_catalog (barcode, product_name, brand, image_url, vector_blob)
            VALUES (?, ?, ?, ?, ?)
        """, (barcode, product_name, brand, image_url, vector_blob))
        conn.commit()
        conn.close()
        return True

    def find_nearest_neighbor(self, query_vector, similarity_threshold=0.96):
        """
        البحث عن أقرب تطابق بصري في قاعدة البيانات وحساب التشابه النقطي (Cosine Similarity)
        """
        if not query_vector:
            return None

        query_np = np.array(query_vector, dtype=np.float32)
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT barcode, product_name, brand, image_url, vector_blob FROM visual_catalog")
        rows = cursor.fetchall()
        conn.close()

        best_match = None
        highest_score = 0.0

        for barcode, product_name, brand, image_url, vector_blob in rows:
            candidate_np = np.frombuffer(vector_blob, dtype=np.float32)
            # المتجهات مطبعة مسبقاً، لذا الضرب النقطي يساوي تماماً Cosine Similarity
            similarity = float(np.dot(query_np, candidate_np))
            
            if similarity > highest_score:
                highest_score = similarity
                best_match = {
                    "barcode": barcode,
                    "product_name": product_name,
                    "brand": brand,
                    "image_url": image_url,
                    "similarity": similarity
                }

        if best_match and highest_score >= similarity_threshold:
            return best_match
        return None


class OCRVerificationGate:
    """
    بوابة التحقق الثنائي باستخدام التعرف الضوئي على الحروف (OCR) لمنع دمج البدائل
    المتشابهة بصرياً ولكن تختلف بالنكهات أو الأنواع (مثل Coke Zero sugar vs Classic Coke).
    """

    def __init__(self):
        self.reader = None

    def _load_reader(self):
        """
        تحميل مكتبة EasyOCR عند الحاجة فقط.
        """
        if self.reader is not None:
            return
            
        import easyocr
        print("⏳ [OCR Engine] جاري تشغيل قارئ النصوص EasyOCR...")
        # يدعم فحص النصوص بالإنجليزية والعربية
        self.reader = easyocr.Reader(['en', 'ar'], gpu=False)
        print("✅ [OCR Engine] قارئ النصوص نشط وجاهز للتحقق.")

    def extract_image_text(self, pil_img):
        """
        قراءة جميع النصوص المطبوعة على عبوة المنتج
        """
        self._load_reader()
        try:
            # تحويل الصورة لمصفوفة بايتات تناسب القارئ
            img_np = np.array(pil_img)
            results = self.reader.readtext(img_np)
            # تجميع النصوص
            words = [res[1].lower().strip() for res in results if res[1]]
            print(f"📖 [OCR Result] الكلمات المستخلصة من الصورة: {words}")
            return words
        except Exception as e:
            print(f"⚠️ [OCR Error] فشل قراءة النصوص من الصورة: {e}")
            return []

    def verify_variant_match(self, pil_img, target_product_name, target_brand):
        """
        بوابة التحقق المزدوجة:
        التأكد من أن الكلمات المستخرجة من الصورة لا تتعارض مع النكهات أو الماركة المطلوبة بالمنتج.
        """
        words = self.extract_image_text(pil_img)
        if not words:
            return True # تمرير افتراضي إذا لم يتم رصد أي كلمات لعدم إعاقة الأتمتة

        title_lower = target_product_name.lower().strip()
        brand_lower = target_brand.lower().strip()

        # 1. منع تضارب العلامات التجارية الصريح
        # إذا رصد الـ OCR ماركة شهيرة أخرى بالصورة غير ماركتنا المطلوبة، نرفض الصورة
        excluded_brand_keywords = ["cola", "pepsi", "nestle", "fanta", "sprite", "galaxy", "kinder", "zwan", "nellara", "alali"]
        for kw in excluded_brand_keywords:
            if kw in words and kw != brand_lower and kw not in title_lower:
                print(f"🚫 [OCR Gate Rejected] تم رصد براند منافس بالصورة '{kw}' يخالف البراند المستهدف '{brand_lower}'")
                return False

        # 2. منع خلط بدائل السكر (Regular vs Zero Sugar / Diet)
        is_zero_sugar_target = "zero" in title_lower or "diet" in title_lower or "no sugar" in title_lower
        is_zero_sugar_image = "zero" in words or "diet" in words or "no sugar" in words or "light" in words
        
        if is_zero_sugar_target != is_zero_sugar_image:
            print("🚫 [OCR Gate Rejected] تضارب واضح بين كوكا كولا كلاسيك وبديل السكر الدايت/الزيرو.")
            return False

        return True
