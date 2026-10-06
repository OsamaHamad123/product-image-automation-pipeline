"""Barcodes the stores write in their own URLs (free evidence, data/url_gtins.json).

Sharjah Co-op files a product under '/p/<GTIN>' and its pictures under 'medias/<GTIN>-1200Wx1200H...', Spinneys
and Waitrose name the picture after the barcode, Union Coop's Magento store too: 23 of the 132 winning image URLs
of the live runs of 2026-10-04/05 carried a valid GTIN, while pages.py reads the barcode from a page's JSON-LD only.

from_url(url, kind) -> GTIN-14 or None     kind 'page' or 'image'; the store's patterns from data/url_gtins.json
url_gtin(cand) -> GTIN-14 or None           the page URL's, else the image URL's (None when the two disagree)
plausible(gtin14) -> bool                   valid, globally unique, a consumer unit, a GS1 prefix of trade items
identity_agrees(score) -> bool              the listing's own title names this row's product (the condition for
                                            using a URL barcode: a write-back proposal, a local-index lookup)

A URL barcode is evidence only: it never becomes gtin_on_page, so it never lifts a tier and never auto-publishes.
Its uses: the review evidence (url_gtin), the barcode a reviewer's approval keeps for a row without one
(evidence.page_gtin, facade), and a local-index lookup of the same barcode (local_index) - each only when the
candidate's own title agrees with the row's identity.
"""

from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, List, Optional, Pattern, Sequence, Tuple
from urllib.parse import unquote, urlsplit

from .gtin import is_global_gtin, normalize_gtin
from .text_norm import domain_matches

logger = logging.getLogger(__name__)

URL_GTINS_PATH = Path(__file__).resolve().parent / "data" / "url_gtins.json"
KINDS = ("page", "image")

# GS1 prefixes (the first three digits of the GTIN-13 form) that number no trade item a store sells: coupons,
# refund receipts, the GS1 Global Office's own ranges (EPC, demonstration, GTIN-8 administration) and ISSN serials.
# The restricted in-store ranges (02x, 04x, 2xx) are gtin.is_restricted's.
_NOT_TRADE_ITEMS = (
    tuple(f"{n:03d}" for n in range(50, 60))           # 050-059 US coupons
    + ("950", "951", "952") + tuple(str(n) for n in range(960, 970))
    + ("977", "980", "981", "982", "983", "984") + tuple(str(n) for n in range(990, 1000))
)


@lru_cache(maxsize=1)
def _stores() -> Tuple[Tuple[str, Tuple[str, ...], Tuple[Pattern, ...], Tuple[Pattern, ...]], ...]:
    try:
        with open(URL_GTINS_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):                       # pragma: no cover - the file ships with the code
        logger.exception("url_gtin: %s unreadable; no URL barcode is read", URL_GTINS_PATH.name)
        return ()
    out = []
    for store in data.get("stores") or []:
        hosts = tuple(str(h).strip().lower() for h in store.get("hosts") or [] if str(h).strip())
        try:
            page = tuple(re.compile(p, re.I) for p in store.get("page") or [])
            image = tuple(re.compile(p, re.I) for p in store.get("image") or [])
        except re.error:
            logger.exception("url_gtin: a pattern of store %r does not compile; the store is skipped",
                             store.get("key"))
            continue
        if hosts and (page or image):
            out.append((str(store.get("key") or ""), hosts, page, image))
    return tuple(out)


def plausible(gtin14: Optional[str]) -> bool:
    """A number worth calling a barcode: a valid GTIN (check digit, length), globally unique (no in-store code), a
    consumer unit (indicator 0: a case code is not the pack the app shows) and a GS1 prefix that numbers trade items."""
    if not gtin14 or not is_global_gtin(gtin14):
        return False
    g14, _ = normalize_gtin(gtin14)
    if not g14 or g14[0] != "0":
        return False
    g13 = g14[1:]
    if g13.startswith("00000"):                         # a GTIN-8: its own prefix (is_restricted checked 0 / 2)
        return True
    if len(set(g13)) <= 2:                              # 0000000000000-like fillers
        return False
    return not g13.startswith(_NOT_TRADE_ITEMS)


def _path(url: str) -> Tuple[str, str]:
    try:
        parts = urlsplit((url or "").strip())
    except ValueError:
        return "", ""
    host = (parts.hostname or "").lower()
    return host, unquote(parts.path or "")


def from_url(url: Optional[str], kind: str = "page") -> Optional[str]:
    """The barcode a known store wrote in this page or image URL, as a GTIN-14; None when there is none (or the
    patterns find two different plausible ones)."""
    if kind not in KINDS or not url:
        return None
    host, path = _path(url)
    if not host or not path:
        return None
    found: List[str] = []
    for _key, hosts, page, image in _stores():
        if not domain_matches(host, hosts):
            continue
        for pattern in page if kind == "page" else image:
            for m in pattern.finditer(path):
                g14, status = normalize_gtin(m.group("gtin"))
                if status == "ok" and plausible(g14) and g14 not in found:
                    found.append(g14)
    return found[0] if len(found) == 1 else None


def url_gtin(cand: Any) -> Optional[str]:
    """The candidate's URL barcode: its page URL's, else its image URL's; None when they name two different ones."""
    page = from_url(getattr(cand, "page_url", "") or "", "page")
    image = from_url(getattr(cand, "image_url", "") or "", "image")
    if page and image and page != image:
        return None
    return page or image


def identity_agrees(score: Any) -> bool:
    """The listing's own title (or page title) names the row's brand and nothing in its evidence disagrees: tier 1
    or 2, no hard reject, no size, pack or variant doubt, no differing page barcode, no other soft doubt."""
    from .score import has_soft_conflict

    if score is None or score.tier not in (1, 2) or score.hard_reject:
        return False
    matched = score.matched or {}
    titled = set(matched.get("brand_fields") or {}) & {"title", "page_title"}
    return (bool(titled) and score.size_status != "conflict" and matched.get("pack") != "conflict"
            and not score.url_only_size_conflict and not has_soft_conflict(score))


def agreeing_gtins(scored: Sequence[Tuple[Any, Any]], limit: int = 2) -> List[str]:
    """The distinct URL barcodes of (candidate, score) pairs whose title agrees with the row, best first."""
    out: List[str] = []
    for cand, score in scored:
        g = url_gtin(cand)
        if g and g not in out and identity_agrees(score):
            out.append(g)
            if len(out) >= limit:
                break
    return out
