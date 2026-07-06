# image_search.py
# موديول للبحث عن صور المنتجات واختيار أفضل جودة وصلة بالمنتج والبراند

import os
import re
import tempfile
import requests
import urllib.parse
import config
print = config.log_runner

# متغيرات جلوبال لتحميل نموذج CLIP مرة واحدة فقط وتوفير الذاكرة والوقت
_clip_model = None
_clip_processor = None

def get_clip_model():
    """
    تحميل نموذج CLIP محلياً وبشكل كسول (Lazy Loading) عند أول استدعاء فقط.
    """
    global _clip_model, _clip_processor
    if _clip_model is None:
        local_dir = "clip_model"
        # نتحقق أن ملف الأوزان pytorch_model.bin موجود ومكتمل
        if os.path.exists(os.path.join(local_dir, "pytorch_model.bin")):
            try:
                print("⏳ جاري تحميل نموذج الذكاء الاصطناعي CLIP محلياً للفحص البصري...")
                from transformers import CLIPProcessor, CLIPModel
                import torch
                _clip_model = CLIPModel.from_pretrained(local_dir).to('cpu')
                _clip_processor = CLIPProcessor.from_pretrained(local_dir)
                print("✅ تم تحميل نموذج CLIP بنجاح!")
            except Exception as e:
                print(f"⚠️ فشل تحميل نموذج CLIP محلياً: {e}")
        else:
            print("ℹ️ نموذج CLIP المحلي غير متوفر أو لم يكتمل تنزيله بعد. سيتم تخطي الفحص الذكي.")
    return _clip_model, _clip_processor

def download_temp_image(url):
    """
    تحميل الصورة مؤقتاً إلى ملف محلي لفحصها بـ CLIP.
    """
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        r = requests.get(url, headers=headers, timeout=5, stream=True)
        if r.status_code == 200:
            fd, temp_path = tempfile.mkstemp(suffix=".jpg")
            with os.fdopen(fd, 'wb') as f:
                for chunk in r.iter_content(chunk_size=128*1024):
                    f.write(chunk)
            return temp_path
    except Exception:
        pass
    return None

def is_image_real_product(image_path):
    """
    استخدام نموذج CLIP للتأكد من أن الصورة هي لمنتج تجاري حقيقي (علبة/زجاجة/كيس)
    وليست رسماً كرتونياً أو فكتورياً أو ملصقاً توضيحياً.
    """
    model, processor = get_clip_model()
    if model is None or processor is None:
        return True # كخيار احتياطي إذا لم يكن النموذج متوفراً
        
    try:
        from PIL import Image
        import torch
        
        image = Image.open(image_path).convert("RGB")
        
        # نصوص الوصف للمقارنة
        texts = [
            "a real photo of a physical commercial product packaging carton, bottle, can or bag",
            "a cartoon drawing, vector illustration, clipart graphics, cute character sticker"
        ]
        
        inputs = processor(text=texts, images=image, return_tensors="pt", padding=True)
        with torch.no_grad():
            outputs = model(**inputs)
            
        logits_per_image = outputs.logits_per_image
        probs = logits_per_image.softmax(dim=-1)
        
        real_prob = probs[0][0].item()
        cartoon_prob = probs[0][1].item()
        
        print(f"🤖 تحليل الذكاء الاصطناعي CLIP للصورة:")
        print(f"   - منتج حقيقي معبأ: {real_prob:.2%}")
        print(f"   - رسمة كرتونية/ملصق: {cartoon_prob:.2%}")
        
        # إذا كانت احتمالية الكرتون أكبر، نرفض الصورة
        if cartoon_prob > real_prob:
            return False
            
        return True
    except Exception as e:
        print(f"⚠️ خطأ أثناء فحص الصورة بـ CLIP: {e}")
        return True

def check_image_relevance_via_clip(image_path, brand, product_name):
    """
    مقارنة الصورة مباشرة مع اسم المنتج النصي للتأكد من التطابق الدلالي بدقة عالية باستخدام تجميع الاستعلامات (Prompt Ensembling).
    """
    model, processor = get_clip_model()
    if model is None or processor is None:
        return 1.0, None  # كخيار احتياطي إذا لم يكن النموذج متوفراً
        
    try:
        from PIL import Image
        import torch
        
        image = Image.open(image_path).convert("RGB")
        
        # تجميع استعلامات نصية متعددة (Prompt Ensembling)
        prompts = [
            f"a product packaging of {brand} {product_name}",
            f"a photo of {brand} {product_name} packaging box, bottle, or bag",
            f"packaged {brand} {product_name} commercial product item",
            f"a retail packaged product photo of {brand} {product_name}"
        ]
        
        inputs = processor(text=prompts, images=image, return_tensors="pt", padding=True)
        with torch.no_grad():
            outputs = model(**inputs)
            
        # استخراج وتطبيع متجهات الخصائص
        image_features = outputs.image_embeds
        text_features = outputs.text_embeds
        
        image_features = image_features / image_features.norm(dim=-1, keepdim=True)
        text_features = text_features / text_features.norm(dim=-1, keepdim=True)
        
        # حساب التشابه الجيبي لكل جملة وصفية ثم أخذ المتوسط
        similarities = (image_features @ text_features.T).squeeze(0)
        avg_similarity = similarities.mean().item()
        
        # تحويل المتجه لقائمة بايثون عادية (512 float)
        image_embedding = image_features.squeeze(0).tolist()
        
        print(f"🤖 [CLIP Ensemble Check] درجة التطابق المتوسطة مع عبوات '{brand} {product_name}': {avg_similarity:.4f}")
        return avg_similarity, image_embedding
    except Exception as e:
        print(f"⚠️ خطأ أثناء حساب تطابق النص المجمع بـ CLIP: {e}")
        return 1.0, None

def find_semantic_cache_match(product_name, brand, threshold=0.92):
    """
    مقارنة دلالية للمنتج الحالي مع أسماء المنتجات المخزنة في SQLite باستخدام المتجهات النصية لـ CLIP للتخطي الفوري للبحث.
    """
    try:
        import local_cache_db
        import torch
        import json
        import sqlite3
        
        # 1. جلب كافة المنتجات المسجلة بكود الكاش
        if not os.path.exists(local_cache_db.DB_PATH):
            return None
            
        conn = sqlite3.connect(local_cache_db.DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT product_name, brand, cloudinary_url, clip_score, metadata_json FROM resolved_products")
        rows = cursor.fetchall()
        conn.close()
        
        if not rows:
            return None
            
        # 2. حساب متجه الاستعلام النصي باستخدام CLIP
        model, processor = get_clip_model()
        if model is None or processor is None:
            return None
            
        query_prompt = f"a product packaging of {brand} {product_name}"
        cached_prompts = [f"a product packaging of {row['brand']} {row['product_name']}" for row in rows]
        
        # ترميز الاستعلام وكافة المنتجات المخزنة دفعة واحدة في مصفوفة مدمجة
        all_texts = [query_prompt] + cached_prompts
        inputs = processor(text=all_texts, return_tensors="pt", padding=True)
        with torch.no_grad():
            text_features = model.get_text_features(**inputs)
            
        # تطبيع المتجهات النصية رياضياً
        text_features = text_features / text_features.norm(dim=-1, keepdim=True)
        query_vector = text_features[0]
        cached_vectors = text_features[1:]
        
        # 3. حساب تشابه جيب التمام مع كافة المتجهات النصية للمنتجات
        best_match = None
        max_similarity = -1.0
        
        for idx, row in enumerate(rows):
            emb = cached_vectors[idx]
            similarity = torch.dot(query_vector, emb).item()
            
            if similarity > max_similarity:
                max_similarity = similarity
                best_match = row
                
        if max_similarity >= threshold and best_match:
            print(f"⚡ [CLIP Vector Cache Hit] تم العثور على تطابق دلالي ذكي بنسبة {max_similarity:.2%} مع منتج الكاش: '{best_match['brand']} {best_match['product_name']}'")
            metadata = {}
            if best_match["metadata_json"]:
                try:
                    metadata = json.loads(best_match["metadata_json"])
                except Exception:
                    pass
            return {
                "url": best_match["cloudinary_url"],
                "clip_score": best_match["clip_score"],
                "metadata": metadata,
                "semantic_similarity": max_similarity
            }
            
    except Exception as e:
        print(f"⚠️ [CLIP Vector Cache Error] فشل الاستعلام الدلالي عن الكاش: {e}")
        
    return None




def validate_image_via_gemini_vision(image_path, product_name, brand):
    """
    استخدام Gemini Vision للتحقق البصري السريع والمؤكد من تطابق الصورة مع البراند والمنتج المطلوبين.
    """
    if not config.GEMINI_API_KEY or not config.ENABLE_GEMINI_PRE_VALIDATION:
        return True
        
    try:
        from PIL import Image
        import base64
        import io
        
        with Image.open(image_path) as img:
            if img.mode != "RGB":
                img = img.convert("RGB")
            img.thumbnail((300, 300))  # تصغير الحجم جداً لتوفير الباندويث والتكلفة
            buffer = io.BytesIO()
            img.save(buffer, format="JPEG", quality=70)
            img_data = base64.b64encode(buffer.getvalue()).decode("utf-8")
            
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash:generateContent?key={config.GEMINI_API_KEY}"
        
        prompt = (
            f"You are a catalog validation assistant.\n"
            f"Analyze the image and verify if it shows a commercial packaged product from the brand '{brand}' representing '{product_name}'.\n"
            f"Perform strict attribute validation checks:\n"
            f"1. Brand check: Must match '{brand}' or its verified synonyms/subsidiaries. Reject if it is a competitor brand (e.g., Almarai instead of Meliha, Mai Dubai instead of Masafi, etc.).\n"
            f"2. Flavor/Type check: If the target '{product_name}' mentions a specific flavor or type (e.g., 'Chocolate', 'Strawberry', 'Full Cream', 'Low Fat'), the package in the image MUST match this flavor/type. If the image shows a mismatch (e.g., target is Chocolate, image is Strawberry), set 'valid' to false.\n"
            f"3. Size/Volume check: Check if the product size/volume matches the target name. If the target is a single small pack (e.g., '180ml') and the image shows a large 1L bottle or a bulk box, reject it. If size is not clearly readable or is close enough, you can accept it but explain in reason.\n"
            f"Reply strictly in JSON format matching this schema:\n"
            f'{{\n'
            f'  "valid": true or false,\n'
            f'  "reason": "brief explanation in English explaining any mismatch or confirmation"\n'
            f'}}'
        )
        
        payload = {
            "contents": [
                {
                    "parts": [
                        {"text": prompt},
                        {
                            "inlineData": {
                                "mimeType": "image/jpeg",
                                "data": img_data
                            }
                        }
                    ]
                }
            ],
            "generationConfig": {
                "responseMimeType": "application/json"
            }
        }
        
        headers = {"Content-Type": "application/json"}
        print(f"🤖 [Gemini Pre-Validation] جاري التحقق البصري الدقيق من المنتج...")
        
        # تحديث عداد المكالمات
        if hasattr(config, "METRICS") and "gemini_api_calls" in config.METRICS:
            config.METRICS["gemini_api_calls"] += 1
            
        response = requests.post(url, headers=headers, json=payload, timeout=15)
        if response.status_code == 200:
            import json
            res_data = response.json()
            text_response = res_data['candidates'][0]['content']['parts'][0]['text'].strip()
            if text_response.startswith("```json"):
                text_response = text_response[7:]
            elif text_response.startswith("```"):
                text_response = text_response[3:]
            if text_response.endswith("```"):
                text_response = text_response[:-3]
            text_response = text_response.strip()
            
            result = json.loads(text_response)
            is_valid = result.get("valid", False)
            reason = result.get("reason", "")
            print(f"🤖 [Gemini Pre-Validation] النتيجة: {'مقبول ✅' if is_valid else 'مرفوض ❌'} | السبب: {reason}")
            return is_valid
    except Exception as e:
        print(f"⚠️ خطأ أثناء التحقق المسبق بـ Gemini Vision: {e}")
        
    return True  # إذا فشل الاتصال بالـ API نقبله كاحتياط

def yandex_image_search(query):
    """
    البحث عن صور باستخدام محرك بحث Yandex (بديل مجاني قوي جداً وغير محدود).
    """
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8'
    }
    url = f"https://yandex.com/images/search?text={urllib.parse.quote(query)}"
    
    try:
        res = requests.get(url, headers=headers, timeout=10)
        if res.status_code != 200:
            print(f"⚠️ خطأ أثناء الاتصال بـ Yandex (كود الاستجابة {res.status_code})")
            return []
            
        if "captcha" in res.text.lower() or "showcaptcha" in res.text.lower():
            print("❌ واجه Yandex تحدي CAPTCHA للتأكد من الروبوتات.")
            return []
            
        import html
        import json
        
        decoded = html.unescape(res.text)
        
        # البحث عن روابط الصور الأصلية والبيانات المرافقة لها
        matches = re.finditer(r'"origUrl"\s*:\s*"([^"]+)"', decoded)
        results = []
        
        for match in matches:
            url_str = match.group(1)
            # استخراج كتلة النصوص المحيطة لاستخراج الدقة والعنوان
            start = max(0, match.start() - 300)
            end = min(len(decoded), match.end() + 300)
            chunk = decoded[start:end]
            
            w_match = re.search(r'"origWidth"\s*:\s*(\d+)', chunk)
            h_match = re.search(r'"origHeight"\s*:\s*(\d+)', chunk)
            t_match = re.search(r'"title"\s*:\s*"([^"]+)"', chunk)
            
            width = int(w_match.group(1)) if w_match else 800
            height = int(h_match.group(1)) if h_match else 800
            title = t_match.group(1) if t_match else query
            
            results.append({
                'url': url_str,
                'width': width,
                'height': height,
                'title': title
            })
            
        return results
    except Exception as e:
        print(f"⚠️ خطأ أثناء البحث في Yandex: {e}")
        return []

def bing_image_search(query):
    """
    البحث عن صور باستخدام محرك بحث Bing (بديل مجاني ممتاز لا يتطلب مفاتيح).
    """
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept-Language': 'en-US,en;q=0.9'
    }
    url = 'https://www.bing.com/images/search'
    params = {'q': query}
    
    try:
        res = requests.get(url, headers=headers, params=params, timeout=10)
        if res.status_code != 200:
            print(f"⚠️ خطأ أثناء الاتصال بـ Bing (كود الاستجابة {res.status_code})")
            return []
            
        import html
        import json
        
        decoded = html.unescape(res.text)
        pattern = r'class="iusc"[^>]+?m="({[^}]+?})".*?exph=(\d+).*?expw=(\d+)'
        matches = re.finditer(pattern, decoded, re.DOTALL)
        
        results = []
        for match in matches:
            json_str = match.group(1)
            exph = match.group(2)
            expw = match.group(3)
            
            try:
                data = json.loads(json_str)
                murl = data.get("murl")
                if murl:
                    murl_decoded = urllib.parse.unquote(murl)
                    title = data.get("t") or data.get("desc") or query
                    results.append({
                        'url': murl_decoded,
                        'width': int(expw),
                        'height': int(exph),
                        'title': title
                    })
            except Exception:
                pass
                
        return results
    except Exception as e:
        print(f"⚠️ خطأ أثناء البحث في Bing: {e}")
        return []

def google_image_search(query):
    """
    البحث عن صور باستخدام Google Custom Search API الرسمي.
    """
    if not config.GOOGLE_SEARCH_API_KEY or not config.GOOGLE_SEARCH_CX:
        return []
        
    url = "https://www.googleapis.com/customsearch/v1"
    params = {
        'key': config.GOOGLE_SEARCH_API_KEY,
        'cx': config.GOOGLE_SEARCH_CX,
        'q': query,
        'searchType': 'image',
        'num': 10
    }
    
    try:
        res = requests.get(url, params=params, timeout=5)
        if res.status_code != 200:
            return []
            
        data = res.json()
        results = []
        for item in data.get('items', []):
            img_info = item.get('image', {})
            results.append({
                'url': item.get('link'),
                'width': int(img_info.get('width', 0)),
                'height': int(img_info.get('height', 0)),
                'title': item.get('title', query)
            })
        return results
    except Exception:
        return []

def is_image_accessible(url):
    """
    التحقق من أن رابط الصورة يمكن الوصول إليه وتنزيله ولا يمنع الـ hotlinking.
    """
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        r = requests.head(url, headers=headers, timeout=3, allow_redirects=True)
        if r.status_code >= 400:
            r = requests.get(url, headers=headers, timeout=3, stream=True)
            
        if r.status_code == 200:
            content_type = r.headers.get('Content-Type', '')
            if 'image' in content_type.lower():
                return True
    except Exception:
        pass
    return False

def clean_product_name_for_search(name):
    """
    تنظيف اسم المنتج من الأحجام والوحدات والصيغ الخاصة لزيادة فرص العثور عليه
    """
    # إزالة الأوزان والأحجام الشائعة
    cleaned = re.sub(r'\b\d+(?:\.\d+)?\s*(?:ml|l|g|kg|pcs|pack|ltr|gm|oz|ozs|milliliter|liter|gram|kilogram|مل|لتر|جرام|جم|كجم|حبة)\b', '', name, flags=re.IGNORECASE)
    # إزالة الأقواس الفارغة أو علامات الترقيم الزائدة
    cleaned = re.sub(r'\s*[\(\[].*?[\)\]]', '', cleaned)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned

def get_fallback_query(product_name):
    """
    توليد استعلام بحث بديل يركز على شكل المنتج التجاري (مثل علبة، كرتونة، كيس)
    بدلاً من المكونات الخام لمنع جلب صور طبيعية غير ملائمة للمتجر.
    """
    cleaned_name = clean_product_name_for_search(product_name)
    name_lower = cleaned_name.lower()
    
    if "milk" in name_lower:
        if "carton" in name_lower or any(x in product_name.lower() for x in ["180ml", "200ml", "250ml"]):
            return f"{cleaned_name} carton"
        return f"{cleaned_name} bottle"
    elif "flour" in name_lower:
        return f"{cleaned_name} bag"
    elif "laban" in name_lower:
        return f"{cleaned_name} bottle"
    elif "kefir" in name_lower or "yogurt" in name_lower:
        return f"{cleaned_name} bottle"
        
    return f"{cleaned_name} product"

def evaluate_and_choose_best_image(results, product_name, brand, requires_brand_match=False, trace=None, step_name="البحث الأساسي", query=None, brand_mappings=None):
    """
    تقييم وترتيب نتائج البحث بناءً على صلتها بالبراند والمنتج ثم الدقة.
    إرجاع الصورة المختارة ونقاط الصلة الخاصة بها.
    """
    top_results = results[:15]  # استخدام أفضل 15 نتيجة صلة من محرك البحث
    scored_results = []
    brand_lower = brand.lower().strip()
    product_keywords = [w.lower().strip() for w in product_name.split() if len(w.strip()) > 1]
    
    # الحصول على المرادفات الخاصة بالبراند المعني لضمان مطابقة صارمة ذكية ودقيقة
    synonyms = [brand_lower]
    excluded_competitors = []
    if brand_mappings:
        # البحث عن البراند المعني في جدول المرادفات
        for k, mapping in brand_mappings.items():
            if k == brand_lower or mapping.get("brand", "").lower().strip() == brand_lower:
                synonyms.extend([s.lower().strip() for s in mapping.get("synonyms", [])])
                if config.FILTER_COMPETITORS:
                    excluded_competitors.extend([c.lower().strip() for c in mapping.get("excluded_competitors", [])])
                
    # إزالة التكرارات والفارغ
    synonyms = list(set([s for s in synonyms if s]))
    excluded_competitors = list(set([c for c in excluded_competitors if c]))
    
    candidates = []
    
    # التحقق من الكلمات الدلالية أو النطاقات الكرتونية والرسومات واستبعادها لضمان جلب صور منتجات حقيقية
    excluded_keywords = [
        "cartoon", "clipart", "vector", "illustration", "cute", "character", "drawing", 
        "sketch", "animated", "pngtree", "nicepng", "vecteezy", "freepik", "cleanpng", 
        "clipartmax", "favpng", "shutterstock/vectors", "vectorstock", "sticker", "kawaii", 
        "doodle", "wallpaper", "clip-art", "toy", "art", "paint", "graphics"
    ]
    excluded_domains = [
        "vecteezy.com", "freepik.com", "pngtree.com", "nicepng.com", "pngitem.com", 
        "clipartmax.com", "cleanpng.com", "favpng.com", "vectorstock.com", 
        "pinterest.com", "i.pinimg.com", "pinimg.com"
    ]

    for item in top_results:
        url_lower = item['url'].lower()
        title_lower = item.get('title', '').lower()
        
        # default candidate details
        relevance_score = 0
        has_brand_match = False
        reasons = []
        is_uae_source = any(x in url_lower or x in title_lower for x in [".ae", "uae", "dubai", "abu dhabi", "sharjah", "ajman", "fujairah", "um al quwain", "ras al khaimah", "الإمارات", "دبي"])
        
        # 1. check dimensions
        if item['width'] < config.MIN_IMAGE_WIDTH or item['height'] < config.MIN_IMAGE_HEIGHT:
            reasons.append(f"الصورة صغيرة جداً ({item['width']}x{item['height']}) - الحد الأدنى {config.MIN_IMAGE_WIDTH}x{config.MIN_IMAGE_HEIGHT}")
            candidates.append({
                "url": item['url'],
                "title": item.get('title', ''),
                "status": "rejected",
                "scores": {"relevance_score": 0, "is_uae_source": is_uae_source},
                "reasons": reasons
            })
            continue
            
        # 2. check cartoon
        is_cartoon_or_illustration = False
        for kw in excluded_keywords:
            if kw in url_lower or kw in title_lower:
                is_cartoon_or_illustration = True
                reasons.append(f"تطابق كلمة مستبعدة (كرتون/رسم): '{kw}'")
                break
        if not is_cartoon_or_illustration:
            for dom in excluded_domains:
                if dom in url_lower:
                    is_cartoon_or_illustration = True
                    reasons.append(f"تطابق نطاق مستبعد: '{dom}'")
                    break
                    
        if is_cartoon_or_illustration:
            candidates.append({
                "url": item['url'],
                "title": item.get('title', ''),
                "status": "rejected",
                "scores": {"relevance_score": 0, "is_uae_source": is_uae_source},
                "reasons": reasons
            })
            continue
            
        # 2.5. check competitor brand match
        has_competitor_match = False
        if excluded_competitors:
            for competitor in excluded_competitors:
                if competitor and (competitor in url_lower or competitor in title_lower):
                    has_competitor_match = True
                    reasons.append(f"تطابق مع منافس مستبعد: '{competitor}'")
                    break
        if has_competitor_match:
            candidates.append({
                "url": item['url'],
                "title": item.get('title', ''),
                "status": "rejected",
                "scores": {"relevance_score": 0, "is_uae_source": is_uae_source},
                "reasons": reasons
            })
            continue
            
        # 3. relevance score
        # A. brand check
        if synonyms:
            for synonym in synonyms:
                if synonym in url_lower:
                    relevance_score += 15
                    has_brand_match = True
                    break
                if synonym in title_lower:
                    relevance_score += 15
                    has_brand_match = True
                    break
                
        # B. product keywords check
        for keyword in product_keywords:
            if keyword in url_lower:
                relevance_score += 3
            if keyword in title_lower:
                relevance_score += 3
                
        # 4. brand match required check
        if requires_brand_match and brand_lower and not has_brand_match:
            reasons.append(f"عدم مطابقة البراند المطلوبة '{brand_lower}'")
            candidates.append({
                "url": item['url'],
                "title": item.get('title', ''),
                "status": "rejected",
                "scores": {"relevance_score": relevance_score, "is_uae_source": is_uae_source},
                "reasons": reasons
            })
            continue
            
        resolution = item['width'] * item['height']
        
        # Add placeholder to candidates
        candidates.append({
            "url": item['url'],
            "title": item.get('title', ''),
            "status": "rejected", # default to rejected
            "scores": {"relevance_score": relevance_score, "is_uae_source": is_uae_source},
            "reasons": reasons
        })
        
        scored_results.append({
            'item': item,
            'relevance_score': relevance_score,
            'resolution': resolution,
            'has_brand_match': has_brand_match,
            'candidate_index': len(candidates) - 1
        })
        
    if not scored_results:
        if trace is not None:
            trace.setdefault('steps', []).append({
                "name": step_name,
                "query": query or "",
                "results_count": len(results),
                "candidates": candidates
            })
        return None, 0
        
    # الترتيب: الصلة أولاً، ثم الدقة
    scored_results.sort(key=lambda x: (x['relevance_score'], x['resolution']), reverse=True)
    
    chosen_item = None
    chosen_relevance = 0
    
    # فحص الروابط بالترتيب واختيار أول رابط يمكن الوصول إليه ويجتاز الفحوصات المتقدمة
    for r in scored_results:
        item = r['item']
        c_idx = r['candidate_index']
        reasons = candidates[c_idx]['reasons']
        
        if is_image_accessible(item['url']):
            # تحميل مؤقت للصورة للتحقق منها بواسطة نماذج الفحص
            temp_img = download_temp_image(item['url'])
            if temp_img:
                is_real = is_image_real_product(temp_img)
                if not is_real:
                    reasons.append("مستبعدة: تم رفض الصورة بواسطة نموذج CLIP (تبدو كرتونية أو غير واقعية)")
                    try:
                        os.remove(temp_img)
                    except Exception:
                        pass
                    continue
                
                # الفحص الدلالي المطور بـ CLIP
                relevance_score_clip, clip_embedding = check_image_relevance_via_clip(temp_img, brand, product_name)
                is_relevant = relevance_score_clip >= config.CLIP_RELEVANCE_THRESHOLD
                is_grey_zone = False
                
                if not is_relevant:
                    if relevance_score_clip >= getattr(config, "CLIP_GREY_ZONE_THRESHOLD", 0.18):
                        is_grey_zone = True
                    else:
                        reasons.append(f"مستبعدة: درجة المطابقة الدلالية منخفضة ({relevance_score_clip:.4f} < {config.CLIP_RELEVANCE_THRESHOLD})")
                        try:
                            os.remove(temp_img)
                        except Exception:
                            pass
                        continue
                        
                # كشف التكرار البصري المحلي لمنع رفع نفس الصورة لمنتجين مختلفين
                try:
                    import local_cache_db
                    duplicate = local_cache_db.find_visual_duplicate(clip_embedding, threshold=0.96)
                    if duplicate and duplicate["product_name"].lower() != product_name.lower():
                        print(f"⚠️ [Visual Duplicate] الصورة مكررة بصرياً مع منتج آخر: '{duplicate['product_name']}'")
                        reasons.append(f"مستبعدة: كشف تكرار بصري متطابق مع منتج آخر ({duplicate['product_name']})")
                        try:
                            os.remove(temp_img)
                        except Exception:
                            pass
                        continue
                except Exception as e:
                    print(f"⚠️ خطأ أثناء فحص التكرار البصري في الكاش: {e}")
                
                # الفحص الذكي بـ Gemini Vision
                is_valid_gemini = validate_image_via_gemini_vision(temp_img, product_name, brand)
                if not is_valid_gemini:
                    reasons.append("مستبعدة: تم رفض المطابقة البصرية عبر Gemini Vision (براند/منتج خاطئ)")
                    try:
                        os.remove(temp_img)
                    except Exception:
                        pass
                    continue
                
                # مقبولة بنجاح (سواء بالقبول المباشر أو المراجعة لاحقاً)
                if is_grey_zone:
                    reasons.append(f"مراجعة: درجة المطابقة متوسطة في المنطقة الرمادية (CLIP Similarity: {relevance_score_clip:.4f})")
                    candidates[c_idx]['status'] = 'accepted'
                    chosen_item = item
                    chosen_item['needs_review'] = True
                    chosen_item['clip_score'] = relevance_score_clip
                    chosen_item['clip_embedding'] = clip_embedding
                    chosen_relevance = r['relevance_score']
                else:
                    reasons.append(f"مقبولة: تم التحقق من واقعية وجودة ومطابقة المنتج (CLIP Similarity: {relevance_score_clip:.4f})")
                    candidates[c_idx]['status'] = 'accepted'
                    chosen_item = item
                    chosen_item['clip_score'] = relevance_score_clip
                    chosen_item['clip_embedding'] = clip_embedding
                    chosen_relevance = r['relevance_score']
                    
                try:
                    os.remove(temp_img)
                except Exception:
                    pass
                break
            else:
                # إذا تعذر تحميلها مؤقتاً للفحص، نقبل الصورة كخيار افتراضي
                reasons.append("مقبولة: الرابط متاح (تخطي الفحوصات لتعذر تحميل الملف المؤقت)")
                candidates[c_idx]['status'] = 'accepted'
                chosen_item = item
                chosen_relevance = r['relevance_score']
                break
        else:
            reasons.append("مستبعدة: رابط الصورة غير صالح أو لا يمكن الوصول إليه")
            
    if chosen_item:
        if trace is not None:
            trace.setdefault('steps', []).append({
                "name": step_name,
                "query": query or "",
                "results_count": len(results),
                "candidates": candidates
            })
        return chosen_item, chosen_relevance
        
    # خيار أخير في حال لم تقبل أي صورة بسبب الفحص ولكنها اجتازت الفلترة الأولية
    r = scored_results[0]
    item = r['item']
    c_idx = r['candidate_index']
    candidates[c_idx]['reasons'].append("مقبولة: كخيار بديل أخير من نتائج التصفية")
    candidates[c_idx]['status'] = 'accepted'
    
    if trace is not None:
        trace.setdefault('steps', []).append({
            "name": step_name,
            "query": query or "",
            "results_count": len(results),
            "candidates": candidates
        })
    return item, r['relevance_score']

def expand_query_via_gemini(product_name, brand):
    """
    استخدام Gemini لإنشاء 3 جمل بحث محسنة ومختلفة لزيادة فرص العثور على صورة المنتج الصحيحة.
    """
    if not config.GEMINI_API_KEY:
        return [f"{brand} {product_name}".strip()]
        
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={config.GEMINI_API_KEY}"
        
        prompt = (
            f"You are a shopping search optimization assistant.\n"
            f"Generate 3 diverse search engine queries for finding product images of: Brand: '{brand}' and Product Name: '{product_name}'.\n"
            f"Requirements:\n"
            f"1. Query 1: standard brand and product name in English.\n"
            f"2. Query 2: optimized shopping search string in English with 'packaging' or 'product pack' appended.\n"
            f"3. Query 3: optimized search string in Arabic containing the translated brand and product name.\n"
            f"Return strictly a JSON array of strings containing exactly 3 queries, like:\n"
            f'["query 1", "query 2", "query 3"]'
        )
        
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json"}
        }
        headers = {"Content-Type": "application/json"}
        
        if hasattr(config, "METRICS") and "gemini_api_calls" in config.METRICS:
            config.METRICS["gemini_api_calls"] += 1
            
        res = requests.post(url, headers=headers, json=payload, timeout=8)
        if res.status_code == 200:
            import json
            res_data = res.json()
            text = res_data['candidates'][0]['content']['parts'][0]['text'].strip()
            if text.startswith("```json"):
                text = text[7:]
            elif text.startswith("```"):
                text = text[3:]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()
            queries = json.loads(text)
            if isinstance(queries, list) and len(queries) >= 1:
                print(f"🤖 [Gemini Query Expansion] الاستعلامات المولدة: {queries}")
                return [q.strip() for q in queries if q.strip()]
    except Exception as e:
        print(f"⚠️ خطأ أثناء توليد الاستعلامات عبر Gemini: {e}")
        
    return [
        f"{brand} {product_name}".strip(),
        f"{brand} {product_name} packaging".strip(),
        f"{brand} {product_name}".strip()
    ]

def search_best_product_image(query, product_name, brand, **kwargs):
    """
    البحث واختيار الصورة الأمثل للمنتج، مع تطبيق خطة بديلة للبحث العام
    في حال لم تكن هناك صور خاصة بالبراند (لأن البراندات المحلية مثل Meliha قد لا تملك صوراً على محركات البحث).
    """
    trace = kwargs.get('trace')
    strict_brand_match = kwargs.get('strict_brand_match')
    brand_mappings = kwargs.get('brand_mappings')
    barcode = kwargs.get('barcode', '')
    
    # 0. الاستعلام من قاعدة البيانات المحلية الكاش كخطوة أولى فائقة السرعة
    try:
        import local_cache_db
        cached = local_cache_db.get_cached_product(barcode=barcode, product_name=product_name, brand=brand)
        if cached:
            print(f"⚡ [SQLite Cache Hit] العثور على حل مخزن محلياً لـ '{product_name}'")
            return {
                "url": cached["cloudinary_url"],
                "title": "مسترجع من الكاش المحلي",
                "width": 800,
                "height": 800,
                "clip_score": cached["clip_score"],
                "metadata": cached["metadata"],
                "source": "sqlite_cache"
            }
    except Exception as e:
        print(f"⚠️ خطأ أثناء قراءة الكاش المحلي: {e}")
        
    # 0.أ. الاستعلام الدلالي الذكي عبر المتجهات (Vector Semantic Search) كخطوة ثانية فائقة السرعة
    try:
        semantic_cached = find_semantic_cache_match(product_name, brand, threshold=0.82)
        if semantic_cached:
            print(f"⚡ [CLIP Vector Cache Hit] العثور على حل دلالي مخزن لـ '{product_name}'")
            config.METRICS["semantic_cache_savings"] += 1
            return {
                "url": semantic_cached["url"],
                "title": "مسترجع دلالياً من الكاش المحلي",
                "width": 800,
                "height": 800,
                "clip_score": semantic_cached["clip_score"],
                "metadata": semantic_cached["metadata"],
                "source": "sqlite_cache",
                "semantic_similarity": semantic_cached.get("semantic_similarity")
            }
    except Exception as e:
        print(f"⚠️ خطأ أثناء البحث الدلالي في الكاش: {e}")
    
    # افتراضياً، نقوم بتفعيل المطابقة الصارمة إذا كان البراند مدخلاً لمنع خلط المنتجات مع المنافسين
    if strict_brand_match is None and brand:
        strict_brand_match = True
        
    # جلب مرادفات البراندات بالخلفية إذا لم تكن ممررة
    if brand and not brand_mappings:
        try:
            import google_sheets
            sheets_client = google_sheets.get_sheets_client()
            if sheets_client:
                brand_mappings = google_sheets.get_brand_mappings(sheets_client, config.SPREADSHEET_NAME_OR_URL)
        except Exception as e:
            print(f"⚠️ فشل جلب مرادفات البراندات في محرك البحث: {e}")
            
    # 0. البحث بالباركود كخيار أول فائق الدقة
    barcode = kwargs.get('barcode', '')
    if barcode and str(barcode).strip():
        barcode_clean = str(barcode).strip()
        print(f"🔍 جاري البحث المخصص باستخدام الباركود للمنتج: '{barcode_clean}'...")
        
        barcode_results = []
        if config.GOOGLE_SEARCH_API_KEY and config.GOOGLE_SEARCH_CX:
            barcode_results = google_image_search(barcode_clean)
        if not barcode_results:
            barcode_results = yandex_image_search(barcode_clean)
        if not barcode_results and config.USE_FALLBACK_SEARCH:
            barcode_results = bing_image_search(barcode_clean)
            
        if barcode_results:
            best_image, brand_score = evaluate_and_choose_best_image(
                barcode_results, product_name, brand, requires_brand_match=True, trace=trace, 
                step_name="البحث بالباركود والبراند", query=barcode_clean, brand_mappings=brand_mappings
            )
            if best_image:
                print(f"🎯 تم العثور على صورة المنتج المطابقة بنجاح عبر الباركود: {best_image['title']}")
                return best_image
        print(f"ℹ️ لم يتم العثور على صورة مطابقة عبر الباركود. الانتقال للبحث النصي الأساسي...")
        
        
    # 1. البحث باستخدام الاستعلامات الموسعة بالذكاء الاصطناعي
    queries = expand_query_via_gemini(product_name, brand)
    
    best_image = None
    brand_score = 0
    all_primary_results = []
    
    for q_idx, q in enumerate(queries, start=1):
        print(f"🔍 [Gemini Query {q_idx}/3] جاري البحث بالاستعلام المحسن: '{q}'...")
        q_results = []
        if config.GOOGLE_SEARCH_API_KEY and config.GOOGLE_SEARCH_CX:
            q_results = google_image_search(q)
        if not q_results:
            q_results = yandex_image_search(q)
        if not q_results and config.USE_FALLBACK_SEARCH:
            q_results = bing_image_search(q)
            
        if q_results:
            all_primary_results.extend(q_results)
            best_image, brand_score = evaluate_and_choose_best_image(
                q_results, product_name, brand, requires_brand_match=True, trace=trace, 
                step_name=f"البحث بالاستعلام المحسن {q_idx}", query=q, brand_mappings=brand_mappings
            )
            if best_image:
                print(f"🎯 تم العثور على صورة مقبولة باستخدام الاستعلام المحسن: '{q}'")
                break
        
    # 2. خطة البحث البديل (Generic Fallback Search)
    # إذا لم نجد صورة مطابقة للبراند (brand_score = 0)، فهذا يعني أن البراند غير مفهرس.
    # في حالة تفعيل المطابقة الصارمة للبراند، نرفض الانتقال للبحث العام لحظر صور البراندات المنافسة.
    if not best_image or brand_score == 0:
        if strict_brand_match and brand:
            print(f"ℹ️ تم تفعيل المطابقة الصارمة للبراند. تخطي البحث العام البديل للبراند '{brand}' لحظر صور المنافسين.")
            
            # تسجيل خطوة تخطي في التتبع للشفافية في الواجهة
            if trace is not None:
                trace.setdefault('steps', []).append({
                    "name": "تخطي البحث العام (مطابقة صارمة للبراند)",
                    "query": query or "",
                    "results_count": 0,
                    "candidates": []
                })
            return None
            
        fallback_query = get_fallback_query(product_name)
        print(f"ℹ️ لم يتم العثور على صور مفهرسة للبراند '{brand}'. تم تشغيل البحث العام للمنتج: '{fallback_query}'...")
        
        fallback_results = []
        # أ. البحث في Yandex للبحث العام
        fallback_results = yandex_image_search(fallback_query)
        # ب. البحث في Bing كبديل للبحث العام
        if not fallback_results and config.USE_FALLBACK_SEARCH:
            fallback_results = bing_image_search(fallback_query)
            
        if fallback_results:
            # تقييم الصور العامة (بدون اشتراط مطابقة البراند) واختيار الصورة الأعلى صلة بالمنتج
            best_image, _ = evaluate_and_choose_best_image(
                fallback_results, product_name, brand, requires_brand_match=False, trace=trace, 
                step_name="البحث العام البديل", query=fallback_query, brand_mappings=brand_mappings
            )
            if best_image:
                print(f"🎯 تم اختيار صورة للمنتج العام بدقة {best_image['width']}x{best_image['height']}: {best_image['title']}")
                return best_image
                
    if best_image:
        print(f"🎯 تم اختيار الصورة المطابقة للبراند بدقة {best_image['width']}x{best_image['height']}: {best_image['title']}")
        return best_image
        
    # إذا فشل كل شيء، نأخذ أول صورة من البحث الأساسي كخيار أخير جداً
    if all_primary_results:
        # إذا كانت المطابقة الصارمة مفعلة، لا يجب أن نأخذ أي صورة عشوائية لم تطابق البراند
        if strict_brand_match and brand:
            print("ℹ️ تم تفعيل المطابقة الصارمة للبراند. تخطي أخذ أي صورة لم تطابق البراند كخيار أخير.")
            return None
        print("⚠️ تحذير: لم نجد صورة براند مطابقة ولا صورة عامة مثالية. اختيار أول نتيجة بحث أساسي كخيار أخير.")
        return all_primary_results[0]
        
    print(f"❌ فشل العثور على أي صورة للمنتج '{product_name}'")
    return None
