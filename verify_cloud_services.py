# verify_cloud_services.py
# سكربت للتحقق من الاتصال بالخدمات السحابية الأساسية وتأكيد صحة الاعتمادات والاشتراكات

import os
import sys
import requests
import json

# تحميل الإعدادات من .env
import config

def print_separator(title):
    print("\n" + "=" * 50)
    print(f"🔍 {title}")
    print("=" * 50)

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
        print("⚠️ لم يتم تعيين SERPER_API_KEY: سيُستخدم Bing كبديل، ونتائجه تذهب للمراجعة دائماً ولا تُنشر تلقائياً.")
        return False
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
        print("⚠️ Serper رد بنجاح لكن بدون صور: تحقق من الحساب أو جرّب لاحقاً.")
        return False
    if response.status_code in (401, 403):
        print(f"❌ مفتاح SERPER_API_KEY غير صالح (Error {response.status_code}).")
    elif response.status_code == 429:
        print("❌ انتهى رصيد Serper أو تم تجاوز الحد (429): اشحن الرصيد من لوحة serper.dev.")
    else:
        print(f"❌ استجابة غير متوقعة من Serper (كود {response.status_code}).")
    return False


def verify_photoroom():
    print_separator("فحص الاتصال بـ PhotoRoom Cloud API (إزالة الخلفية)")
    api_key = config.PHOTOROOM_API_KEY
    if not api_key:
        print("❌ خطأ: لم يتم تعيين مفتاح PHOTOROOM_API_KEY في ملف .env")
        return False
        
    proxies = {"http": config.PROXY_URL, "https": config.PROXY_URL} if config.PROXY_URL else None
    
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
    
    try:
        print(f"🔄 محاولة إرسال طلب بحث تجريبي لـ Google Search ({key[:8]}...)...")
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
        print(f"🔄 محاولة إرسال طلب فحص اتصال عبر البروكسي... ({proxy_url[:15]}...)")
        proxies = {"http": proxy_url, "https": proxy_url}
        # We fetch a Yandex images text query to verify it works
        url = "https://yandex.com/images/search?text=Nellara"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        response = requests.get(url, headers=headers, proxies=proxies, timeout=10)
        if response.status_code == 200:
            print("✅ نجح الاتصال بمحرك Yandex عبر البروكسي والخدمة جاهزة ومصرح لها.")
            return True
        else:
            print(f"❌ فشل الاتصال عبر البروكسي: كود الاستجابة {response.status_code}")
            return False
    except Exception as e:
        print(f"❌ حدث خطأ أثناء فحص اتصال البروكسي: {e}")
        return False

if __name__ == "__main__":
    if "--json" in sys.argv:
        import io
        # كتم المخرجات النصية المطبوعة والتقاطها
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        
        # إجراء الفحص
        sheets_ok = verify_google_sheets()
        cloudinary_ok = verify_cloudinary()
        gemini_ok = verify_gemini()
        photoroom_ok = verify_photoroom()
        serper_ok = verify_serper()
        search_ok = verify_google_search()
        proxy_status = verify_proxy()
        
        # استرجاع النصوص التي طبعت في الخلفية كلوغات تفصيلية لكل خدمة
        captured_logs = sys.stdout.getvalue()
        sys.stdout = old_stdout
        
        # بناء هيكل البيانات الموحد لـ JSON
        results = {
            "status": "success",
            "all_ok": bool(sheets_ok and cloudinary_ok and gemini_ok and photoroom_ok),
            "services": {
                "google_sheets": {
                    "name": "Google Sheets API",
                    "status": "online" if sheets_ok else "offline",
                    "is_critical": True
                },
                "cloudinary": {
                    "name": "Cloudinary CDN",
                    "status": "online" if cloudinary_ok else "offline",
                    "is_critical": True
                },
                "gemini": {
                    "name": "Google Gemini API",
                    "status": "online" if gemini_ok else "offline",
                    "is_critical": True
                },
                "photoroom": {
                    "name": "PhotoRoom API",
                    "status": "online" if photoroom_ok else "offline",
                    "is_critical": True
                },
                "serper": {
                    "name": "Serper (Google Images)",
                    "status": "online" if serper_ok else "offline",
                    "is_critical": False
                },
                "google_search": {
                    "name": "Google Custom Search",
                    "status": "online" if search_ok else ("disabled" if search_ok is None else "offline"),
                    "is_critical": False
                },
                "proxy": {
                    "name": "Proxy Server",
                    "status": "online" if proxy_status is True else ("disabled" if proxy_status is None else "offline"),
                    "is_critical": False
                }
            },
            "raw_logs": captured_logs
        }
        print(json.dumps(results, ensure_ascii=False))
        sys.exit(0 if results["all_ok"] else 1)

    print("=" * 60)
    print("🚦 فحص جاهزية الخدمات السحابية لمشروع أتمتة المنتجات")
    print("=" * 60)
    
    results = {
        "Google Sheets API": verify_google_sheets(),
        "Cloudinary CDN": verify_cloudinary(),
        "Google Gemini API": verify_gemini(),
        "PhotoRoom API": verify_photoroom(),
        "Serper (Google Images)": verify_serper(),
        "Google Custom Search": verify_google_search(),
        "Proxy Connection": verify_proxy()
    }
    
    print("\n" + "=" * 60)
    print("📊 ملخص نتائج الفحص النهائي:")
    print("=" * 60)
    all_ok = True
    for service, status in results.items():
        if status or status is None:
            if status is None:
                status_str = "ℹ️ غير مفعّل"
            else:
                status_str = "✅ يعمل بنجاح"
        else:
            status_str = "❌ فشل الاتصال / غير مهيأ"
            # فقط الخدمات الحيوية تؤدي لتعطيل التشغيل بالكامل
            if service not in ["Serper (Google Images)", "Google Custom Search", "Proxy Connection"]:
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

