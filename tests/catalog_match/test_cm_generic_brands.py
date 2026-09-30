"""Brands that are also common words, end to end: live run 2026-09-30, row 34.

'FRESHLY CHICKEN SHAWARMA 350GM' (brand cell FRESHLY): other brands' chicken shawarma
listings say 'freshly prepared', so they scored tier 1 by text. They took the download
slots and the verifier calls ahead of the real Freshly listing, and their false tier 1
stopped the search before the query that finds it. Providers, fetcher and verifier are
the stage doubles of test_cm_pipeline; scoring, ranking, fetch order, the verifier
budget and routing are the real code. Sockets are blocked.
"""

import socket

import pytest

from catalog_match import pipeline, settings
from catalog_match.identity import build_sku_spec
from catalog_match.models import ProviderResult

from test_cm_pipeline import StubFetcher, StubProvider, StubVerifier, by_url, cand, packshot_png, read_other_brand


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError(f"network access attempted: {args!r}")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    monkeypatch.setattr(settings, "_config", None)
    monkeypatch.setenv("AUTO_PUBLISH_ENABLED", "false")
    monkeypatch.setenv("AUTO_PUBLISH_BRANDS", "")


FRESHLY = build_sku_spec({"name": "FRESHLY CHICKEN SHAWARMA 350GM", "brand": "FRESHLY"}, {})
READ_FRESHLY = {"brand_text": "Freshly", "variant_text": "Chicken Shawarma", "size_text": "350 g", "pack_count": 1,
                "view": "front_packshot", "brand_match": "yes", "variant_match": "yes", "size_match": "yes"}
OTHER_BRANDS = ("Seara", "Zingo", "Al Kabeer", "Americana", "Sadia", "Doux", "Tanmiah", "Al Islami")
WORDINGS = ("freshly prepared", "freshly made", "freshly marinated", "freshly cooked taste", "freshly packed",
            "freshly sliced", "freshly seasoned", "freshly grilled flavour")


def other_brand_listings(n=len(OTHER_BRANDS)):
    """Same product type and size on a UAE retailer page; 'freshly' only as a word."""
    return [cand(i + 1, f"{brand} Chicken Shawarma 350g, {wording} - Carrefour UAE",
                 f"https://www.carrefouruae.com/mafuae/en/{brand.lower().replace(' ', '-')}-chicken-shawarma-350g/p/{i + 1}")
            for i, (brand, wording) in enumerate(zip(OTHER_BRANDS[:n], WORDINGS))]


def freshly_listing(n):
    return cand(n, "Freshly Chicken Shawarma 350g - Lulu UAE",
                f"https://www.luluhypermarket.com/en-ae/freshly-chicken-shawarma-350g/p/{n}")


def readings_for(others, right):
    readings = {c.image_url: read_other_brand(b) for c, b in zip(others, OTHER_BRANDS)}
    readings[right.image_url] = READ_FRESHLY
    return readings


def test_row34_right_listing_behind_eight_other_brands_is_fetched_and_verified():
    others = other_brand_listings()       # provider ranks 1-8: every download slot, were they tier 1
    right = freshly_listing(9)
    cands = others + [right]
    fetcher = StubFetcher({c.image_url: packshot_png(90 + i) for i, c in enumerate(cands)})
    verifier = StubVerifier(readings_for(others, right))

    outcome = pipeline.find_product_image(FRESHLY, providers=[StubProvider("serper", cands)],
                                          fetcher=fetcher, verifier=verifier)

    rows = by_url(outcome)
    for c in others:
        sc = rows[c.image_url].score
        assert sc.tier == 2 and sc.matched["brand"] is True, c.title
        assert "generic_brand_position:title" in sc.conflicts
    assert rows[right.image_url].score.tier == 1
    assert right.image_url in fetcher.fetched
    assert len(verifier.calls) == 1 and right.image_url in verifier.calls[0]
    assert rows[right.image_url].verdict.decision == "MATCH"
    assert outcome.decision == "REVIEW_PRESELECTED"
    assert outcome.winner is rows[right.image_url]


class SiteQueryProvider(StubProvider):
    """The plain name query finds other brands only; the retailer-site query finds the right listing."""

    def __init__(self, plain, site):
        super().__init__("serper", plain)
        self.site = list(site)

    def search(self, query, hl, spec):
        self.calls.append((query, hl))
        found = self.site if "site:" in query else self.cands
        return ProviderResult(provider=self.name, status="ok", http_status=200, latency_ms=0, candidates=list(found))


def test_row34_other_brand_listings_no_longer_stop_the_search():
    others = other_brand_listings(3)
    right = freshly_listing(20)
    serper = SiteQueryProvider(others, [right])
    cands = others + [right]

    outcome = pipeline.find_product_image(
        FRESHLY, providers=[serper],
        fetcher=StubFetcher({c.image_url: packshot_png(110 + i) for i, c in enumerate(cands)}),
        verifier=StubVerifier(readings_for(others, right)))

    assert len(serper.calls) == 2 and "site:" in serper.calls[1][0], "no early stop on 'freshly prepared'"
    rows = by_url(outcome)
    assert rows[right.image_url].score.tier == 1
    assert rows[right.image_url].verdict.decision == "MATCH"
    assert outcome.decision == "REVIEW_PRESELECTED" and outcome.winner is rows[right.image_url]
