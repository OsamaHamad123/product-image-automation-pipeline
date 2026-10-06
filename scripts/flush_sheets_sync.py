# flush_sheets_sync.py
"""
Utility script to immediately flush pending and failed Google Sheets updates.

Run by hand it flushes at once. On the Ubuntu server laqta-outbox-flush.timer runs it every 2 minutes (between runs
nothing else empties the outbox when Redis is not used); there it also:
- skips Google completely while no write is due (no quota spent on an idle server);
- sends ONE Telegram message each time the count of DEAD writes (writes that never reached the sheet) goes up.
  The last count seen is kept in temp/outbox_dead_state.json; a lower count is recorded silently, so the next
  increase alerts again. The message holds counts only, no product names, no keys.
"""

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(ROOT)

import config
import google_sheets

DEAD_STATE_PATH = os.path.join(ROOT, "temp", "outbox_dead_state.json")

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


# ---------------------------------------------------------------------------
# تنبيه الكتابات الميتة (DEAD): مرة لكل زيادة
# ---------------------------------------------------------------------------

def _read_state(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return max(0, int(json.load(f).get("dead", 0)))
    except (OSError, ValueError, TypeError, AttributeError):
        return 0


def _write_state(path, dead):
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"dead": int(dead)}, f)
        os.replace(tmp, path)
        return True
    except OSError as e:
        print(f"⚠️ تعذرت كتابة {path}: {e}")
        return False


def dead_alert_text(dead, previous):
    """نص التنبيه: أرقام فقط (لا أسماء منتجات ولا مفاتيح)."""
    return (
        "🚨 <b>كتابات للشيت فشلت نهائياً (DEAD)</b>\n\n"
        f"🔢 <b>العدد الآن:</b> {int(dead)} (كان {int(previous)})\n\n"
        "💡 <i>معناها صورة انعتمدت بس رابطها ما وصل للشيت بعد كل المحاولات. "
        "افتح صفحة الصحة بلوحة التحكم، وتشغيلة جديدة بتعيد كتابة الرابط.</i>"
    )


def check_dead_alert(count=None, state_path=DEAD_STATE_PATH, send=None, configured=None):
    """
    يرسل تنبيهاً واحداً إذا زاد عدد الكتابات DEAD عن آخر عدد شوهد. يعيد True إذا أُرسل تنبيه.
    - أول مرة (لا ملف حالة) العدد السابق صفر.
    - عدد أقل: يُسجل بصمت (لتنبيه الزيادة التالية).
    - Telegram لم يقبل الرسالة: لا تُحدَّث الحالة فيعاد الإرسال في الجولة التالية.
    - Telegram غير مضبوط: تُحدَّث الحالة بلا إرسال.
    """
    if count is None:
        count = int(google_sheets.outbox_summary().get("dead", 0))
    count = max(0, int(count))
    previous = _read_state(state_path)
    if count <= previous:
        if count < previous:
            _write_state(state_path, count)
        return False
    if configured is None:
        configured = config.telegram_configured()
    if not configured:
        _write_state(state_path, count)
        return False
    send = send or config.send_telegram_alert
    if not send(dead_alert_text(count, previous)):
        print("⚠️ Telegram لم يقبل تنبيه الكتابات الميتة؛ نعيد المحاولة بالجولة القادمة.")
        return False
    _write_state(state_path, count)
    print(f"🔔 أُرسل تنبيه: الكتابات DEAD صارت {count} (كانت {previous}).")
    return True


def main():
    """مرة واحدة: فرّغ إن كان في كتابة مستحقة، ثم فحص تنبيه DEAD. رمز الخروج 0 حتى مع انقطاع Google أو قاعدة البيانات."""
    try:
        due = google_sheets.outbox_due_count()
    except Exception as e:  # noqa: BLE001 - قاعدة البيانات لا ترد: صفحة الصحة تقول ذلك، ولا داعي لفشل الوحدة كل دقيقتين
        print(f"⏳ قاعدة البيانات لا ترد ({type(e).__name__}): أعد المحاولة بعد قليل.")
        return 0
    try:
        if due:
            flush()
        else:
            print("✅ لا توجد كتابات مستحقة؛ لم نفتح الشيت.")
    finally:
        try:
            check_dead_alert()
        except Exception as e:  # noqa: BLE001 - التنبيه لا يُسقط التفريغ
            print(f"⚠️ تعذر فحص الكتابات الميتة ({type(e).__name__}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
