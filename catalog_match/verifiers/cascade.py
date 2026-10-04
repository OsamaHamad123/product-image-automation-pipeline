"""CascadeVerifier: the primary model reads every batch; a stronger model takes one focused second look.

verify(spec, images) -> VerificationResult (the Verifier protocol of catalog_match.models)

1. The PRIMARY reader (VERIFIER_PRIMARY, default "gemini:<GEMINI_MODEL>") reads the batch exactly as
   verify.GeminiVerifier always did. When that call is not ok (key missing, outage, refusal, bad reply)
   its UNKNOWN result is returned unchanged: VERIFIER_DOWN keeps its meaning and nothing is accepted.
2. Otherwise, when no tier-1/2 image of the batch is already MATCH, the top-ranked tier-1/2 image whose
   primary verdict is UNSURE or UNKNOWN (images come in rank order) gets ONE second look from the STRONG
   reader (VERIFIER_STRONG) with the focused one-image prompt at a higher resolution.
3. The strong verdict replaces that image's verdict only when the strong call succeeded, and never
   overrides a primary MISMATCH (merge_verdict): the strong model can turn UNSURE into MATCH or
   MISMATCH, never MISMATCH into MATCH.
4. No second look when VERIFIER_STRONG is 'off', after VERIFIER_STRONG_MAX_CALLS looks for this product
   (default 1), or when this month's strong spend plus the estimate of the call would pass
   VERIFIER_MONTHLY_BUDGET_USD (notice 'strong_budget_exhausted'), when that budget is 0, the owner's
   "no second look" (notice 'strong_budget_zero', no alert), or when the spend cannot be read
   (notice 'strong_budget_unknown').

Every billed call is a usage entry {role, provider, model, input_tokens, output_tokens, estimated, usd,
images} on the result (outcome.vlm_usage in the trace) and a row in the month spend table. calls counts
verifier calls (primary + strong), so outcome.vlm_calls keeps meaning "verifier calls". The strong call's
reader notices are tagged 'strong:<code>' ('strong:claude_key_rejected'); the primary's stay untagged.

The pipeline only builds this default when no verifier is injected; tests and the offline evaluation
inject their own verifier and never reach it.
"""

from __future__ import annotations

import dataclasses
import logging
import threading
from collections import OrderedDict
from typing import Any, Callable, Dict, List, Mapping, Optional

from .. import settings
from ..models import FetchedImage, SkuSpec, VerificationResult, VlmImageVerdict
from ..verify import LONG_SIDE, MATCH, MAX_IMAGES, MISMATCH, UNKNOWN, UNSURE, GeminiVerifier, multipack_unit_image
from . import pricing
from .registry import ModelRef, is_off, parse_model_id
from .spend import MariaDbSpendStore, MemorySpendStore

logger = logging.getLogger(__name__)

STRONG_IMAGES = 1
SECOND_LOOK = (UNSURE, UNKNOWN)
_PRODUCT_MEMORY = 256

# Machine notices (outcome.verifier_notices); ops_health and the dashboard map them to Arabic text.
NOTICE_BUDGET = "strong_budget_exhausted"
NOTICE_BUDGET_ZERO = "strong_budget_zero"      # a budget of 0 is the owner's choice (no second look), not an overrun
NOTICE_BUDGET_UNKNOWN = "strong_budget_unknown"
NOTICE_STRONG_FAILED = "strong_failed"
# The strong call's own reader notices carry this prefix ('strong:claude_key_rejected'): a key problem of the
# second look must not read as one of the primary reader, whose untagged notices come with VERIFIER_DOWN.
STRONG_NOTICE_PREFIX = "strong:"


def merge_verdict(primary: Optional[VlmImageVerdict], strong: Optional[VlmImageVerdict],
                  index: Optional[int] = None) -> Optional[VlmImageVerdict]:
    """The verdict an image keeps after a strong second look.

    * a primary MISMATCH is final: the strong model never overrides it (into MATCH or anything else);
    * a missing or UNKNOWN strong verdict (failed call, skipped image) changes nothing;
    * otherwise the strong reading replaces the primary one, re-indexed to the image's position
      (`index`, else the primary verdict's index).
    """
    if primary is not None and primary.decision == MISMATCH:
        return primary
    if strong is None or strong.decision not in (MATCH, MISMATCH, UNSURE):
        return primary
    if index is None:
        index = primary.index if primary is not None else strong.index
    return dataclasses.replace(strong, index=index)


def needs_second_look(spec: SkuSpec, verdict: Optional[VlmImageVerdict]) -> bool:
    """UNSURE / UNKNOWN, or a reading without a size while the SKU states one (never a MISMATCH, and never one
    unit of a multipack SKU read as such: verify.multipack_unit_image, a MISMATCH before it was UNSURE; a
    second look cannot make one can the 3-can pack)."""
    if verdict is None:
        return True
    if verdict.decision == MISMATCH or multipack_unit_image(spec, verdict):
        return False
    if verdict.decision in SECOND_LOOK:
        return True
    return spec.size is not None and not (verdict.size_text or "").strip()


def _default_tier(spec: SkuSpec, fetched: FetchedImage) -> Optional[int]:
    from ..score import score_candidate
    score = score_candidate(spec, fetched.candidate)
    if score is None or score.hard_reject:
        return None
    return score.tier


class CascadeVerifier:
    """Primary reader for every batch, plus at most `max_strong_calls` strong second looks per product."""

    name = "cascade"

    def __init__(self, primary, strong=None, *, primary_ref: Optional[ModelRef] = None,
                 strong_ref: Optional[ModelRef] = None, max_strong_calls: int = 1, budget_usd: float = 5.0,
                 spend_store=None, prices: Optional[Mapping[str, Mapping[str, float]]] = None,
                 tier_of: Optional[Callable[[SkuSpec, FetchedImage], Optional[int]]] = None):
        self.primary = primary
        self.strong = strong
        self.primary_ref = primary_ref
        self.strong_ref = strong_ref
        self.max_strong_calls = max(0, int(max_strong_calls))
        self.budget_usd = max(0.0, float(budget_usd))
        self.spend_store = spend_store if spend_store is not None else MemorySpendStore()
        self.prices = dict(prices) if prices is not None else dict(pricing.DEFAULT_PRICES)
        self.tier_of = tier_of or _default_tier
        self._lock = threading.Lock()
        self._strong_used: "OrderedDict[int, list]" = OrderedDict()   # id(spec) -> [spec, looks]

    # -- per-product counter (the pipeline calls verify() up to twice per SKU) --------

    def _looks(self, spec: SkuSpec) -> int:
        with self._lock:
            item = self._strong_used.get(id(spec))
            return item[1] if item is not None and item[0] is spec else 0

    def _count_look(self, spec: SkuSpec) -> None:
        with self._lock:
            item = self._strong_used.get(id(spec))
            if item is None or item[0] is not spec:
                item = [spec, 0]          # holding spec keeps its id from being reused while remembered
                self._strong_used[id(spec)] = item
            item[1] += 1
            self._strong_used.move_to_end(id(spec))
            while len(self._strong_used) > _PRODUCT_MEMORY:
                self._strong_used.popitem(last=False)

    # -- accounting --------------------------------------------------------------

    def _account(self, result: VerificationResult, role: str, extra: Optional[Dict[str, Any]] = None) -> None:
        """Stamp role and estimated USD on the result's usage entries and count them in the month spend."""
        for entry in result.usage or []:
            if not isinstance(entry, dict):
                continue
            entry["role"] = role
            usd = pricing.usage_usd(entry, self.prices)
            entry["usd"] = usd if usd is not None else 0.0
            if extra:
                entry.update(extra)
            try:
                self.spend_store.add(entry)
            except Exception as exc:     # the spend table must never break a search
                logger.warning("verifiers: spend not recorded (%s)", type(exc).__name__)

    @staticmethod
    def _run(reader, spec: SkuSpec, images: List[FetchedImage]) -> VerificationResult:
        try:
            result = reader.verify(spec, images)
        except Exception as exc:  # fail closed, as the pipeline does
            logger.exception("verifiers: reader raised")
            result = VerificationResult(status="unknown", calls=1, error=f"verifier_error:{type(exc).__name__}",
                                        verdicts=[VlmImageVerdict(index=i) for i in range(len(images))])
        if not isinstance(result, VerificationResult):
            result = VerificationResult(status="unknown", calls=1, error="bad_verifier_result",
                                        verdicts=[VlmImageVerdict(index=i) for i in range(len(images))])
        return result

    # -- choosing the image ---------------------------------------------------------

    def _tier(self, spec: SkuSpec, fetched: FetchedImage) -> Optional[int]:
        try:
            return self.tier_of(spec, fetched)
        except Exception:
            logger.exception("verifiers: tier lookup failed")
            return None

    def pick(self, spec: SkuSpec, images: List[FetchedImage], result: VerificationResult) -> Optional[int]:
        """Position of the image that gets the second look, or None."""
        by_index = {v.index: v for v in (result.verdicts or []) if isinstance(v, VlmImageVerdict)}
        candidates = []
        for pos, fetched in enumerate(images):
            if fetched is None or not fetched.ok:
                continue
            if self._tier(spec, fetched) not in (1, 2):
                continue                      # only tier 1/2 can become the pick
            verdict = by_index.get(pos)
            if verdict is not None and verdict.decision == MATCH:
                return None                   # the batch already has its pick
            if needs_second_look(spec, verdict):
                candidates.append(pos)
        return candidates[0] if candidates else None

    # -- main entry -------------------------------------------------------------

    def verify(self, spec: SkuSpec, images: List[FetchedImage]) -> VerificationResult:
        images = list(images or [])
        result = self._run(self.primary, spec, images)
        self._account(result, "primary")
        if result.status != "ok" or self.strong is None or self.max_strong_calls <= 0:
            return result
        pos = self.pick(spec, images, result)
        if pos is None or self._looks(spec) >= self.max_strong_calls:
            return result

        notice = self._budget_notice(images[pos])
        if notice:
            logger.info("verifiers: no second look for %s (%s)", spec.sku_key, notice)
            result.notices.append(notice)
            return result

        self._count_look(spec)
        strong = self._run(self.strong, spec, [images[pos]])
        before = next((v for v in result.verdicts if v.index == pos), None)
        after = next((v for v in strong.verdicts if isinstance(v, VlmImageVerdict) and v.index == 0), None) \
            if strong.status == "ok" else None
        merged = merge_verdict(before, after, index=pos)
        applied = merged is not before
        self._account(strong, "strong", {
            "image_index": pos,
            "primary_decision": before.decision if before is not None else UNKNOWN,
            "strong_decision": after.decision if after is not None else UNKNOWN,
            "applied": applied,
        })
        strong_notices = [STRONG_NOTICE_PREFIX + str(n) for n in strong.notices]
        notices = list(result.notices) + [n for n in strong_notices if n not in result.notices]
        if strong.status != "ok" and not strong.notices and strong.error != "no_images":
            notices.append(NOTICE_STRONG_FAILED)
        verdicts = [merged if (v.index == pos and merged is not None) else v for v in result.verdicts]
        if merged is not None and not any(v.index == pos for v in result.verdicts):
            verdicts.append(merged)
        logger.info("verifiers: second look for %s image #%d: %s -> %s (%s)", spec.sku_key, pos,
                    before.decision if before is not None else UNKNOWN,
                    merged.decision if merged is not None else UNKNOWN,
                    "applied" if applied else (strong.error or "kept"))
        return VerificationResult(status="ok", verdicts=verdicts, calls=int(result.calls or 0) + int(strong.calls or 0),
                                  error=None, usage=list(result.usage) + list(strong.usage), notices=notices)

    def _budget_notice(self, fetched: FetchedImage) -> Optional[str]:
        """None when the second look fits in this month's budget, else the notice that explains why not."""
        if self.budget_usd <= 0:
            return NOTICE_BUDGET_ZERO
        try:
            spent = float(self.spend_store.role_spend("strong"))
        except Exception as exc:
            logger.warning("verifiers: strong spend unreadable (%s); no second look", type(exc).__name__)
            return NOTICE_BUDGET_UNKNOWN
        ref = self.strong_ref or parse_model_id(f"{getattr(self.strong, 'provider', '')}:"
                                                f"{getattr(self.strong, 'model', '')}")
        if ref is None:
            return NOTICE_BUDGET_UNKNOWN
        dims = [(fetched.width, fetched.height)] if fetched.width and fetched.height else None
        estimate = pricing.estimate_call_usd(ref, self.prices, STRONG_IMAGES,
                                             getattr(self.strong, "long_side", pricing.STRONG_LONG_SIDE), dims=dims)
        if spent + estimate > self.budget_usd:
            return NOTICE_BUDGET
        return None


# ---------------------------------------------------------------------------
# Building readers from settings
# ---------------------------------------------------------------------------

def build_reader(ref: ModelRef, role: str = "primary"):
    """A reader for one model id: the primary reads up to 4 images at 1024 px; the strong reader one
    image at a higher resolution with the focused prompt."""
    strong = role == "strong"
    long_side = pricing.STRONG_LONG_SIDE if strong else LONG_SIDE
    max_images = STRONG_IMAGES if strong else MAX_IMAGES
    if ref.provider == "claude":
        from .claude import ClaudeVerifier
        return ClaudeVerifier(ref.model, long_side=long_side, focus=strong, max_images=max_images)
    return GeminiVerifier(model=ref.model, long_side=long_side, focus=strong, max_images=max_images)


def primary_ref() -> ModelRef:
    text = settings.verifier_primary()
    ref = parse_model_id(text)
    if ref is None:
        fallback = parse_model_id(f"gemini:{settings.gemini_model()}") or parse_model_id(
            f"gemini:{settings.DEFAULTS['GEMINI_MODEL']}")
        logger.warning("verifiers: VERIFIER_PRIMARY is not a 'gemini:<model>' or 'claude:<model>' id; "
                       "reading with %s", fallback.id)
        return fallback
    return ref


def strong_ref() -> Optional[ModelRef]:
    text = settings.verifier_strong()
    if is_off(text):
        return None
    ref = parse_model_id(text)
    if ref is None:
        logger.warning("verifiers: VERIFIER_STRONG is not a model id or 'off'; no second look")
    return ref


def default_verifier() -> CascadeVerifier:
    """The verifier the pipeline uses when none is injected (built per search from the current settings)."""
    p_ref, s_ref = primary_ref(), strong_ref()
    max_calls = settings.verifier_strong_max_calls()
    return CascadeVerifier(
        build_reader(p_ref, "primary"),
        build_reader(s_ref, "strong") if s_ref is not None and max_calls > 0 else None,
        primary_ref=p_ref,
        strong_ref=s_ref,
        max_strong_calls=max_calls,
        budget_usd=settings.verifier_monthly_budget_usd(),
        spend_store=MariaDbSpendStore(),
        prices=pricing.load_prices(),
    )
