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
    "SERPER_API_KEY": "",
    "GEMINI_API_KEY": "",
    "GEMINI_MODEL": "gemini-3.1-flash-lite",
    "ENABLE_BING_HTML_FALLBACK": True,
    "GOOGLE_SEARCH_API_KEYS": [],
    "GOOGLE_SEARCH_CX_LIST": [],
    "CSE_SUNSET_DATE": "2026-12-31",
    # the legacy Google CSE adapter joins the provider chain only when switched on here (and a key and a cx exist):
    # every live run since 2026-10-04 got HTTP 403 on every call (Custom Search JSON API closed to the project)
    "CSE_LEGACY_ENABLED": False,
    "AUTO_PUBLISH_ENABLED": False,
    "AUTO_PUBLISH_BRANDS": [],
    # a pick of lane 'strict' (catalog_match.decide.pick_lane) of a mapped brand auto-publishes whatever
    # AUTO_PUBLISH_BRANDS says, and only once that lane's own reviews prove it (decide.strict_lane_readiness 'ready');
    # on by default (the owner's approval), the value saved in the Settings page wins
    "AUTO_PUBLISH_STRICT_LANE": True,
    "CANDIDATE_STORE_DIR": os.path.join("temp", "candidates"),
    "PROXY_URL": "",
    # the canvas side is adaptive (image_processor._adaptive_canvas): round(product long side / fill) clamped to
    # [OUTPUT_CANVAS_SIZE, OUTPUT_CANVAS_MAX], so a detailed source keeps its pixels for a 3x phone screen
    # (~1170 px) and a small source gets exactly the canvas it got before. OUTPUT_CANVAS_MAX <= OUTPUT_CANVAS_SIZE
    # turns it off (a fixed canvas).
    "OUTPUT_CANVAS_SIZE": 800,
    "OUTPUT_CANVAS_MAX": 2048,
    # image_processor: when a cloud isolation method fails on credit / key / quota, 'local' isolates with rembg
    "BG_FALLBACK": "local",
    # the rembg model of that fallback: BiRefNet keeps white packaging that u2net / isnet eat
    "REMBG_MODEL": "birefnet-general",
    # 'transparent' (default): RGBA PNG master, product trimmed and centred, no shadow (the app themes it);
    # 'white': the earlier opaque white canvas. OUTPUT_PRODUCT_FILL: share of the square the product's longer
    # side fills on the transparent canvas (the white canvas keeps edge_shadow_engine.CANVAS_FILL_RATIO).
    "OUTPUT_BACKGROUND": "transparent",
    "OUTPUT_PRODUCT_FILL": "0.88",
    # PhotoRoom's x-uncertainty-score header (0 = confident, 1 = unsure): above this the cutout gets the review flag
    # 'photoroom_unsure' (image_processor); no paid retry
    "PHOTOROOM_UNCERTAINTY_MAX": "0.5",
}
REMBG_MODELS = ("birefnet-general", "birefnet-general-lite")
OUTPUT_BACKGROUNDS = ("transparent", "white")

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
# EXPANSION_SCOPE_GATE skip the round's paid steps (X1-X5; the free X0 page recovery still runs) for products they
#                      cannot help: bouquets and other out-of-scope kinds (data/expansion_gate.json), and a brand no
#                      listing names on a store or brand site, which gets one Google Shopping probe only
#                      (catalog_match.expand.scope_gate). On by default
# VISUAL_SEARCH        'auto' (Serper lens, then SerpApi when SERPAPI_API_KEY is set) | 'off' | 'serper' | 'serpapi'
# SERPAPI_API_KEY      secret; written only through the dashboard's write-only field
# SERPAPI_LENS_PRICE_USD  what one SerpApi Google Lens search costs on the owner's plan (ops_health pricing)
DEFAULTS.update({
    "EXPANSION_ENABLED": True,
    "EXPANSION_MAX_CALLS": 4,
    "EXPANSION_SCOPE_GATE": True,
    "VISUAL_SEARCH": "auto",
    "SERPAPI_API_KEY": "",
    "SERPAPI_LENS_PRICE_USD": "0.015",
})
VISUAL_SEARCH_MODES = ("auto", "off", "serper", "serpapi")
# PAGE_MAIN_IMAGES_MAX_PAGES  the normal flow reads the pages of this many tier-1/2 listings on trusted hosts (the
#                             brand's site, a UAE retailer) for their own main image, read like any candidate
#                             (catalog_match.expand P0, free); 0 turns it off, at most 3
# PAGE_MAIN_IMAGES_WAIT_S     how long the search waits for those pages after it starts reading them (they load
#                             while the listings' pictures download); a page still loading is left out
DEFAULTS.update({
    "PAGE_MAIN_IMAGES_MAX_PAGES": 2,
    "PAGE_MAIN_IMAGES_WAIT_S": "3",
})
PAGE_MAIN_IMAGES_MAX_PAGES_LIMIT = 3
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


def auto_publish_strict_lane() -> bool:
    return bool(get("AUTO_PUBLISH_STRICT_LANE"))


def candidate_store_dir() -> str:
    return str(get("CANDIDATE_STORE_DIR"))


def proxy_url() -> str:
    return str(get("PROXY_URL") or "")


def output_canvas_size() -> int:
    size = get("OUTPUT_CANVAS_SIZE")
    return size if size and size > 0 else DEFAULTS["OUTPUT_CANVAS_SIZE"]


def output_canvas_max() -> int:
    """The largest adaptive canvas side: OUTPUT_CANVAS_MAX (default 2048), at least output_canvas_size(), at most 4000."""
    value = get("OUTPUT_CANVAS_MAX")
    if not value or value <= 0:
        value = DEFAULTS["OUTPUT_CANVAS_MAX"]
    return max(output_canvas_size(), min(4000, int(value)))


def photoroom_uncertainty_max() -> float:
    """PHOTOROOM_UNCERTAINTY_MAX clamped to 0..1 (default 0.5); an unreadable value reads as the default."""
    try:
        value = float(str(get("PHOTOROOM_UNCERTAINTY_MAX")).strip())
    except (TypeError, ValueError):
        value = float(DEFAULTS["PHOTOROOM_UNCERTAINTY_MAX"])
    if value != value:  # NaN
        value = float(DEFAULTS["PHOTOROOM_UNCERTAINTY_MAX"])
    return max(0.0, min(1.0, value))


def bg_fallback() -> str:
    """'local' (default) or 'off'; anything else reads as the default."""
    value = str(get("BG_FALLBACK")).strip().lower()
    return value if value in ("local", "off") else DEFAULTS["BG_FALLBACK"]


def rembg_model() -> str:
    """'birefnet-general' (default) or 'birefnet-general-lite'; anything else (u2net, a typo) reads as the default."""
    value = str(get("REMBG_MODEL")).strip().lower()
    return value if value in REMBG_MODELS else DEFAULTS["REMBG_MODEL"]


def output_background() -> str:
    """'transparent' (default) or 'white'; an unknown value falls back to the default."""
    value = str(get("OUTPUT_BACKGROUND") or "").strip().lower()
    return value if value in OUTPUT_BACKGROUNDS else DEFAULTS["OUTPUT_BACKGROUND"]


def output_product_fill() -> float:
    """Share of the transparent square the product's longer side fills, clamped to 0.5-1.0 (default 0.88)."""
    try:
        value = float(str(get("OUTPUT_PRODUCT_FILL")).strip())
    except (TypeError, ValueError):
        value = float(DEFAULTS["OUTPUT_PRODUCT_FILL"])
    if value != value:  # NaN
        value = float(DEFAULTS["OUTPUT_PRODUCT_FILL"])
    return max(0.5, min(1.0, value))


def enable_bing_html_fallback() -> bool:
    return bool(get("ENABLE_BING_HTML_FALLBACK"))


def google_search_api_keys() -> List[str]:
    """Legacy CSE keys. config.py names the list GOOGLE_SEARCH_API_KEYS but .env uses GOOGLE_SEARCH_API_KEY."""
    return get("GOOGLE_SEARCH_API_KEYS") or as_list(os.getenv("GOOGLE_SEARCH_API_KEY"))


def google_search_cx_list() -> List[str]:
    return get("GOOGLE_SEARCH_CX_LIST") or as_list(os.getenv("GOOGLE_SEARCH_CX"))


def cse_legacy_enabled() -> bool:
    """True only when CSE_LEGACY_ENABLED is set: the legacy Google CSE adapter is out of the default chain."""
    return bool(get("CSE_LEGACY_ENABLED"))


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
    # strong re-judges of a cheap MISMATCH that rests only on a variant / size 'no' (tier 1), per product: 1 = on,
    # 0 = off, capped at 1; besides the second looks, within the same month budget, off with the strong model
    # (VERIFIER_STRONG 'off' or VERIFIER_STRONG_MAX_CALLS 0) (verifiers.cascade)
    "VERIFIER_REJUDGE_MAX_CALLS": "1",
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


def verifier_rejudge_max_calls() -> int:
    """Strong re-judges per product: 0 (off) or 1 (the default); a larger value is capped at 1."""
    return int(_number("VERIFIER_REJUDGE_MAX_CALLS", 0.0, 1.0))


def model_prices_text() -> str:
    value = get("MODEL_PRICES")
    return value if isinstance(value, str) else ("" if value is None else str(value))


# --- query normaliser: a cheap model reads an abbreviated sheet name for the search queries only ------------
# QUERY_NORMALIZER                  'gemini' (default): one small Gemini Flash-Lite call per product (cached in the
#                                   database), whose reading writes better search words (catalog_match.normalizer);
#                                   'off': today's queries only. Never evidence, never identity.
# QUERY_NORMALIZER_RUN_BUDGET_USD   what those calls may cost in one run (estimated USD); past it they are skipped
DEFAULTS.update({
    "QUERY_NORMALIZER": "gemini",
    "QUERY_NORMALIZER_RUN_BUDGET_USD": "0.5",
})
QUERY_NORMALIZER_MODES = ("gemini", "off")


def query_normalizer() -> str:
    """'gemini' (default) or 'off'; an unknown value is 'off' (never a surprise paid call)."""
    value = str(get("QUERY_NORMALIZER") or "").strip().lower() or DEFAULTS["QUERY_NORMALIZER"]
    return value if value in QUERY_NORMALIZER_MODES else "off"


def query_normalizer_run_budget_usd() -> float:
    return _number("QUERY_NORMALIZER_RUN_BUDGET_USD", 0.0, 1000.0)


# --- sources package (P3): accessors ---

def expansion_enabled() -> bool:
    return bool(get("EXPANSION_ENABLED"))


def expansion_max_calls() -> int:
    """Paid calls the expansion round may make per product (0 turns the round off)."""
    value = get("EXPANSION_MAX_CALLS")
    return max(0, int(value)) if isinstance(value, int) else int(DEFAULTS["EXPANSION_MAX_CALLS"])


def expansion_scope_gate() -> bool:
    """True (default): the expansion round's paid steps skip out-of-scope products (expand.scope_gate)."""
    return bool(get("EXPANSION_SCOPE_GATE"))


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


def page_main_images_max_pages() -> int:
    """Trusted listing pages read per product for their own main image (0 = off, at most 3)."""
    value = get("PAGE_MAIN_IMAGES_MAX_PAGES")
    value = value if isinstance(value, int) else int(DEFAULTS["PAGE_MAIN_IMAGES_MAX_PAGES"])
    return min(PAGE_MAIN_IMAGES_MAX_PAGES_LIMIT, max(0, value))


def page_main_images_wait_s() -> float:
    """Seconds the search waits for those pages (0..10; a bad value is the default)."""
    return _number("PAGE_MAIN_IMAGES_WAIT_S", 0.0, 10.0)
# --- end sources package ---


# --- local catalog index: UAE retailer product pages from the stores' sitemaps (catalog_match.local_index) ---
# LOCAL_INDEX_ENABLED        look the product up in the local index (built by scripts/build_catalog_index.py);
#                            it only runs once the index has rows
# LOCAL_INDEX_MAX_PAGES      indexed product pages read per product (0 turns the lookup off); reads are free
# LOCAL_INDEX_PAGE_TTL_DAYS  how long what a page said (image, name, GTIN) is reused before it is read again
# LOCAL_INDEX_REFRESH_DAYS   a store whose newest complete harvest is older than this is read again, in the
#                            background, at the start of a nightly or worker run (catalog_match.index_refresh)
# LOCAL_INDEX_REFRESH_MAX_S  seconds one refresh may run (the stores are read one after the other and the refresh
#                            stops at this budget); 0 turns the automatic refresh off
DEFAULTS.update({
    "LOCAL_INDEX_ENABLED": True,
    "LOCAL_INDEX_MAX_PAGES": 3,
    "LOCAL_INDEX_PAGE_TTL_DAYS": 30,
    "LOCAL_INDEX_REFRESH_DAYS": 7,
    "LOCAL_INDEX_REFRESH_MAX_S": 300,
})
LOCAL_INDEX_MAX_PAGES_LIMIT = 8


def local_index_enabled() -> bool:
    return bool(get("LOCAL_INDEX_ENABLED"))


def local_index_max_pages() -> int:
    value = get("LOCAL_INDEX_MAX_PAGES")
    value = value if isinstance(value, int) else int(DEFAULTS["LOCAL_INDEX_MAX_PAGES"])
    return min(LOCAL_INDEX_MAX_PAGES_LIMIT, max(0, value))


def local_index_page_ttl_days() -> int:
    value = get("LOCAL_INDEX_PAGE_TTL_DAYS")
    value = value if isinstance(value, int) else int(DEFAULTS["LOCAL_INDEX_PAGE_TTL_DAYS"])
    return min(365, max(0, value))


def local_index_refresh_days() -> int:
    value = get("LOCAL_INDEX_REFRESH_DAYS")
    value = value if isinstance(value, int) else int(DEFAULTS["LOCAL_INDEX_REFRESH_DAYS"])
    return min(365, max(1, value))


def local_index_refresh_max_s() -> int:
    value = get("LOCAL_INDEX_REFRESH_MAX_S")
    value = value if isinstance(value, int) else int(DEFAULTS["LOCAL_INDEX_REFRESH_MAX_S"])
    return min(3600, max(0, value))
# --- end local catalog index ---


# --- speed package: how many products the worker searches at once, and the hedged Serper request ---
# WORKER_CONCURRENCY    products searched in parallel by the worker (main.run_worker_mode); clamped to 1..8.
#                       Higher is faster but spends the search quota faster (every provider's token bucket in
#                       catalog_match.ratelimit is shared by all of them, so a provider's per-minute limit holds).
# SERPER_HEDGE_AFTER_S  a Serper request still unanswered after this many seconds is sent once more and the
#                       first good answer is used (providers/serper.py); 0 turns it off. The second request is
#                       a second credit, recorded on the call (provider_health hedges).
DEFAULTS.update({
    "WORKER_CONCURRENCY": 5,
    "SERPER_HEDGE_AFTER_S": "4.5",
})
WORKER_CONCURRENCY_MIN = 1
WORKER_CONCURRENCY_MAX = 8


def worker_concurrency() -> int:
    """Products searched at once: WORKER_CONCURRENCY clamped to 1..8 (the default 5 for a missing or bad value)."""
    value = get("WORKER_CONCURRENCY")
    value = value if isinstance(value, int) else int(DEFAULTS["WORKER_CONCURRENCY"])
    return min(WORKER_CONCURRENCY_MAX, max(WORKER_CONCURRENCY_MIN, value))


def serper_hedge_after_s() -> float:
    """Seconds before a slow Serper request is sent a second time; 0 = never (a bad value is the default 4.5)."""
    try:
        value = float(str(get("SERPER_HEDGE_AFTER_S")).strip())
    except (TypeError, ValueError):
        value = float(DEFAULTS["SERPER_HEDGE_AFTER_S"])
    if value != value or value < 0:       # NaN or negative
        value = float(DEFAULTS["SERPER_HEDGE_AFTER_S"])
    return min(60.0, value)
# --- end speed package ---


# --- runtime package: the worker on a server (stops, hangs, memory, storage, dead-man's switch) ---
# PRODUCT_DEADLINE_MINUTES  a product still running after this many minutes is given up: its row goes back to the
#                           queue like a provider outage (PROVIDER_DOWN: retried 10, then 20 minutes later, parked
#                           after 3 in a run) and the worker takes the next row; clamped to 1..60
# SHUTDOWN_GRACE_S          on SIGTERM / SIGHUP (systemctl stop, a reboot) the products in progress get this many
#                           seconds to finish; the rows still running then go back to the queue; clamped to 0..600
# REMBG_MAX_PARALLEL        local BiRefNet cutouts (rembg) run at once; each one takes 2-3 GB of memory on CPU; 1..8
# NIGHTLY_PRUNE_ENABLED     the nightly run ends with the storage cleanup (scripts/prune_storage.py --apply): old
#                           candidate files no review row uses, superseded synced sheet writes, temp/search.log rotation
# PRUNE_MAX_SECONDS         time cap of that cleanup (10..3600)
# PRUNE_KEEP_DAYS           files and synced writes younger than this are never removed (7..3650)
# SEARCH_LOG_MAX_MB         temp/search.log is rotated above this size (1..10240)
# SEARCH_LOG_KEEP           rotated copies kept: search.log.1 .. search.log.N (1..20)
# HEALTHCHECK_URL           optional healthchecks.io-style ping URL (a URL, not an API key): the nightly run pings
#                           <url>/start, then <url> or <url>/fail; '' = off
DEFAULTS.update({
    "PRODUCT_DEADLINE_MINUTES": "8",
    "SHUTDOWN_GRACE_S": 45,
    "REMBG_MAX_PARALLEL": 1,
    "NIGHTLY_PRUNE_ENABLED": True,
    "PRUNE_MAX_SECONDS": 300,
    "PRUNE_KEEP_DAYS": 30,
    "SEARCH_LOG_MAX_MB": 50,
    "SEARCH_LOG_KEEP": 3,
    "HEALTHCHECK_URL": "",
})


def _clamped_int(name: str, low: int, high: int) -> int:
    value = get(name)
    value = value if isinstance(value, int) and not isinstance(value, bool) else int(DEFAULTS[name])
    return min(high, max(low, value))


def product_deadline_s() -> float:
    """Seconds one product may run before the worker gives it up (PRODUCT_DEADLINE_MINUTES, 1..60, default 8)."""
    return _number("PRODUCT_DEADLINE_MINUTES", 1.0, 60.0) * 60.0


def shutdown_grace_s() -> int:
    return _clamped_int("SHUTDOWN_GRACE_S", 0, 600)


def rembg_max_parallel() -> int:
    return _clamped_int("REMBG_MAX_PARALLEL", 1, 8)


def nightly_prune_enabled() -> bool:
    return bool(get("NIGHTLY_PRUNE_ENABLED"))


def prune_max_seconds() -> int:
    return _clamped_int("PRUNE_MAX_SECONDS", 10, 3600)


def prune_keep_days() -> int:
    return _clamped_int("PRUNE_KEEP_DAYS", 7, 3650)


def search_log_max_mb() -> int:
    return _clamped_int("SEARCH_LOG_MAX_MB", 1, 10240)


def search_log_keep() -> int:
    return _clamped_int("SEARCH_LOG_KEEP", 1, 20)


def healthcheck_url() -> str:
    """The ping URL without a trailing '/', or '' when unset or not an http(s) URL (never a surprise request)."""
    value = str(get("HEALTHCHECK_URL") or "").strip().rstrip("/")
    return value if value.lower().startswith(("https://", "http://")) and " " not in value else ""
# --- end runtime package ---


# --- embeddings package: CPU image embeddings, evidence only (catalog_match.embeddings) ---
# EMBEDDINGS            'off' (default) | 'dinov2' | 'siglip2': the model that reads the downloaded pictures for the
#                       brand look check (a review warning, never a decision) and the near-duplicate cosine. 'off'
#                       never imports onnxruntime, never loads a model and never reads the approved_embeddings table.
#                       deploy/ubuntu/install.sh --with-embeddings installs onnxruntime, downloads the model and turns
#                       it on ('dinov2'); an unknown value reads as 'off'.
# EMBEDDINGS_MODEL_DIR  folder of the pinned model files; '' = <U2NET_HOME>/embeddings when U2NET_HOME is set (the
#                       shared models folder of the server's units and dashboard), else temp/models in the repository
DEFAULTS.update({
    "EMBEDDINGS": "off",
    "EMBEDDINGS_MODEL_DIR": "",
})
EMBEDDINGS_MODES = ("off", "dinov2", "siglip2")


def embeddings_mode() -> str:
    """'off' (default), 'dinov2' or 'siglip2'; anything else reads as 'off' (never a surprise model download)."""
    value = str(get("EMBEDDINGS") or "").strip().lower()
    return value if value in EMBEDDINGS_MODES else "off"


def embeddings_model_dir() -> str:
    """Absolute folder of the embedding model files (EMBEDDINGS_MODEL_DIR, see above)."""
    value = str(get("EMBEDDINGS_MODEL_DIR") or "").strip()
    if not value:
        shared = os.getenv("U2NET_HOME", "").strip()
        value = os.path.join(shared, "embeddings") if shared else os.path.join("temp", "models")
    if not os.path.isabs(value):
        value = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), value)
    return value
# --- end embeddings package ---
