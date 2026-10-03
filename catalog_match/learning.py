"""Learning from review decisions: what the reviewers confirm becomes search knowledge for the next products.

Two lessons, both kept in the local database (local_cache_db), neither ever an auto-publish:

  * A store spelling of a sheet brand. brand_discovery marks a pick whose brand only the stores'
    spelling supports with the review warning 'brand_spelling:<spelling>' ('Rio Mare' for the sheet's
    'RIO MARIE'). Approving that image records the spelling (cli_bridge, record_brand_alias); a
    WRONG_BRAND rejection of it counts against it. While approvals outnumber those rejections, the
    sheet brand resolves to the spelling (brand_conf 'learned'): queries write it from the first
    query, scoring and the label reader accept it, and the warning is no longer needed.
  * A brand's sources. A site the reviewers approved a brand's images from at least
    MIN_SOURCE_APPROVALS times, with no identity rejection of an image from it for that brand,
    gets UAE-retailer trust for the brand ('reviewed_source', score.source_trust) and a place in
    its site: query. Social networks and stock-photo sites never count.

apply(mappings, aliases, sources) merges both into the Brands Mapping dict as entries flagged
'learned' (brand_index): the sheet's own entries always win (a phrase the sheet maps keeps its
entry), a learned spelling is never added when the sheet maps the sheet brand or the spelling (the
sheet's owner decides those), and a mapped brand only gains learned_domains. decide.py blocks
auto-publish for brand_conf 'learned' and for a pick whose trust is only 'reviewed_source'.

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


def _usable_source(domain: str) -> str:
    from .decide import _social_host
    from .score import trusted_domains
    from .text_norm import domain_matches

    host = url_host(domain) or str(domain or "").strip().lower()
    if not host or "." not in host or _social_host(host):
        return ""
    if domain_matches(host, trusted_domains().get("stock_or_clipart", [])):
        return ""
    return host


def apply(mappings: Optional[Mapping[str, Any]], aliases: Iterable[Sequence[Any]] = (),
          sources: Optional[Mapping[str, Sequence[str]]] = None) -> Dict[str, Any]:
    """The Brands Mapping dict with what the reviewers taught (see the module docstring)."""
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

    for brand, domains in (sources or {}).items():
        brand = str(brand or "").strip()
        hosts = [h for h in dict.fromkeys(_usable_source(d) for d in domains or ()) if h]
        if not brand or not hosts:
            continue
        res = index.resolve(brand)
        if res.conf == "mapped":
            key = next((k for k, v in out.items() if isinstance(v, Mapping)
                        and match_key(str(v.get("brand") or k)) == match_key(res.canonical)), None)
            if key is None:
                continue
            entry = out[key]
        else:
            entry = next((e for e in learned.values() if any(match_key(s) == match_key(brand) for s in e["synonyms"])),
                         None)
            if entry is None:
                entry = learned.setdefault(learned_key(brand), {"brand": brand, "synonyms": [brand], "learned": True,
                                                               "excluded_competitors": [], "learned_domains": []})
        official = {url_host(d) or d for d in entry.get("official_domains") or []}
        merged = [h for h in dict.fromkeys(list(entry.get("learned_domains") or []) + hosts) if h not in official]
        entry["learned_domains"] = merged[:MAX_SOURCES_PER_BRAND]

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
        sources = _cached("sources", lambda: local_cache_db.get_learned_brand_sources(MIN_SOURCE_APPROVALS), now)
    except Exception as exc:
        logger.info("learning: nothing learned is available (%s)", type(exc).__name__)
        return dict(mappings or {})
    if not aliases and not sources:
        return dict(mappings or {})
    return apply(mappings, aliases, sources)
