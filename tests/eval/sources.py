"""Injected fakes for the costly sources: the expansion round (X0-X5), P0 page reads and the local catalog index.

The default replay never runs them (catalog_match.pipeline turns the round and P0 off when providers or a verifier
are injected, and the replay builds no local index), so the existing scenarios stay exactly what they were. With
eval_report --expansion / --local-index the replay passes these fakes to find_product_image(expansion=, pages=) and
adds a LocalIndexProvider over a MemoryCatalogStore, so their effect, their paid calls and their time are measured.

What they serve comes from a sources overlay next to the set (fixtures/sources_golden.json for the committed set,
fixtures/realistic/sources.json): per SKU more candidates, each tagged with the source that finds it:

    provider "serper_web"       X1 / X5: a web result (its page_url and title) whose page's main image is the
                                candidate's image_url; served when the query's site: list holds its domain
    provider "serper_shopping"  X2: a shopping listing with a direct image
    provider "lens_serper"      X3 / X4: a visual-search match of any seed (surfaced_by may name "X3" / "X4")
    provider "page"             a store page's own main image, read by P0 or X0 for a listing of the normal flow
                                with the same page_url (the page reader serves it)
    provider "local_index"      a row of the local catalog index (its page_url), whose page's main image this is

Labels and readings work as for any candidate (the overlay's "readings"); apply_overlay() merges the candidates and
readings into a copy of the set, so labels, pool recall and kills see them. A page the overlay does not know answers
404, so P0 reads of the normal flow's listings cost a page read and add nothing.

Cost and time: every paid call the round makes is in the outcome's provider_health (query ids X1..X5 / XU), every page
read is counted by the fake reader, and the index lookups by the provider. account() turns them into USD (the prices
of scripts/smoke_live.py: Serper about $0.001 a call, SerpApi Lens more) and an estimate of the live seconds they add
(LIVE_CALL_S: the median latency of each source in the run exports of 2026-10-04/05; page reads PAGE_READ_S), next to
the replay's own wall time per stage (outcome.timings).
"""

from __future__ import annotations

import functools
import importlib.util
import json
import re
import threading
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

EVAL_DIR = Path(__file__).resolve().parent
REPO_ROOT = EVAL_DIR.parent.parent
FIXTURES = EVAL_DIR / "fixtures"
GOLDEN_OVERLAY = FIXTURES / "sources_golden.json"

SOURCE_PROVIDERS = frozenset({"serper_web", "serper_shopping", "lens_serper", "lens_serpapi", "page", "local_index"})
EXPANSION_QUERY_IDS = ("X0", "X1", "X2", "X3", "X4", "X5", "XU")
# Median latency of one answered call in the live run exports of 2026-10-04 / 2026-10-05 (provider_calls[].ms):
# serper_web 2.1 s (78 calls), serper_shopping 1.6 s (53), lens_serper 2.9 s (43), serper 2.3 s (312).
LIVE_CALL_S = {"serper_web": 2.1, "serper_shopping": 1.6, "lens_serper": 2.9, "lens_serpapi": 4.0, "serper": 2.3}
PAGE_READ_S = 1.5              # one store page read (estimate; the index and P0 read pages in parallel)
VERIFY_CALL_S = 5.0            # one label-reader call (estimate)
DEFAULT_SERP_COST = 0.001
DEFAULT_SERPAPI_COST = 0.015
DEFAULT_VLM_COST = 0.001       # one label-reader call (smoke_live.DEFAULT_VLM_COST)


# ---------------------------------------------------------------------------
# The overlay
# ---------------------------------------------------------------------------

def overlay_path(golden_path: Optional[Path]) -> Optional[Path]:
    """The sources overlay of a set: sources.json next to it, or sources_golden.json for the committed set."""
    golden_path = Path(golden_path) if golden_path else FIXTURES / "golden_skus.json"
    if golden_path.resolve() == (FIXTURES / "golden_skus.json").resolve():
        return GOLDEN_OVERLAY if GOLDEN_OVERLAY.exists() else None
    near = golden_path.parent / "sources.json"
    return near if near.exists() else None


def load_overlay(path: Optional[Path]) -> Dict[str, Any]:
    if path is None:
        return {"skus": {}, "readings": {}}
    with open(path, "r", encoding="utf-8") as fh:
        doc = json.load(fh)
    doc.setdefault("skus", {})
    doc.setdefault("readings", {})
    return doc


def apply_overlay(golden: Mapping[str, Any], cassette: Mapping[str, Any], overlay: Mapping[str, Any]
                  ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """(set, cassette) with the overlay's candidates and readings merged in; the inputs are not changed."""
    out = json.loads(json.dumps(golden))
    cas = json.loads(json.dumps(cassette))
    by_id = {s["id"]: s for s in out["skus"]}
    for sku_id, extra in (overlay.get("skus") or {}).items():
        sku = by_id.get(sku_id)
        if sku is None:
            raise ValueError(f"sources overlay: unknown SKU {sku_id!r}")
        known = {c["id"] for c in sku["candidates"]}
        for cand in extra.get("candidates", []):
            if cand["id"] in known:
                raise ValueError(f"sources overlay: {sku_id}/{cand['id']} is already a candidate")
            if cand.get("provider") not in SOURCE_PROVIDERS:
                raise ValueError(f"sources overlay: {sku_id}/{cand['id']} provider {cand.get('provider')!r} is not a "
                                 f"source ({sorted(SOURCE_PROVIDERS)})")
            sku["candidates"].append(dict(cand, source_only=True))
        sku["no_correct_candidate"] = not any(c["label"] == "correct_exact" for c in sku["candidates"])
        if extra.get("dead_pages"):
            sku["dead_pages"] = list(extra["dead_pages"])
        for sid, per in (overlay.get("readings") or {}).items():
            if sid == sku_id:
                cas.setdefault("verdicts", {}).setdefault(sid, {}).update(json.loads(json.dumps(per)))
    for sid in overlay.get("readings") or {}:
        if sid not in by_id:
            raise ValueError(f"sources overlay: readings for unknown SKU {sid!r}")
    return out, cas


def source_candidates(sku: Mapping[str, Any], provider: str) -> List[Mapping[str, Any]]:
    return [c for c in sku.get("candidates", []) if c.get("source_only") and c.get("provider") == provider]


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

def _sites(query: str) -> List[str]:
    return [d.lower().lstrip("*.").replace("www.", "") for d in re.findall(r"site:([^\s()]+)", query or "", flags=re.I)]


def _host(url: str) -> str:
    m = re.match(r"^[a-z]+://([^/]+)", url or "", flags=re.I)
    return (m.group(1).lower() if m else "").replace("www.", "")


def _on_sites(cand: Mapping[str, Any], sites: Sequence[str]) -> bool:
    hosts = {cand.get("domain", ""), _host(cand.get("page_url", ""))}
    return any(h == s or h.endswith("." + s) for h in hosts if h for s in sites)


class FakePages:
    """pages.PageFetcher stand-in: fetch_page(url) -> PageInfo with the overlay image(s) of that page, else a 404.

    A page's main image is every overlay candidate of the SKU (provider page / serper_web / local_index) with that
    page_url, best rank first; the page's name is the candidate's page_title. Counts every read.
    """

    def __init__(self, sku: Mapping[str, Any]):
        from catalog_match.pages import PageImage, PageInfo
        from catalog_match.retrieve import norm_image_url

        self._PageImage, self._PageInfo, self._norm = PageImage, PageInfo, norm_image_url
        self.pages: Dict[str, List[Mapping[str, Any]]] = {}
        for cand in sku.get("candidates", []):
            if cand.get("source_only") and cand.get("provider") in ("page", "serper_web", "local_index"):
                self.pages.setdefault(self._norm(cand["page_url"]), []).append(cand)
        self.dead = {self._norm(u) for u in sku.get("dead_pages", [])}
        self.reads: List[str] = []
        self.by_kind: Dict[str, int] = {"p0": 0, "index": 0, "expansion": 0}
        self._lock = threading.Lock()

    def fetch_page(self, url: str, referer: Optional[str] = None) -> Any:
        """The expansion round reads fetch_page(url, referer); the local index reads fetch_page(url)."""
        return self._read(url, "index" if referer is None else "expansion")

    def fetch_page_now(self, url: str, referer: str = "") -> Any:
        """P0 (expand.PageMainImages) reads through fetch_page_now when the reader has it."""
        return self._read(url, "p0")

    def _read(self, url: str, kind: str) -> Any:
        with self._lock:
            self.reads.append(url)
            self.by_kind[kind] = self.by_kind.get(kind, 0) + 1
        key = self._norm(url)
        cands = sorted(self.pages.get(key, []), key=lambda c: (int(c.get("rank", 0)), c["id"]))
        if not cands or key in self.dead:
            return self._PageInfo(url=url, ok=False, error="http_404")
        first = cands[0]
        images = [self._PageImage(url=c["image_url"], width=c.get("width"), height=c.get("height"), source="jsonld")
                  for c in cands]
        return self._PageInfo(url=url, ok=True, name=first.get("page_title") or first.get("title") or "",
                              html_title=first.get("page_title") or "", images=images,
                              gtins=[g for g in [first.get("gtin_on_page")] if g], sources=["jsonld"])


class FakeSearch:
    """serper_web / serper_shopping stand-in: .search(query, hl, spec) -> ProviderResult of the overlay's hits."""

    def __init__(self, models: Any, sku: Mapping[str, Any], name: str):
        self.models, self.sku, self.name = models, sku, name
        self.sanctioned = True
        self.calls: List[str] = []

    def search(self, query: str, hl: str, spec: Any) -> Any:
        self.calls.append(query)
        sites = _sites(query)
        out = []
        for c in source_candidates(self.sku, self.name):
            if sites and not _on_sites(c, sites):
                continue
            web = self.name == "serper_web"
            out.append(self.models.Candidate(
                image_url="" if web else c["image_url"], page_url=c.get("page_url", ""), page_title="",
                title=c.get("title", ""), snippet=c.get("snippet", ""), domain=c.get("domain", ""),
                width=None if web else c.get("width"), height=None if web else c.get("height"),
                provider=self.name, query_id="", rank=int(c.get("rank", 0)), gtin_on_page=None, sanctioned=True))
        out.sort(key=lambda c: c.rank)
        return self.models.ProviderResult(provider=self.name, status="ok" if out else "empty", http_status=200,
                                          latency_ms=0, candidates=out)


class FakeVisual:
    """The visual search stand-in (a test double with its own search(), as expand._visual expects)."""

    name = "lens_serper"

    def __init__(self, models: Any, sku: Mapping[str, Any]):
        self.models, self.sku = models, sku
        self.calls: List[Tuple[str, str]] = []

    def available(self) -> bool:
        return True

    def search(self, image_url: str, spec: Any, query_id: str, max_calls: int) -> List[Any]:
        self.calls.append((image_url, query_id))
        out = []
        for c in source_candidates(self.sku, "lens_serper"):
            tags = set(c.get("surfaced_by") or [])
            if tags & {"X3", "X4"} and query_id not in tags:
                continue
            out.append(self.models.Candidate(
                image_url=c["image_url"], page_url=c.get("page_url", ""), page_title="", title=c.get("title", ""),
                snippet="", domain=c.get("domain", ""), width=c.get("width"), height=c.get("height"),
                provider="lens_serper", query_id=query_id, rank=int(c.get("rank", 0)), gtin_on_page=None,
                sanctioned=True))
        return [self.models.ProviderResult(provider="lens_serper", status="ok" if out else "empty", http_status=200,
                                           latency_ms=0, candidates=out)]


def local_index_provider(sku: Mapping[str, Any], pages: FakePages) -> Any:
    """A LocalIndexProvider over a MemoryCatalogStore holding the overlay's index rows (their pages unread)."""
    from catalog_match import local_index

    local_index.reset_blocked_hosts()
    store = local_index.MemoryCatalogStore()
    by_store: Dict[str, List[Tuple[str, Optional[str]]]] = {}
    for c in source_candidates(sku, "local_index"):
        by_store.setdefault(c.get("domain") or _host(c["page_url"]), []).append((c["page_url"], None))
    for name, rows in by_store.items():
        store.begin_harvest(name)
        store.upsert(name, rows)
    return local_index.LocalIndexProvider(store=store, fetcher=pages)


class Sources:
    """The fakes of one SKU's run: what to pass to find_product_image, and what they did afterwards."""

    def __init__(self, models: Any, sku: Mapping[str, Any], expansion: bool = False, index: bool = False):
        self.sku = sku
        self.pages = FakePages(sku)
        self.expansion_on, self.index_on = bool(expansion), bool(index)
        self.web = FakeSearch(models, sku, "serper_web")
        self.shopping = FakeSearch(models, sku, "serper_shopping")
        self.visual = FakeVisual(models, sku)
        self.index = local_index_provider(sku, self.pages) if index else None
        self.index_lookups = 0
        if self.index is not None:
            # a lookup asked again (retrieve.rerun_lookups) replaces its health entry: count the calls themselves
            lookup = self.index.lookup

            def counted(spec: Any) -> Any:
                self.index_lookups += 1
                return lookup(spec)

            self.index.lookup = counted

    def kwargs(self) -> Dict[str, Any]:
        """find_product_image() keywords: the round and P0 with the fakes, or both off as in the default replay."""
        if not self.expansion_on:
            return {}
        from catalog_match import expand, settings

        exp = expand.Expansion(web=self.web, shopping=self.shopping, visual=self.visual, pages=self.pages,
                               max_calls=settings.expansion_max_calls())
        return {"expansion": exp if settings.expansion_enabled() else False, "pages": self.pages}

    def providers(self) -> List[Any]:
        return [self.index] if self.index is not None else []

    def account(self, outcome: Any, prices: Optional[Mapping[str, float]] = None) -> Dict[str, Any]:
        """What the sources did for this SKU: paid calls per source, page reads, cost and the live time they add."""
        prices = dict(prices or provider_prices())
        paid: Dict[str, int] = {}
        usd = 0.0
        for h in getattr(outcome, "provider_health", None) or []:
            qid = str(getattr(h, "query_id", "") or "")
            provider = str(getattr(h, "provider", "") or "")
            if qid not in EXPANSION_QUERY_IDS and provider not in ("serper_web", "serper_shopping", "lens_serper",
                                                                   "lens_serpapi"):
                continue
            if getattr(h, "status", "") not in ("ok", "empty"):
                continue
            paid[provider] = paid.get(provider, 0) + 1
            usd += float(prices.get(provider, prices.get("_default", DEFAULT_SERP_COST)))
        index_lookups = self.index_lookups
        reads = dict(self.pages.by_kind)
        # Live time the sources add: every paid call at its median latency (X1 and X2 run in parallel, so this is an
        # upper bound), one wave of page reads for the round and one for the index (each reads its pages in
        # parallel); P0 reads while the listings' pictures download and waits at most PAGE_MAIN_IMAGES_WAIT_S.
        live_s = (sum(LIVE_CALL_S.get(p, 2.0) * n for p, n in paid.items())
                  + PAGE_READ_S * ((reads.get("expansion", 0) > 0) + (reads.get("index", 0) > 0)))
        timings = dict(getattr(outcome, "timings", None) or {})
        return {"expansion_on": self.expansion_on, "index_on": self.index_on, "paid_calls": paid,
                "n_paid_calls": sum(paid.values()), "usd": round(usd, 4), "page_reads": sum(reads.values()),
                "page_reads_by": reads, "index_lookups": index_lookups, "est_live_s": round(live_s, 1),
                "replay_ms": {k: v for k, v in timings.items() if k in ("expansion", "page_images", "retrieval",
                                                                         "total")}}


@functools.lru_cache(maxsize=1)
def _smoke_live() -> Any:
    """scripts/smoke_live.py loaded by path (scripts/ never goes on sys.path: it would shadow repository modules)."""
    path = REPO_ROOT / "scripts" / "smoke_live.py"
    spec = importlib.util.spec_from_file_location("_eval_smoke_live", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def provider_prices() -> Dict[str, float]:
    """USD per answered call per provider (smoke_live.provider_prices, the dashboard's figures)."""
    try:
        return dict(_smoke_live().provider_prices())
    except Exception:            # pragma: no cover - smoke_live is part of the repository
        return {"serper_web": DEFAULT_SERP_COST, "serper_shopping": DEFAULT_SERP_COST,
                "lens_serper": DEFAULT_SERP_COST, "lens_serpapi": DEFAULT_SERPAPI_COST, "_default": DEFAULT_SERP_COST}


def summarize(outcomes: Iterable[Mapping[str, Any]]) -> Dict[str, Any]:
    """Totals over a run's outcomes[].sources (empty when the sources were off)."""
    rows = [o.get("sources") or {} for o in outcomes]
    rows = [r for r in rows if r]
    if not rows:
        return {}
    paid: Dict[str, int] = {}
    for r in rows:
        for p, n in (r.get("paid_calls") or {}).items():
            paid[p] = paid.get(p, 0) + int(n)
    n = len(rows)
    by_kind: Dict[str, int] = {}
    for r in rows:
        for kind, count in (r.get("page_reads_by") or {}).items():
            by_kind[kind] = by_kind.get(kind, 0) + int(count)
    return {
        "skus": n, "paid_calls": dict(sorted(paid.items())), "n_paid_calls": sum(paid.values()),
        "skus_with_paid_calls": sum(1 for r in rows if r.get("n_paid_calls")),
        "usd": round(sum(float(r.get("usd") or 0.0) for r in rows), 4),
        "usd_per_100": round(100.0 * sum(float(r.get("usd") or 0.0) for r in rows) / n, 3),
        "page_reads": sum(int(r.get("page_reads") or 0) for r in rows), "page_reads_by": by_kind,
        "index_lookups": sum(int(r.get("index_lookups") or 0) for r in rows),
        "est_live_s": round(sum(float(r.get("est_live_s") or 0.0) for r in rows), 1),
        "replay_expansion_ms": sum(int((r.get("replay_ms") or {}).get("expansion") or 0) for r in rows),
    }
