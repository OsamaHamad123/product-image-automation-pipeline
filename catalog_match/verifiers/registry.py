"""Model ids for the label readers: "<provider>:<model>", provider 'gemini' or 'claude'.

SUPPORTED_MODELS is the list the dashboard offers (SettingsController::VERIFIER_MODELS mirrors it;
tests/catalog_match/test_cm_verifiers.py keeps the two equal). parse_model_id() also accepts other
well-formed model names of the two providers so an .env can try a new model; such a model is priced
conservatively (pricing.UNKNOWN_PRICES) until MODEL_PRICES names it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, Optional

PROVIDERS = ("gemini", "claude")
OFF = "off"

# id -> label shown in the dashboard and the health page
SUPPORTED_MODELS: Dict[str, str] = {
    "gemini:gemini-3.1-flash-lite": "Gemini 3.1 Flash-Lite",
    "gemini:gemini-3.5-flash": "Gemini 3.5 Flash",
    "claude:claude-haiku-4-5": "Claude Haiku 4.5",
    "claude:claude-sonnet-5-5": "Claude Sonnet 5.5",
    "claude:claude-opus-5-5": "Claude Opus 5.5",
}

_MODEL_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,95}$")


@dataclass(frozen=True)
class ModelRef:
    provider: str      # 'gemini' | 'claude'
    model: str         # e.g. 'gemini-3.5-flash', 'claude-sonnet-5-5'

    @property
    def id(self) -> str:
        return f"{self.provider}:{self.model}"

    @property
    def label(self) -> str:
        return SUPPORTED_MODELS.get(self.id, self.id)

    @property
    def supported(self) -> bool:
        return self.id in SUPPORTED_MODELS


def parse_model_id(text: object) -> Optional[ModelRef]:
    """ModelRef for 'gemini:<model>' / 'claude:<model>', else None ('off', '', typos, other providers)."""
    if not isinstance(text, str):
        return None
    value = text.strip().lower()
    if ":" not in value:
        return None
    provider, model = (part.strip() for part in value.split(":", 1))
    if provider == "anthropic":
        provider = "claude"
    if provider == "gemini" and model.startswith("models/"):
        model = model[len("models/"):]
    if provider not in PROVIDERS or not _MODEL_RE.match(model):
        return None
    return ModelRef(provider, model)


def is_off(text: object) -> bool:
    return isinstance(text, str) and text.strip().lower() in (OFF, "none", "false", "0", "disabled")
