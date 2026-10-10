"""Sitemap harvester for the local catalog index (catalog_match.local_index).

load_stores(path=None) -> [StoreConfig]
    The stores in catalog_match/data/catalog_stores.json: hosts, the path pattern of a product page,
    and optionally the sitemap URLs to start from and include / exclude patterns for child sitemaps.

SitemapHarvester().harvest(store, on_urls=None, max_urls=None, max_sitemaps=None, discover=False)
    -> HarvestReport
    Reads the store's published sitemaps and hands every product URL to on_urls([(url, lastmod)])
    in batches (on_urls returns how many were new). With discover=True the sitemap indexes are read
    first (children named '...index...' before the others, and past the cap) but at most
    DISCOVER_URLSETS lists of URLs, and samples of matching and non-matching URLs are kept, so the
    store's patterns can be checked before a full harvest. Its report ends with the store's 'sitemaps' and
    'sitemap_include' lines ready to paste into the stores file (suggest_config, paste_lines).

Only what a store publishes for crawlers is read, and nothing is worked around:
* robots.txt first (RFC 9309: the group naming ROBOTS_AGENT, else '*'; the longest matching rule
  wins, Allow on a tie; '*' and '$' wildcards). Its Sitemap: lines are the starting points (else the
  store's configured sitemaps, else /sitemap.xml, then /sitemap_index.xml only when that one gave
  nothing; a guessed location that is missing is not a failure). A Sitemap: line on another host is
  read only when the store lists that host in 'sitemap_hosts' (a CDN that serves its sitemaps), and
  is reported otherwise. A sitemap its rules disallow is not read. Its Crawl-delay is kept between
  requests (at least MIN_DELAY_S); a store asking for more than MAX_DELAY_S is skipped, never read
  faster than it asks. A robots.txt that answers 5xx stops the store (RFC 9309: assume disallowed).
  Its Visit-time (a UTC window such as 0400-0845, also across midnight) is kept too: outside it the store is not
  read at all (status 'outside_visit_time', neither blocked nor failed: the next refresh asks again).
* A store with a crawl window of our own (CRAWL_WINDOWS: Sharjah Co-op, 04:00-08:45 UTC, their off-peak hours, the
  setting SHARJAHCOOP_CRAWL_WINDOW) gets no request at all outside it, not even robots.txt (status
  'outside_visit_time', as above), and a harvest still running when it ends stops there (status partial, the next
  refresh goes on from there). Only this crawl: single product pages read by the local index are not affected.
* An answer of 401, 403 or 429 stops the store: it is reported 'blocked' and skipped. So is an HTML
  page at every starting point before any sitemap was read (a bot check); later, an HTML page, a
  redirect to the home page or off the store's hosts is one sitemap that failed. No proxy, no
  browser fingerprint, no other headers: the client says who it is (USER_AGENT).
* Sitemap indexes are followed to MAX_DEPTH. A .gz sitemap is decompressed to at most MAX_XML_BYTES.
  A document with a DOCTYPE, or not UTF-8 (the sitemap protocol's encoding), is refused (no entity
  expansion), and only <loc> / <lastmod> are read. A broken file is one failed sitemap, never the
  end of the run; MAX_UNANSWERED files in a row without any answer (timeouts) end that store's turn ('error').
* Only URLs on the store's hosts whose path matches its product pattern are kept.
* SSRF guard (net_guard): redirects are not left to the client; every request and every redirect hop goes only to
  an http(s) host whose addresses are all public. A refused URL is the error 'blocked_url'.
"""

from __future__ import annotations

import gzip
import io
import json
import logging
import re
import time
import xml.etree.ElementTree as ET
import zlib
from collections import deque
from datetime import datetime, timezone
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Collection, Dict, List, Optional, Sequence, Tuple
from urllib.parse import urljoin, urlsplit

import net_guard

logger = logging.getLogger(__name__)

STORES_PATH = Path(__file__).resolve().parent / "data" / "catalog_stores.json"
USER_AGENT = "ProductImageAutomationPipeline/2.0 (catalog index; reads published sitemaps only)"
ROBOTS_AGENT = "ProductImageAutomationPipeline"
TIMEOUT_S = 30.0
MIN_DELAY_S = 1.0
MAX_DELAY_S = 60.0
MAX_DEPTH = 3
MAX_DOWNLOAD_BYTES = 60 * 1024 * 1024
MAX_XML_BYTES = 60 * 1024 * 1024
DEFAULT_SITEMAPS = ("/sitemap.xml", "/sitemap_index.xml")
BLOCK_STATUSES = (401, 403, 429)
DISCOVER_URLSETS = 3
SAMPLES = 5
BATCH = 1000
MAX_UNANSWERED = 3           # sitemap files in a row that timed out or lost the connection: the store stopped answering

# stores crawled only inside a UTC window of their own, whatever robots.txt says: outside it their site blocks or
# throttles requests. host -> the setting holding the window (catalog_match.settings; its default is the window)
CRAWL_WINDOWS = {"sharjahcoop.ae": "SHARJAHCOOP_CRAWL_WINDOW"}

_DOCTYPE_RE = re.compile(rb"<!DOCTYPE", re.I)
_HTML_START_RE = re.compile(rb"<(?:!doctype\s+html|html)\b", re.I)


class SitemapError(Exception):
    """A document that is not a usable sitemap."""


# ---------------------------------------------------------------------------
# Stores
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class StoreConfig:
    key: str
    name: str
    base_url: str
    hosts: Tuple[str, ...]
    product_path: "re.Pattern[str]"
    sitemaps: Tuple[str, ...] = ()
    include: Optional["re.Pattern[str]"] = None     # child sitemap URLs to read (others are skipped)
    exclude: Optional["re.Pattern[str]"] = None
    enabled: bool = True
    note: str = ""
    sitemap_hosts: Tuple[str, ...] = ()             # other hosts serving the store's sitemaps (a CDN)

    def on_store(self, url: str) -> bool:
        return _host(url) in self.hosts

    def on_sitemap_host(self, url: str) -> bool:
        host = _host(url)
        return bool(host) and (host in self.hosts or host in self.sitemap_hosts)

    def is_product(self, url: str) -> bool:
        try:
            parts = urlsplit(url)
        except ValueError:
            return False
        return (parts.hostname or "").lower() in self.hosts and bool(self.product_path.search(parts.path or ""))


def _host(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def _pattern(value: Any, what: str = "pattern") -> Optional["re.Pattern[str]"]:
    if not value:
        return None
    try:
        return re.compile(str(value))
    except re.error as exc:
        raise ValueError(f"{what} {value!r} is not a valid regular expression: {exc}") from exc


def load_stores(path: Optional[Path] = None) -> List[StoreConfig]:
    """The stores of catalog_stores.json, in file order (disabled ones included); ValueError for a bad file."""
    with open(path or STORES_PATH, "r", encoding="utf-8-sig") as fh:      # Notepad saves a byte-order mark
        data = json.load(fh)
    out = []
    for item in data.get("stores", []):
        base = str(item["base_url"]).rstrip("/")
        hosts = tuple(h.lower() for h in (item.get("hosts") or [urlsplit(base).hostname or ""]))
        key = str(item["key"])
        out.append(StoreConfig(
            key=key, name=str(item.get("name") or key), base_url=base, hosts=hosts,
            product_path=_pattern(item["product_path"], f"{key}: product_path"),
            sitemaps=tuple(str(s) for s in item.get("sitemaps") or ()),
            include=_pattern(item.get("sitemap_include"), f"{key}: sitemap_include"),
            exclude=_pattern(item.get("sitemap_exclude"), f"{key}: sitemap_exclude"),
            enabled=bool(item.get("enabled", True)), note=str(item.get("note") or ""),
            sitemap_hosts=tuple(h.lower() for h in item.get("sitemap_hosts") or ())))
    return out


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _local(tag: Any) -> str:
    return str(tag).rsplit("}", 1)[-1].lower() if isinstance(tag, str) else ""


def looks_like_html(body: bytes) -> bool:
    """True when the document starts as an HTML page: after an XML prolog and comments, <!DOCTYPE html> or <html>.

    A plain scan, not one regular expression: '<!--.*?-->' repeated is exponential on '--><!--' runs.
    """
    head = body[:4096].lstrip()
    if head.startswith(b"<?xml"):
        end = head.find(b"?>")
        if end < 0:
            return False
        head = head[end + 2:].lstrip()
    while head.startswith(b"<!--"):
        end = head.find(b"-->", 4)
        if end < 0:
            return False
        head = head[end + 3:].lstrip()
    return bool(_HTML_START_RE.match(head))


def decompress(body: bytes, limit: int = MAX_XML_BYTES) -> bytes:
    """The XML of a sitemap: .gz files (or gzip bytes) decompressed to at most `limit` bytes."""
    if body[:2] != b"\x1f\x8b":
        return body
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(body)) as gz:
            data = gz.read(limit + 1)
    except (OSError, EOFError, zlib.error) as exc:
        raise SitemapError(f"bad gzip: {type(exc).__name__}") from exc
    if len(data) > limit:
        raise SitemapError("too_large")
    return data


def parse_sitemap(data: bytes) -> Tuple[str, List[Tuple[str, Optional[str]]]]:
    """('index' | 'urlset', [(loc, lastmod)]) of one sitemap document."""
    if data[:2] in (b"\xff\xfe", b"\xfe\xff") or b"\x00" in data[:512]:
        raise SitemapError("not utf-8")          # the protocol's encoding; a UTF-16 file would hide a DOCTYPE
    if looks_like_html(data):
        raise SitemapError("html")
    if _DOCTYPE_RE.search(data):
        raise SitemapError("doctype")
    kind = ""
    out: List[Tuple[str, Optional[str]]] = []
    try:
        for event, elem in ET.iterparse(io.BytesIO(data), events=("start", "end")):
            tag = _local(elem.tag)
            if event == "start":
                if not kind:
                    kind = {"sitemapindex": "index", "urlset": "urlset"}.get(tag, tag or "?")
                continue
            if tag in ("url", "sitemap"):
                loc = lastmod = None
                for child in elem:
                    ctag = _local(child.tag)
                    if ctag == "loc":
                        loc = (child.text or "").strip()
                    elif ctag == "lastmod":
                        lastmod = (child.text or "").strip() or None
                if loc:
                    out.append((loc, lastmod))
                elem.clear()
    except (ET.ParseError, ValueError, LookupError) as exc:      # also an unknown or multi-byte encoding
        raise SitemapError(f"bad xml: {exc}") from exc
    if kind not in ("index", "urlset"):
        raise SitemapError(f"not a sitemap (<{kind}>)")
    return kind, out


# ---------------------------------------------------------------------------
# robots.txt
# ---------------------------------------------------------------------------

@dataclass
class Robots:
    status: str                       # 'ok' | 'missing' | 'blocked' | 'error'
    http_status: Optional[int] = None
    sitemaps: List[str] = field(default_factory=list)
    crawl_delay: Optional[float] = None
    rules: List[Tuple[bool, str]] = field(default_factory=list)   # (allow, path pattern) of the group that applies
    visit_times: List[Tuple[int, int]] = field(default_factory=list)   # Visit-time windows: UTC minutes (start, end)

    def allowed(self, url: str) -> bool:
        return robots_allowed(self.rules, url)

    def visit_allowed(self, now: datetime) -> bool:
        """True without a Visit-time, else when `now` (UTC; a naive time is taken as UTC) is inside a window:
        start <= now < end, and a window whose start is after its end crosses midnight."""
        return in_windows(self.visit_times, now)


def in_windows(windows: Sequence[Sequence[int]], now: datetime) -> bool:
    """True without windows, else when `now` (UTC; a naive time is taken as UTC) is inside one of them (UTC minutes):
    start <= now < end, and a window whose start is after its end crosses midnight."""
    if not windows:
        return True
    now = now.astimezone(timezone.utc) if now.tzinfo else now
    minute = now.hour * 60 + now.minute
    for start, end in windows:
        if start == end:
            return True                     # an empty or full-day window: no restriction
        if (start <= minute < end) if start < end else (minute >= start or minute < end):
            return True
    return False


def crawl_window(store: "StoreConfig") -> Optional[Tuple[int, int]]:
    """The store's own crawl window (CRAWL_WINDOWS, by any of its hosts) in UTC minutes, or None. The setting is read
    at call time; a value that is not a window keeps the setting's default."""
    for host in store.hosts:
        for domain, name in CRAWL_WINDOWS.items():
            if host == domain or host.endswith("." + domain):
                from . import settings
                value = str(settings.get(name) or "").strip()
                return parse_visit_time(value) or parse_visit_time(str(settings.DEFAULTS[name]))
    return None


_VISIT_TIME_RE = re.compile(r"(\d{1,2}):?(\d{2})\s*-\s*(\d{1,2}):?(\d{2})")


def parse_visit_time(value: str) -> Optional[Tuple[int, int]]:
    """'0400-0845' (or '04:00-08:45') -> (240, 525) UTC minutes since midnight; None for anything else."""
    match = _VISIT_TIME_RE.fullmatch(value.strip())
    if not match:
        return None
    h1, m1, h2, m2 = (int(g) for g in match.groups())
    start, end = h1 * 60 + m1, h2 * 60 + m2
    if m1 > 59 or m2 > 59 or start > 1440 or end > 1440:
        return None
    return start, end


def visit_text(windows: Sequence[Sequence[int]]) -> str:
    """'04:00-08:45 UTC' for the console and the harvest report."""
    def clock(m: int) -> str:
        return f"{m // 60 % 24:02d}:{m % 60:02d}"
    return ", ".join(f"{clock(a)}-{clock(b)} UTC" for a, b in windows)


def _agent_matches(value: str) -> bool:
    token = value.strip().lower()
    return token == ROBOTS_AGENT.lower() or token.split("/", 1)[0] == ROBOTS_AGENT.lower()


def parse_robots(text: str) -> Robots:
    """RFC 9309: the groups naming ROBOTS_AGENT (case-insensitive), else the '*' groups; Sitemap: lines anywhere."""
    groups: List[Tuple[List[str], List[Tuple[bool, str]], List[float], List[Tuple[int, int]]]] = []
    sitemaps: List[str] = []
    current: Optional[Tuple[List[str], List[Tuple[bool, str]], List[float], List[Tuple[int, int]]]] = None
    agent_line = False
    for raw in text.lstrip("\ufeff").splitlines():
        line = raw.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        key, value = (part.strip() for part in line.split(":", 1))
        key = key.lower()
        if key == "sitemap":
            if value:
                sitemaps.append(value)
            continue
        if key == "user-agent":
            if current is None or not agent_line:        # a user-agent line after rules starts a new group
                current = ([], [], [], [])
                groups.append(current)
            current[0].append(value)
            agent_line = True
            continue
        agent_line = False
        if current is None:
            continue                                     # rules before any user-agent line apply to nobody
        if key in ("allow", "disallow"):
            if value:                                    # an empty Disallow allows everything: no rule
                current[1].append((key == "allow", value))
        elif key == "crawl-delay":
            try:
                current[2].append(float(value))
            except ValueError:
                pass
        elif key == "visit-time":
            window = parse_visit_time(value)
            if window is not None:
                current[3].append(window)
    chosen = [g for g in groups if any(_agent_matches(a) for a in g[0])] \
        or [g for g in groups if any(a.strip() == "*" for a in g[0])]
    delays = [d for g in chosen for d in g[2] if d >= 0]
    return Robots(status="ok", sitemaps=sitemaps, crawl_delay=max(delays) if delays else None,
                  rules=[r for g in chosen for r in g[1]], visit_times=[w for g in chosen for w in g[3]])


def _rule_matches(pattern: str, path: str) -> bool:
    """RFC 9309 path matching: a prefix match, '*' any characters, a final '$' the end of the path (linear scan)."""
    anchored = pattern.endswith("$")
    if anchored:
        pattern = pattern[:-1]
    p = t = 0
    star = mark = -1
    while True:
        if p == len(pattern) and not anchored:
            return True
        if t == len(path):
            break
        if p < len(pattern) and pattern[p] == "*":
            star, mark = p, t
            p += 1
        elif p < len(pattern) and pattern[p] == path[t]:
            p += 1
            t += 1
        elif star >= 0:
            mark += 1
            p, t = star + 1, mark
        else:
            return False
    while p < len(pattern) and pattern[p] == "*":
        p += 1
    return p == len(pattern)


def robots_allowed(rules: Sequence[Tuple[bool, str]], url: str) -> bool:
    """The longest matching rule wins, Allow on a tie; no matching rule (or /robots.txt itself) is allowed."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    if path == "/robots.txt":
        return True
    best, allow = -1, True
    for is_allow, pattern in rules:
        if _rule_matches(pattern, path):
            n = len(pattern)
            if n > best or (n == best and is_allow):
                best, allow = n, is_allow
    return allow


# ---------------------------------------------------------------------------
# Harvest
# ---------------------------------------------------------------------------

@dataclass
class HarvestReport:
    store: str
    status: str = "ok"                # ok | partial | empty | blocked | error | outside_visit_time
    error: str = ""
    robots: str = ""                  # robots.txt status
    crawl_delay: float = MIN_DELAY_S
    started_from: List[str] = field(default_factory=list)
    sitemaps_read: int = 0
    skipped: List[Tuple[str, str]] = field(default_factory=list)   # (url, why)
    tree: List[Dict[str, Any]] = field(default_factory=list)       # one entry per sitemap read
    urls_seen: int = 0
    product_urls: int = 0
    new_urls: int = 0
    pruned: int = 0
    truncated: bool = False
    visit_window: Optional[List[List[int]]] = None    # robots.txt Visit-time windows (UTC minutes); None: unknown
    product_samples: List[str] = field(default_factory=list)
    other_samples: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


class SitemapHarvester:
    """Reads one store's sitemaps politely (see the module docstring)."""

    def __init__(self, http: Any = None, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic, timeout: float = TIMEOUT_S,
                 max_bytes: int = MAX_DOWNLOAD_BYTES, utc_now: Optional[Callable[[], datetime]] = None) -> None:
        if http is None:
            import requests
            http = requests.Session()
        self.http = http
        self._sleep = sleep
        self._clock = clock
        self._utc_now = utc_now or (lambda: datetime.now(timezone.utc))
        self.timeout = timeout
        self.max_bytes = max_bytes
        self.delay = MIN_DELAY_S
        self._last: Optional[float] = None

    # -- http ---------------------------------------------------------------
    def _wait(self) -> None:
        if self._last is not None:
            left = self.delay - (self._clock() - self._last)
            if left > 0:
                self._sleep(left)
        self._last = self._clock()

    def get(self, url: str, store: Optional[StoreConfig] = None) -> Tuple[Optional[bytes], Optional[int], str]:
        """(body, http status, error) of one polite GET; never raises. With a store, a redirect off its
        (sitemap) hosts or to its home page is an error: that is not the sitemap that was asked for."""
        self._wait()
        headers = {"User-Agent": USER_AGENT, "Accept": "application/xml,text/xml,text/plain;q=0.9,*/*;q=0.5"}
        try:
            resp = net_guard.follow(url, lambda hop, _target: self.http.get(
                hop, headers=headers, timeout=self.timeout, stream=True, allow_redirects=False))
        except net_guard.BlockedURL as exc:
            logger.warning("sitemaps: refused, %s", exc)
            return None, None, net_guard.BLOCKED
        except Exception as exc:
            return None, None, "timeout" if "timeout" in type(exc).__name__.lower() else "connection_error"
        try:
            status = int(getattr(resp, "status_code", 0) or 0)
            if status != 200:
                return None, status, f"http_{status}"
            final = getattr(resp, "url", None)
            if store is not None and isinstance(final, str) and final and final != url:
                if not store.on_sitemap_host(final):
                    return None, status, "redirected outside the store"
                if (urlsplit(final).path or "/") == "/":
                    return None, status, "redirected to the home page"
            buf = bytearray()
            for chunk in resp.iter_content(chunk_size=64 * 1024):
                if chunk:
                    buf.extend(chunk)
                    if len(buf) > self.max_bytes:
                        return None, status, "too_large"
            return bytes(buf), status, ""
        except Exception as exc:
            return None, None, "timeout" if "timeout" in type(exc).__name__.lower() else "connection_error"
        finally:
            close = getattr(resp, "close", None)
            if callable(close):
                close()

    def robots(self, store: StoreConfig) -> Robots:
        body, status, error = self.get(store.base_url + "/robots.txt")
        if status in BLOCK_STATUSES:
            return Robots(status="blocked", http_status=status)
        if status is not None and status >= 500:
            return Robots(status="error", http_status=status)
        if body is None:
            if status is None:      # no answer at all: the sitemaps would not answer either
                return Robots(status="error", http_status=None)
            return Robots(status="missing", http_status=status)   # 404 / 410: no rules, no listed sitemaps
        if looks_like_html(body):
            return Robots(status="missing", http_status=status)
        robots = parse_robots(body.decode("utf-8-sig", errors="replace"))
        robots.http_status = status
        return robots

    # -- harvest ------------------------------------------------------------
    def harvest(self, store: StoreConfig, on_urls: Optional[Callable[[List[Tuple[str, Optional[str]]]], Any]] = None,
                max_urls: Optional[int] = None, max_sitemaps: Optional[int] = None,
                discover: bool = False, should_stop: Optional[Callable[[], bool]] = None,
                skip_urls: Optional[Collection[str]] = None) -> HarvestReport:
        """should_stop: asked before every sitemap file is read and between the batches of a long URL list; True ends
        the harvest there (status partial, 'truncated': the automatic refresh's time budget, catalog_match.index_refresh;
        a list cut part-way is marked 'stopped' in the tree). skip_urls: URL lists an unfinished refresh already read
        (index_refresh's resume): they are not read again, the indexes still are.
        MAX_UNANSWERED sitemap files in a row without an answer (timeout, lost connection) end the store as 'error'."""
        rep = HarvestReport(store=store.key)
        window = crawl_window(store)
        if window is not None:
            if not in_windows([window], self._utc_now()):
                rep.status, rep.visit_window = "outside_visit_time", [list(window)]
                rep.error = (f"this store is crawled only at {visit_text([window])}: store not read now (no request), "
                             "asked again at the next refresh")
                return rep
            budget_stop = should_stop

            def should_stop() -> bool:
                if not in_windows([window], self._utc_now()):
                    rep.error = rep.error or f"stopped at the end of the store's crawl window ({visit_text([window])})"
                    return True
                return budget_stop() if budget_stop is not None else False
        robots = self.robots(store)
        rep.robots = robots.status if robots.http_status is None else f"{robots.status} (http {robots.http_status})"
        if robots.status in ("blocked", "error"):
            rep.status = robots.status
            rep.error = f"robots.txt answered {robots.http_status or 'nothing'}: store skipped"
            return rep
        rep.visit_window = [list(w) for w in robots.visit_times]
        if not robots.visit_allowed(self._utc_now()):
            rep.status = "outside_visit_time"
            rep.error = (f"robots.txt allows visits only at {visit_text(robots.visit_times)}: store not read now, "
                         "asked again at the next refresh")
            return rep
        if robots.crawl_delay is not None and robots.crawl_delay > MAX_DELAY_S:
            rep.status = "blocked"
            rep.error = (f"robots.txt asks for a Crawl-delay of {robots.crawl_delay:g}s (more than {MAX_DELAY_S:g}s): "
                         "store skipped, never read faster than it asks")
            return rep
        self.delay = max(MIN_DELAY_S, robots.crawl_delay or 0.0)
        rep.crawl_delay = self.delay
        listed = [s for s in robots.sitemaps if store.on_sitemap_host(s)]
        rep.skipped.extend((s, "outside the store (robots.txt lists it; add its host to sitemap_hosts)")
                           for s in robots.sitemaps if not store.on_sitemap_host(s))
        guesses: List[str] = []
        starts = [urljoin(store.base_url + "/", s) for s in store.sitemaps] or listed
        if not starts:
            # the usual locations: the second only when the first gave nothing; a missing guess is no failure
            guesses = [store.base_url + p for p in DEFAULT_SITEMAPS]
            starts = guesses[:1]
        rep.started_from = list(starts)
        queue = deque((url, 0) for url in starts)
        seen = set()
        parent_of: Dict[str, str] = {}       # a child sitemap -> the index that listed it (the tree's 'parent')
        emitted = urlsets = failed = unanswered = 0
        start_answers: List[str] = []        # why each starting point gave nothing, while nothing was read
        while queue or (guesses and rep.sitemaps_read == 0 and len(rep.started_from) < len(guesses)
                        and "html" not in start_answers):      # an HTML page at the first guess: a bot check
            if not queue:
                nxt = guesses[len(rep.started_from)]
                rep.started_from.append(nxt)
                queue.append((nxt, 0))
            url, depth = queue.popleft()
            if url in seen:
                continue
            seen.add(url)
            why = self._skip_reason(store, robots, url, depth)
            if why:
                rep.skipped.append((url, why))
                continue
            if skip_urls and url in skip_urls:
                rep.skipped.append((url, "read by the last refresh"))
                continue
            if discover and urlsets >= DISCOVER_URLSETS and "index" not in url.lower():
                rep.skipped.append((url, "not read (discover)"))
                continue
            if max_sitemaps is not None and rep.sitemaps_read >= max_sitemaps:
                rep.truncated = True
                rep.skipped.append((url, "not read (--max-sitemaps)"))
                continue
            if should_stop is not None and should_stop():
                rep.truncated = True
                rep.error = rep.error or "stopped at the time budget"
                rep.skipped.append((url, "not read (time budget)"))
                break
            body, status, error = self.get(url, store)
            if status in BLOCK_STATUSES:
                rep.status, rep.error = "blocked", f"http_{status} on {url}: store skipped"
                break
            unanswered = unanswered + 1 if body is None and error in ("timeout", "connection_error") else 0
            if unanswered >= MAX_UNANSWERED:          # a slow store gives up its turn, the next store is read
                rep.skipped.append((url, error))
                rep.status, rep.error = "error", f"{unanswered} sitemap files in a row got no answer ({error}): stopped"
                break
            try:
                if body is None:
                    raise SitemapError(error or "error")
                kind, entries = parse_sitemap(decompress(body))
            except SitemapError as exc:
                rep.skipped.append((url, str(exc)))
                if depth == 0 and rep.sitemaps_read == 0:
                    start_answers.append(str(exc))
                if not (url in guesses and status in (404, 410)):
                    failed += 1              # a guessed location that is not there is not a failure
                continue
            rep.sitemaps_read += 1
            if kind == "index":
                rep.tree.append({"depth": depth, "url": url, "kind": "index", "entries": len(entries),
                                 "parent": parent_of.get(url)})
                children = [loc for loc, _ in entries]
                for child in children:
                    parent_of.setdefault(child, url)
                if discover:     # the indexes first (the store's structure), then the product lists
                    children.sort(key=lambda u: 0 if "index" in u.lower() else 1 if "product" in u.lower() else 2)
                if depth + 1 > MAX_DEPTH:
                    rep.skipped.extend((c, "too deep") for c in children)
                else:
                    queue.extend((c, depth + 1) for c in children)
                continue
            urlsets += 1
            products = [(loc, lastmod) for loc, lastmod in entries if store.is_product(loc)]
            rep.tree.append({"depth": depth, "url": url, "kind": "urlset", "entries": len(entries),
                             "products": len(products), "parent": parent_of.get(url)})
            rep.urls_seen += len(entries)
            rep.product_urls += len(products)
            for loc, _ in products[:SAMPLES - len(rep.product_samples)]:
                rep.product_samples.append(loc)
            for loc, _ in entries:
                if len(rep.other_samples) >= SAMPLES:
                    break
                if not store.is_product(loc):
                    rep.other_samples.append(loc)
            if max_urls is not None and emitted + len(products) > max_urls:
                products = products[:max(0, max_urls - emitted)]
                rep.truncated = True         # products were cut
                rep.tree[-1]["stopped"] = True
            emitted += len(products)
            cut = False
            if on_urls is not None:
                for i in range(0, len(products), BATCH):
                    if i and should_stop is not None and should_stop():     # one big list cannot overrun the budget
                        cut = True
                        break
                    rep.new_urls += int(on_urls(products[i:i + BATCH]) or 0)
            if cut:
                rep.truncated = True
                rep.error = rep.error or "stopped at the time budget"
                rep.tree[-1]["stopped"] = True
                break
            if max_urls is not None and emitted >= max_urls:
                if queue:
                    rep.truncated = True     # sitemaps left unread
                break
        if rep.status == "ok":
            if rep.sitemaps_read == 0:
                if start_answers and all(a == "html" for a in start_answers):
                    rep.status = "blocked"
                    rep.error = f"an HTML page instead of a sitemap at {rep.started_from[0]} (bot check?)"
                else:
                    rep.status = "error"
                    rep.error = rep.error or "no sitemap could be read"
            elif rep.truncated or failed:
                rep.status = "partial"
            elif rep.product_urls == 0:
                rep.status = "empty"
        logger.info("sitemaps %s: %s, %d sitemaps, %d urls, %d product urls", store.key, rep.status,
                    rep.sitemaps_read, rep.urls_seen, rep.product_urls)
        return rep

    @staticmethod
    def _skip_reason(store: StoreConfig, robots: Robots, url: str, depth: int) -> str:
        if not url.lower().startswith(("http://", "https://")) or not store.on_sitemap_host(url):
            return "outside the store"
        if store.on_store(url) and not robots.allowed(url):     # the store's rules are for its own hosts
            return "disallowed by robots.txt"
        if depth > 0 and store.include is not None and not store.include.search(url):
            return "not included"
        if store.exclude is not None and store.exclude.search(url):
            return "excluded"
        return ""


def format_report(rep: HarvestReport, store: Optional[StoreConfig] = None, discover: bool = False) -> str:
    """The console text of one store's harvest (or discovery)."""
    lines = [f"== {rep.store}{f' ({store.name})' if store else ''}: {rep.status.upper()}"
             + (f" - {rep.error}" if rep.error else "")]
    lines.append(f"   robots.txt {rep.robots or '-'}, crawl delay {rep.crawl_delay:g}s")
    if rep.started_from:
        lines.append("   started from " + ", ".join(rep.started_from[:5]) + (" ..." if len(rep.started_from) > 5 else ""))
    for node in rep.tree[: (60 if discover else 15)]:
        pad = "   " + "  " * int(node["depth"])
        if node["kind"] == "index":
            lines.append(f"{pad}index  {node['url']} -> {node['entries']} sitemaps")
        else:
            lines.append(f"{pad}urlset {node['url']} -> {node['entries']} urls, {node['products']} product pages")
    if len(rep.tree) > (60 if discover else 15):
        lines.append(f"   ... {len(rep.tree) - (60 if discover else 15)} more sitemaps")
    if rep.skipped:
        why: Dict[str, int] = {}
        for _, reason in rep.skipped:
            why[reason] = why.get(reason, 0) + 1
        lines.append("   skipped " + ", ".join(f"{n} {reason}" for reason, n in sorted(why.items())))
        if discover:
            for url, reason in rep.skipped[:30]:
                lines.append(f"     - {url} ({reason})")
    lines.append(f"   {rep.sitemaps_read} sitemaps read, {rep.urls_seen} urls, {rep.product_urls} product pages"
                 + (f", {rep.new_urls} new" if not discover else "") + (" (stopped at a limit)" if rep.truncated else ""))
    if rep.pruned:
        lines.append(f"   {rep.pruned} pages no longer listed were removed")
    if discover or rep.product_samples:
        lines.extend(["   product page samples:"] + [f"     + {u}" for u in rep.product_samples[:SAMPLES]])
    if discover:
        lines.extend(["   other url samples (not product pages by the pattern):"]
                     + [f"     - {u}" for u in rep.other_samples[:SAMPLES]])
        lines.extend(paste_lines(rep))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# What a --discover run found, ready to paste into the stores file
# ---------------------------------------------------------------------------

_REGEX_SPECIAL = frozenset(".^$*+?{}[]\\|()")


def _path_pattern(url: str) -> str:
    """A sitemap URL's path as a regular expression with its numbers generalised, so the other files of the same
    series match it too: '/sitemaps/Product-en-AED-12.xml' -> '/sitemaps/Product-en-AED-\\d+\\.xml$'."""
    out = []
    for part in re.split(r"(\d+)", urlsplit(url).path or "/"):
        out.append(r"\d+" if part.isdigit() else "".join("\\" + ch if ch in _REGEX_SPECIAL else ch for ch in part))
    return "".join(out) + "$"


def suggest_config(rep: HarvestReport) -> Optional[Dict[str, Any]]:
    """The stores-file values a discovery found: {sitemaps, sitemap_include, listed, kept, also_empty}; None when no
    URL list that was read held a product page.

    sitemaps: the starting points the product pages were found under (not the other countries' or languages').
    sitemap_include: the files on the way from them to the URL lists that held product pages, numbers generalised
    (the unread files of the same series match too); '' when it would keep every file the indexes list anyway.
    listed / kept: the files the indexes list and how many of them the include keeps; also_empty: URL lists that were
    read, held no product page and still match it (the file names cannot tell them apart)."""
    nodes = {n["url"]: n for n in rep.tree}
    roots: List[str] = []
    chain: List[str] = []
    for node in rep.tree:
        if node["kind"] != "urlset" or not node.get("products"):
            continue
        hops = 0
        while node.get("parent") in nodes and hops <= MAX_DEPTH:      # up to the starting point
            if node["url"] not in chain:
                chain.append(node["url"])
            node, hops = nodes[node["parent"]], hops + 1
        if node["url"] not in roots:
            roots.append(node["url"])
    if not roots:
        return None
    listed = list(dict.fromkeys([n["url"] for n in rep.tree if n.get("parent")]
                                + [u for u, why in rep.skipped if why == "not read (discover)"]))
    patterns = list(dict.fromkeys(_path_pattern(u) for u in chain))
    include = (patterns[0] if len(patterns) == 1 else "(?:" + "|".join(p[:-1] for p in patterns) + ")$") \
        if patterns else ""
    regex = re.compile(include) if include else None
    kept = [u for u in listed if regex is None or regex.search(u)]
    if len(kept) == len(listed):
        include = ""                         # every listed file is read anyway: nothing to filter
    also_empty = sum(1 for n in rep.tree if n.get("parent") and n["kind"] == "urlset" and not n.get("products")
                     and (not include or (regex is not None and regex.search(n["url"]))))
    return {"sitemaps": roots, "sitemap_include": include, "listed": len(listed), "kept": len(kept),
            "also_empty": also_empty}


def paste_lines(rep: HarvestReport) -> List[str]:
    """The console lines of suggest_config: exactly what to paste into the store's entry of catalog_stores.json."""
    found = suggest_config(rep)
    if found is None:
        why = ("the store did not give its sitemaps" if rep.status in ("blocked", "error", "outside_visit_time")
               else "no page of the URL lists read matched product_path: compare the samples above with it")
        return [f"   nothing to paste ({why})"]
    lines = [f'   paste these lines into the store "{rep.store}" of catalog_match/data/catalog_stores.json:',
             f'     "sitemaps": {json.dumps(found["sitemaps"])},',
             f'     "sitemap_include": {json.dumps(found["sitemap_include"])},',
             '     "enabled": true,']
    if found["listed"]:
        lines.append(f"   (the include keeps {found['kept']} of the {found['listed']} sitemaps the indexes list)")
    if found["also_empty"]:
        lines.append(f"   note: {found['also_empty']} sitemap(s) read without a product page are kept as well")
    if rep.status in ("blocked", "error"):
        lines.append(f"   note: the store stopped answering part way ({rep.status}): run --discover again before pasting")
    return lines


def enabled_stores(stores: Sequence[StoreConfig], keys: Optional[Sequence[str]] = None) -> List[StoreConfig]:
    """The stores asked for by key (disabled ones included), else every enabled store."""
    if keys:
        by_key = {s.key: s for s in stores}
        unknown = [k for k in keys if k not in by_key]
        if unknown:
            raise KeyError(f"unknown store(s) {unknown}; known: {sorted(by_key)}")
        return [by_key[k] for k in keys]
    return [s for s in stores if s.enabled]
