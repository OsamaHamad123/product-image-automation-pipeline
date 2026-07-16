# local_cache_db.py
# موديول التخزين المحلي الذكي باستخدام قاعدة بيانات SQLite لتسريع المطابقة ومنع تكرار البحث

import sqlite3
import os
import json
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "local_cache.db")

def init_db():
    """
    إنشاء قاعدة البيانات وجداولها الأساسية إذا لم تكن موجودة.
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # إنشاء جدول حفظ نتائج مطابقة المنتجات وصورها سحابياً مع البيانات الوصفية
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS resolved_products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                barcode TEXT,
                product_name TEXT NOT NULL,
                brand TEXT,
                original_url TEXT,
                cloudinary_url TEXT NOT NULL,
                clip_score REAL,
                metadata_json TEXT,
                clip_embedding_json TEXT,
                resolved_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # إنشاء جدول تتبع وإدارة أخطاء الأتمتة التقنية
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS product_failures (
                barcode TEXT PRIMARY KEY,
                product_name TEXT NOT NULL,
                brand TEXT,
                error_message TEXT,
                failed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # إنشاء جدول حفظ التغذية الراجعة للتعلم النشط
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS active_learning_feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                feedback_id TEXT UNIQUE,
                asset_id TEXT,
                row_number INTEGER,
                product_name TEXT,
                brand TEXT,
                image_url TEXT,
                rejection_reasons TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # إنشاء جدول طابور المهام المنظم
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS automation_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                row_number INTEGER UNIQUE,
                barcode TEXT,
                product_name TEXT NOT NULL,
                brand TEXT,
                search_query TEXT,
                status TEXT DEFAULT 'pending',
                error_message TEXT,
                attempts INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # التحقق من وجود عمود clip_embedding_json وإضافته إن لم يكن موجوداً (Auto-migration)
        cursor.execute("PRAGMA table_info(resolved_products)")
        columns = [col[1] for col in cursor.fetchall()]
        if "clip_embedding_json" not in columns:
            cursor.execute("ALTER TABLE resolved_products ADD COLUMN clip_embedding_json TEXT")
            print("💾 [SQLite Cache] تم إضافة عمود clip_embedding_json لجدول المتجهات بنجاح.")
            
        # التحقق من وجود عمود perceptual_hash وإضافته إن لم يكن موجوداً (Auto-migration)
        if "perceptual_hash" not in columns:
            cursor.execute("ALTER TABLE resolved_products ADD COLUMN perceptual_hash TEXT")
            print("💾 [SQLite Cache] تم إضافة عمود perceptual_hash لجدول البصمات الإدراكية بنجاح.")
            
        # إنشاء الفهارس (Indices) لتسريع عمليات البحث والاسترجاع الفوري
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_barcode ON resolved_products(barcode)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_name_brand ON resolved_products(product_name, brand)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_queue_status ON automation_queue(status)")
        
        conn.commit()
        conn.close()
        print("💾 [SQLite Cache] تم تهيئة قاعدة بيانات التخزين المحلي بنجاح.")
    except Exception as e:
        print(f"⚠️ [SQLite Cache Error] فشل تهيئة قاعدة البيانات: {e}")

def get_cached_product(barcode=None, product_name=None, brand=None):
    """
    الاستعلام من قاعدة البيانات المحلية باستخدام الباركود أو الاسم والبراند.
    """
    try:
        if not os.path.exists(DB_PATH):
            init_db()
            return None
            
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        row = None
        
        # 1. الاستعلام بالباركود أولاً كمعيار دقيق ومطلق
        if barcode and str(barcode).strip():
            barcode_clean = str(barcode).strip()
            cursor.execute(
                "SELECT * FROM resolved_products WHERE barcode = ? ORDER BY resolved_at DESC LIMIT 1",
                (barcode_clean,)
            )
            row = cursor.fetchone()
            
        # 2. الاستعلام بالاسم والبراند كخطة بديلة عند غياب الباركود
        if not row and product_name:
            name_clean = product_name.strip().lower()
            brand_clean = brand.strip().lower() if brand else ""
            
            # بحث دقيق متجاهلاً حالة الأحرف
            cursor.execute(
                "SELECT * FROM resolved_products WHERE LOWER(product_name) = ? AND LOWER(brand) = ? ORDER BY resolved_at DESC LIMIT 1",
                (name_clean, brand_clean)
            )
            row = cursor.fetchone()
            
        conn.close()
        
        if row:
            metadata = {}
            if row["metadata_json"]:
                try:
                    metadata = json.loads(row["metadata_json"])
                except Exception:
                    pass
            
            return {
                "cloudinary_url": row["cloudinary_url"],
                "original_url": row["original_url"],
                "clip_score": row["clip_score"],
                "metadata": metadata,
                "source": "sqlite_cache"
            }
            
    except Exception as e:
        print(f"⚠️ [SQLite Cache Error] خطأ أثناء القراءة من الكاش: {e}")
        
    return None

def save_product_resolution(barcode, product_name, brand, original_url, cloudinary_url, clip_score, metadata, clip_embedding=None, perceptual_hash=None):
    """
    حفظ أو تحديث نتيجة مطابقة منتج وصورته في قاعدة البيانات للتأكد من عدم تكراره.
    """
    try:
        # تهيئة قاعدة البيانات إن لم تكن مهيأة
        if not os.path.exists(DB_PATH):
            init_db()
            
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        barcode_clean = str(barcode).strip() if barcode else ""
        metadata_str = json.dumps(metadata) if metadata else ""
        embedding_str = json.dumps(clip_embedding) if clip_embedding is not None else ""
        hash_str = str(perceptual_hash) if perceptual_hash is not None else ""
        
        # ننتحقق إذا كان الباركود مسجلاً سابقاً لتحديثه بدلاً من تكرار السجل
        if barcode_clean:
            cursor.execute("SELECT id FROM resolved_products WHERE barcode = ?", (barcode_clean,))
            existing = cursor.fetchone()
            if existing:
                cursor.execute("""
                    UPDATE resolved_products 
                    SET product_name = ?, brand = ?, original_url = ?, cloudinary_url = ?, clip_score = ?, metadata_json = ?, clip_embedding_json = ?, perceptual_hash = ?, resolved_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (product_name, brand, original_url, cloudinary_url, clip_score, metadata_str, embedding_str, hash_str, existing[0]))
                conn.commit()
                conn.close()
                delete_product_failure(barcode_clean)
                print(f"💾 [SQLite Cache] تم تحديث بيانات الباركود الكاش بنجاح: {barcode_clean}")
                return True
                
        # إدراج سجل جديد
        cursor.execute("""
            INSERT INTO resolved_products (barcode, product_name, brand, original_url, cloudinary_url, clip_score, metadata_json, clip_embedding_json, perceptual_hash)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (barcode_clean, product_name, brand, original_url, cloudinary_url, clip_score, metadata_str, embedding_str, hash_str))
        
        conn.commit()
        conn.close()
        delete_product_failure(barcode_clean)
        print(f"💾 [SQLite Cache] تم حفظ الصورة والبيانات الكاش لـ: '{product_name}'")
        return True
    except Exception as e:
        print(f"⚠️ [SQLite Cache Error] فشل حفظ السجل في الكاش: {e}")
        return False

def find_visual_duplicate(target_embedding, threshold=0.96):
    """
    البحث في قاعدة البيانات المحلية عن أي منتج مسجل يحمل صورة مشابهة بصرياً بنسبة كبيرة
    لتجنب خلط الصور المكررة لمنتجات مختلفة.
    """
    if target_embedding is None or not isinstance(target_embedding, list) or len(target_embedding) == 0:
        return None
        
    try:
        if not os.path.exists(DB_PATH):
            return None
            
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # جلب كافة المنتجات التي تحتوي على بصمة متجهة
        cursor.execute("SELECT product_name, brand, clip_embedding_json, cloudinary_url FROM resolved_products WHERE clip_embedding_json IS NOT NULL AND clip_embedding_json != ''")
        rows = cursor.fetchall()
        conn.close()
        
        for row in rows:
            try:
                emb = json.loads(row["clip_embedding_json"])
                if isinstance(emb, list) and len(emb) == len(target_embedding):
                    # حساب التشابه الجيبي (Cosine Similarity)
                    dot_product = sum(x * y for x, y in zip(target_embedding, emb))
                    norm_v1 = sum(x**2 for x in target_embedding) ** 0.5
                    norm_v2 = sum(x**2 for x in emb) ** 0.5
                    similarity = dot_product / (norm_v1 * norm_v2) if (norm_v1 * norm_v2) > 0 else 0.0
                    
                    if similarity >= threshold:
                        print(f"👁️ [Visual Duplicate Detector] تم كشف تكرار بصري مع منتج آخر: '{row['product_name']}' (التطابق البصري: {similarity:.4f})")
                        return {
                            "product_name": row["product_name"],
                            "brand": row["brand"],
                            "cloudinary_url": row["cloudinary_url"],
                            "similarity": similarity
                        }
            except Exception:
                pass
    except Exception as e:
        print(f"⚠️ [SQLite Cache Error] خطأ أثناء كشف التكرار البصري: {e}")
        
    return None

def save_product_failure(barcode, product_name, brand, error_message):
    """
    تسجيل فشل الأتمتة لمنتج معين لتتبعه في طابور الأخطاء.
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        barcode_clean = str(barcode).strip() if barcode else ""
        if not barcode_clean:
            # استخدام اسم المنتج والماركة كباركود بديل إذا لم يكن متوفراً
            barcode_clean = f"ERR_{product_name}_{brand}".replace(" ", "_")
        cursor.execute("""
            INSERT OR REPLACE INTO product_failures (barcode, product_name, brand, error_message, failed_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
        """, (barcode_clean, product_name, brand, error_message))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"⚠️ [SQLite Cache Error] فشل حفظ سجل الخطأ: {e}")
        return False

def delete_product_failure(barcode):
    """
    حذف سجل الفشل عند نجاح مطابقة المنتج لاحقاً.
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        barcode_clean = str(barcode).strip() if barcode else ""
        cursor.execute("DELETE FROM product_failures WHERE barcode = ?", (barcode_clean,))
        # فحص إضافي بالاسم والبراند إذا لم يكن هناك باركود
        if barcode_clean.startswith("ERR_"):
            cursor.execute("DELETE FROM product_failures WHERE barcode LIKE ?", (barcode_clean + "%",))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"⚠️ [SQLite Cache Error] فشل حذف سجل الخطأ: {e}")
        return False

def get_product_failures():
    """
    استرجاع قائمة بكافة المنتجات الفاشلة كـ dict.
    """
    try:
        if not os.path.exists(DB_PATH):
            return {}
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM product_failures")
        rows = cursor.fetchall()
        conn.close()
        return {row["barcode"]: {"error_message": row["error_message"], "failed_at": row["failed_at"]} for row in rows if row["barcode"]}
    except Exception as e:
        print(f"⚠️ [SQLite Cache Error] فشل استرجاع سجلات الأخطاء: {e}")
        return {}

def save_feedback(feedback_id, asset_id, row_number, product_name, brand, image_url, reasons):
    """
    حفظ التغذية الراجعة للتعلم النشط عند استبعاد صورة يدوياً.
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO active_learning_feedback (feedback_id, asset_id, row_number, product_name, brand, image_url, rejection_reasons)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (feedback_id, asset_id, row_number, product_name, brand, image_url, json.dumps(reasons, ensure_ascii=False)))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"⚠️ [SQLite Cache Error] فشل حفظ سجل التغذية الراجعة: {e}")
        return False

def add_to_queue(row_number, barcode, name, brand, query):
    """
    إضافة منتج إلى طابور المعالجة المنظم (يتفادى التكرار)
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO automation_queue (row_number, barcode, product_name, brand, search_query, status, error_message, attempts, updated_at)
            VALUES (?, ?, ?, ?, ?, 'pending', NULL, 0, CURRENT_TIMESTAMP)
        """, (row_number, barcode, name, brand, query))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"⚠️ [SQLite Queue Error] فشل إضافة سجل للطابور: {e}")
        return False

def fetch_next_task():
    """
    سحب المهمة التالية المعلقة ووسمها بـ processing بشكل حاصر وآمن
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # قراءة أول سجل معلق بقفل المعاملة
        cursor.execute("""
            SELECT * FROM automation_queue 
            WHERE status = 'pending' 
            ORDER BY id ASC LIMIT 1
        """)
        row = cursor.fetchone()
        
        if row:
            task_id = row["id"]
            # تحديث الحالة فوراً لـ processing لمنع العمليات الأخرى من سحبه
            cursor.execute("""
                UPDATE automation_queue 
                SET status = 'processing', attempts = attempts + 1, updated_at = CURRENT_TIMESTAMP 
                WHERE id = ?
            """, (task_id,))
            conn.commit()
            
            # إعادة جلب السجل المحدث بالكامل
            cursor.execute("SELECT * FROM automation_queue WHERE id = ?", (task_id,))
            row_updated = cursor.fetchone()
            conn.close()
            return dict(row_updated)
            
        conn.close()
        return None
    except Exception as e:
        print(f"⚠️ [SQLite Queue Error] فشل سحب المهمة التالية: {e}")
        return None

def update_task_status(task_id, status, error_message=None):
    """
    تحديث حالة المهمة بعد المعالجة (completed أو failed)
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE automation_queue 
            SET status = ?, error_message = ?, updated_at = CURRENT_TIMESTAMP 
            WHERE id = ?
        """, (status, error_message, task_id))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"⚠️ [SQLite Queue Error] فشل تحديث حالة المهمة: {e}")
        return False

def get_queue_statistics():
    """
    الحصول على إحصائيات طابور المهام لعرضها على لوحة التحكم
    """
    try:
        if not os.path.exists(DB_PATH):
            return {"total": 0, "pending": 0, "processing": 0, "completed": 0, "failed": 0}
            
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT status, COUNT(*) as cnt 
            FROM automation_queue 
            GROUP BY status
        """)
        rows = cursor.fetchall()
        conn.close()
        
        stats = {"total": 0, "pending": 0, "processing": 0, "completed": 0, "failed": 0}
        for r in rows:
            status = r["status"]
            cnt = r["cnt"]
            if status in stats:
                stats[status] = cnt
            stats["total"] += cnt
        return stats
    except Exception as e:
        print(f"⚠️ [SQLite Queue Error] فشل استرجاع إحصائيات الطابور: {e}")
        return {"total": 0, "pending": 0, "processing": 0, "completed": 0, "failed": 0}

def clear_queue():
    """
    تفريغ كافة سجلات طابور المعالجة للبدء من جديد
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM automation_queue")
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"⚠️ [SQLite Queue Error] فشل مسح الطابور: {e}")
        return False

def get_active_learning_padding_ratio(brand):
    """
    التحقق من التغذية الراجعة لبراند معين لتحديد نسبة الهامش المخصص لمنع قص الأطراف
    """
    if not brand or not os.path.exists(DB_PATH):
        return None
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        # جلب أسباب الاستبعاد لهذا البراند
        cursor.execute("""
            SELECT rejection_reasons FROM active_learning_feedback 
            WHERE LOWER(brand) = ?
        """, (brand.strip().lower(),))
        rows = cursor.fetchall()
        conn.close()
        
        cropping_count = 0
        for r in rows:
            try:
                reasons = json.loads(r[0]) if r[0] else []
            except Exception:
                reasons = []
            # التحقق مما إذا كان السبب هو قص الأطراف
            if any("cropping" in str(reason).lower() or "قص" in str(reason) or "margins" in str(reason).lower() for reason in reasons):
                cropping_count += 1
                
        if cropping_count >= 4:
            return 0.70  # زيادة هامش الأمان ليكون 30%
        elif cropping_count >= 2:
            return 0.75  # زيادة هامش الأمان ليكون 25%
            
        return None
    except Exception as e:
        print(f"⚠️ [Active Learning Error] فشل حساب هامش الأمان المخصص للبراند: {e}")
        return None

def get_active_learning_clutter_flag(brand):
    """
    التحقق مما إذا كان هذا البراند قد تم استبعاد صوره مسبقاً بسبب تداخل الخلفية والتشويش
    """
    if not brand or not os.path.exists(DB_PATH):
        return False
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT rejection_reasons FROM active_learning_feedback 
            WHERE LOWER(brand) = ?
        """, (brand.strip().lower(),))
        rows = cursor.fetchall()
        conn.close()
        
        clutter_count = 0
        for r in rows:
            try:
                reasons = json.loads(r[0]) if r[0] else []
            except Exception:
                reasons = []
            if any("clutter" in str(reason).lower() or "تداخل" in str(reason) or "خلفية" in str(reason) or "background" in str(reason).lower() for reason in reasons):
                clutter_count += 1
                
        return clutter_count >= 2
    except Exception as e:
        print(f"⚠️ [Active Learning Error] فشل حساب تداخل الخلفية للبراند: {e}")
        return False

# تهيئة قاعدة البيانات تلقائياً عند استيراد الموديول للمرة الأولى
init_db()
