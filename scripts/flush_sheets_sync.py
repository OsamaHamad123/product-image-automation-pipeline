# flush_sheets_sync.py
"""
Utility script to immediately flush pending and failed Google Sheets updates.

Run by hand it flushes at once. On the Ubuntu server laqta-outbox-flush.timer runs it every 2 minutes (between runs
nothing else empties the outbox when Redis is not used); there it skips Google completely while no write is due
(no quota spent on an idle server).
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(ROOT)

import config
import google_sheets

def flush():
    print("🚀 Initializing Google Sheets client and flushing updates...")
    client = google_sheets.get_sheets_client()
    if not client:
        print("❌ Could not obtain Google Sheets client.")
        return

    try:
        worksheet = google_sheets.open_worksheet(client, config.SPREADSHEET_NAME_OR_URL)
        if not worksheet:
            print("❌ Could not open worksheet.")
            return

        queue = google_sheets.SQLiteTransactionQueue()
        worker = google_sheets.GoogleSheetsBatchWorker(queue, config.CREDENTIALS_FILE, config.SPREADSHEET_NAME_OR_URL)

        worker._synchronize_pending_records(worksheet)
    except google_sheets.SheetTransientError as e:
        # Not a link or sharing problem: Google refused for now even after retrying. Pending writes stay queued.
        print(f"⏳ Google Sheets غير متاح مؤقتاً؛ الكتابات المعلقة تبقى في الطابور. أعد المحاولة بعد دقائق. ({e})")
        return
    print("✨ Flush process completed.")


def main():
    """مرة واحدة: فرّغ إن كان في كتابة مستحقة. رمز الخروج 0 حتى مع انقطاع Google أو قاعدة البيانات."""
    try:
        due = google_sheets.outbox_due_count()
    except Exception as e:  # noqa: BLE001 - قاعدة البيانات لا ترد: صفحة الصحة تقول ذلك، ولا داعي لفشل الوحدة كل دقيقتين
        print(f"⏳ قاعدة البيانات لا ترد ({type(e).__name__}): أعد المحاولة بعد قليل.")
        return 0
    if due:
        flush()
    else:
        print("✅ لا توجد كتابات مستحقة؛ لم نفتح الشيت.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
