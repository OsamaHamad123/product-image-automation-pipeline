# run_report.py
# تقرير ما حدث في كل تشغيل، ليعرف المالك دائماً نتيجة الليلة:
#   - صف في جدول run_history (local_cache_db.save_run_history)،
#   - الملف temp/nightly/last_report.json (يُكتب حتى عندما لا ترد قاعدة البيانات).
# يكتبه العامل (main.run_worker_mode) في نهاية كل تشغيل من لوحة التحكم أو يدوي، والتشغيل الليلي
# (scripts/run_nightly.py) مرة واحدة لكل ليلة بعد إعادة المحاولات. لا شيء هنا يوقف التشغيل: كل خطأ يُسجل فقط.
#
# النتيجة ورمز الخروج (exit code) لكل سبب توقف (stop_reason):
#   done     0  الطابور انتهى (أو لم يكن فيه شيء)
#   skipped  0  تشغيل آخر حي يحمل القفل، فلم يبدأ هذا التشغيل
#   handed_over 0  عمل التشغيل الليلي ثم توقف على انقطاع، وأثناء انتظار إعادة المحاولة بدأ تشغيل آخر وتولى الطابور:
#               محاولاته وأرقامها تبقى في التقرير (ليس «لم يبدأ»)
#   failed   1  خطأ يحتاج تدخلاً: إعداد الشيت أو بيانات الاعتماد، شيت غير موجود أو غير مشارك، أو خطأ غير متوقع
#   outage   2  انقطاع قد يزول وحده: قاعدة البيانات، أو Google Sheets لا يرد (مهلة، انقطاع الاتصال، 429 / 5xx)، أو
#               محركات البحث (الليلي يعيد المحاولة بعد 15 ثم 60 دقيقة)
#   stopped  3  توقف قبل نهاية الطابور: طلب إيقاف من اللوحة، أو إيقاف العملية (shutdown: SIGTERM / SIGHUP، إعادة
#               تشغيل السيرفر)، أو حد التشغيل الليلي الزمني (time_limit)، أو أي سبب
#               آخر يكتبه العامل (مثل BUDGET_REACHED أو SERPER_CREDIT) ويظهر نصه كما هو؛ الصفوف المتبقية تبقى في الانتظار
#
# التكلفة: من سجل الصرف إن وُجد (أول دالة موجودة من SPEND_LEDGER_FUNCTIONS في local_cache_db، لكل run_id)،
# وإلا تقدير ops_health من عمليات بحث هذا العامل يُحفظ مع التشغيل قبل أن يكتب تشغيل لاحق فوق trace الصفوف.
# التقدير لا يرى محاولات البحث التي أعيدت عند PROVIDER_DOWN (يُحفظ trace آخر محاولة فقط).
# تحديثات الشيت المعلقة من google_sheets.outbox_outcomes() إن وُجدت.

import datetime
import json
import logging
import os
import re
import time

logger = logging.getLogger(__name__)

LAST_REPORT_PATH = os.path.join("temp", "nightly", "last_report.json")

EXIT_CODES = {"done": 0, "skipped": 0, "handed_over": 0, "failed": 1, "outage": 2, "stopped": 3}
DONE_REASONS = ("", "queue_empty")
SKIP_REASONS = ("another_worker",)
# انقطاع قد يزول وحده: التشغيل الليلي يعيد التشغيل كله بعد 15 ثم 60 دقيقة. شيت غير موجود أو غير مشارك ليس منها:
# open_worksheet يرفع SheetTransientError للانقطاع المؤقت ويعيد None لإعداد خاطئ فقط
OUTAGE_REASONS = ("db_unavailable", "sheets_unavailable", "provider_down")
FAILED_REASONS = ("sheet_config", "sheet_not_found", "enqueue_failed", "enqueue_error", "worker_error")
SPEND_LEDGER_FUNCTIONS = ("run_spend", "get_run_spend", "spend_for_run")

REASON_TEXT = {
    "db_unavailable": "قاعدة البيانات لا ترد",
    "sheets_unavailable": "تعذر الاتصال بـ Google Sheets",
    "sheet_not_found": "تعذر فتح الشيت (غير موجود، أو غير مشارك مع حساب الخدمة، أو Google لا يرد)",
    "provider_down": "محركات البحث غير متاحة",
    "sheet_config": "إعداد الشيت غير صالح",
    "enqueue_failed": "فشل تجهيز الطابور",
    "enqueue_error": "خطأ غير متوقع أثناء تجهيز الطابور",
    "worker_error": "خطأ غير متوقع في العامل",
    "stopped": "أُوقف من لوحة التحكم",
    "shutdown": "وقف التشغيل لأن السيرفر انطفى أو انعادت تشغيل الخدمة؛ الصفوف اللي ما خلصت رجعت للانتظار",
    "another_worker": "تشغيل آخر يعمل الآن",
    "budget_reached": "بلغ صرف اليوم الميزانية اليومية (DAILY_BUDGET_USD)",
    "serper_credit": "رصيد Serper انتهى أو مفتاحه مرفوض",
    "time_limit": "بلغ التشغيل الليلي حده الزمني (MaxHours في جدولة المهام) فتوقف قبل نهاية الطابور",
}
OUTCOME_TEXT = {
    "done": "✅ اكتمل",
    "skipped": "⏭️ لم يبدأ",
    "handed_over": "↪️ سلّم الطابور لتشغيل آخر",
    "stopped": "⏸️ توقف قبل نهاية الطابور",
    "outage": "🔌 انقطاع",
    "failed": "❌ فشل",
}
TRIGGER_TEXT = {"nightly": "التشغيل الليلي", "dashboard": "تشغيل من لوحة التحكم", "manual": "تشغيل يدوي"}
HANDED_OVER_TEXT = "توقف على انقطاع، وأثناء انتظار إعادة المحاولة بدأ تشغيل آخر وتولى إكمال الطابور"


# ---------------------------------------------------------------------------
# إخفاء الأسرار: التنبيهات ونصوص الاستثناءات تذهب إلى run_history و last_report.json
# ---------------------------------------------------------------------------

def redact(text):
    """
    النص بلا أسرار: قيم المفاتيح كما يخفيها verify_cloud_services._redact (وقيمة key= في الروابط)، وكلمة مرور قاعدة
    البيانات. لا يرفع أبداً.
    """
    text = "" if text is None else str(text)
    try:
        from verify_cloud_services import _redact
        text = _redact(text)
    except Exception:
        text = re.sub(r"(?i)((?:api_?)?key=)[^&\s'\"]+", r"\1[REDACTED]", text)
    password = os.getenv("DB_PASSWORD", "") or ""
    if len(password) >= 6:
        text = text.replace(password, "[REDACTED]")
    return text


class RedactingFilter(logging.Filter):
    """
    كل سطر سجل بلا أسرار (redact): الرسالة بعد دمج وسائطها، ونص الاستثناء (traceback) ونص المكدس. لا يحجب سطراً ولا
    يرفع أبداً. install_log_redaction يضعه على المسجل الجذر وعلى كل معالجاته.
    """

    def filter(self, record):
        try:
            message = record.getMessage()
            clean = redact(message)
            if clean != message:
                record.msg, record.args = clean, ()
            if record.exc_info and not record.exc_text:
                record.exc_text = logging.Formatter().formatException(record.exc_info)
            if record.exc_text:
                record.exc_text = redact(record.exc_text)
            if record.stack_info:
                record.stack_info = redact(record.stack_info)
        except Exception:  # noqa: BLE001 - a log line is never lost because of the filter
            pass
        return True


_REDACTING_FILTER = RedactingFilter()


def install_log_redaction():
    """
    يضع RedactingFilter على المسجل الجذر وعلى كل معالجاته الحالية وعلى logging.lastResort (ما يُطبع حين لا يوجد
    معالج). فلتر المسجل الجذر وحده لا يرى أسطر المسجلات الأبناء، لذلك يوضع على المعالجات أيضاً؛ يُستدعى بعد إضافة
    المعالجات (cli_bridge._configure_logging، scripts/run_nightly.py، main.py). آمن للاستدعاء أكثر من مرة.
    """
    root = logging.getLogger()
    for target in [root, *root.handlers, logging.lastResort]:
        if target is not None and not any(isinstance(f, RedactingFilter) for f in target.filters):
            target.addFilter(_REDACTING_FILTER)
    return _REDACTING_FILTER


# ---------------------------------------------------------------------------
# سبب التوقف -> النتيجة ورمز الخروج
# ---------------------------------------------------------------------------

def _key(stop_reason):
    return str(stop_reason or "").strip().lower()


def outcome_of(stop_reason):
    """done | skipped | outage | failed | stopped؛ أي سبب غير معروف = stopped (توقف قبل نهاية الطابور)."""
    key = _key(stop_reason)
    if key in DONE_REASONS:
        return "done"
    if key in SKIP_REASONS:
        return "skipped"
    if key in OUTAGE_REASONS:
        return "outage"
    if key in FAILED_REASONS:
        return "failed"
    return "stopped"


def exit_code(stop_reason):
    return EXIT_CODES[outcome_of(stop_reason)]


def is_outage(stop_reason):
    return outcome_of(stop_reason) == "outage"


def reason_text(stop_reason):
    """نص عربي للسبب، أو السبب نفسه كما كتبه العامل إن لم يكن معروفًا؛ '' عند انتهاء الطابور."""
    key = _key(stop_reason)
    if key in DONE_REASONS:
        return ""
    return REASON_TEXT.get(key) or redact(stop_reason)


# ---------------------------------------------------------------------------
# جمع الأرقام (كل قراءة اختيارية: فشلها يُذكر في الملاحظات ولا يوقف التقرير)
# ---------------------------------------------------------------------------

def _usd(value):
    if isinstance(value, dict):
        for key in ("usd", "total_usd", "spent_usd", "spend_usd", "total"):
            if isinstance(value.get(key), (int, float)):
                return float(value[key])
        return None
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def ledger_spend(run_ids, db=None):
    """صرف التشغيل من سجل الصرف إن وُجد (مجموع run_ids)، أو None إن لم يوجد سجل أو لا رقم."""
    if db is None:
        import local_cache_db as db
    fn = next((getattr(db, name) for name in SPEND_LEDGER_FUNCTIONS if callable(getattr(db, name, None))), None)
    if fn is None or not run_ids:
        return None
    values = [_usd(fn(run_id)) for run_id in run_ids]
    values = [v for v in values if v is not None]
    return round(sum(values), 4) if values else None


def estimated_spend(health):
    """التكلفة التقديرية من ملخص ops_health لعمليات بحث العامل (summarize)، أو None."""
    try:
        total = health["windows"]["7d"]["cost_usd"]["total"]
    except (KeyError, TypeError):
        return None
    return round(float(total), 4) if isinstance(total, (int, float)) else None


def worker_health(worker_id, since_seconds):
    """ملخص ops_health لصفوف العامل worker_id خلال since_seconds (قراءة واحدة)، أو None."""
    if not worker_id:
        return None
    try:
        import ops_health
        return ops_health.summarize(ops_health.load_rows(since_seconds=max(1, int(since_seconds)), worker_id=worker_id))
    except Exception as e:
        logger.warning("run_report: ops_health unreadable (%s)", type(e).__name__)
        return None


def outbox_counts(sheets=None, since_ts=None):
    """{pending, conflict, dead} لكتابات الشيت منذ بداية التشغيل (google_sheets.outbox_summary)، أو None."""
    if sheets is None:
        import google_sheets as sheets
    fn = getattr(sheets, "outbox_summary", None)
    if not callable(fn):
        return None
    data = fn(since_ts)
    if not isinstance(data, dict):
        return None
    lowered = {str(k).lower(): v for k, v in data.items()}
    out = {k: int(lowered[k]) for k in ("pending", "conflict", "dead")
           if isinstance(lowered.get(k), (int, float)) and not isinstance(lowered.get(k), bool)}
    return out or None


def _count(value):
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


# صور نُشرت تلقائياً «بدون عزل الخلفية» (المالك أوقف عزل الخلفية بالإعدادات): نفس النص في بطاقة «آخر تشغيل» (HealthController)
BG_SKIPPED_TEXT = "انتشر بدون عزل الخلفية"
BG_FALLBACK_TEXT = "انعزل بطريقة محلية لأن رصيد مزوّد العزل خلص"


def _iso(ts):
    return datetime.datetime.fromtimestamp(ts).isoformat(timespec="seconds") if ts else None


def links_restored_text(count):
    """سطر السجل وتقرير التشغيل: روابط معتمدة اختفت من الشيت وأعاد الإدراج كتابتها بلا بحث (main.plan_enqueue)."""
    return f"{int(count)} روابط معتمدة اختفت من الشيت ورجعت"


def build_report(trigger, attempts, started_ts, ended_ts, health=None, db=None, sheets=None):
    """
    التقرير من محاولات التشغيل (قائمة {stop_reason, run_id, worker_id, notice, bg_skipped}، الأخيرة هي النتيجة):
    {trigger, started_at, ended_at, duration_s, outcome, stop_reason, reason_text, exit_code, attempts,
     attempt_reasons, run_id, run_ids, counts, outbox, spend, notices, database, bg_skipped, bg_fallback,
     links_restored}.
    bg_skipped: صور نشرها العامل تلقائياً «بدون عزل الخلفية» باختيار المالك (main.bg_skipped_count)، مجموع المحاولات.
    bg_fallback: صور نشرها العامل بعزل محلي (rembg بموديل BiRefNet) لأن رصيد PhotoRoom أو remove.bg خلص (main.bg_fallback_count).
    links_restored: روابط معتمدة اختفت من الشيت وأعاد الإدراج كتابتها بلا بحث (main.plan_enqueue: LINK_VANISHED)؛
    تُذكر أيضاً في notices بنص links_restored_text.
    «تشغيل آخر يعمل» بعد محاولة عملت فعلاً ليس «لم يبدأ»: النتيجة handed_over بأرقام المحاولات السابقة.
    """
    if db is None:
        import local_cache_db as db
    attempts = [a for a in attempts if isinstance(a, dict)] or [{}]
    final = attempts[-1]
    stop_reason = final.get("stop_reason") or None
    run_ids = []
    for a in attempts:
        if a.get("run_id") and a["run_id"] not in run_ids:
            run_ids.append(a["run_id"])
    notices = []
    for a in attempts:
        for part in redact(a.get("notice") or a.get("message") or "").split(" | "):
            part = part.strip()
            if part and part not in notices:
                notices.append(part)
    outcome, text = outcome_of(stop_reason), reason_text(stop_reason)
    if outcome == "skipped" and any(_key(a.get("stop_reason")) not in SKIP_REASONS for a in attempts[:-1]):
        outcome, text = "handed_over", HANDED_OVER_TEXT
    report = {
        "trigger": trigger if trigger in TRIGGER_TEXT else "manual",
        "started_at": _iso(started_ts),
        "ended_at": _iso(ended_ts),
        "duration_s": int(max(0, (ended_ts or 0) - (started_ts or 0))) if started_ts and ended_ts else None,
        "outcome": outcome,
        "stop_reason": stop_reason,
        "reason_text": text,
        "exit_code": EXIT_CODES[outcome],
        "attempts": len(attempts),
        "attempt_reasons": [a.get("stop_reason") for a in attempts[:-1]],
        "run_id": run_ids[-1] if run_ids else None,
        "run_ids": run_ids,
        "counts": None,
        "outbox": None,
        "spend": None,
        "notices": notices,
        "database": "unavailable" if _key(stop_reason) == "db_unavailable" else "ok",
        "bg_skipped": sum(_count(a.get("bg_skipped")) for a in attempts),
        "bg_fallback": sum(_count(a.get("bg_fallback")) for a in attempts),
        "links_restored": sum(_count(a.get("links_restored")) for a in attempts),
        "local_index": None,
    }
    if report["links_restored"]:
        report["notices"].append(links_restored_text(report["links_restored"]))
    if report["outcome"] == "skipped":
        return report
    worker_id = next((a.get("worker_id") for a in reversed(attempts) if a.get("worker_id")), None)
    try:
        if run_ids or worker_id:
            since = (ended_ts or time.time()) - (started_ts or time.time()) + 60
            report["counts"] = db.run_outcome_counts(run_ids=run_ids, worker_id=worker_id, since_seconds=since)
    except Exception as e:
        logger.warning("run_report: run counts unreadable: %s", e)
        report["notices"].append("تعذر قراءة نتائج التشغيل من قاعدة البيانات")
        if not db.db_available():
            report["database"] = "unavailable"
    try:
        if run_ids or worker_id:
            since = (ended_ts or time.time()) - (started_ts or time.time()) + 60
            # كم منتج جاوب عليه الفهرس المحلي (مجاني): بطاقة «فهرس المتاجر المحلي» بصفحة الصحة
            report["local_index"] = db.run_local_index_answers(run_ids=run_ids, worker_id=worker_id, since_seconds=since)
    except Exception as e:
        logger.warning("run_report: local index answers unreadable: %s", e)
    try:
        report["outbox"] = outbox_counts(sheets, started_ts)
    except Exception as e:
        logger.warning("run_report: outbox unreadable: %s", e)
    try:
        usd = ledger_spend(run_ids, db)
        if usd is not None:
            report["spend"] = {"usd": usd, "source": "ledger"}
    except Exception as e:
        logger.warning("run_report: spend ledger unreadable: %s", e)
    if report["spend"] is None:
        usd = estimated_spend(health)
        if usd is not None:
            report["spend"] = {"usd": usd, "source": "estimate"}
    return report


# ---------------------------------------------------------------------------
# النشر: run_history، last_report.json
# ---------------------------------------------------------------------------

def history_entry(report):
    """صف run_history من التقرير."""
    counts = report.get("counts") or {}
    outbox = report.get("outbox") or {}
    spend = report.get("spend") or {}
    entry = {
        "run_id": report.get("run_id"),
        "run_trigger": report.get("trigger"),
        "started_at": report.get("started_at"),
        "ended_at": report.get("ended_at"),
        "outcome": report.get("outcome"),
        "stop_reason": report.get("stop_reason"),
        "exit_code": report.get("exit_code"),
        "attempts": report.get("attempts") or 1,
        "outbox_pending": outbox.get("pending"),
        "outbox_conflict": outbox.get("conflict"),
        "outbox_dead": outbox.get("dead"),
        "spend_usd": spend.get("usd"),
        "spend_source": spend.get("source"),
        "notices": redact(" | ".join(report.get("notices") or [])) or None,
        "report_json": report,
    }
    for key in ("enqueued", "searched", "auto_published", "ready_for_review", "not_found", "failed",
                "provider_down", "pending_left"):
        entry[key] = counts.get(key)
    return entry


def write_last_report(report, path=LAST_REPORT_PATH):
    """يكتب التقرير في path (UTF-8، كتابة ذرية). تعيد True عند النجاح."""
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2, default=str)
        os.replace(tmp, path)
        return True
    except OSError as e:
        logger.warning("run_report: could not write %s: %s", path, e)
        return False


def publish(report, db=None, path=LAST_REPORT_PATH):
    """يحفظ التقرير في run_history ثم يكتب last_report.json؛ يطبع ملخصه في السجل."""
    if db is None:
        import local_cache_db as db
    history_id = None
    try:
        history_id = db.save_run_history(history_entry(report))
    except Exception as e:
        logger.warning("run_report: run_history not saved: %s", e)
    report["history_id"] = history_id
    report["notices"] = [redact(n) for n in report.get("notices") or []]       # ما أضافه المستدعي بعد build_report
    write_last_report(report, path)
    counts = report.get("counts") or {}
    print(f"[Run Report] {report['outcome']} (exit {report['exit_code']})"
          + (f": {report['reason_text']}" if report.get("reason_text") else "")
          + (f" | للمراجعة {counts.get('ready_for_review', 0)}، لم يُعثر {counts.get('not_found', 0)}، "
             f"فشل {counts.get('failed', 0)}" if counts else "")
          + ("" if history_id else " | لم يُحفظ في سجل التشغيلات"), flush=True)
    return report


def report_worker_run(info, trigger="manual", db=None, path=LAST_REPORT_PATH):
    """تقرير عامل واحد (لوحة التحكم أو يدوي) من main.LAST_WORKER."""
    report = build_report(trigger, [info], info.get("started_ts"), info.get("ended_ts") or time.time(),
                          health=info.get("health"), db=db)
    return publish(report, db=db, path=path)
