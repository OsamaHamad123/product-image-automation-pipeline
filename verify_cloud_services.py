# verify_cloud_services.py
# سكربت للتحقق من الاتصال بالخدمات السحابية الأساسية وتأكيد صحة الاعتمادات والاشتراكات

import io
import logging
import os
import sys
import threading
import time
import requests
import json
from datetime import datetime, timezone

# تحميل الإعدادات من .env
import config
import builtins
import re
from urllib.parse import urlsplit

# كل ما يطبعه هذا السكربت يمر على _redact: رسائل الأخطاء قد تحتوي الرابط كاملاً (مع ?key=) أو قيمة مفتاح،
# والمخرجات تُعرض في صفحة التشخيص وتُحفظ في ملفات.
_SECRET_SETTINGS = ("GEMINI_API_KEY", "SERPER_API_KEY", "GOOGLE_SEARCH_API_KEYS", "GOOGLE_SEARCH_API_KEY",
                    "CLOUDINARY_API_KEY", "CLOUDINARY_API_SECRET", "PHOTOROOM_API_KEY", "REMOVE_BG_API_KEY",
                    "PROXY_URL", "ANTHROPIC_API_KEY", "SERPAPI_API_KEY")


def _secret_values():
    values = []
    for name in _SECRET_SETTINGS:
        raw = getattr(config, name, "") or os.getenv(name, "")
        for item in (raw if isinstance(raw, (list, tuple)) else str(raw).split(",")):
            item = str(item).strip()
            if len(item) >= 6:
                values.append(item)
    proxy = urlsplit(str(getattr(config, "PROXY_URL", "") or ""))
    if proxy.username:
        values.append(proxy.username)
    if proxy.password:
        values.append(proxy.password)
    return sorted(set(values), key=len, reverse=True)


def _redact(text):
    text = str(text)
    for secret in _secret_values():
        text = text.replace(secret, "[REDACTED]")
    return re.sub(r"(?i)((?:api_?)?key=)[^&\s'\"]+", r"\1[REDACTED]", text)


# مخرجات كل فحص تُلتقط في مخزن خاص بخيطه (الفحوص تعمل بالتوازي في وضع --json)
_capture = threading.local()


def print(*args, **kwargs):  # noqa: A001 - every message of this script is redacted
    buffer = getattr(_capture, "buffer", None)
    if buffer is not None and "file" not in kwargs:
        kwargs["file"] = buffer
    builtins.print(*(_redact(a) for a in args), **kwargs)


def _proxy_host(proxy_url):
    parts = urlsplit(proxy_url)
    return f"{parts.scheme}://{parts.hostname or '?'}{f':{parts.port}' if parts.port else ''}"

def print_separator(title):
    print("\n" + "=" * 50)
    print(f"🔍 {title}")
    print("=" * 50)


def key_origin(name, value):
    """سطر بيقول أي مفتاح انجرّب: من صفحة الإعدادات أو من .env، وآخر 4 حروف منه (مش سر، وبتكفي للمقارنة)."""
    value = str(value or "").strip()
    if not value:
        return ""
    where = "صفحة الإعدادات" if getattr(config, "KEY_SOURCES", {}).get(name) == "settings" else "ملف .env"
    tail = f"، آخره …{value[-4:]}" if len(value) >= 12 else ""
    return f"🔑 المفتاح المستعمل من {where}{tail}."

def verify_google_sheets():
    print_separator("فحص الاتصال بـ Google Sheets API")
    creds_file = config.CREDENTIALS_FILE
    if not os.path.exists(creds_file):
        print(f"❌ خطأ: ملف الاعتمادات (credentials.json) لم يتم العثور عليه في: {creds_file}")
        print("💡 الحل: تأكد من وضع ملف الاعتمادات في المجلد الرئيسي للمشروع.")
        return False
        
    try:
        import google_sheets
        client = google_sheets.get_sheets_client()
        if not client:
            print("❌ فشل الاتصال: تعذر إنشاء عميل Google Sheets API.")
            return False
            
        print("✅ نجح الاتصال الأولي بالخدمة السحابية لـ Google Sheets.")
        
        # محاولة فتح جدول البيانات
        sheet_name = config.SPREADSHEET_NAME_OR_URL
        print(f"🔄 محاولة فتح جدول البيانات: '{sheet_name}'...")
        worksheet = google_sheets.open_worksheet(client, sheet_name)
        if worksheet:
            print(f"✅ نجح العثور على ورقة العمل وفتحها بنجاح!")
            return True
        else:
            print(f"❌ فشل فتح ورقة العمل: تأكد من مشاركة الشيت مع البريد الإلكتروني للـ Service Account.")
            print(f"💡 البريد المستهدف: outomation-agent@boulevard-a50a0.iam.gserviceaccount.com")
            return False
    except Exception as e:
        print(f"❌ حدث خطأ أثناء فحص Google Sheets: {e}")
        return False

def verify_cloudinary():
    print_separator("فحص الاتصال بـ Cloudinary CDN")
    if not config.CLOUDINARY_CLOUD_NAME or not config.CLOUDINARY_API_KEY or not config.CLOUDINARY_API_SECRET:
        print("❌ خطأ: لم يتم ضبط إعدادات Cloudinary في ملف .env")
        print("💡 الحل: تحقق من قيم CLOUDINARY_CLOUD_NAME و CLOUDINARY_API_KEY و CLOUDINARY_API_SECRET.")
        return False
        
    try:
        import cloudinary
        import cloudinary.api
        
        # تهيئة الإعدادات
        cloudinary.config(
            cloud_name = config.CLOUDINARY_CLOUD_NAME,
            api_key = config.CLOUDINARY_API_KEY,
            api_secret = config.CLOUDINARY_API_SECRET,
            secure = True
        )
        
        print(key_origin("CLOUDINARY_API_KEY", config.CLOUDINARY_API_KEY))
        print(f"🔄 محاولة إرسال اختبار Ping إلى Cloudinary ({config.CLOUDINARY_CLOUD_NAME})...")
        res = cloudinary.api.ping()
        if res.get("status") == "ok":
            print("✅ نجح اختبار Ping لـ Cloudinary بنجاح والخدمة جاهزة للعمل.")
            return True
        else:
            print(f"❌ استجابة غير متوقعة من Cloudinary: {res}")
            return False
    except Exception as e:
        print(f"❌ حدث خطأ أثناء فحص Cloudinary: {e}")
        print("💡 الحل: تأكد من صحة بيانات API Key و API Secret في ملف .env")
        return False

def verify_gemini():
    print_separator("فحص الاتصال بـ Google Gemini API")
    api_key = config.GEMINI_API_KEY
    if not api_key:
        print("❌ خطأ: لم يتم تعيين مفتاح GEMINI_API_KEY في ملف .env")
        print("💡 بدون Gemini لا يُقرأ ملصق المنتج، فكل النتائج تذهب للمراجعة البشرية ولا يُنشر شيء تلقائياً.")
        return False

    # نفس الفحص الذي يجريه العامل عند البدء: models.get بالمفتاح في الترويسة (لا يظهر في الروابط أو السجلات)
    from catalog_match.verify import check_model_available
    model = getattr(config, "GEMINI_MODEL", "gemini-3.1-flash-lite")
    print(key_origin("GEMINI_API_KEY", api_key))
    print(f"🔄 التحقق من توفر الموديل {model} ...")
    check = check_model_available(api_key=api_key, model=model, timeout=15)
    if check.ok:
        print(f"✅ مفتاح Gemini صالح والموديل {check.model} متاح.")
        return True
    hints = {
        "model_not_found": "اسم الموديل غير موجود أو متقاعد: اختر موديلاً متاحاً من صفحة الإعدادات.",
        "invalid_request_or_key": "المفتاح غير صالح: أنشئ مفتاحاً جديداً من Google AI Studio.",
        "invalid_key": "المفتاح غير صالح: أنشئ مفتاحاً جديداً من Google AI Studio.",
        "permission_denied": "المفتاح لا يملك صلاحية Gemini API أو المشروع موقوف.",
        "quota": "تم استنفاد الحصة (429): فعّل الدفع (Paid tier) في Google AI Studio.",
        "timeout": "انتهت المهلة: تحقق من الإنترنت أو البروكسي.",
        "connection_error": "تعذر الاتصال: تحقق من الإنترنت أو البروكسي.",
    }
    print(f"❌ فشل فحص Gemini ({check.status}).")
    print(f"💡 {hints.get(check.status, 'راجع المفتاح والموديل في ملف .env أو صفحة الإعدادات.')}")
    return False


def verify_serper():
    print_separator("فحص الاتصال بـ Serper (Google Images) - مصدر الصور الأساسي")
    from catalog_match import settings as cm_settings
    api_key = cm_settings.serper_api_key()
    if not api_key:
        print("❌ لم يتم تعيين SERPER_API_KEY: سيُستخدم Bing كبديل، ونتائجه تذهب للمراجعة دائماً ولا تُنشر تلقائياً.")
        return False
    print(key_origin("SERPER_API_KEY", api_key))
    try:
        print("🔄 إرسال بحث صور تجريبي واحد (يستهلك رصيد استعلام واحد)...")
        response = requests.post(
            "https://google.serper.dev/images",
            headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
            json={"q": "Almarai Fresh Milk Full Fat 1L", "gl": "ae", "hl": "en", "num": 10},
            timeout=15,
        )
    except requests.RequestException as e:
        print(f"❌ تعذر الاتصال بـ Serper ({type(e).__name__}): تحقق من الإنترنت أو البروكسي.")
        return False
    if response.status_code == 200:
        try:
            images = response.json().get("images") or []
        except ValueError:
            images = []
        if images:
            print(f"✅ Serper يعمل: {len(images)} صورة للاستعلام التجريبي.")
            return True
        print("❌ Serper رد بنجاح لكن بدون صور: تحقق من الحساب أو جرّب لاحقاً.")
        return False
    try:
        reason = str((response.json() or {}).get("message") or "").strip()
    except (ValueError, AttributeError):
        reason = ""
    if response.status_code in (401, 403):
        print(f"❌ مفتاح SERPER_API_KEY غير صالح (Error {response.status_code}).")
    elif response.status_code == 429 or "credit" in reason.lower():
        print(f"❌ رصيد هالمفتاح خلص ({response.status_code}{f': {reason}' if reason else ''}): اشحن الرصيد من serper.dev، "
              "أو حط مفتاح فيه رصيد من صفحة الإعدادات.")
    else:
        print(f"❌ استجابة غير متوقعة من Serper (كود {response.status_code}{f': {reason}' if reason else ''}).")
    return False


def verify_photoroom():
    print_separator("فحص الاتصال بـ PhotoRoom Cloud API (إزالة الخلفية)")
    api_key = config.PHOTOROOM_API_KEY
    if not api_key:
        print("❌ خطأ: لم يتم تعيين مفتاح PHOTOROOM_API_KEY في ملف .env")
        return False
    print(key_origin("PHOTOROOM_API_KEY", api_key))
    # بدون بروكسي متل التشغيل الحقيقي (image_processor): بطء البروكسي كان يطلّع «ما بيرد» والمفتاح سليم
    proxies = None

    # 1. التحقق مما إذا كان مفتاح تجريبي Sandbox
    is_sandbox = api_key.strip().lower().startswith("sandbox_")
    if is_sandbox:
        print("⚠️ تنبيه: يتم استخدام مفتاح تجريبي (Sandbox Key).")
        print("💡 سيتم معالجة الصور للتجربة مجاناً ولكنها ستظهر بعلامة مائية (Watermark) ولن تستهلك رصيداً حقيقياً.")
        
    # 2. فحص رصيد الصور المتاحة عبر الـ API
    account_url = "https://image-api.photoroom.com/v2/account"
    headers = {"x-api-key": api_key}
    
    try:
        print("🔄 جاري التحقق من رصيد الحساب والاشتراك النشط لـ PhotoRoom...")
        acc_response = requests.get(account_url, headers=headers, proxies=proxies, timeout=10)
        
        if acc_response.status_code == 200:
            acc_data = acc_response.json()
            images_info = acc_data.get("images", {})
            available = images_info.get("available", 0)
            subscription = images_info.get("subscription", 0)
            
            if not is_sandbox and available <= 0:
                print(f"❌ خطأ: مفتاح PhotoRoom صحيح ومصادق عليه، ولكنه لا يحتوي على رصيد صور متاح (الرصيد المتاح: {available} من {subscription}).")
                print("💡 الحل: يرجى التأكد من نسخ المفتاح من مساحة العمل المدفوعة النشطة (Workspace/Space) وليس المساحة الافتراضية.")
                return False
                
            print(f"✅ نجح فحص مفتاح PhotoRoom بنجاح. رصيد الصور المتاحة: {available} صور (الاشتراك الكلي: {subscription}).")
            return True
        elif acc_response.status_code in [401, 403]:
            print(f"❌ فشل الاتصال بـ PhotoRoom: مفتاح الـ API غير صالح أو غير مصرح (كود {acc_response.status_code}).")
            return False
        elif acc_response.status_code == 402:
            print("❌ فشل الاتصال بـ PhotoRoom: كود الاستجابة 402 (انتهى اشتراك PhotoRoom أو نفد رصيد الصور المتاح تماماً).")
            return False
    except Exception as e:
        print(f"⚠️ تنبيه أثناء الاتصال بنقطة فحص رصيد PhotoRoom: {e}. محاولة استخدام الفحص الاحتياطي...")
        
    # 3. الفحص الاحتياطي (POST request) في حال فشل نقطة فحص الرصيد لأي سبب
    segment_url = "https://sdk.photoroom.com/v1/segment"
    try:
        print("🔄 محاولة إجراء فحص أولي بديل للمصادقة...")
        response = requests.post(segment_url, headers=headers, proxies=proxies, timeout=12)
        
        if response.status_code in [400, 415]:
            print("✅ نجح فحص مفتاح PhotoRoom بنجاح عبر الفحص الاحتياطي والمصادقة صالحة.")
            return True
        elif response.status_code in [401, 403]:
            print(f"❌ فشل الفحص الاحتياطي لـ PhotoRoom: كود {response.status_code} (المفتاح غير صالح).")
            return False
        elif response.status_code == 402:
            print("❌ فشل الفحص الاحتياطي لـ PhotoRoom: كود 402 (الاشتراك غير مدفوع أو الرصيد نفد).")
            return False
        else:
            print(f"⚠️ استجابة غير متوقعة من الفحص الاحتياطي لـ PhotoRoom (كود {response.status_code}): {response.text}")
            return False
    except Exception as e:
        print(f"❌ حدث خطأ أثناء الفحص الاحتياطي لـ PhotoRoom: {e}")
        return False

def verify_google_search():
    print_separator("فحص الاتصال بـ Google Custom Search API (اختياري، قديم)")
    keys = config.GOOGLE_SEARCH_API_KEYS
    cxs = config.GOOGLE_SEARCH_CX_LIST
    if not keys or not cxs:
        print("ℹ️ Google Custom Search غير مهيأ (اختياري وقديم: يتوقف بعد 2026-12-31؛ Serper هو المصدر الأساسي).")
        return None
        
    key = keys[0]
    cx = cxs[0]
    url = "https://www.googleapis.com/customsearch/v1"
    params = {
        "key": key,
        "cx": cx,
        "q": "Nellara Matta Rice",
        "searchType": "image",
        "num": 1,
        "gl": "ae"
    }
    
    print(key_origin("GOOGLE_SEARCH_API_KEY", key))
    try:
        print("🔄 محاولة إرسال طلب بحث تجريبي لـ Google Search...")
        response = requests.get(url, params=params, timeout=10)
        
        if response.status_code == 200:
            print("✅ نجح الاتصال بـ Google Custom Search API وحصة البحث متوفرة.")
            return True
        elif response.status_code == 429:
            print("❌ فشل الاتصال: تم استنفاد حصة البحث اليومية لـ Google Custom Search (Quota Exceeded - Error 429).")
            print("💡 الحل: سيقوم النظام بالتدوير على المفاتيح الأخرى أو استخدام محركات البحث البديلة.")
            return False
        elif response.status_code in [400, 403]:
            print(f"❌ فشل الاتصال: مفتاح البحث أو معرف CX غير صالح (Error {response.status_code}).")
            print(f"تفاصيل الخطأ: {response.text}")
            return False
        else:
            print(f"⚠️ استجابة غير متوقعة من Google Search (كود {response.status_code}): {response.text}")
            return False
    except Exception as e:
        print(f"❌ حدث خطأ أثناء فحص Google Search: {e}")
        return False

def verify_proxy():
    print_separator("فحص اتصال البروكسي السكني")
    proxy_url = config.PROXY_URL
    if not proxy_url:
        print("ℹ️ البروكسي السكني غير مفعّل (PROXY_URL فارغ).")
        return None
        
    try:
        print(f"🔄 محاولة إرسال طلب فحص اتصال عبر البروكسي ({_proxy_host(proxy_url)})...")
        proxies = {"http": proxy_url, "https": proxy_url}
        # البروكسي يُستخدم لبديل Bing عند غياب Serper، فنفحصه على نفس الموقع
        url = "https://www.bing.com/images/search?q=Almarai+Fresh+Milk"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        response = requests.get(url, headers=headers, proxies=proxies, timeout=10)
        if response.status_code == 200:
            print("✅ نجح الاتصال بـ Bing عبر البروكسي.")
            return True
        else:
            print(f"❌ فشل الاتصال عبر البروكسي: كود الاستجابة {response.status_code}")
            return False
    except Exception as e:
        print(f"❌ حدث خطأ أثناء فحص اتصال البروكسي: {e}")
        return False

# ---------------------------------------------------------------------------
# تشغيل كل الفحوص
# ---------------------------------------------------------------------------

# (المفتاح في JSON، الاسم المعروض، دالة الفحص، حرج؟). Serper مصدر الصور الأساسي: بدونه يُستخدم Bing ونتائجه
# لا تُنشر تلقائياً أبداً، فتعطله حرج مثل Gemini.
SERVICES = (
    ("google_sheets", "Google Sheets API", "verify_google_sheets", True),
    ("cloudinary", "Cloudinary CDN", "verify_cloudinary", True),
    ("gemini", "Google Gemini API", "verify_gemini", True),
    ("photoroom", "PhotoRoom API", "verify_photoroom", True),
    ("serper", "Serper (Google Images)", "verify_serper", True),
    ("google_search", "Google Custom Search", "verify_google_search", False),
    ("proxy", "Proxy Server", "verify_proxy", False),
)

# مهلة واحدة لكل الفحوص معاً في وضع --json: صفحة التشخيص تنتظر الرد، وخادم PHP المدمج يخدم طلباً واحداً في كل مرة
DEFAULT_DEADLINE_S = 30

# آخر نتيجة تعرضها صفحة التشخيص عند فتحها (الصفحة لا تشغل الفحص تلقائياً)
LAST_RESULT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "temp", "diagnostics_last.json")


class _ThreadLogHandler(logging.Handler):
    """ينسخ تحذيرات المكتبات (مثل سبب فشل Google Sheets) إلى تفاصيل الفحص الذي أطلقها."""

    def emit(self, record):
        buffer = getattr(_capture, "buffer", None)
        if buffer is not None:
            try:
                buffer.write(_redact(record.getMessage()) + "\n")
            except Exception:
                pass


def _service_status(ok):
    return "online" if ok is True else ("disabled" if ok is None else "offline")


def _run_one(key, fn_name, outcomes):
    _capture.buffer = io.StringIO()
    try:
        ok = globals()[fn_name]()
    except Exception as e:
        print(f"❌ خطأ غير متوقع أثناء الفحص ({type(e).__name__}): {e}")
        ok = False
    outcomes[key] = (ok, _capture.buffer.getvalue())


def run_checks(deadline_s=DEFAULT_DEADLINE_S):
    """
    تشغيل كل الفحوص بالتوازي بمهلة إجمالية واحدة، وإرجاع النتيجة التي تعرضها صفحة التشخيص.
    details لكل خدمة = ما طبعه فحصها فقط. الفحص الذي لم يرد خلال المهلة يُعد فاشلاً ولا يُنتظر (خيوط daemon).
    """
    started = time.monotonic()
    outcomes = {}
    handler = _ThreadLogHandler(level=logging.WARNING)
    logging.getLogger().addHandler(handler)
    try:
        threads = []
        for key, _name, fn_name, _critical in SERVICES:
            thread = threading.Thread(target=_run_one, args=(key, fn_name, outcomes), name=f"verify-{key}",
                                      daemon=True)
            thread.start()
            threads.append(thread)
        for thread in threads:
            thread.join(max(0.0, deadline_s - (time.monotonic() - started)))
        finished = dict(outcomes)
    finally:
        logging.getLogger().removeHandler(handler)

    services = {}
    for key, name, _fn, critical in SERVICES:
        if key in finished:
            ok, details = finished[key]
        else:
            ok, details = False, (f"❌ لم يكتمل فحص {name} خلال {deadline_s:g} ثانية فتوقف انتظاره: "
                                  "تحقق من الإنترنت أو البروكسي ثم أعد الفحص.")
        services[key] = {"name": name, "status": _service_status(ok), "is_critical": critical,
                         "details": details.strip()}
    return {
        "status": "success",
        "all_ok": all(s["status"] == "online" for s in services.values() if s["is_critical"]),
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "duration_s": round(time.monotonic() - started, 1),
        "services": services,
        "raw_logs": "\n\n".join(s["details"] for s in services.values() if s["details"]),
    }


def save_last_result(result, path=LAST_RESULT_PATH):
    """حفظ آخر نتيجة (كتابة ذرية) لتعرضها صفحة التشخيص مع وقتها دون فحص جديد."""
    tmp = f"{path}.{os.getpid()}.tmp"
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False)
        os.replace(tmp, path)
        return True
    except OSError:
        return False


def _deadline_from_argv(argv):
    if "--deadline" in argv:
        try:
            return max(1.0, float(argv[argv.index("--deadline") + 1]))
        except (IndexError, ValueError):
            pass
    return DEFAULT_DEADLINE_S


def main_json(argv, out, result_path=LAST_RESULT_PATH):
    """وضع --json (صفحة التشخيص): وثيقة JSON واحدة على out، وتُحفظ النتيجة مع وقتها. يعيد رمز الخروج."""
    results = run_checks(_deadline_from_argv(argv))
    save_last_result(results, result_path)
    out.write(json.dumps(results, ensure_ascii=False) + "\n")
    out.flush()
    return 0 if results["all_ok"] else 1


if __name__ == "__main__":
    if "--json" in sys.argv:
        # ما تطبعه المكتبات لا يختلط بوثيقة JSON: stdout يبقى مكتوماً حتى الخروج
        real_stdout = sys.stdout
        sys.stdout = io.StringIO()
        code = main_json(sys.argv, real_stdout)
        sys.stderr.flush()
        # فحص عالق بعد المهلة لا يؤخر الخروج
        os._exit(code)

    print("=" * 60)
    print("🚦 فحص جاهزية الخدمات السحابية لمشروع أتمتة المنتجات")
    print("=" * 60)

    results = {name: globals()[fn_name]() for _key, name, fn_name, _critical in SERVICES}

    print("\n" + "=" * 60)
    print("📊 ملخص نتائج الفحص النهائي:")
    print("=" * 60)
    all_ok = True
    for _key, service, _fn, critical in SERVICES:
        status = results[service]
        if status or status is None:
            if status is None:
                status_str = "ℹ️ غير مفعّل"
            else:
                status_str = "✅ يعمل بنجاح"
        else:
            status_str = "❌ فشل الاتصال / غير مهيأ"
            # فقط الخدمات الحيوية تؤدي لتعطيل التشغيل بالكامل (Serper منها: مصدر الصور الأساسي)
            if critical:
                all_ok = False
        print(f"- {service:22}: {status_str}")
    print("=" * 60)

    if not all_ok:
        print("\n⚠️ تنبيه: تم اكتشاف مشاكل في إعدادات بعض الخدمات الحيوية!")
        print("قد لا تتمكن الأتمتة من العمل بشكل صحيح.")
        sys.exit(1)
    else:
        print("\n🎉 كافة الخدمات الحيوية تعمل ومستعدة للتشغيل بأمان!")
        sys.exit(0)
