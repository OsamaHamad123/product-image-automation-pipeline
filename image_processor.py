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
                # تصنيف المنتج دلالياً باستخدام مصنف التصنيفات الذكي المتجهي
                try:
                    from taxonomy_classifier import EnterpriseTaxonomyClassifier
                    classifier = EnterpriseTaxonomyClassifier()
                    cat_path, confidence = classifier.classify_product_title(product_name)
                    if cat_path and " > " in cat_path:
                        parts = cat_path.split(" > ")
                        if len(parts) >= 1: result["category_l1_en"] = parts[0]
                        if len(parts) >= 2: result["category_l2_en"] = parts[1]
                        if len(parts) >= 3: result["category_l3_en"] = parts[2]
                except Exception as ex:
                    print(f"⚠️ خطأ أثناء تصنيف المنتج دلالياً: {ex}")

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

def execute_high_fidelity_refinement(
    image_path: str, 
    output_path: str, 
    guided_radius: int = 4, 
    guided_eps: float = 1e-5
) -> bool:
    """
    تقوم بدمج عزل فجوات الشفافية الداخلية للمنتج برمجياً وصقل الحواف
    باستخدام الفلتر الموجه Guided Filter لمنع هالات الحواف الداكنة.
    """
    try:
        import cv2
        import numpy as np
        from scipy import ndimage
        
        # قراءة الصورة مع جميع القنوات
        src = cv2.imread(image_path, cv2.IMREAD_UNCHANGED)
        if src is None:
            raise FileNotFoundError(f"فشل قراءة الصورة من المسار: {image_path}")
            
        if src.shape[2] < 4:
            # إذا لم تكن هناك قناة شفافية، احفظ الصورة كما هي
            cv2.imwrite(output_path, src)
            return True

        # استخراج قنوات الألوان وقناة الشفافية
        b, g, r, raw_alpha = cv2.split(src)
        rgb_guidance = cv2.merge([b, g, r])

        # 1. إعداد قناع ثنائي وتعبئة الفجوات داخل حدود المنتج
        _, binary_mask = cv2.threshold(raw_alpha, 1, 255, cv2.THRESH_BINARY)
        normalized_binary = (binary_mask / 255).astype(np.int32)
        filled_structure = ndimage.binary_fill_holes(normalized_binary)
        filled_mask = (filled_structure.astype(np.uint8)) * 255

        # تحديد الفجوات المعبأة وإضافتها لقناة الشفافية الأصلية
        internal_holes = cv2.subtract(filled_mask, binary_mask)
        restored_alpha = cv2.bitwise_or(raw_alpha, internal_holes)

        # 2. تطبيق تصفية الحواف الموجهة (Guided Filter)
        try:
            from cv2.ximgproc import guidedFilter
            refined_alpha = guidedFilter(
                guide=rgb_guidance,
                src=restored_alpha,
                radius=guided_radius,
                eps=guided_eps
            )
        except ImportError:
            # تراجع آمن عند غياب cv2.ximgproc (استخدام فلتر ثنائي لتنعيم الحواف)
            kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
            closed_alpha = cv2.morphologyEx(restored_alpha, cv2.MORPH_CLOSE, kernel)
            refined_alpha = cv2.bilateralFilter(closed_alpha, d=5, sigmaColor=75, sigmaSpace=75)

        # التأكد من بقاء قيم الشفافية في النطاق الصحيح
        refined_alpha = np.clip(refined_alpha, 0, 255).astype(np.uint8)

        # دمج وحفظ الصورة النهائية
        refined_rgba = cv2.merge([b, g, r, refined_alpha])
        cv2.imwrite(output_path, refined_rgba)
        print("✅ [High-Fidelity Refinement] تم صقل الحواف وتعبئة الثقوب بنجاح!")
        return True
    except Exception as e:
        print(f"⚠️ فشل صقل حواف الصورة وتعبئة فجواتها: {e}")
        return False

def validate_alpha_matte(rgba_path: str, bbox_area: float = None) -> bool:
    """
    التحقق البرمجي التلقائي من جودة قناع الشفافية لمنع عزل الأجزاء الخاطئة أو مسح المنتجات.
    يعتمد على معيارين أساسيين:
    1. عتبة الكتلة الأمامية (Mass Sparing): لمنع اختفاء أجزاء المنتج بالكامل (0.08 < R_mass < 0.95).
    2. عتبة تسريب الحواف (Canvas Boundary Leakage): للتحقق من عدم وجود خلفية متبقية على أطراف الكانفاس (L_edge < 0.05).
    """
    try:
        import cv2
        import numpy as np
        
        img = cv2.imread(rgba_path, cv2.IMREAD_UNCHANGED)
        if img is None or img.shape[2] < 4:
            print("⚠️ [Validation] الصورة لا تحتوي على قناة شفافية صالحة للتحقق.")
            return False

        _, _, _, alpha = cv2.split(img)
        h, w = alpha.shape
        total_canvas_pixels = h * w

        # 1. التحقق من بقاء كتلة المنتج (Mass Sparing Check)
        foreground_pixels = np.sum(alpha > 0)
        mass_ratio = foreground_pixels / bbox_area if (bbox_area and bbox_area > 0) else foreground_pixels / total_canvas_pixels
        print(f"📊 [Validation Heuristics] Mass Ratio: {mass_ratio:.4f}")
        if mass_ratio < 0.08 or mass_ratio > 0.95:
            print("❌ [Validation Heuristics Failed] نسبة كتلة المنتج خارج الحدود المقبولة.")
            return False

        # 2. التحقق من تسريب الحواف الخارجية (Boundary Leakage Check)
        border_mask = np.ones((h, w), dtype=np.uint8)
        border_mask[4:-4, 4:-4] = 0  # عزل إطار بعرض 4 بكسل على أطراف الصورة
        border_active_pixels = np.sum(cv2.bitwise_and(alpha, alpha, mask=border_mask) > 5)
        total_border_pixels = total_canvas_pixels - (h-8)*(w-8)
        leakage_ratio = border_active_pixels / total_border_pixels
        print(f"📊 [Validation Heuristics] Boundary Edge Leakage: {leakage_ratio:.4f}")
        if leakage_ratio > 0.05:
            print("❌ [Validation Heuristics Failed] تسريب بقايا الخلفية على أطراف الكانفاس أعلى من 5%.")
            return False

        print("✅ [Validation Heuristics Passed] تم اجتياز التحقق من جودة قناع الشفافية بنجاح!")
        return True
    except Exception as e:
        print(f"⚠️ فشل التحقق من جودة الشفافية برمجياً: {e}")
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
            
    elif method == "bria_rmbg":
        print("⏳ [Background Removal] Running Bria RMBG 1.4 model locally (Hugging Face)...")
        try:
            import torch
            from PIL import Image
            from transformers import pipeline
            
            device = 0 if torch.cuda.is_available() else -1
            pipe = pipeline("image-segmentation", model="briaai/RMBG-1.4", trust_remote_code=True, device=device)
            
            img = Image.open(input_path).convert("RGB")
            nobg_img = pipe(img)
            nobg_img.save(output_path, "PNG")
            print("✅ [Background Removal] Bria RMBG 1.4 completed successfully!")
            # صقل حواف الصورة وتعبئة أي فجوات شفافة داخل جسم المنتج
            execute_high_fidelity_refinement(output_path, output_path)
            return True
        except Exception as e:
            print(f"❌ [Background Removal] Bria RMBG 1.4 failed: {e}")
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
            # صقل حواف الصورة وتعبئة أي فجوات شفافة داخل جسم المنتج
            execute_high_fidelity_refinement(output_path, output_path)
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
                # صقل حواف الصورة وتعبئة أي فجوات شفافة داخل جسم المنتج
                execute_high_fidelity_refinement(output_path, output_path)
                return True
            else:
                print(f"❌ فشل إزالة الخلفية عبر API: {response.text}")
                return False
        except Exception as e:
            print(f"❌ خطأ أثناء الاتصال بـ remove.bg API: {e}")
            return False
            
    elif method == "grabcut":
        print("⏳ [Background Removal] Running GrabCut manual segmentation locally (OpenCV)...")
        try:
            import cv2
            import numpy as np
            
            img = cv2.imread(input_path)
            if img is None:
                raise ValueError("Failed to load image for GrabCut")
                
            mask = np.zeros(img.shape[:2], np.uint8)
            bgdModel = np.zeros((1, 65), np.float64)
            fgdModel = np.zeros((1, 65), np.float64)
            
            h, w = img.shape[:2]
            rect = (int(w * 0.05), int(h * 0.05), int(w * 0.9), int(h * 0.9))
            
            cv2.grabCut(img, mask, rect, bgdModel, fgdModel, 5, cv2.GC_INIT_WITH_RECT)
            
            mask2 = np.where((mask==2)|(mask==0), 0, 1).astype('uint8')
            img_rgba = cv2.cvtColor(img, cv2.COLOR_BGR2BGRA)
            img_rgba[:, :, 3] = mask2 * 255
            
            cv2.imwrite(output_path, img_rgba)
            print("✅ [Background Removal] GrabCut segmentation completed successfully!")
            # صقل حواف الصورة وتعبئة أي فجوات شفافة داخل جسم المنتج
            execute_high_fidelity_refinement(output_path, output_path)
            return True
        except Exception as e:
            print(f"❌ [Background Removal] GrabCut segmentation failed: {e}")
            # نسخ الملف الأصلي كبديل
            try:
                with Image.open(input_path) as img_pil:
                    img_pil.save(output_path)
                return True
            except Exception:
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
            new_img.save(output_path, "WEBP", quality=85, method=4)
            
        print("✅ تم إعادة تحجيم وتوسيط الصورة وتطبيق تأثيرات الظل وتنعيم الحواف بنجاح!")
        return True
        
    except Exception as e:
        print(f"❌ خطأ أثناء معالجة تأثيرات الصورة: {e}")
        return False

def upscale_image_if_small(image_path: str, target_min_size: int = 600):
    """
    ترقية دقة الصورة رياضياً باستخدام فلتر Lanczos عالي الجودة إذا كانت أبعادها صغيرة،
    بدون استخدام نماذج الذكاء الاصطناعي التوليدية لمنع التشويه البصري.
    """
    from PIL import Image
    try:
        with Image.open(image_path) as img:
            w, h = img.size
            if w < target_min_size or h < target_min_size:
                # حساب أبعاد الترقية بمعدل 2x
                new_w = w * 2
                new_h = h * 2
                print(f"🔄 [Resize Lanczos] أبعاد الصورة ({w}x{h}) أقل من الحد الأدنى ({target_min_size}). جاري الترقية الرياضية إلى ({new_w}x{new_h})...")
                
                # استخدام Lanczos لإعادة التحجيم
                resized_img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
                resized_img.save(image_path)
                print(f"✅ [Resize Lanczos] تم ترقية الصورة رياضياً بنجاح.")
    except Exception as e:
        print(f"⚠️ خطأ أثناء معالجة ترقية دقة الصورة رياضياً: {e}")

def is_image_blurry(image_path: str, threshold: float = 40.0) -> bool:
    """
    قياس تباين اللابلاسيان لتقييم وضوح الصورة وفلترة الصور المشوشة.
    """
    import cv2
    try:
        img = cv2.imread(image_path)
        if img is None:
            print("⚠️ [Blur Check] لا يمكن قراءة الصورة أو ملف تالف.")
            return True
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        laplacian_var = cv2.Laplacian(gray, cv2.CV_64F).var()
        print(f"📊 [Blur Check] Laplacian Variance: {laplacian_var:.4f}")
        return laplacian_var < threshold
    except Exception as e:
        print(f"⚠️ خطأ أثناء فحص تشويش الصورة: {e}")
        return False

def denoise_image_opencv(image_path: str, output_path: str):
    """
    إزالة التشويش والضوضاء البصرية باستخدام خوارزمية Fast Non-Local Means.
    """
    import cv2
    try:
        img = cv2.imread(image_path)
        if img is None:
            return
        # حماية قناة الشفافية إن وجدت
        if len(img.shape) == 3 and img.shape[2] == 4:
            b, g, r, a = cv2.split(img)
            rgb = cv2.merge([b, g, r])
            denoised_rgb = cv2.fastNlMeansDenoisingColored(rgb, None, h=10, hColor=10, templateWindowSize=7, searchWindowSize=21)
            denoised = cv2.merge([denoised_rgb, a])
        else:
            denoised = cv2.fastNlMeansDenoisingColored(img, None, h=10, hColor=10, templateWindowSize=7, searchWindowSize=21)
        cv2.imwrite(output_path, denoised)
        print("✅ [Denoising] تم تنظيف الصورة وإزالة التشويش بنجاح!")
    except Exception as e:
        print(f"⚠️ خطأ أثناء تنظيف الصورة من التشويش: {e}")



def apply_saliency_smart_crop(image_path: str, output_path: str, target_width: int, target_height: int):
    """
    حساب خريطة الأهمية البصرية واقتصاص الصورة بنسبة 1:1 حول المنتج.
    تتراجع تلقائياً لتصفية الحواف (Canny Edge Detection) في حال عدم توفر مكتبة saliency في OpenCV.
    """
    import cv2
    import numpy as np
    try:
        img = cv2.imread(image_path, cv2.IMREAD_UNCHANGED)
        if img is None:
            return
        h_orig, w_orig = img.shape[:2]
        
        threshold_map = None
        # 1. محاولة حساب الأهمية البصرية (تتطلب opencv-contrib-python)
        try:
            saliency = cv2.saliency.StaticSaliencySpectralResidual_create()
            success, saliency_map = saliency.computeSaliency(img)
            if success:
                saliency_map = (saliency_map * 255).astype(np.uint8)
                _, threshold_map = cv2.threshold(saliency_map, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        except (AttributeError, Exception):
            pass
            
        # 2. مسار التراجع التلقائي باستخدام كشف الحواف التقليدي (Canny) المتوفر دائماً
        if threshold_map is None:
            print("⚠️ [Smart Crop Fallback] حزمة cv2.saliency غير متوفرة. استخدام كشف الحواف Canny كبديل مجاني محلي...")
            # تحويل الصورة إلى تدرج الرمادي
            if len(img.shape) == 3 and img.shape[2] >= 3:
                gray = cv2.cvtColor(img[:, :, :3], cv2.COLOR_BGR2GRAY)
            else:
                gray = img
            
            # تقليل الضوضاء
            blurred = cv2.GaussianBlur(gray, (5, 5), 0)
            # كشف الحواف
            edges = cv2.Canny(blurred, 50, 150)
            # توسيع الحواف قليلاً لسد الفجوات
            kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
            threshold_map = cv2.dilate(edges, kernel, iterations=1)
        
        # 3. إيجاد الكنتور الأكبر لتحديد موقع المنتج
        contours, _ = cv2.findContours(threshold_map, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if contours:
            largest_contour = max(contours, key=cv2.contourArea)
            x, y, w, h = cv2.boundingRect(largest_contour)
            
            center_x = x + (w // 2)
            center_y = y + (h // 2)
            
            # تحديد نافذة القص بنسبة التناسب المستهدفة
            target_ratio = target_width / target_height
            if w_orig / h_orig > target_ratio:
                crop_h = h_orig
                crop_w = int(h_orig * target_ratio)
            else:
                crop_w = w_orig
                crop_h = int(w_orig / target_ratio)
                
            x_start = max(0, min(center_x - (crop_w // 2), w_orig - crop_w))
            y_start = max(0, min(center_y - (crop_h // 2), h_orig - crop_h))
            
            cropped = img[y_start:y_start + crop_h, x_start:x_start + crop_w]
            
            # التحجيم النهائي
            final_img = cv2.resize(cropped, (target_width, target_height), interpolation=cv2.INTER_LANCZOS4)
            cv2.imwrite(output_path, final_img)
            print("✂️ [Smart Crop] تم الاقتصاص الذكي بنجاح حول الكائن الرئيسي!")
        else:
            # تراجع عادي في حال عدم وجود حدود واضحة
            cv2.imwrite(output_path, img)
            print("⚠️ [Smart Crop] لم يتم العثور على كنتور، حفظ الصورة الأصلية كما هي.")
    except Exception as e:
        print(f"⚠️ خطأ أثناء تطبيق الاقتصاص الذكي (Saliency/Canny): {e}")

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
            
            img.save(output_path, "WEBP", quality=85, method=4)
            print("✨ [تجميل الصورة] تم تحسين الألوان وتصفية الأطراف بنجاح!")
            return True
    except Exception as e:
        print(f"⚠️ خطأ أثناء تجميل الصورة: {e}")
        return False

def process_product_image(image_url, product_name, brand, bg_removal_method=None):
    """
    تحميل الصورة وإزالة خلفيتها فقط - التحجيم والقص والتوسيط يتم سحابياً عبر Cloudinary.
    يرجع مسار ملف الصورة بخلفية شفافة جاهزة للرفع.
    """
    old_bg_method = config.BG_REMOVAL_METHOD
    if bg_removal_method:
        mapped = bg_removal_method.lower().strip()
        if mapped == 'bria':
            mapped = 'bria_rmbg'
        config.BG_REMOVAL_METHOD = mapped
        print(f"🔧 [Manual Override] Overriding BG Removal Method to: '{config.BG_REMOVAL_METHOD}'")
        
    try:
        os.makedirs("temp", exist_ok=True)
    
        safe_name = f"{product_name}_{brand}".replace("/", "_").replace("\\", "_").replace(":", "_").replace(" ", "_")
        
        raw_path  = os.path.join("temp", f"raw_{safe_name}.webp")
        nobg_path = os.path.join("temp", f"nobg_{safe_name}.webp")
        
        # 1. تنزيل الصورة الأصلية
        if not download_image(image_url, raw_path):
            return None
            
        # التحقق من وضوح الصورة وتجنب المعالجة إذا كانت مشوشة جداً
        if is_image_blurry(raw_path, threshold=40.0):
            print(f"⚠️ [Blurry Image Detected] الصورة مشوشة جداً (Laplacian Variance < 40). سيتم تخطيها.")
            try:
                if os.path.exists(raw_path):
                    os.remove(raw_path)
            except Exception:
                pass
            return None
    
        # تنظيف التشويش والضوضاء البصرية من الصورة المجلوبة
        denoise_image_opencv(raw_path, raw_path)
    
        # ترقية دقة الصورة بالذكاء الاصطناعي إذا كانت صغيرة
        upscale_image_if_small(raw_path, target_min_size=600)
            
        # 2. الاقتصاص الذكي بالذكاء الاصطناعي إذا تم العثور على مربع محيط بالمنتج
        box = get_product_bounding_box(raw_path, product_name, brand)
        if box:
            cropped_path = os.path.join("temp", f"cropped_{safe_name}.webp")
            if crop_image_by_box(raw_path, box, cropped_path):
                try:
                    if os.path.exists(raw_path):
                        os.remove(raw_path)
                except Exception:
                    pass
                os.rename(cropped_path, raw_path)
        else:
            print("🔄 [Fallback Smart Crop] لم يتم العثور على مربع محيط من Gemini. جاري تشغيل الاقتصاص الذكي القائم على الأهمية البصرية...")
            apply_saliency_smart_crop(raw_path, raw_path, 800, 800)
            
        # حساب مساحة المربع المحيط بالبكسل لربطه بمعايير التحقق Heuristics
        bbox_area = None
        if box:
            try:
                from PIL import Image
                with Image.open(raw_path) as temp_img:
                    orig_w, orig_h = temp_img.size
                    ymin, xmin, ymax, xmax = box
                    pixel_w = (xmax - xmin) * orig_w / 1000.0
                    pixel_h = (ymax - ymin) * orig_h / 1000.0
                    bbox_area = pixel_w * pixel_h
            except Exception as e:
                print(f"⚠️ خطأ أثناء حساب مساحة المربع المحيط بالبكسل: {e}")
    
        # 3. إزالة الخلفية وتطبيق مسار الإصلاح الذاتي (Self-Healing Failover Path)
        success = remove_background(raw_path, nobg_path)
        
        # التحقق البرمجي التلقائي من جودة قناع الشفافية للمسار الأساسي
        is_valid = success and validate_alpha_matte(nobg_path, bbox_area)
        
        if not is_valid:
            # مسار التراجع (Failover): محاولة التراجع المحلي المجاني باستخدام النموذج الآخر أولاً
            local_failover_method = "bria_rmbg" if config.BG_REMOVAL_METHOD.lower() == "rembg" else "rembg"
            print(f"🔄 [Self-Healing Failover] القناع الأساسي لم يمر بمعايير التحقق. محاولة التراجع المحلي باستخدام نموذج '{local_failover_method}'...")
            
            fallback_nobg_path = os.path.join("temp", f"local_fallback_{safe_name}.webp")
            old_method = config.BG_REMOVAL_METHOD
            config.BG_REMOVAL_METHOD = local_failover_method
            fallback_success = remove_background(raw_path, fallback_nobg_path)
            config.BG_REMOVAL_METHOD = old_method
            
            if fallback_success and validate_alpha_matte(fallback_nobg_path, bbox_area):
                print("✅ [Self-Healing Succeeded] مسار التراجع المحلي نجح واجتاز التحقق البرمجي!")
                nobg_path = fallback_nobg_path
                is_valid = True
                
        if not is_valid:
            # مسار التراجع السحابي المدفوع عبر API كخيار أخير
            print("🔄 [Self-Healing Failover] التراجع المحلي لم ينجح. محاولة التراجع السحابي المدفوع...")
            if config.REMOVE_BG_API_KEY:
                api_nobg_path = os.path.join("temp", f"api_fallback_{safe_name}.webp")
                
                # محاكاة إزالة الخلفية عبر API
                old_method = config.BG_REMOVAL_METHOD
                config.BG_REMOVAL_METHOD = "remove_bg_api"
                api_success = remove_background(raw_path, api_nobg_path)
                config.BG_REMOVAL_METHOD = old_method
                
                if api_success and validate_alpha_matte(api_nobg_path, bbox_area):
                    print("✅ [Self-Healing Succeeded] مسار التراجع السحابي نجح واجتاز التحقق البرمجي!")
                    nobg_path = api_nobg_path
                    is_valid = True
                    
            if not is_valid:
                print("❌ [Self-Healing Failed] فشل مسارات التراجع الشفافة. استخدام الصورة الخام الكاملة لحماية المنتج وتحويلها للمراجعة البشرية.")
                nobg_path = raw_path
    
        # 4. تحسين الحواف وتطبيق ظلال الاستوديو الناعمة والتوسيط
        temp_rgba = os.path.join("temp", f"rgba_{safe_name}.webp")
        final_path = os.path.join("temp", f"final_{safe_name}.webp")
        
        if nobg_path != raw_path:
            try:
                from PIL import Image
                import numpy as np
                from edge_shadow_engine import EdgeShadowEngine
                
                # استخراج قناع الشفافية من الصورة المعزولة الخلفية
                with Image.open(nobg_path) as nobg_img:
                    nobg_rgba = nobg_img.convert("RGBA")
                    alpha_channel = np.array(nobg_rgba.getchannel('A'))
                    
                # تشغيل محرك معالجة الحواف Guided Filter
                success_edge = EdgeShadowEngine.process_mask(raw_path, alpha_channel, temp_rgba)
                
                if success_edge:
                    # تطبيق ظلال الاستوديو المركبة والتوسيط
                    success_shadow = EdgeShadowEngine.apply_studio_shadows(temp_rgba, final_path, target_size=config.IMAGE_TARGET_SIZE)
                    if not success_shadow:
                        final_path = nobg_path
                else:
                    final_path = nobg_path
            except Exception as e:
                print(f"⚠️ خطأ أثناء تطبيق تنعيم الحواف والظلال: {e}")
                final_path = nobg_path
        else:
            # إذا لم يتم عزل الخلفية، نستخدم الصورة الخام مباشرة
            final_path = raw_path
            
        # تنظيف الملفات المؤقتة
        for temp_f in [temp_rgba, raw_path, nobg_path]:
            if os.path.exists(temp_f) and temp_f != final_path:
                try:
                    os.remove(temp_f)
                except Exception:
                    pass
                    
        return final_path
    finally:
        config.BG_REMOVAL_METHOD = old_bg_method


def send_telegram_notification(text):
    """
    إرسال تنبيه فوري عبر بوت Telegram (تم إيقافه بالكامل بناءً على طلب العميل).
    """
    return False
