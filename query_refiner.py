# query_refiner.py
# موديول تنظيف الأسماء وتفكيك المنتجات وتوليد استعلامات البحث وتصحيح العلامات التجارية بدقة عالية

import os
import json
import re
import unicodedata
import requests
import config

class QueryRefiner:
    """
    محلل ومنظف أسماء المنتجات لفلترة الكلمات غير المفيدة، واستخلاص المواصفات،
    وتوليد استعلامات البحث المتراجعة بدقة عالية باستخدام Gemini والـ Regex.
    """

    def __init__(self, brand_vocabulary=None):
        # قائمة البراندات المعروفة لتسهيل خوارزمية Levenshtein وتصحيح الأخطاء الإملائية
        self.brand_vocabulary = brand_vocabulary or []

    @staticmethod
    def refine_product_metadata(product_name, brand, category=None):
        """
        تحليل أسماء المنتجات والبراندات المشوشة أو المختصرة باستخدام Gemini لاستخلاص
        أعمدة السمات الموحدة وتصحيح اختصارات البراند (مثل A/G -> American Garden).
        """
        clean_pname = product_name.strip()
        clean_brand = brand.strip() if brand else ""
        
        # القيم التراجعية الافتراضية في حال فشل الاتصال بالـ API أو عدم توفر المفتاح
        fallback = {
            "raw_brand": clean_brand,
            "canonical_brand_en": clean_brand,
            "canonical_brand_ar": "",
            "brand_synonyms": [clean_brand] if clean_brand else [],
            "product_class_en": clean_pname,
            "product_class_ar": "",
            "flavor_variant": "",
            "volume_weight": "",
            "quantity": 1,
            "cleaned_title_en": clean_pname,
            "cleaned_title_ar": "",
            "optimized_search_queries": [f"{clean_brand} {clean_pname}".strip()]
        }
        
        if not config.GEMINI_API_KEY:
            return fallback
            
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{config.GEMINI_MODEL}:generateContent?key={config.GEMINI_API_KEY}"
            
            prompt = (
                f"You are an expert e-Commerce Search & AI Architect. Analyze this messy product entry:\n"
                f"Raw Brand: '{clean_brand}'\n"
                f"Raw Product Name: '{clean_pname}'\n"
                f"Category context: '{category or ''}'\n\n"
                f"Requirements:\n"
                f"1. Resolve brand abbreviations/typos (e.g. 'A/G' or 'A.G.' to 'American Garden', 'M/P' to 'Masterpiece', 'Alali' to 'Al Alali').\n"
                f"2. Extract generic product class in English and Arabic.\n"
                f"3. Extract volume/weight (e.g., '800g', '1.5L') and quantity pack size (integer).\n"
                f"4. Clean title in English and Arabic, stripping abbreviations and packaging noise.\n"
                f"5. Generate a ranked list of 3 optimized search terms for Google/Bing Image search to retrieve product packaging images.\n\n"
                f"Return strictly a JSON object matching this schema:\n"
                f'{{\n'
                f'  "raw_brand": "exact input brand",\n'
                f'  "canonical_brand_en": "standard English brand name",\n'
                f'  "canonical_brand_ar": "transliterated Arabic brand name",\n'
                f'  "brand_synonyms": ["list", "of", "synonyms"],\n'
                f'  "product_class_en": "generic class in English",\n'
                f'  "product_class_ar": "generic class in Arabic",\n'
                f'  "flavor_variant": "flavor/scent/model",\n'
                f'  "volume_weight": "volume or weight with unit",\n'
                f'  "quantity": 1,\n'
                f'  "cleaned_title_en": "clean English title",\n'
                f'  "cleaned_title_ar": "clean Arabic title",\n'
                f'  "optimized_search_queries": ["query 1", "query 2", "query 3"]\n'
                f'}}'
            )
            
            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"responseMimeType": "application/json"}
            }
            headers = {"Content-Type": "application/json"}
            
            if hasattr(config, "METRICS") and "gemini_api_calls" in config.METRICS:
                config.METRICS["gemini_api_calls"] += 1
                
            res = requests.post(url, headers=headers, json=payload, timeout=10)
            if res.status_code == 200:
                res_data = res.json()
                text = res_data['candidates'][0]['content']['parts'][0]['text'].strip()
                if text.startswith("```json"):
                    text = text[7:]
                elif text.startswith("```"):
                    text = text[3:]
                if text.endswith("```"):
                    text = text[:-3]
                text = text.strip()
                
                data = json.loads(text)
                if isinstance(data, dict):
                    return data
        except Exception as e:
            print(f"⚠️ Error parsing product metadata via Gemini: {e}")
            
        return fallback

    def clean_title(self, raw_title):
        """
        تنظيف العنوان الأساسي بإزالة الرموز والباركودات والأوزان والعبوات المكررة.
        """
        if not raw_title:
            return ""
            
        # 1. تطبيع النصوص (Unicode Normalization) وإزالة الرموز الخاصة كالعلامات التجارية المسجلة
        normalized = unicodedata.normalize('NFKC', raw_title)
        normalized = normalized.replace("®", "").replace("™", "").replace("©", "")
        normalized = normalized.lower().strip()

        # 2. إزالة باركودات التوزيع العالمية (UPC / EAN / ISBN)
        normalized = re.sub(r'\b\d{12,13}\b', '', normalized)
        normalized = re.sub(r'\b(978|979)-\d{1,5}-\d{1,7}-\d{1,7}-\d{1}\b', '', normalized)

        # 3. استخلاص وإزالة الوزن والحجم (مثل 5.3 oz, 400g, 1.5L)
        normalized = re.sub(r'\b\d+(\.\d+)?\s*(g|oz|ml|l|kg|ounces|grams|fluid\s*oz|fl\.?\s*oz)\b', '', normalized)

        # 4. استخلاص وإزالة تعبيرات التعبئة والتغليف (مثل Pack of 12, Pk of 6, Box of 4, Qty 10, x12)
        normalized = re.sub(r'\b(pack\s*of|pk\s*of|box\s*of|case\s*of|qty|x)\s*(\d+)\b', '', normalized)
        normalized = re.sub(r'\b(\d+)\s*(count|ct|pcs|pack|pk)\b', '', normalized)

        # إزالة الرموز الخاصة غير المفيدة مع إبقاء الكلمات والأرقام بجميع اللغات والمسافات والشرطات
        normalized = re.sub(r'[^\w\s\-\.\,\/]', ' ', normalized)

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
