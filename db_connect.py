# db_connect.py
# اتصال MariaDB واحد لكل موديولات بايثون (local_cache_db، google_sheets، config، السكربتات).
#
# - المتغيرات البيئية DB_* تُقرأ عند كل اتصال (الاختبارات تغيّر DB_DATABASE قبل الاستيراد).
# - مهل الاتصال والقراءة والكتابة: قاعدة بيانات علّقت (شبكة انقطعت دون إغلاق الاتصال، خادم متجمد) ترفع خطأ بعد
#   المهلة بدل أن يعلق العامل للأبد. مهلة القراءة أطول من أطول انتظار مقصود: GET_LOCK لقفل النشر (60 ثانية)
#   وانتظار أقفال InnoDB (50 ثانية افتراضياً).
# - pymysql.connect تُستدعى وقت الاتصال (لا تُربط عند الاستيراد): الاختبارات و cassette تستبدلها.

import os

import pymysql

DEFAULT_CONNECT_TIMEOUT_S = 10
DEFAULT_READ_TIMEOUT_S = 120
DEFAULT_WRITE_TIMEOUT_S = 60


def _seconds(name, default):
    """مهلة بالثواني من المتغير البيئي name (1..3600)؛ قيمة فارغة أو غير صالحة = default."""
    try:
        value = int(float(str(os.getenv(name) or "").strip()))
    except (TypeError, ValueError):
        return default
    return min(3600, max(1, value))


def timeouts():
    """{connect_timeout, read_timeout, write_timeout} من DB_CONNECT_TIMEOUT_S / DB_READ_TIMEOUT_S / DB_WRITE_TIMEOUT_S."""
    return {
        "connect_timeout": _seconds("DB_CONNECT_TIMEOUT_S", DEFAULT_CONNECT_TIMEOUT_S),
        "read_timeout": _seconds("DB_READ_TIMEOUT_S", DEFAULT_READ_TIMEOUT_S),
        "write_timeout": _seconds("DB_WRITE_TIMEOUT_S", DEFAULT_WRITE_TIMEOUT_S),
    }


def connect(database=True, dict_cursor=True, **overrides):
    """
    اتصال pymysql بإعدادات DB_* والمهل الثلاث. database=False: اتصال بالخادم بلا قاعدة (init_db ينشئها)، أو اسم
    قاعدة بعينها؛ dict_cursor=False: مؤشر صفوف عادي. overrides يغيّر أي وسيط (مثل connect_timeout=5).
    """
    kwargs = {
        "host": os.getenv("DB_HOST", "127.0.0.1"),
        "port": int(os.getenv("DB_PORT", "3306")),
        "user": os.getenv("DB_USERNAME", "root"),
        "charset": "utf8mb4",
    }
    kwargs["password"] = os.environ.get("DB_PASSWORD") or ""
    if isinstance(database, str):
        kwargs["database"] = database
    elif database:
        kwargs["database"] = os.getenv("DB_DATABASE", "automation_db")
    if dict_cursor:
        kwargs["cursorclass"] = pymysql.cursors.DictCursor
    kwargs.update(timeouts())
    kwargs.update(overrides)
    return pymysql.connect(**kwargs)
