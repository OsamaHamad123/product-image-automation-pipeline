"""catalog_match.cassette: the record / replay hooks one by one (the whole run is tests/test_replay_run.py).

* without a cassette every hook is a plain pass-through;
* requests are keyed without secrets, search texts case-folded, a retry by its attempt number;
* a streamed download, a refusal, a cut stream, a timeout and a redirect replay exactly as recorded;
* a label-reader call is keyed on the image content; a batch the cassette does not hold is answered from the
  row's other readings (approximate) or fails closed (a miss), never invented;
* Claude replies and SDK errors round-trip; the breaker, spend and index-size decisions replay as recorded;
* (no key, header or token ever reaches the cassette: tests/catalog_match/test_cm_cassette_review.py).
"""

from __future__ import annotations

import io
import json
import time

import pytest
import requests
from PIL import Image

from catalog_match import cassette, local_index, verify
from catalog_match.fetch import HttpFetcher
from catalog_match.models import Candidate, FetchedImage, SkuSpec
from catalog_match.pages import PageFetcher
from catalog_match.ratelimit import UNLIMITED
from catalog_match.verifiers import claude as claude_mod, spend as spend_mod


@pytest.fixture(autouse=True)
def _no_cassette_left():
    yield
    cassette.uninstall()


def jpeg(color, size=(640, 480)):
    buf = io.BytesIO()
    img = Image.new("RGB", size, (255, 255, 255))
    img.paste(Image.new("RGB", (size[0] // 2, size[1] // 2), color), (size[0] // 4, size[1] // 4))
    img.save(buf, "JPEG", quality=90)
    return buf.getvalue()


class Resp:
    def __init__(self, status=200, body=b"", ctype="image/jpeg", url="", length=None, cut=None):
        self.status_code = status
        self.content = body
        self.headers = {"Content-Type": ctype, "Content-Length": str(len(body) if length is None else length)}
        self.url = url
        self._cut = cut

    @property
    def text(self):
        return self.content.decode("utf-8", errors="replace")

    def json(self):
        return json.loads(self.text)

    def iter_content(self, chunk_size=65536):
        for i in range(0, len(self.content), chunk_size):
            if self._cut is not None and i >= self._cut:
                raise requests.exceptions.ChunkedEncodingError("Connection broken: IncompleteRead")
            yield self.content[i:i + chunk_size]

    def close(self):
        pass


class Session:
    """requests-like session: url -> Resp, or an exception to raise."""

    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    def _answer(self, url):
        self.calls.append(url)
        answer = self.answers[url]
        if isinstance(answer, list):
            answer = answer.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer

    def get(self, url, **kwargs):
        return self._answer(url)

    def post(self, url, **kwargs):
        return self._answer(url)


# ---------------------------------------------------------------------------
# No cassette
# ---------------------------------------------------------------------------

def test_every_hook_is_a_pass_through_without_a_cassette():
    assert cassette.active() is None and cassette.mode() == "" and not cassette.replaying()
    marker = object()
    assert cassette.http("serper", "POST", "https://x", lambda: marker, body={"q": "a"}) is marker
    assert cassette.verifier(lambda: marker) is marker
    assert cassette.local_index_rows(None, lambda: [marker]) == [marker]
    done, pending = {1}, {2}
    assert cassette.local_index_deadline({}, {}, done, pending) == (done, pending)
    cassette.verifier_scope("gemini", "m", False, 1024, "p", [], [])        # nothing to key
    with cassette.row(5):
        pass
    assert cassette.miss_code(cassette.NotRecorded()) == "not_recorded" and cassette.miss_code(ValueError()) is None
    assert cassette.shadow_record(None, None) is None


def test_offline_refuses_and_counts_every_connection():
    import socket

    import pymysql

    with cassette.offline() as attempts:
        with pytest.raises(OSError):
            socket.create_connection(("serper.example", 443), timeout=1)
        with pytest.raises(OSError):
            socket.getaddrinfo("google.serper.dev", 443)
        with pytest.raises(pymysql.err.OperationalError):
            pymysql.connect(host="127.0.0.1")
    assert len(attempts) == 3 and attempts[-1] == "pymysql.connect"


def test_installing_patches_only_while_installed(tmp_path):
    real_spend, real_count = spend_mod.MariaDbSpendStore.role_spend, local_index.DbCatalogStore.count
    cassette.install("record", str(tmp_path / "c"))
    assert spend_mod.MariaDbSpendStore.role_spend is not real_spend and "is_open" in verify.BREAKER.__dict__
    cassette.uninstall()
    assert spend_mod.MariaDbSpendStore.role_spend is real_spend and local_index.DbCatalogStore.count is real_count
    assert "is_open" not in verify.BREAKER.__dict__ and "is_open" not in claude_mod.CLAUDE_BREAKER.__dict__
    with pytest.raises(ValueError):
        cassette.install("live", str(tmp_path / "d"))


# ---------------------------------------------------------------------------
# Keys
# ---------------------------------------------------------------------------

def test_requests_are_keyed_without_secrets_and_with_normalised_search_text():
    a = cassette.canonical_request("lens_serpapi", "GET", "https://serpapi.com/search.json",
                                   params={"engine": "google_lens", "url": "https://i/x.jpg", "api_key": "SECRET-1"})
    b = cassette.canonical_request("lens_serpapi", "GET", "https://serpapi.com/search.json",
                                   params={"api_key": "OTHER-2", "url": "https://i/x.jpg", "engine": "google_lens"})
    assert "SECRET" not in json.dumps(a) and cassette.request_key(a) == cassette.request_key(b)
    cse = cassette.canonical_request("cse_legacy", "GET", "https://www.googleapis.com/customsearch/v1",
                                     params={"q": "Almarai  MILK", "key": "k", "cx": "c", "num": 10})
    assert cse["url"] == "https://www.googleapis.com/customsearch/v1?num=10&q=almarai+milk"
    one = cassette.canonical_request("serper", "POST", "https://google.serper.dev/images",
                                     body={"q": "  Almarai Full   Fat ", "gl": "ae", "X-API-KEY": "s"})
    two = cassette.canonical_request("serper", "POST", "https://google.serper.dev/images",
                                     body={"gl": "ae", "q": "almarai full fat"})
    assert one == two and one["body"] == {"gl": "ae", "q": "almarai full fat"}
    # an image URL is kept as it is (its query can name the image); a proxied attempt is its own key
    img = cassette.canonical_request("fetch", "GET", "https://cdn/x.jpg?b=2&a=1#frag")
    assert img["url"] == "https://cdn/x.jpg?b=2&a=1"
    assert cassette.request_key(img) != cassette.request_key(
        cassette.canonical_request("fetch", "GET", "https://cdn/x.jpg?b=2&a=1", proxy=True))


# ---------------------------------------------------------------------------
# Downloads and pages
# ---------------------------------------------------------------------------

URLS = {
    "ok": "https://cdn.example.ae/ok.jpg",
    "cut": "https://cdn.example.ae/cut.jpg",
    "slow": "https://cdn.example.ae/slow.jpg",
    "gone": "https://cdn.example.ae/gone.jpg",
    "huge": "https://cdn.example.ae/huge.jpg",
}


def noisy_jpeg(size=(900, 700)):
    import numpy as np
    buf = io.BytesIO()
    Image.fromarray(np.random.default_rng(7).integers(0, 255, (size[1], size[0], 3), dtype=np.uint8)).save(
        buf, "JPEG", quality=95)
    return buf.getvalue()


def _download_world():
    big = noisy_jpeg()
    assert len(big) > 2 * 65536
    return {
        URLS["ok"]: Resp(200, jpeg((200, 20, 20))),
        URLS["cut"]: Resp(200, big, cut=65536),
        URLS["slow"]: requests.exceptions.ReadTimeout("Read timed out. (read timeout=10)"),
        URLS["gone"]: Resp(404, b"", "text/html"),
        URLS["huge"]: Resp(200, b"", length=99 * 1024 * 1024),
    }


def _fetch_all(fetcher, urls):
    return {r.candidate.image_url: r for r in fetcher.fetch([Candidate(image_url=u) for u in urls])}


def test_downloads_replay_exactly_as_recorded_and_an_unknown_url_is_a_reported_miss(tmp_path, monkeypatch):
    monkeypatch.setattr("catalog_match.settings.proxy_url", lambda: "")
    folder = str(tmp_path / "cas")
    session = Session(_download_world())
    cassette.install("record", folder)
    with cassette.row(7):
        live = _fetch_all(HttpFetcher(store_dir=str(tmp_path / "a"), session=session), list(URLS.values()))
    cassette.uninstall()
    assert live[URLS["ok"]].ok and live[URLS["cut"]].error == "connection_error"
    assert live[URLS["slow"]].error == "timeout" and live[URLS["gone"]].error == "http_404"
    assert live[URLS["huge"]].error == "too_large"

    calls = len(session.calls)
    cas = cassette.install("replay", folder)
    with cassette.offline() as attempts, cassette.row(7):
        again = _fetch_all(HttpFetcher(store_dir=str(tmp_path / "b"), session=session),
                           list(URLS.values()) + ["https://cdn.example.ae/never.jpg"])
    assert len(session.calls) == calls and attempts == []
    for url in URLS.values():
        a, b = live[url], again[url]
        assert (a.ok, a.error, a.content_sha256, a.width, a.height, a.phash) == \
               (b.ok, b.error, b.content_sha256, b.width, b.height, b.phash), url
    assert again["https://cdn.example.ae/never.jpg"].error == "not_recorded"
    report = cas.row_report(7)
    assert not report["complete"] and [m["kind"] for m in report["misses"]] == ["image"]
    assert report["misses"][0]["request"]["url"] == "https://cdn.example.ae/never.jpg"


def test_a_page_redirect_keeps_its_final_url_and_another_row_reuses_a_page(tmp_path, monkeypatch):
    monkeypatch.setattr("catalog_match.settings.proxy_url", lambda: "")
    asked = "https://www.example.ae/old-product"
    final = "https://www.example.ae/other-product"
    html = ("<html><head><title>Other</title><meta property='og:image' content='https://cdn.example.ae/o.jpg'>"
            "</head><body>" + "text " * 100 + "</body></html>").encode("utf-8")
    session = Session({asked: Resp(200, html, "text/html; charset=utf-8", url=final)})
    folder = str(tmp_path / "cas")
    cassette.install("record", folder)
    with cassette.row(3):
        live = PageFetcher(session=session, bucket_factory=lambda host: UNLIMITED)._fetch_uncached(asked, "")
    cassette.uninstall()
    assert live.redirected_from == asked and live.url == final
    assert list((tmp_path / "cas" / "blobs").glob("*.gz")), "a page body is stored compressed"

    cas = cassette.install("replay", folder)
    with cassette.offline(), cassette.row(9):        # row 9 never read it: the row that did answers
        again = PageFetcher(session=Session({}), bucket_factory=lambda host: UNLIMITED)._fetch_uncached(asked, "")
        missing = PageFetcher(session=Session({}), bucket_factory=lambda host: UNLIMITED)._fetch_uncached(
            "https://www.example.ae/never", "")
    assert (again.url, again.redirected_from, again.images[0].url) == (live.url, live.redirected_from,
                                                                       live.images[0].url)
    assert missing.ok is False and missing.error == "not_recorded"
    assert [m["kind"] for m in cas.row_report(9)["misses"]] == ["page"]


def test_attempts_replay_in_order_and_a_retry_after_a_failure_is_not_invented(tmp_path):
    folder = str(tmp_path / "cas")
    url = "https://google.serper.dev/images"
    answers = [requests.exceptions.ConnectTimeout("connect timeout"), Resp(200, b'{"images": []}', "application/json")]

    def send():
        answer = answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer

    cassette.install("record", folder)
    with cassette.row(2):
        with pytest.raises(requests.exceptions.ConnectTimeout):
            cassette.http("serper", "POST", url, send, body={"q": "a"})
        assert cassette.http("serper", "POST", url, send, body={"q": "a"}).status_code == 200
        with pytest.raises(requests.exceptions.ReadTimeout):
            cassette.http("serper", "POST", url, lambda: (_ for _ in ()).throw(
                requests.exceptions.ReadTimeout("read timeout")), body={"q": "b"})
    cassette.uninstall()

    cas = cassette.install("replay", folder)
    with cassette.row(2):
        with pytest.raises(requests.Timeout):            # the recorded exception class again
            cassette.http("serper", "POST", url, lambda: None, body={"q": "A "})
        assert cassette.http("serper", "POST", url, lambda: None, body={"q": "a"}).json() == {"images": []}
        assert cassette.http("serper", "POST", url, lambda: None, body={"q": "a"}).status_code == 200   # reused
        with pytest.raises(requests.Timeout):
            cassette.http("serper", "POST", url, lambda: None, body={"q": "b"})
        with pytest.raises(cassette.NotRecorded):        # a second try after a timeout: nobody knows its answer
            cassette.http("serper", "POST", url, lambda: None, body={"q": "b"})
    assert [m["request"]["body"]["q"] for m in cas.row_report(2)["misses"]] == ["b"]
    with cassette.row(4):                                # another row: the same sequence as row 2
        with pytest.raises(requests.Timeout):
            cassette.http("serper", "POST", url, lambda: None, body={"q": "a"})
    assert cas.row_report(4)["complete"]


def test_a_zip_made_on_windows_replays(tmp_path):
    import zipfile

    folder = tmp_path / "cassette_win"
    cas = cassette.install("record", str(folder))
    cas.write_meta({"format": cassette.FORMAT})
    with cassette.row(2):
        cassette.http("fetch", "GET", URLS["ok"], lambda: Resp(200, jpeg((1, 2, 3))), stream=True,
                      max_bytes=10 ** 7).close()
    cassette.uninstall()
    archive = tmp_path / "cassette_win.zip"
    with zipfile.ZipFile(archive, "w") as zf:            # PowerShell 5.1 Compress-Archive: backslash names
        for path in folder.rglob("*"):
            if path.is_file():
                zf.write(path, "cassette_win\\" + str(path.relative_to(folder)).replace("/", "\\"))
    cas = cassette.install("replay", str(archive))
    assert cas.read_meta() == {"format": cassette.FORMAT}
    with cassette.row(2):
        resp = cassette.http("fetch", "GET", URLS["ok"], lambda: pytest.fail("no live call"), stream=True)
    assert b"".join(resp.iter_content(4096)) == jpeg((1, 2, 3))
    with pytest.raises(ValueError):
        cassette.install("fill", str(archive))            # a zip is read-only


# ---------------------------------------------------------------------------
# Label readers
# ---------------------------------------------------------------------------

def _fetched(color, name):
    data = jpeg(color)
    import hashlib
    return FetchedImage(candidate=Candidate(image_url=f"https://cdn.example.ae/{name}.jpg"), ok=True,
                        content_sha256=hashlib.sha256(data).hexdigest(), width=640, height=480, path_or_bytes=data)


SPEC = SkuSpec(raw_name="ALMARAI FULL FAT MILK 1L", brand_raw="ALMARAI", brand_canonical="Almarai",
               match_brands=("almarai",), sku_key="almarai-milk")
MATCHING = {"brand_text": "ALMARAI", "variant_text": "", "size_text": "", "pack_count": None,
            "view": "front_packshot", "brand_match": "yes", "variant_match": "unsure", "size_match": "unsure"}
OTHER = dict(MATCHING, brand_text="AL RAWABI", brand_match="no")


def _gemini_reply(entries):
    text = json.dumps({"images": entries, "best_index": 1})
    return Resp(200, json.dumps({"candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": "STOP"}],
                                 "usageMetadata": {"promptTokenCount": 1500, "candidatesTokenCount": 200}}).encode(),
                "application/json")


def _gemini(session):
    return verify.GeminiVerifier(api_key="gemini-key-zzzzzz", model="gemini-3.1-flash-lite", session=session,
                                 breaker=verify.CircuitBreaker(), sleep=lambda s: None)


def test_label_readings_are_keyed_on_image_content_and_fall_back_honestly(tmp_path):
    a, b, c = _fetched((200, 0, 0), "a"), _fetched((0, 200, 0), "b"), _fetched((0, 0, 200), "c")
    url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-flash-lite:generateContent"
    session = Session({url: [_gemini_reply([dict(MATCHING, image_index=1), dict(OTHER, image_index=2)])]})
    folder = str(tmp_path / "cas")
    cassette.install("record", folder)
    with cassette.row(2):
        live = _gemini(session).verify(SPEC, [a, b])
    cassette.uninstall()
    assert live.status == "ok" and [v.decision for v in live.verdicts] == ["MATCH", "MISMATCH"]

    cas = cassette.install("replay", folder)
    with cassette.offline(), cassette.row(2):
        # the same batch under other URLs (same bytes): the same answer, exactly
        renamed = [FetchedImage(**dict(f.__dict__, candidate=Candidate(image_url="https://elsewhere/" + n)))
                   for f, n in ((a, "1"), (b, "2"))]
        same = _gemini(Session({})).verify(SPEC, renamed)
        assert [v.__dict__ for v in same.verdicts] == [v.__dict__ for v in live.verdicts] and same.usage == live.usage
        assert cas.row_report(2) == dict(cas.row_report(2), complete=True, approximate=False)
        # another batch: b's reading comes from the recorded call, c was never read
        mixed = _gemini(Session({})).verify(SPEC, [b, c])
    assert mixed.status == "ok" and [v.decision for v in mixed.verdicts] == ["MISMATCH", "UNKNOWN"]
    report = cas.row_report(2)
    assert report["approximate"] and not report["complete"]
    assert [m["kind"] for m in report["misses"]] == ["verifier_image"]
    with cassette.row(3):        # nothing of row 3 was read: the call fails closed, never a made-up reading
        none = _gemini(Session({})).verify(SPEC, [a])
    assert none.status == "unknown" and none.error == "error:NotRecorded"
    assert [m["kind"] for m in cas.row_report(3)["misses"]] == ["verifier"]


class FakeClaudeClient:
    def __init__(self, replies):
        self.replies = replies
        self.calls = 0
        self.messages = self
        self.beta = self

    def create(self, **kwargs):
        self.calls += 1
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return reply


def test_claude_replies_and_sdk_errors_round_trip(tmp_path):
    anthropic = pytest.importorskip("anthropic")
    httpx = cassette._httpx()
    if httpx is None:
        pytest.skip("no httpx module for the SDK errors")
    a = _fetched((200, 0, 0), "a")
    message = {"type": "message", "model": "claude-haiku-4-5", "stop_reason": "end_turn",
               "content": [{"type": "text", "text": json.dumps({"images": [dict(MATCHING, image_index=1)],
                                                                "best_index": 1})}],
               "usage": {"input_tokens": 1200, "output_tokens": 150}}
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    refused = anthropic.APIStatusError("invalid x-api-key", response=httpx.Response(401, request=request), body=None)
    client = FakeClaudeClient([message, refused])

    def reader(c):
        return claude_mod.ClaudeVerifier("claude-haiku-4-5", api_key="sk-ant-fake-key-1234", client=c,
                                         breaker=verify.CircuitBreaker(), sleep=lambda s: None)

    folder = str(tmp_path / "cas")
    cassette.install("record", folder)
    with cassette.row(2):
        ok = reader(client).verify(SPEC, [a])
    with cassette.row(3):
        bad = reader(client).verify(SPEC, [a])
    cassette.uninstall()
    assert ok.status == "ok" and bad.error == "http_401" and bad.notices == ["claude_key_rejected"]

    cassette.install("replay", folder)
    idle = FakeClaudeClient([])
    with cassette.offline(), cassette.row(2):
        ok2 = reader(idle).verify(SPEC, [a])
    with cassette.offline(), cassette.row(3):
        bad2 = reader(idle).verify(SPEC, [a])
    assert idle.calls == 0
    assert [v.__dict__ for v in ok2.verdicts] == [v.__dict__ for v in ok.verdicts] and ok2.usage == ok.usage
    assert (bad2.status, bad2.error, bad2.notices) == (bad.status, bad.error, bad.notices)


# ---------------------------------------------------------------------------
# Wall-clock and database decisions
# ---------------------------------------------------------------------------

def test_breaker_spend_and_index_size_replay_as_recorded(tmp_path, monkeypatch):
    monkeypatch.setattr(spend_mod.MariaDbSpendStore, "role_spend", lambda self, role="strong", month=None: 4.75)
    monkeypatch.setattr(local_index.DbCatalogStore, "count", lambda self, fresh=False: 1234)
    breaker = verify.BREAKER
    folder = str(tmp_path / "cas")
    cassette.install("record", folder)
    try:
        with cassette.row(2):
            for _ in range(5):
                breaker.record(False)          # the live breaker opens
            assert breaker.is_open() is True
            assert spend_mod.MariaDbSpendStore().role_spend("strong") == 4.75
            assert local_index.DbCatalogStore().count() == 1234
    finally:
        cassette.uninstall()
        breaker.reset()
    monkeypatch.undo()

    cassette.install("replay", folder)
    with cassette.offline(), cassette.row(2):
        assert breaker.is_open() is True                 # recorded open, though this process's breaker is closed
        assert spend_mod.MariaDbSpendStore().role_spend("strong") == 4.75
        assert spend_mod.MariaDbSpendStore().add({"role": "strong", "usd": 1.0}) is True     # no database write
        assert local_index.DbCatalogStore().count() == 1234
    with cassette.offline(), cassette.row(5):            # a row that recorded nothing: the last known values
        assert breaker.is_open() is False
        assert spend_mod.MariaDbSpendStore().role_spend("strong") == 4.75
        assert local_index.DbCatalogStore().count() == 1234


def test_index_rows_and_the_read_deadline_replay_without_the_database(tmp_path):
    rows = [local_index.CatalogRow(id=1, store="lulu", url="https://www.luluhypermarket.com/a/p/1",
                                   slug_text="almarai full fat milk 1l", page_status="ok", page_age_h=5)]
    spec = SkuSpec(raw_name="ALMARAI FULL FAT MILK 1L", brand_raw="ALMARAI", match_brands=("almarai",),
                   class_tokens=("milk",))
    folder = str(tmp_path / "cas")
    from concurrent.futures import ThreadPoolExecutor
    cassette.install("record", folder)
    with cassette.row(2), ThreadPoolExecutor(2) as ex:
        assert cassette.local_index_rows(spec, lambda: rows) == rows
        f1, f2 = ex.submit(lambda: 1), ex.submit(time.sleep, 0.3)
        f1.result()
        futures, todo = {f1: 0, f2: 1}, {0: rows[0], 1: local_index.CatalogRow(id=2, store="x", url="https://x/2")}
        done, pending = cassette.local_index_deadline(futures, todo, {f1}, {f2})
    cassette.uninstall()

    cas = cassette.install("replay", folder)
    with cassette.offline(), cassette.row(2), ThreadPoolExecutor(2) as ex:
        assert cassette.local_index_rows(spec, lambda: pytest.fail("the database is not asked")) == rows
        g1, g2 = ex.submit(lambda: 1), ex.submit(lambda: 2)
        done2, pending2 = cassette.local_index_deadline({g1: 0, g2: 1}, todo, {g1, g2}, set())
    assert done2 == {g1} and pending2 == {g2}           # the read that was late live is late again
    assert cas.row_report(2)["complete"] and not cas.row_report(2)["approximate"]
    other = SkuSpec(raw_name="ALMARAI LABAN 1L", brand_raw="ALMARAI", match_brands=("almarai", "laban"))
    with cassette.offline(), cassette.row(2):
        assert cassette.local_index_rows(other, lambda: []) == rows      # this row's rows, flagged approximate
    assert cas.row_report(2)["approximate"]
    with cassette.offline(), cassette.row(8), pytest.raises(cassette.NotRecorded):
        cassette.local_index_rows(other, lambda: [])

    # a replayed page read is not news for the index: nothing is written back
    store = local_index.DbCatalogStore(connect=lambda: pytest.fail("a replay wrote to the index"))
    store.save_page(1, local_index.PageRecord(status="ok", image_url="https://x/1.jpg"))
