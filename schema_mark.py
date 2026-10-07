# schema_mark.py
# «هل جداول هالموديول محدّثة؟» بسؤال واحد بدل عشرات أوامر CREATE/ALTER بكل استيراد.
#
# كل موديول بيجهّز جداوله (local_cache_db.init_db، طابور الشيت) بيسجّل بصمة ملفه بجدول schema_marks بعد ما ينجح.
# الاستيراد الجاي بيقارن البصمة: نفسها = الجداول جاهزة وما في شي يتنفّذ؛ تغيّر الملف (نسخة جديدة من الكود) أو انرجعت
# نسخة احتياطية أقدم = البصمة مختلفة فالتجهيز بيرجع يشتغل مرة وحدة. لإجبار التجهيز: DELETE FROM schema_marks.
# أي خطأ (الجدول مش موجود، القاعدة ما بترد) معناه «مش محدّث»: التجهيز نفسه بيقرر وبيبلّغ.

import hashlib
import logging

import db_connect

logger = logging.getLogger(__name__)


def fingerprint(path):
    """بصمة ملف الموديول (sha1 لمحتواه): أي تغيير بالكود بيعيد التجهيز مرة."""
    with open(path, "rb") as fh:
        return hashlib.sha1(fh.read()).hexdigest()


def is_current(name, mark):
    try:
        conn = db_connect.connect(dict_cursor=False, connect_timeout=5)
    except Exception:  # noqa: BLE001 - القاعدة ما بترد: التجهيز بيحاول وبيبلّغ
        return False
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT fingerprint FROM schema_marks WHERE name = %s", (name,))
        row = cursor.fetchone()
        return bool(row) and row[0] == mark
    except Exception:  # noqa: BLE001 - جدول schema_marks لسا ما انعمل
        return False
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


def save(name, mark):
    """سجّل إنه تجهيز name نجح بهالبصمة. فشل التسجيل مش مشكلة: الاستيراد الجاي بيعيد التجهيز وبس."""
    try:
        conn = db_connect.connect(dict_cursor=False, connect_timeout=5)
        try:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS schema_marks (
                    name VARCHAR(64) PRIMARY KEY,
                    fingerprint CHAR(40) NOT NULL,
                    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                ) ENGINE=InnoDB CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
            """)
            cursor.execute("REPLACE INTO schema_marks (name, fingerprint) VALUES (%s, %s)", (name, mark))
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning("تعذر تسجيل بصمة الجداول %s: %s", name, exc)
