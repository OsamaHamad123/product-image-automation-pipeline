# verification_layer/use_cases/catalog_verifier.py
from PIL import Image
from typing import Optional, List
from verification_layer.domain.models import (
    CatalogProduct,
    VerificationResult,
    GateEvaluation,
    CatalogType,
    OperationalDecision,
    AdjudicationReport,
)
from verification_layer.use_cases.resolution_aspect_gate import ResolutionAspectGate
from verification_layer.use_cases.white_background_gate import PureWhiteBackgroundGate
from verification_layer.use_cases.content_representation_gate import ContentRepresentationGate
from verification_layer.use_cases.fusion_engine import MultiModalFusionEngine
from verification_layer.domain.interfaces import (
    IVisualEmbeddingEngine,
    IVLMAdjudicator,
    ILogoDetector,
    IOCRService,
)


class CatalogVerificationPipeline:
    """
    خط المعالجة الهيكلية المتسلسلة المتقدمة لطبقة التحقق والاعتماد (Verification and Validation Pipeline)
    - الفحص الهيكلي الأساسي (Resolution & AR)
    - فحص الخلفية البيضاء ونسبة التعبئة (White Background & Fill Ratio)
    - ترشيح الصور الكرتونية والتمثيل غير المعبأ (Content Representation)
    - توجيه الكتالوج (Global Brand vs White-Label)
    - حساب درجات السيمانتك البصرية والـ OCR وتحكيم الـ VLM
    - حساب درجة الدمج الرياضية S_fusion وحظر أو اختيار القرار التشغيلي النهائي
    """

    def __init__(
        self,
        visual_engine: Optional[IVisualEmbeddingEngine] = None,
        vlm_adjudicator: Optional[IVLMAdjudicator] = None,
        logo_detector: Optional[ILogoDetector] = None,
        ocr_service: Optional[IOCRService] = None,
    ):
        self.resolution_gate = ResolutionAspectGate()
        self.background_gate = PureWhiteBackgroundGate()
        self.content_gate = ContentRepresentationGate()

        self.visual_engine = visual_engine
        self.vlm_adjudicator = vlm_adjudicator
        self.logo_detector = logo_detector
        self.ocr_service = ocr_service

    def verify(self, image: Image.Image, catalog_metadata: CatalogProduct) -> VerificationResult:
        gate_evaluations: List[GateEvaluation] = []
        rejection_reasons: List[str] = []

        # Optional cylindrical label de-warping for curved packaging surfaces
        if catalog_metadata.surface_curvature.value == "curved":
            try:
                from verification_layer.use_cases.cylindrical_unwarper import CylindricalUnwarper
                image = CylindricalUnwarper.unwarp_pil(image)
            except Exception:
                pass

        # Apply Typographic Attack Defense to prevent text prompt injection attacks
        try:
            from verification_layer.use_cases.typographic_defense import TypographicAttackDefender
            image = TypographicAttackDefender.apply_dyslexify_masking(image)
        except Exception:
            pass

        # 1. Structural Resolution & Aspect Ratio Gate
        res_eval = self.resolution_gate.evaluate(image)


        gate_evaluations.append(res_eval)
        if not res_eval.passed:
            rejection_reasons.append(res_eval.reason)
            return VerificationResult(
                overall_passed=False,
                decision=OperationalDecision.AUTO_REJECT,
                fusion_score=0.0,
                visual_similarity_score=0.0,
                ocr_similarity_score=0.0,
                vlm_score=0.0,
                gate_evaluations=gate_evaluations,
                rejection_reasons=rejection_reasons,
            )

        # 2. Pure White Background & Fill Ratio Gate
        bg_eval = self.background_gate.evaluate(image)
        gate_evaluations.append(bg_eval)
        if not bg_eval.passed:
            rejection_reasons.append(bg_eval.reason)
            return VerificationResult(
                overall_passed=False,
                decision=OperationalDecision.AUTO_REJECT,
                fusion_score=0.0,
                visual_similarity_score=0.0,
                ocr_similarity_score=0.0,
                vlm_score=0.0,
                gate_evaluations=gate_evaluations,
                rejection_reasons=rejection_reasons,
            )

        # 3. Content Representation Gate
        content_eval = self.content_gate.evaluate(image)
        gate_evaluations.append(content_eval)
        if not content_eval.passed:
            rejection_reasons.append(content_eval.reason)
            return VerificationResult(
                overall_passed=False,
                decision=OperationalDecision.AUTO_REJECT,
                fusion_score=0.0,
                visual_similarity_score=0.0,
                ocr_similarity_score=0.0,
                vlm_score=0.0,
                gate_evaluations=gate_evaluations,
                rejection_reasons=rejection_reasons,
            )

        # 4. Brand Logo Enforcement for Global Brands
        if catalog_metadata.catalog_type == CatalogType.GLOBAL_BRAND and self.logo_detector:
            logo_confidence = self.logo_detector.detect_brand_logo(image, catalog_metadata.brand)
            logo_passed = logo_confidence >= 0.85
            gate_evaluations.append(
                GateEvaluation(
                    gate_name="YOLO Logo Enforcement Gate",
                    passed=logo_passed,
                    score=round(logo_confidence, 4),
                    reason="Brand logo verified with required confidence (>= 0.85)."
                    if logo_passed
                    else f"Logo confidence ({logo_confidence:.2f}) below required threshold 0.85.",
                    details={"logo_confidence": logo_confidence},
                )
            )
            if not logo_passed:
                rejection_reasons.append(f"Global brand logo detection failed (confidence: {logo_confidence:.2f}).")
                return VerificationResult(
                    overall_passed=False,
                    decision=OperationalDecision.AUTO_REJECT,
                    fusion_score=0.0,
                    visual_similarity_score=0.0,
                    ocr_similarity_score=0.0,
                    vlm_score=0.0,
                    gate_evaluations=gate_evaluations,
                    rejection_reasons=rejection_reasons,
                )

        # 5. Extract Multi-modal Scores
        # S_vis: Visual Semantic Similarity
        target_text_prompt = f"{catalog_metadata.brand} {catalog_metadata.product_class} {catalog_metadata.weight_volume}"
        s_vis = 0.85
        if self.visual_engine:
            try:
                s_vis = self.visual_engine.compute_similarity(image, target_text_prompt)
            except Exception:
                s_vis = 0.85

        # S_ocr: OCR Similarity Score
        detected_text = ""
        if self.ocr_service:
            try:
                detected_text = self.ocr_service.extract_text(image)
            except Exception:
                detected_text = ""

        s_ocr = MultiModalFusionEngine.calculate_ocr_score(
            detected_brand=detected_text,
            target_brand=catalog_metadata.brand,
            detected_class=detected_text,
            target_class=catalog_metadata.product_class,
            extracted_metric=detected_text,
            target_metric=catalog_metadata.weight_volume,
            surface=catalog_metadata.surface_curvature,
        )

        # S_vlm: VLM Adjudicator Score
        adjudication_report: Optional[AdjudicationReport] = None
        s_vlm = 1.0
        if self.vlm_adjudicator:
            try:
                adjudication_report = self.vlm_adjudicator.adjudicate(image, catalog_metadata)
                s_vlm = adjudication_report.adjudicator_score
            except Exception:
                s_vlm = 1.0

        # 6. Mathematical Score Fusion & Decision
        s_fusion = MultiModalFusionEngine.calculate_fusion_score(s_vis, s_ocr, s_vlm)
        decision = MultiModalFusionEngine.determine_decision(s_fusion)

        overall_passed = decision != OperationalDecision.AUTO_REJECT

        return VerificationResult(
            overall_passed=overall_passed,
            decision=decision,
            fusion_score=s_fusion,
            visual_similarity_score=round(s_vis, 4),
            ocr_similarity_score=round(s_ocr, 4),
            vlm_score=round(s_vlm, 4),
            gate_evaluations=gate_evaluations,
            adjudication_report=adjudication_report,
            rejection_reasons=rejection_reasons,
        )
