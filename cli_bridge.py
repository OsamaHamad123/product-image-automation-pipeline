# cli_bridge.py
# جسر برمجيات الكونسول لربط لوحة تحكم Laravel مباشرة بعمليات Python دون الحاجة لخادم Flask

import sys
import os
import json
import base64
import traceback

# استيراد موديولات الأتمتة الأساسية
import config
# كتم الطباعة المباشرة لـ log_runner لمنع تلويث مخرجات الـ JSON المسترجعة للوحة التحكم
config._redis_available = False
def silent_log(*args):
    pass
config.log_runner = silent_log

import google_sheets
import image_search
import image_processor
import cloudinary_storage
import local_cache_db

def decode_params():
    """
    فك تشفير البارامترات الممررة كـ Base64 لتجنب أي مشاكل هروب نصوص (Shell Escaping).
    """
    if len(sys.argv) < 3:
        return {}
    try:
        raw_b64 = sys.argv[2]
        decoded = base64.b64decode(raw_b64).decode('utf-8')
        return json.loads(decoded)
    except Exception:
        # تراجع في حال تمرير JSON مباشر
        try:
            return json.loads(sys.argv[2])
        except Exception:
            return {}

def action_get_products(params):
    sheets_client = google_sheets.get_sheets_client()
    if not sheets_client:
        return {'status': 'failed', 'error': 'Google Sheets API connection failed'}
        
    worksheet = google_sheets.open_worksheet(sheets_client, config.SPREADSHEET_NAME_OR_URL)
    if not worksheet:
        return {'status': 'failed', 'error': 'Sheet not found'}
        
    products, _ = google_sheets.get_products(worksheet)
    failures = local_cache_db.get_product_failures()
    
    # دمج الأخطاء
    for prod in products:
        barcode = prod.get("barcode", "").strip() if prod.get("barcode") else ""
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
            
    return {'status': 'success', 'products': products}

def action_search(params):
    product_name = (params.get('product_name') or '').strip()
    brand = (params.get('brand') or '').strip()
    product_name_ar = (params.get('product_name_ar') or '').strip()
    brand_ar = (params.get('brand_ar') or '').strip()
    custom_query = (params.get('custom_query') or '').strip()
    strict_brand_match = params.get('strict_brand_match')
    if strict_brand_match is not None:
        strict_brand_match = bool(strict_brand_match)
        
    barcode = (params.get('barcode') or '').strip()
    category = (params.get('category') or '').strip()
    origin = (params.get('origin') or '').strip()
    skip_cache = bool(params.get('skip_cache', False))
    
    if not product_name:
        return {'status': 'failed', 'error': 'Product name is required'}
        
    brand_mappings = {}
    try:
        sheets_client = google_sheets.get_sheets_client()
        if sheets_client:
            brand_mappings = google_sheets.get_brand_mappings(sheets_client, config.SPREADSHEET_NAME_OR_URL)
    except Exception:
        pass
        
    # استخراج وتصحيح تلقائي للبراند
    if product_name and brand:
        aligned = google_sheets.align_brand_via_gemini(product_name, brand)
        if aligned:
            brand = aligned
    elif product_name and not brand:
        extracted = google_sheets.extract_brand_from_name(product_name, brand_mappings)
        if extracted:
            brand = extracted
        else:
            extracted = google_sheets.extract_brand_from_start(product_name, brand_mappings)
            if extracted:
                brand = extracted
            else:
                extracted = google_sheets.extract_brand_via_gemini(product_name)
                if extracted:
                    brand = extracted

    search_query = custom_query if custom_query else (f"{product_name} {brand}".strip() if brand else product_name)
    trace = {}
    
    best_image = image_search.search_best_product_image(
        search_query, product_name, brand, 
        product_name_ar=product_name_ar, brand_ar=brand_ar,
        trace=trace, strict_brand_match=strict_brand_match,
        barcode=barcode, category=category, origin=origin,
        brand_mappings=brand_mappings, skip_cache=skip_cache
    )
    
    if best_image:
        return {
            'status': 'success',
            'selected_image': best_image,
            'trace': trace,
            'brand': brand
        }
    else:
        return {
            'status': 'failed',
            'trace': trace,
            'brand': brand
        }

def action_select_image(params):
    image_url = (params.get('image_url') or '').strip()
    product_name = (params.get('product_name') or '').strip()
    brand = (params.get('brand') or '').strip()
    row_number = params.get('row_number')
    
    target_width = int(params.get('target_width', 800))
    target_height = int(params.get('target_height', 800))
    padding_ratio = float(params.get('padding_ratio', 0.85))
    bg_color = (params.get('bg_color') or 'ffffff').strip().lstrip('#')
    bg_removal_method = (params.get('bg_removal_method') or 'photoroom').strip()
    
    if not image_url or not product_name or not row_number:
        return {'status': 'failed', 'error': 'Missing parameters'}
        
    row_number = int(row_number)
    
    try:
        # تهيئة طابور المزامنة الخلفي لـ Google Sheets
        google_sheets.init_async_queue(config.CREDENTIALS_FILE, config.SPREADSHEET_NAME_OR_URL)
        
        enhance_param = params.get('enhance', False)
        if isinstance(enhance_param, str):
            enhance_val = (enhance_param.lower() == 'true')
        else:
            enhance_val = bool(enhance_param)
            
        processed_image_path = image_processor.process_product_image(
            image_url, product_name, brand, 
            bg_removal_method=bg_removal_method,
            target_width=target_width,
            target_height=target_height,
            padding_ratio=padding_ratio,
            enhance=enhance_val,
            bypass_heuristics=True
        )
        if not processed_image_path or not os.path.exists(processed_image_path):
            return {'status': 'failed', 'error': 'Failed to download or process image locally'}
            
        upscale = params.get('upscale', True)
        if upscale:
            try:
                # تكبير فائق
                from PIL import Image
                with Image.open(processed_image_path) as img:
                    w, h = img.size
                    img.resize((w*2, h*2), Image.Resampling.LANCZOS).save(processed_image_path)
            except Exception:
                pass
                
        metadata = image_processor.extract_metadata_from_image(processed_image_path, product_name, brand)
        folder = "products"
        tags = []
        
        override_l1_en = (params.get('category_l1_en') or '').strip()
        override_l2_en = (params.get('category_l2_en') or '').strip()
        override_l3_en = (params.get('category_l3_en') or '').strip()
        
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
                
        # قراءة الأبعاد الفعلية للصورة الناتجة بعد معالجتها وتوسيطها (لتمريرها إلى Cloudinary)
        try:
            from PIL import Image
            with Image.open(processed_image_path) as res_img:
                final_w, final_h = res_img.size
        except Exception:
            final_w = target_width or 800
            final_h = target_height or 800
            
        if not final_w or final_w <= 0:
            final_w = 800
        if not final_h or final_h <= 0:
            final_h = 800

        image_link = cloudinary_storage.upload_product_image_to_cloudinary(
            processed_image_path,
            product_name,
            brand,
            folder=folder,
            tags=tags,
            target_width=final_w,
            target_height=final_h,
            padding_ratio=padding_ratio,
            bg_color=bg_color
        )
        
        # استخراج ميزات التعلم النشط لتسجيل ردود الفعل قبل تنظيف وحذف الصورة المعالجة محلياً
        try:
            import cv2
            from PIL import Image as PIL_Image
            from aesthetics_engine import AestheticPredictor, AdvancedClarityMetrics
            from image_quality_gatekeeper import BoundaryComplianceSegmenter
            
            # aesthetic score
            predictor = AestheticPredictor()
            with PIL_Image.open(processed_image_path) as pil_img:
                aes_score = predictor._heuristic_aesthetic_fallback(pil_img)
                
            # brenner & fill & purity
            img_cv = cv2.imread(processed_image_path)
            gray = cv2.cvtColor(img_cv, cv2.COLOR_BGR2GRAY)
            brenner = AdvancedClarityMetrics.compute_brenner_gradient(gray)
            
            segmenter = BoundaryComplianceSegmenter()
            binary_mask, _ = segmenter.segment_foreground(img_cv)
            bg_report = segmenter.verify_background_purity(img_cv, binary_mask)
            purity_val = bg_report.get("purity_score", 0.85)
            
            h_img, w_img = img_cv.shape[:2]
            contours, _ = cv2.findContours(binary_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            fill_val = 0.80
            if contours:
                c_max = max(contours, key=cv2.contourArea)
                bx, by, bw, bh = cv2.boundingRect(c_max)
                fill_val = (bw * bh) / (w_img * h_img)
            
            # تسجيل رد الفعل البشري
            feedback_file = "human_feedback_local.json"
            feedback_data = []
            if os.path.exists(feedback_file):
                try:
                    with open(feedback_file, "r", encoding="utf-8") as f:
                        feedback_data = json.load(f)
                except Exception:
                    pass
            
            feedback_data.append({
                "aesthetic_score": aes_score,
                "sharpness_brenner": brenner,
                "product_fill_ratio": fill_val,
                "background_purity": purity_val,
                "dinov2_similarity": 0.85,
                "human_override_approved": 1
            })
            
            with open(feedback_file, "w", encoding="utf-8") as f:
                json.dump(feedback_data, f, indent=4)
            print("📝 [Active Learning Feedback] تم تسجيل رد الفعل البشري بنجاح.")
        except Exception as e:
            print(f"⚠️ [Active Learning Feedback Error] تعذر تسجيل رد الفعل: {e}")

        # تنظيف محلي
        try:
            if os.path.exists(processed_image_path):
                os.remove(processed_image_path)
        except Exception:
            pass
            
        if not image_link:
            return {'status': 'failed', 'error': 'Failed to upload processed image to Cloudinary'}
            
        sheets_client = google_sheets.get_sheets_client()
        worksheet = google_sheets.open_worksheet(sheets_client, config.SPREADSHEET_NAME_OR_URL)
        _, link_column_index = google_sheets.get_products(worksheet)
        
        update_success = google_sheets.update_image_link(worksheet, row_number, link_column_index, image_link)
        
        if update_success:
            barcode = (params.get('barcode') or '').strip()
            # جلب متجه الـ CLIP للتوافق
            clip_embedding = None
            try:
                _, clip_embedding = image_search.check_image_relevance_via_clip(processed_image_path, brand, product_name)
            except Exception:
                pass
            local_cache_db.save_product_resolution(
                barcode,
                product_name,
                brand,
                image_url,
                image_link,
                1.0,
                metadata,
                clip_embedding
            )
            
            # تحديث حالة المهمة كـ مكتملة في طابور الخلفية ومسح الكانديديت
            try:
                local_cache_db.update_task_status_by_row(row_number, "completed")
                local_cache_db.delete_curation_candidates(row_number)
            except Exception as e:
                print(f"⚠️ [Curation Cleanup Error] {e}")

            return {'status': 'success', 'image_link': image_link}
        return {'status': 'failed', 'error': 'Failed to update Google Sheets link'}
    except Exception as e:
        config.log_error_to_laravel(
            f"CLI action_select_image exception: {str(e)}\n{traceback.format_exc()}",
            product_name=product_name,
            brand=brand,
            level="ERROR"
        )
        return {'status': 'failed', 'error': str(e), 'traceback': traceback.format_exc()}
    finally:
        google_sheets.stop_async_queue()

def action_upload_manual_image(params):
    file_path = params.get('file_path')
    row_number = params.get('row_number')
    product_name = (params.get('product_name') or '').strip()
    brand = (params.get('brand') or '').strip()
    barcode = (params.get('barcode') or '').strip()
    
    target_width = int(params.get('target_width', 800))
    target_height = int(params.get('target_height', 800))
    padding_ratio = float(params.get('padding_ratio', 0.85))
    bg_color = (params.get('bg_color') or 'ffffff').strip().lstrip('#')
    
    if not file_path or not row_number or not product_name or not os.path.exists(file_path):
        return {'status': 'failed', 'error': 'Missing parameters or local file path not found'}
        
    row_number = int(row_number)
    
    try:
        # تهيئة طابور المزامنة الخلفي لـ Google Sheets
        google_sheets.init_async_queue(config.CREDENTIALS_FILE, config.SPREADSHEET_NAME_OR_URL)
        
        # اقتصاص ذكي
        box = image_processor.get_product_bounding_box(file_path, product_name, brand)
        if box:
            cropped = file_path + "_cropped.png"
            if image_processor.crop_image_by_box(file_path, box, cropped):
                try:
                    os.remove(file_path)
                except Exception: pass
                os.rename(cropped, file_path)
                
        # إزالة الخلفية
        nobg = file_path + "_nobg.png"
        if image_processor.remove_background(file_path, nobg):
            try: os.remove(file_path)
            except Exception: pass
            os.rename(nobg, file_path)
            
        # تحسين الجودة
        enhanced = file_path + "_enhanced.png"
        if image_processor.enhance_image_quality(file_path, enhanced):
            try: os.remove(file_path)
            except Exception: pass
            os.rename(enhanced, file_path)
            
        metadata = image_processor.extract_metadata_from_image(file_path, product_name, brand)
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
                
        # قراءة الأبعاد الفعلية للصورة الناتجة بعد معالجتها وتوسيطها (لتمريرها إلى Cloudinary)
        try:
            from PIL import Image
            with Image.open(file_path) as res_img:
                final_w, final_h = res_img.size
        except Exception:
            final_w = target_width or 800
            final_h = target_height or 800
            
        if not final_w or final_w <= 0:
            final_w = 800
        if not final_h or final_h <= 0:
            final_h = 800

        image_link = cloudinary_storage.upload_product_image_to_cloudinary(
            file_path,
            product_name,
            brand,
            folder=folder,
            tags=tags,
            target_width=final_w,
            target_height=final_h,
            padding_ratio=padding_ratio,
            bg_color=bg_color
        )
        
        # تنظيف
        try: os.remove(file_path)
        except Exception: pass
        
        if not image_link:
            return {'status': 'failed', 'error': 'Failed to upload manual image to Cloudinary'}
            
        sheets_client = google_sheets.get_sheets_client()
        worksheet = google_sheets.open_worksheet(sheets_client, config.SPREADSHEET_NAME_OR_URL)
        _, link_column_index = google_sheets.get_products(worksheet)
        
        update_success = google_sheets.update_image_link(worksheet, row_number, link_column_index, image_link)
        if update_success:
            local_cache_db.save_product_resolution(
                barcode,
                product_name,
                brand,
                "manual_upload",
                image_link,
                1.0,
                metadata,
                None
            )
            return {'status': 'success', 'image_link': image_link}
        return {'status': 'failed', 'error': 'Failed to update Google Sheet link'}
    except Exception as e:
        config.log_error_to_laravel(
            f"CLI action_upload_manual_image exception: {str(e)}\n{traceback.format_exc()}",
            product_name=product_name,
            brand=brand,
            barcode=barcode,
            level="ERROR"
        )
        return {'status': 'failed', 'error': str(e), 'traceback': traceback.format_exc()}
    finally:
        google_sheets.stop_async_queue()

def action_reject_image(params):
    import uuid
    image_url = (params.get('image_url') or '').strip()
    product_name = (params.get('product_name') or '').strip()
    brand = (params.get('brand') or '').strip()
    row_number = params.get('row_number')
    rejection_reasons = params.get('rejection_reasons') or []
    
    if not image_url or not product_name or not row_number:
        return {'status': 'failed', 'error': 'Missing parameters'}
        
    row_number = int(row_number)
    feedback_id = str(uuid.uuid4())
    asset_id = image_url.split("/")[-1].split("?")[0]
    
    local_cache_db.save_product_failure(
        barcode=None, 
        product_name=product_name, 
        brand=brand, 
        error_message=f"مستبعدة يدوياً: {', '.join(rejection_reasons)}"
    )
    
    success = local_cache_db.save_feedback(
        feedback_id=feedback_id,
        asset_id=asset_id,
        row_number=row_number,
        product_name=product_name,
        brand=brand,
        image_url=image_url,
        reasons=rejection_reasons
    )
    
    try:
        google_sheets.init_async_queue(config.CREDENTIALS_FILE, config.SPREADSHEET_NAME_OR_URL)
        sheets_client = google_sheets.get_sheets_client()
        worksheet = google_sheets.open_worksheet(sheets_client, config.SPREADSHEET_NAME_OR_URL)
        _, link_col_idx = google_sheets.get_products(worksheet)
        google_sheets.update_image_link(worksheet, row_number, link_col_idx, "")
    except Exception as e:
        pass
    finally:
        google_sheets.stop_async_queue()
        
    if success:
        return {'status': 'success', 'message': 'Feedback logged successfully'}
    return {'status': 'failed', 'error': 'Failed to save feedback log to SQLite'}

def main():
    if len(sys.argv) < 2:
        print(json.dumps({'status': 'failed', 'error': 'No action specified'}))
        sys.exit(1)
        
    action = sys.argv[1]
    params = decode_params()
    
    actions = {
        'get_products': action_get_products,
        'search': action_search,
        'select_image': action_select_image,
        'upload_manual_image': action_upload_manual_image,
        'reject_image': action_reject_image
    }
    
    if action not in actions:
        print(json.dumps({'status': 'failed', 'error': f'Unknown action: {action}'}))
        sys.exit(1)
        
    try:
        result = actions[action](params)
        print(json.dumps(result))
    except Exception as e:
        config.log_error_to_laravel(
            f"CLI main exception for action '{action}': {str(e)}\n{traceback.format_exc()}",
            level="ERROR"
        )
        print(json.dumps({'status': 'failed', 'error': str(e), 'traceback': traceback.format_exc()}))

if __name__ == '__main__':
    main()
