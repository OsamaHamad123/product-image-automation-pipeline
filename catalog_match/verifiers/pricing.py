"""Prices and token estimates for the label readers (USD per 1M tokens, input / output).

DEFAULT_PRICES are estimates the owner can edit on the settings page (system_settings.model_prices,
setting MODEL_PRICES = JSON {model id: {"input": x, "output": y}}). Claude list prices come from the
Claude API reference (Opus 5.5 $4/$20, Sonnet 5.5 $2/$10, Haiku 4.5 $1/$5); the Gemini figures are
estimates. A model with no known price is charged UNKNOWN_PRICES (high on purpose), so a budget can
never be overrun because a price was missing.

Token estimates are used only before a call (the budget check) and when an answer carries no usage.
SettingsController::VERIFIER_ESTIMATE mirrors the constants below for the «cost per 100 products» column;
tests/catalog_match/test_cm_verifiers.py runs both and compares.
"""

from __future__ import annotations

import json
import logging
import math
from typing import Any, Dict, Mapping, Optional, Tuple

from .registry import ModelRef, parse_model_id

logger = logging.getLogger(__name__)

DEFAULT_PRICES: Dict[str, Dict[str, float]] = {
    "gemini:gemini-3.1-flash-lite": {"input": 0.25, "output": 1.50},
    "gemini:gemini-3.5-flash": {"input": 0.50, "output": 3.00},
    "claude:claude-haiku-4-5": {"input": 1.00, "output": 5.00},
    "claude:claude-sonnet-5-5": {"input": 2.00, "output": 10.00},
    "claude:claude-opus-5-5": {"input": 4.00, "output": 20.00},
}
UNKNOWN_PRICES: Dict[str, Dict[str, float]] = {
    "gemini": {"input": 2.50, "output": 15.00},
    "claude": {"input": 10.00, "output": 50.00},
}
MAX_PRICE = 1000.0

# --- token estimates (mirrored in SettingsController::VERIFIER_ESTIMATE) ---
PRIMARY_IMAGES = 4              # the pipeline's verify batch
PRIMARY_LONG_SIDE = 1024        # verify.LONG_SIDE
STRONG_LONG_SIDE = 1568         # the strong model's one-image second look
PROMPT_TOKENS = 800             # build_prompt() with the SKU block (focus adds ~120)
GEMINI_IMAGE_TOKENS = 1120      # one image at Gemini's default media resolution
OUTPUT_PER_IMAGE = 120          # one JSON reading
OUTPUT_BASE = {"gemini": 400, "claude": 600, "claude-haiku": 100}   # thinking + wrapper


def claude_image_tokens(long_side: int, width: Optional[int] = None, height: Optional[int] = None) -> int:
    """Claude bills an image at about width*height/750 tokens, after our downscale to long_side."""
    w, h = width or long_side, height or long_side
    scale = min(1.0, float(long_side) / float(max(w, h, 1)))
    return int(math.ceil((w * scale) * (h * scale) / 750.0))


def _output_base(provider: str, model: str) -> int:
    if provider == "claude" and "haiku" in model:
        return OUTPUT_BASE["claude-haiku"]
    return OUTPUT_BASE.get(provider, OUTPUT_BASE["claude"])


def estimate_tokens(provider: str, n_images: int, prompt: str = "", long_side: int = PRIMARY_LONG_SIDE,
                    model: str = "", dims: Optional[list] = None) -> Tuple[int, int]:
    """(input, output) tokens of one call on n_images images. dims: [(w, h), ...] when known."""
    n = max(0, int(n_images))
    prompt_tokens = max(PROMPT_TOKENS, int(len(prompt or "") / 3.2)) if prompt else PROMPT_TOKENS
    if provider == "claude":
        sizes = list(dims or [])[:n] + [(None, None)] * max(0, n - len(dims or []))
        image_tokens = sum(claude_image_tokens(long_side, w, h) for w, h in sizes)
    else:
        image_tokens = n * GEMINI_IMAGE_TOKENS
    return prompt_tokens + image_tokens, _output_base(provider, model) + OUTPUT_PER_IMAGE * n


# ---------------------------------------------------------------------------
# Prices
# ---------------------------------------------------------------------------

def _price(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number < 0 or number > MAX_PRICE:
        return None
    return number


def load_prices(text: Any = None) -> Dict[str, Dict[str, float]]:
    """DEFAULT_PRICES updated with the valid entries of MODEL_PRICES (JSON text or a mapping).

    Invalid JSON or entries are ignored (logged without their content), never fatal.
    """
    prices = {k: dict(v) for k, v in DEFAULT_PRICES.items()}
    if text is None:
        from .. import settings
        text = settings.model_prices_text()
    if isinstance(text, (bytes, bytearray)):
        text = text.decode("utf-8", "replace")
    if isinstance(text, str):
        if not text.strip():
            return prices
        try:
            data = json.loads(text)
        except ValueError:
            logger.warning("verifiers: MODEL_PRICES is not valid JSON; using the built-in prices")
            return prices
    else:
        data = text
    if not isinstance(data, Mapping):
        logger.warning("verifiers: MODEL_PRICES is not an object; using the built-in prices")
        return prices
    for key, entry in data.items():
        ref = parse_model_id(key)
        if ref is None or not isinstance(entry, Mapping):
            continue
        p_in, p_out = _price(entry.get("input")), _price(entry.get("output"))
        if p_in is None or p_out is None:
            continue
        prices[ref.id] = {"input": p_in, "output": p_out}
    return prices


def price_of(ref: ModelRef, prices: Mapping[str, Mapping[str, float]]) -> Dict[str, float]:
    found = prices.get(ref.id)
    if isinstance(found, Mapping) and _price(found.get("input")) is not None and _price(found.get("output")) is not None:
        return {"input": float(found["input"]), "output": float(found["output"])}
    return dict(UNKNOWN_PRICES.get(ref.provider, UNKNOWN_PRICES["claude"]))


def cost_usd(ref: ModelRef, input_tokens: int, output_tokens: int, prices: Mapping[str, Mapping[str, float]]) -> float:
    price = price_of(ref, prices)
    return round((max(0, input_tokens) * price["input"] + max(0, output_tokens) * price["output"]) / 1e6, 6)


def usage_usd(entry: Mapping[str, Any], prices: Mapping[str, Mapping[str, float]]) -> Optional[float]:
    ref = parse_model_id(f"{entry.get('provider', '')}:{entry.get('model', '')}")
    if ref is None:
        return None
    return cost_usd(ref, int(entry.get("input_tokens") or 0), int(entry.get("output_tokens") or 0), prices)


def estimate_call_usd(ref: ModelRef, prices: Mapping[str, Mapping[str, float]], n_images: int,
                      long_side: int, prompt: str = "", dims: Optional[list] = None) -> float:
    tokens_in, tokens_out = estimate_tokens(ref.provider, n_images, prompt, long_side, ref.model, dims)
    return cost_usd(ref, tokens_in, tokens_out, prices)


def estimate_per_100(ref: ModelRef, role: str, prices: Optional[Mapping[str, Mapping[str, float]]] = None) -> float:
    """USD for 100 products: role 'primary' = one 4-image reading each; 'strong' = one second look each
    (an upper bound: the strong model only looks when the first reading was unsure)."""
    prices = prices if prices is not None else DEFAULT_PRICES
    if role == "strong":
        return round(100 * estimate_call_usd(ref, prices, 1, STRONG_LONG_SIDE), 2)
    return round(100 * estimate_call_usd(ref, prices, PRIMARY_IMAGES, PRIMARY_LONG_SIDE), 2)
