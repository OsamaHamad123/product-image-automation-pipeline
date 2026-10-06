"""sync_worker.py on a server: a clean stop on SIGTERM / SIGHUP (runtime package, item 1).

The cycle in progress finishes (a sheet write is never cut in half), then the heartbeat key is deleted (google_sheets
writes straight to the MariaDB outbox from then on) and what is left in Redis is moved to the outbox, so nothing stays
stranded in Redis while the worker is down. Proven on a real subprocess.
"""

import json
import os
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


class FakeRedis:
    def __init__(self, events=None):
        self.kv = {}
        self.sets = {}
        self.events = events if events is not None else []

    def ping(self):
        return True

    def get(self, key):
        return self.kv.get(key)

    def set(self, key, value, ex=None):
        self.kv[key] = value

    def delete(self, key):
        self.events.append(("delete", key))
        self.kv.pop(key, None)

    def sadd(self, key, member):
        self.sets.setdefault(key, set()).add(member)

    def srem(self, key, member):
        self.sets.get(key, set()).discard(member)

    def smembers(self, key):
        return set(self.sets.get(key, set()))


class FakeQueue:
    def __init__(self, events):
        self.events = events

    def append_update(self, row, col, value, **kw):
        self.events.append(("append", row, kw.get("col_key"), value))


def _payload(sync_worker, row, value):
    import google_sheets
    return json.dumps({"v": google_sheets.REDIS_PAYLOAD_VERSION, "row_index": row, "updates": {"image_link": value},
                       "seqs": {"image_link": 5}})


@pytest.fixture
def stops(monkeypatch):
    import stop_signals

    stop_signals.reset()
    monkeypatch.setattr(stop_signals, "install", lambda *a: ["SIGTERM"])     # keep pytest's own handlers
    yield stop_signals
    stop_signals.reset()


def test_a_stop_signal_finishes_the_cycle_then_hands_redis_over_to_the_outbox(offline, monkeypatch, stops):
    import google_sheets
    import sync_worker

    events = []
    r = FakeRedis(events)
    monkeypatch.setattr(sync_worker, "_client", lambda: r)
    monkeypatch.setattr(google_sheets, "SQLiteTransactionQueue", lambda: FakeQueue(events))
    monkeypatch.setattr(sync_worker, "SYNC_INTERVAL", 0)

    def cycle(redis, state):
        sync_worker.beat(redis)
        events.append(("cycle", "start"))
        stops._handler(signal.SIGTERM, None)          # arrives mid-cycle: recorded, the cycle is not cut
        # a payload a producer wrote while the heartbeat was still there
        redis.set(f"{sync_worker.CACHE_PREFIX}row-7", _payload(sync_worker, 7, "https://img.example/7.jpg"))
        redis.sadd(sync_worker.DIRTY_SET_KEY, "row-7")
        events.append(("cycle", "end"))

    monkeypatch.setattr(sync_worker, "_loop_once", cycle)
    sync_worker.main()
    assert events[:2] == [("cycle", "start"), ("cycle", "end")]
    assert ("delete", sync_worker.HEARTBEAT_KEY) in events
    assert events.index(("delete", sync_worker.HEARTBEAT_KEY)) < events.index(
        ("append", 7, "image_link", "https://img.example/7.jpg"))
    assert sync_worker.HEARTBEAT_KEY not in r.kv, "the last cycle must not beat again"
    assert r.smembers(sync_worker.DIRTY_SET_KEY) == set()


def test_a_failed_handover_leaves_the_payloads_in_redis(offline, monkeypatch, stops):
    import google_sheets
    import sync_worker

    r = FakeRedis()
    r.set(f"{sync_worker.CACHE_PREFIX}row-8", _payload(sync_worker, 8, "x"))
    r.sadd(sync_worker.DIRTY_SET_KEY, "row-8")

    def broken():
        raise OSError("database down")

    monkeypatch.setattr(google_sheets, "SQLiteTransactionQueue", broken)
    sync_worker.shutdown(r, {})                       # never raises
    assert r.smembers(sync_worker.DIRTY_SET_KEY) == {"row-8"}


HARNESS = textwrap.dedent(r'''
    import json, os, socket, sys, threading, time
    sys.path.insert(0, os.environ["REPO_ROOT"])
    events_path = os.path.abspath("events.jsonl")

    def event(kind, **data):
        with open(events_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(dict(data, kind=kind)) + "\n")

    import google_sheets, sync_worker

    def refuse(*a, **k):
        raise OSError("network access is blocked in this harness")

    socket.socket.connect = refuse
    socket.create_connection = refuse

    class Redis:
        kv, sets = {}, {}
        def ping(self): return True
        def get(self, key): return self.kv.get(key)
        def set(self, key, value, ex=None): self.kv[key] = value
        def delete(self, key):
            event("delete", key=key)
            self.kv.pop(key, None)
        def sadd(self, key, member): self.sets.setdefault(key, set()).add(member)
        def srem(self, key, member): self.sets.get(key, set()).discard(member)
        def smembers(self, key): return set(self.sets.get(key, set()))

    class Queue:
        def append_update(self, row, col, value, **kw): event("append", row=row, value=value)

    r = Redis()
    sync_worker._client = lambda: r
    google_sheets.SQLiteTransactionQueue = Queue
    sync_worker.SYNC_INTERVAL = 0.05
    cycles = {"n": 0}

    def cycle(redis, state):
        cycles["n"] += 1
        event("cycle_start", n=cycles["n"])
        time.sleep(1.0)                      # a sheet write in progress
        if cycles["n"] == 1:
            payload = {"v": google_sheets.REDIS_PAYLOAD_VERSION, "row_index": 9, "updates": {"image_link": "late"},
                       "seqs": {"image_link": 7}}
            redis.set(sync_worker.CACHE_PREFIX + "row-9", json.dumps(payload))
            redis.sadd(sync_worker.DIRTY_SET_KEY, "row-9")
        event("cycle_end", n=cycles["n"])

    sync_worker._loop_once = cycle
    sync_worker.main()
    event("exited")
''')


@pytest.mark.skipif(os.name != "posix", reason="needs POSIX signals")
def test_sigterm_stops_a_real_sync_worker_cleanly(tmp_path):
    script = tmp_path / "harness.py"
    script.write_text(HARNESS, encoding="utf-8")
    events_file = tmp_path / "events.jsonl"
    log = tmp_path / "harness.log"

    def events():
        if not events_file.exists():
            return []
        return [json.loads(line) for line in events_file.read_text(encoding="utf-8").splitlines() if line.strip()]

    with open(log, "wb") as sink:
        proc = subprocess.Popen([sys.executable, str(script)], cwd=tmp_path, stdout=sink, stderr=subprocess.STDOUT,
                                env=dict(os.environ, REPO_ROOT=str(ROOT)))
    try:
        deadline = time.monotonic() + 60
        while not any(e["kind"] == "cycle_start" for e in events()):
            assert proc.poll() is None, log.read_text(errors="replace")
            assert time.monotonic() < deadline
            time.sleep(0.05)
        proc.send_signal(signal.SIGTERM)                 # in the middle of the first cycle
        assert proc.wait(timeout=30) == 0, log.read_text(errors="replace")
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    kinds = [e["kind"] for e in events()]
    assert kinds[:2] == ["cycle_start", "cycle_end"] and kinds.count("cycle_start") == 1
    assert {"kind": "append", "row": 9, "value": "late"} in events()
    assert kinds.index("delete") < kinds.index("append") and kinds[-1] == "exited"
