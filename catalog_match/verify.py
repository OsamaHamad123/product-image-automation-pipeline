"""Fail-closed visual verification with ONE Gemini client (decision D6).

GeminiVerifier.verify(spec, images) -> VerificationResult
    * one comparative generateContent call on up to 4 fetched images, each resized
      to a 1024 px long side, JPEG q85, labelled 'Image 1' .. 'Image 4';
    * the key travels in the x-goog-api-key header, never in the URL;
    * generationConfig.responseSchema asks, per image, for what is PRINTED
      (brand_text, variant_text, size_text, pack_count), the view, and
      brand/variant/size match in {yes, no, unsure}, plus best_index;
    * timeout 25 s, one retry with backoff on 429 or 5xx.

The CODE decides, never the model:
    MATCH     brand_match == 'yes', view == 'front_packshot', variant_match != 'no',
              size_match != 'no', and size_text (parsed with catalog_match.sizes)
              agrees with spec.size when both are known
    MISMATCH  any 'no', or a parsed size_text that conflicts with spec.size
    UNSURE    anything else
Every failure path (no key, transport error, non-200 after the retry, a response
that is not the schema, a safety block) returns status 'unknown' with every
verdict UNKNOWN. UNKNOWN is never accepted downstream.

A module-level CircuitBreaker opens after 5 consecutive unknown results and is
reset by any ok result. While it is open verify() makes no HTTP call. After a
cool-down one trial call is let through (half-open), so a long-running worker
recovers when the service comes back.

check_model_available() calls models.get, for worker start-up and settings validation.

Shared with the other label readers (catalog_match.verifiers): build_prompt (focus=True adds the strong
model's FOCUS_LINES for its one-image second look), parse_readings (reply -> verdicts, fail-closed on a
foreign numbering), make_verdict / classify and the CircuitBreaker. Every answered Gemini call carries one
VerificationResult.usage entry (tokens from usageMetadata, or estimated from the image count).
"""

from __future__ import annotations

import base64
import io
import json
import logging
import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

import requests
from PIL import Image

from . import settings
from .fetch import load_image
from .gtin import gtin13
from .models import FetchedImage, SkuSpec, VerificationResult, VlmImageVerdict
from . import variants as variants_mod
from .sizes import compare, compare_pack, parse_sizes
from .text_norm import any_brand_in

logger = logging.getLogger(__name__)

API_ROOT = "https://generativelanguage.googleapis.com/v1beta/models"
MAX_IMAGES = 4
LONG_SIDE = 1024
JPEG_QUALITY = 85
TIMEOUT_S = 25.0
BACKOFF_S = 2.0
RETRY_AFTER_CAP_S = 10.0
BREAKER_THRESHOLD = 5
BREAKER_COOLDOWN_S = 300.0

VIEWS = ("front_packshot", "other_side", "lifestyle", "multi_product", "banner", "not_product")
MATCH_VALUES = ("yes", "no", "unsure")

MATCH, MISMATCH, UNSURE, UNKNOWN = "MATCH", "MISMATCH", "UNSURE", "UNKNOWN"

_ENUM_MATCH = {"type": "STRING", "enum": list(MATCH_VALUES)}
_IMAGE_FIELDS = ("image_index", "brand_text", "variant_text", "size_text", "pack_count", "view",
                 "brand_match", "variant_match", "size_match")
IMAGE_SCHEMA: Dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "image_index": {"type": "INTEGER"},
        "brand_text": {"type": "STRING"},
        "variant_text": {"type": "STRING"},
        "size_text": {"type": "STRING"},
        "pack_count": {"type": "INTEGER", "nullable": True},
        "view": {"type": "STRING", "enum": list(VIEWS)},
        "brand_match": dict(_ENUM_MATCH),
        "variant_match": dict(_ENUM_MATCH),
        "size_match": dict(_ENUM_MATCH),
    },
    "required": [f for f in _IMAGE_FIELDS if f != "pack_count"],
    "propertyOrdering": list(_IMAGE_FIELDS),
}
RESPONSE_SCHEMA: Dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "images": {"type": "ARRAY", "items": IMAGE_SCHEMA},
        "best_index": {"type": "INTEGER", "nullable": True},
    },
    "required": ["images", "best_index"],
    "propertyOrdering": ["images", "best_index"],
}

_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)
_BLOCKING_FINISH = {"SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII",
                    "IMAGE_SAFETY", "LANGUAGE", "OTHER", "MALFORMED_FUNCTION_CALL"}


# ---------------------------------------------------------------------------
# Circuit breaker
# ---------------------------------------------------------------------------

class CircuitBreaker:
    """Opens after `threshold` consecutive unknown results; any ok result resets it."""

    def __init__(self, threshold: int = BREAKER_THRESHOLD, cooldown_s: float = BREAKER_COOLDOWN_S,
                 clock: Callable[[], float] = time.monotonic):
        self.threshold = threshold
        self.cooldown_s = cooldown_s
        self.clock = clock
        self._lock = threading.Lock()
        self._consecutive = 0
        self._opened_at: Optional[float] = None

    @property
    def consecutive_unknown(self) -> int:
        return self._consecutive

    def is_open(self) -> bool:
        """True while calls must be skipped. False when closed or half-open (cool-down over)."""
        with self._lock:
            if self._opened_at is None:
                return False
            return (self.clock() - self._opened_at) < self.cooldown_s

    def record(self, ok: bool) -> None:
        with self._lock:
            if ok:
                if self._opened_at is not None:
                    logger.info("verify: circuit breaker closed after an ok result")
                self._consecutive = 0
                self._opened_at = None
                return
            self._consecutive += 1
            if self._consecutive >= self.threshold:
                if self._opened_at is None:
                    logger.warning("verify: circuit breaker OPEN after %d consecutive unknown results; "
                                   "everything goes to human review", self._consecutive)
                self._opened_at = self.clock()   # (re)start the cool-down

    def reset(self) -> None:
        with self._lock:
            self._consecutive = 0
            self._opened_at = None


BREAKER = CircuitBreaker()


def is_open() -> bool:
    """Whether the module-level breaker is currently open."""
    return BREAKER.is_open()


# ---------------------------------------------------------------------------
# Code-side decision
# ---------------------------------------------------------------------------

def _text(value: Any, limit: int = 200) -> str:
    return value.strip()[:limit] if isinstance(value, str) else ""


def _choice(value: Any, allowed: Sequence[str], default: str) -> str:
    """Only exact enum strings count; anything else (bools, prose) falls back to `default`."""
    if isinstance(value, str) and value.strip().lower() in allowed:
        return value.strip().lower()
    return default


def _count(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and 1 <= value <= 1000:
        return value
    if isinstance(value, float) and value.is_integer() and 1 <= value <= 1000:
        return int(value)
    return None


def size_agreement(spec: SkuSpec, size_text: str) -> str:
    """'match' | 'conflict' | 'ambiguous' | 'other_dimension' | 'unknown' for a VLM size reading."""
    if spec.size is None or not size_text:
        return "unknown"
    found = parse_sizes(size_text, "vlm")
    if not found:
        return "unknown"
    state = compare(spec.size, found)
    if state == "unknown":
        # Something parsed, but not in the SKU's dimension (e.g. '1 L' for a 500 g SKU).
        return "other_dimension"
    if state != "match":
        return state
    if spec.size.dimension != "count" and compare_pack(spec.pack_count, found, spec.size.pieces) == "conflict":
        return "conflict"
    return "match"


REJECT_VIEWS = ("banner", "not_product", "multi_product")


def _brand_reading(spec: SkuSpec, brand_text: str) -> str:
    """'target' | 'other' | 'unknown' for the verbatim brand the model read."""
    if not brand_text:
        return "unknown"
    target = any_brand_in(spec.match_brands, brand_text) if spec.match_brands else None
    if spec.required_brands:
        if any_brand_in(spec.required_brands, brand_text):
            return "target"
        if spec.sibling_brands and any_brand_in(spec.sibling_brands, brand_text):
            return "other"            # 'Nestle Everyday' read for a Nido SKU
        return "unknown"              # parent brand only: the sub-brand is not confirmed
    if target:
        return "target"
    if spec.competitors and any_brand_in(spec.competitors, brand_text):
        return "other"
    return "unknown"


def classify(spec: SkuSpec, verdict: VlmImageVerdict) -> str:
    """MATCH / MISMATCH / UNSURE decided from the model's VERBATIM readings (decision D6).

    The yes/no/unsure flags can only make the result worse, never better:
      * any 'no', a printed size / pack that conflicts, a printed brand that is another
        brand (or a sibling sub-brand), a printed variant that conflicts with the SKU, or a
        banner / not_product / multi_product view  -> MISMATCH;
      * MATCH needs a front packshot, brand_match 'yes' with the brand (and the sub-brand
        the SKU names) readable in brand_text, a printed size that re-parses to the SKU
        size (and pack) when the SKU states one, and variant_match 'yes' with no
        conflicting printed variant when the SKU states variants; anything short of that
        is UNSURE (review, never auto-publish).
    """
    if "no" in (verdict.brand_match, verdict.variant_match, verdict.size_match):
        return MISMATCH
    size_state = size_agreement(spec, verdict.size_text)
    if size_state == "conflict":
        return MISMATCH
    brand_state = _brand_reading(spec, verdict.brand_text)
    if brand_state == "other":
        return MISMATCH
    # The label reading ('White Meat') seldom repeats the product type, so the SKU supplies the context.
    printed_variants = variants_mod.extract_variants(verdict.variant_text, variants_mod.spec_context(spec))
    if variants_mod.conflicts(spec.variants, printed_variants):
        return MISMATCH
    if verdict.view in REJECT_VIEWS:
        # a multipack SKU may legitimately be read as 'several products'
        multipack = (spec.pack_count or 1) > 1
        return UNSURE if (multipack and verdict.view == "multi_product") else MISMATCH
    if verdict.brand_match != "yes" or verdict.view != "front_packshot":
        return UNSURE
    if spec.match_brands and brand_state != "target":
        return UNSURE
    if variants_mod.soft_conflicts(spec.variants, printed_variants):
        return UNSURE
    if spec.variants and verdict.variant_match != "yes":
        return UNSURE
    counted = spec.size is not None and spec.size.dimension == "count"
    if spec.size is not None and size_state != "match":
        return UNSURE
    if spec.size is None and size_state not in ("match", "unknown"):
        return UNSURE
    target_pack = spec.pack_count or 1
    pieces = spec.size.pieces if spec.size is not None else None
    # 'PARATHA 5S 400GM' is one pack holding 5 pieces: a printed count of 5 is those pieces, not 5 packs.
    if (verdict.pack_count is not None and not counted and verdict.pack_count != target_pack
            and verdict.pack_count != pieces):
        return UNSURE
    if target_pack > 1 and not counted:
        # a multipack needs pack evidence: the printed count or an 'N x Q' size
        printed_pack = compare_pack(spec.pack_count, parse_sizes(verdict.size_text, "vlm"))
        if verdict.pack_count != target_pack and printed_pack != "match":
            return UNSURE
    return MATCH


def make_verdict(spec: SkuSpec, index: int, fields: Mapping[str, Any]) -> VlmImageVerdict:
    """Sanitise one per-image reading (from the API or a cassette) and let the code decide."""
    fields = fields if isinstance(fields, Mapping) else {}
    verdict = VlmImageVerdict(
        index=index,
        brand_text=_text(fields.get("brand_text")),
        variant_text=_text(fields.get("variant_text")),
        size_text=_text(fields.get("size_text"), 80),
        pack_count=_count(fields.get("pack_count")),
        view=_choice(fields.get("view"), VIEWS, ""),
        brand_match=_choice(fields.get("brand_match"), MATCH_VALUES, "unsure"),
        variant_match=_choice(fields.get("variant_match"), MATCH_VALUES, "unsure"),
        size_match=_choice(fields.get("size_match"), MATCH_VALUES, "unsure"),
    )
    verdict.decision = classify(spec, verdict)
    return verdict


# ---------------------------------------------------------------------------
# Prompt and request
# ---------------------------------------------------------------------------

def _describe_variants(spec: SkuSpec) -> str:
    if not spec.variants:
        return "none stated"
    return "; ".join(f"{axis}: {value.replace('|', ' / ')}" for axis, value in sorted(spec.variants.items()))


FOCUS_LINES = (
    "FOCUSED SECOND LOOK: a first reader could not confirm every field on this image. Study the label itself, "
    "including small print (the net weight or volume is often near the bottom edge or on a side panel, "
    "e.g. 'Net Wt. 400 g', 'NET 1 L', '6 x 330 ml'). Read the brand, the variant (flavour, fat level, sugar, "
    "form), the net weight or volume and the unit count exactly as printed. If a field is not printed or not "
    "readable, answer '' or unsure: never guess and never copy the target SKU into a reading.",
    "",
)


def build_prompt(spec: SkuSpec, n_images: int, focus: bool = False) -> str:
    """The reading prompt. focus=True (the strong model's one-image second look) adds FOCUS_LINES; the
    schema, the field rules and the code-side decision are the same."""
    brand = spec.brand_canonical or spec.brand_raw or "(unknown)"
    aliases = ", ".join(p for p in spec.match_brands if p) or "none"
    size = spec.size.canonical() if spec.size is not None else "not stated"
    pack = str(spec.pack_count) if spec.pack_count else "single unit"
    if not spec.pack_count and spec.size is not None and spec.size.pieces:
        pack += f" (the sheet also says {spec.size.pieces} pieces: pieces inside one pack, or {spec.size.pieces} packs)"
    lines = [
        "You check product photos for a UAE grocery catalogue. Compare EACH image with the target SKU "
        "and report only what is visibly printed on the pack. Never guess text you cannot read.",
        "",
        "TARGET SKU",
        f"- Name (sheet): {spec.raw_name or '(none)'}",
        f"- Arabic name: {spec.name_ar or '(none)'}",
        f"- Brand: {brand}; Arabic brand: {spec.brand_ar or '(none)'}; accepted brand/sub-brand names: {aliases}",
    ]
    if spec.discovered_brands:
        lines.append(f"- The sheet spells or abbreviates the brand differently from the stores; the stores write it "
                     f"{', '.join(spec.discovered_brands)}. That spelling on the pack is the target brand.")
    if spec.required_brands:
        lines.append(f"- Sub-brand that MUST be printed: {', '.join(spec.required_brands)}. The parent brand alone "
                     "is not enough, and another sub-brand of the same company is a DIFFERENT product "
                     f"({', '.join(spec.sibling_brands) or 'none listed'}): answer brand_match 'no' for those.")
    lines += [
        f"- Product type words: {', '.join(spec.class_tokens) or '(none)'}",
        f"- Variant: {_describe_variants(spec)}",
        f"- Net size per unit: {size}; pack: {pack}",
        f"- GTIN/barcode: {gtin13(spec.gtin) or '(none)'}",
        "",
    ]
    if focus:
        lines += list(FOCUS_LINES)
    lines += [
        f"There are {n_images} images, each preceded by its label 'Image 1'..'Image {n_images}'. "
        "Return one entry per image with image_index = the label number.",
        "- brand_text, variant_text, size_text: copy the printed words verbatim ('' when not readable). "
        "size_text is the printed net content, e.g. '180 ml', '1 L', '6 x 330 ml', '2.25 kg'.",
        "- pack_count: number of units in the pack shown, or null when unknown.",
        "- view: front_packshot = one product (or one shrink-wrapped multipack), front label facing the "
        "camera, plain background; "
        "other_side = one product seen from the back or side; lifestyle = product in a scene, hand or table; "
        "multi_product = several products or a shelf; banner = advertising graphic or promo text; "
        "not_product = no packaged product.",
        "- brand_match: yes = the target brand (or an accepted name) is printed; no = a DIFFERENT brand is "
        "printed; unsure = not readable.",
        "- variant_match: yes = the printed product type and variant (flavour, fat level, sugar, form) match; "
        "no = a different product type or variant is printed (e.g. laban vs milk, low fat vs full fat, "
        "strawberry vs mango); unsure = not readable or not stated.",
        "- size_match: yes = the printed size equals the target; no = a different size or pack is printed; "
        "unsure = not readable.",
        "- best_index: the label number of the image that best shows the target SKU, or null.",
        "Answer with JSON only.",
    ]
    return "\n".join(lines)


def encode_image(img: Image.Image, long_side: int = LONG_SIDE, quality: int = JPEG_QUALITY) -> str:
    """Base64 JPEG of the image on white, downscaled (never upscaled) to `long_side`."""
    if img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        canvas = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        img = Image.alpha_composite(canvas, rgba)
    img = img.convert("RGB")
    w, h = img.size
    if max(w, h) > long_side:
        scale = long_side / float(max(w, h))
        img = img.resize((max(1, int(round(w * scale))), max(1, int(round(h * scale)))), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _strip_fences(text: str) -> str:
    return _FENCE_RE.sub("", text or "").strip()


@dataclass
class ModelCheck:
    """Result of check_model_available(); truthy only when the model answered 200."""

    ok: bool
    status: str
    model: str
    http_status: Optional[int] = None

    def __bool__(self) -> bool:
        return self.ok


def check_model_available(api_key: Optional[str] = None, model: Optional[str] = None,
                          timeout: float = 10.0) -> ModelCheck:
    """models.get for the configured model (worker start-up and settings validation)."""
    key = (api_key if api_key is not None else settings.gemini_api_key()).strip()
    name = (model or settings.gemini_model()).strip()
    if name.startswith("models/"):
        name = name[len("models/"):]
    if not key:
        return ModelCheck(False, "no_api_key", name)
    try:
        resp = requests.get(f"{API_ROOT}/{name}", headers={"x-goog-api-key": key}, timeout=timeout)
    except requests.RequestException as exc:
        logger.warning("verify: models.get failed: %s", type(exc).__name__)
        return ModelCheck(False, "timeout" if isinstance(exc, requests.Timeout) else "connection_error", name)
    status = int(resp.status_code)
    if status == 200:
        return ModelCheck(True, "ok", name, status)
    code = {404: "model_not_found", 400: "invalid_request_or_key", 401: "invalid_key",
            403: "permission_denied", 429: "quota"}.get(status, f"http_{status}")
    logger.warning("verify: model %s is not available (%s)", name, code)
    return ModelCheck(False, code, name, status)




# ---------------------------------------------------------------------------
# Reply parsing (shared by every model adapter, catalog_match.verifiers)
# ---------------------------------------------------------------------------

def parse_readings(spec: SkuSpec, data: Any, slots: List[int], n: int
                   ) -> Tuple[Optional[List[VlmImageVerdict]], Optional[str]]:
    """(verdicts, None) for a reply in the RESPONSE_SCHEMA shape, or (None, error code).

    `slots[label-1]` is the input position of the image labelled 'Image <label>'. A 0-based,
    out-of-range or repeated label means the model's numbering is not ours: readings could
    land on the wrong image, so the whole reply fails closed. Images the reply skipped stay
    UNKNOWN.
    """
    if not isinstance(data, dict) or not isinstance(data.get("images"), list):
        return None, "schema_error"
    verdicts = [VlmImageVerdict(index=i, decision=UNKNOWN) for i in range(n)]
    seen = set()
    labelled = []
    for order, entry in enumerate(data["images"]):
        if not isinstance(entry, dict):
            continue
        label = entry.get("image_index")
        if isinstance(label, bool) or not isinstance(label, int):
            label = order + 1
        if not 1 <= label <= len(slots) or label in seen:
            return None, "schema_error:image_index"
        seen.add(label)
        labelled.append((label, entry))
    for label, entry in labelled:
        pos = slots[label - 1]
        verdicts[pos] = make_verdict(spec, pos, entry)
    if not seen:
        return None, "schema_error"
    return verdicts, None


def _int_or_none(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and value >= 0:
        return int(value)
    return None


# ---------------------------------------------------------------------------
# Verifier
# ---------------------------------------------------------------------------

class GeminiVerifier:
    """Verifier protocol implementation. Construct once per batch or per worker.

    long_side / focus / max_images let catalog_match.verifiers use the same client for the strong
    model's one-image second look (focus=True adds FOCUS_LINES to the prompt). Every answered call
    (HTTP 200) carries one usage entry {provider, model, input_tokens, output_tokens, estimated,
    images}; tokens come from usageMetadata, or are estimated from the image count when it is absent.
    """

    name = "gemini"
    provider = "gemini"

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None,
                 timeout: float = TIMEOUT_S, breaker: Optional[CircuitBreaker] = None,
                 sleep: Callable[[float], None] = time.sleep, backoff_s: float = BACKOFF_S,
                 max_images: int = MAX_IMAGES, session: Optional[requests.Session] = None,
                 long_side: int = LONG_SIDE, focus: bool = False):
        self._api_key = api_key
        self._model = model
        self.timeout = timeout
        self.breaker = breaker if breaker is not None else BREAKER
        self.sleep = sleep
        self.backoff_s = backoff_s
        self.max_images = max_images
        self.session = session
        self.long_side = long_side
        self.focus = focus

    # -- helpers -----------------------------------------------------------

    @property
    def api_key(self) -> str:
        return (self._api_key if self._api_key is not None else settings.gemini_api_key()).strip()

    @property
    def model(self) -> str:
        name = (self._model or settings.gemini_model()).strip()
        return name[len("models/"):] if name.startswith("models/") else name

    @staticmethod
    def _unknown(n: int, error: str, calls: int = 0) -> VerificationResult:
        return VerificationResult(
            status="unknown",
            verdicts=[VlmImageVerdict(index=i, decision=UNKNOWN) for i in range(n)],
            calls=calls,
            error=error,
        )

    def _fail(self, n: int, error: str, calls: int) -> VerificationResult:
        self.breaker.record(False)
        logger.warning("verify: result UNKNOWN (%s); candidates go to human review", error)
        result = self._unknown(n, error, calls)
        if error in ("http_401", "http_403"):
            result.notices.append("gemini_key_rejected")
        return result

    def _post(self, url: str, headers: dict, body: dict):
        poster = self.session.post if self.session is not None else requests.post
        return poster(url, headers=headers, json=body, timeout=self.timeout)

    def _retry_delay(self, resp, attempt: int) -> float:
        retry_after = None
        try:
            retry_after = float((getattr(resp, "headers", None) or {}).get("Retry-After"))
        except (TypeError, ValueError):
            retry_after = None
        if retry_after is not None and retry_after >= 0:
            return min(retry_after, RETRY_AFTER_CAP_S)
        return self.backoff_s * attempt

    # -- main entry ----------------------------------------------------------

    def verify(self, spec: SkuSpec, images: List[FetchedImage]) -> VerificationResult:
        images = list(images or [])
        n = len(images)
        if self.breaker.is_open():
            logger.info("verify: circuit breaker open; skipping the Gemini call")
            return self._unknown(n, "circuit_open")

        key = self.api_key
        if not key:
            return self._fail(n, "no_api_key", 0)

        # Encode up to max_images images; `slots[label-1]` is the input position.
        slots: List[int] = []
        parts: List[dict] = []
        for pos, fetched in enumerate(images):
            if len(slots) >= self.max_images:
                break
            img = load_image(fetched) if fetched is not None else None
            if img is None:
                continue
            try:
                data = encode_image(img, long_side=self.long_side)
            except Exception as exc:
                logger.warning("verify: cannot encode image %d: %s", pos, exc)
                continue
            slots.append(pos)
            parts.append({"text": f"Image {len(slots)}"})
            parts.append({"inlineData": {"mimeType": "image/jpeg", "data": data}})
        if not slots:
            # Nothing to look at is not a verifier failure: do not touch the breaker.
            return self._unknown(n, "no_images")

        prompt = build_prompt(spec, len(slots), focus=True) if self.focus else build_prompt(spec, len(slots))
        body = {
            "contents": [{"role": "user", "parts": [{"text": prompt}] + parts}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseSchema": RESPONSE_SCHEMA,
                "temperature": 0,
            },
        }
        url = f"{API_ROOT}/{self.model}:generateContent"
        headers = {"x-goog-api-key": key, "Content-Type": "application/json"}

        resp = None
        for attempt in (1, 2):
            try:
                resp = self._post(url, headers, body)
            except requests.Timeout:
                return self._fail(n, "timeout", 1)
            except requests.RequestException as exc:
                return self._fail(n, f"connection_error:{type(exc).__name__}", 1)
            except Exception as exc:  # a broken session or adapter must still fail closed
                return self._fail(n, f"error:{type(exc).__name__}", 1)
            status = int(getattr(resp, "status_code", 0) or 0)
            if status == 200:
                break
            if (status == 429 or status >= 500) and attempt == 1:
                self.sleep(self._retry_delay(resp, attempt))
                continue
            return self._fail(n, f"http_{status}", 1)

        return self._parse(spec, resp, slots, n, prompt)

    # -- response parsing ------------------------------------------------------

    def _usage(self, payload: Any, n_images: int, prompt: str) -> Dict[str, Any]:
        """Billed tokens of one answered call: usageMetadata, else an estimate from the image count."""
        meta = payload.get("usageMetadata") if isinstance(payload, dict) else None
        meta = meta if isinstance(meta, dict) else {}
        prompt_tokens = _int_or_none(meta.get("promptTokenCount"))
        output = [_int_or_none(meta.get(k)) for k in ("candidatesTokenCount", "thoughtsTokenCount")]
        entry: Dict[str, Any] = {"provider": "gemini", "model": self.model, "images": n_images}
        if prompt_tokens is not None and any(v is not None for v in output):
            entry.update(input_tokens=prompt_tokens, output_tokens=sum(v or 0 for v in output), estimated=False)
        else:
            from .verifiers.pricing import estimate_tokens
            est_in, est_out = estimate_tokens("gemini", n_images, prompt)
            entry.update(input_tokens=est_in, output_tokens=est_out, estimated=True)
        return entry

    def _parse(self, spec: SkuSpec, resp, slots: List[int], n: int, prompt: str = "") -> VerificationResult:
        try:
            payload = resp.json()
        except Exception:
            payload = None
        result = self._parse_payload(spec, payload, slots, n)
        if isinstance(payload, dict):
            # An API answer is billed whatever its content: the usage goes with ok and unknown results alike.
            result.usage.append(self._usage(payload, len(slots), prompt))
        return result

    def _parse_payload(self, spec: SkuSpec, payload: Any, slots: List[int], n: int) -> VerificationResult:
        if not isinstance(payload, dict):
            return self._fail(n, "bad_json", 1)
        feedback = payload.get("promptFeedback") or {}
        if isinstance(feedback, dict) and feedback.get("blockReason"):
            return self._fail(n, f"blocked:{feedback.get('blockReason')}", 1)
        candidates = payload.get("candidates")
        if not isinstance(candidates, list) or not candidates or not isinstance(candidates[0], dict):
            return self._fail(n, "no_candidates", 1)
        first = candidates[0]
        finish = str(first.get("finishReason") or "")
        if finish in _BLOCKING_FINISH:
            return self._fail(n, f"finish:{finish}", 1)
        try:
            parts_out = first["content"]["parts"]
            text = "".join(p.get("text", "") for p in parts_out if isinstance(p, dict))
        except (KeyError, TypeError, AttributeError):
            return self._fail(n, "no_text", 1)
        try:
            data = json.loads(_strip_fences(text))
        except (ValueError, TypeError):
            return self._fail(n, "parse_error", 1)
        verdicts, error = parse_readings(spec, data, slots, n)
        if verdicts is None:
            return self._fail(n, error or "schema_error", 1)

        self.breaker.record(True)
        logger.info("verify: %s", ", ".join(f"#{v.index}={v.decision}" for v in verdicts))
        return VerificationResult(status="ok", verdicts=verdicts, calls=1, error=None)
