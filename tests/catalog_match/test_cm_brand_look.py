"""The brand look check (catalog_match.embeddings + decide 'brand_look_mismatch'): a listing that names the right
brand but whose picture shows ANOTHER brand's pack (Yumway read as 'Max Foods', Fine tissue as Kleenex).

Evidence only. The candidate's picture is compared with the approved pictures of its own brand and of the other
brands: far from every approved picture of its brand AND close to another brand's approved picture adds the review
warning 'brand_look_mismatch' («شكل العبوة أقرب لماركة تانية»). It never changes the winner, a status, a tier or
the order; it keeps the pick out of lane 'strict' and blocks auto-publish (also through AUTO_PUBLISH_BRANDS). A
brand with fewer than 3 approved pictures is not judged, and EMBEDDINGS=off changes nothing at all.

The vectors come from the colour fake (tests/embed_fakes.py): the colour of a pack plays its design.
"""

import io

import pytest

from catalog_match import decide, embeddings, facade, pipeline, settings
from catalog_match.embeddings import Reference, ReferenceSet, Thresholds, judge
from catalog_match.models import FetchedImage
from embed_fakes import ColourEmbedder, packshot
from test_cm_decide import OK, HEALTHY, SPEC, auto_on, rc, ranked, t1, t2, verdict
from test_cm_pipeline import READ_MATCH, StubFetcher, StubProvider, StubVerifier, cand
from test_cm_pipeline import SPEC as PIPE_SPEC

RED, BLUE, GREEN = (200, 20, 20), (20, 20, 200), (20, 160, 20)
TH = embeddings.THRESHOLDS["dinov2"]
FAKE = ColourEmbedder()


def vec(colour):
    return FAKE.vector(packshot(colour))


def refs(*rows):
    """ReferenceSet from (brand, colour) pairs, one approved picture each."""
    return ReferenceSet([Reference(sku_key=f"s{i}", brand_key=embeddings.brand_key(b), brand=b, vector=vec(c))
                         for i, (b, c) in enumerate(rows)])


ALMARAI_BLUE = [("Almarai", BLUE), ("Almarai", (30, 40, 210)), ("ALMARAI", (25, 25, 190))]


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    monkeypatch.setattr(settings, "_config", None)
    monkeypatch.setenv("AUTO_PUBLISH_ENABLED", "false")
    monkeypatch.setenv("AUTO_PUBLISH_BRANDS", "")
    monkeypatch.delenv("EMBEDDINGS", raising=False)
    embeddings.reset()
    yield
    embeddings.reset()


def look(colour, *rows, th=TH):
    own, related = embeddings.spec_brand_keys(SPEC)
    return judge(vec(colour), refs(*rows), own, related, th)


# ---------------------------------------------------------------------------
# judge(): the rule
# ---------------------------------------------------------------------------

def test_a_pack_far_from_its_brand_and_close_to_another_brands_is_a_mismatch():
    v = look(RED, *ALMARAI_BLUE, ("Al Ain", RED))
    assert v.mismatch and v.other_brand == "Al Ain" and v.n_same == 3
    assert v.same < TH.same_max and v.other >= TH.other_min and v.other - v.same >= TH.margin


def test_a_brand_with_fewer_than_three_approved_pictures_is_not_judged():
    assert look(RED, *ALMARAI_BLUE[:2], ("Al Ain", RED)) is None
    assert look(RED, ("Al Ain", RED), ("Nada", RED), ("Puck", RED)) is None


def test_close_to_one_approved_picture_of_its_own_brand_is_no_mismatch():
    v = look(RED, *ALMARAI_BLUE, ("Almarai", (190, 30, 30)), ("Al Ain", RED))
    assert not v.mismatch and v.same > 0.99


def test_far_from_everything_is_no_mismatch():
    v = look(RED, *ALMARAI_BLUE, ("Al Ain", GREEN))
    assert not v.mismatch and v.other < TH.other_min


def test_the_other_brand_must_be_clearly_closer():
    th = Thresholds(same_max=0.99, other_min=0.5, margin=0.5, near_dup=0.9)
    purple = (150, 20, 150)
    v = look(RED, ("Almarai", purple), ("Almarai", purple), ("Almarai", purple), ("Al Ain", (190, 40, 60)), th=th)
    assert v.other - v.same < th.margin and not v.mismatch


def test_a_spelling_or_sub_brand_of_the_same_brand_is_never_another_brand():
    from catalog_match.identity import build_sku_spec

    spec = build_sku_spec({"name": "AL ALALI FANCY TUNA WATER 170GM", "brand": "AL ALALI"})
    own, related = embeddings.spec_brand_keys(spec)
    rows = refs(("AL ALALI", BLUE), ("Al-Alali", (30, 30, 210)), ("ALALI", RED), ("Al Alali", (20, 30, 190)))
    v = judge(vec(RED), rows, own, related, TH)
    assert v.n_same == 4 and v.same > 0.99 and v.other is None and not v.mismatch
    assert embeddings.related_keys("sup t", "supt") and not embeddings.related_keys("lu", "lurpak")


# ---------------------------------------------------------------------------
# decide: a warning and an auto-publish blocker, never another winner
# ---------------------------------------------------------------------------

def _mismatch(r, other_brand="Al Ain"):
    r.fetched.look = {"same": 0.1, "other": 0.95, "other_brand": other_brand, "n_same": 3, "mismatch": True,
                      "model": "fake:colour"}
    return r


@pytest.mark.parametrize("path", ["brand_list", "strict_lane"])
def test_a_mismatch_keeps_the_winner_and_never_auto_publishes(monkeypatch, path):
    if path == "brand_list":
        auto_on(monkeypatch)
    else:
        auto_on(monkeypatch, brands="")
        monkeypatch.setenv("AUTO_PUBLISH_STRICT_LANE", "true")
    clean = decide.route(SPEC, ranked(rc(t1(), verdict("MATCH")), rc(t2(), verdict("UNSURE"))), OK, HEALTHY, set())
    assert clean.decision == "AUTO_PUBLISH"                                  # without the evidence: published

    win = _mismatch(rc(t1(), verdict("MATCH")))
    other = rc(t2(), verdict("UNSURE"))
    out = decide.route(SPEC, ranked(win, other), OK, HEALTHY, set())
    assert out.winner is win and win.status == "preselected" and other.status == "eligible"
    assert out.decision == "REVIEW_PRESELECTED"
    assert "warn:brand_look_mismatch" in win.reasons and "auto_blocked:brand_look_mismatch" in win.reasons
    assert decide.lane_of(win.reasons) == "other" and "lane:strict" not in win.reasons
    assert "auto_publish" not in win.reasons
    # route is idempotent with the evidence
    again = decide.route(SPEC, ranked(win, other), OK, HEALTHY, set())
    assert again.decision == "REVIEW_PRESELECTED" and win.reasons.count("warn:brand_look_mismatch") == 1


def test_a_mismatch_on_an_alternative_is_shown_under_it_and_changes_nothing():
    win = rc(t1(), verdict("MATCH"))
    alt = _mismatch(rc(t2(), verdict("UNSURE")))
    out = decide.route(SPEC, ranked(win, alt), OK, HEALTHY, set())
    assert out.winner is win and "warn:brand_look_mismatch" not in win.reasons
    assert "brand_look_mismatch" in decide.candidate_warnings(SPEC, alt)
    assert "brand_look_mismatch" not in decide.candidate_warnings(SPEC, win)
    assert facade.evidence(alt)["look"]["other_brand"] == "Al Ain"
    assert "look" not in facade.evidence(win)                               # off / not judged: the shape as before


def test_a_larger_copy_with_the_mismatch_never_replaces_a_clean_winner():
    win = rc(t1(1), verdict("MATCH"))
    big = rc(t1(2), verdict("MATCH"))
    win.fetched.phash = big.fetched.phash = "ffff0000ffff0000"
    win.fetched.width = win.fetched.height = 400
    big.fetched.width = big.fetched.height = 1600
    assert decide.route(SPEC, ranked(win, big), OK, HEALTHY, set()).winner is big    # the upgrade, as before
    _mismatch(big)
    out = decide.route(SPEC, ranked(win, big), OK, HEALTHY, set())
    assert out.winner is win and "warn:brand_look_mismatch" not in win.reasons


def test_the_warning_has_an_arabic_sentence_in_the_review_screen():
    from pathlib import Path

    core = (Path(__file__).resolve().parents[2] / "dashboard" / "public" / "js" / "review" / "core.js").read_text(
        encoding="utf-8")
    assert "brand_look_mismatch: 'شكل العبوة أقرب لماركة تانية" in core
    assert "brand_look_mismatch" in decide.WARNING_CODES


# ---------------------------------------------------------------------------
# The pipeline: the check runs on the downloaded candidates, in one batch
# ---------------------------------------------------------------------------

def _png(colour):
    buf = io.BytesIO()
    packshot(colour, size=(800, 800), margin=150).save(buf, "PNG")
    return buf.getvalue()


def _search(monkeypatch, colour):
    monkeypatch.setenv("AUTO_PUBLISH_ENABLED", "true")
    monkeypatch.setenv("AUTO_PUBLISH_BRANDS", "Almarai")
    pick = cand(2, "Buy Almarai Full Fat Fresh Milk 1L Online - Carrefour UAE",
                "https://www.carrefouruae.com/mafuae/en/almarai-full-fat-fresh-milk-1l/p/108596")
    alt = cand(3, "Almarai Full Fat Milk 1L", "https://www.luluhypermarket.com/en-ae/almarai-full-fat-milk-1l/p/9")
    fetcher = StubFetcher({pick.image_url: _png(colour), alt.image_url: _png((40, 40, 220))})
    verifier = StubVerifier({pick.image_url: READ_MATCH, alt.image_url: dict(READ_MATCH, view="lifestyle")})
    return pick, pipeline.find_product_image(PIPE_SPEC, providers=[StubProvider("serper", [pick, alt])], fetcher=fetcher,
                                             verifier=verifier)


def test_off_the_search_is_untouched(monkeypatch):
    embeddings.set_embedder(ColourEmbedder())
    embeddings.set_reference_loader(lambda model: pytest.fail("references read with EMBEDDINGS off"))
    pick, out = _search(monkeypatch, RED)
    assert out.decision == "AUTO_PUBLISH" and out.winner.candidate.image_url == pick.image_url
    assert all(r.fetched is None or (r.fetched.look is None and r.fetched.embedding is None) for r in out.ranked)
    assert "embeddings" not in out.timings


def test_on_another_brands_pack_is_warned_and_held_for_review(monkeypatch):
    monkeypatch.setenv("EMBEDDINGS", "dinov2")
    fake = ColourEmbedder()
    embeddings.set_embedder(fake)
    approved = [Reference(f"s{i}", embeddings.brand_key(b), b, vec(c))
                for i, (b, c) in enumerate(ALMARAI_BLUE + [("Al Ain", RED)])]
    embeddings.set_reference_loader(lambda model: approved)
    pick, out = _search(monkeypatch, RED)
    assert out.winner.candidate.image_url == pick.image_url                   # the same winner
    assert out.decision == "REVIEW_PRESELECTED"
    assert "warn:brand_look_mismatch" in out.winner.reasons
    assert fake.calls == [2] and "embeddings" in out.timings                 # one batch for both downloads
    alt = next(r for r in out.ranked if r is not out.winner)
    assert alt.fetched.look["mismatch"] is False and alt.fetched.look["same"] > 0.99


def test_on_the_brands_own_look_changes_nothing(monkeypatch):
    monkeypatch.setenv("EMBEDDINGS", "dinov2")
    embeddings.set_embedder(ColourEmbedder())
    approved = [Reference(f"s{i}", embeddings.brand_key(b), b, vec(c))
                for i, (b, c) in enumerate(ALMARAI_BLUE + [("Al Ain", RED)])]
    embeddings.set_reference_loader(lambda model: approved)
    pick, out = _search(monkeypatch, (30, 30, 205))
    assert out.decision == "AUTO_PUBLISH" and "warn:brand_look_mismatch" not in out.winner.reasons


def test_a_brand_without_enough_approved_pictures_loads_no_model(monkeypatch):
    monkeypatch.setenv("EMBEDDINGS", "dinov2")
    fake = ColourEmbedder()
    embeddings.set_embedder(fake)
    embeddings.set_reference_loader(lambda model: [Reference("s1", "almarai", "Almarai", vec(BLUE)),
                                                   Reference("s2", "al ain", "Al Ain", vec(RED))])
    pick, out = _search(monkeypatch, RED)
    assert out.decision == "AUTO_PUBLISH" and fake.calls == []


def test_a_failing_check_or_reference_read_is_no_warning(monkeypatch):
    monkeypatch.setenv("EMBEDDINGS", "dinov2")

    class Broken(ColourEmbedder):
        def embed(self, images):
            raise RuntimeError("onnx")

    embeddings.set_embedder(Broken())
    approved = [Reference(f"s{i}", embeddings.brand_key(b), b, vec(c))
                for i, (b, c) in enumerate(ALMARAI_BLUE + [("Al Ain", RED)])]
    embeddings.set_reference_loader(lambda model: approved)
    pick, out = _search(monkeypatch, RED)
    assert out.decision == "AUTO_PUBLISH"
    embeddings.set_embedder(ColourEmbedder())

    def down(model):
        raise ConnectionError("db")

    embeddings.set_reference_loader(down)
    pick, out = _search(monkeypatch, RED)
    assert out.decision == "AUTO_PUBLISH"


def test_the_references_are_read_once_per_ttl(monkeypatch):
    reads, now = [], [0.0]
    embeddings.set_reference_loader(lambda model: reads.append(model) or [])
    embeddings.references("m", clock=lambda: now[0])
    embeddings.references("m", clock=lambda: now[0])
    assert reads == ["m"]
    now[0] += embeddings.REFERENCE_TTL_S + 1
    embeddings.references("m", clock=lambda: now[0])
    assert reads == ["m", "m"]


def test_annotate_never_touches_a_failed_download(monkeypatch):
    monkeypatch.setenv("EMBEDDINGS", "dinov2")
    embeddings.set_embedder(ColourEmbedder())
    embeddings.set_reference_loader(lambda model: [Reference(f"s{i}", "almarai", "Almarai", vec(BLUE))
                                                   for i in range(3)])
    bad = rc(t1(), fetch_ok=False)
    assert embeddings.annotate(SPEC, [bad]) == 0 and bad.fetched.look is None
    assert isinstance(bad.fetched, FetchedImage)
