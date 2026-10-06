"""«ماركات ناقصة»: helps the owner fill the Brands Mapping sheet instead of typing every brand by hand.

Three steps, each one behind an explicit click on the Run page (cli_bridge actions):

* suggestions(): the brands the queue's rows name that Brands Mapping has no entry for (read only, no paid call),
  with the sheet's Arabic spelling and the store spellings the search already discovered (trace
  outcome.discovered_brands) or learned (learned_brand_aliases), as synonyms to confirm;
* official_site_candidates(): from the results of ONE Serper web query (the bridge sends and records it), the first
  pages that are the brand's own site: never a retailer, marketplace, social network or stock-photo site;
* validate_brand_request(): the strict checks before ONE row per brand is appended to the sheet (google_sheets.
  add_brand_mappings). A domain the owner gave is queued in system_settings.pending_harvest_domains and indexed
  by harvest_pending() at the start of the next worker run (LOCAL_INDEX_ENABLED), the way a store of
  catalog_stores.json is, with a generic product page pattern (BRAND_SITE_PRODUCT_PATH).

Nothing here writes the sheet or calls a provider by itself.
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from collections import Counter
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .brand_index import BrandIndex, is_placeholder_brand
from .text_norm import match_key, phrase_in, tokens, url_host

logger = logging.getLogger(__name__)

SYNONYM_LIMIT = 10
SUGGESTED_SYNONYMS = 8          # leaves room for the Arabic name the page adds
MAX_BRAND_CHARS = 100
MAX_SYNONYM_CHARS = 80
MAX_BATCH = 200                 # brands one «أضف الكل» click may write
OFFICIAL_SITE_CANDIDATES = 2
OFFICIAL_SITE_SPEND_RUN = "brand-site"

# Sites that are never a brand's own site (beside the retailer and stock lists of data/trusted_domains.json).
SOCIAL_AND_MARKETPLACES = (
    "facebook.com", "instagram.com", "twitter.com", "x.com", "tiktok.com", "youtube.com", "linkedin.com",
    "pinterest.com", "snapchat.com", "reddit.com", "whatsapp.com", "t.me", "telegram.org", "wikipedia.org",
    "wikimedia.org", "tripadvisor.com", "yelp.com", "trustpilot.com", "google.com", "bing.com", "yahoo.com",
    "ebay.com", "alibaba.com", "aliexpress.com", "etsy.com", "walmart.com", "jumia.com", "dubizzle.com",
    "opensooq.com", "souq.com", "shein.com", "temu.com", "olx.com", "indiamart.com", "alibaba.ae",
)
# A retailer's own name as the registrable label ('carrefour' for carrefour.com, 'carrefouruae' for carrefouruae.com).
_MIN_RETAILER_LABEL = 5
_RETAILER_SUFFIXES = ("", "uae", "ae", "ksa", "sa", "qatar", "kuwait", "mart", "online", "shop", "store")

_DOMAIN_RE = re.compile(r"^(?=.{4,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+(?:[a-z]{2,63}|xn--[a-z0-9-]{2,59})$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f  ]")
_SPLIT_RE = re.compile(r"[,،;\n\r]+")
_STOP_TOKENS = {"al", "el", "the", "and", "of", "for", "co", "company", "trading", "llc", "ltd", "food", "foods",
                "brand", "brands", "group", "official"}
_SECOND_LEVEL = {"co", "com", "org", "net", "gov", "ac", "edu"}

# Product pages of a brand's own site are not known: the usual shop paths (Shopify, WooCommerce, ...). A site whose
# pages look different indexes nothing and the harvest report says so; the owner can add it to catalog_stores.json.
BRAND_SITE_PRODUCT_PATH = r"/(?:products?|items?|shop|store|p)/[^/?#]+/?$"
HARVEST_MAX_URLS = 3000
HARVEST_MAX_SITEMAPS = 10
HARVEST_MAX_DOMAINS = 3
HARVEST_BUDGET_S = 120.0
FINAL_HARVEST_STATUSES = ("ok", "partial", "empty", "blocked")     # 'error' is tried again at the next run


class BrandRequestError(ValueError):
    """A request the owner's click cannot be served: .code (invalid_brand, invalid_synonym, too_many_synonyms,
    invalid_domain, blocked_domain, too_many_brands) and .field say what to fix."""

    def __init__(self, code: str, field: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.field = field


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------

def clean_brand(value: Any) -> str:
    """The brand cell to write: one line, 1..MAX_BRAND_CHARS characters, not a 'no brand' placeholder."""
    text = " ".join(str(value or "").split())
    if (not text or len(text) > MAX_BRAND_CHARS or _CONTROL_RE.search(text)
            or not any(ch.isalnum() for ch in text) or is_placeholder_brand(text)):
        raise BrandRequestError("invalid_brand", "brand", "the brand is empty, too long or only a placeholder")
    return text


def _as_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [p for p in _SPLIT_RE.split(value)]
    if isinstance(value, (list, tuple, set)):
        return [str(v) if not isinstance(v, str) else v for v in value]
    return [str(value)]


def clean_synonyms(value: Any, brand: str = "") -> List[str]:
    """At most SYNONYM_LIMIT distinct spellings (case-insensitive), the brand itself dropped. A spelling with a comma, a
    control character or more than MAX_SYNONYM_CHARS characters is refused (it would become two cells' worth)."""
    out: List[str] = []
    seen = {match_key(brand)} if brand else set()
    for raw in _as_list(value):
        text = " ".join(str(raw).split())
        if not text:
            continue
        if len(text) > MAX_SYNONYM_CHARS or _CONTROL_RE.search(str(raw)) or re.search(r"[,،;]", text):
            raise BrandRequestError("invalid_synonym", "synonyms", "a synonym is too long or has a separator in it")
        key = match_key(text) or text.casefold()
        if key not in seen:
            seen.add(key)
            out.append(text)
    if len(out) > SYNONYM_LIMIT:
        raise BrandRequestError("too_many_synonyms", "synonyms", f"at most {SYNONYM_LIMIT} synonyms")
    return out


def clean_domain(value: Any) -> str:
    """The bare lower-case host ('almarai.com'), or '' when it is no bare host (a scheme, a path, a port, spaces)."""
    text = str(value or "").strip().lower()
    return text if _DOMAIN_RE.match(text) else ""


def _registrable_label(host: str) -> str:
    labels = [p for p in host.split(".") if p]
    if len(labels) >= 3 and labels[-2] in _SECOND_LEVEL:
        return labels[-3]
    return labels[-2] if len(labels) >= 2 else (labels[0] if labels else "")


def _under(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


def blocked_site(host: str) -> bool:
    """True for a host that is a retailer, marketplace, social network, search engine or stock-photo site: never a
    brand's official site."""
    from .score import trusted_domains

    host = url_host(host if "//" in host else "//" + host) or host.lower()
    data = trusted_domains()
    listed = [d for key in ("uae_retailers", "other_retail", "stock_or_clipart", "structured") for d in data.get(key, [])]
    if any(_under(host, str(d).lower()) for d in list(listed) + list(SOCIAL_AND_MARKETPLACES)):
        return True
    label = re.sub(r"[^a-z0-9]", "", _registrable_label(host))
    names = {re.sub(r"[^a-z0-9]", "", str(n).lower()) for n in data.get("retailer_names", [])}
    return len(label) >= _MIN_RETAILER_LABEL and any(
        len(n) >= _MIN_RETAILER_LABEL and label == n + suffix for n in names for suffix in _RETAILER_SUFFIXES)


def clean_domains(value: Any) -> List[str]:
    out: List[str] = []
    for raw in _as_list(value):
        text = str(raw).strip()
        if not text:
            continue
        host = clean_domain(text)
        if not host:
            raise BrandRequestError("invalid_domain", "official_domains", "an official site must be a bare host")
        if blocked_site(host):
            raise BrandRequestError("blocked_domain", "official_domains", "a store or social site is not a brand's site")
        if host not in out:
            out.append(host)
    return out


def validate_brand_request(item: Mapping[str, Any]) -> Dict[str, Any]:
    """{brand, synonyms, official_domains} cleaned from one request; BrandRequestError for anything wrong."""
    brand = clean_brand(item.get("brand"))
    return {"brand": brand, "synonyms": clean_synonyms(item.get("synonyms"), brand),
            "official_domains": clean_domains(item.get("official_domains"))}


# ---------------------------------------------------------------------------
# suggestions: the brands without a Brands Mapping entry
# ---------------------------------------------------------------------------

def _compact(text: str) -> str:
    return re.sub(r"\s+", "", match_key(text))


def suggestions(queue_rows: Iterable[Mapping[str, Any]], mappings: Optional[Mapping[str, Any]],
                aliases: Iterable[Sequence[Any]] = ()) -> List[Dict[str, Any]]:
    """
    [{brand, rows, brand_ar, synonyms, official_domain: ''}] for every sheet brand the queue's rows name that the
    Brands Mapping sheet does not map (a row whose name starts with a mapped brand is mapped, as the search reads it),
    most rows first. A 'no brand' placeholder and an empty cell are no brand. synonyms: the store spellings the search
    discovered for the brand (most rows first) and the ones learned from review (learned_brand_aliases rows
    (sheet_brand, alias, ...)), minus the brand and its Arabic name. mappings: the sheet's own, not what review taught.
    """
    index = BrandIndex.from_mappings(mappings or {})
    groups: Dict[str, Dict[str, Any]] = {}
    for row in queue_rows or ():
        brand = str(row.get("brand") or "").strip() or str(row.get("brand_ar") or "").strip()
        if not brand or is_placeholder_brand(brand):
            continue
        key = _compact(brand)
        if not key:
            continue
        group = groups.get(key)
        if group is None:
            if index.resolve(brand, row.get("name"), row.get("name_ar")).conf == "mapped":
                groups[key] = {"mapped": True}
                continue
            group = groups.setdefault(key, {"mapped": False, "rows": 0, "spellings": Counter(), "ar": Counter(),
                                            "found": Counter()})
        if group.get("mapped"):
            continue
        group["rows"] += 1
        group["spellings"][brand] += 1
        ar = str(row.get("brand_ar") or "").strip()
        if ar and _compact(ar) != key and not is_placeholder_brand(ar):
            group["ar"][ar] += 1
        for found in row.get("discovered") or ():
            found = str(found or "").strip()
            if found:
                group["found"][found] += 1
    learned: Dict[str, List[str]] = {}
    for alias_row in aliases or ():
        sheet_brand, alias = str(alias_row[0] or ""), str(alias_row[1] or "").strip()
        if alias:
            learned.setdefault(_compact(sheet_brand), []).append(alias)
    out: List[Dict[str, Any]] = []
    for key, group in groups.items():
        if group.get("mapped"):
            continue
        brand = group["spellings"].most_common(1)[0][0]
        brand_ar = group["ar"].most_common(1)[0][0] if group["ar"] else ""
        if not brand_ar and any("؀" <= ch <= "ۿ" for ch in brand):
            brand_ar = brand
        spellings: List[str] = []
        seen = {key, _compact(brand_ar)} if brand_ar else {key}
        for spelling in [s for s, _n in group["found"].most_common()] + learned.get(key, []):
            k = _compact(spelling)
            if k and k not in seen:
                seen.add(k)
                spellings.append(spelling)
        out.append({"brand": brand, "rows": group["rows"], "brand_ar": brand_ar if brand_ar != brand else "",
                    "synonyms": spellings[:SUGGESTED_SYNONYMS], "official_domain": ""})
    out.sort(key=lambda b: (-b["rows"], match_key(b["brand"])))
    return out


# ---------------------------------------------------------------------------
# the official site: from ONE web search's results
# ---------------------------------------------------------------------------

def search_query(brand: str) -> str:
    return f'"{brand}" official website'


def main_token(brand: str) -> str:
    """The word of the brand a site is most likely named after: its first word that is not 'al', 'the', 'foods'...
    (3+ letters or digits)."""
    toks = [t for t in tokens(brand) if len(t) >= 3 and t not in _STOP_TOKENS]
    return toks[0] if toks else ""


def official_site_candidates(results: Iterable[Mapping[str, Any]], brand: str,
                             limit: int = OFFICIAL_SITE_CANDIDATES) -> List[Dict[str, str]]:
    """
    Up to `limit` {title, domain, url} from a web search's organic results (each {link|url|page_url, title}): a result
    whose host is not a retailer, marketplace, social network or stock site (blocked_site) and whose host or page title
    holds the brand's main token (a whole word in the title; in the host as the start of its registrable name, the whole
    brand, or a long token). One result per domain. Hosts that carry the brand come first, then the search's order.
    """
    main = main_token(brand)
    if not main:
        return []
    brand_compact = _compact(brand)
    scored: List[Tuple[int, int, Dict[str, str]]] = []
    seen = set()
    for position, item in enumerate(results or ()):
        link = str(item.get("link") or item.get("url") or item.get("page_url") or "").strip()
        host = url_host(link)
        if not host or host in seen or not clean_domain(host) or blocked_site(host):
            continue
        title = " ".join(str(item.get("title") or "").split())
        label = re.sub(r"[^a-z0-9]", "", _registrable_label(host))
        host_hit = bool(label) and (brand_compact and brand_compact in label or label.startswith(main)
                                    or (len(main) >= 5 and main in label))
        title_hit = phrase_in(main, title)
        if not (host_hit or title_hit):
            continue
        seen.add(host)
        scored.append((0 if host_hit else 1, position, {"title": title[:200], "domain": host, "url": link[:500]}))
    scored.sort(key=lambda s: (s[0], s[1]))
    return [c for _rank, _pos, c in scored[:max(0, int(limit))]]


# ---------------------------------------------------------------------------
# indexing a brand's site at the next worker run
# ---------------------------------------------------------------------------

def store_key(domain: str) -> str:
    """The catalog_products.store key of a brand site (VARCHAR(32)): 'b:<domain>', shortened with a hash when long."""
    key = "b:" + domain
    if len(key) <= 32:
        return key
    return "b:" + domain[:20] + "~" + hashlib.sha1(domain.encode("utf-8")).hexdigest()[:8]


def store_for_domain(domain: str):
    """The StoreConfig the harvester reads for a brand's official site (catalog_match.sitemaps)."""
    from .sitemaps import StoreConfig

    bare = domain[4:] if domain.startswith("www.") else domain
    return StoreConfig(key=store_key(bare), name=bare, base_url="https://" + bare, hosts=(bare, "www." + bare),
                       product_path=re.compile(BRAND_SITE_PRODUCT_PATH), enabled=True,
                       note="official site added from the Brands Mapping assistant (Run page)")


def harvest_pending(max_domains: int = HARVEST_MAX_DOMAINS, budget_s: float = HARVEST_BUDGET_S, harvester=None,
                    db=None, clock=time.monotonic, pending=None, finish=None) -> List[Dict[str, Any]]:
    """
    Indexes the sites queued by «أضف» (system_settings.pending_harvest_domains) with the sitemap harvester, as a
    store of catalog_stores.json is: robots.txt and Crawl-delay kept, a blocked site skipped, at most max_domains sites
    and budget_s seconds per call (the rest stay queued). A site that was read (even to nothing) or refused leaves the
    queue; one that failed to answer stays for the next run. Never raises. Returns [{domain, store, status, urls}].
    pending / finish: the queue's reader and remover (tests); the local database by default.
    """
    import local_cache_db
    from . import sitemaps

    results: List[Dict[str, Any]] = []
    try:
        domains = list(pending() if pending else local_cache_db.pending_harvest_domains())
    except Exception as exc:  # noqa: BLE001 - a database that cannot be read must not stop the run
        logger.warning("brand sites: the queue could not be read (%s)", type(exc).__name__)
        return results
    if not domains:
        return results
    started = clock()
    try:
        if db is None:
            from .local_index import DbCatalogStore
            db = DbCatalogStore()
        harvester = harvester or sitemaps.SitemapHarvester()
    except Exception as exc:  # noqa: BLE001
        logger.warning("brand sites: the index is not available (%s)", type(exc).__name__)
        return results
    done: List[str] = []
    for domain in domains[:max(0, int(max_domains))]:
        if clock() - started > budget_s:
            break
        store = store_for_domain(domain)
        try:
            began = db.begin_harvest(store.key)
            report = harvester.harvest(store, on_urls=lambda batch, key=store.key: db.upsert(key, batch),
                                       max_urls=HARVEST_MAX_URLS, max_sitemaps=HARVEST_MAX_SITEMAPS)
            if report.status != "outside_visit_time":      # robots.txt Visit-time: not read now, stays queued
                db.finish_harvest(store.key, began, report.as_dict())
            status, urls = report.status, report.product_urls
        except Exception as exc:  # noqa: BLE001 - one site's failure must not stop the next one
            logger.warning("brand sites: %s could not be indexed (%s)", domain, type(exc).__name__)
            status, urls = "error", 0
        results.append({"domain": domain, "store": store.key, "status": status, "urls": urls})
        if status in FINAL_HARVEST_STATUSES:
            done.append(domain)
    if done:
        try:
            (finish or local_cache_db.remove_pending_harvest_domains)(done)
        except Exception as exc:  # noqa: BLE001
            logger.warning("brand sites: the queue could not be updated (%s)", type(exc).__name__)
    return results
