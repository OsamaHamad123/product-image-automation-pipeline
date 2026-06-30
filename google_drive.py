# google_drive.py
# موديول للتعامل مع Google Drive API ورفع الصور

import os
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.oauth2 import service_account
import config

def get_drive_service():
    """
    الاتصال بـ Google Drive API باستخدام ملف الاعتمادات.
    """
    try:
        scopes = ['https://www.googleapis.com/auth/drive']
        creds = service_account.Credentials.from_service_account_file(
            config.CREDENTIALS_FILE, scopes=scopes)
        service = build('drive', 'v3', credentials=creds)
        return service
    except Exception as e:
        print(f"❌ فشل الاتصال بـ Google Drive API: {e}")
        return None

def set_file_public(service, file_id):
    """
    جعل الملف المرفوع عاماً (Public) بحيث يمكن لأي شخص لديه الرابط استعراضه.
    """
    try:
        permission = {
            'type': 'anyone',
            'role': 'reader'
        }
        service.permissions().create(
            fileId=file_id,
            body=permission,
            fields='id'
        ).execute()
        return True
    except Exception as e:
        print(f"⚠️ فشل تغيير صلاحية الملف ليصبح عاماً: {e}")
        return False

def upload_product_image(service, local_path, product_name, brand):
    """
    رفع صورة المنتج إلى Google Drive مع تجنب التكرار (تحديث الملف إذا كان موجوداً مسبقاً بنفس الاسم).
    """
    if not os.path.exists(local_path):
        print(f"❌ خطأ: ملف الصورة المحلي غير موجود في المسار: {local_path}")
        return None

    # تسمية الملف في Drive حسب الطلب: ProductName+Brand
    # إزالة أي رموز قد تسبب مشاكل وتعديل الامتداد لـ .png
    clean_product_name = product_name.replace("/", "-").replace("\\", "-")
    clean_brand = brand.replace("/", "-").replace("\\", "-")
    file_name = f"{clean_product_name}+{clean_brand}.png"
    
    folder_id = config.DRIVE_FOLDER_ID.strip()
    
    try:
        # 1. البحث عما إذا كان هناك ملف بنفس الاسم موجود مسبقاً لتجنب التكرار
        query = f"name = '{file_name}' and trashed = false"
        if folder_id:
            query += f" and '{folder_id}' in parents"
            
        search_result = service.files().list(q=query, fields="files(id)").execute()
        existing_files = search_result.get('files', [])
        
        media = MediaFileUpload(local_path, mimetype='image/png', resumable=True)
        
        if existing_files:
            # تحديث الملف الحالي
            file_id = existing_files[0]['id']
            print(f"🔄 تم العثور على ملف سابق بنفس الاسم في Drive (معرف الملف: {file_id}). جاري تحديثه...")
            
            file = service.files().update(
                fileId=file_id,
                media_body=media,
                fields='id, webViewLink'
            ).execute()
        else:
            # رفع ملف جديد
            print(f"📤 جاري رفع صورة جديدة إلى Google Drive باسم: '{file_name}'...")
            file_metadata = {'name': file_name}
            if folder_id:
                file_metadata['parents'] = [folder_id]
                
            file = service.files().create(
                body=file_metadata,
                media_body=media,
                fields='id, webViewLink'
            ).execute()
            
            file_id = file.get('id')
            
        # 2. جعل الملف عاماً ليتمكن الآخرون (والشيت) من الوصول للرابط
        set_file_public(service, file_id)
        
        # 3. جلب رابط المشاهدة
        # نستخدم webViewLink لرابط العرض أو يمكن استخدام رابط التنزيل المباشر
        web_view_link = file.get('webViewLink')
        print(f"✅ تم الرفع بنجاح! رابط الصورة في Drive: {web_view_link}")
        return web_view_link
        
    except Exception as e:
        print(f"❌ حدث خطأ أثناء الرفع إلى Google Drive: {e}")
        return None
