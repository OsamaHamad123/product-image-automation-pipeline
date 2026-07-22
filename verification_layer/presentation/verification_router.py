# verification_layer/presentation/verification_router.py
import io
import base64
import numpy as np
from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from pydantic import BaseModel
from typing import Optional, List, Dict, Any, Tuple
from PIL import Image

from verification_layer.domain.models import CatalogProduct, CatalogType, SurfaceCurvature
from verification_layer.use_cases.catalog_verifier import CatalogVerificationPipeline
from verification_layer.infrastructure.siglip_embedding_adapter import SigLIPVisualEmbeddingAdapter
from verification_layer.infrastructure.vlm_adjudicator_adapter import VLMAdjudicatorAdapter
from verification_layer.infrastructure.yolo_logo_adapter import YOLOLogoDetectorAdapter

# Next-Gen Frontiers Imports
from verification_layer.domain.nextgen_models import LabColor, ProductBrandSpecs
from verification_layer.use_cases.speculative_search_engine import SpeculativeSearchEngine
from verification_layer.use_cases.multimodal_spec_audit import MultiModalSpecAuditUseCase
from verification_layer.use_cases.spatial_packaging_density import SpatialPackagingDensityUseCase
from verification_layer.use_cases.cavi_aesthetic_engine import CAVIAestheticEngineUseCase
from verification_layer.use_cases.spectral_color_fidelity import SpectralColorFidelityUseCase, rgb_to_lab

router = APIRouter(prefix="/api", tags=["Verification & Validation Layer"])

# Instantiate pipeline instances
_pipeline = CatalogVerificationPipeline(
    visual_engine=SigLIPVisualEmbeddingAdapter(),
    vlm_adjudicator=VLMAdjudicatorAdapter(),
    logo_detector=YOLOLogoDetectorAdapter(),
)

_speculative_search = SpeculativeSearchEngine()
_multimodal_audit = MultiModalSpecAuditUseCase()
_spatial_density = SpatialPackagingDensityUseCase()
_cavi_engine = CAVIAestheticEngineUseCase()
_color_fidelity = SpectralColorFidelityUseCase()


class VerifyCatalogRequest(BaseModel):
    image_base64: Optional[str] = None
    image_url: Optional[str] = None
    brand: str
    product_class: str
    weight_volume: str
    catalog_type: Optional[str] = "global_brand"
    surface_curvature: Optional[str] = "flat"


class SpeculativeSearchRequest(BaseModel):
    query: str
    fallback_gtin: Optional[str] = None
    simulate_vector_failure: Optional[bool] = False
    simulate_lexical_failure: Optional[bool] = False


class SpecAuditRequest(BaseModel):
    target_spec_text: str
    detected_text_boxes: List[Dict[str, Any]]
    expected_box: Optional[Tuple[int, int, int, int]] = (10, 10, 200, 200)


class PackagingDensityRequest(BaseModel):
    image_base64: Optional[str] = None


class CAVIAestheticRequest(BaseModel):
    image_base64: Optional[str] = None


class ColorFidelityRequest(BaseModel):
    gtin: str
    brand_name: str
    sample_rgb: Tuple[int, int, int]
    target_l_star: Optional[float] = 50.0
    target_a_star: Optional[float] = 20.0
    target_b_star: Optional[float] = -10.0
    tolerance: Optional[float] = 3.5


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


# ----------------- Next-Gen Frontiers API Endpoints -----------------

@router.post("/nextgen/speculative-search")
async def api_nextgen_speculative_search(request: SpeculativeSearchRequest):
    res = await _speculative_search.execute_speculative_search(
        query=request.query,
        fallback_gtin=request.fallback_gtin,
        fail_vector=request.simulate_vector_failure,
        fail_lexical=request.simulate_lexical_failure
    )
    return {
        "query": res.query,
        "source": res.source,
        "consensus_score": res.consensus_score,
        "candidates": res.candidates,
        "circuit_status": res.circuit_status
    }


@router.post("/nextgen/multimodal-spec-audit")
def api_nextgen_multimodal_spec_audit(request: SpecAuditRequest):
    res = _multimodal_audit.audit_spec_consistency(
        target_spec_text=request.target_spec_text,
        detected_text_boxes=request.detected_text_boxes,
        expected_bounding_box=request.expected_box
    )
    return {
        "spec_text": res.spec_text,
        "text_similarity": res.text_similarity,
        "iou_score": res.iou_score,
        "matched_bounding_box": res.matched_bounding_box,
        "is_consistent": res.is_consistent,
        "status": res.status
    }


@router.post("/nextgen/packaging-density")
def api_nextgen_packaging_density(request: PackagingDensityRequest):
    if request.image_base64:
        img_bytes = base64.b64decode(request.image_base64)
        pil_img = Image.open(io.BytesIO(img_bytes)).convert("L")
        np_arr = np.array(pil_img)
        binary_mask = (np_arr > 50).astype(np.uint8)
    else:
        # Synthetic binary mask centered
        binary_mask = np.zeros((100, 100), dtype=np.uint8)
        binary_mask[20:80, 20:80] = 1

    res = _spatial_density.evaluate_mask_density(binary_mask)
    return {
        "hull_area": res.hull_area,
        "bbox_area": res.bbox_area,
        "packaging_ratio": res.packaging_ratio,
        "is_efficient": res.is_efficient,
        "status_label": res.status_label,
        "hull_vertex_count": res.hull_vertex_count
    }


@router.post("/nextgen/cavi-aesthetic")
def api_nextgen_cavi_aesthetic(request: CAVIAestheticRequest):
    if request.image_base64:
        img_bytes = base64.b64decode(request.image_base64)
        pil_img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        rgb_arr = np.array(pil_img)
    else:
        # Synthetic test image
        rgb_arr = np.random.randint(0, 255, (100, 100, 3), dtype=np.uint8)

    res = _cavi_engine.evaluate_image_cavi(rgb_arr)
    return {
        "focus_variance": res.focus_variance,
        "color_entropy": res.color_entropy,
        "canvas_cleanliness": res.canvas_cleanliness,
        "composite_cavi": res.composite_cavi,
        "viability_rank": res.viability_rank
    }


@router.post("/nextgen/color-fidelity")
def api_nextgen_color_fidelity(request: ColorFidelityRequest):
    extracted_lab = rgb_to_lab(request.sample_rgb)
    specs = ProductBrandSpecs(
        gtin=request.gtin,
        brand_name=request.brand_name,
        target_colors=[
            LabColor(l_star=request.target_l_star, a_star=request.target_a_star, b_star=request.target_b_star, label="Brand Standard")
        ],
        allowed_tolerance=request.tolerance
    )

    res = _color_fidelity.verify_brand_color_compliance(extracted_lab, specs)
    return {
        "gtin": res.gtin,
        "brand_name": res.brand_name,
        "delta_e2000": res.delta_e2000,
        "perception_level": res.perception_level,
        "brand_decision": res.brand_decision,
        "approved": res.approved
    }


class GTINVerifyRequest(BaseModel):
    gtin: str


class FullCatalogAuditRequest(BaseModel):
    sku: str
    title_ar: str
    title_en: str
    brand: str
    image_base64: Optional[str] = None
    capacity_l: Optional[float] = 1.5


@router.post("/nextgen/gtin-verify")
def api_nextgen_gtin_verify(request: GTINVerifyRequest):
    from verification_layer.use_cases.gtin_checksum_verifier import HybridBarcodeEngine
    is_valid = HybridBarcodeEngine.is_valid_gtin13(request.gtin)
    return {
        "gtin": request.gtin,
        "is_valid_ean13": is_valid,
        "status": "VALID_GTIN_BARCODE" if is_valid else "INVALID_GTIN_CHECKSUM"
    }


@router.post("/nextgen/full-catalog-audit")
def api_nextgen_full_catalog_audit(request: FullCatalogAuditRequest):
    from verification_layer.domain.nextgen_models import Product
    from verification_layer.use_cases.process_catalog_audit_pipeline import ProcessCatalogAuditUseCase

    if request.image_base64:
        img_bytes = base64.b64decode(request.image_base64)
        pil_img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    else:
        # Create synthetic high quality RGB test image
        pil_img = Image.new("RGB", (200, 200), color=(255, 255, 255))

    prod = Product(
        sku=request.sku,
        title_ar=request.title_ar,
        title_en=request.title_en,
        specifications={"capacity_l": request.capacity_l},
        expected_brand=request.brand,
        expected_colors_lab=[[53.2, 80.1, 67.2]]
    )

    audit_pipeline = ProcessCatalogAuditUseCase()
    res = audit_pipeline.audit_image_pil(pil_img, prod)

    return {
        "success": res.success,
        "detected_mime": res.detected_mime,
        "colorfulness": res.colorfulness,
        "sharpness": res.sharpness,
        "shadow_preserved": res.shadow_preserved,
        "is_brand_matched": res.is_brand_matched,
        "color_delta_e_deviations": res.color_delta_e_deviations,
        "spatial_packaging_ratio": res.spatial_packaging_ratio,
        "aesthetic_score": res.aesthetic_score,
        "spec_consistency_score": res.spec_consistency_score,
        "live_metrics": res.live_metrics
    }


class HealImageApiRequest(BaseModel):
    product_id: int
    broken_url: str
    candidates_a: Optional[List[str]] = None
    candidates_b: Optional[List[str]] = None


@router.post("/v1/fix-broken-image-link")
async def api_fix_broken_image_link(payload: HealImageApiRequest):
    from verification_layer.use_cases.heal_image import HealImageUseCase
    heal_uc = HealImageUseCase()

    candidates_a = payload.candidates_a or [
        f"https://images.pexels.com/photos/1000/test_product_{payload.product_id}.jpg",
        f"https://via.placeholder.com/800/0000FF/888888.png"
    ]
    candidates_b = payload.candidates_b or [
        f"https://via.placeholder.com/800/0000FF/888888.png",
        f"https://images.pexels.com/photos/1001/alt_product_{payload.product_id}.jpg"
    ]

    result = await heal_uc.execute(
        product_id=payload.product_id,
        broken_url=payload.broken_url,
        candidates_a=candidates_a,
        candidates_b=candidates_b
    )

    return {
        "product_id": result["product_id"],
        "resolved_url": result["resolved_url"],
        "match_percentage": result["match_percentage"],
        "status": result["status"]
    }


class ResolveIntentRequest(BaseModel):
    query_text: str


class EvaluateMerchantAuthorityRequest(BaseModel):
    page_url: str
    image_url: str
    json_ld_schema_str: Optional[str] = None


class CatalogVisualAuditRequest(BaseModel):
    user_query_text: str
    page_url: str
    image_url: str
    json_ld_schema_str: Optional[str] = None


@router.post("/nextgen/resolve-search-intent")
def api_nextgen_resolve_search_intent(request: ResolveIntentRequest):
    from verification_layer.use_cases.search_intent_resolver import SearchIntentResolverUseCase
    resolver = SearchIntentResolverUseCase()
    res = resolver.resolve_query_intent(request.query_text)
    return {
        "raw_text": res.raw_text,
        "cleaned_text": res.cleaned_text,
        "parsed_units": res.parsed_units,
        "intent": res.intent.value
    }


@router.post("/nextgen/evaluate-merchant-authority")
def api_nextgen_evaluate_merchant_authority(request: EvaluateMerchantAuthorityRequest):
    from verification_layer.use_cases.serp_schema_extractor import SERPProductSchemaExtractor
    extractor = SERPProductSchemaExtractor()
    schema_data = {"completeness_score": 0.0}
    if request.json_ld_schema_str:
        schema_data = extractor.parse_json_ld_schema(request.json_ld_schema_str)

    mas = extractor.calculate_merchant_authority(
        page_url=request.page_url,
        image_url=request.image_url,
        schema_completeness=schema_data["completeness_score"]
    )
    return {
        "page_url": request.page_url,
        "image_url": request.image_url,
        "schema_data": schema_data,
        "merchant_authority_score": mas
    }


@router.post("/nextgen/catalog-visual-audit")
def api_nextgen_catalog_visual_audit(request: CatalogVisualAuditRequest):
    from verification_layer.use_cases.catalog_visual_audit_orchestrator import CatalogVisualAuditOrchestrator
    orchestrator = CatalogVisualAuditOrchestrator()

    # Synthetic mock PNG bytes for testing endpoint
    import struct
    signature = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR'
    dimensions = struct.pack('>II', 600, 600)
    mock_bytes = signature + dimensions + b'\x08\x06\x00\x00\x00\x11\x22\x33\x44'

    res = orchestrator.audit_serp_product_image(
        user_query_text=request.user_query_text,
        page_url=request.page_url,
        image_url=request.image_url,
        pre_fetched_bytes=mock_bytes,
        json_ld_schema_str=request.json_ld_schema_str
    )
    return res


class ExecuteSheetsDeltaSyncRequest(BaseModel):
    batch_size: int = 100
    sheet_range: str = "Catalog!A2:E"
    events: Optional[List[Dict[str, Any]]] = None


@router.post("/nextgen/execute-sheets-delta-sync")
def api_nextgen_execute_sheets_delta_sync(request: ExecuteSheetsDeltaSyncRequest):
    from verification_layer.use_cases.delta_catalog_sync import DeltaCatalogSyncUseCase
    from verification_layer.infrastructure.google_sheets_bulk_adapter import (
        MySQLConnectionPoolAdapter,
        RedisWriteBehindBufferAdapter,
        GoogleSheetsBulkGatewayAdapter
    )

    db_adapter = MySQLConnectionPoolAdapter()
    cache_adapter = RedisWriteBehindBufferAdapter()
    sheet_adapter = GoogleSheetsBulkGatewayAdapter()

    # Seed events if provided
    events = request.events or [
        {"product_id": "101", "sku": "SKU-101", "title": "المنتج 101", "price": 49.99, "stock": 50},
        {"product_id": "102", "sku": "SKU-102", "title": "المنتج 102", "price": 89.99, "stock": 25}
    ]

    for evt in events:
        cache_adapter.push_to_events_queue(evt)

    sync_use_case = DeltaCatalogSyncUseCase(
        db_adapter=db_adapter,
        cache_adapter=cache_adapter,
        sheet_adapter=sheet_adapter
    )

    synced_count = sync_use_case.execute_sync_cycle(
        batch_size=request.batch_size,
        sheet_range=request.sheet_range
    )

    return {
        "success": True,
        "synced_products_count": synced_count,
        "input_mode": "RAW",
        "delta_hashing": "SHA-256",
        "status": "DELTA_SYNC_COMPLETED"
    }


@router.get("/v1/curation/stream/{session_id}")
async def api_stream_curation_events(session_id: str, request: Request, last_event_id: Optional[str] = Header(None, alias="Last-Event-ID")):
    from fastapi.responses import StreamingResponse
    from verification_layer.use_cases.process_event_stream import RedisSseRepository, ProcessEventStreamUseCase

    repository = RedisSseRepository()
    use_case = ProcessEventStreamUseCase(repository)

    async def sse_event_generator():
        async for evt in use_case.execute(session_id, last_event_id):
            if await request.is_disconnected():
                break
            payload_str = f"id: {evt['id']}\nevent: {evt['event']}\ndata: {evt['data']}\nretry: {evt.get('retry', 3000)}\n\n"
            yield payload_str.encode("utf-8")

    return StreamingResponse(
        sse_event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive"
        }
    )


class RankFusionRequest(BaseModel):
    dense_results: List[str] = Field(default_factory=list)
    lexical_results: List[str] = Field(default_factory=list)


@router.get("/v1/image-proxy")
async def api_secure_image_proxy(url: str = Query(..., description="Target image URL to proxy safely")):
    from fastapi.responses import Response, StreamingResponse
    import urllib.request
    from verification_layer.use_cases.image_proxy_service import ImageProxyService, detect_magic_bytes_mime

    service = ImageProxyService()
    try:
        secure_url, headers = service.prepare_proxy_request(url)
        req = urllib.request.Request(secure_url, headers=headers)
        with urllib.request.urlopen(req, timeout=6) as resp:
            content = resp.read()

        detected_mime = detect_magic_bytes_mime(content[:32]) or "image/jpeg"
        return Response(
            content=content,
            media_type=detected_mime,
            headers={
                "Cache-Control": "public, max-age=2592000",
                "Access-Control-Allow-Origin": "*",
                "X-Content-Type-Options": "nosniff"
            }
        )
    except Exception as ex:
        raise HTTPException(status_code=400, detail=f"Proxy error: {str(ex)}")


@router.post("/v1/rank-fusion")
def api_process_rank_fusion(request: RankFusionRequest, k: int = Query(60, ge=1)):
    from verification_layer.use_cases.rank_fusion_service import RankFusionService

    service = RankFusionService(k=k, floor_threshold=65.0)
    result = service.evaluate_fusion(request.dense_results, request.lexical_results)
    return {
        "success": True,
        **result
    }


@router.post("/v1/extract-embeddings")
def api_extract_visual_embeddings():
    import numpy as np
    from verification_layer.use_cases.onnx_clip_embedder import OnnxClipEmbedder

    embedder = OnnxClipEmbedder()
    dummy_pixels = np.zeros((224, 224, 3), dtype=np.uint8)
    vector = embedder.extract_embeddings(dummy_pixels)

    return {
        "success": True,
        "dimension": len(vector),
        "l2_norm": float(np.linalg.norm(vector, ord=2)),
        "vector_sample": vector[:5].tolist()
    }







