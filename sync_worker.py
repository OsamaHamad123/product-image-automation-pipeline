# sync_worker.py
# عامل المزامنة الخلفي المؤجل لتحديث بيانات Google Sheets دفعة واحدة عبر Redis (Write-Behind Cache)

import os
import sys
import time
import json
import random
import redis
import traceback
from googleapiclient.errors import HttpError

# إضافة المجلد الحالي للمسار
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import config
import google_sheets

# الاتصال بـ Redis
r = redis.Redis(host=config.REDIS_HOST, port=config.REDIS_PORT, db=config.REDIS_DB, decode_responses=True)
DIRTY_SET_KEY = "writebehind:dirty_set"
CACHE_PREFIX = "product:data:"

def run_sync_cycle(worksheet):
    # 1. سحب المفاتيح المعدلة من Redis Set دون تكرار
    dirty_keys = r.spop(DIRTY_SET_KEY, count=100)
    if not dirty_keys:
        return
        
    print(f"🔄 [Sync Worker] Found {len(dirty_keys)} pending product updates to synchronize...")
    
    batch_data = []
    processed_keys = []
    
    for key in dirty_keys:
        cached_val = r.get(f"{CACHE_PREFIX}{key}")
        if not cached_val:
            continue
            
        try:
            payload = json.loads(cached_val)
            row_index = int(payload.get("row_index"))
            updates = payload.get("updates", {})
            
            # تحويل التحديثات إلى نطاقات خلايا A1
            for col_idx_str, val in updates.items():
                col_idx = int(col_idx_str)
                # gspread يعتمد على 1-indexed للأعمدة والصفوف
                import gspread
                a1_range = gspread.utils.rowcol_to_a1(row_index, col_idx + 1)
                batch_data.append({
                    "range": a1_range,
                    "values": [[str(val)]]
                })
                
            processed_keys.append(key)
        except Exception as pe:
            print(f"⚠️ [Sync Worker Error] Failed to parse payload for {key}: {pe}")
            
    if not batch_data:
        return
        
    print(f"🚀 [Sync Worker] Sending batch update of {len(batch_data)} cells to Google Sheets...")
    
    # محاولة تحديث الخلايا مع تفعيل التراجع الأسي في حال تجاوز الحصة
    retry = 0
    max_retries = 5
    backoff_base = 2
    max_backoff = 32.0
    
    while retry <= max_retries:
        try:
            worksheet.batch_update(batch_data, value_input_option="USER_ENTERED")
            print(f"✅ [Sync Worker] Successfully synchronized {len(processed_keys)} updates to Google Sheets.")
            
            # مسح مفاتيح الكاش من Redis بعد مزامنتها بنجاح
            for key in processed_keys:
                r.delete(f"{CACHE_PREFIX}{key}")
            break
        except Exception as err:
            # التحقق مما إذا كان الخطأ هو تجاوز معدل الطلبات (HTTP 429)
            is_rate_limit = False
            err_str = str(err)
            if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                is_rate_limit = True
                
            if is_rate_limit and retry < max_retries:
                sleep_time = min((backoff_base ** retry) + random.uniform(0.1, 1.0), max_backoff)
                print(f"⚠️ [Sync Worker API Rate Limit] Quota exceeded. Retrying {retry}/{max_retries} in {sleep_time:.2f} seconds...")
                time.sleep(sleep_time)
                retry += 1
            else:
                print(f"❌ [Sync Worker API Error] Failed to batch update: {err}")
                # إعادة إدراج المفاتيح لـ Set لضمان عدم ضياع التحديثات عند الفشل الكامل
                for key in processed_keys:
                    r.sadd(DIRTY_SET_KEY, key)
                raise err

def main():
    # التحقق من أن خادم Redis يعمل قبل بدء الطابور
    try:
        r.ping()
    except Exception:
        print("=" * 60)
        print("ℹ️  [Sync Worker] Redis is not running locally.")
        print("    Exiting sync worker since Write-Behind is not active.")
        print("    FastAPI and Laravel Dashboard will update Google Sheets directly.")
        print("=" * 60)
        return

    print("=" * 60)
    print("🚀 [Sync Worker] Redis Write-Behind Sheets Sync Worker is Active")
    print("=" * 60)
    
    sheets_client = None
    worksheet = None
    
    sync_interval = 15 # فحص الكاش كل 15 ثانية لمزامنة سريعة
    
    while True:
        try:
            if sheets_client is None or worksheet is None:
                sheets_client = google_sheets.get_sheets_client()
                if sheets_client:
                    worksheet = google_sheets.open_worksheet(sheets_client, config.SPREADSHEET_NAME_OR_URL)
                    
            if worksheet:
                run_sync_cycle(worksheet)
                
        except Exception as e:
            print(f"⚠️ [Sync Worker Daemon Error] {e}")
            traceback.print_exc()
            sheets_client = None
            worksheet = None
            
        time.sleep(sync_interval)

if __name__ == "__main__":
    main()
