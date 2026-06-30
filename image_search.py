# image_search.py
# موديول للبحث عن صور المنتجات واختيار أفضل جودة وصلة بالمنتج والبراند

import os
import re
import tempfile
import requests
import urllib.parse
import config

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
                _clip_model = CLIPModel.from_pretrained(local_dir)
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

def get_fallback_query(product_name):
    """
    توليد استعلام بحث بديل يركز على شكل المنتج التجاري (مثل علبة، كرتونة، كيس)
    بدلاً من المكونات الخام لمنع جلب صور طبيعية غير ملائمة للمتجر.
    """
    name_lower = product_name.lower()
    
    if "milk" in name_lower:
        if "180ml" in name_lower or "200ml" in name_lower or "250ml" in name_lower:
            return f"{product_name} carton"
        return f"{product_name} bottle"
    elif "flour" in name_lower:
        return f"{product_name} bag"
    elif "laban" in name_lower:
        return f"{product_name} bottle"
    elif "kefir" in name_lower or "yogurt" in name_lower:
        return f"{product_name} bottle"
        
    return f"{product_name} product"

def evaluate_and_choose_best_image(results, product_name, brand, requires_brand_match=False):
    """
    تقييم وترتيب نتائج البحث بناءً على صلتها بالبراند والمنتج ثم الدقة.
    إرجاع الصورة المختارة ونقاط الصلة الخاصة بها.
    """
    top_results = results[:15]  # استخدام أفضل 15 نتيجة صلة من محرك البحث
    scored_results = []
    brand_lower = brand.lower().strip()
    product_keywords = [w.lower().strip() for w in product_name.split() if len(w.strip()) > 1]
    
    for item in top_results:
        # التحقق من أن أبعاد الصورة تلائم الحد الأدنى المطلق للجودة لتجنب المصغرات والصور المشوشة
        if item['width'] < config.MIN_IMAGE_WIDTH or item['height'] < config.MIN_IMAGE_HEIGHT:
            continue
            
        url_lower = item['url'].lower()
        title_lower = item.get('title', '').lower()
        
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
        
        is_cartoon_or_illustration = False
        for kw in excluded_keywords:
            if kw in url_lower or kw in title_lower:
                is_cartoon_or_illustration = True
                break
        for dom in excluded_domains:
            if dom in url_lower:
                is_cartoon_or_illustration = True
                break
                
        if is_cartoon_or_illustration:
            continue
            
        has_brand_match = False
        relevance_score = 0
        
        # أ. التحقق من وجود اسم البراند في الرابط أو العنوان (ثقل مرتفع جداً)
        if brand_lower:
            if brand_lower in url_lower:
                relevance_score += 15
                has_brand_match = True
            if brand_lower in title_lower:
                relevance_score += 15
                has_brand_match = True
                
        # ب. التحقق من وجود كلمات المنتج في الرابط أو العنوان
        for keyword in product_keywords:
            if keyword in url_lower:
                relevance_score += 3
            if keyword in title_lower:
                relevance_score += 3
                
        # إذا اشترطنا مطابقة البراند ولم نجد مطابقة للبراند في هذه الصورة، نتخطاها
        if requires_brand_match and brand_lower and not has_brand_match:
            continue
            
        resolution = item['width'] * item['height']
        
        scored_results.append({
            'item': item,
            'relevance_score': relevance_score,
            'resolution': resolution,
            'has_brand_match': has_brand_match
        })
        
    if not scored_results:
        return None, 0
        
    # الترتيب: الصلة أولاً، ثم الدقة
    scored_results.sort(key=lambda x: (x['relevance_score'], x['resolution']), reverse=True)
    
    # فحص الروابط بالترتيب واختيار أول رابط يمكن الوصول إليه ويجتاز فحص CLIP الذكي
    for r in scored_results:
        item = r['item']
        if is_image_accessible(item['url']):
            # تحميل مؤقت للصورة للتحقق منها بواسطة نموذج CLIP
            temp_img = download_temp_image(item['url'])
            if temp_img:
                is_real = is_image_real_product(temp_img)
                try:
                    os.remove(temp_img)
                except Exception:
                    pass
                if is_real:
                    return item, r['relevance_score']
                else:
                    print(f"❌ تم استبعاد الصورة بواسطة CLIP لأنها تبدو كرتونية أو غير واقعية: '{item['title']}'")
            else:
                # إذا تعذر تحميلها مؤقتاً للفحص، نقبل الصورة كخيار افتراضي
                return item, r['relevance_score']
            
    # خيار أخير في حال لم تقبل أي صورة
    return scored_results[0]['item'], scored_results[0]['relevance_score']

def search_best_product_image(query, product_name, brand):
    """
    البحث واختيار الصورة الأمثل للمنتج، مع تطبيق خطة بديلة للبحث العام
    في حال لم تكن هناك صور خاصة بالبراند (لأن البراندات المحلية مثل Meliha قد لا تملك صوراً على محركات البحث).
    """
    results = []
    
    # 1. البحث الأساسي بالاسم والبراند معاً
    print(f"🔍 جاري البحث الأساسي للمنتج: '{query}'...")
    
    # أ. تجربة Google Custom Search أولاً
    if config.GOOGLE_SEARCH_API_KEY and config.GOOGLE_SEARCH_CX:
        results = google_image_search(query)
        
    # ب. تجربة Yandex كخيار بديل قوي
    if not results:
        results = yandex_image_search(query)
        
    # ج. تجربة Bing كخيار ثالث
    if not results and config.USE_FALLBACK_SEARCH:
        results = bing_image_search(query)
        
    # التحقق من ملاءمة نتائج البحث الأساسي
    best_image = None
    brand_score = 0
    
    if results:
        # نقوم بالتقييم ونشترط مطابقة البراند (لنتأكد من أننا حصلنا على صورة البراند الحقيقية)
        best_image, brand_score = evaluate_and_choose_best_image(results, product_name, brand, requires_brand_match=True)
        
    # 2. خطة البحث البديل (Generic Fallback Search)
    # إذا لم نجد صورة مطابقة للبراند (brand_score = 0)، فهذا يعني أن البراند غير مفهرس.
    # سنقوم بالبحث عن شكل المنتج العام (مثل علبة حليب أو كيس طحين) لتجنب الصور العامة التعبيرية.
    if not best_image or brand_score == 0:
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
            best_image, _ = evaluate_and_choose_best_image(fallback_results, product_name, brand, requires_brand_match=False)
            if best_image:
                print(f"🎯 تم اختيار صورة للمنتج العام بدقة {best_image['width']}x{best_image['height']}: {best_image['title']}")
                return best_image
                
    if best_image:
        print(f"🎯 تم اختيار الصورة المطابقة للبراند بدقة {best_image['width']}x{best_image['height']}: {best_image['title']}")
        return best_image
        
    # إذا فشل كل شيء، نأخذ أول صورة من البحث الأساسي كخيار أخير جداً
    if results:
        print("⚠️ تحذير: لم نجد صورة براند مطابقة ولا صورة عامة مثالية. اختيار أول نتيجة بحث أساسي كخيار أخير.")
        return results[0]
        
    print(f"❌ فشل العثور على أي صورة للمنتج '{product_name}'")
    return None
