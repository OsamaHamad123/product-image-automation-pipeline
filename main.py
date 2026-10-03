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
#   يُكتب الرابط ببادئة needs_review: ولا يُخزن كحل معتمد.

import json
import os
import re
import socket
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


def search_with_retry(query, name, brand, search_kwargs, sleep=time.sleep,
                      max_attempts=MAX_SEARCH_ATTEMPTS, base_delay=RETRY_BASE_DELAY):
    """
    البحث مع إعادة المحاولة فقط عند PROVIDER_DOWN أو استثناء (بحد أقصى 3 مع ارتداد).
    كل محاولة تحصل على trace جديد. تعيد (best, trace, state, error) حيث state: ok | provider_down | error.
    """
    delay = base_delay
    trace, state, error = {}, "ok", None
    for attempt in range(1, max_attempts + 1):
        trace = {}
        try:
            best = image_search.search_best_product_image(query, name, brand, trace=trace, **search_kwargs)
        except Exception as ex:
            state, error = "error", f"{type(ex).__name__}: {ex}"
            print(f"[Attempt {attempt}/{max_attempts}] فشل البحث للمنتج [{name}]: {error}")
        else:
            if not _is_provider_down(best, trace):
                return best, trace, "ok", None
            state, error = "provider_down", "all search providers unavailable"
            print(f"[Attempt {attempt}/{max_attempts}] محركات البحث غير متاحة للمنتج [{name}].")
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
                  also_rows=None):
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
    duplicates: صورة نُشرت لمنتج آخر (نفس رابط Cloudinary أو pHash اللوحة على مسافة 4 أو أقل، sku_key مختلف):
    'block' (النشر التلقائي من الطابور) لا يكتب شيئاً والحالة 'needs_review' (error='duplicate_image')؛ 'review'
    (الوضع التسلسلي القديم) يكتب الرابط ببادئة needs_review: فقط؛ 'warn' (اعتماد المراجع الصريح) يكتب كالمعتاد.
    المالكون في duplicate_of، وتعذر التحقق يُعامل كتكرار في 'block' و 'review'.
    phash: بصمة اللوحة النهائية (تُخزن مع الحل المعتمد).
    لا تكبير لاحق: اللوحة من image_processor نهائية. البيانات الوصفية تُكتب في الشيت فقط بعد نجاح الرفع.
    الحالة: 'published' (معزولة وليست للمراجعة) | 'needs_review' (رابط ببادئة needs_review:) | 'superseded'
    (لم يُكتب شيء) | 'failed'.
    """
    profile = profile or processing_profile.current()
    w, h = profile.target
    result = image_processor.process_product_image_result(
        image_url, name, brand, target_width=w, target_height=h,
        bg_method=profile.bg_method, candidate_sha256=candidate_sha256, enhance=profile.enhance,
    )
    if not result.path:
        return {"status": "failed", "error": result.error or "processing_failed", "isolated": False,
                "provider": result.provider, "profile": profile.as_dict()}

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
        link = cloudinary_storage.upload_product_image_to_cloudinary(
            result.path, name, brand, folder=folder, tags=tags,
            target_width=result.width, target_height=result.height,
        )
    finally:
        image_processor.cleanup_processed_image(result.path)

    base = {"isolated": bool(result.isolated), "provider": result.provider, "metadata": metadata,
            "width": result.width, "height": result.height, "profile": profile.as_dict(), "phash": phash,
            "quality_flags": getattr(result, "quality_flags", None)}
    if not link:
        return dict(base, status="failed", error="upload_failed")

    # نفس الصورة منشورة لمنتج آخر؟ الرفع الموجود مسبقاً (existing من Cloudinary) دليل إضافي فقط
    owners = local_cache_db.find_image_owners(link, phash, sku_key=sku_key, product_name=name)
    base["duplicate_of"] = list(owners or [])
    base["cloudinary_existing"] = getattr(link, "existing", None)
    duplicate = owners is None or bool(owners)
    if duplicates == "block" and duplicate:
        print(f"[Publish] صورة الصف {row_number} منشورة لمنتج آخر (أو تعذر التحقق)؛ لا نشر تلقائي، تُحال للمراجعة.")
        return dict(base, status="needs_review", error="duplicate_image", link=link)

    review = force_review or not result.isolated or (duplicates == "review" and duplicate)
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
    return dict(base, status="needs_review" if review else "published", link=link, sheet_value=sheet_value,
                rows_written=written, rows_failed=failed)


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
    (مراجع اعتمد المنتج أثناء المعالجة والرفع، أو لم يعد الصف محجوزاً لهذا العامل: لم يُكتب شيء) أو 'failed'.
    الحل يُخزن auto_verified فقط عند النشر الفعلي بلوحة معزولة.
    """
    name = task["product_name"]
    brand = task.get("brand") or ""
    barcode = task.get("barcode") or ""

    def still_ours():
        # إعادة التحقق تحت قفل النشر قبل الكتابة: الحجز ما زال لهذا العامل ولا يوجد اعتماد بشري
        return (local_cache_db.is_claim_held(task["id"], task.get("worker_id"))
                and not _has_human_approval(sku_key))

    try:
        res = publish_image(
            best_image["url"], name, brand, task["row_number"], worksheet, link_column_index,
            barcode=barcode, candidate_sha256=best_image.get("content_sha256"),
            key_size=task_payload(task).get("size"), key_brand=brand, profile=processing_profile.current(),
            sku_key=sku_key, before_write=still_ours, duplicates="block",
        )
    except Exception as e:
        print(f"[Auto-Publish Error] فشل النشر التلقائي لـ [{name}]: {e}")
        return "failed"
    if res["status"] == "superseded":
        print(f"[Auto-Publish] الصف {task['row_number']}: اعتمده مراجع أثناء المعالجة أو لم يعد محجوزاً لهذا "
              "العامل؛ لم يُكتب شيء.")
        return "superseded"
    if res.get("error") == "duplicate_image":
        _warn_duplicate(best_image)
    if res["status"] == "published":
        local_cache_db.save_product_resolution(
            barcode, name, brand, best_image["url"], res["link"], None, res.get("metadata"),
            perceptual_hash=res.get("phash"), verification_status="auto_verified",
            approved_by="auto", sku_key=sku_key,
        )
        local_cache_db.delete_product_failure(barcode)
    elif res["status"] == "failed":
        print(f"[Auto-Publish] تعذر النشر لـ [{name}] ({res.get('error')}); يحال للمراجعة.")
    return res["status"]


DUPLICATE_WARNING = "warn:duplicate_image"


def _warn_duplicate(best_image):
    """تحذير مراجعة على الصورة المختارة (ومرشحها المحفوظ): نفس الصورة منشورة لمنتج آخر."""
    url = best_image.get("url")
    for c in [best_image] + [c for c in best_image.get("candidates") or [] if isinstance(c, dict)]:
        if c is best_image or (url and (c.get("url") or c.get("image_url")) == url):
            reasons = c.setdefault("reasons", [])
            if isinstance(reasons, list) and DUPLICATE_WARNING not in reasons:
                reasons.append(DUPLICATE_WARNING)


def _has_human_approval(sku_key):
    """هل للـ SKU حل معتمد بشرياً في الكاش؟ (خطأ القراءة يُعامل كنعم: الأمان أولاً، فلا نشر تلقائي)."""
    if not sku_key:
        return False
    try:
        cached = local_cache_db.get_cached_product(sku_key=sku_key, strict=True)
    except Exception:
        return True
    return bool(cached) and cached.get("verification_status") == "human_approved"


def _finish_task(task, status, error_message=None, failure_code=None, trace=None):
    """تحديث حالة مهمة سحبها هذا العامل؛ لا يكتب فوق صف اعتمده مراجع أثناء المعالجة (ملكية الحجز)."""
    return local_cache_db.update_task_status(task["id"], status, error_message, failure_code=failure_code,
                                             trace=trace, claim_id=task.get("worker_id") or None)


def pre_cache_product_candidates(task, worksheet=None, link_column_index=None, brand_mappings=None,
                                 sleep=time.sleep):
    """
    البحث المسبق لمهمة من الطابور وحفظ مرشحاتها للمراجعة، أو نشرها إذا كان القرار AUTO_PUBLISH.
    تعيد 'success' | 'failed' | 'provider_down'.
    """
    name = task["product_name"]
    brand = task.get("brand") or ""
    barcode = task.get("barcode") or ""
    row_number = task["row_number"]
    payload = task_payload(task)
    sku_key = task.get("sku_key") or payload.get("sku_key") or compute_sku_key(
        sku_row(name, brand, barcode, payload), brand_mappings)
    query = (task.get("search_query") or "").strip() or default_query(name, brand)

    exclude_urls, exclude_phashes = local_cache_db.get_rejections(sku_key)
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
    best, trace, state, error = search_with_retry(query, name, brand, search_kwargs, sleep=sleep)

    if best is None:
        if state == "provider_down":
            # انقطاع المزودين ليس فشلاً للمنتج: يعود الصف للانتظار ولا يُسجل في product_failures
            _finish_task(task, "pending", "Search providers unavailable",
                         failure_code="PROVIDER_DOWN", trace=trace)
            return "provider_down"
        if state == "error":
            code, message = "SEARCH_ERROR", error or "search raised an exception"
        else:
            code = _outcome(trace).get("failure_code") or "NO_RESULTS"
            message = f"No acceptable image found ({code})"
        _finish_task(task, "failed", message, failure_code=code, trace=trace)
        local_cache_db.save_product_failure(barcode, name, brand, f"{code}: {message}")
        print(f"[Pre-Cache] لا توجد صورة للصف {row_number}: {code}")
        return "failed"

    if not local_cache_db.is_claim_held(task["id"], task.get("worker_id")):
        # مراجع اعتمد/رفض هذا الصف أثناء البحث، أو أعيد سحبه: لا نستبدل مرشحاته ولا ننشر فوقه
        print(f"[Pre-Cache] الصف {row_number} لم يعد محجوزاً لهذا العامل؛ تُهمل النتيجة.")
        return "success"

    decision = best.get("decision")
    if decision == "AUTO_PUBLISH" and _has_human_approval(sku_key):
        # لا يُنشر تلقائياً فوق صورة اعتمدها مراجع: تُعرض النتيجة للمراجعة فقط
        print(f"[Auto-Publish] الصف {row_number} له اعتماد بشري سابق؛ يحال للمراجعة بدل النشر التلقائي.")
        decision = "REVIEW_PRESELECTED"
    if (decision == "AUTO_PUBLISH" and best.get("source") != "sqlite_cache"
            and worksheet is not None and link_column_index is not None):
        status = auto_approve_product(task, best, worksheet, link_column_index, sku_key=sku_key)
        if status == "published":
            _finish_task(task, "completed", failure_code=None,
                         trace={"outcome": _outcome(trace)})
            print(f"[Auto-Publish] تم نشر الصف {row_number} تلقائياً (قرار AUTO_PUBLISH).")
            return "success"
        if status == "superseded" or not local_cache_db.is_claim_held(task["id"], task.get("worker_id")):
            # قرار المراجع (أو حجز أحدث) أثناء المعالجة والرفع يبقى كما هو: لا مرشحات ولا حالة فوقه
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
# {stop_reason, run_id, worker_id, started_ts, ended_ts, notice, health}. stop_reason None = الطابور انتهى.
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
    reason (في LAST_ENQUEUE للتشغيل الليلي): sheets_unavailable / sheet_not_found / db_unavailable تُعاد محاولتها
    ليلاً، وenqueue_failed / sheet_config خطأ إعداد.
    """
    LAST_ENQUEUE.update(reason=reason, message=message)
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
                            "لم يتغير الطابور.", reason="sheets_unavailable")
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
        _enqueue_failed(f"تعذر إضافة الصفوف إلى الطابور: {e}. أُضيف {enqueued} صف قبل الخطأ ولم يُحذف أي صف.",
                        reason="enqueue_failed" if local_cache_db.db_available() else "db_unavailable")

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


# ---------------------------------------------------------------------------
# قفل العامل (temp/pipeline.lock)
# القفل JSON: {pid, host, started_at, started_ts, role, cmd}؛ الصيغة القديمة (رقم العملية فقط) ما زالت تُقرأ،
# و'STARTING' تكتبه لوحة التحكم أثناء الإدراج. القفل يُعد حياً فقط إذا كانت عمليته بايثون تشغّل main.py أو
# run_nightly.py على هذا الجهاز، وبدأت قبل كتابة القفل، والقفل أحدث من MAX_LOCK_AGE_SECONDS. غير ذلك قفل متروك
# (عامل انهار أو أُنهي ثم أُعيد استخدام رقم عمليته): يُحذف ويُسجل السبب، فلا تضيع ليلة بسبب رقم عملية معاد.
# نفس القواعد في لوحة التحكم (ApiController::pipelineProcess).
# ---------------------------------------------------------------------------

# عامل أقدم من هذا يُعد عالقاً: التشغيل الليلي يتوقف بعد 8 ساعات (schedule_nightly.ps1 -MaxHours، حتى 23)
MAX_LOCK_AGE_SECONDS = 24 * 3600
# فرق مسموح بين وقت بدء العملية ووقت كتابة القفل (دقة ساعة النظام)
LOCK_CLOCK_SLACK_SECONDS = 5
LOCK_SCRIPTS = ("main.py", "run_nightly.py")
_LOCK_SCRIPT_RE = re.compile(r"(?:^|[\\/\s\"'])(?:main|run_nightly)\.py(?=$|[\s\"'])", re.IGNORECASE)


def write_lock(role, lock_file=LOCK_FILE):
    """يكتب قفل هذه العملية (role: worker | nightly). أخطاء الكتابة تُرفع."""
    os.makedirs(os.path.dirname(lock_file) or ".", exist_ok=True)
    now = time.time()
    data = {
        "pid": os.getpid(),
        "host": socket.gethostname(),
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now)),
        "started_ts": round(now, 3),
        "role": role,
        "cmd": " ".join(sys.argv)[:300],
    }
    with open(lock_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


def read_lock(lock_file=LOCK_FILE):
    """
    محتوى القفل: None إن لم يوجد، وإلا {raw, kind: json | pid | starting | invalid, pid, host, started_ts, role}.
    started_ts للصيغة القديمة (أو JSON بلا وقت) هو وقت تعديل الملف.
    """
    try:
        with open(lock_file, "r", encoding="utf-8", errors="replace") as f:
            raw = f.read().strip()
        mtime = os.path.getmtime(lock_file)
    except OSError:
        return None
    lock = {"raw": raw, "kind": "invalid", "pid": None, "host": None, "started_ts": mtime, "role": None}
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
        started = data.get("started_ts")
        lock.update(kind="json", pid=pid, host=str(data.get("host") or "") or None, role=data.get("role"),
                    started_ts=float(started) if isinstance(started, (int, float)) else mtime)
    return lock


def _windows_process_info(pid):
    """عملية على ويندوز: سطر الأوامر ووقت البدء من Win32_Process، أو اسم البرنامج فقط من tasklist."""
    import subprocess
    no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    script = (f"$p = Get-CimInstance Win32_Process -Filter 'ProcessId = {int(pid)}' -ErrorAction Stop; "
              "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
              "if ($p) { 'NAME=' + $p.Name; 'CREATED=' + ([DateTimeOffset]$p.CreationDate).ToUnixTimeSeconds(); "
              "'CMD=' + $p.CommandLine }")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                             capture_output=True, timeout=30, creationflags=no_window)
        if out.returncode == 0:
            info = {"alive": False, "name": None, "cmdline": None, "created": None}
            for line in out.stdout.decode("utf-8", errors="replace").splitlines():
                key, _, value = line.partition("=")
                if key == "NAME":
                    info.update(alive=True, name=value.strip())
                elif key == "CREATED" and value.strip().isdigit():
                    info["created"] = float(value.strip())
                elif key == "CMD":
                    info["cmdline"] = value.strip() or None
            return info
    except Exception:
        pass
    out = subprocess.run(["tasklist", "/FI", f"PID eq {int(pid)}", "/FO", "CSV", "/NH"], capture_output=True,
                         timeout=30, creationflags=no_window).stdout.decode(errors="replace")
    for line in out.splitlines():
        cells = [c.strip('"') for c in line.split('","')]
        if len(cells) > 1 and cells[1].strip('"') == str(pid):
            return {"alive": True, "name": cells[0], "cmdline": None, "created": None}
    return {"alive": False, "name": None, "cmdline": None, "created": None}


def _proc_process_info(pid):
    """عملية على لينكس من /proc: الاسم وسطر الأوامر ووقت البدء؛ عملية زومبي ليست حية."""
    base = f"/proc/{int(pid)}"
    try:
        with open(f"{base}/stat", "r") as f:
            stat = f.read()
    except OSError:
        return {"alive": False, "name": None, "cmdline": None, "created": None}
    fields = stat.rsplit(")", 1)[-1].split()
    if fields and fields[0] == "Z":
        return {"alive": False, "name": None, "cmdline": None, "created": None}
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
    (لينكس)، وإلا Win32_Process / tasklist (ويندوز)، وإلا os.kill و ps.
    """
    try:
        import psutil
    except ImportError:
        psutil = None
    if psutil is not None:
        try:
            p = psutil.Process(pid)
            if p.status() == psutil.STATUS_ZOMBIE:
                return {"alive": False, "name": None, "cmdline": None, "created": None}
            info = {"alive": True, "name": None, "cmdline": None, "created": None}
            for key, read in (("name", p.name), ("cmdline", lambda: " ".join(p.cmdline())), ("created", p.create_time)):
                try:
                    info[key] = read()
                except psutil.Error:
                    pass
            return info
        except psutil.NoSuchProcess:
            return {"alive": False, "name": None, "cmdline": None, "created": None}
        except psutil.Error:
            pass
    if os.name == "nt":
        return _windows_process_info(pid)
    if os.path.isdir("/proc"):
        return _proc_process_info(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return {"alive": False, "name": None, "cmdline": None, "created": None}
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


def stale_lock_reason(lock, now=None, process_info=None, host=None):
    """
    None إذا كان القفل لعامل أتمتة حي على هذا الجهاز، وإلا سبب اعتباره متروكاً (نص عربي للسجل).
    process_info(pid) -> {alive, name, cmdline, created} (للاختبارات؛ افتراضياً _process_info).
    """
    now = time.time() if now is None else now
    pid = lock.get("pid")
    if lock.get("kind") not in ("json", "pid") or not isinstance(pid, int):
        return f"محتوى غير مفهوم: «{str(lock.get('raw') or '')[:40]}»"
    if pid <= 1:
        return f"رقم عملية غير صالح ({pid})"
    here = host or socket.gethostname()
    if lock.get("host") and lock["host"].lower() != here.lower():
        return f"القفل من جهاز آخر ({lock['host']})"
    started = lock.get("started_ts")
    if started is not None and now - started > MAX_LOCK_AGE_SECONDS:
        return f"عمره أكثر من {MAX_LOCK_AGE_SECONDS // 3600} ساعة"
    try:
        info = (process_info or _process_info)(pid)
    except Exception as e:
        return f"تعذر فحص العملية {pid}: {type(e).__name__}"
    if not info.get("alive"):
        return f"العملية {pid} لم تعد تعمل"
    name = info.get("name")
    if name and "python" not in name.lower():
        return f"العملية {pid} ليست بايثون ({name})"
    cmdline = info.get("cmdline")
    if cmdline and not _LOCK_SCRIPT_RE.search(cmdline):
        return f"العملية {pid} ليست عامل الأتمتة (main.py / run_nightly.py)"
    created = info.get("created")
    if created is not None and started is not None and created > started + LOCK_CLOCK_SLACK_SECONDS:
        return f"العملية {pid} بدأت بعد كتابة القفل (رقم عملية أُعيد استخدامه)"
    return None


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
    (التشغيل الليلي يحمله أثناء الإدراج): False. قفل متروك يُحذف ويُسجل السبب ثم False.
    """
    lock = read_lock(lock_file)
    if lock is None or lock["kind"] == "starting" or lock.get("pid") == os.getpid():
        return False
    reason = stale_lock_reason(lock, now=now, process_info=process_info)
    if reason is None:
        return True
    removed = _remove_lock_if_unchanged(lock_file, lock["raw"])
    print(f"[Lock] قفل متروك في {lock_file}: {reason}؛ "
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
    """إعداد الشيت (تبويب أو عمود غير موجود) أم تعذر الوصول إليه (قد يزول وحده: يعيده التشغيل الليلي)."""
    config_errors = tuple(c for c in (getattr(google_sheets, "SheetConfigError", None),
                                      getattr(google_sheets, "SheetSchemaError", None)) if isinstance(c, type))
    return "sheet_config" if config_errors and isinstance(error, config_errors) else "sheets_unavailable"


def _cli_trigger(argv):
    """--trigger=dashboard|nightly|manual من سطر الأوامر؛ افتراضياً manual (تشغيل يدوي)."""
    for i, arg in enumerate(argv):
        value = arg.split("=", 1)[1] if arg.startswith("--trigger=") else (
            argv[i + 1] if arg == "--trigger" and i + 1 < len(argv) else None)
        if value and value.strip().lower() in WORKER_TRIGGERS:
            return value.strip().lower()
    return "manual"


def run_worker_mode(trigger="manual", report=True):
    """
    عامل الخلفية: يسحب المهام ذرياً ويعالجها بالتوازي (3 خيوط).
    يخرج فقط عندما ينجح COUNT(*) للمهام المفتوحة ويعيد 0، أو عند توقف المزودين (5 مهام متتالية PROVIDER_DOWN)،
    أو عند طلب إيقاف من لوحة التحكم (stop_requested): لا يسحب مهمة جديدة، ينهي المنتجات الجارية، ثم يعيد
    local_cache_db.stop_run الصفوف العالقة للانتظار. طلب إيقاف سُجل أثناء الإدراج يُنفذ قبل معالجة أي منتج.
    قاعدة بيانات لا ترد عند البدء: يتوقف فوراً (db_unavailable) بدل اعتبار التشغيل منتهياً.
    النتيجة في LAST_WORKER؛ report=True يكتب تقرير التشغيل (run_report: سجل التشغيلات، last_report.json، Telegram).
    التشغيل الليلي يمرر report=False ويكتب تقريراً واحداً لليلة بعد إعادة المحاولات.
    """
    from concurrent.futures import ThreadPoolExecutor
    import threading

    LAST_WORKER.clear()
    lock_file = LOCK_FILE
    os.makedirs("temp", exist_ok=True)
    if _another_worker_running(lock_file):
        LAST_WORKER.update(stop_reason="another_worker")
        print("[Worker] معالج الخلفية يعمل بالفعل. خروج.")
        sys.exit(0)
    try:
        write_lock("nightly" if trigger == "nightly" else "worker", lock_file)
    except Exception:
        pass

    print("=" * 60)
    print("عامل البحث المسبق (Worker) قيد العمل...")
    print("=" * 60)

    load_run_config()
    local_cache_db.resume_automation()   # علم الإيقاف المؤقت القديم لا يمنع تشغيلاً جديداً
    started = time.monotonic()
    started_ts = time.time()
    state = local_cache_db.get_automation_state()
    run_id = state.get("run_id") or None
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
    try:
        if stop_reason == "db_unavailable":
            print(f"[Worker] قاعدة البيانات لا ترد ({state.get('db_error') or '-'})؛ لن يُعالج أي منتج.")
            return
        if stop_reason:
            print("[Worker] طلب إيقاف من لوحة التحكم سُجل قبل بدء العامل؛ لن يُعالج أي منتج.")
            return
        sheets_client = google_sheets.get_sheets_client()
        if not sheets_client:
            stop_reason = "sheets_unavailable"
            start_notice = "SHEETS_UNAVAILABLE: Google Sheets connection failed"
            local_cache_db.update_automation_state(status="error", notice=start_notice)
            return
        try:
            worksheet = google_sheets.open_worksheet(sheets_client, config.SPREADSHEET_NAME_OR_URL)
            if not worksheet:
                # فتح الملف نفسه فشل: إعداد خاطئ، أو انقطاع مؤقت لا تميزه open_worksheet (يعيده التشغيل الليلي)
                stop_reason = "sheet_not_found"
                raise google_sheets.SheetConfigError(f"sheet not found: {config.SPREADSHEET_NAME_OR_URL}")
            link_column_index = google_sheets.find_link_column(worksheet)
        except Exception as e:
            stop_reason = stop_reason or _sheet_failure_reason(e)
            code = "SHEETS_UNAVAILABLE" if stop_reason == "sheets_unavailable" else "SHEET_CONFIG"
            start_notice = f"{code}: {e}"
            local_cache_db.update_automation_state(status="error", notice=start_notice)
            print(f"[Worker] {e}")
            return
        brand_mappings = google_sheets.get_brand_mappings(sheets_client, config.SPREADSHEET_NAME_OR_URL)
        google_sheets.init_async_queue(config.CREDENTIALS_FILE, config.SPREADSHEET_NAME_OR_URL)
        queue_started = True
        _refresh_state("pre_caching", run_id=run_id, notice=notice)

        worker_id = local_cache_db.new_claim_id().split("#")[0]
        lock = threading.Lock()
        counters = {"provider_down_streak": 0}

        def runner(t):
            try:
                local_cache_db.update_automation_state(status="pre_caching", current_product=t["product_name"])
                result = pre_cache_product_candidates(t, worksheet, link_column_index, brand_mappings)
            except Exception as e:
                _finish_task(t, "failed", f"Unexpected worker error: {e}", failure_code="WORKER_ERROR")
                print(f"[Worker Thread Error] الصف {t['row_number']}: {e}")
                result = "failed"
            with lock:
                counters["provider_down_streak"] = counters["provider_down_streak"] + 1 if result == "provider_down" else 0
            _refresh_state("pre_caching", run_id=run_id)

        max_workers = 3
        active = []
        db_outage_since = None
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            while True:
                active = [f for f in active if not f.done()]
                with lock:
                    streak = counters["provider_down_streak"]
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
        # سبب الانقطاع (رصيد Serper / Gemini) من صفوف هذا العامل فقط (worker_id)؛ None يترك التنبيه كما هو.
        # نهاية التشغيل تلغي طلب إيقاف وصل مع نهايته (stop_requested=0) كي لا يوقف عاملاً لاحقاً قبل أي منتج.
        run_seconds = time.monotonic() - started + 60
        health = _run_health(worker_id, run_seconds)
        final_notice = None
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
            else:
                # الطابور انتهى، أو سبب توقف آخر (مثل حد الميزانية) يظهر نصه كما هو في التنبيه والتقرير
                base = notice
                if stop_reason:
                    base = " | ".join(n for n in (
                        f"{str(stop_reason).upper()}: توقف العامل قبل نهاية الطابور ({stop_reason})؛ "
                        "بقيت الصفوف المتبقية في الانتظار", notice) if n)
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
        LAST_WORKER.update(stop_reason=stop_reason, run_id=run_id, worker_id=worker_id, started_ts=started_ts,
                           ended_ts=time.time(), notice=final_notice or start_notice or notice or None, health=health)
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
        run_worker_mode(trigger=_cli_trigger(sys.argv))
        import run_report
        sys.exit(run_report.exit_code(LAST_WORKER.get("stop_reason")))
    else:
        run_automation_pipeline()
