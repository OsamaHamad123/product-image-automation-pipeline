"""Shared fixtures for the «why no pick» tests: real candidates scored by score_candidate and routed by decide.route,
so an outcome here is what the engine would decide for them (used by the worker, backfill and export tests)."""

from catalog_match import decide
from catalog_match.models import Candidate, FetchedImage, ProviderHealth, QualityReport, RankedCandidate, \
    VerificationResult, VlmImageVerdict
from catalog_match.score import rank_key, score_candidate

MAPPINGS = {"mr john": {"brand": "Mr John", "synonyms": ["MR JOHN"], "excluded_competitors": []},
            "almarai": {"brand": "Almarai", "synonyms": ["ALMARAI"], "excluded_competitors": []}}


def listing(title, n=1, domain="carrefouruae.com"):
    slug = title.lower().replace(" ", "-")
    return Candidate(image_url=f"https://cdn.{domain}/img/{n}.jpg", page_url=f"https://www.{domain}/mafuae/en/{slug}/p/{n}",
                     title=title, page_title=title, domain=domain, provider="serper", query_id="Q1", rank=n)


def reading(decision, **extra):
    return VlmImageVerdict(index=0, decision=decision, view="front_packshot", **extra)


def routed(spec, rows, verifier="ok"):
    """decide.route over real scores: rows are (candidate, verdict or None)."""
    ranked = []
    for i, (cand, verdict) in enumerate(rows):
        fetched = FetchedImage(candidate=cand, ok=True, content_sha256=f"{i:064d}", width=1200, height=1200,
                               path_or_bytes=b"x", phash=f"{i:016x}")
        ranked.append(RankedCandidate(candidate=cand, score=score_candidate(spec, cand), fetched=fetched,
                                      quality=QualityReport(hard_ok=True, quality_score=0.8), verdict=verdict))
    ranked.sort(key=lambda rc: rank_key(rc.candidate, rc.score, rc.quality.quality_score))
    verdicts = [rc.verdict for rc in ranked if rc.verdict is not None]
    result = VerificationResult(status=verifier, verdicts=verdicts, calls=1 if verdicts else 0)
    return decide.route(spec, ranked, result, [ProviderHealth("serper", "ok", 200, query_id="Q1")])
