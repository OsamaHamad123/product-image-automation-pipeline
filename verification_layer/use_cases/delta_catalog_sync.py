# verification_layer/use_cases/delta_catalog_sync.py
"""
Delta Catalog Sync Use Case & Adaptive Token Bucket Rate Limiter.
Applies SHA-256 row delta filtering, RAW mode vector input, AIMD congestion control, and jittered backoff.
"""

import time
import random
import threading
from typing import List, Dict, Any, Tuple, Optional
from verification_layer.domain.sheets_models import ProductEntity


# =====================================================================
# 1. Ports & Abstract Interfaces
# =====================================================================

class DatabasePort:
    def bulk_save_products(self, products: List[ProductEntity]) -> bool:
        raise NotImplementedError


class CacheBufferPort:
    def push_to_events_queue(self, event_data: Dict[str, Any]) -> bool:
        raise NotImplementedError

    def fetch_batch_safely(self, batch_size: int) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def clear_processing_batch(self, product_ids: List[str]) -> None:
        raise NotImplementedError

    def get_last_known_hash(self, product_id: str) -> Optional[str]:
        raise NotImplementedError

    def update_known_hash(self, product_id: str, state_hash: str) -> None:
        raise NotImplementedError


class SheetGatewayPort:
    def update_range_bulk(self, range_name: str, values: List[List[Any]]) -> Tuple[bool, Optional[int]]:
        raise NotImplementedError


# =====================================================================
# 2. Adaptive Token Bucket Rate Limiting Engine
# =====================================================================

class AdaptiveTokenBucket:
    """Reactive Token Bucket implementing AIMD (Additive Increase / Multiplicative Decrease)."""

    def __init__(self, capacity: float = 10.0, initial_rate: float = 1.0, max_rate: float = 8.0):
        self.capacity = capacity
        self.tokens = capacity
        self.refill_rate = initial_rate
        self.min_rate = 0.5
        self.max_rate = max_rate
        self.last_update = time.monotonic()
        self.lock = threading.Lock()

        # AIMD Constants
        self.delta_increase = 0.25
        self.beta_decrease = 0.60

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self.last_update
        self.last_update = now
        self.tokens = min(self.capacity, self.tokens + (elapsed * self.refill_rate))

    def acquire(self, tokens_needed: float = 1.0) -> None:
        while True:
            with self.lock:
                self._refill()
                if self.tokens >= tokens_needed:
                    self.tokens -= tokens_needed
                    return
                wait_duration = (tokens_needed - self.tokens) / self.refill_rate
            time.sleep(wait_duration)

    def report_success(self) -> None:
        with self.lock:
            self.refill_rate = min(self.max_rate, self.refill_rate + self.delta_increase)

    def report_throttle(self) -> None:
        with self.lock:
            self.refill_rate = max(self.min_rate, self.refill_rate * self.beta_decrease)
            self.tokens = 0.0


# =====================================================================
# 3. Delta Catalog Sync Use Case
# =====================================================================

class DeltaCatalogSyncUseCase:
    """Orchestrates delta synchronization, UTF-8 validation, SHA-256 delta filtering, and Google Sheets RAW uploads."""

    def __init__(
        self,
        db_adapter: DatabasePort,
        cache_adapter: CacheBufferPort,
        sheet_adapter: SheetGatewayPort,
        rate_limiter: Optional[AdaptiveTokenBucket] = None
    ):
        self.db_adapter = db_adapter
        self.cache_adapter = cache_adapter
        self.sheet_adapter = sheet_adapter
        self.rate_limiter = rate_limiter if rate_limiter else AdaptiveTokenBucket()

    def execute_sync_cycle(self, batch_size: int, sheet_range: str) -> int:
        raw_events = self.cache_adapter.fetch_batch_safely(batch_size)
        if not raw_events:
            return 0

        unique_products: Dict[str, ProductEntity] = {}
        for event in raw_events:
            product = ProductEntity(
                product_id=event["product_id"],
                sku=event["sku"],
                title=event["title"],
                price=float(event["price"]),
                stock=int(event["stock"])
            )
            unique_products[product.product_id] = product

        # 1. Delta filtering with SHA-256 hashes & UTF-8 validation
        dirty_products: List[ProductEntity] = []
        for product_id, product in unique_products.items():
            if not product.validate_utf8():
                continue

            current_hash = product.compute_hash()
            last_hash = self.cache_adapter.get_last_known_hash(product_id)

            if current_hash != last_hash:
                dirty_products.append(product)

        if not dirty_products:
            self.cache_adapter.clear_processing_batch([e["product_id"] for e in raw_events])
            return 0

        # 2. Bulk save changed products to database
        db_success = self.db_adapter.bulk_save_products(dirty_products)
        if not db_success:
            return 0

        # 3. Vectorize data into 2D matrix
        vectorized_data = [p.to_vector(prepend_apostrophe=False) for p in dirty_products]

        # 4. Upload RAW 2D matrix payload with rate limiting & exponential backoff
        sheets_success = self._upload_to_sheets_resiliently(sheet_range, vectorized_data)

        if sheets_success:
            for product in dirty_products:
                self.cache_adapter.update_known_hash(product.product_id, product.compute_hash())
            self.cache_adapter.clear_processing_batch([e["product_id"] for e in raw_events])
            return len(dirty_products)

        return 0

    def _upload_to_sheets_resiliently(self, range_name: str, values: List[List[Any]], max_retries: int = 5) -> bool:
        for attempt in range(max_retries):
            self.rate_limiter.acquire(1.0)

            success, retry_after_seconds = self.sheet_adapter.update_range_bulk(range_name, values)
            if success:
                self.rate_limiter.report_success()
                return True

            self.rate_limiter.report_throttle()

            if attempt == max_retries - 1:
                break

            if retry_after_seconds is not None:
                time.sleep(float(retry_after_seconds))
            else:
                base_delay = 1.0
                max_delay = 30.0
                backoff_delay = min(max_delay, base_delay * (2 ** attempt))
                jitter_val = random.uniform(0, backoff_delay * 0.1)
                time.sleep(backoff_delay + jitter_val)

        return False
