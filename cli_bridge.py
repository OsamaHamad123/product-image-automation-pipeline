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
    for prod in products:
        try:
            prod["sku_key"] = pipeline.compute_sku_key(pipeline.sku_row(
                prod.get("product_name"), prod.get("brand"), prod.get("barcode"), {
                    "name_ar": prod.get("product_name_ar", ""), "brand_ar": prod.get("brand_ar", ""),
                    "category": prod.get("category", ""), "size": prod.get("size", "")}), brand_mappings)
        except Exception as e:
            logger.warning("تعذر حساب sku_key للصف %s: %s", prod.get("row_number"), e)
        barcode = (prod.get("barcode") or "").strip()
        alt_barcode = f"ERR_{prod.get('product_name')}_{prod.get('brand')}".replace(" ", "_")
        failure = failures.get(barcode) if barcode else None
        failure = failure or failures.get(alt_barcode)
        prod["has_error"] = bool(failure)
        prod["error_message"] = failure["error_message"] if failure else ""
    return {'status': 'success', 'products': products}


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


def action_search(params, brand_mappings=None):
    """
    بحث تفاعلي لمنتج واحد. الاستجابة (عقد ثابت للوحة التحكم):
    {status: success|review|not_found|provider_down|error, decision, failure_code, selected_image,
     candidates (أفضل 8 مع status/reasons/warnings/evidence/vlm/scores), provider_health, sku_key, trace}
    warnings: رموز تحذير المراجعة للصورة المرشحة (مثل foreign_store) ليتحقق منها المراجع قبل الاعتماد.
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
        return {'status': 'error', 'error': "Search failed (details in temp/search.log).", 'decision': None,
                'failure_code': 'SEARCH_ERROR',
                'selected_image': None, 'candidates': [], 'provider_health': [], 'sku_key': sku_key, 'trace': trace}

    outcome = trace.get('outcome') if isinstance(trace.get('outcome'), dict) else {}
    decision = (best or {}).get('decision') or outcome.get('decision')
    if best and not decision:
        decision = "REVIEW_PRESELECTED"   # مسار v1: لا يُنشر تلقائياً أبداً
    failure_code = (best or {}).get('failure_code') or outcome.get('failure_code')
    return {
        'status': _status_for(decision, bool(best)),
        'decision': decision,
        'failure_code': failure_code,
        'selected_image': best if (best and decision in SELECTABLE_DECISIONS) else None,
        'candidates': _candidates_for_response(best, trace),
        'provider_health': outcome.get('provider_health') or [],
        'sku_key': (best or {}).get('sku_key') or outcome.get('sku_key') or sku_key,
        'exclusions': {'urls': len(exclude_urls), 'phashes': len(rejected_phashes)},
        'trace': trace,
        'brand': brand,
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


def _identity_problem(params, row_number):
    """
    يتحقق من sku_key والباركود المطلوبين. يعيد (sku_key, barcode, خطأ أو None).
    الباركود لا يُقارن بنسخة الطابور القديمة: الكتابة في الشيت تتحقق من هوية الصف الحي عند التنفيذ،
    وتصحيح المالك للباركود لا يجب أن يمنع الاعتماد.
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
    return sku_key, barcode, None


def _candidate_sha(params, row_number, sku_key, image_url):
    """
    بصمة بايتات المرشح التي تم التحقق منها. الواجهة ترسل candidate_sha256 (والاسم القديم content_sha256)؛
    عند غيابهما تُؤخذ من مرشحات هذا المنتج المحفوظة لنفس الرابط.
    """
    sha = _text(params, 'candidate_sha256') or _text(params, 'content_sha256')
    if sha:
        return sha
    for c in local_cache_db.get_curation_candidates(row_number, sku_key=sku_key or None):
        if c.get("image_url") == image_url and c.get("content_sha256"):
            return c["content_sha256"]
    return None


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


def _record_review(action, params, row_number, sku_key, image_url=None, reason_code=None, approval=None):
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
    try:
        stored = local_cache_db.get_curation_candidates(row_number, sku_key=sku_key or None)
        candidates = [c for c in stored if not _is_cache_hit(c)]      # ما اختاره المحرك فقط
        acted = next((c for c in candidates if image_url and c.get("image_url") == image_url), None)
        decision = was_preselected = None
        if candidates and (acted is not None or action == "manual_upload"):
            has_precheck = any(c.get("status") == "preselected" for c in candidates)
            decision = "REVIEW_PRESELECTED" if has_precheck else "REVIEW_UNSELECTED"
        if acted is not None:
            was_preselected = acted.get("status") == "preselected"
        elif action == "manual_upload":
            was_preselected = False
        view = _reviewer_view(params, action)
        if view is not None:
            # شاشة الكتالوج ترسل ما رآه المراجع فعلاً؛ قد يختلف عن آخر تشغيل محفوظ للعامل على نفس الصنف
            decision, was_preselected = view
            acted = {"identity_tier": _text(params, 'identity_tier') or None,
                     "vlm": {"decision": _text(params, 'vlm_decision') or None}, "page_url": _text(params, 'page_url')}
        if approval and approval.get("verification_status") == "auto_verified":
            decision, was_preselected = "AUTO_PUBLISH", True
        acted = acted or {}
        first = stored[0] if stored else {}
        vlm = acted.get("vlm") if isinstance(acted.get("vlm"), dict) else {}
        local_cache_db.add_review_decision(
            action, sku_key=sku_key, row_number=row_number,
            brand=_text(params, 'brand') or first.get("brand"),
            product_name=_text(params, 'product_name') or first.get("product_name"),
            image_url=image_url, page_domain=_page_domain(acted, params),
            identity_tier=acted.get("identity_tier") or (acted.get("evidence") or {}).get("tier"),
            engine_decision=decision, was_preselected=was_preselected, vlm_decision=vlm.get("decision"),
            reason_code=reason_code,
        )
    except Exception:
        logger.exception("تعذر تسجيل قرار المراجع (%s) للصف %s", action, row_number)


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

    pipeline = _pipeline()
    queue_started = False
    try:
        google_sheets.init_async_queue(config.CREDENTIALS_FILE, config.SPREADSHEET_NAME_OR_URL)
        queue_started = True
        worksheet = _open_sheet()
        link_column_index = google_sheets.find_link_column(worksheet)
        res = pipeline.publish_image(
            image_url, product_name, brand, row_number, worksheet, link_column_index,
            barcode=barcode, candidate_sha256=_candidate_sha(params, row_number, sku_key, image_url),
            bg_method=_text(params, 'bg_removal_method') or None,
            target=(int(params.get('target_width') or 0), int(params.get('target_height') or 0)),
            category_override={k: _text(params, k) for k in ('category_l1_en', 'category_l2_en', 'category_l3_en')},
            enhance=_as_bool(params.get('enhance', False)), key_size=_text(params, 'size') or None,
        )
        if res["status"] == "failed":
            return {'status': 'failed', 'error': res.get('error'), 'isolated': res.get('isolated', False)}

        local_cache_db.save_product_resolution(
            barcode, product_name, brand, image_url, res["link"], None, res.get("metadata"),
            verification_status="human_approved", approved_by="human", sku_key=sku_key,
        )
        local_cache_db.update_task_status_by_row(row_number, "completed", sku_key=sku_key)
        _record_review("approved", params, row_number, sku_key, image_url)
        local_cache_db.delete_curation_candidates(row_number, sku_key=sku_key)
        response = {'status': 'success', 'image_link': res["link"], 'sheet_value': res["sheet_value"],
                    'isolated': res["isolated"], 'provider': res.get("provider"), 'sku_key': sku_key}
        if not res["isolated"]:
            response['warning'] = 'background_not_removed'
        return response
    except Exception as e:
        config.log_error_to_laravel(f"CLI action_select_image exception: {e}\n{traceback.format_exc()}",
                                    product_name=product_name, brand=brand, barcode=barcode, level="ERROR")
        return {'status': 'failed', 'error': "Publishing the image failed (details on the Errors page)."}
    finally:
        if queue_started:
            google_sheets.stop_async_queue()


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

    pipeline = _pipeline()
    sku_key = _text(params, 'sku_key')
    if not sku_key:
        task = local_cache_db.get_task_by_row(row_number)
        sku_key = (task or {}).get("sku_key") or pipeline.compute_sku_key(
            {"name": product_name, "brand": brand, "barcode": barcode}, _load_brand_mappings())
    queue_started = False
    try:
        google_sheets.init_async_queue(config.CREDENTIALS_FILE, config.SPREADSHEET_NAME_OR_URL)
        queue_started = True
        worksheet = _open_sheet()
        link_column_index = google_sheets.find_link_column(worksheet)
        res = pipeline.publish_image(
            file_path, product_name, brand, row_number, worksheet, link_column_index, barcode=barcode,
            bg_method=_text(params, 'bg_removal_method') or None,
            target=(int(params.get('target_width') or 0), int(params.get('target_height') or 0)),
            category_override={k: _text(params, k) for k in ('category_l1_en', 'category_l2_en', 'category_l3_en')},
            enhance=_as_bool(params.get('enhance', False)), key_size=_text(params, 'size') or None,
        )
        try:
            os.remove(file_path)
        except OSError:
            pass
        if res["status"] == "failed":
            return {'status': 'failed', 'error': res.get('error')}
        local_cache_db.save_product_resolution(
            barcode, product_name, brand, "manual_upload", res["link"], None, res.get("metadata"),
            verification_status="human_approved", approved_by="human_upload", sku_key=sku_key,
        )
        local_cache_db.update_task_status_by_row(row_number, "completed", sku_key=sku_key)
        _record_review("manual_upload", params, row_number, sku_key)
        local_cache_db.delete_curation_candidates(row_number, sku_key=sku_key)
        response = {'status': 'success', 'image_link': res["link"], 'sheet_value': res["sheet_value"],
                    'isolated': res["isolated"], 'sku_key': sku_key}
        if not res["isolated"]:
            response['warning'] = 'background_not_removed'
        return response
    except Exception as e:
        config.log_error_to_laravel(f"CLI action_upload_manual_image exception: {e}\n{traceback.format_exc()}",
                                    product_name=product_name, brand=brand, barcode=barcode, level="ERROR")
        return {'status': 'failed', 'error': "Uploading the image failed (details on the Errors page)."}
    finally:
        if queue_started:
            google_sheets.stop_async_queue()


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


def _candidate_phash(row_number, image_url, params=None, sku_key=None):
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
    for c in local_cache_db.get_curation_candidates(row_number, sku_key=sku_key or None):
        if c.get("image_url") != image_url:
            continue
        return _phash_of_stored(c.get("content_sha256")), c.get("page_url") or page_url
    return None, page_url


def _cell_holds(value, image_url):
    value = (value or "").strip()
    if value.startswith("needs_review:"):
        value = value[len("needs_review:"):].strip()
    return value == image_url


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

    brand_mappings = None
    sku_key = _text(params, 'sku_key')
    if not sku_key:
        task = local_cache_db.get_task_by_row(row_number)
        sku_key = (task or {}).get("sku_key") or ""
    if not sku_key:
        if not product_name:
            return {'status': 'error', 'error': 'sku_key or product_name is required'}
        brand_mappings = _load_brand_mappings()
        sku_key = _pipeline().compute_sku_key({"name": product_name, "brand": brand, "barcode": barcode},
                                              brand_mappings)

    phash, page_url = _candidate_phash(row_number, image_url, params, sku_key)
    if not local_cache_db.add_rejected_image(sku_key, image_url, page_url=page_url or _text(params, 'page_url') or None,
                                             phash=phash, reason_code=reason_code):
        return {'status': 'error', 'error': 'could not record the rejection'}
    # رفض مرشح آخر لمنتج معتمد بشرياً لا يُلغي الاعتماد ولا يعيد الصف للطابور (وإلا قد ينشر العامل
    # تلقائياً فوق الرابط المعتمد). يُلغى الاعتماد فقط إذا كانت الصورة المرفوضة هي الصورة المعتمدة.
    approved = local_cache_db.get_cached_product(sku_key=sku_key)
    targets_approval = bool(approved) and image_url in (approved.get("original_url"), approved.get("cloudinary_url"))
    keep_approval = bool(approved) and approved.get("verification_status") == "human_approved" and not targets_approval
    _record_review("rejected", params, row_number, sku_key, image_url, reason_code=reason_code,
                   approval=approved if targets_approval else None)
    superseded = 0
    if not keep_approval:
        superseded = local_cache_db.supersede_resolution(sku_key, barcode=barcode or None)
    local_cache_db.delete_curation_candidates(row_number, sku_key=sku_key)
    if not keep_approval:
        local_cache_db.update_task_status_by_row(row_number, "pending", f"rejected by reviewer: {reason_code}",
                                                 failure_code="REJECTED", sku_key=sku_key)
    local_cache_db.save_feedback(str(uuid.uuid4()), image_url.split("/")[-1].split("?")[0], row_number,
                                 product_name, brand, image_url, [reason_code])

    sheet_cleared = False
    sheet_error = None
    queue_started = False
    try:
        google_sheets.init_async_queue(config.CREDENTIALS_FILE, config.SPREADSHEET_NAME_OR_URL)
        queue_started = True
        worksheet = _open_sheet()
        link_column_index = google_sheets.find_link_column(worksheet, create=False)
        if link_column_index >= 0:
            current = worksheet.cell(row_number, link_column_index + 1).value
            if _cell_holds(current, image_url):
                sheet_cleared = bool(google_sheets.update_image_link(
                    worksheet, row_number, link_column_index, "", barcode=barcode or None,
                    product_name=product_name or None, size=_text(params, 'size') or None))
    except Exception:
        sheet_error = "Could not update the sheet cell (details in temp/search.log)."
        logger.exception("تعذر تحديث الشيت بعد الرفض")
    finally:
        if queue_started:
            google_sheets.stop_async_queue()

    if keep_approval and sheet_cleared:
        # الخلية كانت تحمل الصورة المرفوضة: الاعتماد السابق لم يعد منشوراً، فيُلغى ويعاد الصف للطابور
        superseded = local_cache_db.supersede_resolution(sku_key, barcode=barcode or None)
        local_cache_db.update_task_status_by_row(row_number, "pending", f"rejected by reviewer: {reason_code}",
                                                 failure_code="REJECTED", sku_key=sku_key)
        keep_approval = False
    rejection = {'sku_key': sku_key, 'reason_code': reason_code, 'phash': phash, 'sheet_cleared': sheet_cleared,
                 'superseded': superseded, 'sheet_error': sheet_error, 'approval_kept': keep_approval}
    if _as_bool(params.get('research', False)):
        search_params = dict(params, sku_key=sku_key, skip_cache=True,
                             exclude_urls=_merge_urls(params.get('exclude_urls'), [image_url]))
        response = action_search(search_params, brand_mappings=brand_mappings)
        response['rejection'] = rejection
        return response
    return dict({'status': 'success'}, **rejection)


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


ACTIONS = {
    'get_products': action_get_products,
    'search': action_search,
    'select_image': action_select_image,
    'upload_manual_image': action_upload_manual_image,
    'reject_image': action_reject_image,
    'review_stats': action_review_stats,
    'sheet-preview': action_sheet_preview,
    'sheet-save': action_sheet_save,
    'ops_health': action_ops_health,
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
