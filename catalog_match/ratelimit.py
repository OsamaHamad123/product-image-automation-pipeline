"""Per-provider token buckets shared across threads (decision D7).

API providers never use random sleeps. Each provider name owns one TokenBucket in
a process-wide registry, so every worker thread draws from the same budget.

TokenBucket(rate_per_min, burst, clock=time.monotonic, sleeper=time.sleep)
    acquire() reserves one token and returns the seconds it had to wait (0.0 when a
    token was available). Reservation is done under a lock and the wait happens
    outside it, so N concurrent callers get distinct, evenly spaced slots and no
    caller busy-loops. Both the clock and the sleeper are injectable, so tests never
    sleep for real.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Dict, Optional

logger = logging.getLogger(__name__)


class TokenBucket:
    """Thread-safe token bucket refilled at rate_per_min tokens per minute, capped at burst."""

    def __init__(
        self,
        rate_per_min: float,
        burst: int = 1,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
        name: str = "",
    ) -> None:
        if rate_per_min <= 0:
            raise ValueError("rate_per_min must be > 0")
        if burst < 1:
            raise ValueError("burst must be >= 1")
        self.rate_per_sec = float(rate_per_min) / 60.0
        self.burst = int(burst)
        self.name = name
        self._clock = clock
        self._sleeper = sleeper
        self._lock = threading.Lock()
        self._tokens = float(burst)
        self._stamp = clock()

    def _refill(self, now: float) -> None:
        elapsed = max(0.0, now - self._stamp)
        self._stamp = now
        self._tokens = min(float(self.burst), self._tokens + elapsed * self.rate_per_sec)

    def try_acquire(self) -> bool:
        """Take a token only if one is available right now."""
        with self._lock:
            self._refill(self._clock())
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return True
            return False

    def acquire(self) -> float:
        """Reserve one token, wait for it if needed, and return the seconds waited."""
        with self._lock:
            self._refill(self._clock())
            self._tokens -= 1.0
            wait = 0.0 if self._tokens >= 0.0 else -self._tokens / self.rate_per_sec
        if wait > 0.0:
            logger.debug("rate limit %s: waiting %.2fs", self.name or "bucket", wait)
            self._sleeper(wait)
        return wait


class _Unlimited:
    """A bucket that never waits (used by tests and for providers without a limit)."""

    name = "unlimited"

    def try_acquire(self) -> bool:
        return True

    def acquire(self) -> float:
        return 0.0


UNLIMITED = _Unlimited()

_registry: Dict[str, TokenBucket] = {}
_registry_lock = threading.Lock()


def get_bucket(name: str, rate_per_min: float, burst: int = 1) -> TokenBucket:
    """The shared bucket for a provider name; created on first use with the given rate."""
    key = (name or "").strip().lower()
    with _registry_lock:
        bucket = _registry.get(key)
        if bucket is None:
            bucket = TokenBucket(rate_per_min, burst, name=key)
            _registry[key] = bucket
        return bucket


def set_bucket(name: str, bucket: Optional[TokenBucket]) -> None:
    """Install (or with None remove) the shared bucket for a provider name."""
    key = (name or "").strip().lower()
    with _registry_lock:
        if bucket is None:
            _registry.pop(key, None)
        else:
            _registry[key] = bucket


def reset_registry() -> None:
    with _registry_lock:
        _registry.clear()
