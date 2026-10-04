"""Shared data contract for the catalog_match decision core.

Every stage (identity -> retrieval -> scoring -> fetch/quality -> verification
-> routing) exchanges these types, so each stage can be written and tested on
its own. Keep this module free of I/O and of imports from the rest of the repo.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol, Tuple, runtime_checkable


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Size:
    """A parsed net content. base_value is in ml (volume), g (mass) or units (count)."""

    dimension: str  # 'volume' | 'mass' | 'count'
    base_value: float
    unit_text: str
    pack_count: Optional[int] = None
    source_field: str = ""
    pieces: Optional[int] = None    # 'N pcs' next to a net mass: pieces in one unit OR a pack (unknown)

    def canonical(self) -> str:
        """Stable text form used in sku_key and query building, e.g. '1000ml', '6x180ml'."""
        unit = {"volume": "ml", "mass": "g", "count": "pcs"}.get(self.dimension, "")
        value = f"{self.base_value:g}"
        prefix = f"{self.pack_count}x" if self.pack_count and self.pack_count > 1 else ""
        return f"{prefix}{value}{unit}"


@dataclass(frozen=True)
class SkuSpec:
    """Everything we know about the product we are looking for, normalised once."""

    raw_name: str
    name_ar: str = ""
    brand_raw: str = ""
    brand_canonical: str = ""
    brand_ar: str = ""
    match_brands: Tuple[str, ...] = ()        # normalised phrases incl. sub-brands, each >= 3 alnum chars
    competitors: Tuple[str, ...] = ()         # normalised phrases of known other brands
    official_domains: Tuple[str, ...] = ()
    brand_conf: str = "none"                  # 'mapped' | 'learned' | 'sheet_raw' | 'none'
    gtin: Optional[str] = None                # GTIN-14 when the barcode is valid
    gtin_raw: str = ""
    gtin_status: str = "missing"              # 'ok' | 'missing' | 'bad_check_digit' | 'scientific_notation' | 'bad_length' | ...
    size: Optional[Size] = None
    # the size the sku_key holds: parsed from the RAW names, never moved by a reading fix (identity.make_sku_key)
    key_size: Optional[Size] = None
    pack_count: Optional[int] = None
    variants: Dict[str, str] = field(default_factory=dict)   # axis -> value, e.g. {'fat': 'full'}
    class_tokens: Tuple[str, ...] = ()        # product-type words, e.g. ('fresh', 'milk')
    category: str = ""
    sku_key: str = ""
    required_brands: Tuple[str, ...] = ()     # sub-brand the SKU names ('nido'); tier 1 needs it in evidence
    sibling_brands: Tuple[str, ...] = ()      # the family's other sub-brands ('everyday', 'nesquik')
    # brand_discovery: how the stores write a sheet brand they spell differently ('Rio Mare' for 'RIO MARIE');
    # its normalised phrase is in match_brands too. Never set for a mapped brand; never an auto-publish.
    discovered_brands: Tuple[str, ...] = ()
    # learning: sites the reviewers keep approving this brand's images from (UAE-retailer trust, site: queries)
    learned_domains: Tuple[str, ...] = ()
    # the sheet's brand cell when it only says the product has no brand ('GENERIC / NO BRAND'; brand_index.
    # is_placeholder_brand): brand_raw is then '' and brand_conf 'none', and the product is searched by name only
    brand_placeholder: str = ""

    def __hash__(self) -> int:  # dict field makes the generated hash unusable
        return hash(self.sku_key or (self.raw_name, self.brand_raw))


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Candidate:
    """One image found by a provider, with the page evidence that came with it."""

    image_url: str
    page_url: str = ""
    page_title: str = ""
    title: str = ""
    snippet: str = ""
    domain: str = ""                 # host of the PAGE (falls back to the image host)
    width: Optional[int] = None      # None when the provider did not report real dimensions
    height: Optional[int] = None
    provider: str = ""
    query_id: str = ""
    rank: int = 0
    gtin_on_page: Optional[str] = None
    sanctioned: bool = True          # False for scraped sources: never eligible for AUTO_PUBLISH
    consensus_count: int = 1


@dataclass(frozen=True)
class PlannedQuery:
    query_id: str        # 'Q1'..'Q4', 'R1', 'R2' or 'custom'
    text: str
    hl: str = "en"
    providers_hint: Tuple[str, ...] = ()   # empty = every provider; e.g. ('serper',) for site: queries
    relaxed: bool = False


@dataclass
class ProviderHealth:
    provider: str
    status: str                      # 'ok' | 'empty' | 'error' | 'quota' | 'blocked'
    http_status: Optional[int] = None
    latency_ms: Optional[int] = None
    error: Optional[str] = None
    query_id: str = ""


@dataclass
class ProviderResult:
    provider: str
    status: str                      # 'ok' | 'empty' | 'error' | 'quota' | 'blocked'
    http_status: Optional[int] = None
    latency_ms: Optional[int] = None
    candidates: List[Candidate] = field(default_factory=list)
    error: Optional[str] = None
    query_id: str = ""

    def health(self) -> ProviderHealth:
        return ProviderHealth(
            provider=self.provider,
            status=self.status,
            http_status=self.http_status,
            latency_ms=self.latency_ms,
            error=self.error,
            query_id=self.query_id,
        )


@dataclass
class RetrievalResult:
    pool: List[Candidate] = field(default_factory=list)
    health: List[ProviderResult] = field(default_factory=list)
    queries: List[str] = field(default_factory=list)
    relaxed_ids: set = field(default_factory=set)   # query_ids of relaxed queries that ran


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CandidateScore:
    tier: Optional[int]              # 1 | 2 | 3, or None when hard-rejected
    identity_score: float = 0.0
    hard_reject: Tuple[str, ...] = ()
    matched: Dict[str, Any] = field(default_factory=dict)   # brand, size, variants, gtin, coverage, source_trust
    conflicts: Tuple[str, ...] = ()
    size_status: str = "unknown"     # 'match' | 'conflict' | 'ambiguous' | 'unknown'
    url_only_size_conflict: bool = False

    def __hash__(self) -> int:
        return hash((self.tier, self.identity_score, self.hard_reject, self.conflicts))


# ---------------------------------------------------------------------------
# Fetch and quality
# ---------------------------------------------------------------------------

@dataclass
class FetchedImage:
    candidate: Candidate
    ok: bool
    error: Optional[str] = None      # e.g. 'http_403', 'not_image', 'too_small', 'timeout'
    content_sha256: Optional[str] = None
    width: Optional[int] = None      # real decoded size, after EXIF transpose
    height: Optional[int] = None
    path_or_bytes: Any = None        # path in the candidate store, or raw bytes in tests
    phash: Optional[str] = None      # hex string from image_dedup_bktree.calculate_phash


@dataclass
class QualityReport:
    hard_ok: bool
    hard_reasons: List[str] = field(default_factory=list)
    soft: Dict[str, float] = field(default_factory=dict)   # white_border_ratio, fill_ratio, fg_sharpness, clutter
    quality_score: float = 0.0       # 0..1, tie-break only


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

@dataclass
class VlmImageVerdict:
    index: int                       # position in the images list passed to verify()
    brand_text: str = ""
    variant_text: str = ""
    size_text: str = ""
    pack_count: Optional[int] = None
    view: str = ""                   # front_packshot | other_side | lifestyle | multi_product | banner | not_product
    brand_match: str = "unsure"      # yes | no | unsure
    variant_match: str = "unsure"
    size_match: str = "unsure"
    decision: str = "UNKNOWN"        # MATCH | MISMATCH | UNSURE | UNKNOWN (decided by code, not the model)


@dataclass
class VerificationResult:
    status: str                      # 'ok' | 'unknown'
    verdicts: List[VlmImageVerdict] = field(default_factory=list)
    calls: int = 0
    error: Optional[str] = None
    # verifier package: one entry per billed model call {role, provider, model, input_tokens, output_tokens,
    # estimated, usd, ...} and machine notices for the dashboard ('strong_budget_exhausted', 'claude_key_rejected')
    usage: List[Dict[str, Any]] = field(default_factory=list)
    notices: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Decision
# ---------------------------------------------------------------------------

DECISIONS = (
    "AUTO_PUBLISH",
    "REVIEW_PRESELECTED",
    "REVIEW_UNSELECTED",
    "NOT_FOUND",
    "PROVIDER_DOWN",
    "VERIFIER_DOWN",
)

FAILURE_CODES = (
    "NO_RESULTS",
    "ALL_CONFLICTED",
    "PROVIDER_DOWN",
    "VERIFIER_DOWN",
    "DOWNLOAD_FAILED",
    "SOCIAL_ONLY",       # every brand listing is a social-network post whose picture cannot be downloaded
)


@dataclass
class RankedCandidate:
    candidate: Candidate
    score: CandidateScore
    fetched: Optional[FetchedImage] = None
    quality: Optional[QualityReport] = None
    verdict: Optional[VlmImageVerdict] = None
    status: str = "eligible"         # 'preselected' | 'eligible' | 'rejected' | 'excluded'
    reasons: List[str] = field(default_factory=list)


@dataclass
class SearchOutcome:
    decision: str
    failure_code: Optional[str] = None
    winner: Optional[RankedCandidate] = None
    ranked: List[RankedCandidate] = field(default_factory=list)
    provider_health: List[ProviderHealth] = field(default_factory=list)
    queries: List[str] = field(default_factory=list)
    vlm_calls: int = 0
    sku_key: str = ""
    reject_counts: Dict[str, int] = field(default_factory=dict)   # hard-reject rule -> count
    # verifier package: every billed verifier call of this search (VerificationResult.usage) and its notices
    vlm_usage: List[Dict[str, Any]] = field(default_factory=list)
    verifier_notices: List[str] = field(default_factory=list)
    discovered_brands: List[str] = field(default_factory=list)    # brand_discovery: the store spelling used
    # failure_code SOCIAL_ONLY: the links of the social-network posts that show the product, for the reviewer
    social_links: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Stage protocols (real implementations and test doubles both satisfy these)
# ---------------------------------------------------------------------------

@runtime_checkable
class Provider(Protocol):
    name: str
    sanctioned: bool

    def search(self, query: str, hl: str, spec: SkuSpec) -> ProviderResult: ...


@runtime_checkable
class Fetcher(Protocol):
    def fetch(self, cands: List[Candidate], spec: SkuSpec) -> List[FetchedImage]: ...


@runtime_checkable
class Verifier(Protocol):
    def verify(self, spec: SkuSpec, images: List[FetchedImage]) -> VerificationResult: ...
