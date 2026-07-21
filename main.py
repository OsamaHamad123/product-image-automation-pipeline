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
    # إعادة تحميل الإعدادات والاعتمادات من قاعدة البيانات لضمان استخدام أحدث المفاتيح
    try:
        config.load_db_config()
    except Exception as e:
        print(f"⚠️ فشل تحديث الإعدادات من قاعدة البيانات: {e}")
        
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
            if "brand_filter" in overrides:
                val = overrides["brand_filter"]
                config.BRAND_FILTER = str(val).strip() if (val is not None and str(val).strip().lower() != "none") else ""
            if "row_filter" in overrides:
                val = overrides["row_filter"]
                config.ROW_FILTER = str(val).strip() if (val is not None and str(val).strip().lower() != "none") else ""
            if "auto_approve_threshold" in overrides:
                try:
                    config.AUTO_APPROVE_THRESHOLD = float(overrides["auto_approve_threshold"])
                except ValueError:
                    config.AUTO_APPROVE_THRESHOLD = 0.0
            if "forceOverwrite" in overrides:
                config.FORCE_OVERWRITE_IMAGES = bool(overrides["forceOverwrite"])
                
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
                                if c.get('status') == 'rejected':
                                    continue
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
        google_sheets.clear_cache()
        
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
            
        # تحليل فلتر الصفوف إن وجد
        allowed_rows = None
        if config.ROW_FILTER:
            allowed_rows = set()
            try:
                parts = config.ROW_FILTER.split(',')
                for part in parts:
                    part = part.strip()
                    if '-' in part:
                        start, end = part.split('-')
                        allowed_rows.update(range(int(start), int(end) + 1))
                    else:
                        allowed_rows.add(int(part))
                print(f"🎯 [Enqueue] تطبيق فلتر الصفوف: {sorted(list(allowed_rows))}")
            except Exception as e:
                print(f"⚠️ [Enqueue] خطأ في صياغة فلتر الصفوف: {e}. سيتم تجاهله.")
                allowed_rows = None

        enqueued_count = 0
        for prod in products:
            row_num = prod["row_number"]
            barcode = prod.get("barcode", "")
            name = prod["product_name"]
            brand = prod["brand"]
            query = prod["search_query"]
            existing_link = prod.get("existing_image_link", "")
            
            # تطبيق فلتر الصفوف
            if allowed_rows is not None and row_num not in allowed_rows:
                continue
                
            # تطبيق فلتر الماركة (Brand)
            if config.BRAND_FILTER and brand:
                if config.BRAND_FILTER.lower() not in brand.lower():
                    continue
            
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

def auto_approve_product(task, best_image, worksheet, link_column_index):
    """
    اعتماد ورفع وتحديث بيانات المنتج تلقائياً سحابياً لتجاوز الفرز البشري
    """
    import image_processor
    import cloudinary_storage
    
    name = task["product_name"]
    brand = task["brand"]
    row_num = task["row_number"]
    image_url = best_image["url"]
    
    print(f"🚀 [Auto-Approve] جاري معالجة ورفع الصورة تلقائياً لـ [{name}] لزيادة التطابق...")
    
    try:
        w, h = getattr(config, 'IMAGE_TARGET_SIZE', (800, 800))
        processed_image_path = image_processor.process_product_image(
            image_url, name, brand, 
            bg_removal_method=getattr(config, 'BG_REMOVAL_METHOD', 'photoroom'),
            target_width=w,
            target_height=h,
            padding_ratio=getattr(config, 'IMAGE_PADDING_RATIO', 0.85),
            bypass_heuristics=True
        )
        if not processed_image_path or not os.path.exists(processed_image_path):
            print(f"❌ [Auto-Approve] فشل معالجة الصورة محلياً لـ [{name}]")
            return False
            
        if getattr(config, 'FORCE_UPSCALING', True):
            try:
                from PIL import Image
                with Image.open(processed_image_path) as img:
                    w_f, h_f = img.size
                    img.resize((w_f*2, h_f*2), Image.Resampling.LANCZOS).save(processed_image_path)
            except Exception:
                pass
                
        # استخراج الميتاداتا
        metadata = image_processor.extract_metadata_from_image(processed_image_path, name, brand)
        folder = "products"
        tags = []
        
        if metadata:
            google_sheets.update_product_metadata(worksheet, row_num, metadata)
            cat1 = (metadata.get("category_l1_en") or "").strip().lower().replace(" ", "_").replace("&", "and")
            cat2 = (metadata.get("category_l2_en") or "").strip().lower().replace(" ", "_").replace("&", "and")
            if cat1:
                if cat2:
                    folder = f"products/{cat1}/{cat2}"
                else:
                    folder = f"products/{cat1}"
            tags_str = metadata.get("tags_en", "")
            if tags_str:
                tags = [t.strip() for t in tags_str.split(",") if t.strip()]
                
        try:
            from PIL import Image
            with Image.open(processed_image_path) as res_img:
                final_w, final_h = res_img.size
        except Exception:
            final_w, final_h = w, h
            
        # الرفع لـ Cloudinary
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
        
        if image_link:
            # تحديث شيت جوجل
            update_success = google_sheets.update_image_link(worksheet, row_num, link_column_index, image_link)
            if update_success:
                # الحفظ في كاش قاعدة البيانات
                local_cache_db.save_product_resolution(
                    task.get("barcode", ""),
                    name,
                    brand,
                    image_url,
                    image_link,
                    best_image.get("clip_score", 1.0),
                    metadata,
                    None,
                    perceptual_hash=best_image.get("perceptual_hash")
                )
                
                # إزالة السجل من قائمة الفشل إن وجد
                local_cache_db.delete_product_failure(task.get("barcode", ""))
                
                # حذف الملف المؤقت المحلي
                try:
                    if os.path.exists(processed_image_path):
                        os.remove(processed_image_path)
                except Exception:
                    pass
                return True
        return False
    except Exception as e:
        print(f"❌ [Auto-Approve Error] حدث خطأ أثناء الاعتماد التلقائي لـ [{name}]: {e}")
        return False

def pre_cache_product_candidates(task, worksheet=None, link_column_index=None):
    """
    البحث المسبق وجلب الصور المرشحة وحفظها كاش لصف المنتج لتسريع الفرز البشري لاحقاً
    أو اعتمادها تلقائياً إذا تجاوزت حد الجودة المطلوب.
    """
    name = task["product_name"]
    brand = task["brand"]
    query = task["search_query"] if task["search_query"] else f"{name} {brand}".strip()
    
    print(f"🕵️ [Pre-Cache] جاري البحث عن مرشحات بصريّة لـ: [{name}]")
    
    trace = {}
    max_retries = 3
    best_image = None
    retry_delay = 2.0
    
    for attempt in range(1, max_retries + 1):
        try:
            best_image = image_search.search_best_product_image(
                query, 
                name, 
                brand, 
                product_name_ar=task.get("product_name_ar", ""), 
                brand_ar=task.get("brand_ar", ""),
                barcode=task.get("barcode", ""),
                category=task.get("category", ""),
                origin=task.get("origin", ""),
                trace=trace,
                skip_cache=True
            )
            if best_image:
                break
        except Exception as ex:
            print(f"⚠️ [Attempt {attempt}/{max_retries}] فشل البحث للمنتج [{name}]: {ex}")
            
        if attempt < max_retries:
            print(f"⏱️ الانتظار لمدة {retry_delay:.1f} ثانية قبل إعادة المحاولة...")
            import time
            time.sleep(retry_delay)
            retry_delay *= 2
    
    candidates = []
    seen_urls = set()
    if best_image:
        clip_score = best_image.get("clip_score", 0.0)
        auto_thresh = getattr(config, 'AUTO_APPROVE_THRESHOLD', 0.0)
        
        # الاعتماد التلقائي في حال تجاوز حد الجودة والصلة
        if auto_thresh > 0.0 and clip_score >= auto_thresh and worksheet is not None and link_column_index is not None:
            success = auto_approve_product(task, best_image, worksheet, link_column_index)
            if success:
                local_cache_db.update_task_status(task["id"], "completed")
                print(f"🎉 [Auto-Approve] تم اعتماد المنتج وتحديث الصف {task['row_number']} تلقائياً لتخطي المراجعة (نسبة المطابقة: {clip_score:.4f})")
                return "success"
                
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
                                if c.get('status') == 'rejected':
                                    continue
                                seen_urls.add(url)
                                candidates.append(c)
        # حفظ المرشحات في قاعدة البيانات
        local_cache_db.save_curation_candidates(task["row_number"], name, brand, candidates, best_image["url"])
        local_cache_db.update_task_status(task["id"], "ready_for_review")
        print(f"✅ [Pre-Cache] تم تجهيز {len(candidates)} صور مرشحة للصف {task['row_number']} وحفظها في الكاش بنجاح.")
        return "success"
    else:
        local_cache_db.update_task_status(task["id"], "failed", "No matching images found during search")
        print(f"❌ [Pre-Cache] لم يتم العثور على أي صورة للمنتج في الصف {task['row_number']}.")
        return "failed"

def run_worker_mode():
    """
    تشغيل كمعالج خلفية دائم يسحب المهام من طابور SQLite ويعالجها بالتوازي
    """
    from concurrent.futures import ThreadPoolExecutor
    import concurrent.futures
    import threading
    import subprocess
    
    lock_file = "temp/pipeline.lock"
    
    # 1. منع تشغيل أكثر من وركر واحد بالتزامن
    try:
        os.makedirs("temp", exist_ok=True)
        if os.path.exists(lock_file):
            with open(lock_file, "r") as f:
                pid_str = f.read().strip()
            if pid_str.isdigit() and pid_str != "1" and pid_str != "STARTING":
                pid = int(pid_str)
                import platform
                is_running = False
                if platform.system().lower() == "windows":
                    output = subprocess.check_output(f'tasklist /FI "PID eq {pid}"', shell=True).decode()
                    if str(pid) in output:
                        is_running = True
                else:
                    try:
                        os.kill(pid, 0)
                        is_running = True
                    except OSError:
                        is_running = False
                        
                if is_running:
                    print(f"⚠️ [Worker] معالج الخلفية يعمل بالفعل حالياً (PID: {pid}). خروج.")
                    sys.exit(0)
    except Exception:
        pass
        
    try:
        with open(lock_file, "w") as f:
            f.write(str(os.getpid()))
    except Exception:
        pass
        
    print("=" * 60)
    print("🤖 طابور مهام الأتمتة الممنهج (Parallel SQLite Worker) قيد العمل...")
    print("=" * 60)
    
    load_run_config()
    
    sheets_client = google_sheets.get_sheets_client()
    if not sheets_client:
        print("❌ [Worker] فشل الاتصال بـ Google Sheets API.")
        return
    worksheet = google_sheets.open_worksheet(sheets_client, config.SPREADSHEET_NAME_OR_URL)
    if not worksheet:
        print(f"❌ [Worker] لم يتم العثور على الشيت: {config.SPREADSHEET_NAME_OR_URL}")
        return
        
    _, link_column_index = google_sheets.get_products(worksheet)
    google_sheets.init_async_queue(config.CREDENTIALS_FILE, config.SPREADSHEET_NAME_OR_URL)
    
    db_lock = threading.Lock()
    
    # تحديث الحالة البدئية للأتمتة في قاعدة البيانات
    stats = local_cache_db.get_queue_statistics()
    local_cache_db.update_automation_state(
        status='pre_caching',
        total=stats['total'],
        processed=stats['completed'] + stats['failed'] + stats['processing'],
        success=stats['completed'],
        failed=stats['failed']
    )
    
    # دالة تنفيذ المهمة الفرعية في الثريد
    def worker_task_runner(t):
        try:
            local_cache_db.update_automation_state(status='pre_caching', current_product=t['product_name'])
            pre_cache_product_candidates(t, worksheet, link_column_index)
            
            st = local_cache_db.get_queue_statistics()
            local_cache_db.update_automation_state(
                status='pre_caching',
                processed=st['completed'] + st['failed'] + st['processing'],
                success=st['completed'],
                failed=st['failed']
            )
        except Exception as e:
            local_cache_db.update_task_status(t["id"], "failed", f"Unexpected worker error: {str(e)}")
            print(f"❌ [Worker Thread Error] فشل معالجة المهمة للصف {t['row_number']}: {e}")
            st = local_cache_db.get_queue_statistics()
            local_cache_db.update_automation_state(
                status='pre_caching',
                processed=st['completed'] + st['failed'] + st['processing'],
                success=st['completed'],
                failed=st['failed']
            )
            
    # تشغيل منفذ ثريد متوازي بعدد 3 ثريدات متزامنة
    max_workers = 3
    active_futures = []
    
    try:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            while True:
                active_futures = [f for f in active_futures if not f.done()]
                
                # التحقق مما إذا كان هناك طلب إيقاف مؤقت نشط
                state = local_cache_db.get_automation_state()
                if state.get("pause_requested") == 1:
                    time.sleep(1)
                    continue
                
                if len(active_futures) < max_workers:
                    with db_lock:
                        task = local_cache_db.fetch_next_task()
                        
                    if task:
                        print(f"🔄 [Queue] تم سحب مهمة جديدة للصف {task['row_number']} وجدولتها بالتوازي...")
                        fut = executor.submit(worker_task_runner, task)
                        active_futures.append(fut)
                        continue
                
                if len(active_futures) == 0:
                    stats = local_cache_db.get_queue_statistics()
                    if stats["pending"] == 0:
                        print("🏁 الطابور فارغ تماماً وكل المهام تم توزيعها. خروج الوركر بأمان وتوفير الموارد.")
                        break
                
                time.sleep(1)
    finally:
        try:
            ready_count = local_cache_db.get_ready_for_review_count()
            if ready_count > 0:
                local_cache_db.update_automation_state(status='curation_pending', current_product="")
            else:
                local_cache_db.update_automation_state(status='idle', current_product="")
        except Exception as e:
            print(f"⚠️ [Worker Finally State Update Error] {e}")
            local_cache_db.update_automation_state(status='idle', current_product="")

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
