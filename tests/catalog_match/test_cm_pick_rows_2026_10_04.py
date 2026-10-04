"""Live run of 2026-10-04 (laqta_run_2026-10-04_1619.json): rows left without a pick although the right picture was
among the candidates, and the rows that must stay without one.

Every test is built from the real row: the sheet name and brand, the listing titles, page and image URLs and the
label reader's readings are copied from the run export, in its rank order. Listings are scored again with
score.score_candidate (the recorded tiers are asserted), readings are classified with verify.make_verdict.

    rows 76, 83   brand_refuted turned the tier-1 fallback off although the candidate's own label read the brand
    rows 62, 71, 73  tier-2 UNSURE (size not legible on the front) with corroborated listing evidence
    row 15 (50, 51)  one unit of a multipack SKU read size 'no': UNSURE with a review warning, not MISMATCH
    rows 10, 31   must stay unselected (image file name says 2.5Kg / no size in the listing)
"""

import socket

import pytest

from catalog_match import decide, settings
from catalog_match.identity import build_sku_spec
from catalog_match.models import (
    Candidate, FetchedImage, ProviderHealth, QualityReport, RankedCandidate, VerificationResult,
)
from catalog_match.score import score_candidate
from catalog_match.verify import MISMATCH, UNSURE, classify, make_verdict, multipack_unit_image

OK = VerificationResult(status="ok", calls=1)
HEALTHY = [ProviderHealth("serper", "ok", 200, query_id="Q1")]


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError(f"network access attempted: {args!r}")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    monkeypatch.setattr(settings, "_config", None)
    monkeypatch.setenv("AUTO_PUBLISH_ENABLED", "false")
    monkeypatch.setenv("AUTO_PUBLISH_BRANDS", "")
    monkeypatch.setenv("GTIN_POLICY", "evidence")


def spec_of(name, brand, mappings=None):
    return build_sku_spec({"name": name, "brand": brand}, mappings or {})


def read(brand, variant, size="", view="front_packshot", brand_match="yes", variant_match="yes",
         size_match="unsure", pack=1):
    """One label reading as the export records it (vlm texts + label_reader flags)."""
    return {"brand_text": brand, "variant_text": variant, "size_text": size, "pack_count": pack, "view": view,
            "brand_match": brand_match, "variant_match": variant_match, "size_match": size_match}


def listing(spec, n, title, page_url, image_url, reading=None, size=(1000, 1000), fetch_ok=True):
    """A candidate scored again from its recorded title and URLs, fetched, read by the label reader."""
    cand = Candidate(image_url=image_url, page_url=page_url, title=title, provider="serper", query_id="Q1", rank=n)
    fetched = FetchedImage(candidate=cand, ok=fetch_ok, error=None if fetch_ok else "timeout",
                           width=size[0] if fetch_ok else None, height=size[1] if fetch_ok else None)
    quality = QualityReport(hard_ok=True, quality_score=0.8) if fetch_ok else None
    verdict = make_verdict(spec, n - 1, reading) if reading is not None else None
    return RankedCandidate(candidate=cand, score=score_candidate(spec, cand), fetched=fetched, quality=quality,
                           verdict=verdict)


def route(spec, rcs):
    """decide.route on the candidates in the run's rank order."""
    return decide.route(spec, list(rcs), OK, HEALTHY, set())


def warns(rc):
    return decide.warning_codes(rc.reasons)


# ---------------------------------------------------------------------------
# brand_refuted no longer hides a candidate whose own label reads the brand (rows 76, 83)
# ---------------------------------------------------------------------------

ZWAN_TURKEY = spec_of("ZWAN TURKEY LUNCHEON MEAT WITH HERB850GM", "ZWAN")
CARREFOUR_TURKEY = "https://www.carrefouruae.com/mafuae/en/poultry-luncheon/zwan-luncheon-turkey-meat-850gr/p/484953"
CARREFOUR_TURKEY_TITLE = "Buy Zwan Turkey Luncheon Meat With Herbs, 850g Online | Carrefour UAE"


def row76():
    s = ZWAN_TURKEY
    return [
        listing(s, 1, CARREFOUR_TURKEY_TITLE, CARREFOUR_TURKEY,
                "https://cdn.mafrservices.com/pim-content/UAE/media/product/1289344/1780338000/1289344_main.jpg",
                read("ZWAN", "LUNCHEON", view="multi_product", variant_match="unsure", pack=2), (1700, 1700)),
        listing(s, 2, CARREFOUR_TURKEY_TITLE, CARREFOUR_TURKEY,
                "https://cdn.mafrservices.com/pim-content/UAE/media/product/484953/1780496400/484953_main.jpg",
                read("ZWAN", "TURKEY LUNCHEON MEAT WITH HERBS"), (1700, 1700)),
        listing(s, 3, "Zwan Turkey Luncheon Meat With Herbs 850 g",
                "https://gcc.luluhypermarket.com/en-ae/zwan-turkey-luncheon-meat-with-herbs-850-g/p/215829",
                "https://bf1af2.akinoncloudcdn.com/products/2024/09/20/123372/905410e1-1236-4f73-9f9e-e58bd630632f.jpg",
                read("ZWAN", "TURKEY LUNCHEON MEAT WITH HERBS"), (1500, 1500)),
        # a related product's picture on the same Carrefour page: another brand's pack
        listing(s, 4, CARREFOUR_TURKEY_TITLE, CARREFOUR_TURKEY,
                "https://cdn.mafrservices.com/pim-content/UAE/media/product/1737702/1742025603/1737702_main.jpg",
                read("Bordon", "Chicken Luncheon Meat", "320g", brand_match="no", variant_match="no",
                     size_match="no"), (1700, 1700)),
    ]


def test_row76_the_label_that_reads_zwan_is_preselected_despite_a_bordon_picture_on_the_page():
    rcs = row76()
    assert [rc.score.tier for rc in rcs] == [1, 1, 1, 1]
    assert [rc.verdict.decision for rc in rcs] == [MISMATCH, UNSURE, UNSURE, MISMATCH]
    assert decide.brand_refuted(rcs), "the Bordon pack on a tier-1 listing still refutes the listing text"
    out = route(ZWAN_TURKEY, rcs)
    assert out.decision == "REVIEW_PRESELECTED"
    assert out.winner is rcs[1]                       # the first tier-1 UNSURE whose own label reads ZWAN
    assert "preselected:tier1_unsure" in out.winner.reasons and "warn:vlm_unsure" in out.winner.reasons
    assert not any("vlm:tier1_brand_refuted" in rc.reasons for rc in rcs)
    assert "vlm:tier1_brand_refuted" not in out.reject_counts
    assert rcs[3].status == "rejected"


def test_row76_lulu_is_picked_when_it_is_the_only_label_that_reads_the_brand():
    rcs = row76()
    del rcs[1]                                        # without the Carrefour front picture
    out = route(ZWAN_TURKEY, rcs)
    assert out.decision == "REVIEW_PRESELECTED"
    assert out.winner.candidate.title == "Zwan Turkey Luncheon Meat With Herbs 850 g"


MARA = spec_of("MARA MIXED VEGETABLES 400GM", "MARA")
AMAZON_MARA = "https://www.amazon.ae/Mara-Mixed-Vegetable-Tomato-Sauce/dp/B086CNB76D"
AMAZON_MARA_TITLE = "Mara Mixed Vegetable with Tomato Sauce 400 Gm: Buy Online at Best Pric"
LULU_MARA = "https://gcc.luluhypermarket.com/en-ae/mara-mixed-vegetables-with-tomato-sauce-400-g/p/151446"
LULU_MARA_TITLE = "Mara Mixed Vegetables With Tomato Sauce 400 g Online at Best Price | L"


def test_row83_mara_label_is_preselected_despite_a_giovanni_picture_on_the_amazon_page():
    s = MARA
    rcs = [
        listing(s, 1, AMAZON_MARA_TITLE, AMAZON_MARA, "https://m.media-amazon.com/images/I/61joB4pgtBL.jpg",
                read("I MARA", "MIXED VEGETABLES WITH LEGUMES AND TOMATO SAUCE"), (1073, 1244)),
        listing(s, 2, AMAZON_MARA_TITLE, AMAZON_MARA, "https://m.media-amazon.com/images/I/71DqlTlY8GL.jpg",
                read("MARA", "MIXED VEGETABLES", view="multi_product", pack=3), (2560, 1708)),
        listing(s, 3, LULU_MARA_TITLE, LULU_MARA,
                "https://bf1af2.akinoncloudcdn.com/products/2024/09/11/70112/218156cc-6aa6-4997-b2f3-b61c1c015503.jpg",
                read("MARA", "Mixed vegetables with tomato sauce", variant_match="no"), (1500, 1500)),
        listing(s, 4, LULU_MARA_TITLE, LULU_MARA,
                "https://bf1af2.akinoncloudcdn.com/products/2024/09/11/70112/d0300bd8-78bb-4197-a404-01a02f118f51"
                "_size1920x1920_cropCenter.jpg",
                read("MARA", "Mixed Vegetable with beans and Tomato Sauce", "400g", view="other_side",
                     size_match="yes"), (1920, 1920)),
        listing(s, 8, AMAZON_MARA_TITLE, AMAZON_MARA,
                "https://images-eu.ssl-images-amazon.com/images/I/812UbMQWJ2L.jpg",
                read("GIOVANNI", "MIX VEGETABLES", "400 g", brand_match="no", size_match="yes"), (1751, 2560)),
    ]
    assert all(rc.score.tier == 1 for rc in rcs)
    assert decide.brand_refuted(rcs)
    out = route(s, rcs)
    assert out.decision == "REVIEW_PRESELECTED" and out.winner is rcs[0]
    assert "preselected:tier1_unsure" in out.winner.reasons
    assert "vlm:tier1_brand_refuted" not in out.reject_counts


# -- the Freshly protection stays: an unconfirmed label never rides on refuted listing text --------------

FRESHLY = spec_of("FRESHLY CHICKEN SHAWARMA MEAT 350GM", "FRESHLY")


def freshly(n, reading):
    return listing(FRESHLY, n, f"Freshly Chicken Shawarma 350g pack {n} - Carrefour UAE",
                   f"https://www.carrefouruae.com/mafuae/en/freshly-chicken-shawarma-350g/p/{n}",
                   f"https://cdn.mafrservices.com/freshly/{n}.jpg", reading)


@pytest.mark.parametrize("brand_text,brand_match,picked", [
    ("", "unsure", False),            # no brand read on the fallback's own label
    ("Sadia", "yes", False),          # 'yes' but the printed brand is not Freshly
    ("Freshly", "unsure", False),     # the brand printed but the reader did not confirm it
    ("Freshly", "yes", True),         # its own label reads Freshly: it may be pre-checked
])
def test_freshly_refuted_tier1_fallback_needs_its_own_label_to_read_the_brand(brand_text, brand_match, picked):
    other = freshly(1, read("Seara", "Chicken Shawarma", "350 g", brand_match="no", size_match="yes"))
    fallback = freshly(2, read(brand_text, "Chicken Shawarma", view="lifestyle", brand_match=brand_match))
    assert other.score.tier == 1 and fallback.score.tier == 1
    assert fallback.verdict.decision == UNSURE
    out = route(FRESHLY, [other, fallback])
    if picked:
        assert out.decision == "REVIEW_PRESELECTED" and out.winner is fallback
        assert "vlm:tier1_brand_refuted" not in fallback.reasons
    else:
        assert out.decision == "REVIEW_UNSELECTED" and out.winner is None
        assert "vlm:tier1_brand_refuted" in fallback.reasons
        assert out.reject_counts["vlm:tier1_brand_refuted"] == 1


# ---------------------------------------------------------------------------
# The tier-2 fallback: a tier-2 UNSURE label with corroborated listing evidence (rows 62, 71, 73)
# ---------------------------------------------------------------------------

ZWAN_CHICKEN_HS = spec_of("ZWAN CHICEKN LUNCHEON MEAT HOT&SPIC850GM", "ZWAN")


def row62(spec=ZWAN_CHICKEN_HS, **change):
    s = spec
    return [
        listing(s, 1, "Zwan Luncheon Meat Chicken Hot & Spicy - 850 gm : Amazon.ae: Grocery",
                "https://www.amazon.ae/Zwan-Luncheon-Meat-Chicken-Spicy/dp/B07P67PCC2",
                "https://m.media-amazon.com/images/I/61ntgxA0SXL.jpg",
                dict(read("ZWAN", "LUNCHEON MEAT HOT & SPICY"), **change), (2000, 2000)),
        listing(s, 2, "Zwan Chicken Luncheon Meat Hot and Spicy, 850g",
                "https://www.carrefouruae.com/mafuae/en/poultry-luncheon/zwan-luncheon-meat-chicken-h-850gr/p/11027",
                "https://cdn.mafrservices.com/pim-content/UAE/media/product/11027/1780338000/11027_main.jpg",
                dict(read("ZWAN", "CHICKEN LUNCHEON MEAT HOT & SPICY"), **change), (1700, 1700)),
        listing(s, 3, "Zwan Hot & Spicy Chicken Luncheon Meat Value Pack 850 g Online at Best",
                "https://gcc.luluhypermarket.com/en-ae/zwan-hot-spicy-chicken-luncheon-meat-value-pack-850-g/p/559376/",
                "https://bf1af2.akinoncloudcdn.com/products/2026/01/20/633389/571060c4-c6ad-48d5-a7c2-54f788d3b4f6.jpg",
                dict(read("زوان", "لحم لانشون حارة و مبهرة"), **change), (1500, 1500)),
        listing(s, 7, "Zwan Luncheon Meat, 850 g: Buy Online at Best Price in UAE - Amazon.ae",
                "https://www.amazon.ae/Zwan-Luncheon-Meat-850-g/dp/B07P8B2XHT",
                "https://m.media-amazon.com/images/I/71Yuz46zvQL.jpg",
                read("زوان", "لنشون", variant_match="no"), (1600, 1600)),
    ]


def test_row62_tier2_label_corroborated_by_two_stores_is_preselected():
    rcs = row62()
    assert [rc.score.tier for rc in rcs] == [2, 2, 2, 2]
    assert [rc.verdict.decision for rc in rcs] == [UNSURE, UNSURE, UNSURE, MISMATCH]
    out = route(ZWAN_CHICKEN_HS, rcs)
    assert out.decision == "REVIEW_PRESELECTED" and out.failure_code is None
    assert out.winner is rcs[0]
    assert "preselected:tier2_corroborated" in out.winner.reasons
    # every review warning stays: the label states variants the sheet's typos hide, and nothing is a MATCH
    assert {"vlm_unsure", "sheet_silent:flavour=chili"} <= set(warns(out.winner))
    assert "auto_blocked:not_tier1" in out.winner.reasons and "auto_blocked:not_vlm_match" in out.winner.reasons


def test_the_tier2_fallback_never_auto_publishes(monkeypatch):
    monkeypatch.setenv("AUTO_PUBLISH_ENABLED", "true")
    monkeypatch.setenv("AUTO_PUBLISH_BRANDS", "*")
    mapped = spec_of("ZWAN CHICEKN LUNCHEON MEAT HOT&SPIC850GM", "ZWAN", {"zwan": {"brand": "Zwan"}})
    assert mapped.brand_conf == "mapped"
    out = route(mapped, row62(mapped))
    assert out.decision == "REVIEW_PRESELECTED"
    assert "preselected:tier2_corroborated" in out.winner.reasons
    blocked = sorted(r for r in out.winner.reasons if r.startswith("auto_blocked:"))
    assert blocked == ["auto_blocked:not_tier1", "auto_blocked:not_vlm_match"]
    assert "auto_publish" not in out.winner.reasons


@pytest.mark.parametrize("change", [
    {"view": "other_side"},                         # not a front packshot
    {"brand_text": ""},                             # the label does not show the brand
    {"pack_count": 6},                              # another pack counted on the picture
    {"variant_match": "unsure"},                    # 'HOT & SPICY' printed, the sheet states no variant: needs 'yes'
    {"size_text": "340 g"},                         # another size printed (MISMATCH anyway)
    {"size_text": "1 L"},                           # a size in another dimension
])
def test_row62_a_label_that_leaves_more_than_the_size_open_is_not_enough(change):
    out = route(ZWAN_CHICKEN_HS, row62(**change))
    assert out.decision == "REVIEW_UNSELECTED" and out.winner is None


ZWAN_BEEF_HS = spec_of("ZWAN LUNCHEON MEAT BEEF HOT&SPICY 340GM", "ZWAN")


def row71(with_corroboration=True):
    s = ZWAN_BEEF_HS
    rcs = [
        listing(s, 1, "Buy Zwan Luncheon Meat Beef Hot & Spicy 340G - Shop On The Fresh Marke",
                "https://www.thefreshmarketdubai.com/products/zwan-luncheon-meat-beef-hot-spicy-340g-8714555001239"
                "?srsltid=AU7gw4USPIvBskO_TrzSVyw8eWz7g9NADK86eiNkPJz7kUomh8UjkrbA",
                "https://www.thefreshmarketdubai.com/cdn/shop/files/8714555001239_1a0dc558-7cbf-488f-b474-30405633a7d8"
                ".jpg?v=1763105066&width=1000",
                read("ZWAN", "LUNCHEON MEAT HOT & SPICY")),
        listing(s, 2, "Zwan Beef Luncheon Hot And Spicy 340g",
                "https://martoo.com/product/zwan-beef-luncheon-hot-and-spicy-340g/",
                "https://martoo.com/wp-content/uploads/2025/09/Zwan-Beef-Luncheon-Hot-And-Spicy-24x340g.jpg",
                read("ZWAN", "LUNCHEON MEAT HOT & SPICY", view="multi_product")),
        listing(s, 3, "Zwan Luncheon Meat Hot And Spicy 340grams | Best Price UAE | Dubai, Ab",
                "https://www.noon.com/uae-en/luncheon-meat-hot-and-spicy-340grams/N27683383A/p/",
                "https://f.nooncdn.com/p/pnsku/N27683383A/45/_/1736700492/df78f353-45b1-45fd-a08f-cf5c29a5bc3b.jpg",
                read("زوان", "لحم لنشون حارة و مبهرة"), (660, 900)),
        listing(s, 5, "Buy Zwan Luncheon Meat, 340g Online | Carrefour UAE",
                "https://www.carrefouruae.com/mafuae/en/beef-luncheon/zwan-luncheon-meat-340gr/p/10935",
                "https://cdn.mafrservices.com/pim-content/UAE/media/product/10935/1757135408/10935_main.jpg",
                read("ZWAN", "LUNCHEON", variant_match="no"), (1700, 1700)),
    ]
    if with_corroboration:
        palmyra = listing(s, 4, "Zwan Luncheon Meat Hot And Spicy 340g",
                          "https://palmyraorders.com/products/zwan-luncheon-meat-hot-and-spicy-340-g",
                          "https://palmyraorders.com/cdn/shop/files/zwan-luncheon-meat-hot-and-spicy-340g-shop-your-"
                          "daily-fresh-products-free-delivery.jpg?v=1723634019&width=1000",
                          read("ZWAN", "LUNCHEON MEAT HOT & SPICY"))
        rcs.insert(3, palmyra)
    return rcs


def test_row71_a_generic_store_listing_corroborated_by_another_store_is_preselected():
    rcs = row71()
    assert all(rc.score.tier == 2 for rc in rcs)
    assert rcs[1].score.url_only_size_conflict, "martoo's image file says 24x340g"
    out = route(ZWAN_BEEF_HS, rcs)
    assert out.decision == "REVIEW_PRESELECTED" and out.winner is rcs[0]
    assert out.winner.candidate.title.startswith("Buy Zwan Luncheon Meat Beef Hot & Spicy 340G")
    assert "preselected:tier2_corroborated" in out.winner.reasons and "vlm_unsure" in warns(out.winner)


def test_row71_a_single_generic_store_without_corroboration_stays_unselected():
    # the noon label is in Arabic (no Arabic brand on the sheet: not read as the brand), the rest show other
    # products: one generic domain alone is not enough
    rcs = row71(with_corroboration=False)
    out = route(ZWAN_BEEF_HS, rcs)
    assert out.decision == "REVIEW_UNSELECTED" and out.winner is None
    assert rcs[0].status == "eligible"


ZWAN_TANDOORI = spec_of("ZWAN LUNCHEON MEAT TANDOORI 200GM", "ZWAN")


def test_row73_tandoori_luncheon_meat_is_preselected_in_rank_order():
    s = ZWAN_TANDOORI
    rcs = [
        listing(s, 1, "Magic Trading | Zwan luncheon meat tandoori 200g",
                "https://magic-sl.com/zwan-luncheon-meat-tandoori-200g-",
                "https://magic-sl.com/cache/original/product/11089/diGIUU1zrQKZsCpq97gJZUa5LRP136jj93Nh0Lev.jpg",
                read("ZWAN", "CHICKEN LUNCHEON MEAT TANDOORI"), (619, 368)),
        listing(s, 2, "zwan chicken lanchon meat, 200 g Price | Buy Online in Dubai, UAE - Union  Coop",
                "https://www.unioncoop.ae/luncheon-meat-chicken-200gm-2434830.html",
                "https://www.unioncoop.ae/media/catalog/product/z/w/zwan-tandoori-chicken-luncheon-meat-200-g_hero.jpg",
                read("ZWAN", "CHICKEN LUNCHEON MEAT TANDOORI", "200ge", size_match="yes")),
        listing(s, 4, "Zwan Chicken Tandoori Luncheon Meat 200 g",
                "https://gcc.luluhypermarket.com/en-ae/zwan-chicken-tandoori-luncheon-meat-200-g/p/566018",
                "https://bf1af2.akinoncloudcdn.com/products/2024/09/11/65060/46680861-6ee5-4a21-9515-c922ee68f5d9"
                "_size1920x1920_cropCenter.jpg",
                read("زوان", "لحم لنشون دجاج تندوري"), (1920, 1920)),
        listing(s, 5, "Zwan Chicken Tandoori Luncheon Meat 200g: Buy Online at Best Price in UAE -  Amazon.ae",
                "https://www.amazon.ae/Zwan-Chicken-Tandoori-Luncheon-Meat/dp/B07NF7W5MV",
                "https://m.media-amazon.com/images/I/61cG4cWQ-7L.jpg", read("ZWAN", "تندوري"), (2000, 2000)),
        listing(s, 6, "Zwan Chicken Tandoori Luncheon Meat 200 g Online at Best Price | Lulu UAE",
                "https://gcc.luluhypermarket.com/en-ae/zwan-chicken-tandoori-luncheon-meat-200-g/p/566018",
                "https://bf1af2.akinoncloudcdn.com/products/2024/09/11/65060/888eb80b-dc7d-424c-b8dc-b54d1cf93066.jpg",
                read("ZWAN", "TANDOORI"), (1500, 1500)),
    ]
    assert [rc.score.tier for rc in rcs] == [2, 2, 2, 2, 2]
    out = route(s, rcs)
    assert out.decision == "REVIEW_PRESELECTED" and out.winner is rcs[0]
    assert "preselected:tier2_corroborated" in out.winner.reasons
    # the small picture is pre-checked with its warnings; the reviewer may choose the larger Lulu one
    assert {"vlm_unsure", "low_resolution", "sheet_silent:protein=chicken"} <= set(warns(out.winner))
    assert rcs[4].status == "eligible"


def test_a_trusted_store_alone_corroborates_a_tier2_label():
    # row 62's Amazon listing alone: a UAE retailer's page (score's trusted_page) needs no second domain
    rcs = row62()[:1]
    assert rcs[0].score.matched["source_class"] == "uae_retailer"
    out = route(ZWAN_CHICKEN_HS, rcs)
    assert out.decision == "REVIEW_PRESELECTED" and "preselected:tier2_corroborated" in out.winner.reasons


def test_a_variant_doubt_on_the_listing_is_never_picked_by_the_tier2_fallback():
    # row 71 with a chicken picture file on the first store: the label reading is not asked about the file name
    rcs = row71()
    first = rcs[0]
    doubtful = listing(ZWAN_BEEF_HS, 1, first.candidate.title, first.candidate.page_url,
                       "https://www.thefreshmarketdubai.com/cdn/shop/files/zwan-chicken-luncheon-meat-340g.jpg",
                       read("ZWAN", "LUNCHEON MEAT HOT & SPICY"))
    assert any(str(c).startswith("image_variant_conflict") for c in doubtful.score.conflicts)
    out = route(ZWAN_BEEF_HS, [doubtful] + rcs[1:])
    assert out.decision == "REVIEW_PRESELECTED"
    assert out.winner.candidate.page_url.startswith("https://palmyraorders.com/")


def test_a_differing_page_barcode_is_never_picked_by_the_tier2_fallback():
    s = build_sku_spec({"name": "ZWAN LUNCHEON MEAT BEEF HOT&SPICY 340GM", "brand": "ZWAN",
                        "barcode": "8714555001239"}, {})

    def palmyra(n, gtin):
        cand = Candidate(image_url=f"https://palmyraorders.com/cdn/shop/files/zwan-{n}.jpg",
                         page_url=f"https://palmyraorders.com/products/zwan-luncheon-meat-hot-and-spicy-340-g-{n}",
                         title="Zwan Luncheon Meat Hot And Spicy 340g", provider="serper", rank=n, gtin_on_page=gtin)
        return RankedCandidate(candidate=cand, score=score_candidate(s, cand),
                               fetched=FetchedImage(candidate=cand, ok=True, width=1000, height=1000),
                               quality=QualityReport(hard_ok=True),
                               verdict=make_verdict(s, n, read("ZWAN", "LUNCHEON MEAT HOT & SPICY")))

    fresh = row71()[0].candidate                       # the label reads ZWAN on a second domain too
    other_store = listing(s, 3, fresh.title, fresh.page_url, fresh.image_url, read("ZWAN", "LUNCHEON MEAT HOT & SPICY"))
    conflicted = palmyra(1, "8714555000041")           # the plain beef luncheon meat's barcode
    assert decide.gtin_conflict(conflicted) and conflicted.score.tier == 2
    out = route(s, [conflicted, other_store])
    assert conflicted.status == "eligible" and out.winner is other_store   # the second store, never the conflict
    clean = palmyra(2, None)
    assert clean.score.tier == 2
    out = route(s, [clean, other_store])
    assert out.winner is clean and "preselected:tier2_corroborated" in clean.reasons


# ---------------------------------------------------------------------------
# The listing must state the size itself (rows 10 and 31 stay unselected)
# ---------------------------------------------------------------------------

MR_JOHN = spec_of("MR JOHN FRENCH FRIES 900GM", "MR JOHN")
RAWABI_FRIES = ("Rawabi Hypermarket - Qatar's Trusted Retailer Online",
                "https://rawabihypermarket.com/french-fries/1/11/65/486",
                "https://rawabihypermarket.com/uploads/product_images/featured_image/"
                "Mr._John_Chips_Frozen_(9Mm)__2.5Kg288959.jpg")


@pytest.mark.parametrize("brand_text", ["MISTER JOHN", "MR JOHN"])     # the real reading, and one that reads the brand
def test_row10_mr_john_stays_unselected_its_image_file_says_2_5kg(brand_text):
    s = MR_JOHN
    rawabi = listing(s, 2, *RAWABI_FRIES, read(brand_text, "FRENCH FRIES"))
    # a second store reading the same label: still no listing that states 900 g
    other = listing(s, 9, "Mr John French Fries", "https://shop.example-grocer.com/products/mr-john-french-fries",
                    "https://shop.example-grocer.com/img/mr-john-fries.jpg", read(brand_text, "FRENCH FRIES"))
    assert rawabi.score.tier == 2 and rawabi.score.url_only_size_conflict
    assert not decide.listing_states_size(rawabi) and not decide.listing_states_size(other)
    out = route(s, [rawabi, other])
    assert out.decision == "REVIEW_UNSELECTED" and out.winner is None


def test_row31_family_tuna_stays_unselected_no_listing_states_the_size():
    s = spec_of("FAMILY LIGHT TUNA MEAT CHUNK IN BRINE 185GM", "FAMILY")
    rc = listing(s, 4, "Family Fancy Skipjack Chunks Light Meat Tuna In Brine – myGroceryfinde",
                 "https://mygroceryfinder.com/products/family-fancy-skipjack-chunks-light-meat-tuna-in-brine",
                 "https://mygroceryfinder.com/cdn/shop/products/5441_FamilyFancySkipjackChunksLightMeatTunaInBrine"
                 "_580x.png?v=1620300264",
                 read("Family", "FANCY SKIPJACK CHUNKS PACK LIGHT MEAT TUNA IN BRINE"), (300, 300))
    assert rc.score.tier == 2 and rc.score.size_status == "unknown"
    assert rc.verdict.decision == UNSURE
    out = route(s, [rc])
    assert out.decision == "REVIEW_UNSELECTED" and out.winner is None


def test_a_size_only_in_the_image_file_name_does_not_prove_the_listing():
    s = ZWAN_CHICKEN_HS
    rc = listing(s, 1, "Zwan Chicken Luncheon Meat Hot & Spicy", "https://www.amazon.ae/dp/B07P67PCC2",
                 "https://m.media-amazon.com/images/I/zwan-chicken-luncheon-850g.jpg",
                 read("ZWAN", "CHICKEN LUNCHEON MEAT HOT & SPICY"))
    assert rc.score.size_status == "match" and rc.score.matched["size_fields"] == {"image_file": "match"}
    assert not decide.listing_states_size(rc)
    assert route(s, [rc]).decision == "REVIEW_UNSELECTED"


# ---------------------------------------------------------------------------
# One unit of a multipack SKU (rows 15, 50, 51)
# ---------------------------------------------------------------------------

MEHRAN_2X = spec_of("MEHRAN PLAIN PARATHA 2X400GM 5S", "MEHRAN")
YASMIN = ("Mehran Plain Paratha (5 pieces) 400 g | Yasmin Store",
          "https://yasminstore.com/en/8961102042560-mehran-plain-paratha-5-pieces-400-g",
          "https://cdn.salla.sa/dPKDdg/6a34731d-e9a2-44b3-bb12-e70617f86fba-667.05882352941x1000-"
          "MjtGhc72QgXXp7Gl4ddWYf1DmZRm9ajEMJEkOV3C.jpg")
YASMIN_READ = read("Mehran", "PLAIN PARATHA", "400g", size_match="no", pack=5)


def test_row15_one_paratha_pack_of_a_2x400g_sku_is_unsure_not_mismatch():
    assert (MEHRAN_2X.size.base_value, MEHRAN_2X.pack_count, MEHRAN_2X.size.pieces) == (400.0, 2, 5)
    rc = listing(MEHRAN_2X, 1, *YASMIN, YASMIN_READ, (667, 1000))
    assert rc.score.tier == 2
    assert rc.verdict.decision == UNSURE                          # was MISMATCH: the 'no' was only the pack
    assert multipack_unit_image(MEHRAN_2X, rc.verdict)
    out = route(MEHRAN_2X, [rc])
    # one generic store and nothing to corroborate it: shown to the reviewer, not pre-checked
    assert out.decision == "REVIEW_UNSELECTED" and rc.status == "eligible"
    assert "multipack_unit_image" in decide.candidate_warnings(MEHRAN_2X, rc)


SUPER_TASTY_3X = spec_of("SUPER T/MEAT SOLID TUNA SALT WATE3X185GM", "SUPER TASTY")   # the brand column fixed


def row50():
    s = SUPER_TASTY_3X
    return [
        listing(s, 2, "Buy Super Tasty Light Meat Solid Tuna In Salt Water 185g x 3 Pieces On",
                "https://www.tradeling.com/ae-en/product-details/super-tasty-light-meat-solid-tuna-in-salt-water-"
                "185g-x-3-pieces-66cecc1a3af83dca028710e1-5fba0c6142480f001bed85d4",
                "https://cfn-catalog-prod.tradeling.com/up/5fba0c6142480f001bed85d4/"
                "78c6699bd2074a977c9cab83a21b15d9.png",
                read("Super Tasty", "Light SOLID Tuna In Salt Water", "185 g", size_match="no", pack=None),
                (2626, 1534)),
        listing(s, 3, "Super Tasty Light Meat Solid Tuna in Salt Water, 3x185g",
                "https://onmart.ae/product/super-tasty-light-meat-solid-tuna-in-salt-water-3x185g/",
                "https://onmart.ae/wp-content/uploads/2026/05/image-removebg-preview-2026-01-06T132931.903.png",
                read("Super Tasty", "Light SOLID Tuna In Salt Water", "185 g", size_match="no"), (654, 382)),
    ]


def test_rows50_51_one_can_of_a_3x185g_sku_is_preselected_with_a_warning_once_corroborated():
    rcs = row50()
    assert all(rc.score.tier == 2 for rc in rcs)
    assert [rc.verdict.decision for rc in rcs] == [UNSURE, UNSURE]
    out = route(SUPER_TASTY_3X, rcs)
    assert out.decision == "REVIEW_PRESELECTED" and out.winner is rcs[0]
    assert "preselected:tier2_corroborated" in out.winner.reasons
    assert {"vlm_unsure", "multipack_unit_image"} <= set(warns(out.winner))


@pytest.mark.parametrize("size_text,pack", [
    ("6 x 185g", None),        # another multipack printed
    ("6 x 185 g", 6),
    ("185 g", 6),              # six cans counted on the picture
    ("170 g", 1),              # another unit size (a size conflict)
    ("", 1),                   # nothing printed: the 'no' is not explained
])
def test_a_multipack_reading_that_is_not_one_unit_of_the_sku_stays_mismatch(size_text, pack):
    reading = read("Super Tasty", "Light SOLID Tuna In Salt Water", size_text, size_match="no", pack=pack)
    v = make_verdict(SUPER_TASTY_3X, 0, reading)
    assert v.decision == MISMATCH and not multipack_unit_image(SUPER_TASTY_3X, v)


@pytest.mark.parametrize("change", [{"brand_match": "no"}, {"variant_match": "no"}, {"view": "banner"}])
def test_one_unit_never_excuses_another_no_or_a_banner(change):
    v = make_verdict(SUPER_TASTY_3X, 0, dict(read("Super Tasty", "Light SOLID Tuna In Salt Water", "185 g",
                                                   size_match="no"), **change))
    assert v.decision == MISMATCH


def test_one_unit_is_never_a_match_and_a_single_unit_sku_keeps_its_mismatch():
    v = make_verdict(SUPER_TASTY_3X, 0, read("Super Tasty", "Light SOLID Tuna In Salt Water", "185 g",
                                             size_match="no"))
    v.size_match = "yes"
    assert classify(SUPER_TASTY_3X, v) != "MATCH"            # pack 1 of a 3-pack is never a MATCH
    single = spec_of("SUPER TASTY MEAT SOLID TUNA SALT WATER 185GM", "SUPER TASTY")
    v = make_verdict(single, 0, read("Super Tasty", "Light SOLID Tuna In Salt Water", "185 g", size_match="no"))
    assert v.decision == MISMATCH


def test_one_unit_of_a_multipack_gets_no_strong_second_look():
    # it was a MISMATCH before (no second look): the strong reader cannot make one pack the 2-pack either
    from catalog_match.verifiers.cascade import needs_second_look

    unit = make_verdict(MEHRAN_2X, 0, YASMIN_READ)
    assert unit.decision == UNSURE and not needs_second_look(MEHRAN_2X, unit)
    unreadable = make_verdict(MEHRAN_2X, 0, read("Mehran", "PLAIN PARATHA", "", pack=None))
    assert unreadable.decision == UNSURE and needs_second_look(MEHRAN_2X, unreadable)
