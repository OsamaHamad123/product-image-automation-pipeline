# main.py
# السكربت الرئيسي لتشغيل وإدارة نظام الأتمتة بالكامل

import os
import sys
import config
print = config.log_runner
import google_sheets
import image_search
import image_processor
import cloudinary_storage
import local_cache_db
import time

def load_run_config():
    """
    تحميل إعدادات التشغيل الجماعي المخصصة من ملف run_config.json إن وجد لتجاوز إعدادات config.py
    """
    config_file = "temp/run_config.json"
    if os.path.exists(config_file):
        try:
            import json
            with open(config_file, "r", encoding="utf-8") as f:
                overrides = json.load(f)
                
            if "ignoreUnitClash" in overrides:
                config.IGNORE_UNIT_CLASH = bool(overrides["ignoreUnitClash"])
            if "strictBrandMatch" in overrides:
                config.STRICT_BRAND_MATCH = bool(overrides["strictBrandMatch"])
            if "aiUpscale" in overrides:
                config.FORCE_UPSCALING = bool(overrides["aiUpscale"])
            if "aiEnhance" in overrides:
                config.ENABLE_IMAGE_ENHANCEMENT = bool(overrides["aiEnhance"])
            if "skipCache" in overrides:
                config.SKIP_LOCAL_CACHE = bool(overrides["skipCache"])
            if "target_width" in overrides:
                w = int(overrides["target_width"])
                h = int(overrides.get("target_height", 0))
                config.IMAGE_TARGET_SIZE = (w, h)
            if "padding_ratio" in overrides:
                config.IMAGE_PADDING_RATIO = float(overrides["padding_ratio"])
            if "bg_color" in overrides:
                config.IMAGE_BG_COLOR = str(overrides["bg_color"]).strip().lstrip('#')
            if "curation_mode" in overrides:
                config.CURATION_MODE = bool(overrides["curation_mode"])
                
            print("⚙️ [Run Config] تم تحميل وتطبيق التجاوزات البرمجية من ملف run_config.json بنجاح!")
        except Exception as e:
            print(f"⚠️ خطأ أثناء تحميل إعدادات run_config.json: {e}")

def save_progress(current, total, success, failed, current_product=""):
    try:
        import json
        os.makedirs("temp", exist_ok=True)
        with open("temp/batch_progress.json", "w", encoding="utf-8") as f:
            json.dump({
                "current": current,
                "total": total,
                "success": success,
                "failed": failed,
                "current_product": current_product
            }, f, ensure_ascii=False)
    except Exception:
        pass

def process_single_product(prod, worksheet, link_column_index):
    """
    معالجة منتج فردي بالكامل وتحديث الشيت وحفظه بالكاش
    """
    row_num = prod["row_number"]
    name = prod["product_name"]
    brand = prod["brand"]
    query = prod["search_query"]
    existing_link = prod.get("existing_image_link", "")

    # تخطي المنتجات التي تحتوي بالفعل على رابط لتوفير استهلاك الـ API والوقت (إلا إذا تم تفعيل فرض الكتابة فوقها)
    if existing_link and not config.FORCE_OVERWRITE_IMAGES:
        print(f"⏭️ تخطي: المنتج يحتوي بالفعل على رابط صورة: {existing_link}")
        return "skipped"
        
    # أ. البحث عن أفضل صورة (مع تقييم الصلة والدقة)
    product_name_ar = prod.get("product_name_ar", "")
    brand_ar = prod.get("brand_ar", "")
    trace = {}
    best_image = image_search.search_best_product_image(
        query, 
        name, 
        brand, 
        product_name_ar=product_name_ar, 
        brand_ar=brand_ar,
        barcode=prod.get("barcode", ""),
        category=prod.get("category", ""),
        origin=prod.get("origin", ""),
        trace=trace
    )
    if not best_image:
        config.log_and_fail(prod.get("barcode"), name, brand, "لم يتم العثور على أي صورة تطابق معايير القبول والجودة البصرية.")
        image_processor.send_telegram_notification("")
        return "failed"
        
    # إذا تم تفعيل وضع الفرز المنسق Curation Mode
    if getattr(config, 'CURATION_MODE', False):
        # استخراج المرشحات الفريدة من الـ trace
        candidates = []
        seen_urls = set()
        
        # إذا تم استرجاع النتيجة من الكاش أو التكرار البصري، ننشئ له كانديديت افتراضي وحيد
        if best_image.get("source") in ["sqlite_cache", "visual_duplicate"]:
            candidates.append({
                "url": best_image["url"],
                "title": best_image.get("title", "صورة مسترجعة من الكاش المحلي"),
                "status": "accepted",
                "width": best_image.get("width", 800),
                "height": best_image.get("height", 800)
            })
        else:
            if trace and 'steps' in trace:
                for step in trace['steps']:
                    if 'candidates' in step:
                        for c in step['candidates']:
                            url = c.get('url')
                            if url and url not in seen_urls:
                                seen_urls.add(url)
                                candidates.append(c)
                                
        # حفظ المرشحات في قاعدة البيانات
        local_cache_db.save_curation_candidates(row_num, name, brand, candidates, best_image["url"])
        
        # تحديث الشيت بالرابط المقترح ومسبوقاً بـ needs_review:
        image_link_for_sheets = f"needs_review:{best_image['url']}"
        update_success = google_sheets.update_image_link(worksheet, row_num, link_column_index, image_link_for_sheets)
        if update_success:
            print(f"📋 [Curation Mode] تم إرسال المنتج '{name}' للمراجعة وحفظ {len(candidates)} مرشحين بصريين بنجاح.")
            return "success"
        else:
            return "failed"

    # تحقق من التخزين المحلي الذكي لتسريع المعالجة (في الوضع العادي للأتمتة بدون فرز)
    if best_image.get("source") == "sqlite_cache":
        print(f"⚡ [Local Cache Hit] استرجاع فوري للمنتج '{name}' من الكاش المحلي!")
        image_link = best_image["url"]
        metadata = best_image.get("metadata")
        if metadata:
            google_sheets.update_product_metadata(worksheet, row_num, metadata)
        image_link_for_sheets = image_link
        update_success = google_sheets.update_image_link(worksheet, row_num, link_column_index, image_link_for_sheets)
        if update_success:
            print(f"🎉 تم تحديث بيانات الصف {row_num} بنجاح من الكاش المحلي!")
            return "success"
        else:
            return "failed"

    if best_image.get("source") == "visual_duplicate":
        print(f"👁️ [BK-Tree Deduplicator] إعادة استخدام رابط Cloudinary للمنتج المكرر بصرياً: {best_image['url']}")
        image_link = best_image["url"]
        metadata = best_image.get("metadata")
        if metadata:
            google_sheets.update_product_metadata(worksheet, row_num, metadata)
        image_link_for_sheets = image_link
        update_success = google_sheets.update_image_link(worksheet, row_num, link_column_index, image_link_for_sheets)
        if update_success:
            print(f"🎉 تم تحديث بيانات الصف {row_num} بنجاح من التكرار البصري!")
            # حفظ في الكاش ليكون ضربة مباشرة المرة القادمة
            try:
                local_cache_db.save_product_resolution(
                    prod.get("barcode", ""),
                    name,
                    brand,
                    best_image.get("url"),
                    image_link,
                    1.0,
                    metadata,
                    None,
                    perceptual_hash=best_image.get("perceptual_hash")
                )
            except Exception as e:
                print(f"⚠️ خطأ أثناء حفظ السجل في الكاش: {e}")
            return "success"
        else:
            return "failed"

    image_url = best_image["url"]
    print(f"🔗 الصورة المختارة للتحميل: {image_url}")
    
    # ب. تحميل ومعالجة الصورة محلياً (إزالة الخلفية والتحجيم بدقة عالية)
    w, h = getattr(config, 'IMAGE_TARGET_SIZE', (800, 800))
    processed_image_path = image_processor.process_product_image(
        image_url, name, brand, 
        enhance=getattr(config, 'ENABLE_IMAGE_ENHANCEMENT', False),
        target_width=w,
        target_height=h
    )
    if not processed_image_path or not os.path.exists(processed_image_path):
        config.log_and_fail(prod.get("barcode"), name, brand, "فشل تحميل الصورة المرشحة أو فشل عزل الخلفية وتنعيم الحواف.")
        image_processor.send_telegram_notification("")
        return "failed"
        
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
            
    # قراءة الأبعاد الفعلية للصورة الناتجة بعد معالجتها وتوسيطها (لتمريرها إلى Cloudinary)
    try:
        from PIL import Image
        with Image.open(processed_image_path) as res_img:
            final_w, final_h = res_img.size
    except Exception:
        final_w = w or 800
        final_h = h or 800
        
    # د. رفع الصورة المعالجة محلياً إلى Cloudinary وتوليد الرابط الآمن بالمجلد والوسوم المستهدفة
    image_link = cloudinary_storage.upload_product_image_to_cloudinary(
        processed_image_path, 
        name, 
        brand,
        folder=folder,
        tags=tags,
        target_width=final_w,
        target_height=final_h,
        padding_ratio=getattr(config, 'IMAGE_PADDING_RATIO', 0.85),
        bg_color=getattr(config, 'IMAGE_BG_COLOR', 'ffffff')
    )
    
    # هـ. تنظيف ملف الصورة المعالجة من المجلد المؤقت
    try:
        if os.path.exists(processed_image_path):
            os.remove(processed_image_path)
    except Exception:
        pass
        
    if not image_link:
        config.log_and_fail(prod.get("barcode"), name, brand, "فشل رفع الصورة المعالجة إلى Cloudinary.")
        return "failed"
        
    # إضافة بادئة المراجعة الرمادية إذا تطلب الأمر لتنبيه لوحة العرض
    if getattr(config, 'CURATION_MODE', False):
        image_link_for_sheets = f"needs_review:{image_link}"
    else:
        image_link_for_sheets = f"needs_review:{image_link}" if best_image.get("needs_review") else image_link

    # ج. تحديث الشيت بالرابط الجديد
    update_success = google_sheets.update_image_link(
        worksheet, 
        row_num, 
        link_column_index, 
        image_link_for_sheets
    )
    
    if update_success:
        print(f"🎉 تم تحديث بيانات الصف {row_num} بنجاح بالرابط الجديد!")
        # حفظ في الكاش المحلي للتسجيل والـ Deduplication
        try:
            local_cache_db.save_product_resolution(
                prod.get("barcode", ""),
                name,
                brand,
                image_url,
                image_link,
                best_image.get("clip_score", 0.0),
                metadata,
                best_image.get("clip_embedding")
            )
        except Exception as e:
            print(f"⚠️ خطأ أثناء حفظ السجل في الكاش: {e}")
            
        image_processor.send_telegram_notification("")
        return "success"
    else:
        print(f"⚠️ فشل كتابة الرابط في الشيت للصف {row_num}")
        image_processor.send_telegram_notification("")
        return "failed"

def run_enqueue_mode():
    """
    قراءة شيت جوجل وتعبئة طابور SQLite بالمهام ثم الخروج
    """
    try:
        load_run_config()
        local_cache_db.clear_queue()
        
        sheets_client = google_sheets.get_sheets_client()
        if not sheets_client:
            print("❌ [Enqueue] فشل الاتصال بـ Google Sheets API.")
            sys.exit(1)
        worksheet = google_sheets.open_worksheet(sheets_client, config.SPREADSHEET_NAME_OR_URL)
        if not worksheet:
            print(f"❌ [Enqueue] لم يتم العثور على الشيت: {config.SPREADSHEET_NAME_OR_URL}")
            sys.exit(1)
            
        products, _ = google_sheets.get_products(worksheet)
        if not products:
            print("⚠️ [Enqueue] لم يتم العثور على أي منتجات صالحة.")
            sys.exit(0)
            
        enqueued_count = 0
        for prod in products:
            row_num = prod["row_number"]
            barcode = prod.get("barcode", "")
            name = prod["product_name"]
            brand = prod["brand"]
            query = prod["search_query"]
            existing_link = prod.get("existing_image_link", "")
            
            # إذا لم يتم فرض الكتابة فوق الصور، نتجاهل الموجودة
            if existing_link and not config.FORCE_OVERWRITE_IMAGES:
                continue
                
            local_cache_db.add_to_queue(row_num, barcode, name, brand, query)
            enqueued_count += 1
            
        print(f"📋 [Enqueue] تم إدخال {enqueued_count} منتج في طابور المعالجة بنجاح.")
        
        # تهيئة إحصائيات مبدئية لكي تراها الواجهة فوراً
        local_cache_db.get_queue_statistics()
    except Exception as e:
        print(f"❌ [Enqueue Error] فشل بناء الطابور: {e}")
        sys.exit(1)

def run_worker_mode():
    """
    تشغيل كمعالج خلفية دائم يسحب المهام من طابور SQLite
    """
    import subprocess
    lock_file = "temp/pipeline.lock" # حفاظاً على التوافق مع ApiController.php
    
    # 1. منع تشغيل أكثر من وركر واحد بالتزامن
    try:
        os.makedirs("temp", exist_ok=True)
        if os.path.exists(lock_file):
            with open(lock_file, "r") as f:
                pid_str = f.read().strip()
            if pid_str.isdigit() and pid_str != "1" and pid_str != "STARTING":
                pid = int(pid_str)
                output = subprocess.check_output(f'tasklist /FI "PID eq {pid}"', shell=True).decode()
                if str(pid) in output:
                    print(f"⚠️ [Worker] معالج الخلفية يعمل بالفعل حالياً (PID: {pid}). خروج.")
                    sys.exit(0)
    except Exception:
        pass
        
    # كتابة معرف العملية الحالي في ملف القفل
    try:
        with open(lock_file, "w") as f:
            f.write(str(os.getpid()))
    except Exception:
        pass
        
    print("=" * 60)
    print("🤖 طابور مهام الأتمتة الممنهج (SQLite Worker) قيد العمل...")
    print("=" * 60)
    
    load_run_config()
    
    # الاتصال بـ Google Sheets لفتح الجلسة
    sheets_client = google_sheets.get_sheets_client()
    if not sheets_client:
        print("❌ [Worker] فشل الاتصال بـ Google Sheets API.")
        return
    worksheet = google_sheets.open_worksheet(sheets_client, config.SPREADSHEET_NAME_OR_URL)
    if not worksheet:
        print(f"❌ [Worker] لم يتم العثور على الشيت: {config.SPREADSHEET_NAME_OR_URL}")
        return
        
    # جلب مؤشر عمود الرابط للمنتج
    _, link_column_index = google_sheets.get_products(worksheet)
    
    google_sheets.init_async_queue(config.CREDENTIALS_FILE, config.SPREADSHEET_NAME_OR_URL)
    
    consecutive_idle_checks = 0
    try:
        while True:
            stats = local_cache_db.get_queue_statistics()
            task = local_cache_db.fetch_next_task()
            
            if not task:
                consecutive_idle_checks += 1
                if consecutive_idle_checks >= 5: # انتظار 5 ثوانٍ فارغة قبل الإغلاق
                    print("🏁 الطابور فارغ. خروج الوركر بأمان وتوفير الموارد.")
                    break
                time.sleep(1)
                continue
                
            consecutive_idle_checks = 0
            task_id = task["id"]
            row_num = task["row_number"]
            barcode = task["barcode"]
            name = task["product_name"]
            brand = task["brand"]
            
            print("\n" + "-" * 50)
            print(f"🔄 [Queue Task] جاري معالجة: [{name}] | صف رقم {row_num}")
            print("-" * 50)
            
            # تحديث ملف التقدم batch_progress.json المتوافق مع لوحة التحكم
            processed = stats["completed"] + stats["failed"]
            save_progress(processed + 1, stats["total"], stats["completed"], stats["failed"], name)
            
            # تشغيل خط الأنابيب للمنتج
            result = process_single_product(task, worksheet, link_column_index)
            
            if result == "success":
                local_cache_db.update_task_status(task_id, "completed")
            elif result == "failed":
                local_cache_db.update_task_status(task_id, "failed", "Verification failed or background removal failure")
            elif result == "skipped":
                local_cache_db.update_task_status(task_id, "completed")
                
    finally:
        google_sheets.stop_async_queue()
        try:
            if os.path.exists(lock_file):
                os.remove(lock_file)
            progress_file = "temp/batch_progress.json"
            if os.path.exists(progress_file):
                os.remove(progress_file)
        except Exception:
            pass

def run_automation_pipeline():
    """
    التشغيل التسلسلي القديم الافتراضي
    """
    lock_file = "temp/pipeline.lock"
    try:
        load_run_config()
        google_sheets.init_async_queue(config.CREDENTIALS_FILE, config.SPREADSHEET_NAME_OR_URL)
        
        try:
            os.makedirs("temp", exist_ok=True)
            with open(lock_file, "w") as f:
                f.write(str(os.getpid()))
        except Exception:
            pass
            
        print("=" * 60)
        print("🤖 نظام معالجة ورفع صور المنتجات التلقائي")
        print("=" * 60)
        
        sheets_client = google_sheets.get_sheets_client()
        if not sheets_client:
            print("❌ فشل الاتصال بـ Google Sheets API.")
            return
            
        worksheet = google_sheets.open_worksheet(sheets_client, config.SPREADSHEET_NAME_OR_URL)
        if not worksheet:
            print(f"❌ لم يتم العثور على ورقة العمل أو فتحها: {config.SPREADSHEET_NAME_OR_URL}")
            return
            
        products, link_column_index = google_sheets.get_products(worksheet)
        if not products:
            print("⚠️ لم يتم العثور على أي منتجات صالحة للمعالجة.")
            return
            
        print(f"📋 تم العثور على {len(products)} منتج جاهز للفحص.")
        
        success_count = 0
        skipped_count = 0
        failed_count = 0
        
        save_progress(0, len(products), 0, 0, "بدء التشغيل...")
        
        for i, prod in enumerate(products, start=1):
            name = prod["product_name"]
            save_progress(i, len(products), success_count, failed_count, name)
            
            print("\n" + "-" * 50)
            print(f"🔄 معالجة المنتج ({i}/{len(products)}): [{name}]")
            print("-" * 50)
            
            result = process_single_product(prod, worksheet, link_column_index)
            if result == "success":
                success_count += 1
            elif result == "failed":
                failed_count += 1
            elif result == "skipped":
                skipped_count += 1
                
        print("\n" + "=" * 60)
        print("🏁 انتهت عملية الأتمتة بنجاح!")
        print(f"📊 الخلاصة:")
        print(f"   ✅ ناجح: {success_count}")
        print(f"   ⏭️ تم تخطيه (يحتوي على رابط): {skipped_count}")
        print(f"   ❌ فشل: {failed_count}")
        print("=" * 60)
    finally:
        google_sheets.stop_async_queue()
        try:
            if os.path.exists(lock_file):
                os.remove(lock_file)
            progress_file = "temp/batch_progress.json"
            if os.path.exists(progress_file):
                os.remove(progress_file)
        except Exception:
            pass

if __name__ == "__main__":
    if "--enqueue" in sys.argv:
        run_enqueue_mode()
    elif "--worker" in sys.argv:
        run_worker_mode()
    else:
        run_automation_pipeline()
