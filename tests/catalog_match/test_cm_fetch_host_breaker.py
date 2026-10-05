"""catalog_match.fetch.HostBreaker (WP speed, change 4a): a host whose downloads keep timing out is skipped.

Two downloads of a host ended in 'timeout' / 'connection_error' within 15 minutes (three for a UAE retailer of
data/trusted_domains.json) pause it for 15 minutes: its candidates come back at once as 'host_slow' without any
request. The clock is injected, so nothing sleeps. Sockets are blocked for every test.
"""

import threading

import pytest
import requests

from catalog_match import cassette, fetch as fetch_mod, pipeline, settings
from catalog_match.fetch import HostBreaker, HttpFetcher, host_breaker, reset_host_breaker
from test_cm_fetch import FakeRequests, FakeResponse, _cand, _jpeg, fake  # noqa: F401  (the download fakes)
from test_cm_pipeline import READ_MATCH, SPEC, StubProvider, StubVerifier, cand

MIN = 60.0


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


def timeouts(*urls, each=2):
    """Routes where every attempt (the first and its one retry) of each url times out."""
    return {u: [requests.Timeout("read timed out")] * each for u in urls}


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def breaker(clock):
    return HostBreaker(clock=clock)


def fetch_one(breaker, url, tmp_path):
    [res] = HttpFetcher(store_dir=str(tmp_path), breaker=breaker).fetch([_cand(url)], None)
    return res


def test_two_timed_out_downloads_pause_the_host_and_the_next_one_is_skipped_without_a_request(fake, breaker, tmp_path):
    a, b, c = (f"https://cdn.slowshop.ae/{n}.jpg" for n in "abc")
    f = fake(timeouts(a, b))
    assert fetch_one(breaker, a, tmp_path).error == "timeout" and not breaker.blocked("cdn.slowshop.ae")
    assert fetch_one(breaker, b, tmp_path).error == "timeout" and breaker.blocked("cdn.slowshop.ae")
    sent = len(f.calls)
    res = fetch_one(breaker, c, tmp_path)
    assert (res.ok, res.error) == (False, "host_slow") and len(f.calls) == sent        # nothing was requested


def test_connection_errors_count_like_timeouts(fake, breaker, tmp_path):
    urls = [f"https://img.flaky.ae/{n}.jpg" for n in "ab"]
    fake({u: [requests.ConnectionError("refused")] * 2 for u in urls})
    for u in urls:
        assert fetch_one(breaker, u, tmp_path).error == "connection_error"
    assert breaker.blocked("img.flaky.ae")


def test_other_hosts_are_not_affected(fake, breaker, tmp_path):
    slow = [f"https://cdn.slowshop.ae/{n}.jpg" for n in "ab"]
    good = "https://cdn.goodshop.ae/p.jpg"
    f = fake(dict(timeouts(*slow), **{good: [FakeResponse(200, _jpeg(seed=1))]}))
    for u in slow:
        fetch_one(breaker, u, tmp_path)
    assert breaker.blocked("cdn.slowshop.ae") and not breaker.blocked("cdn.goodshop.ae")
    assert fetch_one(breaker, good, tmp_path).ok is True
    assert [u for u, _ in f.calls].count(good) == 1


def test_the_host_is_tried_again_after_fifteen_minutes_with_a_clean_record(fake, breaker, clock, tmp_path):
    a, b, c, d = (f"https://cdn.slowshop.ae/{n}.jpg" for n in "abcd")
    f = fake(dict(timeouts(a, b), **{c: [requests.Timeout("t")] * 2, d: [FakeResponse(200, _jpeg(seed=2))]}))
    fetch_one(breaker, a, tmp_path)
    fetch_one(breaker, b, tmp_path)
    clock.advance(14 * MIN)
    assert fetch_one(breaker, c, tmp_path).error == "host_slow"
    clock.advance(1 * MIN + 1)
    assert not breaker.blocked("cdn.slowshop.ae")
    assert fetch_one(breaker, c, tmp_path).error == "timeout" and not breaker.blocked("cdn.slowshop.ae")   # 1 failure
    assert fetch_one(breaker, d, tmp_path).ok is True


def test_failures_more_than_fifteen_minutes_apart_do_not_add_up(fake, breaker, clock, tmp_path):
    a, b = "https://cdn.slowshop.ae/a.jpg", "https://cdn.slowshop.ae/b.jpg"
    fake(timeouts(a, b))
    fetch_one(breaker, a, tmp_path)
    clock.advance(15 * MIN + 1)
    fetch_one(breaker, b, tmp_path)
    assert not breaker.blocked("cdn.slowshop.ae")


@pytest.mark.parametrize("host", ["noon.com", "cdn.noon.com", "carrefouruae.com", "luluhypermarket.com",
                                  "images.amazon.ae", "spinneys.com"])
def test_a_uae_retailer_needs_three_failures(host, clock):
    brk = HostBreaker(clock=clock)
    assert brk.limit(host) == 3
    assert brk.record_failure(host) is False and brk.record_failure(host) is False
    assert not brk.blocked(host)
    assert brk.record_failure(host) is True and brk.blocked(host)


@pytest.mark.parametrize("host", ["shops.ae", "shinjukuhalalfood.com", "notnoon.com", "noon.com.evil.example"])
def test_any_other_host_needs_two(host, clock):
    brk = HostBreaker(clock=clock)
    assert brk.limit(host) == 2
    assert brk.record_failure(host) is False and brk.record_failure(host) is True and brk.blocked(host)


@pytest.mark.parametrize("response", [FakeResponse(403, b"no", "text/html"), FakeResponse(404, b"gone", "text/html"),
                                      FakeResponse(503, b"busy", "text/html"),
                                      FakeResponse(200, b"<html>" + b"x" * 5000 + b"</html>", "text/html"),
                                      FakeResponse(200, b"tiny", "image/jpeg")])
def test_a_host_that_answers_never_counts_whatever_the_answer(fake, breaker, tmp_path, response):
    urls = [f"https://cdn.shop.ae/{n}.jpg" for n in "abcd"]
    fake({u: [response, response] for u in urls})
    for u in urls:
        assert fetch_one(breaker, u, tmp_path).error != "host_slow"
    assert not breaker.blocked("cdn.shop.ae")


def test_a_retry_is_not_sent_once_the_host_has_been_paused_meanwhile(fake, breaker, tmp_path, monkeypatch):
    url = "https://cdn.slowshop.ae/x.jpg"
    f = fake({url: [requests.Timeout("t")]})                      # a second attempt would find no route: IndexError
    get = f.get

    def get_and_trip(u, **kwargs):
        try:
            return get(u, **kwargs)
        finally:
            breaker.record_failure("cdn.slowshop.ae")              # other downloads of the host failed meanwhile
            breaker.record_failure("cdn.slowshop.ae")

    monkeypatch.setattr(fetch_mod.requests, "get", get_and_trip)
    res = fetch_one(breaker, url, tmp_path)
    assert res.error == "timeout" and len(f.calls) == 1


def test_a_pause_in_flight_is_not_extended_by_late_failures(clock):
    brk = HostBreaker(clock=clock)
    brk.record_failure("h.example")
    assert brk.record_failure("h.example") is True
    clock.advance(10 * MIN)
    assert brk.record_failure("h.example") is False               # a download that was already under way
    clock.advance(5 * MIN + 1)
    assert not brk.blocked("h.example")                            # still the original 15 minutes


def test_the_breaker_is_thread_safe_and_pauses_a_host_once(clock):
    brk = HostBreaker(clock=clock)
    tripped, errors = [], []

    def hammer(host):
        try:
            for _ in range(200):
                if brk.record_failure(host):
                    tripped.append(host)
                brk.blocked(host)
        except Exception as exc:          # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=hammer, args=(f"h{i % 4}.example",)) for i in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors and sorted(tripped) == [f"h{i}.example" for i in range(4)]
    assert all(brk.blocked(f"h{i}.example") for i in range(4))


def test_reset_clears_every_host_and_the_shared_breaker_is_process_wide():
    shared = host_breaker()
    assert shared is host_breaker() and HttpFetcher()._breaker() is shared
    shared.record_failure("r.example")
    shared.record_failure("r.example")
    assert shared.blocked("r.example")
    reset_host_breaker()
    assert not shared.blocked("r.example")
    other = HostBreaker()
    other.record_failure("r.example")
    other.record_failure("r.example")
    assert other.blocked("r.example") and not shared.blocked("r.example")        # a private breaker is separate


def test_the_breaker_is_off_while_a_cassette_is_installed(fake, breaker, tmp_path, monkeypatch):
    urls = [f"https://cdn.slowshop.ae/{n}.jpg" for n in "abcd"]
    f = fake(timeouts(*urls))
    monkeypatch.setattr(cassette, "active", lambda: object())
    monkeypatch.setattr(cassette, "http", lambda kind, method, url, send, **kw: send())
    for u in urls:
        assert fetch_one(breaker, u, tmp_path).error == "timeout"          # nobody is skipped, nothing is counted
    assert len(f.calls) == 8 and not breaker.blocked("cdn.slowshop.ae")


def test_the_skip_is_counted_in_reject_counts_like_any_download_error(breaker, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "_config", None)
    monkeypatch.setenv("AUTO_PUBLISH_ENABLED", "false")
    monkeypatch.setattr(fetch_mod, "_curl_requests", None)
    monkeypatch.setattr(fetch_mod.settings, "proxy_url", lambda: "")
    monkeypatch.setattr(fetch_mod.requests, "get", lambda *a, **k: pytest.fail("a paused host was requested"))
    for _ in range(2):
        breaker.record_failure("img.example-cdn.com")
    # a store outside the UAE retailer list (a retailer's listing is never skipped: HostBreaker.exempt)
    pack = cand(2, "Almarai Full Fat Fresh Milk 1L | Yasmin Store",
                "https://www.yasminstore.com/almarai-full-fat-fresh-milk-1l")
    outcome = pipeline.find_product_image(
        SPEC, providers=[StubProvider("serper", [pack])], fetcher=HttpFetcher(store_dir=str(tmp_path), breaker=breaker),
        verifier=StubVerifier({pack.image_url: READ_MATCH}))
    assert outcome.reject_counts.get("download:host_slow") == 1
    assert outcome.winner is None
    [rc] = [r for r in outcome.ranked if r.candidate.image_url == pack.image_url]
    assert rc.status == "rejected" and "download:host_slow" in rc.reasons


def test_a_new_run_starts_with_no_slow_host(monkeypatch):
    import main

    shared = host_breaker()
    shared.record_failure("run.example")
    shared.record_failure("run.example")
    assert shared.blocked("run.example")
    main._forget_slow_hosts()
    assert not shared.blocked("run.example")


# --- review fixes: a passing hiccup is forgotten, and a UAE retailer's candidate is never skipped ---------------

def test_a_download_that_comes_back_forgets_the_hosts_earlier_failures(fake, breaker, tmp_path):
    a, ok, b = "https://cdn.slowshop.ae/a.jpg", "https://cdn.slowshop.ae/ok.jpg", "https://cdn.slowshop.ae/b.jpg"
    fake(dict(timeouts(a, b), **{ok: [FakeResponse(200, _jpeg(seed=3))]}))
    assert fetch_one(breaker, a, tmp_path).error == "timeout"
    assert fetch_one(breaker, ok, tmp_path).ok is True
    assert fetch_one(breaker, b, tmp_path).error == "timeout"
    assert not breaker.blocked("cdn.slowshop.ae")                 # one failure since the last answer, not two


def test_a_uae_retailers_listing_is_downloaded_even_while_its_image_cdn_is_paused(fake, breaker, tmp_path):
    cdn = "m.media-amazon.com"
    slow = [f"https://{cdn}/images/I/{n}.jpg" for n in "ab"]
    mine = f"https://{cdn}/images/I/mine.jpg"
    other = f"https://{cdn}/images/I/other.jpg"
    f = fake(dict(timeouts(*slow), **{mine: [FakeResponse(200, _jpeg(seed=4))]}))
    for u in slow:                                                # the CDN itself is no UAE retailer: two pause it
        fetch_one(breaker, u, tmp_path)
    assert breaker.blocked(cdn)
    [res] = HttpFetcher(store_dir=str(tmp_path), breaker=breaker).fetch(
        [_cand(mine, "https://www.amazon.ae/Deep-Blue-Shredded-Tuna/dp/B0CTNL38Z8")], None)
    assert res.ok is True and [u for u, _ in f.calls].count(mine) == 1
    [res] = HttpFetcher(store_dir=str(tmp_path), breaker=breaker).fetch(
        [_cand(other, "https://some-blog.example/tuna-review")], None)
    assert res.error == "host_slow"                               # a non-retailer page on the same CDN is skipped
