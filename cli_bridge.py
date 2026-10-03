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
    for prod in products:
        try:
            prod["sku_key"] = pipeline.compute_sku_key(pipeline.sku_row(
                prod.get("product_name"), prod.get("brand"), prod.get("barcode"), {
                    "name_ar": prod.get("product_name_ar", ""), "brand_ar": prod.get("brand_ar", ""),
                    "category": prod.get("category", ""), "size": prod.get("size", "")}), brand_mappings)
        except Exception as e:
            logger.warning("تعذر حساب sku_key للصف %s: %s", prod.get("row_number"), e)
        prod["has_error"], prod["error_message"] = _row_failure(failures, prod)
    return {'status': 'success', 'products': products}


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
        return {'status': 'error', 'error': "Search failed (details in temp/search.log).", 'decision': None,
                'failure_code': 'SEARCH_ERROR',
                'selected_image': None, 'candidates': [], 'provider_health': [], 'sku_key': sku_key, 'trace': trace}

    if found is not None:
        found.update(best=best, trace=trace)
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
    acted, first = None, {}
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
    _learn_brand_spelling(action, params, acted, reason_code, first_brand=(first or {}).get("brand"))


def _learn_brand_spelling(action, params, acted, reason_code, first_brand=None):
    """
    التعلّم من المراجعة (catalog_match/learning.py): صورة ماركتها مؤكدة فقط بكتابة المتاجر (تنبيه
    brand_spelling:<الكتابة>) يعلّم اعتمادُها البحثَ هذه الكتابة لماركة الشيت، ورفضها بسبب WRONG_BRAND يُحسب ضدها.
    التحذيرات من المرشح المحفوظ (أسبابه) أو مما أرسلته شاشة المراجعة (candidate_warnings). لا يُرفع أي خطأ.
    """
    try:
        if action not in ("approved", "rejected") or (action == "rejected" and reason_code != "WRONG_BRAND"):
            return
        from catalog_match import learning

        spelling = learning.spelling_from((acted or {}).get("reasons") or []) \
            or learning.spelling_from(params.get('candidate_warnings'))
        brand = _text(params, 'brand') or (first_brand or "")
        if spelling and brand:
            local_cache_db.record_brand_alias(brand, spelling, approved=(action == "approved"))
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


def _current_state(sku_key, row_number, product_name):
    """
    (الحالة كما تعيدها الاستجابة، الحل المعتمد الحالي أو None). الحالة: صف الطابور لهذا المنتج عند رقم الصف
    (queue_status / queue_updated_at)، والحل المعتمد (approved_url وهو رابط Cloudinary، approved_image_url
    الصورة الأصلية، approval_status: human_approved | auto_verified، approved_by، approved_at).
    """
    approval = local_cache_db.get_cached_product(sku_key=sku_key) if sku_key else None
    task = local_cache_db.get_task_by_row(row_number)
    if not _same_product_task(task, sku_key, product_name):
        task = None
    approval = approval or {}
    current = {
        "queue_status": (task or {}).get("status") or None,
        "queue_updated_at": _ts((task or {}).get("updated_at")),
        "approved_url": approval.get("cloudinary_url") or None,
        "approved_image_url": approval.get("original_url") or None,
        "approval_status": approval.get("verification_status") or None,
        "approved_by": approval.get("approved_by") or None,
        "approved_at": _ts(approval.get("resolved_at")),
    }
    return current, (approval or None)


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


def _stale_refusal(params, sku_key, row_number, product_name, image_url=None):
    """
    رفض الاعتماد / الرفع فوق قرار لم يره المراجع (None = مسموح). replace=true يتجاوز الفحص.
    expected_state (ما عرضته الصفحة: queue_status, queue_updated_at, approved_url):
      - اعتماد بشري لم تعرضه الصفحة (رابطه غير approved_url) -> already_approved
      - صف الطابور تغيّر منذ فتح الصفحة -> state_changed
    بلا expected_state (عميل قديم): يبقى السلوك القديم، إلا أن اعتماداً بشرياً لصورة أخرى خلال آخر دقيقتين
    (APPROVAL_GUARD_SECONDS) لا يُستبدل -> already_approved. الاستجابة تحمل current لتعرضه الصفحة.
    """
    if _as_bool(params.get('replace', False)):
        return None
    current, approval = _current_state(sku_key, row_number, product_name)
    human = bool(approval) and approval.get("verification_status") == "human_approved"
    expected = _expected_state(params)
    code = None
    if expected is not None:
        shown = _bare_link(expected.get("approved_url"))
        if human and shown not in (approval.get("cloudinary_url"), approval.get("original_url")):
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


def _reviewer_check(params, sku_key, row_number, product_name, image_url, out):
    """
    before_write لاعتماد المراجع ورفعه (تحت قفل النشر للـ SKU، بعد المعالجة والرفع): يعيد فحص C1 لأن الحالة
    قد تتغير أثناء المعالجة، ثم يسحب حجز العامل عن صفوف المنتج فلا ينشر العامل فوق هذا القرار بعد تحرير القفل.
    """
    def check():
        refusal = _stale_refusal(params, sku_key, row_number, product_name, image_url)
        if refusal:
            out["refusal"] = refusal
            return False
        local_cache_db.release_worker_claims(row_number, sku_key=sku_key)
        return True
    return check


def _not_written(res, out):
    """استجابة نشر لم يكتب شيئاً (superseded): رفض C1 إن حدث، وإلا قفل النشر بقي عند غيرنا."""
    if out.get("refusal"):
        return out["refusal"]
    return {'status': 'failed', 'error_code': 'busy', 'error': STALE_ERRORS["busy"]}


def _other_rows(sku_key, row_number):
    """
    صفوف الشيت الأخرى لنفس المنتج: صفوف الطابور بنفس sku_key (المنتج مكرر في الشيت)، كل منها بهويته المسجلة
    عند الإدراج (الباركود والاسم والحجم والبراند)، فيتحقق الشيت من كل صف بهويته هو قبل الكتابة. الاعتماد يُكتب
    فيها كلها، وإلا تبقى الصفوف المكررة فارغة إلى الأبد بينما الطابور يعدّها مكتملة.
    """
    if not sku_key:
        return []
    pipeline = _pipeline()
    out = []
    for task in local_cache_db.get_tasks_by_sku(sku_key):
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


def _latest_link_writes(values):
    """
    سجلات طابور الكتابة (google_sheets.outbox_outcomes: {id, row, column_key, status ...}): آخر كتابة للرابط في كل صف
    فقط، وهي كتابة هذا الاعتماد؛ كتابات قديمة لنفس الصف (تعارض أو فشل قبل أسابيع) وكتابات البيانات الوصفية لا تُحسب.
    """
    records = [v for v in values if isinstance(v, dict) and "id" in v and ("row" in v or "row_number" in v)]
    if not records or len(records) != len(values):
        return values
    latest = {}
    for rec in records:
        if rec.get("column_key") not in (None, "", "link"):
            continue
        row = rec.get("row", rec.get("row_number"))
        if row not in latest or (rec.get("id") or 0) > (latest[row].get("id") or 0):
            latest[row] = rec
    return list(latest.values())


def _sheet_outcome(rows):
    """
    ما حدث لكتابة الرابط في الشيت بعد التفريغ الأخير لطابور الكتابة (عقد C3):
    written (كُتب في كل الصفوف) | pending (ما زال في الطابور، يُعاد لاحقاً) | conflict (رُفض: هوية الصف تغيّرت،
    أو فشل نهائياً) | unknown. المصدر google_sheets.outbox_outcomes(rows) إن وُجدت (حزمة الشيت: حالة كل صف)،
    وإلا unknown. أسوأ حالة بين الصفوف هي النتيجة.
    """
    outcomes = getattr(google_sheets, "outbox_outcomes", None)
    if not callable(outcomes) or not rows:
        return "unknown"
    try:
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
    values = _latest_link_writes(values)
    codes = set()
    for value in values:
        if isinstance(value, dict):
            value = value.get("outcome") or value.get("status") or value.get("sync_status")
        codes.add(_SHEET_OUTCOMES.get(str(value or "").strip().lower(), "unknown"))
    if not codes:
        return "unknown"
    for code in ("conflict", "pending", "unknown"):
        if code in codes:
            return code
    return "written"


def _published_response(res, sku_key, row_number, **extra):
    """
    استجابة الاعتماد / الرفع الناجح. warnings: background_not_removed (كُتب needs_review:)، و duplicate_image
    (نفس الصورة منشورة لمنتج آخر، duplicate_of يسمّيه؛ الاعتماد الصريح يُكتب مع ذلك). warning: أول تحذير.
    """
    response = dict({'status': 'success', 'image_link': res["link"], 'sheet_value': res["sheet_value"],
                     'isolated': res["isolated"], 'sku_key': sku_key,
                     'rows_written': res.get("rows_written") or [row_number]},
                    **extra)
    if res.get("rows_failed"):
        response['rows_failed'] = res["rows_failed"]
    if res.get("quality_flags"):
        response['quality_flags'] = list(res["quality_flags"])     # فحص جودة القص (لماذا لم تُعزل الخلفية)
    warnings = []
    if not res["isolated"]:
        warnings.append('background_not_removed')
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
    refusal = _stale_refusal(params, sku_key, row_number, product_name, image_url)
    if refusal:
        return refusal

    pipeline = _pipeline()
    queue_started = False
    guard = {}
    try:
        google_sheets.init_async_queue(config.CREDENTIALS_FILE, config.SPREADSHEET_NAME_OR_URL)
        queue_started = True
        worksheet = _open_sheet()
        link_column_index = google_sheets.find_link_column(worksheet)
        # ملف المعالجة الواحد (processing_profile) من صفحة الإعدادات، نفسه للنشر التلقائي والرفع اليدوي؛
        # target_width / enhance / bg_removal_method في الطلب لا تغيّره
        res = pipeline.publish_image(
            image_url, product_name, brand, row_number, worksheet, link_column_index,
            barcode=barcode, candidate_sha256=_candidate_sha(params, row_number, sku_key, image_url),
            category_override={k: _text(params, k) for k in ('category_l1_en', 'category_l2_en', 'category_l3_en')},
            key_size=_text(params, 'size') or None, key_brand=brand or None, sku_key=sku_key,
            also_rows=_other_rows(sku_key, row_number),
            before_write=_reviewer_check(params, sku_key, row_number, product_name, image_url, guard),
        )
        if res["status"] == "superseded":
            return _not_written(res, guard)
        if res["status"] == "failed":
            return {'status': 'failed', 'error': res.get('error'), 'isolated': res.get('isolated', False)}

        local_cache_db.save_product_resolution(
            barcode, product_name, brand, image_url, res["link"], None, res.get("metadata"),
            perceptual_hash=res.get("phash"), verification_status="human_approved", approved_by="human",
            sku_key=sku_key,
        )
        local_cache_db.update_task_status_by_row(row_number, "completed", sku_key=sku_key)
        _record_review("approved", params, row_number, sku_key, image_url)
        local_cache_db.delete_curation_candidates(row_number, sku_key=sku_key)
        response = _published_response(res, sku_key, row_number, provider=res.get("provider"))
    except Exception as e:
        config.log_error_to_laravel(f"CLI action_select_image exception: {e}\n{traceback.format_exc()}",
                                    product_name=product_name, brand=brand, barcode=barcode, level="ERROR")
        return {'status': 'failed', 'error': "Publishing the image failed (details on the Errors page)."}
    finally:
        if queue_started:
            google_sheets.stop_async_queue()     # التفريغ الأخير لطابور الكتابة
    response['sheet'] = _sheet_outcome(response['rows_written'])
    response['current'] = _current_state(sku_key, row_number, product_name)[0]   # expected_state للطلب التالي
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
    refusal = _stale_refusal(params, sku_key, row_number, product_name)
    if refusal:
        return refusal
    queue_started = False
    guard = {}
    try:
        google_sheets.init_async_queue(config.CREDENTIALS_FILE, config.SPREADSHEET_NAME_OR_URL)
        queue_started = True
        worksheet = _open_sheet()
        link_column_index = google_sheets.find_link_column(worksheet)
        res = pipeline.publish_image(
            file_path, product_name, brand, row_number, worksheet, link_column_index, barcode=barcode,
            category_override={k: _text(params, k) for k in ('category_l1_en', 'category_l2_en', 'category_l3_en')},
            key_size=_text(params, 'size') or None, key_brand=brand or None, sku_key=sku_key,
            also_rows=_other_rows(sku_key, row_number),
            before_write=_reviewer_check(params, sku_key, row_number, product_name, None, guard),
        )
        try:
            os.remove(file_path)
        except OSError:
            pass
        if res["status"] == "superseded":
            return _not_written(res, guard)
        if res["status"] == "failed":
            return {'status': 'failed', 'error': res.get('error')}
        local_cache_db.save_product_resolution(
            barcode, product_name, brand, "manual_upload", res["link"], None, res.get("metadata"),
            perceptual_hash=res.get("phash"), verification_status="human_approved", approved_by="human_upload",
            sku_key=sku_key,
        )
        local_cache_db.update_task_status_by_row(row_number, "completed", sku_key=sku_key)
        _record_review("manual_upload", params, row_number, sku_key)
        local_cache_db.delete_curation_candidates(row_number, sku_key=sku_key)
        response = _published_response(res, sku_key, row_number)
    except Exception as e:
        config.log_error_to_laravel(f"CLI action_upload_manual_image exception: {e}\n{traceback.format_exc()}",
                                    product_name=product_name, brand=brand, barcode=barcode, level="ERROR")
        return {'status': 'failed', 'error': "Uploading the image failed (details on the Errors page)."}
    finally:
        if queue_started:
            google_sheets.stop_async_queue()     # التفريغ الأخير لطابور الكتابة
    response['sheet'] = _sheet_outcome(response['rows_written'])
    response['current'] = _current_state(sku_key, row_number, product_name)[0]   # expected_state للطلب التالي
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


def _clear_rejected_cells(params, row_number, sku_key, image_url):
    """
    يفرّغ خلية الرابط التي تحمل الصورة المرفوضة (مع بادئة needs_review: أو بدونها) في صف المنتج وفي صفوفه
    المكررة، كل كتابة بهوية صفها (الباركود والاسم والحجم والبراند). يعيد (فُرّغت خلية واحدة على الأقل، خطأ أو None).
    """
    rows = [{"row_number": row_number, "barcode": _text(params, 'barcode'), "product_name": _text(params, 'product_name'),
             "size": _text(params, 'size') or None, "brand": _text(params, 'brand') or None}]
    rows += _other_rows(sku_key, row_number)
    cleared, error = False, None
    queue_started = False
    try:
        google_sheets.init_async_queue(config.CREDENTIALS_FILE, config.SPREADSHEET_NAME_OR_URL)
        queue_started = True
        worksheet = _open_sheet()
        link_column_index = google_sheets.find_link_column(worksheet, create=False)
        if link_column_index >= 0:
            for row in rows:
                current = worksheet.cell(row["row_number"], link_column_index + 1).value
                if not _cell_holds(current, image_url):
                    continue
                cleared = bool(google_sheets.update_image_link(
                    worksheet, row["row_number"], link_column_index, "", barcode=row["barcode"] or None,
                    product_name=row["product_name"] or None, size=row["size"], brand=row["brand"])) or cleared
    except Exception:
        error = "Could not update the sheet cell (details in temp/search.log)."
        logger.exception("تعذر تحديث الشيت بعد الرفض")
    finally:
        if queue_started:
            google_sheets.stop_async_queue()
    return cleared, error


def _save_research_candidates(found, row_number, product_name, brand, sku_key, rejected_url):
    """
    إعادة البحث بعد الرفض تحفظ مرشحاتها الجديدة بنفسها (عقد C2، بـ sku_key المنتج)، بدل أن تحفظها الصفحة.
    لا شيء يُحفظ بلا نتيجة (لم يُعثر على شيء / المزودون معطلون): المرشحات الباقية تبقى. يعيد عدد المحفوظ.
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
                                                   run_id=f"research-{uuid.uuid4().hex[:8]}"):
        return 0
    return len(candidates)


def _set_review_queue_status(row_number, sku_key, product_name, status, reason_code, failure_code=None):
    """حالة الطابور بعد الرفض (None = بلا تغيير). لا يُعاد كتابة نفس الحالة: توقيت الصف يبقى كما رأته الصفحة."""
    if not status:
        return
    task = local_cache_db.get_task_by_row(row_number)
    if _same_product_task(task, sku_key, product_name) and task.get("status") == status:
        return
    if status == "pending":
        local_cache_db.update_task_status_by_row(row_number, "pending", f"rejected by reviewer: {reason_code}",
                                                 failure_code="REJECTED", sku_key=sku_key)
    else:
        local_cache_db.update_task_status_by_row(row_number, status, None, failure_code=failure_code,
                                                 sku_key=sku_key)


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

    phash, page_url = _candidate_phash(row_number, image_url, params, sku_key)
    if not local_cache_db.add_rejected_image(sku_key, image_url, page_url=page_url or _text(params, 'page_url') or None,
                                             phash=phash, reason_code=reason_code):
        return {'status': 'error', 'error': 'could not record the rejection'}
    # رفض مرشح آخر لمنتج معتمد بشرياً لا يُلغي الاعتماد ولا يعيد الصف للطابور (وإلا قد ينشر العامل
    # تلقائياً فوق الرابط المعتمد). يُلغى الاعتماد فقط إذا كانت الصورة المرفوضة هي الصورة المعتمدة.
    approved = local_cache_db.get_cached_product(sku_key=sku_key)
    targets_approval = bool(approved) and image_url in (approved.get("original_url"), approved.get("cloudinary_url"))
    keep_approval = bool(approved) and approved.get("verification_status") == "human_approved" and not targets_approval
    stored = local_cache_db.get_curation_candidates(row_number, sku_key=sku_key or None)
    _record_review("rejected", params, row_number, sku_key, image_url, reason_code=reason_code,
                   approval=approved if targets_approval else None)
    # المراجعة تبقى حية (عقد C2): تُستبعد الصورة المرفوضة وحدها، وباقي المرشحات تبقى للمراجع
    rejected = next((c for c in stored if c.get("image_url") == image_url), None)
    remaining = [c for c in stored if c.get("image_url") != image_url
                 and c.get("status") not in ("rejected", "excluded")]
    shown_status = rejected.get("status") if rejected else _text(params, 'candidate_status')
    was_pick = targets_approval or shown_status == "preselected"
    if rejected:
        local_cache_db.exclude_curation_candidate(row_number, image_url, sku_key=sku_key)
    local_cache_db.save_feedback(str(uuid.uuid4()), image_url.split("/")[-1].split("?")[0], row_number,
                                 product_name, brand, image_url, [reason_code])

    sheet_cleared, sheet_error = _clear_rejected_cells(params, row_number, sku_key, image_url)
    if sheet_cleared:
        # الخلية كانت تحمل الصورة المرفوضة: هي المنشورة (أو المقترحة needs_review:)، فالاعتماد السابق لم يعد
        # منشوراً ويُلغى
        was_pick, keep_approval = True, False
    superseded = 0
    if targets_approval or sheet_cleared:
        # الحل المعتمد (أو المنشور في الخلية) هو المرفوض؛ اعتماد صورة أخرى لا يُلغى برفض غيرها
        superseded = local_cache_db.supersede_resolution(sku_key, barcode=barcode or None)

    response = None
    candidates_saved = 0
    if _as_bool(params.get('research', False)):
        search_params = dict(params, sku_key=sku_key, skip_cache=True,
                             exclude_urls=_merge_urls(params.get('exclude_urls'), [image_url]))
        found = {}
        response = action_search(search_params, brand_mappings=brand_mappings, found=found)
        candidates_saved = _save_research_candidates(found, row_number, product_name, brand, sku_key, image_url)

    # حالة الطابور: الاعتماد البشري الباقي لا يُمس. مرشحات جديدة من إعادة البحث -> بانتظار المراجعة. رفض الاختيار
    # (المسبق أو المعتمد أو المنشور) -> بانتظار المراجعة إن بقي مرشح مؤهل، وإلا يعود الصف للطابور، وكذلك رفض آخر
    # مرشح مؤهل محفوظ (رُفض الاختيار قبله). رفض بديل وغيره باقٍ لا يغيّرها.
    queue_status = None
    if not keep_approval:
        if candidates_saved:
            queue_status = "ready_for_review"
        elif was_pick or (rejected is not None and not remaining):
            queue_status = "ready_for_review" if remaining else "pending"
    _set_review_queue_status(row_number, sku_key, product_name, queue_status, reason_code,
                             (response or {}).get('failure_code'))

    rejection = {'sku_key': sku_key, 'reason_code': reason_code, 'phash': phash, 'sheet_cleared': sheet_cleared,
                 'superseded': superseded, 'sheet_error': sheet_error, 'approval_kept': keep_approval,
                 'candidates_left': len(remaining), 'queue_status': queue_status}
    current, _ = _current_state(sku_key, row_number, product_name)
    if response is not None:
        response['rejection'] = rejection
        response['candidates_saved'] = candidates_saved
        response['current'] = current
        return response
    return dict({'status': 'success', 'current': current}, **rejection)


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


# ---------------------------------------------------------------------------
# run_control (قراءة/كتابة): تشغيل جديد، إيقاف، إصلاح تشغيل عالق — لا يُحذف أي صف أبداً
# ---------------------------------------------------------------------------

# حالة عامل الخلفية كما رأتها لوحة التحكم (ApiController): العملية تُنهى في PHP لأنها تحتاج نظام التشغيل
RUN_CONTROL_WORKERS = ("starting", "running", "killed", "none")


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
    if worker == "killed":
        return (f"تم إيقاف التشغيل. أُعيد {released} صف كان قيد المعالجة إلى الانتظار ليُعالج في التشغيل القادم. "
                + kept)
    if released:
        return f"لم يكن هناك تشغيل نشط. أُعيد {released} صف عالق في «قيد المعالجة» إلى الانتظار. " + kept
    return "لم يكن هناك تشغيل نشط لإيقافه، ولم يتغير أي صف."


def _reset_message(worker, result):
    parts = [f"تم إصلاح حالة التشغيل: حُذف ملف القفل، ومُسح التقدم والتنبيه والإيقاف المؤقت، وأُعيد "
             f"{result['released']} صف من «قيد المعالجة» إلى الانتظار."]
    if worker == "killed":
        parts.append("وأُنهي العامل الذي كان ما زال يعمل.")
    elif worker == "starting":
        parts.append("وسُجل طلب إيقاف للتشغيل الذي كان يقرأ الشيت كي لا يبدأ المعالجة.")
    elif worker == "running":
        parts.append("وسُجل طلب إيقاف للعامل الذي تعذر إنهاؤه، فيتوقف بعد المنتجات الجارية.")
    parts.append(_kept_rows_text(result["queue"]))
    return " ".join(parts)


def action_run_control(params):
    """
    التحكم في تشغيل الأتمتة من لوحة التحكم (local_cache_db). op:
    - start: قبل إطلاق تشغيل جديد (prepare_run): حالة 'starting' بلا أرقام التشغيل السابق، وإلغاء طلبي
      الإيقاف والإيقاف المؤقت القديمين.
    - stop: زر «إيقاف التشغيل» (stop_run). worker: starting | running (العامل لم يبدأ أو ما زال حياً: طلب إيقاف
      يلتزم به) أو killed | none (أُنهي أو لم يكن يعمل: الصفوف قيد المعالجة تعود للانتظار وتُضبط الحالة).
    - reset: زر «إصلاح تشغيل عالق» (reset_run). worker=starting | running يسجل طلب إيقاف للإدراج الذي قد يكون
      ما زال يعمل أو للعامل الذي لم يُنهَ.
    لا يحذف أي صف أو مرشح أو قرار مراجعة. الاستجابة: {status, op, message (عربية), released, stop_requested,
    state, queue}.
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
        return _failure('failed', "تعذر تعديل حالة التشغيل في قاعدة البيانات؛ لم يتغير أي صف "
                                  "(التفاصيل في temp/search.log).", f"run_control {op} failed")
    return {'status': 'success', 'op': op, 'message': message, 'released': result['released'],
            'stop_requested': result['stop_requested'], 'state': result['status'], 'queue': result['queue']}


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
