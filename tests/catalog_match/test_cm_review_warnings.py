"""Review warnings on the pick ('warn:' reasons from decide.route), live dry run of 2026-09-30.

Pre-checked picks were plausible but carried something the sheet does not say, or came from a
weak source: 'Sunbulah Thin French Fries' for SUNBULAH FRENCH FRIES 1KG (row 11), shredded
mozzarella for AL RAWABI MOZZARELLA 200GM (row 17), a Carrefour Kuwait page (row 4), a WhatsApp
photo on a Saudi wholesale site (row 42), tier 1 with Gemini UNSURE (rows 21, 27, 28, 61).
The winner and the decision stay as they were; the winner carries warnings for the reviewer.
"""

import io
import json
import socket
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from catalog_match import decide, facade, pipeline, settings
from catalog_match import quality as quality_mod
from catalog_match import variants as variants_mod
from catalog_match.fetch import phash_hex
from catalog_match.identity import build_sku_spec
from catalog_match.models import (
    Candidate, FetchedImage, ProviderHealth, ProviderResult, QualityReport, RankedCandidate,
    VerificationResult, VlmImageVerdict,
)
from catalog_match.score import score_candidate
from catalog_match.verify import make_verdict

LIVE_ROWS = Path(__file__).resolve().parent / "fixtures" / "live_rows_2026_09_30.json"
HEALTHY = [ProviderHealth("serper", "ok", 200)]
OK = VerificationResult(status="ok", calls=1)
DOWN = VerificationResult(status="unknown", calls=1, error="http_429")

MILK = build_sku_spec({"name": "Almarai Full Fat Milk 1L", "brand": "Almarai", "category": "Dairy"},
                      {"almarai": {"brand": "Almarai", "synonyms": ["المراعي"], "official_domains": ["almarai.com"]}})
FRIES = build_sku_spec({"name": "SUNBULAH FRENCH FRIES 1KG", "brand": "SUNBULAH"}, {})
MOZZARELLA = build_sku_spec({"name": "AL RAWABI MOZZARELLA 200GM", "brand": "AL RAWABI"}, {})


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    monkeypatch.setattr(settings, "_config", None)
    monkeypatch.setenv("AUTO_PUBLISH_ENABLED", "false")
    monkeypatch.setenv("AUTO_PUBLISH_BRANDS", "")


def milk(image_url="https://cdn.mafrservices.com/sys-master-root/h1/108596_main.jpg",
         page_url="https://www.carrefouruae.com/mafuae/en/fresh-food/almarai-full-fat-milk-1l/p/108596",
         title="Buy Almarai Full Fat Milk 1L Online - Shop on Carrefour UAE", domain=""):
    return Candidate(image_url=image_url, page_url=page_url, title=title, page_title=title, domain=domain,
                     provider="serper", query_id="Q1", rank=1)


def reading(decision="MATCH", variant_text="Full Fat Milk", brand="Almarai"):
    return VlmImageVerdict(index=0, brand_text=brand, variant_text=variant_text, size_text="1 L",
                           view="front_packshot", brand_match="yes", variant_match="yes", size_match="yes",
                           decision=decision)


def rc(spec, cand, verdict=None, size=(800, 800)):
    fi = FetchedImage(candidate=cand, ok=True, content_sha256="ab" * 32, width=size[0], height=size[1],
                      path_or_bytes=b"x")
    return RankedCandidate(candidate=cand, score=score_candidate(spec, cand), fetched=fi,
                           quality=QualityReport(hard_ok=True, quality_score=0.8), verdict=verdict)


def route_one(spec, cand, verdict=None, size=(800, 800), verification=OK):
    winner = rc(spec, cand, verdict if verdict is not None else reading(), size)
    outcome = decide.route(spec, [winner], verification, HEALTHY, set())
    return outcome, winner


def warns(r):
    return [x for x in r.reasons if x.startswith("warn:")]


# -- clean picks carry no warning ---------------------------------------------------------------

@pytest.mark.parametrize("cand", [
    milk(),
    milk(image_url="https://www.luluhypermarket.com/medias/almarai-full-fat-milk-1l.jpg",
         page_url="https://www.luluhypermarket.com/en-ae/almarai-full-fat-milk-1l/p/2",
         title="Almarai Full Fat Milk 1L Online at Best Price | Lulu UAE"),
    milk(image_url="https://f.nooncdn.com/p/pnsku/N1/45/1/1.jpg",
         page_url="https://www.noon.com/uae-en/almarai-full-fat-milk-1l/N11/p/",
         title="Shop Almarai Full Fat Milk 1L online in Dubai, Abu Dhabi and all UAE"),
    milk(image_url="https://m.media-amazon.com/images/I/71x.jpg",
         page_url="https://www.amazon.ae/Almarai-Full-Fat-Milk-1L/dp/B0",
         title="Almarai Full Fat Milk 1L : Amazon.ae: Grocery"),
], ids=["carrefour_uae", "lulu_uae", "noon_uae", "amazon_ae"])
def test_a_clean_uae_retailer_packshot_has_no_warning(cand):
    outcome, winner = route_one(MILK, cand)
    assert outcome.winner is winner and outcome.decision == "REVIEW_PRESELECTED"
    assert warns(winner) == []
    assert decide.review_warnings(MILK, winner) == []


# -- sheet_silent ------------------------------------------------------------------------------------

def test_row11_thin_fries_the_sheet_does_not_state():
    thin = Candidate(image_url="https://cdn.mafrservices.com/sys-master-root/sunbulah_thin.jpg",
                     page_url="https://www.carrefouruae.com/mafuae/en/frozen-food/sunbulah-thin-french-fries-1kg/p/1",
                     title="Sunbulah Thin French Fries 1 kg", provider="serper", rank=1)
    outcome, winner = route_one(FRIES, thin, reading(variant_text="Thin French Fries", brand="Sunbulah"))
    assert outcome.winner is winner and outcome.decision == "REVIEW_PRESELECTED"
    assert warns(winner) == ["warn:sheet_silent:fries_cut=thin"]
    # the breadcrumb '/frozen-food/' is a department, not the product: no 'form=frozen'


def test_row17_shredded_mozzarella_read_on_the_label_only():
    # The listing text says nothing about the form (tier 1 stays possible); the label does.
    listing = Candidate(image_url="https://www.luluhypermarket.com/medias/rawabi-mozzarella.jpg",
                        page_url="https://www.luluhypermarket.com/en-ae/al-rawabi-mozzarella-200g/p/9",
                        title="Al Rawabi Mozzarella 200g", provider="serper", rank=1)
    outcome, winner = route_one(MOZZARELLA, listing,
                                reading(variant_text="Shredded Mozzarella Cheese", brand="Al Rawabi"))
    assert outcome.winner is winner
    assert warns(winner) == ["warn:sheet_silent:cheese_form=shredded"]


def test_the_default_cut_or_form_does_not_warn():
    straight = Candidate(image_url="https://cdn.mafrservices.com/x/straight.jpg",
                         page_url="https://www.carrefouruae.com/mafuae/en/sunbulah-straight-cut-fries-1kg/p/2",
                         title="Sunbulah Straight Cut French Fries 1kg", provider="serper", rank=1)
    _, winner = route_one(FRIES, straight, reading(variant_text="Straight Cut", brand="Sunbulah"))
    assert warns(winner) == []
    block = Candidate(image_url="https://www.luluhypermarket.com/medias/rawabi-block.jpg",
                      page_url="https://www.luluhypermarket.com/en-ae/al-rawabi-mozzarella-block-200g/p/3",
                      title="Al Rawabi Mozzarella Cheese Block 200g", provider="serper", rank=1)
    _, winner = route_one(MOZZARELLA, block, reading(variant_text="Mozzarella Block", brand="Al Rawabi"))
    assert warns(winner) == []


def test_a_variant_the_sheet_states_does_not_warn():
    thin_spec = build_sku_spec({"name": "SUNBULAH THIN FRENCH FRIES 1KG", "brand": "SUNBULAH"}, {})
    assert thin_spec.variants == {"fries_cut": "thin"}
    thin = Candidate(image_url="https://cdn.mafrservices.com/x/thin.jpg",
                     page_url="https://www.carrefouruae.com/mafuae/en/sunbulah-thin-french-fries-1kg/p/1",
                     title="Sunbulah Thin French Fries 1 kg", provider="serper", rank=1)
    _, winner = route_one(thin_spec, thin, reading(variant_text="Thin French Fries", brand="Sunbulah"))
    assert warns(winner) == []


# -- vlm_unsure --------------------------------------------------------------------------------------

def test_tier1_pre_checked_with_gemini_unsure_or_down():
    outcome, winner = route_one(MILK, milk(), reading("UNSURE"))
    assert "preselected:tier1_unsure" in winner.reasons
    assert warns(winner) == ["warn:vlm_unsure"]
    outcome, winner = route_one(MILK, milk(), VlmImageVerdict(index=0, decision="UNKNOWN"), verification=DOWN)
    assert outcome.failure_code == "VERIFIER_DOWN" and "preselected:tier1_unknown" in winner.reasons
    assert warns(winner) == ["warn:vlm_unsure"]


# -- low_resolution ----------------------------------------------------------------------------------

def test_a_short_side_below_500px_warns():
    _, winner = route_one(MILK, milk(), size=(480, 900))
    assert warns(winner) == ["warn:low_resolution"]
    _, winner = route_one(MILK, milk(), size=(500, 500))
    assert warns(winner) == []
    assert quality_mod.low_resolution(None, 300) is False      # unknown size never warns


# -- chat_or_screenshot / social_media ------------------------------------------------------------------

@pytest.mark.parametrize("image_url", [
    "https://img.example.com/uploads/WhatsApp%20Image%202025-10-14%20at%2010.07.41%20AM_1760425491.jpeg",
    "https://img.example.com/uploads/WhatsApp_Image_2025-10-14.jpg",
    "https://img.example.com/uploads/IMG-20251014-WA0003.jpg",
    "https://img.example.com/uploads/Screenshot_20251014-101010.png",
    "https://img.example.com/uploads/Screen%20Shot%202025-10-14%20at%2010.07.41.png",
    "https://img.example.com/uploads/signal-2025-10-14-100741.jpeg",
    "https://img.example.com/uploads/photo_2025-10-14_10-07-41.jpg",
    "https://img.example.com/_next/image?url=https%3A%2F%2Fcdn.x.com%2FWhatsApp%2520Image%25202025.jpeg",
])
def test_chat_and_screenshot_exports_warn(image_url):
    cand = milk(image_url=image_url)
    _, winner = route_one(MILK, cand)
    assert warns(winner) == ["warn:chat_or_screenshot"]


def test_ordinary_file_names_do_not_look_like_chat_exports():
    for url in ("https://cdn.mafrservices.com/x/108596_main.jpg", "https://img.example.com/whatsapp-logo.png",
                "https://img.example.com/img_2025_almarai.jpg", "https://img.example.com/signal-orange-juice.jpg"):
        assert decide.review_warnings(MILK, rc(MILK, milk(image_url=url), reading())) == [], url


@pytest.mark.parametrize("image_url, page_url", [
    ("https://scontent-dxb1-1.cdninstagram.com/v/t51.29350-15/1.jpg", "https://www.instagram.com/p/Cx1/"),
    ("https://scontent.fdxb2-1.fna.fbcdn.net/v/t39.30808-6/1.jpg", ""),
    ("https://i.pinimg.com/736x/aa/bb/cc.jpg", "https://www.pinterest.co.uk/pin/1/"),
    ("https://p16-sign-va.tiktokcdn.com/obj/1.jpeg", "https://www.tiktok.com/@shop/video/1"),
    ("https://img.example.com/1.jpg", "https://www.facebook.com/almarai/photos/1"),
])
def test_social_networks_warn(image_url, page_url):
    cand = milk(image_url=image_url, page_url=page_url, title="Almarai Full Fat Milk 1L")
    assert "social_media" in decide.review_warnings(MILK, rc(MILK, cand, reading()))


# -- foreign_store -------------------------------------------------------------------------------------

@pytest.mark.parametrize("page_url", [
    "https://www.carrefour.com.kw/mafkwt/en/frozen/bato-french-fries-900g/p/1",       # live row 4
    "https://www.carton.sa/product/4783",                                             # live row 42
    "https://www.noon.com/saudi-en/almarai-full-fat-milk-1l/N1/p/",
    "https://www.noon.com/egypt-ar/almarai-full-fat-milk-1l/N1/p/",
    "https://www.amazon.sa/Almarai-Full-Fat-Milk-1L/dp/B0",
    "https://www.amazon.com/Almarai-Full-Fat-Milk-1L/dp/B0",
    "https://www.carrefourksa.com/mafsau/en/almarai-full-fat-milk-1l/p/1",
    "https://www.luluhypermarket.com/en-kw/almarai-full-fat-milk-1l/p/1",
    "https://www.talabat.com/kuwait/grocery/almarai-full-fat-milk-1l",
    "https://www.bigbasket.in/pd/almarai-full-fat-milk-1l/",
])
def test_a_store_outside_the_uae_warns(page_url):
    cand = milk(image_url="https://cdnprod.mafretailproxy.com/sys-master-root/1.jpg", page_url=page_url)
    assert decide.review_warnings(MILK, rc(MILK, cand, reading())) == ["foreign_store"], page_url


@pytest.mark.parametrize("page_url", [
    "https://www.noon.com/uae-en/almarai-full-fat-milk-1l/N1/p/",
    "https://www.noon.com/almarai-full-fat-milk-1l/N1/p/",
    "https://www.amazon.ae/Almarai-Tuna-In-Sunflower-Oil/dp/B0",     # 'in' in a slug is not India
    "https://www.talabat.com/uae/grocery/almarai-full-fat-milk-1l",
    "https://www.nesto.ae/almarai-full-fat-milk-1l",                # other_retail, but a UAE store
    "https://www.westzone.com/almarai-full-fat-milk-1l",
    "https://www.almarai.com/sa/products/full-fat-milk",            # the brand's official site
    "https://www.example-blog.com/almarai-milk-review",             # unknown generic host
])
def test_uae_stores_official_sites_and_unknown_hosts_do_not_warn(page_url):
    cand = milk(image_url="https://img.example-cdn.com/1.jpg", page_url=page_url)
    assert "foreign_store" not in decide.review_warnings(MILK, rc(MILK, cand, reading())), page_url


def test_a_country_section_after_a_language_segment():
    # talabat's Arabic pages put the language first: '/ar/kuwait/...' is Kuwait, '/ar/uae/...' the UAE
    kuwait = milk(image_url="https://images.deliveryhero.io/1.jpg",
                  page_url="https://www.talabat.com/ar/kuwait/grocery/1/almarai-full-fat-milk-1l")
    assert decide.review_warnings(MILK, rc(MILK, kuwait, reading())) == ["foreign_store"]
    uae = milk(image_url="https://images.deliveryhero.io/1.jpg",
               page_url="https://www.talabat.com/ar/uae/grocery/1/almarai-full-fat-milk-1l")
    assert decide.review_warnings(MILK, rc(MILK, uae, reading())) == []


def test_the_brand_official_site_on_a_foreign_domain_does_not_warn():
    mapped = build_sku_spec({"name": "SUNBULAH FRENCH FRIES 1KG", "brand": "SUNBULAH"},
                            {"sunbulah": {"brand": "Sunbulah", "official_domains": ["sunbulah.com.sa"]}})
    cand = Candidate(image_url="https://www.sunbulah.com.sa/images/french-fries.png",
                     page_url="https://www.sunbulah.com.sa/en/products/french-fries", title="Sunbulah French Fries",
                     provider="serper")
    assert "foreign_store" not in decide.review_warnings(mapped, rc(mapped, cand, reading(brand="Sunbulah")))
    # the same page for a SKU without that official domain is a Saudi site
    assert "foreign_store" in decide.review_warnings(FRIES, rc(FRIES, cand, reading(brand="Sunbulah")))


@pytest.mark.parametrize("name", [
    "%D8%B5%D9%88%D8%B1%D8%A9%20%D9%88%D8%A7%D8%AA%D8%B3%D8%A7%D8%A8%20%D8%A8%D8%AA%D8%A7%D8%B1%D9%8A%D8%AE"
    "%202025-10-14.jpg",                                                  # صورة واتساب بتاريخ 2025-10-14
    "%D9%84%D9%82%D8%B7%D8%A9%20%D8%B4%D8%A7%D8%B4%D8%A9%202025-10-14.png",   # لقطة شاشة 2025-10-14
])
def test_arabic_chat_and_screenshot_exports_warn(name):
    cand = milk(image_url="https://www.carton.sa/storage/productImages/4783/" + name,
                page_url="https://www.carrefouruae.com/mafuae/en/almarai-full-fat-milk-1l/p/108596")
    assert decide.review_warnings(MILK, rc(MILK, cand, reading())) == ["chat_or_screenshot"]


def test_the_image_host_is_used_when_the_page_is_unknown():
    cand = Candidate(image_url="https://www.carton.sa/storage/productImages/4783/tuna.jpeg", provider="bing_html")
    assert "foreign_store" in decide.review_warnings(MILK, rc(MILK, cand, reading()))


# -- route: the winner and the decision never change -----------------------------------------------------

def test_warnings_never_change_the_winner_or_the_decision(monkeypatch):
    monkeypatch.setenv("AUTO_PUBLISH_ENABLED", "true")
    monkeypatch.setenv("AUTO_PUBLISH_BRANDS", "Almarai")
    small = milk()
    outcome, winner = route_one(MILK, small, size=(400, 400))
    assert outcome.decision == "AUTO_PUBLISH" and outcome.winner is winner
    assert winner.reasons[-2:] == ["auto_publish", "warn:low_resolution"]
    # Only the winner carries warnings; another eligible candidate from a foreign store does not.
    other = rc(MILK, milk(image_url="https://x.com.kw/2.jpg", page_url="https://www.carrefour.com.kw/p/2",
                          title="Almarai milk"), reading("UNSURE"))
    ranked = [rc(MILK, milk(), reading()), other]
    outcome = decide.route(MILK, ranked, OK, HEALTHY, set())
    assert outcome.winner is ranked[0] and warns(other) == []


def test_route_stays_idempotent_with_warnings():
    cand = milk(image_url="https://img.example.com/WhatsApp%20Image%202025.jpeg",
                page_url="https://www.noon.com/saudi-en/almarai-full-fat-milk-1l/N1/p/")
    # A noon /saudi-en/ page is not a UAE retailer page (tier 2), so the pick needs a verified MATCH.
    winner = rc(MILK, cand, reading("MATCH"), size=(300, 300))
    first = decide.route(MILK, [winner], OK, HEALTHY, set())
    snapshot = list(winner.reasons)
    assert first.winner is winner
    assert warns(winner) == ["warn:low_resolution", "warn:chat_or_screenshot", "warn:foreign_store"]
    second = decide.route(MILK, first.ranked, OK, HEALTHY, set())
    assert winner.reasons == snapshot and second.decision == first.decision


def test_warning_codes_are_the_warn_reasons_without_the_prefix():
    reasons = ["vlm:MATCH", "preselected:vlm_match", "warn:sheet_silent:fries_cut=thin", "warn:foreign_store"]
    assert decide.warning_codes(reasons) == ["sheet_silent:fries_cut=thin", "foreign_store"]
    assert decide.warning_codes([]) == []
    # every code route() can emit is listed for the dashboard
    assert {c.split(":", 1)[0] for c in decide.warning_codes(reasons)} <= set(decide.WARNING_CODES)


# -- the new lexicon axes ----------------------------------------------------------------------------

def test_fries_cut_and_cheese_form_are_bound_to_their_context():
    assert variants_mod.extract_variants("Sunbulah Thin French Fries 1 kg") == {"fries_cut": "thin"}
    assert variants_mod.extract_variants("McCain Crinkle Cut Chips") == {"fries_cut": "crinkle"}
    assert variants_mod.extract_variants("Al Rawabi Shredded Mozzarella") == {"cheese_form": "shredded"}
    assert variants_mod.extract_variants("Kraft Cheddar Cheese Slices")["cheese_form"] == "sliced"
    # the same words next to another product are not a cut or a form
    assert "fries_cut" not in variants_mod.extract_variants("Thin Crust Pizza")
    assert "cheese_form" not in variants_mod.extract_variants("Butter Block 500g")
    # a label reading gets the context from the SKU
    assert variants_mod.extract_variants("Thin", variants_mod.spec_context(FRIES)) == {"fries_cut": "thin"}
    # shredded stays a tuna cut next to tuna
    assert variants_mod.extract_variants("Shredded Tuna in Oil")["tuna_cut"] == "flakes"


def test_new_axes_tiers_and_conflicts():
    page = "https://www.carrefouruae.com/mafuae/en/sunbulah-{}-french-fries-1kg/p/1"
    thin = Candidate(image_url="https://cdn.mafrservices.com/x/1.jpg", page_url=page.format("thin"),
                     title="Sunbulah Thin French Fries 1 kg", provider="serper")
    plain = Candidate(image_url="https://cdn.mafrservices.com/x/2.jpg", page_url=page.format("plain"),
                      title="Sunbulah French Fries 1 kg", provider="serper")
    # an unstated marked cut caps tier 1 (a soft cap, never a reject)
    assert score_candidate(FRIES, thin).tier == 2
    assert "unstated_variant:fries_cut" in score_candidate(FRIES, thin).conflicts
    assert score_candidate(FRIES, plain).tier == 1
    # a stated cut against another stated cut is a hard conflict; thin vs shoestring is soft
    crinkle = build_sku_spec({"name": "SUNBULAH CRINKLE CUT FRIES 1KG", "brand": "SUNBULAH"}, {})
    assert score_candidate(crinkle, thin).hard_reject == ("variant_conflict:fries_cut",)
    shoestring = build_sku_spec({"name": "SUNBULAH SHOESTRING FRIES 1KG", "brand": "SUNBULAH"}, {})
    assert score_candidate(shoestring, thin).hard_reject == ()
    assert any(c.startswith("soft_variant_conflict:fries_cut") for c in score_candidate(shoestring, thin).conflicts)


def test_no_live_sheet_row_states_a_cut_or_a_cheese_form():
    rows = json.loads(LIVE_ROWS.read_text(encoding="utf-8"))["rows"]
    for row in rows:
        spec = build_sku_spec({"name": row["name"], "brand": row["brand"]}, {})
        assert not {"fries_cut", "cheese_form"} & set(spec.variants), (row["row"], spec.variants)


# -- one pipeline run: warnings reach the legacy result ------------------------------------------------------

def _packshot(size):
    img = Image.new("RGB", size, (255, 255, 255))
    w, h = size
    ImageDraw.Draw(img).rectangle([int(w * 0.25), int(h * 0.1), int(w * 0.75), int(h * 0.9)], fill=(200, 150, 20))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


class _Provider:
    kind, fallback, name, sanctioned = "search", False, "serper", True

    def __init__(self, cands):
        self.cands = cands

    def search(self, query, hl, spec):
        return ProviderResult(provider="serper", status="ok", http_status=200, candidates=list(self.cands))


class _Fetcher:
    def __init__(self, bodies):
        self.bodies = bodies

    def fetch(self, cands, spec):
        out = []
        for c in cands:
            body = self.bodies[c.image_url]
            with Image.open(io.BytesIO(body)) as im:
                w, h = im.size
                ph = phash_hex(im)
            out.append(FetchedImage(candidate=c, ok=True, content_sha256="cd" * 32, width=w, height=h,
                                    path_or_bytes=body, phash=ph))
        return out


class _Verifier:
    def __init__(self, readings):
        self.readings = readings

    def verify(self, spec, images):
        return VerificationResult(status="ok", calls=1, verdicts=[
            make_verdict(spec, i, self.readings[f.candidate.image_url]) for i, f in enumerate(images)])


def test_pipeline_row11_pick_keeps_its_decision_and_carries_warnings(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    thin = Candidate(image_url="https://cdn.mafrservices.com/sys-master-root/sunbulah-thin.jpg",
                     page_url="https://www.carrefouruae.com/mafuae/en/frozen-food/sunbulah-thin-french-fries-1kg/p/1",
                     title="Sunbulah Thin French Fries 1 kg", page_title="Sunbulah Thin French Fries 1 kg", rank=1)
    other = Candidate(image_url="https://img.example.org/fries.jpg", page_url="https://blog.example.org/fries",
                      title="Crispy fries recipe", rank=2)
    read = {"brand_text": "Sunbulah", "variant_text": "Thin French Fries", "size_text": "1 kg", "pack_count": 1,
            "view": "front_packshot", "brand_match": "yes", "variant_match": "yes", "size_match": "yes"}
    outcome = pipeline.find_product_image(
        FRIES, providers=[_Provider([thin, other])],
        fetcher=_Fetcher({thin.image_url: _packshot((450, 450)), other.image_url: _packshot((900, 900))}),
        verifier=_Verifier({thin.image_url: read, other.image_url: dict(read, brand_text="", brand_match="unsure",
                                                                         view="lifestyle")}))
    assert outcome.decision == "REVIEW_PRESELECTED"
    assert outcome.winner is not None and outcome.winner.candidate.image_url == thin.image_url
    assert outcome.winner.score.tier == 2          # the unstated 'thin' caps tier 1
    assert warns(outcome.winner) == ["warn:sheet_silent:fries_cut=thin", "warn:low_resolution"]

    trace = {}
    legacy = facade.outcome_to_legacy(outcome, trace)
    by_url = {c["url"]: c for c in legacy["candidates"]}
    assert by_url[thin.image_url]["warnings"] == ["sheet_silent:fries_cut=thin", "low_resolution"]
    assert by_url[other.image_url]["warnings"] == []
    assert "warn:low_resolution" in by_url[thin.image_url]["reasons"]
    assert trace["steps"][0]["candidates"][0]["warnings"] == by_url[thin.image_url]["warnings"]


def test_facade_serialises_warnings_for_every_candidate():
    _, winner = route_one(MILK, milk(), size=(300, 400))
    plain = RankedCandidate(candidate=milk(image_url="https://x.ae/2.jpg"), score=score_candidate(MILK, milk()))
    assert facade.serialise_candidate(winner)["warnings"] == ["low_resolution"]
    assert facade.serialise_candidate(plain)["warnings"] == []


def test_frozen_fries_listing_is_tier1_and_carries_no_sheet_silent_warning():
    spec = build_sku_spec({"name": "AIDA FRENCH FRIES 1KG", "brand": "AIDA"}, {})
    cand = Candidate(image_url="https://cdn.mafrservices.com/pim-content/UAE/media/product/1/1_main.jpg",
                     page_url="https://www.carrefouruae.com/mafuae/en/aida-frozen-french-fries-1kg/p/1",
                     title="Aida Frozen French Fries 1kg", page_title="Aida Frozen French Fries 1kg",
                     provider="serper")
    winner = rc(spec, cand, reading("MATCH", variant_text="Frozen French Fries", brand="AIDA"))
    assert winner.score.tier == 1
    out = decide.route(spec, [winner], OK, HEALTHY, set())
    assert out.winner is winner and warns(winner) == []
