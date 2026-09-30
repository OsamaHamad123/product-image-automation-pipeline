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
                          * spec.brand_conf == 'mapped';
                          * the winner did not come from a relaxed query;
                          * not a cache hit;
                          * no other MATCH candidate with a conflicting parsed identity.
    REVIEW_PRESELECTED  a tier 1/2 candidate with MATCH, else a tier 1 candidate the
                        verifier looked at and read as UNSURE, else (only while the
                        verifier is down for the whole SKU) a tier 1 candidate with
                        UNKNOWN; that candidate is pre-checked ('preselected').
                        The tier-1 fallback is off when the verifier read ANOTHER brand
                        on a tier-1 candidate: the text evidence that made the tier is
                        then not trustworthy for this SKU (a brand that is also a common
                        word, e.g. 'Freshly', matched listings of other brands).
    REVIEW_UNSELECTED   candidates exist but none qualifies; nothing is pre-checked.
    NOT_FOUND           providers were healthy and nothing survived the hard filters:
                        failure_code NO_RESULTS (empty pool) or ALL_CONFLICTED.
    PROVIDER_DOWN       nothing survived and every search provider is error/quota/blocked,
                        or the pool is empty and the main query (custom or Q1) was
                        answered by no provider (later 'empty' answers do not count).

failure_code on review decisions
    VERIFIER_DOWN       verification unknown (or not run) while verifiable candidates
                        exist; a tier 1 candidate is still preselected. Also when one
                        of two verifier calls failed and nothing was read as MATCH.
    DOWNLOAD_FAILED     candidates survived the identity rules but every fetch failed.

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
    vlm_unsure                   pre-checked without a MATCH (tier 1, UNSURE or UNKNOWN)
    low_resolution               the downloaded image's short side is below 500 px
    chat_or_screenshot           the image file is a chat or screenshot export
                                 ('WhatsApp Image ...', 'IMG-20251014-WA0003', 'Screenshot')
    social_media                 the image or page host is a social network
    foreign_store                the page is a store outside the UAE: a non-UAE country
                                 TLD, a non-UAE retailer, or a UAE retailer's other-country
                                 section ('noon.com/saudi-en/'); never the brand's own site
"""

from __future__ import annotations

import logging
import re
from typing import Dict, Iterable, List, Optional, Sequence, Set, Union
from urllib.parse import unquote, urlsplit

from . import quality as quality_mod
from . import settings
from . import variants as variants_mod
from .gtin import same_gtin
from .models import (
    Candidate, ProviderHealth, ProviderResult, RankedCandidate, SearchOutcome, Size, SkuSpec,
    VerificationResult,
)
from .score import page_host, trusted_domains
from .sizes import compare, parse_sizes, product_size
from .text_norm import domain_matches, normalize, phrase_in, url_host, url_path_text

logger = logging.getLogger(__name__)

LOOKUP_PROVIDERS = frozenset({"off", "open_food_facts", "openfoodfacts"})
DOWN_STATUSES = frozenset({"error", "quota", "blocked"})
MATCH, MISMATCH, UNSURE, UNKNOWN = "MATCH", "MISMATCH", "UNSURE", "UNKNOWN"

WARN_PREFIX = "warn:"
# Every review warning code (the dashboard maps each one to an Arabic sentence).
WARNING_CODES = ("sheet_silent", "vlm_unsure", "low_resolution", "chat_or_screenshot", "social_media",
                 "foreign_store")

# Reason prefixes written by route(); recomputed on every call so route() is idempotent.
_ROUTE_PREFIXES = ("hard:", "download:", "quality:", "vlm:", "preselected:", "auto_blocked:", "auto_publish",
                   WARN_PREFIX)

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
# 'other_retail' stores (trusted_domains.json) that are UAE stores without an .ae domain.
_UAE_DOTCOM_STORES = ("westzone.com", "instashop.com")
# Country sections of a UAE retailer's site: '/saudi-en/' (noon), '/en-kw/' (Lulu), '/kuwait/' (talabat).
_UAE_MARKETS = frozenset({"ae", "uae"})
_FOREIGN_MARKETS = frozenset({
    "sa", "ksa", "saudi", "kw", "kuwait", "qa", "qatar", "om", "oman", "bh", "bahrain", "eg", "egypt",
    "jo", "jordan", "in", "india", "pk", "pakistan",
})
_LOCALE_WORDS = frozenset({"en", "ar"})


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

    The Open Food Facts GTIN lookup does not search the web for images, so its
    'empty' answer does not make an outage of the image search providers healthy.
    With no health at all nothing was searched, which also counts as down.
    """
    if not health:
        return True
    search = [h for h in health if (h.provider or "").lower() not in LOOKUP_PROVIDERS]
    considered = search or list(health)
    by_provider: Dict[str, List[str]] = {}
    for h in considered:
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
    return {p for p in phrases if phrase_in(p, brand_text)}


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


def brand_refuted(ranked: Sequence[RankedCandidate]) -> bool:
    """True when the verifier read a DIFFERENT brand on a tier-1 candidate.

    Tier 1 rests on the brand being found in the listing text. When the label of such a
    listing shows another brand, that text evidence is unreliable for this SKU, so an
    unconfirmed (UNSURE/UNKNOWN) tier-1 candidate must not be pre-checked either.
    """
    for rc in ranked:
        v = rc.verdict
        if (v is not None and v.decision == MISMATCH and v.brand_match == "no"
                and rc.score is not None and rc.score.tier == 1 and not rc.score.hard_reject):
            return True
    return False


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
    found = variants_mod.merge(*(variants_mod.extract_variants(t, context) for t in texts))
    unmarked = variants_mod.lexicon().unmarked
    out = []
    for axis in sorted(variants_mod.unstated_marked(spec.variants, found)):
        marked = variants_mod.values_of(found[axis]) - unmarked.get(axis, set())
        out.append(f"sheet_silent:{axis}={variants_mod.SEP.join(sorted(marked))}")
    return out


def _chat_or_screenshot(image_url: str) -> bool:
    text = unquote(unquote(image_url or "")).replace("+", " ")
    return _CHAT_OR_SCREENSHOT_RE.search(text) is not None


def _social_host(host: str) -> bool:
    if not host:
        return False
    return domain_matches(host, _SOCIAL_DOMAINS) or not _SOCIAL_LABELS.isdisjoint(host.split(".")[:-1])


def _path_market(page_url: str) -> str:
    """'uae', 'foreign' or '' from the country section of a store page ('/saudi-en/', '/en-ae/', '/kuwait/').

    A leading language-only segment is skipped: talabat's Arabic pages are '/ar/kuwait/...'.
    """
    try:
        path = urlsplit(page_url or "").path
    except ValueError:
        return ""
    for seg in [seg for seg in path.lower().split("/") if seg][:2]:
        parts = [p for p in re.split(r"[-_]", seg) if p]
        if not parts or len(parts) > 2 or not all(p in _UAE_MARKETS | _FOREIGN_MARKETS | _LOCALE_WORDS
                                                  for p in parts):
            return ""     # a product slug, not a country section
        if not _FOREIGN_MARKETS.isdisjoint(parts):
            return "foreign"
        if not _UAE_MARKETS.isdisjoint(parts):
            return "uae"
    return ""


def _foreign_store(spec: SkuSpec, cand: Candidate) -> bool:
    """True when the page is a store outside the UAE (the pack may differ from the UAE one)."""
    host = page_host(cand) or url_host(cand.image_url)
    if not host or domain_matches(host, spec.official_domains):
        return False
    data = trusted_domains()
    if domain_matches(host, data.get("uae_retailers", [])):
        return _path_market(cand.page_url) == "foreign"
    if host.endswith(".ae") or domain_matches(host, _UAE_DOTCOM_STORES):
        return False
    if host.rsplit(".", 1)[-1] in _FOREIGN_TLDS:
        return True
    return domain_matches(host, data.get("other_retail", []))


def review_warnings(spec: SkuSpec, rc: RankedCandidate) -> List[str]:
    """Warning codes for a pre-checked candidate: what the reviewer should double-check first."""
    cand = rc.candidate
    out = _sheet_silent(spec, rc)
    if _decision_of(rc) != MATCH:
        out.append("vlm_unsure")
    if rc.fetched is not None and rc.fetched.ok and quality_mod.low_resolution(rc.fetched.width, rc.fetched.height):
        out.append("low_resolution")
    if _chat_or_screenshot(cand.image_url):
        out.append("chat_or_screenshot")
    if _social_host(url_host(cand.image_url)) or _social_host(page_host(cand)):
        out.append("social_media")
    if _foreign_store(spec, cand):
        out.append("foreign_store")
    return out


def warning_codes(reasons: Iterable[str]) -> List[str]:
    """The warning codes (without 'warn:') among a candidate's reasons."""
    return [str(r)[len(WARN_PREFIX):] for r in reasons or () if str(r).startswith(WARN_PREFIX)]


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
            if rc.verdict.decision == MISMATCH and rc.status != "rejected":
                rc.status = "rejected"
                _add(reject_counts, "vlm:MISMATCH")

    outcome = SearchOutcome(decision="REVIEW_UNSELECTED", ranked=ranked, provider_health=health_list,
                            vlm_calls=vlm_calls, sku_key=spec.sku_key, reject_counts=reject_counts)

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
    if attempted and not any(rc.fetched.ok for rc in attempted):
        outcome.decision, outcome.failure_code = "REVIEW_UNSELECTED", "DOWNLOAD_FAILED"
        logger.info("route %s: every download failed", spec.sku_key)
        return outcome

    def usable(rc: RankedCandidate) -> bool:
        return (rc.status == "eligible" and rc.fetched is not None and rc.fetched.ok
                and (rc.quality is None or rc.quality.hard_ok))

    verifiable = [rc for rc in survivors if usable(rc)]
    verifier_down = verify_state in ("unknown", "not_run")
    if verifiable and verifier_down:
        outcome.failure_code = "VERIFIER_DOWN"

    winner = next((rc for rc in verifiable if rc.score.tier in (1, 2) and _decision_of(rc) == MATCH), None)
    why = "vlm_match"
    if winner is None and verify_state == "partial":
        outcome.failure_code = "VERIFIER_DOWN"
    if winner is None:
        # Tier 1 without a MATCH: UNSURE is a reading the verifier made; UNKNOWN is accepted
        # only while the verifier is down for the whole SKU. With the verifier up, UNKNOWN
        # means it never saw the image (a skipped image or a failed second call).
        fallback = (UNSURE, UNKNOWN) if verifier_down else (UNSURE,)
        winner = next((rc for rc in verifiable if rc.score.tier == 1 and _decision_of(rc) in fallback), None)
        if winner is not None and brand_refuted(ranked):
            winner.reasons.append("vlm:tier1_brand_refuted")
            _add(reject_counts, "vlm:tier1_brand_refuted")
            winner = None
        why = f"tier1_{_decision_of(winner).lower()}" if winner is not None else ""
    if winner is None:
        outcome.decision = "REVIEW_UNSELECTED"
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
    if winner.candidate.query_id and winner.candidate.query_id in relaxed_ids:
        blockers.append("relaxed_query")
    if cache_hit:
        blockers.append("cache_hit")
    if outcome.failure_code:
        blockers.append(outcome.failure_code.lower())
    elif verify_state == "partial":
        blockers.append("verifier_partial")      # the conflict check below could not see every image
    for other in ranked:
        if other is winner or _identity_rejected(other) or other.status == "excluded":
            continue
        if _decision_of(other) != MATCH:
            continue
        clash = identity_conflict(winner, other, spec)
        if clash:
            blockers.append(f"conflicting_match:{clash}")
            break

    if blockers:
        winner.reasons.extend(f"auto_blocked:{b}" for b in blockers)
    else:
        outcome.decision = "AUTO_PUBLISH"
        winner.reasons.append("auto_publish")
    warnings = review_warnings(spec, winner)
    winner.reasons.extend(WARN_PREFIX + w for w in warnings)
    logger.info("route %s: %s winner=%s warnings=%s", spec.sku_key, outcome.decision, winner.candidate.image_url,
                ",".join(warnings) or "-")
    return outcome
