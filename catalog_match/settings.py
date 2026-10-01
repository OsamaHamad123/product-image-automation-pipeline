"""Single accessor for catalog_match settings.

Values are read at call time as getattr(config, NAME, os.getenv(NAME, default)),
so settings that config.load_db_config() pulls from the dashboard's
system_settings table win over .env, and tests can monkeypatch either.
Importing config is optional: tests run without it or without a database.
"""

from __future__ import annotations

import datetime as _dt
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

# --- identity package (P3): how far a barcode is trusted ----------------------------------------
# GTIN_POLICY
#   'evidence' (default)  brand + product name (+ size / variant) is the identity; the barcode only
#                         supports it. A page GTIN that matches the sheet makes tier 1 only with full
#                         brand evidence; one that differs caps the candidate at tier 2 with the
#                         'barcode_conflict' review warning, and is a hard reject only together with a
#                         missing or other brand, a size, pack or variant conflict.
#   'strict'              the earlier rule: any differing page GTIN is a hard reject, and a GTIN match
#                         with brand OR product-type corroboration is tier 1.
#   'off'                 the barcode is not used as evidence and gets no GTIN query (Q4).
DEFAULTS.update({
    "GTIN_POLICY": "evidence",
})
GTIN_POLICIES = ("evidence", "strict", "off")


# --- sources package (P3): expansion round, product pages, shopping and visual search ---
# EXPANSION_ENABLED    one extra search round for SKUs with no confident pick (catalog_match.expand)
# EXPANSION_MAX_CALLS  paid calls the round may make per product (web, shopping, visual search)
# VISUAL_SEARCH        'auto' (Serper lens, then SerpApi when SERPAPI_API_KEY is set) | 'off' | 'serper' | 'serpapi'
# SERPAPI_API_KEY      secret; written only through the dashboard's write-only field
# SERPAPI_LENS_PRICE_USD  what one SerpApi Google Lens search costs on the owner's plan (ops_health pricing)
DEFAULTS.update({
    "EXPANSION_ENABLED": True,
    "EXPANSION_MAX_CALLS": 4,
    "VISUAL_SEARCH": "auto",
    "SERPAPI_API_KEY": "",
    "SERPAPI_LENS_PRICE_USD": "0.015",
})
VISUAL_SEARCH_MODES = ("auto", "off", "serper", "serpapi")
# --- end sources package ---

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


def enable_bing_html_fallback() -> bool:
    return bool(get("ENABLE_BING_HTML_FALLBACK"))


def google_search_api_keys() -> List[str]:
    """Legacy CSE keys. config.py names the list GOOGLE_SEARCH_API_KEYS but .env uses GOOGLE_SEARCH_API_KEY."""
    return get("GOOGLE_SEARCH_API_KEYS") or as_list(os.getenv("GOOGLE_SEARCH_API_KEY"))


def google_search_cx_list() -> List[str]:
    return get("GOOGLE_SEARCH_CX_LIST") or as_list(os.getenv("GOOGLE_SEARCH_CX"))


def cse_sunset_date() -> _dt.date:
    """Last day the legacy Google CSE adapter may run (inclusive)."""
    value = get("CSE_SUNSET_DATE")
    if isinstance(value, _dt.datetime):
        return value.date()
    if isinstance(value, _dt.date):
        return value
    try:
        return _dt.date.fromisoformat(str(value).strip())
    except ValueError:
        return _dt.date.fromisoformat(DEFAULTS["CSE_SUNSET_DATE"])


# --- identity package (P3) ---------------------------------------------------------------------

def gtin_policy() -> str:
    """'evidence' (default), 'strict' or 'off'; an unknown value falls back to 'evidence'."""
    value = str(get("GTIN_POLICY") or "").strip().lower()
    return value if value in GTIN_POLICIES else DEFAULTS["GTIN_POLICY"]


# ---------------------------------------------------------------------------
# verifier package (P3): which models read the labels, the strong second look and its budget.
# Model ids are "<provider>:<model>" with provider 'gemini' or 'claude' (catalog_match.verifiers).
# ---------------------------------------------------------------------------

DEFAULTS.update({
    "ANTHROPIC_API_KEY": "",                        # secret: written only by the dashboard's write-only field
    "VERIFIER_PRIMARY": "",                         # '' = "gemini:<GEMINI_MODEL>"
    "VERIFIER_STRONG": "gemini:gemini-3.5-flash",   # or "claude:<model>", or "off"
    "VERIFIER_MONTHLY_BUDGET_USD": "5",             # month cap (UTC) for the strong model's estimated spend
    "VERIFIER_STRONG_MAX_CALLS": "1",               # strong calls per product
    "MODEL_PRICES": "",                             # JSON {model id: {input, output}} USD per 1M tokens; '' = built-in
})


def anthropic_api_key() -> str:
    return str(get("ANTHROPIC_API_KEY") or "").strip()


def verifier_primary() -> str:
    value = str(get("VERIFIER_PRIMARY") or "").strip()
    return value or f"gemini:{gemini_model()}"


def verifier_strong() -> str:
    value = str(get("VERIFIER_STRONG") or "").strip()
    return value or DEFAULTS["VERIFIER_STRONG"]


def _number(name: str, low: float, high: float) -> float:
    try:
        value = float(str(get(name)).strip())
    except (TypeError, ValueError):
        value = float(DEFAULTS[name])
    if value != value:   # NaN
        value = float(DEFAULTS[name])
    return min(high, max(low, value))


def verifier_monthly_budget_usd() -> float:
    return _number("VERIFIER_MONTHLY_BUDGET_USD", 0.0, 10000.0)


def verifier_strong_max_calls() -> int:
    return int(_number("VERIFIER_STRONG_MAX_CALLS", 0.0, 4.0))


def model_prices_text() -> str:
    value = get("MODEL_PRICES")
    return value if isinstance(value, str) else ("" if value is None else str(value))


# --- sources package (P3): accessors ---

def expansion_enabled() -> bool:
    return bool(get("EXPANSION_ENABLED"))


def expansion_max_calls() -> int:
    """Paid calls the expansion round may make per product (0 turns the round off)."""
    value = get("EXPANSION_MAX_CALLS")
    return max(0, int(value)) if isinstance(value, int) else int(DEFAULTS["EXPANSION_MAX_CALLS"])


def visual_search_mode() -> str:
    """'auto' | 'off' | 'serper' | 'serpapi'; an unknown value is 'off' (never a surprise paid call)."""
    mode = str(get("VISUAL_SEARCH") or "").strip().lower() or str(DEFAULTS["VISUAL_SEARCH"])
    return mode if mode in VISUAL_SEARCH_MODES else "off"


def serpapi_api_key() -> str:
    return str(get("SERPAPI_API_KEY") or "").strip()


def serpapi_lens_price_usd() -> float:
    try:
        price = float(str(get("SERPAPI_LENS_PRICE_USD")).strip())
    except (TypeError, ValueError):
        price = float(DEFAULTS["SERPAPI_LENS_PRICE_USD"])
    return price if price >= 0 else float(DEFAULTS["SERPAPI_LENS_PRICE_USD"])
# --- end sources package ---
