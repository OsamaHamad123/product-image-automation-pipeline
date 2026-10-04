"""Reverse brand index built from the 'Brands Mapping' sheet.

Input is the dict returned by google_sheets.get_brand_mappings():
    {key: {'brand': 'Almarai', 'synonyms': [...], 'excluded_competitors': [...],
           'sub_brands': [...], 'official_domains': [...]}}      # last two optional
Lists may also be comma-separated strings.

resolve(brand_raw, name_en, name_ar) -> BrandResolution
  1. exact normalised lookup of the sheet brand in every canonical name, key,
     synonym (EN+AR) and sub-brand: 'A/G', 'المراعي', 'Al Marai' all resolve;
  2. otherwise a known synonym or sub-brand phrase at the START of the name;
  3. otherwise conf 'sheet_raw' (brand kept as written) or 'none' (no brand).
A store spelling the reviewers taught (catalog_match.learning) is an entry flagged 'learned': the
sheet brand it was taught for (step 1 only) resolves to it with conf 'learned', like a mapped
brand for search and scoring, never an auto-publish (decide.py). A lesson stays with the sheet
brand it was taught for: a learned entry is never matched at the start of another product's
name and its phrases never count as another product's competitor. Learned entries are listed
after the sheet's, and a phrase the sheet already maps keeps its sheet entry. An entry flagged
'sources_only' carries nothing but the sites the reviewers keep approving an unmapped sheet
brand's images from: that brand still resolves as 'sheet_raw' (brand discovery still runs for
it) with those sites as learned_domains.
Nothing is ever guessed from the first word of the name, and there is no fuzzy
matching (it merges real competitors such as Al Rawabi / Al Rabie).

match_brands holds the phrases used to find the brand in evidence text: the
canonical name and synonyms with >= 3 letters/digits, plus the sub-brands that
appear in the product name (sheet brand 'Nestle', name 'Nido ...' -> 'nido').
When the SKU names a sub-brand, `required` holds it (parent-only evidence such as
'Nestle' is then not enough for tier 1) and `siblings` holds the family's other
sub-brands ('Everyday', 'Nesquik'), which count as other brands when the required
sub-brand is absent.
Short synonyms ('A/G', 'AG') can resolve a sheet brand but never match evidence.

is_generic_brand(phrase) is True for a brand that is also an everyday listing word
('Freshly', 'Family', 'Golden Prize'): every significant token is in
data/common_words.json. score.py trusts such a brand's hit only where a brand stands.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Dict, FrozenSet, Iterable, List, Mapping, Optional, Sequence, Tuple

from .text_norm import alnum_len, is_arabic, match_key, match_string, norm_phrase, normalize, phrase_in, tokens

logger = logging.getLogger(__name__)

MIN_MATCH_ALNUM = 3
COMMON_WORDS_PATH = Path(__file__).resolve().parent / "data" / "common_words.json"


def _as_list(value) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        parts = value.replace("،", ",").replace(";", ",").split(",")
    elif isinstance(value, (list, tuple, set)):
        parts = []
        for v in value:
            parts.extend(_as_list(v) if isinstance(v, str) and "," in v else [str(v)])
    else:
        parts = [str(value)]
    out, seen = [], set()
    for p in parts:
        p = str(p).strip()
        if p and p.lower() not in seen:
            seen.add(p.lower())
            out.append(p)
    return out


def _clean_domain(value: str) -> str:
    d = normalize(value).strip()
    for prefix in ("https://", "http://"):
        if d.startswith(prefix):
            d = d[len(prefix):]
    d = d.split("/", 1)[0].split("?", 1)[0].strip(".")
    return d[4:] if d.startswith("www.") else d


def _compact(key: str) -> str:
    return key.replace(" ", "")


def _matchable(phrase: str) -> bool:
    return alnum_len(phrase) >= MIN_MATCH_ALNUM


@lru_cache(maxsize=1)
def _common_words() -> Tuple[FrozenSet[str], FrozenSet[str]]:
    """(common listing words, ignored tokens) of data/common_words.json, match-normalised."""
    with open(COMMON_WORDS_PATH, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    words = frozenset(match_string(w) for w in data.get("words", []) if match_string(w))
    ignored = frozenset(match_string(w) for w in data.get("ignored", []) if match_string(w))
    return words, ignored


def _common(tok: str, words: FrozenSet[str]) -> bool:
    # a plural 's' is folded the way score.py folds evidence tokens ('farms' -> 'farm')
    return tok in words or (len(tok) > 3 and tok.endswith("s") and not tok.endswith("ss") and tok[:-1] in words)


@lru_cache(maxsize=4096)
def is_generic_brand(phrase: Optional[str]) -> bool:
    """True when every significant token of a brand phrase is a common listing word.

    'Freshly', 'Family', 'Golden Prize', 'Super T', 'Al Fresh' are generic; 'Almarai',
    'Al Rawabi', 'American Garden', 'Mr John', '7 Up' and any Arabic phrase are not.
    Single letters and the 'ignored' articles / honorifics are not significant; a number
    or an Arabic word always is, and is never common. A phrase with no significant token
    is not generic.
    """
    words, ignored = _common_words()
    significant = [t for t in tokens(phrase, strip_clitics=True)
                   if t not in ignored and not (len(t) == 1 and t.isalpha())]
    return bool(significant) and all(_common(t, words) for t in significant)


def is_common_word(tok: str) -> bool:
    """True for a common listing word ('premium', 'fresh') or an ignored one ('the', 'al'): no brand by itself."""
    words, ignored = _common_words()
    tok = match_string(tok)
    return tok in ignored or _common(tok, words)


@dataclass(frozen=True)
class BrandEntry:
    canonical: str
    synonyms: Tuple[str, ...] = ()           # raw spellings, canonical included
    sub_brands: Tuple[str, ...] = ()
    competitors: Tuple[str, ...] = ()
    official_domains: Tuple[str, ...] = ()
    learned: bool = False                    # taught by review decisions, not the Brands Mapping sheet
    learned_domains: Tuple[str, ...] = ()    # sites the reviewers keep approving this brand's images from
    sources_only: bool = False               # only learned_domains for an unmapped sheet brand: no identity

    def phrases(self) -> Tuple[str, ...]:
        return tuple(dict.fromkeys((self.canonical,) + self.synonyms + self.sub_brands))


@dataclass(frozen=True)
class BrandResolution:
    canonical: str = ""
    match_brands: Tuple[str, ...] = ()
    competitors: Tuple[str, ...] = ()
    official_domains: Tuple[str, ...] = ()
    conf: str = "none"                       # 'mapped' | 'learned' | 'sheet_raw' | 'none'
    learned_domains: Tuple[str, ...] = ()
    brand_ar: str = ""                       # first Arabic spelling in the mapping, if any
    family: Tuple[str, ...] = field(default=())   # every normalised phrase of the resolved brand
    required: Tuple[str, ...] = ()           # sub-brand(s) the SKU names: evidence must show one for tier 1
    siblings: Tuple[str, ...] = ()           # the family's OTHER sub-brands (competitors when required is absent)


class BrandIndex:
    """Normalised synonym -> brand lookup plus the set of all known brand phrases."""

    def __init__(self, entries: Sequence[BrandEntry]) -> None:
        self.entries: Tuple[BrandEntry, ...] = tuple(entries)
        self._exact: Dict[str, int] = {}
        self._compact: Dict[str, Optional[int]] = {}
        self._sub_exact: Dict[str, int] = {}
        self._sub_compact: Dict[str, Optional[int]] = {}
        self._raw_sources: Dict[str, Tuple[str, ...]] = {}
        for idx, entry in enumerate(self.entries):
            if entry.sources_only:
                key = match_key(entry.canonical)
                if key and entry.learned_domains:
                    self._raw_sources.setdefault(key, entry.learned_domains)
                continue
            for phrase in (entry.canonical,) + entry.synonyms:
                self._add(self._exact, self._compact, phrase, idx)
            for phrase in entry.sub_brands:
                self._add(self._sub_exact, self._sub_compact, phrase, idx)
        known = set()
        for entry in self.entries:
            if entry.learned or entry.sources_only:
                continue          # a lesson never makes another product's brand a competitor
            for phrase in entry.phrases() + entry.competitors:
                p = norm_phrase(phrase)
                if p and _matchable(p):
                    known.add(p)
        self._known: Tuple[str, ...] = tuple(sorted(known))

    def _add(self, exact: Dict[str, int], compact: Dict[str, Optional[int]], phrase: str, idx: int) -> None:
        key = match_key(phrase)
        if not key:
            return
        prev = exact.get(key)
        if prev is not None and prev != idx:
            logger.warning("brand phrase %r maps to both %r and %r; keeping the first",
                           phrase, self.entries[prev].canonical, self.entries[idx].canonical)
        else:
            exact[key] = idx
        ck = _compact(key)
        if ck in compact and compact[ck] != idx:
            compact[ck] = None  # ambiguous compact spelling: never used
        else:
            compact[ck] = idx

    # ------------------------------------------------------------------ build

    @classmethod
    def from_mappings(cls, mappings: Optional[Mapping[str, Mapping]]) -> "BrandIndex":
        entries: List[BrandEntry] = []
        for key, row in (mappings or {}).items():
            if not isinstance(row, Mapping):
                continue
            canonical = str(row.get("brand") or key or "").strip()
            if not canonical:
                continue
            synonyms = _as_list(row.get("synonyms"))
            if key and str(key).strip() and match_key(key) != match_key(canonical):
                synonyms.append(str(key).strip())
            synonyms = [s for s in dict.fromkeys(synonyms) if match_key(s) and match_key(s) != match_key(canonical)]
            entries.append(BrandEntry(
                canonical=canonical,
                synonyms=tuple(synonyms),
                sub_brands=tuple(_as_list(row.get("sub_brands"))),
                competitors=tuple(_as_list(row.get("excluded_competitors"))),
                official_domains=tuple(d for d in (_clean_domain(x) for x in _as_list(row.get("official_domains"))) if d),
                learned=bool(row.get("learned")),
                learned_domains=tuple(d for d in (_clean_domain(x) for x in _as_list(row.get("learned_domains"))) if d),
                sources_only=bool(row.get("sources_only")),
            ))
        return cls(entries)

    # ----------------------------------------------------------------- lookup

    def known_brands(self) -> Tuple[str, ...]:
        """Every canonical, synonym, sub-brand and listed competitor phrase (>= 3 alnum), normalised."""
        return self._known

    def entry(self, canonical: str) -> Optional[BrandEntry]:
        key = match_key(canonical)
        idx = self._exact.get(key)
        return self.entries[idx] if idx is not None else None

    def _lookup(self, text: str, exact: Dict[str, int], compact: Dict[str, Optional[int]]) -> Optional[int]:
        key = match_key(text)
        if not key:
            return None
        if key in exact:
            return exact[key]
        return compact.get(_compact(key))

    def _name_start(self, name: str) -> Optional[Tuple[int, str, bool]]:
        """(entry index, phrase, is_sub_brand) for the longest known phrase that starts the name."""
        name_toks = tokens(name, strip_clitics=True)
        if not name_toks:
            return None
        best: Optional[Tuple[int, str, bool]] = None
        best_len = 0
        for idx, entry in enumerate(self.entries):
            if entry.learned or entry.sources_only:
                continue  # a lesson applies to the sheet brand it was taught for, not to a name that starts with it
            for phrase, is_sub in [(p, False) for p in (entry.canonical,) + entry.synonyms] + [(p, True) for p in entry.sub_brands]:
                if not _matchable(phrase):
                    continue  # 'A/G' at the start of a name is too weak to invent a brand from
                ptoks = tokens(phrase, strip_clitics=True)
                if ptoks and name_toks[:len(ptoks)] == ptoks and len(ptoks) > best_len:
                    best, best_len = (idx, phrase, is_sub), len(ptoks)
        return best

    def resolve(self, brand_raw: Optional[str], name_en: Optional[str] = "", name_ar: Optional[str] = "") -> BrandResolution:
        brand = (brand_raw or "").strip()
        names = [n for n in (name_en or "", name_ar or "") if n and n.strip()]
        idx: Optional[int] = None
        forced_sub: List[str] = []
        if brand:
            idx = self._lookup(brand, self._exact, self._compact)
            if idx is None:
                idx = self._lookup(brand, self._sub_exact, self._sub_compact)
                if idx is not None:
                    forced_sub.append(brand)
        if idx is None:
            for name in names:
                hit = self._name_start(name)
                if hit:
                    idx = hit[0]
                    if hit[2]:
                        forced_sub.append(hit[1])
                    break
        if idx is None:
            return self._unmapped(brand)
        return self._mapped(self.entries[idx], names, forced_sub)

    def _mapped(self, entry: BrandEntry, names: Sequence[str], forced_sub: Sequence[str]) -> BrandResolution:
        phrases: List[str] = []
        for p in (entry.canonical,) + entry.synonyms:
            if _matchable(p):
                phrases.append(norm_phrase(p))
        required: List[str] = []
        for sub in entry.sub_brands:
            if sub in forced_sub or any(phrase_in(sub, n) for n in names):
                if _matchable(sub):
                    required.append(norm_phrase(sub))
        for sub in forced_sub:
            if _matchable(sub):
                required.append(norm_phrase(sub))
        required = [r for r in dict.fromkeys(required) if r]
        phrases.extend(required)
        match_brands = tuple(dict.fromkeys(p for p in phrases if p))
        siblings = tuple(dict.fromkeys(
            norm_phrase(s) for s in entry.sub_brands
            if _matchable(s) and norm_phrase(s) and norm_phrase(s) not in required
            and not any(phrase_in(s, r) or phrase_in(r, s) for r in required)
        )) if required else ()
        family = tuple(dict.fromkeys(norm_phrase(p) for p in entry.phrases() if norm_phrase(p)))
        competitors = [norm_phrase(c) for c in entry.competitors] + list(self._known)
        comp = _other_brands(competitors, family)
        brand_ar = next((s for s in (entry.canonical,) + entry.synonyms if is_arabic(normalize(s)[:1])), "")
        return BrandResolution(
            canonical=entry.canonical,
            match_brands=match_brands,
            competitors=comp,
            official_domains=entry.official_domains,
            conf="learned" if entry.learned else "mapped",
            learned_domains=entry.learned_domains,
            brand_ar=brand_ar,
            family=family,
            required=tuple(required),
            siblings=siblings,
        )

    def _unmapped(self, brand: str) -> BrandResolution:
        if not brand:
            return BrandResolution(conf="none")
        phrase = norm_phrase(brand)
        match_brands = (phrase,) if phrase and _matchable(phrase) else ()
        comp = _other_brands(self._known, (phrase,) if phrase else ())
        return BrandResolution(
            canonical=brand,
            match_brands=match_brands,
            competitors=comp,
            conf="sheet_raw",
            learned_domains=self._raw_sources.get(match_key(brand), ()),
            brand_ar=brand if is_arabic(normalize(brand)[:1]) else "",
            family=(phrase,) if phrase else (),
        )


def _other_brands(candidates: Iterable[str], family: Sequence[str]) -> Tuple[str, ...]:
    """Competitor phrases minus the target's own phrases and anything nested in them.

    'almarai' is not a competitor of an unmapped sheet brand 'Almarai Dairy', and
    'al ain farms' is not one of 'Al Ain'; such candidates carry the target brand anyway.
    """
    family_keys = [match_key(f) for f in family if match_key(f)]
    out = []
    for c in candidates:
        key = match_key(c)
        if not c or not key or not _matchable(c):
            continue
        if any(key == f or phrase_in(key, f) or phrase_in(f, key) for f in family_keys):
            continue
        out.append(c)
    return tuple(dict.fromkeys(out))


def build_index(brand_mappings) -> BrandIndex:
    """Accept a BrandIndex, a mappings dict or None."""
    if isinstance(brand_mappings, BrandIndex):
        return brand_mappings
    return BrandIndex.from_mappings(brand_mappings or {})
