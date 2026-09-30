"""catalog_match.ratelimit: per-provider token buckets shared across threads (D7).

The clock and the sleeper are injected, so nothing here sleeps for real; time.sleep
is patched to fail the test if the bucket ever calls it directly.
"""

import socket
import threading
import time

import pytest

from catalog_match import ratelimit
from catalog_match.ratelimit import TokenBucket


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def _blocked(*args, **kwargs):
        raise RuntimeError("network access is blocked in catalog_match unit tests")

    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)
    monkeypatch.setattr(socket, "getaddrinfo", _blocked)


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    def _forbidden(seconds):
        raise AssertionError(f"time.sleep({seconds}) called; sleeping must go through the injected sleeper")

    monkeypatch.setattr(time, "sleep", _forbidden)


class FakeClock:
    def __init__(self, start=1000.0):
        self.now = start
        self.lock = threading.Lock()

    def __call__(self):
        with self.lock:
            return self.now

    def advance(self, seconds):
        with self.lock:
            self.now += seconds


class RecordingSleeper:
    def __init__(self):
        self.calls = []
        self.lock = threading.Lock()

    def __call__(self, seconds):
        with self.lock:
            self.calls.append(seconds)


def test_twenty_threads_get_exactly_burst_immediate_tokens():
    clock = FakeClock()
    sleeper = RecordingSleeper()
    bucket = TokenBucket(rate_per_min=60, burst=5, clock=clock, sleeper=sleeper)
    start = threading.Barrier(20)
    waits = []
    lock = threading.Lock()

    def worker():
        start.wait()
        waited = bucket.acquire()
        with lock:
            waits.append(waited)

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert all(not t.is_alive() for t in threads)

    assert len(waits) == 20
    immediate = [w for w in waits if w == 0.0]
    assert len(immediate) == 5
    # The other 15 each reserved a distinct slot, one second apart at 60/min, and
    # waited only through the injected sleeper.
    assert sorted(w for w in waits if w > 0) == pytest.approx([float(i) for i in range(1, 16)])
    assert sorted(sleeper.calls) == pytest.approx([float(i) for i in range(1, 16)])


def test_tokens_refill_with_the_clock():
    clock = FakeClock()
    sleeper = RecordingSleeper()
    bucket = TokenBucket(rate_per_min=15, burst=1, clock=clock, sleeper=sleeper)
    assert bucket.acquire() == 0.0
    assert bucket.try_acquire() is False          # empty, and try_acquire never waits
    clock.advance(4.0)                             # 15/min = one token every 4 s
    assert bucket.try_acquire() is True
    assert bucket.acquire() == pytest.approx(4.0)  # next token is 4 s away
    assert sleeper.calls == [pytest.approx(4.0)]


def test_burst_caps_accumulated_tokens():
    clock = FakeClock()
    sleeper = RecordingSleeper()
    bucket = TokenBucket(rate_per_min=60, burst=2, clock=clock, sleeper=sleeper)
    clock.advance(3600)  # an hour idle must not bank 3600 tokens
    assert bucket.acquire() == 0.0
    assert bucket.acquire() == 0.0
    assert bucket.acquire() > 0.0


def test_registry_shares_one_bucket_per_provider_name():
    ratelimit.reset_registry()
    try:
        a = ratelimit.get_bucket("serper", 60, 5)
        b = ratelimit.get_bucket("SERPER", 999, 9)  # same provider: the first bucket wins
        c = ratelimit.get_bucket("off", 15, 1)
        assert a is b
        assert a is not c
        assert c.rate_per_sec * 60 == pytest.approx(15)

        seen = []

        def grab():
            seen.append(ratelimit.get_bucket("bing_html", 12, 2))

        threads = [threading.Thread(target=grab) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len({id(x) for x in seen}) == 1
    finally:
        ratelimit.reset_registry()


def test_invalid_configuration_is_rejected():
    with pytest.raises(ValueError):
        TokenBucket(rate_per_min=0, burst=1)
    with pytest.raises(ValueError):
        TokenBucket(rate_per_min=10, burst=0)
