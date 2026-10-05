"""Hedged Serper request (WP speed, change 3): a request unanswered after SERPER_HEDGE_AFTER_S seconds is sent once
more and the first good answer wins; the extra request is one more credit and is recorded on the call.

The HTTP layer is a fake session whose answers wait on events, so nothing here depends on how fast the machine is.
Sockets are blocked for every test.
"""

import json
import socket
import threading

import pytest
import requests

from catalog_match import cassette, ratelimit, settings
from catalog_match.models import ProviderHealth, ProviderResult, SkuSpec
from catalog_match.providers import serper as serper_mod
from catalog_match.providers.lens import SerperLensProvider
from catalog_match.providers.serper import SerperImagesProvider
from catalog_match.providers.serper_shopping import SerperShoppingProvider
from catalog_match.providers.serper_web import SerperWebProvider

SPEC = SkuSpec(raw_name="Almarai Fresh Milk Full Fat 1L", brand_raw="Almarai", brand_canonical="Almarai")
BODY = {"images": [{"imageUrl": "https://cdn.example.ae/milk.jpg", "link": "https://shop.example.ae/milk",
                    "title": "Almarai Fresh Milk Full Fat 1L", "imageWidth": 800, "imageHeight": 800, "position": 1}],
        "organic": [{"link": "https://shop.example.ae/milk", "title": "Almarai Fresh Milk", "position": 1}],
        "shopping": [{"title": "Almarai Fresh Milk", "imageUrl": "https://cdn.example.ae/milk.jpg",
                      "link": "https://shop.example.ae/milk", "position": 1}]}


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    def refuse(*args, **kwargs):
        raise RuntimeError("network access is blocked in catalog_match unit tests")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    monkeypatch.setattr(settings, "_config", None)
    monkeypatch.delenv("SERPER_HEDGE_AFTER_S", raising=False)
    yield
    cassette.uninstall()


class Resp:
    def __init__(self, status=200, body=None, text=None):
        self.status_code = status
        self._body = BODY if body is None else body
        self.text = text if text is not None else json.dumps(self._body)
        self.closed = False

    def json(self):
        return self._body

    def close(self):
        self.closed = True


class Session:
    """post() number n runs script[n-1] (a callable returning a response or raising); every call is counted."""

    def __init__(self, *script):
        self.script = list(script)
        self.calls = 0
        self.lock = threading.Lock()
        self.results = []

    def post(self, url, **kwargs):
        with self.lock:
            self.calls += 1
            n = self.calls
        step = self.script[min(n, len(self.script)) - 1]
        out = step()
        self.results.append(out)
        return out


def hedge_after(monkeypatch, seconds):
    monkeypatch.setenv("SERPER_HEDGE_AFTER_S", str(seconds))


def make(cls, session, bucket=ratelimit.UNLIMITED):
    return cls(api_key="test-key", session=session, bucket=bucket)


def test_a_fast_answer_is_never_sent_twice(monkeypatch):
    hedge_after(monkeypatch, 5)
    session = Session(lambda: Resp())
    res = make(SerperImagesProvider, session).search("almarai milk", "en", SPEC)
    assert res.status == "ok" and res.hedges == 0 and session.calls == 1
    assert res.health().hedges == 0


def test_a_slow_first_request_is_hedged_and_the_first_good_answer_wins(monkeypatch):
    hedge_after(monkeypatch, 0.05)
    release_first, first_started = threading.Event(), threading.Event()

    def slow():
        first_started.set()
        release_first.wait(10)
        return Resp(200, {"images": []})

    session = Session(slow, lambda: Resp())
    res = make(SerperImagesProvider, session).search("almarai milk", "en", SPEC)
    assert first_started.is_set()
    assert res.status == "ok" and len(res.candidates) == 1          # the duplicate's answer, not the empty one
    assert session.calls == 2 and res.hedges == 1 and res.health().hedges == 1
    release_first.set()                                              # the slow one finishes later and is ignored


def test_the_late_loser_is_closed_and_ignored(monkeypatch):
    hedge_after(monkeypatch, 0.05)
    release_first, finished = threading.Event(), threading.Event()
    losing = Resp()

    def slow():
        release_first.wait(10)
        finished.set()
        return losing

    session = Session(slow, lambda: Resp())
    res = make(SerperImagesProvider, session).search("almarai milk", "en", SPEC)
    assert res.status == "ok" and res.hedges == 1
    release_first.set()
    assert finished.wait(5)
    for _ in range(200):
        if losing.closed:
            break
        threading.Event().wait(0.01)
    assert losing.closed is True


def test_the_first_request_answering_after_a_failed_hedge_is_used(monkeypatch):
    hedge_after(monkeypatch, 0.05)
    second_failed, first_ok = threading.Event(), threading.Event()

    def slow():
        assert second_failed.wait(5)
        first_ok.set()
        return Resp()

    def broken():
        second_failed.set()
        return Resp(500, {}, "server error")

    session = Session(slow, broken)
    res = make(SerperImagesProvider, session).search("almarai milk", "en", SPEC)
    assert first_ok.is_set() and res.status == "ok" and res.http_status == 200 and res.hedges == 1


def test_both_requests_timing_out_is_one_timeout_error_that_still_counts_both(monkeypatch):
    hedge_after(monkeypatch, 0.05)
    second_called = threading.Event()

    def first():
        assert second_called.wait(5)
        raise requests.Timeout("read timed out")

    def second():
        second_called.set()
        raise requests.Timeout("read timed out")

    session = Session(first, second)
    res = make(SerperImagesProvider, session).search("almarai milk", "en", SPEC)
    assert res.status == "error" and res.error == "timeout" and session.calls == 2 and res.hedges == 1


def test_an_http_answer_is_returned_in_preference_to_an_exception_when_both_fail(monkeypatch):
    hedge_after(monkeypatch, 0.05)
    second_called = threading.Event()

    def first():
        assert second_called.wait(5)
        raise requests.Timeout("read timed out")

    def second():
        second_called.set()
        return Resp(429, {}, "slow down")

    res = make(SerperImagesProvider, Session(first, second)).search("almarai milk", "en", SPEC)
    assert res.status == "quota" and res.http_status == 429 and res.hedges == 1


def test_a_fast_failure_is_not_hedged(monkeypatch):
    hedge_after(monkeypatch, 5)
    session = Session(lambda: Resp(429, {}, "slow down"))
    res = make(SerperImagesProvider, session).search("almarai milk", "en", SPEC)
    assert res.status == "quota" and session.calls == 1 and res.hedges == 0
    session = Session(lambda: (_ for _ in ()).throw(requests.ConnectionError("refused")))
    res = make(SerperImagesProvider, session).search("almarai milk", "en", SPEC)
    assert res.status == "error" and session.calls == 1 and res.hedges == 0


def test_no_second_request_without_a_token_in_the_shared_bucket(monkeypatch):
    """The duplicate is a real request: it takes a token (never waiting for one), so a drained bucket sends none."""
    hedge_after(monkeypatch, 0.05)
    release = threading.Event()

    def slow():
        release.wait(0.4)
        return Resp()

    bucket = ratelimit.TokenBucket(60.0, 1, clock=lambda: 0.0, sleeper=lambda s: None)   # one token, no refill
    session = Session(slow, lambda: Resp())
    res = make(SerperImagesProvider, session, bucket).search("almarai milk", "en", SPEC)      # search() takes it
    assert res.status == "ok" and session.calls == 1 and res.hedges == 0


def test_the_hedge_takes_a_token_when_one_is_left(monkeypatch):
    hedge_after(monkeypatch, 0.05)
    release = threading.Event()
    bucket = ratelimit.TokenBucket(60.0, 3, clock=lambda: 0.0, sleeper=lambda s: None)
    session = Session(lambda: (release.wait(0.5), Resp())[1], lambda: Resp())
    res = make(SerperImagesProvider, session, bucket).search("almarai milk", "en", SPEC)
    assert res.hedges == 1 and session.calls == 2
    assert bucket._tokens == pytest.approx(1.0)                      # 3 - the call's own token - the duplicate's


def test_a_setting_of_zero_turns_hedging_off(monkeypatch):
    hedge_after(monkeypatch, 0)
    release = threading.Event()
    session = Session(lambda: (release.wait(0.2), Resp())[1], lambda: Resp())
    res = make(SerperImagesProvider, session).search("almarai milk", "en", SPEC)
    assert res.status == "ok" and session.calls == 1 and res.hedges == 0


def test_the_web_and_shopping_endpoints_hedge_too_and_visual_search_never_does(monkeypatch):
    hedge_after(monkeypatch, 0.05)
    for cls, count in ((SerperWebProvider, 1), (SerperShoppingProvider, 1)):
        release = threading.Event()
        session = Session(lambda: (release.wait(0.5), Resp())[1], lambda: Resp())
        res = make(cls, session).search("almarai milk", "en", SPEC)
        assert res.hedges == 1 and session.calls == 2, cls.__name__
    release = threading.Event()
    session = Session(lambda: (release.wait(0.3), Resp(200, {"visual_matches": []}))[1], lambda: Resp())
    res = make(SerperLensProvider, session).search("https://cdn.example.ae/milk.jpg", "en", SPEC)
    assert session.calls == 1 and res.hedges == 0


def test_the_request_timeout_is_ten_seconds():
    assert SerperImagesProvider.timeout == SerperWebProvider.timeout == SerperShoppingProvider.timeout == 10.0
    seen = {}

    class Spy:
        def post(self, url, **kwargs):
            seen.update(kwargs)
            return Resp()

    make(SerperImagesProvider, Spy()).search("almarai milk", "en", SPEC)
    assert seen["timeout"] == 10.0


# ---------------------------------------------------------------------------
# The cassette: a recorded or replayed run is never hedged and never counted twice
# ---------------------------------------------------------------------------

def test_hedging_is_off_while_a_cassette_is_installed_and_a_replay_is_not_counted_twice(tmp_path, monkeypatch):
    hedge_after(monkeypatch, 0.05)
    folder = str(tmp_path / "cas")
    release = threading.Event()
    session = Session(lambda: (release.wait(0.4), Resp())[1], lambda: Resp())
    cassette.install("record", folder)
    with cassette.row(1):
        recorded = make(SerperImagesProvider, session).search("almarai milk", "en", SPEC)
    cassette.uninstall()
    assert recorded.status == "ok" and recorded.hedges == 0 and session.calls == 1       # slow, yet sent once

    cas = cassette.install("replay", folder)
    with cassette.offline() as attempts, cassette.row(1):
        replayed = make(SerperImagesProvider, session).search("almarai milk", "en", SPEC)
    assert replayed.status == "ok" and replayed.hedges == 0 and attempts == []
    assert session.calls == 1                                                            # nothing was sent again
    assert cas.row_report(1)["complete"] is True


def test_hedged_http_passes_straight_to_the_cassette_when_one_is_installed(monkeypatch):
    seen = []
    monkeypatch.setattr(cassette, "active", lambda: object())
    monkeypatch.setattr(cassette, "http", lambda kind, method, url, send, **kw: seen.append((kind, method, url, kw)) or "x")
    provider = make(SerperImagesProvider, Session(lambda: Resp()))
    sent = []
    assert serper_mod.hedged_http(provider, "https://google.serper.dev/images", {"q": "a"}, lambda: sent.append(1)) == "x"
    assert seen == [("serper", "POST", "https://google.serper.dev/images", {"body": {"q": "a"}})] and sent == []


# ---------------------------------------------------------------------------
# The setting
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value, expected", [(None, 4.5), ("4.5", 4.5), ("2", 2.0), ("0", 0.0), ("0.3", 0.3),
                                             ("-1", 4.5), ("abc", 4.5), ("nan", 4.5), ("", 4.5), ("500", 60.0)])
def test_the_hedge_delay_setting(monkeypatch, value, expected):
    if value is not None:
        monkeypatch.setenv("SERPER_HEDGE_AFTER_S", value)
    assert settings.serper_hedge_after_s() == expected


def test_the_default_hedge_delay_is_four_and_a_half_seconds():
    assert settings.DEFAULTS["SERPER_HEDGE_AFTER_S"] == "4.5" and settings.serper_hedge_after_s() == 4.5


# ---------------------------------------------------------------------------
# Honest cost: every hedge is one more credit wherever calls are counted
# ---------------------------------------------------------------------------

def test_the_hedge_travels_from_the_provider_result_to_the_health_record():
    res = ProviderResult(provider="serper", status="ok", http_status=200, hedges=1)
    assert res.health() == ProviderHealth(provider="serper", status="ok", http_status=200, hedges=1)
    assert ProviderResult(provider="serper", status="ok").health().hedges == 0


def test_a_hedged_call_is_two_credits_in_the_spend_and_the_health_counters():
    import local_cache_db
    import ops_health

    outcome = {"provider_health": [
        {"provider": "serper", "status": "ok", "http_status": 200, "query_id": "Q1", "hedges": 1},
        {"provider": "serper", "status": "empty", "http_status": 200, "query_id": "Q2"},
        {"provider": "serper", "status": "error", "http_status": None, "query_id": "Q3", "hedges": 1},
        {"provider": "serper_web", "status": "ok", "http_status": 200, "query_id": "X1", "hedges": "bad"}]}
    price = ops_health.source_prices()
    spend = {p: (calls, usd) for p, calls, usd in local_cache_db.spend_from_outcome(outcome)}
    assert spend["serper"][0] == 3 and spend["serper"][1] == pytest.approx(3 * price["serper"])   # 2 + 1 (the error: none)
    assert spend["serper_web"][0] == 1
    calls = ops_health._provider_calls(outcome)
    assert [c for c in calls if c[0] == "serper" and c[1] in ops_health.ANSWERED_STATUSES].__len__() == 3
    entry = ops_health.entry_from_row({"outcome_json": json.dumps(dict(outcome, searched_at="2026-10-04T10:00:00+00:00")),
                                       "age_s": 5})
    assert entry is not None and sum(1 for p, s, _h in entry["providers"] if p == "serper" and s == "ok") == 2


def test_the_run_export_and_the_smoke_cost_count_the_extra_credit():
    import sys
    from pathlib import Path

    scripts = str(Path(__file__).resolve().parents[2] / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    import export_run
    import smoke_live

    outcome = {"provider_health": [
        {"provider": "serper", "status": "ok", "http_status": 200, "query_id": "Q1", "latency_ms": 5200, "hedges": 1},
        {"provider": "serper", "status": "ok", "http_status": 200, "query_id": "Q2", "latency_ms": 900}]}
    calls = export_run.provider_calls(outcome, ())
    assert calls[0]["hedged"] is True and calls[0]["hedges"] == 1
    assert "hedged" not in calls[1] and "hedges" not in calls[1]
    prices = {"serper": 0.001, "_default": 0.001}
    assert smoke_live.call_cost(calls[0], prices) == pytest.approx(0.002)
    assert smoke_live.call_cost(calls[1], prices) == pytest.approx(0.001)
    assert smoke_live.credits({"hedges": "x"}) == 1 and smoke_live.credits({}) == 1


def test_smoke_live_records_the_hedge_of_a_counted_provider_call():
    import sys
    from pathlib import Path

    scripts = str(Path(__file__).resolve().parents[2] / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    import smoke_live

    class Inner:
        name, sanctioned = "serper", True

        def search(self, query, hl, spec):
            return ProviderResult(provider="serper", status="ok", http_status=200, hedges=1)

    log = []
    smoke_live.CountingProvider(Inner(), log).search("q", "en", SPEC)
    assert log[0]["hedged"] is True and log[0]["hedges"] == 1
