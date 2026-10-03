# fastapi_server.py
# غلاف HTTP رفيع فوق دوال cli_bridge (نفس السلوك تماماً).
# ملاحظة (D12): لوحة Laravel تستدعي cli_bridge.py مباشرة، ولا يشغّل أي مُشغّل هذا الخادم.
# يبقى قابلاً للاستيراد والتشغيل يدوياً (python fastapi_server.py) لأغراض التطوير فقط.

import json
import os
import subprocess
import sys
from contextlib import asynccontextmanager
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

# إضافة المجلد الحالي للمسار لضمان الاستيراد بشكل صحيح
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import cli_bridge
import local_cache_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    local_cache_db.init_db()
    yield


app = FastAPI(
    title="Product Image Automation API",
    description="غلاف HTTP لدوال cli_bridge (للتطوير فقط؛ لوحة التحكم تستخدم CLI)",
    version="2.0.0",
    lifespan=lifespan,
)


# ----------------- نماذج البيانات -----------------

class SearchRequest(BaseModel):
    product_name: str
    brand: Optional[str] = ""
    product_name_ar: Optional[str] = ""
    brand_ar: Optional[str] = ""
    custom_query: Optional[str] = ""
    barcode: Optional[str] = ""
    category: Optional[str] = ""
    size: Optional[str] = ""
    row_number: Optional[int] = None
    sku_key: Optional[str] = ""
    skip_cache: Optional[bool] = False
    exclude_urls: List[str] = []


class SelectImageRequest(BaseModel):
    image_url: str
    product_name: str
    brand: Optional[str] = ""
    row_number: int
    barcode: Optional[str] = ""
    sku_key: Optional[str] = ""
    content_sha256: Optional[str] = ""
    target_width: Optional[int] = 0
    target_height: Optional[int] = 0
    bg_removal_method: Optional[str] = None
    enhance: Optional[bool] = False
    category_l1_en: Optional[str] = ""
    category_l2_en: Optional[str] = ""
    category_l3_en: Optional[str] = ""


class RejectImageRequest(BaseModel):
    image_url: str
    row_number: int
    reason_code: str
    product_name: Optional[str] = ""
    brand: Optional[str] = ""
    barcode: Optional[str] = ""
    sku_key: Optional[str] = ""
    research: Optional[bool] = False


class UploadManualRequest(BaseModel):
    file_path: str
    row_number: int
    product_name: str
    brand: Optional[str] = ""
    barcode: Optional[str] = ""
    sku_key: Optional[str] = ""
    target_width: Optional[int] = 0
    target_height: Optional[int] = 0
    enhance: Optional[bool] = False


def _dump(model):
    return model.model_dump() if hasattr(model, "model_dump") else model.dict()


def _or_500(result):
    # رسائل cli_bridge ثابتة ولا تحمل نص الاستثناء؛ تفاصيله في temp/search.log وصفحة الأخطاء.
    if result.get("status") in ("failed", "error"):
        raise HTTPException(status_code=500, detail=result.get("error") or "failed")
    return result


# ----------------- نقاط النهاية -----------------

@app.get("/")
def read_root():
    return {"status": "online", "transport": "development wrapper over cli_bridge"}


@app.post("/api/sheet-preview")
def preview_sheet(payload: dict):
    return _or_500(cli_bridge.action_sheet_preview(payload))


@app.post("/api/sheet-save")
def save_sheet_config(payload: dict):
    return _or_500(cli_bridge.action_sheet_save(payload))


@app.get("/api/products")
def get_products():
    return _or_500(cli_bridge.action_get_products({}))


@app.post("/api/search")
def search_product_image(req: SearchRequest):
    # حالات review / not_found / provider_down نتائج مشروعة وتُعاد كما هي
    result = cli_bridge.action_search(_dump(req))
    if result.get("status") == "error":
        raise HTTPException(status_code=500, detail=result.get("error"))
    return result


@app.post("/api/select-image")
def select_product_image(req: SelectImageRequest):
    return _or_500(cli_bridge.action_select_image(_dump(req)))


@app.post("/api/reject-image")
def reject_product_image(req: RejectImageRequest):
    result = cli_bridge.action_reject_image(_dump(req))
    if result.get("status") == "error" and "reason_code" in (result.get("error") or ""):
        raise HTTPException(status_code=400, detail=result.get("error"))
    return _or_500(result)


@app.post("/api/upload-manual-image")
def upload_manual_image(req: UploadManualRequest):
    return _or_500(cli_bridge.action_upload_manual_image(_dump(req)))


@app.get("/api/batch-status")
def get_batch_status():
    """هل عامل الخلفية يعمل (حسب ملف القفل)؟"""
    lock_file = os.path.join("temp", "pipeline.lock")
    if not os.path.exists(lock_file):
        return {"is_running": False}
    try:
        with open(lock_file, "r") as f:
            pid = f.read().strip()
        if pid.startswith("{"):          # main.write_lock: JSON {pid, host, started_at, ...}
            pid = str(json.loads(pid).get("pid", ""))
        if not pid.isdigit():
            return {"is_running": False}
        if os.name == "nt":
            output = subprocess.check_output(["tasklist", "/FI", f"PID eq {pid}"]).decode(errors="replace")
            return {"is_running": pid in output}
        os.kill(int(pid), 0)
        return {"is_running": True}
    except Exception:
        return {"is_running": False}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8001)
