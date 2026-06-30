# image_processor.py
# موديول معالجة وتحميل الصور وإزالة الخلفيات وإعادة التحجيم

import os
import requests
from PIL import Image, ImageOps
import config

def download_image(url, save_path):
    """
    تنزيل الصورة من الرابط وحفظها محلياً.
    """
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        r = requests.get(url, headers=headers, timeout=10, stream=True)
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

def remove_background(input_path, output_path):
    """
    إزالة خلفية الصورة بناءً على الطريقة المحددة في الإعدادات.
    """
    method = config.BG_REMOVAL_METHOD.lower()
    
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
        print("⏳ جاري إزالة الخلفية محلياً باستخدام مكتبة 'rembg' (قد يستغرق أول تشغيل بعض الوقت لتنزيل النموذج)...")
        try:
            from rembg import remove
            with open(input_path, 'rb') as i:
                input_data = i.read()
                output_data = remove(input_data)
            with open(output_path, 'wb') as o:
                o.write(output_data)
            print("✅ تم إزالة الخلفية محلياً بنجاح!")
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
    تغيير حجم الصورة بشكل ديناميكي وتوسيطها داخل مساحة بالأبعاد المطلوبة (مثلاً 800×800) مع الحفاظ على التناسب.
    """
    if target_size is None:
        target_size = config.IMAGE_TARGET_SIZE
        
    print(f"⏳ جاري تحجيم وتوسيط الصورة لتصبح بالأبعاد الديناميكية: {target_size[0]}x{target_size[1]}...")
    try:
        # 1. فتح الصورة الأصلية وتحويلها لنظام RGBA لدعم الشفافية
        with Image.open(input_path) as img:
            img = img.convert("RGBA")
            
            # 2. تغيير حجم الصورة مع الحفاظ على التناسب بحيث تلائم الأبعاد المستهدفة (Thumbnail)
            # نستخدم LANCZOS للحصول على أفضل جودة بكسلات ودقة عالية
            img.thumbnail(target_size, Image.Resampling.LANCZOS)
            
            # 3. إنشاء لوحة خلفية جديدة بالحجم المستهدف والألوان المطلوبة (خلفية بيضاء افتراضياً)
            new_img = Image.new("RGBA", target_size, background_color)
            
            # 4. حساب موقع اللصق لتوسيط المنتج في المنتصف تماماً
            x = (target_size[0] - img.width) // 2
            y = (target_size[1] - img.height) // 2
            
            # 5. لصق الصورة مع استخدام الشفافية كقناع (Mask) لمنع ظهور حواف سوداء
            new_img.paste(img, (x, y), mask=img)
            
            # 6. حفظ الصورة النهائية بصيغة PNG للحفاظ على جودة الألوان والشفافية
            new_img.save(output_path, "PNG")
            
        print("✅ تم إعادة تحجيم وتجهيز الصورة بنجاح!")
        return True
        
    except Exception as e:
        print(f"❌ خطأ أثناء إعادة تحجيم الصورة: {e}")
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
        
    # 2. إزالة الخلفية
    if not remove_background(raw_path, nobg_path):
        # في حال الفشل نستخدم الصورة الخام كمصدر للخطوة التالية
        nobg_path = raw_path
        
    # 3. التحجيم الديناميكي والتوسيط
    if not resize_and_pad_image(nobg_path, final_path):
        return None
        
    # تنظيف الملفات المؤقتة غير الضرورية لتوفير المساحة
    try:
        if os.path.exists(raw_path) and raw_path != nobg_path:
            os.remove(raw_path)
        if os.path.exists(nobg_path) and nobg_path != final_path:
            os.remove(nobg_path)
    except Exception:
        pass
        
    return final_path
