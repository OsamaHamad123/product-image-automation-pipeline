"""Learning from review decisions: what the reviewers confirm becomes search knowledge for the next products.

Two lessons, both kept in the local database (local_cache_db), neither ever an auto-publish:

  * A store spelling of a sheet brand. brand_discovery marks a pick whose brand only the stores'
    spelling supports with the review warning 'brand_spelling:<spelling>' ('Rio Mare' for the sheet's
    'RIO MARIE'). Approving that image records the spelling (cli_bridge, record_brand_alias); a
    WRONG_BRAND rejection of it counts against it. While approvals outnumber those rejections, the
    sheet brand resolves to the spelling (brand_conf 'learned'): queries write it from the first
    query, scoring and the label reader accept it, and the warning is no longer needed.
  * A brand's sources. A site the reviewers approved images of at least MIN_SOURCE_APPROVALS
    different products of a brand from, with no identity rejection of an image from it for that
    brand, gets UAE-retailer trust for the brand ('reviewed_source', score.source_trust) and a
    place in its site: query. The review rows are counted per brand as the search resolves it
    (two sheet spellings of one mapped brand count together, and a rejection under either one
    counts). Only a site the search does not already know is ever learned: never a social
    network, a stock-photo site, the brand's official site, a listed UAE retailer or structured
    source (their trust is already set, and a learned label would block their auto-publish) or a
    store outside the UAE.

apply(mappings, aliases, sources) merges both into the Brands Mapping dict (brand_index): the
sheet's own entries always win (a phrase the sheet maps keeps its entry), a learned spelling is
an entry flagged 'learned', and is never added when the sheet maps the sheet brand or the
spelling (the sheet's owner decides those). A mapped brand or a learned spelling gains
learned_domains; an unmapped sheet brand's sites go in an entry flagged 'sources_only', which
gives the brand no identity: it still resolves as 'sheet_raw' and brand discovery still runs
for it. decide.py blocks auto-publish for brand_conf 'learned' and for a pick whose trust is
only 'reviewed_source', and keeps the 'brand_spelling' warning on a pick whose brand evidence is
only a learned spelling, so a WRONG_BRAND rejection can still count against it.

A rejection the reviewer took back (the review screen's «تراجع عن الرفض»: review_decisions.undone_at, with the
spelling it counted against in learned_alias) counts for neither lesson: local_cache_db.undo_rejection gives the
spelling its rejection back and get_brand_source_counts skips the row.

load_and_apply(mappings) reads both lessons (cached LEARN_CACHE_S per process) and never raises:
without a database the mappings come back unchanged.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .text_norm import match_key, url_host

logger = logging.getLogger(__name__)

SPELLING_CODE = "brand_spelling"
MIN_SOURCE_APPROVALS = 2
LEARN_CACHE_S = 60.0
MAX_SOURCES_PER_BRAND = 3

_cache: Dict[str, Tuple[float, Any]] = {}
_cache_lock = threading.Lock()


def spelling_from(warnings: Any) -> Optional[str]:
    """The store spelling a pick's 'brand_spelling:<spelling>' warning names ('warn:' prefix or not)."""
    if isinstance(warnings, str):
        warnings = [w for w in warnings.replace("|", ",").split(",")] if "brand_spelling" in warnings else []
    for w in warnings or ():
        text = str(w).strip()
        if text.startswith("warn:"):
            text = text[len("warn:"):]
        if text.startswith(SPELLING_CODE + ":"):
            spelling = text.split(":", 1)[1].strip()
            if spelling:
                return spelling[:255]
    return None


def _usable_source(domain: str, official: Iterable[str] = ()) -> str:
    """The host of a reviewed site the search may learn; '' for one whose trust is already set or never given."""
    from .decide import _social_host, foreign_host
    from .score import trusted_domains
    from .text_norm import domain_matches

    host = url_host(domain) or str(domain or "").strip().lower()
    if not host or "." not in host or _social_host(host) or domain_matches(host, list(official)):
        return ""
    data = trusted_domains()
    for listed in ("stock_or_clipart", "uae_retailers", "structured"):
        if domain_matches(host, data.get(listed, [])):
            return ""
    return "" if foreign_host(host) else host


def _source_rows(sources: Any) -> List[Tuple[str, str, int, int]]:
    """(sheet brand, site, approved products, identity rejections) rows; a {brand: [sites]} dict is already vetted."""
    if isinstance(sources, Mapping):
        return [(b, d, MIN_SOURCE_APPROVALS, 0) for b, ds in sources.items() for d in ds or ()]
    rows = []
    for row in sources or ():
        brand, domain, approvals, rejections = (list(row) + [0, 0])[:4]
        rows.append((str(brand or "").strip(), str(domain or "").strip(), int(approvals or 0), int(rejections or 0)))
    return rows


def apply(mappings: Optional[Mapping[str, Any]], aliases: Iterable[Sequence[Any]] = (),
          sources: Any = ()) -> Dict[str, Any]:
    """The Brands Mapping dict with what the reviewers taught (see the module docstring).

    sources: (sheet brand, site, approved products, identity rejections) rows, as
    local_cache_db.get_brand_source_counts returns them (a {brand: [sites]} dict counts as vetted).
    """
    from .brand_index import BrandIndex

    out: Dict[str, Any] = {k: (dict(v) if isinstance(v, Mapping) else v) for k, v in (mappings or {}).items()}
    index = BrandIndex.from_mappings(mappings or {})
    learned: Dict[str, Dict[str, Any]] = {}

    def learned_key(name: str) -> str:
        # the sheet's convention (google_sheets.parse_brand_mapping_rows); a sheet entry with the key wins
        return name.strip().lower()

    for item in aliases or ():
        sheet_brand, alias = str(item[0] or "").strip(), str(item[1] or "").strip()
        if not sheet_brand or not alias or match_key(sheet_brand) == match_key(alias):
            continue
        if index.resolve(sheet_brand).conf == "mapped" or index.resolve(alias).conf == "mapped":
            continue                          # the sheet maps one of them: its owner decides
        entry = learned.setdefault(learned_key(alias), {"brand": alias, "synonyms": [alias], "learned": True,
                                                        "excluded_competitors": [], "learned_domains": []})
        if sheet_brand not in entry["synonyms"]:
            entry["synonyms"].append(sheet_brand)
    learned = {k: v for k, v in learned.items() if k not in out}

    # Each review row counts for the brand the search resolves its sheet brand to.
    with_spellings = BrandIndex.from_mappings({**out, **learned})
    targets: Dict[Tuple[str, str], Dict[str, Any]] = {}
    tally: Dict[Tuple[str, str], Dict[str, List[int]]] = {}
    for brand, domain, approvals, rejections in _source_rows(sources):
        if not brand or not domain:
            continue
        res = with_spellings.resolve(brand)
        if res.conf in ("mapped", "learned"):
            pool = out if res.conf == "mapped" else learned
            key = next((k for k, v in pool.items() if isinstance(v, Mapping)
                        and match_key(str(v.get("brand") or k)) == match_key(res.canonical)), None)
            if key is None:
                continue
            target = (res.conf, key)
            targets.setdefault(target, pool[key])
        else:
            target = ("raw", match_key(brand))
            targets.setdefault(target, {"brand": brand, "synonyms": [], "learned": True, "sources_only": True,
                                        "excluded_competitors": [], "learned_domains": []})
        host = _usable_source(domain, (url_host(d) or d for d in targets[target].get("official_domains") or []))
        if host:
            counts = tally.setdefault(target, {}).setdefault(host, [0, 0])
            counts[0] += approvals
            counts[1] += rejections

    for target, hosts in tally.items():
        entry = targets[target]
        kept = sorted((h for h, (ok, bad) in hosts.items() if ok >= MIN_SOURCE_APPROVALS and not bad),
                      key=lambda h: (-hosts[h][0], h))
        merged = list(dict.fromkeys(list(entry.get("learned_domains") or []) + kept))
        if not merged:
            continue
        entry["learned_domains"] = merged[:MAX_SOURCES_PER_BRAND]
        if target[0] == "raw":
            out.setdefault("learned source: " + target[1], entry)

    for key, entry in learned.items():
        out.setdefault(key, entry)
    return out


def _cached(name: str, loader, now: float) -> Any:
    with _cache_lock:
        hit = _cache.get(name)
        if hit is not None and now - hit[0] < LEARN_CACHE_S:
            return hit[1]
    value = loader()
    with _cache_lock:
        _cache[name] = (now, value)
    return value


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


def load_and_apply(mappings: Optional[Mapping[str, Any]], clock=time.monotonic) -> Dict[str, Any]:
    """apply() with the lessons stored in the local database; the mappings unchanged when it cannot be read."""
    try:
        import local_cache_db

        now = clock()
        aliases = _cached("aliases", lambda: [(a[0], a[1]) for a in local_cache_db.get_learned_brand_aliases()], now)
        sources = _cached("sources", local_cache_db.get_brand_source_counts, now)
    except Exception as exc:
        logger.info("learning: nothing learned is available (%s)", type(exc).__name__)
        return dict(mappings or {})
    if not aliases and not sources:
        return dict(mappings or {})
    return apply(mappings, aliases, sources)
