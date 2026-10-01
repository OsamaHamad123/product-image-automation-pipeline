"""Adapter between the legacy search entry point and the v2 pipeline.

to_sku_row(product_name, brand, kwargs) -> dict
    The sheet row that identity.build_sku_spec() expects, from the arguments of
    image_search.search_best_product_image(query, product_name, brand, **kwargs).

outcome_to_legacy(outcome, trace=None) -> dict | None
    The dict main.py / cli_bridge.py / fastapi_server.py consume:
        url, title, width, height, source (provider), page_url, content_sha256,
        needs_review   decision != 'AUTO_PUBLISH'
        preselect      True only for a REVIEW_PRESELECTED / AUTO_PUBLISH winner
        clip_score     always None (no local similarity model exists; never fabricated)
        decision, failure_code, sku_key
        candidates     the top 8 reviewable candidates, serialised
    url is the winner's image for AUTO_PUBLISH and REVIEW_PRESELECTED. For
    REVIEW_UNSELECTED it is None: nothing is claimed as a pick, the reviewer
    chooses from `candidates`.
    Returns None for NOT_FOUND, PROVIDER_DOWN, or when there is no reviewable candidate.

    trace (when given) always receives
        trace['outcome'] = {decision, failure_code, provider_health, queries, sku_key, ...}
    and one trace['steps'] entry, {'step_name', 'name', 'query', 'results_count',
    'candidates': [...]}, whose candidates carry url, title, page_url, domain, width,
    height, status, reasons, warnings, evidence, vlm and scores {identity_score,
    quality_score, relevance_score}, so the existing UI and save_curation_candidates
    keep working. warnings are the review warning codes of the pick (decide.route's
    'warn:' reasons without the prefix, e.g. 'foreign_store'); [] for the others.
"""

from __future__ import annotations

import dataclasses
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional

from .decide import warning_codes
from .models import RankedCandidate, SearchOutcome

logger = logging.getLogger(__name__)

LEGACY_TOP_N = 8
STEP_NAME = "catalog_match v2"
NO_RESULT_DECISIONS = frozenset({"NOT_FOUND", "PROVIDER_DOWN"})
PICK_DECISIONS = frozenset({"AUTO_PUBLISH", "REVIEW_PRESELECTED"})


# ---------------------------------------------------------------------------
# Input side
# ---------------------------------------------------------------------------

def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def to_sku_row(product_name: Optional[str], brand: Optional[str], kwargs: Optional[Mapping[str, Any]] = None
               ) -> Dict[str, str]:
    """Sheet row for identity.build_sku_spec from the legacy search arguments."""
    kw = dict(kwargs or {})
    return {
        "name": _text(product_name),
        "name_ar": _text(kw.get("product_name_ar") or kw.get("name_ar")),
        "brand": _text(brand),
        "brand_ar": _text(kw.get("brand_ar")),
        "barcode": _text(kw.get("barcode")),
        "category": _text(kw.get("category")),
        "size": _text(kw.get("size_text") or kw.get("size")),
    }


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------

def _json_safe(value: Any) -> Any:
    """Plain JSON types only (tuples, sets and dataclasses become lists / dicts)."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _json_safe(dataclasses.asdict(value))
    if isinstance(value, Mapping):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        items = [_json_safe(v) for v in value]
        return sorted(items, key=str) if isinstance(value, (set, frozenset)) else items
    return str(value)


def _identity_rejected(rc: RankedCandidate) -> bool:
    return rc.score is None or rc.score.tier is None or bool(rc.score.hard_reject)


def _reviewable(rc: RankedCandidate) -> bool:
    """Shown to a reviewer: passed the identity rules and was not excluded as a reviewer negative.

    Quality-flagged, undownloaded and VLM-rejected candidates stay visible (with status
    'rejected' and their reasons); they are never pre-checked.
    """
    return rc.status != "excluded" and not _identity_rejected(rc)


def _dims(rc: RankedCandidate):
    if rc.fetched is not None and rc.fetched.ok and rc.fetched.width and rc.fetched.height:
        return rc.fetched.width, rc.fetched.height
    return rc.candidate.width, rc.candidate.height


def evidence(rc: RankedCandidate) -> Dict[str, Any]:
    """Identity evidence chips for the review UI (GTIN, brand, size, variant, source)."""
    score = rc.score
    matched = dict(score.matched or {}) if score is not None else {}
    variants_matched = list(matched.get("variants") or [])
    variant_conflict = score is not None and any(
        str(r).startswith("variant_conflict") for r in tuple(score.hard_reject) + tuple(score.conflicts))
    if variant_conflict:
        variant_status = "conflict"
    elif variants_matched:
        variant_status = "match"
    else:
        variant_status = "unknown"
    return _json_safe({
        "tier": score.tier if score is not None else None,
        "gtin": matched.get("gtin"),
        "brand": matched.get("brand"),
        "brand_fields": matched.get("brand_fields"),
        "size": score.size_status if score is not None else "unknown",
        "pack": matched.get("pack"),
        "variants_matched": variants_matched,
        # the review UI's Variant chip reads variant_status / variants
        "variant_status": variant_status,
        "variants": variants_matched,
        "variants_found": matched.get("variants_found") or {},
        "coverage": matched.get("coverage"),
        "source_class": matched.get("source_class"),
        "page_domain": matched.get("page_domain") or rc.candidate.domain,
        "conflicts": list(score.conflicts) if score is not None else [],
        "hard_reject": list(score.hard_reject) if score is not None else [],
        "url_only_size_conflict": bool(score.url_only_size_conflict) if score is not None else False,
        "consensus_count": rc.candidate.consensus_count,
        "sanctioned": rc.candidate.sanctioned,
    })


def vlm_payload(rc: RankedCandidate) -> Optional[Dict[str, Any]]:
    if rc.verdict is None:
        return None
    return _json_safe(dataclasses.asdict(rc.verdict))


def serialise_candidate(rc: RankedCandidate) -> Dict[str, Any]:
    """One candidate in the legacy trace / curation shape plus the v2 evidence."""
    width, height = _dims(rc)
    identity_score = float(rc.score.identity_score) if rc.score is not None else 0.0
    quality_score = float(rc.quality.quality_score) if rc.quality is not None else None
    cand = rc.candidate
    return {
        "url": cand.image_url,
        "title": cand.title or cand.page_title or "",
        "page_title": cand.page_title or "",
        "page_url": cand.page_url or "",
        "domain": cand.domain or "",
        "provider": cand.provider or "",
        "query_id": cand.query_id or "",
        "width": width,
        "height": height,
        "status": rc.status,
        "reasons": [str(r) for r in rc.reasons],
        "warnings": warning_codes(rc.reasons),
        "evidence": evidence(rc),
        "vlm": vlm_payload(rc),
        "quality": _json_safe(rc.quality) if rc.quality is not None else None,
        "content_sha256": rc.fetched.content_sha256 if rc.fetched is not None else None,
        "phash": rc.fetched.phash if rc.fetched is not None else None,
        "download_error": (rc.fetched.error if rc.fetched is not None and not rc.fetched.ok else None),
        "scores": {
            "identity_score": identity_score,
            "quality_score": quality_score,
            # the legacy UI and save_curation_candidates read relevance_score
            "relevance_score": identity_score,
        },
    }


def outcome_summary(outcome: SearchOutcome) -> Dict[str, Any]:
    return {
        # When the search ran: the health panel (ops_health) windows on this, not on the queue row's updated_at.
        "searched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "decision": outcome.decision,
        "failure_code": outcome.failure_code,
        "provider_health": [_json_safe(h) for h in outcome.provider_health],
        "queries": list(outcome.queries),
        "sku_key": outcome.sku_key,
        "vlm_calls": outcome.vlm_calls,
        # verifier package: per-model tokens and estimated USD of each verifier call, and its notices
        "vlm_usage": _json_safe(list(getattr(outcome, "vlm_usage", None) or [])),
        "verifier_notices": list(getattr(outcome, "verifier_notices", None) or []),
        "reject_counts": dict(outcome.reject_counts),
        "winner_url": outcome.winner.candidate.image_url if outcome.winner is not None else None,
    }


# ---------------------------------------------------------------------------
# Output side
# ---------------------------------------------------------------------------

def outcome_to_legacy(outcome: SearchOutcome, trace: Optional[dict] = None) -> Optional[Dict[str, Any]]:
    """Legacy result dict for a SearchOutcome (see the module docstring)."""
    ranked = list(outcome.ranked or [])
    if trace is not None:
        trace["outcome"] = outcome_summary(outcome)
        trace.setdefault("steps", []).append({
            "step_name": STEP_NAME,
            "name": STEP_NAME,
            "query": " | ".join(outcome.queries),
            "results_count": len(ranked),
            "candidates": [serialise_candidate(rc) for rc in ranked],
        })

    if outcome.decision in NO_RESULT_DECISIONS:
        logger.info("search %s: %s (%s)", outcome.sku_key, outcome.decision, outcome.failure_code)
        return None
    reviewable = [rc for rc in ranked if _reviewable(rc)]
    if not reviewable:
        logger.info("search %s: %s with no reviewable candidate", outcome.sku_key, outcome.decision)
        return None

    winner = outcome.winner if outcome.decision in PICK_DECISIONS and outcome.winner is not None else None
    if winner is not None and winner.status != "preselected":
        logger.error("search %s: winner without status 'preselected'; not returned as a pick", outcome.sku_key)
        winner = None
    top = reviewable[:LEGACY_TOP_N]
    if winner is not None and winner not in top:
        top = [winner] + top[:LEGACY_TOP_N - 1]

    result: Dict[str, Any] = {
        "url": None,
        "title": "",
        "width": None,
        "height": None,
        "source": None,
        "page_url": "",
        "content_sha256": None,
        "needs_review": outcome.decision != "AUTO_PUBLISH",
        "preselect": winner is not None,
        "unverified": winner is None or winner.verdict is None or winner.verdict.decision != "MATCH",
        "clip_score": None,
        "decision": outcome.decision if winner is not None or outcome.decision not in PICK_DECISIONS
        else "REVIEW_UNSELECTED",
        "failure_code": outcome.failure_code,
        "sku_key": outcome.sku_key,
        "status": winner.status if winner is not None else "review_unselected",
        "candidates": [serialise_candidate(rc) for rc in top],
    }
    if winner is not None:
        width, height = _dims(winner)
        result.update(
            url=winner.candidate.image_url,
            title=winner.candidate.title or winner.candidate.page_title or "",
            width=width,
            height=height,
            source=winner.candidate.provider or None,
            page_url=winner.candidate.page_url or "",
            content_sha256=winner.fetched.content_sha256 if winner.fetched is not None else None,
        )
    result["needs_review"] = result["decision"] != "AUTO_PUBLISH"
    return result
