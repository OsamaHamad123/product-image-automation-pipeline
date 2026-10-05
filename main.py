# main.py
# السكربت الرئيسي لتشغيل نظام الأتمتة: بناء الطابور (--enqueue)، عامل البحث المسبق (--worker)،
# والوضع التسلسلي القديم (بدون وسيط).
#
# القرارات الملزمة هنا (D10/D11/D14):
# - النشر التلقائي فقط عندما يكون قرار البحث AUTO_PUBLISH (لا عتبات clip_score)، وأبداً لنتيجة من الكاش.
# - إعادة المحاولة فقط عند PROVIDER_DOWN أو استثناء؛ "لا نتيجة" نظيفة لا تُعاد.
# - رفض المراجعين (روابط + pHash) يُمرر للبحث كاستبعادات لكل SKU.
# - اللوحة المنشورة بيضاء من image_processor بدون أي تكبير لاحق، بملف المعالجة الواحد (processing_profile:
#   أبعاد اللوحة وتحسين الألوان وطريقة العزل من صفحة الإعدادات) لكل مسارات النشر؛ إذا لم تُعزل الخلفية
#   يُكتب الرابط ببادئة needs_review: ولا يُخزن كحل معتمد، إلا إذا اختار المالك «بدون عزل الخلفية» بالإعدادات
#   (bg_removal_method = none، زر «تجاوز عزل الخلفية»): عندها تُنشر الصورة كما هي نظيفة مع العلامة bg_skipped.

import json
import os
import re
import socket
import sys
import threading
import time
import uuid


def _reconfigure_stdout():
    # نوافذ: مخرجات الكونسول المحولة قد تكون cp1256/cp1252؛ نضمن UTF-8 قبل أي طباعة عربية أو رموز
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


_reconfigure_stdout()

import config
print = config.log_runner
import google_sheets
import image_search
import image_processor
import cloudinary_storage
import local_cache_db
import processing_profile

MAX_SEARCH_ATTEMPTS = 3
RETRY_BASE_DELAY = 2.0
MAX_PROVIDER_DOWN_STREAK = 5
MAX_DB_OUTAGE_SECONDS = 600
MAX_SAVED_CANDIDATES = 20


SUPPORTED_BG_METHODS = ("photoroom", "remove_bg_api", "grabcut", "rembg", "none")


def _supported_bg_method(value):
    """طريقة العزل المختارة في صفحة الدفعات بعد توحيد اسمها، أو None إن لم تكن مدعومة."""
    name = str(value or "").strip().lower()
    name = getattr(image_processor, "_METHOD_ALIASES", {}).get(name, name)
    return name if name in SUPPORTED_BG_METHODS else None


def load_run_config():
    """
    تحميل إعدادات التشغيل الجماعي من temp/run_config.json إن وجد لتجاوز إعدادات config.py.
    """
    try:
        config.load_db_config()
    except Exception as e:
        print(f"تنبيه: فشل تحديث الإعدادات من قاعدة البيانات: {e}")

    config_file = "temp/run_config.json"
    if not os.path.exists(config_file):
        return
    try:
        with open(config_file, "r", encoding="utf-8") as f:
            overrides = json.load(f)

        if "strictBrandMatch" in overrides:
            config.STRICT_BRAND_MATCH = bool(overrides["strictBrandMatch"])
        if "aiEnhance" in overrides:
            config.ENABLE_IMAGE_ENHANCEMENT = bool(overrides["aiEnhance"])
        if "skipCache" in overrides:
            config.SKIP_LOCAL_CACHE = bool(overrides["skipCache"])
        if "target_width" in overrides:
            w = int(overrides["target_width"] or 0)
            h = int(overrides.get("target_height", 0) or 0)
            config.IMAGE_TARGET_SIZE = (w, h)
        if "bg_color" in overrides:
            config.IMAGE_BG_COLOR = str(overrides["bg_color"]).strip().lstrip('#')
        for key in ("bgRemovalMethod", "bg_removal_method"):
            if key in overrides:
                method = _supported_bg_method(overrides[key])
                if method:
                    config.BG_REMOVAL_METHOD = method
                else:
                    print(f"تنبيه: طريقة عزل الخلفية '{overrides[key]}' غير مدعومة وتم تجاهلها.")
        if "curation_mode" in overrides:
            config.CURATION_MODE = bool(overrides["curation_mode"])
        if "brand_filter" in overrides:
            val = overrides["brand_filter"]
            config.BRAND_FILTER = str(val).strip() if (val is not None and str(val).strip().lower() != "none") else ""
        if "row_filter" in overrides:
            val = overrides["row_filter"]
            config.ROW_FILTER = str(val).strip() if (val is not None and str(val).strip().lower() != "none") else ""
        for key in ("forceOverwrite", "reprocess"):
            if key in overrides:
                config.FORCE_OVERWRITE_IMAGES = bool(overrides[key])
        for key in ("auto_publish_enabled", "autoPublish"):
            if key in overrides:
                config.AUTO_PUBLISH_ENABLED = bool(overrides[key])
        if "auto_publish_brands" in overrides:
            val = overrides["auto_publish_brands"]
            items = val if isinstance(val, (list, tuple)) else str(val or "").split(",")
            config.AUTO_PUBLISH_BRANDS = [str(b).strip() for b in items if str(b).strip()]
        for retired in ("ignoreUnitClash", "auto_approve_threshold", "aiUpscale", "padding_ratio"):
            if retired in overrides:
                print(f"تنبيه: الخيار '{retired}' في run_config.json لم يعد مدعوماً وتم تجاهله.")
        print("[Run Config] تم تطبيق التجاوزات من run_config.json.")
    except Exception as e:
        print(f"تنبيه: خطأ أثناء تحميل run_config.json: {e}")


def save_progress(current, total, success, failed, current_product=""):
    try:
        os.makedirs("temp", exist_ok=True)
        with open("temp/batch_progress.json", "w", encoding="utf-8") as f:
            json.dump({"current": current, "total": total, "success": success, "failed": failed,
                       "current_product": current_product}, f, ensure_ascii=False)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# أدوات مشتركة
# ---------------------------------------------------------------------------

def parse_row_filter(text):
    """'2-5, 9' -> {2,3,4,5,9}. فارغ -> None. صيغة خاطئة -> ValueError."""
    text = (text or "").strip()
    if not text:
        return None
    allowed = set()
    for part in text.split(','):
        part = part.strip()
        if not part:
            continue
        if '-' in part:
            start, end = part.split('-', 1)
            start, end = int(start), int(end)
            if end < start:
                raise ValueError(f"نطاق صفوف مقلوب: {part}")
            allowed.update(range(start, end + 1))
        else:
            allowed.add(int(part))
    if not allowed:
        raise ValueError(f"فلتر صفوف فارغ: {text!r}")
    return allowed


def task_payload(task):
    """حمولة الصف الكاملة (name_ar, brand_ar, category, size...) المخزنة في الطابور."""
    raw = task.get("payload_json")
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def sku_row(name, brand, barcode, payload):
    return {
        "name": name or "",
        "brand": brand or "",
        "barcode": barcode or "",
        "name_ar": payload.get("name_ar", ""),
        "brand_ar": payload.get("brand_ar", ""),
        "category": payload.get("category", ""),
        "size": payload.get("size", ""),
    }


def compute_sku_key(row, brand_mappings=None):
    """sku_key الموحد (GTIN-14 أو بصمة البراند|الاسم|الحجم) عبر catalog_match.identity."""
    from catalog_match.identity import build_sku_spec
    return build_sku_spec(row, brand_mappings).sku_key


def _is_gtin_key(sku_key):
    key = str(sku_key or "")
    return len(key) == 14 and key.isdigit()


def compute_alt_sku_key(row, spec=None):
    """
    المفتاح البديل للمنتج: بصمة البراند|الاسم|الحجم بدون الباركود (make_sku_key بلا GTIN). لصف بلا باركود صالح
    يساوي sku_key نفسه؛ ولصف أُضيف له باركود صالح لاحقاً يساوي مفتاحه القديم، فتبقى الاعتمادات والرفض المحفوظة
    بالمفتاح القديم سارية. لا يغيّر أي sku_key محفوظ.
    """
    from catalog_match.identity import build_sku_spec, make_sku_key
    from catalog_match.text_norm import match_key
    spec = spec if spec is not None else build_sku_spec(row, None)
    if not spec.gtin:
        return spec.sku_key
    brand = next((str(row.get(k)) for k in ("brand", "brand_en", "brand_ar") if str(row.get(k) or "").strip()), "")
    # الحجم المقروء من الاسم الخام كما في sku_key نفسه (key_size)، لا الحجم المقروء بعد تصحيح الاسم
    return make_sku_key(None, match_key(brand).replace(" ", ""), spec.raw_name, spec.key_size)


def brand_fingerprint(spec):
    """
    بصمة مدخل البراند في Brands Mapping كما يراه البحث (الاسم المعتمد، المرادفات، المنافسون، المواقع الرسمية،
    العلامات الفرعية). None لبراند بلا مدخل: فشل تحميل الورقة لا يبدو «تغييراً» يعيد البحث عن كل منتجاته.
    """
    import hashlib
    if getattr(spec, "brand_conf", "") != "mapped":
        return None
    parts = [spec.brand_canonical or ""] + ["|".join(sorted(v or ())) for v in (
        spec.match_brands, spec.competitors, spec.official_domains, spec.required_brands, spec.sibling_brands)]
    return hashlib.sha1("\n".join(parts).encode("utf-8")).hexdigest()[:16]


def default_query(name, brand):
    return f"{name or ''} {brand or ''}".strip()


def custom_query_for(query, name, brand):
    """الاستعلام المخصص يُمرر فقط إذا اختلف عن الاستعلام الافتراضي (الاسم + البراند)."""
    q = " ".join(str(query or "").split())
    if not q or q == " ".join(default_query(name, brand).split()):
        return None
    return q


def _with_warning_reasons(c):
    """
    تحذيرات العرض لكل مرشح (warnings من facade: للصورة المختارة وللبدائل) تُحفظ ضمن أسبابه بصيغة warn:<الرمز>،
    لأن صف curation_candidates يحفظ الأسباب فقط، وشاشة المراجعة تقرأ warn:* منها. عرض فقط: لا يقرأها التوجيه.
    """
    warnings = c.get("warnings")
    if not isinstance(warnings, list) or not warnings:
        return c
    reasons = [str(r) for r in (c.get("reasons") or [])]
    for w in warnings:
        tag = f"warn:{w}"
        if w and tag not in reasons:
            reasons.append(tag)
    c["reasons"] = reasons
    return c


def collect_candidates(best, trace, limit=MAX_SAVED_CANDIDATES):
    """المرشحات التي تُعرض على المراجع، مع الحالة والأسباب والأدلة كما أعادها البحث."""
    if best.get("source") == "sqlite_cache":
        preselected = best.get("decision") == "REVIEW_PRESELECTED" or bool(best.get("preselect"))
        return [{
            "url": best["url"],
            "title": best.get("title") or "cached approval",
            "status": "preselected" if preselected else "eligible",
            "width": best.get("width"),
            "height": best.get("height"),
            "reasons": ["cache_hit"],
            "evidence": {"source": "cache", "original_url": best.get("original_url")},
        }]

    cands = best.get("candidates")
    if not cands:
        cands, seen = [], set()
        for step in (trace or {}).get("steps", []) or []:
            for c in step.get("candidates", []) or []:
                url = c.get("url")
                if url and url not in seen:
                    seen.add(url)
                    cands.append(c)
    out = [_with_warning_reasons(dict(c)) for c in cands
           if isinstance(c, dict) and (c.get("url") or c.get("image_url"))]
    if best.get("preselect") and not any(c.get("status") == "preselected" for c in out):
        for c in out:
            if c.get("url") == best.get("url"):
                c["status"] = "preselected"
                break
    return out[:limit]


def _outcome(trace):
    outcome = (trace or {}).get("outcome")
    return outcome if isinstance(outcome, dict) else {}


def _is_provider_down(best, trace):
    outcome = _outcome(trace)
    return best is None and "PROVIDER_DOWN" in (outcome.get("failure_code"), outcome.get("decision"))


def _serper_calls(trace):
    """[(status, http_status)] لاستعلامات Serper في بحث واحد (outcome.provider_health)."""
    calls = []
    for item in _outcome(trace).get("provider_health") or []:
        if isinstance(item, dict) and str(item.get("provider") or "").strip().lower() == "serper":
            try:
                http = int(item.get("http_status"))
            except (TypeError, ValueError):
                http = None
            calls.append((str(item.get("status") or "").strip().lower(), http))
    return calls


def serper_credit_refused(trace):
    """
    قاعدة SERPER_CREDIT في ops_health لبحث واحد: True عندما لم يُجب Serper عن أي استعلام ورفض واحداً على الأقل
    بسبب الرصيد أو المفتاح (quota، أو http 401/403)؛ False عندما أجاب؛ None عندما لم يُستدعَ Serper أصلاً.
    HTTP 429 (quota في المزود) حد سرعة عابر وليس رصيداً: لا يُحسب هنا (serper_rate_limited).
    """
    import ops_health
    calls = _serper_calls(trace)
    if not calls:
        return None
    if any(status in ops_health.ANSWERED_STATUSES for status, _ in calls):
        return False
    return any((status == "quota" and http not in ops_health.RATE_LIMITED_HTTP)
               or (status == "error" and http in ops_health.KEY_REJECTED_HTTP) for status, http in calls)


def serper_rate_limited(trace):
    """True عندما لم يُجب Serper عن أي استعلام ورد بحد السرعة (HTTP 429) مرة على الأقل: انقطاع عابر ينتظر."""
    import ops_health
    calls = _serper_calls(trace)
    return (bool(calls) and not any(status in ops_health.ANSWERED_STATUSES for status, _ in calls)
            and any(http in ops_health.RATE_LIMITED_HTTP for _, http in calls))


def search_with_retry(query, name, brand, search_kwargs, sleep=time.sleep,
                      max_attempts=MAX_SEARCH_ATTEMPTS, base_delay=RETRY_BASE_DELAY, on_attempt=None):
    """
    البحث مع إعادة المحاولة فقط عند PROVIDER_DOWN أو استثناء (بحد أقصى 3 مع ارتداد).
    كل محاولة تحصل على trace جديد. تعيد (best, trace, state, error) حيث state: ok | provider_down | error.
    on_attempt(trace): يُستدعى بعد كل محاولة (سجل الصرف اليومي يحسب كل محاولة، لا الأخيرة فقط)؛ خطؤه لا يوقف البحث.
    """
    delay = base_delay
    trace, state, error = {}, "ok", None
    for attempt in range(1, max_attempts + 1):
        trace = {}
        try:
            best = image_search.search_best_product_image(query, name, brand, trace=trace, **search_kwargs)
        except Exception as ex:
            best = None
            state, error = "error", f"{type(ex).__name__}: {ex}"
            print(f"[Attempt {attempt}/{max_attempts}] فشل البحث للمنتج [{name}]: {error}")
        else:
            if not _is_provider_down(best, trace):
                state, error = "ok", None
            else:
                state, error = "provider_down", "all search providers unavailable"
                print(f"[Attempt {attempt}/{max_attempts}] محركات البحث غير متاحة للمنتج [{name}].")
        if on_attempt is not None:
            try:
                on_attempt(trace)
            except Exception as e:
                print(f"تنبيه: تعذر تسجيل صرف البحث: {e}")
        if state == "ok":
            return best, trace, "ok", None
        if attempt < max_attempts:
            sleep(delay)
            delay *= 2
    return None, trace, state, error


def _folder_and_tags(metadata):
    folder, tags = "products", []
    cat1 = (metadata.get("category_l1_en") or "").strip().lower().replace(" ", "_").replace("&", "and")
    cat2 = (metadata.get("category_l2_en") or "").strip().lower().replace(" ", "_").replace("&", "and")
    if cat1:
        folder = f"products/{cat1}/{cat2}" if cat2 else f"products/{cat1}"
    tags_str = metadata.get("tags_en") or ""
    if tags_str:
        tags = [t.strip() for t in str(tags_str).split(",") if t.strip()]
    return folder, tags


def publish_image(image_url, name, brand, row_number, worksheet, link_column_index, *, barcode="",
                  candidate_sha256=None, category_override=None, force_review=False, key_size=None,
                  key_brand=None, profile=None, sku_key=None, before_write=None, duplicates="warn",
                  also_rows=None, after_write=None, unclean="review", publish_anyway=False, page_url=None):
    """
    معالجة الصورة المعتمدة إلى لوحة النشر النهائية ورفعها وكتابة رابطها في الشيت.
    key_size/key_brand: خلايا الحجم والبراند في الشيت لهذا المنتج، تُضاف إلى هوية الصف المتحقق منها
    قبل الكتابة (بدون باركود تميز الشقيقين بنفس الاسم).
    profile: ملف المعالجة (processing_profile)؛ الافتراضي ملف الإعدادات الحالي، نفسه لكل مسارات النشر.
    before_write: دالة بلا وسائط تُستدعى بعد الرفع ومباشرة قبل الكتابة في الشيت، تحت قفل النشر لهذا الـ sku_key
    (local_cache_db.sku_publish_lock)؛ إذا أعادت False (أو رفعت استثناء، أو بقي القفل عند غيرنا حتى المهلة) لا
    يُكتب شيء وتكون الحالة 'superseded'. المعالجة والرفع قد يستغرقان دقيقة، والمراجع قد يعتمد خلالها.
    also_rows: صفوف الشيت الأخرى لنفس المنتج (نفس sku_key)، كل منها {row_number, barcode, product_name, size,
    brand} بهويته هو: تُكتب فيها نفس القيمة والبيانات الوصفية، وكل كتابة يتحقق منها الشيت بهوية صفها.
    rows_written: الصفوف التي قُبلت كتابة رابطها؛ rows_failed: صفوف also_rows التي رُفضت.
    after_write(result): يُستدعى بعد الكتابة في الشيت وقبل تحرير قفل النشر، بالنتيجة التي ستُعاد: يحفظ فيه المستدعي
    قراره (الحل المعتمد وحالة الطابور)، فمن ينتظر القفل (مراجع آخر أو العامل) يرى القرار في إعادة تحققه. خطؤه يُرفع.
    duplicates: صورة نُشرت لمنتج آخر (نفس رابط Cloudinary، أو pHash اللوحة على مسافة 4 أو أقل بألوان غير مختلفة
    بوضوح: local_cache_db.find_image_owners؛ sku_key مختلف):
    'block' (النشر التلقائي من الطابور) لا يكتب شيئاً والحالة 'needs_review' (error='duplicate_image')؛ 'review'
    (الوضع التسلسلي القديم) يكتب الرابط ببادئة needs_review: فقط؛ 'warn' (اعتماد المراجع الصريح) يكتب كالمعتاد.
    المالكون في duplicate_of، وتعذر التحقق يُعامل كتكرار في 'block' و 'review'.
    phash و color_signature: بصمتا اللوحة النهائية (pHash وبصمة الألوان، تُخزنان مع الحل المعتمد).
    quality_flags: علامات بوابة القص (image_processor) و quality_notes: ملاحظاتها غير المانعة، تعودان دائماً.
    page_url: صفحة المرشح إن عُرفت؛ تُمرر للمعالجة (إن قبلتها) فيُعاد التنزيل بنفس Referer الجلب الأول.
    لوحة لم تُعزل خلفيتها (isolated=False): unclean='review' (العامل) تُكتب ببادئة needs_review:؛ unclean='refuse'
    (اعتماد المراجع ورفعه) لا يُرفع ولا يُكتب شيء والحالة 'quality_refused' (publish_anyway_allowed: علاماتها كلها
    للعرض فقط، PRESENTATION_FLAGS)، فلا يُسجل اعتماد بشري ورابط الشيت needs_review:. publish_anyway=True (تأكيد
    المراجع بعد رؤية العلامات) يكتب اللوحة نظيفة عندما تسمح علاماتها بذلك؛ عزل فشل (بلا علامات أو بعلامة مانعة) أبداً.
    «تجاوز عزل الخلفية»: المالك اختار بالإعدادات «بدون عزل الخلفية» (profile.bg_method = 'none'، مثلاً لما رصيد PhotoRoom
    خلص) والمعالجة أعادت لوحة بلا عزل (provider 'none'): هذا اختياره لا فشل، فتُنشر اللوحة نظيفة في كل المسارات (بلا
    needs_review: ولا quality_refused، والعامل ينشر تلقائياً كالمعتاد) و bg_skipped=True في النتيجة، لتقول استجابة
    الاعتماد وتقرير التشغيل «انتشرت بدون عزل الخلفية». فشل عزل حقيقي بأي طريقة أخرى (photoroom_402، أو لوحة بعلامات)
    يبقى كما هو أعلاه.
    لا تكبير لاحق: اللوحة من image_processor نهائية. البيانات الوصفية تُكتب في الشيت فقط بعد نجاح الرفع.
    الحالة: 'published' (معزولة وليست للمراجعة، أو نُشرت رغم علامات العرض، أو بدون عزل باختيار المالك) |
    'needs_review' (رابط ببادئة needs_review:) | 'quality_refused' | 'superseded' (لم يُكتب شيء) | 'failed'.
    """
    profile = profile or processing_profile.current()
    w, h = profile.target
    extra = {"page_url": page_url} if page_url and _accepts(image_processor.process_product_image_result,
                                                          "page_url") else {}
    result = image_processor.process_product_image_result(
        image_url, name, brand, target_width=w, target_height=h,
        bg_method=profile.bg_method, candidate_sha256=candidate_sha256, enhance=profile.enhance, **extra,
    )
    if not result.path:
        return {"status": "failed", "error": result.error or "processing_failed", "isolated": False,
                "provider": result.provider, "profile": profile.as_dict()}

    flags = [str(f) for f in (getattr(result, "quality_flags", None) or [])]
    # ملاحظات البوابة غير المانعة (image_processor.NON_BLOCKING_NOTES، مثل upscaled) تُعاد كما هي ولا تمنع النشر
    notes = [str(n) for n in (getattr(result, "quality_notes", None) or [])]
    # المالك اختار «بدون عزل الخلفية»: لوحة provider 'none' هي ما طلبه، لا «الخلفية لم تُعزل»
    bg_skipped = _bg_skipped(profile, result)
    unisolated = not result.isolated and not bg_skipped
    anyway_allowed = unisolated and bool(flags) and set(flags) <= PRESENTATION_FLAGS
    anyway = bool(publish_anyway) and anyway_allowed
    if unisolated and not anyway and unclean == "refuse":
        image_processor.cleanup_processed_image(result.path)
        print(f"[Publish] لوحة الصف {row_number} لم تجتز فحص القص ({', '.join(flags) or 'no isolation'})؛ لم يُنشر شيء.")
        return {"status": "quality_refused", "error": "quality_flags" if anyway_allowed else "background_failed",
                "isolated": False, "provider": result.provider, "profile": profile.as_dict(),
                "quality_flags": flags, "quality_notes": notes, "publish_anyway_allowed": anyway_allowed}

    metadata = {}
    try:
        try:
            metadata = image_processor.extract_metadata_from_image(result.path, name, brand) or {}
        except Exception as e:
            print(f"تنبيه: تعذر استخراج البيانات الوصفية لـ [{name}]: {e}")
            metadata = {}
        override = category_override or {}
        if (override.get("category_l1_en") or "").strip():
            import categories
            metadata.update(categories.normalize_category_path(
                override.get("category_l1_en", "").strip(),
                (override.get("category_l2_en") or "").strip(),
                (override.get("category_l3_en") or "").strip()))
        folder, tags = _folder_and_tags(metadata)
        phash = _canvas_phash(result.path)
        color = _canvas_color_signature(result.path)
        link = cloudinary_storage.upload_product_image_to_cloudinary(
            result.path, name, brand, folder=folder, tags=tags,
            target_width=result.width, target_height=result.height,
        )
    finally:
        image_processor.cleanup_processed_image(result.path)

    base = {"isolated": bool(result.isolated), "provider": result.provider, "metadata": metadata,
            "width": result.width, "height": result.height, "profile": profile.as_dict(), "phash": phash,
            "color_signature": color, "quality_flags": flags, "quality_notes": notes, "published_anyway": anyway,
            "bg_skipped": bg_skipped}
    if not link:
        return dict(base, status="failed", error="upload_failed")

    # نفس الصورة منشورة لمنتج آخر؟ الرفع الموجود مسبقاً (existing من Cloudinary) دليل إضافي فقط
    owners = local_cache_db.find_image_owners(link, phash, sku_key=sku_key, product_name=name,
                                              color_signature=color)
    base["duplicate_of"] = list(owners or [])
    base["cloudinary_existing"] = getattr(link, "existing", None)
    duplicate = owners is None or bool(owners)
    if duplicates == "block" and duplicate:
        print(f"[Publish] صورة الصف {row_number} منشورة لمنتج آخر (أو تعذر التحقق)؛ لا نشر تلقائي، تُحال للمراجعة.")
        return dict(base, status="needs_review", error="duplicate_image", link=link)

    review = force_review or (unisolated and not anyway) or (duplicates == "review" and duplicate)
    sheet_value = f"needs_review:{link}" if review else link
    identity = {"barcode": barcode, "product_name": name, "size": key_size, "brand": key_brand}
    with local_cache_db.sku_publish_lock(sku_key) as lock_state:
        if before_write is not None:
            if lock_state == "busy":
                print(f"[Publish] نشر آخر لنفس المنتج ما زال يكتب؛ لم يُكتب شيء للصف {row_number}.")
                return dict(base, status="superseded", error="publish_busy", link=link)
            if not _write_still_allowed(before_write):
                return dict(base, status="superseded", error="superseded", link=link)
        if not google_sheets.update_image_link(worksheet, row_number, link_column_index, sheet_value, **identity):
            return dict(base, status="failed", error="sheet_write_failed", link=link)
        written, failed = [row_number], []
        _write_metadata(worksheet, row_number, metadata, identity)
        for other in also_rows or []:
            other_row = other.get("row_number")
            other_identity = {"barcode": other.get("barcode") or "", "product_name": other.get("product_name"),
                              "size": other.get("size"), "brand": other.get("brand")}
            try:
                ok = google_sheets.update_image_link(worksheet, other_row, link_column_index, sheet_value,
                                                     **other_identity)
            except Exception as e:
                print(f"تنبيه: تعذر كتابة الرابط في الصف المكرر {other_row}: {e}")
                ok = False
            if not ok:
                failed.append(other_row)
                continue
            written.append(other_row)
            _write_metadata(worksheet, other_row, metadata, other_identity)
        outcome = dict(base, status="needs_review" if review else "published", link=link, sheet_value=sheet_value,
                       rows_written=written, rows_failed=failed)
        if after_write is not None:
            after_write(outcome)
    return outcome


def _bg_skipped(profile, result):
    """
    هل اللوحة «بدون عزل الخلفية» باختيار المالك؟ ملف المعالجة يقول none (processing_profile.skips_background) والمعالجة
    أعادت لوحة بلا عزل من الطريقة none نفسها. أي لوحة أخرى لم تُعزل (فشل مزوّد أو علامات فحص القص) ليست كذلك.
    """
    method = str(getattr(profile, "bg_method", "") or "").strip().lower()
    return (method in processing_profile.NO_REMOVAL_METHODS and bool(getattr(result, "path", None))
            and not getattr(result, "isolated", False) and str(getattr(result, "provider", "") or "") == "none")


# علامات بوابة القص التي تخص العرض فقط (الخلفية معزولة): المراجع يستطيع نشر اللوحة رغمها بعد أن يراها
# (publish_anyway). edge_clipped و opaque_backdrop و opaque_fill (لم يُزل شيء من الخلفية: image_processor) وأي علامة
# أخرى، وعزل فشل بلا علامات، لا يُنشر نظيفاً أبداً: هي «الخلفية لم تُعزل» (background_failed).
PRESENTATION_FLAGS = frozenset({"upscaled", "too_small_on_canvas", "second_object", "alpha_haze", "kept_shadow"})


def _accepts(func, name):
    """هل تقبل الدالة الوسيط name (أو **kwargs)؟ لتمرير وسيط جديد لحزمة لم تُدمج بعد دون خطأ."""
    import inspect
    try:
        params = inspect.signature(func).parameters
    except (TypeError, ValueError):
        return False
    return name in params or any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())


def _write_metadata(worksheet, row_number, metadata, identity):
    if not metadata:
        return
    try:
        google_sheets.update_product_metadata(worksheet, row_number, metadata, **identity)
    except Exception as e:
        print(f"تنبيه: تعذر كتابة البيانات الوصفية للصف {row_number}: {e}")


def _canvas_phash(path):
    """pHash اللوحة النهائية (16 خانة hex مثل catalog_match.fetch.phash_hex)، أو None."""
    try:
        from PIL import Image
        from catalog_match.fetch import phash_hex
        with Image.open(path) as img:
            img.load()
            return phash_hex(img.convert("RGB"))
    except Exception as e:
        print(f"تنبيه: تعذر حساب pHash للوحة النهائية: {e}")
        return None


def _canvas_color_signature(path):
    """بصمة ألوان اللوحة النهائية (image_dedup_bktree.color_signature)، أو None."""
    import image_dedup_bktree
    return image_dedup_bktree.color_signature(path)


def _write_still_allowed(before_write):
    """نتيجة إعادة التحقق قبل الكتابة؛ أي استثناء يعني لا (لا نكتب ونحن لا نعرف)."""
    try:
        return bool(before_write())
    except Exception as e:
        print(f"[Publish] تعذرت إعادة التحقق قبل الكتابة: {e}")
        return False


# ---------------------------------------------------------------------------
# الاعتماد التلقائي والبحث المسبق
# ---------------------------------------------------------------------------

def auto_approve_product(task, best_image, worksheet, link_column_index, sku_key=None):
    """
    نشر نتيجة AUTO_PUBLISH مباشرة. تعيد 'published' أو 'needs_review' (الخلفية لم تُعزل) أو 'superseded'
    (مراجع اعتمد المنتج أثناء المعالجة والرفع، أو لم يعد الصف محجوزاً لهذا العامل: لم يُكتب شيء) أو 'rejected'
    (مراجع رفض هذه الصورة لهذا المنتج أثناء المعالجة: لم يُكتب شيء) أو 'busy' (قفل النشر بقي عند غيرنا حتى المهلة)
    أو 'failed'. الحل يُخزن auto_verified فقط عند النشر الفعلي: بلوحة معزولة، أو «بدون عزل الخلفية» باختيار المالك
    (bg_skipped، تُعد لتقرير التشغيل: bg_skipped_count).
    """
    name = task["product_name"]
    brand = task.get("brand") or ""
    barcode = task.get("barcode") or ""
    alt_key = task.get("alt_sku_key") or None
    why = {}

    def still_ours():
        # إعادة التحقق تحت قفل النشر قبل الكتابة: الحجز ما زال لهذا العامل، ولا يوجد اعتماد بشري، ولم يرفض مراجع
        # هذه الصورة (رابطها أو pHash) أثناء المعالجة. تعذر قراءة الرفض = لا نشر
        if not local_cache_db.is_claim_held(task["id"], task.get("worker_id")) or _has_human_approval(sku_key, alt_key):
            return False
        if rejected_image(sku_key, alt_key, best_image["url"], _image_phash(best_image)) is not False:
            why["rejected"] = True
            return False
        return True

    def record(res):
        # تحت قفل النشر: الحل التلقائي يُحفظ قبل أن يرى مراجع ينتظر القفل حالة المنتج
        if res["status"] == "published":
            local_cache_db.save_product_resolution(
                barcode, name, brand, best_image["url"], res["link"], None, res.get("metadata"),
                perceptual_hash=res.get("phash"), verification_status="auto_verified",
                approved_by="auto", sku_key=sku_key, color_signature=res.get("color_signature"),
            )

    try:
        res = publish_image(
            best_image["url"], name, brand, task["row_number"], worksheet, link_column_index,
            barcode=barcode, candidate_sha256=best_image.get("content_sha256"), page_url=best_image.get("page_url"),
            key_size=task_payload(task).get("size"), key_brand=brand, profile=processing_profile.current(),
            sku_key=sku_key, before_write=still_ours, duplicates="block", after_write=record,
        )
    except Exception as e:
        print(f"[Auto-Publish Error] فشل النشر التلقائي لـ [{name}]: {e}")
        return "failed"
    if res["status"] == "superseded":
        if res.get("error") == "publish_busy":
            return "busy"
        if why.get("rejected"):
            print(f"[Auto-Publish] الصف {task['row_number']}: رفض مراجع هذه الصورة أثناء المعالجة؛ لم يُكتب شيء.")
            return "rejected"
        print(f"[Auto-Publish] الصف {task['row_number']}: اعتمده مراجع أثناء المعالجة أو لم يعد محجوزاً لهذا "
              "العامل؛ لم يُكتب شيء.")
        return "superseded"
    if res.get("error") == "duplicate_image":
        _warn_duplicate(best_image)
    if res["status"] == "published":
        local_cache_db.delete_product_failure(barcode)
        if res.get("bg_skipped"):
            _count_bg_skipped()
            print(f"[Auto-Publish] الصف {task['row_number']}: انتشرت بدون عزل الخلفية (عزل الخلفية متوقف بالإعدادات).")
    elif res["status"] == "failed":
        print(f"[Auto-Publish] تعذر النشر لـ [{name}] ({res.get('error')}); يحال للمراجعة.")
    return res["status"]


# صور نشرها هذا العامل تلقائياً «بدون عزل الخلفية» باختيار المالك (تقرير التشغيل: run_report bg_skipped)
_BG_SKIPPED = {"count": 0}
_BG_SKIPPED_LOCK = threading.Lock()


def _count_bg_skipped():
    with _BG_SKIPPED_LOCK:
        _BG_SKIPPED["count"] += 1


def bg_skipped_count(reset=False):
    """عدد الصور التي نشرها العامل بدون عزل الخلفية منذ آخر تصفير (reset=True يصفّر بعد القراءة)."""
    with _BG_SKIPPED_LOCK:
        n = _BG_SKIPPED["count"]
        if reset:
            _BG_SKIPPED["count"] = 0
        return n


DUPLICATE_WARNING = "warn:duplicate_image"


def _warn_duplicate(best_image):
    """تحذير مراجعة على الصورة المختارة (ومرشحها المحفوظ): نفس الصورة منشورة لمنتج آخر."""
    url = best_image.get("url")
    for c in [best_image] + [c for c in best_image.get("candidates") or [] if isinstance(c, dict)]:
        if c is best_image or (url and (c.get("url") or c.get("image_url")) == url):
            reasons = c.setdefault("reasons", [])
            if isinstance(reasons, list) and DUPLICATE_WARNING not in reasons:
                reasons.append(DUPLICATE_WARNING)


def _has_human_approval(sku_key, alt_key=None):
    """
    هل للـ SKU حل معتمد بشرياً في الكاش؟ (خطأ القراءة يُعامل كنعم: الأمان أولاً، فلا نشر تلقائي).
    alt_key: المفتاح بلا باركود؛ اعتماد حُفظ قبل إضافة باركود صالح للصف يبقى اعتماداً لهذا المنتج.
    """
    for key in dict.fromkeys(k for k in (sku_key, alt_key) if k):
        try:
            cached = local_cache_db.get_cached_product(sku_key=key, strict=True)
        except Exception:
            return True
        if bool(cached) and cached.get("verification_status") == "human_approved":
            return True
    return False


def _finish_task(task, status, error_message=None, failure_code=None, trace=None, siblings=None):
    """
    تحديث حالة مهمة سحبها هذا العامل؛ لا يكتب فوق صف اعتمده مراجع أثناء المعالجة (ملكية الحجز).
    صفوف المنتج نفسه التي تنتظر تأخذ النتيجة نفسها (local_cache_db.update_task_status)؛ siblings عند النشر:
    معرفات الصفوف التي كُتب رابطها.
    """
    extra = {"siblings": siblings} if siblings else {}
    return local_cache_db.update_task_status(task["id"], status, error_message, failure_code=failure_code,
                                             trace=trace, claim_id=task.get("worker_id") or None, **extra)


def _task_alt_key(task, row, sku_key):
    """المفتاح البديل المحفوظ مع الصف عند الإدراج، أو يُحسب لصف مفتاحه باركود (لغيره يساوي sku_key)."""
    alt = task.get("alt_sku_key")
    if alt:
        return alt
    if not _is_gtin_key(sku_key):
        return sku_key
    try:
        return compute_alt_sku_key(row)
    except Exception:
        return sku_key


def _rejections(sku_key, alt_key=None):
    """رفض المراجعين بالمفتاح الحالي وبالمفتاح البديل (رفض حُفظ قبل إضافة باركود صالح يبقى سارياً)."""
    urls, phashes = local_cache_db.get_rejections(sku_key)
    urls, phashes = list(urls), list(phashes)
    if alt_key and alt_key != sku_key:
        more_urls, more_phashes = local_cache_db.get_rejections(alt_key)
        urls += [u for u in more_urls if u not in urls]
        phashes += [p for p in more_phashes if p not in phashes]
    return urls, phashes


def _size_key(text):
    """خلية الحجم للمقارنة: '1 L' و '1L' و '1l' واحدة."""
    from catalog_match.text_norm import match_key
    return match_key(str(text or "")).replace(" ", "")


def _same_named_resolution(res, name, brand):
    """الحل المحفوظ لهذا الاسم والبراند نفسيهما (بعد التطبيع: حالة الأحرف والمسافات والترقيم)."""
    from catalog_match.text_norm import match_key
    want = match_key(name)
    return (bool(want) and match_key(res.get("product_name")) == want
            and match_key(res.get("brand")).replace(" ", "") == match_key(brand).replace(" ", ""))


def _gtin_resolution_fits(res, name, brand, size, brand_index=None, sizes=()):
    """
    حل مفتاحه باركود يخدم هذا الصف؟ فحص الهوية نفسه الذي يمر به الكاش (cached_row_matches): باركود مشترك أو خاطئ
    لا يعطي الصف صورة منتج آخر. السجل المحفوظ لا يحفظ إلا الاسم، فحجم لا يذكره إلا عمود الحجم لا يتأكد منه أبداً،
    ولا حتى لاعتماد الصف نفسه (بحث مدفوع ومراجعة ثانية بدل كتابة مجانية). لذلك: نفس الاسم والبراند يكفي، إلا إذا
    ذكرت صفوف أخرى بالباركود نفسه حجماً آخر في عمود الحجم (sizes: أحجام صفوف هذا المفتاح)؛ عندها يبقى الفحص كاملاً.
    """
    if local_cache_db.cached_row_matches(res, name, brand, brand_index, size_text=size or None):
        return True
    own = _size_key(size)
    return (bool(own) and _same_named_resolution(res, name, brand)
            and not any(s and s != own for s in sizes or ()))


def rejected_image(sku_key, alt_key=None, url=None, phash=None):
    """
    هل رفض مراجعٌ هذه الصورة لهذا المنتج (بالمفتاح أو بالمفتاح البديل)؟ رابطها (بصيغة url_norm) أو pHash على مسافة
    استبعاد البحث نفسها (local_cache_db.image_rejected). None عندما تعذرت قراءة الرفض.
    """
    return local_cache_db.image_rejected((sku_key, alt_key), url, phash)


def _image_phash(best_image):
    """pHash الصورة المختارة كما قرأها البحث (أو مرشحها بنفس الرابط)، أو None."""
    if best_image.get("phash"):
        return best_image["phash"]
    for c in best_image.get("candidates") or []:
        if isinstance(c, dict) and (c.get("url") or c.get("image_url")) == best_image.get("url") and c.get("phash"):
            return c["phash"]
    return None


def _servable_resolution(sku_key, alt_key, name, brand, size, brand_mappings=None):
    """
    الحل المعتمد (human_approved / auto_verified) لهذا المنتج بمفتاحه أو بالمفتاح البديل، أو None.
    حل مفتاحه باركود لصف حجمه في عمود الحجم فقط يُقبل بنفس الاسم والبراند (_gtin_resolution_fits).
    """
    for key in dict.fromkeys(k for k in (sku_key, alt_key) if k):
        cached = local_cache_db.get_cached_product(sku_key=key, product_name=name, brand=brand,
                                                   brand_mappings=brand_mappings, size_text=size or None)
        if not cached and size and _is_gtin_key(key):
            plain = local_cache_db.get_cached_product(sku_key=key, product_name=name, brand=brand,
                                                      brand_mappings=brand_mappings)
            sizes = [_size_key(task_payload(t).get("size")) for t in local_cache_db.get_tasks_by_sku(key)]
            if plain and _gtin_resolution_fits(plain, name, brand, size, brand_mappings, sizes):
                cached = plain
        if cached and cached.get("cloudinary_url"):
            return cached
    return None


def _write_row_link(worksheet, link_column_index, row, link, metadata=None):
    """
    كتابة رابط معتمد وبياناته الوصفية في صف واحد بهوية الصف نفسه (الباركود والاسم والحجم والبراند)؛
    التفريغ يرفض الكتابة إن تغير المنتج في هذا الصف. تعيد True عند جدولة الرابط.
    """
    payload = task_payload(row)
    identity = {"barcode": row.get("barcode") or "", "product_name": row.get("product_name"),
                "size": payload.get("size"), "brand": row.get("brand") or ""}
    try:
        ok = google_sheets.update_image_link(worksheet, row["row_number"], link_column_index, link, **identity)
    except Exception as e:
        print(f"[Sheet] تعذر جدولة الرابط للصف {row.get('row_number')}: {e}")
        return False
    if ok and metadata:
        try:
            google_sheets.update_product_metadata(worksheet, row["row_number"], metadata, **identity)
        except Exception as e:
            print(f"تنبيه: تعذر كتابة البيانات الوصفية للصف {row.get('row_number')}: {e}")
    return bool(ok)


def _rejected_resolution(res, sku_key, alt_key):
    """
    هل رفض مراجع صورة هذا الحل بمفتاح الصف أو بمفتاحه البديل: رابطها الأصلي أو رابط Cloudinary، أو بصمة pHash على
    مسافة DUPLICATE_PHASH_DISTANCE أو أقل؟ خطأ قراءة الرفض يُعامل كنعم (لا نكتب ونحن لا نعرف).
    """
    try:
        urls, phashes = _rejections(sku_key, alt_key)
    except Exception as e:
        print(f"[Relink] تعذر قراءة رفض المراجعين: {e}")
        return True
    rejected = {local_cache_db.url_norm(u) for u in urls if u}
    if any(local_cache_db.url_norm(u) in rejected for u in (res.get("original_url"), res.get("cloudinary_url")) if u):
        return True
    mine = local_cache_db._phash_int(res.get("perceptual_hash"))
    if mine is None:
        return False
    for p in phashes:
        other = local_cache_db._phash_int(p)
        if other is not None and bin(mine ^ other).count("1") <= local_cache_db.DUPLICATE_PHASH_DISTANCE:
            return True
    return False


def _relink_task(task, worksheet, link_column_index, sku_key, alt_key, brand_mappings=None):
    """
    مهمة «كتابة رابط معتمد» (task_kind='relink' من الإدراج): للمنتج صورة معتمدة لكن رابطها ليس في هذا الصف
    (صف مكرر، كتابة انتهت CONFLICT / DEAD، أو صف عُدل وللمنتج الجديد اعتماد بشري). يُكتب الرابط بلا بحث.
    تعيد None عندما لم يعد للمنتج حل معتمد (رُفض منذ الإدراج)، أو رفض مراجع صورة الحل بمفتاح الصف أو بديله
    (اعتماد بالمفتاح القديم بقي بعد رفض بالمفتاح الجديد): يجري البحث العادي بدلها، والرفض يُستبعد فيه.
    """
    name, brand = task["product_name"], task.get("brand") or ""
    res = _servable_resolution(sku_key, alt_key, name, brand, task_payload(task).get("size"), brand_mappings)
    if res is None:
        return None
    if _rejected_resolution(res, sku_key, alt_key):
        print(f"[Relink] رفض مراجع الصورة المعتمدة للصف {task['row_number']}؛ لا تُكتب من جديد.")
        return None
    if worksheet is None or link_column_index is None:
        _finish_task(task, "failed", "No worksheet to write the approved link", failure_code="SHEET_WRITE_FAILED")
        return "failed"
    if not local_cache_db.is_claim_held(task["id"], task.get("worker_id")):
        print(f"[Relink] الصف {task['row_number']} لم يعد محجوزاً لهذا العامل؛ لا كتابة.")
        return "success"
    if not _write_row_link(worksheet, link_column_index, task, res["cloudinary_url"], res.get("metadata")):
        _finish_task(task, "failed", "Could not queue the approved link for the sheet",
                     failure_code="SHEET_WRITE_FAILED")
        return "failed"
    _finish_task(task, "completed", None, failure_code=None)
    local_cache_db.delete_product_failure(task.get("barcode") or "", sku_key=sku_key, product_name=name, brand=brand)
    print(f"[Relink] كُتب الرابط المعتمد في الصف {task['row_number']} بلا بحث ({task.get('requeue_reason') or ''}).")
    return "success"


def _publish_to_siblings(task, sku_key, worksheet, link_column_index):
    """
    النشر التلقائي لمنتج له صفوف أخرى في الشيت (نفس sku_key ونفس الهوية: local_cache_db.get_sku_siblings يستبعد
    منتجاً آخر يشاركه خلية الباركود) تنتظر: الصورة المنشورة تُكتب في كل صف بهويته.
    تعيد معرفات الصفوف التي جُدولت كتابتها (تُكمل مع الصف الأصلي)؛ صف تعذرت كتابته يُسجل SHEET_WRITE_FAILED
    والإدراج التالي يعيد كتابة الرابط المعتمد بلا بحث. صف عُدل بعد نشره (review_only) لا يُكتب فيه.
    """
    siblings = [s for s in local_cache_db.get_sku_siblings(task["id"], sku_key) if not s.get("review_only")]
    if not siblings:
        return []
    cached = local_cache_db.get_cached_product(sku_key=sku_key)
    link = (cached or {}).get("cloudinary_url")
    if not link or not local_cache_db.is_claim_held(task["id"], task.get("worker_id")):
        return []
    written = []
    for sib in siblings:
        if _write_row_link(worksheet, link_column_index, sib, link, (cached or {}).get("metadata")):
            written.append(sib["id"])
        else:
            local_cache_db.update_task_status(sib["id"], "failed", "Could not queue the published link for the sheet",
                                              failure_code="SHEET_WRITE_FAILED", siblings=())
    if written:
        print(f"[Auto-Publish] كُتب الرابط نفسه في {len(written)} صف آخر لنفس المنتج.")
    return written


# إعادة تحقق اكتملت بقارئ ملصق يعمل ولم تجد صورة أفضل: الصف ينتظر المراجع بمرشحاته السابقة (لا إعادة فحص أخرى)
RECHECK_NOT_FOUND = "RECHECK_NOT_FOUND"


def _record_spend(trace, run_id=None):
    """تكلفة محاولة بحث واحدة في سجل الصرف اليومي (search_spend). لا يرفع أبداً."""
    local_cache_db.record_search_spend(_outcome(trace), run_id)


def pre_cache_product_candidates(task, worksheet=None, link_column_index=None, brand_mappings=None,
                                 sleep=time.sleep, report=None):
    """
    البحث المسبق لمهمة من الطابور وحفظ مرشحاتها للمراجعة، أو نشرها إذا كان القرار AUTO_PUBLISH.
    تعيد 'success' | 'failed' | 'provider_down'.
    - مهمة relink: يُكتب الرابط المعتمد للمنتج بلا بحث (إلا صورة رفضها مراجع: بحث عادي).
    - صف عُدل بعد نشره (review_only) لا يُنشر تلقائياً أبداً: تُعرض النتيجة للمراجعة.
    - إعادة تحقق (VERIFIER_RECHECK) لم تجد شيئاً: يعود الصف جاهزاً للمراجعة بمرشحاته السابقة إن بقي منها شيء،
      وإلا فهي «لا نتيجة» عادية (فشل يُجدول ويُسجل).
    - رفض Serper كل استعلاماته (رصيد / مفتاح، أو حد السرعة 429) و«لا نتيجة»: انقطاع وليس فشلاً للمنتج؛ حد السرعة
      لا يُحسب في إيقاف العامل بسبب الرصيد.
    report (dict اختياري) يملؤه البحث للعامل: searched، serper_credit (True / False / None).
    """
    report = report if report is not None else {}
    name = task["product_name"]
    brand = task.get("brand") or ""
    barcode = task.get("barcode") or ""
    row_number = task["row_number"]
    payload = task_payload(task)
    row = sku_row(name, brand, barcode, payload)
    sku_key = task.get("sku_key") or payload.get("sku_key") or compute_sku_key(row, brand_mappings)
    alt_key = _task_alt_key(task, row, sku_key)
    query = (task.get("search_query") or "").strip() or default_query(name, brand)

    if task.get("task_kind") == local_cache_db.TASK_RELINK:
        result = _relink_task(task, worksheet, link_column_index, sku_key, alt_key, brand_mappings)
        if result is not None:
            return result
        print(f"[Relink] لم يعد للصف {row_number} حل معتمد؛ يجري البحث بدلاً من ذلك.")

    exclude_urls, exclude_phashes = _rejections(sku_key, alt_key)
    search_kwargs = {
        "product_name_ar": payload.get("name_ar", ""),
        "brand_ar": payload.get("brand_ar", ""),
        "barcode": barcode,
        "category": payload.get("category", ""),
        "sub_category": payload.get("sub_category", ""),
        "size_text": payload.get("size", ""),
        "origin": payload.get("origin", ""),
        "custom_query": custom_query_for(query, name, brand),
        "exclude_urls": list(exclude_urls),
        "exclude_phashes": list(exclude_phashes),
        "skip_cache": bool(getattr(config, "SKIP_LOCAL_CACHE", False)),
    }
    if brand_mappings:
        search_kwargs["brand_mappings"] = brand_mappings

    print(f"[Pre-Cache] البحث عن مرشحات لـ [{name}] (SKU {sku_key})")
    run_id = task.get("run_id") or None
    best, trace, state, error = search_with_retry(query, name, brand, search_kwargs, sleep=sleep,
                                                  on_attempt=lambda tr: _record_spend(tr, run_id))
    credit = serper_credit_refused(trace)
    report["searched"] = True
    report["serper_credit"] = credit
    recheck = task.get("requeue_reason") == "VERIFIER_RECHECK"

    if best is None:
        if state == "error":
            code, message = "SEARCH_ERROR", error or "search raised an exception"
        else:
            code = _outcome(trace).get("failure_code") or "NO_RESULTS"
            message = f"No acceptable image found ({code})"
        # Serper رد بحد السرعة (429) على كل استعلاماته: انقطاع عابر ينتظر موعده، لا «لا نتيجة» ولا رصيد منتهٍ
        throttled = state == "ok" and not credit and serper_rate_limited(trace)
        down = state == "provider_down" or (state == "ok" and credit) or throttled
        if recheck and local_cache_db.has_review_candidates(row_number, sku_key):
            # إعادة التحقق لم تأتِ بجديد والمرشحات السابقة ما زالت أمام المراجع: يعود الصف للمراجعة ولا يضيع.
            # بحث لم يكتمل (انقطاع أو خطأ) يبقى VERIFIER_DOWN فيُعاد فحصه في تشغيل لاحق؛ بحث اكتمل بقارئ يعمل
            # ولم يجد شيئاً: RECHECK_NOT_FOUND (لا إعادة فحص أخرى، المراجع يقرر)
            if down or state == "error":
                _finish_task(task, "ready_for_review", f"Re-verification could not search ({code})",
                             failure_code="VERIFIER_DOWN", trace=trace)
                return "provider_down" if down else "success"
            _finish_task(task, "ready_for_review", f"Re-verification found nothing new ({code})",
                         failure_code=RECHECK_NOT_FOUND, trace=trace)
            return "success"
        # إعادة تحقق لم يبقَ لها مرشح (رفضها المراجع) نتيجة عادية: الانقطاع ينتظر، و«لا نتيجة» تُسجل وتُجدول
        if down:
            # انقطاع المزودين (أو رصيد Serper) ليس فشلاً للمنتج: يعود الصف للانتظار بموعد ولا يُسجل في product_failures
            message = ("Serper refused every query (credit or key)" if credit
                       else "Serper rate-limited every query (HTTP 429)" if throttled
                       else "Search providers unavailable")
            _finish_task(task, "pending", message, failure_code="PROVIDER_DOWN", trace=trace)
            return "provider_down"
        _finish_task(task, "failed", message, failure_code=code, trace=trace)
        local_cache_db.save_product_failure(barcode, name, brand, f"{code}: {message}", sku_key=sku_key)
        print(f"[Pre-Cache] لا توجد صورة للصف {row_number}: {code}")
        return "failed"

    if not local_cache_db.is_claim_held(task["id"], task.get("worker_id")):
        # مراجع اعتمد/رفض هذا الصف أثناء البحث، أو أعيد سحبه: لا نستبدل مرشحاته ولا ننشر فوقه
        print(f"[Pre-Cache] الصف {row_number} لم يعد محجوزاً لهذا العامل؛ تُهمل النتيجة.")
        return "success"

    decision = best.get("decision")
    if decision == "AUTO_PUBLISH" and _has_human_approval(sku_key, alt_key):
        # لا يُنشر تلقائياً فوق صورة اعتمدها مراجع: تُعرض النتيجة للمراجعة فقط
        print(f"[Auto-Publish] الصف {row_number} له اعتماد بشري سابق؛ يحال للمراجعة بدل النشر التلقائي.")
        decision = "REVIEW_PRESELECTED"
    if decision == "AUTO_PUBLISH" and task.get("review_only"):
        # الصف عُدل بعد نشر صورته (أو مُسح رابطه): الصورة الجديدة يراها مراجع قبل أي كتابة
        print(f"[Auto-Publish] الصف {row_number} للمراجعة فقط ({task.get('requeue_reason') or 'review_only'}); "
              "لا نشر تلقائي.")
        decision = "REVIEW_PRESELECTED"
    status = None
    if (decision == "AUTO_PUBLISH" and best.get("source") != "sqlite_cache"
            and worksheet is not None and link_column_index is not None):
        status = auto_approve_product(task, best, worksheet, link_column_index, sku_key=sku_key)
        if status == "published":
            written = _publish_to_siblings(task, sku_key, worksheet, link_column_index)
            _finish_task(task, "completed", failure_code=None,
                         trace={"outcome": _outcome(trace)}, siblings=written)
            print(f"[Auto-Publish] تم نشر الصف {row_number} تلقائياً (قرار AUTO_PUBLISH).")
            return "success"
        held = local_cache_db.is_claim_held(task["id"], task.get("worker_id"))
        if status == "busy" and held:
            # نشر آخر لنفس المنتج احتفظ بالقفل حتى المهلة: يعود الصف للانتظار الآن بدل أن يبقى «قيد المعالجة»
            # حتى ينتهي حجزه (15 دقيقة)
            _finish_task(task, "pending", "Another publish of this product held the publish lock",
                         failure_code="PUBLISH_BUSY", trace={"outcome": _outcome(trace)})
            return "success"
        if status == "superseded" and held and _has_human_approval(sku_key, alt_key):
            # اعتمده مراجع أثناء المعالجة (دون أن يسحب الحجز): المنتج منتهٍ، ويُحرر الحجز
            _finish_task(task, "completed", failure_code=None, trace={"outcome": _outcome(trace)}, siblings=())
            return "success"
        if status in ("superseded", "busy") or not held:
            # قرار المراجع (أو حجز أحدث) أثناء المعالجة والرفع يبقى كما هو: لا مرشحات ولا حالة فوقه
            return "success"
        # الخلفية لم تُعزل (needs_review) أو فشل النشر أو صورة مكررة: المرشحات للمراجعة كما في أي نتيجة غير منشورة؛
        # رفض مراجع الصورة المختارة أثناء المعالجة: باقي المرشحات (بدونها) للمراجعة

    candidates = collect_candidates(best, trace)
    try:
        rejected_urls, rejected_phashes = _rejections(sku_key, alt_key)
        candidates = [c for c in candidates if not local_cache_db.rejected_among(
            c.get("url") or c.get("image_url"), c.get("phash"), rejected_urls, rejected_phashes)]
    except Exception as e:
        print(f"تنبيه: تعذر قراءة رفض المراجعين قبل حفظ المرشحات: {e}")
    if status == "rejected" and not candidates:
        # لم يبق إلا الصورة المرفوضة: بحث جديد يستبعدها
        _finish_task(task, "pending", "The pick was rejected while it was being published", failure_code="REJECTED",
                     trace={"outcome": _outcome(trace)})
        return "success"
    saved = local_cache_db.save_curation_candidates(
        row_number, name, brand, candidates, best.get("url"), sku_key=sku_key, run_id=uuid.uuid4().hex[:16],
        identity=task)
    if not saved:
        _finish_task(task, "failed", "Could not save review candidates",
                     failure_code="CANDIDATE_SAVE_FAILED", trace=trace)
        return "failed"
    _finish_task(task, "ready_for_review", None,
                 failure_code=best.get("failure_code"), trace={"outcome": _outcome(trace)})
    print(f"[Pre-Cache] {len(candidates)} مرشح للصف {row_number} (القرار: {decision or 'v1'}).")
    return "success"


# ---------------------------------------------------------------------------
# الوضع التسلسلي القديم (بدون طابور)
# ---------------------------------------------------------------------------

def process_single_product(prod, worksheet, link_column_index, brand_mappings=None):
    """
    معالجة منتج واحد مباشرة (الوضع القديم). الاسم والبراند يُمرران كما هما في الشيت؛
    QueryRefiner يُستخدم فقط لكتابة الاسم/البراند العربي الناقص في الشيت.
    """
    row_num = prod["row_number"]
    name = prod["product_name"]
    brand = prod.get("brand") or ""
    barcode = prod.get("barcode") or ""
    if prod.get("existing_image_link") and not config.FORCE_OVERWRITE_IMAGES:
        print(f"تخطي الصف {row_num}: يحتوي بالفعل على رابط صورة نهائي.")
        return "skipped"

    # الاسم/البراند العربي للبحث يأتيان من الشيت فقط. ناتج QueryRefiner (تخمين نموذج لغوي) يُكتب في الشيت
    # للتعريب ولا يدخل هوية البحث أبداً (D8/D9): وإلا صار تخمين البراند العربي 'mapped' وقابلاً للنشر التلقائي.
    product_name_ar = prod.get("product_name_ar", "")
    brand_ar = prod.get("brand_ar", "")
    try:
        from query_refiner import QueryRefiner
        refined = QueryRefiner.refine_product_metadata(name, brand, prod.get("category", ""))
        google_sheets.update_product_localization(worksheet, row_num, refined.get("cleaned_title_ar", ""),
                                                  refined.get("canonical_brand_ar", ""))
    except Exception as e:
        print(f"تنبيه: فشل التعريب المسبق عبر Gemini: {e}")

    payload = {"name_ar": product_name_ar, "brand_ar": brand_ar, "category": prod.get("category", ""),
               "sub_category": prod.get("sub_category", ""), "origin": prod.get("origin", ""),
               "size": prod.get("size", "")}
    sku_key = compute_sku_key(sku_row(name, brand, barcode, payload), brand_mappings)
    exclude_urls, exclude_phashes = local_cache_db.get_rejections(sku_key)
    query = default_query(name, brand)
    search_kwargs = {
        "product_name_ar": product_name_ar, "brand_ar": brand_ar, "barcode": barcode,
        "category": payload["category"], "sub_category": payload["sub_category"],
        "size_text": payload["size"], "origin": payload["origin"],
        "exclude_urls": exclude_urls, "exclude_phashes": exclude_phashes,
        "skip_cache": bool(getattr(config, "SKIP_LOCAL_CACHE", False)),
    }
    if brand_mappings:
        search_kwargs["brand_mappings"] = brand_mappings
    # الصرف في سجل اليوم نفسه (الميزانية اليومية تحسب كل بحث مدفوع، لا عامل الطابور وحده)
    best, trace, state, error = search_with_retry(query, name, brand, search_kwargs,
                                                  on_attempt=lambda tr: _record_spend(tr, "sequential"))
    if not best:
        if state == "provider_down":
            print(f"محركات البحث غير متاحة؛ الصف {row_num} لم يُعالج.")
            return "failed"
        code = "SEARCH_ERROR" if state == "error" else (_outcome(trace).get("failure_code") or "NO_RESULTS")
        config.log_and_fail(barcode, name, brand, f"{code}: لم يتم العثور على صورة مقبولة.")
        return "failed"

    # REVIEW_UNSELECTED (url=None): لا يوجد اختيار مسبق؛ تُحفظ المرشحات للمراجعة ولا يُنشر ولا يُكتب شيء في الشيت
    if getattr(config, 'CURATION_MODE', False) or not best.get("url"):
        candidates = collect_candidates(best, trace)
        if not local_cache_db.save_curation_candidates(row_num, name, brand, candidates, best.get("url"),
                                                       sku_key=sku_key, run_id=uuid.uuid4().hex[:16],
                                                       identity=dict(payload, name=name, brand=brand)):
            config.log_and_fail(barcode, name, brand, "CANDIDATE_SAVE_FAILED: تعذر حفظ المرشحات.")
            return "failed"
        if not best.get("url"):
            print(f"الصف {row_num}: لا يوجد مرشح مؤكد ({best.get('decision')}); المرشحات محفوظة للمراجعة.")
            return "success"
        ok = google_sheets.update_image_link(worksheet, row_num, link_column_index, f"needs_review:{best['url']}",
                                             barcode=barcode, product_name=name, size=payload["size"], brand=brand)
        return "success" if ok else "failed"

    decision = best.get("decision")
    if best.get("source") == "sqlite_cache":
        value = f"needs_review:{best['url']}" if best.get("needs_review", True) else best["url"]
        ok = google_sheets.update_image_link(worksheet, row_num, link_column_index, value,
                                             barcode=barcode, product_name=name, size=payload["size"], brand=brand)
        return "success" if ok else "failed"

    res = publish_image(best["url"], name, brand, row_num, worksheet, link_column_index, barcode=barcode,
                        candidate_sha256=best.get("content_sha256"), profile=processing_profile.current(),
                        force_review=decision != "AUTO_PUBLISH", key_size=payload["size"], key_brand=brand,
                        sku_key=sku_key, duplicates="review")
    if res["status"] == "failed":
        config.log_and_fail(barcode, name, brand, f"فشل النشر: {res.get('error')}")
        return "failed"
    if res["status"] == "published":
        local_cache_db.save_product_resolution(
            barcode, name, brand, best["url"], res["link"], None, res.get("metadata"),
            verification_status="auto_verified", approved_by="auto", sku_key=sku_key)
    return "success"


# ---------------------------------------------------------------------------
# بناء الطابور
# ---------------------------------------------------------------------------

LOCK_FILE = "temp/pipeline.lock"

# نتيجة آخر عامل في هذه العملية (يقرؤها التشغيل الليلي و`python main.py --worker` لرمز الخروج):
# {stop_reason, run_id, worker_id, started_ts, ended_ts, notice, health, bg_skipped}. stop_reason None = الطابور انتهى.
# bg_skipped: صور نُشرت تلقائياً بدون عزل الخلفية باختيار المالك في هذا التشغيل.
LAST_WORKER = {}
# سبب فشل آخر إدراج في هذه العملية (_enqueue_failed): {reason, message}
LAST_ENQUEUE = {}


def _release_starting_lock(lock_file=LOCK_FILE):
    """يحذف قفل لوحة التحكم 'STARTING' (مرحلة الإدراج) فقط؛ قفل يحمل PID يحرره صاحبه (العامل أو التشغيل الليلي)."""
    try:
        with open(lock_file, "r") as f:
            if f.read().strip() != "STARTING":
                return
        os.remove(lock_file)
    except OSError:
        pass


def _enqueue_failed(message, reason="enqueue_failed"):
    """
    فشل الإدراج: رسالة عربية في automation_state.notice بحالة 'error' تعرضها اللوحة فوراً، وتحرير قفل 'STARTING'
    فوراً (لا تبقى اللوحة على «قيد التشغيل» وترفض تشغيلاً جديداً لخمس دقائق). العامل لا يبدأ (رمز الخروج 1).
    reason (في LAST_ENQUEUE للتشغيل الليلي): sheets_unavailable / db_unavailable انقطاع تُعاد محاولته ليلاً؛
    sheet_config (إعداد أو بيانات اعتماد) و sheet_not_found (الشيت غير موجود أو غير مشارك) و enqueue_failed فشل
    لا تصلحه إعادة المحاولة (رمز 1).
    """
    LAST_ENQUEUE.update(reason=reason, message=message)
    print(f"[Enqueue Error] {message}")
    # التشغيل انتهى هنا: طلب إيقاف سُجل أثناء الإدراج يُلغى، وإلا أوقف عاملاً يُشغَّل لاحقاً يدوياً قبل أي منتج
    local_cache_db.update_automation_state(status="error", current_product="", notice=f"ENQUEUE_FAILED: {message}",
                                           stop_requested=0)
    _release_starting_lock()
    sys.exit(1)


# الإدراج يسلّم التشغيل الذي أنشأه (run_id) للعامل التالي: التشغيل الليلي في العملية نفسها، ولوحة التحكم في عملية
# `main.py --worker` التالية. عامل يدوي بلا إدراج لا يجد تسليماً، فلا يُنسب إليه التشغيل السابق في automation_state.
RUN_HANDOFF_FILE = "temp/run_handoff.json"


def _write_run_handoff(run_id, path=None):
    path = path or RUN_HANDOFF_FILE
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = f"{path}.{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"run_id": run_id, "pid": os.getpid(), "ts": round(time.time(), 3)}, f)
        os.replace(tmp, path)
    except OSError as e:
        print(f"[Enqueue] تنبيه: تعذر تسليم التشغيل {run_id} للعامل: {e}")


def _claim_run_handoff(run_id, path=None):
    """هل أنشأ إدراجٌ التشغيل run_id ولم يأخذه عامل بعد؟ يستهلك التسليم (يُحذف) في كل الأحوال."""
    path = path or RUN_HANDOFF_FILE
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return False
    try:
        os.remove(path)
    except OSError:
        pass
    return bool(run_id) and isinstance(data, dict) and data.get("run_id") == run_id


def _enqueue_payload(prod):
    return {
        "name_ar": prod.get("product_name_ar", ""),
        "brand_ar": prod.get("brand_ar", ""),
        "category": prod.get("category", ""),
        "sub_category": prod.get("sub_category", ""),
        "origin": prod.get("origin", ""),
        "size": prod.get("size", ""),
    }


def _row_was_edited(prod, sku_key, alt_key, link_rows):
    """
    رابط الصف النهائي نُشر لمنتج آخر: الحلول التي تحمل هذا الرابط كلها لمفاتيح غير مفتاح الصف (أو بديله)،
    واسمها أو براندها (أو باركود صالح مختلف) غير ما في الصف الآن. اختلاف المفتاح وحده (عمود الحجم، صيغة قديمة
    للمفتاح) لا يكفي: لا يُعاد البحث عن صف لم يتغير منتجه.
    """
    from catalog_match.text_norm import match_key
    if not link_rows or any(r.get("sku_key") in (sku_key, alt_key) for r in link_rows):
        return False
    name, brand = match_key(prod.get("product_name")), match_key(prod.get("brand"))
    gtin = local_cache_db._cache_barcode(prod.get("barcode"))
    for r in link_rows:
        if match_key(r.get("product_name")) == name and match_key(r.get("brand")) == brand:
            other = local_cache_db._cache_barcode(r.get("barcode"))
            if not (gtin and other and gtin != other):
                return False
    return True


def _snapshot_resolution(snapshot, prod, payload, sku_key, alt_key, brand_index, human_only=False, sizes=()):
    """
    الحل المعتمد لمنتج الصف من لقطة الحلول: بالمفتاح ثم بالبديل. حل مفتاحه باركود يمر بفحص الهوية نفسه
    الذي يمر به الكاش (_gtin_resolution_fits): باركود مشترك أو خاطئ لا يعطي الصف صورة منتج آخر، واعتماد الصف
    نفسه لا يُرفض لأن حجمه في عمود الحجم فقط. sizes: أحجام صفوف الشيت التي تحمل هذا الباركود.
    """
    for key in dict.fromkeys(k for k in (sku_key, alt_key) if k):
        res = snapshot["by_key"].get(key)
        if not res or (human_only and res.get("verification_status") != "human_approved"):
            continue
        if _is_gtin_key(key) and not _gtin_resolution_fits(
                res, prod.get("product_name"), prod.get("brand") or "", payload.get("size"), brand_index, sizes):
            continue
        return res
    return None


def _sizes_by_gtin(products):
    """{GTIN-14: أحجام عمود الحجم (بعد التطبيع)} لصفوف الشيت ذات الباركود الصالح: صفان بباركود واحد وحجمين مختلفين
    يعنيان باركوداً خاطئاً، فلا يكفي تطابق الاسم والبراند لإعطاء أحدهما صورة الآخر."""
    from catalog_match.gtin import normalize_gtin
    out = {}
    for prod in products:
        try:
            gtin, status = normalize_gtin(str(prod.get("barcode") or "").strip())
        except Exception:
            continue
        if status == "ok" and gtin:
            out.setdefault(gtin, set()).add(_size_key(prod.get("size")))
    return out


# google_sheets.outbox_outcomes: صفوف لكل استدعاء، ونتائج لكل صفحة (limit)
OUTBOX_ROWS_PER_CALL = 500
OUTBOX_PAGE = 500


def _outbox_pages(fn, row_numbers):
    """
    كل نتائج كتابات الشيت لهذه الصفوف من outbox_outcomes. بلا since_id تعيد أحدث limit نتيجة فقط (500)، فمع صفوف
    كثيرة (وكتابات بيانات وصفية لكل صف) تضيع كتابات الرابط الأقدم بصمت. لذلك تُقرأ الصفوف دفعات، وكل دفعة صفحات
    تصاعدية بـ since_id حتى آخرها. شكل آخر للجواب (dict) يُعاد كما هو.
    """
    rows = sorted({int(r) for r in row_numbers})
    out = []
    for start in range(0, len(rows), OUTBOX_ROWS_PER_CALL):
        part = rows[start:start + OUTBOX_ROWS_PER_CALL]
        since = 0
        while True:
            page = fn(part, since_id=since, limit=OUTBOX_PAGE)
            if not isinstance(page, (list, tuple)):
                return page
            out.extend(page)
            ids = []
            for r in page:
                try:
                    ids.append(int(r.get("id")))
                except (AttributeError, TypeError, ValueError):
                    pass
            if len(page) < OUTBOX_PAGE or not ids or max(ids) <= since:
                break
            since = max(ids)
    return out


def _outbox_records(row_numbers):
    """
    كتابات الشيت لهذه الصفوف: {row_number: [{value, status, id}]} بالترتيب. من google_sheets.outbox_outcomes إن
    وُجدت (حزمة الشيت، كل الصفحات: _outbox_pages)، وإلا قراءة طابور الكتابة مباشرة. أي خطأ يعيد {} (حالة الكتابة
    مجهولة).
    """
    raw = None
    fn = getattr(google_sheets, "outbox_outcomes", None)
    if callable(fn):
        try:
            try:
                raw = _outbox_pages(fn, row_numbers)
            except TypeError:
                # دالة بتوقيع أقدم (بلا since_id / limit، أو بلا وسائط)
                try:
                    raw = fn(list(row_numbers))
                except TypeError:
                    raw = fn()
        except Exception as e:
            print(f"تنبيه: تعذر قراءة نتائج كتابات الشيت: {e}")
            raw = None
    if raw is None:
        raw = local_cache_db.outbox_link_writes(row_numbers)
    items = []
    if isinstance(raw, dict):
        for row, value in raw.items():
            for v in (value if isinstance(value, (list, tuple)) else [value]):
                rec = dict(v) if isinstance(v, dict) else {"status": v}
                rec.setdefault("row_number", row)
                items.append(rec)
    elif isinstance(raw, (list, tuple)):
        items = [dict(r) for r in raw if isinstance(r, dict)]
    out = {}
    for i, rec in enumerate(items):
        if rec.get("column_key") not in (None, "", "link"):
            continue                            # كتابات البيانات الوصفية (meta:*) ليست الرابط
        try:
            # google_sheets.outbox_outcomes يسمي الصف row (الصف الحالي بعد أي نقل)؛ قراءة الجدول مباشرة row_number
            row = int(rec.get("row_number", rec.get("row")))
        except (TypeError, ValueError):
            continue
        status = str(rec.get("sync_status") or rec.get("status") or rec.get("outcome") or "").strip().upper()
        out.setdefault(row, []).append({"value": rec.get("value"), "status": status, "id": rec.get("id") or i})
    for recs in out.values():
        recs.sort(key=lambda r: (r["id"] if isinstance(r["id"], int) else 0))
    return out


def _link_write_state(records, link):
    """حالة آخر كتابة لهذا الرابط في هذا الصف (PENDING / FAILED / SYNCED / CONFLICT / DEAD ...)، أو None."""
    matching = [r for r in records or [] if r.get("value") in (None, link)]
    return matching[-1]["status"] if matching else None


def _brand_index_phrases(spec):
    """
    كل عبارة براند (اسم الشيت ومرادفاته) ككلماتها في الفهرس المحلي (3 أحرف فأكثر، tuple مرتبة)، للكشف عن صفحات
    جديدة للبراند: الصفحة تحمل كل كلمات العبارة، لا أطولها وحدها ('sun' من «Sun Top» تطابق كل براند فيه sun).
    """
    from catalog_match.local_index import index_keys
    out = set()
    for phrase in tuple(spec.match_brands or ()) + (spec.brand_raw or "",):
        keys = tuple(sorted({k for k in index_keys(phrase) if len(k) >= 3}))
        if keys:
            out.add(keys)
    return out


def plan_enqueue(products, reprocess=False, brand_mappings=None):
    """
    صفوف الشيت (بعد الفلاتر) -> (صفوف local_cache_db.add_many_to_queue، عدادات). المطابقة عند الإدراج:
    (a) صف بلا رابط نهائي ولمنتجه (sku_key أو المفتاح البديل) صورة معتمدة: مهمة كتابة الرابط (relink) بدل بحث جديد.
        خلية فيها needs_review: تُكتب فقط من اعتماد بشري. كتابة سابقة ما زالت في الطابور (PENDING / FAILED) تُترك؛
        كتابة نجحت ثم مُسح الرابط من الشيت (SYNCED) لا تُعاد كتابتها: بحث للمراجعة فقط (LINK_CLEARED).
    (b) صف رابطه منشور لمنتج آخر (عُدل الصف بعد النشر): بحث للمراجعة فقط (ROW_EDITED)، لا نشر تلقائي فوقه؛
        وإن كان للمنتج الجديد اعتماد بشري يُكتب رابطه.
    (c) صف مكتمل لم يصل رابطه للشيت (CONFLICT / DEAD) يُعاد كتابته (a)، وبلا حل معتمد: بحث للمراجعة (LINK_MISSING).
    صف «لا نتيجة» ظهرت لبراند منتجه صفحات جديدة في الفهرس المحلي منذ آخر بحث: LOCAL_INDEX_CHANGED.
    صفوف المنتج نفسه تتبع صفاً للمراجعة فقط منها (بحث واحد لكل منتج، فلا ينشر أحدها تلقائياً).
    """
    from catalog_match.brand_index import build_index
    from catalog_match.identity import build_sku_spec

    index = build_index(brand_mappings) if brand_mappings else None   # يُبنى مرة واحدة لكل الصفوف
    stats = {"skipped_final": 0, "relink": 0, "edited": 0, "cleared": 0, "missing": 0, "in_flight": 0,
             "index_changed": 0}
    snapshot = local_cache_db.resolution_snapshot()
    queue = local_cache_db.queue_snapshot()
    gtin_sizes = _sizes_by_gtin(products)
    entries = []
    for prod in products:
        name, brand = prod["product_name"], prod.get("brand") or ""
        barcode = prod.get("barcode", "")
        payload = _enqueue_payload(prod)
        row = sku_row(name, brand, barcode, payload)
        spec = build_sku_spec(row, index)
        sku_key, alt_key = spec.sku_key, compute_alt_sku_key(row, spec)
        entry = {"prod": prod, "payload": payload, "spec": spec, "sku_key": sku_key, "alt_key": alt_key,
                 "brand_fp": brand_fingerprint(spec), "task_kind": None, "review_only": False, "reason": None}
        link = prod.get("existing_image_link")
        if link and not reprocess:
            if not _row_was_edited(prod, sku_key, alt_key,
                                   snapshot["by_url"].get(local_cache_db.url_norm(link)) or []):
                stats["skipped_final"] += 1
                continue
            stats["edited"] += 1
            entry["reason"] = "ROW_EDITED"
            if _snapshot_resolution(snapshot, prod, payload, sku_key, alt_key, index, human_only=True,
                                    sizes=gtin_sizes.get(sku_key)):
                entry["task_kind"] = local_cache_db.TASK_RELINK
            else:
                entry["review_only"] = True
        elif not reprocess:
            res = _snapshot_resolution(snapshot, prod, payload, sku_key, alt_key, index,
                                       human_only=bool(prod.get("needs_review")), sizes=gtin_sizes.get(sku_key))
            old = queue.get(prod["row_number"])
            if res:
                entry["resolution"] = res
            elif old and old.get("status") == "completed" and old.get("sku_key") == sku_key:
                entry["review_only"], entry["reason"] = True, "LINK_MISSING"
                stats["missing"] += 1
        entries.append(entry)

    waiting = [e for e in entries if e.get("resolution")]
    outbox = _outbox_records([e["prod"]["row_number"] for e in waiting]) if waiting else {}
    for e in waiting:
        state = _link_write_state(outbox.get(e["prod"]["row_number"]), e["resolution"]["cloudinary_url"])
        if state in ("PENDING", "FAILED"):
            e["skip"] = True
            stats["in_flight"] += 1
        elif state == "SYNCED":
            e["review_only"], e["reason"] = True, "LINK_CLEARED"
            stats["cleared"] += 1
        else:
            e["task_kind"] = local_cache_db.TASK_RELINK
            e["reason"] = "SHEET_WRITE_RETRY" if state in ("CONFLICT", "DEAD") else "APPROVED_IMAGE"

    # «لا نتيجة» تنتظر موعدها: هل ظهرت صفحات جديدة للبراند في الفهرس المحلي منذ آخر بحث؟ صف استنفد محاولاته
    # (3 / 7 / 30 يوماً) لا يعود بها كل ليلة: يبقى فاشلاً حتى يتغير مدخل البراند أو يُطلب من جديد
    sleeping = []
    for e in entries:
        old = queue.get(e["prod"]["row_number"])
        if (not e.get("skip") and e["task_kind"] is None and not e["reason"] and old
                and old.get("status") == "failed" and old.get("failure_code") in local_cache_db.NOT_FOUND_CODES
                and old.get("sku_key") == e["sku_key"] and old.get("searched_at")
                and int(old.get("fail_count") or 0) <= len(local_cache_db.NOT_FOUND_RETRY_DAYS)):
            e["tokens"] = _brand_index_phrases(e["spec"])
            sleeping.append((e, old["searched_at"]))
    if sleeping:
        news = local_cache_db.catalog_brand_news(set().union(*(e["tokens"] for e, _ in sleeping)))
        for e, searched_at in sleeping:
            newest = [news[t] for t in e["tokens"] if t in news]
            if newest and max(newest) > searched_at:
                e["reason"] = "LOCAL_INDEX_CHANGED"
                stats["index_changed"] += 1

    # بحث واحد لكل منتج: إن كان أحد صفوف المنتج للمراجعة فقط فنتيجة بحثه (التي تُطبق على كل صفوفه) للمراجعة
    review_skus = {e["sku_key"] for e in entries if e["review_only"] and not e.get("skip")}
    rows = []
    for e in entries:
        if e.get("skip"):
            continue
        if e["task_kind"] == local_cache_db.TASK_RELINK:
            stats["relink"] += 1
        elif e["sku_key"] in review_skus:
            e["review_only"] = True
        prod = e["prod"]
        name, brand = prod["product_name"], prod.get("brand") or ""
        rows.append(local_cache_db.queue_input(
            prod["row_number"], prod.get("barcode", ""), name, brand, prod.get("search_query") or default_query(name, brand),
            payload=e["payload"], sku_key=e["sku_key"], alt_sku_key=e["alt_key"], brand_fp=e["brand_fp"],
            task_kind=e["task_kind"], review_only=e["review_only"], requeue_reason=e["reason"]))
    return rows, stats


def run_enqueue_mode():
    """
    قراءة الشيت وإضافة الصفوف للطابور (Upsert). يتم التحقق من الاتصال وعناوين الأعمدة والفلاتر
    قبل لمس الطابور، ولا تُمسح صفوف المراجعة أبداً. كل إدراج يبدأ تشغيلاً جديداً (run_id) يحمله كل صف
    سيعالجه العامل (local_cache_db.begin_run)، فيُحسب التقدم من صفوف هذا التشغيل فقط.
    المطابقة مع الكاش وطابور الكتابة (plan_enqueue) تسبق الإدراج، والإدراج دفعات (add_many_to_queue).
    """
    try:
        load_run_config()
        google_sheets.clear_cache()
        try:
            allowed_rows = parse_row_filter(config.ROW_FILTER)
        except ValueError:
            _enqueue_failed(f"فلتر الصفوف غير صالح: «{config.ROW_FILTER}». اكتب أرقام صفوف أو نطاقات مثل 5-20 "
                            "أو 5,8,12. لم يتغير الطابور.")

        sheets_client = google_sheets.get_sheets_client()
        if not sheets_client:
            # gspread.service_account لا يتصل بالشبكة: الفشل هنا ملف اعتماد مفقود أو تالف (إعداد، لا انقطاع)
            _enqueue_failed(f"تعذر تحميل بيانات اعتماد Google من الملف «{config.CREDENTIALS_FILE}» (مفقود أو تالف). "
                            "تحقق من ملف بيانات الاعتماد. لم يتغير الطابور.", reason="sheet_config")
        worksheet = google_sheets.open_worksheet(sheets_client, config.SPREADSHEET_NAME_OR_URL)
        if not worksheet:
            _enqueue_failed(f"لم يُعثر على الشيت «{config.SPREADSHEET_NAME_OR_URL}». تحقق من الرابط واسم ورقة "
                            "العمل ومن مشاركة الشيت مع حساب الخدمة. لم يتغير الطابور.", reason="sheet_not_found")
        products, _ = google_sheets.get_products(worksheet)
        products = products or []
        if not products:
            # لا صفوف جديدة؛ التشغيل يعالج ما بقي في الانتظار فقط (begin_run أدناه)
            print("[Enqueue] لم يتم العثور على أي منتجات صالحة.")
        brand_mappings = google_sheets.get_brand_mappings(sheets_client, config.SPREADSHEET_NAME_OR_URL) \
            if products else {}
    except SystemExit:
        raise
    except Exception as e:
        _enqueue_failed(f"تعذر قراءة الشيت: {e}. لم يتغير الطابور.", reason=_sheet_failure_reason(e))

    reprocess = bool(getattr(config, "FORCE_OVERWRITE_IMAGES", False))
    brand_filter = (config.BRAND_FILTER or "").lower()
    selected = [prod for prod in products
                if (allowed_rows is None or prod["row_number"] in allowed_rows)
                and (not brand_filter or brand_filter in (prod.get("brand") or "").lower())]
    done = {"insert": 0, "keep": 0, "reset": 0}
    try:
        rows, stats = plan_enqueue(selected, reprocess=reprocess, brand_mappings=brand_mappings)
        local_cache_db.add_many_to_queue(rows, reprocess=reprocess, totals=done)
        print(f"[Enqueue] {len(rows)} صف في الطابور (جديد {done['insert']}، يعود للانتظار {done['reset']}، "
              f"باقٍ كما هو {done['keep']})؛ {stats['skipped_final']} صف تم تخطيه لأن رابطه نهائي.")
        if stats["relink"] or stats["in_flight"]:
            print(f"[Enqueue] {stats['relink']} صف لمنتج له صورة معتمدة: يُكتب رابطها بلا بحث؛ "
                  f"{stats['in_flight']} كتابة ما زالت في طابور الشيت.")
        if stats["edited"] or stats["cleared"] or stats["missing"]:
            print(f"[Enqueue] للمراجعة فقط: {stats['edited']} صف عُدل بعد نشر صورته، {stats['cleared']} صف مُسح رابطه، "
                  f"{stats['missing']} صف مكتمل بلا رابط.")
        if stats["index_changed"]:
            print(f"[Enqueue] {stats['index_changed']} منتج «لا نتيجة» ظهرت لبراندها صفحات جديدة؛ يُعاد بحثه الآن.")
        local_cache_db.get_queue_statistics()
    except Exception as e:
        _enqueue_failed(f"تعذر إضافة الصفوف إلى الطابور: {e}. أُضيف {sum(done.values())} صف قبل الخطأ ولم يُحذف أي صف.",
                        reason="enqueue_failed" if local_cache_db.db_available() else "db_unavailable")

    run_id = local_cache_db.new_run_id()
    run_rows = local_cache_db.begin_run(run_id)
    if run_rows is None:
        print("[Enqueue] تنبيه: تعذر تسجيل التشغيل الجديد؛ ستعرض اللوحة تقدم الطابور كله.")
    else:
        print(f"[Enqueue] التشغيل {run_id} سيعالج {run_rows} صف (الصفوف في الانتظار بما فيها ما بقي من تشغيل سابق).")
        _write_run_handoff(run_id)


# ---------------------------------------------------------------------------
# عامل الخلفية
# ---------------------------------------------------------------------------

def check_verifier():
    """
    فحص قارئ الملصق الأساسي (VERIFIER_PRIMARY) عند بدء العامل؛ يعيد نص تنبيه للوحة التحكم أو '' إذا كان متاحاً.
    قارئ Claude يحتاج مفتاح Anthropic؛ قارئ Gemini يحتاج مفتاح Gemini ونموذجاً متاحاً.
    """
    try:
        from catalog_match import settings as cm_settings
        from catalog_match import verify as cm_verify
        try:
            from catalog_match.verifiers import cascade as cm_cascade
            ref = cm_cascade.primary_ref()
        except Exception:
            ref = None
        if ref is not None and ref.provider == "claude":
            if not cm_settings.anthropic_api_key():
                return "VERIFIER_NOT_CONFIGURED_CLAUDE: no Anthropic key, every result goes to human review"
            return ""
        if not cm_settings.gemini_api_key():
            return "VERIFIER_NOT_CONFIGURED: no Gemini key, every result goes to human review"
        check = cm_verify.check_model_available(model=ref.model if ref is not None else None)
        if not check:
            return f"VERIFIER_UNAVAILABLE: {check.status} ({check.model}); every result goes to human review"
        return ""
    except Exception as e:
        return f"VERIFIER_CHECK_FAILED: {type(e).__name__}"


def _run_health(worker_id, since_seconds):
    """
    ملخص ops_health لعمليات بحث هذا العامل (قراءة واحدة عند الإنهاء): تنبيهات الانقطاع للوحة، والتكلفة التقديرية
    لتقرير التشغيل قبل أن يكتب تشغيل لاحق فوق trace الصفوف. None بلا عامل أو عند فشل القراءة (لا يوقف الإنهاء).
    """
    if not worker_id:
        return None
    try:
        import ops_health
        return ops_health.summarize(ops_health.load_rows(since_seconds=max(1, int(since_seconds)), worker_id=worker_id))
    except Exception as e:
        print(f"تنبيه: تعذر فحص انقطاع المزودين: {e}")
        return None


def _outage_notice(worker_id, since_seconds, base=None, health=None):
    """
    تنبيه اللوحة عند انتهاء العامل: base + سبب انقطاع ظهر في عمليات بحث هذا العامل (رصيد Serper انتهى /
    Gemini لا يستجيب، من ops_health). None عندما لا يوجد أيهما فيبقى التنبيه الحالي. فشل الفحص لا يوقف الإنهاء.
    health: ملخص _run_health المقروء مسبقاً (لا قراءة ثانية).
    """
    outage = ""
    if worker_id:
        try:
            import ops_health
            if isinstance(health, dict):
                outage = ops_health.notice_from_report(health)
            else:
                outage = ops_health.outage_notice(since_seconds, worker_id=worker_id)
        except Exception as e:
            print(f"تنبيه: تعذر فحص انقطاع المزودين: {e}")
    return _merge_notices(base, outage)


_NOTICE_CODE_RE = re.compile(r"^([A-Z][A-Z0-9_]+):")


def _merge_notices(*notices):
    """التنبيهات بفاصل ' | '، وكل رمز (CODE: ...) مرة واحدة: الأول يبقى (سبب التوقف قبل تنبيه ops_health نفسه)."""
    parts, codes = [], set()
    for notice in notices:
        for part in str(notice or "").split(" | "):
            part = part.strip()
            m = _NOTICE_CODE_RE.match(part)
            if not part or (m and m.group(1) in codes):
                continue
            if m:
                codes.add(m.group(1))
            parts.append(part)
    return " | ".join(parts) or None


def _stop_notice(stop_reason):
    """نص التنبيه لسبب توقف قبل نهاية الطابور (رصيد Serper بنص ops_health نفسه، أيا كان عدد عمليات البحث)."""
    code = str(stop_reason).upper()
    if stop_reason == "serper_credit":
        try:
            import ops_health
            text = ops_health.ALERTS["SERPER_CREDIT"]
        except Exception:
            text = "رصيد Serper انتهى أو المفتاح مرفوض"
        return f"{code}: {text}؛ توقف العامل وبقيت الصفوف المتبقية في الانتظار"
    return f"{code}: توقف العامل قبل نهاية الطابور ({stop_reason})؛ بقيت الصفوف المتبقية في الانتظار"


def _refresh_state(status, run_id=None, **extra):
    """العدادات من صفوف التشغيل الحالي (run_id) فقط؛ بدون run_id (عامل شُغل يدوياً) من الطابور كله."""
    try:
        stats = local_cache_db.get_run_statistics(run_id) if run_id else local_cache_db.get_queue_statistics()
    except Exception as e:
        print(f"تنبيه: تعذر قراءة إحصائيات الطابور: {e}")
        local_cache_db.update_automation_state(status=status, **extra)
        return
    local_cache_db.update_automation_state(
        status=status,
        total=stats["total"],
        processed=stats["completed"] + stats["failed"] + stats["ready_for_review"],
        success=stats["completed"] + stats["ready_for_review"],
        failed=stats["failed"],
        **extra,
    )


# ---------------------------------------------------------------------------
# قفل العامل (temp/pipeline.lock)
# القفل JSON: {pid, host, started_at, started_ts, heartbeat_ts, proc_created, role, trigger, cmd} ويضيف العامل
# run_id و worker_id مع النبض؛ الصيغة القديمة (رقم العملية فقط) ما زالت تُقرأ، و'STARTING' تكتبه لوحة التحكم أثناء
# الإدراج. الكتابة ذرية (ملف مؤقت ثم os.replace)، وقفل جديد يُنشأ حصرياً (لا يأخذه عاملان معاً).
# العامل يجدد heartbeat_ts من حلقته كل LOCK_HEARTBEAT_SECONDS (حتى أثناء الإيقاف المؤقت وانتظار قاعدة البيانات).
# القفل متروك فقط بحكم إيجابي: من جهاز آخر، أو عمليته انتهت، أو ليست بايثون تشغّل main.py / run_nightly.py، أو بدأت في
# وقت غير وقت صاحب القفل (رقم عملية أُعيد استخدامه). قفل تتأكد هوية عمليته (سطر الأوامر ووقت البدء) لا يتقادم أبداً
# مهما طال التشغيل. فحص تعذر (PowerShell أو tasklist لا يرد) يُعاد مرة ولا يحذف القفل: يبقى حياً ما دام نبضه أحدث من
# LOCK_STALE_HEARTBEAT_SECONDS، وكذلك قفل لا نعرف من عمليته إلا اسمها. لوحة التحكم تسأل بايثون (cli_bridge
# lock_state) فلا توجد قاعدة ثانية.
# ---------------------------------------------------------------------------

# العامل يجدد نبض القفل بهذا الفاصل
LOCK_HEARTBEAT_SECONDS = 30
# قفل لا تتأكد هوية عمليته (فحص تعذر، أو الاسم فقط) يُعد متروكاً عندما يصبح نبضه أقدم من هذا
LOCK_STALE_HEARTBEAT_SECONDS = 30 * 60
# فرق مسموح بين وقتي بدء العملية (القراءة والكتابة) ووقت كتابة القفل (دقة ساعة النظام)
LOCK_CLOCK_SLACK_SECONDS = 5
LOCK_SCRIPTS = ("main.py", "run_nightly.py")
_LOCK_SCRIPT_RE = re.compile(r"(?:^|[\\/\s\"'])(?:main|run_nightly)\.py(?=$|[\s\"'])", re.IGNORECASE)
_OWN_PROCESS_CREATED = []


def _own_process_created():
    """
    وقت بدء هذه العملية (ثوانٍ UTC) بالفحص نفسه الذي يقرأ به الآخرون القفل (_process_info)، فتكون المقارنة بين
    قيمتين من المصدر نفسه (لا وقت النظام مقابل تحويل WMI، ولا أثر لتغيير التوقيت الصيفي). None إن تعذر الفحص.
    """
    if not _OWN_PROCESS_CREATED:
        try:
            created = _process_info(os.getpid()).get("created")
        except Exception:
            created = None
        _OWN_PROCESS_CREATED.append(round(float(created), 3) if isinstance(created, (int, float)) else None)
    return _OWN_PROCESS_CREATED[0]


def _lock_data(role, trigger=None, now=None):
    now = time.time() if now is None else now
    return {
        "pid": os.getpid(),
        "host": socket.gethostname(),
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now)),
        "started_ts": round(now, 3),
        "heartbeat_ts": round(now, 3),
        "proc_created": _own_process_created(),
        "role": role,
        "trigger": trigger if trigger in WORKER_TRIGGERS else ("nightly" if role == "nightly" else None),
        "cmd": " ".join(sys.argv)[:300],
    }


def _lock_tmp(lock_file):
    return f"{lock_file}.{os.getpid()}.tmp"


def _write_lock_file(lock_file, data, attempts=5):
    """
    كتابة ذرية: ملف مؤقت ثم os.replace، فلا يقرأ أحد قفلاً نصف مكتوب. على ويندوز يفشل الاستبدال لحظة يكون القفل
    مفتوحاً للقراءة (لوحة التحكم تقرؤه كل بضع ثوانٍ): يُعاد بعد لحظة، ثم يُرفع الخطأ.
    """
    os.makedirs(os.path.dirname(lock_file) or ".", exist_ok=True)
    tmp = _lock_tmp(lock_file)
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    for attempt in range(attempts):
        try:
            os.replace(tmp, lock_file)
            return
        except PermissionError:
            if attempt == attempts - 1:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
                raise
            time.sleep(0.05)


def _create_lock_file(lock_file, data):
    """ينشئ القفل فقط إن لم يوجد، ذرياً وحصرياً (رابط صلب للملف المؤقت). False إذا سبقنا إليه أحد."""
    os.makedirs(os.path.dirname(lock_file) or ".", exist_ok=True)
    tmp = _lock_tmp(lock_file)
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    try:
        try:
            os.link(tmp, lock_file)
            return True
        except FileExistsError:
            return False
        except (OSError, AttributeError, NotImplementedError):
            # نظام ملفات بلا روابط صلبة: إنشاء حصري (O_EXCL) ثم الكتابة
            try:
                fd = os.open(lock_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
            except FileExistsError:
                return False
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            return True
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def write_lock(role, lock_file=LOCK_FILE, trigger=None):
    """يكتب قفل هذه العملية (role: worker | nightly) ذرياً فوق أي قفل موجود. أخطاء الكتابة تُرفع."""
    _write_lock_file(lock_file, _lock_data(role, trigger))


def acquire_lock(role, lock_file=LOCK_FILE, trigger=None, take_starting=True):
    """
    يأخذ القفل: ينشئه حصرياً إن لم يوجد، أو يحل محل قفل هذه العملية (التشغيل الليلي أثناء الإدراج) أو 'STARTING'
    (إدراج لوحة التحكم يسلّم قفله لعامله؛ take_starting=False للتشغيل الليلي: STARTING تشغيلٌ آخر بدأ للتو).
    True إذا صار القفل لهذه العملية، False إذا كان لعملية أخرى. أخطاء الكتابة تُرفع.
    """
    data = _lock_data(role, trigger)
    lock = read_lock(lock_file)
    if lock is None:
        return _create_lock_file(lock_file, data)
    if (take_starting and lock["kind"] == "starting") or lock.get("pid") == os.getpid():
        _write_lock_file(lock_file, data)
        return True
    return False


def refresh_lock(lock_file=LOCK_FILE, now=None, **fields):
    """
    نبض العامل: يجدد heartbeat_ts (ويضيف fields مثل run_id و worker_id) في قفل هذه العملية فقط، ذرياً. لا يكتب
    فوق قفل عملية أخرى ولا يعيد قفلاً حُذف. True عند الكتابة؛ لا يرفع أبداً.
    """
    try:
        lock = read_lock(lock_file)
        if lock is None or lock["kind"] != "json" or lock.get("pid") != os.getpid():
            return False
        data = json.loads(lock["raw"])
        data.update(fields)
        data["heartbeat_ts"] = round(time.time() if now is None else now, 3)
        _write_lock_file(lock_file, data)
        return True
    except Exception as e:
        print(f"[Lock] تعذر تجديد نبض القفل: {e}")
        return False


def _lock_number(value):
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def read_lock(lock_file=LOCK_FILE):
    """
    محتوى القفل: None إن لم يوجد، وإلا {raw, kind: json | pid | starting | invalid, pid, host, started_ts,
    heartbeat_ts, proc_created, role, trigger, run_id, worker_id, mtime}. started_ts و heartbeat_ts للصيغة القديمة
    (أو JSON بلا وقت) هما وقت تعديل الملف.
    """
    try:
        with open(lock_file, "r", encoding="utf-8", errors="replace") as f:
            raw = f.read().strip()
        mtime = os.path.getmtime(lock_file)
    except OSError:
        return None
    lock = {"raw": raw, "kind": "invalid", "pid": None, "host": None, "started_ts": mtime, "heartbeat_ts": mtime,
            "proc_created": None, "role": None, "trigger": None, "run_id": None, "worker_id": None, "mtime": mtime}
    if raw == "STARTING":
        lock["kind"] = "starting"
    elif raw.isdigit():
        lock.update(kind="pid", pid=int(raw))
    elif raw.startswith("{"):
        try:
            data = json.loads(raw)
            pid = int(data.get("pid"))
        except (ValueError, TypeError, AttributeError):
            return lock
        started = _lock_number(data.get("started_ts"))
        beat = _lock_number(data.get("heartbeat_ts"))
        text = lambda key: str(data.get(key) or "") or None  # noqa: E731
        lock.update(kind="json", pid=pid, host=text("host"), role=data.get("role"), trigger=text("trigger"),
                    run_id=text("run_id"), worker_id=text("worker_id"), proc_created=_lock_number(data.get("proc_created")),
                    started_ts=started if started is not None else mtime, heartbeat_ts=beat if beat is not None else mtime)
    return lock


_DEAD = {"alive": False, "name": None, "cmdline": None, "created": None}


def _parse_windows_probe(raw):
    """
    مخرجات فحص PowerShell (PROBE=1 ثم NAME= / CREATED= / CMD= إن وُجدت العملية): BOM في أول سطر (UTF-8 مع BOM)
    ونهايات CRLF مقبولة. None إذا لم يظهر PROBE (مخرجات غير مفهومة: لا نعرف، ولا نقول «العملية انتهت»).
    """
    text = raw.decode("utf-8-sig", errors="replace") if isinstance(raw, (bytes, bytearray)) else str(raw or "")
    info, seen = dict(_DEAD), False
    for line in text.splitlines():
        key, _, value = line.strip().lstrip("﻿").partition("=")
        key, value = key.strip().upper(), value.strip()
        if key == "PROBE":
            seen = True
        elif key == "NAME" and value:
            seen = True
            info.update(alive=True, name=value)
        elif key == "CREATED" and value.lstrip("-").isdigit():
            info["created"] = float(value)
        elif key == "CMD":
            info["cmdline"] = value or None
    return info if seen else None


def _windows_process_info(pid):
    """
    عملية على ويندوز: الاسم وسطر الأوامر ووقت البدء (UTC) من Win32_Process عبر PowerShell، أو الاسم فقط من tasklist.
    فحص لم يكتمل (مهلة، خطأ، مخرجات غير مفهومة) يرفع RuntimeError: «لا نعرف» ليس «العملية انتهت».
    """
    import subprocess
    run = subprocess.run
    no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    script = ("[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false; "
              f"$p = Get-CimInstance Win32_Process -Filter 'ProcessId = {int(pid)}' -ErrorAction Stop; "
              "'PROBE=1'; "
              "if ($p) { 'NAME=' + $p.Name; "
              "'CREATED=' + ([DateTimeOffset]($p.CreationDate.ToUniversalTime())).ToUnixTimeSeconds(); "
              "'CMD=' + $p.CommandLine }")
    problems = []
    try:
        out = run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                  capture_output=True, timeout=30, creationflags=no_window)
        info = _parse_windows_probe(out.stdout) if out.returncode == 0 else None
        if info is not None:
            return info
        problems.append(f"PowerShell exit {out.returncode}")
    except Exception as e:
        problems.append(f"PowerShell {type(e).__name__}")
    try:
        out = run(["tasklist", "/FI", f"PID eq {int(pid)}", "/FO", "CSV", "/NH"], capture_output=True,
                  timeout=30, creationflags=no_window)
    except Exception as e:
        raise RuntimeError("; ".join(problems + [f"tasklist {type(e).__name__}"])) from e
    if out.returncode != 0:
        raise RuntimeError("; ".join(problems + [f"tasklist exit {out.returncode}"]))
    raw = out.stdout
    text = raw.decode("utf-8", errors="replace") if isinstance(raw, (bytes, bytearray)) else str(raw or "")
    for line in text.splitlines():
        cells = [c.strip().strip('"') for c in line.strip().lstrip("﻿").split('","')]
        if len(cells) > 1 and cells[1].strip() == str(int(pid)):
            return {"alive": True, "name": cells[0] or None, "cmdline": None, "created": None}
    # tasklist أجاب بلا سطر لهذه العملية (رسالة «لا توجد مهام» بلغة النظام): العملية انتهت
    return dict(_DEAD)


def _proc_process_info(pid):
    """عملية على لينكس من /proc: الاسم وسطر الأوامر ووقت البدء؛ عملية زومبي ليست حية."""
    base = f"/proc/{int(pid)}"
    try:
        with open(f"{base}/stat", "r") as f:
            stat = f.read()
    except OSError:
        return dict(_DEAD)
    fields = stat.rsplit(")", 1)[-1].split()
    if fields and fields[0] == "Z":
        return dict(_DEAD)
    info = {"alive": True, "name": None, "cmdline": None, "created": None}
    try:
        with open(f"{base}/cmdline", "rb") as f:
            argv = [a.decode("utf-8", errors="replace") for a in f.read().split(b"\0") if a]
        if argv:
            info["cmdline"] = " ".join(argv)
            info["name"] = re.split(r"[\\/]", argv[0])[-1]
    except OSError:
        pass
    try:
        with open("/proc/stat", "r") as f:
            boot = next(float(line.split()[1]) for line in f if line.startswith("btime "))
        info["created"] = boot + int(fields[19]) / os.sysconf("SC_CLK_TCK")
    except (OSError, ValueError, IndexError, StopIteration):
        pass
    return info


def _process_info(pid):
    """
    {alive, name, cmdline, created} لعملية؛ None للحقل الذي لا يمكن معرفته. psutil إن كان مثبتاً، وإلا /proc
    (لينكس)، وإلا Win32_Process / tasklist (ويندوز)، وإلا os.kill و ps. فحص لم يكتمل يرفع (لا يعني «انتهت»).
    """
    try:
        import psutil
    except ImportError:
        psutil = None
    if psutil is not None:
        try:
            p = psutil.Process(pid)
            if p.status() == psutil.STATUS_ZOMBIE:
                return dict(_DEAD)
            info = {"alive": True, "name": None, "cmdline": None, "created": None}
            for key, read in (("name", p.name), ("cmdline", lambda: " ".join(p.cmdline())), ("created", p.create_time)):
                try:
                    info[key] = read()
                except psutil.Error:
                    pass
            return info
        except psutil.NoSuchProcess:
            return dict(_DEAD)
        except psutil.Error:
            pass
    if os.name == "nt":
        return _windows_process_info(pid)
    if os.path.isdir("/proc"):
        return _proc_process_info(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return dict(_DEAD)
    except OSError:
        pass
    info = {"alive": True, "name": None, "cmdline": None, "created": None}
    try:
        import subprocess
        out = subprocess.run(["ps", "-p", str(int(pid)), "-o", "command="], capture_output=True, timeout=10)
        info["cmdline"] = out.stdout.decode(errors="replace").strip() or None
        if info["cmdline"]:
            info["name"] = re.split(r"[\\/]", info["cmdline"].split()[0])[-1]
    except Exception:
        pass
    return info


def lock_verdict(lock, now=None, process_info=None, host=None):
    """
    حكم القفل: {stale: سبب عربي (للسجل) أو None لعامل حي، verified: هوية العملية مؤكدة (سطر الأوامر ووقت البدء)،
    probe_error: فحص العملية تعذر مرتين (نص قصير) أو None، heartbeat_age: ثوانٍ منذ آخر نبض}.
    فقط حكم إيجابي يجعل القفل متروكاً؛ فحص تعذر أو هوية ناقصة (الاسم فقط) تعني قفلاً حياً حتى يتقادم نبضه
    (LOCK_STALE_HEARTBEAT_SECONDS). process_info(pid) -> {alive, name, cmdline, created} (للاختبارات).
    """
    now = time.time() if now is None else now
    beat = lock.get("heartbeat_ts")
    age = None if beat is None else max(0.0, now - beat)
    verdict = {"stale": None, "verified": False, "probe_error": None, "heartbeat_age": age}
    pid = lock.get("pid")
    if lock.get("kind") not in ("json", "pid") or not isinstance(pid, int):
        if lock.get("kind") == "invalid" and not lock.get("raw") and age is not None and age <= LOCK_CLOCK_SLACK_SECONDS:
            return verdict          # قفل فارغ كُتب للتو: إنشاء حصري لم يكتمل بعد
        verdict["stale"] = f"محتوى غير مفهوم: «{str(lock.get('raw') or '')[:40]}»"
        return verdict
    if pid <= 1:
        verdict["stale"] = f"رقم عملية غير صالح ({pid})"
        return verdict
    here = host or socket.gethostname()
    if lock.get("host") and lock["host"].lower() != here.lower():
        verdict["stale"] = f"القفل من جهاز آخر ({lock['host']})"
        return verdict
    info, problems = None, []
    for _ in range(2):              # فحص تعذر يُعاد مرة واحدة
        try:
            info = (process_info or _process_info)(pid)
            break
        except Exception as e:
            problems.append(f"{type(e).__name__}: {e}"[:200])
    old_beat = age is not None and age > LOCK_STALE_HEARTBEAT_SECONDS
    stale_minutes = LOCK_STALE_HEARTBEAT_SECONDS // 60
    if info is None:
        verdict["probe_error"] = problems[-1]
        if old_beat:
            verdict["stale"] = f"تعذر فحص العملية {pid} ونبض القفل أقدم من {stale_minutes} دقيقة"
        return verdict
    if not info.get("alive"):
        verdict["stale"] = f"العملية {pid} لم تعد تعمل"
        return verdict
    name = info.get("name")
    if name and "python" not in name.lower():
        verdict["stale"] = f"العملية {pid} ليست بايثون ({name})"
        return verdict
    cmdline = info.get("cmdline")
    if cmdline and not _LOCK_SCRIPT_RE.search(cmdline):
        verdict["stale"] = f"العملية {pid} ليست عامل الأتمتة (main.py / run_nightly.py)"
        return verdict
    created, own, started = info.get("created"), lock.get("proc_created"), lock.get("started_ts")
    if created is not None and own is not None:
        # وقتا بدء العملية من الفحص نفسه (عند الكتابة والآن): أي فرق يعني عملية أخرى بالرقم نفسه
        if abs(created - own) > LOCK_CLOCK_SLACK_SECONDS:
            verdict["stale"] = f"العملية {pid} ليست العملية التي كتبت القفل (رقم عملية أُعيد استخدامه)"
            return verdict
    elif created is not None and started is not None and created > started + LOCK_CLOCK_SLACK_SECONDS:
        verdict["stale"] = f"العملية {pid} بدأت بعد كتابة القفل (رقم عملية أُعيد استخدامه)"
        return verdict
    verdict["verified"] = bool(cmdline) and created is not None
    if not verdict["verified"] and old_beat:
        verdict["stale"] = (f"هوية العملية {pid} غير مؤكدة (الاسم فقط) ونبض القفل أقدم من {stale_minutes} دقيقة")
    return verdict


def stale_lock_reason(lock, now=None, process_info=None, host=None):
    """None إذا كان القفل لعامل أتمتة حي على هذا الجهاز، وإلا سبب اعتباره متروكاً (نص عربي للسجل)."""
    return lock_verdict(lock, now=now, process_info=process_info, host=host)["stale"]


def _remove_lock_if_unchanged(lock_file, raw):
    """يحذف القفل فقط إذا لم يكتبه أحد بعد قراءته (عامل بدأ للتو)."""
    try:
        with open(lock_file, "r", encoding="utf-8", errors="replace") as f:
            if f.read().strip() != raw:
                return False
        os.remove(lock_file)
        return True
    except OSError:
        return False


def _another_worker_running(lock_file, now=None, process_info=None):
    """
    هل يحمل القفل عامل أتمتة حي آخر؟ لا قفل، أو 'STARTING' (العامل يأخذ القفل من الإدراج)، أو قفل هذه العملية
    (التشغيل الليلي يحمله أثناء الإدراج): False. قفل متروك يُحذف ويُسجل السبب ثم False. فحص تعذر يُسجل ولا يحذف.
    """
    lock = read_lock(lock_file)
    if lock is None or lock["kind"] == "starting" or lock.get("pid") == os.getpid():
        return False
    verdict = lock_verdict(lock, now=now, process_info=process_info)
    if verdict["probe_error"]:
        print(f"[Lock] تعذر فحص العملية {lock.get('pid')} مرتين ({verdict['probe_error']})"
              + ("." if verdict["stale"] else "؛ يُعد القفل حياً (لا يُحذف قفل لم يثبت أنه متروك)."))
    if verdict["stale"] is None:
        return True
    removed = _remove_lock_if_unchanged(lock_file, lock["raw"])
    print(f"[Lock] قفل متروك في {lock_file}: {verdict['stale']}؛ "
          + ("حُذف ويستمر التشغيل." if removed else "تغير أثناء الفحص فلم يُحذف."))
    return False


def release_own_lock(lock_file=LOCK_FILE):
    """يحذف القفل فقط إذا كان لهذه العملية (لا يحذف قفل عامل آخر بدأ بعد أن عُدّ قفلنا متروكاً)."""
    lock = read_lock(lock_file)
    if lock is not None and lock.get("pid") == os.getpid():
        _remove_lock_if_unchanged(lock_file, lock["raw"])


WORKER_TRIGGERS = ("dashboard", "nightly", "manual")
DB_UNAVAILABLE_NOTICE = "DB_UNAVAILABLE: تعذر الوصول إلى قاعدة البيانات فتوقف العامل؛ بقيت الصفوف المتبقية في الانتظار"


def _sheet_failure_reason(error):
    """
    سبب فشل قراءة الشيت: sheet_config لإعداد خاطئ (تبويب أو عمود غير موجود)؛ sheets_unavailable فقط لانقطاع قد
    يزول وحده فيعيده التشغيل الليلي (SheetTransientError، انقطاع الاتصال أو مهلته، 429 / 5xx)؛ و enqueue_failed لأي
    خطأ آخر (TypeError، KeyError، PermissionError...): إعادة المحاولة بعد ساعة لا تصلحه.
    """
    config_errors = tuple(c for c in (getattr(google_sheets, "SheetConfigError", None),
                                      getattr(google_sheets, "SheetSchemaError", None)) if isinstance(c, type))
    if config_errors and isinstance(error, config_errors):
        return "sheet_config"
    transient = getattr(google_sheets, "SheetTransientError", None)
    if isinstance(transient, type) and isinstance(error, transient):
        return "sheets_unavailable"
    if isinstance(error, (ConnectionError, TimeoutError)):
        return "sheets_unavailable"
    try:
        # requests / google-auth: انقطاع الاتصال ومهلته، و APIError برمز 429 / 5xx
        if google_sheets._is_transient(error):
            return "sheets_unavailable"
    except Exception:
        pass
    return "enqueue_failed"


def _redacted(text):
    """نص خطأ بلا أسرار (run_report.redact) قبل أن يصل إلى تنبيه اللوحة والتقرير."""
    try:
        import run_report
        return run_report.redact(text)
    except Exception:
        return str(text)


def _cli_trigger(argv):
    """--trigger=dashboard|nightly|manual من سطر الأوامر؛ افتراضياً manual (تشغيل يدوي)."""
    for i, arg in enumerate(argv):
        value = arg.split("=", 1)[1] if arg.startswith("--trigger=") else (
            argv[i + 1] if arg == "--trigger" and i + 1 < len(argv) else None)
        if value and value.strip().lower() in WORKER_TRIGGERS:
            return value.strip().lower()
    return "manual"


BUDGET_WARN_RATIO = 0.8
# العامل بلا مهمة جاهزة (ينتظر موعد إعادة محاولة) يحدّث automation_state بهذا الفاصل؛ اللوحة تعد التشغيل عالقاً
# بعد 600 ثانية بلا تحديث (QueueStats::stuckReason)
WAIT_HEARTBEAT_SECONDS = 60


def _daily_budget():
    """DAILY_BUDGET_USD (0 أو قيمة غير صالحة = بلا حد)."""
    try:
        return max(0.0, float(getattr(config, "DAILY_BUDGET_USD", 0) or 0))
    except (TypeError, ValueError):
        return 0.0


def _credit_stop_searches():
    """SERPER_CREDIT_STOP_SEARCHES (0 = لا إيقاف بسبب رصيد Serper)."""
    try:
        return max(0, int(getattr(config, "SERPER_CREDIT_STOP_SEARCHES", 3) or 0))
    except (TypeError, ValueError):
        return 3


def _next_credit_streak(streak, report):
    """
    عدد عمليات البحث المتتالية التي رفض فيها Serper كل استعلاماته (رصيد / مفتاح). بحث أجاب فيه Serper يصفّره؛
    مهمة بلا بحث (كتابة رابط) أو بحث لم يستدعِ Serper لا يغيّره.
    """
    credit = (report or {}).get("serper_credit")
    if credit is True:
        return streak + 1
    if credit is False:
        return 0
    return streak


def _prepare_verifier_rechecks(notice, run_id):
    """
    صفوف جاهزة للمراجعة بسبب تعطل قارئ الملصق (VERIFIER_DOWN): إن كان القارئ متاحاً في هذا التشغيل (لا تنبيه
    VERIFIER_*) تعود للبحث بأولوية إعادة التحقق؛ وإلا تعود صفوف إعادة تحقق سابقة لم تُسحب إلى المراجعة.
    يستدعيها العامل عند أول سحب فقط (بعد فحص الميزانية والرصيد وطلب الإيقاف): تشغيل يتوقف قبل أي سحب لا يُخرج
    صفوفاً من المراجعة. تعيد عدد الصفوف التي عادت للبحث.
    """
    if notice:
        parked = local_cache_db.park_verifier_rechecks()
        if parked:
            print(f"[Worker] قارئ الملصق غير متاح؛ {parked} صف إعادة تحقق عاد للمراجعة.")
        return 0
    requeued = local_cache_db.requeue_verifier_down(run_id)
    if requeued:
        print(f"[Worker] {requeued} صف انتظر المراجعة لأن قارئ الملصق تعطل؛ يُعاد بحثه الآن بقارئ يعمل.")
    return requeued or 0


def _forget_brand_spellings():
    """
    كل تشغيل يبدأ بلا كتابات ماركات أثبتتها صفوف تشغيل سابق أو ورقة Brands Mapping سابقة (catalog_match.brand_discovery):
    العامل يعيش طويلاً، والذاكرة لا تُفرغ وحدها.
    """
    try:
        from catalog_match import brand_discovery
        brand_discovery.forget_all()
    except Exception as e:
        print(f"تنبيه: تعذر تفريغ ذاكرة كتابات الماركات: {e}")


def run_worker_mode(trigger="manual", report=True, deadline_ts=None):
    """
    عامل الخلفية: يسحب المهام ذرياً ويعالجها بالتوازي (3 خيوط).
    يخرج فقط عندما ينجح COUNT(*) للمهام المفتوحة ويعيد 0، أو عند توقف المزودين (5 مهام متتالية PROVIDER_DOWN)،
    أو عند طلب إيقاف من لوحة التحكم (stop_requested): لا يسحب مهمة جديدة، ينهي المنتجات الجارية، ثم يعيد
    local_cache_db.stop_run الصفوف العالقة للانتظار. طلب إيقاف سُجل أثناء الإدراج يُنفذ قبل معالجة أي منتج.
    قاعدة بيانات لا ترد عند البدء: يتوقف فوراً (db_unavailable) بدل اعتبار التشغيل منتهياً.
    النتيجة في LAST_WORKER؛ report=True يكتب تقرير التشغيل (run_report: سجل التشغيلات، last_report.json، Telegram).
    التشغيل الليلي يمرر report=False ويكتب تقريراً واحداً لليلة بعد إعادة المحاولات، و deadline_ts (حد جدولة المهام
    ناقص هامش): بعده لا يسحب العامل مهمة جديدة وينتهي بعد المنتجات الجارية (time_limit) قبل أن تُنهي جدولة المهام العملية.
    """
    from concurrent.futures import ThreadPoolExecutor
    import threading

    LAST_WORKER.clear()
    bg_skipped_count(reset=True)
    lock_file = LOCK_FILE
    os.makedirs("temp", exist_ok=True)
    if _another_worker_running(lock_file):
        LAST_WORKER.update(stop_reason="another_worker")
        print("[Worker] معالج الخلفية يعمل بالفعل. خروج.")
        sys.exit(0)
    try:
        acquired = acquire_lock("nightly" if trigger == "nightly" else "worker", lock_file, trigger=trigger)
    except Exception as e:
        acquired = True          # تعذر كتابة القفل لا يمنع التشغيل (كما كان)
        print(f"[Worker] تعذر كتابة القفل {lock_file}: {e}")
    if not acquired:
        LAST_WORKER.update(stop_reason="another_worker")
        print("[Worker] عامل آخر أخذ القفل للتو. خروج.")
        sys.exit(0)
    lock_beat = [time.monotonic()]

    def lock_heartbeat(**fields):
        # نبض القفل: عامل حي لا يُعد قفله متروكاً مهما طال التشغيل (موقوف مؤقتاً، أو ينتظر قاعدة البيانات)
        if fields or time.monotonic() - lock_beat[0] >= LOCK_HEARTBEAT_SECONDS:
            lock_beat[0] = time.monotonic()
            refresh_lock(lock_file, **fields)

    print("=" * 60)
    print("عامل البحث المسبق (Worker) قيد العمل...")
    print("=" * 60)

    load_run_config()
    local_cache_db.resume_automation()   # علم الإيقاف المؤقت القديم لا يمنع تشغيلاً جديداً
    started = time.monotonic()
    started_ts = time.time()
    state = local_cache_db.get_automation_state()
    run_id = state.get("run_id") or None
    # التقرير يعد صفوف run_id فقط إن أنشأه إدراج هذا التشغيل؛ عامل يدوي بلا إدراج يُعد بصفوف worker_id (لا أرقام
    # التشغيل السابق الذي بقي run_id في automation_state)
    report_run_id = run_id if _claim_run_handoff(run_id) else None
    # طلب إيقاف وصل أثناء الإدراج (قبل وجود العامل): لا يُعالج أي منتج
    stop_reason = "stopped" if state.get("stop_requested") == 1 else None
    if state.get("status") == "db_unavailable":
        stop_reason = "db_unavailable"
    notice = "" if stop_reason else check_verifier()
    if notice:
        print(f"[Worker] {notice}")

    queue_started = False
    worker_id = None
    start_notice = None          # سبب التوقف قبل أي منتج (للتقرير)
    rechecks_prepared = False    # إعادة التحقق تُجهز عند أول سحب (_prepare_verifier_rechecks)
    rechecks_requeued = 0
    crash = None                 # خطأ أنهى العامل (لتنبيه WORKER_ERROR)
    try:
        if stop_reason == "db_unavailable":
            print(f"[Worker] قاعدة البيانات لا ترد ({state.get('db_error') or '-'})؛ لن يُعالج أي منتج.")
            return
        if stop_reason:
            print("[Worker] طلب إيقاف من لوحة التحكم سُجل قبل بدء العامل؛ لن يُعالج أي منتج.")
            return
        sheets_client = google_sheets.get_sheets_client()
        if not sheets_client:
            # ملف اعتماد مفقود أو تالف (gspread.service_account لا يتصل بالشبكة): إعداد، لا انقطاع يعيده الليلي
            stop_reason = "sheet_config"
            start_notice = (f"SHEET_CONFIG: تعذر تحميل بيانات اعتماد Google من الملف «{config.CREDENTIALS_FILE}» "
                            "(مفقود أو تالف)")
            local_cache_db.update_automation_state(status="error", notice=start_notice)
            return
        try:
            worksheet = google_sheets.open_worksheet(sheets_client, config.SPREADSHEET_NAME_OR_URL)
            if not worksheet:
                # الملف غير موجود أو غير مشارك مع حساب الخدمة؛ الانقطاع المؤقت يرفع SheetTransientError ولا يصل هنا
                stop_reason = "sheet_not_found"
                raise google_sheets.SheetConfigError(f"sheet not found: {config.SPREADSHEET_NAME_OR_URL}")
            link_column_index = google_sheets.find_link_column(worksheet)
        except Exception as e:
            reason = _sheet_failure_reason(e)
            stop_reason = stop_reason or ("worker_error" if reason == "enqueue_failed" else reason)
            print(f"[Worker] {e}")
            if stop_reason == "worker_error":
                crash = _redacted(f"{type(e).__name__}: {e}")[:200]   # التنبيه WORKER_ERROR يُكتب في finally
                return
            code = "SHEETS_UNAVAILABLE" if stop_reason == "sheets_unavailable" else "SHEET_CONFIG"
            start_notice = f"{code}: {e}"
            local_cache_db.update_automation_state(status="error", notice=start_notice)
            return
        brand_mappings = google_sheets.get_brand_mappings(sheets_client, config.SPREADSHEET_NAME_OR_URL)
        _forget_brand_spellings()
        google_sheets.init_async_queue(config.CREDENTIALS_FILE, config.SPREADSHEET_NAME_OR_URL)
        queue_started = True
        _refresh_state("pre_caching", run_id=run_id, notice=notice)

        worker_id = local_cache_db.new_claim_id().split("#")[0]
        lock_heartbeat(worker_id=worker_id, run_id=report_run_id)   # منه يُكتب تقرير عامل أنهته اللوحة
        lock = threading.Lock()
        counters = {"provider_down_streak": 0, "credit_streak": 0}
        budget = _daily_budget()
        credit_stop = _credit_stop_searches()
        budget_warned = False

        def runner(t):
            report = {}
            try:
                local_cache_db.update_automation_state(status="pre_caching", current_product=t["product_name"])
                result = pre_cache_product_candidates(t, worksheet, link_column_index, brand_mappings, report=report)
            except Exception as e:
                _finish_task(t, "failed", f"Unexpected worker error: {e}", failure_code="WORKER_ERROR")
                print(f"[Worker Thread Error] الصف {t['row_number']}: {e}")
                result = "failed"
            with lock:
                counters["provider_down_streak"] = counters["provider_down_streak"] + 1 if result == "provider_down" else 0
                counters["credit_streak"] = _next_credit_streak(counters["credit_streak"], report)
            _refresh_state("pre_caching", run_id=run_id)

        max_workers = 3
        active = []
        db_outage_since = None
        last_beat = time.monotonic()
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            while True:
                lock_heartbeat()
                active = [f for f in active if not f.done()]
                with lock:
                    streak = counters["provider_down_streak"]
                    credit_streak = counters["credit_streak"]
                if credit_stop and credit_streak >= credit_stop:
                    # لا نكمل الدفع لبحث بلا Serper: التنبيه SERPER_CREDIT يضيفه _outage_notice من صفوف هذا العامل
                    stop_reason = "serper_credit"
                    print(f"[Worker] رفض Serper كل الاستعلامات في آخر {credit_streak} عمليات بحث (رصيد أو مفتاح)؛ "
                          "إيقاف العامل وإبقاء الصفوف في الانتظار.")
                    break
                if streak >= MAX_PROVIDER_DOWN_STREAK:
                    stop_reason = "provider_down"
                    print("[Worker] محركات البحث غير متاحة لعدة منتجات متتالية؛ إيقاف العامل وإبقاء الصفوف في الانتظار.")
                    break
                try:
                    state = local_cache_db.get_automation_state()
                    if state.get("stop_requested") == 1:
                        # لا مهمة جديدة؛ الخروج من المنفذ ينتظر المنتجات الجارية، والباقي يبقى في الانتظار
                        stop_reason = "stopped"
                        print("[Worker] طلب إيقاف من لوحة التحكم؛ ينتهي العامل بعد المنتجات الجارية.")
                        break
                    if deadline_ts is not None and time.time() >= deadline_ts:
                        stop_reason = "time_limit"
                        print("[Worker] بلغ التشغيل حده الزمني؛ لا مهمة جديدة، وينتهي العامل بعد المنتجات الجارية.")
                        break
                    if state.get("pause_requested") == 1:
                        time.sleep(1)
                        continue
                    if len(active) < max_workers:
                        if budget > 0:
                            spent = local_cache_db.spend_today()
                            if spent >= budget:
                                stop_reason = "budget_reached"
                                # سبب التوقف أولاً، ويبقى تنبيه قارئ الملصق (VERIFIER_*) من بداية التشغيل بعده:
                                # كلاهما صحيح ولا يحل أحدهما محل الآخر
                                notice = " | ".join(n for n in (
                                    f"BUDGET_REACHED: daily search budget {budget:.2f} USD reached (spent {spent:.2f})",
                                    notice) if n)
                                print(f"[Worker] بلغ صرف اليوم {spent:.2f}$ الميزانية اليومية {budget:.2f}$؛ "
                                      "إيقاف العامل وإبقاء الصفوف في الانتظار.")
                                break
                            if not budget_warned and spent >= BUDGET_WARN_RATIO * budget:
                                budget_warned = True
                                print(f"[Worker] تنبيه: صرف اليوم {spent:.2f}$ بلغ {int(BUDGET_WARN_RATIO * 100)}% "
                                      f"من الميزانية اليومية {budget:.2f}$.")
                        if not rechecks_prepared:
                            # إعادة التحقق تُخرج صفوفاً من المراجعة: فقط عندما يوشك العامل أن يسحب فعلاً
                            rechecks_prepared = True
                            rechecks_requeued = _prepare_verifier_rechecks(notice, run_id)
                        task = local_cache_db.fetch_next_task(worker_id)
                        if task:
                            print(f"[Queue] سحب مهمة الصف {task['row_number']}.")
                            active.append(executor.submit(runner, task))
                            db_outage_since = None
                            last_beat = time.monotonic()
                            continue
                    if not active and local_cache_db.count_open_tasks() == 0:
                        print("[Worker] الطابور فارغ؛ خروج العامل.")
                        break
                    if not active and time.monotonic() - last_beat >= WAIT_HEARTBEAT_SECONDS:
                        # لا مهمة جاهزة الآن (صف ينتظر موعد إعادة المحاولة بعد انقطاع المزودين، 10-20 دقيقة): نبضة
                        # تُبقي updated_at حديثاً، فلا تعرض اللوحة «العامل شغّال بلا تقدم» وزر «إصلاح تشغيل عالق»
                        _refresh_state("pre_caching", run_id=run_id, current_product="")
                        last_beat = time.monotonic()
                    db_outage_since = None
                except Exception as e:
                    # خطأ قاعدة البيانات ليس "طابوراً فارغاً": ننتظر ونعيد المحاولة
                    db_outage_since = db_outage_since or time.time()
                    print(f"[Worker] خطأ في قاعدة البيانات: {e}")
                    if time.time() - db_outage_since > MAX_DB_OUTAGE_SECONDS and not active:
                        stop_reason = "db_unavailable"
                        break
                    time.sleep(5)
                    continue
                time.sleep(1)
    except BaseException as e:
        # عامل انهار ليس «اكتمل»: خطأ غير متوقع = worker_error (رمز 1)، و Ctrl+C = stopped (رمز 3)
        if isinstance(e, KeyboardInterrupt):
            stop_reason = stop_reason or "stopped"
        elif not (isinstance(e, SystemExit) and e.code in (0, None)):
            stop_reason = stop_reason or "worker_error"
            crash = _redacted(f"{type(e).__name__}: {e}")[:200]
            print(f"[Worker] خطأ غير متوقع أنهى العامل: {crash}")
        raise
    finally:
        # سبب الانقطاع (رصيد Serper / Gemini) من صفوف هذا العامل فقط (worker_id)؛ None يترك التنبيه كما هو.
        # نهاية التشغيل تلغي طلب إيقاف وصل مع نهايته (stop_requested=0) كي لا يوقف عاملاً لاحقاً قبل أي منتج.
        run_seconds = time.monotonic() - started + 60
        health = _run_health(worker_id, run_seconds)
        final_notice = None
        if rechecks_requeued:
            # صفوف إعادة تحقق لم يصل إليها هذا التشغيل (توقف مبكراً، أو أُجلت بعد انقطاع المزودين) تعود للمراجعة
            # بمرشحاتها قبل كتابة الحالة النهائية، فلا تختفي من المراجعة حتى التشغيل التالي
            parked = local_cache_db.park_verifier_rechecks()
            if parked:
                print(f"[Worker] {parked} صف إعادة تحقق لم يصل إليه التشغيل؛ عاد للمراجعة.")
        try:
            if stop_reason == "stopped":
                # يعيد أي صف بقي 'processing' إلى الانتظار، ويلغي طلب الإيقاف، ويضبط الحالة (مراجعة أو خامل)
                local_cache_db.stop_run(worker_active=False)
            elif stop_reason == "provider_down":
                final_notice = _outage_notice(worker_id, run_seconds,
                                              "PROVIDER_DOWN: search providers unavailable; remaining rows stay pending",
                                              health=health)
                local_cache_db.update_automation_state(status="provider_down", current_product="", stop_requested=0,
                                                       notice=final_notice)
            elif stop_reason == "db_unavailable":
                # غالباً يفشل هذا التحديث أيضاً؛ التقرير (run_report) وملف last_report.json يقولان ذلك صراحة
                final_notice = DB_UNAVAILABLE_NOTICE
                local_cache_db.update_automation_state(status="error", current_product="", stop_requested=0,
                                                       notice=final_notice)
            elif stop_reason in ("sheets_unavailable", "sheet_config", "sheet_not_found"):
                pass
            elif stop_reason == "worker_error":
                final_notice = _merge_notices(
                    f"WORKER_ERROR: خطأ غير متوقع أوقف العامل ({crash or '-'})؛ بقيت الصفوف المتبقية في الانتظار", notice)
                local_cache_db.update_automation_state(status="error", current_product="", stop_requested=0,
                                                       notice=final_notice)
            else:
                # الطابور انتهى، أو سبب توقف آخر (مثل حد الميزانية) يظهر نصه كما هو في التنبيه والتقرير
                base = notice
                if stop_reason and not str(notice or "").startswith(f"{str(stop_reason).upper()}:"):
                    # SERPER_CREDIT يُذكر سببه دائماً: تنبيه ops_health يحتاج عمليتي بحث على الأقل، والإيقاف قد يكون
                    # بعد واحدة (SERPER_CREDIT_STOP_SEARCHES=1)؛ تنبيه ops_health بالرمز نفسه لا يتكرر
                    base = _merge_notices(_stop_notice(stop_reason), notice)
                final_notice = _outage_notice(worker_id, run_seconds, base, health=health)
                ready = local_cache_db.get_ready_for_review_count()
                if ready is None:
                    # خطأ قاعدة البيانات ليس «لا شيء للمراجعة»: الحالة تبقى، ولوحة التحكم تضبطها عند عودة القاعدة
                    print("[Worker] تعذر قراءة عدد الصفوف الجاهزة للمراجعة؛ لم تُكتب الحالة النهائية.")
                else:
                    local_cache_db.update_automation_state(status="curation_pending" if ready > 0 else "idle",
                                                           current_product="", stop_requested=0, notice=final_notice)
        except Exception as e:
            print(f"[Worker] تعذر تحديث الحالة النهائية: {e}")
        if queue_started:
            google_sheets.stop_async_queue()
        release_own_lock(lock_file)
        try:
            if os.path.exists("temp/batch_progress.json"):
                os.remove("temp/batch_progress.json")
        except Exception:
            pass
        LAST_WORKER.update(stop_reason=stop_reason, run_id=report_run_id, worker_id=worker_id,
                           started_ts=started_ts, ended_ts=time.time(),
                           notice=final_notice or start_notice or notice or None, health=health,
                           bg_skipped=bg_skipped_count())
        if report:
            try:
                import run_report
                run_report.report_worker_run(dict(LAST_WORKER), trigger=trigger)
            except Exception as e:
                print(f"[Worker] تعذر كتابة تقرير التشغيل: {e}")


def run_automation_pipeline():
    """التشغيل التسلسلي القديم (بدون طابور)."""
    lock_file = "temp/pipeline.lock"
    try:
        load_run_config()
        google_sheets.init_async_queue(config.CREDENTIALS_FILE, config.SPREADSHEET_NAME_OR_URL)
        try:
            os.makedirs("temp", exist_ok=True)
            with open(lock_file, "w") as f:
                f.write(str(os.getpid()))
        except Exception:
            pass

        sheets_client = google_sheets.get_sheets_client()
        if not sheets_client:
            print("فشل الاتصال بـ Google Sheets API.")
            return
        try:
            worksheet = google_sheets.open_worksheet(sheets_client, config.SPREADSHEET_NAME_OR_URL)
            if not worksheet:
                print(f"لم يتم العثور على ورقة العمل: {config.SPREADSHEET_NAME_OR_URL}")
                return
            products, link_column_index = google_sheets.get_products(worksheet)
        except google_sheets.SheetTransientError as e:
            # ليس خطأ رابط أو مشاركة: Google رفض مؤقتاً حتى بعد إعادة المحاولة
            print(f"Google Sheets غير متاح مؤقتاً؛ لم يبدأ التشغيل. أعد المحاولة بعد دقائق. ({e})")
            return
        if not products:
            print("لم يتم العثور على أي منتجات صالحة للمعالجة.")
            return
        brand_mappings = google_sheets.get_brand_mappings(sheets_client, config.SPREADSHEET_NAME_OR_URL)
        _forget_brand_spellings()

        success_count = skipped_count = failed_count = 0
        save_progress(0, len(products), 0, 0, "بدء التشغيل...")
        for i, prod in enumerate(products, start=1):
            save_progress(i, len(products), success_count, failed_count, prod["product_name"])
            print(f"معالجة المنتج ({i}/{len(products)}): [{prod['product_name']}]")
            try:
                result = process_single_product(prod, worksheet, link_column_index, brand_mappings)
            except Exception as e:
                print(f"خطأ غير متوقع في الصف {prod['row_number']}: {e}")
                result = "failed"
            if result == "success":
                success_count += 1
            elif result == "failed":
                failed_count += 1
            elif result == "skipped":
                skipped_count += 1
        print(f"انتهت المعالجة: ناجح {success_count}، متخطى {skipped_count}، فاشل {failed_count}.")
    finally:
        google_sheets.stop_async_queue()
        for path in (lock_file, "temp/batch_progress.json"):
            try:
                if os.path.exists(path):
                    os.remove(path)
            except Exception:
                pass


if __name__ == "__main__":
    if "--enqueue" in sys.argv:
        run_enqueue_mode()
    elif "--worker" in sys.argv:
        run_worker_mode(trigger=_cli_trigger(sys.argv))
        import run_report
        sys.exit(run_report.exit_code(LAST_WORKER.get("stop_reason")))
    else:
        run_automation_pipeline()
