"""Claude label reader: the same contract as verify.GeminiVerifier, through the official Anthropic SDK.

ClaudeVerifier(model).verify(spec, images) -> VerificationResult
    * one call with up to 4 images (base64 JPEG on white, downscaled to long_side), each after a text
      label 'Image k', then the same prompt as Gemini (verify.build_prompt; focus=True for the strong
      model's one-image second look);
    * structured output: output_config.format = a JSON schema of the same fields; the reply is parsed
      by verify.parse_readings and the CODE decides (verify.make_verdict / classify), never the model;
    * claude-opus-5-5 / claude-sonnet-5-5 run thinking that cannot be switched off: effort 'low' keeps
      this short reading cheap, and they go through client.beta.messages.create with the server-side
      refusal fallback (betas=['server-side-fallback-2026-07-01'], fallbacks='default');
    * stop_reason 'refusal' (the whole fallback chain declined) is UNKNOWN for that call; so is a reply
      cut by max_tokens, a reply that is not the schema and every SDK error;
    * 401 / 403 = the key is rejected: UNKNOWN with the notice 'claude_key_rejected', no retry;
      429 / 5xx / connection errors / timeouts are retried once after a short backoff, then UNKNOWN;
    * a module-level CircuitBreaker per provider (CLAUDE_BREAKER) as for Gemini;
    * the key comes from settings.anthropic_api_key() (system_settings / .env through config.py) and is
      passed to the client explicitly with a fixed base URL; it is never logged, never put in a usage
      entry, an error code or a trace.
Every answered call carries one usage entry {provider, model, input_tokens, output_tokens, estimated, images}.
"""

from __future__ import annotations

import hmac
import json
import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from .. import cassette, settings
from ..fetch import load_image
from ..models import FetchedImage, SkuSpec, VerificationResult, VlmImageVerdict
from ..verify import (
    BACKOFF_S, LONG_SIDE, MATCH_VALUES, MAX_IMAGES, RETRY_AFTER_CAP_S, UNKNOWN, VIEWS, CircuitBreaker,
    build_prompt, encode_image, parse_readings, _strip_fences,
)

logger = logging.getLogger(__name__)

BASE_URL = "https://api.anthropic.com"
TIMEOUT_S = 60.0
FALLBACK_BETA = "server-side-fallback-2026-07-01"
# Models whose thinking cannot be disabled: effort 'low' and the server-side refusal fallback.
THINKING_MODELS = ("claude-opus-5-5", "claude-sonnet-5-5")
MAX_TOKENS = {"thinking": 6000, "plain": 2000}

CLAUDE_BREAKER = CircuitBreaker()

_ENUM = {"type": "string", "enum": list(MATCH_VALUES)}
_IMAGE_PROPERTIES = {
    "image_index": {"type": "integer"},
    "brand_text": {"type": "string"},
    "variant_text": {"type": "string"},
    "size_text": {"type": "string"},
    "pack_count": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
    "view": {"type": "string", "enum": list(VIEWS)},
    "brand_match": dict(_ENUM),
    "variant_match": dict(_ENUM),
    "size_match": dict(_ENUM),
}
# The same fields as verify.RESPONSE_SCHEMA, in JSON Schema form for output_config.format.
OUTPUT_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "images": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": _IMAGE_PROPERTIES,
                "required": list(_IMAGE_PROPERTIES),
                "additionalProperties": False,
            },
        },
        "best_index": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
    },
    "required": ["images", "best_index"],
    "additionalProperties": False,
}

_CLIENTS: Dict[str, Any] = {}
_CLIENTS_LOCK = threading.Lock()


def _sdk():
    import anthropic  # imported lazily: a missing package is an UNKNOWN result, not an import error
    return anthropic


def _client_for(key: str, timeout: float):
    """One SDK client for the current key, no SDK retries: we retry once ourselves. A changed key replaces the
    client; the key is compared in constant time and only the client (which needs it anyway) keeps it."""
    with _CLIENTS_LOCK:
        current = _CLIENTS.get("current")
        if current is not None and hmac.compare_digest(current[0].encode("utf-8"), key.encode("utf-8")):
            return current[1]
        client = _sdk().Anthropic(api_key=key, base_url=BASE_URL, max_retries=0, timeout=timeout)
        _CLIENTS.clear()
        _CLIENTS["current"] = (key, client)
        return client


def _field(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


class ClaudeVerifier:
    """Verifier protocol implementation for one Claude model."""

    name = "claude"
    provider = "claude"

    def __init__(self, model: str, api_key: Optional[str] = None, client: Any = None,
                 timeout: float = TIMEOUT_S, breaker: Optional[CircuitBreaker] = None,
                 sleep: Callable[[float], None] = time.sleep, backoff_s: float = BACKOFF_S,
                 max_images: int = MAX_IMAGES, long_side: int = LONG_SIDE, focus: bool = False):
        self.model = model
        self._api_key = api_key
        self._client = client
        self.timeout = timeout
        self.breaker = breaker if breaker is not None else CLAUDE_BREAKER
        self.sleep = sleep
        self.backoff_s = backoff_s
        self.max_images = max_images
        self.long_side = long_side
        self.focus = focus

    @property
    def api_key(self) -> str:
        return (self._api_key if self._api_key is not None else settings.anthropic_api_key()).strip()

    @property
    def thinking(self) -> bool:
        return self.model in THINKING_MODELS

    # -- results -------------------------------------------------------------

    @staticmethod
    def _unknown(n: int, error: str, calls: int = 0) -> VerificationResult:
        return VerificationResult(status="unknown", calls=calls, error=error,
                                  verdicts=[VlmImageVerdict(index=i, decision=UNKNOWN) for i in range(n)])

    def _fail(self, n: int, error: str, calls: int, notice: Optional[str] = None) -> VerificationResult:
        self.breaker.record(False)
        logger.warning("verify(claude %s): result UNKNOWN (%s); candidates go to human review", self.model, error)
        result = self._unknown(n, error, calls)
        if notice:
            result.notices.append(notice)
        return result

    # -- request -------------------------------------------------------------

    def _content(self, spec: SkuSpec, images: List[FetchedImage]):
        slots: List[int] = []
        dims: List[tuple] = []
        content: List[dict] = []
        for pos, fetched in enumerate(images):
            if len(slots) >= self.max_images:
                break
            img = load_image(fetched) if fetched is not None else None
            if img is None:
                continue
            try:
                data = encode_image(img, long_side=self.long_side)
                w, h = img.size
                scale = min(1.0, float(self.long_side) / float(max(w, h, 1)))
                dims.append((max(1, round(w * scale)), max(1, round(h * scale))))
            except Exception as exc:
                logger.warning("verify(claude): cannot encode image %d: %s", pos, type(exc).__name__)
                continue
            slots.append(pos)
            content.append({"type": "text", "text": f"Image {len(slots)}"})
            content.append({"type": "image",
                            "source": {"type": "base64", "media_type": "image/jpeg", "data": data}})
        prompt = build_prompt(spec, len(slots), focus=True) if self.focus else build_prompt(spec, len(slots))
        content.append({"type": "text", "text": prompt})
        return slots, dims, content, prompt

    def _request(self, content: List[dict]) -> Dict[str, Any]:
        output_config: Dict[str, Any] = {"format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}}
        kwargs: Dict[str, Any] = {
            "model": self.model,
            "max_tokens": MAX_TOKENS["thinking" if self.thinking else "plain"],
            "messages": [{"role": "user", "content": content}],
        }
        if self.thinking:
            output_config["effort"] = "low"
            kwargs["betas"] = [FALLBACK_BETA]
            kwargs["fallbacks"] = "default"
        kwargs["output_config"] = output_config
        return kwargs

    def _send(self, client, kwargs: Dict[str, Any]):
        if self.thinking:
            return cassette.verifier(lambda: client.beta.messages.create(**kwargs))
        return cassette.verifier(lambda: client.messages.create(**kwargs))

    def _retry_delay(self, exc: Exception, attempt: int) -> float:
        response = getattr(exc, "response", None)
        headers = getattr(response, "headers", None) or {}
        try:
            retry_after = float(headers.get("retry-after"))
        except (TypeError, ValueError, AttributeError):
            retry_after = None
        if retry_after is not None and retry_after >= 0:
            return min(retry_after, RETRY_AFTER_CAP_S)
        return self.backoff_s * attempt

    @staticmethod
    def _classify_error(sdk, exc: Exception):
        """(error code, retryable, notice) for an SDK exception. Never includes the message text."""
        if isinstance(exc, sdk.APITimeoutError):
            return "timeout", True, None
        if isinstance(exc, sdk.APIConnectionError):
            return "connection_error", True, None
        if isinstance(exc, sdk.APIStatusError):
            status = _int(getattr(exc, "status_code", 0))
            if status in (401, 403):
                return f"http_{status}", False, "claude_key_rejected"
            if status == 404:
                return "http_404", False, "claude_model_not_found"
            if status == 429 or status >= 500:
                return f"http_{status}", True, None
            return f"http_{status or 'error'}", False, None
        return f"error:{type(exc).__name__}", False, None

    # -- main entry ----------------------------------------------------------

    def verify(self, spec: SkuSpec, images: List[FetchedImage]) -> VerificationResult:
        images = list(images or [])
        n = len(images)
        if self.breaker.is_open():
            logger.info("verify(claude): circuit breaker open; skipping the call")
            return self._unknown(n, "circuit_open")
        key = self.api_key
        if not key:
            return self._fail(n, "no_api_key", 0, "claude_key_missing")
        try:
            sdk = _sdk()
        except Exception:
            return self._fail(n, "sdk_missing", 0)

        slots, dims, content, prompt = self._content(spec, images)
        if not slots:
            return self._unknown(n, "no_images")
        cassette.verifier_scope(self.provider, self.model, self.focus, self.long_side, prompt, images, slots)
        try:
            client = self._client if self._client is not None else _client_for(key, self.timeout)
        except Exception as exc:
            return self._fail(n, f"error:{type(exc).__name__}", 0)
        kwargs = self._request(content)

        response = None
        for attempt in (1, 2):
            try:
                response = self._send(client, kwargs)
                break
            except Exception as exc:  # every SDK error fails closed
                code, retryable, notice = self._classify_error(sdk, exc)
                if retryable and attempt == 1:
                    self.sleep(self._retry_delay(exc, attempt))
                    continue
                return self._fail(n, code, 1, notice)

        result = self._parse(spec, response, slots, n)
        result.usage.append(self._usage(response, len(slots), prompt, dims))
        return result

    # -- response --------------------------------------------------------------

    def _usage(self, response: Any, n_images: int, prompt: str, dims: List[tuple]) -> Dict[str, Any]:
        usage = _field(response, "usage")
        served = _field(response, "model") or self.model
        entry: Dict[str, Any] = {"provider": "claude", "model": str(served), "images": n_images}
        if str(served) != self.model:
            entry["requested_model"] = self.model        # answered by the refusal fallback
        if usage is not None and _field(usage, "input_tokens") is not None:
            tokens_in = (_int(_field(usage, "input_tokens")) + _int(_field(usage, "cache_creation_input_tokens"))
                         + _int(_field(usage, "cache_read_input_tokens")))
            entry.update(input_tokens=tokens_in, output_tokens=_int(_field(usage, "output_tokens")), estimated=False)
        else:
            from .pricing import estimate_tokens
            tokens_in, tokens_out = estimate_tokens("claude", n_images, prompt, self.long_side, self.model, dims)
            entry.update(input_tokens=tokens_in, output_tokens=tokens_out, estimated=True)
        return entry

    def _parse(self, spec: SkuSpec, response: Any, slots: List[int], n: int) -> VerificationResult:
        stop = str(_field(response, "stop_reason") or "")
        if stop == "refusal":
            return self._fail(n, "refusal", 1)
        if stop == "max_tokens":
            return self._fail(n, "max_tokens", 1)
        blocks = _field(response, "content") or []
        text = ""
        for block in blocks if isinstance(blocks, list) else []:
            if _field(block, "type") == "text":
                text = str(_field(block, "text") or "")
                break
        if not text.strip():
            return self._fail(n, "no_text", 1)
        try:
            data = json.loads(_strip_fences(text))
        except (ValueError, TypeError):
            return self._fail(n, "parse_error", 1)
        verdicts, error = parse_readings(spec, data, slots, n)
        if verdicts is None:
            return self._fail(n, error or "schema_error", 1)
        self.breaker.record(True)
        logger.info("verify(claude %s): %s", self.model, ", ".join(f"#{v.index}={v.decision}" for v in verdicts))
        return VerificationResult(status="ok", verdicts=verdicts, calls=1, error=None)
