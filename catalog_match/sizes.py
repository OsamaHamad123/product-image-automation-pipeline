"""Net-content and pack-count parsing for grocery titles, slugs and sheet cells.

parse_sizes(text, source_field) -> list[Size]
    Grammar (English and Arabic, on normalised text):
      'N x Q unit', 'NxQunit', 'N cans x Q unit'  -> pack N of Q
      'Q unit x N'                                 -> pack N of Q
      'Q unit' + '(pack of N)' / 'N pack' / "N's" / 'twin pack' / 'N+M free' -> pack N of Q
      'Q ml' + 'N pcs'                             -> pack N of Q (volume)
      'Q g' + 'N pcs'                              -> Q with pieces=N (pieces in one box or a
                                                      pack: compare_pack calls it ambiguous)
      "N's Q g" (N's written BEFORE a net mass)    -> Q with pieces=N, like 'N pcs':
                                                      'PARATHA 5S 400GM' is 5 pieces in 400 g,
                                                      'LAYS 6'S 23G' is six 23 g bags
      'N pcs' / 'N bags' ... with no measured size -> a count
      '1/2 kg', '½ L', '1 1/2 kg'                  -> fractions
      '2.5-3 kg'                                   -> a range: two sizes, so 'ambiguous'
    Decimals use '.' or ','; ',ddd' is a thousands separator.
    Numbers that are nutrient amounts ('10g fibre', 'per 100g', '30g per serving')
    are ignored.

product_size(sizes) -> Size | None | 'ambiguous'
compare(target, found, tol=0.03) -> 'match' | 'conflict' | 'ambiguous' | 'unknown'
compare_pack(target_pack, found) -> 'match' | 'conflict' | 'ambiguous' | 'unknown'

Each evidence field (title, page title, page URL, image URL) must be parsed on
its own: concatenating them invents sizes ('/p/1' + 'g.jpg' reads as '1 g').
"""

from __future__ import annotations

import logging
import re
from dataclasses import replace
from typing import Iterable, List, Optional, Sequence, Tuple, Union

from .models import Size
from .text_norm import normalize

logger = logging.getLogger(__name__)

AMBIGUOUS = "ambiguous"
DEFAULT_TOL = 0.03

# unit spelling -> (dimension, factor to ml / g)
_UNITS = {
    # volume
    "ml": ("volume", 1.0), "mls": ("volume", 1.0),
    "millilitre": ("volume", 1.0), "millilitres": ("volume", 1.0),
    "milliliter": ("volume", 1.0), "milliliters": ("volume", 1.0),
    "cl": ("volume", 10.0),
    "l": ("volume", 1000.0), "lt": ("volume", 1000.0), "ltr": ("volume", 1000.0),
    "ltrs": ("volume", 1000.0), "litre": ("volume", 1000.0), "litres": ("volume", 1000.0),
    "liter": ("volume", 1000.0), "liters": ("volume", 1000.0),
    "fl oz": ("volume", 29.5735), "floz": ("volume", 29.5735), "fl. oz": ("volume", 29.5735),
    "مل": ("volume", 1.0), "ملل": ("volume", 1.0), "مللي": ("volume", 1.0), "ملي": ("volume", 1.0),
    "مليلتر": ("volume", 1.0), "ملليلتر": ("volume", 1.0),
    "لتر": ("volume", 1000.0), "ليتر": ("volume", 1000.0), "لترات": ("volume", 1000.0),
    "ل": ("volume", 1000.0),
    # mass
    "g": ("mass", 1.0), "gm": ("mass", 1.0), "gms": ("mass", 1.0), "gr": ("mass", 1.0),
    "grs": ("mass", 1.0), "gram": ("mass", 1.0), "grams": ("mass", 1.0),
    "gramme": ("mass", 1.0), "grammes": ("mass", 1.0),
    "kg": ("mass", 1000.0), "kgs": ("mass", 1000.0), "kilo": ("mass", 1000.0),
    "kilos": ("mass", 1000.0), "kilogram": ("mass", 1000.0), "kilograms": ("mass", 1000.0),
    "oz": ("mass", 28.3495), "lb": ("mass", 453.592), "lbs": ("mass", 453.592),
    "غ": ("mass", 1.0), "غم": ("mass", 1.0), "غرام": ("mass", 1.0), "جم": ("mass", 1.0),
    "جرام": ("mass", 1.0), "جرامات": ("mass", 1.0), "غرامات": ("mass", 1.0),
    "كجم": ("mass", 1000.0), "كغ": ("mass", 1000.0), "كغم": ("mass", 1000.0),
    "كيلو": ("mass", 1000.0), "كيلوغرام": ("mass", 1000.0), "كيلوجرام": ("mass", 1000.0),
    "كيلو غرام": ("mass", 1000.0), "كيلو جرام": ("mass", 1000.0),
}


def _unit_alternation() -> str:
    alts = []
    for u in sorted(_UNITS, key=len, reverse=True):
        alts.append(re.escape(u).replace(r"\ ", r"\s*"))
    return "(?:" + "|".join(alts) + ")"


_UNIT = _unit_alternation()
_NUM = r"\d+(?:[.,]\d+)?"
_END = r"(?![^\W\d_])"          # not followed by a letter
_START = r"(?<![\w.])"          # not preceded by a letter/digit/dot
_TIMES = r"[x×*]"
_CONTAINER = r"(?:\s*(?:cans?|bottles?|pcs|pieces?|packs?|pouch(?:es)?|cups?|boxes|bags|tins?|jars?|علب|علبه|عبوات|عبوه|زجاجات|حبات|حبه))?"

_MULTI_A = re.compile(
    _START + r"(?P<n>\d{1,3})" + _CONTAINER + r"\s*" + _TIMES + r"\s*(?P<q>" + _NUM + r")\s*(?P<u>" + _UNIT + r")" + _END
)
_MULTI_B = re.compile(
    _START + r"(?P<q>" + _NUM + r")\s*(?P<u>" + _UNIT + r")\s*" + _TIMES + r"\s*(?P<n>\d{1,3})(?![\w.])"
)
_SINGLE = re.compile(_START + r"(?P<q>" + _NUM + r")\s*(?P<u>" + _UNIT + r")" + _END)

# Pack indicators: they multiply a measured size ('pack of 6', '6 pack', "6's").
_PACK = re.compile(
    r"pack\s*of\s*(?P<a>\d{1,3})(?!\d)"
    r"|" + _START + r"(?P<b>\d{1,3})\s*[-]?\s*(?:packs|pack|pk|pkt)" + _END
    + r"|" + _START + r"(?P<c>\d{1,3})['’]?s" + _END
    + r"|" + _START + r"(?P<d>\d{1,3})\s*(?:عبوات|عبوه)" + _END
    + r"|(?:عبوه|علبه)\s*(?:من|فيها)?\s*(?P<e>\d{1,3})" + r"(?!\d)"
)
# Piece indicators ('16 pcs', '16 pieces'). With a volume they are a pack ('Laban 180ml
# 6 pcs'); with a net MASS they usually count the pieces inside one box ('Ferrero Rocher
# 16 pcs 200g'), so they are kept as Size.pieces and compared as ambiguous, never as a pack.
_PIECES = re.compile(
    _START + r"(?P<a>\d{1,3})\s*[-]?\s*(?:pcs|pc|pieces|piece)" + _END
    + r"|" + _START + r"(?P<b>\d{1,3})\s*(?:حبات|حبه|قطع|قطعه)" + _END
)
# Worded packs ('twin pack') and bonus packs ('4+1 free' is 5 units).
_WORD_PACK = re.compile(r"(?<![^\W\d_])(?P<w>twin|double|triple)\s*[-]?\s*pack" + _END)
_WORD_PACK_N = {"twin": 2, "double": 2, "triple": 3}
_PLUS_FREE = re.compile(_START + r"(?P<a>\d{1,2})\s*\+\s*(?P<b>\d{1,2})\s*(?:free|مجانا|مجاني)" + _END)

# Fractions ('1/2 kg', NFKC turns '½' into '1⁄2') and ranges ('2.5-3 kg') before a unit.
_FRACTION_RE = re.compile(
    _START + r"(?:(?P<w>\d{1,3})\s+)?(?P<n>\d{1,2})\s*[/⁄]\s*(?P<d>\d{1,2})(?=\s*" + _UNIT + _END + ")"
)
_RANGE_RE = re.compile(
    _START + r"(?P<a>" + _NUM + r")\s*[-–]\s*(?P<b>" + _NUM + r")\s*(?P<u>" + _UNIT + r")" + _END
)
_PACK_WORD_RE = re.compile(r"pack|pcs|pc\b|piece|pk|حب|قطع|عبو|علب")
# Content counts: the product itself is counted ('100 tea bags', '30 capsules').
_CONTENT_COUNT = re.compile(
    _START + r"(?P<n>\d{1,4})\s*(?:tea\s*bags|teabags|bags|sachets|capsules|pods|tablets|rolls|sheets|count|ct|eggs|كيس|اكياس|كبسوله|كبسولات)" + _END
)

_NUTRIENT_AFTER = re.compile(
    r"^\s*(?:of\s+)?(?:fib(?:re|er)s?|protein|fat(?!\s*free)|sugars?(?!\s*free)|carbs?|carbohydrates?"
    r"|sodium|kcal|cal|calories|energy|per\s+serving|serving|portion|ألياف|الياف|بروتين|دهون|سكر|سعرات)"
    r"|^\s*(?:/|per)\s*(?:serving|portion)"
)
_CONTEXT_BEFORE = re.compile(r"(?:\bper|لكل|serving(?:\s*size)?\s*[:=]?|حصه)\s*$")
_NUTRIENT_MASS_MAX = 100.0  # nutrient amounts are small masses; never drop a 500 g net weight


def _to_float(num: str) -> Optional[float]:
    num = num.strip()
    if "," in num:
        whole, frac = num.split(",", 1)
        num = whole + frac if len(frac) == 3 else whole + "." + frac  # ',ddd' = thousands
    try:
        return float(num)
    except ValueError:
        return None


def _unit_info(unit_text: str) -> Optional[Tuple[str, float]]:
    key = re.sub(r"\s+", " ", unit_text.strip())
    if key in _UNITS:
        return _UNITS[key]
    compact = key.replace(" ", "")
    for name, info in _UNITS.items():
        if name.replace(" ", "") == compact:
            return info
    return None


def _overlaps(span: Tuple[int, int], taken: List[Tuple[int, int]]) -> bool:
    return any(span[0] < e and s < span[1] for s, e in taken)


def _make(q: str, unit: str, pack: Optional[int], text: str, source_field: str) -> Optional[Size]:
    value = _to_float(q)
    info = _unit_info(unit)
    if value is None or info is None or value <= 0:
        return None
    dimension, factor = info
    pack = pack if pack and pack > 1 else None
    return Size(dimension=dimension, base_value=round(value * factor, 3), unit_text=text.strip(),
                pack_count=pack, source_field=source_field)


def _is_nutrient(t: str, start: int, end: int, size: Size) -> bool:
    if _CONTEXT_BEFORE.search(t[max(0, start - 16):start]):
        return True
    if size.dimension == "mass" and size.base_value <= _NUTRIENT_MASS_MAX:
        return bool(_NUTRIENT_AFTER.match(t[end:end + 24]))
    return False


def _fraction(m: "re.Match[str]") -> str:
    num, den = int(m.group("n")), int(m.group("d"))
    if den == 0 or num >= den:
        return m.group(0)
    value = int(m.group("w") or 0) + num / den
    return f"{value:g}"


def _prepare(t: str) -> str:
    """Rewrite fractions to decimals and 'a-b unit' ranges to two sizes (so the text is ambiguous)."""
    t = _FRACTION_RE.sub(_fraction, t)
    return _RANGE_RE.sub(lambda m: f"{m.group('a')} {m.group('u')} / {m.group('b')} {m.group('u')}", t)


def parse_sizes(text: Optional[str], source_field: str = "") -> List[Size]:
    """Parse every net-content statement in one evidence field."""
    t = normalize(text)
    if not t or not any(ch.isdigit() for ch in t):
        return []
    t = _prepare(t)
    taken: List[Tuple[int, int]] = []
    found: List[Tuple[int, Size]] = []

    for rx in (_MULTI_A, _MULTI_B):
        for m in rx.finditer(t):
            if _overlaps(m.span(), taken):
                continue
            size = _make(m.group("q"), m.group("u"), int(m.group("n")), m.group(0), source_field)
            if size is not None:
                taken.append(m.span())
                found.append((m.start(), size))

    for m in _SINGLE.finditer(t):
        if _overlaps(m.span(), taken):
            continue
        size = _make(m.group("q"), m.group("u"), None, m.group(0), source_field)
        if size is None:
            continue
        taken.append(m.span())
        if _is_nutrient(t, m.start(), m.end(), size):
            logger.debug("ignored nutrient amount %r in %r", m.group(0), source_field)
            continue
        found.append((m.start(), size))

    packs: List[Tuple[int, int, str, str]] = []          # (start, n, raw text, 'pack' | 'pieces' | 'n_s')
    for rx, kind in ((_PACK, "pack"), (_PIECES, "pieces")):
        for m in rx.finditer(t):
            if _overlaps(m.span(), taken):
                continue
            n = next(int(g) for g in m.groups() if g)
            # "N's": a pack after a size ('75G 5S'); before a net mass it may count the pieces in one pack
            packs.append((m.start(), n, m.group(0), "n_s" if kind == "pack" and m.group("c") else kind))
    for m in _WORD_PACK.finditer(t):
        packs.append((m.start(), _WORD_PACK_N[m.group("w")], m.group(0), "pack"))
    for m in _PLUS_FREE.finditer(t):
        packs.append((m.start(), int(m.group("a")) + int(m.group("b")), f"pack {m.group(0)}", "pack"))

    ordered = sorted(found, key=lambda x: x[0])
    if ordered:
        out: List[Size] = []
        for pos, s in ordered:
            # "N's" before a net mass reads like 'N pcs' ('PARATHA 5S 400GM': 5 pieces, 400 g in all, or
            # five 400 g packs?), so it is ambiguous; after the size, or with a volume, it is a pack.
            pack_ns = {n for p, n, _, kind in packs
                       if n > 1 and (kind == "pack" or (kind == "n_s" and (p > pos or s.dimension != "mass")))}
            piece_ns = {n for p, n, _, kind in packs
                        if n > 1 and (kind == "pieces" or (kind == "n_s" and p < pos and s.dimension == "mass"))}
            if s.pack_count:
                out.append(s)
            elif s.dimension == "mass":
                if len(pack_ns) == 1:
                    out.append(replace(s, pack_count=next(iter(pack_ns))))
                elif not pack_ns and len(piece_ns) == 1:
                    out.append(replace(s, pieces=next(iter(piece_ns))))
                else:
                    out.append(s)
            else:
                both = pack_ns | piece_ns
                out.append(replace(s, pack_count=next(iter(both))) if len(both) == 1 else s)
        return out

    counts: List[Size] = []
    for _, n, raw, _kind in packs:
        if n > 0:
            counts.append(Size("count", float(n), raw.strip(), None, source_field))
    for m in _CONTENT_COUNT.finditer(t):
        if _overlaps(m.span(), taken):
            continue
        counts.append(Size("count", float(int(m.group("n"))), m.group(0).strip(), None, source_field))
    return counts


def is_pack_count(size: Size) -> bool:
    """A 'count' size that states a number of packs ('6 pcs') rather than contents ('100 bags')."""
    return size.dimension == "count" and bool(_PACK_WORD_RE.search(size.unit_text))


def _same_value(a: float, b: float, tol: float) -> bool:
    hi = max(a, b)
    return hi <= 0 or abs(a - b) / hi <= tol


def _distinct(sizes: Sequence[Size], tol: float, with_pack: bool) -> List[Size]:
    groups: List[Size] = []
    for s in sizes:
        for g in groups:
            if g.dimension == s.dimension and _same_value(g.base_value, s.base_value, tol) and (
                not with_pack or (g.pack_count or 1) == (s.pack_count or 1)
            ):
                break
        else:
            groups.append(s)
    return groups


def product_size(sizes: Sequence[Size], tol: float = DEFAULT_TOL) -> Union[Size, None, str]:
    """The single size a text states, None when it states none, 'ambiguous' when several."""
    if not sizes:
        return None
    groups = _distinct(list(sizes), tol, with_pack=True)
    if len(groups) > 1:
        return AMBIGUOUS
    return groups[0]


def compare(target: Optional[Size], found: Sequence[Size], tol: float = DEFAULT_TOL) -> str:
    """Compare the per-unit net content of the target with the sizes found in ONE field.

    Only sizes of the target's dimension are compared (a volume is never compared
    with a mass). Several distinct values give 'ambiguous' (a listing of 5/10/20 kg),
    one differing value by more than `tol` gives 'conflict'. Pack counts are
    compared separately by compare_pack().
    """
    if target is None or not found:
        return "unknown"
    same_dim = [s for s in found if s.dimension == target.dimension]
    if not same_dim:
        return "unknown"
    groups = _distinct(same_dim, tol, with_pack=False)
    if len(groups) > 1:
        return AMBIGUOUS
    return "match" if _same_value(groups[0].base_value, target.base_value, tol) else "conflict"


def compare_pack(target_pack: Optional[int], found: Sequence[Size], target_pieces: Optional[int] = None) -> str:
    """Compare pack counts. A missing target pack means a single unit.

    target_pieces is the SKU's own 'N pcs' next to a net mass ('Kinder Bueno 43g 6 pcs'):
    one box of N pieces or an N-pack, so the same wording matches, a single unit or an
    N-pack is ambiguous (review, never a hard reject) and any other pack conflicts.
    """
    target_n = target_pack if target_pack and target_pack > 1 else 1
    explicit = set()
    loose = False
    pieces = set()
    for s in found:
        if s.dimension == "count":
            if is_pack_count(s) and s.base_value >= 1:
                explicit.add(int(s.base_value))
            continue
        if s.pack_count and s.pack_count > 1:
            explicit.add(s.pack_count)
        else:
            loose = True
            if getattr(s, "pieces", None):
                pieces.add(s.pieces)
    if target_n == 1 and target_pieces and target_pieces > 1:
        if explicit - {1, target_pieces}:
            return "conflict"
        if not explicit and pieces == {target_pieces}:
            return "match"
        return AMBIGUOUS
    if not explicit:
        # '16 pcs 200g': pieces inside one box, or a 16-pack? Never a conflict, never a match.
        return AMBIGUOUS if pieces else "unknown"
    if target_n > 1:
        if explicit == {target_n}:
            return "match"
        return AMBIGUOUS if target_n in explicit else "conflict"
    # single-unit target
    return AMBIGUOUS if loose else "conflict"


def parse_first(texts: Iterable[Tuple[str, str]]) -> Union[Size, None, str]:
    """product_size of the first (text, source_field) pair that states a size."""
    for text, field_name in texts:
        ps = product_size(parse_sizes(text, field_name))
        if ps is not None:
            return ps
    return None
