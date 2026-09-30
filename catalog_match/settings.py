"""Single accessor for catalog_match settings.

Values are read at call time as getattr(config, NAME, os.getenv(NAME, default)),
so settings that config.load_db_config() pulls from the dashboard's
system_settings table win over .env, and tests can monkeypatch either.
Importing config is optional: tests run without it or without a database.
"""

from __future__ import annotations

import os
from typing import Any, List

try:  # config connects to MariaDB at import; never let that break catalog_match
    import config as _config  # type: ignore
except Exception:  # pragma: no cover - depends on the environment
    _config = None


DEFAULTS = {
    "SEARCH_ENGINE": "v2",
    "SERPER_API_KEY": "",
    "GEMINI_API_KEY": "",
    "GEMINI_MODEL": "gemini-3.1-flash-lite",
    "ENABLE_BING_HTML_FALLBACK": True,
    "GOOGLE_SEARCH_API_KEYS": [],
    "GOOGLE_SEARCH_CX_LIST": [],
    "CSE_SUNSET_DATE": "2026-12-31",
    "AUTO_PUBLISH_ENABLED": False,
    "AUTO_PUBLISH_BRANDS": [],
    "CANDIDATE_STORE_DIR": os.path.join("temp", "candidates"),
    "PROXY_URL": "",
    "OUTPUT_CANVAS_SIZE": 800,
}

_TRUE = {"1", "true", "yes", "on"}


def _raw(name: str) -> Any:
    default = DEFAULTS.get(name)
    if _config is not None and hasattr(_config, name):
        return getattr(_config, name)
    env = os.getenv(name)
    return default if env is None else env


def get(name: str) -> Any:
    """Return a setting coerced to the type of its default."""
    value = _raw(name)
    default = DEFAULTS.get(name)
    if isinstance(default, bool):
        return value if isinstance(value, bool) else str(value).strip().lower() in _TRUE
    if isinstance(default, int) and not isinstance(default, bool):
        try:
            return int(value)
        except (TypeError, ValueError):
            return default
    if isinstance(default, list):
        return as_list(value)
    return "" if value is None else value


def as_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(v).strip() for v in value if str(v).strip()]
    return [v.strip() for v in str(value).split(",") if v.strip()]


def search_engine() -> str:
    return str(get("SEARCH_ENGINE")).strip().lower() or "v2"


def serper_api_key() -> str:
    return str(get("SERPER_API_KEY")).strip()


def gemini_api_key() -> str:
    return str(get("GEMINI_API_KEY")).strip()


def gemini_model() -> str:
    return str(get("GEMINI_MODEL")).strip() or DEFAULTS["GEMINI_MODEL"]


def auto_publish_enabled() -> bool:
    return bool(get("AUTO_PUBLISH_ENABLED"))


def auto_publish_brands() -> List[str]:
    return get("AUTO_PUBLISH_BRANDS")


def candidate_store_dir() -> str:
    return str(get("CANDIDATE_STORE_DIR"))


def proxy_url() -> str:
    return str(get("PROXY_URL") or "")


def output_canvas_size() -> int:
    size = get("OUTPUT_CANVAS_SIZE")
    return size if size and size > 0 else DEFAULTS["OUTPUT_CANVAS_SIZE"]
