"""catalog_match.expand.scope_gate (EXPANSION_SCOPE_GATE): the paid steps X1-X5 only where they can find a pick.

Live runs 2026-10-04/05: the expansion round made 175 of 498 search calls and picked in 6 of 54 rows. Bouquets
(5 rows, 15 calls) and brands no listing names on a store or brand site (typos and abbreviations: 'BARTS TRADITON',
'SUP/T', 'SQ SALITED') never got a pick from it. The stage doubles are test_cm_expand's; the rest is production code.
"""

import pytest

from catalog_match import expand
from catalog_match.identity import build_sku_spec
from catalog_match.models import Candidate, RankedCandidate
from catalog_match.score import score_candidate

from test_cm_expand import (  # noqa: F401  (the autouse fixture keeps every test offline)
    MAPPINGS, READ_MATCH, StubFetcher, StubPages, StubProvider, StubVerifier, _offline, cand, health_of, hit,
    packshot_png,
)
from catalog_match import pipeline

UNMAPPED = build_sku_spec({"name": "BISBELL BB2208 S/STEEL ICE CREAM SCOOP WITH CLIP", "brand": "BISBELL"}, {})
TYPO = build_sku_spec({"name": "SQ SALITED DRY PRAWNS 200G", "brand": "SQ SALITED"}, {})
BOUQUET = build_sku_spec({"name": "FERNS N PETALS BLUE ORCHIDS BOUQUET 8PCS", "brand": "FERNS N PETALS"}, {})
MAPPED = build_sku_spec({"name": "BARTS TRADITIONAL FRIES 1KG", "brand": "BARTS"}, MAPPINGS)

SCOOP_IMG = "https://m.media-amazon.com/images/I/51SQhomVwzL.jpg"
SCOOP_PAGE = "https://www.amazon.ae/Cream-Stainless-Plastic-Handle-Kitchen/dp/B0BTC3W9ZV"
BISBELL_PAGE = "https://bisbell.ae/product/s-steel-ice-cream-scoop-with-clip-bb2208/"
BISBELL_IMG = "https://bisbell.ae/wp-content/uploads/2023/11/BB2208.jpg"
READ_SCOOP = dict(READ_MATCH, brand_text="Bisbell", variant_text="Ice Cream Scoop", size_text="")


def ranked(spec, *cands):
    return [RankedCandidate(candidate=c, score=score_candidate(spec, c)) for c in cands]


def run(spec, main_cands, *, web=(), shop=(), docs=None, bodies=None, readings=None):
    web_p = StubProvider("serper_web", list(web))
    shop_p = StubProvider("serper_shopping", list(shop))
    exp = expand.Expansion(web=web_p, shopping=shop_p, visual=None, pages=StubPages(docs or {}), max_calls=4)
    outcome = pipeline.find_product_image(spec, providers=[StubProvider("serper", main_cands)],
                                          fetcher=StubFetcher(bodies or {}), verifier=StubVerifier(readings or {}),
                                          expansion=exp)
    return outcome, web_p, shop_p


# ---------------------------------------------------------------------------
# The rules
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name, expected", [
    ("FERNS N PETALS BLUE ORCHIDS BOUQUET 8PCS", "flowers"),
    ("FERNS N PETALS MAJESTIC ROSES RED 50PCS", "flowers"),
    ("GARDEN TULIP FLOWERS ROSE BEAUTY RED ROSE BOUQUET", "flowers"),
    ("باقة ورود حمراء 12 حبة", "flowers"),
    ("CORTAS ROSE WATER 300ML", ""),                 # a grocery: singular 'rose', and water
    ("ROSES SYRUP 750ML", ""),                       # 'unless' keeps the round
    ("WILD FLOWERS HONEY 500G", ""),
    ("ALMARAI FRESH MILK 1L", ""),
])
def test_out_of_scope_words(name, expected):
    assert expand.out_of_scope(build_sku_spec({"name": name, "brand": "X BRAND"}, {})) == expected


def test_gate_reasons():
    other = cand("https://img.example-cdn.com/scoop.jpg", "Stainless Steel Ice Cream Scoop", SCOOP_PAGE)
    assert expand.scope_gate(BOUQUET, []) == "out_of_scope:flowers"
    # an unmapped brand no listing names: one shopping probe only
    assert expand.scope_gate(UNMAPPED, ranked(UNMAPPED, other)) == expand.BRAND_NOT_FOUND
    # a mapped brand is never gated that way: a store page Google Images did not rank is what the round is for
    assert expand.scope_gate(MAPPED, ranked(MAPPED, other)) == ""
    # a listing names the brand on a UAE store, even one the identity rules reject (another size): no gate
    prawns = cand("https://img.example-cdn.com/p.jpg", "Dry Prawns 200g", "https://www.noon.com/uae-en/p/p/")
    rejected = cand("https://img.example-cdn.com/b2.jpg", "SQ Salited Dry Prawns 500g", SCOOP_PAGE)
    assert score_candidate(TYPO, rejected).hard_reject
    assert expand.scope_gate(TYPO, ranked(TYPO, prawns)) == expand.BRAND_NOT_FOUND
    assert expand.scope_gate(TYPO, ranked(TYPO, prawns, rejected)) == ""
    # ... but not when that rejected listing is on an unknown site (no store, no brand site)
    blog = cand("https://img.example-cdn.com/c.jpg", "SQ Salited Dry Prawns 500g", "https://someblog.example.com/p")
    assert expand.scope_gate(TYPO, ranked(TYPO, prawns, blog)) == expand.BRAND_NOT_FOUND
    # a site named after the brand names it, whatever its title says
    own = cand("https://bisbell.ae/wp-content/uploads/a.jpg", "S/Steel Ice Cream Scoop With Clip", BISBELL_PAGE)
    assert expand.host_names_brand(UNMAPPED, "bisbell.ae") and not expand.host_names_brand(UNMAPPED, "noon.com")
    assert score_candidate(UNMAPPED, own).tier == 3
    assert expand.scope_gate(UNMAPPED, ranked(UNMAPPED, other, own)) == ""


# ---------------------------------------------------------------------------
# The round with the gate
# ---------------------------------------------------------------------------

def test_a_bouquet_spends_no_paid_call():
    roses = cand("https://img.example-cdn.com/roses.jpg", "Blue Orchids Bouquet", "https://florist.example.com/o")
    outcome, web, shop = run(BOUQUET, [roses])
    assert web.calls == [] and shop.calls == []
    assert {p for p, _, _ in health_of(outcome)} == {"serper"}                 # the normal flow only


def test_a_typo_brand_gets_one_shopping_probe_and_stops():
    other = cand("https://img.example-cdn.com/prawns.jpg", "Dry Prawns 200g", "https://www.noon.com/uae-en/x/p/")
    outcome, web, shop = run(TYPO, [other], web=[hit("https://www.luluhypermarket.com/en-ae/x/p/1", "Dry Prawns")],
                             shop=[hit("https://shop.example.ae/prawns", "Salted Dry Prawns 200g",
                                       provider="serper_shopping", image_url="https://img.example-cdn.com/s.jpg")])
    assert shop.calls == ["SQ SALITED DRY PRAWNS 200g"]
    assert web.calls == []
    assert health_of(outcome)[-1] == ("serper_shopping", "X2", "ok")
    assert outcome.decision in ("REVIEW_UNSELECTED", "NOT_FOUND")


def test_the_probe_that_finds_the_brand_store_keeps_the_round_and_its_pick():
    """'BISBELL BB2208 ...' (live run 2026-10-04, row 91): Google Images had only other brands' scoops; Google
    Shopping found the brand's own store, whose page gave the pick."""
    other = cand(SCOOP_IMG, "Ice Cream Scoop, Stainless Steel Fruit Ice Cream Scoop Spoon", SCOOP_PAGE)
    listing = hit(BISBELL_PAGE, "S/Steel Ice Cream Scoop With Clip | Bisbell", provider="serper_shopping",
                  image_url=BISBELL_IMG)
    outcome, web, shop = run(UNMAPPED, [other], shop=[listing], web=[],
                             bodies={BISBELL_IMG: packshot_png(9), SCOOP_IMG: packshot_png(4)},
                             readings={BISBELL_IMG: READ_SCOOP})
    assert len(shop.calls) == 1 and len(web.calls) == 1                         # the probe, then X1
    assert outcome.decision == "REVIEW_PRESELECTED"
    assert outcome.winner.candidate.image_url == BISBELL_IMG
    assert [q for _, q, _ in health_of(outcome)][-2:] == ["X2", "X1"]


def test_the_gate_off_keeps_every_paid_step(monkeypatch):
    monkeypatch.setenv("EXPANSION_SCOPE_GATE", "false")
    roses = cand("https://img.example-cdn.com/roses.jpg", "Blue Orchids Bouquet", "https://florist.example.com/o")
    _, web, shop = run(BOUQUET, [roses])
    assert len(web.calls) >= 1 and len(shop.calls) == 1
    other = cand("https://img.example-cdn.com/prawns.jpg", "Dry Prawns 200g", "https://www.noon.com/uae-en/x/p/")
    _, web, shop = run(TYPO, [other])
    assert len(web.calls) >= 1 and len(shop.calls) == 1


def test_x0_page_recovery_still_runs_for_a_gated_product(monkeypatch):
    """The gate stops the paid steps only: a bouquet listing whose picture failed still gets its page read (free)."""
    page = "https://www.flowers.ae/blue-orchids-bouquet-8pcs"
    listing = cand("https://img.example-cdn.com/broken.jpg", "Ferns N Petals Blue Orchids Bouquet 8 pcs", page)
    doc = ('<html><head><title>Blue Orchids Bouquet</title><script type="application/ld+json">'
           '{"@type": "Product", "name": "Ferns N Petals Blue Orchids Bouquet 8 pcs", '
           '"image": "https://www.flowers.ae/img/orchids-1200.jpg"}</script></head></html>')
    outcome, web, shop = run(BOUQUET, [listing], docs={page: doc},
                             bodies={"https://www.flowers.ae/img/orchids-1200.jpg": packshot_png(7)})
    assert web.calls == [] and shop.calls == []
    assert any(rc.candidate.image_url == "https://www.flowers.ae/img/orchids-1200.jpg" for rc in outcome.ranked)


def test_names_brand_reads_the_snippet_and_the_host():
    c = Candidate(image_url="https://img.example-cdn.com/x.jpg", page_url="https://www.noon.com/uae-en/x/p/",
                  title="Ice Cream Scoop", snippet="By Bisbell, stainless steel")
    assert expand.names_brand(UNMAPPED, c)
    c2 = Candidate(image_url="https://cdn.bisbell.ae/x.jpg", title="Scoop")
    assert expand.names_brand(UNMAPPED, c2)
    assert not expand.names_brand(UNMAPPED, Candidate(image_url="https://img.example-cdn.com/y.jpg", title="Scoop"))
