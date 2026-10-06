"""Sheet row -> SkuSpec: everything we know about the product we are looking for.

build_sku_spec(row, brand_mappings, size_text=None)
    row keys follow the search facade kwargs: name, name_ar, brand, brand_ar,
    barcode, category, size ('name_en' / 'product_name' are accepted for name).

* raw_name is kept exactly as the sheet has it; nothing here rewrites it.
* brand goes through the reverse brand index (mapped / sheet_raw / none). A brand cell that only says the
  product has none ('GENERIC / NO BRAND', 'N/A'; brand_index.is_placeholder_brand) is an empty cell: no brand
  in the queries, the matching or the label prompt (brand_conf 'none'); the cell is kept in brand_placeholder
  and in the sku_key.
* an unmapped (sheet_raw) brand whose last word(s) begin a variant phrase that the name continues right after
  the brand is matched without them: 'AMERICAN LIGHT' in 'AMERICAN LIGHT MEAT TUNA' is matched as 'AMERICAN'
  ('LIGHT MEAT' is the tuna's meat grade, catalog_match.variants), so a label reading 'American' is the target
  brand. brand_raw keeps the cell; nothing is trimmed from a mapped or learned brand, inside a word the cell
  writes together ('X/LIGHT'), or down to a phrase evidence cannot be matched on ('SQ': brand_index.
  matchable_brand). A brand that only holds such a word ('SUPER WHITE' before 'WHITE MEAT') stays whole: the
  phrase must start in the brand and end after it.
* size, variants and class_tokens are parsed from the READABLE names
  (catalog_match.sheet_names: a size glued to a word split off, 'MASALA160 GM' ->
  'MASALA 160 GM'; sheet compounds and typos fixed, 'SOLIDTUNA' -> 'SOLID TUNA').
* size is the size column merged with the name (see _pick_size): a pack stated only
  in the name, or a measure stated only in the name, is never dropped.
* class_tokens are the product-type words left after removing brand, size (and unit
  words, 'mm' included: '9MM' fries are a cut, not a product word), variant and stop
  words ('Almarai Full Fat Milk 1L' -> ('milk',)).
* sku_key is the GTIN-14 when the barcode is valid, otherwise
  sha1(norm sheet brand | norm raw name | size canonical)[:16]. The sheet brand is
  the raw brand cell (or the Arabic brand cell), never the mapping-derived canonical
  brand, so the key is stable when the Brands Mapping sheet changes or fails to load.
  The size in the key is parsed from the RAW names, never from the readable ones: a
  new spelling fix or size rule never moves an existing row's key.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import replace
from typing import Any, List, Mapping, Optional, Sequence, Set, Tuple, Union

from . import sheet_names
from . import variants as variants_mod
from .brand_index import BrandIndex, BrandResolution, build_index, is_placeholder_brand, matchable_brand
from .gtin import normalize_gtin
from .models import Size, SkuSpec
from .sizes import compare, is_pack_count, parse_sizes, product_size
from .text_norm import is_arabic, match_key, normalize, strip_arabic_clitics, tokens

logger = logging.getLogger(__name__)

_STOPWORDS_RAW = (
    # English function words and packaging / marketing words
    "a an the of and with in for by from to on at or new pack pcs pc piece pieces x"
    " can cans bottle bottles jar jars pouch pouches carton cartons tin tins box boxes"
    " bag bags packet packets sachet sachets tetra pet plastic glass value offer promo"
    " free buy online uae price size net wt weight approx each only special edition"
    # Arabic
    " و مع من في على عن الى او عبوه عبوات علبه علب زجاجه كرتون كيس اكياس قطعه قطع حبه حبات"
    " عرض جديد نكهه بنكهه طعم حجم وزن صافي"
)
_UNIT_WORDS_RAW = (
    "ml mls cl l lt ltr ltrs litre litres liter liters g gm gms gr grs gram grams kg kgs kilo"
    " kilos kilogram kilograms oz lb lbs fl floz pk pkt ct"
    # a length: '9MM' / '900 MM' fries (live run 2026-10-03, rows 4, 5, 12) is the cut, never a product word
    " mm"
    " مل ملل مللي ملي لتر ليتر لترات ل غ غم غرام جم جرام كجم كغ كغم كيلو كيلوغرام كيلوجرام"
)


def _token_set(raw: str) -> Set[str]:
    return {strip_arabic_clitics(t) for t in tokens(raw)}


_STOPWORDS = _token_set(_STOPWORDS_RAW)
_UNIT_WORDS = _token_set(_UNIT_WORDS_RAW)
# words that never name the product (function, packaging and unit words): query_plan compares two queries without them
FILLER_WORDS = frozenset(_STOPWORDS | _UNIT_WORDS)


def _first(row: Mapping[str, Any], *keys: str) -> str:
    for k in keys:
        v = row.get(k)
        if v is not None and str(v).strip():
            return str(v)
    return ""


def _one_size(text: Optional[str], source: str) -> Union[Size, None, str]:
    return product_size(parse_sizes(text, source)) if text else None


def _pick_size(size_text: Optional[str], name: str, name_ar: str) -> Optional[Size]:
    """The SKU's size: the size column merged with what the name says.

    The size column often holds only the per-unit size ('330ml') while the name
    carries the pack ('Pepsi Cola Can 330ml x 6'), or only a count ('6 pcs') while the
    name carries the measure ('Almarai Milk 1L x 6'). Dropping either half makes the
    correct multipack a hard reject and the single unit tier 1, so they are merged:
      * column measured, no pack + name states the same measure with a pack -> column + pack;
      * column is a pack count ('6 pcs') + name states a measure -> name measure x count;
      * column counts the units ('170 PCS') + name states the same count in packs ('5X170PCS') -> column + pack;
      * column and name measured in different dimensions -> the name wins;
      * otherwise the column wins; with no usable column, the English then Arabic name.
    """
    col = _one_size(size_text, "sheet_size")
    if col is not None and not isinstance(col, Size):
        logger.debug("sheet_size states several sizes (%r); using the name", size_text)
        col = None
    name_size: Optional[Size] = None
    for text, source in ((name, "name"), (name_ar, "name_ar")):
        ps = _one_size(text, source)
        if isinstance(ps, Size):
            name_size = ps
            break
        if ps is not None:
            logger.debug("%s states several sizes (%r); trying the next field", source, text)
    if col is None:
        return name_size
    if name_size is None:
        return col
    col_measured = col.dimension in ("volume", "mass")
    name_measured = name_size.dimension in ("volume", "mass")
    if col_measured and name_measured:
        if col.dimension != name_size.dimension:
            return name_size
        if (not col.pack_count and name_size.pack_count
                and compare(col, [name_size]) == "match"):
            return replace(col, pack_count=name_size.pack_count)
        return col
    if col_measured and is_pack_count(name_size) and name_size.base_value > 1 and not col.pack_count:
        return replace(col, pack_count=int(name_size.base_value))       # name says '6 pcs' only
    if is_pack_count(col) and col.base_value > 1 and name_measured:
        n = int(col.base_value)
        if not name_size.pack_count or name_size.pack_count == n:
            return replace(name_size, pack_count=n)
        return name_size
    if (col.dimension == name_size.dimension == "count" and not col.pack_count and name_size.pack_count
            and compare(col, [name_size]) == "match"):
        return replace(col, pack_count=name_size.pack_count)            # column '170 PCS', name '... 5X170PCS'
    return col


def _pack_count(size: Optional[Size]) -> Optional[int]:
    if size is None:
        return None
    if size.pack_count and size.pack_count > 1:
        return size.pack_count
    if is_pack_count(size) and size.base_value > 1:
        return int(size.base_value)
    return None


def _class_tokens(texts: Sequence[str], exclude: Set[str], context: str = "") -> Tuple[str, ...]:
    out: List[str] = []
    seen: Set[str] = set()
    for text in texts:
        if not text:
            continue
        var_toks = variants_mod.variant_tokens(text, context)
        for tok in tokens(text):
            key = strip_arabic_clitics(tok)
            if key in seen:
                continue
            if tok[0].isdigit() or key in exclude or key in var_toks:
                continue
            if key in _STOPWORDS or key in _UNIT_WORDS:
                continue
            if len(key) < 2:
                continue
            seen.add(key)
            out.append(tok)
    return tuple(out)


def description_words(spec: SkuSpec, text: Optional[str] = None) -> Tuple[str, ...]:
    """The words that describe the product in its readable sheet name (sheet_names.spec_name), in order and once:
    without the brand's words (the brand, its mapped name and spellings, a sub-brand the SKU names), sizes and
    units, and stop words ('in', 'with', 'and', 'of'); clitics stripped. Unlike class_tokens the variant words
    stay: 'DEEP BLUE SHREDDED TUNA IN SUNFLOWER OIL 185G' -> ('shredded', 'tuna', 'sunflower', 'oil').
    text, when given, is read by the same rules instead of the sheet name (a label's printed variant: 'DEEP blue
    Tuna SHREDDED SUNFLOWER OIL' -> ('tuna', 'shredded', 'sunflower', 'oil')). verify.overruled_flags compares
    the two to read a label's variant 'no'."""
    brand_words: Set[str] = set()
    for phrase in (spec.brand_canonical, spec.brand_ar) + tuple(spec.match_brands) + tuple(spec.required_brands):
        brand_words |= _token_set(phrase or "")
    out: List[str] = []
    for tok in tokens(sheet_names.spec_name(spec) if text is None else text, strip_clitics=True):
        if tok[0].isdigit() or len(tok) < 2 or tok in brand_words or tok in _STOPWORDS or tok in _UNIT_WORDS:
            continue
        if tok not in out:
            out.append(tok)
    return tuple(out)


def make_sku_key(gtin14: Optional[str], brand: str, raw_name: str, size: Optional[Size]) -> str:
    if gtin14:
        return gtin14
    basis = "|".join((normalize(brand), normalize(raw_name), size.canonical() if size else ""))
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()[:16]


def _leading_words(phrase: str, n: int) -> str:
    """The first n tokens of the phrase as it is written, cut only between two words it separates by a space
    ('AMERICAN LIGHT', 1 -> 'AMERICAN'); '' when the cut falls inside a written word ('X/LIGHT')."""
    count = 0
    for chunk in re.finditer(r"\S+", phrase):
        count += len(tokens(chunk.group(0)))
        if count == n:
            return phrase[:chunk.end()].rstrip(" /\\.,;:-_|&+")
        if count > n:
            return ""
    return ""


def matching_brand(brand: str, name: str, context: str = "") -> str:
    """The sheet brand without its last word(s) when they begin a variant phrase the name continues right after
    the brand ('AMERICAN LIGHT' + 'MEAT TUNA' -> 'AMERICAN'); '' when the brand stays whole (module docstring).

    name is the readable sheet name (catalog_match.sheet_names), context opens the context-bound phrases.
    Live run 2026-10-04, rows 27-28: the label reader read 'American' and 'american light' never matched it.
    """
    btoks = tokens(brand, strip_clitics=True)
    if len(btoks) < 2 or any(is_arabic(t) for t in btoks):
        return ""
    ntoks = tokens(name, strip_clitics=True)
    n = len(btoks)
    start = next((i for i in range(len(ntoks) - n + 1) if ntoks[i:i + n] == btoks), None)
    if start is None:
        return ""
    end = start + n
    # a phrase that starts inside the brand (never on its first word) and ends after it
    cuts = [a for _axis, _value, (a, b) in variants_mod.phrase_spans(name, context) if start < a < end < b]
    if not cuts:
        return ""
    kept = _leading_words(" ".join(brand.split()), min(cuts) - start)
    return kept if kept and matchable_brand(kept) else ""


def _resolve_brand(index: BrandIndex, brand_raw: str, brand_ar: str, name: str, name_ar: str) -> BrandResolution:
    res = index.resolve(brand_raw, name, name_ar)
    if res.conf != "mapped" and brand_ar.strip():
        res_ar = index.resolve(brand_ar, name, name_ar)
        if res_ar.conf == "mapped" or res.conf == "none":
            return res_ar
    return res


def build_sku_spec(row: Mapping[str, Any], brand_mappings=None, size_text: Optional[str] = None) -> SkuSpec:
    """Build the SkuSpec for one sheet row. brand_mappings may be a dict, a BrandIndex or None."""
    row = row or {}
    raw_name = _first(row, "name", "name_en", "product_name")
    name_ar = _first(row, "name_ar", "product_name_ar")
    brand_cell = _first(row, "brand", "brand_en")
    brand_ar_cell = _first(row, "brand_ar")
    # 'GENERIC / NO BRAND' is an empty cell for the search (the sku_key below still reads the cell)
    brand_raw = "" if is_placeholder_brand(brand_cell) else brand_cell
    brand_ar_row = "" if is_placeholder_brand(brand_ar_cell) else brand_ar_cell
    barcode = row.get("barcode")
    category = _first(row, "category")
    if size_text is None:
        size_text = _first(row, "size")

    index = build_index(brand_mappings)
    res = _resolve_brand(index, brand_raw, brand_ar_row, raw_name, name_ar)

    gtin14, gtin_status = normalize_gtin(barcode)
    # The size, variants and product-type words are parsed from the names as the stores write them: a size
    # glued to a word split off ('WATE3X185GM'), sheet compounds and typos fixed ('SOLIDTUNA SALTWATER',
    # 'CHICKN LUNCHENMEAT'; catalog_match.sheet_names). The sku_key keeps the raw name and the size parsed
    # from it (key_size), so an existing row's key never moves (live run 2026-10-03, rows 32-53).
    raw_context = sheet_names.raw_context(raw_name, name_ar, category)
    name = sheet_names.readable(raw_name, raw_context)
    name_ar_read = sheet_names.readable(name_ar, raw_context)
    size = _pick_size(size_text, name, name_ar_read)
    key_size = _pick_size(size_text, raw_name, name_ar)
    # Both names and the category open context-bound phrases ('white' next to 'tuna').
    variant_context = " ".join(t for t in (name, name_ar_read, category) if t)
    # 'AMERICAN LIGHT' + 'MEAT TUNA': the unmapped sheet brand is matched as 'AMERICAN' (its 'LIGHT' is the name's)
    trimmed = matching_brand(brand_raw, name, variant_context) \
        if res.conf == "sheet_raw" and brand_raw and res.canonical == brand_raw.strip() else ""
    if trimmed:
        res = index.unmapped(trimmed, brand_raw)
    sheet_brand = trimmed or brand_raw
    # a brand name never states a protein ('LAMB WESTON BURGER FRIES'); variants_mod.spec_brands reads the same
    brands = tuple(p for p in dict.fromkeys((sheet_brand, res.canonical, brand_ar_row or res.brand_ar)
                                            + tuple(res.match_brands) + tuple(res.competitors)) if p)
    variants = variants_mod.sku_variants((name, name_ar_read), variant_context, brands)

    brand_words: Set[str] = set()
    for phrase in (sheet_brand, brand_ar_row, res.canonical, res.brand_ar) + tuple(res.match_brands) + tuple(res.family):
        brand_words |= _token_set(phrase or "")
    class_tokens = _class_tokens((name, name_ar_read), brand_words, variant_context)

    # The key must not depend on the Brands Mapping sheet: editing it (or failing to load
    # it) would orphan approvals, rejections and queued review rows. Use the sheet's own
    # brand cell (English, else Arabic), normalised inside make_sku_key, as written: a
    # placeholder ('GENERIC / NO BRAND') or a trimmed brand never moves a key.
    # Spaces and punctuation are dropped so 'Al Marai' / 'Al-Marai' / 'Almarai' share a key.
    brand_for_key = match_key(brand_cell or brand_ar_cell).replace(" ", "")
    placeholder = next((c for c in (brand_cell, brand_ar_cell) if is_placeholder_brand(c)), "") \
        if res.conf == "none" else ""
    return SkuSpec(
        raw_name=raw_name,
        name_ar=name_ar,
        brand_raw=brand_raw,
        brand_canonical=res.canonical,
        brand_ar=brand_ar_row or res.brand_ar,
        match_brands=tuple(res.match_brands),
        competitors=tuple(res.competitors),
        official_domains=tuple(res.official_domains),
        learned_domains=tuple(getattr(res, "learned_domains", ()) or ()),
        brand_conf=res.conf,
        gtin=gtin14,
        gtin_raw="" if barcode is None else str(barcode),
        gtin_status=gtin_status,
        size=size,
        key_size=key_size,
        pack_count=_pack_count(size),
        variants=variants,
        class_tokens=class_tokens,
        category=category,
        sku_key=make_sku_key(gtin14, brand_for_key, raw_name, key_size),
        required_brands=tuple(res.required),
        sibling_brands=tuple(res.siblings),
        brand_placeholder=" ".join(placeholder.split()),
    )
