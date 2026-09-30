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
* matched_axes(target, found): axes whose value sets are identical.
* unstated_marked(target, found): axes the target does not state where the
  candidate states a 'marked' value (low fat, diet, decaf, a flavour). Scoring uses
  this to keep such candidates out of tier 1; it is never a hard reject.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Set, Tuple

from .text_norm import tokens

logger = logging.getLogger(__name__)

LEXICON_PATH = Path(__file__).resolve().parent / "data" / "variants_lexicon.json"
SEP = "+"


class _Lexicon:
    def __init__(self, raw: dict) -> None:
        # (phrase tokens, axis, value), longest phrase first
        entries: List[Tuple[Tuple[str, ...], str, str]] = []
        self.unmarked: Dict[str, Set[str]] = {}
        self.axes: Tuple[str, ...] = tuple(raw.get("axes", {}).keys())
        for axis, spec in raw.get("axes", {}).items():
            self.unmarked[axis] = set(spec.get("unmarked", []))
            for value, phrases in spec.get("values", {}).items():
                for phrase in phrases:
                    toks = tuple(tokens(phrase, strip_clitics=True))
                    if toks:
                        entries.append((toks, axis, value))
        entries.sort(key=lambda e: (-len(e[0]), -sum(len(t) for t in e[0])))
        self.entries = entries
        # every token that belongs to some phrase, for a cheap pre-filter
        self.vocabulary = {t for toks, _, _ in entries for t in toks}


@lru_cache(maxsize=1)
def lexicon() -> _Lexicon:
    with open(LEXICON_PATH, "r", encoding="utf-8") as fh:
        return _Lexicon(json.load(fh))


def _scan(text: Optional[str]) -> List[Tuple[str, str, Tuple[int, int]]]:
    """Return (axis, value, token span) for every lexicon phrase in text, longest first, no overlaps."""
    toks = tokens(text, strip_clitics=True)
    if not toks:
        return []
    lex = lexicon()
    if not lex.vocabulary.intersection(toks):
        return []
    used = [False] * len(toks)
    hits: List[Tuple[str, str, Tuple[int, int]]] = []
    for phrase, axis, value in lex.entries:
        n = len(phrase)
        if n > len(toks):
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


def extract_variants(text: Optional[str]) -> Dict[str, str]:
    """{axis: value} for every variant axis stated in the text."""
    per_axis: Dict[str, Set[str]] = {}
    for axis, value, _ in _scan(text):
        per_axis.setdefault(axis, set()).add(value)
    return {axis: _join(vals) for axis, vals in per_axis.items()}


def variant_tokens(text: Optional[str]) -> Set[str]:
    """The (clitic-stripped) tokens of the text that belong to a variant phrase."""
    toks = tokens(text, strip_clitics=True)
    out: Set[str] = set()
    for _, _, (a, b) in _scan(text):
        out.update(toks[a:b])
    return out


def merge(*variant_dicts: Mapping[str, str]) -> Dict[str, str]:
    """Union of several {axis: value} dicts (e.g. English and Arabic name)."""
    per_axis: Dict[str, Set[str]] = {}
    for d in variant_dicts:
        for axis, value in (d or {}).items():
            per_axis.setdefault(axis, set()).update(values_of(value))
    return {axis: _join(vals) for axis, vals in per_axis.items() if vals}


def conflicts(target: Mapping[str, str], found: Mapping[str, str]) -> List[str]:
    """Axes stated on both sides whose value sets are disjoint."""
    out = []
    for axis, tval in (target or {}).items():
        fval = (found or {}).get(axis)
        if not fval or not tval:
            continue
        if values_of(tval).isdisjoint(values_of(fval)):
            out.append(axis)
    return out


def matched_axes(target: Mapping[str, str], found: Mapping[str, str]) -> List[str]:
    """Target axes whose value set is stated identically in `found`."""
    return [
        axis for axis, tval in (target or {}).items()
        if tval and values_of(tval) == values_of((found or {}).get(axis))
    ]


def unstated_marked(target: Mapping[str, str], found: Mapping[str, str]) -> List[str]:
    """Axes absent from the target where `found` states a marked (non-default) value."""
    lex = lexicon()
    out = []
    for axis, fval in (found or {}).items():
        if (target or {}).get(axis):
            continue
        if values_of(fval) - lex.unmarked.get(axis, set()):
            out.append(axis)
    return out
