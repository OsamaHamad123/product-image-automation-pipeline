# google_sheets.py
# موديول التعامل مع Google Sheets وجلب وتحديث بيانات المنتجات

import time
import os
import json
import random
import gspread
import pymysql
import threading
from gspread.exceptions import APIError
from oauth2client.service_account import ServiceAccountCredentials
import config

_queue = None
_worker = None

def clear_cache():
    cache_dir = os.path.dirname(os.path.abspath(__file__))
    p_cache = os.path.join(cache_dir, "products_cache.json")
    b_cache = os.path.join(cache_dir, "brand_mappings_cache.json")
    for f in [p_cache, b_cache]:
        if os.path.exists(f):
            try:
                os.remove(f)
                print(f"🧹 [Google Sheets Cache] Cleared cache file: {os.path.basename(f)}")
            except Exception:
                pass

class SQLiteTransactionQueue:
    def __init__(self, db_path=None):
        self._setup_schema()

    def _connect(self):
        return pymysql.connect(
            host=os.getenv("DB_HOST", "127.0.0.1"),
            port=int(os.getenv("DB_PORT", "3306")),
            user=os.getenv("DB_USERNAME", "root"),
            password=os.getenv("DB_PASSWORD", ""),
            database=os.getenv("DB_DATABASE", "automation_db"),
            charset='utf8mb4',
            cursorclass=pymysql.cursors.DictCursor
        )

    def _setup_schema(self):
        conn = self._connect()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS sheet_updates (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    row_number INT NOT NULL,
                    col_index INT NOT NULL,
                    value TEXT NOT NULL,
                    registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    sync_status VARCHAR(255) DEFAULT 'PENDING'
                ) ENGINE=InnoDB CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
            """)
            conn.commit()
        finally:
            conn.close()

    def append_update(self, row_number, col_index, value):
        conn = self._connect()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO sheet_updates (row_number, col_index, value) VALUES (%s, %s, %s)",
                (row_number, col_index, value)
            )
            conn.commit()
        finally:
            conn.close()
        clear_cache()



class GoogleSheetsBatchWorker(threading.Thread):
    def __init__(self, queue, credentials_json_path, spreadsheet_name_or_url, sync_interval=5):
        super().__init__()
        self.queue = queue
        self.creds_path = credentials_json_path
        self.spreadsheet_name = spreadsheet_name_or_url
        self.sync_interval = sync_interval
        self._exit_signal = threading.Event()
        self.daemon = True

    def stop_gracefully(self):
        self._exit_signal.set()

    def run(self):
        client = None
        worksheet = None
        
        while not self._exit_signal.is_set():
            try:
                if client is None or worksheet is None:
                    import google_sheets
                    sheets_client = google_sheets.get_sheets_client()
                    if sheets_client:
                        client = sheets_client
                        worksheet = google_sheets.open_worksheet(client, self.spreadsheet_name)
                
                if worksheet:
                    self._synchronize_pending_records(worksheet)
            except Exception as e:
                print(f"⚠️ [GoogleSheetsBatchWorker Error] {e}")
                client = None
                worksheet = None
                
            self._exit_signal.wait(self.sync_interval)
            
        if worksheet:
            try:
                self._synchronize_pending_records(worksheet)
            except Exception:
                pass

    def _synchronize_pending_records(self, worksheet):
        conn = self.queue._connect()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, row_number, col_index, value FROM sheet_updates WHERE sync_status = 'PENDING' LIMIT 100"
            )
            rows = cursor.fetchall()
            
            if not rows:
                return
                
            print(f"🔄 [Async Sheets Sync] Synchronizing {len(rows)} pending cell updates to Google Sheets...")
            batch_data = []
            row_ids = []
            
            for r in rows:
                row_id = r["id"]
                r_num = r["row_number"]
                c_idx = r["col_index"]
                val = r["value"]
                
                row_ids.append(row_id)
                a1_range = gspread.utils.rowcol_to_a1(r_num, c_idx + 1)
                batch_data.append({
                    "range": a1_range,
                    "values": [[str(val)]]
                })
                
            try:
                worksheet.batch_update(batch_data, value_input_option="USER_ENTERED")
                id_placeholders = ",".join("%s" for _ in row_ids)
                cursor.execute(
                    f"UPDATE sheet_updates SET sync_status = 'SYNCED' WHERE id IN ({id_placeholders})",
                    row_ids
                )
                conn.commit()
                print(f"✅ [Async Sheets Sync] Successfully synced {len(rows)} updates.")
                clear_cache()
            except Exception as e:
                print(f"❌ [Async Sheets Sync Error] {e}")
        finally:
            conn.close()

def init_async_queue(creds_path, spreadsheet_name, sync_interval=5):
    global _queue, _worker
    _queue = SQLiteTransactionQueue()
    _worker = GoogleSheetsBatchWorker(_queue, creds_path, spreadsheet_name, sync_interval)
    _worker.start()
    print("🚀 [Async Sheets Sync] Background batch worker started successfully.")

def stop_async_queue():
    global _worker
    if _worker:
        _worker.stop_gracefully()
        _worker.join(timeout=10)
        _worker = None
        print("🛑 [Async Sheets Sync] Background batch worker stopped.")

def retry_gspread_on_429(max_retries=5):
    """
    مُزخرف (Decorator) لإعادة محاولة استدعاءات Google API عند تلقي الأخطاء 429 أو 5xx
    باستخدام ارتداد لوغاريتمي عشوائي (Exponential Backoff with Jitter).
    """
    def decorator(func):
        def wrapper(*args, **kwargs):
            retry = 0
            while retry <= max_retries:
                try:
                    return func(*args, **kwargs)
                except APIError as e:
                    if e.code in [429, 500, 503]:
                        retry += 1
                        if retry > max_retries:
                            raise e
                        backoff = min((2 ** retry) + random.uniform(0.1, 1.0), 32)
                        print(f"⚠️ [Google Sheets API] تفعيل الارتداد اللوغاريتمي المحمي. إعادة محاولة {retry}/{max_retries} خلال {backoff:.2f} ثانية...")
                        time.sleep(backoff)
                    else:
                        raise e
                except Exception as e:
                    raise e
            return None
        return wrapper
    return decorator

def get_sheets_client():
    """
    الاتصال بـ Google Sheets API باستخدام ملف الاعتمادات من الإعدادات.
    """
    try:
        gc = gspread.service_account(filename=config.CREDENTIALS_FILE)
        return gc
    except Exception as e:
        print(f"❌ فشل الاتصال بـ Google Sheets API: {e}")
        return None

def open_worksheet(client, sheet_name_or_url, worksheet_index=0):
    """
    فتح ورقة العمل المحددة بالاسم أو الرابط.
    """
    try:
        if sheet_name_or_url.startswith("https://"):
            sh = client.open_by_url(sheet_name_or_url)
        else:
            sh = client.open(sheet_name_or_url)
        return sh.get_worksheet(worksheet_index)
    except Exception as e:
        print(f"❌ فشل فتح جدول البيانات '{sheet_name_or_url}': {e}")
        return None

def get_product_columns_indices(headers):
    """
    تحديد فهارس (Indices) الأعمدة المهمة ديناميكياً من خلال عناوين الجدول مع تحديد أولويات صارمة.
    """
    name_indices = [i for i, h in enumerate(headers) if h.lower() in ["productname", "product name", "اسم المنتج"]]
    brand_indices = [i for i, h in enumerate(headers) if h.lower() in ["brand", "البراند", "العلامة التجارية"]]
    barcode_indices = [i for i, h in enumerate(headers) if h.lower() in ["barcode", "باركود", "الباركود"]]
    category_indices = [i for i, h in enumerate(headers) if h.lower() in ["category", "الفئة", "التصنيف"]]
    origin_indices = [i for i, h in enumerate(headers) if h.lower() in ["origin", "بلد المنشأ", "المنشأ"]]

    name_idx = name_indices[0] if name_indices else 2
    brand_idx = brand_indices[0] if brand_indices else 4
    
    # تحديد أولوية عمود الرابط: نبحث عن drive image link أولاً ثم البدائل لمنع الخلط مع عمود images العام
    link_idx = -1
    for term in ["drive image link", "image link", "رابط الصورة", "images"]:
        indices = [i for i, h in enumerate(headers) if h.lower().strip() == term]
        if indices:
            link_idx = indices[0]
            break

    barcode_idx = barcode_indices[0] if barcode_indices else -1
    category_idx = category_indices[0] if category_indices else -1
    origin_idx = origin_indices[0] if origin_indices else -1

    return name_idx, brand_idx, link_idx, barcode_idx, category_idx, origin_idx

def get_products(worksheet):
    """
    جلب جميع المنتجات من الشيت مع تحديد رقم الصف لكل منتج لتسهيل التحديث لاحقاً.
    """
    cache_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "products_cache.json")
    ttl = 3600
    if os.path.exists(cache_file):
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                cached = json.load(f)
            if time.time() - cached.get("timestamp", 0) < ttl:
                print("⚡ [Google Sheets Cache] Loaded products list from cache file.")
                return cached["products"], cached["link_idx"]
        except Exception as ce:
            print(f"⚠️ [Google Sheets Cache] Failed to load cache: {ce}")

    try:
        rows = worksheet.get_all_values()
        if not rows or len(rows) <= 1:
            print("⚠️ لا توجد بيانات في الشيت (أو يوجد صف العناوين فقط).")
            return [], -1

        headers = rows[0]
        name_idx, brand_idx, link_idx, barcode_idx, category_idx, origin_idx = get_product_columns_indices(headers)

        # إذا لم يكن عمود الرابط موجوداً، نقوم بإنشائه في نهاية الجدول
        if link_idx == -1:
            link_idx = len(headers)
            new_column_name = "Drive Image Link"
            worksheet.update_cell(1, link_idx + 1, new_column_name)
            print(f"ℹ️ تم إنشاء عمود جديد لحفظ روابط الصور باسم '{new_column_name}' في العمود رقم {link_idx + 1}")

        # جلب مرادفات البراندات للمطابقة التلقائية
        brand_mappings = {}
        try:
            sheets_client = get_sheets_client()
            if sheets_client:
                brand_mappings = get_brand_mappings(sheets_client, worksheet.spreadsheet.url)
        except Exception as e:
            print(f"⚠️ فشل جلب مرادفات البراندات أثناء قراءة المنتجات: {e}")

        name_ar_indices = [i for i, h in enumerate(headers) if h.lower() in ["productname arabic", "product name arabic", "اسم المنتج بالعربي", "اسم المنتج عربي"]]
        brand_ar_indices = [i for i, h in enumerate(headers) if h.lower() in ["brand arabic", "brand_arabic", "البراند بالعربي", "البراند عربي"]]
        sub_sub_indices = [i for i, h in enumerate(headers) if h.lower() in ["sub sub category", "sub_sub_category"]]
        sub_sub_ar_indices = [i for i, h in enumerate(headers) if h.lower() in ["sub sub category arabic", "sub_sub_category_arabic"]]
        
        name_ar_idx = name_ar_indices[0] if name_ar_indices else -1
        brand_ar_idx = brand_ar_indices[0] if brand_ar_indices else -1
        sub_sub_idx = sub_sub_indices[0] if sub_sub_indices else -1
        sub_sub_ar_idx = sub_sub_ar_indices[0] if sub_sub_ar_indices else -1

        products = []
        # تبدأ الحلقة من الصف الثاني (الفهرس 1) لأن الصف الأول يحتوي على العناوين
        for idx, row in enumerate(rows[1:], start=2):
            product_name = row[name_idx].strip() if name_idx < len(row) else ""
            brand = row[brand_idx].strip() if brand_idx < len(row) else ""
            
            # استخراج البراند تلقائياً إذا كان فارغاً
            if product_name and not brand:
                extracted = extract_brand_from_name(product_name, brand_mappings)
                if extracted:
                    brand = extracted
                    print(f"💡 [Auto Brand] تم استخراج البراند '{brand}' تلقائياً لـ '{product_name}' من جدول المرادفات.")
                else:
                    extracted = extract_brand_from_start(product_name, brand_mappings)
                    if extracted:
                        brand = extracted
                        print(f"💡 [Auto Brand] تم استخراج البراند '{brand}' تلقائياً لـ '{product_name}' من بداية الاسم.")

            product_name_ar = row[name_ar_idx].strip() if (name_ar_idx != -1 and name_ar_idx < len(row)) else ""
            brand_ar = row[brand_ar_idx].strip() if (brand_ar_idx != -1 and brand_ar_idx < len(row)) else ""
            sub_sub_category = row[sub_sub_idx].strip() if (sub_sub_idx != -1 and sub_sub_idx < len(row)) else ""
            sub_sub_category_ar = row[sub_sub_ar_idx].strip() if (sub_sub_ar_idx != -1 and sub_sub_ar_idx < len(row)) else ""
            
            barcode = row[barcode_idx].strip() if (barcode_idx != -1 and barcode_idx < len(row)) else ""
            category = row[category_idx].strip() if (category_idx != -1 and category_idx < len(row)) else ""
            origin = row[origin_idx].strip() if (origin_idx != -1 and origin_idx < len(row)) else ""
            
            # قراءة الرابط الحالي إذا كان العمود موجوداً وبه قيمة
            existing_link = row[link_idx].strip() if link_idx < len(row) else ""
            
            needs_review = False
            needs_review_url = ""
            if existing_link.startswith("needs_review:"):
                needs_review = True
                needs_review_url = existing_link.replace("needs_review:", "").strip()
                existing_link = "" # نعتبر الرابط الرئيسي فارغاً ليظهر في طابور الأتمتة والمراجعة

            # نقوم فقط بمعالجة الصفوف التي تحتوي على اسم منتج على الأقل
            if product_name:
                products.append({
                    "row_number": idx,
                    "product_name": product_name,
                    "product_name_ar": product_name_ar,
                    "brand": brand,
                    "brand_ar": brand_ar,
                    "sub_sub_category": sub_sub_category,
                    "sub_sub_category_ar": sub_sub_category_ar,
                    "barcode": barcode,
                    "category": category,
                    "origin": origin,
                    "existing_image_link": existing_link,
                    "needs_review": needs_review,
                    "needs_review_url": needs_review_url,
                    "search_query": f"{product_name} {brand}".strip()
                })
        
        # Save cache
        try:
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump({
                    "timestamp": time.time(),
                    "products": products,
                    "link_idx": link_idx
                }, f, ensure_ascii=False, indent=2)
            print("💾 [Google Sheets Cache] Products list cached successfully.")
        except Exception as ce:
            print(f"⚠️ [Google Sheets Cache] Failed to write cache: {ce}")

        return products, link_idx

    except Exception as e:
        print(f"❌ حدث خطأ أثناء قراءة المنتجات من الشيت: {e}")
        return [], -1

def _redis_write_behind(row_number, col_idx, value):
    try:
        import redis
        import json
        r = redis.Redis(host=config.REDIS_HOST, port=config.REDIS_PORT, db=config.REDIS_DB, socket_timeout=0.2, decode_responses=True)
        key = f"row_{row_number}"
        cached = r.get(f"product:data:{key}")
        if cached:
            payload = json.loads(cached)
        else:
            payload = {"row_index": row_number, "updates": {}}
        payload["updates"][str(col_idx)] = value
        r.set(f"product:data:{key}", json.dumps(payload))
        r.sadd("writebehind:dirty_set", key)
        
        # تفريغ كاش الكتالوج لضمان التحديث في المتصفح
        r.delete("laravel_database_laravel_cache:products_json_v1")
        r.delete("laravel_cache:products_json_v1")
        return True
    except Exception as e:
        print(f"⚠️ [Redis Write-Behind Fallback] {e}")
        return False

@retry_gspread_on_429()
def update_image_link(worksheet, row_number, link_column_index, image_link):
    """
    تحديث خلية رابط الصورة لصف منتج معين.
    """
    if _redis_write_behind(row_number, link_column_index, image_link):
        print(f"⏳ [Redis Write-Behind] تمت جدولة تحديث الرابط في الصف {row_number} عبر Redis.")
        return True

    global _queue, _worker
    if _queue is not None and _worker is not None:
        _queue.append_update(row_number, link_column_index, image_link)
        print(f"⏳ [Queue Sheets Update] تمت جدولة تحديث الرابط في الصف {row_number} في طابور الخلفية.")
        return True
        
    try:
        # gspread يعتمد على ترقيم 1-indexed للأعمدة والصفوف
        worksheet.update_cell(row_number, link_column_index + 1, image_link)
        print(f"✅ تم تحديث الرابط في الصف {row_number} بنجاح.")
        clear_cache()
        return True
    except Exception as e:
        print(f"❌ فشل تحديث الرابط في الصف {row_number}: {e}")
        return False

def get_brand_mappings(client, sheet_name_or_url):
    """
    جلب مرادفات البراندات والمنافسين المستبعدين من ورقة العمل 'Brands Mapping'.
    إذا لم تكن موجودة، يتم إنشاؤها تلقائياً وتعبئتها بالقيم الافتراضية.
    """
    cache_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "brand_mappings_cache.json")
    ttl = 300
    if os.path.exists(cache_file):
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                cached = json.load(f)
            if time.time() - cached.get("timestamp", 0) < ttl:
                print("⚡ [Google Sheets Cache] Loaded brand mappings from cache file.")
                return cached["mappings"]
        except Exception as ce:
            print(f"⚠️ [Google Sheets Cache] Failed to load brand mappings cache: {ce}")

    try:
        if sheet_name_or_url.startswith("https://"):
            sh = client.open_by_url(sheet_name_or_url)
        else:
            sh = client.open(sheet_name_or_url)
            
        # محاولة فتح ورقة العمل
        try:
            worksheet = sh.worksheet("Brands Mapping")
        except gspread.exceptions.WorksheetNotFound:
            print("ℹ️ ورقة العمل 'Brands Mapping' غير موجودة. جاري إنشاؤها تلقائياً بالقيم الافتراضية...")
            # إنشاء الورقة الجديدة بـ 3 أعمدة
            worksheet = sh.add_worksheet(title="Brands Mapping", rows="100", cols="3")
            
            # العناوين والبيانات الافتراضية
            headers = ["Brand", "Synonyms", "Excluded Competitors"]
            default_rows = [
                headers,
                ["Meliha", "Mleiha, مليحة, مليحه", "Almarai, Sutas, Koita, Lacnor, Baladna, Al Rawabi, Nadec, Nada"],
                ["Saba Sanabel", "Sabaa Sanabel, سبع سنابل, صبا سنابل, سنابل", "Al Baker, Jenan, Grand Mills, Organic Larder"],
                ["Mai Dubai", "May Dubai, ماي دبي, مي دبي, مياه دبي", "Masafi, Al Ain, Oasis, Arwa, Aquafina, Nestle Pure Life, Voss, Evian"],
                ["Almarai", "Al Marai, المراعي", "Sutas, Koita, Lacnor, Baladna, Al Rawabi, Nadec, Nada, Meliha, Mleiha"],
                ["Masafi", "مسافي", "Al Ain, Oasis, Arwa, Aquafina, Nestle Pure Life, Mai Dubai, Voss, Evian"],
                ["Al Ain", "العين, alain", "Masafi, Oasis, Arwa, Aquafina, Nestle Pure Life, Mai Dubai, Voss, Evian"]
            ]
            
            # تحديث الورقة بالقيم الافتراضية
            worksheet.update("A1:C7", default_rows)
            print("✅ تم إنشاء ورقة 'Brands Mapping' وتعبئتها بالقيم الافتراضية بنجاح.")
            
        # قراءة جميع الصفوف
        rows = worksheet.get_all_values()
        if not rows or len(rows) <= 1:
            return {}
            
        mappings = {}
        
        for r in rows[1:]:
            if len(r) > 0 and r[0].strip():
                brand_name = r[0].strip().lower()
                syns = [s.strip() for s in r[1].split(",") if s.strip()] if len(r) > 1 else []
                comps = [c.strip() for c in r[2].split(",") if c.strip()] if len(r) > 2 else []
                
                # إضافة البراند نفسه لقائمة المرادفات لضمان وجوده
                if r[0].strip() not in syns:
                    syns.insert(0, r[0].strip())
                    
                mappings[brand_name] = {
                    "brand": r[0].strip(),
                    "synonyms": syns,
                    "excluded_competitors": comps
                }
                
        # Save cache
        try:
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump({
                    "timestamp": time.time(),
                    "mappings": mappings
                }, f, ensure_ascii=False, indent=2)
            print("💾 [Google Sheets Cache] Brand mappings cached successfully.")
        except Exception as ce:
            print(f"⚠️ [Google Sheets Cache] Failed to write brand mappings cache: {ce}")

        return mappings
    except Exception as e:
        print(f"❌ حدث خطأ أثناء جلب مرادفات البراندات من الشيت: {e}")
        return {}

@retry_gspread_on_429()
def update_product_metadata(worksheet, row_number, metadata):
    """
    تحديث شيت البيانات بالقيم الغذائية والمكونات والوصف التسويقي دفعة واحدة (Batch Update).
    """
    try:
        # 1. قراءة صف العناوين الأول
        headers = worksheet.row_values(1)
        
        # 2. البحث عن الأعمدة أو إضافتها إن لم تكن موجودة
        col_names = {
            "nutrition": "Nutrition Facts",
            "ingredients": "Ingredients",
            "description_en": "Description EN",
            "description_ar": "Description AR",
            "category_l1_en": "Category L1 EN",
            "category_l2_en": "Category L2 EN",
            "category_l3_en": "Category L3 EN",
            "category_l1_ar": "Category L1 AR",
            "category_l2_ar": "Category L2 AR",
            "category_l3_ar": "Category L3 AR",
            "tags_en": "Tags EN",
            "tags_ar": "Tags AR"
        }
        
        col_indices = {}
        header_changed = False
        
        for key, name in col_names.items():
            found_idx = -1
            for idx, h in enumerate(headers):
                if h.strip().lower() == name.lower():
                    found_idx = idx
                    break
            
            if found_idx == -1:
                found_idx = len(headers)
                headers.append(name)
                try:
                    if found_idx + 1 > worksheet.col_count:
                        cols_to_add = (found_idx + 1) - worksheet.col_count
                        worksheet.add_cols(cols_to_add)
                        print(f"ℹ️ تم توسيع أعمدة الشيت بإضافة {cols_to_add} أعمدة جديدة.")
                except Exception as ex:
                    print(f"⚠️ فشل توسيع أعمدة الشيت تلقائياً: {ex}")
                worksheet.update_cell(1, found_idx + 1, name)
                header_changed = True
                print(f"ℹ️ تم إنشاء عمود جديد '{name}' في العمود رقم {found_idx + 1}")
                
            col_indices[key] = found_idx
            
        # 3. محاولة الحفظ عبر Redis Write-Behind
        try:
            import redis
            import json
            r = redis.Redis(host=config.REDIS_HOST, port=config.REDIS_PORT, db=config.REDIS_DB, socket_timeout=0.2, decode_responses=True)
            key = f"row_{row_number}"
            
            cached = r.get(f"product:data:{key}")
            if cached:
                payload = json.loads(cached)
            else:
                payload = {"row_index": row_number, "updates": {}}
                
            for k, val in metadata.items():
                if k in col_indices and val:
                    payload["updates"][str(col_indices[k])] = val
                    
            r.set(f"product:data:{key}", json.dumps(payload))
            r.sadd("writebehind:dirty_set", key)
            
            # تفريغ كاش الكتالوج
            r.delete("laravel_database_laravel_cache:products_json_v1")
            r.delete("laravel_cache:products_json_v1")
            print(f"⏳ [Redis Write-Behind] تمت جدولة تحديث {len(metadata)} حقول وصفية للمنتج في الصف {row_number} عبر Redis.")
            return True
        except Exception as ree:
            print(f"⚠️ [Redis Write-Behind Fallback] {ree}")
            
        # 4. تحديث خلايا البيانات للصف المعني (SQLite / direct fallback)
        global _queue, _worker
        if _queue is not None and _worker is not None:
            for key, val in metadata.items():
                if key in col_indices and val:
                    _queue.append_update(row_number, col_indices[key], val)
            print(f"⏳ [Queue Sheets Update] تمت جدولة تحديث {len(metadata)} حقول وصفية للمنتج في الصف {row_number} في الخلفية.")
            return True

        # مسار الكتابة الفوري المتزامن كخيار بديل
        batch_data = []
        for key, val in metadata.items():
            if key in col_indices and val:
                col_letter = gspread.utils.rowcol_to_a1(row_number, col_indices[key] + 1)
                batch_data.append({
                    "range": col_letter,
                    "values": [[str(val)]]
                })
                
        if batch_data:
            worksheet.batch_update(batch_data, value_input_option="USER_ENTERED")
                
        print(f"✅ [Batch Update Sheets] تم تحديث {len(batch_data)} حقول وصفية للمنتج بنجاح في الصف {row_number}.")
        return True
    except Exception as e:
        print(f"❌ فشل تحديث البيانات الوصفية للمنتج في الصف {row_number}: {e}")
        return False

def extract_brand_from_name(product_name, brand_mappings):
    """
    البحث عن اسم البراند أو أحد مرادفاته داخل اسم المنتج وتوحيده للاسم المعتمد.
    """
    import re
    if not product_name or not brand_mappings:
        return ""
        
    prod_name_lower = product_name.lower().strip()
    
    # البحث عن تطابق مباشر أو مرادفات للبراند
    for brand_key, mapping in brand_mappings.items():
        # نفحص قائمة المرادفات (التي تحتوي أيضاً على البراند نفسه)
        for synonym in mapping.get("synonyms", []):
            syn_lower = synonym.lower().strip()
            if not syn_lower:
                continue
            
            # نتحقق من أن المرادف موجود ككلمة كاملة بحدود الكلمة أو substring إن كان طوله أكبر من 2 لمنع المطابقات الخاطئة
            pattern = rf"\b{re.escape(syn_lower)}\b"
            if re.search(pattern, prod_name_lower) or (len(syn_lower) > 2 and syn_lower in prod_name_lower):
                return mapping["brand"]
                    
    return ""

def extract_brand_via_gemini(product_name):
    """
    استخراج اسم البراند (العلامة التجارية) من اسم المنتج باستخدام Gemini 3.5 Flash كخيار بديل.
    """
    import requests
    import json
    
    if not config.GEMINI_API_KEY or not product_name:
        return ""
        
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{config.GEMINI_MODEL}:generateContent?key={config.GEMINI_API_KEY}"
        
        prompt = (
            f"Extract the brand name (manufacturer/brand name) from this e-commerce product name: '{product_name}'.\n"
            f"If there is a clear brand name, return only that brand name (e.g. 'Meliha', 'Mai Dubai', 'Almarai', 'Baladna', 'Lacnor').\n"
            f"If there is no brand name, reply with 'Unknown'.\n"
            f"Reply strictly in JSON format matching this schema:\n"
            f'{{\n'
            f'  "brand": "extracted brand name or Unknown"\n'
            f'}}'
        )
        
        payload = {
            "contents": [
                {
                    "parts": [
                        {"text": prompt}
                    ]
                }
            ],
            "generationConfig": {
                "responseMimeType": "application/json"
            }
        }
        
        headers = {"Content-Type": "application/json"}
        print(f"🤖 [Auto Brand] جاري استخراج اسم البراند لـ '{product_name}' عبر Gemini 3.5 Flash...")
        
        # update API calls metric if configuration dict exists
        if hasattr(config, "METRICS") and "gemini_api_calls" in config.METRICS:
            config.METRICS["gemini_api_calls"] += 1
            
        response = requests.post(url, headers=headers, json=payload, timeout=10)
        if response.status_code == 200:
            res_data = response.json()
            text_response = res_data['candidates'][0]['content']['parts'][0]['text'].strip()
            if text_response.startswith("```json"):
                text_response = text_response[7:]
            elif text_response.startswith("```"):
                text_response = text_response[3:]
            if text_response.endswith("```"):
                text_response = text_response[:-3]
            text_response = text_response.strip()
            
            result = json.loads(text_response)
            brand = result.get("brand", "").strip()
            if brand.lower() == "unknown":
                return ""
            return brand
    except Exception as e:
        print(f"⚠️ خطأ أثناء استخراج اسم البراند بـ Gemini: {e}")
        
    return ""

def extract_brand_from_start(product_name, brand_mappings):
    """
    محاولة استخراج البراند من بداية اسم المنتج إذا لم يكن مسجلاً في ورقة المرادفات.
    نتخطى الكلمات الوصفية الشائعة (مثل Organic, Fresh, حليب، إلخ).
    """
    import re
    if not product_name:
        return ""
        
    # 1. تنظيف اسم المنتج
    name_clean = product_name.strip()
    words = name_clean.split()
    if not words:
        return ""
        
    # كلمات وصفية شائعة باللغة الإنجليزية والعربية يجب ألا نعتبرها براندات
    skip_words = {
        # English descriptive/generic words
        "organic", "fresh", "pure", "natural", "long", "whole", "chakki", "drinking", "water",
        "chocolate", "local", "premium", "frozen", "sweet", "salted", "unsalted", "green", 
        "red", "white", "black", "low", "fat", "full", "skimmed", "light", "lite", "diet", 
        "healthy", "daily", "fine", "golden", "royal", "classic", "original", "extra", 
        "virgin", "powdered", "instant", "canned", "sliced", "milk", "bread", "cheese",
        "butter", "juice", "yogurt", "flour", "oil", "ghee", "sugar", "salt", "rice", "tea", "coffee",
        # Arabic descriptive/generic words
        "حليب", "لبن", "زبادي", "قشطة", "طحين", "دقيق", "مياه", "ماء", "عصير", "شراب", "جبن", 
        "جبنة", "زبدة", "سمن", "زيت", "شوكولاتة", "كاكاو", "شاي", "قهوة", "سكر", "ملح", "أرز", 
        "خبز", "توست", "بسكويت", "كعك", "حلوى", "عسل", "مربى", "صلصة", "معجون", "خضار", "فواكه", 
        "طازج", "عضوي", "طبيعي", "سادة", "كامل", "قليل", "خالي", "الدسم", "لايت", "دايت", "مبخر",
        "كيس", "علبة", "كرتون"
    }
    
    first_word = words[0].lower().strip(",.-()\"'")
    
    # إذا كانت الكلمة الأولى كلمة وصفية شائعة، نتخطاها
    if first_word in skip_words:
        # ربما الكلمة الثانية هي البراند؟ مثل "Organic Meliha Milk"
        if len(words) > 1:
            second_word = words[1].lower().strip(",.-()\"'")
            if second_word not in skip_words and len(second_word) > 2:
                # نرجع الكلمة الثانية بالصيغة الأصلية (مرفوعة الحروف الأولى)
                return words[1].strip(",.-()\"'")
        return ""
        
    # نتحقق إذا كانت الكلمة الأولى "Al" أو "El" أو "Abu" أو "Mai" أو "May" أو "Saba" أو "Sabaa"
    # فغالباً البراند يتكون من كلمتين
    two_word_starters = {"al", "el", "abu", "mai", "may", "saba", "sabaa", "grand", "new", "old"}
    if first_word in two_word_starters and len(words) > 1:
        second_word = words[1].lower().strip(",.-()\"'")
        if second_word not in skip_words:
            return f"{words[0]} {words[1]}".strip(",.-()\"'")
            
    if len(first_word) > 2:
        return words[0].strip(",.-()\"'")
        
    return ""

