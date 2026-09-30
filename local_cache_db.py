# local_cache_db.py
# موديول التخزين المحلي في MariaDB: طابور المهام، مرشحات المراجعة، كاش الحلول المعتمدة، ورفض المراجعين.
#
# القواعد (D11):
# - الكاش يخدم فقط الحلول بحالة human_approved أو auto_verified، ويعتمد على sku_key،
#   وعند وجود باركود لا يوجد أي رجوع للمطابقة بالاسم (منع إعطاء صورة منتج شقيق).
# - رفض المراجع يُسجل في rejected_images (الرابط + pHash) ويُمرر للبحث كاستبعادات.
# - سحب المهام من الطابور ذري (UPDATE واحد مع lease) ولا تُبتلع أخطاء قاعدة البيانات.

import json
import logging
import os
import socket
import uuid

import pymysql

logger = logging.getLogger(__name__)

# حالات الحل المعتمد في resolved_products
SERVABLE_STATUSES = ("human_approved", "auto_verified")
VERIFICATION_STATUSES = ("human_approved", "auto_verified", "superseded", "legacy")

# أكواد الرفض (D11) + الأكواد التجميلية القديمة الثلاثة
IDENTITY_REASON_CODES = (
    "WRONG_PRODUCT", "WRONG_BRAND", "WRONG_VARIANT", "WRONG_SIZE", "WRONG_PACK",
    "NOT_PACKSHOT", "LOW_QUALITY",
)
COSMETIC_REASON_CODES = ("HALO_ARTIFACT", "BACKGROUND_BLEED", "CROP_MARGIN_CLIPPING")
REJECT_REASON_CODES = IDENTITY_REASON_CODES + COSMETIC_REASON_CODES

# مدة الحجز (lease) للمهمة المسحوبة قبل أن تصبح قابلة للسحب مجدداً
LEASE_MINUTES = 15
MAX_TITLE_CHARS = 250


# إعداد الاتصال باستخدام المتغيرات البيئية (تُقرأ عند كل اتصال حتى تعمل الاختبارات على automation_test)
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


def _close(conn):
    try:
        conn.close()
    except Exception:
        pass


def url_norm(url):
    """الصيغة المقارنة لرابط الصورة (المضيف + المسار، بدون www والاستعلام)."""
    if not url:
        return ""
    try:
        from catalog_match.text_norm import url_key
        return url_key(url)[:768]
    except Exception:
        return str(url).strip().lower()[:768]


# ---------------------------------------------------------------------------
# المخطط (Schema) — كل التعديلات Idempotent عبر ADD COLUMN IF NOT EXISTS (MariaDB)
# ---------------------------------------------------------------------------

_SCHEMA_MIGRATIONS = [
    # resolved_products
    "ALTER TABLE resolved_products ADD COLUMN IF NOT EXISTS clip_embedding_json TEXT NULL",
    "ALTER TABLE resolved_products ADD COLUMN IF NOT EXISTS perceptual_hash VARCHAR(255) NULL",
    "ALTER TABLE resolved_products ADD COLUMN IF NOT EXISTS sku_key VARCHAR(64) NULL",
    "ALTER TABLE resolved_products ADD COLUMN IF NOT EXISTS verification_status "
    "ENUM('human_approved','auto_verified','superseded','legacy') NOT NULL DEFAULT 'legacy'",
    "ALTER TABLE resolved_products ADD COLUMN IF NOT EXISTS approved_by VARCHAR(64) NULL",
    "ALTER TABLE resolved_products ADD INDEX IF NOT EXISTS idx_barcode (barcode)",
    "ALTER TABLE resolved_products ADD INDEX IF NOT EXISTS idx_name_brand (product_name, brand)",
    "ALTER TABLE resolved_products ADD INDEX IF NOT EXISTS idx_resolved_sku (sku_key)",
    # automation_queue
    "ALTER TABLE automation_queue ADD COLUMN IF NOT EXISTS sku_key VARCHAR(64) NULL",
    "ALTER TABLE automation_queue ADD COLUMN IF NOT EXISTS payload_json LONGTEXT NULL",
    "ALTER TABLE automation_queue ADD COLUMN IF NOT EXISTS worker_id VARCHAR(64) NULL",
    "ALTER TABLE automation_queue ADD COLUMN IF NOT EXISTS lease_until DATETIME NULL",
    "ALTER TABLE automation_queue ADD COLUMN IF NOT EXISTS failure_code VARCHAR(32) NULL",
    "ALTER TABLE automation_queue ADD COLUMN IF NOT EXISTS trace_json LONGTEXT NULL",
    "ALTER TABLE automation_queue ADD INDEX IF NOT EXISTS idx_queue_status (status)",
    "ALTER TABLE automation_queue ADD INDEX IF NOT EXISTS idx_queue_worker (worker_id)",
    "ALTER TABLE automation_queue ADD INDEX IF NOT EXISTS idx_queue_sku (sku_key)",
    # curation_candidates
    "ALTER TABLE curation_candidates MODIFY COLUMN title TEXT NULL",
    "ALTER TABLE curation_candidates ADD COLUMN IF NOT EXISTS sku_key VARCHAR(64) NULL",
    "ALTER TABLE curation_candidates ADD COLUMN IF NOT EXISTS run_id VARCHAR(64) NULL",
    "ALTER TABLE curation_candidates ADD COLUMN IF NOT EXISTS reasons_json LONGTEXT NULL",
    "ALTER TABLE curation_candidates ADD COLUMN IF NOT EXISTS evidence_json LONGTEXT NULL",
    "ALTER TABLE curation_candidates ADD COLUMN IF NOT EXISTS vlm_json LONGTEXT NULL",
    "ALTER TABLE curation_candidates ADD COLUMN IF NOT EXISTS content_sha256 VARCHAR(64) NULL",
    "ALTER TABLE curation_candidates ADD COLUMN IF NOT EXISTS identity_tier VARCHAR(8) NULL",
    "ALTER TABLE curation_candidates ADD COLUMN IF NOT EXISTS page_url TEXT NULL",
    "ALTER TABLE curation_candidates ADD INDEX IF NOT EXISTS idx_curation_row (`row_number`)",
    "ALTER TABLE curation_candidates ADD INDEX IF NOT EXISTS idx_curation_sku (sku_key)",
    # automation_state: رسالة تنبيه مرئية للوحة التحكم (مثل عدم توفر نموذج Gemini)
    "ALTER TABLE automation_state ADD COLUMN IF NOT EXISTS notice VARCHAR(255) NULL",
]


def init_db():
    """
    إنشاء قاعدة البيانات وجداولها إن لم تكن موجودة وتطبيق الترقيات بشكل Idempotent.
    تعيد True عند النجاح و False عند الفشل (لا ترفع استثناء كي لا يفشل الاستيراد عند غياب MariaDB).
    """
    try:
        # الاتصال بخادم MariaDB دون تحديد قاعدة البيانات لإنشائها أولاً إن لم تكن موجودة
        conn = pymysql.connect(
            host=os.getenv("DB_HOST", "127.0.0.1"),
            port=int(os.getenv("DB_PORT", "3306")),
            user=os.getenv("DB_USERNAME", "root"),
            password=os.getenv("DB_PASSWORD", ""),
            connect_timeout=5,
        )
        cursor = conn.cursor()
        db_name = os.getenv("DB_DATABASE", "automation_db")
        cursor.execute(f"CREATE DATABASE IF NOT EXISTS `{db_name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
        conn.commit()
        conn.close()

        conn = get_db_connection()
        cursor = conn.cursor()

        # 1. نتائج المطابقة المعتمدة (الكاش)
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

        # 2. أخطاء المنتجات (فشل حقيقي في المنتج؛ انقطاع المزودين لا يُسجل هنا)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS product_failures (
                barcode VARCHAR(255) PRIMARY KEY,
                product_name VARCHAR(255) NOT NULL,
                brand VARCHAR(255) NULL,
                error_message TEXT NULL,
                failed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
        """)

        # 3. سجل ملاحظات المراجعين (تعرضه صفحة التعلم النشط في لوحة التحكم)
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

        # 4. طابور المهام
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

        # 5. المرشحات المعروضة على المراجع
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS curation_candidates (
                id INT AUTO_INCREMENT PRIMARY KEY,
                `row_number` INT NOT NULL,
                product_name VARCHAR(255) NOT NULL,
                brand VARCHAR(255) NULL,
                image_url TEXT NOT NULL,
                title TEXT NULL,
                width INT NULL,
                height INT NULL,
                clip_score DOUBLE NULL,
                source_domain VARCHAR(255) NULL,
                is_selected INT DEFAULT 0,
                status VARCHAR(255) DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
        """)

        # 6. حالة جلسة الأتمتة
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
        cursor.execute("INSERT IGNORE INTO automation_state (`key`, status) VALUES ('active_session', 'idle')")

        # 7. الإعدادات العامة (مفاتيح API وبيانات الاعتماد)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS system_settings (
                `key` VARCHAR(255) PRIMARY KEY,
                `value` TEXT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            ) ENGINE=InnoDB CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
        """)

        # 8. رفض المراجعين (D11): روابط وبصمات pHash مستبعدة لكل SKU
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS rejected_images (
                id INT AUTO_INCREMENT PRIMARY KEY,
                sku_key VARCHAR(64) NOT NULL,
                url_norm VARCHAR(768) NULL,
                original_url TEXT NULL,
                page_url TEXT NULL,
                phash VARCHAR(32) NULL,
                reason_code VARCHAR(32) NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_rejected_sku (sku_key)
            ) ENGINE=InnoDB CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
        """)

        # القيم الافتراضية المبدئية من ملف .env (INSERT IGNORE لا يغير القيم الموجودة)
        import config
        default_settings = {
            "photoroom_api_key": getattr(config, "PHOTOROOM_API_KEY", ""),
            "gemini_api_key": getattr(config, "GEMINI_API_KEY", ""),
            "gemini_model": getattr(config, "GEMINI_MODEL", "gemini-3.1-flash-lite"),
            "cloudinary_cloud_name": getattr(config, "CLOUDINARY_CLOUD_NAME", ""),
            "cloudinary_api_key": getattr(config, "CLOUDINARY_API_KEY", ""),
            "cloudinary_api_secret": getattr(config, "CLOUDINARY_API_SECRET", ""),
            "google_search_api_key": os.getenv("GOOGLE_SEARCH_API_KEY", ""),
            "google_search_cx": os.getenv("GOOGLE_SEARCH_CX", ""),
            "serper_api_key": getattr(config, "SERPER_API_KEY", ""),
            "search_engine": getattr(config, "SEARCH_ENGINE", "v2"),
            "auto_publish_enabled": "false",
            "auto_publish_brands": "",
            "output_canvas_size": str(getattr(config, "OUTPUT_CANVAS_SIZE", 800)),
            "strict_brand_match": "true",
        }
        for k, v in default_settings.items():
            cursor.execute("INSERT IGNORE INTO system_settings (`key`, `value`) VALUES (%s, %s)", (k, v))

        for stmt in _SCHEMA_MIGRATIONS:
            cursor.execute(stmt)

        conn.commit()
        conn.close()
        logger.info("[MariaDB] تم تهيئة/ترقية قاعدة البيانات '%s' بنجاح.", db_name)
        return True
    except Exception as e:
        logger.warning("[MariaDB] فشل تهيئة قاعدة البيانات: %s", e)
        return False


# ---------------------------------------------------------------------------
# الكاش (resolved_products)
# ---------------------------------------------------------------------------

_SERVABLE_SQL = "verification_status IN ('human_approved','auto_verified')"


def _cache_row_to_dict(row):
    metadata = {}
    if row.get("metadata_json"):
        try:
            metadata = json.loads(row["metadata_json"])
        except Exception:
            metadata = {}
    return {
        "cloudinary_url": row["cloudinary_url"],
        "original_url": row.get("original_url"),
        "clip_score": row.get("clip_score"),
        "metadata": metadata,
        "sku_key": row.get("sku_key"),
        "verification_status": row.get("verification_status"),
        "approved_by": row.get("approved_by"),
        "perceptual_hash": row.get("perceptual_hash"),
        "source": "mariadb_cache",
    }


def get_cached_product(barcode=None, product_name=None, brand=None, sku_key=None):
    """
    الاستعلام من الكاش. لا يخدم إلا الحلول المعتمدة (human_approved / auto_verified).
    - بـ sku_key أولاً إن مُرر.
    - عند وجود باركود: بحث صارم بالباركود فقط، ولا رجوع للاسم أبداً.
    - بدون باركود ولا sku_key: مطابقة دقيقة للاسم والبراند.
    خطأ القراءة يُعامل كعدم وجود (None) ويُسجل.
    """
    barcode_clean = str(barcode).strip() if barcode is not None else ""
    sku_clean = str(sku_key).strip() if sku_key else ""
    try:
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            row = None
            if sku_clean:
                cursor.execute(
                    f"SELECT * FROM resolved_products WHERE sku_key = %s AND {_SERVABLE_SQL} "
                    "ORDER BY resolved_at DESC, id DESC LIMIT 1",
                    (sku_clean,),
                )
                row = cursor.fetchone()
            if row is None and barcode_clean:
                cursor.execute(
                    f"SELECT * FROM resolved_products WHERE barcode = %s AND {_SERVABLE_SQL} "
                    "ORDER BY resolved_at DESC, id DESC LIMIT 1",
                    (barcode_clean,),
                )
                row = cursor.fetchone()
            elif row is None and not sku_clean and product_name:
                cursor.execute(
                    f"SELECT * FROM resolved_products WHERE LOWER(product_name) = %s AND LOWER(brand) = %s "
                    f"AND {_SERVABLE_SQL} ORDER BY resolved_at DESC, id DESC LIMIT 1",
                    (product_name.strip().lower(), (brand or "").strip().lower()),
                )
                row = cursor.fetchone()
        finally:
            _close(conn)
        return _cache_row_to_dict(row) if row else None
    except Exception as e:
        logger.warning("[MariaDB Cache] خطأ أثناء القراءة من الكاش: %s", e)
        return None


def save_product_resolution(barcode, product_name, brand, original_url, cloudinary_url, clip_score=None,
                            metadata=None, clip_embedding=None, perceptual_hash=None,
                            verification_status="legacy", approved_by=None, sku_key=None):
    """
    حفظ أو تحديث الحل المعتمد لمنتج (Upsert بـ sku_key، أو بالباركود إن لم يوجد sku_key).
    أحدث سجل مطابق يُحدّث، وأي سجلات مطابقة أخرى تصبح superseded.
    """
    if verification_status not in VERIFICATION_STATUSES:
        raise ValueError(f"verification_status غير صالح: {verification_status!r}")
    barcode_clean = str(barcode).strip() if barcode else ""
    sku_clean = str(sku_key).strip() if sku_key else ""
    metadata_str = json.dumps(metadata, ensure_ascii=False) if metadata else ""
    embedding_str = json.dumps(clip_embedding) if clip_embedding is not None else ""
    hash_str = str(perceptual_hash) if perceptual_hash is not None else ""
    try:
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            clauses, params = [], []
            if sku_clean:
                clauses.append("sku_key = %s")
                params.append(sku_clean)
            if barcode_clean:
                clauses.append("barcode = %s")
                params.append(barcode_clean)
            existing = []
            if clauses:
                cursor.execute(
                    f"SELECT id FROM resolved_products WHERE {' OR '.join(clauses)} ORDER BY id DESC",
                    tuple(params),
                )
                existing = [r["id"] for r in cursor.fetchall()]
            values = (barcode_clean, product_name, brand, original_url, cloudinary_url, clip_score,
                      metadata_str, embedding_str, hash_str, sku_clean or None, verification_status, approved_by)
            if existing:
                cursor.execute("""
                    UPDATE resolved_products
                    SET barcode = %s, product_name = %s, brand = %s, original_url = %s, cloudinary_url = %s,
                        clip_score = %s, metadata_json = %s, clip_embedding_json = %s, perceptual_hash = %s,
                        sku_key = %s, verification_status = %s, approved_by = %s, resolved_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                """, values + (existing[0],))
                older = existing[1:]
                if older:
                    placeholders = ",".join("%s" for _ in older)
                    cursor.execute(
                        f"UPDATE resolved_products SET verification_status = 'superseded' WHERE id IN ({placeholders})",
                        tuple(older),
                    )
            else:
                cursor.execute("""
                    INSERT INTO resolved_products (barcode, product_name, brand, original_url, cloudinary_url,
                        clip_score, metadata_json, clip_embedding_json, perceptual_hash, sku_key,
                        verification_status, approved_by)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, values)
            conn.commit()
        finally:
            _close(conn)
    except Exception as e:
        logger.warning("[MariaDB Cache] فشل حفظ الحل المعتمد لـ '%s': %s", product_name, e)
        return False
    if verification_status in SERVABLE_STATUSES:
        delete_product_failure(barcode_clean or f"ERR_{product_name}_{brand}".replace(" ", "_"))
    return True


def supersede_resolution(sku_key, barcode=None):
    """
    وسم كل الحلول السابقة لهذا الـ SKU (أو لهذا الباركود) بأنها superseded كي لا يعيدها الكاش.
    تعيد عدد السجلات المتأثرة، أو None عند خطأ قاعدة البيانات.
    """
    clauses, params = [], []
    if sku_key:
        clauses.append("sku_key = %s")
        params.append(str(sku_key).strip())
    if barcode and str(barcode).strip():
        clauses.append("barcode = %s")
        params.append(str(barcode).strip())
    if not clauses:
        return 0
    try:
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                f"UPDATE resolved_products SET verification_status = 'superseded' "
                f"WHERE ({' OR '.join(clauses)}) AND verification_status <> 'superseded'",
                tuple(params),
            )
            affected = cursor.rowcount
            conn.commit()
            return affected
        finally:
            _close(conn)
    except Exception as e:
        logger.warning("[MariaDB Cache] فشل إلغاء الحل السابق لـ %s: %s", sku_key, e)
        return None


def find_visual_duplicate(target_embedding, threshold=0.96):
    """
    (مسار v1 فقط) البحث عن منتج مسجل بمتجه بصري مشابه جداً. لا يُستخدم في v2.
    """
    if target_embedding is None or not isinstance(target_embedding, list) or len(target_embedding) == 0:
        return None
    try:
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT product_name, brand, clip_embedding_json, cloudinary_url FROM resolved_products "
                "WHERE clip_embedding_json IS NOT NULL AND clip_embedding_json != '' AND " + _SERVABLE_SQL
            )
            rows = cursor.fetchall()
        finally:
            _close(conn)
        for row in rows:
            try:
                emb = json.loads(row["clip_embedding_json"])
                if isinstance(emb, list) and len(emb) == len(target_embedding):
                    dot = sum(x * y for x, y in zip(target_embedding, emb))
                    n1 = sum(x ** 2 for x in target_embedding) ** 0.5
                    n2 = sum(x ** 2 for x in emb) ** 0.5
                    sim = dot / (n1 * n2) if (n1 * n2) > 0 else 0.0
                    if sim >= threshold:
                        return {"product_name": row["product_name"], "brand": row["brand"],
                                "cloudinary_url": row["cloudinary_url"], "similarity": sim}
            except Exception:
                pass
    except Exception as e:
        logger.warning("[MariaDB Cache] خطأ أثناء كشف التكرار البصري: %s", e)
    return None


# ---------------------------------------------------------------------------
# رفض المراجعين (rejected_images)
# ---------------------------------------------------------------------------

def add_rejected_image(sku_key, url, page_url=None, phash=None, reason_code="WRONG_PRODUCT"):
    """تسجيل صورة رفضها المراجع لهذا الـ SKU. تعيد True عند النجاح."""
    if not sku_key or not url:
        raise ValueError("sku_key و url مطلوبان لتسجيل الرفض")
    if reason_code not in REJECT_REASON_CODES:
        raise ValueError(f"reason_code غير صالح: {reason_code!r}")
    try:
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO rejected_images (sku_key, url_norm, original_url, page_url, phash, reason_code) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (str(sku_key).strip(), url_norm(url), url, page_url or None, phash or None, reason_code),
            )
            conn.commit()
            return True
        finally:
            _close(conn)
    except Exception as e:
        logger.warning("[MariaDB] فشل تسجيل الصورة المرفوضة للـ SKU %s: %s", sku_key, e)
        return False


def get_rejections(sku_key):
    """
    إرجاع (الروابط المرفوضة، بصمات pHash المرفوضة) لهذا الـ SKU لتمريرها للبحث كاستبعادات.
    الأكواد التجميلية القديمة (هالة، تسرب خلفية، قص حواف) تخص المعالجة لا هوية الصورة، فلا تُستبعد بها المصادر.
    أخطاء قاعدة البيانات تُرفع (لا نبحث بصمت دون الاستبعادات).
    """
    if not sku_key:
        return [], []
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        placeholders = ",".join("%s" for _ in COSMETIC_REASON_CODES)
        cursor.execute(
            f"SELECT original_url, phash FROM rejected_images WHERE sku_key = %s "
            f"AND reason_code NOT IN ({placeholders}) ORDER BY id",
            (str(sku_key).strip(),) + COSMETIC_REASON_CODES,
        )
        rows = cursor.fetchall()
    finally:
        _close(conn)
    urls, phashes = [], []
    for r in rows:
        if r.get("original_url") and r["original_url"] not in urls:
            urls.append(r["original_url"])
        if r.get("phash") and r["phash"] not in phashes:
            phashes.append(r["phash"])
    return urls, phashes


# ---------------------------------------------------------------------------
# أخطاء المنتجات والملاحظات
# ---------------------------------------------------------------------------

def save_product_failure(barcode, product_name, brand, error_message):
    """تسجيل فشل منتج حقيقي (لا تُسجل هنا انقطاعات المزودين)."""
    try:
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            barcode_clean = str(barcode).strip() if barcode else ""
            if not barcode_clean:
                barcode_clean = f"ERR_{product_name}_{brand}".replace(" ", "_")
            cursor.execute("""
                REPLACE INTO product_failures (barcode, product_name, brand, error_message, failed_at)
                VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)
            """, (barcode_clean[:255], product_name, brand, error_message))
            conn.commit()
        finally:
            _close(conn)
        return True
    except Exception as e:
        logger.warning("[MariaDB] فشل حفظ سجل الخطأ: %s", e)
        return False


def delete_product_failure(barcode):
    """حذف سجل الفشل عند نجاح مطابقة المنتج لاحقاً."""
    try:
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            barcode_clean = str(barcode).strip() if barcode else ""
            cursor.execute("DELETE FROM product_failures WHERE barcode = %s", (barcode_clean,))
            if barcode_clean.startswith("ERR_"):
                cursor.execute("DELETE FROM product_failures WHERE barcode LIKE %s", (barcode_clean + "%",))
            conn.commit()
        finally:
            _close(conn)
        return True
    except Exception as e:
        logger.warning("[MariaDB] فشل حذف سجل الخطأ: %s", e)
        return False


def get_product_failures():
    """استرجاع كافة المنتجات الفاشلة كـ dict."""
    try:
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM product_failures")
            rows = cursor.fetchall()
        finally:
            _close(conn)
        return {row["barcode"]: {"error_message": row["error_message"], "failed_at": row["failed_at"]}
                for row in rows if row["barcode"]}
    except Exception as e:
        logger.warning("[MariaDB] فشل استرجاع سجلات الأخطاء: %s", e)
        return {}


def save_feedback(feedback_id, asset_id, row_number, product_name, brand, image_url, reasons):
    """حفظ ملاحظة المراجع (تعرضها صفحة التعلم النشط في لوحة التحكم)."""
    try:
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO active_learning_feedback (feedback_id, asset_id, `row_number`, product_name, brand, image_url, rejection_reasons)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (feedback_id, asset_id, row_number, product_name, brand, image_url, json.dumps(reasons, ensure_ascii=False)))
            conn.commit()
        finally:
            _close(conn)
        return True
    except Exception as e:
        logger.warning("[MariaDB] فشل حفظ سجل الملاحظات: %s", e)
        return False


def get_active_learning_clutter_flag(brand):
    """
    (مسار v1 فقط) هل رُفضت صور هذا البراند مرتين على الأقل بسبب تداخل الخلفية؟
    """
    if not brand:
        return False
    try:
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT rejection_reasons FROM active_learning_feedback WHERE LOWER(brand) = %s",
                           (brand.strip().lower(),))
            rows = cursor.fetchall()
        finally:
            _close(conn)
        count = 0
        for r in rows:
            reasons = _loads(r.get("rejection_reasons"), [])
            if any("clutter" in str(x).lower() or "background" in str(x).lower() or "تداخل" in str(x)
                   or "خلفية" in str(x) for x in reasons):
                count += 1
        return count >= 2
    except Exception as e:
        logger.warning("[Active Learning] فشل حساب تداخل الخلفية للبراند: %s", e)
        return False


# ---------------------------------------------------------------------------
# طابور المهام (automation_queue)
# ---------------------------------------------------------------------------

def add_to_queue(row_number, barcode, name, brand, query, payload=None, sku_key=None, reprocess=False):
    """
    إضافة/تحديث صف في الطابور (Upsert على row_number).
    لا يعيد أبداً ضبط صف جاهز للمراجعة أو مكتمل (ولا صف قيد المعالجة بحجز ساري) إلا إذا reprocess=True
    أو تغيّر المنتج في هذا الصف (sku_key مختلف). تعيد True عند النجاح وترفع أخطاء قاعدة البيانات.
    """
    payload_json = json.dumps(payload, ensure_ascii=False) if payload is not None else None
    # ملاحظة: في ON DUPLICATE KEY UPDATE تُقيّم الإسنادات بالترتيب ويرى كل إسناد القيم المحدثة قبله؛
    # لذلك تأتي الأعمدة المعتمدة على الحالة القديمة أولاً، ثم status، ثم أعمدة الهوية.
    # نفس المنتج: نفس sku_key، أو صف قديم بلا sku_key (قبل الترقية) بنفس الاسم
    keep = ("(%s = 0 AND (sku_key <=> VALUES(sku_key) OR (sku_key IS NULL AND product_name <=> VALUES(product_name))) AND ("
            "status IN ('ready_for_review','completed') "
            "OR (status = 'processing' AND lease_until IS NOT NULL AND lease_until >= NOW())))")
    sql = f"""
        INSERT INTO automation_queue (`row_number`, barcode, product_name, brand, search_query, status,
                                      error_message, attempts, sku_key, payload_json, failure_code, updated_at)
        VALUES (%s, %s, %s, %s, %s, 'pending', NULL, 0, %s, %s, NULL, CURRENT_TIMESTAMP)
        ON DUPLICATE KEY UPDATE
            error_message = IF({keep}, error_message, NULL),
            attempts = IF({keep}, attempts, 0),
            failure_code = IF({keep}, failure_code, NULL),
            trace_json = IF({keep}, trace_json, NULL),
            worker_id = IF({keep}, worker_id, NULL),
            lease_until = IF({keep}, lease_until, NULL),
            status = IF({keep}, status, 'pending'),
            barcode = VALUES(barcode),
            product_name = VALUES(product_name),
            brand = VALUES(brand),
            search_query = VALUES(search_query),
            sku_key = VALUES(sku_key),
            payload_json = VALUES(payload_json),
            updated_at = CURRENT_TIMESTAMP
    """
    flag = 1 if reprocess else 0
    params = (row_number, barcode, name, brand, query, sku_key, payload_json) + (flag,) * 7
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(sql, params)
        conn.commit()
    finally:
        _close(conn)
    return True


def new_claim_id(worker_id=None):
    base = worker_id or f"{socket.gethostname()[:20]}:{os.getpid()}"
    return f"{base}#{uuid.uuid4().hex[:12]}"[:64]


def fetch_next_task(worker_id=None):
    """
    سحب ذري للمهمة التالية: UPDATE واحد يحجز الصف (pending، أو processing انتهى حجزه) بمعرف سحب فريد،
    ثم SELECT بذلك المعرف. شرط الحالة يتكرر في الـ WHERE الخارجي كي يفشل المتسابق الثاني بدل أن يسرق الصف.
    أخطاء قاعدة البيانات تُرفع (لا تتحول إلى None).
    """
    claim_id = new_claim_id(worker_id)
    claimable = "(status='pending' OR (status='processing' AND lease_until<NOW()))"
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE automation_queue SET status='processing', worker_id=%s, "
            f"lease_until=NOW() + INTERVAL {LEASE_MINUTES} MINUTE, attempts=attempts+1, updated_at=CURRENT_TIMESTAMP "
            "WHERE id=(SELECT id FROM (SELECT id FROM automation_queue WHERE "
            "status='pending' OR (status='processing' AND lease_until<NOW()) ORDER BY id LIMIT 1) t) "
            f"AND {claimable}",
            (claim_id,),
        )
        conn.commit()
        cursor.execute("SELECT * FROM automation_queue WHERE worker_id = %s LIMIT 1", (claim_id,))
        row = cursor.fetchone()
    finally:
        _close(conn)
    return dict(row) if row else None


def get_task_by_row(row_number):
    """صف الطابور لرقم صف الشيت (أو None). أخطاء قاعدة البيانات تُسجل وتعيد None."""
    try:
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM automation_queue WHERE `row_number` = %s", (row_number,))
            row = cursor.fetchone()
        finally:
            _close(conn)
        return dict(row) if row else None
    except Exception as e:
        logger.warning("[MariaDB Queue] فشل قراءة صف الطابور %s: %s", row_number, e)
        return None


def _trace_to_json(trace):
    if trace is None:
        return None
    try:
        return json.dumps(trace, ensure_ascii=False, default=str)
    except Exception:
        return None


def update_task_status(task_id, status, error_message=None, failure_code=None, trace=None):
    """تحديث حالة المهمة بعد المعالجة، مع رمز الفشل والـ trace، وتحرير الحجز."""
    try:
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE automation_queue
                SET status = %s, error_message = %s, failure_code = %s,
                    trace_json = COALESCE(%s, trace_json), lease_until = NULL, updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
            """, (status, error_message, failure_code, _trace_to_json(trace), task_id))
            conn.commit()
        finally:
            _close(conn)
        return True
    except Exception as e:
        logger.warning("[MariaDB Queue] فشل تحديث حالة المهمة %s: %s", task_id, e)
        return False


def update_task_status_by_row(row_number, status, error_message=None, failure_code=None):
    """تحديث حالة المهمة باستخدام رقم صف الشيت."""
    try:
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE automation_queue
                SET status = %s, error_message = %s, failure_code = %s, lease_until = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE `row_number` = %s
            """, (status, error_message, failure_code, row_number))
            conn.commit()
        finally:
            _close(conn)
        return True
    except Exception as e:
        logger.warning("[MariaDB Queue] فشل تحديث حالة المهمة للصف %s: %s", row_number, e)
        return False


def get_queue_statistics():
    """إحصائيات الطابور حسب الحالة (تشمل ready_for_review). أخطاء قاعدة البيانات تُرفع."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT status, COUNT(*) AS cnt FROM automation_queue GROUP BY status")
        rows = cursor.fetchall()
    finally:
        _close(conn)
    stats = {"total": 0, "pending": 0, "processing": 0, "ready_for_review": 0, "completed": 0, "failed": 0}
    for r in rows:
        status = r["status"] or "unknown"
        stats[status] = stats.get(status, 0) + int(r["cnt"])
        stats["total"] += int(r["cnt"])
    return stats


def count_open_tasks():
    """عدد المهام المفتوحة (pending أو processing) بـ COUNT(*) موثوق. أخطاء قاعدة البيانات تُرفع."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) AS cnt FROM automation_queue WHERE status IN ('pending','processing')")
        row = cursor.fetchone()
    finally:
        _close(conn)
    return int(row["cnt"])


def clear_queue():
    """تفريغ الطابور بالكامل (زر إعادة الضبط في لوحة التحكم فقط؛ الإدراج لا يستدعيه)."""
    try:
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM automation_queue")
            conn.commit()
        finally:
            _close(conn)
        return True
    except Exception as e:
        logger.warning("[MariaDB Queue] فشل مسح الطابور: %s", e)
        return False


def get_ready_for_review_count():
    """عدد المنتجات الجاهزة للمراجعة."""
    try:
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) AS count FROM automation_queue WHERE status = 'ready_for_review'")
            row = cursor.fetchone()
        finally:
            _close(conn)
        if row:
            return row['count']
    except Exception as e:
        logger.warning("[MariaDB Queue] فشل حساب المهام الجاهزة للمراجعة: %s", e)
    return 0


# ---------------------------------------------------------------------------
# مرشحات المراجعة (curation_candidates)
# ---------------------------------------------------------------------------

def _json_or_none(value):
    if value is None or value == "" or value == [] or value == {}:
        return None
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        return None


def _candidate_score(c):
    scores = c.get("scores") or {}
    for value in (scores.get("identity_score"), scores.get("relevance_score"), c.get("relevance_score")):
        if isinstance(value, (int, float)):
            return float(value)
    return None


def _as_int(value):
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def save_curation_candidates(row_number, product_name, brand, candidates, best_url=None, sku_key=None, run_id=None):
    """
    استبدال مرشحات الصف بمرشحات التشغيل الحالي في معاملة واحدة.
    تُحفظ الحالة والأسباب والأدلة وقراءة VLM لكل مرشح. is_selected=1 فقط للحالة 'preselected'
    (best_url لم يعد يحدد الاختيار المسبق). تعيد True عند النجاح و False عند أي خطأ (مع التراجع).
    """
    run_id = run_id or uuid.uuid4().hex[:16]
    try:
        conn = get_db_connection()
    except Exception as e:
        logger.warning("[Curation] تعذر الاتصال لحفظ مرشحات الصف %s: %s", row_number, e)
        return False
    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM curation_candidates WHERE `row_number` = %s", (row_number,))
        seen = set()
        for c in candidates or []:
            url = (c.get("url") or c.get("image_url") or "").strip()
            if not url or url in seen:
                continue
            seen.add(url)
            status = str(c.get("status") or "eligible")
            title = str(c.get("title") or c.get("page_title") or "")[:MAX_TITLE_CHARS]
            page_url = c.get("page_url") or None
            domain = c.get("domain") or ""
            if not domain:
                try:
                    from urllib.parse import urlparse
                    domain = urlparse(page_url or url).netloc
                except Exception:
                    domain = ""
            evidence = c.get("evidence")
            tier = c.get("identity_tier") or c.get("tier")
            if tier is None and isinstance(evidence, dict):
                tier = evidence.get("tier")
            cursor.execute("""
                INSERT INTO curation_candidates (
                    `row_number`, product_name, brand, image_url, title, width, height, clip_score, source_domain,
                    is_selected, status, sku_key, run_id, reasons_json, evidence_json, vlm_json, content_sha256,
                    identity_tier, page_url
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                row_number, product_name, brand, url, title, _as_int(c.get("width")), _as_int(c.get("height")),
                _candidate_score(c), str(domain)[:255], 1 if status == "preselected" else 0, status[:255],
                sku_key, run_id, _json_or_none(c.get("reasons")), _json_or_none(evidence),
                _json_or_none(c.get("vlm")), c.get("content_sha256") or None,
                (str(tier)[:8] if tier is not None else None), page_url,
            ))
        conn.commit()
        return True
    except Exception as e:
        logger.warning("[Curation] فشل حفظ مرشحات الصف %s: %s", row_number, e)
        try:
            conn.rollback()
        except Exception:
            pass
        return False
    finally:
        _close(conn)


def _loads(value, default):
    if not value:
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


def get_curation_candidates(row_number):
    """جلب المرشحات المحفوظة لصف معين (المختار مسبقاً أولاً)."""
    try:
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM curation_candidates WHERE `row_number` = %s ORDER BY is_selected DESC, id ASC",
                (row_number,),
            )
            rows = cursor.fetchall()
        finally:
            _close(conn)
        out = []
        for r in rows:
            out.append({
                "id": r["id"],
                "row_number": r["row_number"],
                "product_name": r["product_name"],
                "brand": r["brand"],
                "image_url": r["image_url"],
                "title": r["title"],
                "width": r["width"],
                "height": r["height"],
                "clip_score": r.get("clip_score"),
                "source_domain": r["source_domain"],
                "is_selected": r["is_selected"],
                "status": r["status"],
                "sku_key": r.get("sku_key"),
                "run_id": r.get("run_id"),
                "reasons": _loads(r.get("reasons_json"), []),
                "evidence": _loads(r.get("evidence_json"), {}),
                "vlm": _loads(r.get("vlm_json"), None),
                "content_sha256": r.get("content_sha256"),
                "identity_tier": r.get("identity_tier"),
                "page_url": r.get("page_url"),
            })
        return out
    except Exception as e:
        logger.warning("[Curation] فشل جلب مرشحات الصف %s: %s", row_number, e)
        return []


def delete_curation_candidates(row_number):
    """مسح كل مرشحات صف معين بعد اعتماده أو رفضه."""
    try:
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM curation_candidates WHERE `row_number` = %s", (row_number,))
            conn.commit()
        finally:
            _close(conn)
        return True
    except Exception as e:
        logger.warning("[Curation] فشل مسح مرشحات الصف %s: %s", row_number, e)
        return False


# ---------------------------------------------------------------------------
# حالة جلسة الأتمتة (automation_state)
# ---------------------------------------------------------------------------

def update_automation_state(status, total=None, processed=None, success=None, failed=None,
                            current_product=None, notice=None):
    """تحديث حالة ومؤشرات جلسة الأتمتة الجارية."""
    try:
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            updates = ["status = %s", "updated_at = CURRENT_TIMESTAMP"]
            params = [status]
            for column, value in (("total_items", total), ("processed_items", processed),
                                  ("success_count", success), ("failed_count", failed),
                                  ("current_product_name", current_product), ("notice", notice)):
                if value is not None:
                    updates.append(f"{column} = %s")
                    params.append(value[:255] if isinstance(value, str) else value)
            params.append("active_session")
            cursor.execute(f"UPDATE automation_state SET {', '.join(updates)} WHERE `key` = %s", params)
            conn.commit()
        finally:
            _close(conn)
        return True
    except Exception as e:
        logger.warning("[MariaDB State] فشل تحديث حالة الأتمتة: %s", e)
        return False


def get_automation_state():
    """الاستعلام عن حالة الأتمتة الحالية."""
    try:
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM automation_state WHERE `key` = 'active_session'")
            row = cursor.fetchone()
        finally:
            _close(conn)
        if row:
            return dict(row)
    except Exception as e:
        logger.warning("[MariaDB State] فشل استرداد حالة الأتمتة: %s", e)
    return {"status": "idle", "total_items": 0, "processed_items": 0, "success_count": 0, "failed_count": 0,
            "current_product_name": "", "pause_requested": 0}


def _set_pause(value):
    try:
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("UPDATE automation_state SET pause_requested = %s WHERE `key` = 'active_session'", (value,))
            conn.commit()
        finally:
            _close(conn)
        return True
    except Exception as e:
        logger.warning("[MariaDB State] فشل تعديل علم الإيقاف المؤقت: %s", e)
        return False


def pause_automation():
    """تعيين علم طلب الإيقاف المؤقت."""
    return _set_pause(1)


def resume_automation():
    """إلغاء علم الإيقاف المؤقت."""
    return _set_pause(0)


# تهيئة قاعدة البيانات تلقائياً عند استيراد الموديول للمرة الأولى
init_db()
