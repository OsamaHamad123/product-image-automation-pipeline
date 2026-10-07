# stop_signals.py
# إيقاف نظيف عند SIGTERM / SIGHUP (systemctl stop، مهلة systemd، إعادة تشغيل الجهاز، إغلاق الطرفية).
#
# - بدون معالج، بايثون يموت فوراً عند SIGTERM: لا تعمل كتل finally، فيبقى القفل temp/pipeline.lock والصفوف
#   'processing' وحالة automation_state عالقة، ولا يُكتب تقرير.
# - المعالج يُثبت في الخيط الرئيسي فقط (install)، ويرفع KeyboardInterrupt مرة واحدة: نفس مسار Ctrl+C «توقف»
#   (stopped، رمز الخروج 3) الموجود أصلاً في main.run_worker_mode والتشغيل الليلي.
# - الإشارة الثانية لا ترفع شيئاً: التنظيف الجاري (كتابة الحالة، تحرير القفل، التقرير) يكمل.
# - deferred(): تنظيف يجب أن يكتمل؛ إشارة تصل داخله تُسجل فقط (requested()) ولا تقطعه.
# - لا استيرادات ثقيلة: يُستورد قبل config (الذي يتصل بقاعدة البيانات عند الاستيراد).

import contextlib
import signal
import threading

STOP_SIGNALS = ("SIGTERM", "SIGHUP")

_STATE = {"signal": None, "deferred": 0, "raised": False}


def _handler(signum, frame):
    try:
        name = signal.Signals(signum).name
    except ValueError:
        name = str(signum)
    if _STATE["signal"] is None:
        _STATE["signal"] = name
    if _STATE["raised"] or _STATE["deferred"] > 0:
        return
    _STATE["raised"] = True
    raise KeyboardInterrupt(f"stop signal {name}")


def install(names=STOP_SIGNALS):
    """
    يثبت المعالج لهذه الإشارات (SIGHUP غير موجودة في ويندوز فتُتخطى). من الخيط الرئيسي فقط: من خيط آخر لا يفعل
    شيئاً ويعيد []. يعيد أسماء الإشارات المثبتة.
    """
    if threading.current_thread() is not threading.main_thread():
        return []
    installed = []
    for name in names:
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        try:
            signal.signal(sig, _handler)
        except (ValueError, OSError):
            continue
        installed.append(name)
    return installed


def requested():
    """اسم أول إشارة إيقاف وصلت (مثل 'SIGTERM')، أو None."""
    return _STATE["signal"]


@contextlib.contextmanager
def deferred():
    """تنظيف يجب أن يكتمل: إشارة إيقاف تصل داخله تُسجل (requested()) ولا ترفع KeyboardInterrupt."""
    _STATE["deferred"] += 1
    try:
        yield
    finally:
        _STATE["deferred"] -= 1


def reset():
    """للاختبارات وبداية عملية جديدة: لا إشارة مسجلة."""
    _STATE.update(signal=None, deferred=0, raised=False)
