"""Brand discovery: the stores' spelling of a sheet brand they write differently (identity, search accuracy).

discover(spec, candidates) -> Optional[Discovery]
apply(spec, discovery)     -> SkuSpec
corrected_query(spec)      -> Optional[PlannedQuery]

The owner's sheet sometimes misspells a brand ('RIO MARIE' for Rio Mare, 'INA PARAMANS' for Ina
Paarman's) or abbreviates it ('SUP/T', 'SUPER T/', 'SUPER/T' for Super Tasty). Without a Brands
Mapping synonym no listing then carries the brand: every candidate is tier 3, and the label reader
cannot confirm the brand either (live run 2026-10-03, rows 37, 45 and 49-52).

discover() runs after the first retrieval, only when the brand is not mapped and NO candidate states
the sheet's brand (title, page title or the page's own URL slug). The words that open each listing
title, page title and slug (after 'Buy' / 'Shop') are compared with the sheet brand:
  * spelling: the same number of words, exactly one word one edit away (a letter added, dropped or
    changed, or two neighbours swapped), never on its first or last letter, the word 4+ letters and
    the brand 7+ letters. 'rio marie' -> 'rio mare', 'ina paramans' -> 'ina paarmans'; 'american' is
    never 'americana' (the last letter differs: that is another brand, not a typo);
  * abbreviation: only for a sheet brand written with '/' or '.' or with a one-letter word, of 2+
    words; every sheet word starts its store word, at least one store word is longer, the first sheet
    word keeps 3+ letters and the store phrase 6+ letters. 'sup t' -> 'super tasty'.
The store phrase counts only where the same text also names one of the product-type words (sheet words
written together count: 'SOLIDTUNA' in 'Solid Tuna'), it must come from a UAE store (a known UAE
retailer outside its other-country sections, or a .ae site) or from two different sites, and it must
not be a known other brand.

apply() makes the spec accept the store spelling: it is added to match_brands (scoring, the label
reader's accepted names and its brand check) and to discovered_brands (queries write it, the prompt
names it). brand_conf does not change, so nothing auto-publishes on a discovered spelling, and a pick
whose brand evidence is only the store spelling carries the 'brand_spelling' review warning
(decide.review_warnings). corrected_query() is one more query written with the store spelling.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, replace
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from .models import Candidate, PlannedQuery, SkuSpec
from .text_norm import any_brand_in, is_arabic, match_string, store_market, tokens, url_host, url_path_text

logger = logging.getLogger(__name__)

QUERY_ID = "B1"
LEAD_SKIP = frozenset({"buy", "shop", "order", "new"})
MIN_SPELLING_LETTERS = 7
MIN_WORD_LETTERS = 4
MIN_ABBREV_FIRST = 3
MIN_ABBREV_LETTERS = 6
MIN_JOINED_LETTERS = 6           # a sheet word found inside the run-together title ('solidtuna')
_ABBREV_MARK_RE = re.compile(r"[A-Za-z][/.]|[/.][A-Za-z]")


@dataclass(frozen=True)
class Discovery:
    phrase: str                  # the store spelling, normalised ('rio mare')
    display: str                 # as queries write it ('Rio Mare')
    kind: str                    # 'spelling' | 'abbreviation'
    sheet_phrase: str            # the sheet phrase it stands for ('rio marie')
    domains: Tuple[str, ...]     # the sites that write it


# ---------------------------------------------------------------------------
# Word comparison
# ---------------------------------------------------------------------------

def osa_distance(a: str, b: str) -> int:
    """Optimal string alignment distance: insertions, deletions, substitutions and adjacent swaps."""
    if a == b:
        return 0
    rows = [list(range(len(b) + 1))]
    for i in range(1, len(a) + 1):
        row = [i] + [0] * len(b)
        for j in range(1, len(b) + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            row[j] = min(rows[i - 1][j] + 1, row[j - 1] + 1, rows[i - 1][j - 1] + cost)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                row[j] = min(row[j], rows[i - 2][j - 2] + 1)
        rows.append(row)
    return rows[-1][-1]


def _latin_word(w: str) -> bool:
    return w.isalpha() and not is_arabic(w)


def _one_inner_edit(sheet: str, store: str) -> bool:
    """One edit away, the first and last letters kept ('marie' / 'mare'; never 'american' / 'americana')."""
    if sheet == store or not (_latin_word(sheet) and _latin_word(store)):
        return False
    if min(len(sheet), len(store)) < MIN_WORD_LETTERS or abs(len(sheet) - len(store)) > 1:
        return False
    if sheet[0] != store[0] or sheet[-1] != store[-1]:
        return False
    return osa_distance(sheet, store) == 1


def spelling_of(sheet: Sequence[str], store: Sequence[str]) -> bool:
    """The store words are the sheet brand with one typo (see the module docstring)."""
    if len(sheet) != len(store) or sum(len(w) for w in store) < MIN_SPELLING_LETTERS:
        return False
    diffs = [(s, w) for s, w in zip(sheet, store) if s != w]
    return len(diffs) == 1 and _one_inner_edit(*diffs[0])


def abbreviated(raw_brand: str, sheet: Sequence[str]) -> bool:
    """The sheet writes the brand short: 'SUP/T', 'SUPER T/', 'SUPER/T', 'A.B. FOODS'."""
    return bool(_ABBREV_MARK_RE.search(raw_brand or "")) or any(len(w) == 1 and w.isalpha() for w in sheet)


def abbreviation_of(sheet: Sequence[str], store: Sequence[str]) -> bool:
    """Every sheet word starts its store word and at least one is longer ('sup t' -> 'super tasty')."""
    if len(sheet) != len(store) or len(sheet) < 2 or len(sheet[0]) < MIN_ABBREV_FIRST:
        return False
    if not all(_latin_word(w) and w.startswith(s) for s, w in zip(sheet, store)):
        return False
    return any(s != w for s, w in zip(sheet, store)) and sum(len(w) for w in store) >= MIN_ABBREV_LETTERS


# ---------------------------------------------------------------------------
# Listings
# ---------------------------------------------------------------------------

def _texts(cand: Candidate) -> List[str]:
    from .score import strip_site_suffix
    return [strip_site_suffix(cand.title or ""), strip_site_suffix(cand.page_title or ""),
            url_path_text(cand.page_url, product_segment=True)]


def _leading(text: str) -> List[str]:
    words = tokens(text)
    while words and words[0] in LEAD_SKIP:
        words = words[1:]
    return words


def _stem(word: str) -> str:
    return word[:-1] if (not is_arabic(word) and len(word) > 3 and word.endswith("s")
                         and not word.endswith("ss")) else word


def names_the_product(spec: SkuSpec, text: str) -> bool:
    """The text names one of the SKU's product-type words (also a run-together sheet word)."""
    if not spec.class_tokens:
        return True
    words = tokens(text, strip_clitics=True)
    stems, joined = {_stem(w) for w in words}, "".join(words)
    for tok in spec.class_tokens:
        for w in tokens(tok, strip_clitics=True):
            if _stem(w) in stems or (len(w) >= MIN_JOINED_LETTERS and w in joined):
                return True
    return False


def _domain(cand: Candidate) -> str:
    from .score import page_host
    return page_host(cand) or url_host(cand.image_url)


def _uae_store(cand: Candidate) -> bool:
    """A known UAE retailer outside its other-country sections, or any .ae site."""
    from .score import trusted_domains
    from .text_norm import domain_matches
    host = _domain(cand)
    if not host or store_market(cand.page_url) == "foreign":
        return False
    return host.endswith(".ae") or domain_matches(host, trusted_domains().get("uae_retailers", []))


def states_the_brand(spec: SkuSpec, cands: Iterable[Candidate]) -> bool:
    """True when any listing writes one of the spec's brand phrases (title, page title or slug)."""
    return any(any_brand_in(spec.match_brands, text) for c in cands for text in _texts(c) if text)


def _sheet_phrases(spec: SkuSpec) -> List[Tuple[str, List[str]]]:
    out = []
    for phrase in spec.match_brands:
        words = tokens(phrase)
        if words and all(_latin_word(w) for w in words) and (phrase, words) not in out:
            out.append((phrase, words))
    return out


def discover(spec: SkuSpec, cands: Iterable[Candidate]) -> Optional[Discovery]:
    """The store spelling of the sheet brand, or None (see the module docstring)."""
    cands = list(cands or ())
    if spec.brand_conf == "mapped" or not spec.match_brands or not cands or states_the_brand(spec, cands):
        return None
    phrases = _sheet_phrases(spec)
    if not phrases:
        return None
    found: Dict[str, Dict] = {}
    for cand in cands:
        for text in _texts(cand):
            lead = _leading(text)
            if not lead or not names_the_product(spec, text):
                continue
            for phrase, sheet in phrases:
                store = lead[:len(sheet)]
                if spelling_of(sheet, store):
                    kind = "spelling"
                elif abbreviated(spec.brand_raw, sheet) and abbreviation_of(sheet, store):
                    kind = "abbreviation"
                else:
                    continue
                key = " ".join(store)
                entry = found.setdefault(key, {"kind": kind, "sheet": phrase, "domains": set(), "uae": False})
                entry["domains"].add(_domain(cand))
                entry["uae"] = entry["uae"] or _uae_store(cand)
    ranked = []
    for key, e in found.items():
        if not (e["uae"] or len(e["domains"]) >= 2):
            continue
        if spec.competitors and any_brand_in(spec.competitors, key):
            continue                         # a known other brand is never a spelling of this one
        ranked.append((not e["uae"], -len(e["domains"]), key, e))
    if not ranked:
        return None
    _, _, key, e = sorted(ranked)[0]
    found_one = Discovery(phrase=key, display=" ".join(w.capitalize() for w in key.split()), kind=e["kind"],
                          sheet_phrase=e["sheet"], domains=tuple(sorted(d for d in e["domains"] if d)))
    logger.info("brand discovery sku=%s: sheet %r is written %r by %s (%s)", spec.sku_key, e["sheet"],
                found_one.display, ", ".join(found_one.domains), found_one.kind)
    return found_one


def apply(spec: SkuSpec, found: Discovery) -> SkuSpec:
    """The spec that also accepts the store spelling (brand_conf unchanged: never an auto-publish)."""
    return replace(spec, match_brands=tuple(dict.fromkeys(tuple(spec.match_brands) + (found.phrase,))),
                   discovered_brands=tuple(dict.fromkeys(tuple(spec.discovered_brands) + (found.display,))))


def discovered_phrases(spec: SkuSpec) -> Set[str]:
    return {match_string(d) for d in spec.discovered_brands if d}


def corrected_query(spec: SkuSpec, already: Sequence[str] = ()) -> Optional[PlannedQuery]:
    """Q1 written with the store spelling (query_plan prefers discovered_brands), unless it already ran."""
    from .query_plan import build_queries

    q1 = next((q for q in build_queries(spec) if q.query_id == "Q1"), None)
    if q1 is None or not q1.text or q1.text in set(already):
        return None
    return PlannedQuery(query_id=QUERY_ID, text=q1.text, hl=q1.hl)
