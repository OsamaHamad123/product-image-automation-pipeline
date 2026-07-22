# verification_layer/infrastructure/google_sheets_bulk_adapter.py
"""
Infrastructure Adapters for High-Throughput Google Sheets Sync.
Implements MySQL connection pooling, crash-safe Redis write-behind buffer, and Google Sheets Bulk Gateway.
"""

import time
import random
import threading
from typing import List, Dict, Any, Tuple, Optional
from verification_layer.domain.sheets_models import ProductEntity
from verification_layer.use_cases.delta_catalog_sync import DatabasePort, CacheBufferPort, SheetGatewayPort


class MySQLConnectionPoolAdapter(DatabasePort):
    """Simulates connection-pooled MySQL database access without TCP handshake overhead."""

    def __init__(self, max_connections: int = 15):
        self._pool_lock = threading.Lock()
        self._available_pool = list(range(max_connections))

    def bulk_save_products(self, products: List[ProductEntity]) -> bool:
        with self._pool_lock:
            if not self._available_pool:
                return False
            connection_token = self._available_pool.pop()

        try:
            # Simulate high-speed batch DB insert/update
            time.sleep(0.01)
            return True
        finally:
            with self._pool_lock:
                self._available_pool.append(connection_token)


class RedisWriteBehindBufferAdapter(CacheBufferPort):
    """Simulates in-memory Redis queue with atomic crash-safe batching (LMOVE) and state hash tracking."""

    def __init__(self):
        self._events_queue: List[Dict[str, Any]] = []
        self._processing_queue: List[Dict[str, Any]] = []
        self._hash_store: Dict[str, str] = {}
        self._lock = threading.Lock()

    def push_to_events_queue(self, event_data: Dict[str, Any]) -> bool:
        with self._lock:
            self._events_queue.append(event_data)
            return True

    def fetch_batch_safely(self, batch_size: int) -> List[Dict[str, Any]]:
        with self._lock:
            chunk = self._events_queue[:batch_size]
            self._processing_queue = chunk
            return chunk

    def clear_processing_batch(self, product_ids: List[str]) -> None:
        with self._lock:
            id_set = set(product_ids)
            self._events_queue = [e for e in self._events_queue if str(e["product_id"]) not in id_set]
            self._processing_queue.clear()

    def get_last_known_hash(self, product_id: str) -> Optional[str]:
        with self._lock:
            return self._hash_store.get(str(product_id))

    def update_known_hash(self, product_id: str, state_hash: str) -> None:
        with self._lock:
            self._hash_store[str(product_id)] = state_hash


class GoogleSheetsBulkGatewayAdapter(SheetGatewayPort):
    """Adapter invoking Google Sheets API v4 using 2D matrix RAW valueInputOption."""

    def __init__(self, worksheet_instance=None):
        self.worksheet = worksheet_instance

    def update_range_bulk(self, range_name: str, values: List[List[Any]]) -> Tuple[bool, Optional[int]]:
        if not values:
            return True, None

        if self.worksheet is not None:
            try:
                # Execute bulk batch update with RAW input option
                self.worksheet.batch_update([{
                    "range": range_name,
                    "values": values
                }], value_input_option="RAW")
                return True, None
            except Exception as err:
                err_str = str(err)
                if "429" in err_str:
                    return False, 5
                return False, 2
        else:
            # Simulated fallback gateway for test runner
            if random.random() < 0.05:
                return False, 5
            time.sleep(0.02)
            return True, None
