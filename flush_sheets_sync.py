# flush_sheets_sync.py
"""
Utility script to immediately flush pending and failed Google Sheets updates.
"""

import sys
import os

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import config
import google_sheets

def flush():
    print("🚀 Initializing Google Sheets client and flushing updates...")
    client = google_sheets.get_sheets_client()
    if not client:
        print("❌ Could not obtain Google Sheets client.")
        return

    worksheet = google_sheets.open_worksheet(client, config.SPREADSHEET_NAME_OR_URL)
    if not worksheet:
        print("❌ Could not open worksheet.")
        return

    queue = google_sheets.SQLiteTransactionQueue()
    worker = google_sheets.GoogleSheetsBatchWorker(queue, config.CREDENTIALS_FILE, config.SPREADSHEET_NAME_OR_URL)
    
    worker._synchronize_pending_records(worksheet)
    print("✨ Flush process completed.")

if __name__ == "__main__":
    flush()
