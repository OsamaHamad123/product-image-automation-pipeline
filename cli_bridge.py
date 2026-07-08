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
        
    # استخراج تلقائي للبراند
    if product_name and not brand:
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
    
    if not image_url or not product_name or not brand or not row_number:
        return {'status': 'failed', 'error': 'Missing parameters'}
        
    row_number = int(row_number)
    
    try:
        processed_image_path = image_processor.process_product_image(image_url, product_name, brand)
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
            return {'status': 'success', 'image_link': image_link}
        return {'status': 'failed', 'error': 'Failed to update Google Sheets link'}
    except Exception as e:
        return {'status': 'failed', 'error': str(e), 'traceback': traceback.format_exc()}

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
    
    if not file_path or not row_number or not product_name or not brand or not os.path.exists(file_path):
        return {'status': 'failed', 'error': 'Missing parameters or local file path not found'}
        
    row_number = int(row_number)
    
    try:
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
                
        image_link = cloudinary_storage.upload_product_image_to_cloudinary(
            file_path,
            product_name,
            brand,
            folder=folder,
            tags=tags,
            target_width=target_width,
            target_height=target_height,
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
        return {'status': 'failed', 'error': str(e), 'traceback': traceback.format_exc()}

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
        'upload_manual_image': action_upload_manual_image
    }
    
    if action not in actions:
        print(json.dumps({'status': 'failed', 'error': f'Unknown action: {action}'}))
        sys.exit(1)
        
    try:
        result = actions[action](params)
        print(json.dumps(result))
    except Exception as e:
        print(json.dumps({'status': 'failed', 'error': str(e), 'traceback': traceback.format_exc()}))

if __name__ == '__main__':
    main()
