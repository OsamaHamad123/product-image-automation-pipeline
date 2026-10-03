"""The v2 search pipeline: one SKU in, one SearchOutcome out (decisions D3-D10).

find_product_image(spec, *, providers=None, fetcher=None, verifier=None,
                   custom_query=None, exclude_urls=(), exclude_phashes=(),
                   brand_index=None) -> SearchOutcome

Steps
    1. retrieve   pooled retrieval over the query plan (or the staff custom query);
                  early stop as soon as a web-search candidate is tier 1 (an Open
                  Food Facts record alone never stops the search). When brand
                  discovery finds the stores' spelling of the sheet brand, the local
                  catalog index is asked again with it (free), alongside one corrected
                  query.
    2. score      every pooled candidate with score.score_candidate.
    3. relax      R1/R2 into the same pool, only when no candidate is tier 1 or 2
                  and there is no custom query (relaxed winners are capped at review).
    4. fetch      the top 8 identity survivors by rank. Failed downloads stay in the
                  ranked list (decide marks them 'rejected'); images within pHash
                  distance 6 of a reviewer negative are dropped from the outcome.
    5. quality    soft assessment; a hard quality failure is 'rejected' but kept so a
                  reviewer can still see it.
    6. verify     the top 4 usable candidates in one comparative call.
    7. verify #2  when nothing is MATCH yet and unverified tier-1/2 candidates remain,
                  one more call on the next 4 (never more than 2 calls per SKU).
    8. decide     decide.route() maps everything to a decision.
    9. expand     catalog_match.expand: when nothing was picked (or the pick is low-resolution),
                  one more round of paid sources (UAE retailer pages through web search, Google
                  Shopping, visual search) into the same pool, then the same stages and the same
                  route() rules. Off when the caller injected providers or a verifier (tests,
                  the offline eval) unless it passes `expansion` itself.

Nothing wins by arriving first: every query's candidates are pooled and ranked once.
The per-SKU caps are 4 provider queries (the Open Food Facts lookup is not a query)
and 2 verifier calls (the default verifier, catalog_match.verifiers, may add one budgeted
strong second look inside a call: VERIFIER_STRONG_MAX_CALLS per SKU).
"""

from __future__ import annotations

import logging
from typing import Any, Iterable, List, Mapping, Optional, Sequence, Set, Tuple, Union

from . import brand_discovery, decide, expand as expand_mod, quality as quality_mod
from .fetch import load_image, phash_distance
from .models import (
    Candidate, CandidateScore, FetchedImage, RankedCandidate, SearchOutcome, SkuSpec,
    VerificationResult, VlmImageVerdict,
)
from .retrieve import NO_EARLY_STOP_PROVIDERS, Retriever, t1_early_stop
from .score import rank, score_candidate

logger = logging.getLogger(__name__)

MAX_QUERIES = 4
MAX_FETCH = 8
VERIFY_BATCH = 4
MAX_VERIFY_CALLS = 2
PHASH_EXCLUDE_DISTANCE = 6


# ---------------------------------------------------------------------------
# Defaults (real network stages; tests always inject their own)
# ---------------------------------------------------------------------------

def _default_providers() -> list:
    from .providers import default_providers
    return default_providers()


def _default_fetcher():
    from .fetch import HttpFetcher
    return HttpFetcher()


def _default_verifier():
    # verifier package: VERIFIER_PRIMARY reads every batch, VERIFIER_STRONG takes one budgeted second look
    from .verifiers.cascade import default_verifier
    return default_verifier()


def _as_spec(spec: Union[SkuSpec, Mapping[str, Any]], brand_index) -> SkuSpec:
    """Accept a ready SkuSpec, or a sheet row that is turned into one with brand_index."""
    if isinstance(spec, SkuSpec):
        return spec
    from .identity import build_sku_spec
    return build_sku_spec(dict(spec or {}), brand_index)


def _phash_hex(value: Any) -> Optional[str]:
    if value is None or value == "":
        return None
    if isinstance(value, int) and not isinstance(value, bool):
        return f"{value:016x}"
    text = str(value).strip().lower()
    if text.startswith("0x"):
        text = text[2:]
    try:
        int(text, 16)
    except ValueError:
        return None
    return text


def _near_negative(phash: Optional[str], negatives: Sequence[str]) -> Optional[int]:
    """Smallest pHash distance to a reviewer negative when it is within the limit, else None."""
    best: Optional[int] = None
    for neg in negatives:
        d = phash_distance(phash, neg)
        if d is not None and d <= PHASH_EXCLUDE_DISTANCE and (best is None or d < best):
            best = d
    return best


# ---------------------------------------------------------------------------
# Stages
# ---------------------------------------------------------------------------

def _score_pool(spec: SkuSpec, pool: Iterable[Candidate], negatives) -> List[Tuple[Candidate, CandidateScore]]:
    return [(c, score_candidate(spec, c, negatives)) for c in pool]


def _has_t1_t2(scored: Sequence[Tuple[Candidate, CandidateScore]]) -> bool:
    """A web-search tier-1/2 candidate: the lookups (the local index, a GTIN record) never cancel the relaxations,
    the web search runs as it would without them."""
    return any(s.tier in (1, 2) and not s.hard_reject and (c.provider or "").lower() not in NO_EARLY_STOP_PROVIDERS
               for c, s in scored)


def _identity_ok(rc: RankedCandidate) -> bool:
    return rc.score is not None and rc.score.tier is not None and not rc.score.hard_reject


def _usable(rc: RankedCandidate) -> bool:
    return (_identity_ok(rc) and rc.fetched is not None and rc.fetched.ok
            and (rc.quality is None or rc.quality.hard_ok))


def _fetch(spec: SkuSpec, fetcher, ranked: List[RankedCandidate], phash_negatives: Sequence[str]
           ) -> Tuple[List[RankedCandidate], int]:
    """Fetch the top identity survivors; returns (ranked without pHash negatives, n dropped).

    Slots taken by reviewer-negative images are refilled once from the next candidates,
    so a rejected image that reappears under a new URL does not shrink the review set.
    """
    dropped: Set[int] = set()
    attempted: Set[int] = set()
    for _round in range(2):
        todo = [rc for rc in ranked if _identity_ok(rc) and id(rc) not in attempted]
        slots = MAX_FETCH - sum(1 for rc in ranked if id(rc) in attempted and id(rc) not in dropped)
        todo = todo[:max(0, slots)]
        if not todo:
            break
        try:
            results = list(fetcher.fetch([rc.candidate for rc in todo], spec) or [])
        except Exception as exc:  # a broken fetcher must not lose the pool
            logger.exception("pipeline: fetcher raised")
            results = [FetchedImage(candidate=rc.candidate, ok=False, error=f"fetch_error:{type(exc).__name__}")
                       for rc in todo]
        by_url = {}
        for fi in results:
            if isinstance(fi, FetchedImage):
                by_url.setdefault(fi.candidate.image_url, fi)
        new_drops = 0
        for i, rc in enumerate(todo):
            attempted.add(id(rc))
            fi = by_url.get(rc.candidate.image_url)
            if fi is None and i < len(results) and isinstance(results[i], FetchedImage):
                fi = results[i]
            rc.fetched = fi if fi is not None else FetchedImage(candidate=rc.candidate, ok=False, error="not_fetched")
            if rc.fetched.ok and phash_negatives:
                dist = _near_negative(rc.fetched.phash, phash_negatives)
                if dist is not None:
                    logger.info("pipeline: %s dropped, pHash distance %d to a reviewer negative",
                                rc.candidate.image_url, dist)
                    dropped.add(id(rc))
                    new_drops += 1
        if not new_drops:
            break
    kept = [rc for rc in ranked if id(rc) not in dropped]
    return kept, len(dropped)


def _assess(ranked: Sequence[RankedCandidate]) -> None:
    for rc in ranked:
        if rc.fetched is None or not rc.fetched.ok:
            continue
        img = load_image(rc.fetched)
        if img is None:
            rc.fetched.ok = False
            rc.fetched.error = rc.fetched.error or "decode_error"
            continue
        try:
            rc.quality = quality_mod.assess(img)
        except Exception:  # assess never raises by contract; stay defensive
            logger.exception("pipeline: quality assessment failed for %s", rc.candidate.image_url)
            rc.quality = None


def _rerank(ranked: List[RankedCandidate]) -> List[RankedCandidate]:
    """Re-sort with the soft quality score as the final tie-break (identity keys unchanged)."""
    by_pair = {id(rc.candidate): rc for rc in ranked}
    quality_map = {rc.candidate.image_url: (rc.quality.quality_score if rc.quality and rc.quality.hard_ok else 0.0)
                   for rc in ranked}
    order = rank([(rc.candidate, rc.score) for rc in ranked], quality_map)
    return [by_pair[id(c)] for c, _ in order]


def _verify(spec: SkuSpec, verifier, batch: List[RankedCandidate]) -> Optional[VerificationResult]:
    if not batch:
        return None
    try:
        result = verifier.verify(spec, [rc.fetched for rc in batch])
    except Exception as exc:  # fail closed: an exception is an unknown result
        logger.exception("pipeline: verifier raised")
        result = VerificationResult(status="unknown", calls=1, error=f"verifier_error:{type(exc).__name__}",
                                    verdicts=[VlmImageVerdict(index=i) for i in range(len(batch))])
    if not isinstance(result, VerificationResult):
        logger.error("pipeline: verifier returned %s, treated as unknown", type(result).__name__)
        result = VerificationResult(status="unknown", calls=1, error="bad_verifier_result",
                                    verdicts=[VlmImageVerdict(index=i) for i in range(len(batch))])
    verdicts = {v.index: v for v in (result.verdicts or []) if isinstance(v, VlmImageVerdict)}
    for i, rc in enumerate(batch):
        v = verdicts.get(i) if result.status == "ok" else None
        # An unknown call, or an image the reply skipped, is UNKNOWN: never accepted downstream.
        rc.verdict = v if v is not None else VlmImageVerdict(index=i, decision=decide.UNKNOWN)
    return result


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def find_product_image(spec: Union[SkuSpec, Mapping[str, Any]], *, providers: Optional[Sequence] = None,
                       fetcher=None, verifier=None, custom_query: Optional[str] = None,
                       exclude_urls: Iterable[str] = (), exclude_phashes: Iterable[Any] = (),
                       brand_index=None, expansion: Any = None) -> SearchOutcome:
    """Find, check and route the image for one SKU. Never returns an unchecked pick as final.

    expansion: None (default: the configured round, only when neither providers nor a
    verifier were injected), False (never), True (the configured round even with injected
    stages) or an expand.Expansion.
    """
    spec = _as_spec(spec, brand_index)
    exp = expand_mod.resolve(expansion, injected=providers is not None or verifier is not None)
    providers = list(providers) if providers is not None else _default_providers()
    fetcher = fetcher if fetcher is not None else _default_fetcher()
    verifier = verifier if verifier is not None else _default_verifier()
    exclude_urls = [u for u in (exclude_urls or ()) if u]
    phash_negatives = [h for h in (_phash_hex(p) for p in (exclude_phashes or ())) if h]
    negatives = {"urls": exclude_urls} if exclude_urls else None
    custom = custom_query.strip() if custom_query and custom_query.strip() else None

    # 1. retrieve (early stop on tier 1)
    retriever = Retriever(spec, providers, exclude_urls=exclude_urls, max_queries=MAX_QUERIES,
                          early_stop=t1_early_stop(spec, negatives))
    retrieval = retriever.run(custom)

    # 1b. brand discovery: a sheet brand no listing writes the sheet's way ('RIO MARIE', 'SUP/T') but the
    #     stores write one typo or abbreviation away ('Rio Mare', 'Super Tasty'): accept the store spelling
    #     (never an auto-publish) and send the first query once more, written with it.
    #     A spelling an earlier row of this process proved for the same (or a sibling) sheet brand counts too.
    found = brand_discovery.find(spec, retrieval.pool)
    if found is not None:
        spec = brand_discovery.apply(spec, found)
        retriever.spec = spec
        retriever.pool.spec = spec
        retriever.early_stop = t1_early_stop(spec, negatives)      # tier 1 in the store spelling now stops too
        # the corrected query only when the listings found so far are not already tier 1 in the store spelling
        extra = brand_discovery.corrected_query(spec, retrieval.queries) \
            if not custom and not retriever.early_stop(list(retrieval.pool)) else None
        # the local catalog index is asked again with the store spelling (free), alongside the corrected query
        retrieval = retriever.rerun_lookups(extra)

    # 2. score
    scored = _score_pool(spec, retrieval.pool, negatives)

    # 3. relaxations only when the pool has no tier-1/2 candidate
    if not custom and not _has_t1_t2(scored) and not retriever.stopped:
        retrieval = retriever.relax()
        scored = _score_pool(spec, retrieval.pool, negatives)

    # 4. rank and fetch
    ranked = [RankedCandidate(candidate=c, score=s) for c, s in rank(scored)]
    ranked, n_phash_dropped = _fetch(spec, fetcher, ranked, phash_negatives) if ranked else (ranked, 0)

    # 5. soft quality (hard failures stay visible, decide marks them rejected)
    _assess(ranked)
    ranked = _rerank(ranked)

    # 6. first verifier call on the top 4 usable candidates
    results: List[VerificationResult] = []
    usable = [rc for rc in ranked if _usable(rc)]
    first = usable[:VERIFY_BATCH]
    res = _verify(spec, verifier, first)
    if res is not None:
        results.append(res)

    # 7. one more call when nothing matched and unverified tier-1/2 candidates remain. A MATCH on a page whose
    #    barcode differs from the sheet's counts only as decide.route counts it: with a full reading (brand, size,
    #    variant 'yes'); otherwise it cannot be picked and the next candidates are still worth reading.
    if res is not None and res.status == "ok" and len(results) < MAX_VERIFY_CALLS:
        matched = any(rc.verdict is not None and rc.verdict.decision == decide.MATCH
                      and (not decide.gtin_conflict(rc) or decide.full_match(spec, rc)) for rc in first)
        rest = [rc for rc in usable[VERIFY_BATCH:] if rc.verdict is None and rc.score.tier in (1, 2)]
        if not matched and rest:
            res2 = _verify(spec, verifier, rest[:VERIFY_BATCH])
            if res2 is not None:
                results.append(res2)

    # 8. decide
    outcome = decide.route(spec, ranked, results, retrieval.health, retrieval.relaxed_ids)

    # 9. expansion round (sources package): only when nothing confident was picked
    extra_queries: List[str] = []
    if exp is not None:
        report = expand_mod.run_round(expand_mod.RoundInput(
            spec=spec, exp=exp, outcome=outcome, ranked=ranked, results=results,
            health=list(retrieval.health), relaxed_ids=set(retrieval.relaxed_ids), pool=retriever.pool,
            fetcher=fetcher, verifier=verifier, phash_negatives=phash_negatives, negatives=negatives,
            custom_query=custom))
        outcome = report.outcome
        results = results + report.verify_results
        extra_queries = report.queries
        n_phash_dropped += report.phash_dropped
    outcome.queries = list(retrieval.queries) + extra_queries
    outcome.discovered_brands = list(spec.discovered_brands)
    outcome.vlm_calls = sum(int(r.calls or 0) for r in results)
    # verifier package: per-model usage of every billed verifier call, and its dashboard notices
    outcome.vlm_usage = [dict(u) for r in results for u in (getattr(r, "usage", None) or []) if isinstance(u, dict)]
    outcome.verifier_notices = sorted({str(n) for r in results for n in (getattr(r, "notices", None) or [])})
    if n_phash_dropped:
        outcome.reject_counts["reviewer_negative_phash"] = n_phash_dropped
    if retriever.pool.excluded:
        outcome.reject_counts["reviewer_negative_url"] = retriever.pool.excluded
    logger.info("pipeline sku=%s decision=%s failure=%s pool=%d queries=%d vlm_calls=%d",
                spec.sku_key, outcome.decision, outcome.failure_code, len(ranked), len(outcome.queries),
                outcome.vlm_calls)
    return outcome
