# run_report.py
# تقرير ما حدث في كل تشغيل، ليعرف المالك دائماً نتيجة الليلة:
#   - صف في جدول run_history (local_cache_db.save_run_history)،
#   - الملف temp/nightly/last_report.json (يُكتب حتى عندما لا ترد قاعدة البيانات)،
#   - رسالة Telegram عربية قصيرة عندما يكون TELEGRAM_BOT_TOKEN و TELEGRAM_CHAT_ID مضبوطين.
# يكتبه العامل (main.run_worker_mode) في نهاية كل تشغيل من لوحة التحكم أو يدوي، والتشغيل الليلي
# (scripts/run_nightly.py) مرة واحدة لكل ليلة بعد إعادة المحاولات. لا شيء هنا يوقف التشغيل: كل خطأ يُسجل فقط.
#
# النتيجة ورمز الخروج (exit code) لكل سبب توقف (stop_reason):
#   done     0  الطابور انتهى (أو لم يكن فيه شيء)
#   skipped  0  تشغيل آخر حي يحمل القفل، فلم يبدأ هذا التشغيل
#   failed   1  خطأ يحتاج تدخلاً: إعداد الشيت، أو خطأ غير متوقع
#   outage   2  انقطاع: قاعدة البيانات، أو Google Sheets، أو محركات البحث (الليلي يعيد المحاولة بعد 15 ثم 60 دقيقة)
#   stopped  3  توقف قبل نهاية الطابور: طلب إيقاف من اللوحة، أو أي سبب آخر يكتبه العامل (مثل BUDGET_REACHED
#               أو SERPER_CREDIT) ويظهر نصه كما هو؛ الصفوف المتبقية تبقى في الانتظار
#
# التكلفة: من سجل الصرف إن وُجد (أول دالة موجودة من SPEND_LEDGER_FUNCTIONS في local_cache_db، لكل run_id)،
# وإلا تقدير ops_health من عمليات بحث هذا العامل يُحفظ مع التشغيل قبل أن يكتب تشغيل لاحق فوق trace الصفوف.
# التقدير لا يرى محاولات البحث التي أعيدت عند PROVIDER_DOWN (يُحفظ trace آخر محاولة فقط).
# تحديثات الشيت المعلقة من google_sheets.outbox_outcomes() إن وُجدت.

import datetime
import html
import json
import logging
import os
import time

logger = logging.getLogger(__name__)

LAST_REPORT_PATH = os.path.join("temp", "nightly", "last_report.json")
TELEGRAM_MAX_CHARS = 3500

EXIT_CODES = {"done": 0, "skipped": 0, "failed": 1, "outage": 2, "stopped": 3}
DONE_REASONS = ("", "queue_empty")
SKIP_REASONS = ("another_worker",)
# انقطاع قد يزول وحده: التشغيل الليلي يعيد التشغيل كله بعد 15 ثم 60 دقيقة
OUTAGE_REASONS = ("db_unavailable", "sheets_unavailable", "sheet_not_found", "provider_down")
FAILED_REASONS = ("sheet_config", "enqueue_failed", "enqueue_error", "worker_error")
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
    "another_worker": "تشغيل آخر يعمل الآن",
    "budget_reached": "بلغ صرف اليوم الميزانية اليومية (DAILY_BUDGET_USD)",
    "serper_credit": "رصيد Serper انتهى أو مفتاحه مرفوض",
}
OUTCOME_TEXT = {
    "done": "✅ اكتمل",
    "skipped": "⏭️ لم يبدأ",
    "stopped": "⏸️ توقف قبل نهاية الطابور",
    "outage": "🔌 انقطاع",
    "failed": "❌ فشل",
}
TRIGGER_TEXT = {"nightly": "التشغيل الليلي", "dashboard": "تشغيل من لوحة التحكم", "manual": "تشغيل يدوي"}


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
    return REASON_TEXT.get(key) or str(stop_reason)


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


def _iso(ts):
    return datetime.datetime.fromtimestamp(ts).isoformat(timespec="seconds") if ts else None


def build_report(trigger, attempts, started_ts, ended_ts, health=None, db=None, sheets=None):
    """
    التقرير من محاولات التشغيل (قائمة {stop_reason, run_id, worker_id, notice}، الأخيرة هي النتيجة):
    {trigger, started_at, ended_at, duration_s, outcome, stop_reason, reason_text, exit_code, attempts,
     attempt_reasons, run_id, run_ids, counts, outbox, spend, notices, database}.
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
        for part in str(a.get("notice") or a.get("message") or "").split(" | "):
            part = part.strip()
            if part and part not in notices:
                notices.append(part)
    report = {
        "trigger": trigger if trigger in TRIGGER_TEXT else "manual",
        "started_at": _iso(started_ts),
        "ended_at": _iso(ended_ts),
        "duration_s": int(max(0, (ended_ts or 0) - (started_ts or 0))) if started_ts and ended_ts else None,
        "outcome": outcome_of(stop_reason),
        "stop_reason": stop_reason,
        "reason_text": reason_text(stop_reason),
        "exit_code": exit_code(stop_reason),
        "attempts": len(attempts),
        "attempt_reasons": [a.get("stop_reason") for a in attempts[:-1]],
        "run_id": run_ids[-1] if run_ids else None,
        "run_ids": run_ids,
        "counts": None,
        "outbox": None,
        "spend": None,
        "notices": notices,
        "database": "unavailable" if _key(stop_reason) == "db_unavailable" else "ok",
    }
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
# النشر: run_history، last_report.json، Telegram
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
        "notices": " | ".join(report.get("notices") or []) or None,
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


def _duration(seconds):
    if seconds is None:
        return ""
    minutes = int(seconds) // 60
    return f"{minutes // 60} س {minutes % 60} د" if minutes >= 60 else f"{minutes} د"


def telegram_text(report):
    """رسالة Telegram عربية قصيرة (HTML) من التقرير؛ كل نص متغير مُهرّب."""
    esc = lambda value: html.escape(str(value), quote=False)  # noqa: E731
    outcome = report.get("outcome") or "failed"
    title = f"{OUTCOME_TEXT.get(outcome, outcome)} — {TRIGGER_TEXT.get(report.get('trigger'), 'تشغيل')}"
    lines = [f"<b>{esc(title)}</b>"]
    when = (report.get("started_at") or "").replace("T", " ")[:16]
    duration = _duration(report.get("duration_s"))
    if when:
        lines.append(esc(when) + (f" · المدة {esc(duration)}" if duration else ""))
    if report.get("reason_text"):
        lines.append(f"السبب: {esc(report['reason_text'])}")
    if (report.get("attempts") or 1) > 1:
        lines.append(f"المحاولات: {report['attempts']} (أُعيد التشغيل بعد انقطاع)")
    counts = report.get("counts")
    if counts:
        lines.append(f"أُضيف للطابور {counts.get('enqueued', 0)} · بُحث {counts.get('searched', 0)}")
        lines.append(f"بانتظار المراجعة {counts.get('ready_for_review', 0)} · نُشر تلقائياً {counts.get('auto_published', 0)}")
        lines.append(f"لم يُعثر على صورة {counts.get('not_found', 0)} · فشل {counts.get('failed', 0)}")
        if counts.get("pending_left"):
            lines.append(f"بقي في الانتظار {counts['pending_left']}"
                         + (f" (منها {counts['provider_down']} لأن محركات البحث لم ترد)" if counts.get("provider_down") else ""))
    elif report.get("database") == "unavailable":
        lines.append("الأرقام غير متاحة (قاعدة البيانات لا ترد).")
    outbox = report.get("outbox")
    if outbox and any(outbox.values()):
        lines.append(f"تحديثات الشيت: معلقة {outbox.get('pending', 0)} · تعارض {outbox.get('conflict', 0)}"
                     f" · فشلت نهائياً {outbox.get('dead', 0)}")
    spend = report.get("spend")
    if spend and spend.get("usd") is not None:
        lines.append(f"التكلفة: {spend['usd']:.2f}$" + (" (تقديرية)" if spend.get("source") == "estimate" else ""))
    for notice in (report.get("notices") or [])[:3]:
        lines.append(f"⚠️ {esc(str(notice)[:200])}")
    return "\n".join(lines)[:TELEGRAM_MAX_CHARS]


def notify(report, sender=None, config_module=None):
    """رسالة Telegram فقط عندما يكون مضبوطاً؛ sender(text) -> bool (افتراضياً config.send_telegram_alert)."""
    if config_module is None:
        import config as config_module
    if not config_module.telegram_configured():
        return False
    try:
        return bool((sender or config_module.send_telegram_alert)(telegram_text(report)))
    except Exception as e:
        logger.warning("run_report: telegram failed (%s)", type(e).__name__)
        return False


def publish(report, db=None, path=LAST_REPORT_PATH, sender=None, config_module=None):
    """يحفظ التقرير في run_history ثم يرسله عبر Telegram ثم يكتب last_report.json؛ يطبع ملخصه في السجل."""
    if db is None:
        import local_cache_db as db
    history_id = None
    try:
        history_id = db.save_run_history(history_entry(report))
    except Exception as e:
        logger.warning("run_report: run_history not saved: %s", e)
    report["history_id"] = history_id
    report["telegram_sent"] = notify(report, sender=sender, config_module=config_module)
    write_last_report(report, path)
    counts = report.get("counts") or {}
    print(f"[Run Report] {report['outcome']} (exit {report['exit_code']})"
          + (f": {report['reason_text']}" if report.get("reason_text") else "")
          + (f" | للمراجعة {counts.get('ready_for_review', 0)}، لم يُعثر {counts.get('not_found', 0)}، "
             f"فشل {counts.get('failed', 0)}" if counts else "")
          + ("" if history_id else " | لم يُحفظ في سجل التشغيلات"), flush=True)
    return report


def report_worker_run(info, trigger="manual", db=None, sender=None, path=LAST_REPORT_PATH):
    """تقرير عامل واحد (لوحة التحكم أو يدوي) من main.LAST_WORKER."""
    report = build_report(trigger, [info], info.get("started_ts"), info.get("ended_ts") or time.time(),
                          health=info.get("health"), db=db)
    return publish(report, db=db, path=path, sender=sender)
