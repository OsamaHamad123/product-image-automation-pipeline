# publish_check.py
# «فحص النشر»: بروفة لسلسلة النشر الحقيقية (cli_bridge.action_select_image -> main.publish_image) خطوة خطوة على صورة
# تجريبية، بدون ما يلمس أي منتج. «فحص الاتصالات» (verify_cloud_services) يسأل كل خدمة إذا بترد فقط؛ هون نمشي
# نفس الدوال اللي بينشر فيها الاعتماد:
#   1. download: صورة منتج بانتظار المراجعة (أول مرشح مختار مسبقاً، local_cache_db.first_review_candidate): مخزن
#      المرشحات ببصمتها (image_processor._load_from_candidate_store) ثم التنزيل الحقيقي (_download_bytes: مباشرة ثم
#      البروكسي) ومطابقة البصمة. بلا منتج بانتظار المراجعة: الصورة التجريبية assets/selftest/publish_check_sample.png.
#   2. process: image_processor.process_product_image_result بملف المعالجة الحالي (processing_profile.current())
#      على ملف مؤقت: قد يكلّف طلب عزل خلفية واحد (وقراءة Gemini لصندوق المنتج إن كان مفتاحها محفوظاً). عزل الخلفية
#      متوقف بالإعدادات (none): ✅ بلا أي طلب مدفوع، لأن الاعتماد ينشرها كما هي (main.publish_image: bg_skipped).
#      فشل رصيد أو مفتاح أو حصة PhotoRoom / remove.bg (bg_skip_offered): البطاقة تعرض زر «تجاوز عزل الخلفية».
#   3. upload: cloudinary_storage.upload_selftest_image (رافع النشر نفسه) إلى laqta_selftest/publish_check باسم ثابت
#      يُستبدل، ثم destroy_selftest_image بعد كل رفع نجح: لا تبقى صورة، ولا تُلمس صورة منتج.
#   4. sheet: الشيت كما يفتحه النشر (google_sheets.open_worksheet مع SPREADSHEET_TAB_NAME)، عمود الرابط بـ
#      find_link_column(create=False)، ثم قراءة خلية عنوان عمود الرابط وكتابة القيمة نفسها بنفس طريقة طابور الكتابة
#      (values_batch_update، RAW): يثبت صلاحية التعديل على التبويب الحقيقي بلا تغيير أي بيانات. عنوان ما انقرأ
#      بشكل أكيد: لا كتابة، وملاحظة. ومعها اسم التبويب وتراكم طابور الكتابة (google_sheets.outbox_summary)؛ طابور
#      لا يُقرأ (قاعدة البيانات) فشل، لأن الاعتماد يكتب بالشيت عن طريقه. تبويب اسمه نسخة أو اقتراحات
#      (_looks_like_backup) ملاحظة ⚠️ تقول وين رح ينكتب، وبلا SPREADSHEET_TAB_NAME التفاصيل تقول إنو أول تبويب.
# كل خطوة بمهلتها (خيط daemon)، والخطوة اللي بتحتاج نتيجة خطوة فشلت «ما انفحصت» (skipped) لا فشل. لا كتابة بأي صف
# منتج، ولا طابور، ولا إعدادات، ولا أقفال؛ آمن أثناء تشغيل (ملاحظة فقط). كل نص بالنتيجة يمر على
# verify_cloud_services._redact (لا يظهر أي مفتاح). آخر نتيجة تُحفظ في temp/publish_check_last.json لصفحة الصحة.

import hashlib
import json
import logging
import os
import re
import shutil
import tempfile
import threading
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit

import config
import cloudinary_storage
import google_sheets
import image_processor
import local_cache_db
import processing_profile
import verify_cloud_services

logger = logging.getLogger("publish_check")

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
LAST_RESULT_PATH = os.path.join(PROJECT_DIR, "temp", "publish_check_last.json")
SAMPLE_IMAGE = os.path.join(PROJECT_DIR, "assets", "selftest", "publish_check_sample.png")
SAMPLE_PRODUCT = ("Laqta Test Bottle 500ml", "Laqta")

OK, WARN, FAIL, SKIPPED = "ok", "warn", "fail", "skipped"
STEP_ORDER = ("download", "process", "upload", "sheet")
STEP_TITLES = {
    "download": "تنزيل الصورة",
    "process": "عزل الخلفية والمعالجة",
    "upload": "الرفع على Cloudinary",
    "sheet": "الكتابة بالشيت",
}
# ما تحتاجه كل خطوة من خطوة قبلها (الشيت لا يحتاج الصورة: يُفحص دائماً)
STEP_NEEDS = {"download": None, "process": ("bytes", "download"), "upload": ("canvas", "process"), "sheet": None}
# مهلة كل خطوة بالثواني، بحسب مهل النشر نفسه: التنزيل مباشرة ثم البروكسي (3 محاولات × 15 ث لكل طريق)، العزل حتى
# 3 محاولات (30 ث لكل مزوّد) مع صندوق Gemini، الرفع 3 محاولات × 20 ث ثم المسح، وفتح الشيت مع إعادة المحاولة.
# مجموعها (380 ث) أقل من مهلة لوحة التحكم (HealthController::PUBLISH_CHECK_KILL_SECONDS).
STEP_TIMEOUTS = {"download": 110, "process": 120, "upload": 90, "sheet": 60}
TIMEOUT_ACTIONS = {
    "download": "موقع المتجر أو البروكسي بطيء كتير: افحص البروكسي بصفحة الإعدادات أو شيله، وأعد الفحص.",
    "process": "خدمة عزل الخلفية بطيئة أو ما بترد: أعد الفحص بعد شوي.",
    "upload": "Cloudinary بطيء أو ما بيرد: تأكد من الإنترنت وأعد الفحص.",
    "sheet": "Google Sheets ما ردّ بالوقت: أعد الفحص بعد شوي.",
}
RUN_ACTIVE_NOTE = ("في تشغيل شغّال هلق: الفحص ما بيلمس الطابور ولا المنتجات ولا الأقفال، بس بيرفع صورة تجريبية "
                   "وبيمسحها، فما بيأثر عالتشغيل.")
COST_NOTE = ("الفحص ممكن يكلّف طلب عزل خلفية واحد (ونادراً أكتر إذا ما زبط العزل من أول مرة)، وقراءة Gemini وحدة "
             "إذا مفتاحها محفوظ.")

# أسباب فشل تنزيل الصورة المعتمدة بنفس كلمات شاشة المراجعة (dashboard/public/js/review/core.js SERVER_ERROR_TEXT)
DOWNLOAD_ERROR_TEXT = (
    (r"^source_changed$", "الصورة على موقع المتجر تغيّرت من وقت ما انفحصت، فما نشرناها: أعد البحث عن المنتج."),
    (r"^download_(timeout|connection_error|http_5\d\d|failed)$",
     "ما قدرنا ننزّل الصورة من موقع المتجر (الاتصال أو البروكسي): جرّب مرة ثانية."),
    (r"^download_http_(403|429)$",
     "موقع المتجر رفض تنزيل الصورة: جرّب مرة ثانية، وإذا تكرر جرّب صورة من متجر ثاني."),
    (r"^download_http_(404|410)$", "الصورة انشالت من موقع المتجر: أعد البحث أو اختر صورة ثانية."),
    (r"^download_|^(not_image|image_too_large|source_too_large)$",
     "الرابط ما عاد صورة صالحة: اختر صورة ثانية أو أعد البحث."),
)
# أخطاء البروكسي نفسه (بعد فشل التنزيل المباشر): البروكسي هو المشكلة لا موقع المتجر
_PROXY_DOWN = ("timeout", "connection_error", "http_407")
PROXY_ACTION = "البروكسي ما بيرد: افحصه بصفحة الإعدادات أو شيله."

# علامات فحص القص بنفس كلمات شاشة المراجعة (core.js QUALITY_FLAG_LABELS)
QUALITY_FLAG_TEXT = {
    "opaque_fill": "لم تُزل الخلفية (بقيت الصورة معتمة)",
    "opaque_backdrop": "بقي صندوق خلفية معتم حول المنتج",
    "edge_clipped": "المنتج مقصوص عند حافة الصورة",
    "alpha_haze": "هالة أو ضباب حول حواف المنتج",
    "second_object": "ظهر جسم آخر بجانب المنتج",
    "upscaled": "الصورة المصدر صغيرة فكُبّرت",
    "too_small_on_canvas": "المنتج صغير على اللوحة",
    "kept_shadow": "بقي ظل ظاهر مع المنتج",
}
METHOD_NAMES = {"photoroom": "PhotoRoom", "remove_bg_api": "remove.bg", "grabcut": "GrabCut (محلي)",
                "rembg": "rembg (محلي)", "bria_rmbg": "Bria (محلي)", "none": "بدون عزل"}
PROVIDER_NAMES = dict(METHOD_NAMES, source_alpha="شفافية الصورة الأصلية", white_source="قص محلي للخلفية البيضاء")
_PROVIDER_LABELS = {"photoroom": "PhotoRoom", "removebg": "remove.bg"}

# فشل الرفع حسب صنف الاستثناء (cloudinary.exceptions، UploadResult.cause)
UPLOAD_CAUSES = {
    "AuthorizationRequired": ("upload_auth", "Cloudinary رفض مفتاح الحساب.", "مفتاح Cloudinary مرفوض: حدّثه بالإعدادات."),
    "NotAllowed": ("upload_not_allowed", "مفتاح Cloudinary ما إله صلاحية رفع.",
                   "مفتاح Cloudinary ما إله صلاحية رفع: استعمل مفتاح الحساب الرئيسي بالإعدادات."),
    "BadRequest": ("upload_bad_request", "Cloudinary رفض الطلب.",
                   "تأكد من اسم حساب Cloudinary ومفتاحه بالإعدادات."),
    "NotFound": ("upload_not_found", "Cloudinary ما لقى الحساب.", "تأكد من اسم حساب Cloudinary بالإعدادات."),
    "RateLimited": ("upload_rate_limited", "Cloudinary رافض طلبات كتير هلق.", "استنى شوي وأعد الفحص."),
}
UPLOAD_NETWORK = ("upload_failed", "Cloudinary ما ردّ.", "Cloudinary ما بيرد: تأكد من الإنترنت وأعد الفحص.")

# اسم تبويب مش تبويب المنتجات على الأغلب: نسخة احتياطية، أو تبويب اقتراحات عملته أداة تانية (فحص 2026-10-05: «منتجات
# جديدة مقترحة 2» صار أول تبويب، وبلا SPREADSHEET_TAB_NAME النشر بيقرأ ويكتب بأول تبويب). كلمات إنكليزية (بداية كلمة،
# بلا حالة أحرف)، وعبارات، وجذور عربية (جزء من النص)
_BACKUP_PREFIXES = ("backup", "copy", "archive", "propos", "suggest")
_BACKUP_WORDS = ("old", "bak")
_BACKUP_PHRASES = ("new products", "copy of")
_BACKUP_AR = ("نسخة", "نسخه", "احتياط", "قديم", "أرشيف", "ارشيف", "مقترح", "اقتراح")
TAB_ACTION = "النشر رح يكتب بتبويب «{title}» — إذا مش تبويب منتجاتك، اختار التبويب الصح من الإعدادات (تبويب «الشيت»)"

# فشل عزل الخلفية عند PhotoRoom أو remove.bg بسبب الرصيد أو المفتاح أو الحصة: «تجاوز عزل الخلفية» (bg_removal_method =
# none) بيخلي الاعتماد يمشي لحد ما ينحل. نفس القاعدة بـ HealthController::BG_SKIP_PATTERN و health.js و review/core.js
BG_SKIP_CODE_RE = r"^(photoroom|removebg)_(no_key|401|402|403|429)$"
SKIP_ACTION = "اضغط «تجاوز عزل الخلفية»"
BG_SKIPPED_NOTE = "عزل الخلفية متوقف بالإعدادات: الصورة بتنتشر متل ما هي"


def bg_skip_offered(code):
    """هل رمز فشل المعالجة رصيد أو مفتاح أو حصة مزوّد عزل، فينفع معه «تجاوز عزل الخلفية»؟"""
    return bool(re.match(BG_SKIP_CODE_RE, str(code or "")))

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


# ---------------------------------------------------------------------------
# نصوص
# ---------------------------------------------------------------------------

def download_error_text(code):
    """جملة عربية لرمز فشل تنزيل (نفس ترتيب core.js SERVER_ERROR_TEXT)، أو None لرمز غير معروف."""
    code = str(code or "")
    for pattern, text in DOWNLOAD_ERROR_TEXT:
        if re.search(pattern, code, re.IGNORECASE):
            return text
    return None


def _flags_text(flags):
    return "، ".join(QUALITY_FLAG_TEXT.get(str(f), "ملاحظة أخرى من فحص القص") for f in flags)


def _process_error(code, method):
    """(تفاصيل، ما العمل) لرمز فشل المعالجة (image_processor.ProcessResult.error)."""
    code = str(code or "processing_failed")
    method_name = METHOD_NAMES.get(method, method or "؟")
    prefix = code.split("_", 1)[0]
    name = _PROVIDER_LABELS.get(prefix)
    if name:
        rest = code[len(prefix) + 1:]
        # رصيد أو مفتاح أو حصة (bg_skip_offered): كل اعتماد رح يفشل بنفس الشكل، فالبطاقة بتعرض «تجاوز عزل الخلفية»
        if rest == "no_key":
            return (f"مفتاح {name} مش محفوظ.", f"ضيف مفتاح {name} بالإعدادات، أو {SKIP_ACTION}.")
        if rest in ("401", "403"):
            return (f"{name} رفض المفتاح.", f"مفتاح {name} مرفوض: حدّثه بالإعدادات، أو {SKIP_ACTION}.")
        if rest == "402":
            return (f"رصيد {name} خلص أو الاشتراك موقوف.", f"اشحن رصيد {name}، أو {SKIP_ACTION}.")
        if rest == "429":
            return (f"{name} رافض طلبات كتير هلق (الحصة خلصت).", f"استنى دقيقة وأعد الفحص، أو {SKIP_ACTION}.")
        if rest in ("timeout", "connection_error") or rest.startswith("5"):
            return (f"{name} ما ردّ.", f"{name} ما بيرد: تأكد من الإنترنت وأعد الفحص بعد شوي.")
        if rest == "bad_output":
            return (f"{name} رجّع نتيجة مش صورة.", "أعد الفحص؛ وإذا تكرر جرّب مزوّد عزل تاني من تبويب «معالجة الصور».")
        return (f"{name} رجّع خطأ.", f"حدّث مفتاح {name} بالإعدادات أو جرّب مزوّد عزل تاني.")
    if code in ("rembg_not_installed",):
        return ("مكتبة rembg مش منزّلة على هالجهاز.", "اختار PhotoRoom أو remove.bg من تبويب «معالجة الصور».")
    if code in ("bria_rmbg_unsupported", "unknown_bg_method"):
        return (f"طريقة العزل المختارة ({method_name}) مش مدعومة.", "اختار PhotoRoom أو remove.bg من تبويب «معالجة الصور».")
    if code.startswith(("grabcut_", "rembg_")) or code.endswith("_empty_cutout"):
        return (f"العزل بـ {method_name} ما طلّع منتج من الصورة.", "اختار PhotoRoom أو remove.bg من تبويب «معالجة الصور».")
    if code.startswith(("source_", "candidate_", "download_")) or code.endswith("not_image"):
        return ("الصورة نفسها مش صالحة للمعالجة.", "أعد الفحص؛ وإذا تكرر اختار صورة تانية للمنتج.")
    return ("صار خطأ غير متوقع بالمعالجة.", "التفاصيل بسجل الأتمتة تحت؛ أعد الفحص.")


def _host(url):
    try:
        return (urlsplit(str(url or "")).hostname or "").lower().removeprefix("www.")
    except ValueError:
        return ""


def _redact(text):
    return verify_cloud_services._redact(text)


def _scrub(value):
    """كل نص بالنتيجة بعد حجب القيم السرية (مفاتيح الإعدادات وبيانات دخول البروكسي و key= بالروابط)."""
    if isinstance(value, str):
        return _redact(value)
    if isinstance(value, dict):
        return {k: _scrub(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_scrub(v) for v in value]
    return value


def _outcome(status, detail, action="", code="", **data):
    return {"status": status, "detail_ar": detail, "action_ar": action, "code": code, "data": data}


# ---------------------------------------------------------------------------
# 1. تنزيل الصورة
# ---------------------------------------------------------------------------

def _pick_sample():
    """(عينة، ملاحظة): أول مرشح مختار مسبقاً لمنتج بانتظار المراجعة، وإلا الصورة التجريبية."""
    try:
        row = local_cache_db.first_review_candidate()
    except Exception as e:  # noqa: BLE001 - قاعدة البيانات لا ترد: الصورة التجريبية
        logger.warning("publish_check: could not read the review queue: %s", e)
        row = None
        note = "ما قدرنا نقرأ قاعدة البيانات لنجيب صورة منتج بانتظار المراجعة، فجرّبنا على الصورة التجريبية."
    else:
        note = "ما في منتج بانتظار المراجعة بصورة مختارة، فجرّبنا على الصورة التجريبية."
    if row and str(row.get("image_url") or "").strip():
        return {"kind": "review", "product_name": str(row.get("product_name") or ""),
                "brand": str(row.get("brand") or ""), "row_number": row.get("row_number"),
                "image_url": str(row["image_url"]).strip(), "sha": str(row.get("content_sha256") or "").strip().lower(),
                "page_url": row.get("page_url") or None}, ""
    name, brand = SAMPLE_PRODUCT
    return {"kind": "bundled", "product_name": name, "brand": brand, "row_number": None, "image_url": "",
            "sha": "", "page_url": None}, note


def fetch_error_word(error):
    """كلمة عربية قصيرة لرمز تنزيل (http_client.FetchResult.error)؛ الرمز نفسه يبقى في code."""
    error = str(error or "")
    if error == "timeout":
        return "انتهت المهلة"
    if error == "connection_error":
        return "ما في اتصال"
    if error == "http_407":
        return "البروكسي طلب تسجيل دخول"
    if error in ("http_403", "http_429"):
        return "الموقع رفض الطلب"
    if error in ("http_404", "http_410"):
        return "الصورة مش موجودة"
    if error.startswith("http_5"):
        return "الموقع فيه عطل"
    if error == "not_image":
        return "الرد مش صورة"
    if error == "too_large":
        return "الصورة أكبر من الحد"
    return "خطأ"


def _route_text(info, data):
    direct = fetch_error_word(info.get("direct_error"))
    if data is not None:
        if info.get("route") == "proxy":
            return f"التنزيل المباشر ما زبط ({direct})، ونزلت عن طريق البروكسي."
        return "نزلت مباشرة من موقع المتجر (بدون بروكسي)."
    if info.get("proxy_tried"):
        return (f"التنزيل المباشر ما زبط ({direct})، وعن طريق البروكسي كمان ما زبط "
                f"({fetch_error_word(info.get('proxy_error'))}).")
    return f"التنزيل المباشر ما زبط ({direct}).{'' if _proxy_set() else ' ما في بروكسي مضبوط.'}"


def _proxy_set():
    return image_processor.settings.proxy_url()


def step_download(ctx):
    sample, note = _pick_sample()
    shown = {"kind": sample["kind"], "product_name": sample["product_name"], "row_number": sample["row_number"],
             "source": _host(sample["image_url"])}
    if sample["kind"] == "bundled":
        try:
            with open(SAMPLE_IMAGE, "rb") as fh:
                data = fh.read()
        except OSError:
            return _outcome(FAIL, note + " والصورة التجريبية نفسها مش موجودة بمجلد المشروع.",
                            "ملف الصورة التجريبية ناقص من نسخة المشروع: حدّث نسخة المشروع.",
                            "sample_missing", sample=shown)
        return _outcome(WARN, note + " تنزيل الصور من مواقع المتاجر ما انفحص هالمرة.",
                        "أعد الفحص لما يكون في منتج بانتظار المراجعة لنجرّب التنزيل من موقع المتجر كمان.",
                        "no_review_sample", sample=shown, bytes=data, name=sample["product_name"], brand=sample["brand"])

    row = f" (صف {sample['row_number']})" if sample["row_number"] is not None else ""
    lines = [f"المنتج: {sample['product_name']}{row}" + (f"، الصورة من {shown['source']}." if shown["source"] else ".")]
    sha = sample["sha"] if _SHA256_RE.match(sample["sha"]) else ""
    stored = image_processor._load_from_candidate_store(sha) if sha else None
    if not sha:
        lines.append("الصورة ما إلها بصمة محفوظة، فالاعتماد بينزّلها من موقع المتجر.")
    elif stored is not None:
        lines.append("الصورة موجودة بالمخزن، فالاعتماد بياخدها من المخزن بدون تنزيل.")
    else:
        lines.append("الصورة مش موجودة بالمخزن، فالاعتماد بينزّلها من موقع المتجر.")

    info = {}
    data, error = image_processor._download_bytes(sample["image_url"], sample["page_url"], info=info)
    lines.append(_route_text(info, data))
    if data is not None and sha and hashlib.sha256(data).hexdigest() != sha:
        data, error = None, "source_changed"
        lines.append("البايتات اللي نزلت مش نفس الصورة اللي انفحصت (البصمة تغيّرت).")
    payload = stored if stored is not None else data
    extra = {"sample": shown, "bytes": payload, "name": sample["product_name"], "brand": sample["brand"],
             "route": info.get("route"), "store_hit": stored is not None}
    if error is None:
        return _outcome(OK, " ".join(lines), "", "", **extra)
    text = download_error_text(error) or "ما قدرنا ننزّل الصورة."
    proxy_down = info.get("proxy_tried") and str(info.get("proxy_error") or "") in _PROXY_DOWN
    action = PROXY_ACTION if proxy_down else text
    if stored is not None:
        # الاعتماد يقرأ الصورة من المخزن فيمشي؛ لكن المنتجات اللي صورها مش بالمخزن بتتعطل بنفس الشكل
        return _outcome(WARN, " ".join(lines) + " الاعتماد لهالمنتج رح يمشي من المخزن، بس المنتجات اللي صورها مش "
                        "بالمخزن رح تفشل بنفس الشكل.", action, error, **extra)
    return _outcome(FAIL, " ".join(lines), action, error, **extra)


# ---------------------------------------------------------------------------
# 2. عزل الخلفية والمعالجة
# ---------------------------------------------------------------------------

def step_process(ctx):
    profile = processing_profile.current()
    method = profile.bg_method
    work = tempfile.mkdtemp(prefix="pubcheck_")
    try:
        source = os.path.join(work, "source")
        with open(source, "wb") as fh:
            fh.write(ctx["bytes"])
        w, h = profile.target
        result = image_processor.process_product_image_result(
            source, ctx.get("name") or "", ctx.get("brand") or "", target_width=w, target_height=h,
            bg_method=method, enhance=profile.enhance)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    method_line = f"طريقة العزل بالإعدادات: {METHOD_NAMES.get(method, method)}."
    if not result.path:
        detail, action = _process_error(result.error, method)
        return _outcome(FAIL, f"{method_line} {detail}", action, str(result.error or "processing_failed"),
                        provider=result.provider)
    provider = PROVIDER_NAMES.get(result.provider, result.provider)
    size = f"لوحة {result.width}×{result.height}"
    flags = [str(f) for f in (result.quality_flags or [])]
    notes = [str(n) for n in (result.quality_notes or [])]
    data = {"canvas": result.path, "provider": result.provider, "isolated": bool(result.isolated),
            "quality_flags": flags}
    skipped = str(method or "").strip().lower() in processing_profile.NO_REMOVAL_METHODS
    if skipped and result.provider == "none" and not result.isolated:
        # المالك أوقف عزل الخلفية (تبويب «معالجة الصور» أو «تجاوز عزل الخلفية»): الاعتماد بينشرها نظيفة (main.publish_image
        # bg_skipped)، وما في أي طلب مدفوع
        return _outcome(OK, f"{method_line} {BG_SKIPPED_NOTE} على {size}، بدون أي طلب عزل مدفوع. صورة خلفيتها "
                        "مش بيضا بتبين خلفيتها.", "", "bg_skipped", **data)
    if result.isolated:
        detail = f"{method_line} انعزلت الخلفية بـ {provider}، و{size} جاهزة."
        if notes:
            detail += f" ملاحظة: {_flags_text(notes)}."
        return _outcome(OK, detail, "", "", **data)
    if flags:
        import main as automation
        allowed = set(flags) <= automation.PRESENTATION_FLAGS
        detail = (f"{method_line} العزل اشتغل بـ {provider}، بس فحص القص لقى على هالصورة: {_flags_text(flags)}. "
                  + ("بالاعتماد بتنطلب منك موافقة لتنشرها رغم ذلك." if allowed
                     else "بالاعتماد هالصورة بالذات ما بتنتشر (الخلفية ما انعزلت منيح)."))
        return _outcome(WARN, detail, "إذا تكرر هالشي على صور كتير، جرّب مزوّد عزل تاني من تبويب «معالجة الصور».",
                        "quality_flags", **data)
    return _outcome(FAIL, f"{method_line} الصورة ما انعزلت خلفيتها، والاعتماد ما بينشر صورة خلفيتها ما انعزلت.",
                    "اختار PhotoRoom أو remove.bg من تبويب «معالجة الصور» بالإعدادات، أو «بدون عزل الخلفية» لتنتشر الصور "
                    "متل ما هي.", "background_not_removed", **data)


# ---------------------------------------------------------------------------
# 3. الرفع على Cloudinary ثم المسح
# ---------------------------------------------------------------------------

def step_upload(ctx):
    if not (config.CLOUDINARY_CLOUD_NAME and config.CLOUDINARY_API_KEY and config.CLOUDINARY_API_SECRET):
        return _outcome(FAIL, "إعدادات Cloudinary ناقصة (اسم الحساب أو المفتاح أو السر).",
                        "ضيف بيانات Cloudinary بتبويب «المفاتيح» بالإعدادات.", "cloudinary_not_configured")
    target = f"{cloudinary_storage.SELFTEST_FOLDER}/{cloudinary_storage.SELFTEST_PUBLIC_ID}"
    result = cloudinary_storage.upload_selftest_image(ctx["canvas"])
    if not result.public_id:
        code, detail, action = UPLOAD_CAUSES.get(str(result.cause or ""), UPLOAD_NETWORK)
        if result.error and result.error != "upload_failed":
            code, detail, action = (result.error, "اللوحة ما انقرت قبل الرفع.", "أعد الفحص؛ التفاصيل بسجل الأتمتة تحت.")
        return _outcome(FAIL, f"ما انرفعت الصورة التجريبية: {detail}", action, code, cause=result.cause)

    # كل رفع وصل لـ Cloudinary يُمسح، حتى لو ما طابق ما انبعت (الأصل موجود هناك)
    destroy_error = cloudinary_storage.destroy_selftest_image(result.public_id)
    if not result.url:
        detail = ("Cloudinary خزّن صورة مش نفس اللي انبعتت (الحجم أو البصمة)، فالنشر ما بيستعمل رابطها: غالباً في "
                  "إعداد رفع افتراضي (upload preset) بيعدّل الصور.")
        cleanup = "وانمسحت الصورة التجريبية." if destroy_error is None else "وما قدرنا نمسح الصورة التجريبية."
        return _outcome(FAIL, f"{detail} {cleanup}", "شيل إعداد الرفع الافتراضي من حساب Cloudinary وأعد الفحص.",
                        str(result.error or "upload_mismatch"), deleted=destroy_error is None)
    if destroy_error is None:
        return _outcome(OK, "انرفعت الصورة التجريبية على Cloudinary بمجلد الفحص وانمسحت، وما ضل شي منها.", "", "",
                        deleted=True)
    return _outcome(WARN, "الرفع شغّال، بس ما قدرنا نمسح الصورة التجريبية بعد الرفع. الفحص الجاي بيكتب فوقها.",
                    f"النشر نفسه شغّال؛ إذا بدك امسح الصورة {target} من حساب Cloudinary.", destroy_error,
                    deleted=False)


# ---------------------------------------------------------------------------
# 4. الكتابة بالشيت
# ---------------------------------------------------------------------------

def _service_email():
    try:
        with open(config.CREDENTIALS_FILE, "r", encoding="utf-8") as fh:
            email = str((json.load(fh) or {}).get("client_email") or "").strip()
        return email if "@" in email else ""
    except (OSError, ValueError, AttributeError):
        return ""


def _share_action():
    email = _service_email()
    who = f" ({email})" if email else ""
    return f"حساب الخدمة{who} ما عنده صلاحية تعديل على الشيت: شارك الشيت معه كمحرر."


def _a1(title, row, col):
    import gspread
    return "'" + str(title).replace("'", "''") + "'!" + gspread.utils.rowcol_to_a1(row, col)


def _looks_like_backup(title):
    """اسم تبويب نسخة احتياطية أو اقتراحات (مش تبويب المنتجات على الأغلب)، بلا حالة أحرف: «Copy of Products»،
    «Products backup»، «New Products»، «Suggested items»، «منتجات جديدة مقترحة 2»، «نسخة من المنتجات»."""
    text = google_sheets.normalize_header(title)
    words = text.split()
    raw = str(title or "")
    return (any(w.startswith(_BACKUP_PREFIXES) for w in words) or bool(set(words) & set(_BACKUP_WORDS))
            or any(p in text for p in _BACKUP_PHRASES) or any(w in raw or w in text for w in _BACKUP_AR))


def _tab_warning(lines, title, **data):
    """⚠️: النشر رح يكتب بتبويب اسمه بيوحي إنو نسخة أو اقتراحات (_looks_like_backup)."""
    detail = " ".join(lines + ["اسم التبويب بيوحي إنو نسخة أو تبويب اقتراحات، مش تبويب المنتجات."])
    return _outcome(WARN, detail, TAB_ACTION.format(title=title), "sheet_tab_backup", tab=title, **data)


def _outbox_line():
    """
    (سطر عن طابور الكتابة، عدد الكتابات اللي فشلت نهائياً بآخر 7 أيام، الطابور مقروء؟). الاعتماد يكتب بالشيت عن طريق
    هذا الطابور في قاعدة البيانات، فطابور لا يُقرأ يعني أن الكتابة نفسها ستفشل.
    """
    try:
        everything = google_sheets.outbox_summary()
        recent = google_sheets.outbox_summary(since_ts=time.time() - 7 * 86400)
    except Exception as e:  # noqa: BLE001
        logger.warning("publish_check: could not read the sheet outbox: %s", e)
        return "ما قدرنا نقرأ طابور الكتابة بالشيت من قاعدة البيانات.", 0, False
    pending, dead = int(everything.get("pending") or 0), int(recent.get("dead") or 0)
    return (f"طابور الكتابة بالشيت: {pending} كتابة بتستنى، و{dead} كتابة فشلت نهائياً بآخر 7 أيام.", dead, True)


def _sheet_failure(exc, title):
    """نتيجة خطأ قراءة أو كتابة من Google بعد فتح الشيت."""
    if isinstance(exc, google_sheets.SheetTransientError) or google_sheets._is_transient(exc):
        return _outcome(FAIL, "Google Sheets ما ردّ (ضغط أو انقطاع).", TIMEOUT_ACTIONS["sheet"], "sheet_unavailable",
                        tab=title)
    code = getattr(exc, "code", None)
    text = str(exc).casefold()
    if "protected" in text:
        return _outcome(WARN, f"صف العناوين بتبويب «{title}» محمي، فما قدرنا نجرّب الكتابة عليه.",
                        "تأكد إن عمود رابط الصورة مش محمي بالشيت.", "sheet_header_protected", tab=title)
    if code == 403 or "permission" in text:
        return _outcome(FAIL, f"الكتابة على تبويب «{title}» انرفضت.", _share_action(), "sheet_no_edit", tab=title)
    if code == 404:
        return _outcome(FAIL, "الشيت أو التبويب ما عاد موجود.", "تأكد من رابط الشيت واسم التبويب بالإعدادات.",
                        "sheet_not_found", tab=title)
    return _outcome(FAIL, "Google Sheets رفض الكتابة.", "التفاصيل بسجل الأتمتة تحت؛ أعد الفحص.",
                    f"sheet_write_failed:{type(exc).__name__}", tab=title)


DB_ACTION = "قاعدة البيانات ما بترد، والكتابة بالشيت بتمر عن طريقها: شغّل قاعدة البيانات وأعد الفحص."


def step_sheet(ctx):
    outcome = _sheet_permission()
    if outcome["status"] in (OK, WARN) and outcome["data"].get("outbox_ok") is False:
        outcome.update(status=FAIL, action_ar=DB_ACTION, code="outbox_unavailable")
    return outcome


def _sheet_permission():
    if not os.path.exists(config.CREDENTIALS_FILE):
        return _outcome(FAIL, "ملف حساب الخدمة (credentials.json) مش موجود.",
                        "حط ملف credentials.json لحساب الخدمة بمجلد المشروع.", "credentials_missing")
    client = google_sheets.get_sheets_client()
    if not client:
        return _outcome(FAIL, "ملف حساب الخدمة (credentials.json) مرفوض أو تالف.",
                        "نزّل مفتاح جديد لحساب الخدمة من Google Cloud وحطه بمجلد المشروع.", "sheet_credentials_rejected")
    try:
        worksheet = google_sheets.open_worksheet(client, config.SPREADSHEET_NAME_OR_URL)
    except google_sheets.SheetConfigError:
        tab = (getattr(config, "SPREADSHEET_TAB_NAME", "") or "").strip()
        return _outcome(FAIL, f"التبويب «{tab}» مش موجود بالشيت.", "اختار تبويب المنتجات الصح من تبويب «الشيت» بالإعدادات.",
                        "sheet_tab_missing")
    except google_sheets.SheetTransientError:
        return _outcome(FAIL, "Google Sheets ما ردّ (ضغط أو انقطاع).", TIMEOUT_ACTIONS["sheet"], "sheet_unavailable")
    if worksheet is None:
        return _outcome(FAIL, "ما قدرنا نفتح الشيت: يا الرابط غلط، يا الشيت مش مشارك مع حساب الخدمة.",
                        _share_action() + " وتأكد من رابط الشيت بالإعدادات.", "sheet_not_shared")
    title = str(getattr(worksheet, "title", "") or "")
    tab_name = (getattr(config, "SPREADSHEET_TAB_NAME", "") or "").strip()
    lines = [f"النشر رح يكتب بتبويب «{title}»" + ("." if tab_name else " (أول تبويب، لأنو ما في تبويب محدد بالإعدادات): "
                                                  "التشغيل والنشر بيقروا وبيكتبوا بأول تبويب بالشيت.")]
    suspicious = _looks_like_backup(title)
    outbox, dead, outbox_ok = _outbox_line()
    try:
        idx = google_sheets.find_link_column(worksheet, create=False)
        headers = google_sheets._worksheet_headers(worksheet)
    except Exception as e:  # noqa: BLE001
        return _sheet_failure(e, title)
    columns = google_sheets.resolve_columns(headers)
    if columns["name"] == -1:
        return _outcome(FAIL, " ".join(lines) + " بس التبويب ما فيه عمود اسم المنتج، فالنشر ما بيلاقي المنتجات فيه.",
                        "اختار تبويب المنتجات الصح من تبويب «الشيت» بالإعدادات.", "sheet_no_name_column", tab=title)
    if idx == -1:
        if suspicious:
            return _tab_warning(lines + ["ما في عمود لرابط الصورة بهالتبويب؛ أول اعتماد بيضيفه، فما جرّبنا الكتابة.",
                                         outbox], title, outbox_ok=outbox_ok)
        return _outcome(WARN, " ".join(lines + ["ما في عمود لرابط الصورة بهالتبويب؛ أول اعتماد بيضيفه، فما جرّبنا الكتابة.",
                                                outbox]),
                        "إذا بدك تتأكد من صلاحية الكتابة، ضيف عمود اسمه Drive Image Link وأعد الفحص.",
                        "sheet_no_link_column", tab=title, outbox_ok=outbox_ok)

    cell = _a1(title, 1, idx + 1)
    try:
        got = worksheet.spreadsheet.values_get(cell, params={"valueRenderOption": "FORMULA"})
    except Exception as e:  # noqa: BLE001
        return _sheet_failure(e, title)
    values = (got or {}).get("values") or [[]]
    raw = values[0][0] if values and values[0] else None
    shown = str(headers[idx]).strip() if idx < len(headers) else ""
    reliable = (isinstance(raw, str) and raw.strip() and not raw.startswith("=") and raw.strip() == shown
                and google_sheets.resolve_columns([raw])["link"] == 0)
    if not reliable:
        if suspicious:
            return _tab_warning(lines + ["ما قدرنا نقرأ عنوان عمود رابط الصورة بشكل أكيد، فما جرّبنا الكتابة.", outbox],
                                title, outbox_ok=outbox_ok)
        return _outcome(WARN, " ".join(lines + ["ما قدرنا نقرأ عنوان عمود رابط الصورة بشكل أكيد، فما جرّبنا الكتابة "
                                                "عشان ما نغيّر شي بالشيت.", outbox]),
                        "أعد الفحص بعد شوي؛ وإذا تكرر، اكتب عنوان العمود كنص عادي (Drive Image Link).",
                        "sheet_header_unreadable", tab=title, outbox_ok=outbox_ok)
    body = {"valueInputOption": "RAW", "data": [{"range": cell, "values": [[raw]]}]}
    try:
        worksheet.spreadsheet.values_batch_update(body)
    except Exception as e:  # noqa: BLE001
        return _sheet_failure(e, title)
    lines.append("حساب الخدمة بيقدر يكتب: كتبنا عنوان عمود رابط الصورة نفسه فوق حاله، وما تغيّر شي.")
    lines.append(outbox)
    if suspicious:
        return _tab_warning(lines, title, outbox_ok=outbox_ok)
    if dead:
        return _outcome(WARN, " ".join(lines), "في روابط ما انكتبت بالشيت نهائياً: أعد اعتماد هالمنتجات من شاشة "
                        "المراجعة.", "sheet_outbox_dead", tab=title, outbox_ok=outbox_ok)
    return _outcome(OK, " ".join(lines), "", "", tab=title, outbox_ok=outbox_ok)


# ---------------------------------------------------------------------------
# التشغيل
# ---------------------------------------------------------------------------

STEPS = {"download": step_download, "process": step_process, "upload": step_upload, "sheet": step_sheet}


def _run_active():
    """هل في تشغيل للعامل هلق (قفل temp/pipeline.lock حي، بنفس قاعدة main.lock_verdict)؟ لا يغيّر شيئاً."""
    try:
        import main as automation
        path = automation.LOCK_FILE
        path = path if os.path.isabs(path) else os.path.join(PROJECT_DIR, path)
        lock = automation.read_lock(path)
        if lock is None:
            return False
        if lock.get("kind") == "starting":
            return time.time() - float(lock.get("mtime") or 0) < 300
        return not automation.lock_verdict(lock)["stale"]
    except Exception as e:  # noqa: BLE001
        logger.warning("publish_check: could not read the worker lock: %s", e)
        return False


def _run_step(key, ctx, timeout_s):
    """تشغيل خطوة بمهلتها في خيط daemon. يعيد (outcome، ms)."""
    box = {}

    def target():
        try:
            box["outcome"] = STEPS[key](ctx)
        except Exception as e:  # noqa: BLE001 - خطوة انهارت تفشل وحدها
            logger.exception("publish_check: step %s crashed", key)
            box["outcome"] = _outcome(FAIL, "صار خطأ غير متوقع بهالخطوة.", "التفاصيل بسجل الأتمتة تحت؛ أعد الفحص.",
                                      f"step_crashed:{type(e).__name__}")

    started = time.monotonic()
    thread = threading.Thread(target=target, name=f"publish-check-{key}", daemon=True)
    thread.start()
    thread.join(timeout_s)
    ms = int((time.monotonic() - started) * 1000)
    if "outcome" not in box:
        return _outcome(FAIL, f"الخطوة أخدت أكتر من {timeout_s:g} ثانية فوقفنا نستناها.", TIMEOUT_ACTIONS[key],
                        "step_timeout"), ms
    return box["outcome"], ms


def _summary(steps):
    failed = [s for s in steps if s["status"] == FAIL]
    if failed:
        first = failed[0]
        text = f"النشر واقف عند «{first['title_ar']}»: {first['action_ar'] or first['detail_ar']}"
        if len(failed) > 1:
            others = "، ".join(f"«{s['title_ar']}»" for s in failed[1:])
            text += f" وكمان ما زبطت: {others}."
        return FAIL, text
    warned = [s for s in steps if s["status"] in (WARN, SKIPPED)]
    if warned:
        first = warned[0]
        return WARN, f"النشر لازم يمشي، بس في ملاحظة عند «{first['title_ar']}»: {first['action_ar'] or first['detail_ar']}"
    if any(s.get("code") == "bg_skipped" for s in steps):
        return OK, ("النشر شغّال بدون عزل الخلفية: نزّلنا الصورة، وحطيناها متل ما هي على لوحة بيضا (عزل الخلفية متوقف "
                    "بالإعدادات)، ورفعناها على Cloudinary ومسحناها، وحساب الخدمة بيقدر يكتب بالشيت.")
    return OK, ("النشر شغّال: نزّلنا الصورة، وعزلنا خلفيتها، ورفعناها على Cloudinary ومسحناها، وحساب الخدمة "
                "بيقدر يكتب بالشيت.")


def _iso(moment):
    return moment.isoformat(timespec="seconds")


def run_publish_check(timeouts=None, result_path=LAST_RESULT_PATH, save=True):
    """
    بروفة النشر كاملة. تعيد {ok, overall (ok | warn | fail), started_at, finished_at, duration_ms, steps: [{key,
    status: ok | warn | fail | skipped, ms, title_ar, detail_ar, action_ar, code}], summary_ar, failed_step, sample,
    sheet_tab, run_active, notes}، وتحفظها في result_path (save=True). لا ترفع استثناءات.
    """
    limits = dict(STEP_TIMEOUTS, **(timeouts or {}))
    started = datetime.now(timezone.utc)
    clock = time.monotonic()
    run_active = _run_active()
    ctx, steps, extras = {}, [], {}
    try:
        for key in STEP_ORDER:
            step = {"key": key, "title_ar": STEP_TITLES[key]}
            need = STEP_NEEDS[key]
            if need and ctx.get(need[0]) is None:
                step.update(status=SKIPPED, ms=0, code="", action_ar="",
                            detail_ar=f"ما انفحصت لأنو خطوة «{STEP_TITLES[need[1]]}» ما نجحت.")
                steps.append(step)
                continue
            outcome, ms = _run_step(key, ctx, limits[key])
            data = outcome.pop("data", {}) or {}
            ctx.update(data)
            extras.update({k: data[k] for k in ("sample", "tab") if k in data})
            step.update(outcome, ms=ms)
            steps.append(step)
    finally:
        image_processor.cleanup_processed_image(ctx.get("canvas"))
    overall, summary = _summary(steps)
    failed = next((s["key"] for s in steps if s["status"] == FAIL), None)
    notes = [RUN_ACTIVE_NOTE] if run_active else []
    finished = datetime.now(timezone.utc)
    result = _scrub({
        "ok": overall == OK, "overall": overall, "started_at": _iso(started), "finished_at": _iso(finished),
        "duration_ms": int((time.monotonic() - clock) * 1000), "steps": steps, "summary_ar": summary,
        "failed_step": failed, "sample": extras.get("sample"), "sheet_tab": extras.get("tab"),
        "run_active": run_active, "notes": notes, "cost_note": COST_NOTE,
    })
    if save:
        verify_cloud_services.save_last_result(result, result_path)
    return result


def read_last_result(path=LAST_RESULT_PATH):
    """آخر نتيجة محفوظة أو None."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get("steps"), list) else None
