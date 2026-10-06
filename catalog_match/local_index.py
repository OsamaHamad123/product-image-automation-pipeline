"""Local catalog index: UAE retailer product pages listed in the stores' own sitemaps (free retrieval).

Built offline by scripts/build_catalog_index.py (catalog_match.sitemaps): every product URL a store
lists in its sitemaps becomes one row of catalog_products, and the words of its URL slug become rows
of catalog_tokens. At search time LocalIndexProvider (a lookup provider that needs no GTIN) runs in
parallel with the first web query:

  1. rows whose slug (or page title, once the page was read) holds every word of one of the SKU's
     brand phrases come out of the index, most product words first (at most CANDIDATE_ROWS per
     phrase); with a valid GTIN, rows whose page stated that GTIN come too, and so do rows whose own
     URL is that barcode (url_gtin, read from the URL at harvest time: catalog_match.url_gtin), even
     before their page was ever read;
  2. each row is scored like a search result that has only its URL (score.score_candidate on the
     page slug and the page title): a hard reject (another brand, a size, pack or variant conflict)
     or a size / pack conflict in the URL alone is dropped, so is a row without brand evidence or
     below MIN_COVERAGE of the product-type words;
     the rest are ranked with score.rank_key, one row per store first;
  3. walking the ranked rows, the first LOCAL_INDEX_MAX_PAGES usable ones give candidates: a row
     read within LOCAL_INDEX_PAGE_TTL_DAYS gives what its page said then, an unread or stale row is
     read now (pages.PageFetcher, in parallel) for its main image, product name and GTIN. A row
     known to be dead (redirected, no image, 404 / 410), one that failed in the last
     FAILED_PAGE_TTL_H and one on a host left alone is skipped and takes no slot. What a page
     said is kept in its row (a transient failure keeps what an earlier read said, only the
     status changes), so the next run needs no request for it. The reads hold the first step
     at most READ_DEADLINE_S: a page still loading then gives nothing now (its record is still
     saved for the next run);
  4. each page gives at most one candidate: provider 'local_index', query_id 'IDX', sanctioned
     False (a page we read ourselves never auto-publishes; it can be pre-selected for review).

A page that now redirects elsewhere (sold out, delisted) gives nothing and is remembered as such.
A store host that refused or timed out BLOCKED_HOST_LIMIT reads in a row is not asked again in
this process.
The index never stops the web search early (retrieve.t1_early_stop) and its answers never make a
web-search outage look healthy (decide.LOOKUP_PROVIDERS): it only adds candidates.

lookup_gtins(spec, gtins, known_pages) asks the index for the barcodes other stores wrote in their URLs for a
row without a valid sheet barcode (url_gtin.agreeing_gtins: only listings whose title agrees with the row), under
query_id IDXG: the same row choice and page reads, the barcode never counts as evidence for a candidate.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple
from urllib.parse import urlsplit, urlunsplit

from . import cassette, settings
from .gtin import is_global_gtin
from .models import Candidate, CandidateScore, SkuSpec
from .providers.base import BaseProvider, ProviderEmpty
from .text_norm import is_arabic, tokens, url_host, url_path_text

logger = logging.getLogger(__name__)

PROVIDER = "local_index"
QUERY_ID = "IDX"
GTIN_QUERY_ID = "IDXG"         # lookup_gtins: the barcodes other stores' URLs gave
CANDIDATE_ROWS = 200           # rows pulled from the index per brand phrase, most product words first
MIN_COVERAGE = 0.5             # share of the SKU's product-type words a row's slug / title must hold
FAILED_PAGE_TTL_H = 24         # a timeout, 5xx or refusal is retried the next day
PERMANENT_PAGE_STATUSES = ("ok", "redirected", "no_image", "http_404", "http_410")
BLOCKED_HOST_LIMIT = 3         # refusals in a row before a host is left alone for this process
MAX_TOKEN_LEN = 64
MAX_NAME_KEYS = 12
# units and pack words are size evidence (score.py reads them), not product words to rank rows by
UNIT_WORDS = frozenset({"ml", "cl", "lt", "ltr", "ltrs", "litre", "liter", "kg", "kgs", "gm", "gms", "gr", "grm",
                        "gram", "oz", "lb", "pc", "pcs", "piece", "pack", "pk", "pkt"})
COUNT_CACHE_S = 600.0
FETCH_WORKERS = 3
READ_DEADLINE_S = 8.0          # the page reads hold the first search step (Q1 waits on the lookups) at most this long
PAGE_TIMEOUT_S = 6.0           # one index page read (the expansion round's reads keep pages.PAGE_TIMEOUT_S)
HOST_FAILURE_STATUSES = ("http_403", "http_429", "timeout", "connection_error")


# ---------------------------------------------------------------------------
# Words and URLs
# ---------------------------------------------------------------------------

def index_key(token: str) -> str:
    """The stored form of one word: a plural 's' folded like score._stem ('treasures' -> 'treasure')."""
    tok = (token or "")[:MAX_TOKEN_LEN]
    if not is_arabic(tok) and len(tok) > 3 and tok.endswith("s") and not tok.endswith("ss"):
        return tok[:-1]
    return tok


def _indexable(key: str) -> bool:
    # sizes and single letters ('400', 'g') would make huge, useless posting lists; score.py reads sizes
    return len(key) >= 2 and not key.replace(".", "").isdigit()


def index_keys(text: Optional[str]) -> List[str]:
    """The distinct index keys of a text, in order."""
    out: List[str] = []
    for tok in tokens(text, strip_clitics=True):
        key = index_key(tok)
        if _indexable(key) and key not in out:
            out.append(key)
    return out


# A product id marker after the slug: '/<department>/<slug>/p/<id>' (Carrefour, Lulu), '/<slug>/dp/<id>'.
_ID_MARKERS = frozenset({"p", "dp"})


def slug_text(url: str) -> str:
    """The words of a product URL's own slug (no locale or department breadcrumbs): the segment before an id
    marker ('/p/<id>') when there is one, else the segment with the most words. A department name with more
    words than the product's slug ('carbonated-soft-drinks-and-mixers/pepsi-can-330ml/p/1') is never indexed
    in its place."""
    try:
        segs = [seg for seg in urlsplit((url or "").strip()).path.split("/") if seg]
    except ValueError:
        segs = []
    for i in range(1, len(segs) - 1):
        if segs[i].lower() in _ID_MARKERS and any(ch in "-_" for ch in segs[i - 1]):   # a slug, not a locale
            return url_path_text("https://slug.invalid/" + segs[i - 1])
    return url_path_text(url, product_segment=True)


def clean_url(url: str) -> str:
    """The product URL without its query string or fragment (tracking parameters are not the product)."""
    try:
        parts = urlsplit((url or "").strip())
    except ValueError:
        return (url or "").strip()
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def url_hash(url: str) -> str:
    """Dedupe key of a product URL: host without 'www.' + path without a trailing slash, lower-cased."""
    try:
        parts = urlsplit((url or "").strip())
        host = (parts.hostname or "").lower()
    except ValueError:
        return hashlib.sha1((url or "").strip().lower().encode("utf-8")).hexdigest()
    if host.startswith("www."):
        host = host[4:]
    key = host + (parts.path or "").rstrip("/").lower()
    return hashlib.sha1(key.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Rows and stores
# ---------------------------------------------------------------------------

@dataclass
class CatalogRow:
    id: int
    store: str
    url: str
    slug_text: str = ""
    page_title: str = ""
    image_url: str = ""
    image_width: Optional[int] = None
    image_height: Optional[int] = None
    gtin: Optional[str] = None
    page_status: str = ""              # '' never read | 'ok' | 'redirected' | 'no_image' | a fetch error
    page_age_h: Optional[int] = None   # hours since the page was read; None = never read
    hits: int = 0                      # index words of the query the row holds (index order)
    url_gtin: Optional[str] = None     # the barcode the store wrote in the product URL itself (catalog_match.url_gtin)


@dataclass
class PageRecord:
    """What one read of a product page said, kept in its row."""
    status: str
    page_title: str = ""
    image_url: str = ""
    width: Optional[int] = None
    height: Optional[int] = None
    gtin: Optional[str] = None


def _required_keys(phrase: str) -> List[str]:
    """The index keys a row must hold for one brand phrase: its words of 3+ letters ('al alali' -> alali)."""
    keys = index_keys(phrase)
    long_keys = [k for k in keys if len(k) >= 3]
    return long_keys or keys


class MemoryCatalogStore:
    """In-process index with the same interface as DbCatalogStore (tests, --dry-run)."""

    def __init__(self) -> None:
        self.rows: Dict[int, CatalogRow] = {}
        self._by_hash: Dict[str, int] = {}
        self._seen: Dict[int, int] = {}
        self._tokens: Dict[str, Set[int]] = {}
        self._checked: Dict[int, float] = {}
        self._generation = 0
        self.harvests: List[Dict[str, Any]] = []
        self.clock: Callable[[], float] = time.time

    # -- index ------------------------------------------------------------
    def count(self) -> int:
        return len(self.rows)

    def begin_harvest(self, store: str) -> int:
        self._generation += 1
        return self._generation

    def upsert(self, store: str, entries: Iterable[Tuple[str, Optional[str]]]) -> int:
        new = 0
        for url, _lastmod in entries:
            url = clean_url(url)
            key = url_hash(url)
            row_id = self._by_hash.get(key)
            if row_id is None:
                row_id = len(self.rows) + 1
                while row_id in self.rows:
                    row_id += 1
                self.rows[row_id] = CatalogRow(id=row_id, store=store, url=url, slug_text=slug_text(url),
                                               url_gtin=_url_gtin_of(url))
                self._by_hash[key] = row_id
                self._add_tokens(row_id, self.rows[row_id].slug_text)
                new += 1
            self._seen[row_id] = self._generation
        return new

    def prune(self, store: str, generation: int) -> int:
        gone = [i for i, r in self.rows.items() if r.store == store and self._seen.get(i, 0) < generation]
        for i in gone:
            row = self.rows.pop(i)
            self._by_hash.pop(url_hash(row.url), None)
            for ids in self._tokens.values():
                ids.discard(i)
        return len(gone)

    def finish_harvest(self, store: str, generation: int, report: Dict[str, Any]) -> None:
        self.harvests.append(dict(report, store=store))

    def _add_tokens(self, row_id: int, text: str) -> None:
        for key in index_keys(text):
            self._tokens.setdefault(key, set()).add(row_id)

    # -- search -------------------------------------------------------------
    def find(self, required: Sequence[str], extra: Sequence[str], limit: int = CANDIDATE_ROWS) -> List[CatalogRow]:
        req = list(dict.fromkeys(required))
        if not req:
            return []
        ids = set.intersection(*(self._tokens.get(k, set()) for k in req))
        keys = list(dict.fromkeys(req + list(extra)))
        hits = {i: sum(1 for k in keys if i in self._tokens.get(k, ())) for i in ids}
        order = sorted(ids, key=lambda i: (-hits[i], i))[:max(0, int(limit))]
        return [replace(self._aged(self.rows[i]), hits=hits[i]) for i in order]

    def by_gtin(self, gtin: str, limit: int = 20) -> List[CatalogRow]:
        rows = [r for r in self.rows.values() if gtin and gtin in (r.gtin, r.url_gtin)]
        return [self._aged(r) for r in sorted(rows, key=lambda r: r.id)[:limit]]

    def _aged(self, row: CatalogRow) -> CatalogRow:
        stamp = self._checked.get(row.id)
        age = None if stamp is None else int(max(0.0, self.clock() - stamp) // 3600)
        return replace(row, page_age_h=age)

    def save_page(self, row_id: int, rec: PageRecord) -> None:
        row = self.rows.get(row_id)
        if row is None:
            return
        self._checked[row_id] = self.clock()
        if rec.status not in PERMANENT_PAGE_STATUSES:     # a transient failure: what the page said before stays
            self.rows[row_id] = replace(row, page_status=rec.status)
            return
        self.rows[row_id] = replace(row, page_status=rec.status, page_title=rec.page_title or row.page_title,
                                    image_url=rec.image_url, image_width=rec.width, image_height=rec.height,
                                    gtin=rec.gtin)
        if rec.page_title:
            self._add_tokens(row_id, rec.page_title)

    def harvest_ages(self) -> Dict[str, Dict[str, Any]]:
        return {}              # the memory store keeps no harvest log

    def stats(self) -> List[Dict[str, Any]]:
        by_store: Dict[str, Dict[str, Any]] = {}
        for r in self.rows.values():
            s = by_store.setdefault(r.store, {"store": r.store, "products": 0, "pages_read": 0, "with_image": 0})
            s["products"] += 1
            s["pages_read"] += int(bool(r.page_status))
            s["with_image"] += int(r.page_status == "ok" and bool(r.image_url))
        return [by_store[k] for k in sorted(by_store)]


class DbCatalogStore:
    """The index in MariaDB (tables catalog_products, catalog_tokens, catalog_harvests; local_cache_db.init_db)."""

    BATCH = 500
    _count_cache: Dict[str, Tuple[float, int]] = {}
    _count_lock = threading.Lock()

    def __init__(self, connect: Optional[Callable[[], Any]] = None, clock: Callable[[], float] = time.monotonic) -> None:
        self._connect = connect
        self._clock = clock

    def _conn(self):
        if self._connect is not None:
            return self._connect()
        import local_cache_db  # the repo root module; catalog_match stays importable without a database
        return local_cache_db.get_db_connection()

    def _cache_key(self) -> str:
        import os
        return f"{os.getenv('DB_HOST', '')}:{os.getenv('DB_PORT', '')}/{os.getenv('DB_DATABASE', '')}"

    # -- index ------------------------------------------------------------
    def count(self, fresh: bool = False) -> int:
        """Indexed product rows (0 when the table is missing or the database is down); cached for COUNT_CACHE_S."""
        key = self._cache_key()
        now = self._clock()
        with self._count_lock:
            hit = self._count_cache.get(key)
            if hit is not None and not fresh and now - hit[0] < COUNT_CACHE_S:
                return hit[1]
        try:
            conn = self._conn()
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT COUNT(*) AS n FROM catalog_products")
                    row = cur.fetchone() or {}
                    n = int(row.get("n") or 0) if isinstance(row, dict) else int(row[0] or 0)
            finally:
                conn.close()
        except Exception as exc:
            logger.info("local index: not available (%s)", type(exc).__name__)
            n = 0
        with self._count_lock:
            self._count_cache[key] = (now, n)
        return n

    def begin_harvest(self, store: str) -> Any:
        """The database time the harvest started (rows last seen before it are gone from the store)."""
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT NOW() AS t")
                return (cur.fetchone() or {}).get("t")
        finally:
            conn.close()

    def upsert(self, store: str, entries: Iterable[Tuple[str, Optional[str]]]) -> int:
        """Insert new product URLs (with their slug words) and mark known ones seen; returns how many were new."""
        new = 0
        batch: List[Tuple[str, Optional[str]]] = []
        for entry in entries:
            batch.append(entry)
            if len(batch) >= self.BATCH:
                new += self._upsert_batch(store, batch)
                batch = []
        if batch:
            new += self._upsert_batch(store, batch)
        with self._count_lock:
            self._count_cache.clear()
        return new

    def _upsert_batch(self, store: str, batch: List[Tuple[str, Optional[str]]]) -> int:
        rows: Dict[str, Tuple[str, str, Optional[str]]] = {}
        for url, lastmod in batch:
            url = clean_url(url)
            if url:
                rows[url_hash(url)] = (url, slug_text(url), (lastmod or None) and str(lastmod)[:32], _url_gtin_of(url))
        if not rows:
            return 0
        hashes = list(rows)
        marks = ",".join(["%s"] * len(hashes))
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(f"SELECT url_hash FROM catalog_products WHERE url_hash IN ({marks})", hashes)
                known = {r["url_hash"] for r in cur.fetchall()}
                cur.executemany(
                    "INSERT INTO catalog_products (store, url, url_hash, slug_text, lastmod, url_gtin, first_seen, "
                    "last_seen) VALUES (%s, %s, %s, %s, %s, %s, NOW(), NOW()) "
                    "ON DUPLICATE KEY UPDATE store = VALUES(store), lastmod = VALUES(lastmod), "
                    "url_gtin = VALUES(url_gtin), last_seen = NOW()",
                    [(store, u, h, s[:512], m, g) for h, (u, s, m, g) in rows.items()])
                fresh = [h for h in hashes if h not in known]
                if fresh:
                    marks = ",".join(["%s"] * len(fresh))
                    cur.execute(f"SELECT id, slug_text FROM catalog_products WHERE url_hash IN ({marks})", fresh)
                    pairs = [(k, r["id"]) for r in cur.fetchall() for k in index_keys(r["slug_text"])]
                    if pairs:
                        cur.executemany("INSERT IGNORE INTO catalog_tokens (token, product_id) VALUES (%s, %s)", pairs)
            conn.commit()
        finally:
            conn.close()
        return len(fresh)

    def prune(self, store: str, started: Any) -> int:
        """Delete the store's rows its sitemaps no longer list (tokens go with them: ON DELETE CASCADE)."""
        if started is None:
            return 0
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                n = cur.execute("DELETE FROM catalog_products WHERE store = %s AND last_seen < %s", (store, started))
            conn.commit()
        finally:
            conn.close()
        with self._count_lock:
            self._count_cache.clear()
        return int(n or 0)

    def finish_harvest(self, store: str, started: Any, report: Dict[str, Any]) -> None:
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO catalog_harvests (store, started_at, finished_at, status, sitemaps_read, urls_seen, "
                    "product_urls, new_urls, pruned, error) VALUES (%s, %s, NOW(), %s, %s, %s, %s, %s, %s, %s)",
                    (store, started, str(report.get("status") or "")[:16], int(report.get("sitemaps_read") or 0),
                     int(report.get("urls_seen") or 0), int(report.get("product_urls") or 0),
                     int(report.get("new_urls") or 0), int(report.get("pruned") or 0),
                     (str(report.get("error") or "")[:250] or None)))
            conn.commit()
        finally:
            conn.close()

    def harvest_ages(self) -> Dict[str, Dict[str, Any]]:
        """Per store, from the harvest log, in the database's own clock (no time zone to get wrong):
        {ok_age_s: seconds since the newest complete harvest (status ok / empty) or None,
         last_status, last_age_s: the newest harvest of any status}. Used by catalog_match.index_refresh."""
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT store, TIMESTAMPDIFF(SECOND, MAX(finished_at), NOW()) AS age_s "
                            "FROM catalog_harvests WHERE status IN ('ok', 'empty') AND finished_at IS NOT NULL "
                            "GROUP BY store")
                ok = {r["store"]: r["age_s"] for r in cur.fetchall()}
                cur.execute("SELECT h.store, h.status, TIMESTAMPDIFF(SECOND, h.finished_at, NOW()) AS age_s "
                            "FROM catalog_harvests h JOIN (SELECT store, MAX(id) AS id FROM catalog_harvests "
                            "GROUP BY store) m ON m.id = h.id")
                last = {r["store"]: r for r in cur.fetchall()}
        finally:
            conn.close()
        out: Dict[str, Dict[str, Any]] = {}
        for store in set(ok) | set(last):
            h = last.get(store) or {}
            out[store] = {"ok_age_s": None if ok.get(store) is None else max(0, int(ok[store])),
                          "last_status": str(h.get("status") or ""),
                          "last_age_s": None if h.get("age_s") is None else max(0, int(h["age_s"]))}
        return out

    # -- search -------------------------------------------------------------
    _ROW_COLUMNS = ("p.id, p.store, p.url, p.slug_text, p.page_title, p.image_url, p.image_width, p.image_height, "
                    "p.gtin, p.url_gtin, p.page_status, TIMESTAMPDIFF(HOUR, p.page_checked_at, NOW()) AS page_age_h")

    def find(self, required: Sequence[str], extra: Sequence[str], limit: int = CANDIDATE_ROWS) -> List[CatalogRow]:
        req = list(dict.fromkeys(k[:MAX_TOKEN_LEN] for k in required if k))
        if not req:
            return []
        keys = list(dict.fromkeys(req + [k[:MAX_TOKEN_LEN] for k in extra if k]))
        # The brand's own rows first (one posting list), then the product words are counted only on them.
        sql = (
            f"SELECT {self._ROW_COLUMNS}, h.hits FROM ("
            f" SELECT product_id, COUNT(*) AS hits FROM catalog_tokens"
            f" WHERE token IN ({','.join(['%s'] * len(keys))})"
            f" AND product_id IN (SELECT product_id FROM catalog_tokens WHERE token = %s)"
            f" GROUP BY product_id HAVING SUM(token IN ({','.join(['%s'] * len(req))})) = %s"
            f" ORDER BY hits DESC, product_id LIMIT %s) h"
            f" JOIN catalog_products p ON p.id = h.product_id ORDER BY h.hits DESC, p.id")
        return self._rows(sql, keys + [req[0]] + req + [len(req), max(0, int(limit))])

    def by_gtin(self, gtin: str, limit: int = 20) -> List[CatalogRow]:
        if not gtin:
            return []
        # the barcode the page stated, or the one the store wrote in the product URL (two indexed lookups)
        sql = (f"SELECT {self._ROW_COLUMNS}, 0 AS hits FROM catalog_products p WHERE p.gtin = %s "
               f"UNION SELECT {self._ROW_COLUMNS}, 0 AS hits FROM catalog_products p WHERE p.url_gtin = %s "
               "ORDER BY id LIMIT %s")
        return self._rows(sql, [gtin, gtin, int(limit)])

    def _rows(self, sql: str, params: Sequence[Any]) -> List[CatalogRow]:
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, list(params))
                found = cur.fetchall()
        finally:
            conn.close()
        return [CatalogRow(id=int(r["id"]), store=r["store"] or "", url=r["url"] or "", slug_text=r["slug_text"] or "",
                           page_title=r["page_title"] or "", image_url=r["image_url"] or "",
                           image_width=r["image_width"], image_height=r["image_height"], gtin=r["gtin"] or None,
                           url_gtin=r.get("url_gtin") or None,
                           page_status=r["page_status"] or "",
                           page_age_h=None if r["page_age_h"] is None else int(r["page_age_h"]),
                           hits=int(r.get("hits") or 0))
                for r in found]

    def save_page(self, row_id: int, rec: PageRecord) -> None:
        if cassette.replaying():      # a replayed page read is not news for the index
            return
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                if rec.status not in PERMANENT_PAGE_STATUSES:
                    # a transient failure: the image, size and GTIN an earlier read found stay
                    cur.execute("UPDATE catalog_products SET page_checked_at = NOW(), page_status = %s WHERE id = %s",
                                (rec.status[:32], row_id))
                    conn.commit()
                    return
                cur.execute(
                    "UPDATE catalog_products SET page_checked_at = NOW(), page_status = %s, "
                    "page_title = COALESCE(NULLIF(%s, ''), page_title), image_url = NULLIF(%s, ''), "
                    "image_width = %s, image_height = %s, gtin = %s WHERE id = %s",
                    (rec.status[:32], (rec.page_title or "")[:512], rec.image_url or "", rec.width, rec.height,
                     rec.gtin, row_id))
                pairs = [(k, row_id) for k in index_keys(rec.page_title)]
                if pairs:
                    cur.executemany("INSERT IGNORE INTO catalog_tokens (token, product_id) VALUES (%s, %s)", pairs)
            conn.commit()
        finally:
            conn.close()

    def stats(self) -> List[Dict[str, Any]]:
        """Per store: indexed products, pages read, pages with an image, and the last harvest."""
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT store, COUNT(*) AS products, SUM(page_checked_at IS NOT NULL) AS pages_read, "
                    "SUM(page_status = 'ok' AND image_url IS NOT NULL) AS with_image "
                    "FROM catalog_products GROUP BY store ORDER BY store")
                out = [{"store": r["store"], "products": int(r["products"] or 0),
                        "pages_read": int(r["pages_read"] or 0), "with_image": int(r["with_image"] or 0)}
                       for r in cur.fetchall()]
                cur.execute(
                    "SELECT h.store, h.finished_at, h.status, h.product_urls FROM catalog_harvests h "
                    "JOIN (SELECT store, MAX(id) AS id FROM catalog_harvests GROUP BY store) m ON m.id = h.id")
                last = {r["store"]: r for r in cur.fetchall()}
        finally:
            conn.close()
        for s in out:
            h = last.get(s["store"])
            s["last_harvest"] = str(h["finished_at"]) if h else ""
            s["last_status"] = (h["status"] or "") if h else ""
        return out


# ---------------------------------------------------------------------------
# Choosing the rows worth reading
# ---------------------------------------------------------------------------

def search_keys(spec: SkuSpec) -> Tuple[List[List[str]], List[str]]:
    """(required keys per brand phrase, the product's other words) for CatalogStore.find()."""
    groups: List[List[str]] = []
    brand_keys: Set[str] = set()
    for phrase in spec.match_brands:
        keys = _required_keys(phrase)
        brand_keys.update(index_keys(phrase))
        if keys and keys not in groups:
            groups.append(keys)
    extra: List[str] = []
    for text in (spec.raw_name,) + tuple(spec.class_tokens):
        for key in index_keys(text):
            if key not in brand_keys and key not in UNIT_WORDS and key not in extra:
                extra.append(key)
    return groups, extra[:MAX_NAME_KEYS]


def _url_gtin_of(url: str) -> Optional[str]:
    """The barcode a known store wrote in this product URL (catalog_match.url_gtin), or None."""
    from .url_gtin import from_url
    try:
        return from_url(url, "page")
    except Exception:  # pragma: no cover - a pattern never raises; the harvest must not stop for it
        return None


def row_candidate(row: CatalogRow, rank: int = 0) -> Candidate:
    """The row as a candidate: its URL, the page title and image once the page was read."""
    return Candidate(
        image_url=row.image_url or "", page_url=row.url, page_title=row.page_title or "",
        title=row.page_title or "", domain=url_host(row.url), width=row.image_width, height=row.image_height,
        provider=PROVIDER, query_id=QUERY_ID, rank=rank, gtin_on_page=row.gtin, sanctioned=False)


def rank_rows(spec: SkuSpec, rows: Sequence[CatalogRow], max_pages: Optional[int] = None,
              gtins: Sequence[str] = ()) -> List[Tuple[CatalogRow, CandidateScore]]:
    """The rows worth reading, best first: no hard reject, brand evidence, enough product words;
    the best row of each store before a second row of any store. All of them unless max_pages is
    given (the provider walks the list and skips the rows it cannot use). A row whose own URL is the sheet's barcode,
    or one of `gtins` (lookup_gtins), is worth a read without brand evidence in its slug."""
    from .score import rank_key, score_candidate

    scored: List[Tuple[Tuple, CatalogRow, CandidateScore]] = []
    seen: Set[int] = set()
    for i, row in enumerate(rows):
        if row.id in seen:
            continue
        seen.add(row.id)
        cand = row_candidate(row, rank=i + 1)
        score = score_candidate(spec, cand)
        if score.tier is None or score.hard_reject:
            continue
        matched = score.matched or {}
        # score.py leaves a size seen only in a URL to the reviewer; here it is reason enough not to read the page
        if (score.size_status == "conflict" or matched.get("pack") == "conflict"
                or any(c.startswith(("url_size_conflict", "url_pack_conflict")) for c in score.conflicts)):
            continue
        # the store's own URL is the sheet's barcode: worth one free read (the URL is never evidence itself)
        wanted = (spec.gtin,) + tuple(gtins)
        gtin_match = matched.get("gtin") == "match" or bool(row.url_gtin and row.url_gtin in wanted)
        if not matched.get("brand") and not gtin_match:
            continue
        if not gtin_match and float(matched.get("coverage") or 0.0) < MIN_COVERAGE:
            continue
        scored.append((rank_key(cand, score), row, score))
    scored.sort(key=lambda item: item[0])
    first, rest, stores = [], [], set()
    for _, row, score in scored:
        (rest if row.store in stores else first).append((row, score))
        stores.add(row.store)
    ranked = first + rest
    return ranked if max_pages is None else ranked[:max(0, int(max_pages))]


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------

_blocked_hosts: Dict[str, int] = {}
_blocked_lock = threading.Lock()


def _note_host(host: str, status: str) -> None:
    with _blocked_lock:
        if status in HOST_FAILURE_STATUSES:
            _blocked_hosts[host] = _blocked_hosts.get(host, 0) + 1
        elif status in PERMANENT_PAGE_STATUSES:
            _blocked_hosts.pop(host, None)


def host_blocked(host: str) -> bool:
    with _blocked_lock:
        return _blocked_hosts.get(host, 0) >= BLOCKED_HOST_LIMIT


def reset_blocked_hosts() -> None:
    with _blocked_lock:
        _blocked_hosts.clear()


class LocalIndexProvider(BaseProvider):
    """A lookup provider over the local catalog index (see the module docstring)."""

    name = PROVIDER
    sanctioned = False
    kind = "lookup"
    needs_gtin = False
    lookup_query_id = QUERY_ID
    gtin_query_id = GTIN_QUERY_ID
    rate_per_min = 6000.0      # a local database: the page reads have their own per-host buckets
    burst = 100

    def __init__(self, store: Any = None, fetcher: Any = None, max_pages: Optional[int] = None,
                 page_ttl_days: Optional[int] = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.store = store if store is not None else DbCatalogStore()
        self._fetcher = fetcher
        self.max_pages = settings.local_index_max_pages() if max_pages is None else max(0, int(max_pages))
        days = settings.local_index_page_ttl_days() if page_ttl_days is None else max(0, int(page_ttl_days))
        self.page_ttl_h = days * 24

    @classmethod
    def create(cls, store: Any = None, fetcher: Any = None) -> Optional["LocalIndexProvider"]:
        """The provider when LOCAL_INDEX_ENABLED, LOCAL_INDEX_MAX_PAGES > 0 and the index has rows; else None."""
        if not settings.local_index_enabled() or settings.local_index_max_pages() <= 0:
            return None
        store = store if store is not None else DbCatalogStore()
        try:
            rows = int(store.count())
        except Exception:  # pragma: no cover - DbCatalogStore.count() never raises
            rows = 0
        return cls(store=store, fetcher=fetcher) if rows > 0 else None

    def fetcher(self):
        if self._fetcher is None:
            from .pages import PageFetcher
            self._fetcher = PageFetcher(timeout=PAGE_TIMEOUT_S)
        return self._fetcher

    def lookup(self, spec: SkuSpec) -> Any:
        """One index lookup for the SKU; never raises (BaseProvider.search)."""
        return self.search("", "", spec)

    # ------------------------------------------------------------------
    def _rows(self, spec: SkuSpec) -> List[CatalogRow]:
        rows: List[CatalogRow] = []
        if spec.gtin and is_global_gtin(spec.gtin):
            rows.extend(self.store.by_gtin(spec.gtin))
        groups, extra = search_keys(spec)
        for required in groups:
            rows.extend(self.store.find(required, extra, CANDIDATE_ROWS))
        return rows

    def _fresh(self, row: CatalogRow) -> bool:
        if not row.page_status or row.page_age_h is None:
            return False
        ttl = self.page_ttl_h if row.page_status in PERMANENT_PAGE_STATUSES else FAILED_PAGE_TTL_H
        return row.page_age_h < ttl

    def lookup_gtins(self, spec: SkuSpec, gtins: Sequence[str], known_pages: Sequence[str] = ()) -> Any:
        """The indexed pages of these barcodes (other stores' URL barcodes for a row without one; see the module
        docstring), as a ProviderResult under GTIN_QUERY_ID. Pages already in the pool are not read again. Never
        raises (BaseProvider.search)."""
        self._gtin_request = (tuple(g for g in gtins if g), tuple(known_pages or ()))
        try:
            return self.search(GTIN_QUERY_ID, "", spec)
        finally:
            self._gtin_request = None

    def _gtin_rows(self, spec: SkuSpec) -> List[CatalogRow]:
        gtins, known_pages = getattr(self, "_gtin_request", None) or ((), ())
        known = {url_hash(u) for u in known_pages if u}
        rows: List[CatalogRow] = []
        for g in gtins:
            # one snapshot per barcode (a cassette keys index rows by the spec's barcode and brand words)
            probe = replace(spec, gtin=g, match_brands=())
            rows.extend(r for r in cassette.local_index_rows(probe, lambda g=g: self.store.by_gtin(g))
                        if url_hash(r.url) not in known)
        return rows

    def _search(self, query: str, hl: str, spec: SkuSpec) -> List[Candidate]:
        if self.max_pages <= 0:
            return []
        if query == GTIN_QUERY_ID:
            ranked = rank_rows(spec, self._gtin_rows(spec), gtins=self._gtin_request[0])
            if not ranked:
                raise ProviderEmpty("no indexed page of these barcodes")
        else:
            ranked = rank_rows(spec, cassette.local_index_rows(spec, lambda: self._rows(spec)))   # snapshot / replay
            if not ranked:
                raise ProviderEmpty("no indexed page of this brand and product")
        found: Dict[int, List[Candidate]] = {}
        todo: List[Tuple[int, CatalogRow]] = []
        used = skipped = 0
        for i, (row, _score) in enumerate(ranked):
            if used >= self.max_pages:
                break
            if self._fresh(row):
                if row.page_status == "ok" and row.image_url:
                    found[i] = [row_candidate(row, rank=i + 1)]
                    used += 1
                else:
                    skipped += 1     # known dead, or failed lately: no slot
            elif host_blocked(url_host(row.url)):
                skipped += 1
                logger.info("local index: %s refused earlier reads; not asked again in this run", url_host(row.url))
            else:
                todo.append((i, row))
                used += 1
        if todo:
            for i, cands in self._read_all(todo).items():
                if cands:
                    found[i] = cands
        out = [c for i in sorted(found) for c in found[i]]
        logger.info("local index sku=%s: %d rows ranked, %d skipped, %d pages read now, %d candidates",
                    spec.sku_key, len(ranked), skipped, len(todo), len(out))
        if not out:
            raise ProviderEmpty("indexed pages gave no product image")
        return out

    def _read_all(self, todo: List[Tuple[int, CatalogRow]]) -> Dict[int, List[Candidate]]:
        """Read the pages in parallel for at most READ_DEADLINE_S; a page still loading then gives nothing now."""
        ex = ThreadPoolExecutor(max_workers=max(1, min(FETCH_WORKERS, len(todo))))
        futures = {ex.submit(self._read, i, row): i for i, row in todo}
        done, pending = wait(futures, timeout=READ_DEADLINE_S)
        done, pending = cassette.local_index_deadline(futures, dict(todo), done, pending)   # the recorded run's
        # the reads still running finish in the background and save their record for the next run
        ex.shutdown(wait=False, cancel_futures=True)
        if pending:
            logger.info("local index: %d page read(s) still running after %.0f s; not waited for",
                        len(pending), READ_DEADLINE_S)
        out: Dict[int, List[Candidate]] = {}
        for fut in done:
            try:
                out[futures[fut]] = fut.result()
            except Exception as exc:  # pragma: no cover - _read never raises
                logger.warning("local index: page read failed (%s)", type(exc).__name__)
        return out

    def _read(self, i: int, row: CatalogRow) -> List[Candidate]:
        """Read one indexed page, remember what it said, and return its candidate."""
        from .pages import page_candidates, page_gtin, page_title_of

        info = self.fetcher().fetch_page(row.url)
        cands: List[Candidate] = []
        if not info.ok:
            rec = PageRecord(status=(info.error or "error")[:32])
        elif info.redirected_from:
            rec = PageRecord(status="redirected")
        else:
            # the same rank as the row gives once the page is remembered (page_candidates counts in tens)
            cands = [replace(c, provider=PROVIDER, query_id=QUERY_ID, rank=i + 1)
                     for c in page_candidates(info, query_id=QUERY_ID)]
            first = cands[0] if cands else None
            rec = PageRecord(status="ok" if first else "no_image", page_title=page_title_of(info)[:512],
                             image_url=first.image_url if first else "", width=first.width if first else None,
                             height=first.height if first else None, gtin=page_gtin(info))
        _note_host(url_host(row.url), rec.status)
        try:
            self.store.save_page(row.id, rec)
        except Exception as exc:  # the candidate is still good when the row cannot be updated
            logger.warning("local index: page record not saved (%s)", type(exc).__name__)
        return cands
