"""Variant extraction (fat level, flavour, sugar, caffeine, form) in English and Arabic.

The lexicon (data/variants_lexicon.json) defines mutually exclusive groups called
axes. extract_variants() returns {axis: value}; when a text names several values
of one axis ('Strawberry & Banana', or a listing 'Full Fat / Low Fat') the value is
the sorted values joined with '+'.

Rules used by scoring:
* conflicts(target, found): an axis conflicts only when BOTH sides state it and the
  value sets share nothing. An axis the target does not state never conflicts, so
  'Almarai Fresh Milk' accepts a 'Full Fat' listing, and a missing flavour is never
  read as 'plain' ('plain' conflicts with a flavour only when the target says plain).
* soft_conflicts(target, found): disjoint values inside one lexicon 'soft group'
  (Diet vs Zero Sugar vs Sugar Free), or a generic value against values it covers
  ('meat' against mutton and lamb); scoring keeps these out of tier 1 without a hard
  reject, since retailers word the same line differently.
* matched_axes(target, found): axes whose value sets are identical (see _same: a listing
  may add the generic word, 'Mutton Meat Masala' for 'MUTTON MASALA', or the covered values
  a generic SKU is made for, 'Meat Masala for Mutton & Lamb' for 'MEAT MASALA').
* unstated_marked(target, found): axes the target does not state where the
  candidate states a 'marked' value (low fat, diet, decaf, a flavour). Scoring uses
  this to keep such candidates out of tier 1; it is never a hard reject.

Context-bound phrases ('context_values' in the lexicon) count only when the text, or the
`context` text passed in, has a token of that context: 'white' is a tuna meat grade only
next to 'tuna'. Callers that compare a candidate or a label reading with a SKU pass
spec_context(spec) plus the candidate's own texts, and compare it with target_variants(spec,
...): the SKU read again with the context the listing opens ('Indomie Chicken Flavour Noodles
with Seasoning' opens the protein axis for 'INDOMIE CHICKEN NOODLES'), so both sides are read
by the same rules. Bound phrases ('bound_values') count only right next to a word of their
context: 'meat' is a protein in 'meat masala' or 'meat burger', never in 'luncheon meat (with
spices)'.

An axis's 'generic' values cover some specific ones ('meat' covers beef, mutton and lamb). On the
SKU side (sku_variants) a covered value stated with the generic one replaces it: 'Mutton Meat
Masala' is mutton, so it still conflicts with a chicken masala (live run 2026-10-03: 'ALLDE MEAT
MASALA' and Allde Chicken Masala). A listing or a label keeps the generic value it states:
'Everest Meat Masala for Mutton & Chicken' is a meat masala, never only a mutton and chicken one.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Dict, FrozenSet, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from .text_norm import tokens

logger = logging.getLogger(__name__)

LEXICON_PATH = Path(__file__).resolve().parent / "data" / "variants_lexicon.json"
SEP = "+"


@dataclass(frozen=True)
class _Bound:
    """Where a bound phrase counts: next to an anchor word, across bridge words, never after a not_after word."""

    anchors: FrozenSet[str]
    bridges: FrozenSet[str]
    not_after: FrozenSet[str]


Entry = Tuple[Tuple[str, ...], str, str, Optional[FrozenSet[str]], Optional[_Bound]]


def _toks(words: Iterable[str]) -> FrozenSet[str]:
    return frozenset(t for word in words for t in tokens(word, strip_clitics=True))


class _Lexicon:
    def __init__(self, raw: dict) -> None:
        # (phrase tokens, axis, value, context tokens or None, bound or None), longest phrase first
        entries: List[Entry] = []
        self.unmarked: Dict[str, Set[str]] = {}
        # axis -> [(context tokens, values that are not marked for products of that context)]
        self.context_unmarked: Dict[str, List[Tuple[FrozenSet[str], Set[str]]]] = {}
        self.soft_groups: Dict[str, List[Set[str]]] = {}
        # axis -> {generic value: the specific values it covers} ('meat' -> beef, mutton, lamb)
        self.generic: Dict[str, Dict[str, Set[str]]] = {}
        self.never_in_brand: Set[str] = set()
        self.axes: Tuple[str, ...] = tuple(raw.get("axes", {}).keys())
        contexts = {name: _toks(words) for name, words in (raw.get("contexts") or {}).items()}
        # every token that opens a context-bound phrase somewhere (target_variants' pre-filter)
        opening: Set[str] = set()

        def context(axis: str, name: str) -> FrozenSet[str]:
            if name not in contexts:
                raise ValueError(f"variants lexicon: axis {axis!r} uses unknown context {name!r}")
            return contexts[name]

        def add(axis: str, values: Mapping[str, List[str]], ctx: Optional[FrozenSet[str]],
                bound: Optional[_Bound] = None) -> None:
            for value, phrases in values.items():
                for phrase in phrases:
                    toks = tuple(tokens(phrase, strip_clitics=True))
                    if toks:
                        entries.append((toks, axis, value, ctx, bound))

        for axis, spec in raw.get("axes", {}).items():
            self.unmarked[axis] = set(spec.get("unmarked", []))
            self.soft_groups[axis] = [set(g) for g in spec.get("soft_groups", [])]
            self.generic[axis] = {g: set(v) for g, v in (spec.get("generic") or {}).items()}
            if spec.get("never_in_brand"):
                self.never_in_brand.add(axis)
            for ctx_name, values in (spec.get("context_unmarked") or {}).items():
                self.context_unmarked.setdefault(axis, []).append((context(axis, ctx_name), set(values)))
            add(axis, spec.get("values", {}), None)
            for ctx_name, values in (spec.get("context_values") or {}).items():
                opening.update(context(axis, ctx_name))
                add(axis, values, context(axis, ctx_name))
            bound_values = spec.get("bound_values") or {}
            if bound_values:
                # the axis's own value words bridge too: 'Meat & Chicken Masala'
                own = _toks(p for vals in [spec.get("values", {})] + list((spec.get("context_values") or {}).values())
                            for phrases in vals.values() for p in phrases)
                bridges = _toks(spec.get("bound_bridges", [])) | own
                not_after = _toks(spec.get("bound_not_after", []))
                for ctx_name, values in bound_values.items():
                    add(axis, values, None, _Bound(context(axis, ctx_name), bridges, not_after))
        # Longest phrase first; at equal length a context-bound phrase wins ('light' on a tuna can).
        entries.sort(key=lambda e: (-len(e[0]), 0 if (e[3] or e[4]) else 1, -sum(len(t) for t in e[0])))
        self.entries = entries
        # every token that belongs to some phrase, for a cheap pre-filter
        self.vocabulary = {t for toks, _, _, _, _ in entries for t in toks}
        self.opening: FrozenSet[str] = frozenset(opening)


@lru_cache(maxsize=1)
def lexicon() -> _Lexicon:
    with open(LEXICON_PATH, "r", encoding="utf-8") as fh:
        return _Lexicon(json.load(fh))


def _anchored(toks: Sequence[str], i: int, j: int, bound: _Bound) -> bool:
    """A bound phrase at toks[i:j] has an anchor next to it (the next word across bridges, or the word before)."""
    if i > 0 and toks[i - 1] in bound.not_after:
        return False
    k = j
    while k < len(toks) and toks[k] in bound.bridges:
        k += 1
    if k < len(toks) and toks[k] in bound.anchors:
        return True
    k = i - 1
    while k >= 0 and toks[k] in bound.bridges:
        k -= 1
    return k >= 0 and toks[k] in bound.anchors


def _brand_spans(toks: Sequence[str], brands: Iterable[str]) -> List[Tuple[int, int]]:
    spans: List[Tuple[int, int]] = []
    for phrase in brands or ():
        p = tokens(phrase, strip_clitics=True)
        n = len(p)
        if not n:
            continue
        for i in range(len(toks) - n + 1):
            if list(toks[i:i + n]) == p:
                spans.append((i, i + n))
    return spans


def _scan(text: Optional[str], context: Optional[str] = None,
          brands: Iterable[str] = ()) -> List[Tuple[str, str, Tuple[int, int]]]:
    """Return (axis, value, token span) for every lexicon phrase in text, longest first, no overlaps.

    A context-bound phrase counts only when `text` or `context` has a token of its context; a bound
    phrase only next to a token of its context in `text`; a never_in_brand axis never inside one of
    the `brands` phrases.
    """
    toks = tokens(text, strip_clitics=True)
    if not toks:
        return []
    lex = lexicon()
    if not lex.vocabulary.intersection(toks):
        return []
    present = set(toks) | set(tokens(context, strip_clitics=True))
    in_brand = _brand_spans(toks, brands) if lex.never_in_brand else []
    used = [False] * len(toks)
    hits: List[Tuple[str, str, Tuple[int, int]]] = []
    for phrase, axis, value, ctx, bound in lex.entries:
        n = len(phrase)
        if n > len(toks) or (ctx is not None and ctx.isdisjoint(present)):
            continue
        if bound is not None and bound.anchors.isdisjoint(toks):
            continue
        for i in range(len(toks) - n + 1):
            if tuple(toks[i:i + n]) == phrase and not any(used[i:i + n]):
                if bound is not None and not _anchored(toks, i, i + n, bound):
                    continue
                if axis in lex.never_in_brand and any(a < i + n and i < b for a, b in in_brand):
                    continue
                for j in range(i, i + n):
                    used[j] = True
                hits.append((axis, value, (i, i + n)))
    return hits


def _join(values: Iterable[str]) -> str:
    return SEP.join(sorted(set(values)))


def values_of(value: Optional[str]) -> Set[str]:
    return set(value.split(SEP)) if value else set()


def spec_context(spec) -> str:
    """The SKU text that opens context-bound phrases when a candidate or a label is compared with it
    (the sheet name as written and as the stores write it: 'SOLIDTUNA' holds the 'tuna' of 'SOLID TUNA')."""
    from .sheet_names import spec_name      # sheet_names reads the lexicon's contexts through abbreviations
    parts = [getattr(spec, "raw_name", ""), spec_name(spec), getattr(spec, "name_ar", ""),
             getattr(spec, "category", "")]
    parts.extend(getattr(spec, "class_tokens", ()) or ())
    return " ".join(p for p in parts if p)


def spec_brands(spec) -> Tuple[str, ...]:
    """The SKU's brand phrases and its known competitors (a never_in_brand axis is read outside them)."""
    phrases = (getattr(spec, "brand_raw", ""), getattr(spec, "brand_canonical", ""), getattr(spec, "brand_ar", ""))
    phrases += tuple(getattr(spec, "match_brands", ()) or ()) + tuple(getattr(spec, "competitors", ()) or ())
    return tuple(p for p in dict.fromkeys(phrases) if p)


def _covered(axis: str, generic_values: Set[str]) -> Set[str]:
    gen = lexicon().generic.get(axis) or {}
    out: Set[str] = set()
    for g in generic_values:
        out |= gen.get(g, set())
    return out


def _specific(axis: str, values: Set[str]) -> Set[str]:
    """The values without a generic one when a value it covers is stated too ('meat' + 'mutton' -> mutton)."""
    gen = lexicon().generic.get(axis) or {}
    drop = {g for g in values if g in gen and values & gen[g]}
    return values - drop


def extract_variants(text: Optional[str], context: Optional[str] = None,
                     brands: Iterable[str] = ()) -> Dict[str, str]:
    """{axis: value} for every variant axis stated in the text (a generic value is kept as stated)."""
    per_axis: Dict[str, Set[str]] = {}
    for axis, value, _ in _scan(text, context, brands):
        per_axis.setdefault(axis, set()).add(value)
    return {axis: _join(vals) for axis, vals in per_axis.items()}


def phrase_spans(text: Optional[str], context: Optional[str] = None) -> List[Tuple[str, str, Tuple[int, int]]]:
    """(axis, value, (start, end)) for every variant phrase of the text, positions in its clitic-stripped tokens
    (identity.py reads where a sheet brand's last words begin one: 'AMERICAN LIGHT' + 'MEAT TUNA')."""
    return _scan(text, context)


def variant_tokens(text: Optional[str], context: Optional[str] = None, brands: Iterable[str] = ()) -> Set[str]:
    """The (clitic-stripped) tokens of the text that belong to a variant phrase."""
    toks = tokens(text, strip_clitics=True)
    out: Set[str] = set()
    for _, _, (a, b) in _scan(text, context, brands):
        out.update(toks[a:b])
    return out


def merge(*variant_dicts: Mapping[str, str]) -> Dict[str, str]:
    """Union of several {axis: value} dicts (e.g. English and Arabic name)."""
    per_axis: Dict[str, Set[str]] = {}
    for d in variant_dicts:
        for axis, value in (d or {}).items():
            per_axis.setdefault(axis, set()).update(values_of(value))
    return {axis: _join(vals) for axis, vals in per_axis.items() if vals}


def sku_variants(texts: Iterable[str], context: Optional[str] = None, brands: Iterable[str] = ()) -> Dict[str, str]:
    """The SKU's variants from its names: merged, a generic value given way to a value it covers."""
    brands = tuple(brands or ())
    merged = merge(*(extract_variants(t, context, brands) for t in texts if t))
    return {axis: _join(_specific(axis, values_of(v))) for axis, v in merged.items()}


@lru_cache(maxsize=4096)
def _sku_reread(names: Tuple[str, ...], context: str, brands: Tuple[str, ...], opened: FrozenSet[str]
                ) -> Dict[str, str]:
    return sku_variants(names, context + " " + " ".join(sorted(opened)), brands)


def target_variants(spec, *texts: Optional[str]) -> Dict[str, str]:
    """The SKU's variants compared with `texts` (a listing's fields, a label reading): spec.variants plus the
    axes the SKU names only in a context those texts open ('INDOMIE CHICKEN NOODLES' against 'Chicken
    Flavour Noodles with Seasoning' is chicken). An axis the SKU already states keeps its value."""
    base = dict(getattr(spec, "variants", None) or {})
    opened = lexicon().opening.intersection(tokens(" ".join(t for t in texts if t), strip_clitics=True))
    if not opened:
        return base
    from .sheet_names import raw_context, readable, spec_name
    own = spec_context(spec)
    opened = frozenset(opened - set(tokens(own, strip_clitics=True)))
    if not opened:
        return base
    name_ar = getattr(spec, "name_ar", "") or ""
    names = (spec_name(spec), readable(name_ar, raw_context(getattr(spec, "raw_name", ""), name_ar,
                                                            getattr(spec, "category", ""))))
    context = " ".join(t for t in names + (getattr(spec, "category", "") or "",) if t)
    for axis, value in _sku_reread(names, context, spec_brands(spec), opened).items():
        base.setdefault(axis, value)
    return base


def _soft_pair(axis: str, tvals: Set[str], fvals: Set[str]) -> bool:
    """True when every stated value on both sides lies in one soft group of the axis, or one side states
    only generic values and the other only values they cover ('meat' against mutton and lamb)."""
    for group in lexicon().soft_groups.get(axis, []):
        if tvals <= group and fvals <= group:
            return True
    gen = lexicon().generic.get(axis) or {}
    for a, b in ((tvals, fvals), (fvals, tvals)):
        if a and a <= set(gen) and b <= _covered(axis, a):
            return True
    return False


def conflicts(target: Mapping[str, str], found: Mapping[str, str]) -> List[str]:
    """Axes stated on both sides whose value sets are disjoint (hard conflicts only)."""
    out = []
    for axis, tval in (target or {}).items():
        fval = (found or {}).get(axis)
        if not fval or not tval:
            continue
        tv, fv = values_of(tval), values_of(fval)
        if tv.isdisjoint(fv) and not _soft_pair(axis, tv, fv):
            out.append(axis)
    return out


def soft_conflicts(target: Mapping[str, str], found: Mapping[str, str]) -> List[str]:
    """Axes whose disjoint values are closely related lines (Diet vs Zero Sugar): never tier 1."""
    out = []
    for axis, tval in (target or {}).items():
        fval = (found or {}).get(axis)
        if not fval or not tval:
            continue
        tv, fv = values_of(tval), values_of(fval)
        if tv.isdisjoint(fv) and _soft_pair(axis, tv, fv):
            out.append(axis)
    return out


def _same(axis: str, tv: Set[str], fv: Set[str]) -> bool:
    """The found values state the target's: identical, identical once the found side's generic word gives way
    ('Mutton Meat Masala' for mutton), or a generic target the found side names with values it covers
    ('Meat Masala for Mutton & Lamb' for meat; never with a value it does not cover, such as chicken)."""
    if not tv or not fv:
        return False
    if tv == fv or tv == _specific(axis, fv):
        return True
    gen = lexicon().generic.get(axis) or {}
    return tv <= set(gen) and tv <= fv and fv - tv <= _covered(axis, tv)


def matched_axes(target: Mapping[str, str], found: Mapping[str, str]) -> List[str]:
    """Target axes whose value set is stated in `found` (see _same)."""
    return [
        axis for axis, tval in (target or {}).items()
        if tval and _same(axis, values_of(tval), values_of((found or {}).get(axis)))
    ]


def unmarked_values(axis: str, context: Optional[str] = None) -> Set[str]:
    """Values of `axis` that are not suspicious when a SKU leaves the axis out.

    The lexicon's 'unmarked' values, plus those its 'context_unmarked' lists for a context the
    SKU text belongs to ('frozen' for fries, paratha or nuggets, which are sold frozen).
    """
    lex = lexicon()
    out = set(lex.unmarked.get(axis, set()))
    rules = lex.context_unmarked.get(axis)
    if rules and context:
        present = set(tokens(context, strip_clitics=True))
        for ctx, values in rules:
            if not ctx.isdisjoint(present):
                out |= values
    return out


def unstated_marked(target: Mapping[str, str], found: Mapping[str, str], context: Optional[str] = None) -> List[str]:
    """Axes absent from the target where `found` states a marked (non-default) value.

    `context` is the SKU text (spec_context) that makes context-unmarked values default.
    """
    out = []
    for axis, fval in (found or {}).items():
        if (target or {}).get(axis):
            continue
        if values_of(fval) - unmarked_values(axis, context):
            out.append(axis)
    return out
