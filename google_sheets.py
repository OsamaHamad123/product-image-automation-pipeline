# google_sheets.py
# موديول التعامل مع Google Sheets: قراءة المنتجات بعناوين أعمدة مرنة، وكتابة آمنة عبر طابور MariaDB (outbox).
#
# قواعد الأمان (SHEET-1/2, SYNC-1/2, D12):
# - الأعمدة تُحدد بأسماء العناوين (مع جدول مرادفات) ولا يوجد أي رجوع لمواقع ثابتة؛ غياب عمود الاسم خطأ صريح.
# - لا يُخترع البراند من أول كلمة في الاسم: البراند الفارغ يبقى فارغاً.
# - كل كتابة تحمل هوية المنتج (الباركود/الاسم/الحجم/البراند). عند التفريغ نعيد قراءة أعمدة الهوية للصف الهدف،
#   وأي اختلاف يُسجل CONFLICT ولا يُكتب. الباركود وحده مفتاح فقط إذا كان GTIN صالحاً (رقم تحقق GS1)؛
#   'N/A' و'0' و'-' و'6.29E+12' ليست مفاتيح أبداً، فيُقارن الاسم مع الحجم والبراند. عمود الهدف يُحدد باسم
#   العنوان وقت التفريغ.
# - الكتابة المؤجلة عبر Redis تُستخدم فقط إذا كان مفتاح نبض sync_worker موجوداً.

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

import config
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
MAX_OUTBOX_ATTEMPTS = 5
OUTBOX_BATCH = 200

# أخطاء مؤقتة تُعاد محاولتها: تجاوز الحصة وأخطاء الخادم
_TRANSIENT_CODES = (429, 500, 502, 503, 504)
_sleep = time.sleep      # قابلة للاستبدال في الاختبارات


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


def _cell(row, idx):
    return row[idx].strip() if 0 <= idx < len(row) else ""


def _norm_barcode(value):
    digits = re.sub(r"\D", "", str(value or ""))
    return digits.lstrip("0")


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


def _write_cache(name, payload, version):
    try:
        payload = dict(payload, timestamp=time.time(), version=version)
        with open(_cache_path(name), "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
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

class SQLiteTransactionQueue:
    """طابور الكتابة (outbox) في جدول sheet_updates على MariaDB (الاسم تاريخي)."""

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
                "ALTER TABLE sheet_updates ADD INDEX IF NOT EXISTS idx_sheet_updates_status (sync_status)",
            ):
                cursor.execute(stmt)
            conn.commit()
        finally:
            conn.close()

    def append_update(self, row_number, col_index, value, col_name=None, key_barcode=None, key_name=None,
                      key_size=None, key_brand=None):
        conn = self._connect()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO sheet_updates (`row_number`, `col_index`, `value`, col_name, key_barcode, key_name, "
                "key_size, key_brand) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (row_number, col_index, "" if value is None else str(value), col_name, key_barcode, key_name,
                 key_size, key_brand)
            )
            conn.commit()
        finally:
            conn.close()
        clear_cache()


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
           cli_bridge)؛ وإلا قد يرسل مُفرِّغ متأخر قيمة قديمة بعد أن كتب آخر القيمة الأحدث فيمحوها.
           من لم يحصل على القفل يتخطى هذه الدورة (الصفوف تبقى PENDING لمن يملكه).
        1. قراءة الصفوف المعلقة بالترتيب (ORDER BY id) ودمج التكرارات على نفس الخلية ولنفس الهوية المتوقعة
           فقط (الأحدث يفوز)؛ سجلات بهويات مختلفة تُفحص كل منها على حدة.
        2. تحديد عمود الهدف باسم العنوان الآن، والتحقق من هوية كل سجل بقراءة عمود المفتاح (CONFLICT عند الاختلاف).
        3. إرسال دفعة واحدة؛ عند فشلها نعيد المحاولة صفاً صفاً كي لا يوقف صف معطوب البقية.
           كل فشل فردي يزيد attempts ويحفظ last_error، وبعد 5 محاولات تصبح الحالة DEAD.
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
        cursor.execute(
            "SELECT id, `row_number`, `col_index`, `value`, col_name, key_barcode, key_name, key_size, key_brand, "
            "attempts "
            "FROM sheet_updates WHERE sync_status IN ('PENDING', 'FAILED') ORDER BY id LIMIT %s",
            (OUTBOX_BATCH,),
        )
        rows = list(cursor.fetchall())
        if not rows:
            return

        # 1. الأحدث لكل خلية (صف، عمود) ولنفس الهوية المتوقعة يفوز؛ الأقدم يصبح SUPERSEDED.
        #    كتابة لمنتج آخر على نفس رقم الصف (رقم صف قديم) لا تُلغي كتابة المنتج الصحيح ولا العكس.
        latest = {}
        for r in rows:
            cell_key = (r["row_number"], (r.get("col_name") or "").strip().casefold() or r["col_index"],
                        _norm_barcode(r.get("key_barcode")), _norm_name(r.get("key_name")),
                        _norm_size(r.get("key_size")), _norm_name(r.get("key_brand")))
            if cell_key not in latest or r["id"] > latest[cell_key]["id"]:
                latest[cell_key] = r
        keep_ids = {r["id"] for r in latest.values()}
        superseded = [r["id"] for r in rows if r["id"] not in keep_ids]
        _set_status(cursor, superseded, "SUPERSEDED")
        rows = sorted(latest.values(), key=lambda r: r["id"])

        # 2. العمود بالاسم الآن + التحقق من الهوية
        headers = worksheet.row_values(1)
        normalized_headers = [normalize_header(h) for h in headers]
        ws_max_rows = worksheet.row_count
        targets = []
        conflicts = {}
        out_of_bounds = []
        for r in rows:
            col = r["col_index"]
            if r.get("col_name"):
                wanted = normalize_header(r["col_name"])
                if wanted not in normalized_headers:
                    conflicts[r["id"]] = f"column '{r['col_name']}' not found in sheet headers"
                    continue
                col = normalized_headers.index(wanted)
            if r["row_number"] <= 1 or col < 0 or r["row_number"] > ws_max_rows:
                out_of_bounds.append(r["id"])
                continue
            targets.append((r, col))

        records = {}
        for r, _ in targets:
            expect = _expectation(r.get("key_barcode"), r.get("key_name"), r.get("key_size"), r.get("key_brand"))
            if expect:
                records[r["id"]] = (r["row_number"], expect)
        record_conflicts = find_record_conflicts(worksheet, records, headers=headers) if records else {}
        ready = []
        for r, col in targets:
            reason = record_conflicts.get(r["id"])
            if reason:
                conflicts[r["id"]] = reason
            else:
                ready.append((r, col))
        # بعد التحقق: خلية واحدة تُكتب مرة واحدة فقط (الأحدث)
        newest = {}
        for r, col in ready:
            if (r["row_number"], col) not in newest or r["id"] > newest[(r["row_number"], col)][0]["id"]:
                newest[(r["row_number"], col)] = (r, col)
        dup_ids = [r["id"] for r, col in ready if newest[(r["row_number"], col)][0] is not r]
        _set_status(cursor, dup_ids, "SUPERSEDED")
        ready = sorted(newest.values(), key=lambda item: item[0]["id"])

        for rid, reason in conflicts.items():
            _set_status(cursor, [rid], "CONFLICT", reason[:1000])
            logger.warning("[Sheets Outbox] CONFLICT للتحديث %s: %s", rid, reason)
        _set_status(cursor, out_of_bounds, "SKIPPED_OUT_OF_BOUNDS")
        conn.commit()
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
            logger.warning("[Sheets Outbox] فشل الإرسال الجماعي (%s)؛ إعادة المحاولة صفاً صفاً.", e)

        for r, col in ready:
            try:
                send(body_for([(r, col)]))
                _set_status(cursor, [r["id"]], "SYNCED")
            except Exception as e:
                attempts = int(r.get("attempts") or 0) + 1
                status = "DEAD" if attempts >= MAX_OUTBOX_ATTEMPTS else "FAILED"
                cursor.execute(
                    "UPDATE sheet_updates SET attempts = %s, last_error = %s, sync_status = %s WHERE id = %s",
                    (attempts, str(e)[:1000], status, r["id"]),
                )
                logger.warning("[Sheets Outbox] فشل التحديث %s (المحاولة %s): %s", r["id"], attempts, e)
            conn.commit()
        clear_cache()


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


def _merged_payload(cached, row_number, updates, expect):
    """الحمولة بعد الدمج، أو None إذا تعذر الدمج بأمان (صيغة غير معروفة أو هوية مختلفة)."""
    payload = json.loads(cached) if cached else {"row_index": row_number, "updates": {}}
    if not isinstance(payload, dict) or not isinstance(payload.get("updates"), dict):
        logger.warning("[Redis Write-Behind] صيغة غير معروفة للصف %s؛ استخدام طابور MariaDB.", row_number)
        return None
    if cached and (payload.get("expect") or None) != (expect or None):
        # حمولة معلقة لمنتج آخر على نفس رقم الصف: لا ندمج (وإلا كُتبت قيمها بهوية المنتج الجديد)
        logger.warning("[Redis Write-Behind] حمولة الصف %s تخص هوية أخرى؛ استخدام طابور MariaDB.", row_number)
        return None
    payload["row_index"] = row_number
    for col, value in updates.items():
        payload["updates"][str(col)] = value
    if expect:
        payload["expect"] = expect
    return json.dumps(payload, ensure_ascii=False)


def _redis_merge_payload(r, key, row_number, updates, expect):
    """دمج ذري (WATCH/MULTI) لتحديثات الصف في حمولته؛ يعيد False ليُستخدم طابور MariaDB."""
    cache_key = f"{CACHE_PREFIX}{key}"
    if getattr(r, "pipeline", None) is None:
        merged = _merged_payload(r.get(cache_key), row_number, updates, expect)
        if merged is None:
            return False
        r.set(cache_key, merged)
        return True
    import redis
    for _ in range(5):
        with r.pipeline() as p:
            try:
                p.watch(cache_key)
                merged = _merged_payload(p.get(cache_key), row_number, updates, expect)
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
    جدولة تحديثات صف عبر Redis بالصيغة {'row_index', 'updates': {col: value}, 'expect': {...}}.
    تعيد False (ليُستخدم طابور MariaDB) إذا لم يكن Redis متاحاً أو لا يوجد نبض حي لـ sync_worker.
    """
    r = _get_redis()
    if r is None:
        return False
    try:
        if not r.exists(HEARTBEAT_KEY):
            return False
        key = f"row_{row_number}"
        if not _redis_merge_payload(r, key, row_number, updates, expect):
            return False
        r.sadd(DIRTY_SET_KEY, key)
        return True
    except Exception as e:
        logger.warning("[Redis Write-Behind] %s؛ استخدام طابور MariaDB.", e)
        return False


def _header_name(worksheet, col_idx):
    if worksheet is None:
        return None
    try:
        headers = _worksheet_headers(worksheet)
    except APIError:
        raise
    except Exception:
        return None
    return headers[col_idx] if 0 <= col_idx < len(headers) and str(headers[col_idx]).strip() else None


@retry_gspread_on_429()
def update_image_link(worksheet, row_number, link_column_index, image_link, barcode=None, product_name=None,
                      size=None, brand=None):
    """
    تحديث خلية رابط الصورة لصف منتج. barcode/product_name/size/brand هوية المنتج المتوقع في هذا الصف
    (قيم خلايا الشيت نفسها):
    عند التفريغ يُعاد التحقق منها، وأي اختلاف يُسجل CONFLICT ولا يُكتب.
    أخطاء APIError تُرفع كي يعمل مُزخرف إعادة المحاولة.
    """
    expect = _expectation(barcode, product_name, size, brand)
    try:
        if _redis_write_behind(row_number, {link_column_index: image_link}, expect):
            logger.info("[Redis Write-Behind] جدولة رابط الصف %s.", row_number)
            return True

        if _queue is not None and _worker is not None:
            _queue.append_update(row_number, link_column_index, image_link,
                                 col_name=_header_name(worksheet, link_column_index), **_outbox_keys(expect))
            logger.info("[Sheets Outbox] جدولة رابط الصف %s.", row_number)
            return True

        if expect:
            conflicts = find_identity_conflicts(worksheet, {row_number: expect})
            if row_number in conflicts:
                logger.warning("[Google Sheets] رفض الكتابة في الصف %s: %s", row_number, conflicts[row_number])
                return False
        worksheet.update_cell(row_number, link_column_index + 1, image_link)
        clear_cache()
        return True
    except APIError:
        raise
    except Exception as e:
        logger.error("فشل تحديث الرابط في الصف %s: %s", row_number, e)
        return False


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
    تحديث أعمدة البيانات الوصفية لصف (ينشئ العناوين الناقصة). نفس قواعد الهوية والطابور مثل update_image_link.
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
        updates = {col_indices[k]: str(metadata[k]) for k in col_indices}
        if not updates:
            return True

        if _redis_write_behind(row_number, updates, expect):
            return True

        if _queue is not None and _worker is not None:
            for col, value in updates.items():
                _queue.append_update(row_number, col, value, col_name=headers[col], **_outbox_keys(expect))
            return True

        if expect:
            conflicts = find_identity_conflicts(worksheet, {row_number: expect}, headers=headers)
            if row_number in conflicts:
                logger.warning("[Google Sheets] رفض كتابة البيانات الوصفية في الصف %s: %s",
                               row_number, conflicts[row_number])
                return False
        worksheet.batch_update([
            {"range": gspread.utils.rowcol_to_a1(row_number, col + 1), "values": [[value]]}
            for col, value in updates.items()
        ], value_input_option="RAW")
        return True
    except APIError:
        raise
    except Exception as e:
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


def _sheet_brand_mappings(client, sheet_name_or_url):
    cached = _read_cache("brand_mappings_cache.json", 300, BRAND_CACHE_VERSION)
    if cached:
        return cached["mappings"]
    try:
        sh = _open_spreadsheet(client, sheet_name_or_url)
        try:
            worksheet = sh.worksheet("Brands Mapping")
        except gspread.exceptions.WorksheetNotFound:
            logger.info("ورقة 'Brands Mapping' غير موجودة؛ إنشاؤها بالقيم الافتراضية.")
            worksheet = sh.add_worksheet(title="Brands Mapping", rows="100", cols="5")
            default_rows = [
                ["Brand", "Synonyms", "Excluded Competitors", "Sub-brands", "Official domains"],
                ["Meliha", "Mleiha, مليحة, مليحه", "Almarai, Sutas, Koita, Lacnor, Baladna, Al Rawabi, Nadec, Nada", "", ""],
                ["Saba Sanabel", "Sabaa Sanabel, سبع سنابل, صبا سنابل, سنابل", "Al Baker, Jenan, Grand Mills, Organic Larder", "", ""],
                ["Mai Dubai", "May Dubai, ماي دبي, مي دبي, مياه دبي", "Masafi, Al Ain, Oasis, Arwa, Aquafina, Nestle Pure Life, Voss, Evian", "", ""],
                ["Almarai", "Al Marai, المراعي", "Sutas, Koita, Lacnor, Baladna, Al Rawabi, Nadec, Nada, Meliha, Mleiha", "", ""],
                ["Masafi", "مسافي", "Al Ain, Oasis, Arwa, Aquafina, Nestle Pure Life, Mai Dubai, Voss, Evian", "", ""],
                ["Al Ain", "العين, alain", "Masafi, Oasis, Arwa, Aquafina, Nestle Pure Life, Mai Dubai, Voss, Evian", "", ""],
            ]
            worksheet.update("A1:E7", default_rows)
        mappings = parse_brand_mapping_rows(worksheet.get_all_values())
        _write_cache("brand_mappings_cache.json", {"mappings": mappings}, BRAND_CACHE_VERSION)
        return mappings
    except Exception as e:
        logger.error("خطأ أثناء جلب مرادفات البراندات من الشيت: %s", e)
        return {}


# ---------------------------------------------------------------------------
# مسار v1 القديم فقط
# ---------------------------------------------------------------------------

def align_brand_via_gemini(product_name, sheet_brand):
    """
    (مسار التراجع v1 فقط؛ أزيل من مسار البحث v2 — D8) التحقق من البراند عبر Gemini.
    يعيد البراند كما هو عند أي خطأ.
    """
    import requests

    if not config.GEMINI_API_KEY or not product_name or not sheet_brand:
        return sheet_brand
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{config.GEMINI_MODEL}:generateContent"
        prompt = (
            f"You are an e-commerce catalog data verification assistant.\n"
            f"We have a product named '{product_name}' which is classified under the brand '{sheet_brand}' in our sheet.\n"
            f"Only set 'is_correct' to false if the product name explicitly mentions a different, competing brand name. "
            f"In that case extract the actual brand from the title.\n"
            f'Reply strictly in JSON: {{"is_correct": true or false, "corrected_brand": "the correct brand name"}}'
        )
        payload = {"contents": [{"parts": [{"text": prompt}]}],
                   "generationConfig": {"responseMimeType": "application/json"}}
        headers = {"Content-Type": "application/json", "x-goog-api-key": config.GEMINI_API_KEY}
        if hasattr(config, "METRICS") and "gemini_api_calls" in config.METRICS:
            config.METRICS["gemini_api_calls"] += 1
        response = requests.post(url, headers=headers, json=payload, timeout=10)
        if response.status_code == 200:
            text = response.json()['candidates'][0]['content']['parts'][0]['text'].strip()
            text = re.sub(r"^```(?:json)?|```$", "", text).strip()
            result = json.loads(text)
            corrected = str(result.get("corrected_brand") or sheet_brand).strip()
            if result.get("is_correct") is False and corrected and corrected.lower() != "unknown":
                logger.info("[Brand Alignment v1] '%s': '%s' -> '%s'", product_name, sheet_brand, corrected)
                return corrected
    except Exception as e:
        logger.warning("خطأ أثناء تصحيح البراند بـ Gemini: %s", e)
    return sheet_brand


def update_product_localization(worksheet, row_number, clean_title_ar, canonical_brand_ar):
    """
    (الوضع التسلسلي القديم) كتابة الاسم والبراند العربي المقترحين إذا كانت خلاياهما فارغة.
    """
    try:
        headers = worksheet.row_values(1)
        cols = resolve_columns(headers)
        for idx, value, label in ((cols["name_ar"], clean_title_ar, "اسم المنتج بالعربي"),
                                  (cols["brand_ar"], canonical_brand_ar, "البراند بالعربي")):
            if idx == -1 or not value:
                continue
            current = worksheet.cell(row_number, idx + 1).value
            if not current or not str(current).strip():
                worksheet.update_cell(row_number, idx + 1, value)
                logger.info("[Localization] تحديث %s في الصف %s", label, row_number)
    except Exception as e:
        logger.warning("فشل تحديث التعريب التلقائي في الشيت: %s", e)
