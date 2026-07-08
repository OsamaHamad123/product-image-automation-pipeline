# query_refiner.py
# موديول تنظيف الأسماء وتفكيك المنتجات وتوليد استعلامات البحث المتراجعة

import re
import unicodedata

class QueryRefiner:
    """
    محلل ومنظف أسماء المنتجات لفلترة الكلمات غير المفيدة، واستخلاص المواصفات، وتوليد استعلامات البحث المتراجعة.
    """

    def __init__(self, brand_vocabulary=None):
        # قائمة البراندات المعروفة لتسهيل خوارزمية Levenshtein وتصحيح الأخطاء الإملائية
        self.brand_vocabulary = brand_vocabulary or []

    def clean_title(self, raw_title):
        """
        تنظيف العنوان الأساسي بإزالة الرموز والباركودات والأوزان والعبوات المكررة.
        """
        if not raw_title:
            return ""
            
        # 1. تطبيع النصوص (Unicode Normalization) وإزالة الرموز الخاصة كالعلامات التجارية المسجلة
        normalized = unicodedata.normalize('NFKD', raw_title).encode('ascii', 'ignore').decode('ascii')
        normalized = normalized.replace("®", "").replace("™", "").replace("©", "")
        normalized = normalized.lower().strip()

        # 2. إزالة باركودات التوزيع العالمية (UPC / EAN / ISBN)
        # EAN-13 (13 رقماً) & UPC-A (12 رقماً)
        normalized = re.sub(r'\b\d{12,13}\b', '', normalized)
        # ISBN-13
        normalized = re.sub(r'\b(978|979)-\d{1,5}-\d{1,7}-\d{1,7}-\d{1}\b', '', normalized)

        # 3. استخلاص وإزالة الوزن والحجم (مثل 5.3 oz, 400g, 1.5L)
        normalized = re.sub(r'\b\d+(\.\d+)?\s*(g|oz|ml|l|kg|ounces|grams|fluid\s*oz|fl\.?\s*oz)\b', '', normalized)

        # 4. استخلاص وإزالة تعبيرات التعبئة والتغليف (مثل Pack of 12, Pk of 6, Box of 4, Qty 10, x12)
        normalized = re.sub(r'\b(pack\s*of|pk\s*of|box\s*of|case\s*of|qty|x)\s*(\d+)\b', '', normalized)
        normalized = re.sub(r'\b(\d+)\s*(count|ct|pcs|pack|pk)\b', '', normalized)

        # تنظيف الفراغات الزائدة
        cleaned = re.sub(r'\s+', ' ', normalized).strip()
        return cleaned

    def compute_levenshtein_distance(self, s1, s2):
        """
        حساب مسافة ليفنشتاين (Levenshtein Distance) لنسبة التشابه اللفظي لتصحيح التيبو (Typo Correction).
        """
        if len(s1) < len(s2):
            return self.compute_levenshtein_distance(s2, s1)
        if len(s2) == 0:
            return len(s1)

        previous_row = range(len(s2) + 1)
        for i, c1 in enumerate(s1):
            current_row = [i + 1]
            for j, c2 in enumerate(s2):
                insertions = previous_row[j + 1] + 1
                deletions = current_row[j] + 1
                substitutions = previous_row[j] + (c1 != c2)
                current_row.append(min(insertions, deletions, substitutions))
            previous_row = current_row

        return previous_row[-1]

    def correct_brand_typo(self, brand_token):
        """
        تصحيح الأخطاء الإملائية للبراند إذا كان في حدود مسافة 15% من أحد أسماء البراندات الموثقة.
        """
        if not brand_token or not self.brand_vocabulary:
            return brand_token

        brand_lower = brand_token.lower().strip()
        best_match = brand_token
        min_dist = float('inf')

        for vocab in self.brand_vocabulary:
            vocab_lower = vocab.lower().strip()
            dist = self.compute_levenshtein_distance(brand_lower, vocab_lower)
            max_len = max(len(brand_lower), len(vocab_lower))
            
            if max_len > 0 and (dist / max_len) <= 0.15: # نسبة تيبو 15% كحد أقصى مقبول
                if dist < min_dist:
                    min_dist = dist
                    best_match = vocab

        return best_match

    def generate_fallback_sequence(self, brand, product_name, category=None, sub_category=None):
        """
        توليد تسلسل الاستعلامات المتراجع (Cascading Fallback Queries) لضمان جلب نتائج صور تحت أي ظرف.
        """
        cleaned_name = self.clean_title(product_name)
        cleaned_brand = self.correct_brand_typo(brand)
        
        # إزالة اسم البراند من اسم المنتج إذا كان مكرراً لتجنب الازدواجية بالاستعلام
        brand_lower = cleaned_brand.lower()
        if cleaned_name.startswith(brand_lower):
            cleaned_name = cleaned_name[len(brand_lower):].strip()

        # الاستعلامات المتدرجة
        queries = []
        
        # 1. الاستعلام المستهدف بالكامل (أعلى دقة)
        query_target = f"{cleaned_brand} {cleaned_name}".strip()
        queries.append(query_target)
        
        # 2. الاستعلام البديل (البراند والتاكسونومي)
        if category or sub_category:
            tax = sub_category or category
            query_fallback_brand = f"{cleaned_brand} {tax}".strip()
            queries.append(query_fallback_brand)
            
        # 3. الاستعلام العام (النوع والوصف)
        if category:
            query_generic = f"{category} {cleaned_name}".strip()
            queries.append(query_generic)
            
        # إزالة المكرر
        unique_queries = []
        for q in queries:
            if q and q not in unique_queries:
                unique_queries.append(q)
                
        return unique_queries
