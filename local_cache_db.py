# local_cache_db.py
# موديول التخزين المحلي في MariaDB: طابور المهام، مرشحات المراجعة، كاش الحلول المعتمدة، ورفض المراجعين.
#
# القواعد (D11):
# - الكاش يخدم فقط الحلول بحالة human_approved أو auto_verified، ويعتمد على sku_key،
#   وعند وجود باركود لا يوجد أي رجوع للمطابقة بالاسم (منع إعطاء صورة منتج شقيق).
# - رفض المراجع يُسجل في rejected_images (الرابط + pHash) ويُمرر للبحث كاستبعادات.
# - سحب المهام من الطابور ذري (UPDATE واحد مع lease) ولا تُبتلع أخطاء قاعدة البيانات.

import contextlib
import json
import logging
import math
import os
import socket
import uuid
from collections import Counter

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
    # بصمة ألوان اللوحة (image_dedup_bktree.color_signature): تطابق pHash بلون مختلف بوضوح ليس الصورة نفسها
    "ALTER TABLE resolved_products ADD COLUMN IF NOT EXISTS color_signature VARCHAR(64) NULL",
    # automation_queue
    "ALTER TABLE automation_queue ADD COLUMN IF NOT EXISTS sku_key VARCHAR(64) NULL",
    "ALTER TABLE automation_queue ADD COLUMN IF NOT EXISTS payload_json LONGTEXT NULL",
    "ALTER TABLE automation_queue ADD COLUMN IF NOT EXISTS worker_id VARCHAR(64) NULL",
    "ALTER TABLE automation_queue ADD COLUMN IF NOT EXISTS lease_until DATETIME NULL",
    "ALTER TABLE automation_queue ADD COLUMN IF NOT EXISTS failure_code VARCHAR(32) NULL",
    "ALTER TABLE automation_queue ADD COLUMN IF NOT EXISTS trace_json LONGTEXT NULL",
    # التشغيل الذي يعالج الصف (begin_run): تقدم التشغيل يُحسب من صفوفه فقط وليس من الطابور كله
    "ALTER TABLE automation_queue ADD COLUMN IF NOT EXISTS run_id VARCHAR(64) NULL",
    "ALTER TABLE automation_queue ADD INDEX IF NOT EXISTS idx_queue_status (status)",
    "ALTER TABLE automation_queue ADD INDEX IF NOT EXISTS idx_queue_worker (worker_id)",
    "ALTER TABLE automation_queue ADD INDEX IF NOT EXISTS idx_queue_sku (sku_key)",
    "ALTER TABLE automation_queue ADD INDEX IF NOT EXISTS idx_queue_run (run_id)",
    # جدولة الطابور (P4a): مفتاح بديل بلا باركود، موعد المحاولة التالية وعداداتها، أولوية السحب، نوع المهمة
    # (بحث أو كتابة رابط معتمد)، منع النشر التلقائي لصف عُدل، سبب إعادة الإدراج، وبصمة البراند وقت الإدراج
    "ALTER TABLE automation_queue ADD COLUMN IF NOT EXISTS alt_sku_key VARCHAR(64) NULL",
    "ALTER TABLE automation_queue ADD COLUMN IF NOT EXISTS next_attempt_at DATETIME NULL",
    "ALTER TABLE automation_queue ADD COLUMN IF NOT EXISTS fail_count INT NOT NULL DEFAULT 0",
    "ALTER TABLE automation_queue ADD COLUMN IF NOT EXISTS down_count INT NOT NULL DEFAULT 0",
    "ALTER TABLE automation_queue ADD COLUMN IF NOT EXISTS reverify_count INT NOT NULL DEFAULT 0",
    "ALTER TABLE automation_queue ADD COLUMN IF NOT EXISTS priority TINYINT NOT NULL DEFAULT 0",
    "ALTER TABLE automation_queue ADD COLUMN IF NOT EXISTS task_kind VARCHAR(16) NULL",
    "ALTER TABLE automation_queue ADD COLUMN IF NOT EXISTS review_only TINYINT NOT NULL DEFAULT 0",
    "ALTER TABLE automation_queue ADD COLUMN IF NOT EXISTS requeue_reason VARCHAR(32) NULL",
    "ALTER TABLE automation_queue ADD COLUMN IF NOT EXISTS brand_fp VARCHAR(16) NULL",
    "ALTER TABLE automation_queue ADD COLUMN IF NOT EXISTS searched_at DATETIME NULL",
    "ALTER TABLE automation_queue ADD INDEX IF NOT EXISTS idx_queue_claim (status, priority, id)",
    # product_failures: سجل لكل منتج (sku_key) وليس لكل خلية باركود ('N/A' مشتركة بين منتجات كثيرة)
    "ALTER TABLE product_failures ADD COLUMN IF NOT EXISTS sku_key VARCHAR(64) NULL",
    "ALTER TABLE product_failures ADD UNIQUE INDEX IF NOT EXISTS uq_failure_sku (sku_key)",
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
    # automation_state: التشغيل الحالي، وطلب الإيقاف من لوحة التحكم (يلتزم به العامل بين المنتجات أو عند بدئه)
    "ALTER TABLE automation_state ADD COLUMN IF NOT EXISTS run_id VARCHAR(64) NULL",
    "ALTER TABLE automation_state ADD COLUMN IF NOT EXISTS stop_requested INT DEFAULT 0",
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

        # 9. قرارات المراجعين: صف لكل اعتماد أو رفض أو رفع يدوي مع ما عرضه المحرك (دليل فتح النشر الآلي)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS review_decisions (
                id INT AUTO_INCREMENT PRIMARY KEY,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                action VARCHAR(16) NOT NULL,
                sku_key VARCHAR(64) NULL,
                `row_number` INT NULL,
                brand VARCHAR(255) NULL,
                product_name VARCHAR(255) NULL,
                image_url TEXT NULL,
                page_domain VARCHAR(255) NULL,
                identity_tier VARCHAR(8) NULL,
                engine_decision VARCHAR(32) NULL,
                was_preselected TINYINT(1) NULL,
                vlm_decision VARCHAR(16) NULL,
                reason_code VARCHAR(32) NULL,
                INDEX idx_review_sku (sku_key),
                INDEX idx_review_brand (brand),
                INDEX idx_review_created (created_at)
            ) ENGINE=InnoDB CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
        """)

        # 10. الفهرس المحلي (catalog_match/local_index.py): روابط منتجات المتاجر من خرائط مواقعها (sitemaps)،
        # مع ما قالته صفحة المنتج عند قراءتها (الصورة والاسم والباركود). يبنيه scripts/build_catalog_index.py
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS catalog_products (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                store VARCHAR(32) NOT NULL,
                url TEXT NOT NULL,
                url_hash CHAR(40) NOT NULL,
                slug_text VARCHAR(512) NULL,
                lastmod VARCHAR(32) NULL,
                first_seen TIMESTAMP NULL DEFAULT NULL,
                last_seen TIMESTAMP NULL DEFAULT NULL,
                page_checked_at TIMESTAMP NULL DEFAULT NULL,
                page_status VARCHAR(32) NULL,
                page_title VARCHAR(512) NULL,
                image_url TEXT NULL,
                image_width INT NULL,
                image_height INT NULL,
                gtin VARCHAR(14) NULL,
                UNIQUE KEY uq_catalog_url (url_hash),
                INDEX idx_catalog_store_seen (store, last_seen),
                INDEX idx_catalog_gtin (gtin)
            ) ENGINE=InnoDB CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
        """)
        # كلمات رابط المنتج (وعنوان صفحته بعد قراءتها): البحث بكلمات الماركة بدون مسح الجدول كله
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS catalog_tokens (
                token VARCHAR(64) NOT NULL,
                product_id BIGINT NOT NULL,
                PRIMARY KEY (token, product_id),
                INDEX idx_catalog_tokens_product (product_id),
                CONSTRAINT fk_catalog_tokens_product FOREIGN KEY (product_id)
                    REFERENCES catalog_products (id) ON DELETE CASCADE
            ) ENGINE=InnoDB CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
        """)
        # سجل كل جمع لخرائط متجر: الحالة (ok / blocked / error / partial) وعدد الروابط
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS catalog_harvests (
                id INT AUTO_INCREMENT PRIMARY KEY,
                store VARCHAR(32) NOT NULL,
                started_at TIMESTAMP NULL DEFAULT NULL,
                finished_at TIMESTAMP NULL DEFAULT NULL,
                status VARCHAR(16) NOT NULL,
                sitemaps_read INT NOT NULL DEFAULT 0,
                urls_seen INT NOT NULL DEFAULT 0,
                product_urls INT NOT NULL DEFAULT 0,
                new_urls INT NOT NULL DEFAULT 0,
                pruned INT NOT NULL DEFAULT 0,
                error VARCHAR(255) NULL,
                INDEX idx_catalog_harvests_store (store)
            ) ENGINE=InnoDB CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
        """)

        # 11. ما تعلّمه البحث من المراجعة (catalog_match/learning.py): كتابة المتاجر لماركة الشيت كما اعتمدها المراجع
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS learned_brand_aliases (
                id INT AUTO_INCREMENT PRIMARY KEY,
                brand_key VARCHAR(255) NOT NULL,
                sheet_brand VARCHAR(255) NOT NULL,
                alias VARCHAR(255) NOT NULL,
                approvals INT NOT NULL DEFAULT 0,
                rejections INT NOT NULL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                UNIQUE KEY uq_learned_alias (brand_key, alias)
            ) ENGINE=InnoDB CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
        """)

        # 12. سجل التشغيلات (run_report.py): صف لكل تشغيل من لوحة التحكم أو يدوي، وصف واحد لكل ليلة
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS run_history (
                id INT AUTO_INCREMENT PRIMARY KEY,
                run_id VARCHAR(64) NULL,
                run_trigger VARCHAR(16) NOT NULL DEFAULT 'manual',
                started_at DATETIME NULL,
                ended_at DATETIME NULL,
                outcome VARCHAR(16) NOT NULL,
                stop_reason VARCHAR(64) NULL,
                exit_code INT NOT NULL DEFAULT 0,
                attempts INT NOT NULL DEFAULT 1,
                enqueued INT NULL,
                searched INT NULL,
                auto_published INT NULL,
                ready_for_review INT NULL,
                not_found INT NULL,
                failed INT NULL,
                provider_down INT NULL,
                pending_left INT NULL,
                outbox_pending INT NULL,
                outbox_conflict INT NULL,
                outbox_dead INT NULL,
                spend_usd DECIMAL(12,4) NULL,
                spend_source VARCHAR(16) NULL,
                notices TEXT NULL,
                report_json LONGTEXT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_run_history_started (started_at),
                INDEX idx_run_history_run (run_id)
            ) ENGINE=InnoDB CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
        """)
        # 13. سجل الصرف اليومي للبحث (P4a): استدعاءات كل مزود مدفوع وتكلفتها التقديرية لكل يوم وتشغيل،
        # يُقرأ قبل كل سحب مهمة عند ضبط DAILY_BUDGET_USD
        cursor.execute(SPEND_TABLE_SQL)

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
        _rekey_junk_failures(cursor)

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


def _cache_barcode(barcode):
    """
    الباركود كما يُستخدم مفتاحاً للكاش: النص المنظف فقط عندما يكون GTIN صالحاً (رقم تحقق سليم).
    خلايا مثل 'N/A' و'-' و'0' و'6.29E+12' تشترك فيها منتجات مختلفة، فلا تُستخدم للبحث أو التحديث أبداً.
    """
    text = str(barcode).strip() if barcode is not None else ""
    if not text:
        return ""
    try:
        from catalog_match.gtin import normalize_gtin
        _gtin14, status = normalize_gtin(text)
    except Exception:
        return ""
    return text if status == "ok" else ""


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
        "color_signature": row.get("color_signature"),
        "resolved_at": row.get("resolved_at"),
        "source": "mariadb_cache",
    }


def get_cached_product(barcode=None, product_name=None, brand=None, sku_key=None, brand_mappings=None,
                       size_text=None, strict=False):
    """
    الاستعلام من الكاش. لا يخدم إلا الحلول المعتمدة (human_approved / auto_verified).
    - بـ sku_key أولاً إن مُرر.
    - عند وجود باركود GTIN صالح: بحث صارم بالباركود فقط، ولا رجوع للاسم أبداً.
      الباركود غير الصالح ('N/A' أو '0' أو '6.29E+12') لا يُستخدم مفتاحاً أبداً: قد تشترك فيه منتجات مختلفة.
    - بدون باركود صالح ولا sku_key: مطابقة دقيقة للاسم والبراند.
    خطأ القراءة يُعامل كعدم وجود (None) ويُسجل.

    هوية المنتج هي البراند والاسم، والباركود دليل مساعد فقط (قد يكون خاطئاً أو مشتركاً بين منتجين):
    سجل وُجد بالباركود (أو بـ sku_key هو GTIN) لا يُخدم إلا إذا طابق براندُه البراندَ المطلوب
    (بعد التطبيع ومرادفات جدول البراندات إن مُررت brand_mappings) واتفق الاسمان
    (نفس كلمات المنتج ونفس الحجم والنوع). وإلا يُتجاهل الكاش ويجري البحث (identity_mismatch).
    يُطبق هذا الفحص عندما يمرر المستدعي product_name أو brand؛ استعلام حالة الاعتماد بـ sku_key وحده لا يتغير.
    size_text: خلية الحجم (SIZE) في الشيت إن وُجدت؛ تدخل في حجم المنتج المطلوب، والسجل المخزن لا يحفظ إلا
    الاسم، فحجم لا يذكره إلا عمود الحجم لا يمكن تأكيده (يُتجاهل الكاش).
    strict=True: خطأ قاعدة البيانات يُرفع بدل None (لمن يجب أن يعامل الخطأ كـ «يوجد اعتماد»، مثل النشر التلقائي).
    """
    barcode_clean = _cache_barcode(barcode)
    sku_clean = str(sku_key).strip() if sku_key else ""
    try:
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            row = None
            barcode_keyed = False
            if sku_clean:
                cursor.execute(
                    f"SELECT * FROM resolved_products WHERE sku_key = %s AND {_SERVABLE_SQL} "
                    "ORDER BY resolved_at DESC, id DESC LIMIT 1",
                    (sku_clean,),
                )
                row = cursor.fetchone()
                barcode_keyed = row is not None and _gtin_sku_key(sku_clean)
            if row is None and barcode_clean:
                cursor.execute(
                    f"SELECT * FROM resolved_products WHERE barcode = %s AND {_SERVABLE_SQL} "
                    "ORDER BY resolved_at DESC, id DESC LIMIT 1",
                    (barcode_clean,),
                )
                row = cursor.fetchone()
                barcode_keyed = row is not None
            elif row is None and not sku_clean and product_name:
                cursor.execute(
                    f"SELECT * FROM resolved_products WHERE LOWER(product_name) = %s AND LOWER(brand) = %s "
                    f"AND {_SERVABLE_SQL} ORDER BY resolved_at DESC, id DESC LIMIT 1",
                    (product_name.strip().lower(), (brand or "").strip().lower()),
                )
                row = cursor.fetchone()
        finally:
            _close(conn)
        if row and barcode_keyed and (product_name or brand):
            if not cached_row_matches(row, product_name, brand, brand_mappings, size_text=size_text):
                logger.info("[MariaDB Cache] الصورة المخزنة لهذا الباركود تخص منتجاً آخر (%r / %r)؛ "
                            "يُتجاهل الكاش لـ %r ويجري البحث (identity_mismatch).",
                            row.get("brand"), row.get("product_name"), product_name)
                return None
        return _cache_row_to_dict(row) if row else None
    except Exception as e:
        logger.warning("[MariaDB Cache] خطأ أثناء القراءة من الكاش: %s", e)
        if strict:
            raise
        return None


# --- identity package (P3): a barcode-keyed cache row must be the same product -------------------

# Product-type words of the two names must overlap at least this much (shared / all, both names).
CACHE_NAME_JACCARD = 0.75


def _gtin_sku_key(sku_key):
    """True when the sku_key is a GTIN-14 (identity.make_sku_key uses the barcode when it is valid)."""
    return len(sku_key) == 14 and sku_key.isdigit()


def _cache_brands_agree(req, got, req_name, got_name):
    """Same brand: equal sheet brand (spacing/punctuation ignored), the same mapped brand, or a brand
    phrase of one side in the other's brand. With one side's brand empty, the other's brand must be
    named in that side's product name. Both empty: nothing to compare (the names decide)."""
    from catalog_match.text_norm import any_phrase_in, match_key, normalize

    k_req = match_key(req.brand_raw).replace(" ", "")
    k_got = match_key(got.brand_raw).replace(" ", "")
    if not k_req and not k_got:
        return True
    if k_req and k_got:
        if k_req == k_got:
            return True
        if (req.brand_conf == "mapped" and got.brand_conf == "mapped" and req.brand_canonical
                and normalize(req.brand_canonical) == normalize(got.brand_canonical)):
            return True
        return bool(any_phrase_in(req.match_brands, got.brand_raw) or any_phrase_in(got.match_brands, req.brand_raw))
    if k_req:
        return bool(any_phrase_in(req.match_brands, got_name))
    return bool(any_phrase_in(got.match_brands, req_name))


def _cache_brand_words(*specs):
    """Every token (and joined spelling: 'al marai' -> 'almarai') of either side's brand phrases."""
    from catalog_match.text_norm import tokens

    out = set()
    for spec in specs:
        for phrase in (spec.brand_raw, spec.brand_canonical, spec.brand_ar) + tuple(spec.match_brands):
            toks = tokens(phrase or "", strip_clitics=True)
            out.update(toks)
            if toks:
                out.add("".join(toks))
    return out


def _cache_class_words(spec, brand_words):
    """The product-type words of a name (plural 's' folded), without any spelling of either brand."""
    from catalog_match.text_norm import is_arabic, tokens

    out = set()
    for word in spec.class_tokens:
        for tok in tokens(word, strip_clitics=True):
            if tok in brand_words:
                continue
            if not is_arabic(tok) and len(tok) > 3 and tok.endswith("s") and not tok.endswith("ss"):
                tok = tok[:-1]
            out.add(tok)
    return out


def cached_row_matches(row, product_name, brand, brand_mappings=None, size_text=None):
    """
    Does a cached resolution found by barcode describe the requested product? Fail closed: any
    doubt or error answers False (the cache is ignored and the product is searched).

    Same product means: the brands agree (see _cache_brands_agree), the stated sizes and pack
    counts match (a size stated on one side only is a doubt), the variant readings are identical
    (full fat vs low fat, diet, a flavour), and the product-type words overlap with a Jaccard
    similarity of at least CACHE_NAME_JACCARD.

    size_text is the requested row's sheet SIZE cell: it is part of that product's size (merged
    with its name, as identity.build_sku_spec does). The stored row keeps only its name, so a
    size that only the column states is not confirmed and the row is not served.
    """
    try:
        from catalog_match.identity import build_sku_spec
        from catalog_match.sizes import compare

        req_name = str(product_name or "").strip()
        got_name = str(row.get("product_name") or "").strip()
        if not req_name or not got_name:
            return False
        req = build_sku_spec({"name": req_name, "brand": str(brand or "").strip()}, brand_mappings or None,
                             size_text=str(size_text).strip() if size_text else None)
        got = build_sku_spec({"name": got_name, "brand": str(row.get("brand") or "").strip()},
                             brand_mappings or None)
        return _specs_agree(req, got, req_name, got_name, compare)
    except Exception as e:  # fail closed
        logger.warning("[MariaDB Cache] تعذر مقارنة هوية السجل المخزن: %s", e)
        return False


def _specs_agree(req, got, req_name, got_name, compare, one_sided_size=False):
    """
    قاعدة «نفس المنتج» لـ cached_row_matches و same_product: البراند، الحجم والعبوة، النوع، وكلمات الاسم.
    one_sided_size: حجم يذكره طرف واحد فقط ليس اختلافاً (صفان بنفس الباركود: الباركود يحدد الحجم).
    """
    if not _cache_brands_agree(req, got, req_name, got_name):
        return False
    if (req.size is None) != (got.size is None) and not one_sided_size:
        return False
    if req.size is not None and got.size is not None:
        if compare(req.size, [got.size]) != "match" or (req.pack_count or 1) != (got.pack_count or 1):
            return False
    if dict(req.variants) != dict(got.variants):
        return False
    brand_words = _cache_brand_words(req, got)
    a, b = _cache_class_words(req, brand_words), _cache_class_words(got, brand_words)
    if not a and not b:
        return True
    return len(a & b) / len(a | b) >= CACHE_NAME_JACCARD


def _identity_text(row, *keys):
    for key in keys:
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return ""


def queue_row_identity(row):
    """
    هوية صف الشيت كما سُجلت في صف الطابور عند الإدراج: {name, brand, name_ar, brand_ar, size}. تقبل أيضاً قاموس
    هوية جاهزاً (name أو product_name، و name_ar أو product_name_ar).
    """
    row = row or {}
    payload = row.get("payload_json")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload) if payload else {}
        except ValueError:
            payload = {}
    payload = payload if isinstance(payload, dict) else {}
    merged = dict(payload, **{k: v for k, v in row.items() if v not in (None, "")})
    return {"name": _identity_text(merged, "name", "product_name"), "brand": _identity_text(merged, "brand"),
            "name_ar": _identity_text(merged, "name_ar", "product_name_ar"),
            "brand_ar": _identity_text(merged, "brand_ar"), "size": _identity_text(merged, "size")}


def _names_agree(a_name, a_brand, a_size, b_name, b_brand, b_size, brand_mappings, extra_a=None, extra_b=None):
    from catalog_match.identity import build_sku_spec
    from catalog_match.sizes import compare

    if not a_name or not b_name:
        return False
    req = build_sku_spec(dict({"name": a_name, "brand": a_brand, "size": a_size}, **(extra_a or {})),
                         brand_mappings or None)
    got = build_sku_spec(dict({"name": b_name, "brand": b_brand, "size": b_size}, **(extra_b or {})),
                         brand_mappings or None)
    # المفتاح بلا باركود يتضمن الحجم، وبباركود يحدده الباركود: حجم في صف واحد فقط ليس منتجاً آخر
    return _specs_agree(req, got, a_name, b_name, compare, one_sided_size=True)


def same_product(a, b, brand_mappings=None):
    """
    هل يصف صفّا الشيت هذان المنتج نفسه؟ sku_key وحده لا يكفي: منتجان مختلفان قد يتشاركان خلية باركود واحدة،
    والمفتاح بلا باركود لا يقرأ الاسم العربي. a و b: queue_row_identity (أو صف طابور / قاموس هوية).
    نفس قاعدة cached_row_matches على الاسم والبراند الإنجليزيين (والحجم من خلية الحجم أو الاسم)، ثم الاسمان العربيان
    (مع البراند العربي) بالقاعدة نفسها عندما يكون لكلا الصفين اسم عربي. أي شك أو خطأ = لا (لا يُكتب في صف الآخر).
    """
    try:
        from catalog_match.text_norm import normalize

        a, b = queue_row_identity(a), queue_row_identity(b)
        brand_a = a["brand"] or a["brand_ar"]
        brand_b = b["brand"] or b["brand_ar"]
        if a["name"] or b["name"]:
            if not _names_agree(a["name"], brand_a, a["size"], b["name"], brand_b, b["size"], brand_mappings):
                return False
        elif not (a["name_ar"] and b["name_ar"]):
            return False
        if a["name_ar"] and b["name_ar"] and normalize(a["name_ar"]) != normalize(b["name_ar"]):
            return _names_agree(a["name_ar"], brand_a, a["size"], b["name_ar"], brand_b, b["size"], brand_mappings,
                                {"brand_ar": a["brand_ar"]}, {"brand_ar": b["brand_ar"]})
        return True
    except Exception as e:  # fail closed
        logger.warning("[MariaDB Queue] تعذر مقارنة هوية صفين: %s", e)
        return False


def _remember_phash(hash_str, row_id, cloudinary_url, product_name):
    """Adds a saved image to the in-memory duplicate index, if it has a hash."""
    if not hash_str or row_id is None:
        return
    try:
        import image_dedup_bktree
        image_dedup_bktree.remember_image(hash_str, str(row_id), cloudinary_url, product_name)
    except Exception as e:
        logger.warning("[BKTree] Could not add the saved image to the duplicate index: %s", e)


def save_product_resolution(barcode, product_name, brand, original_url, cloudinary_url, clip_score=None,
                            metadata=None, clip_embedding=None, perceptual_hash=None,
                            verification_status="legacy", approved_by=None, sku_key=None, color_signature=None):
    """
    حفظ أو تحديث الحل المعتمد لمنتج (Upsert بـ sku_key، أو بالباركود إن لم يوجد sku_key).
    أحدث سجل مطابق يُحدّث، وأي سجلات مطابقة أخرى تصبح superseded.
    حل auto_verified لا يحل أبداً محل اعتماد بشري (human_approved): إذا كان أي سجل مطابق معتمداً بشرياً
    لا يُكتب شيء وتعيد False (مراجع اعتمد أثناء نشر العامل التلقائي). الفحص والكتابة في معاملة واحدة تقفل السجلات
    المطابقة (SELECT ... FOR UPDATE): اعتماد بشري يُكتب في اللحظة نفسها ينتظر أو يُرى، ولا يُكتب فوقه أبداً.
    """
    if verification_status not in VERIFICATION_STATUSES:
        raise ValueError(f"verification_status غير صالح: {verification_status!r}")
    barcode_raw = str(barcode).strip() if barcode else ""
    barcode_clean = _cache_barcode(barcode)   # مفتاح المطابقة: GTIN صالح فقط
    sku_clean = str(sku_key).strip() if sku_key else ""
    metadata_str = json.dumps(metadata, ensure_ascii=False) if metadata else ""
    embedding_str = json.dumps(clip_embedding) if clip_embedding is not None else ""
    hash_str = str(perceptual_hash) if perceptual_hash is not None else ""
    values = (barcode_raw, product_name, brand, original_url, cloudinary_url, clip_score,
              metadata_str, embedding_str, hash_str, sku_clean or None, verification_status, approved_by,
              str(color_signature)[:64] if color_signature else None)

    def attempt():
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            found = {}
            # كل مفتاح باستعلامه (فهرسه): القفل على السجلات المطابقة وفجواتها فقط، لا على الجدول
            for column, value in (("sku_key", sku_clean), ("barcode", barcode_clean)):
                if value:
                    cursor.execute(f"SELECT id, verification_status FROM resolved_products WHERE {column} = %s "
                                   "FOR UPDATE", (value,))
                    for r in cursor.fetchall() or []:
                        found[r["id"]] = r
            existing = sorted(found, reverse=True)
            if verification_status == "auto_verified" and any(
                    r.get("verification_status") == "human_approved" for r in found.values()):
                conn.rollback()
                logger.warning("[MariaDB Cache] لا يُحفظ نشر تلقائي فوق اعتماد بشري لـ '%s' (SKU %s).",
                               product_name, sku_clean or barcode_clean)
                return None
            if existing:
                cursor.execute("""
                    UPDATE resolved_products
                    SET barcode = %s, product_name = %s, brand = %s, original_url = %s, cloudinary_url = %s,
                        clip_score = %s, metadata_json = %s, clip_embedding_json = %s, perceptual_hash = %s,
                        sku_key = %s, verification_status = %s, approved_by = %s, color_signature = %s,
                        resolved_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                """, values + (existing[0],))
                saved_id = existing[0]
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
                        verification_status, approved_by, color_signature)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, values)
                saved_id = cursor.lastrowid
            conn.commit()
            return saved_id
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
        finally:
            _close(conn)

    try:
        # حفظان متزامنان لمنتج بلا سجل: قفل الفجوة يجعل أحدهما deadlock فيُعاد ويرى سجل الآخر
        saved_id = _retry_lock_conflicts(attempt)
    except Exception as e:
        logger.warning("[MariaDB Cache] فشل حفظ الحل المعتمد لـ '%s': %s", product_name, e)
        return False
    if saved_id is None:
        return False
    _remember_phash(hash_str, saved_id, cloudinary_url, product_name)
    if verification_status in SERVABLE_STATUSES:
        delete_product_failure(barcode_raw or f"ERR_{product_name}_{brand}".replace(" ", "_"),
                               sku_key=sku_clean or None, product_name=product_name, brand=brand)
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
    barcode_clean = _cache_barcode(barcode)
    if barcode_clean:
        clauses.append("barcode = %s")
        params.append(barcode_clean)
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


# صورة منشورة «لمنتج آخر»: نفس رابط Cloudinary، أو pHash للوحة النهائية على هذه المسافة أو أقل
DUPLICATE_PHASH_DISTANCE = 4


def _colors_differ(a, b):
    """image_dedup_bktree.colors_differ؛ تعذر المقارنة = لا اختلاف (يبقى التطابق تكراراً)."""
    try:
        import image_dedup_bktree
        return image_dedup_bktree.colors_differ(a, b)
    except Exception as e:
        logger.warning("[MariaDB Cache] تعذر مقارنة بصمتي الألوان: %s", e)
        return False


def _phash_int(value):
    """pHash بصيغة v2 (16 خانة hex، catalog_match.fetch.phash_hex) كعدد، أو None لغير ذلك (القيم القديمة)."""
    text = str(value or "").strip().lower()
    if len(text) != 16:
        return None
    try:
        return int(text, 16)
    except ValueError:
        return None


def find_image_owners(cloudinary_url=None, phash=None, sku_key=None, product_name=None,
                      max_distance=DUPLICATE_PHASH_DISTANCE, color_signature=None):
    """
    المنتجات الأخرى التي نُشرت لها نفس الصورة: نفس رابط Cloudinary (الرفع يسمي الملف ببصمة بايتاته، فنفس
    اللوحة = نفس الرابط، تكرار دائماً)، أو pHash اللوحة النهائية على مسافة max_distance أو أقل ولونها غير مختلف بوضوح:
    pHash (32x32 رمادي) لا يرى اللون، فنفس العبوة بملصق أحمر وأزرق (نكهتان) مسافتها صفر. color_signature: بصمة ألوان
    اللوحة (image_dedup_bktree.color_signature)؛ تطابق pHash مع بصمة ألوان مختلفة بوضوح ليس تكراراً، وبصمة غائبة
    (هنا أو لصورة نُشرت قبلها) لا تُسقط التطابق. يقرأ الحلول المعتمدة فقط
    (human_approved / auto_verified). «منتج آخر» = sku_key مختلف؛ سجل قديم بلا sku_key يُعد منتجاً آخر إذا اختلف
    اسمه. تعيد [{sku_key, product_name, brand, cloudinary_url, verification_status, match, distance}] (منتج واحد
    لكل مالك، الأقرب أولاً)، أو None عند خطأ قاعدة البيانات (النشر التلقائي يعامله كتكرار).
    """
    url = str(cloudinary_url or "").strip()
    target = _phash_int(phash)
    if not url and target is None:
        return []
    sku = str(sku_key or "").strip()
    name = " ".join(str(product_name or "").lower().split())
    try:
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT sku_key, product_name, brand, cloudinary_url, perceptual_hash, verification_status, "
                f"color_signature FROM resolved_products WHERE {_SERVABLE_SQL} AND (cloudinary_url = %s "
                "OR (perceptual_hash IS NOT NULL AND perceptual_hash <> ''))",
                (url,),
            )
            rows = cursor.fetchall()
        finally:
            _close(conn)
    except Exception as e:
        logger.warning("[MariaDB Cache] تعذر فحص تكرار الصورة المنشورة: %s", e)
        return None
    owners = {}
    for r in rows:
        owner_sku = str(r.get("sku_key") or "").strip()
        owner_name = " ".join(str(r.get("product_name") or "").lower().split())
        if owner_sku and sku:
            if owner_sku == sku:
                continue
        elif owner_name == name:
            continue
        match, distance = None, None
        if url and str(r.get("cloudinary_url") or "").strip() == url:
            match, distance = "url", 0
        else:
            other = _phash_int(r.get("perceptual_hash"))
            if target is not None and other is not None:
                d = bin(target ^ other).count("1")
                if d <= max_distance and not _colors_differ(color_signature, r.get("color_signature")):
                    match, distance = "phash", d
        if match is None:
            continue
        key = owner_sku or f"name:{owner_name}"
        if key in owners and owners[key]["distance"] <= distance:
            continue
        owners[key] = {"sku_key": owner_sku or None, "product_name": r.get("product_name"), "brand": r.get("brand"),
                       "cloudinary_url": r.get("cloudinary_url"), "verification_status": r.get("verification_status"),
                       "match": match, "distance": distance}
    return sorted(owners.values(), key=lambda o: o["distance"])


# مهلة انتظار قفل النشر لنفس الـ SKU (عامل آخر أو مراجع يكتب نفس المنتج الآن)
PUBLISH_LOCK_SECONDS = 60


@contextlib.contextmanager
def sku_publish_lock(sku_key, timeout=PUBLISH_LOCK_SECONDS):
    """
    قفل MariaDB مسمى (GET_LOCK) لكل SKU حول «إعادة التحقق ثم الكتابة في الشيت» عند النشر: النشر التلقائي
    واعتماد المراجع لنفس المنتج لا يتداخلان، فمن يكتب ثانياً يرى ما سجله الأول قبل أن يكتب.
    يعطي 'held' عند الحصول عليه، و'busy' إذا انتهت المهلة والقفل عند غيره، و'unavailable' إذا تعذر الاتصال
    بقاعدة البيانات (المستدعي يعيد التحقق بنفسه، والتحقق يفشل مغلقاً عند تعطل القاعدة)، و'none' بلا sku_key.
    القفل يُحرر عند الخروج، أو تلقائياً إذا انقطع الاتصال (توقف العملية لا يتركه معلقاً).
    """
    sku = str(sku_key or "").strip()
    if not sku:
        yield "none"
        return
    name = f"lq_publish:{sku}:{os.getenv('DB_DATABASE', 'automation_db')}"[:64]
    conn, state = None, "unavailable"
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT GET_LOCK(%s, %s) AS got", (name, int(timeout)))
        state = "held" if (cursor.fetchone() or {}).get("got") == 1 else "busy"
    except Exception as e:
        logger.warning("[MariaDB Publish] تعذر أخذ قفل النشر للـ SKU %s: %s", sku, e)
        state = "unavailable"
    try:
        yield state
    finally:
        if conn is not None:
            if state == "held":
                try:
                    conn.cursor().execute("SELECT RELEASE_LOCK(%s) AS released", (name,))
                except Exception:
                    pass
            _close(conn)


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

def failure_key(barcode, product_name, brand):
    """
    مفتاح عرض سجل الفشل (عمود barcode، الذي تطابقه لوحة التحكم و cli_bridge): الباركود عندما يكون GTIN صالحاً،
    وإلا ERR_<الاسم>_<البراند>. خلية باركود مثل 'N/A' أو '0' تشترك فيها منتجات كثيرة، فلا تكون مفتاحاً أبداً
    (كانت سجلات هذه المنتجات تكتب فوق بعضها وتظهر الخطأ نفسه على كل صف 'N/A').
    """
    return (_cache_barcode(barcode) or f"ERR_{product_name}_{brand}".replace(" ", "_"))[:255]


def _rekey_junk_failures(cursor):
    """
    ترقية لمرة واحدة (Idempotent): سجلات قديمة مفتاحها خلية باركود غير صالحة ('N/A') تنتقل إلى مفتاح المنتج
    ERR_<الاسم>_<البراند> المحفوظ في السجل نفسه. إن وُجد سجل بذلك المفتاح فهو سجل المنتج نفسه ويُحذف القديم.
    """
    cursor.execute("SELECT barcode, product_name, brand FROM product_failures "
                   "WHERE sku_key IS NULL AND barcode NOT LIKE 'ERR\\_%%'")
    for row in cursor.fetchall() or []:
        old = row["barcode"]
        if not old or _cache_barcode(old):
            continue
        new = failure_key("", row["product_name"], row["brand"] or "")
        cursor.execute("SELECT 1 FROM product_failures WHERE barcode = %s", (new,))
        if cursor.fetchone():
            cursor.execute("DELETE FROM product_failures WHERE barcode = %s", (old,))
        else:
            cursor.execute("UPDATE product_failures SET barcode = %s WHERE barcode = %s", (new, old))


def save_product_failure(barcode, product_name, brand, error_message, sku_key=None):
    """
    تسجيل فشل منتج حقيقي (لا تُسجل هنا انقطاعات المزودين). سجل واحد لكل منتج: بـ sku_key عند تمريره
    (عمود فريد)، ومفتاح العرض failure_key. منتج آخر يحمل مفتاح العرض نفسه (نفس الاسم والبراند وحجم آخر)
    لا يُكتب فوقه: يُضاف sku_key إلى المفتاح.
    """
    sku_clean = str(sku_key).strip()[:64] if sku_key else ""
    key = failure_key(barcode, product_name, brand or "")
    try:
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            if sku_clean:
                cursor.execute("DELETE FROM product_failures WHERE sku_key = %s", (sku_clean,))
                cursor.execute("SELECT sku_key FROM product_failures WHERE barcode = %s", (key,))
                holder = cursor.fetchone()
                if holder and holder.get("sku_key") and holder["sku_key"] != sku_clean:
                    key = f"{key[:190]}#{sku_clean}"
            cursor.execute("""
                REPLACE INTO product_failures (barcode, product_name, brand, error_message, failed_at, sku_key)
                VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP, %s)
            """, (key, product_name, brand, error_message, sku_clean or None))
            conn.commit()
        finally:
            _close(conn)
        return True
    except Exception as e:
        logger.warning("[MariaDB] فشل حفظ سجل الخطأ: %s", e)
        return False


def delete_product_failure(barcode, sku_key=None, product_name=None, brand=None):
    """
    حذف سجل الفشل عند نجاح مطابقة المنتج لاحقاً: بالمفتاح الممرر (السلوك القديم)، وبـ sku_key،
    وبمفتاح العرض failure_key عند تمرير الاسم.
    """
    try:
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            barcode_clean = str(barcode).strip() if barcode else ""
            keys = [barcode_clean] if barcode_clean else []
            if product_name:
                keys.append(failure_key(barcode, product_name, brand or ""))
            for key in dict.fromkeys(keys):
                cursor.execute("DELETE FROM product_failures WHERE barcode = %s", (key,))
                if key.startswith("ERR_"):
                    cursor.execute("DELETE FROM product_failures WHERE barcode LIKE %s", (key + "%",))
            if sku_key:
                cursor.execute("DELETE FROM product_failures WHERE sku_key = %s", (str(sku_key).strip(),))
            conn.commit()
        finally:
            _close(conn)
        return True
    except Exception as e:
        logger.warning("[MariaDB] فشل حذف سجل الخطأ: %s", e)
        return False


def get_product_failures():
    """استرجاع كافة المنتجات الفاشلة كـ dict بمفتاح العرض (عمود barcode)، وبـ sku_key أيضاً للسجلات الجديدة."""
    try:
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM product_failures")
            rows = cursor.fetchall()
        finally:
            _close(conn)
        out = {}
        for row in rows:
            entry = {"error_message": row["error_message"], "failed_at": row["failed_at"]}
            if row.get("sku_key"):
                out.setdefault(row["sku_key"], entry)
            if row["barcode"]:
                out[row["barcode"]] = entry
        return out
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
# قرارات المراجعين (review_decisions): دليل فتح النشر الآلي لكل براند
# ---------------------------------------------------------------------------

REVIEW_ACTIONS = ("approved", "rejected", "manual_upload")
# قرارات البحث التي يختار فيها المحرك صورة مسبقاً (نفس PICK_DECISIONS في catalog_match.facade)
PICK_DECISIONS = ("AUTO_PUBLISH", "REVIEW_PRESELECTED")
# يجهز براند للنشر الآلي عندما يُراجع 30 اختياراً مسبقاً على الأقل ويبلغ الحد الأدنى لفاصل ويلسون (95%)
# لنسبة قبولها 98%. عملياً: مع قبول كل الاختيارات يلزم 189 مراجعة لبلوغ 98%.
AUTO_PUBLISH_MIN_REVIEWED = 30
AUTO_PUBLISH_MIN_LOWER_BOUND = 0.98
WILSON_Z = 1.96
TOP_REJECT_REASONS = 3
_REVIEWS_NEEDED_CAP = 100000


def _clip(value, limit):
    text = str(value).strip() if value is not None else ""
    return text[:limit] or None


def add_review_decision(action, sku_key=None, row_number=None, brand=None, product_name=None, image_url=None,
                        page_domain=None, identity_tier=None, engine_decision=None, was_preselected=None,
                        vlm_decision=None, reason_code=None):
    """
    تسجيل قرار مراجع واحد (approved | rejected | manual_upload). was_preselected: هل الصورة هي التي اختارها
    المحرك مسبقاً (None عند عدم المعرفة). أخطاء قاعدة البيانات تُرفع؛ cli_bridge يلتقطها كي لا يتعطل الاعتماد.
    """
    if action not in REVIEW_ACTIONS:
        raise ValueError(f"action غير صالح: {action!r}")
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO review_decisions (action, sku_key, `row_number`, brand, product_name, image_url, page_domain,
                                          identity_tier, engine_decision, was_preselected, vlm_decision, reason_code)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            action, _clip(sku_key, 64), _as_int(row_number), _clip(brand, 255), _clip(product_name, 255),
            image_url or None, _clip(page_domain, 255), _clip(identity_tier, 8), _clip(engine_decision, 32),
            None if was_preselected is None else int(bool(was_preselected)), _clip(vlm_decision, 16),
            _clip(reason_code, 32),
        ))
        conn.commit()
    finally:
        _close(conn)
    return True


# ---------------------------------------------------------------------------
# ما يتعلّمه البحث من المراجعة (catalog_match/learning.py)
# ---------------------------------------------------------------------------

def _alias_key(brand):
    from catalog_match.text_norm import match_key
    return (match_key(brand) or str(brand or "").strip().lower())[:255]


def record_brand_alias(sheet_brand, alias, approved=True):
    """
    اعتماد (approved=True) أو رفض WRONG_BRAND لصورة كانت ماركتها مؤكدة فقط بكتابة المتاجر (تنبيه brand_spelling):
    يُحسب للكتابة أو عليها. تُستعمل الكتابة ما دامت الاعتمادات أكثر من الرفض. لا يُرفع خطأ: التعلّم لا يعطل المراجعة.
    """
    sheet_brand, alias = str(sheet_brand or "").strip(), str(alias or "").strip()
    if not sheet_brand or not alias or _alias_key(sheet_brand) == _alias_key(alias):
        return False
    column = "approvals" if approved else "rejections"
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            f"INSERT INTO learned_brand_aliases (brand_key, sheet_brand, alias, {column}) VALUES (%s, %s, %s, 1) "
            f"ON DUPLICATE KEY UPDATE {column} = {column} + 1, sheet_brand = VALUES(sheet_brand)",
            (_alias_key(sheet_brand), _clip(sheet_brand, 255), _clip(alias, 255)))
        conn.commit()
        return True
    finally:
        _close(conn)


def get_learned_brand_aliases():
    """[(sheet_brand, alias, approvals, rejections)] للكتابات التي اعتماداتها أكثر من رفضها، الأقوى أولاً."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT sheet_brand, alias, approvals, rejections FROM learned_brand_aliases "
                       "WHERE approvals > rejections ORDER BY approvals - rejections DESC, id")
        return [(r["sheet_brand"], r["alias"], int(r["approvals"]), int(r["rejections"])) for r in cursor.fetchall()]
    finally:
        _close(conn)


def get_brand_source_counts():
    """
    [(ماركة الشيت, الموقع, عدد المنتجات المعتمدة منه, عدد رفض الهوية)] لكل ماركة وموقع في review_decisions.
    المنتجات المعتمدة تُعد مرة واحدة لكل منتج (sku_key، وإلا اسم المنتج أو الصورة): إعادة اعتماد المنتج نفسه لا تُحسب
    مرتين. رفض الهوية: منتج أو ماركة أو نوع أو حجم أو عبوة مختلفة. التجميع حسب الماركة كما يفهمها البحث (فتهجئتان في
    الشيت لماركة واحدة تُحسبان معاً) يجري في catalog_match.learning.apply. أخطاء قاعدة البيانات تُرفع.
    """
    identity = ",".join(["%s"] * len(IDENTITY_REASON_CODES[:5]))
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(f"""
            SELECT MIN(TRIM(brand)) AS brand, LOWER(TRIM(page_domain)) AS domain,
                   COUNT(DISTINCT CASE WHEN action = 'approved'
                         THEN COALESCE(NULLIF(sku_key, ''), NULLIF(product_name, ''), image_url, CONCAT('#', id))
                         END) AS approvals,
                   SUM(action = 'rejected' AND reason_code IN ({identity})) AS identity_rejections
            FROM review_decisions
            WHERE brand IS NOT NULL AND TRIM(brand) <> '' AND page_domain IS NOT NULL AND TRIM(page_domain) <> ''
            GROUP BY LOWER(TRIM(brand)), LOWER(TRIM(page_domain))
        """, tuple(IDENTITY_REASON_CODES[:5]))
        return [(r["brand"], r["domain"], int(r["approvals"] or 0), int(r["identity_rejections"] or 0))
                for r in cursor.fetchall()]
    finally:
        _close(conn)


def get_review_decisions():
    """كل قرارات المراجعين بالترتيب الزمني (مدخل review_stats). أخطاء قاعدة البيانات تُرفع."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, created_at, action, sku_key, `row_number`, brand, product_name, image_url, page_domain, "
            "identity_tier, engine_decision, was_preselected, vlm_decision, reason_code "
            "FROM review_decisions ORDER BY created_at, id"
        )
        rows = cursor.fetchall()
    finally:
        _close(conn)
    return [dict(r) for r in rows]


def wilson_lower_bound(successes, n, z=WILSON_Z):
    """الحد الأدنى لفاصل ويلسون لنسبة ثنائية (نفس صيغة tests/eval/metrics.py). None عندما n = 0."""
    if n <= 0:
        return None
    p = successes / n
    z2 = z * z
    centre = p + z2 / (2 * n)
    margin = z * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n))
    return (centre - margin) / (1 + z2 / n)


def brand_status(prechecked, accepted):
    """ready | low_precision (الدقة نفسها أقل من العتبة) | needs_reviews."""
    lower = wilson_lower_bound(accepted, prechecked)
    if prechecked >= AUTO_PUBLISH_MIN_REVIEWED and lower >= AUTO_PUBLISH_MIN_LOWER_BOUND:
        return "ready"
    if prechecked >= AUTO_PUBLISH_MIN_REVIEWED and accepted / prechecked < AUTO_PUBLISH_MIN_LOWER_BOUND:
        return "low_precision"
    return "needs_reviews"


def reviews_needed(prechecked, accepted):
    """
    أقل عدد اختيارات مسبقة مراجعة يجهز عنده البراند إذا قُبلت كل الاختيارات القادمة
    (None إذا تجاوز _REVIEWS_NEEDED_CAP). الحد الأدنى يزداد مع كل قبول، فيكفي بحث ثنائي.
    """
    failures = prechecked - accepted

    def ready_at(n):
        return wilson_lower_bound(n - failures, n) >= AUTO_PUBLISH_MIN_LOWER_BOUND

    low, high = max(prechecked, AUTO_PUBLISH_MIN_REVIEWED), _REVIEWS_NEEDED_CAP
    if not ready_at(high):
        return None
    while low < high:
        mid = (low + high) // 2
        if ready_at(mid):
            high = mid
        else:
            low = mid + 1
    return low


def _brand_key(brand):
    try:
        from catalog_match.text_norm import normalize
        return normalize(brand)
    except Exception:
        return " ".join(str(brand or "").lower().split())


def _listable_brand(brand):
    """
    هل يُكتب البراند مدخلاً واحداً في AUTO_PUBLISH_BRANDS؟ الإعداد مفصول بفواصل، و'*' يفتح كل البراندات،
    و'category:' يفتح فئة كاملة (catalog_match.decide.auto_publish_allowed). براند بلا اسم أو بهذه الصيغ
    لا يجهز ولا يُقترح أبداً: لصقه يفتح النشر الآلي لبراندات لم تُراجع.
    """
    text = str(brand or "").strip()
    return bool(text) and "," not in text and text != "*" and not text.lower().startswith("category:")


def _precheck_verdict(rows):
    """
    حكم المراجع على اختيار المحرك المسبق لـ SKU واحد (صفوفه بالترتيب الزمني):
    False إذا رُفضت صورة مختارة مسبقاً، أو اعتُمدت صورة أخرى (أو رُفعت يدوياً) بينما عرض المحرك اختياراً مسبقاً؛
    True إذا اعتُمدت الصورة المختارة مسبقاً؛ None إذا لم يحكم المراجع على اختيار مسبق.
    رفض لاحق لرابط اعتُمد كاختيار مسبق يُعد رفضاً للاختيار المسبق حتى لو لم تُعرف حالته عند الرفض.
    """
    prechecked = {url_norm(r.get("image_url")) for r in rows if r.get("was_preselected") == 1 and r.get("image_url")}
    verdict = None
    for r in rows:
        flag = r.get("was_preselected")
        on_precheck = flag == 1 or (flag is None and bool(r.get("image_url"))
                                    and url_norm(r.get("image_url")) in prechecked)
        action = r.get("action")
        if action == "rejected" and on_precheck:
            return False
        if action == "approved" and on_precheck:
            verdict = True
        elif action in ("approved", "manual_upload") and flag == 0 and r.get("engine_decision") in PICK_DECISIONS:
            return False
    return verdict


def _ratio(num, den):
    return None if den == 0 else num / den


def _lower_bound(accepted, prechecked):
    """wilson_lower_bound بلا ضجيج الفاصلة العائمة تحت الصفر (0 من 5 تعطي -3e-17)."""
    lower = wilson_lower_bound(accepted, prechecked)
    return None if lower is None else max(0.0, lower)


def review_stats(rows):
    """
    إحصائيات قرارات المراجعين (دالة نقية على صفوف review_decisions):
    - brands: لكل براند المنتجات المراجعة (SKU)، والاختيارات المسبقة المراجعة (prechecked)، والمقبول منها،
      والدقة، والحد الأدنى لفاصل ويلسون 95%، والحالة (brand_status)، وعدد المراجعات اللازم، وأكثر أسباب الرفض.
    - domains: الاعتمادات والرفض لكل نطاق صفحة.
    - overall، والبراندات الجاهزة، وقيمة AUTO_PUBLISH_BRANDS المقترحة (لا تُكتب في أي إعداد).
    الحكم على الاختيار المسبق يُحسب مرة واحدة لكل SKU (_precheck_verdict)؛ البراند يُجمع بـ normalize كما يطابقه
    catalog_match.decide.auto_publish_allowed.
    """
    rows = sorted((dict(r) for r in rows or []), key=lambda r: (str(r.get("created_at") or ""), r.get("id") or 0))
    by_sku = {}
    for r in rows:
        sku = (r.get("sku_key") or "").strip() or f"row:{r.get('row_number')}"
        by_sku.setdefault(sku, []).append(r)

    brands = {}
    for sku_rows in by_sku.values():
        brand = next((r["brand"].strip() for r in reversed(sku_rows) if (r.get("brand") or "").strip()), "")
        entry = brands.setdefault(_brand_key(brand), {"brand": brand, "reviewed_skus": 0, "prechecked": 0,
                                                      "accepted": 0, "reasons": Counter()})
        entry["brand"] = entry["brand"] or brand
        entry["reviewed_skus"] += 1
        verdict = _precheck_verdict(sku_rows)
        if verdict is not None:
            entry["prechecked"] += 1
            entry["accepted"] += int(verdict)
        entry["reasons"].update(r["reason_code"] for r in sku_rows
                                if r.get("action") == "rejected" and r.get("reason_code"))

    brand_rows = []
    for key, entry in brands.items():
        prechecked, accepted = entry["prechecked"], entry["accepted"]
        listable = bool(key) and _listable_brand(entry["brand"])
        status = brand_status(prechecked, accepted) if listable else "needs_reviews"
        brand_rows.append({
            "brand": entry["brand"],
            "reviewed_skus": entry["reviewed_skus"],
            "prechecked": prechecked,
            "accepted": accepted,
            "precision": _ratio(accepted, prechecked),
            "lower_bound": _lower_bound(accepted, prechecked),
            "status": status,
            "reviews_needed": reviews_needed(prechecked, accepted) if listable else None,
            "top_reject_reasons": sorted(entry["reasons"].items(), key=lambda kv: (-kv[1], kv[0]))[:TOP_REJECT_REASONS],
        })
    brand_rows.sort(key=lambda b: (-b["prechecked"], -b["reviewed_skus"], _brand_key(b["brand"])))

    domains = {}
    for r in rows:
        domain = (r.get("page_domain") or "").strip().lower()
        if domain and r.get("action") in ("approved", "rejected"):
            domains.setdefault(domain, {"domain": domain, "approved": 0, "rejected": 0})[r["action"]] += 1
    domain_rows = sorted(domains.values(), key=lambda d: (-(d["approved"] + d["rejected"]), d["domain"]))

    actions = Counter(r.get("action") for r in rows)
    prechecked = sum(b["prechecked"] for b in brand_rows)
    accepted = sum(b["accepted"] for b in brand_rows)
    ready = sorted((b["brand"] for b in brand_rows if b["status"] == "ready"), key=_brand_key)
    return {
        "thresholds": {"min_reviewed": AUTO_PUBLISH_MIN_REVIEWED, "min_lower_bound": AUTO_PUBLISH_MIN_LOWER_BOUND,
                       "perfect_record_reviews": reviews_needed(0, 0)},
        "overall": {
            "actions": len(rows),
            "approved": actions["approved"],
            "rejected": actions["rejected"],
            "manual_upload": actions["manual_upload"],
            "reviewed_skus": len(by_sku),
            "prechecked": prechecked,
            "accepted": accepted,
            "precision": _ratio(accepted, prechecked),
            "lower_bound": _lower_bound(accepted, prechecked),
        },
        "brands": brand_rows,
        "domains": domain_rows,
        "ready_brands": ready,
        "suggested_auto_publish_brands": ", ".join(ready),
    }


# ---------------------------------------------------------------------------
# طابور المهام (automation_queue)
# ---------------------------------------------------------------------------

# --- جدولة الطابور (P4a) ---------------------------------------------------------------------------
# أولوية السحب: الصفوف الجديدة (وكتابة رابط معتمد) أولاً، ثم إعادة التحقق، ثم إعادة المحاولة التي حان موعدها
PRIORITY_NEW, PRIORITY_REVERIFY, PRIORITY_RETRY = 0, 1, 2
# «لا نتيجة» نظيفة: إعادة المحاولة بعد 3 ثم 7 ثم 30 يوماً، ثم يبقى الصف فاشلاً (أو قبل ذلك إذا تغير
# مدخل البراند في Brands Mapping أو ظهرت صفحات جديدة للبراند في الفهرس المحلي)
NOT_FOUND_CODES = ("NO_RESULTS", "ALL_CONFLICTED")
NOT_FOUND_RETRY_DAYS = (3, 7, 30)
# انقطاع المزودين لصف واحد: انتظار 10 دقائق يتضاعف، وبعد 3 محاولات في التشغيل نفسه يُركن الصف للتشغيل التالي
PROVIDER_DOWN_BACKOFF_MINUTES = 10
MAX_PROVIDER_DOWN_PER_RUN = 3
PROVIDER_DOWN_PARK_MINUTES = 12 * 60
# العامل ينتظر صفاً مؤجلاً يحين موعده خلال هذه المدة؛ ما بعدها يبقى في الانتظار للتشغيل التالي
OPEN_TASK_HORIZON_MINUTES = 45
# صف جاهز للمراجعة بسبب تعطل قارئ الملصق (VERIFIER_DOWN) يُعاد بحثه عند تشغيل يكون فيه القارئ متاحاً، مرتين كحد أقصى
MAX_REVERIFY = 2
TASK_RELINK = "relink"
# أسباب تفتح صفاً مكتملاً بلا رابط في الشيت للبحث من جديد (للمراجعة فقط)
REOPEN_REASONS = ("LINK_CLEARED", "LINK_MISSING")
QUEUE_BATCH = 500
# تعارض أقفال InnoDB (1213 deadlock، 1205 انتهاء انتظار القفل) بين السحب وكتابة النتيجة أو الإدراج:
# تُعاد المعاملة حتى LOCK_RETRIES مرات
LOCK_CONFLICT_CODES = (1205, 1213)
LOCK_RETRIES = 3

_QUEUE_COLUMNS = ("`row_number`", "barcode", "product_name", "brand", "search_query", "status", "attempts",
                  "sku_key", "alt_sku_key", "payload_json", "brand_fp", "priority", "task_kind", "review_only",
                  "requeue_reason", "fail_count", "reverify_count")
_IDENTITY_UPDATE = ("barcode = VALUES(barcode), product_name = VALUES(product_name), brand = VALUES(brand), "
                    "search_query = VALUES(search_query), sku_key = VALUES(sku_key), "
                    "alt_sku_key = VALUES(alt_sku_key), payload_json = VALUES(payload_json), "
                    "updated_at = CURRENT_TIMESTAMP")
# الصيغة (VALUES من %s فقط، ولا %s بعد ON DUPLICATE) تجعل pymysql يرسل الدفعة كلها في عبارة INSERT واحدة
_QUEUE_INSERT = (f"INSERT INTO automation_queue ({', '.join(_QUEUE_COLUMNS)}) "
                 f"VALUES ({', '.join(['%s'] * len(_QUEUE_COLUMNS))}) ON DUPLICATE KEY UPDATE ")
# صف موجود يبقى كما هو (مراجعة، مكتمل، حجز ساري، فاشل لم يحن موعده): أعمدة الهوية والحمولة فقط تتحدث
_QUEUE_KEEP_SQL = _QUEUE_INSERT + _IDENTITY_UPDATE
# صف يعود للانتظار: حالة جديدة بلا رمز فشل ولا trace ولا حجز، والمحاولة التالية الآن
_QUEUE_RESET_SQL = _QUEUE_INSERT + (
    "status = 'pending', error_message = NULL, attempts = 0, failure_code = NULL, trace_json = NULL, "
    "worker_id = NULL, lease_until = NULL, next_attempt_at = NULL, down_count = 0, "
    "fail_count = VALUES(fail_count), reverify_count = VALUES(reverify_count), priority = VALUES(priority), "
    "task_kind = VALUES(task_kind), review_only = VALUES(review_only), requeue_reason = VALUES(requeue_reason), "
    "brand_fp = VALUES(brand_fp), " + _IDENTITY_UPDATE)


def _same_text(a, b):
    return str(a or "").strip().casefold() == str(b or "").strip().casefold()


def plan_queue_row(old, new, reprocess=False):
    """
    ماذا يفعل الإدراج بصف واحد: ('insert' | 'keep' | 'reset', حقول الصف عند الإدراج أو إعادة الضبط).
    old: صف الطابور الحالي (أو None) مع live (حجز ساري)، has_next و due (موعد المحاولة التالية موجود / حان).
    new: الصف من الشيت (الهوية، task_kind، review_only، requeue_reason، brand_fp).
    - منتج آخر في الصف (sku_key مختلف) أو reprocess: صف جديد من الصفر.
    - حجز ساري لعامل: يبقى كما هو.
    - كتابة رابط معتمد (task_kind='relink'): تعود للانتظار مهما كانت الحالة.
    - جاهز للمراجعة: يبقى. مكتمل: يبقى، إلا إذا مُسح رابطه من الشيت (REOPEN_REASONS).
    - فاشل بـ «لا نتيجة»: يبقى حتى يحين موعده (3 / 7 / 30 يوماً) أو يتغير مدخل البراند أو الفهرس المحلي.
      فاشل لسبب آخر: يعود للانتظار (إعادة محاولة).
    - في الانتظار (أو حجز انتهى): يعود للانتظار بأولويته، والمحاولة التالية الآن.
    """
    fresh = {"priority": PRIORITY_NEW, "fail_count": 0, "reverify_count": 0, "task_kind": new.get("task_kind") or None,
             "review_only": 1 if new.get("review_only") else 0, "requeue_reason": new.get("requeue_reason") or None}
    if old is None:
        return "insert", fresh
    same = old.get("sku_key") == new.get("sku_key") or (
        old.get("sku_key") is None and _same_text(old.get("product_name"), new.get("product_name")))
    if reprocess or not same:
        return "reset", fresh
    if old.get("status") == "processing" and old.get("live"):
        return "keep", None
    carry = dict(fresh, fail_count=int(old.get("fail_count") or 0), reverify_count=int(old.get("reverify_count") or 0))
    reason = fresh["requeue_reason"]
    if fresh["task_kind"] == TASK_RELINK:
        return "reset", carry
    status = old.get("status")
    if status == "ready_for_review":
        return "keep", None
    if status == "completed":
        return ("reset", carry) if reason in REOPEN_REASONS else ("keep", None)
    if status == "failed":
        if old.get("failure_code") in NOT_FOUND_CODES and reason not in REOPEN_REASONS:
            brand_changed = bool(old.get("brand_fp") and new.get("brand_fp") and old["brand_fp"] != new["brand_fp"])
            exhausted = carry["fail_count"] > len(NOT_FOUND_RETRY_DAYS)
            due = not exhausted and (not old.get("has_next") or bool(old.get("due")))
            if brand_changed:
                reason = "BRAND_MAPPING_CHANGED"
            elif reason != "LOCAL_INDEX_CHANGED":
                if not due:
                    return "keep", None
                reason = "SCHEDULED_RETRY"
        return "reset", dict(carry, priority=PRIORITY_RETRY, requeue_reason=reason or "RETRY")
    # في الانتظار من تشغيل سابق (انقطاع مزودين، ميزانية، إيقاف) أو حجز انتهى: يعود الآن بأولويته وسببه
    return "reset", dict(carry, priority=int(old.get("priority") or 0),
                         requeue_reason=reason or old.get("requeue_reason") or None)


def _queue_row_params(new, fields):
    return (new["row_number"], new.get("barcode"), new.get("product_name"), new.get("brand"), new.get("search_query"),
            "pending", 0, new.get("sku_key"), new.get("alt_sku_key"), new.get("payload_json"), new.get("brand_fp"),
            fields["priority"], fields["task_kind"], fields["review_only"], fields["requeue_reason"],
            fields["fail_count"], fields["reverify_count"])


def _upsert_queue_chunk(cursor, chunk, reprocess):
    numbers = [r["row_number"] for r in chunk]
    cursor.execute(
        "SELECT id, `row_number`, sku_key, product_name, status, failure_code, fail_count, reverify_count, priority, "
        "task_kind, review_only, requeue_reason, brand_fp, "
        "(lease_until IS NOT NULL AND lease_until >= NOW()) AS live, (next_attempt_at IS NOT NULL) AS has_next, "
        "(next_attempt_at IS NOT NULL AND next_attempt_at <= NOW()) AS due "
        f"FROM automation_queue WHERE `row_number` IN ({','.join(['%s'] * len(numbers))}) FOR UPDATE",
        tuple(numbers),
    )
    existing = {r["row_number"]: r for r in cursor.fetchall() or []}
    keep_rows, reset_rows = [], []
    counts = {"insert": 0, "keep": 0, "reset": 0}
    for new in chunk:
        old = existing.get(new["row_number"])
        action, fields = plan_queue_row(old, new, reprocess)
        counts[action] += 1
        if action == "keep":
            fields = {"priority": old.get("priority") or 0, "task_kind": old.get("task_kind"),
                      "review_only": old.get("review_only") or 0, "requeue_reason": old.get("requeue_reason"),
                      "fail_count": old.get("fail_count") or 0, "reverify_count": old.get("reverify_count") or 0}
            keep_rows.append(_queue_row_params(new, fields))
        elif action == "insert":
            keep_rows.append(_queue_row_params(new, fields))        # صف جديد: ON DUPLICATE لا يُستخدم
        else:
            reset_rows.append(_queue_row_params(new, fields))
    if keep_rows:
        cursor.executemany(_QUEUE_KEEP_SQL, keep_rows)
    if reset_rows:
        cursor.executemany(_QUEUE_RESET_SQL, reset_rows)
    return counts


def queue_input(row_number, barcode, name, brand, query, payload=None, sku_key=None, alt_sku_key=None,
                 brand_fp=None, task_kind=None, review_only=False, requeue_reason=None):
    return {"row_number": row_number, "barcode": barcode, "product_name": name, "brand": brand,
            "search_query": query, "sku_key": sku_key, "alt_sku_key": alt_sku_key or sku_key,
            "payload_json": json.dumps(payload, ensure_ascii=False) if payload is not None else None,
            "brand_fp": brand_fp, "task_kind": task_kind, "review_only": 1 if review_only else 0,
            "requeue_reason": requeue_reason}


def add_many_to_queue(rows, reprocess=False, totals=None):
    """
    إدراج/تحديث صفوف كثيرة في الطابور (Upsert على row_number) بدفعات من QUEUE_BATCH صف: قراءة الصفوف الحالية
    (FOR UPDATE) وعبارتا INSERT جماعيتان ثم commit لكل دفعة، بدل اتصال و commit لكل صف (10 آلاف صف في ثوانٍ).
    rows: قواميس queue_input (row_number, barcode, product_name, brand, search_query, sku_key, alt_sku_key,
    payload_json, brand_fp, task_kind, review_only, requeue_reason). القرار لكل صف من plan_queue_row.
    تعيد {'insert', 'keep', 'reset'} وترفع أخطاء قاعدة البيانات. totals (dict اختياري): يُحدَّث بعد كل دفعة تُحفظ،
    فيعرف المستدعي كم صفاً حُفظ قبل خطأ في منتصف الطريق.
    """
    latest = {}
    for r in rows or []:
        latest[r["row_number"]] = r                      # رقم صف مكرر: الأخير يفوز
    items = list(latest.values())
    totals = totals if totals is not None else {}
    for k in ("insert", "keep", "reset"):
        totals.setdefault(k, 0)
    if not items:
        return totals
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        for start in range(0, len(items), QUEUE_BATCH):
            for n in range(1, LOCK_RETRIES + 1):
                try:
                    counts = _upsert_queue_chunk(cursor, items[start:start + QUEUE_BATCH], bool(reprocess))
                    conn.commit()
                    break
                except pymysql.err.OperationalError as e:
                    conn.rollback()
                    if not e.args or e.args[0] not in LOCK_CONFLICT_CODES or n == LOCK_RETRIES:
                        raise
                    logger.warning("[MariaDB Queue] تعارض أقفال أثناء الإدراج (%s)؛ إعادة الدفعة.", e.args[0])
                except Exception:
                    conn.rollback()
                    raise
            for k, v in counts.items():
                totals[k] += v
    finally:
        _close(conn)
    return totals


def add_to_queue(row_number, barcode, name, brand, query, payload=None, sku_key=None, reprocess=False,
                 alt_sku_key=None, brand_fp=None, task_kind=None, review_only=False, requeue_reason=None):
    """
    إضافة/تحديث صف واحد في الطابور (Upsert على row_number؛ نفس قرار add_many_to_queue).
    لا يعيد أبداً ضبط صف جاهز للمراجعة أو مكتمل (ولا صف قيد المعالجة بحجز ساري) إلا إذا reprocess=True
    أو تغيّر المنتج في هذا الصف (sku_key مختلف)، وصف «لا نتيجة» ينتظر موعده. تعيد True وترفع أخطاء قاعدة البيانات.
    """
    add_many_to_queue([queue_input(row_number, barcode, name, brand, query, payload, sku_key, alt_sku_key,
                                    brand_fp, task_kind, review_only, requeue_reason)], reprocess=reprocess)
    return True


CLAIMABLE_SQL = "(status='pending' OR (status='processing' AND (lease_until IS NULL OR lease_until<NOW())))"


def _claimable(alias=""):
    """
    صف قابل للسحب: في الانتظار، أو قيد المعالجة انتهى حجزه؛ إلا صفاً أعاده انقطاع المزودين بموعد لم يحن بعد.
    إعادة المحاولة من لوحة التحكم أو رفض المراجع تمسح رمز PROVIDER_DOWN فيُسحب الصف فوراً.
    """
    p = f"{alias}." if alias else ""
    return (f"(({p}status='pending' OR ({p}status='processing' AND ({p}lease_until IS NULL OR {p}lease_until<NOW()))) "
            f"AND NOT ({p}status='pending' AND {p}failure_code <=> 'PROVIDER_DOWN' AND {p}next_attempt_at > NOW()))")


# منتج واحد = بحث واحد: صف لمنتج له صف آخر قيد المعالجة بحجز ساري ينتظر نتيجته (تُطبق عليه عند انتهائه)
_SIBLING_BUSY_SQL = ("q.sku_key IS NOT NULL AND EXISTS (SELECT 1 FROM automation_queue s WHERE s.sku_key = q.sku_key "
                     "AND s.id <> q.id AND s.status = 'processing' AND s.lease_until >= NOW())")
_BUSY_SCAN_LIMIT = 500


def _not_really_busy_ids(cursor):
    """
    صفوف قابلة للسحب يمنعها _SIBLING_BUSY_SQL فقط بسبب صفوف قيد المعالجة بنفس المفتاح لمنتج آخر (نفس خلية الباركود،
    أو اسم عربي آخر: same_product): لا تنتظر نتيجة منتج آخر.
    """
    cursor.execute(
        "SELECT q.id, q.product_name, q.brand, q.payload_json, s.product_name AS s_name, s.brand AS s_brand, "
        "s.payload_json AS s_payload FROM automation_queue q JOIN automation_queue s ON s.sku_key = q.sku_key "
        "AND s.id <> q.id AND s.status = 'processing' AND s.lease_until >= NOW() "
        f"WHERE q.sku_key IS NOT NULL AND {_claimable('q')} LIMIT {_BUSY_SCAN_LIMIT}")
    waits = {}
    for r in cursor.fetchall() or []:
        other = {"product_name": r.get("s_name"), "brand": r.get("s_brand"), "payload_json": r.get("s_payload")}
        waits[r["id"]] = waits.get(r["id"], False) or same_product(r, other)
    return sorted(int(i) for i, same in waits.items() if not same)


def new_claim_id(worker_id=None):
    base = worker_id or f"{socket.gethostname()[:20]}:{os.getpid()}"
    return f"{base}#{uuid.uuid4().hex[:12]}"[:64]


def fetch_next_task(worker_id=None):
    """
    سحب ذري للمهمة التالية: UPDATE واحد يحجز الصف (pending، أو processing انتهى حجزه) بمعرف سحب فريد،
    ثم SELECT بذلك المعرف. شرط الحالة يتكرر في الـ WHERE الخارجي كي يفشل المتسابق الثاني بدل أن يسرق الصف.
    الترتيب: الأولوية (جديد، ثم إعادة تحقق، ثم إعادة محاولة) ثم id. صف أعاده انقطاع المزودين لا يُسحب قبل موعده،
    وصف لمنتج (sku_key) قيد المعالجة في صف آخر ينتظر (بحث واحد لكل منتج).
    أخطاء قاعدة البيانات تُرفع (لا تتحول إلى None).
    """
    claim_id = new_claim_id(worker_id)
    # حجز بلا lease_until (صفوف تركها الإصدار القديم في 'processing' عند إيقافه) يُعامل كمنتهٍ،
    # وإلا لن يُسحب أبداً ويبقى count_open_tasks أكبر من صفر فلا ينتهي العامل.
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        free = _not_really_busy_ids(cursor)
        free_sql = f" OR q.id IN ({','.join(['%s'] * len(free))})" if free else ""
        cursor.execute(
            "UPDATE automation_queue SET status='processing', worker_id=%s, "
            f"lease_until=NOW() + INTERVAL {LEASE_MINUTES} MINUTE, attempts=attempts+1, updated_at=CURRENT_TIMESTAMP "
            f"WHERE id=(SELECT id FROM (SELECT q.id FROM automation_queue q WHERE {_claimable('q')} "
            f"AND (NOT ({_SIBLING_BUSY_SQL}){free_sql}) ORDER BY q.priority, q.id LIMIT 1) t) "
            f"AND {_claimable()}",
            (claim_id,) + tuple(free),
        )
        conn.commit()
        cursor.execute("SELECT * FROM automation_queue WHERE worker_id = %s LIMIT 1", (claim_id,))
        row = cursor.fetchone()
    finally:
        _close(conn)
    return dict(row) if row else None


def is_claim_held(task_id, claim_id):
    """
    هل ما زال الصف محجوزاً بمعرف السحب هذا (status='processing' و worker_id=claim_id)؟
    بدون claim_id (استدعاء خارج العامل) تعيد True. خطأ القراءة يعيد True ويُسجل: تحديث الحالة
    اللاحق يتحقق من الملكية مرة أخرى.
    """
    if not claim_id:
        return True
    try:
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM automation_queue WHERE id = %s AND worker_id = %s AND status = 'processing'",
                           (task_id, claim_id))
            row = cursor.fetchone()
        finally:
            _close(conn)
        return row is not None
    except Exception as e:
        logger.warning("[MariaDB Queue] تعذر التحقق من حجز المهمة %s: %s", task_id, e)
        return True


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


def get_tasks_by_sku(sku_key):
    """
    صفوف الطابور لهذا الـ sku_key مرتبة برقم الصف: نفس المنتج قد يتكرر في أكثر من صف بالشيت، والاعتماد يُكتب
    في كل صفوفه. أخطاء قاعدة البيانات تُسجل وتعيد [] (يُكتب الصف المطلوب وحده كما كان).
    """
    sku = str(sku_key or "").strip()
    if not sku:
        return []
    try:
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM automation_queue WHERE sku_key = %s ORDER BY `row_number`", (sku,))
            rows = cursor.fetchall()
        finally:
            _close(conn)
        return [dict(r) for r in rows]
    except Exception as e:
        logger.warning("[MariaDB Queue] فشل قراءة صفوف الطابور للـ SKU %s: %s", sku, e)
        return []


def _trace_to_json(trace):
    if trace is None:
        return None
    try:
        return json.dumps(trace, ensure_ascii=False, default=str)
    except Exception:
        return None


def _retry_lock_conflicts(attempt, retries=LOCK_RETRIES):
    """تنفيذ معاملة (attempt تفتح اتصالها وتغلقه) مع إعادتها عند تعارض أقفال؛ أي خطأ آخر يُرفع كما هو."""
    import time
    for n in range(1, retries + 1):
        try:
            return attempt()
        except pymysql.err.OperationalError as e:
            if not e.args or e.args[0] not in LOCK_CONFLICT_CODES or n == retries:
                raise
            logger.warning("[MariaDB Queue] تعارض أقفال (%s)؛ إعادة المعاملة (%s/%s).", e.args[0], n, retries)
            time.sleep(0.1 * n)


def outcome_schedule(row, status, failure_code, group_fail_count=0):
    """
    موعد المحاولة التالية وعداداتها بعد نتيجة البحث: {next_minutes (None = لا موعد), fail_count, down_count, priority}.
    - PROVIDER_DOWN (يعود للانتظار): 10 دقائق ثم 20 ...؛ المحاولة الثالثة في التشغيل نفسه تركن الصف 12 ساعة
      (الإدراج التالي يعيده فوراً). down_count يُصفّر عند كل إدراج، فهو عدد محاولات هذا التشغيل.
    - «لا نتيجة» (NO_RESULTS / ALL_CONFLICTED): 3 ثم 7 ثم 30 يوماً، ثم بلا موعد (يبقى فاشلاً).
      group_fail_count: أعلى عداد بين صفوف المنتج نفسه، فلا يبدأ الجدول من جديد لصف مكرر.
    - نتيجة للمراجعة أو نشر: تُصفّر العدادات.
    """
    fail = int(row.get("fail_count") or 0)
    down = int(row.get("down_count") or 0)
    priority = int(row.get("priority") or 0)
    if status == "pending" and failure_code == "PROVIDER_DOWN":
        down += 1
        minutes = (PROVIDER_DOWN_PARK_MINUTES if down >= MAX_PROVIDER_DOWN_PER_RUN
                   else min(PROVIDER_DOWN_BACKOFF_MINUTES * 2 ** (down - 1), PROVIDER_DOWN_PARK_MINUTES))
        return {"next_minutes": minutes, "fail_count": fail, "down_count": down, "priority": PRIORITY_RETRY}
    if status == "failed" and failure_code in NOT_FOUND_CODES:
        fail = max(fail, int(group_fail_count or 0)) + 1
        days = NOT_FOUND_RETRY_DAYS[fail - 1] if fail <= len(NOT_FOUND_RETRY_DAYS) else None
        return {"next_minutes": days * 24 * 60 if days else None, "fail_count": fail, "down_count": 0,
                "priority": PRIORITY_RETRY}
    if status in ("ready_for_review", "completed"):
        return {"next_minutes": None, "fail_count": 0, "down_count": 0, "priority": priority}
    return {"next_minutes": None, "fail_count": fail, "down_count": 0, "priority": priority}


def update_task_status(task_id, status, error_message=None, failure_code=None, trace=None, claim_id=None,
                       siblings=None):
    """
    تحديث حالة المهمة بعد المعالجة، مع رمز الفشل والـ trace وموعد المحاولة التالية (outcome_schedule)، وتحرير الحجز.
    claim_id (معرف السحب من fetch_next_task): عند تمريره لا يُحدَّث الصف إلا إذا كان ما زال محجوزاً بهذا
    المعرف وفي حالة 'processing'؛ فلا تكتب نتيجة العامل فوق اعتماد بشري تم أثناء المعالجة أو فوق حجز
    أعيد سحبه بعد انتهائه. تعيد False إن لم يعد الحجز ملكاً للعامل.
    منتج واحد = بحث واحد: صفوف المنتج نفسه (sku_key) التي تنتظر (pending / failed، مهمة بحث) تأخذ النتيجة نفسها
    في المعاملة نفسها. siblings=None: كلها، إلا عند 'completed' (النشر يكتب رابط كل صف أولاً ويمرر معرفاته)؛
    siblings=[ids]: هذه الصفوف فقط؛ siblings=(): لا شيء. صف مكتمل أو جاهز للمراجعة أو قيد المعالجة لا يُلمس،
    ونتيجة عابرة (انقطاع المزودين، خطأ بحث) لا تغيّر صفاً فاشلاً ينتظر موعده.
    """
    trace_json = _trace_to_json(trace)

    def attempt():
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            sql = ("SELECT id, sku_key, task_kind, fail_count, down_count, priority, product_name, brand, payload_json "
                   "FROM automation_queue WHERE id = %s")
            params = (task_id,)
            if claim_id:
                sql += " AND worker_id = %s AND status = 'processing'"
                params += (claim_id,)
            cursor.execute(sql + " FOR UPDATE", params)
            row = cursor.fetchone()
            if row is None:
                conn.rollback()
                if claim_id:
                    logger.warning("[MariaDB Queue] المهمة %s لم تعد محجوزة بـ %s؛ لم تُكتب الحالة %s",
                                   task_id, claim_id, status)
                    return False
                return True
            sibling_rows = []
            sku = row.get("sku_key")
            if sku and not row.get("task_kind") and siblings != ():
                # نتيجة عابرة (انقطاع، خطأ بحث) لا تغيّر صفاً فاشلاً ينتظر موعده: لم نعرف شيئاً جديداً عن المنتج
                learned = status in ("ready_for_review", "completed") or (
                    status == "failed" and failure_code in NOT_FOUND_CODES)
                waiting = "('pending','failed')" if learned else "('pending')"
                base = ("SELECT id, fail_count, product_name, brand, payload_json FROM automation_queue "
                        f"WHERE sku_key = %s AND id <> %s AND status IN {waiting} AND task_kind IS NULL")
                if siblings is None and status != "completed":
                    cursor.execute(base + " FOR UPDATE", (sku, task_id))
                    sibling_rows = list(cursor.fetchall() or [])
                elif siblings:
                    ids = [int(i) for i in siblings]
                    cursor.execute(base + f" AND id IN ({','.join(['%s'] * len(ids))}) FOR UPDATE",
                                   (sku, task_id) + tuple(ids))
                    sibling_rows = list(cursor.fetchall() or [])
                # منتج آخر يشارك المفتاح (نفس خلية الباركود، أو اسم عربي آخر) ليس صفاً شقيقاً: يُبحث عنه باسمه هو
                sibling_rows = [r for r in sibling_rows if same_product(row, r)]
            group_fail = max([int(r.get("fail_count") or 0) for r in sibling_rows] or [0])
            plan = outcome_schedule(row, status, failure_code, group_fail)
            if plan["next_minutes"] is None:
                next_sql, next_params = "NULL", ()
            else:
                next_sql, next_params = "NOW() + INTERVAL %s MINUTE", (int(plan["next_minutes"]),)
            counters = (plan["fail_count"], plan["down_count"], plan["priority"])
            cursor.execute(
                "UPDATE automation_queue SET status = %s, error_message = %s, failure_code = %s, "
                "trace_json = COALESCE(%s, trace_json), lease_until = NULL, "
                f"next_attempt_at = {next_sql}, fail_count = %s, down_count = %s, priority = %s, "
                "searched_at = NOW(), updated_at = CURRENT_TIMESTAMP WHERE id = %s",
                (status, error_message, failure_code, trace_json) + next_params + counters + (task_id,),
            )
            if sibling_rows:
                ids = tuple(int(r["id"]) for r in sibling_rows)
                cursor.execute(
                    "UPDATE automation_queue SET status = %s, error_message = %s, failure_code = %s, lease_until = NULL, "
                    f"next_attempt_at = {next_sql}, fail_count = %s, down_count = %s, priority = %s, "
                    "searched_at = NOW(), updated_at = CURRENT_TIMESTAMP "
                    f"WHERE id IN ({','.join(['%s'] * len(ids))})",
                    (status, error_message, failure_code) + next_params + counters + ids,
                )
            conn.commit()
        finally:
            _close(conn)
        return True

    try:
        return _retry_lock_conflicts(attempt)
    except Exception as e:
        logger.warning("[MariaDB Queue] فشل تحديث حالة المهمة %s: %s", task_id, e)
        return False


def get_sku_siblings(task_id, sku_key):
    """
    صفوف المنتج نفسه (sku_key) التي تنتظر نتيجة بحث هذه المهمة (pending / failed، مهمة بحث)، مع هويتها في الشيت.
    صف بنفس المفتاح لمنتج آخر (نفس خلية الباركود، أو اسم عربي آخر: same_product) ليس منها.
    خطأ القراءة يُسجل ويعيد [] (لا تُكتب صفوف إضافية).
    """
    if not sku_key:
        return []
    try:
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT product_name, brand, payload_json FROM automation_queue WHERE id = %s", (task_id,))
            own = cursor.fetchone()
            if own is None:
                return []
            cursor.execute(
                "SELECT id, `row_number`, barcode, product_name, brand, payload_json, review_only "
                "FROM automation_queue WHERE sku_key = %s AND id <> %s AND status IN ('pending','failed') "
                "AND task_kind IS NULL ORDER BY id", (str(sku_key).strip(), task_id))
            return [dict(r) for r in cursor.fetchall() or [] if same_product(own, r)]
        finally:
            _close(conn)
    except Exception as e:
        logger.warning("[MariaDB Queue] تعذر قراءة صفوف المنتج %s: %s", sku_key, e)
        return []


def requeue_verifier_down(run_id=None, max_reverify=MAX_REVERIFY):
    """
    صفوف جاهزة للمراجعة لأن قارئ الملصق تعطل (VERIFIER_DOWN) ولم يقرر فيها مراجع بعد تعود للانتظار بأولوية
    إعادة التحقق (بحث جديد بقارئ يعمل)، حتى max_reverify مرة لكل صف ثم تنتظر المراجع. مرشحاتها تبقى حتى يحل
    محلها البحث الجديد. يستدعيه العامل عندما يكون القارئ متاحاً. تعيد عدد الصفوف، أو None عند خطأ (يُسجل).
    """
    try:
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE automation_queue SET status = 'pending', priority = %s, requeue_reason = 'VERIFIER_RECHECK', "
                "reverify_count = reverify_count + 1, run_id = COALESCE(%s, run_id), worker_id = NULL, "
                "lease_until = NULL, next_attempt_at = NULL, updated_at = CURRENT_TIMESTAMP "
                "WHERE status = 'ready_for_review' AND failure_code = 'VERIFIER_DOWN' AND reverify_count < %s "
                "AND task_kind IS NULL",
                (PRIORITY_REVERIFY, run_id, int(max_reverify)),
            )
            count = cursor.rowcount
            conn.commit()
            return count
        finally:
            _close(conn)
    except Exception as e:
        logger.warning("[MariaDB Queue] تعذر إعادة صفوف VERIFIER_DOWN للتحقق: %s", e)
        return None


def park_verifier_rechecks():
    """
    قارئ الملصق ما زال معطلاً في هذا التشغيل: صفوف إعادة التحقق التي لم تُسحب بعد تعود جاهزة للمراجعة
    (مرشحاتها ما زالت محفوظة)، فلا يُدفع بحث جديد سينتهي VERIFIER_DOWN مرة أخرى. تعيد العدد أو None عند خطأ.
    """
    try:
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE automation_queue SET status = 'ready_for_review', failure_code = 'VERIFIER_DOWN', "
                "reverify_count = GREATEST(reverify_count - 1, 0), updated_at = CURRENT_TIMESTAMP "
                "WHERE status = 'pending' AND requeue_reason = 'VERIFIER_RECHECK'")
            count = cursor.rowcount
            conn.commit()
            return count
        finally:
            _close(conn)
    except Exception as e:
        logger.warning("[MariaDB Queue] تعذر إرجاع صفوف إعادة التحقق للمراجعة: %s", e)
        return None


# حالة «بانتظار المراجعة» تنتهي عندما لا يبقى أي صف جاهز للمراجعة (اعتمد المراجع أو رفض آخر صف)
_SETTLE_REVIEW_SQL = (
    "UPDATE automation_state SET status = 'idle', current_product_name = '' "
    "WHERE `key` = 'active_session' AND status = 'curation_pending' "
    "AND NOT EXISTS (SELECT 1 FROM automation_queue WHERE status = 'ready_for_review')"
)


def update_task_status_by_row(row_number, status, error_message=None, failure_code=None, sku_key=None, rows=None):
    """
    تحديث حالة المهمة لمنتج: بـ sku_key عند تمريره (فلا يتأثر منتج آخر انتقل إلى رقم الصف نفسه
    بعد تعديل الشيت)، وبرقم الصف فقط للصفوف القديمة بلا sku_key.
    rows: أرقام صفوف هذا المنتج بين صفوف المفتاح (same_product)، فلا يتأثر منتج آخر يشاركه الباركود.
    قرارات المراجع (اعتماد / رفض / رفع يدوي) تمر من هنا: إذا لم يبق صف جاهز للمراجعة تصبح الحالة خاملة.
    """
    clause, params = _row_or_sku_clause(row_number, sku_key, rows)
    try:
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(f"""
                UPDATE automation_queue
                SET status = %s, error_message = %s, failure_code = %s, lease_until = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE {clause}
            """, (status, error_message, failure_code) + params)
            cursor.execute(_SETTLE_REVIEW_SQL)
            conn.commit()
        finally:
            _close(conn)
        return True
    except Exception as e:
        logger.warning("[MariaDB Queue] فشل تحديث حالة المهمة للصف %s: %s", row_number, e)
        return False


def release_worker_claims(row_number, sku_key=None, rows=None):
    """
    يسحب حجز العامل عن صفوف هذا المنتج قيد المعالجة (worker_id = NULL) دون تغيير حالتها: قرار مراجع على وشك
    الكتابة في الشيت، فالعامل الذي يعالج نفس المنتج يفقد ملكية الحجز (is_claim_held) ولا ينشر فوقه ولا يكتب
    حالته. الصف يبقى 'processing' حتى يكتب المراجع حالته، أو حتى ينتهي الحجز فيُسحب من جديد إن فشل الاعتماد.
    تعيد عدد الصفوف، أو None عند خطأ قاعدة البيانات. rows: صفوف هذا المنتج فقط (انظر update_task_status_by_row).
    """
    clause, params = _row_or_sku_clause(row_number, sku_key, rows)
    try:
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(f"UPDATE automation_queue SET worker_id = NULL WHERE {clause} AND status = 'processing' "
                           "AND worker_id IS NOT NULL", params)
            affected = cursor.rowcount
            conn.commit()
        finally:
            _close(conn)
        return affected
    except Exception as e:
        logger.warning("[MariaDB Queue] تعذر سحب حجز العامل عن الصف %s: %s", row_number, e)
        return None


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
    """
    عدد المهام المفتوحة (pending أو processing) التي يستطيع هذا التشغيل معالجتها، بـ COUNT(*) موثوق. صف أعاده
    انقطاع المزودين بموعد أبعد من OPEN_TASK_HORIZON_MINUTES لا يُحسب: يبقى في الانتظار للتشغيل التالي ولا يُبقي
    العامل حياً بلا عمل. أخطاء قاعدة البيانات تُرفع.
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) AS cnt FROM automation_queue WHERE status IN ('pending','processing') "
            "AND NOT (status = 'pending' AND failure_code <=> 'PROVIDER_DOWN' "
            f"AND next_attempt_at > NOW() + INTERVAL {OPEN_TASK_HORIZON_MINUTES} MINUTE)")
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
    """عدد المنتجات الجاهزة للمراجعة، أو None عند خطأ قاعدة البيانات (ليس 0: لا يُعد التشغيل بلا مراجعة)."""
    try:
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) AS count FROM automation_queue WHERE status = 'ready_for_review'")
            row = cursor.fetchone()
        finally:
            _close(conn)
        return int(row['count']) if row else 0
    except Exception as e:
        logger.warning("[MariaDB Queue] فشل حساب المهام الجاهزة للمراجعة: %s", e)
    return None


# ---------------------------------------------------------------------------
# سجل الصرف اليومي للبحث (search_spend) — P4a. صف لكل (يوم، تشغيل، مزود): الاستدعاءات المدفوعة التي أجاب عنها
# المزود وتكلفتها التقديرية بأسعار ops_health، وقراءات الملصق المدفوعة (Gemini / Claude). اليوم هو تاريخ خادم
# MariaDB (CURDATE)، أي اليوم المحلي على جهاز المالك. يقرؤه العامل قبل كل سحب عند ضبط DAILY_BUDGET_USD.
# ---------------------------------------------------------------------------

SPEND_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS search_spend (
        day DATE NOT NULL,
        run_id VARCHAR(64) NOT NULL DEFAULT '',
        provider VARCHAR(32) NOT NULL,
        calls INT NOT NULL DEFAULT 0,
        usd DECIMAL(14,6) NOT NULL DEFAULT 0,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (day, run_id, provider)
    ) ENGINE=InnoDB CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
"""


def spend_from_outcome(outcome):
    """
    تكلفة بحث واحد من trace['outcome']: [(provider, calls, usd)]. كل استعلام لمزود مدفوع أجاب عنه (ok / empty)
    بسعره في ops_health.source_prices؛ الاستعلامات المرفوضة (رصيد، مفتاح، خطأ) لا تُحتسب، مثل لوحة التشخيصات.
    قراءات الملصق: usd المسجل لكل استدعاء (outcome.vlm_usage)، وإلا vlm_calls بسعر Gemini الثابت.
    """
    import ops_health

    outcome = outcome if isinstance(outcome, dict) else {}
    prices = ops_health.source_prices()
    totals = {}

    def add(provider, calls, usd):
        entry = totals.setdefault(provider, [0, 0.0])
        entry[0] += calls
        entry[1] += usd

    for item in outcome.get("provider_health") or []:
        if not isinstance(item, dict):
            continue
        provider = str(item.get("provider") or "").strip().lower()
        status = str(item.get("status") or "").strip().lower()
        if provider in ops_health.PAID_PROVIDERS and status in ops_health.ANSWERED_STATUSES:
            add(provider, 1, float(prices.get(provider) or 0.0))
    usage = [u for u in (outcome.get("vlm_usage") or []) if isinstance(u, dict)]
    for u in usage:
        provider = "claude" if str(u.get("provider") or "").strip().lower() == "claude" else "gemini"
        try:
            usd = max(0.0, float(u.get("usd") or 0.0))
        except (TypeError, ValueError):
            usd = 0.0
        add(provider, 1, usd)
    if not usage:
        try:
            calls = max(0, int(outcome.get("vlm_calls") or 0))
        except (TypeError, ValueError):
            calls = 0
        if calls:
            add("gemini", calls, calls * ops_health.GEMINI_COST_PER_CALL)
    return [(p, c, round(u, 6)) for p, (c, u) in sorted(totals.items()) if c > 0]


def record_search_spend(outcome, run_id=None):
    """
    يضيف تكلفة بحث واحد إلى سجل اليوم. لا يرفع أبداً: صف ضائع يُسجل في السجل والبحث يكمل.
    تعيد عدد المزودين المسجلين، أو None عند الفشل.
    """
    try:
        items = spend_from_outcome(outcome)
    except Exception as e:
        logger.warning("[Spend] تعذر حساب تكلفة البحث: %s", e)
        return None
    if not items:
        return 0
    try:
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            for provider, calls, usd in items:
                cursor.execute(
                    "INSERT INTO search_spend (day, run_id, provider, calls, usd) VALUES (CURDATE(), %s, %s, %s, %s) "
                    "ON DUPLICATE KEY UPDATE calls = calls + VALUES(calls), usd = usd + VALUES(usd)",
                    (str(run_id or "")[:64], provider[:32], int(calls), float(usd)),
                )
            conn.commit()
            return len(items)
        finally:
            _close(conn)
    except Exception as e:
        logger.warning("[Spend] لم يُسجل صرف البحث: %s", e)
        return None


def spend_today():
    """التكلفة التقديرية لكل عمليات البحث اليوم (دولار). أخطاء قاعدة البيانات تُرفع: صرف مجهول لا يصبح ميزانية مفتوحة."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT COALESCE(SUM(usd), 0) AS usd FROM search_spend WHERE day = CURDATE()")
        row = cursor.fetchone() or {}
    finally:
        _close(conn)
    return float(row.get("usd") or 0.0)


def run_spend(run_id):
    """تكلفة تشغيل واحد من سجل الصرف (دولار)، أو None إن لم يُسجل له شيء. أخطاء قاعدة البيانات تُرفع."""
    if not run_id:
        return None
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) AS n, COALESCE(SUM(usd), 0) AS usd FROM search_spend WHERE run_id = %s",
                       (str(run_id)[:64],))
        row = cursor.fetchone() or {}
    finally:
        _close(conn)
    return float(row.get("usd") or 0.0) if int(row.get("n") or 0) else None


def spend_by_day(days=7):
    """[{day, provider, calls, usd}] لآخر days يوماً (قراءة فقط؛ أخطاء قاعدة البيانات تُرفع)."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT day, provider, SUM(calls) AS calls, SUM(usd) AS usd FROM search_spend "
            "WHERE day >= CURDATE() - INTERVAL %s DAY GROUP BY day, provider ORDER BY day DESC, provider",
            (max(0, int(days) - 1),))
        rows = cursor.fetchall() or []
    finally:
        _close(conn)
    return [{"day": str(r["day"]), "provider": r["provider"], "calls": int(r["calls"] or 0),
             "usd": round(float(r["usd"] or 0.0), 6)} for r in rows]


# ---------------------------------------------------------------------------
# قراءات المطابقة عند الإدراج (P4a): ما يعرفه الطابور والكاش والفهرس المحلي وطابور الكتابة عن كل صف.
# كلها قراءة فقط.
# ---------------------------------------------------------------------------

def queue_snapshot():
    """{row_number: {sku_key, status, failure_code, fail_count, searched_at}} لكل صفوف الطابور. الأخطاء تُرفع."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT `row_number`, sku_key, status, failure_code, fail_count, searched_at "
                       "FROM automation_queue")
        rows = cursor.fetchall() or []
    finally:
        _close(conn)
    return {r["row_number"]: dict(r) for r in rows}


def resolution_snapshot():
    """
    الحلول المحفوظة: {'by_key': {sku_key: أحدث حل قابل للخدمة}, 'by_url': {url_norm(cloudinary_url): [حلول]}}.
    by_url يشمل كل الحالات (حتى superseded): رابط في الشيت يدل على المنتج الذي نُشر له. الأخطاء تُرفع.
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, sku_key, barcode, product_name, brand, cloudinary_url, verification_status, metadata_json "
            "FROM resolved_products WHERE sku_key IS NOT NULL AND sku_key <> '' ORDER BY resolved_at DESC, id DESC")
        rows = cursor.fetchall() or []
    finally:
        _close(conn)
    by_key, by_url = {}, {}
    for r in rows:
        r = dict(r)
        if r.get("verification_status") in SERVABLE_STATUSES:
            by_key.setdefault(r["sku_key"], r)
        if r.get("cloudinary_url"):
            by_url.setdefault(url_norm(r["cloudinary_url"]), []).append(r)
    return {"by_key": by_key, "by_url": by_url}


def catalog_brand_news(tokens):
    """
    {كلمة براند: أحدث first_seen} لصفوف الفهرس المحلي التي تحمل كل كلمة: متجر بدأ يعرض منتجات جديدة لهذا البراند.
    {} عند الخطأ (يُسجل): لا إعادة بحث مبكرة، والجدول الزمني يبقى.
    """
    tokens = sorted({str(t) for t in tokens or [] if t})
    out = {}
    if not tokens:
        return out
    try:
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            for start in range(0, len(tokens), 500):
                part = tokens[start:start + 500]
                cursor.execute(
                    "SELECT t.token, MAX(p.first_seen) AS newest FROM catalog_tokens t "
                    "JOIN catalog_products p ON p.id = t.product_id "
                    f"WHERE t.token IN ({','.join(['%s'] * len(part))}) GROUP BY t.token", tuple(part))
                for r in cursor.fetchall() or []:
                    if r.get("newest") is not None:
                        out[r["token"]] = r["newest"]
        finally:
            _close(conn)
    except Exception as e:
        logger.warning("[Catalog Index] تعذر فحص الصفوف الجديدة للبراندات: %s", e)
        return {}
    return out


def outbox_max_id():
    """
    أكبر معرّف في طابور كتابة الشيت (sheet_updates) الآن: ما يُجدول بعده هو كتابات هذا الطلب (cli_bridge._sheet_outcome
    يقرأ ما بعده فقط). None عند الخطأ أو غياب الجدول.
    """
    try:
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT COALESCE(MAX(id), 0) AS top FROM sheet_updates")
            return int((cursor.fetchone() or {}).get("top") or 0)
        finally:
            _close(conn)
    except Exception as e:
        logger.warning("[Sheets Outbox] تعذر قراءة آخر معرّف في طابور الكتابة: %s", e)
        return None


def outbox_link_writes(row_numbers):
    """
    كتابات طابور الشيت (sheet_updates) لهذه الصفوف بالترتيب: [{id, row_number, value, sync_status}].
    قراءة فقط، احتياط عندما لا يوفر google_sheets الدالة outbox_outcomes. [] عند الخطأ أو غياب الجدول.
    """
    numbers = sorted({int(n) for n in row_numbers or []})
    out = []
    if not numbers:
        return out
    try:
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            for start in range(0, len(numbers), 1000):
                part = numbers[start:start + 1000]
                cursor.execute(
                    "SELECT id, `row_number`, `value`, sync_status FROM sheet_updates "
                    f"WHERE `row_number` IN ({','.join(['%s'] * len(part))}) ORDER BY id", tuple(part))
                out.extend(dict(r) for r in cursor.fetchall() or [])
        finally:
            _close(conn)
    except Exception as e:
        logger.warning("[Sheets Outbox] تعذر قراءة حالة كتابات الشيت: %s", e)
        return []
    return out


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


def save_curation_candidates(row_number, product_name, brand, candidates, best_url=None, sku_key=None, run_id=None,
                             identity=None):
    """
    استبدال مرشحات المنتج بمرشحات التشغيل الحالي في معاملة واحدة.
    تُحفظ الحالة والأسباب والأدلة وقراءة VLM لكل مرشح. is_selected=1 فقط للحالة 'preselected'
    (best_url لم يعد يحدد الاختيار المسبق). تعيد True عند النجاح و False عند أي خطأ (مع التراجع).
    الحذف بنفس قاعدة القراءة (_row_or_sku_clause): مرشحات هذا الـ sku_key، ورقم الصف فقط للصفوف القديمة بلا
    sku_key؛ فمنتج انتقل إلى رقم صف منتج آخر بعد تعديل الشيت لا يمسح مرشحات ذلك المنتج. مرشحات منتج آخر يشارك
    هذا المنتج الـ sku_key (نفس خلية الباركود) لا تُمسح (_foreign_candidate_ids). identity: هوية المنتج
    (queue_row_identity)؛ الافتراضي الاسم والبراند.
    """
    run_id = run_id or uuid.uuid4().hex[:16]
    if identity is None:
        identity = {"name": product_name, "brand": brand}
    try:
        conn = get_db_connection()
    except Exception as e:
        logger.warning("[Curation] تعذر الاتصال لحفظ مرشحات الصف %s: %s", row_number, e)
        return False
    try:
        cursor = conn.cursor()
        clause, params = _without_ids(*_row_or_sku_clause(row_number, sku_key),
                                      _foreign_candidate_ids(cursor, sku_key, identity))
        cursor.execute(f"DELETE FROM curation_candidates WHERE {clause}", params)
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


def get_curation_candidates(row_number, sku_key=None, identity=None):
    """
    جلب المرشحات المحفوظة لمنتج (المختار مسبقاً أولاً): بـ sku_key عند تمريره، وإلا برقم الصف.
    identity (هوية المنتج): تُستبعد مرشحات منتج آخر يشاركه الـ sku_key.
    """
    clause, params = _row_or_sku_clause(row_number, sku_key)
    try:
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            clause, params = _without_ids(clause, params, _foreign_candidate_ids(cursor, sku_key, identity))
            cursor.execute(
                f"SELECT * FROM curation_candidates WHERE {clause} ORDER BY is_selected DESC, id ASC",
                params,
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


def _row_or_sku_clause(row_number, sku_key, rows=None):
    """
    شرط يحدد صفوف منتج واحد: بـ sku_key عند توفره (أرقام الصفوف تتغير عند تعديل الشيت)،
    وبرقم الصف فقط للصفوف القديمة بلا sku_key.
    rows: أرقام صفوف المنتج بين صفوف هذا الـ sku_key (same_product)؛ منتج آخر يشاركه المفتاح (نفس خلية الباركود)
    لا يُلمس. None = كل صفوف المفتاح.
    """
    if sku_key and rows is not None:
        numbers = sorted({int(r) for r in rows})
        if not numbers:
            return "(sku_key IS NULL AND `row_number` = %s)", (row_number,)
        marks = ",".join(["%s"] * len(numbers))
        return (f"((sku_key = %s AND `row_number` IN ({marks})) OR (sku_key IS NULL AND `row_number` = %s))",
                (str(sku_key).strip(),) + tuple(numbers) + (row_number,))
    if sku_key:
        return "(sku_key = %s OR (sku_key IS NULL AND `row_number` = %s))", (str(sku_key).strip(), row_number)
    return "`row_number` = %s", (row_number,)


def _foreign_candidate_ids(cursor, sku_key, identity):
    """
    معرفات مرشحات محفوظة بهذا الـ sku_key لمنتج آخر يشاركه المفتاح: اسمها وبراندها المحفوظان لمنتج آخر، أو صف الطابور
    عند رقم صفها (بنفس المفتاح) هويته لمنتج آخر (الاسم العربي). identity: هوية المنتج المطلوب (queue_row_identity).
    """
    if not sku_key or identity is None:
        return []
    identity = queue_row_identity(identity)
    sku = str(sku_key).strip()
    cursor.execute("SELECT id, `row_number`, product_name, brand FROM curation_candidates WHERE sku_key = %s", (sku,))
    found = list(cursor.fetchall() or [])
    if not found:
        return []
    cursor.execute("SELECT `row_number`, product_name, brand, payload_json FROM automation_queue WHERE sku_key = %s",
                   (sku,))
    queue = {r["row_number"]: r for r in cursor.fetchall() or []}
    verdicts, foreign = {}, []
    for c in found:
        key = (c.get("row_number"), c.get("product_name"), c.get("brand"))
        if key not in verdicts:
            # المرشح لا يحفظ الحجم: حجم المنتج المطلوب (المفتاح نفسه يتضمنه لصف بلا باركود)
            stored = {"name": c.get("product_name"), "brand": c.get("brand"), "size": identity["size"]}
            task = queue.get(c.get("row_number"))
            verdicts[key] = same_product(identity, stored) and (task is None or same_product(identity, task))
        if not verdicts[key]:
            foreign.append(int(c["id"]))
    return foreign


def _without_ids(clause, params, ids):
    if not ids:
        return clause, params
    return f"{clause} AND id NOT IN ({','.join(['%s'] * len(ids))})", tuple(params) + tuple(ids)


def exclude_curation_candidate(row_number, image_url, sku_key=None, identity=None):
    """
    رفض المراجع لصورة واحدة: يُعلَّم مرشحها وحده 'excluded' (ولا يبقى مختاراً)، وباقي مرشحات المنتج تبقى
    للمراجعة. تعيد عدد الصفوف، أو None عند خطأ قاعدة البيانات. identity: لا تُمس مرشحات منتج آخر يشاركه المفتاح.
    """
    clause, params = _row_or_sku_clause(row_number, sku_key)
    try:
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            clause, params = _without_ids(clause, params, _foreign_candidate_ids(cursor, sku_key, identity))
            cursor.execute(f"UPDATE curation_candidates SET status = 'excluded', is_selected = 0 "
                           f"WHERE {clause} AND image_url = %s", params + (image_url,))
            affected = cursor.rowcount
            conn.commit()
        finally:
            _close(conn)
        return affected
    except Exception as e:
        logger.warning("[Curation] فشل استبعاد المرشح المرفوض للصف %s: %s", row_number, e)
        return None


def delete_curation_candidates(row_number, sku_key=None, identity=None):
    """
    مسح كل مرشحات منتج بعد اعتماده أو رفضه (بـ sku_key عند توفره، وإلا برقم الصف). identity (هوية المنتج):
    مرشحات منتج آخر يشاركه الـ sku_key تبقى.
    """
    clause, params = _row_or_sku_clause(row_number, sku_key)
    try:
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            clause, params = _without_ids(clause, params, _foreign_candidate_ids(cursor, sku_key, identity))
            cursor.execute(f"DELETE FROM curation_candidates WHERE {clause}", params)
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
                            current_product=None, notice=None, stop_requested=None):
    """تحديث حالة ومؤشرات جلسة الأتمتة الجارية. stop_requested=0 عند نهاية تشغيل: طلب إيقاف لم يعد له تشغيل."""
    try:
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            updates = ["status = %s", "updated_at = CURRENT_TIMESTAMP"]
            params = [status]
            for column, value in (("total_items", total), ("processed_items", processed),
                                  ("success_count", success), ("failed_count", failed),
                                  ("current_product_name", current_product), ("notice", notice),
                                  ("stop_requested", stop_requested)):
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
        error = None
    except Exception as e:
        logger.warning("[MariaDB State] فشل استرداد حالة الأتمتة: %s", e)
        error = f"{type(e).__name__}: {e}"[:255]
    # قاعدة بيانات لا ترد ليست «خاملاً»: الحالة 'db_unavailable' (لا تُكتب في الجدول أبداً) فلا يُعد التشغيل منتهياً
    return {"status": "db_unavailable" if error else "idle", "db_error": error, "total_items": 0,
            "processed_items": 0, "success_count": 0, "failed_count": 0, "current_product_name": "",
            "pause_requested": 0}


def db_available():
    """هل ترد قاعدة البيانات الآن (SELECT 1)؟ لا ترفع استثناء."""
    try:
        conn = get_db_connection()
        try:
            conn.cursor().execute("SELECT 1")
        finally:
            _close(conn)
        return True
    except Exception as e:
        logger.warning("[MariaDB] قاعدة البيانات لا ترد: %s", e)
        return False


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


# ---------------------------------------------------------------------------
# التحكم في التشغيل (تشغيل جديد، إيقاف، إصلاح تشغيل عالق) — cli_bridge run_control والتشغيل الليلي.
# لا يُحذف أي صف أو مرشح أو قرار مراجعة هنا أبداً: الإيقاف يعيد الصفوف قيد المعالجة إلى الانتظار فقط.
# ---------------------------------------------------------------------------

def new_run_id():
    return uuid.uuid4().hex[:16]


def prepare_run():
    """
    قبل كل تشغيل جديد (زر التشغيل في اللوحة، والتشغيل الليلي): الحالة 'starting' بلا أرقام التشغيل السابق
    (run_id فارغ حتى ينتهي الإدراج)، ويُلغى طلب الإيقاف والإيقاف المؤقت القديمان والتنبيه السابق.
    يُستدعى قبل بدء الإدراج، فطلب إيقاف يصل أثناء قراءة الشيت لا يُمسح. تعيد True، أو False عند خطأ قاعدة البيانات.
    """
    try:
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE automation_state SET status = 'starting', stop_requested = 0, pause_requested = 0, "
                "run_id = NULL, notice = NULL, current_product_name = '', total_items = 0, processed_items = 0, "
                "success_count = 0, failed_count = 0, updated_at = CURRENT_TIMESTAMP WHERE `key` = 'active_session'"
            )
            conn.commit()
        finally:
            _close(conn)
        return True
    except Exception as e:
        logger.warning("[MariaDB State] فشل تجهيز التشغيل الجديد: %s", e)
        return False


def begin_run(run_id):
    """
    نهاية الإدراج: كل صف مفتوح (pending أو processing) سيعالجه العامل في هذا التشغيل فيحمل run_id، وهذا يشمل
    الصفوف التي أعاد الإدراج ضبطها وأي صف بقي في الانتظار من تشغيل سابق. الصفوف الجاهزة للمراجعة أو المعتمدة
    التي أبقاها الإدراج كما هي ليست عمل هذا التشغيل، فلا تُحسب في تقدمه (وإلا بدأ التقدم من نسبة عالية).
    يسجل run_id وعدد صفوفه في automation_state. تعيد عدد الصفوف، أو None عند خطأ قاعدة البيانات (يُسجل).
    """
    try:
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("UPDATE automation_queue SET run_id = %s WHERE status IN ('pending','processing')",
                           (run_id,))
            cursor.execute("SELECT COUNT(*) AS cnt FROM automation_queue WHERE run_id = %s", (run_id,))
            total = int(cursor.fetchone()["cnt"])
            cursor.execute(
                "UPDATE automation_state SET run_id = %s, total_items = %s, processed_items = 0, success_count = 0, "
                "failed_count = 0, updated_at = CURRENT_TIMESTAMP WHERE `key` = 'active_session'",
                (run_id, total),
            )
            conn.commit()
        finally:
            _close(conn)
        return total
    except Exception as e:
        logger.warning("[MariaDB State] فشل تسجيل التشغيل %s: %s", run_id, e)
        return None


def _status_counts(cursor, where="", params=()):
    cursor.execute(f"SELECT status, COUNT(*) AS cnt FROM automation_queue {where} GROUP BY status", params)
    stats = {"total": 0, "pending": 0, "processing": 0, "ready_for_review": 0, "completed": 0, "failed": 0}
    for r in cursor.fetchall():
        status = r["status"] or "unknown"
        stats[status] = stats.get(status, 0) + int(r["cnt"])
        stats["total"] += int(r["cnt"])
    return stats


def get_run_statistics(run_id):
    """
    عدادات صفوف تشغيل واحد (run_id) حسب الحالة، مع processed = جاهز للمراجعة + مكتمل + فاشل.
    أخطاء قاعدة البيانات تُرفع.
    """
    conn = get_db_connection()
    try:
        stats = _status_counts(conn.cursor(), "WHERE run_id = %s", (run_id,))
    finally:
        _close(conn)
    stats["processed"] = stats["ready_for_review"] + stats["completed"] + stats["failed"]
    stats["run_id"] = run_id
    return stats


def _release_processing(cursor):
    """الصفوف العالقة في 'processing' (عامل أُنهي أو توقف) تعود إلى 'pending' بلا حجز؛ تعيد عددها."""
    cursor.execute(
        "UPDATE automation_queue SET status = 'pending', worker_id = NULL, lease_until = NULL, "
        "updated_at = CURRENT_TIMESTAMP WHERE status = 'processing'"
    )
    return cursor.rowcount


def _settled_status(cursor):
    """الحالة بعد انتهاء التشغيل: بانتظار المراجعة إن بقي صف جاهز للمراجعة، وإلا خامل."""
    cursor.execute("SELECT COUNT(*) AS cnt FROM automation_queue WHERE status = 'ready_for_review'")
    return "curation_pending" if int(cursor.fetchone()["cnt"]) > 0 else "idle"


def _run_control_result(cursor, released, stop_requested):
    cursor.execute("SELECT status FROM automation_state WHERE `key` = 'active_session'")
    row = cursor.fetchone() or {}
    return {"released": released, "stop_requested": bool(stop_requested), "status": row.get("status"),
            "queue": _status_counts(cursor)}


def stop_run(worker_active=False):
    """
    زر «إيقاف التشغيل». لا يُحذف أي صف: الجاهز للمراجعة والمعتمد والفاشل والمنتظر يبقى كما هو مع مرشحاته.
    - worker_active=True (الإدراج ما زال يقرأ الشيت ولا عامل بعد، أو العامل ما زال حياً): يُسجل طلب إيقاف
      يلتزم به العامل بين المنتجات، أو عند بدئه قبل معالجة أي منتج؛ الحالة لا تتغير.
    - worker_active=False (أُنهي العامل أو لم يكن يعمل): الصفوف في 'processing' تعود إلى 'pending'، ويُلغى طلبا
      الإيقاف والإيقاف المؤقت، والحالة: بانتظار المراجعة إن بقي صف جاهز، وإلا خامل. التنبيه يبقى كما هو.
    تعيد {released, stop_requested, status, queue}. أخطاء قاعدة البيانات تُرفع.
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        released = 0
        if worker_active:
            cursor.execute("UPDATE automation_state SET stop_requested = 1 WHERE `key` = 'active_session'")
        else:
            released = _release_processing(cursor)
            cursor.execute(
                "UPDATE automation_state SET status = %s, stop_requested = 0, pause_requested = 0, "
                "current_product_name = '', updated_at = CURRENT_TIMESTAMP WHERE `key` = 'active_session'",
                (_settled_status(cursor),),
            )
        conn.commit()
        return _run_control_result(cursor, released, worker_active)
    finally:
        _close(conn)


def reset_run(worker_active=False):
    """
    زر «إصلاح تشغيل عالق»: يمسح حالة التشغيل العالقة فقط، ولا يحذف أي صف ولا يلمس curation_candidates
    ولا review_decisions ولا rejected_images ولا resolved_products. الصفوف في 'processing' تعود إلى 'pending'،
    ويُلغى الإيقاف المؤقت، ويُمسح التقدم (run_id والعدادات) والمنتج الحالي والتنبيه، والحالة: بانتظار المراجعة
    إن بقي صف جاهز، وإلا خامل. worker_active=True: قد يكون الإدراج ما زال يقرأ الشيت أو بقي عامل حياً، فيُسجل طلب
    إيقاف كي لا يبدأ المعالجة أو يتوقف بعد المنتجات الجارية؛ وإلا يُلغى طلب الإيقاف. تعيد
    {released, stop_requested, status, queue}. أخطاء قاعدة البيانات تُرفع.
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        released = _release_processing(cursor)
        cursor.execute(
            "UPDATE automation_state SET status = %s, stop_requested = %s, pause_requested = 0, run_id = NULL, "
            "notice = NULL, current_product_name = '', total_items = 0, processed_items = 0, success_count = 0, "
            "failed_count = 0, updated_at = CURRENT_TIMESTAMP WHERE `key` = 'active_session'",
            (_settled_status(cursor), 1 if worker_active else 0),
        )
        conn.commit()
        return _run_control_result(cursor, released, worker_active)
    finally:
        _close(conn)


# ---------------------------------------------------------------------------
# نتيجة التشغيل وسجل التشغيلات (run_history) — run_report.py يكتبها بعد كل تشغيل
# ---------------------------------------------------------------------------

# رموز «لم يُعثر على صورة مقبولة» (المنتج، لا عطل خدمة) كما في QueueStats::NOT_FOUND_CODES
NOT_FOUND_CODES = ("NO_RESULTS", "ALL_CONFLICTED", "NO_MATCH", "NOT_FOUND")
RUN_COUNT_KEYS = ("enqueued", "searched", "auto_published", "ready_for_review", "not_found", "failed",
                  "provider_down", "pending_left")


def run_outcome_counts(run_ids=None, worker_id=None, since_seconds=None):
    """
    نتيجة تشغيل من صفوف الطابور: صفوف run_ids (محاولات الليلة كلها)، أو بلا run_id صفوف العامل worker_id
    (قبل '#') التي تحدثت خلال since_seconds. تعيد {enqueued, searched, auto_published, ready_for_review,
    not_found, failed, provider_down, pending_left}:
      enqueued       صفوف التشغيل؛ searched ما بُحث عنه (انتهى، أو عاد للانتظار لأن المزودين لا يردون)
      auto_published مكتمل بقرار AUTO_PUBLISH؛ not_found فشل برمز «لا صورة مقبولة»؛ failed باقي الفشل
      provider_down  عاد للانتظار برمز PROVIDER_DOWN؛ pending_left ما بقي في الانتظار أو قيد المعالجة
    أخطاء قاعدة البيانات تُرفع.
    """
    ids = [str(r) for r in (run_ids or []) if r]
    if ids:
        where, params = f"run_id IN ({', '.join(['%s'] * len(ids))})", tuple(ids)
    elif worker_id:
        prefix = str(worker_id).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "#%"
        where = "worker_id LIKE %s AND updated_at >= NOW() - INTERVAL %s SECOND"
        params = (prefix, int(since_seconds or 24 * 3600))
    else:
        return dict.fromkeys(RUN_COUNT_KEYS, 0)
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT status, UPPER(COALESCE(failure_code, '')) AS code, "
            "JSON_UNQUOTE(JSON_EXTRACT(trace_json, '$.outcome.decision')) AS decision, COUNT(*) AS cnt "
            f"FROM automation_queue WHERE {where} GROUP BY status, code, decision", params)
        rows = cursor.fetchall()
    finally:
        _close(conn)
    counts = dict.fromkeys(RUN_COUNT_KEYS, 0)
    for r in rows:
        n, status, code = int(r["cnt"]), r["status"], r["code"] or ""
        counts["enqueued"] += n
        if status == "completed":
            counts["searched"] += n
            counts["auto_published"] += n if r.get("decision") == "AUTO_PUBLISH" else 0
        elif status == "ready_for_review":
            counts["searched"] += n
            counts["ready_for_review"] += n
        elif status == "failed":
            counts["searched"] += n
            counts["not_found" if code in NOT_FOUND_CODES else "failed"] += n
        else:
            counts["pending_left"] += n
            if code == "PROVIDER_DOWN":
                counts["provider_down"] += n
                counts["searched"] += n
    return counts


_RUN_HISTORY_FIELDS = ("run_id", "run_trigger", "started_at", "ended_at", "outcome", "stop_reason", "exit_code",
                       "attempts") + RUN_COUNT_KEYS + ("outbox_pending", "outbox_conflict", "outbox_dead",
                                                       "spend_usd", "spend_source", "notices", "report_json")


def save_run_history(entry):
    """
    يضيف صفاً إلى run_history من قاموس بأسماء الأعمدة (الناقص NULL). تعيد رقم الصف، أو None عند خطأ قاعدة البيانات
    (يُسجل؛ التقرير يبقى في temp/nightly/last_report.json).
    """
    values = []
    for field in _RUN_HISTORY_FIELDS:
        value = entry.get(field)
        if isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False, default=str)
        if field in ("stop_reason", "outcome", "run_trigger", "spend_source", "run_id") and isinstance(value, str):
            value = value[:64]
        values.append(value)
    try:
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                f"INSERT INTO run_history ({', '.join(_RUN_HISTORY_FIELDS)}) "
                f"VALUES ({', '.join(['%s'] * len(_RUN_HISTORY_FIELDS))})", tuple(values))
            conn.commit()
            return cursor.lastrowid
        finally:
            _close(conn)
    except Exception as e:
        logger.warning("[MariaDB] فشل حفظ سجل التشغيل: %s", e)
        return None


def get_run_history(limit=20):
    """أحدث صفوف run_history (الأحدث أولاً) بلا report_json. أخطاء قاعدة البيانات تُرفع."""
    columns = ", ".join(("id",) + tuple(f for f in _RUN_HISTORY_FIELDS if f != "report_json") + ("created_at",))
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(f"SELECT {columns} FROM run_history ORDER BY id DESC LIMIT %s", (max(1, int(limit)),))
        return [dict(r) for r in cursor.fetchall()]
    finally:
        _close(conn)


# تهيئة قاعدة البيانات تلقائياً عند استيراد الموديول للمرة الأولى
init_db()
