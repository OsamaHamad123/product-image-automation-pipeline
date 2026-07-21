# verification_layer/domain/interfaces.py
from typing import Protocol, Optional, Dict, Any
from PIL import Image
from verification_layer.domain.models import CatalogProduct, AdjudicationReport


class IVisualEmbeddingEngine(Protocol):
    def compute_similarity(self, image: Image.Image, text: str) -> float:
        """Compute cosine similarity score (S_vis) between image and text."""
        ...


class IVLMAdjudicator(Protocol):
    def adjudicate(self, image: Image.Image, catalog_metadata: CatalogProduct) -> AdjudicationReport:
        """Perform multimodal verification using VLM prompt engineering."""
        ...


class ILogoDetector(Protocol):
    def detect_brand_logo(self, image: Image.Image, brand_name: str) -> float:
        """Detect brand logo using YOLO or dedicated object detector (returns confidence [0, 1])."""
        ...


class IOCRService(Protocol):
    def extract_text(self, image: Image.Image) -> str:
        """Extract bilingual OCR text from image."""
        ...
