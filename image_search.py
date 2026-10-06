# image_search.py
# نقطة الدخول العامة للبحث عن صورة منتج: الكاش المحلي ثم catalog_match (المحرك الوحيد)، بالشكل الذي يقرؤه
# main.py و cli_bridge.py. محرك البحث القديم v1 حُذف مع وحداته (aesthetics_engine، image_quality_gatekeeper،
# query_refiner): لا يوجد إعداد يختار محركاً.

import logging

import config

logger = logging.getLogger(__name__)


def search_best_product_image(query, product_name, brand, **kwargs):
    """
    نقطة الدخول العامة للبحث عن صورة المنتج (التوقيع ثابت لكل المستدعين): مسار catalog_match.

    تجميع المرشحين من كل الاستعلامات، ترتيب بالهوية، تحقق Gemini مغلق عند الفشل، ثم توجيه القرار. لا توجد
    مواءمة للبراند عبر Gemini (D8).
    الوسائط الإضافية المقبولة: custom_query, exclude_urls, exclude_phashes, product_name_ar,
    brand_ar, category, size_text, barcode, skip_cache, trace, brand_mappings
    (وسائط أخرى مثل origin تُقرأ في facade.to_sku_row أو تُتجاهل). query لا يُستخدم: خطة الاستعلامات من الهوية.
    يعيد None عند NOT_FOUND أو PROVIDER_DOWN أو غياب أي مرشح قابل للمراجعة.
    """
    from catalog_match import facade, identity, pipeline

    trace = kwargs.get("trace")
    row = facade.to_sku_row(product_name, brand, kwargs)

    # 0. الكاش المحلي أولاً (مطابقة صارمة بالباركود عند وجوده).
    # لا يُستشار الكاش عندما يوجّه الموظف البحث (استعلام مخصص أو صور مستبعدة): التوجيه يجب أن يُنفَّذ (D8).
    steering = bool((kwargs.get("custom_query") or "").strip() or kwargs.get("exclude_urls")
                    or kwargs.get("exclude_phashes"))
    if not kwargs.get("skip_cache") and not steering:
        try:
            key_spec = identity.build_sku_spec(row, kwargs.get("brand_mappings") or None,
                                               size_text=kwargs.get("size_text") or None)
            # الباركود غير الصالح ('N/A' / '6.29E+12') لا يصلح مفتاحاً: يُمرَّر فارغاً
            cache_barcode = row["barcode"] if key_spec.gtin_status == "ok" else ""
            cached = _cached_result(cache_barcode, row["name"], row["brand"], key_spec.sku_key, trace,
                                    brand_mappings=kwargs.get("brand_mappings") or None,
                                    size_text=row.get("size") or None)
            if cached:
                logger.info("local cache hit for %r", product_name)
                return cached
        except Exception as e:
            logger.warning("local cache lookup failed: %s", e)

    # 1. مرادفات البراندات: تُجلب فقط إن لم تُمرَّر
    brand_mappings = kwargs.get("brand_mappings")
    if not brand_mappings:
        brand_mappings = _load_brand_mappings_for_search()

    spec = identity.build_sku_spec(row, brand_mappings, size_text=kwargs.get("size_text") or None)
    outcome = pipeline.find_product_image(
        spec,
        custom_query=kwargs.get("custom_query") or None,
        exclude_urls=kwargs.get("exclude_urls") or (),
        exclude_phashes=kwargs.get("exclude_phashes") or (),
    )
    # spec: تحذيرات العرض لكل مرشح (decide.candidate_warnings) وليس للمختارة فقط؛ لا تغيّر القرار
    return facade.outcome_to_legacy(outcome, trace, spec=spec)


def _load_brand_mappings_for_search():
    """جلب جدول مرادفات البراندات من Google Sheets (يُستدعى فقط عندما لا تُمرَّر المرادفات)."""
    try:
        import google_sheets
        sheets_client = google_sheets.get_sheets_client()
        if sheets_client:
            return google_sheets.get_brand_mappings(sheets_client, config.SPREADSHEET_NAME_OR_URL) or {}
    except Exception as e:
        logger.warning("failed to load brand mappings: %s", e)
    return {}


def _cached_result(barcode, product_name, brand, sku_key, trace, brand_mappings=None, size_text=None):
    """البحث في الكاش المحلي (نتيجة مسترجعة تحتاج دائماً مراجعة ولا تُنشر تلقائياً).

    السجل المخزن بالباركود لا يُخدم إلا إذا طابق براندُه واسمُه المنتجَ المطلوب (local_cache_db
    يتحقق من ذلك عند تمرير الاسم والبراند): باركود مشترك بين منتجين لا يعطي أحدهما صورة الآخر.
    """
    import inspect
    import local_cache_db

    lookup = local_cache_db.get_cached_product
    kwargs = {"barcode": barcode, "product_name": product_name, "brand": brand}
    try:
        params = inspect.signature(lookup).parameters
        if "sku_key" in params:
            kwargs["sku_key"] = sku_key
        if "brand_mappings" in params and brand_mappings:
            kwargs["brand_mappings"] = brand_mappings
        if "size_text" in params and size_text:
            kwargs["size_text"] = size_text
    except (TypeError, ValueError):
        pass
    cached = lookup(**kwargs)
    if not cached:
        return None
    url = cached.get("cloudinary_url") or cached.get("url")
    if not url:
        return None
    result = {
        "url": url,
        "title": "مسترجع من الكاش المحلي",
        "width": 800,
        "height": 800,
        "source": "sqlite_cache",
        "page_url": "",
        "content_sha256": None,
        "needs_review": True,
        "preselect": True,
        "unverified": False,
        "clip_score": None,
        "metadata": cached.get("metadata"),
        "decision": "REVIEW_PRESELECTED",
        "failure_code": None,
        "sku_key": sku_key,
        "status": "preselected",
        "candidates": [],
    }
    if trace is not None:
        trace["outcome"] = {
            "decision": "REVIEW_PRESELECTED", "failure_code": None, "provider_health": [],
            "queries": [], "sku_key": sku_key, "vlm_calls": 0, "reject_counts": {},
            "winner_url": url, "cache_hit": True,
        }
    return result
