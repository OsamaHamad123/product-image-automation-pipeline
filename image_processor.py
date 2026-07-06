import os
import requests
import base64
import json
import io
from PIL import Image, ImageOps
import config
import categories


def get_product_bounding_box(image_path, product_name, brand):
    """
    استخدام Gemini Vision لتحديد المربع المحيط بالمنتج (Bounding Box) بصيغة [ymin, xmin, ymax, xmax].
    الإحداثيات تكون نسبية من 0 إلى 1000.
    """
    if not config.GEMINI_API_KEY:
        return None
        
    try:
        # قراءة الصورة وضغطها لتفادي payloads الكبيرة
        with Image.open(image_path) as img:
            if img.mode != "RGB":
                img = img.convert("RGB")
            img.thumbnail((400, 400))
            buffer = io.BytesIO()
            img.save(buffer, format="JPEG", quality=70)
            img_data = base64.b64encode(buffer.getvalue()).decode("utf-8")
            
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash:generateContent?key={config.GEMINI_API_KEY}"
        
        prompt = (
            f"Locate the main commercial packaged product of the brand '{brand}' for '{product_name}' in this image. "
            f"Return the single bounding box enclosing ONLY the product package (carton, bottle, tub, bag). "
            f"The bounding box should be returned as normalized coordinates [ymin, xmin, ymax, xmax] "
            f"where 0 represents the top/left edge and 1000 represents the bottom/right edge of the image. "
            f"Reply strictly in JSON format matching this schema:\n"
            f'{{"box": [ymin, xmin, ymax, xmax]}}'
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
        print(f"🤖 جاري تحديد موقع المنتج بصرياً عبر Gemini 3.5 Vision...")
        config.METRICS["gemini_api_calls"] += 1
        response = requests.post(url, headers=headers, json=payload, timeout=15)
        
        if response.status_code == 200:
            res_data = response.json()
            text_response = res_data['candidates'][0]['content']['parts'][0]['text']
            text = text_response.strip()
            if text.startswith("```json"):
                text = text[7:]
            elif text.startswith("```"):
                text = text[3:]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()
            result = json.loads(text)
            box = result.get("box")
            if isinstance(box, list) and len(box) == 4:
                ymin, xmin, ymax, xmax = box
                if all(0 <= v <= 1000 for v in [ymin, xmin, ymax, xmax]) and (ymax > ymin) and (xmax > xmin):
                    print(f"🎯 تم تحديد موقع المنتج: [ymin={ymin}, xmin={xmin}, ymax={ymax}, xmax={xmax}]")
                    return box
        else:
            print(f"⚠️ فشل استدعاء Gemini API لتحديد موقع المنتج (كود {response.status_code}): {response.text}")
    except Exception as e:
        print(f"⚠️ خطأ أثناء تحديد موقع المنتج بـ Gemini: {e}")
        
    return None

def extract_metadata_from_image(image_path, product_name, brand):
    """
    استخدام Gemini Vision لتحليل العبوة واستخراج السعرات الحرارية، المكونات، والوصف التسويقي الثنائي والتصنيفات المطبقة.
    """
    if not config.GEMINI_API_KEY:
        return None
        
    try:
        # قراءة الصورة وضغطها
        with Image.open(image_path) as img:
            if img.mode != "RGB":
                img = img.convert("RGB")
            img.thumbnail((500, 500))  # دقة أعلى قليلاً للقراءة الدقيقة للنصوص
            buffer = io.BytesIO()
            img.save(buffer, format="JPEG", quality=80)
            img_data = base64.b64encode(buffer.getvalue()).decode("utf-8")
            
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash:generateContent?key={config.GEMINI_API_KEY}"
        
        # توليد نصوص مسارات التصنيف المعتمدة ديناميكياً لإرفاقها بالـ Prompt
        taxonomy_lines = []
        for l1_en, l1_data in categories.CATEGORIES.items():
            l1_ar = l1_data["ar"]
            for l2_en, l2_data in l1_data["subs"].items():
                l2_ar = l2_data["ar"]
                for l3_en, l3_ar in l2_data["sub_subs"].items():
                    taxonomy_lines.append(f"- {l1_en} ({l1_ar}) > {l2_en} ({l2_ar}) > {l3_en} ({l3_ar})")
        taxonomy_str = "\n".join(taxonomy_lines)

        prompt = (
            f"You are an expert e-commerce catalog manager. Analyze the product package image for '{brand} - {product_name}'.\n"
            f"Tasks:\n"
            f"1. Extract the Nutrition Facts (e.g. calories, fat, protein, carbs, sugar) printed on the label and summarize them as a concise English text summary.\n"
            f"2. Extract the complete Ingredients List and clearly list any allergens (e.g., contains gluten, dairy, or nuts).\n"
            f"3. Write a compelling e-commerce marketing description for the product in English.\n"
            f"4. Write a compelling e-commerce marketing description for the product in Arabic.\n"
            f"5. Automatically categorize the product into a 3-level hierarchy (L1 Category, L2 Category, L3 Category) strictly choosing from the predefined taxonomy list below.\n"
            f"   Predefined Taxonomy Paths:\n{taxonomy_str}\n\n"
            f"6. Generate 3 to 6 smart tags/attributes for the product (e.g., Organic, Low Fat, Gluten-Free) in both English and Arabic (as comma-separated strings).\n\n"
            f"Reply strictly in JSON format matching this schema:\n"
            f'{{\n'
            f'  "nutrition": "Nutrition Facts summary text (e.g. Calories 150, Fat 5g, Carbs 20g, Protein 3g per 100g)",\n'
            f'  "ingredients": "Ingredients list (e.g. wheat flour, sugar, salt. Allergens: contains gluten)",\n'
            f'  "description_en": "a compelling e-commerce description in English",\n'
            f'  "description_ar": "وصف تسويقي جذاب ومقنع للمنتج باللغة العربية",\n'
            f'  "category_l1_en": "Must be the exact English L1 Category name from the chosen path in the taxonomy",\n'
            f'  "category_l2_en": "Must be the exact English L2 Category name from the chosen path in the taxonomy",\n'
            f'  "category_l3_en": "Must be the exact English L3 Category name from the chosen path in the taxonomy",\n'
            f'  "category_l1_ar": "Must be the exact Arabic L1 Category name from the chosen path in the taxonomy",\n'
            f'  "category_l2_ar": "Must be the exact Arabic L2 Category name from the chosen path in the taxonomy",\n'
            f'  "category_l3_ar": "Must be the exact Arabic L3 Category name from the chosen path in the taxonomy",\n'
            f'  "tags_en": "Tag1, Tag2, Tag3",\n'
            f'  "tags_ar": "وسم1, وسم2, وسم3"\n'
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
        print(f"🤖 جاري استخراج السعرات والبيانات التسويقية من الصورة عبر Gemini 3.5 Vision...")
        config.METRICS["gemini_api_calls"] += 1
        response = requests.post(url, headers=headers, json=payload, timeout=60)
        
        if response.status_code == 200:
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
            if isinstance(result, dict):
                # تطبيع وتصحيح التصنيفات المحددة مع قاعدة الفهرس المعتمدة
                normalized_cats = categories.normalize_category_path(
                    result.get("category_l1_en", ""),
                    result.get("category_l2_en", ""),
                    result.get("category_l3_en", "")
                )
                result.update(normalized_cats)
                print("🎯 تم استخراج السعرات والبيانات التسويقية وتطبيع التصنيفات بنجاح!")
                return result
        else:
            print(f"⚠️ فشل استدعاء Gemini API لاستخراج البيانات الوصفية (كود {response.status_code}): {response.text}")
    except Exception as e:
        print(f"⚠️ خطأ أثناء استخراج البيانات الوصفية من الصورة بـ Gemini: {e}")
        
    return None

def crop_image_by_box(image_path, box, output_path):
    """
    اقتصاص الصورة بناءً على المربع المحدد بالإحداثيات النسبية [ymin, xmin, ymax, xmax] (من 0 إلى 1000).
    """
    try:
        ymin, xmin, ymax, xmax = box
        with Image.open(image_path) as img:
            width, height = img.size
            
            # تحويل الإحداثيات النسبية (0-1000) إلى بكسلات فعلية
            left = int((xmin / 1000) * width)
            top = int((ymin / 1000) * height)
            right = int((xmax / 1000) * width)
            bottom = int((ymax / 1000) * height)
            
            # التأكد من عدم تجاوز الحدود
            left = max(0, min(left, width - 1))
            top = max(0, min(top, height - 1))
            right = max(left + 1, min(right, width))
            bottom = max(top + 1, min(bottom, height))
            
            cropped = img.crop((left, top, right, bottom))
            cropped.save(output_path)
            print(f"✂️ تم اقتصاص المنتج بنجاح وحفظه في: {output_path}")
            return True
    except Exception as e:
        print(f"❌ خطأ أثناء اقتصاص الصورة: {e}")
        return False

def download_image(url, save_path):
    """
    تنزيل الصورة من الرابط وحفظها محلياً.
    """
    if not (url.startswith("http://") or url.startswith("https://")):
        import shutil
        try:
            shutil.copy(url, save_path)
            return True
        except Exception as e:
            print(f"❌ خطأ أثناء نسخ الملف المحلي: {e}")
            return False
            
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        proxies = {"http": config.PROXY_URL, "https": config.PROXY_URL} if config.PROXY_URL else None
        r = requests.get(url, headers=headers, timeout=10, stream=True, proxies=proxies)
        if r.status_code == 200:
            with open(save_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
            return True
        else:
            print(f"❌ فشل تنزيل الصورة، كود الاستجابة: {r.status_code}")
            return False
    except Exception as e:
        print(f"❌ خطأ أثناء تنزيل الصورة: {e}")
        return False

def is_background_already_removed(image_path):
    """
    التحقق مما إذا كانت الصورة تحتوي بالفعل على خلفية مزالة (شفافة).
    """
    try:
        with Image.open(image_path) as img:
            # إذا كان نظام الألوان يدعم الشفافية
            if img.mode in ('RGBA', 'LA') or (img.mode == 'P' and 'transparency' in img.info):
                alpha = img.convert('RGBA').split()[-1]
                min_alpha, max_alpha = alpha.getextrema()
                # إذا وجدنا بكسل واحد على الأقل شفاف (ألفا أقل من 255)
                if min_alpha < 255:
                    return True
    except Exception:
        pass
    return False

def remove_white_background_floodfill(input_path, output_path, thresh=30):
    """
    إزالة الخلفية البيضاء باستخدام خوارزمية Flood-fill من الزوايا الأربعة.
    ترجع True إذا نجح الملء وعثر على زوايا بيضاء، وFalse خلاف ذلك.
    """
    try:
        from PIL import ImageDraw
        img = Image.open(input_path).convert("RGBA")
        width, height = img.size
        corners = [(0, 0), (width - 1, 0), (0, height - 1), (width - 1, height - 1)]
        
        filled = False
        for cx, cy in corners:
            pixel = img.getpixel((cx, cy))
            # إذا كان لون الزاوية قريب من الأبيض (أكبر من 240)
            if pixel[0] > 240 and pixel[1] > 240 and pixel[2] > 240:
                ImageDraw.floodfill(img, (cx, cy), (0, 0, 0, 0), thresh=thresh)
                filled = True
                
        if filled:
            img.save(output_path, "PNG")
            return True
    except Exception as e:
        print(f"⚠️ خطأ أثناء إزالة الخلفية البيضاء بـ floodfill: {e}")
    return False

def remove_background(input_path, output_path):
    """
    إزالة خلفية الصورة بناءً على الطريقة المحددة في الإعدادات.
    """
    # 1. تحقق مما إذا كانت الخلفية مزالة أصلاً (شفافة) لتخطي معالجة rembg وحماية جودة الصورة
    if is_background_already_removed(input_path):
        print("ℹ️ الصورة تحتوي بالفعل على خلفية شفافة (مزالة). سيتم تخطي عملية القص لحمايتها وتوفير الوقت.")
        try:
            with Image.open(input_path) as img:
                img.save(output_path)
            return True
        except Exception as e:
            print(f"❌ خطأ أثناء نسخ الصورة الشفافة: {e}")
            return False

    method = config.BG_REMOVAL_METHOD.lower()

    # 2. تحقق مما إذا كانت الخلفية بيضاء صلبة، ونقوم بإزالتها بـ Flood-fill كخيار مجاني سريع فقط عند إيقاف rembg
    if method == "none":
        print("⏳ جاري فحص الصورة للكشف عن خلفية بيضاء صلبة...")
        if remove_white_background_floodfill(input_path, output_path, thresh=30):
            print("✅ تم إزالة الخلفية البيضاء الخارجية بنجاح باستخدام خوارزمية Flood-fill!")
            return True
    
    if method == "none":
        # تخطي إزالة الخلفية
        print("ℹ️ تخطي إزالة الخلفية بناءً على الإعدادات (BG_REMOVAL_METHOD = 'none').")
        # نسخ الملف الأصلي إلى مسار المخرج
        try:
            with Image.open(input_path) as img:
                img.save(output_path)
            return True
        except Exception as e:
            print(f"❌ خطأ أثناء نسخ الصورة الأصلية: {e}")
            return False
            
    elif method == "rembg":
        print("⏳ جاري إزالة الخلفية محلياً باستخدام مكتبة 'rembg' ونموذج 'isnet-general-use' الاحترافي...")
        try:
            from rembg import remove, new_session
            # استخدام نموذج IS-Net الاحترافي للقص الدقيق للمنتجات التجارية
            session = new_session("isnet-general-use")
            with open(input_path, 'rb') as i:
                input_data = i.read()
                output_data = remove(input_data, session=session)
            with open(output_path, 'wb') as o:
                o.write(output_data)
            print("✅ تم إزالة الخلفية محلياً بنجاح باستخدام نموذج IS-Net!")
            return True
        except ImportError:
            print("❌ خطأ: مكتبة 'rembg' غير مثبتة. يرجى تثبيتها باستخدام الأمر: pip install rembg")
            print("💡 سيتم تخطي إزالة الخلفية لهذه الصورة.")
            # نسخ الملف كبديل
            with Image.open(input_path) as img:
                img.save(output_path)
            return True
        except Exception as e:
            print(f"❌ حدث خطأ أثناء إزالة الخلفية محلياً: {e}")
            return False
            
    elif method == "remove_bg_api":
        print("⏳ جاري إزالة الخلفية سحابياً باستخدام 'remove.bg' API...")
        if not config.REMOVE_BG_API_KEY:
            print("❌ خطأ: مفتاح REMOVE_BG_API_KEY غير موجود في config.py.")
            return False
            
        try:
            response = requests.post(
                'https://api.remove.bg/v1.0/removebg',
                files={'image_file': open(input_path, 'rb')},
                data={'size': 'auto'},
                headers={'X-Api-Key': config.REMOVE_BG_API_KEY},
                timeout=15
            )
            if response.status_code == requests.codes.ok:
                with open(output_path, 'wb') as out:
                    out.write(response.content)
                print("✅ تم إزالة الخلفية عبر API بنجاح!")
                return True
            else:
                print(f"❌ فشل إزالة الخلفية عبر API: {response.text}")
                return False
        except Exception as e:
            print(f"❌ خطأ أثناء الاتصال بـ remove.bg API: {e}")
            return False
            
    else:
        print(f"⚠️ طريقة إزالة الخلفية غير معروفة: '{method}'. سيتم تخطي الإزالة.")
        with Image.open(input_path) as img:
            img.save(output_path)
        return True

def resize_and_pad_image(input_path, output_path, target_size=None, background_color=(255, 255, 255, 255)):
    """
    تغيير حجم الصورة بشكل ديناميكي وتوسيطها داخل مساحة بالأبعاد المطلوبة (مثلاً 800×800) مع الحفاظ على التناسب،
    مع تطبيق تنعيم الحواف (Edge Feathering) وإضافة ظل ساقط طبيعي وناعم (Drop Shadow).
    """
    if target_size is None:
        target_size = config.IMAGE_TARGET_SIZE
        
    print(f"⏳ جاري تحجيم وتجهيز الصورة بالأبعاد: {target_size[0]}x{target_size[1]} مع إضافة ظل وتنعيم الحواف...")
    try:
        from PIL import ImageFilter
        
        # 1. فتح الصورة الأصلية وتحويلها لنظام RGBA لدعم الشفافية والظلال
        with Image.open(input_path) as img:
            img = img.convert("RGBA")
            
            # 2. تغيير حجم الصورة مع الحفاظ على التناسب (مقياس 85% لتوفير هامش كافي للظل)
            img.thumbnail((int(target_size[0] * 0.85), int(target_size[1] * 0.85)), Image.Resampling.LANCZOS)
            
            alpha = img.getchannel('A')
            # تطبيق تمويه خفيف جداً لجعل الحواف ناعمة ومنع القص الخشن دون التضحية بدقة ونصوص العبوة
            alpha_feathered = alpha.filter(ImageFilter.GaussianBlur(radius=0.8))
            img.putalpha(alpha_feathered)
            
            # --- ب. إنشاء ظل ساقط ناعم (Soft Drop Shadow) ---
            # إنشاء قناع ظل بلون رمادي داكن
            shadow = Image.new("RGBA", img.size, (20, 20, 20, 255))
            shadow.putalpha(alpha_feathered)
            
            # تكبير وتنعيم الظل للحصول على انسيابية طبيعية
            shadow_large = shadow.resize((img.width + 12, img.height + 12), Image.Resampling.BILINEAR)
            shadow_blurred = shadow_large.filter(ImageFilter.GaussianBlur(radius=15))
            
            # تخفيف شفافية الظل ليصبح طبيعياً وغير مزعج (عتامة بنسبة 22%)
            shadow_alpha = shadow_blurred.getchannel('A')
            shadow_alpha = shadow_alpha.point(lambda p: int(p * 0.22))
            shadow_blurred.putalpha(shadow_alpha)
            
            # 3. إنشاء لوحة خلفية جديدة بالحجم المستهدف والألوان المطلوبة (خلفية بيضاء افتراضياً)
            new_img = Image.new("RGBA", target_size, background_color)
            
            # 4. حساب مواقع اللصق لتوسيط المنتج والظل
            x = (target_size[0] - img.width) // 2
            y = (target_size[1] - img.height) // 2
            
            # إزاحة الظل للأسفل واليمين لمحاكاة إضاءة استوديو متناسقة
            sx = (target_size[0] - shadow_large.width) // 2 + 3
            sy = (target_size[1] - shadow_large.height) // 2 + 12
            
            # 5. لصق قناع الظل أولاً
            new_img.paste(shadow_blurred, (sx, sy), mask=shadow_blurred)
            
            # 6. لصق الصورة الممهدة فوق قناع الظل مباشرة
            new_img.paste(img, (x, y), mask=img)
            
            # 7. حفظ الصورة النهائية
            new_img.save(output_path, "PNG")
            
        print("✅ تم إعادة تحجيم وتوسيط الصورة وتطبيق تأثيرات الظل وتنعيم الحواف بنجاح!")
        return True
        
    except Exception as e:
        print(f"❌ خطأ أثناء معالجة تأثيرات الصورة: {e}")
        return False

def enhance_image_quality(image_path, output_path):
    """
    تجميل وتحسين جودة صورة المنتج:
    1. زيادة التباين (Contrast) لإبراز تفاصيل وألوان العبوة.
    2. زيادة حدة النصوص والخطوط بفلتر الشحذ (Sharpness).
    3. تنظيف وتصفية البكسلات المشوشة والعلامات المائية المتواجدة في الأطراف أو الزوايا.
    """
    try:
        from PIL import Image, ImageEnhance, ImageFilter
        
        with Image.open(image_path) as img:
            if img.mode != "RGBA":
                img = img.convert("RGBA")
                
            width, height = img.size
            margin_w = int(width * 0.05)
            margin_h = int(height * 0.05)
            
            pixels = img.load()
            
            # تنظيف الهوامش والأطراف من أي بكسلات أو علامات مائية معزولة
            for x in range(width):
                for y in range(height):
                    if x < margin_w or x > (width - margin_w) or y < margin_h or y > (height - margin_h):
                        r, g, b, a = pixels[x, y]
                        if a > 0:
                            pixels[x, y] = (255, 255, 255, 0)
                            
            # تعزيز التباين والألوان والحدة
            enhancer_contrast = ImageEnhance.Contrast(img)
            img = enhancer_contrast.enhance(1.15)
            
            enhancer_color = ImageEnhance.Color(img)
            img = enhancer_color.enhance(1.10)
            
            img = img.filter(ImageFilter.SHARPEN)
            enhancer_sharpness = ImageEnhance.Sharpness(img)
            img = enhancer_sharpness.enhance(1.20)
            
            img.save(output_path, "PNG")
            print("✨ [تجميل الصورة] تم تحسين الألوان وتصفية الأطراف بنجاح!")
            return True
    except Exception as e:
        print(f"⚠️ خطأ أثناء تجميل الصورة: {e}")
        return False

def process_product_image(image_url, product_name, brand):
    """
    المعالج الشامل لصورة المنتج: تحميل، إزالة خلفية، تحجيم.
    يرجع مسار ملف الصورة المعالجة النهائي.
    """
    # إنشاء مجلد مؤقت للعمليات داخل بيئة العمل
    os.makedirs("temp", exist_ok=True)
    
    # تنظيف اسم الملف
    safe_name = f"{product_name}_{brand}".replace("/", "_").replace("\\", "_").replace(":", "_").replace(" ", "_")
    
    raw_path = os.path.join("temp", f"raw_{safe_name}.png")
    nobg_path = os.path.join("temp", f"nobg_{safe_name}.png")
    final_path = os.path.join("temp", f"final_{safe_name}.png")
    
    # 1. تنزيل الصورة
    if not download_image(image_url, raw_path):
        return None
        
    # 1.5. الاقتصاص الذكي بالذكاء الاصطناعي إذا تم العثور على مربع محيط بالمنتج
    box = get_product_bounding_box(raw_path, product_name, brand)
    if box:
        cropped_path = os.path.join("temp", f"cropped_{safe_name}.png")
        if crop_image_by_box(raw_path, box, cropped_path):
            try:
                if os.path.exists(raw_path):
                    os.remove(raw_path)
            except Exception:
                pass
            os.rename(cropped_path, raw_path)
        
    # 2. إزالة الخلفية
    if not remove_background(raw_path, nobg_path):
        # في حال الفشل نستخدم الصورة الخام كمصدر للخطوة التالية
        nobg_path = raw_path
        
    # 3. التحجيم الديناميكي والتوسيط
    if not resize_and_pad_image(nobg_path, final_path):
        return None
        
    # 4. التجميل التوليدي والحد البصري وإزالة العلامات المائية الهامشية
    temp_enhanced = os.path.join("temp", f"enhanced_{safe_name}.png")
    if enhance_image_quality(final_path, temp_enhanced):
        try:
            if os.path.exists(final_path):
                os.remove(final_path)
            os.rename(temp_enhanced, final_path)
        except Exception:
            pass
        
    # تنظيف الملفات المؤقتة غير الضرورية لتوفير المساحة
    try:
        if os.path.exists(raw_path) and raw_path != nobg_path:
            os.remove(raw_path)
        if os.path.exists(nobg_path) and nobg_path != final_path:
            os.remove(nobg_path)
    except Exception:
        pass
        
    return final_path

def send_telegram_notification(text):
    """
    إرسال تنبيه فوري عبر بوت Telegram إذا تم توفير مفتاح البوت ومعرف الدردشة في الإعدادات.
    """
    token = config.TELEGRAM_BOT_TOKEN
    chat_id = config.TELEGRAM_CHAT_ID
    if not token or not chat_id:
        return False
        
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML"
    }
    try:
        proxies = {"http": config.PROXY_URL, "https": config.PROXY_URL} if config.PROXY_URL else None
        res = requests.post(url, json=payload, timeout=10, proxies=proxies)
        if res.status_code == 200:
            return True
        else:
            print(f"⚠️ فشل إرسال إشعار Telegram: {res.text}")
            return False
    except Exception as e:
        print(f"⚠️ خطأ أثناء الاتصال بـ Telegram API: {e}")
        return False
