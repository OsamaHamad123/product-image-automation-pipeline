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
    REVIEW_PRESELECTED  a tier 1/2 candidate with MATCH, else a tier 1 candidate with
                        UNSURE or UNKNOWN; that candidate is pre-checked ('preselected').
    REVIEW_UNSELECTED   candidates exist but none qualifies; nothing is pre-checked.
    NOT_FOUND           providers were healthy and nothing survived the hard filters:
                        failure_code NO_RESULTS (empty pool) or ALL_CONFLICTED.
    PROVIDER_DOWN       nothing survived and every search provider is error/quota/blocked.

failure_code on review decisions
    VERIFIER_DOWN       verification unknown (or not run) while verifiable candidates
                        exist; a tier 1 candidate is still preselected.
    DOWNLOAD_FAILED     candidates survived the identity rules but every fetch failed.

Only the winner of REVIEW_PRESELECTED / AUTO_PUBLISH has status 'preselected'. The
others are 'eligible' or 'rejected' (with reasons) or stay 'excluded' when the
pipeline removed them as reviewer negatives. A candidate whose download failed,
whose image failed a hard quality gate or whose verdict is MISMATCH is never
preselected.
"""

from __future__ import annotations

import logging
from typing import Dict, Iterable, List, Optional, Sequence, Set, Union

from . import settings
from . import variants as variants_mod
from .gtin import same_gtin
from .models import (
    ProviderHealth, ProviderResult, RankedCandidate, SearchOutcome, Size, SkuSpec,
    VerificationResult,
)
from .sizes import compare, parse_sizes, product_size
from .text_norm import normalize

logger = logging.getLogger(__name__)

LOOKUP_PROVIDERS = frozenset({"off", "open_food_facts", "openfoodfacts"})
DOWN_STATUSES = frozenset({"error", "quota", "blocked"})
MATCH, MISMATCH, UNSURE, UNKNOWN = "MATCH", "MISMATCH", "UNSURE", "UNKNOWN"

# Reason prefixes written by route(); recomputed on every call so route() is idempotent.
_ROUTE_PREFIXES = ("hard:", "download:", "quality:", "vlm:", "preselected:", "auto_blocked:", "auto_publish")


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


def identity_conflict(a: RankedCandidate, b: RankedCandidate) -> Optional[str]:
    """Why two MATCH candidates cannot show the same SKU, or None when they agree."""
    va, vb = a.verdict, b.verdict
    if va is None or vb is None:
        return None
    sa, sb = _printed_size(va.size_text), _printed_size(vb.size_text)
    if sa is not None and sb is not None and sa.dimension == sb.dimension:
        if compare(sa, [sb]) == "conflict":
            return "size"
        if (sa.pack_count or 1) != (sb.pack_count or 1):
            return "pack"
    axes = variants_mod.conflicts(variants_mod.extract_variants(va.variant_text),
                                  variants_mod.extract_variants(vb.variant_text))
    if axes:
        return f"variant:{axes[0]}"
    if va.pack_count and vb.pack_count and va.pack_count != vb.pack_count:
        return "pack"
    if same_gtin(a.candidate.gtin_on_page, b.candidate.gtin_on_page) is False:
        return "gtin"
    return None


def _add(counts: Dict[str, int], key: str) -> None:
    counts[key] = counts.get(key, 0) + 1


def _reset(rc: RankedCandidate) -> bool:
    """Drop route-owned reasons; return True when the pipeline rejected it for its own reason."""
    kept = [r for r in rc.reasons if not str(r).startswith(_ROUTE_PREFIXES)]
    pipeline_rejected = rc.status == "rejected" and len(kept) > 0
    rc.reasons[:] = kept
    return pipeline_rejected


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
    if any(r.status == "ok" for r in results):
        verify_state = "ok"
    elif results:
        verify_state = "unknown"
    else:
        verify_state = "not_run"

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
        if providers_down(health_list):
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
    if verifiable and verify_state != "ok":
        outcome.failure_code = "VERIFIER_DOWN"

    winner = next((rc for rc in verifiable if rc.score.tier in (1, 2) and _decision_of(rc) == MATCH), None)
    why = "vlm_match"
    if winner is None:
        winner = next((rc for rc in verifiable
                       if rc.score.tier == 1 and _decision_of(rc) in (UNSURE, UNKNOWN)), None)
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
    for other in ranked:
        if other is winner or _identity_rejected(other) or other.status == "excluded":
            continue
        if _decision_of(other) != MATCH:
            continue
        clash = identity_conflict(winner, other)
        if clash:
            blockers.append(f"conflicting_match:{clash}")
            break

    if blockers:
        winner.reasons.extend(f"auto_blocked:{b}" for b in blockers)
    else:
        outcome.decision = "AUTO_PUBLISH"
        winner.reasons.append("auto_publish")
    logger.info("route %s: %s winner=%s", spec.sku_key, outcome.decision, winner.candidate.image_url)
    return outcome
