"""Pooled retrieval (decisions D3, D7, D8): every query's candidates go into ONE pool.

retrieve(spec, providers, custom_query=None, exclude_urls=(), max_queries=4,
         early_stop=None, relax_when=None) -> RetrievalResult

* The lookup providers run once, in parallel with Q1: the GTIN lookups (Open Food
  Facts) only for a valid GTIN, the local catalog index (needs_gtin False) always;
  then the next planned queries run in order. Nothing wins by coming first: the
  caller scores and ranks the whole pool. Retriever.rerun_lookups() asks the
  brand-word lookups (the local index) once more after brand discovery changed the spec.
* early_stop(pool) -> bool is checked after each step (callers pass a T1 test built
  on score.py, see t1_early_stop()).
* At most max_queries planned queries are sent (relaxations share that budget). A
  query no provider may serve (Q3 is Serper-only) is skipped and does not count.
* Per query, the primary search providers run in parallel. Fallback providers
  (Bing HTML when a sanctioned API is configured) run only when every primary
  provider returned error/quota/blocked. A provider that answered quota, blocked or
  401/403 is not asked again during this SKU.
* A provider that raises is isolated: its health is 'error' and the other
  providers' candidates remain.
* Candidates are deduplicated on norm_image_url(); duplicates merge their page
  evidence and raise consensus_count (one count per distinct page site: the same store
  page found by two providers, or a hit without a page, is not a second source).
  An entry a page we read ourselves touched (the local catalog index, the expansion
  round's page reads: PAGE_READ_PROVIDERS) carries scraped evidence: it is never
  sanctioned, even when a search API also returns the image, so it never auto-publishes;
  an entry the index created keeps its provider, so it never stops the web search early.
* exclude_urls (reviewer negatives) are dropped, compared as normalised URLs. The
  pHash half of the negative filter runs after download (fetch stage).
* Relaxations R1/R2 run only when the caller asks: relax_when(pool) is True, or
  Retriever.relax() is called (the pipeline does this when the scored pool has no
  T1/T2 candidate). Never with a custom query.
"""

from __future__ import annotations

import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit

from .gtin import is_global_gtin
from .models import Candidate, PlannedQuery, ProviderResult, RetrievalResult, SkuSpec
from .query_plan import build_queries, relaxations

logger = logging.getLogger(__name__)

STATUSES = ("ok", "empty", "error", "quota", "blocked")
DOWN_STATUSES = ("error", "quota", "blocked")
LOOKUP_QUERY_ID = "OFF"

# Query parameters that only change the rendition of an image, never which image it is.
RESIZE_PARAMS = frozenset({"w", "h", "width", "height", "resize", "fit", "q",
                           "quality", "format", "fm", "auto", "dpr"})
_IMAGE_PATH_RE = re.compile(r"\.(?:jpe?g|png|webp|gif|avif|bmp|tiff?|heic)$", re.I)

EarlyStop = Callable[[List[Candidate]], bool]


# ---------------------------------------------------------------------------
# URL normalisation
# ---------------------------------------------------------------------------

def norm_image_url(url: Optional[str]) -> str:
    """Dedupe key of an image URL: lower-cased host (no www) + path, rendition params stripped.

    The query string is dropped for a path that ends in an image extension (it only
    carries renditions or cache busters). For an extension-less endpoint such as
    '/_next/image?url=...' or '/image?id=...' the query identifies the image, so the
    non-rendition parameters are kept, sorted.
    """
    if not url:
        return ""
    s = str(url).strip()
    if "://" not in s and not s.startswith("//"):
        s = "//" + s
    try:
        parts = urlsplit(s)
        host = (parts.hostname or "").lower().rstrip(".")
    except ValueError:
        return str(url).strip().lower()
    if host.startswith("www."):
        host = host[4:]
    path = unquote(parts.path or "").rstrip("/").lower()
    kept = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k.lower() not in RESIZE_PARAMS]
    query = "" if (not kept or _IMAGE_PATH_RE.search(path)) else urlencode(sorted(kept))
    return host + path + (("?" + query) if query else "")


def _has_rendition_params(url: str) -> bool:
    try:
        return any(k.lower() in RESIZE_PARAMS for k, _ in parse_qsl(urlsplit(url).query))
    except ValueError:
        return False


def _page_key(page_url: str) -> str:
    return norm_image_url(page_url) if page_url else ""


def _page_site(page_url: str) -> str:
    """The site of the page an image was found on ('' without a page): consensus counts sites, not providers."""
    from .text_norm import url_host
    return url_host(page_url) if page_url else ""


# ---------------------------------------------------------------------------
# Pool
# ---------------------------------------------------------------------------

@dataclass
class _Entry:
    cand: Candidate
    sources: Set[str] = field(default_factory=set)    # the page sites ('' = a hit without a page)
    page_read: bool = False         # a page we read ourselves gave evidence to this entry


# Providers whose candidates come from a page we read ourselves (scraped evidence, catalog_match.pages).
PAGE_READ_PROVIDERS = frozenset({"local_index", "page"})


def _page_read(cand: Candidate) -> bool:
    return (cand.provider or "").lower() in PAGE_READ_PROVIDERS


class CandidatePool:
    """Ordered, deduplicated candidate pool with evidence merging."""

    def __init__(self, spec: SkuSpec, exclude_urls: Iterable[str] = ()) -> None:
        self.spec = spec
        self.exclude = {norm_image_url(u) for u in (exclude_urls or ()) if u}
        self.exclude.discard("")
        self._entries: Dict[str, _Entry] = {}
        self.excluded = 0

    def __len__(self) -> int:
        return len(self._entries)

    def candidates(self) -> List[Candidate]:
        return [e.cand for e in self._entries.values()]

    def entries(self) -> List[Tuple[str, Candidate]]:
        """(dedupe key, representative candidate) pairs in pool order (the expansion round's view)."""
        return [(key, e.cand) for key, e in self._entries.items()]

    def is_excluded(self, image_url: str) -> bool:
        """True when the image is a reviewer negative (compared as a normalised URL)."""
        return norm_image_url(image_url) in self.exclude

    def unsanction(self, key: str) -> None:
        """The entry now carries evidence that may never auto-publish (a fetched page): sanctioned False."""
        entry = self._entries.get(key)
        if entry is not None and entry.cand.sanctioned:
            entry.cand = replace(entry.cand, sanctioned=False)

    def add(self, cand: Candidate, relaxed: bool = False) -> bool:
        """Add or merge one candidate; returns True when it created a new pool entry."""
        key = norm_image_url(cand.image_url)
        if not key:
            return False
        if key in self.exclude:
            self.excluded += 1
            return False
        source = _page_site(cand.page_url)
        entry = self._entries.get(key)
        if entry is None:
            page_read = _page_read(cand)
            self._entries[key] = _Entry(replace(cand, consensus_count=1, sanctioned=cand.sanctioned and not page_read),
                                        {source}, page_read)
            return True
        entry.sources.add(source)
        entry.page_read = entry.page_read or _page_read(cand)
        merged = self._merge(entry.cand, cand, relaxed, entry.page_read)
        entry.cand = replace(merged, consensus_count=max(1, len(entry.sources - {""})))
        return False

    def _trust(self, cand: Candidate) -> int:
        from .score import source_trust  # local import: score is heavier and optional here
        try:
            return source_trust(self.spec, cand)[0]
        except Exception:  # pragma: no cover - defensive
            return 0

    def _evidence_rank(self, cand: Candidate) -> Tuple[int, int, int]:
        return (self._trust(cand), int(bool(cand.page_url)), int(bool(cand.page_title or cand.title)))

    def _merge(self, rep: Candidate, new: Candidate, new_relaxed: bool, page_read: bool = False) -> Candidate:
        changes: Dict[str, object] = {}
        # Page evidence travels as one block (a title belongs to its page).
        if (new.page_url or new.page_title or new.title) and self._evidence_rank(new) > self._evidence_rank(rep):
            changes.update(page_url=new.page_url, page_title=new.page_title, title=new.title,
                           snippet=new.snippet or rep.snippet, domain=new.domain or rep.domain)
        elif not rep.page_url or _page_key(rep.page_url) == _page_key(new.page_url):
            for name in ("page_url", "page_title", "title", "snippet", "domain"):
                if not getattr(rep, name) and getattr(new, name):
                    changes[name] = getattr(new, name)
        if not rep.gtin_on_page and new.gtin_on_page:
            changes["gtin_on_page"] = new.gtin_on_page
        # Prefer the full-size rendition of the image, with its own dimensions.
        if _has_rendition_params(rep.image_url) and not _has_rendition_params(new.image_url):
            changes["image_url"] = new.image_url
            if new.width and new.height:
                changes.update(width=new.width, height=new.height)
        if rep.width is None and rep.height is None and new.width and new.height and "width" not in changes:
            changes.update(width=new.width, height=new.height)
        # Found by a sanctioned source in a non-relaxed query: the image is sanctioned, unless a page we read
        # ourselves gave the entry evidence (scraped text never makes an image auto-publishable).
        if page_read:
            if rep.sanctioned:
                changes.update(sanctioned=False)
        elif new.sanctioned and not rep.sanctioned and not new_relaxed:
            changes.update(sanctioned=True, provider=new.provider, rank=new.rank)
        return replace(rep, **changes) if changes else rep


# ---------------------------------------------------------------------------
# Retriever
# ---------------------------------------------------------------------------

def _valid_gtin(spec: SkuSpec) -> bool:
    return bool(spec.gtin) and is_global_gtin(spec.gtin)


def _lookup_query(provider) -> PlannedQuery:
    """The pseudo-query a lookup is filed under: 'OFF' for a GTIN lookup, the provider's own id otherwise."""
    return PlannedQuery(query_id=str(getattr(provider, "lookup_query_id", "") or LOOKUP_QUERY_ID), text="", hl="")


class Retriever:
    """Runs the query plan against the providers and keeps one pool across calls."""

    def __init__(self, spec: SkuSpec, providers: Sequence, exclude_urls: Iterable[str] = (),
                 max_queries: int = 4, early_stop: Optional[EarlyStop] = None) -> None:
        self.spec = spec
        self.providers = list(providers or [])
        self.max_queries = max(0, int(max_queries))
        self.early_stop = early_stop
        self.pool = CandidatePool(spec, exclude_urls)
        self.result = RetrievalResult()
        self.stopped = False
        self.custom_query: Optional[str] = None
        self._disabled: Set[str] = set()
        self._workers = max(2, len(self.providers) + 1)

    # ----------------------------------------------------------------- calls

    def _call(self, provider, query: PlannedQuery, lookup: bool = False) -> ProviderResult:
        name = str(getattr(provider, "name", "") or type(provider).__name__)
        start = time.monotonic()
        try:
            if lookup and hasattr(provider, "lookup"):
                res = provider.lookup(self.spec)
            else:
                res = provider.search(query.text, query.hl, self.spec)
            if not isinstance(res, ProviderResult):
                raise TypeError(f"provider returned {type(res).__name__}, not ProviderResult")
        except Exception as exc:  # isolate a broken provider
            logger.warning("provider=%s status=error query_id=%s error=%s: %s",
                           name, query.query_id, type(exc).__name__, exc)
            res = ProviderResult(provider=name, status="error", error=f"{type(exc).__name__}: {exc}"[:300])
        sanctioned = bool(getattr(provider, "sanctioned", False))
        cands = [
            replace(c, query_id=query.query_id, provider=c.provider or name,
                    sanctioned=bool(c.sanctioned and sanctioned))
            for c in (res.candidates or [])
            if isinstance(c, Candidate)
        ]
        status = res.status if res.status in STATUSES else "error"
        if status == "ok" and not cands:
            status = "empty"
        latency = res.latency_ms if res.latency_ms is not None else int((time.monotonic() - start) * 1000)
        return replace(res, provider=res.provider or name, status=status, candidates=cands,
                       query_id=query.query_id, latency_ms=latency)

    def _note_health(self, provider, res: ProviderResult) -> None:
        if res.status in ("quota", "blocked") or (res.status == "error" and res.http_status in (401, 403)):
            self._disabled.add(str(getattr(provider, "name", "")))

    def _eligible(self, query: PlannedQuery):
        out = []
        for p in self.providers:
            name = str(getattr(p, "name", ""))
            if getattr(p, "kind", "search") != "search" or name in self._disabled:
                continue
            if query.providers_hint and name not in query.providers_hint:
                continue
            out.append(p)
        return out

    def _run_query(self, query: PlannedQuery, ex: ThreadPoolExecutor) -> Optional[List[ProviderResult]]:
        eligible = self._eligible(query)
        if not eligible:
            logger.info("retrieve: %s skipped (no provider may serve it)", query.query_id)
            return None
        primary = [p for p in eligible if not getattr(p, "fallback", False)]
        fallback = [p for p in eligible if getattr(p, "fallback", False)]
        results: List[ProviderResult] = []
        if primary:
            futs = [(p, ex.submit(self._call, p, query)) for p in primary]
            for p, fut in futs:
                res = fut.result()
                self._note_health(p, res)
                results.append(res)
        if fallback and all(r.status in DOWN_STATUSES for r in results):
            futs = [(p, ex.submit(self._call, p, query)) for p in fallback]
            for p, fut in futs:
                res = fut.result()
                self._note_health(p, res)
                results.append(res)
        self.result.queries.append(query.text)
        if query.relaxed:
            self.result.relaxed_ids.add(query.query_id)
        return results

    def _merge(self, results: Iterable[ProviderResult], relaxed: bool = False) -> None:
        for res in results:
            self.result.health.append(res)
            for cand in res.candidates:
                self.pool.add(cand, relaxed=relaxed)
        self.result.pool = self.pool.candidates()

    def _should_stop(self) -> bool:
        if self.early_stop is None:
            return False
        try:
            stop = bool(self.early_stop(list(self.result.pool)))
        except Exception:
            logger.exception("retrieve: early_stop raised; continuing")
            return False
        if stop:
            self.stopped = True
        return stop

    def _runs_lookup(self, provider) -> bool:
        """A lookup provider runs once per SKU: a GTIN lookup only for a valid GTIN."""
        if getattr(provider, "kind", "search") != "lookup":
            return False
        return _valid_gtin(self.spec) or not getattr(provider, "needs_gtin", True)

    def _budget(self) -> int:
        return self.max_queries - len(self.result.queries)

    # ---------------------------------------------------------------- public

    def run(self, custom_query: Optional[str] = None) -> RetrievalResult:
        """Run the GTIN lookups and the planned queries (or the custom query)."""
        self.custom_query = custom_query.strip() if custom_query and custom_query.strip() else None
        plan = build_queries(self.spec, self.custom_query)
        lookups = [p for p in self.providers if self._runs_lookup(p)]
        with ThreadPoolExecutor(max_workers=self._workers) as ex:
            # Step 1: the lookups in parallel with the first query.
            lookup_futs = [ex.submit(self._call, p, _lookup_query(p), True) for p in lookups]
            first_results: List[ProviderResult] = []
            rest = list(plan)
            while rest and self._budget() > 0:
                q = rest.pop(0)
                ran = self._run_query(q, ex)
                if ran is not None:
                    first_results = ran
                    break
            lookup_results = [f.result() for f in lookup_futs]
            self._merge(lookup_results)
            self._merge(first_results)
            if self._should_stop():
                self._log_summary("early stop after the first step")
                return self.result
            # Step 2: the next queries, in order.
            for q in rest:
                if self._budget() <= 0:
                    logger.info("retrieve: query budget of %d reached", self.max_queries)
                    break
                ran = self._run_query(q, ex)
                if ran is None:
                    continue
                self._merge(ran)
                if self._should_stop():
                    self._log_summary(f"early stop after {q.query_id}")
                    return self.result
        self._log_summary("plan complete")
        return self.result

    def run_extra(self, query: PlannedQuery) -> RetrievalResult:
        """One more planned query into the same pool, within the query budget (not a relaxation)."""
        if self._budget() <= 0:
            logger.info("retrieve: query budget of %d reached; %s not sent", self.max_queries, query.query_id)
            return self.result
        with ThreadPoolExecutor(max_workers=self._workers) as ex:
            ran = self._run_query(query, ex)
        if ran is not None:
            self._merge(ran)
        self._log_summary(f"extra {query.query_id}")
        return self.result

    def rerun_lookups(self, extra: Optional[PlannedQuery] = None) -> RetrievalResult:
        """Ask the lookups that read the spec's brand words once more, with the spec as it is now, in parallel
        with `extra` (one more planned query, within the query budget, not a relaxation) when one is given.

        Only the lookups that need no GTIN (the local catalog index): a GTIN lookup reads the barcode, which
        brand discovery never changes. After discovery the spec accepts the stores' spelling ('Rio Mare' for
        the sheet's 'RIO MARIE'), and the index rows written that way were never asked for in the first step
        (live run 2026-10-03). Free: a local lookup and at most LOCAL_INDEX_MAX_PAGES page reads.
        """
        lookups = [p for p in self.providers if self._runs_lookup(p) and not getattr(p, "needs_gtin", True)]
        query = extra
        if query is not None and self._budget() <= 0:
            logger.info("retrieve: query budget of %d reached; %s not sent", self.max_queries, query.query_id)
            query = None
        if not lookups and query is None:
            return self.result
        with ThreadPoolExecutor(max_workers=self._workers) as ex:
            lookup_futs = [ex.submit(self._call, p, _lookup_query(p), True) for p in lookups]
            ran = self._run_query(query, ex) if query is not None else None
            lookup_results = [f.result() for f in lookup_futs]
        self._merge(lookup_results)
        if ran is not None:
            self._merge(ran)
        self._log_summary(f"lookups again{' + ' + query.query_id if query is not None else ''}")
        return self.result

    def relax(self) -> RetrievalResult:
        """Run R1/R2 into the same pool, within the remaining query budget."""
        if self.custom_query:
            logger.info("retrieve: no relaxations for a custom query")
            return self.result
        if self.stopped:
            return self.result
        with ThreadPoolExecutor(max_workers=self._workers) as ex:
            for q in relaxations(self.spec):
                if self._budget() <= 0:
                    logger.info("retrieve: query budget of %d reached; %s not sent", self.max_queries, q.query_id)
                    break
                ran = self._run_query(q, ex)
                if ran is None:
                    continue
                self._merge(ran, relaxed=True)
                if self._should_stop():
                    break
        self._log_summary("relaxations")
        return self.result

    def _log_summary(self, what: str) -> None:
        logger.info(
            "retrieve sku=%s %s: queries=%d pool=%d excluded=%d health=%s",
            self.spec.sku_key, what, len(self.result.queries), len(self.result.pool), self.pool.excluded,
            ",".join(f"{h.provider}:{h.query_id}:{h.status}" for h in self.result.health),
        )


def retrieve(spec: SkuSpec, providers: Sequence, custom_query: Optional[str] = None,
             exclude_urls: Iterable[str] = (), max_queries: int = 4,
             early_stop: Optional[EarlyStop] = None,
             relax_when: Optional[EarlyStop] = None) -> RetrievalResult:
    """Pool candidates from every provider and planned query (see the module docstring)."""
    r = Retriever(spec, providers, exclude_urls=exclude_urls, max_queries=max_queries, early_stop=early_stop)
    r.run(custom_query)
    if relax_when is not None and not r.custom_query and not r.stopped:
        try:
            want = bool(relax_when(list(r.result.pool)))
        except Exception:
            logger.exception("retrieve: relax_when raised; no relaxations")
            want = False
        if want:
            r.relax()
    return r.result


# ---------------------------------------------------------------------------
# Helpers for callers
# ---------------------------------------------------------------------------

LOOKUP_PROVIDER_NAMES = frozenset({"off", "open_food_facts", "openfoodfacts"})
# Candidates that never stop the web search on their own: the GTIN lookups, and the local catalog
# index (catalog_match.local_index.PROVIDER), which only adds pages the web search may not rank.
NO_EARLY_STOP_PROVIDERS = LOOKUP_PROVIDER_NAMES | {"local_index"}


def t1_early_stop(spec: SkuSpec, negatives=None) -> EarlyStop:
    """early_stop callable: True once a web-search candidate in the pool is tier 1 (score.py).

    A GTIN-lookup record (Open Food Facts) never stops the search on its own: its photo is
    often a user snapshot and its data can be wrong, while the retailer packshot the next
    query would find is usually better evidence. A local-index page does not either: the
    index only adds candidates, the web search runs as it would without it.
    """
    from .score import has_tier1

    def _stop(pool: List[Candidate]) -> bool:
        web = [c for c in pool if (c.provider or "").lower() not in NO_EARLY_STOP_PROVIDERS]
        return has_tier1(spec, web, negatives)

    return _stop


def no_t1_or_t2(spec: SkuSpec, negatives=None) -> EarlyStop:
    """relax_when callable: True when no pooled candidate scores tier 1 or 2."""
    from .score import score_candidate

    def _want(pool: List[Candidate]) -> bool:
        return not any(score_candidate(spec, c, negatives).tier in (1, 2) for c in pool)

    return _want
