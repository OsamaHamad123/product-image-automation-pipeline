"""catalog_match.score: identity tiers, hard rejects and the D4 rank key.

Every candidate here is scored by the real score_candidate(); nothing is stubbed.
The named cases come from the WP-2 plan and the audit dossier proofs
(p6 'ag' vs images, s1 slug brand / 'nada' vs canada / Al Ain city, p7 Arabic brand,
s4 sibling beats exact, t4/g2 size grammar, MISS-5 parent brand).
"""

import socket

import pytest

from catalog_match.identity import build_sku_spec
from catalog_match.models import Candidate, QualityReport
from catalog_match.score import rank, rank_key, score_candidate


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def _blocked(*args, **kwargs):
        raise RuntimeError("network access is blocked in catalog_match unit tests")

    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)
    monkeypatch.setattr(socket, "getaddrinfo", _blocked)


def _row(brand, synonyms, competitors, **extra):
    syns = [s.strip() for s in synonyms.split(",") if s.strip()]
    if brand not in syns:
        syns.insert(0, brand)
    row = {"brand": brand, "synonyms": syns,
           "excluded_competitors": [c.strip() for c in competitors.split(",") if c.strip()]}
    row.update(extra)
    return row


MAPPINGS = {
    "meliha": _row("Meliha", "Mleiha, مليحة, مليحه", "Almarai, Sutas, Koita, Lacnor, Baladna, Al Rawabi, Nadec, Nada"),
    "mai dubai": _row("Mai Dubai", "May Dubai, ماي دبي, مي دبي, مياه دبي", "Masafi, Al Ain, Oasis, Arwa, Aquafina, Nestle Pure Life, Voss, Evian"),
    "almarai": _row("Almarai", "Al Marai, المراعي", "Sutas, Koita, Lacnor, Baladna, Al Rawabi, Nadec, Nada, Meliha, Mleiha"),
    "masafi": _row("Masafi", "مسافي", "Al Ain, Oasis, Arwa, Aquafina, Nestle Pure Life, Mai Dubai, Voss, Evian"),
    "al ain": _row("Al Ain", "العين, alain", "Masafi, Oasis, Arwa, Aquafina, Nestle Pure Life, Mai Dubai, Voss, Evian"),
    "al rawabi": _row("Al Rawabi", "الروابي, Alrawabi", "Almarai, Nadec, Al Ain"),
    "american garden": _row("American Garden", "A/G, AG, أمريكان جاردن", "Heinz"),
    "nestle": _row("Nestle", "نستله", "", sub_brands=["Nido", "KitKat"]),
    "heinz": _row("Heinz", "هاينز", "American Garden"),
    "pepsi": _row("Pepsi", "بيبسي", "Coca Cola"),
}


def spec_for(name, brand, **row):
    return build_sku_spec(dict(row, name=name, brand=brand), MAPPINGS)


def cand(image_url="https://cdn.example.com/p/1.jpg", **kw):
    return Candidate(image_url=image_url, **kw)


# ---------------------------------------------------------------------------
# Named cases (WP-2 acceptance)
# ---------------------------------------------------------------------------

def test_al_rawabi_arabic_title_and_carrefour_slug_is_tier1():
    spec = spec_for("Al Rawabi Laban Up 180ml", "Al Rawabi")
    c = cand("https://cdn.mafrservices.com/sys-master-root/h7a/9087/abc_main.jpg",
             title="لبن أب الروابي 180 مل",
             page_url="https://www.carrefouruae.com/mafuae/en/laban/al-rawabi-laban-up-180ml/p/123456",
             domain="carrefouruae.com")
    sc = score_candidate(spec, c)
    assert sc.hard_reject == ()
    assert sc.tier == 1
    assert sc.matched["brand"] is True
    assert set(sc.matched["brand_fields"]) >= {"title", "page_slug"}   # Arabic title AND hyphenated slug
    assert sc.size_status == "match"
    assert sc.matched["source_class"] == "uae_retailer"


def test_ag_vs_heinz_images_package_is_competitor():
    spec = spec_for("A/G Mayonnaise 473ml", "A/G")
    heinz = cand("https://cdn.shop.com/images/heinz-mayonnaise-package.jpg")
    sc = score_candidate(spec, heinz)
    assert "competitor_brand" in sc.hard_reject
    assert sc.tier is None
    assert sc.matched["brand"] is False              # 'ag' never matched 'images' / 'package'
    titled = cand("https://cdn.shop.com/images/x.jpg", title="Heinz Mayonnaise 400ml")
    assert "competitor_brand" in score_candidate(spec, titled).hard_reject
    # the correct product is matched through the full brand name
    good = cand("https://cdn.shop.com/images/y.jpg", title="American Garden Mayonnaise 473ml",
                page_url="https://www.luluhypermarket.com/en-ae/american-garden-mayonnaise-473ml/p/1")
    gsc = score_candidate(spec, good)
    assert gsc.tier == 1 and gsc.matched["brand"] is True


def test_masafi_talabat_al_ain_city_is_not_a_competitor():
    spec = spec_for("Masafi Water 1.5L", "Masafi")
    c = cand("https://images.deliveryhero.io/image/talabat/nv/masafi.jpg",
             title="Masafi Water 1.5L - delivery in Dubai, Abu Dhabi & Al Ain",
             page_url="https://www.talabat.com/uae/grocery/masafi-water-1-5l",
             domain="talabat.com")
    sc = score_candidate(spec, c)
    assert sc.hard_reject == ()
    assert sc.tier == 1


def test_canada_slug_is_not_nada():
    spec = spec_for("Almarai Full Fat Milk 1L", "Almarai")
    slug_only = cand("https://cdn.shop.com/img/123.jpg",
                     page_url="https://shop.example.ae/almarai-full-fat-milk-canada/p/123")
    sc = score_candidate(spec, slug_only)
    assert sc.hard_reject == ()
    assert sc.matched["brand"] is True
    in_image_path = cand("https://cdn.shop.com/almarai-full-fat-milk-canada/123.jpg", title="Almarai Full Fat Milk 1L")
    assert score_candidate(spec, in_image_path).hard_reject == ()
    # control: a real 'Nada' listing with no Almarai evidence IS rejected
    nada = cand("https://cdn.shop.com/img/9.jpg", title="Nada Full Fat Milk 1L")
    assert "competitor_brand" in score_candidate(spec, nada).hard_reject


def test_full_fat_vs_low_fat_is_variant_conflict():
    spec = spec_for("Almarai Full Fat Milk 1L", "Almarai")
    c = cand(title="Almarai Low Fat Milk 1L", page_url="https://www.carrefouruae.com/mafuae/en/almarai-low-fat-milk-1l/p/2")
    sc = score_candidate(spec, c)
    assert "variant_conflict:fat" in sc.hard_reject
    assert sc.tier is None


def test_pepsi_2_25l_vs_24x330ml_is_rejected():
    spec = spec_for("Pepsi 2.25L", "Pepsi")
    c = cand(title="Pepsi Soft Drink Can 24x330ml", page_url="https://www.noon.com/uae-en/pepsi-24x330ml/N1/p")
    sc = score_candidate(spec, c)
    assert sc.tier is None
    assert "size_conflict" in sc.hard_reject
    assert "pack_conflict" in sc.hard_reject


def test_laban_180_vs_200_is_size_conflict():
    spec = spec_for("Almarai Laban 180ml", "Almarai")
    sc = score_candidate(spec, cand(title="Almarai Laban 200ml"))
    assert "size_conflict" in sc.hard_reject
    assert sc.size_status == "conflict"
    # the 20 % tolerance of the old parser treated these as equal; 180 vs 180 still matches
    assert score_candidate(spec, cand(title="Almarai Laban 180 ml")).size_status == "match"


def test_url_only_size_conflict_is_soft_and_caps_tier():
    spec = spec_for("Almarai Full Fat Milk 1L", "Almarai")
    conflict = cand(title="Almarai Full Fat Milk 1L",
                    page_url="https://www.carrefouruae.com/mafuae/en/almarai-full-fat-milk-2l/p/1")
    sc = score_candidate(spec, conflict)
    assert sc.hard_reject == ()
    assert sc.url_only_size_conflict is True
    assert sc.tier is not None and sc.tier <= 2
    # control: the same candidate with an agreeing slug is tier 1, so the cap is what demoted it
    agree = cand(title="Almarai Full Fat Milk 1L",
                 page_url="https://www.carrefouruae.com/mafuae/en/almarai-full-fat-milk-1l/p/1")
    assert score_candidate(spec, agree).tier == 1
    # a URL-only conflict with no title size is still not a reject
    no_title_size = cand(title="Almarai Full Fat Milk",
                         page_url="https://www.carrefouruae.com/mafuae/en/almarai-full-fat-milk-2l/p/1")
    sc = score_candidate(spec, no_title_size)
    assert sc.hard_reject == () and sc.url_only_size_conflict and sc.tier == 2


def test_multipack_target_and_counted_products():
    six = spec_for("Almarai Laban 6 x 180ml", "Almarai")
    page = "https://www.carrefouruae.com/mafuae/en/laban/p/9"
    assert score_candidate(six, cand(title="Almarai Laban 6x180ml", page_url=page)).tier == 1
    four = score_candidate(six, cand(title="Almarai Laban 4 x 180ml", page_url=page))
    assert "pack_conflict" in four.hard_reject
    # a single-bottle title for a 6-pack SKU is not a conflict, but it cannot prove the pack (no tier 1)
    single = score_candidate(six, cand(title="Almarai Laban 180ml", page_url=page))
    assert single.hard_reject == () and single.tier == 2
    # a counted product: the count is the size, never a 'pack' of the target
    eggs = spec_for("Al Jazira Fresh Eggs 30 pcs", "Al Jazira")
    assert score_candidate(eggs, cand(title="Al Jazira Fresh Eggs 30 pcs", page_url=page)).tier == 1
    fifteen = score_candidate(eggs, cand(title="Al Jazira Fresh Eggs 15 pcs", page_url=page))
    assert "size_conflict" in fifteen.hard_reject and "pack_conflict" not in fifteen.hard_reject


def test_no_evidence_is_tier3():
    spec = spec_for("Almarai Full Fat Milk 1L", "Almarai")
    sc = score_candidate(spec, cand("https://f.nooncdn.com/p/v1612345678/N40123456A_1.jpg"))
    assert sc.hard_reject == ()
    assert sc.tier == 3
    assert sc.size_status == "unknown"


def test_image_directories_are_not_evidence():
    # only the image FILENAME is evidence; CDN directories ('images/', 'brands/almarai/') are not
    spec = spec_for("Almarai Full Fat Milk 1L", "Almarai")
    sc = score_candidate(spec, cand("https://cdn.shop.ae/brands/almarai/banner-2024.jpg"))
    assert sc.matched["brand"] is False and sc.tier == 3
    named = score_candidate(spec, cand("https://cdn.shop.ae/uploads/almarai-full-fat-milk-1l.jpg"))
    assert named.matched["brand"] is True and named.size_status == "match"


def test_tier1_needs_a_trusted_page():
    spec = spec_for("Almarai Full Fat Milk 1L", "Almarai")
    title = "Almarai Full Fat Milk 1L"
    assert score_candidate(spec, cand(title=title, page_url="https://www.carrefouruae.com/mafuae/en/a/p/1")).tier == 1
    assert score_candidate(spec, cand(title=title, page_url="https://www.amazon.com/dp/B01")).tier == 2
    assert score_candidate(spec, cand(title=title, page_url="https://milk-recipes.example.com/post")).tier == 2
    assert score_candidate(spec, cand(title=title)).tier == 2
    # and coverage of the product words: a milk-powder page for a milk SKU is not enough
    powder = spec_for("Almarai Full Fat Milk Powder 2.25kg", "Almarai")
    thin = cand(title="Almarai Full Fat Powder 2.25kg", page_url="https://www.noon.com/uae-en/x/p")
    assert score_candidate(powder, thin).matched["coverage"] == 0.0
    assert score_candidate(powder, thin).tier == 2


def test_parent_brand_nestle_matches_nido_title():
    spec = spec_for("Nido Fortified Milk Powder 2.25kg", "Nestle")
    c = cand(title="Nido Fortified Full Cream Milk Powder 2.25kg",
             page_url="https://www.noon.com/uae-en/nido-fortified-full-cream-milk-powder-2-25kg/N11/p")
    sc = score_candidate(spec, c)
    assert sc.matched["brand"] is True
    assert sc.hard_reject == ()
    assert sc.tier == 1
    # 2.25 kg vs 2.5 kg is a different SKU
    sibling = cand(title="Nido Fortified Milk Powder 2.5kg")
    assert "size_conflict" in score_candidate(spec, sibling).hard_reject


def test_gtin_on_page():
    spec = spec_for("Drinking Water 500ml", "Mai Dubai", barcode="6297000611365")
    same = cand("https://images.openfoodfacts.org/images/products/629/700/061/1365/front.jpg",
                page_title="Drinking water 500 ml", provider="off", gtin_on_page="6297000611365",
                page_url="https://world.openfoodfacts.org/product/6297000611365")
    sc = score_candidate(spec, same)
    assert sc.tier == 1
    assert sc.matched["gtin"] == "match"
    other = cand(page_title="Drinking water 500 ml", provider="off", gtin_on_page="4006381333931")
    osc = score_candidate(spec, other)
    assert "gtin_mismatch" in osc.hard_reject
    assert osc.tier is None


# ---------------------------------------------------------------------------
# Dossier regressions and the remaining hard rules
# ---------------------------------------------------------------------------

def test_arabic_sheet_brand_matches_english_title():
    # p7: brand 'المراعي' rejected every correct 'Almarai ...' result
    spec = spec_for("Almarai Fresh Milk Full Fat 1L", "المراعي")
    c = cand(title="Almarai Fresh Milk Full Fat 1L",
             page_url="https://www.carrefouruae.com/mafuae/en/fresh-milk/almarai-fresh-milk-full-fat-1l/p/5")
    sc = score_candidate(spec, c)
    assert sc.tier == 1 and sc.matched["brand"] is True


def test_al_ain_brand_vs_masafi_and_alain_slug():
    spec = spec_for("Al Ain Water 1.5L", "Al Ain")
    assert "competitor_brand" in score_candidate(spec, cand(title="Masafi Water 1.5L")).hard_reject
    sc = score_candidate(spec, cand(page_url="https://www.spinneys.com/en-ae/alain-water-1-5l/p/8"))
    assert sc.matched["brand"] is True and sc.size_status == "match" and sc.hard_reject == ()


def test_slug_decimal_is_not_a_conflict():
    # SRC-3: '.../al-ain-water-1-5l/...' used to parse as 5 L and hard-reject the correct item
    spec = spec_for("Al Ain Water 1.5L", "Al Ain")
    c = cand("https://cdn.shop.ae/al-ain-water-1-5l.jpg", page_url="https://shop.ae/al-ain-water-1-5l/p/7")
    sc = score_candidate(spec, c)
    assert sc.hard_reject == ()
    assert sc.url_only_size_conflict is False
    assert sc.size_status == "match"


def test_separate_fields_do_not_invent_sizes():
    spec = spec_for("Almarai Laban 180ml", "Almarai")
    sc = score_candidate(spec, cand("https://cdn.shop.ae/assets/g.jpg", title="Almarai Laban",
                                    page_url="https://shop.ae/p/1"))
    assert sc.size_status == "unknown" and not sc.url_only_size_conflict and sc.hard_reject == ()


def test_multi_size_listing_is_ambiguous_not_conflict():
    spec = spec_for("Tilda Basmati Rice 5kg", "Tilda")
    listing = cand(title="Tilda Basmati Rice 5kg, 10kg, 20kg",
                   page_url="https://www.carrefouruae.com/mafuae/en/tilda-basmati-rice/p/3")
    sc = score_candidate(spec, listing)
    assert sc.hard_reject == ()
    assert sc.size_status == "ambiguous"
    assert sc.tier == 2
    # 'rice' is not found in 'price': the banner covers none of the product words
    banner = score_candidate(spec, cand(title="Tilda price offers"))
    assert banner.matched["coverage"] == 0.0 and banner.tier == 2


def test_tang_is_not_tangerine():
    spec = spec_for("Tang Orange Drink Powder 2kg", "Tang")
    sc = score_candidate(spec, cand(title="Al Rawabi Tangerine Juice 1L"))
    assert sc.matched["brand"] is False
    assert "competitor_brand" in sc.hard_reject


def test_uploads_directory_is_not_evidence():
    # s1 case 2: 'up' (Laban Up) matched 'wp-content/uploads' and boosted a brand banner
    spec = spec_for("Al Rawabi Laban Up 180ml", "Al Rawabi")
    banner = cand("https://alrawabi.ae/wp-content/uploads/2023/05/home-banner.jpg", title="Al Rawabi Dairy")
    sc = score_candidate(spec, banner)
    assert sc.matched["coverage"] == 0.0
    assert sc.tier == 2


def test_sibling_flavour_and_unstated_marked_variant():
    spec = spec_for("Almarai Laban Strawberry 180ml", "Almarai")
    plain = score_candidate(spec, cand(title="Almarai Laban Plain 180ml"))
    assert "variant_conflict:flavour" in plain.hard_reject
    # a target without flavour: a flavoured listing is not rejected but cannot be tier 1
    unflavoured = spec_for("Almarai Laban 180ml", "Almarai")
    sc = score_candidate(unflavoured, cand(title="Almarai Laban Strawberry 180ml",
                                           page_url="https://www.carrefouruae.com/mafuae/en/almarai-laban-strawberry-180ml/p/4"))
    assert sc.hard_reject == () and sc.tier == 2
    assert "unstated_variant:flavour" in sc.conflicts


def test_stock_and_reviewer_negative():
    spec = spec_for("Almarai Full Fat Milk 1L", "Almarai")
    stock = cand("https://image.shutterstock.com/image-photo/milk-carton-260nw-1.jpg",
                 title="Almarai Full Fat Milk 1L", page_url="https://www.shutterstock.com/image-photo/milk-1")
    assert "stock_or_clipart" in score_candidate(spec, stock).hard_reject
    clipart = cand(title="Milk carton clipart")
    assert "stock_or_clipart" in score_candidate(spec, clipart).hard_reject
    url = "https://cdn.shop.ae/img/almarai-1l.jpg"
    rejected = score_candidate(spec, cand(url, title="Almarai Full Fat Milk 1L"),
                               negatives=["https://www.CDN.shop.ae/img/almarai-1l.jpg?w=300"])
    assert "reviewer_negative" in rejected.hard_reject and rejected.tier is None
    kept = score_candidate(spec, cand(url, title="Almarai Full Fat Milk 1L"), negatives=["https://cdn.shop.ae/img/other.jpg"])
    assert kept.hard_reject == ()


def test_competitor_in_page_slug_only():
    spec = spec_for("Almarai Fresh Milk 1L", "Almarai")
    sc = score_candidate(spec, cand(page_url="https://www.noon.com/uae-en/nadec-fresh-milk-1l/N123/p"))
    assert "competitor_brand" in sc.hard_reject


def test_unknown_brand_never_triggers_competitor_rule():
    spec = spec_for("Tomato Paste 400g", "")
    assert spec.brand_conf == "none"
    sc = score_candidate(spec, cand(title="Heinz Tomato Paste 400g"))
    assert sc.hard_reject == () and sc.tier == 3


def test_source_trust_order():
    spec = build_sku_spec({"name": "Almarai Full Fat Milk 1L", "brand": "Almarai"},
                          dict(MAPPINGS, almarai=dict(MAPPINGS["almarai"], official_domains=["almarai.com"])))
    trust = lambda page: score_candidate(spec, cand(title="Almarai Full Fat Milk 1L", page_url=page)).matched["source_trust"]
    official = trust("https://www.almarai.com/en/products/milk")
    uae = trust("https://www.noon.com/uae-en/x/p")
    other = trust("https://www.amazon.com/dp/B000")
    generic = trust("https://someblog.example.com/milk")
    assert official > uae > other > generic
    off = score_candidate(spec, cand(title="Almarai Full Fat Milk 1L", provider="off")).matched["source_trust"]
    assert uae > off > other
    # the image CDN host is not a page domain
    cdn_only = cand("https://cdn.mafrservices.com/a.jpg", title="Almarai Full Fat Milk 1L", domain="cdn.mafrservices.com")
    assert score_candidate(spec, cdn_only).matched["source_trust"] == generic


# ---------------------------------------------------------------------------
# Brands that are also common words (live run 2026-09-30, row 34 'FRESHLY')
# ---------------------------------------------------------------------------

FRESHLY = build_sku_spec({"name": "FRESHLY CHICKEN SHAWARMA 350GM", "brand": "FRESHLY"}, {})
FAMILY = build_sku_spec({"name": "FAMILY LIGHT TUNA BRINE 185GM", "brand": "FAMILY"}, {})
CARREFOUR_PAGE = "https://www.carrefouruae.com/mafuae/en/p/1"


def listing(title, page_url=CARREFOUR_PAGE, image_url="https://cdn.example.com/p/1.jpg", **kw):
    return cand(image_url, title=title, page_title=title, page_url=page_url, **kw)


def position_conflicts(sc):
    return [c for c in sc.conflicts if c.startswith("generic_brand_position:")]


@pytest.mark.parametrize("spec, title, page_url", [
    (FRESHLY, "Freshly Chicken Shawarma 350g - Carrefour UAE", CARREFOUR_PAGE),
    (FRESHLY, "Buy Freshly Chicken Shawarma 350g Online | Lulu UAE", "https://www.luluhypermarket.com/en-ae/p/2"),
    (FAMILY, "Family Light Meat Tuna in Brine 185g : Amazon.ae: Grocery", "https://www.amazon.ae/dp/B0C1"),
    (FAMILY, "Light Meat Tuna in Brine 185g", "https://www.carrefouruae.com/family-light-meat-tuna-brine-185g/p/123"),
])
def test_common_word_brand_where_a_brand_stands_is_tier1(spec, title, page_url):
    sc = score_candidate(spec, listing(title, page_url))
    assert sc.tier == 1
    assert position_conflicts(sc) == []


def test_common_word_brand_elsewhere_keeps_the_brand_but_not_tier1():
    # row 34: other brands' listings carry 'freshly' as a word; only the brand position differs
    for title in ("Seara Chicken Shawarma 350g, freshly prepared - Carrefour UAE",
                  "Zingo Chicken Shawarma 350g | Freshly made in the UAE | Lulu UAE"):
        sc = score_candidate(FRESHLY, listing(title))
        assert sc.tier == 2 and sc.matched["brand"] is True and sc.hard_reject == ()
        assert position_conflicts(sc) == ["generic_brand_position:title", "generic_brand_position:page_title"]
    frozen = score_candidate(FRESHLY, listing("Seara Chicken Shawarma 350g Freshly Frozen"))
    assert frozen.tier == 2 and "generic_brand_position:title" in frozen.conflicts
    # a slug that does not open with the brand, and the image filename, are no brand position either
    slug = score_candidate(FRESHLY, listing(
        "Chicken Shawarma 350g", "https://www.carrefouruae.com/mafuae/en/chicken-shawarma-350g-freshly-prepared/p/9"))
    assert slug.tier == 2 and position_conflicts(slug) == ["generic_brand_position:page_slug"]
    image = score_candidate(FRESHLY, listing("Chicken Shawarma 350g",
                                             image_url="https://cdn.example.com/chicken-shawarma-freshly-350g.jpg"))
    assert image.tier == 2 and position_conflicts(image) == ["generic_brand_position:image_file"]
    # control: the same text opened by the brand is tier 1
    assert score_candidate(FRESHLY, listing("Freshly Chicken Shawarma 350g, freshly prepared - Carrefour UAE")).tier == 1


def test_common_word_brand_on_its_official_domain_is_tier1():
    spec = build_sku_spec({"name": "FRESHLY CHICKEN SHAWARMA 350GM", "brand": "Freshly"},
                          {"freshly": {"brand": "Freshly", "official_domains": ["freshly-foods.ae"]}})
    title = "Chicken Shawarma 350g - freshly made by Freshly"
    assert score_candidate(spec, listing(title, "https://www.freshly-foods.ae/products/chicken-shawarma")).tier == 1
    assert score_candidate(spec, listing(title)).tier == 2          # the same text on a retailer page


def test_common_word_brand_that_is_also_a_store_name():
    # 'Target' is a store name: the brand is tried before store names are skipped
    target = build_sku_spec({"name": "TARGET CHICKEN LUNCHEON 340GM", "brand": "TARGET"}, {})
    for title in ("Target Chicken Luncheon 340g - Carrefour UAE", "Buy Target Chicken Luncheon 340g Online | Lulu UAE"):
        sc = score_candidate(target, listing(title))
        assert sc.tier == 1 and position_conflicts(sc) == [], title


def test_distinctive_phrase_of_a_common_word_brand_counts_anywhere():
    spec = build_sku_spec({"name": "FAMILY SKIPJACK CHUNKS 185GM", "brand": "FAMILY"},
                          {"family": {"brand": "Family", "synonyms": ["فاميلي"]}})
    assert score_candidate(spec, listing("Skipjack Tuna Chunks 185g - Family")).tier == 2
    assert score_candidate(spec, listing("Skipjack Tuna Chunks 185g - Family فاميلي")).tier == 1


def test_common_word_out_of_place_does_not_corroborate_a_gtin():
    spec = build_sku_spec({"name": "FRESHLY CHICKEN SHAWARMA 350GM", "brand": "FRESHLY",
                           "barcode": "6297000611365"}, {})
    record = dict(provider="off", gtin_on_page="6297000611365",
                  page_url="https://world.openfoodfacts.org/product/6297000611365")
    # the product words still corroborate the GTIN match ...
    covered = score_candidate(spec, listing("Seara Chicken Shawarma 350 g, freshly prepared", **record))
    assert covered.tier == 1 and covered.matched["gtin"] == "match"
    # ... a common word out of place does not
    bare = score_candidate(spec, listing("Seara Snack, freshly packed", **record))
    assert bare.tier == 2 and bare.matched["gtin"] == "match" and bare.matched["brand"] is True


def test_distinctive_brand_is_full_evidence_anywhere():
    spec = spec_for("Almarai Full Fat Milk 1L", "Almarai")
    sc = score_candidate(spec, listing("Full Fat Milk 1L from Almarai", "https://www.noon.com/uae-en/full-fat-milk-1l/N1/p"))
    assert sc.tier == 1 and position_conflicts(sc) == []


# ---------------------------------------------------------------------------
# Ranking (D4 lexicographic key)
# ---------------------------------------------------------------------------

def test_rank_identity_before_quality():
    spec = spec_for("Almarai Laban Strawberry 180ml", "Almarai")
    exact = cand("https://cdn.a.ae/exact.jpg", title="Almarai Laban Strawberry 180ml",
                 page_url="https://www.carrefouruae.com/mafuae/en/almarai-laban-strawberry-180ml/p/1")
    sibling = cand("https://cdn.b.ae/sibling.jpg", title="Almarai Laban 180ml",
                   page_url="https://www.noon.com/uae-en/almarai-laban-180ml/N2/p")
    s_exact, s_sibling = score_candidate(spec, exact), score_candidate(spec, sibling)
    assert (s_exact.tier, s_sibling.tier) == (1, 2)          # the premise of the test
    quality = {exact.image_url: 0.2, sibling.image_url: 0.95}
    ordered = rank([(sibling, s_sibling), (exact, s_exact)], quality)
    assert [c.image_url for c, _ in ordered] == [exact.image_url, sibling.image_url]


def test_rank_s4_sharper_plain_sibling_loses():
    # s4: exact 'Almarai Laban Strawberry 180ml' at 700px vs sharper 'Almarai Laban Plain' at 1600px
    spec = spec_for("Almarai Laban Strawberry 180ml", "Almarai")
    exact = cand("https://cdn.a.ae/strawberry.jpg", title="Almarai Laban Strawberry 180ml", width=700, height=700)
    plain = cand("https://cdn.a.ae/plain.jpg", title="Almarai Laban Plain", width=1600, height=1600)
    scored = [(plain, score_candidate(spec, plain)), (exact, score_candidate(spec, exact))]
    ordered = rank(scored, {plain: QualityReport(hard_ok=True, quality_score=0.99),
                            exact: QualityReport(hard_ok=True, quality_score=0.3)})
    assert ordered[0][0] is exact
    assert ordered[-1][0] is plain and ordered[-1][1].tier is None


def test_rank_key_order_within_a_tier():
    spec = spec_for("Almarai Full Fat Milk 1L", "Almarai")
    size_match = cand("https://x/1.jpg", title="Almarai Milk 1L", page_url="https://someblog.example.com/a")
    size_unknown = cand("https://x/2.jpg", title="Almarai Full Fat Milk", page_url="https://www.carrefouruae.com/mafuae/en/x/p/1")
    s1, s2 = score_candidate(spec, size_match), score_candidate(spec, size_unknown)
    assert s1.tier == s2.tier == 2
    # size match outranks variants, trust and a much better photo
    ordered = rank([(size_unknown, s2), (size_match, s1)], {size_unknown.image_url: 1.0, size_match.image_url: 0.0})
    assert ordered[0][0] is size_match
    # with identical identity evidence, consensus then quality break the tie
    twin_a = cand("https://x/a.jpg", title="Almarai Full Fat Milk 1L", consensus_count=1)
    twin_b = cand("https://x/b.jpg", title="Almarai Full Fat Milk 1L", consensus_count=3)
    twin_c = cand("https://x/c.jpg", title="Almarai Full Fat Milk 1L", consensus_count=1)
    scored = [(t, score_candidate(spec, t)) for t in (twin_a, twin_b, twin_c)]
    ordered = rank(scored, {twin_a.image_url: 0.1, twin_c.image_url: 0.9})
    assert [c.image_url for c, _ in ordered] == [twin_b.image_url, twin_c.image_url, twin_a.image_url]
    # quality alone never lifts a rejected candidate
    rejected = cand("https://x/r.jpg", title="Almarai Low Fat Milk 1L")
    ordered = rank(scored + [(rejected, score_candidate(spec, rejected))], {rejected.image_url: 1.0})
    assert ordered[-1][0] is rejected
    assert rank_key(twin_b, scored[1][1]) < rank_key(twin_a, scored[0][1])
