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
# - الإدراج يمسح trace_json للصفوف التي يعيدها للانتظار، وكل تشغيل يكتب فوق trace الصف: تكلفة بحث سابق لصف
#   أُعيد البحث عنه تختفي من نوافذ الطابور (تقدير أقل من الحقيقة).
# لذلك يحفظ كل تشغيل تكلفته عند نهايته في run_history (run_report.py: من سجل الصرف إن وُجد، وإلا تقدير هذا
# الملخص لعمليات بحث العامل نفسه، مرة واحدة لكل تشغيل أو ليلة فلا تُحسب مرتين)، وruns_cost() يجمعها لكل نافذة.
# هذا الرقم لا يمحوه بحث لاحق، لكنه لا يرى محاولات PROVIDER_DOWN المعادة داخل البحث الواحد، ولا البحث اليدوي
# من صفحة المراجعة (خارج الطابور)، ولا ما قبل وجود run_history. الصرف الفعلي يحتاج سجل صرف لكل استدعاء.

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
        # حزمة المحقق (verifier, P3): استهلاك كل نموذج قراءة وتنبيهاته
        "vlm_usage": _vlm_usage(outcome),
        "verifier_notices": [str(n) for n in (outcome.get("verifier_notices") or []) if isinstance(n, str)],
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
    models = {}                                    # (role, provider, model) -> استهلاك نموذج القراءة
    model_cost = {"gemini": 0.0, "claude": 0.0}    # من الاستهلاك المسجل (outcome.vlm_usage)
    legacy_calls = 0                               # استدعاءات بلا استهلاك مسجل: بالسعر الثابت للاستدعاء
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
            if provider in SERPER_BILLED_PROVIDERS and status in ANSWERED_STATUSES:
                serper_queries += 1
        verifier["calls"] += e["vlm_calls"]
        verifier["searches"] += 1 if e["vlm_calls"] > 0 else 0
        verifier["down"] += 1 if e["search_failure_code"] == "VERIFIER_DOWN" else 0
        usage = e.get("vlm_usage") or []
        if not usage:
            legacy_calls += e["vlm_calls"]
        for u in usage:
            key = (u["role"], u["provider"], u["model"])
            m = models.setdefault(key, {"role": u["role"], "provider": u["provider"], "model": u["model"],
                                        "calls": 0, "input_tokens": 0, "output_tokens": 0, "usd": 0.0,
                                        "estimated_calls": 0})
            m["calls"] += 1
            m["input_tokens"] += u["input_tokens"]
            m["output_tokens"] += u["output_tokens"]
            m["usd"] += u["usd"]
            m["estimated_calls"] += 1 if u["estimated"] else 0
            model_cost["claude" if u["provider"] == "claude" else "gemini"] += u["usd"]
    sources = paid_sources(entries)
    serper_cost = sum(v["cost_usd"] for k, v in sources.items() if k in SERPER_BILLED_PROVIDERS)
    serpapi_cost = sum(v["cost_usd"] for k, v in sources.items() if k not in SERPER_BILLED_PROVIDERS)
    gemini_cost = legacy_calls * GEMINI_COST_PER_CALL + model_cost["gemini"]
    claude_cost = model_cost["claude"]
    top = sorted(codes.items(), key=lambda kv: (-kv[1], kv[0]))[:TOP_FAILURE_CODES]
    cost = {"serper": round(serper_cost, 4), "gemini": round(gemini_cost, 4)}
    if claude_cost > 0 or any(k[1] == "claude" for k in models):
        cost["claude"] = round(claude_cost, 4)
    if serpapi_cost > 0:
        cost["serpapi"] = round(serpapi_cost, 4)
    cost["total"] = round(serper_cost + serpapi_cost + gemini_cost + claude_cost, 4)
    return {
        "searches": searches,
        "unreadable": unreadable,
        "decisions": dict(sorted(decisions.items(), key=lambda kv: (-kv[1], kv[0]))),
        "providers": dict(sorted(providers.items())),
        "verifier": verifier,
        "serper_queries": serper_queries,
        "sources": sources,
        "cost_usd": cost,
        "verifier_models": [dict(m, usd=round(m["usd"], 4)) for m in
                            sorted(models.values(), key=lambda m: (m["role"] != "primary", -m["usd"], m["model"]))],
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
    out.extend(verifier_alerts(readable))   # حزمة المحقق (verifier, P3)
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
        "source_prices": source_prices(),
    }


def health_report():
    """ملخص صفحة التشخيصات (قراءة فقط؛ أخطاء قاعدة البيانات تُرفع)."""
    report = summarize(load_rows())
    report["verifier_month"] = verifier_month()   # حزمة المحقق (verifier, P3): صرف الشهر وميزانية النموذج القوي
    report["runs_cost"] = runs_cost()             # حزمة التشغيل الليلي (P4b): التكلفة كما حُفظت عند نهاية كل تشغيل
    return report


# ---------------------------------------------------------------------------
# حزمة التشغيل الليلي (P4b): تكلفة التشغيلات من run_history (تبقى بعد أن يكتب تشغيل لاحق فوق trace الصفوف)
# ---------------------------------------------------------------------------

RUNS_COST_SQL = """
    SELECT TIMESTAMPDIFF(SECOND, COALESCE(ended_at, created_at), NOW()) AS age_s, spend_usd, spend_source
    FROM run_history
    WHERE COALESCE(ended_at, created_at) >= NOW() - INTERVAL %s SECOND
"""


def runs_cost_summary(rows):
    """
    لكل نافذة (24h، 7d): {usd, runs, priced_runs, estimated_runs} من صفوف run_history (دالة نقية). تشغيل بلا
    تكلفة محفوظة (قاعدة البيانات لم ترد، أو لم يبحث) يُعد في runs فقط.
    """
    out = {}
    for name, seconds in WINDOWS:
        usd, runs, priced, estimated = 0.0, 0, 0, 0
        for r in rows or []:
            age = _as_int(r.get("age_s"))
            if age is None or age > seconds:
                continue
            runs += 1
            spend = _as_float(r.get("spend_usd"))
            if spend is None:
                continue
            usd += spend
            priced += 1
            estimated += 1 if str(r.get("spend_source") or "") == "estimate" else 0
        out[name] = {"usd": round(usd, 4), "runs": runs, "priced_runs": priced, "estimated_runs": estimated}
    return out


def runs_cost():
    """runs_cost_summary لآخر 7 أيام من run_history، أو None إذا تعذرت القراءة (لا يُختلق رقم)."""
    try:
        import local_cache_db
        conn = local_cache_db.get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(RUNS_COST_SQL, (WINDOWS[-1][1],))
            rows = [dict(r) for r in cursor.fetchall()]
        finally:
            try:
                conn.close()
            except Exception:
                pass
    except Exception as exc:
        logger.warning("ops_health: run_history unreadable (%s)", type(exc).__name__)
        return None
    return runs_cost_summary(rows)


def outage_notice(since_seconds, worker_id=None):
    """
    نص automation_state.notice لانقطاع ظهر في عمليات بحث العامل worker_id خلال since_seconds الأخيرة،
    مثل 'SERPER_CREDIT: رصيد Serper انتهى أو المفتاح مرفوض'، أو '' إن لم يوجد. أخطاء قاعدة البيانات تُرفع.
    """
    return notice_from_report(summarize(load_rows(since_seconds=max(1, int(since_seconds)), worker_id=worker_id)))


def notice_from_report(report):
    """نص التنبيه من ملخص summarize: 'CODE: الرسالة' لكل تنبيه مفصولة بـ ' | '، أو ''."""
    return " | ".join(f"{a['code']}: {a['message']}" for a in (report or {}).get("alerts") or [])


# ---------------------------------------------------------------------------
# حزمة المحقق (verifier, P3): استهلاك نماذج قراءة الملصق وتكلفتها، وتنبيهات الميزانية والمفتاح.
# outcome.vlm_usage: لكل استدعاء مدفوع {role, provider, model, input_tokens, output_tokens, estimated, usd}
# (catalog_match.verifiers)، و outcome.verifier_notices: رموز مثل 'strong_budget_exhausted'.
# ---------------------------------------------------------------------------

ALERTS.update({
    "VERIFIER_BUDGET": "ميزانية النموذج القوي لهذا الشهر انتهت",
    "VERIFIER_KEY": "مفتاح نموذج التحقق الإضافي مرفوض أو غير محفوظ",
})
BUDGET_NOTICES = ("strong_budget_exhausted",)
# مفتاح النموذج القوي فقط (الإشعارات الموسومة strong: من catalog_match.verifiers.cascade). مشكلة مفتاح
# النموذج الأساسي تظهر كـ VERIFIER_DOWN وتنبيه GEMINI_DOWN، لا هنا: إيقاف النموذج القوي لا يحلها.
KEY_NOTICES = ("strong:claude_key_rejected", "strong:claude_key_missing", "strong:gemini_key_rejected")


def _as_float(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and number >= 0 else None


def _vlm_usage(outcome):
    """outcome.vlm_usage مطبّعاً: مدخل تالف يُتجاهل، والرموز نصوص قصيرة، والأرقام غير السالبة فقط."""
    out = []
    for item in outcome.get("vlm_usage") or []:
        if not isinstance(item, dict):
            continue
        provider = str(item.get("provider") or "").strip().lower()[:16]
        model = str(item.get("model") or "").strip()[:96]
        if not provider or not model:
            continue
        out.append({
            "role": "strong" if str(item.get("role") or "") == "strong" else "primary",
            "provider": provider,
            "model": model,
            "input_tokens": max(0, _as_int(item.get("input_tokens")) or 0),
            "output_tokens": max(0, _as_int(item.get("output_tokens")) or 0),
            "usd": _as_float(item.get("usd")) or 0.0,
            "estimated": bool(item.get("estimated")),
        })
    return out


def _strong_ran(e):
    return any(u["role"] == "strong" for u in e.get("vlm_usage") or [])


def verifier_alerts(entries):
    """
    تنبيهات حزمة المحقق من المدخلات المقروءة (الأحدث أولاً):
      VERIFIER_BUDGET  آخر بحث احتاج نظرة النموذج القوي تخطاها لأن ميزانية الشهر انتهت (ولم تأتِ بعده نظرة)؛
      VERIFIER_KEY     آخر ALERT_MIN_SEARCHES عمليات بحث على الأقل رفض فيها مزود النموذج الإضافي المفتاح أو لم يجده.
    """
    out = []
    budget = 0
    for e in entries:
        notices = e.get("verifier_notices") or []
        if any(n in BUDGET_NOTICES for n in notices):
            budget += 1
            continue
        if _strong_ran(e):
            break
    if budget >= 1:
        out.append({"code": "VERIFIER_BUDGET", "message": ALERTS["VERIFIER_BUDGET"], "searches": budget,
                    "detail": f"آخر {budget} عمليات بحث احتاجت نظرة ثانية ولم تأخذها لأن الميزانية الشهرية انتهت؛ "
                              "ارفع الميزانية من الإعدادات أو انتظر الشهر القادم. النتائج غير المؤكدة تذهب للمراجعة."})
    key = 0
    for e in entries:
        notices = e.get("verifier_notices") or []
        if any(n in KEY_NOTICES for n in notices):
            key += 1
            continue
        if _strong_ran(e):
            break
    if key >= ALERT_MIN_SEARCHES:
        out.append({"code": "VERIFIER_KEY", "message": ALERTS["VERIFIER_KEY"], "searches": key,
                    "detail": f"آخر {key} عمليات بحث: النموذج الإضافي لم يقرأ أي ملصق لأن مفتاحه مرفوض أو غير محفوظ "
                              "(Anthropic أو Gemini). أضف المفتاح من الإعدادات أو أوقف النموذج القوي."})
    return out


def verifier_month():
    """
    صرف الشهر الحالي (UTC) من جدول verifier_spend: {month, budget_usd, strong_usd, total_usd, models, primary,
    strong}. قراءة فقط؛ None إذا تعذرت القراءة (لا يُختلق رقم).
    """
    try:
        from catalog_match import settings as cm_settings
        from catalog_match.verifiers.spend import MariaDbSpendStore, current_month
        rows = MariaDbSpendStore().month_rows()
        budget = cm_settings.verifier_monthly_budget_usd()
        primary, strong = cm_settings.verifier_primary(), cm_settings.verifier_strong()
    except Exception as exc:
        logger.warning("ops_health: verifier month spend unreadable (%s)", type(exc).__name__)
        return None
    strong_usd = sum(r["usd"] for r in rows if r["role"] == "strong")
    return {
        "month": current_month(),
        "budget_usd": round(budget, 2),
        "strong_usd": round(strong_usd, 4),
        "total_usd": round(sum(r["usd"] for r in rows), 4),
        "models": rows,
        "primary": primary,
        "strong": strong,
    }


# --- sources package (P3): كل مصدر مدفوع يُسعَّر حسب الاستدعاءات التي أجاب عنها ---
# Serper يحاسب على كل نقطة نهاية: صور Google (serper)، بحث الويب لصفحات المتاجر (serper_web)، Google Shopping
# (serper_shopping)، والبحث بالصورة (lens_serper). SerpApi Google Lens (lens_serpapi) أغلى بكثير، وسعره من
# الإعداد SERPAPI_LENS_PRICE_USD (افتراضياً 0.015$ لكل بحث، خطة Developer). الأسعار قابلة للتعديل هنا.
SERPER_WEB_COST_PER_QUERY = 0.001
SERPER_SHOPPING_COST_PER_QUERY = 0.001
SERPER_LENS_COST_PER_CALL = 0.001
SERPAPI_LENS_DEFAULT_COST = 0.015
SERPER_BILLED_PROVIDERS = ("serper", "serper_web", "serper_shopping", "lens_serper")
PAID_PROVIDERS = SERPER_BILLED_PROVIDERS + ("lens_serpapi",)


def _serpapi_lens_price():
    try:
        from catalog_match import settings as cm_settings
        return float(cm_settings.serpapi_lens_price_usd())
    except Exception:
        return SERPAPI_LENS_DEFAULT_COST


def source_prices():
    """سعر الاستدعاء الواحد لكل مصدر مدفوع (دولار)."""
    return {
        "serper": SERPER_COST_PER_QUERY,
        "serper_web": SERPER_WEB_COST_PER_QUERY,
        "serper_shopping": SERPER_SHOPPING_COST_PER_QUERY,
        "lens_serper": SERPER_LENS_COST_PER_CALL,
        "lens_serpapi": _serpapi_lens_price(),
    }


def paid_sources(entries):
    """
    لكل مصدر مدفوع ظهر في النافذة: {calls: الاستدعاءات التي أجاب عنها (ok/empty)، cost_usd}.
    الاستدعاءات المرفوضة (رصيد، مفتاح، خطأ) لا تُحتسب، مثل استعلامات Serper للصور.
    """
    prices = source_prices()
    calls = {}
    for e in entries:
        if not e.get("readable"):
            continue
        for provider, status, _http in e["providers"]:
            if provider in PAID_PROVIDERS and status in ANSWERED_STATUSES:
                calls[provider] = calls.get(provider, 0) + 1
    return {name: {"calls": n, "cost_usd": round(n * prices[name], 4)} for name, n in sorted(calls.items())}
# --- end sources package ---
