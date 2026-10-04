"""Decision routing (decision D10): SearchOutcome from ranked, fetched, verified candidates.

route(spec, ranked, verification, health, relaxed_ids, cache_hit=False) -> SearchOutcome

`ranked` is best-first (score.rank order). `verification` is one VerificationResult,
a list of them (the pipeline may make two calls), or None when nothing was sent.
Per-candidate verdicts are read from RankedCandidate.verdict.

Decisions
    AUTO_PUBLISH        every one of:
                          * settings.auto_publish_enabled() and the brand (or 'category:<name>')
                            is in AUTO_PUBLISH_BRANDS, or the list is '*';
                          * the winner is tier 1 with VLM verdict MATCH;
                          * the winner's source is sanctioned;
                          * spec.brand_conf == 'mapped' (never 'learned');
                          * the page's trust is not only learned from reviews ('reviewed_source'),
                            and neither is a larger copy's that would be published instead;
                          * the winner did not come from a relaxed query;
                          * not a cache hit;
                          * the winner's page barcode does not differ from the sheet's;
                          * no other MATCH candidate with a conflicting parsed identity.
    REVIEW_PRESELECTED  a tier 1/2 candidate with MATCH (one whose page barcode differs
                        from the sheet's only when the verifier read brand, size and
                        variant as 'yes', and only when no MATCH without that
                        conflict exists), else a tier 1 candidate the
                        verifier looked at and read as UNSURE, else (only while the
                        verifier is down for the whole SKU) a tier 1 candidate with
                        UNKNOWN, else a corroborated tier-2 UNSURE candidate (below);
                        that candidate is pre-checked ('preselected').
                        When the verifier read ANOTHER brand on a tier-1 candidate
                        (brand_refuted) the text evidence that made the tier is not
                        trustworthy for this SKU (a brand that is also a common word,
                        e.g. 'Freshly', matched listings of other brands): the fallback
                        then takes only a candidate whose OWN label confirms the brand
                        (brand_match 'yes' and the printed brand reads as the target,
                        verify.brand_confirmed), else it is off ('vlm:tier1_brand_refuted').
                        A store page that also shows a related product's picture (another
                        brand's pack) no longer hides the right one (live run 2026-10-04,
                        rows 76 and 83).
                        Among several tier-1 UNSURE candidates, one the reader left UNSURE by
                        itself comes before one whose 'no' the code set aside (a size within the
                        tolerance, verify.size_close; a flag its own text cannot support,
                        verify.overruled_flags), then rank order: the pick of a row that had
                        one before stays the same (live run 2026-10-04 19:33, rows 5 and 9).
                        Tier-2 fallback ('preselected:tier2_corroborated'; live run 2026-10-04,
                        rows 62, 71 and 73: the net size is not legible on the front, the title
                        lacks a variant word or the source is a generic store) - a tier-2
                        UNSURE candidate with every one of the rules below; among several, a
                        trusted page first, then a picture that is not low resolution, then
                        rank order (row 73: Union Coop's picture, not a 619x368 one):
                          * usable (eligible, fetched, quality hard_ok), no page barcode that
                            differs from the sheet's, no variant doubt on its own listing
                            (image file name, a related variant line);
                          * its label carries the identity (label_carries_identity): the brand
                            confirmed, a front packshot, the size not read as different (one
                            unit of a multipack excepted) and any printed size agreeing, no
                            printed variant conflict, the variant 'yes' when the SKU states one
                            or the label prints a marked one the SKU does not (else not 'no'),
                            and no other pack counted;
                          * its listing text proves the size (listing_states_size): a size match
                            in the title, page title or URL slug, never only the image file
                            name, and no other size or pack in a URL;
                          * corroborated: at least two distinct page domains among the usable
                            tier 1/2 candidates whose label carries the identity, or its own
                            page is trusted (brand-official, UAE retailer or structured:
                            score.TRUST_STRUCTURED or above, as score's tier-1 'trusted_page');
                          * brand_refuted applies with the same own-label exception (refuted_for);
                            a label that carries the identity confirms the brand.
                        It is never auto-published (tier 1 and MATCH stay required) and keeps
                        every review warning.
    REVIEW_UNSELECTED   candidates exist but none qualifies; nothing is pre-checked.
    NOT_FOUND           providers were healthy and nothing survived the hard filters:
                        failure_code NO_RESULTS (empty pool) or ALL_CONFLICTED.
    PROVIDER_DOWN       nothing survived and every search provider is error/quota/blocked,
                        or the pool is empty and the main query (custom or Q1) was
                        answered by no provider (later 'empty' answers do not count),
                        or every web search provider is down and only the lookups (the
                        local catalog index, Open Food Facts) gave survivors: their
                        answers never hide an outage, the product is searched again.

failure_code on review decisions
    VERIFIER_DOWN       verification unknown (or not run) while verifiable candidates
                        exist; a tier 1 candidate is still preselected. Also when one
                        of two verifier calls failed and nothing was read as MATCH.
    DOWNLOAD_FAILED     candidates survived the identity rules but every fetch failed.
    SOCIAL_ONLY         every tier-1/2 survivor is a social-network post whose picture could
                        not be downloaded (whatever happened to other brands' listings): the
                        post links are in outcome.social_links for the reviewer.

Only the winner of REVIEW_PRESELECTED / AUTO_PUBLISH has status 'preselected'. The
others are 'eligible' or 'rejected' (with reasons) or stay 'excluded' when the
pipeline removed them as reviewer negatives. A candidate whose download failed,
whose image failed a hard quality gate or whose verdict is MISMATCH is never
preselected.

Review warnings ('warn:<code>' reasons on the winner) tell the reviewer what to
double-check before approving. They never change the winner or the decision.
    sheet_silent:<axis>=<value>  the listing text (title, page title, the product's own
                                 slug) or the label reading states a marked variant on
                                 an axis the SKU does not state ('thin' fries, 'shredded')
    vlm_unsure                   pre-checked without a MATCH (tier 1 UNSURE or UNKNOWN, or the
                                 corroborated tier-2 fallback)
    multipack_unit_image         the picture shows ONE unit of a multipack SKU (one can of
                                 '3X185GM'): the label's only 'no' was the size, its printed size
                                 is the per-unit size (verify.multipack_unit_image)
    size_close:<printed>/<sheet> the label's only 'no' was the size and its printed size is the
                                 sheet's within the size tolerance but not exactly ('840g/850g':
                                 '840ge' on an 850G SKU, verify.size_close): check and fix the sheet
    low_resolution               the downloaded image's short side is below 500 px
    chat_or_screenshot           the image file is a chat or screenshot export
                                 ('WhatsApp Image ...', 'IMG-20251014-WA0003', 'Screenshot')
    social_media                 the image or page host is a social network
    foreign_store                the page is a store outside the UAE: a non-UAE country
                                 TLD ('.sa', '.ca', '.co.uk'; not generic ones like '.io'),
                                 a non-UAE retailer, or a UAE retailer's other-country
                                 section ('noon.com/saudi-en/'); never the brand's own site
    barcode_conflict             the page carries a valid barcode that differs from the
                                 sheet's (GTIN_POLICY 'evidence': tier 2 at most)
    brand_spelling:<spelling>    the brand is confirmed only in the stores' spelling of a
                                 sheet brand they write differently (brand_discovery:
                                 'Rio Mare' for 'RIO MARIE', 'Super Tasty' for 'SUP/T');
                                 approving the pick teaches it (catalog_match.learning).
                                 It stays on once the spelling is learned, so a
                                 WRONG_BRAND rejection can still count against it

Overruled flags ('vlm:flag_overruled:size' / 'vlm:flag_overruled:variant' reasons, not warnings): on every
candidate whose reading is UNSURE only because verify.overruled_flags set aside a 'no' its own verbatim text
cannot support (a size 'no' with nothing printed read, a variant 'no' whose printed text holds every word
describing the SKU). The reader's doubt stays visible to the reviewer ('vlm_unsure' on a pick, the reason in
the export and the review screen's detail line); it is never a MATCH, so never an auto-publish.

Display-only warnings (candidate_warnings): the review screen shows warnings under every
eligible candidate, not only the pick, so a reviewer who chooses an alternative sees the same
cautions. They are computed after route() from the same evidence and are never written to
RankedCandidate.reasons: route(), resolution_upgrade(), expand and every auto-publish rule never
read them, and the winner, the tiers and the decision stay exactly as route() made them.
Two codes exist only there:
    size_unverified              the sheet states a size (or a pack count) and neither the
                                 listing evidence (size / pack match, or the sheet's own
                                 barcode on the page) nor the label reading ('yes') confirmed it,
                                 e.g. a tier-2 pick the verifier read with size_match 'unsure'
    variant_unverified           the sheet states a variant and neither the listing (every stated
                                 axis matched, or the sheet's barcode) nor the label reading
                                 confirmed it

Best-resolution copy (resolution_upgrade): once the winner and the decision are fixed, a
fetched copy of the same picture (pHash distance <= 6, aspect within 10 %) with a larger
short side is published instead when its own listing evidence is no weaker and it adds no
risk (see resolution_upgrade()). The decision and the verifier reading stay the winner's;
the copy carries 'resolution_upgrade:<winner short side>x<copy short side>' and the
replaced winner 'resolution_upgrade:replaced'.
"""

from __future__ import annotations

import logging
import re
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple, Union
from urllib.parse import unquote

from . import quality as quality_mod
from . import settings
from . import variants as variants_mod
from .gtin import same_gtin
from .models import (
    Candidate, ProviderHealth, ProviderResult, RankedCandidate, SearchOutcome, Size, SkuSpec,
    VerificationResult,
)
from .fetch import phash_distance
from .score import IDENTITY_KEYS, TRUST_STRUCTURED, page_host, rank_key, trusted_domains
from .sizes import compare, parse_sizes, product_size
from .text_norm import brand_in, domain_matches, match_key, match_string, normalize, store_market, url_host, url_path_text
from .verify import brand_confirmed, multipack_unit_image, overruled_flags, size_agreement, size_close

logger = logging.getLogger(__name__)

# Lookups, not web searches: their answers never make a search outage look healthy (local_index: the
# local catalog index, catalog_match.local_index).
LOOKUP_PROVIDERS = frozenset({"off", "open_food_facts", "openfoodfacts", "local_index"})
DOWN_STATUSES = frozenset({"error", "quota", "blocked"})
MATCH, MISMATCH, UNSURE, UNKNOWN = "MATCH", "MISMATCH", "UNSURE", "UNKNOWN"

WARN_PREFIX = "warn:"
# Display-only codes (candidate_warnings): shown to the reviewer, never read by routing.
DISPLAY_ONLY_WARNING_CODES = ("size_unverified", "variant_unverified")
# Every review warning code (the dashboard maps each one to an Arabic sentence).
WARNING_CODES = ("sheet_silent", "vlm_unsure", "multipack_unit_image", "size_close", "low_resolution",
                 "chat_or_screenshot", "social_media", "foreign_store", "barcode_conflict",
                 "brand_spelling") + DISPLAY_ONLY_WARNING_CODES
# Reason on a candidate whose label reading said 'no' to a flag its own verbatim text cannot support
# (verify.overruled_flags): 'vlm:flag_overruled:size' / 'vlm:flag_overruled:variant'.
FLAG_OVERRULED = "vlm:flag_overruled"

RESOLUTION_PREFIX = "resolution_upgrade"
# Reason prefixes written by route(); recomputed on every call so route() is idempotent.
_ROUTE_PREFIXES = ("hard:", "download:", "quality:", "vlm:", "preselected:", "auto_blocked:", "auto_publish",
                   "gtin:", RESOLUTION_PREFIX, WARN_PREFIX)

# Best-resolution copy: another fetched copy of the winning image is published instead when it
# is the same picture (pHash distance and aspect ratio) with a larger short side.
RESOLUTION_PHASH_MAX = 6
RESOLUTION_ASPECT_TOL = 0.10
# Warnings that a larger copy may differ in without adding a concern: it is larger by
# construction, and the verifier reading stays the winner's.
_UPGRADE_NEUTRAL_WARNINGS = frozenset({"low_resolution", "vlm_unsure"})

# File names that chat apps and screenshot tools give exported images.
_CHAT_OR_SCREENSHOT_RE = re.compile(
    r"whats\s*app[\s_-]*image"                      # WhatsApp Image 2025-10-14 at 10.07.41 AM.jpeg
    r"|(?<![a-z])img[-_]\d{8}[-_]wa\d+"             # IMG-20251014-WA0003.jpg (WhatsApp on Android)
    r"|screen[\s_-]*shot"                           # Screenshot_20251014.png, Screen Shot 2025-10-14 at ...
    r"|(?<![a-z])signal[-_]\d{4}-\d{2}-\d{2}"       # signal-2025-10-14-100741.jpeg
    r"|(?<![a-z])photo_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}"    # photo_2025-10-14_10-07-41.jpg (Telegram)
    r"|صور[ةه][\s_-]*واتساب"                        # صورة واتساب بتاريخ 2025-10-14 ... (WhatsApp in Arabic)
    r"|لقط[ةه][\s_-]*شاش[ةه]",                      # لقطة شاشة 2025-10-14 ... (screenshot in Arabic)
    re.IGNORECASE,
)
# Social networks and their image CDNs, matched on one host label ('i.pinimg.com', 'pinterest.co.uk').
_SOCIAL_LABELS = frozenset({
    "instagram", "cdninstagram", "facebook", "fbcdn", "fbsbx", "tiktok", "tiktokcdn", "tiktokcdn-us",
    "pinterest", "pinimg", "twitter", "twimg", "snapchat", "reddit", "redditmedia", "youtube", "ytimg",
})
_SOCIAL_DOMAINS = ("x.com", "fb.com", "t.co", "redd.it", "threads.net")
# Country TLDs of the other markets the catalogue's products are also sold in.
_FOREIGN_TLDS = frozenset({"kw", "sa", "qa", "om", "bh", "in", "pk", "eg", "jo"})
# Two-letter domains sold as generic names (start-ups, media, shops): no country, so no warning on their own.
_GENERIC_CCTLDS = frozenset({"io", "co", "me", "tv", "ai", "ly", "gg", "fm", "am", "to", "cc", "ws", "so", "sh",
                             "is", "it", "la", "nu", "pw", "vc", "tk"})


def _is_foreign_tld(host: str) -> bool:
    """A country domain outside the UAE ('.sa', '.ca', '.co.uk'); not '.ae' and not a generic-use one ('.io')."""
    tld = host.rsplit(".", 1)[-1]
    if tld in _FOREIGN_TLDS:
        return True
    return len(tld) == 2 and tld.isalpha() and tld != "ae" and tld not in _GENERIC_CCTLDS
# 'other_retail' stores (trusted_domains.json) that are UAE stores without an .ae domain.
_UAE_DOTCOM_STORES = ("westzone.com", "instashop.com")

# Marketplaces whose first host label names the country store: angola.desertcart.com sells the Angolan
# listing (live run 2026-10-03), uae.desertcart.com the UAE one; www. says nothing either way.
_COUNTRY_SUBDOMAIN_STORES = ("desertcart.com",)
_UAE_STORE_LABELS = frozenset({"uae", "ae", "dubai"})
_NEUTRAL_HOST_LABELS = frozenset({"www", "m", "shop", "store"})


def _country_store_label(host: str) -> Optional[str]:
    """The country label of a country-subdomain marketplace host ('angola'), else None."""
    for base in _COUNTRY_SUBDOMAIN_STORES:
        if host.endswith("." + base):
            label = host[: -len(base) - 1].rsplit(".", 1)[-1]
            return None if label in _NEUTRAL_HOST_LABELS else label
    return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _as_health(item) -> ProviderHealth:
    if isinstance(item, ProviderHealth):
        return item
    if isinstance(item, ProviderResult):
        return item.health()
    return ProviderHealth(provider=str(getattr(item, "provider", "")), status=str(getattr(item, "status", "")),
                          http_status=getattr(item, "http_status", None),
                          latency_ms=getattr(item, "latency_ms", None), error=getattr(item, "error", None),
                          query_id=str(getattr(item, "query_id", "") or ""))


def _as_results(verification) -> List[VerificationResult]:
    if verification is None:
        return []
    if isinstance(verification, VerificationResult):
        return [verification]
    return [v for v in verification if v is not None]


def providers_down(health: Sequence[ProviderHealth]) -> bool:
    """True when no search provider answered (every one is error / quota / blocked).

    The Open Food Facts GTIN lookup and the local catalog index do not search the web
    for images, so their answers do not make an outage of the image search providers healthy.
    With no web search health at all nothing searched the web, which also counts as down.
    """
    search = [h for h in health if (h.provider or "").lower() not in LOOKUP_PROVIDERS]
    if not search:
        return True
    by_provider: Dict[str, List[str]] = {}
    for h in search:
        by_provider.setdefault((h.provider or "").lower(), []).append((h.status or "").lower())
    return all(all(s in DOWN_STATUSES for s in statuses) for statuses in by_provider.values())


MAIN_QUERY_IDS = ("custom", "Q1")


def main_query_down(health: Sequence[ProviderHealth]) -> bool:
    """True when the main query (the staff custom query, else Q1) got no answer from any provider.

    Later queries that answered 'empty' do not prove the product is missing when the
    query most likely to find it never ran: that is an outage (retryable), not NOT_FOUND.
    """
    search = [h for h in health if (h.provider or "").lower() not in LOOKUP_PROVIDERS]
    if not search:
        return False
    main_id = next((h.query_id for h in search if h.query_id in MAIN_QUERY_IDS), search[0].query_id)
    statuses = [(h.status or "").lower() for h in search if h.query_id == main_id]
    return bool(statuses) and all(s in DOWN_STATUSES for s in statuses)


def auto_publish_allowed(spec: SkuSpec) -> bool:
    """AUTO_PUBLISH_ENABLED and the brand (or 'category:<name>') is allow-listed, or '*'."""
    if not settings.auto_publish_enabled():
        return False
    entries = settings.auto_publish_brands()
    brands = {normalize(b) for b in (spec.brand_canonical, spec.brand_raw) if b and b.strip()}
    category = normalize(spec.category) if spec.category else ""
    for entry in entries:
        entry = str(entry).strip()
        if entry == "*":
            return True
        if entry.lower().startswith("category:"):
            if category and normalize(entry.split(":", 1)[1]) == category:
                return True
        elif normalize(entry) in brands:
            return True
    return False


def _decision_of(rc: RankedCandidate) -> str:
    return rc.verdict.decision if rc.verdict is not None else UNKNOWN


def _identity_rejected(rc: RankedCandidate) -> bool:
    return rc.score is None or rc.score.tier is None or bool(rc.score.hard_reject)


def _printed_size(text: str) -> Optional[Size]:
    ps = product_size(parse_sizes(text, "vlm")) if text else None
    return ps if isinstance(ps, Size) else None


def _sub_brands_read(spec: Optional[SkuSpec], brand_text: str) -> Set[str]:
    if spec is None or not brand_text:
        return set()
    phrases = tuple(spec.required_brands) + tuple(spec.sibling_brands)
    return {p for p in phrases if brand_in(p, brand_text)}


def identity_conflict(a: RankedCandidate, b: RankedCandidate, spec: Optional[SkuSpec] = None) -> Optional[str]:
    """Why two MATCH candidates cannot show the same SKU, or None when they agree."""
    va, vb = a.verdict, b.verdict
    if va is None or vb is None:
        return None
    ba, bb = _sub_brands_read(spec, va.brand_text), _sub_brands_read(spec, vb.brand_text)
    if ba and bb and ba.isdisjoint(bb):
        return "brand"
    sa, sb = _printed_size(va.size_text), _printed_size(vb.size_text)
    if sa is not None and sb is not None and sa.dimension == sb.dimension:
        if compare(sa, [sb]) == "conflict":
            return "size"
        if (sa.pack_count or 1) != (sb.pack_count or 1):
            return "pack"
    context = variants_mod.spec_context(spec) if spec is not None else None
    pa = variants_mod.extract_variants(va.variant_text, context)
    pb = variants_mod.extract_variants(vb.variant_text, context)
    axes = variants_mod.conflicts(pa, pb) or variants_mod.soft_conflicts(pa, pb)
    if axes:
        return f"variant:{axes[0]}"
    if va.pack_count and vb.pack_count and va.pack_count != vb.pack_count:
        return "pack"
    if same_gtin(a.candidate.gtin_on_page, b.candidate.gtin_on_page) is False:
        return "gtin"
    return None


def gtin_conflict(rc: RankedCandidate) -> bool:
    """True when the candidate's page carries a valid GTIN that differs from the sheet's."""
    matched = (rc.score.matched or {}) if rc.score is not None else {}
    return matched.get("gtin") == "mismatch"


def full_match(spec: SkuSpec, rc: RankedCandidate) -> bool:
    """MATCH with the verifier reading the brand and the size (and every stated variant) as 'yes'.

    A candidate whose page barcode differs from the sheet's needs this reading to be pre-checked:
    the same brand's other sizes and flavours carry other barcodes.
    """
    v = rc.verdict
    if v is None or v.decision != MATCH:
        return False
    if v.brand_match != "yes" or v.size_match != "yes":
        return False
    return v.variant_match == "yes" if spec.variants else v.variant_match != "no"


def brand_refuted(ranked: Sequence[RankedCandidate]) -> bool:
    """True when the verifier read a DIFFERENT brand on a tier-1 candidate.

    Tier 1 rests on the brand being found in the listing text. When the label of such a
    listing shows another brand, that text evidence is unreliable for this SKU, so an
    unconfirmed (UNSURE/UNKNOWN) candidate must not be pre-checked on it either: only one
    whose own label confirms the brand may be (refuted_for).
    """
    for rc in ranked:
        v = rc.verdict
        if (v is not None and v.decision == MISMATCH and v.brand_match == "no"
                and rc.score is not None and rc.score.tier == 1 and not rc.score.hard_reject):
            return True
    return False


def refuted_for(spec: SkuSpec, ranked: Sequence[RankedCandidate], rc: RankedCandidate) -> bool:
    """True when brand_refuted keeps `rc` from being pre-checked without a MATCH: another brand was read on a
    tier-1 listing and rc's OWN label does not confirm the brand (verify.brand_confirmed: brand_match 'yes'
    and the printed brand reads as the target, the sub-brand the SKU names included).

    Live run 2026-10-04, row 76 'ZWAN TURKEY LUNCHEON MEAT WITH HERB850GM': the Carrefour page also showed a
    Bordon pack (a related product), which turned the fallback off for the whole SKU although Lulu's 'Zwan
    Turkey Luncheon Meat With Herbs 850 g' label read ZWAN. A label with no brand read, or another one, is still
    blocked: the 'Freshly' listings of other brands (live run 2026-09-30, row 34) stay unselected.
    """
    return brand_refuted(ranked) and not brand_confirmed(spec, rc.verdict)


# ---------------------------------------------------------------------------
# The tier-2 fallback (REVIEW_PRESELECTED 'preselected:tier2_corroborated')
# ---------------------------------------------------------------------------

# Listing fields whose size is the listing's own statement of the product (never only the image file name).
_LISTING_SIZE_FIELDS = ("title", "page_title", "page_slug")
# Variant doubts on a tier-2 listing's own evidence: another variant in its image file name, or a closely related
# variant line (Diet vs Zero Sugar). The label reading that the fallback rests on is not asked to settle them.
_TIER2_VARIANT_DOUBTS = ("image_variant_conflict", "soft_variant_conflict")


def label_carries_identity(spec: SkuSpec, rc: RankedCandidate) -> bool:
    """The label reading states the SKU's identity as far as the label is legible (the tier-2 fallback).

    Every one of: the brand confirmed on the label (verify.brand_confirmed), a front packshot, size_match not
    'no' (one unit of a multipack SKU excepted: verify.multipack_unit_image) and a printed size, when one was
    read, that agrees with the SKU; no printed variant that conflicts (or nearly conflicts) with the SKU;
    variant_match 'yes' when the SKU states a variant or the label prints a marked one the SKU does not state,
    else not 'no'; and no other pack counted on the picture (the SKU's pack, the pieces one unit holds, or
    unknown). Only a size the label does not show legibly is left open.
    """
    v = rc.verdict
    if v is None or v.view != "front_packshot" or not brand_confirmed(spec, v):
        return False
    unit_of_multipack = multipack_unit_image(spec, v)
    if v.size_match == "no" and not unit_of_multipack:
        return False
    if size_agreement(spec, v.size_text) not in ("match", "unknown"):
        return False
    context = variants_mod.spec_context(spec)
    target = variants_mod.target_variants(spec, v.variant_text)
    printed = variants_mod.extract_variants(v.variant_text, context, variants_mod.spec_brands(spec))
    if variants_mod.conflicts(target, printed) or variants_mod.soft_conflicts(target, printed):
        return False
    # a variant the SKU states, or a marked one only the label states ('HOT & SPICY' under a sheet typo), needs the
    # reader's 'yes' (it was shown the sheet's own name); otherwise it is enough that the variant is not read as 'no'
    if spec.variants or variants_mod.unstated_marked(target, printed, context):
        if v.variant_match != "yes":
            return False
    elif v.variant_match == "no":
        return False
    counted = spec.size is not None and spec.size.dimension == "count"
    pieces = spec.size.pieces if spec.size is not None else None
    return (counted or unit_of_multipack or v.pack_count in (None, spec.pack_count or 1)
            or (pieces is not None and v.pack_count == pieces))


def listing_states_size(rc: RankedCandidate) -> bool:
    """The listing's own text proves the SKU's size: score size 'match' found in the title, the page title or the
    page's URL slug (never only in the image file name), and no other size or pack in a URL
    (score.url_only_size_conflict). Live run 2026-10-04, row 10 'MR JOHN FRENCH FRIES 900GM': the only Mr John
    picture's file name says 2.5Kg and its page title states no size, so it stays unselected."""
    score = rc.score
    if score is None or score.size_status != "match" or score.url_only_size_conflict:
        return False
    fields = (score.matched or {}).get("size_fields") or {}
    return any(fields.get(name) == "match" for name in _LISTING_SIZE_FIELDS)


def _page_domain(rc: RankedCandidate) -> str:
    return (rc.score.matched or {}).get("page_domain") or page_host(rc.candidate) or url_host(rc.candidate.image_url)


def tier2_corroborated(spec: SkuSpec, verifiable: Sequence[RankedCandidate], ranked: Sequence[RankedCandidate]
                       ) -> Optional[RankedCandidate]:
    """The tier-2 UNSURE candidate the tier-2 fallback may pre-check, or None.

    See the module docstring (REVIEW_PRESELECTED): usable, no differing page barcode and no variant doubt on its
    own listing, label_carries_identity, listing_states_size, and corroborated by a second page domain whose label
    carries the identity too, or by its own trusted page (official, UAE retailer, structured). The refined
    brand_refuted rule applies as to the tier-1 fallback (refuted_for); a label that carries the identity
    confirms the brand, so it is never what blocks this pick.
    Among the candidates that qualify, a trusted page comes first, then a picture that is not low resolution,
    then rank order: these pictures are all the same product, the reviewer should see the best copy (live run
    2026-10-04, row 73: a 619x368 picture of a generic site ranked above Lulu's own picture).
    """
    readers = [rc for rc in verifiable if rc.score.tier in (1, 2) and label_carries_identity(spec, rc)]
    domains = {d for d in (_page_domain(rc) for rc in readers) if d}
    qualified: List[Tuple[bool, bool, int, RankedCandidate]] = []
    for order, rc in enumerate(readers):
        if rc.score.tier != 2 or _decision_of(rc) != UNSURE or gtin_conflict(rc):
            continue
        if any(str(c).startswith(_TIER2_VARIANT_DOUBTS) for c in rc.score.conflicts or ()):
            continue
        if not listing_states_size(rc) or refuted_for(spec, ranked, rc):
            continue
        trusted = int((rc.score.matched or {}).get("source_trust") or 0) >= TRUST_STRUCTURED
        if trusted or len(domains) >= 2:
            low_res = bool(rc.fetched is not None and rc.fetched.ok
                           and quality_mod.low_resolution(rc.fetched.width, rc.fetched.height))
            qualified.append((not trusted, low_res, order, rc))
    return min(qualified, key=lambda q: q[:3])[3] if qualified else None


def no_set_aside(spec: SkuSpec, rc: RankedCandidate) -> bool:
    """The reader answered 'no' to a flag and only verify.size_close or verify.overruled_flags made it UNSURE."""
    v = rc.verdict
    return (v is not None and v.decision == UNSURE
            and (size_close(spec, v) is not None or bool(overruled_flags(spec, v))))


def _add(counts: Dict[str, int], key: str) -> None:
    counts[key] = counts.get(key, 0) + 1


def _reset(rc: RankedCandidate) -> bool:
    """Drop route-owned reasons; return True when the pipeline rejected it for its own reason."""
    kept = [r for r in rc.reasons if not str(r).startswith(_ROUTE_PREFIXES)]
    pipeline_rejected = rc.status == "rejected" and len(kept) > 0
    rc.reasons[:] = kept
    return pipeline_rejected


# ---------------------------------------------------------------------------
# Review warnings
# ---------------------------------------------------------------------------

def _sheet_silent(spec: SkuSpec, rc: RankedCandidate) -> List[str]:
    """'sheet_silent:<axis>=<value>' for each marked variant the pick states and the SKU does not."""
    cand = rc.candidate
    context = variants_mod.spec_context(spec)
    # The product's own slug segment only: department breadcrumbs ('/fresh-food/') are not the product.
    texts = (cand.title, cand.page_title, url_path_text(cand.page_url, product_segment=True),
             rc.verdict.variant_text if rc.verdict is not None else "")
    # read like score_candidate: the listing's own context, and the SKU re-read in it
    read_context = " ".join([context] + [t for t in texts if t])
    brands = variants_mod.spec_brands(spec)
    found = variants_mod.merge(*(variants_mod.extract_variants(t, read_context, brands) for t in texts))
    target = variants_mod.target_variants(spec, *texts)
    out = []
    for axis in sorted(variants_mod.unstated_marked(target, found, context)):
        marked = variants_mod.values_of(found[axis]) - variants_mod.unmarked_values(axis, context)
        out.append(f"sheet_silent:{axis}={variants_mod.SEP.join(sorted(marked))}")
    return out


def _chat_or_screenshot(image_url: str) -> bool:
    text = unquote(unquote(image_url or "")).replace("+", " ")
    return _CHAT_OR_SCREENSHOT_RE.search(text) is not None


def _social_host(host: str) -> bool:
    if not host:
        return False
    return domain_matches(host, _SOCIAL_DOMAINS) or not _SOCIAL_LABELS.isdisjoint(host.split(".")[:-1])


def foreign_host(host: str) -> bool:
    """True when the host alone says a store outside the UAE: a country label ('angola.desertcart.com'),
    a foreign country domain ('.sa', '.co.uk') or a listed foreign store (trusted_domains.json other_retail)."""
    label = _country_store_label(host)
    if label is not None:
        return label not in _UAE_STORE_LABELS
    if host.endswith(".ae") or domain_matches(host, _UAE_DOTCOM_STORES):
        return False
    if _is_foreign_tld(host):
        return True
    return domain_matches(host, trusted_domains().get("other_retail", []))


def _foreign_store(spec: SkuSpec, cand: Candidate) -> bool:
    """True when the page is a store outside the UAE (the pack may differ from the UAE one)."""
    host = page_host(cand) or url_host(cand.image_url)
    if not host or domain_matches(host, spec.official_domains):
        return False
    if _country_store_label(host) is None and domain_matches(host, trusted_domains().get("uae_retailers", [])):
        return store_market(cand.page_url) == "foreign"
    return foreign_host(host)


def review_warnings(spec: SkuSpec, rc: RankedCandidate, reading_of: Optional[RankedCandidate] = None
                    ) -> List[str]:
    """Warning codes for a pre-checked candidate: what the reviewer should double-check first.

    reading_of, when given, is the candidate whose verifier reading the decision rests on (the
    winner, when a larger copy of its picture is published instead of it); by default rc itself.
    """
    cand = rc.candidate
    if reading_of is not None and reading_of is not rc:
        rc = RankedCandidate(candidate=rc.candidate, score=rc.score, fetched=rc.fetched, quality=rc.quality,
                             verdict=reading_of.verdict, status=rc.status)
    out = _sheet_silent(spec, rc)
    if _decision_of(rc) != MATCH:
        out.append("vlm_unsure")
    if rc.verdict is not None and multipack_unit_image(spec, rc.verdict):
        out.append("multipack_unit_image")
    close = size_close(spec, rc.verdict) if rc.verdict is not None else None
    if close is not None and spec.size is not None:
        out.append(f"size_close:{close.canonical()}/{spec.size.canonical()}")
    if rc.fetched is not None and rc.fetched.ok and quality_mod.low_resolution(rc.fetched.width, rc.fetched.height):
        out.append("low_resolution")
    if _chat_or_screenshot(cand.image_url):
        out.append("chat_or_screenshot")
    if _social_host(url_host(cand.image_url)) or _social_host(page_host(cand)):
        out.append("social_media")
    if _foreign_store(spec, cand):
        out.append("foreign_store")
    if gtin_conflict(rc):
        out.append("barcode_conflict")
    spelling = _store_spelling(spec)
    if spelling and _brand_spelling_only(spec, rc):
        out.append(f"brand_spelling:{spelling}")
    return out


def _store_spelling(spec: SkuSpec) -> str:
    """The store spelling the SKU's brand is searched under instead of the sheet's: the one brand_discovery
    found, or one the reviewers taught (brand_conf 'learned' under another name); '' for neither."""
    if spec.discovered_brands:
        return spec.discovered_brands[0]
    if spec.brand_conf == "learned" and spec.brand_canonical \
            and match_key(spec.brand_canonical) != match_key(spec.brand_raw):
        return spec.brand_canonical
    return ""


def _brand_spelling_only(spec: SkuSpec, rc: RankedCandidate) -> bool:
    """The brand evidence of this pick is only the store spelling (_store_spelling), never the sheet's own.

    It stays a warning while the spelling is learned: the reviewer's approval keeps counting for it and a
    WRONG_BRAND rejection against it (catalog_match.learning), so one mistaken approval can be undone.
    """
    hits = {match_string(p) for p in ((rc.score.matched or {}).get("brand_fields") or {}).values()} \
        if rc.score is not None else set()
    if spec.discovered_brands:
        return not (hits - {match_string(d) for d in spec.discovered_brands if d})
    return match_string(spec.brand_raw) not in hits


def social_only_links(survivors: Sequence[RankedCandidate]) -> List[str]:
    """The post links when every tier-1/2 survivor is a social-network post whose picture could not be
    downloaded; [] otherwise (failure_code SOCIAL_ONLY).

    Live run 2026-10-03, rows 29, 38 and 41 (CHALIYAR, KABANI, MAHRA MEAT MASALA): the brand was found only
    in Instagram and Facebook posts, whose pictures those networks refuse to hand out. Row 29 read
    DOWNLOAD_FAILED and rows 38 and 41 no failure at all (other brands' store listings had been read), so the
    reviewer was never told where the product had been seen. The networks' blocking is never worked around.
    """
    brand = [rc for rc in survivors if rc.score is not None and rc.score.tier in (1, 2)]
    if not brand or not all(_social_post(rc.candidate) and rc.fetched is not None and not rc.fetched.ok
                            for rc in brand):
        return []
    return list(dict.fromkeys(rc.candidate.page_url or rc.candidate.image_url for rc in brand))


def _social_post(cand: Candidate) -> bool:
    return _social_host(url_host(cand.image_url)) or _social_host(page_host(cand))


def warning_codes(reasons: Iterable[str]) -> List[str]:
    """The warning codes (without 'warn:') among a candidate's reasons."""
    return [str(r)[len(WARN_PREFIX):] for r in reasons or () if str(r).startswith(WARN_PREFIX)]


# ---------------------------------------------------------------------------
# Display-only warnings (the review screen; never read by routing)
# ---------------------------------------------------------------------------

def unverified_warnings(spec: SkuSpec, rc: RankedCandidate) -> List[str]:
    """'size_unverified' / 'variant_unverified' for one candidate (display only, see candidate_warnings).

    The sheet states a size (or a pack count) / a variant, and neither the candidate's listing evidence nor
    the label reading (rc.verdict, 'yes') confirmed it. The sheet's own barcode on the page confirms both.
    """
    score = rc.score
    matched = (score.matched or {}) if score is not None else {}
    if matched.get("gtin") == "match":
        return []
    v = rc.verdict
    out: List[str] = []
    size_confirmed = (score is not None and score.size_status == "match") or (v is not None and v.size_match == "yes")
    # the verifier reads the unit count apart from the net content (verify.build_prompt): its pack_count
    pack_confirmed = matched.get("pack") == "match" or (v is not None and v.pack_count == spec.pack_count)
    if (spec.size is not None and not size_confirmed) or (spec.pack_count and spec.pack_count > 1
                                                          and not pack_confirmed):
        out.append("size_unverified")
    if spec.variants and not set(spec.variants) <= set(matched.get("variants") or ()) \
            and not (v is not None and v.variant_match == "yes"):
        out.append("variant_unverified")
    return out


def candidate_warnings(spec: SkuSpec, rc: RankedCandidate,
                       reading_of: Optional[RankedCandidate] = None) -> List[str]:
    """Display-only warning codes for one reviewable candidate: the pick and every eligible alternative.

    The pre-checked candidate keeps exactly its 'warn:' reasons (route's review_warnings, with the reading the
    decision rests on: reading_of, the replaced winner of a best-resolution copy) and gains the display-only
    codes. An eligible alternative gets review_warnings() of its own evidence, without 'vlm_unsure' when the
    verifier never read it (that is not a doubt of the reader). Rejected and excluded candidates get none: the
    screen says why they were set aside.

    Pure: it reads the candidate and never writes rc.reasons or rc.status, so it cannot change the winner,
    the tiers, the decision or any auto-publish rule (route() and resolution_upgrade() never call it).
    """
    if rc.status not in ("preselected", "eligible") or _identity_rejected(rc):
        return []
    reading = reading_of if reading_of is not None else rc
    if rc.status == "preselected":
        out = warning_codes(rc.reasons)
    else:
        out = review_warnings(spec, rc, reading_of=reading_of)
        if reading.verdict is None:
            out = [w for w in out if w != "vlm_unsure"]
    view = rc
    if reading is not rc:
        view = RankedCandidate(candidate=rc.candidate, score=rc.score, fetched=rc.fetched, quality=rc.quality,
                               verdict=reading.verdict, status=rc.status)
    for code in unverified_warnings(spec, view):
        if code not in out:
            out.append(code)
    return out


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------

def route(spec: SkuSpec, ranked: Sequence[RankedCandidate],
          verification: Union[VerificationResult, Iterable[VerificationResult], None],
          health: Sequence[Union[ProviderHealth, ProviderResult]],
          relaxed_ids: Optional[Set[str]] = None, cache_hit: bool = False) -> SearchOutcome:
    ranked = list(ranked or [])
    relaxed_ids = set(relaxed_ids or ())
    health_list = [_as_health(h) for h in (health or [])]
    results = _as_results(verification)
    vlm_calls = sum(int(r.calls or 0) for r in results)
    n_ok = sum(1 for r in results if r.status == "ok")
    if not results:
        verify_state = "not_run"
    elif n_ok == len(results):
        verify_state = "ok"
    elif n_ok:
        verify_state = "partial"      # one call answered, the other failed
    else:
        verify_state = "unknown"

    reject_counts: Dict[str, int] = {}
    survivors: List[RankedCandidate] = []
    for rc in ranked:
        pipeline_rejected = _reset(rc)
        if rc.status == "excluded":
            _add(reject_counts, "excluded")
            continue
        if _identity_rejected(rc):
            rc.status = "rejected"
            rules = (rc.score.hard_reject if rc.score is not None else ()) or ("unscored",)
            for rule in rules:
                _add(reject_counts, rule)
                rc.reasons.append(f"hard:{rule}")
            continue
        survivors.append(rc)
        rc.status = "rejected" if pipeline_rejected else "eligible"
        if rc.fetched is not None and not rc.fetched.ok:
            rc.status = "rejected"
            code = f"download:{rc.fetched.error or 'error'}"
            rc.reasons.append(code)
            _add(reject_counts, code)
        elif rc.quality is not None and not rc.quality.hard_ok:
            rc.status = "rejected"
            for reason in rc.quality.hard_reasons or ["hard_fail"]:
                code = f"quality:{reason}"
                rc.reasons.append(code)
                _add(reject_counts, code)
        if rc.verdict is not None:
            rc.reasons.append(f"vlm:{rc.verdict.decision}")
            if rc.verdict.decision == UNSURE:
                rc.reasons.extend(f"{FLAG_OVERRULED}:{flag}" for flag in overruled_flags(spec, rc.verdict))
            if rc.verdict.decision == MISMATCH and rc.status != "rejected":
                rc.status = "rejected"
                _add(reject_counts, "vlm:MISMATCH")

    outcome = SearchOutcome(decision="REVIEW_UNSELECTED", ranked=ranked, provider_health=health_list,
                            vlm_calls=vlm_calls, sku_key=spec.sku_key, reject_counts=reject_counts)

    # -- the web search is down and only the lookups answered ------------------
    # (an index page or a GTIN record alone never hides an outage: the product is searched again later)
    if survivors and providers_down(health_list) \
            and all((rc.candidate.provider or "").lower() in LOOKUP_PROVIDERS for rc in survivors):
        outcome.decision, outcome.failure_code = "PROVIDER_DOWN", "PROVIDER_DOWN"
        logger.info("route %s: PROVIDER_DOWN (only lookups answered)", spec.sku_key)
        return outcome

    # -- nothing survived the hard filters -------------------------------------
    if not survivors:
        if providers_down(health_list) or (not ranked and main_query_down(health_list)):
            outcome.decision, outcome.failure_code = "PROVIDER_DOWN", "PROVIDER_DOWN"
        else:
            outcome.decision = "NOT_FOUND"
            outcome.failure_code = "ALL_CONFLICTED" if ranked else "NO_RESULTS"
        logger.info("route %s: %s/%s %s", spec.sku_key, outcome.decision, outcome.failure_code, reject_counts)
        return outcome

    attempted = [rc for rc in survivors if rc.fetched is not None]
    social = social_only_links(survivors)
    if attempted and not any(rc.fetched.ok for rc in attempted):
        outcome.decision = "REVIEW_UNSELECTED"
        outcome.failure_code = "SOCIAL_ONLY" if social else "DOWNLOAD_FAILED"
        outcome.social_links = social
        logger.info("route %s: every download failed%s", spec.sku_key, " (social-network posts only)" if social else "")
        return outcome

    def usable(rc: RankedCandidate) -> bool:
        return (rc.status == "eligible" and rc.fetched is not None and rc.fetched.ok
                and (rc.quality is None or rc.quality.hard_ok))

    verifiable = [rc for rc in survivors if usable(rc)]
    verifier_down = verify_state in ("unknown", "not_run")
    if verifiable and verifier_down:
        outcome.failure_code = "VERIFIER_DOWN"

    # A page barcode that differs from the sheet's (tier 2 at most) is pre-checked only on a
    # MATCH that read the brand, the size and the variant as 'yes'; never on the tier-1 fallback.
    for rc in verifiable:
        if gtin_conflict(rc) and _decision_of(rc) == MATCH and not full_match(spec, rc):
            rc.reasons.append("gtin:conflict_needs_full_match")
    # A MATCH without that doubt is preferred to one with it, whatever their rank order.
    winner = next((rc for rc in verifiable if rc.score.tier in (1, 2) and _decision_of(rc) == MATCH
                   and not gtin_conflict(rc)), None)
    if winner is None:
        winner = next((rc for rc in verifiable if rc.score.tier in (1, 2) and _decision_of(rc) == MATCH
                       and gtin_conflict(rc) and full_match(spec, rc)), None)
    why = "vlm_match"
    if winner is None and verify_state == "partial":
        outcome.failure_code = "VERIFIER_DOWN"
    if winner is None:
        # Tier 1 without a MATCH: UNSURE is a reading the verifier made; UNKNOWN is accepted
        # only while the verifier is down for the whole SKU. With the verifier up, UNKNOWN
        # means it never saw the image (a skipped image or a failed second call).
        fallback = (UNSURE, UNKNOWN) if verifier_down else (UNSURE,)
        tier1 = [rc for rc in verifiable if rc.score.tier == 1 and _decision_of(rc) in fallback
                 and not gtin_conflict(rc)]
        # a reading whose 'no' was set aside (a size within the tolerance, a flag its own text cannot support)
        # comes after one the reader left UNSURE by itself; rank order otherwise (a stable sort)
        tier1.sort(key=lambda rc: no_set_aside(spec, rc))
        winner = tier1[0] if tier1 else None
        if winner is not None and refuted_for(spec, ranked, winner):
            # another brand was read on a tier-1 listing: only a candidate whose own label confirms the brand
            confirmed = next((rc for rc in tier1 if not refuted_for(spec, ranked, rc)), None)
            if confirmed is None:
                winner.reasons.append("vlm:tier1_brand_refuted")
                _add(reject_counts, "vlm:tier1_brand_refuted")
            winner = confirmed
        why = f"tier1_{_decision_of(winner).lower()}" if winner is not None else ""
    if winner is None and not verifier_down:
        # a tier-2 label reading with corroborated listing evidence (never auto-published: not tier 1, not MATCH)
        winner = tier2_corroborated(spec, verifiable, ranked)
        why = "tier2_corroborated" if winner is not None else ""
    if winner is None:
        outcome.decision = "REVIEW_UNSELECTED"
        if social and not outcome.failure_code:
            outcome.failure_code, outcome.social_links = "SOCIAL_ONLY", social
        logger.info("route %s: REVIEW_UNSELECTED (%s)", spec.sku_key, outcome.failure_code or "no match")
        return outcome

    winner.status = "preselected"
    winner.reasons.append(f"preselected:{why}")
    outcome.winner = winner
    outcome.decision = "REVIEW_PRESELECTED"

    blockers: List[str] = []
    if not auto_publish_allowed(spec):
        blockers.append("auto_publish_off_for_brand" if settings.auto_publish_enabled() else "auto_publish_disabled")
    if winner.score.tier != 1:
        blockers.append("not_tier1")
    if _decision_of(winner) != MATCH:
        blockers.append("not_vlm_match")
    if not winner.candidate.sanctioned:
        blockers.append("unsanctioned_source")
    if spec.brand_conf != "mapped":
        blockers.append(f"brand_conf_{spec.brand_conf or 'none'}")
    if (winner.score.matched or {}).get("source_class") == "reviewed_source":
        # a site trusted only because reviewers keep approving it (catalog_match.learning): review, never auto
        blockers.append("reviewed_source")
    if winner.candidate.query_id and winner.candidate.query_id in relaxed_ids:
        blockers.append("relaxed_query")
    if cache_hit:
        blockers.append("cache_hit")
    if gtin_conflict(winner):
        blockers.append("barcode_conflict")
    if outcome.failure_code:
        blockers.append(outcome.failure_code.lower())
    elif verify_state == "partial":
        blockers.append("verifier_partial")      # the conflict check below could not see every image
    clash = _conflicting_match(spec, winner, ranked)
    if clash:
        blockers.append(f"conflicting_match:{clash}")

    if blockers:
        winner.reasons.extend(f"auto_blocked:{b}" for b in blockers)
    else:
        outcome.decision = "AUTO_PUBLISH"
        winner.reasons.append("auto_publish")

    # Best-resolution copy: the same picture, larger, from a page that is no weaker.
    published = winner
    copy = resolution_upgrade(spec, winner, ranked, relaxed_ids, outcome.decision)
    if copy is not None:
        moved = [r for r in winner.reasons if r.startswith(("preselected:", "auto_blocked:", "auto_publish"))]
        winner.reasons[:] = [r for r in winner.reasons if r not in moved]
        winner.reasons.append(f"{RESOLUTION_PREFIX}:replaced")
        winner.status = "eligible"
        copy.status = "preselected"
        copy.reasons.extend(moved)
        copy.reasons.append(f"{RESOLUTION_PREFIX}:{_short_side(winner)}x{_short_side(copy)}")
        outcome.winner = published = copy
    warnings = review_warnings(spec, published, reading_of=winner)
    published.reasons.extend(WARN_PREFIX + w for w in warnings)
    logger.info("route %s: %s winner=%s warnings=%s", spec.sku_key, outcome.decision,
                published.candidate.image_url, ",".join(warnings) or "-")
    return outcome


# ---------------------------------------------------------------------------
# Best-resolution copy
# ---------------------------------------------------------------------------

def _conflicting_match(spec: SkuSpec, pick: RankedCandidate, ranked: Sequence[RankedCandidate]) -> Optional[str]:
    """Why another MATCH candidate cannot show the same SKU as `pick` (the first clash), else None."""
    for other in ranked:
        if other is pick or _identity_rejected(other) or other.status == "excluded":
            continue
        if _decision_of(other) != MATCH:
            continue
        clash = identity_conflict(pick, other, spec)
        if clash:
            return clash
    return None


def _short_side(rc: RankedCandidate) -> int:
    f = rc.fetched
    if f is None or not f.ok or not f.width or not f.height:
        return 0
    return int(min(f.width, f.height))


def _same_picture(a: RankedCandidate, b: RankedCandidate) -> bool:
    """pHash distance <= RESOLUTION_PHASH_MAX and the aspect ratio within RESOLUTION_ASPECT_TOL of a's."""
    dist = phash_distance(a.fetched.phash, b.fetched.phash)
    if dist is None or dist > RESOLUTION_PHASH_MAX:
        return False
    ra = a.fetched.width / a.fetched.height
    rb = b.fetched.width / b.fetched.height
    return abs(ra - rb) <= RESOLUTION_ASPECT_TOL * ra


def _identity_not_weaker(copy: RankedCandidate, winner: RankedCandidate) -> bool:
    """The copy's own listing evidence is at least the winner's on every identity key and on source trust.

    Keys (score.rank_key, lower is better): tier, size match, variants matched, class coverage,
    no soft conflict, source trust. A larger picture never buys a weaker listing.
    """
    kc = rank_key(copy.candidate, copy.score)
    kw = rank_key(winner.candidate, winner.score)
    return all(c <= w for c, w in zip(kc[:IDENTITY_KEYS], kw[:IDENTITY_KEYS]))


def resolution_upgrade(spec: SkuSpec, winner: RankedCandidate, ranked: Sequence[RankedCandidate],
                       relaxed_ids: Optional[Set[str]] = None, decision: str = "REVIEW_PRESELECTED"
                       ) -> Optional[RankedCandidate]:
    """The largest copy of the winner's picture that may be published instead of it, or None.

    A copy qualifies only when every one of these holds:
      * it is an eligible, fetched, quality-passing candidate (never hard-rejected, excluded,
        a failed download or a verifier MISMATCH);
      * it is the same picture: pHash distance <= 6 and aspect ratio within 10 %; its short
        side is larger than the winner's;
      * its own identity evidence is no weaker: same or better tier (never downward), size
        match, variants matched, class coverage and page trust, and it carries no score
        conflict (soft size / pack / variant / brand doubt) the winner does not;
      * it adds no risk the winner did not carry: sanctioned when the winner is, not from a
        relaxed query unless the winner is, no barcode conflict or differing page GTIN, no
        identity conflict with the winner's reading, no review warning the winner lacks;
      * its own verifier reading, when it has one, is not weaker than the winner's (UNSURE
        never replaces a MATCH); for AUTO_PUBLISH it must have its own MATCH and no clash
        with any other MATCH candidate.
    The decision and its verifier reading stay the winner's.
    """
    base = _short_side(winner)
    if not base or winner.fetched is None or not winner.fetched.phash or winner.score is None:
        return None
    # Soft doubts on the winner's own listing (the verifier read it as a match with them on record).
    w_conflicts = set(winner.score.conflicts or ())
    relaxed_ids = set(relaxed_ids or ())
    w_decision = _decision_of(winner)
    w_relaxed = bool(winner.candidate.query_id) and winner.candidate.query_id in relaxed_ids
    w_warnings = set(review_warnings(spec, winner)) - _UPGRADE_NEUTRAL_WARNINGS
    best: Optional[RankedCandidate] = None
    for other in ranked:
        if other is winner or other.status != "eligible" or _identity_rejected(other):
            continue
        if other.fetched is None or not other.fetched.ok or not other.fetched.phash:
            continue
        if other.quality is not None and not other.quality.hard_ok:
            continue
        size = _short_side(other)
        if size <= base or (best is not None and size <= _short_side(best)):
            continue
        if not _same_picture(winner, other) or not _identity_not_weaker(other, winner):
            continue
        # A size / pack / variant / brand doubt on the copy's own evidence that the winner does not
        # carry (a low-fat image file name, a 'pack of 6' URL): a grey-scale pHash cannot tell
        # colour variants apart and the verifier never saw this image.
        if not set(other.score.conflicts or ()) <= w_conflicts:
            continue
        if winner.candidate.sanctioned and not other.candidate.sanctioned:
            continue
        if not w_relaxed and other.candidate.query_id and other.candidate.query_id in relaxed_ids:
            continue
        if gtin_conflict(other) or same_gtin(winner.candidate.gtin_on_page, other.candidate.gtin_on_page) is False:
            continue
        o_decision = _decision_of(other)
        if o_decision == MISMATCH or (o_decision == UNSURE and w_decision == MATCH):
            continue
        if decision == "AUTO_PUBLISH" and (o_decision != MATCH or _conflicting_match(spec, other, ranked)
                                           or (other.score.matched or {}).get("source_class") == "reviewed_source"):
            continue      # what blocks the winner's auto-publish blocks a copy's too
        if identity_conflict(winner, other, spec):
            continue
        added = set(review_warnings(spec, other, reading_of=winner)) - _UPGRADE_NEUTRAL_WARNINGS - w_warnings
        if added:
            continue
        best = other
    return best
