# google_sheets.py
# موديول التعامل مع Google Sheets: قراءة المنتجات بعناوين أعمدة مرنة، وكتابة آمنة عبر طابور MariaDB (outbox).
#
# قواعد الأمان (SHEET-1/2, SYNC-1/2, D12):
# - الأعمدة تُحدد بأسماء العناوين (مع جدول مرادفات) ولا يوجد أي رجوع لمواقع ثابتة؛ غياب عمود الاسم خطأ صريح.
# - لا يُخترع البراند من أول كلمة في الاسم: البراند الفارغ يبقى فارغاً.
# - كل كتابة تحمل هوية المنتج (الباركود/الاسم/الحجم/البراند). عند التفريغ نعيد قراءة أعمدة الهوية للصف الهدف،
#   وأي اختلاف يُسجل CONFLICT ولا يُكتب. الباركود وحده مفتاح فقط إذا كان GTIN صالحاً (رقم تحقق GS1)؛
#   'N/A' و'0' و'-' و'6.29E+12' ليست مفاتيح أبداً، فيُقارن الاسم مع الحجم والبراند.
# - عند CONFLICT تُقرأ أعمدة الهوية للورقة كلها مرة واحدة: إذا طابق المنتجَ صفٌ واحد فقط تُنقل الكتابة إليه
#   (صف أُدرج أو حُذف فوقه)، وإلا تبقى CONFLICT.
# - عمود الهدف يُحمل كمفتاح منطقي ('link' أو 'meta:<key>') ويُحدد من العناوين وقت الكتابة، لا برقم العمود
#   ولا باسم العنوان الذي كان في ذلك الموقع عند الجدولة (إدراج عمود أثناء التشغيل لا يغيّر العمود المكتوب).
# - الكتابة الفاشلة تُعاد على مواعيد متباعدة (1 د، 5 د، 30 د، 2 س) عبر التشغيلات قبل أن تصبح DEAD؛
#   كل كتابة تنتهي دون أن تُكتب (CONFLICT / DEAD / SKIPPED_OUT_OF_BOUNDS) تُبلَّغ عبر outcome hook،
#   ونتائج الطابور تُقرأ عبر outbox_outcomes().
# - الترتيب: كل كتابة تحمل seq (وقت الجدولة بالنانوثانية) والمُفرِّغ لا يرسل قيمة أقدم من قيمة كُتبت بعدها
#   لنفس العمود ونفس المنتج. Redis ليس قناة كتابة ثانية: sync_worker ينقل حمولاته إلى هذا الطابور نفسه.
# - الكتابة المؤجلة عبر Redis تُستخدم فقط إذا كان مفتاح نبض sync_worker موجوداً.

import hashlib
import json
import logging
import os
import random
import re
import threading
import time
import unicodedata

import gspread
import pymysql
from gspread.exceptions import APIError

import atomic_file
import config
import db_connect
from catalog_match.gtin import normalize_gtin

logger = logging.getLogger(__name__)

_queue = None
_worker = None
_redis_client = None
_redis_cache_available = None

# مكان ملفات الكاش المحلية (قابل للتغيير في الاختبارات)
CACHE_DIR = os.path.dirname(os.path.abspath(__file__))
PRODUCTS_CACHE_VERSION = 2
BRAND_CACHE_VERSION = 2

HEARTBEAT_KEY = "writebehind:heartbeat"
DIRTY_SET_KEY = "writebehind:dirty_set"
CACHE_PREFIX = "product:data:"
REDIS_PAYLOAD_VERSION = 2
# مواعيد إعادة محاولة الكتابة الفاشلة (ثوانٍ) بعد المحاولات 1..4؛ فشل المحاولة الخامسة يجعلها DEAD
RETRY_BACKOFF = (60, 300, 1800, 7200)
MAX_OUTBOX_ATTEMPTS = len(RETRY_BACKOFF) + 1
OUTBOX_BATCH = 200

# المفاتيح المنطقية لأعمدة الكتابة: 'link' (عمود رابط الصورة بمرادفاته) و'meta:<key>' (أعمدة البيانات الوصفية)
LINK_KEY = "link"
META_PREFIX = "meta:"
# عمود الباركود ('barcode' بمرادفاته): يُكتب فقط من «اكتب الباركودات المختارة» (queue_barcode_writes)، وفقط في خلية
# فارغة يُعاد فحصها وقت التفريغ (FILL_ONLY_KEYS): باركود موجود في الشيت لا يُكتب فوقه أبداً
BARCODE_KEY = "barcode"
FILL_ONLY_KEYS = frozenset({BARCODE_KEY})

# أخطاء مؤقتة تُعاد محاولتها: تجاوز الحصة وأخطاء الخادم
_TRANSIENT_CODES = (429, 500, 502, 503, 504)
_sleep = time.sleep      # قابلة للاستبدال في الاختبارات
_now = time.time


class SheetConfigError(Exception):
    """إعداد الشيت غير صالح (مثل تبويب غير موجود)."""


class SheetSchemaError(SheetConfigError):
    """عناوين الأعمدة لا تسمح بتحديد عمود إلزامي (مثل اسم المنتج)."""


class SheetTransientError(Exception):
    """
    Google Sheets غير متاح مؤقتاً (429 / 5xx / انقطاع الاتصال) بعد استنفاد إعادة المحاولة.
    ليس خطأ إعداد: الرابط والمشاركة سليمان، والحل الانتظار ثم إعادة المحاولة.
    """


# ---------------------------------------------------------------------------
# عناوين الأعمدة ومرادفاتها
# ---------------------------------------------------------------------------

_NON_ALNUM_RE = re.compile(r"[\W_]+", re.UNICODE)


def normalize_header(header):
    """strip + casefold + تحويل أي علامات ترقيم أو فراغات متتالية إلى فراغ واحد."""
    text = unicodedata.normalize("NFKC", str(header or "")).casefold()
    return _NON_ALNUM_RE.sub(" ", text).strip()


# الترتيب داخل كل قائمة هو الأولوية (أول مرادف موجود يفوز، لا ترتيب الأعمدة في الشيت)
COLUMN_SYNONYMS = {
    "barcode": ["barcode", "ean", "gtin", "upc", "item code", "barcode no", "barcode number",
                "الباركود", "باركود"],
    "name": ["product name", "productname", "item name", "name en", "description", "اسم المنتج"],
    "brand": ["brand", "brand en", "brand name", "البراند", "الماركة", "العلامة التجارية"],
    "size": ["size", "weight", "volume", "pack size", "الحجم"],
    "name_ar": ["product name arabic", "productname arabic", "name ar", "product name ar",
                "اسم المنتج بالعربي", "اسم المنتج عربي"],
    "brand_ar": ["brand arabic", "brand ar", "البراند بالعربي", "البراند عربي"],
    "category": ["category", "الفئة", "التصنيف"],
    "sub_category": ["sub category", "subcategory"],
    "sub_sub_category": ["sub sub category"],
    "sub_sub_category_ar": ["sub sub category arabic"],
    "origin": ["origin", "بلد المنشأ", "المنشأ"],
    "link": ["drive image link", "image link", "رابط الصورة", "images"],
}
DEFAULT_LINK_HEADER = "Drive Image Link"


def resolve_columns(headers):
    """{مفتاح منطقي: فهرس العمود (0-based) أو -1} حسب جدول المرادفات."""
    normalized = [normalize_header(h) for h in headers or []]
    out = {}
    for key, synonyms in COLUMN_SYNONYMS.items():
        idx = -1
        for syn in synonyms:
            target = normalize_header(syn)
            if target in normalized:
                idx = normalized.index(target)
                break
        out[key] = idx
    return out


def resolve_column_key(headers, col_key):
    """
    فهرس العمود (0-based) لمفتاح منطقي حسب العناوين الحالية، أو -1. المفاتيح: مفاتيح COLUMN_SYNONYMS
    (مثل 'link') و'meta:<key>' لأعمدة البيانات الوصفية (_METADATA_COLUMNS).
    """
    key = str(col_key or "").strip()
    if key in COLUMN_SYNONYMS:
        return resolve_columns(headers)[key]
    if key.startswith(META_PREFIX) and key[len(META_PREFIX):] in _METADATA_COLUMNS:
        target = normalize_header(_METADATA_COLUMNS[key[len(META_PREFIX):]])
        normalized = [normalize_header(h) for h in headers or []]
        return normalized.index(target) if target in normalized else -1
    return -1


def _col_key_for_header(header):
    """المفتاح المنطقي لاسم عنوان (لكتابات قديمة في الطابور بلا col_key)، أو None."""
    target = normalize_header(header)
    if not target:
        return None
    for key, synonyms in COLUMN_SYNONYMS.items():
        if target in (normalize_header(s) for s in synonyms):
            return key
    for key, name in _METADATA_COLUMNS.items():
        if normalize_header(name) == target:
            return f"{META_PREFIX}{key}"
    return None


def _cell(row, idx):
    return row[idx].strip() if 0 <= idx < len(row) else ""


def _gtin(value):
    """GTIN-14 لباركود صالح (طول ورقم تحقق GS1 صحيحان)، وإلا None: العناصر النائبة ليست مفاتيح أبداً."""
    try:
        return normalize_gtin(value)[0]
    except Exception:
        return None


def _norm_name(value):
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", str(value or "")).casefold()).strip()


# ---------------------------------------------------------------------------
# الكاش المحلي لقراءات الشيت
# ---------------------------------------------------------------------------

def _cache_path(name):
    return os.path.join(CACHE_DIR, name)


def clear_cache():
    for name in ("products_cache.json", "brand_mappings_cache.json"):
        path = _cache_path(name)
        if os.path.exists(path):
            try:
                os.remove(path)
                logger.debug("[Google Sheets Cache] حذف ملف الكاش %s", name)
            except Exception:
                pass


def clear_brand_cache():
    """يحذف كاش ورقة Brands Mapping فقط (كاش صفوف المنتجات يبقى): التشغيل الجاي يقرأ الورقة من جديد."""
    path = _cache_path("brand_mappings_cache.json")
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass
    try:
        from catalog_match import learning
        learning.clear_cache()
    except Exception:  # noqa: BLE001 - كاش التعلّم يُمسح إن أمكن؛ عمره ثوانٍ
        pass


def _read_cache(name, ttl, version):
    path = _cache_path(name)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            cached = json.load(f)
        if cached.get("version") == version and time.time() - cached.get("timestamp", 0) < ttl:
            return cached
    except Exception as ce:
        logger.warning("[Google Sheets Cache] تعذر قراءة %s: %s", name, ce)
    return None


def cached_products():
    """
    صفوف المنتجات من كاش آخر قراءة للشيت (products_cache.json، كما يعيدها get_products) بلا أي طلب لـ Google ومهما
    كان عمرها، أو None بلا كاش. للقراءة فقط (اقتراحات الباركود): كل كتابة تُتحقق من الصف الحي وقت التفريغ.
    """
    cached = _read_cache("products_cache.json", float("inf"), PRODUCTS_CACHE_VERSION)
    products = cached.get("products") if cached else None
    return [p for p in products if isinstance(p, dict)] if isinstance(products, list) else None


def _write_cache(name, payload, version):
    try:
        payload = dict(payload, timestamp=time.time(), version=version)
        # ذرياً: الجسر واللوحة يقرآن الكاش أثناء كتابته، وانقطاع الكهرباء لا يترك ملفاً نصف مكتوب
        atomic_file.write_json(_cache_path(name), payload, ensure_ascii=False, indent=2)
    except Exception as ce:
        logger.warning("[Google Sheets Cache] تعذر كتابة %s: %s", name, ce)


# ---------------------------------------------------------------------------
# إعادة المحاولة عند 429 / 5xx
# ---------------------------------------------------------------------------

def _is_transient(exc):
    """
    خطأ مؤقت يستحق إعادة المحاولة: APIError برمز 429/500/502/503/504 (أو 403 لتجاوز معدل Drive)،
    أو انقطاع/مهلة اتصال. 'غير موجود' و'لا صلاحية' (404/403/SpreadsheetNotFound) ليست مؤقتة.
    """
    if isinstance(exc, APIError):
        status = getattr(getattr(exc, "response", None), "status_code", None)
        code = getattr(exc, "code", None)
        if code in _TRANSIENT_CODES or status in _TRANSIENT_CODES:
            return True
        return code == 403 and "ratelimitexceeded" in str(exc).replace(" ", "").casefold()
    if isinstance(exc, (ConnectionError, TimeoutError)):
        return True
    try:
        import requests
        if isinstance(exc, (requests.exceptions.ConnectionError, requests.exceptions.Timeout,
                            requests.exceptions.ChunkedEncodingError)):
            return True
    except ImportError:  # pragma: no cover - requests is a gspread dependency
        pass
    try:
        from google.auth.exceptions import TransportError
        return isinstance(exc, TransportError)
    except ImportError:  # pragma: no cover
        return False


def retry_gspread_on_429(max_retries=5):
    """
    مُزخرف لإعادة محاولة استدعاءات Google API عند الأخطاء المؤقتة (429 و5xx وانقطاع الاتصال، انظر
    _is_transient) مع ارتداد أسّي عشوائي. الأخطاء الأخرى تُرفع كما هي.
    """
    def decorator(func):
        def wrapper(*args, **kwargs):
            retry = 0
            while True:
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    if _is_transient(e) and retry < max_retries:
                        retry += 1
                        backoff = min((2 ** retry) + random.uniform(0.1, 1.0), 32)
                        logger.warning("[Google Sheets API] خطأ مؤقت (%s)؛ إعادة المحاولة %s/%s خلال %.2f ثانية",
                                       _one_line(e), retry, max_retries, backoff)
                        _sleep(backoff)
                        continue
                    raise
        wrapper.__wrapped__ = func
        wrapper.__name__ = getattr(func, "__name__", "wrapper")
        return wrapper
    return decorator


def _retrying(func, *args, **kwargs):
    """
    استدعاء Google API مع إعادة المحاولة عند الأخطاء المؤقتة؛ إذا بقي الخطأ مؤقتاً بعد كل المحاولات يُرفع
    SheetTransientError (رسالة واضحة أنه ليس خطأ في الرابط أو المشاركة).
    """
    try:
        return retry_gspread_on_429()(func)(*args, **kwargs)
    except Exception as e:
        if _is_transient(e):
            raise SheetTransientError(
                "Google Sheets غير متاح مؤقتاً (تجاوز الحصة أو خطأ في خادم Google أو انقطاع الاتصال) بعد عدة "
                f"محاولات: {_one_line(e)}. هذا ليس خطأ في رابط الشيت أو مشاركته؛ أعد المحاولة بعد دقائق."
            ) from e
        raise


# ---------------------------------------------------------------------------
# الاتصال وفتح الشيت
# ---------------------------------------------------------------------------

def get_sheets_client():
    """الاتصال بـ Google Sheets API باستخدام ملف الاعتمادات."""
    try:
        return gspread.service_account(filename=config.CREDENTIALS_FILE)
    except Exception as e:
        logger.error("فشل الاتصال بـ Google Sheets API: %s", e)
        return None


def _one_line(value):
    """نص آمن لسطر سجل واحد: قيم يدخلها المستخدم لا يجوز أن تزوّر أسطر سجل جديدة."""
    return str(value).replace("\r\n", " ").replace("\n", " ").replace("\r", " ")


def _open_spreadsheet(client, sheet_name_or_url):
    if str(sheet_name_or_url).startswith("https://"):
        return client.open_by_url(sheet_name_or_url)
    return client.open(sheet_name_or_url)


def open_worksheet(client, sheet_name_or_url, worksheet_index=0):
    """
    فتح ورقة العمل. إذا كان SPREADSHEET_TAB_NAME مضبوطاً وغير موجود نرفع SheetConfigError
    بدلاً من الكتابة بصمت في التبويب الأول. تعيد None إذا كان الملف غير موجود أو غير مشارك مع حساب الخدمة.
    الأخطاء المؤقتة (429 / 5xx / انقطاع الاتصال) تُعاد محاولتها، وإذا استمرت يُرفع SheetTransientError
    (لا None: رسالة 'الشيت غير موجود / تحقق من المشاركة' لا تصف خطأً مؤقتاً).
    """
    try:
        sh = _retrying(_open_spreadsheet, client, sheet_name_or_url)
    except SheetTransientError as e:
        logger.error("تعذر فتح جدول البيانات '%s' مؤقتاً: %s", _one_line(sheet_name_or_url), e)
        raise
    except Exception as e:
        logger.error("فشل فتح جدول البيانات '%s' (غير موجود أو غير مشارك مع حساب الخدمة): %s",
                     _one_line(sheet_name_or_url), _one_line(e))
        return None
    tab_name = (getattr(config, "SPREADSHEET_TAB_NAME", "") or "").strip()
    if tab_name and worksheet_index == 0:
        try:
            return _retrying(sh.worksheet, tab_name)
        except gspread.exceptions.WorksheetNotFound:
            try:
                titles = [ws.title for ws in sh.worksheets()]
            except Exception:
                titles = []
            raise SheetConfigError(f"التبويب '{tab_name}' غير موجود في الشيت. التبويبات المتاحة: {titles}")
    return _retrying(sh.get_worksheet, worksheet_index)


_HEADER_TTL = 60.0
_header_cache = {}
_header_lock = threading.Lock()


def _worksheet_headers(worksheet, fresh=False):
    """صف العناوين (مع كاش قصير 60 ثانية لكل ورقة لتقليل استهلاك الحصة)."""
    key = (getattr(worksheet, "id", None), getattr(worksheet, "title", None), id(worksheet))
    now = time.time()
    with _header_lock:
        hit = _header_cache.get(key)
        if hit and not fresh and now - hit[0] < _HEADER_TTL:
            return list(hit[1])
    headers = worksheet.row_values(1)
    with _header_lock:
        _header_cache[key] = (now, list(headers))
    return list(headers)


def find_link_column(worksheet, create=True):
    """
    فهرس عمود رابط الصورة من العناوين الحالية؛ يُنشأ العمود في النهاية إن لم يوجد و create=True.
    الأخطاء المؤقتة تُعاد محاولتها (SheetTransientError إذا استمرت).
    """
    headers = _retrying(_worksheet_headers, worksheet, fresh=True)
    idx = resolve_columns(headers)["link"]
    if idx == -1 and create:
        idx = len(headers)
        _retrying(worksheet.update_cell, 1, idx + 1, DEFAULT_LINK_HEADER)
        _retrying(_worksheet_headers, worksheet, fresh=True)
        logger.info("تم إنشاء عمود '%s' في العمود رقم %s", DEFAULT_LINK_HEADER, idx + 1)
    return idx


def _fresh_row_count(worksheet):
    """
    عدد صفوف الورقة الآن: gspread يحفظ row_count من لحظة فتح الورقة، فورقة كبرت أثناء التشغيل تبدو أصغر.
    يُحدَّث row_count المحفوظ أيضاً. يعيد None إذا تعذرت القراءة (لا يُحكم على أي كتابة بأنها خارج الورقة).
    """
    try:
        meta = _retrying(worksheet.spreadsheet.fetch_sheet_metadata)
        for sheet in (meta or {}).get("sheets", []):
            props = sheet.get("properties") or {}
            if props.get("sheetId") == getattr(worksheet, "id", None):
                stored = getattr(worksheet, "_properties", None)
                if isinstance(stored, dict):
                    stored.update(props)
                return int(props["gridProperties"]["rowCount"])
        logger.warning("[Google Sheets] الورقة %s غير موجودة في بيانات الشيت.", getattr(worksheet, "title", ""))
    except Exception as e:
        logger.warning("[Google Sheets] تعذر تحديث عدد صفوف الورقة: %s", _one_line(e))
    return None


# ---------------------------------------------------------------------------
# قراءة المنتجات
# ---------------------------------------------------------------------------

def get_products(worksheet):
    """
    جلب كل المنتجات مع رقم الصف. الأعمدة تُحدد بالعناوين؛ يُرفع SheetSchemaError إذا لم يوجد عمود الاسم.
    البراند الفارغ يبقى ''. تعيد (products, link_idx). الأخطاء المؤقتة تُعاد محاولتها (SheetTransientError إذا استمرت).
    """
    cached = _read_cache("products_cache.json", 3600, PRODUCTS_CACHE_VERSION)
    if cached:
        return cached["products"], cached["link_idx"]

    rows = _retrying(worksheet.get_all_values)
    if not rows or len(rows) <= 1:
        logger.warning("لا توجد بيانات في الشيت (أو يوجد صف العناوين فقط).")
        return [], -1

    headers = rows[0]
    cols = resolve_columns(headers)
    if cols["name"] == -1:
        raise SheetSchemaError(
            "تعذر تحديد عمود اسم المنتج. العناوين الموجودة: "
            f"{[h for h in headers]}. العناوين المقبولة: {COLUMN_SYNONYMS['name']}"
        )

    link_idx = cols["link"]
    if link_idx == -1:
        link_idx = len(headers)
        _retrying(worksheet.update_cell, 1, link_idx + 1, DEFAULT_LINK_HEADER)
        logger.info("تم إنشاء عمود جديد '%s' في العمود رقم %s", DEFAULT_LINK_HEADER, link_idx + 1)

    products = []
    for idx, row in enumerate(rows[1:], start=2):
        product_name = _cell(row, cols["name"])
        if not product_name:
            continue
        brand = _cell(row, cols["brand"])
        existing_link = _cell(row, link_idx)
        needs_review = False
        needs_review_url = ""
        if existing_link.startswith("needs_review:"):
            needs_review = True
            needs_review_url = existing_link[len("needs_review:"):].strip()
            existing_link = ""  # ليس رابطاً نهائياً: الصف يبقى في طابور الأتمتة والمراجعة
        products.append({
            "row_number": idx,
            "product_name": product_name,
            "product_name_ar": _cell(row, cols["name_ar"]),
            "brand": brand,
            "brand_ar": _cell(row, cols["brand_ar"]),
            "size": _cell(row, cols["size"]),
            "category": _cell(row, cols["category"]),
            "sub_category": _cell(row, cols["sub_category"]),
            "sub_sub_category": _cell(row, cols["sub_sub_category"]),
            "sub_sub_category_ar": _cell(row, cols["sub_sub_category_ar"]),
            "barcode": _cell(row, cols["barcode"]),
            "origin": _cell(row, cols["origin"]),
            "existing_image_link": existing_link,
            "needs_review": needs_review,
            "needs_review_url": needs_review_url,
            "search_query": f"{product_name} {brand}".strip(),
        })

    _write_cache("products_cache.json", {"products": products, "link_idx": link_idx}, PRODUCTS_CACHE_VERSION)
    return products, link_idx


# ---------------------------------------------------------------------------
# هوية الصفوف (للتحقق قبل الكتابة)
# ---------------------------------------------------------------------------

def _expectation(barcode=None, product_name=None, size=None, brand=None):
    """
    هوية المنتج المتوقع في الصف. الحجم والبراند يُخزنان مع الاسم: بدون باركود صالح، شقيقان بنفس الاسم
    (1L و 2L، أو نفس الاسم لبراندين) في صفين متجاورين يتميزان بهما فقط.
    """
    expect = {}
    for key, value in (("barcode", barcode), ("name", product_name), ("size", size), ("brand", brand)):
        if value and str(value).strip():
            expect[key] = str(value).strip()
    return expect or None


def _norm_size(value):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(value or "")).casefold())


# كيف تُقارن كل خانة هوية بين التوقع والشيت
_IDENTITY_NORMS = {"barcode": _gtin, "name": _norm_name, "size": _norm_size, "brand": _norm_name}


def _key_fields(expect, cols):
    """
    خانات الهوية التي تُقارن لتوقع معين: الباركود وحده إذا كان GTIN صالحاً وعموده موجوداً في الشيت، وإلا
    الاسم مع الحجم والبراند (كل منهما إذا كان في التوقع وكان عموده موجوداً). [] إذا تعذر التحقق.
    """
    expect = expect or {}
    if cols.get("barcode", -1) != -1 and _gtin(expect.get("barcode")):
        return ["barcode"]
    if expect.get("name") and cols.get("name", -1) != -1:
        return ["name"] + [f for f in ("size", "brand") if expect.get(f) and cols.get(f, -1) != -1]
    return []


def _identity_mismatch(expect, cells, fields):
    """سبب عدم تطابق خلايا صف ({field: القيمة}) مع التوقع في الخانات المحددة، أو None عند التطابق."""
    for field in fields:
        norm = _IDENTITY_NORMS[field]
        if norm(cells.get(field)) != norm(expect[field]):
            return f"{field} mismatch: sheet has {cells.get(field)!r}, expected {expect[field]!r}"
    return None


def _identity_key(expect):
    """
    هوية قانونية للتوقع (لدمج الكتابات وترتيبها): 'gtin:<GTIN-14>' لباركود صالح، وإلا
    'name:<الاسم>|<الحجم>|<البراند>'؛ '' بلا توقع.
    """
    expect = expect or {}
    gtin = _gtin(expect.get("barcode"))
    if gtin:
        return f"gtin:{gtin}"
    if any(expect.get(f) for f in ("name", "size", "brand")):
        return "name:" + "|".join((_norm_name(expect.get("name")), _norm_size(expect.get("size")),
                                   _norm_name(expect.get("brand"))))
    return f"raw:{expect.get('barcode')}" if expect else ""


def _identity_hash(expect):
    key = _identity_key(expect)
    return hashlib.sha1(key.encode("utf-8")).hexdigest() if key else None


def _outbox_keys(expect):
    """أعمدة الهوية في طابور MariaDB لتوقع معين."""
    expect = expect or {}
    return {"key_barcode": expect.get("barcode"), "key_name": expect.get("name"),
            "key_size": expect.get("size"), "key_brand": expect.get("brand")}


def _read_identity_cells(worksheet, columns, rows=None):
    """
    قيم عدة أعمدة عبر batch_get واحد. columns: {field: فهرس العمود}. rows: أرقام صفوف (يُقرأ النطاق المتصل
    بين أصغرها وأكبرها)، أو None للعمود كله بدءاً من الصف 2. يعيد {field: {row: القيمة}}.
    """
    fields = list(columns)
    if not fields:
        return {}
    lo, hi = (min(rows), max(rows)) if rows else (2, None)
    ranges = []
    for field in fields:
        start = gspread.utils.rowcol_to_a1(lo, columns[field] + 1)
        if hi is None:
            ranges.append(f"{start}:{re.sub(r'[0-9]', '', start)}")
        else:
            ranges.append(f"{start}:{gspread.utils.rowcol_to_a1(hi, columns[field] + 1)}")
    result = _retrying(worksheet.batch_get, ranges)
    out = {}
    for i, field in enumerate(fields):
        values = list(result[i]) if result and i < len(result) else []
        out[field] = {lo + offset: (str(cell[0]).strip() if cell else "") for offset, cell in enumerate(values)}
    return out


def find_record_conflicts(worksheet, records, headers=None):
    """
    records: {record_id: (row_number, {'barcode': ..., 'name': ..., 'size': ..., 'brand': ...})}.
    يعيد {record_id: سبب} لكل سجل لا تطابق أعمدة الهوية في صفه هويته هو. التحقق لكل سجل على حدة:
    سجلان لمنتجين مختلفين على نفس رقم الصف (رقم صف قديم) لا يأخذ أحدهما حكم الآخر.
    الباركود هو المفتاح فقط إذا كان GTIN صالحاً (وعموده موجوداً)، وإلا الاسم مع الحجم والبراند (_key_fields).
    """
    records = {k: (row, e) for k, (row, e) in (records or {}).items() if e}
    if not records:
        return {}
    if headers is None:
        headers = _worksheet_headers(worksheet, fresh=True)
    cols = resolve_columns(headers)
    fields = {k: _key_fields(e, cols) for k, (_, e) in records.items()}
    keyed = [k for k in records if fields[k]]
    conflicts = {}
    if keyed:
        needed = sorted({f for k in keyed for f in fields[k]})
        cells = _read_identity_cells(worksheet, {f: cols[f] for f in needed}, [records[k][0] for k in keyed])
        for k in keyed:
            row, e = records[k]
            reason = _identity_mismatch(e, {f: cells[f].get(row, "") for f in fields[k]}, fields[k])
            if reason:
                conflicts[k] = reason
    for k in records:
        if not fields[k]:
            conflicts[k] = ("no key column in sheet to verify row identity "
                            "(the barcode is not a valid GTIN and there is no product name to compare)")
    return conflicts


# خانات مطابقة الصفوف: (عمود الشيت، دالة التطبيع، خانة التوقع). 'gtin' يقارن GTIN صالحاً، و'barcode_text' يقارن
# نص خلية الباركود كما هو (للنقل الصارم: 'N/A' لا تطابق '-'، والفارغة لا تطابق إلا فارغة)
_MATCH_FIELDS = {"gtin": ("barcode", _gtin, "barcode"), "barcode_text": ("barcode", _norm_name, "barcode"),
                 "name": ("name", _norm_name, "name"), "size": ("size", _norm_size, "size"),
                 "brand": ("brand", _norm_name, "brand")}


def _relocation_fields(expect, cols):
    """
    خانات المطابقة الصارمة لنقل كتابة إلى صف آخر: GTIN صالح وحده، أو هوية كاملة: الاسم والحجم والبراند (أعمدتها
    الثلاثة موجودة في الشيت) ومعها نص الباركود إن كان عموده موجوداً، والخانة الفارغة في التوقع لا تطابق إلا خانة
    فارغة. [] = لا نقل أبداً (عمود ناقص أو بلا اسم): براند أو حجم فارغ لا يصبح «أي قيمة».
    """
    expect = expect or {}
    if cols.get("barcode", -1) != -1 and _gtin(expect.get("barcode")):
        return ["gtin"]
    if expect.get("name") and all(cols.get(f, -1) != -1 for f in ("name", "size", "brand")):
        return ["name", "size", "brand"] + (["barcode_text"] if cols.get("barcode", -1) != -1 else [])
    return []


def _verification_fields(expect, cols):
    """خانات _key_fields بأسماء _MATCH_FIELDS (مطابقة التحقق: الحجم والبراند فقط إذا كانا في التوقع)."""
    return ["gtin" if f == "barcode" else f for f in _key_fields(expect, cols)]


def find_identity_rows(worksheet, expectations, headers=None, strict=True):
    """
    expectations: {record_id: expect}. يعيد {record_id: [أرقام كل الصفوف التي تطابق هوية المنتج]} بعد قراءة
    أعمدة الهوية للورقة كلها مرة واحدة (batch_get واحد). strict=True (للنقل): مطابقة كاملة حسب
    _relocation_fields؛ strict=False: مطابقة التحقق (_key_fields)، لعدّ صفوف المنتج. توقع بلا مفتاح يعيد [].
    """
    expectations = {k: e for k, e in (expectations or {}).items() if e}
    if not expectations:
        return {}
    if headers is None:
        headers = _worksheet_headers(worksheet, fresh=True)
    cols = resolve_columns(headers)
    choose = _relocation_fields if strict else _verification_fields
    fields = {k: choose(e, cols) for k, e in expectations.items()}
    out = {k: [] for k in expectations}
    used = sorted({f for fs in fields.values() for f in fs})
    if not used:
        return out
    columns = sorted({_MATCH_FIELDS[f][0] for f in used})
    cells = _read_identity_cells(worksheet, {c: cols[c] for c in columns})
    all_rows = sorted({row for c in columns for row in cells[c]})
    normalized = {f: {row: _MATCH_FIELDS[f][1](cells[_MATCH_FIELDS[f][0]].get(row, "")) for row in all_rows}
                  for f in used}
    for k, e in expectations.items():
        if not fields[k]:
            continue
        want = {f: _MATCH_FIELDS[f][1](e.get(_MATCH_FIELDS[f][2]) or "") for f in fields[k]}
        out[k] = [row for row in all_rows if all(normalized[f][row] == want[f] for f in fields[k])]
    return out


def find_identity_conflicts(worksheet, expectations, headers=None):
    """
    expectations: {row_number: {'barcode': ..., 'name': ...}}.
    يعيد {row_number: سبب} للصفوف التي لا يطابق عمود المفتاح فيها الهوية المخزنة.
    """
    records = {r: (r, e) for r, e in (expectations or {}).items() if e}
    return find_record_conflicts(worksheet, records, headers=headers)


# ---------------------------------------------------------------------------
# طابور الكتابة في MariaDB (outbox)
# ---------------------------------------------------------------------------

_seq_lock = threading.Lock()
_last_seq = 0


def _next_seq(floor=0):
    """
    تسلسل الكتابة: وقت الجدولة بالنانوثانية، متزايد تماماً داخل العملية ولا يقل عن floor. الكتابات من كل العمليات
    والقنوات تُرتب به، والمُفرِّغ لا يرسل قيمة seq أصغر من قيمة كُتبت بعدها لنفس الخلية ونفس المنتج.
    """
    global _last_seq
    with _seq_lock:
        _last_seq = max(time.time_ns(), _last_seq + 1, int(floor or 0))
        return _last_seq


def _db_connect():
    # اتصال واحد لكل الموديولات بمهل اتصال وقراءة وكتابة (db_connect)
    return db_connect.connect()


class SQLiteTransactionQueue:
    """طابور الكتابة (outbox) في جدول sheet_updates على MariaDB (الاسم تاريخي)."""

    def __init__(self, db_path=None):
        self._setup_schema()

    def _connect(self):
        return _db_connect()

    def _setup_schema(self):
        conn = self._connect()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS sheet_updates (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    `row_number` INT NOT NULL,
                    `col_index` INT NOT NULL,
                    `value` TEXT NOT NULL,
                    registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    sync_status VARCHAR(255) DEFAULT 'PENDING'
                ) ENGINE=InnoDB CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
            """)
            for stmt in (
                "ALTER TABLE sheet_updates ADD COLUMN IF NOT EXISTS col_name VARCHAR(255) NULL",
                "ALTER TABLE sheet_updates ADD COLUMN IF NOT EXISTS key_barcode VARCHAR(255) NULL",
                "ALTER TABLE sheet_updates ADD COLUMN IF NOT EXISTS key_name VARCHAR(512) NULL",
                "ALTER TABLE sheet_updates ADD COLUMN IF NOT EXISTS key_size VARCHAR(255) NULL",
                "ALTER TABLE sheet_updates ADD COLUMN IF NOT EXISTS key_brand VARCHAR(255) NULL",
                "ALTER TABLE sheet_updates ADD COLUMN IF NOT EXISTS attempts INT NOT NULL DEFAULT 0",
                "ALTER TABLE sheet_updates ADD COLUMN IF NOT EXISTS last_error TEXT NULL",
                # المفتاح المنطقي للعمود، تسلسل الكتابة، بصمة الهوية، الصف عند الجدولة بعد النقل، موعد إعادة المحاولة
                "ALTER TABLE sheet_updates ADD COLUMN IF NOT EXISTS col_key VARCHAR(64) NULL",
                "ALTER TABLE sheet_updates ADD COLUMN IF NOT EXISTS seq BIGINT NULL",
                "ALTER TABLE sheet_updates ADD COLUMN IF NOT EXISTS ident CHAR(40) NULL",
                "ALTER TABLE sheet_updates ADD COLUMN IF NOT EXISTS relocated_from INT NULL",
                "ALTER TABLE sheet_updates ADD COLUMN IF NOT EXISTS next_attempt_at BIGINT NULL",
                # «بدّل بس إذا»: الكتابة بتنكتب بس إذا الخلية لسا فيها هالقيمة (نفس الأصل بأي شكل تسليم): إعادة القص
                # (recut.py) ما بتكتب أبداً فوق رابط غيّره المالك بإيده من بعد ما كتبناه
                "ALTER TABLE sheet_updates ADD COLUMN IF NOT EXISTS expect_value TEXT NULL",
                "ALTER TABLE sheet_updates ADD INDEX IF NOT EXISTS idx_sheet_updates_status (sync_status)",
                "ALTER TABLE sheet_updates ADD INDEX IF NOT EXISTS idx_sheet_updates_ident (ident)",
                "ALTER TABLE sheet_updates ADD INDEX IF NOT EXISTS idx_sheet_updates_row (`row_number`)",
                "ALTER TABLE sheet_updates ADD INDEX IF NOT EXISTS idx_sheet_updates_seq (seq)",
            ):
                cursor.execute(stmt)
            conn.commit()
        finally:
            conn.close()

    def append_update(self, row_number, col_index, value, col_name=None, key_barcode=None, key_name=None,
                      key_size=None, key_brand=None, col_key=None, seq=None, expect_value=None):
        """
        جدولة كتابة خلية. col_key: المفتاح المنطقي للعمود ('link' أو 'meta:<key>') ويُحدد عموده وقت الكتابة؛
        col_index/col_name للكتابات القديمة فقط. يعيد معرّف الصف.
        expect_value: الكتابة بتنكتب بس إذا الخلية وقت التفريغ لسا فيها هالقيمة (رابط تسليم إلنا لنفس الأصل بأي شكل
        بيحسب نفسه)، وإلا CONFLICT (cell_changed) بلا كتابة.
        seq: تسلسل كتابة نُقلت من Redis (بوقت جدولتها هناك)؛ نقلها مرة أخرى (نقل جزئي أو حمولة تغيرت أثناء النقل)
        لا يكرر الصف. دونه يُولَّد الآن ولا يقل عن أكبر seq في الطابور + 1: الترتيب بين العمليات يبقى صحيحاً حتى
        لو رجعت ساعة الجهاز أو وقعت كتابتان في نفس نبضة الساعة.
        """
        expect = _expectation(key_barcode, key_name, key_size, key_brand)
        ident = _identity_hash(expect)
        conn = self._connect()
        try:
            cursor = conn.cursor()
            if seq:
                cursor.execute(
                    "SELECT id FROM sheet_updates WHERE (`row_number` = %s OR relocated_from = %s) AND col_key <=> %s "
                    "AND seq = %s AND ident <=> %s ORDER BY id LIMIT 1",
                    (row_number, row_number, col_key, int(seq), ident))
                existing = cursor.fetchone()
                if existing:
                    return existing["id"]
            else:
                cursor.execute("SELECT MAX(seq) AS top FROM sheet_updates")
                seq = _next_seq(floor=int((cursor.fetchone() or {}).get("top") or 0) + 1)
            cursor.execute(
                "INSERT INTO sheet_updates (`row_number`, `col_index`, `value`, col_name, col_key, seq, ident, "
                "expect_value, key_barcode, key_name, key_size, key_brand) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (row_number, -1 if col_index is None else col_index, "" if value is None else str(value),
                 col_name, col_key, int(seq), ident, None if expect_value is None else str(expect_value),
                 key_barcode, key_name, key_size, key_brand)
            )
            new_id = getattr(cursor, "lastrowid", None)
            conn.commit()
        finally:
            conn.close()
        clear_cache()
        return new_id


def _outbox_lock_name():
    """اسم قفل المُفرِّغ؛ يتضمن اسم قاعدة البيانات لأن أقفال MariaDB على مستوى الخادم كله."""
    return f"sheet_outbox_flush:{os.getenv('DB_DATABASE', 'automation_db')}"[:64]


def _set_status(cursor, ids, status, error=None):
    if not ids:
        return
    placeholders = ",".join("%s" for _ in ids)
    cursor.execute(
        f"UPDATE sheet_updates SET sync_status = %s, last_error = %s WHERE id IN ({placeholders})",
        (status, error) + tuple(ids),
    )


# ---------------------------------------------------------------------------
# نتائج الكتابات: لا تنتهي أي كتابة بصمت
# ---------------------------------------------------------------------------

# الحالات التي تنتهي فيها الكتابة دون أن تصل للشيت؛ كل منها يُبلَّغ عبر outcome hook
UNWRITTEN_FINAL_STATUSES = ("CONFLICT", "DEAD", "SKIPPED_OUT_OF_BOUNDS")
_outcome_hook = None


def set_outcome_hook(hook):
    """
    hook(outcome) يُستدعى لكل كتابة انتهت دون أن تُكتب (CONFLICT / DEAD / SKIPPED_OUT_OF_BOUNDS).
    outcome: {id, row, queued_row, column_key, status, error, attempts, value, barcode, product_name, brand}.
    None يعيد الافتراضي: سطر خطأ في السجل وفي سجل لارافيل (صفحة الأخطاء). أخطاء الـ hook لا تُرفع أبداً.
    """
    global _outcome_hook
    _outcome_hook = hook


def _default_outcome_hook(outcome):
    message = _one_line(
        f"[Sheets Outbox] {outcome.get('status')}: الكتابة {outcome.get('id')} للصف {outcome.get('row')} "
        f"(العمود {outcome.get('column_key')}) لم تُكتب في الشيت: {outcome.get('error')}")
    logger.error("%s", message)
    log = getattr(config, "log_error_to_laravel", None)
    if callable(log):
        # قيم خلايا الشيت تُمرر سطراً واحداً: خلية فيها سطر جديد لا تزوّر أسطر سجل
        fields = {k: _one_line(outcome[k]) if outcome.get(k) else None for k in ("barcode", "product_name", "brand")}
        try:
            log(message, level="ERROR", **fields)
        except Exception:
            pass


def _report_outcome(outcome):
    """يبلّغ نتيجة كتابة لم تُكتب؛ لا يرفع أي خطأ أبداً."""
    try:
        (_outcome_hook or _default_outcome_hook)(dict(outcome))
    except Exception as e:
        logger.warning("[Sheets Outbox] تعذر إبلاغ نتيجة الكتابة %s: %s", outcome.get("id"), e)


def _column_key_of(r):
    return (r.get("col_key") or _col_key_for_header(r.get("col_name")) or r.get("col_name")
            or str(r.get("col_index")))


def _outcome(r, status, error):
    return {"id": r.get("id"), "row": r.get("row_number"),
            "queued_row": r.get("relocated_from") or r.get("row_number"), "column_key": _column_key_of(r),
            "status": status, "error": error, "attempts": int(r.get("attempts") or 0), "value": r.get("value"),
            "barcode": r.get("key_barcode"), "product_name": r.get("key_name"), "brand": r.get("key_brand")}


def outbox_outcomes(row_numbers=None, since_id=None, limit=500):
    """
    نتائج الكتابات المجدولة كما في الطابور، بترتيب المعرّف: [{id, row, queued_row, column_key, status, error,
    attempts, next_attempt_at, value}]. row: الصف الذي كُتبت (أو ستُكتب) فيه؛ queued_row: الصف عند الجدولة.
    row_numbers: صفوف محددة (الحالي أو عند الجدولة). since_id: ما بعد معرّف معين (للمتابعة التدريجية)؛ دونه
    تُعاد آخر limit نتيجة. الحالات: PENDING، FAILED (تنتظر next_attempt_at)، SYNCED، SUPERSEDED، CONFLICT،
    DEAD، SKIPPED_OUT_OF_BOUNDS.
    """
    where, params = [], []
    if row_numbers:
        rows = sorted({int(r) for r in row_numbers})
        marks = ",".join("%s" for _ in rows)
        where.append(f"(`row_number` IN ({marks}) OR relocated_from IN ({marks}))")
        params += rows + rows
    if since_id is not None:
        where.append("id > %s")
        params.append(int(since_id))
    sql = ("SELECT id, `row_number`, relocated_from, `col_index`, col_name, col_key, sync_status, last_error, "
           "attempts, next_attempt_at, `value` FROM sheet_updates")
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id " + ("ASC" if since_id is not None else "DESC") + " LIMIT %s"
    params.append(int(limit))
    conn = (_queue._connect if _queue is not None else _db_connect)()
    try:
        cursor = conn.cursor()
        cursor.execute(sql, tuple(params))
        found = sorted(cursor.fetchall(), key=lambda r: r["id"])
    finally:
        conn.close()
    return [{"id": r["id"], "row": r["row_number"], "queued_row": r.get("relocated_from") or r["row_number"],
             "column_key": _column_key_of(r), "status": r.get("sync_status"), "error": r.get("last_error"),
             "attempts": int(r.get("attempts") or 0), "next_attempt_at": r.get("next_attempt_at"),
             "value": r.get("value")} for r in found]


def outbox_summary(since_ts=None):
    """
    عدد الكتابات المجدولة حسب نتيجتها منذ since_ts (ثوانٍ unix؛ دونه كل ما في الطابور):
    {pending (PENDING و FAILED التي تنتظر إعادة المحاولة), conflict (CONFLICT و SKIPPED_OUT_OF_BOUNDS), dead, written}.
    لتقرير التشغيل (run_report): ما لم يُكتب من هذا التشغيل، لا تراكم الليالي السابقة.
    """
    sql = "SELECT sync_status, COUNT(*) AS n FROM sheet_updates"
    params = ()
    if since_ts is not None:
        sql += " WHERE registered_at >= FROM_UNIXTIME(%s)"
        params = (int(since_ts),)
    sql += " GROUP BY sync_status"
    conn = (_queue._connect if _queue is not None else _db_connect)()
    try:
        cursor = conn.cursor()
        cursor.execute(sql, params)
        found = cursor.fetchall()
    finally:
        conn.close()
    out = {"pending": 0, "conflict": 0, "dead": 0, "written": 0}
    bucket = {"PENDING": "pending", "FAILED": "pending", "CONFLICT": "conflict",
              "SKIPPED_OUT_OF_BOUNDS": "conflict", "DEAD": "dead", "SYNCED": "written"}
    for r in found:
        key = bucket.get(str(r.get("sync_status") or "").upper())
        if key:
            out[key] += int(r.get("n") or 0)
    return out


def outbox_due_count(now=None):
    """
    كم كتابة تنتظر التفريغ الآن: PENDING، و FAILED حلّ موعد إعادة محاولتها (نفس شرط _flush_locked). صفر = لا داعي
    لفتح الشيت: مؤقت التفريغ بين التشغيلات (laqta-outbox-flush.timer) يتخطى Google حينها فلا يستهلك حصته.
    أخطاء قاعدة البيانات تُرفع.
    """
    conn = (_queue._connect if _queue is not None else _db_connect)()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) AS n FROM sheet_updates WHERE sync_status = 'PENDING' "
            "OR (sync_status = 'FAILED' AND (next_attempt_at IS NULL OR next_attempt_at <= %s))",
            (int(_now() if now is None else now),))
        return int((cursor.fetchone() or {}).get("n") or 0)
    finally:
        conn.close()


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
        worksheet = None
        while not self._exit_signal.is_set():
            try:
                if worksheet is None:
                    client = get_sheets_client()
                    if client:
                        worksheet = open_worksheet(client, self.spreadsheet_name)
                if worksheet:
                    self._synchronize_pending_records(worksheet)
            except Exception as e:
                logger.warning("[GoogleSheetsBatchWorker] %s", e)
                worksheet = None
            self._exit_signal.wait(self.sync_interval)
        if worksheet:
            try:
                # التفريغ الأخير ينتظر القفل قليلاً كي لا تبقى كتابات هذه العملية معلقة بلا مُفرِّغ
                self._synchronize_pending_records(worksheet, lock_timeout=30)
            except Exception as e:
                logger.warning("[GoogleSheetsBatchWorker] فشل التفريغ الأخير: %s", e)

    def _synchronize_pending_records(self, worksheet, lock_timeout=0):
        """
        تفريغ طابور الكتابة:
        0. قفل MariaDB مسمى (GET_LOCK) يجعل المُفرِّغ واحداً في كل لحظة عبر كل العمليات (العامل وكل استدعاء
           cli_bridge و sync_worker)؛ وإلا قد يرسل مُفرِّغ متأخر قيمة قديمة بعد أن كتب آخر القيمة الأحدث فيمحوها.
           من لم يحصل على القفل يتخطى هذه الدورة (الصفوف تبقى PENDING لمن يملكه).
        1. قراءة الصفوف المستحقة (PENDING، و FAILED حلّ موعد إعادة محاولتها) ودمج التكرارات على نفس الخلية
           ولنفس الهوية فقط (الأحدث بالتسلسل seq يفوز)؛ سجلات بهويات مختلفة تُفحص كل منها على حدة.
        2. تحديد عمود الهدف بالمفتاح المنطقي من العناوين الآن، وتحديث عدد صفوف الورقة قبل الحكم على صف بأنه خارجها.
        3. التحقق من هوية كل سجل بقراءة أعمدة الهوية؛ عند CONFLICT (أو صف خارج الورقة) تُقرأ أعمدة الهوية للورقة
           كلها مرة واحدة وتُنقل الكتابة إلى الصف الوحيد الذي يطابق المنتج، وإلا تبقى CONFLICT.
        4. لا تُرسل قيمة أقدم (seq أصغر) من قيمة كُتبت بعدها لنفس العمود ونفس المنتج (SUPERSEDED).
        5. إرسال دفعة واحدة؛ عند فشل مؤقت (429/5xx/انقطاع) تنتظر كل الكتابات موعد إعادة محاولتها، وعند فشل آخر
           نعيد المحاولة صفاً صفاً كي لا يوقف صف معطوب البقية. كل فشل يزيد attempts ويحدد next_attempt_at
           (1 د، 5 د، 30 د، 2 س)، وبعد 5 محاولات تصبح الحالة DEAD.
        كل CONFLICT / DEAD / SKIPPED_OUT_OF_BOUNDS يُبلَّغ عبر outcome hook.
        """
        conn = self.queue._connect()
        lock_name = _outbox_lock_name()
        locked = False
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT GET_LOCK(%s, %s) AS got", (lock_name, int(lock_timeout)))
            locked = bool((cursor.fetchone() or {}).get("got"))
            if not locked:
                logger.debug("[Sheets Outbox] مُفرِّغ آخر يعمل الآن؛ تخطي هذه الدورة.")
                return
            self._flush_locked(worksheet, conn, cursor)
        finally:
            if locked:
                try:
                    conn.cursor().execute("SELECT RELEASE_LOCK(%s) AS released", (lock_name,))
                except Exception:
                    pass
            conn.close()

    def _flush_locked(self, worksheet, conn, cursor):
        """Body of one flush; the caller holds the outbox lock and closes the connection."""
        now = _now()
        cursor.execute(
            "SELECT id, `row_number`, `col_index`, `value`, col_name, col_key, seq, ident, relocated_from, "
            "key_barcode, key_name, key_size, key_brand, attempts, expect_value "
            "FROM sheet_updates WHERE sync_status = 'PENDING' "
            "OR (sync_status = 'FAILED' AND (next_attempt_at IS NULL OR next_attempt_at <= %s)) "
            "ORDER BY id LIMIT %s",
            (int(now), OUTBOX_BATCH),
        )
        rows = list(cursor.fetchall())
        if not rows:
            return
        for r in rows:
            r["expect"] = _expectation(r.get("key_barcode"), r.get("key_name"), r.get("key_size"),
                                       r.get("key_brand"))
            r["ident"] = r.get("ident") or _identity_hash(r["expect"])

        def order(r):
            return int(r.get("seq") or 0), r["id"]

        # 1. الأحدث لكل خلية (صف، عمود) ولنفس الهوية المتوقعة يفوز؛ الأقدم يصبح SUPERSEDED.
        #    كتابة لمنتج آخر على نفس رقم الصف (رقم صف قديم) لا تُلغي كتابة المنتج الصحيح ولا العكس.
        latest = {}
        for r in rows:
            cell_key = (r["row_number"], r.get("col_key") or normalize_header(r.get("col_name")) or r["col_index"],
                        _identity_key(r["expect"]))
            if cell_key not in latest or order(r) > order(latest[cell_key]):
                latest[cell_key] = r
        keep_ids = {r["id"] for r in latest.values()}
        _set_status(cursor, [r["id"] for r in rows if r["id"] not in keep_ids], "SUPERSEDED")
        rows = sorted(latest.values(), key=order)

        # 2. العمود بالمفتاح المنطقي الآن + حدود الورقة (row_count يُحدَّث قبل الحكم بأن صفاً خارجها)
        headers = _retrying(worksheet.row_values, 1)
        normalized_headers = [normalize_header(h) for h in headers]
        cached_rows = worksheet.row_count
        max_rows = cached_rows
        if any(r["row_number"] > cached_rows for r in rows):
            max_rows = _fresh_row_count(worksheet)
        targets, conflicts, out_of_bounds, beyond = [], {}, {}, set()
        for r in rows:
            if r.get("col_key"):
                col = resolve_column_key(headers, r["col_key"])
                if col == -1:
                    conflicts[r["id"]] = f"column '{r['col_key']}' not found in sheet headers"
                    continue
            elif r.get("col_name"):
                wanted = normalize_header(r["col_name"])
                if wanted not in normalized_headers:
                    conflicts[r["id"]] = f"column '{r['col_name']}' not found in sheet headers"
                    continue
                col = normalized_headers.index(wanted)
            else:
                col = r["col_index"]
            if r["row_number"] <= 1 or col < 0:
                out_of_bounds[r["id"]] = f"row {r['row_number']} / column index {col} is not a data cell"
                continue
            if r["row_number"] > cached_rows:
                if max_rows is None:
                    continue          # تعذر تحديث عدد الصفوف: تبقى PENDING للدورة التالية ولا يُحكم عليها
                if r["row_number"] > max_rows:
                    beyond.add(r["id"])
            targets.append((r, col))

        # 3. التحقق من الهوية؛ عند CONFLICT (أو صف خارج الورقة) نقل مبدئي إلى الصف الوحيد الذي يطابق المنتج مطابقة
        #    كاملة (find_identity_rows الصارمة). النقل يُحفظ فقط بعد فحص الترتيب وخلية الهدف (الخطوة 5).
        records = {r["id"]: (r["row_number"], r["expect"]) for r, _ in targets
                   if r["expect"] and r["id"] not in beyond}
        record_conflicts = find_record_conflicts(worksheet, records, headers=headers) if records else {}
        lost = {r["id"]: r["expect"] for r, _ in targets
                if r["expect"] and (r["id"] in record_conflicts or r["id"] in beyond)}
        found = find_identity_rows(worksheet, lost, headers=headers) if lost else {}
        ready, moved = [], {}
        for r, col in targets:
            if r["id"] not in record_conflicts and r["id"] not in beyond:
                ready.append((r, col))
                continue
            matches = found.get(r["id"]) or []
            if len(matches) == 1:
                moved[r["id"]] = (r["row_number"], r.get("relocated_from"))
                r["relocated_from"] = r.get("relocated_from") or r["row_number"]
                r["row_number"] = matches[0]
                ready.append((r, col))
                continue
            where = "no row matches" if not matches else f"{len(matches)} rows match"
            if r["id"] in beyond:
                out_of_bounds[r["id"]] = (f"row {r['row_number']} is outside the sheet ({max_rows} rows); "
                                          f"{where} this product")
            else:
                conflicts[r["id"]] = f"{record_conflicts[r['id']]}; {where} this product"

        # 4. بعد التحقق: خلية واحدة تُكتب مرة واحدة فقط (الأحدث)، ولا تُرسل قيمة أقدم مما كُتب بعدها
        newest = {}
        for r, col in ready:
            if (r["row_number"], col) not in newest or order(r) > order(newest[(r["row_number"], col)][0]):
                newest[(r["row_number"], col)] = (r, col)
        _set_status(cursor, [r["id"] for r, col in ready if newest[(r["row_number"], col)][0] is not r],
                    "SUPERSEDED")
        ready = sorted(newest.values(), key=lambda item: order(item[0]))
        stale = _older_than_written(cursor, worksheet, headers, ready)
        _set_status(cursor, sorted(stale), "SUPERSEDED", "a newer value was already written to this cell")
        ready = [(r, col) for r, col in ready if r["id"] not in stale]

        # 5. النقل لا يكتب أبداً فوق قيمة مختلفة: خلية الهدف في صف المنتج الجديد فارغة أو تحمل نفس القيمة، وإلا CONFLICT
        relocated = [(r, col) for r, col in ready if r["id"] in moved]
        if relocated:
            held = _read_cells(worksheet, [(r["row_number"], col) for r, col in relocated])
            for r, col in relocated:
                current = held.get((r["row_number"], col), "")
                replaces = r.get("expect_value") is not None and _cell_holds(current, r["expect_value"])
                if current and current != str(r["value"]).strip() and not replaces:
                    conflicts[r["id"]] = (f"the product moved from row {moved[r['id']][0]} to row {r['row_number']}, "
                                          f"whose cell already holds a different value ({current[:200]!r}); not moved")
                    r["row_number"], r["relocated_from"] = moved[r["id"]]
                else:
                    self._relocate(cursor, r, moved[r["id"]][0])
            ready = [(r, col) for r, col in ready if r["id"] not in conflicts]

        # 6. عمود يُملأ فقط (الباركود): خلية الهدف تُقرأ الآن، وأي قيمة فيها لا يُكتب فوقها (CONFLICT). نفس القيمة
        #    مكتوبة أصلاً: SYNCED بلا إرسال
        fill = [(r, col) for r, col in ready if r.get("col_key") in FILL_ONLY_KEYS]
        if fill:
            held = _read_cells(worksheet, [(r["row_number"], col) for r, col in fill])
            already = []
            for r, col in fill:
                current = held.get((r["row_number"], col), "")
                if current and current == str(r["value"]).strip():
                    already.append(r["id"])
                elif current:
                    conflicts[r["id"]] = (f"cell_not_empty: the {r['col_key']} cell of row {r['row_number']} already "
                                          f"holds {current[:60]!r}; not overwritten")
            _set_status(cursor, already, "SYNCED")
            ready = [(r, col) for r, col in ready if r["id"] not in conflicts and r["id"] not in already]

        # 6ب. «بدّل بس إذا» (expect_value، إعادة القص recut.py): خلية الهدف تُقرأ الآن؛ بتنكتب بس إذا لسا فيها القيمة
        #     اللي منبدّلها (نفس الأصل بأي شكل تسليم). المالك غيّرها بإيده: CONFLICT بلا كتابة. القيمة الجديدة نفسها: SYNCED
        swap = [(r, col) for r, col in ready if r.get("expect_value") is not None]
        if swap:
            held = _read_cells(worksheet, [(r["row_number"], col) for r, col in swap])
            already = []
            for r, col in swap:
                current = held.get((r["row_number"], col), "")
                if current == str(r["value"]).strip():
                    already.append(r["id"])
                elif not _cell_holds(current, r["expect_value"]):
                    conflicts[r["id"]] = (f"cell_changed: the {r.get('col_key') or 'target'} cell of row "
                                          f"{r['row_number']} now holds {current[:200]!r}, not the value this write "
                                          "replaces; not overwritten")
            _set_status(cursor, already, "SYNCED")
            ready = [(r, col) for r, col in ready if r["id"] not in conflicts and r["id"] not in already]

        unwritten = []
        by_id = {r["id"]: r for r in rows}
        for rid, reason in conflicts.items():
            _set_status(cursor, [rid], "CONFLICT", reason[:1000])
            logger.warning("[Sheets Outbox] CONFLICT للتحديث %s: %s", rid, reason)
            unwritten.append(_outcome(by_id[rid], "CONFLICT", reason))
        for rid, reason in out_of_bounds.items():
            _set_status(cursor, [rid], "SKIPPED_OUT_OF_BOUNDS", reason[:1000])
            unwritten.append(_outcome(by_id[rid], "SKIPPED_OUT_OF_BOUNDS", reason))
        conn.commit()
        for outcome in unwritten:
            _report_outcome(outcome)
        if not ready:
            return

        def body_for(items):
            return {
                "valueInputOption": "RAW",
                "data": [{
                    "range": f"'{worksheet.title}'!{gspread.utils.rowcol_to_a1(r['row_number'], col + 1)}",
                    "values": [[str(r['value'])]],
                } for r, col in items],
            }

        send = retry_gspread_on_429(max_retries=5)(worksheet.spreadsheet.values_batch_update)
        try:
            send(body_for(ready))
            _set_status(cursor, [r["id"] for r, _ in ready], "SYNCED")
            conn.commit()
            logger.info("[Sheets Outbox] تمت مزامنة %s خلية.", len(ready))
            return
        except Exception as e:
            if _is_transient(e):
                # الخدمة نفسها غير متاحة: الإرسال صفاً صفاً لن ينجح، وكل كتابة تنتظر موعد إعادة محاولتها
                logger.warning("[Sheets Outbox] Google Sheets غير متاح الآن (%s)؛ إعادة المحاولة لاحقاً.", e)
                self._record_failures(conn, cursor, [r for r, _ in ready], e, now)
                return
            logger.warning("[Sheets Outbox] فشل الإرسال الجماعي (%s)؛ إعادة المحاولة صفاً صفاً.", e)

        send_one = retry_gspread_on_429(max_retries=2)(worksheet.spreadsheet.values_batch_update)
        for r, col in ready:
            try:
                send_one(body_for([(r, col)]))
                _set_status(cursor, [r["id"]], "SYNCED")
                conn.commit()
            except Exception as e:
                self._record_failures(conn, cursor, [r], e, now)
        clear_cache()

    @staticmethod
    def _relocate(cursor, r, old_row):
        """
        حفظ نقل كتابة إلى صف المنتج الحالي (r['row_number']؛ أُدرج أو حُذف صف فوقه). relocated_from يحفظ الصف
        عند الجدولة.
        """
        cursor.execute("UPDATE sheet_updates SET `row_number` = %s, relocated_from = %s WHERE id = %s",
                       (r["row_number"], r["relocated_from"], r["id"]))
        logger.warning("[Sheets Outbox] الكتابة %s: المنتج لم يعد في الصف %s؛ نُقلت إلى الصف %s (الصف الوحيد المطابق).",
                       r["id"], old_row, r["row_number"])

    @staticmethod
    def _record_failures(conn, cursor, items, error, now):
        """
        فشل إرسال: attempts+1 وموعد إعادة المحاولة التالي (RETRY_BACKOFF) عبر التشغيلات؛ عند MAX_OUTBOX_ATTEMPTS
        تصبح DEAD وتُبلَّغ.
        """
        dead = []
        for r in items:
            attempts = int(r.get("attempts") or 0) + 1
            if attempts >= MAX_OUTBOX_ATTEMPTS:
                status, due = "DEAD", None
            else:
                status, due = "FAILED", int(now + RETRY_BACKOFF[attempts - 1])
            cursor.execute(
                "UPDATE sheet_updates SET attempts = %s, last_error = %s, sync_status = %s, next_attempt_at = %s "
                "WHERE id = %s",
                (attempts, str(error)[:1000], status, due, r["id"]),
            )
            logger.warning("[Sheets Outbox] فشل التحديث %s (المحاولة %s، الحالة %s): %s",
                           r["id"], attempts, status, error)
            if status == "DEAD":
                dead.append(_outcome(dict(r, attempts=attempts), "DEAD", str(error)[:1000]))
        conn.commit()
        for outcome in dead:
            _report_outcome(outcome)


def _cell_holds(current, expected):
    """
    هل خلية الشيت لسا فيها القيمة المتوقعة (expect_value)؟ نفس النص، أو رابطا تسليم إلنا لنفس الأصل والنسخة (القديم
    q_auto,f_auto والجديد: delivery_urls.same_delivery_asset). خلية مراجعة معلقة (needs_review:) بتساوي حالها بس.
    """
    cur, exp = str(current or "").strip(), str(expected or "").strip()
    if cur == exp:
        return True
    from delivery_urls import REVIEW_PREFIX, same_delivery_asset
    if not cur or not exp or cur.startswith(REVIEW_PREFIX) or exp.startswith(REVIEW_PREFIX):
        return False
    return same_delivery_asset(cur, exp)


def _read_cells(worksheet, cells):
    """قيم خلايا محددة [(row, col 0-based)] عبر batch_get واحد: {(row, col): القيمة}."""
    cells = list(dict.fromkeys(cells))
    ranges = [f"{gspread.utils.rowcol_to_a1(row, col + 1)}:{gspread.utils.rowcol_to_a1(row, col + 1)}"
              for row, col in cells]
    result = _retrying(worksheet.batch_get, ranges) if ranges else []
    out = {}
    for i, cell in enumerate(cells):
        values = list(result[i]) if result and i < len(result) else []
        out[cell] = str(values[0][0]).strip() if values and values[0] else ""
    return out


def _older_than_written(cursor, worksheet, headers, items):
    """
    معرّفات الكتابات التي كُتبت بعدها قيمة أحدث (seq أكبر) لنفس العمود؛ لا تُرسل أبداً، كي لا تمحو قيمةٌ قديمة
    قيمةً أحدث (بين العمليات، وحمولات Redis المنقولة متأخرة، وإعادة المحاولة المؤجلة). الكتابة قديمة إذا:
    - كُتبت بعدها قيمة لنفس المنتج (نفس بصمة الهوية) في أي صف: الصف ينجرف مع كل إدراج أو حذف. الاستثناء: منتج له
      الآن أكثر من صف (صفوف مكررة)؛ عندها فقط إذا كانت في نفس الصف (الحالي أو عند الجدولة)، فلا يلغي صف مكرر
      كتابة الصف الآخر.
    - أو كُتبت بعدها قيمة في نفس خلية الهدف بهوية بصيغة أخرى (مثلاً أُضيف الباركود بعد الجدولة)، ما دام منتج
      تلك الكتابة الأحدث ما زال في هذا الصف (وإلا فالقيمة الأحدث انتقلت مع صفها).
    """
    keyed = []
    for r, _ in items:
        col_key = r.get("col_key") or _col_key_for_header(r.get("col_name"))
        if col_key:
            keyed.append((r, col_key))
    if not keyed:
        return set()
    idents = sorted({r["ident"] for r, _ in keyed if r.get("ident")})
    rows = sorted({r["row_number"] for r, _ in keyed})
    clauses = ["`row_number` IN (" + ",".join("%s" for _ in rows) + ")"]
    params = [min(int(r.get("seq") or 0) for r, _ in keyed)] + rows
    if idents:
        clauses.append("ident IN (" + ",".join("%s" for _ in idents) + ")")
        params += idents
    cursor.execute(
        "SELECT id, `row_number`, relocated_from, col_key, ident, seq, key_barcode, key_name, key_size, key_brand "
        "FROM sheet_updates WHERE sync_status = 'SYNCED' AND seq > %s AND (" + " OR ".join(clauses) + ")",
        tuple(params),
    )
    written = [w for w in cursor.fetchall() if w.get("seq") is not None and w.get("col_key")]
    stale, elsewhere, same_cell = set(), {}, {}
    for r, col_key in keyed:
        newer = [w for w in written if w["col_key"] == col_key and int(w["seq"]) > int(r.get("seq") or 0)]
        mine = [w for w in newer if r.get("ident") and w.get("ident") == r["ident"]]
        cells = {r["row_number"], r.get("relocated_from")} - {None}
        if any(cells & ({w["row_number"], w.get("relocated_from")} - {None}) for w in mine):
            stale.add(r["id"])
            continue
        if mine:
            elsewhere[r["id"]] = r
        others = [w for w in newer if w["row_number"] == r["row_number"] and w not in mine]
        if others:
            same_cell[r["id"]] = (r, others)
    # نفس المنتج في صف آخر: قديمة ما لم يكن للمنتج الآن أكثر من صف
    if elsewhere:
        found = find_identity_rows(worksheet, {k: r["expect"] for k, r in elsewhere.items()}, headers=headers,
                                   strict=False)
        stale.update(k for k in elsewhere if len(found.get(k) or []) <= 1)
    # نفس الخلية بهوية أخرى: قديمة ما دام منتج الكتابة الأحدث ما زال في هذا الصف
    records = {}
    for k, (r, others) in same_cell.items():
        if k in stale:
            continue
        for w in others:
            expect = _expectation(w.get("key_barcode"), w.get("key_name"), w.get("key_size"), w.get("key_brand"))
            if not expect:
                stale.add(k)
                break
            records[(k, w["id"])] = (r["row_number"], expect)
    records = {key: rec for key, rec in records.items() if key[0] not in stale}
    if records:
        conflicts = find_record_conflicts(worksheet, records, headers=headers)
        stale.update(key[0] for key in records if key not in conflicts)
    return stale


def flush_outbox(worksheet, queue=None, lock_timeout=0):
    """تفريغ واحد لطابور الكتابة من أي عملية (sync_worker أو سكربت): نفس القفل والقواعد مثل عامل الخلفية."""
    queue = queue or _queue or SQLiteTransactionQueue()
    GoogleSheetsBatchWorker(queue, None, None)._synchronize_pending_records(worksheet, lock_timeout=lock_timeout)


def init_async_queue(creds_path, spreadsheet_name, sync_interval=5):
    global _queue, _worker
    _queue = SQLiteTransactionQueue()
    _worker = GoogleSheetsBatchWorker(_queue, creds_path, spreadsheet_name, sync_interval)
    _worker.start()
    logger.info("[Sheets Outbox] بدأ عامل المزامنة في الخلفية.")


def stop_async_queue():
    global _worker
    if _worker:
        _worker.stop_gracefully()
        _worker.join(timeout=10)
        _worker = None
        logger.info("[Sheets Outbox] توقف عامل المزامنة.")


# ---------------------------------------------------------------------------
# الكتابة المؤجلة عبر Redis (فقط عند وجود نبض sync_worker)
# ---------------------------------------------------------------------------

def _get_redis():
    """عميل Redis أو None إذا لم يكن الخادم متاحاً (يُفحص مرة واحدة لكل عملية)."""
    global _redis_client, _redis_cache_available
    if _redis_cache_available is False:
        return None
    if _redis_client is not None:
        return _redis_client
    try:
        import redis
        client = redis.Redis(host=config.REDIS_HOST, port=config.REDIS_PORT, db=config.REDIS_DB,
                             socket_timeout=0.2, decode_responses=True)
        client.ping()
        _redis_client = client
        _redis_cache_available = True
        return client
    except Exception:
        _redis_cache_available = False
        logger.debug("[google_sheets] Redis غير متاح محلياً؛ الكتابة عبر طابور MariaDB.")
        return None


def _merged_payload(cached, row_number, updates, expect, seqs):
    """
    الحمولة بعد الدمج بالصيغة {'v': 2, 'row_index', 'updates': {col_key: value}, 'seqs': {col_key: seq},
    'expect'}، أو None إذا تعذر الدمج بأمان (صيغة غير معروفة أو قديمة بأرقام أعمدة، أو هوية مختلفة).
    """
    payload = json.loads(cached) if cached else {"v": REDIS_PAYLOAD_VERSION, "row_index": row_number,
                                                 "updates": {}, "seqs": {}}
    if (not isinstance(payload, dict) or payload.get("v") != REDIS_PAYLOAD_VERSION
            or not isinstance(payload.get("updates"), dict) or not isinstance(payload.get("seqs"), dict)):
        logger.warning("[Redis Write-Behind] صيغة غير معروفة للصف %s؛ استخدام طابور MariaDB.", row_number)
        return None
    if cached and (payload.get("expect") or None) != (expect or None):
        # حمولة معلقة لمنتج آخر على نفس رقم الصف: لا ندمج (وإلا كُتبت قيمها بهوية المنتج الجديد)
        logger.warning("[Redis Write-Behind] حمولة الصف %s تخص هوية أخرى؛ استخدام طابور MariaDB.", row_number)
        return None
    payload["row_index"] = row_number
    for col_key, value in updates.items():
        payload["updates"][str(col_key)] = value
        payload["seqs"][str(col_key)] = seqs[col_key]
    if expect:
        payload["expect"] = expect
    return json.dumps(payload, ensure_ascii=False)


def _redis_merge_payload(r, key, row_number, updates, expect, seqs):
    """دمج ذري (WATCH/MULTI) لتحديثات الصف في حمولته؛ يعيد False ليُستخدم طابور MariaDB."""
    cache_key = f"{CACHE_PREFIX}{key}"
    if getattr(r, "pipeline", None) is None:
        merged = _merged_payload(r.get(cache_key), row_number, updates, expect, seqs)
        if merged is None:
            return False
        r.set(cache_key, merged)
        return True
    import redis
    for _ in range(5):
        with r.pipeline() as p:
            try:
                p.watch(cache_key)
                merged = _merged_payload(p.get(cache_key), row_number, updates, expect, seqs)
                if merged is None:
                    p.unwatch()
                    return False
                p.multi()
                p.set(cache_key, merged)
                p.execute()
                return True
            except redis.WatchError:
                continue
    return False


def _redis_write_behind(row_number, updates, expect=None):
    """
    جدولة تحديثات صف عبر Redis؛ updates: {مفتاح منطقي للعمود: قيمة} (لا أرقام أعمدة: العمود يُحدد وقت الكتابة).
    كل قيمة تحمل seq وقت جدولتها. sync_worker ينقل الحمولة إلى طابور MariaDB نفسه (قناة كتابة واحدة).
    تعيد False (ليُستخدم طابور MariaDB) إذا لم يكن Redis متاحاً أو لا يوجد نبض حي لـ sync_worker.
    """
    r = _get_redis()
    if r is None:
        return False
    try:
        if not r.exists(HEARTBEAT_KEY):
            return False
        key = f"row_{row_number}"
        seqs = {col_key: _next_seq() for col_key in updates}
        if not _redis_merge_payload(r, key, row_number, updates, expect, seqs):
            return False
        r.sadd(DIRTY_SET_KEY, key)
        return True
    except Exception as e:
        logger.warning("[Redis Write-Behind] %s؛ استخدام طابور MariaDB.", e)
        return False


@retry_gspread_on_429()
def update_image_link(worksheet, row_number, link_column_index, image_link, barcode=None, product_name=None,
                      size=None, brand=None):
    """
    تحديث خلية رابط الصورة لصف منتج. barcode/product_name/size/brand هوية المنتج المتوقع في هذا الصف
    (قيم خلايا الشيت نفسها): عند التفريغ يُعاد التحقق منها، وأي اختلاف يُسجل CONFLICT ولا يُكتب (أو تُنقل الكتابة
    إلى الصف الوحيد المطابق للمنتج).
    العمود يُحمل كمفتاح منطقي 'link' ويُحدد من العناوين وقت الكتابة: إدراج عمود أو حذفه يسار عمود الرابط أثناء
    التشغيل لا يغيّر العمود المكتوب. link_column_index يبقى للتوافق مع المستدعين ولا يحدد العمود.
    رابط تسليم Cloudinary إلنا بالتحويل القديم (q_auto,f_auto: اعتماد محفوظ قبل f_webp، يكتبه relink أو الصفوف المكررة)
    بينكتب بالتحويل الجديد لنفس الأصل (delivery_urls.migrated_delivery_url): التطبيق ما بياخد JPEG بلا شفافية.
    الأخطاء المؤقتة و APIError تُرفع كي يعمل مُزخرف إعادة المحاولة.
    """
    from delivery_urls import migrated_delivery_url

    image_link = migrated_delivery_url(image_link) or image_link
    expect = _expectation(barcode, product_name, size, brand)
    try:
        if _redis_write_behind(row_number, {LINK_KEY: image_link}, expect):
            logger.info("[Redis Write-Behind] جدولة رابط الصف %s.", row_number)
            return True

        if _queue is not None and _worker is not None:
            _queue.append_update(row_number, link_column_index, image_link, col_key=LINK_KEY, **_outbox_keys(expect))
            logger.info("[Sheets Outbox] جدولة رابط الصف %s.", row_number)
            return True

        headers = _worksheet_headers(worksheet, fresh=True)
        col = resolve_column_key(headers, LINK_KEY)
        if col == -1:
            logger.error("[Google Sheets] لا يوجد عمود رابط الصورة في الشيت؛ لم يُكتب رابط الصف %s.", row_number)
            return False
        if expect:
            conflicts = find_identity_conflicts(worksheet, {row_number: expect}, headers=headers)
            if row_number in conflicts:
                logger.warning("[Google Sheets] رفض الكتابة في الصف %s: %s", row_number, conflicts[row_number])
                return False
        worksheet.update_cell(row_number, col + 1, image_link)
        clear_cache()
        return True
    except Exception as e:
        if isinstance(e, APIError) or _is_transient(e):
            raise
        logger.error("فشل تحديث الرابط في الصف %s: %s", row_number, e)
        return False


def queue_link_writes(items):
    """
    يجدول كتابة رابط صورة لكل صف في طابور MariaDB (outbox) مباشرة، بلا Redis وبلا كتابة مباشرة في الشيت (ترحيل روابط
    التسليم: scripts/migrate_delivery_urls.py): items [{row_number, value, barcode, product_name, size, brand}]؛ الهوية
    كما في الشيت، والتفريغ يتخطى الصف الذي تغيّر منتجه (CONFLICT) أو كتابة أقدم من كتابة أحدث لنفس الخلية. يعيد
    {row: معرّف}.
    replace (اختياري لكل item): الرابط اللي لازم يكون لسا بالخلية وقت التفريغ (إعادة القص، recut.py): إذا المالك غيّره
    بإيده الكتابة بتصير CONFLICT ولا شي بينكتب (expect_value).
    """
    queue = _queue or SQLiteTransactionQueue()
    out = {}
    for item in items or ():
        expect = _expectation(item.get("barcode"), item.get("product_name"), item.get("size"), item.get("brand"))
        extra = {"expect_value": str(item["replace"])} if item.get("replace") is not None else {}
        out[int(item["row_number"])] = queue.append_update(int(item["row_number"]), None, str(item["value"]),
                                                           col_key=LINK_KEY, **_outbox_keys(expect), **extra)
    return out


def queue_barcode_writes(items):
    """
    يجدول كتابة باركود لكل صف في طابور MariaDB (outbox) مباشرة، بلا Redis وبلا كتابة مباشرة في الشيت: items
    [{row_number, value, barcode, product_name, size, brand}]؛ barcode/product_name/size/brand هوية الصف كما في
    الشيت (خلية الباركود الحالية فارغة أو غير صالحة، فالتحقق بالاسم والحجم والبراند كما في كتابات الصور). التفريغ
    يتخطى الصف الذي تغيّر منتجه (CONFLICT) ولا يكتب فوق خلية باركود غير فارغة (FILL_ONLY_KEYS). يعيد {row: معرّف}.
    """
    queue = _queue or SQLiteTransactionQueue()
    out = {}
    for item in items or ():
        expect = _expectation(item.get("barcode"), item.get("product_name"), item.get("size"), item.get("brand"))
        out[int(item["row_number"])] = queue.append_update(int(item["row_number"]), None, str(item["value"]),
                                                           col_key=BARCODE_KEY, **_outbox_keys(expect))
    return out


_METADATA_COLUMNS = {
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
    "tags_ar": "Tags AR",
}


@retry_gspread_on_429()
def update_product_metadata(worksheet, row_number, metadata, barcode=None, product_name=None,
                            size=None, brand=None):
    """
    تحديث أعمدة البيانات الوصفية لصف (ينشئ العناوين الناقصة). نفس قواعد الهوية والطابور مثل update_image_link؛
    كل عمود يُحمل كمفتاح منطقي 'meta:<key>' ويُحدد بعنوانه وقت الكتابة.
    """
    expect = _expectation(barcode, product_name, size, brand)
    try:
        headers = _worksheet_headers(worksheet, fresh=True)
        normalized = [normalize_header(h) for h in headers]
        col_indices = {}
        for key, name in _METADATA_COLUMNS.items():
            if not (metadata or {}).get(key):
                continue
            target = normalize_header(name)
            if target in normalized:
                col_indices[key] = normalized.index(target)
                continue
            new_idx = len(headers)
            if new_idx + 1 > worksheet.col_count:
                worksheet.add_cols(new_idx + 1 - worksheet.col_count)
            worksheet.update_cell(1, new_idx + 1, name)
            headers.append(name)
            normalized.append(target)
            col_indices[key] = new_idx
            logger.info("تم إنشاء عمود '%s' في العمود رقم %s", name, new_idx + 1)
        _worksheet_headers(worksheet, fresh=True)
        if not col_indices:
            return True

        if _redis_write_behind(row_number, {f"{META_PREFIX}{k}": str(metadata[k]) for k in col_indices}, expect):
            return True

        if _queue is not None and _worker is not None:
            for key, col in col_indices.items():
                _queue.append_update(row_number, col, str(metadata[key]), col_name=headers[col],
                                     col_key=f"{META_PREFIX}{key}", **_outbox_keys(expect))
            return True

        if expect:
            conflicts = find_identity_conflicts(worksheet, {row_number: expect}, headers=headers)
            if row_number in conflicts:
                logger.warning("[Google Sheets] رفض كتابة البيانات الوصفية في الصف %s: %s",
                               row_number, conflicts[row_number])
                return False
        worksheet.batch_update([
            {"range": gspread.utils.rowcol_to_a1(row_number, col + 1), "values": [[str(metadata[key])]]}
            for key, col in col_indices.items()
        ], value_input_option="RAW")
        return True
    except Exception as e:
        if isinstance(e, APIError) or _is_transient(e):
            raise
        logger.error("فشل تحديث البيانات الوصفية في الصف %s: %s", row_number, e)
        return False


# ---------------------------------------------------------------------------
# ورقة 'Brands Mapping'
# ---------------------------------------------------------------------------

_BRAND_SHEET_COLUMNS = {
    "brand": ["brand", "brand name", "البراند"],
    "synonyms": ["synonyms", "aliases", "المرادفات"],
    "competitors": ["excluded competitors", "competitors", "المنافسين"],
    "sub_brands": ["sub brands", "subbrands", "البراندات الفرعية"],
    "official_domains": ["official domains", "domains", "official domain"],
}


def _split_list(value):
    return [p.strip() for p in str(value or "").replace("،", ",").split(",") if p.strip()]


def parse_brand_mapping_rows(rows):
    """تحويل صفوف ورقة 'Brands Mapping' (مع صف العناوين) إلى قاموس المرادفات."""
    if not rows or len(rows) <= 1:
        return {}
    normalized = [normalize_header(h) for h in rows[0]]
    cols = {}
    for key, synonyms in _BRAND_SHEET_COLUMNS.items():
        cols[key] = next((normalized.index(normalize_header(s)) for s in synonyms
                          if normalize_header(s) in normalized), -1)
    if cols["brand"] == -1:
        # الورقة القديمة المنشأة تلقائياً بلا عناوين معروفة: الأعمدة الثلاثة الأولى فقط
        cols.update({"brand": 0, "synonyms": 1, "competitors": 2})
    mappings = {}
    for r in rows[1:]:
        brand = _cell(r, cols["brand"])
        if not brand:
            continue
        syns = _split_list(_cell(r, cols["synonyms"]))
        if brand not in syns:
            syns.insert(0, brand)
        mappings[brand.lower()] = {
            "brand": brand,
            "synonyms": syns,
            "excluded_competitors": _split_list(_cell(r, cols["competitors"])),
            "sub_brands": _split_list(_cell(r, cols["sub_brands"])),
            "official_domains": _split_list(_cell(r, cols["official_domains"])),
        }
    return mappings


def get_brand_mappings(client, sheet_name_or_url):
    """
    جلب مرادفات البراندات من ورقة 'Brands Mapping' (أعمدة بالعناوين: Brand, Synonyms,
    Excluded Competitors, Sub-brands, Official domains). تُنشأ الورقة بالقيم الافتراضية إن لم توجد.
    يُضاف إليها ما تعلّمه البحث من المراجعة (catalog_match/learning.py): كتابة المتاجر لماركة اعتمدها المراجع،
    والمواقع التي يتكرر اعتماد صور الماركة منها. ما في الشيت يبقى هو الأساس ولا يغيّره التعلّم.
    """
    return _with_learning(_sheet_brand_mappings(client, sheet_name_or_url))


def _with_learning(mappings):
    try:
        from catalog_match import learning
        return learning.load_and_apply(mappings)
    except Exception as e:  # التعلّم لا يعطل البحث أبداً
        logger.warning("تعذر إضافة ما تعلّمه البحث من المراجعة: %s", e)
        return mappings


BRANDS_SHEET_TITLE = "Brands Mapping"
BRANDS_SHEET_HEADERS = ["Brand", "Synonyms", "Excluded Competitors", "Sub-brands", "Official domains"]


def _brands_worksheet(sh):
    """ورقة 'Brands Mapping'؛ تُنشأ بالقيم الافتراضية إن لم توجد."""
    try:
        return sh.worksheet(BRANDS_SHEET_TITLE)
    except gspread.exceptions.WorksheetNotFound:
        logger.info("ورقة 'Brands Mapping' غير موجودة؛ إنشاؤها بالقيم الافتراضية.")
        worksheet = sh.add_worksheet(title=BRANDS_SHEET_TITLE, rows="100", cols="5")
        default_rows = [
            BRANDS_SHEET_HEADERS,
            ["Meliha", "Mleiha, مليحة, مليحه", "Almarai, Sutas, Koita, Lacnor, Baladna, Al Rawabi, Nadec, Nada", "", ""],
            ["Saba Sanabel", "Sabaa Sanabel, سبع سنابل, صبا سنابل, سنابل", "Al Baker, Jenan, Grand Mills, Organic Larder", "", ""],
            ["Mai Dubai", "May Dubai, ماي دبي, مي دبي, مياه دبي", "Masafi, Al Ain, Oasis, Arwa, Aquafina, Nestle Pure Life, Voss, Evian", "", ""],
            ["Almarai", "Al Marai, المراعي", "Sutas, Koita, Lacnor, Baladna, Al Rawabi, Nadec, Nada, Meliha, Mleiha", "", ""],
            ["Masafi", "مسافي", "Al Ain, Oasis, Arwa, Aquafina, Nestle Pure Life, Mai Dubai, Voss, Evian", "", ""],
            ["Al Ain", "العين, alain", "Masafi, Oasis, Arwa, Aquafina, Nestle Pure Life, Mai Dubai, Voss, Evian", "", ""],
        ]
        worksheet.update("A1:E7", default_rows)
        return worksheet


def _read_brand_sheet(client, sheet_name_or_url):
    """مرادفات ورقة Brands Mapping كما في الشيت (كاش 5 دقائق)؛ أي خطأ قراءة يُرفع."""
    cached = _read_cache("brand_mappings_cache.json", 300, BRAND_CACHE_VERSION)
    if cached:
        return cached["mappings"]
    sh = _open_spreadsheet(client, sheet_name_or_url)
    worksheet = _brands_worksheet(sh)
    mappings = parse_brand_mapping_rows(worksheet.get_all_values())
    _write_cache("brand_mappings_cache.json", {"mappings": mappings}, BRAND_CACHE_VERSION)
    return mappings


def _sheet_brand_mappings(client, sheet_name_or_url):
    try:
        return _read_brand_sheet(client, sheet_name_or_url)
    except Exception as e:
        logger.error("خطأ أثناء جلب مرادفات البراندات من الشيت: %s", e)
        return {}


def sheet_brand_mappings(client, sheet_name_or_url):
    """
    ما في ورقة Brands Mapping وحده (بلا ما تعلّمه البحث من المراجعة)، من الكاش القصير نفسه الذي يقرؤه البحث. خطأ القراءة
    يُرفع (ورقة فاضية تعيد {}، وورقة ما انقرت ما تعيد {}): «ماركات ناقصة» ما بتقترح كل الماركات لأن الشيت ما انقرا.
    """
    return _read_brand_sheet(client, sheet_name_or_url)


def _brand_key(text):
    """مفتاح مقارنة ماركة بلا اعتبار لحالة الأحرف أو الفراغات أو علامات الترقيم."""
    return _NON_ALNUM_RE.sub("", unicodedata.normalize("NFKC", str(text or "")).casefold())


def _brand_columns(headers):
    """فهارس أعمدة ورقة Brands Mapping بعناوينها (نفس قراءة parse_brand_mapping_rows)؛ -1 لعمود غير موجود."""
    normalized = [normalize_header(h) for h in headers]
    cols = {}
    for key, synonyms in _BRAND_SHEET_COLUMNS.items():
        cols[key] = next((normalized.index(normalize_header(s)) for s in synonyms
                          if normalize_header(s) in normalized), -1)
    if cols["brand"] == -1:
        cols.update({"brand": 0, "synonyms": 1, "competitors": 2})
    return cols


def add_brand_mappings(client, sheet_name_or_url, items):
    """
    يضيف ماركات لورقة 'Brands Mapping' بصف لكل ماركة، بطلب كتابة واحد (append_rows) بعد قراءة طازجة للورقة (لا كاش):
    items [{brand, synonyms: [...], official_domains: [...]}] جاهزة التحقق (catalog_match.brand_assistant). ماركة مكتوبة
    أصلاً (هي أو أحد مرادفاتها، بلا اعتبار للحالة أو الفراغات) أو مكررة بالقائمة نفسها تُتخطى مع سببها ولا تُكتب، ومرادف
    مكتوب أصلاً لماركة ثانية لا يُكرر. تعيد {'added': [brand], 'skipped': [{'brand', 'reason': 'duplicate'}],
    'written': {brand: {'synonyms': نص الخلية, 'official_domains': نص الخلية}}} (اللي انكتب فعلاً، للتراجع). الأخطاء
    المؤقتة تُعاد محاولتها (SheetTransientError إن استمرت). كاش الماركات يُحذف بعد الكتابة كي يراها التشغيل الجاي.
    """
    sh = _retrying(_open_spreadsheet, client, sheet_name_or_url)
    worksheet = _retrying(_brands_worksheet, sh)
    rows = _retrying(worksheet.get_all_values)
    headers = list(rows[0]) if rows else list(BRANDS_SHEET_HEADERS)
    cols = _brand_columns(headers)
    known = set()
    for entry in parse_brand_mapping_rows(rows).values():
        known.update(k for k in (_brand_key(x) for x in [entry["brand"]] + list(entry["synonyms"])) if k)
    width = max(len(headers), 5)
    if cols["official_domains"] == -1:
        cols["official_domains"] = len(headers)
        width = max(width, len(headers) + 1)
        if rows and any(i.get("official_domains") for i in items):
            _retrying(worksheet.update_cell, 1, len(headers) + 1, BRANDS_SHEET_HEADERS[4])
    added, skipped, values, written = [], [], [], {}
    for item in items:
        brand = str(item["brand"]).strip()
        key = _brand_key(brand)
        if not key or key in known:
            skipped.append({"brand": brand, "reason": "duplicate"})
            continue
        synonyms = [x for x in item.get("synonyms") or [] if _brand_key(x) not in known and _brand_key(x) != key]
        row = [""] * width
        row[cols["brand"]] = brand
        if cols["synonyms"] != -1:
            row[cols["synonyms"]] = ", ".join(synonyms)
        row[cols["official_domains"]] = ", ".join(item.get("official_domains") or [])
        values.append(row)
        added.append(brand)
        written[brand] = {"synonyms": ", ".join(synonyms) if cols["synonyms"] != -1 else "",
                          "official_domains": row[cols["official_domains"]]}
        known.add(key)
        known.update(k for k in (_brand_key(x) for x in synonyms) if k)
    if values:
        _retrying(worksheet.append_rows, values, value_input_option="RAW")
        clear_brand_cache()
    return {"added": added, "skipped": skipped, "written": written}


def remove_brand_mapping(client, sheet_name_or_url, entry):
    """
    تراجع «عبّي جدول الماركات» عن ماركة واحدة: يمسح صفها من ورقة 'Brands Mapping' بعد قراءة طازجة، بس إذا الصف لسا
    متل ما كتبه المساعد بالضبط (entry {brand, synonyms, official_domains}: نص الخلايا متل ما انكتب، بلا اعتبار للفراغات
    حول الفواصل) وما في غيره بنفس الماركة. تعيد 'removed' | 'missing' (ما في صف للماركة) | 'changed' (المالك عدّل
    الصف أو في أكتر من صف: ما منمسح شي). طلب كتابة واحد (حذف الصف)؛ كاش الماركات بينحذف بعده.
    """
    def same(a, b):
        return [x.strip() for x in str(a or "").replace("،", ",").split(",") if x.strip()] == \
            [x.strip() for x in str(b or "").replace("،", ",").split(",") if x.strip()]

    sh = _retrying(_open_spreadsheet, client, sheet_name_or_url)
    worksheet = _retrying(_brands_worksheet, sh)
    rows = _retrying(worksheet.get_all_values)
    if not rows:
        return "missing"
    cols = _brand_columns(rows[0])
    key = _brand_key(entry.get("brand"))
    matches = [i for i, r in enumerate(rows[1:], start=2) if key and _brand_key(_cell(r, cols["brand"])) == key]
    if not matches:
        return "missing"
    if len(matches) > 1:
        return "changed"
    row = rows[matches[0] - 1]
    if str(_cell(row, cols["brand"])).strip() != str(entry.get("brand") or "").strip() \
            or not same(_cell(row, cols["synonyms"]) if cols["synonyms"] != -1 else "", entry.get("synonyms")) \
            or not same(_cell(row, cols["official_domains"]) if cols["official_domains"] != -1 else "",
                        entry.get("official_domains")):
        return "changed"
    _retrying(worksheet.delete_rows, matches[0])
    clear_brand_cache()
    return "removed"
