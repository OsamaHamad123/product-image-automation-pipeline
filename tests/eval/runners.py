"""Replay one golden SKU through an engine, fully offline.

run_legacy(sku, cassette)  drives the unmodified v1 code, image_search.search_best_product_image.
run_v2(sku, cassette)      drives catalog_match.pipeline.find_product_image with the production provider set.

Both return a metrics.Outcome. Nothing here touches the network, a database
or an API key: providers answer from the fixture, downloads come from
imagegen and the vision model answers from the recorded cassette.

Replay rules shared by both engines
-----------------------------------
* A provider returns the SKU's candidates whose surfaced_by includes the kind
  of the query: "gtin" when the query carries the SKU's barcode, else "text".
  A site: query only returns candidates from the listed domains.
* surfaced_by may instead name query ids ("Q1".."Q4", "R1", "R2", "custom"): such a
  candidate is returned only for those queries of the v2 plan. Recorded sets
  (scripts/eval_record.py) store it that way, so a v2 replay measures the query
  plan, the early stop and the relaxations instead of serving the whole pool to
  every query.
* v2 fixture providers only answer a text query that is about the SKU (it shares
  a brand or name word with the sheet row); an unrelated query returns nothing.
* The same pool therefore reaches v1 and v2; Open Food Facts ("off")
  candidates only reach v2, because v1 has no such source.
* v2 runs with the provider set production builds (provider_set): "serper" is
  Serper primary + Open Food Facts + Bing HTML as fallback-only (plus the legacy
  CSE adapter when the SKU has cse_legacy candidates); "bing_only" is the no-key
  setup, Bing HTML as the sole (unsanctioned) search source + Open Food Facts.
* Serper candidates are fed to v1 as its Google engine (title and real size),
  Bing candidates through v1's own Bing mapping: title = m["desc"] or the
  query when "desc" is missing, and a reported size of 800x800.
"""

from __future__ import annotations

import contextlib
import functools
import hashlib
import inspect
import io
import json
import logging
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Mapping, Optional, Sequence, Tuple
from unittest import mock

import imagegen
import imagegen_extra
import metrics
from metrics import Outcome

log = logging.getLogger(__name__)

EVAL_DIR = Path(__file__).resolve().parent
FIXTURES = EVAL_DIR / "fixtures"
REPO_ROOT = EVAL_DIR.parent.parent

DOWNLOAD_OK = "ok"


# ---------------------------------------------------------------------------
# Fixture access
# ---------------------------------------------------------------------------

@functools.lru_cache(maxsize=None)
def _load_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def load_mappings(path: Optional[os.PathLike] = None) -> Dict[str, Any]:
    doc = _load_json(str(path or FIXTURES / "brand_mappings.json"))
    return json.loads(json.dumps(doc["mappings"]))      # a private copy per caller


def candidate_seed(sku_id: str, cand_id: str) -> int:
    return int(hashlib.sha256(f"{sku_id}/{cand_id}".encode("utf-8")).hexdigest()[:8], 16)


@functools.lru_cache(maxsize=None)
def _render(recipe_json: str, seed: int) -> bytes:
    recipe = json.loads(recipe_json)
    if recipe.get("kind") in imagegen_extra.EXTRA_RECIPES:
        return imagegen_extra.generate(recipe, seed)        # the other sets' recipes; imagegen itself never changes
    return imagegen.generate(recipe, seed)


def candidate_image(sku: Mapping[str, Any], cand: Mapping[str, Any]) -> bytes:
    """Image bytes for a candidate: a recorded blob when present, else the synthetic recipe.

    seed_of names another candidate of the SKU whose seed the recipe is drawn with: the same picture again (the
    same recipe gives the same bytes; another JPEG quality a near-duplicate), as stores copy one packshot.
    """
    blob = cand.get("image_file")
    if blob:
        base = Path(sku.get("_base_dir") or FIXTURES)
        return (base / blob).read_bytes()
    recipe = cand.get("image_recipe")
    if not recipe:
        raise ValueError(f"{sku['id']}/{cand['id']}: no image_recipe or image_file")
    return _render(json.dumps(recipe, sort_keys=True), candidate_seed(sku["id"], cand.get("seed_of") or cand["id"]))


def candidate_mime(cand: Mapping[str, Any]) -> str:
    if cand.get("mime"):
        return str(cand["mime"])
    return imagegen_extra.mime_type(cand.get("image_recipe") or {"kind": "packshot_white"})


def norm_url(url: str) -> str:
    """Lower-cased host + path, query string dropped: how a candidate is recognised again."""
    url = (url or "").strip()
    url = re.sub(r"^[a-z]+://", "", url, flags=re.I)
    url = url.split("#", 1)[0].split("?", 1)[0]
    if url.lower().startswith("www."):
        url = url[4:]
    return url.lower().rstrip("/")


class UrlIndex:
    """image_url -> candidate for one SKU (exact first, then normalised)."""

    def __init__(self, sku: Mapping[str, Any]):
        self.exact = {c["image_url"]: c for c in sku.get("candidates", [])}
        self.loose = {norm_url(c["image_url"]): c for c in sku.get("candidates", [])}

    def get(self, url: Optional[str]) -> Optional[Mapping[str, Any]]:
        if not url:
            return None
        return self.exact.get(url) or self.loose.get(norm_url(url))

    def cid(self, url: Optional[str]) -> Optional[str]:
        cand = self.get(url)
        return cand["id"] if cand else None


def _digits(text: str) -> str:
    return re.sub(r"\D", "", text or "")


def query_kind(query: str, sku: Mapping[str, Any]) -> str:
    """'gtin' when the query carries the SKU's barcode (raw or as digits), else 'text'."""
    raw = str(sku.get("barcode") or "").strip()
    if raw and raw in query:
        return "gtin"
    digits = _digits(raw).lstrip("0")
    if len(digits) >= 8 and digits in _digits(query):
        return "gtin"
    return "text"


def site_domains(query: str) -> List[str]:
    return [d.lower().lstrip("*.").replace("www.", "") for d in re.findall(r"site:([^\s()]+)", query, flags=re.I)]


def _host(url: str) -> str:
    m = re.match(r"^[a-z]+://([^/]+)", url or "", flags=re.I)
    return (m.group(1).lower() if m else "").replace("www.", "")


QUERY_IDS = frozenset({"Q1", "Q2", "Q3", "Q4", "R1", "R2", "custom"})


def _norm_tokens(text: str) -> List[str]:
    """Clitic-stripped, normalised tokens (catalog_match.text_norm when present)."""
    try:
        from catalog_match.text_norm import tokens
        return list(tokens(text, strip_clitics=True))
    except ImportError:  # pragma: no cover - catalog_match is part of the repository
        return re.findall(r"\w+", (text or "").lower())


def _content_tokens(text: str) -> set:
    return {t for t in _norm_tokens(text) if len(t) >= 2 and not any(ch.isdigit() for ch in t)}


def query_mentions_sku(query: str, sku: Mapping[str, Any]) -> bool:
    """True when a text query shares a brand or name word with the sheet row (site: scopes ignored)."""
    words = _content_tokens(re.sub(r"site:\S+|\bOR\b", " ", query or ""))
    row = " ".join(str(sku.get(k) or "") for k in ("brand", "brand_ar", "name_en", "name_ar"))
    return bool(words & _content_tokens(row))


def surfaced(sku: Mapping[str, Any], provider: str, query: str, query_id: Optional[str] = None,
             relevant_only: bool = False) -> List[Mapping[str, Any]]:
    """Candidates a provider returns for a query, in the provider's rank order.

    query_id is the v2 plan id of the query (None for the v1 replay). A candidate whose
    surfaced_by names query ids is returned only for those ids; without an id the ids
    stand for their kind (Q4 is the GTIN query, the others are text). relevant_only
    drops every candidate for a text query that is not about the SKU.
    """
    kind = query_kind(query, sku)
    if relevant_only and kind == "text" and not query_mentions_sku(query, sku):
        return []
    sites = site_domains(query)
    out = []
    for cand in sku.get("candidates", []):
        if cand.get("provider") != provider:
            continue
        tags = set(cand.get("surfaced_by", ["text"]))
        ids = tags & QUERY_IDS
        if ids:
            if query_id is not None:
                if query_id not in ids:
                    continue
            elif kind not in {("gtin" if i == "Q4" else "text") for i in ids} | (tags - QUERY_IDS):
                continue
        elif kind not in tags:
            continue
        if sites:
            hosts = {cand.get("domain", ""), _host(cand.get("page_url", "")), _host(cand.get("image_url", ""))}
            if not any(h == s or h.endswith("." + s) for h in hosts if h for s in sites):
                continue
        out.append(cand)
    return sorted(out, key=lambda c: (int(c.get("rank", 0)), c["id"]))


def sku_row(sku: Mapping[str, Any]) -> Dict[str, str]:
    """The sheet row as the engines receive it. An Arabic-only row has its Arabic name in the name column."""
    return {
        "name": sku.get("name_en") or sku.get("name_ar") or "",
        "name_ar": sku.get("name_ar", ""),
        "brand": sku.get("brand", ""),
        "brand_ar": sku.get("brand_ar", ""),
        "barcode": sku.get("barcode", ""),
        "category": sku.get("category", ""),
        "size": sku.get("size", ""),
    }


def cassette_entry(cassette: Mapping[str, Any], sku_id: str, cand_id: str) -> Optional[Dict[str, Any]]:
    return (cassette.get("verdicts", {}).get(sku_id) or {}).get(cand_id)


# ---------------------------------------------------------------------------
# Network isolation
# ---------------------------------------------------------------------------

class NetworkBlocked(OSError):
    """Raised by the harness for any socket connection that leaves the machine."""


_LOOPBACK = ("127.", "::1", "localhost", "0:0:0:0:0:0:0:1")


def _is_loopback(host: Any) -> bool:
    return isinstance(host, str) and (host.startswith(_LOOPBACK) or host == "")


@contextlib.contextmanager
def network_blocked(attempts: Optional[List[str]] = None, block_db: bool = True) -> Iterator[List[str]]:
    """Refuse every outbound connection for the duration and record the attempts.

    Loopback stays open because asyncio on Windows builds its self-pipe with
    a loopback connect. Database connects are refused as well when block_db,
    so no run can depend on MariaDB being up.
    """
    import socket

    attempts = attempts if attempts is not None else []
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    real_getaddrinfo = socket.getaddrinfo

    def guard(address: Any) -> None:
        host = address[0] if isinstance(address, tuple) and address else address
        if not _is_loopback(host):
            attempts.append(f"connect {address!r}")
            raise NetworkBlocked(f"offline evaluation: connection to {address!r} refused")

    def connect(self, address):  # type: ignore[no-untyped-def]
        guard(address)
        return real_connect(self, address)

    def connect_ex(self, address):  # type: ignore[no-untyped-def]
        guard(address)
        return real_connect_ex(self, address)

    def getaddrinfo(host, *args, **kwargs):  # type: ignore[no-untyped-def]
        if not _is_loopback(host):
            attempts.append(f"getaddrinfo {host!r}")
            raise NetworkBlocked(f"offline evaluation: DNS lookup of {host!r} refused")
        return real_getaddrinfo(host, *args, **kwargs)

    patches = [
        mock.patch.object(socket.socket, "connect", connect),
        mock.patch.object(socket.socket, "connect_ex", connect_ex),
        mock.patch.object(socket, "getaddrinfo", getaddrinfo),
    ]
    if block_db:
        try:
            import pymysql

            def refuse_db(*args, **kwargs):  # type: ignore[no-untyped-def]
                attempts.append("pymysql.connect")
                raise pymysql.err.OperationalError(2003, "offline evaluation: database disabled")

            patches.append(mock.patch.object(pymysql, "connect", refuse_db))
        except ImportError:  # pragma: no cover - pymysql is a runtime dependency
            pass
    with contextlib.ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        yield attempts


def outbound_attempts(attempts: Sequence[str]) -> List[str]:
    """Attempts that tried to leave the machine (DB refusals are not network)."""
    return [a for a in attempts if not a.startswith("pymysql")]


# ---------------------------------------------------------------------------
# Legacy (v1) replay
# ---------------------------------------------------------------------------

# Arabic legacy reason text -> short rule name. Order matters: first match wins.
_LEGACY_RULES: Tuple[Tuple[str, str], ...] = (
    ("الصورة صغيرة جداً", "too_small_reported"),
    ("كلمة مستبعدة", "cartoon_keyword"),
    ("نطاق مستبعد", "excluded_domain"),
    ("منافس مستبعد", "competitor_substring"),
    ("تعارض في الحجم", "size_clash"),
    ("تعارض في عدد العبوات", "pack_clash"),
    ("عدم مطابقة البراند", "brand_mismatch"),
    ("فشل التحميل", "download_failed"),
    ("درجة المطابقة الدلالية", "semantic_score_low"),
    ("BLIP", "blip_brand_conflict"),
    ("Moondream", "moondream_rejected"),
    ("تكرار بصري", "visual_duplicate"),
    ("Gemini Vision", "gemini_rejected"),
    ("فشل تحليل الصورة", "image_analysis_error"),
)


def legacy_rules(reasons: Sequence[str]) -> List[str]:
    """Rule names for a legacy candidate's reason strings (quality gates keep their English label)."""
    rules: List[str] = []
    for reason in reasons:
        text = str(reason)
        if text.startswith("مقبولة"):
            continue                                   # acceptance notes, not rejections
        if "فشل التحقق الهندسي" in text:
            found = [q for q in metrics.QUALITY_RULES_V1 if q in text]
            rules.extend(found or ["quality_gate_other"])
            continue
        for needle, rule in _LEGACY_RULES:
            if needle in text:
                rules.append(rule)
                break
        else:
            rules.append("other:" + text[:40])
    return rules


# Legacy config values the replay pins to the shipped defaults, so a local .env
# or dashboard settings row cannot change the baseline.
LEGACY_CONFIG = {
    "FILTER_COMPETITORS": True,
    "STRICT_BRAND_MATCH": True,
    "DISABLE_LOCAL_AI_MODELS": True,
    "USE_SIGLIP_SEMANTIC_CHECK": True,
    "USE_BLIP_CAPTION_CHECK": True,
    "USE_MOONDREAM_CHECK": False,
    "ENABLE_GEMINI_PRE_VALIDATION": True,
    "CLIP_RELEVANCE_THRESHOLD": 0.22,
    "CLIP_GREY_ZONE_THRESHOLD": 0.18,
    "MIN_IMAGE_WIDTH": 100,
    "MIN_IMAGE_HEIGHT": 100,
    "GOOGLE_SEARCH_API_KEY": "",
    "GOOGLE_SEARCH_API_KEYS": [],
    "GOOGLE_SEARCH_CX": "",
    "GOOGLE_SEARCH_CX_LIST": [],
    "PROXY_URL": "",
}


def legacy_modules() -> Dict[str, Any]:
    """Import the legacy modules (their import-time DB probes fail harmlessly when offline)."""
    import google_sheets
    import image_dedup_bktree
    import image_quality_gatekeeper
    import image_search
    import local_cache_db
    import config
    return {
        "config": config,
        "google_sheets": google_sheets,
        "image_dedup_bktree": image_dedup_bktree,
        "image_quality_gatekeeper": image_quality_gatekeeper,
        "image_search": image_search,
        "local_cache_db": local_cache_db,
    }


def legacy_gemini_accepts(entry: Optional[Mapping[str, Any]]) -> bool:
    """The legacy binary Gemini check, answered from the structured verdict.

    Mirrors the legacy prompt: the brand must match, a variant or size that is
    visibly different rejects, and an unreadable size may still be accepted.
    """
    if not entry:
        return False
    return (entry.get("brand_match") == "yes" and entry.get("variant_match") != "no"
            and entry.get("size_match") != "no")


def legacy_queries(product_name: str, brand: str) -> List[str]:
    """Stand-in for expand_query_via_gemini: '{brand} {name}', as its own no-Gemini fallback words it.

    The fallback also lists a '... packaging' variant, but every text query
    surfaces the same fixture pool and the loop stops at the first hit, so
    one query gives the same result.
    """
    name, brand = (product_name or "").strip(), (brand or "").strip()
    if brand and name.lower().startswith(brand.lower()):
        return [name]
    return [f"{brand} {name}".strip()]


def _legacy_item(cand: Mapping[str, Any], engine: str, query: str) -> Dict[str, Any]:
    if engine == "bing":
        m = cand.get("bing_m") or {}
        return {"url": cand["image_url"], "title": m.get("desc", query), "width": 800, "height": 800}
    return {"url": cand["image_url"], "title": cand.get("title", ""), "width": int(cand.get("width") or 800),
            "height": int(cand.get("height") or 800)}


def _legacy_download(sku: Mapping[str, Any], cand: Optional[Mapping[str, Any]], image_search: Any):
    """What legacy stream_and_validate_target + execute_batch_processing would yield for one URL."""
    from PIL import Image

    if cand is None:
        return None, "FAILED_DOWNLOAD"
    download = cand.get("download", DOWNLOAD_OK)
    if download == "403":
        return None, "REJECTED_STATUS_403"
    if download == "html":
        return None, "UNSUPPORTED_MIME_TYPE_text/html; charset=utf-8"
    if download == "svg":
        return None, "UNSUPPORTED_MIME_TYPE_image/svg+xml"
    if download != DOWNLOAD_OK:
        return None, f"REJECTED_STATUS_{download}"
    mime = candidate_mime(cand)
    if not any(m in mime for m in getattr(image_search, "SUPPORTED_MIME_TYPES", (mime,))):
        return None, f"UNSUPPORTED_MIME_TYPE_{mime}"
    data = candidate_image(sku, cand)
    max_size = getattr(image_search, "MAX_FILE_SIZE", 4 * 1024 * 1024)
    min_size = getattr(image_search, "MIN_FILE_SIZE", 10 * 1024)
    if len(data) > max_size:
        return None, "DYNAMIC_STREAM_ABORT_OVERSIZE"
    if len(data) < min_size:
        return None, f"FINAL_DOWNLOAD_SIZE_TOO_SMALL_{len(data)}"
    try:
        with Image.open(io.BytesIO(data)) as im:
            im.verify()
    except Exception as exc:  # pragma: no cover - fixture images always decode
        return None, f"IMAGE_CORRUPT_OR_UNREADABLE_{exc}"
    return data, "VERIFICATION_SUCCESS"


def run_legacy(sku: Mapping[str, Any], cassette: Mapping[str, Any], scenario: str = "normal",
               mappings: Optional[Dict[str, Any]] = None) -> Outcome:
    """Run the legacy v1 search for one fixture SKU and normalise its result."""
    mods = legacy_modules()
    image_search = mods["image_search"]
    gatekeeper = mods["image_quality_gatekeeper"]
    config = mods["config"]
    mappings = mappings if mappings is not None else load_mappings()
    index = UrlIndex(sku)
    by_sha: Dict[str, str] = {}
    calls = {"google": 0, "bing": 0, "vlm": 0, "downloads": 0}
    queries: List[str] = []

    async def fetch_google(self, session, query):  # type: ignore[no-untyped-def]
        calls["google"] += 1
        queries.append(query)
        return [_legacy_item(c, "google", query) for c in surfaced(sku, "serper", query)]

    async def fetch_bing(self, session, query):  # type: ignore[no-untyped-def]
        calls["bing"] += 1
        return [_legacy_item(c, "bing", query) for c in surfaced(sku, "bing_html", query)]

    async def fetch_nothing(self, query):  # type: ignore[no-untyped-def]
        return []

    async def batch(url_collection):  # type: ignore[no-untyped-def]
        results = {}
        for url in url_collection:
            calls["downloads"] += 1
            data, status = _legacy_download(sku, index.get(url), image_search)
            if data is not None:
                by_sha[hashlib.sha256(data).hexdigest()] = index.cid(url) or ""
            results[url] = (data, status)
        return results

    def gemini(image_path, product_name, brand):  # type: ignore[no-untyped-def]
        calls["vlm"] += 1
        with open(image_path, "rb") as fh:
            cid = by_sha.get(hashlib.sha256(fh.read()).hexdigest())
        return legacy_gemini_accepts(cassette_entry(cassette, sku["id"], cid) if cid else None)

    real_gemini = image_search.validate_image_via_gemini_vision

    def gemini_down(image_path, product_name, brand):  # type: ignore[no-untyped-def]
        calls["vlm"] += 1
        return real_gemini(image_path, product_name, brand)   # the real code with no key configured

    def no_segmentation(self, bgr_image):  # type: ignore[no-untyped-def]
        raise RuntimeError("GrabCut skipped in replay; legacy discards its result via a NameError")

    scraper = image_search.ParallelConsensusScraper
    # The legacy entry points the replay cannot work without.
    patches = [
        mock.patch.object(scraper, "_fetch_google", fetch_google),
        mock.patch.object(scraper, "_fetch_bing", fetch_bing),
        mock.patch.object(image_search, "execute_batch_processing", batch),
        mock.patch.object(time, "sleep", lambda *a, **k: None),
        # once the v2 facade is merged, SEARCH_ENGINE selects v1 or v2 behind the same function
        mock.patch.dict(os.environ, {"SEARCH_ENGINE": "v1"}),
    ]
    # Helpers that later clean-ups may delete: stub them only while they exist.
    optional = [
        (scraper, "_fetch_yandex", fetch_nothing),
        (scraper, "_fetch_duckduckgo", fetch_nothing),
        (image_search, "expand_query_via_gemini", legacy_queries),
        (image_search, "get_bktree", lambda: mods["image_dedup_bktree"].PerceptualDeduplicationTree()),
        (image_search, "print", lambda *a, **k: None),
        (mods["google_sheets"], "align_brand_via_gemini", lambda product_name, sheet_brand: sheet_brand),
        (mods["google_sheets"], "get_sheets_client", lambda *a, **k: object()),
        (mods["google_sheets"], "get_brand_mappings", lambda *a, **k: mappings),
        (mods["local_cache_db"], "get_cached_product", lambda *a, **k: None),
        (mods["local_cache_db"], "find_visual_duplicate", lambda *a, **k: None),
        (mods["local_cache_db"], "get_active_learning_clutter_flag", lambda *a, **k: False),
        (getattr(gatekeeper, "BoundaryComplianceSegmenter", None), "segment_foreground", no_segmentation),
    ]
    patches += [mock.patch.object(obj, name, value) for obj, name, value in optional
                if obj is not None and hasattr(obj, name)]
    if isinstance(getattr(image_search, "_dynamic_brand_mappings", None), dict):
        patches.append(mock.patch.dict(image_search._dynamic_brand_mappings, clear=True))
    pins = dict(LEGACY_CONFIG, SEARCH_ENGINE="v1")
    if scenario == "gemini_down":
        pins["GEMINI_API_KEY"] = ""
        patches.append(mock.patch.object(image_search, "validate_image_via_gemini_vision", gemini_down))
    else:
        pins["GEMINI_API_KEY"] = "offline-cassette"
        patches.append(mock.patch.object(image_search, "validate_image_via_gemini_vision", gemini))
    for key, value in pins.items():
        patches.append(mock.patch.object(config, key, value, create=True))

    row = sku_row(sku)
    trace: Dict[str, Any] = {}
    t0 = time.perf_counter()
    result: Any = None
    error: Optional[str] = None
    with contextlib.ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        try:
            result = image_search.search_best_product_image(
                f"{row['name']} {row['brand']}".strip(), row["name"], row["brand"],
                product_name_ar=row["name_ar"], brand_ar=row["brand_ar"], barcode=row["barcode"],
                category=row["category"], trace=trace, brand_mappings=mappings)
        except Exception as exc:  # the replay records crashes instead of hiding them
            log.exception("legacy search crashed for %s", sku["id"])
            error = f"{type(exc).__name__}: {exc}"
    seconds = time.perf_counter() - t0
    return _legacy_outcome(sku, index, result, trace, error, queries, calls, seconds)


def _legacy_outcome(sku, index, result, trace, error, queries, calls, seconds) -> Outcome:  # type: ignore[no-untyped-def]
    pool: List[str] = []
    kills: Dict[str, List[str]] = {}
    chosen_url = result.get("url") if isinstance(result, dict) else None
    chosen_id = index.cid(chosen_url)
    last_resort = False
    for step in trace.get("steps", []):
        for cand in step.get("candidates", []):
            cid = index.cid(cand.get("url"))
            if cid is None:
                continue
            if cid not in pool:
                pool.append(cid)
            reasons = [str(r) for r in cand.get("reasons", [])]
            if cid == chosen_id and any("كخيار بديل أخير" in r for r in reasons):
                last_resort = True
            if cand.get("status") != "accepted":
                rules = legacy_rules(reasons)
                if rules:
                    merged = kills.setdefault(cid, [])
                    merged.extend(r for r in rules if r not in merged)
    kills.pop(chosen_id, None)

    if error is not None:
        decision, status = metrics.ERROR, "error"
    elif not chosen_url:
        decision, status = metrics.NOT_FOUND, "none"
    else:
        needs_review = bool(result.get("needs_review"))
        decision = metrics.PRESELECTED if needs_review else metrics.AUTO
        status = str(result.get("status") or ("last_resort" if last_resort else "accepted"))
    needs_review = bool(isinstance(result, dict) and result.get("needs_review")) or not chosen_url
    return Outcome(
        sku_id=sku["id"], engine="v1", decision=decision, chosen_id=chosen_id, chosen_url=chosen_url,
        needs_review=needs_review, auto=bool(chosen_url) and not needs_review and error is None, status=status,
        failure_code=None, pool=pool, kills=kills, queries=list(queries),
        provider_calls={"google": calls["google"], "bing": calls["bing"]}, vlm_calls=calls["vlm"],
        vlm_images_max=1 if calls["vlm"] else 0, n_preselected=1 if decision == metrics.PRESELECTED else 0,
        error=error, seconds=round(seconds, 3))


# ---------------------------------------------------------------------------
# v2 replay
# ---------------------------------------------------------------------------

def _v2_modules():
    """catalog_match stages. Imported directly: a broken v2 import must fail the gate, not skip it."""
    import importlib

    return tuple(importlib.import_module(f"catalog_match.{name}") for name in ("pipeline", "identity", "models"))


# Search providers a fixture candidate may name -> sanctioned (D7). "off" is the GTIN lookup.
SEARCH_PROVIDERS = {"serper": True, "cse_legacy": True, "bing_html": False}
LOOKUP_PROVIDER = "off"
PROVIDER_SETS = ("serper", "bing_only")


def _to_candidate(models: Any, cand: Mapping[str, Any], as_provider: Optional[str] = None) -> Any:
    """models.Candidate for a fixture candidate; as_provider re-serves it as another provider's result."""
    provider = as_provider or cand["provider"]
    as_bing = provider == "bing_html"
    return models.Candidate(
        image_url=cand["image_url"], page_url=cand.get("page_url", ""), page_title=cand.get("page_title", ""),
        title=cand.get("title", ""), snippet=cand.get("snippet", ""), domain=cand.get("domain", ""),
        width=None if as_bing else cand.get("width"), height=None if as_bing else cand.get("height"),
        provider=provider, query_id="", rank=int(cand.get("rank", 0)), gtin_on_page=cand.get("gtin_on_page"),
        sanctioned=SEARCH_PROVIDERS.get(provider, provider == LOOKUP_PROVIDER))


class FixtureProvider:
    """models.Provider backed by the fixture: returns the candidates a query would surface.

    serve lists the fixture provider names this provider answers with (default: its own
    name); the "bing_only" set lets Bing answer with the Serper listings as well, re-shaped
    as Bing results (unsanctioned, no dimensions), so the no-key setup has a real pool.
    """

    def __init__(self, models: Any, sku: Mapping[str, Any], name: str, sanctioned: bool, fallback: bool = False,
                 serve: Optional[Sequence[str]] = None):
        self.models, self.sku, self.name, self.sanctioned = models, sku, name, sanctioned
        self.fallback = bool(fallback)
        self.serve = tuple(serve or (name,))
        self.calls: List[Tuple[str, str]] = []
        self._plan_key: Any = None
        self._plan: Dict[str, str] = {}

    def query_id(self, query: str, spec: Any) -> Optional[str]:
        """The v2 plan id of a query text (Q1..Q4, R1, R2), 'custom' for any other text."""
        if not isinstance(spec, self.models.SkuSpec):
            return None
        if self._plan_key is not spec:
            from catalog_match.query_plan import build_queries, relaxations
            self._plan = {}
            for q in list(build_queries(spec)) + list(relaxations(spec)):
                self._plan.setdefault(" ".join(q.text.split()), q.query_id)
            self._plan_key = spec
        return self._plan.get(" ".join((query or "").split()), "custom")

    def search(self, query: str, hl: str, spec: Any) -> Any:
        self.calls.append((query, hl))
        qid = self.query_id(query, spec)
        rows = [c for src in self.serve for c in surfaced(self.sku, src, query, query_id=qid, relevant_only=True)]
        rows.sort(key=lambda c: (int(c.get("rank", 0)), c["provider"] != self.name, c["id"]))
        cands = [_to_candidate(self.models, c, as_provider=self.name) for c in rows]
        return self.models.ProviderResult(provider=self.name, status="ok" if cands else "empty", http_status=200,
                                          latency_ms=0, candidates=cands)


class FixtureOffProvider(FixtureProvider):
    """Open Food Facts: a GTIN lookup, answered only for a valid GTIN.

    kind = "lookup" like the real catalog_match OffProvider: retrieve() calls lookup()
    once per SKU (in parallel with Q1) instead of search() for every query.
    """

    kind = "lookup"

    def __init__(self, models: Any, sku: Mapping[str, Any]):
        super().__init__(models, sku, "off", True)
        self.lookups = 0

    def _result(self, spec: Any) -> Any:
        cands = []
        if getattr(spec, "gtin", None):
            cands = [_to_candidate(self.models, c) for c in self.sku.get("candidates", [])
                     if c.get("provider") == "off" and _digits(c.get("gtin_on_page") or "").lstrip("0")
                     == _digits(spec.gtin).lstrip("0")]
        return self.models.ProviderResult(provider="off", status="ok" if cands else "empty", http_status=200,
                                          latency_ms=0, candidates=cands)

    def lookup(self, spec: Any) -> Any:
        self.lookups += 1
        return self._result(spec)

    def search(self, query: str, hl: str, spec: Any) -> Any:
        self.calls.append((query, hl))
        return self._result(spec)


class FixtureLookupProvider(FixtureProvider):
    """A recorded set's candidates the engine found without a search (eval_record --from-db): its own page reads
    ('page') and the local catalog index ('local_index'). A lookup like the real LocalIndexProvider: asked once per
    SKU, unsanctioned (never auto-published), it never stops the search on its own."""

    kind = "lookup"
    needs_gtin = False

    def __init__(self, models: Any, sku: Mapping[str, Any], name: str):
        super().__init__(models, sku, name, False)
        self.lookup_query_id = "IDX" if name == "local_index" else "PAGE"
        self.lookups = 0

    def lookup(self, spec: Any) -> Any:
        self.lookups += 1
        cands = [_to_candidate(self.models, c) for c in self.sku.get("candidates", [])
                 if c.get("provider") == self.name and not c.get("source_only")]
        return self.models.ProviderResult(provider=self.name, status="ok" if cands else "empty", http_status=200,
                                          latency_ms=0, candidates=cands)

    def search(self, query: str, hl: str, spec: Any) -> Any:
        self.calls.append((query, hl))
        return self.lookup(spec)


RECORDED_LOOKUPS = ("page", "local_index")
MAX_SERVED_SIDE = 2048


def _served_bytes(data: bytes, cand: Mapping[str, Any]) -> bytes:
    """A recorded blob stored smaller than the image the engine downloaded (eval_record --from-db keeps <= 384 px
    copies) is served back at its recorded size (capped at MAX_SERVED_SIDE), so the size rules see the original's
    size; the image-quality scores of such a set read an upscaled copy."""
    size = cand.get("recorded_size")
    if not size or not cand.get("image_file"):
        return data
    from PIL import Image

    with Image.open(io.BytesIO(data)) as im:
        im.load()
        w, h = int(size[0]), int(size[1])
        scale = min(1.0, MAX_SERVED_SIDE / float(max(w, h, 1)))
        w, h = max(1, round(w * scale)), max(1, round(h * scale))
        if (w, h) == im.size or w <= im.size[0]:
            return data
        out = im.convert("RGBA" if im.mode in ("RGBA", "LA", "P") else "RGB").resize((w, h), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    if out.mode == "RGBA":
        out.save(buf, format="PNG")
    else:
        out.save(buf, format="JPEG", quality=92)
    return buf.getvalue()


class FixtureFetcher:
    """models.Fetcher returning imagegen bytes; failed downloads mirror the fixture's download field."""

    ERRORS = {"403": "http_403", "html": "not_image", "svg": "not_image", "not_recorded": "not_recorded"}

    def __init__(self, models: Any, sku: Mapping[str, Any]):
        self.models, self.sku = models, sku
        self.index = UrlIndex(sku)
        self.fetched: List[str] = []

    def fetch(self, cands: List[Any], spec: Any) -> List[Any]:
        from PIL import Image

        import image_dedup_bktree

        out = []
        for cand in cands:
            fc = self.index.get(cand.image_url)
            self.fetched.append(fc["id"] if fc else cand.image_url)
            if fc is None:
                out.append(self.models.FetchedImage(candidate=cand, ok=False, error="unknown_url"))
                continue
            download = fc.get("download", DOWNLOAD_OK)
            if download != DOWNLOAD_OK:
                out.append(self.models.FetchedImage(candidate=cand, ok=False,
                                                    error=self.ERRORS.get(download, f"http_{download}")))
                continue
            data = _served_bytes(candidate_image(self.sku, fc), fc)
            with Image.open(io.BytesIO(data)) as im:
                im.load()
                width, height = im.size
                phash = format(image_dedup_bktree.calculate_phash(im.convert("RGB")), "016x")
            out.append(self.models.FetchedImage(candidate=cand, ok=True, content_sha256=hashlib.sha256(data).hexdigest(),
                                                width=width, height=height, path_or_bytes=data, phash=phash))
        return out


def _size_conflict(size_text: str, spec: Any) -> bool:
    """D6: the verdict's size_text is re-parsed with the D4 grammar and must agree with the SKU size."""
    if not size_text or getattr(spec, "size", None) is None:
        return False
    try:
        from catalog_match import sizes
    except ImportError:  # sizes grammar not merged yet: rely on the model's size call
        return False
    try:
        found = sizes.parse_sizes(size_text, "vlm")
        return bool(found) and sizes.compare(spec.size, found) == "conflict"
    except Exception:
        log.warning("catalog_match.sizes could not re-parse %r; the model's size call stands", size_text,
                    exc_info=True)
        return False


def _pack_conflict(pack_count: Any, spec: Any) -> bool:
    target = getattr(spec, "pack_count", None) or getattr(getattr(spec, "size", None), "pack_count", None)
    if not target or pack_count in (None, ""):
        return False
    return int(pack_count or 1) != int(target or 1)


def decide_verdict(entry: Mapping[str, Any], spec: Any) -> str:
    """Code decides from the recorded readings (D6): MATCH, MISMATCH or UNSURE."""
    if "no" in (entry.get("brand_match"), entry.get("variant_match"), entry.get("size_match")):
        return "MISMATCH"
    if _size_conflict(entry.get("size_text", ""), spec) or _pack_conflict(entry.get("pack_count"), spec):
        return "MISMATCH"
    if entry.get("brand_match") == "yes" and entry.get("view") == "front_packshot":
        return "MATCH"
    return "UNSURE"


def _real_make_verdict() -> Optional[Callable[..., Any]]:
    """catalog_match.verify.make_verdict when merged: the production code decides from the readings."""
    try:
        from catalog_match.verify import make_verdict
        return make_verdict
    except ImportError:
        return None


class CassetteVerifier:
    """models.Verifier answering from vlm_cassette.json; the 'gemini_down' scenario returns unknown.

    Any other scenario ('normal', 'vlm_noisy') answers from the cassette it is given: the
    harness overlays the recorded misreads of fixtures/vlm_noisy.json for 'vlm_noisy'.

    The recorded readings are turned into a verdict by catalog_match.verify.make_verdict (the
    same code that decides for a live Gemini reply), so the replay measures the real D6 rules;
    decide_verdict() above is only the fallback before verify.py exists.
    """

    FIELDS = ("brand_text", "variant_text", "size_text", "pack_count", "view", "brand_match", "variant_match",
              "size_match")

    def __init__(self, models: Any, sku: Mapping[str, Any], cassette: Mapping[str, Any], scenario: str = "normal"):
        self.models, self.sku, self.cassette, self.scenario = models, sku, cassette, scenario
        self.index = UrlIndex(sku)
        self.calls = 0
        self.max_images = 0
        self.missing = 0                 # images asked about that the cassette holds no reading of

    def verify(self, spec: Any, images: List[Any]) -> Any:
        self.calls += 1
        self.max_images = max(self.max_images, len(images))
        m = self.models
        if self.scenario == "gemini_down":
            return m.VerificationResult(status="unknown", calls=1, error="gemini_down",
                                        verdicts=[m.VlmImageVerdict(index=i, decision="UNKNOWN")
                                                  for i in range(len(images))])
        verdicts = []
        # The real rules need a SkuSpec; protocol checks that pass spec=None use decide_verdict().
        make_verdict = _real_make_verdict() if isinstance(spec, m.SkuSpec) else None
        # A recorded set (eval_record --from-db) says what a candidate nobody read live gets: "missing": "UNKNOWN"
        # (never accepted). The committed cassettes say nothing: such a candidate is UNSURE, as it always was.
        missing = str(self.cassette.get("missing") or "UNSURE")
        for i, fetched in enumerate(images):
            cid = self.index.cid(getattr(getattr(fetched, "candidate", None), "image_url", None))
            entry = cassette_entry(self.cassette, self.sku["id"], cid) if cid else None
            if entry is None:
                self.missing += 1
                verdicts.append(m.VlmImageVerdict(index=i, decision=missing))
                continue
            if not any(k in entry for k in self.FIELDS) and entry.get("recorded_decision"):
                # only the decision was recorded (a reviewed image the database no longer keeps the reading of)
                verdicts.append(m.VlmImageVerdict(index=i, decision=str(entry["recorded_decision"])))
                continue
            if make_verdict is not None:
                verdicts.append(make_verdict(spec, i, entry))
                continue
            fields = {k: entry.get(k) for k in self.FIELDS if k in entry}
            fields = {k: ("" if v is None and k.endswith("_text") else v) for k, v in fields.items()}
            verdicts.append(m.VlmImageVerdict(index=i, decision=decide_verdict(entry, spec), **fields))
        return m.VerificationResult(status="ok", verdicts=verdicts, calls=1)


def v2_kill_rules(rc: Any) -> List[str]:
    """Why a ranked v2 candidate was rejected, as rule names (quality reasons prefixed 'quality:')."""
    rules: List[str] = []
    score = getattr(rc, "score", None)
    for rule in getattr(score, "hard_reject", ()) or ():
        rules.append(str(rule))
    fetched = getattr(rc, "fetched", None)
    if fetched is not None and not getattr(fetched, "ok", True):
        rules.append("fetch:" + str(getattr(fetched, "error", None) or "failed"))
    quality = getattr(rc, "quality", None)
    if quality is not None and not getattr(quality, "hard_ok", True):
        reasons = list(getattr(quality, "hard_reasons", []) or []) or ["hard_fail"]
        rules.extend("quality:" + str(r) for r in reasons)
    verdict = getattr(rc, "verdict", None)
    if verdict is not None and getattr(verdict, "decision", "") == "MISMATCH":
        rules.append("vlm:MISMATCH")
    if getattr(rc, "status", "") == "excluded" and not rules:
        rules.append("excluded")
    return rules


@contextlib.contextmanager
def _v2_settings(auto_publish: bool) -> Iterator[None]:
    """Enable auto-publish for every brand, so the eval measures what AUTO_PUBLISH would publish."""
    values = {"AUTO_PUBLISH_ENABLED": auto_publish, "AUTO_PUBLISH_BRANDS": ["*"] if auto_publish else [],
              "CANDIDATE_STORE_DIR": os.path.join(tempfile.gettempdir(), "catalog_match_eval_candidates")}
    env = {k: (",".join(v) if isinstance(v, list) else str(v).lower() if isinstance(v, bool) else str(v))
           for k, v in values.items()}
    with contextlib.ExitStack() as stack:
        stack.enter_context(mock.patch.dict(os.environ, env))
        try:
            from catalog_match import settings
            cfg = getattr(settings, "_config", None)
        except Exception:  # pragma: no cover - settings is part of the shared contract
            cfg = None
        if cfg is not None:
            for key, value in values.items():
                stack.enter_context(mock.patch.object(cfg, key, value, create=True))
        yield


def _call_with_supported(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Call fn with only the keyword arguments its signature accepts."""
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):  # pragma: no cover
        return fn(*args, **kwargs)
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return fn(*args, **kwargs)
    return fn(*args, **{k: v for k, v in kwargs.items() if k in params})


def build_providers(models: Any, sku: Mapping[str, Any], provider_set: str = "serper") -> List[Any]:
    """The fixture providers for one SKU, shaped like catalog_match.providers.default_providers().

    "serper":    Serper (primary) + Open Food Facts + the legacy CSE adapter when the SKU has
                 cse_legacy candidates + Bing HTML as fallback-only (it runs only when every
                 sanctioned provider is down), i.e. a SERPER_API_KEY with ENABLE_BING_HTML_FALLBACK.
    "bing_only": Open Food Facts + Bing HTML as the sole search source (no sanctioned key, the
                 owner's setup per D7); Bing answers with the Bing and Serper listings of the
                 fixture, re-shaped as unsanctioned Bing results.
    A candidate from a provider the replay does not know fails loudly instead of vanishing. Candidates of a sources
    overlay (source_only, tests/eval/sources.py) are served by the expansion and local-index fakes, never here.
    """
    names = {c.get("provider") for c in sku.get("candidates", []) if not c.get("source_only")}
    unknown = sorted(str(n) for n in names - set(SEARCH_PROVIDERS) - {LOOKUP_PROVIDER} - set(RECORDED_LOOKUPS))
    if unknown:
        raise ValueError(f"{sku.get('id')}: candidates from unknown provider(s) {unknown}; "
                         f"the replay knows {sorted(SEARCH_PROVIDERS)}, {LOOKUP_PROVIDER!r} and the recorded "
                         f"lookups {list(RECORDED_LOOKUPS)}")
    off = FixtureOffProvider(models, sku)
    # a recorded set's own page reads / index rows (eval_record --from-db): lookups, only when the set has them
    recorded = [FixtureLookupProvider(models, sku, n) for n in RECORDED_LOOKUPS if n in names]
    if provider_set == "serper":
        providers: List[Any] = [FixtureProvider(models, sku, "serper", True), off]
        if "cse_legacy" in names:
            providers.append(FixtureProvider(models, sku, "cse_legacy", True))
        providers.append(FixtureProvider(models, sku, "bing_html", False, fallback=True))
        return providers + recorded
    if provider_set == "bing_only":
        return [off, FixtureProvider(models, sku, "bing_html", False,
                                     serve=("bing_html",) + tuple(n for n in SEARCH_PROVIDERS if n != "bing_html"))
                ] + recorded
    raise ValueError(f"provider_set must be one of {PROVIDER_SETS}, not {provider_set!r}")


def run_v2(sku: Mapping[str, Any], cassette: Mapping[str, Any], scenario: str = "normal",
           mappings: Optional[Dict[str, Any]] = None, auto_publish: bool = True,
           provider_set: str = "serper", sources: Optional[Mapping[str, Any]] = None,
           verifier: Any = None) -> Outcome:
    """Run catalog_match for one fixture SKU with fixture providers, fetcher and cassette verifier.

    sources ({"expansion": bool, "index": bool}, tests/eval/sources.py): the expansion round with P0 page reads
    and / or the local catalog index, through injected fakes; None or both False is the default replay (neither
    runs, exactly as before). verifier: a verifier to use instead of the cassette (compare_verifiers, a live
    reader); its calls / usage are read from the outcome.
    """
    pipeline, identity, models = _v2_modules()
    mappings = mappings if mappings is not None else load_mappings()
    index = UrlIndex(sku)
    providers = build_providers(models, sku, provider_set)
    search_providers = [p for p in providers if getattr(p, "kind", "search") == "search"]
    fetcher = FixtureFetcher(models, sku)
    src = None
    if sources and (sources.get("expansion") or sources.get("index")):
        import sources as sources_mod
        src = sources_mod.Sources(models, sku, expansion=bool(sources.get("expansion")),
                                  index=bool(sources.get("index")))
        providers = providers + src.providers()
    replay_verifier = CassetteVerifier(models, sku, cassette, scenario)
    verifier = verifier if verifier is not None else replay_verifier
    brand_index = None
    try:
        from catalog_match.brand_index import BrandIndex
        brand_index = BrandIndex.from_mappings(mappings)
    except Exception:
        log.debug("BrandIndex unavailable; the pipeline builds its own", exc_info=True)

    t0 = time.perf_counter()
    outcome: Any = None
    error: Optional[str] = None
    with _v2_settings(auto_publish):
        try:
            spec = _call_with_supported(identity.build_sku_spec, sku_row(sku), mappings)
            outcome = _call_with_supported(pipeline.find_product_image, spec, providers=providers,
                                           fetcher=fetcher, verifier=verifier, brand_index=brand_index,
                                           **(src.kwargs() if src is not None else {}))
        except Exception as exc:
            log.exception("v2 pipeline crashed for %s", sku["id"])
            error = f"{type(exc).__name__}: {exc}"
    seconds = time.perf_counter() - t0

    provider_calls = {p.name: len(p.calls) for p in search_providers}
    verifier_calls = int(getattr(verifier, "calls", 0) or 0)
    if error is not None or outcome is None:
        return Outcome(sku_id=sku["id"], engine="v2", decision=metrics.ERROR, error=error, status="error",
                       provider_calls=provider_calls, vlm_calls=verifier_calls, seconds=round(seconds, 3))

    ranked = list(getattr(outcome, "ranked", []) or [])
    decision = str(outcome.decision)
    chosen = None
    if decision in (metrics.AUTO, metrics.PRESELECTED) and outcome.winner is not None:
        chosen = outcome.winner
    preselected = [rc for rc in ranked if getattr(rc, "status", "") == "preselected"]
    if chosen is None and preselected:
        chosen = preselected[0]
    chosen_url = chosen.candidate.image_url if chosen is not None else None
    chosen_id = index.cid(chosen_url)

    pool: List[str] = []
    kills: Dict[str, List[str]] = {}
    for rc in ranked:
        cid = index.cid(rc.candidate.image_url)
        if cid is None:
            continue
        if cid not in pool:
            pool.append(cid)
        rejected = getattr(rc, "status", "") in ("rejected", "excluded") or getattr(rc.score, "tier", 1) is None
        if rejected and cid != chosen_id:
            rules = v2_kill_rules(rc)
            if rules:
                kills.setdefault(cid, []).extend(r for r in rules if r not in kills.get(cid, []))
    auto = decision == metrics.AUTO and chosen is not None
    used: Dict[str, Any] = {"missing_readings": replay_verifier.missing} if replay_verifier.missing else {}
    if verifier is not replay_verifier:
        used = {"usage": [dict(u) for u in getattr(outcome, "vlm_usage", None) or [] if isinstance(u, dict)],
                "notices": list(getattr(outcome, "verifier_notices", None) or []),
                "stats": dict(getattr(verifier, "stats", None) or {})}
    return Outcome(
        sku_id=sku["id"], engine="v2", decision=decision, chosen_id=chosen_id, chosen_url=chosen_url,
        needs_review=not auto, auto=auto, status=str(outcome.failure_code or ""),
        failure_code=outcome.failure_code, pool=pool, kills=kills, queries=list(outcome.queries or []),
        provider_calls=provider_calls, vlm_calls=max(int(outcome.vlm_calls or 0), verifier_calls),
        vlm_images_max=int(getattr(verifier, "max_images", 0) or 0), n_preselected=len(preselected), error=None,
        seconds=round(seconds, 3), lane=pick_lane(chosen),
        sources=src.account(outcome) if src is not None else {}, verifier=used)


def pick_lane(chosen: Any) -> Optional[str]:
    """The lane the engine gave its pick (catalog_match.decide.lane_of on the pick's reasons), None without a pick."""
    if chosen is None:
        return None
    from catalog_match.decide import lane_of
    return lane_of(getattr(chosen, "reasons", None) or [])
