# local_cache_db.py
# موديول التخزين المحلي الذكي باستخدام قاعدة بيانات MariaDB لتسريع المطابقة ومنع تكرار البحث

import pymysql
import os
import json
from datetime import datetime

# إعداد الاتصال باستخدام المتغيرات البيئية
def get_db_connection():
    return pymysql.connect(
        host=os.getenv("DB_HOST", "127.0.0.1"),
        port=int(os.getenv("DB_PORT", "3306")),
        user=os.getenv("DB_USERNAME", "root"),
        password=os.getenv("DB_PASSWORD", ""),
        database=os.getenv("DB_DATABASE", "automation_db"),
        charset='utf8mb4',
        cursorclass=pymysql.cursors.DictCursor
    )

def init_db():
    """
    إنشاء قاعدة البيانات وجداولها الأساسية إذا لم تكن موجودة.
    """
    try:
        # الاتصال بخادم MySQL/MariaDB دون تحديد قاعدة البيانات لإنشائها أولاً إن لم تكن موجودة
        conn = pymysql.connect(
            host=os.getenv("DB_HOST", "127.0.0.1"),
            port=int(os.getenv("DB_PORT", "3306")),
            user=os.getenv("DB_USERNAME", "root"),
            password=os.getenv("DB_PASSWORD", "")
        )
        cursor = conn.cursor()
        db_name = os.getenv("DB_DATABASE", "automation_db")
        cursor.execute(f"CREATE DATABASE IF NOT EXISTS {db_name} CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
        conn.commit()
        conn.close()

        # الاتصال بقاعدة البيانات الخاصة بالتطبيق
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # 1. إنشاء جدول حفظ نتائج مطابقة المنتجات وصورها سحابياً مع البيانات الوصفية
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS resolved_products (
                id INT AUTO_INCREMENT PRIMARY KEY,
                barcode VARCHAR(255) NULL,
                product_name VARCHAR(255) NOT NULL,
                brand VARCHAR(255) NULL,
                original_url TEXT NULL,
                cloudinary_url TEXT NOT NULL,
                clip_score DOUBLE NULL,
                metadata_json TEXT NULL,
                clip_embedding_json TEXT NULL,
                perceptual_hash VARCHAR(255) NULL,
                resolved_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            ) ENGINE=InnoDB CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
        """)
        
        # 2. إنشاء جدول تتبع وإدارة أخطاء الأتمتة التقنية
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS product_failures (
                barcode VARCHAR(255) PRIMARY KEY,
                product_name VARCHAR(255) NOT NULL,
                brand VARCHAR(255) NULL,
                error_message TEXT NULL,
                failed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
        """)
        
        # 3. إنشاء جدول حفظ التغذية الراجعة للتعلم النشط
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS active_learning_feedback (
                id INT AUTO_INCREMENT PRIMARY KEY,
                feedback_id VARCHAR(255) UNIQUE,
                asset_id VARCHAR(255) NULL,
                `row_number` INT NULL,
                product_name VARCHAR(255) NULL,
                brand VARCHAR(255) NULL,
                image_url TEXT NULL,
                rejection_reasons TEXT NULL,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
        """)
        
        # 4. إنشاء جدول طابور المهام المنظم
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS automation_queue (
                id INT AUTO_INCREMENT PRIMARY KEY,
                `row_number` INT UNIQUE,
                barcode VARCHAR(255) NULL,
                product_name VARCHAR(255) NOT NULL,
                brand VARCHAR(255) NULL,
                search_query TEXT NULL,
                status VARCHAR(255) DEFAULT 'pending',
                error_message TEXT NULL,
                attempts INT DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            ) ENGINE=InnoDB CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
        """)
        
        # 5. إنشاء جدول حفظ نتائج الصور المرشحة للفرز والاعتماد البصري
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS curation_candidates (
                id INT AUTO_INCREMENT PRIMARY KEY,
                `row_number` INT NOT NULL,
                product_name VARCHAR(255) NOT NULL,
                brand VARCHAR(255) NULL,
                image_url TEXT NOT NULL,
                title VARCHAR(255) NULL,
                width INT NULL,
                height INT NULL,
                clip_score DOUBLE NULL,
                source_domain VARCHAR(255) NULL,
                is_selected INT DEFAULT 0,
                status VARCHAR(255) DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
        """)
        
        # 6. إنشاء جدول إدارة حالة الأتمتة بالخلفية
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS automation_state (
                `key` VARCHAR(255) PRIMARY KEY,
                status VARCHAR(255) DEFAULT 'idle',
                total_items INT DEFAULT 0,
                processed_items INT DEFAULT 0,
                success_count INT DEFAULT 0,
                failed_count INT DEFAULT 0,
                current_product_name VARCHAR(255) NULL,
                pause_requested INT DEFAULT 0,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            ) ENGINE=InnoDB CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
        """)
        
        # إدراج سجل الجلسة الافتراضي الأولي
        cursor.execute("INSERT IGNORE INTO automation_state (`key`, status) VALUES ('active_session', 'idle')")
        
        # 7. إنشاء جدول الإعدادات العامة لحفظ مفاتيح API وبيانات الاعتماد
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS system_settings (
                `key` VARCHAR(255) PRIMARY KEY,
                `value` TEXT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            ) ENGINE=InnoDB CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
        """)
        
        # إدراج القيم الافتراضية المبدئية من ملف .env لتجنب فقدانها
        import config
        default_settings = {
            "photoroom_api_key": getattr(config, "PHOTOROOM_API_KEY", ""),
            "gemini_api_key": getattr(config, "GEMINI_API_KEY", ""),
            "gemini_model": getattr(config, "GEMINI_MODEL", "gemini-3.5-flash"),
            "cloudinary_cloud_name": getattr(config, "CLOUDINARY_CLOUD_NAME", ""),
            "cloudinary_api_key": getattr(config, "CLOUDINARY_API_KEY", ""),
            "cloudinary_api_secret": getattr(config, "CLOUDINARY_API_SECRET", ""),
            "google_search_api_key": os.getenv("GOOGLE_SEARCH_API_KEY", ""),
            "google_search_cx": os.getenv("GOOGLE_SEARCH_CX", "")
        }
        for k, v in default_settings.items():
            cursor.execute(
                "INSERT IGNORE INTO system_settings (`key`, `value`) VALUES (%s, %s)",
                (k, v)
            )
            
        # التحقق من وجود الأعمدة والمؤشرات وتحديث الجداول القائمة إن وجدت (Auto-migration)
        cursor.execute("SHOW COLUMNS FROM resolved_products")
        columns = [col["Field"] for col in cursor.fetchall()]
        if "clip_embedding_json" not in columns:
            cursor.execute("ALTER TABLE resolved_products ADD COLUMN clip_embedding_json TEXT")
            print("💾 [MariaDB Cache] تم إضافة عمود clip_embedding_json لجدول المتجهات بنجاح.")
            
        if "perceptual_hash" not in columns:
            cursor.execute("ALTER TABLE resolved_products ADD COLUMN perceptual_hash VARCHAR(255)")
            print("💾 [MariaDB Cache] تم إضافة عمود perceptual_hash لجدول البصمات بنجاح.")
            
        # إنشاء الفهارس (Indices) لتسريع عمليات البحث والاسترجاع الفوري
        try:
            cursor.execute("ALTER TABLE resolved_products ADD INDEX idx_barcode (barcode)")
        except Exception:
            pass
        try:
            cursor.execute("ALTER TABLE resolved_products ADD INDEX idx_name_brand (product_name, brand)")
        except Exception:
            pass
        try:
            cursor.execute("ALTER TABLE automation_queue ADD INDEX idx_queue_status (status)")
        except Exception:
            pass
        try:
            cursor.execute("ALTER TABLE curation_candidates ADD INDEX idx_curation_row (`row_number`)")
        except Exception:
            pass
            
        conn.commit()
        conn.close()
        print("💾 [MariaDB Cache] تم تهيئة قاعدة بيانات التخزين المحلي بنجاح (محدثة بالفرز والاعتماد).")
    except Exception as e:
        print(f"⚠️ [MariaDB Cache Error] فشل تهيئة قاعدة البيانات: {e}")

def get_cached_product(barcode=None, product_name=None, brand=None):
    """
    الاستعلام من قاعدة البيانات المحلية باستخدام الباركود أو الاسم والبراند.
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        row = None
        
        # 1. الاستعلام بالباركود أولاً كمعيار دقيق ومطلق
        if barcode and str(barcode).strip():
            barcode_clean = str(barcode).strip()
            cursor.execute(
                "SELECT * FROM resolved_products WHERE barcode = %s ORDER BY resolved_at DESC LIMIT 1",
                (barcode_clean,)
            )
            row = cursor.fetchone()
            
        # 2. الاستعلام بالاسم والبراند كخطة بديلة عند غياب الباركود
        if not row and product_name:
            name_clean = product_name.strip().lower()
            brand_clean = brand.strip().lower() if brand else ""
            
            # بحث دقيق متجاهلاً حالة الأحرف
            cursor.execute(
                "SELECT * FROM resolved_products WHERE LOWER(product_name) = %s AND LOWER(brand) = %s ORDER BY resolved_at DESC LIMIT 1",
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
                "source": "mariadb_cache"
            }
            
    except Exception as e:
        print(f"⚠️ [MariaDB Cache Error] خطأ أثناء القراءة من الكاش: {e}")
        
    return None

def save_product_resolution(barcode, product_name, brand, original_url, cloudinary_url, clip_score, metadata, clip_embedding=None, perceptual_hash=None):
    """
    حفظ أو تحديث نتيجة مطابقة منتج وصورته في قاعدة البيانات للتأكد من عدم تكراره.
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        barcode_clean = str(barcode).strip() if barcode else ""
        metadata_str = json.dumps(metadata) if metadata else ""
        embedding_str = json.dumps(clip_embedding) if clip_embedding is not None else ""
        hash_str = str(perceptual_hash) if perceptual_hash is not None else ""
        
        # نتحقق إذا كان الباركود مسجلاً سابقاً لتحديثه بدلاً من تكرار السجل
        if barcode_clean:
            cursor.execute("SELECT id FROM resolved_products WHERE barcode = %s", (barcode_clean,))
            existing = cursor.fetchone()
            if existing:
                cursor.execute("""
                    UPDATE resolved_products 
                    SET product_name = %s, brand = %s, original_url = %s, cloudinary_url = %s, clip_score = %s, metadata_json = %s, clip_embedding_json = %s, perceptual_hash = %s, resolved_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                """, (product_name, brand, original_url, cloudinary_url, clip_score, metadata_str, embedding_str, hash_str, existing["id"]))
                conn.commit()
                conn.close()
                delete_product_failure(barcode_clean)
                print(f"💾 [MariaDB Cache] تم تحديث بيانات الباركود الكاش بنجاح: {barcode_clean}")
                return True
                
        # إدراج سجل جديد
        cursor.execute("""
            INSERT INTO resolved_products (barcode, product_name, brand, original_url, cloudinary_url, clip_score, metadata_json, clip_embedding_json, perceptual_hash)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (barcode_clean, product_name, brand, original_url, cloudinary_url, clip_score, metadata_str, embedding_str, hash_str))
        
        conn.commit()
        conn.close()
        delete_product_failure(barcode_clean)
        print(f"💾 [MariaDB Cache] تم حفظ الصورة والبيانات الكاش لـ: '{product_name}'")
        return True
    except Exception as e:
        print(f"⚠️ [MariaDB Cache Error] فشل حفظ السجل في الكاش: {e}")
        return False

def find_visual_duplicate(target_embedding, threshold=0.96):
    """
    البحث في قاعدة البيانات المحلية عن أي منتج مسجل يحمل صورة مشابهة بصرياً بنسبة كبيرة
    لتجنب خلط الصور المكررة لمنتجات مختلفة.
    """
    if target_embedding is None or not isinstance(target_embedding, list) or len(target_embedding) == 0:
        return None
        
    try:
        conn = get_db_connection()
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
        print(f"⚠️ [MariaDB Cache Error] خطأ أثناء كشف التكرار البصري: {e}")
        
    return None

def save_product_failure(barcode, product_name, brand, error_message):
    """
    تسجيل فشل الأتمتة لمنتج معين لتتبعه في طابور الأخطاء.
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        barcode_clean = str(barcode).strip() if barcode else ""
        if not barcode_clean:
            # استخدام اسم المنتج والماركة كباركود بديل إذا لم يكن متوفراً
            barcode_clean = f"ERR_{product_name}_{brand}".replace(" ", "_")
        cursor.execute("""
            REPLACE INTO product_failures (barcode, product_name, brand, error_message, failed_at)
            VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)
        """, (barcode_clean, product_name, brand, error_message))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"⚠️ [MariaDB Cache Error] فشل حفظ سجل الخطأ: {e}")
        return False

def delete_product_failure(barcode):
    """
    حذف سجل الفشل عند نجاح مطابقة المنتج لاحقاً.
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        barcode_clean = str(barcode).strip() if barcode else ""
        cursor.execute("DELETE FROM product_failures WHERE barcode = %s", (barcode_clean,))
        # فحص إضافي بالاسم والبراند إذا لم يكن هناك باركود
        if barcode_clean.startswith("ERR_"):
            cursor.execute("DELETE FROM product_failures WHERE barcode LIKE %s", (barcode_clean + "%",))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"⚠️ [MariaDB Cache Error] فشل حذف سجل الخطأ: {e}")
        return False

def get_product_failures():
    """
    استرجاع قائمة بكافة المنتجات الفاشلة كـ dict.
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM product_failures")
        rows = cursor.fetchall()
        conn.close()
        return {row["barcode"]: {"error_message": row["error_message"], "failed_at": row["failed_at"]} for row in rows if row["barcode"]}
    except Exception as e:
        print(f"⚠️ [MariaDB Cache Error] فشل استرجاع سجلات الأخطاء: {e}")
        return {}

def save_feedback(feedback_id, asset_id, row_number, product_name, brand, image_url, reasons):
    """
    حفظ التغذية الراجعة للتعلم النشط عند استبعاد صورة يدوياً.
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO active_learning_feedback (feedback_id, asset_id, `row_number`, product_name, brand, image_url, rejection_reasons)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (feedback_id, asset_id, row_number, product_name, brand, image_url, json.dumps(reasons, ensure_ascii=False)))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"⚠️ [MariaDB Cache Error] فشل حفظ سجل التغذية الراجعة: {e}")
        return False

def add_to_queue(row_number, barcode, name, brand, query):
    """
    إضافة منتج إلى طابور المعالجة المنظم (يتفادى التكرار)
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            REPLACE INTO automation_queue (`row_number`, barcode, product_name, brand, search_query, status, error_message, attempts, updated_at)
            VALUES (%s, %s, %s, %s, %s, 'pending', NULL, 0, CURRENT_TIMESTAMP)
        """, (row_number, barcode, name, brand, query))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"⚠️ [MariaDB Queue Error] فشل إضافة سجل للطابور: {e}")
        return False

def fetch_next_task():
    """
    سحب المهمة التالية المعلقة ووسمها بـ processing بشكل حاصر وآمن
    """
    try:
        conn = get_db_connection()
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
                WHERE id = %s
            """, (task_id,))
            conn.commit()
            
            # إعادة جلب السجل المحدث بالكامل
            cursor.execute("SELECT * FROM automation_queue WHERE id = %s", (task_id,))
            row_updated = cursor.fetchone()
            conn.close()
            return dict(row_updated)
            
        conn.close()
        return None
    except Exception as e:
        print(f"⚠️ [MariaDB Queue Error] فشل سحب المهمة التالية: {e}")
        return None

def update_task_status(task_id, status, error_message=None):
    """
    تحديث حالة المهمة بعد المعالجة (completed أو failed)
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE automation_queue 
            SET status = %s, error_message = %s, updated_at = CURRENT_TIMESTAMP 
            WHERE id = %s
        """, (status, error_message, task_id))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"⚠️ [MariaDB Queue Error] فشل تحديث حالة المهمة: {e}")
        return False

def update_task_status_by_row(row_number, status, error_message=None):
    """
    تحديث حالة المهمة في طابور المعالجة باستخدام رقم الصف (Row Number)
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE automation_queue 
            SET status = %s, error_message = %s, updated_at = CURRENT_TIMESTAMP 
            WHERE `row_number` = %s
        """, (status, error_message, row_number))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"⚠️ [MariaDB Queue Error] فشل تحديث حالة المهمة للصف {row_number}: {e}")
        return False

def get_queue_statistics():
    """
    الحصول على إحصائيات طابور المهام لعرضها على لوحة التحكم
    """
    try:
        conn = get_db_connection()
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
        print(f"⚠️ [MariaDB Queue Error] فشل استرجاع إحصائيات الطابور: {e}")
        return {"total": 0, "pending": 0, "processing": 0, "completed": 0, "failed": 0}

def clear_queue():
    """
    تفريغ كافة سجلات طابور المعالجة للبدء من جديد
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM automation_queue")
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"⚠️ [MariaDB Queue Error] فشل مسح الطابور: {e}")
        return False

def get_active_learning_padding_ratio(brand):
    """
    التحقق من التغذية الراجعة لبراند معين لتحديد نسبة الهامش المخصص لمنع قص الأطراف
    """
    if not brand:
        return None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        # جلب أسباب الاستبعاد لهذا البراند
        cursor.execute("""
            SELECT rejection_reasons FROM active_learning_feedback 
            WHERE LOWER(brand) = %s
        """, (brand.strip().lower(),))
        rows = cursor.fetchall()
        conn.close()
        
        cropping_count = 0
        for r in rows:
            try:
                reasons = json.loads(r["rejection_reasons"]) if r["rejection_reasons"] else []
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
    if not brand:
        return False
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT rejection_reasons FROM active_learning_feedback 
            WHERE LOWER(brand) = %s
        """, (brand.strip().lower(),))
        rows = cursor.fetchall()
        conn.close()
        
        clutter_count = 0
        for r in rows:
            try:
                reasons = json.loads(r["rejection_reasons"]) if r["rejection_reasons"] else []
            except Exception:
                reasons = []
            if any("clutter" in str(reason).lower() or "تداخل" in str(reason) or "خلفية" in str(reason) or "background" in str(reason).lower() for reason in reasons):
                clutter_count += 1
                
        return clutter_count >= 2
    except Exception as e:
        print(f"⚠️ [Active Learning Error] فشل حساب تداخل الخلفية للبراند: {e}")
        return False

def save_curation_candidates(row_number, product_name, brand, candidates, best_url):
    """
    حفظ جميع الصور المرشحة لعملية الفرز والاعتماد البصري.
    سيتم مسح أي مرشح سابق لنفس رقم الصف وحفظ المرشحات الجديدة.
    سيتم تعليم المرشح ذو الرابط المماثل لـ best_url كـ selected تلقائياً.
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # 1. مسح المرشحات السابقة لهذا الصف
        cursor.execute("DELETE FROM curation_candidates WHERE `row_number` = %s", (row_number,))
        
        # 2. إدخال المرشحات الجديدة
        for c in candidates:
            url = c.get("url")
            if not url:
                continue
            title = c.get("title", "")
            width = c.get("width", 0)
            height = c.get("height", 0)
            
            # extract clip score
            clip_score = 0.0
            if c.get("scores") and "relevance_score" in c["scores"]:
                clip_score = float(c["scores"]["relevance_score"])
            elif "clip_score" in c:
                clip_score = float(c["clip_score"])
            elif "relevance_score" in c:
                clip_score = float(c["relevance_score"])
                
            # source domain
            from urllib.parse import urlparse
            source_domain = ""
            try:
                parsed_uri = urlparse(url)
                source_domain = parsed_uri.netloc
            except Exception:
                pass
                
            is_selected = 1 if url == best_url else 0
            status = 'pending'
            
            cursor.execute("""
                INSERT INTO curation_candidates (
                    `row_number`, product_name, brand, image_url, title, width, height, clip_score, source_domain, is_selected, status
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                row_number, product_name, brand, url, title, int(width or 0), int(height or 0),
                float(clip_score or 0.0), source_domain, is_selected, status
            ))
            
        conn.commit()
        conn.close()
        print(f"💾 [Curation Database] تم حفظ المرشحات للصف {row_number} بنجاح.")
    except Exception as e:
        print(f"⚠️ [Curation Database Error] فشل حفظ مرشحات الفرز للصف {row_number}: {e}")

def get_curation_candidates(row_number):
    """
    جلب جميع الصور المرشحة المحفوظة لصف معين.
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM curation_candidates WHERE `row_number` = %s ORDER BY clip_score DESC", (row_number,))
        rows = cursor.fetchall()
        conn.close()
        
        candidates = []
        for r in rows:
            candidates.append({
                "id": r["id"],
                "row_number": r["row_number"],
                "product_name": r["product_name"],
                "brand": r["brand"],
                "image_url": r["image_url"],
                "title": r["title"],
                "width": r["width"],
                "height": r["height"],
                "clip_score": r["clip_score"],
                "source_domain": r["source_domain"],
                "is_selected": r["is_selected"],
                "status": r["status"]
            })
        return candidates
    except Exception as e:
        print(f"⚠️ [Curation Database Error] فشل جلب مرشحات الفرز للصف {row_number}: {e}")
        return []

def update_automation_state(status, total=None, processed=None, success=None, failed=None, current_product=None):
    """
    تحديث حالة ومؤشرات جلسة الأتمتة الجارية حالياً في قاعدة البيانات.
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        updates = ["status = %s", "updated_at = CURRENT_TIMESTAMP"]
        params = [status]
        
        if total is not None:
            updates.append("total_items = %s")
            params.append(total)
        if processed is not None:
            updates.append("processed_items = %s")
            params.append(processed)
        if success is not None:
            updates.append("success_count = %s")
            params.append(success)
        if failed is not None:
            updates.append("failed_count = %s")
            params.append(failed)
        if current_product is not None:
            updates.append("current_product_name = %s")
            params.append(current_product)
            
        params.append("active_session")
        query = f"UPDATE automation_state SET {', '.join(updates)} WHERE `key` = %s"
        cursor.execute(query, params)
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"⚠️ [MariaDB State Error] فشل تحديث حالة الأتمتة: {e}")
        return False

def get_automation_state():
    """
    الاستعلام عن حالة الأتمتة الحالية من قاعدة البيانات.
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM automation_state WHERE `key` = 'active_session'")
        row = cursor.fetchone()
        conn.close()
        if row:
            return dict(row)
    except Exception as e:
        print(f"⚠️ [MariaDB State Error] فشل استرداد حالة الأتمتة: {e}")
    return {"status": "idle", "total_items": 0, "processed_items": 0, "success_count": 0, "failed_count": 0, "current_product_name": "", "pause_requested": 0}

def pause_automation():
    """
    تعيين علم طلب الإيقاف المؤقت لجلسة الأتمتة الجارية.
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE automation_state SET pause_requested = 1 WHERE `key` = 'active_session'")
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"⚠️ [MariaDB State Error] فشل إيقاف الأتمتة مؤقتاً: {e}")
        return False

def resume_automation():
    """
    إعادة استئناف جلسة الأتمتة وإلغاء علم الإيقاف.
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE automation_state SET pause_requested = 0 WHERE `key` = 'active_session'")
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"⚠️ [MariaDB State Error] فشل استئناف الأتمتة: {e}")
        return False

def delete_curation_candidates(row_number):
    """
    مسح جميع المرشحات البصرية المخزنة لصف معين بعد اعتماده أو استبعاده.
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM curation_candidates WHERE `row_number` = %s", (row_number,))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"⚠️ [Curation Database Error] فشل مسح مرشحات الصف {row_number}: {e}")
        return False

def get_ready_for_review_count():
    """
    جلب عدد المنتجات الجاهزة للمراجعة بانتظار الاعتماد.
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) as count FROM automation_queue WHERE status = 'ready_for_review'")
        row = cursor.fetchone()
        conn.close()
        if row:
            return row['count']
    except Exception as e:
        print(f"⚠️ [MariaDB Queue Error] فشل حساب المهام الجاهزة للمراجعة: {e}")
    return 0

# تهيئة قاعدة البيانات تلقائياً عند استيراد الموديول للمرة الأولى
init_db()
