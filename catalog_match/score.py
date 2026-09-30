"""Identity scoring and ranking (decision D4). Image quality never enters a tier.

score_candidate(spec, cand, negatives=None) -> CandidateScore

Evidence fields are parsed SEPARATELY (never concatenated):
    title        cand.title
    page_title   cand.page_title        (cand.snippet only counts as brand presence)
    page_slug    url_path_text(cand.page_url)
    image_file   url_path_text(cand.image_url, filename_only=True)
The host is used only for source trust (and the stock/clipart rule).

Hard rejects (tier None):
    competitor_brand        a known other brand in title / page_title / page_slug /
                            image_file while no target brand phrase is present anywhere
    size_conflict           title or page_title states another size (> 3 %)
    pack_conflict           title or page_title states another pack count
    variant_conflict:<axis> title / page_title / page_slug states an exclusive other variant
    gtin_mismatch           gtin_on_page is a valid GTIN different from the SKU's
    stock_or_clipart        stock/clipart domain or keyword
    reviewer_negative       the image URL was rejected by a reviewer before

Soft conflicts cap the tier at 2: a size or pack conflict found only in a URL
(url_only_size_conflict), a variant conflict found only in the image filename, a
'marked' variant the SKU does not state (low fat / diet / decaf / a flavour / a
form such as fresh or long-life), a closely related variant line (Diet vs Zero
Sugar), a pack the title leaves ambiguous for a single-unit SKU ('16 pcs 200g'),
and a missing sub-brand the SKU names (parent-brand-only evidence).

Brand evidence ignores store-name title segments ('- Shop on Carrefour UAE'), so a
private-label SKU never matches another brand through the retailer's name.

A brand that is also an everyday listing word (brand_index.is_generic_brand: 'Freshly',
'Family', 'Golden Prize') turns up in other brands' listings ('Seara Chicken Shawarma
350g, freshly prepared'). Its hit is full brand evidence only where a brand stands: at
the start of the product part of the title or page title (after 'Buy' / 'Shop' and store
names), at the start of the page slug's product segment, or on the brand's official
domain. A hit anywhere else keeps the brand match (tier 2, conflict
generic_brand_position:<field>) but neither makes tier 1 nor corroborates a GTIN match.
Distinctive brands are not affected.

Tiers:
    1  GTIN match with brand or class-coverage corroboration, or brand + size +
       every specified variant matched with class coverage >= 0.5 on a
       brand-official, UAE-retailer or structured page (a common-word brand only
       where a brand stands)
    2  brand matched (or GTIN matched), no hard conflict
    3  no brand evidence, no hard conflict

rank(scored, quality) sorts by the lexicographic key
    tier > size match > variants matched > class coverage > source trust
    > consensus_count > quality soft score
so a sharper photo can only break ties between candidates with identical identity evidence.
"""

from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from . import variants as variants_mod
from .brand_index import is_generic_brand
from .gtin import normalize_gtin
from .models import Candidate, CandidateScore, SkuSpec
from .sizes import compare, compare_pack, parse_sizes
from .text_norm import (
    any_phrase_in, domain_matches, is_arabic, match_string, store_market, tokens, url_host, url_path_text,
)

logger = logging.getLogger(__name__)

TRUSTED_DOMAINS_PATH = Path(__file__).resolve().parent / "data" / "trusted_domains.json"

COVERAGE_T1 = 0.5
TRUST_OFFICIAL, TRUST_UAE_RETAILER, TRUST_STRUCTURED, TRUST_OTHER_RETAIL, TRUST_GENERIC = 4, 3, 2, 1, 0
TRUST_NAMES = {4: "official", 3: "uae_retailer", 2: "structured", 1: "other_retail", 0: "generic"}

TEXT_FIELDS = ("title", "page_title")                      # hard size / pack evidence
VARIANT_HARD_FIELDS = ("title", "page_title", "page_slug")  # hard variant evidence
COMPETITOR_FIELDS = ("title", "page_title", "page_slug", "image_file")
URL_FIELDS = ("page_slug", "image_file")
DEPARTMENT_AXES = ("form",)   # variant axes retailers also use as department names in page URLs
IDENTITY_FIELDS = ("title", "page_title", "page_slug", "image_file")


@lru_cache(maxsize=1)
def trusted_domains() -> Dict[str, Any]:
    with open(TRUSTED_DOMAINS_PATH, "r", encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# Evidence helpers
# ---------------------------------------------------------------------------

def evidence_fields(cand: Candidate) -> Dict[str, str]:
    """The separately-parsed evidence texts of one candidate."""
    return {
        "title": cand.title or "",
        "page_title": cand.page_title or "",
        "snippet": cand.snippet or "",
        "page_slug": url_path_text(cand.page_url),
        "image_file": url_path_text(cand.image_url, filename_only=True),
    }


_SEGMENT_SPLIT_RE = re.compile(r"\s+[-–—|:·•]\s+|\s*[|·•]\s*")


@lru_cache(maxsize=1)
def _retailer_vocab() -> Tuple[Tuple[str, ...], frozenset]:
    data = trusted_domains()
    names = tuple(sorted({match_string(n) for n in data.get("retailer_names", []) if match_string(n)},
                         key=len, reverse=True))
    filler = frozenset(match_string(w) for w in data.get("site_filler", []) if match_string(w))
    return names, filler


def _site_only(segment: str) -> bool:
    """True when a title segment is only a store name plus filler ('Shop on Carrefour UAE')."""
    names, filler = _retailer_vocab()
    text = " " + match_string(segment) + " "
    found = False
    for name in names:
        needle = " " + name + " "
        if needle in text:
            found = True
            text = text.replace(needle, " ")
    if not found:
        return False
    return all(tok in filler for tok in text.split())


_TRAILING_SITE_RE = re.compile(r"\s+(?:online\s+)?(?:at|on|from|in)\s+(?P<rest>[^|]*)$")


def strip_site_suffix(text: str) -> str:
    """Drop store-name segments ('- Shop on Carrefour UAE', '| Lulu UAE', 'at Amazon.ae') from a title.

    Used for brand evidence only: a retailer's own name in every listing title must
    not count as the brand of a private-label SKU (brand 'Carrefour'), nor as a
    competitor of the target brand.
    """
    if not text:
        return ""
    kept = [seg for seg in _SEGMENT_SPLIT_RE.split(text) if seg and not _site_only(seg)]
    out = " - ".join(kept)
    m = _TRAILING_SITE_RE.search(out)
    if m and _site_only(m.group("rest")):
        out = out[:m.start()]
    return out


def _brand_leads(phrases: Sequence[str], text: str) -> Optional[bool]:
    """Does a brand phrase open `text` once leading store names and filler ('Buy', 'Shop on
    Carrefour') are skipped? None when the text holds nothing but such words.

    The phrase is tried before a store name is skipped, so a brand that is also a store
    name ('Target') still opens its own listing.
    """
    names, filler = _retailer_vocab()
    keys = [k for k in (match_string(p) for p in phrases) if k]
    rest = match_string(text)
    while rest:
        if any(rest == k or rest.startswith(k + " ") for k in keys):
            return True
        store = next((n for n in names if rest == n or rest.startswith(n + " ")), None)
        if store:
            rest = rest[len(store):].lstrip()
            continue
        word, _, rest = rest.partition(" ")
        if word not in filler:
            return False
    return None


def _brand_opens_title(phrases: Sequence[str], text: str) -> bool:
    """True when a brand phrase opens the product part of a title: its first segment that is
    more than store names and filler ('Buy Freshly Chicken Shawarma 350g Online | Lulu UAE')."""
    for segment in _SEGMENT_SPLIT_RE.split(text or ""):
        lead = _brand_leads(phrases, segment)
        if lead is not None:
            return lead
    return False


def _generic_brand_weak_fields(spec: SkuSpec, cand: Candidate, brand_texts: Mapping[str, str]) -> List[str]:
    """Identity fields whose only brand evidence is a common-word brand out of a brand position.

    Empty when every brand phrase of the SKU is distinctive, or when some field shows a
    distinctive phrase anywhere or a common-word phrase where a brand stands (see the
    module docstring). The caller handles the brand's official domain.
    """
    generic = [p for p in spec.match_brands if is_generic_brand(p)]
    if not generic:
        return []
    distinctive = [p for p in spec.match_brands if p not in generic]
    weak: List[str] = []
    for name in IDENTITY_FIELDS:
        text = brand_texts[name]
        if distinctive and any_phrase_in(distinctive, text):
            return []
        if not any_phrase_in(generic, text):
            continue
        if name in TEXT_FIELDS and _brand_opens_title(generic, text):
            return []
        if name == "page_slug" and _brand_leads(generic, url_path_text(cand.page_url, product_segment=True)):
            return []
        weak.append(name)
    return weak


def page_host(cand: Candidate) -> str:
    """Host of the page the image was found on; '' when only the image host is known."""
    if cand.page_url:
        host = url_host(cand.page_url)
        if host:
            return host
    domain = url_host(cand.domain) if cand.domain else ""
    if domain and domain != url_host(cand.image_url):
        return domain
    return ""


def source_trust(spec: SkuSpec, cand: Candidate) -> Tuple[int, str]:
    """(trust level, name) of the candidate's page: official > UAE retailer > structured > other retail > generic."""
    data = trusted_domains()
    host = page_host(cand)
    if host and domain_matches(host, spec.official_domains):
        return TRUST_OFFICIAL, TRUST_NAMES[TRUST_OFFICIAL]
    if host and domain_matches(host, data.get("uae_retailers", [])):
        # The same store's other-country section ('/saudi-en/', '/en-kw/') sells the foreign pack.
        if store_market(cand.page_url) == "foreign":
            return TRUST_OTHER_RETAIL, TRUST_NAMES[TRUST_OTHER_RETAIL]
        return TRUST_UAE_RETAILER, TRUST_NAMES[TRUST_UAE_RETAILER]
    if (host and domain_matches(host, data.get("structured", []))) or (
        (cand.provider or "").lower() in set(data.get("structured_providers", []))
    ):
        return TRUST_STRUCTURED, TRUST_NAMES[TRUST_STRUCTURED]
    if host and domain_matches(host, data.get("other_retail", [])):
        return TRUST_OTHER_RETAIL, TRUST_NAMES[TRUST_OTHER_RETAIL]
    return TRUST_GENERIC, TRUST_NAMES[TRUST_GENERIC]


def _is_stock(cand: Candidate, fields: Mapping[str, str]) -> Optional[str]:
    data = trusted_domains()
    stock_domains = data.get("stock_or_clipart", [])
    for host in (page_host(cand), url_host(cand.image_url)):
        if host and domain_matches(host, stock_domains):
            return host
    kw = data.get("stock_keywords", [])
    for name in ("title", "page_title", "page_slug", "image_file"):
        hit = any_phrase_in(kw, fields.get(name))
        if hit:
            return hit
    return None


def _negative_urls(negatives) -> Set[str]:
    if not negatives:
        return set()
    if isinstance(negatives, Mapping):
        urls: List[str] = []
        for key in ("urls", "exclude_urls", "image_urls"):
            urls.extend(negatives.get(key) or [])
    elif isinstance(negatives, str):
        urls = [negatives]
    else:
        urls = list(negatives)
    # The same key the candidate pool uses: an extension-less endpoint keeps its
    # identifying query ('/_next/image?url=...'), so one rejection never hides other images.
    from .retrieve import norm_image_url
    return {norm_image_url(u) for u in urls if u}


def _stem(tok: str) -> str:
    if not is_arabic(tok) and len(tok) > 3 and tok.endswith("s") and not tok.endswith("ss"):
        return tok[:-1]
    return tok


def _token_bag(texts: Iterable[str]) -> Set[str]:
    bag: Set[str] = set()
    for text in texts:
        bag.update(_stem(t) for t in tokens(text, strip_clitics=True))
    return bag


def class_coverage(spec: SkuSpec, fields: Mapping[str, str]) -> float:
    """Share of the SKU's product-type words present in the evidence (best of English / Arabic)."""
    if not spec.class_tokens:
        return 1.0
    bag = _token_bag(fields.get(f, "") for f in IDENTITY_FIELDS)
    groups: Dict[bool, List[str]] = {}
    for tok in spec.class_tokens:
        stems = [_stem(t) for t in tokens(tok, strip_clitics=True)]
        if stems:
            groups.setdefault(is_arabic(stems[0]), []).append(stems[0])
    best = 0.0
    for toks in groups.values():
        best = max(best, sum(1 for t in toks if t in bag) / len(toks))
    return round(best, 4)


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def score_candidate(spec: SkuSpec, cand: Candidate, negatives=None) -> CandidateScore:
    fields = evidence_fields(cand)
    hard: List[str] = []
    conflicts: List[str] = []
    soft_cap = False

    # --- brand -----------------------------------------------------------
    # Store-name suffixes ('- Shop on Carrefour UAE') are not brand evidence.
    brand_texts = dict(fields)
    for name in ("title", "page_title", "snippet"):
        brand_texts[name] = strip_site_suffix(fields[name])
    brand_fields: Dict[str, str] = {}
    for name in IDENTITY_FIELDS + ("snippet",):
        hit = any_phrase_in(spec.match_brands, brand_texts[name]) if spec.match_brands else None
        if hit:
            brand_fields[name] = hit
    brand_ok = any(name in brand_fields for name in IDENTITY_FIELDS)
    if spec.match_brands and not brand_fields:
        for name in COMPETITOR_FIELDS:
            comp = any_phrase_in(spec.competitors, brand_texts[name])
            if comp:
                hard.append("competitor_brand")
                conflicts.append(f"competitor_brand:{name}:{comp}")
                break
    # A SKU that names a sub-brand ('Nido') needs that sub-brand: parent-only evidence
    # ('Nestle') caps at tier 2, and a sibling sub-brand ('Nestle Everyday') is another product.
    sub_brand_ok = True
    if spec.required_brands:
        sub_brand_ok = any(any_phrase_in(spec.required_brands, brand_texts[n]) for n in IDENTITY_FIELDS)
        if not sub_brand_ok:
            for name in COMPETITOR_FIELDS:
                sib = any_phrase_in(spec.sibling_brands, brand_texts[name]) if spec.sibling_brands else None
                if sib:
                    if "competitor_brand" not in hard:
                        hard.append("competitor_brand")
                    conflicts.append(f"sibling_sub_brand:{name}:{sib}")
                    break
            if brand_ok:
                conflicts.append("sub_brand_missing")
                soft_cap = True

    # --- GTIN ------------------------------------------------------------
    gtin_state: Optional[str] = None
    if cand.gtin_on_page and spec.gtin:
        page_gtin, _status = normalize_gtin(cand.gtin_on_page)
        if page_gtin:
            gtin_state = "match" if page_gtin == spec.gtin else "mismatch"
            if gtin_state == "mismatch":
                hard.append("gtin_mismatch")
                conflicts.append(f"gtin_mismatch:{page_gtin}")

    # --- size and pack ---------------------------------------------------
    size_by_field: Dict[str, str] = {}
    pack_by_field: Dict[str, str] = {}
    # For a counted product ('Eggs 30 pcs') the count IS the size; packs are compared
    # separately only for measured sizes, or when the SKU states a pack but no size.
    count_target = spec.size is not None and spec.size.dimension == "count"
    check_pack = (spec.size is not None and not count_target) or (spec.size is None and bool(spec.pack_count))
    if spec.size is not None or spec.pack_count:
        for name in TEXT_FIELDS + URL_FIELDS:
            found = parse_sizes(fields[name], name)
            if not found:
                continue
            if spec.size is not None:
                size_by_field[name] = compare(spec.size, found)
            if check_pack:
                pack_by_field[name] = compare_pack(spec.pack_count, found,
                                                   spec.size.pieces if spec.size is not None else None)
    text_sizes = [size_by_field.get(f) for f in TEXT_FIELDS if f in size_by_field]
    url_sizes = [size_by_field.get(f) for f in URL_FIELDS if f in size_by_field]
    text_packs = [pack_by_field.get(f) for f in TEXT_FIELDS if f in pack_by_field]
    url_packs = [pack_by_field.get(f) for f in URL_FIELDS if f in pack_by_field]

    for name in TEXT_FIELDS:
        if size_by_field.get(name) == "conflict":
            conflicts.append(f"size_conflict:{name}")
        if pack_by_field.get(name) == "conflict":
            conflicts.append(f"pack_conflict:{name}")
    if "conflict" in text_sizes:
        hard.append("size_conflict")
    if "conflict" in text_packs:
        hard.append("pack_conflict")

    url_only_size_conflict = False
    if "conflict" not in text_sizes and "conflict" not in text_packs and (
        "conflict" in url_sizes or "conflict" in url_packs
    ):
        url_only_size_conflict = True
        soft_cap = True
        for name in URL_FIELDS:
            if size_by_field.get(name) == "conflict":
                conflicts.append(f"url_size_conflict:{name}")
            if pack_by_field.get(name) == "conflict":
                conflicts.append(f"url_pack_conflict:{name}")

    if "conflict" in text_sizes:
        size_status = "conflict"
    elif "ambiguous" in text_sizes:
        size_status = "ambiguous"
    elif "match" in text_sizes:
        size_status = "match"
    elif "conflict" in url_sizes:
        size_status = "conflict"
    elif "match" in url_sizes:
        size_status = "match"
    elif "ambiguous" in url_sizes:
        size_status = "ambiguous"
    else:
        size_status = "unknown"

    all_packs = text_packs + url_packs
    if "conflict" in all_packs:
        pack_status = "conflict"
    elif "match" in all_packs:
        pack_status = "match"
    elif "ambiguous" in all_packs:
        pack_status = "ambiguous"
    else:
        pack_status = "unknown"
    if check_pack and not spec.pack_count and "ambiguous" in text_packs:
        # a single-unit SKU against '16 pcs 200g' or a '330ml / 6 x 330ml' listing: not proven
        conflicts.append("pack_ambiguous")
        soft_cap = True

    # --- variants --------------------------------------------------------
    context = variants_mod.spec_context(spec)
    found_variants = {name: variants_mod.extract_variants(fields[name], context) for name in IDENTITY_FIELDS}
    hard_axes: List[str] = []
    for name in VARIANT_HARD_FIELDS:
        for axis in variants_mod.conflicts(spec.variants, found_variants[name]):
            conflicts.append(f"variant_conflict:{axis}:{name}")
            if axis not in hard_axes:
                hard_axes.append(axis)
    hard.extend(f"variant_conflict:{axis}" for axis in hard_axes)
    for axis in variants_mod.conflicts(spec.variants, found_variants["image_file"]):
        if axis not in hard_axes:
            conflicts.append(f"image_variant_conflict:{axis}")
            soft_cap = True
    for name in IDENTITY_FIELDS:
        for axis in variants_mod.soft_conflicts(spec.variants, found_variants[name]):
            conflicts.append(f"soft_variant_conflict:{axis}:{name}")   # Diet vs Zero Sugar
            soft_cap = True
    matched_axes: List[str] = []
    for axis in spec.variants:
        if axis in hard_axes:
            continue
        if any(axis in variants_mod.matched_axes(spec.variants, found_variants[n]) for n in IDENTITY_FIELDS):
            matched_axes.append(axis)
    unstated: List[str] = []
    for name in VARIANT_HARD_FIELDS:
        found = found_variants[name]
        if name == "page_slug":
            # Retailers name departments after a form ('/fresh-food/' holds water, juice and laban):
            # an unstated form counts from the product's own slug segment, not the breadcrumbs.
            own = variants_mod.extract_variants(url_path_text(cand.page_url, product_segment=True), context)
            found = {axis: value for axis, value in found.items() if axis not in DEPARTMENT_AXES}
            found.update({axis: value for axis, value in own.items() if axis in DEPARTMENT_AXES})
        for axis in variants_mod.unstated_marked(spec.variants, found, context):
            if axis not in unstated:
                unstated.append(axis)
    for axis in unstated:
        conflicts.append(f"unstated_variant:{axis}")
        soft_cap = True
    variants_ok = len(matched_axes) == len(spec.variants)

    # --- source, stock, negatives ---------------------------------------
    trust, trust_name = source_trust(spec, cand)
    stock_hit = _is_stock(cand, fields)
    if stock_hit:
        hard.append("stock_or_clipart")
        conflicts.append(f"stock_or_clipart:{stock_hit}")
    neg = _negative_urls(negatives)
    if neg:
        from .retrieve import norm_image_url
        if norm_image_url(cand.image_url) in neg:
            hard.append("reviewer_negative")

    # --- common-word brand position --------------------------------------
    # 'freshly' in '... 350g, freshly prepared' is not the brand Freshly: out of a brand
    # position (and off the official domain) the hit keeps brand_ok but never makes tier 1.
    generic_weak: List[str] = []
    if brand_ok and trust != TRUST_OFFICIAL:
        generic_weak = _generic_brand_weak_fields(spec, cand, brand_texts)
    conflicts.extend(f"generic_brand_position:{name}" for name in generic_weak)
    brand_t1 = brand_ok and not generic_weak

    coverage = class_coverage(spec, fields)

    # --- tier ------------------------------------------------------------
    gtin_ok = gtin_state == "match"
    size_ok = spec.size is not None and size_status == "match" and (
        count_target or not spec.pack_count or pack_status == "match"
    )
    trusted_page = trust >= TRUST_STRUCTURED
    if hard:
        tier: Optional[int] = None
    else:
        # A GTIN match alone (an Open Food Facts record) is tier 1 only with brand or
        # product-type corroboration: records can be wrong, and in-store codes are reused.
        gtin_t1 = gtin_ok and (brand_t1 or coverage >= COVERAGE_T1)
        t1 = gtin_t1 or (brand_t1 and size_ok and variants_ok and coverage >= COVERAGE_T1 and trusted_page)
        if t1 and not soft_cap:
            tier = 1
        elif brand_ok or gtin_ok:
            tier = 2
        else:
            tier = 3

    matched = {
        "brand": brand_ok,
        "brand_fields": dict(brand_fields),
        "size": size_status,
        "size_fields": dict(size_by_field),
        "pack": pack_status,
        "variants": list(matched_axes),
        "variants_found": {k: v for k, v in found_variants.items() if v},
        "gtin": gtin_state,
        "coverage": coverage,
        "source_trust": trust,
        "source_class": trust_name,
        "page_domain": page_host(cand),
    }
    return CandidateScore(
        tier=tier,
        identity_score=_identity_score(tier, brand_ok, gtin_ok, size_status, len(matched_axes),
                                       len(spec.variants), coverage, trust),
        hard_reject=tuple(dict.fromkeys(hard)),
        matched=matched,
        conflicts=tuple(dict.fromkeys(conflicts)),
        size_status=size_status,
        url_only_size_conflict=url_only_size_conflict,
    )


def _identity_score(tier, brand_ok, gtin_ok, size_status, n_matched, n_variants, coverage, trust) -> float:
    """0..1 summary for display only; ranking uses rank_key()."""
    if tier is None:
        return 0.0
    if gtin_ok:
        return 1.0
    score = 0.30 * brand_ok
    score += 0.25 if size_status == "match" else (0.08 if size_status in ("unknown", "ambiguous") else 0.0)
    score += 0.20 * (n_matched / n_variants if n_variants else 1.0)
    score += 0.15 * coverage
    score += 0.10 * (trust / TRUST_OFFICIAL)
    return round(min(score, 0.99), 4)


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------

_SIZE_RANK = {"match": 2, "unknown": 1, "ambiguous": 1, "conflict": 0}


def _quality_value(quality: Optional[Mapping], cand: Candidate) -> float:
    if not quality:
        return 0.0
    value = None
    try:
        value = quality.get(cand)
    except TypeError:
        value = None
    if value is None:
        value = quality.get(cand.image_url)
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, Mapping):
        return float(value.get("quality_score") or 0.0)
    return float(getattr(value, "quality_score", 0.0) or 0.0)


def rank_key(cand: Candidate, score: CandidateScore, quality_score: float = 0.0) -> Tuple:
    """Ascending sort key implementing the D4 lexicographic order (best first)."""
    rejected = score.tier is None or bool(score.hard_reject)
    tier_rank = 0 if rejected else 4 - int(score.tier)
    matched = score.matched or {}
    return (
        -tier_rank,
        -_SIZE_RANK.get(score.size_status, 0),
        -len(matched.get("variants") or ()),
        -float(matched.get("coverage") or 0.0),
        -int(matched.get("source_trust") or 0),
        -int(cand.consensus_count or 1),
        -float(quality_score or 0.0),
        cand.rank if cand.rank and cand.rank > 0 else 10 ** 6,   # provider order, deterministic tie-break
    )


def rank(scored: Sequence[Tuple[Candidate, CandidateScore]], quality: Optional[Mapping] = None
         ) -> List[Tuple[Candidate, CandidateScore]]:
    """Sort (candidate, score) pairs best-first; hard-rejected pairs go last. Stable for full ties.

    quality maps a Candidate or its image_url to a float, a QualityReport or a dict
    with 'quality_score'. It is the LAST key: it never overrides identity evidence.
    """
    indexed = list(enumerate(scored))
    indexed.sort(key=lambda item: rank_key(item[1][0], item[1][1], _quality_value(quality, item[1][0])) + (item[0],))
    return [pair for _, pair in indexed]


def has_tier1(spec: SkuSpec, cands: Iterable[Candidate], negatives=None) -> bool:
    """True when any candidate scores tier 1 (the retrieval early-stop test of D3)."""
    return any(score_candidate(spec, c, negatives).tier == 1 for c in cands)
