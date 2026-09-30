"""Sheet row -> SkuSpec: everything we know about the product we are looking for.

build_sku_spec(row, brand_mappings, size_text=None)
    row keys follow the search facade kwargs: name, name_ar, brand, brand_ar,
    barcode, category, size ('name_en' / 'product_name' are accepted for name).

* raw_name is kept exactly as the sheet has it; nothing here rewrites it.
* brand goes through the reverse brand index (mapped / sheet_raw / none).
* size comes from the size column when it parses to one size, else from the name.
* class_tokens are the product-type words left after removing brand, size,
  variant and stop words ('Almarai Full Fat Milk 1L' -> ('milk',)).
* sku_key is the GTIN-14 when the barcode is valid, otherwise
  sha1(norm brand | norm raw name | size canonical)[:16].
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any, List, Mapping, Optional, Sequence, Set, Tuple

from . import variants as variants_mod
from .brand_index import BrandIndex, BrandResolution, build_index
from .gtin import normalize_gtin
from .models import Size, SkuSpec
from .sizes import is_pack_count, parse_sizes, product_size
from .text_norm import normalize, strip_arabic_clitics, tokens

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
    " مل ملل مللي ملي لتر ليتر لترات ل غ غم غرام جم جرام كجم كغ كغم كيلو كيلوغرام كيلوجرام"
)


def _token_set(raw: str) -> Set[str]:
    return {strip_arabic_clitics(t) for t in tokens(raw)}


_STOPWORDS = _token_set(_STOPWORDS_RAW)
_UNIT_WORDS = _token_set(_UNIT_WORDS_RAW)


def _first(row: Mapping[str, Any], *keys: str) -> str:
    for k in keys:
        v = row.get(k)
        if v is not None and str(v).strip():
            return str(v)
    return ""


def _pick_size(size_text: Optional[str], name: str, name_ar: str) -> Optional[Size]:
    for text, source in ((size_text, "sheet_size"), (name, "name"), (name_ar, "name_ar")):
        if not text:
            continue
        ps = product_size(parse_sizes(text, source))
        if isinstance(ps, Size):
            return ps
        if ps is not None:
            logger.debug("%s states several sizes (%r); trying the next field", source, text)
    return None


def _pack_count(size: Optional[Size]) -> Optional[int]:
    if size is None:
        return None
    if size.pack_count and size.pack_count > 1:
        return size.pack_count
    if is_pack_count(size) and size.base_value > 1:
        return int(size.base_value)
    return None


def _class_tokens(texts: Sequence[str], exclude: Set[str]) -> Tuple[str, ...]:
    out: List[str] = []
    seen: Set[str] = set()
    for text in texts:
        if not text:
            continue
        var_toks = variants_mod.variant_tokens(text)
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


def make_sku_key(gtin14: Optional[str], brand: str, raw_name: str, size: Optional[Size]) -> str:
    if gtin14:
        return gtin14
    basis = "|".join((normalize(brand), normalize(raw_name), size.canonical() if size else ""))
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()[:16]


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
    brand_raw = _first(row, "brand", "brand_en")
    brand_ar_row = _first(row, "brand_ar")
    barcode = row.get("barcode")
    category = _first(row, "category")
    if size_text is None:
        size_text = _first(row, "size")

    index = build_index(brand_mappings)
    res = _resolve_brand(index, brand_raw, brand_ar_row, raw_name, name_ar)

    gtin14, gtin_status = normalize_gtin(barcode)
    size = _pick_size(size_text, raw_name, name_ar)
    variants = variants_mod.merge(
        variants_mod.extract_variants(raw_name),
        variants_mod.extract_variants(name_ar),
    )

    brand_words: Set[str] = set()
    for phrase in (brand_raw, brand_ar_row, res.canonical, res.brand_ar) + tuple(res.match_brands) + tuple(res.family):
        brand_words |= _token_set(phrase or "")
    class_tokens = _class_tokens((raw_name, name_ar), brand_words)

    brand_for_key = res.canonical or brand_raw
    return SkuSpec(
        raw_name=raw_name,
        name_ar=name_ar,
        brand_raw=brand_raw,
        brand_canonical=res.canonical,
        brand_ar=brand_ar_row or res.brand_ar,
        match_brands=tuple(res.match_brands),
        competitors=tuple(res.competitors),
        official_domains=tuple(res.official_domains),
        brand_conf=res.conf,
        gtin=gtin14,
        gtin_raw="" if barcode is None else str(barcode),
        gtin_status=gtin_status,
        size=size,
        pack_count=_pack_count(size),
        variants=variants,
        class_tokens=class_tokens,
        category=category,
        sku_key=make_sku_key(gtin14, brand_for_key, raw_name, size),
    )
