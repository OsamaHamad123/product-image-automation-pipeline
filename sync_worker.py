# sync_worker.py
# عامل المزامنة المؤجلة (Write-Behind) من Redis إلى Google Sheets.
#
# - يضبط مفتاح النبض 'writebehind:heartbeat' (TTL 30 ثانية) في كل دورة؛ google_sheets لا يستخدم Redis
#   إلا إذا وجد هذا النبض، وإلا يكتب عبر طابور MariaDB.
# - قناة كتابة واحدة: لا يكتب هذا العامل في الشيت مباشرة. ينقل كل حمولة Redis إلى طابور MariaDB (sheet_updates)
#   ثم يفرّغ الطابور بنفس قفل وقواعد عامل الخلفية (google_sheets.flush_outbox): العمود بالمفتاح المنطقي وقت
#   الكتابة، التحقق من الهوية ونقل الكتابة للصف الوحيد المطابق، إعادة المحاولة المؤجلة، إبلاغ النتائج، والترتيب
#   بالتسلسل seq (قيمة أقدم لا تُكتب أبداً بعد قيمة أحدث، أياً كانت القناة التي حملتهما).
# - لا يُحذف مفتاح من مجموعة dirty (SREM) إلا بعد نقل حمولته كاملة إلى الطابور، وفقط إذا لم تتغير أثناء النقل
#   (تحديث وصل أثناءها يُنقل في الدورة التالية). تعذر النقل (قاعدة البيانات متوقفة) يُبقي الحمولة كما هي.
# - الحمولات ذات الصيغة غير المعروفة تُترك كما هي وتُسجل في السجل؛ لا تُحذف أبداً.
# - الحمولات القديمة التي تحدد الأعمدة بأرقامها (قبل المفاتيح المنطقية) لا تُكتب: عمودها لا يمكن التحقق منه
#   (إدراج عمود يغيّر معنى الرقم). تُنقل إلى 'writebehind:conflicts' وحمولتها إلى 'writebehind:conflict_payload:<key>'
#   وتُبلَّغ عبر outcome hook (سجل الأخطاء)؛ الصف يُعاد نشره من الكاش في التشغيل التالي لأن خليته بقيت فارغة.

import json
import logging
import os
import sys
import time

# إضافة المجلد الحالي للمسار
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import config
import google_sheets

logger = logging.getLogger("sync_worker")

DIRTY_SET_KEY = google_sheets.DIRTY_SET_KEY
CACHE_PREFIX = google_sheets.CACHE_PREFIX
HEARTBEAT_KEY = google_sheets.HEARTBEAT_KEY
CONFLICT_SET_KEY = "writebehind:conflicts"
CONFLICT_PAYLOAD_PREFIX = "writebehind:conflict_payload:"
HEARTBEAT_TTL = 30
SYNC_INTERVAL = 10
BATCH_SIZE = 100

_reported_unknown = set()


def _client():
    import redis
    return redis.Redis(host=config.REDIS_HOST, port=config.REDIS_PORT, db=config.REDIS_DB, decode_responses=True)


def beat(r):
    """نبض حي: google_sheets يستخدم Redis فقط بوجوده."""
    r.set(HEARTBEAT_KEY, str(int(time.time())), ex=HEARTBEAT_TTL)


def _parse_payload(raw):
    """
    {'row_index', 'updates': {col_key: value}, 'seqs', 'expect', 'legacy'} أو None إذا كانت الصيغة غير معروفة.
    legacy=True: حمولة قديمة تحدد الأعمدة بأرقامها.
    """
    try:
        payload = json.loads(raw)
        row_index = int(payload["row_index"])
        updates = payload["updates"]
        if not isinstance(updates, dict) or row_index <= 1:
            return None
        expect = payload.get("expect") if isinstance(payload.get("expect"), dict) else None
        if payload.get("v") == google_sheets.REDIS_PAYLOAD_VERSION:
            seqs = payload.get("seqs") if isinstance(payload.get("seqs"), dict) else {}
            return {"row_index": row_index, "updates": {str(k): v for k, v in updates.items()},
                    "seqs": seqs, "expect": expect, "legacy": False}
        if "v" not in payload and updates and all(str(k).lstrip("-").isdigit() for k in updates):
            return {"row_index": row_index, "updates": updates, "seqs": {}, "expect": expect, "legacy": True}
        return None
    except Exception:
        return None


def _compare_and_delete(r, key, raw):
    """
    يحذف الحمولة ويزيل المفتاح من dirty فقط إذا كانت الحمولة ما زالت كما قُرئت. تحديث وصل أثناء
    النقل يبقى ليُنقل في الدورة التالية (لا يضيع). ذري عبر WATCH/MULTI عند توفره.
    """
    cache_key = f"{CACHE_PREFIX}{key}"
    pipeline = getattr(r, "pipeline", None)
    if pipeline is not None:
        try:
            import redis
            with r.pipeline() as p:
                try:
                    p.watch(cache_key)
                    if p.get(cache_key) != raw:
                        p.unwatch()
                        return False
                    p.multi()
                    p.delete(cache_key)
                    p.srem(DIRTY_SET_KEY, key)
                    p.execute()
                    return True
                except redis.WatchError:
                    return False
        except ImportError:  # pragma: no cover - redis is a dependency of this worker
            pass
    if r.get(cache_key) != raw:
        return False
    r.delete(cache_key)
    r.srem(DIRTY_SET_KEY, key)
    return True


def _park_legacy(r, key, raw, parsed):
    """حمولة قديمة بأرقام أعمدة: تُنقل جانباً دون كتابة وتُبلَّغ (عمودها لا يمكن التحقق منه)."""
    r.set(f"{CONFLICT_PAYLOAD_PREFIX}{key}", raw)
    r.delete(f"{CACHE_PREFIX}{key}")
    r.smove(DIRTY_SET_KEY, CONFLICT_SET_KEY, key)
    expect = parsed.get("expect") or {}
    google_sheets._report_outcome({
        "id": None, "row": parsed["row_index"], "queued_row": parsed["row_index"],
        "column_key": ",".join(str(c) for c in parsed["updates"]), "status": "CONFLICT", "attempts": 0,
        "error": f"legacy Redis payload {key} addresses columns by position; not written (parked in {CONFLICT_SET_KEY})",
        "value": None, "barcode": expect.get("barcode"), "product_name": expect.get("name"),
        "brand": expect.get("brand"),
    })


def _forward(queue, parsed):
    """كل خلية في الحمولة تصبح صفاً في طابور MariaDB بمفتاحها المنطقي وتسلسلها وهوية المنتج."""
    keys = google_sheets._outbox_keys(parsed.get("expect"))
    for col_key, value in parsed["updates"].items():
        queue.append_update(parsed["row_index"], -1, value, col_key=col_key, seq=parsed["seqs"].get(col_key),
                            **keys)


def run_sync_cycle(worksheet, r=None, queue=None, flush=True):
    """
    دورة مزامنة واحدة: نقل حمولات Redis إلى طابور MariaDB، ثم تفريغ الطابور في الشيت (flush=True).
    تعيد عدد المفاتيح التي نُقلت بنجاح.
    """
    r = r or _client()
    beat(r)
    keys = list(r.smembers(DIRTY_SET_KEY) or [])[:BATCH_SIZE]
    forwarded = 0
    for key in sorted(keys):
        raw = r.get(f"{CACHE_PREFIX}{key}")
        parsed = _parse_payload(raw) if raw else None
        if parsed is None:
            if key not in _reported_unknown:
                _reported_unknown.add(key)
                logger.warning("[Sync Worker] حمولة مفقودة أو بصيغة غير معروفة للمفتاح %s؛ تُترك كما هي.", key)
            continue
        if parsed["legacy"]:
            logger.error("[Sync Worker] حمولة قديمة بأرقام أعمدة للمفتاح %s؛ نقلها إلى %s دون كتابة.",
                         key, CONFLICT_SET_KEY)
            _park_legacy(r, key, raw, parsed)
            continue
        try:
            queue = queue or google_sheets.SQLiteTransactionQueue()
            _forward(queue, parsed)
        except Exception as e:
            # تبقى الحمولة في dirty كما هي وتُنقل في الدورة التالية (التكرار يُدمج في الطابور: نفس seq)
            logger.warning("[Sync Worker] تعذر نقل %s إلى طابور MariaDB: %s", key, e)
            continue
        if _compare_and_delete(r, key, raw):
            forwarded += 1
    if flush and worksheet is not None:
        google_sheets.flush_outbox(worksheet, queue)
    return forwarded


def _loop_once(r, state):
    """
    دورة واحدة من حلقة العامل: نقل حمولات Redis إلى الطابور أولاً (لا ينتظر فتح الشيت)، ثم فتح الشيت إن لزم
    وتفريغ الطابور. فتح الشيت قد يطول (إعادة محاولة الأخطاء المؤقتة) فيُجدد النبض بعده.
    state: {'queue', 'worksheet'} يبقى بين الدورات.
    """
    beat(r)
    if state.get("queue") is None:
        state["queue"] = google_sheets.SQLiteTransactionQueue()
    run_sync_cycle(None, r, queue=state["queue"], flush=False)
    if state.get("worksheet") is None:
        client = google_sheets.get_sheets_client()
        if client:
            try:
                state["worksheet"] = google_sheets.open_worksheet(client, config.SPREADSHEET_NAME_OR_URL)
            finally:
                beat(r)
    if state.get("worksheet") is not None:
        google_sheets.flush_outbox(state["worksheet"], state["queue"])
        beat(r)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    r = _client()
    try:
        r.ping()
    except Exception:
        logger.info("[Sync Worker] Redis غير متاح محلياً؛ لا حاجة لهذا العامل (الكتابة تتم عبر طابور MariaDB).")
        return

    logger.info("[Sync Worker] عامل المزامنة المؤجلة يعمل.")
    state = {}
    while True:
        try:
            _loop_once(r, state)
        except Exception as e:
            logger.exception("[Sync Worker] خطأ في الدورة: %s", e)
            state["worksheet"] = None
        time.sleep(SYNC_INTERVAL)


if __name__ == "__main__":
    main()
