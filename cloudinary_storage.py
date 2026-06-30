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

def upload_product_image_to_cloudinary(local_path, product_name, brand):
    """
    رفع الصورة المعالجة محلياً (المزال خلفيتها ومحجمة بدقة Lanczos) إلى Cloudinary،
    ثم تطبيق تحسينات الألوان والحدة وتجاوز كاش الـ CDN بالنسخة الصحيحة.
    """
    if not os.path.exists(local_path):
        print(f"❌ خطأ: ملف الصورة المحلي غير موجود في المسار: {local_path}")
        return None

    # تنظيف اسم الملف ليكون كمعرف عام (Public ID) نظيف وخالي من المسافات
    clean_product_name = product_name.replace("/", "-").replace("\\", "-").replace(" ", "_")
    clean_brand = brand.replace("/", "-").replace("\\", "-").replace(" ", "_")
    public_id = f"{clean_product_name}+{clean_brand}"
    
    try:
        print(f"📤 جاري رفع الصورة المعالجة محلياً إلى Cloudinary باسم: 'products/{public_id}'...")
        
        # رفع الصورة المحلية
        response = cloudinary.uploader.upload(
            local_path,
            public_id = public_id,
            folder = "products",
            overwrite = True
        )
        
        # إنشاء رابط التحويل الديناميكي
        transformation = []
        
        # أ. تحسين الألوان والتباين بالذكاء الاصطناعي (e_enhance)
        if config.CLOUDINARY_AI_ENHANCE:
            transformation.append({"effect": "enhance"})
            
        # ب. زيادة حدة الصورة وتقليل التشويش (e_sharpen)
        if config.CLOUDINARY_SHARPEN > 0:
            transformation.append({"effect": f"sharpen:{config.CLOUDINARY_SHARPEN}"})
            
        # ج. التحسين التلقائي للجودة والضغط (q_auto)
        if config.CLOUDINARY_AUTO_QUALITY:
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
        return transformed_url
        
    except Exception as e:
        print(f"❌ حدث خطأ أثناء الرفع إلى Cloudinary: {e}")
        return None
