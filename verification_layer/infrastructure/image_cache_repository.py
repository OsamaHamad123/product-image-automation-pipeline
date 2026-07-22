# verification_layer/infrastructure/image_cache_repository.py
"""
Image Cache Repository for SQLite/MariaDB local_cache.db.
Manages product_image_cache table and targeted cache invalidation.
"""

import sqlite3
import os
from typing import Optional, Tuple


class ImageCacheRepository:
    def __init__(self, db_path: Optional[str] = None):
        if not db_path:
            base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            db_path = os.path.join(base_dir, "local_cache.db")
        self.db_path = db_path
        self._initialize_db()

    def _initialize_db(self):
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS product_image_cache (
                    product_id INTEGER PRIMARY KEY,
                    cached_url TEXT NOT NULL,
                    is_valid INTEGER DEFAULT 1,
                    last_checked TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()

    def invalidate_cache(self, product_id: int, url: str) -> None:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE product_image_cache SET is_valid = 0 WHERE product_id = ? AND cached_url = ?",
                (product_id, url)
            )
            conn.commit()

    def update_valid_cache(self, product_id: int, url: str) -> None:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO product_image_cache (product_id, cached_url, is_valid, last_checked)
                VALUES (?, ?, 1, CURRENT_TIMESTAMP)
            """, (product_id, url))
            conn.commit()

    def get_cache(self, product_id: int) -> Optional[Tuple[str, int]]:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT cached_url, is_valid FROM product_image_cache WHERE product_id = ?",
                (product_id,)
            )
            row = cursor.fetchone()
            return row if row else None
