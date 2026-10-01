"""The expansion round: one more search for SKUs the normal flow could not pick (sources package, P3).

Most SKUs with no suggestion had NO correct image anywhere in the Google Images results,
while the correct images mostly live on UAE retailer product pages (Lulu, Carrefour,
amazon.ae, noon, talabat) and on the brand's own site. This round asks those sources
directly, after the normal flow has decided.

run_round(RoundInput) -> RoundReport

When it runs
    * 'expand'   the decision is NOT_FOUND or REVIEW_UNSELECTED (no confident pick), the
                 search providers were not down (PROVIDER_DOWN) and the verifier answered
                 (no VERIFIER_DOWN: a candidate nobody can read is not worth paying for);
    * 'upgrade'  the decision is REVIEW_PRESELECTED / AUTO_PUBLISH but the pick carries the
                 low_resolution warning: one visual search for the same packshot at a higher
                 resolution;
    * never when the caller injected providers or a verifier (tests, the offline eval),
      unless it also passed an Expansion explicitly (pipeline.find_product_image).

The round ('expand'), within EXPANSION_MAX_CALLS paid calls (every provider call counts):
    X1  serper_web: the SKU's Q1 text (or the staff's custom query) scoped with site: OR
        over the brand's official domains and the main UAE retailers; the result pages
        are fetched (catalog_match.pages) for their product image, name and GTIN;
    X2  serper_shopping: the same text; full-size listing images are direct candidates,
        thumbnail listings are followed to the store page;
    X3, X4  visual search seeded by the best near-matches of the normal flow: brand-
        consistent tier 1/2 candidates the verifier read as UNSURE (or MATCH on an image
        too small to use), tier-1 or 'only the size is missing' tier-2 candidates it never
        read, and right-brand images rejected only for being too small. SerpApi (about
        15x a Serper call) is asked for at most one seed per product;
    X5  serper_web over the other UAE retailers, only when calls remain, no seed existed
        and the first rounds found no tier 1/2 candidate.
    Page links of web results are fetched only on UAE retailer, brand-official or known
    retail hosts (and any .ae host); hits whose own title or URL already breaks a hard
    identity rule are never fetched.

Then everything goes through the normal stages with the normal rules: the new candidates
join the SAME pool (deduplicated, evidence merged, reviewer negatives excluded), every
candidate is scored by score.py (a page that states another brand, size or variant is
hard-rejected exactly like a search result), the new tier-1/2 candidates are downloaded
(images within pHash distance 6 of a reviewer negative dropped), quality-assessed, and
the new usable tier-1/2 candidates no verifier read yet are verified (up to 2 calls of 4,
the second only when the first found no MATCH). A new image whose bytes are identical to
an image the verifier already read inherits that reading (same pixels, same reading).
decide.route() then re-decides over the whole pool with the normal results plus the new
ones. Images read from a fetched page are provider 'page', sanctioned False: they can be
pre-checked for review but never auto-published. The same holds for an image a search API
also found once a page's evidence merged into it (scraped text never makes an auto-publish).

The upgrade ('upgrade'): one visual search on the pick's image; new matches in the same
pHash family (distance <= UPGRADE_PHASH_DISTANCE), with a short side >= 500 px and larger
than the pick, at the same or a better identity tier, are downloaded and verified (one
call). The first one read as MATCH is placed just before the pick and route() re-decides;
the upgrade is kept only when it becomes the pick and an AUTO_PUBLISH decision stays
AUTO_PUBLISH. Otherwise the original decision is restored unchanged.

Every paid call is recorded: provider calls in the outcome's provider_health (provider
'serper_web' | 'serper_shopping' | 'lens_serper' | 'lens_serpapi', query_id X1..X5 / XU),
verifier calls in RoundReport.verify_results (the pipeline adds them to vlm_calls). Page
fetches are free and are logged, not recorded as provider calls.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from . import decide, settings
from .fetch import phash_distance
from .models import Candidate, ProviderResult, RankedCandidate, SearchOutcome, SkuSpec, VerificationResult
from .pages import PROVIDER as PAGE_PROVIDER, PageFetcher, is_thumbnail, page_candidates
from .providers.lens import build_visual_search
from .providers.serper_shopping import SerperShoppingProvider
from .providers.serper_web import SerperWebProvider, site_query
from .quality import LOW_RES_SHORT_SIDE
from .query_plan import build_queries
from .retrieve import norm_image_url
from .score import rank, rank_key, score_candidate, trusted_domains
from .text_norm import domain_matches, url_host

logger = logging.getLogger(__name__)

PICK_DECISIONS = ("REVIEW_PRESELECTED", "AUTO_PUBLISH")
WEB_GROUP_1 = ("luluhypermarket.com", "carrefouruae.com", "amazon.ae", "noon.com", "talabat.com")
MAX_OFFICIAL_SITES = 2
MAX_PAGES = {"web": 5, "shopping": 4, "lens": 4}
MAX_LENS_SEEDS = 2
MAX_SERPAPI_SEEDS = 1
PAGE_WORKERS = 6
VERIFY_BATCH = 4
MAX_VERIFY_CALLS = 2
UPGRADE_PHASH_DISTANCE = 8
UPGRADE_MAX_FETCH = 6
SMALL_IMAGE_REASONS = frozenset({"short_side<250"})
SERPER_REFUSED_HTTP = (401, 403)
# conflicts that mean more than 'the size is not stated' (a near-match must lack ONLY the size)
_NOT_ONLY_SIZE = ("sub_brand_missing", "unstated_variant", "soft_variant_conflict", "image_variant_conflict",
                  "url_size_conflict", "url_pack_conflict", "pack_ambiguous", "generic_brand_position")


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class Expansion:
    """The sources of one expansion round. Any of them may be None (not configured)."""

    web: Any = None              # .search(query, hl, spec) -> ProviderResult of page links
    shopping: Any = None         # .search(query, hl, spec) -> ProviderResult of listings
    visual: Any = None           # .search(image_url, spec, query_id, max_calls) -> [ProviderResult]
    pages: Any = None            # .fetch_page(url, referer) -> pages.PageInfo
    max_calls: int = 4

    def active(self) -> bool:
        return self.max_calls > 0 and any(s is not None for s in (self.web, self.shopping, self.visual))


def default_expansion() -> Optional[Expansion]:
    """The round built from settings, or None (disabled, no budget, or no key for any source)."""
    if not settings.expansion_enabled():
        return None
    max_calls = settings.expansion_max_calls()
    if max_calls <= 0:
        return None
    key = settings.serper_api_key()
    exp = Expansion(
        web=SerperWebProvider(api_key=key) if key else None,
        shopping=SerperShoppingProvider(api_key=key) if key else None,
        visual=build_visual_search(),
        pages=PageFetcher(),
        max_calls=max_calls,
    )
    return exp if exp.active() else None


def resolve(expansion: Any, injected: bool) -> Optional[Expansion]:
    """The round for one find_product_image() call.

    None (the default) -> default_expansion(), but only when the caller injected neither
    providers nor a verifier; False -> no round; True -> default_expansion() even with
    injected stages (an explicit opt-in, e.g. a live dry run); an Expansion -> itself.
    """
    if expansion is None:
        return None if injected else default_expansion()
    if expansion is False:
        return None
    if expansion is True:
        return default_expansion()
    return expansion if getattr(expansion, "active", lambda: True)() else None


# ---------------------------------------------------------------------------
# Round input / output
# ---------------------------------------------------------------------------

@dataclass
class RoundInput:
    spec: SkuSpec
    exp: Expansion
    outcome: SearchOutcome
    ranked: List[RankedCandidate]
    results: List[VerificationResult]
    health: List[ProviderResult]
    relaxed_ids: Set[str]
    pool: Any                    # retrieve.CandidatePool of the normal flow
    fetcher: Any
    verifier: Any
    phash_negatives: Sequence[str] = ()
    negatives: Any = None
    custom_query: Optional[str] = None


@dataclass
class RoundReport:
    outcome: SearchOutcome
    ran: str = ""                                   # '' | 'expand' | 'upgrade'
    health: List[ProviderResult] = field(default_factory=list)
    queries: List[str] = field(default_factory=list)
    verify_results: List[VerificationResult] = field(default_factory=list)
    phash_dropped: int = 0
    pages_fetched: int = 0
    new_candidates: int = 0
    upgraded: bool = False

    @property
    def calls(self) -> int:
        return len(self.health)


class _Budget:
    def __init__(self, total: int) -> None:
        self.left = max(0, int(total))

    def take(self, n: int = 1) -> bool:
        if self.left < n:
            return False
        self.left -= n
        return True


# ---------------------------------------------------------------------------
# Trigger
# ---------------------------------------------------------------------------

def _serper_refused(health: Iterable[ProviderResult]) -> bool:
    """The Serper key was refused in the normal flow (credit / key): its other endpoints would be too."""
    for h in health or ():
        if (h.provider or "") != "serper":
            continue
        if h.status in ("quota", "blocked") or (h.status == "error" and h.http_status in SERPER_REFUSED_HTTP):
            return True
    return False


def _visual_ready(exp: Expansion) -> bool:
    visual = exp.visual
    return visual is not None and bool(getattr(visual, "available", lambda: True)())


def trigger(outcome: SearchOutcome, exp: Optional[Expansion]) -> str:
    """'expand' | 'upgrade' | '' (see the module docstring)."""
    if exp is None or not exp.active():
        return ""
    if outcome.decision in PICK_DECISIONS:
        w = outcome.winner
        if (w is not None and f"{decide.WARN_PREFIX}low_resolution" in w.reasons and _visual_ready(exp)
                and w.fetched is not None and w.fetched.ok and w.fetched.phash
                and w.candidate.image_url.lower().startswith(("http://", "https://"))):
            return "upgrade"
        return ""
    if outcome.decision == "PROVIDER_DOWN" or outcome.failure_code == "VERIFIER_DOWN":
        return ""
    return "expand"


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------

def text_query(spec: SkuSpec, custom_query: Optional[str] = None) -> Tuple[str, str]:
    """(text, hl): the staff's custom query, else the plan's Q1 (brand + written-out name + size)."""
    if custom_query and custom_query.strip():
        text = " ".join(custom_query.split())
        plan = build_queries(spec, text)
        return (plan[0].text, plan[0].hl) if plan else ("", "en")
    q1 = next((q for q in build_queries(spec) if q.query_id == "Q1"), None)
    return (q1.text, q1.hl) if q1 is not None else ("", "en")


def site_groups(spec: SkuSpec) -> List[List[str]]:
    """Two small OR groups: the brand's sites + the main UAE retailers, then the other UAE retailers."""
    retailers = [d.lower() for d in trusted_domains().get("uae_retailers", [])]
    official = [d.strip().lower() for d in spec.official_domains if d and d.strip()]
    first = list(dict.fromkeys(official[:MAX_OFFICIAL_SITES] + [d for d in WEB_GROUP_1 if d in retailers]))
    second = list(dict.fromkeys([d for d in retailers if d not in first]
                                + official[MAX_OFFICIAL_SITES:MAX_OFFICIAL_SITES + 2]))
    return [first, second]


# ---------------------------------------------------------------------------
# Hits -> candidates
# ---------------------------------------------------------------------------

def _fetchable(spec: SkuSpec, page_url: str) -> bool:
    """Pages worth one GET: UAE retailers, the brand's own sites, known retail, any .ae host."""
    host = url_host(page_url)
    if not host:
        return False
    data = trusted_domains()
    if domain_matches(host, data.get("stock_or_clipart", [])) or decide._social_host(host):
        return False
    return (domain_matches(host, spec.official_domains) or host.endswith(".ae")
            or domain_matches(host, data.get("uae_retailers", []))
            or domain_matches(host, data.get("other_retail", [])))


def _prefilter(spec: SkuSpec, hits: List[Candidate], negatives) -> List[Candidate]:
    """Page hits in identity order; a hit whose own title / URL already breaks a hard rule is dropped."""
    scored = []
    for hit in hits:
        s = score_candidate(spec, replace(hit, image_url=""), negatives)
        if s.hard_reject:
            logger.debug("expand: %s not fetched (%s)", hit.page_url, ",".join(s.hard_reject))
            continue
        scored.append((hit, s))
    return [c for c, _ in rank(scored)]


def _call(provider, query: str, hl: str, spec: SkuSpec, query_id: str) -> ProviderResult:
    name = str(getattr(provider, "name", "") or type(provider).__name__)
    try:
        res = provider.search(query, hl, spec)
        if not isinstance(res, ProviderResult):
            raise TypeError(f"provider returned {type(res).__name__}, not ProviderResult")
    except Exception as exc:  # isolate a broken source
        logger.warning("expand: provider=%s query_id=%s raised %s", name, query_id, type(exc).__name__)
        res = ProviderResult(provider=name, status="error", error=f"{type(exc).__name__}"[:300])
    cands = [replace(c, query_id=query_id, provider=c.provider or name) for c in (res.candidates or [])
             if isinstance(c, Candidate)]
    status = res.status if res.status in ("ok", "empty", "error", "quota", "blocked") else "error"
    if status == "ok" and not cands:
        status = "empty"
    return replace(res, provider=res.provider or name, status=status, candidates=cands, query_id=query_id)


class _Collector:
    """Turns provider hits into image candidates: direct images and product pages read with pages.py."""

    def __init__(self, inp: RoundInput, report: RoundReport) -> None:
        self.inp = inp
        self.report = report
        self.spec = inp.spec
        self.seen_pages: Set[str] = set()

    def _fetch_pages(self, hits: List[Candidate]) -> List[Candidate]:
        pages = self.inp.exp.pages
        if pages is None or not hits:
            return []

        def one(hit: Candidate) -> List[Candidate]:
            try:
                info = pages.fetch_page(hit.page_url, "")
            except Exception as exc:  # a broken page must not lose the round
                logger.warning("expand: page fetch raised %s for %s", type(exc).__name__, url_host(hit.page_url))
                return []
            if info is None or not getattr(info, "ok", False):
                return []
            return page_candidates(info, title=hit.title, snippet=hit.snippet, query_id=hit.query_id, rank=hit.rank)

        with ThreadPoolExecutor(max_workers=max(1, min(PAGE_WORKERS, len(hits)))) as ex:
            lists = list(ex.map(one, hits))
        self.report.pages_fetched += len(hits)
        return [c for lst in lists for c in lst]

    def collect(self, results: Iterable[ProviderResult], kind: str) -> List[Candidate]:
        """Candidates from one source's results. kind: 'web' | 'shopping' | 'lens'."""
        direct: List[Candidate] = []
        to_follow: List[Candidate] = []
        for res in results:
            for hit in res.candidates:
                image_ok = bool(hit.image_url) and kind != "web" and not is_thumbnail(hit.image_url, hit.width,
                                                                                      hit.height)
                if image_ok:
                    direct.append(replace(hit, sanctioned=bool(hit.sanctioned)))
                if hit.page_url and not image_ok and _fetchable(self.spec, hit.page_url):
                    key = norm_image_url(hit.page_url)
                    if key and key not in self.seen_pages:
                        to_follow.append(hit)
        follow = _prefilter(self.spec, to_follow, self.inp.negatives)
        picked: List[Candidate] = []
        for hit in follow:
            key = norm_image_url(hit.page_url)
            if key in self.seen_pages:
                continue
            self.seen_pages.add(key)
            picked.append(hit)
            if len(picked) >= MAX_PAGES[kind]:
                break
        return direct + self._fetch_pages(picked)


# ---------------------------------------------------------------------------
# Seeds for visual search
# ---------------------------------------------------------------------------

def _lacks_only_size(spec: SkuSpec, rc: RankedCandidate) -> bool:
    s = rc.score
    matched = s.matched or {}
    return (s.tier == 2 and bool(matched.get("brand")) and s.size_status in ("unknown", "ambiguous")
            and len(matched.get("variants") or ()) == len(spec.variants)
            and float(matched.get("coverage") or 0.0) >= 0.5
            and not any(str(c).startswith(_NOT_ONLY_SIZE) for c in s.conflicts))


def near_matches(spec: SkuSpec, ranked: Sequence[RankedCandidate]) -> List[RankedCandidate]:
    """Seeds for visual search: brand-consistent candidates that were close but not confirmed."""
    out: List[RankedCandidate] = []
    for rc in ranked:
        s = rc.score
        if s is None or s.tier not in (1, 2) or s.hard_reject or rc.status == "excluded":
            continue
        if not rc.candidate.image_url.lower().startswith(("http://", "https://")):
            continue
        v = rc.verdict
        if v is not None and (v.decision == decide.MISMATCH or v.brand_match == "no"):
            continue
        if rc.fetched is not None and not rc.fetched.ok:
            continue                       # the image is not there (404, not an image)
        small = rc.quality is not None and not rc.quality.hard_ok
        if small and not set(rc.quality.hard_reasons or ()) <= SMALL_IMAGE_REASONS:
            continue                       # a banner or a blank frame finds nothing useful
        decision = v.decision if v is not None else decide.UNKNOWN
        if small or decision in (decide.UNSURE, decide.MATCH) or s.tier == 1 or _lacks_only_size(spec, rc):
            out.append(rc)
    return out


def _distinct(seeds: List[RankedCandidate], limit: int) -> List[RankedCandidate]:
    picked: List[RankedCandidate] = []
    for rc in seeds:
        ph = rc.fetched.phash if rc.fetched is not None else None
        if ph and any(phash_distance(ph, p.fetched.phash if p.fetched is not None else None) is not None
                      and phash_distance(ph, p.fetched.phash) <= 6 for p in picked):
            continue
        picked.append(rc)
        if len(picked) >= limit:
            break
    return picked


# ---------------------------------------------------------------------------
# Shared stages (the pipeline's own helpers, imported lazily: pipeline imports this module)
# ---------------------------------------------------------------------------

def _stages():
    from . import pipeline
    return pipeline


def _identity_ok(rc: RankedCandidate) -> bool:
    return rc.score is not None and rc.score.tier is not None and not rc.score.hard_reject


def _merge_into_pool(inp: RoundInput, new: List[Candidate]) -> Tuple[List[RankedCandidate], List[RankedCandidate]]:
    """Add the new candidates to the pool; (every ranked candidate, the new ones), all scored.

    A fetched page's evidence (its name, URL, GTIN) merges into an entry with the same
    image like any other evidence, but it is scraped text: an entry a page candidate
    touched is never sanctioned, so a page can pre-check an image for review but can
    never be what makes it auto-publishable (even when a search API also found the image).
    """
    pool = inp.pool
    by_cand = {id(rc.candidate): rc for rc in inp.ranked}
    by_key: Dict[str, RankedCandidate] = {}
    dropped: Set[str] = set()                   # pHash reviewer negatives of the normal flow
    for key, cand in pool.entries():
        rc = by_cand.get(id(cand))
        if rc is None:
            dropped.add(key)
        else:
            by_key[key] = rc
    for cand in new:
        pool.add(cand)
    for key in {norm_image_url(c.image_url) for c in new if (c.provider or "") == PAGE_PROVIDER}:
        pool.unsanction(key)
    everything: List[RankedCandidate] = []
    fresh: List[RankedCandidate] = []
    for key, cand in pool.entries():
        if key in dropped:
            continue
        rc = by_key.get(key)
        if rc is None:
            rc = RankedCandidate(candidate=cand, score=score_candidate(inp.spec, cand, inp.negatives))
            fresh.append(rc)
        elif cand is not rc.candidate:
            if rc.fetched is not None and cand.image_url != rc.candidate.image_url:
                # the image that was downloaded and read stays the one offered
                cand = replace(cand, image_url=rc.candidate.image_url, width=rc.candidate.width,
                               height=rc.candidate.height)
            rc.candidate = cand
            rc.score = score_candidate(inp.spec, cand, inp.negatives)
        everything.append(rc)
    return everything, fresh


def _inherit_readings(everything: List[RankedCandidate], fresh: List[RankedCandidate]) -> int:
    """A new image with the same bytes as one the verifier read gets that reading."""
    fresh_ids = {id(rc) for rc in fresh}
    read = {rc.fetched.content_sha256: rc.verdict for rc in everything
            if id(rc) not in fresh_ids and rc.verdict is not None and rc.fetched is not None and rc.fetched.ok
            and rc.fetched.content_sha256}
    n = 0
    for rc in fresh:
        if rc.verdict is None and rc.fetched is not None and rc.fetched.ok:
            verdict = read.get(rc.fetched.content_sha256)
            if verdict is not None:
                rc.verdict = replace(verdict)
                n += 1
    return n


def _verify_new(inp: RoundInput, everything: List[RankedCandidate]) -> List[VerificationResult]:
    p = _stages()
    todo = [rc for rc in everything if p._usable(rc) and rc.verdict is None and rc.score.tier in (1, 2)]
    out: List[VerificationResult] = []
    first = todo[:VERIFY_BATCH]
    res = p._verify(inp.spec, inp.verifier, first)
    if res is None:
        return out
    out.append(res)
    if res.status == "ok" and len(out) < MAX_VERIFY_CALLS:
        matched = any(rc.verdict is not None and rc.verdict.decision == decide.MATCH for rc in first)
        rest = [rc for rc in todo[VERIFY_BATCH:] if rc.verdict is None]
        if not matched and rest:
            res2 = p._verify(inp.spec, inp.verifier, rest[:VERIFY_BATCH])
            if res2 is not None:
                out.append(res2)
    return out


def _record(report: RoundReport, res: ProviderResult, what: str) -> None:
    report.health.append(res)
    report.queries.append(f"{res.provider}[{res.query_id}]: {what}")


# ---------------------------------------------------------------------------
# The round
# ---------------------------------------------------------------------------

def _expand(inp: RoundInput, report: RoundReport) -> RoundReport:
    exp, spec = inp.exp, inp.spec
    budget = _Budget(exp.max_calls)
    serper_ok = not _serper_refused(inp.health)
    collector = _Collector(inp, report)
    text, hl = text_query(spec, inp.custom_query)
    groups = site_groups(spec)
    new: List[Candidate] = []

    # X1 + X2 in parallel
    jobs: List[Tuple[str, Any, str, str]] = []
    if text and serper_ok:
        if exp.web is not None and groups[0] and budget.take():
            jobs.append(("X1", exp.web, site_query(text, groups[0]), "web"))
        if exp.shopping is not None and budget.take():
            jobs.append(("X2", exp.shopping, text, "shopping"))
    if jobs:
        with ThreadPoolExecutor(max_workers=len(jobs)) as ex:
            futs = [(qid, query, kind, ex.submit(_call, prov, query, hl, spec, qid)) for qid, prov, query, kind in jobs]
            done = [(qid, query, kind, f.result()) for qid, query, kind, f in futs]
        for qid, query, kind, res in done:
            _record(report, res, query)
            new.extend(collector.collect([res], kind))

    # X3, X4: visual search from the normal flow's near-matches
    seeds = _distinct(near_matches(spec, inp.ranked), MAX_LENS_SEEDS) if _visual_ready(exp) else []
    skip = ("lens_serper",) if not serper_ok else ()
    serpapi_used = 0
    for i, seed in enumerate(seeds):
        if budget.left <= 0:
            break
        nxt = _next_backend(exp.visual, skip)
        if nxt is None or (nxt == "lens_serpapi" and serpapi_used >= MAX_SERPAPI_SEEDS):
            break
        results = _visual(exp.visual, seed.candidate.image_url, spec, f"X{3 + i}", budget, skip,
                          serpapi_left=MAX_SERPAPI_SEEDS - serpapi_used)
        for res in results:
            _record(report, res, seed.candidate.image_url)
            serpapi_used += 1 if res.provider == "lens_serpapi" else 0
        new.extend(collector.collect(results, "lens"))

    # X5: the other UAE retailers, only when nothing promising turned up
    if (not seeds and text and serper_ok and exp.web is not None and len(groups) > 1 and groups[1]
            and budget.left > 0 and not _promising(spec, new, inp.negatives) and budget.take()):
        query = site_query(text, groups[1])
        res = _call(exp.web, query, hl, spec, "X5")
        _record(report, res, query)
        new.extend(collector.collect([res], "web"))

    report.new_candidates = len(new)
    if not new:
        _append_health(report)
        logger.info("expand sku=%s: %d calls, nothing new", spec.sku_key, report.calls)
        return report

    # the normal stages over the grown pool
    p = _stages()
    everything, fresh = _merge_into_pool(inp, new)
    # The new candidates, and the pool's never-downloaded ones that are tier 1/2 now (a page's
    # evidence merged into an image the normal flow ranked below its download slots).
    fresh_ids = {id(rc) for rc in fresh}
    unseen = [rc for rc in everything if id(rc) not in fresh_ids and rc.fetched is None]
    fetch_list = [rc for rc in rank_rcs(fresh + unseen) if _identity_ok(rc) and rc.score.tier in (1, 2)]
    kept, n_dropped = p._fetch(spec, inp.fetcher, fetch_list, inp.phash_negatives) if fetch_list else ([], 0)
    gone = {id(rc) for rc in fetch_list} - {id(rc) for rc in kept}
    everything = [rc for rc in everything if id(rc) not in gone]
    report.phash_dropped += n_dropped
    p._assess(kept)
    everything = p._rerank(everything)
    inherited = _inherit_readings(everything, fresh)
    report.verify_results.extend(_verify_new(inp, everything))
    report.outcome = decide.route(spec, everything, list(inp.results) + report.verify_results,
                                  list(inp.health) + report.health, inp.relaxed_ids)
    logger.info("expand sku=%s: %d calls, %d pages, %d new candidates (%d fetched, %d readings inherited), "
                "%d verifier calls -> %s", spec.sku_key, report.calls, report.pages_fetched, len(fresh), len(kept),
                inherited, sum(int(r.calls or 0) for r in report.verify_results), report.outcome.decision)
    return report


def rank_rcs(rcs: List[RankedCandidate]) -> List[RankedCandidate]:
    by_id = {id(rc.candidate): rc for rc in rcs}
    return [by_id[id(c)] for c, _ in rank([(rc.candidate, rc.score) for rc in rcs])]


def _promising(spec: SkuSpec, cands: List[Candidate], negatives) -> bool:
    return any(score_candidate(spec, c, negatives).tier in (1, 2) for c in cands)


def _next_backend(visual, skip: Sequence[str]) -> Optional[str]:
    names = getattr(visual, "backends", None)
    if names is None:
        return getattr(visual, "name", "visual")
    for backend in names:
        if backend.name in skip:
            continue
        if getattr(visual, "_usable", lambda b: True)(backend):
            return backend.name
    return None


def _visual(visual, image_url: str, spec: SkuSpec, query_id: str, budget: _Budget, skip: Sequence[str],
            serpapi_left: int) -> List[ProviderResult]:
    """One seed through the visual search, within the call budget; SerpApi only while allowed."""
    results: List[ProviderResult] = []
    backends = getattr(visual, "backends", None)
    if backends is None:                       # a test double with its own search()
        if not budget.take():
            return results
        try:
            out = visual.search(image_url, spec, query_id, 1)
        except Exception as exc:
            out = [ProviderResult(provider="visual", status="error", error=type(exc).__name__)]
        return [_stamp(r, query_id) for r in out or []][:1]
    for backend in backends:
        if backend.name in skip or not getattr(visual, "_usable", lambda b: True)(backend):
            continue
        if backend.name == "lens_serpapi" and serpapi_left <= 0:
            continue
        if not budget.take():
            break
        res = _call(backend, image_url, "en", spec, query_id)
        results.append(res)
        if res.provider == "lens_serpapi":
            serpapi_left -= 1
        if res.status in ("ok", "empty"):
            break
    return results


def _stamp(res: ProviderResult, query_id: str) -> ProviderResult:
    cands = [replace(c, query_id=query_id, provider=c.provider or res.provider) for c in res.candidates
             if isinstance(c, Candidate)]
    return replace(res, query_id=query_id, candidates=cands)


def _append_health(report: RoundReport) -> None:
    """Paid calls of a round that did not re-route still belong in the outcome's provider_health."""
    if report.health:
        report.outcome.provider_health.extend(r.health() for r in report.health)


def _identity_key(rc: RankedCandidate) -> Tuple:
    """score.rank_key's identity part: tier, size, variants, class coverage, source trust (no quality)."""
    return rank_key(rc.candidate, rc.score)[:5]


def _upgrade(inp: RoundInput, report: RoundReport) -> RoundReport:
    exp, spec, outcome = inp.exp, inp.spec, inp.outcome
    winner = outcome.winner
    # Identity before image quality: a sharper copy may replace the pick only when its own identity
    # evidence is at least as strong (a Lulu pick is never swapped for an amazon.in copy) and it
    # brings no review warning the pick did not have (other than losing low_resolution).
    winner_key = _identity_key(winner)
    winner_warnings = set(decide.warning_codes(winner.reasons))
    budget = _Budget(min(1, exp.max_calls))
    skip = ("lens_serper",) if _serper_refused(inp.health) else ()
    results = _visual(exp.visual, winner.candidate.image_url, spec, "XU", budget, skip,
                      serpapi_left=MAX_SERPAPI_SEEDS)
    for res in results:
        _record(report, res, winner.candidate.image_url)
    collector = _Collector(inp, report)
    cands = collector.collect(results, "lens")
    known = {norm_image_url(rc.candidate.image_url) for rc in inp.ranked} | {k for k, _ in inp.pool.entries()}
    fresh: List[RankedCandidate] = []
    for c in cands:
        key = norm_image_url(c.image_url)
        if not key or key in known or inp.pool.is_excluded(c.image_url):
            continue
        known.add(key)
        rc = RankedCandidate(candidate=c, score=score_candidate(spec, c, inp.negatives))
        if _identity_ok(rc) and _identity_key(rc) <= winner_key:
            fresh.append(rc)
    report.new_candidates = len(fresh)
    w_short = min(winner.fetched.width or 0, winner.fetched.height or 0)
    family: List[RankedCandidate] = []
    if fresh:
        p = _stages()
        kept, n_dropped = p._fetch(spec, inp.fetcher, rank_rcs(fresh)[:UPGRADE_MAX_FETCH], inp.phash_negatives)
        report.phash_dropped += n_dropped
        p._assess(kept)
        for rc in kept:
            if not p._usable(rc):
                continue
            d = phash_distance(rc.fetched.phash, winner.fetched.phash)
            short = min(rc.fetched.width or 0, rc.fetched.height or 0)
            if d is not None and d <= UPGRADE_PHASH_DISTANCE and short >= LOW_RES_SHORT_SIDE and short > w_short:
                family.append(rc)
        if family:
            res = p._verify(spec, inp.verifier, family[:VERIFY_BATCH])
            if res is not None:
                report.verify_results.append(res)
    good = [rc for rc in family[:VERIFY_BATCH]
            if rc.verdict is not None and rc.verdict.decision == decide.MATCH and _identity_key(rc) <= winner_key]
    health = list(inp.health) + report.health
    if good:
        up = good[0]
        trial = list(inp.ranked)
        trial.insert(next(i for i, rc in enumerate(trial) if rc is winner), up)
        out = decide.route(spec, trial, list(inp.results) + report.verify_results, health, inp.relaxed_ids)
        new_warnings = set(decide.warning_codes(up.reasons)) - winner_warnings - {"low_resolution"}
        keep = (out.winner is up and out.decision in PICK_DECISIONS and not new_warnings
                and not (outcome.decision == "AUTO_PUBLISH" and out.decision != "AUTO_PUBLISH"))
        if keep:
            inp.pool.add(up.candidate)
            report.outcome = out
            report.upgraded = True
            logger.info("expand sku=%s: low-resolution pick upgraded to %s (%sx%s)", spec.sku_key,
                        up.candidate.image_url, up.fetched.width, up.fetched.height)
            return report
    # restore the original decision exactly (route() is idempotent over the same inputs)
    if report.health or report.verify_results:
        report.outcome = decide.route(spec, inp.ranked, inp.results, health, inp.relaxed_ids)
    logger.info("expand sku=%s: no higher-resolution copy of the pick (%d matches, %d in its pHash family)",
                spec.sku_key, len(cands), len(family))
    return report


def run_round(inp: RoundInput) -> RoundReport:
    """Run the expansion round when the outcome calls for it (see the module docstring). Never raises."""
    report = RoundReport(outcome=inp.outcome)
    kind = trigger(inp.outcome, inp.exp)
    if not kind:
        return report
    report.ran = kind
    snapshot = [(rc, rc.candidate, rc.score, rc.fetched, rc.quality, rc.verdict) for rc in inp.ranked]
    try:
        return _upgrade(inp, report) if kind == "upgrade" else _expand(inp, report)
    except Exception:  # the normal decision stands; the paid calls already made stay recorded
        logger.exception("expand sku=%s: the %s round failed; the normal decision stands", inp.spec.sku_key, kind)
        for rc, cand, score, fetched, quality, verdict in snapshot:
            rc.candidate, rc.score, rc.fetched, rc.quality, rc.verdict = cand, score, fetched, quality, verdict
        safe = RoundReport(outcome=decide.route(inp.spec, inp.ranked, inp.results,
                                                list(inp.health) + report.health, inp.relaxed_ids),
                           ran=kind, health=report.health, queries=report.queries,
                           verify_results=report.verify_results)
        return safe
