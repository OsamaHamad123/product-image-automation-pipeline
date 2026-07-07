# cloudinary_storage.py
# موديول للتعامل مع Cloudinary ورفع ومعالجة الصور سحابياً

import os
import cloudinary
import cloudinary.uploader
import cloudinary.utils
import config

# تهيئة إعدادات Cloudinary
cloudinary.config(
    cloud_name = config.CLOUDINARY_CLOUD_NAME,
    api_key = config.CLOUDINARY_API_KEY,
    api_secret = config.CLOUDINARY_API_SECRET,
    secure = True
)

def upload_product_image_to_cloudinary(local_path, product_name, brand, folder=None, tags=None,
                                         target_width=800, target_height=800, padding_ratio=0.85, bg_color="ffffff"):
    """
    رفع الصورة المعالجة محلياً إلى Cloudinary مع بناء تحويلات ديناميكية فورية للحجم والتوسيط وهوامش الأمان ولون الخلفية سحابياً.
    """
    if not os.path.exists(local_path):
        print(f"❌ خطأ: ملف الصورة المحلي غير موجود في المسار: {local_path}")
        return None

    # تنظيف اسم الملف ليكون كمعرف عام (Public ID) نظيف وخالي من المسافات
    clean_product_name = product_name.replace("/", "-").replace("\\", "-").replace(" ", "_").replace("+", "_")
    clean_brand = brand.replace("/", "-").replace("\\", "-").replace(" ", "_").replace("+", "_")
    public_id = f"{clean_product_name}_{clean_brand}"
    
    # تحديد المجلد المستهدف
    target_folder = folder if folder else "products"
    
    try:
        print(f"📤 جاري رفع الصورة المعالجة محلياً إلى Cloudinary في المجلد '{target_folder}' باسم: '{public_id}'...")
        
        # إعداد خيارات الرفع وتفعيل إزالة الخلفية بالذكاء الاصطناعي سحابياً عند الطلب
        upload_options = {
            "public_id": public_id,
            "folder": target_folder,
            "overwrite": True
        }
        if tags:
            upload_options["tags"] = tags
            
        if config.CLOUDINARY_BG_REMOVAL:
            upload_options["background_removal"] = "cloudinary_ai"
            
        # رفع الصورة المحلية
        response = cloudinary.uploader.upload(local_path, **upload_options)
        
        # إنشاء رابط التحويل الديناميكي المستند لخيارات المستخدم سحابياً
        # حساب أبعاد المنتج الداخلية بناءً على هامش الأمان المطلوب
        inner_w = int(target_width  * padding_ratio)
        inner_h = int(target_height * padding_ratio)
        
        # التحقق برمجياً من شفافية الصورة المحلية لتطبيق الاقتصاص فقط عند وجودها
        has_transparency = False
        try:
            from PIL import Image
            with Image.open(local_path) as img:
                if img.mode in ('RGBA', 'LA') or (img.mode == 'P' and 'transparency' in img.info):
                    has_transparency = True
        except Exception as e:
            print(f"⚠️ خطأ أثناء فحص شفافية الصورة: {e}")

        # تحديد تأثير القص مع السماحية المطلوبة من الإعدادات لمنع تشويه حواف المنتج
        trim_tolerance = getattr(config, "CLOUDINARY_TRIM_TOLERANCE", 5)
        trim_effect = f"trim:{trim_tolerance}" if trim_tolerance else "trim"
        
        transformation = []
        if has_transparency:
            # 1. إزالة أي هوامش شفافة فارغة زائدة حول المنتج بسماحية محددة
            transformation.append({"effect": trim_effect})
            print(f"✨ تم تطبيق الاقتصاص السحابي {trim_effect} لوجود خلفية شفافة في الصورة.")
        else:
            print("⚠️ الصورة لا تحتوي على خلفية شفافة. سيتم تخطي trim سحابياً والاعتماد على اقتصاص Gemini المحلي لحماية حواف المنتج.")
            
        transformation.append(
            # 2. تحجيم المنتج ليتناسب ضمن مساحة الـ inner بالبكسل مع الحفاظ على النسبة (c_fit)
            {"width": inner_w, "height": inner_h, "crop": "fit"}
        )
        
        # 3. توسيط المنتج وإكمال canvas الأبعاد المستهدفة مع لون الخلفية المحدد (c_pad)
        pad_transformation = {
            "width": target_width, 
            "height": target_height, 
            "crop": "pad"
        }
        if bg_color and bg_color.lower() != "transparent":
            clean_bg = bg_color.lstrip('#')
            pad_transformation["background"] = f"rgb:{clean_bg}"
            
        transformation.append(pad_transformation)

        
        # أ. تحسين الألوان والتباين بالذكاء الاصطناعي (e_enhance)
        if config.CLOUDINARY_AI_ENHANCE:
            transformation.append({"effect": "enhance"})
            
        # ب. زيادة حدة الصورة وتقليل التشويش (e_sharpen)
        if config.CLOUDINARY_SHARPEN > 0:
            transformation.append({"effect": f"sharpen:{config.CLOUDINARY_SHARPEN}"})
            
        # ج. التحسين التلقائي للجودة والضغط (q_auto)
        if getattr(config, "CLOUDINARY_QUALITY", None):
            transformation.append({"quality": config.CLOUDINARY_QUALITY})
        elif config.CLOUDINARY_AUTO_QUALITY:
            transformation.append({"quality": "auto"})
            
        # د. التحويل التلقائي لأفضل تنسيق متصفح للويب (f_auto)
        if config.CLOUDINARY_AUTO_FORMAT:
            transformation.append({"fetch_format": "auto"})
            
        # توليد الرابط الآمن مع التحويلات المحددة وإرفاق رقم الإصدار الصحيح لمنع كاش الـ CDN
        transformed_url, options = cloudinary.utils.cloudinary_url(
            response['public_id'],
            secure = True,
            version = response.get('version'),  # هام جداً لتجاوز الكاش والمبكسل القديم
            transformation = transformation
        )
        
        print(f"✅ تم الرفع وتوليد رابط Cloudinary النهائي بنجاح: {transformed_url}")
        config.METRICS["cloudinary_uploads"] += 1
        return transformed_url
        
    except Exception as e:
        print(f"❌ حدث خطأ أثناء الرفع إلى Cloudinary: {e}")
        return None
