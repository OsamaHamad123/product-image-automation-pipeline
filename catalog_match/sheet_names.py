"""Sheet product names read the way the stores write them (identity parsing and queries; never the key).

readable(name, context=None) -> str
    split_glued_sizes(), then the sheet compounds and typos of data/sheet_spellings.json.

The owner's sheet glues a size to the word before it and runs words together or truncates them:
'MASALA160 GM', 'WATE3X185GM', 'OIL3X185GM' (no size was parsed, and rows 50/51 lost their pack of 3),
'LIGHTMEAT', 'SOLIDTUNA SALTWATER', 'SALT WATE', 'CHICKN LUNCHENMEAT' (live run 2026-10-03, rows 32,
33, 38, 49-53). identity.build_sku_spec parses the size, the variants and the product-type words from
the readable name, and query_plan writes it; the sku_key is still built from the raw sheet name and
the size parsed from it (identity.make_sku_key), so nothing here can move an existing row's key.

* split_glued_sizes puts a space between a letter and a size glued to it: a number (or 'N x Q')
  directly followed by a unit of catalog_match.sizes and then by no letter. Only sizes are split:
  'T3', 'B12', 'OMEGA3' and '7UP' stay as written.
* The data file's keys match whole words (catalog_match.abbreviations.rewrite, the engine of the
  query shorthand), longest key first; every entry carries the live row it comes from.
"""

from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import FrozenSet, List, Optional, Tuple

from . import abbreviations
from .sizes import _UNIT
from .text_norm import tokens

logger = logging.getLogger(__name__)

SPELLINGS_PATH = Path(__file__).resolve().parent / "data" / "sheet_spellings.json"

# A size glued to the letter before it: 'WATER170GM', 'MASALA160 GM', 'WATE3X185GM', 'OIL3X185GM'
# (the 'x' of '3X185GM' is the pack's multiplication sign, not a word: it stays glued).
_GLUED_SIZE_RE = re.compile(
    r"(?<=[^\W\d_])(?<!\d[x×])(?=(?:\d{1,3}\s*[x×*]\s*)?\d+(?:[.,]\d+)?\s*" + _UNIT + r"(?![^\W\d_]))",
    re.IGNORECASE)


def split_glued_sizes(text: Optional[str]) -> str:
    """'WATE3X185GM' -> 'WATE 3X185GM'; every other character is kept as written."""
    if not text:
        return text or ""
    return _GLUED_SIZE_RE.sub(" ", text)


@lru_cache(maxsize=1)
def rules() -> Tuple[abbreviations.Rule, ...]:
    """Every entry of data/sheet_spellings.json as a rewrite rule, longest key first."""
    with open(SPELLINGS_PATH, "r", encoding="utf-8") as fh:
        raw = json.load(fh)
    contexts = abbreviations.load_contexts()
    out: List[abbreviations.Rule] = []
    for key, entry in (raw.get("entries") or {}).items():
        if not isinstance(entry, dict) or not str(entry.get("why") or "").strip():
            raise ValueError(f"sheet spellings: entry {key!r} needs 'reads' and a one-line 'why'")
        words = " ".join(str(entry.get("reads") or "").split())
        ctx_name = entry.get("context")
        if ctx_name and ctx_name not in contexts:
            raise ValueError(f"sheet spellings: entry {key!r} uses unknown context {ctx_name!r}")
        toks = tuple(tokens(key))
        if toks and words:
            out.append(abbreviations.Rule(key, toks, words, "sheet_spelling",
                                          contexts[ctx_name] if ctx_name else None))
    out.sort(key=lambda r: -len(r.toks))
    return tuple(out)


@lru_cache(maxsize=1)
def _vocabulary() -> FrozenSet[str]:
    return frozenset(t for r in rules() for t in r.toks)


def raw_context(raw_name: Optional[str], name_ar: Optional[str] = "", category: Optional[str] = "") -> str:
    """The sheet text that opens a context-bound entry: both names and the category, as written."""
    return " ".join(t for t in (raw_name, name_ar, category) if t)


def spec_name(spec) -> str:
    """The readable English (or main) name of a SkuSpec: what identity parsed and what Q1 writes."""
    raw = getattr(spec, "raw_name", "") or ""
    return readable(raw, raw_context(raw, getattr(spec, "name_ar", ""), getattr(spec, "category", "")))


@lru_cache(maxsize=4096)
def readable(name: Optional[str], context: Optional[str] = None) -> str:
    """The sheet name with glued sizes split and known compounds / typos written as the stores write them."""
    if not name:
        return name or ""
    split = split_glued_sizes(name)
    out = abbreviations.rewrite(split, context, rules(), _vocabulary())
    if out != split:
        # a fixed word may open a context-bound entry ('SOLIDTUNA' gives the 'tuna' that 'SALT WATE' needs)
        out = abbreviations.rewrite(out, context, rules(), _vocabulary())
    return out
