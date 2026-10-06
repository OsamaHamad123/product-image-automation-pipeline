# cli_bridge.py
# جسر الكونسول الذي تستدعيه لوحة Laravel مباشرة (النقل الوحيد، D12):
#   python cli_bridge.py <action> <base64(json params)>
# المخرجات: وثيقة JSON واحدة فقط على stdout. كل سجلات المكتبات والطباعة الجانبية تذهب إلى temp/search.log.

import sys


def _reconfigure_utf8(stream):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


# يجب أن يسبق أي استيراد يطبع (config يطبع عند التحميل، والطباعة العربية تنهار على cp1256 في نوافذ)
_reconfigure_utf8(sys.stdout)
_reconfigure_utf8(sys.stderr)

import os

LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "temp", "search.log")
_JSON_STDOUT = None


def _open_log_stream(path=None):
    path = path or LOG_PATH
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return open(path, "a", encoding="utf-8", errors="replace")
    except OSError:
        return sys.stderr


if __name__ == "__main__":
    # عند التشغيل كسكربت: احجز stdout الحقيقي لوثيقة JSON وحوّل كل طباعة وقت الاستيراد إلى ملف السجل
    _JSON_STDOUT = sys.stdout
    sys.stdout = _open_log_stream()

import base64
import json
import logging
import time
import traceback
import uuid

import config
# log_runner يطبع على stdout؛ في الجسر نوجهه إلى السجل فقط
config._redis_available = False
_bridge_logger = logging.getLogger("cli_bridge")


def silent_log(*args):
    _bridge_logger.info(" ".join(str(a) for a in args))


config.log_runner = silent_log

import google_sheets
import image_search
import image_processor
import local_cache_db

logger = _bridge_logger

MAX_RESPONSE_CANDIDATES = 8
SELECTABLE_DECISIONS = ("AUTO_PUBLISH", "REVIEW_PRESELECTED")
WARNING_PREFIX = "warn:"   # تحذيرات المراجعة التي يضيفها decide.route للصورة المرشحة
REASON_ALIASES = {"BRAND_STYLE_MISMATCH": "WRONG_BRAND"}   # الكود القديم في واجهة الكتالوج


def _pipeline():
    """main.py يحوي أدوات النشر والبحث المشتركة (يُستورد عند الحاجة)."""
    import main as automation_main
    return automation_main


def decode_params():
    """فك البارامترات الممررة كـ Base64 (أو JSON مباشر)."""
    if len(sys.argv) < 3:
        return {}
    try:
        return json.loads(base64.b64decode(sys.argv[2]).decode('utf-8'))
    except Exception:
        try:
            return json.loads(sys.argv[2])
        except Exception:
            return {}


def _text(params, key):
    value = params.get(key)
    return "" if value is None else str(value).strip()


def _as_bool(value):
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def _failure(status, message, context):
    """
    حمولة خطأ برسالة ثابتة فقط: نص الاستثناء والـ traceback يذهبان إلى السجل (temp/search.log)
    ولا يُعادان أبداً في الاستجابة، لأن fastapi_server يمرر هذه الحمولة لعملاء HTTP.
    يجب استدعاؤها من داخل كتلة except.
    """
    logger.exception("%s", context)
    return {'status': status, 'error': message}


def _load_brand_mappings():
    try:
        client = google_sheets.get_sheets_client()
        if client:
            return google_sheets.get_brand_mappings(client, config.SPREADSHEET_NAME_OR_URL) or {}
    except Exception as e:
        logger.warning("تعذر تحميل مرادفات البراندات: %s", e)
    return {}


def _open_sheet():
    client = google_sheets.get_sheets_client()
    if not client:
        raise RuntimeError("Google Sheets API connection failed")
    worksheet = google_sheets.open_worksheet(client, config.SPREADSHEET_NAME_OR_URL)
    if not worksheet:
        raise RuntimeError(f"Sheet not found: {config.SPREADSHEET_NAME_OR_URL}")
    return worksheet


# ---------------------------------------------------------------------------
# get_products
# ---------------------------------------------------------------------------

def action_get_products(params):
    try:
        worksheet = _open_sheet()
        products, _ = google_sheets.get_products(worksheet)
    except google_sheets.SheetTransientError:
        # ليس خطأ رابط أو مشاركة: Google رفض مؤقتاً (الحصة أو خطأ خادم) حتى بعد إعادة المحاولة
        return _failure('failed', "Google Sheets is temporarily unavailable (quota or a Google server error). "
                                  "The sheet link and sharing are fine; try again in a minute.", "get_products failed")
    except Exception:
        return _failure('failed', "Could not read the Google Sheet. Check the spreadsheet URL and tab, and that it "
                                  "is shared with the service account (details in temp/search.log).", "get_products failed")
    failures = local_cache_db.get_product_failures()
    # sku_key لكل منتج (نفس حساب الطابور) حتى تربط لوحة التحكم المرشحات بالمنتج وليس برقم الصف
    brand_mappings = _load_brand_mappings() if products else {}
    pipeline = _pipeline() if products else None
    if products:
        try:
            from catalog_match.brand_index import BrandIndex
            brand_mappings = BrandIndex.from_mappings(brand_mappings)   # يُبنى مرة واحدة لكل الصفوف
        except Exception as e:
            logger.warning("تعذر بناء فهرس البراندات: %s", e)
    from catalog_match.identity import build_sku_spec
    quality = _sheet_quality_inputs(products)
    for prod in products:
        try:
            row = pipeline.sku_row(
                prod.get("product_name"), prod.get("brand"), prod.get("barcode"), {
                    "name_ar": prod.get("product_name_ar", ""), "brand_ar": prod.get("brand_ar", ""),
                    "category": prod.get("category", ""), "size": prod.get("size", "")})
            # main.compute_sku_key هو build_sku_spec(...).sku_key: المواصفة نفسها تُقرأ مرة واحدة
            spec = build_sku_spec(row, brand_mappings)
            prod["sku_key"] = spec.sku_key
            # المفتاح بلا باركود (main.compute_alt_sku_key): صف كُتب له باركود بعد اعتماده يجد اعتماده ورفضه به
            prod["alt_sku_key"] = pipeline.compute_alt_sku_key(row, spec)
            prod["sheet_states"] = _sheet_states(spec)
            prod["sheet_issues"] = _sheet_issues(row, spec, quality, prod.get("row_number"))
        except Exception as e:
            logger.warning("تعذر حساب sku_key للصف %s: %s", prod.get("row_number"), e)
        prod["has_error"], prod["error_message"] = _row_failure(failures, prod)
    # اسم التبويب الذي قُرئت منه الصفوف (open_worksheet فتحه، فلا طلب إضافي): صفحة التشغيل تعرضه من كاش هذه القراءة
    return {'status': 'success', 'products': products, 'sheet_tab': _tab_title(worksheet)}


def _tab_title(worksheet):
    try:
        return str(getattr(worksheet, "title", "") or "")
    except Exception:       # noqa: BLE001 - اسم التبويب للعرض فقط
        return ""


def _sheet_quality_inputs(products):
    """ما يحتاجه فحص جودة بيانات الشيت مرة واحدة لكل الصفوف: كلمات أسماء الشيت (لتمييز غلطة إملائية من كلمة تتكرر)
    والصفوف التي يتكرر باركودها لمنتج آخر. لا قراءة إضافية للشيت."""
    try:
        from catalog_match import explain
        return {"vocab": explain.Vocabulary.from_names([p.get("product_name") or "" for p in products]),
                "duplicates": explain.duplicate_barcodes(products)}
    except Exception as e:
        logger.warning("تعذر تجهيز فحص جودة بيانات الشيت: %s", e)
        return None


def _sheet_issues(row, spec, quality, row_number):
    """
    ما ينقص صف الشيت لاختيار واثق (catalog_match.explain.sheet_issues): حجم، باركود صالح، ماركة في Brands Mapping،
    غلطة إملائية محتملة، وباركود مكتوب لمنتج آخر. لوحة «جودة بيانات الشيت» في صفحة التشغيل تقرؤها من كاش المنتجات.
    """
    if quality is None:
        return None
    try:
        from catalog_match import explain
        issues = explain.sheet_issues(row, spec=spec, vocab=quality["vocab"])
        others = quality["duplicates"].get(int(row_number or 0))
        if others:
            issue = {"key": "duplicate_barcode", "rows": others}
            issue["text"] = explain.sheet_issue_text(issue)
            issues.append(issue)
        return issues
    except Exception as e:
        logger.warning("تعذر فحص جودة بيانات الصف %s: %s", row_number, e)
        return None


def _sheet_states(spec):
    """
    ما يذكره صف الشيت كما يقرؤه البحث (catalog_match.identity.build_sku_spec): حجم (size)، عدد العبوات (pack)، ومحاور
    النوع (variants). شاشة المراجعة تشتق منه «الحجم / النوع غير مؤكد» لمرشحات حُفظت قبل أن يحسبها الخادم، بقاعدة
    catalog_match.decide.unverified_warnings نفسها.
    """
    return {"size": spec.size is not None, "pack": int(spec.pack_count or 1),
            "variants": sorted(str(axis) for axis in (spec.variants or {}))}


def _row_failure(failures, prod):
    """
    سجل فشل الصف (local_cache_db.get_product_failures): بـ sku_key الصف أولاً (سجل لكل منتج، ومنه سجلات المفتاح
    ERR_..#<sku_key> لحجم آخر بنفس الاسم والبراند)، ثم بمفتاح العرض (الباركود أو ERR_<الاسم>_<البراند>) لسجل قديم
    بلا sku_key أو لسجل المنتج نفسه فقط: صف 2 لتر لا يعرض خطأ صف 1 لتر. تعيد (has_error, error_message).
    """
    sku = str(prod.get("sku_key") or "").strip()
    failure = failures.get(sku) if sku else None
    if failure is None:
        barcode = (prod.get("barcode") or "").strip()
        alt_barcode = f"ERR_{prod.get('product_name')}_{prod.get('brand')}".replace(" ", "_")
        for key in (barcode, alt_barcode):
            found = failures.get(key) if key else None
            if found and (not sku or not found.get("sku_key") or found.get("sku_key") == sku):
                failure = found
                break
    return bool(failure), (failure["error_message"] if failure else "")


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------

def _serialize_candidate(c):
    url = c.get("url") or c.get("image_url") or ""
    page_url = c.get("page_url") or ""
    domain = c.get("domain") or c.get("source_domain") or ""
    if not domain:
        try:
            from urllib.parse import urlparse
            domain = urlparse(page_url or url).netloc
        except Exception:
            domain = ""
    reasons = list(c.get("reasons") or [])
    # رموز التحذير بلا البادئة 'warn:' حتى لا تحلل الواجهة الأسباب بنفسها
    warnings = c.get("warnings")
    if not isinstance(warnings, list):
        warnings = [str(r)[len(WARNING_PREFIX):] for r in reasons if str(r).startswith(WARNING_PREFIX)]
    return {
        "url": url,
        "title": c.get("title") or "",
        "page_url": page_url,
        "domain": domain,
        "status": c.get("status") or "eligible",
        "reasons": reasons,
        "warnings": [str(w) for w in warnings],
        "evidence": c.get("evidence") or {},
        "vlm": c.get("vlm"),
        "scores": c.get("scores") or {},
        "width": c.get("width"),
        "height": c.get("height"),
        "content_sha256": c.get("content_sha256"),
    }


def _candidates_for_response(best, trace):
    source = best.get("candidates") if best else None
    if not source:
        source, seen = [], set()
        for step in (trace or {}).get("steps", []) or []:
            for c in step.get("candidates", []) or []:
                url = c.get("url")
                if url and url not in seen:
                    seen.add(url)
                    source.append(c)
    return [_serialize_candidate(c) for c in source if isinstance(c, dict)][:MAX_RESPONSE_CANDIDATES]


def _status_for(decision, has_result):
    if decision == "AUTO_PUBLISH":
        return "success"
    if decision == "PROVIDER_DOWN":
        return "provider_down"
    if decision in ("REVIEW_PRESELECTED", "REVIEW_UNSELECTED", "VERIFIER_DOWN") or has_result:
        return "review"
    return "not_found"


def _merge_urls(*lists):
    out = []
    for items in lists:
        for u in items or []:
            u = str(u or "").strip()
            if u and u not in out:
                out.append(u)
    return out


SPEND_RUN_DASHBOARD = "dashboard"
SPEND_RUN_RESEARCH = "research-"


def _record_spend(trace, sku_key, found):
    """
    تكلفة البحث من اللوحة في سجل الصرف اليومي نفسه الذي يقرؤه العامل قبل كل سحب (local_cache_db.record_search_spend،
    بأسعار ops_health): بحث المراجع بـ run_id 'dashboard'، وإعادة البحث بعد الرفض (reject_image يمرر found) بـ
    'research-<sku_key>'. لا يرفع أبداً.
    """
    run_id = f"{SPEND_RUN_RESEARCH}{sku_key or ''}"[:64] if found is not None else SPEND_RUN_DASHBOARD
    local_cache_db.record_search_spend((trace or {}).get('outcome'), run_id)


def action_search(params, brand_mappings=None, found=None):
    """
    بحث تفاعلي لمنتج واحد. الاستجابة (عقد ثابت للوحة التحكم):
    {status: success|review|not_found|provider_down|error, decision, failure_code, selected_image,
     candidates (أفضل 8 مع status/reasons/warnings/evidence/vlm/scores), provider_health, sku_key, trace}
    warnings: رموز تحذير المراجعة للصورة المرشحة (مثل foreign_store) ليتحقق منها المراجع قبل الاعتماد.
    found: قاموس اختياري يُعاد فيه best و trace كما هما (reject_image يحفظ منهما المرشحات الجديدة).
    """
    pipeline = _pipeline()
    product_name = _text(params, 'product_name')
    brand = _text(params, 'brand')
    if not product_name:
        return {'status': 'error', 'error': 'product_name is required'}

    payload = {}
    row_number = params.get('row_number')
    if row_number not in (None, ""):
        task = local_cache_db.get_task_by_row(int(row_number))
        if task and (task.get("product_name") or "").strip() == product_name:
            payload = pipeline.task_payload(task)
    name_ar = _text(params, 'product_name_ar') or payload.get('name_ar', '')
    brand_ar = _text(params, 'brand_ar') or payload.get('brand_ar', '')
    category = _text(params, 'category') or payload.get('category', '')
    size = _text(params, 'size') or payload.get('size', '')
    sub_category = _text(params, 'sub_category') or payload.get('sub_category', '')
    origin = _text(params, 'origin') or payload.get('origin', '')
    barcode = _text(params, 'barcode')

    if brand_mappings is None:
        brand_mappings = _load_brand_mappings()
    sku_key = _text(params, 'sku_key') or pipeline.compute_sku_key({
        "name": product_name, "brand": brand, "barcode": barcode, "name_ar": name_ar,
        "brand_ar": brand_ar, "category": category, "size": size}, brand_mappings)

    rejected_urls, rejected_phashes = local_cache_db.get_rejections(sku_key)
    exclude_urls = _merge_urls(params.get('exclude_urls'), rejected_urls)
    custom_query = pipeline.custom_query_for(_text(params, 'custom_query'), product_name, brand)
    query = custom_query or pipeline.default_query(product_name, brand)

    trace = {}
    try:
        best = image_search.search_best_product_image(
            query, product_name, brand,
            product_name_ar=name_ar, brand_ar=brand_ar, barcode=barcode, category=category,
            sub_category=sub_category, size_text=size, origin=origin,
            custom_query=custom_query, exclude_urls=exclude_urls, exclude_phashes=list(rejected_phashes),
            skip_cache=_as_bool(params.get('skip_cache', False)), brand_mappings=brand_mappings, trace=trace,
        )
    except Exception:
        logger.exception("search failed for %s", product_name)
        _record_spend(trace, sku_key, found)
        return {'status': 'error', 'error': "Search failed (details in temp/search.log).", 'decision': None,
                'failure_code': 'SEARCH_ERROR',
                'selected_image': None, 'candidates': [], 'provider_health': [], 'sku_key': sku_key, 'trace': trace}
    _record_spend(trace, sku_key, found)

    if found is not None:
        found.update(best=best, trace=trace)
    outcome = trace.get('outcome') if isinstance(trace.get('outcome'), dict) else {}
    decision = (best or {}).get('decision') or outcome.get('decision')
    if best and not decision:
        decision = "REVIEW_PRESELECTED"   # مسار v1: لا يُنشر تلقائياً أبداً
    failure_code = (best or {}).get('failure_code') or outcome.get('failure_code')
    candidates = _candidates_for_response(best, trace)
    return {
        'status': _status_for(decision, bool(best)),
        'decision': decision,
        'failure_code': failure_code,
        'selected_image': best if (best and decision in SELECTABLE_DECISIONS) else None,
        'candidates': candidates,
        # فئة اختيار المحرك (strict | unsure | other، أو None بلا اختيار): ترسلها شاشة الكتالوج مع قرار المراجع
        'lane': _pick_lane(candidates) if decision in local_cache_db.PICK_DECISIONS else None,
        'provider_health': outcome.get('provider_health') or [],
        'sku_key': (best or {}).get('sku_key') or outcome.get('sku_key') or sku_key,
        'exclusions': {'urls': len(exclude_urls), 'phashes': len(rejected_phashes)},
        'trace': trace,
        'brand': brand,
        # لماذا لا توجد صورة مختارة (catalog_match.explain)، أو None عندما اختار البحث صورة
        'explain': outcome.get('explain') if isinstance(outcome.get('explain'), dict) else None,
    }


# ---------------------------------------------------------------------------
# select_image (اعتماد بشري)
# ---------------------------------------------------------------------------

def _same_product_task(task, sku_key, product_name):
    """
    هل صف الطابور عند رقم الصف هذا هو نفس المنتج المطلوب؟ أرقام الصفوف تتغير عند تعديل الشيت،
    لذلك يُطابق بـ sku_key، وإلا بالاسم. صف طابور لمنتج آخر يُتجاهل تماماً.
    """
    if not task:
        return False
    task_sku = (task.get("sku_key") or "").strip()
    if sku_key and task_sku:
        return task_sku == sku_key
    task_name = " ".join((task.get("product_name") or "").lower().split())
    name = " ".join((product_name or "").lower().split())
    return bool(task_name) and task_name == name


# sku_key المرسل لمنتج غير الذي تصفه حقول الطلب (نتيجة بحث لمنتج آخر وصلت متأخرة، أو صفحة قديمة): لا يُكتب شيء
PRODUCT_CHANGED_ERROR = "المنتج تغيّر أثناء المراجعة؛ افتحه من جديد"

# حقول حمولة الطابور في مفتاح الصف (main.sku_row): (اسم الحقل في الطلب، اسمه في الحمولة)
_KEY_PAYLOAD_FIELDS = (("product_name_ar", "name_ar"), ("brand_ar", "brand_ar"), ("category", "category"),
                       ("size", "size"))


def _request_sku_key(params, task=None):
    """
    sku_key لحقول المنتج في الطلب بنفس حساب مفتاح صف الشيت عند بناء الطابور
    (main.compute_sku_key(main.sku_row(...))، والمفتاح لا يعتمد على شيت مرادفات البراندات).
    الاسم والبراند والباركود من الطلب دائماً. الحقل الغائب تماماً من الطلب (صفحة الدفعات لا ترسل الاسم والبراند
    بالعربية، ورفع الصورة لا يمرر الحجم) يؤخذ من حمولة صف الطابور task، ويمرره المستدعي فقط إن كان لنفس المنتج.
    """
    pipeline = _pipeline()
    stored = pipeline.task_payload(task) if task else {}
    payload = {key: (_text(params, sent) if sent in params else str(stored.get(key) or "").strip())
               for sent, key in _KEY_PAYLOAD_FIELDS}
    return pipeline.compute_sku_key(pipeline.sku_row(
        _text(params, 'product_name'), _text(params, 'brand'), _text(params, 'barcode'), payload))


def _product_changed(params, task):
    """
    هل sku_key المرسل لمنتج آخر غير الذي تصفه حقول الطلب (الاسم والبراند والباركود والحجم)؟ يُعاد حسابه من الحقول
    ويقارن. طلب بلا sku_key أو بلا اسم منتج لا يحمل هوية للمقارنة. task: صف الطابور عند رقم الصف (أو None).
    """
    sku_key = _text(params, 'sku_key')
    product_name = _text(params, 'product_name')
    if not sku_key or not product_name:
        return False
    same = _same_product_task(task, sku_key, product_name)
    return _request_sku_key(params, task if same else None) != sku_key


def _identity_problem(params, row_number):
    """
    يتحقق من sku_key والباركود المطلوبين. يعيد (sku_key, barcode, خطأ أو None).
    الباركود لا يُقارن بنسخة الطابور القديمة: الكتابة في الشيت تتحقق من هوية الصف الحي عند التنفيذ،
    وتصحيح المالك للباركود لا يجب أن يمنع الاعتماد. sku_key المرسل يجب أن يطابق حقول المنتج المرسلة معه.
    """
    sku_key = _text(params, 'sku_key')
    barcode = _text(params, 'barcode')
    task = local_cache_db.get_task_by_row(row_number)
    if _same_product_task(task, sku_key, _text(params, 'product_name')):
        known_barcode = (task.get("barcode") or "").strip()
        if known_barcode and not barcode:
            return sku_key, barcode, "barcode is required for this row (the sheet has one)"
        sku_key = sku_key or (task.get("sku_key") or "")
    if not sku_key:
        return sku_key, barcode, "sku_key is required"
    if _product_changed(params, task):
        return sku_key, barcode, PRODUCT_CHANGED_ERROR
    return sku_key, barcode, None


def _candidate_sha(params, row_number, sku_key, image_url, identity=None):
    """
    بصمة بايتات المرشح التي تم التحقق منها وتُنشر: بصمة مرشح هذا المنتج المحفوظ لنفس الرابط (_trusted_sha). البصمة
    التي ترسلها الواجهة (candidate_sha256 أو الاسم القديم content_sha256) لا تختار بايتات من مخزن المرشحات إلا إذا
    كانت بصمة ذلك المرشح؛ بلا مرشح محفوظ تُنزّل الصورة من رابطها (ما عرضته الصفحة).
    """
    return _trusted_sha(params, row_number, sku_key, image_url, identity)


def _candidate_page(params, row_number, sku_key, image_url, identity=None):
    """صفحة المرشح الذي يُعتمد (page_url المرسل، وإلا من مرشحات المنتج المحفوظة لنفس الرابط)، أو None."""
    sent = _text(params, 'page_url')
    if sent:
        return sent
    try:
        for c in local_cache_db.get_curation_candidates(row_number, sku_key=sku_key or None, identity=identity):
            if c.get("image_url") == image_url and c.get("page_url"):
                return c["page_url"]
    except Exception:
        # تحسين للتنزيل فقط (Referer): خطؤه لا يمنع الاعتماد
        logger.exception("تعذر قراءة صفحة المرشح للصف %s", row_number)
    return None


def _approval_page_gtin(params, row_number, sku_key, image_url, barcode, identity=None):
    """
    (page_gtin, صفحته) للاعتماد: الباركود الذي ذكرته صفحة متجر الصورة المعتمدة عندما لا يوجد باركود صالح في الشيت
    (catalog_match.gtin.barcode_from_page: GTIN صالح بالـ checksum وعالمي). من أدلة المرشح المحفوظ لنفس الرابط
    (evidence.page_gtin)، وإلا مما عرضته شاشة المراجعة (page_gtin: بحث مباشر لا تُحفظ مرشحاته). (None, None) بدونه.
    لا يُكتب في الشيت أبداً: المالك ينسخه (scripts/export_barcodes.py). لا يُرفع أي خطأ.
    """
    try:
        from catalog_match.gtin import barcode_from_page, is_valid_gtin

        if is_valid_gtin(barcode):
            return None, None          # الشيت فيه باركود صالح
        for c in local_cache_db.get_curation_candidates(row_number, sku_key=sku_key or None, identity=identity):
            if c.get("image_url") == image_url:
                found = barcode_from_page(barcode, (c.get("evidence") or {}).get("page_gtin"))
                if found:
                    return found, c.get("page_url") or _text(params, 'page_url') or None
        found = barcode_from_page(barcode, _text(params, 'page_gtin'))
        return (found, _text(params, 'page_url') or None) if found else (None, None)
    except Exception:
        logger.exception("تعذر قراءة باركود صفحة المتجر للصف %s", row_number)
        return None, None


def _page_domain(candidate, params):
    """نطاق الصفحة التي جاءت منها الصورة: من أدلة المرشح أو مصدره المحفوظ، وإلا من page_url المرسل."""
    for value in ((candidate.get("evidence") or {}).get("page_domain"), candidate.get("source_domain")):
        host = str(value or "").strip().lower()
        if host:
            return host[4:] if host.startswith("www.") else host
    from catalog_match.text_norm import url_host
    return url_host(_text(params, 'page_url') or candidate.get("page_url") or "") or None


def _is_cache_hit(candidate):
    """
    مرشح الكاش (main.collect_candidates): حل معتمد سابق يُعرض مجدداً بحالة preselected. ليس اختيار المحرك
    ولا يُنشر تلقائياً أبداً، فلا يُحسب دليلاً على دقة الاختيار المسبق.
    """
    evidence = candidate.get("evidence") if isinstance(candidate.get("evidence"), dict) else {}
    return evidence.get("source") == "cache" or "cache_hit" in (candidate.get("reasons") or [])


# ما عرضته شاشة الكتالوج للمراجع (بحث مباشر لا تُحفظ مرشحاته): القرار وحالة الصورة التي تصرف بها
_VIEW_DECISIONS = ("AUTO_PUBLISH", "REVIEW_PRESELECTED", "REVIEW_UNSELECTED")


def _pick_lane(candidates):
    """
    فئة اختيار المحرك (catalog_match.decide.lane_of: strict | unsure | other) من أسباب المرشح المختار مسبقاً بين
    المرشحات، أو None بلا اختيار (أو لمرشح الكاش: اعتماد سابق وليس اختيار المحرك).
    """
    from catalog_match.decide import lane_of
    pick = next((c for c in candidates or [] if isinstance(c, dict) and c.get("status") == "preselected"
                 and not _is_cache_hit(c)), None)
    return lane_of(pick.get("reasons") or []) if pick is not None else None


def _reviewer_view(params, action):
    """
    (engine_decision, was_preselected) كما عرضتهما شاشة الكتالوج (search_decision و candidate_status)، أو None
    عندما لم ترسل الشاشة قراراً معروفاً أو كانت الصورة من الكاش (اعتماد بشري سابق وليس اختيار المحرك).
    """
    decision = _text(params, 'search_decision')
    if decision not in _VIEW_DECISIONS or _as_bool(params.get('candidate_cache_hit', False)):
        return None
    if action == "manual_upload":
        return decision, False
    status = _text(params, 'candidate_status')
    return decision, (status == "preselected") if status else None


def _record_review(action, params, row_number, sku_key, image_url=None, reason_code=None, approval=None,
                   identity=None):
    """
    يسجل قرار المراجع في review_decisions (دليل فتح النشر الآلي لكل براند). يُستدعى قبل حذف مرشحات المنتج:
    ما عرضه المحرك يُقرأ منها. قرار البحث يُعرف فقط عندما تكون الصورة بين المرشحات المحفوظة (أو عند الرفع
    اليدوي بدلها): REVIEW_PRESELECTED إن كان بينها اختيار مسبق، وإلا REVIEW_UNSELECTED. شاشة الكتالوج تبحث
    مباشرة ولا تحفظ مرشحاتها، فترسل ما عرضته (search_decision و candidate_status، انظر _reviewer_view) ويُعتمد
    بدل المحفوظ؛ دون ذلك يبقى القرار غير معروف (None)، وكذلك مرشح الكاش (_is_cache_hit). approval: الحل
    المعتمد الذي يستهدفه الرفض؛
    رفض صورة نُشرت تلقائياً هو رفض لاختيار المحرك (AUTO_PUBLISH).
    أي خطأ هنا يُسجل في السجل ولا يغير نتيجة الإجراء.
    """
    acted, first, lesson = None, {}, None
    try:
        stored = local_cache_db.get_curation_candidates(row_number, sku_key=sku_key or None, identity=identity)
        candidates = [c for c in stored if not _is_cache_hit(c)]      # ما اختاره المحرك فقط
        acted = next((c for c in candidates if image_url and c.get("image_url") == image_url), None)
        decision = was_preselected = lane = None
        if candidates and (acted is not None or action == "manual_upload"):
            has_precheck = any(c.get("status") == "preselected" for c in candidates)
            decision = "REVIEW_PRESELECTED" if has_precheck else "REVIEW_UNSELECTED"
            lane = _pick_lane(candidates)
        if acted is not None:
            was_preselected = acted.get("status") == "preselected"
        elif action == "manual_upload":
            was_preselected = False
        view = _reviewer_view(params, action)
        if view is not None:
            # شاشة الكتالوج ترسل ما رآه المراجع فعلاً؛ قد يختلف عن آخر تشغيل محفوظ للعامل على نفس الصنف
            decision, was_preselected = view
            # فئة الاختيار كما أرسلتها الشاشة مع نتيجة بحثها، وإلا فئة الاختيار المحفوظ (نفس المرشحات المعروضة)
            shown = _text(params, 'search_lane')
            lane = shown if shown in local_cache_db.LANES else (lane or _pick_lane(stored))
            acted = {"identity_tier": _text(params, 'identity_tier') or None,
                     "vlm": {"decision": _text(params, 'vlm_decision') or None}, "page_url": _text(params, 'page_url')}
        if approval and approval.get("verification_status") == "auto_verified":
            # ما يُنشر آلياً تجاوز كل القواعد: فئته strict (catalog_match.decide.pick_lane)
            decision, was_preselected, lane = "AUTO_PUBLISH", True, "strict"
        if decision not in local_cache_db.PICK_DECISIONS:
            lane = None                      # لا اختيار للمحرك: لا فئة
        acted = acted or {}
        first = stored[0] if stored else {}
        vlm = acted.get("vlm") if isinstance(acted.get("vlm"), dict) else {}
        lesson = _learned_spelling(action, params, acted, reason_code, first_brand=first.get("brand"))
        local_cache_db.add_review_decision(
            action, sku_key=sku_key, row_number=row_number,
            brand=_text(params, 'brand') or first.get("brand"),
            product_name=_text(params, 'product_name') or first.get("product_name"),
            image_url=image_url, page_domain=_page_domain(acted, params),
            identity_tier=acted.get("identity_tier") or (acted.get("evidence") or {}).get("tier"),
            engine_decision=decision, was_preselected=was_preselected, vlm_decision=vlm.get("decision"),
            reason_code=reason_code, lane=lane,
            # الكتابة التي يُحسب لها القرار أو عليها: التراجع عن الرفض (undo_reject) يُرجع عدّها
            **({"learned_alias": lesson[1]} if lesson else {}),
        )
    except Exception:
        logger.exception("تعذر تسجيل قرار المراجع (%s) للصف %s", action, row_number)
    _learn_brand_spelling(action, params, acted, reason_code, first_brand=(first or {}).get("brand"))


def _learned_spelling(action, params, acted, reason_code, first_brand=None):
    """
    (ماركة الشيت، كتابة المتاجر) التي يعلّمها هذا القرار للبحث أو يُحسب ضدها، أو None: صورة ماركتها مؤكدة فقط بكتابة
    المتاجر (تنبيه brand_spelling:<الكتابة>) يعلّم اعتمادُها البحثَ هذه الكتابة، ورفضها بسبب WRONG_BRAND يُحسب ضدها.
    التحذيرات من المرشح المحفوظ (أسبابه) أو مما أرسلته شاشة المراجعة (candidate_warnings). لا يُرفع أي خطأ.
    """
    try:
        if action not in ("approved", "rejected") or (action == "rejected" and reason_code != "WRONG_BRAND"):
            return None
        from catalog_match import learning

        spelling = learning.spelling_from((acted or {}).get("reasons") or []) \
            or learning.spelling_from(params.get('candidate_warnings'))
        brand = _text(params, 'brand') or (first_brand or "")
        return (brand, spelling) if spelling and brand else None
    except Exception:
        logger.exception("تعذر قراءة ما يعلّمه قرار المراجع (%s) للبحث", action)
        return None


def _learn_brand_spelling(action, params, acted, reason_code, first_brand=None):
    """
    التعلّم من المراجعة (catalog_match/learning.py): الكتابة التي يعلّمها القرار (_learned_spelling) تُحسب لماركة
    الشيت عند الاعتماد وضدها عند رفض WRONG_BRAND. لا يُرفع أي خطأ.
    """
    try:
        lesson = _learned_spelling(action, params, acted, reason_code, first_brand)
        if lesson:
            from catalog_match import learning

            local_cache_db.record_brand_alias(lesson[0], lesson[1], approved=(action == "approved"))
            learning.clear_cache()
    except Exception:
        logger.exception("تعذر تسجيل ما تعلّمه البحث من قرار المراجع (%s)", action)


# ---------------------------------------------------------------------------
# الاعتماد فوق قرار لم يره المراجع (عقد C1 مع شاشة المراجعة)
# ---------------------------------------------------------------------------

# بلا expected_state (عميل قديم): اعتماد بشري لصورة أخرى خلال هذه المدة لا يُستبدل إلا بـ replace
APPROVAL_GUARD_SECONDS = 120
STALE_ERRORS = {
    "already_approved": "هذا المنتج اعتمده مراجع آخر؛ راجع الصورة المعتمدة قبل استبدالها",
    "state_changed": "حالة المنتج تغيّرت منذ فتحه؛ افتحه من جديد",
    "busy": "نشر آخر لهذا المنتج ما زال يكتب في الشيت؛ حاول بعد قليل",
}
# state_changed لأن الصورة نفسها رُفضت لهذا المنتج بعد فتح الصفحة (reason، و current.rejected_image)
IMAGE_REJECTED = "image_rejected"
IMAGE_REJECTED_ERROR = "هذه الصورة رفضها مراجع آخر لهذا المنتج؛ اختر صورة أخرى"


def _ts(value):
    """توقيت كنص 'YYYY-MM-DD HH:MM:SS' للمقارنة (datetime من بايثون أو نص من صفحة اللوحة)، أو None."""
    if value in (None, ""):
        return None
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    text = str(value).strip().replace("T", " ")[:19]
    return text or None


def _bare_link(value):
    text = str(value or "").strip()
    if text.startswith("needs_review:"):
        text = text[len("needs_review:"):].strip()
    return text


def _same_image(value):
    """
    الرابط بصيغة المقارنة: بلا بادئة needs_review:، ورابط تسليم Cloudinary إلنا بالتحويل الجديد (القديم q_auto,f_auto
    نفس الصورة: الشيت بعد scripts/migrate_delivery_urls.py والاعتماد المحفوظ قبله، delivery_urls.canonical_delivery_url).
    """
    from delivery_urls import canonical_delivery_url
    return canonical_delivery_url(_bare_link(value))


def _row_of(task):
    try:
        return int((task or {}).get("row_number"))
    except (TypeError, ValueError):
        return None


def _review_scope(params, sku_key, row_number):
    """
    ما يخص المنتج المطلوب في فحص C1: {identity, tasks, row}. identity و tasks من _product_scope (هويته وصفوف طابوره).
    row: رقم صف الطابور الذي تعرض الصفحة حالته: صف الطلب نفسه إن كان صف طابوره لهذا المنتج؛ وإلا (أُزيح صف المنتج
    في الشيت بعد إدراجه، والصفحة تطابق صف الطابور بـ sku_key) الصف الذي طابقته الصفحة (expected_state.queue_row) إن
    كان من صفوف هذا المنتج، وإلا أول صف طابور لهذا المنتج. None: لا صف طابور لهذا المنتج.
    """
    task = local_cache_db.get_task_by_row(row_number)
    own = task if _same_product_task(task, sku_key, _text(params, 'product_name')) else None
    identity, tasks = _product_scope(params, sku_key, row_number, own=own, own_known=True)
    row = int(row_number) if own is not None else None      # own: the queue row at the request's row number
    if row is None and tasks:
        seen = _row_of({"row_number": (_expected_state(params) or {}).get("queue_row")})
        row = seen if seen in _rows_of(tasks) else _row_of(tasks[0])
    return {"identity": identity, "tasks": tasks, "row": row}


def _approval_is_this_products(approval, identity):
    """
    هل الحل المعتمد بمفتاح المنتج هو اعتماد هذا المنتج؟ منتجان مختلفان قد يتشاركان مفتاح GTIN (خلية باركود واحدة)،
    فالمفتاح وحده لا يكفي: اسم وبراند السجل المعتمد يُقارنان بهوية المنتج المطلوب (local_cache_db.same_product، قاعدة
    صفوف المنتج نفسها في _product_scope). عند الشك (سجل بلا اسم، أو طلب بلا هوية) يُعد اعتماد هذا المنتج: الحارس يبقى.
    """
    if not approval:
        return False
    name = str(approval.get("product_name") or "").strip()
    identity = identity or {}
    if not name or not identity.get("name"):
        return True
    if " ".join(name.lower().split()) == " ".join(str(identity["name"]).lower().split()):
        return True
    return local_cache_db.same_product(identity, {"name": name, "brand": str(approval.get("brand") or "").strip()})


def _current_state(sku_key, row_number, product_name, scope=None):
    """
    (الحالة كما تعيدها الاستجابة، الحل المعتمد لهذا المنتج أو None). الحالة: صف طابور هذا المنتج (queue_status /
    queue_updated_at، و queue_row رقمه: صف الطلب، أو صفه المزاح، _review_scope)، والحل المعتمد لهذا المنتج
    (approved_url وهو رابط Cloudinary، approved_image_url الصورة الأصلية، approval_status: human_approved |
    auto_verified، approved_by، approved_at، approved_for اسم المنتج الذي اعتُمد له). اعتماد منتج آخر يشارك المفتاح
    (نفس خلية الباركود) ليس اعتماد هذا المنتج: لا يظهر في approved_url، ويُسمّى في key_approval_of.
    scope: _review_scope (يُحسب هنا إن غاب)؛ صف الطابور يُقرأ من جديد في كل استدعاء.
    """
    if scope is None:
        scope = _review_scope({"product_name": product_name, "sku_key": sku_key}, sku_key, row_number)
    found = local_cache_db.get_cached_product(sku_key=sku_key) if sku_key else None
    # صف صار له باركود بعد اعتماده: الاعتماد محفوظ بالمفتاح القديم، مفتاح صفوف طابوره البديل
    for alt in ([] if found or not sku_key else _alt_keys(sku_key, scope.get("tasks"))):
        found = local_cache_db.get_cached_product(sku_key=alt)
        if found:
            break
    approval = found if _approval_is_this_products(found, scope.get("identity")) else None
    task = local_cache_db.get_task_by_row(scope["row"]) if scope.get("row") is not None else None
    if task is not None and _row_of(task) != scope["row"] and _row_of(task) is not None:
        task = None
    if task is not None and sku_key and (task.get("sku_key") or "").strip() not in ("", sku_key):
        task = None
    approval = approval or {}
    current = {
        "queue_status": (task or {}).get("status") or None,
        "queue_updated_at": _ts((task or {}).get("updated_at")),
        "queue_row": _row_of(task) if task else None,
        "approved_url": approval.get("cloudinary_url") or None,
        "approved_image_url": approval.get("original_url") or None,
        "approval_status": approval.get("verification_status") or None,
        "approved_by": approval.get("approved_by") or None,
        "approved_at": _ts(approval.get("resolved_at")),
        "approved_for": (str(approval.get("product_name") or "").strip() or None) if approval else None,
    }
    if found and not approval:
        current["key_approval_of"] = str(found.get("product_name") or "").strip() or None
    return current, (approval or None)


def _alt_keys(sku_key, tasks=None):
    """
    المفاتيح البديلة لمنتج (alt_sku_key لصفوف طابوره، main.compute_alt_sku_key): مفتاحه قبل أن يُكتب له باركود صالح؛
    اعتماده ورفضه المحفوظان به يبقيان له. tasks: صفوف طابور المنتج (وإلا تُقرأ بالمفتاح).
    """
    tasks = local_cache_db.get_tasks_by_sku(sku_key) if tasks is None else tasks
    return sorted({str(t.get("alt_sku_key") or "").strip() for t in tasks or ()} - {"", str(sku_key or "")})


def _expected_state(params):
    expected = params.get('expected_state')
    if isinstance(expected, str) and expected.strip():
        try:
            expected = json.loads(expected)
        except ValueError:
            return None
    return expected if isinstance(expected, dict) else None


def _queue_changed(expected, current):
    """
    هل تغيّر صف الطابور منذ فتح الصفحة؟ الحالة ثم التوقيت (إن أرسلته الصفحة). صفحة لم ترَ صفاً (null) لا ترى
    الصفوف المكتملة أصلاً، فصف 'completed' يطابقها؛ الاعتماد البشري الذي أكمله يُفحص منفصلاً (already_approved).
    """
    seen = str(expected.get("queue_status") or "").strip() or None
    now = current["queue_status"]
    if seen is None:
        return now not in (None, "completed")
    if seen != now:
        return True
    seen_at = _ts(expected.get("queue_updated_at"))
    return bool(seen_at) and seen_at != current["queue_updated_at"]


def _approval_age_seconds(approval):
    resolved_at = approval.get("resolved_at")
    if resolved_at in (None, ""):
        return None
    if not hasattr(resolved_at, "timestamp"):
        try:
            import datetime
            resolved_at = datetime.datetime.strptime(_ts(resolved_at), "%Y-%m-%d %H:%M:%S")
        except (TypeError, ValueError):
            return None
    import time
    return abs(time.time() - resolved_at.timestamp())


def _trusted_sha(params, row_number, sku_key, image_url, identity=None):
    """
    بصمة بايتات المرشح الذي يُعتمد كما حفظها الخادم: content_sha256 لمرشح هذا المنتج المحفوظ بنفس الرابط، أو None.
    بصمة يرسلها الطلب (candidate_sha256 / content_sha256) لا تُستخدم أبداً بدل ذلك: بها كانت المعالجة تنشر بايتات
    مرشح آخر من مخزن المرشحات بينما يسمّي السجل image_url. بلا مرشح محفوظ بهذا الرابط (بحث مباشر من شاشة المراجعة)
    تُنزّل الصورة من رابطها، وهي ما عرضته الصفحة للمراجع (/api/image-proxy).
    """
    if not image_url:
        return None
    sent = _text(params, 'candidate_sha256') or _text(params, 'content_sha256')
    try:
        stored = local_cache_db.get_curation_candidates(row_number, sku_key=sku_key or None, identity=identity)
    except Exception:
        # تعذرت القراءة: لا بايتات محفوظة موثوقة (تُنزّل الصورة من رابطها)، والاعتماد نفسه لا يفشل بسببها
        logger.exception("تعذر قراءة المرشحات المحفوظة للصف %s", row_number)
        return None
    for c in stored:
        if c.get("image_url") == image_url and c.get("content_sha256"):
            if sent and sent.lower() != str(c["content_sha256"]).lower():
                logger.warning("بصمة sha256 المرسلة للصف %s لا تطابق المرشح المحفوظ لنفس الرابط؛ تُهمل", row_number)
            return c["content_sha256"]
    return None


def _image_phash(params, row_number, sku_key, image_url, identity=None):
    """
    pHash الصورة المطلوب اعتمادها لفحص الصور المرفوضة، من بايتات مرشح هذا المنتج المحفوظ لهذا الرابط (_trusted_sha) فقط،
    ولا يؤخذ أبداً من الطلب (phash أو بصمة sha256 مرسلة كانا يتجاوزان نصف الفحص). صورة بلا مرشح محفوظ (بحث مباشر من
    شاشة المراجعة) تُفحص برابطها: البحث نفسه استبعد الصور القريبة من بصمات الرفض (exclude_phashes) عند إيجادها.
    """
    sha = _trusted_sha(params, row_number, sku_key, image_url, identity)
    return _phash_of_stored(sha) if sha else None


def _has_phash_rejections(keys):
    """هل رُفضت لهذا المنتج صور ببصمة pHash؟ (خطأ القراءة: نعم، فيُحسب pHash الصورة ويُفحص)."""
    try:
        return any(local_cache_db.get_rejections(key)[1] for key in dict.fromkeys(k for k in keys if k))
    except Exception:
        return True


def _rejected_refusal(params, sku_key, row_number, product_name, image_url, scope=None):
    """
    رفض اعتماد صورة رفضها مراجع لهذا المنتج (رابطها أو pHash، بالمفتاح أو بمفتاح الصف البديل) بعد فتح الصفحة: الرفض لا
    يغيّر حالة الطابور ولا توقيته، فلا يراه expected_state. state_changed مع reason=image_rejected و
    current.rejected_image. replace لا يتجاوزه: الرفض دائم لهذه الصورة لهذا المنتج (ارفع الصورة يدوياً إن كان الرفض خطأ).
    pHash الصورة من بايتات مرشحها المحفوظ (_image_phash)، ويُقرأ فقط عندما رُفضت لهذا المنتج صور ببصمتها.
    """
    if not image_url or not sku_key:
        return None
    scope = scope or _review_scope(params, sku_key, row_number)
    task = local_cache_db.get_task_by_row(scope["row"]) if scope.get("row") is not None else None
    alt = (task or {}).get("alt_sku_key") if (task or {}).get("sku_key") in (None, "", sku_key) else None
    keys = (sku_key, alt)
    rejected = local_cache_db.image_rejected(keys, image_url)
    if rejected is False and _has_phash_rejections(keys):
        rejected = local_cache_db.image_rejected(
            keys, None, _image_phash(params, row_number, sku_key, image_url, scope.get("identity")))
    if not rejected:
        return None
    current = dict(_current_state(sku_key, row_number, product_name, scope)[0], rejected_image=True)
    return {'status': 'failed', 'error_code': 'state_changed', 'reason': IMAGE_REJECTED,
            'error': IMAGE_REJECTED_ERROR, 'current': current}


def _shows(shown, approval):
    """هل الصورة المعتمدة التي عرضتها الصفحة (shown، بلا بادئة needs_review:) هي هذا الاعتماد؟"""
    links = {_same_image(approval.get("cloudinary_url")), _same_image(approval.get("original_url"))}
    if not _bare_link(approval.get("cloudinary_url")):
        links.add("")       # اعتماد بلا رابط Cloudinary: current.approved_url كان null
    return _same_image(shown) in links


def _stale_refusal(params, sku_key, row_number, product_name, image_url=None, scope=None):
    """
    رفض الاعتماد / الرفع فوق قرار لم يره المراجع (None = مسموح).
    - الصورة رفضها مراجع لهذا المنتج -> state_changed (reason=image_rejected، _rejected_refusal)؛ replace لا يتجاوزه
    expected_state (ما عرضته الصفحة: queue_status, queue_updated_at, approved_url، و queue_row صف الطابور الذي طابقته):
      - اعتماد بشري لهذا المنتج لم تعرضه الصفحة (رابطه غير approved_url) -> already_approved
      - صف طابور هذا المنتج تغيّر منذ فتح الصفحة -> state_changed
    replace=true (تأكيد المراجع الصريح بالاستبدال) لا يتجاوز هذا الفحص: الصفحة ترسل معه ما عرضه التأكيد (current
    الرفض السابق) في expected_state، فإن تغيّر شيء مرة أخرى بعد التأكيد يُرفض ويُعرض من جديد.
    بلا expected_state (عميل قديم): يبقى السلوك القديم (replace يتجاوز)، إلا أن اعتماداً بشرياً لصورة أخرى خلال آخر
    دقيقتين (APPROVAL_GUARD_SECONDS) لا يُستبدل بلا replace -> already_approved. الاستجابة تحمل current لتعرضه الصفحة.
    اعتماد منتج آخر يشارك المفتاح ليس اعتماد هذا المنتج (_current_state): لا يرفض ولا يُعرض للاستبدال.
    """
    scope = scope or _review_scope(params, sku_key, row_number)
    refusal = _rejected_refusal(params, sku_key, row_number, product_name, image_url, scope)
    if refusal:
        return refusal
    expected = _expected_state(params)
    replace = _as_bool(params.get('replace', False))
    if replace and expected is None:
        return None
    current, approval = _current_state(sku_key, row_number, product_name, scope)
    human = bool(approval) and approval.get("verification_status") == "human_approved"
    code = None
    if expected is not None:
        if human and not _shows(_bare_link(expected.get("approved_url")), approval):
            code = "already_approved"
        elif _queue_changed(expected, current):
            code = "state_changed"
    elif human and not (image_url and image_url == approval.get("original_url")):
        age = _approval_age_seconds(approval)
        if age is not None and age < APPROVAL_GUARD_SECONDS:
            code = "already_approved"
    if code is None:
        return None
    return {'status': 'failed', 'error_code': code, 'error': STALE_ERRORS[code], 'current': current}


def _reviewer_check(params, sku_key, row_number, product_name, image_url, out, rows=None, scope=None):
    """
    before_write لاعتماد المراجع ورفعه (تحت قفل النشر للـ SKU، بعد المعالجة والرفع): يعيد فحص C1 لأن الحالة
    قد تتغير أثناء المعالجة، ثم يسحب حجز العامل عن صفوف المنتج (rows: صفوف هذا المنتج فقط، _product_scope) فلا
    ينشر العامل فوق هذا القرار بعد تحرير القفل.
    """
    def check():
        refusal = _stale_refusal(params, sku_key, row_number, product_name, image_url, scope)
        if refusal:
            out["refusal"] = refusal
            return False
        local_cache_db.release_worker_claims(row_number, sku_key=sku_key, rows=rows)
        return True
    return check


def _human_decision(barcode, product_name, brand, original_url, approved_by, sku_key, row_number, rows,
                    page_gtin=None, page_gtin_url=None):
    """
    after_write لاعتماد المراجع ورفعه: الحل المعتمد بشرياً وحالة صفوف المنتج (مكتملة) تُكتب قبل تحرير قفل النشر،
    فمراجع آخر ينتظر القفل يرى هذا الاعتماد في إعادة فحص C1 (already_approved) ولا يكتب فوقه في صمت.
    page_gtin / page_gtin_url: باركود صفحة المتجر لصف بلا باركود (_approval_page_gtin)، يُحفظ مع الاعتماد.
    """
    def record(res):
        if res.get("status") != "published":
            return      # رابط needs_review: ليس اعتماداً: لا يُسجل اعتماد بشري ولا يكتمل الصف (الشيت وقاعدة البيانات متفقان)
        local_cache_db.save_product_resolution(
            barcode, product_name, brand, original_url, res["link"], None, res.get("metadata"),
            perceptual_hash=res.get("phash"), verification_status="human_approved", approved_by=approved_by,
            sku_key=sku_key, color_signature=res.get("color_signature"),
            **({"page_gtin": page_gtin, "page_gtin_url": page_gtin_url} if page_gtin else {}),
        )
        local_cache_db.update_task_status_by_row(row_number, "completed", sku_key=sku_key, rows=rows)
    return record


# لوحة لم تجتز فحص القص (main.publish_image: quality_refused): لا يُرفع ولا يُكتب شيء، ويبقى المنتج بانتظار المراجعة
QUALITY_ERRORS = {
    "quality_flags": "فحص القص وجد ملاحظات على الصورة؛ لم تُنشر. راجعها ثم انشرها رغم ذلك أو اختر صورة أخرى",
    "background_failed": "لم تُعزل خلفية الصورة؛ لم تُنشر. اختر صورة أخرى أو ارفع صورة أوضح",
}


def _quality_refusal(res, sku_key, row_number, product_name, scope=None):
    """
    استجابة اعتماد / رفع لم يُنشر لأن القص لم يجتز الفحص: error_code quality_flags (علامات عرض فقط؛ يعيد المراجع الطلب
    مع publish_anyway=true بعد تأكيده) أو background_failed (لا نشر بهذه الصورة). quality_flags و quality_notes كما هي.
    """
    code = res.get("error") if res.get("error") in QUALITY_ERRORS else "background_failed"
    return {'status': 'failed', 'error_code': code, 'error': QUALITY_ERRORS[code],
            'quality_flags': list(res.get("quality_flags") or []), 'quality_notes': list(res.get("quality_notes") or []),
            'publish_anyway_allowed': bool(res.get("publish_anyway_allowed")), 'isolated': False,
            'current': _current_state(sku_key, row_number, product_name, scope)[0]}


def _not_written(res, out):
    """استجابة نشر لم يكتب شيئاً (superseded): رفض C1 إن حدث، وإلا قفل النشر بقي عند غيرنا."""
    if out.get("refusal"):
        return out["refusal"]
    return {'status': 'failed', 'error_code': 'busy', 'error': STALE_ERRORS["busy"]}


def _request_identity(params, task=None):
    """
    هوية المنتج المطلوب (local_cache_db.queue_row_identity): الاسم والبراند من الطلب، والاسم والبراند بالعربي والحجم
    من الطلب إن أرسلها، وإلا من حمولة صف الطابور task (يمرره المستدعي فقط إن كان لنفس المنتج).
    """
    stored = _pipeline().task_payload(task) if task else {}

    def pick(sent, key):
        return _text(params, sent) if sent in params else str(stored.get(key) or "").strip()

    return {"name": _text(params, 'product_name'), "brand": _text(params, 'brand'),
            "name_ar": pick('product_name_ar', 'name_ar'), "brand_ar": pick('brand_ar', 'brand_ar'),
            "size": pick('size', 'size')}


def _product_scope(params, sku_key, row_number, own=None, own_known=False):
    """
    (هوية المنتج، صفوف الطابور لهذا المنتج). sku_key وحده لا يكفي: منتجان مختلفان قد يتشاركان خلية باركود واحدة،
    والمفتاح بلا باركود لا يقرأ الاسم العربي. الصفوف: صفوف المفتاح التي تصف المنتج نفسه (local_cache_db.same_product)،
    وصف الطابور عند رقم صف الطلب إن كان لنفس المنتج (_same_product_task: هو الصف الذي تعرضه الصفحة).
    own_known: own هو صف الطلب لنفس المنتج كما قرأه المستدعي (أو None)، فلا يُقرأ من جديد.
    """
    if not own_known:
        task = local_cache_db.get_task_by_row(row_number)
        own = task if _same_product_task(task, sku_key, _text(params, 'product_name')) else None
    identity = _request_identity(params, own)
    tasks = []
    for t in (local_cache_db.get_tasks_by_sku(sku_key) if sku_key else []):
        same_row = own is not None and t.get("id") == own.get("id")
        if same_row or local_cache_db.same_product(identity, t):
            tasks.append(t)
    return identity, tasks


def _rows_of(tasks):
    out = []
    for task in tasks:
        try:
            out.append(int(task.get("row_number")))
        except (TypeError, ValueError):
            continue
    return out


def _other_rows(sku_key, row_number, tasks=None):
    """
    صفوف الشيت الأخرى لنفس المنتج: صفوف الطابور لهذا المنتج (_product_scope؛ المنتج مكرر في الشيت)، كل منها بهويته
    المسجلة عند الإدراج (الباركود والاسم والحجم والبراند)، فيتحقق الشيت من كل صف بهويته هو قبل الكتابة. الاعتماد
    يُكتب فيها كلها، وإلا تبقى الصفوف المكررة فارغة إلى الأبد بينما الطابور يعدّها مكتملة. منتج آخر يشارك المفتاح
    (نفس خلية الباركود) ليس منها أبداً.
    """
    if not sku_key:
        return []
    pipeline = _pipeline()
    out = []
    for task in (tasks if tasks is not None else []):
        try:
            other = int(task.get("row_number"))
        except (TypeError, ValueError):
            continue
        if other == int(row_number):
            continue
        payload = pipeline.task_payload(task)
        out.append({"row_number": other, "barcode": str(task.get("barcode") or "").strip(),
                    "product_name": str(task.get("product_name") or "").strip(),
                    "size": str(payload.get("size") or "").strip() or None,
                    "brand": str(task.get("brand") or "").strip() or None})
    return out


# حالات google_sheets.outbox_outcomes (أو sync_status في طابور الكتابة) كما تعيدها الاستجابة (عقد C3)
_SHEET_OUTCOMES = {"written": "written", "synced": "written", "pending": "pending", "failed": "pending",
                   "conflict": "conflict", "dead": "conflict", "skipped_out_of_bounds": "conflict",
                   # كتابة أحدث لنفس الخلية سبقتها: صورة هذا الاعتماد ليست ما في الشيت
                   "superseded": "conflict"}


def _own_link_writes(values, rows=None, since_id=None, value=None):
    """
    سجلات طابور الكتابة (google_sheets.outbox_outcomes: {id, row, queued_row, column_key, status, value ...}): كتابة
    الرابط التي جدولها هذا الطلب في كل صف، الأحدث فقط. since_id: آخر معرّف في الطابور قبل الجدولة (ما بعده فقط)؛
    value: القيمة التي كتبها الطلب. كتابات قديمة لنفس الصف (تعارض أو فشل قبل أسابيع، أو كتابة أُلغيت)، وكتابات
    البيانات الوصفية، وكتابة طلب آخر للخلية نفسها لا تُحسب. صف بلا كتابة من هذا الطلب (مع since_id) = unknown.
    """
    records = [v for v in values if isinstance(v, dict) and "id" in v and ("row" in v or "row_number" in v)]
    if not records or len(records) != len(values):
        return values
    latest = {}
    for rec in records:
        if rec.get("column_key") not in (None, "", "link"):
            continue
        if since_id is not None and int(rec.get("id") or 0) <= int(since_id):
            continue
        if value is not None and "value" in rec and str(rec.get("value") or "") != str(value):
            continue
        row = rec.get("queued_row") or rec.get("row", rec.get("row_number"))
        if since_id is not None and rows and row not in rows:
            continue
        if row not in latest or (rec.get("id") or 0) > (latest[row].get("id") or 0):
            latest[row] = rec
    out = list(latest.values())
    if since_id is not None:
        out += ["unknown" for row in (rows or []) if row not in latest]
    return out


def _sheet_outcome(rows, since_id=None, value=None):
    """
    ما حدث لكتابة الرابط في الشيت بعد التفريغ الأخير لطابور الكتابة (عقد C3):
    written (كُتب في كل الصفوف) | pending (ما زال في الطابور، يُعاد لاحقاً) | conflict (رُفض: هوية الصف تغيّرت،
    أو فشل نهائياً، أو كتابة أحدث لنفس الخلية سبقتها) | unknown. المصدر google_sheets.outbox_outcomes إن وُجدت (حزمة
    الشيت)، وإلا unknown. since_id / value: كتابات هذا الطلب فقط (_own_link_writes). أسوأ حالة بين الصفوف هي النتيجة.
    """
    outcomes = getattr(google_sheets, "outbox_outcomes", None)
    if not callable(outcomes) or not rows:
        return "unknown"
    try:
        if since_id is None:
            result = outcomes(list(rows))
        else:
            try:
                result = outcomes(list(rows), since_id=since_id)
            except TypeError:
                result = outcomes(list(rows))
    except Exception:
        logger.exception("تعذر قراءة نتيجة الكتابة في الشيت للصفوف %s", rows)
        return "unknown"
    if isinstance(result, dict):
        values = list(result.values())
    elif isinstance(result, (list, tuple, set)):
        values = list(result)
    else:
        values = [result]
    values = _own_link_writes(values, [int(r) for r in rows], since_id, value)
    codes = set()
    for item in values:
        if isinstance(item, dict):
            item = item.get("outcome") or item.get("status") or item.get("sync_status")
        codes.add(_SHEET_OUTCOMES.get(str(item or "").strip().lower(), "unknown"))
    if not codes:
        return "unknown"
    for code in ("conflict", "pending", "unknown"):
        if code in codes:
            return code
    return "written"


def _published_response(res, sku_key, row_number, **extra):
    """
    استجابة الاعتماد / الرفع الناجح. warnings: background_not_removed (كُتب needs_review:)، quality_flags (نُشرت
    رغم علامات العرض بعد تأكيد المراجع، published_anyway)، و duplicate_image (نفس الصورة منشورة لمنتج آخر،
    duplicate_of يسمّيه؛ الاعتماد الصريح يُكتب مع ذلك). warning: أول تحذير. quality_flags / quality_notes: فحص القص.
    bg_skipped: انتشرت بدون عزل الخلفية لأن المالك أوقفه بالإعدادات (main.publish_image)؛ ليس تحذيراً، فالرابط نظيف.
    bg_fallback: {provider, from, code}: خلص رصيد المزوّد السحابي (from) فعزلتها rembg المحلية المجانية (provider: rembg،
    موديل BiRefNet) واجتازت فحص القص؛ ليس تحذيراً، فالرابط نظيف والخلفية معزولة (main.publish_image).
    white_url: النسخة البيضا المعتمة (JPEG) من نفس الأصل (cloudinary_storage.white_version_url)، للتطبيق متى بده مربع
    أبيض؛ image_link هو الأصل الشفاف (OUTPUT_BACKGROUND = transparent).
    """
    response = dict({'status': 'success', 'image_link': res["link"], 'sheet_value': res["sheet_value"],
                     'isolated': res["isolated"], 'sku_key': sku_key,
                     'rows_written': res.get("rows_written") or [row_number]},
                    **extra)
    if res.get("rows_failed"):
        response['rows_failed'] = res["rows_failed"]
    if res.get("quality_flags"):
        response['quality_flags'] = list(res["quality_flags"])     # فحص جودة القص (لماذا لم تُعزل الخلفية)
    if res.get("quality_notes"):
        response['quality_notes'] = list(res["quality_notes"])     # ملاحظات الفحص غير المانعة
    if res.get("bg_skipped"):
        response['bg_skipped'] = True       # «انتشرت بدون عزل الخلفية» (عزل الخلفية متوقف بالإعدادات)
    if res.get("bg_fallback"):
        response['bg_fallback'] = dict(res["bg_fallback"])   # «انعزلت الخلفية بطريقة محلية لأن رصيد المزوّد خلص»
    if res.get("white_url"):
        response['white_url'] = res["white_url"]   # النسخة البيضا من نفس الأصل (b_white,...,f_jpg)
    warnings = []
    if str(res.get("sheet_value") or "").startswith("needs_review:"):
        warnings.append('background_not_removed')
    if res.get("published_anyway"):
        response['published_anyway'] = True
        warnings.append('quality_flags')
    if res.get("duplicate_of"):
        warnings.append('duplicate_image')
        response['duplicate_of'] = res["duplicate_of"]
    if warnings:
        response['warning'] = warnings[0]
        response['warnings'] = warnings
    return response


def action_select_image(params):
    image_url = _text(params, 'image_url')
    product_name = _text(params, 'product_name')
    brand = _text(params, 'brand')
    row_number = params.get('row_number')
    if not image_url or not product_name or not row_number:
        return {'status': 'failed', 'error': 'image_url, product_name and row_number are required'}
    row_number = int(row_number)
    sku_key, barcode, problem = _identity_problem(params, row_number)
    if problem:
        return {'status': 'failed', 'error': problem}
    scope = _review_scope(params, sku_key, row_number)
    refusal = _stale_refusal(params, sku_key, row_number, product_name, image_url, scope)
    if refusal:
        return refusal

    pipeline = _pipeline()
    identity, tasks = scope["identity"], scope["tasks"]
    rows = _rows_of(tasks)
    # الباركود الذي ذكرته صفحة المتجر لصف بلا باركود: يُقرأ قبل حذف المرشحات ويُحفظ مع الاعتماد
    page_gtin, page_gtin_url = _approval_page_gtin(params, row_number, sku_key, image_url, barcode, identity)
    queue_started = False
    guard = {}
    try:
        google_sheets.init_async_queue(config.CREDENTIALS_FILE, config.SPREADSHEET_NAME_OR_URL)
        queue_started = True
        outbox_since = local_cache_db.outbox_max_id()      # ما يُجدول بعده هو كتابات هذا الاعتماد (C3)
        worksheet = _open_sheet()
        link_column_index = google_sheets.find_link_column(worksheet)
        # ملف المعالجة الواحد (processing_profile) من صفحة الإعدادات، نفسه للنشر التلقائي والرفع اليدوي؛
        # target_width / enhance / bg_removal_method في الطلب لا تغيّره
        res = pipeline.publish_image(
            image_url, product_name, brand, row_number, worksheet, link_column_index,
            barcode=barcode, candidate_sha256=_candidate_sha(params, row_number, sku_key, image_url, identity),
            page_url=_candidate_page(params, row_number, sku_key, image_url, identity),
            category_override={k: _text(params, k) for k in ('category_l1_en', 'category_l2_en', 'category_l3_en')},
            key_size=_text(params, 'size') or None, key_brand=brand or None, sku_key=sku_key,
            also_rows=_other_rows(sku_key, row_number, tasks),
            before_write=_reviewer_check(params, sku_key, row_number, product_name, image_url, guard, rows, scope),
            after_write=_human_decision(barcode, product_name, brand, image_url, "human", sku_key, row_number, rows,
                                        page_gtin, page_gtin_url),
            unclean="refuse", publish_anyway=_as_bool(params.get('publish_anyway', False)),
        )
        if res["status"] == "quality_refused":
            return _quality_refusal(res, sku_key, row_number, product_name, scope)
        if res["status"] == "superseded":
            return _not_written(res, guard)
        if res["status"] == "failed":
            # صفحة الأعطال تقول لماذا لم يُعتمد (الواجهة تترجم الرمز؛ هذا السطر يبقى للتشخيص)
            config.log_error_to_laravel(f"Approval not published (row {row_number}): {res.get('error')}",
                                        product_name=product_name, brand=brand, barcode=barcode, level="WARNING")
            return {'status': 'failed', 'error': res.get('error'), 'isolated': res.get('isolated', False)}

        _record_review("approved", params, row_number, sku_key, image_url, identity=identity)
        local_cache_db.delete_curation_candidates(row_number, sku_key=sku_key, identity=identity)
        # «الباركود من صفحة المتجر» على بطاقة الصورة المعتمدة (اعتماد فعلي فقط، لا رابط needs_review:)
        extra = {"page_gtin": page_gtin} if page_gtin and res.get("status") == "published" else {}
        response = _published_response(res, sku_key, row_number, provider=res.get("provider"), **extra)
    except Exception as e:
        config.log_error_to_laravel(f"CLI action_select_image exception: {e}\n{traceback.format_exc()}",
                                    product_name=product_name, brand=brand, barcode=barcode, level="ERROR")
        return {'status': 'failed', 'error': "Publishing the image failed (details on the Errors page)."}
    finally:
        if queue_started:
            google_sheets.stop_async_queue()     # التفريغ الأخير لطابور الكتابة
    response['sheet'] = _sheet_outcome(response['rows_written'], outbox_since, response['sheet_value'])
    response['current'] = _current_state(sku_key, row_number, product_name, scope)[0]   # expected_state للطلب التالي
    return response


# ---------------------------------------------------------------------------
# upload_manual_image
# ---------------------------------------------------------------------------

def action_upload_manual_image(params):
    file_path = params.get('file_path')
    row_number = params.get('row_number')
    product_name = _text(params, 'product_name')
    brand = _text(params, 'brand')
    barcode = _text(params, 'barcode')
    if not file_path or not row_number or not product_name or not os.path.exists(file_path):
        return {'status': 'failed', 'error': 'Missing parameters or local file path not found'}
    row_number = int(row_number)
    task = local_cache_db.get_task_by_row(row_number)
    if _product_changed(params, task):
        return {'status': 'failed', 'error': PRODUCT_CHANGED_ERROR}

    pipeline = _pipeline()
    sku_key = _text(params, 'sku_key')
    if not sku_key:
        sku_key = (task or {}).get("sku_key") or pipeline.compute_sku_key(
            {"name": product_name, "brand": brand, "barcode": barcode}, _load_brand_mappings())
    scope = _review_scope(params, sku_key, row_number)
    refusal = _stale_refusal(params, sku_key, row_number, product_name, None, scope)
    if refusal:
        return refusal
    identity, tasks = scope["identity"], scope["tasks"]
    rows = _rows_of(tasks)
    queue_started = False
    guard = {}
    try:
        google_sheets.init_async_queue(config.CREDENTIALS_FILE, config.SPREADSHEET_NAME_OR_URL)
        queue_started = True
        outbox_since = local_cache_db.outbox_max_id()      # ما يُجدول بعده هو كتابات هذا الرفع (C3)
        worksheet = _open_sheet()
        link_column_index = google_sheets.find_link_column(worksheet)
        res = pipeline.publish_image(
            file_path, product_name, brand, row_number, worksheet, link_column_index, barcode=barcode,
            category_override={k: _text(params, k) for k in ('category_l1_en', 'category_l2_en', 'category_l3_en')},
            key_size=_text(params, 'size') or None, key_brand=brand or None, sku_key=sku_key,
            also_rows=_other_rows(sku_key, row_number, tasks),
            before_write=_reviewer_check(params, sku_key, row_number, product_name, None, guard, rows, scope),
            after_write=_human_decision(barcode, product_name, brand, "manual_upload", "human_upload", sku_key,
                                        row_number, rows),
            unclean="refuse", publish_anyway=_as_bool(params.get('publish_anyway', False)),
        )
        try:
            os.remove(file_path)
        except OSError:
            pass
        if res["status"] == "quality_refused":
            return _quality_refusal(res, sku_key, row_number, product_name, scope)
        if res["status"] == "superseded":
            return _not_written(res, guard)
        if res["status"] == "failed":
            return {'status': 'failed', 'error': res.get('error')}
        _record_review("manual_upload", params, row_number, sku_key, identity=identity)
        local_cache_db.delete_curation_candidates(row_number, sku_key=sku_key, identity=identity)
        response = _published_response(res, sku_key, row_number)
    except Exception as e:
        config.log_error_to_laravel(f"CLI action_upload_manual_image exception: {e}\n{traceback.format_exc()}",
                                    product_name=product_name, brand=brand, barcode=barcode, level="ERROR")
        return {'status': 'failed', 'error': "Uploading the image failed (details on the Errors page)."}
    finally:
        if queue_started:
            google_sheets.stop_async_queue()     # التفريغ الأخير لطابور الكتابة
    response['sheet'] = _sheet_outcome(response['rows_written'], outbox_since, response['sheet_value'])
    response['current'] = _current_state(sku_key, row_number, product_name, scope)[0]   # expected_state للطلب التالي
    return response


# ---------------------------------------------------------------------------
# reject_image (رفض بشري يغيّر النتائج المستقبلية)
# ---------------------------------------------------------------------------

def _phash_of_stored(sha):
    """pHash لبايتات مرشح محفوظة في مخزن المرشحات (أو None)."""
    if not sha:
        return None
    data = image_processor._load_from_candidate_store(sha)
    if not data:
        return None
    try:
        import io
        from PIL import Image
        from catalog_match.fetch import phash_hex
        with Image.open(io.BytesIO(data)) as img:
            img.load()
            return phash_hex(img.convert("RGB"))
    except Exception as e:
        logger.warning("تعذر حساب pHash للمرشح المرفوض: %s", e)
        return None


def _candidate_phash(row_number, image_url, params=None, sku_key=None, identity=None):
    """
    pHash للمرشح المرفوض ورابط صفحته. يعيد (phash, page_url).
    المصادر بالترتيب: phash المرسل، ثم بصمة البايتات المرسلة (candidate_sha256/content_sha256) من مخزن
    المرشحات مباشرة (شاشة الكتالوج لا تحفظ نتائجها في curation_candidates)، ثم مرشحات المنتج المحفوظة.
    """
    params = params or {}
    page_url = _text(params, 'page_url') or None
    sent_phash = _text(params, 'phash')
    if sent_phash:
        return sent_phash, page_url
    phash = _phash_of_stored(_text(params, 'candidate_sha256') or _text(params, 'content_sha256'))
    if phash:
        return phash, page_url
    for c in local_cache_db.get_curation_candidates(row_number, sku_key=sku_key or None, identity=identity):
        if c.get("image_url") != image_url:
            continue
        return _phash_of_stored(c.get("content_sha256")), c.get("page_url") or page_url
    return None, page_url


def _cell_holds(value, images):
    """
    هل تحمل قيمة الخلية (مع بادئة needs_review: أو بدونها) إحدى صور images (رابط واحد أو مجموعة)؟ رابط التسليم القديم
    والجديد لنفس الأصل نفس الصورة (_same_image).
    """
    value = _same_image(value)
    images = {images} if isinstance(images, str) else set(images or ())
    return bool(value) and value in {_same_image(i) for i in images}


def _approval_matches(approval, image_url, phash):
    """
    هل الحل المعتمد هو الصورة المرفوضة؟ رابطها الأصلي أو رابط Cloudinary المنشور (مع needs_review: أو بدونها)، أو
    pHash المحفوظ معه على مسافة DUPLICATE_PHASH_DISTANCE أو أقل من pHash الصورة المرفوضة.
    """
    if not approval:
        return False
    url = _bare_link(image_url)
    if url and url in (approval.get("original_url"), approval.get("cloudinary_url")):
        return True
    if url and local_cache_db.url_norm(url) in {local_cache_db.url_norm(approval.get(k))
                                                for k in ("original_url", "cloudinary_url") if approval.get(k)}:
        return True
    a, b = local_cache_db._phash_int(approval.get("perceptual_hash")), local_cache_db._phash_int(phash)
    return a is not None and b is not None and bin(a ^ b).count("1") <= local_cache_db.DUPLICATE_PHASH_DISTANCE


def _pending_links(rows):
    """
    {رقم الصف: قيمة} لآخر كتابة رابط ما زالت في طابور الكتابة (PENDING / FAILED تنتظر إعادة المحاولة) لهذه الصفوف:
    ما ستصير إليه الخلية، والخلية الحية لا تراها بعد. None عند تعذر قراءة الطابور.
    """
    outcomes = getattr(google_sheets, "outbox_outcomes", None)
    if not callable(outcomes) or not rows:
        return {}
    try:
        records = outcomes(list(rows))
    except Exception:
        logger.exception("تعذر قراءة الكتابات المعلقة للصفوف %s", rows)
        return None
    latest = {}
    for rec in records or []:
        if not isinstance(rec, dict) or rec.get("column_key") not in (None, "", "link"):
            continue
        if str(rec.get("status") or "").upper() not in ("PENDING", "FAILED"):
            continue
        row = rec.get("queued_row") or rec.get("row")
        if row not in latest or (rec.get("id") or 0) > (latest[row].get("id") or 0):
            latest[row] = rec
    return {int(row): str(rec.get("value") or "") for row, rec in latest.items()}


def _clear_rejected_cells(params, row_number, sku_key, images, tasks=None, flush=True):
    """
    يفرّغ خلية الرابط التي تحمل الصورة المرفوضة (images: رابطها، ورابطا الحل المعتمد إن كان هو المرفوض، مع بادئة
    needs_review: أو بدونها) في صف المنتج وفي صفوفه المكررة (tasks: صفوف هذا المنتج، _product_scope)، كل كتابة بهوية
    صفها (الباركود والاسم والحجم والبراند). ما في الخلية = آخر كتابة رابط معلقة في طابور الكتابة لهذا الصف إن وُجدت
    (اعتماد صورة أخرى لم يُكتب بعد)، وإلا الخلية الحية. يُستدعى تحت قفل النشر للـ SKU؛ flush=False: التفريغ الأخير
    لطابور الكتابة (اتصالات Google) يتركه للمستدعي بعد تحرير القفل.
    يعيد (فُرّغت خلية واحدة على الأقل، خطأ أو None).
    """
    rows = [{"row_number": row_number, "barcode": _text(params, 'barcode'), "product_name": _text(params, 'product_name'),
             "size": _text(params, 'size') or None, "brand": _text(params, 'brand') or None}]
    rows += _other_rows(sku_key, row_number, tasks)
    cleared, error = False, None
    queue_started = False
    try:
        google_sheets.init_async_queue(config.CREDENTIALS_FILE, config.SPREADSHEET_NAME_OR_URL)
        queue_started = True
        pending = _pending_links([r["row_number"] for r in rows])
        if pending is None:
            logger.warning("طابور الكتابة غير مقروء؛ يُحكم على خلايا الرفض من الشيت الحي وحده")
            pending = {}
        worksheet = _open_sheet()
        link_column_index = google_sheets.find_link_column(worksheet, create=False)
        if link_column_index >= 0:
            for row in rows:
                if row["row_number"] in pending:
                    current = pending[row["row_number"]]
                else:
                    current = worksheet.cell(row["row_number"], link_column_index + 1).value
                if not _cell_holds(current, images):
                    continue
                cleared = bool(google_sheets.update_image_link(
                    worksheet, row["row_number"], link_column_index, "", barcode=row["barcode"] or None,
                    product_name=row["product_name"] or None, size=row["size"], brand=row["brand"])) or cleared
    except Exception:
        error = "Could not update the sheet cell (details in temp/search.log)."
        logger.exception("تعذر تحديث الشيت بعد الرفض")
    finally:
        if queue_started and flush:
            google_sheets.stop_async_queue()
    return cleared, error


def _save_research_candidates(found, row_number, product_name, brand, sku_key, rejected_url, identity=None):
    """
    إعادة البحث بعد الرفض تحفظ مرشحاتها الجديدة بنفسها (عقد C2، بـ sku_key المنتج)، بدل أن تحفظها الصفحة، ومعها سبب
    «بلا اقتراح» للبحث الجديد في trace صف الطابور. لا شيء يُحفظ بلا نتيجة (لم يُعثر على شيء / المزودون معطلون):
    المرشحات الباقية تبقى. يعيد عدد المحفوظ.
    """
    best = found.get("best")
    if not best:
        return 0
    candidates = [c for c in _pipeline().collect_candidates(best, found.get("trace"))
                  if (c.get("url") or c.get("image_url")) != rejected_url]
    if not candidates:
        return 0
    if not local_cache_db.save_curation_candidates(row_number, product_name, best.get("brand") or brand, candidates,
                                                   best.get("url"), sku_key=sku_key,
                                                   run_id=f"research-{uuid.uuid4().hex[:8]}", identity=identity):
        return 0
    # the row's «why no pick» is now the new search's (None: it found a pick)
    outcome = (found.get("trace") or {}).get("outcome")
    _save_research_explain(row_number, sku_key, product_name,
                           outcome.get("explain") if isinstance(outcome, dict) else None)
    return len(candidates)


def _save_research_explain(row_number, sku_key, product_name, explain):
    """
    مرشحات إعادة البحث بعد الرفض حلّت محل السابقة: سبب «بلا اقتراح» في trace صف الطابور يصير سبب البحث الجديد
    (None = البحث الجديد اختار صورة). لا يغيّر الحالة ولا يكتب فوق نتيجة أحدث للعامل.
    """
    try:
        task = local_cache_db.get_task_by_row(row_number)
        if task and task.get("trace_json") and _same_product_task(task, sku_key, product_name):
            local_cache_db.set_queue_explain(task["id"], task["trace_json"], explain)
    except Exception:
        logger.exception("could not store the research's no-pick reason for row %s", row_number)


def _set_review_queue_status(row_number, sku_key, product_name, status, reason_code, failure_code=None, rows=None):
    """
    حالة الطابور بعد الرفض (None = بلا تغيير). لا يُعاد كتابة نفس الحالة: توقيت الصف يبقى كما رأته الصفحة.
    rows: صفوف هذا المنتج فقط (_product_scope)؛ منتج آخر يشاركه الباركود لا يعود للطابور برفض صورة غيره.
    """
    if not status:
        return
    task = local_cache_db.get_task_by_row(row_number)
    if _same_product_task(task, sku_key, product_name) and task.get("status") == status:
        return
    if status == "pending":
        local_cache_db.update_task_status_by_row(row_number, "pending", f"rejected by reviewer: {reason_code}",
                                                 failure_code="REJECTED", sku_key=sku_key, rows=rows)
    else:
        local_cache_db.update_task_status_by_row(row_number, status, None, failure_code=failure_code,
                                                 sku_key=sku_key, rows=rows)


def action_reject_image(params):
    image_url = _text(params, 'image_url')
    product_name = _text(params, 'product_name')
    brand = _text(params, 'brand')
    barcode = _text(params, 'barcode')
    row_number = params.get('row_number')
    reason_code = _text(params, 'reason_code')
    if not reason_code and params.get('rejection_reasons'):
        reason_code = str(params['rejection_reasons'][0]).strip()
    reason_code = REASON_ALIASES.get(reason_code, reason_code)
    if not image_url or not row_number:
        return {'status': 'error', 'error': 'image_url and row_number are required'}
    if reason_code not in local_cache_db.REJECT_REASON_CODES:
        return {'status': 'error', 'error': f"invalid reason_code {reason_code!r}",
                'allowed': list(local_cache_db.REJECT_REASON_CODES)}
    row_number = int(row_number)
    task = local_cache_db.get_task_by_row(row_number)
    if _product_changed(params, task):
        return {'status': 'error', 'error': PRODUCT_CHANGED_ERROR}

    brand_mappings = None
    sku_key = _text(params, 'sku_key')
    if not sku_key:
        sku_key = (task or {}).get("sku_key") or ""
    if not sku_key:
        if not product_name:
            return {'status': 'error', 'error': 'sku_key or product_name is required'}
        brand_mappings = _load_brand_mappings()
        sku_key = _pipeline().compute_sku_key({"name": product_name, "brand": brand, "barcode": barcode},
                                              brand_mappings)

    scope = _review_scope(params, sku_key, row_number)
    identity, tasks = scope["identity"], scope["tasks"]
    rows = _rows_of(tasks)
    # المفتاح البديل (قبل إضافة باركود صالح للصف): اعتماد حُفظ به يبقى اعتماداً لهذا المنتج
    alt_keys = sorted({str(t.get("alt_sku_key") or "").strip() for t in tasks} - {"", sku_key})
    phash, page_url = _candidate_phash(row_number, image_url, params, sku_key, identity)
    page_url = page_url or _text(params, 'page_url') or None
    try:
        with local_cache_db.sku_publish_lock(sku_key) as lock_state:
            if lock_state == "busy":
                # نشر لنفس المنتج ما زال يكتب: لا يُحكم على الخلية أو الاعتماد قبل أن يُسجل قراره
                return {'status': 'error', 'error_code': 'busy', 'error': STALE_ERRORS["busy"],
                        'current': _current_state(sku_key, row_number, product_name, scope)[0]}
            if not local_cache_db.add_rejected_image(sku_key, image_url, page_url=page_url, phash=phash,
                                                     reason_code=reason_code):
                return {'status': 'error', 'error': 'could not record the rejection'}
            # رفض مرشح آخر لمنتج معتمد بشرياً لا يُلغي الاعتماد ولا يعيد الصف للطابور (وإلا قد ينشر العامل
            # تلقائياً فوق الرابط المعتمد). يُلغى الاعتماد فقط إذا كان هو الصورة المرفوضة (الرابط أو رابط Cloudinary أو
            # pHash)، بالمفتاح أو بالمفتاح البديل.
            approvals = {sku_key: local_cache_db.get_cached_product(sku_key=sku_key)}
            for key in alt_keys:
                approvals[key] = local_cache_db.get_cached_product(sku_key=key)
            approved = approvals[sku_key]
            targeted = [key for key, found in approvals.items() if _approval_matches(found, image_url, phash)]
            targets_approval = sku_key in targeted
            target = approvals[targeted[0]] if targeted else None
            keep_approval = (bool(approved) and approved.get("verification_status") == "human_approved"
                             and not targets_approval)
            stored = local_cache_db.get_curation_candidates(row_number, sku_key=sku_key or None, identity=identity)
            _record_review("rejected", params, row_number, sku_key, image_url, reason_code=reason_code,
                           approval=target, identity=identity)
            # المراجعة تبقى حية (عقد C2): تُستبعد الصورة المرفوضة وحدها، وباقي المرشحات تبقى للمراجع
            rejected = next((c for c in stored if c.get("image_url") == image_url), None)
            remaining = [c for c in stored if c.get("image_url") != image_url
                         and c.get("status") not in ("rejected", "excluded")]
            shown_status = rejected.get("status") if rejected else _text(params, 'candidate_status')
            was_pick = bool(targeted) or shown_status == "preselected"
            if rejected:
                local_cache_db.exclude_curation_candidate(row_number, image_url, sku_key=sku_key, identity=identity)
            local_cache_db.save_feedback(str(uuid.uuid4()), image_url.split("/")[-1].split("?")[0], row_number,
                                         product_name, brand, image_url, [reason_code])

            images = {_bare_link(image_url)}
            for found in (approvals[key] for key in targeted):
                images |= {u for u in (found.get("cloudinary_url"), found.get("original_url")) if u}
            sheet_cleared, sheet_error = _clear_rejected_cells(params, row_number, sku_key, images, tasks,
                                                                     flush=False)
            if sheet_cleared:
                # الخلية (أو كتابتها المعلقة) كانت تحمل الصورة المرفوضة: هي المنشورة أو المقترحة. اعتماد صورة أخرى يبقى
                # (الإدراج التالي يعيد كتابة رابطه)؛ لا يُلغى إلا الاعتماد الذي هو الصورة المرفوضة
                was_pick = True
            superseded = 0
            for key in targeted:
                count = local_cache_db.supersede_resolution(key, barcode=(barcode or None) if key == sku_key else None)
                superseded += count or 0
                if key != sku_key:
                    local_cache_db.add_rejected_image(key, image_url, page_url=page_url, phash=phash,
                                                      reason_code=reason_code)

            # حالة الطابور: الاعتماد البشري الباقي لا يُمس. رفض الاختيار (المسبق أو المعتمد أو المنشور) -> بانتظار
            # المراجعة إن بقي مرشح مؤهل، وإلا يعود الصف للطابور، وكذلك رفض آخر مرشح مؤهل محفوظ (رُفض الاختيار قبله).
            # رفض بديل وغيره باقٍ لا يغيّرها. تُكتب تحت القفل: اعتماد ينتظر القفل يأتي بعدها فلا تمحوه. مع إعادة البحث
            # تُكتب بعده (مرشحات جديدة -> بانتظار المراجعة)، فلا يسحب العامل الصف أثناءه.
            queue_status = None
            if not keep_approval and (was_pick or (rejected is not None and not remaining)):
                queue_status = "ready_for_review" if remaining else "pending"
            research = _as_bool(params.get('research', False))
            if not research:
                _set_review_queue_status(row_number, sku_key, product_name, queue_status, reason_code, None, rows)
    finally:
        # التفريغ الأخير لطابور الكتابة بعد تحرير قفل النشر: نشر آخر لنفس المنتج لا ينتظر اتصالات Google
        google_sheets.stop_async_queue()

    response = None
    candidates_saved = 0
    if research:
        search_params = dict(params, sku_key=sku_key, skip_cache=True,
                             exclude_urls=_merge_urls(params.get('exclude_urls'), [image_url]))
        found = {}
        response = action_search(search_params, brand_mappings=brand_mappings, found=found)
        with local_cache_db.sku_publish_lock(sku_key) as lock_state:
            candidates_saved = _save_research_candidates(found, row_number, product_name, brand, sku_key, image_url,
                                                         identity)
            if candidates_saved and not keep_approval:
                queue_status = "ready_for_review"
            # مراجع اعتمد المنتج أثناء البحث: قراره (المسجل تحت القفل قبلنا) يبقى كما هو
            now = local_cache_db.get_cached_product(sku_key=sku_key) or {}
            if lock_state == "busy" or (now.get("verification_status") == "human_approved"
                                        and not _approval_matches(now, image_url, phash)):
                queue_status = None
            _set_review_queue_status(row_number, sku_key, product_name, queue_status, reason_code,
                                     (response or {}).get('failure_code'), rows)

    rejection = {'sku_key': sku_key, 'reason_code': reason_code, 'phash': phash, 'sheet_cleared': sheet_cleared,
                 'superseded': superseded, 'sheet_error': sheet_error, 'approval_kept': keep_approval,
                 'candidates_left': len(remaining), 'queue_status': queue_status}
    current, _ = _current_state(sku_key, row_number, product_name, scope)
    if response is not None:
        response['rejection'] = rejection
        response['candidates_saved'] = candidates_saved
        response['current'] = current
        return response
    return dict({'status': 'success', 'current': current}, **rejection)


# ---------------------------------------------------------------------------
# undo_reject: «تراجع عن الرفض» (رفض بالغلط أو للتجربة)
# ---------------------------------------------------------------------------

def action_undo_reject(params):
    """
    التراجع عن رفض صورة لمنتج (POST /api/review/undo-reject): يُحذف صف rejected_images واحد لهذا الـ sku_key والرابط،
    ويُعلَّم قرار الرفض في review_decisions بـ undone_at فلا تحسبه إحصائيات المراجعة ولا التعلّم
    (local_cache_db.undo_rejection). بعدها «دوّر مرة ثانية» يقدر يلاقي الصورة. لا يغيّر الشيت ولا الاعتماد ولا الطابور.
    {status: success, ...} أو not_found (ما في رفض مسجل لهالصورة) أو error.
    """
    sku_key = _text(params, 'sku_key')
    image_url = _text(params, 'image_url')
    if not sku_key or not image_url:
        return {'status': 'error', 'error': 'sku_key and image_url are required'}
    try:
        result = local_cache_db.undo_rejection(sku_key, image_url, row_number=params.get('row_number'))
        # رفض حُفظ قبل أن يُكتب للصف باركود: محفوظ بالمفتاح البديل
        for alt in ([] if result.get("removed") else _alt_keys(sku_key)):
            again = local_cache_db.undo_rejection(alt, image_url, row_number=params.get('row_number'))
            if again.get("removed"):
                result = again
                break
    except Exception:
        return _failure('failed', "Could not undo the rejection (details in temp/search.log).", "undo_reject failed")
    if not result.get("removed"):
        return dict({'status': 'not_found', 'error': "ما في رفض مسجل لهالصورة لهالمنتج", 'sku_key': sku_key,
                     'image_url': image_url}, **result)
    if result.get("alias_restored"):
        try:
            from catalog_match import learning
            learning.clear_cache()
        except Exception:
            logger.exception("تعذر تفريغ كاش التعلّم بعد التراجع عن الرفض")
    return dict({'status': 'success', 'sku_key': sku_key, 'image_url': image_url}, **result)


# ---------------------------------------------------------------------------
# review_stats (قراءة فقط: دليل النشر الآلي من قرارات المراجعين)
# ---------------------------------------------------------------------------

def action_review_stats(params):
    """
    دقة الاختيار المسبق لكل براند ونطاق من review_decisions (local_cache_db.review_stats، نفس حساب
    scripts/review_stats.py). لا يغير أي إعداد.
    """
    try:
        rows = local_cache_db.get_review_decisions()
    except Exception:
        return _failure('failed', "Could not read the review decisions (details in temp/search.log).",
                        "review_stats failed")
    return dict({'status': 'success'}, **local_cache_db.review_stats(rows))


# ---------------------------------------------------------------------------
# إعدادات الشيت
# ---------------------------------------------------------------------------

def action_sheet_preview(params):
    spreadsheet_url = _text(params, "spreadsheet_url")
    tab_name = _text(params, "tab_name")
    if not spreadsheet_url:
        return {"status": "failed", "error": "Spreadsheet URL or name is required"}
    try:
        sheets_client = google_sheets.get_sheets_client()
        if not sheets_client:
            return {"status": "failed", "error": "Google Sheets API connection failed"}
        sh = sheets_client.open_by_url(spreadsheet_url) if spreadsheet_url.startswith("https://") \
            else sheets_client.open(spreadsheet_url)
        worksheet = sh.worksheet(tab_name) if tab_name else sh.get_worksheet(0)
        all_values = worksheet.get_all_values()
        if not all_values:
            return {"status": "success", "headers": [], "rows": [], "columns": {}}
        return {"status": "success", "headers": all_values[0], "rows": all_values[1:6],
                "columns": google_sheets.resolve_columns(all_values[0])}
    except Exception:
        return _failure("failed", "Could not open the spreadsheet. Check the URL or name and that it is shared with "
                                  "the service account (details in temp/search.log).", "sheet_preview failed")


def action_sheet_save(params):
    spreadsheet_url = _text(params, "spreadsheet_url")
    tab_name = _text(params, "tab_name")
    if not spreadsheet_url:
        return {"status": "failed", "error": "Spreadsheet URL or name is required"}
    try:
        env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
        if os.path.exists(env_path):
            with open(env_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            updated_url = updated_tab = False
            for i, line in enumerate(lines):
                if line.strip().startswith("SPREADSHEET_NAME_OR_URL="):
                    lines[i] = f"SPREADSHEET_NAME_OR_URL=\"{spreadsheet_url}\"\n"
                    updated_url = True
                elif line.strip().startswith("SPREADSHEET_TAB_NAME="):
                    lines[i] = f"SPREADSHEET_TAB_NAME=\"{tab_name}\"\n"
                    updated_tab = True
            if not updated_url:
                lines.append(f"\nSPREADSHEET_NAME_OR_URL=\"{spreadsheet_url}\"\n")
            if not updated_tab:
                lines.append(f"SPREADSHEET_TAB_NAME=\"{tab_name}\"\n")
            with open(env_path, "w", encoding="utf-8") as f:
                f.writelines(lines)
        config.SPREADSHEET_NAME_OR_URL = spreadsheet_url
        config.SPREADSHEET_TAB_NAME = tab_name
        google_sheets.clear_cache()
        return {"status": "success", "message": "Spreadsheet configuration updated successfully"}
    except Exception:
        return _failure("failed", "Could not save the spreadsheet settings (details in temp/search.log).",
                        "sheet_save failed")


# ---------------------------------------------------------------------------
# explain_backfill: لماذا لا توجد صورة مختارة، لصفوف حُفظت قبل أن يحسبه العامل
# ---------------------------------------------------------------------------

def _brand_index(mappings):
    try:
        from catalog_match.brand_index import BrandIndex
        return BrandIndex.from_mappings(mappings or {})
    except Exception as e:
        logger.warning("تعذر بناء فهرس البراندات: %s", e)
        return mappings or {}


def action_explain_backfill(params):
    """
    يحسب outcome.explain (catalog_match.explain.explain_stored) لصفوف الطابور بانتظار المراجعة أو الفاشلة التي حُفظت
    نتيجتها قبل أن يحسبه العامل، مما حُفظ فقط (trace الصف ومرشحاته وحمولة الصف): بلا بحث وبلا تكلفة. لا يغيّر حالة أي
    صف ولا وقت تحديثه، ولا يكتب فوق نتيجة أحدث. تعيد {status, filled, checked}.
    """
    from catalog_match import explain
    try:
        rows = local_cache_db.queue_rows_missing_explain()
    except Exception:
        return _failure("failed", "Could not read the automation queue (details in temp/search.log).",
                        "explain_backfill failed")
    if not rows:
        return {"status": "success", "filled": 0, "checked": 0}
    mappings = _brand_index(_load_brand_mappings())
    vocab = explain.default_vocabulary()
    filled = 0
    for row in rows:
        try:
            candidates = local_cache_db.get_curation_candidates(
                row["row_number"], row.get("sku_key"), identity=local_cache_db.queue_row_identity(row))
            data = explain.explain_stored(row, candidates, mappings, vocab)
            if local_cache_db.set_queue_explain(row["id"], row["trace_json"], data):
                filled += 1
        except Exception:
            logger.exception("explain_backfill: row %s", row.get("row_number"))
    return {"status": "success", "filled": filled, "checked": len(rows)}


# ---------------------------------------------------------------------------
# export_run: «تصدير تقرير للتحليل» (scripts/export_run.py) — قراءة فقط، بلا بحث وبلا تكلفة
# ---------------------------------------------------------------------------

EXPORT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "temp", "exports")
EXPORT_KEEP_SECONDS = 3600


def _clean_old_exports(now=None):
    """تقارير قديمة بقيت في temp/exports (لوحة التحكم تحذف التقرير بعد تنزيله): تُحذف بعد ساعة."""
    now = now or time.time()
    try:
        for name in os.listdir(EXPORT_DIR):
            path = os.path.join(EXPORT_DIR, name)
            if name.startswith("laqta_run_") and os.path.isfile(path) and now - os.path.getmtime(path) > EXPORT_KEEP_SECONDS:
                os.remove(path)
    except OSError:
        pass


def action_export_run(params):
    """
    ملف JSON واحد بكل صفوف آخر تشغيل (scope=latest)، أو تشغيل محدد (scope=run و run_id)، أو كل ما ينتظر المراجعة
    (scope=review)، بشكل scripts/smoke_live.py --json: يُكتب في temp/exports وتعيد اسمه، ولوحة التحكم تنزّله وتحذفه.
    يُقرأ من الطابور وما حُفظ فقط (لا بحث ولا تكلفة)، وكل قيمة سرية مضبوطة تُستبدل بـ [hidden].
    """
    scripts_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    import export_run

    scope = str(params.get('scope') or 'latest')
    run_id = _text(params, 'run_id') or None
    os.makedirs(EXPORT_DIR, exist_ok=True)
    _clean_old_exports()
    name = export_run.default_name()
    try:
        _path, rows = export_run.write_export(os.path.join(EXPORT_DIR, name), scope, run_id,
                                              mappings=_brand_index(_load_brand_mappings()))
    except ValueError:
        return {'status': 'error', 'error': 'invalid scope or run id'}
    except Exception:
        return _failure('failed', "Could not export the run (details in temp/search.log).", "export_run failed")
    return {'status': 'success', 'file': name, 'rows': rows}


# ---------------------------------------------------------------------------
# ops_health (قراءة فقط: صحة البحث وتكلفته لصفحة التشخيصات)
# ---------------------------------------------------------------------------

def action_ops_health(params):
    """ملخص automation_queue.trace_json لآخر 24 ساعة و7 أيام (ops_health.summarize). لا يكتب أي شيء."""
    import ops_health
    try:
        report = ops_health.health_report()
    except Exception:
        return _failure("failed", "Could not read the automation queue (details in temp/search.log).",
                        "ops_health failed")
    return dict({"status": "success"}, **report)


# ---------------------------------------------------------------------------
# run_control (قراءة/كتابة): تشغيل جديد، إيقاف، إصلاح تشغيل عالق — لا يُحذف أي صف أبداً
# ---------------------------------------------------------------------------

# حالة عامل الخلفية كما رأتها لوحة التحكم (ApiController): العملية تُنهى في PHP لأنها تحتاج نظام التشغيل.
# exited = توقف العامل بنفسه بعد طلب الإيقاف (وكتب تقريره)؛ killed = لم يتوقف في المهلة فأُنهي
RUN_CONTROL_WORKERS = ("starting", "running", "exited", "killed", "none")
# مهلة لوحة التحكم قبل إنهاء عامل لم يلتزم بطلب الإيقاف (ApiController::$stopWaitSeconds)
STOP_WAIT_SECONDS = 90


def _kept_rows_text(queue):
    return (f"لم يُحذف أي صف: {queue.get('ready_for_review', 0)} منتج بانتظار المراجعة، "
            f"{queue.get('pending', 0)} صف في الانتظار، {queue.get('completed', 0)} معتمد، "
            f"{queue.get('failed', 0)} فاشل.")


def _stop_message(worker, result):
    kept = _kept_rows_text(result["queue"])
    if worker == "starting":
        return ("سُجل طلب الإيقاف: التشغيل ما زال يقرأ الشيت، وسيتوقف العامل فور بدئه قبل معالجة أي منتج. "
                "لم يُحذف أي صف.")
    if worker == "running":
        return ("سُجل طلب الإيقاف: سيتوقف العامل بعد إنهاء المنتجات الجارية، وتبقى باقي الصفوف في الانتظار. "
                "لم يُحذف أي صف.")
    released = result["released"]
    if worker == "exited":
        return ("تم إيقاف التشغيل: أنهى العامل المنتجات الجارية ثم توقف وكتب تقرير التشغيل، وبقيت باقي الصفوف في "
                "الانتظار. " + kept)
    if worker == "killed":
        return (f"تم إيقاف التشغيل: لم يتوقف العامل خلال {STOP_WAIT_SECONDS} ثانية فأُنهي، وكُتب تقرير «توقف» للتشغيل. "
                f"أُعيد {released} صف كان قيد المعالجة إلى الانتظار ليُعالج في التشغيل القادم. " + kept)
    if released:
        return f"لم يكن هناك تشغيل نشط. أُعيد {released} صف عالق في «قيد المعالجة» إلى الانتظار. " + kept
    return "لم يكن هناك تشغيل نشط لإيقافه، ولم يتغير أي صف."


def _reset_message(worker, result):
    lock = "بقي ملف القفل لأن العامل ما زال يعمل" if worker == "running" else "حُذف ملف القفل"
    parts = [f"تم إصلاح حالة التشغيل: {lock}، ومُسح التقدم والتنبيه والإيقاف المؤقت، وأُعيد "
             f"{result['released']} صف من «قيد المعالجة» إلى الانتظار."]
    if worker == "exited":
        parts.append("وتوقف العامل الذي كان ما زال يعمل بعد إنهاء المنتجات الجارية، وكتب تقرير التشغيل.")
    elif worker == "killed":
        parts.append(f"وأُنهي العامل الذي لم يتوقف خلال {STOP_WAIT_SECONDS} ثانية، وكُتب تقرير «توقف» للتشغيل.")
    elif worker == "starting":
        parts.append("وسُجل طلب إيقاف للتشغيل الذي كان يقرأ الشيت كي لا يبدأ المعالجة.")
    elif worker == "running":
        parts.append("وسُجل طلب إيقاف للعامل الذي تعذر إنهاؤه، فيتوقف بعد المنتجات الجارية.")
    parts.append(_kept_rows_text(result["queue"]))
    return " ".join(parts)


def _killed_run_report(op):
    """
    العامل أُنهي قسراً (taskkill /F أو kill -9) فلم تعمل كتلة finally فيه ولا التشغيل الليلي: لا تقرير ولا صف في سجل
    التشغيلات ولا تكلفة. يكتب run_control تقرير «توقف» بدلاً منه من قفله (من بدأه ومتى، و run_id و worker_id اللذان
    سجلهما العامل مع النبض)، قبل أن تحذف اللوحة القفل. تعيد رقم صف run_history أو None؛ لا ترفع أبداً.
    """
    try:
        automation = _pipeline()
        lock = automation.read_lock(automation.LOCK_FILE)
        if not lock or lock.get("kind") not in ("json", "pid"):
            return None
        import run_report
        trigger = lock.get("trigger") or ("nightly" if lock.get("role") == "nightly" else "dashboard")
        info = {"stop_reason": "stopped", "run_id": lock.get("run_id"), "worker_id": lock.get("worker_id"),
                "started_ts": lock.get("started_ts"), "ended_ts": time.time(),
                "notice": f"STOPPED: لم يتوقف العامل خلال {STOP_WAIT_SECONDS} ثانية بعد طلب الإيقاف فأنهته لوحة "
                          f"التحكم ({op})؛ عادت الصفوف قيد المعالجة إلى الانتظار"}
        return run_report.report_worker_run(info, trigger=trigger).get("history_id")
    except Exception:
        logger.exception("run_control %s: the stopped run's report could not be written", op)
        return None


def action_run_control(params):
    """
    التحكم في تشغيل الأتمتة من لوحة التحكم (local_cache_db). op:
    - start: قبل إطلاق تشغيل جديد (prepare_run): حالة 'starting' بلا أرقام التشغيل السابق، وإلغاء طلبي
      الإيقاف والإيقاف المؤقت القديمين.
    - stop: زر «إيقاف التشغيل» (stop_run). worker: starting | running (العامل لم يبدأ أو ما زال حياً: طلب إيقاف
      يلتزم به) أو exited | killed | none (توقف بنفسه، أو أُنهي، أو لم يكن يعمل: الصفوف قيد المعالجة تعود للانتظار
      وتُضبط الحالة).
    - reset: زر «إصلاح تشغيل عالق» (reset_run). worker=starting | running يسجل طلب إيقاف للإدراج الذي قد يكون
      ما زال يعمل أو للعامل الذي لم يُنهَ.
    worker=killed: يُكتب تقرير «توقف» للتشغيل (_killed_run_report)، فالعامل المُنهى لم يكتبه.
    لا يحذف أي صف أو مرشح أو قرار مراجعة. الاستجابة: {status, op, message (عربية), released, stop_requested,
    state, queue} و history_id لتقرير العامل المُنهى.
    """
    op = _text(params, 'op')
    worker = _text(params, 'worker') or "none"
    if op not in ("start", "stop", "reset"):
        return {'status': 'error', 'error': f"invalid op {op!r}", 'allowed': ["start", "stop", "reset"]}
    if worker not in RUN_CONTROL_WORKERS:
        return {'status': 'error', 'error': f"invalid worker {worker!r}", 'allowed': list(RUN_CONTROL_WORKERS)}
    if op == "start":
        if not local_cache_db.prepare_run():
            return {'status': 'failed', 'op': op,
                    'error': "تعذر تجهيز التشغيل في قاعدة البيانات؛ لم يبدأ أي تشغيل (التفاصيل في temp/search.log)."}
        return {'status': 'success', 'op': op, 'message': "تم تجهيز تشغيل جديد."}
    worker_active = worker in ("starting", "running")
    try:
        if op == "stop":
            result = local_cache_db.stop_run(worker_active=worker_active)
            message = _stop_message(worker, result)
        else:
            result = local_cache_db.reset_run(worker_active=worker_active)
            message = _reset_message(worker, result)
    except Exception:
        if worker == "killed":
            _killed_run_report(op)
        return _failure('failed', "تعذر تعديل حالة التشغيل في قاعدة البيانات؛ لم يتغير أي صف "
                                  "(التفاصيل في temp/search.log).", f"run_control {op} failed")
    out = {'status': 'success', 'op': op, 'message': message, 'released': result['released'],
           'stop_requested': result['stop_requested'], 'state': result['status'], 'queue': result['queue']}
    if worker == "killed":
        out['history_id'] = _killed_run_report(op)
    return out


# ---------------------------------------------------------------------------
# lock_state (قراءة فقط): حالة قفل العامل بقاعدة main.lock_verdict نفسها. لوحة التحكم تسأل هنا بدل أن تعيد كتابة
# القاعدة في PHP (اختلفت القاعدتان: رقم عملية معاد كان «يعمل» في اللوحة و«متروكاً» في بايثون، فأُنهيت عملية أخرى)
# ---------------------------------------------------------------------------

# 'STARTING' (إدراج لوحة التحكم) يُعد تشغيلاً بهذا العمر فقط (نفس ApiController::pipelineProcess و run_nightly)
STARTING_GRACE_S = 300


def action_lock_state(params):
    """
    حالة temp/pipeline.lock كما يراها العامل والتشغيل الليلي. lock: مسار القفل (افتراضياً قفل هذا المشروع؛ يُقبل
    ملف اسمه pipeline.lock فقط). لا يحذف القفل ولا يغير شيئاً. الاستجابة: {status, state: none | starting | running,
    pid, verified (هوية العملية مؤكدة: سطر الأوامر ووقت البدء؛ وحدها تسمح للوحة بإنهائها), reason (سبب اعتبار القفل
    متروكاً)، probe_error, role, trigger, run_id, worker_id, started_ts, heartbeat_age_s, lock_exists}.
    """
    automation = _pipeline()
    path = _text(params, 'lock') or automation.LOCK_FILE
    if os.path.basename(path) != "pipeline.lock":
        return {'status': 'error', 'error': "lock must name a pipeline.lock file"}
    lock = automation.read_lock(path)
    out = {'status': 'success', 'state': 'none', 'pid': None, 'verified': False, 'reason': '', 'probe_error': None,
           'role': None, 'trigger': None, 'run_id': None, 'worker_id': None, 'started_ts': None,
           'heartbeat_age_s': None, 'lock_exists': lock is not None}
    if lock is None:
        return out
    if lock['kind'] == 'starting':
        if time.time() - lock['mtime'] < STARTING_GRACE_S:
            out['state'] = 'starting'
        else:
            out['reason'] = f"STARTING أقدم من {STARTING_GRACE_S // 60} دقائق"
        return out
    verdict = automation.lock_verdict(lock)
    age = verdict['heartbeat_age']
    out.update(state='none' if verdict['stale'] else 'running', pid=lock.get('pid'), verified=verdict['verified'],
               reason=verdict['stale'] or '', probe_error=verdict['probe_error'], role=lock.get('role'),
               trigger=lock.get('trigger'), run_id=lock.get('run_id'), worker_id=lock.get('worker_id'),
               started_ts=lock.get('started_ts'), heartbeat_age_s=None if age is None else int(age))
    return out


# ---------------------------------------------------------------------------
# publish_check («فحص النشر» بصفحة الصحة): بروفة سلسلة النشر على صورة تجريبية بدون ما يلمس أي منتج
# ---------------------------------------------------------------------------

def action_publish_check(params):
    """
    publish_check.run_publish_check: تنزيل صورة منتج بانتظار المراجعة (أو الصورة التجريبية)، عزل خلفيتها بملف
    المعالجة، رفعها إلى laqta_selftest/publish_check ثم مسحها، وكتابة عنوان عمود الرابط بالشيت بقيمته نفسها. لا يكتب
    بأي صف منتج ولا طابور ولا إعدادات. الاستجابة: {status: success, ok, overall, steps, summary_ar, ...} (عربية،
    بلا أي مفتاح)، وتُحفظ في temp/publish_check_last.json.
    """
    import publish_check
    try:
        result = publish_check.run_publish_check()
    except Exception:
        return _failure("failed", "ما قدرنا نشغّل فحص النشر (التفاصيل في temp/search.log).", "publish_check failed")
    return dict({"status": "success"}, **result)


def action_bg_methods(params):
    """
    طرق عزل الخلفية المحلية المجانية المنزّلة على هالجهاز (image_processor.local_methods_available): {status, local:
    {grabcut, rembg}}. تبويب «معالجة الصور» يذكر GrabCut و rembg فقط إذا كانت هون (لا وعد بشي مش منزّل). للقراءة فقط.
    """
    return {"status": "success", "local": image_processor.local_methods_available()}


# ---------------------------------------------------------------------------
# local_index_refresh («حدّث الفهرس هلق» ببطاقة «فهرس المتاجر المحلي» بصفحة الصحة): تحديث الفهرس المحلي كمهمة خلفية
# ---------------------------------------------------------------------------

def action_local_index_refresh(params):
    """
    يبدأ نفس تحديث الفهرس المحلي اللي بيشغّله التشغيل الليلي والعامل (catalog_match.index_refresh) كمهمة بعملية مستقلة
    (scripts/build_catalog_index.py --refresh --force) ويرجع فوراً: {status: success, started, running, message_ar}.
    started=False مع running=True: في تحديث شغّال أصلاً. بيقرأ خرايط المتاجر بس (robots.txt وCrawl-delay محفوظين، متجر
    رد «ممنوع» بيتخطى 7 أيام)؛ ما بيكتب بالشيت ولا بـ Cloudinary ولا بيصرف أي بحث مدفوع. التقدم بيظهر بالبطاقة عند التحديث.
    """
    from catalog_match import index_refresh

    result = index_refresh.start_detached(trigger="dashboard", force=True)
    messages = {
        "started": "بلّش تحديث الفهرس بالخلفية. افتح الصفحة بعد شوي لتشوف التقدم.",
        "running": "في تحديث للفهرس شغّال هلق. افتح الصفحة بعد شوي لتشوف التقدم.",
        "disabled": "الفهرس المحلي مطفي بالإعدادات: شغّله أول من تبويب «متقدم».",
        "unavailable": "ما قدرنا نبدأ تحديث الفهرس: شوف صفحة الأخطاء.",
    }
    return {"status": "success", "started": bool(result.get("started")), "running": bool(result.get("running")),
            "reason": result.get("reason"), "message_ar": messages.get(result.get("reason"), "")}


# ---------------------------------------------------------------------------
# «ماركات ناقصة من Brands Mapping» (catalog_match/brand_assistant.py): brand_suggestions (قراءة فقط، بلا بحث مدفوع)،
# brand_official_site (استعلام Serper واحد بزر صريح)، brand_add (يكتب شيت المالك، بزر صريح فقط)
# ---------------------------------------------------------------------------

def _brand_invalid(code, message, field=""):
    return {"status": "invalid", "code": code, "field": field, "error": message}


def action_brand_suggestions(params):
    """
    الماركات التي تذكرها صفوف الطابور وما لها صف في ورقة Brands Mapping: [{brand, rows, brand_ar, synonyms,
    official_domain: ''}]، الأكثر صفوفاً أولاً. synonyms كتابات المتاجر التي اكتشفها البحث (trace outcome.
    discovered_brands) أو تعلّمها من المراجعة. قراءة فقط: لا بحث مدفوع ولا كتابة بالشيت. ورقة ما انقرت = خطأ (ما نقترح
    كل الماركات على أساس شيت ما شفناه).
    """
    from catalog_match import brand_assistant
    try:
        rows = local_cache_db.queue_brand_rows()
    except Exception:
        return _failure("failed", "Could not read the automation queue (details in temp/search.log).",
                        "brand_suggestions failed")
    try:
        client = google_sheets.get_sheets_client()
        if not client:
            raise RuntimeError("Google Sheets API connection failed")
        mappings = google_sheets.sheet_brand_mappings(client, config.SPREADSHEET_NAME_OR_URL) or {}
    except Exception:
        return _failure("failed", "Could not read the Brands Mapping sheet (details in temp/search.log).",
                        "brand_suggestions sheet read failed")
    try:
        aliases = local_cache_db.get_learned_brand_aliases()
    except Exception as e:
        logger.warning("brand_suggestions: learned spellings unavailable: %s", e)
        aliases = []
    try:
        brands = brand_assistant.suggestions(rows, mappings, aliases)
    except Exception:
        return _failure("failed", "Could not work out the missing brands (details in temp/search.log).",
                        "brand_suggestions failed")
    return {"status": "success", "brands": brands, "rows": len(rows)}


def action_brand_official_site(params):
    """
    «اقترح الموقع الرسمي»: استعلام Serper ويب واحد بالضبط `"<brand>" official website` (بلا hedging، ويُسجل في سجل الصرف
    مثل كل بحث؛ ما أُجيب عنه فقط يُحتسب)، ثم أول نتيجتين مو متجر ولا سوق ولا شبكة اجتماعية ولا موقع صور، وفي نطاقها أو
    عنوان صفحتها كلمة الماركة الرئيسية: {status: success, brand, candidates: [{title, domain, url}], queries: 1}.
    لا يكتب شيئاً.
    """
    from catalog_match import brand_assistant, settings as cm_settings
    from catalog_match.providers.serper_web import SerperWebProvider, parse_organic
    try:
        brand = brand_assistant.clean_brand(_text(params, "brand"))
    except brand_assistant.BrandRequestError as e:
        return _brand_invalid(e.code, str(e), e.field)
    if not cm_settings.serper_api_key():
        return {"status": "unavailable", "code": "no_key", "error": "No Serper key is configured."}
    provider = SerperWebProvider()
    provider.hedge = False                           # exactly one query, one credit
    try:
        results = parse_organic(provider._request(brand_assistant.search_query(brand), "en"))
    except Exception:
        return _failure("failed", "The search did not answer (details in temp/search.log).", "brand_official_site failed")
    local_cache_db.record_search_spend(
        {"provider_health": [{"provider": "serper_web", "status": "ok" if results else "empty", "hedges": 0}]},
        brand_assistant.OFFICIAL_SITE_SPEND_RUN)
    candidates = brand_assistant.official_site_candidates(
        [{"link": c.page_url, "title": c.title} for c in results], brand)
    return {"status": "success", "brand": brand, "candidates": candidates, "queries": 1}


def action_brand_add(params):
    """
    يضيف ماركة لورقة 'Brands Mapping' بصف واحد (Brand, Synonyms, ..., Official domains): {brand, synonyms: [...],
    official_domains: [...]} أو {items: [{...}, ...]} لـ «أضف الكل» (صف لكل ماركة، بطلب كتابة واحد). يكتب شيت المالك:
    يُستدعى فقط من زر صريح. يتحقق: الماركة غير فاضية ولا مكتوبة أصلاً (بعد قراءة طازجة للورقة، بلا اعتبار للحالة أو
    الفراغات)، كل موقع نطاق صرف بلا بروتوكول ولا مسار، وعشرة مرادفات كحد أقصى. كاش الماركات يُحذف ليراها التشغيل
    الجاي، ومواقع الماركات تنضاف لطابور الفهرسة (system_settings.pending_harvest_domains) لتفهرس أول التشغيل الجاي.
    """
    from catalog_match import brand_assistant as ba
    raw = params.get("items") if isinstance(params.get("items"), list) else [params]
    if not raw or len(raw) > ba.MAX_BATCH:
        return _brand_invalid("too_many_brands", "Between 1 and %d brands at a time." % ba.MAX_BATCH, "items")
    items = []
    try:
        for entry in raw:
            if not isinstance(entry, dict):
                return _brand_invalid("invalid_brand", "Each brand must be an object.", "brand")
            items.append(ba.validate_brand_request(entry))
    except ba.BrandRequestError as e:
        return _brand_invalid(e.code, str(e), e.field)
    try:
        client = google_sheets.get_sheets_client()
        if not client:
            raise RuntimeError("Google Sheets API connection failed")
        res = google_sheets.add_brand_mappings(client, config.SPREADSHEET_NAME_OR_URL, items)
    except google_sheets.SheetTransientError:
        return _failure("failed", "Google Sheets is temporarily unavailable (quota or a Google server error). "
                                  "Nothing was written; try again in a minute.", "brand_add failed")
    except Exception:
        return _failure("failed", "Could not write the Brands Mapping sheet. Check that it is shared with the service "
                                  "account (details in temp/search.log).", "brand_add failed")
    added = res.get("added") or []
    if not added:
        return {"status": "duplicate", "code": "duplicate", "error": "The brand is already in Brands Mapping.",
                "skipped": res.get("skipped") or []}
    domains = [d for item in items if item["brand"] in added for d in item["official_domains"]]
    queued = []
    if domains:
        try:
            local_cache_db.add_pending_harvest_domains(domains)
            queued = domains
        except Exception as e:
            logger.warning("brand_add: the sites could not be queued for indexing: %s", e)
    return {"status": "success", "added": added, "skipped": res.get("skipped") or [], "harvest_queued": queued}


# ---------------------------------------------------------------------------
# «باركودات من صفحات المتاجر»: barcode_suggestions (قراءة فقط) و barcode_write (طابور كتابة الشيت)
# ---------------------------------------------------------------------------

BARCODE_WRITE_MAX = 300
NO_BARCODE_COLUMN_ERROR = "الشيت ما فيه عمود باركود (Barcode أو EAN أو GTIN). ما كتبنا شي، وما منضيف أعمدة للشيت."


def _barcode_entry(row_number, sku_key, name, brand, size, barcode, record, gtin):
    from catalog_match.text_norm import url_host
    page = str(record.get("page_gtin_url") or "").strip()
    return {"row": int(row_number), "sku_key": sku_key, "name": str(name or "").strip(),
            "brand": str(brand or "").strip(), "size": str(size or "").strip(), "gtin": gtin,
            "domain": (url_host(page) or "") if page else "", "page_url": page,
            "sheet_barcode": str(barcode or "").strip(), "duplicate": False, "reason": None}


def _barcode_plan(records):
    """
    الصفوف المعتمدة التي حُفظ معها باركود صفحة المتجر (records: local_cache_db.get_page_barcodes) وخلية باركودها في
    الشيت ما زالت فارغة أو غير صالحة، من صفوف الشيت المخزنة (google_sheets.cached_products، بلا طلب لـ Google)، وإلا من
    هوية صفوف الطابور. {source, rows, duplicates, filled}: rows تُكتب؛ duplicates باركود يتشاركه صفان (أو مكتوب لصف
    آخر بالشيت): لا يُكتب؛ filled خلية فيها نص غير باركود: لا يُكتب فوقها.
    """
    from catalog_match.gtin import display_gtin, is_global_gtin, normalize_gtin
    pipeline = _pipeline()
    approvals = {}
    for rec in records or ():
        gtin = display_gtin(rec.get("page_gtin"))
        if gtin and is_global_gtin(gtin) and rec.get("sku_key"):
            approvals[str(rec["sku_key"])] = (rec, gtin)
    products = google_sheets.cached_products()
    source = "sheet_cache" if products is not None else "queue"
    found, taken = [], {}
    if products is not None:
        for prod in products:
            barcode = str(prod.get("barcode") or "").strip()
            gtin14 = normalize_gtin(barcode)[0] if barcode else None
            if gtin14:
                taken.setdefault(gtin14, set()).add(prod.get("row_number"))
                continue                                     # the row has a valid barcode already
            if not approvals or prod.get("row_number") is None:
                continue
            try:
                key = pipeline.compute_sku_key(pipeline.sku_row(
                    prod.get("product_name"), prod.get("brand"), barcode, {
                        "name_ar": prod.get("product_name_ar", ""), "brand_ar": prod.get("brand_ar", ""),
                        "category": prod.get("category", ""), "size": prod.get("size", "")}))
            except Exception as e:
                logger.warning("barcode_suggestions: no sku_key for row %s: %s", prod.get("row_number"), e)
                continue
            if key in approvals:
                rec, gtin = approvals[key]
                found.append(_barcode_entry(prod["row_number"], key, prod.get("product_name"), prod.get("brand"),
                                            prod.get("size"), barcode, rec, gtin))
    else:
        for key, (rec, gtin) in approvals.items():
            barcode = str(rec.get("queue_barcode") or "").strip()
            if rec.get("row_number") is None or (barcode and normalize_gtin(barcode)[0]):
                continue
            payload = pipeline.task_payload({"payload_json": rec.get("queue_payload")})
            found.append(_barcode_entry(rec["row_number"], key, rec.get("queue_name") or rec.get("product_name"),
                                        rec.get("queue_brand") or rec.get("brand"), payload.get("size"), barcode, rec,
                                        gtin))
    counts = {}
    for e in found:
        e["gtin14"] = normalize_gtin(e["gtin"])[0]
        counts[e["gtin14"]] = counts.get(e["gtin14"], 0) + 1
    for e in found:
        gtin14 = e.pop("gtin14")
        if counts[gtin14] > 1 or taken.get(gtin14):
            e["duplicate"], e["reason"] = True, "duplicate"
        elif e["sheet_barcode"]:
            e["reason"] = "cell_not_empty"
    found.sort(key=lambda e: (e["row"], e["name"]))
    return {"source": source, "rows": [e for e in found if not e["reason"]],
            "duplicates": [e for e in found if e["reason"] == "duplicate"],
            "filled": [e for e in found if e["reason"] == "cell_not_empty"]}


def action_barcode_suggestions(params):
    """
    «باركودات لقيناها من صفحات المتاجر»: الصفوف المعتمدة التي ذكرت صفحة متجر صورتها باركوداً صالحاً عالمياً
    (resolved_products.page_gtin) وخلية باركودها في الشيت فارغة أو غير صالحة: {status, source, count, rows,
    duplicates, filled}، كل صف {row, sku_key, name, brand, size, gtin, domain, page_url, sheet_barcode, duplicate,
    reason}. قراءة فقط: صفوف الشيت المخزنة، بلا طلب لـ Google وبلا كتابة.
    """
    try:
        plan = _barcode_plan(local_cache_db.get_page_barcodes())
    except Exception:
        return _failure("failed", "Could not read the approved barcodes (details in temp/search.log).",
                        "barcode_suggestions failed")
    count = len(plan["rows"]) + len(plan["duplicates"]) + len(plan["filled"])
    return dict({"status": "success", "count": count}, **plan)


def _barcode_items(raw):
    """[{row, sku_key, gtin}] من الطلب، أو None لطلب غير صالح."""
    if not isinstance(raw, list) or not 1 <= len(raw) <= BARCODE_WRITE_MAX:
        return None
    items = []
    for entry in raw:
        if not isinstance(entry, dict):
            return None
        try:
            row = int(entry.get("row"))
        except (TypeError, ValueError):
            return None
        sku = str(entry.get("sku_key") or "").strip()
        gtin = str(entry.get("gtin") or "").strip()
        if row < 2 or not sku or len(sku) > 64 or not gtin or len(gtin) > 32:
            return None
        items.append({"row": row, "sku_key": sku, "gtin": gtin})
    return items


def _barcode_outcome(outcome):
    """(written | queued | skipped، سبب التخطي) لنتيجة كتابة في الطابور (google_sheets.outbox_outcomes)."""
    status = str((outcome or {}).get("status") or "").upper()
    if status == "SYNCED":
        return "written", None
    if status in ("", "PENDING", "FAILED"):
        return "queued", None
    error = str(outcome.get("error") or "")
    if error.startswith("cell_not_empty"):
        return "skipped", "cell_not_empty"
    if "mismatch" in error or "match" in error or status == "SKIPPED_OUT_OF_BOUNDS":
        return "skipped", "row_changed"
    return "skipped", "sheet_refused"


def action_barcode_write(params):
    """
    «اكتب الباركودات المختارة بالشيت»: {items: [{row, sku_key, gtin}]}. لكل صف: الباركود صالح (رقم التحقق) ويساوي
    page_gtin المحفوظ مع اعتماد هذا الـ sku_key، والصف ما زال من اقتراحات barcode_suggestions (خلية فارغة، بلا صف آخر
    يتشارك الباركود). عمود الباركود بعنوانه (google_sheets.resolve_columns)؛ بدونه رفض واضح ولا يُضاف عمود. كتابة
    واحدة لكل صف في طابور الشيت بهوية الصف (الاسم والحجم والبراند)، ثم تفريغ: صف تغيّر منتجه أو صارت خلية باركوده غير
    فارغة يُتخطى ولا يُكتب فوقه. صف كُتب: صف الطابور يأخذ مفتاح الباركود (local_cache_db.rekey_queue_rows).
    {status, written, queued, skipped: [{row, sku_key, gtin, reason}], rows_written}.
    """
    from catalog_match.gtin import display_gtin, is_global_gtin, normalize_gtin
    items = _barcode_items(params.get("items"))
    if items is None:
        return {"status": "invalid", "code": "bad_items",
                "error": "items must be 1 to %d rows of {row, sku_key, gtin}" % BARCODE_WRITE_MAX}
    try:
        records = local_cache_db.get_page_barcodes()
        plan = _barcode_plan(records)
    except Exception:
        return _failure("failed", "Could not read the approved barcodes (details in temp/search.log).",
                        "barcode_write failed")
    stored = {str(rec.get("sku_key")): display_gtin(rec.get("page_gtin")) for rec in records}
    listed = {(e["row"], e["sku_key"]): e for e in plan["rows"] + plan["duplicates"] + plan["filled"]}
    skipped, ready, seen = [], [], set()
    for it in items:
        gtin14 = normalize_gtin(it["gtin"])[0]
        entry = listed.get((it["row"], it["sku_key"]))
        if not gtin14 or not is_global_gtin(gtin14):
            reason = "bad_checksum"
        elif not stored.get(it["sku_key"]) or stored[it["sku_key"]] != display_gtin(gtin14):
            reason = "gtin_mismatch"
        elif it["row"] in seen:
            reason = "repeated"
        elif entry is None:
            reason = "row_changed"
        else:
            reason = entry["reason"]
        if reason:
            skipped.append({"row": it["row"], "sku_key": it["sku_key"], "gtin": it["gtin"], "reason": reason})
            continue
        seen.add(it["row"])
        ready.append(entry)
    out = {"status": "success", "written": 0, "queued": 0, "skipped": skipped, "rows_written": []}
    if not ready:
        return out
    try:
        worksheet = _open_sheet()
        headers = google_sheets._worksheet_headers(worksheet, fresh=True)
    except Exception:
        return _failure("failed", "Could not open the Google Sheet; nothing was written (details in temp/search.log).",
                        "barcode_write sheet open failed")
    if google_sheets.resolve_columns(headers).get("barcode", -1) == -1:
        return {"status": "refused", "code": "no_barcode_column", "error": NO_BARCODE_COLUMN_ERROR}
    since = local_cache_db.outbox_max_id()
    ids = google_sheets.queue_barcode_writes([
        {"row_number": e["row"], "value": e["gtin"], "barcode": e["sheet_barcode"], "product_name": e["name"],
         "size": e["size"], "brand": e["brand"]} for e in ready])
    try:
        google_sheets.flush_outbox(worksheet, lock_timeout=30)
    except Exception:
        logger.exception("barcode_write: the flush failed; the writes stay in the outbox")
    try:
        outcomes = {o["id"]: o for o in google_sheets.outbox_outcomes(
            sorted(ids), since_id=since, limit=len(ids) * 4 + 200)}
    except Exception:
        logger.exception("barcode_write: the outbox outcomes could not be read")
        outcomes = {}
    rekey = []
    for e in ready:
        found = outcomes.get(ids.get(e["row"]))
        kind, reason = _barcode_outcome(found)
        if kind == "skipped":
            skipped.append({"row": e["row"], "sku_key": e["sku_key"], "gtin": e["gtin"], "reason": reason})
            continue
        out[kind] += 1
        if kind == "written":
            out["rows_written"].append(found.get("row") or e["row"])
            rekey.append((e["row"], e["sku_key"], normalize_gtin(e["gtin"])[0], e["gtin"]))
    if rekey:
        try:
            local_cache_db.rekey_queue_rows(rekey)
        except Exception:
            logger.exception("barcode_write: the queue rows keep their old key until the next run")
    return out


ACTIONS = {
    'get_products': action_get_products,
    'search': action_search,
    'select_image': action_select_image,
    'upload_manual_image': action_upload_manual_image,
    'reject_image': action_reject_image,
    'undo_reject': action_undo_reject,
    'review_stats': action_review_stats,
    'sheet-preview': action_sheet_preview,
    'sheet-save': action_sheet_save,
    'explain_backfill': action_explain_backfill,
    'brand_suggestions': action_brand_suggestions,
    'brand_official_site': action_brand_official_site,
    'brand_add': action_brand_add,
    'barcode_suggestions': action_barcode_suggestions,
    'barcode_write': action_barcode_write,
    'export_run': action_export_run,
    'lock_state': action_lock_state,
    'publish_check': action_publish_check,
    'bg_methods': action_bg_methods,
    'local_index_refresh': action_local_index_refresh,
    'ops_health': action_ops_health,
    'run_control': action_run_control,
}


def _configure_logging(stream):
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "_cli_bridge", False):
            root.removeHandler(handler)
    handler = logging.StreamHandler(stream)
    handler._cli_bridge = True
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.addHandler(handler)
    if root.level > logging.INFO or root.level == logging.NOTSET:
        root.setLevel(logging.INFO)
    return handler


def main(argv=None):
    """تنفيذ إجراء واحد وكتابة وثيقة JSON واحدة على stdout الحقيقي."""
    argv = list(sys.argv if argv is None else argv)
    json_out = _JSON_STDOUT or sys.stdout
    previous_stdout = sys.stdout
    own_stream = _JSON_STDOUT is None or sys.stdout is _JSON_STDOUT
    log_stream = _open_log_stream() if own_stream else sys.stdout
    sys.stdout = log_stream
    handler = _configure_logging(log_stream)
    previous_cwd = os.getcwd()
    exit_code = 0
    try:
        # مسارات temp/ النسبية (مخزن المرشحات، السجلات) تُحسب من مجلد المشروع مهما كان مجلد PHP الحالي
        os.chdir(os.path.dirname(os.path.abspath(__file__)))
        if len(argv) < 2:
            result, exit_code = {'status': 'error', 'error': 'No action specified'}, 1
        elif argv[1] not in ACTIONS:
            result, exit_code = {'status': 'error', 'error': f'Unknown action: {argv[1]}'}, 1
        else:
            saved_argv = sys.argv
            sys.argv = argv
            try:
                params = decode_params()
            finally:
                sys.argv = saved_argv
            try:
                result = ACTIONS[argv[1]](params)
            except Exception as e:
                config.log_error_to_laravel(f"CLI main exception for action '{argv[1]}': {e}\n{traceback.format_exc()}",
                                            level="ERROR")
                result = {'status': 'error', 'error': "The action failed (details on the Errors page)."}
    finally:
        os.chdir(previous_cwd)
        logging.getLogger().removeHandler(handler)
        sys.stdout = previous_stdout
        if own_stream and log_stream is not sys.stderr:
            try:
                log_stream.close()
            except Exception:
                pass
    json_out.write(json.dumps(result, default=str) + "\n")
    json_out.flush()
    return exit_code


if __name__ == '__main__':
    sys.exit(main())
