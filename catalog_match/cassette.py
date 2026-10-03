"""Record and replay of live runs: every external answer of a dry run, kept in a cassette (P7).

A live dry run (scripts/smoke_live.py --record DIR) stores every answer the pipeline gets from outside:
search API responses, image downloads, product pages, label-reader replies, the local-index rows it read
from the database and the few decisions that depend on the wall clock or the database (which local-index
page reads finished in time, the label readers' circuit breakers, the strong reader's month spend, the
local-index size). scripts/replay_run.py DIR then runs the same products again, offline and for free,
against any version of the code: every hook answers from the cassette, and a request the cassette does not
hold is reported, never silently invented.

install(mode, directory, redact=None) / uninstall() / active() / mode() / replaying()
    'record'  the real call is made and its answer stored;
    'replay'  the answer comes from the cassette; a miss is reported (below);
    'fill'    the cassette's answer when it has one, else the real call, stored (tops a cassette up).
row(number)
    context for the current sheet row. The pipeline's thread pools run inside it, so it is process-wide
    (one row at a time, as smoke_live runs them).

Hooks (each is a plain pass-through while no cassette is installed):
    http(kind, method, url, send, ...)        the providers' API calls, image downloads and page reads
    verifier_scope(...) + verifier(send)      one label-reader call (GeminiVerifier._post, ClaudeVerifier._send)
    local_index_rows(spec, read)              the rows LocalIndexProvider read from the index
    local_index_deadline(...)                 which index page reads finished within READ_DEADLINE_S
    miss_code(exc)                            'not_recorded' for a replay miss (a download's error code)

Layout of a cassette (a directory, or a zip of one; a zip is read-only):
    meta.json          format, git commit, settings snapshot WITHOUT secrets, the brand mappings the run used
                       (after learning), the rows, Python / Pillow / numpy versions, strong-reader spend
    http.jsonl         {key, kind, request, response, row, attempt, seq[, shadow]}
    verifier.jsonl     {key, kind 'verifier', request {provider, model, focus, long_side, prompt_sha256, images},
                        response, readings, row, attempt, seq} and events {kind 'event', name, row, n, value}
    local_index.jsonl  {key, kind 'rows' | 'deadline', ...} and events (index size)
    blobs/<sha256>     bodies of images; '<sha256>.gz' gzip-compressed pages and large API bodies

Secrets never enter a cassette: requests are stored without headers; query and body parameters named like a
key (api_key, key, cx, X-API-KEY, Authorization, token, ...) are dropped; an exception text loses any 'key=...'
value; every stored line passes the caller's redact() (scripts/smoke_live.py passes its redact() over the
configured secret values). meta.json keeps only whether each secret setting was set (or how many keys).

Keys: method + canonical URL (+ the JSON body of a POST); a search text ('q') is case-folded with its
whitespace collapsed; an image download is also found under providers.base.canonical_image_url of its URL;
a redirect keeps its final URL. A label-reader call is keyed on (provider, model, focus, long side, prompt
sha256, the image CONTENT sha256s in slot order), never on the re-encoded JPEG, so a Pillow upgrade moves no
key. Answers are looked up by key and attempt number within the row (the pipeline's threads may ask in any
order). A key the row never asked is answered from the nearest row that asked it (the in-process page cache
would have served it there); a repeat beyond the recorded attempts reuses the last answer only when that
answer was a success (a retry after a failure is not known, so it is a miss).

Replay misses (never silent: each one is listed in the row's replay report and in the misses manifest):
    provider       NotRecorded raised: ProviderResult status 'error', error 'NotRecorded: not_recorded'
    image / page   the download fails with error 'not_recorded' (a page: PageInfo(error='not_recorded'))
    label reader   the image's reading from another recorded call of the same row and model (the row is
                   marked approximate), else the call fails closed (UNKNOWN) and the row is incomplete
    index rows     the row's other recorded index rows (approximate), else the lookup fails ('not_recorded')

shadow_record(spec, outcome) (record mode, --record-shadow) stores extra answers after the live decision of a
row is final, for later code that ranks differently: every pooled image up to SHADOW_MAX_IMAGES, the pages of
the tier-1/2 listings, X1/X2 for a row without a pick, and a primary reading of every downloaded image.
"""

from __future__ import annotations

import contextlib
import gzip
import hashlib
import importlib
import json
import logging
import os
import re
import socket
import threading
import uuid
import zipfile
from typing import Any, Callable, Dict, Iterable, Iterator, List, Mapping, Optional, Sequence, Tuple
from unittest import mock
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

logger = logging.getLogger(__name__)

FORMAT = "cassette/1"
MISSES_FORMAT = "cassette_misses/1"
MODES = ("record", "replay", "fill")
META_FILE = "meta.json"
HTTP_FILE = "http.jsonl"
VERIFIER_FILE = "verifier.jsonl"
INDEX_FILE = "local_index.jsonl"
BLOB_DIR = "blobs"
NOT_RECORDED = "not_recorded"
INLINE_MAX = 64 * 1024          # API bodies up to this size stay inline in the JSONL line
STREAM_CHUNK = 64 * 1024
KEPT_HEADERS = ("Content-Type", "Content-Length", "Retry-After")
REPLAY_WAIT_S = 120.0           # replayed page reads answer at once; a fill run's live reads may take this long
SHADOW_MAX_IMAGES = 24
SHADOW_MAX_PAGES = 12
SHADOW_VERIFY_BATCH = 4
READING_FIELDS = ("brand_text", "variant_text", "size_text", "pack_count", "view", "brand_match", "variant_match",
                  "size_match")

# Request parameters that carry a credential (or an account id) and never enter a cassette.
_SECRET_PARAM_RE = re.compile(r"(?i)^(api_?key|apikey|key|cx|x-api-key|x-goog-api-key|authorization|token|"
                              r"access_token|secret|password|auth|signature)$")
# Settings that are secrets: a cassette keeps only whether they were set (or how many there were).
_SECRET_SETTING_RE = re.compile(r"(?i)(KEY|SECRET|TOKEN|PASSWORD|PROXY_URL|_CX)")
_KEY_IN_TEXT_RE = re.compile(r"(?i)((?:api_?)?key=)[^&\s'\"]+")
_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


class NotRecorded(Exception):
    """A replay asked for an answer the cassette does not hold."""

    def __init__(self, what: str = "") -> None:
        super().__init__(NOT_RECORDED)
        self.what = what


# ---------------------------------------------------------------------------
# Module state and the public hooks
# ---------------------------------------------------------------------------

_STATE: Optional["Cassette"] = None
_STATE_LOCK = threading.Lock()


def install(mode: str, directory: str, redact: Optional[Callable[[str], str]] = None) -> "Cassette":
    """Install the process-wide cassette (an installed one is closed first) and return it."""
    global _STATE
    if mode not in MODES:
        raise ValueError(f"cassette mode must be one of {MODES}, not {mode!r}")
    with _STATE_LOCK:
        old, _STATE = _STATE, None
    if old is not None:
        logger.warning("cassette: a %s cassette was still installed; closed", old.mode)
        old.close()
    cas = Cassette(mode, directory, redact=redact)
    with _STATE_LOCK:
        _STATE = cas
    return cas


def uninstall() -> Optional["Cassette"]:
    """Close and remove the installed cassette (returns it, or None)."""
    global _STATE
    with _STATE_LOCK:
        cas, _STATE = _STATE, None
    if cas is not None:
        cas.close()
    return cas


def active() -> Optional["Cassette"]:
    return _STATE


def mode() -> str:
    """'record' | 'replay' | 'fill', or '' when no cassette is installed."""
    cas = _STATE
    return cas.mode if cas is not None else ""


def replaying() -> bool:
    """True while answers may come from a cassette (replay and fill): nothing is written back to the index."""
    cas = _STATE
    return cas is not None and cas.mode in ("replay", "fill")


@contextlib.contextmanager
def row(number: int) -> Iterator[None]:
    """The sheet row every answer inside belongs to (no-op without a cassette)."""
    cas = _STATE
    if cas is None:
        yield
        return
    with cas.row_context(number):
        yield


def http(kind: str, method: str, url: str, send: Callable[[], Any], *, params: Any = None, body: Any = None,
         headers: Optional[Mapping[str, str]] = None, proxy: bool = False, stream: bool = False,
         max_bytes: Optional[int] = None) -> Any:
    """send() (the real HTTP call) through the cassette. kind: the provider name, or 'fetch' for a download."""
    cas = _STATE
    if cas is None:
        return send()
    return cas.http(kind, method, url, send, params=params, body=body, headers=headers, proxy=proxy,
                    stream=stream, max_bytes=max_bytes)


def verifier_scope(provider: str, model: str, focus: bool, long_side: int, prompt: str,
                   images: Sequence[Any], slots: Sequence[int]) -> None:
    """Key the next label-reader call of this thread (the images actually sent are images[slots[i]])."""
    cas = _STATE
    if cas is not None:
        cas.set_scope(provider, model, focus, long_side, prompt, images, slots)


def verifier(send: Callable[[], Any]) -> Any:
    """send() (one label-reader HTTP / SDK call) through the cassette, keyed by verifier_scope()."""
    cas = _STATE
    if cas is None:
        return send()
    return cas.verifier(send)


def local_index_rows(spec: Any, read: Callable[[], List[Any]]) -> List[Any]:
    """The index rows for the SKU: read() from the database, or the cassette's snapshot of them."""
    cas = _STATE
    if cas is None:
        return read()
    return cas.local_index_rows(spec, read)


def local_index_deadline(futures: Mapping[Any, int], rows: Mapping[int, Any], done: set, pending: set
                         ) -> Tuple[set, set]:
    """(done, pending) of the index page reads at READ_DEADLINE_S, as the recorded run saw them."""
    cas = _STATE
    if cas is None:
        return done, pending
    return cas.local_index_deadline(futures, rows, done, pending)


def miss_code(exc: BaseException) -> Optional[str]:
    """'not_recorded' for a replay miss, else None (the caller's own error mapping applies)."""
    return NOT_RECORDED if isinstance(exc, NotRecorded) else None


# ---------------------------------------------------------------------------
# Canonical requests and keys
# ---------------------------------------------------------------------------

def _norm_text(value: Any) -> str:
    return " ".join(str(value or "").split()).casefold()


def _clean_pairs(pairs: Iterable[Tuple[Any, Any]]) -> List[Tuple[str, str]]:
    out = []
    for k, v in pairs:
        k = str(k)
        if _SECRET_PARAM_RE.match(k):
            continue
        out.append((k, _norm_text(v) if k.lower() == "q" else str(v)))
    return sorted(out)


def _clean_body(body: Any) -> Any:
    if isinstance(body, Mapping):
        return {str(k): (_norm_text(v) if str(k).lower() == "q" else _clean_body(v))
                for k, v in sorted(body.items(), key=lambda kv: str(kv[0])) if not _SECRET_PARAM_RE.match(str(k))}
    if isinstance(body, (list, tuple)):
        return [_clean_body(v) for v in body]
    return body


def canonical_request(kind: str, method: str, url: str, params: Any = None, body: Any = None,
                      proxy: bool = False) -> Dict[str, Any]:
    """The stored, secret-free form of a request; its JSON is what the key is computed from."""
    method = (method or "GET").upper()
    url = (url or "").strip().split("#", 1)[0]
    if kind != "fetch":
        try:
            parts = urlsplit(url)
            pairs = parse_qsl(parts.query, keep_blank_values=True)
            if isinstance(params, Mapping):
                pairs += list(params.items())
            elif params:
                pairs += list(params)
            url = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(_clean_pairs(pairs)), ""))
        except ValueError:
            pass
    request: Dict[str, Any] = {"kind": "fetch" if kind == "fetch" else "api", "method": method, "url": url}
    if body is not None:
        request["body"] = _clean_body(body)
    if proxy:
        request["proxy"] = True
    return request


def request_key(request: Mapping[str, Any]) -> str:
    text = json.dumps(request, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:40]


def _alias_url(url: str) -> str:
    """The URL providers.base.canonical_image_url gives (original image behind a resized CDN rendition)."""
    try:
        from .providers.base import canonical_image_url
        return canonical_image_url(url)
    except Exception:  # pragma: no cover - defensive
        return url


def image_sha(fetched: Any) -> str:
    """sha256 of an image's downloaded bytes (FetchedImage.content_sha256, else computed)."""
    sha = getattr(fetched, "content_sha256", None)
    if sha:
        return str(sha)
    src = getattr(fetched, "path_or_bytes", None)
    try:
        if isinstance(src, (bytes, bytearray)):
            data = bytes(src)
        else:
            with open(str(src), "rb") as fh:
                data = fh.read()
    except Exception:
        return ""
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# Responses
# ---------------------------------------------------------------------------

def _headers_of(resp: Any) -> Dict[str, str]:
    raw = getattr(resp, "headers", None) or {}
    out = {}
    for name in KEPT_HEADERS:
        try:
            value = raw.get(name)
            if value is None:
                value = raw.get(name.lower())
        except Exception:
            value = None
        if value is not None:
            out[name] = str(value)
    return out


class _Headers(dict):
    """Case-insensitive read access, like requests' CaseInsensitiveDict."""

    def get(self, key: Any, default: Any = None) -> Any:
        low = str(key).lower()
        for k, v in self.items():
            if k.lower() == low:
                return v
        return default

    def __getitem__(self, key: Any) -> Any:
        value = self.get(key, None)
        if value is None:
            raise KeyError(key)
        return value

    def __contains__(self, key: Any) -> bool:
        return self.get(key, None) is not None


class Replayed:
    """A requests-like response rebuilt from the cassette (also what a streamed download returns in record mode)."""

    def __init__(self, status_code: int, headers: Optional[Mapping[str, str]] = None, content: bytes = b"",
                 url: str = "", stream_error: Optional[BaseException] = None) -> None:
        self.status_code = int(status_code or 0)
        self.headers = _Headers(headers or {})
        self.content = content or b""
        self.url = url
        self._stream_error = stream_error
        self.encoding = "utf-8"
        self.reason = ""

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 400

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")

    def json(self, **kwargs: Any) -> Any:
        return json.loads(self.text.lstrip("﻿"), **kwargs)

    def iter_content(self, chunk_size: int = STREAM_CHUNK, decode_unicode: bool = False) -> Iterator[bytes]:
        size = max(1, int(chunk_size or STREAM_CHUNK))
        for i in range(0, len(self.content), size):
            yield self.content[i:i + size]
        if self._stream_error is not None:
            raise self._stream_error

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"http_{self.status_code}")

    def close(self) -> None:
        return None


def _exc_info(exc: BaseException, redact: Callable[[str], str]) -> Dict[str, Any]:
    # a transport error's text can hold the request URL, and SerpApi's carries the key in its query
    message = _KEY_IN_TEXT_RE.sub(r"\1[hidden]", redact(str(exc)))[:500]
    info: Dict[str, Any] = {"module": type(exc).__module__, "type": type(exc).__name__,
                            "mro": [c.__name__ for c in type(exc).__mro__], "message": message}
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        info["status_code"] = status
    return info


def _httpx():
    for name in ("httpx", "httpx2"):
        try:
            return importlib.import_module(name)
        except Exception:
            continue
    return None


def _anthropic_error(info: Mapping[str, Any]) -> Optional[BaseException]:
    try:
        import anthropic
        httpx = _httpx()
        request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        mro = info.get("mro") or []
        if "APITimeoutError" in mro:
            return anthropic.APITimeoutError(request=request)
        if "APIConnectionError" in mro:
            return anthropic.APIConnectionError(request=request)
        status = int(info.get("status_code") or 500)
        return anthropic.APIStatusError(str(info.get("message") or f"http {status}"),
                                        response=httpx.Response(status, request=request), body=None)
    except Exception:
        return None


def rebuild_exception(info: Mapping[str, Any]) -> BaseException:
    """The recorded exception again: the same class when it can be imported, else one with the same name."""
    message = str(info.get("message") or "")
    mro = info.get("mro") or []
    if any(name in mro for name in ("APIStatusError", "APITimeoutError", "APIConnectionError")):
        exc = _anthropic_error(info)
        if exc is not None:
            return exc
    try:
        cls = getattr(importlib.import_module(str(info.get("module"))), str(info.get("type")))
        if isinstance(cls, type) and issubclass(cls, BaseException):
            return cls(message)
    except Exception:
        pass
    name = re.sub(r"\W", "_", str(info.get("type") or "RecordedError")) or "RecordedError"
    return type(name, (Exception,), {"__module__": __name__})(message)


def _dump_message(resp: Any) -> Any:
    """An SDK message as plain JSON data (ClaudeVerifier reads dicts and objects alike)."""
    if isinstance(resp, (dict, list)):
        return json.loads(json.dumps(resp, default=str))
    for attempt in (lambda: resp.model_dump(mode="json"), lambda: json.loads(resp.model_dump_json()),
                    lambda: resp.to_dict()):
        try:
            data = attempt()
            if isinstance(data, dict):
                return data
        except Exception:
            continue
    return json.loads(json.dumps(getattr(resp, "__dict__", {}), default=str))


def _reply_text(provider: str, response: Mapping[str, Any]) -> str:
    if provider == "claude":
        message = response.get("message") or {}
        for block in message.get("content") or []:
            if isinstance(block, dict) and block.get("type") == "text":
                return str(block.get("text") or "")
        return ""
    payload = response.get("json")
    try:
        parts = payload["candidates"][0]["content"]["parts"]
        return "".join(p.get("text", "") for p in parts if isinstance(p, dict))
    except (KeyError, IndexError, TypeError):
        return ""


def readings_of(provider: str, response: Mapping[str, Any], shas: Sequence[str]) -> List[Dict[str, Any]]:
    """Per-image readings of one recorded reply: [{sha, fields}] (the fallback for a later batch)."""
    try:
        data = json.loads(_FENCE_RE.sub("", _reply_text(provider, response)).strip())
    except (ValueError, TypeError):
        return []
    out = []
    for order, entry in enumerate(data.get("images") or [] if isinstance(data, dict) else []):
        if not isinstance(entry, dict):
            continue
        label = entry.get("image_index")
        if isinstance(label, bool) or not isinstance(label, int):
            label = order + 1
        if 1 <= label <= len(shas):
            out.append({"sha": shas[label - 1], "fields": {k: entry.get(k) for k in READING_FIELDS}})
    return out


def _synthetic_reply(provider: str, model: str, entries: List[Dict[str, Any]]) -> Any:
    text = json.dumps({"images": entries, "best_index": None})
    if provider == "claude":
        return {"type": "message", "model": model, "stop_reason": "end_turn", "usage": None,
                "content": [{"type": "text", "text": text}]}
    payload = {"candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": "STOP"}]}
    return Replayed(200, {"Content-Type": "application/json"}, json.dumps(payload).encode("utf-8"))


# ---------------------------------------------------------------------------
# Storage: a directory (read / write) or a zip of one (read only)
# ---------------------------------------------------------------------------

class _Store:
    def __init__(self, path: str, writable: bool) -> None:
        self.path = str(path)
        self.writable = writable
        self._zip: Optional[zipfile.ZipFile] = None
        self._prefix = ""
        self._lock = threading.Lock()
        self._handles: Dict[str, Any] = {}
        self._names: Dict[str, str] = {}
        self.closed = False
        if os.path.isfile(self.path) and zipfile.is_zipfile(self.path):
            if writable:
                raise ValueError(f"{self.path}: a zipped cassette can only be replayed; unzip it to record into it")
            self._zip = zipfile.ZipFile(self.path)
            # Windows PowerShell 5.1's Compress-Archive stores 'folder\\file': names are matched with '/'
            self._names = {n.replace("\\", "/"): n for n in self._zip.namelist()}
            metas = sorted((n for n in self._names if n.endswith(META_FILE)), key=len)
            self._prefix = metas[0][:-len(META_FILE)] if metas else ""
        elif writable:
            os.makedirs(os.path.join(self.path, BLOB_DIR), exist_ok=True)
        elif not os.path.isdir(self.path):
            raise FileNotFoundError(f"no cassette at {self.path}")

    def read_bytes(self, name: str) -> Optional[bytes]:
        if self._zip is not None:
            member = self._names.get(self._prefix + name)
            if member is None:
                return None
            with self._lock:
                return self._zip.read(member)
        try:
            with open(os.path.join(self.path, name), "rb") as fh:
                return fh.read()
        except OSError:
            return None

    def read_text(self, name: str) -> Optional[str]:
        data = self.read_bytes(name)
        return None if data is None else data.decode("utf-8", errors="replace")

    def write_text(self, name: str, text: str) -> None:
        target = os.path.join(self.path, name)
        tmp = f"{target}.{uuid.uuid4().hex}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, target)

    def append_line(self, name: str, line: str) -> None:
        with self._lock:
            if self.closed:
                # a page read the last row left running finished after the run: kept, without a lingering handle
                with open(os.path.join(self.path, name), "a", encoding="utf-8") as late:
                    late.write(line + "\n")
                return
            fh = self._handles.get(name)
            if fh is None:
                fh = self._handles[name] = open(os.path.join(self.path, name), "a", encoding="utf-8")
            fh.write(line + "\n")
            fh.flush()

    def blob_name(self, sha: str, gz: bool) -> str:
        return f"{BLOB_DIR}/{sha}{'.gz' if gz else ''}"

    def write_blob(self, data: bytes, gz: bool) -> Tuple[str, int]:
        sha = hashlib.sha256(data).hexdigest()
        name = self.blob_name(sha, gz)
        target = os.path.join(self.path, name)
        if not os.path.exists(target):
            payload = gzip.compress(data, mtime=0) if gz else data
            tmp = f"{target}.{uuid.uuid4().hex}.tmp"
            with open(tmp, "wb") as fh:
                fh.write(payload)
            try:
                os.replace(tmp, target)
            except OSError:
                if os.path.exists(tmp):
                    os.remove(tmp)
        return sha, len(data)

    def read_blob(self, sha: str, gz: bool) -> Optional[bytes]:
        data = self.read_bytes(self.blob_name(sha, gz))
        if data is not None and gz:
            data = gzip.decompress(data)
        return data

    def close(self) -> None:
        with self._lock:
            self.closed = True
            for fh in self._handles.values():
                try:
                    fh.close()
                except Exception:  # pragma: no cover - best effort
                    pass
            self._handles.clear()
            if self._zip is not None:
                self._zip.close()
                self._zip = None


def _textish(ctype: str, body: bytes) -> bool:
    ctype = (ctype or "").lower()
    if any(t in ctype for t in ("html", "xml", "json", "text", "javascript")):
        return True
    return body[:256].lstrip(b"\xef\xbb\xbf \t\r\n").startswith((b"<", b"{", b"["))


# ---------------------------------------------------------------------------
# The cassette
# ---------------------------------------------------------------------------

class Cassette:
    """One installed cassette (see the module docstring). Thread-safe."""

    def __init__(self, mode: str, directory: str, redact: Optional[Callable[[str], str]] = None) -> None:
        self.mode = mode
        self.directory = str(directory)
        self.store = _Store(directory, writable=mode in ("record", "fill"))
        self.redact = redact or (lambda text: text)
        self._lock = threading.RLock()
        self._local = threading.local()
        self._row = 0
        self._seq = 0
        self._attempts: Dict[Tuple[int, str], int] = {}
        self._entries: Dict[str, List[Dict[str, Any]]] = {}
        self._aliases: Dict[str, List[Dict[str, Any]]] = {}
        self._by_row_kind: Dict[Tuple[int, str], List[Dict[str, Any]]] = {}
        self._events: Dict[Tuple[str, int], List[Any]] = {}
        self._event_pos: Dict[Tuple[str, int], int] = {}
        self.reports: Dict[int, Dict[str, Any]] = {}
        self.counts = {"recorded": 0, "served": 0, "live": 0, "missed": 0}
        self._shadow = False
        self._undo: List[Callable[[], None]] = []
        if mode in ("replay", "fill"):
            self._load()
        self._patch()

    # -- lifecycle ---------------------------------------------------------------

    def close(self) -> None:
        for undo in reversed(self._undo):
            try:
                undo()
            except Exception:  # pragma: no cover - best effort
                logger.debug("cassette: undo failed", exc_info=True)
        self._undo.clear()
        self.store.close()

    def begin_row(self, number: int) -> None:
        with self._lock:
            self._row = int(number)
            self._report(self._row)
        if self.mode != "record":
            # A replay asks every page again (the live run's 30-minute page cache is a wall-clock effect);
            # an answer the row never asked comes from the row that did.
            try:
                from . import pages
                pages.clear_cache()
            except Exception:  # pragma: no cover - defensive
                pass

    @contextlib.contextmanager
    def row_context(self, number: int) -> Iterator[None]:
        """Every answer inside belongs to this sheet row (until the next row begins: a page read the row left
        running in the background is still filed under it)."""
        self.begin_row(number)
        yield

    @property
    def current_row(self) -> int:
        return self._row

    def read_meta(self) -> Dict[str, Any]:
        text = self.store.read_text(META_FILE)
        return json.loads(text) if text else {}

    def write_meta(self, meta: Mapping[str, Any]) -> None:
        line = self.redact(json.dumps(meta, ensure_ascii=False, indent=1, sort_keys=True, default=str))
        self.store.write_text(META_FILE, line)

    # -- reports -------------------------------------------------------------------

    def _report(self, number: int) -> Dict[str, Any]:
        rep = self.reports.get(number)
        if rep is None:
            rep = self.reports[number] = {"misses": [], "approximations": [], "served": 0, "live": 0, "_seen": set()}
        return rep

    def _miss(self, kind: str, what: Mapping[str, Any], key: str) -> None:
        with self._lock:
            rep = self._report(self._row)
            self.counts["missed"] += 1
            if ("miss", kind, key) in rep["_seen"]:
                return
            rep["_seen"].add(("miss", kind, key))
            rep["misses"].append({"kind": kind, "key": key, **dict(what)})
        logger.warning("cassette: row %s: no recorded answer for %s %s", self._row, kind,
                       what.get("url") or what.get("model") or "")

    def _forget_misses(self, row: int, kind: str, urls: Iterable[str]) -> None:
        urls = set(urls)
        with self._lock:
            rep = self._report(row)
            rep["misses"] = [m for m in rep["misses"]
                             if not (m.get("kind") == kind and (m.get("request") or {}).get("url") in urls)]

    def _approx(self, text: str) -> None:
        with self._lock:
            rep = self._report(self._row)
            if text not in rep["approximations"]:
                rep["approximations"].append(text)

    def row_report(self, number: int) -> Dict[str, Any]:
        """{complete, misses, approximate, approximations, answers}: what the replay of one row could use."""
        rep = self._report(int(number))
        return {"complete": not rep["misses"], "misses": [dict(m) for m in rep["misses"]],
                "approximate": bool(rep["approximations"]), "approximations": list(rep["approximations"]),
                "answers": rep["served"], "live_calls": rep["live"]}

    def misses_manifest(self, rows: Optional[Iterable[int]] = None) -> Dict[str, Any]:
        """The misses of the replayed rows, for smoke_live.py --record <cassette> --fill-misses <manifest>."""
        wanted = sorted(set(rows)) if rows is not None else sorted(self.reports)
        misses = [dict(m, row=n) for n in wanted for m in self.reports.get(n, {}).get("misses", [])]
        return {"format": MISSES_FORMAT, "cassette": os.path.abspath(self.directory),
                "rows": sorted({m["row"] for m in misses}), "misses": misses}

    # -- index of recorded entries ----------------------------------------------------

    def _load(self) -> None:
        for name in (HTTP_FILE, VERIFIER_FILE, INDEX_FILE):
            text = self.store.read_text(name) or ""
            for number, line in enumerate(text.splitlines(), start=1):
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                except ValueError:
                    logger.warning("cassette: %s line %d is not JSON (an interrupted run?); skipped", name, number)
                    continue
                self._index(entry)
                self._seq = max(self._seq, int(entry.get("seq") or 0))

    def _index(self, entry: Dict[str, Any]) -> None:
        if entry.get("kind") == "event":
            self._events.setdefault((entry["name"], int(entry.get("row") or 0)), []).append(entry.get("value"))
            return
        key = entry.get("key")
        if not key:
            return
        self._entries.setdefault(key, []).append(entry)
        self._by_row_kind.setdefault((int(entry.get("row") or 0), str(entry.get("kind"))), []).append(entry)
        request = entry.get("request") or {}
        if request.get("kind") == "fetch":
            alias = request_key(canonical_request("fetch", request.get("method", "GET"),
                                                  _alias_url(request.get("url", "")), proxy=request.get("proxy", False)))
            self._aliases.setdefault(alias, []).append(entry)

    def _write(self, name: str, entry: Dict[str, Any]) -> None:
        with self._lock:
            self._seq += 1
            entry["seq"] = self._seq
            if self._shadow:
                entry["shadow"] = True
            line = self.redact(json.dumps(entry, ensure_ascii=False, sort_keys=True, default=str))
            self.store.append_line(name, line)
            self._index(json.loads(line))
            if entry.get("kind") != "event":
                self.counts["recorded"] += 1
                self._report(int(entry.get("row") or 0))["live"] += 1

    def _next_attempt(self, row: int, key: str) -> int:
        with self._lock:
            n = self._attempts.get((row, key), 0) + 1
            self._attempts[(row, key)] = n
            return n

    def asked(self, row: int, key: str) -> bool:
        """True when this row already holds an answer for the key."""
        return any(int(e.get("row") or 0) == row for e in self._entries.get(key, ()))

    @staticmethod
    def _succeeded(entry: Mapping[str, Any]) -> bool:
        response = entry.get("response") or {}
        if "exception" in response:
            return False
        if "message" in response or "rows" in entry or "done" in entry:
            return True
        return int(response.get("status") or 0) == 200

    def _pick(self, entries: List[Dict[str, Any]], attempt: int) -> Tuple[Optional[Dict[str, Any]], str]:
        """The answer to the n-th ask among one row's entries for a key: '' exact, 'reused' or None."""
        exact = next((e for e in entries if int(e.get("attempt") or 0) == attempt), None)
        if exact is not None:
            return exact, ""
        last = entries[-1]
        return (last, "reused") if self._succeeded(last) else (None, "")

    def _answer(self, key: str, row: int, entries: Optional[List[Dict[str, Any]]] = None
                ) -> Tuple[Optional[Dict[str, Any]], str]:
        """(entry, how) for this ask: how is '' (this row), 'reused', 'row <n>' (another row) or '' with None."""
        attempt = self._next_attempt(row, key)
        entries = self._entries.get(key, []) if entries is None else entries
        if not entries:
            return None, ""
        same = [e for e in entries if int(e.get("row") or 0) == row]
        if same:
            return self._pick(same, attempt)
        rows = sorted({int(e.get("row") or 0) for e in entries})
        earlier = [r for r in rows if r < row]
        other = earlier[-1] if earlier else rows[0]
        entry, _how = self._pick([e for e in entries if int(e.get("row") or 0) == other], attempt)
        return entry, f"row {other}" if entry is not None else ""

    def _served(self) -> None:
        with self._lock:
            self.counts["served"] += 1
            self._report(self._row)["served"] += 1

    # -- events: wall-clock and database decisions ------------------------------------

    def _event(self, name: str, live: Callable[[], Any], default: Callable[[], Any], file: str) -> Any:
        row = self._row
        with self._lock:
            n = self._event_pos[(name, row)] = self._event_pos.get((name, row), 0) + 1
        values = self._events.get((name, row)) if self.mode != "record" else None
        if values and n <= len(values):
            return self._event_value(values[n - 1])
        if self.mode == "replay":
            return self._event_value(values[-1]) if values else default()
        try:
            value = {"value": live()}
        except Exception as exc:
            text = _KEY_IN_TEXT_RE.sub(r"\1[hidden]", self.redact(str(exc)))[:200]
            value = {"raise": f"{type(exc).__name__}: {text}"}
        self._write(file, {"kind": "event", "name": name, "row": row, "n": n, "value": value})
        return self._event_value(value)

    @staticmethod
    def _event_value(value: Any) -> Any:
        if isinstance(value, dict) and "raise" in value:
            raise RuntimeError(value["raise"])
        return value.get("value") if isinstance(value, dict) else value

    def _last_event(self, name: str, fallback: Any) -> Any:
        """The value of the latest row before the current one that recorded this event."""
        rows = sorted(r for (n, r) in self._events if n == name and r < self._row)
        for r in reversed(rows):
            for value in reversed(self._events[(name, r)]):
                if isinstance(value, dict) and "value" in value:
                    return value["value"]
        return fallback

    def _patch(self) -> None:
        """Route the wall-clock and database decisions through events (and keep a replay off the database)."""
        from . import local_index, verify
        from .verifiers import claude as claude_mod, spend as spend_mod

        cas = self
        for name, breaker in (("gemini", verify.BREAKER), ("claude", claude_mod.CLAUDE_BREAKER)):
            real_open = breaker.is_open

            def is_open(_real=real_open, _name=name):
                return bool(cas._event(f"breaker:{_name}", _real, lambda: False, VERIFIER_FILE))

            breaker.is_open = is_open
            self._undo.append(lambda b=breaker: b.__dict__.pop("is_open", None))

        real_spend = spend_mod.MariaDbSpendStore.role_spend
        real_add = spend_mod.MariaDbSpendStore.add
        real_count = local_index.DbCatalogStore.count

        def role_spend(store, role="strong", month=None):
            return float(cas._event(f"spend:{role}", lambda: real_spend(store, role, month),
                                    lambda: cas._last_event(f"spend:{role}", 0.0), VERIFIER_FILE))

        def add(store, entry, month=None):
            return True if cas.mode == "replay" else real_add(store, entry, month)

        def count(store, fresh=False):
            return int(cas._event("index_count", lambda: real_count(store, fresh),
                                  lambda: cas._last_event("index_count", 0), INDEX_FILE))

        for owner, attr, value in ((spend_mod.MariaDbSpendStore, "role_spend", role_spend),
                                   (spend_mod.MariaDbSpendStore, "add", add),
                                   (local_index.DbCatalogStore, "count", count)):
            patcher = mock.patch.object(owner, attr, value)
            patcher.start()
            self._undo.append(patcher.stop)

    # -- HTTP -------------------------------------------------------------------------

    def http(self, kind: str, method: str, url: str, send: Callable[[], Any], *, params: Any = None,
             body: Any = None, headers: Optional[Mapping[str, str]] = None, proxy: bool = False, stream: bool = False,
             max_bytes: Optional[int] = None) -> Any:
        request = canonical_request(kind, method, url, params, body, proxy)
        key = request_key(request)
        row = self._row
        label = kind
        if kind == "fetch":
            accept = str((headers or {}).get("Accept", "") or "")
            label = "page" if accept.startswith("text/html") else "image"
        if self.mode == "record":
            return self._record_http(label, request, key, row, self._next_attempt(row, key), send, stream, max_bytes)
        entry, how = self._answer(key, row)
        if entry is None and kind == "fetch":
            alias = request_key(canonical_request("fetch", method, _alias_url(url), proxy=proxy))
            entry, how = self._answer(alias, row, self._aliases.get(alias, []))
            if entry is not None:
                self._approx(f"{label} answered under another rendition of its URL: {url[:120]}")
        if entry is not None:
            self._served()
            return self._replay_http(entry, stream)
        if self.mode == "fill":
            return self._record_http(label, request, key, row, self._attempts.get((row, key), 1), send, stream,
                                     max_bytes)
        self._miss(label, {"request": request}, key)
        raise NotRecorded(f"{label} {request.get('url', '')}")

    def _body(self, data: bytes, ctype: str, fetch: bool) -> Optional[Dict[str, Any]]:
        if not data:
            return None
        text = _textish(ctype, data)
        if not fetch and text and len(data) <= INLINE_MAX:
            try:
                return {"text": data.decode("utf-8"), "size": len(data)}
            except UnicodeDecodeError:
                pass
        sha, size = self.store.write_blob(data, gz=text)
        return {"blob": sha, "gz": text, "size": size}

    def _content(self, body: Optional[Mapping[str, Any]]) -> bytes:
        if not body:
            return b""
        if "text" in body:
            return str(body["text"]).encode("utf-8")
        data = self.store.read_blob(str(body["blob"]), bool(body.get("gz")))
        if data is None:
            raise NotRecorded(f"blob {body.get('blob')}")
        return data

    def _record_http(self, label: str, request: Dict[str, Any], key: str, row: int, attempt: int,
                     send: Callable[[], Any], stream: bool, max_bytes: Optional[int]) -> Any:
        entry: Dict[str, Any] = {"kind": label, "key": key, "request": request, "row": row, "attempt": attempt}
        try:
            resp = send()
        except Exception as exc:
            entry["response"] = {"exception": _exc_info(exc, self.redact)}
            self._write(HTTP_FILE, entry)
            raise
        status = int(getattr(resp, "status_code", 0) or 0)
        headers = _headers_of(resp)
        final = getattr(resp, "url", None)
        final = final.strip() if isinstance(final, str) and final.strip() else ""
        response: Dict[str, Any] = {"status": status, "headers": headers}
        if final and final != request.get("url"):
            response["url"] = final
        if not stream:
            data = self._plain_body(resp)
            response["body"] = self._body(data, headers.get("Content-Type", ""), fetch=False)
            entry["response"] = response
            self._write(HTTP_FILE, entry)
            return resp
        data, error, truncated = self._read_stream(resp, status, headers, max_bytes)
        response["body"] = self._body(data, headers.get("Content-Type", ""), fetch=True)
        if error is not None:
            response["stream_error"] = _exc_info(error, self.redact)
        if truncated:
            response["truncated"] = True
        entry["response"] = response
        self._write(HTTP_FILE, entry)
        return Replayed(status, headers, data, final or request.get("url", ""), error)

    @staticmethod
    def _plain_body(resp: Any) -> bytes:
        try:
            content = resp.content
            if isinstance(content, (bytes, bytearray)):
                return bytes(content)
        except Exception:
            pass
        try:
            text = resp.text
            if isinstance(text, str):
                return text.encode("utf-8")
        except Exception:
            pass
        try:
            return json.dumps(resp.json()).encode("utf-8")
        except Exception:
            return b""

    @staticmethod
    def _read_stream(resp: Any, status: int, headers: Mapping[str, str], max_bytes: Optional[int]
                     ) -> Tuple[bytes, Optional[BaseException], bool]:
        """The streamed body as the caller would read it: nothing for a refusal or a too-large Content-Length,
        at most max_bytes + 1 bytes, and the exception that cut the stream."""
        buf = bytearray()
        error: Optional[BaseException] = None
        truncated = False
        try:
            if status != 200:
                return b"", None, False
            length = headers.get("Content-Length")
            if length is not None and max_bytes is not None:
                try:
                    if int(length) > max_bytes:
                        return b"", None, False
                except (TypeError, ValueError):
                    pass
            try:
                for chunk in resp.iter_content(chunk_size=STREAM_CHUNK):
                    if not chunk:
                        continue
                    buf.extend(chunk)
                    if max_bytes is not None and len(buf) > max_bytes:
                        truncated = True
                        break
            except Exception as exc:
                error = exc
            return bytes(buf), error, truncated
        finally:
            close = getattr(resp, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:  # pragma: no cover - best effort
                    pass

    def _replay_http(self, entry: Mapping[str, Any], stream: bool) -> Any:
        response = entry.get("response") or {}
        if "exception" in response:
            raise rebuild_exception(response["exception"])
        request = entry.get("request") or {}
        error = rebuild_exception(response["stream_error"]) if response.get("stream_error") else None
        return Replayed(int(response.get("status") or 0), response.get("headers") or {},
                        self._content(response.get("body")), response.get("url") or request.get("url", ""), error)

    # -- label readers -------------------------------------------------------------------

    def set_scope(self, provider: str, model: str, focus: bool, long_side: int, prompt: str,
                  images: Sequence[Any], slots: Sequence[int]) -> None:
        shas = [image_sha(images[p]) for p in slots]
        self._local.scope = {
            "request": {"kind": "verifier", "provider": str(provider), "model": str(model), "focus": bool(focus),
                        "long_side": int(long_side or 0),
                        "prompt_sha256": hashlib.sha256((prompt or "").encode("utf-8")).hexdigest(),
                        "images": shas},
            "prompt": prompt or "",
        }

    def verifier(self, send: Callable[[], Any]) -> Any:
        scope = getattr(self._local, "scope", None)
        if scope is None:
            if self.mode == "record":
                logger.warning("cassette: a label-reader call without a key was not recorded")
                return send()
            self._miss("verifier", {"request": {"error": "no key"}}, "no-scope")
            raise NotRecorded("verifier")
        request = scope["request"]
        key = request_key(request)
        row = self._row
        if self.mode == "record":
            return self._record_verifier(scope, key, row, self._next_attempt(row, key), send)
        entry, how = self._answer(key, row)
        if entry is not None:
            self._served()
            return self._replay_verifier(entry, request["provider"])
        if self.mode == "fill":
            return self._record_verifier(scope, key, row, self._attempts.get((row, key), 1), send)
        reply = self._reading_fallback(request, key)
        if reply is not None:
            self._served()
            return reply
        self._miss("verifier", {"request": dict(request, prompt=scope["prompt"])}, key)
        raise NotRecorded("verifier")

    def _record_verifier(self, scope: Mapping[str, Any], key: str, row: int, attempt: int,
                         send: Callable[[], Any]) -> Any:
        request = dict(scope["request"], prompt=scope["prompt"])
        entry: Dict[str, Any] = {"kind": "verifier", "key": key, "request": request, "row": row, "attempt": attempt}
        try:
            resp = send()
        except Exception as exc:
            entry["response"] = {"exception": _exc_info(exc, self.redact)}
            self._write(VERIFIER_FILE, entry)
            raise
        if request["provider"] == "claude":
            response: Dict[str, Any] = {"message": _dump_message(resp)}
        else:
            response = {"status": int(getattr(resp, "status_code", 0) or 0), "headers": _headers_of(resp)}
            try:
                response["json"] = resp.json()
            except Exception:
                response["text"] = self._plain_body(resp).decode("utf-8", errors="replace")[:INLINE_MAX]
        entry["response"] = response
        entry["readings"] = readings_of(request["provider"], response, request["images"])
        self._write(VERIFIER_FILE, entry)
        return resp

    def _replay_verifier(self, entry: Mapping[str, Any], provider: str) -> Any:
        response = entry.get("response") or {}
        if "exception" in response:
            raise rebuild_exception(response["exception"])
        if provider == "claude":
            return json.loads(json.dumps(response.get("message")))
        if "json" in response:
            content = json.dumps(response["json"]).encode("utf-8")
        else:
            content = str(response.get("text") or "").encode("utf-8")
        return Replayed(int(response.get("status") or 0), response.get("headers") or {}, content)

    def _reading_fallback(self, request: Mapping[str, Any], key: str) -> Any:
        """A reply built from this row's other recorded readings of the same images by the same model."""
        row = self._row
        same_model = [e for e in self._by_row_kind.get((row, "verifier"), [])
                      if (e.get("request") or {}).get("provider") == request["provider"]
                      and (e.get("request") or {}).get("model") == request["model"]]
        same_model.sort(key=lambda e: (bool((e.get("request") or {}).get("focus")) != request["focus"],
                                       int(e.get("seq") or 0)))
        entries, missing = [], []
        for label, sha in enumerate(request["images"], start=1):
            fields = next((r["fields"] for e in same_model for r in e.get("readings") or [] if r.get("sha") == sha),
                          None)
            if fields is None:
                missing.append(sha)
                continue
            entries.append(dict(fields, image_index=label))
        if not entries:
            return None
        self._approx(f"label reading of {len(entries)} image(s) taken from another recorded call "
                     f"({request['provider']}:{request['model']})")
        for sha in missing:
            self._miss("verifier_image", {"request": {"provider": request["provider"], "model": request["model"],
                                                      "image": sha}}, f"{key}:{sha}")
        return _synthetic_reply(request["provider"], request["model"], entries)

    # -- local index ---------------------------------------------------------------------

    def local_index_rows(self, spec: Any, read: Callable[[], List[Any]]) -> List[Any]:
        from dataclasses import asdict

        from . import local_index
        from .gtin import is_global_gtin

        groups, extra = local_index.search_keys(spec)
        gtin = spec.gtin if spec.gtin and is_global_gtin(spec.gtin) else ""
        request = {"kind": "index_rows", "gtin": gtin, "groups": groups, "extra": extra}
        key = request_key(request)
        row = self._row
        if self.mode == "record":
            attempt = self._next_attempt(row, key)
        else:
            entry, _how = self._answer(key, row)
            attempt = self._attempts.get((row, key), 1)
            if entry is None and self.mode == "replay":
                recorded = self._by_row_kind.get((row, "rows"), [])
                if recorded:
                    entry = {"rows": [r for e in recorded for r in e.get("rows") or []]}
                    self._approx("local index rows of another lookup of this row (the brand or product words changed)")
            if entry is not None:
                self._served()
                if "exception" in (entry.get("response") or {}):
                    raise rebuild_exception(entry["response"]["exception"])
                seen, out = set(), []
                for data in entry.get("rows") or []:
                    if data.get("id") not in seen:
                        seen.add(data.get("id"))
                        out.append(local_index.CatalogRow(**data))
                return out
            if self.mode == "replay":
                self._miss("local_index", {"request": request}, key)
                raise NotRecorded("local_index")
        entry = {"kind": "rows", "key": key, "request": request, "row": row, "attempt": attempt}
        try:
            rows = read()
        except Exception as exc:
            entry["response"] = {"exception": _exc_info(exc, self.redact)}
            self._write(INDEX_FILE, entry)
            raise
        entry["rows"] = [asdict(r) for r in rows]
        self._write(INDEX_FILE, entry)
        return rows

    def local_index_deadline(self, futures: Mapping[Any, int], rows: Mapping[int, Any], done: set, pending: set
                             ) -> Tuple[set, set]:
        urls = {fut: getattr(rows.get(i), "url", "") for fut, i in futures.items()}
        request = {"kind": "deadline", "urls": sorted(urls.values())}
        key = request_key(request)
        row = self._row
        if self.mode == "record":
            attempt = self._next_attempt(row, key)
        else:
            entry, _how = self._answer(key, row)
            attempt = self._attempts.get((row, key), 1)
            if entry is not None or self.mode == "replay":
                from concurrent.futures import wait
                wait(list(futures), timeout=REPLAY_WAIT_S)
                if entry is None:
                    self._approx("local index page reads: no recorded deadline for this set of pages; all counted "
                                 "as finished in time")
                    return set(futures), set()
                self._served()
                in_time = set(entry.get("done") or [])
                ready = {f for f in futures if urls[f] in in_time and f.done()}
                # a read the live run gave up on is discarded here too: its answer (maybe never recorded,
                # the run may have ended first) is not a miss
                self._forget_misses(row, "page", {urls[f] for f in futures if f not in ready})
                return ready, set(futures) - ready
        self._write(INDEX_FILE, {"kind": "deadline", "key": key, "request": request, "row": row, "attempt": attempt,
                                 "done": sorted(urls[f] for f in done)})
        return done, pending

    # -- shadow recording (record mode, after a row's live decision) ------------------------

    def shadow(self, spec: Any, outcome: Any, max_images: int = SHADOW_MAX_IMAGES,
               max_pages: int = SHADOW_MAX_PAGES) -> Dict[str, Any]:
        """Extra answers for later code: see shadow_record()."""
        from . import expand, pages, ratelimit, settings
        from .fetch import HttpFetcher
        from .providers.cse_legacy import CseLegacyProvider
        from .providers.lens import SerperLensProvider
        from .providers.serper import SerperImagesProvider, has_site_operators, without_site_operators
        from .providers.serper_shopping import SerperShoppingProvider
        from .providers.serper_web import SerperWebProvider, site_query
        from .verifiers import cascade, pricing
        from .verify import CircuitBreaker

        report = {"downloads": 0, "pages": 0, "search_calls": 0, "verifier_calls": 0, "cost_usd": 0.0}
        if self.mode != "record":
            return report
        row = self._row
        flags = (SerperImagesProvider.operators_blocked, SerperLensProvider.unsupported,
                 CseLegacyProvider.disabled_reason)
        buckets: Dict[str, Any] = {}

        def bucket(name: str, rate: float, burst: int):
            # own buckets: the shadow work never delays the live run's next row
            return buckets.setdefault(name, ratelimit.TokenBucket(rate, burst, name=f"shadow:{name}"))

        def fetched_key(url: str) -> str:
            return request_key(canonical_request("fetch", "GET", url))

        self._shadow = True
        try:
            ranked = list(getattr(outcome, "ranked", None) or [])
            images: List[Any] = [rc.fetched for rc in ranked if rc.fetched is not None and rc.fetched.ok]
            todo = []
            for rc in ranked[:max_images]:
                url = rc.candidate.image_url
                if url and url.lower().startswith(("http://", "https://")) and not self.asked(row, fetched_key(url)):
                    todo.append(rc.candidate)
            page_reader = pages.PageFetcher(bucket_factory=lambda host: bucket(f"page:{host}", pages.PAGES_PER_MIN,
                                                                               pages.PAGE_BURST))
            page_urls: List[Tuple[str, str]] = []
            for rc in ranked:
                score = rc.score
                url = rc.candidate.page_url
                if (score is not None and score.tier in (1, 2) and url.lower().startswith(("http://", "https://"))
                        and not self.asked(row, fetched_key(url)) and (url, rc.candidate.title) not in page_urls):
                    page_urls.append((url, rc.candidate.title))
            # X1 / X2 of the expansion round for a row without a pick, when the live round did not send them
            if getattr(outcome, "decision", "") not in expand.PICK_DECISIONS and settings.serper_api_key():
                text, hl = expand.text_query(spec)
                groups = expand.site_groups(spec)
                calls = ((SerperWebProvider(bucket=bucket("serper_web", 120.0, 5)), site_query(text, groups[0]), "web"),
                         (SerperShoppingProvider(bucket=bucket("serper_shopping", 120.0, 5)), text, "shopping"))
                for provider, query, kind in calls:
                    sent = without_site_operators(query) if (SerperImagesProvider.operators_blocked
                                                             and has_site_operators(query)) else query
                    req = canonical_request(provider.name, "POST", provider.endpoint, body=provider._payload(sent, hl))
                    if not text or (kind == "web" and not groups[0]) or self.asked(row, request_key(req)):
                        continue
                    res = provider.search(query, hl, spec)
                    report["search_calls"] += 1
                    for i, hit in enumerate(res.candidates):
                        image_ok = kind == "shopping" and hit.image_url and not pages.is_thumbnail(
                            hit.image_url, hit.width, hit.height)
                        if image_ok and len(todo) < 2 * max_images:
                            todo.append(hit)
                        elif hit.page_url and expand._fetchable(spec, hit.page_url) \
                                and i < expand.MAX_PAGES[kind] and (hit.page_url, hit.title) not in page_urls:
                            page_urls.append((hit.page_url, hit.title))
            for url, title in page_urls[:max_pages]:
                info = page_reader._fetch_uncached(url, "")
                report["pages"] += 1
                for cand in pages.page_candidates(info, title=title):
                    if not self.asked(row, fetched_key(cand.image_url)):
                        todo.append(cand)
            seen_urls, unique = set(), []
            for cand in todo:
                if cand.image_url not in seen_urls:
                    seen_urls.add(cand.image_url)
                    unique.append(cand)
            if unique:
                got = HttpFetcher(max_items=len(unique)).fetch(unique, spec)
                report["downloads"] = len(got)
                images += [fi for fi in got if fi.ok]
            # one primary reading of every downloaded image the row has no reading of yet
            ref = cascade.primary_ref()
            reader = cascade.build_reader(ref, "primary")
            reader.breaker = CircuitBreaker()
            read = {r.get("sha") for e in self._by_row_kind.get((row, "verifier"), [])
                    if (e.get("request") or {}).get("model") == ref.model for r in e.get("readings") or []}
            unread, shas = [], set()
            for fi in images:
                sha = image_sha(fi)
                if sha and sha not in read and sha not in shas:
                    shas.add(sha)
                    unread.append(fi)
            prices = pricing.load_prices()
            for i in range(0, len(unread), SHADOW_VERIFY_BATCH):
                result = reader.verify(spec, unread[i:i + SHADOW_VERIFY_BATCH])
                report["verifier_calls"] += int(result.calls or 0)
                for usage in result.usage or []:
                    report["cost_usd"] += float(pricing.usage_usd(usage, prices) or 0.0)
            report["cost_usd"] = round(report["cost_usd"] + 0.001 * report["search_calls"], 6)
        except Exception as exc:  # the shadow work never breaks the run
            logger.warning("cassette: shadow recording of row %s stopped (%s)", row, type(exc).__name__)
            report["error"] = f"{type(exc).__name__}: {self.redact(str(exc))[:200]}"
        finally:
            self._shadow = False
            SerperImagesProvider.operators_blocked, SerperLensProvider.unsupported, \
                CseLegacyProvider.disabled_reason = flags
        return report


def shadow_record(spec: Any, outcome: Any) -> Optional[Dict[str, Any]]:
    """Record extra answers for a finished row (record mode only; the live decision is already made):
    every pooled image up to SHADOW_MAX_IMAGES, the pages of its tier-1/2 listings (up to SHADOW_MAX_PAGES),
    X1 (retailer web search) and X2 (shopping) for a row without a pick, and one primary reading of every
    downloaded image in batches of SHADOW_VERIFY_BATCH. Returns what it did and its estimated cost."""
    cas = _STATE
    if cas is None or cas.mode != "record":
        return None
    return cas.shadow(spec, outcome)


# ---------------------------------------------------------------------------
# Snapshots for meta.json and an offline guard for the replay
# ---------------------------------------------------------------------------

def is_secret_setting(name: str) -> bool:
    return bool(_SECRET_SETTING_RE.search(name or ""))


def settings_snapshot() -> Dict[str, Any]:
    """Every catalog_match setting: values for the plain ones, only 'set' (or how many) for the secret ones."""
    from . import settings

    values: Dict[str, Any] = {}
    configured: Dict[str, Any] = {}
    for name in sorted(settings.DEFAULTS):
        if name == "GOOGLE_SEARCH_API_KEYS":
            configured[name] = len(settings.google_search_api_keys())
        elif name == "GOOGLE_SEARCH_CX_LIST":
            configured[name] = len(settings.google_search_cx_list())
        elif is_secret_setting(name):
            configured[name] = bool(str(settings.get(name) or "").strip())
        else:
            value = settings.get(name)
            values[name] = value if isinstance(value, (bool, int, float, str, list)) or value is None else str(value)
    return {"values": values, "configured": configured}


def library_versions() -> Dict[str, Any]:
    """What decodes and measures the images (a different version can move a quality score or a pHash)."""
    import platform

    out: Dict[str, Any] = {"python": platform.python_version()}
    for name, module in (("pillow", "PIL"), ("numpy", "numpy"), ("scipy", "scipy")):
        try:
            out[name] = getattr(importlib.import_module(module), "__version__", "")
        except Exception:
            out[name] = ""
    try:
        importlib.import_module("curl_cffi")
        out["curl_cffi"] = True
    except Exception:
        out["curl_cffi"] = False
    return out


class NetworkBlocked(OSError):
    """A replay tried to open a connection."""


_LOOPBACK = ("127.", "::1", "localhost", "0:0:0:0:0:0:0:1")


@contextlib.contextmanager
def offline(attempts: Optional[List[str]] = None) -> Iterator[List[str]]:
    """Refuse every outbound connection and every database connect for the duration; record the attempts."""
    attempts = attempts if attempts is not None else []
    real_connect, real_connect_ex = socket.socket.connect, socket.socket.connect_ex
    real_getaddrinfo, real_create = socket.getaddrinfo, socket.create_connection

    def loopback(host: Any) -> bool:
        return isinstance(host, str) and (host.startswith(_LOOPBACK) or host == "")

    def guard(address: Any) -> None:
        host = address[0] if isinstance(address, tuple) and address else address
        if not loopback(host):
            attempts.append(f"connect {address!r}")
            raise NetworkBlocked(f"replay: connection to {address!r} refused")

    def connect(sock, address):  # type: ignore[no-untyped-def]
        guard(address)
        return real_connect(sock, address)

    def connect_ex(sock, address):  # type: ignore[no-untyped-def]
        guard(address)
        return real_connect_ex(sock, address)

    def getaddrinfo(host, *args, **kwargs):  # type: ignore[no-untyped-def]
        if not loopback(host):
            attempts.append(f"getaddrinfo {host!r}")
            raise NetworkBlocked(f"replay: DNS lookup of {host!r} refused")
        return real_getaddrinfo(host, *args, **kwargs)

    def create_connection(address, *args, **kwargs):  # type: ignore[no-untyped-def]
        guard(address)
        return real_create(address, *args, **kwargs)

    patches = [mock.patch.object(socket.socket, "connect", connect),
               mock.patch.object(socket.socket, "connect_ex", connect_ex),
               mock.patch.object(socket, "getaddrinfo", getaddrinfo),
               mock.patch.object(socket, "create_connection", create_connection)]
    try:
        import pymysql

        def refuse_db(*args, **kwargs):  # type: ignore[no-untyped-def]
            attempts.append("pymysql.connect")
            raise pymysql.err.OperationalError(2003, "replay: database disabled")

        patches.append(mock.patch.object(pymysql, "connect", refuse_db))
    except ImportError:  # pragma: no cover - pymysql is a runtime dependency
        pass
    with contextlib.ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        yield attempts

