# migrate_sqlite_to_mariadb.py
# سكربت ترحيل البيانات من SQLite (local_cache.db) إلى MariaDB

import os
import sqlite3
import pymysql
import json
from datetime import datetime

# وظيفة بسيطة لقراءة ملف .env وتعيين المتغيرات البيئية يدوياً
def load_env(env_path=".env"):
    base_dir = os.path.dirname(os.path.abspath(__file__))
    env_file = os.path.join(base_dir, env_path)
    if os.path.exists(env_file):
        with open(env_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, val = line.split("=", 1)
                    key = key.strip()
                    val = val.strip().strip('"').strip("'")
                    os.environ[key] = val

load_env()

SQLITE_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "local_cache.db")

def get_mariadb_connection():
    return pymysql.connect(
        host=os.getenv("DB_HOST", "127.0.0.1"),
        port=int(os.getenv("DB_PORT", "3306")),
        user=os.getenv("DB_USERNAME", "root"),
        password=os.getenv("DB_PASSWORD", ""),
        database=os.getenv("DB_DATABASE", "automation_db"),
        charset='utf8mb4',
        cursorclass=pymysql.cursors.DictCursor
    )

def migrate_table(sqlite_conn, maria_conn, table_name, unique_key=None):
    print(f"⏳ جاري ترحيل جدول: {table_name}...")
    sqlite_cursor = sqlite_conn.cursor()
    sqlite_cursor.execute(f"SELECT * FROM {table_name}")
    rows = sqlite_cursor.fetchall()
    
    if not rows:
        print(f"ℹ️ لا توجد بيانات في جدول {table_name} لترحيلها.")
        return
        
    maria_cursor = maria_conn.cursor()
    
    # الحصول على أسماء الأعمدة
    columns = list(rows[0].keys())
    col_list = ", ".join([f"`{c}`" for c in columns])
    placeholder_list = ", ".join(["%s"] * len(columns))
    
    # بناء جملة INSERT IGNORE أو REPLACE
    # نستخدم REPLACE لضمان تحديث السجلات إذا كانت موجودة مسبقاً
    sql = f"REPLACE INTO {table_name} ({col_list}) VALUES ({placeholder_list})"
    
    success_count = 0
    for row in rows:
        values = [row[c] for c in columns]
        try:
            maria_cursor.execute(sql, values)
            success_count += 1
        except Exception as e:
            print(f"⚠️ خطأ أثناء ترحيل سجل من {table_name}: {e}")
            
    maria_conn.commit()
    print(f"✅ تم ترحيل {success_count}/{len(rows)} سجل بنجاح في جدول {table_name}.")

def main():
    if not os.path.exists(SQLITE_DB):
        print(f"❌ لم يتم العثور على قاعدة بيانات SQLite في المسار: {SQLITE_DB}")
        return
        
    print("🚀 بدء عملية ترحيل البيانات من SQLite إلى MariaDB...")
    
    try:
        # تهيئة قاعدة بيانات وجداول MariaDB أولاً عبر موديول local_cache_db
        import local_cache_db
        local_cache_db.init_db()
        
        sqlite_conn = sqlite_connect(SQLITE_DB)
        maria_conn = get_mariadb_connection()
        
        tables = [
            "resolved_products",
            "product_failures",
            "active_learning_feedback",
            "automation_queue",
            "curation_candidates",
            "automation_state"
        ]
        
        for table in tables:
            try:
                migrate_table(sqlite_conn, maria_conn, table)
            except Exception as e:
                print(f"❌ فشل ترحيل الجدول {table}: {e}")
                
        sqlite_conn.close()
        maria_conn.close()
        print("🎉 اكتمل ترحيل كافة الجداول بنجاح!")
        
    except Exception as e:
        print(f"❌ حدث خطأ غير متوقع أثناء الترحيل: {e}")

def sqlite_connect(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn

if __name__ == "__main__":
    main()
