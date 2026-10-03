"""Deterministic query plan (decision D8): no LLM free-text queries.

build_queries(spec, custom_query=None) -> list[PlannedQuery], at most 4:
    Q1 (hl=en)  '{brand_en} {name words} {size}' (hl=ar with the Arabic brand when the
                sheet name itself is written in Arabic)
    Q2 (hl=ar)  '{brand_ar} {Arabic name words} {size_ar}', only when name_ar has Arabic text
    Q3 (hl=en)  Q1 scoped with site: OR over the brand's official domains and the UAE
                retailers; Serper only (providers_hint=('serper',))
    Q4 (hl=en)  '"{brand_en}" {gtin}', only when the GTIN is valid and global (and
                GTIN_POLICY is not 'off')
A staff custom_query REPLACES the plan: it is the only query, with query_id 'custom'.

relaxations(spec) -> [R1 (variant words dropped), R2 (size dropped)], flagged relaxed.

Name words are the sheet name's own words, in order, with every spelling of the
target brand removed (the brand is written exactly once, as a prefix; a spelling
glued or split differently, 'ALALALI' for 'AL ALALI', is the same brand) and, when the
SKU has a size, every size / pack expression removed (the size is appended once as
a normalised token such as '1L', '330ml' or '24x330ml'). Sub-brands and alternate
spellings that are not the canonical brand ('Nido' for Nestle) are kept. In an English
name the remaining words have known sheet shorthand written out for the search engine
('S/F OIL' -> 'SUNFLOWER OIL', 'L/MEAT' -> 'LIGHT MEAT'; catalog_match.abbreviations);
the Arabic Q2 and a custom query are never rewritten. The English brand loses stray
punctuation ('SUPER T/' -> 'SUPER T', 'SUPER/T' -> 'SUPER T') but never gains letters:
'SUP/T' is written 'SUP T', and naming it 'Super T' is the Brands Mapping sheet's job.
The bare GTIN is never a query on its own: a valid GTIN goes to the Open Food Facts
lookup and, with the brand, to Q4.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Set, Tuple

from . import abbreviations
from . import settings
from . import variants as variants_mod
from .gtin import is_restricted, normalize_gtin
from .models import PlannedQuery, Size, SkuSpec
from .sizes import parse_sizes
from .text_norm import alnum_len, tokens

logger = logging.getLogger(__name__)

MAX_PLANNED_QUERIES = 4
RETAILER_SITES = ("carrefouruae.com", "noon.com", "luluhypermarket.com", "amazon.ae", "talabat.com")
MAX_OFFICIAL_SITES = 2

_SPLIT_RE = re.compile(r"(?:[\s()\[\]{};|]|,(?!\d))+")   # keep '1,5L' (comma decimal) in one word
_EDGE_PUNCT = ".:-_'\"`"
_LATIN_RE = re.compile(r"[A-Za-z]")
_ARABIC_RE = re.compile("[؀-ۿ]")
_DIGITS_ONLY_RE = re.compile(r"[\d\s\-\"'.]+")
_BRAND_EDGE_PUNCT = _EDGE_PUNCT + "/\\|,;*#"           # 'SUPER T/' -> 'SUPER T'
_BRAND_SLASH_RE = re.compile(r"(?<=[^\W\d_])\s*/\s*(?=[^\W\d_])")   # 'SUPER/T' -> 'SUPER T'; '24/7' kept

# Pack indicators removed together with the size (the size token re-states the pack).
_PACK_UNITS = {"pcs", "pc", "pieces", "piece", "pack", "packs", "pk", "pkt", "s",
               "حبات", "حبه", "قطع", "قطعه", "عبوات", "عبوه"}


@dataclass(frozen=True)
class _Word:
    text: str                  # surface form (NFKC), kept for the query
    keys: Tuple[str, ...]      # match tokens (normalised, clitics stripped)


def _words(text: Optional[str]) -> List[_Word]:
    out: List[_Word] = []
    for part in _SPLIT_RE.split(unicodedata.normalize("NFKC", text or "")):
        part = part.strip(_EDGE_PUNCT)
        if not part:
            continue
        keys = tuple(tokens(part, strip_clitics=True))
        if keys:
            out.append(_Word(part, keys))
    return out


def _compact(phrase: Optional[str]) -> str:
    return "".join(tokens(phrase, strip_clitics=True))


def _has_latin(text: Optional[str]) -> bool:
    return bool(text) and bool(_LATIN_RE.search(text))


def _has_arabic(text: Optional[str]) -> bool:
    return bool(text) and bool(_ARABIC_RE.search(text))


def _flat(words: Sequence[_Word]) -> List[Tuple[str, int]]:
    return [(k, wi) for wi, w in enumerate(words) for k in w.keys]


def _mark_phrase(words: Sequence[_Word], phrase: str, marks: Set[int]) -> None:
    """Mark every word touched by an occurrence of phrase (token sequence) in words."""
    pkeys = tokens(phrase, strip_clitics=True)
    if not pkeys:
        return
    flat = _flat(words)
    n = len(pkeys)
    i = 0
    while i <= len(flat) - n:
        if all(flat[i + j][0] == pkeys[j] for j in range(n)):
            marks.update(flat[i + j][1] for j in range(n))
            i += n
        else:
            i += 1


def _mark_joined(words: Sequence[_Word], phrase: str, marks: Set[int]) -> None:
    """Mark whole words that spell the phrase with other word breaks ('ALALALI' for 'AL ALALI', and back)."""
    target = _compact(phrase)
    if not target:
        return
    for start in range(len(words)):
        joined = ""
        for end in range(start, len(words)):
            joined += "".join(words[end].keys)
            if not target.startswith(joined):
                break
            if joined == target:
                marks.update(range(start, end + 1))
                break


def _mark_packs(words: Sequence[_Word], marks: Set[int]) -> None:
    """Mark 'pack of N', 'N pcs', "N's" (and Arabic 'N حبات') expressions."""
    flat = _flat(words)
    for i, (key, wi) in enumerate(flat):
        nxt = flat[i + 1] if i + 1 < len(flat) else None
        if key == "pack" and nxt and nxt[0] == "of" and i + 2 < len(flat) and flat[i + 2][0].isdigit():
            marks.update((wi, nxt[1], flat[i + 2][1]))
        elif key.isdigit() and nxt and nxt[0] in _PACK_UNITS:
            marks.update((wi, nxt[1]))


# ---------------------------------------------------------------------------
# Brand and size tokens
# ---------------------------------------------------------------------------

def english_brand(spec: SkuSpec) -> str:
    """The Latin-script brand written in English queries ('' when none is known).

    Stray sheet punctuation is dropped ('SUPER T/' -> 'SUPER T') and a slash between
    letters becomes a space, as every search engine reads it ('SUPER/T' -> 'SUPER T',
    'SUP/T' -> 'SUP T'). Letters are never added or changed. A store spelling found by
    brand_discovery ('Rio Mare' for the sheet's 'RIO MARIE') is written first.
    """
    for phrase in tuple(spec.discovered_brands) + (spec.brand_canonical, spec.brand_raw) + tuple(spec.match_brands):
        if phrase and _has_latin(phrase) and not _has_arabic(phrase) and alnum_len(phrase) >= 2:
            return " ".join(_BRAND_SLASH_RE.sub(" ", phrase).split()).strip(_BRAND_EDGE_PUNCT + " ")
    return ""


def arabic_brand(spec: SkuSpec) -> str:
    """The Arabic-script brand written in the Arabic query ('' when none is known)."""
    for phrase in (spec.brand_ar, spec.brand_canonical, spec.brand_raw) + tuple(spec.match_brands):
        if phrase and _has_arabic(phrase):
            return " ".join(phrase.split())
    return ""


def _brand_spellings(spec: SkuSpec, brand_en: str, brand_ar: str) -> List[str]:
    """Every spelling of the target brand to strip from a name before prefixing the brand once.

    A match phrase counts as the same brand only when its compact form equals the
    canonical/English/Arabic brand's, so sub-brands such as 'Nido' stay in the query.
    A sheet brand shorter than 3 characters ('A/G') is always stripped.
    """
    base = [p for p in (brand_en, brand_ar, spec.brand_canonical) if p]
    base_keys = {_compact(p) for p in base if _compact(p)}
    out = list(base)
    for phrase in tuple(spec.match_brands) + (spec.brand_raw,):
        if not phrase:
            continue
        if _compact(phrase) in base_keys or alnum_len(phrase) < 3:
            out.append(phrase)
    uniq = list(dict.fromkeys(p for p in out if _compact(p)))
    return sorted(uniq, key=lambda p: -len(tokens(p)))


def _fmt(value: float) -> str:
    return f"{value:g}"


def _scaled(size: Size) -> Tuple[float, str, str]:
    """(value, English unit, Arabic unit) in the unit a label would use."""
    v = size.base_value
    if size.dimension == "volume":
        if v >= 1000 and abs(v / 1000 - round(v / 1000, 2)) < 1e-9:
            return v / 1000, "L", "لتر"
        return v, "ml", "مل"
    if v >= 1000 and abs(v / 1000 - round(v / 1000, 2)) < 1e-9:
        return v / 1000, "kg", "كجم"
    return v, "g", "غرام"


def size_token(size: Optional[Size], lang: str = "en") -> str:
    """Search-friendly size text: '1L', '330ml', '2.25kg', '24x330ml'; Arabic: '1 لتر'."""
    if size is None:
        return ""
    unit_text = " ".join((size.unit_text or "").split())
    ascii_text = bool(unit_text) and unit_text.isascii() and _has_latin(unit_text)
    if size.dimension == "count":
        n = int(size.base_value)
        if lang == "ar":
            return f"{n} حبة"
        return unit_text if ascii_text else f"{n} pcs"
    value, unit_en, unit_ar = _scaled(size)
    if lang != "ar" and ascii_text and abs(value - round(value, 2)) > 1e-9:
        return unit_text  # imperial sizes such as '12 fl oz' read better as written
    text = f"{_fmt(round(value, 2))}{unit_en}" if lang != "ar" else f"{_fmt(round(value, 2))} {unit_ar}"
    if size.pack_count and size.pack_count > 1:
        text = f"{size.pack_count}x{text}"
    return text


def display_gtin(gtin14: str) -> str:
    """The GTIN as printed on the pack: the shortest of GTIN-8/12/13/14 that holds it."""
    digits = gtin14.lstrip("0") or "0"
    for length in (8, 12, 13, 14):
        if len(digits) <= length:
            return digits.zfill(length)
    return gtin14


# ---------------------------------------------------------------------------
# Name analysis
# ---------------------------------------------------------------------------

@dataclass
class _NameParts:
    brand: str
    words: List[_Word]
    removed: Set[int]          # brand spellings, and size/pack expressions when the SKU has a size
    variant_idx: Set[int]      # words that name a variant ('Full', 'Fat', 'Strawberry')
    size_tok: str

    def text(self, drop_variants: bool = False, with_size: bool = True) -> str:
        drop = set(self.removed) | (self.variant_idx if drop_variants else set())
        parts = [self.brand] if self.brand else []
        parts += [w.text for i, w in enumerate(self.words) if i not in drop]
        if with_size and self.size_tok:
            parts.append(self.size_tok)
        return " ".join(p for p in parts if p).strip()

    lang: str = "en"

    def has_content(self) -> bool:
        return bool(self.brand) or self.has_words()

    def has_words(self, drop_variants: bool = False) -> bool:
        drop = set(self.removed) | (self.variant_idx if drop_variants else set())
        return any(i not in drop for i in range(len(self.words)))


def _expand_shorthand(words: List[_Word], removed: Set[int], context: str) -> Tuple[List[_Word], Set[int]]:
    """The words with sheet shorthand written out ('S/F OIL' -> 'SUNFLOWER OIL').

    Brand and size words are never rewritten, and a shorthand phrase never spans them.
    """
    out: List[_Word] = []
    out_removed: Set[int] = set()
    run: List[_Word] = []

    def flush() -> None:
        text = " ".join(w.text for w in run)
        expanded = abbreviations.expand(text, context)
        out.extend(run if expanded == text else _words(expanded))
        run.clear()

    for i, w in enumerate(words):
        if i in removed:
            flush()
            out_removed.add(len(out))
            out.append(w)
        else:
            run.append(w)
    flush()
    return out, out_removed


def _analyse(spec: SkuSpec, name: str, brand: str, spellings: Sequence[str], lang: str) -> _NameParts:
    words = _words(name)
    removed: Set[int] = set()
    for phrase in spellings:
        _mark_phrase(words, phrase, removed)
        _mark_joined(words, phrase, removed)
    if spec.size is not None:
        for found in parse_sizes(name, "query"):
            _mark_phrase(words, found.unit_text, removed)
        _mark_packs(words, removed)
    context = variants_mod.spec_context(spec)
    if lang == "en":
        words, removed = _expand_shorthand(words, removed, context)
    # The lexicon reads the shorthand and its written-out words alike ('S/F OIL', 'Sunflower Oil').
    var_keys = variants_mod.variant_tokens(" ".join(w.text for w in words), context)
    variant_idx = {
        i for i, w in enumerate(words)
        if i not in removed and var_keys and all(k in var_keys for k in w.keys)
    }
    return _NameParts(brand=brand, words=words, removed=removed, variant_idx=variant_idx,
                      size_tok=size_token(spec.size, lang), lang=lang)


def _primary_parts(spec: SkuSpec) -> _NameParts:
    """Q1 comes from the sheet name; an Arabic-script sheet name gives an Arabic Q1 (hl=ar)."""
    brand_en, brand_ar = english_brand(spec), arabic_brand(spec)
    lang = _lang_of(spec.raw_name) if spec.raw_name else "en"
    brand = (brand_ar or brand_en) if lang == "ar" else (brand_en or brand_ar)
    return _analyse(spec, spec.raw_name, brand, _brand_spellings(spec, brand_en, brand_ar), lang)


def _lang_of(text: str) -> str:
    arabic = len(_ARABIC_RE.findall(text))
    latin = len(_LATIN_RE.findall(text))
    return "ar" if arabic > latin else "en"


def _is_bare_number(text: str) -> bool:
    return bool(_DIGITS_ONLY_RE.fullmatch(text or "")) and any(ch.isdigit() for ch in text)


def _site_clause(spec: SkuSpec) -> str:
    sites: List[str] = []
    for d in tuple(spec.official_domains)[:MAX_OFFICIAL_SITES] + RETAILER_SITES:
        d = (d or "").strip().lower()
        if d and d not in sites:
            sites.append(d)
    return "(" + " OR ".join(f"site:{d}" for d in sites) + ")"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _custom(spec: SkuSpec, custom_query: str) -> List[PlannedQuery]:
    text = " ".join(custom_query.split())
    if _is_bare_number(text):
        gtin14, status = normalize_gtin(text.strip("\"' "))
        if status == "ok":
            # A bare barcode is never sent as an image query; keep the staff intent with the brand.
            brand = english_brand(spec) or arabic_brand(spec)
            prefix = f'"{brand}"' if brand else _primary_parts(spec).text(with_size=False)
            if not prefix:
                logger.warning("custom query %r is a bare GTIN and the SKU has no brand or name; skipped", text)
                return []
            text = f"{prefix} {display_gtin(gtin14)}"
    return [PlannedQuery(query_id="custom", text=text, hl=_lang_of(text))]


def build_queries(spec: SkuSpec, custom_query: Optional[str] = None) -> List[PlannedQuery]:
    """The ordered, deterministic query plan for one SKU (at most 4 queries)."""
    if custom_query and custom_query.strip():
        return _custom(spec, custom_query)

    plan: List[PlannedQuery] = []
    main = _primary_parts(spec)
    q1 = main.text() if main.has_content() else ""
    if q1 and alnum_len(q1) >= 2:
        plan.append(PlannedQuery(query_id="Q1", text=q1, hl=main.lang))
    else:
        q1 = ""

    if _has_arabic(spec.name_ar):
        brand_en, brand_ar = english_brand(spec), arabic_brand(spec)
        ar = _analyse(spec, spec.name_ar, brand_ar or brand_en,
                      _brand_spellings(spec, brand_en, brand_ar), "ar")
        q2 = ar.text() if ar.has_content() else ""
        if q2:
            plan.append(PlannedQuery(query_id="Q2", text=q2, hl="ar"))

    if q1:
        plan.append(PlannedQuery(query_id="Q3", text=f"{q1} {_site_clause(spec)}", hl=main.lang,
                                 providers_hint=("serper",)))

    # Q4 (GTIN query, identity package): only a valid, global barcode, always with the brand, and
    # never under GTIN_POLICY 'off'. Its answers are scored like any other: a listing of another
    # brand that prints the code gains nothing from it (score.py).
    gtin14, status = normalize_gtin(spec.gtin) if spec.gtin else (None, "missing")
    brand_q4 = english_brand(spec) or arabic_brand(spec)
    if (status == "ok" and gtin14 and brand_q4 and not is_restricted(gtin14)
            and settings.gtin_policy() != "off"):
        plan.append(PlannedQuery(query_id="Q4", text=f'"{brand_q4}" {display_gtin(gtin14)}',
                                 hl="ar" if _lang_of(brand_q4) == "ar" else "en"))

    return _clean(plan)[:MAX_PLANNED_QUERIES]


def relaxations(spec: SkuSpec) -> List[PlannedQuery]:
    """R1 (variant words dropped) and R2 (size dropped), each only when it differs from Q1."""
    main = _primary_parts(spec)
    if not main.has_content():
        return []
    q1 = main.text()
    out: List[PlannedQuery] = []
    # A relaxation that leaves only the brand ('Pepsi') would match every product of the brand.
    if spec.variants and main.variant_idx - main.removed and main.has_words(drop_variants=True):
        r1 = main.text(drop_variants=True)
        if r1 and r1 != q1:
            out.append(PlannedQuery(query_id="R1", text=r1, hl=main.lang, relaxed=True))
    if spec.size is not None and main.size_tok and main.has_words():
        r2 = main.text(with_size=False)
        if r2 and r2 != q1:
            out.append(PlannedQuery(query_id="R2", text=r2, hl=main.lang, relaxed=True))
    return _clean(out)


def _clean(plan: Iterable[PlannedQuery]) -> List[PlannedQuery]:
    """Drop empty texts, exact duplicates and anything that is only a number (a bare GTIN)."""
    out: List[PlannedQuery] = []
    seen: Set[str] = set()
    for q in plan:
        key = " ".join(q.text.split()).casefold()
        if not key or key in seen:
            continue
        if _is_bare_number(q.text):
            logger.warning("query %s %r is only a number; never sent as an image query", q.query_id, q.text)
            continue
        seen.add(key)
        out.append(q)
    return out
