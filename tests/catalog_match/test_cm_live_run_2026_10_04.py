"""Live run of 2026-10-04 (laqta_run_2026-10-04_1619.json, 100 rows, every brand 'sheet_raw'): the Brand column.

Names, brands, titles, URLs and label readings are the run's own. Sockets are blocked: every stage is a double.

* Rows 95-97, brand 'GENERIC / NO BRAND': searched as a brand ('GENERIC NO BRAND ICE CREAM CANDY 13g') and told
  to add it to Brands Mapping. A placeholder brand is no brand (brand_index.is_placeholder_brand): never in the
  queries, the matching or the label prompt, never 'brand_unknown'; the reason is 'no_brand'. The sku_key is kept.
* Rows 27-28, brand 'AMERICAN LIGHT' on 'AMERICAN LIGHT MEAT TUNA ...': the label reader read 'American', which
  'american light' never matched (UNSURE). The brand is matched as 'AMERICAN' ('LIGHT MEAT' is the meat grade),
  with a sheet note. Brands whose last word is a product word that does not open a phrase of the name stay whole.
* Row 79, brand 'SQ SALITED': only a sheet note ('SQ' is too short to match; 'SALITED' is 'SALTED').
* Row 4, 'BATO FRENCH FRIES 900 MM': no size, so a 2.5 KG bag was pre-selected. A sheet note says '900 GM' is
  meant; a 9 mm cut ('FARMILA FRENCH FRIES 9MM 1KG') stays a cut and raises nothing.
"""

import json
import socket

import pytest

from catalog_match import brand_discovery as bd
from catalog_match import explain, pipeline, settings
from catalog_match.brand_index import PLACEHOLDER_BRANDS, build_index, is_placeholder_brand
from catalog_match.identity import build_sku_spec, matching_brand
from catalog_match.models import Candidate, FetchedImage, ProviderResult, VerificationResult
from catalog_match.query_plan import build_queries, relaxations
from catalog_match.score import score_candidate
from catalog_match.verify import MATCH, UNSURE, _brand_reading, build_prompt, make_verdict


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
