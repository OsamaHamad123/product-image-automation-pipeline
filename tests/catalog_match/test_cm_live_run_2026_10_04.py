"""Live run of 2026-10-04 (laqta_run_2026-10-04_1619.json, 100 rows, every brand 'sheet_raw'): the Brand column.

Names, brands, titles, URLs and label readings are the run's own. Sockets are blocked: every stage is a double.

* Rows 95-97, brand 'GENERIC / NO BRAND': searched as a brand ('GENERIC NO BRAND ICE CREAM CANDY 13g') and told
  to add it to Brands Mapping. A placeholder brand is no brand (brand_index.is_placeholder_brand): never in the
  queries, the matching or the label prompt, never 'brand_unknown'; the reason is 'no_brand'. The sku_key is kept.
* Rows 27-28, brand 'AMERICAN LIGHT' on 'AMERICAN LIGHT MEAT TUNA ...': the label reader read 'American', which
  'american light' never matched (UNSURE). The brand is matched as 'AMERICAN' ('LIGHT MEAT' is the meat grade),
  with a sheet note. Brands whose last word is a product word that does not open a phrase of the name stay whole.
* Row 79, brand 'SQ SALITED': only a sheet note ('SQ' is too short to match; 'SALITED' is 'SALTED').
* Rows 49-52 (SUP/T, SUPER T/, SUPER/T): brand discovery did find Super Tasty in the run (each row's third query,
  B1, and its expansion queries were written 'Super Tasty', and the label reader's 'Super Tasty' counted as the
  brand); the export showed discovered_brands [] because the trace never kept them and the export wrote []. The
  trace keeps them now, the export and the stored reason read them, and a remembered spelling writes the planned
  queries from Q1 (no 'SUPER T MEAT ...' first).
* Row 4, 'BATO FRENCH FRIES 900 MM': no size, so a 2.5 KG bag was pre-selected. A sheet note says '900 GM' is
  meant; a 9 mm cut ('FARMILA FRENCH FRIES 9MM 1KG') stays a cut and raises nothing.
"""

import json
import socket
import sys
from pathlib import Path

import pytest

from catalog_match import brand_discovery as bd
from catalog_match import explain, facade, pipeline, settings
from catalog_match.brand_index import PLACEHOLDER_BRANDS, build_index, is_placeholder_brand
from catalog_match.identity import build_sku_spec, matching_brand
from catalog_match.models import Candidate, FetchedImage, ProviderResult, VerificationResult
from catalog_match.query_plan import build_queries, relaxations
from catalog_match.score import score_candidate
from catalog_match.verify import MATCH, UNSURE, _brand_reading, build_prompt, make_verdict

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    def refuse(*args, **kwargs):
        raise RuntimeError("network access is blocked in catalog_match unit tests")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    monkeypatch.setattr(settings, "_config", None)
    monkeypatch.setenv("AUTO_PUBLISH_ENABLED", "false")
    monkeypatch.setenv("AUTO_PUBLISH_BRANDS", "")
    bd.forget_all()                      # each test is a run of its own
    yield
    bd.forget_all()


def spec_of(name, brand, mappings=None, **row):
    return build_sku_spec(dict({"name": name, "brand": brand}, **row), mappings or {})


def listing(title, page_url, image_url, n=1):
    return Candidate(image_url=image_url, page_url=page_url, title=title, page_title=title, provider="serper",
                     query_id="Q1", rank=n)


def reading(brand, variant, size, pack=1, view="front_packshot", brand_match="yes", variant_match="yes",
            size_match="yes"):
    return {"brand_text": brand, "variant_text": variant, "size_text": size, "pack_count": pack, "view": view,
            "brand_match": brand_match, "variant_match": variant_match, "size_match": size_match}


# ---------------------------------------------------------------------------
# Rows 95-97: 'GENERIC / NO BRAND' is no brand
# ---------------------------------------------------------------------------

ROW95 = ("ICE CREAM CANDY 13 GM", "GENERIC / NO BRAND")
ROW96 = ("ICE CREAM CONE MARSHAMELLOW", "GENERIC / NO BRAND")
ROW97 = ("ICE CREAM MARSHAMALLO JELLY FILLING 12GM", "GENERIC / NO BRAND")


@pytest.mark.parametrize("cell", list(PLACEHOLDER_BRANDS) + [
    "generic / no brand", "GENERIC/NO BRAND", "No-Brand", "NOBRAND", " n.a. ", "None", "—", "NO BRAND / GENERIC",
    "بدون ماركه",
])
def test_a_placeholder_brand_cell_is_recognised_whatever_its_case_spacing_or_punctuation(cell):
    assert is_placeholder_brand(cell)


@pytest.mark.parametrize("cell", ["", "  ", "AIDA", "NADA", "SUPER/T", "SUP/T", "A/G", "7UP", "GENERIC MILLS",
                                  "NO NAME", "NONE SUCH", "AMERICAN LIGHT", "المراعي"])
def test_a_real_brand_or_an_empty_cell_is_no_placeholder(cell):
    assert not is_placeholder_brand(cell)


@pytest.mark.parametrize("name, brand, key", [ROW95 + ("c2ebe3007937919c",), ROW96 + (None,), ROW97 + (None,)])
def test_rows_95_97_have_no_brand_and_keep_their_key(name, brand, key):
    spec = spec_of(name, brand)
    assert (spec.brand_conf, spec.match_brands, spec.brand_raw, spec.brand_canonical) == ("none", (), "", "")
    assert spec.brand_placeholder == "GENERIC / NO BRAND"
    if key:
        assert spec.sku_key == key                                   # the run's own key: approvals stay attached
    queries = [q.text for q in build_queries(spec) + relaxations(spec)]
    assert queries and not any("GENERIC" in q.upper() or "BRAND" in q.upper() for q in queries), queries
    assert "GENERIC" not in build_prompt(spec, 1)


def test_row95_q1_is_the_name_alone():
    assert build_queries(spec_of(*ROW95))[0].text == "ICE CREAM CANDY 13g"      # was 'GENERIC NO BRAND ICE CREAM ...'


def test_a_placeholder_in_the_arabic_brand_cell_is_no_brand_either():
    spec = spec_of("ICE CREAM CANDY 13 GM", "", brand_ar="بدون ماركة")
    assert (spec.brand_conf, spec.match_brands, spec.brand_ar) == ("none", (), "")
    assert spec.brand_placeholder == "بدون ماركة"


def test_a_placeholder_brand_still_resolves_a_mapped_brand_that_starts_the_name():
    maps = {"almarai": {"brand": "Almarai", "synonyms": ["المراعي"]}}
    spec = spec_of("ALMARAI FRESH MILK 1L", "N/A", maps)
    assert (spec.brand_conf, spec.brand_canonical, spec.brand_placeholder) == ("mapped", "Almarai", "")
    assert build_index(maps).resolve("GENERIC / NO BRAND", "ICE CREAM CANDY 13 GM").conf == "none"


ROW95_TOP = [
    {"status": "eligible", "reasons": ["vlm:MISMATCH"], "tier": 3, "domain": "minutes.noon.com",
     "image_url": "https://f.nooncdn.com/p/pzsku/ZE09EAC607D4FD0F2168DZ/45/1746424316/"
                  "4197ef84-0b7f-41cb-8258-3a84a90be44d.jpg",
     "page_url": "https://minutes.noon.com/uae-en/now-product/ZE09EAC607D4FD0F2168DZ-1/",
     "vlm": {"decision": "MISMATCH", "view": "front_packshot", "brand": "IGLOO", "variant": "COTTON CANDY", "size": ""}},
    {"status": "eligible", "reasons": ["vlm:MISMATCH"], "tier": 3, "domain": "carrefouruae.com",
     "image_url": "https://cdn.mafrservices.com/pim-content/UAE/media/product/1642137/1739185203/1642137_main.jpg",
     "page_url": "https://www.carrefouruae.com/mafuae/en/cones/igloo-cone-cotton-candy-120ml/p/1642137",
     "vlm": {"decision": "MISMATCH", "view": "front_packshot", "brand": "IGLOO", "variant": "COTTON CANDY", "size": ""}},
]


def test_row95_says_it_has_no_brand_never_that_the_brand_is_unknown():
    spec = spec_of(*ROW95)
    row = {"name": ROW95[0], "brand": ROW95[1], "barcode": "", "size": ""}
    issues = explain.sheet_issues(row, spec=spec, vocab=explain.Vocabulary())
    assert [i["key"] for i in issues] == ["no_barcode", "no_brand"]       # was ['no_barcode', 'brand_unknown']
    assert issues[-1]["text"] == "المنتج بلا ماركة بالشيت"
    record = {"decision": "REVIEW_UNSELECTED", "failure_code": None, "reject_counts": {"vlm:MISMATCH": 2},
              "top": ROW95_TOP}
    why = explain.explain(record, issues, brand=spec.brand_raw)
    assert (why["engine"], why["key"], why["label"]) == ("brand_not_found", "no_brand", "منتج بلا ماركة")
    assert why["text"] == ("المنتج بلا ماركة بالشيت، فالبحث بالاسم بس وصعب نلاقي صورته الصحيحة. "
                           "إذا إله ماركة اكتبها بعمود الماركة، أو اختر من الصور تحت، أو صوّره وارفع الصورة.")
    assert "Brands Mapping" not in why["text"]
    # nothing found at all: the same reason, without the images line
    empty = explain.explain({"decision": "NOT_FOUND", "failure_code": "NO_RESULTS", "top": []}, issues)
    assert empty["key"] == "no_brand" and empty["action"] == "إذا إله ماركة اكتبها بعمود الماركة، أو صوّره وارفع الصورة."
    # a reason outside the sheet stays
    down = explain.explain({"decision": "PROVIDER_DOWN", "failure_code": "PROVIDER_DOWN", "top": []}, issues)
    assert down["key"] == "provider_down"


def test_the_stored_reason_of_a_no_brand_row_reads_the_queue_row():
    trace = {"outcome": {"decision": "REVIEW_UNSELECTED", "failure_code": None, "reject_counts": {}}}
    queue_row = {"product_name": ROW95[0], "brand": ROW95[1], "barcode": "", "status": "ready_for_review",
                 "trace_json": json.dumps(trace), "payload_json": "{}"}
    cands = [{"url": c["image_url"], "page_url": c["page_url"], "status": "eligible", "reasons": c["reasons"],
              "evidence": {"tier": 3}, "vlm": {"decision": "MISMATCH", "brand_text": "IGLOO"}} for c in ROW95_TOP]
    why = explain.explain_stored(queue_row, cands, mappings={}, vocab=explain.Vocabulary())
    assert why["key"] == "no_brand" and [s["key"] for s in why["sheet"]] == ["no_barcode", "no_brand"]


# ---------------------------------------------------------------------------
# Rows 27-28: 'AMERICAN LIGHT' is matched as 'AMERICAN'
# ---------------------------------------------------------------------------

ROW27 = ("AMERICAN LIGHT MEAT TUNA 185 GM", "AMERICAN LIGHT")
ROW28 = ("AMERICAN LIGHT MEAT TUNA SOLID 185GM", "AMERICAN LIGHT")
AMAZON_28 = listing("American Light Meat Tuna 185 g: Buy Online at Best Price in UAE - Amazon.ae",
                    "https://www.amazon.ae/American-Light-Meat-Tuna-185/dp/B0B8GMKGM8",
                    "https://m.media-amazon.com/images/I/81Dh1T79vqL.jpg")
LULU_28 = listing("American Light Meat Tuna 185 g Online at Best Price | Lulu UAE",
                  "https://gcc.luluhypermarket.com/en-ae/american-light-meat-tuna-185-g/p/258114",
                  "https://bf1af2.akinoncloudcdn.com/products/2024/09/11/65163/292b4be2-eb2f-4364-980e-1eeee5d8e4f9.jpg",
                  n=2)
# what the label reader read on them in the run (row 28's top 1 and 3)
AMAZON_READ = reading("American", "LIGHT MEAT TUNA FANCY SOLID PACK", "6.5 OZ. (185 g)")
LULU_READ = reading("American", "LIGHT MEAT TUNA Fancy Solid Pack", "185 g")


@pytest.mark.parametrize("name, brand, key", [ROW27 + ("fe0fbbf624dd8d1a",), ROW28 + ("5eb2bb69d6b8563f",)])
def test_american_light_is_matched_as_american_and_keeps_its_cell_and_key(name, brand, key):
    spec = spec_of(name, brand)
    assert (spec.brand_raw, spec.brand_canonical, spec.match_brands) == ("AMERICAN LIGHT", "AMERICAN", ("american",))
    assert spec.brand_conf == "sheet_raw" and spec.sku_key == key
    assert spec.variants["tuna_meat"] == "light"                          # 'LIGHT' is still the meat grade
    assert build_queries(spec)[0].text == name.replace("185 GM", "185g").replace("185GM", "185g")   # unchanged


def test_row28_the_label_reading_american_is_the_target_brand():
    spec = spec_of(*ROW28)
    assert _brand_reading(spec, "American") == "target"
    assert make_verdict(spec, 0, AMAZON_READ).decision == MATCH            # was UNSURE in the run
    assert make_verdict(spec, 0, LULU_READ).decision == MATCH
    assert _brand_reading(spec, "Americana") == "unknown"                  # still a whole word
    # the sheet's own spelling as a mapped brand is the owner's: never trimmed
    mapped = spec_of(*ROW28, mappings={"american light": {"brand": "AMERICAN LIGHT"}})
    assert mapped.match_brands == ("american light",) and make_verdict(mapped, 0, AMAZON_READ).decision == UNSURE


def test_row27_the_stores_listings_keep_their_tiers():
    spec = spec_of(*ROW27)
    assert score_candidate(spec, LULU_28).tier == 1 and score_candidate(spec, AMAZON_28).tier == 1


class ByQuery:
    """A web search double: the candidates of the first rule whose words the query holds."""
    kind, fallback, sanctioned, name = "search", False, True, "serper"

    def __init__(self, rules):
        self.rules, self.queries = list(rules), []

    def search(self, query, hl, spec_):
        self.queries.append(query)
        cands = next((c for words, c in self.rules if words.lower() in query.lower()), [])
        return ProviderResult(provider="serper", status="ok" if cands else "empty", candidates=list(cands))


def _png(seed):
    import io

    from PIL import Image, ImageDraw

    img = Image.new("RGB", (800, 800), (255, 255, 255))
    ImageDraw.Draw(img).rectangle([200 + seed * 10, 120, 600 - seed * 10, 680], fill=(30 + seed * 40, 80, 150))
    ImageDraw.Draw(img).ellipse([300, 200 + seed * 20, 500, 400 + seed * 20], fill=(200, 40 + seed * 30, 40))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


class Images:
    def __init__(self, cands):
        self.bodies = {c.image_url: _png(i) for i, c in enumerate(cands)}

    def fetch(self, cands, spec_):
        import io

        from PIL import Image

        from catalog_match.fetch import phash_hex

        out = []
        for c in cands:
            body = self.bodies.get(c.image_url)
            if body is None:
                out.append(FetchedImage(candidate=c, ok=False, error="http_404"))
                continue
            with Image.open(io.BytesIO(body)) as im:
                out.append(FetchedImage(candidate=c, ok=True, content_sha256=f"{abs(hash(body)):064x}"[:64],
                                        width=im.width, height=im.height, path_or_bytes=body, phash=phash_hex(im)))
        return out


class Reads:
    """A label reader double: the run's readings by image URL; verify.make_verdict decides."""

    def __init__(self, readings):
        self.readings = dict(readings)

    def verify(self, spec_, images):
        return VerificationResult(status="ok", calls=1, verdicts=[
            make_verdict(spec_, i, self.readings.get(f.candidate.image_url, {})) for i, f in enumerate(images)])


def test_row28_end_to_end_the_amazon_or_lulu_image_is_preselected():
    cands = [AMAZON_28, LULU_28]
    out = pipeline.find_product_image(spec_of(*ROW28), providers=[ByQuery([("", cands)])], fetcher=Images(cands),
                                      verifier=Reads({AMAZON_28.image_url: AMAZON_READ, LULU_28.image_url: LULU_READ}),
                                      expansion=False)
    assert out.decision == "REVIEW_PRESELECTED"                            # was REVIEW_UNSELECTED ('unsure')
    assert out.winner.candidate.image_url in (AMAZON_28.image_url, LULU_28.image_url)
    assert out.winner.verdict.decision == MATCH


@pytest.mark.parametrize("name, brand", [
    ("GOLDEN PRIZE L/ MEAT TUNA VEGE OIL 185GM", "GOLDEN PRIZE"),          # row 35: 'L/ MEAT' starts after the brand
    ("SUPER WHITE WHITE MEAT TUNA 185G", "SUPER WHITE"),                  # 'WHITE' alone never continues
    ("LIGHT HOUSE LIGHT MEAT TUNA 185G", "LIGHT HOUSE"),                  # the first word is never trimmed
    ("TASTY FOOD MEAT MASALA 160GM", "TASTY FOOD"),                       # row 54: 'FOOD MEAT' is no phrase
    ("GREEN FARM MEAT MASALA 200GM", "GREEN FARM"),                       # row 36
    ("ORGANIC SPICES PRAWNS MASALA 200GM", "ORGANIC SPICES"),             # row 78: 'SPICES' is a product word
    ("NILE GARDENS FROZEN MIXED VEGETABLES 400 G", "NILE GARDENS"),       # row 85
    ("KITCHEN TREASURE MEAT MASALA 160GM", "KITCHEN TREASURE"),           # row 39
    ("AMERICAN GOLD LGT MEAT TUNA FLAKE IN SUNFLOWER OIL 160GM", "AMERICAN GOLD"),   # row 26
    ("SUPER/T LIGHT MEAT TUNA SOBEANOIL 185GM", "SUPER/T"),               # row 52
    ("SQ SALITED DRY PRAWNS FISF", "SQ SALITED"),                         # row 79: 'SQ' could not be matched
    ("AMERICAN/LIGHT MEAT TUNA 185GM", "AMERICAN/LIGHT"),                 # written as one word: never cut inside
])
def test_a_brand_whose_word_does_not_open_a_phrase_of_the_name_stays_whole(name, brand):
    spec = spec_of(name, brand)
    assert spec.brand_canonical == brand and matching_brand(brand, name, name) == ""


def test_a_full_cream_brand_word_is_the_milk_s():
    spec = spec_of("NADA FULL CREAM MILK 1L", "NADA FULL")
    assert spec.match_brands == ("nada",) and spec.variants == {"fat": "full"}


def test_american_light_and_sq_salited_get_a_sheet_note():
    vocab = explain.Vocabulary()
    note = explain.brand_product_word(spec_of(*ROW28), vocab)
    assert note == {"key": "brand_has_product_word", "brand": "AMERICAN LIGHT", "suggest": "AMERICAN", "word": "LIGHT",
                    "known": True}
    assert explain.sheet_issue_text(note) == "عمود الماركة فيه كلمة من اسم المنتج: «AMERICAN LIGHT» — الماركة غالبًا «AMERICAN»"
    sq = spec_of("SQ SALITED DRY PRAWNS FISF", "SQ SALITED")
    assert sq.match_brands == ("sq salited",)                              # not trimmed: 'SQ' cannot be matched
    note = explain.brand_product_word(sq, vocab)
    assert (note["suggest"], note["word"], note["fix"], note["known"]) == ("SQ", "SALITED", "SALTED", False)
    assert explain.sheet_issue_text(note) == ("عمود الماركة فيه كلمة من اسم المنتج: «SQ SALITED» — الماركة غالبًا "
                                              "«SQ»، و«SALITED» قصدك «SALTED»")


@pytest.mark.parametrize("name, brand", [
    ("BARTS TRADITON FRENCH FRIES STRAIGHT CUT 1KG", "BARTS TRADITON"),   # a typo of a word, but no product phrase
    ("GOLDEN PRIZE L/ MEAT TUNA VEGE OIL 185GM", "GOLDEN PRIZE"),
    ("ORGANIC SPICES PRAWNS MASALA 200GM", "ORGANIC SPICES"),
    ("HUP HUP FRENCH FRIES REGULAR CUT 1 KG", "HUP HUP"),
    ("DOUBLE HORSE MEAT MASALA 140GM", "DOUBLE HORSE"),
    ("SUPER WHITE WHITE MEAT TUNA 185G", "SUPER WHITE"),
])
def test_no_sheet_note_for_a_brand_without_a_word_of_the_name(name, brand):
    assert explain.brand_product_word(spec_of(name, brand), explain.Vocabulary()) is None
    assert explain.brand_product_word(spec_of(name, brand, {brand.lower(): {"brand": brand}}),
                                      explain.Vocabulary()) is None


def test_row79_the_brand_note_is_why_no_listing_names_the_brand():
    spec = spec_of("SQ SALITED DRY PRAWNS FISF", "SQ SALITED")
    row = {"name": spec.raw_name, "brand": "SQ SALITED", "barcode": "", "size": ""}
    issues = explain.sheet_issues(row, spec=spec, vocab=explain.Vocabulary())
    assert [i["key"] for i in issues] == ["no_size", "no_barcode", "brand_unknown", "brand_has_product_word"]
    record = {"decision": "REVIEW_UNSELECTED", "failure_code": None, "reject_counts": {},
              "top": [{"status": "eligible", "reasons": [], "tier": 3, "image_url": "https://cdn.x.ae/1.jpg"}]}
    why = explain.explain(record, issues, brand="SQ SALITED")
    assert (why["engine"], why["key"]) == ("brand_not_found", "brand_has_product_word")   # was brand_unknown
    assert why["text"].startswith("عمود الماركة فيه كلمة من اسم المنتج: «SQ SALITED»، والماركة غالبًا «SQ»، "
                                  "و«SALITED» قصدك «SALTED»")
    # 'AMERICAN LIGHT' is already matched as 'AMERICAN': its note never replaces a reason
    spec = spec_of(*ROW28)
    issues = explain.sheet_issues({"name": ROW28[0], "brand": ROW28[1]}, spec=spec, vocab=explain.Vocabulary())
    assert explain.explain(record, issues, brand=ROW28[1])["key"] == "brand_unknown"


# ---------------------------------------------------------------------------
# Rows 49-52: Super Tasty
# ---------------------------------------------------------------------------

ROW49 = ("SUP/T WT/MEAT SOLIDTUNA SALTWATER 185GM", "SUP/T")
ROW50 = ("SUPER T/MEAT SOLID TUNA SALT WATE3X185GM", "SUPER T/")
ROW51 = ("SUPER T/MEAT SOLID TUNA SUNFL OIL3X185GM", "SUPER T/")
ROW52 = ("SUPER/T LIGHT MEAT TUNA SOBEANOIL 185GM", "SUPER/T")
TRADELING = "https://www.tradeling.com/ae-en/product-details/"
ROW50_POOL = [
    listing("Super Tasty Meat Solid Tuna In Salt Water Light 185G |...",
            "https://emiratescoop.suppy.app/shop/stores/147/AIRPORT%20VIEW/products/311803/"
            "Super%20Tasty%20Meat%20Solid%20Tuna%20In%20Salt%20Water%20Light%20185G",
            "https://suppystorage.blob.core.windows.net/valeur/items/FMCG_465092_1.jpg", 1),
    listing("Buy Super Tasty Light Meat Solid Tuna In Salt Water 185g x 3 Pieces Online  in UAE | Tradeling",
            TRADELING + "super-tasty-light-meat-solid-tuna-in-salt-water-185g-x-3-pieces-66cecc1a3af83dca028710e1-"
                        "5fba0c6142480f001bed85d4",
            "https://cfn-catalog-prod.tradeling.com/up/5fba0c6142480f001bed85d4/78c6699bd2074a977c9cab83a21b15d9.png", 2),
    listing("Super Tasty Light Meat Solid Tuna in Salt Water, 3x185g",
            "https://onmart.ae/product/super-tasty-light-meat-solid-tuna-in-salt-water-3x185g/",
            "https://onmart.ae/wp-content/uploads/2026/05/image-removebg-preview-2026-01-06T132931.903.png", 3),
]
ROW51_POOL = [
    listing("SUPER TASTY Light Meat Solid Tuna In Sunflower Oil 185g | Sharjah  Co-operative Society",
            "https://www.sharjahcoop.ae/en/super-tasty-light-meat-solid-tuna-in-sunflower-oil-185g/p/6290360095158",
            "https://www.sharjahcoop.ae/medias/300Wx300H-00000-6290360095158-001.jpg", 1),
    listing("Buy Super Tasty White Meat Solid Premium Tuna In Sunflower Oil 185g x 3  Pieces Online in UAE | Tradeling",
            TRADELING + "super-tasty-white-meat-solid-premium-tuna-in-sunflower-oil-185g-x-3-pieces-"
                        "66cecc1a3af83dca02871825-5fba0c6142480f001bed85d4",
            "https://c8n.tradeling.com/img/plain/pim/rs:auto:1600::0/f:webp/q:90/up/5fba0c6142480f001bed85d4/"
            "403af066d1b25692bc6d35a00da83507.jpg", 2),
    listing("SUPER TASTY W/M TUNA IN S/F OIL3X185G: Buy Online at Best Price in UAE -  Amazon.ae",
            "https://www.amazon.ae/SUPER-TASTY-TUNA-OIL3X185G/dp/B09XVCS27X",
            "https://m.media-amazon.com/images/I/516WVQJlopL.jpg", 3),
]
ROW52_POOL = [
    listing("Super Tasty L.Meat Tuna In Soya Oil 185g | Sharjah Co-operative Society",
            "https://www.sharjahcoop.ae/en/super-tasty-lmeat-tuna-in-soya-oil-185g/p/6290360090887",
            "https://www.sharjahcoop.ae/medias/1200Wx1200H-00000-6290360090887-001.jpg", 1),
    listing("SUPER TASTY LIGHT MEAT TUNA SHREDED IN SOYA BEAN OIL 185GM | Safari Online  - Shop Online from Safari "
            "Hyermarket", "https://safarihypermarket.ae/en/products/super-tasty-light-meat-tuna-shreded-in-soya-bean-oil-185gm-1",
            "https://qc-products-images-in.s3.ap-south-1.amazonaws.com/optimized-images/"
            "optimized_0ded6ef9-0705-4148-91ab-f1d5fdded482.jpeg", 2),
]
# row 49's results in the run: other brands only
ROW49_OTHERS = [
    listing("Super White White Meat Tuna 185g – SuperDokan",
            "https://superdokan.com/en-lb/collections/vendors/products/super-white-white-meat-tuna",
            "https://superdokan.com/cdn/shop/products/12_b2e7b73c-b92a-478e-9d24-f7b00b6b7efd.jpg", 1),
    listing("Aqua Premium Solid Tuna White | Gourmet Food Stores", "https://gourmetegypt.com/aqua-premium-solid-tuna-white",
            "https://gourmetegypt.com/media/catalog/product/7/9/796520942738_2_1.jpg", 2),
]


@pytest.mark.parametrize("row, pool", [(ROW50, ROW50_POOL), (ROW51, ROW51_POOL), (ROW52, ROW52_POOL)])
def test_super_t_and_super_slash_t_are_super_tasty_on_the_runs_own_listings(row, pool):
    spec = spec_of(*row)
    found = bd.find(spec, pool)
    assert (found.display, found.kind) == ("Super Tasty", "abbreviation")
    applied = bd.apply(spec, found)
    assert applied.discovered_brands == ("Super Tasty",) and "super tasty" in applied.match_brands
    # the run's readings on rows 50 / 51: 'Super Tasty' is the target brand once the spec carries it
    assert _brand_reading(spec, "Super Tasty") == "unknown"
    assert _brand_reading(applied, "Super Tasty") == "target"


def test_rows_50_51_after_row_52_plan_their_queries_with_super_tasty():
    bd.remember(spec_of(*ROW52), bd.discover(spec_of(*ROW52), ROW52_POOL))
    for row in (ROW50, ROW51):
        spec = spec_of(*row)
        hint = bd.planned_hint(spec)
        assert hint is not None and hint.display == "Super Tasty"
        assert build_queries(bd.as_hint(spec, hint))[0].text.startswith("Super Tasty MEAT SOLID TUNA ")
        assert bd.as_hint(spec, hint).match_brands == spec.match_brands          # queries only, never evidence
    # row 49 ('SUP/T', searched again in the same run) is a sibling spelling: the same plan
    assert build_queries(bd.as_hint(spec_of(*ROW49), bd.planned_hint(spec_of(*ROW49))))[0].text == \
        "Super Tasty WHITE MEAT SOLID TUNA SALT WATER 185g"
    # a mapped brand never takes a remembered spelling
    mapped = spec_of(*ROW50, mappings={"super t": {"brand": "Super T", "synonyms": ["SUPER T/"]}})
    assert bd.planned_hint(mapped) is None


ST_ONMART_READ = reading("Super Tasty", "Light Meat Solid Tuna in Salt Water", "3 x 185 g", pack=3)


def test_row50_after_row52_never_searches_super_t_and_reads_super_tasty_as_its_brand():
    row52 = pipeline.find_product_image(spec_of(*ROW52), providers=[ByQuery([("", ROW52_POOL)])],
                                        fetcher=Images(ROW52_POOL), verifier=Reads({}), expansion=False)
    assert row52.discovered_brands == ["Super Tasty"]
    search = ByQuery([("super tasty", ROW50_POOL)])                         # 'SUPER T ...' finds nothing
    onmart = ROW50_POOL[2]
    out = pipeline.find_product_image(spec_of(*ROW50), providers=[search], fetcher=Images(ROW50_POOL),
                                      verifier=Reads({onmart.image_url: ST_ONMART_READ}), expansion=False)
    assert search.queries[0] == "Super Tasty MEAT SOLID TUNA SALT WATER 3x185g"
    assert not any(q.upper().startswith("SUPER T ") for q in search.queries), search.queries   # was Q1 and Q3
    assert out.discovered_brands == ["Super Tasty"]
    assert out.decision == "REVIEW_PRESELECTED" and out.winner.candidate.image_url == onmart.image_url
    assert out.winner.verdict.decision == MATCH                            # the label's 'Super Tasty' is the brand
    assert "auto_blocked:brand_conf_sheet_raw" in out.winner.reasons


def test_a_remembered_spelling_this_row_does_not_name_sends_the_sheets_own_query_once():
    bd.remember(spec_of(*ROW52), bd.discover(spec_of(*ROW52), ROW52_POOL))
    search = ByQuery([("", ROW49_OTHERS)])                                 # row 49's results: other brands only
    out = pipeline.find_product_image(spec_of(*ROW49), providers=[search], fetcher=Images(ROW49_OTHERS),
                                      verifier=Reads({}), expansion=False)
    assert search.queries[0].startswith("Super Tasty ")
    sheet_way = [q for q in search.queries if q == "SUP T WHITE MEAT SOLID TUNA SALT WATER 185g"]
    assert len(sheet_way) == 1                                              # the sheet's spelling is still searched
    assert out.discovered_brands == []


def test_the_trace_the_export_and_the_stored_reason_keep_the_discovered_spelling():
    sys.path.insert(0, str(ROOT / "scripts"))
    import export_run

    spec = spec_of(*ROW50)
    applied = bd.apply(spec, bd.find(spec, ROW50_POOL))
    outcome = pipeline.SearchOutcome(decision="REVIEW_UNSELECTED", sku_key=spec.sku_key,
                                     queries=["Super Tasty MEAT SOLID TUNA SALT WATER 3x185g"],
                                     discovered_brands=list(applied.discovered_brands))
    trace = {}
    facade.outcome_to_legacy(outcome, trace, spec=spec)
    assert trace["outcome"]["discovered_brands"] == ["Super Tasty"]       # was never stored
    # the run's no-pick reason no longer says no store writes the brand
    assert not any(i["key"] == "brand_unknown" for i in trace["outcome"]["explain"]["sheet"])
    queue_row = {"row_number": 50, "product_name": ROW50[0], "brand": ROW50[1], "barcode": "",
                 "status": "ready_for_review", "trace_json": json.dumps(trace), "payload_json": "{}",
                 "sku_key": spec.sku_key}
    del trace["outcome"]["explain"]                                         # a row saved before the reason
    queue_row["trace_json"] = json.dumps(trace)
    stored = explain.explain_stored(queue_row, [], mappings={}, vocab=explain.Vocabulary())
    assert not any(s["key"] == "brand_unknown" for s in stored["sheet"])
    row = export_run.export_row(queue_row, [], mappings={}, vocab=explain.Vocabulary(), prices={"serper": 0.001})
    assert row["discovered_brands"] == ["Super Tasty"]                    # was always []
    assert not any(i["key"] == "brand_unknown" for i in row["sheet_issues"])


# ---------------------------------------------------------------------------
# Row 4: '900 MM' is a weight written in millimetres
# ---------------------------------------------------------------------------

def test_row4_900_mm_gets_a_sheet_note_and_the_size_is_still_unread():
    spec = spec_of("BATO FRENCH FRIES 900 MM", "BATO")
    assert spec.size is None and spec.sku_key == "834b2a9ce73f921f"       # parsing unchanged, key kept
    issues = explain.sheet_issues({"name": spec.raw_name, "brand": "BATO"}, spec=spec, vocab=explain.Vocabulary())
    assert [i["key"] for i in issues] == ["no_size", "size_unit_typo", "no_barcode", "brand_unknown"]
    assert issues[1]["text"] == "الحجم مكتوب «900 MM» — غالبًا قصدك «900 GM»"
    # a label reader unsure of the bags: the unit is why, not only the missing size
    record = {"decision": "REVIEW_UNSELECTED", "failure_code": None, "reject_counts": {},
              "top": [{"status": "eligible", "reasons": ["vlm:UNSURE"], "tier": 1, "size": "unknown",
                       "image_url": "https://storage.googleapis.com/takeapp/media/cm3oji9wf00050ciagj2t8fnc.jpg",
                       "vlm": {"decision": "UNSURE"}}]}
    why = explain.explain(record, issues, brand="BATO")
    assert (why["engine"], why["key"], why["label"]) == ("unsure", "size_unit_typo", "وحدة الحجم غلط")
    assert "«900 GM»" in why["action"]


@pytest.mark.parametrize("name, brand, size", [
    ("FARMILA FRENCH FRIES 9MM 1KG", "FARMILA", ""),                      # row 5: a 9 mm cut, and a weight
    ("TOMEX FRENCH FRIES 9MM 1 KG", "TOMEX", ""),                         # row 12
    ("MCCAIN FRENCH FRIES 9MM", "MCCAIN", ""),                            # a cut, no size: under 50 mm
    ("BATO FRENCH FRIES 900 MM", "BATO", "900 GM"),                       # the size column says the weight
    ("ICE CREAM SCOOP 180 MM", "DELCASA", ""),                             # a scoop is no food
    ("KITCHEN TISSUE 230 MM", "FINE", ""),
    ("BATO FRENCH FRIES 900 MM 10MM CUT", "BATO", ""),                    # two lengths: no guess
])
def test_a_real_length_raises_no_size_unit_note(name, brand, size):
    spec = spec_of(name, brand, size=size)
    row = {"name": name, "brand": brand, "size": size}
    assert explain.size_unit_typo(row, spec) is None
    assert "size_unit_typo" not in [i["key"] for i in explain.sheet_issues(row, spec=spec, vocab=explain.Vocabulary())]


def test_farmila_9mm_keeps_its_1kg():
    assert spec_of("FARMILA FRENCH FRIES 9MM 1KG", "FARMILA").size.canonical() == "1000g"
