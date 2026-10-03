# main.py
# السكربت الرئيسي لتشغيل نظام الأتمتة: بناء الطابور (--enqueue)، عامل البحث المسبق (--worker)،
# والوضع التسلسلي القديم (بدون وسيط).
#
# القرارات الملزمة هنا (D10/D11/D14):
# - النشر التلقائي فقط عندما يكون قرار البحث AUTO_PUBLISH (لا عتبات clip_score)، وأبداً لنتيجة من الكاش.
# - إعادة المحاولة فقط عند PROVIDER_DOWN أو استثناء؛ "لا نتيجة" نظيفة لا تُعاد.
# - رفض المراجعين (روابط + pHash) يُمرر للبحث كاستبعادات لكل SKU.
# - اللوحة المنشورة 800x800 بيضاء من image_processor بدون أي تكبير لاحق؛ إذا لم تُعزل الخلفية
#   يُكتب الرابط ببادئة needs_review: ولا يُخزن كحل معتمد.

import json
import os
import sys
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
    return make_sku_key(None, match_key(brand).replace(" ", ""), spec.raw_name, spec.size)


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
    out = [dict(c) for c in cands if isinstance(c, dict) and (c.get("url") or c.get("image_url"))]
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


def serper_credit_refused(trace):
    """
    قاعدة SERPER_CREDIT في ops_health لبحث واحد: True عندما لم يُجب Serper عن أي استعلام ورفض واحداً على الأقل
    بسبب الرصيد أو المفتاح (quota، أو http 401/403)؛ False عندما أجاب؛ None عندما لم يُستدعَ Serper أصلاً.
    """
    import ops_health
    calls = []
    for item in _outcome(trace).get("provider_health") or []:
        if isinstance(item, dict) and str(item.get("provider") or "").strip().lower() == "serper":
            try:
                http = int(item.get("http_status"))
            except (TypeError, ValueError):
                http = None
            calls.append((str(item.get("status") or "").strip().lower(), http))
    if not calls:
        return None
    if any(status in ops_health.ANSWERED_STATUSES for status, _ in calls):
        return False
    return any(status == "quota" or (status == "error" and http in ops_health.KEY_REJECTED_HTTP)
               for status, http in calls)


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
                  candidate_sha256=None, bg_method=None, target=(0, 0), category_override=None,
                  enhance=False, force_review=False, key_size=None, key_brand=None):
    """
    معالجة الصورة المعتمدة إلى لوحة النشر النهائية ورفعها وكتابة رابطها في الشيت.
    key_size/key_brand: خلايا الحجم والبراند في الشيت لهذا المنتج، تُضاف إلى هوية الصف المتحقق منها
    قبل الكتابة (بدون باركود تميز الشقيقين بنفس الاسم).
    لا تكبير لاحق: اللوحة من image_processor نهائية. البيانات الوصفية تُكتب في الشيت فقط بعد نجاح الرفع.
    الحالة: 'published' (معزولة وليست للمراجعة) | 'needs_review' (رابط ببادئة needs_review:) | 'failed'.
    """
    w, h = target or (0, 0)
    result = image_processor.process_product_image_result(
        image_url, name, brand, target_width=w or 0, target_height=h or 0,
        bg_method=bg_method, candidate_sha256=candidate_sha256, enhance=bool(enhance),
    )
    if not result.path:
        return {"status": "failed", "error": result.error or "processing_failed", "isolated": False,
                "provider": result.provider}

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
        link = cloudinary_storage.upload_product_image_to_cloudinary(
            result.path, name, brand, folder=folder, tags=tags,
            target_width=result.width, target_height=result.height,
        )
    finally:
        image_processor.cleanup_processed_image(result.path)

    base = {"isolated": bool(result.isolated), "provider": result.provider, "metadata": metadata,
            "width": result.width, "height": result.height}
    if not link:
        return dict(base, status="failed", error="upload_failed")

    review = force_review or not result.isolated
    sheet_value = f"needs_review:{link}" if review else link
    identity = {"barcode": barcode, "product_name": name, "size": key_size, "brand": key_brand}
    if not google_sheets.update_image_link(worksheet, row_number, link_column_index, sheet_value, **identity):
        return dict(base, status="failed", error="sheet_write_failed", link=link)
    if metadata:
        try:
            google_sheets.update_product_metadata(worksheet, row_number, metadata, **identity)
        except Exception as e:
            print(f"تنبيه: تعذر كتابة البيانات الوصفية للصف {row_number}: {e}")
    return dict(base, status="needs_review" if review else "published", link=link, sheet_value=sheet_value)


# ---------------------------------------------------------------------------
# الاعتماد التلقائي والبحث المسبق
# ---------------------------------------------------------------------------

def auto_approve_product(task, best_image, worksheet, link_column_index, sku_key=None):
    """
    نشر نتيجة AUTO_PUBLISH مباشرة. تعيد 'published' أو 'needs_review' (الخلفية لم تُعزل) أو 'failed'.
    الحل يُخزن auto_verified فقط عند النشر الفعلي بلوحة معزولة.
    """
    name = task["product_name"]
    brand = task.get("brand") or ""
    barcode = task.get("barcode") or ""
    try:
        res = publish_image(
            best_image["url"], name, brand, task["row_number"], worksheet, link_column_index,
            barcode=barcode, candidate_sha256=best_image.get("content_sha256"),
            key_size=task_payload(task).get("size"), key_brand=brand,
            bg_method=getattr(config, "BG_REMOVAL_METHOD", None),
            target=getattr(config, "IMAGE_TARGET_SIZE", (0, 0)),
        )
    except Exception as e:
        print(f"[Auto-Publish Error] فشل النشر التلقائي لـ [{name}]: {e}")
        return "failed"
    if res["status"] == "published":
        local_cache_db.save_product_resolution(
            barcode, name, brand, best_image["url"], res["link"], None, res.get("metadata"),
            perceptual_hash=best_image.get("phash"), verification_status="auto_verified",
            approved_by="auto", sku_key=sku_key,
        )
        local_cache_db.delete_product_failure(barcode)
    elif res["status"] == "failed":
        print(f"[Auto-Publish] تعذر النشر لـ [{name}] ({res.get('error')}); يحال للمراجعة.")
    return res["status"]


def _has_human_approval(sku_key, alt_key=None):
    """
    هل للـ SKU حل معتمد بشرياً في الكاش؟ (خطأ القراءة يُعامل كنعم: الأمان أولاً، فلا نشر تلقائي).
    alt_key: المفتاح بلا باركود؛ اعتماد حُفظ قبل إضافة باركود صالح للصف يبقى اعتماداً لهذا المنتج.
    """
    for key in dict.fromkeys(k for k in (sku_key, alt_key) if k):
        try:
            cached = local_cache_db.get_cached_product(sku_key=key)
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


def _servable_resolution(sku_key, alt_key, name, brand, size, brand_mappings=None):
    """الحل المعتمد (human_approved / auto_verified) لهذا المنتج بمفتاحه أو بالمفتاح البديل، أو None."""
    for key in dict.fromkeys(k for k in (sku_key, alt_key) if k):
        cached = local_cache_db.get_cached_product(sku_key=key, product_name=name, brand=brand,
                                                   brand_mappings=brand_mappings, size_text=size or None)
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


def _relink_task(task, worksheet, link_column_index, sku_key, alt_key, brand_mappings=None):
    """
    مهمة «كتابة رابط معتمد» (task_kind='relink' من الإدراج): للمنتج صورة معتمدة لكن رابطها ليس في هذا الصف
    (صف مكرر، كتابة انتهت CONFLICT / DEAD، أو صف عُدل وللمنتج الجديد اعتماد بشري). يُكتب الرابط بلا بحث.
    تعيد None عندما لم يعد للمنتج حل معتمد (رُفض منذ الإدراج): يجري البحث العادي بدلها.
    """
    name, brand = task["product_name"], task.get("brand") or ""
    res = _servable_resolution(sku_key, alt_key, name, brand, task_payload(task).get("size"), brand_mappings)
    if res is None:
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
    النشر التلقائي لمنتج له صفوف أخرى في الشيت (نفس sku_key) تنتظر: الصورة المنشورة تُكتب في كل صف بهويته.
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


def _record_spend(trace, run_id=None):
    """تكلفة محاولة بحث واحدة في سجل الصرف اليومي (search_spend). لا يرفع أبداً."""
    local_cache_db.record_search_spend(_outcome(trace), run_id)


def pre_cache_product_candidates(task, worksheet=None, link_column_index=None, brand_mappings=None,
                                 sleep=time.sleep, report=None):
    """
    البحث المسبق لمهمة من الطابور وحفظ مرشحاتها للمراجعة، أو نشرها إذا كان القرار AUTO_PUBLISH.
    تعيد 'success' | 'failed' | 'provider_down'.
    - مهمة relink: يُكتب الرابط المعتمد للمنتج بلا بحث.
    - صف عُدل بعد نشره (review_only) لا يُنشر تلقائياً أبداً: تُعرض النتيجة للمراجعة.
    - إعادة تحقق (VERIFIER_RECHECK) لم تجد شيئاً: يعود الصف جاهزاً للمراجعة بمرشحاته السابقة.
    - رفض Serper كل استعلاماته (رصيد / مفتاح) و«لا نتيجة»: انقطاع وليس فشلاً للمنتج.
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
        down = state == "provider_down" or (state == "ok" and credit)
        if recheck:
            # إعادة التحقق لم تأتِ بجديد: المرشحات السابقة ما زالت محفوظة، فيعود الصف للمراجعة ولا يضيع
            _finish_task(task, "ready_for_review", f"Re-verification found nothing new ({code})",
                         failure_code="VERIFIER_DOWN", trace=trace)
            return "provider_down" if down else "success"
        if down:
            # انقطاع المزودين (أو رصيد Serper) ليس فشلاً للمنتج: يعود الصف للانتظار بموعد ولا يُسجل في product_failures
            message = "Serper refused every query (credit or key)" if credit else "Search providers unavailable"
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
    if (decision == "AUTO_PUBLISH" and best.get("source") != "sqlite_cache"
            and worksheet is not None and link_column_index is not None):
        status = auto_approve_product(task, best, worksheet, link_column_index, sku_key=sku_key)
        if status == "published":
            written = _publish_to_siblings(task, sku_key, worksheet, link_column_index)
            _finish_task(task, "completed", failure_code=None,
                         trace={"outcome": _outcome(trace)}, siblings=written)
            print(f"[Auto-Publish] تم نشر الصف {row_number} تلقائياً (قرار AUTO_PUBLISH).")
            return "success"

    candidates = collect_candidates(best, trace)
    saved = local_cache_db.save_curation_candidates(
        row_number, name, brand, candidates, best.get("url"), sku_key=sku_key, run_id=uuid.uuid4().hex[:16])
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
    best, trace, state, error = search_with_retry(query, name, brand, search_kwargs)
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
                                                       sku_key=sku_key, run_id=uuid.uuid4().hex[:16]):
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
                        candidate_sha256=best.get("content_sha256"),
                        bg_method=getattr(config, "BG_REMOVAL_METHOD", None),
                        target=getattr(config, "IMAGE_TARGET_SIZE", (0, 0)),
                        enhance=getattr(config, 'ENABLE_IMAGE_ENHANCEMENT', False),
                        force_review=decision != "AUTO_PUBLISH", key_size=payload["size"], key_brand=brand)
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


def _release_starting_lock(lock_file=LOCK_FILE):
    """يحذف قفل لوحة التحكم 'STARTING' (مرحلة الإدراج) فقط؛ قفل يحمل PID يحرره صاحبه (العامل أو التشغيل الليلي)."""
    try:
        with open(lock_file, "r") as f:
            if f.read().strip() != "STARTING":
                return
        os.remove(lock_file)
    except OSError:
        pass


def _enqueue_failed(message):
    """
    فشل الإدراج: رسالة عربية في automation_state.notice بحالة 'error' تعرضها اللوحة فوراً، وتحرير قفل 'STARTING'
    فوراً (لا تبقى اللوحة على «قيد التشغيل» وترفض تشغيلاً جديداً لخمس دقائق). العامل لا يبدأ (رمز الخروج 1).
    """
    print(f"[Enqueue Error] {message}")
    # التشغيل انتهى هنا: طلب إيقاف سُجل أثناء الإدراج يُلغى، وإلا أوقف عاملاً يُشغَّل لاحقاً يدوياً قبل أي منتج
    local_cache_db.update_automation_state(status="error", current_product="", notice=f"ENQUEUE_FAILED: {message}",
                                           stop_requested=0)
    _release_starting_lock()
    sys.exit(1)


def run_enqueue_mode():
    """
    قراءة الشيت وإضافة الصفوف للطابور (Upsert). يتم التحقق من الاتصال وعناوين الأعمدة والفلاتر
    قبل لمس الطابور، ولا تُمسح صفوف المراجعة أبداً. كل إدراج يبدأ تشغيلاً جديداً (run_id) يحمله كل صف
    سيعالجه العامل (local_cache_db.begin_run)، فيُحسب التقدم من صفوف هذا التشغيل فقط.
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
            _enqueue_failed("تعذر الاتصال بـ Google Sheets. تحقق من ملف بيانات الاعتماد والاتصال بالإنترنت. "
                            "لم يتغير الطابور.")
        worksheet = google_sheets.open_worksheet(sheets_client, config.SPREADSHEET_NAME_OR_URL)
        if not worksheet:
            _enqueue_failed(f"لم يُعثر على الشيت «{config.SPREADSHEET_NAME_OR_URL}». تحقق من الرابط واسم ورقة "
                            "العمل ومن مشاركة الشيت مع حساب الخدمة. لم يتغير الطابور.")
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
        _enqueue_failed(f"تعذر قراءة الشيت: {e}. لم يتغير الطابور.")

    reprocess = bool(getattr(config, "FORCE_OVERWRITE_IMAGES", False))
    brand_filter = (config.BRAND_FILTER or "").lower()
    enqueued = skipped_final = 0
    try:
        for prod in products:
            row_num = prod["row_number"]
            name = prod["product_name"]
            brand = prod.get("brand") or ""
            if allowed_rows is not None and row_num not in allowed_rows:
                continue
            if brand_filter and brand_filter not in brand.lower():
                continue
            if prod.get("existing_image_link") and not reprocess:
                skipped_final += 1
                continue
            payload = {
                "name_ar": prod.get("product_name_ar", ""),
                "brand_ar": prod.get("brand_ar", ""),
                "category": prod.get("category", ""),
                "sub_category": prod.get("sub_category", ""),
                "origin": prod.get("origin", ""),
                "size": prod.get("size", ""),
            }
            barcode = prod.get("barcode", "")
            sku_key = compute_sku_key(sku_row(name, brand, barcode, payload), brand_mappings)
            local_cache_db.add_to_queue(row_num, barcode, name, brand, prod.get("search_query") or default_query(name, brand),
                                        payload=payload, sku_key=sku_key, reprocess=reprocess)
            enqueued += 1
        print(f"[Enqueue] {enqueued} صف في الطابور؛ {skipped_final} صف تم تخطيه لأن رابطه نهائي.")
        local_cache_db.get_queue_statistics()
    except Exception as e:
        _enqueue_failed(f"تعذر إضافة الصفوف إلى الطابور: {e}. أُضيف {enqueued} صف قبل الخطأ ولم يُحذف أي صف.")

    run_id = local_cache_db.new_run_id()
    run_rows = local_cache_db.begin_run(run_id)
    if run_rows is None:
        print("[Enqueue] تنبيه: تعذر تسجيل التشغيل الجديد؛ ستعرض اللوحة تقدم الطابور كله.")
    else:
        print(f"[Enqueue] التشغيل {run_id} سيعالج {run_rows} صف (الصفوف في الانتظار بما فيها ما بقي من تشغيل سابق).")


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


def _outage_notice(worker_id, since_seconds, base=None):
    """
    تنبيه اللوحة عند انتهاء العامل: base + سبب انقطاع ظهر في عمليات بحث هذا العامل (رصيد Serper انتهى /
    Gemini لا يستجيب، من ops_health). None عندما لا يوجد أيهما فيبقى التنبيه الحالي. فشل الفحص لا يوقف الإنهاء.
    """
    outage = ""
    if worker_id:
        try:
            import ops_health
            outage = ops_health.outage_notice(since_seconds, worker_id=worker_id)
        except Exception as e:
            print(f"تنبيه: تعذر فحص انقطاع المزودين: {e}")
    return " | ".join(n for n in (base, outage) if n) or None


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


def _another_worker_running(lock_file):
    import subprocess
    if not os.path.exists(lock_file):
        return False
    try:
        with open(lock_file, "r") as f:
            pid_str = f.read().strip()
        if not pid_str.isdigit() or pid_str == "1" or int(pid_str) == os.getpid():
            return False
        pid = int(pid_str)
        import platform
        if platform.system().lower() == "windows":
            output = subprocess.check_output(f'tasklist /FI "PID eq {pid}"', shell=True).decode(errors="replace")
            return str(pid) in output
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    except Exception:
        return False


BUDGET_WARN_RATIO = 0.8


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
    """
    if notice:
        parked = local_cache_db.park_verifier_rechecks()
        if parked:
            print(f"[Worker] قارئ الملصق غير متاح؛ {parked} صف إعادة تحقق عاد للمراجعة.")
        return
    requeued = local_cache_db.requeue_verifier_down(run_id)
    if requeued:
        print(f"[Worker] {requeued} صف انتظر المراجعة لأن قارئ الملصق تعطل؛ يُعاد بحثه الآن بقارئ يعمل.")


def run_worker_mode():
    """
    عامل الخلفية: يسحب المهام ذرياً ويعالجها بالتوازي (3 خيوط).
    يخرج فقط عندما ينجح COUNT(*) للمهام المفتوحة ويعيد 0، أو عند توقف المزودين (5 مهام متتالية PROVIDER_DOWN)،
    أو عند طلب إيقاف من لوحة التحكم (stop_requested): لا يسحب مهمة جديدة، ينهي المنتجات الجارية، ثم يعيد
    local_cache_db.stop_run الصفوف العالقة للانتظار. طلب إيقاف سُجل أثناء الإدراج يُنفذ قبل معالجة أي منتج.
    """
    from concurrent.futures import ThreadPoolExecutor
    import threading

    lock_file = LOCK_FILE
    os.makedirs("temp", exist_ok=True)
    if _another_worker_running(lock_file):
        print("[Worker] معالج الخلفية يعمل بالفعل. خروج.")
        sys.exit(0)
    try:
        with open(lock_file, "w") as f:
            f.write(str(os.getpid()))
    except Exception:
        pass

    print("=" * 60)
    print("عامل البحث المسبق (Worker) قيد العمل...")
    print("=" * 60)

    load_run_config()
    local_cache_db.resume_automation()   # علم الإيقاف المؤقت القديم لا يمنع تشغيلاً جديداً
    started = time.monotonic()
    state = local_cache_db.get_automation_state()
    run_id = state.get("run_id") or None
    # طلب إيقاف وصل أثناء الإدراج (قبل وجود العامل): لا يُعالج أي منتج
    stop_reason = "stopped" if state.get("stop_requested") == 1 else None
    notice = "" if stop_reason else check_verifier()
    if notice:
        print(f"[Worker] {notice}")

    queue_started = False
    worker_id = None
    try:
        if stop_reason:
            print("[Worker] طلب إيقاف من لوحة التحكم سُجل قبل بدء العامل؛ لن يُعالج أي منتج.")
            return
        sheets_client = google_sheets.get_sheets_client()
        if not sheets_client:
            stop_reason = "sheets_unavailable"
            local_cache_db.update_automation_state(status="error", notice="SHEETS_UNAVAILABLE: Google Sheets connection failed")
            return
        try:
            worksheet = google_sheets.open_worksheet(sheets_client, config.SPREADSHEET_NAME_OR_URL)
            if not worksheet:
                raise google_sheets.SheetConfigError(f"sheet not found: {config.SPREADSHEET_NAME_OR_URL}")
            link_column_index = google_sheets.find_link_column(worksheet)
        except Exception as e:
            stop_reason = "sheet_config"
            local_cache_db.update_automation_state(status="error", notice=f"SHEET_CONFIG: {e}")
            print(f"[Worker] {e}")
            return
        brand_mappings = google_sheets.get_brand_mappings(sheets_client, config.SPREADSHEET_NAME_OR_URL)
        google_sheets.init_async_queue(config.CREDENTIALS_FILE, config.SPREADSHEET_NAME_OR_URL)
        queue_started = True
        _refresh_state("pre_caching", run_id=run_id, notice=notice)

        worker_id = local_cache_db.new_claim_id().split("#")[0]
        lock = threading.Lock()
        counters = {"provider_down_streak": 0, "credit_streak": 0}
        _prepare_verifier_rechecks(notice, run_id)
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
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            while True:
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
                    if state.get("pause_requested") == 1:
                        time.sleep(1)
                        continue
                    if len(active) < max_workers:
                        if budget > 0:
                            spent = local_cache_db.spend_today()
                            if spent >= budget:
                                stop_reason = "budget_reached"
                                notice = (f"BUDGET_REACHED: daily search budget {budget:.2f} USD reached "
                                          f"(spent {spent:.2f}); remaining rows stay pending")
                                print(f"[Worker] بلغ صرف اليوم {spent:.2f}$ الميزانية اليومية {budget:.2f}$؛ "
                                      "إيقاف العامل وإبقاء الصفوف في الانتظار.")
                                break
                            if not budget_warned and spent >= BUDGET_WARN_RATIO * budget:
                                budget_warned = True
                                print(f"[Worker] تنبيه: صرف اليوم {spent:.2f}$ بلغ {int(BUDGET_WARN_RATIO * 100)}% "
                                      f"من الميزانية اليومية {budget:.2f}$.")
                        task = local_cache_db.fetch_next_task(worker_id)
                        if task:
                            print(f"[Queue] سحب مهمة الصف {task['row_number']}.")
                            active.append(executor.submit(runner, task))
                            db_outage_since = None
                            continue
                    if not active and local_cache_db.count_open_tasks() == 0:
                        print("[Worker] الطابور فارغ؛ خروج العامل.")
                        break
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
    finally:
        try:
            # سبب الانقطاع (رصيد Serper / Gemini) من صفوف هذا العامل فقط (worker_id)؛ None يترك التنبيه كما هو.
            # نهاية التشغيل تلغي طلب إيقاف وصل مع نهايته (stop_requested=0) كي لا يوقف عاملاً لاحقاً قبل أي منتج.
            run_seconds = time.monotonic() - started + 60
            if stop_reason == "stopped":
                # يعيد أي صف بقي 'processing' إلى الانتظار، ويلغي طلب الإيقاف، ويضبط الحالة (مراجعة أو خامل)
                local_cache_db.stop_run(worker_active=False)
            elif stop_reason == "provider_down":
                local_cache_db.update_automation_state(
                    status="provider_down", current_product="", stop_requested=0,
                    notice=_outage_notice(worker_id, run_seconds,
                                          "PROVIDER_DOWN: search providers unavailable; remaining rows stay pending"))
            elif stop_reason in ("sheets_unavailable", "sheet_config"):
                pass
            elif local_cache_db.get_ready_for_review_count() > 0:
                local_cache_db.update_automation_state(status="curation_pending", current_product="", stop_requested=0,
                                                       notice=_outage_notice(worker_id, run_seconds, notice))
            else:
                local_cache_db.update_automation_state(status="idle", current_product="", stop_requested=0,
                                                       notice=_outage_notice(worker_id, run_seconds, notice))
        except Exception as e:
            print(f"[Worker] تعذر تحديث الحالة النهائية: {e}")
        if queue_started:
            google_sheets.stop_async_queue()
        for path in (lock_file, "temp/batch_progress.json"):
            try:
                if os.path.exists(path):
                    os.remove(path)
            except Exception:
                pass


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
        worksheet = google_sheets.open_worksheet(sheets_client, config.SPREADSHEET_NAME_OR_URL)
        if not worksheet:
            print(f"لم يتم العثور على ورقة العمل: {config.SPREADSHEET_NAME_OR_URL}")
            return
        products, link_column_index = google_sheets.get_products(worksheet)
        if not products:
            print("لم يتم العثور على أي منتجات صالحة للمعالجة.")
            return
        brand_mappings = google_sheets.get_brand_mappings(sheets_client, config.SPREADSHEET_NAME_OR_URL)

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
        run_worker_mode()
    else:
        run_automation_pipeline()
