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
        
        # تخطي المنتجات التي تحتوي بالفعل على رابط لتوفير استهلاك الـ API والوقت (إلا إذا تم تفعيل فرض الكتابة فوقها)
        if existing_link and not config.FORCE_OVERWRITE_IMAGES:
            print(f"⏭️ تخطي: المنتج يحتوي بالفعل على رابط صورة: {existing_link}")
            skipped_count += 1
            continue
            
        # أ. البحث عن أفضل صورة (مع تقييم الصلة والدقة)
        product_name_ar = prod.get("product_name_ar", "")
        brand_ar = prod.get("brand_ar", "")
        best_image = image_search.search_best_product_image(
            query, 
            name, 
            brand, 
            product_name_ar=product_name_ar, 
            brand_ar=brand_ar,
            barcode=prod.get("barcode", ""),
            category=prod.get("category", ""),
            origin=prod.get("origin", "")
        )
        if not best_image:
            print(f"⚠️ تخطي: لم نعثر على صورة مناسبة للمنتج '{name}'")
            failed_count += 1
            # إشعار Telegram بالفشل
            msg = (
                f"<b>⚠️ فشل أتمتة منتج!</b>\n\n"
                f"📦 <b>المنتج:</b> {name}\n"
                f"🏷️ <b>الماركة:</b> {brand}\n"
                f"❌ <b>السبب:</b> لم يتم العثور على أي صورة تطابق معايير القبول والجودة البصرية."
            )
            image_processor.send_telegram_notification(msg)
            continue
            
        image_url = best_image["url"]
        print(f"🔗 الصورة المختارة للتحميل: {image_url}")
        
        # ب. تحميل ومعالجة الصورة محلياً (إزالة الخلفية والتحجيم بدقة عالية)
        processed_image_path = image_processor.process_product_image(image_url, name, brand)
        if not processed_image_path or not os.path.exists(processed_image_path):
            print(f"⚠️ فشل في تحميل أو معالجة الصورة محلياً للمنتج '{name}'")
            failed_count += 1
            # إشعار Telegram بالفشل
            msg = (
                f"<b>⚠️ فشل أتمتة منتج!</b>\n\n"
                f"📦 <b>المنتج:</b> {name}\n"
                f"🏷️ <b>الماركة:</b> {brand}\n"
                f"❌ <b>السبب:</b> فشل تحميل الصورة المرشحة أو فشل عزل الخلفية وتنعيم الحواف."
            )
            image_processor.send_telegram_notification(msg)
            continue
            
        # ج. استخراج البيانات الوصفية (القيم الغذائية والمكونات والوصف التسويقي والتصنيفات) أولاً لتنظيم المجلدات سحابياً
        metadata = image_processor.extract_metadata_from_image(processed_image_path, name, brand)
        
        folder = "products"
        tags = []
        if metadata:
            google_sheets.update_product_metadata(worksheet, row_num, metadata)
            
            # بناء هيكلية المجلدات بناءً على تصنيف الويب المستخرج
            cat1 = metadata.get("category_l1_en", "").strip().lower().replace(" ", "_").replace("&", "and")
            cat2 = metadata.get("category_l2_en", "").strip().lower().replace(" ", "_").replace("&", "and")
            if cat1:
                if cat2:
                    folder = f"products/{cat1}/{cat2}"
                else:
                    folder = f"products/{cat1}"
                    
            # تحويل الوسوم لقائمة
            tags_str = metadata.get("tags_en", "")
            if tags_str:
                tags = [t.strip() for t in tags_str.split(",") if t.strip()]
                
        # د. رفع الصورة المعالجة محلياً إلى Cloudinary وتوليد الرابط الآمن بالمجلد والوسوم المستهدفة
        image_link = cloudinary_storage.upload_product_image_to_cloudinary(
            processed_image_path, 
            name, 
            brand,
            folder=folder,
            tags=tags
        )
        
        # هـ. تنظيف ملف الصورة المعالجة من المجلد المؤقت
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
            # إشعار Telegram بالنجاح
            msg = (
                f"<b>🎉 تم أتمتة منتج جديد بنجاح!</b>\n\n"
                f"📦 <b>المنتج:</b> {name}\n"
                f"🏷️ <b>الماركة:</b> {brand}\n"
                f"📂 <b>المجلد:</b> <code>{folder}</code>\n"
                f"🏷️ <b>الوسوم:</b> {metadata.get('tags_ar', '') if metadata else ''}\n"
                f"🔗 <a href='{image_link}'>رابط الصورة النهائي</a>"
            )
            image_processor.send_telegram_notification(msg)
        else:
            print(f"⚠️ فشل كتابة الرابط في الشيت للصف {row_num}")
            failed_count += 1
            # إشعار Telegram بالفشل
            msg = (
                f"<b>⚠️ فشل أتمتة منتج!</b>\n\n"
                f"📦 <b>المنتج:</b> {name}\n"
                f"🏷️ <b>الماركة:</b> {brand}\n"
                f"❌ <b>السبب:</b> فشل كتابة وتحديث الرابط الجديد داخل ورقة Google Sheets."
            )
            image_processor.send_telegram_notification(msg)
            
    print("\n" + "=" * 60)
    print("🏁 انتهت عملية الأتمتة بنجاح!")
    print(f"📊 الخلاصة:")
    print(f"   ✅ ناجح: {success_count}")
    print(f"   ⏭️ تم تخطيه (يحتوي على رابط): {skipped_count}")
    print(f"   ❌ فشل: {failed_count}")
    print("=" * 60)

if __name__ == "__main__":
    run_automation_pipeline()
