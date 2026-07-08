# taxonomy_classifier.py
# موديول مصنف التصنيفات الذكي بنظام GS1 المتجهي (SentenceTransformers & FAISS Product Classifier)

import os
import json
import numpy as np
import faiss

# تصنيفات هرمية افتراضية للمنتجات لتأسيس دليل المبيعات والمطابقة
DEFAULT_TAXONOMY = [
    "Fresh Food > Vegetables & Fruits",
    "Fresh Food > Dairy & Eggs",
    "Fresh Food > Meat & Poultry",
    "Pantry > Canned & Jarred Food",
    "Pantry > Rice, Pasta & Grains",
    "Pantry > Spices, Oils & Sauces",
    "Beverages > Soft Drinks & Juices",
    "Beverages > Tea & Coffee",
    "Beverages > Water",
    "Health & Beauty > Hair Care",
    "Health & Beauty > Skin Care & Soap",
    "Household > Cleaning Supplies",
    "Household > Kitchen & Paper Rolls",
    "Baby Care > Diapers & Wipes",
    "Baby Care > Baby Food & Formula"
]

class EnterpriseTaxonomyClassifier:
    """
    مصنف تصنيفات ذكي معتمد على متجهات التشابه الدلالي (Semantic Embedding matching)
    لمطابقة أسماء المنتجات مع شجرة تصنيف متجر التجزئة بدقة متناهية وسرعة فائقة.
    """

    def __init__(self, mode="vector", taxonomy_list=None):
        self.mode = mode
        self.taxonomy_labels = taxonomy_list or DEFAULT_TAXONOMY
        self.encoder = None
        self.faiss_index = None
        self.vector_dim = 384 # أبعاد نموذج all-MiniLM-L6-v2

    def _lazy_load_encoder(self):
        """
        تحميل نموذج الـ embedding والـ FAISS عند أول استدعاء.
        """
        if self.encoder is not None:
            return
            
        try:
            from sentence_transformers import SentenceTransformer
            print("⏳ [Taxonomy Encoder] جاري تحميل نموذج SentenceTransformer 'all-MiniLM-L6-v2'...")
            self.encoder = SentenceTransformer("all-MiniLM-L6-v2")
            print("✅ [Taxonomy Encoder] تم تحميل النموذج الدلالي بنجاح.")
            
            # بناء الفهرس المتجهي
            self._build_vector_index()
        except Exception as e:
            print(f"⚠️ [Taxonomy Classifier Warning] تعذر تحميل SentenceTransformers: {e}. سيتم التراجع للمطابقة المعجمية.")
            self.encoder = None

    def _build_vector_index(self):
        """
        توليد متجهات التصنيفات وبناء فهرس FAISS المتجهي السريع للضرب الداخلي (Inner Product).
        """
        if self.encoder is None:
            return
            
        try:
            print("⏳ [FAISS Index] جاري فهرسة التصنيفات في الفهرس المتجهي...")
            # توليد متجهات التصنيفات مع تطبيعها بالكامل لتساوي طولها = 1
            embeddings = self.encoder.encode(
                self.taxonomy_labels,
                convert_to_numpy=True,
                normalize_embeddings=True
            )
            
            # تهيئة الفهرس للبحث بالضرب النقطي (يعادل جيب التمام Cosine Similarity للمتجهات المطبعة)
            self.faiss_index = faiss.IndexFlatIP(self.vector_dim)
            self.faiss_index.add(embeddings.astype(np.float32))
            print(f"✅ [FAISS Index] تم فهرسة {len(self.taxonomy_labels)} تصنيفاً بنجاح.")
        except Exception as e:
            print(f"⚠️ [FAISS Index Error] فشل بناء الفهرس: {e}")
            self.faiss_index = None

    def classify_product_title(self, product_title):
        """
        تصنيف اسم المنتج وإرجاع الفئة الأكثر ملائمة مع درجة اليقين والثقة (Confidence Score).
        """
        self._lazy_load_encoder()
        
        # التراجع للمطابقة المعجمية البسيطة (Lexical Matching Heuristics)
        if self.encoder is None or self.faiss_index is None:
            return self._lexical_taxonomy_fallback(product_title)

        try:
            # استخراج وتطبيع متجه اسم المنتج المستهدف
            query_vector = self.encoder.encode(
                [product_title],
                convert_to_numpy=True,
                normalize_embeddings=True
            ).astype(np.float32)
            
            # البحث عن التطابق البصري الأقرب K-Nearest Neighbors (k=1)
            similarities, indices = self.faiss_index.search(query_vector, k=1)
            
            match_index = indices[0][0]
            confidence_score = float(similarities[0][0])
            
            category_path = self.taxonomy_labels[match_index]
            print(f"🎯 [Semantic Classifier] منتج '{product_title}' -> تصنيف: '{category_path}' (نسبة الثقة: {confidence_score:.2f})")
            return category_path, confidence_score
        except Exception as e:
            print(f"⚠️ [Semantic Classifier Error] فشل التصنيف المتجهي: {e}")
            return self._lexical_taxonomy_fallback(product_title)

    def _lexical_taxonomy_fallback(self, product_title):
        """
        تراجع معجمي بديل وسريع يعتمد على الكلمات المفتاحية لمطابقة الفئة.
        """
        title_lower = product_title.lower()
        
        # خوارزمية تطابق معجمي مبسطة
        if any(kw in title_lower for kw in ["milk", "egg", "eggs", "cheese", "yogurt", "butter"]):
            return "Fresh Food > Dairy & Eggs", 0.70
        elif any(kw in title_lower for kw in ["juice", "soda", "drink", "cola", "pepsi", "fanta", "water"]):
            return "Beverages > Soft Drinks & Juices", 0.70
        elif any(kw in title_lower for kw in ["shampoo", "hair", "soap", "cream", "lotion", "skin"]):
            return "Health & Beauty > Skin Care & Soap", 0.70
        elif any(kw in title_lower for kw in ["clean", "detergent", "powder", "spray", "dish", "liquid"]):
            return "Household > Cleaning Supplies", 0.70
            
        return "Fresh Food > Vegetables & Fruits", 0.40 # الفئة الافتراضية الاحتياطية
