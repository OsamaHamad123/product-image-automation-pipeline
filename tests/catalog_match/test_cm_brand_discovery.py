"""catalog_match.brand_discovery: the stores' spelling of a sheet brand they write differently.

Cases are the live run of 2026-10-03 (runs/2026-10-03/smoke_6.json): rows 37 (INA PARAMANS / Ina
Paarman's), 45 (RIO MARIE / Rio Mare), 49-52 (SUP/T, SUPER T/, SUPER/T / Super Tasty) and 3 (BARTS
TRADITON). The guards are just as important: 'American' is never 'Americana', a mapped brand is
never re-spelled, and nothing found this way auto-publishes. Sockets are blocked.
"""

import io
import socket

import pytest
from PIL import Image, ImageDraw

from catalog_match import brand_discovery as bd
from catalog_match import decide, pipeline, settings
from catalog_match.fetch import phash_hex
from catalog_match.identity import build_sku_spec
from catalog_match.models import Candidate, FetchedImage, ProviderResult, VerificationResult
from catalog_match.query_plan import build_queries
from catalog_match.verify import _brand_reading, build_prompt, make_verdict


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


def spec(name, brand, mappings=None):
    return build_sku_spec({"name": name, "brand": brand}, mappings or {})


def listing(title, page_url, n=0):
    return Candidate(image_url=f"https://img.example-cdn.com/{n}.jpg", page_url=page_url, title=title,
                     page_title=title, provider="serper", rank=n + 1)


LULU = "https://gcc.luluhypermarket.com/en-ae/{}/p/{}"
RIO = spec("RIO MARIE LIGHT MEAT TUNA IN SUN OIL 3X70GM", "RIO MARIE")
INA = spec("INA PARAMANS SEASOING MEAT SPICE 200ML", "INA PARAMANS")


# ---------------------------------------------------------------------------
# Word rules
# ---------------------------------------------------------------------------

def test_osa_distance_counts_a_swap_as_one_edit():
    assert bd.osa_distance("paramans", "paarmans") == 1
    assert bd.osa_distance("marie", "mare") == 1
    assert bd.osa_distance("traditon", "tradition") == 1
    assert bd.osa_distance("almarai", "almarai") == 0
    assert bd.osa_distance("kraft", "craft") == 1


@pytest.mark.parametrize("sheet,store,ok", [
    ("rio marie", "rio mare", True),               # row 45
    ("ina paramans", "ina paarmans", True),        # row 37, two neighbours swapped
    ("barts traditon", "barts tradition", True),   # row 3
    ("american", "americana", False),              # another brand: the last letter differs
    ("americana", "american", False),
    ("almarai", "almaraj", False),                 # the last letter is never the edit
    ("lacnor", "bacnor", False),                   # neither is the first
    ("nido", "nida", False),                       # too short to tell a typo from a brand
    ("rio marie", "rio mar", False),               # two edits
    ("rio marie", "rio marie", False),             # the same: nothing to discover
    ("rio marie", "rio mare tuna", False),         # another number of words
])
def test_a_spelling_is_one_inner_edit_of_a_long_enough_brand(sheet, store, ok):
    assert bd.spelling_of(sheet.split(), store.split()) is ok


@pytest.mark.parametrize("raw,sheet,store,ok", [
    ("SUP/T", "sup t", "super tasty", True),       # row 49
    ("SUPER T/", "super t", "super tasty", True),  # row 50
    ("SUPER/T", "super t", "super tasty", True),   # row 52
    ("SUP", "sup", "super", False),                # one word: too easy to hit any brand
    ("SU/T", "su t", "super tasty", False),        # the first word keeps 3+ letters
    ("SUPER/T", "super t", "super t", False),      # nothing expanded
    ("SUP/T", "sup t", "supreme tuna", True),      # the rule alone allows it: the listing gates below decide
])
def test_an_abbreviation_starts_every_store_word(raw, sheet, store, ok):
    assert bd.abbreviation_of(sheet.split(), store.split()) is ok


def test_only_a_short_written_brand_counts_as_abbreviated():
    assert bd.abbreviated("SUP/T", ["sup", "t"]) and bd.abbreviated("SUPER T/", ["super", "t"])
    assert not bd.abbreviated("RIO MARIE", ["rio", "marie"]) and not bd.abbreviated("SUP", ["sup"])
    assert bd.abbreviated("A.B. FOODS", ["a", "b", "foods"])


# ---------------------------------------------------------------------------
# discover()
# ---------------------------------------------------------------------------

def test_typos_found_where_a_uae_store_writes_the_brand():
    found = bd.discover(RIO, [listing("Rio Mare Light Meat Tuna In Sunflower Oil 70 g",
                                      LULU.format("rio-mare-light-meat-tuna-70-g", 1))])
    assert (found.phrase, found.display, found.kind, found.sheet_phrase) == ("rio mare", "Rio Mare", "spelling",
                                                                            "rio marie")
    found = bd.discover(INA, [listing("Ina Paarman's Seasoning Meat Spice, 200ml (Pack of 1) : Amazon.ae",
                                      "https://www.amazon.ae/Ina-Paarmans-Seasoning-Meat-Spice/dp/B01")])
    assert found.phrase == "ina paarmans"


@pytest.mark.parametrize("name,brand", [
    ("SUP/T WT/MEAT SOLIDTUNA SALTWATER 185GM", "SUP/T"),
    ("SUPER T/MEAT SOLID TUNA SALT WATE3X185GM", "SUPER T/"),
    ("SUPER/T LIGHT MEAT TUNA SOBEANOIL 185GM", "SUPER/T"),
])
def test_super_tasty_from_its_abbreviations(name, brand):
    cands = [listing("Super Tasty L.Meat Tuna In Soya Oil 185g | Sharjah Co-operative Society",
                     "https://sharjahcoop.ae/super-tasty-l-meat-tuna-in-soya-oil-185g", 0),
             listing("Buy Super Tasty Light Meat Solid Tuna In Salt Water 185g x 3 Pieces Online in UAE | Tradeling",
                     "https://www.tradeling.com/en/super-tasty-light-meat-tuna", 1)]
    found = bd.discover(spec(name, brand), cands)
    assert (found.display, found.kind) == ("Super Tasty", "abbreviation")
    assert found.domains == ("sharjahcoop.ae", "tradeling.com")


def test_nothing_is_discovered_when_a_listing_already_writes_the_sheet_brand():
    cands = [listing("Rio Marie Tuna 70 g", "https://www.example-shop.com/rio-marie-tuna"),
             listing("Rio Mare Light Meat Tuna 70 g", LULU.format("rio-mare-light-meat-tuna-70-g", 1), 1)]
    assert bd.discover(RIO, cands) is None


def test_a_brand_written_in_full_is_never_read_as_an_abbreviation():
    # 'gold' starts 'golden' and 'star' starts 'starlight': only a sheet brand written short may expand
    gold_star = spec("GOLD STAR LIGHT MEAT TUNA 185G", "GOLD STAR")
    cands = [listing("Golden Starlight Light Meat Tuna 185 g", LULU.format("golden-starlight-tuna-185-g", n), n)
             for n in range(2)]
    assert bd.abbreviation_of(["gold", "star"], ["golden", "starlight"])
    assert bd.discover(gold_star, cands) is None


def test_americana_is_never_american():
    american = spec("AMERICAN LIGHT MEAT TUNA SOLID 185GM", "AMERICAN")
    cands = [listing("Americana Light Tuna Solid in Sunflower Oil 185 g Online at Best Price | Lulu UAE",
                     LULU.format("americana-light-tuna-solid-185-g", n), n) for n in range(3)]
    assert bd.discover(american, cands) is None


def test_a_mapped_brand_or_a_known_other_brand_is_never_respelled():
    mapped = spec("RIO MARIE LIGHT MEAT TUNA 70G", "RIO MARIE", {"rio marie": {"brand": "Rio Marie"}})
    cands = [listing("Rio Mare Light Meat Tuna 70 g", LULU.format("rio-mare-light-meat-tuna-70-g", 1))]
    assert mapped.brand_conf == "mapped" and bd.discover(mapped, cands) is None
    rival = spec("RIO MARIE LIGHT MEAT TUNA 70G", "RIO MARIE",
                 {"rio mare": {"brand": "Rio Mare"}, "nadec": {"brand": "Nadec"}})
    assert "rio mare" in rival.competitors and bd.discover(rival, cands) is None


def test_one_foreign_site_is_not_enough_two_sites_are():
    foreign = [listing("Rio Mare Light Meat Tuna 70g", "https://www.carrefourksa.com/mafsau/en/tuna/rio-mare/p/1")]
    assert bd.discover(RIO, foreign) is None
    second = listing("Rio Mare Light Meat Tuna in Sunflower Oil", "https://www.example-grocer.com/rio-mare-tuna", 1)
    assert bd.discover(RIO, foreign + [second]).domains == ("carrefourksa.com", "example-grocer.com")


def test_the_store_spelling_must_open_a_listing_of_the_same_kind_of_product():
    beauty = [listing("Rio Mare Hair Shampoo 400 ml", LULU.format("rio-mare-hair-shampoo", 1))]
    assert bd.discover(RIO, beauty) is None                              # no product word of the SKU
    mid_title = [listing("Light Meat Tuna by Rio Mare 70 g", LULU.format("light-meat-tuna-rio-mare", 2))]
    assert bd.discover(RIO, mid_title) is None                           # not the opening words


# ---------------------------------------------------------------------------
# apply(), queries, the label reader, the review warning
# ---------------------------------------------------------------------------

def _found_rio():
    return bd.discover(RIO, [listing("Rio Mare Light Meat Tuna In Sunflower Oil 70 g",
                                     LULU.format("rio-mare-light-meat-tuna-70-g", 1))])


def test_apply_accepts_the_store_spelling_without_making_it_a_mapped_brand():
    s = bd.apply(RIO, _found_rio())
    assert s.match_brands == ("rio marie", "rio mare") and s.discovered_brands == ("Rio Mare",)
    assert (s.brand_conf, s.sku_key, s.brand_raw) == (RIO.brand_conf, RIO.sku_key, RIO.brand_raw)


def test_the_corrected_query_writes_the_store_spelling_once():
    s = bd.apply(RIO, _found_rio())
    q = bd.corrected_query(s)
    assert q.query_id == "B1" and q.text.startswith("Rio Mare ") and "MARIE" not in q.text
    assert build_queries(RIO)[0].text.startswith("RIO MARIE")
    assert bd.corrected_query(s, [q.text]) is None


def test_the_label_reader_is_told_and_accepts_the_store_spelling():
    s = bd.apply(INA, bd.discover(INA, [listing("Ina Paarman's Meat Spice 200g", "https://www.spinneys.com/en-ae/"
                                                                                  "catalogue/ina-paarmans-meat-spice_1/")]))
    assert "the stores write it Ina Paarmans" in build_prompt(s, 1)
    assert _brand_reading(INA, "INA PAARMAN'S") == "unknown"
    assert _brand_reading(s, "INA PAARMAN'S") == "target"


# ---------------------------------------------------------------------------
# End to end: found, verified, pre-selected with the warning, never auto-published
# ---------------------------------------------------------------------------

def _png(seed=2):
    img = Image.new("RGB", (800, 800), (255, 255, 255))
    ImageDraw.Draw(img).rectangle([220, 120, 580, 680], fill=(30 + seed * 30, 80, 150))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


class Search:
    kind, fallback, sanctioned = "search", False, True

    def __init__(self, cands):
        self.name, self.cands, self.queries = "serper", list(cands), []

    def search(self, query, hl, spec_):
        self.queries.append(query)
        return ProviderResult(provider="serper", status="ok", candidates=list(self.cands))


class Images:
    def __init__(self, bodies):
        self.bodies = bodies

    def fetch(self, cands, spec_):
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


class ReadsRioMare:
    def __init__(self):
        self.prompts = []

    def verify(self, spec_, images):
        self.prompts.append(build_prompt(spec_, len(images)))
        reading = {"brand_text": "Rio mare", "variant_text": "Light Meat Tuna in Sunflower Oil", "size_text": "3 x 70 g",
                   "pack_count": 3, "view": "front_packshot", "brand_match": "yes", "variant_match": "yes",
                   "size_match": "yes"}
        return VerificationResult(status="ok", calls=1,
                                  verdicts=[make_verdict(spec_, i, reading) for i, _ in enumerate(images)])


def test_row45_is_found_preselected_with_the_warning_and_never_auto_published(monkeypatch):
    monkeypatch.setenv("AUTO_PUBLISH_ENABLED", "true")
    monkeypatch.setenv("AUTO_PUBLISH_BRANDS", "*")
    hit = Candidate(image_url="https://img.example-cdn.com/rio-mare-3x70.jpg",
                    page_url="https://www.carrefouruae.com/mafuae/en/tuna/rio-mare-light-meat-tuna-in-sunflower-oil-"
                             "70g-pack-of-3/p/1",
                    title="Buy Rio Mare Light Meat Tuna in Sunflower Oil, 70g Pack of 3 Online | Carrefour UAE",
                    page_title="Rio Mare Light Meat Tuna in Sunflower Oil 70g Pack of 3", provider="serper", rank=1)
    search = Search([hit])
    verifier = ReadsRioMare()
    outcome = pipeline.find_product_image(RIO, providers=[search], fetcher=Images({hit.image_url: _png()}),
                                          verifier=verifier, expansion=False)
    assert outcome.discovered_brands == ["Rio Mare"]
    assert any(q.startswith("Rio Mare ") for q in search.queries)        # the corrected query was sent
    assert outcome.decision == "REVIEW_PRESELECTED"                      # never AUTO_PUBLISH: not a mapped brand
    assert "auto_blocked:brand_conf_sheet_raw" in outcome.winner.reasons
    assert decide.WARN_PREFIX + "brand_spelling" in outcome.winner.reasons
    assert "the stores write it Rio Mare" in verifier.prompts[0]


def test_the_warning_is_not_shown_when_the_sheet_spelling_is_on_the_listing_too():
    s = bd.apply(RIO, _found_rio())
    from catalog_match.models import RankedCandidate
    from catalog_match.score import score_candidate
    both = listing("Rio Mare (Rio Marie) Light Meat Tuna 70 g", LULU.format("rio-mare-tuna", 3))
    only = listing("Rio Mare Light Meat Tuna 70 g", LULU.format("rio-mare-tuna", 4))
    assert "brand_spelling" not in decide.review_warnings(s, RankedCandidate(both, score_candidate(s, both)))
    assert "brand_spelling" in decide.review_warnings(s, RankedCandidate(only, score_candidate(s, only)))
