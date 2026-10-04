"""catalog_match.cassette, review fixes: what a live paid recording on the owner's machine must never do.

The web is a real requests.Session over a fake transport adapter (no network): every answer is a urllib3
response turned into a requests.Response by HTTPAdapter.build_response, so resp.url holds the full query
string with the keys and the CSE engine id, exactly as a live response does. Bodies look like SerpApi's,
Google CSE's and Serper's, some large enough to go to blobs.

* no key or engine id anywhere in a cassette: every file, the gzip blobs decompressed (and each layer that
  hides them works on its own);
* a title with U+2028 / U+0085 replays (written escaped, read on '\\n' only), also from a CRLF zip;
* a missing, damaged or held blob is never a silent answer; a full disk never changes the live run;
* the offline guard also stops a local proxy; a fill after an interrupted run keeps its first answer;
* a discarded page read, a cut body, a changed index row and extra breaker asks are visible in the report.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import errno
import gzip
import importlib.util
import io
import json
import logging
import os
import socket
import threading
import types
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qsl, quote, quote_plus, urlsplit

import pytest
import requests
import urllib3
from PIL import Image

from catalog_match import cassette, local_index, settings as cm_settings, verify
from catalog_match.fetch import HttpFetcher
from catalog_match.models import Candidate, SkuSpec
from catalog_match.pages import PageFetcher
from catalog_match.providers.cse_legacy import CseLegacyProvider
from catalog_match.providers.lens import SerpApiLensProvider
from catalog_match.providers.serper import SerperImagesProvider
from catalog_match.providers.serper_shopping import SerperShoppingProvider
from catalog_match.providers.serper_web import SerperWebProvider
from catalog_match.ratelimit import UNLIMITED
from catalog_match.verifiers import claude as claude_mod, spend as spend_mod

REPO = Path(__file__).resolve().parents[2]
TODAY = dt.date(2026, 10, 1)          # before CSE_SUNSET_DATE, whatever day the suite runs

# obviously fake values, shaped like the real ones (an old-style CSE engine id has a ':')
FAKE_KEYS = {
    "SERPER_API_KEY": "fake-serper-0c1d2e3f4a5b6c7d8e9f",
    "SERPAPI_API_KEY": "fake-serpapi-9a8b7c6d5e4f3a2b1c0d",
    "GEMINI_API_KEY": "fake-gemini-AIzaFAKE1234567890",
    "ANTHROPIC_API_KEY": "fake-anthropic-key-000111222333",
    "GOOGLE_SEARCH_API_KEYS": ["fake-cse-key-one-AIzaQwErTy", "fake-cse-key-two-AIzaAsDfGh"],
    "GOOGLE_SEARCH_CX_LIST": ["017576662512468239146:omuauf_lfve", "a1b2c3d4e5f6fakecx"],
}
NO_KEYS = {k: ([] if isinstance(v, list) else "") for k, v in FAKE_KEYS.items()}
CSE_KEYS, CSE_CX = FAKE_KEYS["GOOGLE_SEARCH_API_KEYS"], FAKE_KEYS["GOOGLE_SEARCH_CX_LIST"]
ALL_FAKE = [FAKE_KEYS["SERPER_API_KEY"], FAKE_KEYS["SERPAPI_API_KEY"], FAKE_KEYS["GEMINI_API_KEY"],
            FAKE_KEYS["ANTHROPIC_API_KEY"], *CSE_KEYS, *CSE_CX]

SPEC = SkuSpec(raw_name="ALMARAI FULL FAT MILK 1L", brand_raw="ALMARAI", brand_canonical="Almarai",
               match_brands=("almarai",), sku_key="almarai-milk")
MATCHING = {"brand_text": "ALMARAI", "variant_text": "Full Fat", "size_text": "1 L", "pack_count": 1,
            "view": "front_packshot", "brand_match": "yes", "variant_match": "yes", "size_match": "yes"}
SHOP = "https://www.example-grocer.ae"
CDN = "https://cdn.example-grocer.ae"


@pytest.fixture(autouse=True)
def _clean():
    yield
    cassette.uninstall()
    verify.BREAKER.reset()
    claude_mod.CLAUDE_BREAKER.reset()


def _smoke():
    spec = importlib.util.spec_from_file_location("smoke_live_cassette_review", REPO / "scripts" / "smoke_live.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def jpeg(color, size=(640, 480)):
    buf = io.BytesIO()
    img = Image.new("RGB", size, (255, 255, 255))
    img.paste(Image.new("RGB", (size[0] // 2, size[1] // 2), color), (size[0] // 4, size[1] // 4))
    img.save(buf, "JPEG", quality=90)
    return buf.getvalue()


def noisy_jpeg(seed, size=(400, 300)):
    import numpy as np
    buf = io.BytesIO()
    Image.fromarray(np.random.default_rng(seed).integers(0, 255, (size[1], size[0], 3), dtype=np.uint8)).save(
        buf, "JPEG", quality=95)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Real-looking API bodies
# ---------------------------------------------------------------------------

def serper_images(q, n=100, title=None):
    return {"searchParameters": {"q": q, "gl": "ae", "hl": "en", "type": "images", "num": n, "engine": "google"},
            "images": [{"title": title or f"Almarai Full Fat Fresh Milk 1L - Rich & Creamy, Long Life | pack {i} | "
                                                 "Example Grocer UAE Online Shopping",
                        "imageUrl": f"{CDN}/milk/{i}.jpg", "imageWidth": 800, "imageHeight": 800,
                        "thumbnailUrl": f"https://encrypted-tbn0.gstatic.com/images?q=tbn:ANd9GcR{i:04d}xYz&s",
                        "thumbnailWidth": 225, "thumbnailHeight": 225, "source": "Example Grocer UAE",
                        "domain": "www.example-grocer.ae", "link": f"{SHOP}/en/almarai-full-fat-milk-1l/p/{1000 + i}",
                        "googleUrl": f"https://www.google.com/imgres?imgurl={quote(f'{CDN}/milk/{i}.jpg', safe='')}"
                                     f"&tbnid=Fake{i:06d}AbCdEfGhIjKlMn&vet=12ahUKEwiFakeVet{i:04d}&docid=FakeDoc{i:08d}"
                                     f"&w=800&h=800&itg=1&hl=en&gl=ae",
                        "position": i} for i in range(1, n + 1)],
            "credits": 1}


def serper_web(q):
    return {"searchParameters": {"q": q, "gl": "ae", "hl": "en", "type": "search", "engine": "google"},
            "organic": [{"title": "Almarai Full Fat Fresh Milk 1L | Example Grocer", "link": f"{SHOP}/p/77",
                         "snippet": "Almarai Full Fat Fresh Milk 1 L. Order online.", "position": 1}],
            "credits": 1}


def serper_shopping(q):
    return {"searchParameters": {"q": q, "gl": "ae", "hl": "en", "type": "shopping", "engine": "google"},
            "shopping": [{"title": "Almarai Full Fat Fresh Milk 1L", "source": "Example Grocer",
                          "link": f"{SHOP}/p/78", "price": "AED 6.50", "imageUrl": f"{CDN}/milk/shop.jpg",
                          "rating": 4.6, "ratingCount": 120, "productId": "12345678901234567890", "position": 1}],
            "credits": 2}


def serpapi_lens(query, key, n=220):
    """A SerpApi google_lens answer that echoes the key: in a free-text note, a link and a parameter block."""
    return {"search_metadata": {"id": "6713f0c9e1fake", "status": "Success",
                                "json_endpoint": "https://serpapi.com/searches/fake/6713f0c9e1fake.json",
                                "note": f"requested with {key}", "total_time_taken": 2.31},
            "search_parameters": {"engine": "google_lens", "url": query.get("url"), "hl": "en", "country": "ae",
                                  "api_key": key},
            "visual_matches": [{"position": i, "title": f"Almarai Full Fat Milk 1L - listing {i}",
                                "link": f"{SHOP}/p/{2000 + i}", "source": "Example Grocer UAE",
                                "source_icon": "https://encrypted-tbn1.gstatic.com/favicon-tbn?q=tbn:fake",
                                "thumbnail": f"https://encrypted-tbn2.gstatic.com/images?q=tbn:fake{i}",
                                "image": f"{CDN}/lens/{i}.jpg", "image_width": 800, "image_height": 800}
                               for i in range(1, n + 1)],
            "serpapi_pagination": {"next": f"https://serpapi.com/search.json?api_key={key}&engine=google_lens"
                                           f"&page_token=fakeToken123&url={quote(str(query.get('url')), safe='')}"}}


def cse_body(query, large=False, echo_cx=None):
    cx = echo_cx or query.get("cx")
    request = {"title": f"Google Custom Search - {query.get('q')}", "totalResults": "1230",
               "searchTerms": query.get("q"), "count": 10, "startIndex": 1, "inputEncoding": "utf8",
               "outputEncoding": "utf8", "safe": "off", "cx": cx, "gl": "ae", "cr": "countryAE", "hl": "en",
               "searchType": "image"}
    snippet = "Almarai Full Fat Fresh Milk 1L, fresh from the farm. " * (140 if large else 1)
    return {"kind": "customsearch#search",
            "url": {"type": "application/json",
                    "template": "https://www.googleapis.com/customsearch/v1?q={searchTerms}&num={count?}"
                                "&start={startIndex?}&cx={cx?}&searchType={searchType?}&alt=json"},
            "queries": {"request": [request], "nextPage": [dict(request, startIndex=11)]},
            "context": {"title": "UAE products"},
            "searchInformation": {"searchTime": 0.31, "formattedSearchTime": "0.31", "totalResults": "1230",
                                  "formattedTotalResults": "1,230"},
            "items": [{"kind": "customsearch#result", "title": f"Almarai Full Fat Milk 1L {i}",
                       "htmlTitle": f"<b>Almarai</b> Full Fat Milk 1L {i}", "link": f"{CDN}/cse/{i}.jpg",
                       "displayLink": "www.example-grocer.ae", "snippet": snippet, "htmlSnippet": snippet,
                       "mime": "image/jpeg", "fileFormat": "image/jpeg",
                       "image": {"contextLink": f"{SHOP}/p/{3000 + i}", "height": 800, "width": 800,
                                 "byteSize": 51234, "thumbnailLink": f"https://encrypted-tbn3.gstatic.com/i?q={i}",
                                 "thumbnailHeight": 144, "thumbnailWidth": 144}} for i in range(1, 11)]}


CSE_QUOTA = {"error": {"code": 429, "message": "Quota exceeded for quota metric 'Queries' and limit 'Queries per day' "
                                               "of service 'customsearch.googleapis.com' for consumer "
                                               "'project_number:123456789'.",
                       "errors": [{"message": "Quota exceeded", "domain": "global", "reason": "rateLimitExceeded"}],
                       "status": "RESOURCE_EXHAUSTED"}}


def gemini_reply(n):
    text = json.dumps({"images": [dict(MATCHING, image_index=i) for i in range(1, n + 1)], "best_index": 1})
    return {"candidates": [{"content": {"parts": [{"text": text}], "role": "model"}, "finishReason": "STOP"}],
            "usageMetadata": {"promptTokenCount": 1500, "candidatesTokenCount": 120, "totalTokenCount": 1620},
            "modelVersion": "gemini-3.1-flash-lite"}


def product_page(n_lines=1500):
    ld = json.dumps({"@context": "https://schema.org", "@type": "Product", "name": "Almarai Full Fat Fresh Milk 1L",
                     "brand": {"@type": "Brand", "name": "Almarai"}, "image": f"{CDN}/milk/page.jpg"})
    filler = "".join(f"<li>Customers also bought item {i}</li>" for i in range(n_lines))
    return (f"<html><head><title>Almarai Full Fat Fresh Milk 1L</title>"
            f"<script type=\"application/ld+json\">{ld}</script></head><body><h1>Almarai Full Fat Fresh Milk 1L</h1>"
            f"<ul>{filler}</ul></body></html>").encode("utf-8")


# ---------------------------------------------------------------------------
# The fake transport
# ---------------------------------------------------------------------------

def connection_error(request, host):
    return requests.exceptions.ConnectionError(
        f"HTTPSConnectionPool(host='{host}', port=443): Max retries exceeded with url: {request.path_url} "
        f"(Caused by NewConnectionError('<urllib3.connection.HTTPSConnection object at 0x7f3a2c>: Failed to "
        f"establish a new connection: [Errno 111] Connection refused'))")


class FakeWeb(requests.adapters.HTTPAdapter):
    """The transport of a real requests.Session, answering from a fake web (no socket is ever opened)."""

    def __init__(self, serper_title=None, gemini_busy_first=True, serper_n=100):
        super().__init__()
        self.sent = []
        self.serper_title = serper_title
        self.serper_n = serper_n
        self.gemini_busy = gemini_busy_first
        self.images = {f"{CDN}/milk/1.jpg": jpeg((200, 30, 30)), f"{CDN}/milk/2.jpg": jpeg((30, 160, 30)),
                       f"{CDN}/milk/big.jpg": noisy_jpeg(1), f"{CDN}/milk/chunked.jpg": noisy_jpeg(2)}
        self.lock = threading.Lock()

    def session(self):
        s = requests.Session()
        s.trust_env = False
        s.mount("https://", self)
        s.mount("http://", self)
        return s

    def send(self, request, stream=False, timeout=None, verify=True, cert=None, proxies=None):
        with self.lock:
            self.sent.append(request.url)
        answer = self.answer(request)
        if isinstance(answer, BaseException):
            raise answer
        status, ctype, body = answer[:3]
        headers = {"Content-Type": ctype}
        if len(answer) < 4 or answer[3]:                  # a chunked answer has no Content-Length
            headers["Content-Length"] = str(len(body))
        raw = urllib3.HTTPResponse(body=io.BytesIO(body), headers=headers, status=status,
                                   reason="OK" if status == 200 else "Error", preload_content=False,
                                   decode_content=False, request_method=request.method, request_url=request.url)
        return self.build_response(request, raw)

    @staticmethod
    def _json(status, data):
        return status, "application/json; charset=utf-8", json.dumps(data, ensure_ascii=False).encode("utf-8")

    def answer(self, request):
        parts = urlsplit(request.url)
        host, path = parts.hostname, parts.path
        query = dict(parse_qsl(parts.query))
        body = json.loads(request.body) if request.method == "POST" and request.body else {}
        if host == "google.serper.dev":
            q = body.get("q", "")
            if "forbidden" in q:
                return self._json(403, {"message": f"Unauthorized: {request.headers.get('X-API-KEY')} is not valid",
                                        "statusCode": 403})
            table = {"/images": lambda: serper_images(q, self.serper_n, self.serper_title), "/search": lambda: serper_web(q),
                     "/shopping": lambda: serper_shopping(q)}
            return self._json(200, table[path]())
        if host == "serpapi.com":
            if "unreachable" in query.get("url", ""):
                return connection_error(request, host)
            return self._json(200, serpapi_lens(query, query.get("api_key")))
        if host == "www.googleapis.com":
            if "unreachable" in query.get("q", ""):
                return connection_error(request, host)
            if query.get("key") == CSE_KEYS[0] or query.get("key", "").endswith("-1"):
                return self._json(429, CSE_QUOTA)
            return self._json(200, cse_body(query, large="large" in query.get("q", ""),
                                            echo_cx="other-engine-id-424242" if "echo" in query.get("q", "") else None))
        if host == "generativelanguage.googleapis.com":
            if self.gemini_busy:
                self.gemini_busy = False
                return self._json(503, {"error": {"code": 503, "message": "The model is overloaded. Please try "
                                                                          "again later.", "status": "UNAVAILABLE"}})
            n = sum(1 for p in body["contents"][0]["parts"] if "inlineData" in p)
            return self._json(200, gemini_reply(n))
        if host == "cdn.example-grocer.ae":
            url = f"{CDN}{path}"
            if path.endswith("/gone.jpg"):
                return 404, "text/html", b"<html><body>Not Found</body></html>"
            if path.endswith("/slow.jpg"):
                return requests.exceptions.ReadTimeout(
                    "HTTPSConnectionPool(host='cdn.example-grocer.ae', port=443): Read timed out. (read timeout=10)")
            if url in self.images:
                return 200, "image/jpeg", self.images[url], not path.endswith("/chunked.jpg")
            return 200, "image/jpeg", jpeg((90, 90, 210))
        if host == "www.example-grocer.ae":
            if path.endswith("/missing"):
                return 404, "text/html; charset=utf-8", b"<html><body>Page not found</body></html>"
            return 200, "text/html; charset=utf-8", product_page()
        raise AssertionError(f"unexpected request to {host}")


class DeadAdapter(requests.adapters.HTTPAdapter):
    """A replay must not send anything."""

    def send(self, request, **kwargs):
        raise AssertionError(f"a replay sent {request.method} {urlsplit(request.url).hostname}")


def dead_session():
    s = requests.Session()
    s.trust_env = False
    s.mount("https://", DeadAdapter())
    s.mount("http://", DeadAdapter())
    return s


class FakeClaudeClient:
    """An SDK-like client: replies as dicts, errors as exceptions."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.messages = self
        self.beta = self

    def create(self, **kwargs):
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return reply


def claude_message(n=1):
    return {"type": "message", "model": "claude-haiku-4-5", "stop_reason": "end_turn",
            "content": [{"type": "text", "text": json.dumps({"images": [dict(MATCHING, image_index=i)
                                                                         for i in range(1, n + 1)],
                                                             "best_index": 1})}],
            "usage": {"input_tokens": 1200, "output_tokens": 150}}


@contextlib.contextmanager
def configured(values):
    """The owner's settings: these values as config attributes and environment variables (no proxy)."""
    env = {k: (",".join(v) if isinstance(v, list) else str(v)) for k, v in values.items()}
    env.update(PROXY_URL="", GOOGLE_SEARCH_API_KEY="", GOOGLE_SEARCH_CX="")
    with contextlib.ExitStack() as stack:
        stack.enter_context(mock.patch.dict(os.environ, env))
        cfg = getattr(cm_settings, "_config", None)
        if cfg is not None:
            for k, v in dict(values, PROXY_URL="").items():
                stack.enter_context(mock.patch.object(cfg, k, v, create=True))
        yield


def stored_files(folder):
    """(name, bytes) of every file of a cassette, gzip blobs decompressed (kept temporary files too)."""
    out = []
    for path in sorted(Path(folder).rglob("*")):
        if path.is_file():
            data = path.read_bytes()
            if path.name.endswith(".gz") or ".gz." in path.name:
                data = gzip.decompress(data)
            out.append((str(path.relative_to(folder)), data))
    return out


def hits(folder, values):
    forms = {f for v in values for f in (v, quote(v, safe=""), quote_plus(v))}
    return sorted({(name, f) for name, data in stored_files(folder) for f in forms if f.encode("utf-8") in data})


def entries(folder, name="http.jsonl"):
    text = (Path(folder) / name).read_text(encoding="utf-8")
    return [json.loads(line) for line in text.split("\n") if line.strip()]


# ---------------------------------------------------------------------------
# C1 / C2: no key or engine id anywhere
# ---------------------------------------------------------------------------

def _every_provider(session, explicit_keys):
    """One live row through every hook kind: Serper images (large, and a refusal), web and shopping, SerpApi Lens
    (large, and a transport error), CSE (key rotation after a quota error, a large body, a transport error),
    Gemini (busy, then an answer), Claude (an answer and an error), downloads and pages (with failures)."""
    k = FAKE_KEYS if explicit_keys else {}
    out = {}
    out["serper"] = SerperImagesProvider(api_key=k.get("SERPER_API_KEY"), session=session, bucket=UNLIMITED).search(
        "Almarai full fat milk 1L", "en", SPEC)
    out["serper_403"] = SerperImagesProvider(api_key=k.get("SERPER_API_KEY"), session=session,
                                             bucket=UNLIMITED).search("forbidden almarai", "en", SPEC)
    out["web"] = SerperWebProvider(api_key=k.get("SERPER_API_KEY"), session=session, bucket=UNLIMITED).search(
        "Almarai full fat milk", "en", SPEC)
    out["shopping"] = SerperShoppingProvider(api_key=k.get("SERPER_API_KEY"), session=session,
                                             bucket=UNLIMITED).search("Almarai full fat milk", "en", SPEC)
    lens = SerpApiLensProvider(api_key=k.get("SERPAPI_API_KEY"), session=session, bucket=UNLIMITED)
    out["lens"] = lens.search(f"{CDN}/milk/1.jpg", "en", SPEC)
    out["lens_down"] = lens.search(f"{CDN}/unreachable.jpg", "en", SPEC)
    cse = CseLegacyProvider(k.get("GOOGLE_SEARCH_API_KEYS") or cm_settings.google_search_api_keys(),
                            k.get("GOOGLE_SEARCH_CX_LIST") or cm_settings.google_search_cx_list(), today=TODAY,
                            session=session, bucket=UNLIMITED)
    out["cse"] = cse.search("Almarai milk", "en", SPEC)
    out["cse_large"] = cse.search("Almarai milk large", "en", SPEC)
    out["cse_down"] = cse.search("Almarai milk unreachable", "en", SPEC)
    got = HttpFetcher(store_dir=None, session=session).fetch(
        [Candidate(image_url=f"{CDN}/milk/{n}") for n in ("1.jpg", "2.jpg", "gone.jpg", "slow.jpg")])
    out["downloads"] = {fi.candidate.image_url: (fi.ok, fi.error, fi.content_sha256) for fi in got}
    reader = PageFetcher(session=session, bucket_factory=lambda host: UNLIMITED)
    out["page"] = reader._fetch_uncached(f"{SHOP}/p/77", "")
    out["page_missing"] = reader._fetch_uncached(f"{SHOP}/missing", "")
    images = [fi for fi in got if fi.ok]
    out["gemini"] = verify.GeminiVerifier(api_key=k.get("GEMINI_API_KEY"), model="gemini-3.1-flash-lite",
                                          session=session, breaker=verify.CircuitBreaker(),
                                          sleep=lambda s: None).verify(SPEC, images)
    client = FakeClaudeClient([claude_message(len(images)), PermissionError(
        f"Error code: 401 - invalid x-api-key {FAKE_KEYS['ANTHROPIC_API_KEY']} (request req_fake01)")])
    claude = claude_mod.ClaudeVerifier("claude-haiku-4-5", api_key=FAKE_KEYS["ANTHROPIC_API_KEY"], client=client,
                                       breaker=verify.CircuitBreaker(), sleep=lambda s: None)
    out["claude"] = claude.verify(SPEC, images)
    out["claude_err"] = claude.verify(SPEC, images[:1])
    return out, images


@pytest.mark.parametrize("setup", ["configured_settings_no_redact", "smoke_live_redact_keys_passed"])
def test_no_key_or_engine_id_anywhere_in_the_cassette(tmp_path, setup):
    smoke = _smoke()
    web = FakeWeb()
    folder = tmp_path / "cas"
    if setup == "configured_settings_no_redact":
        # the cassette alone: the configured keys and every value sent as a key parameter are hidden
        settings_values, redact, explicit = FAKE_KEYS, None, False
    else:
        # nothing configured (keys handed to the providers), smoke_live's redact() over its secret values
        found = smoke.secret_values(lambda name: FAKE_KEYS.get(name, ""))
        settings_values, explicit = NO_KEYS, True

        def redact(text):
            return smoke.redact(text, found, query_keys=False)
    with configured(settings_values):
        cassette.install("record", str(folder), redact=redact)
        with cassette.row(2):
            live, images = _every_provider(web.session(), explicit)
        cassette.uninstall()
    assert live["serper"].status == "ok" and live["lens"].status == "ok" and live["cse"].status == "ok"
    assert live["serper_403"].status == "error" and live["lens_down"].status == "error"
    assert live["cse_down"].status == "error" and live["gemini"].status == "ok" and live["claude"].status == "ok"
    assert live["claude_err"].status == "unknown" and live["page"].ok and not live["page_missing"].ok
    assert sum(1 for ok, _e, _s in live["downloads"].values() if ok) == 2

    assert hits(folder, ALL_FAKE) == []
    recorded = entries(folder)
    for e in recorded:
        if e["request"]["kind"] == "api":        # an API's final URL is its request URL: never stored again
            assert "url" not in (e["response"] or {}), e["response"].get("url")
    blobs = {e["kind"] for e in recorded if (e["response"].get("body") or {}).get("blob")}
    assert {"serper", "lens_serpapi", "cse_legacy", "page", "image"} <= blobs, blobs

    # and the scrubbed cassette still answers every call the same way, offline, with dummy keys
    cas = cassette.install("replay", str(folder))
    dummy = {"SERPER_API_KEY": "dummy-serper-key", "SERPAPI_API_KEY": "dummy-serpapi-key",
             "GEMINI_API_KEY": "dummy-gemini-key", "ANTHROPIC_API_KEY": FAKE_KEYS["ANTHROPIC_API_KEY"],
             "GOOGLE_SEARCH_API_KEYS": ["dummy-cse-1", "dummy-cse-2"], "GOOGLE_SEARCH_CX_LIST": ["cx-1", "cx-2"]}
    with configured(dummy), cassette.offline() as attempts, cassette.row(2):
        again, _ = _every_provider(dead_session(), False)
    assert attempts == []
    for name in ("serper", "serper_403", "web", "shopping", "lens", "lens_down", "cse", "cse_large", "cse_down"):
        assert again[name].status == live[name].status, name
        assert [c.image_url for c in again[name].candidates] == [c.image_url for c in live[name].candidates], name
    assert again["downloads"] == live["downloads"]
    assert (again["page"].url, [i.url for i in again["page"].images]) == (live["page"].url,
                                                                          [i.url for i in live["page"].images])
    assert [v.decision for v in again["gemini"].verdicts] == [v.decision for v in live["gemini"].verdicts]
    assert [v.decision for v in again["claude"].verdicts] == [v.decision for v in live["claude"].verdicts]
    assert cas.row_report(2)["complete"], cas.row_report(2)


def _record(folder, fn, redact=None, row=2, values=NO_KEYS):
    with configured(values):
        cassette.install("record", str(folder), redact=redact)
        with cassette.row(row):
            result = fn()
        cas = cassette.uninstall()
    return result, cas


def test_each_layer_hides_keys_on_its_own(tmp_path):
    web = FakeWeb()
    session = web.session()

    # the response URL (requests puts the whole query in it) is cleaned like its request
    folder = tmp_path / "url"
    cse = CseLegacyProvider(CSE_KEYS, CSE_CX, today=TODAY, session=session, bucket=UNLIMITED)
    _record(folder, lambda: cse.search("Almarai milk", "en", SPEC))
    assert all("url" not in e["response"] for e in entries(folder))

    # a "cx" field the request never sent (another engine id) is blanked by the JSON key-field scrub
    folder = tmp_path / "field"
    _record(folder, lambda: cse.search("Almarai milk echo", "en", SPEC))
    assert hits(folder, ["other-engine-id-424242"]) == []
    assert '\\"cx\\": \\"[hidden]\\"' in (folder / "http.jsonl").read_text(encoding="utf-8")

    # a key the request sent as api_key, echoed in free text, is hidden by value (nothing configured, no redact)
    folder = tmp_path / "learned"
    lens = SerpApiLensProvider(api_key=FAKE_KEYS["SERPAPI_API_KEY"], session=session, bucket=UNLIMITED)
    _record(folder, lambda: lens.search(f"{CDN}/milk/1.jpg", "en", SPEC))
    assert hits(folder, [FAKE_KEYS["SERPAPI_API_KEY"]]) == []
    assert "requested with [hidden]" in gzip.decompress(next((folder / "blobs").glob("*.gz")).read_bytes()).decode()

    # a configured key the cassette never sees in a request (Serper's goes in a header) is hidden by value
    folder = tmp_path / "configured"
    serper = SerperImagesProvider(session=session, bucket=UNLIMITED)
    res, _cas = _record(folder, lambda: serper.search("forbidden almarai", "en", SPEC), values=FAKE_KEYS)
    assert res.status == "error" and hits(folder, [FAKE_KEYS["SERPER_API_KEY"]]) == []

    # the caller's redact() also reaches a large API body stored as a blob
    folder = tmp_path / "blob_redact"
    _record(folder, lambda: lens.search(f"{CDN}/milk/1.jpg", "en", SPEC),
            redact=lambda text: text.replace("Example Grocer UAE", "[owner-hidden]"))
    assert hits(folder, ["Example Grocer UAE"]) == []

    # an error text loses cx= and key= values in any position, also URL-encoded
    text = "GET /customsearch/v1?cx=017576662512468239146%3Aomuauf_lfve&q=milk&key=AIzaFAKE&num=10 failed"
    assert cassette.hide_keys_in_text(text) == "GET /customsearch/v1?cx=[hidden]&q=milk&key=[hidden]&num=10 failed"
    smoke = _smoke()
    assert "omuauf" not in smoke.redact(text, []) and "AIzaFAKE" not in smoke.redact(text, [])
    assert smoke.secret_values(lambda name: FAKE_KEYS.get(name, "")) and all(
        cx in smoke.secret_values(lambda name: FAKE_KEYS.get(name, "")) for cx in CSE_CX)


# ---------------------------------------------------------------------------
# C3: line separators inside a title
# ---------------------------------------------------------------------------

TRICKY = "Almarai Full Fat Fresh Milk\u20281L\u2029fresh\x85"


def _serper_titles(session, q="Almarai full fat milk 1L"):
    res = SerperImagesProvider(api_key="k" * 12, session=session, bucket=UNLIMITED).search(q, "en", SPEC)
    return res.status, [c.title or c.page_title for c in res.candidates]


def test_a_title_with_unicode_line_separators_is_written_escaped_and_replays(tmp_path):
    web = FakeWeb(serper_title=TRICKY, serper_n=5)          # a small body: inline in the line
    folder = tmp_path / "cas"
    live, _ = _record(folder, lambda: _serper_titles(web.session()))
    assert live[0] == "ok" and len(live[1]) == 5 and all("\u2028" in t and "\u2029" in t for t in live[1])
    raw = (folder / "http.jsonl").read_bytes()
    for ch in "\u2028\u2029\x85":
        assert ch.encode("utf-8") not in raw          # escaped: no editor or splitlines() breaks the line
    assert len(raw.decode("utf-8").splitlines()) == raw.count(b"\n") == 1

    cas = cassette.install("replay", str(folder))
    with cassette.offline(), cassette.row(2):
        assert _serper_titles(dead_session()) == live
    assert cas.row_report(2)["complete"]


def test_an_older_cassette_with_raw_line_separators_and_a_crlf_zip_replay(tmp_path):
    web = FakeWeb(serper_title=TRICKY, serper_n=5)
    folder = tmp_path / "cassette_old"
    with configured(NO_KEYS):
        cas = cassette.install("record", str(folder))
        cas.write_meta({"format": cassette.FORMAT})
        with cassette.row(2):
            live = _serper_titles(web.session())
        cassette.uninstall()
    # what the first version wrote: the characters raw inside the line
    text = (folder / "http.jsonl").read_text(encoding="utf-8")
    old = text.replace("\\u2028", "\u2028").replace("\\u2029", "\u2029").replace("\\u0085", "\x85")
    assert old != text
    (folder / "http.jsonl").write_bytes(old.encode("utf-8"))
    cas = cassette.install("replay", str(folder))
    with cassette.offline(), cassette.row(2):
        assert _serper_titles(dead_session()) == live
    assert cas.row_report(2)["complete"] and cas.row_report(2)["misses"] == []
    cassette.uninstall()

    # copied through Windows (CRLF line ends) and zipped by PowerShell (backslash names)
    (folder / "http.jsonl").write_bytes(old.replace("\n", "\r\n").encode("utf-8"))
    archive = tmp_path / "cassette_old.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        for path in folder.rglob("*"):
            if path.is_file():
                zf.write(path, "cassette_old\\" + str(path.relative_to(folder)).replace("/", "\\"))
    cas = cassette.install("replay", str(archive))
    with cassette.offline(), cassette.row(2):
        assert _serper_titles(dead_session()) == live
    assert cas.row_report(2)["complete"]


# ---------------------------------------------------------------------------
# C4: a blob that is missing, damaged or held by another program
# ---------------------------------------------------------------------------

def _downloads(session, names=("1.jpg", "2.jpg"), max_bytes=None):
    fetcher = HttpFetcher(session=session) if max_bytes is None else HttpFetcher(session=session, max_bytes=max_bytes)
    got = fetcher.fetch([Candidate(image_url=f"{CDN}/milk/{n}") for n in names])
    return {fi.candidate.image_url.rsplit("/", 1)[-1]: (fi.ok, fi.error, fi.content_sha256) for fi in got}


def test_a_missing_or_damaged_blob_is_a_reported_miss_and_a_fill_fetches_it_again(tmp_path, monkeypatch):
    monkeypatch.setattr("catalog_match.settings.proxy_url", lambda: "")
    web = FakeWeb()
    folder = tmp_path / "cas"
    live, _ = _record(folder, lambda: _downloads(web.session()), row=7)
    assert live["1.jpg"][0] and live["2.jpg"][0]
    by_name = {e["request"]["url"].rsplit("/", 1)[-1]: e["response"]["body"]["blob"] for e in entries(folder)}
    (folder / "blobs" / by_name["1.jpg"]).unlink()                                  # lost in a partial copy
    damaged = folder / "blobs" / by_name["2.jpg"]
    damaged.write_bytes(damaged.read_bytes()[:1000])                               # cut short

    cas = cassette.install("replay", str(folder))
    with cassette.offline(), cassette.row(7):
        again = _downloads(dead_session())
    assert again == {"1.jpg": (False, "not_recorded", None), "2.jpg": (False, "not_recorded", None)}
    report = cas.row_report(7)
    assert not report["complete"] and [m["kind"] for m in report["misses"]] == ["image", "image"]
    assert all("missing or damaged" in m["reason"] for m in report["misses"])
    cassette.uninstall()

    sent = len(web.sent)
    with configured(NO_KEYS):
        cassette.install("fill", str(folder))
        with cassette.row(7):
            filled = _downloads(web.session())
        cassette.uninstall()
    assert filled == live and len(web.sent) == sent + 2          # fetched live again and stored
    cas = cassette.install("replay", str(folder))
    with cassette.offline(), cassette.row(7):
        assert _downloads(dead_session()) == live
    assert cas.row_report(7)["complete"]


def _held_blobs(monkeypatch, failures):
    """os.replace into blobs/ fails like a file an antivirus holds on Windows (the first `failures` times)."""
    real = os.replace
    count = {"n": 0}

    def replace(src, dst):
        if os.path.basename(os.path.dirname(str(dst))) == cassette.BLOB_DIR:
            count["n"] += 1
            if count["n"] <= failures:
                raise PermissionError(32, "The process cannot access the file because it is being used by another "
                                          "process", str(dst))
        return real(src, dst)

    monkeypatch.setattr(cassette, "REPLACE_WAITS", (0, 0, 0))
    monkeypatch.setattr(os, "replace", replace)
    return count


def test_a_blob_held_by_another_program_is_retried_then_kept_and_still_replays(tmp_path, monkeypatch):
    monkeypatch.setattr("catalog_match.settings.proxy_url", lambda: "")
    web = FakeWeb()
    # a short hold: the retry puts the blob in place
    _held_blobs(monkeypatch, failures=2)
    live, cas = _record(tmp_path / "short", lambda: _downloads(web.session(), ("1.jpg",)), row=7)
    assert live["1.jpg"][0] and cas.row_report(7)["complete"] and cas.row_report(7)["not_stored"] == []
    assert not list((tmp_path / "short" / "blobs").glob("*.tmp"))

    # a hold that outlasts every retry: the live result is unchanged, the bytes stay under the temporary name,
    # the recording's row report says so, and a replay still finds them
    _held_blobs(monkeypatch, failures=10 ** 6)
    folder = tmp_path / "long"
    live, cas = _record(folder, lambda: _downloads(web.session(), ("1.jpg",)), row=7)
    assert live["1.jpg"][0]
    report = cas.row_report(7)
    assert not report["complete"] and "temporary name" in report["not_stored"][0]["what"]
    assert "PermissionError" in report["not_stored"][0]["error"]
    kept = list((folder / "blobs").glob("*.tmp"))
    assert len(kept) == 1 and entries(folder)[0]["response"]["body"]["blob"] in kept[0].name
    monkeypatch.undo()
    cas = cassette.install("replay", str(folder))
    with cassette.offline(), cassette.row(7):
        assert _downloads(dead_session(), ("1.jpg",)) == live
    assert cas.row_report(7)["complete"]


# ---------------------------------------------------------------------------
# C7: a failing store never changes the live run
# ---------------------------------------------------------------------------

def test_a_full_disk_never_changes_the_live_answers_and_is_reported_once(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr("catalog_match.settings.proxy_url", lambda: "")
    monkeypatch.setattr(spend_mod.MariaDbSpendStore, "role_spend", lambda self, role="strong", month=None: 3.5)
    monkeypatch.setattr(local_index.DbCatalogStore, "count", lambda self, fresh=False: 4321)
    rows = [local_index.CatalogRow(id=1, store="lulu", url=f"{SHOP}/p/9", slug_text="almarai full fat milk 1l")]
    web = FakeWeb(gemini_busy_first=False)

    def run(session):
        out = {"serper": _serper_titles(session), "downloads": _downloads(session)}
        page = PageFetcher(session=session, bucket_factory=lambda host: UNLIMITED)._fetch_uncached(f"{SHOP}/p/77", "")
        out["page"] = (page.ok, page.url, [i.url for i in page.images])
        images = [fi for fi in HttpFetcher(session=session).fetch([Candidate(image_url=f"{CDN}/milk/1.jpg")])]
        out["gemini"] = verify.GeminiVerifier(api_key="g" * 12, model="gemini-3.1-flash-lite", session=session,
                                              breaker=verify.BREAKER, sleep=lambda s: None).verify(SPEC, images)
        out["claude"] = claude_mod.ClaudeVerifier("claude-haiku-4-5", api_key="c" * 12,
                                                  client=FakeClaudeClient([claude_message()]),
                                                  breaker=claude_mod.CLAUDE_BREAKER, sleep=lambda s: None
                                                  ).verify(SPEC, images)
        out["index_rows"] = cassette.local_index_rows(SPEC, lambda: list(rows))
        with ThreadPoolExecutor(1) as ex:
            f = ex.submit(lambda: 1)
            f.result()
            out["deadline"] = cassette.local_index_deadline({f: 0}, {0: rows[0]}, {f}, set()) == ({f}, set())
        out["breaker_open"] = verify.BREAKER.is_open()
        out["spend"] = spend_mod.MariaDbSpendStore().role_spend("strong")
        out["count"] = local_index.DbCatalogStore().count()
        return out

    with configured(NO_KEYS):
        baseline = run(web.session())
        verify.BREAKER.reset()
        cas = cassette.install("record", str(tmp_path / "cas"))

        def full(*args, **kwargs):
            raise OSError(errno.ENOSPC, "No space left on device")

        monkeypatch.setattr(cas.store, "append_line", full)
        monkeypatch.setattr(cas.store, "write_blob", full)
        with caplog.at_level(logging.DEBUG, logger="catalog_match.cassette"), cassette.row(2):
            recorded = run(web.session())
        cassette.uninstall()
    for name in ("serper", "downloads", "page", "index_rows", "deadline", "breaker_open", "spend", "count"):
        assert recorded[name] == baseline[name], name
    for name in ("gemini", "claude"):
        assert recorded[name].status == baseline[name].status == "ok", (name, recorded[name].error)
        assert [v.decision for v in recorded[name].verdicts] == [v.decision for v in baseline[name].verdicts]
    assert verify.BREAKER.consecutive_unknown == 0          # the label reader's success was counted as one
    report = cas.row_report(2)
    assert not report["complete"] and len(report["not_stored"]) >= 10
    assert all("No space left on device" in s["error"] for s in report["not_stored"])
    kinds = {s["what"].split(" ")[0] for s in report["not_stored"]}
    assert {"serper", "image", "page", "label-reader", "local", "event"} <= kinds, kinds
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR and "could not be stored" in r.getMessage()]
    assert len(errors) == 1


# ---------------------------------------------------------------------------
# C8: a local proxy is no way out
# ---------------------------------------------------------------------------

class FakeProxy:
    """A proxy on this machine that records what reaches it (started before the guard, like a real one)."""

    def __init__(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        self.accepted = 0
        self.received = []
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while True:
            try:
                conn, _addr = self.sock.accept()
            except OSError:
                return
            self.accepted += 1
            with conn:
                conn.settimeout(2)
                try:
                    self.received.append(conn.recv(4096))
                    conn.sendall(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")
                except OSError:
                    pass

    def close(self):
        self.sock.close()


@pytest.fixture
def local_proxy(monkeypatch):
    proxy = FakeProxy()
    for name in ("NO_PROXY", "no_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(name, raising=False)
    for name in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        monkeypatch.setenv(name, f"http://127.0.0.1:{proxy.port}")
    yield proxy
    proxy.close()


def test_the_guard_refuses_a_proxy_on_this_machine(local_proxy):
    with cassette.offline() as attempts:
        with pytest.raises(requests.exceptions.RequestException):
            requests.get("https://google.serper.dev/images", timeout=3)
    assert local_proxy.accepted == 0 and local_proxy.received == []      # not even connected to
    assert f"connect ('127.0.0.1', {local_proxy.port})" in attempts


def test_the_replay_environment_drops_the_proxy_settings(local_proxy, tmp_path):
    replay_run = importlib.util.module_from_spec(spec := importlib.util.spec_from_file_location(
        "replay_run_guard_test", REPO / "scripts" / "replay_run.py"))
    spec.loader.exec_module(replay_run)
    meta = {"settings": {"values": {}, "configured": {}}}
    with replay_run.replay_environment(meta, str(tmp_path / "cands")) as attempts:
        assert not any(os.environ.get(n) for n in replay_run.PROXY_ENV) and os.environ["NO_PROXY"] == "*"
        with pytest.raises(requests.exceptions.RequestException):
            requests.get("https://google.serper.dev/images", timeout=3)
    assert local_proxy.accepted == 0 and local_proxy.received == []
    assert attempts == ["getaddrinfo 'google.serper.dev'"]                 # tried directly, refused
    assert os.environ["HTTPS_PROXY"] == f"http://127.0.0.1:{local_proxy.port}"   # restored afterwards


def test_a_request_through_a_proxy_is_refused_even_on_an_allowed_socket():
    with cassette.offline() as attempts:
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.bind(("127.0.0.1", 0))
        server.listen(1)                       # this process listens here (socketpair() on Windows does this)
        client = socket.create_connection(server.getsockname(), timeout=2)
        conn, _addr = server.accept()
        client.sendall(b"ping")
        assert conn.recv(4) == b"ping"
        with pytest.raises(OSError):
            client.sendall(b"CONNECT google.serper.dev:443 HTTP/1.1\r\nHost: google.serper.dev:443\r\n\r\n")
        with pytest.raises(OSError):
            client.send(b"GET http://google.serper.dev/images HTTP/1.1\r\n\r\n")
        for s in (client, conn, server):
            s.close()
    assert attempts == ["proxy 'CONNECT google.serper.dev:443 HTTP/1.1'",
                        "proxy 'GET http://google.serper.dev/images HTTP/1.1'"]


# ---------------------------------------------------------------------------
# C9: a fill after an interrupted recording
# ---------------------------------------------------------------------------

def test_a_fill_after_an_interrupted_recording_keeps_its_first_answer(tmp_path, caplog):
    web = FakeWeb()
    folder = tmp_path / "cas"
    _record(folder, lambda: _serper_titles(web.session(), "almarai one"))
    with open(folder / "http.jsonl", "a", encoding="utf-8") as fh:
        fh.write('{"kind": "serper", "key": "half-writ')                  # the run was killed mid-line
    with configured(NO_KEYS):
        cassette.install("fill", str(folder))
        with cassette.row(3):
            live = _serper_titles(web.session(), "almarai two")
        cassette.uninstall()
    cas = cassette.install("replay", str(folder))
    with caplog.at_level(logging.WARNING, logger="catalog_match.cassette"), cassette.offline(), cassette.row(3):
        assert _serper_titles(dead_session(), "almarai two") == live
    assert cas.row_report(3)["complete"]
    assert any("is not JSON" in r.getMessage() for r in caplog.records)       # only the cut line is lost


# ---------------------------------------------------------------------------
# P2 / P3 / P6: nothing silently substituted
# ---------------------------------------------------------------------------

def test_a_page_read_the_deadline_discarded_is_a_listed_miss_when_asked_again(tmp_path, monkeypatch):
    monkeypatch.setattr("catalog_match.settings.proxy_url", lambda: "")
    from catalog_match import pages
    late = f"{SHOP}/p/late"
    row = local_index.CatalogRow(id=5, store="spinneys", url=late)
    with ThreadPoolExecutor(1) as ex:
        f = ex.submit(lambda: 1)
        f.result()
        # recorded: the index read of this page was late, and the run ended before it finished
        _record(tmp_path / "cas", lambda: cassette.local_index_deadline({f: 0}, {0: row}, set(), {f}))
    cas = cassette.install("replay", str(tmp_path / "cas"))
    pages.clear_cache()
    reader = PageFetcher(session=dead_session(), bucket_factory=lambda host: UNLIMITED)
    with cassette.offline(), cassette.row(2), ThreadPoolExecutor(1) as ex:
        g = ex.submit(reader.fetch_page, late)
        assert g.result().error == "not_recorded"
        cassette.local_index_deadline({g: 0}, {0: row}, {g}, set())
        assert cas.row_report(2)["misses"] == []            # the discarded read is no miss, as live
        again = reader.fetch_page(late)                     # the pipeline's own read of the same page
    assert again.error == "not_recorded"
    assert [m["request"]["url"] for m in cas.row_report(2)["misses"]] == [late]


def test_a_cut_or_unread_body_is_a_miss_for_code_that_reads_more(tmp_path, monkeypatch):
    monkeypatch.setattr("catalog_match.settings.proxy_url", lambda: "")
    web = FakeWeb()
    folder = tmp_path / "cas"
    # big.jpg announces its length (over the limit: not read); chunked.jpg does not (read up to the limit + 1)
    live, _ = _record(folder, lambda: _downloads(web.session(), ("big.jpg", "chunked.jpg"), max_bytes=4000))
    assert live == {"big.jpg": (False, "too_large", None), "chunked.jpg": (False, "too_large", None)}
    cas = cassette.install("replay", str(folder))
    with cassette.offline(), cassette.row(2):
        assert _downloads(dead_session(), ("big.jpg", "chunked.jpg"), max_bytes=4000) == live    # same limit
        assert cas.row_report(2)["complete"]
    with cassette.offline(), cassette.row(3):
        bigger = _downloads(dead_session(), ("big.jpg", "chunked.jpg"), max_bytes=10 ** 7)
    assert bigger == {"big.jpg": (False, "not_recorded", None), "chunked.jpg": (False, "not_recorded", None)}
    reasons = sorted(m["reason"] for m in cas.row_report(3)["misses"])
    assert "was cut at 4001 bytes" in reasons[1] and "over the recording's limit (4000)" in reasons[0]


def test_changed_index_rows_and_extra_breaker_asks_are_visible(tmp_path):
    rows = [local_index.CatalogRow(id=1, store="lulu", url=f"{SHOP}/p/9", slug_text="almarai full fat milk 1l")]
    folder = tmp_path / "cas"

    def record():
        assert verify.BREAKER.is_open() is False
        return cassette.local_index_rows(SPEC, lambda: list(rows))

    _record(folder, record)
    text = (folder / "local_index.jsonl").read_text(encoding="utf-8")
    (folder / "local_index.jsonl").write_text(text.replace('"store": "lulu"', '"store": "lulu", "region": "dxb"'),
                                              encoding="utf-8")
    cas = cassette.install("replay", str(folder))
    with cassette.offline(), cassette.row(2):
        assert cassette.local_index_rows(SPEC, lambda: []) == rows      # a field this code lacks: dropped
        assert verify.BREAKER.is_open() is False
        assert verify.BREAKER.is_open() is False                       # asked once more than recorded
    notes = " | ".join(cas.row_report(2)["approximations"])
    assert "no longer has dropped (region)" in notes and "breaker:gemini: asked more often than recorded" in notes
    with cassette.offline(), cassette.row(9):
        verify.BREAKER.is_open()
    assert "breaker:gemini: not recorded for this row" in " ".join(cas.row_report(9)["approximations"])
    cassette.uninstall()

    # a row this code cannot build at all is a miss, never a provider crash
    (folder / "local_index.jsonl").write_text(text.replace('"url":', '"page_link":'), encoding="utf-8")
    cas = cassette.install("replay", str(folder))
    with cassette.offline(), cassette.row(2), pytest.raises(cassette.NotRecorded):
        cassette.local_index_rows(SPEC, lambda: [])
    assert [m["kind"] for m in cas.row_report(2)["misses"]] == ["local_index"]
    assert "do not fit this code" in cas.row_report(2)["misses"][0]["reason"]


def test_a_replay_skips_the_retry_back_off_and_counts_it(tmp_path):
    web = FakeWeb(gemini_busy_first=True)
    images = HttpFetcher(session=web.session()).fetch([Candidate(image_url=f"{CDN}/milk/1.jpg")])
    waits = []

    def reader(session, sleep):
        return verify.GeminiVerifier(api_key="g" * 12, model="gemini-3.1-flash-lite", session=session,
                                     breaker=verify.CircuitBreaker(), sleep=sleep, backoff_s=2.0)

    live, _ = _record(tmp_path / "cas", lambda: reader(web.session(), waits.append).verify(SPEC, images))
    assert live.status == "ok" and waits == [2.0]                   # live: the 503 was waited out
    cas = cassette.install("replay", str(tmp_path / "cas"))
    with cassette.offline(), cassette.row(2):
        again = reader(dead_session(), waits.append).verify(SPEC, images)
    assert again.status == "ok" and waits == [2.0]                  # no wait in the replay
    assert cas.row_report(2)["retry_wait_s"] == 2.0


def test_a_shadow_recording_prices_its_searches_at_the_runs_serp_cost(tmp_path, monkeypatch):
    from catalog_match.models import ProviderResult

    calls = []

    def search(self, query, hl, spec):
        calls.append(self.name)
        return ProviderResult(provider=self.name, status="empty")

    monkeypatch.setattr(SerperWebProvider, "search", search)
    monkeypatch.setattr(SerperShoppingProvider, "search", search)
    monkeypatch.setattr("catalog_match.settings.serper_api_key", lambda: "s" * 12)
    outcome = types.SimpleNamespace(ranked=[], decision="NO_MATCH")
    with configured(NO_KEYS):
        cassette.install("record", str(tmp_path / "cas"))
        with cassette.row(2):
            report = cassette.shadow_record(SPEC, outcome, serp_cost=0.0125)
        cassette.uninstall()
    assert "error" not in report and calls == ["serper_web", "serper_shopping"]
    assert report["search_calls"] == 2 and report["cost_usd"] == pytest.approx(0.025)
