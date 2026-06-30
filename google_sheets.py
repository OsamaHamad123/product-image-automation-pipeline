# google_sheets.py
# موديول للتعامل مع Google Sheets

import gspread
import config

def get_sheets_client():
    """
    الاتصال بـ Google Sheets API باستخدام ملف الاعتمادات من الإعدادات.
    """
    try:
        gc = gspread.service_account(filename=config.CREDENTIALS_FILE)
        return gc
    except Exception as e:
        print(f"❌ فشل الاتصال بـ Google Sheets API: {e}")
        return None

def open_worksheet(client, sheet_name_or_url, worksheet_index=0):
    """
    فتح ورقة العمل المحددة بالاسم أو الرابط.
    """
    try:
        if sheet_name_or_url.startswith("https://"):
            sh = client.open_by_url(sheet_name_or_url)
        else:
            sh = client.open(sheet_name_or_url)
        return sh.get_worksheet(worksheet_index)
    except Exception as e:
        print(f"❌ فشل فتح جدول البيانات '{sheet_name_or_url}': {e}")
        return None

def get_product_columns_indices(headers):
    """
    تحديد فهارس (Indices) الأعمدة المهمة ديناميكياً من خلال عناوين الجدول.
    """
    # البحث عن الأعمدة باللغة الإنجليزية أو العربية
    name_indices = [i for i, h in enumerate(headers) if h.lower() in ["productname", "product name", "اسم المنتج"]]
    brand_indices = [i for i, h in enumerate(headers) if h.lower() in ["brand", "البراند", "العلامة التجارية"]]
    link_indices = [i for i, h in enumerate(headers) if h.lower() in ["drive image link", "image link", "رابط الصورة"]]

    # الفهارس الافتراضية إذا لم يتم العثور عليها بالأسماء
    # حسب لقطة الشاشة:ProductName في العمود الثالث (C) أي index 2، والـ Brand في العمود الخامس (E) أي index 4
    name_idx = name_indices[0] if name_indices else 2
    brand_idx = brand_indices[0] if brand_indices else 4
    link_idx = link_indices[0] if link_indices else -1 # -1 يعني غير موجود وسنقوم بإنشائه

    return name_idx, brand_idx, link_idx

def get_products(worksheet):
    """
    جلب جميع المنتجات من الشيت مع تحديد رقم الصف لكل منتج لتسهيل التحديث لاحقاً.
    """
    try:
        rows = worksheet.get_all_values()
        if not rows or len(rows) <= 1:
            print("⚠️ لا توجد بيانات في الشيت (أو يوجد صف العناوين فقط).")
            return [], -1

        headers = rows[0]
        name_idx, brand_idx, link_idx = get_product_columns_indices(headers)

        # إذا لم يكن عمود الرابط موجوداً، نقوم بإنشائه في نهاية الجدول
        if link_idx == -1:
            link_idx = len(headers)
            new_column_name = "Drive Image Link"
            worksheet.update_cell(1, link_idx + 1, new_column_name)
            print(f"ℹ️ تم إنشاء عمود جديد لحفظ روابط الصور باسم '{new_column_name}' في العمود رقم {link_idx + 1}")

        products = []
        # تبدأ الحلقة من الصف الثاني (الفهرس 1) لأن الصف الأول يحتوي على العناوين
        for idx, row in enumerate(rows[1:], start=2):
            product_name = row[name_idx].strip() if name_idx < len(row) else ""
            brand = row[brand_idx].strip() if brand_idx < len(row) else ""
            
            # قراءة الرابط الحالي إذا كان العمود موجوداً وبه قيمة
            existing_link = row[link_idx].strip() if link_idx < len(row) else ""

            # نقوم فقط بمعالجة الصفوف التي تحتوي على اسم منتج على الأقل
            if product_name:
                products.append({
                    "row_number": idx,
                    "product_name": product_name,
                    "brand": brand,
                    "existing_image_link": existing_link,
                    "search_query": f"{product_name} {brand}".strip()
                })
        
        return products, link_idx

    except Exception as e:
        print(f"❌ حدث خطأ أثناء قراءة المنتجات من الشيت: {e}")
        return [], -1

def update_image_link(worksheet, row_number, link_column_index, image_link):
    """
    تحديث خلية رابط الصورة لصف منتج معين.
    """
    try:
        # gspread يعتمد على ترقيم 1-indexed للأعمدة والصفوف
        worksheet.update_cell(row_number, link_column_index + 1, image_link)
        print(f"✅ تم تحديث الرابط في الصف {row_number} بنجاح.")
        return True
    except Exception as e:
        print(f"❌ فشل تحديث الرابط في الصف {row_number}: {e}")
        return False
