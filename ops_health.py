# ops_health.py
# صحة البحث وتكلفته لصفحة التشخيصات، وتنبيهات انقطاع المزودين لعامل الخلفية.
#
# المصدر الوحيد: automation_queue.trace_json الذي يكتبه main.py، ومنه trace['outcome'] (catalog_match.facade):
#   decision, failure_code, provider_health [{provider, status, http_status, ...}], vlm_calls, queries.
# قراءة فقط: استعلام واحد على أحدث MAX_ROWS صف تم تحديثه خلال 7 أيام؛ كل رقم يأتي من الجدول ولا شيء يُختلق.
#
# حدود معروفة (تُعرض في اللوحة):
# - الطابور يحفظ آخر بحث لكل صف فقط: إعادة المحاولة عند PROVIDER_DOWN والبحث من الكتالوج غير محسوبة.
# - وقت البحث هو outcome.searched_at إن وُجد، وإلا آخر تحديث للصف (updated_at). إعادة الإدراج في الطابور
#   تحدّث updated_at لصفوف المراجعة دون بحث جديد، فقد تدخل عمليات بحث أقدم في نافذة الـ 24 ساعة.

import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

MAX_ROWS = 2000
WINDOWS = (("24h", 24 * 3600), ("7d", 7 * 24 * 3600))
ALERT_WINDOW_SECONDS = 24 * 3600

# نفس تقدير scripts/smoke_live.py: Serper حوالي 1$ لكل 1000 استعلام (D7)، واستدعاء Gemini حوالي 0.001$ (D6)
SERPER_COST_PER_QUERY = 0.001
GEMINI_COST_PER_CALL = 0.001

PROVIDER_STATUSES = ("ok", "empty", "error", "quota", "blocked")
ANSWERED_STATUSES = ("ok", "empty")          # استعلام أجاب عنه المزود (يُحتسب في التكلفة)
KEY_REJECTED_HTTP = (401, 403)
TOP_FAILURE_CODES = 5
# عدد عمليات البحث المتتالية (الأحدث أولاً) الفاشلة بنفس السبب قبل إظهار التنبيه
ALERT_MIN_SEARCHES = 2

ALERTS = {
    "SERPER_CREDIT": "رصيد Serper انتهى أو المفتاح مرفوض",
    "GEMINI_DOWN": "Gemini لا يستجيب",
}

LOAD_SQL = """
    SELECT status, failure_code, trace_json IS NOT NULL AS has_trace,
           TIMESTAMPDIFF(SECOND, updated_at, NOW()) AS age_s,
           JSON_EXTRACT(trace_json, '$.outcome') AS outcome_json
    FROM automation_queue
    WHERE updated_at >= NOW() - INTERVAL %s SECOND
      AND (trace_json IS NOT NULL OR failure_code IS NOT NULL){worker_clause}
    ORDER BY updated_at DESC, id DESC
    LIMIT %s
"""


# ---------------------------------------------------------------------------
# قراءة الصفوف
# ---------------------------------------------------------------------------

def _like_prefix(text):
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "#%"


def load_rows(since_seconds=WINDOWS[-1][1], limit=MAX_ROWS, worker_id=None):
    """
    أحدث صفوف الطابور التي تحدثت خلال since_seconds (قراءة فقط). يُستخرج trace['outcome'] داخل MariaDB
    (JSON_EXTRACT) فلا تُنقل مرشحات الـ trace الكاملة؛ trace تالف يعود outcome_json = NULL.
    worker_id (معرف العامل قبل '#' في معرف السحب): صفوف هذا العامل فقط، فلا تدخل صفوف أعاد الإدراج تأريخها.
    أخطاء قاعدة البيانات تُرفع.
    """
    import local_cache_db

    params = [int(since_seconds)]
    worker_clause = ""
    if worker_id:
        worker_clause = "\n      AND worker_id LIKE %s"
        params.append(_like_prefix(str(worker_id)))
    params.append(int(limit))
    conn = local_cache_db.get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(LOAD_SQL.format(worker_clause=worker_clause), tuple(params))
        rows = cursor.fetchall()
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# تطبيع صف واحد
# ---------------------------------------------------------------------------

def _loads(value):
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", errors="replace")
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return None
    return value


def _as_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _searched_age(outcome, now):
    """عمر البحث بالثواني من outcome.searched_at (ISO بمنطقة زمنية)، أو None."""
    raw = outcome.get("searched_at")
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        when = datetime.fromisoformat(raw.strip())
    except ValueError:
        return None
    if when.tzinfo is None:
        return None
    return max(0, int((now - when).total_seconds()))


def _provider_calls(outcome):
    calls = []
    for item in outcome.get("provider_health") or []:
        if not isinstance(item, dict):
            continue
        provider = str(item.get("provider") or "").strip() or "unknown"
        status = str(item.get("status") or "").strip().lower()
        calls.append((provider, status, _as_int(item.get("http_status"))))
    return calls


def entry_from_row(row, now=None):
    """
    صف طابور -> مدخل موحد: {age_s, timed_by, readable, decision, failure_code, providers, vlm_calls}.
    readable=False عندما يوجد trace لكن بدون outcome مقروء (JSON تالف، أو trace المسار القديم v1).
    يعيد None إن لم يُعرف عمر الصف.
    """
    now = now or datetime.now(timezone.utc)
    outcome = _loads(row.get("outcome_json"))
    outcome = outcome if isinstance(outcome, dict) else None
    age, timed_by = (_searched_age(outcome, now), "searched_at") if outcome else (None, None)
    if age is None:
        age, timed_by = _as_int(row.get("age_s")), "updated_at"
    if age is None:
        return None
    outcome = outcome or {}
    return {
        "age_s": max(0, age),
        "timed_by": timed_by,
        "has_trace": bool(row.get("has_trace", True)),
        "readable": bool(outcome),
        "decision": str(outcome.get("decision") or "") or None,
        # رمز الصف هو الحالة الحالية (يشمل SEARCH_ERROR و REJECTED)، ثم رمز البحث
        "failure_code": (str(row.get("failure_code") or "").strip() or str(outcome.get("failure_code") or "").strip()
                         or None),
        "search_failure_code": str(outcome.get("failure_code") or "").strip() or None,
        "providers": _provider_calls(outcome),
        "vlm_calls": max(0, _as_int(outcome.get("vlm_calls")) or 0),
    }


# ---------------------------------------------------------------------------
# التجميع
# ---------------------------------------------------------------------------

def _count(counter, key, n=1):
    counter[key] = counter.get(key, 0) + n


def window_stats(entries):
    """إحصائيات نافذة زمنية واحدة من مدخلات entry_from_row."""
    decisions, providers, codes = {}, {}, {}
    searches = unreadable = serper_queries = 0
    verifier = {"calls": 0, "searches": 0, "down": 0}
    for e in entries:
        if e["failure_code"]:
            _count(codes, e["failure_code"])
        if not e["readable"]:
            unreadable += 1 if e["has_trace"] else 0
            continue
        searches += 1
        if e["decision"]:
            _count(decisions, e["decision"])
        for provider, status, _http in e["providers"]:
            row = providers.setdefault(provider, dict.fromkeys(PROVIDER_STATUSES, 0))
            _count(row, status if status in PROVIDER_STATUSES else "other")
            if provider == "serper" and status in ANSWERED_STATUSES:
                serper_queries += 1
        verifier["calls"] += e["vlm_calls"]
        verifier["searches"] += 1 if e["vlm_calls"] > 0 else 0
        verifier["down"] += 1 if e["search_failure_code"] == "VERIFIER_DOWN" else 0
    serper_cost = serper_queries * SERPER_COST_PER_QUERY
    gemini_cost = verifier["calls"] * GEMINI_COST_PER_CALL
    top = sorted(codes.items(), key=lambda kv: (-kv[1], kv[0]))[:TOP_FAILURE_CODES]
    return {
        "searches": searches,
        "unreadable": unreadable,
        "decisions": dict(sorted(decisions.items(), key=lambda kv: (-kv[1], kv[0]))),
        "providers": dict(sorted(providers.items())),
        "verifier": verifier,
        "serper_queries": serper_queries,
        "cost_usd": {"serper": round(serper_cost, 4), "gemini": round(gemini_cost, 4),
                     "total": round(serper_cost + gemini_cost, 4)},
        "failure_codes": [{"code": code, "count": n} for code, n in top],
    }


def _serper_credit_streak(entries):
    """
    عدد عمليات البحث المتتالية (الأحدث أولاً) التي رفض فيها Serper كل استعلاماته بسبب الرصيد أو المفتاح
    (quota، أو http 401/403) دون أي إجابة. تتوقف العدّ عند أول بحث أجاب فيه Serper أو فشل لسبب آخر.
    """
    streak = 0
    for e in entries:
        serper = [(status, http) for provider, status, http in e["providers"] if provider == "serper"]
        if not serper:
            continue
        if any(status in ANSWERED_STATUSES for status, _ in serper):
            break
        credit = [s for s, http in serper if s == "quota" or (s == "error" and http in KEY_REJECTED_HTTP)]
        if not credit:
            break
        streak += 1
    return streak


def _verifier_down_streak(entries):
    """عدد عمليات البحث المتتالية (الأحدث أولاً) التي احتاجت Gemini وعادت VERIFIER_DOWN."""
    streak = 0
    for e in entries:
        down = e["search_failure_code"] == "VERIFIER_DOWN"
        if not down and e["vlm_calls"] <= 0:
            continue                      # بحث لم يحتج المحقق (لا مرشحات قابلة للتحقق)
        if not down:
            break
        streak += 1
    return streak


def alerts(entries):
    """
    تنبيهات الانقطاع من أحدث المدخلات (يجب أن تكون مرتبة الأحدث أولاً):
      SERPER_CREDIT  آخر ALERT_MIN_SEARCHES عمليات بحث على الأقل رُفضت فيها كل استعلامات Serper (رصيد/مفتاح)؛
      GEMINI_DOWN    آخر ALERT_MIN_SEARCHES عمليات بحث على الأقل احتاجت Gemini ولم يُجب في أي منها.
    """
    readable = [e for e in entries if e["readable"]]
    out = []
    serper = _serper_credit_streak(readable)
    if serper >= ALERT_MIN_SEARCHES:
        out.append({"code": "SERPER_CREDIT", "message": ALERTS["SERPER_CREDIT"], "searches": serper,
                    "detail": f"آخر {serper} عمليات بحث: رفض Serper كل الاستعلامات (رصيد أو مفتاح: quota / 401 / 403)."})
    gemini = _verifier_down_streak(readable)
    if gemini >= ALERT_MIN_SEARCHES:
        out.append({"code": "GEMINI_DOWN", "message": ALERTS["GEMINI_DOWN"], "searches": gemini,
                    "detail": f"آخر {gemini} عمليات بحث احتاجت التحقق: لم يُقرأ أي ملصق (VERIFIER_DOWN)؛ "
                              "النتائج تذهب للمراجعة البشرية."})
    return out


def summarize(rows, now=None, limit=MAX_ROWS):
    """
    تقرير الصحة والتكلفة من صفوف load_rows (دالة نقية بلا قاعدة بيانات):
    {scanned, limit, truncated, latest_age_s, timed_by_row_update, windows: {24h, 7d}, alerts, prices}.
    """
    now = now or datetime.now(timezone.utc)
    rows = list(rows or [])
    entries = [e for e in (entry_from_row(r, now) for r in rows if isinstance(r, dict)) if e is not None]
    entries.sort(key=lambda e: e["age_s"])
    readable_ages = [e["age_s"] for e in entries if e["readable"]]
    windows = {name: window_stats([e for e in entries if e["age_s"] <= seconds]) for name, seconds in WINDOWS}
    in_alert_window = [e for e in entries if e["age_s"] <= ALERT_WINDOW_SECONDS]
    return {
        "scanned": len(rows),
        "limit": limit,
        "truncated": len(rows) >= limit,
        "latest_age_s": readable_ages[0] if readable_ages else None,
        "timed_by_row_update": sum(1 for e in entries if e["readable"] and e["timed_by"] == "updated_at"),
        "windows": windows,
        "alerts": alerts(in_alert_window),
        "prices": {"serper_per_query": SERPER_COST_PER_QUERY, "gemini_per_call": GEMINI_COST_PER_CALL},
    }


def health_report():
    """ملخص صفحة التشخيصات (قراءة فقط؛ أخطاء قاعدة البيانات تُرفع)."""
    return summarize(load_rows())


def outage_notice(since_seconds, worker_id=None):
    """
    نص automation_state.notice لانقطاع ظهر في عمليات بحث العامل worker_id خلال since_seconds الأخيرة،
    مثل 'SERPER_CREDIT: رصيد Serper انتهى أو المفتاح مرفوض'، أو '' إن لم يوجد. أخطاء قاعدة البيانات تُرفع.
    """
    report = summarize(load_rows(since_seconds=max(1, int(since_seconds)), worker_id=worker_id))
    return " | ".join(f"{a['code']}: {a['message']}" for a in report["alerts"])
