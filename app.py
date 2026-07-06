# app.py
from flask import Flask, request, jsonify, render_template_string
import os
import sys
import image_search
import image_processor
import cloudinary_storage
import google_sheets
import config
print = config.log_runner
import requests
import categories

app = Flask(__name__)

# Premium, modern dashboard with spreadsheet integration and manual override curation console
# Headless API backend. No HTML template required.

@app.route('/')
def home():
    return jsonify({
        'status': 'online',
        'service': 'Product Image Automation Backend API Microservice',
        'version': '5.0'
    })

import threading

def upscale_image(image_path, scale=2):
    """
    تحسين دقة وأبعاد الصورة باستخدام نموذج EDSR (Super-Resolution) بالذكاء الاصطناعي.
    """
    try:
        from super_image import EdsrModel, ImageLoader
        import torch
        from PIL import Image
        import time

        t0 = time.time()
        print(f"🌀 [AI Upscaler] البدء في زيادة أبعاد الصورة بمعدل {scale}x: {image_path}")
        
        img = Image.open(image_path).convert('RGB')
        
        # تحميل أوزان النموذج محلياً (تحميل خفيف وسريع على CPU)
        model = EdsrModel.from_pretrained('eugenesiow/edsr-base', scale=scale)
        inputs = ImageLoader.load_image(img)
        
        with torch.no_grad():
            preds = model(inputs)
            
        # حفظ الصورة الفائقة بدلاً من النسخة القديمة
        ImageLoader.save_image(preds, image_path)
        print(f"✨ [AI Upscaler] تم التكبير بنجاح في: {time.time() - t0:.2f} ثانية.")
        return True
    except Exception as e:
        print(f"⚠️ [AI Upscaler Warning] فشل تكبير الصورة: {e}")
        return False

def async_process_webhook_product(row_number, product_name, brand, product_name_ar="", brand_ar="", barcode="", category="", origin=""):
    """
    معالجة المنتج المستلم من Webhook بشكل غير متزامن في الخلفية لمنع حظر الطلب.
    """
    print(f"🧵 [Webhook Thread] بدء المعالجة للمنتج: '{product_name}' (صف {row_number})")
    try:
        sheets_client = google_sheets.get_sheets_client()
        worksheet = google_sheets.open_worksheet(sheets_client, config.SPREADSHEET_NAME_OR_URL)
        _, link_column_index = google_sheets.get_products(worksheet)
        
        # أ. البحث عن أفضل صورة
        query = f"{brand} {product_name}"
        best_image = image_search.search_best_product_image(
            query, 
            product_name, 
            brand, 
            product_name_ar=product_name_ar, 
            brand_ar=brand_ar,
            barcode=barcode,
            category=category,
            origin=origin
        )
        
        if not best_image:
            config.log_and_fail(barcode, product_name, brand, "لم يتم العثور على أي صورة تطابق معايير القبول والجودة البصرية.")
            msg = (
                f"<b>⚠️ فشل أتمتة منتج من الشيت (تلقائي)!</b>\n\n"
                f"📦 <b>المنتج:</b> {product_name}\n"
                f"🏷️ <b>الماركة:</b> {brand}\n"
                f"❌ <b>السبب:</b> لم يتم العثور على أي صورة تطابق معايير القبول والجودة البصرية."
            )
            image_processor.send_telegram_notification(msg)
            config.METRICS["failed_runs"] += 1
            return
            
        # تحقق من الكاش المحلي لتسريع المعالجة وتفادي التنزيل والرفع غير الضروري
        if best_image.get("source") == "sqlite_cache":
            print(f"⚡ [Webhook SQLite Cache Hit] استرجاع فوري لمنتج الويب هوك '{product_name}'")
            image_link = best_image["url"]
            metadata = best_image.get("metadata")
            if metadata:
                google_sheets.update_product_metadata(worksheet, row_number, metadata)
            update_success = google_sheets.update_image_link(worksheet, row_number, link_column_index, image_link)
            if update_success:
                msg = (
                    f"<b>🎉 تم أتمتة منتج الويب هوك بنجاح (من الكاش المحلي)!</b>\n\n"
                    f"📦 <b>المنتج:</b> {product_name}\n"
                    f"🏷️ <b>الماركة:</b> {brand}\n"
                    f"🔗 <a href='{image_link}'>رابط الصورة النهائي</a>"
                )
                image_processor.send_telegram_notification(msg)
                config.METRICS["successful_runs"] += 1
            else:
                config.METRICS["failed_runs"] += 1
            return

        image_url = best_image["url"]
        
        # ب. معالجة وتجميل الصورة محلياً
        processed_image_path = image_processor.process_product_image(image_url, product_name, brand)
        if not processed_image_path or not os.path.exists(processed_image_path):
            config.log_and_fail(barcode, product_name, brand, "فشل تحميل الصورة المرشحة أو فشل عزل الخلفية وتنعيم الحواف.")
            msg = (
                f"<b>⚠️ فشل أتمتة منتج من الشيت (تلقائي)!</b>\n\n"
                f"📦 <b>المنتج:</b> {product_name}\n"
                f"🏷️ <b>الماركة:</b> {brand}\n"
                f"❌ <b>السبب:</b> فشل تحميل الصورة المرشحة أو فشل عزل الخلفية وتنعيم الحواف."
            )
            image_processor.send_telegram_notification(msg)
            config.METRICS["failed_runs"] += 1
            return
            
        # تحسين جودة الصورة وتكبيرها بالذكاء الاصطناعي (تلقائياً للويب هوك)
        upscale_image(processed_image_path, scale=2)
            
        # ج. استخراج البيانات الوصفية (القيم الغذائية والمكونات والتصنيفات) أولاً لتنظيم المجلدات سحابياً
        metadata = image_processor.extract_metadata_from_image(processed_image_path, product_name, brand)
        
        folder = "products"
        tags = []
        
        if metadata:
            google_sheets.update_product_metadata(worksheet, row_number, metadata)
            cat1 = metadata.get("category_l1_en", "").strip().lower().replace(" ", "_").replace("&", "and")
            cat2 = metadata.get("category_l2_en", "").strip().lower().replace(" ", "_").replace("&", "and")
            if cat1:
                if cat2:
                    folder = f"products/{cat1}/{cat2}"
                else:
                    folder = f"products/{cat1}"
            tags_str = metadata.get("tags_en", "")
            if tags_str:
                tags = [t.strip() for t in tags_str.split(",") if t.strip()]
                
        # د. رفع الصورة المعالجة محلياً إلى Cloudinary وتوليد الرابط الآمن بالمجلد والوسوم المستهدفة
        image_link = cloudinary_storage.upload_product_image_to_cloudinary(
            processed_image_path, 
            product_name, 
            brand,
            folder=folder,
            tags=tags
        )
        
        # هـ. تنظيف ملف الصورة المعالجة
        try:
            if os.path.exists(processed_image_path):
                os.remove(processed_image_path)
        except Exception:
            pass
            
        if not image_link:
            config.log_and_fail(barcode, product_name, brand, "فشل رفع الصورة المعالجة إلى Cloudinary.")
            msg = (
                f"<b>⚠️ فشل أتمتة منتج من الشيت (تلقائي)!</b>\n\n"
                f"📦 <b>المنتج:</b> {product_name}\n"
                f"🏷️ <b>الماركة:</b> {brand}\n"
                f"❌ <b>السبب:</b> فشل رفع الصورة المعالجة إلى Cloudinary."
            )
            image_processor.send_telegram_notification(msg)
            config.METRICS["failed_runs"] += 1
            return
            
        # إضافة بادئة المراجعة الرمادية إذا تطلب الأمر لتنبيه لوحة العرض
        image_link_for_sheets = f"needs_review:{image_link}" if best_image.get("needs_review") else image_link

        # و. تحديث الشيت بالرابط الجديد
        update_success = google_sheets.update_image_link(
            worksheet, 
            row_number, 
            link_column_index, 
            image_link_for_sheets
        )
        
        if update_success:
            # حفظ في الكاش المحلي
            try:
                import local_cache_db
                local_cache_db.save_product_resolution(
                    barcode,
                    product_name,
                    brand,
                    image_url,
                    image_link,
                    best_image.get("clip_score", 0.0),
                    metadata,
                    best_image.get("clip_embedding")
                )
            except Exception as e:
                print(f"⚠️ خطأ أثناء حفظ السجل في الكاش: {e}")
                
            msg = (
                f"<b>🎉 تم أتمتة منتج جديد من الشيت (تلقائياً)!</b>\n\n"
                f"📦 <b>المنتج:</b> {product_name}\n"
                f"🏷️ <b>الماركة:</b> {brand}\n"
                f"📂 <b>المجلد:</b> <code>{folder}</code>\n"
                f"🏷️ <b>الوسوم:</b> {metadata.get('tags_ar', '') if metadata else ''}\n"
                f"🔗 <a href='{image_link}'>رابط الصورة النهائي</a>"
            )
            image_processor.send_telegram_notification(msg)
            config.METRICS["successful_runs"] += 1
        else:
            msg = (
                f"<b>⚠️ فشل أتمتة منتج من الشيت (تلقائي)!</b>\n\n"
                f"📦 <b>المنتج:</b> {product_name}\n"
                f"🏷️ <b>الماركة:</b> {brand}\n"
                f"❌ <b>السبب:</b> فشل كتابة الرابط النهائي داخل ورقة Google Sheets."
            )
            image_processor.send_telegram_notification(msg)
            config.METRICS["failed_runs"] += 1
            
    except Exception as e:
        print(f"❌ [Webhook Thread Error] {e}")
        msg = (
            f"<b>⚠️ خطأ غير متوقع أثناء معالجة المنتج!</b>\n\n"
            f"📦 <b>المنتج:</b> {product_name}\n"
            f"❌ <b>الخطأ:</b> {str(e)}"
        )
        image_processor.send_telegram_notification(msg)
        config.METRICS["failed_runs"] += 1

@app.route('/api/webhook/sheets', methods=['POST'])
def sheets_webhook():
    """
    استقبال التنبيهات اللحظية عند إضافة أو تعديل منتج في Google Sheets
    """
    data = request.json
    if not data:
        return jsonify({"error": "Missing JSON payload"}), 400
        
    row_number = data.get("row_number")
    product_name = data.get("product_name")
    brand = data.get("brand")
    product_name_ar = data.get("product_name_ar", "")
    brand_ar = data.get("brand_ar", "")
    barcode = data.get("barcode", "")
    category = data.get("category", "")
    origin = data.get("origin", "")
    
    if not row_number or not product_name or not brand:
        return jsonify({"error": "Missing required fields (row_number, product_name, brand)"}), 400
        
    # تشغيل خط الأنابيب في خيط منفصل (Thread) لعدم تجميد طلب الويب هوك
    thread = threading.Thread(
        target=async_process_webhook_product,
        args=(row_number, product_name, brand, product_name_ar, brand_ar, barcode, category, origin)
    )
    thread.daemon = True
    thread.start()
    
    return jsonify({
        "status": "queued",
        "message": "Product processing has been queued in the background."
    }), 200

BATCH_PROGRESS = {
    "is_running": False,
    "total": 0,
    "current": 0,
    "current_product": "",
    "success": 0,
    "failed": 0
}

def run_all_automation_thread():
    """
    تشغيل الأتمتة الكاملة لكافة المنتجات غير المكتملة في الشيت بالخلفية.
    """
    global BATCH_PROGRESS
    print("🧵 [Batch Thread] بدء الأتمتة الكاملة لكافة منتجات الشيت المفقودة...")
    try:
        sheets_client = google_sheets.get_sheets_client()
        if not sheets_client:
            print("❌ [Batch Thread] فشل الاتصال بـ Google Sheets API")
            BATCH_PROGRESS["is_running"] = False
            return
            
        worksheet = google_sheets.open_worksheet(sheets_client, config.SPREADSHEET_NAME_OR_URL)
        if not worksheet:
            print("❌ [Batch Thread] فشل فتح الشيت")
            BATCH_PROGRESS["is_running"] = False
            return
            
        products, link_column_index = google_sheets.get_products(worksheet)
        missing_products = [p for p in products if not p["existing_image_link"]]
        
        print(f"🧵 [Batch Thread] تم العثور على {len(missing_products)} منتج بحاجة لأتمتة الصور من أصل {len(products)}.")
        
        BATCH_PROGRESS.update({
            "is_running": True,
            "total": len(missing_products),
            "current": 0,
            "current_product": "",
            "success": 0,
            "failed": 0
        })
        
        success_count = 0
        failed_count = 0
        
        for idx, prod in enumerate(missing_products, start=1):
            row_num = prod["row_number"]
            name = prod["product_name"]
            brand = prod["brand"]
            query = prod["search_query"]
            product_name_ar = prod.get("product_name_ar", "")
            brand_ar = prod.get("brand_ar", "")
            
            BATCH_PROGRESS["current"] = idx
            BATCH_PROGRESS["current_product"] = name
            
            print(f"🔄 [Batch Thread] ({idx}/{len(missing_products)}) جاري معالجة: '{name}' | صف {row_num}")
            
            # أ. البحث عن أفضل صورة
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
                config.log_and_fail(prod.get("barcode", ""), name, brand, "لم يتم العثور على أي صورة تطابق معايير القبول والجودة البصرية.")
                failed_count += 1
                msg = (
                    f"<b>⚠️ فشل أتمتة منتج من الشيت (Batch)!</b>\n\n"
                    f"📦 <b>المنتج:</b> {name}\n"
                    f"🏷️ <b>الماركة:</b> {brand}\n"
                    f"❌ <b>السبب:</b> لم يتم العثور على أي صورة تطابق معايير القبول والجودة البصرية."
                )
                image_processor.send_telegram_notification(msg)
                config.METRICS["failed_runs"] += 1
                continue
                
            # تحقق من التخزين المحلي SQLite للتسريع والتفادي
            if best_image.get("source") == "sqlite_cache":
                print(f"⚡ [Batch SQLite Cache Hit] استرجاع فوري للمنتج '{name}'")
                image_link = best_image["url"]
                metadata = best_image.get("metadata")
                if metadata:
                    google_sheets.update_product_metadata(worksheet, row_num, metadata)
                update_success = google_sheets.update_image_link(worksheet, row_num, link_column_index, image_link)
                if update_success:
                    success_count += 1
                    BATCH_PROGRESS["success"] += 1
                    msg = (
                        f"<b>🎉 تم أتمتة منتج جديد بنجاح (Batch Cache Hit)!</b>\n\n"
                        f"📦 <b>المنتج:</b> {name}\n"
                        f"🏷️ <b>الماركة:</b> {brand}\n"
                        f"🔗 <a href='{image_link}'>رابط الصورة النهائي</a>"
                    )
                    image_processor.send_telegram_notification(msg)
                    config.METRICS["successful_runs"] += 1
                else:
                    failed_count += 1
                    BATCH_PROGRESS["failed"] += 1
                    config.METRICS["failed_runs"] += 1
                continue

            image_url = best_image["url"]
            
            # ب. تحميل ومعالجة الصورة محلياً
            processed_image_path = image_processor.process_product_image(image_url, name, brand)
            if not processed_image_path or not os.path.exists(processed_image_path):
                config.log_and_fail(prod.get("barcode", ""), name, brand, "فشل تحميل الصورة المرشحة أو فشل عزل الخلفية وتنعيم الحواف.")
                failed_count += 1
                msg = (
                    f"<b>⚠️ فشل أتمتة منتج من الشيت (Batch)!</b>\n\n"
                    f"📦 <b>المنتج:</b> {name}\n"
                    f"🏷️ <b>الماركة:</b> {brand}\n"
                    f"❌ <b>السبب:</b> فشل تحميل الصورة أو عزل الخلفية."
                )
                image_processor.send_telegram_notification(msg)
                config.METRICS["failed_runs"] += 1
                continue
                
            # ج. استخراج البيانات الوصفية
            metadata = image_processor.extract_metadata_from_image(processed_image_path, name, brand)
            folder = "products"
            tags = []
            if metadata:
                google_sheets.update_product_metadata(worksheet, row_num, metadata)
                
                cat1 = metadata.get("category_l1_en", "").strip().lower().replace(" ", "_").replace("&", "and")
                cat2 = metadata.get("category_l2_en", "").strip().lower().replace(" ", "_").replace("&", "and")
                if cat1:
                    if cat2:
                        folder = f"products/{cat1}/{cat2}"
                    else:
                        folder = f"products/{cat1}"
                        
                tags_str = metadata.get("tags_en", "")
                if tags_str:
                    tags = [t.strip() for t in tags_str.split(",") if t.strip()]
                    
            # د. الرفع لكلويديناري
            image_link = cloudinary_storage.upload_product_image_to_cloudinary(
                processed_image_path, 
                name, 
                brand,
                folder=folder,
                tags=tags
            )
            
            if not image_link:
                config.log_and_fail(prod.get("barcode", ""), name, brand, "فشل رفع الصورة المعالجة إلى Cloudinary.")
                failed_count += 1
                BATCH_PROGRESS["failed"] += 1
                config.METRICS["failed_runs"] += 1
                continue
                
            # إضافة بادئة المراجعة الرمادية إذا تطلب الأمر لتنبيه لوحة العرض
            image_link_for_sheets = f"needs_review:{image_link}" if best_image.get("needs_review") else image_link

            # هـ. تحديث الشيت
            update_success = google_sheets.update_image_link(
                worksheet, 
                row_num, 
                link_column_index, 
                image_link_for_sheets
            )
            
            # تنظيف الصورة المؤقتة
            try:
                if os.path.exists(processed_image_path):
                    os.remove(processed_image_path)
            except Exception:
                pass
                
            if update_success:
                success_count += 1
                BATCH_PROGRESS["success"] += 1
                
                # حفظ في الكاش المحلي
                try:
                    import local_cache_db
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
                    
                msg = (
                    f"<b>🎉 تم أتمتة منتج جديد بنجاح (Batch)!</b>\n\n"
                    f"📦 <b>المنتج:</b> {name}\n"
                    f"🏷️ <b>الماركة:</b> {brand}\n"
                    f"📂 <b>المجلد:</b> <code>{folder}</code>\n"
                    f"🔗 <a href='{image_link}'>رابط الصورة النهائي</a>"
                )
                image_processor.send_telegram_notification(msg)
                config.METRICS["successful_runs"] += 1
            else:
                failed_count += 1
                BATCH_PROGRESS["failed"] += 1
                config.METRICS["failed_runs"] += 1
                
        print(f"🏁 [Batch Thread] اكتملت عملية الأتمتة الكلية. نجاح: {success_count} | فشل: {failed_count}")
        BATCH_PROGRESS["is_running"] = False
        
    except Exception as e:
        print(f"❌ [Batch Thread Error] {e}")
        BATCH_PROGRESS["is_running"] = False

@app.route('/api/batch_status', methods=['GET'])
def api_batch_status():
    """
    إرجاع حالة تقدم تشغيل الأتمتة في الخلفية
    """
    return jsonify(BATCH_PROGRESS)

@app.route('/api/run_all', methods=['POST'])
def api_run_all():
    """
    بدء الأتمتة الكاملة لكافة المنتجات المتبقية في الشيت في الخلفية.
    """
    global BATCH_PROGRESS
    BATCH_PROGRESS["is_running"] = True
    thread = threading.Thread(target=run_all_automation_thread)
    thread.daemon = True
    thread.start()
    return jsonify({
        "status": "success",
        "message": "Full automation run started in the background."
    })

@app.route('/api/metrics', methods=['GET'])
def api_metrics():
    """
    إرجاع إحصائيات استهلاك الـ API والتشغيل الحالي
    """
    return jsonify(config.METRICS)

@app.route('/api/products', methods=['GET'])
def api_products():
    """
    جلب كافة المنتجات من Google Sheets مع دمج سجلات الأخطاء التقنية من SQLite.
    """
    try:
        sheets_client = google_sheets.get_sheets_client()
        if not sheets_client:
            return jsonify({'error': 'Google Sheets API connection failed'}), 500
            
        worksheet = google_sheets.open_worksheet(sheets_client, config.SPREADSHEET_NAME_OR_URL)
        if not worksheet:
            return jsonify({'error': 'Sheet not found'}), 404
            
        products, _ = google_sheets.get_products(worksheet)
        
        # جلب قائمة الأخطاء التقنية المدمجة من SQLite
        import local_cache_db
        failures = local_cache_db.get_product_failures()
        
        # دمج الأخطاء مع المنتجات المعادة
        for prod in products:
            barcode = prod.get("barcode", "").strip() if prod.get("barcode") else ""
            # خطة بديلة للباركود إن لم يوجد
            alt_barcode = f"ERR_{prod.get('product_name')}_{prod.get('brand')}".replace(" ", "_")
            
            if barcode and barcode in failures:
                prod["has_error"] = True
                prod["error_message"] = failures[barcode]["error_message"]
            elif alt_barcode in failures:
                prod["has_error"] = True
                prod["error_message"] = failures[alt_barcode]["error_message"]
            else:
                prod["has_error"] = False
                prod["error_message"] = ""
                
        return jsonify({
            'status': 'success',
            'products': products
        })
    except Exception as e:
        print(f"[Flask API Error] Failed to read products: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/search', methods=['POST'])
def api_search():
    data = request.json or {}
    product_name = (data.get('product_name') or '').strip()
    brand = (data.get('brand') or '').strip()
    product_name_ar = (data.get('product_name_ar') or '').strip()
    brand_ar = (data.get('brand_ar') or '').strip()
    custom_query = (data.get('custom_query') or '').strip()
    ignore_unit_clash = bool(data.get('ignore_unit_clash', False))
    strict_brand_match = data.get('strict_brand_match')
    if strict_brand_match is not None:
        strict_brand_match = bool(strict_brand_match)
    
    barcode = (data.get('barcode') or '').strip()
    category = (data.get('category') or '').strip()
    origin = (data.get('origin') or '').strip()
    
    if not product_name:
        return jsonify({'error': 'Product name is required'}), 400
        
    # جلب مرادفات البراندات لجميع عمليات البحث لتوفيرها لخطوات التحقق والفرز الذكي
    brand_mappings = {}
    try:
        sheets_client = google_sheets.get_sheets_client()
        if sheets_client:
            brand_mappings = google_sheets.get_brand_mappings(sheets_client, config.SPREADSHEET_NAME_OR_URL)
    except Exception as e:
        print(f"⚠️ فشل جلب مرادفات البراندات في API: {e}")
        
    # استخراج اسم البراند تلقائياً إذا كان الحقل فارغاً
    if product_name and not brand:
        extracted = google_sheets.extract_brand_from_name(product_name, brand_mappings)
        if extracted:
            brand = extracted
            print(f"💡 [API Auto Brand] تم استخراج البراند '{brand}' تلقائياً لـ '{product_name}' من المرادفات.")
        else:
            extracted = google_sheets.extract_brand_from_start(product_name, brand_mappings)
            if extracted:
                brand = extracted
                print(f"💡 [API Auto Brand] تم استخراج البراند '{brand}' تلقائياً لـ '{product_name}' من بداية الاسم.")
            else:
                extracted = google_sheets.extract_brand_via_gemini(product_name)
                if extracted:
                    brand = extracted
                    print(f"💡 [API Auto Brand] تم استخراج البراند '{brand}' تلقائياً لـ '{product_name}' عبر Gemini.")
                
    search_query = custom_query if custom_query else (f"{product_name} {brand}".strip() if brand else product_name)
    
    trace = {}
    print(f"\n[Flask API] Localized Search query: '{search_query}' for Product: '{product_name}', Brand: '{brand}'")
    
    best_image = image_search.search_best_product_image(
        search_query, product_name, brand, 
        product_name_ar=product_name_ar, brand_ar=brand_ar,
        trace=trace, strict_brand_match=strict_brand_match,
        barcode=barcode, category=category, origin=origin,
        brand_mappings=brand_mappings
    )
    
    if best_image:
        return jsonify({
            'status': 'success',
            'selected_image': best_image,
            'trace': trace,
            'brand': brand
        })
    else:
        return jsonify({
            'status': 'failed',
            'trace': trace,
            'brand': brand
        })

@app.route('/api/select_image', methods=['POST'])
def api_select_image():
    """
    الموافقة اليدوية (Override) وتحميل وتعديل الصورة ورفعها لـ Cloudinary ثم تحديث Google Sheet
    """
    data = request.json or {}
    image_url = (data.get('image_url') or '').strip()
    product_name = (data.get('product_name') or '').strip()
    brand = (data.get('brand') or '').strip()
    row_number = data.get('row_number')
    # معايير التحجيم والتوسيط الديناميكية من اختيار المستخدم
    target_width  = int(data.get('target_width',  800))
    target_height = int(data.get('target_height', 800))
    padding_ratio = float(data.get('padding_ratio', 0.85))
    bg_color      = (data.get('bg_color') or 'ffffff').strip().lstrip('#')
    
    if not image_url or not product_name or not brand or not row_number:
        return jsonify({'error': 'Missing parameters'}), 400
        
    row_number = int(row_number)
    
    print(f"\n[Flask API] Manual override selected for Row {row_number}")
    print(f"🔗 URL: {image_url}")
    
    try:
        # 1. تحميل ومعالجة الصورة محلياً (إزالة خلفية وحجم وتوسيط)
        processed_image_path = image_processor.process_product_image(image_url, product_name, brand)
        if not processed_image_path or not os.path.exists(processed_image_path):
            msg = (
                f"<b>⚠️ فشل اعتماد صورة يدوياً للمنتج!</b>\n\n"
                f"📦 <b>المنتج:</b> {product_name}\n"
                f"❌ <b>السبب:</b> فشل تحميل الصورة أو معالجتها محلياً."
            )
            image_processor.send_telegram_notification(msg)
            config.METRICS["failed_runs"] += 1
            return jsonify({'error': 'Failed to download or process image locally'}), 500
            
        # تحسين الجودة بالتكبير الفائق بالذكاء الاصطناعي
        upscale = data.get('upscale', True)
        if upscale:
            upscale_image(processed_image_path, scale=2)
            
        # 2. استخراج البيانات الوصفية من الصورة وتحديث الشيت أولاً لتنظيم المجلدات سحابياً
        metadata = image_processor.extract_metadata_from_image(processed_image_path, product_name, brand)
        
        folder = "products"
        tags = []
        
        # التحقق من وجود تعديلات تصنيف الفئات يدوياً من لوحة التحكم
        override_l1_en = (data.get('category_l1_en') or '').strip()
        override_l2_en = (data.get('category_l2_en') or '').strip()
        override_l3_en = (data.get('category_l3_en') or '').strip()
        
        if override_l1_en:
            import categories
            norm = categories.normalize_category_path(override_l1_en, override_l2_en, override_l3_en)
            if not metadata:
                metadata = {}
            metadata.update(norm)

        if metadata:
            sheets_client = google_sheets.get_sheets_client()
            worksheet = google_sheets.open_worksheet(sheets_client, config.SPREADSHEET_NAME_OR_URL)
            google_sheets.update_product_metadata(worksheet, row_number, metadata)
            
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
                
        # 3. رفع الصورة إلى Cloudinary بالمجلد والوسوم وخيارات التحجيم المحددة من المستخدم
        image_link = cloudinary_storage.upload_product_image_to_cloudinary(
            processed_image_path,
            product_name,
            brand,
            folder=folder,
            tags=tags,
            target_width=target_width,
            target_height=target_height,
            padding_ratio=padding_ratio,
            bg_color=bg_color
        )
        
        # استخراج البصمة المتجهة للصورة المعتمدة يدوياً قبل حذفها لحفظها بالكاش
        clip_embedding = None
        try:
            import image_search
            _, clip_embedding = image_search.check_image_relevance_via_clip(processed_image_path, brand, product_name)
        except Exception as e:
            print(f"⚠️ خطأ أثناء حساب متجه CLIP للصورة المعتمدة يدوياً: {e}")
            
        # تنظيف الملف المؤقت
        try:
            if os.path.exists(processed_image_path):
                os.remove(processed_image_path)
        except Exception:
            pass
            
        if not image_link:
            msg = (
                f"<b>⚠️ فشل اعتماد صورة يدوياً للمنتج!</b>\n\n"
                f"📦 <b>المنتج:</b> {product_name}\n"
                f"❌ <b>السبب:</b> فشل رفع الصورة المعالجة إلى Cloudinary."
            )
            image_processor.send_telegram_notification(msg)
            config.METRICS["failed_runs"] += 1
            return jsonify({'error': 'Failed to upload processed image to Cloudinary'}), 500
            
        # 4. تحديث Google Sheets بالرابط الجديد
        sheets_client = google_sheets.get_sheets_client()
        worksheet = google_sheets.open_worksheet(sheets_client, config.SPREADSHEET_NAME_OR_URL)
        _, link_column_index = google_sheets.get_products(worksheet)
        
        update_success = google_sheets.update_image_link(
            worksheet,
            row_number,
            link_column_index,
            image_link
        )
        
        if update_success:
            print(f"🎉 [Flask API] Row {row_number} updated with: {image_link}")
            
            # حفظ في الكاش المحلي كاختيار معتمد يدوياً
            barcode = (data.get('barcode') or '').strip()
            try:
                import local_cache_db
                local_cache_db.save_product_resolution(
                    barcode,
                    product_name,
                    brand,
                    image_url,
                    image_link,
                    1.0,  # ثقة قصوى للاعتماد اليدوي البشري
                    metadata,
                    clip_embedding
                )
            except Exception as e:
                print(f"⚠️ خطأ أثناء حفظ السجل اليدوي في الكاش: {e}")
                
            msg = (
                f"<b>🎉 تم اعتماد صورة منتج يدوياً بنجاح!</b>\n\n"
                f"📦 <b>المنتج:</b> {product_name}\n"
                f"🏷️ <b>الماركة:</b> {brand}\n"
                f"📂 <b>المجلد:</b> <code>{folder}</code>\n"
                f"🏷️ <b>الوسوم:</b> {metadata.get('tags_ar', '') if metadata else ''}\n"
                f"🔗 <a href='{image_link}'>رابط الصورة النهائي</a>"
            )
            image_processor.send_telegram_notification(msg)
            config.METRICS["successful_runs"] += 1
            return jsonify({
                'status': 'success',
                'image_link': image_link
            })
        else:
            msg = (
                f"<b>⚠️ فشل اعتماد صورة يدوياً للمنتج!</b>\n\n"
                f"📦 <b>المنتج:</b> {product_name}\n"
                f"❌ <b>السبب:</b> فشل كتابة الرابط النهائي داخل ورقة Google Sheets."
            )
            image_processor.send_telegram_notification(msg)
            config.METRICS["failed_runs"] += 1
            return jsonify({'error': 'Failed to write link to Google Sheet'}), 500
            
    except Exception as e:
        print(f"[Flask API Error] Failed during manual override update: {e}")
        msg = (
            f"<b>⚠️ خطأ غير متوقع أثناء معالجة الاعتماد اليدوي!</b>\n\n"
            f"📦 <b>المنتج:</b> {product_name}\n"
            f"❌ <b>الخطأ:</b> {str(e)}"
        )
        image_processor.send_telegram_notification(msg)
        config.METRICS["failed_runs"] += 1
        return jsonify({'error': str(e)}), 500

@app.route('/api/upload_manual_image', methods=['POST'])
def api_upload_manual_image():
    """
    مسار لرفع صورة يدوياً من جهاز المستخدم، معالجة الخلفية والتحجيم تلقائياً،
    ورفع النتيجة لـ Cloudinary وتحديث الشيت والكاش المحلي.
    """
    try:
        if 'file' not in request.files:
            return jsonify({'error': 'No file uploaded'}), 400
            
        file = request.files['file']
        row_number = request.form.get('row_number')
        product_name = request.form.get('product_name', '').strip()
        brand = request.form.get('brand', '').strip()
        barcode = request.form.get('barcode', '').strip()
        # معايير التحجيم الديناميكية من اختيار المستخدم
        target_width  = int(request.form.get('target_width',  800))
        target_height = int(request.form.get('target_height', 800))
        padding_ratio = float(request.form.get('padding_ratio', 0.85))
        bg_color      = (request.form.get('bg_color') or 'ffffff').strip().lstrip('#')
        
        if not file or not row_number or not product_name or not brand:
            return jsonify({'error': 'Missing parameters'}), 400
            
        row_number = int(row_number)
        
        # إنشاء مجلد مؤقت للعمليات
        os.makedirs("temp", exist_ok=True)
        safe_name = f"manual_upload_{product_name}_{brand}".replace("/", "_").replace("\\", "_").replace(":", "_").replace(" ", "_")
        raw_path = os.path.join("temp", f"raw_{safe_name}.png")
        
        # حفظ الملف المرفوع محلياً
        file.save(raw_path)
        
        print(f"\n[Flask API] Manual drag-drop image uploaded for Row {row_number} - File size: {os.path.getsize(raw_path)} bytes")
        
        # 1. اقتصاص ذكي بالذكاء الاصطناعي إن وُجد مربع محيط بالمنتج
        import image_processor
        box = image_processor.get_product_bounding_box(raw_path, product_name, brand)
        if box:
            cropped_path = os.path.join("temp", f"cropped_{safe_name}.png")
            if image_processor.crop_image_by_box(raw_path, box, cropped_path):
                try:
                    if os.path.exists(raw_path):
                        os.remove(raw_path)
                except Exception:
                    pass
                os.rename(cropped_path, raw_path)
                
        # 2. إزالة الخلفية فقط - التحجيم والقص والتوسيط يتم سحابياً عبر Cloudinary
        nobg_path = os.path.join("temp", f"nobg_{safe_name}.png")
        if not image_processor.remove_background(raw_path, nobg_path):
            nobg_path = raw_path
            
        # 3. تحسين طفيف للجودة قبل الرفع بدون resize_and_pad محلي
        temp_enhanced = os.path.join("temp", f"enhanced_{safe_name}.png")
        final_path = nobg_path
        if image_processor.enhance_image_quality(nobg_path, temp_enhanced):
            try:
                final_path = temp_enhanced
            except Exception:
                pass
            
        # 2. استخراج الميتاداتا
        metadata = image_processor.extract_metadata_from_image(final_path, product_name, brand)
        folder = "products"
        tags = []
        if metadata:
            sheets_client = google_sheets.get_sheets_client()
            worksheet = google_sheets.open_worksheet(sheets_client, config.SPREADSHEET_NAME_OR_URL)
            google_sheets.update_product_metadata(worksheet, row_number, metadata)
            cat1 = metadata.get("category_l1_en", "").strip().lower().replace(" ", "_").replace("&", "and")
            cat2 = metadata.get("category_l2_en", "").strip().lower().replace(" ", "_").replace("&", "and")
            if cat1:
                if cat2:
                    folder = f"products/{cat1}/{cat2}"
                else:
                    folder = f"products/{cat1}"
            tags_str = metadata.get("tags_en", "")
            if tags_str:
                tags = [t.strip() for t in tags_str.split(",") if t.strip()]
                
        # 3. رفع النتيجة لكلويديناري مع خيارات التحجيم المحددة من المستخدم
        import cloudinary_storage
        image_link = cloudinary_storage.upload_product_image_to_cloudinary(
            final_path,
            product_name,
            brand,
            folder=folder,
            tags=tags,
            target_width=target_width,
            target_height=target_height,
            padding_ratio=padding_ratio,
            bg_color=bg_color
        )
        
        # استخراج بصمة الـ CLIP
        clip_embedding = None
        try:
            import image_search
            _, clip_embedding = image_search.check_image_relevance_via_clip(final_path, brand, product_name)
        except Exception as e:
            print(f"⚠️ خطأ أثناء حساب متجه CLIP للصورة المرفوعة: {e}")
            
        # تنظيف الملفات المؤقتة
        for p in [raw_path, nobg_path, final_path]:
            try:
                if os.path.exists(p):
                    os.remove(p)
            except Exception:
                pass
                
        if not image_link:
            return jsonify({'error': 'Failed to upload processed image to Cloudinary'}), 500
            
        # 4. تحديث ورقة الشيت والكاش
        sheets_client = google_sheets.get_sheets_client()
        worksheet = google_sheets.open_worksheet(sheets_client, config.SPREADSHEET_NAME_OR_URL)
        _, link_column_index = google_sheets.get_products(worksheet)
        
        update_success = google_sheets.update_image_link(worksheet, row_number, link_column_index, image_link)
        
        if update_success:
            import local_cache_db
            local_cache_db.save_product_resolution(
                barcode,
                product_name,
                brand,
                "manual_upload",
                image_link,
                1.0,
                metadata,
                clip_embedding
            )
            
            # إشعار Telegram بالنجاح
            msg = (
                f"<b>🎉 تم رفع وتجميل صورة منتج يدوياً بنجاح!</b>\n\n"
                f"📦 <b>المنتج:</b> {product_name}\n"
                f"🏷️ <b>الماركة:</b> {brand}\n"
                f"🔗 <a href='{image_link}'>رابط الصورة المعالجة نهائياً</a>"
            )
            image_processor.send_telegram_notification(msg)
            config.METRICS["successful_runs"] += 1
            return jsonify({
                'status': 'success',
                'image_link': image_link
            })
            
        return jsonify({'error': 'Failed to update Google Sheet'}), 500
        
    except Exception as e:
        print(f"[Flask API Error] Failed during manual drag-drop upload: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/logs', methods=['GET'])
def api_logs():
    """
    سحب اللوغات الحية لخيوط العمليات الخلفية
    """
    return jsonify({'logs': config.RUNNER_LOGS})

# ==========================================
# 🤖 INTERACTIVE TELEGRAM BOT CONTROL SECTION
# ==========================================

LATEST_SEARCHES = {} # حفظ حالة آخر بحث تفاعلي لكل مستخدم: {chat_id: {"row_number": rn, "candidates": [...]}}
USER_STATES = {}     # حفظ حالة المحادثة المتعددة الخطوات: {chat_id: {"state": "waiting...", "row_number": rn}}

def send_telegram_msg(chat_id, text, reply_markup=None):
    token = getattr(config, "TELEGRAM_BOT_TOKEN", "")
    if not token:
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML"
    }
    if reply_markup:
        import json
        payload["reply_markup"] = json.dumps(reply_markup) if isinstance(reply_markup, dict) else reply_markup
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Error sending Telegram message: {e}")

def send_telegram_photo(chat_id, photo, caption, reply_markup=None):
    token = getattr(config, "TELEGRAM_BOT_TOKEN", "")
    if not token:
        return
    url = f"https://api.telegram.org/bot{token}/sendPhoto"
    import json
    data = {
        "chat_id": chat_id,
        "caption": caption,
        "parse_mode": "HTML"
    }
    if reply_markup:
        data["reply_markup"] = json.dumps(reply_markup) if isinstance(reply_markup, dict) else reply_markup
        
    try:
        if isinstance(photo, str) and (photo.startswith("http://") or photo.startswith("https://")):
            data["photo"] = photo
            requests.post(url, json=data, timeout=15)
        else:
            # ملف محلي
            file_path = photo
            if os.path.exists(file_path):
                with open(file_path, "rb") as f:
                    requests.post(url, data=data, files={"photo": f}, timeout=15)
            else:
                print(f"❌ Local photo path not found: {file_path}")
    except Exception as e:
        print(f"Error sending Telegram photo: {e}")

def answer_telegram_callback(callback_query_id, text):
    token = getattr(config, "TELEGRAM_BOT_TOKEN", "")
    if not token:
        return
    url = f"https://api.telegram.org/bot{token}/answerCallbackQuery"
    payload = {
        "callback_query_id": callback_query_id,
        "text": text
    }
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Error answering callback query: {e}")

def parse_message_intent_with_gemini(message_text):
    """
    تحليل نية الرسائل النصية للمستخدم بلغة طبيعية وتحديد الإجراء المناسب بـ Gemini.
    """
    if not getattr(config, "GEMINI_API_KEY", ""):
        return {"intent": "unknown", "row_number": None, "query": None}
        
    prompt = (
        "You are an AI assistant parsing user commands for an e-commerce catalog bot.\n"
        "Analyze the user's message and categorize it into one of these intents:\n"
        "- 'status': the user wants to check catalog progress, sheet status, or API metrics.\n"
        "- 'run_all': the user wants to start the full automation/batch processing of the sheet.\n"
        "- 'search_row': the user wants to look up or process a specific row number in the sheet.\n"
        "- 'search_catalog': the user wants to view or search uploaded assets for a brand in Cloudinary.\n"
        "- 'unknown': none of the above.\n\n"
        "Rules:\n"
        "1. Return ONLY a valid JSON object in this format:\n"
        "{\n"
        "  \"intent\": \"intent_name\",\n"
        "  \"row_number\": null or integer (if intent is search_row),\n"
        "  \"query\": null or string (if intent is search_catalog or query refers to a search string)\n"
        "}\n"
        "2. Do not include markdown formatting, backticks, or wrapping. Just the raw JSON string.\n\n"
        f"User message: '{message_text}'"
    )
    
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash:generateContent?key={config.GEMINI_API_KEY}"
    payload = {
        "contents": [
            {
                "parts": [
                    {"text": prompt}
                ]
            }
        ]
    }
    
    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code == 200:
            config.METRICS["gemini_api_calls"] += 1
            res_data = response.json()
            text_out = res_data["candidates"][0]["content"]["parts"][0]["text"].strip()
            if text_out.startswith("```"):
                text_out = text_out.replace("```json", "").replace("```", "").strip()
            import json
            parsed = json.loads(text_out)
            return parsed
    except Exception as e:
        print(f"Error parsing NLP with Gemini: {e}")
    return {"intent": "unknown", "row_number": None, "query": None}

def handle_bot_status(chat_id):
    try:
        send_telegram_msg(chat_id, "⏳ جاري الاتصال بـ Google Sheets وجلب إحصائيات الكتالوج الحالية...")
        sheets_client = google_sheets.get_sheets_client()
        worksheet = google_sheets.open_worksheet(sheets_client, config.SPREADSHEET_NAME_OR_URL)
        if not worksheet:
            send_telegram_msg(chat_id, "❌ فشل الوصول لورقة Google Sheets.")
            return
        products, _ = google_sheets.get_products(worksheet)
        total = len(products)
        linked = sum(1 for p in products if p["existing_image_link"])
        missing = total - linked
        percentage = f"{(linked / total * 100):.1f}%" if total > 0 else "0%"
        
        gemini_calls = config.METRICS.get("gemini_api_calls", 0)
        cost = gemini_calls * 0.000075
        
        msg = (
            f"📊 <b>إحصائيات الكتالوج الحالية:</b>\n\n"
            f"📦 <b>إجمالي المنتجات بالشيت:</b> {total}\n"
            f"✅ <b>المنتجات المكتملة:</b> {linked}\n"
            f"❌ <b>المنتجات المفقودة:</b> {missing}\n"
            f"📈 <b>نسبة الإنجاز الكلية:</b> {percentage}\n\n"
            f"🧠 <b>مطالبات Gemini API:</b> {gemini_calls}\n"
            f"💰 <b>التكلفة الإجمالية المقدرة:</b> ${cost:.4f} USD"
        )
        send_telegram_msg(chat_id, msg)
    except Exception as e:
        send_telegram_msg(chat_id, f"❌ حدث خطأ أثناء جلب الحالة: {e}")

def handle_bot_run_all(chat_id):
    thread = threading.Thread(target=run_all_automation_thread)
    thread.daemon = True
    thread.start()
    send_telegram_msg(
        chat_id, 
        "⚡ <b>تم تشغيل الأتمتة الكاملة لكافة منتجات الشيت بالخلفية بنجاح!</b>\n"
        "سيقوم الخادم الآن بالبحث والمعالجة والرفع مباشرة وتحديث الخلايا تلقائياً."
    )

def handle_bot_catalog(chat_id, query):
    """
    تصفح المجلدات السحابية لكلاوديناري والبحث فيها وإعادتها على تليجرام.
    """
    try:
        import cloudinary.api
        send_telegram_msg(chat_id, f"📂 جاري الاستعلام في كلاوديناري عن المجلد/البراند: '{query}'...")
        
        prefix_str = "products"
        if query:
            prefix_str = f"products/{query.lower().strip()}"
            
        res = cloudinary.api.resources(type="upload", prefix=prefix_str, max_results=5)
        resources = res.get("resources", [])
        
        if not resources and query:
            # تجربة فلترة المجلد العام
            res = cloudinary.api.resources(type="upload", prefix="products", max_results=10)
            resources = [r for r in res.get("resources", []) if query.lower() in r.get("public_id", "").lower()]
            
        if not resources:
            send_telegram_msg(chat_id, f"❌ لم يتم العثور على أي أصول أو صور سحابية مطابقة للبراند '{query}'.")
            return
            
        send_telegram_msg(chat_id, f"🖼️ <b>صور المنتج المرفوعة سحابياً (أول {len(resources[:5])} نتائج):</b>")
        for idx, r in enumerate(resources[:5]):
            url = r.get("secure_url")
            public_id = r.get("public_id")
            created_at = r.get("created_at", "")
            
            caption = (
                f"🖼️ <b>صورة سحابية #{idx+1}</b>\n"
                f"📝 <b>المعرف السحابي:</b> <code>{public_id}</code>\n"
                f"📅 <b>تاريخ الرفع:</b> {created_at}\n"
                f"🔗 <a href='{url}'>رابط مباشر</a>"
            )
            send_telegram_photo(chat_id, url, caption)
    except Exception as e:
        send_telegram_msg(chat_id, f"❌ خطأ أثناء تصفح كلاوديناري: {e}")

def send_bot_candidate(chat_id, row_number, idx):
    """
    عرض مرشح بصري فردي مع لوحة تحكم تفاعلية (التنقل، التعديل، البحث المخصص، والاعتماد).
    """
    search_data = LATEST_SEARCHES.get(chat_id)
    if not search_data:
        send_telegram_msg(chat_id, "❌ لا توجد جلسة بحث نشطة حالياً.")
        return
        
    candidates = search_data["candidates"]
    if idx >= len(candidates):
        send_telegram_msg(
            chat_id,
            f"🏁 <b>انتهت الصور المرشحة لهذا المنتج (الصف {row_number})!</b>\n\n"
            "يمكنك النقر على زر <code>بحث مخصص</code> لتغيير استعلام البحث يدوياً وكتابة جملة بحث جديدة.",
            {
                "inline_keyboard": [
                    [{"text": "✏️ جرب كلمة بحث أخرى مخصصة", "callback_data": f"customquery_{row_number}"}]
                ]
            }
        )
        return
        
    cand = candidates[idx]
    
    # التحقق من وجود تعديل محلي لهذه الصورة
    temp_path = f"temp_edit_{chat_id}.png"
    if os.path.exists(temp_path):
        photo_source = temp_path
        caption_suffix = " (الصورة معدلة محلياً ⚙️)"
    else:
        photo_source = cand["url"]
        caption_suffix = ""
        
    title = cand.get("title", "بدون عنوان")
    domain = cand.get("domain", "موقع عام")
    status = cand.get("status", "accepted")
    reasons = cand.get("reasons", [])
    
    status_label = "✅ مطابقة ومقبولة" if status == "accepted" else "⚠️ مستبعدة تلقائياً"
    reasons_text = "\n❌ <b>أسباب الاستبعاد:</b>\n" + "\n".join([f"• {r}" for r in reasons]) if (status == "rejected" and reasons) else ""
    
    caption = (
        f"📦 <b>المنتج:</b> {search_data['product_name']}\n"
        f"🚦 <b>الحالة بالأداة:</b> {status_label}{reasons_text}\n"
        f"🖼️ <b>صورة مرشحة ({idx+1}/{len(candidates)}){caption_suffix}</b>\n"
        f"📝 <b>العنوان:</b> {title}\n"
        f"🌐 <b>المصدر:</b> {domain}\n"
        f" صف رقم {row_number}"
    )
    
    markup = {
        "inline_keyboard": [
            [
                {"text": "✅ اعتماد وتثبيت", "callback_data": f"approve_{row_number}_{idx}"},
                {"text": "➡️ الصورة التالية", "callback_data": f"next_{row_number}_{idx}"}
            ],
            [
                {"text": "⚙️ تعديل الصورة", "callback_data": f"editmenu_{row_number}_{idx}"},
                {"text": "✏️ بحث مخصص", "callback_data": f"customquery_{row_number}"}
            ]
        ]
    }
    
    send_telegram_photo(chat_id, photo_source, caption, markup)

def handle_bot_row_search(chat_id, row_num):
    try:
        # حذف أي ملفات تعديل مؤقتة قديمة
        temp_path = f"temp_edit_{chat_id}.png"
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except:
            pass
            
        sheets_client = google_sheets.get_sheets_client()
        worksheet = google_sheets.open_worksheet(sheets_client, config.SPREADSHEET_NAME_OR_URL)
        if not worksheet:
            send_telegram_msg(chat_id, "❌ فشل الوصول لورقة Google Sheets.")
            return
        products, _ = google_sheets.get_products(worksheet)
        
        prod = None
        for p in products:
            if p["row_number"] == row_num:
                prod = p
                break
                
        if not prod:
            send_telegram_msg(chat_id, f"❌ لم يتم العثور على منتج في الصف رقم {row_num}.")
            return
            
        name = prod["product_name"]
        brand = prod["brand"]
        query = prod["search_query"]
        product_name_ar = prod.get("product_name_ar", "")
        brand_ar = prod.get("brand_ar", "")
        barcode = prod.get("barcode", "")
        category = prod.get("category", "")
        origin = prod.get("origin", "")
        
        send_telegram_msg(chat_id, f"🔍 جاري تشغيل البحث المتقدم وتطبيق فلاتر الجودة لـ:\n<b>{name}</b> (الماركة: {brand}) | صف {row_num}...")
        
        trace = {}
        # استدعاء خط فحص الصور الذكي المتطابق مع لوحة الويب
        best_image = image_search.search_best_product_image(
            query, 
            name, 
            brand, 
            product_name_ar=product_name_ar, 
            brand_ar=brand_ar,
            trace=trace,
            barcode=barcode,
            category=category,
            origin=origin
        )
        
        # استخراج كافة المرشحات الفريدة التي فحصها الخوارزمية
        candidates = []
        seen_urls = set()
        if trace and "steps" in trace:
            for step in trace["steps"]:
                if "candidates" in step:
                    for c in step["candidates"]:
                        if c["url"] not in seen_urls:
                            seen_urls.add(c["url"])
                            candidates.append(c)
                            
        # فرز الصور المقبولة لتظهر أولاً
        candidates.sort(key=lambda x: 0 if x.get("status") == "accepted" else 1)
        
        if not candidates:
            send_telegram_msg(chat_id, f"❌ لم يتم العثور على أي صور مرشحة للمنتج '{name}' صف {row_num}.")
            return
            
        LATEST_SEARCHES[chat_id] = {
            "row_number": row_num,
            "product_name": name,
            "brand": brand,
            "candidates": candidates
        }
        
        send_bot_candidate(chat_id, row_num, 0)
            
    except Exception as e:
        send_telegram_msg(chat_id, f"❌ خطأ أثناء معالجة البحث: {e}")

def handle_bot_editaction(chat_id, row_num, idx, action):
    """
    تحرير الصورة محلياً بواسطة Pillow من أزرار تليجرام المباشرة.
    """
    search_data = LATEST_SEARCHES.get(chat_id)
    if not search_data:
        send_telegram_msg(chat_id, "❌ انتهت الجلسة الحالية.")
        return
        
    candidates = search_data["candidates"]
    cand = candidates[idx]
    image_url = cand["url"]
    
    temp_path = f"temp_edit_{chat_id}.png"
    
    try:
        # 1. تنزيل الصورة محلياً للتعديل إن لم تكن منزلة
        if not os.path.exists(temp_path):
            resp = requests.get(image_url, timeout=15)
            if resp.status_code == 200:
                with open(temp_path, "wb") as f:
                    f.write(resp.content)
            else:
                send_telegram_msg(chat_id, "❌ فشل تحميل الملف الأصلي للتعديل.")
                return
                
        # 2. تطبيق الفلتر المطلوب
        from PIL import Image, ImageEnhance
        
        if action == "bg":
            send_telegram_msg(chat_id, "⏳ جاري عزل خلفية الصورة بالذكاء الاصطناعي...")
            # عزل الخلفية
            processed_bg = image_processor.remove_background(temp_path)
            if processed_bg and os.path.exists(processed_bg):
                import shutil
                shutil.copy(processed_bg, temp_path)
                try:
                    os.remove(processed_bg)
                except:
                    pass
                send_telegram_msg(chat_id, "✅ تم إزالة الخلفية والضجيج بنجاح!")
            else:
                send_telegram_msg(chat_id, "⚠️ فشل عزل الخلفية. قد تكون الخلفية معزولة مسبقاً أو تعذر تحديد الحواف.")
                
        elif action == "color":
            img = Image.open(temp_path)
            enhancer = ImageEnhance.Color(img.convert("RGB"))
            img_enhanced = enhancer.enhance(1.4) # زيادة التشبع اللوني بـ 40%
            img_enhanced.save(temp_path)
            send_telegram_msg(chat_id, "✅ تم زيادة تشبع الألوان وتحسين المظهر الجمالي للعبوة!")
            
        elif action == "wm":
            img = Image.open(temp_path).convert("RGBA")
            from PIL import ImageDraw
            draw = ImageDraw.Draw(img)
            w, h = img.size
            # وضع نص علامة مائية بالركن
            draw.text((15, h - 35), "PREMIUM CATALOG", fill=(200, 200, 200, 150))
            img.convert("RGB").save(temp_path)
            send_telegram_msg(chat_id, "✅ تم إدراج علامة مائية للمتجر الإلكتروني بنجاح!")
            
        # إرسال المعاينة المحدثة للمستخدم
        send_bot_candidate(chat_id, row_num, idx)
        
    except Exception as e:
        send_telegram_msg(chat_id, f"❌ خطأ أثناء تطبيق الفلتر: {e}")

def handle_bot_approve(chat_id, row_num, idx):
    search_data = LATEST_SEARCHES.get(chat_id)
    if not search_data or search_data["row_number"] != row_num:
        send_telegram_msg(chat_id, "⚠️ انتهت صلاحية جلسة البحث الحالية.")
        return
        
    candidates = search_data["candidates"]
    if idx >= len(candidates):
        send_telegram_msg(chat_id, "⚠️ الصورة المحددة غير صالحة.")
        return
        
    cand = candidates[idx]
    product_name = search_data["product_name"]
    brand = search_data["brand"]
    
    # فحص إذا تم استخدام الصورة المعدلة محلياً
    temp_path = f"temp_edit_{chat_id}.png"
    if os.path.exists(temp_path):
        image_source = temp_path
        is_local = True
    else:
        image_source = cand["url"]
        is_local = False
        
    send_telegram_msg(chat_id, f"⏳ جاري رفع واعتماد الصورة للمنتج '{product_name}' صف {row_num}...")
    
    def worker():
        try:
            # 1. المعالجة البصرية
            processed_path = image_processor.process_product_image(image_source, product_name, brand)
            if not processed_path or not os.path.exists(processed_path):
                send_telegram_msg(chat_id, f"❌ فشل تحميل أو معالجة الصورة للمنتج '{product_name}' صف {row_num}.")
                return
                
            # 2. البيانات الوصفية والرفع السحابي
            metadata = image_processor.extract_metadata_from_image(processed_path, product_name, brand)
            folder = "products"
            tags = []
            
            sheets_client = google_sheets.get_sheets_client()
            worksheet = google_sheets.open_worksheet(sheets_client, config.SPREADSHEET_NAME_OR_URL)
            
            if metadata:
                google_sheets.update_product_metadata(worksheet, row_num, metadata)
                cat1 = metadata.get("category_l1_en", "").strip().lower().replace(" ", "_").replace("&", "and")
                cat2 = metadata.get("category_l2_en", "").strip().lower().replace(" ", "_").replace("&", "and")
                if cat1:
                    if cat2:
                        folder = f"products/{cat1}/{cat2}"
                    else:
                        folder = f"products/{cat1}"
                tags_str = metadata.get("tags_en", "")
                if tags_str:
                    tags = [t.strip() for t in tags_str.split(",") if t.strip()]
                    
            image_link = cloudinary_storage.upload_product_image_to_cloudinary(
                processed_path,
                product_name,
                brand,
                folder=folder,
                tags=tags
            )
            
            # تنظيف الصور المؤقتة
            try:
                if os.path.exists(processed_path):
                    os.remove(processed_path)
            except:
                pass
            if is_local:
                try:
                    if os.path.exists(temp_path):
                        os.remove(temp_path)
                except:
                    pass
                    
            # 3. تحديث Google Sheets بالرابط الجديد
            _, link_column_index = google_sheets.get_products(worksheet)
            update_success = google_sheets.update_image_link(
                worksheet,
                row_num,
                link_column_index,
                image_link
            )
            
            if update_success:
                send_telegram_msg(
                    chat_id, 
                    f"<b>🎉 تم اعتماد وتثبيت الصورة وتحديث الشيت بنجاح!</b>\n\n"
                    f"📦 <b>المنتج:</b> {product_name}\n"
                    f"🔗 <a href='{image_link}'>رابط كلويديناري النهائي</a>"
                )
                config.METRICS["successful_runs"] += 1
            else:
                send_telegram_msg(chat_id, f"❌ فشل تحديث خلية الشيت بالرابط النهائي للصف {row_num}.")
                config.METRICS["failed_runs"] += 1
                
        except Exception as e:
            send_telegram_msg(chat_id, f"❌ خطأ غير متوقع أثناء معالجة الاعتماد: {e}")
            config.METRICS["failed_runs"] += 1

    t = threading.Thread(target=worker)
    t.daemon = True
    t.start()

def telegram_bot_polling_loop():
    import time
    token = getattr(config, "TELEGRAM_BOT_TOKEN", "")
    if not token:
        print("⚠️ [Telegram Bot] لم يتم تحديد توكن البوت في config.py")
        return
        
    print("🤖 [Telegram Bot] بدء مستمع الأوامر التفاعلي (Long Polling)...")
    offset = 0
    
    while True:
        try:
            url = f"https://api.telegram.org/bot{token}/getUpdates?offset={offset}&timeout=15"
            response = requests.get(url, timeout=20)
            if response.status_code == 200:
                data = response.json()
                if data.get("ok"):
                    for update in data.get("result", []):
                        offset = update["update_id"] + 1
                        
                        # 1. معالجة الرسائل المستلمة
                        if "message" in update:
                            message = update["message"]
                            chat_id = message["chat"]["id"]
                            text = message.get("text", "").strip()
                            
                            state_data = USER_STATES.get(chat_id, {})
                            
                            # حالة انتظار إدخال كلمة بحث مخصصة للمنتج
                            if state_data.get("state") == "waiting_for_custom_query":
                                row_num = state_data["row_number"]
                                send_telegram_msg(chat_id, f"🔍 جاري البحث المخصص عن '{text}' في الويب للمنتج...")
                                
                                results = image_search.execute_hybrid_search(text)
                                if not results:
                                    send_telegram_msg(chat_id, "❌ لم يتم العثور على نتائج. يرجى كتابة كلمة بحث أخرى مخصصة:")
                                else:
                                    # تحديث القائمة بالنتائج الجديدة وعرض أول مرشح
                                    LATEST_SEARCHES[chat_id]["candidates"] = results[:5]
                                    # حذف التعديلات المؤقتة القديمة
                                    temp_path = f"temp_edit_{chat_id}.png"
                                    try:
                                        if os.path.exists(temp_path):
                                            os.remove(temp_path)
                                    except:
                                        pass
                                    USER_STATES[chat_id] = {}
                                    send_bot_candidate(chat_id, row_num, 0)
                                    
                            # الأوامر الرئيسية والرسائل اللغوية الطبيعية بـ Gemini
                            elif text:
                                if text == "/start" or text == "/help":
                                    menu = {
                                        "inline_keyboard": [
                                            [
                                                {"text": "📊 الحالة العامة (Status)", "callback_data": "cmd_status"},
                                                {"text": "⚡ تشغيل الأتمتة (Run All)", "callback_data": "cmd_run_all"}
                                            ]
                                        ]
                                    }
                                    send_telegram_msg(
                                        chat_id, 
                                        "🤖 <b>مرحباً بك في لوحة تحكم الكتالوج وأتمتة صور المنتجات الذكية!</b>\n\n"
                                        "يمكنك التحدث معي بالعامية واللغة الطبيعية (Gemini AI), أو الضغط على الأزرار بالأسفل, أو كتابة الأوامر:\n"
                                        "• <code>/status</code> - لمعرفة إحصائيات الشيت والاستهلاك.\n"
                                        "• <code>/run_all</code> - لبدء تشغيل أتمتة الشيت بالخلفية.\n"
                                        "• <code>/row [رقم الصف]</code> - للبحث والتحكم اليدوي بصف معين (مثل: <code>/row 13</code>).\n"
                                        "• <code>/catalog [البراند]</code> - لتصفح الصور المرفوعة مسبقاً بكلاوديناري.",
                                        menu
                                    )
                                elif text.startswith("/row "):
                                    parts = text.split()
                                    if len(parts) >= 2 and parts[1].isdigit():
                                        handle_bot_row_search(chat_id, int(parts[1]))
                                    else:
                                        send_telegram_msg(chat_id, "⚠️ يرجى استخدام صيغة صف صحيحة، مثال: <code>/row 12</code>")
                                        
                                elif text.startswith("/catalog "):
                                    query = text[9:].strip()
                                    handle_bot_catalog(chat_id, query)
                                    
                                elif text == "/status":
                                    handle_bot_status(chat_id)
                                    
                                elif text == "/run_all":
                                    handle_bot_run_all(chat_id)
                                    
                                else:
                                    # التفسير الذكي للنصوص باللغة الطبيعية بواسطة Gemini (NLP)
                                    intent_data = parse_message_intent_with_gemini(text)
                                    intent = intent_data.get("intent", "unknown")
                                    
                                    if intent == "status":
                                        handle_bot_status(chat_id)
                                    elif intent == "run_all":
                                        handle_bot_run_all(chat_id)
                                    elif intent == "search_row" and intent_data.get("row_number"):
                                        handle_bot_row_search(chat_id, intent_data["row_number"])
                                    elif intent == "search_catalog" and intent_data.get("query"):
                                        handle_bot_catalog(chat_id, intent_data["query"])
                                    else:
                                        send_telegram_msg(
                                            chat_id, 
                                            "😅 <i>عذراً، لم أفهم قصدك تماماً.</i>\n\n"
                                            "يرجى الضغط على الأزرار بالأسفل، أو كتابة كلمة واضحة مثل:\n"
                                            "• 'حالة الشيت الحين'\n"
                                            "• 'شغل الأتمتة بالكامل'\n"
                                            "• 'ابحث عن صف 13'\n"
                                            "• 'تصفح صور شركة لاكنور في السحابة'"
                                        )
                                        
                        # 2. معالجة Callback Queries
                        elif "callback_query" in update:
                            cq = update["callback_query"]
                            cq_id = cq["id"]
                            chat_id = cq["message"]["chat"]["id"]
                            cq_data = cq.get("data", "")
                            
                            if cq_data == "cmd_status":
                                answer_telegram_callback(cq_id, "جاري جلب الإحصائيات الحية...")
                                handle_bot_status(chat_id)
                                
                            elif cq_data == "cmd_run_all":
                                answer_telegram_callback(cq_id, "جاري إطلاق الأتمتة...")
                                handle_bot_run_all(chat_id)
                                
                            elif cq_data.startswith("approve_"):
                                answer_telegram_callback(cq_id, "جاري معالجة الصورة واعتمادها...")
                                parts = cq_data.split("_")
                                if len(parts) >= 3:
                                    handle_bot_approve(chat_id, int(parts[1]), int(parts[2]))
                                    
                            elif cq_data.startswith("next_"):
                                parts = cq_data.split("_")
                                if len(parts) >= 3:
                                    row_num = int(parts[1])
                                    next_idx = int(parts[2]) + 1
                                    answer_telegram_callback(cq_id, "جاري تحميل الصورة التالية...")
                                    # حذف أي تعديلات مؤقتة عند التنقل
                                    temp_path = f"temp_edit_{chat_id}.png"
                                    try:
                                        if os.path.exists(temp_path):
                                            os.remove(temp_path)
                                    except:
                                        pass
                                    send_bot_candidate(chat_id, row_num, next_idx)
                                    
                            elif cq_data.startswith("customquery_"):
                                parts = cq_data.split("_")
                                if len(parts) >= 2:
                                    row_num = int(parts[1])
                                    answer_telegram_callback(cq_id, "بانتظار كلمة البحث الجديدة...")
                                    USER_STATES[chat_id] = {
                                        "state": "waiting_for_custom_query",
                                        "row_number": row_num
                                    }
                                    send_telegram_msg(chat_id, "📝 <b>يرجى كتابة كلمة أو جملة البحث المخصصة التي تريد استخدامها لهذا المنتج بالكامل:</b>")
                                    
                            elif cq_data.startswith("editmenu_"):
                                parts = cq_data.split("_")
                                if len(parts) >= 3:
                                    row_num = int(parts[1])
                                    idx = int(parts[2])
                                    answer_telegram_callback(cq_id, "جاري فتح قائمة محرر الصور البصري...")
                                    
                                    markup = {
                                        "inline_keyboard": [
                                            [
                                                {"text": "✨ عزل الخلفية", "callback_data": f"editaction_{row_num}_{idx}_bg"},
                                                {"text": "🎨 تحسين الألوان", "callback_data": f"editaction_{row_num}_{idx}_color"}
                                            ],
                                            [
                                                {"text": "🏷️ ختم علامة مائية", "callback_data": f"editaction_{row_num}_{idx}_wm"},
                                                {"text": "↩️ العودة للمرشح الأصلي", "callback_data": f"backto_{row_num}_{idx}"}
                                            ]
                                        ]
                                    }
                                    send_telegram_msg(chat_id, "⚙️ <b>قائمة محرر الصور التفاعلي (In-Chat Editor):</b>\nاختر التعديل لتطبيقه على الصورة الحالية مباشرة:", markup)
                                    
                            elif cq_data.startswith("editaction_"):
                                parts = cq_data.split("_")
                                if len(parts) >= 4:
                                    row_num = int(parts[1])
                                    idx = int(parts[2])
                                    action = parts[3]
                                    answer_telegram_callback(cq_id, "جاري معالجة وتعديل الصورة...")
                                    handle_bot_editaction(chat_id, row_num, idx, action)
                                    
                            elif cq_data.startswith("backto_"):
                                parts = cq_data.split("_")
                                if len(parts) >= 3:
                                    row_num = int(parts[1])
                                    idx = int(parts[2])
                                    answer_telegram_callback(cq_id, "جاري العودة للمرشح البصري...")
                                    temp_path = f"temp_edit_{chat_id}.png"
                                    try:
                                        if os.path.exists(temp_path):
                                            os.remove(temp_path)
                                    except:
                                        pass
                                    send_bot_candidate(chat_id, row_num, idx)
                                    
            elif response.status_code == 401:
                print("⚠️ [Telegram Bot] التوكن المدخل للبوت غير صالح!")
                time.sleep(10)
            else:
                print(f"⚠️ [Telegram Bot] خطأ في جلب التحديثات (HTTP {response.status_code})")
                time.sleep(5)
        except Exception as e:
            print(f"⚠️ [Telegram Bot Error] {e}")
            time.sleep(3)

if __name__ == '__main__':
    print("🚀 Starting localized test dashboard on http://127.0.0.1:5000")
    
    # تشغيل مستمع البوت التفاعلي لتليجرام بالخلفية مرة واحدة فقط لمنع التكرار بفعل Flask Reloader
    import os
    if os.environ.get('WERKZEUG_RUN_MAIN') == 'true' or not app.debug:
        t_bot = threading.Thread(target=telegram_bot_polling_loop)
        t_bot.daemon = True
        t_bot.start()
        
    app.run(debug=True, port=5000)
