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
  (Diet vs Zero Sugar vs Sugar Free); scoring keeps these out of tier 1 without a
  hard reject, since retailers word the same line differently.
* matched_axes(target, found): axes whose value sets are identical.
* unstated_marked(target, found): axes the target does not state where the
  candidate states a 'marked' value (low fat, diet, decaf, a flavour). Scoring uses
  this to keep such candidates out of tier 1; it is never a hard reject.

Context-bound phrases ('context_values' in the lexicon) count only when the text, or the
`context` text passed in, has a token of that context: 'white' is a tuna meat grade only
next to 'tuna'. Callers that compare a candidate or a label reading with a SKU pass
spec_context(spec), because a label reading ('White Meat') rarely repeats the product type.

An axis's 'generic' values give way to a specific value stated in the same text (or in the
other name, for merge()): the protein of 'Mutton Meat Masala' is mutton, not mutton+meat,
so it still conflicts with a chicken masala (live run 2026-10-03: 'ZWAN BEEF LUNCHEON MEAT'
and the Zwan Chicken listing, 'ALLDE MEAT MASALA' and Allde Chicken Masala).
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Dict, FrozenSet, Iterable, List, Mapping, Optional, Set, Tuple

from .text_norm import tokens

logger = logging.getLogger(__name__)

LEXICON_PATH = Path(__file__).resolve().parent / "data" / "variants_lexicon.json"
SEP = "+"


Entry = Tuple[Tuple[str, ...], str, str, Optional[FrozenSet[str]]]


class _Lexicon:
    def __init__(self, raw: dict) -> None:
        # (phrase tokens, axis, value, context tokens or None), longest phrase first
        entries: List[Entry] = []
        self.unmarked: Dict[str, Set[str]] = {}
        # axis -> [(context tokens, values that are not marked for products of that context)]
        self.context_unmarked: Dict[str, List[Tuple[FrozenSet[str], Set[str]]]] = {}
        self.soft_groups: Dict[str, List[Set[str]]] = {}
        # axis -> values that a more specific value of the axis replaces ('meat' next to 'beef' is beef)
        self.generic: Dict[str, Set[str]] = {}
        self.axes: Tuple[str, ...] = tuple(raw.get("axes", {}).keys())
        contexts = {name: frozenset(t for word in words for t in tokens(word, strip_clitics=True))
                    for name, words in (raw.get("contexts") or {}).items()}

        def add(axis: str, values: Mapping[str, List[str]], ctx: Optional[FrozenSet[str]]) -> None:
            for value, phrases in values.items():
                for phrase in phrases:
                    toks = tuple(tokens(phrase, strip_clitics=True))
                    if toks:
                        entries.append((toks, axis, value, ctx))

        for axis, spec in raw.get("axes", {}).items():
            self.unmarked[axis] = set(spec.get("unmarked", []))
            self.soft_groups[axis] = [set(g) for g in spec.get("soft_groups", [])]
            self.generic[axis] = set(spec.get("generic", []))
            for ctx_name, values in (spec.get("context_unmarked") or {}).items():
                if ctx_name not in contexts:
                    raise ValueError(f"variants lexicon: axis {axis!r} uses unknown context {ctx_name!r}")
                self.context_unmarked.setdefault(axis, []).append((contexts[ctx_name], set(values)))
            add(axis, spec.get("values", {}), None)
            for ctx_name, values in (spec.get("context_values") or {}).items():
                if ctx_name not in contexts:
                    raise ValueError(f"variants lexicon: axis {axis!r} uses unknown context {ctx_name!r}")
                add(axis, values, contexts[ctx_name])
        # Longest phrase first; at equal length a context-bound phrase wins ('light' on a tuna can).
        entries.sort(key=lambda e: (-len(e[0]), 0 if e[3] else 1, -sum(len(t) for t in e[0])))
        self.entries = entries
        # every token that belongs to some phrase, for a cheap pre-filter
        self.vocabulary = {t for toks, _, _, _ in entries for t in toks}


@lru_cache(maxsize=1)
def lexicon() -> _Lexicon:
    with open(LEXICON_PATH, "r", encoding="utf-8") as fh:
        return _Lexicon(json.load(fh))


def _scan(text: Optional[str], context: Optional[str] = None) -> List[Tuple[str, str, Tuple[int, int]]]:
    """Return (axis, value, token span) for every lexicon phrase in text, longest first, no overlaps.

    A context-bound phrase counts only when `text` or `context` has a token of its context.
    """
    toks = tokens(text, strip_clitics=True)
    if not toks:
        return []
    lex = lexicon()
    if not lex.vocabulary.intersection(toks):
        return []
    present = set(toks) | set(tokens(context, strip_clitics=True))
    used = [False] * len(toks)
    hits: List[Tuple[str, str, Tuple[int, int]]] = []
    for phrase, axis, value, ctx in lex.entries:
        n = len(phrase)
        if n > len(toks) or (ctx is not None and ctx.isdisjoint(present)):
            continue
        for i in range(len(toks) - n + 1):
            if tuple(toks[i:i + n]) == phrase and not any(used[i:i + n]):
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


def _specific(axis: str, values: Set[str]) -> Set[str]:
    """The values without the axis's generic ones when a specific one is stated too ('meat' + 'mutton' -> mutton)."""
    generic = lexicon().generic.get(axis)
    if generic and values - generic:
        return values - generic
    return values


def extract_variants(text: Optional[str], context: Optional[str] = None) -> Dict[str, str]:
    """{axis: value} for every variant axis stated in the text."""
    per_axis: Dict[str, Set[str]] = {}
    for axis, value, _ in _scan(text, context):
        per_axis.setdefault(axis, set()).add(value)
    return {axis: _join(_specific(axis, vals)) for axis, vals in per_axis.items()}


def variant_tokens(text: Optional[str], context: Optional[str] = None) -> Set[str]:
    """The (clitic-stripped) tokens of the text that belong to a variant phrase."""
    toks = tokens(text, strip_clitics=True)
    out: Set[str] = set()
    for _, _, (a, b) in _scan(text, context):
        out.update(toks[a:b])
    return out


def merge(*variant_dicts: Mapping[str, str]) -> Dict[str, str]:
    """Union of several {axis: value} dicts (e.g. English and Arabic name)."""
    per_axis: Dict[str, Set[str]] = {}
    for d in variant_dicts:
        for axis, value in (d or {}).items():
            per_axis.setdefault(axis, set()).update(values_of(value))
    return {axis: _join(_specific(axis, vals)) for axis, vals in per_axis.items() if vals}


def _soft_pair(axis: str, tvals: Set[str], fvals: Set[str]) -> bool:
    """True when every stated value on both sides lies in one soft group of the axis."""
    for group in lexicon().soft_groups.get(axis, []):
        if tvals <= group and fvals <= group:
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


def matched_axes(target: Mapping[str, str], found: Mapping[str, str]) -> List[str]:
    """Target axes whose value set is stated identically in `found`."""
    return [
        axis for axis, tval in (target or {}).items()
        if tval and values_of(tval) == values_of((found or {}).get(axis))
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
