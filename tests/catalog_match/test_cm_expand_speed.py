"""The expansion round and time (WP speed, change 4b).

* A row with no usable brand ('GENERIC / NO BRAND', or an empty cell and no brand in the name) never runs the round:
  every listing of such a row stays at tier 3 and none can be picked, so its paid calls could only be wasted.
* The round stops as soon as it has a winner: when X1 + X2 give a pick, the visual searches (and X5) are not made;
  without a pick it goes on exactly as before.

Stage doubles and the explicit Expansion come from test_cm_expand.py. Sockets are blocked for every test.
"""

import pytest

from catalog_match import expand, pipeline
from catalog_match.identity import build_sku_spec
from catalog_match.models import Candidate, SearchOutcome
from catalog_match.providers.lens import VisualSearch
from test_cm_expand import (  # noqa: F401  (the offline fixture and the doubles)
    EMBORG, INSTA, LULU_IMAGE, LULU_PAGE, MAPPINGS, READ_EMBORG, READ_MATCH, READ_SIZE_UNREADABLE, SPEC, StubFetcher, StubPages,
    StubProvider, StubVerifier, _offline, cand, health_of, hit, lulu_html, packshot_png,
)

NO_BRAND_ROW = {"name": "TRADITIONAL FRIES 1KG", "brand": "GENERIC / NO BRAND"}


def spec_of(row):
    return build_sku_spec(row, MAPPINGS)


def run_with(spec, main_cands, *, web=(), shop=(), lens=None, docs=None, bodies=None, readings=None, max_calls=4):
    web_p, shop_p = StubProvider("serper_web", list(web)), StubProvider("serper_shopping", list(shop))
    exp = expand.Expansion(web=web_p, shopping=shop_p, visual=lens, pages=StubPages(docs or {}), max_calls=max_calls)
    verifier = StubVerifier(readings or {})
    outcome = pipeline.find_product_image(spec, providers=[StubProvider("serper", main_cands)],
                                          fetcher=StubFetcher(bodies or {}), verifier=verifier, expansion=exp)
    return outcome, {"web": web_p, "shop": shop_p, "verifier": verifier, "exp": exp}


# ---------------------------------------------------------------------------
# No usable brand: no round
# ---------------------------------------------------------------------------

def test_the_placeholder_brand_row_is_what_the_tests_below_mean():
    spec = spec_of(NO_BRAND_ROW)
    assert spec.brand_placeholder and spec.brand_raw == "" and spec.brand_conf == "none" and not spec.match_brands
    assert expand.no_usable_brand(spec) is True
    assert expand.no_usable_brand(SPEC) is False


@pytest.mark.parametrize("row", [
    NO_BRAND_ROW,
    {"name": "TRADITIONAL FRIES 1KG", "brand": "N/A"},
    {"name": "TRADITIONAL FRIES 1KG", "brand": "-"},
    {"name": "TRADITIONAL FRIES 1KG", "brand": ""},
])
def test_a_row_with_no_usable_brand_makes_no_paid_call(row):
    spec = spec_of(row)
    assert expand.no_usable_brand(spec) is True
    listing = cand(EMBORG, "Emborg French Fries 1kg", "https://www.noon.com/uae-en/emborg-fries/p/")
    outcome, d = run_with(spec, [listing], web=[hit(LULU_PAGE, "Barts Traditional Fries 1kg | Lulu UAE")],
                          docs={LULU_PAGE: lulu_html()}, bodies={LULU_IMAGE: packshot_png(5)},
                          readings={LULU_IMAGE: READ_MATCH})
    assert d["web"].calls == [] and d["shop"].calls == []
    assert not [h for h in health_of(outcome) if h[1].startswith("X")]
    assert outcome.decision != "REVIEW_PRESELECTED" and outcome.winner is None
    assert "expansion" not in outcome.timings and {"retrieval", "total"} <= set(outcome.timings)


def test_the_skipped_round_says_why_and_a_branded_row_still_runs():
    exp = expand.Expansion(web=StubProvider("serper_web", []), max_calls=4)
    outcome = SearchOutcome(decision="NOT_FOUND", failure_code="NO_RESULTS")
    assert expand.trigger(outcome, exp, spec_of(NO_BRAND_ROW)) == expand.NO_BRAND
    assert expand.trigger(outcome, exp, SPEC) == "expand"
    assert expand.trigger(outcome, exp) == "expand"                    # no spec given: the earlier behaviour
    from catalog_match.expand import RoundInput, run_round

    def inp(spec):
        return RoundInput(spec=spec, exp=exp, outcome=outcome, ranked=[], results=[], health=[], relaxed_ids=set(),
                          pool=None, fetcher=None, verifier=None)

    skipped = run_round(inp(spec_of(NO_BRAND_ROW)))
    assert (skipped.ran, skipped.skipped, skipped.calls) == ("", "no_brand", 0)
    assert skipped.outcome is outcome


def test_a_valid_barcode_under_the_strict_policy_still_lets_a_brandless_row_expand(monkeypatch):
    """GTIN_POLICY 'strict' makes a page that carries the sheet's GTIN tier 2 without any brand: worth the calls."""
    row = dict(NO_BRAND_ROW, barcode="6281007035309")
    spec = spec_of(row)
    assert spec.gtin and expand.no_usable_brand(spec) is True          # the default policy: brand is the identity
    monkeypatch.setenv("GTIN_POLICY", "strict")
    assert expand.no_usable_brand(spec) is False
    monkeypatch.setenv("GTIN_POLICY", "off")
    assert expand.no_usable_brand(spec) is True


def test_a_brand_the_stores_spell_differently_is_a_usable_brand():
    spec = build_sku_spec({"name": "BARTS TRADITIONAL FRIES 1KG", "brand": "BARTZ"}, MAPPINGS)
    assert spec.brand_raw and expand.no_usable_brand(spec) is False


# ---------------------------------------------------------------------------
# Stop at the winner
# ---------------------------------------------------------------------------

CAR_IMG = "https://cdn.mafrservices.com/sys-master-root/h1/77811_main.jpg"
CAR_PAGE = "https://www.carrefouruae.com/mafuae/en/frozen-potato/barts-traditional-fries-1kg/p/77811"


def visual_backend():
    match = Candidate(image_url=CAR_IMG, page_url=CAR_PAGE, title="Barts Traditional Fries 1kg - Carrefour UAE",
                      snippet="Carrefour UAE", domain="carrefouruae.com", provider="lens_serper", rank=1,
                      width=1000, height=1000)
    return StubProvider("lens_serper", [match])


def test_a_pick_from_the_web_step_spares_the_visual_searches():
    insta = cand(INSTA, "Barts fries", "https://www.instagram.com/p/barts")           # a near-match: a seed exists
    backend = visual_backend()
    outcome, d = run_with(SPEC, [insta], web=[hit(LULU_PAGE, "Barts Traditional Fries 1kg | Lulu UAE")],
                          lens=VisualSearch([backend]), docs={LULU_PAGE: lulu_html()},
                          bodies={INSTA: packshot_png(9), LULU_IMAGE: packshot_png(5)},
                          readings={INSTA: READ_SIZE_UNREADABLE, LULU_IMAGE: READ_MATCH})
    assert outcome.decision == "REVIEW_PRESELECTED" and outcome.winner.candidate.image_url == LULU_IMAGE
    assert backend.calls == []                                                        # no visual search was paid for
    assert [h[:2] for h in health_of(outcome) if h[1].startswith("X")] == [("serper_web", "X1"),
                                                                           ("serper_shopping", "X2")]
    assert len(d["web"].calls) == 1                                                   # and no X5


@pytest.mark.parametrize("first_step_reading", [READ_EMBORG, READ_SIZE_UNREADABLE],
                         ids=["the page's picture is another brand", "a weak pick: tier 1 left UNSURE"])
def test_without_a_confident_pick_the_round_goes_on_to_the_visual_search_and_grows_the_pool_twice(first_step_reading):
    insta = cand(INSTA, "Barts fries", "https://www.instagram.com/p/barts")
    backend = visual_backend()
    outcome, d = run_with(SPEC, [insta], web=[hit(LULU_PAGE, "Barts Traditional Fries 1kg | Lulu UAE")],
                          lens=VisualSearch([backend]), docs={LULU_PAGE: lulu_html()},
                          bodies={INSTA: packshot_png(9), LULU_IMAGE: packshot_png(5), CAR_IMG: packshot_png(9, (1000, 1000))},
                          readings={INSTA: READ_SIZE_UNREADABLE, LULU_IMAGE: first_step_reading,
                                    CAR_IMG: READ_MATCH})
    assert backend.calls == [INSTA]                                                   # the seed is still the near-match
    assert outcome.decision == "REVIEW_PRESELECTED" and outcome.winner.candidate.image_url == CAR_IMG
    assert [h[:2] for h in health_of(outcome) if h[1].startswith("X")] == [
        ("serper_web", "X1"), ("serper_shopping", "X2"), ("lens_serper", "X3")]
    assert d["verifier"].calls[1] == [LULU_IMAGE] and d["verifier"].calls[2] == [CAR_IMG]
    assert LULU_IMAGE in [rc.candidate.image_url for rc in outcome.ranked]            # the first step's find is kept


def test_a_round_with_no_later_step_to_spare_grows_once_as_before():
    """No visual search configured and X5 not needed: nothing to spare, so one grow at the end, as it always was."""
    grows = []
    real = expand._grow

    def counting(inp, report, new, *args, **kwargs):
        grows.append(len(new))
        return real(inp, report, new, *args, **kwargs)

    expand._grow, saved = counting, expand._grow
    try:
        outcome, d = run_with(SPEC, [cand(EMBORG, "Emborg French Fries 1kg", "https://www.noon.com/uae-en/emborg-fries/p/")],
                              web=[hit(LULU_PAGE, "Barts Traditional Fries 1kg | Lulu UAE")],
                              docs={LULU_PAGE: lulu_html()}, bodies={LULU_IMAGE: packshot_png(5)},
                              readings={LULU_IMAGE: READ_MATCH})
    finally:
        expand._grow = saved
    assert grows == [1] and outcome.decision == "REVIEW_PRESELECTED"


def test_the_second_web_group_is_not_asked_when_the_first_step_already_has_a_pick():
    """X5 (the other retailers) is only for a row nothing promising turned up for; a pick spares it too."""
    outcome, d = run_with(SPEC, [], web=[hit(LULU_PAGE, "Barts Traditional Fries 1kg | Lulu UAE")],
                          docs={LULU_PAGE: lulu_html()}, bodies={LULU_IMAGE: packshot_png(5)},
                          readings={LULU_IMAGE: READ_MATCH})
    assert outcome.decision == "REVIEW_PRESELECTED" and len(d["web"].calls) == 1
