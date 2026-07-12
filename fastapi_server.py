# fastapi_server.py
# خادم API مستمر لتشغيل واستدعاء نماذج الذكاء الاصطناعي وجلب البيانات بمرونة وسرعة فائقة

import os
import sys
import json
import uuid
import traceback
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import List, Optional

# إضافة المجلد الحالي للمسار لضمان الاستيراد بشكل صحيح
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import config
import google_sheets
import image_search
import image_processor
import cloudinary_storage
import local_cache_db

# إعداد قنوات التدوين الصامتة عند الاستدعاء لمنع تلويث مخرجات خادم الويب
config._redis_available = True
def api_log(*args):
    msg = " ".join(str(a) for a in args)
    print(f"[FastAPI Log] {msg}")
config.log_runner = api_log

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. تهيئة وتحميل النماذج محلياً في الذاكرة عند بدء التشغيل لتجنب أعباء التحميل المتكررة
    print("⏳ [FastAPI startup] جاري تحميل نماذج الذكاء الاصطناعي محلياً للفحص البصري...")
    try:
        # تحميل نموذج CLIP
        clip_model, clip_proc = image_search.get_clip_model()
        if clip_model is not None:
            print("✅ [FastAPI startup] تم تحميل نموذج CLIP بنجاح في الذاكرة.")
        
        # تحميل نموذج SigLIP
        siglip_model, siglip_proc = image_search.get_siglip_model()
        if siglip_model is not None:
            print("✅ [FastAPI startup] تم تحميل نموذج SigLIP بنجاح في الذاكرة.")
            
        # تحميل نموذج BLIP
        blip_model, blip_proc = image_search.get_blip_model()
        if blip_model is not None:
            print("✅ [FastAPI startup] تم تحميل نموذج BLIP بنجاح في الذاكرة.")
            
        # تهيئة قاعدة البيانات المحلية إذا لم تكن موجودة
        local_cache_db.init_db()
        
    except Exception as e:
        print(f"⚠️ [FastAPI startup Error] فشل تحميل أحد النماذج: {e}")
        traceback.print_exc()
        
    yield
    print("🛑 [FastAPI shutdown] إيقاف خادم FastAPI.")

app = FastAPI(
    title="Product Image Automation API",
    description="خادم خدمات الأتمتة وإثراء كتالوجات المنتجات والتطابق البصري",
    version="1.0.0",
    lifespan=lifespan
)

# ----------------- نماذج البيانات Pydantic -----------------

class SearchRequest(BaseModel):
    product_name: str
    brand: Optional[str] = ""
    product_name_ar: Optional[str] = ""
    brand_ar: Optional[str] = ""
    custom_query: Optional[str] = ""
    strict_brand_match: Optional[bool] = True
    barcode: Optional[str] = ""
    category: Optional[str] = ""
    origin: Optional[str] = ""
    skip_cache: Optional[bool] = False

class SelectImageRequest(BaseModel):
    image_url: str
    product_name: str
    brand: str
    row_number: int
    barcode: Optional[str] = ""
    target_width: Optional[int] = 800
    target_height: Optional[int] = 800
    padding_ratio: Optional[float] = 0.85
    bg_color: Optional[str] = "ffffff"
    bg_removal_method: Optional[str] = None
    upscale: Optional[bool] = True
    enhance: Optional[bool] = False
    category_l1_en: Optional[str] = ""
    category_l2_en: Optional[str] = ""
    category_l3_en: Optional[str] = ""

class RejectImageRequest(BaseModel):
    image_url: str
    product_name: str
    brand: str
    row_number: int
    rejection_reasons: List[str] = []

class UploadManualRequest(BaseModel):
    file_path: str
    row_number: int
    product_name: str
    brand: str
    barcode: Optional[str] = ""
    target_width: Optional[int] = 800
    target_height: Optional[int] = 800
    padding_ratio: Optional[float] = 0.85
    bg_color: Optional[str] = "ffffff"
    upscale: Optional[bool] = True
    enhance: Optional[bool] = False

# ----------------- نقاط النهاية للأتمتة -----------------

@app.get("/")
def read_root():
    return {
        "status": "online",
        "message": "Product Image Automation pipeline API is active",
        "models_loaded": {
            "clip": image_search._clip_model is not None,
            "siglip": image_search._siglip_model is not None,
            "blip": image_search._blip_model is not None
        }
    }

@app.get("/api/products")
def get_products():
    """
    استرجاع قائمة المنتجات من الشيت أو من الكاش الموثق.
    """
    try:
        sheets_client = google_sheets.get_sheets_client()
        if not sheets_client:
            raise HTTPException(status_code=500, detail="Google Sheets API connection failed")
            
        worksheet = google_sheets.open_worksheet(sheets_client, config.SPREADSHEET_NAME_OR_URL)
        if not worksheet:
            raise HTTPException(status_code=404, detail="Sheet not found")
            
        products, _ = google_sheets.get_products(worksheet)
        failures = local_cache_db.get_product_failures()
        
        # دمج الأخطاء للمنتجات
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
                
        return {"status": "success", "products": products}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/search")
def search_product_image(req: SearchRequest):
    """
    البحث التوافقي الذكي والتحقق الفوري مع النماذج المحملة بالذاكرة.
    """
    try:
        product_name = req.product_name.strip() if req.product_name else ""
        brand = req.brand.strip() if req.brand else ""
        
        # البحث عن البراند تلقائياً من الشيت إذا كان فارغاً
        brand_mappings = {}
        try:
            sheets_client = google_sheets.get_sheets_client()
            if sheets_client:
                brand_mappings = google_sheets.get_brand_mappings(sheets_client, config.SPREADSHEET_NAME_OR_URL)
        except Exception:
            pass
            
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

        search_query = req.custom_query.strip() if req.custom_query else (f"{product_name} {brand}".strip() if brand else product_name)
        trace = {}
        
        best_image = image_search.search_best_product_image(
            search_query, product_name, brand, 
            product_name_ar=req.product_name_ar.strip() if req.product_name_ar else "", 
            brand_ar=req.brand_ar.strip() if req.brand_ar else "",
            trace=trace, 
            strict_brand_match=req.strict_brand_match,
            barcode=req.barcode.strip() if req.barcode else "", 
            category=req.category.strip() if req.category else "", 
            origin=req.origin.strip() if req.origin else "",
            brand_mappings=brand_mappings, 
            skip_cache=req.skip_cache
        )
        
        if best_image:
            return {
                "status": "success",
                "selected_image": best_image,
                "trace": trace,
                "brand": brand
            }
        else:
            return {
                "status": "failed",
                "message": "No matching images passed the quality thresholds",
                "trace": trace,
                "brand": brand
            }
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/select-image")
def select_product_image(req: SelectImageRequest):
    """
    اعتماد صورة معالجة محلياً ورفعها سحابياً لـ Cloudinary وجدولة تحديث الشيت عبر Redis.
    """
    try:
        # 1. معالجة وحذف خلفية الصورة محلياً
        processed_image_path = image_processor.process_product_image(
            req.image_url, req.product_name, req.brand, 
            bg_removal_method=req.bg_removal_method,
            enhance=req.enhance,
            target_width=req.target_width,
            target_height=req.target_height
        )
        
        # قراءة الأبعاد الفعلية للصورة الناتجة بعد معالجتها وتوسيطها (لتمريرها إلى Cloudinary)
        try:
            from PIL import Image
            with Image.open(processed_image_path) as res_img:
                final_w, final_h = res_img.size
        except Exception:
            final_w = req.target_width or 800
            final_h = req.target_height or 800
            
        if not final_w or final_w <= 0:
            final_w = 800
        if not final_h or final_h <= 0:
            final_h = 800
        if processed_image_path == "blurry":
            raise HTTPException(status_code=400, detail="الصورة المختارة مشوشة جداً ومنخفضة الجودة (Blurry Image). يرجى اختيار صورة أخرى.")
            
        if not processed_image_path or not os.path.exists(processed_image_path):
            raise HTTPException(status_code=500, detail="Failed to process image or remove background")
            
        # 2. تكبير الصورة فائقة الجودة لـ Lanczos
        if req.upscale:
            try:
                from PIL import Image
                with Image.open(processed_image_path) as img:
                    w, h = img.size
                    img.resize((w*2, h*2), Image.Resampling.LANCZOS).save(processed_image_path)
            except Exception:
                pass
                
        # 3. استخراج البيانات الوصفية (Gemini Vision)
        metadata = image_processor.extract_metadata_from_image(processed_image_path, req.product_name, req.brand)
        folder = "products"
        tags = []
        
        # دمج التصنيفات المعدلة يدوياً
        if req.category_l1_en:
            import categories
            norm = categories.normalize_category_path(req.category_l1_en, req.category_l2_en, req.category_l3_en)
            if not metadata:
                metadata = {}
            metadata.update(norm)
            
        if metadata:
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
                
        # 4. الرفع إلى Cloudinary
        image_link = cloudinary_storage.upload_product_image_to_cloudinary(
            processed_image_path,
            req.product_name,
            req.brand,
            folder=folder,
            tags=tags,
            target_width=final_w,
            target_height=final_h,
            padding_ratio=req.padding_ratio,
            bg_color=req.bg_color.strip().lstrip('#')
        )
        
        if not image_link:
            raise HTTPException(status_code=500, detail="Cloudinary upload failed")
            
        # 5. استخراج المتجهات الإدراكية للتعلم النشط قبل مسح الملف المؤقت
        clip_embedding = None
        try:
            _, clip_embedding = image_search.check_image_relevance_via_clip(processed_image_path, req.brand, req.product_name)
        except Exception:
            pass
            
        # تنظيف محلي للملف
        try:
            if os.path.exists(processed_image_path):
                os.remove(processed_image_path)
        except Exception:
            pass
            
        # 6. جدولة تحديث Google Sheets عبر Redis (Write-Behind)
        # نقوم بتحديث البيانات فوراً في الكاش المحلي وتعبئتها في قائمة التحديثات المؤجلة
        try:
            import redis
            # الاتصال بخادم Redis المحلي
            r = redis.Redis(host=config.REDIS_HOST, port=config.REDIS_PORT, db=config.REDIS_DB, decode_responses=True)
            
            # حفظ في SQLite الكاش المحلي لمنع التكرار
            local_cache_db.save_product_resolution(
                req.barcode,
                req.product_name,
                req.brand,
                req.image_url,
                image_link,
                1.0,
                metadata,
                clip_embedding
            )
            
            # دفع البيانات إلى Redis
            cache_payload = {
                "row_index": req.row_number,
                "barcode": req.barcode,
                "product_name": req.product_name,
                "brand": req.brand,
                "cloudinary_url": image_link,
                "ingredients": metadata.get("ingredients", "") if metadata else "",
                "categories": metadata.get("category_l1_en", "") if metadata else ""
            }
            
            # حفظ في Redis Hash وجدولته في Set
            r.set(f"product:data:{req.barcode if req.barcode else req.product_name}", json.dumps(cache_payload))
            r.sadd("writebehind:dirty_set", req.barcode if req.barcode else req.product_name)
            
            # تفريغ كاش الكتالوج لضمان جلب الصفحة بشكل صحيح مع التحديث الجديد
            r.delete("laravel_database_laravel_cache:products_json_v1")
            r.delete("laravel_cache:products_json_v1")
            
        except Exception as ree:
            print(f"⚠️ [Redis Cache Write Error] {ree} -> Falling back to Google Sheets direct update...")
            try:
                sheets_client = google_sheets.get_sheets_client()
                if sheets_client:
                    worksheet = google_sheets.open_worksheet(sheets_client, config.SPREADSHEET_NAME_OR_URL)
                    if worksheet:
                        _, link_col_idx = google_sheets.get_products(worksheet)
                        google_sheets.update_image_link(worksheet, req.row_number, link_col_idx, image_link)
                        if metadata:
                            google_sheets.update_product_metadata(worksheet, req.row_number, metadata)
            except Exception as se:
                print(f"❌ [Sheets Fallback Error] {se}")
            
        return {"status": "success", "image_link": image_link, "metadata": metadata}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/reject-image")
def reject_product_image(req: RejectImageRequest):
    """
    استبعاد صورة من الكتالوج وجدولة إفراغ الخلية في شيت جوجل.
    """
    try:
        feedback_id = str(uuid.uuid4())
        asset_id = req.image_url.split("/")[-1].split("?")[0]
        
        # حفظ خطأ الفشل لـ SQLite
        local_cache_db.save_product_failure(
            barcode=None,
            product_name=req.product_name,
            brand=req.brand,
            error_message=f"مستبعدة يدوياً: {', '.join(req.rejection_reasons)}"
        )
        
        # حفظ التغذية الراجعة
        success = local_cache_db.save_feedback(
            feedback_id=feedback_id,
            asset_id=asset_id,
            row_number=req.row_number,
            product_name=req.product_name,
            brand=req.brand,
            image_url=req.image_url,
            reasons=req.rejection_reasons
        )
        
        if not success:
            raise HTTPException(status_code=500, detail="Failed to save feedback to local database")
            
        # جدولة مسح رابط الصورة في الشيت عبر Redis
        try:
            import redis
            r = redis.Redis(host=config.REDIS_HOST, port=config.REDIS_PORT, db=config.REDIS_DB, decode_responses=True)
            cache_payload = {
                "row_index": req.row_number,
                "barcode": "",
                "product_name": req.product_name,
                "brand": req.brand,
                "cloudinary_url": "",
                "ingredients": "",
                "categories": ""
            }
            key = f"product:data:reject_{req.row_number}"
            r.set(key, json.dumps(cache_payload))
            r.sadd("writebehind:dirty_set", f"reject_{req.row_number}")
            
            # مسح كاش الكتالوج
            r.delete("laravel_database_laravel_cache:products_json_v1")
            r.delete("laravel_cache:products_json_v1")
        except Exception as ree:
            print(f"⚠️ [Redis Cache Write Error] {ree} -> Falling back to Google Sheets direct update...")
            try:
                sheets_client = google_sheets.get_sheets_client()
                if sheets_client:
                    worksheet = google_sheets.open_worksheet(sheets_client, config.SPREADSHEET_NAME_OR_URL)
                    if worksheet:
                        _, link_col_idx = google_sheets.get_products(worksheet)
                        google_sheets.update_image_link(worksheet, req.row_number, link_col_idx, "")
            except Exception as se:
                print(f"❌ [Sheets Fallback Error] {se}")
            
        return {"status": "success", "message": "Feedback logged and blank link scheduled in Sheets"}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/upload-manual-image")
def upload_manual_image(req: UploadManualRequest):
    """
    رفع صورة يدوية ومعالجتها بالكامل (اقتصاص، إزالة خلفية، استخراج وصف، رفع Cloudinary، جدولة شيت جوجل).
    """
    try:
        file_path = req.file_path
        if not os.path.exists(file_path):
            raise HTTPException(status_code=404, detail="Uploaded file path not found")
            
        # 1. اقتصاص الصورة تلقائياً
        box = image_processor.get_product_bounding_box(file_path, req.product_name, req.brand)
        if box:
            cropped = file_path + "_cropped.png"
            if image_processor.crop_image_by_box(file_path, box, cropped):
                try: os.remove(file_path)
                except Exception: pass
                os.rename(cropped, file_path)
                
        # 2. إزالة الخلفية
        nobg = file_path + "_nobg.png"
        if image_processor.remove_background(file_path, nobg):
            try: os.remove(file_path)
            except Exception: pass
            os.rename(nobg, file_path)
            
        # 3. تحسين دقة تفاصيل الصورة لـ Lanczos
        if req.enhance:
            enhanced = file_path + "_enhanced.png"
            if image_processor.enhance_image_quality(file_path, enhanced):
                try: os.remove(file_path)
                except Exception: pass
                os.rename(enhanced, file_path)
            
        # 4. استخراج البيانات الوصفية بـ Gemini Vision
        metadata = image_processor.extract_metadata_from_image(file_path, req.product_name, req.brand)
        folder = "products"
        tags = []
        
        # تحديد الأبعاد ديناميكياً للرفع اليدوي لحماية الجودة والبيكسلات
        try:
            from PIL import Image
            with Image.open(file_path) as res_img:
                final_w, final_h = res_img.size
                max_dim = max(final_w, final_h)
                dynamic_dim = max(800, min(max_dim, 1600))
                final_w = dynamic_dim
                final_h = dynamic_dim
        except Exception:
            final_w = req.target_width or 800
            final_h = req.target_height or 800
            
        if not final_w or final_w <= 0:
            final_w = 800
        if not final_h or final_h <= 0:
            final_h = 800
        
        if metadata:
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
                
        # 5. الرفع لـ Cloudinary
        image_link = cloudinary_storage.upload_product_image_to_cloudinary(
            file_path,
            req.product_name,
            req.brand,
            folder=folder,
            tags=tags,
            target_width=final_w,
            target_height=final_h,
            padding_ratio=req.padding_ratio,
            bg_color=req.bg_color.strip().lstrip('#')
        )
        
        # حذف الصورة المحلية المؤقتة
        try: os.remove(file_path)
        except Exception: pass
        
        if not image_link:
            raise HTTPException(status_code=500, detail="Failed to upload manual image to Cloudinary")
            
        # 6. جدولة التحديث في Google Sheets
        try:
            import redis
            r = redis.Redis(host=config.REDIS_HOST, port=config.REDIS_PORT, db=config.REDIS_DB, decode_responses=True)
            
            # حفظ في SQLite الكاش المحلي لمنع التكرار
            local_cache_db.save_product_resolution(
                req.barcode,
                req.product_name,
                req.brand,
                "manual_upload",
                image_link,
                1.0,
                metadata,
                None
            )
            
            cache_payload = {
                "row_index": req.row_number,
                "barcode": req.barcode,
                "product_name": req.product_name,
                "brand": req.brand,
                "cloudinary_url": image_link,
                "ingredients": metadata.get("ingredients", "") if metadata else "",
                "categories": metadata.get("category_l1_en", "") if metadata else ""
            }
            
            r.set(f"product:data:{req.barcode if req.barcode else req.product_name}", json.dumps(cache_payload))
            r.sadd("writebehind:dirty_set", req.barcode if req.barcode else req.product_name)
            
            r.delete("laravel_database_laravel_cache:products_json_v1")
            r.delete("laravel_cache:products_json_v1")
        except Exception as ree:
            print(f"⚠️ [Redis Cache Write Error] {ree} -> Falling back to Google Sheets direct update...")
            try:
                sheets_client = google_sheets.get_sheets_client()
                if sheets_client:
                    worksheet = google_sheets.open_worksheet(sheets_client, config.SPREADSHEET_NAME_OR_URL)
                    if worksheet:
                        _, link_col_idx = google_sheets.get_products(worksheet)
                        google_sheets.update_image_link(worksheet, req.row_number, link_col_idx, image_link)
                        if metadata:
                            google_sheets.update_product_metadata(worksheet, req.row_number, metadata)
            except Exception as se:
                print(f"❌ [Sheets Fallback Error] {se}")
            
        return {"status": "success", "image_link": image_link, "metadata": metadata}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/batch-status")
def get_batch_status():
    """
    مراقبة حالة تشغيل خط المعالجة الكلي بالخلفية.
    """
    lock_file = 'temp/pipeline.lock'
    is_running = False
    
    if os.path.exists(lock_file):
        try:
            with open(lock_file, "r") as f:
                pid = f.read().strip()
            if pid.isdigit():
                # التحقق في بيئة نظام Windows من تشغيل العملية
                import subprocess
                output = subprocess.check_output(f'tasklist /FI "PID eq {pid}"', shell=True).decode('utf-8')
                if pid in output:
                    is_running = True
                else:
                    os.remove(lock_file)
        except Exception:
            pass
            
    return {"is_running": is_running}

if __name__ == "__main__":
    import uvicorn
    # تشغيل الخادم على المنفذ 8001
    uvicorn.run(app, host="127.0.0.1", port=8001)
