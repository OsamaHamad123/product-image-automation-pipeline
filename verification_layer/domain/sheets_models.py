# verification_layer/domain/sheets_models.py
"""
Google Sheets Sync Domain Entities & Value Objects.
Pure Python domain models for UTF-8 string validation, vectorization, and SHA-256 delta hashing.
"""

import hashlib
from typing import List, Any


class ProductEntity:
    """Represents core catalog product entity for vectorized bulk sync."""

    def __init__(self, product_id: str, sku: str, title: str, price: float, stock: int):
        self.product_id = str(product_id)
        self.sku = str(sku)
        self.title = str(title)
        self.price = float(price)
        self.stock = int(stock)

    def to_vector(self, prepend_apostrophe: bool = False) -> List[Any]:
        """Convert attributes to row vector for 2D matrix bulk updates."""
        sku_val = f"'{self.sku}" if prepend_apostrophe else self.sku
        return [self.product_id, sku_val, self.title, str(self.price), str(self.stock)]

    def compute_hash(self) -> str:
        """Computes SHA-256 state hash over critical product attributes."""
        payload = f"{self.sku}|{self.title}|{self.price:.4f}|{self.stock}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def validate_utf8(self) -> bool:
        """Validates all text fields for valid UTF-8 encoding."""
        try:
            self.product_id.encode("utf-8")
            self.sku.encode("utf-8")
            self.title.encode("utf-8")
            return True
        except UnicodeEncodeError:
            return False
