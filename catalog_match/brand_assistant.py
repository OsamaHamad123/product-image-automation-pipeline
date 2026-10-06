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
* bulk_suggestions(): «عبّي جدول الماركات», one proposed Brands Mapping row for EVERY missing brand at once, built
  from evidence only (approved reviews and the spellings they taught, the local index's product pages named after
  the brand and their stores, the cached official site of an earlier search, the store spellings the search saw),
  with a confidence and each piece of evidence in plain Levantine: 'high' (two kinds of evidence, or two approved
  images) is pre-ticked, 'low' (one kind) needs the owner's own tick, 'none' («ما لقينا دليل كافي») cannot be
  ticked. The bridge writes only what this function proposes for a ticked brand, never what the page sends.

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


def brand_key(brand: str) -> str:
    """The key a brand is grouped, cached (brand_sites) and logged (brand_writes) under: its match key, no spaces."""
    return _compact(brand)[:191]


def _missing_groups(queue_rows: Iterable[Mapping[str, Any]], mappings: Optional[Mapping[str, Any]]
                    ) -> List[Dict[str, Any]]:
    """The sheet brands the rows name that Brands Mapping does not map: [{key, rows, spellings, ar, found}] (Counters
    of the brand cell's spellings, its Arabic names and the store spellings the search discovered)."""
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
    return [dict(group, key=key) for key, group in groups.items() if not group.get("mapped")]


def _learned_by_key(aliases: Iterable[Sequence[Any]]) -> Dict[str, List[Tuple[str, int]]]:
    """learned_brand_aliases rows (sheet_brand, alias[, approvals, ...]) as {brand key: [(alias, approvals)]}."""
    learned: Dict[str, List[Tuple[str, int]]] = {}
    for alias_row in aliases or ():
        sheet_brand, alias = str(alias_row[0] or ""), str(alias_row[1] or "").strip()
        approvals = int(alias_row[2] or 0) if len(alias_row) > 2 and str(alias_row[2] or "").strip().isdigit() else 0
        if alias:
            learned.setdefault(_compact(sheet_brand), []).append((alias, approvals))
    return learned


def _group_view(group: Mapping[str, Any], learned: Sequence[str]) -> Dict[str, Any]:
    key = group["key"]
    brand = group["spellings"].most_common(1)[0][0]
    brand_ar = group["ar"].most_common(1)[0][0] if group["ar"] else ""
    if not brand_ar and any("؀" <= ch <= "ۿ" for ch in brand):
        brand_ar = brand
    spellings: List[str] = []
    seen = {key, _compact(brand_ar)} if brand_ar else {key}
    for spelling in [s for s, _n in group["found"].most_common()] + list(learned):
        k = _compact(spelling)
        if k and k not in seen:
            seen.add(k)
            spellings.append(spelling)
    return {"brand": brand, "rows": group["rows"], "brand_ar": brand_ar if brand_ar != brand else "",
            "synonyms": spellings[:SUGGESTED_SYNONYMS], "official_domain": ""}


def suggestions(queue_rows: Iterable[Mapping[str, Any]], mappings: Optional[Mapping[str, Any]],
                aliases: Iterable[Sequence[Any]] = ()) -> List[Dict[str, Any]]:
    """
    [{brand, rows, brand_ar, synonyms, official_domain: ''}] for every sheet brand the queue's rows name that the
    Brands Mapping sheet does not map (a row whose name starts with a mapped brand is mapped, as the search reads it),
    most rows first. A 'no brand' placeholder and an empty cell are no brand. synonyms: the store spellings the search
    discovered for the brand (most rows first) and the ones learned from review (learned_brand_aliases rows
    (sheet_brand, alias, ...)), minus the brand and its Arabic name. mappings: the sheet's own, not what review taught.
    """
    learned = _learned_by_key(aliases)
    out = [_group_view(group, [a for a, _n in learned.get(group["key"], [])])
           for group in _missing_groups(queue_rows, mappings)]
    out.sort(key=lambda b: (-b["rows"], match_key(b["brand"])))
    return out


# ---------------------------------------------------------------------------
# «عبّي جدول الماركات»: one suggestion for every missing brand, from evidence only
# ---------------------------------------------------------------------------

HIGH, LOW, NONE = "high", "low", "none"
CONFIDENCE_ORDER = {HIGH: 0, LOW: 1, NONE: 2}
NO_EVIDENCE_TEXT = "ما لقينا دليل كافي"
BULK_SITE_LOOKUPS = 10          # official-site searches one «دوّر عالمواقع الرسمية» click may send (one query each)
INDEX_MIN_PRODUCTS = 2          # product pages of the local index named after the brand that count as evidence
INDEX_ROWS = 60                 # index rows read per brand
APPROVALS_HIGH = 2              # approved images of the brand that make a suggestion 'high' on their own
EVIDENCE_LIST = 3               # names shown in one evidence sentence


def _ar_count(n: int, one: str, two: str, few: str, many: str) -> str:
    """'صورة وحدة' / 'صورتين' / '3 صور' / '11 صورة' (Levantine number agreement)."""
    if n == 1:
        return one
    if n == 2:
        return two
    return f"{n} {few if 3 <= n <= 10 else many}"


def _listing(names: Sequence[str]) -> str:
    shown = [str(n) for n in names[:EVIDENCE_LIST]]
    more = len(names) - len(shown)
    return "، ".join(shown) + (f" و{more} غيرها" if more > 0 else "")


def host_carries_brand(host: str, brand: str) -> bool:
    """True when the host's registrable name starts with the brand's main word, holds the whole brand, or holds a
    long (5+ letters) main word: 'almarai.com' for Almarai. The same test official_site_candidates ranks first."""
    main = main_token(brand)
    label = re.sub(r"[^a-z0-9]", "", _registrable_label(host or ""))
    if not main or not label:
        return False
    brand_compact = _compact(brand)
    return bool(brand_compact and brand_compact in label) or label.startswith(main) or (len(main) >= 5 and main in label)


def site_for_cache(candidates: Sequence[Mapping[str, Any]], brand: str) -> str:
    """The official site a search's candidates (official_site_candidates) give for the cache: the first one whose
    host carries the brand (never a page that only names it in its title), else ''."""
    for c in candidates or ():
        domain = clean_domain(c.get("domain"))
        if domain and not blocked_site(domain) and host_carries_brand(domain, brand):
            return domain
    return ""


def index_evidence(brand: str, find, limit: int = INDEX_ROWS) -> Dict[str, Any]:
    """
    {products, stores} of the local index's product pages named after the brand: its whole name on token boundaries
    in the page's slug words or its title (text_norm.phrase_in), read through find(required_keys, extra, limit) (the
    local_index store's own lookup: rows holding every 3+ letter word of the brand). stores: the pages' hosts, most
    pages first. Errors are raised (the caller treats them as 'index not available').
    """
    from .local_index import index_keys

    keys = index_keys(brand)
    required = [k for k in keys if len(k) >= 3] or keys
    if not required:
        return {"products": 0, "stores": []}
    hits = [r for r in find(required, [], limit) or ()
            if phrase_in(brand, getattr(r, "slug_text", "")) or phrase_in(brand, getattr(r, "page_title", ""))]
    stores = Counter(url_host(getattr(r, "url", "")) or str(getattr(r, "store", "")) for r in hits)
    return {"products": len(hits), "stores": [h for h, _n in stores.most_common() if h]}


def _approvals_by_key(approvals: Iterable[Sequence[Any]]) -> Dict[str, Dict[str, Any]]:
    """local_cache_db.get_brand_source_counts rows (brand, domain, approvals, identity_rejections) per brand key:
    {n, domains: Counter}."""
    out: Dict[str, Dict[str, Any]] = {}
    for row in approvals or ():
        brand, domain = str(row[0] or ""), str(row[1] or "").strip().lower()
        n = int(row[2] or 0)
        key = _compact(brand)
        if not key or n <= 0:
            continue
        entry = out.setdefault(key, {"n": 0, "domains": Counter()})
        entry["n"] += n
        if domain:
            entry["domains"][domain] += n
    return out


def bulk_suggestions(queue_rows: Iterable[Mapping[str, Any]], mappings: Optional[Mapping[str, Any]],
                     aliases: Iterable[Sequence[Any]] = (), approvals: Iterable[Sequence[Any]] = (),
                     index_lookup=None, sites: Optional[Mapping[str, Mapping[str, Any]]] = None
                     ) -> List[Dict[str, Any]]:
    """
    One proposed Brands Mapping row for every brand the rows name that the sheet's mapping does not know (the same
    brands as suggestions()), each {brand, rows, brand_ar, synonyms, official_domain, confidence, evidence: [{kind,
    text}], selectable, checked, site_searched}, from evidence only:

    * reviews:   images of the brand reviewers approved (approvals: get_brand_source_counts rows) and the spellings
                 approvals taught (aliases: learned_brand_aliases rows);
    * index:     the local index's product pages named after the brand and their stores (index_lookup(brand) ->
                 index_evidence's {products, stores}; None or an error = the index says nothing);
    * site:      the official site an earlier search found (sites: {brand key: {domain}} from the cache; a site is
                 used only when its host carries the brand, host_carries_brand), never a new search here;
    * spellings: the store spellings the search discovered for the brand's rows.

    confidence: 'high' with two kinds of evidence, or APPROVALS_HIGH approved images (pre-ticked); 'low' with one
    kind (the owner ticks it); 'none' without any (shown with «ما لقينا دليل كافي», never selectable). The brand is
    the sheet's own cell; synonyms are the sheet's Arabic name, the store spellings and the learned ones (each in the
    evidence); official_domain is the cached site or ''. High first, then most rows.
    """
    learned = _learned_by_key(aliases)
    approved = _approvals_by_key(approvals)
    out: List[Dict[str, Any]] = []
    for group in _missing_groups(queue_rows, mappings):
        key = group["key"]
        taught = learned.get(key, [])
        view = _group_view(group, [a for a, _n in taught])
        brand = view["brand"]
        evidence: List[Dict[str, str]] = []
        kinds = set()
        seen = approved.get(key)
        n_approved = seen["n"] if seen else 0
        if n_approved:
            kinds.add("reviews")
            where = [d for d, _n in seen["domains"].most_common()]
            evidence.append({"kind": "reviews", "text": "اعتمدت " + _ar_count(n_approved, "صورة وحدة", "صورتين", "صور", "صورة")
                             + " لهالماركة بالمراجعة" + (f" (من {_listing(where)})" if where else "") + "."})
        spellings_taught = [a for a, _n in taught if _compact(a) != key]
        if spellings_taught:
            kinds.add("reviews")
            evidence.append({"kind": "aliases", "text": "المراجعة علّمتنا إنها بتنكتب كمان: " + _listing(spellings_taught) + "."})
        idx = None
        if index_lookup is not None:
            try:
                idx = index_lookup(brand)
            except Exception as exc:  # noqa: BLE001 - an index that cannot be read says nothing
                logger.warning("brand bulk: the local index could not be read for one brand (%s)", type(exc).__name__)
                idx = None
        products = int((idx or {}).get("products") or 0)
        if products:
            stores = list((idx or {}).get("stores") or [])
            if products >= INDEX_MIN_PRODUCTS:
                kinds.add("index")
            evidence.append({"kind": "index", "text": "لقينا " + _ar_count(products, "منتج واحد", "منتجين", "منتجات", "منتج")
                             + " باسمها بفهرس المتاجر" + (f" ({_listing(stores)})" if stores else "") + "."})
        site = (sites or {}).get(key)
        domain = clean_domain((site or {}).get("domain"))
        if domain and (blocked_site(domain) or not host_carries_brand(domain, brand)):
            domain = ""
        if domain:
            kinds.add("site")
            evidence.append({"kind": "site", "text": f"موقعها الرسمي: {domain}."})
        found = [s for s, _n in group["found"].most_common() if _compact(s) != key]
        if found:
            kinds.add("spellings")
            evidence.append({"kind": "spellings", "text": "البحث لقاها بالمتاجر مكتوبة: " + _listing(found) + "."})
        confidence = HIGH if (n_approved >= APPROVALS_HIGH or len(kinds) >= 2) else (LOW if kinds else NONE)
        if confidence == NONE:
            evidence = [{"kind": "none", "text": NO_EVIDENCE_TEXT}]
        out.append(dict(view, official_domain=domain, confidence=confidence, evidence=evidence,
                        selectable=confidence != NONE, checked=confidence == HIGH, site_searched=site is not None))
    out.sort(key=lambda b: (CONFIDENCE_ORDER[b["confidence"]], -b["rows"], match_key(b["brand"])))
    return out


def bulk_items(proposals: Sequence[Mapping[str, Any]], requested: Iterable[Any]
               ) -> Tuple[List[Dict[str, Any]], List[Dict[str, str]]]:
    """
    (items to write, skipped) for the brands the owner ticked (requested: brand names as the page shows them): each
    item is the proposal's own {brand, synonyms, official_domains} (its Arabic name first), validated like «أضف»;
    never what the page sends. skipped [{brand, reason}]: 'gone' (mapped meanwhile, or no longer in the sheet),
    'no_evidence' (confidence none), 'invalid' (the proposal fails the checks), 'repeated'.
    """
    by_key = {_compact(p["brand"]): p for p in proposals or ()}
    items: List[Dict[str, Any]] = []
    skipped: List[Dict[str, str]] = []
    done = set()
    for raw in requested or ():
        if not isinstance(raw, str):
            continue
        name = " ".join(raw.split())
        key = _compact(name)
        if not key:
            continue
        if key in done:
            skipped.append({"brand": name, "reason": "repeated"})
            continue
        done.add(key)
        proposal = by_key.get(key)
        if proposal is None:
            skipped.append({"brand": name, "reason": "gone"})
            continue
        if not proposal.get("selectable"):
            skipped.append({"brand": proposal["brand"], "reason": "no_evidence"})
            continue
        synonyms = ([proposal["brand_ar"]] if proposal.get("brand_ar") else []) + list(proposal.get("synonyms") or [])
        try:
            item = validate_brand_request({"brand": proposal["brand"], "synonyms": synonyms[:SYNONYM_LIMIT],
                                           "official_domains": [proposal["official_domain"]]
                                           if proposal.get("official_domain") else []})
        except BrandRequestError:
            skipped.append({"brand": proposal["brand"], "reason": "invalid"})
            continue
        items.append(item)
    return items, skipped


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
    scored: List[Tuple[int, int, Dict[str, str]]] = []
    seen = set()
    for position, item in enumerate(results or ()):
        link = str(item.get("link") or item.get("url") or item.get("page_url") or "").strip()
        host = url_host(link)
        if not host or host in seen or not clean_domain(host) or blocked_site(host):
            continue
        title = " ".join(str(item.get("title") or "").split())
        host_hit = host_carries_brand(host, brand)
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


def _not_public(store, sitemaps):
    """A 'blocked' HarvestReport when the site's name resolves to this server or a private network (net_guard): it is
    never read and leaves the queue. None otherwise; a name that does not resolve now is left to the harvester (it
    fails to answer and the site stays queued)."""
    import net_guard

    try:
        net_guard.assert_public_url(store.base_url)
    except net_guard.BlockedURL as exc:
        logger.warning("brand sites: %s refused (%s)", store.name, exc)
        return sitemaps.HarvestReport(store=store.key, status="blocked",
                                      error="the site's address is not on the public internet: never read")
    except Exception:  # noqa: BLE001 - HostLookupFailed and the like: the harvester's own request reports it
        pass
    return None


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
            report = _not_public(store, sitemaps) or harvester.harvest(
                store, on_urls=lambda batch, key=store.key: db.upsert(key, batch),
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
