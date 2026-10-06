# atomic_file.py
# كتابة ذرية لملفات الحالة JSON (كاش الشيت products_cache.json، تقدم الدفعة batch_progress.json ...).
#
# open(path, "w") يفرّغ الملف أولاً: قارئ في عملية أخرى (اللوحة، الجسر) أو انقطاع الكهرباء أثناء الكتابة يترك ملفاً
# فارغاً أو نصف مكتوب. هنا: ملف مؤقت باسم فريد في المجلد نفسه، ثم os.replace (ذري على نفس نظام الملفات).
# على ويندوز يفشل الاستبدال لحظة يكون الملف مفتوحاً للقراءة: يُعاد بعد لحظة، ثم يُرفع الخطأ (والملف المؤقت يُحذف).

import json
import os
import threading
import time


def write_json(path, data, attempts=5, **dump_kwargs):
    """يكتب data بصيغة JSON في path ذرياً. dump_kwargs تمرر لـ json.dump (ensure_ascii، indent ...). الأخطاء تُرفع."""
    folder = os.path.dirname(path) or "."
    os.makedirs(folder, exist_ok=True)
    tmp = os.path.join(folder, f".{os.path.basename(path)}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, **dump_kwargs)
        for attempt in range(attempts):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                if attempt == attempts - 1:
                    raise
                time.sleep(0.05)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
