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
        return False
        
    model = getattr(config, "GEMINI_MODEL", "gemini-3.5-flash")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    headers = {"Content-Type": "application/json"}
    
    payload = {
        "contents": [{
            "parts": [{"text": "Hello, respond with only the word: OK"}]
        }]
    }
    
    try:
        print(f"🔄 إرسال طلب اختبار خفيف إلى Gemini API ({model})...")
        response = requests.post(url, headers=headers, json=payload, timeout=15)
        
        if response.status_code == 200:
            data = response.json()
            reply = data['candidates'][0]['content']['parts'][0]['text'].strip()
            print(f"✅ نجح الاتصال بـ Gemini API. الرد المستلم: '{reply}'")
            return True
        elif response.status_code == 429:
            print("❌ فشل الاتصال: تم استنفاد حصة استخدام Gemini API (Quota Exceeded - Error 429).")
            print("💡 الحل: تحقق من حد الفوترة للـ Billing في حساب Google AI Studio الخاص بك.")
            return False
        elif response.status_code in [400, 403]:
            print(f"❌ فشل الاتصال: مفتاح GEMINI_API_KEY غير صالح أو الموديل غير مدعوم (Error {response.status_code}).")
            print(f"تفاصيل الخطأ: {response.text}")
            return False
        else:
            print(f"❌ فشل الاتصال بـ Gemini (كود {response.status_code})")
            print(f"تفاصيل الخطأ: {response.text}")
            return False
    except Exception as e:
        print(f"❌ حدث خطأ أثناء فحص Gemini: {e}")
        return False

def verify_photoroom():
    print_separator("فحص الاتصال بـ PhotoRoom Cloud API (إزالة الخلفية)")
    api_key = config.PHOTOROOM_API_KEY
    if not api_key:
        print("❌ خطأ: لم يتم تعيين مفتاح PHOTOROOM_API_KEY في ملف .env")
        return False
        
    url = "https://sdk.photoroom.com/v1/segment"
    headers = {"x-api-key": api_key}
    
    try:
        print("🔄 إرسال طلب فحص أولي لـ PhotoRoom (التحقق من حالة الاشتراك والمصادقة)...")
        response = requests.post(url, headers=headers, timeout=10)
        
        if response.status_code in [400, 415]:
            print("✅ نجح فحص مفتاح PhotoRoom بنجاح والخدمة معتمدة وجاهزة.")
            return True
        elif response.status_code in [401, 403]:
            print(f"❌ فشل الاتصال بـ PhotoRoom: كود الاستجابة {response.status_code} (مفتاح الـ API غير صالح أو غير مصرح).")
            print("💡 الحل: يرجى التحقق من صحة PHOTOROOM_API_KEY وتجديده في ملف .env")
            return False
        elif response.status_code == 402:
            print("❌ فشل الاتصال بـ PhotoRoom: كود الاستجابة 402 (انتهى اشتراك PhotoRoom أو نفد رصيد الحساب المتاح).")
            print("💡 الحل: يرجى شحن الرصيد أو تجديد خطة الاشتراك في لوحة تحكم PhotoRoom.")
            return False
        elif response.status_code == 429:
            print("❌ فشل الاتصال بـ PhotoRoom: كود الاستجابة 429 (تم تجاوز حد الطلبات المتزامنة أو استنفاد الحصة).")
            return False
        else:
            print(f"⚠️ استجابة غير متوقعة من PhotoRoom (كود {response.status_code}): {response.text}")
            return False
    except Exception as e:
        print(f"❌ حدث خطأ أثناء فحص PhotoRoom: {e}")
        return False

def verify_google_search():
    print_separator("فحص الاتصال بـ Google Custom Search API")
    keys = config.GOOGLE_SEARCH_API_KEYS
    cxs = config.GOOGLE_SEARCH_CX_LIST
    if not keys or not cxs:
        print("❌ خطأ: لم يتم تعيين مفاتيح GOOGLE_SEARCH_API_KEY أو معرفات GOOGLE_SEARCH_CX في ملف .env")
        return False
        
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
        search_ok = verify_google_search()
        
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
                "google_search": {
                    "name": "Google Custom Search",
                    "status": "online" if search_ok else "offline",
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
        "Google Custom Search": verify_google_search()
    }
    
    print("\n" + "=" * 60)
    print("📊 ملخص نتائج الفحص النهائي:")
    print("=" * 60)
    all_ok = True
    for service, status in results.items():
        if status:
            status_str = "✅ يعمل بنجاح"
        else:
            status_str = "❌ فشل الاتصال / غير مهيأ"
            # فقط الخدمات الحيوية تؤدي لتعطيل التشغيل بالكامل
            # Google Custom Search ليس حرجاً تماماً بوجود الـ Fallbacks
            if service != "Google Custom Search":
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

