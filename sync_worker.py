# sync_worker.py
# عامل المزامنة المؤجلة (Write-Behind) من Redis إلى Google Sheets.
#
# - يضبط مفتاح النبض 'writebehind:heartbeat' (TTL 30 ثانية) في كل دورة؛ google_sheets لا يستخدم Redis
#   إلا إذا وجد هذا النبض، وإلا يكتب عبر طابور MariaDB.
# - لا يُحذف مفتاح من مجموعة dirty (SREM) إلا بعد نجاح الكتابة الفعلية.
# - الحمولات ذات الصيغة غير المعروفة تُترك كما هي وتُسجل في السجل؛ لا تُحذف أبداً.
# - إذا حملت الحمولة هوية متوقعة ('expect') وتعارضت مع الشيت، تُنقل إلى 'writebehind:conflicts' دون كتابة،
#   وتُنقل حمولتها إلى 'writebehind:conflict_payload:<key>' كي لا تُدمج في كتابة لاحقة صحيحة لنفس الصف.
# - بعد الكتابة يُحذف المفتاح فقط إذا لم تتغير حمولته أثناء المزامنة (تحديث وصل أثناءها يُكتب في الدورة التالية).
# - المفتاح الذي يفشل 5 مرات متتالية يُنقل إلى 'writebehind:dead' ولا يُعاد (كي لا يستهلك حصة الكتابة).

import json
import logging
import os
import random
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
DEAD_SET_KEY = "writebehind:dead"
ATTEMPTS_KEY = "writebehind:attempts"
MAX_KEY_ATTEMPTS = 5
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
    """(row_index, updates, expect) أو None إذا كانت الصيغة غير معروفة."""
    try:
        payload = json.loads(raw)
        row_index = int(payload["row_index"])
        updates = payload["updates"]
        if not isinstance(updates, dict) or row_index <= 1:
            return None
        parsed = {int(col): val for col, val in updates.items()}
        expect = payload.get("expect") if isinstance(payload.get("expect"), dict) else None
        return row_index, parsed, expect
    except Exception:
        return None


def _batch(row_index, updates):
    import gspread
    return [{"range": gspread.utils.rowcol_to_a1(row_index, col + 1), "values": [[str(val)]]}
            for col, val in updates.items()]


def _write(worksheet, data, max_retries=5):
    """كتابة دفعة مع ارتداد أسّي عند تجاوز الحصة (429)."""
    retry = 0
    while True:
        try:
            worksheet.batch_update(data, value_input_option="RAW")
            return
        except Exception as err:
            text = str(err)
            if ("429" in text or "RESOURCE_EXHAUSTED" in text) and retry < max_retries:
                sleep_time = min((2 ** retry) + random.uniform(0.1, 1.0), 32.0)
                logger.warning("[Sync Worker] تجاوز الحصة؛ إعادة المحاولة %s/%s خلال %.1f ثانية",
                               retry + 1, max_retries, sleep_time)
                time.sleep(sleep_time)
                retry += 1
                continue
            raise


def _compare_and_delete(r, key, raw):
    """
    يحذف الحمولة ويزيل المفتاح من dirty فقط إذا كانت الحمولة ما زالت كما قُرئت. تحديث وصل أثناء
    المزامنة يبقى ليُكتب في الدورة التالية (لا يضيع). ذري عبر WATCH/MULTI عند توفره.
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
                    p.hdel(ATTEMPTS_KEY, key)
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
    _clear_attempts(r, key)
    return True


def _clear_attempts(r, key):
    try:
        r.hdel(ATTEMPTS_KEY, key)
    except Exception:
        pass


def _note_failure(r, key):
    """يسجل فشلاً للمفتاح؛ بعد MAX_KEY_ATTEMPTS ينقله إلى المجموعة الميتة ولا يُعاد."""
    try:
        attempts = int(r.hincrby(ATTEMPTS_KEY, key, 1))
    except Exception:
        return
    if attempts >= MAX_KEY_ATTEMPTS:
        logger.error("[Sync Worker] المفتاح %s فشل %s مرات؛ نقله إلى %s.", key, attempts, DEAD_SET_KEY)
        r.smove(DIRTY_SET_KEY, DEAD_SET_KEY, key)
        _clear_attempts(r, key)


def run_sync_cycle(worksheet, r=None):
    """دورة مزامنة واحدة. تعيد عدد المفاتيح التي كُتبت بنجاح."""
    r = r or _client()
    beat(r)
    keys = list(r.smembers(DIRTY_SET_KEY) or [])[:BATCH_SIZE]
    if not keys:
        return 0

    jobs = []   # (key, row_index, updates, expect, raw)
    for key in sorted(keys):
        raw = r.get(f"{CACHE_PREFIX}{key}")
        parsed = _parse_payload(raw) if raw else None
        if parsed is None:
            if key not in _reported_unknown:
                _reported_unknown.add(key)
                logger.warning("[Sync Worker] حمولة مفقودة أو بصيغة غير معروفة للمفتاح %s؛ تُترك كما هي.", key)
            continue
        jobs.append((key,) + parsed + (raw,))
    if not jobs:
        return 0

    records = {key: (row, expect) for key, row, _, expect, _ in jobs if expect}
    conflicts = google_sheets.find_record_conflicts(worksheet, records) if records else {}
    ready = []
    for key, row, updates, expect, raw in jobs:
        if expect and key in conflicts:
            logger.warning("[Sync Worker] تعارض هوية في الصف %s (%s)؛ نقل %s إلى %s دون كتابة.",
                           row, conflicts[key], key, CONFLICT_SET_KEY)
            # الحمولة تُنقل جانباً: كتابة لاحقة صحيحة لنفس الصف تبدأ من حمولة فارغة ولا ترث قيمها
            r.set(f"{CONFLICT_PAYLOAD_PREFIX}{key}", raw)
            r.delete(f"{CACHE_PREFIX}{key}")
            r.smove(DIRTY_SET_KEY, CONFLICT_SET_KEY, key)
            _clear_attempts(r, key)
        else:
            ready.append((key, row, updates, raw))
    if not ready:
        return 0

    written = 0
    try:
        data = []
        for _, row, updates, _ in ready:
            data.extend(_batch(row, updates))
        _write(worksheet, data)
        for key, _, _, raw in ready:
            _compare_and_delete(r, key, raw)
        written = len(ready)
    except Exception as err:
        logger.warning("[Sync Worker] فشل الإرسال الجماعي (%s)؛ إعادة المحاولة مفتاحاً مفتاحاً.", err)
        for key, row, updates, raw in ready:
            try:
                _write(worksheet, _batch(row, updates), max_retries=2)
                _compare_and_delete(r, key, raw)
                written += 1
            except Exception as single_err:
                # يبقى المفتاح في dirty وتبقى حمولته: يُعاد في الدورة التالية، حتى MAX_KEY_ATTEMPTS
                logger.warning("[Sync Worker] فشل مزامنة %s: %s", key, single_err)
                _note_failure(r, key)
    if written:
        google_sheets.clear_cache()
    return written


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    r = _client()
    try:
        r.ping()
    except Exception:
        logger.info("[Sync Worker] Redis غير متاح محلياً؛ لا حاجة لهذا العامل (الكتابة تتم عبر طابور MariaDB).")
        return

    logger.info("[Sync Worker] عامل المزامنة المؤجلة يعمل.")
    worksheet = None
    while True:
        try:
            beat(r)
            if worksheet is None:
                client = google_sheets.get_sheets_client()
                if client:
                    worksheet = google_sheets.open_worksheet(client, config.SPREADSHEET_NAME_OR_URL)
            if worksheet:
                run_sync_cycle(worksheet, r)
        except Exception as e:
            logger.exception("[Sync Worker] خطأ في الدورة: %s", e)
            worksheet = None
        time.sleep(SYNC_INTERVAL)


if __name__ == "__main__":
    main()
