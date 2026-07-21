# verification_layer/presentation/verification_router.py
import io
import base64
from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from PIL import Image

from verification_layer.domain.models import CatalogProduct, CatalogType, SurfaceCurvature
from verification_layer.use_cases.catalog_verifier import CatalogVerificationPipeline
from verification_layer.infrastructure.siglip_embedding_adapter import SigLIPVisualEmbeddingAdapter
from verification_layer.infrastructure.vlm_adjudicator_adapter import VLMAdjudicatorAdapter
from verification_layer.infrastructure.yolo_logo_adapter import YOLOLogoDetectorAdapter

router = APIRouter(prefix="/api", tags=["Verification & Validation Layer"])

# Instantiate single pipeline instance
_pipeline = CatalogVerificationPipeline(
    visual_engine=SigLIPVisualEmbeddingAdapter(),
    vlm_adjudicator=VLMAdjudicatorAdapter(),
    logo_detector=YOLOLogoDetectorAdapter(),
)


class VerifyCatalogRequest(BaseModel):
    image_base64: Optional[str] = None
    image_url: Optional[str] = None
    brand: str
    product_class: str
    weight_volume: str
    catalog_type: Optional[str] = "global_brand"
    surface_curvature: Optional[str] = "flat"


@router.post("/verify-catalog-image")
async def verify_catalog_image(request: VerifyCatalogRequest):
    """
    نقطة تحقق واعتماد صور المنتجات وتحديد القرار التشغيلي تلقائياً
    """
    if not request.image_base64 and not request.image_url:
        raise HTTPException(status_code=400, detail="Missing image data: provide image_base64 or image_url.")

    try:
        if request.image_base64:
            img_data = base64.b64decode(request.image_base64)
            img = Image.open(io.BytesIO(img_data))
        else:
            import requests
            resp = requests.get(request.image_url, timeout=10)
            img = Image.open(io.BytesIO(resp.content))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to load image: {e}")

    catalog_meta = CatalogProduct(
        asin_or_gtin="SYSTEM_VERIFY",
        brand=request.brand,
        product_class=request.product_class,
        weight_volume=request.weight_volume,
        catalog_type=CatalogType(request.catalog_type.lower()) if request.catalog_type else CatalogType.GLOBAL_BRAND,
        surface_curvature=SurfaceCurvature(request.surface_curvature.lower()) if request.surface_curvature else SurfaceCurvature.FLAT,
    )

    result = _pipeline.verify(img, catalog_meta)

    return {
        "overall_passed": result.overall_passed,
        "decision": result.decision.value,
        "scores": {
            "fusion_score": result.fusion_score,
            "visual_similarity_score": result.visual_similarity_score,
            "ocr_similarity_score": result.ocr_similarity_score,
            "vlm_score": result.vlm_score,
        },
        "gate_evaluations": [
            {
                "gate_name": g.gate_name,
                "passed": g.passed,
                "score": g.score,
                "reason": g.reason,
                "details": g.details,
            }
            for g in result.gate_evaluations
        ],
        "adjudication_report": {
            "brand_match_status": result.adjudication_report.brand_evaluation.match_status.value,
            "product_class_match_status": result.adjudication_report.product_class_evaluation.match_status.value,
            "weight_volume_match_status": result.adjudication_report.weight_volume_evaluation.match_status.value,
            "is_retail_packaging": result.adjudication_report.packaging_integrity_evaluation.is_retail_packaging,
            "justification": result.adjudication_report.justification_narrative,
        }
        if result.adjudication_report
        else None,
        "rejection_reasons": result.rejection_reasons,
    }
