"""Sitemap harvester for the local catalog index (catalog_match.local_index).

load_stores(path=None) -> [StoreConfig]
    The stores in catalog_match/data/catalog_stores.json: hosts, the path pattern of a product page,
    and optionally the sitemap URLs to start from and include / exclude patterns for child sitemaps.

SitemapHarvester().harvest(store, on_urls=None, max_urls=None, max_sitemaps=None, discover=False)
    -> HarvestReport
    Reads the store's published sitemaps and hands every product URL to on_urls([(url, lastmod)])
    in batches (on_urls returns how many were new). With discover=True every sitemap index is read
    but at most DISCOVER_URLSETS lists of URLs, and samples of matching and non-matching URLs are
    kept, so the store's patterns can be checked before a full harvest.

Only what a store publishes for crawlers is read, and nothing is worked around:
* robots.txt first. Its Sitemap: lines are the starting points (else the store's configured
  sitemaps, else /sitemap.xml and /sitemap_index.xml); a sitemap its rules disallow for
  ROBOTS_AGENT is not read; its Crawl-delay is kept between requests (at least MIN_DELAY_S, at
  most MAX_DELAY_S). A robots.txt that answers 5xx stops the store (RFC 9309: assume disallowed).
* An answer of 401, 403 or 429, or an HTML page where a sitemap should be (a bot check), stops the
  store: it is reported 'blocked' and skipped. No proxy, no browser fingerprint, no other headers:
  the client says who it is (USER_AGENT).
* Sitemap indexes are followed to MAX_DEPTH. A .gz sitemap is decompressed to at most MAX_XML_BYTES.
  A document with a DOCTYPE is refused (no entity expansion), and only <loc> / <lastmod> are read.
* Only URLs on the store's hosts whose path matches its product pattern are kept.
"""

from __future__ import annotations

import gzip
import io
import json
import logging
import re
import time
import xml.etree.ElementTree as ET
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

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

    def on_store(self, url: str) -> bool:
        try:
            host = (urlsplit(url).hostname or "").lower()
        except ValueError:
            return False
        return host in self.hosts

    def is_product(self, url: str) -> bool:
        try:
            parts = urlsplit(url)
        except ValueError:
            return False
        return (parts.hostname or "").lower() in self.hosts and bool(self.product_path.search(parts.path or ""))


def _pattern(value: Any) -> Optional["re.Pattern[str]"]:
    return re.compile(str(value)) if value else None


def load_stores(path: Optional[Path] = None) -> List[StoreConfig]:
    """The stores of catalog_stores.json, in file order (disabled ones included)."""
    with open(path or STORES_PATH, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    out = []
    for item in data.get("stores", []):
        base = str(item["base_url"]).rstrip("/")
        hosts = tuple(h.lower() for h in (item.get("hosts") or [urlsplit(base).hostname or ""]))
        out.append(StoreConfig(
            key=str(item["key"]), name=str(item.get("name") or item["key"]), base_url=base, hosts=hosts,
            product_path=re.compile(str(item["product_path"])),
            sitemaps=tuple(str(s) for s in item.get("sitemaps") or ()),
            include=_pattern(item.get("sitemap_include")), exclude=_pattern(item.get("sitemap_exclude")),
            enabled=bool(item.get("enabled", True)), note=str(item.get("note") or "")))
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
    except (OSError, EOFError) as exc:
        raise SitemapError(f"bad gzip: {type(exc).__name__}") from exc
    if len(data) > limit:
        raise SitemapError("too_large")
    return data


def parse_sitemap(data: bytes) -> Tuple[str, List[Tuple[str, Optional[str]]]]:
    """('index' | 'urlset', [(loc, lastmod)]) of one sitemap document."""
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
    except ET.ParseError as exc:
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
    parser: Optional[RobotFileParser] = None

    def allowed(self, url: str) -> bool:
        return self.parser is None or self.parser.can_fetch(ROBOTS_AGENT, url)


def parse_robots(text: str) -> Robots:
    parser = RobotFileParser()
    parser.parse(text.splitlines())
    delay = parser.crawl_delay(ROBOTS_AGENT)
    try:
        delay = float(delay) if delay is not None else None
    except (TypeError, ValueError):
        delay = None
    return Robots(status="ok", sitemaps=list(parser.site_maps() or []), crawl_delay=delay, parser=parser)


# ---------------------------------------------------------------------------
# Harvest
# ---------------------------------------------------------------------------

@dataclass
class HarvestReport:
    store: str
    status: str = "ok"                # ok | partial | empty | blocked | error
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
    product_samples: List[str] = field(default_factory=list)
    other_samples: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


class SitemapHarvester:
    """Reads one store's sitemaps politely (see the module docstring)."""

    def __init__(self, http: Any = None, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic, timeout: float = TIMEOUT_S,
                 max_bytes: int = MAX_DOWNLOAD_BYTES) -> None:
        if http is None:
            import requests
            http = requests.Session()
        self.http = http
        self._sleep = sleep
        self._clock = clock
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

    def get(self, url: str) -> Tuple[Optional[bytes], Optional[int], str]:
        """(body, http status, error) of one polite GET; never raises."""
        self._wait()
        headers = {"User-Agent": USER_AGENT, "Accept": "application/xml,text/xml,text/plain;q=0.9,*/*;q=0.5"}
        try:
            resp = self.http.get(url, headers=headers, timeout=self.timeout, stream=True, allow_redirects=True)
        except Exception as exc:
            return None, None, "timeout" if "timeout" in type(exc).__name__.lower() else "connection_error"
        try:
            status = int(getattr(resp, "status_code", 0) or 0)
            if status != 200:
                return None, status, f"http_{status}"
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
        robots = parse_robots(body.decode("utf-8", errors="replace"))
        robots.http_status = status
        return robots

    # -- harvest ------------------------------------------------------------
    def harvest(self, store: StoreConfig, on_urls: Optional[Callable[[List[Tuple[str, Optional[str]]]], Any]] = None,
                max_urls: Optional[int] = None, max_sitemaps: Optional[int] = None,
                discover: bool = False) -> HarvestReport:
        rep = HarvestReport(store=store.key)
        robots = self.robots(store)
        rep.robots = robots.status if robots.http_status is None else f"{robots.status} (http {robots.http_status})"
        if robots.status in ("blocked", "error"):
            rep.status = robots.status
            rep.error = f"robots.txt answered {robots.http_status or 'nothing'}: store skipped"
            return rep
        self.delay = min(MAX_DELAY_S, max(MIN_DELAY_S, robots.crawl_delay or 0.0))
        rep.crawl_delay = self.delay
        starts = [urljoin(store.base_url + "/", s) for s in store.sitemaps] or \
            [s for s in robots.sitemaps if store.on_store(s)] or [store.base_url + p for p in DEFAULT_SITEMAPS]
        rep.started_from = list(starts)
        queue = deque((url, 0) for url in starts)
        seen = set()
        emitted = urlsets = failed = 0
        while queue:
            url, depth = queue.popleft()
            if url in seen:
                continue
            seen.add(url)
            why = self._skip_reason(store, robots, url, depth)
            if why:
                rep.skipped.append((url, why))
                continue
            if discover and urlsets >= DISCOVER_URLSETS:
                rep.skipped.append((url, "not read (discover)"))
                continue
            if max_sitemaps is not None and rep.sitemaps_read >= max_sitemaps:
                rep.truncated = True
                rep.skipped.append((url, "not read (--max-sitemaps)"))
                continue
            body, status, error = self.get(url)
            if status in BLOCK_STATUSES:
                rep.status, rep.error = "blocked", f"http_{status} on {url}: store skipped"
                break
            try:
                if body is None:
                    raise SitemapError(error or "error")
                kind, entries = parse_sitemap(decompress(body))
            except SitemapError as exc:
                if str(exc) == "html":
                    rep.status, rep.error = "blocked", f"an HTML page instead of a sitemap at {url} (bot check?)"
                    break
                rep.skipped.append((url, str(exc)))
                failed += 1
                continue
            rep.sitemaps_read += 1
            if kind == "index":
                rep.tree.append({"depth": depth, "url": url, "kind": "index", "entries": len(entries)})
                children = [loc for loc, _ in entries]
                if discover:     # read the product lists first
                    children.sort(key=lambda u: 0 if "product" in u.lower() else 1)
                if depth + 1 > MAX_DEPTH:
                    rep.skipped.extend((c, "too deep") for c in children)
                else:
                    queue.extend((c, depth + 1) for c in children)
                continue
            urlsets += 1
            products = [(loc, lastmod) for loc, lastmod in entries if store.is_product(loc)]
            rep.tree.append({"depth": depth, "url": url, "kind": "urlset", "entries": len(entries),
                             "products": len(products)})
            rep.urls_seen += len(entries)
            rep.product_urls += len(products)
            for loc, _ in products[:SAMPLES - len(rep.product_samples)]:
                rep.product_samples.append(loc)
            for loc, _ in entries:
                if len(rep.other_samples) >= SAMPLES:
                    break
                if not store.is_product(loc):
                    rep.other_samples.append(loc)
            if max_urls is not None and emitted + len(products) >= max_urls:
                products = products[:max(0, max_urls - emitted)]
                rep.truncated = True
            emitted += len(products)
            if on_urls is not None:
                for i in range(0, len(products), BATCH):
                    rep.new_urls += int(on_urls(products[i:i + BATCH]) or 0)
            if rep.truncated and max_urls is not None and emitted >= max_urls:
                break
        if rep.status == "ok":
            if rep.sitemaps_read == 0:
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
        if not url.lower().startswith(("http://", "https://")) or not store.on_store(url):
            return "outside the store"
        if not robots.allowed(url):
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
    return "\n".join(lines)


def enabled_stores(stores: Sequence[StoreConfig], keys: Optional[Sequence[str]] = None) -> List[StoreConfig]:
    """The stores asked for by key (disabled ones included), else every enabled store."""
    if keys:
        by_key = {s.key: s for s in stores}
        unknown = [k for k in keys if k not in by_key]
        if unknown:
            raise KeyError(f"unknown store(s) {unknown}; known: {sorted(by_key)}")
        return [by_key[k] for k in keys]
    return [s for s in stores if s.enabled]
