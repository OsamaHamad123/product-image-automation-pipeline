"""Sheet shorthand written out for search engines ('TUNA S/F OIL' -> 'TUNA SUNFLOWER OIL').

expand(text, context=None) -> str
    Every key of data/abbreviations.json found in the text is replaced by its search
    words; every other character is kept exactly as written.

* Keys match whole words on normalised tokens (catalog_match.text_norm): 'S/F OIL',
  's/f oil' and 'S.F OIL' are one key, while '5S', 'S/FOIL' and the 'S/F' of 'W/S/F'
  are not words of their own. Inside a key the words may be joined only by spaces,
  '/', '.', '-' or '\\'; a bracket or a comma ends it.
* The longest key wins and matches never overlap: 'WITH VEG OIL' is vegetable oil,
  'WITH VEG' is vegetables.
* A group with a context (a token set of variants_lexicon.json 'contexts') expands only
  when the text or the `context` text has a token of it: 'SUN OIL' is sunflower oil on
  a tuna can only.
* The words take the case style of the sheet text: upper-case text gets upper-case words.

Only query text is expanded; identity reads the same shorthand through the variant lexicon.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import FrozenSet, List, Optional, Tuple

from .text_norm import normalize, tokens
from .variants import LEXICON_PATH

logger = logging.getLogger(__name__)

ABBREVIATIONS_PATH = Path(__file__).resolve().parent / "data" / "abbreviations.json"

_TOKEN_RE = re.compile(r"\d+(?:\.\d+)?|[^\W\d_]+")    # text_norm's tokens, with their positions
_JOIN_RE = re.compile(r"[\s./\\-]+")                    # what may stand between the words of a key
_ABBREV_MARKS = "/\\"                                   # a slash next to a word continues the abbreviation


@dataclass(frozen=True)
class Rule:
    key: str                              # as written in the file ('S/F OIL')
    toks: Tuple[str, ...]                 # its match tokens ('s', 'f', 'oil')
    words: str                            # the search words ('Sunflower Oil')
    group: str
    context: Optional[FrozenSet[str]]     # tokens of the group's context; None = every name


@lru_cache(maxsize=1)
def rules() -> Tuple[Rule, ...]:
    """Every rule of the data file, longest key first (file order on a tie)."""
    with open(ABBREVIATIONS_PATH, "r", encoding="utf-8") as fh:
        raw = json.load(fh)
    contexts = load_contexts()
    out: List[Rule] = []
    for group, spec in (raw.get("groups") or {}).items():
        ctx_name = spec.get("context")
        if ctx_name and ctx_name not in contexts:
            raise ValueError(f"abbreviations: group {group!r} uses unknown context {ctx_name!r}")
        for key, words in (spec.get("expand") or {}).items():
            toks = tuple(tokens(key))
            if toks and words.strip():
                out.append(Rule(key, toks, " ".join(words.split()), group,
                                contexts[ctx_name] if ctx_name else None))
    out.sort(key=lambda r: -len(r.toks))
    return tuple(out)


@lru_cache(maxsize=1)
def _vocabulary() -> FrozenSet[str]:
    """Every token of some key, for a cheap pre-filter."""
    return frozenset(t for r in rules() for t in r.toks)


def _is_edge(text: str, i: int) -> bool:
    """True when position i of text is not inside a word or an abbreviation ('W/S', '5S')."""
    return not (0 <= i < len(text)) or not (text[i].isalnum() or text[i] in _ABBREV_MARKS)


def expand(text: Optional[str], context: Optional[str] = None) -> str:
    """The text with every known sheet shorthand written out (see the module docstring)."""
    return rewrite(text, context, rules(), _vocabulary())


def load_contexts() -> dict:
    """The token sets of variants_lexicon.json 'contexts', by name (a rule's 'context' names one)."""
    with open(LEXICON_PATH, "r", encoding="utf-8") as fh:
        return {name: frozenset(t for word in words for t in tokens(word, strip_clitics=True))
                for name, words in (json.load(fh).get("contexts") or {}).items()}


def rewrite(text: Optional[str], context: Optional[str], rule_set: Tuple[Rule, ...],
            vocabulary: FrozenSet[str]) -> str:
    """The matching engine of expand(): every rule of `rule_set` (longest key first) replaced by its words.

    catalog_match.sheet_names runs the same engine over its own data file (sheet compounds and typos).
    """
    if not text:
        return text or ""
    found = [(m.start(), m.end(), normalize(m.group())) for m in _TOKEN_RE.finditer(text)]
    keys = [k for _, _, k in found]
    if vocabulary.isdisjoint(keys):
        return text
    present = set(tokens(text, strip_clitics=True)) | set(tokens(context, strip_clitics=True))
    used = [False] * len(found)
    spans: List[Tuple[int, int, str]] = []
    for rule in rule_set:
        n = len(rule.toks)
        if rule.context is not None and rule.context.isdisjoint(present):
            continue
        for i in range(len(found) - n + 1):
            if tuple(keys[i:i + n]) != rule.toks or any(used[i:i + n]):
                continue
            start, end = found[i][0], found[i + n - 1][1]
            if not (_is_edge(text, start - 1) and _is_edge(text, end)):
                continue
            if not all(_JOIN_RE.fullmatch(text[found[j][1]:found[j + 1][0]]) for j in range(i, i + n - 1)):
                continue
            surface = text[start:end]
            spans.append((start, end, rule.words.upper() if surface.isupper() else rule.words))
            for j in range(i, i + n):
                used[j] = True
    if not spans:
        return text
    out, pos = [], 0
    for start, end, words in sorted(spans):
        out.append(text[pos:start])
        out.append(words)
        pos = end
    out.append(text[pos:])
    return "".join(out)
