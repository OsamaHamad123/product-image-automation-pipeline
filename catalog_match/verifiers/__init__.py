"""Model-agnostic label readers (the verifier package).

A reader is (provider, model) with verify(spec, images) -> VerificationResult, exactly like
catalog_match.verify.GeminiVerifier: the same prompt (verify.build_prompt), the same code-side decision
(verify.make_verdict / classify), fail-closed (every error is UNKNOWN), a circuit breaker per provider.

    registry   model ids "gemini:<model>" / "claude:<model>" and the list the dashboard offers
    pricing    USD per 1M tokens (MODEL_PRICES over built-in estimates) and token estimates
    claude     ClaudeVerifier (official Anthropic SDK, structured output, refusal and error handling)
    spend      the month-scoped spend counter in MariaDB (verifier_spend)
    cascade    CascadeVerifier (primary for every batch + one strong second look, and one strong re-judge of a
               MISMATCH that rests only on a variant / size 'no') and default_verifier()

pipeline._default_verifier() returns default_verifier() when no verifier is injected.
"""

from .cascade import (CascadeVerifier, build_reader, default_verifier, merge_verdict, needs_rejudge,
                      needs_second_look)
from .registry import SUPPORTED_MODELS, ModelRef, is_off, parse_model_id

__all__ = [
    "CascadeVerifier", "ModelRef", "SUPPORTED_MODELS", "build_reader", "default_verifier", "is_off",
    "merge_verdict", "needs_rejudge", "needs_second_look", "parse_model_id",
]
