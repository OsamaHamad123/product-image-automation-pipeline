# main.py
# السكربت الرئيسي لتشغيل وإدارة نظام الأتمتة بالكامل

import os
import sys
import config
import google_sheets
import image_search
import image_processor
import cloudinary_storage

def run_automation_pipeline():
    """
    تشغيل خط الأنابيب المؤتمت لمعالجة وتحديث صور المنتجات.
    """
    print("=" * 60)
    print("🤖 نظام معالجة ورفع صور المنتجات التلقائي")
    print("=" * 60)
    
    # 1. الاتصال بـ Google Sheets
    sheets_client = google_sheets.get_sheets_client()
    if not sheets_client:
        print("❌ فشل الاتصال بـ Google Sheets API. تأكد من إعداد credentials.json بشكل صحيح.")
        return
        
    worksheet = google_sheets.open_worksheet(sheets_client, config.SPREADSHEET_NAME_OR_URL)
    if not worksheet:
        print(f"❌ لم يتم العثور على ورقة العمل أو فتحها: {config.SPREADSHEET_NAME_OR_URL}")
        return
        
    # 2. قراءة قائمة المنتجات
    products, link_column_index = google_sheets.get_products(worksheet)
    if not products:
        print("⚠️ لم يتم العثور على أي منتجات صالحة للمعالجة.")
        return
        
    print(f"📋 تم العثور على {len(products)} منتج جاهز للفحص.")
    
    success_count = 0
    skipped_count = 0
    failed_count = 0
    
    # 4. معالجة كل منتج على حدة
    for i, prod in enumerate(products, start=1):
        row_num = prod["row_number"]
        name = prod["product_name"]
        brand = prod["brand"]
        query = prod["search_query"]
        existing_link = prod["existing_image_link"]
        
        print("\n" + "-" * 50)
        print(f"🔄 معالجة المنتج ({i}/{len(products)}): [{name}] (البراند: {brand}) | صف رقم {row_num}")
        print("-" * 50)
        
        # تخطي المنتجات التي تحتوي بالفعل على رابط لتوفير استهلاك الـ API والوقت
        if existing_link:
            print(f"⏭️ تخطي: المنتج يحتوي بالفعل على رابط صورة: {existing_link}")
            skipped_count += 1
            continue
            
        # أ. البحث عن أفضل صورة (مع تقييم الصلة والدقة)
        best_image = image_search.search_best_product_image(query, name, brand)
        if not best_image:
            print(f"⚠️ تخطي: لم نعثر على صورة مناسبة للمنتج '{name}'")
            failed_count += 1
            continue
            
        image_url = best_image["url"]
        print(f"🔗 الصورة المختارة للتحميل: {image_url}")
        
        # ب. تحميل ومعالجة الصورة محلياً (إزالة الخلفية والتحجيم بدقة عالية)
        processed_image_path = image_processor.process_product_image(image_url, name, brand)
        if not processed_image_path or not os.path.exists(processed_image_path):
            print(f"⚠️ فشل في تحميل أو معالجة الصورة محلياً للمنتج '{name}'")
            failed_count += 1
            continue
            
        # ج. رفع الصورة المعالجة محلياً إلى Cloudinary وتوليد الرابط الآمن
        image_link = cloudinary_storage.upload_product_image_to_cloudinary(
            processed_image_path, 
            name, 
            brand
        )
        
        # د. تنظيف ملف الصورة المعالجة من المجلد المؤقت
        try:
            if os.path.exists(processed_image_path):
                os.remove(processed_image_path)
        except Exception:
            pass
            
        if not image_link:
            print(f"⚠️ فشل رفع الصورة المعالجة إلى Cloudinary للمنتج '{name}'")
            failed_count += 1
            continue
            
        # ج. تحديث الشيت بالرابط الجديد
        update_success = google_sheets.update_image_link(
            worksheet, 
            row_num, 
            link_column_index, 
            image_link
        )
        
        if update_success:
            print(f"🎉 تم تحديث بيانات الصف {row_num} بنجاح بالرابط الجديد!")
            success_count += 1
        else:
            print(f"⚠️ فشل كتابة الرابط في الشيت للصف {row_num}")
            failed_count += 1
            
    print("\n" + "=" * 60)
    print("🏁 انتهت عملية الأتمتة بنجاح!")
    print(f"📊 الخلاصة:")
    print(f"   ✅ ناجح: {success_count}")
    print(f"   ⏭️ تم تخطيه (يحتوي على رابط): {skipped_count}")
    print(f"   ❌ فشل: {failed_count}")
    print("=" * 60)

if __name__ == "__main__":
    run_automation_pipeline()
